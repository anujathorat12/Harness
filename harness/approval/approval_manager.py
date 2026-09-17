"""
Human-in-the-loop approval.

An approval is always backed by a durable Approval row (status survives a
restart and is queryable via the API), plus a best-effort in-process
asyncio.Event used only to wake a waiter immediately instead of waiting for
the next poll tick. If the Event is lost (process restart while an action is
paused), the polling fallback in wait_for_resolution() still notices the
resolution or the timeout within POLL_INTERVAL_SECONDS -- correctness does
not depend on the Event firing, only latency does.

KNOWN LIMITATION (documented in ARCHITECTURE.md): a session whose process is
paused waiting on approval, if the harness process itself is restarted, does
not automatically resume the *in-flight task coroutine* -- the coroutine
holding the sandboxed agent's stdio connection is gone. The Approval and
Action rows correctly reflect what happened and an operator can see the
session ended up "failed: harness restarted while paused", but true
crash-resume of a live sandbox is out of scope for this build. A production
deployment addressing this would need to either keep sandboxes external to
the harness process's own memory (e.g. detached containers reattached on
restart) or accept re-running the task from the last completed action. This
is called out explicitly rather than silently glossed over.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from harness.audit.audit_logger import log_event
from harness.core.config import settings
from harness.domain import models

POLL_INTERVAL_SECONDS = 0.5

_wake_events: dict[str, asyncio.Event] = {}


def _aware(dt: datetime) -> datetime:
    """SQLite (via aiosqlite) does not actually preserve tzinfo on
    DateTime(timezone=True) columns -- values round-trip as naive. Every
    comparison in this module goes through this helper so 'naive vs aware'
    TypeErrors can't happen regardless of which database backend is in use
    (Postgres does preserve tzinfo, so this is a no-op there)."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _get_event(approval_id: str) -> asyncio.Event:
    ev = _wake_events.get(approval_id)
    if ev is None:
        ev = asyncio.Event()
        _wake_events[approval_id] = ev
    return ev


class DuplicateResolutionError(Exception):
    pass


async def create_approval(
    db: AsyncSession, *, action_id: str, timeout_seconds: int | None = None
) -> models.Approval:
    timeout_seconds = timeout_seconds or settings.approval_default_timeout_seconds
    approval = models.Approval(
        action_id=action_id,
        status="pending",
        timeout_at=datetime.now(timezone.utc) + timedelta(seconds=timeout_seconds),
    )
    db.add(approval)
    await db.flush()
    return approval


async def resolve_approval(
    db: AsyncSession, *, approval_id: str, approve: bool, approver: str, reason: str | None = None
) -> models.Approval:
    approval = await db.get(models.Approval, approval_id)
    if approval is None:
        raise ValueError(f"no such approval: {approval_id}")
    if approval.status != "pending":
        # Idempotency / conflicting-resolution guard: the FIRST resolution
        # wins; every subsequent attempt (duplicate approval, or a denial
        # arriving after an approval already fired, or vice versa) is
        # rejected rather than silently overwriting the audit-relevant
        # outcome.
        raise DuplicateResolutionError(
            f"approval {approval_id} already resolved with status={approval.status!r}; "
            f"a second resolution attempt ({'approve' if approve else 'deny'} by {approver}) was rejected"
        )
    approval.status = "approved" if approve else "denied"
    approval.approver = approver
    approval.reason = reason
    approval.resolved_at = datetime.now(timezone.utc)
    await db.flush()

    await log_event(
        db,
        event_type="approval_resolved",
        action_id=approval.action_id,
        approval_id=approval.id,
        actor=approver,
        decision=approval.status,
        details={"reason": reason},
    )
    await db.commit()

    _get_event(approval_id).set()
    return approval


async def wait_for_resolution(db_factory, approval_id: str) -> models.Approval:
    """Blocks until the approval is resolved (approve/deny) or times out.
    `db_factory` is an async-context-manager session factory (SessionLocal)
    so this function can open its own short-lived sessions for each poll,
    independent of whatever session the caller is using -- this matters
    because the caller's own session may be held open across a long wait.
    """
    event = _get_event(approval_id)
    async with db_factory() as db:
        approval = await db.get(models.Approval, approval_id)
        if approval is None:
            raise ValueError(f"no such approval: {approval_id}")
        timeout_at = _aware(approval.timeout_at)

    while True:
        now = datetime.now(timezone.utc)
        remaining = (timeout_at - now).total_seconds()
        if remaining <= 0:
            async with db_factory() as db:
                approval = await db.get(models.Approval, approval_id)
                if approval.status == "pending":
                    approval.status = "timed_out"
                    approval.resolved_at = now
                    await db.flush()
                    await log_event(
                        db,
                        event_type="approval_timed_out",
                        action_id=approval.action_id,
                        approval_id=approval.id,
                        decision="timed_out",
                    )
                    await db.commit()
                await db.refresh(approval)
                _wake_events.pop(approval_id, None)
                return approval

        try:
            await asyncio.wait_for(event.wait(), timeout=min(POLL_INTERVAL_SECONDS, remaining))
        except asyncio.TimeoutError:
            pass

        async with db_factory() as db:
            approval = await db.get(models.Approval, approval_id)
            if approval.status != "pending":
                _wake_events.pop(approval_id, None)
                return approval

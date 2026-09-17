"""
Audit logging.

This module is the only place AuditEvent rows are created, and it only ever
INSERTs (never UPDATE/DELETE) -- an audit trail that could be edited after
the fact would not be trustworthy. Every call site that writes an
AuditEvent goes through log_event(), which redacts sensitive keys before
anything touches the database, so accidental leakage of a secret parameter
into the durable log is a redact-list bug, not a "forgot to redact at this
one call site" bug.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from harness.core.config import settings
from harness.domain import models


def redact(data: Any) -> Any:
    if isinstance(data, dict):
        out = {}
        for k, v in data.items():
            if any(bad in k.lower() for bad in settings.audit_redacted_keys):
                out[k] = "***REDACTED***"
            else:
                out[k] = redact(v)
        return out
    if isinstance(data, list):
        return [redact(v) for v in data]
    return data


async def log_event(
    db: AsyncSession,
    *,
    event_type: str,
    agent_id: str | None = None,
    session_id: str | None = None,
    task_id: str | None = None,
    action_id: str | None = None,
    approval_id: str | None = None,
    policy_id: str | None = None,
    policy_version: int | None = None,
    rule_id: str | None = None,
    decision: str | None = None,
    actor: str = "system",
    details: dict[str, Any] | None = None,
) -> models.AuditEvent:
    event = models.AuditEvent(
        event_type=event_type,
        agent_id=agent_id,
        session_id=session_id,
        task_id=task_id,
        action_id=action_id,
        approval_id=approval_id,
        policy_id=policy_id,
        policy_version=policy_version,
        rule_id=rule_id,
        decision=decision,
        actor=actor,
        details=redact(details or {}),
    )
    db.add(event)
    await db.flush()
    return event

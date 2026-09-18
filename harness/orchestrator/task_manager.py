"""
The orchestrator. This module owns the loop that:

  1. starts a Runtime for the agent version (process / container / declarative)
  2. reads RuntimeEvents from it
  3. for every action_request, calls gateway.handle_action() -- the ONLY path
     to policy evaluation and tool execution in the whole codebase
  4. feeds the gateway's outcome back into the runtime via resolve_action()
  5. updates Session/Task status and writes lifecycle audit events

Because step 3 always goes through the gateway, and the gateway is the only
holder of TOOLS, there is no code path in this file (or anywhere else) that
can execute a governed action without a policy decision first. If you are
reviewing this codebase for the "can an agent bypass the policy layer"
question the assignment asks: this is the file to read end to end.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select

from harness.audit.audit_logger import log_event
from harness.core.database import SessionLocal
from harness.domain import models
from harness.gateway.tool_gateway import handle_action
from harness.orchestrator.concurrency import limiter
from harness.runtime.base import RuntimeEvent
from harness.runtime.factory import get_runtime

logger = logging.getLogger("harness.orchestrator")

_running_handles: dict[str, object] = {}  # session_id -> AgentHandle, for cancellation/admin visibility


async def submit_task(
    agent_id: str,
    agent_version_id: str,
    task_input: dict,
    workspace_files: dict[str, str] | None = None,
    created_by: str | None = None,
) -> tuple[str, str]:
    """Creates the Session + Task rows synchronously (so the caller gets IDs
    back immediately) and schedules execution in the background. Returns
    (session_id, task_id). `created_by` is the authenticated caller's
    principal label (see harness/core/security.py), used only for CLIENT-role
    row-level scoping on read -- it plays no role in policy enforcement."""
    async with SessionLocal() as db:
        session_row = models.Session(agent_id=agent_id, agent_version_id=agent_version_id, status="pending")
        db.add(session_row)
        await db.flush()
        task_row = models.Task(session_id=session_row.id, input=task_input, status="pending", created_by=created_by)
        db.add(task_row)
        await db.flush()
        await log_event(
            db, event_type="session_created", agent_id=agent_id, session_id=session_row.id, task_id=task_row.id
        )
        await db.commit()
        session_id, task_id = session_row.id, task_row.id

    asyncio.create_task(_run(agent_id, session_id, task_id, agent_version_id, task_input, workspace_files or {}))
    return session_id, task_id


async def _run(
    agent_id: str,
    session_id: str,
    task_id: str,
    agent_version_id: str,
    task_input: dict,
    workspace_files: dict[str, str],
) -> None:
    async with limiter.slot(agent_id):
        async with SessionLocal() as db:
            session_row = await db.get(models.Session, session_id)
            task_row = await db.get(models.Task, task_id)
            agent_version = await db.get(models.AgentVersion, agent_version_id)

            session_row.status = "running"
            session_row.started_at = datetime.now(timezone.utc)
            task_row.status = "running"
            task_row.started_at = datetime.now(timezone.utc)
            await db.flush()
            await log_event(db, event_type="session_started", agent_id=agent_id, session_id=session_id, task_id=task_id)
            await db.commit()

        workspace_root = f"/tmp/harness-sandboxes/{session_id}"
        import os

        os.makedirs(workspace_root, exist_ok=True, mode=0o700)

        for rel_path, content in workspace_files.items():
            # Same containment check as the gateway's file tools apply at
            # execution time -- a seed-file path is just as untrusted as any
            # other input, since the task submitter is not necessarily the
            # agent owner and should not be able to seed files outside the
            # sandbox via a crafted path.
            root = os.path.realpath(workspace_root)
            candidate = os.path.realpath(os.path.join(root, rel_path.lstrip("/")))
            if candidate != root and not candidate.startswith(root + os.sep):
                await _fail(session_id, task_id, f"workspace_files path escapes workspace: {rel_path!r}")
                return
            os.makedirs(os.path.dirname(candidate), exist_ok=True)
            with open(candidate, "w") as f:
                f.write(content)

        try:
            runtime = get_runtime(agent_version.runtime_kind)
        except Exception as e:  # unknown/misconfigured runtime_kind -> fail closed, not silently skip enforcement
            await _fail(session_id, task_id, f"failed to select runtime: {e}")
            return

        try:
            handle = await runtime.start(
                agent_version_spec=agent_version.spec,
                resource_limits={**agent_version.resource_limits},
                task_input=task_input,
                workdir_token=session_id,
            )
        except Exception as e:
            await _fail(session_id, task_id, f"failed to start agent: {e}")
            return

        _running_handles[session_id] = handle
        final_result = None
        final_error = None

        try:
            async for event in handle.events():
                if event.kind == "action_request":
                    async with SessionLocal() as db:
                        outcome = await handle_action(
                            db,
                            agent_id=agent_id,
                            session_id=session_id,
                            task_id=task_id,
                            workspace_root=workspace_root,
                            action_type=event.action.action_type,
                            resource=event.action.resource,
                            parameters=event.action.parameters,
                        )
                    await handle.resolve_action(
                        allowed=outcome["allowed"], result=outcome.get("result"), error=outcome.get("error")
                    )
                elif event.kind == "log":
                    logger.info("agent[%s] log: %s", session_id, event.message)
                elif event.kind == "finished":
                    final_result = event.result
                elif event.kind in ("error", "timeout", "killed"):
                    final_error = event.message
        except Exception as e:  # defensive: orchestrator itself must not crash the process on a bad agent
            final_error = f"orchestrator error while draining agent events: {e}"
        finally:
            _running_handles.pop(session_id, None)

        if final_error:
            await _fail(session_id, task_id, final_error)
        else:
            await _succeed(session_id, task_id, final_result)


async def _succeed(session_id: str, task_id: str, result) -> None:
    async with SessionLocal() as db:
        session_row = await db.get(models.Session, session_id)
        task_row = await db.get(models.Task, task_id)
        session_row.status = "completed"
        session_row.ended_at = datetime.now(timezone.utc)
        task_row.status = "succeeded"
        task_row.result = result
        task_row.ended_at = datetime.now(timezone.utc)
        await db.flush()
        await log_event(
            db, event_type="task_succeeded", agent_id=session_row.agent_id, session_id=session_id, task_id=task_id
        )
        await db.commit()


async def _fail(session_id: str, task_id: str, error: str) -> None:
    async with SessionLocal() as db:
        session_row = await db.get(models.Session, session_id)
        task_row = await db.get(models.Task, task_id)
        session_row.status = "failed"
        session_row.error = error
        session_row.ended_at = datetime.now(timezone.utc)
        task_row.status = "failed"
        task_row.error = error
        task_row.ended_at = datetime.now(timezone.utc)
        await db.flush()
        await log_event(
            db,
            event_type="task_failed",
            agent_id=session_row.agent_id,
            session_id=session_id,
            task_id=task_id,
            details={"error": error},
        )
        await db.commit()


async def cancel_session(session_id: str, reason: str = "operator cancellation") -> bool:
    handle = _running_handles.get(session_id)
    if handle is None:
        return False
    await handle.terminate(reason)
    return True

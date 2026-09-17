"""
ToolGateway.handle_action() is THE enforcement point referenced throughout
this codebase's comments and in ARCHITECTURE.md. It is the only function in
the entire platform that is allowed to call into harness/gateway/tools.py.
No runtime, no orchestrator code, nothing else holds a reference to TOOLS.

The sequence, matching the assignment's required flow exactly:

    ACTION REQUEST
         v
    write Action row (status=pending)          <- exists even if we crash next line
         v
    resolve effective policy (session > agent) <- NoPolicyAttachedError -> deny
         v
    policy_engine.evaluate()
         v
    audit the decision
         v
    ALLOW              DENY           REQUIRE_APPROVAL
      v                  v                    v
   execute tool      return denial      create Approval row, commit,
      v                                 wait (pause), then:
   audit result                         approved -> execute tool -> audit
                                         denied/timed_out -> deny -> audit

Every branch ends by returning a plain dict {"allowed": bool, "result":
..., "error": ...} which is exactly the payload the Runtime.resolve_action()
call expects -- so orchestrator.py's loop is a two-line pass-through and
cannot accidentally skip a step.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from harness.approval.approval_manager import create_approval, wait_for_resolution
from harness.audit.audit_logger import log_event
from harness.core.database import SessionLocal
from harness.domain import models
from harness.gateway.tools import TOOLS, ToolExecutionError
from harness.policy_engine.evaluator import ActionContext, evaluate
from harness.policy_engine.resolver import NoPolicyAttachedError, resolve_effective_policy


class GatewayResult(dict):
    """dict subclass purely for readability at call sites; behaves like a
    plain {'allowed': bool, 'result': Any, 'error': str|None} dict."""


async def handle_action(
    db: AsyncSession,
    *,
    agent_id: str,
    session_id: str,
    task_id: str,
    workspace_root: str,
    action_type: str,
    resource: str | None,
    parameters: dict[str, Any],
) -> GatewayResult:
    action = models.Action(
        task_id=task_id,
        session_id=session_id,
        agent_id=agent_id,
        action_type=action_type,
        resource=resource,
        parameters=parameters,
        status="pending",
    )
    db.add(action)
    await db.flush()
    await log_event(
        db,
        event_type="action_attempted",
        agent_id=agent_id,
        session_id=session_id,
        task_id=task_id,
        action_id=action.id,
        details={"action_type": action_type, "resource": resource, "parameters": parameters},
    )
    await db.commit()

    session_row = await db.get(models.Session, session_id)

    # --- Resolve policy + evaluate (fail closed on any problem) ---
    try:
        policy_doc, policy_version_row = await resolve_effective_policy(db, agent_id=agent_id, session_id=session_id)
        decision = evaluate(
            policy_doc,
            ActionContext(agent_id=agent_id, session_id=session_id, action_type=action_type, resource=resource, parameters=parameters),
            budget_state=session_row.budget_state or {},
        )
        policy_id = policy_version_row.policy_id
        policy_version_num = policy_version_row.version
    except NoPolicyAttachedError as e:
        decision = None
        policy_id = None
        policy_version_num = None
        effect = "deny"
        rule_id = None
        reason = str(e)
    else:
        effect = decision.effect
        rule_id = decision.rule_id
        reason = decision.reason
        if decision.budget_exceeded:
            # Budget rejections are audited distinctly so an operator can
            # tell "policy denied this" apart from "budget ran out".
            reason = f"budget_exceeded: {reason}"

    action.policy_id = policy_id
    action.policy_version = policy_version_num
    action.rule_id = rule_id
    action.decision = effect
    action.decided_at = datetime.now(timezone.utc)
    await db.flush()
    await log_event(
        db,
        event_type="policy_decision",
        agent_id=agent_id,
        session_id=session_id,
        task_id=task_id,
        action_id=action.id,
        policy_id=policy_id,
        policy_version=policy_version_num,
        rule_id=rule_id,
        decision=effect,
        details={"reason": reason},
    )
    await db.commit()

    if effect == "deny":
        action.status = "denied"
        action.error = f"denied by policy: {reason}"
        action.completed_at = datetime.now(timezone.utc)
        await db.flush()
        await db.commit()
        return GatewayResult(allowed=False, result=None, error=f"denied by policy: {reason}")

    if effect == "require_approval":
        approval = await create_approval(db, action_id=action.id)
        action.status = "approval_pending"
        await db.flush()
        await log_event(
            db,
            event_type="approval_requested",
            agent_id=agent_id,
            session_id=session_id,
            task_id=task_id,
            action_id=action.id,
            approval_id=approval.id,
            policy_id=policy_id,
            policy_version=policy_version_num,
            rule_id=rule_id,
            decision=effect,
        )
        await db.commit()

        resolved = await wait_for_resolution(SessionLocal, approval.id)

        # Re-fetch the action in this session (it may be stale after the wait).
        action = await db.get(models.Action, action.id)
        if resolved.status == "approved":
            action.status = "approved"
            await db.flush()
            await db.commit()
            return await _execute_and_record(db, action, session_row, action_type, resource, parameters, workspace_root)
        else:
            action.status = "denied" if resolved.status == "denied" else "timed_out"
            action.error = f"approval {resolved.status} (approver={resolved.approver})"
            action.completed_at = datetime.now(timezone.utc)
            await db.flush()
            await db.commit()
            return GatewayResult(
                allowed=False, result=None, error=f"approval {resolved.status} (approver={resolved.approver})"
            )

    # effect == "allow"
    return await _execute_and_record(db, action, session_row, action_type, resource, parameters, workspace_root)


async def _execute_and_record(
    db: AsyncSession,
    action: models.Action,
    session_row: models.Session,
    action_type: str,
    resource: str | None,
    parameters: dict[str, Any],
    workspace_root: str,
) -> GatewayResult:
    tool = TOOLS.get(action_type)
    action.status = "executing"
    await db.flush()
    await db.commit()

    start = time.monotonic()
    if tool is None:
        action.status = "error"
        action.error = f"no tool implementation registered for action_type={action_type!r}"
        action.completed_at = datetime.now(timezone.utc)
        action.duration_ms = (time.monotonic() - start) * 1000
        await db.flush()
        await log_event(
            db,
            event_type="action_execution_error",
            agent_id=action.agent_id,
            session_id=action.session_id,
            task_id=action.task_id,
            action_id=action.id,
            details={"error": action.error},
        )
        await db.commit()
        return GatewayResult(allowed=True, result=None, error=action.error)

    try:
        result = await tool(resource, parameters, workspace_root)
        action.status = "completed"
        action.result = result
        action.completed_at = datetime.now(timezone.utc)
        action.duration_ms = (time.monotonic() - start) * 1000
        await _apply_budget(db, session_row, action)
        await db.flush()
        await log_event(
            db,
            event_type="action_executed",
            agent_id=action.agent_id,
            session_id=action.session_id,
            task_id=action.task_id,
            action_id=action.id,
            details={"result": result, "duration_ms": action.duration_ms},
        )
        await db.commit()
        return GatewayResult(allowed=True, result=result, error=None)
    except ToolExecutionError as e:
        action.status = "error"
        action.error = str(e)
        action.completed_at = datetime.now(timezone.utc)
        action.duration_ms = (time.monotonic() - start) * 1000
        await db.flush()
        await log_event(
            db,
            event_type="action_execution_error",
            agent_id=action.agent_id,
            session_id=action.session_id,
            task_id=action.task_id,
            action_id=action.id,
            details={"error": str(e)},
        )
        await db.commit()
        return GatewayResult(allowed=True, result=None, error=str(e))


async def _apply_budget(db: AsyncSession, session_row: models.Session, action: models.Action) -> None:
    """If the matched rule carried a budget, record consumption on the
    session so subsequent evaluate() calls see accurate budget_state."""
    if not action.policy_id or not action.rule_id:
        return
    policy_version = None
    for pv in await _policy_versions_for(db, action.policy_id):
        if pv.version == action.policy_version:
            policy_version = pv
            break
    if policy_version is None:
        return
    from harness.policy_engine.schema import PolicyDocument

    doc = PolicyDocument.model_validate(policy_version.document)
    rule = next((r for r in doc.rules if r.id == action.rule_id), None)
    if rule is None or rule.budget is None:
        return
    amount = _get_path(action.parameters or {}, rule.budget.amount_field.replace("parameters.", "", 1))
    try:
        amount = float(amount) if amount is not None else 0.0
    except (TypeError, ValueError):
        amount = 0.0
    state = dict(session_row.budget_state or {})
    state[rule.budget.key] = state.get(rule.budget.key, 0.0) + amount
    session_row.budget_state = state
    await db.flush()


def _get_path(obj: dict, dotted: str):
    cur = obj
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


async def _policy_versions_for(db: AsyncSession, policy_id: str):
    from sqlalchemy import select

    stmt = select(models.PolicyVersion).where(models.PolicyVersion.policy_id == policy_id)
    return (await db.execute(stmt)).scalars().all()

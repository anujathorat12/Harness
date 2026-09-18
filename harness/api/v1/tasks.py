from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from harness.api.v1 import schemas
from harness.core.database import get_db
from harness.core.security import ADMIN, AUDITOR, CLIENT, OPERATOR, Principal, require_role
from harness.domain import models
from harness.orchestrator.task_manager import cancel_session, submit_task

router = APIRouter(tags=["tasks"])

_VIEW_ROLES = (ADMIN, OPERATOR, AUDITOR, CLIENT)


async def _submit_task_for_agent(
    db: AsyncSession,
    *,
    agent_id: str,
    agent_version_id: str | None,
    task_input: dict,
    workspace_files: dict[str, str],
    principal: Principal,
) -> schemas.SubmitTaskResponse:
    """The one place a task actually gets created for an onboarded agent --
    both POST /tasks (flat, agent_id in the body) and POST
    /agents/{agent_id}/tasks (REST-nested, agent_id in the path) call this
    exact function. There is no per-agent branching here or anywhere
    downstream of it: `agent_id` only ever selects *which row* to read
    (Agent, AgentVersion), never *which code path* to run -- the same
    submit_task() -> Runtime -> gateway -> policy engine pipeline handles
    every agent, every shape, identically."""
    agent = await db.get(models.Agent, agent_id)
    if agent is None:
        raise HTTPException(404, "agent not found")

    if agent_version_id:
        version = await db.get(models.AgentVersion, agent_version_id)
        if version is None or version.agent_id != agent_id:
            raise HTTPException(404, "agent_version not found for this agent")
    else:
        stmt = (
            select(models.AgentVersion)
            .where(models.AgentVersion.agent_id == agent_id, models.AgentVersion.status == "active")
            .order_by(models.AgentVersion.version.desc())
        )
        version = (await db.execute(stmt)).scalars().first()
        if version is None:
            raise HTTPException(409, "agent has no active version")

    session_id, task_id = await submit_task(
        agent_id, version.id, task_input, workspace_files=workspace_files, created_by=principal.label
    )
    return schemas.SubmitTaskResponse(session_id=session_id, task_id=task_id, agent_id=agent_id, status="pending")


@router.post(
    "/tasks", response_model=schemas.SubmitTaskResponse, status_code=202,
)
async def create_task(
    body: schemas.SubmitTaskRequest,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_role(ADMIN, OPERATOR, CLIENT)),
):
    return await _submit_task_for_agent(
        db,
        agent_id=body.agent_id,
        agent_version_id=body.agent_version_id,
        task_input=body.input,
        workspace_files=body.workspace_files,
        principal=principal,
    )


@router.post(
    "/agents/{agent_id}/tasks",
    response_model=schemas.SubmitTaskResponse,
    status_code=202,
    summary="Invoke an onboarded agent (external application entry point)",
)
async def create_task_for_agent(
    agent_id: str,
    body: schemas.SubmitTaskToAgentRequest,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_role(ADMIN, OPERATOR, CLIENT)),
):
    """The REST contract external applications integrate against to invoke
    an agent onboarded into this platform. The external caller never talks
    to the agent, its runtime, or its container/process directly -- it
    identifies the agent purely by `agent_id` (from `GET /agents`) and
    submits input here; the Harness owns everything from this point on:
    starting the sandboxed runtime, receiving the agent's action requests,
    evaluating them against the attached policy, pausing for human approval
    when required, and recording the audit trail. Poll
    `GET /tasks/{task_id}` (already agent-agnostic) for status and result --
    see docs/EXTERNAL_INTEGRATION.md for the complete external-client flow."""
    return await _submit_task_for_agent(
        db,
        agent_id=agent_id,
        agent_version_id=body.agent_version_id,
        task_input=body.input,
        workspace_files=body.workspace_files,
        principal=principal,
    )


def _enforce_client_owns_task(task: models.Task, principal: Principal) -> None:
    if principal.role == CLIENT and task.created_by != principal.label:
        # 404, not 403: a CLIENT should not learn that a task ID exists at
        # all if it isn't theirs -- same reasoning as returning 404 for a
        # missing row, so existence isn't leaked to a role that has no
        # business knowing about other clients' tasks.
        raise HTTPException(404, "task not found")


@router.get("/tasks/{task_id}", response_model=schemas.TaskOut)
async def get_task(
    task_id: str, db: AsyncSession = Depends(get_db), principal: Principal = Depends(require_role(*_VIEW_ROLES))
):
    task = await db.get(models.Task, task_id)
    if task is None:
        raise HTTPException(404, "task not found")
    _enforce_client_owns_task(task, principal)
    return task


@router.get("/tasks", response_model=list[schemas.TaskOut])
async def list_tasks(
    session_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_role(*_VIEW_ROLES)),
):
    stmt = select(models.Task)
    if session_id:
        stmt = stmt.where(models.Task.session_id == session_id)
    if status:
        stmt = stmt.where(models.Task.status == status)
    if principal.role == CLIENT:
        stmt = stmt.where(models.Task.created_by == principal.label)
    stmt = stmt.order_by(models.Task.created_at.desc()).limit(min(limit, 200)).offset(offset)
    return (await db.execute(stmt)).scalars().all()


@router.get("/sessions/{session_id}", response_model=schemas.SessionOut)
async def get_session(
    session_id: str, db: AsyncSession = Depends(get_db), principal: Principal = Depends(require_role(*_VIEW_ROLES))
):
    session = await db.get(models.Session, session_id)
    if session is None:
        raise HTTPException(404, "session not found")
    if principal.role == CLIENT:
        owns = (
            await db.execute(
                select(models.Task).where(models.Task.session_id == session_id, models.Task.created_by == principal.label)
            )
        ).scalars().first()
        if owns is None:
            raise HTTPException(404, "session not found")
    return session


@router.get("/sessions", response_model=list[schemas.SessionOut])
async def list_sessions(
    agent_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_role(*_VIEW_ROLES)),
):
    stmt = select(models.Session)
    if agent_id:
        stmt = stmt.where(models.Session.agent_id == agent_id)
    if status:
        stmt = stmt.where(models.Session.status == status)
    if principal.role == CLIENT:
        stmt = stmt.join(models.Task, models.Task.session_id == models.Session.id).where(
            models.Task.created_by == principal.label
        )
    stmt = stmt.order_by(models.Session.created_at.desc()).limit(min(limit, 200)).offset(offset)
    return (await db.execute(stmt)).scalars().all()


@router.post("/sessions/{session_id}/cancel", status_code=202, dependencies=[Depends(require_role(ADMIN, OPERATOR))])
async def cancel(session_id: str, db: AsyncSession = Depends(get_db)):
    session = await db.get(models.Session, session_id)
    if session is None:
        raise HTTPException(404, "session not found")
    cancelled = await cancel_session(session_id, reason="operator requested cancellation via API")
    return {"cancelled": cancelled}


@router.get("/tasks/{task_id}/actions", response_model=list[schemas.ActionOut])
async def list_task_actions(
    task_id: str, db: AsyncSession = Depends(get_db), principal: Principal = Depends(require_role(*_VIEW_ROLES))
):
    task = await db.get(models.Task, task_id)
    if task is None:
        raise HTTPException(404, "task not found")
    _enforce_client_owns_task(task, principal)
    stmt = select(models.Action).where(models.Action.task_id == task_id).order_by(models.Action.created_at.asc())
    return (await db.execute(stmt)).scalars().all()


@router.get(
    "/actions/{action_id}", response_model=schemas.ActionOut, dependencies=[Depends(require_role(ADMIN, OPERATOR, AUDITOR))]
)
async def get_action(action_id: str, db: AsyncSession = Depends(get_db)):
    """Single-action lookup, mainly for the admin UI's Approvals page: an
    Approval row only carries `action_id`, so this is how it renders the
    actual action_type/resource/agent that's waiting on a decision."""
    action = await db.get(models.Action, action_id)
    if action is None:
        raise HTTPException(404, "action not found")
    return action

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from harness.api.v1 import schemas
from harness.core.database import get_db
from harness.domain import models
from harness.orchestrator.task_manager import cancel_session, submit_task

router = APIRouter(tags=["tasks"])


@router.post("/tasks", response_model=schemas.SubmitTaskResponse, status_code=202)
async def create_task(body: schemas.SubmitTaskRequest, db: AsyncSession = Depends(get_db)):
    agent = await db.get(models.Agent, body.agent_id)
    if agent is None:
        raise HTTPException(404, "agent not found")

    if body.agent_version_id:
        version = await db.get(models.AgentVersion, body.agent_version_id)
        if version is None or version.agent_id != body.agent_id:
            raise HTTPException(404, "agent_version not found for this agent")
    else:
        stmt = (
            select(models.AgentVersion)
            .where(models.AgentVersion.agent_id == body.agent_id, models.AgentVersion.status == "active")
            .order_by(models.AgentVersion.version.desc())
        )
        version = (await db.execute(stmt)).scalars().first()
        if version is None:
            raise HTTPException(409, "agent has no active version")

    session_id, task_id = await submit_task(body.agent_id, version.id, body.input, workspace_files=body.workspace_files)
    return schemas.SubmitTaskResponse(session_id=session_id, task_id=task_id)


@router.get("/tasks/{task_id}", response_model=schemas.TaskOut)
async def get_task(task_id: str, db: AsyncSession = Depends(get_db)):
    task = await db.get(models.Task, task_id)
    if task is None:
        raise HTTPException(404, "task not found")
    return task


@router.get("/tasks", response_model=list[schemas.TaskOut])
async def list_tasks(session_id: str | None = None, status: str | None = None, limit: int = 50, offset: int = 0,
                      db: AsyncSession = Depends(get_db)):
    stmt = select(models.Task)
    if session_id:
        stmt = stmt.where(models.Task.session_id == session_id)
    if status:
        stmt = stmt.where(models.Task.status == status)
    stmt = stmt.order_by(models.Task.created_at.desc()).limit(min(limit, 200)).offset(offset)
    return (await db.execute(stmt)).scalars().all()


@router.get("/sessions/{session_id}", response_model=schemas.SessionOut)
async def get_session(session_id: str, db: AsyncSession = Depends(get_db)):
    session = await db.get(models.Session, session_id)
    if session is None:
        raise HTTPException(404, "session not found")
    return session


@router.get("/sessions", response_model=list[schemas.SessionOut])
async def list_sessions(agent_id: str | None = None, status: str | None = None, limit: int = 50, offset: int = 0,
                         db: AsyncSession = Depends(get_db)):
    stmt = select(models.Session)
    if agent_id:
        stmt = stmt.where(models.Session.agent_id == agent_id)
    if status:
        stmt = stmt.where(models.Session.status == status)
    stmt = stmt.order_by(models.Session.created_at.desc()).limit(min(limit, 200)).offset(offset)
    return (await db.execute(stmt)).scalars().all()


@router.post("/sessions/{session_id}/cancel", status_code=202)
async def cancel(session_id: str, db: AsyncSession = Depends(get_db)):
    session = await db.get(models.Session, session_id)
    if session is None:
        raise HTTPException(404, "session not found")
    cancelled = await cancel_session(session_id, reason="operator requested cancellation via API")
    return {"cancelled": cancelled}


@router.get("/tasks/{task_id}/actions", response_model=list[schemas.ActionOut])
async def list_task_actions(task_id: str, db: AsyncSession = Depends(get_db)):
    stmt = select(models.Action).where(models.Action.task_id == task_id).order_by(models.Action.created_at.asc())
    return (await db.execute(stmt)).scalars().all()

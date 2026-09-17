from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from harness.api.v1 import schemas
from harness.core.database import get_db
from harness.domain import models

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("/events", response_model=list[schemas.AuditEventOut])
async def query_audit(
    agent_id: str | None = None,
    session_id: str | None = None,
    task_id: str | None = None,
    action_id: str | None = None,
    event_type: str | None = None,
    limit: int = 100,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    """Reconstructs what happened: who did what, when, against what, under
    which policy/version/rule, what decision, and what happened afterward --
    the questions ARCHITECTURE.md's audit model section commits to
    answering. Every action's full story is `GET /audit/events?action_id=...`."""
    stmt = select(models.AuditEvent)
    if agent_id:
        stmt = stmt.where(models.AuditEvent.agent_id == agent_id)
    if session_id:
        stmt = stmt.where(models.AuditEvent.session_id == session_id)
    if task_id:
        stmt = stmt.where(models.AuditEvent.task_id == task_id)
    if action_id:
        stmt = stmt.where(models.AuditEvent.action_id == action_id)
    if event_type:
        stmt = stmt.where(models.AuditEvent.event_type == event_type)
    stmt = stmt.order_by(models.AuditEvent.timestamp.asc()).limit(min(limit, 500)).offset(offset)
    return (await db.execute(stmt)).scalars().all()

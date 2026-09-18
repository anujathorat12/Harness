from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from harness.api.v1 import schemas
from harness.core.database import get_db
from harness.core.security import ADMIN, AUDITOR, OPERATOR, require_role
from harness.domain import models

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


async def _count(db: AsyncSession, *where) -> int:
    stmt = select(func.count()).select_from(models.Session)
    for clause in where:
        stmt = stmt.where(clause)
    return (await db.execute(stmt)).scalar_one()


@router.get("/summary", response_model=schemas.DashboardSummary)
async def summary(db: AsyncSession = Depends(get_db), _principal=Depends(require_role(ADMIN, OPERATOR, AUDITOR))):
    """One-call aggregate view for the admin UI's landing page. Every number
    here is a plain read of the same durable rows the rest of the API
    exposes (Session/Approval/Action/AuditEvent) -- nothing is cached or
    computed from in-process state, so this is always consistent with what
    GET /sessions, /approvals, /audit/events would show."""
    agents_total = (await db.execute(select(func.count()).select_from(models.Agent))).scalar_one()
    sessions_running = await _count(db, models.Session.status == "running")
    sessions_completed = await _count(db, models.Session.status == "completed")
    sessions_failed = await _count(db, models.Session.status == "failed")
    approvals_pending = (
        await db.execute(select(func.count()).select_from(models.Approval).where(models.Approval.status == "pending"))
    ).scalar_one()

    recent_actions = (
        (await db.execute(select(models.Action).order_by(models.Action.created_at.desc()).limit(10))).scalars().all()
    )
    recent_audit_events = (
        (await db.execute(select(models.AuditEvent).order_by(models.AuditEvent.timestamp.desc()).limit(10)))
        .scalars()
        .all()
    )

    return schemas.DashboardSummary(
        agents_total=agents_total,
        sessions_running=sessions_running,
        sessions_completed=sessions_completed,
        sessions_failed=sessions_failed,
        approvals_pending=approvals_pending,
        recent_actions=list(recent_actions),
        recent_audit_events=list(recent_audit_events),
    )

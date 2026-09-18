from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from harness.api.v1 import schemas
from harness.approval.approval_manager import DuplicateResolutionError, resolve_approval
from harness.core.database import get_db
from harness.core.security import ADMIN, OPERATOR, Principal, require_role
from harness.domain import models

router = APIRouter(prefix="/approvals", tags=["approvals"])


@router.get("", response_model=list[schemas.ApprovalOut], dependencies=[Depends(require_role(ADMIN, OPERATOR))])
async def list_approvals(status: str | None = "pending", limit: int = 50, db: AsyncSession = Depends(get_db)):
    stmt = select(models.Approval)
    if status:
        stmt = stmt.where(models.Approval.status == status)
    stmt = stmt.order_by(models.Approval.created_at.asc()).limit(min(limit, 200))
    return (await db.execute(stmt)).scalars().all()


@router.get(
    "/{approval_id}", response_model=schemas.ApprovalOut, dependencies=[Depends(require_role(ADMIN, OPERATOR))]
)
async def get_approval(approval_id: str, db: AsyncSession = Depends(get_db)):
    approval = await db.get(models.Approval, approval_id)
    if approval is None:
        raise HTTPException(404, "approval not found")
    return approval


@router.post("/{approval_id}/resolve", response_model=schemas.ApprovalOut)
async def resolve(
    approval_id: str,
    body: schemas.ResolveApprovalRequest,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_role(ADMIN, OPERATOR)),
):
    """Approve or deny a paused action. The FIRST resolution wins; a second
    call (duplicate, or a conflicting decision arriving late) is rejected
    with 409 rather than silently overwriting the recorded outcome.

    `approver` is the authenticated caller's principal label unless the
    request body explicitly overrides it (useful for a human-readable name
    from Swagger/curl) -- either way, WHO is allowed to call this at all is
    enforced by the require_role dependency above, not by the approver
    string, which is recorded for audit display only."""
    approver = body.approver or principal.label
    try:
        return await resolve_approval(db, approval_id=approval_id, approve=body.approve, approver=approver, reason=body.reason)
    except DuplicateResolutionError as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(404, str(e))

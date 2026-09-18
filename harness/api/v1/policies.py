from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from harness.api.v1 import schemas
from harness.audit.audit_logger import log_event
from harness.core.database import get_db
from harness.core.security import ADMIN, OPERATOR, require_role
from harness.domain import models
from harness.policy_engine.loader import PolicyValidationError, dump_canonical_json, parse_policy_source

router = APIRouter(prefix="/policies", tags=["policies"])


@router.post("", response_model=schemas.PolicyOut, status_code=201, dependencies=[Depends(require_role(ADMIN))])
async def create_policy(body: schemas.CreatePolicyRequest, db: AsyncSession = Depends(get_db)):
    """Creates a policy and its first immutable version. The submitted
    source is validated against the canonical schema BEFORE anything is
    persisted -- an invalid policy never becomes a PolicyVersion row
    (secure default: invalid policy -> reject, not "reject at enforcement
    time")."""
    existing = (await db.execute(select(models.Policy).where(models.Policy.name == body.name))).scalars().first()
    if existing:
        raise HTTPException(409, f"a policy named {body.name!r} already exists; POST a new version instead")

    try:
        doc = parse_policy_source(body.source)
    except PolicyValidationError as e:
        raise HTTPException(422, f"policy validation failed: {e}")

    policy = models.Policy(name=body.name, description=body.description)
    db.add(policy)
    await db.flush()

    version = models.PolicyVersion(
        policy_id=policy.id, version=doc.version, document=dump_canonical_json(doc), raw_source=body.source
    )
    db.add(version)
    await db.flush()
    await log_event(db, event_type="policy_created", policy_id=policy.id, policy_version=doc.version,
                     details={"name": body.name, "rule_count": len(doc.rules)})
    await db.commit()
    await db.refresh(policy, attribute_names=["versions"])
    return policy


@router.post(
    "/{policy_id}/versions",
    response_model=schemas.PolicyVersionOut,
    status_code=201,
    dependencies=[Depends(require_role(ADMIN))],
)
async def add_policy_version(policy_id: str, body: schemas.AddPolicyVersionRequest, db: AsyncSession = Depends(get_db)):
    policy = await db.get(models.Policy, policy_id)
    if policy is None:
        raise HTTPException(404, "policy not found")
    try:
        doc = parse_policy_source(body.source)
    except PolicyValidationError as e:
        raise HTTPException(422, f"policy validation failed: {e}")

    existing_version = (
        await db.execute(
            select(models.PolicyVersion).where(
                models.PolicyVersion.policy_id == policy_id, models.PolicyVersion.version == doc.version
            )
        )
    ).scalars().first()
    if existing_version:
        raise HTTPException(409, f"policy version {doc.version} already exists for this policy (versions are immutable)")

    version = models.PolicyVersion(
        policy_id=policy_id, version=doc.version, document=dump_canonical_json(doc), raw_source=body.source
    )
    db.add(version)
    await db.flush()
    await log_event(db, event_type="policy_version_created", policy_id=policy_id, policy_version=doc.version)
    await db.commit()
    return version


@router.get("", response_model=list[schemas.PolicyOut], dependencies=[Depends(require_role(ADMIN, OPERATOR))])
async def list_policies(db: AsyncSession = Depends(get_db)):
    stmt = select(models.Policy).options(selectinload(models.Policy.versions))
    return (await db.execute(stmt)).scalars().all()


@router.get("/{policy_id}", response_model=schemas.PolicyOut, dependencies=[Depends(require_role(ADMIN, OPERATOR))])
async def get_policy(policy_id: str, db: AsyncSession = Depends(get_db)):
    stmt = select(models.Policy).where(models.Policy.id == policy_id).options(selectinload(models.Policy.versions))
    policy = (await db.execute(stmt)).scalars().first()
    if policy is None:
        raise HTTPException(404, "policy not found")
    return policy


@router.get(
    "/attachments/lookup",
    response_model=schemas.PolicyAttachmentOut | None,
    dependencies=[Depends(require_role(ADMIN, OPERATOR))],
)
async def lookup_attachment(
    scope_type: Literal["agent", "session"], scope_id: str, db: AsyncSession = Depends(get_db)
):
    """Read-only convenience for the admin UI ('which policy governs this
    agent/session right now?'). Purely a projection of PolicyAttachment rows
    -- it does not participate in policy resolution at enforcement time,
    which stays entirely inside harness/policy_engine/resolver.py."""
    stmt = (
        select(models.PolicyAttachment)
        .where(models.PolicyAttachment.scope_type == scope_type, models.PolicyAttachment.scope_id == scope_id)
        .order_by(models.PolicyAttachment.created_at.desc())
    )
    attachment = (await db.execute(stmt)).scalars().first()
    if attachment is None:
        return None
    pv = await db.get(models.PolicyVersion, attachment.policy_version_id)
    if pv is None:
        return None
    policy = await db.get(models.Policy, pv.policy_id)
    return schemas.PolicyAttachmentOut(
        policy_id=pv.policy_id,
        policy_name=policy.name if policy else "",
        version=pv.version,
        scope_type=scope_type,
        scope_id=scope_id,
    )


@router.post("/{policy_id}/versions/{version}/attach", status_code=201, dependencies=[Depends(require_role(ADMIN))])
async def attach_policy(policy_id: str, version: int, body: schemas.AttachPolicyRequest, db: AsyncSession = Depends(get_db)):
    """Attaches a specific, immutable policy VERSION to an agent (default
    for all its sessions) or a single session (overrides the agent default
    for that session only)."""
    pv = (
        await db.execute(
            select(models.PolicyVersion).where(
                models.PolicyVersion.policy_id == policy_id, models.PolicyVersion.version == version
            )
        )
    ).scalars().first()
    if pv is None:
        raise HTTPException(404, "policy version not found")

    if body.scope_type == "agent" and await db.get(models.Agent, body.scope_id) is None:
        raise HTTPException(404, "agent not found")
    if body.scope_type == "session" and await db.get(models.Session, body.scope_id) is None:
        raise HTTPException(404, "session not found")

    # Replace any existing attachment at this exact scope (an agent/session
    # has exactly one active attachment at a time; re-attaching supersedes).
    existing = (
        await db.execute(
            select(models.PolicyAttachment).where(
                models.PolicyAttachment.scope_type == body.scope_type,
                models.PolicyAttachment.scope_id == body.scope_id,
            )
        )
    ).scalars().all()
    for row in existing:
        await db.delete(row)

    attachment = models.PolicyAttachment(policy_version_id=pv.id, scope_type=body.scope_type, scope_id=body.scope_id)
    db.add(attachment)
    await db.flush()
    await log_event(
        db,
        event_type="policy_attached",
        policy_id=policy_id,
        policy_version=version,
        agent_id=body.scope_id if body.scope_type == "agent" else None,
        session_id=body.scope_id if body.scope_type == "session" else None,
        details={"scope_type": body.scope_type, "scope_id": body.scope_id},
    )
    await db.commit()
    return {"attached": True, "policy_id": policy_id, "policy_version": version, "scope_type": body.scope_type, "scope_id": body.scope_id}

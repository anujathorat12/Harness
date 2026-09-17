from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from harness.domain import models
from harness.policy_engine.schema import PolicyDocument


class NoPolicyAttachedError(Exception):
    """Raised when a session has no policy attachment at all. Per the
    secure-defaults requirement, the caller must treat this as deny-all,
    not as 'no policy means unrestricted'."""


async def resolve_effective_policy(
    db: AsyncSession, *, agent_id: str, session_id: str
) -> tuple[PolicyDocument, models.PolicyVersion]:
    # Session-scoped attachment wins if present.
    stmt = select(models.PolicyAttachment).where(
        models.PolicyAttachment.scope_type == "session",
        models.PolicyAttachment.scope_id == session_id,
    )
    attachment = (await db.execute(stmt)).scalars().first()

    if attachment is None:
        stmt = select(models.PolicyAttachment).where(
            models.PolicyAttachment.scope_type == "agent",
            models.PolicyAttachment.scope_id == agent_id,
        )
        attachment = (await db.execute(stmt)).scalars().first()

    if attachment is None:
        raise NoPolicyAttachedError(f"no policy attached to agent={agent_id} or session={session_id}")

    version = await db.get(models.PolicyVersion, attachment.policy_version_id)
    if version is None:
        raise NoPolicyAttachedError("attached policy version no longer exists")

    doc = PolicyDocument.model_validate(version.document)
    return doc, version

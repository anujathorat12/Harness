from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from harness.api.v1 import schemas
from harness.audit.audit_logger import log_event
from harness.core.database import get_db
from harness.core.security import ADMIN, OPERATOR, require_role
from harness.domain import models

router = APIRouter(prefix="/agents", tags=["agents"])


@router.post("", response_model=schemas.AgentOut, status_code=201, dependencies=[Depends(require_role(ADMIN))])
async def register_agent(body: schemas.RegisterAgentRequest, db: AsyncSession = Depends(get_db)):
    """Register a new agent identity and its first runnable version. This is
    the BYOA entry point: any team can call this with either agent 'shape'
    (code or declarative) and get back an agent_id + version_id to submit
    tasks against."""
    existing = (await db.execute(select(models.Agent).where(models.Agent.name == body.name))).scalars().first()
    if existing:
        raise HTTPException(409, f"an agent named {body.name!r} is already registered")

    agent = models.Agent(name=body.name, description=body.description, owner=body.owner, shape=body.shape)
    db.add(agent)
    await db.flush()

    version = models.AgentVersion(
        agent_id=agent.id,
        version=1,
        status="active",
        runtime_kind=body.initial_version.runtime_kind,
        spec=body.initial_version.spec,
        resource_limits=body.initial_version.resource_limits,
    )
    db.add(version)
    await db.flush()

    await log_event(db, event_type="agent_registered", agent_id=agent.id, actor=body.owner,
                     details={"name": body.name, "shape": body.shape, "runtime_kind": version.runtime_kind})
    await db.commit()
    await db.refresh(agent, attribute_names=["versions"])
    return agent


@router.post(
    "/{agent_id}/versions",
    response_model=schemas.AgentVersionOut,
    status_code=201,
    dependencies=[Depends(require_role(ADMIN))],
)
async def add_agent_version(agent_id: str, body: schemas.AddAgentVersionRequest, db: AsyncSession = Depends(get_db)):
    agent = await db.get(models.Agent, agent_id)
    if agent is None:
        raise HTTPException(404, "agent not found")
    latest = (
        await db.execute(
            select(models.AgentVersion).where(models.AgentVersion.agent_id == agent_id).order_by(models.AgentVersion.version.desc())
        )
    ).scalars().first()
    next_version = (latest.version + 1) if latest else 1
    version = models.AgentVersion(
        agent_id=agent_id,
        version=next_version,
        status="active",
        runtime_kind=body.runtime_kind,
        spec=body.spec,
        resource_limits=body.resource_limits,
    )
    db.add(version)
    await db.flush()
    await log_event(db, event_type="agent_version_registered", agent_id=agent_id,
                     details={"version": next_version, "runtime_kind": body.runtime_kind})
    await db.commit()
    return version


@router.get("", response_model=list[schemas.AgentOut], dependencies=[Depends(require_role(ADMIN, OPERATOR))])
async def list_agents(db: AsyncSession = Depends(get_db)):
    stmt = select(models.Agent).options(selectinload(models.Agent.versions))
    return (await db.execute(stmt)).scalars().all()


@router.get("/{agent_id}", response_model=schemas.AgentOut, dependencies=[Depends(require_role(ADMIN, OPERATOR))])
async def get_agent(agent_id: str, db: AsyncSession = Depends(get_db)):
    stmt = select(models.Agent).where(models.Agent.id == agent_id).options(selectinload(models.Agent.versions))
    agent = (await db.execute(stmt)).scalars().first()
    if agent is None:
        raise HTTPException(404, "agent not found")
    return agent

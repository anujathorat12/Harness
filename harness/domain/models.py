"""
Persistent data model.

Everything that needs to survive a restart lives here: agents, policies
(immutable per version), sessions, tasks, actions, approvals, and the audit
log. In-memory-only state (e.g. an asyncio.Event used to wake a paused task)
is layered on top of these rows in the orchestrator, never instead of them --
see ARCHITECTURE.md "State & restart" for exactly what that means.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from harness.core.database import Base


def _id() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Agent(Base):
    """A registered agent identity. Versions carry the actual runnable
    definition; the Agent row is the stable identity/name an owner registers
    once and then publishes new versions under."""

    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    name: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    owner: Mapped[str] = mapped_column(String(200))
    shape: Mapped[str] = mapped_column(String(20))  # "code" | "declarative"
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    versions: Mapped[list["AgentVersion"]] = relationship(back_populates="agent", cascade="all, delete-orphan")


class AgentVersion(Base):
    """One immutable, runnable version of an agent. `runtime_kind` selects
    which Runtime implementation executes it. `spec` is the shape-specific
    payload:
      - code agents:        {"image": ..., "command": [...], "env": {...}}
                             or {"entrypoint_script": "<path inside package>"}
      - declarative agents: {"steps": [{"action": ..., "resource": ..., "parameters": {...}}, ...]}
    """

    __tablename__ = "agent_versions"
    __table_args__ = (Index("ix_agent_version_unique", "agent_id", "version", unique=True),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active|deprecated|disabled
    runtime_kind: Mapped[str] = mapped_column(String(20))  # "process" | "container" | "declarative"
    spec: Mapped[dict] = mapped_column(JSON)
    resource_limits: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    agent: Mapped["Agent"] = relationship(back_populates="versions")


class Policy(Base):
    __tablename__ = "policies"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    name: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    versions: Mapped[list["PolicyVersion"]] = relationship(back_populates="policy", cascade="all, delete-orphan")


class PolicyVersion(Base):
    """An immutable, versioned policy document. Once created, a version's
    `document` never changes -- editing a policy means authoring a new
    version. This is what lets the audit trail say, truthfully, "policy X
    version N governed this action" forever, even after the policy is later
    edited."""

    __tablename__ = "policy_versions"
    __table_args__ = (Index("ix_policy_version_unique", "policy_id", "version", unique=True),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    policy_id: Mapped[str] = mapped_column(ForeignKey("policies.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    document: Mapped[dict] = mapped_column(JSON)  # parsed + schema-validated canonical form
    raw_source: Mapped[str] = mapped_column(Text)  # original YAML/JSON as submitted
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    policy: Mapped["Policy"] = relationship(back_populates="versions")


class PolicyAttachment(Base):
    """Binds a policy VERSION to a scope: either an agent (applies to every
    session of that agent unless overridden) or a specific session (overrides
    the agent-level policy for that session only). Per-session override
    combined with per-agent default is what "attachable per-agent or
    per-session" means operationally."""

    __tablename__ = "policy_attachments"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    policy_version_id: Mapped[str] = mapped_column(ForeignKey("policy_versions.id"), index=True)
    scope_type: Mapped[str] = mapped_column(String(10))  # "agent" | "session"
    scope_id: Mapped[str] = mapped_column(String(32), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Session(Base):
    """One isolated execution context for an agent: one sandbox instance,
    one policy binding, one lifetime. A session can run multiple tasks
    sequentially but never concurrently with itself."""

    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), index=True)
    agent_version_id: Mapped[str] = mapped_column(ForeignKey("agent_versions.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    # pending|running|paused_for_approval|completed|failed|terminated
    budget_state: Mapped[dict] = mapped_column(JSON, default=dict)  # {budget_key: consumed_amount}
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), index=True)
    input: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    # pending|running|paused_for_approval|succeeded|failed|cancelled
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Action(Base):
    """One governed action attempt by an agent. This row exists BEFORE the
    policy decision is known (status=pending) so that even an action that
    crashes mid-evaluation leaves a trace -- there is no code path that
    executes a tool without first writing this row."""

    __tablename__ = "actions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id"), index=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id"), index=True)

    action_type: Mapped[str] = mapped_column(String(100), index=True)
    resource: Mapped[str | None] = mapped_column(String(500), nullable=True)
    parameters: Mapped[dict] = mapped_column(JSON, default=dict)  # redacted before storage

    policy_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    policy_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rule_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    decision: Mapped[str | None] = mapped_column(String(20), nullable=True)  # allow|deny|require_approval

    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    # pending -> decided -> (executing -> completed|error) | denied | approval_pending -> approved/rejected/timed_out
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[float | None] = mapped_column(Float, nullable=True)


class Approval(Base):
    __tablename__ = "approvals"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    action_id: Mapped[str] = mapped_column(ForeignKey("actions.id"), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)  # pending|approved|denied|timed_out
    approver: Mapped[str | None] = mapped_column(String(200), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    timeout_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditEvent(Base):
    """Append-only. Nothing in this codebase ever UPDATEs or DELETEs a row in
    this table -- audit_logger only INSERTs. See audit/audit_logger.py."""

    __tablename__ = "audit_events"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_id)
    event_type: Mapped[str] = mapped_column(String(50), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)

    agent_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    session_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    task_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    action_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    approval_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)

    policy_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    policy_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rule_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    decision: Mapped[str | None] = mapped_column(String(20), nullable=True)

    actor: Mapped[str] = mapped_column(String(200), default="system")
    details: Mapped[dict] = mapped_column(JSON, default=dict)  # redacted

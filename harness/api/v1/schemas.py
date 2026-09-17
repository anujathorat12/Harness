from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# ---------- Agents ----------

class AgentVersionSpec(BaseModel):
    runtime_kind: Literal["process", "container", "declarative"]
    spec: dict[str, Any] = Field(description="Shape-specific runnable definition; see docs/AGENT_PROTOCOL.md")
    resource_limits: dict[str, Any] = Field(default_factory=dict)


class RegisterAgentRequest(BaseModel):
    name: str
    description: str = ""
    owner: str
    shape: Literal["code", "declarative"]
    initial_version: AgentVersionSpec


class AgentVersionOut(BaseModel):
    id: str
    version: int
    status: str
    runtime_kind: str
    resource_limits: dict[str, Any]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AgentOut(BaseModel):
    id: str
    name: str
    description: str
    owner: str
    shape: str
    created_at: datetime
    versions: list[AgentVersionOut] = []

    model_config = ConfigDict(from_attributes=True)


class AddAgentVersionRequest(BaseModel):
    runtime_kind: Literal["process", "container", "declarative"]
    spec: dict[str, Any]
    resource_limits: dict[str, Any] = Field(default_factory=dict)


# ---------- Policies ----------

class CreatePolicyRequest(BaseModel):
    name: str
    description: str = ""
    source: str = Field(description="YAML or JSON policy document, see docs/POLICY_FORMAT.md")


class AddPolicyVersionRequest(BaseModel):
    source: str


class PolicyVersionOut(BaseModel):
    id: str
    version: int
    document: dict[str, Any]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PolicyOut(BaseModel):
    id: str
    name: str
    description: str
    created_at: datetime
    versions: list[PolicyVersionOut] = []

    model_config = ConfigDict(from_attributes=True)


class AttachPolicyRequest(BaseModel):
    scope_type: Literal["agent", "session"]
    scope_id: str


# ---------- Tasks / Sessions ----------

class SubmitTaskRequest(BaseModel):
    agent_id: str
    agent_version_id: str | None = Field(default=None, description="Defaults to the agent's latest active version")
    input: dict[str, Any] = Field(default_factory=dict)
    workspace_files: dict[str, str] = Field(
        default_factory=dict,
        description="Optional seed files written into the session's isolated workspace before the agent starts, "
        "keyed by path relative to /workspace (e.g. {'report.txt': 'contents...'}).",
    )


class SubmitTaskResponse(BaseModel):
    session_id: str
    task_id: str


class TaskOut(BaseModel):
    id: str
    session_id: str
    status: str
    input: dict[str, Any]
    result: dict[str, Any] | None
    error: str | None
    created_at: datetime
    started_at: datetime | None
    ended_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class SessionOut(BaseModel):
    id: str
    agent_id: str
    agent_version_id: str
    status: str
    budget_state: dict[str, Any]
    error: str | None
    created_at: datetime
    started_at: datetime | None
    ended_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


# ---------- Approvals ----------

class ResolveApprovalRequest(BaseModel):
    approve: bool
    approver: str
    reason: str | None = None


class ApprovalOut(BaseModel):
    id: str
    action_id: str
    status: str
    approver: str | None
    reason: str | None
    created_at: datetime
    timeout_at: datetime
    resolved_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class ActionOut(BaseModel):
    id: str
    task_id: str
    session_id: str
    agent_id: str
    action_type: str
    resource: str | None
    parameters: dict[str, Any]
    policy_id: str | None
    policy_version: int | None
    rule_id: str | None
    decision: str | None
    status: str
    result: dict[str, Any] | None
    error: str | None
    created_at: datetime
    decided_at: datetime | None
    completed_at: datetime | None
    duration_ms: float | None

    model_config = ConfigDict(from_attributes=True)


# ---------- Audit ----------

class AuditEventOut(BaseModel):
    id: str
    event_type: str
    timestamp: datetime
    agent_id: str | None
    session_id: str | None
    task_id: str | None
    action_id: str | None
    approval_id: str | None
    policy_id: str | None
    policy_version: int | None
    rule_id: str | None
    decision: str | None
    actor: str
    details: dict[str, Any]

    model_config = ConfigDict(from_attributes=True)


class ErrorResponse(BaseModel):
    error: str
    detail: str | None = None

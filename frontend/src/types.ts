// Mirrors harness/api/v1/schemas.py exactly. This file has no logic -- it
// only describes shapes the backend already returns, so there is nothing
// here for the frontend to get out of sync with the Policy Engine, the
// approval flow, or any other backend-authoritative decision.

export type Role = "ADMIN" | "OPERATOR" | "AUDITOR" | "CLIENT";

export interface Principal {
  role: Role;
  label: string;
}

export interface AgentVersion {
  id: string;
  version: number;
  status: string;
  runtime_kind: string;
  resource_limits: Record<string, unknown>;
  created_at: string;
}

export interface Agent {
  id: string;
  name: string;
  description: string;
  owner: string;
  shape: string;
  created_at: string;
  versions: AgentVersion[];
}

export interface PolicyVersion {
  id: string;
  version: number;
  document: Record<string, unknown>;
  created_at: string;
}

export interface Policy {
  id: string;
  name: string;
  description: string;
  created_at: string;
  versions: PolicyVersion[];
}

export interface PolicyAttachment {
  policy_id: string;
  policy_name: string;
  version: number;
  scope_type: string;
  scope_id: string;
}

export interface Task {
  id: string;
  session_id: string;
  status: string;
  input: Record<string, unknown>;
  result: Record<string, unknown> | null;
  error: string | null;
  created_by: string | null;
  created_at: string;
  started_at: string | null;
  ended_at: string | null;
}

export interface Session {
  id: string;
  agent_id: string;
  agent_version_id: string;
  status: string;
  budget_state: Record<string, number>;
  error: string | null;
  created_at: string;
  started_at: string | null;
  ended_at: string | null;
}

export interface Approval {
  id: string;
  action_id: string;
  status: string;
  approver: string | null;
  reason: string | null;
  created_at: string;
  timeout_at: string;
  resolved_at: string | null;
}

export interface Action {
  id: string;
  task_id: string;
  session_id: string;
  agent_id: string;
  action_type: string;
  resource: string | null;
  parameters: Record<string, unknown>;
  policy_id: string | null;
  policy_version: number | null;
  rule_id: string | null;
  decision: string | null;
  status: string;
  result: Record<string, unknown> | null;
  error: string | null;
  created_at: string;
  decided_at: string | null;
  completed_at: string | null;
  duration_ms: number | null;
}

export interface AuditEvent {
  id: string;
  event_type: string;
  timestamp: string;
  agent_id: string | null;
  session_id: string | null;
  task_id: string | null;
  action_id: string | null;
  approval_id: string | null;
  policy_id: string | null;
  policy_version: number | null;
  rule_id: string | null;
  decision: string | null;
  actor: string;
  details: Record<string, unknown>;
}

export interface DashboardSummary {
  agents_total: number;
  sessions_running: number;
  sessions_completed: number;
  sessions_failed: number;
  approvals_pending: number;
  recent_actions: Action[];
  recent_audit_events: AuditEvent[];
}

export interface SystemStatus {
  environment: string;
  api_version: string;
  database: string;
  sessions_running_now: number;
  max_concurrent_sessions_total: number;
  max_concurrent_sessions_per_agent: number;
  container_runtime_available: boolean;
  container_runtime_note: string;
}

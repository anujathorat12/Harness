// Thin fetch wrapper. This file's entire job is: attach the stored API key,
// prefix the base URL, and turn a non-2xx response into a thrown Error with
// the backend's own message. It contains zero policy/approval/authorization
// logic of its own -- every decision comes back from the backend untouched.
import type {
  Agent,
  Approval,
  AuditEvent,
  DashboardSummary,
  Policy,
  PolicyAttachment,
  Principal,
  Session,
  SystemStatus,
  Task,
  Action,
} from "./types";

const BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";
const STORAGE_KEY = "harness_api_key";

export function getStoredApiKey(): string | null {
  try {
    return localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

export function setStoredApiKey(key: string | null): void {
  try {
    if (key) localStorage.setItem(STORAGE_KEY, key);
    else localStorage.removeItem(STORAGE_KEY);
  } catch {
    // ignore -- storage may be unavailable (private mode, etc.); the app
    // still works, it just re-prompts for the key on next load.
  }
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const key = getStoredApiKey();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(key ? { "X-API-Key": key } : {}),
    ...((options.headers as Record<string, string>) || {}),
  };
  const res = await fetch(`${BASE_URL}${path}`, { ...options, headers });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail || body.error || detail;
    } catch {
      // response wasn't JSON -- keep statusText
    }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  me: () => request<Principal>("/api/v1/auth/me"),

  dashboardSummary: () => request<DashboardSummary>("/api/v1/dashboard/summary"),
  systemStatus: () => request<SystemStatus>("/api/v1/system/status"),

  listAgents: () => request<Agent[]>("/api/v1/agents"),
  getAgent: (id: string) => request<Agent>(`/api/v1/agents/${id}`),
  registerAgent: (body: unknown) =>
    request<Agent>("/api/v1/agents", { method: "POST", body: JSON.stringify(body) }),

  listPolicies: () => request<Policy[]>("/api/v1/policies"),
  getPolicy: (id: string) => request<Policy>(`/api/v1/policies/${id}`),
  createPolicy: (body: { name: string; description?: string; source: string }) =>
    request<Policy>("/api/v1/policies", { method: "POST", body: JSON.stringify(body) }),
  addPolicyVersion: (policyId: string, source: string) =>
    request(`/api/v1/policies/${policyId}/versions`, { method: "POST", body: JSON.stringify({ source }) }),
  lookupAttachment: (scopeType: "agent" | "session", scopeId: string) =>
    request<PolicyAttachment | null>(
      `/api/v1/policies/attachments/lookup?${new URLSearchParams({ scope_type: scopeType, scope_id: scopeId })}`
    ),
  attachPolicy: (policyId: string, version: number, scopeType: "agent" | "session", scopeId: string) =>
    request(`/api/v1/policies/${policyId}/versions/${version}/attach`, {
      method: "POST",
      body: JSON.stringify({ scope_type: scopeType, scope_id: scopeId }),
    }),

  listSessions: (params: Record<string, string> = {}) =>
    request<Session[]>(`/api/v1/sessions?${new URLSearchParams(params)}`),
  getSession: (id: string) => request<Session>(`/api/v1/sessions/${id}`),
  cancelSession: (id: string) => request(`/api/v1/sessions/${id}/cancel`, { method: "POST" }),

  listTasks: (params: Record<string, string> = {}) =>
    request<Task[]>(`/api/v1/tasks?${new URLSearchParams(params)}`),
  getTask: (id: string) => request<Task>(`/api/v1/tasks/${id}`),
  listTaskActions: (taskId: string) => request<Action[]>(`/api/v1/tasks/${taskId}/actions`),
  getAction: (actionId: string) => request<Action>(`/api/v1/actions/${actionId}`),
  submitTask: (body: { agent_id: string; agent_version_id?: string; input: Record<string, unknown>; workspace_files?: Record<string, string> }) =>
    request<{ session_id: string; task_id: string }>("/api/v1/tasks", { method: "POST", body: JSON.stringify(body) }),

  listApprovals: (status = "pending") => request<Approval[]>(`/api/v1/approvals?status=${status}`),
  resolveApproval: (id: string, approve: boolean, reason?: string) =>
    request<Approval>(`/api/v1/approvals/${id}/resolve`, {
      method: "POST",
      body: JSON.stringify({ approve, reason }),
    }),

  queryAudit: (params: Record<string, string> = {}) =>
    request<AuditEvent[]>(`/api/v1/audit/events?${new URLSearchParams(params)}`),
};

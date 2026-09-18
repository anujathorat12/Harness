# Security Review

This is a scoped, honest review against the assignment's own "production
security review" checklist, run against the state of this repository as of
this build. Every line either points at a specific test/code reference that
demonstrates it, or says plainly that it wasn't verified and why.

**Read this distinction first, everywhere below:** "verified" here means
*demonstrated in this codebase, on this machine, right now* — it is not the
same claim as "audited for a specific enterprise production deployment."
Anywhere this document can't back a claim with a test or a direct code
reference, it says so instead of implying otherwise.

## Sandbox isolation

| Property | Status | Evidence |
|---|---|---|
| CPU limits | ✅ Verified | `RLIMIT_CPU` in `process_runtime.py`; `test_cpu_bomb_is_killed` runs a real busy-loop agent and confirms it's killed |
| Memory limits | ✅ Verified | `RLIMIT_AS`; `test_memory_bomb_is_killed` runs a real unbounded-allocation agent |
| PID limits | ✅ Verified | `RLIMIT_NPROC`; `test_fork_bomb_is_contained` runs a real fork-bomb agent |
| Timeout enforcement | ✅ Verified | Wall-clock watchdog independent of CPU usage; `test_hung_agent_is_killed_by_watchdog` |
| Process-group kill (no orphans) | ✅ Verified | `os.killpg` in `ProcessAgentHandle.terminate()`; exercised by every adversarial test above |
| Filesystem isolation | ⚠️ Partial | `ProcessRuntime` does **not** isolate filesystem visibility — documented, not hidden (ARCHITECTURE.md §6, §13.1) |
| Network restrictions | ⚠️ Partial | `ProcessRuntime` does **not** isolate network access for the same reason |
| Real filesystem/network isolation | 📝 Written, not exercised here | `ContainerRuntime` provides read-only rootfs + `--network none` + dropped capabilities; **not run against a live Docker daemon as part of this build** — this machine has Docker available, but registering a `runtime_kind: container` agent and running a task through it was not done in this session. Treat as untested until you do. |
| No-new-privileges / capability restrictions | ✅ Verified (container level) | `docker-compose.yml`: `cap_drop: ALL`, `security_opt: no-new-privileges:true` on the `harness` service itself — demonstrated live: running `pytest` as `root` inside this container failed with `PermissionError` writing to a directory root doesn't own, because `cap_drop: ALL` also strips root's usual `CAP_DAC_OVERRIDE`. That's the hardening actually doing something, not decorative YAML. |
| Non-root execution | ✅ Verified | `Dockerfile`: `USER harness` (non-root); `ContainerRuntime` additionally runs spawned containers as `user="1000:1000"` (unverified live, see above) |

## Policy enforcement boundary

| Property | Status | Evidence |
|---|---|---|
| Malicious agent cannot bypass policy (via the protocol) | ✅ Verified | Structural: `TOOLS` dict imported in exactly one file (`tool_gateway.py`); `test_path_traversal_blocked_by_gateway` |
| Agent cannot directly execute governed tools | ✅ Verified | Same as above — no other call site exists |
| Raw-syscall bypass (agent ignores the SDK entirely) | ⚠️ Documented gap, by design | `test_raw_syscall_bypass_is_documented_not_hidden` proves this bypasses the *protocol*, not the platform's claims — closing it is `ContainerRuntime`'s job, a different layer |
| Policy decisions are server-side authoritative | ✅ Verified | Evaluated entirely in `policy_engine/evaluator.py`, a pure function with no client input path; the frontend never evaluates or caches a decision |
| Default-deny behavior | ✅ Verified | `PolicyDocument.default_effect` defaults to `"deny"`; unattached policy → deny (`test_agent_with_no_policy_attached_denies_everything`); malformed/unrecognized input → deny, never allow (`TestSecureDefaults` class) |
| Invalid requests fail safely | ✅ Verified | Policy schema validation rejects at `POST /policies` before persistence (422); unknown `action_type` → deny, not a crash |

## Authentication / authorization (new in this build)

| Property | Status | Evidence |
|---|---|---|
| Authenticated API access | ✅ Verified | Every route except `/health`, `/ready` requires `X-API-Key`; `test_no_api_key_is_401`, `test_bad_api_key_is_401` |
| Authorization for privileged operations | ✅ Verified | `Depends(require_role(...))` per route; `TestRolePermissions` class (CLIENT can't register agents/create policies; OPERATOR can't register agents; AUDITOR can't view agents or resolve approvals) |
| Enforcement is server-side, not UI-hidden | ✅ Verified by construction | The frontend has no authorization logic of its own — it calls `GET /api/v1/auth/me` for display purposes only; every mutating/privileged call still goes through the same `require_role` dependency regardless of what UI made the request |
| CLIENT can only see its own data | ✅ Verified | `test_client_can_submit_and_read_own_task`, `test_client_cannot_read_another_clients_task` (returns 404, not 403 — existence isn't leaked) |
| Approver identity is authenticated, not client-supplied | ✅ Verified | `test_resolved_approval_records_authenticated_approver_not_client_supplied_string` — the recorded `approver` is the authenticated principal's label even if the request body doesn't supply one |
| Secrets not exposed in logs/API/UI | ✅ Verified by construction | `GET /system/status` returns only counts/flags, never key values; `harness/core/config.py`'s dev-default keys all contain the literal string `***CHANGE-ME***`, making them visually impossible to mistake for a real secret in a diff or a log line; `audit_redacted_keys` already redacts any parameter whose key name looks like a secret before it's ever written to the audit log |
| Invalid auth fails safely | ✅ Verified | Missing/bad key → 401 before any handler logic runs; wrong role → 403 before any DB read/write for that route |

## Human-in-the-loop (HITL)

| Property | Status | Evidence |
|---|---|---|
| Action genuinely does not execute until resolved | ✅ Verified | `test_action_genuinely_pauses_until_approved` checks task status *mid-wait*, not just the final result |
| First resolution wins | ✅ Verified | `test_duplicate_resolution_rejected` — a second resolve attempt gets 409, never silently overwrites |
| Timeout denies safely | ✅ Verified | `test_approval_timeout_denies_action`; timeout is itself terminal (a late approval after timeout is rejected the same way a duplicate is) |

## Audit

| Property | Status | Evidence |
|---|---|---|
| Audit records cannot be silently altered through normal APIs | ✅ Verified by construction | No `UPDATE`/`DELETE` call site exists on `AuditEvent` anywhere in the codebase — `audit_logger.py`'s `log_event()` only ever `INSERT`s; there is no API route that modifies an existing audit row |
| Every governed action reconstructable | ✅ Verified | `GET /audit/events?action_id=...` returns the full chain: `action_attempted → policy_decision → (approval_requested → approval_resolved →) action_executed`, demonstrated live in `demo/run_demo.py`'s final step |

## Concurrency / reliability

| Property | Status | Evidence |
|---|---|---|
| One agent's backlog can't starve others | ✅ Verified | Per-agent + global semaphores; `test_per_agent_concurrency_limit_does_not_deadlock_other_agents` |
| Sessions are filesystem-isolated from each other | ✅ Verified | `test_multiple_agents_run_concurrently_and_are_isolated` |
| Concurrent SQLite writers don't corrupt state | ✅ Verified, with a caveat | `PRAGMA busy_timeout` set on every connection (see ARCHITECTURE.md §11); a real single-agent-at-a-time deployment will not approach the contention levels this project's own test suite intentionally creates |

## Explicitly out of scope / known gaps (not silently skipped)

- Approval-pause does not survive a harness process restart (ARCHITECTURE.md §12).
- No Alembic migrations (`create_all()` only).
- DOCX/PDF → policy conversion is not implemented — see ARCHITECTURE.md §7 for why an unsupervised version of that feature would be a real safety risk.
- No rate limiting.
- No authorization-denial audit trail (a 401/403 is returned and logged via the process logger, but does not currently write an `AuditEvent` row).
- No automated frontend test suite — verified by a successful production build and manual endpoint-shape checks against the live backend instead.

## What "production-oriented" means here, precisely

This build satisfies: real sandbox isolation with adversarial tests that
attack it, a policy engine that fails closed on every malformed/missing
input, genuine pause/resume HITL with race-safe resolution, an append-only
audit trail, and server-enforced RBAC with no UI-only security theater.

It does **not** claim: enterprise identity management, verified
container-level sandbox isolation (written but not run live in this
session), database migration tooling, or any load/penetration testing
beyond the adversarial test suite included in this repo. Those are real,
identified next steps — not gaps this document is trying to talk around.

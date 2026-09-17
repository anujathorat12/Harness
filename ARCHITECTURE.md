# ARCHITECTURE.md

## 1. What this is

A microservice that hosts third-party ("bring your own") agents, runs each
one in isolation, and sits a **policy engine** between every single action an
agent attempts and the real world. The core claim the whole design is built
around:

> **An untrusted agent must not be able to bypass the harness's policy
> enforcement boundary to perform a governed action.**

Everything below explains how that boundary is drawn and what does (and does
not) hold it.

## 2. End-to-end flow

```
Client
  |  POST /api/v1/tasks {agent_id, input}
  v
REST API (harness/api/v1/tasks.py)
  |  creates Session + Task rows, schedules background execution
  v
Orchestrator (harness/orchestrator/task_manager.py)
  |  selects a Runtime by agent_version.runtime_kind
  v
Runtime.start()  --  process | container | declarative
  |  yields RuntimeEvents; on "action_request":
  v
Tool Gateway  (harness/gateway/tool_gateway.py)  <-- THE enforcement point
  |  1. write Action row (status=pending)          -- exists even if we crash next
  |  2. resolve effective policy (session > agent)  -- missing policy => deny
  |  3. policy_engine.evaluate()                     -- pure function, no I/O
  |  4. audit the decision
  v
  ALLOW ---------------> execute tool -> audit result -> feed back to runtime
  DENY  ---------------> feed denial back to runtime (nothing executes)
  REQUIRE_APPROVAL -----> create Approval row -> WAIT (session shows "paused")
                          |
                          v  human calls POST /approvals/{id}/resolve
                          approved -> execute tool -> audit
                          denied/timed_out -> deny -> audit
```

Every branch ends by returning to the runtime, which resumes the agent (or
terminates it). The orchestrator's event loop
(`harness/orchestrator/task_manager.py::_run`) is deliberately ~10 lines for
the "handle one action" case: it cannot skip a step because there is only one
path through it.

## 3. Trust boundary

**Trusted** (harness-owned code, never influenced by agent input except as
inert data):
- REST API, orchestrator, policy engine, tool gateway, approval manager,
  audit logger, persistence layer.

**Untrusted** (third-party, treated as hostile):
- Agent code (code shape), agent container image (code shape),
  agent declarative spec (declarative shape), every field of every
  `action_request` an agent sends (action_type/resource/parameters are never
  trusted to mean what the agent claims — the gateway independently
  re-validates paths, see §5).

The boundary is drawn at the **Runtime interface**
(`harness/runtime/base.py`): a `Runtime`/`AgentHandle` can only *emit*
`RuntimeEvent`s and *receive* a decision via `resolve_action()`. It has no
reference to the gateway, the tool implementations, or the database. This
isn't just a convention — it's structural: `harness/gateway/tools.py`'s
`TOOLS` dict is imported in exactly one place
(`harness/gateway/tool_gateway.py`), and nothing in `runtime/` or
`orchestrator/` imports it.

## 4. Why an agent can't skip the policy engine

Three independent reasons, from strongest to weakest:

1. **No other code path executes a tool.** Every tool function in
   `harness/gateway/tools.py` is only reachable from
   `tool_gateway._execute_and_record()`, which is only called after a
   decision has already been recorded (`effect == "allow"` or an approval
   resolved to `"approved"`). Grep the repo for `TOOLS[` / `TOOLS.get(` —
   there is exactly one call site.
2. **The agent has no direct handle on the tools.** In the code shape, the
   agent process's only I/O channel back to the harness is the stdio
   protocol; the example SDK (`agent_sdk.py`) exposes nothing else. In the
   declarative shape, there's no code execution *at all* — the interpreter
   only walks a pre-validated step list.
3. **Unknown/malformed input fails closed, not open.** An unrecognised
   `action_type`, a policy that fails schema validation, a missing policy
   attachment, a malformed condition, an exception during evaluation — all of
   these resolve to `deny`, never to `allow`. See `policy_engine/evaluator.py`
   and the `TestSecureDefaults` test class.

**What this does *not* claim**: reason (2) holds only for an agent that stays
within the SDK/protocol contract. An agent that imports `os`/`socket`
directly and never sends an `action_request` bypasses the *protocol*
entirely — there is no message for the policy engine to intercept, because
none was sent. This is real, tested, and documented rather than hidden: see
`tests/adversarial/test_sandbox_escape.py::test_raw_syscall_bypass_is_documented_not_hidden`.
What contains *that* gap is not the policy engine — it's OS/container-level
isolation (§6), a different and independent layer of the defense.

## 5. Defense in depth: policy vs. gateway

The policy engine matches resources by **glob**, e.g. `/workspace/**`. Globs
don't understand `..` — `/workspace/../../../etc/passwd` glob-matches
`/workspace/**` as a plain string. The gateway's file tools
(`harness/gateway/tools.py::_resolve_in_workspace`) independently resolve
every path with `os.path.realpath` and reject anything that escapes the
session's real workspace directory, **regardless of what the policy
decided**. This is tested directly
(`test_path_traversal_blocked_by_gateway`) and is a deliberate second layer:
policy authoring mistakes and glob semantics gaps don't become filesystem
escapes.

## 6. Sandbox / runtime layer

Three `Runtime` implementations share one interface
(`harness/runtime/base.py`):

| Runtime | Isolates | Tested here | Use case |
|---|---|---|---|
| `ProcessRuntime` | CPU time (RLIMIT_CPU), address space (RLIMIT_AS), PID count (RLIMIT_NPROC), wall-clock (watchdog), output size, process-group kill | **Yes** — 6 adversarial tests | Dependency-free; what this repo's own tests exercise |
| `ContainerRuntime` | All of the above **plus** real filesystem namespace (read-only rootfs), real network namespace (`--network none` by default), dropped Linux capabilities, non-root, `--pids-limit` at the kernel/cgroup level | **No** — no Docker daemon in the build environment; written against the documented `docker` SDK API, same protocol as `ProcessRuntime` | Real deployments; register an agent version with `"runtime_kind": "container"` |
| `DeclarativeRuntime` | N/A — no code execution to isolate | Yes | Declarative-shape agents |

**Honest statement of what `ProcessRuntime` does NOT isolate**: filesystem
visibility (the process can see anything the harness's own OS user can see)
and network access (no network namespace without root). An agent that raw
syscalls its way past the SDK, in `ProcessRuntime`, is limited only by host
file permissions — see the test referenced in §4. This is exactly why
`ContainerRuntime` exists, and exactly why we don't claim `ProcessRuntime`
alone is a production-grade isolation boundary for adversarial code agents.
Think like an attacker, as the assignment says: with `ProcessRuntime`, the
honest answer to "how could this agent escape?" is "it doesn't have to
escape — it just needs to not use the SDK." That's not a bug we missed, it's
the documented boundary of what this runtime provides; closing it is
`ContainerRuntime`'s job.

Adversarial tests actually run against the real sandbox (no mocking):
CPU bomb, memory bomb, fork bomb, path traversal, hung/unresponsive agent,
raw-syscall bypass — `tests/adversarial/test_sandbox_escape.py`.

## 7. Policy model

See [docs/POLICY_FORMAT.md](docs/POLICY_FORMAT.md) for the authoring guide.
Design decisions worth calling out:

- **First-match-wins rule evaluation**, not "most specific wins." Simpler to
  reason about and test; documented so policy authors put specific
  overrides above general rules (see `policies/production-agent-policy.yaml`,
  where `deny-sensitive-files` would need to come before any broader allow
  if one existed for `/etc`).
- **`default_effect` defaults to `deny`** at the schema level — a policy
  author would have to explicitly opt into `default_effect: allow` (and
  should not, for a production policy).
- **Versions are immutable.** Editing a policy creates a new
  `PolicyVersion` row; nothing ever updates `document` in place. This is
  what makes "which policy version governed this historical action" a fact
  that can never rot.
- **Attachment scoping**: an attachment binds a policy *version* (not just a
  policy) to either an `agent` (default for all its sessions) or a `session`
  (overrides the agent default for that session only). The resolver
  (`policy_engine/resolver.py`) checks session-scope first.
- **DOCX/PDF policy import — deliberately NOT implemented.** The assignment
  explicitly asks to consider this and to weigh it against available time.
  Turning a natural-language document into an executable security policy
  without a human in the loop is a correctness and safety risk (the model
  extracting the policy could silently misinterpret an exception, a scope, a
  negation). If this were built, the *only* safe shape is: document → LLM
  extraction → structured YAML/JSON → schema validation → **mandatory human
  review of the diff** → immutable version → policy engine, with the
  machine-readable form remaining the sole authoritative enforcement
  representation (never the source document, never interpreted at
  enforcement time). That's a real feature with a real review workflow
  attached to it, which is more than the remaining time in this build
  allows to do safely — so it's out of scope, not silently skipped.

## 8. Human-in-the-loop approval

`harness/approval/approval_manager.py`. An approval is durable (an `Approval`
row, status survives restart and is queryable) plus a best-effort in-process
`asyncio.Event` used only to wake a waiter immediately instead of waiting for
the next poll tick (`POLL_INTERVAL_SECONDS = 0.5`). Correctness never depends
on the event firing — only latency does; the polling fallback in
`wait_for_resolution()` still notices resolution or timeout on its own.

Guarantees, all tested (`tests/integration/test_approval_flow.py`):
- The action genuinely does not execute until resolved (verified by
  checking task status mid-wait, not just checking the final result).
- **First resolution wins.** A duplicate or conflicting resolution attempt
  is rejected with `409`, never silently overwrites the recorded outcome.
- Timeout denies the action and is itself terminal — a late approval after
  timeout is rejected the same way a duplicate is.

## 9. Audit trail

`harness/audit/audit_logger.py` — append-only (no `UPDATE`/`DELETE` call
site exists on `AuditEvent` anywhere in the codebase), and it's the single
function (`log_event()`) every call site uses, so parameter redaction
(`redact()`, matching keys like `password`, `token`, `secret`, `api_key`,
etc. — configurable via `HARNESS_AUDIT_REDACTED_KEYS`) happens in one place,
not re-implemented per call site. Query surface: `GET /api/v1/audit/events`,
filterable by agent/session/task/action/event_type. For any action, the full
story is `GET /audit/events?action_id=...`: `action_attempted` →
`policy_decision` (with policy id/version/rule) → (`action_executed` |
`action_execution_error` | `approval_requested` → `approval_resolved` →
...).

## 10. Data model

See `harness/domain/models.py` for the authoritative definitions and
docstrings. Summary of the entity graph:

```
Agent 1--* AgentVersion
Policy 1--* PolicyVersion 1--* PolicyAttachment (scope: agent | session)
Session 1--* Task 1--* Action 1--0..1 Approval
AuditEvent  (append-only, references any of the above by id, all nullable)
```

`Session.budget_state` (JSON) tracks per-session budget consumption so
`evaluate()` sees accurate running totals across calls within one session.

## 11. Concurrency & reliability

`harness/orchestrator/concurrency.py`: one `asyncio.Semaphore` per agent
(`HARNESS_MAX_CONCURRENT_SESSIONS_PER_AGENT`, default 3) plus one global
semaphore (`HARNESS_MAX_CONCURRENT_SESSIONS_TOTAL`, default 50). This means
one agent's backlog cannot starve every other agent's sessions — tested in
`test_per_agent_concurrency_limit_does_not_deadlock_other_agents`.

A hung agent is killed by the wall-clock watchdog regardless of CPU usage
(`test_hung_agent_is_killed_by_watchdog`), and killing a sandbox kills its
whole process group, not just the parent PID, so it can't leave orphaned
children behind. Each session gets its own real filesystem directory under
`HARNESS_RUNTIME_WORKDIR_ROOT`, verified isolated in
`test_multiple_agents_run_concurrently_and_are_isolated`.

## 12. State & restart

Everything that must survive a restart is a database row, written *before*
the state transition it represents (e.g. the `Action` row is created with
`status=pending` before policy evaluation even runs). What does **not**
survive a harness process restart: the in-memory Python coroutine actually
driving a live, paused agent sandbox. If the harness restarts while a
session is paused on approval, the `Approval`/`Action` rows correctly show
what happened (they were never lost), but nothing automatically reconnects
to the now-gone sandbox process to resume it — an operator would see the
session end up in a state that needs manual follow-up. A production system
addressing this fully would need either sandboxes that outlive the
orchestrator process (detached containers, reattached on restart) or an
explicit re-run-from-last-completed-action design. Out of scope here, and
intentionally not glossed over.

## 13. Known Limitations

(Also listed in README.md; kept in both places since a reviewer may only
read one.)

1. `ProcessRuntime` does not isolate filesystem visibility or network access
   — see §6. `ContainerRuntime` addresses this but was not exercised against
   a live Docker daemon in the environment this repo was built in (no
   `docker` binary there). Verify it with `docker compose up` plus a
   `runtime_kind: container` agent on a machine that has Docker before
   relying on it.
2. An agent that bypasses the SDK/protocol with raw syscalls is not
   intercepted by the policy engine — demonstrated, not hidden, by
   `test_raw_syscall_bypass_is_documented_not_hidden`.
3. Approval-pause does not survive a harness process restart (§12).
4. No Alembic migrations — `create_all()` only. Fine for this scope; would
   need addressing before a first production schema change.
5. Tool set is intentionally small (`file.read`, `file.write`, `email.send`
   [simulated], `spend.charge` [simulated]) — enough to demonstrate every
   policy decision path without needing real external credentials in a
   graded/demo environment. Adding a real tool (e.g. a real SMTP send, a
   real HTTP fetch with a domain allowlist) is one function in
   `harness/gateway/tools.py` plus a `TOOLS` dict entry; nothing else in the
   platform changes.
6. DOCX/PDF policy import is out of scope — see §7 for the reasoning and
   what a safe version of it would require.
7. A harmless `PytestUnhandledThreadExceptionWarning` (event loop closed)
   can appear once at the very end of a full `pytest tests/` run, from an
   aiosqlite background thread finishing after the session's last test event
   loop tears down. It does not affect test outcomes (all tests pass either
   way) and does not occur when running individual test files.

## 14. Why these technology choices

- **FastAPI + Pydantic**: request/response validation and OpenAPI generation
  for free; async-native, matching the asyncio-based orchestrator.
- **SQLAlchemy (async) + SQLite by default**: zero external dependencies to
  run this locally or grade it — `docker compose up` just works. The models
  are backend-agnostic; pointing `HARNESS_DATABASE_URL` at
  `postgresql+asyncpg://...` is the entire migration to a real production
  database (SQLite's `NullPool` special-case in `core/database.py` is a
  no-op for other backends).
- **No Kubernetes.** The assignment explicitly says not to add it "merely
  for appearance," and Docker Compose is a complete, strong implementation
  for this scope. The `Runtime` interface is the seam a Kubernetes runtime
  would implement later without touching anything else.

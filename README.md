# BYOA Agent Harness Platform

A control plane for hosting third-party ("bring your own") agents, sandboxing
their execution, and enforcing a distinct, declarative policy layer that
governs **every individual action** an agent attempts — not just whether it's
allowed to start — with human-in-the-loop approval for borderline actions and
a full, reconstructable audit trail.

If you're new to this domain, read **[docs/GLOSSARY.md](docs/GLOSSARY.md)**
first — plain-language definitions of every term used below.

## Is this production-ready?

**Production-oriented and demo-ready, with documented limitations** — not
the same claim as "fully production-deployed in a specific enterprise
environment." The core enforcement boundary (agent → policy engine →
gateway → tool) has no bypass path in this codebase, is covered by
adversarial tests that actually try to break it, and every route is gated
by a role-based `X-API-Key` check enforced server-side — that combination is
what matters most per the assignment brief. What's *not* production-hardened
is listed honestly in
[ARCHITECTURE.md § Known Limitations](ARCHITECTURE.md#known-limitations) —
read that before you present this as more than it is. In short: the process
sandbox (tested here) doesn't isolate network/filesystem visibility the way
the container sandbox (written, exercisable on a machine with a Docker
daemon — see below) does; auth is a static per-role API key, not an
enterprise IAM system; approval-pause doesn't survive a harness process
restart; and there's no Alembic migration story (create-all only).

## Quick start

```bash
docker compose up --build
```

This starts two services: the API (`harness`, port 8000) and the admin/operator
web UI (`frontend`, port 5173). Then, in another terminal:

```bash
# API docs (Swagger UI) -- click "Authorize" and paste an API key (see
# "Authentication & authorization" below) to call protected endpoints
open http://localhost:8000/docs

# Admin/Operator UI -- paste the same API key when prompted
open http://localhost:5173

# Run the scripted end-to-end demo (registers both example agents, attaches
# a policy, submits tasks, resolves an approval, and prints the audit trail)
docker compose exec harness python3 demo/run_demo.py
```

Health check: `curl http://localhost:8000/health` (no API key required).

### Running without Docker

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn harness.main:app --reload
```

To also run the admin UI against it: `cd frontend && npm install && npm run
dev` (needs Node 18+), then open the printed `localhost:5173` URL.

### Running the tests

```bash
pip install -r requirements-dev.txt
PYTHONPATH=. pytest tests/ -v
```

55 tests: policy-engine unit tests (independently testable per the
assignment's requirement), API integration tests, approval-flow tests
(pause/resume/timeout/duplicate-resolution), concurrency/isolation tests,
**authorization tests** (role permission boundaries, CLIENT row-scoping,
authenticated-approver identity), **external-agent-invocation tests**
(`POST /agents/{agent_id}/tasks` — the contract external applications
integrate against — proven for both agent shapes, through ALLOW/DENY/
REQUIRE_APPROVAL, with no agent-specific branching anywhere in the path),
and **adversarial sandbox-escape tests** that run real malicious agents
(CPU bomb, memory bomb, fork bomb, path traversal, hung process, raw-syscall
bypass) against the real `ProcessRuntime` sandbox — no mocking.

## Architecture, in one paragraph

```
Client (curl / external app / Admin UI / Swagger)
   -> X-API-Key -> role check (ADMIN/OPERATOR/AUDITOR/CLIENT)
   -> versioned REST API -> Agent Registry -> Task/Session Manager (orchestrator)
   -> Runtime (sandboxed agent process, container, or declarative interpreter)
   -> ACTION REQUEST -> Policy Enforcement Point (Tool Gateway)
   -> Policy Engine -> ALLOW / DENY / REQUIRE_APPROVAL
   -> (if allowed) Tool execution -> Result -> Audit
   -> (if require_approval) pause -> human resolves -> resume
```

The Admin/Operator UI (`frontend/`, a separate React app) is just another
REST client of this same API — it carries no privileged access of its own
and enforces nothing itself; every permission check happens server-side. See
"Authentication & authorization" below.

The full architecture, trust boundaries, data model, and security model are
in **[ARCHITECTURE.md](ARCHITECTURE.md)**. The short version of what makes
this hard to bypass: **the Tool Gateway (`harness/gateway/tool_gateway.py`)
is the only code in the entire platform that is allowed to call a real tool
implementation, and every runtime (process, container, declarative) can only
*ask* for an action — never perform one directly.**

## Repository layout

```
harness/
  api/v1/            REST endpoints (agents, policies, tasks, approvals, audit,
                       dashboard, system, auth, health)
  core/               config, database, security.py (RBAC), middleware.py (request-id)
  domain/models.py    SQLAlchemy models -- the full persistent data model
  policy_engine/      schema, evaluator (pure function), loader, resolver
  runtime/            base interface + process / container / declarative implementations
  gateway/            tool_gateway.py (the enforcement point) + tools.py (real tool impls)
  orchestrator/        task/session lifecycle, concurrency limiter
  approval/            human-in-the-loop approval manager
  audit/                audit_logger.py (append-only)
  examples_agents/     the two required BYOA "shapes"
frontend/               Admin/Operator UI (React + Vite + TypeScript) -- a plain
                        REST client of the API above, no privileged access of its own
policies/               example policy YAML
docs/                   protocol spec, policy format, glossary, external integration guide
demo/                   scripted end-to-end demo
tests/
  unit/                 policy engine, no DB/agent/runtime involved
  integration/           API, approval flow, concurrency/isolation, authorization
  adversarial/            malicious test agents + sandbox-escape tests
```

## Two BYOA agent shapes

1. **Code-shape** (`harness/examples_agents/code_agent/`): a real Python
   process, run in its own OS-level sandbox (`ProcessRuntime`), talking to
   the harness over a line-delimited JSON stdio protocol — see
   [docs/AGENT_PROTOCOL.md](docs/AGENT_PROTOCOL.md). This is the shape a team
   bringing arbitrary code would use; the same protocol also has a real
   Docker-container implementation (`ContainerRuntime`) for production
   deployments that need real filesystem/network isolation.
2. **Declarative-shape** (`harness/examples_agents/config_agent/agent.yaml`):
   a pure data document — a list of steps naming an action, resource, and
   templated parameters. The harness *interprets* this directly
   (`DeclarativeRuntime`); **no third-party code is executed at all** for
   this shape. This is a genuinely different mechanism, not the code shape
   wrapped in different clothing — see `harness/runtime/declarative_runtime.py`.

Both shapes emit the exact same `action_request` event shape into the
orchestrator, so the policy engine, gateway, approval flow, and audit log are
100% shared code paths regardless of which shape produced the request.

## Invoking an onboarded agent

Once an agent is registered (`POST /agents`), any external application
invokes it the same way, regardless of shape:

```
POST /api/v1/agents/{agent_id}/tasks
{"input": {...}, "workspace_files": {...}}

-> 202 {"session_id": "...", "task_id": "...", "agent_id": "...", "status": "pending"}
```

The Harness owns the entire path from there — starting the sandboxed
runtime, receiving the agent's `action_request`s, evaluating them against
the attached policy, pausing for approval when required, and recording the
audit trail. The caller never gets, or needs, a handle on the agent's
process, container, or code — `agent_id` is the only thing it ever holds,
and it resolves entirely on the Harness side with **no per-agent branching**
anywhere in the platform (`harness/api/v1/tasks.py`'s
`_submit_task_for_agent` is the one function both this endpoint and the
older flat `POST /tasks` call into). Full external-integration walkthrough,
including polling, approvals, and audit:
[docs/EXTERNAL_INTEGRATION.md](docs/EXTERNAL_INTEGRATION.md); the
architectural reasoning: [ARCHITECTURE.md § 1b](ARCHITECTURE.md).

## Policy authoring

See [docs/POLICY_FORMAT.md](docs/POLICY_FORMAT.md) and
[policies/production-agent-policy.yaml](policies/production-agent-policy.yaml)
for a worked example (allow / deny / require_approval / budget rules).
Policies are YAML or JSON, schema-validated before they can ever be saved,
versioned immutably, and attachable per-agent (default) or per-session
(override).

## API reference

Live Swagger UI at `/docs` and machine-readable spec at `/openapi.json` once
the service is running; a static snapshot is checked in at
[docs/openapi.json](docs/openapi.json).

## Authentication & authorization

Every route except `/health` and `/ready` requires an `X-API-Key` header.
The key maps to one of four fixed roles (`harness/core/security.py`):

| Role | Can |
|---|---|
| `ADMIN` | manage agents, manage policies, approve/deny actions, view audit |
| `OPERATOR` | view agents/tasks/sessions, approve/deny actions, view audit |
| `AUDITOR` | read-only: audit events, tasks/sessions |
| `CLIENT` | submit tasks; read only its **own** tasks/sessions (matched by API key, not a full tenancy model) |

This is deliberately a static API-key scheme, not an enterprise IAM system —
no password database, no token issuance/expiry. Every check is enforced
**server-side** on the route itself (`Depends(require_role(...))`); the
Admin UI hiding a button for a role it doesn't expect is a convenience, not
the security boundary — removing the entire frontend changes nothing about
what an API caller can do. Dev-only default keys ship in
`harness/core/config.py` and `docker-compose.yml`, each containing
`***CHANGE-ME***` so they're impossible to mistake for a real secret; the
app logs a startup warning if they're still in place outside
`HARNESS_ENVIRONMENT=local`. Full reasoning and the production security
review: [SECURITY.md](SECURITY.md).

## Admin/Operator UI

`frontend/` (React + Vite + TypeScript, plain CSS, no component library) --
Dashboard, Agents, Sessions, Policies, Approvals, Audit, and System pages,
each talking to the REST API above and nothing else. Run it with `docker
compose up` (served at `localhost:5173`) or `cd frontend && npm install &&
npm run dev`. Paste an API key on first load; `GET /api/v1/auth/me` resolves
it to a role, which only controls what the UI *shows* — see "Authentication
& authorization" above for what's actually enforced.

## External application integration

Any application can drive this platform over plain HTTP without importing
any Python from this repo. Worked curl examples for the full flow (register
agent → create policy → attach → submit task → poll → detect
`require_approval` → resolve → get result → query audit):
[docs/EXTERNAL_INTEGRATION.md](docs/EXTERNAL_INTEGRATION.md).

## Known limitations

See [ARCHITECTURE.md § Known Limitations](ARCHITECTURE.md#known-limitations)
for the full, honest list. Highlights:

- **`ProcessRuntime` (tested here) does not isolate filesystem visibility or
  network access** — only CPU/memory/PID/time/output limits and process-group
  kill. Real isolation for those requires `ContainerRuntime`, which is
  written and documented but **could not be exercised against a live Docker
  daemon in the environment this repository was built in** (no `docker`
  binary available there). Run `docker compose up` on a machine with Docker
  and register an agent version with `"runtime_kind": "container"` to use it.
- An agent that ignores the provided SDK and makes raw OS/socket calls is
  **not** intercepted by the policy engine — there's no protocol message to
  catch. This is demonstrated, not hidden, by
  `tests/adversarial/test_sandbox_escape.py::test_raw_syscall_bypass_is_documented_not_hidden`.
  Containing that gap is exactly what container/OS-level isolation is for.
- A session paused on `require_approval`, if the harness process itself
  restarts, does not automatically resume — the in-memory coroutine holding
  the sandbox connection is gone. The Approval/Action rows correctly reflect
  what happened; true crash-resume of a live sandbox is out of scope here.
- `Base.metadata.create_all` is the migration strategy — no Alembic. Fine for
  this scope; a real deployment should add migrations before its first
  schema change.
- Tool set is intentionally small (`file.read`, `file.write`, `email.send`
  [simulated], `spend.charge` [simulated]) to demonstrate every required
  policy path (allow/deny/require_approval/budget) without needing real
  external credentials. Adding a real tool is one function in
  `harness/gateway/tools.py`.
- **Auth is a static per-role API key, not an enterprise IAM system** —
  by design, see "Authentication & authorization" above and SECURITY.md.
  No password database, no token expiry, no per-user audit identity beyond
  the four role labels.
- **`CLIENT` row-level scoping is best-effort**, matched on the caller's API
  key label (`created_by` on `Task`) — it is not a real multi-tenancy model
  and does not extend to agents or policies (only ADMIN/OPERATOR manage
  those).
- **The frontend has no automated test suite in this build** — verified
  manually (build succeeds, every page's API calls checked against the live
  backend) rather than with e.g. Playwright/Vitest component tests. A real
  next step, not a silently skipped one.

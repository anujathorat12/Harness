# BYOA Agent Harness Platform

A control plane for hosting third-party ("bring your own") agents, sandboxing
their execution, and enforcing a distinct, declarative policy layer that
governs **every individual action** an agent attempts — not just whether it's
allowed to start — with human-in-the-loop approval for borderline actions and
a full, reconstructable audit trail.

If you're new to this domain, read **[docs/GLOSSARY.md](docs/GLOSSARY.md)**
first — plain-language definitions of every term used below.

## Is this production-ready?

**Ready, with documented limitations.** The core enforcement boundary (agent
→ policy engine → gateway → tool) has no bypass path in this codebase, is
covered by adversarial tests that actually try to break it, and that's the
one property that matters most per the assignment brief. What's *not*
production-hardened is listed honestly in
[ARCHITECTURE.md § Known Limitations](ARCHITECTURE.md#known-limitations) —
read that before you present this as more than it is. In short: the process
sandbox (tested here) doesn't isolate network/filesystem visibility the way
the container sandbox (written, not exercised here — no Docker daemon in the
build environment) does; approval-pause doesn't survive a harness process
restart; and there's no Alembic migration story (create-all only).

## Quick start

```bash
docker compose up --build
```

Then, in another terminal:

```bash
# API docs (Swagger UI)
open http://localhost:8000/docs

# Run the scripted end-to-end demo (registers both example agents, attaches
# a policy, submits tasks, resolves an approval, and prints the audit trail)
python3 demo/run_demo.py
```

Health check: `curl http://localhost:8000/health`

### Running without Docker

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
uvicorn harness.main:app --reload
```

### Running the tests

```bash
pip install -r requirements-dev.txt
PYTHONPATH=. pytest tests/ -v
```

34+ tests: policy-engine unit tests (independently testable per the
assignment's requirement), API integration tests, approval-flow tests
(pause/resume/timeout/duplicate-resolution), concurrency/isolation tests,
and **adversarial sandbox-escape tests** that run real malicious agents
(CPU bomb, memory bomb, fork bomb, path traversal, hung process, raw-syscall
bypass) against the real `ProcessRuntime` sandbox — no mocking.

## Architecture, in one paragraph

```
Client -> versioned REST API -> Agent Registry -> Task/Session Manager (orchestrator)
   -> Runtime (sandboxed agent process, container, or declarative interpreter)
   -> ACTION REQUEST -> Policy Enforcement Point (Tool Gateway)
   -> Policy Engine -> ALLOW / DENY / REQUIRE_APPROVAL
   -> (if allowed) Tool execution -> Result -> Audit
   -> (if require_approval) pause -> human resolves -> resume
```

The full architecture, trust boundaries, data model, and security model are
in **[ARCHITECTURE.md](ARCHITECTURE.md)**. The short version of what makes
this hard to bypass: **the Tool Gateway (`harness/gateway/tool_gateway.py`)
is the only code in the entire platform that is allowed to call a real tool
implementation, and every runtime (process, container, declarative) can only
*ask* for an action — never perform one directly.**

## Repository layout

```
harness/
  api/v1/            REST endpoints (agents, policies, tasks, approvals, audit, health)
  core/               config, database
  domain/models.py    SQLAlchemy models -- the full persistent data model
  policy_engine/      schema, evaluator (pure function), loader, resolver
  runtime/            base interface + process / container / declarative implementations
  gateway/            tool_gateway.py (the enforcement point) + tools.py (real tool impls)
  orchestrator/        task/session lifecycle, concurrency limiter
  approval/            human-in-the-loop approval manager
  audit/                audit_logger.py (append-only)
  examples_agents/     the two required BYOA "shapes"
policies/               example policy YAML
docs/                   protocol spec, policy format, glossary
demo/                   scripted end-to-end demo
tests/
  unit/                 policy engine, no DB/agent/runtime involved
  integration/           API, approval flow, concurrency/isolation
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

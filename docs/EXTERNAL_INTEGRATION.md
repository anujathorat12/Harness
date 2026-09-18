# External Application Integration

Any application can drive this platform over plain HTTP — nothing here
requires importing a Python module from this repo, and nothing here talks
to an onboarded agent directly. **The Harness is the only thing that can
start, drive, or communicate with an agent's sandbox** — an external
application only ever calls the Harness's own REST API. This is the whole
contract:

```
External Application
    |  X-API-Key: <role key>
    v
Harness REST API  (/api/v1/...)
    |
    v
Agent Registry -> Task/Session -> Agent Runtime -> Policy Enforcement -> Tool
    |
    v
Result
    |
    v
Harness API  (poll for status, resolve approvals, query audit)
    |
    v
External Application
```

The application never sees an agent's implementation (Python class,
container image, declarative step list), process ID, container ID, or tool
function — `agent_id` is the only handle it ever holds, and it's opaque:
the same `POST /api/v1/agents/{agent_id}/tasks` call works identically for
a code-shape agent and a declarative-shape agent, with no branching on
`agent_id` anywhere in the platform. See
[ARCHITECTURE.md § 1b](../ARCHITECTURE.md) for the full path and
`tests/integration/test_external_agent_api.py` for the tests that prove it.

Every example below is a real `curl` command that works against
`docker compose up`. Replace `dev-admin-key-***CHANGE-ME***` with your own
key if you've changed the defaults (see README § Authentication &
authorization). A minimal Python client using the same calls is at the
bottom.

## 1. Register an agent

```bash
curl -s -X POST http://localhost:8000/api/v1/agents \
  -H "X-API-Key: dev-admin-key-***CHANGE-ME***" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "my-external-agent",
    "owner": "external-team",
    "shape": "declarative",
    "initial_version": {
      "runtime_kind": "declarative",
      "spec": {
        "name": "my-external-agent",
        "steps": [
          {"action": "file.read", "resource": "/workspace/{{input.source_file}}", "parameters": {}}
        ]
      },
      "resource_limits": {"max_steps": 20}
    }
  }'
```

Response includes `id` (the agent ID) — save it.

## 2. Create a policy

```bash
curl -s -X POST http://localhost:8000/api/v1/policies \
  -H "X-API-Key: dev-admin-key-***CHANGE-ME***" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "my-external-policy",
    "source": "policy:\n  name: my-external-policy\n  version: 1\n  default_effect: deny\nrules:\n  - id: allow-workspace-read\n    action: file.read\n    resource: \"/workspace/**\"\n    effect: allow\n"
  }'
```

Response includes `id` and `versions[0].version` (normally `1`).

## 3. Attach the policy to the agent

```bash
curl -s -X POST "http://localhost:8000/api/v1/policies/<POLICY_ID>/versions/1/attach" \
  -H "X-API-Key: dev-admin-key-***CHANGE-ME***" \
  -H "Content-Type: application/json" \
  -d '{"scope_type": "agent", "scope_id": "<AGENT_ID>"}'
```

## 4. Invoke the agent (submit a task)

This is the contract an external application actually integrates
against — the agent is identified purely by the `{agent_id}` in the URL,
exactly like the assignment's own example:

```bash
curl -s -X POST "http://localhost:8000/api/v1/agents/<AGENT_ID>/tasks" \
  -H "X-API-Key: dev-client-key-***CHANGE-ME***" \
  -H "Content-Type: application/json" \
  -d '{
    "input": {"source_file": "notes.txt"},
    "workspace_files": {"notes.txt": "hello from an external app"}
  }'
```

Response (`202 Accepted`, immediately — the task runs in the background):

```json
{
  "session_id": "5898bf7e06364799bb4626a3575af8a2",
  "task_id": "8ab73b1170bd40dfb31cf18620bdc0f0",
  "agent_id": "<AGENT_ID>",
  "status": "pending"
}
```

(`POST /api/v1/tasks` with `agent_id` in the JSON body instead of the URL
still works too — same underlying function, kept for backward
compatibility — but `POST /agents/{agent_id}/tasks` is the documented
public contract for new integrations.)

## 5. Check task status

```bash
curl -s "http://localhost:8000/api/v1/tasks/<TASK_ID>" \
  -H "X-API-Key: dev-client-key-***CHANGE-ME***"
```

`status` is one of `pending`, `running`, `succeeded`, `failed`. Poll this
until it's terminal (`succeeded`/`failed`) — there is no webhook/push in
this build, polling every few hundred milliseconds is the pattern
`demo/run_demo.py` itself uses.

## 6. Detect an action waiting for approval

A task can be `running` while one of its actions is paused. Check for a
pending approval tied to that task's actions:

```bash
curl -s "http://localhost:8000/api/v1/approvals?status=pending" \
  -H "X-API-Key: dev-operator-key-***CHANGE-ME***"
```

Each entry has `action_id` — cross-reference with
`GET /api/v1/tasks/<TASK_ID>/actions` to confirm it belongs to your task.

## 7. Resolve the approval

```bash
curl -s -X POST "http://localhost:8000/api/v1/approvals/<APPROVAL_ID>/resolve" \
  -H "X-API-Key: dev-operator-key-***CHANGE-ME***" \
  -H "Content-Type: application/json" \
  -d '{"approve": true}'
```

Requires `ADMIN` or `OPERATOR`. The `approver` recorded is your
authenticated principal's label, not anything the request body claims —
see SECURITY.md.

## 8. Retrieve the result

```bash
curl -s "http://localhost:8000/api/v1/tasks/<TASK_ID>" \
  -H "X-API-Key: dev-client-key-***CHANGE-ME***"
```

Once `status` is `succeeded`, the `result` field has the agent's output;
`error` is set if it `failed`.

## 9. Query the audit trail

```bash
curl -s "http://localhost:8000/api/v1/audit/events?session_id=<SESSION_ID>" \
  -H "X-API-Key: dev-auditor-key-***CHANGE-ME***"
```

Filterable by `agent_id`, `session_id`, `task_id`, `action_id`,
`event_type`. This is how any external system reconstructs "what actually
happened" without trusting the calling application's own logs.

## Minimal Python client

```python
import time
import httpx

BASE_URL = "http://localhost:8000"
ADMIN_KEY = "dev-admin-key-***CHANGE-ME***"
CLIENT_KEY = "dev-client-key-***CHANGE-ME***"

client = httpx.Client(base_url=BASE_URL, headers={"X-API-Key": ADMIN_KEY}, timeout=30)

def wait_for_task(task_id: str, timeout: float = 30) -> dict:
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        task = client.get(f"/api/v1/tasks/{task_id}").json()
        if task["status"] in ("succeeded", "failed"):
            return task
        time.sleep(0.3)
    raise TimeoutError(f"task {task_id} did not finish in {timeout}s")

# ... register agent, create + attach policy (see steps 1-3 above) ...

submission = client.post(
    f"/api/v1/agents/{AGENT_ID}/tasks",
    json={"input": {"source_file": "notes.txt"}, "workspace_files": {"notes.txt": "hi"}},
    headers={"X-API-Key": CLIENT_KEY},
).json()
print(submission)  # {"session_id": "...", "task_id": "...", "agent_id": "...", "status": "pending"}

result = wait_for_task(submission["task_id"])
print(result)
```

See `demo/run_demo.py` for a complete, runnable version of this whole flow,
including the approval-resolution step, and
`tests/integration/test_external_agent_api.py` for the automated tests
covering this exact external-client path (multiple agent shapes, ALLOW,
DENY, REQUIRE_APPROVAL/resolve, and authorization boundaries).

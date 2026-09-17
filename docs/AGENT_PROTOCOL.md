# Agent Protocol (code-shape agents)

Code-shape agents (both `ProcessRuntime` and `ContainerRuntime`) talk to the
harness over a line-delimited JSON protocol on stdin/stdout. Every message is
exactly one JSON object per line (`\n`-terminated). This document is the
contract a third-party team would implement against to bring their own
code-shape agent.

## Message types

### Harness → Agent

**`task_input`** (sent once, immediately after the process starts)
```json
{"type": "task_input", "input": {"any": "JSON object from the task submission"}}
```

**`action_response`** (sent once per `action_request` the agent sent)
```json
{"type": "action_response", "allowed": true, "result": {"...": "..."}, "error": null}
```
- `allowed: false` means the policy denied the action (or an approval was
  denied/timed out) — `error` explains why, `result` is always `null`.
- `allowed: true` with `error` set means the policy *allowed* it but the
  tool itself failed at execution time (e.g. a file that doesn't exist) —
  distinguish this from a policy denial in your error handling.

### Agent → Harness

**`action_request`** (send this and then **block reading the next line from
stdin** — do not proceed until you get the matching `action_response`)
```json
{"type": "action_request", "action_type": "file.read", "resource": "/workspace/report.txt", "parameters": {}}
```
- `action_type`: a string the attached policy's rules match against (e.g.
  `file.read`, `file.write`, `email.send`, `spend.charge`). Also must match
  a real tool implementation registered in `harness/gateway/tools.py` or
  execution will fail even if policy allows it.
- `resource`: a logical path, conventionally rooted at `/workspace` for
  filesystem actions (e.g. `/workspace/notes.txt`) to match the
  `/workspace/**` glob convention used in example policies. `null` if not
  applicable (e.g. `spend.charge`).
- `parameters`: whatever the specific action needs (e.g. `{"to": "...",
  "subject": "..."}` for `email.send`).

**`log`** (optional, informational only — never trusted for control flow)
```json
{"type": "log", "message": "human-readable progress note"}
```

**`finished`** (send once, then exit with code 0)
```json
{"type": "finished", "result": {"any": "JSON-serializable result"}}
```

**`error`** (send once, then exit — for a graceful failure with a message,
as opposed to crashing)
```json
{"type": "error", "message": "what went wrong"}
```

If the process exits without sending `finished` or `error`, the harness
treats a non-zero exit code as a failure and a zero exit code with no
message as an (unusual but accepted) empty success.

## Minimal example

See `harness/examples_agents/code_agent/agent_sdk.py` for a small reference
implementation of this protocol, and `agent.py` in the same directory for a
complete example agent built on it. The SDK's `call_action()` function *is*
the protocol: it writes an `action_request` line and blocks on `readline()`
until the matching `action_response` arrives. An agent that only uses this
SDK physically cannot perform a governed action without that round trip.

## Security note

This protocol is not itself a security boundary against an agent that
chooses not to use it — see ARCHITECTURE.md §4 and §6 for what does and does
not hold if an agent ignores the SDK and makes raw OS calls instead.

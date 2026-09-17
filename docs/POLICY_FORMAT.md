# Policy Format

Policies are YAML or JSON. Schema is enforced by
`harness/policy_engine/schema.py` (Pydantic) — anything that doesn't conform
is rejected with a 422 at `POST /api/v1/policies`, before it's ever saved.

## Shape

```yaml
policy:
  name: production-agent-policy
  version: 1
  default_effect: deny   # optional, defaults to "deny" if omitted

rules:
  - id: allow-workspace-read       # unique within the policy
    action: file.read              # or "*" to match any action type
    resource: "/workspace/**"      # optional; glob, omit to match any resource
    effect: allow                  # allow | deny | require_approval
    description: "..."             # optional, free text
    conditions:                    # optional, ALL must match
      - field: parameters.amount_usd
        op: lte                    # eq|ne|lt|lte|gt|gte|in|not_in|prefix
        value: 10
    budget:                        # optional
      key: spend_usd
      limit: 5
      amount_field: parameters.amount_usd
```

You can also write `name`/`version`/`default_effect` flattened at the top
level instead of nested under `policy:` — both are accepted
(`harness/policy_engine/loader.py` normalizes either shape).

## Evaluation semantics

- **Rules are evaluated in order; the first matching rule wins.** Not "most
  specific wins." Put specific overrides *before* broader rules — see
  `deny-sensitive-files` coming before any hypothetical broader
  `file.read` allow in `policies/production-agent-policy.yaml`.
- **No rule matches → `default_effect` applies** (which itself defaults to
  `deny` if you don't set it).
- **A rule matches when**: `action` matches (`*` matches anything),
  **and** `resource` glob matches (or the rule has no `resource`, matching
  anything), **and** every `condition` is true (a rule with no conditions
  always passes this check).
- **`resource` glob syntax**: standard shell-style globs, with `**` treated
  as "match anything including `/`" (so `/workspace/**` matches
  `/workspace/a/b/c.txt`). Note this is a *string* match — it does not
  understand `..`; that's why the gateway independently re-checks path
  containment (see ARCHITECTURE.md §5). Don't rely on glob matching alone
  for security-critical path scoping.
- **`conditions`**: `field` is a dotted path into the action context
  (`agent_id`, `session_id`, `action_type`, `resource`, or anything under
  `parameters.*`). A condition that can't be evaluated (type mismatch, missing
  field) is treated as **not matching** — it never raises, and a rule with a
  failing condition simply falls through to the next rule / default effect.
- **`budget`**: if the matched rule carries a budget and this action would
  push cumulative consumption (tracked per-session, in `Session.budget_state`)
  over `limit`, the decision becomes `deny` even though the rule's `effect`
  was `allow`, and the audit reason is tagged `budget_exceeded`.

## Versioning & attachment

- `POST /api/v1/policies` creates a policy **and** its first version.
- `POST /api/v1/policies/{id}/versions` adds a new version — the `version`
  field in the source must be higher than any existing version for that
  policy (an attempt to reuse a version number is rejected with 409).
  **Versions are immutable** once created.
- `POST /api/v1/policies/{id}/versions/{n}/attach` with
  `{"scope_type": "agent"|"session", "scope_id": "..."}` attaches that exact
  version to an agent (the default for all its sessions) or a specific
  session (overrides the agent default for that session only).
- An agent (or session) with **no policy attached at all** is fail-closed:
  every action it attempts is denied (see
  `tests/integration/test_api.py::TestUnattachedPolicyFailsClosed`) — an
  unconfigured agent is not an unrestricted one.

## DOCX/PDF policies

Not implemented — see ARCHITECTURE.md §7 for why, and what a safe version
of this feature would require if built later. The machine-readable YAML/JSON
form is, and will remain, the only representation the policy engine actually
enforces.

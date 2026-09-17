# Glossary

Plain-language definitions, in the order you'll hit them.

- **Agent** — a piece of automation (code, or a config file) that wants to
  *do things* (read files, send email, spend money) on someone's behalf.
  "Bring your own agent" (BYOA) means a third party can register their own
  agent to run on this platform, instead of the platform only running agents
  its own team wrote.
- **Agent shape** — *how* an agent is defined. This platform supports two:
  a **code-shape** agent (a real program, run as its own process) and a
  **declarative-shape** agent (a data file — a list of steps — with no code
  at all).
- **Harness** — the whole platform in this repo: the service that registers
  agents, runs them safely, and enforces policy on what they're allowed to
  do. "Harness" and "platform" are used interchangeably.
- **Sandbox / Runtime** — the isolated place an agent actually executes, so
  that a buggy or malicious agent can't affect the host machine or other
  agents. "Runtime" is the code that manages one kind of sandbox (e.g. a
  plain OS process, or a Docker container).
- **Session** — one isolated run of one agent: one sandbox instance, one
  policy binding, one lifetime. Think of it as "this particular execution."
- **Task** — one unit of work submitted to an agent (e.g. "summarize this
  report and email it"). A session can run more than one task over its
  life, but never two at once.
- **Action** — one specific thing an agent asks to do *during* a task, e.g.
  "read file X" or "send this email." This is the thing policy governs —
  not the task as a whole, and not just "can this agent run at all."
- **Policy** — a set of rules, written in a data format (YAML/JSON), that
  says which actions are allowed, denied, or need a human to sign off.
  "Policy-as-code": rules live in a file the system can check automatically,
  not in scattered if-statements in the code.
- **Policy Engine** — the one component whose whole job is: given an action
  and a policy, decide `allow`, `deny`, or `require_approval`. It has no
  other job — it doesn't run agents, doesn't touch the database, doesn't
  execute anything. That's what makes it easy to test on its own.
  (`harness/policy_engine/evaluator.py`)
- **Rule** — one line item inside a policy: "action X on resource Y is
  effect Z" (optionally only when some condition holds).
- **Effect** — the outcome a rule produces: `allow`, `deny`, or
  `require_approval`.
- **Default deny / fail closed** — if nothing in the policy says an action
  is allowed, it's denied. The safe direction to fail in: when in doubt, say
  no, rather than when in doubt, say yes.
- **Tool Gateway** — the *only* door through which an agent's action can
  actually happen in the real world (actually read the file, actually send
  the email). The gateway always checks with the Policy Engine first. No
  other code path can reach the real tools.
- **Human-in-the-loop / Approval** — when a policy says `require_approval`,
  the agent's task pauses and waits for a person to explicitly approve or
  deny that one action before anything proceeds.
- **Audit trail** — a permanent, append-only record of everything that was
  attempted, what was decided, by which rule, and what happened next — so
  someone can reconstruct "who did what, when, and was it allowed" after
  the fact.
- **Concurrency** — more than one agent/session running at the same time.
  The harness limits how many run at once (per agent and in total) so one
  agent can't hog all the resources or affect another agent's session.
- **Budget** — an optional limit on how much of something (e.g. simulated
  dollars) a session can consume before the policy engine starts denying
  further requests, even if the action itself would otherwise be allowed.

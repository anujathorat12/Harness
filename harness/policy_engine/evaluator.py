"""
The Policy Engine.

evaluate() is a pure function: (PolicyDocument, ActionContext, budget_state) -> Decision.
It has no knowledge of HTTP, the database, agents, or runtimes, which is what
makes it independently unit-testable per the assignment's requirement --
"you should be able to unit-test 'does this policy allow this action' without
spinning up a real agent execution."

Rule matching is first-match-wins, evaluated in the order rules appear in the
document (like a firewall ruleset). This is documented, deterministic, and
easy for a policy author to reason about: put more specific / higher-priority
rules first.

Secure defaults enforced here, not left to callers:
  - no rule matches            -> policy.default_effect (itself defaults to "deny")
  - unknown/empty action type  -> deny
  - malformed context           -> deny (never raises out of evaluate())
  - unrecognised effect string  -> impossible: Effect is a closed Literal type,
                                    enforced at parse time (schema.py)
"""
from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from typing import Any

from harness.policy_engine.schema import Condition, PolicyDocument, Rule


@dataclass
class ActionContext:
    agent_id: str
    session_id: str
    action_type: str
    resource: str | None = None
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass
class Decision:
    effect: str  # allow | deny | require_approval
    policy_name: str
    policy_version: int
    rule_id: str | None  # None when default_effect applied (no rule matched)
    reason: str
    budget_exceeded: bool = False


def _get_path(obj: dict[str, Any], dotted: str) -> Any:
    cur: Any = obj
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def _context_as_dict(ctx: ActionContext) -> dict[str, Any]:
    return {
        "agent_id": ctx.agent_id,
        "session_id": ctx.session_id,
        "action_type": ctx.action_type,
        "resource": ctx.resource,
        "parameters": ctx.parameters,
    }


def _condition_matches(cond: Condition, ctx_dict: dict[str, Any]) -> bool:
    actual = _get_path(ctx_dict, cond.field)
    expected = cond.value
    try:
        if cond.op == "eq":
            return actual == expected
        if cond.op == "ne":
            return actual != expected
        if cond.op == "lt":
            return actual is not None and actual < expected
        if cond.op == "lte":
            return actual is not None and actual <= expected
        if cond.op == "gt":
            return actual is not None and actual > expected
        if cond.op == "gte":
            return actual is not None and actual >= expected
        if cond.op == "in":
            return actual in (expected or [])
        if cond.op == "not_in":
            return actual not in (expected or [])
        if cond.op == "prefix":
            return isinstance(actual, str) and isinstance(expected, str) and actual.startswith(expected)
    except TypeError:
        # Mismatched, incomparable types -> condition does not match.
        # Never let a malformed condition raise; that would be a policy
        # evaluation failure, which must fail closed (deny), not crash.
        return False
    return False


def _action_matches(rule_type: str, actual_type: str) -> bool:
    if rule_type == "*":
        return True
    return rule_type == actual_type


def _resource_matches(pattern: str | None, resource: str | None) -> bool:
    if pattern is None:
        return True
    if resource is None:
        return False
    # translate ** -> match across path separators, * -> match within a segment
    regex = fnmatch.translate(pattern.replace("**", "\0DOUBLESTAR\0"))
    regex = regex.replace("\0DOUBLESTAR\0", ".*")
    import re

    return re.match(regex, resource) is not None


def _rule_matches(rule: Rule, ctx: ActionContext, ctx_dict: dict[str, Any]) -> bool:
    if not _action_matches(rule.action, ctx.action_type):
        return False
    if not _resource_matches(rule.resource, ctx.resource):
        return False
    return all(_condition_matches(c, ctx_dict) for c in rule.conditions)


def evaluate(
    policy: PolicyDocument,
    ctx: ActionContext,
    budget_state: dict[str, float] | None = None,
) -> Decision:
    budget_state = budget_state or {}

    if not ctx.action_type or not isinstance(ctx.action_type, str):
        return Decision(
            effect="deny",
            policy_name=policy.name,
            policy_version=policy.version,
            rule_id=None,
            reason="unknown or missing action_type",
        )

    ctx_dict = _context_as_dict(ctx)

    for rule in policy.rules:
        if _rule_matches(rule, ctx, ctx_dict):
            if rule.budget is not None:
                consumed = budget_state.get(rule.budget.key, 0.0)
                this_amount = _get_path(ctx_dict, rule.budget.amount_field) or 0
                try:
                    this_amount = float(this_amount)
                except (TypeError, ValueError):
                    this_amount = 0.0
                if consumed + this_amount > rule.budget.limit:
                    return Decision(
                        effect="deny",
                        policy_name=policy.name,
                        policy_version=policy.version,
                        rule_id=rule.id,
                        reason=(
                            f"budget '{rule.budget.key}' exceeded: "
                            f"{consumed}+{this_amount} > {rule.budget.limit}"
                        ),
                        budget_exceeded=True,
                    )
            return Decision(
                effect=rule.effect,
                policy_name=policy.name,
                policy_version=policy.version,
                rule_id=rule.id,
                reason=f"matched rule '{rule.id}'",
            )

    # No rule matched -> secure default.
    return Decision(
        effect=policy.default_effect,
        policy_name=policy.name,
        policy_version=policy.version,
        rule_id=None,
        reason="no rule matched; applied policy default_effect",
    )

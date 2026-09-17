import pytest

from harness.policy_engine.evaluator import ActionContext, evaluate
from harness.policy_engine.loader import PolicyValidationError, parse_policy_source
from harness.policy_engine.schema import PolicyDocument, Rule


def make_policy(**overrides) -> PolicyDocument:
    base = dict(
        name="test-policy",
        version=1,
        default_effect="deny",
        rules=[
            Rule(id="allow-workspace-read", action="file.read", resource="/workspace/**", effect="allow"),
            Rule(id="deny-etc", action="file.read", resource="/etc/**", effect="deny"),
            Rule(id="approve-email", action="email.send", effect="require_approval"),
        ],
    )
    base.update(overrides)
    return PolicyDocument(**base)


def ctx(**kw) -> ActionContext:
    defaults = dict(agent_id="a1", session_id="s1", action_type="file.read", resource="/workspace/x.txt")
    defaults.update(kw)
    return ActionContext(**defaults)


class TestBasicDecisions:
    def test_allow_rule_matches(self):
        d = evaluate(make_policy(), ctx(resource="/workspace/report.txt"))
        assert d.effect == "allow"
        assert d.rule_id == "allow-workspace-read"

    def test_deny_rule_matches(self):
        d = evaluate(make_policy(), ctx(resource="/etc/shadow"))
        assert d.effect == "deny"
        assert d.rule_id == "deny-etc"

    def test_require_approval_rule_matches(self):
        d = evaluate(make_policy(), ctx(action_type="email.send", resource=None))
        assert d.effect == "require_approval"
        assert d.rule_id == "approve-email"


class TestSecureDefaults:
    def test_default_deny_when_no_rule_matches(self):
        d = evaluate(make_policy(), ctx(action_type="network.connect", resource="evil.example.com"))
        assert d.effect == "deny"
        assert d.rule_id is None
        assert "default" in d.reason

    def test_unknown_action_type_denied(self):
        d = evaluate(make_policy(), ctx(action_type="", resource=None))
        assert d.effect == "deny"

    def test_explicit_default_effect_still_defaults_to_deny(self):
        # Even if someone forgets default_effect, the schema-level default is deny.
        p = PolicyDocument(name="p", version=1, rules=[])
        assert p.default_effect == "deny"

    def test_policy_cannot_set_default_effect_to_something_invalid(self):
        with pytest.raises(Exception):
            PolicyDocument(name="p", version=1, default_effect="maybe", rules=[])  # type: ignore[arg-type]


class TestPrecedence:
    def test_first_matching_rule_wins(self):
        p = make_policy(
            rules=[
                Rule(id="specific-deny", action="file.read", resource="/workspace/secret.txt", effect="deny"),
                Rule(id="general-allow", action="file.read", resource="/workspace/**", effect="allow"),
            ]
        )
        d = evaluate(p, ctx(resource="/workspace/secret.txt"))
        assert d.effect == "deny"
        assert d.rule_id == "specific-deny"

        d2 = evaluate(p, ctx(resource="/workspace/other.txt"))
        assert d2.effect == "allow"
        assert d2.rule_id == "general-allow"


class TestConditions:
    def test_condition_must_match_for_rule_to_apply(self):
        p = make_policy(
            rules=[
                Rule(
                    id="allow-small-spend",
                    action="spend.charge",
                    effect="allow",
                    conditions=[{"field": "parameters.amount_usd", "op": "lte", "value": 10}],
                ),
            ]
        )
        allowed = evaluate(p, ctx(action_type="spend.charge", resource=None, parameters={"amount_usd": 5}))
        denied = evaluate(p, ctx(action_type="spend.charge", resource=None, parameters={"amount_usd": 500}))
        assert allowed.effect == "allow"
        assert denied.effect == "deny"  # falls through to default_effect

    def test_malformed_condition_field_fails_closed_not_crash(self):
        p = make_policy(
            rules=[
                Rule(
                    id="weird",
                    action="spend.charge",
                    effect="allow",
                    conditions=[{"field": "parameters.amount_usd", "op": "lte", "value": 10}],
                ),
            ]
        )
        # parameters.amount_usd is a string, not comparable to int -> must not raise
        d = evaluate(p, ctx(action_type="spend.charge", resource=None, parameters={"amount_usd": "not-a-number"}))
        assert d.effect == "deny"


class TestBudgets:
    def test_budget_enforced_across_calls(self):
        p = make_policy(
            rules=[
                Rule(
                    id="budgeted-spend",
                    action="spend.charge",
                    effect="allow",
                    budget={"key": "spend_usd", "limit": 100, "amount_field": "parameters.amount_usd"},
                )
            ]
        )
        d1 = evaluate(p, ctx(action_type="spend.charge", resource=None, parameters={"amount_usd": 60}), budget_state={})
        assert d1.effect == "allow"

        d2 = evaluate(
            p,
            ctx(action_type="spend.charge", resource=None, parameters={"amount_usd": 60}),
            budget_state={"spend_usd": 60},
        )
        assert d2.effect == "deny"
        assert d2.budget_exceeded is True


class TestPolicyLoading:
    def test_valid_yaml_parses(self):
        src = """
policy:
  name: production-agent-policy
  version: 1
rules:
  - id: allow-workspace-read
    action: file.read
    resource: /workspace/**
    effect: allow
"""
        doc = parse_policy_source(src)
        assert doc.name == "production-agent-policy"
        assert len(doc.rules) == 1

    def test_invalid_yaml_rejected(self):
        with pytest.raises(PolicyValidationError):
            parse_policy_source("not: valid: yaml: [")

    def test_unknown_effect_rejected(self):
        src = """
policy: {name: p, version: 1}
rules:
  - {id: r1, action: x, effect: maybe_allow}
"""
        with pytest.raises(PolicyValidationError):
            parse_policy_source(src)

    def test_duplicate_rule_ids_rejected(self):
        src = """
policy: {name: p, version: 1}
rules:
  - {id: dup, action: a, effect: allow}
  - {id: dup, action: b, effect: deny}
"""
        with pytest.raises(PolicyValidationError):
            parse_policy_source(src)

    def test_non_mapping_document_rejected(self):
        with pytest.raises(PolicyValidationError):
            parse_policy_source("- just\n- a\n- list\n")

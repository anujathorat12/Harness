"""
Canonical policy schema.

A policy document, once parsed from YAML/JSON, must conform to this Pydantic
model or it is REJECTED before it can ever become an (immutable) PolicyVersion.
This is "invalid policy -> reject" from the secure-defaults requirement: a
malformed policy never silently becomes a permissive one.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

Effect = Literal["allow", "deny", "require_approval"]


class Condition(BaseModel):
    """A single equality/comparison condition matched against the action
    context. Kept deliberately simple (no arbitrary code execution in
    policy) so that evaluation stays deterministic and side-effect-free.

    field: dotted path into the action context, e.g. "parameters.amount_usd"
    op:    eq | ne | lt | lte | gt | gte | in | not_in | prefix
    value: the comparison value
    """

    field: str
    op: Literal["eq", "ne", "lt", "lte", "gt", "gte", "in", "not_in", "prefix"] = "eq"
    value: object = None


class Rule(BaseModel):
    id: str
    action: str = Field(description="Action type this rule matches, e.g. 'file.read'. '*' matches any.")
    resource: str | None = Field(
        default=None,
        description="Glob-style resource pattern, e.g. '/workspace/**'. Omit to match any resource.",
    )
    effect: Effect
    conditions: list[Condition] = Field(default_factory=list)
    budget: "Budget | None" = None
    description: str = ""


class Budget(BaseModel):
    """Optional per-rule spend/usage budget. Consumption is tracked at the
    session scope (see policy_engine/budgets table in AuditEvent details)."""

    key: str = Field(description="Budget bucket name, e.g. 'llm_tokens' or 'spend_usd'.")
    limit: float
    amount_field: str = Field(
        default="parameters.amount",
        description="Dotted path into the action context giving how much this single action consumes.",
    )


class PolicyDocument(BaseModel):
    name: str
    version: int = Field(ge=1)
    default_effect: Effect = "deny"  # secure default: fail closed if no rule matches
    rules: list[Rule] = Field(default_factory=list)

    @field_validator("rules")
    @classmethod
    def _unique_rule_ids(cls, rules: list[Rule]) -> list[Rule]:
        ids = [r.id for r in rules]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise ValueError(f"duplicate rule ids in policy: {sorted(dupes)}")
        return rules

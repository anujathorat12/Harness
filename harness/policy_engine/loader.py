from __future__ import annotations

import json

import yaml
from pydantic import ValidationError

from harness.policy_engine.schema import PolicyDocument


class PolicyValidationError(Exception):
    def __init__(self, message: str, errors: list | None = None):
        super().__init__(message)
        self.errors = errors or []


def parse_policy_source(raw_source: str) -> PolicyDocument:
    """Parse a YAML or JSON policy document and validate it against the
    canonical schema. Raises PolicyValidationError on any problem -- there is
    no code path that turns an invalid document into a usable PolicyDocument.
    """
    try:
        data = yaml.safe_load(raw_source)
    except yaml.YAMLError as e:
        raise PolicyValidationError(f"policy is not valid YAML/JSON: {e}") from e

    if not isinstance(data, dict):
        raise PolicyValidationError("policy document must be a mapping/object at the top level")

    # Support the natural authoring shape:
    #   policy: {name, version}
    #   rules: [...]
    if "policy" in data and isinstance(data["policy"], dict):
        merged = {**data["policy"], "rules": data.get("rules", [])}
    else:
        merged = data

    try:
        return PolicyDocument.model_validate(merged)
    except ValidationError as e:
        raise PolicyValidationError(f"policy schema validation failed: {e}", errors=e.errors()) from e


def dump_canonical_json(doc: PolicyDocument) -> dict:
    return json.loads(doc.model_dump_json())

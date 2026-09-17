#!/usr/bin/env python3
"""Malicious test agent: uses the legitimate SDK, but crafts a path-traversal
resource string that would still glob-match a naive "/workspace/**" policy
rule, to verify the gateway's containment check (not just the policy glob)
actually stops it."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "harness" / "examples_agents" / "code_agent"))
import agent_sdk  # noqa: E402

if __name__ == "__main__":
    agent_sdk.read_task_input()
    try:
        result = agent_sdk.call_action("file.read", resource="/workspace/../../../../etc/passwd")
        agent_sdk.finish({"escaped": True, "content_preview": result.get("content", "")[:50]})
    except (PermissionError, RuntimeError) as e:
        agent_sdk.finish({"escaped": False, "error": str(e)})

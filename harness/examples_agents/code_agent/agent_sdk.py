"""
Minimal SDK for a code-shape agent talking to the harness over stdio.

This is the ONLY I/O primitive this SDK exposes for governed actions:
call_action() writes an action_request line to stdout and blocks reading the
harness's response from stdin. An agent that only uses this SDK physically
cannot perform a governed action (file/network/spend/etc.) without going
through the harness's policy decision first, because the SDK gives it no
other way to reach those resources.

(A malicious agent could ignore this SDK entirely and call os/socket
directly -- see ARCHITECTURE.md "Security model & limitations" for the
honest treatment of that gap and why process/container isolation, not this
protocol, is what has to contain it.)
"""
from __future__ import annotations

import json
import sys
from typing import Any


def read_task_input() -> dict[str, Any]:
    line = sys.stdin.readline()
    msg = json.loads(line)
    assert msg.get("type") == "task_input"
    return msg.get("input", {})


def call_action(action_type: str, resource: str | None = None, parameters: dict[str, Any] | None = None) -> dict[str, Any]:
    request = {"type": "action_request", "action_type": action_type, "resource": resource, "parameters": parameters or {}}
    sys.stdout.write(json.dumps(request) + "\n")
    sys.stdout.flush()
    line = sys.stdin.readline()
    if not line:
        raise RuntimeError("harness closed the connection while waiting for an action decision")
    response = json.loads(line)
    if response.get("type") != "action_response":
        raise RuntimeError(f"unexpected message from harness: {response}")
    if not response.get("allowed"):
        raise PermissionError(response.get("error") or "action denied by policy")
    if response.get("error"):
        raise RuntimeError(response["error"])
    return response.get("result") or {}


def log(message: str) -> None:
    sys.stdout.write(json.dumps({"type": "log", "message": message}) + "\n")
    sys.stdout.flush()


def finish(result: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps({"type": "finished", "result": result}) + "\n")
    sys.stdout.flush()


def fail(message: str) -> None:
    sys.stdout.write(json.dumps({"type": "error", "message": message}) + "\n")
    sys.stdout.flush()

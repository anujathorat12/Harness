"""
Concrete tool implementations.

Each tool is a small async function with the signature
    async def tool(resource, parameters, workspace_root) -> dict
registered in TOOLS below. Adding a new governed action type means adding one
entry here -- nothing else in the platform needs to change, and nothing here
is ever reachable except through ToolGateway.execute(), which is the only
caller (see gateway/tool_gateway.py).

file.read / file.write are deliberately defensive even though the policy
engine may already have allowed the path: a resource path is resolved
against the session's workspace root and re-checked to stay inside it. This
is defense-in-depth against a policy authoring mistake (e.g. a resource glob
that's broader than intended) and against path-traversal payloads like
"../../etc/passwd" that a naive glob match might not have caught -- the
os.path.realpath containment check below is what actually stops those.
"""
from __future__ import annotations

import os
from typing import Any, Awaitable, Callable

Tool = Callable[[str | None, dict[str, Any], str], Awaitable[dict[str, Any]]]


class ToolExecutionError(Exception):
    pass


def _resolve_in_workspace(resource: str | None, workspace_root: str) -> str:
    """Resources are authored (in policy and by agents) as logical paths
    rooted at "/workspace", e.g. "/workspace/report.txt", matching the
    "/workspace/**" glob convention used in example policies. This maps that
    logical path onto the session's real, isolated workspace directory --
    and then re-checks containment with os.path.realpath, so a resource like
    "/workspace/../../etc/passwd" (which would still glob-match "/workspace/**"
    under a naive matcher) is caught here regardless of what the policy
    engine decided."""
    if resource is None:
        raise ToolExecutionError("resource is required for filesystem tools")
    rel = resource
    if rel.startswith("/workspace/"):
        rel = rel[len("/workspace/"):]
    elif rel == "/workspace":
        rel = ""
    else:
        rel = rel.lstrip("/")
    root = os.path.realpath(workspace_root)
    candidate = os.path.realpath(os.path.join(root, rel))
    if candidate != root and not candidate.startswith(root + os.sep):
        raise ToolExecutionError(f"resource '{resource}' escapes the session workspace (path traversal blocked)")
    return candidate


async def file_read(resource: str | None, parameters: dict[str, Any], workspace_root: str) -> dict[str, Any]:
    path = _resolve_in_workspace(resource, workspace_root)
    if not os.path.isfile(path):
        raise ToolExecutionError(f"no such file: {resource}")
    max_bytes = int(parameters.get("max_bytes", 200_000))
    with open(path, "rb") as f:
        data = f.read(max_bytes + 1)
    truncated = len(data) > max_bytes
    data = data[:max_bytes]
    return {"content": data.decode("utf-8", errors="replace"), "truncated": truncated}


async def file_write(resource: str | None, parameters: dict[str, Any], workspace_root: str) -> dict[str, Any]:
    path = _resolve_in_workspace(resource, workspace_root)
    content = parameters.get("content", "")
    if not isinstance(content, str):
        raise ToolExecutionError("parameters.content must be a string")
    max_bytes = 5_000_000
    encoded = content.encode("utf-8")
    if len(encoded) > max_bytes:
        raise ToolExecutionError("write exceeds maximum file size")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(encoded)
    return {"bytes_written": len(encoded)}


async def email_send(resource: str | None, parameters: dict[str, Any], workspace_root: str) -> dict[str, Any]:
    # Simulated: no real SMTP integration in this build (kept out of scope
    # deliberately -- see ARCHITECTURE.md). Demonstrates the require_approval
    # path end to end without needing real credentials/mail infra.
    to = parameters.get("to")
    subject = parameters.get("subject", "")
    if not to:
        raise ToolExecutionError("parameters.to is required")
    return {"simulated": True, "to": to, "subject": subject, "status": "queued"}


async def spend_charge(resource: str | None, parameters: dict[str, Any], workspace_root: str) -> dict[str, Any]:
    amount = parameters.get("amount_usd")
    if amount is None:
        raise ToolExecutionError("parameters.amount_usd is required")
    return {"simulated": True, "charged_usd": amount}


TOOLS: dict[str, Tool] = {
    "file.read": file_read,
    "file.write": file_write,
    "email.send": email_send,
    "spend.charge": spend_charge,
}

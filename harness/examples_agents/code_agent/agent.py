#!/usr/bin/env python3
"""
Example code-shape agent ("report-mailer").

Task input: {"report_path": "report.txt", "notify": "team@example.com"}

Behavior:
  1. file.read the report (expected to be ALLOWED by a reasonable policy
     scoping it to /workspace/**)
  2. email.send a summary to `notify` (expected to REQUIRE_APPROVAL under
     the example policy)
  3. attempts file.read of /etc/passwd, an intentionally out-of-scope
     resource, to demonstrate the DENY path end-to-end in the demo/tests

This file is UNTRUSTED input to the harness -- it is exactly the kind of
third-party package a BYOA registration would accept. It imports nothing
except the stdlib and the tiny agent_sdk shim.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import agent_sdk  # noqa: E402


def main() -> None:
    task_input = agent_sdk.read_task_input()
    report_path = task_input.get("report_path", "report.txt")
    notify = task_input.get("notify", "team@example.com")

    workspace_resource = f"/workspace/{report_path}"
    try:
        read_result = agent_sdk.call_action("file.read", resource=workspace_resource)
        agent_sdk.log(f"read {len(read_result.get('content', ''))} bytes from {workspace_resource}")
    except PermissionError as e:
        agent_sdk.fail(f"could not read report: {e}")
        return

    summary = read_result["content"][:200]

    try:
        send_result = agent_sdk.call_action(
            "email.send",
            parameters={"to": notify, "subject": "Report summary", "body": summary},
        )
        agent_sdk.log(f"email queued: {send_result}")
    except PermissionError as e:
        agent_sdk.log(f"email send was denied/not approved: {e}")
        send_result = None

    # Intentionally out-of-scope action, to demonstrate the deny path.
    denied_attempt = None
    try:
        agent_sdk.call_action("file.read", resource="/etc/passwd")
    except PermissionError as e:
        denied_attempt = str(e)
        agent_sdk.log(f"as expected, out-of-scope read was denied: {e}")

    agent_sdk.finish(
        {
            "summary": summary,
            "email_result": send_result,
            "out_of_scope_denied": denied_attempt,
        }
    )


if __name__ == "__main__":
    main()

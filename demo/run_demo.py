#!/usr/bin/env python3
"""
Scripted end-to-end demo.

Run this against a live harness (docker compose up, or `uvicorn
harness.main:app`) and it will:
  1. register the code-shape example agent
  2. register the declarative-shape example agent
  3. create and attach the example policy
  4. submit a task to the code agent that hits ALLOW, REQUIRE_APPROVAL, and
     DENY in a single run (the intentional failure/edge case: an
     out-of-scope file read that policy denies, handled gracefully)
  5. resolve the pending approval
  6. submit a task to the declarative agent (the second BYOA shape)
  7. print the full audit trail for both sessions

This is what to screen-record for the assignment's required demo video: run
`python3 demo/run_demo.py` and narrate each printed section as it appears.

Usage:
    python3 demo/run_demo.py [--base-url http://localhost:8000]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path

import httpx
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent


def banner(title: str) -> None:
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def wait_for_task(client: httpx.Client, task_id: str, timeout: float = 30) -> dict:
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        r = client.get(f"/api/v1/tasks/{task_id}")
        r.raise_for_status()
        task = r.json()
        if task["status"] in ("succeeded", "failed"):
            return task
        time.sleep(0.3)
    raise TimeoutError(f"task {task_id} did not finish within {timeout}s")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8000")
    args = parser.parse_args()

    client = httpx.Client(base_url=args.base_url, timeout=30)

    try:
        client.get("/health").raise_for_status()
    except httpx.HTTPError as e:
        print(f"Could not reach the harness at {args.base_url}: {e}")
        print("Start it first: docker compose up   (or) uvicorn harness.main:app")
        sys.exit(1)

    suffix = uuid.uuid4().hex[:6]

    banner("1. Register the code-shape agent (real OS subprocess sandbox)")
    r = client.post(
        "/api/v1/agents",
        json={
            "name": f"demo-report-mailer-{suffix}",
            "owner": "demo",
            "shape": "code",
            "initial_version": {
                "runtime_kind": "process",
                "spec": {
                    "entrypoint": [
                        "python3",
                        str(REPO_ROOT / "harness" / "examples_agents" / "code_agent" / "agent.py"),
                    ]
                },
                "resource_limits": {"cpu_seconds": 5, "memory_mb": 128, "timeout_seconds": 15},
            },
        },
    )
    r.raise_for_status()
    code_agent = r.json()
    print(json.dumps(code_agent, indent=2))

    banner("2. Register the declarative-shape agent (data-only, zero code execution)")
    with open(REPO_ROOT / "harness" / "examples_agents" / "config_agent" / "agent.yaml") as f:
        decl_spec = yaml.safe_load(f)
    r = client.post(
        "/api/v1/agents",
        json={
            "name": f"demo-workspace-archiver-{suffix}",
            "owner": "demo",
            "shape": "declarative",
            "initial_version": {"runtime_kind": "declarative", "spec": decl_spec, "resource_limits": {"max_steps": 20}},
        },
    )
    r.raise_for_status()
    decl_agent = r.json()
    print(json.dumps(decl_agent, indent=2))

    banner("3. Create and attach the example policy (allow / deny / require_approval / budget)")
    with open(REPO_ROOT / "policies" / "production-agent-policy.yaml") as f:
        policy_src = f.read()
    print(policy_src)
    r = client.post("/api/v1/policies", json={"name": f"demo-policy-{suffix}", "source": policy_src})
    r.raise_for_status()
    policy = r.json()
    for agent in (code_agent, decl_agent):
        r = client.post(
            f"/api/v1/policies/{policy['id']}/versions/1/attach",
            json={"scope_type": "agent", "scope_id": agent["id"]},
        )
        r.raise_for_status()
    print(f"Attached policy {policy['id']} v1 to both agents.")

    banner("4. Submit a task to the code agent -- exercises ALLOW, REQUIRE_APPROVAL, and DENY")
    r = client.post(
        "/api/v1/tasks",
        json={
            "agent_id": code_agent["id"],
            "agent_version_id": code_agent["versions"][0]["id"],
            "input": {"report_path": "report.txt", "notify": "team@example.com"},
            "workspace_files": {"report.txt": "Q3 revenue up 12%. Churn down. Team morale high."},
        },
    )
    r.raise_for_status()
    submission = r.json()
    task_id, session_id = submission["task_id"], submission["session_id"]
    print(f"task_id={task_id} session_id={session_id}")
    print("Agent will: (a) read report.txt [ALLOW], (b) email the summary [REQUIRE_APPROVAL],")
    print("            (c) attempt to read /etc/passwd [DENY -- the edge case, handled gracefully]")

    banner("5. Wait for the approval to appear, then resolve it")
    approval = None
    for _ in range(50):
        r = client.get("/api/v1/approvals", params={"status": "pending"})
        pending = [a for a in r.json() if a["action_id"]]
        if pending:
            approval = pending[-1]
            break
        time.sleep(0.2)
    if approval is None:
        print("No pending approval appeared -- something's wrong.")
        sys.exit(1)
    print(f"Pending approval: {json.dumps(approval, indent=2)}")
    r = client.post(f"/api/v1/approvals/{approval['id']}/resolve", json={"approve": True, "approver": "demo-manager"})
    r.raise_for_status()
    print(f"Resolved: {json.dumps(r.json(), indent=2)}")

    banner("6. Wait for the task to finish and show the result")
    task = wait_for_task(client, task_id)
    print(json.dumps(task, indent=2))

    banner("7. Submit a task to the declarative agent (second BYOA shape)")
    r = client.post(
        "/api/v1/tasks",
        json={
            "agent_id": decl_agent["id"],
            "agent_version_id": decl_agent["versions"][0]["id"],
            "input": {"source_file": "notes.txt", "archive_note": "archived by demo"},
            "workspace_files": {"notes.txt": "meeting notes"},
        },
    )
    r.raise_for_status()
    decl_task_id = r.json()["task_id"]
    decl_task = wait_for_task(client, decl_task_id)
    print(json.dumps(decl_task, indent=2))

    banner("8. Full audit trail for the code-agent session (every action, every decision, every rule)")
    r = client.get("/api/v1/audit/events", params={"session_id": session_id})
    for event in r.json():
        print(f" {event['timestamp']}  {event['event_type']:22s} decision={str(event['decision']):16s} rule={event['rule_id']}")

    banner("Demo complete")
    print("Swagger UI: " + args.base_url + "/docs")


if __name__ == "__main__":
    main()

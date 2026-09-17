from __future__ import annotations

import asyncio

import pytest

from tests.conftest import unique

APPROVAL_POLICY = """
policy: {name: p, version: 1, default_effect: deny}
rules:
  - {id: approve-spend, action: spend.charge, effect: require_approval}
"""


async def register_and_submit(app_client, policy_source=APPROVAL_POLICY):
    r = await app_client.post(
        "/api/v1/agents",
        json={
            "name": unique("approval-agent"),
            "owner": "tester",
            "shape": "declarative",
            "initial_version": {
                "runtime_kind": "declarative",
                "spec": {"name": "spender", "steps": [{"action": "spend.charge", "parameters": {"amount_usd": 2}}]},
                "resource_limits": {},
            },
        },
    )
    agent = r.json()
    r = await app_client.post("/api/v1/policies", json={"name": unique("approval-policy"), "source": policy_source})
    policy = r.json()
    r = await app_client.post(
        f"/api/v1/policies/{policy['id']}/versions/1/attach", json={"scope_type": "agent", "scope_id": agent["id"]}
    )
    assert r.status_code == 201
    r = await app_client.post(
        "/api/v1/tasks", json={"agent_id": agent["id"], "agent_version_id": agent["versions"][0]["id"], "input": {}}
    )
    return r.json()["task_id"]


async def wait_for_pending_approval(app_client, timeout=10):
    for _ in range(int(timeout / 0.2)):
        r = await app_client.get("/api/v1/approvals", params={"status": "pending"})
        if r.json():
            return r.json()[-1]  # most recently created, in case other tests left approvals pending
        await asyncio.sleep(0.2)
    raise AssertionError("no pending approval appeared in time")


async def wait_task_terminal(app_client, task_id, timeout=15):
    for _ in range(int(timeout / 0.2)):
        r = await app_client.get(f"/api/v1/tasks/{task_id}")
        if r.json()["status"] in ("succeeded", "failed"):
            return r.json()
        await asyncio.sleep(0.2)
    raise AssertionError("task did not reach a terminal state in time")


class TestApprovalFlow:
    @pytest.mark.asyncio
    async def test_action_genuinely_pauses_until_approved(self, app_client):
        task_id = await register_and_submit(app_client)
        approval = await wait_for_pending_approval(app_client)

        # Confirm the task is genuinely paused, not just slow: give it a
        # moment and verify it has NOT completed while approval is pending.
        await asyncio.sleep(0.5)
        r = await app_client.get(f"/api/v1/tasks/{task_id}")
        assert r.json()["status"] == "running", "task must not proceed before approval is resolved"

        r = await app_client.post(f"/api/v1/approvals/{approval['id']}/resolve", json={"approve": True, "approver": "mgr"})
        assert r.status_code == 200
        assert r.json()["status"] == "approved"

        task = await wait_task_terminal(app_client, task_id)
        assert task["status"] == "succeeded"

    @pytest.mark.asyncio
    async def test_denied_approval_blocks_the_action(self, app_client):
        task_id = await register_and_submit(app_client)
        approval = await wait_for_pending_approval(app_client)
        r = await app_client.post(f"/api/v1/approvals/{approval['id']}/resolve", json={"approve": False, "approver": "mgr"})
        assert r.status_code == 200
        assert r.json()["status"] == "denied"

        task = await wait_task_terminal(app_client, task_id)
        assert task["status"] == "succeeded"  # the declarative agent finishes its step list either way
        actions = (await app_client.get(f"/api/v1/tasks/{task_id}/actions")).json()
        assert actions[0]["status"] == "denied"
        assert actions[0]["error"] is not None and "denied" in actions[0]["error"]

    @pytest.mark.asyncio
    async def test_duplicate_resolution_rejected(self, app_client):
        task_id = await register_and_submit(app_client)
        approval = await wait_for_pending_approval(app_client)
        r1 = await app_client.post(f"/api/v1/approvals/{approval['id']}/resolve", json={"approve": True, "approver": "mgr1"})
        assert r1.status_code == 200

        r2 = await app_client.post(f"/api/v1/approvals/{approval['id']}/resolve", json={"approve": False, "approver": "mgr2"})
        assert r2.status_code == 409, "a second, conflicting resolution must be rejected, not silently applied"

        # First resolution's outcome must stand.
        r = await app_client.get(f"/api/v1/approvals/{approval['id']}")
        assert r.json()["status"] == "approved"
        assert r.json()["approver"] == "mgr1"

    @pytest.mark.asyncio
    async def test_approval_timeout_denies_action(self, app_client):
        # HARNESS_APPROVAL_DEFAULT_TIMEOUT_SECONDS=5 is set in conftest for
        # exactly this test -- real deployments would use a longer default.
        task_id = await register_and_submit(app_client)
        approval = await wait_for_pending_approval(app_client)

        task = await wait_task_terminal(app_client, task_id, timeout=20)
        assert task["status"] == "succeeded"
        actions = (await app_client.get(f"/api/v1/tasks/{task_id}/actions")).json()
        assert actions[0]["status"] == "timed_out"

        r = await app_client.get(f"/api/v1/approvals/{approval['id']}")
        assert r.json()["status"] == "timed_out"

        # Approving after timeout must be rejected -- an action that already
        # ran (denied-by-timeout) cannot be retroactively approved.
        r = await app_client.post(f"/api/v1/approvals/{approval['id']}/resolve", json={"approve": True, "approver": "late-mgr"})
        assert r.status_code == 409

"""
Authorization tests: API-key -> role -> permission enforcement.

These prove the RBAC layer added in harness/core/security.py actually gates
the routes it's supposed to, server-side -- never relying on the frontend
to hide a button. See docs/SECURITY.md for the full permission table these
tests are checking.
"""
from __future__ import annotations

import asyncio

import pytest

from tests.conftest import unique

# submit_task() schedules the actual run as a fire-and-forget
# asyncio.create_task() -- if a test returns without waiting for it to reach
# a terminal state, that background coroutine keeps running (and keeps
# writing to the single shared SQLite file) concurrently with every test
# that runs after it, which intermittently collides with THEIR writes
# ("database is locked"). Every test that submits a task must drain it with
# this helper before returning, exactly like the pre-existing test suite's
# own polling pattern (see demo/run_demo.py's wait_for_task).
async def _wait_for_task(app_client, task_id: str, timeout: float = 15) -> dict:
    for _ in range(int(timeout / 0.2)):
        r = await app_client.get(f"/api/v1/tasks/{task_id}")
        task = r.json()
        if task["status"] in ("succeeded", "failed"):
            return task
        await asyncio.sleep(0.2)
    raise AssertionError(f"task {task_id} did not finish within {timeout}s")

DECLARATIVE_SPEC = {
    "name": "authz-test-agent",
    "steps": [{"action": "file.read", "resource": "/workspace/notes.txt", "parameters": {}}],
}

PERMISSIVE_POLICY = """
policy: {name: %s, version: 1, default_effect: deny}
rules:
  - {id: allow-all-reads, action: file.read, resource: "/workspace/**", effect: allow}
"""


async def _register_declarative_agent(app_client) -> dict:
    r = await app_client.post(
        "/api/v1/agents",
        json={
            "name": unique("authz-agent"),
            "owner": "test",
            "shape": "declarative",
            "initial_version": {"runtime_kind": "declarative", "spec": DECLARATIVE_SPEC, "resource_limits": {}},
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _attach_permissive_policy(app_client, agent_id: str) -> None:
    r = await app_client.post(
        "/api/v1/policies", json={"name": unique("authz-policy"), "source": PERMISSIVE_POLICY % unique("p")}
    )
    assert r.status_code == 201, r.text
    policy = r.json()
    r = await app_client.post(
        f"/api/v1/policies/{policy['id']}/versions/1/attach", json={"scope_type": "agent", "scope_id": agent_id}
    )
    assert r.status_code == 201, r.text


class TestUnauthenticated:
    @pytest.mark.asyncio
    async def test_no_api_key_is_401(self, anon_client):
        r = await anon_client.get("/api/v1/agents")
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_bad_api_key_is_401(self, app_client):
        r = await app_client.get("/api/v1/agents", headers={"X-API-Key": "totally-not-a-real-key"})
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_health_needs_no_key(self, anon_client):
        r = await anon_client.get("/health")
        assert r.status_code == 200


class TestRolePermissions:
    @pytest.mark.asyncio
    async def test_client_cannot_register_agent(self, client_client):
        r = await client_client.post(
            "/api/v1/agents",
            json={
                "name": unique("should-fail"),
                "owner": "x",
                "shape": "declarative",
                "initial_version": {"runtime_kind": "declarative", "spec": DECLARATIVE_SPEC, "resource_limits": {}},
            },
        )
        assert r.status_code == 403

    @pytest.mark.asyncio
    async def test_client_cannot_create_policy(self, client_client):
        r = await client_client.post("/api/v1/policies", json={"name": unique("should-fail"), "source": PERMISSIVE_POLICY % unique("p")})
        assert r.status_code == 403

    @pytest.mark.asyncio
    async def test_operator_can_view_agents_but_not_register(self, app_client, operator_client):
        agent = await _register_declarative_agent(app_client)  # ADMIN registers it
        r = await operator_client.get(f"/api/v1/agents/{agent['id']}")
        assert r.status_code == 200

        r = await operator_client.post(
            "/api/v1/agents",
            json={
                "name": unique("should-fail"),
                "owner": "x",
                "shape": "declarative",
                "initial_version": {"runtime_kind": "declarative", "spec": DECLARATIVE_SPEC, "resource_limits": {}},
            },
        )
        assert r.status_code == 403

    @pytest.mark.asyncio
    async def test_auditor_can_read_audit_but_not_resolve_approvals(self, app_client, auditor_client):
        r = await auditor_client.get("/api/v1/audit/events")
        assert r.status_code == 200

        r = await auditor_client.post("/api/v1/approvals/nonexistent-id/resolve", json={"approve": True})
        assert r.status_code == 403  # role check happens before the 404 lookup

    @pytest.mark.asyncio
    async def test_auditor_cannot_view_agents(self, auditor_client):
        r = await auditor_client.get("/api/v1/agents")
        assert r.status_code == 403


class TestClientTaskScoping:
    @pytest.mark.asyncio
    async def test_client_can_submit_and_read_own_task(self, app_client, client_client):
        agent = await _register_declarative_agent(app_client)
        await _attach_permissive_policy(app_client, agent["id"])

        r = await client_client.post(
            "/api/v1/tasks",
            json={"agent_id": agent["id"], "input": {}, "workspace_files": {"notes.txt": "hi"}},
        )
        assert r.status_code == 202, r.text
        task_id = r.json()["task_id"]
        await _wait_for_task(client_client, task_id)

        r = await client_client.get(f"/api/v1/tasks/{task_id}")
        assert r.status_code == 200
        assert r.json()["created_by"] == "client-key"

    @pytest.mark.asyncio
    async def test_client_cannot_read_another_clients_task(self, app_client, client_client):
        # ADMIN submits a task with no CLIENT ownership at all (created_by
        # stays None) -- a CLIENT must not be able to read it either, since
        # None != "client-key".
        agent = await _register_declarative_agent(app_client)
        await _attach_permissive_policy(app_client, agent["id"])
        r = await app_client.post(
            "/api/v1/tasks", json={"agent_id": agent["id"], "input": {}, "workspace_files": {"notes.txt": "hi"}}
        )
        assert r.status_code == 202, r.text
        task_id = r.json()["task_id"]
        await _wait_for_task(app_client, task_id)

        r = await client_client.get(f"/api/v1/tasks/{task_id}")
        assert r.status_code == 404  # not 403 -- existence itself isn't leaked to CLIENT


class TestApproverIdentity:
    @pytest.mark.asyncio
    async def test_resolved_approval_records_authenticated_approver_not_client_supplied_string(
        self, app_client, operator_client
    ):
        # Reuses the code-agent + email-approval flow already proven in
        # test_approval_flow.py, but checks the identity side: the approver
        # recorded is the OPERATOR principal's label, even if the caller
        # doesn't pass one at all.
        from tests.conftest import agent_script

        r = await app_client.post(
            "/api/v1/agents",
            json={
                "name": unique("authz-approve-agent"),
                "owner": "test",
                "shape": "code",
                "initial_version": {
                    "runtime_kind": "process",
                    "spec": {"entrypoint": ["python3", agent_script("harness", "examples_agents", "code_agent", "agent.py")]},
                    "resource_limits": {"cpu_seconds": 5, "memory_mb": 128, "timeout_seconds": 15},
                },
            },
        )
        assert r.status_code == 201, r.text
        agent = r.json()

        policy_src = """
policy: {name: %s, version: 1, default_effect: deny}
rules:
  - {id: allow-read, action: file.read, resource: "/workspace/**", effect: allow}
  - {id: approve-email, action: email.send, effect: require_approval}
""" % unique("p")
        r = await app_client.post("/api/v1/policies", json={"name": unique("authz-approve-policy"), "source": policy_src})
        assert r.status_code == 201, r.text
        policy = r.json()
        r = await app_client.post(
            f"/api/v1/policies/{policy['id']}/versions/1/attach", json={"scope_type": "agent", "scope_id": agent["id"]}
        )
        assert r.status_code == 201

        r = await app_client.post(
            "/api/v1/tasks",
            json={
                "agent_id": agent["id"],
                "input": {"report_path": "report.txt", "notify": "team@example.com"},
                "workspace_files": {"report.txt": "hi"},
            },
        )
        assert r.status_code == 202, r.text
        task_id = r.json()["task_id"]

        approval = None
        for _ in range(50):
            r = await app_client.get("/api/v1/approvals", params={"status": "pending"})
            pending = r.json()
            if pending:
                approval = pending[-1]
                break
            await asyncio.sleep(0.2)
        assert approval is not None, "no pending approval appeared"

        r = await operator_client.post(f"/api/v1/approvals/{approval['id']}/resolve", json={"approve": True})
        assert r.status_code == 200, r.text
        assert r.json()["approver"] == "operator-key"
        await _wait_for_task(app_client, task_id)

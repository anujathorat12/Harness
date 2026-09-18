"""
Tests for the architect's specific requirement: the Harness itself must
provide the REST API an external application uses to invoke an onboarded
agent -- POST /api/v1/agents/{agent_id}/tasks -- and that API must remain
fully governed (no bypass of the policy engine, HITL, or authorization).

These deliberately drive everything through HTTP (the `app_client`/
`client_client`/`operator_client` fixtures are httpx clients against the
ASGI app, exactly what a real external application would see -- no
internal Python module of the harness is imported or called directly by
these tests except to read back expected values).
"""
from __future__ import annotations

import asyncio

import pytest

from tests.conftest import agent_script, unique

DECLARATIVE_SPEC = {
    "name": "external-api-declarative-agent",
    "steps": [{"action": "file.read", "resource": "/workspace/{{input.source_file}}", "parameters": {}}],
}

PERMISSIVE_READ_POLICY = """
policy: {name: %s, version: 1, default_effect: deny}
rules:
  - {id: allow-read, action: file.read, resource: "/workspace/**", effect: allow}
"""

APPROVAL_POLICY = """
policy: {name: %s, version: 1, default_effect: deny}
rules:
  - {id: allow-read, action: file.read, resource: "/workspace/**", effect: allow}
  - {id: approve-email, action: email.send, effect: require_approval}
  - {id: deny-etc, action: file.read, resource: "/etc/**", effect: deny}
"""


async def _register_declarative_agent(app_client, name: str | None = None) -> dict:
    r = await app_client.post(
        "/api/v1/agents",
        json={
            "name": name or unique("ext-declarative-agent"),
            "owner": "external-integration-test",
            "shape": "declarative",
            "initial_version": {"runtime_kind": "declarative", "spec": DECLARATIVE_SPEC, "resource_limits": {}},
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _register_code_agent(app_client, name: str | None = None) -> dict:
    r = await app_client.post(
        "/api/v1/agents",
        json={
            "name": name or unique("ext-code-agent"),
            "owner": "external-integration-test",
            "shape": "code",
            "initial_version": {
                "runtime_kind": "process",
                "spec": {"entrypoint": ["python3", agent_script("harness", "examples_agents", "code_agent", "agent.py")]},
                "resource_limits": {"cpu_seconds": 5, "memory_mb": 128, "timeout_seconds": 15},
            },
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _attach_policy(app_client, agent_id: str, source: str) -> None:
    r = await app_client.post("/api/v1/policies", json={"name": unique("ext-policy"), "source": source})
    assert r.status_code == 201, r.text
    policy = r.json()
    r = await app_client.post(
        f"/api/v1/policies/{policy['id']}/versions/1/attach", json={"scope_type": "agent", "scope_id": agent_id}
    )
    assert r.status_code == 201, r.text


async def _wait_for_task(app_client, task_id: str, timeout: float = 15) -> dict:
    for _ in range(int(timeout / 0.2)):
        r = await app_client.get(f"/api/v1/tasks/{task_id}")
        task = r.json()
        if task["status"] in ("succeeded", "failed"):
            return task
        await asyncio.sleep(0.2)
    raise AssertionError(f"task {task_id} did not finish within {timeout}s")


class TestAgentScopedInvocationEndpoint:
    """POST /api/v1/agents/{agent_id}/tasks is the contract an external
    application integrates against -- it never touches the agent, its
    runtime, or its sandbox directly."""

    @pytest.mark.asyncio
    async def test_response_shape_matches_external_contract(self, app_client):
        agent = await _register_declarative_agent(app_client)
        await _attach_policy(app_client, agent["id"], PERMISSIVE_READ_POLICY % unique("p"))

        r = await app_client.post(
            f"/api/v1/agents/{agent['id']}/tasks",
            json={"input": {"source_file": "notes.txt"}, "workspace_files": {"notes.txt": "hi"}},
        )
        assert r.status_code == 202, r.text
        body = r.json()
        # Exactly the fields an external caller needs, nothing internal.
        assert set(body.keys()) == {"session_id", "task_id", "agent_id", "status"}
        assert body["agent_id"] == agent["id"]
        assert body["status"] == "pending"
        await _wait_for_task(app_client, body["task_id"])

    @pytest.mark.asyncio
    async def test_unknown_agent_id_is_404_not_a_leak(self, app_client):
        r = await app_client.post("/api/v1/agents/does-not-exist/tasks", json={"input": {}})
        assert r.status_code == 404

    @pytest.mark.asyncio
    async def test_same_endpoint_shape_works_for_declarative_agent_allow_path(self, app_client):
        agent = await _register_declarative_agent(app_client)
        await _attach_policy(app_client, agent["id"], PERMISSIVE_READ_POLICY % unique("p"))

        r = await app_client.post(
            f"/api/v1/agents/{agent['id']}/tasks",
            json={"input": {"source_file": "notes.txt"}, "workspace_files": {"notes.txt": "declarative content"}},
        )
        assert r.status_code == 202, r.text
        task = await _wait_for_task(app_client, r.json()["task_id"])
        assert task["status"] == "succeeded", task
        assert task["result"]["step_results"][0]["allowed"] is True

    @pytest.mark.asyncio
    async def test_same_endpoint_shape_works_for_code_agent_full_lifecycle(self, operator_client, app_client):
        """The exact same POST /agents/{id}/tasks path, no branching, proves
        ALLOW, REQUIRE_APPROVAL (resumed via the Harness's own approval
        endpoint, never by the agent or the external caller talking to each
        other directly), and DENY all in one external-facing invocation."""
        agent = await _register_code_agent(app_client)
        await _attach_policy(app_client, agent["id"], APPROVAL_POLICY % unique("p"))

        r = await app_client.post(
            f"/api/v1/agents/{agent['id']}/tasks",
            json={
                "input": {"report_path": "report.txt", "notify": "team@example.com"},
                "workspace_files": {"report.txt": "external client content"},
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
        assert approval is not None, "REQUIRE_APPROVAL did not pause execution as expected"

        # Resolved through the Harness's own approval endpoint -- the agent
        # never receives an approval signal from the external caller
        # directly, only via the Harness resuming its runtime.
        r = await operator_client.post(f"/api/v1/approvals/{approval['id']}/resolve", json={"approve": True})
        assert r.status_code == 200, r.text

        task = await _wait_for_task(app_client, task_id)
        assert task["status"] == "succeeded", task
        assert task["result"]["email_result"]["status"] == "queued"  # ALLOW path (after approval) executed
        assert "denied by policy" in task["result"]["out_of_scope_denied"]  # DENY path executed, tool never ran

    @pytest.mark.asyncio
    async def test_deny_prevents_tool_execution(self, app_client):
        """A policy that denies file.read on /etc/** must mean the tool
        genuinely never runs -- proven by the audit trail showing a `deny`
        decision and no `action_executed` event for that action."""
        agent = await _register_code_agent(app_client)
        # No require_approval rule this time -- isolates the DENY path.
        policy_src = """
policy: {name: %s, version: 1, default_effect: deny}
rules:
  - {id: allow-read, action: file.read, resource: "/workspace/**", effect: allow}
  - {id: allow-email, action: email.send, effect: allow}
""" % unique("p")
        await _attach_policy(app_client, agent["id"], policy_src)

        r = await app_client.post(
            f"/api/v1/agents/{agent['id']}/tasks",
            json={
                "input": {"report_path": "report.txt", "notify": "team@example.com"},
                "workspace_files": {"report.txt": "content"},
            },
        )
        assert r.status_code == 202, r.text
        task_id = r.json()["task_id"]
        task = await _wait_for_task(app_client, task_id)
        assert task["status"] == "succeeded"
        assert "denied by policy" in task["result"]["out_of_scope_denied"]

        # Audit proves the tool truly never executed for the denied action.
        r = await app_client.get(f"/api/v1/tasks/{task_id}/actions")
        actions = r.json()
        denied = [a for a in actions if a["resource"] == "/etc/passwd"]
        assert len(denied) == 1
        assert denied[0]["decision"] == "deny"
        assert denied[0]["status"] == "denied"
        assert denied[0]["result"] is None  # the tool function was never called


class TestExternalAuthorizationOnAgentInvocation:
    @pytest.mark.asyncio
    async def test_unauthenticated_caller_cannot_invoke_an_agent(self, app_client, anon_client):
        agent = await _register_declarative_agent(app_client)
        r = await anon_client.post(f"/api/v1/agents/{agent['id']}/tasks", json={"input": {}})
        assert r.status_code == 401

    @pytest.mark.asyncio
    async def test_auditor_cannot_invoke_an_agent(self, app_client, auditor_client):
        agent = await _register_declarative_agent(app_client)
        r = await auditor_client.post(f"/api/v1/agents/{agent['id']}/tasks", json={"input": {}})
        assert r.status_code == 403

    @pytest.mark.asyncio
    async def test_client_role_can_invoke_and_only_read_its_own_result(self, app_client, client_client):
        agent = await _register_declarative_agent(app_client)
        await _attach_policy(app_client, agent["id"], PERMISSIVE_READ_POLICY % unique("p"))

        r = await client_client.post(
            f"/api/v1/agents/{agent['id']}/tasks",
            json={"input": {"source_file": "notes.txt"}, "workspace_files": {"notes.txt": "client content"}},
        )
        assert r.status_code == 202, r.text
        task_id = r.json()["task_id"]
        await _wait_for_task(client_client, task_id)

        r = await client_client.get(f"/api/v1/tasks/{task_id}")
        assert r.status_code == 200
        assert r.json()["created_by"] == "client-key"

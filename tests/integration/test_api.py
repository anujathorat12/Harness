from __future__ import annotations

import asyncio

import pytest

from tests.conftest import agent_script, unique

DECLARATIVE_SPEC = {"name": "noop-agent", "steps": [{"action": "spend.charge", "parameters": {"amount_usd": 1}}]}

PERMISSIVE_POLICY = """
policy: {name: p, version: 1, default_effect: deny}
rules:
  - {id: allow-spend, action: spend.charge, effect: allow}
  - {id: allow-reads, action: file.read, resource: "/workspace/**", effect: allow}
"""


async def register_declarative_agent(app_client, name=None):
    r = await app_client.post(
        "/api/v1/agents",
        json={
            "name": name or unique("agent"),
            "owner": "tester",
            "shape": "declarative",
            "initial_version": {"runtime_kind": "declarative", "spec": DECLARATIVE_SPEC, "resource_limits": {}},
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


async def attach_permissive_policy(app_client, agent_id, source=PERMISSIVE_POLICY):
    r = await app_client.post("/api/v1/policies", json={"name": unique("policy"), "source": source})
    assert r.status_code == 201, r.text
    policy = r.json()
    r = await app_client.post(
        f"/api/v1/policies/{policy['id']}/versions/1/attach", json={"scope_type": "agent", "scope_id": agent_id}
    )
    assert r.status_code == 201
    return policy


class TestAgentRegistry:
    @pytest.mark.asyncio
    async def test_register_and_fetch_agent(self, app_client):
        agent = await register_declarative_agent(app_client)
        r = await app_client.get(f"/api/v1/agents/{agent['id']}")
        assert r.status_code == 200
        assert r.json()["id"] == agent["id"]
        assert len(r.json()["versions"]) == 1

    @pytest.mark.asyncio
    async def test_duplicate_agent_name_rejected(self, app_client):
        name = unique("dup-agent")
        await register_declarative_agent(app_client, name=name)
        r = await app_client.post(
            "/api/v1/agents",
            json={
                "name": name,
                "owner": "tester",
                "shape": "declarative",
                "initial_version": {"runtime_kind": "declarative", "spec": DECLARATIVE_SPEC, "resource_limits": {}},
            },
        )
        assert r.status_code == 409

    @pytest.mark.asyncio
    async def test_add_second_agent_version(self, app_client):
        agent = await register_declarative_agent(app_client)
        r = await app_client.post(
            f"/api/v1/agents/{agent['id']}/versions",
            json={"runtime_kind": "declarative", "spec": DECLARATIVE_SPEC, "resource_limits": {}},
        )
        assert r.status_code == 201
        assert r.json()["version"] == 2

    @pytest.mark.asyncio
    async def test_two_distinct_agent_shapes_both_runnable(self, app_client):
        """Proves the harness isn't secretly hardcoded to one agent shape:
        registers one 'code' agent (real subprocess) and one 'declarative'
        agent (interpreted spec, zero code execution) and runs both to
        completion through the identical task-submission API."""
        code_agent_resp = await app_client.post(
            "/api/v1/agents",
            json={
                "name": unique("code-shape"),
                "owner": "tester",
                "shape": "code",
                "initial_version": {
                    "runtime_kind": "process",
                    "spec": {"entrypoint": ["python3", agent_script("harness", "examples_agents", "code_agent", "agent.py")]},
                    "resource_limits": {"cpu_seconds": 5, "memory_mb": 128, "timeout_seconds": 10},
                },
            },
        )
        assert code_agent_resp.status_code == 201
        code_agent = code_agent_resp.json()

        decl_agent = await register_declarative_agent(app_client)

        for agent in (code_agent, decl_agent):
            await attach_permissive_policy(
                app_client,
                agent["id"],
                source="""
policy: {name: p, version: 1, default_effect: deny}
rules:
  - {id: a1, action: file.read, resource: "/workspace/**", effect: allow}
  - {id: a2, action: email.send, effect: allow}
  - {id: a3, action: spend.charge, effect: allow}
""",
            )

        r = await app_client.post(
            "/api/v1/tasks",
            json={
                "agent_id": code_agent["id"],
                "agent_version_id": code_agent["versions"][0]["id"],
                "input": {"report_path": "r.txt", "notify": "a@b.com"},
                "workspace_files": {"r.txt": "hello"},
            },
        )
        code_task_id = r.json()["task_id"]

        r = await app_client.post(
            "/api/v1/tasks",
            json={"agent_id": decl_agent["id"], "agent_version_id": decl_agent["versions"][0]["id"], "input": {}},
        )
        decl_task_id = r.json()["task_id"]

        for task_id in (code_task_id, decl_task_id):
            for _ in range(60):
                r = await app_client.get(f"/api/v1/tasks/{task_id}")
                if r.json()["status"] in ("succeeded", "failed"):
                    break
                await asyncio.sleep(0.2)
            assert r.json()["status"] == "succeeded", r.json()


class TestPolicyAuthoring:
    @pytest.mark.asyncio
    async def test_reject_invalid_policy_at_creation(self, app_client):
        r = await app_client.post(
            "/api/v1/policies",
            json={"name": unique("bad"), "source": "policy: {name: p, version: 1}\nrules:\n  - {id: r1, action: x, effect: not_a_real_effect}"},
        )
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_policy_versions_are_immutable_and_stack(self, app_client):
        name = unique("versioned-policy")
        r = await app_client.post(
            "/api/v1/policies", json={"name": name, "source": "policy: {name: p, version: 1}\nrules: []"}
        )
        policy = r.json()
        r = await app_client.post(
            f"/api/v1/policies/{policy['id']}/versions", json={"source": "policy: {name: p, version: 2}\nrules: []"}
        )
        assert r.status_code == 201
        assert r.json()["version"] == 2

        # Re-submitting version 1 again must be rejected -- versions never change in place.
        r = await app_client.post(
            f"/api/v1/policies/{policy['id']}/versions", json={"source": "policy: {name: p, version: 1}\nrules: []"}
        )
        assert r.status_code == 409

    @pytest.mark.asyncio
    async def test_session_scoped_attachment_overrides_agent_default(self, app_client):
        agent = await register_declarative_agent(app_client)
        # Agent-level: deny everything.
        await attach_permissive_policy(
            app_client, agent["id"], source="policy: {name: deny-all, version: 1, default_effect: deny}\nrules: []"
        )

        r = await app_client.post(
            "/api/v1/tasks",
            json={"agent_id": agent["id"], "agent_version_id": agent["versions"][0]["id"], "input": {}},
        )
        task_id = r.json()["task_id"]
        session_id = r.json()["session_id"]

        # Session-level override: allow the spend action for THIS session only.
        r = await app_client.post("/api/v1/policies", json={"name": unique("session-override"), "source": PERMISSIVE_POLICY})
        override_policy = r.json()
        r = await app_client.post(
            f"/api/v1/policies/{override_policy['id']}/versions/1/attach",
            json={"scope_type": "session", "scope_id": session_id},
        )
        assert r.status_code == 201

        for _ in range(60):
            r = await app_client.get(f"/api/v1/tasks/{task_id}")
            if r.json()["status"] in ("succeeded", "failed"):
                break
            await asyncio.sleep(0.2)
        assert r.json()["status"] == "succeeded", r.json()


class TestUnattachedPolicyFailsClosed:
    @pytest.mark.asyncio
    async def test_agent_with_no_policy_attached_denies_everything(self, app_client):
        agent = await register_declarative_agent(app_client)
        # deliberately do NOT attach any policy
        r = await app_client.post(
            "/api/v1/tasks",
            json={"agent_id": agent["id"], "agent_version_id": agent["versions"][0]["id"], "input": {}},
        )
        task_id = r.json()["task_id"]
        for _ in range(60):
            r = await app_client.get(f"/api/v1/tasks/{task_id}")
            t = r.json()
            if t["status"] in ("succeeded", "failed"):
                break
            await asyncio.sleep(0.2)
        actions_r = await app_client.get(f"/api/v1/tasks/{task_id}/actions")
        actions = actions_r.json()
        assert len(actions) == 1
        assert actions[0]["decision"] == "deny"
        assert actions[0]["status"] == "denied"

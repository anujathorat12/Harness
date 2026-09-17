from __future__ import annotations

import asyncio

import pytest

from tests.conftest import agent_script, unique


async def register_code_agent(app_client, entrypoint, cpu_seconds=5, memory_mb=128, timeout_seconds=10):
    r = await app_client.post(
        "/api/v1/agents",
        json={
            "name": unique("concurrent-agent"),
            "owner": "tester",
            "shape": "code",
            "initial_version": {
                "runtime_kind": "process",
                "spec": {"entrypoint": entrypoint},
                "resource_limits": {"cpu_seconds": cpu_seconds, "memory_mb": memory_mb, "timeout_seconds": timeout_seconds},
            },
        },
    )
    assert r.status_code == 201, r.text
    agent = r.json()
    r = await app_client.post(
        "/api/v1/policies",
        json={
            "name": unique("policy"),
            "source": 'policy: {name: p, version: 1, default_effect: deny}\nrules:\n  - {id: r, action: file.read, resource: "/workspace/**", effect: allow}',
        },
    )
    policy = r.json()
    await app_client.post(
        f"/api/v1/policies/{policy['id']}/versions/1/attach", json={"scope_type": "agent", "scope_id": agent["id"]}
    )
    return agent


class TestConcurrency:
    @pytest.mark.asyncio
    async def test_multiple_agents_run_concurrently_and_are_isolated(self, app_client):
        """Runs a crashing/hanging agent and several well-behaved agents at
        the same time. The well-behaved ones must all still succeed
        (independent sandboxing/scheduling), and their sessions/workspaces
        must not see each other's files (session isolation)."""
        good_agent = await register_code_agent(
            app_client, ["python3", agent_script("harness", "examples_agents", "code_agent", "agent.py")]
        )
        hanging_agent = await register_code_agent(
            app_client,
            ["python3", agent_script("tests", "adversarial", "malicious_agents", "hang_forever.py")],
            timeout_seconds=3,
        )

        # Fire off 4 well-behaved tasks and 1 hanging task concurrently.
        submissions = []
        for i in range(4):
            submissions.append(
                app_client.post(
                    "/api/v1/tasks",
                    json={
                        "agent_id": good_agent["id"],
                        "agent_version_id": good_agent["versions"][0]["id"],
                        "input": {"report_path": "r.txt", "notify": "x@y.com"},
                        "workspace_files": {"r.txt": f"unique content for run {i}"},
                    },
                )
            )
        submissions.append(
            app_client.post(
                "/api/v1/tasks",
                json={"agent_id": hanging_agent["id"], "agent_version_id": hanging_agent["versions"][0]["id"], "input": {}},
            )
        )
        responses = await asyncio.gather(*submissions)
        task_ids = [r.json()["task_id"] for r in responses[:4]]
        hanging_task_id = responses[4].json()["task_id"]
        session_ids = [r.json()["session_id"] for r in responses[:4]]

        async def wait(tid):
            for _ in range(75):
                r = await app_client.get(f"/api/v1/tasks/{tid}")
                if r.json()["status"] in ("succeeded", "failed"):
                    return r.json()
                await asyncio.sleep(0.2)
            raise AssertionError(f"task {tid} never finished")

        results = await asyncio.gather(*(wait(t) for t in task_ids))
        hanging_result = await wait(hanging_task_id)

        for res in results:
            assert res["status"] == "succeeded", res
        assert hanging_result["status"] == "failed"  # killed by watchdog, did not affect the others

        # Session isolation: no session's workspace leaked into another's.
        import os

        for i, sid in enumerate(session_ids):
            ws = f"/tmp/harness-sandboxes/{sid}"
            entries = os.listdir(ws)
            assert entries == ["r.txt"], f"session {sid} workspace unexpectedly contains {entries}"
            with open(os.path.join(ws, "r.txt")) as f:
                assert f.read() == f"unique content for run {i}"

    @pytest.mark.asyncio
    async def test_per_agent_concurrency_limit_does_not_deadlock_other_agents(self, app_client):
        """max_concurrent_sessions_per_agent caps one agent's in-flight
        sessions without blocking a completely different agent's tasks."""
        agent_a = await register_code_agent(
            app_client, ["python3", agent_script("harness", "examples_agents", "code_agent", "agent.py")]
        )
        agent_b = await register_code_agent(
            app_client, ["python3", agent_script("harness", "examples_agents", "code_agent", "agent.py")]
        )

        # Flood agent_a with more tasks than its per-agent concurrency slot
        # allows (default is 3 -- see core/config.py), then submit one task
        # to agent_b and confirm it still completes promptly rather than
        # queueing behind agent_a's backlog.
        for i in range(6):
            await app_client.post(
                "/api/v1/tasks",
                json={
                    "agent_id": agent_a["id"],
                    "agent_version_id": agent_a["versions"][0]["id"],
                    "input": {"report_path": "r.txt", "notify": "x@y.com"},
                    "workspace_files": {"r.txt": "a"},
                },
            )

        r = await app_client.post(
            "/api/v1/tasks",
            json={
                "agent_id": agent_b["id"],
                "agent_version_id": agent_b["versions"][0]["id"],
                "input": {"report_path": "r.txt", "notify": "x@y.com"},
                "workspace_files": {"r.txt": "b"},
            },
        )
        task_b = r.json()["task_id"]

        for _ in range(50):
            r = await app_client.get(f"/api/v1/tasks/{task_b}")
            if r.json()["status"] in ("succeeded", "failed"):
                break
            await asyncio.sleep(0.2)
        assert r.json()["status"] == "succeeded", "agent B's task should not be starved by agent A's backlog"

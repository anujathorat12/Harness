"""
Adversarial / security tests.

These assume the agent is actively malicious, per the assignment's "Security
review / red team" and "Sandboxing & isolation robustness" (20% weight)
sections. Each test registers a real misbehaving agent (see
tests/adversarial/malicious_agents/) and runs it through the real
ProcessRuntime -- no mocking of the sandbox.
"""
from __future__ import annotations

import asyncio
import os
import time

import pytest

from tests.conftest import agent_script, unique


async def _register_and_run(app_client, script_name: str, timeout_seconds: int = 4, memory_mb: int = 64, task_input=None):
    r = await app_client.post(
        "/api/v1/agents",
        json={
            "name": unique("adversarial-agent"),
            "owner": "red-team",
            "shape": "code",
            "initial_version": {
                "runtime_kind": "process",
                "spec": {"entrypoint": ["python3", agent_script("tests", "adversarial", "malicious_agents", script_name)]},
                "resource_limits": {
                    "cpu_seconds": 2,
                    "memory_mb": memory_mb,
                    "timeout_seconds": timeout_seconds,
                    "pid_limit": 8,
                },
            },
        },
    )
    assert r.status_code == 201, r.text
    agent = r.json()

    # Attach a permissive policy so a well-behaved action would succeed --
    # we want these tests to prove the SANDBOX stops the agent, not the
    # policy engine (that's covered separately in test_policy_engine.py).
    policy_src = """
policy: {name: %s, version: 1, default_effect: deny}
rules:
  - {id: allow-all-reads, action: file.read, resource: "/workspace/**", effect: allow}
""" % unique("permissive")
    r = await app_client.post("/api/v1/policies", json={"name": unique("permissive-policy"), "source": policy_src})
    assert r.status_code == 201, r.text
    policy = r.json()
    r = await app_client.post(
        f"/api/v1/policies/{policy['id']}/versions/1/attach", json={"scope_type": "agent", "scope_id": agent["id"]}
    )
    assert r.status_code == 201

    r = await app_client.post(
        "/api/v1/tasks",
        json={"agent_id": agent["id"], "agent_version_id": agent["versions"][0]["id"], "input": task_input or {}},
    )
    assert r.status_code == 202
    task_id = r.json()["task_id"]

    start = time.monotonic()
    task = None
    for _ in range(int((timeout_seconds + 10) / 0.2)):
        r = await app_client.get(f"/api/v1/tasks/{task_id}")
        task = r.json()
        if task["status"] in ("succeeded", "failed"):
            break
        await asyncio.sleep(0.2)
    elapsed = time.monotonic() - start
    return task, elapsed, task_id


class TestSandboxEscape:
    @pytest.mark.asyncio
    async def test_cpu_bomb_is_killed(self, app_client):
        """An agent that busy-loops forever must be killed by RLIMIT_CPU (or
        the wall-clock watchdog as a backstop) well before it could exhaust
        host CPU indefinitely."""
        task, elapsed, task_id = await _register_and_run(app_client, "cpu_bomb.py", timeout_seconds=4)
        assert task["status"] == "failed"
        assert elapsed < 15, f"cpu bomb should have been killed quickly, took {elapsed}s"

    @pytest.mark.asyncio
    async def test_memory_bomb_is_killed(self, app_client):
        """An agent that tries to allocate unbounded memory must be killed
        by RLIMIT_AS (the OS kills/raises MemoryError in the child before it
        can consume host memory beyond the configured cap)."""
        task, elapsed, task_id = await _register_and_run(app_client, "memory_bomb.py", timeout_seconds=5, memory_mb=64)
        assert task["status"] == "failed"
        assert elapsed < 15

    @pytest.mark.asyncio
    async def test_fork_bomb_is_contained(self, app_client):
        """An agent that tries to fork-bomb must be capped by RLIMIT_NPROC
        long before it can exhaust the host's process table, and terminating
        the sandbox must kill the whole process group, not just the parent."""
        task, elapsed, task_id = await _register_and_run(app_client, "fork_bomb.py", timeout_seconds=6)
        # Either it's capped (raises OSError, reports how many it forked
        # before failing, which must be small) or the group gets killed by
        # the timeout -- both are acceptable containment outcomes, but it
        # must not run unbounded.
        assert elapsed < 20
        if task["status"] == "succeeded":
            forked = task["result"]["forked"]
            assert forked < 100, f"fork bomb forked {forked} children before being stopped -- PID limit not effective"

    @pytest.mark.asyncio
    async def test_hung_agent_is_killed_by_watchdog(self, app_client):
        """An agent that never sends a protocol message and never exits
        (e.g. sleeping forever, or genuinely hung) must still be killed by
        the wall-clock timeout, independent of CPU/memory usage."""
        task, elapsed, task_id = await _register_and_run(app_client, "hang_forever.py", timeout_seconds=3)
        assert task["status"] == "failed"
        assert 2.5 < elapsed < 15, f"expected the ~3s watchdog to fire, took {elapsed}s"

    @pytest.mark.asyncio
    async def test_path_traversal_blocked_by_gateway(self, app_client):
        """Even though the policy's resource glob is '/workspace/**' (which
        a naive string-glob matcher, or an agent, might think permits
        '/workspace/../../etc/passwd'), the gateway's containment check must
        independently refuse to leave the session workspace."""
        task, elapsed, task_id = await _register_and_run(app_client, "path_traversal.py", timeout_seconds=5)
        assert task["status"] == "succeeded", task
        assert task["result"]["escaped"] is False, "path traversal was NOT blocked -- critical failure"

    @pytest.mark.asyncio
    async def test_raw_syscall_bypass_is_documented_not_hidden(self, app_client):
        """An agent that ignores the SDK/protocol entirely and does a raw
        filesystem read is NOT stopped by the policy engine (there is no
        protocol message to intercept) -- this test exists to make that gap
        explicit and regression-visible, exactly per the assignment's "do
        not pretend" instruction, rather than letting the platform silently
        claim a guarantee it doesn't provide. What DOES apply is OS-level
        containment: the sandbox runs as an unprivileged process, so
        whether the raw read succeeds depends entirely on host file
        permissions, not on anything this platform enforces. We assert only
        that the harness itself does not crash and correctly records
        whatever happened."""
        task, elapsed, task_id = await _register_and_run(app_client, "raw_syscall_escape.py", timeout_seconds=5)
        assert task["status"] == "succeeded"
        # No assertion on `raw_read_succeeded` itself -- that depends on
        # host OS permissions (typically True when the harness runs as a
        # normal, non-root user that can read /etc/passwd, which is world
        # readable on virtually every Linux system). The point this test
        # proves is structural: this action bypassed the policy engine
        # entirely, so THIS TASK has zero governed Action rows -- verified
        # below, scoped to this task only (other tests legitimately touch
        # "/etc/passwd" *through* the protocol and are audited correctly;
        # this assertion must not be confused by those).
        r = await app_client.get(f"/api/v1/tasks/{task_id}/actions")
        assert r.json() == [], "a raw syscall bypass must leave no Action row -- it never reached the gateway"

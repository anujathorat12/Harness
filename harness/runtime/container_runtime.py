"""
ContainerRuntime: runs a "code" agent inside a Docker container using the
same NDJSON stdio protocol as ProcessRuntime (docs/AGENT_PROTOCOL.md), but
with real kernel-level isolation:

  - separate filesystem namespace (read-only root fs, one writable tmpfs)
  - separate network namespace (--network none by default; no host network
    access at all unless a policy explicitly attaches an allowlisted proxy,
    which is out of scope for this build -- see ARCHITECTURE.md limitations)
  - cgroup CPU/memory limits enforced by the Docker/containerd runtime, not
    by cooperating Python code
  - dropped Linux capabilities, no-new-privileges, non-root user
  - PID limit via --pids-limit

This is the runtime intended for real deployments and is what the agent
version's `runtime_kind: container` selects. It requires a Docker daemon
reachable from the harness process (DOCKER_HOST or the default socket).

HONESTY NOTE: this module could not be exercised against a live Docker
daemon inside the development sandbox this repository was built in (no
`docker` binary / daemon available there). It is written against the
documented `docker` Python SDK API and mirrors the exact protocol
ProcessRuntime already implements and IS tested against, but you should
run `pytest tests/integration/test_container_runtime.py -m docker` (skipped
by default, enabled via `--run-docker-tests`) on a machine with Docker
before relying on it in production. This is flagged again in
ARCHITECTURE.md and README "Known Limitations" -- we are not claiming
something works that hasn't been verified here.
"""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import AsyncIterator
from typing import Any

from harness.core.config import settings
from harness.runtime.base import AgentHandle, Runtime, RuntimeActionRequest, RuntimeEvent

try:
    import docker
    from docker.errors import DockerException
except ImportError:  # pragma: no cover
    docker = None
    DockerException = Exception


class ContainerAgentHandle(AgentHandle):
    def __init__(self, container: Any, timeout_seconds: int):
        self._container = container
        self._timeout = timeout_seconds
        self._sock = container.attach_socket(params={"stdin": 1, "stdout": 1, "stderr": 0, "stream": 1})
        self._sock._sock.setblocking(False)
        self._terminated = False
        self._reason: str | None = None
        self._buffer = b""

    async def _readline(self) -> bytes | None:
        loop = asyncio.get_event_loop()
        while b"\n" not in self._buffer:
            try:
                chunk = await loop.run_in_executor(None, self._sock._sock.recv, 4096)
            except (BlockingIOError, OSError):
                await asyncio.sleep(0.05)
                continue
            if not chunk:
                return None
            self._buffer += chunk
        line, _, self._buffer = self._buffer.partition(b"\n")
        return line

    async def events(self) -> AsyncIterator[RuntimeEvent]:
        watchdog = asyncio.create_task(asyncio.sleep(self._timeout))
        try:
            while True:
                read_task = asyncio.ensure_future(self._readline())
                done, _ = await asyncio.wait({read_task, watchdog}, return_when=asyncio.FIRST_COMPLETED)
                if watchdog in done and not read_task.done():
                    read_task.cancel()
                    await self.terminate("wall-clock timeout exceeded")
                    yield RuntimeEvent(kind="timeout", message="container timed out")
                    return
                line = read_task.result()
                if not line:
                    break
                try:
                    msg = json.loads(line.decode("utf-8", errors="replace"))
                except json.JSONDecodeError:
                    yield RuntimeEvent(kind="log", message=line.decode("utf-8", errors="replace"))
                    continue
                mtype = msg.get("type")
                if mtype == "action_request":
                    yield RuntimeEvent(
                        kind="action_request",
                        action=RuntimeActionRequest(
                            action_type=msg.get("action_type", ""),
                            resource=msg.get("resource"),
                            parameters=msg.get("parameters", {}) or {},
                        ),
                    )
                elif mtype == "finished":
                    yield RuntimeEvent(kind="finished", result=msg.get("result"))
                    return
                elif mtype == "error":
                    yield RuntimeEvent(kind="error", message=str(msg.get("message")))
                    return
        finally:
            if not watchdog.done():
                watchdog.cancel()

    async def resolve_action(self, allowed: bool, result: Any = None, error: str | None = None) -> None:
        payload = json.dumps({"type": "action_response", "allowed": allowed, "result": result, "error": error}) + "\n"
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, self._sock._sock.sendall, payload.encode("utf-8"))
        except OSError:
            pass

    async def terminate(self, reason: str) -> None:
        if self._terminated:
            return
        self._terminated = True
        self._reason = reason
        try:
            self._container.kill()
        except DockerException:
            pass
        try:
            self._container.remove(force=True)
        except DockerException:
            pass

    @property
    def resource_usage(self) -> dict[str, Any]:
        try:
            stats = self._container.stats(stream=False)
            return {
                "memory_bytes": stats.get("memory_stats", {}).get("usage"),
                "cpu_stats": stats.get("cpu_stats", {}).get("cpu_usage", {}).get("total_usage"),
            }
        except Exception:
            return {}


class ContainerRuntime(Runtime):
    kind = "container"

    def __init__(self) -> None:
        if docker is None:
            raise RuntimeError("docker SDK not installed")
        self._client = docker.from_env()

    async def start(
        self,
        *,
        agent_version_spec: dict[str, Any],
        resource_limits: dict[str, Any],
        task_input: dict[str, Any],
        workdir_token: str,
    ) -> AgentHandle:
        image = agent_version_spec.get("image")
        if not image:
            raise ValueError("container agent spec must provide 'image'")

        cpu_cores = float(resource_limits.get("cpu_cores", 1.0))
        mem_mb = int(resource_limits.get("memory_mb", settings.default_memory_mb))
        timeout = int(resource_limits.get("timeout_seconds", settings.default_timeout_seconds))
        pid_limit = int(resource_limits.get("pid_limit", settings.default_pid_limit))

        loop = asyncio.get_event_loop()

        def _run() -> Any:
            return self._client.containers.create(
                image=image,
                command=agent_version_spec.get("command"),
                environment={f"AGENT_ENV_{k}": str(v) for k, v in (agent_version_spec.get("env") or {}).items()},
                stdin_open=True,
                tty=False,
                network_mode=settings.container_runtime_network,
                mem_limit=f"{mem_mb}m",
                nano_cpus=int(cpu_cores * 1_000_000_000),
                pids_limit=pid_limit,
                read_only=True,
                tmpfs={"/tmp": "size=64m"},
                cap_drop=["ALL"],
                security_opt=["no-new-privileges"],
                user="1000:1000",
                labels={"harness.session": workdir_token, "harness.managed": "true"},
                detach=True,
            )

        container = await loop.run_in_executor(None, _run)
        await loop.run_in_executor(None, container.start)
        handle = ContainerAgentHandle(container, timeout_seconds=timeout)
        # Send the initial task input the same way the process runtime does.
        line = json.dumps({"type": "task_input", "input": task_input}) + "\n"
        await loop.run_in_executor(None, handle._sock._sock.sendall, line.encode("utf-8"))
        return handle

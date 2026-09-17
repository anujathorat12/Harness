"""
ProcessRuntime: runs a "code" agent as a separate OS process communicating
over an NDJSON stdio protocol (see docs/AGENT_PROTOCOL.md).

WHAT THIS ACTUALLY ISOLATES (tested):
  - CPU time      (RLIMIT_CPU)         -> agent killed if it burns too much CPU
  - Address space  (RLIMIT_AS)          -> agent killed/OOM'd if it allocates too much memory
  - Output size    (byte cap on stdout) -> a runaway print loop can't exhaust harness memory
  - Wall-clock time (asyncio timeout)   -> a hung agent is killed after N seconds
  - Process group isolation             -> killing the sandbox kills every child it spawned
  - A fresh, unique working directory per session, outside any other
    session's directory

WHAT THIS DOES *NOT* ISOLATE (documented limitation -- see ARCHITECTURE.md
"Security model & limitations"): filesystem visibility (the agent process
can still see the whole host filesystem it has OS permission to read/write,
same UID as the harness unless the harness itself is run as an unprivileged
user) and network access (no netns without root). This is why
ContainerRuntime exists: for a real deployment, code agents should run
container_runtime.py, which gives real filesystem and network namespace
isolation via Docker. ProcessRuntime is provided as a dependency-free runtime
that works in any environment (including this development sandbox, which has
no Docker daemon) and is the one exercised by this repo's own test suite.
Both implement the same Runtime interface, so switching is a one-line config
change per agent version (`runtime_kind`).

The enforcement boundary that DOES hold regardless of runtime: the agent
process's only channel to the outside world for governed actions is this
stdio protocol. The example agent's SDK exposes no other I/O primitive, so an
agent that stays within the provided SDK physically cannot perform a
governed action without an action_request round-trip. See
ARCHITECTURE.md for the honest treatment of what happens if an agent doesn't
stay within the SDK (raw os/socket calls) -- that is exactly the gap
container/OS isolation is there to contain, not something the protocol can
prevent on its own.
"""
from __future__ import annotations

import asyncio
import ctypes
import json
import os
import resource
import signal
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

from harness.core.config import settings
from harness.runtime.base import AgentHandle, Runtime, RuntimeActionRequest, RuntimeEvent

PR_SET_NO_NEW_PRIVS = 38


def _make_preexec_fn(cpu_seconds: int, memory_mb: int, pid_limit: int) -> Any:
    def preexec() -> None:
        # New process group so we can kill the whole tree on timeout/termination.
        os.setsid()
        # Best-effort no-new-privileges (Linux only; ignored elsewhere).
        try:
            libc = ctypes.CDLL("libc.so.6", use_errno=True)
            libc.prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0)
        except Exception:
            pass
        try:
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds + 1))
        except (ValueError, OSError):
            pass
        try:
            mem_bytes = memory_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))
        except (ValueError, OSError):
            pass
        try:
            resource.setrlimit(resource.RLIMIT_NPROC, (pid_limit, pid_limit))
        except (ValueError, OSError):
            pass
        try:
            resource.setrlimit(resource.RLIMIT_FSIZE, (50 * 1024 * 1024, 50 * 1024 * 1024))
        except (ValueError, OSError):
            pass

    return preexec


class ProcessAgentHandle(AgentHandle):
    def __init__(self, proc: asyncio.subprocess.Process, timeout_seconds: int, output_cap: int, workdir: str):
        self._proc = proc
        self._timeout = timeout_seconds
        self._output_cap = output_cap
        self._workdir = workdir
        self._start = time.monotonic()
        self._bytes_read = 0
        self._deadline_task: asyncio.Task | None = None
        self._terminated = False
        self._termination_reason: str | None = None

    async def _watchdog(self) -> None:
        try:
            await asyncio.sleep(self._timeout)
            await self.terminate("wall-clock timeout exceeded")
        except asyncio.CancelledError:
            pass

    async def events(self) -> AsyncIterator[RuntimeEvent]:
        self._deadline_task = asyncio.create_task(self._watchdog())
        try:
            while True:
                if self._proc.stdout is None:
                    break
                try:
                    line = await self._proc.stdout.readline()
                except (ValueError, ConnectionResetError):
                    break
                if not line:
                    break
                self._bytes_read += len(line)
                if self._bytes_read > self._output_cap:
                    await self.terminate("output byte cap exceeded")
                    yield RuntimeEvent(kind="error", message="agent exceeded output cap and was terminated")
                    return
                try:
                    msg = json.loads(line.decode("utf-8", errors="replace"))
                except json.JSONDecodeError:
                    yield RuntimeEvent(kind="log", message=line.decode("utf-8", errors="replace").rstrip())
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
                elif mtype == "log":
                    yield RuntimeEvent(kind="log", message=str(msg.get("message", "")))
                elif mtype == "finished":
                    yield RuntimeEvent(kind="finished", result=msg.get("result"))
                    return
                elif mtype == "error":
                    yield RuntimeEvent(kind="error", message=str(msg.get("message", "unknown agent error")))
                    return
                else:
                    yield RuntimeEvent(kind="log", message=f"unrecognised protocol message: {msg}")

            rc = await self._proc.wait()
            if self._terminated:
                yield RuntimeEvent(kind="killed", message=self._termination_reason or "terminated")
            elif rc != 0:
                yield RuntimeEvent(kind="error", message=f"agent process exited with code {rc}")
            else:
                yield RuntimeEvent(kind="finished", result=None)
        finally:
            if self._deadline_task and not self._deadline_task.done():
                self._deadline_task.cancel()

    async def resolve_action(self, allowed: bool, result: Any = None, error: str | None = None) -> None:
        if self._proc.stdin is None or self._proc.stdin.is_closing():
            return
        payload = json.dumps({"type": "action_response", "allowed": allowed, "result": result, "error": error}) + "\n"
        try:
            self._proc.stdin.write(payload.encode("utf-8"))
            await self._proc.stdin.drain()
        except (ConnectionResetError, BrokenPipeError):
            pass

    async def terminate(self, reason: str) -> None:
        if self._terminated:
            return
        self._terminated = True
        self._termination_reason = reason
        pgid = None
        try:
            pgid = os.getpgid(self._proc.pid)
        except ProcessLookupError:
            pass
        try:
            if pgid is not None:
                os.killpg(pgid, signal.SIGKILL)
            else:
                self._proc.kill()
        except ProcessLookupError:
            pass
        try:
            await asyncio.wait_for(self._proc.wait(), timeout=5)
        except (asyncio.TimeoutError, ProcessLookupError):
            pass

    @property
    def resource_usage(self) -> dict[str, Any]:
        elapsed = time.monotonic() - self._start
        return {"wall_seconds": round(elapsed, 3), "stdout_bytes_read": self._bytes_read}


class ProcessRuntime(Runtime):
    kind = "process"

    async def start(
        self,
        *,
        agent_version_spec: dict[str, Any],
        resource_limits: dict[str, Any],
        task_input: dict[str, Any],
        workdir_token: str,
    ) -> AgentHandle:
        entrypoint = agent_version_spec.get("entrypoint")
        if not entrypoint or not isinstance(entrypoint, list):
            raise ValueError("code agent spec must provide 'entrypoint' as an argv list, e.g. ['python3', 'agent.py']")

        cpu = int(resource_limits.get("cpu_seconds", settings.default_cpu_seconds))
        mem = int(resource_limits.get("memory_mb", settings.default_memory_mb))
        timeout = int(resource_limits.get("timeout_seconds", settings.default_timeout_seconds))
        pid_limit = int(resource_limits.get("pid_limit", settings.default_pid_limit))
        output_cap = int(resource_limits.get("output_bytes_limit", settings.default_output_bytes_limit))

        workdir = os.path.join(settings.runtime_workdir_root, workdir_token or uuid.uuid4().hex)
        os.makedirs(workdir, exist_ok=True, mode=0o700)

        env = {
            "PATH": "/usr/bin:/bin",
            "HARNESS_SANDBOX": "1",
            "HOME": workdir,
        }
        env.update({f"AGENT_ENV_{k}": str(v) for k, v in (agent_version_spec.get("env") or {}).items()})

        proc = await asyncio.create_subprocess_exec(
            *entrypoint,
            cwd=agent_version_spec.get("cwd") or workdir,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            preexec_fn=_make_preexec_fn(cpu, mem, pid_limit),
        )
        handle = ProcessAgentHandle(proc, timeout_seconds=timeout, output_cap=output_cap, workdir=workdir)

        if proc.stdin:
            line = json.dumps({"type": "task_input", "input": task_input}) + "\n"
            proc.stdin.write(line.encode("utf-8"))
            await proc.stdin.drain()

        return handle

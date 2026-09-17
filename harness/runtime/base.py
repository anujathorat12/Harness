"""
Runtime interface.

A Runtime is responsible for one thing: executing untrusted agent code (or
interpreting an untrusted declarative spec) in isolation, and surfacing the
actions that agent wants to take back to the harness -- WITHOUT ever
executing those actions itself. The runtime never calls the tool gateway.
Only the orchestrator does, after the policy engine has ruled.

This is the seam that keeps "the agent's own logic" separate from "the
harness's enforcement": a Runtime implementation cannot, by construction,
approve its own agent's actions -- it can only ask.

New runtimes (e.g. a future Kubernetes runtime) implement this interface and
nothing else needs to change.
"""
from __future__ import annotations

import abc
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class RuntimeActionRequest:
    """What the agent is asking permission to do."""
    action_type: str
    resource: str | None
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass
class RuntimeEvent:
    """One item yielded by a running agent. `kind` distinguishes an action
    request (must go through policy before the agent may proceed) from
    terminal events (finished/crashed/timed out)."""
    kind: Literal["action_request", "log", "finished", "error", "timeout", "killed"]
    action: RuntimeActionRequest | None = None
    message: str = ""
    result: Any = None


class AgentHandle(abc.ABC):
    """A running (or paused) instance of an agent inside a sandbox."""

    @abc.abstractmethod
    async def events(self) -> AsyncIterator[RuntimeEvent]:
        """Yields RuntimeEvents. When an "action_request" event is yielded,
        the caller (orchestrator) MUST call resolve_action() with the
        decision before iterating again -- the agent process is blocked on
        that response, which is what prevents it from proceeding without a
        policy decision."""
        raise NotImplementedError

    @abc.abstractmethod
    async def resolve_action(self, allowed: bool, result: Any = None, error: str | None = None) -> None:
        """Send the policy decision (and tool result, if allowed) back to
        the blocked agent so it can continue."""
        raise NotImplementedError

    @abc.abstractmethod
    async def terminate(self, reason: str) -> None:
        """Forcibly kill the sandbox (timeout, resource exhaustion, operator
        request, or task cancellation)."""
        raise NotImplementedError

    @property
    @abc.abstractmethod
    def resource_usage(self) -> dict[str, Any]:
        raise NotImplementedError


class Runtime(abc.ABC):
    kind: str

    @abc.abstractmethod
    async def start(
        self,
        *,
        agent_version_spec: dict[str, Any],
        resource_limits: dict[str, Any],
        task_input: dict[str, Any],
        workdir_token: str,
    ) -> AgentHandle:
        raise NotImplementedError

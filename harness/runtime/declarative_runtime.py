"""
DeclarativeRuntime: the second required agent "shape".

Unlike ProcessRuntime, this runtime executes NO third-party code whatsoever.
The agent "version" is a pure data document (a list of steps, each naming an
action type, a resource, and parameters, optionally templated from the task
input). The harness itself walks this list and emits an action_request event
per step -- exactly the same event stream shape ProcessRuntime produces, so
the orchestrator/policy engine/gateway code downstream is 100% shared and
cannot tell (and does not need to know) which shape produced a given action
request.

Because there's no process to sandbox, the "sandbox" here is simpler but
just as real: the interpreter only knows how to do one thing (walk a
validated list of steps and substitute {{input.*}} placeholders) and has no
eval/exec, no filesystem access of its own, and no way to do anything except
emit the next action_request and wait to be told the result.

Templating: a parameter value that is the exact string "{{input.foo.bar}}"
is replaced with the dotted-path lookup into task_input. This is a
substitution, not code evaluation -- there's no expression language, no
control flow, nothing to inject into.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from harness.runtime.base import AgentHandle, Runtime, RuntimeActionRequest, RuntimeEvent


import re


def _resolve_template(value: Any, task_input: dict[str, Any]) -> Any:
    if isinstance(value, str):
        full_match = re.fullmatch(r"\{\{\s*(input\.[\w.]+)\s*\}\}", value)
        if full_match:
            # Whole string is one placeholder: preserve the underlying type
            # (e.g. a number or dict from task_input), not just a string.
            return _lookup_input_path(full_match.group(1), task_input)

        def _sub(m: "re.Match[str]") -> str:
            resolved = _lookup_input_path(m.group(1), task_input)
            return "" if resolved is None else str(resolved)

        return re.sub(r"\{\{\s*(input\.[\w.]+)\s*\}\}", _sub, value)
    if isinstance(value, dict):
        return {k: _resolve_template(v, task_input) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_template(v, task_input) for v in value]
    return value


def _lookup_input_path(path: str, task_input: dict[str, Any]) -> Any:
    cur: Any = task_input
    for part in path.split(".")[1:]:  # drop leading "input"
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


class DeclarativeAgentHandle(AgentHandle):
    def __init__(self, steps: list[dict[str, Any]], task_input: dict[str, Any]):
        self._steps = steps
        self._task_input = task_input
        self._index = 0
        self._results: list[Any] = []
        self._terminated = False
        self._terminate_reason: str | None = None
        self._resolution_queue: asyncio.Queue = asyncio.Queue(maxsize=1)

    async def events(self) -> AsyncIterator[RuntimeEvent]:
        while self._index < len(self._steps):
            if self._terminated:
                yield RuntimeEvent(kind="killed", message=self._terminate_reason or "terminated")
                return
            step = self._steps[self._index]
            action_type = step.get("action")
            if not action_type or not isinstance(action_type, str):
                yield RuntimeEvent(kind="error", message=f"step {self._index} missing 'action'")
                return
            resource = _resolve_template(step.get("resource"), self._task_input)
            parameters = _resolve_template(step.get("parameters", {}) or {}, self._task_input)
            yield RuntimeEvent(
                kind="action_request",
                action=RuntimeActionRequest(action_type=action_type, resource=resource, parameters=parameters),
            )
            # Block here until resolve_action() pushes the decision, or
            # terminate() is called. This is the interpreter's equivalent of
            # ProcessRuntime's blocked-subprocess-read: the interpreter
            # cannot advance to the next step without an explicit decision.
            get_task = asyncio.ensure_future(self._resolution_queue.get())
            while True:
                done, _ = await asyncio.wait({get_task}, timeout=0.1)
                if self._terminated:
                    if not get_task.done():
                        get_task.cancel()
                    yield RuntimeEvent(kind="killed", message=self._terminate_reason or "terminated")
                    return
                if get_task in done:
                    break
            outcome = get_task.result()
            self._results.append(outcome)
            self._index += 1

        yield RuntimeEvent(kind="finished", result={"step_results": self._results})

    async def resolve_action(self, allowed: bool, result: Any = None, error: str | None = None) -> None:
        await self._resolution_queue.put({"allowed": allowed, "result": result, "error": error})

    async def terminate(self, reason: str) -> None:
        self._terminated = True
        self._terminate_reason = reason

    @property
    def resource_usage(self) -> dict[str, Any]:
        return {"steps_completed": self._index, "steps_total": len(self._steps)}


class DeclarativeRuntime(Runtime):
    kind = "declarative"

    async def start(
        self,
        *,
        agent_version_spec: dict[str, Any],
        resource_limits: dict[str, Any],
        task_input: dict[str, Any],
        workdir_token: str,
    ) -> AgentHandle:
        steps = agent_version_spec.get("steps")
        if not isinstance(steps, list):
            raise ValueError("declarative agent spec must provide 'steps' as a list")
        max_steps = int(resource_limits.get("max_steps", 200))
        if len(steps) > max_steps:
            raise ValueError(f"declarative agent has {len(steps)} steps, exceeding max_steps={max_steps}")
        return DeclarativeAgentHandle(steps, task_input)

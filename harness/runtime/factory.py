from __future__ import annotations

from harness.runtime.base import Runtime
from harness.runtime.declarative_runtime import DeclarativeRuntime
from harness.runtime.process_runtime import ProcessRuntime

_process = ProcessRuntime()
_declarative = DeclarativeRuntime()
_container: Runtime | None = None


def get_runtime(kind: str) -> Runtime:
    global _container
    if kind == "process":
        return _process
    if kind == "declarative":
        return _declarative
    if kind == "container":
        if _container is None:
            from harness.runtime.container_runtime import ContainerRuntime

            _container = ContainerRuntime()
        return _container
    raise ValueError(f"unknown runtime_kind: {kind!r}")

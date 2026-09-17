from __future__ import annotations

import asyncio

from harness.core.config import settings


class ConcurrencyLimiter:
    """One asyncio.Semaphore per agent (fairness / blast-radius containment:
    one agent's backlog can't starve every other agent) plus one global
    semaphore (host resource protection)."""

    def __init__(self) -> None:
        self._global = asyncio.Semaphore(settings.max_concurrent_sessions_total)
        self._per_agent: dict[str, asyncio.Semaphore] = {}

    def _agent_sem(self, agent_id: str) -> asyncio.Semaphore:
        sem = self._per_agent.get(agent_id)
        if sem is None:
            sem = asyncio.Semaphore(settings.max_concurrent_sessions_per_agent)
            self._per_agent[agent_id] = sem
        return sem

    class _Slot:
        def __init__(self, global_sem: asyncio.Semaphore, agent_sem: asyncio.Semaphore):
            self._g = global_sem
            self._a = agent_sem

        async def __aenter__(self) -> "ConcurrencyLimiter._Slot":
            await self._g.acquire()
            try:
                await self._a.acquire()
            except BaseException:
                self._g.release()
                raise
            return self

        async def __aexit__(self, *exc) -> None:
            self._a.release()
            self._g.release()

    def slot(self, agent_id: str) -> "ConcurrencyLimiter._Slot":
        return ConcurrencyLimiter._Slot(self._global, self._agent_sem(agent_id))


limiter = ConcurrencyLimiter()

"""Helpers shared by the job runner and its mixins: the off-loop store call, the per-job slot handle, and the cancel-all helper."""
from __future__ import annotations

import asyncio
from typing import Any
from collections.abc import Callable




async def store_call[T](function: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
    """Run one store (SQLite/DPAPI) operation off the event loop."""
    return await asyncio.to_thread(function, *args, **kwargs)

class JobSlot:
    """One job's hold on an active-job slot; released while the job waits for an approval."""

    def __init__(self, slots: asyncio.Semaphore) -> None:
        self._slots = slots
        self.held = False

    async def acquire(self) -> None:
        if not self.held:
            await self._slots.acquire()
            self.held = True

    def release(self) -> None:
        if self.held:
            self.held = False
            self._slots.release()

async def cancel_all(tasks: Any) -> None:
    pending = [task for task in tasks if not task.done()]
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)

"""Approvals for pending writes in investigation jobs: the one-time code, the approved call, and the waits for approval."""
from __future__ import annotations

from .common import JobSlot, store_call

import asyncio
from datetime import datetime, timedelta
from typing import Any

from mcp.types import CallToolResult

from ..agents.specialists import (
    SpecialistError,
)


class ApprovalMixin:
    """Approval flow of JobRunner (moved out of runner.py)."""

    async def approve_job(self, job_id: str, code: str) -> dict[str, Any]:
        approved = await self._approve_pending_call(job_id, code=code)
        return {"job_id": job_id, "approved": approved}

    async def _approve_pending_call(self, job_id: str, *, code: str) -> bool:
        live = job_id in self._approval_events
        pending = await store_call(self._approve_in_store, job_id, code, live)
        if pending is None:
            return False
        event = self._approval_events.get(job_id)
        task = self._tasks.get(job_id)
        if event is not None and task is not None and not task.done():
            event.set()
        elif task is None or task.done():
            # No investigation is waiting (for example after a restart): run only the stored operation.
            self._tasks[job_id] = asyncio.create_task(
                self._run_approved_write(job_id, pending), name=f"mcp-hub-approved-write-{job_id}",
            )
        return True

    def _approve_in_store(self, job_id: str, code: str, live: bool) -> dict[str, Any] | None:
        """Approve the pending call; return it, or None when it is missing, expired, or the code is wrong.

        A live wait is bounded by its running job; a wait restored after a restart expires
        once the job window since creation has passed.
        """
        pending = self.store.get_pending_call(job_id, include_arguments=True)
        if pending is None:
            return None
        if not live and self.store.expire_awaiting_approval(self._job_window, job_id=job_id):
            return None
        if not self.store.approve_pending_call(job_id, code):
            return None
        return pending

    async def _run_approved_write(self, job_id: str, expected: dict[str, Any]) -> None:
        slot = JobSlot(self._slots)
        try:
            await slot.acquire()
            tool_name, arguments = await store_call(self.store.consume_approved_call, job_id)
            if tool_name != expected["tool_name"] or arguments != expected["arguments"]:
                raise SpecialistError("Approved operation no longer matches its stored request")
            result = await self.runtime.call_child_tool(
                tool_name,
                arguments,
                request_id=f"job:{job_id}",
                client_name="investigation",
                agent_role=expected.get("agent"),
            )
            await store_call(self._finish_approved_write, job_id, expected.get("agent"), tool_name, result)
        except asyncio.CancelledError:
            # The store write runs on a worker thread; shield keeps it going if the task is cancelled again.
            await asyncio.shield(store_call(self._settle_interrupted, job_id))
            raise
        except Exception as exc:
            await store_call(self._fail_approved_write, job_id, expected, exc)
        finally:
            slot.release()
            self._tasks.pop(job_id, None)
            self._approval_events.pop(job_id, None)
            self._approval_codes.pop(job_id, None)

    def _finish_approved_write(self, job_id: str, agent: str | None, tool_name: str, result: CallToolResult) -> None:
        source_id = "approved-write"
        self.store.append_event(
            job_id, "tool.call.failed" if result.is_error else "tool.call.succeeded",
            agent=agent, tool=tool_name, source_id=source_id,
        )
        self.store.save_encrypted_result(job_id, source_id, result.model_dump(mode="json"))
        outcome = "The approved write returned an error." if result.is_error else "The approved write completed."
        self.store.save_summary(job_id, f"{outcome} The investigation did not resume after the Hub restarted.")
        if self.store.get_job(job_id)["state"] == "running":
            self.store.transition_job(job_id, "partial", event="job.partial")

    def _fail_approved_write(self, job_id: str, expected: dict[str, Any], exc: Exception) -> None:
        self.store.append_event(job_id, "approved_write.failed", agent=expected.get("agent"), tool=expected.get("tool_name"))
        if self.store.get_job(job_id)["state"] == "running":
            self.store.save_summary(job_id, f"{type(exc).__name__}: approved write could not be completed.")
            self.store.transition_job(job_id, "partial", event="job.partial")

    def watch_pending_approvals(self) -> int:
        """At service start, settle expired approvals and time out the younger ones.

        MCP one-time codes were held in memory and do not survive the restart. Returns how many still wait.
        """
        self.store.expire_awaiting_approval(self._job_window)
        waiting = self.store.list_job_ids_by_state("awaiting_approval")
        now = self.store.clock()
        for job_id in waiting:
            created = datetime.fromisoformat(self.store.get_job(job_id)["created_at"])
            delay = max(0.0, (created + self._job_window - now).total_seconds()) + _EXPIRY_MARGIN_SECONDS
            timer = asyncio.create_task(self._expire_approval_later(job_id, delay), name=f"mcp-hub-approval-{job_id}")
            self._timers.add(timer)
            timer.add_done_callback(self._timers.discard)
        return len(waiting)

    async def _expire_approval_later(self, job_id: str, delay: float) -> None:
        await asyncio.sleep(delay)
        await store_call(self.store.expire_awaiting_approval, self._job_window, job_id=job_id)

    async def _await_approval(self, job_id: str, role: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Hold a write until its one-time code approves exactly this call; return the approved arguments.

        A job has one pending write at a time, and it gives up its active slot while it waits.
        """
        async with self._approval_locks.setdefault(job_id, asyncio.Lock()):
            # Registered before the pending call exists, so an approval can never miss this wait.
            event = asyncio.Event()
            self._approval_events[job_id] = event
            try:
                ttl = timedelta(minutes=self.limits.approval_code_minutes)
                code = await store_call(self.store.create_pending_call, job_id, name, arguments, ttl=ttl, agent=role)
                self._approval_codes[job_id] = code
                await store_call(self.store.append_event, job_id, "approval.code_issued", agent=role, tool=name)
                await self._wait_without_slot(job_id, event)
            finally:
                if self._approval_events.get(job_id) is event:
                    del self._approval_events[job_id]
            return await self._consume_approval(job_id, name, arguments)

    async def _wait_without_slot(self, job_id: str, event: asyncio.Event) -> None:
        slot = self._job_slots.get(job_id)
        if slot is not None:
            slot.release()
        await event.wait()
        if slot is not None:
            await slot.acquire()

    async def _consume_approval(self, job_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if (await store_call(self.store.get_job, job_id))["state"] == "cancelled":
            raise asyncio.CancelledError
        try:
            approved_name, approved_args = await store_call(self.store.consume_approved_call, job_id)
        except KeyError:
            raise SpecialistError("Pending write approval is no longer available") from None
        if approved_name != name or approved_args != arguments:
            raise SpecialistError("Approved operation does not match the pending tool call")
        return approved_args


_EXPIRY_MARGIN_SECONDS = 0.25

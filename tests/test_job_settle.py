"""A running job whose task is cancelled (shutdown) ends `interrupted`, and the settle step runs off the event loop
(research.md R8, FR-012). The fixture is the one the hardening tests use."""
from __future__ import annotations

import asyncio
import threading
import time
import unittest

from tests.helpers.jobs import _Runtime, _runner


class SettleInterruptedTest(unittest.TestCase):
    def test_a_running_job_whose_task_is_cancelled_ends_interrupted(self) -> None:
        with _runner(_Runtime([]), {}) as runner:
            async def hang(job_id: str) -> None:
                await asyncio.sleep(30)

            runner._execute_job = hang  # the job is running, then it waits until something cancels it

            async def exercise() -> str:
                job_id = (await runner.submit_issue(project="orders", question="Q", env="dev"))["job_id"]
                task = runner._tasks[job_id]
                deadline = time.monotonic() + 5
                while runner.store.get_job(job_id)["state"] != "running":
                    if time.monotonic() > deadline:
                        self.fail("the job never reached running")
                    await asyncio.sleep(0.01)
                task.cancel()  # what shutdown does: cancel the task while the job is still running
                await asyncio.gather(task, return_exceptions=True)
                return job_id

            job_id = asyncio.run(exercise())
            self.assertEqual(runner.store.get_job(job_id)["state"], "interrupted")
            self.assertNotIn(job_id, runner._tasks)

    def test_the_settle_step_does_not_run_on_the_event_loop_thread(self) -> None:
        with _runner(_Runtime([]), {}) as runner:
            loop_thread = threading.get_ident()
            seen: list[int] = []
            original = runner._settle_interrupted

            def record(job_id: str) -> None:
                seen.append(threading.get_ident())
                original(job_id)

            runner._settle_interrupted = record

            async def hang(job_id: str) -> None:
                await asyncio.sleep(30)

            runner._execute_job = hang

            async def exercise() -> None:
                job_id = (await runner.submit_issue(project="orders", question="Q", env="dev"))["job_id"]
                task = runner._tasks[job_id]
                deadline = time.monotonic() + 5
                while runner.store.get_job(job_id)["state"] != "running":
                    if time.monotonic() > deadline:
                        self.fail("the job never reached running")
                    await asyncio.sleep(0.01)
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

            asyncio.run(exercise())
            self.assertEqual(len(seen), 1)
            self.assertNotEqual(seen[0], loop_thread)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest

from mcp_hub.activity_ui.tracker import ActivityTracker, ToolCallRecord


class ActivityTrackerTest(unittest.TestCase):
    def test_begin_and_finish_update_state_events_and_return_a_record(self) -> None:
        tracker = ActivityTracker()
        call = tracker.begin("child", "inspect", "Codex", "logs")
        running = tracker.snapshot(["child"], {"child": 2})["children"]["child"]
        self.assertEqual((running["active_calls"], running["last_state"], running["tool_count"]), (1, "running", 2))
        self.assertEqual(running["active_call_details"], [{"call_id": call.call_id, "tool": "inspect"}])

        record = tracker.finish(call, "failed", "ToolError", 15)
        snapshot = tracker.snapshot(["child", "idle"], {})
        child = snapshot["children"]["child"]
        self.assertEqual((child["active_calls"], child["last_state"], child["last_error_type"]), (0, "failed", "ToolError"))
        self.assertEqual(child["last_duration_ms"], 15)
        self.assertIsNone(snapshot["children"]["idle"]["last_tool"])
        self.assertEqual([event["state"] for event in snapshot["events"]], ["started", "failed"])
        self.assertEqual(snapshot["sequence"], 2)
        self.assertIsInstance(record, ToolCallRecord)
        self.assertEqual(
            (record.server_id, record.tool, record.client, record.agent_role, record.state, record.error_type),
            ("child", "inspect", "Codex", "logs", "failed", "ToolError"),
        )
        self.assertEqual(record.started_at, call.started_at)
        self.assertLessEqual(record.started_at, record.finished_at)

    def test_negative_duration_is_clamped_in_the_record(self) -> None:
        tracker = ActivityTracker()
        record = tracker.finish(tracker.begin("child", "inspect", "Codex", None), "succeeded", None, -5)
        self.assertEqual(record.duration_ms, 0)
        self.assertIsNone(record.error_type)

    def test_history_is_bounded_and_full_subscriber_is_told_to_resync(self) -> None:
        tracker = ActivityTracker(history=3, queue_size=2)
        subscriber = tracker.subscribe()
        for call_id in range(3):
            tracker.append_event("child", "inspect", "started", call_id=call_id)
        self.assertEqual(len(tracker.snapshot([], {})["events"]), 3)
        self.assertEqual(subscriber.qsize(), 1)
        self.assertEqual(subscriber.get_nowait(), {"resync": True})

        tracker.unsubscribe(subscriber)
        tracker.append_event("child", "inspect", "started", call_id=9)
        self.assertTrue(subscriber.empty())
        self.assertEqual([event["call_id"] for event in tracker.snapshot([], {})["events"]], [1, 2, 9])


if __name__ == "__main__":
    unittest.main()

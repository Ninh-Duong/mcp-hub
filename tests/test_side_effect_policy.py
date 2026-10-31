from __future__ import annotations

import json
import unittest
from pathlib import Path


class SideEffectPolicyTest(unittest.TestCase):
    def test_allows_only_documented_encrypted_local_cache_writes_automatically(self) -> None:
        from mcp_hub.jobs.policy import SideEffectPolicy

        policy = SideEffectPolicy()
        self.assertFalse(policy.requires_approval("jira__sync_ticket", read_only_hint=False))
        self.assertFalse(policy.requires_approval("pr__sync_my_open_prs", read_only_hint=False))
        self.assertFalse(policy.requires_approval("pr__get_pr_context_pack", read_only_hint=False))
        self.assertTrue(policy.requires_approval("logs__export_openobserve_logs", read_only_hint=False))
        self.assertTrue(policy.requires_approval("unknown__write", read_only_hint=False))

    def test_forced_jira_sync_waits_for_approval(self) -> None:
        from mcp_hub.jobs.policy import SideEffectPolicy

        policy = SideEffectPolicy()
        for arguments in (None, {"issue_key": "EX-1"}, {"issue_key": "EX-1", "force": False}):
            with self.subTest(arguments=arguments):
                self.assertFalse(policy.requires_approval("jira__sync_ticket", read_only_hint=False, arguments=arguments))
        for force in (True, "true", 1):
            with self.subTest(force=force):
                self.assertTrue(policy.requires_approval(
                    "jira__sync_ticket", read_only_hint=False, arguments={"issue_key": "EX-1", "force": force},
                ))
        self.assertFalse(policy.requires_approval("pr__sync_my_open_prs", read_only_hint=False, arguments={"force": True}))
        # A child that marks the sync read-only cannot lift the forced-sync gate.
        self.assertTrue(policy.requires_approval("jira__sync_ticket", read_only_hint=True, arguments={"force": True}))

    def test_never_gates_read_only_tools(self) -> None:
        from mcp_hub.jobs.policy import SideEffectPolicy

        self.assertFalse(SideEffectPolicy().requires_approval("jira__read_ticket", read_only_hint=True))

    def test_cache_write_exemptions_match_exposed_tool_names(self) -> None:
        from mcp_hub.jobs.policy import AUTOMATIC_ENCRYPTED_CACHE_WRITES

        hub = json.loads((Path(__file__).resolve().parents[1] / "config" / "hub.example.json").read_text(encoding="utf-8"))
        exposed = {tool["exposeAs"] for feature in hub["features"].values() for tool in feature["tools"]}
        self.assertLessEqual(AUTOMATIC_ENCRYPTED_CACHE_WRITES, exposed)


if __name__ == "__main__":
    unittest.main()

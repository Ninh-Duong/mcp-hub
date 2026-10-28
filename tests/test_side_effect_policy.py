from __future__ import annotations

import unittest


class SideEffectPolicyTest(unittest.TestCase):
    def test_allows_only_documented_encrypted_local_cache_writes_automatically(self) -> None:
        from mcp_hub.jobs.policy import SideEffectPolicy

        policy = SideEffectPolicy()
        self.assertFalse(policy.requires_approval("jira__jira_sync_ticket", read_only_hint=False))
        self.assertFalse(policy.requires_approval("pr__sync_my_open_prs", read_only_hint=False))
        self.assertFalse(policy.requires_approval("pr__get_pr_context_pack", read_only_hint=False))
        self.assertTrue(policy.requires_approval("logs__export_openobserve_logs", read_only_hint=False))
        self.assertTrue(policy.requires_approval("unknown__write", read_only_hint=False))

    def test_never_gates_read_only_tools(self) -> None:
        from mcp_hub.jobs.policy import SideEffectPolicy

        self.assertFalse(SideEffectPolicy().requires_approval("jira__jira_read_ticket", read_only_hint=True))


if __name__ == "__main__":
    unittest.main()

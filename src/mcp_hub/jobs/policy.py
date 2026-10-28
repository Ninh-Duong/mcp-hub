from __future__ import annotations


# These tools only refresh documented encrypted local caches as part of a read workflow.
AUTOMATIC_ENCRYPTED_CACHE_WRITES = frozenset({
    "jira__sync_ticket",
    "pr__sync_my_open_prs",
    "pr__get_pr_context_pack",
})


class SideEffectPolicy:
    def requires_approval(self, tool_name: str, *, read_only_hint: bool | None) -> bool:
        if read_only_hint is True:
            return False
        if tool_name in AUTOMATIC_ENCRYPTED_CACHE_WRITES:
            return False
        return True

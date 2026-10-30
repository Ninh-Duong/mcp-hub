from __future__ import annotations

from typing import Any


# These tools only refresh documented encrypted local caches as part of a read workflow.
AUTOMATIC_ENCRYPTED_CACHE_WRITES = frozenset({
    "jira__sync_ticket",
    "pr__sync_my_open_prs",
    "pr__get_pr_context_pack",
})

# Cache refreshes that are automatic only as an incremental refresh: the child skips the
# upstream fetch when nothing changed. A forced full re-download (`force=true`) is not a
# routine read step, so it waits for the user's approval like any other write.
FORCE_REQUIRES_APPROVAL = frozenset({"jira__sync_ticket"})


class SideEffectPolicy:
    def requires_approval(
        self, tool_name: str, *, read_only_hint: bool | None, arguments: dict[str, Any] | None = None,
    ) -> bool:
        if read_only_hint is True:
            return False
        if tool_name in FORCE_REQUIRES_APPROVAL and (arguments or {}).get("force", False) is not False:
            return True
        if tool_name in AUTOMATIC_ENCRYPTED_CACHE_WRITES:
            return False
        return True

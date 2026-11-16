from __future__ import annotations

import json
from typing import Any

from ..settings import BitnetSettings
from .client import BitnetClient

_SYSTEM_PROMPT = (
    "Summarize these findings for an operator in at most 8 bullet points. Cite [source_id]. Do not invent."
)


class BitnetDraftSummarizer:
    """Generates an offline draft summary using local BitNet when coordinator is unavailable."""

    def __init__(self, client: BitnetClient, settings: BitnetSettings | None = None) -> None:
        self.client = client
        self.settings = settings or BitnetSettings()

    async def summarize(self, question: str, findings: list[dict[str, Any]]) -> str | None:
        findings_json = json.dumps(findings, indent=2, ensure_ascii=False)
        user_text = f"Question: {question}\n\nFindings:\n{findings_json}"
        limit = self.settings.summary_input_chars
        if len(user_text) > limit:
            user_text = user_text[:limit]
        text = await self.client.complete_text(
            _SYSTEM_PROMPT, user_text,
            max_tokens=self.settings.summary_output_tokens, timeout=self.settings.summary_timeout_seconds,
        )
        if not text or not text.strip():
            return None
        return f"Draft (local model):\n{text.strip()}"

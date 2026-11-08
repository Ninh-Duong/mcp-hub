from __future__ import annotations

import json
from typing import Any

from .client import BitnetClient

_SYSTEM_PROMPT = (
    "Summarize these findings for an operator in at most 8 bullet points. Cite [source_id]. Do not invent."
)


class BitnetDraftSummarizer:
    """Generates an offline draft summary using local BitNet when coordinator is unavailable."""

    def __init__(self, client: BitnetClient) -> None:
        self.client = client

    async def summarize(self, question: str, findings: list[dict[str, Any]]) -> str | None:
        findings_json = json.dumps(findings, indent=2, ensure_ascii=False)
        user_text = f"Question: {question}\n\nFindings:\n{findings_json}"
        if len(user_text) > 10000:
            user_text = user_text[:10000]
        text = await self.client.complete_text(_SYSTEM_PROMPT, user_text, max_tokens=400, timeout=30.0)
        if not text or not text.strip():
            return None
        return f"Draft (local model):\n{text.strip()}"

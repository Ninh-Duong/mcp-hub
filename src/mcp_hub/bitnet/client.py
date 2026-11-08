from __future__ import annotations

import json
from typing import Any

import httpx


def _extract_first_json(text: str) -> dict[str, Any] | None:
    trimmed = text.strip()
    if trimmed.startswith("{") and trimmed.endswith("}"):
        try:
            obj = json.loads(trimmed)
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass

    start = text.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escape = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
            else:
                if ch == '"':
                    in_string = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            obj = json.loads(text[start : i + 1])
                            if isinstance(obj, dict):
                                return obj
                        except Exception:
                            pass
                        break
        start = text.find("{", start + 1)
    return None


class BitnetClient:
    def __init__(self, port: int, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.port = port
        self.base_url = f"http://127.0.0.1:{port}"
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(base_url=self.base_url, transport=self._transport)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def complete_text(
        self, system: str, user: str, *, max_tokens: int = 512, timeout: float = 20.0,
    ) -> str | None:
        payload = {
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0,
            "max_tokens": max_tokens,
        }
        try:
            client = await self._get_client()
            resp = await client.post("/v1/chat/completions", json=payload, timeout=timeout)
            if resp.status_code != 200:
                return None
            data = resp.json()
            choices = data.get("choices")
            if choices and isinstance(choices, list):
                content = choices[0].get("message", {}).get("content")
                if isinstance(content, str):
                    return content
            return None
        except Exception:
            return None

    async def complete_json(
        self, system: str, user: str, *, max_tokens: int = 256, timeout: float = 3.0,
    ) -> dict[str, Any] | None:
        """POST http://127.0.0.1:{port}/v1/chat/completions (temperature 0).

        Return the first JSON object found in the reply text, or None on any HTTP, timeout,
        or parse error. Never raises.
        """
        text = await self.complete_text(system, user, max_tokens=max_tokens, timeout=timeout)
        if not text:
            return None
        return _extract_first_json(text)

    async def healthy(self, timeout: float = 1.0) -> bool:
        """GET /health -> 200. Never raises."""
        try:
            client = await self._get_client()
            resp = await client.get("/health", timeout=timeout)
            return resp.status_code == 200
        except Exception:
            return False

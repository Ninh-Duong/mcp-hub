"""Shared HTTP-style fakes: a response that reads like an opened URL (tests/helpers)."""
from __future__ import annotations

import io


class BytesResponse(io.BytesIO):
    def __enter__(self) -> BytesResponse:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

"""Shared test fakes (desktop). Test modules import them from here instead of from each other."""
from __future__ import annotations

from __future__ import annotations
import json
from pathlib import Path


TOKEN = "t" * 48


class FakeOpener:
    """Answers by URL path; a value that is an exception is raised instead."""

    def __init__(self, answers: dict[str, object]) -> None:
        self.answers = answers
        self.requests: list[tuple[str, str, str | None, float]] = []

    def __call__(self, request, timeout: float):
        path = request.full_url.split("8765", 1)[1]
        self.requests.append((request.get_method(), path, request.get_header("Authorization"), timeout))
        answer = self.answers[path]
        if isinstance(answer, Exception):
            raise answer
        return FakeResponse(answer)


def event(sequence=1, stream_id="hub-one", **values):
    return dict(stream_id=stream_id, sequence=sequence, at="2026-11-12T03:00:00+00:00",
                operation="plan", state="started", source="bitnet", **values)


def snapshot(sequence=1, stream_id="hub-one", events=None):
    return dict(stream_id=stream_id, sequence=sequence, events=events if events is not None else [event(sequence, stream_id)])


def write(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


class FakeResponse:
    def __init__(self, value: object) -> None:
        self.body = json.dumps(value).encode("utf-8")

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body

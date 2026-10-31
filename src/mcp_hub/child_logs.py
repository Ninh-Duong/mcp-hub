"""Size-capped stderr logs for child MCP processes (`logs/children/<server-id>.log`).

A child's stderr is a pipe drained by a daemon thread into a `CappedLog`, so the
child process writes without blocking and the event loop never touches the file.
Child stderr may contain the child's own secrets: only the user inspects these files.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import BinaryIO, TextIO

DEFAULT_MAX_BYTES = 5 * 1024 * 1024
_CHUNK_BYTES = 64 * 1024


class CappedLog:
    """Thread-safe append-only log file rotated to `<name>.1` before it would exceed `max_bytes`.

    The file is opened lazily on the first write, from the writing thread, and a file
    already over the cap at open is rotated first. Writes after `close` are dropped.
    """

    def __init__(self, path: Path, max_bytes: int = DEFAULT_MAX_BYTES) -> None:
        self.path = path
        self.max_bytes = max_bytes
        self._lock = threading.Lock()
        self._file: BinaryIO | None = None
        self._size = 0
        self._closed = False

    def write(self, data: bytes) -> None:
        with self._lock:
            if self._closed or not data:
                return
            if self._file is None:
                self._open()
            data = data[-self.max_bytes:]
            if self._size and self._size + len(data) > self.max_bytes:
                self._rotate()
            assert self._file is not None
            self._file.write(data)
            self._file.flush()
            self._size += len(data)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            if self._file is not None:
                self._file.close()
                self._file = None

    def _open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.is_file() and self.path.stat().st_size > self.max_bytes:
            os.replace(self.path, self.path.with_name(self.path.name + ".1"))
        self._file = open(self.path, "ab")
        self._size = self._file.tell()

    def _rotate(self) -> None:
        assert self._file is not None
        self._file.close()
        os.replace(self.path, self.path.with_name(self.path.name + ".1"))
        self._file = open(self.path, "ab")
        self._size = 0


def pipe_into(log: CappedLog, name: str) -> TextIO:
    """Return the write end of a new pipe whose output a daemon thread appends to `log`.

    Pass it as a child's stderr and close it once the child is spawned or gone; the
    thread ends when every copy of the write end (the child's included) is closed.
    """
    read_fd, write_fd = os.pipe()
    threading.Thread(target=_pump, args=(read_fd, log), name=f"mcp-hub-stderr-{name}", daemon=True).start()
    return os.fdopen(write_fd, "w", encoding="utf-8")


def _pump(read_fd: int, log: CappedLog) -> None:
    with os.fdopen(read_fd, "rb", buffering=0) as stream:
        while chunk := stream.read(_CHUNK_BYTES):
            try:
                log.write(chunk)
            except OSError:
                pass  # keep draining: a child blocked on a full stderr pipe would stall

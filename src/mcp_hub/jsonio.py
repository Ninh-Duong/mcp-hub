"""JSON files: one reader (UTF-8, a byte-order mark is accepted) and one atomic writer.

Leaf module: it imports nothing from mcp_hub, so every layer can use it. The desktop app keeps its own file I/O
because it may not import mcp_hub (docs/desktop-app.md).
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def read_json(path: Path) -> Any:
    """Parse the JSON file at `path`. A UTF-8 byte-order mark is accepted, as Notepad and Windows PowerShell write it."""
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json_atomic(
    path: Path,
    value: Any,
    *,
    indent: int | None = None,
    separators: tuple[str, str] | None = None,
    sort_keys: bool = False,
    trailing_newline: bool = False,
    mode: int | None = None,
) -> None:
    """Replace `path` with `value` as JSON: write a temporary file in the same folder, then `os.replace` it.

    A failure before the replace leaves the old file in place. `mode` sets the permission bits of the new file (for
    example 0o600 for a file that holds a token). `chmod` is best effort: it has little effect on Windows, and a
    failure to change permissions does not stop the write.
    """
    text = json.dumps(value, indent=indent, separators=separators, sort_keys=sort_keys)
    if trailing_newline:
        text += "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
        if mode is not None:
            _set_mode_best_effort(temporary, mode)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _set_mode_best_effort(path: str, mode: int) -> None:
    try:
        os.chmod(path, mode)
    except OSError:
        pass

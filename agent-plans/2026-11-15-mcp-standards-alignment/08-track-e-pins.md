# Track E: tool definition pins

Status: ready
Branch: `arch/track-e` (from `arch/base`). Worktree: `../mcp-hub-track-e` (sibling of the Hub checkout).

Owns:
- `src/mcp_hub/tool_pins.py` (new)
- `tests/test_tool_pins.py` (new)

This track is standalone. It does not touch `server.py`; Phase 2 calls it from `_register_routes`. Also do not touch `config.py`.

## T50 `fingerprint` (done)

```python
def fingerprint(tool: Tool) -> str:
```

Build a dict with exactly these keys, using the camelCase wire names: `name`, `description`, `inputSchema`, `outputSchema`, `annotations`. Take the values from `tool.model_dump(by_alias=True, mode="json")` (verify the field aliases in the SDK's `Tool`). Missing values are `None`. Serialize with `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`, encode UTF-8, and return the SHA-256 hex digest.

Tests: the same tool built with keys in a different order gives the same digest; changing the description, the input schema, or the annotations changes it; the `title`, `icons` and `meta` fields do not affect it.

## T51 `description_findings` (done)

```python
def description_findings(text: str, max_chars: int) -> list[str]:
```

Returns a list of finding codes; an empty list means clean. Codes:
- `"tag-characters"`: any code point in U+E0000 to U+E007F.
- `"bidi-control"`: any code point in U+202A to U+202E or U+2066 to U+2069.
- `"zero-width"`: any code point in U+200B to U+200D, U+2060, or U+FEFF.
- `"too-long"`: `len(text) > max_chars`.

Tests: one case per code; clean text gives `[]`; a text with two kinds gives both codes, in the order above.

## T52 `PinStore` (done)

```python
@dataclass(frozen=True)
class PinVerdict:
    new: tuple[str, ...]
    same: tuple[str, ...]
    changed: tuple[str, ...]

class PinStore:
    def __init__(self, path: Path) -> None: ...
    def load(self) -> None: ...                         # missing file: empty; unreadable or corrupt: empty and self.corrupt = True
    def check(self, server_id: str, tools: Iterable[Tool]) -> PinVerdict: ...
    def accept(self, server_id: str, tool: Tool) -> None: ...
    def save(self) -> None: ...                         # synchronous; the caller uses asyncio.to_thread
```

Behaviour:
- The file holds only `{"version": 1, "servers": {server_id: {tool_name: sha256_hex}}}`. No descriptions, no schemas, no values.
- `check` pins a tool it has never seen (adds it to the store in memory, reported under `new`). A known tool with the same fingerprint is `same`. A known tool with a different fingerprint is `changed`, and its stored hash is left alone until `accept`.
- Tools the server no longer lists are removed from that server's entry on `check`.
- `save` writes to a temporary file in the same directory and then `os.replace`s it, so a crash never leaves half a file.

Tests: patch the store path to a temporary directory (never write into `config/private/`); a round trip through `save` and `load`; a changed tool is reported until `accept`; a corrupt file loads as empty with `corrupt` set; the saved file never contains the description text (assert the substring is absent).

## Verify

```
cd "../mcp-hub-track-e"
uv run --no-sync python -m unittest tests.test_tool_pins -v
uv run --no-sync python -m ruff check .
uv run --no-sync python scripts/check_code_standards.py
uv run --no-sync python scripts/package_qc.py
```

## Stop and ask

- `Tool.model_dump(by_alias=True)` does not produce the camelCase names listed above.

## Progress log

- T50 done: `fingerprint` in `tool_pins.py` (SHA-256 over canonical wire fields); 8 tests in `tests/test_tool_pins.py`; SDK `Tool.model_dump(by_alias=True)` gives camelCase keys.
- T51 done: `description_findings` in `tool_pins.py` (tag-characters, bidi-control, zero-width, too-long, in that order); 7 tests added to `tests/test_tool_pins.py`.
- T52 done: `PinVerdict` and `PinStore` in `tool_pins.py` (load, check, accept, atomic save; corrupt files load empty); 9 tests added, 24 in total in `tests/test_tool_pins.py`.

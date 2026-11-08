# Track A: rules + plan assistant

Status: approved · Branch: `feat/bitnet-planning` (worktree `../mcp-hub-a`, from `master` after track 0 is merged) · Parallel with B and C · Read [00-overview.md](00-overview.md) first.

Goal: implement `PlanAssistant` (overview 7.3). Deterministic rules come first; BitNet fills only what is left.

## Owns

- `src/mcp_hub/bitnet/rules.py` (new)
- `src/mcp_hub/bitnet/planner.py` (new)
- `tests/test_bitnet_rules.py`, `tests/test_bitnet_planner.py` (new)

Use, but do not change: `mcp_hub.extensions` (`PlanContext`, `PlanHints`), `mcp_hub.bitnet.client.BitnetClient`.

## Tasks

### [ ] T101 `rules.py`, the deterministic parser
```python
@dataclass(frozen=True)
class RuleResult:
    hints: PlanHints                 # source="rules"
    unresolved: frozenset[str]       # subset of {"playbook", "servers", "env", "window"}

def parse_rules(context: PlanContext) -> RuleResult: ...
```
Keep the patterns as module constants. **Vietnamese:** `scripts/check_code_standards.py` rejects Vietnamese letters in tracked files, outside `dashboard/locales/vi.json`. So all patterns are plain ASCII, matched against a folded copy of the task:
```python
def fold(text: str) -> str:
    """Casefold and strip diacritics (for example 'H\u00f4m nay' -> 'hom nay'); keeps offsets irrelevant (match-only)."""
    text = text.replace("\u0111", "d").replace("\u0110", "D")
    return "".join(c for c in unicodedata.normalize("NFD", text) if not unicodedata.combining(c)).casefold()
```
Tests that need accented input write it with `\u` escapes, for example `"h\u00f4m nay"`.

- **Env** (only if the env is in `context.envs`; when `envs` is empty, use the canonical set `dev`, `stg`, `prod`):
  - `prod`: `production|prod|live|moi truong that`;
  - `stg`: `staging|stage|stg|uat`;
  - `dev`: `development|dev`.
  - If two different envs match, leave env unresolved.
- **Window** (from `context.now`, UTC):
  - `today|hom nay|24h|24 gio|last 24 hours|trong ngay` → `[now-24h, now]`;
  - `yesterday|hom qua` → `[start of yesterday UTC, start of today UTC]`;
  - `(\d+)\s*(h|gio|hours?)` → `[now-Nh, now]`;
  - `(\d+)\s*(ngay|days?)` → `[now-N days, now]`;
  - `this week|tuan nay` → `[now-7d, now]`;
  - a match that resolves to more than 168 h stays unresolved.
- **Playbook** (only ids present in `context.playbooks`):
  - a GUID, or `rcid|request id|correlation id` followed by a token → `rcid-bug`;
  - a Jira key `\b[A-Z][A-Z0-9]+-\d+\b` (on the original text) → `jira-ticket-bug`;
  - a pull request URL (`/pull-requests/\d+` or `/pull/\d+`) → `pr-review`;
  - otherwise unresolved.
- **Servers** (only connected ids in `context.servers`; map by keyword to the server whose id contains the fragment):
  - `log|logs|loi|error|exception|500` → `logs`;
  - `sql|database|db|table|bang|du lieu|query` → `sql`;
  - `jira|ticket` → `jira`;
  - `pr|pull request` → `pr`;
  - `source|code|ma nguon` → `source`.
  - Resolve the fragment to the server id with `next(s for s in context.servers if fragment in s)`. Put any id the fragment does not match into `unresolved`.
- **Tests** (table-driven, `now = 2026-11-08T10:00:00Z`):

| Input Expected |
|---|---|
| "check production hom nay" | env `prod`, window `[2026-11-07T10:00Z, 2026-11-08T10:00Z]` |
| "loi sql orders-api dev hom qua" | env `dev`, servers ⊇ the sql and logs server ids, window = yesterday UTC |
| "compare dev and prod" | env unresolved |
| "last 6h errors" | 6 h window |
| "last 30 days" | window unresolved (over 168 h) |
| "ABC-123 crash" | playbook `jira-ticket-bug` |
| "hello" | everything unresolved |
| `"check production h\u00f4m nay"` (accented, escaped) | same result as the unaccented form |
| an env not in `context.envs` | dropped |

### [ ] T102 `planner.py`, rules first, then BitNet
```python
class BitnetPlanAssistant:  # implements extensions.PlanAssistant
    def __init__(self, client: BitnetClient, *, timeout: float) -> None: ...
    async def hints(self, context: PlanContext) -> PlanHints None: ...
```
1. `result = parse_rules(context)`. If `result.unresolved` is empty, return `result.hints`.
2. Otherwise build the prompt. Keep it a module constant and send the task as the **user** message:
   - System: "You map an operator request to a JSON object. Reply with one JSON object only, no prose. Keys: playbook (one of {ids} or null), servers (array, subset of {servers}), env (one of {envs} or null), window_hours (integer 1-168 or null). Use null when unsure."
   - Ask only for the unresolved keys, and list them in the system message.
3. Call `client.complete_json(system, user, max_tokens=128, timeout=self.timeout)`.
4. Validate strictly:
   - an unknown key, a wrong type, or an out-of-enum value drops **that key**;
   - `window_hours` becomes `[now - h, now]`.
5. Merge. Rule values always win. If the model filled at least one key, use `source="bitnet"`; otherwise return the rule hints (`source="rules"`), or `None` when the rules found nothing either.
6. Never raise: catch everything and return the rule hints or `None`.
- Tests (with a fake client):
  - resolved by the rules → the client is never called;
  - the model fills env → `source="bitnet"`;
  - the model returns an out-of-enum env → dropped;
  - the model returns `None` → rule hints;
  - the client raises → rule hints;
  - `window_hours=500` → dropped;
  - a rule value is not overwritten by the model.

### [ ] T103 Quality pass
- Each function is at most 50 lines, and the pattern tables are constants.
- Coverage of both new modules is at least 90 %.
- `uv run --no-sync python scripts/package_qc.py` passes.

## Acceptance for track A
- SC-002 holds: the rules alone resolve "check production hom nay".
- No module outside `src/mcp_hub/bitnet/` was changed.

## Blocked

- (none)

## Progress log

- 2026-11-08 Claude (Opus): track written. Next: T101.

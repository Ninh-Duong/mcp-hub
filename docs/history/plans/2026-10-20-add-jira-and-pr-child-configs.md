# Add Jira Sync and PR Companion Child Configs Implementation Plan

> **Superseded.** Automatic sibling discovery replaced the enable flags, and the templates now use empty account values. The current behavior is in `docs/mcps/` and `config/mcps/*.example.json`; do not execute this plan.

> **For agentic workers:** Use `subagent-driven-development` or `executing-plans` to implement this plan task by task.

**Goal:** Add disabled private Hub configurations for Jira Sync and PR Companion, keeping their launch settings, tool aliases, and documentation aligned with the existing child repos.

**Architecture:** Reuse the Hub's existing templates, contracts, and read-only smoke checks. Merge only the two server entries and their `jira` / `pr` feature entries into the private Hub manifest, preserving the enabled logs child. Correct one stale PR integration instruction that conflicts with the current Hub rules.

**Tech Stack:** JSON configuration, PowerShell, Python 3.12 with `uv`, Node.js 20.12+ for PR Companion.

**Spec:** `docs/adding-a-child-mcp.md`, `docs/child-mcp-compatibility.md`, `../mcp-jira-sync/docs/PARENT_MCP_GUIDE.md`, and `../mcp-pr-companion/docs/HUB_INTEGRATION.md`.

## Global Constraints

- Use `mcp-jira-sync` and `mcp-pr-companion`; the Hub runbook requires server IDs to match child repository names. Do not register the misspelled `mcp-pr-compaion` ID.
- Keep each new server and feature disabled until its child credentials are filled and the user confirms activation.
- Create `config/private/mcps/mcp-jira-sync.json` and `config/private/mcps/mcp-pr-companion.json` by copying their tracked examples. After creation, never open or print them; inspect or change them only with `scripts/private_config.py keys|set`.
- Keep Jira secret fields as `<SET_BY_USER>` until the user fills them locally. PR credentials stay in the child repo's `.env`; never copy them into Hub config or chat.
- Use sibling-relative paths such as `../mcp-jira-sync` and `../mcp-pr-companion` in tracked files and config.
- Preserve the existing `mcp-server-logs` server and `logs` feature in `config/private/hub.json`.
- Do not change child source, tool names, Hub templates, contracts, or smoke checks unless a mismatch is found during implementation. The current Hub examples already list the expected Jira and PR tools.
- Before any commit, run the Hub's three required checks: unit tests, smoke, and leak check. Do not commit as part of this plan.

---

### Task 1: Create the two private child configs

**Files:**
- Create: `config/private/mcps/mcp-jira-sync.json` from `config/mcps/mcp-jira-sync.example.json`
- Create: `config/private/mcps/mcp-pr-companion.json` from `config/mcps/mcp-pr-companion.example.json`

**Interfaces:**
- Consumes: the two tracked child templates and the existing `scripts/private_config.py` helper.
- Produces: private configs with the documented relative cwd, launch command, args, timeout, and env policy.

- [x] **Step 1: Check whether either private config already exists.**

Run in PowerShell from the Hub root:

```powershell
Test-Path config/private/mcps/mcp-jira-sync.json
Test-Path config/private/mcps/mcp-pr-companion.json
```

Expected: both are absent in the current checkout. If either exists when executing this plan, do not overwrite or open it; inspect its key names with Step 3.

- [x] **Step 2: Copy only missing templates.**

```powershell
New-Item -ItemType Directory -Force config/private/mcps | Out-Null
if (-not (Test-Path config/private/mcps/mcp-jira-sync.json)) { Copy-Item config/mcps/mcp-jira-sync.example.json config/private/mcps/mcp-jira-sync.json }
if (-not (Test-Path config/private/mcps/mcp-pr-companion.json)) { Copy-Item config/mcps/mcp-pr-companion.example.json config/private/mcps/mcp-pr-companion.json }
```

Expected: Jira runs with the Hub venv and points `CENTRAL_CONFIG_PATH` at its own private file; PR Companion runs `node bin/cli.js server` from `../mcp-pr-companion` with the documented Windows environment inheritance and 300-second timeout.

- [x] **Step 3: Inspect keys without opening either file.**

```powershell
uv run python scripts/private_config.py keys mcp-jira-sync
uv run python scripts/private_config.py keys mcp-pr-companion
```

Expected: the helper prints key names and redacted values. Jira retains `<SET_BY_USER>` for `jira.email`, `jira.api_token`, and `jira.ai_context_key`; do not enter real values. PR has no Hub credential fields.

- [x] **Step 4: Confirm the new private files are ignored.**

```powershell
git check-ignore config/private/mcps/mcp-jira-sync.json config/private/mcps/mcp-pr-companion.json
```

Expected: both paths are reported as ignored.

### Task 2: Merge disabled entries into the private Hub manifest

**Files:**
- Modify: `config/private/hub.json`

**Interfaces:**
- Consumes: `config/hub.example.json` as the canonical server and feature allowlist.
- Produces: two disabled server entries and two disabled features, with all existing entries preserved.

- [x] **Step 1: Add only the two server entries from the example.**

Add under `servers` without replacing `mcp-server-logs`:

```json
"mcp-jira-sync": { "enabled": false, "configFile": "config/private/mcps/mcp-jira-sync.json" },
"mcp-pr-companion": { "enabled": false, "configFile": "config/private/mcps/mcp-pr-companion.json" }
```

- [x] **Step 2: Add the `jira` and `pr` features from the example.**

Copy their complete `tools` arrays from `config/hub.example.json`, preserving these aliases:

```text
jira__check_connection
jira__list_synced
jira__sync_ticket
jira__read_ticket
jira__read_asset

pr__get_pr_context_pack
pr__search_pr_files
pr__get_pr_file_context
pr__get_pr_file_changes
pr__get_pr_sync_status
pr__check_bitbucket_connection
pr__get_pr_comments
pr__list_my_open_prs
pr__sync_my_open_prs
```

Set both feature objects to `"enabled": false`. Preserve the existing `logs` feature. Do not change `config/hub.example.json`; it already contains these exact entries.

- [x] **Step 3: Check only server/feature IDs and enabled flags.**

Read `config/private/hub.json` (allowed by Hub `AGENTS.md`) and confirm it contains exactly the prior logs entries plus `mcp-jira-sync`, `mcp-pr-companion`, `jira`, and `pr`; all four new enabled flags must be false. Do not include any credentials in the manifest.

Expected: no `mcp-pr-compaion` key, no duplicate aliases, and no changes to the current logs configuration.

### Task 3: Correct the stale PR Companion Hub instruction

**Files:**
- Modify: `../mcp-pr-companion/docs/HUB_INTEGRATION.md`

**Interfaces:**
- Consumes: `../mcp-hub/AGENTS.md` and `../mcp-hub/docs/adding-a-child-mcp.md`.
- Produces: child documentation that distinguishes the Hub manifest from private child config files.

- [x] **Step 1: Replace the inaccurate editing instruction in section 3.**

Replace the sentence that says agents must edit Hub config only through `scripts/private_config.py` and must never open all of `config/private/**` with:

```text
Follow the Hub's AGENTS.md and docs/adding-a-child-mcp.md: config/private/hub.json may be read and edited, but must contain no secrets. Use scripts/private_config.py keys|set only for config/private/mcps/mcp-jira-sync.json and config/private/mcps/mcp-pr-companion.json, and never open or print those child config files after creation.
```

- [x] **Step 2: Review the child-repo diff.**

Run in the PR Companion repo:

```powershell
git diff --check
git diff -- docs/HUB_INTEGRATION.md
```

Expected: exactly one documentation correction, with no credentials, machine paths, tool behavior changes, or unrelated formatting.

### Task 4: Hand off credentials and activate only after confirmation

**Files:**
- Modify after user confirmation: `config/private/mcps/mcp-jira-sync.json` through `scripts/private_config.py` where safe, and `config/private/hub.json`
- User-managed: `../mcp-pr-companion/.env`

**Interfaces:**
- Consumes: the user's locally supplied Jira and Bitbucket configuration.
- Produces: enabled server and feature entries only after both children pass their documented checks.

- [x] **Step 1: Tell the user which values must be filled locally; never request them in chat.**

Jira requires a real `jira.base_url`, `jira.email`, `jira.api_token`, and `jira.ai_context_key`. The PR child requires at least `BITBUCKET_EMAIL` and `BITBUCKET_API_TOKEN` in its own `.env`; `node bin/cli.js check` also requires a repository setting per `docs/HUB_INTEGRATION.md`. Keep PR secrets out of Hub config.

- [x] **Step 2: Prepare PR Companion.**

From `../mcp-pr-companion`, run (credentials are not needed for preparation):

```powershell
node bin/cli.js prepare
```

Expected: dependencies/build are ready. Preparation completed successfully. Never open or print `.env`.

- [ ] **Step 3: Run the credential-backed PR check after `.env` is filled.**

From `../mcp-pr-companion`, run:

```powershell
node bin/cli.js check
```

Expected: all ✓. Never open or print `.env`.

- [ ] **Step 4: Wait for the user to confirm credentials are filled.**

Do not enable either new server while Jira contains placeholders or before the user confirms setup. `jira__check_connection` checks Jira login but does not validate `ai_context_key`; require that key too.

- [ ] **Step 5: Enable both server and feature pairs in `config/private/hub.json`.**

After confirmation only, set `mcp-jira-sync` and `jira` to true, and `mcp-pr-companion` and `pr` to true. Then restart the client process that runs the Hub.

### Task 5: Verify the Hub and repository rules

**Files:**
- Verify: `config/private/hub.json` (IDs and states only)
- Verify: `../mcp-pr-companion/docs/HUB_INTEGRATION.md`
- No source or test files are expected to change.

**Interfaces:**
- Consumes: completed child configs and read-only smoke tools.
- Produces: verified enabled child connections with the documented tool aliases.

- [x] **Step 1: Run PR Companion's source checks.**

From `../mcp-pr-companion`, run:

```powershell
npx tsc --noEmit
npm test
```

Expected: typecheck and unit tests pass. Both passed. The child tests must not call Bitbucket.

- [ ] **Step 2: Run PR Companion's credential-backed check.**

After `.env` is filled, run from `../mcp-pr-companion`:

```powershell
node bin/cli.js check
```

Expected: all ✓.

- [x] **Step 3: Run the Hub offline checks.**

From the Hub root, run:

```powershell
uv run python -m unittest discover tests
uv run python scripts/check_leaks.py
```

Expected: unit tests and leak check pass. Both passed.

- [ ] **Step 4: Run live Hub smoke after credentials are ready and both children are enabled.**

From the Hub root, run:

```powershell
uv run python scripts/smoke.py
```

Expected: smoke reports `mcp-jira-sync` via `jira__check_connection` and `mcp-pr-companion` via `pr__check_bitbucket_connection`, both read-only. Keep both children disabled and defer this step until the user confirms credentials are filled.

- [x] **Step 5: Check repository status without staging or committing.**

Run `git status --short` in the Hub and PR Companion repos. Expected: only the intended PR integration documentation is tracked; new private config files remain ignored. Do not commit.

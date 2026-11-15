# Orchestrator runbook

For the session that dispatches the tracks, reviews each branch, and merges it. The orchestrator never implements a track's tasks.

## Environment

- Hub checkout (`master`): the running service uses it. Never switch it, never edit it for this plan.
- Arch checkout `../mcp-hub-arch` on `arch/base`: where merges and the final checks happen.
- One worktree per track, created from `arch/base`, each with its own venv:

```
cd "../mcp-hub-arch"
git worktree add "../mcp-hub-track-a" -b arch/track-a arch/base
cd "../mcp-hub-track-a" && uv sync --locked
```

Repeat for `b`, `c`, `d`, `e`, `f`. Track worktrees are removed after their branch is merged (`git worktree remove`), never with `--force` while work is uncommitted.

## Dispatch (Phase 1)

Start all six agents in one message, each with `subagent_type: "general-purpose"` and `model: "haiku"`. Do not use `isolation: "worktree"`: the tool branches from the session's default branch, not from `arch/base`.

Prompt template (fill `<x>` and `<card>`):

```
You implement one track of the mcp-hub plan. Your worktree is ../mcp-hub-track-<x>
(a sibling of the Hub checkout root, where your shell starts), on branch arch/track-<x>.

1. Read, in this order: agent-plans/2026-11-15-mcp-standards-alignment/00-overview.md
   (all of it, including the Haiku protocol) and agent-plans/2026-11-15-mcp-standards-alignment/<card>.
2. Work only inside that worktree. Start every shell call with `cd` to your worktree (`../mcp-hub-track-<x>`, relative to the Hub checkout root); cd does not persist between calls.
   Run commands with uv run --no-sync.
3. Follow the Haiku protocol in 00-overview.md for every task in your card, in order.
4. Commit after each green task on branch arch/track-<x>. End each commit message with the
   `Co-Authored-By: Claude Haiku 5.5` trailer that the orchestrator gives you in this prompt.
5. Never push, never merge, never change the version, never touch files outside your card's Owns list.
6. Finish with: task IDs done, files changed, the actual output of the verify commands, deviations,
   and blockers. Stop and report, without guessing, when a stop rule in your card or in 00-overview.md applies.
```

Cards: track A `04-track-a-results.md`, B `05-track-b-events.md`, C `06-track-c-bridge.md`, D `07-track-d-schema-policy.md`, E `08-track-e-pins.md`, F `09-track-f-harness.md`.

## Review and merge (each track)

1. Read the branch diff: `git -C "../mcp-hub-track-<x>" diff arch/base...HEAD --stat`, then the full diff.
2. Check that only Owns-list files changed. Any other file is a reject.
3. Run `scripts/package_qc.py` in the track worktree yourself; do not trust the agent's report.
4. Review the logic for the card's FR requirements. Use the `code-review` skill (level `high`) on the branch diff; fix or send back each confirmed finding.
5. Merge in the arch checkout: `git -C "../mcp-hub-arch" merge --no-ff arch/track-<x> -m "merge track <x>"`, then run `package_qc.py` there. If it fails, send the track back; do not fix it in the arch checkout by hand.
6. Record the merge in the overview's progress log, then remove the track worktree.

Merge order: any order works for Phase 1 (no track depends on another). Merge in the order A, C, E, B, D, F to keep conflicts obvious.

## Phase 2 and 3

- Phase 2 (`10-track-w-wiring.md`): create `arch/track-w` from `arch/base` after every Phase 1 merge, dispatch one agent, and review each commit as it arrives (one commit per task makes this practical).
- Phase 3 (`11-track-q-quality.md`): same procedure as Phase 1.

## Final gate (before asking the user about merge to `master` and the version)

```
cd "../mcp-hub-arch"
uv run --no-sync python scripts/package_qc.py
uv run --no-sync python scripts/smoke.py    # with MCP_HUB_PRIVATE_DIR, MCP_HUB_LOG_DIR, and a free port
```

Then run `code-review-and-quality` on `master..arch/base`. Report the results to the user and ask two questions: merge into `master`? raise the MINOR version (the `version` line in `pyproject.toml` and `uv.lock`)? Do neither without a yes.

## Rollback

- A track that fails review: delete its branch and worktree; `arch/base` is unchanged.
- A merge that breaks `package_qc`: `git -C "../mcp-hub-arch" revert -m 1 <merge-commit>` (a new commit; never reset a shared branch).
- Nothing in this plan touches `master` or the running service until the user approves a merge.

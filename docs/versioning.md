# Versions and upgrades

This page owns how mcp-hub is versioned, how a release is made, how each part is upgraded, and how the desktop status app finds and installs a newer version. Other docs link here instead of repeating it.

## What has a version

| Part | Where its version lives | How it reaches a user |
|---|---|---|
| **mcp-hub** (the Hub and the desktop status app) | `version` in `pyproject.toml`, the only source. A release is the Git tag `v<version>`. | The Hub is a Git checkout. The status app is an installer attached to the GitHub Release of that tag. |
| **Child MCPs** (`mcp-jira-sync`, `mcp-pr-companion`, `mcp-server-logs`, `mcp-read-source`, `mcp-sqlserver`) | Each child's own repository. The Hub follows the child's default branch; there is no pinned child version. | A Git checkout next to the Hub, cloned by `Setup.cmd` from the address in `config/hub.example.json`. |

The desktop app never has a version of its own: it reports the `mcp-hub` version it was built from (the package metadata inside the bundle). `scripts/build_desktop.py` fails the build when the bundled exe's `--version` differs from `pyproject.toml`.

## Numbering

`MAJOR.MINOR.PATCH`, three numbers (no pre-release suffix: the update check ignores one). While the major number is 0:

- raise **MINOR** for a new feature, a changed behavior, or a changed setting, command, or route;
- raise **PATCH** for a fix that changes no behavior.

The installer installs only over an **older** version (see [desktop-app.md](desktop-app.md#install-run-disable)), so every release must be higher than the one before.

## Make a release

A release is made by merging a version change into `master`. Nobody pushes a tag by hand.

1. Raise `version` in `pyproject.toml` and the `mcp-hub` entry of `uv.lock` (`uv lock` rewrites it). Only after the user agrees, see [After a finished change](#after-a-finished-change-ask-about-the-version).
2. Run `uv run python scripts/package_qc.py` and `uv run python scripts/smoke.py`. Both must pass; a `WARN` from a child whose account is not filled in is allowed.
3. Commit and merge to `master`.
4. `.github/workflows/desktop-release.yml` runs on the push to `master`. It reads the version and, when no Release `v<version>` exists yet, runs package QC, builds the installer, writes its SHA-256, creates the tag `v<version>` at the built commit, and publishes the GitHub Release with both files and generated notes. The notes are what the app's **What's new** link shows. A push to `master` that does not raise the version publishes nothing.
5. Manual fallback: `git tag v<version>` then `git push origin v<version>` runs the same workflow for that tag, and the tag must equal the project version. Agents never push a tag.
6. Run the workflow by hand (`workflow_dispatch`) to build and check the installer without publishing.

Details of the build and the workflow: [packaging-and-qc.md](packaging-and-qc.md#desktop-installer).

## After a finished change: ask about the version

Agents in this repository ask the user, once per finished change, whether to raise the version. They do not raise it on their own.

- **When to ask:** when a change the user will notice is finished and `package_qc.py` passes (a feature, a changed behavior, a setting, a command, a route, a fix). Do not ask for a change that touches only docs, tests, or the untracked `agent-plans/` folder; say in the final message why no question was asked.
- **What to ask:** one question in the chat. Give the current version, the suggested bump (MINOR or PATCH under [Numbering](#numbering)) with a short reason, and the option to keep the version.
- **Do not** ask again for each commit of the same change, and do not raise the version before the user agrees.
- **If the user agrees:** raise `version` (and `uv.lock`), run `package_qc.py` and `smoke.py`, and commit. Remind the user that merging to `master` is the release.
- **If the user declines:** change nothing and state "version not raised" in the final message.

## Upgrade each part

Upgrade in this order and check at the end.

| Part | How |
|---|---|
| **Desktop status app** | Press **Update** on the card the app shows (below), or run `.\scripts\hubctl.ps1 app -Install`, or run the newer `mcp-hub-status-setup-<version>.exe`. The app keeps its folder, its sign-in entry, and the remembered look. |
| **Hub** | In the `mcp-hub` folder: `git pull --ff-only`, then `.\scripts\hubctl.ps1 restart`. The restart syncs the locked environment and runs every child's `prepare` step. If you only sign out and in again instead, the Hub still syncs its own Python environment when `uv.lock` changed (children's `prepare` steps run only through `hubctl`). Running `Setup.cmd` again does the same and also downloads any default child that is missing. |
| **A child MCP** | In the child's folder: `git pull --ff-only`, then `.\scripts\hubctl.ps1 restart` in `mcp-hub` (it runs the child's own `prepare` step again). |
| **Check** | `uv run python scripts/smoke.py`: each child in turn, then the Hub. `FAIL` needs a fix; `WARN` only means an account or project folder is not filled in yet. |

`git pull --ff-only` stops instead of merging when the folder has local commits or the history diverged; resolve that first. Nothing here ever reads or changes the private files under `config/private/`.

## How the app finds a new version

The installed status app asks for an update **once, when it starts**, and never again while it runs. There is no switch to turn this off.

- **The request.** One `GET https://api.github.com/repos/<owner>/mcp-hub/releases/latest` (the repository is `UPDATE_REPOSITORY` in `desktop/constants.py`). It sends no token, no user data, and nothing from the Hub; the Hub's admin token never goes through the connection used for GitHub. A failed request (offline, rate limit, an odd answer) shows nothing.
- **When a card appears.** The latest Release is neither a draft nor a pre-release, its tag is a version higher than the one the app runs, and it carries `mcp-hub-status-setup-<version>.exe` and its `.sha256` from this repository's Releases. The card says which version is available, shows the version in use, and has **Update** and **What's new** (the Release page).
- **Only the installed app.** An app run from source has no installer to replace and never offers an update.

## What Update does

1. **Health gate.** Update is disabled while a child fails the Hub's live check: a child that is `unavailable` or keeps `reconnecting` blocks it, and the card names it. This is the same `PASS`/`WARN`/`FAIL` split as `scripts/smoke.py`, read from the state the Hub already reports (the installed app does not run scripts). A child that only needs its account (`credentials required`), is `disabled`, or is `connecting` does not block, and neither does a Hub that is not running (the card says its health was not checked).
2. **Download and verify.** The installer and its `.sha256` come from the Release into the user's temp folder. The installer runs only when its SHA-256 matches; otherwise it is deleted and the card shows one fixed sentence. The file name is built from the verified version, never from the download address, and a download is stopped at 300 MB.
3. **Install silently, reopen.** The app starts a hidden PowerShell and exits. That script waits for the app to end, runs the installer with `/SILENT /SUPPRESSMSGBOXES /NORESTART`, deletes it, and opens the app again, also when the installer failed, so the user is never left without the app.
4. **Failure.** The card shows `desktop.update.failed` and lets the user try again. The Hub and its children are never touched.

**Trust.** The SHA-256 protects against a damaged or interrupted download. It is published next to the installer, so it does not protect against someone who can change the Release itself. The installer is not code-signed, so Windows SmartScreen may warn once, as it does for a manual install.

## Go back to an earlier version

- **Status app:** the installer never installs over a newer or the same version. Uninstall it (Windows Settings → Apps → **mcp-hub Status**, or run the setup again and choose **Uninstall**), then run the earlier `mcp-hub-status-setup-<version>.exe` from that Release.
- **Hub or a child:** `git fetch --tags`, `git checkout v<version>` (for a child, the commit that worked), then `.\scripts\hubctl.ps1 restart`. Return to the branch with `git switch -` and `git pull --ff-only`.

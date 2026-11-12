"""Detached, bounded updater started by hubctl so work survives the Hub restart."""
from __future__ import annotations

import argparse
import concurrent.futures
import os
import re
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import HUB_ROOT, server_switches
from .update_state import add_event, write

GIT_TIMEOUT_SECONDS = 120
SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _run(command: list[str], cwd: Path, timeout: int = GIT_TIMEOUT_SECONDS) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "Never"}
    return subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout, check=False)


def _result(command: list[str], cwd: Path) -> tuple[int, str]:
    try:
        result = _run(command, cwd)
    except subprocess.TimeoutExpired:
        return 124, "timeout"
    except OSError:
        return 127, "git_unavailable"
    return result.returncode, "ok" if result.returncode == 0 else "git_failed"


def _output(command: list[str], cwd: Path) -> tuple[int, str, str]:
    try:
        result = _run(command, cwd)
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except OSError:
        return 127, "", "git_unavailable"
    return result.returncode, result.stdout.strip(), "ok" if result.returncode == 0 else "git_failed"


def _repo_component(server_id: str, cwd: Path) -> dict[str, Any]:
    return {"id": server_id, "kind": "child", "state": "checking", "phase": "check", "error_code": None,
            "repo": str(cwd)}


def _check_repo(item: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    repo = Path(item["repo"])
    if not (repo / ".git").exists():
        return item, "repo_missing"
    code, status, reason = _output(["git", "status", "--porcelain"], repo)
    if code:
        return item, reason
    if status.strip():
        return item, "worktree_dirty"
    code, upstream, reason = _output(["git", "rev-parse", "--abbrev-ref", "@{upstream}"], repo)
    if code:
        return item, "upstream_missing"
    code, reason = _result(["git", "fetch", "--quiet", "--no-tags"], repo)
    if code:
        return item, reason
    code, ahead, reason = _output(["git", "rev-list", "--count", f"{upstream}..HEAD"], repo)
    if code:
        return item, reason
    code, behind, reason = _output(["git", "rev-list", "--count", f"HEAD..{upstream}"], repo)
    if code:
        return item, reason
    if ahead != "0":
        return item, "branch_ahead_or_diverged"
    item["available"] = behind != "0"
    item["upstream"] = upstream
    return item, None


def _update_repo(item: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    if not item.get("available"):
        item["state"] = "complete"
        item["phase"] = "current"
        return item, None
    code, reason = _result(["git", "pull", "--ff-only"], Path(item["repo"]))
    item["state"] = "complete" if code == 0 else "failed"
    item["phase"] = "pull"
    return item, None if code == 0 else reason


def _transition(value: dict[str, Any], item: dict[str, Any], state: str, phase: str, error: str | None = None) -> None:
    item.update(state=state, phase=phase, error_code=error)
    for saved in value.get("components", []):
        if saved.get("id") == item.get("id"):
            saved.update(state=state, phase=phase, error_code=error)
            break
    add_event(value, item["id"], phase, state, error)
    write(value)


def _load_children() -> list[dict[str, Any]]:
    result = []
    for server in server_switches():
        server_id = server.server_id
        if not server.enabled or not SAFE_ID.fullmatch(server_id):
            continue
        repo = (HUB_ROOT.parent / server_id).resolve()
        if repo.parent == HUB_ROOT.parent.resolve() and repo.is_dir():
            result.append(_repo_component(server_id, repo))
    return result


def _initialize(run_id: str, mode: str) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    value = {"run_id": run_id, "state": "running", "started_at": now, "updated_at": now,
             "components": [], "events": []}
    write(value)
    add_event(value, "mcp-hub", mode, "running")
    write(value)
    return value


def _check(run_id: str) -> int:
    value = _initialize(run_id, "check")
    try:
        items = _load_children()
        hub = _repo_component("mcp-hub", HUB_ROOT)
        hub["kind"] = "hub"
        items.append(hub)
    except Exception:
        value["state"] = "partial"
        add_event(value, "mcp-hub", "check", "failed", "config_unavailable")
        write(value)
        return 1
    value["components"] = [{k: v for k, v in item.items() if k not in ("repo", "upstream", "available")} for item in items]
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(8, len(items)))) as pool:
        checked = list(pool.map(_check_repo, items))
    for item, error in checked:
        state = "failed" if error else ("available" if item.get("available") else "complete")
        _transition(value, item, state, "check", error)
    value["components"] = [{k: v for k, v in item.items() if k not in ("repo", "upstream", "available")} for item in items]
    value["state"] = "partial" if any(item.get("error_code") for item in value["components"]) else "complete"
    write(value)
    return 0


def _check_components(value: dict[str, Any], items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(8, len(items)))) as pool:
        checked = list(pool.map(_check_repo, items))
    eligible = []
    for item, error in checked:
        if error:
            state = "skipped" if error in {"worktree_dirty", "branch_ahead_or_diverged", "upstream_missing"} else "failed"
            _transition(value, item, state, "check", error)
        elif item.get("available"):
            _transition(value, item, "available", "check")
            eligible.append(item)
        else:
            _transition(value, item, "complete", "current")
    return eligible


def _pull_components(value: dict[str, Any], items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for item in items:
        _transition(value, item, "updating", "pull")
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(8, len(items)))) as pool:
        updated = list(pool.map(_update_repo, items))
    successful = []
    for item, error in updated:
        _transition(value, item, item["state"], item["phase"], error)
        if item["state"] == "complete":
            successful.append(item)
    return successful


def run_update(run_id: str, retry_ids: set[str] | None = None) -> int:
    value = _initialize(run_id, "update")
    try:
        items = _load_children()
    except Exception:
        add_event(value, "mcp-hub", "check", "failed", "config_unavailable")
        value["state"] = "partial"
        write(value)
        return 1
    update_hub = retry_ids is None or "mcp-hub" in retry_ids
    if retry_ids:
        items = [item for item in items if item["id"] in retry_ids]
    hub_item = _repo_component("mcp-hub", HUB_ROOT)
    hub_item["kind"] = "hub"
    if update_hub:
        items.append(hub_item)
    else:
        hub_item.update(state="complete", phase="not_requested")
        items.append(hub_item)
    value["components"] = [{k: v for k, v in item.items() if k not in ("repo", "upstream", "available")} for item in items]
    check_items = items if update_hub else [item for item in items if item["id"] != "mcp-hub"]
    eligible = _check_components(value, check_items)
    if not eligible:
        value["state"] = "partial" if any(item.get("state") in {"failed", "skipped"} for item in value["components"]) else "complete"
        write(value)
        return 0 if value["state"] == "complete" else 1
    return _apply_updates(value, items, eligible)


def _apply_updates(value: dict[str, Any], items: list[dict[str, Any]], eligible: list[dict[str, Any]]) -> int:
    if not _restart_hub("stop"):
        for item in eligible:
            _transition(value, item, "failed", "stop_hub", "hub_stop_failed")
        value["state"] = "partial"
        write(value)
        return 1
    successful = _pull_components(value, eligible)
    hub = next(item for item in items if item["id"] == "mcp-hub")
    if successful:
        hub_update_error = hub.get("error_code")
        restarted = _restart_hub("restart")
        if not restarted:
            _transition(value, hub, "failed", "restart", "hub_start_failed")
            smoke = 1
        else:
            smoke = _smoke()
            state = "failed" if smoke or hub_update_error else "complete"
            error = "health_check_failed" if smoke else hub_update_error
            _transition(value, hub, state, "smoke" if smoke else ("pull" if hub_update_error else "smoke"), error)
    else:
        started = _restart_hub("start")
        if not started:
            _transition(value, hub, "failed", "start", "hub_start_failed")
    value["state"] = "partial" if any(item.get("state") in {"failed", "skipped"} for item in value["components"]) else "complete"
    write(value)
    return 0 if value["state"] == "complete" else 1


def _restart_hub(command: str) -> bool:
    system_root = os.environ.get("SystemRoot")
    if not system_root:
        return False
    powershell = os.path.join(system_root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    script = HUB_ROOT / "scripts" / "hubctl.ps1"
    try:
        result = subprocess.run([powershell, "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass",
                                "-File", str(script), command], cwd=HUB_ROOT, stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=900,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False)
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _smoke() -> int:
    python = HUB_ROOT / ".venv" / "Scripts" / "python.exe"
    if not python.is_file():
        python = Path(sys.executable)
    try:
        result = subprocess.run([str(python), "scripts/smoke.py"], cwd=HUB_ROOT, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=900, check=False)
        return result.returncode
    except (OSError, subprocess.TimeoutExpired):
        return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--retry", default="")
    parser.add_argument("--run-id", default="")
    args = parser.parse_args(argv)
    ids = {item for item in args.retry.split(",") if SAFE_ID.fullmatch(item)}
    run_id = args.run_id if re.fullmatch(r"[A-Fa-f0-9-]{36}", args.run_id) else str(uuid.uuid4())
    return _check(run_id) if args.check else run_update(run_id, ids or None)


if __name__ == "__main__":
    raise SystemExit(main())

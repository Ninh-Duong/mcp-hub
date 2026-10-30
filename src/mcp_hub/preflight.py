"""Check and install what enabled child MCPs need before the Hub starts.

Run as `python -m mcp_hub.preflight [--install]`. Each child declares its needs in the
`prepare` block of its server entry: a runtime (for example Node.js with a minimum
version and a winget package) and a command that installs and builds its dependencies.
Only names, states, and error types are reported; child output is never printed.
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Callable, Iterable, Sequence

from .audit import AuditLog
from .children import child_environment
from .config import HubConfig, PrepareSpec, RuntimeRequirement, ServerConfig, load_config

_VERSION_RE = re.compile(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?")
VERSION_TIMEOUT_SECONDS = 30.0
WINGET_TIMEOUT_SECONDS = 900.0


@dataclass(frozen=True)
class CheckResult:
    server_id: str
    step: str
    ok: bool
    detail: str


Runner = Callable[..., subprocess.CompletedProcess]
Which = Callable[[str], "str | None"]


_DEFAULT_PATHEXT = ".COM;.EXE;.BAT;.CMD"


def find_executable(name: str, path: str | None = None, platform: str = sys.platform) -> str | None:
    """Find `name` in the absolute entries of PATH, like `shutil.which` but never in the current directory.

    `shutil.which` searches the working directory first on Windows, so a planted `node.exe` or
    `winget.exe` there would run. Names with a directory component and relative PATH entries
    (including `.`) are never resolved.
    """
    if not name or os.path.dirname(name):
        return None
    search = os.environ.get("PATH", "") if path is None else path
    entries = [entry for entry in search.split(os.pathsep) if entry and os.path.isabs(entry)]
    for entry in entries:
        for candidate_name in _candidate_names(name, platform):
            candidate = os.path.join(entry, candidate_name)
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
    return None


def _candidate_names(name: str, platform: str) -> list[str]:
    if platform != "win32":
        return [name]
    extensions = [ext for ext in os.environ.get("PATHEXT", _DEFAULT_PATHEXT).split(";") if ext]
    if any(name.lower().endswith(ext.lower()) for ext in extensions):
        return [name]
    return [name + ext for ext in extensions]


def run_hidden(command: Sequence[str], *, cwd: str | None = None, env: dict[str, str] | None = None,
               timeout: float) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(command), cwd=cwd, env=env, timeout=timeout, check=False,
        stdin=subprocess.DEVNULL, capture_output=True, text=True, errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def parse_version(text: str) -> tuple[int, ...] | None:
    match = _VERSION_RE.search(text or "")
    if match is None:
        return None
    return tuple(int(part) for part in match.groups() if part is not None)


def format_version(version: tuple[int, ...]) -> str:
    return ".".join(str(part) for part in version)


def installed_version(requirement: RuntimeRequirement, *, runner: Runner = run_hidden,
                      which: Which = find_executable) -> tuple[int, ...] | None:
    executable = which(requirement.name)
    if executable is None:
        return None
    try:
        result = runner([executable, "--version"], timeout=VERSION_TIMEOUT_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return parse_version(result.stdout) if result.returncode == 0 else None


def install_runtime(requirement: RuntimeRequirement, *, upgrade: bool, runner: Runner = run_hidden,
                    which: Which = find_executable, platform: str = sys.platform) -> bool:
    winget = which("winget") if platform == "win32" and requirement.winget_id else None
    if winget is None:
        return False
    action = "upgrade" if upgrade else "install"
    command = [winget, action, "--id", requirement.winget_id, "-e", "--silent",
               "--accept-package-agreements", "--accept-source-agreements"]
    try:
        return runner(command, timeout=WINGET_TIMEOUT_SECONDS).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _registry_path_entries() -> list[str]:
    import winreg

    locations = (
        (winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
        (winreg.HKEY_CURRENT_USER, r"Environment"),
    )
    entries: list[str] = []
    for hive, key_path in locations:
        try:
            with winreg.OpenKey(hive, key_path) as key:
                value, _kind = winreg.QueryValueEx(key, "Path")
        except OSError:
            continue
        entries.extend(os.path.expandvars(part) for part in str(value).split(os.pathsep) if part)
    return entries


def refresh_windows_path(env: dict[str, str] | None = None,
                         read_registry: Callable[[], Iterable[str]] | None = None,
                         platform: str = sys.platform) -> None:
    """Append PATH entries added since this process started, e.g. by a winget install."""
    if platform != "win32":
        return
    target = os.environ if env is None else env
    current = [part for part in target.get("PATH", "").split(os.pathsep) if part]
    known = {part.rstrip("\\/").casefold() for part in current}
    for entry in (read_registry or _registry_path_entries)():
        if entry.rstrip("\\/").casefold() not in known:
            current.append(entry)
            known.add(entry.rstrip("\\/").casefold())
    target["PATH"] = os.pathsep.join(current)


def ensure_runtime(server_id: str, requirement: RuntimeRequirement, *, install: bool,
                   runner: Runner = run_hidden, which: Which = find_executable,
                   platform: str = sys.platform) -> CheckResult:
    wanted = format_version(requirement.min_version)
    version = installed_version(requirement, runner=runner, which=which)
    if version is not None and version >= requirement.min_version:
        return CheckResult(server_id, requirement.name, True, f"{format_version(version)} (>= {wanted})")
    found = "missing" if version is None else f"{format_version(version)} is older than {wanted}"
    if not install:
        return CheckResult(server_id, requirement.name, False, found)
    if not install_runtime(requirement, upgrade=version is not None, runner=runner, which=which, platform=platform):
        hint = f"winget {requirement.winget_id}" if requirement.winget_id else "no installer configured"
        return CheckResult(server_id, requirement.name, False, f"{found}; automatic install failed ({hint})")
    refresh_windows_path(platform=platform)
    version = installed_version(requirement, runner=runner, which=which)
    if version is None or version < requirement.min_version:
        return CheckResult(server_id, requirement.name, False, f"{found}; still not usable after install")
    return CheckResult(server_id, requirement.name, True, f"installed {format_version(version)}")


def run_prepare(server: ServerConfig, spec: PrepareSpec, *, runner: Runner = run_hidden,
                which: Which = find_executable) -> CheckResult:
    executable = which(spec.command[0]) or spec.command[0]
    # The same variables the child gets at runtime, never the Hub's whole environment.
    env = child_environment(server)
    try:
        result = runner([executable, *spec.command[1:]], cwd=str(server.cwd), env=env,
                        timeout=spec.timeout_seconds)
    except subprocess.TimeoutExpired:
        return CheckResult(server.server_id, "prepare", False, f"timed out after {spec.timeout_seconds:g}s")
    except OSError as exc:
        return CheckResult(server.server_id, "prepare", False, f"could not start ({type(exc).__name__})")
    if result.returncode != 0:
        command = " ".join(spec.command)
        return CheckResult(server.server_id, "prepare", False,
                           f"exit {result.returncode}; run `{command}` in the child repository for details")
    return CheckResult(server.server_id, "prepare", True, "dependencies ready")


def check_server(server: ServerConfig, *, install: bool, runner: Runner = run_hidden,
                 which: Which = find_executable, platform: str = sys.platform) -> list[CheckResult]:
    spec = server.prepare
    if spec is None:
        return []
    results: list[CheckResult] = []
    if spec.runtime is not None:
        results.append(ensure_runtime(server.server_id, spec.runtime, install=install,
                                      runner=runner, which=which, platform=platform))
        if not results[-1].ok:
            return results
    if install:
        results.append(run_prepare(server, spec, runner=runner, which=which))
    return results


def run_preflight(config: HubConfig, *, install: bool, runner: Runner = run_hidden,
                  which: Which = find_executable, platform: str = sys.platform) -> list[CheckResult]:
    results: list[CheckResult] = []
    for server in config.servers.values():
        results.extend(check_server(server, install=install, runner=runner, which=which, platform=platform))
    return results


def report(results: list[CheckResult], audit: AuditLog) -> None:
    for result in results:
        audit.write("preflight.check", level=logging.INFO if result.ok else logging.WARNING,
                    server=result.server_id, step=result.step, ok=result.ok)
        print(f"{result.server_id:<18} {result.step:<8} {'OK' if result.ok else 'FAIL':<5} {result.detail}")
    if not results:
        print("No enabled child MCP declares prepare requirements.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--install", action="store_true",
                        help="install missing runtimes with winget and prepare child dependencies")
    args = parser.parse_args(argv)
    refresh_windows_path()
    results = run_preflight(load_config(), install=args.install)
    report(results, AuditLog())
    return 0 if all(result.ok for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())

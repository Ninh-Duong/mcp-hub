"""Update check and install for the installed desktop app (docs/versioning.md).

At start the app asks GitHub for the latest Release of its repository (`constants.UPDATE_REPOSITORY`). When that
version is higher, the window offers an Update button: the installer and its SHA-256 are downloaded from the same
Release, the installer runs only when the hash matches, and a hidden PowerShell waits for the app to exit, runs the
installer silently, and opens the new app. The only data sent is the request itself: no token, no Hub data. The Hub's
token never goes through the opener used here (status.loopback_opener is the Hub's).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Any

from .constants import APP_NAME, EXE_NAME, UPDATE_REPOSITORY
from .status import DesktopError

Version = tuple[int, int, int]
Opener = Callable[..., Any]

API_URL = f"https://api.github.com/repos/{UPDATE_REPOSITORY}/releases/latest"
RELEASES_PREFIX = f"https://github.com/{UPDATE_REPOSITORY}/releases/"
DOWNLOAD_PREFIX = f"{RELEASES_PREFIX}download/"
CHECK_TIMEOUT_SECONDS = 10.0
DOWNLOAD_TIMEOUT_SECONDS = 60.0
MAX_RELEASE_JSON_BYTES = 1_000_000
MAX_INSTALLER_BYTES = 300 * 1024 * 1024
CHUNK_BYTES = 1024 * 1024
UPDATE_FOLDER = f"{APP_NAME}-update"
_VERSION = re.compile(r"v?(\d+)\.(\d+)\.(\d+)")
_SHA256 = re.compile(r"[0-9a-fA-F]{64}")
# Waits for the app (its PID) to exit, runs the installer silently, then opens the app again, also when the
# installer failed, so the user is never left without the app. Paths and the PID come through the environment.
UPDATE_SCRIPT = (
    "$ErrorActionPreference = 'SilentlyContinue'; "
    "Wait-Process -Id $env:STATUS_UPDATE_PID -Timeout 60; "
    "Start-Process -FilePath $env:STATUS_UPDATE_INSTALLER -ArgumentList '/SILENT','/SUPPRESSMSGBOXES','/NORESTART' -Wait; "
    "Remove-Item -LiteralPath $env:STATUS_UPDATE_INSTALLER -Force; "
    "Start-Process -FilePath $env:STATUS_UPDATE_EXE"
)


@dataclass(frozen=True)
class Release:
    version: Version
    setup_url: str
    sha_url: str
    notes_url: str

    @property
    def label(self) -> str:
        return ".".join(map(str, self.version))


def parse_version(text: str) -> Version | None:
    """`0.2.0` or `v0.2.0`; anything else (a pre-release such as `0.2.0-rc1`, text) is None."""
    match = _VERSION.fullmatch(text.strip()) if isinstance(text, str) else None
    return (int(match[1]), int(match[2]), int(match[3])) if match else None


def app_version() -> str | None:
    """The version this app was built from (pyproject.toml, through the package metadata), or None.

    An install upgraded by an older installer can still hold the previous version's metadata next to the
    new one; the highest version is the one that runs, so it wins over whichever is found first.
    """
    versions = {dist.version for dist in metadata.distributions(name="mcp-hub")}
    if not versions:
        return None
    return max(versions, key=lambda text: (parse_version(text) is not None, parse_version(text) or (0, 0, 0)))


def github_opener() -> urllib.request.OpenerDirector:
    """HTTPS with the system proxy, for GitHub only. Built on use: TLS setup stays out of the app's start."""
    return urllib.request.build_opener()


def _get(opener: Opener, url: str, limit: int, timeout: float) -> bytes:
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": APP_NAME})
    with opener(request, timeout=timeout) as response:
        return response.read(limit + 1)[:limit]


def _asset_url(assets: list[Any], name: str) -> str | None:
    for asset in assets:
        url = asset.get("browser_download_url") if isinstance(asset, dict) else None
        if isinstance(asset, dict) and asset.get("name") == name and isinstance(url, str) and url.startswith(DOWNLOAD_PREFIX):
            return url
    return None


def _release_from(data: Any, current: Version) -> Release | None:
    if not isinstance(data, dict) or data.get("draft") or data.get("prerelease"):
        return None
    version = parse_version(str(data.get("tag_name", "")))
    assets = data.get("assets") if isinstance(data.get("assets"), list) else []
    notes = data.get("html_url")
    if version is None or version <= current or not isinstance(notes, str) or not notes.startswith(RELEASES_PREFIX):
        return None
    name = f"mcp-hub-status-setup-{'.'.join(map(str, version))}.exe"
    setup, sha = _asset_url(assets, name), _asset_url(assets, f"{name}.sha256")
    return Release(version, setup, sha, notes) if setup and sha else None


def check_latest(current: str | None, opener: Opener | None = None) -> Release | None:
    """The latest Release when its version is higher than `current` and it carries an installer with its SHA-256.

    Any failure (no network, rate limit, odd answer) is None: a failed check shows nothing.
    """
    version = parse_version(current or "")
    if version is None:
        return None
    try:
        data = json.loads(_get(opener or github_opener().open, API_URL, MAX_RELEASE_JSON_BYTES, CHECK_TIMEOUT_SECONDS))
    except (OSError, ValueError):
        return None
    return _release_from(data, version)


def open_notes(release: Release, opener: Callable[[str], Any] = webbrowser.open) -> None:
    """Open the Release page ("what's new"); the address was checked to be this repository's Releases."""
    opener(release.notes_url)


def _expected_hash(text: bytes) -> str:
    first = text.decode("ascii", errors="replace").split(maxsplit=1)
    if not first or not _SHA256.fullmatch(first[0]):
        raise DesktopError("The update has no valid SHA-256")
    return first[0].lower()


def _download(opener: Opener, url: str, path: Path) -> str:
    """Write the installer to `path` and return its SHA-256; stops at MAX_INSTALLER_BYTES."""
    digest, size = hashlib.sha256(), 0
    request = urllib.request.Request(url, headers={"User-Agent": APP_NAME})
    with opener(request, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response, path.open("wb") as file:
        while chunk := response.read(CHUNK_BYTES):
            size += len(chunk)
            if size > MAX_INSTALLER_BYTES:
                raise DesktopError("The update is larger than expected")
            digest.update(chunk)
            file.write(chunk)
    return digest.hexdigest()


def download_verified(release: Release, folder: Path, opener: Opener | None = None) -> Path:
    """Download the installer next to its hash and return it only when the SHA-256 matches; else delete it.

    The file name is built from the validated version, never from the download address.
    """
    opener = opener or github_opener().open
    target = folder / f"mcp-hub-status-setup-{release.label}.exe"
    partial = target.with_suffix(".part")
    try:
        folder.mkdir(parents=True, exist_ok=True)
        expected = _expected_hash(_get(opener, release.sha_url, 1024, CHECK_TIMEOUT_SECONDS))
        actual = _download(opener, release.setup_url, partial)
        if actual != expected:
            raise DesktopError("The update does not match its SHA-256")
        os.replace(partial, target)
    except OSError as exc:
        raise DesktopError("The update could not be downloaded") from exc
    finally:
        partial.unlink(missing_ok=True)
    return target


def launch_update(installer: Path, exe: str, pid: int, run: Callable[..., Any] = subprocess.Popen) -> None:
    """Start the hidden PowerShell that installs after this app exits and opens the new one. Raises OSError."""
    system_root = os.environ.get("SystemRoot")
    if not system_root:
        raise OSError("Updating requires Windows")
    powershell = Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    environment = {**os.environ, "STATUS_UPDATE_PID": str(pid), "STATUS_UPDATE_INSTALLER": str(installer),
                   "STATUS_UPDATE_EXE": exe}
    run([str(powershell), "-NoProfile", "-NonInteractive", "-Command", UPDATE_SCRIPT], env=environment,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def install(release: Release) -> None:
    """Download and verify the installer, then hand over to the update script. The caller exits the app afterwards."""
    exe = sys.executable if getattr(sys, "frozen", False) else None
    if exe is None or Path(exe).name != EXE_NAME:
        raise DesktopError("Only the installed app can update itself")
    installer = download_verified(release, Path(tempfile.gettempdir()) / UPDATE_FOLDER)
    try:
        launch_update(installer, exe, os.getpid())
    except OSError as exc:
        raise DesktopError("The update could not be started") from exc


@dataclass(frozen=True)
class Updater:
    """What the window needs to offer updates; the window takes it as one argument, so tests pass fakes."""

    current: str
    check: Callable[[], Release | None]
    install: Callable[[Release], None] = install
    open_notes: Callable[[Release], None] = open_notes


def default_updater() -> Updater | None:
    """Only the installed app can replace itself, and only when it knows its own version."""
    current = app_version()
    if current is None or not getattr(sys, "frozen", False):
        return None
    return Updater(current, lambda: check_latest(current))

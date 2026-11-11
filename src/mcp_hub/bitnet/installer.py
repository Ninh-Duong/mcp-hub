"""Install the prebuilt BitNet runtime and its model, so nobody builds anything (docs/bitnet.md).

The Hub's GitHub Release carries `bitnet-runtime-win-x64.zip` (llama-server built by CI) and
`bitnet-manifest.json` (the runtime's SHA-256 and the pinned model URL with its SHA-256). Every file is
downloaded into `vendor/bitnet/.partial/`, checked against its SHA-256, and only then moved into place;
`install.json` is written last, so a stopped or failed install never looks installed.

Blocking work: run `Installer.run` on a worker thread (`asyncio.to_thread`).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import threading
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.error import HTTPError, URLError

from ..settings import BitnetSettings

MANIFEST_ASSET = "bitnet-manifest.json"
RUNTIME_ASSET = "bitnet-runtime-win-x64.zip"
SERVER_EXE = "llama-server.exe"
INSTALL_VERSION = 2
_CHUNK = 1 << 20
_MANIFEST_MAX_BYTES = 64 * 1024
_RELEASE_MAX_BYTES = 2 * 1024 * 1024
_USER_AGENT = "mcp-hub-bitnet-installer"
# Steps in the order they run; the desktop app shows one text per step.
STEPS = ("prepare", "runtime", "model", "extract")

Progress = Callable[[str, int | None], None]
Opener = Callable[..., Any]


class InstallError(Exception):
    """A failed install. `code` is one of ERROR_CODES; it holds no path, URL, or secret."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


ERROR_CODES = ("platform", "disk", "network", "release", "checksum", "extract", "cancelled")


class Cancelled(InstallError):
    def __init__(self) -> None:
        super().__init__("cancelled")


@dataclass(frozen=True)
class Download:
    name: str
    url: str
    sha256: str
    size: int


@dataclass(frozen=True)
class InstallPlan:
    runtime: Download
    model: Download


def bitnet_dir(root: Path) -> Path:
    return root / "vendor" / "bitnet"


def hub_version() -> str | None:
    try:
        return metadata.version("mcp-hub")
    except metadata.PackageNotFoundError:
        return None


def _get(opener: Opener, url: str, timeout: float) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT, "Accept": "*/*"})
    return opener(request, timeout=timeout)


def _read_json(opener: Opener, url: str, timeout: float, limit: int) -> dict[str, Any]:
    try:
        with _get(opener, url, timeout) as response:
            raw = response.read(limit + 1)
    except HTTPError as exc:
        raise InstallError("release" if exc.code == 404 else "network") from exc
    except (URLError, OSError) as exc:
        raise InstallError("network") from exc
    if len(raw) > limit:
        raise InstallError("release")
    try:
        value = json.loads(raw.decode("utf-8"))
    except ValueError as exc:
        raise InstallError("release") from exc
    if not isinstance(value, dict):
        raise InstallError("release")
    return value


def fetch_release(opener: Opener, repository: str, version: str | None, timeout: float) -> dict[str, Any]:
    """The Release of this Hub version, or the latest one when this version was never released."""
    api = f"https://api.github.com/repos/{repository}/releases"
    if version:
        try:
            return _read_json(opener, f"{api}/tags/v{version}", timeout, _RELEASE_MAX_BYTES)
        except InstallError as exc:
            if exc.code != "release":
                raise
    return _read_json(opener, f"{api}/latest", timeout, _RELEASE_MAX_BYTES)


def _asset_url(release: dict[str, Any], name: str) -> str:
    for asset in release.get("assets") or ():
        if isinstance(asset, dict) and asset.get("name") == name:
            url = asset.get("browser_download_url")
            if isinstance(url, str) and url.startswith("https://"):
                return url
    raise InstallError("release")


def _download_entry(value: Any, *, name: str, url: str | None = None) -> Download:
    if not isinstance(value, dict):
        raise InstallError("release")
    sha = value.get("sha256")
    size = value.get("size")
    link = url or value.get("url")
    if not (isinstance(sha, str) and len(sha) == 64 and all(c in "0123456789abcdef" for c in sha.lower())):
        raise InstallError("release")
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise InstallError("release")
    if not (isinstance(link, str) and link.startswith("https://")):
        raise InstallError("release")
    return Download(name, link, sha.lower(), size)


def resolve_plan(opener: Opener, release: dict[str, Any], timeout: float) -> InstallPlan:
    """Read the manifest of a Release: the runtime asset and the pinned model, each with its SHA-256 and size."""
    manifest = _read_json(opener, _asset_url(release, MANIFEST_ASSET), timeout, _MANIFEST_MAX_BYTES)
    runtime = _download_entry(manifest.get("runtime"), name=RUNTIME_ASSET, url=_asset_url(release, RUNTIME_ASSET))
    model_name = (manifest.get("model") or {}).get("file") if isinstance(manifest.get("model"), dict) else None
    if not isinstance(model_name, str) or "/" in model_name or "\\" in model_name or not model_name.endswith(".gguf"):
        raise InstallError("release")
    return InstallPlan(runtime, _download_entry(manifest.get("model"), name=model_name))


def sha256_file(path: Path, cancel: threading.Event | None = None) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK), b""):
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            digest.update(chunk)
    return digest.hexdigest()


def _stream(response: Any, target: Path, item: Download, report: Callable[[int], None],
            cancel: threading.Event) -> str:
    """Write the body to `target`, hashing as it goes; never more than the manifest's size."""
    digest, received = hashlib.sha256(), 0
    with target.open("wb") as handle:
        while True:
            if cancel.is_set():
                raise Cancelled()
            chunk = response.read(_CHUNK)
            if not chunk:
                break
            received += len(chunk)
            if received > item.size:
                raise InstallError("checksum")
            handle.write(chunk)
            digest.update(chunk)
            report(received)
    return digest.hexdigest()


def download_verified(opener: Opener, item: Download, dest: Path, *, timeout: float, progress: Callable[[int], None],
                      cancel: threading.Event) -> Path:
    """Download `item` to `dest` unless a file with its SHA-256 is already there; a bad file is deleted."""
    if dest.is_file() and dest.stat().st_size == item.size and sha256_file(dest, cancel) == item.sha256:
        progress(100)
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    try:
        with _get(opener, item.url, timeout) as response:
            actual = _stream(response, part, item, lambda done: progress(min(100, done * 100 // item.size)), cancel)
    except InstallError:
        part.unlink(missing_ok=True)
        raise
    except (URLError, OSError) as exc:
        part.unlink(missing_ok=True)
        raise InstallError("network") from exc
    if actual != item.sha256:
        part.unlink(missing_ok=True)
        raise InstallError("checksum")
    os.replace(part, dest)
    return dest


def _safe_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    """Refuse absolute paths and `..` so nothing is written outside the target folder."""
    members = archive.infolist()
    for info in members:
        path = PurePosixPath(info.filename.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts or (path.parts and ":" in path.parts[0]):
            raise InstallError("extract")
    return members


def extract_runtime(archive_path: Path, target: Path) -> Path:
    """Unpack the runtime zip into `target` (replaced as a whole) and return llama-server's path."""
    staging = target.with_name(target.name + ".new")
    shutil.rmtree(staging, ignore_errors=True)
    try:
        with zipfile.ZipFile(archive_path) as archive:
            archive.extractall(staging, members=_safe_members(archive))
    except (zipfile.BadZipFile, OSError) as exc:
        shutil.rmtree(staging, ignore_errors=True)
        raise InstallError("extract") from exc
    servers = sorted(staging.rglob(SERVER_EXE))
    if not servers:
        shutil.rmtree(staging, ignore_errors=True)
        raise InstallError("extract")
    shutil.rmtree(target, ignore_errors=True)
    os.replace(staging, target)
    return target / servers[0].relative_to(staging)


def write_install(root: Path, server: Path, model: Path) -> None:
    """Written last: the sidecar starts only when this file names a server and a model that both exist."""
    data = {
        "server": server.relative_to(root).as_posix(),
        "model": model.relative_to(root).as_posix(),
        "version": INSTALL_VERSION,
    }
    path = bitnet_dir(root) / "install.json"
    temp = path.with_name("install.json.tmp")
    temp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(temp, path)


def default_opener(request: Any, timeout: float) -> Any:
    return urllib.request.urlopen(request, timeout=timeout)  # noqa: S310 - https URLs checked above


class Installer:
    """One install run. `run` blocks; call it on a worker thread and set `cancel` to stop it early."""

    def __init__(
        self,
        root: Path,
        settings: BitnetSettings,
        *,
        opener: Opener = default_opener,
        version: Callable[[], str | None] = hub_version,
        disk_usage: Callable[[Path], Any] = shutil.disk_usage,
        platform: str = sys.platform,
    ) -> None:
        self.root, self.settings = root, settings
        self.opener, self.version, self.disk_usage, self.platform = opener, version, disk_usage, platform
        self.cancel = threading.Event()

    def run(self, progress: Progress) -> Path:
        """Install and return the path of `install.json`; raise InstallError with a short code on failure."""
        progress("prepare", None)
        plan = self._prepare()
        base = bitnet_dir(self.root)
        partial = base / ".partial"
        timeout = self.settings.download_timeout_seconds
        runtime_zip = download_verified(self.opener, plan.runtime, partial / plan.runtime.name, timeout=timeout,
                                        progress=lambda pct: progress("runtime", pct), cancel=self.cancel)
        model_dir = base / "models" / self.settings.model
        model = download_verified(self.opener, plan.model, model_dir / plan.model.name, timeout=timeout,
                                  progress=lambda pct: progress("model", pct), cancel=self.cancel)
        progress("extract", None)
        server = extract_runtime(runtime_zip, base / "runtime")
        write_install(self.root, server, model)
        shutil.rmtree(partial, ignore_errors=True)
        return base / "install.json"

    def _prepare(self) -> InstallPlan:
        if self.platform != "win32":
            raise InstallError("platform")
        base = bitnet_dir(self.root)
        base.mkdir(parents=True, exist_ok=True)
        if self.disk_usage(base).free < self.settings.min_free_bytes:
            raise InstallError("disk")
        timeout = self.settings.download_timeout_seconds
        release = fetch_release(self.opener, self.settings.release_repository, self.version(), timeout)
        return resolve_plan(self.opener, release, timeout)

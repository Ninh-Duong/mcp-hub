from __future__ import annotations

import json
import os
import secrets
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .config import PRIVATE_DIR_ENV, PRIVATE_ROOT
from .settings import HttpSettings

# Where the Hub tells local clients (the desktop status app) how to reach it: `MCP_HUB_CONNECTION_FILE`,
# else next to the other private files when `MCP_HUB_PRIVATE_DIR` relocates them (tests), else the user's
# private app-data folder. The desktop app reads the same path (desktop/connection.py).
CONNECTION_ENV = "MCP_HUB_CONNECTION_FILE"
ICACLS_TIMEOUT_SECONDS = 30


class ServiceConfigError(ValueError):
    """Raised when the local shared-service configuration is unsafe or invalid."""


@dataclass(frozen=True)
class ServiceConfig:
    host: str
    port: int
    token: str = field(repr=False)


def load_service_config(
    *,
    path: str | Path | None = None,
    private_root: str | Path = PRIVATE_ROOT,
    create: bool = True,
    port: int = HttpSettings().port,
) -> ServiceConfig:
    """The service's host, port, and client token. The service (`create=True`) writes `port` from settings
    into the file, keeping the token; readers such as the bridge (`create=False`) take the port the file has."""
    root = Path(private_root).resolve()
    config_path = Path(path) if path is not None else root / "service.json"
    if not config_path.is_absolute():
        config_path = root / config_path
    config_path = config_path.resolve()
    if not config_path.is_relative_to(root):
        raise ServiceConfigError("Service config must be inside config/private/")

    if not config_path.is_file():
        if not create:
            raise ServiceConfigError("Service config is missing; start the Hub first")
        config_path.parent.mkdir(parents=True, exist_ok=True)
        value = {"version": 1, "host": "127.0.0.1", "port": port, "token": secrets.token_urlsafe(48)}
        _write_private_json(config_path, value)
    else:
        try:
            value = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ServiceConfigError(f"Could not read service config ({type(exc).__name__})") from exc

    if not isinstance(value, dict) or value.get("version") != 1:
        raise ServiceConfigError("Unsupported service config version")
    host = value.get("host")
    if host != "127.0.0.1":
        raise ServiceConfigError("The shared Hub must bind to 127.0.0.1")
    token = value.get("token")
    if not isinstance(token, str) or len(token) < 32:
        raise ServiceConfigError("Service bearer token must contain at least 32 characters")
    if create and value.get("port") != port:
        value = {**value, "port": port}
        _write_private_json(config_path, value)
    stored_port = value.get("port")
    if isinstance(stored_port, bool) or not isinstance(stored_port, int) or not 1024 <= stored_port <= 65535:
        raise ServiceConfigError("The shared service port must be between 1024 and 65535")
    return ServiceConfig(host=host, port=stored_port, token=token)


def new_admin_token() -> str:
    """The admin token for one Hub run: never stored in config/private/, so AI clients' files never hold it."""
    return secrets.token_urlsafe(48)


def connection_file() -> Path:
    override = os.environ.get(CONNECTION_ENV)
    if override:
        return Path(override)
    if os.environ.get(PRIVATE_DIR_ENV):
        return PRIVATE_ROOT / "connection.json"
    data = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "state")
    return Path(data) / "mcp-hub" / "connection.json"


def restrict_to_current_user(path: Path, run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> None:
    """On Windows, let only this user and SYSTEM open `path` (chmod there only toggles read-only). Raises OSError."""
    if os.name != "nt":
        return
    user, system_root = os.environ.get("USERNAME"), os.environ.get("SystemRoot")
    if not user or not system_root:
        raise OSError("Cannot name the current user to restrict the connection file")
    domain = os.environ.get("USERDOMAIN")
    account = f"{domain}\\{user}" if domain else user
    icacls = Path(system_root) / "System32" / "icacls.exe"
    command = [str(icacls), str(path), "/inheritance:r", "/grant:r", f"{account}:F", "*S-1-5-18:F"]
    try:
        result = run(command, stdin=subprocess.DEVNULL, capture_output=True, timeout=ICACLS_TIMEOUT_SECONDS,
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired as exc:
        raise OSError("icacls did not finish in time") from exc
    if result.returncode != 0:
        raise OSError("icacls could not restrict the connection file")


def write_connection_file(
    config: ServiceConfig,
    admin_token: str,
    path: Path | None = None,
    restrict: Callable[[Path], None] = restrict_to_current_user,
) -> bool:
    """Write the Hub's URL and this run's admin token for local tools; False when the file was already current.

    Only hubctl and the desktop app read it. The admin token is not the client token in service.json, so an
    AI client's bridge never holds it; the file is limited to this user (and SYSTEM).
    """
    path = path or connection_file()
    value = {"version": 1, "url": f"http://{config.host}:{config.port}", "token": admin_token}
    try:
        if json.loads(path.read_text(encoding="utf-8")) == value:
            return False
    except (OSError, ValueError):
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_private_json(path, value)
    restrict(path)
    return True


def _write_private_json(path: Path, value: dict[str, object]) -> None:
    data = json.dumps(value, indent=2) + "\n"
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix="service-",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_file.write(data)
            temp_path = Path(temp_file.name)
        try:
            temp_path.chmod(0o600)
        except OSError:
            pass
        temp_path.replace(path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)

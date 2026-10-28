from __future__ import annotations

import json
import secrets
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .config import PRIVATE_ROOT


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
) -> ServiceConfig:
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
        value = {"version": 1, "host": "127.0.0.1", "port": 8765, "token": secrets.token_urlsafe(48)}
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
    port = value.get("port")
    if isinstance(port, bool) or not isinstance(port, int) or port != 8765:
        raise ServiceConfigError("The shared service uses the fixed localhost port 8765")
    token = value.get("token")
    if not isinstance(token, str) or len(token) < 32:
        raise ServiceConfigError("Service bearer token must contain at least 32 characters")
    return ServiceConfig(host=host, port=port, token=token)


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

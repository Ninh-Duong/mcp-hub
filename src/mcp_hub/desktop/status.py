"""What the desktop status app shows, read from the running Hub's HTTP service. No Tk here.

The bearer token is read in process from `config/private/service.json` (as the stdio bridge does)
and only ever sent to the Hub on 127.0.0.1; it is never displayed or logged.
"""
from __future__ import annotations

import json
import urllib.request
import webbrowser
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote

from ..service_config import ServiceConfig, ServiceConfigError, load_service_config

REQUEST_TIMEOUT_SECONDS = 3.0
TONES = ("ok", "wait", "off", "error")
# Hub states the app can show; `starting` is set by the app itself after Start Hub.
HUB_TONES = {"running": "ok", "starting": "wait", "stopped": "error", "not_installed": "off", "unauthorized": "error"}
_CHILD_TONES = {"connected": "ok", "connecting": "wait", "reconnecting": "wait", "disabled": "off"}

# Never route the token through a proxy configured for other traffic.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
Opener = Callable[..., Any]


class DesktopError(RuntimeError):
    """An action of the desktop app failed; the message holds no secret or private path."""


@dataclass(frozen=True)
class ChildRow:
    server_id: str
    state: str
    tone: str


@dataclass(frozen=True)
class HubSnapshot:
    hub: str
    activity_ui: bool = False
    children: tuple[ChildRow, ...] = ()
    clients: tuple[str, ...] = ()
    checked_at: datetime = field(default_factory=datetime.now)

    @property
    def tone(self) -> str:
        return HUB_TONES[self.hub]


def child_tone(state: str) -> str:
    """`connected` is ok, `connecting`/`reconnecting` wait, `disabled` off, anything else an error."""
    return _CHILD_TONES.get(state, "error")


def client_labels(clients: list[dict[str, Any]]) -> tuple[str, ...]:
    """Live AI clients only, as `name` or `name ×N` for several connections of one client."""
    counts: Counter[str] = Counter()
    for client in clients:
        if isinstance(client, dict) and client.get("live"):
            connections = client.get("connections")
            counts[str(client.get("client") or "unknown")] += connections if isinstance(connections, int) and connections > 0 else 1
    return tuple(name if count == 1 else f"{name} ×{count}" for name, count in counts.items())


def snapshot_from_status(status: dict[str, Any]) -> HubSnapshot:
    children = status.get("children") if isinstance(status.get("children"), dict) else {}
    extensions = status.get("extensions") if isinstance(status.get("extensions"), dict) else {}
    clients = status.get("clients") if isinstance(status.get("clients"), list) else []
    return HubSnapshot(
        hub="running",
        activity_ui=bool(extensions.get("activityUi")),
        children=tuple(ChildRow(str(server_id), str(state), child_tone(str(state))) for server_id, state in children.items()),
        clients=client_labels(clients),
    )


def base_url(config: ServiceConfig) -> str:
    return f"http://{config.host}:{config.port}"


def request_json(opener: Opener, url: str, token: str | None = None, method: str = "GET") -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    request = urllib.request.Request(url, data=b"" if method == "POST" else None, headers=headers, method=method)
    with opener(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        value = json.loads(response.read().decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("The Hub returned an unexpected response")
    return value


def fetch_snapshot(*, opener: Opener = _OPENER.open, private_root: Path | None = None) -> HubSnapshot:
    """One status read: never raises, every failure maps to a Hub state."""
    try:
        config = _load_config(private_root)
    except ServiceConfigError:
        return HubSnapshot(hub="not_installed")
    url = base_url(config)
    try:
        if request_json(opener, f"{url}/health").get("ok") is not True:
            return HubSnapshot(hub="stopped")
        status = request_json(opener, f"{url}/dashboard/api/status", config.token)
    except HTTPError as exc:
        return HubSnapshot(hub="unauthorized" if exc.code == 401 else "stopped")
    except (URLError, OSError, ValueError):
        return HubSnapshot(hub="stopped")
    return snapshot_from_status(status)


def open_dashboard(
    *,
    opener: Opener = _OPENER.open,
    browser: Callable[[str], Any] = webbrowser.open,
    private_root: Path | None = None,
) -> None:
    """Pair a browser session with the bearer token, then open the dashboard (as `hubctl open` does)."""
    try:
        config = _load_config(private_root)
        pairing = request_json(opener, f"{base_url(config)}/dashboard/pair", config.token, method="POST")
    except (ServiceConfigError, OSError, ValueError) as exc:
        raise DesktopError(f"Dashboard pairing failed ({type(exc).__name__})") from exc
    token = pairing.get("pairing_token")
    if not isinstance(token, str) or not token:
        raise DesktopError("Dashboard pairing failed (no pairing token)")
    browser(f"{base_url(config)}/dashboard/?pair={quote(token, safe='')}")


def _load_config(private_root: Path | None) -> ServiceConfig:
    if private_root is None:
        return load_service_config(create=False)
    return load_service_config(private_root=private_root, create=False)

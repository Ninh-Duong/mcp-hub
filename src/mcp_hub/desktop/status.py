"""What the desktop status app shows, read from the running Hub's HTTP service.

The Hub's URL and bearer token come from its connection file (connection.py). The token is only ever
sent to the Hub on 127.0.0.1; it is never displayed or logged.
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

from .connection import ConnectionMissing, load_connection

REQUEST_TIMEOUT_SECONDS = 3.0
TONES = ("ok", "wait", "off", "error")
# Hub states the app can show; `starting` is set by the app itself after Start Hub.
HUB_TONES = {"running": "ok", "starting": "wait", "stopped": "error", "not_installed": "off", "unauthorized": "error"}
_CHILD_TONES = {"connected": "ok", "connecting": "wait", "reconnecting": "wait", "disabled": "off"}
# BitNet's dot: green only when llama-server answers; anything still in progress waits.
_BITNET_TONES = {"ready": "ok", "installing": "wait", "starting": "wait", "restarting": "wait", "unavailable": "error"}


def loopback_opener() -> urllib.request.OpenerDirector:
    """Plain HTTP only, for the Hub on 127.0.0.1. Without a ProxyHandler no proxy configured for other traffic
    ever sees the token, and without an HTTPS handler no TLS setup runs (it can fail in the installed app)."""
    opener = urllib.request.OpenerDirector()
    for handler in (urllib.request.UnknownHandler(), urllib.request.HTTPHandler(),
                    urllib.request.HTTPDefaultErrorHandler(), urllib.request.HTTPErrorProcessor()):
        opener.add_handler(handler)
    return opener


OPENER = loopback_opener()
Opener = Callable[..., Any]


class DesktopError(RuntimeError):
    """An action of the desktop app failed; the message holds no secret or private path."""


@dataclass(frozen=True)
class ChildRow:
    server_id: str
    state: str
    tone: str
    # Started with required settings still empty: shown as a warning even though the child runs.
    incomplete: bool = False


@dataclass(frozen=True)
class BitnetInfo:
    """The Hub's `bitnet` status block: installed or not, the sidecar's state, and a running install."""

    installed: bool = False
    enabled: bool = False
    state: str = "not_installed"
    restart_needed: bool = False
    installing: bool = False
    step: str | None = None
    progress: int | None = None
    error: str | None = None

    @property
    def tone(self) -> str:
        return bitnet_tone(self.state)


def bitnet_tone(state: str) -> str:
    """`ready` is ok; installing, starting, and restarting wait; `unavailable` is an error; anything else off."""
    return _BITNET_TONES.get(state, "off")


def bitnet_from_status(value: Any) -> BitnetInfo | None:
    """None for a Hub too old to report BitNet; unexpected values fall back to their defaults."""
    if not isinstance(value, dict) or not value.get("state"):
        return None
    install = value.get("install") if isinstance(value.get("install"), dict) else {}
    progress = install.get("progress")
    step, error = install.get("step"), install.get("error")
    return BitnetInfo(
        installed=value.get("installed") is True,
        enabled=value.get("enabled") is True,
        state=str(value["state"]),
        restart_needed=value.get("restart_needed") is True,
        installing=install.get("running") is True,
        step=step if isinstance(step, str) else None,
        progress=progress if isinstance(progress, int) and not isinstance(progress, bool) else None,
        error=error if isinstance(error, str) else None,
    )


@dataclass(frozen=True)
class HubSnapshot:
    hub: str
    activity_ui: bool = False
    children: tuple[ChildRow, ...] = ()
    clients: tuple[str, ...] = ()
    # The raw names behind `clients` (`name ×N` labels), in the same order.
    client_names: tuple[str, ...] = ()
    # AI clients seen in the last 30 minutes that are not connected now.
    recent: tuple[str, ...] = ()
    checked_at: datetime = field(default_factory=datetime.now)
    bitnet_state: str | None = None
    bitnet: BitnetInfo | None = None

    @property
    def tone(self) -> str:
        return HUB_TONES[self.hub]


def child_tone(state: str) -> str:
    """`connected` is ok, `connecting`/`reconnecting` wait, `disabled` off, anything else an error."""
    return _CHILD_TONES.get(state, "error")


def _child_row(server_id: str, state: str, incomplete: bool) -> ChildRow:
    """A connected child with a settings gap is a warning (wait tone), not a plain ok."""
    tone = child_tone(state)
    if incomplete and tone == "ok":
        tone = "wait"
    return ChildRow(server_id, state, tone, incomplete=incomplete)


def count_labels(counts: Counter[str]) -> tuple[str, ...]:
    """`name`, or `name ×N` when a client holds several connections or calls."""
    return tuple(name if count == 1 else f"{name} ×{count}" for name, count in counts.items())


def _live_counts(clients: list[dict[str, Any]]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for client in clients:
        if isinstance(client, dict) and client.get("live"):
            connections = client.get("connections")
            counts[str(client.get("client") or "unknown")] += connections if isinstance(connections, int) and connections > 0 else 1
    return counts


def client_labels(clients: list[dict[str, Any]]) -> tuple[str, ...]:
    """Live AI clients only, as `name` or `name ×N` for several connections of one client."""
    return count_labels(_live_counts(clients))


def live_client_names(clients: list[dict[str, Any]]) -> tuple[str, ...]:
    """The names behind `client_labels`, in the same order."""
    return tuple(_live_counts(clients))


def recent_clients(clients: list[dict[str, Any]]) -> tuple[str, ...]:
    """Clients the Hub saw recently that have no live connection, each name once."""
    live = {str(client.get("client") or "unknown") for client in clients if isinstance(client, dict) and client.get("live")}
    names = (str(client.get("client") or "unknown") for client in clients if isinstance(client, dict) and not client.get("live"))
    return tuple(dict.fromkeys(name for name in names if name not in live))


def snapshot_from_status(status: dict[str, Any]) -> HubSnapshot:
    children = status.get("children") if isinstance(status.get("children"), dict) else {}
    extensions = status.get("extensions") if isinstance(status.get("extensions"), dict) else {}
    clients = status.get("clients") if isinstance(status.get("clients"), list) else []
    bitnet = status.get("bitnet") if isinstance(status.get("bitnet"), dict) else {}
    bitnet_state = str(bitnet.get("state")) if bitnet.get("state") else None
    incomplete = status.get("config_incomplete") if isinstance(status.get("config_incomplete"), dict) else {}
    return HubSnapshot(
        hub="running",
        activity_ui=bool(extensions.get("activityUi")),
        children=tuple(_child_row(str(server_id), str(state), str(server_id) in incomplete)
                       for server_id, state in children.items()),
        clients=client_labels(clients),
        client_names=live_client_names(clients),
        recent=recent_clients(clients),
        bitnet_state=bitnet_state,
        bitnet=bitnet_from_status(bitnet),
    )


def request_json(
    opener: Opener, url: str, token: str | None = None, method: str = "GET", body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    data = None
    if method == "POST":
        data = json.dumps(body or {}).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    with opener(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        value = json.loads(response.read().decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("The Hub returned an unexpected response")
    return value


def fetch_snapshot(*, opener: Opener = OPENER.open, connection_file: Path | None = None) -> HubSnapshot:
    """One status read: never raises, every failure maps to a Hub state."""
    try:
        connection = load_connection(connection_file)
    except ConnectionMissing:
        return HubSnapshot(hub="not_installed")
    try:
        if request_json(opener, f"{connection.url}/health").get("ok") is not True:
            return HubSnapshot(hub="stopped")
        status = request_json(opener, f"{connection.url}/dashboard/api/status", connection.token)
    except HTTPError as exc:
        return HubSnapshot(hub="unauthorized" if exc.code == 401 else "stopped")
    except (URLError, OSError, ValueError):
        return HubSnapshot(hub="stopped")
    return snapshot_from_status(status)


def open_dashboard(
    *,
    opener: Opener = OPENER.open,
    browser: Callable[[str], Any] = webbrowser.open,
    connection_file: Path | None = None,
) -> None:
    """Pair a browser session with the bearer token, then open the dashboard (as `hubctl open` does)."""
    try:
        connection = load_connection(connection_file)
        pairing = request_json(opener, f"{connection.url}/dashboard/pair", connection.token, method="POST")
    except (OSError, ValueError) as exc:
        raise DesktopError(f"Dashboard pairing failed ({type(exc).__name__})") from exc
    token = pairing.get("pairing_token")
    if not isinstance(token, str) or not token:
        raise DesktopError("Dashboard pairing failed (no pairing token)")
    browser(f"{connection.url}/dashboard/?pair={quote(token, safe='')}")

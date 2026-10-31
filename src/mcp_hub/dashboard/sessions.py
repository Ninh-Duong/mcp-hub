from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from ..settings import DashboardSettings


def _now() -> datetime:
    return datetime.now(timezone.utc)


class DashboardSessions:
    """A pairing token opens the dashboard once, shortly after `hubctl open`; the browser session it starts
    lasts a day's work. Both lifetimes come from settings.dashboard."""

    def __init__(self, *, clock=_now, settings: DashboardSettings = DashboardSettings()) -> None:
        self.clock = clock
        self.pairing_ttl = timedelta(seconds=settings.pairing_seconds)
        self.session_ttl = timedelta(hours=settings.session_hours)
        self.pairing_tokens: dict[str, datetime] = {}
        self.sessions: dict[str, datetime] = {}

    def issue_pairing_token(self) -> str:
        self._purge()
        token = secrets.token_urlsafe(32)
        self.pairing_tokens[self._digest(token)] = self.clock() + self.pairing_ttl
        return token

    def is_pairing_valid(self, token: str | None) -> bool:
        if not token:
            return False
        self._purge()
        expiry = self.pairing_tokens.get(self._digest(token))
        return expiry is not None and expiry > self.clock()

    def consume_pairing_token(self, token: str | None) -> str | None:
        if not token or not self.is_pairing_valid(token):
            return None
        self.pairing_tokens.pop(self._digest(token), None)
        session = secrets.token_urlsafe(32)
        self.sessions[self._digest(session)] = self.clock() + self.session_ttl
        return session

    def is_session_valid(self, token: str | None) -> bool:
        if not token:
            return False
        self._purge()
        expiry = self.sessions.get(self._digest(token))
        return expiry is not None and expiry > self.clock()

    def _purge(self) -> None:
        now = self.clock()
        self.pairing_tokens = {key: expiry for key, expiry in self.pairing_tokens.items() if expiry > now}
        self.sessions = {key: expiry for key, expiry in self.sessions.items() if expiry > now}

    @staticmethod
    def _digest(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

"""Playbook confirmation for `hub__plan`: the Hub offers the playbooks that fit a task, and plans only after the
user picks one.

The offer carries a `confirm_token`. A token is single use, bound to the task text, limited to the offered
choices, and expires after `policy.confirmTokenSeconds`. Pure logic: the Hub runtime makes the offer, the
refusal, and the audit events.
"""
from __future__ import annotations

import hashlib
import re
import secrets
import time
from collections.abc import Callable, Iterable, Mapping

from .playbooks import Playbook
from .policy import PolicyRefused

CONFIRM_ARG = "confirm_token"
# The user chose a plan without a playbook: the servers and steps the agent declares.
NONE_CHOICE = "none"
MODES = ("always", "ambiguous", "off")

_WORD = re.compile(r"[a-z0-9]{3,}")
# Words that match every playbook's wording and so say nothing about the task.
_STOP = frozenset({"the", "and", "for", "with", "that", "this", "from", "user", "wants", "gives", "when", "what"})


def _words(text: str) -> set[str]:
    return set(_WORD.findall(text.lower())) - _STOP


def _scores(task: str, playbooks: Mapping[str, Playbook]) -> dict[str, int]:
    words = _words(task)
    return {pb_id: len(words & _words(playbook.when)) for pb_id, playbook in playbooks.items()}


def choices(task: str, playbooks: Mapping[str, Playbook]) -> list[tuple[str, str]]:
    """(playbook ID or NONE_CHOICE, what it is for), best match first; the no-playbook choice is always last."""
    scores = _scores(task, playbooks)
    ranked = sorted(playbooks, key=lambda pb_id: (-scores[pb_id], pb_id))
    listed = [(pb_id, playbooks[pb_id].when) for pb_id in ranked]
    return [*listed, (NONE_CHOICE, "no playbook: a custom plan with the child servers and steps you declare")]


def needs_confirmation(mode: str, task: str, playbooks: Mapping[str, Playbook]) -> bool:
    """`always`: every plan without a confirmed choice; `ambiguous`: unless exactly one playbook matches; `off`: never."""
    if mode == "off":
        return False
    if mode == "always":
        return True
    return sum(1 for score in _scores(task, playbooks).values() if score > 0) != 1


def offer_text(token: str, options: Iterable[tuple[str, str]]) -> str:
    listed = "\n".join(f"- {choice}: {what}" for choice, what in options)
    return (
        "No plan yet: confirm the playbook with the user first. Ask which of these fits their request, then call "
        f"hub__plan again with the same task, the chosen playbook (omit it for {NONE_CHOICE}), and "
        f"{CONFIRM_ARG}: {token}.\n"
        f"Choices:\n{listed}"
    )


def _digest(task: str) -> str:
    return hashlib.sha256(task.strip().encode("utf-8")).hexdigest()


class ConfirmTokens:
    """Issued offers by token: each one is used once, for one task, and only for the choices it offered."""

    def __init__(self, ttl_seconds: float, clock: Callable[[], float] = time.monotonic) -> None:
        self.ttl_seconds = ttl_seconds
        self.clock = clock
        self._issued: dict[str, tuple[float, str, frozenset[str]]] = {}

    def issue(self, task: str, offered: Iterable[str]) -> str:
        self._prune()
        token = secrets.token_urlsafe(16)
        self._issued[token] = (self.clock() + self.ttl_seconds, _digest(task), frozenset(offered))
        return token

    def redeem(self, token: object, task: str, choice: str) -> None:
        """Use a token for one choice; PolicyRefused says what to do when it does not fit."""
        entry = self._issued.pop(token, None) if isinstance(token, str) else None
        if entry is None or self.clock() > entry[0]:
            raise PolicyRefused(
                f"'{CONFIRM_ARG}' is unknown, already used, or expired. Ask the user again: call hub__plan "
                "without it to get a new offer."
            )
        _expires, digest, offered = entry
        if digest != _digest(task):
            raise PolicyRefused(f"'{CONFIRM_ARG}' was issued for another task. Ask the user again for this one.")
        if choice not in offered:
            raise PolicyRefused(f"'playbook' must be one of the offered choices: {', '.join(sorted(offered))}.")

    def _prune(self) -> None:
        now = self.clock()
        for token in [token for token, entry in self._issued.items() if now > entry[0]]:
            del self._issued[token]

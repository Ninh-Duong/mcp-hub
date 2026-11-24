"""Start-up inputs shared by the shared service (`service.py`) and the stdio entry point (`server.serve`).

Settings, the Hub config, the playbooks, and the projects are read in one place. A bad input stops the start and is
logged once as `config.load.failed`, the same event for both entry points.
"""
from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass

from .audit import AuditLog
from .client_profiles import ClientProfileCatalog, load_client_profiles
from .config import HubConfig, load_config
from .planning.playbooks import Playbook, load_playbooks
from .projects import ProjectConfig, load_projects
from .settings import Settings, load_settings


@dataclass(frozen=True)
class StartInputs:
    settings: Settings
    config: HubConfig
    playbooks: Mapping[str, Playbook]
    projects: Mapping[str, ProjectConfig]
    client_profiles: ClientProfileCatalog | None = None


def _log_failure(audit: AuditLog, exc: Exception) -> None:
    audit.write("config.load.failed", level=logging.ERROR, error_type=type(exc).__name__, error=str(exc))


def load_settings_logged() -> Settings:
    """The Hub config's `settings` block. A bad value stops the start; the failure is logged to a default audit log."""
    try:
        return load_settings()
    except Exception as exc:
        audit = AuditLog()
        _log_failure(audit, exc)
        audit.close()
        raise


def load_start_inputs(audit: AuditLog, settings: Settings | None = None) -> StartInputs:
    """Read what the Hub needs to start. Pass `settings` when the caller already read them (the service does, to pick its port)."""
    try:
        resolved = load_settings() if settings is None else settings
        config = load_config()
        playbooks = load_playbooks()
        projects = load_projects()
        client_profiles = load_client_profiles()
    except Exception as exc:
        _log_failure(audit, exc)
        raise
    return StartInputs(
        settings=resolved,
        config=config,
        playbooks=playbooks,
        projects=projects,
        client_profiles=client_profiles,
    )

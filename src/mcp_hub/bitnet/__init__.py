"""Optional local BitNet extension; see docs/bitnet.md."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..audit import AuditLog
from ..dashboard.api import DashboardExtension
from ..full_results import FullResultStore
from ..server import HubRuntime
from .client import BitnetClient
from .condense import BitnetCondenser
from .planner import BitnetPlanAssistant
from .setup import BitnetSetup
from .sidecar import Sidecar
from .summarize import BitnetDraftSummarizer


@dataclass
class BitnetExtension:
    sidecar: Sidecar
    setup: BitnetSetup

    def dashboard_extension(self) -> DashboardExtension:
        # The setup's status includes the sidecar's state; its route installs while BitNet is on, too.
        return DashboardExtension(name="bitnet", routes=self.setup.routes(), status_fields=self.setup.status_fields)

    async def start(self) -> None:
        await self.sidecar.start()

    async def close(self) -> None:
        await self.setup.close()
        await self.sidecar.stop()


def install_setup(runtime: HubRuntime, *, root: Path, audit: AuditLog | None) -> BitnetSetup:
    """The install state and route only, for a Hub that runs without the BitNet extension."""
    return BitnetSetup(root, runtime.settings.bitnet, audit)


def install(runtime: HubRuntime, job_runner: Any, *, root: Path, audit: AuditLog) -> BitnetExtension:
    settings = runtime.settings.bitnet
    client = BitnetClient(settings.port)
    runtime.plan_assistant = BitnetPlanAssistant(client, timeout=settings.plan_timeout_seconds)
    runtime.condenser = BitnetCondenser(
        client, max_chars=settings.condense_min_chars, timeout=settings.condense_timeout_seconds,
    )
    runtime.full_results = FullResultStore(settings.full_result_max_entries, settings.full_result_ttl_seconds)
    if job_runner is not None:
        job_runner.plan_assistant = runtime.plan_assistant
        job_runner.condenser = runtime.condenser
        job_runner.summarizer = BitnetDraftSummarizer(client)
    sidecar = Sidecar(root, settings, audit, log_dir=getattr(runtime, "child_log_dir", None))
    return BitnetExtension(sidecar, BitnetSetup(root, settings, audit, sidecar=sidecar))

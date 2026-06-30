from __future__ import annotations

from pydantic import BaseModel

from evander.config import (
    AdoWorkerGlobalConfig,
    DiscordIngestWorkerGlobalConfig,
    DiscordWakeWorkerGlobalConfig,
    ProxyIssueSyncWorkerGlobalConfig,
    ScheduledWakeWorkerGlobalConfig,
    TeamsIngestWorkerGlobalConfig,
    TeamsMeetingIngestWorkerGlobalConfig,
    TriggerOrchestratorWorkerGlobalConfig,
)

_WORKER_DEFINITIONS: tuple[tuple[str, str, str], ...] = (
    ("ado", "ADO Worker", "evander-ado-worker.service"),
    (
        "proxy-issue-sync",
        "Proxy Issue Sync Worker",
        "evander-proxy-issue-sync.service",
    ),
    ("discord-ingest", "Discord Ingest Worker", "evander-discord-ingest.service"),
    ("discord-wake", "Discord Wake Worker", "evander-discord-wake.service"),
    ("scheduled-wake", "Scheduled Wake Worker", "evander-scheduled-wake.service"),
    ("teams-ingest", "Teams Ingest Worker", "evander-teams-ingest.service"),
    (
        "teams-meeting-ingest",
        "Teams Meeting Ingest Worker",
        "evander-teams-meeting-ingest.service",
    ),
    (
        "effect-orchestrator",
        "Effect Orchestrator Worker",
        "evander-effect-orchestrator.service",
    ),
    (
        "trigger-orchestrator",
        "Trigger Orchestrator Worker",
        "evander-trigger-orchestrator.service",
    ),
)

_WORKER_GLOBAL_CONFIG_PATHS: dict[str, str] = {
    "ado": "config/ado-worker/config.json",
    "proxy-issue-sync": "config/proxy-issue-sync/config.json",
    "discord-ingest": "config/discord-ingest/config.json",
    "discord-wake": "config/discord-wake/config.json",
    "scheduled-wake": "config/scheduled-wake/config.json",
    "teams-ingest": "config/teams-ingest/config.json",
    "teams-meeting-ingest": "config/teams-meeting-ingest/config.json",
    "effect-orchestrator": "config/trigger-orchestrator/config.json",
    "trigger-orchestrator": "config/trigger-orchestrator/config.json",
}

_WORKER_GLOBAL_CONFIG_MODELS: dict[str, type[BaseModel]] = {
    "ado": AdoWorkerGlobalConfig,
    "proxy-issue-sync": ProxyIssueSyncWorkerGlobalConfig,
    "discord-ingest": DiscordIngestWorkerGlobalConfig,
    "discord-wake": DiscordWakeWorkerGlobalConfig,
    "scheduled-wake": ScheduledWakeWorkerGlobalConfig,
    "teams-ingest": TeamsIngestWorkerGlobalConfig,
    "teams-meeting-ingest": TeamsMeetingIngestWorkerGlobalConfig,
    "effect-orchestrator": TriggerOrchestratorWorkerGlobalConfig,
    "trigger-orchestrator": TriggerOrchestratorWorkerGlobalConfig,
}


def _worker_definition(worker_id: str) -> tuple[str, str, str] | None:
    normalized = worker_id.strip()
    return next((entry for entry in _WORKER_DEFINITIONS if entry[0] == normalized), None)

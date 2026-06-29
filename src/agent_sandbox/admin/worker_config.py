from __future__ import annotations

from pydantic import BaseModel

from agent_sandbox.config import (
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
    ("ado", "ADO Worker", "agent-sandbox-ado-worker.service"),
    (
        "proxy-issue-sync",
        "Proxy Issue Sync Worker",
        "agent-sandbox-proxy-issue-sync.service",
    ),
    ("discord-ingest", "Discord Ingest Worker", "agent-sandbox-discord-ingest.service"),
    ("discord-wake", "Discord Wake Worker", "agent-sandbox-discord-wake.service"),
    ("scheduled-wake", "Scheduled Wake Worker", "agent-sandbox-scheduled-wake.service"),
    ("teams-ingest", "Teams Ingest Worker", "agent-sandbox-teams-ingest.service"),
    (
        "teams-meeting-ingest",
        "Teams Meeting Ingest Worker",
        "agent-sandbox-teams-meeting-ingest.service",
    ),
    (
        "effect-orchestrator",
        "Effect Orchestrator Worker",
        "agent-sandbox-effect-orchestrator.service",
    ),
    (
        "trigger-orchestrator",
        "Trigger Orchestrator Worker",
        "agent-sandbox-trigger-orchestrator.service",
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

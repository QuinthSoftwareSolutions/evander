"""Descriptive extension contract; provider work happens only on explicit invocation."""

from __future__ import annotations

import os
from pathlib import Path

from evander.addons import AddonRegistration
from evander.config import GatewayConfig
from evander.integration_contracts import (
    AddonIntegration,
    IntegrationBackgroundWorker,
    IntegrationCheckResult,
    IntegrationConfigSection,
    IntegrationCredentialProvider,
    IntegrationReadinessCheck,
)
from .starter import TEAMS_ASSISTANT_STARTER


def _config(context):
    if context.service is not None:
        runtime = getattr(context.service, "runtime", context.service)
        return runtime.provider_config.resolve_for_directory(context.directory_path)
    if context.config is None:
        raise ValueError("Teams/Graph requires host configuration")
    return context.config


def _credential_configured(context):
    config = _config(context)
    if config.teams_backend == "graph_api":
        return bool(os.environ.get(config.teams_graph_access_token_env, "").strip())
    if config.teams_backend != "delegated_graph_api":
        return False
    if config.teams_delegated_credential_ref:
        from evander.credentials.store import CredentialStore

        metadata = CredentialStore(config.runtime_dir).metadata(
            config.teams_delegated_credential_ref
        )
        return metadata["status"] == "active"
    # A legacy file may contain several identities. This is configuration evidence
    # only; readiness never decrypts credentials or refreshes a token.
    return Path(config.teams_delegated_credential_store_path).is_file()


def _resolve_credential(context):
    config = _config(context)
    if config.teams_backend == "graph_api":
        token = os.environ.get(config.teams_graph_access_token_env, "").strip()
        if not token:
            raise ValueError("Teams access token is unavailable")
        return {"access_token": token}
    if config.teams_backend != "delegated_graph_api":
        raise ValueError("No Graph Teams backend is configured")
    from evander.runtime import _build_delegated_teams_token_provider

    provider = _build_delegated_teams_token_provider(config, context.directory_path)
    return {"access_token": provider.get_bearer_token()}


def _configuration_ready(context):
    config = _config(context)
    if config.teams_backend == "stub":
        return IntegrationCheckResult("unknown", "teams_backend_not_configured")
    if config.teams_backend == "delegated_graph_api" and not (
        config.teams_delegated_identity_id and config.teams_delegated_client_id
    ):
        return IntegrationCheckResult("blocked", "teams_identity_not_configured")
    if not _credential_configured(context):
        return IntegrationCheckResult("blocked", "teams_credential_not_configured")
    return IntegrationCheckResult("ready", "teams_configuration_ready")


def _worker_factory(kind):
    def create(context):
        from .workers import create_worker

        return create_worker(kind, _config(context), context.project_dir)

    return create


def register_addon():
    schema = GatewayConfig.model_json_schema()
    settings = {
        "type": "object",
        "properties": {
            name: value for name, value in schema["properties"].items() if name.startswith("teams_")
        },
        "$defs": schema.get("$defs", {}),
        "additionalProperties": False,
    }
    return AddonRegistration(
        addon_id="teams-graph",
        display_name="Microsoft Teams and Graph",
        requires_gateway_restart=True,
        metadata={
            "description": "Teams messaging, Graph mail, and meeting/voice integration.",
            "configuration": "Existing directory provider settings and credential references.",
        },
        integration=AddonIntegration(
            workflow_starters=(TEAMS_ASSISTANT_STARTER,),
            settings=(IntegrationConfigSection("teams", "Teams provider settings", settings),),
            credentials=(
                IntegrationCredentialProvider(
                    "teams-identity",
                    "Teams identity",
                    _credential_configured,
                    _resolve_credential,
                    required=False,
                ),
            ),
            readiness_checks=(
                IntegrationReadinessCheck(
                    "teams-configuration",
                    "Teams configuration",
                    _configuration_ready,
                ),
            ),
            workers=tuple(
                IntegrationBackgroundWorker(worker_id, label, _worker_factory(worker_id))
                for worker_id, label in (
                    ("teams-ingest", "Teams message ingestion"),
                    ("teams-notifications", "Graph change notifications"),
                    ("teams-meeting-ingest", "Teams meeting ingestion"),
                )
            ),
        ),
    )

"""Evander connectors commands."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated

import typer

from evander.audit import AuditLogger
from evander.communication_artifacts import CommunicationArtifactStore
from evander.config import GatewayConfig
from evander.connectors.models import normalize_connector_id
from evander.connectors.service import ConnectorService, connector_registry_from_addons
from evander.models import AuditEvent, SessionManifest, ToolName

from . import cli_app, cli_common, cli_runtime


def _connector_service_for_option(config: Path) -> tuple[ConnectorService, GatewayConfig]:
    cfg = cli_runtime._load_runtime_config_from_option(config)
    runtime = cli_runtime.build_runtime(cfg)
    service = ConnectorService(
        runtime_dir=cfg.runtime_dir,
        provider_config=runtime.provider_config,
        artifact_store_factory=lambda: CommunicationArtifactStore(cfg.runtime_dir),
        registry=connector_registry_from_addons(
            cfg.addon_loader,
            base_dir=cli_runtime._project_dir_for_config_option(config),
        ),
    )
    return service, cfg


@cli_app.connectors_app.command("list", short_help="List effective connectors for a directory.")
def connectors_list(
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
    directory: Annotated[str, typer.Option("--directory")] = "root",
) -> None:
    service, _ = _connector_service_for_option(config)
    connector_config = service.provider_config.resolve_connectors_for_directory(directory)
    service.validate_config(connector_config)
    cli_common._echo_model_json(
        [
            {
                "connector_id": connector_id,
                "display_name": connector.display_name,
                "description": connector.description,
                "driver": connector.driver,
                "disabled": connector.disabled,
                "operations": sorted(
                    name
                    for name, operation in connector.operations.items()
                    if not operation.disabled
                ),
                "auth_status": service.auth_status(connector_config, connector),
            }
            for connector_id, connector in sorted(connector_config.connectors.items())
        ]
    )


@cli_app.connectors_app.command("show", short_help="Show one effective connector definition.")
def connectors_show(
    connector_id: str,
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
    directory: Annotated[str, typer.Option("--directory")] = "root",
) -> None:
    service, _ = _connector_service_for_option(config)
    connector_config = service.provider_config.resolve_connectors_for_directory(directory)
    normalized = normalize_connector_id(connector_id)
    connector = connector_config.connectors.get(normalized)
    if connector is None:
        raise typer.BadParameter(f"unknown connector: {normalized}")
    service.registry.require(connector.driver).validate_definition(connector)
    cli_common._echo_model_json(
        {
            "connector_id": normalized,
            **connector.model_dump(mode="json", exclude_none=True),
            "auth_status": service.auth_status(connector_config, connector),
        }
    )


@cli_app.connectors_app.command(
    "validate", short_help="Validate effective connector configuration."
)
def connectors_validate(
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
    directory: Annotated[str, typer.Option("--directory")] = "root",
) -> None:
    service, _ = _connector_service_for_option(config)
    connector_config = service.provider_config.resolve_connectors_for_directory(directory)
    service.validate_config(connector_config)
    cli_common._echo_model_json(
        {
            "status": "ok",
            "directory_path": directory,
            "connector_count": len(connector_config.connectors),
            "driver_names": list(service.registry.driver_names),
        }
    )


@cli_app.connectors_app.command("test", short_help="Run a connector's configured health operation.")
def connectors_test(
    connector_id: str,
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
    directory: Annotated[str, typer.Option("--directory")] = "root",
) -> None:
    service, cfg = _connector_service_for_option(config)
    connector_config = service.provider_config.resolve_connectors_for_directory(directory)
    normalized = normalize_connector_id(connector_id)
    connector = connector_config.connectors.get(normalized)
    if connector is None or connector.disabled:
        raise typer.BadParameter(f"unknown or disabled connector: {normalized}")
    if connector.health_operation is None:
        raise typer.BadParameter("connector does not define health_operation")
    session = SessionManifest(
        session_id=f"cli-connector-test-{normalized}",
        directory_path=directory,
        allowed_tools=[ToolName.CALL_CONNECTOR],
        connector_grants={normalized: [connector.health_operation]},
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    audit = AuditLogger(cfg.audit_log_path)
    try:
        result = asyncio.run(
            service.invoke(
                session=session,
                connector_id=normalized,
                operation_name=connector.health_operation,
                arguments=dict(connector.health_arguments),
            )
        )
    except Exception as exc:
        audit.log(
            AuditEvent(
                event_type="connector_test",
                session_id=session.session_id,
                directory_path=directory,
                tool_name=ToolName.CALL_CONNECTOR.value,
                connector_id=normalized,
                connector_operation=connector.health_operation,
                actor_user_id="cli-operator",
                decision="error",
                result={"error_class": type(exc).__name__},
            )
        )
        raise
    audit.log(
        AuditEvent(
            event_type="connector_test",
            session_id=session.session_id,
            directory_path=directory,
            tool_name=ToolName.CALL_CONNECTOR.value,
            connector_id=normalized,
            connector_operation=connector.health_operation,
            actor_user_id="cli-operator",
            decision="allowed",
            result={
                "result_mode": result.result_mode,
                "upstream_status": result.upstream_status,
                "duration_ms": result.duration_ms,
            },
        )
    )
    cli_common._echo_model_json(result)

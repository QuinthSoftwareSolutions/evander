"""Evander workers commands."""

from __future__ import annotations

import importlib
import logging
from pathlib import Path
from typing import Annotated

import typer

from evander.config import GatewayConfig
from evander.daemons.ado_issue_worker import run_worker_command
from evander.daemons.effect_orchestrator import build_effect_orchestrator_worker
from evander.daemons.proxy_issue_sync import (
    DEFAULT_PROXY_ISSUE_SYNC_FRESHNESS_SECONDS,
    DEFAULT_PROXY_ISSUE_SYNC_INTERVAL_SECONDS,
    run_proxy_issue_sync_worker,
)
from evander.daemons.proxy_issue_sync import run_once_from_runtime as run_proxy_issue_sync_once
from evander.daemons.trigger_orchestrator import build_trigger_orchestrator_worker
from evander.messaging.scheduled_wake import build_scheduled_wake_worker

from . import cli_app, cli_common, cli_runtime


def _configure_worker_logging(log_level: str) -> None:
    logging_level = getattr(logging, log_level.upper(), None)
    if not isinstance(logging_level, int):
        raise typer.BadParameter(f"Invalid --log-level value: {log_level}")
    logging.basicConfig(
        level=logging_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def _build_optional_worker(
    cfg: GatewayConfig,
    *,
    project_dir: Path,
    module_name: str,
    builder_name: str,
    worker_label: str,
):
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name == module_name:
            raise RuntimeError(
                f"{worker_label} worker module {module_name} is not available."
            ) from exc
        raise
    builder = getattr(module, builder_name, None)
    if builder is None:
        raise RuntimeError(
            f"{worker_label} worker module does not export {builder_name}(config, *, project_dir=...)."
        )
    return builder(cfg, project_dir=project_dir)


def _build_teams_ingest_worker(cfg: GatewayConfig, *, project_dir: Path):
    return _build_optional_worker(
        cfg,
        project_dir=project_dir,
        module_name="evander.messaging.teams_ingest",
        builder_name="build_teams_ingest_worker",
        worker_label="Teams ingest",
    )


def _build_teams_meeting_ingest_worker(cfg: GatewayConfig, *, project_dir: Path):
    return _build_optional_worker(
        cfg,
        project_dir=project_dir,
        module_name="evander.messaging.teams_meeting_ingest",
        builder_name="build_teams_meeting_ingest_worker",
        worker_label="Teams meeting ingest",
    )


@cli_app.app.command(
    "force-complete-await-agent",
    rich_help_panel="Background Workers",
    short_help="Force-complete a waiting await-agent dispatch using current VM state.",
)
def force_complete_await_agent(
    dispatch_id: str,
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
    log_level: Annotated[str, typer.Option("--log-level", help="Python logging level.")] = "INFO",
) -> None:
    _configure_worker_logging(log_level)

    cfg = cli_runtime._load_runtime_config_from_option(config)
    worker = build_effect_orchestrator_worker(
        cfg, project_dir=cli_runtime._project_dir_for_config_option(config)
    )
    if not worker.force_complete_await_agent(dispatch_id):
        raise typer.BadParameter(f"Unable to force-complete await-agent dispatch: {dispatch_id}")
    typer.echo(f"Force-completed await-agent dispatch {dispatch_id}")


@cli_app.app.command(
    "teams-ingest",
    rich_help_panel="Background Workers",
    short_help="Run the Teams ingest worker.",
)
def teams_ingest(
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
    run_once: Annotated[
        bool,
        typer.Option("--once", help="Run a single Teams ingest cycle and exit."),
    ] = False,
    log_level: Annotated[str, typer.Option("--log-level", help="Python logging level.")] = "INFO",
) -> None:
    _configure_worker_logging(log_level)

    cfg = cli_runtime._load_runtime_config_from_option(config)
    worker = _build_teams_ingest_worker(
        cfg, project_dir=cli_runtime._project_dir_for_config_option(config)
    )
    if run_once:
        result = worker.run_once_with_lock()
        typer.echo(
            f"Teams ingest worker cycle complete directories={result.directories_polled} "
            f"sessions={result.sessions_seen} "
            f"new_messages={result.new_messages_discovered} "
            f"notifications_processed={getattr(result, 'notifications_processed', 0)} "
            f"trigger_wake_notified={result.trigger_wake_notified} "
            f"sleep_seconds={result.next_sleep_seconds:.3f}"
        )
        return

    worker.run_forever()


@cli_app.app.command(
    "teams-meeting-ingest",
    rich_help_panel="Background Workers",
    short_help="Run the Teams meeting media ingest worker.",
)
def teams_meeting_ingest(
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
    run_once: Annotated[
        bool,
        typer.Option("--once", help="Run a single Teams meeting ingest cycle and exit."),
    ] = False,
    log_level: Annotated[str, typer.Option("--log-level", help="Python logging level.")] = "INFO",
) -> None:
    _configure_worker_logging(log_level)

    cfg = cli_runtime._load_runtime_config_from_option(config)
    worker = _build_teams_meeting_ingest_worker(
        cfg, project_dir=cli_runtime._project_dir_for_config_option(config)
    )
    if run_once:
        result = worker.run_once_with_lock()
        typer.echo(
            f"Teams meeting ingest worker cycle complete meetings={result.meetings_polled} "
            f"events={result.events_ingested} errors={result.errors_recorded} "
            f"sleep_seconds={result.next_sleep_seconds:.3f}"
        )
        return

    worker.run_forever()


@cli_app.app.command(
    "scheduled-wake",
    rich_help_panel="Background Workers",
    short_help="Run the scheduled wake worker.",
)
def scheduled_wake(
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
    run_once: Annotated[
        bool,
        typer.Option("--once", help="Run a single scheduled wake cycle and exit."),
    ] = False,
    log_level: Annotated[str, typer.Option("--log-level", help="Python logging level.")] = "INFO",
) -> None:
    _configure_worker_logging(log_level)

    cfg = cli_runtime._load_runtime_config_from_option(config)
    worker = build_scheduled_wake_worker(
        cfg, project_dir=cli_runtime._project_dir_for_config_option(config)
    )
    if run_once:
        result = worker.run_once_with_lock()
        typer.echo(
            f"Scheduled wake cycle complete due={result.requests_due} "
            f"dispatched={result.wakes_dispatched} sleep_seconds={result.next_sleep_seconds:.3f}"
        )
        return

    worker.run_forever()


@cli_app.app.command(
    "trigger-orchestrator",
    rich_help_panel="Background Workers",
    short_help="Run the trigger orchestrator worker.",
)
def trigger_orchestrator(
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
    run_once: Annotated[
        bool,
        typer.Option("--once", help="Run a single trigger orchestrator cycle and exit."),
    ] = False,
    log_level: Annotated[str, typer.Option("--log-level", help="Python logging level.")] = "INFO",
) -> None:
    _configure_worker_logging(log_level)

    cfg = cli_runtime._load_runtime_config_from_option(config)
    worker = build_trigger_orchestrator_worker(
        cfg, project_dir=cli_runtime._project_dir_for_config_option(config)
    )
    if run_once:
        result = worker.run_once_with_lock()
        typer.echo(
            f"Trigger orchestrator cycle complete triggers={result.configured_triggers} "
            f"discovered={result.events_discovered} published={result.events_published} "
            f"sleep_seconds={result.next_sleep_seconds:.3f}"
        )
        return

    worker.run_forever()


@cli_app.app.command(
    "proxy-issue-sync",
    rich_help_panel="Background Workers",
    short_help="Sync local proxy issues from Azure DevOps.",
)
def proxy_issue_sync(
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
    run_once: Annotated[
        bool,
        typer.Option("--once", help="Run a single proxy issue sync cycle and exit."),
    ] = False,
    force: Annotated[
        bool,
        typer.Option("--force", help="Sync records regardless of freshness."),
    ] = False,
    interval_seconds: Annotated[
        float,
        typer.Option(
            "--interval-seconds",
            help="Daemon polling interval in seconds.",
        ),
    ] = DEFAULT_PROXY_ISSUE_SYNC_INTERVAL_SECONDS,
    freshness_window_seconds: Annotated[
        float,
        typer.Option(
            "--freshness-window-seconds",
            help="Skip records synced more recently than this many seconds unless --force is set.",
        ),
    ] = DEFAULT_PROXY_ISSUE_SYNC_FRESHNESS_SECONDS,
    log_level: Annotated[str, typer.Option("--log-level", help="Python logging level.")] = "INFO",
) -> None:
    _configure_worker_logging(log_level)
    runtime_dir = cli_runtime._runtime_dir_for_config_option(config)
    if run_once:
        result = run_proxy_issue_sync_once(
            runtime_dir=runtime_dir,
            freshness_window_seconds=freshness_window_seconds,
            force=force,
        )
        typer.echo(
            f"Proxy issue sync complete candidates={result.candidates} "
            f"skipped_fresh={result.skipped_fresh} updated={result.updated} "
            f"missing={result.missing} failed={result.failed}"
        )
        return

    run_proxy_issue_sync_worker(
        runtime_dir=runtime_dir,
        interval_seconds=interval_seconds,
        freshness_window_seconds=freshness_window_seconds,
        force=force,
    )


@cli_app.app.command(
    "poll-ado-backlog",
    rich_help_panel="Background Workers",
    short_help="Poll Azure DevOps PR feedback and completion state.",
)
def poll_ado_backlog(
    log_level: Annotated[str, typer.Option("--log-level", help="Python logging level.")] = "INFO",
) -> None:
    run_worker_command(log_level=log_level)

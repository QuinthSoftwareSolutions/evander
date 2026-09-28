"""Evander maintenance commands."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Annotated

import typer

from evander.bootstrap import load_runtime_gateway_config
from evander.migrations import (
    migrate_directory_ownership_defaults,
    migrate_issue_setup_env_to_root,
    migrate_pr_template_to_root,
)
from evander.model_usage import DEFAULT_MODEL_PRICING_FILENAME, seed_default_model_pricing_catalog
from evander.repositories.audit_index import ToolCallAuditIndex, audit_index_path_for_log_path

from . import cli_app, cli_common, cli_runtime


@cli_app.app.command("migrate-orchestration-storage", rich_help_panel="Health and State")
def migrate_orchestration_storage_command(
    database: Annotated[
        Path, typer.Option("--database", help="Exact orchestration SQLite database path.")
    ],
    apply_migration: Annotated[
        bool, typer.Option("--apply", help="Apply resumable storage conversion.")
    ] = False,
    check: Annotated[
        bool, typer.Option("--check", help="Read-only readiness check (the default).")
    ] = False,
    offline: Annotated[
        bool,
        typer.Option("--offline", help="Confirm every process using this database is stopped."),
    ] = False,
    downgrade: Annotated[
        bool,
        typer.Option(
            "--downgrade", help="Rehydrate inline records before starting the previous release."
        ),
    ] = False,
    verify: Annotated[
        bool, typer.Option("--verify", help="Read and verify every operational payload.")
    ] = False,
    max_batches: Annotated[
        int | None,
        typer.Option("--max-batches", min=1, help="Bound this invocation; rerun to resume."),
    ] = None,
    team_database: Annotated[
        Path | None,
        typer.Option(
            "--team-database",
            help="Also check or prepare this Team activity retention index offline.",
        ),
    ] = None,
) -> None:
    """Check, migrate, or roll back operational payload storage."""
    from evander.orchestration.storage_migration import (
        check_orchestration_storage,
        migrate_orchestration_storage,
    )

    if check and apply_migration:
        raise typer.BadParameter("choose --check or --apply")
    if apply_migration and not offline:
        raise typer.BadParameter("--apply requires --offline after stopping all database users")
    if downgrade and not apply_migration:
        raise typer.BadParameter("--downgrade requires --apply --offline")
    try:
        result = (
            migrate_orchestration_storage(database, downgrade=downgrade, max_batches=max_batches)
            if apply_migration
            else check_orchestration_storage(database, verify=verify)
        )
        if apply_migration and verify:
            result["verification"] = check_orchestration_storage(database, verify=True)
        if team_database is not None:
            from evander_team_addon.sqlite_schema import (
                check_team_activity_retention_index,
                prepare_team_activity_retention_index,
            )

            if apply_migration:
                prepare_team_activity_retention_index(team_database)
            result["team_activity_retention_index_ready"] = check_team_activity_retention_index(
                team_database
            )
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(json.dumps(result, indent=2, sort_keys=True))
    if result.get("errors") or result.get("verification", {}).get("errors"):
        raise typer.Exit(code=1)


@cli_app.app.command(
    "init-state",
    rich_help_panel="Health and State",
    short_help="Create runtime state files.",
)
def init_state(config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR) -> None:
    host_cfg = cli_runtime._load_host_config_from_option(config)
    seed_default_model_pricing_catalog(
        host_cfg.runtime_dir,
        legacy_catalog_path=(
            cli_runtime._project_dir_for_config_option(config)
            / "config"
            / DEFAULT_MODEL_PRICING_FILENAME
        ),
    )
    runtime = cli_runtime.build_runtime(
        load_runtime_gateway_config(runtime_dir=host_cfg.runtime_dir)
    )
    runtime.directories.ensure_root()
    typer.echo(f"Initialized runtime state under {host_cfg.runtime_dir}")


@cli_app.app.command(
    "migrate-internal-events-to-sqlite",
    rich_help_panel="Health and State",
    short_help="Check or apply the offline internal-event SQLite cutover.",
)
def migrate_internal_events_to_sqlite(
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
    check: Annotated[
        bool,
        typer.Option("--check", help="Validate legacy stores without changing them."),
    ] = False,
    apply_migration: Annotated[
        bool,
        typer.Option("--apply", help="Import, archive, and remove the legacy stores."),
    ] = False,
    project_dir: Annotated[
        Path | None,
        typer.Option(
            "--project-dir",
            help="Evander deployment root. Defaults to the runtime directory parent.",
        ),
    ] = None,
) -> None:
    from evander.orchestration.internal_event_migration import (
        apply_internal_event_migration,
        check_internal_event_migration,
    )

    if check and apply_migration:
        raise typer.BadParameter("choose either --check or --apply")
    runtime_dir = cli_runtime._runtime_dir_for_config_option(config)
    try:
        report = (
            apply_internal_event_migration(runtime_dir, project_dir=project_dir)
            if apply_migration
            else check_internal_event_migration(runtime_dir, project_dir=project_dir)
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(json.dumps(report.payload(), indent=2, sort_keys=True))
    if report.errors:
        raise typer.Exit(code=1)


@cli_app.app.command(
    "migrate-directory-ownership",
    rich_help_panel="Health and State",
    short_help="Backfill missing directory ownership fields to root.",
)
def migrate_directory_ownership(
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
) -> None:
    cfg = cli_runtime._load_runtime_config_from_option(config)
    summary = migrate_directory_ownership_defaults(cfg)
    typer.echo(
        f"Backfilled {summary.changed_records} record(s) across {summary.changed_files} file(s)"
    )


@cli_app.app.command(
    "migrate-issue-setup-env-to-root",
    rich_help_panel="Health and State",
    short_help="Import issue-setup env settings into root issue_setup config.",
)
def migrate_issue_setup_env_root(
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
    env_file: Annotated[
        Path | None,
        typer.Option(
            "--env-file",
            help="Path to an env file to import. Current process env vars override file values.",
        ),
    ] = None,
    overwrite: Annotated[
        bool,
        typer.Option(
            "--overwrite/--no-overwrite",
            help="Replace existing root issue_setup config when it differs.",
        ),
    ] = True,
) -> None:
    cfg = cli_runtime._load_runtime_config_from_option(config)
    resolved_env_file = env_file.expanduser() if env_file is not None else None
    result = migrate_issue_setup_env_to_root(cfg, env_file=resolved_env_file, overwrite=overwrite)
    typer.echo(
        f"Issue setup env migration complete: {result.status} {result.domain} -> {result.path}"
    )


@cli_app.app.command(
    "migrate-pr-template-to-root",
    rich_help_panel="Health and State",
    short_help="Import pr-template.md into root pr_template config.",
)
def migrate_pr_template_root(
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
    template_file: Annotated[
        Path | None,
        typer.Option(
            "--template-file",
            help="Path to the legacy markdown template file to import. Defaults to config/pr-template.md.",
        ),
    ] = None,
    overwrite: Annotated[
        bool,
        typer.Option(
            "--overwrite/--no-overwrite",
            help="Replace existing root pr_template config when it differs.",
        ),
    ] = True,
) -> None:
    cfg = cli_runtime._load_runtime_config_from_option(config)
    resolved_template_file = (
        template_file.expanduser()
        if template_file is not None
        else cli_runtime._workspace_root_for_config_option(config) / "config" / "pr-template.md"
    )
    result = migrate_pr_template_to_root(
        cfg, template_file=resolved_template_file, overwrite=overwrite
    )
    typer.echo(f"PR template migration complete: {result.status} {result.domain} -> {result.path}")


@cli_app.app.command(
    "rebuild-audit-index",
    rich_help_panel="Health and State",
    short_help="Import tool-call audit JSONL entries into the SQLite audit index.",
)
def rebuild_audit_index(
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
    database_path: Annotated[
        Path | None,
        typer.Option(
            "--database-path",
            help="Override destination SQLite database path. Defaults to runtime/audit.db.",
        ),
    ] = None,
    max_lines: Annotated[
        int | None,
        typer.Option(
            "--max-lines",
            min=1,
            help="Maximum JSONL lines to import in this run. Omit to import the full log.",
        ),
    ] = None,
    reset: Annotated[
        bool,
        typer.Option(
            "--reset/--resume",
            help="Rebuild the index from the start instead of resuming saved import progress.",
        ),
    ] = True,
    json_output: Annotated[
        bool, typer.Option("--json", help="Emit machine-readable output.")
    ] = False,
) -> None:
    runtime_config = cli_runtime._load_runtime_config_from_option(config)
    destination = database_path or audit_index_path_for_log_path(runtime_config.audit_log_path)
    index = ToolCallAuditIndex(destination)
    result = index.import_from_jsonl(
        runtime_config.audit_log_path,
        max_lines=max_lines,
        reset=reset,
    )
    if result.complete:
        index.rebuild_search_index_if_needed()
    if json_output:
        typer.echo(json.dumps(result.as_dict(), indent=2, sort_keys=True))
        return
    typer.echo(f"Audit index import complete: {result.database_path}")
    typer.echo(f"source={result.source_path}")
    typer.echo(
        f"lines_scanned={result.lines_scanned} imported={result.imported} "
        f"existing={result.existing} skipped_malformed={result.skipped_malformed} "
        f"skipped_non_tool_call={result.skipped_non_tool_call}"
    )
    typer.echo(
        f"offset_bytes={result.offset_bytes} complete={result.complete} "
        f"stopped_by_limit={result.stopped_by_limit}"
    )

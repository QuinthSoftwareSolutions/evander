"""Evander setup commands."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from evander.setup import setup_doctor_payload, setup_plan_payload
from evander.setup.tui import run_setup_tui

from . import cli_app, cli_common, cli_runtime


@cli_app.setup_app.callback()
def setup_callback(
    ctx: typer.Context,
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
    project_dir: cli_common.SetupProjectDirOption = Path("."),
) -> None:
    """Launch the guided setup checklist when no setup subcommand is provided."""
    if ctx.invoked_subcommand is not None:
        return
    runtime_dir = cli_runtime._runtime_dir_for_config_option(config)
    run_setup_tui(project_dir=project_dir, runtime_dir=runtime_dir)


@cli_app.setup_app.command("doctor", short_help="Show setup checklist and host readiness.")
def setup_doctor(
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
    project_dir: cli_common.SetupProjectDirOption = Path("."),
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    runtime_dir = cli_runtime._runtime_dir_for_config_option(config)
    payload = setup_doctor_payload(project_dir=project_dir, runtime_dir=runtime_dir)
    if as_json:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    for item in payload["checklist"]:
        typer.echo(f"[{item['status']}] {item['label']}: {item['summary']}")


@cli_app.setup_app.command("plan", short_help="Show the pending guided setup launch plan.")
def setup_plan(
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
    project_dir: cli_common.SetupProjectDirOption = Path("."),
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable JSON."),
    ] = False,
) -> None:
    runtime_dir = cli_runtime._runtime_dir_for_config_option(config)
    payload = setup_plan_payload(project_dir=project_dir, runtime_dir=runtime_dir)
    if as_json:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    typer.echo("Setup plan")
    typer.echo(f"Ready: {payload['ready']}")
    for step in payload["steps"]:
        typer.echo(f"- {step}")

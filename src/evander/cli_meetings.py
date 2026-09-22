"""Evander meetings commands."""

from __future__ import annotations

from typing import Annotated

import httpx
import typer

from evander.teams_meeting import TeamsMeetingService

from . import cli_app, cli_common, cli_runtime


@cli_app.teams_meeting_app.command("start", short_help="Start a live Teams meeting agent session.")
def teams_meeting_start(
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
    directory: Annotated[
        str,
        typer.Option("--directory", help="Directory path for the dedicated meeting session."),
    ] = "root",
    join_url: Annotated[
        str,
        typer.Option("--join-url", help="Manual Teams meeting join URL for the media bot."),
    ] = "",
    model: Annotated[
        str | None,
        typer.Option("--model", help="Agent model override for the dedicated session."),
    ] = None,
    session_id: Annotated[
        str | None,
        typer.Option("--session-id", help="Optional explicit session id to create."),
    ] = None,
    origin_session_id: Annotated[
        str | None,
        typer.Option(
            "--origin-session-id",
            help="Existing agent session to project into the live meeting.",
        ),
    ] = None,
    prompt: Annotated[
        str | None,
        typer.Option("--prompt", help="Voice-session task prompt for the projected meeting agent."),
    ] = None,
    ttl_hours: Annotated[
        int | None,
        typer.Option("--ttl-hours", help="Session time to live in hours."),
    ] = None,
) -> None:
    cfg = cli_runtime._load_runtime_config_from_option(config)
    runtime = cli_runtime.build_runtime(cfg)
    service = TeamsMeetingService(
        config=cfg,
        runtime=runtime,
        store=runtime.teams_meeting_store,
        bridge_client=runtime.teams_meeting_bridge_client,
    )
    try:
        result = service.start_meeting(
            directory_path=directory,
            join_url=join_url,
            model=model,
            session_id=session_id,
            ttl_hours=ttl_hours,
            origin_session_id=origin_session_id,
            prompt=prompt,
        )
    except (ValueError, RuntimeError, httpx.HTTPError) as exc:
        typer.echo(f"Teams meeting start failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    cli_common._echo_model_json(result)


@cli_app.teams_meeting_app.command("stop", short_help="Stop a live Teams meeting agent session.")
def teams_meeting_stop(
    session_id: Annotated[str, typer.Argument(help="Teams meeting session id.")],
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
) -> None:
    cfg = cli_runtime._load_runtime_config_from_option(config)
    runtime = cli_runtime.build_runtime(cfg)
    service = TeamsMeetingService(
        config=cfg,
        runtime=runtime,
        store=runtime.teams_meeting_store,
        bridge_client=runtime.teams_meeting_bridge_client,
    )
    try:
        result = service.stop_meeting(session_id=session_id)
    except (ValueError, RuntimeError, httpx.HTTPError) as exc:
        typer.echo(f"Teams meeting stop failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    cli_common._echo_model_json(result)

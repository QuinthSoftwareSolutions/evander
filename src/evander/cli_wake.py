"""Evander wake commands."""

from __future__ import annotations

import json
from typing import Annotated

import typer

from . import cli_app, cli_common, cli_runtime


@cli_app.wake_schedules_app.command("create")
def wake_schedule_create(
    session_id: Annotated[str, typer.Option("--session-id", help="Target session id.")],
    prompt: Annotated[str, typer.Option("--prompt", help="Resume prompt.")],
    resume_at: Annotated[
        str | None, typer.Option("--resume-at", help="One-time UTC timestamp (ISO-8601).")
    ] = None,
    cron: Annotated[
        str | None, typer.Option("--cron", help="Recurring standard 5-field cron expression.")
    ] = None,
    mode: Annotated[
        str, typer.Option("--mode", help="Resume mode: same_context or new_context.")
    ] = "same_context",
    source: Annotated[
        str, typer.Option("--source", help="Schedule source tag for auditing.")
    ] = "admin_cli",
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
) -> None:
    if bool(resume_at) == bool(cron):
        raise typer.BadParameter("Provide exactly one of --resume-at or --cron.")
    service = cli_runtime.build_admin_runtime(config).operations_admin
    parsed_resume_at = cli_common._parse_utc_datetime(resume_at) if resume_at else None
    try:
        created = service.create_wake_schedule(
            session_id=session_id,
            prompt=prompt,
            resume_at=parsed_resume_at,
            cron=cron,
            resume_mode=mode,
            source=source,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(
        json.dumps(
            {
                "request_id": created.request_id,
                "session_id": created.session_id,
                "resume_at": created.schedule.resume_at.isoformat()
                if created.schedule.resume_at
                else None,
                "cron": created.schedule.cron,
                "resume_mode": created.resume_mode,
                "enabled": created.enabled,
                "next_run_at": created.next_run_at.isoformat() if created.next_run_at else None,
            },
            indent=2,
        )
    )


@cli_app.wake_schedules_app.command("list")
def wake_schedule_list(
    session_id: Annotated[
        str | None, typer.Option("--session-id", help="Filter to a specific session id.")
    ] = None,
    include_disabled: Annotated[bool, typer.Option("--include-disabled/--enabled-only")] = True,
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
) -> None:
    service = cli_runtime.build_admin_runtime(config).operations_admin
    schedules = service.list_wake_schedules(
        session_id=session_id, include_disabled=include_disabled
    )
    typer.echo(json.dumps(schedules, indent=2, default=str))


@cli_app.wake_schedules_app.command("edit")
def wake_schedule_edit(
    request_id: Annotated[str, typer.Argument(help="Wake schedule request id.")],
    prompt: Annotated[str | None, typer.Option("--prompt", help="Updated prompt text.")] = None,
    resume_at: Annotated[
        str | None, typer.Option("--resume-at", help="Updated one-time UTC timestamp (ISO-8601).")
    ] = None,
    cron: Annotated[
        str | None,
        typer.Option("--cron", help="Updated recurring standard 5-field cron expression."),
    ] = None,
    mode: Annotated[
        str | None, typer.Option("--mode", help="Updated mode: same_context or new_context.")
    ] = None,
    enabled: Annotated[
        bool | None, typer.Option("--enabled/--disabled", help="Override enabled state.")
    ] = None,
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
) -> None:
    service = cli_runtime.build_admin_runtime(config).operations_admin
    parsed_resume_at = cli_common._parse_utc_datetime(resume_at) if resume_at else None
    try:
        updated = service.edit_wake_schedule(
            request_id,
            prompt=prompt,
            resume_at=parsed_resume_at,
            cron=cron,
            resume_mode=mode,
            enabled=enabled,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(
        json.dumps(
            {
                "request_id": updated.request_id,
                "session_id": updated.session_id,
                "resume_at": updated.schedule.resume_at.isoformat()
                if updated.schedule.resume_at
                else None,
                "cron": updated.schedule.cron,
                "resume_mode": updated.resume_mode,
                "enabled": updated.enabled,
                "next_run_at": updated.next_run_at.isoformat() if updated.next_run_at else None,
            },
            indent=2,
        )
    )


@cli_app.wake_schedules_app.command("delete")
def wake_schedule_delete(
    request_id: Annotated[str, typer.Argument(help="Wake schedule request id.")],
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
) -> None:
    service = cli_runtime.build_admin_runtime(config).operations_admin
    deleted = service.delete_wake_schedule(request_id)
    if not deleted:
        raise typer.BadParameter(f"Wake schedule not found: {request_id}")
    typer.echo(f"Deleted wake schedule {request_id}")

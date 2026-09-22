"""Trusted-host privacy administration; never an anonymous web identity."""

from __future__ import annotations

import json
import os
import pwd
from pathlib import Path
from typing import Annotated

import typer

from evander.privacy.models import PrivacyError

app = typer.Typer(help="Trusted-host privacy inventory and revision-guarded operations.")
Config = Annotated[Path, typer.Option("--config", help="Evander runtime directory.")]
Directory = Annotated[str, typer.Option("--directory-path", help="Exact directory scope.")]


def service(config: Path):
    from evander.admin.composition import build_admin_runtime

    return build_admin_runtime(config).privacy_admin


def host_actor() -> str:
    """Use the kernel UID, not spoofable USER/LOGNAME environment variables."""
    uid = os.getuid()
    return f"host:{uid}:{pwd.getpwuid(uid).pw_name}"


def emit(value: dict) -> None:
    typer.echo(json.dumps(value, indent=2))


@app.command("maintenance")
def maintenance(
    config: Config = Path("runtime"),
    limit: Annotated[int, typer.Option("--limit", min=1, max=1000)] = 50,
) -> None:
    """Run one bounded maintenance pass; installation defaults are report-only."""
    try:
        emit(service(config).maintenance(limit=limit, actor=host_actor()))
    except PrivacyError as exc:
        raise typer.BadParameter(str(exc)) from exc


@app.command("status")
def status(directory_path: Directory = "root", config: Config = Path("runtime")) -> None:
    """Show coverage and metadata; unsupported adapters remain explicit."""
    try:
        emit(service(config).snapshot(directory_path))
    except PrivacyError as exc:
        raise typer.BadParameter(str(exc)) from exc


@app.command("export-review")
def export_review(
    job_id: str,
    destination: Annotated[
        Path, typer.Option("--destination", help="New private reviewer file; never overwrites.")
    ],
    directory_path: Directory = "root",
    config: Config = Path("runtime"),
) -> None:
    """Download an unexpired export for trusted-host review, not subject delivery.

    Awaiting-review exports may contain third-party information. Review and use
    the separately approved exports.release action before external disclosure.
    """
    try:
        content = service(config).export_bytes(directory_path, job_id, actor=host_actor())
        fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        emit({"review_file": str(destination), "subject_delivery": False})
    except (PrivacyError, OSError) as exc:
        raise typer.BadParameter(str(exc)) from exc


@app.command("job")
def job_detail(
    job_id: str, directory_path: Directory = "root", config: Config = Path("runtime")
) -> None:
    """Inspect persisted selection references and revision before approving a job."""
    try:
        emit(service(config).job_detail(directory_path, job_id, actor=host_actor()))
    except PrivacyError as exc:
        raise typer.BadParameter(str(exc)) from exc


@app.command("record")
def record_detail(
    kind: str, record_id: str, directory_path: Directory = "root", config: Config = Path("runtime")
) -> None:
    """Inspect persisted case, hold, incident or followup evidence in the exact scope."""
    try:
        emit(service(config).record_detail(directory_path, kind, record_id, actor=host_actor()))
    except (PrivacyError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc


@app.command("action")
def action(
    action: str,
    payload_file: Annotated[Path, typer.Option("--payload-file", exists=True, dir_okay=False)],
    expected_revision: Annotated[str, typer.Option("--expected-revision")],
    directory_path: Directory = "root",
    config: Config = Path("runtime"),
) -> None:
    """Execute a reviewed action as the OS user; CLI access trusts host permissions.

    Get the current revision with status. Payload is an action-specific JSON
    object. There is no automatic stale-revision retry or implicit approval.
    """
    try:
        if payload_file.stat().st_size > 256 * 1024:
            raise ValueError("Payload exceeds 256 KiB")
        payload = json.loads(payload_file.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("Payload must be a JSON object")
        if not expected_revision.strip():
            raise ValueError("expected_revision is required")
        emit(
            service(config).mutate(
                action,
                directory_path,
                payload,
                expected_revision=expected_revision,
                actor=host_actor(),
            )
        )
    except (PrivacyError, ValueError, OSError) as exc:
        raise typer.BadParameter(str(exc)) from exc

"""Operator commands for repository indexing; exported query.py needs no Evander."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

app = typer.Typer(help="Build, query and export repository indexes.")
Config = Annotated[Path, typer.Option("--config", help="Evander runtime directory.")]
Directory = Annotated[str, typer.Option("--directory-path")]


def service(config: Path):
    from evander.admin.composition import build_admin_runtime

    return build_admin_runtime(config).repository_index_admin


def emit(value):
    typer.echo(json.dumps(value, indent=2))


@app.command("status")
def status(
    managed_repo_id: str,
    directory_path: Directory,
    config: Config = Path("runtime"),
    commit_sha: str | None = None,
):
    emit(service(config).status(managed_repo_id, directory_path, commit_sha))


@app.command("configure")
def configure(
    managed_repo_id: str,
    settings_file: Path,
    expected_revision: int,
    directory_path: Directory,
    config: Config = Path("runtime"),
):
    emit(
        service(config).save_settings(
            managed_repo_id,
            directory_path,
            json.loads(settings_file.read_text()),
            expected_revision=expected_revision,
        )
    )


@app.command("ensure")
def ensure(
    managed_repo_id: str,
    directory_path: Directory,
    config: Config = Path("runtime"),
    commit_sha: str | None = None,
    rebuild: bool = False,
):
    emit(service(config).ensure(managed_repo_id, directory_path, commit_sha, rebuild=rebuild))


@app.command("reconcile")
def reconcile(directory_path: Directory, config: Config = Path("runtime"), limit: int = 4):
    emit(service(config).reconcile(directory_path, limit=limit))


@app.command("cancel")
def cancel(
    managed_repo_id: str, build_id: str, directory_path: Directory, config: Config = Path("runtime")
):
    emit(service(config).cancel(managed_repo_id, directory_path, build_id))


@app.command("export")
def export(
    managed_repo_id: str,
    build_id: str,
    destination: Path,
    directory_path: Directory,
    config: Config = Path("runtime"),
    include_embeddings: bool = False,
):
    if destination.exists():
        raise typer.BadParameter("Destination already exists; choose a new file")
    service(config).export(
        managed_repo_id,
        directory_path,
        build_id,
        destination,
        include_embeddings=include_embeddings,
    )
    emit({"exported": str(destination)})


@app.command("search")
def search(
    managed_repo_id: str,
    commit_sha: str,
    query: str,
    directory_path: Directory,
    config: Config = Path("runtime"),
    mode: str = "hybrid",
    limit: int = 10,
    offset: int = 0,
    allow_baseline: bool = False,
):
    emit(
        service(config).search(
            managed_repo_id,
            directory_path,
            commit_sha,
            query,
            mode=mode,
            limit=limit,
            offset=offset,
            allow_baseline=allow_baseline,
        )
    )


@app.command("navigate")
def navigate(
    managed_repo_id: str,
    commit_sha: str,
    symbol: str,
    directory_path: Directory,
    config: Config = Path("runtime"),
    kind: str = "definitions",
    limit: int = 20,
    offset: int = 0,
    allow_baseline: bool = False,
):
    emit(
        service(config).navigate(
            managed_repo_id,
            directory_path,
            commit_sha,
            symbol,
            kind=kind,
            limit=limit,
            offset=offset,
            allow_baseline=allow_baseline,
        )
    )

"""Evander git commands."""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Annotated

import typer

from . import cli_app, cli_common, cli_runtime


def _build_managed_git_admin_for_cli(config: Path):
    from evander.admin.services.managed_git_service import ManagedGitAdminService

    return ManagedGitAdminService(
        config=cli_runtime._load_runtime_config_from_option(config),
        runtime=None,
        project_dir=cli_runtime._project_dir_for_config_option(config),
    )


def _managed_git_jsonable(value: object) -> object:
    if hasattr(value, "model_dump") and callable(getattr(value, "model_dump")):
        return value.model_dump(mode="json")
    if is_dataclass(value):
        return _managed_git_jsonable(asdict(value))
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _managed_git_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_managed_git_jsonable(item) for item in value]
    return value


def _echo_managed_git_json(value: object) -> None:
    typer.echo(json.dumps(_managed_git_jsonable(value), indent=2, sort_keys=True))


def _managed_git_call(operation, /, *args: object, **kwargs: object) -> object:
    try:
        return operation(*args, **kwargs)
    except Exception as exc:
        if isinstance(exc, (LookupError, RuntimeError, ValueError)) or isinstance(
            getattr(exc, "status_code", None), int
        ):
            raise typer.BadParameter(str(exc) or "Managed Git operation failed") from exc
        raise


@cli_app.git_repos_app.command("register", short_help="Register or refresh a managed repository.")
def git_repos_register(
    directory_path: Annotated[
        str,
        typer.Option(
            "--directory-path",
            help="Owning directory path used for trust-boundary isolation.",
        ),
    ],
    repo_source: Annotated[
        str,
        typer.Option(
            "--repo-source",
            help="Canonical URL or local Git source used during registration.",
        ),
    ],
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
    default_branch: Annotated[
        str | None,
        typer.Option(
            "--default-branch",
            help="Override the detected canonical default branch.",
        ),
    ] = None,
    refresh: Annotated[
        bool,
        typer.Option(
            "--refresh/--no-refresh",
            help="Refresh canonical refs while registering the repository.",
        ),
    ] = True,
) -> None:
    managed_git = cli_runtime.build_admin_runtime(config).managed_git_admin
    result = _managed_git_call(
        managed_git.register_repository,
        directory_path,
        repo_source,
        default_branch=default_branch,
        refresh=refresh,
    )
    _echo_managed_git_json(result)


@cli_app.git_repos_app.command("list", short_help="List managed repositories.")
def git_repos_list(
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
    directory_path: Annotated[
        str | None,
        typer.Option(
            "--directory-path",
            help="Limit results to an owning directory subtree.",
        ),
    ] = None,
    include_descendants: Annotated[
        bool,
        typer.Option(
            "--include-descendants/--no-include-descendants",
            help="Include repositories owned by descendant directories.",
        ),
    ] = True,
) -> None:
    managed_git = cli_runtime.build_admin_runtime(config).managed_git_admin
    repositories = _managed_git_call(
        managed_git.list_repositories,
        directory_path=directory_path,
        include_descendants=include_descendants,
    )
    _echo_managed_git_json({"repositories": repositories})


@cli_app.git_repos_app.command("show", short_help="Show one managed repository.")
def git_repos_show(
    managed_repo_id: str,
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
    directory_path: Annotated[
        str | None,
        typer.Option(
            "--directory-path",
            help="Require the repository to be visible from this directory path.",
        ),
    ] = None,
) -> None:
    managed_git = cli_runtime.build_admin_runtime(config).managed_git_admin
    repository = _managed_git_call(
        managed_git.get_repository,
        managed_repo_id,
        directory_path=directory_path,
    )
    _echo_managed_git_json(repository)


@cli_app.git_repos_app.command("refresh", short_help="Refresh canonical refs for a repository.")
def git_repos_refresh(
    managed_repo_id: str,
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
) -> None:
    managed_git = cli_runtime.build_admin_runtime(config).managed_git_admin
    result = _managed_git_call(managed_git.refresh_repository, managed_repo_id)
    _echo_managed_git_json(result)


@cli_app.git_repos_app.command(
    "set-host-identity",
    short_help="Set the author and committer identity for host-created commits.",
)
def git_repos_set_host_identity(
    managed_repo_id: str,
    directory_path: Annotated[
        str,
        typer.Option(
            "--directory-path",
            help="Directory scope from which the managed repository is visible.",
        ),
    ],
    name: Annotated[
        str,
        typer.Option(
            "--name",
            help="Git author and committer name for host-created commits.",
        ),
    ],
    email: Annotated[
        str,
        typer.Option(
            "--email",
            help="Git author and committer email for host-created commits.",
        ),
    ],
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
) -> None:
    managed_git = cli_runtime.build_admin_runtime(config).managed_git_admin
    result = _managed_git_call(
        managed_git.set_host_commit_identity,
        managed_repo_id,
        directory_path=directory_path,
        name=name,
        email=email,
    )
    _echo_managed_git_json(result)


@cli_app.git_repos_app.command("delete", short_help="Delete an unused managed repository.")
def git_repos_delete(
    managed_repo_id: str,
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
) -> None:
    managed_git = cli_runtime.build_admin_runtime(config).managed_git_admin
    result = _managed_git_call(managed_git.delete_repository, managed_repo_id)
    _echo_managed_git_json(result)


@cli_app.git_repos_app.command("maintenance", short_help="Run managed repository maintenance.")
def git_repos_maintenance(
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
    managed_repo_id: Annotated[
        str | None,
        typer.Option(
            "--managed-repo-id",
            help="Run maintenance for one repository instead of every repository.",
        ),
    ] = None,
) -> None:
    managed_git = cli_runtime.build_admin_runtime(config).managed_git_admin
    result = _managed_git_call(managed_git.maintenance, managed_repo_id)
    _echo_managed_git_json(result)


@cli_app.git_repos_app.command("grants", short_help="List managed repository grants for a session.")
def git_repos_grants(
    session_id: str,
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
) -> None:
    grants = cli_runtime.build_admin_runtime(config).vm_agent_admin.list_managed_git_access(
        session_id
    )
    _echo_managed_git_json({"session_id": session_id, "grants": grants})


@cli_app.git_repos_app.command(
    "grant", short_help="Grant a session access to one managed repository."
)
def git_repos_grant(
    session_id: str,
    managed_repo_id: Annotated[str, typer.Option("--managed-repo-id")],
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
    access_mode: Annotated[
        str,
        typer.Option("--access-mode", help="read-only or write."),
    ] = "read-only",
) -> None:
    grant = cli_runtime.build_admin_runtime(config).vm_agent_admin.grant_managed_git_access(
        session_id,
        managed_repo_id,
        access_mode=access_mode,
        lifetime="session",
    )
    _echo_managed_git_json(grant)


@cli_app.git_repos_app.command("revoke-grant", short_help="Revoke one session repository grant.")
def git_repos_revoke_grant(
    session_id: str,
    managed_repo_id: Annotated[str, typer.Option("--managed-repo-id")],
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
) -> None:
    result = cli_runtime.build_admin_runtime(config).vm_agent_admin.revoke_managed_git_access(
        session_id,
        managed_repo_id,
    )
    _echo_managed_git_json(result)


@cli_app.git_repos_app.command(
    "clone-into-agent",
    short_help="Clone a managed repository and provision agent Git access.",
)
def git_repos_clone_into_agent(
    session_id: str,
    managed_repo_id: Annotated[str, typer.Option("--managed-repo-id")],
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
    access_mode: Annotated[
        str,
        typer.Option("--access-mode", help="read-only or write."),
    ] = "read-only",
    lifetime: Annotated[
        str,
        typer.Option(
            "--lifetime",
            help="session for repeat fetch/pull access, or operation for one-shot clone access.",
        ),
    ] = "session",
) -> None:
    result = cli_runtime.build_admin_runtime(config).vm_agent_admin.clone_managed_repo_into_agent(
        session_id,
        managed_repo_id,
        access_mode=access_mode,
        lifetime=lifetime,
    )
    _echo_managed_git_json(result)

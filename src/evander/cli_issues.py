"""Evander issues commands."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from . import cli_app, cli_common, cli_runtime, cli_session_support


@cli_app.app.command(
    "destroy-issue-vm",
    rich_help_panel="Issue Lifecycle",
    short_help="Destroy an issue VM and state.",
)
def destroy_issue_vm(
    target: str,
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
) -> None:
    service = cli_runtime.build_admin_runtime(config).vm_agent_admin
    result = service.destroy_issue_vm(target)
    cli_common._echo_action_result_or_exit(result)


@cli_app.app.command(
    "prune-orphaned-issue-state",
    rich_help_panel="Issue Lifecycle",
    short_help="Remove issue state for missing VMs.",
)
def prune_orphaned_issue_state(
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
) -> None:
    cfg = cli_runtime._load_runtime_config_from_option(config)
    runtime = cli_runtime.build_runtime(cfg)
    live_vm_names = cli_session_support._list_existing_issue_vm_names()
    live_issue_numbers = {
        issue_number
        for issue_number in (
            cli_session_support._extract_issue_number(vm_name) for vm_name in live_vm_names
        )
        if issue_number is not None
    }
    session_repository = cli_session_support._runtime_session_repository(runtime)
    sessions = {session.session_id: session for session in session_repository.list_sessions()}
    orphan_session_ids = {
        session_id
        for session_id, session in sessions.items()
        if (
            (
                issue_number := (
                    cli_session_support._extract_issue_number(session.vm_id)
                    or cli_session_support._extract_issue_number(session_id)
                )
            )
            is not None
            and f"vm-{issue_number}" not in live_vm_names
        )
    }
    orphan_issue_numbers = {
        issue_number
        for issue_number in (
            (
                cli_session_support._extract_issue_number(sessions[session_id].vm_id)
                or cli_session_support._extract_issue_number(session_id)
            )
            for session_id in orphan_session_ids
        )
        if issue_number is not None
    }
    orphan_issue_numbers.update(
        issue_number
        for issue_number in cli_session_support._issue_numbers_from_pr_map(cfg.pr_map_path)
        if issue_number not in live_issue_numbers
    )

    session_streams = cli_session_support._session_streams_for_session_ids(
        sessions, session_ids=orphan_session_ids
    )
    if orphan_session_ids:
        for session_id in orphan_session_ids:
            session_repository.delete_session(session_id)
    removed_approvals = cli_session_support._prune_approvals_for_sessions(
        runtime, session_ids=orphan_session_ids
    )

    removed_pr_map_entries_a, removed_pr_map_streams_a = cli_session_support._prune_pr_map(
        cfg.pr_map_path,
        session_ids=orphan_session_ids,
    )
    removed_pr_map_entries_b, removed_pr_map_streams_b = (
        cli_session_support._prune_pr_map_for_issue_numbers(
            cfg.pr_map_path,
            issue_numbers=orphan_issue_numbers,
        )
    )
    removed_streams = session_streams | removed_pr_map_streams_a | removed_pr_map_streams_b
    removed_cursors, removed_feedback = cli_session_support._prune_pr_feedback_store(
        cfg.runtime_dir / "pr-feedback-store.json",
        streams=removed_streams,
    )

    typer.echo(
        f"Pruned orphaned issue state "
        f"removed_sessions={len(orphan_session_ids)} "
        f"removed_approvals={removed_approvals} "
        f"removed_pr_map_entries={removed_pr_map_entries_a + removed_pr_map_entries_b} "
        f"removed_feedback_cursors={removed_cursors} "
        f"removed_feedback_items={removed_feedback}"
    )


@cli_app.app.command(
    "create-issue",
    rich_help_panel="Issue Lifecycle",
    short_help="Create a local/proxy-backed issue record.",
)
def create_issue(
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
    title: Annotated[
        str,
        typer.Option("--title", help="Issue title."),
    ] = "",
    body: Annotated[
        str,
        typer.Option("--body", help="Issue body/description."),
    ] = "",
    managed_repo_id: Annotated[
        str | None,
        typer.Option(
            "--managed-repo-id",
            help="Existing managed repository to bind to the issue.",
        ),
    ] = None,
    repo_source: Annotated[
        str | None,
        typer.Option(
            "--repo-source",
            help="Transient repository source to register and bind to this issue.",
        ),
    ] = None,
    directory_path: Annotated[
        str,
        typer.Option("--directory-path", help="Owning directory path."),
    ] = "root",
    yaml_path: Annotated[
        Path | None,
        typer.Option(
            "--yaml-path",
            help="Import an existing issue YAML file instead of creating one from title/body.",
        ),
    ] = None,
) -> None:
    service = cli_runtime.build_admin_runtime(config).issue_git_admin
    issue = service.create_issue(
        title=title,
        body=body,
        managed_repo_id=managed_repo_id,
        repo_source=repo_source,
        yaml_path=str(yaml_path) if yaml_path is not None else None,
        directory_path=directory_path,
    )
    typer.echo(f"Created local issue {issue.issue_number} ({issue.title}) at {issue.yaml_path}")


@cli_app.app.command(
    "create-local-issue",
    rich_help_panel="Issue Lifecycle",
    short_help="Create a local issue record.",
)
def create_local_issue(
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
    title: Annotated[
        str,
        typer.Option("--title", help="Local issue title."),
    ] = "",
    body: Annotated[
        str,
        typer.Option("--body", help="Local issue body/description."),
    ] = "",
    managed_repo_id: Annotated[
        str | None,
        typer.Option(
            "--managed-repo-id",
            help="Existing managed repository to bind to the local issue.",
        ),
    ] = None,
    repo_source: Annotated[
        str | None,
        typer.Option(
            "--repo-source",
            help="Transient repository source to register and bind to this local issue.",
        ),
    ] = None,
    directory_path: Annotated[
        str,
        typer.Option("--directory-path", help="Owning directory path."),
    ] = "root",
    yaml_path: Annotated[
        Path | None,
        typer.Option(
            "--yaml-path",
            help="Import an existing issue YAML file instead of creating one from title/body.",
        ),
    ] = None,
) -> None:
    service = cli_runtime.build_admin_runtime(config).issue_git_admin
    issue = service.create_local_issue(
        title=title,
        body=body,
        managed_repo_id=managed_repo_id,
        repo_source=repo_source,
        yaml_path=str(yaml_path) if yaml_path is not None else None,
        directory_path=directory_path,
    )
    typer.echo(f"Created local issue {issue.issue_number} ({issue.title}) at {issue.yaml_path}")


@cli_app.app.command(
    "list-issues",
    rich_help_panel="Issue Lifecycle",
    short_help="List local issue records.",
)
def list_issues(
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable local issue records."),
    ] = False,
) -> None:
    service = cli_runtime.build_admin_runtime(config).issue_git_admin
    issues = service.list_issues()
    if as_json:
        typer.echo(
            json.dumps(
                [issue.model_dump(mode="json") for issue in issues], indent=2, sort_keys=True
            )
        )
        return
    if not issues:
        return
    typer.echo(f"{'Issue':<8} {'Title':<32} YAML")
    for issue in issues:
        typer.echo(f"{issue.issue_number:<8} {issue.title[:32]:<32} {issue.yaml_path}")


@cli_app.app.command(
    "list-local-issues",
    rich_help_panel="Issue Lifecycle",
    short_help="List local issue records.",
)
def list_local_issues(
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable local issue records."),
    ] = False,
) -> None:
    list_issues(config=config, as_json=as_json)


@cli_app.app.command(
    "get-issue",
    rich_help_panel="Issue Lifecycle",
    short_help="Read a local/proxy issue payload by local issue identifier.",
)
def get_issue(
    issue_id: str,
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
    include_remote_comments: Annotated[
        bool,
        typer.Option(
            "--include-remote-comments/--no-include-remote-comments",
            help="Include remote comments when resolving proxy-backed issues.",
        ),
    ] = True,
    sync_proxy_cache: Annotated[
        bool,
        typer.Option(
            "--sync-proxy-cache/--no-sync-proxy-cache",
            help="Refresh and persist cached proxy issue fields after reading.",
        ),
    ] = True,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable issue details."),
    ] = False,
) -> None:
    service = cli_runtime.build_admin_runtime(config).issue_git_admin
    payload = service.get_issue(
        issue_id,
        include_remote_comments=include_remote_comments,
        sync_proxy_cache=sync_proxy_cache,
    )
    if as_json:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    typer.echo(f"Issue {payload.get('issue_number', issue_id)}")
    typer.echo(f"Source: {payload.get('source_type', '')}")
    typer.echo(f"Title: {payload.get('title', '')}")
    typer.echo(f"State: {payload.get('state', '')}")
    typer.echo(f"Updated: {payload.get('updated_at', '')}")


@cli_app.app.command(
    "add-issue-comment",
    rich_help_panel="Issue Lifecycle",
    short_help="Add an issue comment by local issue identifier.",
)
def add_issue_comment(
    issue_id: str,
    comment: Annotated[
        str,
        typer.Option("--comment", help="Comment text to add."),
    ],
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
    sync_proxy_cache: Annotated[
        bool,
        typer.Option(
            "--sync-proxy-cache/--no-sync-proxy-cache",
            help="Refresh and persist cached proxy issue fields after writing.",
        ),
    ] = True,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable write result."),
    ] = False,
) -> None:
    service = cli_runtime.build_admin_runtime(config).issue_git_admin
    payload = service.add_issue_comment(issue_id, comment, sync_proxy_cache=sync_proxy_cache)
    if as_json:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    typer.echo(
        f"Comment added to issue {payload.get('issue_number', issue_id)} ({payload.get('source_type', '')})"
    )


@cli_app.app.command(
    "set-issue-state",
    rich_help_panel="Issue Lifecycle",
    short_help="Set issue state by local issue identifier.",
)
def set_issue_state(
    issue_id: str,
    state: Annotated[
        str,
        typer.Option("--state", help="Target issue state value."),
    ],
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
    sync_proxy_cache: Annotated[
        bool,
        typer.Option(
            "--sync-proxy-cache/--no-sync-proxy-cache",
            help="Refresh and persist cached proxy issue fields after writing.",
        ),
    ] = True,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable write result."),
    ] = False,
) -> None:
    service = cli_runtime.build_admin_runtime(config).issue_git_admin
    payload = service.set_issue_state(issue_id, state, sync_proxy_cache=sync_proxy_cache)
    if as_json:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    typer.echo(
        f"State updated for issue {payload.get('issue_number', issue_id)} ({payload.get('source_type', '')})"
    )


@cli_app.app.command(
    "create-issue-vm",
    rich_help_panel="Issue Lifecycle",
    short_help="Create an issue VM.",
)
def create_issue_vm(
    issue: str,
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
    session_id: Annotated[
        str | None,
        typer.Option(
            "--session-id", help="Session identifier to create and pass into the VM setup flow."
        ),
    ] = None,
    vm_user: Annotated[str, typer.Option("--vm-user", help="Guest user to configure.")] = "agent",
    ttl_hours: Annotated[
        int | None, typer.Option("--ttl-hours", help="Session time to live in hours.")
    ] = None,
    discord_channel: Annotated[
        list[str],
        typer.Option("--discord-channel", help="Allowlisted Discord control channel."),
    ] = [],
    email_mailbox: Annotated[
        list[str],
        typer.Option(
            "--email-mailbox", help="Allowlisted mailbox id. Repeat for multiple mailboxes."
        ),
    ] = [],
    email_folder: Annotated[
        list[str],
        typer.Option(
            "--email-folder", help="Allowlisted email folder. Repeat for multiple folders."
        ),
    ] = [],
    dotnet: Annotated[
        bool, typer.Option("--dotnet", help="Bootstrap the host .NET SDK into the guest.")
    ] = False,
    nuget: Annotated[
        str | None,
        typer.Option(
            "--nuget",
            help="Project or solution path used to prewarm the shared NuGet cache and bootstrap NuGet relay config.",
        ),
    ] = None,
    mssql: Annotated[
        bool, typer.Option("--mssql", help="Provision a guest-local Microsoft SQL Server instance.")
    ] = False,
    mssql_init_root: Annotated[
        str | None,
        typer.Option(
            "--mssql-init-root",
            help="Host directory copied into /var/opt/mssql/init. If present, init.sql is executed.",
        ),
    ] = None,
    playwright: Annotated[
        bool,
        typer.Option(
            "--playwright", help="Bootstrap the host-seeded Playwright runtime into the guest."
        ),
    ] = False,
    codex_skill: Annotated[
        list[str],
        typer.Option(
            "--codex-skill",
            help="Codex skill directory name to copy into guest ~/.codex/skills. Repeat for multiple skills.",
        ),
    ] = [],
    model: Annotated[
        str | None,
        typer.Option("--model", help="Agent model override for the created session."),
    ] = None,
    reasoning_effort: Annotated[
        str | None,
        typer.Option("--reasoning-effort", help="Native reasoning effort default for the session."),
    ] = None,
    fast_mode: Annotated[
        bool | None,
        typer.Option(
            "--fast-mode/--no-fast-mode",
            help="Codex Fast mode; omit to inherit the session default.",
        ),
    ] = None,
    speed: cli_common.SpeedOption = None,
    model_provider_mode: Annotated[
        str | None,
        typer.Option(
            "--model-provider-mode",
            help=(
                "Session relay mode override: openai_public, local_openai, "
                "azure_openai, anthropic, or openrouter."
            ),
        ),
    ] = None,
) -> None:
    if mssql_init_root is not None and not mssql:
        raise typer.BadParameter("--mssql-init-root requires --mssql.")
    service = cli_runtime.build_admin_runtime(config).vm_agent_admin
    result = service.create_issue_vm(
        issue=issue,
        session_id=session_id,
        vm_user=vm_user,
        ttl_hours=ttl_hours,
        discord_channels=discord_channel,
        email_mailboxes=email_mailbox,
        email_folders=email_folder,
        dotnet=dotnet,
        nuget=nuget,
        mssql=mssql,
        mssql_init_root=mssql_init_root,
        playwright=playwright,
        codex_skills=codex_skill,
        model=model,
        reasoning_effort=reasoning_effort,
        fast_mode=cli_common.resolve_speed_option(fast_mode, speed),
        model_provider_mode=model_provider_mode,
    )
    cli_common._echo_action_result_or_exit(result)

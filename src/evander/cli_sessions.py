"""Evander sessions commands."""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Annotated

import typer

from evander.reasoning_effort import normalize_reasoning_effort, validate_session_reasoning_effort
from evander.admin.services.issue_setup_helpers import (
    resolve_agent_model_for_manifest,
    resolve_effective_issue_setup_config,
)
from evander.directories import DirectoryService
from evander.models import ApprovalStatus, SessionManifest, ToolName
from evander.provider_config import (
    MODEL_PROVIDER_MODE_METADATA_KEY,
    normalize_model_provider_mode_override,
)

from . import cli_app, cli_common, cli_runtime, cli_session_support


@cli_app.app.command(
    "create-session",
    rich_help_panel="Sessions and Feedback",
    short_help="Create a session manifest.",
)
def create_session(
    session_id: str,
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
    vm_id: Annotated[
        str | None, typer.Option("--vm-id", help="VM identifier to bind to the session.")
    ] = None,
    directory: Annotated[
        str | None,
        typer.Option(
            "--directory", help="Directory path whose effective config should apply to the session."
        ),
    ] = None,
    discord_channel: Annotated[
        list[str] | None,
        typer.Option("--discord-channel", help="Allowlisted Discord control channel."),
    ] = None,
    email_folder: Annotated[
        list[str] | None,
        typer.Option("--email-folder", help="Allowlisted email folder."),
    ] = None,
    ttl_hours: Annotated[
        int | None, typer.Option("--ttl-hours", help="Session time to live in hours.")
    ] = None,
    model: Annotated[
        str | None,
        typer.Option("--model", help="Agent model override for this session."),
    ] = None,
    reasoning_effort: Annotated[
        str | None,
        typer.Option("--reasoning-effort", help="Native agent reasoning level; omit to inherit."),
    ] = None,
) -> None:
    cfg = cli_runtime._load_runtime_config_from_option(config)
    runtime = cli_runtime.build_runtime(cfg)
    service = DirectoryService(runtime.directories)
    normalized_directory = service.show(directory or "root").directory.path
    policy = runtime.policy_config.resolve_for_directory(normalized_directory)
    manifest = SessionManifest(
        session_id=session_id,
        vm_id=vm_id,
        directory_path=normalized_directory,
        allowed_tools=[
            ToolName.DISCORD_GET_COMMANDS,
            ToolName.DISCORD_POST_STATUS,
            ToolName.ADD_MESSAGE_REACTION,
            ToolName.HAS_PENDING_DISCORD_MESSAGES,
            ToolName.LIST_UNREAD_DISCORD_MESSAGES,
            ToolName.LIST_ALL_DISCORD_MESSAGES,
            ToolName.EMAIL_LIST_UNREAD,
            ToolName.EMAIL_GET_MESSAGE,
            ToolName.EMAIL_CREATE_DRAFT,
            ToolName.APPROVAL_SUBMIT,
            ToolName.APPROVAL_CHECK,
            ToolName.STAGE_ARTIFACT,
            ToolName.PROMPT_AGENT,
            ToolName.RESPOND_TO_AGENT,
            ToolName.HAS_PENDING_FEEDBACK,
            ToolName.LIST_PULL_REQUESTS,
            ToolName.GET_PULL_REQUEST,
            ToolName.LIST_UNREAD_FEEDBACK,
            ToolName.LIST_ALL_FEEDBACK,
            ToolName.LIST_PR_FEEDBACK_ATTACHMENTS,
            ToolName.DOWNLOAD_PR_FEEDBACK_ATTACHMENT,
            ToolName.COMMUNICATION_COPY_ARTIFACT_TO_WORKSPACE,
            ToolName.POST_PR_FEEDBACK_REPLY,
        ],
        approval_required_tools=list(policy.approval_required_tools),
        discord_channel_allowlist=discord_channel or [],
        email_folder_allowlist=email_folder or [],
        email_mailbox_allowlist=policy.email_mailbox_allowlist,
        email_folder_allowlist_by_mailbox=policy.email_folder_allowlist_by_mailbox,
        email_draft_recipient_allowlist=policy.email_draft_recipient_allowlist,
        email_draft_domain_allowlist=policy.email_draft_domain_allowlist,
        email_send_enabled=policy.email_send_enabled,
        expires_at=datetime.now(cli_common.UTC)
        + cli_session_support._resolve_session_ttl(policy, ttl_hours),
        metadata={},
    )
    if str(os.environ.get("AGENT_PROVIDER", "")).strip().lower() == "copilot":
        manifest.allowed_tools.append(ToolName.COPILOT_HOST_AUTH)
        manifest.metadata["agent_provider"] = "copilot"
    normalized_model = (model or "").strip() or None
    if normalized_model is not None:
        manifest.model = normalized_model
    else:
        try:
            issue_setup = resolve_effective_issue_setup_config(
                config=cfg,
                runtime=runtime,
                directory_path=normalized_directory,
                root_dir=cli_runtime._project_dir_for_config_option(config),
            )
            manifest.model = resolve_agent_model_for_manifest(manifest, issue_setup=issue_setup)
        except ValueError:
            manifest.model = None
    manifest.reasoning_effort = normalize_reasoning_effort(reasoning_effort)
    validate_session_reasoning_effort(
        manifest, runtime.provider_config.resolve_for_directory(manifest.directory_path)
    )
    if manifest.email_send_enabled:
        manifest.allowed_tools.extend([ToolName.EMAIL_SEND, ToolName.EMAIL_SEND_APPROVED])
    session_repository = cli_session_support._runtime_session_repository(runtime)
    if session_repository.get_session(session_id) is not None:
        typer.echo(f"Session already exists: {session_id}", err=True)
        raise typer.Exit(code=1)
    session_repository.upsert_session(manifest)
    typer.echo(f"Created session manifest {session_id}")


@cli_app.app.command(
    "prune-sessions",
    rich_help_panel="Sessions and Feedback",
    short_help="Delete expired or all sessions.",
)
def prune_sessions(
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
    expired_only: Annotated[
        bool,
        typer.Option(
            "--expired-only/--all",
            help="Delete only expired session manifests, or all session manifests.",
        ),
    ] = True,
) -> None:
    cfg = cli_runtime._load_runtime_config_from_option(config)
    runtime = cli_runtime.build_runtime(cfg)
    session_repository = cli_session_support._runtime_session_repository(runtime)
    sessions = {session.session_id: session for session in session_repository.list_sessions()}
    now = datetime.now(cli_common.UTC)

    if expired_only:
        keys_to_delete = [
            session_id for session_id, session in sessions.items() if session.expires_at <= now
        ]
    else:
        keys_to_delete = list(sessions.keys())

    for session_id in keys_to_delete:
        session_repository.delete_session(session_id)

    removed_pr_map_entries, removed_streams = cli_session_support._prune_pr_map(
        cfg.pr_map_path,
        session_ids=set(keys_to_delete),
    )
    removed_cursors, removed_feedback = cli_session_support._prune_pr_feedback_store(
        cfg.runtime_dir / "pr-feedback-store.json",
        streams=removed_streams,
    )
    mode = "expired" if expired_only else "all"
    typer.echo(
        f"Pruned {len(keys_to_delete)} {mode} session(s) "
        f"removed_pr_map_entries={removed_pr_map_entries} "
        f"removed_feedback_cursors={removed_cursors} "
        f"removed_feedback_items={removed_feedback}"
    )


@cli_app.app.command(
    "list-sessions",
    rich_help_panel="Sessions and Feedback",
    short_help="List active, expired, or all session manifests.",
)
def list_sessions(
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
    all_sessions: Annotated[
        bool,
        typer.Option("--all", help="Show both active and expired sessions."),
    ] = False,
    expired_only: Annotated[
        bool,
        typer.Option("--expired-only", help="Show only expired sessions."),
    ] = False,
) -> None:
    if all_sessions and expired_only:
        raise typer.BadParameter("--all and --expired-only cannot be used together.")

    cfg = cli_runtime._load_runtime_config_from_option(config)
    runtime = cli_runtime.build_runtime(cfg)
    sessions = cli_session_support._runtime_sessions_by_id(runtime)
    now = datetime.now(cli_common.UTC)

    selected: list[tuple[str, SessionManifest]] = []
    for item in sorted(sessions.items(), key=cli_session_support._session_sort_key):
        _, manifest = item
        is_expired = manifest.expires_at <= now
        if expired_only:
            if is_expired:
                selected.append(item)
        elif all_sessions:
            selected.append(item)
        elif not is_expired:
            selected.append(item)

    if not selected:
        typer.echo("No sessions found.")
        return

    for session_id, manifest in selected:
        status = "expired" if manifest.expires_at <= now else "active"
        typer.echo(
            f"{session_id}\tstatus={status}\tvm_id={manifest.vm_id or '-'}\t"
            f"created_at={manifest.created_at.isoformat()}\texpires_at={manifest.expires_at.isoformat()}"
        )


@cli_app.app.command(
    "renew-session",
    rich_help_panel="Sessions and Feedback",
    short_help="Extend or reissue a session manifest.",
)
def renew_session(
    target: str,
    extension: Annotated[
        str,
        typer.Option(
            "--extend-by",
            help="Duration to set from now, in <number><h|d> form.",
        ),
    ] = "1d",
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
) -> None:
    cfg = cli_runtime._load_runtime_config_from_option(config)
    runtime = cli_runtime.build_runtime(cfg)
    session_repository = cli_session_support._runtime_session_repository(runtime)
    sessions = {session.session_id: session for session in session_repository.list_sessions()}
    session_id, manifest = cli_session_support._resolve_session_target(target, sessions)
    duration = cli_session_support._parse_session_extension(extension)
    base_time = datetime.now(cli_common.UTC)
    previous_expires_at = manifest.expires_at
    manifest.expires_at = base_time + duration
    session_repository.upsert_session(manifest)
    typer.echo(
        f"Renewed session {session_id} "
        f"(previous_expires_at={previous_expires_at.isoformat()} "
        f"new_expires_at={manifest.expires_at.isoformat()})"
    )


@cli_app.app.command(
    "revoke-session",
    rich_help_panel="Sessions and Feedback",
    short_help="Expire a session immediately.",
)
def revoke_session(
    target: str,
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
) -> None:
    cfg = cli_runtime._load_runtime_config_from_option(config)
    runtime = cli_runtime.build_runtime(cfg)
    session_repository = cli_session_support._runtime_session_repository(runtime)
    sessions = {session.session_id: session for session in session_repository.list_sessions()}
    session_id, manifest = cli_session_support._resolve_session_target(target, sessions)
    previous_expires_at = manifest.expires_at
    manifest.expires_at = datetime.now(cli_common.UTC)
    session_repository.upsert_session(manifest)
    typer.echo(
        f"Revoked session {session_id} "
        f"(previous_expires_at={previous_expires_at.isoformat()} "
        f"new_expires_at={manifest.expires_at.isoformat()})"
    )


@cli_app.app.command(
    "delete-session",
    rich_help_panel="Sessions and Feedback",
    short_help="Delete a session manifest.",
)
def delete_session(
    target: str,
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
) -> None:
    service = cli_runtime.build_admin_runtime(config).vm_agent_admin
    try:
        result = service.delete_session(target)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    cli_common._echo_action_result_or_exit(result)


@cli_app.app.command(
    "delete-expired-sessions",
    rich_help_panel="Sessions and Feedback",
    short_help="Delete all expired session manifests.",
)
def delete_expired_sessions(
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
) -> None:
    admin_runtime = cli_runtime.build_admin_runtime(config)
    sessions = {
        session.session_id: session
        for session in admin_runtime.runtime.session_repository.list_sessions()
    }
    now = datetime.now(cli_common.UTC)
    expired_session_ids = [
        session_id for session_id, session in sessions.items() if session.expires_at <= now
    ]
    deleted = 0
    skipped: list[str] = []
    for session_id in expired_session_ids:
        try:
            admin_runtime.vm_agent_admin.delete_session(session_id)
        except ValueError as exc:
            skipped.append(f"{session_id}: {exc}")
            continue
        deleted += 1
    typer.echo(f"Deleted {deleted} expired session(s)")
    if skipped:
        typer.echo(f"Skipped {len(skipped)} associated expired session(s):", err=True)
        for item in skipped:
            typer.echo(f"  {item}", err=True)


@cli_app.app.command(
    "set-session-model",
    rich_help_panel="Sessions and Feedback",
    short_help="Set or clear a session model override.",
)
def set_session_model(
    target: str,
    model: Annotated[
        str,
        typer.Option(
            "--model", help="Model to persist. Use an empty value with --clear to remove."
        ),
    ] = "",
    clear: Annotated[
        bool,
        typer.Option(
            "--clear", help="Clear the session model so directory defaults apply at invocation."
        ),
    ] = False,
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
) -> None:
    if not clear and not model.strip():
        raise typer.BadParameter("Provide --model with a non-empty value, or pass --clear.")
    if clear and model.strip():
        raise typer.BadParameter("Use either --model or --clear, not both.")
    cfg = cli_runtime._load_runtime_config_from_option(config)
    runtime = cli_runtime.build_runtime(cfg)
    session_repository = cli_session_support._runtime_session_repository(runtime)
    sessions = {session.session_id: session for session in session_repository.list_sessions()}
    session_id, manifest = cli_session_support._resolve_session_target(target, sessions)
    manifest.model = None if clear else model.strip()
    validate_session_reasoning_effort(
        manifest, runtime.provider_config.resolve_for_directory(manifest.directory_path)
    )
    session_repository.upsert_session(manifest)
    if clear:
        typer.echo(f"Cleared model override for session {session_id}")
        return
    typer.echo(f"Set model override for session {session_id} to {manifest.model}")


@cli_app.app.command("set-session-reasoning-effort", rich_help_panel="Sessions and Feedback")
def set_session_reasoning_effort(
    target: str,
    effort: Annotated[str | None, typer.Option("--reasoning-effort", "--effort")] = None,
    clear: Annotated[bool, typer.Option("--clear", help="Restore the harness default.")] = False,
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
) -> None:
    if clear == (effort is not None):
        raise typer.BadParameter("Provide --reasoning-effort or --clear, exclusively.")
    try:
        result = cli_runtime.build_admin_runtime(
            config
        ).session_admin.update_session_model_settings(
            target,
            {"reasoning_effort": None if clear else effort},
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(result.message)


@cli_app.app.command(
    "set-session-model-provider-mode",
    rich_help_panel="Sessions and Feedback",
    short_help="Set or clear a session relay-mode override.",
)
def set_session_model_provider_mode(
    target: str,
    mode: Annotated[
        str,
        typer.Option(
            "--mode",
            help=(
                "Relay mode to persist: openai_public, local_openai, azure_openai, "
                "anthropic, or openrouter."
            ),
        ),
    ] = "",
    clear: Annotated[
        bool,
        typer.Option("--clear", help="Clear the session override so directory defaults apply."),
    ] = False,
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
) -> None:
    if not clear and not mode.strip():
        raise typer.BadParameter("Provide --mode with a non-empty value, or pass --clear.")
    if clear and mode.strip():
        raise typer.BadParameter("Use either --mode or --clear, not both.")
    cfg = cli_runtime._load_runtime_config_from_option(config)
    runtime = cli_runtime.build_runtime(cfg)
    session_repository = cli_session_support._runtime_session_repository(runtime)
    sessions = {session.session_id: session for session in session_repository.list_sessions()}
    session_id, manifest = cli_session_support._resolve_session_target(target, sessions)
    value = "" if clear else normalize_model_provider_mode_override(mode)
    metadata = dict(manifest.metadata)
    metadata[MODEL_PROVIDER_MODE_METADATA_KEY] = value
    manifest.metadata = metadata
    session_repository.upsert_session(manifest)
    if clear:
        typer.echo(f"Cleared model provider mode override for session {session_id}")
        return
    typer.echo(f"Set model provider mode override for session {session_id} to {value}")


@cli_app.app.command(
    "add-tool-permission",
    rich_help_panel="Sessions and Feedback",
    short_help="Add an allowed tool to a session manifest.",
)
def add_tool_permission(
    target: str,
    tool: ToolName,
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
) -> None:
    cfg = cli_runtime._load_runtime_config_from_option(config)
    runtime = cli_runtime.build_runtime(cfg)
    session_repository = cli_session_support._runtime_session_repository(runtime)
    sessions = {session.session_id: session for session in session_repository.list_sessions()}
    session_id, manifest = cli_session_support._resolve_session_target(target, sessions)
    if tool in manifest.allowed_tools:
        typer.echo(f"Tool permission {tool.value} already present on session {session_id}")
        return

    manifest.allowed_tools.append(tool)
    session_repository.upsert_session(manifest)
    typer.echo(f"Added tool permission {tool.value} to session {session_id}")


@cli_app.app.command(
    "revoke-tool-permission",
    rich_help_panel="Sessions and Feedback",
    short_help="Remove an allowed tool from a session manifest.",
)
def revoke_tool_permission(
    target: str,
    tool: ToolName,
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
) -> None:
    cfg = cli_runtime._load_runtime_config_from_option(config)
    runtime = cli_runtime.build_runtime(cfg)
    session_repository = cli_session_support._runtime_session_repository(runtime)
    sessions = {session.session_id: session for session in session_repository.list_sessions()}
    session_id, manifest = cli_session_support._resolve_session_target(target, sessions)
    if tool not in manifest.allowed_tools:
        typer.echo(f"Tool permission {tool.value} not present on session {session_id}")
        return

    manifest.allowed_tools.remove(tool)
    session_repository.upsert_session(manifest)
    typer.echo(f"Revoked tool permission {tool.value} from session {session_id}")


@cli_app.app.command(
    "attach-pr-feedback",
    rich_help_panel="Sessions and Feedback",
    short_help="Bind a PR feedback stream.",
)
def attach_pr_feedback(
    session_id: str,
    provider: Annotated[str, typer.Option("--provider", help="Feedback provider name.")],
    repo: Annotated[str, typer.Option("--repo", help="Provider repository identifier.")],
    pr_id: Annotated[str, typer.Option("--pr-id", help="Pull request identifier.")],
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
) -> None:
    cfg = cli_runtime._load_runtime_config_from_option(config)
    runtime = cli_runtime.build_runtime(cfg)
    session_repository = cli_session_support._runtime_session_repository(runtime)
    manifest = session_repository.get_session(session_id)
    if manifest is None:
        raise typer.BadParameter(f"Unknown session: {session_id}")

    stream = {
        "provider": provider.strip(),
        "repo": repo.strip(),
        "pr_id": pr_id.strip(),
    }
    if not all(stream.values()):
        raise typer.BadParameter("--provider, --repo, and --pr-id must all be non-empty.")

    existing_streams = manifest.metadata.get("pr_feedback_streams")
    if not isinstance(existing_streams, list):
        existing_streams = []

    if stream not in existing_streams:
        existing_streams.append(stream)
    manifest.metadata["pr_feedback_streams"] = existing_streams

    for tool in (
        ToolName.HAS_PENDING_FEEDBACK,
        ToolName.LIST_PULL_REQUESTS,
        ToolName.GET_PULL_REQUEST,
        ToolName.LIST_UNREAD_FEEDBACK,
        ToolName.LIST_ALL_FEEDBACK,
        ToolName.LIST_PR_FEEDBACK_ATTACHMENTS,
        ToolName.DOWNLOAD_PR_FEEDBACK_ATTACHMENT,
        ToolName.COMMUNICATION_COPY_ARTIFACT_TO_WORKSPACE,
        ToolName.POST_PR_FEEDBACK_REPLY,
    ):
        if tool not in manifest.allowed_tools:
            manifest.allowed_tools.append(tool)

    session_repository.upsert_session(manifest)
    typer.echo(
        f"Attached PR feedback stream {stream['provider']}:{stream['repo']}:{stream['pr_id']} "
        f"to session {session_id}"
    )


@cli_app.app.command(
    "approve",
    rich_help_panel="Sessions and Feedback",
    short_help="Approve or deny a request.",
)
def approve(
    request_id: str,
    approver: str,
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
    approve: Annotated[
        bool, typer.Option("--approve/--deny", help="Approve or deny the request.")
    ] = True,
    reason: Annotated[
        str, typer.Option("--reason", help="Operator-entered reason for the decision.")
    ] = "",
) -> None:
    service = cli_runtime.build_admin_runtime(config).approval_audit_admin
    normalized_reason = reason.strip() or ("approved-via-cli" if approve else "denied-via-cli")
    try:
        request = service.decide_approval_request(
            request_id,
            approver=approver,
            approve=approve,
            reason=normalized_reason,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"Updated approval {request_id} to {request.status.value}")


@cli_app.app.command(
    "list-approvals",
    rich_help_panel="Sessions and Feedback",
    short_help="List approval requests and queue health.",
)
def list_approvals(
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
    status: Annotated[
        list[ApprovalStatus],
        typer.Option("--status", help="Filter by approval status. Repeat for multiple values."),
    ] = [],
    directory_path: Annotated[
        str | None, typer.Option("--directory-path", help="Filter by canonical directory path.")
    ] = None,
    direct_only: Annotated[
        bool, typer.Option("--direct-only", help="Match only the selected directory path exactly.")
    ] = False,
    session_id: Annotated[
        str | None, typer.Option("--session-id", help="Filter by session id.")
    ] = None,
    vm_id: Annotated[str | None, typer.Option("--vm-id", help="Filter by VM id.")] = None,
    tool: Annotated[str | None, typer.Option("--tool", help="Filter by tool/action type.")] = None,
    older_than_seconds: Annotated[
        int | None, typer.Option("--older-than-seconds", help="Minimum age in seconds.")
    ] = None,
    show_payload: Annotated[
        bool, typer.Option("--show-payload", help="Show redacted payloads inline.")
    ] = False,
) -> None:
    service = cli_runtime.build_admin_runtime(config).approval_audit_admin
    statuses = set(status) or None
    health = service.approval_queue_health(
        statuses=statuses,
        directory_path=directory_path,
        direct_only=direct_only,
        session_id=session_id,
        vm_id=vm_id,
        tool_name=tool,
        min_age_seconds=older_than_seconds,
    )
    typer.echo(
        "Queue "
        f"total={health.total} pending={health.pending} approved={health.approved} "
        f"denied={health.denied} expired={health.expired} "
        f"oldest_pending_age_seconds={health.oldest_pending_age_seconds}"
    )
    requests = service.list_approval_requests(
        statuses=statuses,
        directory_path=directory_path,
        direct_only=direct_only,
        session_id=session_id,
        vm_id=vm_id,
        tool_name=tool,
        min_age_seconds=older_than_seconds,
    )
    for request in requests:
        age_seconds = int((datetime.now(cli_common.UTC) - request.created_at).total_seconds())
        expires_in_seconds = int(
            (request.expires_at - datetime.now(cli_common.UTC)).total_seconds()
        )
        typer.echo(
            "\t".join(
                [
                    request.request_id,
                    request.status.value,
                    request.directory_path,
                    request.session_id,
                    request.vm_id or "-",
                    request.tool_name or request.action_type,
                    f"age={age_seconds}s",
                    f"expires_in={expires_in_seconds}s",
                    request.human_summary,
                ]
            )
        )
        if show_payload:
            review = service.get_approval_review(request.request_id)
            typer.echo(json.dumps(review["redacted_payload"], indent=2, sort_keys=True))


@cli_app.app.command(
    "show-approval",
    rich_help_panel="Sessions and Feedback",
    short_help="Show approval request details for review.",
)
def show_approval(
    request_id: str,
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
) -> None:
    service = cli_runtime.build_admin_runtime(config).approval_audit_admin
    try:
        payload = service.get_approval_review(request_id)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(json.dumps(payload, indent=2, sort_keys=True))

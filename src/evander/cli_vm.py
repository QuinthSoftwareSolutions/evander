"""Evander vm commands."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Annotated

import typer

from evander.audit import AuditEventType
from evander.config import GatewayConfig

from . import cli_app, cli_common, cli_runtime, cli_session_support


@cli_app.app.command(
    "destroy-vm",
    rich_help_panel="VM Operations",
    short_help="Destroy a VM and prune local session state.",
)
def destroy_vm(
    target: str,
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
) -> None:
    service = cli_runtime.build_admin_runtime(config).vm_agent_admin
    result = service.destroy_vm(target)
    cli_common._echo_action_result_or_exit(result)


@cli_app.app.command(
    "list-vms",
    rich_help_panel="VM Operations",
    short_help="List known VMs.",
)
def list_vms(
    vm_user: Annotated[
        str, typer.Option("--vm-user", help="Guest user to inspect over SSH.")
    ] = "agent",
    all_vms: Annotated[
        bool,
        typer.Option("--all", help="Include inactive libvirt VMs in the output."),
    ] = False,
    omit_state: Annotated[
        bool,
        typer.Option(
            "--omit-state",
            help="Skip SSH guest state inspection for faster VM listings.",
        ),
    ] = False,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable VM summaries."),
    ] = False,
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
) -> None:
    if as_json:
        service = cli_runtime.build_admin_runtime(config).vm_agent_admin
        payload = [
            summary.model_dump(mode="json")
            for summary in service.list_vms(
                include_inactive=all_vms, omit_state=omit_state, vm_user=vm_user
            )
        ]
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return

    script_path = Path(__file__).resolve().parents[2] / "scripts" / "list_running_vms.sh"
    cmd = [str(script_path)]
    if all_vms:
        cmd.append("--all")
    if omit_state:
        cmd.append("--omit-state")
    cmd.append(vm_user)
    cli_common._run_subprocess_compat(cmd, check=True, capture_output=False, text=False)


@cli_app.app.command(
    "start-vm",
    rich_help_panel="VM Operations",
    short_help="Start a VM.",
)
def start_vm(
    target: str,
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
) -> None:
    service = cli_runtime.build_admin_runtime(config).vm_agent_admin
    cli_common._echo_action_result_or_exit(service.start_vm(target))


@cli_app.app.command(
    "shutdown-vm",
    rich_help_panel="VM Operations",
    short_help="Gracefully stop a VM.",
)
def shutdown_vm(
    target: str,
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
    timeout_seconds: Annotated[
        int,
        typer.Option("--timeout-seconds", min=1, help="How long to wait for graceful shutdown."),
    ] = 60,
) -> None:
    service = cli_runtime.build_admin_runtime(config).vm_agent_admin
    cli_common._echo_action_result_or_exit(
        service.shutdown_vm(target, timeout_seconds=timeout_seconds)
    )


@cli_app.app.command(
    "ssh",
    rich_help_panel="VM Operations",
    short_help="Open an SSH session.",
)
def ssh(
    target: str,
    vm_user: Annotated[str, typer.Option("--vm-user", help="Guest user to connect as.")] = "agent",
) -> None:
    vm_name = cli_session_support._resolve_vm_name(target)
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "ssh.sh"
    cli_common._run_subprocess_compat(
        [str(script_path), vm_name, vm_user], check=True, capture_output=False, text=False
    )


@cli_app.app.command(
    "logs",
    rich_help_panel="VM Operations",
    short_help="Tail guest agent logs.",
)
def logs(
    target: str,
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
    repo_name: Annotated[
        str | None,
        typer.Option(
            "--repo-name",
            help="Repo basename used to select the guest agent log.",
        ),
    ] = None,
    lines: Annotated[
        int,
        typer.Option(
            "--lines", min=1, help="Number of log lines to print from the guest agent log."
        ),
    ] = 10,
    vm_user: Annotated[
        str, typer.Option("--vm-user", help="Guest user to inspect over SSH.")
    ] = "agent",
) -> None:
    normalized = target.strip()
    is_legacy_issue_target = normalized.isdigit() or (
        normalized.startswith("vm-") and normalized[3:].isdigit()
    )
    if is_legacy_issue_target:
        _follow_agent_logs(
            target,
            lines=lines,
            repo_name=repo_name,
            vm_user=vm_user,
        )
        return

    service = cli_runtime.build_admin_runtime(config).vm_agent_admin
    output = service.get_agent_log_snapshot(
        target, lines=lines, repo_name=repo_name, vm_user=vm_user
    )
    typer.echo(output)


def _follow_agent_logs(
    target: str,
    *,
    lines: int,
    repo_name: str | None,
    vm_user: str,
    resolved_vm_name: str | None = None,
) -> None:
    normalized = target.strip()
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "tail_issue_vm_log.sh"
    if normalized.isdigit() or (normalized.startswith("vm-") and normalized[3:].isdigit()):
        issue_number = cli_session_support._resolve_issue_number_or_raise(target)
        cmd = [str(script_path), issue_number, str(lines)]
    else:
        vm_name = str(resolved_vm_name or "").strip() or cli_session_support._resolve_vm_name(
            target
        )
        cmd = [str(script_path), "--vm-name", vm_name, str(lines)]
    if repo_name is not None:
        cmd.append(repo_name)
        cmd.append(vm_user)
    elif vm_user != "agent":
        cmd.extend(["", vm_user])
    cli_common._run_subprocess_compat(cmd, check=True, capture_output=False, text=False)


def _container_log_delta(previous: str, current: str) -> str:
    if not current or current == previous:
        return ""
    if not previous:
        return current
    if current.startswith(previous):
        return current[len(previous) :]
    previous_lines = previous.splitlines(keepends=True)
    current_lines = current.splitlines(keepends=True)
    max_overlap = min(len(previous_lines), len(current_lines))
    overlap = 0
    for candidate in range(max_overlap, 0, -1):
        if previous_lines[-candidate:] == current_lines[:candidate]:
            overlap = candidate
            break
    return "".join(current_lines[overlap:])


def _container_log_tail(text: str, *, lines: int) -> str:
    selected = text.splitlines(keepends=True)[-max(1, int(lines)) :]
    return "".join(selected)


def _observe_container_agent_logs(
    service: object,
    *,
    session_id: str,
    execution_id: str | None,
    lines: int,
    poll_interval_seconds: float = 1.0,
) -> None:
    get_snapshot = getattr(service, "get_admin_agent_log_snapshot")
    get_agent = getattr(service, "get_agent")
    log_target = execution_id or session_id
    previous = ""
    initialized = False
    while True:
        # Container snapshots support ``lines=0`` as the full execution log. Keeping
        # a complete prior snapshot makes following append-only logs lossless even
        # when more than the requested initial tail arrives between polls.
        current = str(
            get_snapshot(
                log_target,
                backend="container_worker_vm",
                view="executions",
                lines=0,
            )
            or ""
        )
        delta = (
            _container_log_delta(previous, current)
            if initialized
            else _container_log_tail(current, lines=lines)
        )
        if delta:
            typer.echo(delta, nl=False)
        previous = current
        initialized = True
        agent = get_agent(
            session_id,
            backend="container_worker_vm",
            reconcile_containers=True,
        )
        if str(getattr(agent, "status", "") or "").strip().casefold() not in {
            "preparing",
            "starting",
            "running",
        }:
            return
        time.sleep(max(0.05, float(poll_interval_seconds)))


def _set_agent_control_state(target: str, *, action: str, vm_user: str, config: Path) -> None:
    normalized = target.strip()
    try:
        cfg = cli_runtime._load_runtime_config_from_option(config)
    except ValueError:
        runtime_dir = cli_runtime._runtime_dir_for_config_option(config)
        cfg = GatewayConfig(runtime_dir=runtime_dir)
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "set_issue_agent_state.sh"
    if normalized.isdigit() or (normalized.startswith("vm-") and normalized[3:].isdigit()):
        issue_number = cli_session_support._resolve_issue_number_or_raise(target)
        cmd = [
            str(script_path),
            action,
            issue_number,
            vm_user,
            "--host-state-path",
            str(cfg.host_agent_control_state_path),
        ]
    else:
        vm_name = cli_session_support._resolve_vm_name(target)
        cmd = [
            str(script_path),
            "--state",
            action,
            "--vm-name",
            vm_name,
            "--vm-user",
            vm_user,
            "--host-state-path",
            str(cfg.host_agent_control_state_path),
        ]
    cli_common._run_subprocess_compat(
        cmd,
        check=True,
        capture_output=False,
        text=False,
    )


@cli_app.app.command(
    "disable-agent",
    rich_help_panel="VM Operations",
    short_help="Pause the guest agent.",
)
def disable_agent(
    target: str,
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
    vm_user: Annotated[str, typer.Option("--vm-user", help="Guest user to connect as.")] = "agent",
) -> None:
    _set_agent_control_state(target, action="disable", vm_user=vm_user, config=config)


@cli_app.app.command(
    "enable-agent",
    rich_help_panel="VM Operations",
    short_help="Allow the guest agent to run.",
)
def enable_agent(
    target: str,
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
    vm_user: Annotated[str, typer.Option("--vm-user", help="Guest user to connect as.")] = "agent",
) -> None:
    _set_agent_control_state(target, action="enable", vm_user=vm_user, config=config)


@cli_app.app.command(
    "resume-agent",
    rich_help_panel="VM Operations",
    short_help="Resume an agent with a prompt.",
)
def resume_agent(
    target: str,
    message: Annotated[
        str,
        typer.Option(
            "--message",
            help="Prompt passed to the agent resume command.",
        ),
    ],
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
    observe: Annotated[
        bool,
        typer.Option(
            "--observe",
            help="Tail the guest agent log after resuming the agent instead of returning immediately.",
        ),
    ] = False,
    lines: Annotated[
        int,
        typer.Option(
            "--lines", min=1, help="Number of log lines to print before following when observing."
        ),
    ] = 10,
    vm_user: Annotated[str, typer.Option("--vm-user", help="Guest user to connect as.")] = "agent",
    reasoning_effort: Annotated[
        str | None,
        typer.Option("--reasoning-effort", help="Native reasoning level; omit to inherit."),
    ] = None,
    fast_mode: Annotated[
        bool | None,
        typer.Option(
            "--fast-mode/--no-fast-mode",
            help="Codex Fast mode; omit to inherit the session default.",
        ),
    ] = None,
) -> None:
    normalized = target.strip()
    cfg = cli_runtime._load_runtime_config_from_option(config)
    runtime = cli_runtime.build_runtime(cfg)
    admin = cli_runtime.build_admin_runtime(config).vm_agent_admin
    sessions = cli_session_support._runtime_sessions_by_id(runtime)
    try:
        resolved_session_id, _ = cli_session_support._resolve_session_target(normalized, sessions)
    except typer.BadParameter as exc:
        raise typer.BadParameter(
            f"Cannot resolve resume target {target!r} to a session. "
            "Use a session id, issue number, or vm name with an existing session."
        ) from exc
    try:
        agent_model = admin.resolve_agent_model_for_target(resolved_session_id)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if normalized.isdigit() or (normalized.startswith("vm-") and normalized[3:].isdigit()):
        issue_number = cli_session_support._resolve_issue_number_or_raise(target)
        vm_name = f"vm-{issue_number}"
    else:
        vm_name = cli_session_support._resolve_vm_name(target)

    runtime.audit.log_decision(
        event_type=AuditEventType.VM_START_ATTEMPTED,
        session_id=resolved_session_id,
        vm_id=vm_name,
        decision="attempted",
        reason="resume-agent-command",
    )
    runtime.audit.log_decision(
        event_type=AuditEventType.RESUME_ATTEMPTED,
        session_id=resolved_session_id,
        vm_id=vm_name,
        decision="attempted",
        reason="resume-agent-command",
    )
    try:
        result = admin.enqueue_agent_prompt(
            resolved_session_id,
            message,
            model=agent_model,
            reasoning_effort=reasoning_effort,
            fast_mode=fast_mode,
            vm_user=vm_user,
            resume_mode="same_context",
        )
    except Exception as exc:
        reason = f"resume-agent-error-{type(exc).__name__}"
        runtime.audit.log_decision(
            event_type=AuditEventType.VM_START_FAILED,
            session_id=resolved_session_id,
            vm_id=vm_name,
            decision="failed",
            reason=reason,
            args={"error": str(exc)},
        )
        runtime.audit.log_decision(
            event_type=AuditEventType.RESUME_FAILED,
            session_id=resolved_session_id,
            vm_id=vm_name,
            decision="failed",
            reason=reason,
            args={"error": str(exc)},
        )
        raise

    result_vm_name = str(getattr(result, "vm_name", None) or vm_name)
    if bool(getattr(result, "queued", False)):
        queue_key = str(getattr(result, "queue_key", None) or "").strip()
        result_message = str(getattr(result, "message", None) or "").strip()
        typer.echo(result_message or f"Queued prompt for {result_vm_name} (queue_key={queue_key})")
        if observe:
            typer.echo("Prompt is queued; log observation will be available after dispatch.")
        return

    if bool(getattr(result, "success", False)):
        runtime.audit.log_decision(
            event_type=AuditEventType.VM_START_SUCCEEDED,
            session_id=resolved_session_id,
            vm_id=result_vm_name,
            decision="succeeded",
            reason="resume-agent-dispatched",
        )
        runtime.audit.log_decision(
            event_type=AuditEventType.RESUME_SUCCEEDED,
            session_id=resolved_session_id,
            vm_id=result_vm_name,
            decision="succeeded",
            reason="resume-agent-dispatched",
        )
        if observe:
            if str(getattr(result, "provider", "") or "").strip() == "container_worker_vm":
                provider_details = getattr(result, "provider_details", {})
                execution_id = (
                    str(provider_details.get("execution_id") or "").strip()
                    if isinstance(provider_details, dict)
                    else ""
                )
                _observe_container_agent_logs(
                    admin,
                    session_id=(
                        str(getattr(result, "session_id", None) or resolved_session_id).strip()
                    ),
                    execution_id=execution_id or None,
                    lines=lines,
                )
            else:
                _follow_agent_logs(
                    target,
                    repo_name=None,
                    lines=lines,
                    vm_user=vm_user,
                    resolved_vm_name=result_vm_name,
                )
        return

    failure_message = str(getattr(result, "message", None) or "Agent resume failed").strip()
    reason = "resume-agent-dispatch-failed"
    runtime.audit.log_decision(
        event_type=AuditEventType.VM_START_FAILED,
        session_id=resolved_session_id,
        vm_id=vm_name,
        decision="failed",
        reason=reason,
        args={"error": failure_message},
    )
    runtime.audit.log_decision(
        event_type=AuditEventType.RESUME_FAILED,
        session_id=resolved_session_id,
        vm_id=vm_name,
        decision="failed",
        reason=reason,
        args={"error": failure_message},
    )
    typer.echo(failure_message, err=True)
    raise typer.Exit(code=1)


@cli_app.app.command(
    "create-vm",
    rich_help_panel="VM Operations",
    short_help="Create and bootstrap a standalone VM.",
)
def create_vm(
    vm_name: str,
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
    directory_path: Annotated[
        str,
        typer.Option("--directory-path", help="Directory path that owns the new VM/session."),
    ] = "root",
    session_id: Annotated[
        str | None,
        typer.Option(
            "--session-id", help="Session identifier to create and pass into the VM launch flow."
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
        bool,
        typer.Option("--nuget", help="Bootstrap NuGet relay configuration into the guest."),
    ] = False,
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
    reasoning_effort: Annotated[
        str | None,
        typer.Option("--reasoning-effort", help="Native reasoning level; omit to inherit."),
    ] = None,
    fast_mode: Annotated[
        bool | None,
        typer.Option(
            "--fast-mode/--no-fast-mode",
            help="Codex Fast mode; omit to inherit the session default.",
        ),
    ] = None,
) -> None:
    if mssql_init_root is not None and not mssql:
        raise typer.BadParameter("--mssql-init-root requires --mssql.")
    service = cli_runtime.build_admin_runtime(config).vm_agent_admin
    result = service.create_vm(
        vm_name=vm_name,
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
        directory_path=directory_path,
        model=model,
        reasoning_effort=reasoning_effort,
        fast_mode=fast_mode,
        model_provider_mode=model_provider_mode,
    )
    cli_common._echo_action_result_or_exit(result)


@cli_app.app.command(
    "clone-repo-into-vm",
    rich_help_panel="VM Operations",
    short_help="Clone a repo into an existing VM/session.",
)
def clone_repo_into_vm(
    vm_name: str,
    managed_repo_id: Annotated[
        str,
        typer.Option(
            "--managed-repo-id",
            help="Directory-scoped managed repository ID to clone into the guest.",
        ),
    ],
    clone_branch: Annotated[
        str | None,
        typer.Option(
            "--clone-branch",
            help="Branch to clone and record as the agent's main branch in the guest.",
        ),
    ] = None,
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
    directory_path: Annotated[
        str,
        typer.Option("--directory-path", help="Directory path that owns the VM/session."),
    ] = "root",
    session_id: Annotated[
        str | None,
        typer.Option("--session-id", help="Existing session identifier associated with the VM."),
    ] = None,
    vm_user: Annotated[str, typer.Option("--vm-user", help="Guest user to configure.")] = "agent",
) -> None:
    service = cli_runtime.build_admin_runtime(config).vm_agent_admin
    result = service.clone_repo_into_vm(
        vm_name=vm_name,
        session_id=session_id,
        managed_repo_id=managed_repo_id,
        clone_branch=clone_branch,
        vm_user=vm_user,
        directory_path=directory_path,
    )
    cli_common._echo_action_result_or_exit(result)


@cli_app.app.command(
    "bootstrap-python-in-vm",
    rich_help_panel="VM Operations",
    short_help="Bootstrap Python tooling inside an existing guest repo.",
)
def bootstrap_python_in_vm(
    vm_name: str,
    host_checkout: Annotated[
        str,
        typer.Option(
            "--host-checkout",
            help="Host checkout root used to build the guest-local Python environment.",
        ),
    ],
    target_dir: Annotated[
        str,
        typer.Option("--target-dir", help="Guest repo root inside the VM."),
    ],
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
    session_id: Annotated[
        str | None,
        typer.Option("--session-id", help="Existing session identifier associated with the VM."),
    ] = None,
    vm_user: Annotated[str, typer.Option("--vm-user", help="Guest user to configure.")] = "agent",
) -> None:
    service = cli_runtime.build_admin_runtime(config).vm_agent_admin
    result = service.bootstrap_python_in_vm(
        vm_name=vm_name,
        session_id=session_id,
        host_checkout=host_checkout,
        target_dir=target_dir,
        vm_user=vm_user,
    )
    cli_common._echo_action_result_or_exit(result)


@cli_app.app.command(
    "bootstrap-frontend-in-vm",
    rich_help_panel="VM Operations",
    short_help="Bootstrap frontend dependencies into an existing guest repo.",
)
def bootstrap_frontend_in_vm(
    vm_name: str,
    host_checkout: Annotated[
        str,
        typer.Option(
            "--host-checkout", help="Host checkout root used to seed frontend dependencies."
        ),
    ],
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
    install_root: Annotated[
        list[str],
        typer.Option(
            "--install-root",
            help="Install root inside the host checkout. Repeat for multiple roots.",
        ),
    ] = [],
    session_id: Annotated[
        str | None,
        typer.Option("--session-id", help="Existing session identifier associated with the VM."),
    ] = None,
    vm_user: Annotated[str, typer.Option("--vm-user", help="Guest user to configure.")] = "agent",
) -> None:
    service = cli_runtime.build_admin_runtime(config).vm_agent_admin
    result = service.bootstrap_frontend_in_vm(
        vm_name=vm_name,
        session_id=session_id,
        host_checkout=host_checkout,
        install_roots=install_root,
        vm_user=vm_user,
    )
    cli_common._echo_action_result_or_exit(result)

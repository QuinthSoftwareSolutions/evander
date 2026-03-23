from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from itertools import count
from pathlib import Path
from typing import Annotated

import httpx
import typer
from pydantic import ValidationError

from .ado_issue_worker import run_worker_command
from .config import GatewayConfig, load_config
from .email.providers import JsonEmailProvider, MultiMailboxEmailProvider
from .models import ApprovalStatus, DiscordCommandEnvelope, EmailMessage, SessionManifest, ToolName
from .runtime import build_runtime

app = typer.Typer(help="Utilities for the agent sandbox host control plane.")

ConfigOption = Annotated[Path, typer.Option("--config", help="Path to the gateway YAML config.")]
_discord_seed_sequence = count()
UTC = timezone.utc


@dataclass(slots=True)
class PathStatus:
    path: str
    exists: bool
    is_dir: bool
    readable: bool
    writable: bool


@dataclass(slots=True)
class EndpointStatus:
    name: str
    url: str
    reachable: bool
    status_code: int | None
    error: str | None


@dataclass(slots=True)
class UnitStatus:
    unit: str
    state: str
    available: bool
    detail: str | None


def _path_status(path: Path, *, is_dir: bool = False) -> PathStatus:
    return PathStatus(
        path=str(path),
        exists=path.exists(),
        is_dir=path.is_dir() if path.exists() else is_dir,
        readable=os.access(path, os.R_OK),
        writable=os.access(path, os.W_OK),
    )


def _normalize_host_for_local_check(host: str) -> str:
    if host in {"0.0.0.0", "::", ""}:
        return "127.0.0.1"
    return host


def _get_systemd_unit_state(unit: str) -> UnitStatus:
    try:
        result = subprocess.run(
            ["systemctl", "is-active", unit],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return UnitStatus(unit=unit, state="unavailable", available=False, detail="systemctl not found")

    detail = result.stderr.strip() or None
    if result.returncode == 0:
        return UnitStatus(unit=unit, state=result.stdout.strip() or "active", available=True, detail=detail)

    state = result.stdout.strip() or "unknown"
    return UnitStatus(unit=unit, state=state, available=True, detail=detail)


def _check_health_endpoint(name: str, url: str) -> EndpointStatus:
    try:
        response = httpx.get(url, timeout=3.0)
    except Exception as exc:
        return EndpointStatus(name=name, url=url, reachable=False, status_code=None, error=str(exc))
    return EndpointStatus(
        name=name,
        url=url,
        reachable=response.status_code < 500,
        status_code=response.status_code,
        error=None,
    )


def _format_rows(rows: list[tuple[str, str]]) -> str:
    key_width = max(len(key) for key, _ in rows)
    return "\n".join(f"{key:<{key_width}}  {value}" for key, value in rows)


def _default_issue_session_id(issue_number: str) -> str:
    return f"issue-{issue_number}-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{os.getpid()}"


def _resolve_vm_name(target: str) -> str:
    normalized = target.strip()
    if normalized.isdigit():
        return f"vm-{normalized}"
    return normalized


def _resolve_issue_number_or_raise(target: str) -> str:
    normalized = target.strip()
    if normalized.isdigit():
        return normalized
    if normalized.startswith("vm-") and normalized[3:].isdigit():
        return normalized[3:]
    raise typer.BadParameter(
        f"Could not resolve issue number from target {target!r}. "
        "Pass an issue number or vm-<issue-number>."
    )


def _read_issue_number_from_yaml(issue_path: Path) -> str:
    text = issue_path.read_text(encoding="utf-8")
    patterns = [
        r"(?m)^\s*issue_number\s*:\s*[\"']?([0-9]+)[\"']?\s*$",
        r"(?m)^\s*number\s*:\s*[\"']?([0-9]+)[\"']?\s*$",
        r"(?m)^\s*id\s*:\s*[\"']?([0-9]+)[\"']?\s*$",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)
    raise typer.BadParameter(
        f"Could not read issue number from YAML file: {issue_path}. "
        "Expected one of: issue_number:, number:, or id:."
    )


def _resolve_issue_number(issue_input: str) -> str:
    candidate = Path(issue_input)
    if candidate.is_file():
        return _read_issue_number_from_yaml(candidate)
    return issue_input


def _parse_pr_feedback_streams(values: list[str]) -> list[dict[str, str]]:
    streams: list[dict[str, str]] = []
    for raw in values:
        provider, separator, remainder = raw.partition(":")
        repo, separator2, pr_id = remainder.rpartition(":")
        provider = provider.strip()
        repo = repo.strip()
        pr_id = pr_id.strip()
        if not separator or not separator2 or not provider or not repo or not pr_id:
            raise typer.BadParameter(
                f"Invalid --pr-feedback-stream value {raw!r}. Expected provider:repo:pr_id."
            )
        streams.append({"provider": provider, "repo": repo, "pr_id": pr_id})
    return streams


def _build_issue_session_manifest(
    *,
    cfg: GatewayConfig,
    session_id: str,
    vm_id: str,
    ttl_hours: int | None,
    discord_channels: list[str],
    email_mailboxes: list[str],
    email_folders: list[str],
) -> SessionManifest:
    allowed_tools: list[ToolName] = [
        ToolName.APPROVAL_SUBMIT,
        ToolName.APPROVAL_CHECK,
    ]

    if discord_channels:
        allowed_tools.extend(
            [
                ToolName.DISCORD_GET_COMMANDS,
                ToolName.DISCORD_POST_STATUS,
            ]
        )

    email_requested = bool(email_mailboxes or email_folders)
    resolved_email_mailboxes = email_mailboxes or (cfg.email_mailbox_allowlist if email_requested else [])
    resolved_email_folders = email_folders or (
        sorted(
            {
                folder
                for folders in cfg.email_folder_allowlist_by_mailbox.values()
                for folder in folders
            }
        )
        if email_requested
        else []
    )
    if email_requested:
        allowed_tools.extend(
            [
                ToolName.EMAIL_LIST_UNREAD,
                ToolName.EMAIL_GET_MESSAGE,
                ToolName.EMAIL_CREATE_DRAFT,
            ]
        )
        if cfg.email_send_enabled:
            allowed_tools.append(ToolName.EMAIL_SEND_APPROVED)

    unique_tools = list(dict.fromkeys(allowed_tools))
    return SessionManifest(
        session_id=session_id,
        vm_id=vm_id,
        allowed_tools=unique_tools,
        discord_channel_allowlist=discord_channels,
        email_folder_allowlist=resolved_email_folders,
        email_mailbox_allowlist=resolved_email_mailboxes,
        email_folder_allowlist_by_mailbox=cfg.email_folder_allowlist_by_mailbox,
        email_draft_recipient_allowlist=cfg.email_draft_recipient_allowlist,
        email_draft_domain_allowlist=cfg.email_draft_domain_allowlist,
        email_send_enabled=cfg.email_send_enabled,
        expires_at=datetime.now(UTC) + timedelta(hours=ttl_hours or 8),
        metadata={},
    )


@app.command("init-state")
def init_state(config: ConfigOption = Path("config/gateway.yaml")) -> None:
    cfg = load_config(config)
    build_runtime(cfg)
    typer.echo(f"Initialized runtime state under {cfg.runtime_dir}")


@app.command("status")
def status(config: ConfigOption = Path("config/gateway.yaml"), as_json: Annotated[bool, typer.Option("--json")] = False) -> None:
    report: dict[str, object] = {
        "generated_at": datetime.now(UTC).isoformat(),
        "overall": "ok",
        "config": {"loaded": False, "path": str(config), "error": None},
        "runtime_paths": [],
        "systemd_units": [],
        "health": [],
        "sessions": {
            "total": 0,
            "active": 0,
            "expired": 0,
            "next_expiration": None,
            "last_expiration": None,
        },
    }

    cfg: GatewayConfig | None = None
    try:
        cfg = load_config(config)
        report["config"] = {"loaded": True, "path": str(config), "error": None}
    except Exception as exc:
        report["overall"] = "degraded"
        report["config"] = {"loaded": False, "path": str(config), "error": str(exc)}

    if cfg is not None:
        runtime_paths = [
            _path_status(cfg.runtime_dir, is_dir=True),
            _path_status(cfg.session_store_path),
            _path_status(cfg.approval_store_path),
            _path_status(cfg.draft_store_path),
            _path_status(cfg.email_store_path),
            _path_status(cfg.email_cursor_store_path),
            _path_status(cfg.discord_command_store_path),
            _path_status(cfg.audit_log_path),
        ]
        report["runtime_paths"] = [asdict(path_status) for path_status in runtime_paths]

        for path_status in runtime_paths:
            if not path_status.exists or not path_status.readable or not path_status.writable:
                report["overall"] = "degraded"

        units = [
            "agent-sandbox-firewall.service",
            "agent-sandbox-gateway.service",
            "agent-sandbox-openai-relay.service",
        ]
        unit_statuses = [_get_systemd_unit_state(unit) for unit in units]
        report["systemd_units"] = [asdict(unit_status) for unit_status in unit_statuses]
        for unit_status in unit_statuses:
            if unit_status.available and unit_status.state != "active":
                report["overall"] = "degraded"

        gateway_health_url = cfg.gateway_url
        relay_health_url = (
            f"http://{_normalize_host_for_local_check(cfg.openai_relay_host)}:{cfg.openai_relay_port}/healthz"
        )
        health_results = [
            _check_health_endpoint("gateway", gateway_health_url),
            _check_health_endpoint("openai_relay", relay_health_url),
        ]
        report["health"] = [asdict(result) for result in health_results]
        for endpoint in health_results:
            if not endpoint.reachable:
                report["overall"] = "degraded"

        if cfg.session_store_path.exists():
            try:
                raw_sessions = json.loads(cfg.session_store_path.read_text(encoding="utf-8"))
                sessions = [
                    SessionManifest.model_validate(value)
                    for value in raw_sessions.values()
                ]
            except (json.JSONDecodeError, ValidationError, OSError) as exc:
                report["overall"] = "degraded"
                report["sessions"] = {
                    "total": 0,
                    "active": 0,
                    "expired": 0,
                    "next_expiration": None,
                    "last_expiration": None,
                    "error": str(exc),
                }
            else:
                now = datetime.now(UTC)
                active = [session for session in sessions if session.expires_at > now]
                expired = [session for session in sessions if session.expires_at <= now]
                next_expiration = min((session.expires_at for session in active), default=None)
                last_expiration = max((session.expires_at for session in sessions), default=None)
                report["sessions"] = {
                    "total": len(sessions),
                    "active": len(active),
                    "expired": len(expired),
                    "next_expiration": next_expiration.isoformat() if next_expiration else None,
                    "last_expiration": last_expiration.isoformat() if last_expiration else None,
                }

    if as_json:
        typer.echo(json.dumps(report, indent=2, sort_keys=True))
        return

    config_info = report["config"]
    config_loaded = "yes" if config_info["loaded"] else "no"
    rows: list[tuple[str, str]] = [
        ("Overall", str(report["overall"])),
        ("Generated", str(report["generated_at"])),
        ("Config loaded", config_loaded),
        ("Config path", str(config_info["path"])),
    ]
    if config_info["error"]:
        rows.append(("Config error", str(config_info["error"])))

    typer.echo("Agent Sandbox Status")
    typer.echo(_format_rows(rows))

    if report["runtime_paths"]:
        typer.echo("\nRuntime paths")
        for path_status in report["runtime_paths"]:
            status = "ok" if path_status["exists"] and path_status["readable"] and path_status["writable"] else "degraded"
            typer.echo(
                f"- {path_status['path']}: {status} "
                f"(exists={path_status['exists']} read={path_status['readable']} write={path_status['writable']})"
            )

    if report["systemd_units"]:
        typer.echo("\nSystemd units")
        for unit_status in report["systemd_units"]:
            availability = "available" if unit_status["available"] else "unavailable"
            typer.echo(f"- {unit_status['unit']}: {unit_status['state']} ({availability})")

    if report["health"]:
        typer.echo("\nHealth endpoints")
        for endpoint in report["health"]:
            status = "ok" if endpoint["reachable"] else "degraded"
            status_code = endpoint["status_code"] if endpoint["status_code"] is not None else "n/a"
            typer.echo(f"- {endpoint['name']}: {status} (url={endpoint['url']} status_code={status_code})")
            if endpoint["error"]:
                typer.echo(f"  error: {endpoint['error']}")

    session_summary = report["sessions"]
    typer.echo("\nSessions")
    typer.echo(
        _format_rows(
            [
                ("Total", str(session_summary["total"])),
                ("Active", str(session_summary["active"])),
                ("Expired", str(session_summary["expired"])),
                ("Next expiration", str(session_summary["next_expiration"] or "n/a")),
                ("Last expiration", str(session_summary["last_expiration"] or "n/a")),
            ]
        )
    )


@app.command("create-session")
def create_session(
    session_id: str,
    config: ConfigOption = Path("config/gateway.yaml"),
    vm_id: Annotated[str | None, typer.Option("--vm-id", help="VM identifier to bind to the session.")] = None,
    discord_channel: Annotated[
        list[str] | None,
        typer.Option("--discord-channel", help="Allowlisted Discord control channel."),
    ] = None,
    email_folder: Annotated[
        list[str] | None,
        typer.Option("--email-folder", help="Allowlisted email folder."),
    ] = None,
    ttl_hours: Annotated[int | None, typer.Option("--ttl-hours", help="Session time to live in hours.")] = None,
) -> None:
    cfg = load_config(config)
    runtime = build_runtime(cfg)
    manifest = SessionManifest(
        session_id=session_id,
        vm_id=vm_id,
        allowed_tools=[
            ToolName.DISCORD_GET_COMMANDS,
            ToolName.DISCORD_POST_STATUS,
            ToolName.EMAIL_LIST_UNREAD,
            ToolName.EMAIL_GET_MESSAGE,
            ToolName.EMAIL_CREATE_DRAFT,
            ToolName.APPROVAL_SUBMIT,
            ToolName.APPROVAL_CHECK,
            ToolName.HAS_PENDING_FEEDBACK,
            ToolName.LIST_UNREAD_FEEDBACK,
            ToolName.POST_PR_FEEDBACK_REPLY,
        ],
        discord_channel_allowlist=discord_channel or [],
        email_folder_allowlist=email_folder or [],
        email_mailbox_allowlist=cfg.email_mailbox_allowlist,
        email_folder_allowlist_by_mailbox=cfg.email_folder_allowlist_by_mailbox,
        email_draft_recipient_allowlist=cfg.email_draft_recipient_allowlist,
        email_draft_domain_allowlist=cfg.email_draft_domain_allowlist,
        email_send_enabled=cfg.email_send_enabled,
        expires_at=datetime.now(UTC) + timedelta(hours=ttl_hours or 8),
    )
    if manifest.email_send_enabled:
        manifest.allowed_tools.append(ToolName.EMAIL_SEND_APPROVED)
    runtime.sessions.put(session_id, manifest)
    typer.echo(f"Created session manifest {session_id}")


@app.command("prune-sessions")
def prune_sessions(
    config: ConfigOption = Path("config/gateway.yaml"),
    expired_only: Annotated[
        bool,
        typer.Option(
            "--expired-only/--all",
            help="Delete only expired session manifests, or all session manifests.",
        ),
    ] = True,
) -> None:
    cfg = load_config(config)
    runtime = build_runtime(cfg)
    sessions = runtime.sessions.read_all()
    now = datetime.now(UTC)

    if expired_only:
        keys_to_delete = [
            session_id
            for session_id, session in sessions.items()
            if session.expires_at <= now
        ]
    else:
        keys_to_delete = list(sessions.keys())

    for session_id in keys_to_delete:
        runtime.sessions.delete(session_id)

    mode = "expired" if expired_only else "all"
    typer.echo(f"Pruned {len(keys_to_delete)} {mode} session(s)")


@app.command("list-vms")
def list_vms(
    vm_user: Annotated[str, typer.Option("--vm-user", help="Guest user to inspect over SSH.")] = "agent",
) -> None:
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "list_running_vms.sh"
    subprocess.run([str(script_path), vm_user], check=True)


@app.command("ssh")
def ssh(
    target: str,
    vm_user: Annotated[str, typer.Option("--vm-user", help="Guest user to connect as.")] = "agent",
) -> None:
    vm_name = _resolve_vm_name(target)
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "ssh.sh"
    subprocess.run([str(script_path), vm_name, vm_user], check=True)


@app.command("logs")
def logs(
    target: str,
    repo_name: Annotated[
        str | None,
        typer.Option("--repo-name", help="Repo basename used to select the guest Codex log."),
    ] = None,
    vm_user: Annotated[str, typer.Option("--vm-user", help="Guest user to inspect over SSH.")] = "agent",
) -> None:
    issue_number = _resolve_issue_number_or_raise(target)
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "tail_issue_vm_log.sh"
    cmd = [str(script_path), issue_number]
    if repo_name is not None:
        cmd.append(repo_name)
    cmd.append(vm_user)
    subprocess.run(cmd, check=True)


@app.command("resume-agent")
def resume_agent(
    target: str,
    message: Annotated[
        str,
        typer.Option("--message", help="Prompt passed to codex exec resume --last."),
    ],
    vm_user: Annotated[str, typer.Option("--vm-user", help="Guest user to connect as.")] = "agent",
) -> None:
    issue_number = _resolve_issue_number_or_raise(target)
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "resume_issue_agent.sh"
    subprocess.run([str(script_path), message, issue_number, vm_user], check=True)


@app.command("create-issue-vm")
def create_issue_vm(
    issue: str,
    config: ConfigOption = Path("config/gateway.yaml"),
    session_id: Annotated[
        str | None,
        typer.Option("--session-id", help="Session identifier to create and pass into the VM setup flow."),
    ] = None,
    repo_source: Annotated[
        str | None,
        typer.Option(
            "--repo-source",
            help="Host repo path or remote URL to mirror for guest Git access.",
        ),
    ] = None,
    vm_user: Annotated[str, typer.Option("--vm-user", help="Guest user to configure.")] = "agent",
    ttl_hours: Annotated[int | None, typer.Option("--ttl-hours", help="Session time to live in hours.")] = None,
    discord_channel: Annotated[
        list[str],
        typer.Option("--discord-channel", help="Allowlisted Discord control channel."),
    ] = [],
    email_mailbox: Annotated[
        list[str],
        typer.Option("--email-mailbox", help="Allowlisted mailbox id. Repeat for multiple mailboxes."),
    ] = [],
    email_folder: Annotated[
        list[str],
        typer.Option("--email-folder", help="Allowlisted email folder. Repeat for multiple folders."),
    ] = [],
    dotnet: Annotated[bool, typer.Option("--dotnet", help="Bootstrap the host .NET SDK into the guest.")] = False,
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
        bool, typer.Option("--playwright", help="Bootstrap the host-seeded Playwright runtime into the guest.")
    ] = False,
    frontend_deps: Annotated[
        bool,
        typer.Option(
            "--frontend-deps",
            help="Bootstrap host-seeded frontend install roots into the guest.",
        ),
    ] = False,
    frontend_host_checkout: Annotated[
        str | None,
        typer.Option(
            "--frontend-host-checkout",
            help="Host checkout root for frontend install roots.",
        ),
    ] = None,
    frontend_install_root: Annotated[
        list[str],
        typer.Option(
            "--frontend-install-root",
            help="Install root inside the host checkout. Repeat for multiple roots.",
        ),
    ] = [],
    start_agent: Annotated[
        bool,
        typer.Option("--start-agent/--no-start-agent", help="Start the guest Codex run after setup."),
    ] = True,
) -> None:
    issue_number = _resolve_issue_number(issue)
    resolved_session_id = session_id or _default_issue_session_id(issue_number)
    resolved_vm_name = f"vm-{issue_number}"

    cfg = load_config(config)
    runtime = build_runtime(cfg)
    if runtime.sessions.get(resolved_session_id) is not None:
        raise typer.BadParameter(f"Session already exists: {resolved_session_id}")
    if mssql and not os.environ.get("MSSQL_SA_PASSWORD", "").strip():
        raise typer.BadParameter("MSSQL_SA_PASSWORD must be set in the environment when --mssql is used.")
    if mssql_init_root is not None and not mssql:
        raise typer.BadParameter("--mssql-init-root requires --mssql.")

    manifest = _build_issue_session_manifest(
        cfg=cfg,
        session_id=resolved_session_id,
        vm_id=resolved_vm_name,
        ttl_hours=ttl_hours,
        discord_channels=discord_channel,
        email_mailboxes=email_mailbox,
        email_folders=email_folder,
    )
    runtime.sessions.put(resolved_session_id, manifest)

    setup_script = Path(__file__).resolve().parents[2] / "scripts" / "setup_issue_vm.sh"
    cmd = [str(setup_script)]
    if not start_agent:
        cmd.append("--dry-run")
    if dotnet:
        cmd.append("--dotnet")
    if nuget is not None:
        cmd.append("--nuget")
    if mssql:
        cmd.append("--mssql")
    if mssql_init_root is not None:
        cmd.extend(["--mssql-init-root", mssql_init_root])
    if playwright:
        cmd.append("--playwright")
    if frontend_deps:
        cmd.append("--frontend-deps")
    if frontend_host_checkout is not None:
        cmd.extend(["--frontend-host-checkout", frontend_host_checkout])
    for install_root in frontend_install_root:
        cmd.extend(["--frontend-install-root", install_root])

    cmd.append(issue)
    if nuget is not None:
        cmd.append(nuget)
    if repo_source is not None:
        cmd.append(repo_source)
    cmd.extend([resolved_session_id, vm_user])

    subprocess.run(cmd, check=True)
    typer.echo(
        f"Created issue VM session {resolved_session_id} for issue {issue_number} "
        f"using VM {resolved_vm_name}"
    )


@app.command("attach-pr-feedback")
def attach_pr_feedback(
    session_id: str,
    provider: Annotated[str, typer.Option("--provider", help="Feedback provider name.")] ,
    repo: Annotated[str, typer.Option("--repo", help="Provider repository identifier.")] ,
    pr_id: Annotated[str, typer.Option("--pr-id", help="Pull request identifier.")] ,
    config: ConfigOption = Path("config/gateway.yaml"),
) -> None:
    cfg = load_config(config)
    runtime = build_runtime(cfg)
    manifest = runtime.sessions.get(session_id)
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
        ToolName.LIST_UNREAD_FEEDBACK,
        ToolName.POST_PR_FEEDBACK_REPLY,
    ):
        if tool not in manifest.allowed_tools:
            manifest.allowed_tools.append(tool)

    runtime.sessions.put(session_id, manifest)
    typer.echo(
        f"Attached PR feedback stream {stream['provider']}:{stream['repo']}:{stream['pr_id']} "
        f"to session {session_id}"
    )


@app.command("approve")
def approve(
    request_id: str,
    approver: str,
    config: ConfigOption = Path("config/gateway.yaml"),
    approve: Annotated[bool, typer.Option("--approve/--deny", help="Approve or deny the request.")] = True,
) -> None:
    cfg = load_config(config)
    runtime = build_runtime(cfg)
    request = runtime.approvals.get(request_id)
    if request is None:
        raise typer.BadParameter(f"Unknown approval request: {request_id}")
    request.status = ApprovalStatus.APPROVED if approve else ApprovalStatus.DENIED
    request.approver = approver
    request.decision_at = datetime.now(UTC)
    runtime.approvals.put(request_id, request)
    typer.echo(f"Updated approval {request_id} to {request.status.value}")


@app.command("seed-email")
def seed_email(
    message_id: str,
    folder: str,
    sender: str,
    subject: str,
    body_text: str,
    mailbox_id: Annotated[str, typer.Option("--mailbox-id", help="Mailbox identifier.")] = "default",
    recipient: Annotated[
        list[str],
        typer.Option("--recipient", help="Repeat for each recipient address."),
    ] = [],
    config: ConfigOption = Path("config/gateway.yaml"),
) -> None:
    if not recipient:
        raise typer.BadParameter("At least one --recipient value is required.")
    cfg = load_config(config)
    runtime = build_runtime(cfg)
    message = EmailMessage(
        message_id=message_id,
        mailbox_id=mailbox_id,
        folder=folder,
        sender=sender,
        recipients=recipient,
        subject=subject,
        body_text=body_text,
    )
    provider = runtime.email_service.provider
    if isinstance(provider, JsonEmailProvider):
        provider.email_store.put(message_id, message)
        typer.echo(f"Seeded email {message_id}")
        return

    if isinstance(provider, MultiMailboxEmailProvider):
        mailbox_provider = provider.providers_by_mailbox.get(mailbox_id)
        if isinstance(mailbox_provider, JsonEmailProvider):
            mailbox_provider.email_store.put(message_id, message)
            typer.echo(f"Seeded email {message_id}")
            return

    raise typer.BadParameter(
        f"mailbox {mailbox_id!r} is not configured with json_store provider and cannot be locally seeded"
    )


@app.command("seed-discord-channel")
def seed_discord_channel(
    channel_id: str,
    config: ConfigOption = Path("config/gateway.yaml"),
) -> None:
    cfg = load_config(config)
    runtime = build_runtime(cfg)
    now = datetime.now(UTC)
    seed_cursor = str(int(now.timestamp() * 1_000_000) + next(_discord_seed_sequence))
    envelope = DiscordCommandEnvelope(
        command_id=seed_cursor,
        author_id="discord-channel-seed",
        channel_id=channel_id,
        content="seed",
        created_at=now,
        metadata={
            "source": "discord_channel_seed",
            "channel_id": channel_id,
            "author_id": "discord-channel-seed",
            "seeded_at": now.isoformat(),
        },
    )
    runtime.discord_service.command_store.put(seed_cursor, envelope)
    typer.echo(
        f"Seeded Discord channel {channel_id} with cursor {seed_cursor}. "
        "Use that cursor as the initial since_cursor for smoke tests."
    )


@app.command("poll-ado-backlog")
def poll_ado_backlog(
    log_level: Annotated[str, typer.Option("--log-level", help="Python logging level.")] = "INFO",
    reprocess: Annotated[
        int | None,
        typer.Option(
            "--reprocess",
            help="Force reprocess a specific work item ID by forgetting previous state first.",
        ),
    ] = None,
    forget: Annotated[
        int | None,
        typer.Option("--forget", help="Forget a specific work item ID in the processed store, then exit."),
    ] = None,
    list_unprocessed: Annotated[
        bool,
        typer.Option(
            "--list-unprocessed",
            help="List eligible work items that match the worker query but are not yet in the processed store.",
        ),
    ] = False,
) -> None:
    run_worker_command(
        log_level=log_level,
        reprocess=reprocess,
        forget=forget,
        list_unprocessed=list_unprocessed,
    )

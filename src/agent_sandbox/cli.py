from __future__ import annotations

from typing import Annotated
from datetime import UTC, datetime, timedelta
from pathlib import Path

import typer

from .config import load_config
from .models import ApprovalStatus, EmailMessage, SessionManifest, ToolName
from .runtime import build_runtime

app = typer.Typer(help="Utilities for the agent sandbox host control plane.")

ConfigOption = Annotated[Path, typer.Option("--config", help="Path to the gateway YAML config.")]


@app.command("init-state")
def init_state(config: ConfigOption = Path("config/gateway.yaml")) -> None:
    cfg = load_config(config)
    build_runtime(cfg)
    typer.echo(f"Initialized runtime state under {cfg.runtime_dir}")


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
    runtime.email_service.provider.email_store.put(message_id, message)
    typer.echo(f"Seeded email {message_id}")

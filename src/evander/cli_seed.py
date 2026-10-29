"""Evander seed commands."""

from __future__ import annotations

from datetime import datetime
from itertools import count
from typing import Annotated

import typer

from evander.email.providers import JsonEmailProvider, MultiMailboxEmailProvider
from evander.models import DiscordCommandEnvelope, EmailMessage

from . import cli_app, cli_common, cli_runtime

_discord_seed_sequence = count()


@cli_app.app.command(
    "seed-email",
    rich_help_panel="Seeding and Fixtures",
    short_help="Insert a test email.",
)
def seed_email(
    message_id: str,
    folder: str,
    sender: str,
    subject: str,
    body_text: str,
    mailbox_id: Annotated[
        str, typer.Option("--mailbox-id", help="Mailbox identifier.")
    ] = "default",
    recipient: Annotated[
        list[str],
        typer.Option("--recipient", help="Repeat for each recipient address."),
    ] = [],
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
) -> None:
    if not recipient:
        raise typer.BadParameter("At least one --recipient value is required.")
    cfg = cli_runtime._load_runtime_config_from_option(config)
    runtime = cli_runtime.build_runtime(cfg)
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


@cli_app.app.command(
    "seed-discord-channel",
    rich_help_panel="Seeding and Fixtures",
    short_help="Insert a test Discord cursor.",
)
def seed_discord_channel(
    channel_id: str,
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
) -> None:
    cfg = cli_runtime._load_runtime_config_from_option(config)
    runtime = cli_runtime.build_runtime(cfg)
    now = datetime.now(cli_common.UTC)
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

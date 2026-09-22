"""Evander auth commands."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, is_dataclass
from datetime import datetime
from typing import Annotated

import httpx
import typer

from evander.admin.composition import AdminRuntimeComponents
from evander.messaging.teams_auth import TeamsAuthError

from . import cli_app, cli_common, cli_runtime

_TEAMS_AUTH_SUMMARY_FIELDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Status", ("status", "state")),
    ("Backend", ("backend", "auth_backend", "provider")),
    ("Signed in", ("signed_in", "authenticated", "is_authenticated")),
    (
        "Account",
        ("account", "account_identifier", "user", "user_principal_name", "username", "email"),
    ),
    ("Tenant", ("tenant", "tenant_id", "tenant_name")),
    ("Authority", ("authority", "issuer", "environment")),
    ("Team ID", ("team_id",)),
    ("Channel name", ("channel_name", "display_name")),
    ("Channel ID", ("channel_id",)),
    ("Allowlist entry", ("allowlist_entry",)),
    ("Matches", ("match_count", "count")),
    ("Scopes", ("scopes", "permissions")),
    ("Expires at", ("expires_at", "expires", "expires_on")),
    ("Created at", ("created_at", "created")),
    ("Updated at", ("updated_at", "updated")),
    ("Verification URI", ("verification_uri", "verification_url", "login_uri")),
    ("Verification URI complete", ("verification_uri_complete",)),
    ("User code", ("user_code",)),
    ("Device code", ("device_code",)),
    ("Message", ("message", "instructions")),
    ("Store path", ("store_path", "path")),
)


def _teams_auth_is_sensitive_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "", key.casefold())
    return any(
        marker in normalized
        for marker in (
            "access",
            "refresh",
            "token",
            "secret",
            "password",
            "cookie",
            "authorization",
            "clientsecret",
            "idtoken",
        )
    )


def _teams_auth_result_payload(result: object) -> object:
    for attribute in ("data", "payload", "result"):
        value = getattr(result, attribute, None)
        if value is not None:
            return value
    return result


def _teams_auth_normalize_value(value: object) -> object:
    if hasattr(value, "model_dump") and callable(getattr(value, "model_dump")):
        return value.model_dump(mode="json")
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _teams_auth_normalize_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_teams_auth_normalize_value(item) for item in value]
    if isinstance(value, tuple):
        return [_teams_auth_normalize_value(item) for item in value]
    return value


def _teams_auth_redact(value: object) -> object:
    normalized = _teams_auth_normalize_value(value)
    if isinstance(normalized, dict):
        return {
            key: "<redacted>"
            if _teams_auth_is_sensitive_key(str(key))
            else _teams_auth_redact(item)
            for key, item in normalized.items()
        }
    if isinstance(normalized, list):
        return [_teams_auth_redact(item) for item in normalized]
    if isinstance(normalized, tuple):
        return [_teams_auth_redact(item) for item in normalized]
    return normalized


def _teams_auth_scalar_text(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, list):
        items = [_teams_auth_scalar_text(item) for item in value]
        filtered = [item for item in items if item]
        return ", ".join(filtered) if filtered else None
    return str(value)


def _teams_auth_extract_fields(payload: object) -> list[tuple[str, str]]:
    if not isinstance(payload, dict):
        return []

    rows: list[tuple[str, str]] = []
    for label, keys in _TEAMS_AUTH_SUMMARY_FIELDS:
        for key in keys:
            if key not in payload:
                continue
            text = _teams_auth_scalar_text(payload.get(key))
            if text:
                rows.append((label, text))
                break
    return rows


def _teams_auth_print_payload(title: str, payload: object) -> None:
    normalized = _teams_auth_normalize_value(payload)
    typer.echo(title)
    if normalized is None:
        typer.echo("Completed successfully.")
        return
    if isinstance(normalized, list):
        if not normalized:
            typer.echo("No Teams auth entries found.")
            return
        typer.echo(f"Entries: {len(normalized)}")
        for index, item in enumerate(normalized, start=1):
            item_payload = _teams_auth_normalize_value(item)
            rows = (
                _teams_auth_extract_fields(item_payload) if isinstance(item_payload, dict) else []
            )
            if rows:
                typer.echo(f"- Entry {index}")
                for label, value in rows:
                    typer.echo(f"  {label:<24} {value}")
                continue
            typer.echo(f"- Entry {index}")
            typer.echo(json.dumps(_teams_auth_redact(item_payload), indent=2, sort_keys=True))
        return

    rows = _teams_auth_extract_fields(normalized) if isinstance(normalized, dict) else []
    if rows:
        for label, value in rows:
            typer.echo(f"{label:<24} {value}")
        remaining: dict[str, object] = {}
        if isinstance(normalized, dict):
            known_keys = {
                key for _, keys in _TEAMS_AUTH_SUMMARY_FIELDS for key in keys if key in normalized
            }
            remaining = {key: value for key, value in normalized.items() if key not in known_keys}
        if remaining:
            typer.echo("Details")
            typer.echo(json.dumps(_teams_auth_redact(remaining), indent=2, sort_keys=True))
        return

    typer.echo(json.dumps(_teams_auth_redact(normalized), indent=2, sort_keys=True))


def _teams_auth_service(config: cli_common.ConfigOption) -> AdminRuntimeComponents:
    return cli_runtime.build_admin_runtime(config)


def _teams_auth_call(service: AdminRuntimeComponents, action: str, **kwargs: object) -> object:
    method = getattr(service.teams_admin, action, None)
    if not callable(method):
        raise typer.BadParameter(
            f"Teams auth {action} is unavailable in this build. "
            f"Expected TeamsAdminService.{action}()."
        )
    return method(**kwargs)


def _teams_auth_emit_result(title: str, result: object) -> None:
    if hasattr(result, "ok") and getattr(result, "ok") is False:
        cli_common._echo_admin_result_or_exit(result)
        return
    payload = _teams_auth_result_payload(result)
    _teams_auth_print_payload(title, payload)


@cli_app.teams_auth_app.command("login", short_help="Start delegated Teams auth device-code flow.")
def teams_auth_login(
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
    directory: cli_common.DirectoryOption = "root",
) -> None:
    service = _teams_auth_service(config)
    login_session = _teams_auth_call(service, "login", directory=directory)
    _teams_auth_emit_result("Teams auth login", login_session)
    try:
        result = _teams_auth_call(
            service, "complete_login", directory=directory, login_session=login_session
        )
    except TeamsAuthError as exc:
        typer.echo("", err=True)
        typer.echo(f"Teams auth login failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    typer.echo("")
    _teams_auth_emit_result("Teams auth status", result)


@cli_app.teams_auth_app.command("status", short_help="Show delegated Teams auth status.")
def teams_auth_status(
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
    directory: cli_common.DirectoryOption = "root",
) -> None:
    service = _teams_auth_service(config)
    result = _teams_auth_call(service, "status", directory=directory)
    _teams_auth_emit_result("Teams auth status", result)


@cli_app.teams_auth_app.command("logout", short_help="Clear delegated Teams auth state.")
def teams_auth_logout(
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
    directory: cli_common.DirectoryOption = "root",
) -> None:
    service = _teams_auth_service(config)
    result = _teams_auth_call(service, "logout", directory=directory)
    _teams_auth_emit_result("Teams auth logout", result)


@cli_app.teams_auth_app.command("list", short_help="List delegated Teams auth entries.")
def teams_auth_list(config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR) -> None:
    service = _teams_auth_service(config)
    result = _teams_auth_call(service, "list")
    _teams_auth_emit_result("Teams auth entries", result)


@cli_app.teams_auth_app.command("channels", short_help="Resolve Teams channel IDs within a team.")
def teams_auth_channels(
    team_id: Annotated[str, typer.Option("--team-id", help="Microsoft Teams team ID to query.")],
    channel_name: Annotated[
        str | None,
        typer.Option("--name", help="Exact Teams channel display name to match."),
    ] = None,
    include_all: Annotated[
        bool,
        typer.Option("--all", help="List all channels in the team instead of filtering by name."),
    ] = False,
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
    directory: cli_common.DirectoryOption = "root",
) -> None:
    service = _teams_auth_service(config)
    try:
        result = _teams_auth_call(
            service,
            "list_channels",
            directory=directory,
            team_id=team_id,
            channel_name=channel_name,
            include_all=include_all,
        )
    except (TeamsAuthError, ValueError, httpx.HTTPError) as exc:
        typer.echo(f"Teams channel lookup failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    _teams_auth_emit_result("Teams channels", result)

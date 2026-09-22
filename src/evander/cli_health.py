"""Evander health commands."""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Annotated

import httpx
import typer
from pydantic import ValidationError

from evander.config import GatewayConfig
from evander.repositories.session_manifests import JsonSessionManifestRepository
from evander.runtime_generation import read_runtime_generation_status

from . import cli_app, cli_common, cli_runtime


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
        return UnitStatus(
            unit=unit, state="unavailable", available=False, detail="systemctl not found"
        )

    detail = result.stderr.strip() or None
    if result.returncode == 0:
        return UnitStatus(
            unit=unit, state=result.stdout.strip() or "active", available=True, detail=detail
        )

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


@cli_app.app.command(
    "status",
    rich_help_panel="Health and State",
    short_help="Show host and runtime health.",
)
def status(
    config: cli_common.ConfigOption = cli_common.CLI_DEFAULT_RUNTIME_DIR,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    report: dict[str, object] = {
        "generated_at": datetime.now(cli_common.UTC).isoformat(),
        "overall": "ok",
        "config": {"loaded": False, "path": str(config), "error": None},
        "runtime_paths": [],
        "systemd_units": [],
        "health": [],
        "runtime_generation": {
            "state": "not_registered",
            "coherent": False,
            "components": {},
            "missing_components": [],
            "errors": {},
            "generations": [],
            "current_generation": None,
            "stale_components": [],
        },
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
        cfg = cli_runtime._load_runtime_config_from_option(config)
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
            _path_status(cfg.directories_store_path),
            _path_status(cfg.audit_log_path),
        ]
        report["runtime_paths"] = [asdict(path_status) for path_status in runtime_paths]

        for path_status in runtime_paths:
            if not path_status.exists or not path_status.readable or not path_status.writable:
                report["overall"] = "degraded"

        units = [
            "evander-firewall.service",
            "evander-gateway.service",
            "evander-admin-web.service",
            "evander-trigger-orchestrator.service",
            "evander-effect-orchestrator.service",
            "evander-team-runtime.target",
            "evander-openai-relay.service",
            "evander-http-target-relay.service",
            "evander-http-target-browser-proxy.service",
            "evander-discord-wake.service",
            "evander-scheduled-wake.service",
            "evander-managed-git-refresh.service",
        ]
        unit_statuses = [_get_systemd_unit_state(unit) for unit in units]
        report["systemd_units"] = [asdict(unit_status) for unit_status in unit_statuses]
        for unit_status in unit_statuses:
            if unit_status.available and unit_status.state != "active":
                report["overall"] = "degraded"

        generation_status = read_runtime_generation_status(cfg.runtime_dir)
        report["runtime_generation"] = generation_status
        if generation_status["state"] != "coherent":
            report["overall"] = "degraded"

        vm_defaults = cli_common._resolve_root_vm_defaults(cfg)
        gateway_health_url = vm_defaults.gateway_url
        relay_health_url = f"http://{_normalize_host_for_local_check(cfg.openai_relay_host)}:{cfg.openai_relay_port}/healthz"
        http_target_relay_health_url = f"http://{_normalize_host_for_local_check(cfg.http_target_relay_host)}:{cfg.http_target_relay_port}/healthz"
        http_target_browser_proxy_health_url = f"http://{_normalize_host_for_local_check(cfg.http_target_browser_proxy_host)}:{cfg.http_target_browser_proxy_port}/healthz"
        health_results = [
            _check_health_endpoint("gateway", gateway_health_url),
            _check_health_endpoint("openai_relay", relay_health_url),
            _check_health_endpoint("http_target_relay", http_target_relay_health_url),
            _check_health_endpoint(
                "http_target_browser_proxy", http_target_browser_proxy_health_url
            ),
        ]
        report["health"] = [asdict(result) for result in health_results]
        for endpoint in health_results:
            if not endpoint.reachable:
                report["overall"] = "degraded"

        session_repository = JsonSessionManifestRepository(cfg.session_store_path)
        try:
            sessions = session_repository.list_sessions()
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
            now = datetime.now(cli_common.UTC)
            active = session_repository.list_active_sessions(now=now)
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

    typer.echo("Evander Status")
    typer.echo(_format_rows(rows))

    if report["runtime_paths"]:
        typer.echo("\nRuntime paths")
        for path_status in report["runtime_paths"]:
            status = (
                "ok"
                if path_status["exists"] and path_status["readable"] and path_status["writable"]
                else "degraded"
            )
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
            typer.echo(
                f"- {endpoint['name']}: {status} (url={endpoint['url']} status_code={status_code})"
            )
            if endpoint["error"]:
                typer.echo(f"  error: {endpoint['error']}")

    generation = report["runtime_generation"]
    typer.echo("\nTeam runtime generation")
    typer.echo(f"- state: {generation['state']}")
    for component, registration in sorted(generation["components"].items()):
        typer.echo(
            f"- {component}: {registration['generation']} "
            f"(registered={registration.get('registered_at', 'unknown')})"
        )
    if generation["missing_components"]:
        typer.echo("- missing: " + ", ".join(generation["missing_components"]))
    if generation.get("stale_components"):
        typer.echo("- stale: " + ", ".join(generation["stale_components"]))

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

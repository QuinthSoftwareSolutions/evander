"""CLI option aliases, output helpers, and subprocess compatibility."""

from __future__ import annotations

import inspect
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

import typer

from evander.config import GatewayConfig
from evander.fast_mode import CodexSpeed, FastModeValue
from evander.vm.defaults_config import EffectiveVmDefaultsConfig, VmDefaultsConfigResolver

CLI_DEFAULT_RUNTIME_DIR = Path("runtime")


SpeedOption = Annotated[
    CodexSpeed | None,
    typer.Option("--speed", help="Codex speed: standard, fast, or ultrafast; omit to inherit."),
]


def resolve_speed_option(
    fast_mode: bool | None, speed: CodexSpeed | None
) -> FastModeValue | None:
    if speed is None:
        return fast_mode
    if fast_mode is not None:
        raise typer.BadParameter("Use --speed or --fast-mode/--no-fast-mode, exclusively.")
    return {
        CodexSpeed.STANDARD: False,
        CodexSpeed.FAST: True,
        CodexSpeed.ULTRAFAST: "ultrafast",
    }[speed]


DirectoryOption = Annotated[
    str,
    typer.Option(
        "--directory",
        help="Directory path for delegated Teams auth operations.",
    ),
]


ConfigOption = Annotated[
    Path,
    typer.Option(
        "--runtime-dir",
        "--config",
        help="Runtime directory root.",
    ),
]


SetupProjectDirOption = Annotated[
    Path,
    typer.Option(
        "--project-dir",
        help="Checked-out Evander repository root. Defaults to the current working directory.",
    ),
]


UTC = timezone.utc


def _echo_model_json(model: object) -> None:
    if hasattr(model, "model_dump"):
        typer.echo(json.dumps(model.model_dump(mode="json"), indent=2, sort_keys=True))
        return
    typer.echo(json.dumps(model, indent=2, sort_keys=True))


def _parse_utc_datetime(value: str) -> datetime:
    normalized = value.strip()
    if not normalized:
        raise typer.BadParameter("timestamp is required")
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError as exc:
        raise typer.BadParameter(
            "Invalid timestamp. Use ISO-8601 with timezone, e.g. 2026-04-01T10:30:00Z"
        ) from exc
    if parsed.tzinfo is None:
        raise typer.BadParameter(
            "timestamp must include timezone information (for example, trailing 'Z')."
        )
    return parsed.astimezone(UTC)


def _resolve_root_vm_defaults(cfg: GatewayConfig) -> EffectiveVmDefaultsConfig:
    return VmDefaultsConfigResolver(cfg).resolve_for_directory("root")


def _run_subprocess_compat(
    cmd: list[str],
    *,
    check: bool,
    capture_output: bool | None = None,
    text: bool | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    kwargs: dict[str, object] = {"check": check}
    try:
        parameters = inspect.signature(subprocess.run).parameters
    except (TypeError, ValueError):
        parameters = {}
    if capture_output is not None and "capture_output" in parameters:
        kwargs["capture_output"] = capture_output
    if text is not None and "text" in parameters:
        kwargs["text"] = text
    if env is not None and "env" in parameters:
        kwargs["env"] = env
    return subprocess.run(cmd, **kwargs)


def _echo_admin_result_or_exit(result, *, success_message: str | None = None) -> None:
    if getattr(result, "ok", False):
        if success_message is not None:
            typer.echo(success_message)
        return
    errors = getattr(result, "errors", []) or []
    message = errors[0].message if errors else "operation failed"
    raise typer.BadParameter(message)


def _echo_action_result_or_exit(result) -> None:
    typer.echo(result.message)
    if not result.success:
        raise typer.Exit(code=1)

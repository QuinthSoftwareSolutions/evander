from __future__ import annotations

import importlib
import json
import inspect
import logging
import os
import re
import shutil
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timedelta, timezone
from itertools import count
from pathlib import Path
from typing import Annotated

import httpx
import typer
from pydantic import ValidationError
from typer.core import TyperGroup

from agent_sandbox.admin.service import (
    AdminService,
    resolve_agent_model_for_manifest,
    resolve_effective_issue_setup_config,
)
from agent_sandbox.audit import AuditEventType
from agent_sandbox.daemons.ado_issue_worker import run_worker_command
from agent_sandbox.bootstrap import (
    load_host_bootstrap_config,
    load_runtime_gateway_config,
    resolve_project_dir,
    resolve_runtime_dir,
)
from agent_sandbox.config import GatewayConfig, load_config
from agent_sandbox.provider_config import (
    MODEL_PROVIDER_MODE_METADATA_KEY,
    normalize_model_provider_mode_override,
)
from agent_sandbox.messaging.discord_wake import build_discord_wake_worker
from agent_sandbox.directories import DirectoryService
from agent_sandbox.daemons.effect_orchestrator import build_effect_orchestrator_worker
from agent_sandbox.daemons.proxy_issue_sync import (
    DEFAULT_PROXY_ISSUE_SYNC_FRESHNESS_SECONDS,
    DEFAULT_PROXY_ISSUE_SYNC_INTERVAL_SECONDS,
    run_once_from_runtime as run_proxy_issue_sync_once,
    run_proxy_issue_sync_worker,
)
from agent_sandbox.execution import (
    ContainerWorkerExecutionRepository,
    ContainerWorkerVmExecutionBackend,
    ExecutionBackendKind,
    ExecutionSpec,
    ExecutionStatusKind,
    build_execution_backend,
)
from agent_sandbox.messaging.scheduled_wake import build_scheduled_wake_worker
from agent_sandbox.teams_meeting import TeamsMeetingService
from agent_sandbox.daemons.trigger_orchestrator import build_trigger_orchestrator_worker
from agent_sandbox.migrations import (
    migrate_ado_worker_env_to_root,
    migrate_directory_ownership_defaults,
    migrate_global_config_to_root,
    migrate_heartbeat_config_to_wake,
    migrate_issue_setup_env_to_root,
    migrate_pr_template_to_root,
)
from agent_sandbox.orchestration.migration import migrate_orchestration_store_to_sqlite
from agent_sandbox.repositories.audit_index import (
    ToolCallAuditIndex,
    audit_index_path_for_log_path,
)
from agent_sandbox.email.providers import JsonEmailProvider, MultiMailboxEmailProvider
from agent_sandbox.models import (
    ApprovalStatus,
    DiscordCommandEnvelope,
    EmailMessage,
    SessionManifest,
    ToolName,
)
from agent_sandbox.policy_config import EffectivePolicyConfig
from agent_sandbox.runtime import build_runtime
from agent_sandbox.repositories.session_manifests import JsonSessionManifestRepository
from agent_sandbox.messaging.teams_auth import TeamsAuthError
from agent_sandbox.vm.defaults_config import EffectiveVmDefaultsConfig, VmDefaultsConfigResolver
from agent_sandbox.vm_to_container_migration import (
    copy_vm_session_state,
    create_migration_backup,
    cutover_sessions_to_container,
    default_local_worker_root,
    default_migration_backup_root,
    plan_payload,
    build_session_migration_plan,
    restore_selected_manifests_from_backup,
    select_sessions_for_migration,
    verify_worker_session_state,
)
from agent_sandbox.workflow.runtime_config import load_trigger_orchestrator_worker_global_config
from agent_sandbox.pr_feedback.store import PrFeedbackStore

_COMMAND_PANELS = {
    "Health and State": 0,
    "Sessions and Feedback": 1,
    "Issue Lifecycle": 2,
    "VM Operations": 3,
    "Seeding and Fixtures": 4,
    "Background Workers": 5,
}


class GroupedHelpTyperGroup(TyperGroup):
    def list_commands(self, ctx) -> list[str]:
        def command_sort_key(name: str) -> tuple[int, str]:
            command = self.commands[name]
            panel = getattr(command, "rich_help_panel", None)
            panel_rank = _COMMAND_PANELS.get(panel or "", len(_COMMAND_PANELS))
            return (panel_rank, name.casefold())

        return sorted(self.commands, key=command_sort_key)


app = typer.Typer(
    help="Utilities for the agent sandbox host control plane.", cls=GroupedHelpTyperGroup
)
directories_app = typer.Typer(help="Directory management and directory-scoped config inspection.")
wake_schedules_app = typer.Typer(help="Manage scheduled wake requests.")
teams_auth_app = typer.Typer(help="Manage delegated Teams auth.")
teams_meeting_app = typer.Typer(help="Manage live Teams meeting agent sessions.")
execution_app = typer.Typer(help="Manual execution backend controls.")
app.add_typer(directories_app, name="directories")
app.add_typer(wake_schedules_app, name="wake-schedules")
app.add_typer(teams_auth_app, name="teams-auth", rich_help_panel="Sessions and Feedback")
app.add_typer(teams_meeting_app, name="teams-meeting", rich_help_panel="Sessions and Feedback")
app.add_typer(execution_app, name="execution", rich_help_panel="VM Operations")

CLI_DEFAULT_RUNTIME_DIR = Path("runtime")
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
        help="Runtime directory root. A legacy gateway YAML path is accepted for bootstrap compatibility.",
    ),
]
_discord_seed_sequence = count()
UTC = timezone.utc


def _bootstrap_inputs(config: Path) -> tuple[Path, Path | None]:
    if config.suffix.casefold() in {".yaml", ".yml"}:
        legacy = config
        return resolve_runtime_dir(legacy_config_path=legacy), legacy
    return Path(config).expanduser().resolve(), None


def _workspace_root_for_config_option(config: Path) -> Path:
    return config.expanduser().resolve().parent


def _project_dir_for_config_option(config: Path) -> Path:
    if config.suffix.casefold() in {".yaml", ".yml"}:
        return resolve_project_dir(legacy_config_path=config)
    return config.expanduser().resolve().parent


def _load_runtime_config_from_option(config: Path) -> GatewayConfig:
    runtime_dir, legacy_config_path = _bootstrap_inputs(config)
    return load_runtime_gateway_config(
        runtime_dir=runtime_dir, legacy_config_path=legacy_config_path
    )


def _load_host_config_from_option(config: Path):
    runtime_dir, legacy_config_path = _bootstrap_inputs(config)
    return load_host_bootstrap_config(
        runtime_dir=runtime_dir, legacy_config_path=legacy_config_path
    )


def _build_execution_backend_from_option(config: Path):
    cfg = _load_runtime_config_from_option(config)
    return build_execution_backend(
        cfg,
        admin_service=AdminService(config) if str(cfg.execution_backend) == "libvirt_vm" else None,
        project_dir=_project_dir_for_config_option(config),
    )


def _container_worker_repository(config: GatewayConfig) -> ContainerWorkerExecutionRepository:
    return ContainerWorkerExecutionRepository(
        config.runtime_dir / "container-worker-executions.json"
    )


def _container_worker_target_exists(config: GatewayConfig, target: str) -> bool:
    try:
        _container_worker_repository(config).resolve(target)
    except ValueError as exc:
        return str(exc).startswith("Ambiguous container worker session target")
    return True


def _preflight_container_worker_config(config: GatewayConfig) -> None:
    if not config.container_worker_base_url:
        raise typer.BadParameter("container_worker_base_url is required for container_worker_vm.")
    if not os.environ.get(config.container_worker_token_env, ""):
        raise typer.BadParameter(
            f"container worker bearer token env var {config.container_worker_token_env} is not set."
        )


def _build_execution_backend_for_kind(
    config_path: Path,
    *,
    backend: ExecutionBackendKind | None = None,
    target: str | None = None,
    default_container: bool = False,
    require_container_token: bool = True,
):
    cfg = _load_runtime_config_from_option(config_path)
    if backend is not None:
        selected = backend
    elif default_container:
        selected = ExecutionBackendKind.CONTAINER_WORKER_VM
    elif target and _container_worker_target_exists(cfg, target):
        selected = ExecutionBackendKind.CONTAINER_WORKER_VM
    else:
        selected = ExecutionBackendKind(str(cfg.execution_backend))

    if selected == ExecutionBackendKind.CONTAINER_WORKER_VM:
        if require_container_token:
            _preflight_container_worker_config(cfg)
        elif not cfg.container_worker_base_url:
            raise typer.BadParameter(
                "container_worker_base_url is required for container_worker_vm."
            )
        container_cfg = cfg.model_copy(
            update={"execution_backend": ExecutionBackendKind.CONTAINER_WORKER_VM.value}
        )
        return ContainerWorkerVmExecutionBackend(config=container_cfg)

    libvirt_cfg = cfg.model_copy(
        update={"execution_backend": ExecutionBackendKind.LIBVIRT_VM.value}
    )
    return build_execution_backend(
        libvirt_cfg,
        admin_service=AdminService(config_path),
        project_dir=_project_dir_for_config_option(config_path),
    )


def _echo_model_json(model: object) -> None:
    if hasattr(model, "model_dump"):
        typer.echo(json.dumps(model.model_dump(mode="json"), indent=2, sort_keys=True))
        return
    typer.echo(json.dumps(model, indent=2, sort_keys=True))


def _build_teams_ingest_worker(cfg: GatewayConfig, *, project_dir: Path):
    try:
        module = importlib.import_module("agent_sandbox.messaging.teams_ingest")
    except ModuleNotFoundError as exc:
        if exc.name == "agent_sandbox.messaging.teams_ingest":
            raise RuntimeError(
                "Teams ingest worker module agent_sandbox.messaging.teams_ingest is not available."
            ) from exc
        raise
    builder = getattr(module, "build_teams_ingest_worker", None)
    if builder is None:
        raise RuntimeError(
            "Teams ingest worker module does not export build_teams_ingest_worker(config, *, project_dir=...)."
        )
    return builder(cfg, project_dir=project_dir)


def _build_teams_meeting_ingest_worker(cfg: GatewayConfig, *, project_dir: Path):
    try:
        module = importlib.import_module("agent_sandbox.messaging.teams_meeting_ingest")
    except ModuleNotFoundError as exc:
        if exc.name == "agent_sandbox.messaging.teams_meeting_ingest":
            raise RuntimeError(
                "Teams meeting ingest worker module agent_sandbox.messaging.teams_meeting_ingest is not available."
            ) from exc
        raise
    builder = getattr(module, "build_teams_meeting_ingest_worker", None)
    if builder is None:
        raise RuntimeError(
            "Teams meeting ingest worker module does not export build_teams_meeting_ingest_worker(config, *, project_dir=...)."
        )
    return builder(cfg, project_dir=project_dir)


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


BackendOption = Annotated[
    ExecutionBackendKind | None,
    typer.Option(
        "--backend", help="Force an execution backend instead of auto-detecting container mappings."
    ),
]


def _parse_metadata_pairs(pairs: list[str] | None) -> dict[str, str]:
    metadata: dict[str, str] = {}
    forbidden = {"env", "environment", "environment_variables"}
    for raw in pairs or []:
        if "=" not in raw:
            raise typer.BadParameter("--metadata values must use key=value format.")
        key, value = raw.split("=", 1)
        normalized_key = key.strip()
        if not normalized_key:
            raise typer.BadParameter("--metadata keys must not be empty.")
        if normalized_key.casefold() in forbidden:
            raise typer.BadParameter(f"--metadata key {normalized_key} is not allowed.")
        metadata[normalized_key] = value
    return metadata


def _resource_limits(
    memory: str | None, cpus: float | None, pids_limit: int | None
) -> dict[str, object] | None:
    limits: dict[str, object] = {}
    if memory:
        limits["memory"] = memory
    if cpus is not None:
        limits["cpus"] = cpus
    if pids_limit is not None:
        limits["pids_limit"] = pids_limit
    return limits or None


def _container_mapping_payload(record) -> dict[str, object]:
    return {
        "execution_id": record.execution_id,
        "session_id": record.session_id,
        "worker_job_id": record.worker_job_id,
        "target_name": record.target_name,
        "worker_url": record.worker_url,
        "last_status": record.last_status,
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
        "started_at": record.started_at.isoformat() if record.started_at else None,
        "finished_at": record.finished_at.isoformat() if record.finished_at else None,
    }


@execution_app.command("start-container-job")
def execution_start_container_job(
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
    session_id: Annotated[
        str | None, typer.Option("--session-id", help="Session id for worker persistence.")
    ] = None,
    command: Annotated[
        str, typer.Option("--command", help="Command to run inside the worker container.")
    ] = "",
    target_name: Annotated[
        str | None, typer.Option("--target-name", help="Optional stable execution id/target name.")
    ] = None,
    directory_path: Annotated[
        str,
        typer.Option(
            "--directory-path", "--directory", help="Directory path owning the execution."
        ),
    ] = "root",
    image: Annotated[str | None, typer.Option("--image", help="Container image override.")] = None,
    repo_source: Annotated[
        str | None, typer.Option("--repo-source", help="Explicit repository source to clone.")
    ] = None,
    clone_branch: Annotated[
        str | None, typer.Option("--clone-branch", help="Explicit repository branch to clone.")
    ] = None,
    metadata: Annotated[
        list[str] | None,
        typer.Option("--metadata", help="Sanitized metadata as key=value. May be repeated."),
    ] = None,
    memory: Annotated[
        str | None, typer.Option("--memory", help="Container memory limit, e.g. 512m.")
    ] = None,
    cpus: Annotated[
        float | None, typer.Option("--cpus", min=0.01, help="Container CPU limit.")
    ] = None,
    pids_limit: Annotated[
        int | None, typer.Option("--pids-limit", min=1, help="Container PID limit.")
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON output.")] = False,
) -> None:
    if not command.strip():
        raise typer.BadParameter("--command is required")
    backend = _build_execution_backend_for_kind(config, default_container=True)
    prepared = backend.prepare(
        ExecutionSpec(
            backend=ExecutionBackendKind.CONTAINER_WORKER_VM,
            target_name=target_name,
            session_id=session_id,
            directory_path=directory_path,
            command=command,
            image=image,
            repo_source=repo_source,
            clone_branch=clone_branch,
            resource_limits=_resource_limits(memory, cpus, pids_limit),
            metadata=_parse_metadata_pairs(metadata),
        )
    )
    handle = backend.start(prepared)
    if json_output:
        _echo_model_json(handle)
        return
    typer.echo(
        f"Started container worker execution {handle.execution_id} "
        f"for session {handle.session_id or '<none>'}."
    )


@execution_app.command("status")
def execution_status(
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
    target: str = typer.Argument(...),
    backend: BackendOption = None,
) -> None:
    backend = _build_execution_backend_for_kind(config, backend=backend, target=target)
    _echo_model_json(backend.inspect(target))


@execution_app.command("logs")
def execution_logs(
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
    target: str = typer.Argument(...),
    lines: Annotated[
        int, typer.Option("--lines", min=1, help="Number of trailing lines to print.")
    ] = 50,
    backend: BackendOption = None,
) -> None:
    backend = _build_execution_backend_for_kind(config, backend=backend, target=target)
    typer.echo(backend.get_log_snapshot(target, lines=lines), nl=False)


@execution_app.command("stop")
def execution_stop(
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
    target: str = typer.Argument(...),
    timeout_seconds: Annotated[int, typer.Option("--timeout-seconds", min=1)] = 60,
    backend: BackendOption = None,
) -> None:
    backend = _build_execution_backend_for_kind(config, backend=backend, target=target)
    _echo_model_json(backend.stop(target, timeout_seconds=timeout_seconds))


@execution_app.command("destroy")
def execution_destroy(
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
    target: str = typer.Argument(...),
    backend: BackendOption = None,
) -> None:
    backend = _build_execution_backend_for_kind(config, backend=backend, target=target)
    _echo_model_json(backend.destroy(target))


@execution_app.command("list-container-worker-executions")
def execution_list_container_worker_executions(
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
    session_id: Annotated[
        str | None, typer.Option("--session-id", help="Filter by session id.")
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON output.")] = False,
) -> None:
    cfg = _load_runtime_config_from_option(config)
    repository = _container_worker_repository(cfg)
    if session_id:
        list_by_session = getattr(repository, "list_by_session", None)
        if callable(list_by_session):
            records = list_by_session(session_id)
        else:
            list_all_ordered = getattr(repository, "list_all_ordered", None)
            if callable(list_all_ordered):
                records = [
                    record
                    for record in list_all_ordered()
                    if record.session_id == session_id
                ]
            else:
                records = [
                    record
                    for record in sorted(
                        repository.read_all().values(),
                        key=lambda record: (record.created_at, record.execution_id),
                    )
                    if record.session_id == session_id
                ]
    else:
        list_all_ordered = getattr(repository, "list_all_ordered", None)
        if callable(list_all_ordered):
            records = list_all_ordered()
        else:
            records = sorted(
                repository.read_all().values(),
                key=lambda record: (record.created_at, record.execution_id),
            )
    payload = [_container_mapping_payload(record) for record in records]
    if json_output:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    if not payload:
        typer.echo("No container worker executions found.")
        return
    rows = [
        (
            item["execution_id"],
            item["session_id"],
            item["worker_job_id"],
            item["target_name"],
            item["last_status"],
            item["worker_url"],
        )
        for item in payload
    ]
    header = ("EXECUTION", "SESSION", "WORKER_JOB", "TARGET", "STATUS", "WORKER_URL")
    widths = [max(len(str(row[index])) for row in [header, *rows]) for index in range(6)]
    typer.echo("  ".join(value.ljust(widths[index]) for index, value in enumerate(header)))
    for row in rows:
        typer.echo("  ".join(str(value).ljust(widths[index]) for index, value in enumerate(row)))


def _poll_container_execution(
    backend, execution_id: str, *, timeout_seconds: float, poll_interval_seconds: float
):
    deadline = time.monotonic() + timeout_seconds
    status = backend.inspect(execution_id)
    terminal = {
        ExecutionStatusKind.STOPPED,
        ExecutionStatusKind.FAILED,
        ExecutionStatusKind.DESTROYED,
    }
    while status.status not in terminal and time.monotonic() < deadline:
        time.sleep(poll_interval_seconds)
        status = backend.inspect(execution_id)
    return status


@execution_app.command("smoke-container-worker")
def execution_smoke_container_worker(
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
    session_id: Annotated[
        str, typer.Option("--session-id", help="Session id to reuse across both smoke jobs.")
    ] = "smoke-session",
    image: Annotated[
        str, typer.Option("--image", help="Container image to run.")
    ] = "agent-sandbox-runner:latest",
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON output.")] = False,
    timeout_seconds: Annotated[
        int, typer.Option("--timeout-seconds", min=1, help="Per-job timeout.")
    ] = 120,
    poll_interval_seconds: Annotated[
        float,
        typer.Option(
            "--poll-interval-seconds",
            min=0.1,
            help="Polling interval while waiting for smoke jobs.",
        ),
    ] = 2.0,
) -> None:
    backend = _build_execution_backend_for_kind(config, default_container=True)
    run_id = uuid.uuid4().hex[:12]
    agent_marker = f"agent-home-{run_id}"
    repo_root_marker = f"repo-root-{run_id}"
    run1_target = f"phase4-smoke-{run_id}-1"
    run2_target = f"phase4-smoke-{run_id}-2"
    run1_command = (
        "set -euo pipefail; "
        f"printf '%s\\n' {agent_marker!r} > /home/agent/phase4-agent-home-marker.txt; "
        "mkdir -p /home/agent/repos; "
        f"printf '%s\\n' {repo_root_marker!r} > /home/agent/repos/phase4-repo-root-marker.txt; "
        "cat /home/agent/phase4-agent-home-marker.txt; "
        "cat /home/agent/repos/phase4-repo-root-marker.txt"
    )
    run2_command = (
        "set -euo pipefail; "
        "agent_marker=$(cat /home/agent/phase4-agent-home-marker.txt); "
        "repo_root_marker=$(cat /home/agent/repos/phase4-repo-root-marker.txt); "
        f'test "$agent_marker" = {agent_marker!r}; '
        f'test "$repo_root_marker" = {repo_root_marker!r}; '
        'printf \'agent_marker=%s\\nrepo_root_marker=%s\\n\' "$agent_marker" "$repo_root_marker"'
    )

    run1 = backend.start(
        backend.prepare(
            ExecutionSpec(
                backend=ExecutionBackendKind.CONTAINER_WORKER_VM,
                target_name=run1_target,
                session_id=session_id,
                command=run1_command,
                image=image,
                metadata={"smoke": "phase4", "run": "1"},
            )
        )
    )
    run1_status = _poll_container_execution(
        backend,
        run1.execution_id,
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )
    _run1_logs = backend.get_log_snapshot(run1.execution_id, lines=200)
    if run1_status.status != ExecutionStatusKind.STOPPED:
        raise typer.BadParameter(
            f"smoke run 1 did not complete successfully: {run1_status.status.value}"
        )

    run2 = backend.start(
        backend.prepare(
            ExecutionSpec(
                backend=ExecutionBackendKind.CONTAINER_WORKER_VM,
                target_name=run2_target,
                session_id=session_id,
                command=run2_command,
                image=image,
                metadata={"smoke": "phase4", "run": "2"},
            )
        )
    )
    run2_status = _poll_container_execution(
        backend,
        run2.execution_id,
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )
    run2_logs = backend.get_log_snapshot(run2.execution_id, lines=200)
    if run2_status.status != ExecutionStatusKind.STOPPED:
        raise typer.BadParameter(
            f"smoke run 2 did not complete successfully: {run2_status.status.value}"
        )
    if agent_marker not in run2_logs or repo_root_marker not in run2_logs:
        raise typer.BadParameter("smoke run 2 logs did not prove both marker reads.")

    payload = {
        "success": True,
        "session_id": session_id,
        "run1": run1_status.model_dump(mode="json"),
        "run2": run2_status.model_dump(mode="json"),
        "run2_logs": run2_logs,
    }
    if json_output:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    typer.echo(f"Container worker smoke succeeded for session {session_id}.")
    typer.echo(run2_logs, nl=False)


def _teams_auth_service(config: ConfigOption) -> AdminService:
    return AdminService(config)


def _teams_auth_call(service: AdminService, action: str, **kwargs: object) -> object:
    method = getattr(service, f"teams_auth_{action}", None)
    if not callable(method):
        raise typer.BadParameter(
            f"Teams auth {action} is unavailable in this build. "
            f"Expected AdminService.teams_auth_{action}()."
        )
    return method(**kwargs)


def _teams_auth_emit_result(title: str, result: object) -> None:
    if hasattr(result, "ok") and getattr(result, "ok") is False:
        _echo_admin_result_or_exit(result)
        return
    payload = _teams_auth_result_payload(result)
    _teams_auth_print_payload(title, payload)


def _default_issue_session_id(issue_number: str) -> str:
    return f"issue-{issue_number}-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{os.getpid()}"


def _issue_session_id_prefix(issue_number: str) -> str:
    return f"issue-{issue_number}-"


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


def _extract_issue_number(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip()
    if normalized.isdigit():
        return normalized
    if normalized.startswith("vm-") and normalized[3:].isdigit():
        return normalized[3:]
    match = re.match(r"^issue-([0-9]+)(?:-|$)", normalized)
    if match:
        return match.group(1)
    return None


def _load_optional_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        values[key] = value.strip().strip('"').strip("'")
    return values


def _resolve_git_root(root_dir: Path) -> Path:
    env_value = os.environ.get("AGENT_SANDBOX_GIT_ROOT", "").strip()
    if env_value:
        return Path(env_value)
    return root_dir / "git-remotes"


def _matching_issue_session_ids(
    issue_number: str, sessions: dict[str, SessionManifest]
) -> set[str]:
    vm_name = f"vm-{issue_number}"
    prefix = _issue_session_id_prefix(issue_number)
    matches: set[str] = set()
    for session_id, session in sessions.items():
        if session.vm_id == vm_name or session_id == issue_number or session_id.startswith(prefix):
            matches.add(session_id)
    return matches


def _matching_vm_session_ids(vm_name: str, sessions: dict[str, SessionManifest]) -> set[str]:
    return {session_id for session_id, session in sessions.items() if session.vm_id == vm_name}


def _parse_session_extension(value: str | None) -> timedelta:
    raw_value = (value or "1d").strip().lower()
    match = re.fullmatch(r"(\d+)([hd])", raw_value)
    if match is None:
        raise typer.BadParameter(
            "Extension must use the format <number><h|d>, for example 8h or 2d."
        )

    amount = int(match.group(1))
    unit = match.group(2)
    if unit == "h":
        return timedelta(hours=amount)
    return timedelta(days=amount)


def _default_session_ttl(policy: EffectivePolicyConfig) -> timedelta:
    return timedelta(seconds=policy.default_session_ttl_seconds)


def _resolve_session_ttl(policy: EffectivePolicyConfig, ttl_hours: int | None) -> timedelta:
    if ttl_hours is not None:
        return timedelta(hours=ttl_hours)
    return _default_session_ttl(policy)


def _resolve_session_target(
    target: str, sessions: dict[str, SessionManifest]
) -> tuple[str, SessionManifest]:
    normalized = target.strip()
    if not normalized:
        raise typer.BadParameter("Target must be a non-empty issue number, vm name, or session id.")

    direct_match = sessions.get(normalized)
    if direct_match is not None:
        return normalized, direct_match

    matching_ids: set[str] = set()
    issue_number = _extract_issue_number(normalized)
    if issue_number is not None:
        matching_ids.update(_matching_issue_session_ids(issue_number, sessions))
    matching_ids.update(_matching_vm_session_ids(normalized, sessions))

    if not matching_ids:
        raise typer.BadParameter(f"Unknown session target: {target}")

    resolved_session_id = max(
        matching_ids,
        key=lambda session_id: _session_sort_key((session_id, sessions[session_id])),
    )
    return resolved_session_id, sessions[resolved_session_id]


def _runtime_session_repository(runtime: object) -> JsonSessionManifestRepository:
    repository = getattr(runtime, "session_repository", None)
    if isinstance(repository, JsonSessionManifestRepository):
        return repository
    config = getattr(runtime, "config", None)
    path = getattr(config, "session_store_path", None)
    if path is None:
        raise RuntimeError("runtime session repository is unavailable")
    return JsonSessionManifestRepository(path)


def _runtime_sessions_by_id(runtime: object) -> dict[str, SessionManifest]:
    repository = _runtime_session_repository(runtime)
    return {session.session_id: session for session in repository.list_sessions()}


def _session_sort_key(item: tuple[str, SessionManifest]) -> tuple[datetime, datetime, str]:
    session_id, manifest = item
    return (manifest.created_at, manifest.expires_at, session_id)


def _session_streams_for_session_ids(
    sessions: dict[str, SessionManifest],
    *,
    session_ids: set[str],
) -> set[tuple[str, str, str]]:
    streams: set[tuple[str, str, str]] = set()
    for session_id in session_ids:
        session = sessions.get(session_id)
        if session is None:
            continue
        raw_streams = session.metadata.get("pr_feedback_streams")
        if not isinstance(raw_streams, list):
            continue
        for raw_stream in raw_streams:
            if not isinstance(raw_stream, dict):
                continue
            provider = str(raw_stream.get("provider", "")).strip()
            repo = str(raw_stream.get("repo", "")).strip()
            pr_id = str(raw_stream.get("pr_id", "")).strip()
            if provider and repo and pr_id:
                streams.add((provider, repo, pr_id))
    return streams


def _prune_pr_map(path: Path, *, session_ids: set[str]) -> tuple[int, set[tuple[str, str, str]]]:
    if not path.exists():
        return 0, set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0, set()
    entries = payload.get("entries")
    if not isinstance(entries, dict):
        return 0, set()

    removed_streams: set[tuple[str, str, str]] = set()
    kept_entries: dict[str, object] = {}
    removed_count = 0
    for entry_key, raw_entry in entries.items():
        if not isinstance(raw_entry, dict):
            kept_entries[entry_key] = raw_entry
            continue
        session_id = str(raw_entry.get("session_id", "")).strip()
        if session_id in session_ids:
            provider = str(raw_entry.get("provider", "")).strip()
            repo = str(raw_entry.get("repo", "")).strip()
            pr_id = str(raw_entry.get("pr_id", "")).strip()
            if provider and repo and pr_id:
                removed_streams.add((provider, repo, pr_id))
            removed_count += 1
            continue
        kept_entries[entry_key] = raw_entry

    if removed_count:
        payload["entries"] = kept_entries
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return removed_count, removed_streams


def _prune_pr_map_for_issue_numbers(
    path: Path,
    *,
    issue_numbers: set[str],
) -> tuple[int, set[tuple[str, str, str]]]:
    if not path.exists() or not issue_numbers:
        return 0, set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0, set()
    entries = payload.get("entries")
    if not isinstance(entries, dict):
        return 0, set()

    removed_streams: set[tuple[str, str, str]] = set()
    kept_entries: dict[str, object] = {}
    removed_count = 0
    for entry_key, raw_entry in entries.items():
        if not isinstance(raw_entry, dict):
            kept_entries[entry_key] = raw_entry
            continue
        session_issue_number = _extract_issue_number(str(raw_entry.get("session_id", "")).strip())
        if session_issue_number in issue_numbers:
            provider = str(raw_entry.get("provider", "")).strip()
            repo = str(raw_entry.get("repo", "")).strip()
            pr_id = str(raw_entry.get("pr_id", "")).strip()
            if provider and repo and pr_id:
                removed_streams.add((provider, repo, pr_id))
            removed_count += 1
            continue
        kept_entries[entry_key] = raw_entry

    if removed_count:
        payload["entries"] = kept_entries
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return removed_count, removed_streams


def _prune_pr_feedback_store(path: Path, *, streams: set[tuple[str, str, str]]) -> tuple[int, int]:
    if not streams:
        return 0, 0
    if not path.exists():
        return 0, 0
    return PrFeedbackStore(path).prune_streams(streams)


def _prune_approvals_for_sessions(runtime: object, *, session_ids: set[str]) -> int:
    approvals_store = getattr(runtime, "approvals", None)
    if approvals_store is None or not session_ids:
        return 0
    approvals = approvals_store.read_all()
    removed_count = 0
    remaining = {}
    for request_id, approval in approvals.items():
        if approval.session_id in session_ids:
            removed_count += 1
            continue
        remaining[request_id] = approval
    if removed_count:
        approvals_store.write_all(remaining)
    return removed_count


def _remove_issue_bare_repo(
    issue_number: str, *, root_dir: Path, runtime_dir: Path
) -> tuple[bool, str | None]:
    git_root = _resolve_git_root(root_dir)
    bare_repo_path = git_root / f"{issue_number}_repo.git"
    hook_log_paths = [
        root_dir / "runtime" / "hook-logs" / f"{bare_repo_path.name}.post-receive.log",
        runtime_dir / "hook-logs" / f"{bare_repo_path.name}.post-receive.log",
    ]
    trusted_repo_path: str | None = None
    if bare_repo_path.exists():
        try:
            config_result = subprocess.run(
                [
                    "git",
                    f"--git-dir={bare_repo_path}",
                    "config",
                    "--get",
                    "agent-sandbox.trustedRepoPath",
                ],
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError:
            config_result = None
        if config_result is not None and config_result.returncode == 0:
            trusted_repo_path = (config_result.stdout or "").strip() or None
        shutil.rmtree(bare_repo_path, ignore_errors=True)
    for hook_log_path in hook_log_paths:
        if hook_log_path.exists():
            hook_log_path.unlink()
    if trusted_repo_path:
        mirror_remote_name = f"{issue_number}-mirror"
        try:
            subprocess.run(
                ["git", "-C", trusted_repo_path, "remote", "remove", mirror_remote_name],
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError:
            pass
    return bare_repo_path.exists() is False, str(
        bare_repo_path
    ) if bare_repo_path.exists() else str(bare_repo_path)


def _list_existing_issue_vm_names() -> set[str]:
    completed = subprocess.run(
        ["virsh", "list", "--all", "--name"],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise typer.Exit(code=completed.returncode or 1)
    return {line.strip() for line in (completed.stdout or "").splitlines() if line.strip()}


def _issue_numbers_from_pr_map(path: Path) -> set[str]:
    if not path.exists():
        return set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    entries = payload.get("entries")
    if not isinstance(entries, dict):
        return set()
    issue_numbers: set[str] = set()
    for raw_entry in entries.values():
        if not isinstance(raw_entry, dict):
            continue
        issue_number = _extract_issue_number(str(raw_entry.get("session_id", "")).strip())
        if issue_number is not None:
            issue_numbers.add(issue_number)
    return issue_numbers


def _issue_numbers_from_bare_repos(root_dir: Path) -> set[str]:
    git_root = _resolve_git_root(root_dir)
    if not git_root.exists():
        return set()
    issue_numbers: set[str] = set()
    for bare_repo in git_root.glob("*_repo.git"):
        issue_number = _extract_issue_number(bare_repo.name.removesuffix("_repo.git"))
        if issue_number is not None:
            issue_numbers.add(issue_number)
    return issue_numbers


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
    policy: EffectivePolicyConfig,
    session_id: str,
    vm_id: str,
    directory_path: str,
    ttl_hours: int | None,
    discord_channels: list[str],
    email_mailboxes: list[str],
    email_folders: list[str],
) -> SessionManifest:
    allowed_tools: list[ToolName] = [
        ToolName.APPROVAL_SUBMIT,
        ToolName.APPROVAL_CHECK,
        ToolName.STAGE_ARTIFACT,
    ]

    if discord_channels:
        allowed_tools.extend(
            [
                ToolName.DISCORD_GET_COMMANDS,
                ToolName.DISCORD_POST_STATUS,
                ToolName.ADD_MESSAGE_REACTION,
                ToolName.HAS_PENDING_DISCORD_MESSAGES,
                ToolName.LIST_UNREAD_DISCORD_MESSAGES,
                ToolName.LIST_ALL_DISCORD_MESSAGES,
            ]
        )

    email_requested = bool(email_mailboxes or email_folders)
    resolved_email_mailboxes = email_mailboxes or (
        policy.email_mailbox_allowlist if email_requested else []
    )
    resolved_email_folders = email_folders or (
        sorted(
            {
                folder
                for folders in policy.email_folder_allowlist_by_mailbox.values()
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
        if policy.email_send_enabled:
            allowed_tools.extend([ToolName.EMAIL_SEND, ToolName.EMAIL_SEND_APPROVED])

    metadata: dict[str, object] = {}
    if str(os.environ.get("AGENT_PROVIDER", "")).strip().lower() == "copilot":
        allowed_tools.append(ToolName.COPILOT_HOST_AUTH)
        metadata["agent_provider"] = "copilot"

    unique_tools = list(dict.fromkeys(allowed_tools))
    return SessionManifest(
        session_id=session_id,
        vm_id=vm_id,
        directory_path=directory_path,
        allowed_tools=unique_tools,
        approval_required_tools=list(policy.approval_required_tools),
        discord_channel_allowlist=discord_channels,
        email_folder_allowlist=resolved_email_folders,
        email_mailbox_allowlist=resolved_email_mailboxes,
        email_folder_allowlist_by_mailbox=policy.email_folder_allowlist_by_mailbox,
        email_draft_recipient_allowlist=policy.email_draft_recipient_allowlist,
        email_draft_domain_allowlist=policy.email_draft_domain_allowlist,
        email_send_enabled=policy.email_send_enabled,
        expires_at=datetime.now(UTC) + _resolve_session_ttl(policy, ttl_hours),
        metadata=metadata,
    )


@app.command(
    "init-state",
    rich_help_panel="Health and State",
    short_help="Create runtime state files.",
)
def init_state(config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR) -> None:
    host_cfg = _load_host_config_from_option(config)
    runtime = build_runtime(load_runtime_gateway_config(runtime_dir=host_cfg.runtime_dir))
    runtime.directories.ensure_root()
    typer.echo(f"Initialized runtime state under {host_cfg.runtime_dir}")


@directories_app.command("list", short_help="List directory children.")
def directories_list(
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
    parent: Annotated[str, typer.Option("--parent", help="Parent directory path or id.")] = "root",
) -> None:
    service = AdminService(config)
    result = service.directory_admin.list(parent)
    _echo_admin_result_or_exit(result)
    payload = result.data
    assert payload is not None
    typer.echo(json.dumps(payload.model_dump(mode="json"), indent=2, sort_keys=True))


@directories_app.command("show", short_help="Show one directory and its ancestry.")
def directories_show(
    reference: str,
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
) -> None:
    service = AdminService(config)
    result = service.operations_admin.get_directory(reference)
    _echo_admin_result_or_exit(result)
    payload = result.data
    assert payload is not None
    typer.echo(json.dumps(payload.model_dump(mode="json"), indent=2, sort_keys=True))


@directories_app.command("create", short_help="Create a child directory.")
def directories_create(
    name: str,
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
    parent: Annotated[str, typer.Option("--parent", help="Parent directory path or id.")] = "root",
) -> None:
    service = AdminService(config)
    result = service.operations_admin.create_directory_child(parent, name)
    _echo_admin_result_or_exit(result)
    assert result.data is not None
    typer.echo(result.data.details.directory.path)


@directories_app.command("rename", short_help="Rename a directory.")
def directories_rename(
    reference: str,
    new_name: str,
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
) -> None:
    service = AdminService(config)
    result = service.directory_admin.update(reference, new_name=new_name)
    _echo_admin_result_or_exit(result)
    assert result.data is not None
    typer.echo(result.data.details.directory.path)


@directories_app.command("move", short_help="Move a directory to a new parent.")
def directories_move(
    reference: str,
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
    new_parent: Annotated[
        str, typer.Option("--new-parent", help="New parent directory path or id.")
    ] = "root",
) -> None:
    service = AdminService(config)
    result = service.directory_admin.update(reference, new_parent=new_parent)
    _echo_admin_result_or_exit(result)
    assert result.data is not None
    typer.echo(result.data.details.directory.path)


@directories_app.command("delete", short_help="Delete a directory.")
def directories_delete(
    reference: str,
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
    recursive: Annotated[
        bool, typer.Option("--recursive", help="Delete non-empty directories recursively.")
    ] = False,
) -> None:
    service = AdminService(config)
    result = service.directory_admin.delete(reference, recursive=recursive)
    _echo_admin_result_or_exit(result)
    assert result.data is not None
    typer.echo(json.dumps(result.data.model_dump(mode="json"), indent=2, sort_keys=True))


@directories_app.command(
    "config-show", short_help="Show raw and effective config for one directory domain."
)
def directories_config_show(
    reference: str,
    domain: Annotated[str, typer.Option("--domain", help="Directory config domain name.")],
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
) -> None:
    service = AdminService(config)
    raw_result = service.operations_admin.read_directory_domain_config(reference, domain)
    effective_result = service.operations_admin.compute_directory_effective_config(
        reference, domain
    )
    _echo_admin_result_or_exit(raw_result)
    _echo_admin_result_or_exit(effective_result)
    assert raw_result.data is not None
    assert effective_result.data is not None
    typer.echo(
        json.dumps(
            {
                "raw": raw_result.data.model_dump(mode="json"),
                "effective": effective_result.data.model_dump(mode="json"),
                "validation_diagnostics": [
                    item.model_dump(mode="json") for item in effective_result.validation_diagnostics
                ],
            },
            indent=2,
            sort_keys=True,
        )
    )


@directories_app.command("config-set", short_help="Write raw config for one directory domain.")
def directories_config_set(
    reference: str,
    payload: str,
    domain: Annotated[str, typer.Option("--domain", help="Directory config domain name.")],
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
) -> None:
    try:
        parsed = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise typer.BadParameter(f"invalid JSON payload: {exc}") from exc
    if not isinstance(parsed, dict):
        raise typer.BadParameter("config payload must be a JSON object")
    service = AdminService(config)
    result = service.operations_admin.write_directory_domain_config(reference, domain, parsed)
    _echo_admin_result_or_exit(result)
    assert result.data is not None
    typer.echo(json.dumps(result.data.model_dump(mode="json"), indent=2, sort_keys=True))


@app.command(
    "migrate-directory-ownership",
    rich_help_panel="Health and State",
    short_help="Backfill missing directory ownership fields to root.",
)
def migrate_directory_ownership(
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
) -> None:
    cfg = _load_runtime_config_from_option(config)
    summary = migrate_directory_ownership_defaults(cfg)
    typer.echo(
        f"Backfilled {summary.changed_records} record(s) across {summary.changed_files} file(s)"
    )


@app.command(
    "migrate-global-config-to-root",
    rich_help_panel="Health and State",
    short_help="Materialize current global config as explicit root directory config.",
)
def migrate_global_config_root(
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
    overwrite: Annotated[
        bool,
        typer.Option(
            "--overwrite",
            help="Replace existing root domain files when they differ from the current global config.",
        ),
    ] = False,
) -> None:
    runtime_dir, legacy_config_path = _bootstrap_inputs(config)
    if legacy_config_path is not None and legacy_config_path.exists():
        cfg = load_config(legacy_config_path)
    else:
        cfg = _load_runtime_config_from_option(runtime_dir)
    summary = migrate_global_config_to_root(cfg, overwrite=overwrite)
    typer.echo(
        "Root config migration complete: "
        f"written={len(summary.written_domains)} "
        f"skipped={len(summary.skipped_domains)} "
        f"unchanged={len(summary.unchanged_domains)}"
    )
    if summary.written_domains:
        typer.echo(f"written: {', '.join(summary.written_domains)}")
    if summary.skipped_domains:
        typer.echo(f"skipped: {', '.join(summary.skipped_domains)}")
    if summary.unchanged_domains:
        typer.echo(f"unchanged: {', '.join(summary.unchanged_domains)}")


@app.command(
    "migrate-heartbeat-config-to-wake",
    rich_help_panel="Health and State",
    short_help="Rename legacy heartbeat directory config files to wake config files.",
)
def migrate_heartbeat_config_wake(
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
    overwrite: Annotated[
        bool,
        typer.Option(
            "--overwrite",
            help="Replace an existing wake.json when it differs from the migrated heartbeat.json.",
        ),
    ] = False,
) -> None:
    cfg = _load_runtime_config_from_option(config)
    summary = migrate_heartbeat_config_to_wake(cfg, overwrite=overwrite)
    typer.echo(
        "Wake config migration complete: "
        f"scanned={summary.scanned_files} "
        f"written={summary.written_files} "
        f"skipped={summary.skipped_files} "
        f"removed_legacy={summary.removed_legacy_files} "
        f"metadata_updated={summary.metadata_updated}"
    )


@app.command(
    "migrate-ado-worker-env-to-root",
    rich_help_panel="Health and State",
    short_help="Import ado-worker env settings into root ado_worker config.",
)
def migrate_ado_worker_env_root(
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
    env_file: Annotated[
        Path | None,
        typer.Option(
            "--env-file",
            help="Path to the ado-worker env file to import. Current process env vars override file values.",
        ),
    ] = None,
    overwrite: Annotated[
        bool,
        typer.Option(
            "--overwrite/--no-overwrite",
            help="Replace existing root ado_worker config when it differs.",
        ),
    ] = True,
) -> None:
    runtime_dir, legacy_config_path = _bootstrap_inputs(config)
    if legacy_config_path is not None and legacy_config_path.exists():
        cfg = load_config(legacy_config_path)
    else:
        cfg = _load_runtime_config_from_option(runtime_dir)
    resolved_env_file = (
        env_file.expanduser()
        if env_file is not None
        else _workspace_root_for_config_option(config) / "config" / "ado-worker.env"
    )
    result = migrate_ado_worker_env_to_root(cfg, env_file=resolved_env_file, overwrite=overwrite)
    typer.echo(
        f"ADO worker env migration complete: {result.status} {result.domain} -> {result.path}"
    )


@app.command(
    "migrate-issue-setup-env-to-root",
    rich_help_panel="Health and State",
    short_help="Import issue-setup env settings into root issue_setup config.",
)
def migrate_issue_setup_env_root(
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
    env_file: Annotated[
        Path | None,
        typer.Option(
            "--env-file",
            help="Path to an env file to import. Current process env vars override file values.",
        ),
    ] = None,
    overwrite: Annotated[
        bool,
        typer.Option(
            "--overwrite/--no-overwrite",
            help="Replace existing root issue_setup config when it differs.",
        ),
    ] = True,
) -> None:
    runtime_dir, legacy_config_path = _bootstrap_inputs(config)
    if legacy_config_path is not None and legacy_config_path.exists():
        cfg = load_config(legacy_config_path)
    else:
        cfg = _load_runtime_config_from_option(runtime_dir)
    resolved_env_file = env_file.expanduser() if env_file is not None else None
    result = migrate_issue_setup_env_to_root(cfg, env_file=resolved_env_file, overwrite=overwrite)
    typer.echo(
        f"Issue setup env migration complete: {result.status} {result.domain} -> {result.path}"
    )


@app.command(
    "migrate-pr-template-to-root",
    rich_help_panel="Health and State",
    short_help="Import pr-template.md into root pr_template config.",
)
def migrate_pr_template_root(
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
    template_file: Annotated[
        Path | None,
        typer.Option(
            "--template-file",
            help="Path to the legacy markdown template file to import. Defaults to config/pr-template.md.",
        ),
    ] = None,
    overwrite: Annotated[
        bool,
        typer.Option(
            "--overwrite/--no-overwrite",
            help="Replace existing root pr_template config when it differs.",
        ),
    ] = True,
) -> None:
    runtime_dir, legacy_config_path = _bootstrap_inputs(config)
    if legacy_config_path is not None and legacy_config_path.exists():
        cfg = load_config(legacy_config_path)
    else:
        cfg = _load_runtime_config_from_option(runtime_dir)
    resolved_template_file = (
        template_file.expanduser()
        if template_file is not None
        else _workspace_root_for_config_option(config) / "config" / "pr-template.md"
    )
    result = migrate_pr_template_to_root(
        cfg, template_file=resolved_template_file, overwrite=overwrite
    )
    typer.echo(f"PR template migration complete: {result.status} {result.domain} -> {result.path}")


@app.command(
    "migrate-orchestration-store",
    rich_help_panel="Health and State",
    short_help="Import orchestration JSON stores into the SQLite runtime database.",
)
def migrate_orchestration_store(
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
    database_path: Annotated[
        Path | None,
        typer.Option(
            "--database-path",
            help="Override destination SQLite database path. Defaults to worker orchestration_store_path.",
        ),
    ] = None,
    retention_days: Annotated[
        int | None,
        typer.Option(
            "--retention-days",
            min=0,
            help="Completed history retention after import. Defaults to worker orchestration_retention_days.",
        ),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable output.")] = False,
) -> None:
    runtime_config = _load_runtime_config_from_option(config)
    project_dir = _project_dir_for_config_option(config)
    worker_config = load_trigger_orchestrator_worker_global_config(
        project_dir, base_config=runtime_config
    )
    destination = database_path or worker_config.orchestration_store_path
    effective_retention_days = (
        retention_days
        if retention_days is not None
        else worker_config.orchestration_retention_days
    )
    result = migrate_orchestration_store_to_sqlite(
        database_path=destination,
        trigger_events_path=worker_config.event_queue_store_path,
        effect_executions_path=worker_config.effect_execution_store_path,
        gate_executions_path=worker_config.gate_execution_store_path,
        workflow_events_path=worker_config.workflow_event_journal_path,
        retention_days=effective_retention_days,
    )
    if json_output:
        typer.echo(json.dumps(result.as_dict(), indent=2, sort_keys=True))
        return
    typer.echo(f"Orchestration store migration complete: {result.database_path}")
    typer.echo(
        "trigger_events "
        f"imported={result.trigger_events_imported} "
        f"existing={result.trigger_events_existing} "
        f"skipped={result.trigger_events_skipped}"
    )
    typer.echo(
        "effect_executions "
        f"imported={result.effect_executions_imported} "
        f"existing={result.effect_executions_existing} "
        f"skipped={result.effect_executions_skipped}"
    )
    typer.echo(
        "gate_executions "
        f"imported={result.gate_executions_imported} "
        f"existing={result.gate_executions_existing} "
        f"skipped={result.gate_executions_skipped}"
    )
    typer.echo(
        "workflow_events "
        f"imported={result.workflow_events_imported} "
        f"existing={result.workflow_events_existing} "
        f"skipped={result.workflow_events_skipped}"
    )
    typer.echo(
        "retention_deleted "
        f"trigger_events={result.retention_trigger_events_deleted} "
        f"effect_executions={result.retention_effect_executions_deleted} "
        f"gate_executions={result.retention_gate_executions_deleted} "
        f"workflow_events={result.retention_workflow_events_deleted}"
    )


@app.command(
    "rebuild-audit-index",
    rich_help_panel="Health and State",
    short_help="Import tool-call audit JSONL entries into the SQLite audit index.",
)
def rebuild_audit_index(
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
    database_path: Annotated[
        Path | None,
        typer.Option(
            "--database-path",
            help="Override destination SQLite database path. Defaults to runtime/audit.db.",
        ),
    ] = None,
    max_lines: Annotated[
        int | None,
        typer.Option(
            "--max-lines",
            min=1,
            help="Maximum JSONL lines to import in this run. Omit to import the full log.",
        ),
    ] = None,
    reset: Annotated[
        bool,
        typer.Option(
            "--reset/--resume",
            help="Rebuild the index from the start instead of resuming saved import progress.",
        ),
    ] = True,
    json_output: Annotated[bool, typer.Option("--json", help="Emit machine-readable output.")] = False,
) -> None:
    runtime_config = _load_runtime_config_from_option(config)
    destination = database_path or audit_index_path_for_log_path(runtime_config.audit_log_path)
    index = ToolCallAuditIndex(destination)
    result = index.import_from_jsonl(
        runtime_config.audit_log_path,
        max_lines=max_lines,
        reset=reset,
    )
    if json_output:
        typer.echo(json.dumps(result.as_dict(), indent=2, sort_keys=True))
        return
    typer.echo(f"Audit index import complete: {result.database_path}")
    typer.echo(f"source={result.source_path}")
    typer.echo(
        f"lines_scanned={result.lines_scanned} imported={result.imported} "
        f"existing={result.existing} skipped_malformed={result.skipped_malformed} "
        f"skipped_non_tool_call={result.skipped_non_tool_call}"
    )
    typer.echo(
        f"offset_bytes={result.offset_bytes} complete={result.complete} "
        f"stopped_by_limit={result.stopped_by_limit}"
    )


@app.command(
    "status",
    rich_help_panel="Health and State",
    short_help="Show host and runtime health.",
)
def status(
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
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
        cfg = _load_runtime_config_from_option(config)
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
            "agent-sandbox-firewall.service",
            "agent-sandbox-gateway.service",
            "agent-sandbox-openai-relay.service",
            "agent-sandbox-http-target-relay.service",
            "agent-sandbox-discord-wake.service",
            "agent-sandbox-scheduled-wake.service",
        ]
        unit_statuses = [_get_systemd_unit_state(unit) for unit in units]
        report["systemd_units"] = [asdict(unit_status) for unit_status in unit_statuses]
        for unit_status in unit_statuses:
            if unit_status.available and unit_status.state != "active":
                report["overall"] = "degraded"

        vm_defaults = _resolve_root_vm_defaults(cfg)
        gateway_health_url = vm_defaults.gateway_url
        relay_health_url = f"http://{_normalize_host_for_local_check(cfg.openai_relay_host)}:{cfg.openai_relay_port}/healthz"
        http_target_relay_health_url = f"http://{_normalize_host_for_local_check(cfg.http_target_relay_host)}:{cfg.http_target_relay_port}/healthz"
        health_results = [
            _check_health_endpoint("gateway", gateway_health_url),
            _check_health_endpoint("openai_relay", relay_health_url),
            _check_health_endpoint("http_target_relay", http_target_relay_health_url),
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
            now = datetime.now(UTC)
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

    typer.echo("Agent Sandbox Status")
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


@teams_auth_app.command("login", short_help="Start delegated Teams auth device-code flow.")
def teams_auth_login(
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
    directory: DirectoryOption = "root",
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


@teams_auth_app.command("status", short_help="Show delegated Teams auth status.")
def teams_auth_status(
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
    directory: DirectoryOption = "root",
) -> None:
    service = _teams_auth_service(config)
    result = _teams_auth_call(service, "status", directory=directory)
    _teams_auth_emit_result("Teams auth status", result)


@teams_auth_app.command("logout", short_help="Clear delegated Teams auth state.")
def teams_auth_logout(
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
    directory: DirectoryOption = "root",
) -> None:
    service = _teams_auth_service(config)
    result = _teams_auth_call(service, "logout", directory=directory)
    _teams_auth_emit_result("Teams auth logout", result)


@teams_auth_app.command("list", short_help="List delegated Teams auth entries.")
def teams_auth_list(config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR) -> None:
    service = _teams_auth_service(config)
    result = _teams_auth_call(service, "list")
    _teams_auth_emit_result("Teams auth entries", result)


@teams_auth_app.command("channels", short_help="Resolve Teams channel IDs within a team.")
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
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
    directory: DirectoryOption = "root",
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


@teams_meeting_app.command("start", short_help="Start a live Teams meeting agent session.")
def teams_meeting_start(
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
    directory: Annotated[
        str,
        typer.Option("--directory", help="Directory path for the dedicated meeting session."),
    ] = "root",
    join_url: Annotated[
        str,
        typer.Option("--join-url", help="Manual Teams meeting join URL for the media bot."),
    ] = "",
    model: Annotated[
        str | None,
        typer.Option("--model", help="Agent model override for the dedicated session."),
    ] = None,
    session_id: Annotated[
        str | None,
        typer.Option("--session-id", help="Optional explicit session id to create."),
    ] = None,
    origin_session_id: Annotated[
        str | None,
        typer.Option(
            "--origin-session-id",
            help="Existing agent session to project into the live meeting.",
        ),
    ] = None,
    prompt: Annotated[
        str | None,
        typer.Option("--prompt", help="Voice-session task prompt for the projected meeting agent."),
    ] = None,
    ttl_hours: Annotated[
        int | None,
        typer.Option("--ttl-hours", help="Session time to live in hours."),
    ] = None,
) -> None:
    cfg = _load_runtime_config_from_option(config)
    runtime = build_runtime(cfg)
    service = TeamsMeetingService(
        config=cfg,
        runtime=runtime,
        store=runtime.teams_meeting_store,
        bridge_client=runtime.teams_meeting_bridge_client,
    )
    try:
        result = service.start_meeting(
            directory_path=directory,
            join_url=join_url,
            model=model,
            session_id=session_id,
            ttl_hours=ttl_hours,
            origin_session_id=origin_session_id,
            prompt=prompt,
        )
    except (ValueError, RuntimeError, httpx.HTTPError) as exc:
        typer.echo(f"Teams meeting start failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    _echo_model_json(result)


@teams_meeting_app.command("stop", short_help="Stop a live Teams meeting agent session.")
def teams_meeting_stop(
    session_id: Annotated[str, typer.Argument(help="Teams meeting session id.")],
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
) -> None:
    cfg = _load_runtime_config_from_option(config)
    runtime = build_runtime(cfg)
    service = TeamsMeetingService(
        config=cfg,
        runtime=runtime,
        store=runtime.teams_meeting_store,
        bridge_client=runtime.teams_meeting_bridge_client,
    )
    try:
        result = service.stop_meeting(session_id=session_id)
    except (ValueError, RuntimeError, httpx.HTTPError) as exc:
        typer.echo(f"Teams meeting stop failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    _echo_model_json(result)


@app.command(
    "create-session",
    rich_help_panel="Sessions and Feedback",
    short_help="Create a session manifest.",
)
def create_session(
    session_id: str,
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
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
) -> None:
    cfg = _load_runtime_config_from_option(config)
    runtime = build_runtime(cfg)
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
        expires_at=datetime.now(UTC) + _resolve_session_ttl(policy, ttl_hours),
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
                root_dir=_project_dir_for_config_option(config),
            )
            manifest.model = resolve_agent_model_for_manifest(manifest, issue_setup=issue_setup)
        except ValueError:
            manifest.model = None
    if manifest.email_send_enabled:
        manifest.allowed_tools.extend([ToolName.EMAIL_SEND, ToolName.EMAIL_SEND_APPROVED])
    _runtime_session_repository(runtime).upsert_session(manifest)
    typer.echo(f"Created session manifest {session_id}")


@app.command(
    "prune-sessions",
    rich_help_panel="Sessions and Feedback",
    short_help="Delete expired or all sessions.",
)
def prune_sessions(
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
    expired_only: Annotated[
        bool,
        typer.Option(
            "--expired-only/--all",
            help="Delete only expired session manifests, or all session manifests.",
        ),
    ] = True,
) -> None:
    cfg = _load_runtime_config_from_option(config)
    runtime = build_runtime(cfg)
    session_repository = _runtime_session_repository(runtime)
    sessions = {session.session_id: session for session in session_repository.list_sessions()}
    now = datetime.now(UTC)

    if expired_only:
        keys_to_delete = [
            session_id for session_id, session in sessions.items() if session.expires_at <= now
        ]
    else:
        keys_to_delete = list(sessions.keys())

    for session_id in keys_to_delete:
        session_repository.delete_session(session_id)

    mode = "expired" if expired_only else "all"
    typer.echo(f"Pruned {len(keys_to_delete)} {mode} session(s)")


@app.command(
    "list-sessions",
    rich_help_panel="Sessions and Feedback",
    short_help="List active, expired, or all session manifests.",
)
def list_sessions(
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
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

    cfg = _load_runtime_config_from_option(config)
    runtime = build_runtime(cfg)
    sessions = _runtime_sessions_by_id(runtime)
    now = datetime.now(UTC)

    selected: list[tuple[str, SessionManifest]] = []
    for item in sorted(sessions.items(), key=_session_sort_key):
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


@app.command(
    "migrate-vm-sessions-to-container",
    rich_help_panel="Sessions and Feedback",
    short_help="Copy libvirt VM session state into the local container worker and cut over manifests.",
)
def migrate_vm_sessions_to_container(
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
    session_id: Annotated[
        list[str] | None,
        typer.Option("--session-id", help="Session id to migrate. May be repeated."),
    ] = None,
    all_sessions: Annotated[
        bool,
        typer.Option("--all", help="Select all libvirt-backed sessions."),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Preview selected sessions without copying or rewriting state."),
    ] = False,
    copy_only: Annotated[
        bool,
        typer.Option("--copy-only", help="Copy VM state into worker session directories."),
    ] = False,
    cutover: Annotated[
        bool,
        typer.Option("--cutover", help="Rewrite selected session manifests to container_worker_vm."),
    ] = False,
    verify_only: Annotated[
        bool,
        typer.Option("--verify-only", help="Verify copied worker state without copying or cutting over."),
    ] = False,
    rollback: Annotated[
        Path | None,
        typer.Option("--rollback", help="Restore selected manifests from a migration backup directory."),
    ] = None,
    worker_root: Annotated[
        Path | None,
        typer.Option("--worker-root", help="Container worker root. Defaults to local setup if present."),
    ] = None,
    backup_root: Annotated[
        Path | None,
        typer.Option("--backup-root", help="Directory for manifest cutover backups."),
    ] = None,
    vm_user: Annotated[
        str,
        typer.Option("--vm-user", help="SSH user for copying /home/agent and /artifacts."),
    ] = "agent",
    log_vm_user: Annotated[
        str,
        typer.Option("--log-vm-user", help="SSH user for optional VM run-log copy."),
    ] = "agent-admin",
    include_logs: Annotated[
        bool,
        typer.Option("--include-logs/--skip-logs", help="Best-effort copy of VM agent run logs."),
    ] = True,
    final_delta: Annotated[
        bool,
        typer.Option("--final-delta", help="Run rsync with --delete during the state copy."),
    ] = False,
    skip_worker_session_check: Annotated[
        bool,
        typer.Option(
            "--skip-worker-session-check",
            help="Allow cutover without verifying copied worker session roots.",
        ),
    ] = False,
    ssh_timeout_seconds: Annotated[
        int,
        typer.Option("--ssh-timeout-seconds", min=1, help="Per-VM SSH discovery timeout."),
    ] = 180,
    json_output: Annotated[bool, typer.Option("--json", help="Emit JSON output.")] = False,
) -> None:
    if rollback is not None and (dry_run or copy_only or cutover or verify_only):
        raise typer.BadParameter("--rollback cannot be combined with migration phase options.")
    if final_delta and not copy_only:
        raise typer.BadParameter("--final-delta requires --copy-only.")
    if not any((dry_run, copy_only, cutover, verify_only, rollback is not None)):
        dry_run = True

    cfg = _load_runtime_config_from_option(config)
    repository = JsonSessionManifestRepository(cfg.session_store_path)
    selected_session_ids = [value for value in session_id or [] if str(value).strip()]

    if rollback is not None:
        restored = restore_selected_manifests_from_backup(
            repository,
            rollback.expanduser().resolve(),
            session_ids=selected_session_ids or None,
        )
        report = {
            "rollback": {
                "backup_path": str(rollback.expanduser().resolve()),
                "restored_session_ids": restored,
            }
        }
        if json_output:
            typer.echo(json.dumps(report, indent=2, sort_keys=True))
            return
        typer.echo(
            f"Restored {len(restored)} session manifest(s) from {rollback.expanduser().resolve()}."
        )
        for restored_session_id in restored:
            typer.echo(f"- {restored_session_id}")
        return

    sessions = {session.session_id: session for session in repository.list_sessions()}
    try:
        selected = select_sessions_for_migration(
            sessions,
            session_ids=selected_session_ids,
            all_sessions=all_sessions,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc

    resolved_worker_root = (
        worker_root.expanduser().resolve()
        if worker_root is not None
        else default_local_worker_root(cfg.runtime_dir)
    )
    resolved_backup_root = (
        backup_root.expanduser().resolve()
        if backup_root is not None
        else default_migration_backup_root(cfg.runtime_dir)
    )
    plans = [
        build_session_migration_plan(session, worker_root=resolved_worker_root)
        for session in selected
    ]
    report: dict[str, object] = {
        "worker_root": str(resolved_worker_root),
        "backup_root": str(resolved_backup_root),
        "plan": plan_payload(plans),
        "copy_results": [],
        "verifications": [],
        "cutover": None,
    }
    blocking_plans = [plan for plan in plans if not plan.eligible]
    if blocking_plans and (copy_only or cutover):
        if json_output:
            typer.echo(json.dumps(report, indent=2, sort_keys=True))
        blockers = "; ".join(
            f"{plan.session_id}: {', '.join(plan.blockers)}" for plan in blocking_plans
        )
        raise typer.BadParameter(f"selected sessions are not migratable: {blockers}")

    if dry_run:
        if json_output:
            typer.echo(json.dumps(report, indent=2, sort_keys=True))
            return
        _echo_vm_to_container_plan(plans, worker_root=resolved_worker_root)
        return

    if copy_only:
        copy_results = []

        def _progress(message: str) -> None:
            typer.echo(message, err=True)

        for session in selected:
            result = copy_vm_session_state(
                session,
                config=cfg,
                worker_root=resolved_worker_root,
                vm_user=vm_user,
                log_vm_user=log_vm_user,
                include_logs=include_logs,
                final_delta=final_delta,
                ssh_timeout_seconds=ssh_timeout_seconds,
                progress=_progress if not json_output else None,
            )
            copy_results.append(result.as_json())
        report["copy_results"] = copy_results
        failed_copies = [
            item for item in copy_results if isinstance(item, dict) and not item.get("ok")
        ]
        if failed_copies and cutover:
            if json_output:
                typer.echo(json.dumps(report, indent=2, sort_keys=True))
            raise typer.BadParameter("state copy failed for at least one session; refusing cutover.")

    if verify_only or cutover or copy_only:
        verifications = [
            verify_worker_session_state(
                worker_root=resolved_worker_root,
                session_id=session.session_id,
            ).as_json()
            for session in selected
        ]
        report["verifications"] = verifications
        failed_verifications = [
            item for item in verifications if isinstance(item, dict) and not item.get("ok")
        ]
        if failed_verifications and cutover and not skip_worker_session_check:
            if json_output:
                typer.echo(json.dumps(report, indent=2, sort_keys=True))
            raise typer.BadParameter(
                "worker session verification failed; rerun copy or pass --skip-worker-session-check."
            )

    if cutover:
        backup = create_migration_backup(
            repository,
            selected,
            backup_root=resolved_backup_root,
        )
        migrated = cutover_sessions_to_container(
            repository,
            selected,
            backup_id=backup.backup_id,
        )
        report["cutover"] = {
            "backup": backup.as_json(),
            "migrated_session_ids": [session.session_id for session in migrated],
        }

    if json_output:
        typer.echo(json.dumps(report, indent=2, sort_keys=True))
        return
    _echo_vm_to_container_report(report)


def _echo_vm_to_container_plan(
    plans: list[object], *, worker_root: Path
) -> None:
    typer.echo(f"Worker root: {worker_root}")
    if not plans:
        typer.echo("No sessions selected.")
        return
    for plan in plans:
        session_id = getattr(plan, "session_id", "")
        vm_name = getattr(plan, "source_vm_name", None) or "-"
        destination = getattr(plan, "destination", None)
        destination_text = str(getattr(destination, "agent_home", "-"))
        status = "eligible" if getattr(plan, "eligible", False) else "blocked"
        typer.echo(f"{session_id}\t{status}\tvm={vm_name}\tagent_home={destination_text}")
        blockers = getattr(plan, "blockers", []) or []
        warnings = getattr(plan, "warnings", []) or []
        for blocker in blockers:
            typer.echo(f"  blocker: {blocker}")
        for warning in warnings:
            typer.echo(f"  warning: {warning}")


def _echo_vm_to_container_report(report: dict[str, object]) -> None:
    copy_results = report.get("copy_results")
    if isinstance(copy_results, list) and copy_results:
        for item in copy_results:
            if not isinstance(item, dict):
                continue
            copied = ", ".join(str(label) for label in item.get("copied", []) or []) or "none"
            status = "ok" if item.get("ok") else "failed"
            typer.echo(f"copy {item.get('session_id')}: {status}; copied={copied}")
            for warning in item.get("warnings", []) or []:
                typer.echo(f"  warning: {warning}")
            for error in item.get("errors", []) or []:
                typer.echo(f"  error: {error}")
    verifications = report.get("verifications")
    if isinstance(verifications, list) and verifications:
        for item in verifications:
            if not isinstance(item, dict):
                continue
            status = "ok" if item.get("ok") else "failed"
            typer.echo(f"verify {item.get('session_id')}: {status}")
            for warning in item.get("warnings", []) or []:
                typer.echo(f"  warning: {warning}")
            for error in item.get("errors", []) or []:
                typer.echo(f"  error: {error}")
    cutover = report.get("cutover")
    if isinstance(cutover, dict):
        backup = cutover.get("backup")
        backup_path = backup.get("path") if isinstance(backup, dict) else None
        migrated = cutover.get("migrated_session_ids") or []
        typer.echo(f"Cut over {len(migrated)} session manifest(s). Backup: {backup_path}")
        for migrated_session_id in migrated:
            typer.echo(f"- {migrated_session_id}")


@app.command(
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
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
) -> None:
    cfg = _load_runtime_config_from_option(config)
    runtime = build_runtime(cfg)
    session_repository = _runtime_session_repository(runtime)
    sessions = {session.session_id: session for session in session_repository.list_sessions()}
    session_id, manifest = _resolve_session_target(target, sessions)
    duration = _parse_session_extension(extension)
    base_time = datetime.now(UTC)
    previous_expires_at = manifest.expires_at
    manifest.expires_at = base_time + duration
    session_repository.upsert_session(manifest)
    typer.echo(
        f"Renewed session {session_id} "
        f"(previous_expires_at={previous_expires_at.isoformat()} "
        f"new_expires_at={manifest.expires_at.isoformat()})"
    )


@app.command(
    "revoke-session",
    rich_help_panel="Sessions and Feedback",
    short_help="Expire a session immediately.",
)
def revoke_session(
    target: str,
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
) -> None:
    cfg = _load_runtime_config_from_option(config)
    runtime = build_runtime(cfg)
    session_repository = _runtime_session_repository(runtime)
    sessions = {session.session_id: session for session in session_repository.list_sessions()}
    session_id, manifest = _resolve_session_target(target, sessions)
    previous_expires_at = manifest.expires_at
    manifest.expires_at = datetime.now(UTC)
    session_repository.upsert_session(manifest)
    typer.echo(
        f"Revoked session {session_id} "
        f"(previous_expires_at={previous_expires_at.isoformat()} "
        f"new_expires_at={manifest.expires_at.isoformat()})"
    )


@app.command(
    "delete-session",
    rich_help_panel="Sessions and Feedback",
    short_help="Delete a session manifest.",
)
def delete_session(
    target: str,
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
) -> None:
    service = AdminService(config)
    try:
        result = service.delete_session(target)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    _echo_action_result_or_exit(result)


@app.command(
    "delete-expired-sessions",
    rich_help_panel="Sessions and Feedback",
    short_help="Delete all expired session manifests.",
)
def delete_expired_sessions(
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
) -> None:
    service = AdminService(config)
    sessions = {
        session.session_id: session
        for session in service.runtime.session_repository.list_sessions()
    }
    now = datetime.now(UTC)
    expired_session_ids = [
        session_id for session_id, session in sessions.items() if session.expires_at <= now
    ]
    deleted = 0
    skipped: list[str] = []
    for session_id in expired_session_ids:
        try:
            service.delete_session(session_id)
        except ValueError as exc:
            skipped.append(f"{session_id}: {exc}")
            continue
        deleted += 1
    typer.echo(f"Deleted {deleted} expired session(s)")
    if skipped:
        typer.echo(f"Skipped {len(skipped)} associated expired session(s):", err=True)
        for item in skipped:
            typer.echo(f"  {item}", err=True)


@app.command(
    "destroy-vm",
    rich_help_panel="VM Operations",
    short_help="Destroy a VM and prune local session state.",
)
def destroy_vm(
    target: str,
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
) -> None:
    service = AdminService(config)
    result = service.destroy_vm(target)
    _echo_action_result_or_exit(result)


@app.command(
    "destroy-issue-vm",
    rich_help_panel="Issue Lifecycle",
    short_help="Destroy an issue VM and state.",
)
def destroy_issue_vm(
    target: str,
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
) -> None:
    issue_number = _resolve_issue_number_or_raise(target)
    vm_name = f"vm-{issue_number}"
    cfg = _load_runtime_config_from_option(config)
    runtime = build_runtime(cfg)

    session_repository = _runtime_session_repository(runtime)
    sessions = {session.session_id: session for session in session_repository.list_sessions()}
    session_ids = _matching_issue_session_ids(issue_number, sessions)
    if session_ids:
        for session_id in session_ids:
            session_repository.delete_session(session_id)
    removed_approvals = _prune_approvals_for_sessions(runtime, session_ids=session_ids)

    removed_pr_map_entries, removed_streams = _prune_pr_map(
        cfg.pr_map_path, session_ids=session_ids
    )
    removed_cursors, removed_feedback = _prune_pr_feedback_store(
        cfg.runtime_dir / "pr-feedback-store.json",
        streams=removed_streams,
    )

    root_dir = Path(__file__).resolve().parents[2]
    bare_repo_removed, bare_repo_path = _remove_issue_bare_repo(
        issue_number,
        root_dir=root_dir,
        runtime_dir=cfg.runtime_dir,
    )

    destroy_script = root_dir / "scripts" / "destroy_agent_vm.sh"
    _run_subprocess_compat(
        [str(destroy_script), vm_name], check=True, capture_output=False, text=False
    )

    typer.echo(
        f"Destroyed issue VM {vm_name}; "
        f"removed_sessions={len(session_ids)} "
        f"removed_pr_map_entries={removed_pr_map_entries} "
        f"removed_approvals={removed_approvals} "
        f"removed_feedback_cursors={removed_cursors} "
        f"removed_feedback_items={removed_feedback} "
        f"bare_repo_removed={bare_repo_removed} "
        f"bare_repo_path={bare_repo_path}"
    )


@app.command(
    "prune-orphaned-issue-state",
    rich_help_panel="Issue Lifecycle",
    short_help="Remove issue state for missing VMs.",
)
def prune_orphaned_issue_state(
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
) -> None:
    cfg = _load_runtime_config_from_option(config)
    runtime = build_runtime(cfg)
    live_vm_names = _list_existing_issue_vm_names()
    live_issue_numbers = {
        issue_number
        for issue_number in (_extract_issue_number(vm_name) for vm_name in live_vm_names)
        if issue_number is not None
    }
    root_dir = Path(__file__).resolve().parents[2]

    session_repository = _runtime_session_repository(runtime)
    sessions = {session.session_id: session for session in session_repository.list_sessions()}
    orphan_session_ids = {
        session_id
        for session_id, session in sessions.items()
        if (
            (
                issue_number := (
                    _extract_issue_number(session.vm_id) or _extract_issue_number(session_id)
                )
            )
            is not None
            and f"vm-{issue_number}" not in live_vm_names
        )
    }
    orphan_issue_numbers = {
        issue_number
        for issue_number in (
            (_extract_issue_number(sessions[session_id].vm_id) or _extract_issue_number(session_id))
            for session_id in orphan_session_ids
        )
        if issue_number is not None
    }
    orphan_issue_numbers.update(
        issue_number
        for issue_number in _issue_numbers_from_pr_map(cfg.pr_map_path)
        if issue_number not in live_issue_numbers
    )
    orphan_issue_numbers.update(
        issue_number
        for issue_number in _issue_numbers_from_bare_repos(root_dir)
        if issue_number not in live_issue_numbers
    )

    session_streams = _session_streams_for_session_ids(sessions, session_ids=orphan_session_ids)
    if orphan_session_ids:
        for session_id in orphan_session_ids:
            session_repository.delete_session(session_id)
    removed_approvals = _prune_approvals_for_sessions(runtime, session_ids=orphan_session_ids)

    removed_pr_map_entries_a, removed_pr_map_streams_a = _prune_pr_map(
        cfg.pr_map_path,
        session_ids=orphan_session_ids,
    )
    removed_pr_map_entries_b, removed_pr_map_streams_b = _prune_pr_map_for_issue_numbers(
        cfg.pr_map_path,
        issue_numbers=orphan_issue_numbers,
    )
    removed_streams = session_streams | removed_pr_map_streams_a | removed_pr_map_streams_b
    removed_cursors, removed_feedback = _prune_pr_feedback_store(
        cfg.runtime_dir / "pr-feedback-store.json",
        streams=removed_streams,
    )

    removed_bare_repo_count = 0
    for issue_number in sorted(orphan_issue_numbers):
        if issue_number in live_issue_numbers:
            continue
        bare_repo_removed, _ = _remove_issue_bare_repo(
            issue_number,
            root_dir=root_dir,
            runtime_dir=cfg.runtime_dir,
        )
        if bare_repo_removed:
            removed_bare_repo_count += 1

    typer.echo(
        f"Pruned orphaned issue state "
        f"removed_sessions={len(orphan_session_ids)} "
        f"removed_approvals={removed_approvals} "
        f"removed_pr_map_entries={removed_pr_map_entries_a + removed_pr_map_entries_b} "
        f"removed_feedback_cursors={removed_cursors} "
        f"removed_feedback_items={removed_feedback} "
        f"removed_bare_repos={removed_bare_repo_count}"
    )


@app.command(
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
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
) -> None:
    if as_json:
        service = AdminService(config)
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
    _run_subprocess_compat(cmd, check=True, capture_output=False, text=False)


def _echo_action_result_or_exit(result) -> None:
    typer.echo(result.message)
    if not result.success:
        raise typer.Exit(code=1)


@app.command(
    "start-vm",
    rich_help_panel="VM Operations",
    short_help="Start a VM.",
)
def start_vm(
    target: str,
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
) -> None:
    service = AdminService(config)
    _echo_action_result_or_exit(service.start_vm(target))


@app.command(
    "shutdown-vm",
    rich_help_panel="VM Operations",
    short_help="Gracefully stop a VM.",
)
def shutdown_vm(
    target: str,
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
    timeout_seconds: Annotated[
        int,
        typer.Option("--timeout-seconds", min=1, help="How long to wait for graceful shutdown."),
    ] = 60,
) -> None:
    service = AdminService(config)
    _echo_action_result_or_exit(service.shutdown_vm(target, timeout_seconds=timeout_seconds))


@app.command(
    "create-issue",
    rich_help_panel="Issue Lifecycle",
    short_help="Create a local/proxy-backed issue record.",
)
def create_issue(
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
    title: Annotated[
        str,
        typer.Option("--title", help="Issue title."),
    ] = "",
    body: Annotated[
        str,
        typer.Option("--body", help="Issue body/description."),
    ] = "",
    repo_source: Annotated[
        str | None,
        typer.Option(
            "--repo-source", help="Default repo source used when starting a VM for this issue."
        ),
    ] = None,
    yaml_path: Annotated[
        Path | None,
        typer.Option(
            "--yaml-path",
            help="Import an existing issue YAML file instead of creating one from title/body.",
        ),
    ] = None,
) -> None:
    service = AdminService(config)
    issue = service.create_issue(
        title=title,
        body=body,
        repo_source=repo_source,
        yaml_path=str(yaml_path) if yaml_path is not None else None,
    )
    typer.echo(f"Created local issue {issue.issue_number} ({issue.title}) at {issue.yaml_path}")


@app.command(
    "create-local-issue",
    rich_help_panel="Issue Lifecycle",
    short_help="Create a local issue record.",
)
def create_local_issue(
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
    title: Annotated[
        str,
        typer.Option("--title", help="Local issue title."),
    ] = "",
    body: Annotated[
        str,
        typer.Option("--body", help="Local issue body/description."),
    ] = "",
    repo_source: Annotated[
        str | None,
        typer.Option(
            "--repo-source",
            help="Default repo source used when starting a VM for this local issue.",
        ),
    ] = None,
    yaml_path: Annotated[
        Path | None,
        typer.Option(
            "--yaml-path",
            help="Import an existing issue YAML file instead of creating one from title/body.",
        ),
    ] = None,
) -> None:
    service = AdminService(config)
    issue = service.create_local_issue(
        title=title,
        body=body,
        repo_source=repo_source,
        yaml_path=str(yaml_path) if yaml_path is not None else None,
    )
    typer.echo(f"Created local issue {issue.issue_number} ({issue.title}) at {issue.yaml_path}")


@app.command(
    "list-issues",
    rich_help_panel="Issue Lifecycle",
    short_help="List local issue records.",
)
def list_issues(
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable local issue records."),
    ] = False,
) -> None:
    service = AdminService(config)
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


@app.command(
    "list-local-issues",
    rich_help_panel="Issue Lifecycle",
    short_help="List local issue records.",
)
def list_local_issues(
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
    as_json: Annotated[
        bool,
        typer.Option("--json", help="Emit machine-readable local issue records."),
    ] = False,
) -> None:
    list_issues(config=config, as_json=as_json)


@app.command(
    "get-issue",
    rich_help_panel="Issue Lifecycle",
    short_help="Read a local/proxy issue payload by local issue identifier.",
)
def get_issue(
    issue_id: str,
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
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
    service = AdminService(config)
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


@app.command(
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
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
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
    service = AdminService(config)
    payload = service.add_issue_comment(
        issue_id, comment, sync_proxy_cache=sync_proxy_cache
    )
    if as_json:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    typer.echo(
        f"Comment added to issue {payload.get('issue_number', issue_id)} ({payload.get('source_type', '')})"
    )


@app.command(
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
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
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
    service = AdminService(config)
    payload = service.set_issue_state(
        issue_id, state, sync_proxy_cache=sync_proxy_cache
    )
    if as_json:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    typer.echo(
        f"State updated for issue {payload.get('issue_number', issue_id)} ({payload.get('source_type', '')})"
    )


@app.command(
    "ssh",
    rich_help_panel="VM Operations",
    short_help="Open an SSH session.",
)
def ssh(
    target: str,
    vm_user: Annotated[str, typer.Option("--vm-user", help="Guest user to connect as.")] = "agent",
) -> None:
    vm_name = _resolve_vm_name(target)
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "ssh.sh"
    _run_subprocess_compat(
        [str(script_path), vm_name, vm_user], check=True, capture_output=False, text=False
    )


@app.command(
    "logs",
    rich_help_panel="VM Operations",
    short_help="Tail guest agent logs.",
)
def logs(
    target: str,
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
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
        issue_number = _resolve_issue_number_or_raise(target)
        script_path = Path(__file__).resolve().parents[2] / "scripts" / "tail_issue_vm_log.sh"
        cmd = [str(script_path), issue_number, str(lines)]
        if repo_name is not None:
            cmd.append(repo_name)
            cmd.append(vm_user)
        elif vm_user != "agent":
            cmd.extend(["", vm_user])
        _run_subprocess_compat(cmd, check=True, capture_output=False, text=False)
        return

    service = AdminService(config)
    output = service.get_agent_log_snapshot(
        target, lines=lines, repo_name=repo_name, vm_user=vm_user
    )
    typer.echo(output)


def _set_agent_control_state(target: str, *, action: str, vm_user: str, config: Path) -> None:
    normalized = target.strip()
    try:
        cfg = _load_runtime_config_from_option(config)
    except ValueError:
        runtime_dir, _ = _bootstrap_inputs(config)
        cfg = GatewayConfig(runtime_dir=runtime_dir)
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "set_issue_agent_state.sh"
    if normalized.isdigit() or (normalized.startswith("vm-") and normalized[3:].isdigit()):
        issue_number = _resolve_issue_number_or_raise(target)
        cmd = [
            str(script_path),
            action,
            issue_number,
            vm_user,
            "--host-state-path",
            str(cfg.host_agent_control_state_path),
        ]
    else:
        vm_name = _resolve_vm_name(target)
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
    _run_subprocess_compat(
        cmd,
        check=True,
        capture_output=False,
        text=False,
    )


@app.command(
    "disable-agent",
    rich_help_panel="VM Operations",
    short_help="Pause the guest agent.",
)
def disable_agent(
    target: str,
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
    vm_user: Annotated[str, typer.Option("--vm-user", help="Guest user to connect as.")] = "agent",
) -> None:
    _set_agent_control_state(target, action="disable", vm_user=vm_user, config=config)


@app.command(
    "enable-agent",
    rich_help_panel="VM Operations",
    short_help="Allow the guest agent to run.",
)
def enable_agent(
    target: str,
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
    vm_user: Annotated[str, typer.Option("--vm-user", help="Guest user to connect as.")] = "agent",
) -> None:
    _set_agent_control_state(target, action="enable", vm_user=vm_user, config=config)


@app.command(
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
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
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
) -> None:
    normalized = target.strip()
    cfg = _load_runtime_config_from_option(config)
    runtime = build_runtime(cfg)
    admin = AdminService(config)
    sessions = _runtime_sessions_by_id(runtime)
    try:
        resolved_session_id, _ = _resolve_session_target(normalized, sessions)
    except typer.BadParameter as exc:
        raise typer.BadParameter(
            f"Cannot resolve resume target {target!r} to a session. "
            "Use a session id, issue number, or vm name with an existing session."
        ) from exc
    try:
        agent_model = admin.resolve_agent_model_for_target(resolved_session_id)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "resume_issue_agent.sh"
    if normalized.isdigit() or (normalized.startswith("vm-") and normalized[3:].isdigit()):
        issue_number = _resolve_issue_number_or_raise(target)
        vm_name = f"vm-{issue_number}"
        cmd = [
            str(script_path),
            message,
            issue_number,
            vm_user,
            "--host-control-state-path",
            str(cfg.host_agent_control_state_path),
        ]
    else:
        vm_name = _resolve_vm_name(target)
        cmd = [
            str(script_path),
            "--prompt",
            message,
            "--vm-name",
            vm_name,
            "--vm-user",
            vm_user,
            "--host-control-state-path",
            str(cfg.host_agent_control_state_path),
        ]
    if observe:
        cmd.extend(["--observe", str(lines)])

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
    if observe:
        completed = subprocess.run(cmd, check=False, env=os.environ | {"AGENT_MODEL": agent_model})
    else:
        completed = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            env=os.environ | {"AGENT_MODEL": agent_model},
        )
    if completed.returncode == 0:
        runtime.audit.log_decision(
            event_type=AuditEventType.VM_START_SUCCEEDED,
            session_id=resolved_session_id,
            vm_id=vm_name,
            decision="succeeded",
            reason="resume-script-success",
        )
        runtime.audit.log_decision(
            event_type=AuditEventType.RESUME_SUCCEEDED,
            session_id=resolved_session_id,
            vm_id=vm_name,
            decision="succeeded",
            reason="resume-script-success",
        )
        return

    reason = f"resume-script-exit-{completed.returncode}"
    runtime.audit.log_decision(
        event_type=AuditEventType.VM_START_FAILED,
        session_id=resolved_session_id,
        vm_id=vm_name,
        decision="failed",
        reason=reason,
        args={"stderr": (completed.stderr or "").strip()},
    )
    runtime.audit.log_decision(
        event_type=AuditEventType.RESUME_FAILED,
        session_id=resolved_session_id,
        vm_id=vm_name,
        decision="failed",
        reason=reason,
        args={"stderr": (completed.stderr or "").strip()},
    )
    if getattr(completed, "stdout", ""):
        typer.echo(completed.stdout, nl=False)
    if getattr(completed, "stderr", ""):
        typer.echo(completed.stderr, err=True, nl=False)
    raise typer.Exit(code=completed.returncode)


@app.command(
    "create-vm",
    rich_help_panel="VM Operations",
    short_help="Create and bootstrap a standalone VM.",
)
def create_vm(
    vm_name: str,
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
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
                "azure_openai, or anthropic."
            ),
        ),
    ] = None,
) -> None:
    if mssql_init_root is not None and not mssql:
        raise typer.BadParameter("--mssql-init-root requires --mssql.")
    service = AdminService(config)
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
        model_provider_mode=model_provider_mode,
    )
    _echo_action_result_or_exit(result)


@app.command(
    "clone-repo-into-vm",
    rich_help_panel="VM Operations",
    short_help="Clone a repo into an existing VM/session.",
)
def clone_repo_into_vm(
    vm_name: str,
    repo_source: Annotated[
        str,
        typer.Option("--repo-source", help="Host repo path or remote URL to clone into the guest."),
    ],
    clone_branch: Annotated[
        str | None,
        typer.Option(
            "--clone-branch",
            help="Branch to clone and record as the agent's main branch in the guest.",
        ),
    ] = None,
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
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
    service = AdminService(config)
    result = service.clone_repo_into_vm(
        vm_name=vm_name,
        session_id=session_id,
        repo_source=repo_source,
        clone_branch=clone_branch,
        vm_user=vm_user,
        directory_path=directory_path,
    )
    _echo_action_result_or_exit(result)


@app.command(
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
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
    session_id: Annotated[
        str | None,
        typer.Option("--session-id", help="Existing session identifier associated with the VM."),
    ] = None,
    vm_user: Annotated[str, typer.Option("--vm-user", help="Guest user to configure.")] = "agent",
) -> None:
    service = AdminService(config)
    result = service.bootstrap_python_in_vm(
        vm_name=vm_name,
        session_id=session_id,
        host_checkout=host_checkout,
        target_dir=target_dir,
        vm_user=vm_user,
    )
    _echo_action_result_or_exit(result)


@app.command(
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
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
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
    service = AdminService(config)
    result = service.bootstrap_frontend_in_vm(
        vm_name=vm_name,
        session_id=session_id,
        host_checkout=host_checkout,
        install_roots=install_root,
        vm_user=vm_user,
    )
    _echo_action_result_or_exit(result)


@app.command(
    "create-issue-vm",
    rich_help_panel="Issue Lifecycle",
    short_help="Create an issue VM.",
)
def create_issue_vm(
    issue: str,
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
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
    model_provider_mode: Annotated[
        str | None,
        typer.Option(
            "--model-provider-mode",
            help=(
                "Session relay mode override: openai_public, local_openai, "
                "azure_openai, or anthropic."
            ),
        ),
    ] = None,
) -> None:
    if mssql_init_root is not None and not mssql:
        raise typer.BadParameter("--mssql-init-root requires --mssql.")
    service = AdminService(config)
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
        model_provider_mode=model_provider_mode,
    )
    _echo_action_result_or_exit(result)


@app.command(
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
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
) -> None:
    if not clear and not model.strip():
        raise typer.BadParameter("Provide --model with a non-empty value, or pass --clear.")
    if clear and model.strip():
        raise typer.BadParameter("Use either --model or --clear, not both.")
    cfg = _load_runtime_config_from_option(config)
    runtime = build_runtime(cfg)
    session_repository = _runtime_session_repository(runtime)
    sessions = {session.session_id: session for session in session_repository.list_sessions()}
    session_id, manifest = _resolve_session_target(target, sessions)
    manifest.model = None if clear else model.strip()
    session_repository.upsert_session(manifest)
    if clear:
        typer.echo(f"Cleared model override for session {session_id}")
        return
    typer.echo(f"Set model override for session {session_id} to {manifest.model}")


@app.command(
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
            help="Relay mode to persist: openai_public, local_openai, azure_openai, or anthropic.",
        ),
    ] = "",
    clear: Annotated[
        bool,
        typer.Option("--clear", help="Clear the session override so directory defaults apply."),
    ] = False,
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
) -> None:
    if not clear and not mode.strip():
        raise typer.BadParameter("Provide --mode with a non-empty value, or pass --clear.")
    if clear and mode.strip():
        raise typer.BadParameter("Use either --mode or --clear, not both.")
    cfg = _load_runtime_config_from_option(config)
    runtime = build_runtime(cfg)
    session_repository = _runtime_session_repository(runtime)
    sessions = {session.session_id: session for session in session_repository.list_sessions()}
    session_id, manifest = _resolve_session_target(target, sessions)
    value = "" if clear else normalize_model_provider_mode_override(mode)
    metadata = dict(manifest.metadata)
    metadata[MODEL_PROVIDER_MODE_METADATA_KEY] = value
    manifest.metadata = metadata
    session_repository.upsert_session(manifest)
    if clear:
        typer.echo(f"Cleared model provider mode override for session {session_id}")
        return
    typer.echo(f"Set model provider mode override for session {session_id} to {value}")


@app.command(
    "add-tool-permission",
    rich_help_panel="Sessions and Feedback",
    short_help="Add an allowed tool to a session manifest.",
)
def add_tool_permission(
    target: str,
    tool: ToolName,
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
) -> None:
    cfg = _load_runtime_config_from_option(config)
    runtime = build_runtime(cfg)
    session_repository = _runtime_session_repository(runtime)
    sessions = {session.session_id: session for session in session_repository.list_sessions()}
    session_id, manifest = _resolve_session_target(target, sessions)
    if tool in manifest.allowed_tools:
        typer.echo(f"Tool permission {tool.value} already present on session {session_id}")
        return

    manifest.allowed_tools.append(tool)
    session_repository.upsert_session(manifest)
    typer.echo(f"Added tool permission {tool.value} to session {session_id}")


@app.command(
    "revoke-tool-permission",
    rich_help_panel="Sessions and Feedback",
    short_help="Remove an allowed tool from a session manifest.",
)
def revoke_tool_permission(
    target: str,
    tool: ToolName,
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
) -> None:
    cfg = _load_runtime_config_from_option(config)
    runtime = build_runtime(cfg)
    session_repository = _runtime_session_repository(runtime)
    sessions = {session.session_id: session for session in session_repository.list_sessions()}
    session_id, manifest = _resolve_session_target(target, sessions)
    if tool not in manifest.allowed_tools:
        typer.echo(f"Tool permission {tool.value} not present on session {session_id}")
        return

    manifest.allowed_tools.remove(tool)
    session_repository.upsert_session(manifest)
    typer.echo(f"Revoked tool permission {tool.value} from session {session_id}")


@app.command(
    "attach-pr-feedback",
    rich_help_panel="Sessions and Feedback",
    short_help="Bind a PR feedback stream.",
)
def attach_pr_feedback(
    session_id: str,
    provider: Annotated[str, typer.Option("--provider", help="Feedback provider name.")],
    repo: Annotated[str, typer.Option("--repo", help="Provider repository identifier.")],
    pr_id: Annotated[str, typer.Option("--pr-id", help="Pull request identifier.")],
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
) -> None:
    cfg = _load_runtime_config_from_option(config)
    runtime = build_runtime(cfg)
    session_repository = _runtime_session_repository(runtime)
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
        ToolName.POST_PR_FEEDBACK_REPLY,
    ):
        if tool not in manifest.allowed_tools:
            manifest.allowed_tools.append(tool)

    session_repository.upsert_session(manifest)
    typer.echo(
        f"Attached PR feedback stream {stream['provider']}:{stream['repo']}:{stream['pr_id']} "
        f"to session {session_id}"
    )


@app.command(
    "approve",
    rich_help_panel="Sessions and Feedback",
    short_help="Approve or deny a request.",
)
def approve(
    request_id: str,
    approver: str,
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
    approve: Annotated[
        bool, typer.Option("--approve/--deny", help="Approve or deny the request.")
    ] = True,
    reason: Annotated[
        str, typer.Option("--reason", help="Operator-entered reason for the decision.")
    ] = "",
) -> None:
    service = AdminService(config)
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


@app.command(
    "list-approvals",
    rich_help_panel="Sessions and Feedback",
    short_help="List approval requests and queue health.",
)
def list_approvals(
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
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
    service = AdminService(config)
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
        age_seconds = int((datetime.now(UTC) - request.created_at).total_seconds())
        expires_in_seconds = int((request.expires_at - datetime.now(UTC)).total_seconds())
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


@app.command(
    "show-approval",
    rich_help_panel="Sessions and Feedback",
    short_help="Show approval request details for review.",
)
def show_approval(
    request_id: str,
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
) -> None:
    service = AdminService(config)
    try:
        payload = service.get_approval_review(request_id)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(json.dumps(payload, indent=2, sort_keys=True))


@app.command(
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
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
) -> None:
    if not recipient:
        raise typer.BadParameter("At least one --recipient value is required.")
    cfg = _load_runtime_config_from_option(config)
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


@app.command(
    "seed-discord-channel",
    rich_help_panel="Seeding and Fixtures",
    short_help="Insert a test Discord cursor.",
)
def seed_discord_channel(
    channel_id: str,
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
) -> None:
    cfg = _load_runtime_config_from_option(config)
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


@wake_schedules_app.command("create")
def wake_schedule_create(
    session_id: Annotated[str, typer.Option("--session-id", help="Target session id.")],
    prompt: Annotated[str, typer.Option("--prompt", help="Resume prompt.")],
    resume_at: Annotated[
        str | None, typer.Option("--resume-at", help="One-time UTC timestamp (ISO-8601).")
    ] = None,
    cron: Annotated[
        str | None, typer.Option("--cron", help="Recurring standard 5-field cron expression.")
    ] = None,
    mode: Annotated[
        str, typer.Option("--mode", help="Resume mode: same_context or new_context.")
    ] = "same_context",
    source: Annotated[
        str, typer.Option("--source", help="Schedule source tag for auditing.")
    ] = "admin_cli",
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
) -> None:
    if bool(resume_at) == bool(cron):
        raise typer.BadParameter("Provide exactly one of --resume-at or --cron.")
    service = AdminService(config)
    parsed_resume_at = _parse_utc_datetime(resume_at) if resume_at else None
    try:
        created = service.operations_admin.create_wake_schedule(
            session_id=session_id,
            prompt=prompt,
            resume_at=parsed_resume_at,
            cron=cron,
            resume_mode=mode,
            source=source,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(
        json.dumps(
            {
                "request_id": created.request_id,
                "session_id": created.session_id,
                "resume_at": created.schedule.resume_at.isoformat()
                if created.schedule.resume_at
                else None,
                "cron": created.schedule.cron,
                "resume_mode": created.resume_mode,
                "enabled": created.enabled,
                "next_run_at": created.next_run_at.isoformat() if created.next_run_at else None,
            },
            indent=2,
        )
    )


@wake_schedules_app.command("list")
def wake_schedule_list(
    session_id: Annotated[
        str | None, typer.Option("--session-id", help="Filter to a specific session id.")
    ] = None,
    include_disabled: Annotated[bool, typer.Option("--include-disabled/--enabled-only")] = True,
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
) -> None:
    service = AdminService(config)
    schedules = service.operations_admin.list_wake_schedules(
        session_id=session_id, include_disabled=include_disabled
    )
    typer.echo(json.dumps(schedules, indent=2, default=str))


@wake_schedules_app.command("edit")
def wake_schedule_edit(
    request_id: Annotated[str, typer.Argument(help="Wake schedule request id.")],
    prompt: Annotated[str | None, typer.Option("--prompt", help="Updated prompt text.")] = None,
    resume_at: Annotated[
        str | None, typer.Option("--resume-at", help="Updated one-time UTC timestamp (ISO-8601).")
    ] = None,
    cron: Annotated[
        str | None, typer.Option("--cron", help="Updated recurring standard 5-field cron expression.")
    ] = None,
    mode: Annotated[
        str | None, typer.Option("--mode", help="Updated mode: same_context or new_context.")
    ] = None,
    enabled: Annotated[
        bool | None, typer.Option("--enabled/--disabled", help="Override enabled state.")
    ] = None,
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
) -> None:
    service = AdminService(config)
    parsed_resume_at = _parse_utc_datetime(resume_at) if resume_at else None
    try:
        updated = service.operations_admin.edit_wake_schedule(
            request_id,
            prompt=prompt,
            resume_at=parsed_resume_at,
            cron=cron,
            resume_mode=mode,
            enabled=enabled,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(
        json.dumps(
            {
                "request_id": updated.request_id,
                "session_id": updated.session_id,
                "resume_at": updated.schedule.resume_at.isoformat()
                if updated.schedule.resume_at
                else None,
                "cron": updated.schedule.cron,
                "resume_mode": updated.resume_mode,
                "enabled": updated.enabled,
                "next_run_at": updated.next_run_at.isoformat() if updated.next_run_at else None,
            },
            indent=2,
        )
    )


@wake_schedules_app.command("delete")
def wake_schedule_delete(
    request_id: Annotated[str, typer.Argument(help="Wake schedule request id.")],
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
) -> None:
    service = AdminService(config)
    deleted = service.operations_admin.delete_wake_schedule(request_id)
    if not deleted:
        raise typer.BadParameter(f"Wake schedule not found: {request_id}")
    typer.echo(f"Deleted wake schedule {request_id}")


@app.command(
    "force-complete-await-agent",
    rich_help_panel="Background Workers",
    short_help="Force-complete a waiting await-agent dispatch using current VM state.",
)
def force_complete_await_agent(
    dispatch_id: str,
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
    log_level: Annotated[str, typer.Option("--log-level", help="Python logging level.")] = "INFO",
) -> None:
    logging_level = getattr(logging, log_level.upper(), None)
    if not isinstance(logging_level, int):
        raise typer.BadParameter(f"Invalid --log-level value: {log_level}")
    logging.basicConfig(
        level=logging_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    cfg = _load_runtime_config_from_option(config)
    worker = build_effect_orchestrator_worker(
        cfg, project_dir=_project_dir_for_config_option(config)
    )
    if not worker.force_complete_await_agent(dispatch_id):
        raise typer.BadParameter(f"Unable to force-complete await-agent dispatch: {dispatch_id}")
    typer.echo(f"Force-completed await-agent dispatch {dispatch_id}")


@app.command(
    "teams-ingest",
    rich_help_panel="Background Workers",
    short_help="Run the Teams ingest worker.",
)
def teams_ingest(
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
    run_once: Annotated[
        bool,
        typer.Option("--once", help="Run a single Teams ingest cycle and exit."),
    ] = False,
    log_level: Annotated[str, typer.Option("--log-level", help="Python logging level.")] = "INFO",
) -> None:
    logging_level = getattr(logging, log_level.upper(), None)
    if not isinstance(logging_level, int):
        raise typer.BadParameter(f"Invalid --log-level value: {log_level}")
    logging.basicConfig(
        level=logging_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    cfg = _load_runtime_config_from_option(config)
    worker = _build_teams_ingest_worker(cfg, project_dir=_project_dir_for_config_option(config))
    if run_once:
        result = worker.run_once_with_lock()
        typer.echo(
            f"Teams ingest worker cycle complete directories={result.directories_polled} "
            f"sessions={result.sessions_seen} "
            f"new_messages={result.new_messages_discovered} "
            f"trigger_wake_notified={result.trigger_wake_notified} "
            f"sleep_seconds={result.next_sleep_seconds:.3f}"
        )
        return

    worker.run_forever()


@app.command(
    "teams-meeting-ingest",
    rich_help_panel="Background Workers",
    short_help="Run the Teams meeting media ingest worker.",
)
def teams_meeting_ingest(
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
    run_once: Annotated[
        bool,
        typer.Option("--once", help="Run a single Teams meeting ingest cycle and exit."),
    ] = False,
    log_level: Annotated[str, typer.Option("--log-level", help="Python logging level.")] = "INFO",
) -> None:
    logging_level = getattr(logging, log_level.upper(), None)
    if not isinstance(logging_level, int):
        raise typer.BadParameter(f"Invalid --log-level value: {log_level}")
    logging.basicConfig(
        level=logging_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    cfg = _load_runtime_config_from_option(config)
    worker = _build_teams_meeting_ingest_worker(
        cfg, project_dir=_project_dir_for_config_option(config)
    )
    if run_once:
        result = worker.run_once_with_lock()
        typer.echo(
            f"Teams meeting ingest worker cycle complete meetings={result.meetings_polled} "
            f"events={result.events_ingested} errors={result.errors_recorded} "
            f"sleep_seconds={result.next_sleep_seconds:.3f}"
        )
        return

    worker.run_forever()


@app.command(
    "discord-wake",
    rich_help_panel="Background Workers",
    short_help="Run the Discord wake worker.",
)
def discord_wake(
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
    run_once: Annotated[
        bool,
        typer.Option("--once", help="Run a single Discord wake cycle and exit."),
    ] = False,
    log_level: Annotated[str, typer.Option("--log-level", help="Python logging level.")] = "INFO",
) -> None:
    logging_level = getattr(logging, log_level.upper(), None)
    if not isinstance(logging_level, int):
        raise typer.BadParameter(f"Invalid --log-level value: {log_level}")
    logging.basicConfig(
        level=logging_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    cfg = _load_runtime_config_from_option(config)
    worker = build_discord_wake_worker(cfg, project_dir=_project_dir_for_config_option(config))
    if run_once:
        result = worker.run_once_with_lock()
        typer.echo(
            f"Discord wake cycle complete channels={result.channels_polled} "
            f"pending={result.pending_channels} sleep_seconds={result.next_sleep_seconds:.3f}"
        )
        return

    worker.run_forever()


@app.command(
    "scheduled-wake",
    rich_help_panel="Background Workers",
    short_help="Run the scheduled wake worker.",
)
def scheduled_wake(
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
    run_once: Annotated[
        bool,
        typer.Option("--once", help="Run a single scheduled wake cycle and exit."),
    ] = False,
    log_level: Annotated[str, typer.Option("--log-level", help="Python logging level.")] = "INFO",
) -> None:
    logging_level = getattr(logging, log_level.upper(), None)
    if not isinstance(logging_level, int):
        raise typer.BadParameter(f"Invalid --log-level value: {log_level}")
    logging.basicConfig(
        level=logging_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    cfg = _load_runtime_config_from_option(config)
    worker = build_scheduled_wake_worker(cfg, project_dir=_project_dir_for_config_option(config))
    if run_once:
        result = worker.run_once_with_lock()
        typer.echo(
            f"Scheduled wake cycle complete due={result.requests_due} "
            f"dispatched={result.wakes_dispatched} sleep_seconds={result.next_sleep_seconds:.3f}"
        )
        return

    worker.run_forever()


@app.command(
    "trigger-orchestrator",
    rich_help_panel="Background Workers",
    short_help="Run the trigger orchestrator worker.",
)
def trigger_orchestrator(
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
    run_once: Annotated[
        bool,
        typer.Option("--once", help="Run a single trigger orchestrator cycle and exit."),
    ] = False,
    log_level: Annotated[str, typer.Option("--log-level", help="Python logging level.")] = "INFO",
) -> None:
    logging_level = getattr(logging, log_level.upper(), None)
    if not isinstance(logging_level, int):
        raise typer.BadParameter(f"Invalid --log-level value: {log_level}")
    logging.basicConfig(
        level=logging_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    cfg = _load_runtime_config_from_option(config)
    worker = build_trigger_orchestrator_worker(
        cfg, project_dir=_project_dir_for_config_option(config)
    )
    if run_once:
        result = worker.run_once_with_lock()
        typer.echo(
            f"Trigger orchestrator cycle complete triggers={result.configured_triggers} "
            f"discovered={result.events_discovered} published={result.events_published} "
            f"sleep_seconds={result.next_sleep_seconds:.3f}"
        )
        return

    worker.run_forever()


@app.command(
    "proxy-issue-sync",
    rich_help_panel="Background Workers",
    short_help="Sync local proxy issues from Azure DevOps.",
)
def proxy_issue_sync(
    config: ConfigOption = CLI_DEFAULT_RUNTIME_DIR,
    run_once: Annotated[
        bool,
        typer.Option("--once", help="Run a single proxy issue sync cycle and exit."),
    ] = False,
    force: Annotated[
        bool,
        typer.Option("--force", help="Sync records regardless of freshness."),
    ] = False,
    interval_seconds: Annotated[
        float,
        typer.Option(
            "--interval-seconds",
            help="Daemon polling interval in seconds.",
        ),
    ] = DEFAULT_PROXY_ISSUE_SYNC_INTERVAL_SECONDS,
    freshness_window_seconds: Annotated[
        float,
        typer.Option(
            "--freshness-window-seconds",
            help="Skip records synced more recently than this many seconds unless --force is set.",
        ),
    ] = DEFAULT_PROXY_ISSUE_SYNC_FRESHNESS_SECONDS,
    log_level: Annotated[str, typer.Option("--log-level", help="Python logging level.")] = "INFO",
) -> None:
    logging_level = getattr(logging, log_level.upper(), None)
    if not isinstance(logging_level, int):
        raise typer.BadParameter(f"Invalid --log-level value: {log_level}")
    logging.basicConfig(
        level=logging_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    runtime_dir, _legacy_config_path = _bootstrap_inputs(config)
    if run_once:
        result = run_proxy_issue_sync_once(
            runtime_dir=runtime_dir,
            freshness_window_seconds=freshness_window_seconds,
            force=force,
        )
        typer.echo(
            f"Proxy issue sync complete candidates={result.candidates} "
            f"skipped_fresh={result.skipped_fresh} updated={result.updated} "
            f"missing={result.missing} failed={result.failed}"
        )
        return

    run_proxy_issue_sync_worker(
        runtime_dir=runtime_dir,
        interval_seconds=interval_seconds,
        freshness_window_seconds=freshness_window_seconds,
        force=force,
    )


@app.command(
    "poll-ado-backlog",
    rich_help_panel="Background Workers",
    short_help="Poll Azure DevOps backlog.",
)
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
        typer.Option(
            "--forget", help="Forget a specific work item ID in the processed store, then exit."
        ),
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


if __name__ == "__main__":
    app()
CLI_DEFAULT_RUNTIME_DIR = Path("runtime")

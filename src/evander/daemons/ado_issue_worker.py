from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

import httpx
import yaml

from evander.bootstrap import load_runtime_gateway_config, resolve_project_dir
from evander.audit import AuditEventType, AuditLogger
from evander.azure_devops import resolve_azure_devops_organization_url
from evander.config import GatewayConfig, TriggerOrchestratorWorkerGlobalConfig
from evander.directories import DirectoryService, DirectoryStore
from evander.directory_config import (
    DirectoryConfigResolver,
    DirectoryConfigStore,
    get_domain_base_config,
    validate_effective_domain_payload,
)
from evander.events.models import TriggerEvent, materialize_node_definition
from evander.events.provider_models import (
    PullRequestCompletedTriggerDefinition,
    PullRequestNewFeedbackTriggerDefinition,
)
from evander.models import (
    DirectoryPath,
    LocalIssueRecord,
    PullRequestItem,
    PrFeedbackItem,
    RemoteIssueReference,
)
from evander.pr_tracking import (
    PrMapStore,
    completion_event_id,
    completion_event_payload,
    is_pr_completed,
    status_snapshot_from_pull_request,
)
from evander.pr_feedback.agent_directed import annotate_agent_directed_feedback
from evander.pr_feedback.providers import (
    AzureDevOpsFeedbackAdapter,
    pr_feedback_item_payload,
)
from evander.pr_feedback.store import FeedbackEnvelope, PrFeedbackStore, _stream_key
from evander.provider_config import ProviderConfigResolver
from evander.repositories.session_manifests import JsonSessionManifestRepository
from evander.orchestration.host_polling import notify_wake_socket_for_lock
from evander.services import AzureDevOpsPrQueryProvider, _normalize_ignored_feedback_payload
from evander.storage import JsonStore
from evander.triggers.providers._cursor_state import TriggerCursorStore
from evander.triggers.providers.pull_request_new_feedback import _parse_dt
from evander.triggers.queue import SqliteTriggerEventQueue, TriggerEventQueue
from evander.workflow.node_state import workflow_node_is_enabled
from evander.workflow.runtime_config import load_trigger_orchestrator_worker_global_config

_LOG = logging.getLogger(__name__)
_ADO_WORKER_CONFIG_RELATIVE_PATH = Path("config") / "ado-worker" / "config.json"
_MIN_ADO_DATETIME = datetime.min.replace(tzinfo=timezone.utc)
_ADO_PR_COMPLETED_DEFAULT_STATUSES = ("completed", "abandoned")


@dataclass(frozen=True)
class AdoWorkerRuntimeConfig:
    poll_interval_seconds: float
    directories: tuple[str, ...]
    runtime_dir: Path
    yaml_output_dir: Path
    processed_store_path: Path
    ado_work_items_azure_devops_pat_env: str
    fetch_retry_count: int
    fetch_retry_backoff_seconds: float
    include_comments: bool
    comments_max_count: int
    comment_text_max_chars: int

    @classmethod
    def load(
        cls, *, workspace_root: Path, legacy_env_runtime_dir: str | None = None
    ) -> AdoWorkerRuntimeConfig:
        raw_config = _load_json_object(workspace_root / _ADO_WORKER_CONFIG_RELATIVE_PATH)
        runtime_dir_default = Path(legacy_env_runtime_dir or "runtime").expanduser()
        if not runtime_dir_default.is_absolute():
            runtime_dir_default = workspace_root / runtime_dir_default
        runtime_dir = _resolve_path_setting(
            raw_config,
            workspace_root=workspace_root,
            json_keys=("runtime_dir", "ADO_RUNTIME_DIR"),
            env_name="ADO_RUNTIME_DIR",
            default=runtime_dir_default,
        )
        if runtime_dir is None:
            raise ValueError("runtime_dir must resolve to a non-empty path.")
        yaml_output_dir = _resolve_path_setting(
            raw_config,
            workspace_root=workspace_root,
            json_keys=("yaml_output_dir", "ADO_YAML_OUTPUT_DIR"),
            env_name="ADO_YAML_OUTPUT_DIR",
            default=runtime_dir / "ado-work-items",
        )
        directories = _resolve_string_list_setting(
            raw_config,
            json_keys=("directories", "ADO_DIRECTORIES"),
            env_name="ADO_DIRECTORIES",
            default=(),
        )
        processed_store_path = _resolve_path_setting(
            raw_config,
            workspace_root=workspace_root,
            json_keys=("processed_store_path", "ADO_PROCESSED_STORE_PATH"),
            env_name="ADO_PROCESSED_STORE_PATH",
            default=runtime_dir / "ado-processed-work-items.json",
        )
        ado_work_items_azure_devops_pat_env = _resolve_string_setting(
            raw_config,
            json_keys=(
                "ado_work_items_azure_devops_pat_env",
                "ADO_WORK_ITEMS_AZURE_DEVOPS_PAT_ENV",
            ),
            env_name="ADO_WORK_ITEMS_AZURE_DEVOPS_PAT_ENV",
            default="ADO_WORK_ITEMS_AZURE_DEVOPS_PAT",
        )
        fetch_retry_count = int(
            _resolve_numeric_setting(
                raw_config,
                json_keys=("fetch_retry_count", "ADO_FETCH_RETRY_COUNT"),
                env_name="ADO_FETCH_RETRY_COUNT",
                default=2,
                kind="int",
                minimum=0,
            )
        )
        fetch_retry_backoff_seconds = _resolve_numeric_setting(
            raw_config,
            json_keys=("fetch_retry_backoff_seconds", "ADO_FETCH_RETRY_BACKOFF_SECONDS"),
            env_name="ADO_FETCH_RETRY_BACKOFF_SECONDS",
            default=2.0,
            kind="float",
            minimum=0.0,
        )
        include_comments = _resolve_bool_setting(
            raw_config,
            json_keys=("include_comments", "ADO_INCLUDE_COMMENTS"),
            env_name="ADO_INCLUDE_COMMENTS",
            default=True,
        )
        comments_max_count = int(
            _resolve_numeric_setting(
                raw_config,
                json_keys=("comments_max_count", "ADO_COMMENTS_MAX_COUNT"),
                env_name="ADO_COMMENTS_MAX_COUNT",
                default=20,
                kind="int",
                minimum=1,
            )
        )
        comment_text_max_chars = int(
            _resolve_numeric_setting(
                raw_config,
                json_keys=("comment_text_max_chars", "ADO_COMMENT_TEXT_MAX_CHARS"),
                env_name="ADO_COMMENT_TEXT_MAX_CHARS",
                default=4000,
                kind="int",
                minimum=1,
            )
        )
        return cls(
            poll_interval_seconds=_resolve_numeric_setting(
                raw_config,
                json_keys=("poll_interval_seconds", "ADO_POLL_INTERVAL_SECONDS"),
                env_name="ADO_POLL_INTERVAL_SECONDS",
                default=30.0,
                kind="float",
                minimum=0.0,
            ),
            runtime_dir=runtime_dir,
            directories=directories,
            yaml_output_dir=yaml_output_dir,
            processed_store_path=processed_store_path,
            ado_work_items_azure_devops_pat_env=ado_work_items_azure_devops_pat_env,
            fetch_retry_count=fetch_retry_count,
            fetch_retry_backoff_seconds=fetch_retry_backoff_seconds,
            include_comments=include_comments,
            comments_max_count=comments_max_count,
            comment_text_max_chars=comment_text_max_chars,
        )


@dataclass(frozen=True)
class WorkerConfig:
    organization_url: str
    project: str
    pat_env_var_name: str
    poll_interval_seconds: float
    area_filters: tuple[str, ...]
    team_filters: tuple[str, ...]
    runtime_dir: Path
    yaml_output_dir: Path
    processed_store_path: Path
    repo_path: str | None
    fetch_retry_count: int
    fetch_retry_backoff_seconds: float
    include_comments: bool
    comments_max_count: int
    comment_text_max_chars: int
    directories: tuple[str, ...] = ()
    directory_path: str = "root"
    gateway_config_path: Path | None = None
    legacy_config_path: Path | None = None

    @classmethod
    def load_from_env(cls, *, require_runtime_secrets: bool = True) -> WorkerConfig:
        legacy_config_env = os.environ.get(
            "ADO_GATEWAY_CONFIG", os.environ.get("EVANDER_CONFIG", "")
        ).strip()
        legacy_config_path = Path(legacy_config_env).expanduser() if legacy_config_env else None
        workspace_root = resolve_project_dir(legacy_config_path=legacy_config_path)
        runtime_dir_env = os.environ.get("ADO_RUNTIME_DIR", "").strip() or None
        worker_runtime_config = AdoWorkerRuntimeConfig.load(
            workspace_root=workspace_root,
            legacy_env_runtime_dir=runtime_dir_env,
        )
        runtime_dir = worker_runtime_config.runtime_dir
        runtime_gateway_config = load_runtime_gateway_config(
            runtime_dir=runtime_dir,
            legacy_config_path=legacy_config_path,
        )
        initial_directory_path = (
            worker_runtime_config.directories[0] if worker_runtime_config.directories else "root"
        )
        return _resolve_worker_config_for_directory(
            worker_runtime_config=worker_runtime_config,
            runtime_gateway_config=runtime_gateway_config,
            directory_path=initial_directory_path,
            legacy_config_path=legacy_config_path,
            require_runtime_secrets=require_runtime_secrets,
        )


def _resolve_worker_config_for_directory(
    *,
    worker_runtime_config: AdoWorkerRuntimeConfig,
    runtime_gateway_config: GatewayConfig,
    directory_path: str,
    legacy_config_path: Path | None,
    require_runtime_secrets: bool = True,
) -> WorkerConfig:
    _ = require_runtime_secrets
    directory_store = DirectoryStore(runtime_gateway_config.directories_store_path)
    directory_store.ensure_root()
    directory_service = DirectoryService(directory_store)
    requested_directory_path = DirectoryPath(segments=directory_path).as_posix()
    try:
        normalized_directory_path = directory_service.show(requested_directory_path).directory.path
        resolution_directory_path = normalized_directory_path
    except ValueError:
        normalized_directory_path = requested_directory_path
        resolution_directory_path = "root"
    config_store = DirectoryConfigStore(
        directory_store, runtime_dir=runtime_gateway_config.runtime_dir
    )
    provider_config = ProviderConfigResolver(runtime_gateway_config).resolve_for_directory(
        resolution_directory_path
    )
    ado_worker_config = (
        DirectoryConfigResolver(
            directory_service,
            base_gateway_config=runtime_gateway_config,
            config_store=config_store,
        )
        .resolve_domain(
            resolution_directory_path,
            domain="ado_worker",
            base_config=get_domain_base_config(runtime_gateway_config, "ado_worker"),
            validator=lambda candidate: validate_effective_domain_payload("ado_worker", candidate),
        )
        .effective_config
    )
    organization_url = provider_config.azure_devops_organization_url
    project = provider_config.azure_devops_project
    pat_env_var_name = (
        worker_runtime_config.ado_work_items_azure_devops_pat_env.strip()
        or "ADO_WORK_ITEMS_AZURE_DEVOPS_PAT"
    )
    repo_path = _domain_optional_str_with_env_override(
        "ado_repo_source",
        ado_worker_config,
        env_name="ADO_REPO_PATH",
    )

    return WorkerConfig(
        organization_url=organization_url.rstrip("/"),
        project=project,
        pat_env_var_name=pat_env_var_name,
        poll_interval_seconds=worker_runtime_config.poll_interval_seconds,
        area_filters=_domain_csv_with_env_override(
            "ado_area_filters",
            ado_worker_config,
            env_name="ADO_AREA_FILTERS",
        ),
        team_filters=_domain_csv_with_env_override(
            "ado_team_filters",
            ado_worker_config,
            env_name="ADO_TEAM_FILTERS",
        ),
        runtime_dir=worker_runtime_config.runtime_dir,
        yaml_output_dir=worker_runtime_config.yaml_output_dir,
        processed_store_path=worker_runtime_config.processed_store_path,
        repo_path=repo_path,
        fetch_retry_count=worker_runtime_config.fetch_retry_count,
        fetch_retry_backoff_seconds=worker_runtime_config.fetch_retry_backoff_seconds,
        include_comments=worker_runtime_config.include_comments,
        comments_max_count=worker_runtime_config.comments_max_count,
        comment_text_max_chars=worker_runtime_config.comment_text_max_chars,
        directories=worker_runtime_config.directories,
        directory_path=normalized_directory_path,
        gateway_config_path=legacy_config_path,
        legacy_config_path=legacy_config_path,
    )


@dataclass(frozen=True)
class _ExplicitAdoPrCandidates:
    organization_url: str
    project: str
    pull_requests: list[PullRequestItem]


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"Environment variable {name} is required.")
    return value


def _load_json_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"JSON config file {path} must contain an object.")
    return raw


def _is_env_set(name: str) -> bool:
    return os.environ.get(name) is not None


def _log_deprecated_env_override(field_name: str, *env_names: str) -> None:
    _LOG.warning(
        "Deprecated ADO worker env override in use for %s via %s. "
        "Set the value in directory config domain 'ado_worker' instead.",
        field_name,
        ", ".join(env_names),
    )


def _first_json_value(raw_config: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key not in raw_config:
            continue
        value = raw_config[key]
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def _resolve_path_setting(
    raw_config: dict[str, Any],
    *,
    workspace_root: Path,
    json_keys: tuple[str, ...],
    env_name: str,
    default: Path,
) -> Path:
    value = _first_json_value(raw_config, json_keys)
    if value is None and _is_env_set(env_name):
        _log_deprecated_env_override(json_keys[0], env_name)
        value = _optional_env(env_name)
    if value is None:
        value = default
    if isinstance(value, Path):
        path = value
    elif isinstance(value, str):
        path = Path(value)
    else:
        raise ValueError(f"{json_keys[0]} must be a path string in the worker config.")
    path = path.expanduser()
    if not path.is_absolute():
        path = workspace_root / path
    return path


def _resolve_numeric_setting(
    raw_config: dict[str, Any],
    *,
    json_keys: tuple[str, ...],
    env_name: str,
    default: int | float,
    kind: str,
    minimum: int | float | None = None,
) -> int | float:
    value = _first_json_value(raw_config, json_keys)
    if value is None and _is_env_set(env_name):
        _log_deprecated_env_override(json_keys[0], env_name)
        value = os.environ.get(env_name)
    if value is None:
        value = default
    if isinstance(value, bool):
        raise ValueError(f"{json_keys[0]} must be numeric.")
    try:
        if kind == "int":
            if isinstance(value, float) and not value.is_integer():
                raise ValueError
            parsed: int | float = int(value)
        elif kind == "float":
            parsed = float(value)
        else:
            raise ValueError(f"Unsupported numeric kind: {kind}")
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{json_keys[0]} must be numeric.") from exc
    if minimum is not None and parsed < minimum:
        raise ValueError(f"{json_keys[0]} must be >= {minimum}.")
    return parsed


def _resolve_bool_setting(
    raw_config: dict[str, Any],
    *,
    json_keys: tuple[str, ...],
    env_name: str,
    default: bool,
) -> bool:
    value = _first_json_value(raw_config, json_keys)
    if value is None and _is_env_set(env_name):
        _log_deprecated_env_override(json_keys[0], env_name)
        return _bool_env(env_name, default)
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    raise ValueError(f"{json_keys[0]} must be a boolean value (true/false).")


def _resolve_string_setting(
    raw_config: dict[str, Any],
    *,
    json_keys: tuple[str, ...],
    env_name: str,
    default: str,
) -> str:
    value = _first_json_value(raw_config, json_keys)
    if value is None and _is_env_set(env_name):
        _log_deprecated_env_override(json_keys[0], env_name)
        value = _optional_env(env_name)
    if value is None:
        value = default
    if not isinstance(value, str):
        raise ValueError(f"{json_keys[0]} must be a string.")
    normalized = value.strip()
    return normalized or default


def _resolve_string_list_setting(
    raw_config: dict[str, Any],
    *,
    json_keys: tuple[str, ...],
    env_name: str,
    default: tuple[str, ...],
) -> tuple[str, ...]:
    value = _first_json_value(raw_config, json_keys)
    if value is None and _is_env_set(env_name):
        _log_deprecated_env_override(json_keys[0], env_name)
        value = _csv_env(env_name)
    if value is None:
        return default
    if isinstance(value, str):
        items = [part.strip() for part in value.replace(",", "\n").splitlines() if part.strip()]
        return tuple(DirectoryPath(segments=item).as_posix() for item in items)
    if isinstance(value, (list, tuple)):
        return tuple(
            DirectoryPath(segments=str(item).strip()).as_posix()
            for item in value
            if str(item).strip()
        )
    raise ValueError(f"{json_keys[0]} must be a list of strings.")


def _domain_csv_with_env_override(
    field_name: str, domain_config: dict[str, Any], *, env_name: str
) -> tuple[str, ...]:
    if _is_env_set(env_name):
        _log_deprecated_env_override(field_name, env_name)
        return _csv_env(env_name)
    value = domain_config.get(field_name, [])
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list in directory domain ado_worker.")
    return tuple(str(item) for item in value if str(item).strip())


def _domain_bool_with_env_override(
    field_name: str,
    domain_config: dict[str, Any],
    *,
    env_name: str | None,
    default: bool,
) -> bool:
    if env_name is not None and _is_env_set(env_name):
        _log_deprecated_env_override(field_name, env_name)
        return _bool_env(env_name, default)
    value = domain_config.get(field_name, default)
    if not isinstance(value, bool):
        raise ValueError(f"{field_name} must be a boolean in directory domain ado_worker.")
    return value


def _domain_optional_str_with_env_override(
    field_name: str,
    domain_config: dict[str, Any],
    *,
    env_name: str | None,
) -> str | None:
    if env_name is not None and _is_env_set(env_name):
        _log_deprecated_env_override(field_name, env_name)
        return _optional_env(env_name)
    value = domain_config.get(field_name)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string or null in directory domain ado_worker.")
    normalized = value.strip()
    return normalized or None


def _domain_str_with_env_override(
    field_name: str,
    domain_config: dict[str, Any],
    *,
    env_name: str,
    default: str,
    required: bool = False,
) -> str:
    if _is_env_set(env_name):
        _log_deprecated_env_override(field_name, env_name)
        value = _require_env(env_name) if required else (_optional_env(env_name) or default)
    else:
        raw = domain_config.get(field_name, default)
        if not isinstance(raw, str):
            raise ValueError(f"{field_name} must be a string in directory domain ado_worker.")
        value = raw.strip() or default
    if required and not value:
        raise ValueError(f"{field_name} must be set in directory domain ado_worker or {env_name}.")
    return value


def _optional_env(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


def _csv_env(name: str) -> tuple[str, ...]:
    raw = os.environ.get(name, "")
    values = [part.strip() for part in raw.split(",") if part.strip()]
    return tuple(values)


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be a boolean value (true/false).")


def _escape_wiql_string(value: str) -> str:
    return value.replace("'", "''")


def _build_wiql(config: WorkerConfig) -> str:
    clauses = [
        "[System.TeamProject] = @project",
        "[System.State] = 'Committed'",
        "[System.Tags] CONTAINS 'truman'",
    ]

    if config.area_filters:
        area_clause = " OR ".join(
            f"[System.AreaPath] UNDER '{_escape_wiql_string(area)}'" for area in config.area_filters
        )
        clauses.append(f"({area_clause})")

    if config.team_filters:
        team_clause = " OR ".join(
            f"[System.IterationPath] UNDER '{_escape_wiql_string(team)}'"
            for team in config.team_filters
        )
        clauses.append(f"({team_clause})")

    where_clause = " AND ".join(clauses)
    return (
        "SELECT [System.Id] "
        "FROM WorkItems "
        f"WHERE {where_clause} "
        "ORDER BY [System.ChangedDate] ASC, [System.Id] ASC"
    )


def _build_auth_header(pat: str) -> str:
    token = base64.b64encode(f":{pat}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


def _normalize_tags(raw_tags: str | None) -> set[str]:
    if not raw_tags:
        return set()
    return {tag.strip().lower() for tag in raw_tags.split(";") if tag.strip()}


def _combine_description_with_ado_sections(
    description: str,
    *,
    repro_steps: str = "",
    system_info: str = "",
    acceptance_criteria: str = "",
) -> str:
    parts: list[str] = []

    description = description.strip()
    if description:
        parts.append(description)

    for header, content in (
        ("Repro Steps", repro_steps),
        ("System Info", system_info),
        ("Acceptance Criteria", acceptance_criteria),
    ):
        normalized_content = content.strip()
        if normalized_content:
            parts.append(f"{header}\n\n{normalized_content}")

    return "\n\n".join(parts)


def _parse_ado_datetime(value: str | None) -> datetime:
    if not value:
        return _MIN_ADO_DATETIME

    normalized = value
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return _MIN_ADO_DATETIME

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class ProcessedWorkItemStore:
    """Atomic JSON state keyed by Azure work item id."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = Lock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("{}", encoding="utf-8")

    def is_processed(
        self,
        work_item_id: int | str,
        *,
        directory_path: str = "root",
        provider: str | None = None,
        organization: str | None = None,
        project: str | None = None,
    ) -> bool:
        with self._lock:
            payload = self._read_raw()
            return (
                _processed_work_item_key(
                    work_item_id,
                    directory_path,
                    provider=provider,
                    organization=organization,
                    project=project,
                )
                in payload
            )

    def mark_processed(
        self,
        work_item_id: int | str,
        yaml_path: Path,
        script_exit_code: int,
        output_snippet: str,
        *,
        directory_path: str = "root",
        provider: str | None = None,
        organization: str | None = None,
        project: str | None = None,
        issue_id: str | None = None,
    ) -> None:
        with self._lock:
            payload = self._read_raw()
            payload[
                _processed_work_item_key(
                    work_item_id,
                    directory_path,
                    provider=provider,
                    organization=organization,
                    project=project,
                )
            ] = {
                "processed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "yaml_path": str(yaml_path),
                "script_exit_code": script_exit_code,
                "script_output_snippet": output_snippet,
                "directory_path": directory_path,
                "provider": provider,
                "organization": organization,
                "project": project,
                "remote_work_item_id": str(work_item_id),
                "issue_id": issue_id,
            }
            self._atomic_write(payload)

    def forget(
        self,
        work_item_id: int | str,
        *,
        directory_path: str = "root",
        provider: str | None = None,
        organization: str | None = None,
        project: str | None = None,
    ) -> bool:
        with self._lock:
            payload = self._read_raw()
            existed = (
                payload.pop(
                    _processed_work_item_key(
                        work_item_id,
                        directory_path,
                        provider=provider,
                        organization=organization,
                        project=project,
                    ),
                    None,
                )
                is not None
            )
            self._atomic_write(payload)
            return existed

    def forget_all(self, work_item_id: int | str) -> int:
        normalized_work_item_id = str(work_item_id)
        with self._lock:
            payload = self._read_raw()
            removed_keys = [
                key
                for key, value in payload.items()
                if key == normalized_work_item_id
                or key.endswith(f"::{normalized_work_item_id}")
                or (
                    isinstance(value, dict)
                    and str(value.get("remote_work_item_id") or "").strip()
                    == normalized_work_item_id
                )
            ]
            if not removed_keys:
                return 0
            for key in removed_keys:
                payload.pop(key, None)
            self._atomic_write(payload)
            return len(removed_keys)

    def _read_raw(self) -> dict[str, Any]:
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _atomic_write(self, payload: dict[str, Any]) -> None:
        tmp_path = self.path.with_suffix(f"{self.path.suffix}.tmp")
        tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp_path.replace(self.path)


def fetch_candidate_ids(client: httpx.Client, config: WorkerConfig, auth_header: str) -> list[int]:
    wiql_url = f"{config.organization_url}/{config.project}/_apis/wit/wiql"
    response = client.post(
        wiql_url,
        params={"api-version": "7.1"},
        headers={"Authorization": auth_header},
        json={"query": _build_wiql(config)},
    )
    response.raise_for_status()
    payload = response.json()
    return [item["id"] for item in payload.get("workItems", []) if isinstance(item.get("id"), int)]


def fetch_work_item_batch(
    client: httpx.Client,
    config: WorkerConfig,
    auth_header: str,
    candidate_ids: list[int],
) -> list[dict[str, Any]]:
    if not candidate_ids:
        return []

    batch_url = f"{config.organization_url}/{config.project}/_apis/wit/workitemsbatch"
    response = client.post(
        batch_url,
        params={"api-version": "7.1"},
        headers={"Authorization": auth_header},
        json={
            "ids": candidate_ids,
            "fields": [
                "System.Id",
                "System.Title",
                "System.Description",
                "Microsoft.VSTS.TCM.ReproSteps",
                "Microsoft.VSTS.TCM.SystemInfo",
                "Microsoft.VSTS.Common.AcceptanceCriteria",
                "System.Tags",
                "System.State",
                "System.ChangedDate",
                "System.AreaPath",
                "System.IterationPath",
            ],
            "errorPolicy": "Omit",
        },
    )
    response.raise_for_status()
    return response.json().get("value", [])


def _normalize_comment_text(raw_text: Any, *, max_chars: int) -> str:
    if raw_text is None:
        return ""
    text = str(raw_text).strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars]


def fetch_work_item_comments(
    client: httpx.Client,
    config: WorkerConfig,
    auth_header: str,
    work_item_id: int,
) -> list[dict[str, str]]:
    comments_url = (
        f"{config.organization_url}/{config.project}/_apis/wit/workItems/{work_item_id}/comments"
    )
    response = client.get(
        comments_url,
        params={
            "api-version": "7.1-preview.4",
            "$top": str(config.comments_max_count),
            "order": "asc",
        },
        headers={"Authorization": auth_header},
    )
    response.raise_for_status()
    raw_comments = response.json().get("comments", [])

    normalized: list[dict[str, str]] = []
    for comment in raw_comments:
        text = _normalize_comment_text(
            comment.get("text", ""),
            max_chars=config.comment_text_max_chars,
        )
        if not text:
            continue
        revised_by = comment.get("revisedBy", {})
        normalized.append(
            {
                "id": str(comment.get("id", "")).strip(),
                "text": text,
                "author": str(revised_by.get("displayName", "")).strip(),
                "created_date": str(comment.get("createdDate", "")),
                "modified_date": str(comment.get("modifiedDate", "")),
                "url": str((comment.get("_links") or {}).get("web", {}).get("href", "")).strip(),
            }
        )
    return normalized


def _attach_comments_to_item(
    *,
    client: httpx.Client,
    config: WorkerConfig,
    auth_header: str,
    item: dict[str, Any],
) -> None:
    if not config.include_comments:
        return

    work_item_id = int(item["id"])
    try:
        item["comments"] = fetch_work_item_comments(
            client=client,
            config=config,
            auth_header=auth_header,
            work_item_id=work_item_id,
        )
    except httpx.HTTPError:
        logging.warning("work_item=%s comments_fetch_failed", work_item_id)
        item["comments"] = []


def gate_and_order_items(
    items: list[dict[str, Any]], config: WorkerConfig
) -> tuple[list[dict[str, Any]], int]:
    matched: list[dict[str, Any]] = []
    skipped = 0

    for item in items:
        fields = item.get("fields", {})
        state = str(fields.get("System.State", "")).strip()
        normalized_tags = _normalize_tags(fields.get("System.Tags"))

        if state != "Committed" or "truman" not in normalized_tags:
            skipped += 1
            continue

        area_path = str(fields.get("System.AreaPath", "")).strip()
        if config.area_filters and not any(
            area_path.startswith(prefix) for prefix in config.area_filters
        ):
            skipped += 1
            continue

        team_path = str(fields.get("System.IterationPath", "")).strip()
        if config.team_filters and not any(
            team_path.startswith(prefix) for prefix in config.team_filters
        ):
            skipped += 1
            continue

        work_item_id = item.get("id")
        if not isinstance(work_item_id, int):
            skipped += 1
            continue

        matched.append(
            {
                "id": work_item_id,
                "title": str(fields.get("System.Title", "")).strip(),
                "description": _combine_description_with_ado_sections(
                    str(fields.get("System.Description", "") or ""),
                    repro_steps=str(fields.get("Microsoft.VSTS.TCM.ReproSteps", "") or ""),
                    system_info=str(fields.get("Microsoft.VSTS.TCM.SystemInfo", "") or ""),
                    acceptance_criteria=str(
                        fields.get("Microsoft.VSTS.Common.AcceptanceCriteria", "") or ""
                    ),
                ),
                "tags": sorted(normalized_tags),
                "state": state,
                "url": item.get("url", ""),
                "updated_date": str(fields.get("System.ChangedDate", "")),
            }
        )

    matched.sort(
        key=lambda work_item: (_parse_ado_datetime(work_item["updated_date"]), work_item["id"])
    )
    return matched, skipped


def poll_once(
    client: httpx.Client,
    config: WorkerConfig,
    auth_header: str,
    *,
    store: ProcessedWorkItemStore | None = None,
) -> list[dict[str, Any]]:
    candidate_ids = fetch_candidate_ids(client=client, config=config, auth_header=auth_header)
    fetched_items = fetch_work_item_batch(
        client=client,
        config=config,
        auth_header=auth_header,
        candidate_ids=candidate_ids,
    )
    matched_items, skipped_count = gate_and_order_items(fetched_items, config)
    processed_skip_count = 0
    if store is not None:
        refresh_items: list[dict[str, Any]] = []
        for item in matched_items:
            if _work_item_needs_ingest_or_refresh(item, config=config, store=store):
                refresh_items.append(item)
            else:
                processed_skip_count += 1
        matched_items = refresh_items
    for item in matched_items:
        _attach_comments_to_item(
            client=client,
            config=config,
            auth_header=auth_header,
            item=item,
        )

    logging.info(
        "cycle polled=%d matched=%d skipped=%d skipped_processed=%d",
        len(candidate_ids),
        len(matched_items),
        skipped_count,
        processed_skip_count,
    )
    return matched_items


def filter_unprocessed_work_items(
    items: list[dict[str, Any]],
    store: ProcessedWorkItemStore,
    *,
    config: WorkerConfig,
) -> list[dict[str, Any]]:
    return [
        item
        for item in items
        if not store.is_processed(
            int(item["id"]),
            directory_path=config.directory_path,
            provider="azure_devops",
            organization=config.organization_url,
            project=config.project,
        )
    ]


def format_work_item_listing(work_item: dict[str, Any]) -> str:
    return "\t".join(
        [
            str(work_item["id"]),
            work_item.get("updated_date", ""),
            work_item.get("state", ""),
            work_item.get("title", ""),
        ]
    )


def list_unprocessed_work_items(
    client: httpx.Client,
    config: WorkerConfig,
    auth_header: str,
    store: ProcessedWorkItemStore,
) -> list[dict[str, Any]]:
    items = poll_once(client=client, config=config, auth_header=auth_header)
    return filter_unprocessed_work_items(items, store, config=config)


def _resolve_directory_worker_config(
    config: WorkerConfig,
    runtime_gateway_config: GatewayConfig,
    directory_path: str,
) -> WorkerConfig:
    worker_runtime_config = AdoWorkerRuntimeConfig(
        poll_interval_seconds=config.poll_interval_seconds,
        directories=config.directories,
        runtime_dir=config.runtime_dir,
        yaml_output_dir=config.yaml_output_dir,
        processed_store_path=config.processed_store_path,
        ado_work_items_azure_devops_pat_env=config.pat_env_var_name,
        fetch_retry_count=config.fetch_retry_count,
        fetch_retry_backoff_seconds=config.fetch_retry_backoff_seconds,
        include_comments=config.include_comments,
        comments_max_count=config.comments_max_count,
        comment_text_max_chars=config.comment_text_max_chars,
    )
    return _resolve_worker_config_for_directory(
        worker_runtime_config=worker_runtime_config,
        runtime_gateway_config=runtime_gateway_config,
        directory_path=directory_path,
        legacy_config_path=config.legacy_config_path or config.gateway_config_path,
    )


def _poll_and_process_directory(
    *,
    client: httpx.Client,
    config: WorkerConfig,
    auth_header: str,
    store: ProcessedWorkItemStore,
    runtime_gateway_config: GatewayConfig,
    directory_path: str,
) -> None:
    directory_config = _resolve_directory_worker_config(
        config, runtime_gateway_config, directory_path
    )
    attempts = 0
    while True:
        try:
            items = poll_once(
                client=client,
                config=directory_config,
                auth_header=auth_header,
                store=store,
            )
            break
        except httpx.HTTPError as exc:
            attempts += 1
            if attempts > directory_config.fetch_retry_count or not _is_transient_fetch_error(exc):
                raise
            sleep_seconds = directory_config.fetch_retry_backoff_seconds * attempts
            logging.warning(
                "cycle directory=%s transient_fetch_error=%s retry=%d/%d sleep=%.1fs",
                directory_path,
                exc.__class__.__name__,
                attempts,
                directory_config.fetch_retry_count,
                sleep_seconds,
            )
            time.sleep(sleep_seconds)

    for item in items:
        process_work_item(
            item,
            config=directory_config,
            store=store,
        )


def _unprocessed_items_for_worker(
    *,
    client: httpx.Client,
    config: WorkerConfig,
    auth_header: str,
    store: ProcessedWorkItemStore,
    runtime_gateway_config: GatewayConfig,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for directory_path in config.directories:
        try:
            directory_config = _resolve_directory_worker_config(
                config, runtime_gateway_config, directory_path
            )
        except Exception as exc:
            logging.warning(
                "list-unprocessed directory=%s config_resolution_failed error=%s",
                directory_path,
                exc,
            )
            continue
        items.extend(
            list_unprocessed_work_items(
                client=client, config=directory_config, auth_header=auth_header, store=store
            )
        )
    return items


def _forget_work_item_for_worker(
    store: ProcessedWorkItemStore, config: WorkerConfig, work_item_id: int
) -> bool:
    removed = False
    directory_paths = config.directories or (config.directory_path,)
    for directory_path in directory_paths:
        removed = (
            store.forget(
                work_item_id,
                directory_path=directory_path,
                provider="azure_devops",
                organization=config.organization_url,
                project=config.project,
            )
            or removed
        )
        removed = store.forget(work_item_id, directory_path=directory_path) or removed
    return removed


def _build_proxy_issue_id(config: WorkerConfig, work_item_id: int, directory_path: str) -> str:
    return _processed_work_item_key(
        work_item_id,
        directory_path,
        provider="azure_devops",
        organization=config.organization_url,
        project=config.project,
    )


def _find_unified_issue_record(
    *, config: WorkerConfig, work_item_id: int
) -> tuple[str | None, LocalIssueRecord | None]:
    store_path = config.runtime_dir / "local-issues.json"
    if not store_path.exists():
        return None, None
    store = JsonStore(store_path, LocalIssueRecord)
    normalized_directory_path = DirectoryPath(segments=config.directory_path).as_posix()
    for issue_id, record in store.read_all().items():
        remote = record.remote_reference
        if (
            record.directory_path == normalized_directory_path
            and remote is not None
            and remote.provider == "azure_devops"
            and (remote.organization or "").rstrip("/") == config.organization_url.rstrip("/")
            and (remote.project or "") == config.project
            and (remote.work_item_id or "") == str(work_item_id)
        ):
            return issue_id, record
    return None, None


def _upsert_unified_issue_record(
    *,
    config: WorkerConfig,
    work_item: dict[str, Any],
    yaml_path: Path,
) -> LocalIssueRecord:
    store = JsonStore(config.runtime_dir / "local-issues.json", LocalIssueRecord)
    work_item_id = int(work_item["id"])
    normalized_directory_path = DirectoryPath(segments=config.directory_path).as_posix()
    now = datetime.now(timezone.utc)
    existing_key, existing_record = _find_unified_issue_record(
        config=config, work_item_id=work_item_id
    )
    issue_id = existing_key or _build_proxy_issue_id(
        config, work_item_id, normalized_directory_path
    )
    issue_number = str(work_item_id)
    description = str(work_item.get("description", "") or "")
    metadata = _preserved_local_issue_metadata(existing_record=existing_record, yaml_path=yaml_path)
    metadata["proxy_cache"] = _proxy_cache_from_work_item(work_item)

    managed_repo_id = existing_record.managed_repo_id if existing_record is not None else None
    managed_git_admin = None
    if config.repo_path:
        from evander.admin.composition import build_admin_runtime

        managed_git_admin = build_admin_runtime(runtime_dir=config.runtime_dir).managed_git_admin
        managed_record = managed_git_admin.register_repository(
            directory_path=normalized_directory_path,
            repo_source=config.repo_path,
        )
        managed_repo_id = str(managed_record["managed_repo_id"])

    remote_reference = RemoteIssueReference(
        provider="azure_devops",
        organization=config.organization_url.rstrip("/"),
        project=config.project,
        work_item_id=issue_number,
        work_item_url=str(work_item.get("url", "") or "") or None,
        last_synced_at=now,
        last_remote_updated_at=_parse_ado_datetime(str(work_item.get("updated_date", "") or "")),
        last_known_remote_state=str(work_item.get("state", "") or "") or None,
    )

    record = LocalIssueRecord(
        issue_id=issue_id,
        issue_number=issue_number,
        title=str(work_item.get("title", "") or "").strip() or f"ADO Work Item {issue_number}",
        body=description,
        description=description,
        yaml_path=str(yaml_path),
        directory_path=normalized_directory_path,
        managed_repo_id=managed_repo_id,
        source_type="remote_proxy",
        remote_reference=remote_reference,
        tags=[str(tag) for tag in work_item.get("tags", []) if str(tag).strip()],
        state=str(work_item.get("state", "") or "") or None,
        url=str(work_item.get("url", "") or "") or None,
        comments=[
            dict(comment) for comment in work_item.get("comments", []) if isinstance(comment, dict)
        ],
        source="ado_worker",
        metadata=metadata,
        created_at=existing_record.created_at if existing_record is not None else now,
        updated_at=now,
    )
    store.put(record.issue_id, record)
    if managed_git_admin is not None and managed_repo_id is not None:
        managed_git_admin.unbind_all(kind="issues", binding_id=record.issue_id)
        managed_git_admin.bind(managed_repo_id, kind="issues", binding_id=record.issue_id)
    return record


def _proxy_cache_from_work_item(work_item: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": str(work_item.get("title", "") or ""),
        "description": str(work_item.get("description", "") or ""),
        "state": str(work_item.get("state", "") or ""),
        "url": str(work_item.get("url", "") or ""),
        "updated_at": str(work_item.get("updated_date", "") or ""),
        "tags": [str(tag) for tag in work_item.get("tags", []) if str(tag).strip()],
        "comments": [
            dict(comment) for comment in work_item.get("comments", []) if isinstance(comment, dict)
        ],
    }


def _build_issue_payload(record: LocalIssueRecord) -> dict[str, Any]:
    remote = record.remote_reference
    payload = {
        "issue_number": int(record.issue_number)
        if record.issue_number.isdigit()
        else record.issue_number,
        "issue_id": record.issue_id,
        "title": record.title,
        "description": record.description or record.body,
        "tags": record.tags,
        "state": record.state or "",
        "url": (remote.work_item_url if remote is not None else None) or record.url or "",
        "updated_date": (
            remote.last_remote_updated_at.isoformat().replace("+00:00", "Z")
            if remote is not None and remote.last_remote_updated_at is not None
            else ""
        ),
        "comments": record.comments,
        "directory_path": record.directory_path,
        "remote_reference": remote.model_dump(mode="json") if remote is not None else None,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    metadata_steps = record.metadata.get("steps") if isinstance(record.metadata, dict) else None
    if isinstance(metadata_steps, list) and all(isinstance(step, str) for step in metadata_steps):
        payload["steps"] = list(metadata_steps)
    metadata_plan_summary = (
        record.metadata.get("plan_summary") if isinstance(record.metadata, dict) else None
    )
    if isinstance(metadata_plan_summary, str) and metadata_plan_summary.strip():
        payload["plan_summary"] = metadata_plan_summary
    return payload


def _preserved_local_issue_metadata(
    *,
    existing_record: LocalIssueRecord | None,
    yaml_path: Path,
) -> dict[str, Any]:
    metadata = dict(existing_record.metadata) if existing_record is not None else {}
    if not yaml_path.exists():
        return metadata
    try:
        loaded = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    except Exception:
        return metadata
    if not isinstance(loaded, dict):
        return metadata
    yaml_steps = loaded.get("steps")
    if (
        "steps" not in metadata
        and isinstance(yaml_steps, list)
        and all(isinstance(step, str) for step in yaml_steps)
    ):
        metadata["steps"] = list(yaml_steps)
    yaml_plan_summary = loaded.get("plan_summary")
    if (
        "plan_summary" not in metadata
        and isinstance(yaml_plan_summary, str)
        and yaml_plan_summary.strip()
    ):
        metadata["plan_summary"] = yaml_plan_summary
    return metadata


def _persist_unified_issue_record(
    config: WorkerConfig, record: LocalIssueRecord, yaml_path: Path
) -> None:
    store = JsonStore(config.runtime_dir / "local-issues.json", LocalIssueRecord)
    store.put(record.issue_id, record)
    _atomic_write_yaml(yaml_path, _build_issue_payload(record))


def _atomic_write_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(f"{path.suffix}.tmp")
    tmp_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    tmp_path.replace(path)


def _processed_work_item_key(
    work_item_id: int | str,
    directory_path: str,
    *,
    provider: str | None = None,
    organization: str | None = None,
    project: str | None = None,
) -> str:
    if provider and organization and project:
        return (
            f"{DirectoryPath(segments=directory_path).as_posix()}::"
            f"{provider}::{organization.rstrip('/')}::{project}::{work_item_id}"
        )
    if directory_path == "root":
        return str(work_item_id)
    return f"{directory_path}::{work_item_id}"


def _normalize_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _processed_work_item_refresh_decision(
    *, work_item: dict[str, Any], config: WorkerConfig
) -> tuple[bool, LocalIssueRecord | None]:
    work_item_id = int(work_item["id"])
    _existing_key, existing_record = _find_unified_issue_record(
        config=config, work_item_id=work_item_id
    )
    if existing_record is None:
        return True, None

    incoming_changed_at = _parse_ado_datetime(str(work_item.get("updated_date", "") or ""))
    remote = existing_record.remote_reference
    existing_changed_at = remote.last_remote_updated_at if remote is not None else None
    if existing_changed_at is None:
        return incoming_changed_at > _MIN_ADO_DATETIME, existing_record
    return incoming_changed_at > _normalize_datetime(existing_changed_at), existing_record


def _work_item_needs_ingest_or_refresh(
    work_item: dict[str, Any],
    *,
    config: WorkerConfig,
    store: ProcessedWorkItemStore,
) -> bool:
    work_item_id = int(work_item["id"])
    if not store.is_processed(
        work_item_id,
        directory_path=config.directory_path,
        provider="azure_devops",
        organization=config.organization_url,
        project=config.project,
    ):
        return True

    should_refresh, existing_record = _processed_work_item_refresh_decision(
        work_item=work_item, config=config
    )
    if not should_refresh:
        issue_id = existing_record.issue_id if existing_record is not None else ""
        logging.info("work_item=%s skipped=already_processed issue_id=%s", work_item_id, issue_id)
    return should_refresh


def _worker_runtime_config(config: WorkerConfig) -> GatewayConfig:
    legacy_config_path = config.legacy_config_path or config.gateway_config_path
    return load_runtime_gateway_config(
        runtime_dir=config.runtime_dir, legacy_config_path=legacy_config_path
    )


def _is_transient_fetch_error(exc: Exception) -> bool:
    if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None:
        return exc.response.status_code in {408, 429, 500, 502, 503, 504}
    return False


def process_work_item(
    work_item: dict[str, Any],
    *,
    config: WorkerConfig,
    store: ProcessedWorkItemStore,
) -> bool:
    work_item_id = int(work_item["id"])
    already_processed = store.is_processed(
        work_item_id,
        directory_path=config.directory_path,
        provider="azure_devops",
        organization=config.organization_url,
        project=config.project,
    )
    if already_processed:
        should_refresh, existing_record = _processed_work_item_refresh_decision(
            work_item=work_item, config=config
        )
        if not should_refresh:
            issue_id = existing_record.issue_id if existing_record is not None else ""
            logging.info(
                "work_item=%s skipped=already_processed issue_id=%s", work_item_id, issue_id
            )
            return False

    yaml_path = config.yaml_output_dir / f"work-item-{work_item_id}.yaml"
    record = _upsert_unified_issue_record(config=config, work_item=work_item, yaml_path=yaml_path)
    _persist_unified_issue_record(config, record, yaml_path)

    if already_processed:
        logging.info(
            "work_item=%s refreshed=%s issue_id=%s",
            work_item_id,
            "missing_local_issue" if existing_record is None else "remote_changed",
            record.issue_id,
        )
        return False

    store.mark_processed(
        work_item_id=work_item_id,
        yaml_path=yaml_path,
        script_exit_code=0,
        output_snippet="ADO work item ingested; session creation is handled by workflows/effects.",
        directory_path=config.directory_path,
        provider="azure_devops",
        organization=config.organization_url,
        project=config.project,
        issue_id=record.issue_id,
    )
    logging.info("work_item=%s processed yaml=%s", work_item_id, yaml_path)
    return True


def fetch_work_item_by_id(
    client: httpx.Client,
    config: WorkerConfig,
    auth_header: str,
    work_item_id: int,
) -> dict[str, Any] | None:
    items = fetch_work_item_batch(client, config, auth_header, [work_item_id])
    matched, _ = gate_and_order_items(items, config)
    if not matched:
        return None
    item = matched[0]
    _attach_comments_to_item(
        client=client,
        config=config,
        auth_header=auth_header,
        item=item,
    )
    return item


def _processed_store_for_config(config: WorkerConfig) -> ProcessedWorkItemStore:
    return ProcessedWorkItemStore(config.processed_store_path)


def _processed_store_for_runtime() -> ProcessedWorkItemStore:
    legacy_config_env = os.environ.get(
        "ADO_GATEWAY_CONFIG", os.environ.get("EVANDER_CONFIG", "")
    ).strip()
    legacy_config_path = Path(legacy_config_env).expanduser() if legacy_config_env else None
    workspace_root = resolve_project_dir(legacy_config_path=legacy_config_path)
    runtime_dir_env = os.environ.get("ADO_RUNTIME_DIR", "").strip() or None
    worker_runtime_config = AdoWorkerRuntimeConfig.load(
        workspace_root=workspace_root,
        legacy_env_runtime_dir=runtime_dir_env,
    )
    return ProcessedWorkItemStore(worker_runtime_config.processed_store_path)


def _trigger_orchestrator_config_path(config: WorkerConfig) -> Path:
    legacy_config_path = config.legacy_config_path or config.gateway_config_path
    if legacy_config_path is not None:
        project_dir = resolve_project_dir(legacy_config_path=legacy_config_path)
    else:
        project_dir = config.runtime_dir.parent
    return project_dir / "config" / "trigger-orchestrator" / "config.json"


def _load_ado_pr_feedback_triggers(
    config: WorkerConfig,
) -> list[PullRequestNewFeedbackTriggerDefinition]:
    worker_config = _load_trigger_orchestrator_worker_config(config)
    if worker_config is None:
        return []
    triggers: list[PullRequestNewFeedbackTriggerDefinition] = []
    for trigger in worker_config.triggers:
        if not workflow_node_is_enabled(trigger):
            continue
        materialized = materialize_node_definition(trigger)
        if (
            isinstance(materialized, PullRequestNewFeedbackTriggerDefinition)
            and materialized.provider == "ado"
        ):
            triggers.append(materialized)
    return triggers


def _load_ado_pr_completed_triggers(
    config: WorkerConfig,
) -> list[PullRequestCompletedTriggerDefinition]:
    worker_config = _load_trigger_orchestrator_worker_config(config)
    if worker_config is None:
        return []
    triggers: list[PullRequestCompletedTriggerDefinition] = []
    for trigger in worker_config.triggers:
        if not workflow_node_is_enabled(trigger):
            continue
        materialized = materialize_node_definition(trigger)
        if (
            isinstance(materialized, PullRequestCompletedTriggerDefinition)
            and materialized.provider == "ado"
        ):
            triggers.append(materialized)
    return triggers


def _load_trigger_orchestrator_worker_config(
    config: WorkerConfig,
) -> TriggerOrchestratorWorkerGlobalConfig | None:
    config_path = _trigger_orchestrator_config_path(config)
    if not config_path.exists():
        return None
    return load_trigger_orchestrator_worker_global_config(
        config_path.parents[2],
        base_config=GatewayConfig(runtime_dir=config.runtime_dir),
    )


def _build_trigger_event_queue_for_worker_config(
    config: WorkerConfig,
    worker_config: TriggerOrchestratorWorkerGlobalConfig | None,
):
    if (
        worker_config is not None
        and worker_config.orchestration_store_backend == "sqlite"
        and "orchestration_store_backend" in worker_config.model_fields_set
    ):
        return SqliteTriggerEventQueue(
            worker_config.orchestration_store_path,
            retention_days=worker_config.orchestration_retention_days,
        )
    return TriggerEventQueue(
        (worker_config.event_queue_store_path if worker_config is not None else None)
        or config.runtime_dir / "trigger-orchestrator-events.json",
        journal_path=(
            worker_config.workflow_event_journal_path if worker_config is not None else None
        ),
    )


def _normalize_optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _trigger_has_explicit_pr_criteria(
    trigger: PullRequestNewFeedbackTriggerDefinition | PullRequestCompletedTriggerDefinition,
) -> bool:
    return _normalize_optional_text(trigger.repo) is not None


def _explicit_ado_trigger_pull_request_statuses(
    trigger: PullRequestNewFeedbackTriggerDefinition | PullRequestCompletedTriggerDefinition,
) -> tuple[str | None, ...]:
    status = _normalize_optional_text(trigger.status)
    if status:
        return (status,)
    if trigger.type == "trigger:pull-request:completed":
        return _ADO_PR_COMPLETED_DEFAULT_STATUSES
    return (None,)


def _trigger_effective_directory_path(
    trigger: PullRequestNewFeedbackTriggerDefinition | PullRequestCompletedTriggerDefinition,
    current_directory_path: str,
) -> str:
    return (
        _normalize_optional_text(trigger.directory_path)
        or DirectoryPath(segments=current_directory_path).as_posix()
    )


def _ado_organization_url_for_trigger(
    *,
    trigger: PullRequestNewFeedbackTriggerDefinition | PullRequestCompletedTriggerDefinition,
    legacy_organization_url: str | None,
) -> str:
    return resolve_azure_devops_organization_url(
        ado_organization=trigger.ado_organization,
        legacy_organization_url=legacy_organization_url,
        node_type=trigger.type,
    )


def _list_explicit_ado_trigger_pull_requests(
    *,
    trigger: PullRequestNewFeedbackTriggerDefinition | PullRequestCompletedTriggerDefinition,
    config: WorkerConfig,
    runtime_gateway_config: GatewayConfig,
    current_directory_path: str,
) -> _ExplicitAdoPrCandidates:
    normalized_current_directory_path = DirectoryPath(segments=current_directory_path).as_posix()
    trigger_directory_path = _normalize_optional_text(trigger.directory_path)
    if trigger_directory_path is not None:
        effective_directory_path = DirectoryPath(segments=trigger_directory_path).as_posix()
        if effective_directory_path != normalized_current_directory_path:
            _LOG.debug(
                "Skipping Azure PR polling for trigger_id=%s directory=%s current_directory=%s",
                trigger.id,
                effective_directory_path,
                normalized_current_directory_path,
            )
            return _ExplicitAdoPrCandidates(
                organization_url="",
                project="",
                pull_requests=[],
            )
    effective_directory_path = trigger_directory_path or normalized_current_directory_path
    directory_config = _resolve_directory_worker_config(
        config, runtime_gateway_config, effective_directory_path
    )
    organization_url = _ado_organization_url_for_trigger(
        trigger=trigger,
        legacy_organization_url=(
            _normalize_optional_text(directory_config.organization_url)
            or _normalize_optional_text(config.organization_url)
        ),
    )
    pat_env_name = runtime_gateway_config.azure_devops_pr_pat_env
    pat = os.environ.get(pat_env_name, "").strip()
    if not pat:
        _LOG.debug(
            "Skipping Azure PR polling for directory=%s because %s is unset",
            effective_directory_path,
            pat_env_name,
        )
        return _ExplicitAdoPrCandidates(
            organization_url="",
            project="",
            pull_requests=[],
        )

    repo = _normalize_optional_text(trigger.repo)
    if not repo:
        return _ExplicitAdoPrCandidates(
            organization_url="",
            project="",
            pull_requests=[],
        )

    project = (
        _normalize_optional_text(trigger.project)
        or _normalize_optional_text(directory_config.project)
        or _normalize_optional_text(config.project)
    )
    provider = AzureDevOpsPrQueryProvider(
        organization_url=organization_url,
        project=project or "",
        pat=pat,
        timeout_seconds=30.0,
    )
    pull_requests_by_key: dict[tuple[str, str, str], PullRequestItem] = {}
    for status in _explicit_ado_trigger_pull_request_statuses(trigger):
        response = provider.list_pull_requests(
            repo=repo,
            project=project,
            status=status,
            creator_id=trigger.creator_id,
            reviewer_id=trigger.reviewer_id,
            source_ref_name=trigger.source_ref_name,
            target_ref_name=trigger.target_ref_name,
            source_repository_id=trigger.source_repository_id,
            repository_id=trigger.repository_id,
            min_time=trigger.min_time,
            max_time=trigger.max_time,
            query_time_range_type=trigger.query_time_range_type,
            include_links=trigger.include_links,
            max_comment_length=trigger.max_comment_length,
            tags=trigger.tags,
            top=trigger.top,
            skip=trigger.skip,
            cursor=trigger.cursor,
        )
        for pull_request in response.pull_requests:
            pull_requests_by_key[(pull_request.provider, pull_request.repo, pull_request.pr_id)] = (
                pull_request
            )
    return _ExplicitAdoPrCandidates(
        organization_url=organization_url,
        project=project or "",
        pull_requests=list(pull_requests_by_key.values()),
    )


def _effect_lock_path_for_worker_config(
    config: WorkerConfig,
    worker_config: TriggerOrchestratorWorkerGlobalConfig | None,
) -> Path:
    if worker_config is not None:
        return worker_config.effect_lock_path
    return config.runtime_dir / "effect-orchestrator.lock"


def _load_ado_pr_feedback_session_bindings(
    *,
    session_store_path: Path,
    pr_map_path: Path,
    directory_path: str,
) -> dict[tuple[str, str], dict[str, str]]:
    if not pr_map_path.exists():
        return {}
    try:
        payload = json.loads(pr_map_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    entries = payload.get("entries")
    if not isinstance(entries, dict):
        return {}

    session_directory_paths: dict[str, str] = {}
    session_vm_ids: dict[str, str] = {}
    session_repository = JsonSessionManifestRepository(session_store_path)
    session_ado_projects: dict[str, str] = {}
    for manifest in session_repository.list_sessions():
        normalized_session_id = str(manifest.session_id).strip()
        if not normalized_session_id:
            continue
        normalized_directory_path = str(manifest.directory_path or "root").strip() or "root"
        normalized_vm_id = str(manifest.vm_id or "").strip()
        session_directory_paths[normalized_session_id] = normalized_directory_path
        if normalized_vm_id:
            session_vm_ids[normalized_session_id] = normalized_vm_id
        metadata = getattr(manifest, "metadata", None)
        if isinstance(metadata, dict):
            normalized_ado_project = str(metadata.get("ado_project", "")).strip()
            if normalized_ado_project:
                session_ado_projects[normalized_session_id] = normalized_ado_project

    bindings: dict[tuple[str, str], dict[str, str]] = {}
    for raw in entries.values():
        if not isinstance(raw, dict):
            continue
        provider = str(raw.get("provider", "")).strip()
        repo = str(raw.get("repo", "")).strip()
        pr_id = str(raw.get("pr_id", "")).strip()
        session_id = str(raw.get("session_id", "")).strip()
        mapped_directory_path = (
            str(raw.get("directory_path", "")).strip()
            or session_directory_paths.get(session_id)
            or "root"
        )
        if provider != "azure_devops" or not repo or not pr_id:
            continue
        if directory_path and mapped_directory_path != directory_path:
            continue
        bindings[(repo, pr_id)] = {
            "provider": provider,
            "repo": repo,
            "pr_id": pr_id,
            "session_id": session_id,
            "vm_id": session_vm_ids.get(session_id, ""),
            "ado_project": session_ado_projects.get(session_id, ""),
        }
    return bindings


def _latest_cursor_value(*values: str | None) -> str | None:
    latest: datetime | None = None
    latest_raw: str | None = None
    for value in values:
        parsed = _parse_dt(value)
        if parsed is None:
            continue
        if latest is None or parsed > latest:
            latest = parsed
            latest_raw = value
    return latest_raw


def _ado_project_for_feedback_stream(
    *,
    repo: str,
    binding: dict[str, str],
    trigger: PullRequestNewFeedbackTriggerDefinition | PullRequestCompletedTriggerDefinition,
    config: WorkerConfig,
) -> str:
    parts = [part.strip() for part in repo.split("/") if part.strip()]
    if len(parts) >= 3:
        return parts[-2]
    return binding.get("ado_project") or trigger.project or config.project


def _publish_directory_pr_feedback_events(
    *,
    client: httpx.Client,
    config: WorkerConfig,
    runtime_gateway_config: GatewayConfig,
    directory_path: str,
) -> int:
    triggers = _load_ado_pr_feedback_triggers(config)
    if not triggers:
        return 0

    legacy_bindings: dict[tuple[str, str], dict[str, str]] = {}
    if any(not _trigger_has_explicit_pr_criteria(trigger) for trigger in triggers):
        legacy_bindings = _load_ado_pr_feedback_session_bindings(
            session_store_path=runtime_gateway_config.session_store_path,
            pr_map_path=runtime_gateway_config.pr_map_path,
            directory_path=directory_path,
        )

    pat_env_name = runtime_gateway_config.azure_devops_pr_pat_env
    pat = os.environ.get(pat_env_name, "").strip()
    if not pat:
        _LOG.debug(
            "Skipping Azure PR feedback polling for directory=%s because %s is unset",
            directory_path,
            pat_env_name,
        )
        return 0

    cursor_store = TriggerCursorStore(config.runtime_dir / "trigger-cursors-pr-feedback.json")
    trigger_worker_config = _load_trigger_orchestrator_worker_config(config)
    event_queue = _build_trigger_event_queue_for_worker_config(config, trigger_worker_config)
    effect_wake_lock_path = _effect_lock_path_for_worker_config(config, trigger_worker_config)
    feedback_store = PrFeedbackStore(config.runtime_dir / "pr-feedback-store.json")
    audit_logger = AuditLogger(runtime_gateway_config.audit_log_path)
    ignored_feedback_ids = _read_ignored_feedback_ids(
        config.runtime_dir / "ignored-feedback-ids.json"
    )
    published_count = 0

    for trigger in triggers:
        if _trigger_has_explicit_pr_criteria(trigger):
            explicit_candidates = _list_explicit_ado_trigger_pull_requests(
                trigger=trigger,
                config=config,
                runtime_gateway_config=runtime_gateway_config,
                current_directory_path=directory_path,
            )
            streams = [
                (
                    {
                        "provider": candidate.provider,
                        "repo": candidate.repo,
                        "pr_id": candidate.pr_id,
                    },
                    {},
                    explicit_candidates.organization_url,
                    explicit_candidates.project,
                )
                for candidate in explicit_candidates.pull_requests
            ]
        else:
            organization_url = _ado_organization_url_for_trigger(
                trigger=trigger,
                legacy_organization_url=config.organization_url,
            )
            streams = [
                (
                    {"provider": "azure_devops", "repo": repo, "pr_id": pr_id},
                    legacy_bindings.get((repo, pr_id), {}),
                    organization_url,
                    _ado_project_for_feedback_stream(
                        repo=repo,
                        binding=legacy_bindings.get((repo, pr_id), {}),
                        trigger=trigger,
                        config=config,
                    ),
                )
                for repo, pr_id in sorted(legacy_bindings.keys())
            ]

        state = cursor_store.get(trigger.id)
        state_cursors = state.get("stream_cursors", {})
        if not isinstance(state_cursors, dict):
            state_cursors = {}
        previous_cursors = dict(state_cursors)
        next_cursors = dict(state_cursors)
        discovered: list[tuple[dict[str, str], dict[str, str], PrFeedbackItem]] = []

        for stream, binding, organization_url, project in streams:
            repo = stream["repo"]
            pr_id = stream["pr_id"]
            adapter = AzureDevOpsFeedbackAdapter(
                organization_url=organization_url,
                project=project,
                pat=pat,
                pat_env_name=pat_env_name,
            )
            stream_key = _stream_key("azure_devops", repo, pr_id)
            try:
                result = adapter.poll(
                    client=client,
                    repo=repo,
                    pr_id=pr_id,
                    cursor=previous_cursors.get(stream_key),
                )
            except httpx.HTTPStatusError as exc:
                status_code = exc.response.status_code if exc.response is not None else None
                if status_code == 404:
                    _LOG.warning(
                        "Skipping Azure PR feedback stream after 404 "
                        "directory=%s trigger_id=%s repo=%s pr_id=%s project=%s",
                        directory_path,
                        trigger.id,
                        repo,
                        pr_id,
                        adapter.project,
                    )
                    continue
                raise
            for envelope in result.feedback:
                feedback_item = annotate_agent_directed_feedback(
                    PrFeedbackItem.model_validate(pr_feedback_item_payload(envelope)),
                    agent_author_names=trigger.agent_author_names,
                    agent_mention_names=trigger.agent_mention_names,
                    agent_author_ids=trigger.agent_author_ids,
                    agent_mention_ids=trigger.agent_mention_ids,
                )
                discovered.append(
                    (
                        {
                            "provider": feedback_item.provider,
                            "repo": feedback_item.repo,
                            "pr_id": feedback_item.pr_id,
                        },
                        binding,
                        feedback_item,
                    )
                )
            next_cursors[stream_key] = _latest_cursor_value(
                next_cursors.get(stream_key), result.next_cursor
            )

        discovered.sort(key=lambda item: item[2].created_at)
        for stream, binding, feedback in discovered:
            stream_key = _stream_key(stream["provider"], stream["repo"], stream["pr_id"])
            previous = _parse_dt(previous_cursors.get(stream_key))
            if previous is not None and feedback.created_at <= previous:
                continue

            stored = feedback_store.add_feedback_if_new(
                FeedbackEnvelope.model_validate(feedback.model_dump(mode="json"))
            )
            next_cursors[stream_key] = _latest_cursor_value(
                next_cursors.get(stream_key),
                feedback.created_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            )
            feedback_store.set_cursor(stream_key, next_cursors[stream_key])
            if feedback.feedback_id in ignored_feedback_ids:
                continue
            if trigger.suppress_agent_authored_feedback and bool(
                (feedback.metadata or {}).get("agent_directed_author_self")
            ):
                continue
            if stored:
                audit_logger.log_decision(
                    event_type=AuditEventType.FEEDBACK_DETECTED,
                    session_id=binding["session_id"]
                    if binding and binding.get("session_id")
                    else None,
                    vm_id=binding["vm_id"] if binding and binding.get("vm_id") else None,
                    provider=feedback.provider,
                    repo=feedback.repo,
                    pr_id=feedback.pr_id,
                    decision="detected",
                    reason="new-feedback",
                    args={
                        "feedback": feedback.model_dump(mode="json"),
                        "stream_bindings": [binding] if binding else [],
                    },
                )
            event = TriggerEvent(
                event_id=f"{trigger.id}:{stream['provider']}:{stream['repo']}:{stream['pr_id']}:{feedback.feedback_id}",
                trigger_id=trigger.id,
                trigger_type=trigger.type,
                occurred_at=feedback.created_at.astimezone(UTC),
                payload={
                    "pr": stream,
                    "feedback": feedback.model_dump(mode="json"),
                }
                | (
                    {"session_id": binding["session_id"]}
                    if binding and binding.get("session_id")
                    else {}
                ),
            )
            publish_result = event_queue.publish_result(event)
            record = publish_result.record
            if publish_result.created and record.event_id == event.event_id:
                published_count += 1
                notify_wake_socket_for_lock(effect_wake_lock_path)
        cursor_store.put(trigger.id, {"stream_cursors": next_cursors})

    return published_count


def _publish_directory_pr_completion_events(
    *,
    client: httpx.Client,
    config: WorkerConfig,
    runtime_gateway_config: GatewayConfig,
    directory_path: str,
) -> int:
    _ = client
    triggers = _load_ado_pr_completed_triggers(config)
    if not triggers:
        return 0

    legacy_bindings: dict[tuple[str, str], dict[str, str]] = {}
    if any(not _trigger_has_explicit_pr_criteria(trigger) for trigger in triggers):
        legacy_bindings = _load_ado_pr_feedback_session_bindings(
            session_store_path=runtime_gateway_config.session_store_path,
            pr_map_path=runtime_gateway_config.pr_map_path,
            directory_path=directory_path,
        )

    pat_env_name = runtime_gateway_config.azure_devops_pr_pat_env
    pat = os.environ.get(pat_env_name, "").strip()
    if not pat:
        _LOG.debug(
            "Skipping Azure PR completion polling for directory=%s because %s is unset",
            directory_path,
            pat_env_name,
        )
        return 0

    trigger_worker_config = _load_trigger_orchestrator_worker_config(config)
    event_queue = _build_trigger_event_queue_for_worker_config(config, trigger_worker_config)
    effect_wake_lock_path = _effect_lock_path_for_worker_config(config, trigger_worker_config)
    pr_map_store = PrMapStore(runtime_gateway_config.pr_map_path)
    audit_logger = AuditLogger(runtime_gateway_config.audit_log_path)
    completion_cursor_store: TriggerCursorStore | None = None
    if any(_trigger_has_explicit_pr_criteria(trigger) for trigger in triggers):
        completion_cursor_store = TriggerCursorStore(
            config.runtime_dir / "trigger-cursors-pr-completed.json"
        )
    published_count = 0

    for trigger in triggers:
        explicit_pr_criteria = _trigger_has_explicit_pr_criteria(trigger)
        legacy_organization_url = ""
        completed_streams: dict[str, Any] = {}
        if explicit_pr_criteria and completion_cursor_store is not None:
            state = completion_cursor_store.get(trigger.id)
            cursor_streams = state.get("completed_streams", {}) if isinstance(state, dict) else {}
            if isinstance(cursor_streams, dict):
                completed_streams = dict(cursor_streams)

        if explicit_pr_criteria:
            explicit_candidates = _list_explicit_ado_trigger_pull_requests(
                trigger=trigger,
                config=config,
                runtime_gateway_config=runtime_gateway_config,
                current_directory_path=directory_path,
            )
            candidates = explicit_candidates.pull_requests
        else:
            explicit_candidates = None
            legacy_organization_url = _ado_organization_url_for_trigger(
                trigger=trigger,
                legacy_organization_url=config.organization_url,
            )
            candidates = [
                PullRequestItem(provider="azure_devops", repo=repo, pr_id=pr_id)
                for repo, pr_id in sorted(legacy_bindings.keys())
            ]

        for candidate in candidates:
            repo = candidate.repo
            pr_id = candidate.pr_id
            stream_key = _stream_key("azure_devops", repo, pr_id)
            if explicit_pr_criteria and stream_key in completed_streams:
                continue
            binding = {} if explicit_pr_criteria else legacy_bindings.get((repo, pr_id), {})
            provider = AzureDevOpsPrQueryProvider(
                organization_url=(
                    explicit_candidates.organization_url
                    if explicit_candidates is not None
                    else legacy_organization_url
                ),
                project=(
                    explicit_candidates.project
                    if explicit_candidates is not None
                    else _ado_project_for_feedback_stream(
                        repo=repo,
                        binding=binding,
                        trigger=trigger,
                        config=config,
                    )
                ),
                pat=pat,
                timeout_seconds=30.0,
            )
            try:
                response = provider.get_pull_request(
                    repo=repo,
                    pr_id=pr_id,
                    include_reviewers=False,
                    include_commits=False,
                )
            except httpx.HTTPStatusError as exc:
                status_code = exc.response.status_code if exc.response is not None else None
                if status_code == 404:
                    _LOG.warning(
                        "Skipping Azure PR completion stream after 404 "
                        "directory=%s trigger_id=%s repo=%s pr_id=%s project=%s",
                        directory_path,
                        trigger.id,
                        repo,
                        pr_id,
                        provider.project,
                    )
                    continue
                raise
            snapshot = status_snapshot_from_pull_request(response, repo=repo, pr_id=pr_id)
            if not is_pr_completed(snapshot):
                continue
            if explicit_pr_criteria:
                event_entry = snapshot
                mark_published = False
            else:
                update = pr_map_store.update_status(
                    snapshot,
                    trigger_id=trigger.id,
                )
                if not update.completed_ready:
                    continue
                event_entry = update.entry
                mark_published = True

            event_id = completion_event_id(trigger.id, event_entry)
            event = TriggerEvent(
                event_id=event_id,
                trigger_id=trigger.id,
                trigger_type=trigger.type,
                occurred_at=_pr_completion_occurred_at(event_entry),
                payload=completion_event_payload(event_entry),
            )
            publish_result = event_queue.publish_result(event)
            record = publish_result.record
            if record.event_id == event.event_id:
                if mark_published:
                    pr_map_store.mark_completion_event_published(
                        provider=event_entry["provider"],
                        repo=event_entry["repo"],
                        pr_id=event_entry["pr_id"],
                        trigger_id=trigger.id,
                        event_id=event.event_id,
                    )
                if explicit_pr_criteria:
                    completed_streams[stream_key] = event.event_id
                    completion_cursor_store.put(
                        trigger.id,
                        {"completed_streams": completed_streams},
                    )
                audit_logger.log_decision(
                    event_type=AuditEventType.PR_COMPLETION_DETECTED,
                    session_id=binding["session_id"]
                    if binding and binding.get("session_id")
                    else None,
                    vm_id=binding["vm_id"] if binding and binding.get("vm_id") else None,
                    provider=event_entry["provider"],
                    repo=event_entry["repo"],
                    pr_id=event_entry["pr_id"],
                    decision="detected",
                    reason="pr-completed",
                    args={"pr": completion_event_payload(event_entry)["pr"]},
                    result={"event_id": event.event_id},
                )
            if publish_result.created and record.event_id == event.event_id:
                published_count += 1
                notify_wake_socket_for_lock(effect_wake_lock_path)

    return published_count


def _pr_completion_occurred_at(entry: dict[str, Any]) -> datetime:
    parsed = _parse_dt(entry.get("completed_at")) or _parse_dt(entry.get("pr_status_updated_at"))
    return parsed or datetime.now(UTC)


def _read_ignored_feedback_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    payload = json.loads(path.read_text(encoding="utf-8"))
    normalized_payload, feedback_ids, changed = _normalize_ignored_feedback_payload(payload)
    if changed:
        temp_path = path.with_suffix(f"{path.suffix}.tmp")
        temp_path.write_text(
            json.dumps(normalized_payload, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temp_path.replace(path)
    return feedback_ids


def run_worker(config: WorkerConfig) -> None:
    if not config.directories:
        while True:
            logging.info("cycle skipped=no_directories")
            time.sleep(config.poll_interval_seconds)

    runtime_gateway_config = _worker_runtime_config(config)

    timeout = httpx.Timeout(connect=5.0, write=10.0, read=20.0, pool=5.0)
    with httpx.Client(timeout=timeout) as client:
        while True:
            cycle_start = time.monotonic()
            try:
                for directory_path in config.directories:
                    try:
                        _publish_directory_pr_feedback_events(
                            client=client,
                            config=config,
                            runtime_gateway_config=runtime_gateway_config,
                            directory_path=directory_path,
                        )
                        _publish_directory_pr_completion_events(
                            client=client,
                            config=config,
                            runtime_gateway_config=runtime_gateway_config,
                            directory_path=directory_path,
                        )
                    except httpx.HTTPError as exc:
                        logging.warning(
                            "cycle directory=%s error=%s",
                            directory_path,
                            exc.__class__.__name__,
                        )
                    except Exception:
                        logging.exception("cycle directory=%s error=unexpected", directory_path)
            except Exception:  # pragma: no cover
                logging.exception("cycle error=unexpected")

            elapsed = time.monotonic() - cycle_start
            sleep_seconds = max(0.0, config.poll_interval_seconds - elapsed)
            time.sleep(sleep_seconds)


def run_worker_command(
    *,
    log_level: str = "INFO",
    forget: int | None = None,
) -> None:
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if forget is not None:
        store = _processed_store_for_runtime()
        removed_count = store.forget_all(forget)
        logging.info("forget work_item=%s removed_count=%s", forget, removed_count)
        return

    config = WorkerConfig.load_from_env()

    run_worker(config)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Publish Azure DevOps PR feedback/completion events."
    )
    parser.add_argument("--log-level", default="INFO", help="Python logging level.")
    parser.add_argument(
        "--forget",
        type=int,
        help="Forget a specific work item ID in the processed store, then exit.",
    )
    args = parser.parse_args()

    run_worker_command(
        log_level=args.log_level,
        forget=args.forget,
    )


if __name__ == "__main__":
    main()

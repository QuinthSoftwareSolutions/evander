from __future__ import annotations

from collections.abc import Callable, Mapping
from importlib import resources
import hashlib
import json
import logging
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from agent_sandbox.admin.tool_visibility import ToolVisibilityPayload
from agent_sandbox.admin.services.ports import (
    DirectoryPathPort,
    PrivilegedHelperPort,
    WorkflowConfigPort,
)
from agent_sandbox.admin.services import (
    git_helpers,
    process_helpers,
    project_helpers,
    session_helpers,
    vm_helpers,
)
from agent_sandbox.admin.services.issue_setup_helpers import (
    EffectiveIssueSetupConfig,
    resolve_agent_model_for_manifest,
    resolve_effective_issue_setup_config,
)

from agent_sandbox.approval_notification_targets import (
    resolve_approval_notification_target_ids_for_directory,
)
from agent_sandbox.admin.services.local_issue_identity import LocalIssueIdentityResolver
from agent_sandbox.contact_allowlists import validate_allowed_contact_ids_for_directory
from agent_sandbox.config import GatewayConfig
from agent_sandbox.container_worker_images import resolve_container_worker_image_selector
from agent_sandbox.directories import DirectoryPath, DirectoryStore
from agent_sandbox.git_pr_notes import agent_pr_notes_ref_for_session
from agent_sandbox.messaging.targets import load_communication_targets_config
from agent_sandbox.effects.registry import EffectProviderFactory
from agent_sandbox.effects.providers.agent_session import (
    build_container_session_files,
    default_agent_provider_for_config,
)
from agent_sandbox.events.provider_models import AgentEnqueuePromptEffectDefinition
from agent_sandbox.execution import ExecutionBackendKind
from agent_sandbox.execution.container_worker_backend import ContainerWorkerVmExecutionBackend
from agent_sandbox.execution.libvirt_backend import LibvirtVmExecutionBackend
from agent_sandbox.execution.container_worker_client import ContainerWorkerClientError
from agent_sandbox.models import (
    AgentPromptQueueRecord,
    AgentPromptQueueSummary,
    AgentRecord,
    ContainerSessionLifecycle,
    ContainerSessionLifecycleAction,
    ContainerSessionLifecycleActions,
    ContainerSessionLifecycleState,
    SessionActionResult,
    SessionManifest,
    SessionSummary,
    ToolName,
    VmActionResult,
    VmSummary,
    tool_name_value,
)
from agent_sandbox.provider_config import (
    MODEL_PROVIDER_MODE_METADATA_KEY,
    normalize_model_provider_mode_override,
)
from agent_sandbox.repositories.chat_assistant_sessions import JsonChatAssistantSessionRepository
from agent_sandbox.repositories.session_manifests import as_session_manifest_repository
from agent_sandbox.repositories.skills_library import (
    SkillLibraryEntry,
    SkillLibraryRepository,
    normalize_skill_names,
)
from agent_sandbox.session_expiry import ensure_session_not_expired
from agent_sandbox.session_artifacts import (
    AGENT_ARTIFACTS_ENV,
    DEFAULT_AGENT_ARTIFACTS_DIR,
    normalize_artifact_relative_path,
)
from agent_sandbox.runtime import allowed_tools_for_capability_profile
from agent_sandbox.services import parse_teams_target
from agent_sandbox.vm.defaults_config import EffectiveVmDefaultsConfig, VmDefaultsConfigResolver
from agent_sandbox.vm.provider import VmProviderRegistry
from agent_sandbox.vm.templates import resolve_libvirt_vm_template_selector
from agent_sandbox.workflow.catalog import WorkflowEditorFieldOption
from agent_sandbox.workflow.events import workflow_event_journal_path
from agent_sandbox.worker.security import sanitize_text

_ASSISTANT_AGENTS_DESTINATION_PATH = "/home/agent/AGENTS.md"
_ASSISTANT_AGENTS_TEMPLATE_NAME = "chat-assistant-AGENTS.md"
AGENT_CREATE_OPERATION_MAX_WORKERS = 4
_CONTAINER_SESSION_ID_LOG_PATTERN = re.compile(
    r"^session id:\s*(?P<thread_id>\S+)\s*$", re.IGNORECASE | re.MULTILINE
)
_CONTAINER_THREAD_FULL_GROUP_RECORD_LIMIT = 100
_VM_ARTIFACT_TEXT_READ_COMMAND = f"""python3 - <<'PY'
from pathlib import Path, PurePosixPath
import os
import sys

root = Path(os.environ.get({AGENT_ARTIFACTS_ENV!r}) or {DEFAULT_AGENT_ARTIFACTS_DIR!r})
relative_text = os.environ.get("AGENT_SANDBOX_ARTIFACT_RELATIVE_PATH") or ""
relative = PurePosixPath(relative_text)
if not relative_text or relative.is_absolute() or any(part in {{"", ".", ".."}} for part in relative.parts):
    print("artifact path must be relative and must not contain dot or parent segments", file=sys.stderr)
    raise SystemExit(64)
try:
    root_resolved = root.resolve(strict=True)
    target = (root / relative.as_posix()).resolve(strict=True)
    target.relative_to(root_resolved)
except FileNotFoundError:
    print(f"artifact file not found: {{relative.as_posix()}}", file=sys.stderr)
    raise SystemExit(66)
except ValueError:
    print("artifact path must stay within the session artifacts folder", file=sys.stderr)
    raise SystemExit(64)
if not target.is_file():
    print(f"artifact path is not a regular file: {{relative.as_posix()}}", file=sys.stderr)
    raise SystemExit(66)
try:
    sys.stdout.write(target.read_text(encoding="utf-8"))
except UnicodeDecodeError:
    print("artifact file is not valid UTF-8 text", file=sys.stderr)
    raise SystemExit(65)
PY
"""

AgentCreateOperationStatus = Literal["queued", "running", "succeeded", "failed"]


class AgentCreateOperation(BaseModel):
    operation_id: str
    status: AgentCreateOperationStatus
    backend: str
    target_name: str
    session_id: str | None = None
    directory_path: str = "root"
    owner_id: str | None = None
    owner_pid: int | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    started_at: datetime | None = None
    finished_at: datetime | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


COMMUNICATION_FILE_SESSION_POLICY_FIELDS = session_helpers.COMMUNICATION_FILE_SESSION_POLICY_FIELDS
SESSION_BOOL_POLICY_FIELDS = session_helpers.SESSION_BOOL_POLICY_FIELDS
_PROTECTED_MIRROR_HEAD_REFSPECS = git_helpers.PROTECTED_MIRROR_HEAD_REFSPECS
_build_issue_session_manifest = session_helpers.build_issue_session_manifest
_default_base_branch_for_repo_remote = git_helpers.default_base_branch_for_repo_remote
_guest_agent_pr_note_helper_b64 = project_helpers.guest_agent_pr_note_helper_b64
_guest_push_agent_helper_b64 = project_helpers.guest_push_agent_helper_b64
_guest_sync_base_helper_b64 = project_helpers.guest_sync_base_helper_b64
_is_remote_git_source = git_helpers.is_remote_git_source
_mirror_remote_name_for_repo = git_helpers.mirror_remote_name_for_repo
_normalize_allowed_tools = session_helpers.normalize_allowed_tools
_normalize_plain_string_list = session_helpers.normalize_plain_string_list
_normalize_session_labels = session_helpers.normalize_session_labels
_normalize_session_policy_bool = session_helpers.normalize_session_policy_bool
_normalize_session_policy_non_negative_int_or_none = (
    session_helpers.normalize_session_policy_non_negative_int_or_none
)
_prune_approvals_for_sessions = session_helpers.prune_approvals_for_sessions
_prune_pr_feedback_store = git_helpers.prune_pr_feedback_store
_prune_pr_map = git_helpers.prune_pr_map
_resolve_canonical_origin_url_for_repo = git_helpers.resolve_canonical_origin_url_for_repo
_resolve_private_key_path = vm_helpers.resolve_private_key_path
_resolve_session_target = session_helpers.resolve_session_target
_resolve_vm_ip = vm_helpers.resolve_vm_ip
_run_subprocess = process_helpers.run_subprocess
_session_policy_update_from_payload = session_helpers.session_policy_update_from_payload
_ssh_options = vm_helpers.ssh_options
_sync_added_discord_session_cursors = session_helpers.sync_added_discord_session_cursors
_utc_now = session_helpers.utc_now
_validate_allowed_contact_ids_for_directory = (
    session_helpers.validate_allowed_contact_ids_for_runtime_directory
)


def _deep_merge_mapping(base: Mapping[str, Any], update: Mapping[str, Any]) -> dict[str, Any]:
    merged: dict[str, Any] = dict(base)
    for key, value in update.items():
        existing = merged.get(key)
        if isinstance(existing, Mapping) and isinstance(value, Mapping):
            merged[key] = _deep_merge_mapping(existing, value)
            continue
        merged[key] = value
    return merged


@dataclass(frozen=True)
class NormalizedVmCreationOptions:
    provider: str | None
    allowed_contact_ids: list[str] | None
    payload: dict[str, Any]

    def provider_kwargs(self) -> dict[str, Any]:
        payload = dict(self.payload)
        payload["allowed_contact_ids"] = self.allowed_contact_ids
        return payload


def _managed_codex_skills_root(runtime_dir: Path, skill_names: list[str] | None) -> Path | None:
    names = normalize_skill_names(skill_names)
    if not names:
        return None
    repository = SkillLibraryRepository(runtime_dir)
    if all(repository.has_skill(name) for name in names):
        return repository.skills_root
    return None


def _safe_chat_assistant_identifier(value: str) -> str:
    raw_value = str(value or "").strip() or "anonymous"
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", raw_value).strip("-") or "anonymous"
    digest = sha256(raw_value.encode("utf-8")).hexdigest()[:10]
    return f"{normalized[:37]}-{digest}"


def _is_synthetic_chat_assistant_thread_id(value: str | None) -> bool:
    return str(value or "").strip().startswith("chat-thread-")


def _agent_provider_from_session(session: SessionManifest | Any | None) -> str | None:
    metadata = getattr(session, "metadata", {}) if session is not None else {}
    if isinstance(metadata, Mapping):
        candidate = str(
            metadata.get("agent_provider") or metadata.get("agent_harness") or ""
        ).strip()
        if candidate:
            return candidate
    return None


def _chat_thread_mapping_key(*, agent_thread_id: str | None, thread_id: str | None) -> str:
    normalized_agent_thread_id = str(agent_thread_id or "").strip()
    if normalized_agent_thread_id:
        return normalized_agent_thread_id
    normalized_thread_id = str(thread_id or "").strip()
    if normalized_thread_id:
        return f"admin-chat:{normalized_thread_id}"
    return "admin-chat:unbound"


def _chat_thread_id_from_queue_key(queue_key: str | None) -> str:
    normalized = str(queue_key or "").strip()
    if not normalized.startswith("chat:"):
        return ""
    parts = normalized.split(":", 2)
    return parts[1].strip() if len(parts) >= 2 else ""


def _assistant_capability_profile(settings: dict[str, Any] | None) -> str:
    payload = settings or {}
    if bool(payload.get("read_only", True)):
        return "read-only"
    configured = str(payload.get("default_profile") or "").strip()
    return configured or "operator-limited"


def _assistant_allowed_tools(profile: str) -> list[ToolName]:
    tools = list(allowed_tools_for_capability_profile(profile))
    if ToolName.ASSISTANT_POST_CHAT_REPLY not in tools:
        tools.append(ToolName.ASSISTANT_POST_CHAT_REPLY)
    return tools


def _assistant_mutation_policy(*, profile: str) -> dict[str, Any]:
    return {
        "writable_scopes": [
            "config/assistant",
            "config/workflow",
            "config/directory",
            "runtime/agents",
        ],
        "require_destructive_approval_gate": profile == "full-admin",
        "destructive_approval_gate": "approval_submit",
    }


def _assistant_agents_md_content(project_dir: Path) -> str:
    root = Path(project_dir)
    for candidate in (
        root / "src" / "agent_sandbox" / "templates" / _ASSISTANT_AGENTS_TEMPLATE_NAME,
        root / "templates" / _ASSISTANT_AGENTS_TEMPLATE_NAME,
    ):
        if candidate.exists():
            return candidate.read_text(encoding="utf-8")
    return (
        resources.files("agent_sandbox")
        .joinpath("templates", _ASSISTANT_AGENTS_TEMPLATE_NAME)
        .read_text(encoding="utf-8")
    )


def _assistant_home_file_specs(project_dir: Path) -> list[dict[str, Any]]:
    return [
        {
            "destination_path": _ASSISTANT_AGENTS_DESTINATION_PATH,
            "mode": "0644",
            "content": _assistant_agents_md_content(project_dir),
        }
    ]


def _chat_assistant_vm_name(*, backend: str, session_id: str) -> str:
    if backend == "libvirt_vm":
        return "assistant-shared"
    return session_id


# VM/agent/container helper ownership moved from the legacy AdminService facade.
def _append_unique_string_values(base: list[str], values: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for value in [*base, *values]:
        normalized = str(value or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        merged.append(normalized)
    return merged


def _email_mailbox_id_from_auth_profile_ref(auth_profile_ref: object, *, target_id: str) -> str:
    normalized = str(auth_profile_ref or "").strip()
    if normalized.startswith("email."):
        normalized = normalized.removeprefix("email.")
    if not normalized:
        raise ValueError(
            f"email communication target {target_id!r} requires policy.auth_profile_ref"
        )
    return normalized


def _admin_agent_communication_allowlists(
    payload: Mapping[str, Any],
    *,
    config: GatewayConfig,
    directory_path: str,
) -> dict[str, list[str] | None]:
    discord_channels = _normalize_plain_string_list(
        payload.get("discord_channels") if "discord_channels" in payload else None,
        field_name="discord_channels",
    )
    teams_channel_allowlist = _normalize_plain_string_list(
        payload.get("teams_channel_allowlist") if "teams_channel_allowlist" in payload else None,
        field_name="teams_channel_allowlist",
    )
    teams_chat_allowlist = _normalize_plain_string_list(
        payload.get("teams_chat_allowlist") if "teams_chat_allowlist" in payload else None,
        field_name="teams_chat_allowlist",
    )
    teams_user_allowlist = _normalize_plain_string_list(
        payload.get("teams_user_allowlist") if "teams_user_allowlist" in payload else None,
        field_name="teams_user_allowlist",
    )
    email_mailboxes = _normalize_plain_string_list(
        payload.get("email_mailboxes") if "email_mailboxes" in payload else None,
        field_name="email_mailboxes",
    )
    email_folders = _normalize_plain_string_list(
        payload.get("email_folders") if "email_folders" in payload else None,
        field_name="email_folders",
    )
    communication_target_ids = [
        target_id.lower()
        for target_id in _normalize_plain_string_list(
            payload.get("communication_target_ids")
            if "communication_target_ids" in payload
            else None,
            field_name="communication_target_ids",
        )
    ]
    if not communication_target_ids:
        return {
            "discord_channels": discord_channels or None,
            "teams_channel_allowlist": teams_channel_allowlist or None,
            "teams_chat_allowlist": teams_chat_allowlist or None,
            "teams_user_allowlist": teams_user_allowlist or None,
            "email_mailboxes": email_mailboxes or None,
            "email_folders": email_folders or None,
        }

    targets_by_id = load_communication_targets_config(
        runtime_dir=config.runtime_dir,
        directory_store=DirectoryStore(config.directories_store_path),
        directory_path=directory_path,
    ).by_id()
    selected_discord_channels: list[str] = []
    selected_teams_channels: list[str] = []
    selected_teams_chats: list[str] = []
    selected_teams_users: list[str] = []
    selected_email_mailboxes: list[str] = []
    for target_id in communication_target_ids:
        target = targets_by_id.get(target_id)
        if target is None:
            raise ValueError(
                "communication_target_ids contains unknown target "
                f"{target_id!r} for directory {directory_path!r}"
            )
        if target.provider == "discord":
            selected_discord_channels.append(target.id)
            continue
        if target.provider == "teams":
            teams_kind = parse_teams_target(target.address).kind
            if teams_kind == "chat":
                selected_teams_chats.append(target.id)
            elif teams_kind == "user":
                selected_teams_users.append(target.id)
            else:
                selected_teams_channels.append(target.id)
            continue
        if target.provider == "email":
            missing_capabilities = sorted({"read", "write"} - set(target.policy.capabilities))
            if missing_capabilities:
                raise ValueError(
                    "email communication target "
                    f"{target.id!r} requires both read and write capabilities"
                )
            selected_email_mailboxes.append(
                _email_mailbox_id_from_auth_profile_ref(
                    target.policy.auth_profile_ref,
                    target_id=target.id,
                )
            )
            continue
        raise ValueError(
            "communication_target_ids only supports Teams, Discord, and email targets; "
            f"target {target.id!r} uses provider {target.provider!r}"
        )

    return {
        "discord_channels": _append_unique_string_values(
            discord_channels, selected_discord_channels
        )
        or None,
        "teams_channel_allowlist": _append_unique_string_values(
            teams_channel_allowlist,
            selected_teams_channels,
        )
        or None,
        "teams_chat_allowlist": _append_unique_string_values(
            teams_chat_allowlist,
            selected_teams_chats,
        )
        or None,
        "teams_user_allowlist": _append_unique_string_values(
            teams_user_allowlist,
            selected_teams_users,
        )
        or None,
        "email_mailboxes": _append_unique_string_values(email_mailboxes, selected_email_mailboxes)
        or None,
        "email_folders": email_folders or None,
    }


def _resolve_session_approval_notification_target_ids(
    runtime: RuntimeContext,
    config: GatewayConfig,
    session: SessionManifest,
    target_ids: object,
) -> list[str]:
    return resolve_approval_notification_target_ids_for_directory(
        runtime_dir=config.runtime_dir,
        directory_store=runtime.directories,
        directory_path=session.directory_path,
        target_ids=target_ids,
    )


def _coerce_optional_string(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _coerce_optional_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _log_page_from_end(
    text: str,
    *,
    limit: int,
    offset: int,
    query: str | None = None,
) -> dict[str, object]:
    lines = text.splitlines()
    normalized_query = str(query or "").strip()
    if normalized_query:
        needle = normalized_query.casefold()
        lines = [line for line in lines if needle in line.casefold()]
    total_lines = len(lines)
    normalized_limit = max(1, int(limit))
    normalized_offset = max(0, int(offset))
    end_index = max(0, total_lines - normalized_offset)
    start_index = max(0, end_index - normalized_limit)
    page_lines = lines[start_index:end_index]
    return {
        "text": "\n".join(page_lines) + ("\n" if page_lines else ""),
        "limit": normalized_limit,
        "offset": normalized_offset,
        "returned_lines": len(page_lines),
        "total_lines": total_lines,
        "start_line": start_index + 1 if page_lines else 0,
        "end_line": end_index if page_lines else 0,
        "has_more_before": start_index > 0,
        "query": normalized_query or None,
    }


def _log_page_from_start(
    text: str,
    *,
    limit: int,
    offset: int,
    query: str | None = None,
) -> dict[str, object]:
    lines = text.splitlines()
    normalized_query = str(query or "").strip()
    if normalized_query:
        needle = normalized_query.casefold()
        lines = [line for line in lines if needle in line.casefold()]
    total_lines = len(lines)
    normalized_limit = max(1, int(limit))
    normalized_offset = max(0, int(offset))
    start_index = min(total_lines, normalized_offset)
    end_index = min(total_lines, start_index + normalized_limit)
    page_lines = lines[start_index:end_index]
    return {
        "text": "\n".join(page_lines) + ("\n" if page_lines else ""),
        "limit": normalized_limit,
        "offset": normalized_offset,
        "returned_lines": len(page_lines),
        "total_lines": total_lines,
        "start_line": start_index + 1 if page_lines else 0,
        "end_line": end_index if page_lines else 0,
        "has_more_before": end_index < total_lines,
        "query": normalized_query or None,
    }


@dataclass(frozen=True)
class VmAgentAdminDependencies:
    config: Any
    runtime: Any
    project_dir: Path
    local_issue_store: Any
    worker_admin: Any
    privileged_helper: PrivilegedHelperPort
    directory_path: DirectoryPathPort
    workflow_config: WorkflowConfigPort
    resolve_agent_model_for_session: Any
    resolve_vm_defaults: Any
    resolve_issue_setup: Any
    resolve_local_issue_record: Any
    resolve_local_issue_key: Any
    resolve_repo_source: Any
    sync_repo_into_bare_remote: Any
    clone_repo_into_vm: Any
    list_vms_for_agents: Callable[..., list[VmSummary]] | None = None
    admin_service_override: (
        Callable[[str, tuple[Any, ...], dict[str, Any]], tuple[bool, Any]] | None
    ) = None


@dataclass(frozen=True)
class _VmProviderRegistryDependencies:
    runtime: Any
    directory_path: DirectoryPathPort
    resolve_vm_defaults: Any
    resolve_agent_model_for_session: Any
    privileged_helper: PrivilegedHelperPort
    directory_ownership: Any
    agent_control_store: Any

    def normalize_directory_path(self, directory_path: str | None) -> str:
        return self.directory_path.normalize_directory_path(directory_path)

    def get_privileged_helper(self) -> Any | None:
        return self.privileged_helper.get_privileged_helper()

    def get_use_privileged_helper(self) -> bool:
        return self.privileged_helper.get_use_privileged_helper()

    def direct_owned_entity_ids(self, directory_path: str, *, kind: str = "vm") -> set[str]:
        return self.directory_ownership.direct_owned_entity_ids(directory_path, kind=kind)

    def sync_added_discord_session_cursors(
        self, *, previous_session: Any, updated_session: Any
    ) -> None:
        session_helpers.sync_added_discord_session_cursors(
            self.runtime,
            previous_session=previous_session,
            updated_session=updated_session,
        )


class _EmptyDirectoryOwnership:
    def direct_owned_entity_ids(self, directory_path: str, *, kind: str = "vm") -> set[str]:
        del directory_path, kind
        return set()


class _EnabledAgentControlStore:
    def status_for_vm(self, vm_name: str) -> str:
        del vm_name
        return "enabled"


class _CallbackPrivilegedHelperPort:
    def __init__(
        self,
        get_privileged_helper: Any | None,
        get_use_privileged_helper: Any | None,
    ) -> None:
        self._get_privileged_helper = get_privileged_helper or (lambda: None)
        self._get_use_privileged_helper = get_use_privileged_helper or (lambda: False)

    def get_privileged_helper(self) -> Any | None:
        return self._get_privileged_helper()

    def get_use_privileged_helper(self) -> bool:
        return bool(self._get_use_privileged_helper())

    def set_privileged_helper(self, helper: Any | None) -> None:
        self._get_privileged_helper = lambda: helper


@dataclass
class VmAgentAdminSupport:
    config: Any
    runtime: Any
    project_dir: Path
    local_issue_store: Any
    local_issue_identity_resolver: LocalIssueIdentityResolver
    worker_admin: Any
    workflow_config_admin: Any
    directory_admin: Any
    privileged_helper: PrivilegedHelperPort | None = None
    get_privileged_helper: Any | None = None
    get_use_privileged_helper: Any | None = None

    def __post_init__(self) -> None:
        if self.privileged_helper is None:
            self.privileged_helper = _CallbackPrivilegedHelperPort(
                self.get_privileged_helper,
                self.get_use_privileged_helper,
            )

    def normalize_directory_path(self, directory_path: str | None) -> str:
        if not directory_path:
            return "root"
        result = self.directory_admin.get(directory_path)
        if not result.ok or result.data is None:
            return "root"
        return result.data.details.directory.path

    def read_worker_global_config(self, worker_id: str) -> Any:
        return self.workflow_config_admin.read_worker_global_config(worker_id)

    def resolve_vm_defaults(self, directory_path: str) -> Any:
        resolver = VmDefaultsConfigResolver(
            self.config,
            directory_store=self.runtime.directories,
            session_store=self.runtime.session_repository,
        )
        return resolver.resolve_for_directory(directory_path)

    def resolve_issue_setup(self, directory_path: str, *, root_dir: Path) -> Any:
        return resolve_effective_issue_setup_config(
            config=self.config,
            runtime=self.runtime,
            directory_path=directory_path,
            root_dir=root_dir,
        )

    def resolve_agent_model_for_session(self, session_or_id: Any) -> str:
        if isinstance(session_or_id, SessionManifest):
            session = session_or_id
            session_id = session.session_id
        else:
            session_id = str(session_or_id).strip()
            if not session_id:
                raise ValueError(
                    "Cannot resolve AGENT_MODEL: missing session id. Provide a valid session id or SessionManifest."
                )
            session = self.runtime.session_repository.get_session(session_id)
            if session is None:
                raise ValueError(
                    f"Cannot resolve AGENT_MODEL for session '{session_id}': session not found. "
                    "Remediation: create the session first or pass a SessionManifest with session.model set."
                )
        issue_setup = self.resolve_issue_setup(session.directory_path, root_dir=self.project_dir)
        return resolve_agent_model_for_manifest(session, issue_setup=issue_setup)

    def resolve_local_issue_record(self, issue_identifier: str) -> Any:
        return self.local_issue_identity_resolver.resolve_record(issue_identifier)

    def resolve_local_issue_key(self, identifier: str) -> str | None:
        return self.local_issue_identity_resolver.resolve_key(identifier)

    def find_local_issue_by_remote_identifier(self, issue_identifier: str) -> Any | None:
        return self.local_issue_identity_resolver.find_by_remote_identifier(issue_identifier)

    def resolve_repo_source(self, repo_source: str) -> tuple[str, str]:
        resolve_script = self.project_dir / "scripts" / "resolve_repo_source.sh"
        path_result = process_helpers.run_subprocess(
            [str(resolve_script), "--path-only", repo_source], capture_output=True, text=True
        )
        if path_result.returncode != 0:
            raise RuntimeError(
                (path_result.stderr or path_result.stdout or "").strip()
                or f"Failed to resolve repo source: {repo_source}"
            )
        name_result = process_helpers.run_subprocess(
            [str(resolve_script), "--name-only", repo_source], capture_output=True, text=True
        )
        if name_result.returncode != 0:
            raise RuntimeError(
                (name_result.stderr or name_result.stdout or "").strip()
                or f"Failed to determine repo name: {repo_source}"
            )
        return (path_result.stdout or "").strip(), (name_result.stdout or "").strip()

    def sync_repo_into_bare_remote(
        self,
        repo_name: str,
        resolved_repo_path: str,
        *,
        issue_setup: Any,
        repo_source: str | None = None,
    ) -> Path:
        create_remote_script = self.project_dir / "scripts" / "create_git_remote.sh"
        completed = process_helpers.run_subprocess(
            [str(create_remote_script), repo_name, "agents"],
            capture_output=True,
            text=True,
            env=os.environ
            | {
                "AGENT_SANDBOX_GIT_ROOT": issue_setup.git_root,
                "AGENT_SANDBOX_GIT_HOST": issue_setup.git_host,
                "AGENT_SANDBOX_GIT_USER": issue_setup.git_user,
                "AGENT_SANDBOX_GIT_SSH_ALIAS": issue_setup.git_ssh_alias,
            },
        )
        if completed.returncode != 0:
            raise RuntimeError(
                (completed.stderr or completed.stdout or "").strip()
                or f"Failed to create git remote for {repo_name}"
            )
        bare_repo_path = Path(issue_setup.git_root) / f"{repo_name}.git"
        fetch_result = process_helpers.run_subprocess(
            [
                "git",
                f"--git-dir={bare_repo_path}",
                "fetch",
                "--prune",
                resolved_repo_path,
                "+refs/heads/*:refs/heads/*",
                "+refs/tags/*:refs/tags/*",
                *git_helpers.PROTECTED_MIRROR_HEAD_REFSPECS,
            ],
            capture_output=True,
            text=True,
        )
        if fetch_result.returncode != 0:
            raise RuntimeError(
                (fetch_result.stderr or fetch_result.stdout or "").strip()
                or f"Failed to mirror repo into {bare_repo_path}"
            )
        trusted_repo_result = process_helpers.run_subprocess(
            [
                "git",
                f"--git-dir={bare_repo_path}",
                "config",
                "agent-sandbox.trustedRepoPath",
                resolved_repo_path,
            ],
            capture_output=True,
            text=True,
        )
        if trusted_repo_result.returncode != 0:
            raise RuntimeError(
                (trusted_repo_result.stderr or trusted_repo_result.stdout or "").strip()
                or f"Failed to record trusted repo path for {bare_repo_path}"
            )
        mirror_source = (repo_source or resolved_repo_path).strip()
        for key, value in (
            ("agent-sandbox.mirrorSource", mirror_source),
            (
                "agent-sandbox.mirrorRemoteName",
                git_helpers.mirror_remote_name_for_repo(resolved_repo_path),
            ),
            ("agent-sandbox.mirrorSyncRemote", "1"),
            ("agent-sandbox.mirrorForceLocal", "0"),
        ):
            result = process_helpers.run_subprocess(
                ["git", f"--git-dir={bare_repo_path}", "config", key, value],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    (result.stderr or result.stdout or "").strip()
                    or f"Failed to record {key} for {bare_repo_path}"
                )
        mirror_remote_name = git_helpers.mirror_remote_name_for_repo(resolved_repo_path)
        canonical_origin_url = git_helpers.resolve_canonical_origin_url_for_repo(
            resolved_repo_path, mirror_remote_name
        )
        if canonical_origin_url:
            canonical_config_result = process_helpers.run_subprocess(
                [
                    "git",
                    f"--git-dir={bare_repo_path}",
                    "config",
                    "agent-sandbox.canonicalOriginUrl",
                    canonical_origin_url,
                ],
                capture_output=True,
                text=True,
            )
            if canonical_config_result.returncode != 0:
                raise RuntimeError(
                    (canonical_config_result.stderr or canonical_config_result.stdout or "").strip()
                    or f"Failed to record canonical origin URL for {bare_repo_path}"
                )
        default_base_branch = git_helpers.default_base_branch_for_repo_remote(
            resolved_repo_path, mirror_remote_name
        )
        if default_base_branch:
            base_branch_config_result = process_helpers.run_subprocess(
                [
                    "git",
                    f"--git-dir={bare_repo_path}",
                    "config",
                    "agent-sandbox.baseBranch",
                    default_base_branch,
                ],
                capture_output=True,
                text=True,
            )
            if base_branch_config_result.returncode != 0:
                raise RuntimeError(
                    (
                        base_branch_config_result.stderr or base_branch_config_result.stdout or ""
                    ).strip()
                    or f"Failed to record base branch for {bare_repo_path}"
                )
            symbolic_ref_result = process_helpers.run_subprocess(
                [
                    "git",
                    f"--git-dir={bare_repo_path}",
                    "symbolic-ref",
                    "HEAD",
                    f"refs/heads/{default_base_branch}",
                ],
                capture_output=True,
                text=True,
            )
            if symbolic_ref_result.returncode != 0:
                raise RuntimeError(
                    (symbolic_ref_result.stderr or symbolic_ref_result.stdout or "").strip()
                    or f"Failed to set HEAD for {bare_repo_path}"
                )
        return bare_repo_path

    def clone_repo_into_vm(
        self,
        *,
        vm_name: str,
        vm_user: str,
        session_id: str,
        repo_name: str,
        issue_setup: Any,
        repo_source: str | None = None,
        clone_branch: str | None = None,
        issue_number: str | None = None,
        vm_ip: str | None = None,
        git_author_name: str | None = None,
        git_author_email: str | None = None,
        git_committer_name: str | None = None,
        git_committer_email: str | None = None,
    ) -> str:
        bootstrap_git_script = self.project_dir / "scripts" / "bootstrap_git_vm.sh"
        completed = process_helpers.run_subprocess(
            [str(bootstrap_git_script), vm_name, vm_user],
            capture_output=True,
            text=True,
            env=os.environ
            | {
                "AGENT_SANDBOX_GIT_ROOT": issue_setup.git_root,
                "AGENT_SANDBOX_GIT_HOST": issue_setup.git_host,
                "AGENT_SANDBOX_GIT_USER": issue_setup.git_user,
                "AGENT_SANDBOX_GIT_SSH_ALIAS": issue_setup.git_ssh_alias,
            },
        )
        if completed.returncode != 0:
            raise RuntimeError(
                (completed.stderr or completed.stdout or "").strip()
                or f"Failed to bootstrap guest git for {vm_name}"
            )

        vm_ip = vm_ip or vm_helpers.resolve_vm_ip(vm_name)
        ssh_private_key_path = vm_helpers.resolve_private_key_path()
        if not vm_ip or ssh_private_key_path is None:
            raise RuntimeError(f"Unable to resolve SSH access for {vm_name}")

        remote_url = f"ssh://{issue_setup.git_ssh_alias}{issue_setup.git_root}/{repo_name}.git"
        pr_notes_ref = agent_pr_notes_ref_for_session(session_id)
        canonical_remote_url = (
            repo_source.strip()
            if repo_source and git_helpers.is_remote_git_source(repo_source)
            else ""
        )
        guest_repo_root = f"/home/{vm_user}/{repo_name}"
        agent_pr_note_helper_b64 = project_helpers.guest_agent_pr_note_helper_b64(self.project_dir)
        agent_push_helper_b64 = project_helpers.guest_push_agent_helper_b64(self.project_dir)
        sync_base_helper_b64 = project_helpers.guest_sync_base_helper_b64(self.project_dir)
        clone_branch_line = ""
        clone_command = 'git clone "${REMOTE_URL}" "${TARGET_DIR}"'
        if clone_branch:
            clone_command = 'git clone --branch "${CLONE_BRANCH}" "${REMOTE_URL}" "${TARGET_DIR}"'
            clone_branch_line = f'CLONE_BRANCH="{clone_branch}"\n'
        issue_config_line = ""
        if issue_number:
            issue_config_line = (
                f'git -C "${{TARGET_DIR}}" config agent-sandbox.issueNumber "{issue_number}"\n'
                f'git -C "${{TARGET_DIR}}" config agent-sandbox.destinationBranch "feature/issue-{issue_number}"\n'
            )
        branch_suffix_line = (
            'git -C "${TARGET_DIR}" config agent-sandbox.branchSuffix "${VM_NAME}"\n'
        )
        normalized_author_name = str(git_author_name or "").strip()
        normalized_author_email = str(git_author_email or "").strip()
        if bool(normalized_author_name) != bool(normalized_author_email):
            raise ValueError("git_author_name and git_author_email must both be set")
        normalized_committer_name = str(git_committer_name or "").strip()
        normalized_committer_email = str(git_committer_email or "").strip()
        if bool(normalized_committer_name) != bool(normalized_committer_email):
            raise ValueError("git_committer_name and git_committer_email must both be set")
        git_user_name = normalized_author_name or "Truman Bot"
        git_user_email = normalized_author_email or "truman@quinth.se"
        git_identity_config_line = ""
        if normalized_author_name and normalized_author_email:
            git_identity_config_line += (
                'git -C "${TARGET_DIR}" config author.name "${GIT_AUTHOR_NAME}"\n'
                'git -C "${TARGET_DIR}" config author.email "${GIT_AUTHOR_EMAIL}"\n'
                'git -C "${TARGET_DIR}" config agent-sandbox.gitAuthorName "${GIT_AUTHOR_NAME}"\n'
                'git -C "${TARGET_DIR}" config agent-sandbox.gitAuthorEmail "${GIT_AUTHOR_EMAIL}"\n'
            )
        if normalized_committer_name and normalized_committer_email:
            git_identity_config_line += (
                'git -C "${TARGET_DIR}" config committer.name "${GIT_COMMITTER_NAME}"\n'
                'git -C "${TARGET_DIR}" config committer.email "${GIT_COMMITTER_EMAIL}"\n'
                'git -C "${TARGET_DIR}" config agent-sandbox.gitCommitterName "${GIT_COMMITTER_NAME}"\n'
                'git -C "${TARGET_DIR}" config agent-sandbox.gitCommitterEmail "${GIT_COMMITTER_EMAIL}"\n'
            )
        remote_script = f"""
set -euo pipefail
. ~/.profile
TARGET_DIR="{guest_repo_root}"
REMOTE_URL="{remote_url}"
CANONICAL_REMOTE_URL="{canonical_remote_url}"
SESSION_ID="{session_id}"
VM_NAME="{vm_name}"
PR_NOTES_REF="{pr_notes_ref}"
GIT_USER_NAME={shlex.quote(git_user_name)}
GIT_USER_EMAIL={shlex.quote(git_user_email)}
GIT_AUTHOR_NAME={shlex.quote(normalized_author_name)}
GIT_AUTHOR_EMAIL={shlex.quote(normalized_author_email)}
GIT_COMMITTER_NAME={shlex.quote(normalized_committer_name)}
GIT_COMMITTER_EMAIL={shlex.quote(normalized_committer_email)}
AGENT_PR_NOTE_HELPER_B64="{agent_pr_note_helper_b64}"
AGENT_PUSH_HELPER_B64="{agent_push_helper_b64}"
SYNC_BASE_HELPER_B64="{sync_base_helper_b64}"
WORK_BRANCH="work"
{clone_branch_line}\
if [[ -e "${{TARGET_DIR}}" ]]; then
  if [[ ! -d "${{TARGET_DIR}}/.git" ]]; then
    echo "Guest repo path exists but is not a Git worktree: ${{TARGET_DIR}}" >&2
    exit 1
  fi
  REPO_ALREADY_PREPARED=1
else
  REPO_ALREADY_PREPARED=0
  {clone_command}
fi
if [[ -n "${{CANONICAL_REMOTE_URL}}" ]]; then
  if git -C "${{TARGET_DIR}}" remote get-url origin >/dev/null 2>&1; then
    git -C "${{TARGET_DIR}}" remote set-url origin "${{CANONICAL_REMOTE_URL}}"
  else
    git -C "${{TARGET_DIR}}" remote add origin "${{CANONICAL_REMOTE_URL}}"
  fi
  git -C "${{TARGET_DIR}}" config "url.${{REMOTE_URL}}.insteadOf" "${{CANONICAL_REMOTE_URL}}"
  if git -C "${{TARGET_DIR}}" remote get-url agent >/dev/null 2>&1; then
    git -C "${{TARGET_DIR}}" remote set-url agent "${{REMOTE_URL}}"
  else
    git -C "${{TARGET_DIR}}" remote add agent "${{REMOTE_URL}}"
  fi
else
  if git -C "${{TARGET_DIR}}" remote get-url origin >/dev/null 2>&1; then
    git -C "${{TARGET_DIR}}" remote set-url origin "${{REMOTE_URL}}"
  else
    git -C "${{TARGET_DIR}}" remote add origin "${{REMOTE_URL}}"
  fi
  if git -C "${{TARGET_DIR}}" remote get-url agent >/dev/null 2>&1; then
    git -C "${{TARGET_DIR}}" remote set-url agent "${{REMOTE_URL}}"
  else
    git -C "${{TARGET_DIR}}" remote add agent "${{REMOTE_URL}}"
  fi
fi
git -C "${{TARGET_DIR}}" config user.name "${{GIT_USER_NAME}}"
git -C "${{TARGET_DIR}}" config user.email "${{GIT_USER_EMAIL}}"
BASE_BRANCH="${{CLONE_BRANCH:-}}"
if [[ -z "${{BASE_BRANCH}}" ]]; then
  BASE_BRANCH="$(git -C "${{TARGET_DIR}}" branch --show-current || true)"
fi
if [[ -n "${{BASE_BRANCH}}" ]]; then
  git -C "${{TARGET_DIR}}" config agent-sandbox.baseBranch "${{BASE_BRANCH}}"
fi
if [[ "${{REPO_ALREADY_PREPARED}}" == "0" ]]; then
  git -C "${{TARGET_DIR}}" config agent-sandbox.workBranch "${{WORK_BRANCH}}"
  CURRENT_BRANCH="$(git -C "${{TARGET_DIR}}" branch --show-current || true)"
  if [[ "${{CURRENT_BRANCH}}" != "${{WORK_BRANCH}}" ]]; then
    if git -C "${{TARGET_DIR}}" show-ref --verify --quiet "refs/heads/${{WORK_BRANCH}}"; then
      git -C "${{TARGET_DIR}}" switch "${{WORK_BRANCH}}"
    else
      git -C "${{TARGET_DIR}}" switch -c "${{WORK_BRANCH}}"
    fi
  fi
else
  if ! git -C "${{TARGET_DIR}}" config --get agent-sandbox.workBranch >/dev/null 2>&1; then
    CURRENT_BRANCH="$(git -C "${{TARGET_DIR}}" branch --show-current || true)"
    if [[ -n "${{CURRENT_BRANCH}}" ]]; then
      git -C "${{TARGET_DIR}}" config agent-sandbox.workBranch "${{CURRENT_BRANCH}}"
    else
      git -C "${{TARGET_DIR}}" config agent-sandbox.workBranch "${{WORK_BRANCH}}"
    fi
  fi
fi
git -C "${{TARGET_DIR}}" config agent-sandbox.sessionId "${{SESSION_ID}}"
git -C "${{TARGET_DIR}}" config agent-sandbox.prNotesRef "${{PR_NOTES_REF}}"
{issue_config_line}{branch_suffix_line}{git_identity_config_line}install -d -m 0755 "${{TARGET_DIR}}/.agent-sandbox/bin"
python3 - "${{TARGET_DIR}}/.agent-sandbox/bin/agent-pr-note" "${{AGENT_PR_NOTE_HELPER_B64}}" <<'PY'
import base64
import os
import sys
import logging
from pathlib import Path

target_path = Path(sys.argv[1])
target_path.write_bytes(base64.b64decode(sys.argv[2]))
os.chmod(target_path, 0o755)
PY
python3 - "${{TARGET_DIR}}/.agent-sandbox/bin/push-agent" "${{AGENT_PUSH_HELPER_B64}}" <<'PY'
import base64
import os
import sys
from pathlib import Path

target_path = Path(sys.argv[1])
target_path.write_bytes(base64.b64decode(sys.argv[2]))
os.chmod(target_path, 0o755)
PY
python3 - "${{TARGET_DIR}}/.agent-sandbox/bin/sync-base" "${{SYNC_BASE_HELPER_B64}}" <<'PY'
import base64
import os
import sys
from pathlib import Path

target_path = Path(sys.argv[1])
target_path.write_bytes(base64.b64decode(sys.argv[2]))
os.chmod(target_path, 0o755)
PY
git -C "${{TARGET_DIR}}" config alias.agent-pr-note '!f(){{ repo_root=$(git rev-parse --show-toplevel); "${{repo_root}}/.agent-sandbox/bin/agent-pr-note" "$@"; }}; f'
git -C "${{TARGET_DIR}}" config alias.push-agent '!f(){{ repo_root=$(git rev-parse --show-toplevel); "${{repo_root}}/.agent-sandbox/bin/push-agent" "$@"; }}; f'
git -C "${{TARGET_DIR}}" config alias.sync-base '!f(){{ repo_root=$(git rev-parse --show-toplevel); "${{repo_root}}/.agent-sandbox/bin/sync-base" "$@"; }}; f'
"""
        ssh_result = process_helpers.run_subprocess(
            [
                "ssh",
                *vm_helpers.ssh_options(vm_name, vm_ip, ssh_private_key_path),
                f"{vm_user}@{vm_ip}",
                "bash",
                "-s",
            ],
            capture_output=True,
            text=True,
            timeout=60.0,
            input=remote_script,
        )
        if ssh_result.returncode != 0:
            raise RuntimeError(
                (ssh_result.stderr or ssh_result.stdout or "").strip()
                or f"Failed to clone repo into {vm_name}"
            )
        return guest_repo_root


class VmAgentAdminService:
    def __init__(self, dependencies: VmAgentAdminDependencies) -> None:
        self._deps = dependencies
        self._assistant_session_repo = JsonChatAssistantSessionRepository(
            Path(self._deps.config.runtime_dir) / "chat-assistant-session-mappings.json"
        )
        self._operations_admin: Any | None = None
        self._skill_admin: Any | None = None
        self._workflow_config_admin: Any | None = None
        self._directory_admin: Any | None = None
        self._skill_library: Any | None = None
        self._agent_create_operations: Any | None = None
        self._agent_create_operation_owner_id_value: str | None = None
        self._agent_create_operation_payloads_value: dict[str, dict[str, Any]] | None = None
        self._agent_create_operation_payloads_lock_value: Any | None = None
        self._agent_create_executor_value: Any | None = None
        self._execution_backend_instance: Any | None = None
        self._project_dir_provider: Any | None = None

    def configure_admin_collaborators(
        self,
        *,
        operations_admin: Any | None = None,
        skill_admin: Any | None = None,
        workflow_config_admin: Any | None = None,
        directory_admin: Any | None = None,
        skill_library: Any | None = None,
        agent_create_operations: Any | None = None,
        agent_create_operation_owner_id: str | None = None,
        agent_create_operation_payloads: dict[str, dict[str, Any]] | None = None,
        agent_create_operation_payloads_lock: Any | None = None,
        agent_create_executor: Any | None = None,
        project_dir_provider: Any | None = None,
    ) -> None:
        self._operations_admin = operations_admin
        self._skill_admin = skill_admin
        self._workflow_config_admin = workflow_config_admin
        self._directory_admin = directory_admin
        self._skill_library = skill_library
        self._agent_create_operations = agent_create_operations
        self._agent_create_operation_owner_id_value = agent_create_operation_owner_id
        self._agent_create_operation_payloads_value = agent_create_operation_payloads
        self._agent_create_operation_payloads_lock_value = agent_create_operation_payloads_lock
        self._agent_create_executor_value = agent_create_executor
        self._project_dir_provider = project_dir_provider

    @property
    def config(self) -> Any:
        return self._deps.config

    @property
    def runtime(self) -> Any:
        return self._deps.runtime

    @property
    def project_dir(self) -> Path:
        if self._project_dir_provider is not None:
            return Path(self._project_dir_provider())
        return self._deps.project_dir

    @property
    def directory_admin(self) -> Any:
        if self._directory_admin is None:
            raise RuntimeError("VmAgentAdminService directory_admin collaborator is not configured")
        return self._directory_admin

    @property
    def operations_admin(self) -> Any:
        if self._operations_admin is None:
            raise RuntimeError(
                "VmAgentAdminService operations_admin collaborator is not configured"
            )
        return self._operations_admin

    @property
    def skill_admin(self) -> Any:
        if self._skill_admin is None:
            raise RuntimeError("VmAgentAdminService skill_admin collaborator is not configured")
        return self._skill_admin

    @property
    def workflow_config_admin(self) -> Any:
        return self._workflow_config_admin or self._deps.workflow_config

    @property
    def skill_library(self) -> Any:
        if self._skill_library is not None:
            return self._skill_library
        return SkillLibraryRepository(self.config.runtime_dir)

    @property
    def agent_create_operations(self) -> Any:
        if self._agent_create_operations is None:
            raise RuntimeError(
                "VmAgentAdminService agent_create_operations store is not configured"
            )
        return self._agent_create_operations

    @property
    def _agent_create_operation_owner_id(self) -> str:
        return self._agent_create_operation_owner_id_value or "vm-agent-admin"

    @property
    def _agent_create_operation_payloads(self) -> dict[str, dict[str, Any]]:
        if self._agent_create_operation_payloads_value is None:
            self._agent_create_operation_payloads_value = {}
        return self._agent_create_operation_payloads_value

    @property
    def _agent_create_operation_payloads_lock(self) -> Any:
        if self._agent_create_operation_payloads_lock_value is None:
            raise RuntimeError("VmAgentAdminService agent create payload lock is not configured")
        return self._agent_create_operation_payloads_lock_value

    @property
    def _agent_create_executor(self) -> Any:
        if self._agent_create_executor_value is None:
            raise RuntimeError("VmAgentAdminService agent create executor is not configured")
        return self._agent_create_executor_value

    def validate_managed_codex_skills(
        self, skill_names: list[str] | tuple[str, ...] | None
    ) -> list[str]:
        if self._skill_admin is not None:
            return self._skill_admin.validate_managed_codex_skills(skill_names)
        names = normalize_skill_names(skill_names)
        for name in names:
            if not self.skill_library.has_skill(name):
                raise ValueError(f"Unknown Codex skill: {name}")
        return names

    def resolve_container_codex_skill_files(
        self, skill_names: list[str] | tuple[str, ...] | None
    ) -> list[dict[str, Any]]:
        if self._skill_admin is not None:
            return self._skill_admin.resolve_container_codex_skill_files(skill_names)
        names = self.validate_managed_codex_skills(skill_names)
        return self.skill_library.container_file_specs(names)

    def effective_execution_backend(self, directory_path: str | None = None) -> str:
        resolved_config = self.runtime.provider_config.resolve_for_directory(directory_path)
        return str(resolved_config.execution_backend or "libvirt_vm")

    def execution_backend(self):
        backend = self._execution_backend_instance
        if backend is not None:
            return backend
        selected_backend = ExecutionBackendKind(str(self.config.execution_backend))
        if selected_backend == ExecutionBackendKind.LIBVIRT_VM:
            backend = LibvirtVmExecutionBackend(vm_admin_service=self)
        elif selected_backend == ExecutionBackendKind.CONTAINER_WORKER_VM:
            if not self.config.container_worker_base_url:
                raise ValueError(
                    "execution_backend 'container_worker_vm' requires container_worker_base_url."
                )
            backend = ContainerWorkerVmExecutionBackend(config=self.config)
        else:
            raise ValueError(f"Unsupported execution backend: {selected_backend.value}")
        self._execution_backend_instance = backend
        return backend

    def _stable_json_revision(self, payload: object) -> str:
        if hasattr(self.workflow_config_admin, "_stable_json_revision"):
            return self.workflow_config_admin._stable_json_revision(payload)
        data = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode(
            "utf-8"
        )
        return hashlib.sha256(data).hexdigest()

    def _communication_notification_target_options(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._communication_notification_target_options(
            *args, **kwargs
        )

    def _agent_communication_target_options(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._agent_communication_target_options(*args, **kwargs)

    def _workflow_run_store_paths(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._workflow_run_store_paths(*args, **kwargs)

    def _workflow_uses_sqlite_store(self, *args: Any, **kwargs: Any):
        return self.workflow_config_admin._workflow_uses_sqlite_store(*args, **kwargs)

    # VM/agent/container domain methods moved from AdminService.
    def _session_ids_by_vm(self) -> dict[str, list[str]]:
        mapping: dict[str, list[str]] = {}
        for session_id, session in self.runtime.session_repository.read_all().items():
            if session.vm_id:
                mapping.setdefault(session.vm_id, []).append(session_id)
        for session_ids in mapping.values():
            session_ids.sort()
        return mapping

    def _directory_paths_by_vm(self) -> dict[str, list[str]]:
        mapping: dict[str, set[str]] = {}
        for session in self.runtime.session_repository.list_sessions():
            if session.vm_id:
                mapping.setdefault(session.vm_id, set()).add(session.directory_path)
        return {vm_id: sorted(paths) for vm_id, paths in mapping.items()}

    def _labels_by_vm(self) -> dict[str, dict[str, str]]:
        mapping: dict[str, dict[str, str] | None] = {}
        sessions = self.runtime.session_repository.read_all()
        for session_id in sorted(sessions):
            session = sessions[session_id]
            if not session.vm_id:
                continue
            labels = _normalize_session_labels(session.metadata.get("labels"))
            current = mapping.get(session.vm_id)
            if current is None:
                mapping[session.vm_id] = dict(labels)
                continue
            mapping[session.vm_id] = {
                key: value for key, value in current.items() if labels.get(key) == value
            }
        return {vm_id: labels for vm_id, labels in mapping.items() if labels}

    def _normalize_directory_path(self, directory_path: str | None) -> str:
        if not directory_path:
            return "root"
        result = self.directory_admin.get(directory_path)
        if not result.ok or result.data is None:
            return "root"
        return result.data.details.directory.path

    def _resolve_vm_defaults(self, directory_path: str) -> EffectiveVmDefaultsConfig:
        resolver = VmDefaultsConfigResolver(
            self.config,
            directory_store=self.runtime.directories,
            session_store=self.runtime.session_repository,
        )
        return resolver.resolve_for_directory(directory_path)

    def _resolve_issue_setup(
        self, directory_path: str, *, root_dir: Path
    ) -> EffectiveIssueSetupConfig:
        return resolve_effective_issue_setup_config(
            config=self.config,
            runtime=self.runtime,
            directory_path=directory_path,
            root_dir=root_dir,
        )

    def _resolve_agent_model_for_session(self, session_or_id: SessionManifest | str) -> str:
        if isinstance(session_or_id, SessionManifest):
            session = session_or_id
            session_id = session.session_id
        else:
            session_id = str(session_or_id).strip()
            if not session_id:
                raise ValueError(
                    "Cannot resolve AGENT_MODEL: missing session id. Provide a valid session id or SessionManifest."
                )
            session = self.runtime.session_repository.get_session(session_id)
            if session is None:
                raise ValueError(
                    f"Cannot resolve AGENT_MODEL for session '{session_id}': session not found. "
                    "Remediation: create the session first or pass a SessionManifest with session.model set."
                )
        issue_setup = self._resolve_issue_setup(session.directory_path, root_dir=self.project_dir)
        return resolve_agent_model_for_manifest(session, issue_setup=issue_setup)

    def resolve_agent_model_for_target(self, target: str) -> str:
        session = self.runtime.session_repository.resolve_session_target(target)
        if session is None:
            raise ValueError(f"Unknown session target: {target}")
        return self._resolve_agent_model_for_session(session.session_id)

    def _resolve_repo_source(self, repo_source: str) -> tuple[str, str]:
        resolve_script = self.project_dir / "scripts" / "resolve_repo_source.sh"
        path_result = _run_subprocess(
            [str(resolve_script), "--path-only", repo_source], capture_output=True, text=True
        )
        if path_result.returncode != 0:
            raise RuntimeError(
                (path_result.stderr or path_result.stdout or "").strip()
                or f"Failed to resolve repo source: {repo_source}"
            )
        name_result = _run_subprocess(
            [str(resolve_script), "--name-only", repo_source], capture_output=True, text=True
        )
        if name_result.returncode != 0:
            raise RuntimeError(
                (name_result.stderr or name_result.stdout or "").strip()
                or f"Failed to determine repo name: {repo_source}"
            )
        return (path_result.stdout or "").strip(), (name_result.stdout or "").strip()

    def _sync_repo_into_bare_remote(
        self,
        repo_name: str,
        resolved_repo_path: str,
        *,
        issue_setup: EffectiveIssueSetupConfig,
        repo_source: str | None = None,
    ) -> Path:
        create_remote_script = self.project_dir / "scripts" / "create_git_remote.sh"
        completed = _run_subprocess(
            [str(create_remote_script), repo_name, "agents"],
            capture_output=True,
            text=True,
            env=os.environ
            | {
                "AGENT_SANDBOX_GIT_ROOT": issue_setup.git_root,
                "AGENT_SANDBOX_GIT_HOST": issue_setup.git_host,
                "AGENT_SANDBOX_GIT_USER": issue_setup.git_user,
                "AGENT_SANDBOX_GIT_SSH_ALIAS": issue_setup.git_ssh_alias,
            },
        )
        if completed.returncode != 0:
            raise RuntimeError(
                (completed.stderr or completed.stdout or "").strip()
                or f"Failed to create git remote for {repo_name}"
            )
        bare_repo_path = Path(issue_setup.git_root) / f"{repo_name}.git"
        fetch_result = _run_subprocess(
            [
                "git",
                f"--git-dir={bare_repo_path}",
                "fetch",
                "--prune",
                resolved_repo_path,
                "+refs/heads/*:refs/heads/*",
                "+refs/tags/*:refs/tags/*",
                *_PROTECTED_MIRROR_HEAD_REFSPECS,
            ],
            capture_output=True,
            text=True,
        )
        if fetch_result.returncode != 0:
            raise RuntimeError(
                (fetch_result.stderr or fetch_result.stdout or "").strip()
                or f"Failed to mirror repo into {bare_repo_path}"
            )
        trusted_repo_result = _run_subprocess(
            [
                "git",
                f"--git-dir={bare_repo_path}",
                "config",
                "agent-sandbox.trustedRepoPath",
                resolved_repo_path,
            ],
            capture_output=True,
            text=True,
        )
        if trusted_repo_result.returncode != 0:
            raise RuntimeError(
                (trusted_repo_result.stderr or trusted_repo_result.stdout or "").strip()
                or f"Failed to record trusted repo path for {bare_repo_path}"
            )
        mirror_source = (repo_source or resolved_repo_path).strip()
        mirror_source_result = _run_subprocess(
            [
                "git",
                f"--git-dir={bare_repo_path}",
                "config",
                "agent-sandbox.mirrorSource",
                mirror_source,
            ],
            capture_output=True,
            text=True,
        )
        if mirror_source_result.returncode != 0:
            raise RuntimeError(
                (mirror_source_result.stderr or mirror_source_result.stdout or "").strip()
                or f"Failed to record mirror source for {bare_repo_path}"
            )
        mirror_remote_name = _mirror_remote_name_for_repo(resolved_repo_path)
        mirror_remote_result = _run_subprocess(
            [
                "git",
                f"--git-dir={bare_repo_path}",
                "config",
                "agent-sandbox.mirrorRemoteName",
                mirror_remote_name,
            ],
            capture_output=True,
            text=True,
        )
        if mirror_remote_result.returncode != 0:
            raise RuntimeError(
                (mirror_remote_result.stderr or mirror_remote_result.stdout or "").strip()
                or f"Failed to record mirror remote name for {bare_repo_path}"
            )
        mirror_sync_result = _run_subprocess(
            ["git", f"--git-dir={bare_repo_path}", "config", "agent-sandbox.mirrorSyncRemote", "1"],
            capture_output=True,
            text=True,
        )
        if mirror_sync_result.returncode != 0:
            raise RuntimeError(
                (mirror_sync_result.stderr or mirror_sync_result.stdout or "").strip()
                or f"Failed to record mirror sync policy for {bare_repo_path}"
            )
        mirror_force_local_result = _run_subprocess(
            ["git", f"--git-dir={bare_repo_path}", "config", "agent-sandbox.mirrorForceLocal", "0"],
            capture_output=True,
            text=True,
        )
        if mirror_force_local_result.returncode != 0:
            raise RuntimeError(
                (mirror_force_local_result.stderr or mirror_force_local_result.stdout or "").strip()
                or f"Failed to record mirror force-local policy for {bare_repo_path}"
            )
        canonical_origin_url = _resolve_canonical_origin_url_for_repo(
            resolved_repo_path, mirror_remote_name
        )
        if canonical_origin_url:
            canonical_config_result = _run_subprocess(
                [
                    "git",
                    f"--git-dir={bare_repo_path}",
                    "config",
                    "agent-sandbox.canonicalOriginUrl",
                    canonical_origin_url,
                ],
                capture_output=True,
                text=True,
            )
            if canonical_config_result.returncode != 0:
                raise RuntimeError(
                    (canonical_config_result.stderr or canonical_config_result.stdout or "").strip()
                    or f"Failed to record canonical origin URL for {bare_repo_path}"
                )
        default_base_branch = _default_base_branch_for_repo_remote(
            resolved_repo_path, mirror_remote_name
        )
        if default_base_branch:
            base_branch_config_result = _run_subprocess(
                [
                    "git",
                    f"--git-dir={bare_repo_path}",
                    "config",
                    "agent-sandbox.baseBranch",
                    default_base_branch,
                ],
                capture_output=True,
                text=True,
            )
            if base_branch_config_result.returncode != 0:
                raise RuntimeError(
                    (
                        base_branch_config_result.stderr or base_branch_config_result.stdout or ""
                    ).strip()
                    or f"Failed to record base branch for {bare_repo_path}"
                )
            symbolic_ref_result = _run_subprocess(
                [
                    "git",
                    f"--git-dir={bare_repo_path}",
                    "symbolic-ref",
                    "HEAD",
                    f"refs/heads/{default_base_branch}",
                ],
                capture_output=True,
                text=True,
            )
            if symbolic_ref_result.returncode != 0:
                raise RuntimeError(
                    (symbolic_ref_result.stderr or symbolic_ref_result.stdout or "").strip()
                    or f"Failed to set HEAD for {bare_repo_path}"
                )
        return bare_repo_path

    def _clone_repo_into_vm(
        self,
        *,
        vm_name: str,
        vm_user: str,
        session_id: str,
        repo_name: str,
        issue_setup: EffectiveIssueSetupConfig,
        repo_source: str | None = None,
        clone_branch: str | None = None,
        issue_number: str | None = None,
        vm_ip: str | None = None,
    ) -> str:
        bootstrap_git_script = self.project_dir / "scripts" / "bootstrap_git_vm.sh"
        completed = _run_subprocess(
            [str(bootstrap_git_script), vm_name, vm_user],
            capture_output=True,
            text=True,
            env=os.environ
            | {
                "AGENT_SANDBOX_GIT_ROOT": issue_setup.git_root,
                "AGENT_SANDBOX_GIT_HOST": issue_setup.git_host,
                "AGENT_SANDBOX_GIT_USER": issue_setup.git_user,
                "AGENT_SANDBOX_GIT_SSH_ALIAS": issue_setup.git_ssh_alias,
            },
        )
        if completed.returncode != 0:
            raise RuntimeError(
                (completed.stderr or completed.stdout or "").strip()
                or f"Failed to bootstrap guest git for {vm_name}"
            )

        vm_ip = vm_ip or _resolve_vm_ip(vm_name)
        ssh_private_key_path = _resolve_private_key_path()
        if not vm_ip or ssh_private_key_path is None:
            raise RuntimeError(f"Unable to resolve SSH access for {vm_name}")

        remote_url = f"ssh://{issue_setup.git_ssh_alias}{issue_setup.git_root}/{repo_name}.git"
        pr_notes_ref = agent_pr_notes_ref_for_session(session_id)
        canonical_remote_url = (
            repo_source.strip() if repo_source and _is_remote_git_source(repo_source) else ""
        )
        guest_repo_root = f"/home/{vm_user}/{repo_name}"
        agent_pr_note_helper_b64 = _guest_agent_pr_note_helper_b64(self.project_dir)
        agent_push_helper_b64 = _guest_push_agent_helper_b64(self.project_dir)
        sync_base_helper_b64 = _guest_sync_base_helper_b64(self.project_dir)
        clone_branch_line = ""
        clone_command = 'git clone "${REMOTE_URL}" "${TARGET_DIR}"'
        if clone_branch:
            clone_command = 'git clone --branch "${CLONE_BRANCH}" "${REMOTE_URL}" "${TARGET_DIR}"'
            clone_branch_line = f'CLONE_BRANCH="{clone_branch}"\n'
        issue_config_line = ""
        if issue_number:
            issue_config_line = (
                f'git -C "${{TARGET_DIR}}" config agent-sandbox.issueNumber "{issue_number}"\n'
                f'git -C "${{TARGET_DIR}}" config agent-sandbox.destinationBranch "feature/issue-{issue_number}"\n'
            )
        branch_suffix_line = (
            'git -C "${TARGET_DIR}" config agent-sandbox.branchSuffix "${VM_NAME}"\n'
        )
        remote_script = f"""
set -euo pipefail
. ~/.profile
TARGET_DIR="{guest_repo_root}"
REMOTE_URL="{remote_url}"
CANONICAL_REMOTE_URL="{canonical_remote_url}"
SESSION_ID="{session_id}"
VM_NAME="{vm_name}"
PR_NOTES_REF="{pr_notes_ref}"
AGENT_PR_NOTE_HELPER_B64="{agent_pr_note_helper_b64}"
AGENT_PUSH_HELPER_B64="{agent_push_helper_b64}"
SYNC_BASE_HELPER_B64="{sync_base_helper_b64}"
WORK_BRANCH="work"
{clone_branch_line}\
if [[ -e "${{TARGET_DIR}}" ]]; then
  echo "Guest repo path already exists: ${{TARGET_DIR}}" >&2
  exit 1
fi
{clone_command}
if [[ -n "${{CANONICAL_REMOTE_URL}}" ]]; then
  git -C "${{TARGET_DIR}}" remote set-url origin "${{CANONICAL_REMOTE_URL}}"
  git -C "${{TARGET_DIR}}" config "url.${{REMOTE_URL}}.insteadOf" "${{CANONICAL_REMOTE_URL}}"
  git -C "${{TARGET_DIR}}" remote add agent "${{REMOTE_URL}}"
else
  git -C "${{TARGET_DIR}}" remote add agent "${{REMOTE_URL}}"
fi
git -C "${{TARGET_DIR}}" config user.name "Truman Bot"
git -C "${{TARGET_DIR}}" config user.email "truman@quinth.se"
BASE_BRANCH="${{CLONE_BRANCH:-}}"
if [[ -z "${{BASE_BRANCH}}" ]]; then
  BASE_BRANCH="$(git -C "${{TARGET_DIR}}" branch --show-current || true)"
fi
if [[ -n "${{BASE_BRANCH}}" ]]; then
  git -C "${{TARGET_DIR}}" config agent-sandbox.baseBranch "${{BASE_BRANCH}}"
fi
git -C "${{TARGET_DIR}}" config agent-sandbox.workBranch "${{WORK_BRANCH}}"
CURRENT_BRANCH="$(git -C "${{TARGET_DIR}}" branch --show-current || true)"
if [[ "${{CURRENT_BRANCH}}" != "${{WORK_BRANCH}}" ]]; then
  if git -C "${{TARGET_DIR}}" show-ref --verify --quiet "refs/heads/${{WORK_BRANCH}}"; then
    git -C "${{TARGET_DIR}}" switch "${{WORK_BRANCH}}"
  else
    git -C "${{TARGET_DIR}}" switch -c "${{WORK_BRANCH}}"
  fi
fi
git -C "${{TARGET_DIR}}" config agent-sandbox.sessionId "${{SESSION_ID}}"
git -C "${{TARGET_DIR}}" config agent-sandbox.prNotesRef "${{PR_NOTES_REF}}"
{issue_config_line}{branch_suffix_line}install -d -m 0755 "${{TARGET_DIR}}/.agent-sandbox/bin"
python3 - "${{TARGET_DIR}}/.agent-sandbox/bin/agent-pr-note" "${{AGENT_PR_NOTE_HELPER_B64}}" <<'PY'
import base64
import os
import sys
from pathlib import Path

target_path = Path(sys.argv[1])
target_path.write_bytes(base64.b64decode(sys.argv[2]))
os.chmod(target_path, 0o755)
PY
python3 - "${{TARGET_DIR}}/.agent-sandbox/bin/push-agent" "${{AGENT_PUSH_HELPER_B64}}" <<'PY'
import base64
import os
import sys
from pathlib import Path

target_path = Path(sys.argv[1])
target_path.write_bytes(base64.b64decode(sys.argv[2]))
os.chmod(target_path, 0o755)
PY
python3 - "${{TARGET_DIR}}/.agent-sandbox/bin/sync-base" "${{SYNC_BASE_HELPER_B64}}" <<'PY'
import base64
import os
import sys
from pathlib import Path

target_path = Path(sys.argv[1])
target_path.write_bytes(base64.b64decode(sys.argv[2]))
os.chmod(target_path, 0o755)
PY
git -C "${{TARGET_DIR}}" config alias.agent-pr-note '!f(){{ repo_root=$(git rev-parse --show-toplevel); "${{repo_root}}/.agent-sandbox/bin/agent-pr-note" "$@"; }}; f'
git -C "${{TARGET_DIR}}" config alias.push-agent '!f(){{ repo_root=$(git rev-parse --show-toplevel); "${{repo_root}}/.agent-sandbox/bin/push-agent" "$@"; }}; f'
git -C "${{TARGET_DIR}}" config alias.sync-base '!f(){{ repo_root=$(git rev-parse --show-toplevel); "${{repo_root}}/.agent-sandbox/bin/sync-base" "$@"; }}; f'
"""
        ssh_result = _run_subprocess(
            [
                "ssh",
                *_ssh_options(vm_name, vm_ip, ssh_private_key_path),
                f"{vm_user}@{vm_ip}",
                "bash",
                "-s",
            ],
            capture_output=True,
            text=True,
            timeout=60.0,
            input=remote_script,
        )
        if ssh_result.returncode != 0:
            raise RuntimeError(
                (ssh_result.stderr or ssh_result.stdout or "").strip()
                or f"Failed to clone repo into {vm_name}"
            )
        return guest_repo_root

    def list_sessions(
        self,
        *,
        include_expired: bool = False,
        expired_only: bool = False,
        directory_path: str | None = None,
        direct_only: bool = False,
    ) -> list[SessionSummary]:
        summaries = self.operations_admin.list_sessions(
            include_expired=include_expired,
            expired_only=expired_only,
            directory_path=directory_path,
            direct_only=direct_only,
        )
        return [self._session_summary_with_container_lifecycle(summary) for summary in summaries]

    def delete_session(self, target: str) -> SessionActionResult:
        sessions = {
            session.session_id: session
            for session in self.runtime.session_repository.list_sessions()
        }
        session_id, manifest = _resolve_session_target(target, sessions)
        associated_agent = self._session_associated_agent_description(session_id, manifest)
        if associated_agent:
            raise ValueError(
                f"Session {session_id!r} is associated with {associated_agent}. "
                "Delete the agent instead so backend cleanup can run."
            )

        worker_session_deleted, removed_mappings = self._delete_detached_worker_session_state(
            session_id
        )
        result = self.operations_admin.delete_session(session_id)
        removed_approvals = _prune_approvals_for_sessions(self.runtime, session_ids={session_id})
        removed_assistant_mappings = self._delete_chat_assistant_session_mappings({session_id})
        cleanup_parts: list[str] = []
        if worker_session_deleted:
            cleanup_parts.append("worker_session_deleted=1")
        if removed_mappings:
            cleanup_parts.append(f"removed_mappings={removed_mappings}")
        if removed_approvals:
            cleanup_parts.append(f"removed_approvals={removed_approvals}")
        if removed_assistant_mappings:
            cleanup_parts.append(f"removed_assistant_mappings={removed_assistant_mappings}")
        if cleanup_parts:
            result.message = f"{result.message}; " + " ".join(cleanup_parts)
        return result

    def read_session_artifact_text(
        self,
        *,
        session_id: str,
        path: str,
        vm_user: str = "agent",
    ) -> dict[str, Any]:
        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            raise ValueError("session_id is required")
        relative_path = normalize_artifact_relative_path(path)
        session = self.runtime.session_repository.get_session(normalized_session_id)
        if session is None:
            raise ValueError(f"Unknown session: {normalized_session_id}")

        metadata = session.metadata if isinstance(session.metadata, Mapping) else {}
        provider = str(
            metadata.get("execution_backend")
            or session.vm_provider
            or metadata.get("vm_provider")
            or ""
        ).strip()
        is_container_session = provider == "container_worker_vm"
        if is_container_session:
            worker_url = self._container_worker_url_for_target(session=session)
            backend = self._container_worker_backend(
                directory_path=session.directory_path,
                worker_url=worker_url,
            )
            content = backend.read_session_artifact_text(
                session_id=normalized_session_id,
                relative_path=relative_path,
            )
            return {
                "session_id": normalized_session_id,
                "path": relative_path,
                "content": content,
                "byte_count": len(content.encode("utf-8")),
                "artifacts_root": DEFAULT_AGENT_ARTIFACTS_DIR,
                "execution_backend": "container_worker_vm",
            }

        result = self.vm_exec(
            normalized_session_id,
            command=_VM_ARTIFACT_TEXT_READ_COMMAND,
            vm_user=(str(vm_user or "").strip() or "agent"),
            environment={"AGENT_SANDBOX_ARTIFACT_RELATIVE_PATH": relative_path},
            timeout_seconds=30.0,
            check=True,
        )
        if not bool(result.get("success")):
            raise RuntimeError(
                str(
                    result.get("stderr") or result.get("stdout") or result.get("message") or ""
                ).strip()
                or "VM artifact read failed"
            )
        content = str(result.get("stdout") or "")
        return {
            "session_id": normalized_session_id,
            "path": relative_path,
            "content": content,
            "byte_count": len(content.encode("utf-8")),
            "artifacts_root": DEFAULT_AGENT_ARTIFACTS_DIR,
            "execution_backend": "libvirt_vm",
            "vm_name": result.get("vm_name") or session.vm_id,
            "vm_provider": session.vm_provider,
        }

    def cleanup_container_session(self, session_id: str) -> SessionActionResult:
        normalized_session_id = str(session_id or "").strip()
        if not normalized_session_id:
            raise ValueError("session_id is required")
        session = self.runtime.session_repository.get_session(normalized_session_id)
        if session is None:
            raise ValueError(f"Unknown session: {normalized_session_id}")
        if not self._is_container_session(session):
            raise ValueError(f"Session {normalized_session_id!r} is not a container worker session")

        lifecycle = self._container_session_lifecycle(session)
        if lifecycle is None or not lifecycle.actions.cleanup.supported:
            reason = (
                lifecycle.actions.cleanup.reason
                if lifecycle is not None and lifecycle.actions.cleanup.reason
                else "Cleanup is not currently supported for this session."
            )
            raise ValueError(reason)

        execution_records = self._container_execution_records_for_session(normalized_session_id)
        worker_backend = self._container_worker_backend(
            directory_path=session.directory_path,
            worker_url=self._container_worker_url_for_target(
                session=session,
                records=execution_records,
            ),
        )
        attempted_at = _utc_now()
        try:
            worker_backend.delete_session(normalized_session_id)
        except ContainerWorkerClientError as exc:
            completed_at = _utc_now()
            sanitized_error = sanitize_text(str(exc))
            updated_session = self._container_session_lifecycle_tombstone(
                session,
                state=ContainerSessionLifecycleState.CLEANUP_FAILED,
                cleanup_status="cleanup_failed",
                attempted_at=attempted_at,
                completed_at=completed_at,
                cleanup_error=sanitized_error,
            )
            self.runtime.session_repository.upsert_session(updated_session)
            return SessionActionResult(
                success=False,
                session_id=normalized_session_id,
                action="cleanup_container_session",
                message=(
                    f"Cleanup failed for container worker session {normalized_session_id}: "
                    f"{sanitized_error}"
                ),
            )

        completed_at = _utc_now()
        updated_session = self._container_session_lifecycle_tombstone(
            session,
            state=ContainerSessionLifecycleState.CLEANED_UP,
            cleanup_status="cleanup_succeeded",
            attempted_at=attempted_at,
            completed_at=completed_at,
            cleanup_error=None,
        )
        self.runtime.session_repository.upsert_session(updated_session)
        return SessionActionResult(
            success=True,
            session_id=normalized_session_id,
            action="cleanup_container_session",
            message=f"Cleaned up container worker session {normalized_session_id}",
        )

    def _container_worker_execution_repository(self):
        return self.container_worker_execution_repository()

    @staticmethod
    def _container_worker_backend_url(backend: Any) -> str | None:
        client = getattr(backend, "client", None)
        client_url = str(getattr(client, "base_url", "") or "").strip()
        if client_url:
            return client_url
        config = getattr(backend, "config", None)
        config_url = str(getattr(config, "container_worker_base_url", "") or "").strip()
        return config_url or None

    @staticmethod
    def _container_worker_url_from_metadata(metadata: Any) -> str | None:
        return VmAgentAdminService.container_worker_url_from_metadata(metadata)

    def _container_worker_url_for_target(
        self,
        *,
        session: SessionManifest | None = None,
        record: Any | None = None,
        records: list[Any] | None = None,
    ) -> str | None:
        return self.container_worker_url_for_target(
            session=session,
            record=record,
            records=records,
        )

    @staticmethod
    def _agent_record_directory_path(directory_path: str | None) -> str:
        try:
            return DirectoryPath(segments=str(directory_path or "root")).as_posix()
        except ValueError:
            return "root"

    @classmethod
    def _agent_record_matches_directory(
        cls,
        record_directory_path: str | None,
        selected_directory_path: str,
        *,
        direct_only: bool,
    ) -> bool:
        record_path = cls._agent_record_directory_path(record_directory_path)
        selected_path = cls._agent_record_directory_path(selected_directory_path)
        if direct_only:
            return record_path == selected_path
        return record_path == selected_path or record_path.startswith(f"{selected_path}/")

    @staticmethod
    def _container_agent_status(last_status: str | None) -> str:
        normalized = str(last_status or "").strip().casefold()
        if normalized in {"created", "starting"}:
            return "starting"
        if normalized == "running":
            return "running"
        if normalized in {"stopped", "exited"}:
            return "stopped"
        if normalized == "failed":
            return "failed"
        if normalized == "removed":
            return "destroyed"
        return "unknown"

    @staticmethod
    def _container_codex_outcome(record: Any) -> Mapping[str, Any] | None:
        metadata = getattr(record, "metadata", None)
        if not isinstance(metadata, Mapping):
            return None
        outcome = metadata.get("codex_outcome")
        if not isinstance(outcome, Mapping):
            return None
        return outcome

    @classmethod
    def _container_execution_status(cls, record: Any) -> str:
        outcome = cls._container_codex_outcome(record)
        if outcome is not None:
            status = str(outcome.get("status") or "").strip().casefold()
            if status == "failed":
                return "failed"
        return cls._container_agent_status(getattr(record, "last_status", None))

    @staticmethod
    def _agent_status_is_active(status: str | None) -> bool:
        return str(status or "").strip().casefold() in {
            "preparing",
            "starting",
            "running",
        }

    @staticmethod
    def _container_execution_sort_key(record: Any) -> tuple[datetime, str]:
        timestamp = (
            getattr(record, "started_at", None)
            or getattr(record, "created_at", None)
            or getattr(record, "updated_at", None)
            or datetime.min.replace(tzinfo=UTC)
        )
        return timestamp, str(getattr(record, "execution_id", "") or "")

    @classmethod
    def _container_session_status(cls, records: list[Any]) -> str:
        if not records:
            return "prepared"
        statuses = [cls._container_execution_status(record) for record in records]
        if "running" in statuses:
            return "running"
        if "starting" in statuses:
            return "starting"
        latest = max(records, key=cls._container_execution_sort_key)
        latest_status = cls._container_execution_status(latest)
        if latest_status == "failed":
            return "failed"
        return "idle"

    @staticmethod
    def _normalize_agent_backend(backend: str | None) -> str:
        selected_backend = str(backend or "libvirt_vm").strip()
        if selected_backend in {"libvirt", "libvirt_vm"}:
            return "libvirt_vm"
        if selected_backend == "container_worker_vm":
            return selected_backend
        raise ValueError(f"Unknown agent backend: {selected_backend}")

    @classmethod
    def _normalize_agent_log_view(cls, view: str | None) -> str:
        selected_view = str(view or "executions").strip()
        if selected_view in {"", "executions"}:
            return "executions"
        if selected_view == "threads":
            return "threads"
        raise ValueError(f"Unknown agent log view: {selected_view}")

    @staticmethod
    def _container_log_thread_id(text: str) -> str | None:
        match = _CONTAINER_SESSION_ID_LOG_PATTERN.search(text)
        if match is None:
            return None
        thread_id = str(match.group("thread_id") or "").strip()
        return thread_id or None

    @staticmethod
    def _container_log_without_session_id_lines(text: str) -> str:
        lines = [
            line
            for line in str(text or "").splitlines()
            if _CONTAINER_SESSION_ID_LOG_PATTERN.match(line) is None
        ]
        if not lines:
            return ""
        return "\n".join(lines) + "\n"

    @staticmethod
    def _container_execution_stored_thread_id(execution: Any) -> str | None:
        metadata = getattr(execution, "metadata", None)
        if not isinstance(metadata, Mapping):
            return None

        candidates = [
            metadata.get("thread_id"),
            metadata.get("agent_thread_id"),
            metadata.get("resume_thread_id"),
        ]
        for nested_key in ("outputs", "result", "container_worker"):
            nested = metadata.get(nested_key)
            if isinstance(nested, Mapping):
                candidates.extend(
                    [
                        nested.get("thread_id"),
                        nested.get("agent_thread_id"),
                        nested.get("resume_thread_id"),
                    ]
                )

        for candidate in candidates:
            thread_id = str(candidate or "").strip()
            if thread_id and not thread_id.startswith("dispatch-"):
                return thread_id
        return None

    @classmethod
    def _normalize_agent_backend_filter(cls, backend: str | None) -> str:
        selected_backend = str(backend or "all").strip()
        if selected_backend in {"", "all"}:
            return "all"
        return cls._normalize_agent_backend(selected_backend)

    @staticmethod
    def _agent_optional_string(payload: Mapping[str, Any], key: str) -> str | None:
        value = payload.get(key)
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @staticmethod
    def _agent_string_list(payload: Mapping[str, Any], key: str) -> list[str] | None:
        if key not in payload or payload.get(key) is None:
            return None
        value = payload.get(key)
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, (list, tuple)):
            raise ValueError(f"{key} must be a list of strings")
        normalized = [str(item).strip() for item in value if str(item).strip()]
        return normalized

    @staticmethod
    def _agent_int_or_none(payload: Mapping[str, Any], key: str) -> int | None:
        if key not in payload:
            return None
        value = payload.get(key)
        if value is None or value == "":
            return None
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{key} must be an integer") from exc

    @staticmethod
    def _container_session_target_name(session: SessionManifest) -> str:
        metadata = session.metadata if isinstance(session.metadata, dict) else {}
        return (
            str(metadata.get("target_name") or session.vm_provider_id or session.session_id).strip()
            or session.session_id
        )

    @staticmethod
    def _is_container_session(session: SessionManifest) -> bool:
        metadata = getattr(session, "metadata", {})
        metadata = metadata if isinstance(metadata, dict) else {}
        return (
            str(getattr(session, "vm_provider", "") or "").strip() == "container_worker_vm"
            or str(metadata.get("execution_backend") or "").strip() == "container_worker_vm"
        )

    @staticmethod
    def _container_lifecycle_action(
        *,
        supported: bool,
        kind: str | None,
        label: str,
        reason: str | None = None,
    ) -> ContainerSessionLifecycleAction:
        return ContainerSessionLifecycleAction(
            supported=supported,
            kind=kind,
            label=label,
            reason=reason,
        )

    @staticmethod
    def _container_session_lifecycle_metadata(session: SessionManifest) -> dict[str, Any]:
        metadata = dict(session.metadata) if isinstance(session.metadata, dict) else {}
        lifecycle = metadata.get("container_lifecycle")
        payload: dict[str, Any] = dict(lifecycle) if isinstance(lifecycle, dict) else {}
        for key in (
            "state",
            "cleanup_status",
            "cleanup_attempted_at",
            "cleanup_completed_at",
            "cleanup_error",
        ):
            if key in metadata and key not in payload:
                payload[key] = metadata[key]
        return payload

    @staticmethod
    def _container_record_lifecycle_metadata(record: Any) -> dict[str, Any]:
        metadata = dict(record.metadata) if isinstance(record.metadata, dict) else {}
        lifecycle = metadata.get("container_lifecycle")
        payload: dict[str, Any] = dict(lifecycle) if isinstance(lifecycle, dict) else {}
        for key in (
            "state",
            "cleanup_status",
            "cleanup_attempted_at",
            "cleanup_completed_at",
            "cleanup_error",
        ):
            if key in metadata and key not in payload:
                payload[key] = metadata[key]
        return payload

    @staticmethod
    def _container_session_lifecycle_tombstone(
        session: SessionManifest,
        *,
        state: ContainerSessionLifecycleState,
        cleanup_status: str,
        attempted_at: datetime,
        completed_at: datetime,
        cleanup_error: str | None,
    ) -> SessionManifest:
        metadata = dict(session.metadata) if isinstance(session.metadata, dict) else {}
        lifecycle = metadata.get("container_lifecycle")
        payload: dict[str, Any] = dict(lifecycle) if isinstance(lifecycle, dict) else {}
        payload.update(
            {
                "state": state.value,
                "cleanup_status": cleanup_status,
                "cleanup_attempted_at": attempted_at,
                "cleanup_completed_at": completed_at,
                "cleanup_error": cleanup_error,
            }
        )
        metadata["container_lifecycle"] = payload
        updated_expires_at = session.expires_at
        if state == ContainerSessionLifecycleState.CLEANED_UP and session.expires_at > completed_at:
            updated_expires_at = completed_at
        return session.model_copy(
            update={
                "metadata": metadata,
                "expires_at": updated_expires_at,
            }
        )

    @staticmethod
    def _container_execution_lifecycle_status(record: Any) -> str:
        normalized = str(getattr(record, "last_status", "") or "").strip().casefold()
        if normalized in {"created", "starting"}:
            return "starting"
        if normalized == "running":
            return "running"
        if normalized in {"stopped", "exited", "removed"}:
            return "stopped"
        if normalized in {"failed", "timeout", "timed_out"}:
            return "failed"
        return "unknown"

    def _session_summary_with_container_lifecycle(self, summary: SessionSummary) -> SessionSummary:
        if not self._is_container_session(summary):
            return summary.model_copy(update={"container_lifecycle": None})
        executions = self._container_execution_records_for_session(summary.session_id)
        lifecycle = self._container_session_lifecycle(summary, executions=executions)
        return summary.model_copy(update={"container_lifecycle": lifecycle})

    def _container_session_lifecycle(
        self,
        session: SessionManifest,
        *,
        executions: list[Any] | None = None,
    ) -> ContainerSessionLifecycle | None:
        if not self._is_container_session(session):
            return None
        execution_records = sorted(
            list(executions or self._container_execution_records_for_session(session.session_id)),
            key=self._container_execution_sort_key,
            reverse=True,
        )
        session_lifecycle_payload = self._container_session_lifecycle_metadata(session)
        record_lifecycle_payloads = [
            self._container_record_lifecycle_metadata(record) for record in execution_records
        ]
        metadata_payloads = [session_lifecycle_payload, *record_lifecycle_payloads]

        def _first_value(payloads: list[dict[str, Any]], *keys: str) -> Any:
            for payload in payloads:
                for key in keys:
                    if key not in payload:
                        continue
                    value = payload.get(key)
                    if value is None:
                        continue
                    if isinstance(value, str):
                        text = value.strip()
                        if not text:
                            continue
                        return text
                    return value
            return None

        def _max_datetime(*keys: str) -> datetime | None:
            candidates: list[datetime] = []
            for payload in metadata_payloads:
                for key in keys:
                    parsed = _coerce_optional_datetime(payload.get(key))
                    if parsed is not None:
                        candidates.append(parsed)
            if not candidates:
                return None
            return max(candidates)

        session_cleanup_status = (
            str(_first_value([session_lifecycle_payload], "cleanup_status", "state") or "")
            .strip()
            .casefold()
        )
        record_cleanup_status = (
            str(_first_value(record_lifecycle_payloads, "cleanup_status", "state") or "")
            .strip()
            .casefold()
        )
        cleanup_attempted_at = _max_datetime("cleanup_attempted_at")
        cleanup_completed_at = _max_datetime("cleanup_completed_at")
        cleanup_error = _coerce_optional_string(_first_value(metadata_payloads, "cleanup_error"))

        active_records = [
            record
            for record in execution_records
            if self._container_execution_lifecycle_status(record) in {"starting", "running"}
        ]
        if active_records:
            state = (
                ContainerSessionLifecycleState.RUNNING
                if any(
                    self._container_execution_lifecycle_status(record) == "running"
                    for record in active_records
                )
                else ContainerSessionLifecycleState.STARTING
            )
        elif session_cleanup_status in {
            "cleaned_up",
            "cleanup_succeeded",
            "succeeded",
            "success",
        }:
            state = ContainerSessionLifecycleState.CLEANED_UP
        elif (
            session_cleanup_status in {"cleanup_failed", "failed", "error"}
            or record_cleanup_status in {"cleanup_failed", "failed", "error"}
            or cleanup_error
        ):
            state = ContainerSessionLifecycleState.CLEANUP_FAILED
        else:
            if execution_records:
                latest_record = execution_records[0]
                latest_status = self._container_execution_lifecycle_status(latest_record)
                if latest_status == "failed":
                    state = ContainerSessionLifecycleState.FAILED
                elif latest_status == "stopped":
                    state = ContainerSessionLifecycleState.STOPPED
                elif session.expires_at <= _utc_now():
                    state = ContainerSessionLifecycleState.ABANDONED
                else:
                    state = ContainerSessionLifecycleState.PREPARED
            elif session.expires_at <= _utc_now():
                state = ContainerSessionLifecycleState.ABANDONED
            else:
                state = ContainerSessionLifecycleState.PREPARED

        last_heartbeat_at = None
        active_heartbeat_candidates: list[datetime] = []
        for record in execution_records:
            if self._container_execution_lifecycle_status(record) not in {"starting", "running"}:
                continue
            updated_at = getattr(record, "updated_at", None)
            if isinstance(updated_at, datetime):
                active_heartbeat_candidates.append(updated_at)
        if active_heartbeat_candidates:
            last_heartbeat_at = max(active_heartbeat_candidates)

        completion_candidates: list[datetime] = []
        for record in execution_records:
            finished_at = getattr(record, "finished_at", None)
            if isinstance(finished_at, datetime):
                completion_candidates.append(finished_at)
        last_completion_at = max(completion_candidates) if completion_candidates else None
        last_signal_at = max(
            [
                timestamp
                for timestamp in (last_heartbeat_at, last_completion_at)
                if timestamp is not None
            ],
            default=None,
        )

        cleanup_supported = state in {
            ContainerSessionLifecycleState.STOPPED,
            ContainerSessionLifecycleState.FAILED,
            ContainerSessionLifecycleState.ABANDONED,
            ContainerSessionLifecycleState.CLEANUP_FAILED,
        }
        if cleanup_supported:
            cleanup_reason = None
        elif state == ContainerSessionLifecycleState.CLEANED_UP:
            cleanup_reason = "Session has already been cleaned up."
        elif state in {
            ContainerSessionLifecycleState.RUNNING,
            ContainerSessionLifecycleState.STARTING,
        }:
            cleanup_reason = "Session still has active executions."
        else:
            cleanup_reason = "Cleanup is not currently supported for this session."

        recovery_supported = (
            state
            not in {
                ContainerSessionLifecycleState.RUNNING,
                ContainerSessionLifecycleState.STARTING,
                ContainerSessionLifecycleState.CLEANED_UP,
            }
            and session.expires_at <= _utc_now()
        )
        if recovery_supported:
            recovery_reason = None
        elif state == ContainerSessionLifecycleState.CLEANED_UP:
            recovery_reason = "Session has already been cleaned up."
        elif state in {
            ContainerSessionLifecycleState.RUNNING,
            ContainerSessionLifecycleState.STARTING,
        }:
            recovery_reason = "Session still has active executions."
        else:
            recovery_reason = "Session is not expired."

        return ContainerSessionLifecycle(
            state=state,
            last_heartbeat_at=last_heartbeat_at,
            last_completion_at=last_completion_at,
            last_signal_at=last_signal_at,
            cleanup_attempted_at=cleanup_attempted_at,
            cleanup_completed_at=cleanup_completed_at,
            cleanup_error=cleanup_error,
            actions=ContainerSessionLifecycleActions(
                cleanup=self._container_lifecycle_action(
                    supported=cleanup_supported,
                    kind="cleanup_container_session",
                    label="Cleanup session",
                    reason=cleanup_reason,
                ),
                recovery=self._container_lifecycle_action(
                    supported=recovery_supported,
                    kind="renew_session",
                    label="Renew session",
                    reason=recovery_reason,
                ),
            ),
        )

    def _agent_session_policy_payload(
        self, session: SessionManifest | None
    ) -> dict[str, Any] | None:
        if session is None:
            return None
        labels = _normalize_session_labels(session.metadata.get("labels"))
        return {
            "session_id": session.session_id,
            "model": session.model,
            "expires_at": session.expires_at,
            "allowed_tools": [tool_name_value(tool) for tool in session.allowed_tools],
            "approval_required_tools": [
                tool_name_value(tool) for tool in session.approval_required_tools
            ],
            "approval_tool_settings": {
                tool_name: settings.model_dump(mode="json")
                for tool_name, settings in session.approval_tool_settings.items()
            },
            "approval_notification_target_ids": list(session.approval_notification_target_ids),
            "approval_notification_target_options": [
                option.model_dump(mode="json")
                for option in self._approval_notification_target_options(session)
            ],
            "tool_argument_policies": {
                tool_name: [rule.model_dump(mode="json") for rule in rules]
                for tool_name, rules in session.tool_argument_policies.items()
            },
            "allowed_script_names": list(session.allowed_script_names),
            "allowed_contact_ids": (
                None if session.allowed_contact_ids is None else list(session.allowed_contact_ids)
            ),
            "discord_channels": list(session.discord_channel_allowlist),
            "discord_channel_allowlist": list(session.discord_channel_allowlist),
            "teams_channel_allowlist": list(session.teams_channel_allowlist),
            "teams_chat_allowlist": list(session.teams_chat_allowlist),
            "teams_user_allowlist": list(session.teams_user_allowlist),
            "email_mailboxes": list(session.email_mailbox_allowlist),
            "email_folders": list(session.email_folder_allowlist),
            "communication_file_receive_enabled": session.communication_file_receive_enabled,
            "communication_file_send_enabled": session.communication_file_send_enabled,
            "communication_file_max_bytes": session.communication_file_max_bytes,
            "communication_file_allowed_mime_types": list(
                session.communication_file_allowed_mime_types
            ),
            "communication_file_allowed_extensions": list(
                session.communication_file_allowed_extensions
            ),
            "communication_file_blocked_extensions": list(
                session.communication_file_blocked_extensions
            ),
            "communication_file_require_approval_for_send": (
                session.communication_file_require_approval_for_send
            ),
            "web_search_enabled": session.web_search_enabled,
            "metadata": dict(session.metadata),
            "labels": labels,
        }

    def _approval_notification_target_options(
        self, session: SessionManifest | None
    ) -> list[WorkflowEditorFieldOption]:
        if session is None:
            return []
        try:
            return self._communication_notification_target_options(session.directory_path)
        except Exception:
            return []

    def effective_tool_policy_payload(
        self, session: SessionManifest | None
    ) -> dict[str, Any] | None:
        if session is None:
            return None
        effective_policy = self.runtime.policy_config.resolve_for_session_manifest(session)
        tool_summary = self.runtime.policy.allowed_tool_summary_for_session(
            session,
            effective_policy=effective_policy,
        )
        inherited_approval_required_tools: list[str] = []
        approval_required_tools: list[str] = []
        seen_tools: set[str] = set()
        for tool in effective_policy.approval_required_tools:
            value = tool_name_value(tool)
            if value in seen_tools:
                continue
            seen_tools.add(value)
            inherited_approval_required_tools.append(value)
            approval_required_tools.append(value)
        for tool in session.approval_required_tools:
            value = tool_name_value(tool)
            if value in seen_tools:
                continue
            seen_tools.add(value)
            approval_required_tools.append(value)

        tool_argument_policies: dict[str, list[dict[str, Any]]] = {}
        for source in (effective_policy.tool_argument_policies, session.tool_argument_policies):
            for tool_name, rules in source.items():
                tool_rules = tool_argument_policies.setdefault(tool_name, [])
                tool_rules.extend(rule.model_dump(mode="json") for rule in rules)

        approval_tool_settings = {
            tool_name_value(tool): self.runtime.policy.approval_grant_settings_for_tool(
                session,
                tool,
                effective_policy=effective_policy,
            ).model_dump(mode="json")
            for tool in self.runtime.policy._iter_known_tool_names()
        }
        mcp_tool_policies = {
            tool_name: rule.model_dump(mode="json")
            for tool_name, rule in effective_policy.mcp_tool_policies.items()
        }

        return {
            "allowed_tools": [tool_name_value(tool) for tool in session.allowed_tools],
            "tool_summary": tool_summary,
            "inherited_approval_required_tools": inherited_approval_required_tools,
            "approval_required_tools": approval_required_tools,
            "approval_tool_settings": approval_tool_settings,
            "mcp_tool_policies": mcp_tool_policies,
            "tool_argument_policies": tool_argument_policies,
        }

    def _read_agent_prompt_queue_records(self) -> tuple[dict[str, Any], str | None]:
        queue_path = self.config.runtime_dir / "queued-agent-prompts.json"
        if not queue_path.exists():
            return {}, None
        try:
            from agent_sandbox.messaging.agent_prompt_queue import AgentPromptQueueStore

            return AgentPromptQueueStore(queue_path).read_all(), None
        except Exception as exc:
            return {}, str(exc)

    @staticmethod
    def _agent_prompt_queue_candidates(agent: AgentRecord) -> set[str]:
        candidates = {
            str(value or "").strip()
            for value in (agent.agent_id, agent.target_name, agent.session_id)
        }
        host = agent.host if isinstance(agent.host, Mapping) else {}
        for key in ("vm_name", "target_name"):
            value = host.get(key)
            if isinstance(value, str) and value.strip():
                candidates.add(value.strip())
        session_ids = host.get("session_ids")
        if isinstance(session_ids, list):
            for session_id in session_ids:
                normalized = str(session_id or "").strip()
                if normalized:
                    candidates.add(normalized)
        return {candidate for candidate in candidates if candidate}

    @staticmethod
    def _agent_prompt_queue_record_matches(record: Any, *, candidates: set[str]) -> bool:
        record_values = {
            str(value or "").strip()
            for value in (
                getattr(record, "queue_key", None),
                getattr(record, "target", None),
                getattr(record, "vm_name", None),
                getattr(record, "session_id", None),
            )
        }
        return bool(candidates.intersection(record_values))

    @staticmethod
    def _agent_prompt_queue_preview(prompt: str) -> tuple[str | None, int]:
        prompt_length = len(prompt)
        normalized = re.sub(r"\s+", " ", prompt).strip()
        if not normalized:
            return None, prompt_length
        if len(normalized) > 240:
            normalized = f"{normalized[:237]}..."
        return normalized, prompt_length

    def _agent_prompt_queue_record_payload(self, record: Any) -> AgentPromptQueueRecord:
        prompt = str(getattr(record, "prompt", "") or "")
        prompt_preview, prompt_length = self._agent_prompt_queue_preview(prompt)
        return AgentPromptQueueRecord(
            queue_key=str(getattr(record, "queue_key", "") or ""),
            effect_id=str(getattr(record, "effect_id", "") or ""),
            target=str(getattr(record, "target", "") or ""),
            vm_name=str(getattr(record, "vm_name", "") or ""),
            session_id=getattr(record, "session_id", None),
            created_at=getattr(record, "created_at"),
            updated_at=getattr(record, "updated_at"),
            coalesced_count=int(getattr(record, "coalesced_count", 1) or 1),
            retry_count=int(getattr(record, "retry_count", 1) or 1),
            max_retries=getattr(record, "max_retries", None),
            prompt_preview=prompt_preview,
            prompt_length=prompt_length,
            metadata=dict(getattr(record, "metadata", {}) or {}),
        )

    def _agent_prompt_queue_summary(
        self,
        agent: AgentRecord,
        queued_records: Mapping[str, Any],
        *,
        read_error: str | None,
        record_limit: int = 20,
    ) -> AgentPromptQueueSummary:
        candidates = self._agent_prompt_queue_candidates(agent)
        matches = [
            record
            for record in queued_records.values()
            if self._agent_prompt_queue_record_matches(record, candidates=candidates)
        ]
        if not matches:
            return AgentPromptQueueSummary(read_error=read_error)
        matches.sort(
            key=lambda record: getattr(record, "updated_at", getattr(record, "created_at", None)),
            reverse=True,
        )
        return AgentPromptQueueSummary(
            record_count=len(matches),
            coalesced_count=sum(
                max(1, int(getattr(record, "coalesced_count", 1) or 1)) for record in matches
            ),
            oldest_created_at=min(getattr(record, "created_at") for record in matches),
            newest_updated_at=max(getattr(record, "updated_at") for record in matches),
            records=[
                self._agent_prompt_queue_record_payload(record) for record in matches[:record_limit]
            ],
            truncated_count=max(0, len(matches) - record_limit),
            read_error=read_error,
        )

    def _agent_records_with_prompt_queue(self, records: list[AgentRecord]) -> list[AgentRecord]:
        if not records:
            return records
        queued_records, read_error = self._read_agent_prompt_queue_records()
        if not queued_records and read_error is None:
            return records
        return [
            record.model_copy(
                update={
                    "prompt_queue": self._agent_prompt_queue_summary(
                        record, queued_records, read_error=read_error
                    )
                }
            )
            for record in records
        ]

    def _agent_record_from_vm(
        self,
        summary: VmSummary,
        *,
        include_effective_tool_policy: bool = True,
    ) -> AgentRecord:
        power_state = str(summary.power_state or summary.libvirt_state or "unknown").strip()
        status = "running" if power_state == "running" else "stopped"
        directory_path = summary.directory_paths[0] if len(summary.directory_paths) == 1 else "root"
        session_policy = None
        effective_tool_policy = None
        runtime_image: dict[str, Any] | None = None
        if len(summary.session_ids) == 1:
            session = self.runtime.session_repository.get_session(summary.session_ids[0])
            if session is not None:
                session_policy = self._agent_session_policy_payload(session)
                if include_effective_tool_policy:
                    effective_tool_policy = self.effective_tool_policy_payload(session)
                runtime_image = self.session_runtime_image_payload(session)
        host: dict[str, Any] = {
            "kind": "vm",
            "vm_name": summary.vm_name,
            "provider": summary.provider,
            "issue_number": summary.issue_number,
            "session_ids": list(summary.session_ids),
            "labels": dict(summary.labels),
            "directory_paths": list(summary.directory_paths),
            "power_state": summary.power_state,
            "libvirt_state": summary.libvirt_state,
            "provider_state": summary.provider_state,
            "provider_details": dict(summary.provider_details),
            "ip_address": summary.ip_address,
            "agent": {
                "state": summary.agent_state,
                "pid": summary.agent_pid,
                "dispatch_id": summary.dispatch_id,
                "repo_names": list(summary.agent_repo_names),
                "preferred_repo_name": summary.agent_repo_names[0]
                if len(summary.agent_repo_names) == 1
                else None,
            },
            "session_policy": session_policy,
        }
        if runtime_image is not None:
            host["runtime_image"] = runtime_image
        return AgentRecord(
            agent_id=summary.vm_name,
            backend="libvirt_vm",
            target_name=summary.vm_name,
            session_id=summary.session_ids[0] if len(summary.session_ids) == 1 else None,
            directory_path=directory_path,
            status=status,
            updated_at=summary.last_state_update_at,
            effective_tool_policy=effective_tool_policy,
            host=host,
            capabilities={
                "start": True,
                "shutdown": True,
                "resume": True,
                "enable_disable": True,
                "kill": True,
                "logs": True,
                "stop": False,
                "destroy": True,
            },
        )

    def _agent_record_from_stale_vm_session(
        self,
        session: SessionManifest,
        *,
        inventory_error: str | None = None,
        include_effective_tool_policy: bool = True,
    ) -> AgentRecord:
        vm_name = str(session.vm_id or session.vm_provider_id or session.session_id).strip()
        directory_path = self._agent_record_directory_path(session.directory_path)
        metadata = dict(session.metadata) if isinstance(session.metadata, dict) else {}
        labels = _normalize_session_labels(metadata.get("labels"))
        host: dict[str, Any] = {
            "kind": "vm_session",
            "vm_name": vm_name,
            "provider": str(session.vm_provider or "libvirt").strip() or "libvirt",
            "session_ids": [session.session_id],
            "labels": labels,
            "directory_paths": [directory_path],
            "inventory_status": "missing",
            "session_policy": self._agent_session_policy_payload(session),
        }
        if inventory_error:
            host["inventory_error"] = inventory_error
        host["runtime_image"] = self.session_runtime_image_payload(session)
        return AgentRecord(
            agent_id=vm_name,
            backend="libvirt_vm",
            target_name=vm_name,
            session_id=session.session_id,
            directory_path=directory_path,
            status="stale",
            updated_at=session.created_at,
            effective_tool_policy=(
                self.effective_tool_policy_payload(session)
                if include_effective_tool_policy
                else None
            ),
            host=host,
            capabilities={
                "start": False,
                "shutdown": False,
                "resume": False,
                "enable_disable": False,
                "kill": False,
                "logs": False,
                "stop": False,
                "destroy": True,
            },
        )

    def _reconcile_container_execution_records(self, records: list[Any]) -> list[Any]:
        reconciled: list[Any] = []
        for record in records:
            status = self._container_agent_status(record.last_status)
            if status not in {"starting", "running", "unknown"}:
                reconciled.append(record)
                continue
            try:
                self._container_worker_backend(
                    directory_path=record.directory_path,
                    worker_url=self._container_worker_url_for_target(record=record),
                ).inspect(record.execution_id)
                record = (
                    self._container_worker_execution_repository().get(record.execution_id) or record
                )
            except Exception:
                pass
            reconciled.append(record)
        return reconciled

    def _container_execution_payload(self, record: Any) -> dict[str, Any]:
        status = self._container_execution_status(record)
        metadata = dict(record.metadata) if isinstance(record.metadata, dict) else {}
        image = metadata.get("image")
        return {
            "execution_id": record.execution_id,
            "session_id": record.session_id,
            "worker_job_id": record.worker_job_id,
            "worker_url": record.worker_url,
            "target_name": record.target_name,
            "status": status,
            "last_status": record.last_status,
            "created_at": record.created_at,
            "started_at": record.started_at,
            "finished_at": record.finished_at,
            "updated_at": record.updated_at,
            "image": image if isinstance(image, str) and image.strip() else None,
            "metadata": metadata,
        }

    def _container_worker_image_details(
        self,
        metadata: Mapping[str, Any] | None,
        *,
        fallback_metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, str | None]:
        container_worker = (
            metadata.get("container_worker") if isinstance(metadata, Mapping) else None
        )
        image: str | None = None
        image_alias: str | None = None
        if isinstance(container_worker, Mapping):
            raw_image = container_worker.get("image")
            if isinstance(raw_image, str):
                image = raw_image.strip() or None
            raw_alias = container_worker.get("image_alias")
            if isinstance(raw_alias, str):
                image_alias = raw_alias.strip() or None
        if image is None and isinstance(fallback_metadata, Mapping):
            raw_fallback_image = fallback_metadata.get("image")
            if isinstance(raw_fallback_image, str):
                image = raw_fallback_image.strip() or None
        return {"image": image, "image_alias": image_alias}

    @staticmethod
    def _runtime_image_ref_display_details(image_ref: str) -> tuple[str, bool]:
        display_ref = image_ref.strip()
        if not display_ref:
            return "", False

        sanitized = False
        candidate = display_ref

        if candidate.startswith("file://"):
            candidate = candidate.removeprefix("file://")
            sanitized = True
        if candidate.startswith("~/"):
            candidate = candidate[2:]
            sanitized = True
        elif candidate.startswith("~\\"):
            candidate = candidate[2:]
            sanitized = True
        if re.match(r"^[A-Za-z]:[\\/]", candidate):
            candidate = candidate[2:]
            sanitized = True
        if "/" in candidate or "\\" in candidate:
            candidate = candidate.replace("\\", "/").rstrip("/")
            candidate = candidate.split("/")[-1]
            sanitized = True
        return candidate or display_ref, sanitized

    def session_runtime_image_payload(
        self, session: SessionManifest | SessionSummary
    ) -> dict[str, Any]:
        if self._is_container_session(session):
            image_details = self._container_worker_image_details(
                dict(session.metadata) if isinstance(session.metadata, dict) else {}
            )
            payload: dict[str, Any] = {
                "backend": "container_worker_vm",
                "kind": "container_worker_image",
                "label": "Container worker image",
                "image_alias": image_details["image_alias"],
                "image_ref": image_details["image"],
                "missing_reason": None,
            }
            if image_details["image"] is None:
                payload["missing_reason"] = "container_worker_image_missing"
            return payload

        metadata = dict(session.metadata) if isinstance(session.metadata, dict) else {}
        template = metadata.get("libvirt_vm_template")
        if not isinstance(template, Mapping):
            return {
                "backend": "libvirt_vm",
                "kind": "libvirt_vm_template",
                "label": "Legacy session: VM template metadata unavailable",
                "template_alias": None,
                "image_ref": None,
                "image_ref_sanitized": False,
                "missing_reason": "legacy_missing_libvirt_vm_template",
            }

        raw_image_ref = template.get("image_ref")
        if not isinstance(raw_image_ref, str):
            return {
                "backend": "libvirt_vm",
                "kind": "libvirt_vm_template",
                "label": "Legacy session: VM template metadata unavailable",
                "template_alias": None,
                "image_ref": None,
                "image_ref_sanitized": False,
                "missing_reason": "legacy_missing_libvirt_vm_template",
            }

        image_ref = raw_image_ref.strip()
        if not image_ref:
            return {
                "backend": "libvirt_vm",
                "kind": "libvirt_vm_template",
                "label": "Legacy session: VM template metadata unavailable",
                "template_alias": None,
                "image_ref": None,
                "image_ref_sanitized": False,
                "missing_reason": "legacy_missing_libvirt_vm_template",
            }

        display_image_ref, image_ref_sanitized = self._runtime_image_ref_display_details(image_ref)
        raw_template_alias = template.get("template_alias")
        template_alias = (
            _coerce_optional_string(raw_template_alias)
            if isinstance(raw_template_alias, str)
            else None
        )
        return {
            "backend": "libvirt_vm",
            "kind": "libvirt_vm_template",
            "label": "Libvirt VM template",
            "template_alias": template_alias,
            "image_ref": display_image_ref,
            "image_ref_sanitized": image_ref_sanitized,
            "missing_reason": None,
        }

    def _agent_record_from_container_execution(
        self,
        record: Any,
        *,
        reconcile: bool,
        include_effective_tool_policy: bool = True,
    ) -> AgentRecord:
        status = self._container_execution_status(record)
        message: str | None = None
        session = self.runtime.session_repository.get_session(record.session_id)
        if reconcile:
            try:
                self._container_worker_backend(
                    directory_path=record.directory_path,
                    worker_url=self._container_worker_url_for_target(record=record),
                ).inspect(record.execution_id)
            except Exception as exc:
                message = str(exc)
            else:
                record = self._container_worker_execution_repository().resolve(record.execution_id)
                status = self._container_execution_status(record)
        host = {
            "kind": "container_worker",
            "worker_job_id": record.worker_job_id,
            "worker_url": record.worker_url,
            "last_status": record.last_status,
            "metadata": dict(record.metadata),
            "session_policy": self._agent_session_policy_payload(session),
        }
        if message:
            host["reconcile_error"] = message
        image_details = self._container_worker_image_details(
            dict(session.metadata) if isinstance(session.metadata, dict) else {},
            fallback_metadata=record.metadata if isinstance(record.metadata, Mapping) else None,
        )
        if image_details["image"] is not None:
            host["image"] = image_details["image"]
        if image_details["image_alias"] is not None:
            host["image_alias"] = image_details["image_alias"]
        return AgentRecord(
            agent_id=record.execution_id,
            backend="container_worker_vm",
            target_name=record.target_name,
            session_id=record.session_id,
            directory_path=self._agent_record_directory_path(record.directory_path),
            status=status,
            started_at=record.started_at,
            finished_at=record.finished_at,
            updated_at=record.updated_at,
            effective_tool_policy=(
                self.effective_tool_policy_payload(session)
                if include_effective_tool_policy
                else None
            ),
            host=host,
            capabilities={
                "start": False,
                "shutdown": False,
                "resume": False,
                "enable_disable": False,
                "kill": False,
                "logs": True,
                "stop": True,
                "destroy": True,
            },
        )

    def _agent_record_from_container_session(
        self,
        session: SessionManifest,
        *,
        executions: list[Any] | None = None,
        reconcile: bool = False,
        include_effective_tool_policy: bool = True,
    ) -> AgentRecord:
        metadata = dict(session.metadata) if isinstance(session.metadata, dict) else {}
        target_name = self._container_session_target_name(session)
        labels = _normalize_session_labels(metadata.get("labels"))
        execution_records = sorted(
            list(executions or []),
            key=self._container_execution_sort_key,
            reverse=True,
        )
        if reconcile and execution_records:
            execution_records = sorted(
                self._reconcile_container_execution_records(execution_records),
                key=self._container_execution_sort_key,
                reverse=True,
            )
        latest_execution = execution_records[0] if execution_records else None
        execution_payloads = [
            self._container_execution_payload(record) for record in execution_records
        ]
        lifecycle = self._container_session_lifecycle(session, executions=execution_records)
        status = self._container_session_status(execution_records)
        if lifecycle is not None and lifecycle.state in {
            ContainerSessionLifecycleState.ABANDONED,
            ContainerSessionLifecycleState.CLEANUP_FAILED,
            ContainerSessionLifecycleState.CLEANED_UP,
        }:
            status = lifecycle.state.value
        active_count = sum(
            1
            for record in execution_records
            if self._agent_status_is_active(self._container_agent_status(record.last_status))
        )
        host: dict[str, Any] = {
            "kind": "container_worker_session",
            "metadata": metadata,
            "session_policy": self._agent_session_policy_payload(session),
            "executions": execution_payloads,
            "execution_count": len(execution_records),
            "active_execution_count": active_count,
        }
        latest_metadata: dict[str, Any] = {}
        if latest_execution is not None:
            latest_metadata = (
                dict(latest_execution.metadata)
                if isinstance(latest_execution.metadata, dict)
                else {}
            )
            host.update(
                {
                    "latest_execution_id": latest_execution.execution_id,
                    "worker_job_id": latest_execution.worker_job_id,
                    "worker_url": latest_execution.worker_url,
                    "last_status": latest_execution.last_status,
                }
            )
        image_details = self._container_worker_image_details(
            metadata,
            fallback_metadata=latest_metadata,
        )
        if image_details["image"] is not None:
            host["image"] = image_details["image"]
        if image_details["image_alias"] is not None:
            host["image_alias"] = image_details["image_alias"]
        if labels:
            host["labels"] = labels
        return AgentRecord(
            agent_id=session.session_id,
            backend="container_worker_vm",
            target_name=target_name,
            session_id=session.session_id,
            directory_path=self._agent_record_directory_path(session.directory_path),
            status=status,
            started_at=None,
            finished_at=None,
            updated_at=(
                latest_execution.updated_at if latest_execution is not None else session.created_at
            ),
            effective_tool_policy=(
                self.effective_tool_policy_payload(session)
                if include_effective_tool_policy
                else None
            ),
            host=host,
            capabilities={
                "start": False,
                "shutdown": False,
                "resume": False,
                "enable_disable": False,
                "kill": False,
                "logs": bool(execution_records),
                "stop": active_count > 0,
                "destroy": True,
            },
        )

    def list_agents(
        self,
        *,
        directory_path: str | None = None,
        direct_only: bool = False,
        include_inactive: bool = True,
        reconcile_containers: bool = True,
        omit_vm_state: bool = False,
        summary_only: bool = False,
        backend: str | None = None,
        all_directories: bool = False,
        include_prompt_queue: bool = False,
    ) -> list[AgentRecord]:
        normalized_directory = self._normalize_directory_path(directory_path)
        selected_backend = self._normalize_agent_backend_filter(backend)
        include_effective_tool_policy = not summary_only
        records: list[AgentRecord] = []
        vm_inventory_error: str | None = None
        inventoried_vm_names: set[str] = set()
        inventoried_session_ids: set[str] = set()
        if selected_backend in {"all", "libvirt_vm"}:
            try:
                list_vms_for_agents = self._deps.list_vms_for_agents or self.list_vms
                vm_summaries = list_vms_for_agents(
                    include_inactive=include_inactive,
                    omit_state=omit_vm_state,
                    directory_path=None if all_directories else normalized_directory,
                    direct_only=False if all_directories else direct_only,
                )
            except Exception as exc:
                vm_inventory_error = str(exc)
                vm_summaries = []
            for summary in vm_summaries:
                inventoried_vm_names.add(str(summary.vm_name or "").strip())
                inventoried_session_ids.update(
                    str(session_id or "").strip() for session_id in summary.session_ids
                )
                record = self._agent_record_from_vm(
                    summary,
                    include_effective_tool_policy=include_effective_tool_policy,
                )
                if include_inactive or self._agent_status_is_active(record.status):
                    records.append(record)
            for session in sorted(
                self.runtime.session_repository.list_sessions(),
                key=lambda item: (item.created_at, item.session_id),
                reverse=True,
            ):
                vm_name = str(session.vm_id or "").strip()
                if not vm_name or self._is_container_session(session):
                    continue
                if vm_name in inventoried_vm_names or session.session_id in inventoried_session_ids:
                    continue
                if not all_directories and not self._agent_record_matches_directory(
                    session.directory_path,
                    normalized_directory,
                    direct_only=direct_only,
                ):
                    continue
                record = self._agent_record_from_stale_vm_session(
                    session,
                    inventory_error=vm_inventory_error,
                    include_effective_tool_policy=include_effective_tool_policy,
                )
                if include_inactive or self._agent_status_is_active(record.status):
                    records.append(record)
            if selected_backend == "libvirt_vm" and vm_inventory_error and not records:
                raise RuntimeError(vm_inventory_error)

        if selected_backend == "libvirt_vm":
            return (
                self._agent_records_with_prompt_queue(records) if include_prompt_queue else records
            )

        container_repository = self._container_worker_execution_repository()
        list_all_ordered = getattr(container_repository, "list_all_ordered", None)
        list_by_directory = getattr(container_repository, "list_by_directory", None)
        if all_directories:
            if callable(list_all_ordered):
                container_records = list_all_ordered()
            else:
                container_records = list(container_repository.read_all().values())
        elif direct_only and callable(list_by_directory):
            container_records = list_by_directory(normalized_directory)
        elif callable(list_all_ordered):
            container_records = list_all_ordered()
        else:
            container_records = list(container_repository.read_all().values())

        container_records_by_session: dict[str, list[Any]] = {}
        for container_record in container_records:
            session_id = str(container_record.session_id or "").strip()
            if not session_id:
                continue
            if not all_directories and not self._agent_record_matches_directory(
                container_record.directory_path,
                normalized_directory,
                direct_only=direct_only,
            ):
                continue
            container_records_by_session.setdefault(session_id, []).append(container_record)
        for session in sorted(
            self.runtime.session_repository.list_sessions(),
            key=lambda item: (item.created_at, item.session_id),
            reverse=True,
        ):
            if not self._is_container_session(session):
                continue
            if not all_directories and not self._agent_record_matches_directory(
                session.directory_path,
                normalized_directory,
                direct_only=direct_only,
            ):
                continue
            record = self._agent_record_from_container_session(
                session,
                executions=container_records_by_session.get(session.session_id, []),
                reconcile=reconcile_containers,
                include_effective_tool_policy=include_effective_tool_policy,
            )
            if include_inactive or self._agent_status_is_active(record.status):
                records.append(record)
        return self._agent_records_with_prompt_queue(records) if include_prompt_queue else records

    def _agent_create_operation_details(
        self, payload: Mapping[str, Any]
    ) -> tuple[str, str, str | None, str]:
        selected_backend = self._normalize_agent_backend(
            self._agent_optional_string(payload, "backend")
            or self._agent_optional_string(payload, "execution_backend")
        )
        directory_path = self._normalize_directory_path(
            self._agent_optional_string(payload, "directory_path")
        )
        if selected_backend == "libvirt_vm":
            vm_name = self._agent_optional_string(payload, "vm_name")
            if not vm_name:
                raise ValueError("vm_name is required for libvirt_vm agents")
            return (
                selected_backend,
                vm_name,
                self._agent_optional_string(payload, "session_id"),
                directory_path,
            )

        session_id = self._agent_optional_string(payload, "session_id")
        if not session_id:
            raise ValueError("session_id is required for container_worker_vm agents")
        target_name = self._agent_optional_string(payload, "target_name") or session_id
        return selected_backend, target_name, session_id, directory_path

    @staticmethod
    def _agent_create_operation_worker_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
        return json.loads(json.dumps(dict(payload), default=str))

    def submit_admin_agent_create(self, payload: Mapping[str, Any]) -> AgentCreateOperation:
        if not isinstance(payload, Mapping):
            raise ValueError("Request body must be a JSON object")
        worker_payload = self._agent_create_operation_worker_payload(payload)
        backend, target_name, session_id, directory_path = self._agent_create_operation_details(
            worker_payload
        )
        now = _utc_now()
        operation = AgentCreateOperation(
            operation_id=f"agent-create-{uuid4().hex}",
            status="queued",
            backend=backend,
            target_name=target_name,
            session_id=session_id,
            directory_path=directory_path,
            owner_id=self._agent_create_operation_owner_id,
            owner_pid=os.getpid(),
            created_at=now,
            updated_at=now,
        )
        with self._agent_create_operation_payloads_lock:
            self._agent_create_operation_payloads[operation.operation_id] = worker_payload
        try:
            self.agent_create_operations.put(operation.operation_id, operation)
            self._agent_create_executor.submit(
                self._run_agent_create_operation,
                operation.operation_id,
            )
        except Exception:
            with self._agent_create_operation_payloads_lock:
                self._agent_create_operation_payloads.pop(operation.operation_id, None)
            raise
        return self.get_admin_agent_create_operation(operation.operation_id)

    def list_admin_agent_create_operations(
        self,
        *,
        limit: int = 50,
        directory_path: str | None = None,
    ) -> list[AgentCreateOperation]:
        normalized_directory = (
            self._normalize_directory_path(directory_path) if directory_path else None
        )
        operations = list(self.agent_create_operations.read_all().values())
        if normalized_directory is not None:
            operations = [
                operation
                for operation in operations
                if operation.directory_path == normalized_directory
            ]
        operations.sort(
            key=lambda operation: (
                operation.updated_at,
                operation.created_at,
                operation.operation_id,
            ),
            reverse=True,
        )
        bounded_limit = max(1, min(int(limit), 200))
        return operations[:bounded_limit]

    def get_admin_agent_create_operation(self, operation_id: str) -> AgentCreateOperation:
        normalized_operation_id = str(operation_id or "").strip()
        operation = self.agent_create_operations.get(normalized_operation_id)
        if operation is None:
            raise LookupError(f"Agent create operation not found: {normalized_operation_id}")
        return operation

    def _update_agent_create_operation(
        self,
        operation_id: str,
        updater: Any,
    ) -> AgentCreateOperation | None:
        def _update(items: dict[str, AgentCreateOperation]) -> AgentCreateOperation | None:
            current = items.get(operation_id)
            if current is None:
                return None
            updated = updater(current)
            items[operation_id] = updated
            return updated

        return self.agent_create_operations.update_all(_update)

    def _run_agent_create_operation(self, operation_id: str) -> None:
        started_at = _utc_now()
        operation = self._update_agent_create_operation(
            operation_id,
            lambda current: (
                current.model_copy(
                    update={
                        "status": "running",
                        "started_at": current.started_at or started_at,
                        "updated_at": started_at,
                        "error": None,
                    }
                )
                if current.status in {"queued", "running"}
                else current
            ),
        )
        if operation is None or operation.status != "running":
            return
        try:
            with self._agent_create_operation_payloads_lock:
                worker_payload = self._agent_create_operation_payloads.get(operation_id)
            if worker_payload is None:
                raise RuntimeError("Agent create request payload is no longer available.")
            overridden, result = self._admin_service_override(
                "create_admin_agent", worker_payload
            )
            if not overridden:
                result = self.create_admin_agent(worker_payload)
            finished_at = _utc_now()
            if bool(result.get("success")):
                self._update_agent_create_operation(
                    operation_id,
                    lambda current: current.model_copy(
                        update={
                            "status": "succeeded",
                            "result": result,
                            "error": None,
                            "finished_at": finished_at,
                            "updated_at": finished_at,
                        }
                    ),
                )
                return
            self._update_agent_create_operation(
                operation_id,
                lambda current: current.model_copy(
                    update={
                        "status": "failed",
                        "result": result,
                        "error": str(result.get("message") or "Agent create failed."),
                        "finished_at": finished_at,
                        "updated_at": finished_at,
                    }
                ),
            )
        except Exception as exc:
            finished_at = _utc_now()
            error_message = str(exc) or exc.__class__.__name__
            self._update_agent_create_operation(
                operation_id,
                lambda current: current.model_copy(
                    update={
                        "status": "failed",
                        "error": error_message,
                        "finished_at": finished_at,
                        "updated_at": finished_at,
                    }
                ),
            )
        finally:
            with self._agent_create_operation_payloads_lock:
                self._agent_create_operation_payloads.pop(operation_id, None)

    @staticmethod
    def _agent_create_operation_owner_is_alive(owner_pid: int | None) -> bool:
        if owner_pid is None or owner_pid <= 0:
            return False
        if owner_pid == os.getpid():
            return True
        try:
            os.kill(owner_pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def mark_abandoned_agent_create_operations_failed(self) -> None:
        now = _utc_now()

        def _mark(items: dict[str, AgentCreateOperation]) -> None:
            for operation_id, operation in list(items.items()):
                if operation.status not in {"queued", "running"}:
                    continue
                if operation.owner_pid is None:
                    continue
                if self._agent_create_operation_owner_is_alive(operation.owner_pid):
                    continue
                items[operation_id] = operation.model_copy(
                    update={
                        "status": "failed",
                        "error": "Admin Web process exited before this create operation finished.",
                        "finished_at": now,
                        "updated_at": now,
                    }
                )

        self.agent_create_operations.update_all(_mark)

    def sync_added_discord_session_cursors(
        self,
        *,
        previous_session: SessionManifest | None,
        updated_session: SessionManifest,
    ) -> dict[str, str]:
        return _sync_added_discord_session_cursors(
            self.runtime,
            previous_session=previous_session,
            updated_session=updated_session,
        )

    def _admin_service_override(
        self, method_name: str, *args: Any, **kwargs: Any
    ) -> tuple[bool, Any]:
        override = self._deps.admin_service_override
        if override is None:
            return False, None
        return override(method_name, args, kwargs)

    def _create_vm_for_admin_agent(self, **kwargs: Any) -> VmActionResult:
        overridden, result = self._admin_service_override("create_vm", **kwargs)
        if overridden:
            return result
        return self.create_vm(**kwargs)

    def create_admin_agent(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        selected_backend = self._normalize_agent_backend(
            self._agent_optional_string(payload, "backend")
            or self._agent_optional_string(payload, "execution_backend")
        )
        if selected_backend == "libvirt_vm":
            vm_name = self._agent_optional_string(payload, "vm_name")
            if not vm_name:
                raise ValueError("vm_name is required for libvirt_vm agents")
            codex_skills = self.validate_managed_codex_skills(
                self._agent_string_list(payload, "codex_skills")
            )
            agent_provider = self._agent_optional_string(payload, "agent_provider")
            directory_path = self._agent_optional_string(payload, "directory_path") or "root"
            vm_image_selector = self._agent_optional_string(payload, "vm_image")
            vm_defaults = self.resolve_vm_defaults(directory_path)
            vm_image_selection = resolve_libvirt_vm_template_selector(
                vm_image_selector,
                default_template=vm_defaults.libvirt_vm_default_template,
                templates=vm_defaults.libvirt_vm_templates,
            )
            create_metadata: dict[str, Any] = {
                "libvirt_vm_template": {
                    "image_ref": vm_image_selection["image_ref"],
                    "template_alias": vm_image_selection["template_alias"],
                }
            }
            if agent_provider:
                create_metadata["agent_provider"] = agent_provider
            model_provider_mode = normalize_model_provider_mode_override(
                payload.get(MODEL_PROVIDER_MODE_METADATA_KEY)
            )
            if model_provider_mode:
                create_metadata[MODEL_PROVIDER_MODE_METADATA_KEY] = model_provider_mode
            resolved_allowed_contact_ids = _validate_allowed_contact_ids_for_directory(
                self.runtime,
                session_id=self._agent_optional_string(payload, "session_id") or vm_name,
                directory_path=directory_path,
                allowed_contact_ids=payload.get("allowed_contact_ids"),
            )
            communication_allowlists = _admin_agent_communication_allowlists(
                payload,
                config=self.config,
                directory_path=directory_path,
            )
            result = self._create_vm_for_admin_agent(
                provider=self._agent_optional_string(payload, "provider"),
                vm_name=vm_name,
                session_id=self._agent_optional_string(payload, "session_id"),
                labels=_normalize_session_labels(payload.get("labels")),
                model=self._agent_optional_string(payload, "model"),
                vm_user=self._agent_optional_string(payload, "vm_user") or "agent",
                ttl_seconds=self._agent_int_or_none(payload, "ttl_seconds"),
                ttl_hours=self._agent_int_or_none(payload, "ttl_hours"),
                allowed_tools=self._agent_string_list(payload, "allowed_tools"),
                **(
                    {"tool_argument_policies": payload["tool_argument_policies"]}
                    if "tool_argument_policies" in payload
                    else {}
                ),
                allowed_script_names=self._agent_string_list(payload, "allowed_script_names"),
                allowed_contact_ids=resolved_allowed_contact_ids,
                discord_channels=communication_allowlists["discord_channels"],
                teams_channel_allowlist=communication_allowlists["teams_channel_allowlist"],
                teams_chat_allowlist=communication_allowlists["teams_chat_allowlist"],
                teams_user_allowlist=communication_allowlists["teams_user_allowlist"],
                email_mailboxes=communication_allowlists["email_mailboxes"],
                email_folders=communication_allowlists["email_folders"],
                communication_file_receive_enabled=(
                    _normalize_session_policy_bool(
                        payload.get("communication_file_receive_enabled"),
                        field_name="communication_file_receive_enabled",
                    )
                    if "communication_file_receive_enabled" in payload
                    else None
                ),
                communication_file_send_enabled=(
                    _normalize_session_policy_bool(
                        payload.get("communication_file_send_enabled"),
                        field_name="communication_file_send_enabled",
                    )
                    if "communication_file_send_enabled" in payload
                    else None
                ),
                communication_file_max_bytes=(
                    _normalize_session_policy_non_negative_int_or_none(
                        payload.get("communication_file_max_bytes"),
                        field_name="communication_file_max_bytes",
                    )
                    if "communication_file_max_bytes" in payload
                    else None
                ),
                communication_file_require_approval_for_send=(
                    _normalize_session_policy_bool(
                        payload.get("communication_file_require_approval_for_send"),
                        field_name="communication_file_require_approval_for_send",
                    )
                    if "communication_file_require_approval_for_send" in payload
                    else None
                ),
                web_search_enabled=(
                    _normalize_session_policy_bool(
                        payload.get("web_search_enabled"),
                        field_name="web_search_enabled",
                    )
                    if "web_search_enabled" in payload
                    else False
                ),
                dotnet=bool(payload.get("dotnet", False)),
                nuget=bool(payload.get("nuget", False)),
                mssql=bool(payload.get("mssql", False)),
                mssql_init_root=self._agent_optional_string(payload, "mssql_init_root"),
                playwright=bool(payload.get("playwright", False)),
                codex_skills=codex_skills,
                directory_path=directory_path,
                metadata=create_metadata,
            )
            session_id = self._agent_optional_string(payload, "session_id")
            session = (
                self.runtime.session_repository.get_session(session_id) if session_id else None
            )
            return {
                "success": result.success,
                "agent_id": result.vm_name or vm_name,
                "backend": "libvirt_vm",
                "target_name": result.vm_name or vm_name,
                "action": result.action,
                "message": result.message,
                "status": "created" if result.success else "failed",
                "session_id": session_id,
                "provider": result.provider,
                "provider_operation_id": result.provider_operation_id,
                "effective_tool_policy": self.effective_tool_policy_payload(session),
                "vm_image": vm_image_selection["image_ref"],
                "vm_image_alias": vm_image_selection["template_alias"],
            }

        session_id = self._agent_optional_string(payload, "session_id")
        if not session_id:
            raise ValueError("session_id is required for container_worker_vm agents")
        if bool(payload.get("mssql", False)) or self._agent_optional_string(
            payload, "mssql_init_root"
        ):
            raise ValueError(
                "create_admin_agent with container_worker_vm does not support MSSQL yet"
            )
        codex_skill_files = self.resolve_container_codex_skill_files(
            self._agent_string_list(payload, "codex_skills")
        )
        target_name = self._agent_optional_string(payload, "target_name") or session_id
        directory_path = self._normalize_directory_path(
            self._agent_optional_string(payload, "directory_path")
        )
        session_repository = self.runtime.session_repository
        if session_repository.get_session(session_id) is not None:
            raise ValueError(f"Session already exists: {session_id}")

        policy = self.runtime.policy_config.resolve_for_directory(directory_path)
        labels = _normalize_session_labels(payload.get("labels"))
        model = self._agent_optional_string(payload, "model")
        backend = self._container_worker_backend(directory_path=directory_path)
        from agent_sandbox.effects.providers.agent_session import default_agent_provider_for_config

        requested_agent_provider = self._agent_optional_string(payload, "agent_provider")
        agent_provider = requested_agent_provider or default_agent_provider_for_config(
            backend.config
        )
        container_image_selection = resolve_container_worker_image_selector(
            self._agent_optional_string(payload, "container_image"),
            default_image=backend.config.container_worker_default_image,
            images=backend.config.container_worker_images,
        )
        metadata: dict[str, Any] = {
            "execution_backend": "container_worker_vm",
            "target_name": target_name,
        }
        if requested_agent_provider or agent_provider == "claude":
            metadata["agent_provider"] = agent_provider
        model_provider_mode = normalize_model_provider_mode_override(
            payload.get(MODEL_PROVIDER_MODE_METADATA_KEY)
        )
        if model_provider_mode:
            metadata[MODEL_PROVIDER_MODE_METADATA_KEY] = model_provider_mode
        if labels:
            metadata["labels"] = labels
        worker_url = self._container_worker_backend_url(backend)
        if worker_url:
            metadata["worker_url"] = worker_url
        metadata["container_worker"] = container_image_selection
        resolved_allowed_contact_ids = _validate_allowed_contact_ids_for_directory(
            self.runtime,
            session_id=session_id,
            directory_path=directory_path,
            allowed_contact_ids=payload.get("allowed_contact_ids"),
        )
        communication_allowlists = _admin_agent_communication_allowlists(
            payload,
            config=self.config,
            directory_path=directory_path,
        )
        manifest = _build_issue_session_manifest(
            policy=policy,
            session_id=session_id,
            vm_id=target_name,
            issue_number=None,
            directory_path=directory_path,
            ttl_hours=self._agent_int_or_none(payload, "ttl_hours"),
            ttl_seconds=self._agent_int_or_none(payload, "ttl_seconds"),
            extra_allowed_tools=self._agent_string_list(payload, "allowed_tools"),
            **(
                {"tool_argument_policies": payload["tool_argument_policies"]}
                if "tool_argument_policies" in payload
                else {}
            ),
            allowed_script_names=self._agent_string_list(payload, "allowed_script_names"),
            allowed_contact_ids=resolved_allowed_contact_ids,
            discord_channels=communication_allowlists["discord_channels"] or [],
            teams_channel_allowlist=communication_allowlists["teams_channel_allowlist"] or [],
            teams_chat_allowlist=communication_allowlists["teams_chat_allowlist"] or [],
            teams_user_allowlist=communication_allowlists["teams_user_allowlist"] or [],
            email_mailboxes=communication_allowlists["email_mailboxes"] or [],
            email_folders=communication_allowlists["email_folders"] or [],
            communication_file_receive_enabled=(
                _normalize_session_policy_bool(
                    payload.get("communication_file_receive_enabled"),
                    field_name="communication_file_receive_enabled",
                )
                if "communication_file_receive_enabled" in payload
                else None
            ),
            communication_file_send_enabled=(
                _normalize_session_policy_bool(
                    payload.get("communication_file_send_enabled"),
                    field_name="communication_file_send_enabled",
                )
                if "communication_file_send_enabled" in payload
                else None
            ),
            communication_file_max_bytes=(
                _normalize_session_policy_non_negative_int_or_none(
                    payload.get("communication_file_max_bytes"),
                    field_name="communication_file_max_bytes",
                )
                if "communication_file_max_bytes" in payload
                else None
            ),
            communication_file_require_approval_for_send=(
                _normalize_session_policy_bool(
                    payload.get("communication_file_require_approval_for_send"),
                    field_name="communication_file_require_approval_for_send",
                )
                if "communication_file_require_approval_for_send" in payload
                else None
            ),
            web_search_enabled=(
                _normalize_session_policy_bool(
                    payload.get("web_search_enabled"),
                    field_name="web_search_enabled",
                )
                if "web_search_enabled" in payload
                else False
            ),
        ).model_copy(
            update={
                "vm_id": None,
                "vm_provider": "container_worker_vm",
                "vm_provider_id": target_name,
                "model": model,
                "metadata": metadata,
            }
        )
        session_record = backend.prepare_session(
            session_id=session_id,
            metadata={
                "execution_backend": "container_worker_vm",
                "directory_path": directory_path,
                "target_name": target_name,
                "labels": labels,
                "container_worker": container_image_selection,
                **({"worker_url": worker_url} if worker_url else {}),
            },
        )
        from agent_sandbox.effects.providers.agent_session import (
            build_container_session_files,
        )

        backend.write_files(
            session_id=session_id,
            files=[
                *build_container_session_files(
                    config=backend.config,
                    session_id=session_id,
                    model=model,
                    agent_provider=agent_provider,
                    web_search_enabled=(
                        _normalize_session_policy_bool(
                            payload.get("web_search_enabled"),
                            field_name="web_search_enabled",
                        )
                        if "web_search_enabled" in payload
                        else False
                    ),
                    dotnet=bool(payload.get("dotnet", False)),
                    nuget=bool(payload.get("nuget", False)),
                    playwright=bool(payload.get("playwright", False)),
                ),
                *codex_skill_files,
            ],
        )
        session_repository.upsert_session(manifest)
        _sync_added_discord_session_cursors(
            self.runtime,
            previous_session=None,
            updated_session=manifest,
        )
        session = session_repository.get_session(session_id)
        return {
            "success": True,
            "agent_id": session_id,
            "backend": "container_worker_vm",
            "target_name": target_name,
            "action": "create",
            "message": f"Prepared container worker session {session_id}",
            "status": "prepared",
            "session_id": session_id,
            "agent_home": session_record.agent_home_container_path,
            "repo_root": session_record.repo_root_container_path,
            "container_image": container_image_selection["image"],
            "container_image_alias": container_image_selection["image_alias"],
            "effective_tool_policy": self.effective_tool_policy_payload(session),
        }

    def _resolve_container_agent_session(
        self,
        agent_id: str,
        *,
        require_session: bool,
    ) -> tuple[SessionManifest | None, Any | None]:
        direct_session = self.runtime.session_repository.get_session(agent_id)
        if direct_session is not None and self._is_container_session(direct_session):
            return direct_session, None

        repository = self._container_worker_execution_repository()
        try:
            record = repository.resolve(agent_id)
        except ValueError as exc:
            if str(exc).startswith("Ambiguous container worker session target"):
                raise
            record = None
        if record is not None:
            session = self.runtime.session_repository.get_session(record.session_id)
            if session is None and require_session:
                raise ValueError(f"Unknown session: {record.session_id}")
            return session, record

        matches = [
            session
            for session in self.runtime.session_repository.list_sessions()
            if self._is_container_session(session)
            and self._container_session_target_name(session) == agent_id
        ]
        if len(matches) == 1:
            return matches[0], None
        if len(matches) > 1:
            session_ids = ", ".join(sorted(session.session_id for session in matches))
            raise ValueError(
                f"Container worker target {agent_id!r} is associated with multiple sessions: {session_ids}"
            )
        if require_session:
            raise ValueError(f"Unknown container worker agent target: {agent_id}")
        return None, None

    def _container_execution_records_for_session(self, session_id: str) -> list[Any]:
        repository = self._container_worker_execution_repository()
        list_by_session = getattr(repository, "list_by_session", None)
        if callable(list_by_session):
            records = list_by_session(session_id)
        else:
            records = [
                record
                for record in repository.read_all().values()
                if record.session_id == session_id
            ]
        return sorted(records, key=self._container_execution_sort_key)

    def _session_associated_agent_description(
        self,
        session_id: str,
        session: SessionManifest,
    ) -> str | None:
        if self._is_container_session(session):
            target_name = self._container_session_target_name(session)
            return f"container worker agent {target_name!r}"
        vm_name = str(session.vm_id or "").strip()
        if vm_name:
            return f"VM agent {vm_name!r}"
        return None

    def _delete_chat_assistant_session_mappings(self, session_ids: set[str]) -> int:
        repository = JsonChatAssistantSessionRepository(
            self.config.runtime_dir / "chat-assistant-session-mappings.json"
        )
        return repository.delete_mappings_for_sessions(session_ids)

    def _delete_detached_worker_session_state(self, session_id: str) -> tuple[bool, int]:
        execution_records = self._container_execution_records_for_session(session_id)
        if not execution_records:
            return False, 0
        latest_record = sorted(
            execution_records,
            key=self._container_execution_sort_key,
            reverse=True,
        )[0]
        worker_backend = self._container_worker_backend(
            directory_path=getattr(latest_record, "directory_path", None),
            worker_url=self._container_worker_url_for_target(
                record=latest_record,
                records=execution_records,
            ),
        )
        worker_session_deleted = self._worker_delete_session_removed(
            worker_backend.delete_session(session_id)
        )
        container_repository = self._container_worker_execution_repository()
        removed_mappings = 0
        list_by_session = getattr(container_repository, "list_by_session", None)
        if callable(list_by_session):
            records = list_by_session(session_id)
        else:
            records = [
                candidate
                for candidate in container_repository.read_all().values()
                if candidate.session_id == session_id
            ]
        for candidate in records:
            container_repository.delete(candidate.execution_id)
            removed_mappings += 1
        return worker_session_deleted, removed_mappings

    @staticmethod
    def _worker_delete_session_removed(result: Any) -> bool:
        if isinstance(result, Mapping) and "deleted" in result:
            return bool(result["deleted"])
        return bool(result)

    def _resolve_vm_agent_session(self, agent_id: str) -> SessionManifest:
        direct_session = self.runtime.session_repository.get_session(agent_id)
        if direct_session is not None:
            return direct_session
        matches = self.runtime.session_repository.list_sessions_for_vm(agent_id)
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            session_ids = ", ".join(sorted(session.session_id for session in matches))
            raise ValueError(f"VM {agent_id!r} is associated with multiple sessions: {session_ids}")
        raise ValueError(f"No session is linked to VM agent target: {agent_id}")

    def _record_installed_codex_skills(self, session_id: str, skill_names: list[str]) -> None:
        session = self.runtime.session_repository.get_session(session_id)
        if session is None:
            return
        metadata = dict(session.metadata)
        existing = metadata.get("codex_skills")
        merged: list[str] = []
        if isinstance(existing, list):
            for item in existing:
                if isinstance(item, str) and item.strip():
                    merged.append(item)
        merged.extend(skill_names)
        metadata["codex_skills"] = normalize_skill_names(merged)
        metadata["codex_skills_updated_at"] = datetime.now(UTC).isoformat()
        self.runtime.session_repository.upsert_session(
            session.model_copy(update={"metadata": metadata})
        )

    @staticmethod
    def _skill_install_files_for_codex_home(
        entries: list[SkillLibraryEntry], *, codex_home: str
    ) -> list[dict[str, Any]]:
        normalized_home = str(codex_home or "").strip().rstrip("/")
        if (
            not normalized_home
            or not normalized_home.startswith("/")
            or "\n" in normalized_home
            or "\x00" in normalized_home
        ):
            raise RuntimeError(f"Guest CODEX_HOME resolved to an invalid path: {codex_home!r}")
        return [
            {
                "destination_path": f"{normalized_home}/skills/{entry.name}/SKILL.md",
                "mode": "0644",
                "content": entry.content,
                "overwrite": True,
            }
            for entry in entries
        ]

    def _install_codex_skills_into_vm_agent(
        self,
        agent_id: str,
        *,
        skill_names: list[str],
        vm_user: str,
    ) -> dict[str, Any]:
        session = self._resolve_vm_agent_session(agent_id)
        target = session.session_id
        target_name = str(session.vm_id or agent_id).strip()
        if not target_name:
            raise ValueError(f"Session {session.session_id!r} is not linked to a VM")
        entries = [self.skill_library.get_skill(name) for name in skill_names]
        codex_home_result = self.vm_exec(
            target,
            command='printf "%s" "${CODEX_HOME:-$HOME/.config/agent-sandbox/codex}"',
            vm_user=vm_user,
            timeout_seconds=30,
            check=True,
        )
        if not bool(codex_home_result.get("success")):
            message = str(codex_home_result.get("message") or "Failed to resolve guest CODEX_HOME")
            raise RuntimeError(message)
        codex_home = str(codex_home_result.get("stdout") or "").strip()
        files = self._skill_install_files_for_codex_home(entries, codex_home=codex_home)
        result = self.vm_copy_files(target, files=files, vm_user=vm_user)
        if not bool(result.get("success")):
            message = str(result.get("message") or "Failed to copy Codex skills")
            raise RuntimeError(message)
        copied_paths = list(result.get("copied_paths") or [])
        self._record_installed_codex_skills(session.session_id, skill_names)
        return {
            "success": True,
            "agent_id": agent_id,
            "backend": "libvirt_vm",
            "target_name": target_name,
            "action": "install-skills",
            "message": (
                f"Installed {len(skill_names)} Codex skill"
                f"{'' if len(skill_names) == 1 else 's'} into session "
                f"{session.session_id}: "
                f"{', '.join(skill_names)}"
            ),
            "status": "updated",
            "session_id": session.session_id,
            "copied_count": len(copied_paths),
            "copied_paths": copied_paths,
            "codex_skills": list(skill_names),
        }

    def _install_codex_skills_into_container_agent(
        self,
        agent_id: str,
        *,
        skill_names: list[str],
    ) -> dict[str, Any]:
        session, record = self._resolve_container_agent_session(agent_id, require_session=True)
        if session is None:
            raise ValueError(f"Unknown container worker agent target: {agent_id}")
        execution_records = self._container_execution_records_for_session(session.session_id)
        worker_backend = self._container_worker_backend(
            directory_path=session.directory_path,
            worker_url=self._container_worker_url_for_target(
                session=session,
                record=record,
                records=execution_records,
            ),
        )
        files = self.resolve_container_codex_skill_files(skill_names)
        result = worker_backend.write_files(session_id=session.session_id, files=files)
        copied_paths = list(getattr(result, "copied_paths", []) or [])
        self._record_installed_codex_skills(session.session_id, skill_names)
        return {
            "success": True,
            "agent_id": agent_id,
            "backend": "container_worker_vm",
            "target_name": self._container_session_target_name(session),
            "action": "install-skills",
            "message": (
                f"Installed {len(skill_names)} Codex skill"
                f"{'' if len(skill_names) == 1 else 's'} into session "
                f"{session.session_id}: "
                f"{', '.join(skill_names)}"
            ),
            "status": "updated",
            "session_id": session.session_id,
            "copied_count": int(getattr(result, "copied_count", len(copied_paths)) or 0),
            "copied_paths": copied_paths,
            "codex_skills": list(skill_names),
        }

    def install_admin_agent_skills(
        self,
        agent_id: str,
        *,
        backend: str | None = None,
        skill_names: list[str] | tuple[str, ...] | None = None,
        vm_user: str = "agent",
    ) -> dict[str, Any]:
        names = self.validate_managed_codex_skills(skill_names)
        if not names:
            raise ValueError("codex_skills must contain at least one skill")
        selected_backend = self._normalize_agent_backend(backend)
        if selected_backend == "libvirt_vm":
            return self._install_codex_skills_into_vm_agent(
                agent_id,
                skill_names=names,
                vm_user=vm_user,
            )
        return self._install_codex_skills_into_container_agent(agent_id, skill_names=names)

    def update_admin_agent(
        self,
        agent_id: str,
        *,
        backend: str | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        selected_backend = self._normalize_agent_backend(backend)
        body = dict(payload or {})
        allowed_fields = {
            "model",
            "ttl_hours",
            "allowed_tools",
            "approval_required_tools",
            "approval_tool_settings",
            "approval_notification_target_ids",
            "tool_argument_policies",
            "allowed_script_names",
            "allowed_contact_ids",
            "discord_channels",
            "discord_channel_allowlist",
            "teams_channels",
            "teams_channel_allowlist",
            "teams_chat_allowlist",
            "teams_user_allowlist",
            "metadata",
            "web_search_enabled",
            "email_mailboxes",
            "email_folders",
            "labels",
            *COMMUNICATION_FILE_SESSION_POLICY_FIELDS,
        }
        unsupported = sorted(set(body) - allowed_fields)
        if unsupported:
            raise ValueError("Unsupported agent policy fields: " + ", ".join(unsupported))
        session: SessionManifest | None
        if selected_backend == "container_worker_vm":
            session, record = self._resolve_container_agent_session(agent_id, require_session=True)
            del record
            target_name = self._container_session_target_name(session)
        else:
            session = self._resolve_vm_agent_session(agent_id)
            target_name = str(session.vm_id or agent_id)

        update: dict[str, Any] = {}
        if "model" in body:
            update["model"] = self._agent_optional_string(body, "model")
        if "ttl_hours" in body:
            ttl_hours = self._agent_int_or_none(body, "ttl_hours")
            if ttl_hours is None:
                raise ValueError("ttl_hours must be an integer when supplied")
            update["expires_at"] = _utc_now() + timedelta(hours=ttl_hours)
        if "allowed_tools" in body:
            update["allowed_tools"] = _normalize_allowed_tools(
                self._agent_string_list(body, "allowed_tools") or []
            )
        session_policy_fields = {
            key: body[key]
            for key in (
                "approval_required_tools",
                "approval_tool_settings",
                "approval_notification_target_ids",
                "tool_argument_policies",
                "allowed_script_names",
                "allowed_contact_ids",
                "discord_channels",
                "discord_channel_allowlist",
                "teams_channels",
                "teams_channel_allowlist",
                "teams_chat_allowlist",
                "teams_user_allowlist",
                "metadata",
                "web_search_enabled",
                *COMMUNICATION_FILE_SESSION_POLICY_FIELDS,
            )
            if key in body
        }
        if session_policy_fields:
            contacts_config = None
            if "allowed_contact_ids" in session_policy_fields:
                contacts_config = self.runtime.provider_config.resolve_contacts_for_session_id(
                    session.session_id
                )
            update.update(
                _session_policy_update_from_payload(
                    session,
                    session_policy_fields,
                    contacts_config=contacts_config,
                )
            )
        if "approval_notification_target_ids" in update:
            update["approval_notification_target_ids"] = (
                _resolve_session_approval_notification_target_ids(
                    self.runtime,
                    self.config,
                    session,
                    update["approval_notification_target_ids"],
                )
            )
        if "email_mailboxes" in body:
            update["email_mailbox_allowlist"] = (
                self._agent_string_list(body, "email_mailboxes") or []
            )
        if "email_folders" in body:
            update["email_folder_allowlist"] = self._agent_string_list(body, "email_folders") or []
        if "labels" in body:
            metadata = dict(update["metadata"] if "metadata" in update else session.metadata)
            labels = _normalize_session_labels(body.get("labels"))
            if labels:
                metadata["labels"] = labels
            else:
                metadata.pop("labels", None)
            update["metadata"] = metadata

        updated = session.model_copy(update=update)
        self.runtime.session_repository.upsert_session(updated)
        _sync_added_discord_session_cursors(
            self.runtime,
            previous_session=session,
            updated_session=updated,
        )
        return {
            "success": True,
            "agent_id": agent_id,
            "backend": selected_backend,
            "target_name": target_name,
            "action": "edit",
            "message": f"Updated policy for session {updated.session_id}",
            "status": "updated",
            "session_id": updated.session_id,
            "effective_tool_policy": self.effective_tool_policy_payload(updated),
        }

    def delete_admin_agent(
        self,
        agent_id: str,
        *,
        backend: str | None = None,
    ) -> dict[str, Any]:
        selected_backend = self._normalize_agent_backend(backend)
        if selected_backend == "libvirt_vm":
            direct_session = self.runtime.session_repository.get_session(agent_id)
            if direct_session is not None:
                vm_name = str(direct_session.vm_id or "").strip()
                if not vm_name:
                    raise ValueError(f"Session {agent_id!r} is not linked to a VM")
            else:
                vm_name = agent_id
                matches = self.runtime.session_repository.list_sessions_for_vm(vm_name)
                if len(matches) > 1:
                    session_ids = ", ".join(sorted(session.session_id for session in matches))
                    raise ValueError(
                        f"Refusing to delete VM {vm_name!r}: multiple linked sessions "
                        f"({session_ids}). Delete is only allowed when the VM-to-session link is unambiguous."
                    )
            matches = self.runtime.session_repository.list_sessions_for_vm(vm_name)
            if len(matches) > 1:
                session_ids = ", ".join(sorted(session.session_id for session in matches))
                raise ValueError(
                    f"Refusing to delete VM {vm_name!r}: multiple linked sessions "
                    f"({session_ids}). Delete is only allowed when the VM-to-session link is unambiguous."
                )
            session_ids_to_delete = {session.session_id for session in matches}
            result = self.destroy_vm(vm_name)
            removed_assistant_mappings = (
                self._delete_chat_assistant_session_mappings(session_ids_to_delete)
                if result.success
                else 0
            )
            message = result.message
            if removed_assistant_mappings:
                message = f"{message} removed_assistant_mappings={removed_assistant_mappings}"
            return {
                "success": result.success,
                "agent_id": vm_name,
                "backend": "libvirt_vm",
                "target_name": vm_name,
                "action": "delete",
                "message": message,
                "status": "deleted" if result.success else "failed",
                "session_id": matches[0].session_id if len(matches) == 1 else None,
            }

        session, record = self._resolve_container_agent_session(agent_id, require_session=False)
        session_id = (
            str(getattr(session, "session_id", "") or "").strip()
            if session is not None
            else str(getattr(record, "session_id", "") or "").strip()
        )
        if not session_id:
            raise ValueError(f"Unknown container worker agent target: {agent_id}")
        target_name = (
            self._container_session_target_name(session)
            if session is not None
            else str(getattr(record, "target_name", "") or agent_id)
        )
        execution_records = self._container_execution_records_for_session(session_id)
        worker_backend = self._container_worker_backend(
            directory_path=(
                session.directory_path
                if session is not None
                else getattr(record, "directory_path", None)
            ),
            worker_url=self._container_worker_url_for_target(
                session=session,
                record=record,
                records=execution_records,
            ),
        )
        if record is not None:
            worker_backend.destroy(record.execution_id)
        worker_session_deleted = worker_backend.delete_session(session_id)
        worker_session_removed = self._worker_delete_session_removed(worker_session_deleted)
        container_repository = self._container_worker_execution_repository()
        removed_mappings = 0
        list_by_session = getattr(container_repository, "list_by_session", None)
        if callable(list_by_session):
            records = list_by_session(session_id)
        else:
            records = [
                candidate
                for candidate in container_repository.read_all().values()
                if candidate.session_id == session_id
            ]
        for candidate in records:
            container_repository.delete(candidate.execution_id)
            removed_mappings += 1
        removed_session = self.runtime.session_repository.delete_session(session_id)
        removed_assistant_mappings = self._delete_chat_assistant_session_mappings({session_id})
        removed_approvals = _prune_approvals_for_sessions(self.runtime, session_ids={session_id})
        removed_pr_map_entries, removed_streams = _prune_pr_map(
            self.config.pr_map_path,
            session_ids={session_id},
        )
        removed_cursors, removed_feedback = _prune_pr_feedback_store(
            self.config.runtime_dir / "pr-feedback-store.json",
            streams=removed_streams,
        )
        return {
            "success": True,
            "agent_id": agent_id,
            "backend": "container_worker_vm",
            "target_name": target_name,
            "action": "delete",
            "message": (
                f"Deleted container worker session {session_id}; "
                f"removed_mappings={removed_mappings} "
                f"removed_session={int(removed_session)} "
                f"removed_assistant_mappings={removed_assistant_mappings} "
                f"removed_approvals={removed_approvals} "
                f"removed_pr_map_entries={removed_pr_map_entries} "
                f"removed_feedback_cursors={removed_cursors} "
                f"removed_feedback_items={removed_feedback} "
                f"worker_session_deleted={int(worker_session_removed)}"
            ),
            "status": "deleted",
            "session_id": session_id,
        }

    def get_admin_agent_log_snapshot(
        self,
        agent_id: str,
        *,
        backend: str | None = None,
        view: str | None = None,
        lines: int = 50,
        repo_name: str | None = None,
        vm_user: str = "agent",
    ) -> str:
        _ = self._normalize_agent_log_view(view)
        selected_backend = str(backend or "libvirt_vm").strip()
        if selected_backend in {"libvirt", "libvirt_vm"}:
            return self.get_agent_log_snapshot(
                agent_id,
                lines=lines,
                repo_name=repo_name,
                vm_user=vm_user,
            )
        if selected_backend == "container_worker_vm":
            session, record = self._resolve_container_agent_session(agent_id, require_session=False)
            session_id = (
                str(session.session_id).strip()
                if session is not None
                else str(getattr(record, "session_id", "") or "").strip()
            )
            execution_records = (
                self._container_execution_records_for_session(session_id) if session_id else []
            )
            worker_backend = self._container_worker_backend(
                directory_path=(
                    session.directory_path
                    if session is not None
                    else getattr(record, "directory_path", None)
                ),
                worker_url=self._container_worker_url_for_target(
                    session=session,
                    record=record,
                    records=execution_records,
                ),
            )
            if session is None or record is not None:
                try:
                    worker_backend.inspect(agent_id)
                except Exception:
                    pass
                return worker_backend.get_log_snapshot(agent_id, lines=lines, mode="execution")
            if not execution_records:
                return f"No container executions have run for session {session_id}.\n"
            if self._normalize_agent_log_view(view) == "threads":
                return self._join_container_log_chunks(
                    self._container_thread_log_chunks(
                        worker_backend,
                        execution_records,
                        lines=lines,
                    )
                )
            per_execution_lines = lines
            if lines > 0:
                per_execution_lines = max(1, lines // max(1, len(execution_records)))
            chunks: list[str] = []
            for execution in execution_records:
                header_parts = [
                    f"execution={execution.execution_id}",
                    f"status={self._container_execution_status(execution)}",
                    f"worker_job={execution.worker_job_id}",
                    "log=execution",
                ]
                if execution.started_at is not None:
                    header_parts.append(f"started={execution.started_at.isoformat()}")
                if execution.finished_at is not None:
                    header_parts.append(f"finished={execution.finished_at.isoformat()}")
                try:
                    text = worker_backend.get_log_snapshot(
                        execution.execution_id,
                        lines=per_execution_lines,
                        mode="execution",
                    ).rstrip()
                except Exception as exc:
                    text = f"[log unavailable: {exc}]"
                chunks.append("=== " + " ".join(header_parts) + " ===\n" + text)
            return "\n\n".join(chunks).rstrip() + "\n"
        raise ValueError(f"Unknown agent backend: {selected_backend}")

    def _container_execution_log_chunk(
        self,
        worker_backend: Any,
        execution: Any,
        *,
        lines: int,
        mode: str,
    ) -> str:
        header_parts = [
            f"execution={execution.execution_id}",
            f"status={self._container_execution_status(execution)}",
            f"worker_job={execution.worker_job_id}",
            f"log={mode}",
        ]
        if execution.started_at is not None:
            header_parts.append(f"started={execution.started_at.isoformat()}")
        if execution.finished_at is not None:
            header_parts.append(f"finished={execution.finished_at.isoformat()}")
        try:
            text = worker_backend.get_log_snapshot(
                execution.execution_id,
                lines=lines,
                mode=mode,
            ).rstrip()
        except Exception as exc:
            text = f"[log unavailable: {exc}]"
        header = "=== " + " ".join(header_parts) + " ==="
        return header if not text else header + "\n" + text

    @staticmethod
    def _join_container_log_chunks(chunks: list[str]) -> str:
        return "\n\n".join(chunk for chunk in chunks if chunk).rstrip() + "\n"

    def _container_thread_execution_fallback_log(
        self,
        worker_backend: Any,
        records: list[Any],
        *,
        lines: int,
    ) -> str:
        ordered_records = sorted(records, key=self._container_execution_sort_key)
        per_execution_lines = lines
        if lines > 0 and len(ordered_records) > 1:
            per_execution_lines = max(1, lines // len(ordered_records))
        chunks: list[str] = []
        for execution in ordered_records:
            try:
                text = worker_backend.get_log_snapshot(
                    execution.execution_id,
                    lines=per_execution_lines,
                    mode="execution",
                )
            except Exception as exc:
                text = f"[log unavailable: {exc}]"
            body = self._container_log_without_session_id_lines(text).rstrip()
            if body:
                chunks.append(body)
        return self._join_container_log_chunks(chunks).rstrip()

    def _container_thread_transcript(
        self,
        worker_backend: Any,
        records: list[Any],
        *,
        lines: int,
    ) -> str:
        first_execution = records[0]
        session_error: Exception | None = None
        try:
            transcript = worker_backend.get_log_snapshot(
                first_execution.execution_id,
                lines=lines,
                mode="session",
            ).rstrip()
        except Exception as exc:
            session_error = exc
            transcript = ""
        if transcript:
            return transcript
        fallback = self._container_thread_execution_fallback_log(
            worker_backend,
            records,
            lines=lines,
        ).rstrip()
        if fallback:
            return fallback
        if session_error is not None:
            return f"[log unavailable: {session_error}]"
        return ""

    def _container_execution_thread_probe_text(self, worker_backend: Any, execution: Any) -> str:
        page_reader = getattr(worker_backend, "get_log_page", None)
        if callable(page_reader):
            try:
                page = page_reader(
                    execution.execution_id,
                    limit=1,
                    offset=0,
                    query="session id:",
                    mode="raw",
                )
                text = str(page.get("text") or "")
                if text:
                    return text
            except Exception:
                pass
        try:
            return str(
                worker_backend.get_log_snapshot(
                    execution.execution_id,
                    lines=0,
                    mode="raw",
                )
            )
        except Exception as exc:
            return f"[log unavailable: {exc}]"

    def _container_thread_log_chunks(
        self,
        worker_backend: Any,
        execution_records: list[Any],
        *,
        lines: int,
    ) -> list[str]:
        sorted_records = sorted(execution_records, key=self._container_execution_sort_key)
        grouped_records: dict[str, list[Any]] = {}
        unthreaded_chunks: list[tuple[tuple[datetime, str], str]] = []
        for execution in sorted_records:
            raw_text = ""
            thread_id = self._container_execution_stored_thread_id(execution)
            if thread_id is None:
                try:
                    raw_text = self._container_execution_thread_probe_text(
                        worker_backend, execution
                    )
                except Exception as exc:
                    raw_text = f"[log unavailable: {exc}]"
                thread_id = self._container_log_thread_id(raw_text)
            if thread_id is None:
                header_parts = [
                    "thread=unthreaded",
                    f"first_execution={execution.execution_id}",
                    "executions=1",
                ]
                if execution.started_at is not None:
                    header_parts.append(f"started={execution.started_at.isoformat()}")
                header = "=== " + " ".join(header_parts) + " ==="
                body = raw_text.rstrip()
                unthreaded_chunks.append(
                    (
                        self._container_execution_sort_key(execution),
                        header if not body else header + "\n" + body,
                    )
                )
                continue
            grouped_records.setdefault(thread_id, []).append(execution)

        threaded_chunks: list[tuple[tuple[datetime, str], str]] = []
        for thread_id, records in grouped_records.items():
            ordered_records = sorted(records, key=self._container_execution_sort_key)
            first_execution = ordered_records[0]
            header_parts = [
                f"thread={thread_id}",
                f"first_execution={first_execution.execution_id}",
                f"executions={len(ordered_records)}",
            ]
            if first_execution.started_at is not None:
                header_parts.append(f"started={first_execution.started_at.isoformat()}")
            header = "=== " + " ".join(header_parts) + " ==="
            transcript = self._container_thread_transcript(
                worker_backend,
                ordered_records,
                lines=lines,
            )
            threaded_chunks.append(
                (
                    self._container_execution_sort_key(first_execution),
                    header if not transcript else header + "\n" + transcript,
                )
            )

        return [
            chunk
            for _, chunk in sorted(
                [*threaded_chunks, *unthreaded_chunks],
                key=lambda item: item[0],
            )
        ]

    def _container_thread_groups(
        self,
        worker_backend: Any,
        execution_records: list[Any],
    ) -> list[tuple[tuple[datetime, str], str | None, list[Any], str | None]]:
        sorted_records = sorted(execution_records, key=self._container_execution_sort_key)
        grouped_records: dict[str, list[Any]] = {}
        unthreaded_groups: list[tuple[tuple[datetime, str], str | None, list[Any], str | None]] = []
        for execution in sorted_records:
            raw_text = None
            thread_id = self._container_execution_stored_thread_id(execution)
            if thread_id is None:
                raw_text = self._container_execution_thread_probe_text(worker_backend, execution)
                thread_id = self._container_log_thread_id(raw_text)
            if thread_id is None:
                unthreaded_groups.append(
                    (
                        self._container_execution_sort_key(execution),
                        None,
                        [execution],
                        raw_text,
                    )
                )
                continue
            grouped_records.setdefault(thread_id, []).append(execution)

        threaded_groups: list[tuple[tuple[datetime, str], str | None, list[Any], str | None]] = []
        for thread_id, records in grouped_records.items():
            ordered_records = sorted(records, key=self._container_execution_sort_key)
            threaded_groups.append(
                (
                    self._container_execution_sort_key(ordered_records[0]),
                    thread_id,
                    ordered_records,
                    None,
                )
            )
        return sorted([*threaded_groups, *unthreaded_groups], key=lambda item: item[0])

    def _container_thread_window_chunks(
        self,
        worker_backend: Any,
        execution_records: list[Any],
        *,
        target_lines: int,
    ) -> tuple[list[str], bool]:
        sorted_records = sorted(execution_records, key=self._container_execution_sort_key)
        chunks: list[str] = []
        grouped_records: dict[str, list[Any]] = {}
        thread_chunk_indexes: dict[str, int] = {}
        dirty_thread_ids: set[str] = set()
        line_count = 0
        truncated_after = False
        for index, execution in enumerate(sorted_records):
            raw_text = None
            thread_id = self._container_execution_stored_thread_id(execution)
            if thread_id is None:
                raw_text = self._container_execution_thread_probe_text(worker_backend, execution)
                thread_id = self._container_log_thread_id(raw_text)
            if thread_id is not None:
                records = grouped_records.setdefault(thread_id, [])
                records.append(execution)
                chunk_index = thread_chunk_indexes.get(thread_id)
                if chunk_index is not None:
                    dirty_thread_ids.add(thread_id)
                    continue
                thread_chunk_indexes[thread_id] = len(chunks)
            else:
                records = [execution]
            chunk = self._container_thread_group_chunk(
                worker_backend,
                thread_id=thread_id,
                records=records,
                raw_text=raw_text,
            )
            chunks.append(chunk)
            line_count += len(chunk.splitlines()) + (1 if len(chunks) > 1 else 0)
            if line_count >= target_lines:
                truncated_after = index < len(sorted_records) - 1
                break
        for thread_id in dirty_thread_ids:
            chunk_index = thread_chunk_indexes.get(thread_id)
            if chunk_index is None:
                continue
            chunks[chunk_index] = self._container_thread_group_chunk(
                worker_backend,
                thread_id=thread_id,
                records=grouped_records[thread_id],
                raw_text=None,
            )
        return chunks, truncated_after

    def _container_thread_group_chunk(
        self,
        worker_backend: Any,
        *,
        thread_id: str | None,
        records: list[Any],
        raw_text: str | None,
    ) -> str:
        first_execution = records[0]
        header_parts = [
            f"thread={thread_id or 'unthreaded'}",
            f"first_execution={first_execution.execution_id}",
            f"executions={len(records)}",
        ]
        if first_execution.started_at is not None:
            header_parts.append(f"started={first_execution.started_at.isoformat()}")
        header = "=== " + " ".join(header_parts) + " ==="
        if thread_id is None:
            body = str(raw_text or "").rstrip()
            return header if not body else header + "\n" + body
        transcript = self._container_thread_transcript(
            worker_backend,
            records,
            lines=0,
        )
        return header if not transcript else header + "\n" + transcript

    def _container_thread_log_page(
        self,
        *,
        session_id: str,
        execution_records: list[Any],
        worker_backend: Any,
        limit: int,
        offset: int,
        query: str | None,
    ) -> dict[str, object]:
        if not execution_records:
            return _log_page_from_start(
                f"No container executions have run for session {session_id}.\n",
                limit=limit,
                offset=offset,
                query=query,
            )
        normalized_query = str(query or "").strip()
        chunks: list[str] = []
        target_lines = max(1, int(limit)) + max(0, int(offset)) + 1
        line_count = 0
        truncated_after = False
        if (
            not normalized_query
            and len(execution_records) > _CONTAINER_THREAD_FULL_GROUP_RECORD_LIMIT
        ):
            chunks, truncated_after = self._container_thread_window_chunks(
                worker_backend,
                execution_records,
                target_lines=target_lines,
            )
        else:
            groups = self._container_thread_groups(worker_backend, execution_records)
            for index, (_sort_key, thread_id, records, raw_text) in enumerate(groups):
                chunk = self._container_thread_group_chunk(
                    worker_backend,
                    thread_id=thread_id,
                    records=records,
                    raw_text=raw_text,
                )
                chunks.append(chunk)
                if not normalized_query:
                    line_count += len(chunk.splitlines()) + (1 if len(chunks) > 1 else 0)
                    if line_count >= target_lines:
                        truncated_after = index < len(groups) - 1
                        break
        page = _log_page_from_start(
            self._join_container_log_chunks(chunks),
            limit=limit,
            offset=offset,
            query=normalized_query or None,
        )
        if truncated_after:
            page["has_more_before"] = True
            page["total_lines"] = None
            page["end_line"] = None
        return page

    def _container_execution_log_page(
        self,
        *,
        session_id: str,
        execution_records: list[Any],
        worker_backend: Any,
        limit: int,
        offset: int,
        query: str | None,
    ) -> dict[str, object]:
        if not execution_records:
            return _log_page_from_end(
                f"No container executions have run for session {session_id}.\n",
                limit=limit,
                offset=offset,
                query=query,
            )

        normalized_query = str(query or "").strip()
        sorted_records = sorted(execution_records, key=self._container_execution_sort_key)
        if not normalized_query and offset == 0:
            page = _log_page_from_end(
                self._container_execution_log_chunk(
                    worker_backend,
                    sorted_records[-1],
                    lines=0,
                    mode="execution",
                ),
                limit=limit,
                offset=0,
            )
            if len(sorted_records) > 1:
                page["has_more_before"] = True
                page["total_lines"] = None
                page["start_line"] = None
                page["end_line"] = None
            return page

        chunks: list[str] = []
        truncated_before = False
        target_lines = max(1, limit) + max(0, offset) + 1
        line_count = 0
        records_to_read = (
            list(enumerate(sorted_records))
            if normalized_query
            else list(reversed(list(enumerate(sorted_records))))
        )
        for _original_index, execution in records_to_read:
            chunk = self._container_execution_log_chunk(
                worker_backend,
                execution,
                lines=0,
                mode="execution",
            )
            if normalized_query:
                chunks.append(chunk)
                continue
            chunks.insert(0, chunk)
            line_count += len(chunk.splitlines()) + (1 if len(chunks) > 1 else 0)
            if line_count >= target_lines:
                truncated_before = _original_index > 0
                break

        page = _log_page_from_end(
            self._join_container_log_chunks(chunks),
            limit=limit,
            offset=offset,
            query=normalized_query or None,
        )
        if truncated_before:
            page["has_more_before"] = True
            page["total_lines"] = None
            page["start_line"] = None
            page["end_line"] = None
        return page

    def get_admin_agent_log_page(
        self,
        agent_id: str,
        *,
        backend: str | None = None,
        view: str | None = None,
        limit: int = 200,
        offset: int = 0,
        query: str | None = None,
        repo_name: str | None = None,
        vm_user: str = "agent",
    ) -> dict[str, object]:
        normalized_limit = max(1, min(int(limit), 5000))
        normalized_offset = max(0, int(offset))
        normalized_query = str(query or "").strip()
        normalized_view = self._normalize_agent_log_view(view)
        selected_backend = str(backend or "libvirt_vm").strip()
        if selected_backend in {"libvirt", "libvirt_vm"}:
            window_lines = normalized_limit + normalized_offset
            if normalized_query:
                window_lines = max(window_lines, 5000)
            text = self.get_agent_log_snapshot(
                agent_id,
                lines=window_lines,
                repo_name=repo_name,
                vm_user=vm_user,
            )
            page = _log_page_from_end(
                text,
                limit=normalized_limit,
                offset=normalized_offset,
                query=normalized_query or None,
            )
            if not normalized_query and page["returned_lines"] >= normalized_limit:
                page["has_more_before"] = True
                page["total_lines"] = None
                page["start_line"] = None
                page["end_line"] = None
            page["view"] = normalized_view
            return page
        if selected_backend == "container_worker_vm":
            session, record = self._resolve_container_agent_session(agent_id, require_session=False)
            session_id = (
                str(session.session_id).strip()
                if session is not None
                else str(getattr(record, "session_id", "") or "").strip()
            )
            execution_records = (
                self._container_execution_records_for_session(session_id) if session_id else []
            )
            worker_backend = self._container_worker_backend(
                directory_path=(
                    session.directory_path
                    if session is not None
                    else getattr(record, "directory_path", None)
                ),
                worker_url=self._container_worker_url_for_target(
                    session=session,
                    record=record,
                    records=execution_records,
                ),
            )
            target = agent_id
            if session is not None and record is None:
                if normalized_view == "threads":
                    page = self._container_thread_log_page(
                        session_id=session_id,
                        execution_records=execution_records,
                        worker_backend=worker_backend,
                        limit=normalized_limit,
                        offset=normalized_offset,
                        query=normalized_query or None,
                    )
                else:
                    page = self._container_execution_log_page(
                        session_id=session_id,
                        execution_records=execution_records,
                        worker_backend=worker_backend,
                        limit=normalized_limit,
                        offset=normalized_offset,
                        query=normalized_query or None,
                    )
                page["view"] = normalized_view
                return page
            page_reader = getattr(worker_backend, "get_log_page", None)
            if callable(page_reader):
                page = page_reader(
                    target,
                    limit=normalized_limit,
                    offset=normalized_offset,
                    query=normalized_query or None,
                    mode="execution",
                )
                page["view"] = normalized_view
                return page
            text = worker_backend.get_log_snapshot(
                target, lines=normalized_limit + normalized_offset, mode="execution"
            )
            page = _log_page_from_end(
                text,
                limit=normalized_limit,
                offset=normalized_offset,
                query=normalized_query or None,
            )
            page["view"] = normalized_view
            return page
        raise ValueError(f"Unknown agent backend: {selected_backend}")

    def run_admin_agent_action(
        self,
        agent_id: str,
        action: str,
        *,
        backend: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        selected_backend = self._normalize_agent_backend(backend)
        body = payload or {}
        if selected_backend == "libvirt_vm":
            if action == "start":
                overridden, result = self._admin_service_override("start_vm", agent_id)
                if not overridden:
                    result = self.start_vm(agent_id)
            elif action == "shutdown":
                result = self.shutdown_vm(agent_id)
            elif action == "enable-agent":
                result = self.enable_agent(agent_id)
            elif action == "disable-agent":
                result = self.disable_agent(agent_id)
            elif action == "kill-agent":
                pid = body.get("pid")
                if pid in {None, ""}:
                    raise ValueError("kill-agent requires a running agent pid")
                result = self.kill_agent(agent_id, int(pid))
            elif action == "prompt-agent":
                return self._prompt_admin_agent(agent_id, backend=selected_backend, payload=body)
            elif action == "resume-agent":
                prompt = str(body.get("prompt") or "").strip()
                if not prompt:
                    raise ValueError("resume-agent requires a non-empty prompt")
                result = self.resume_agent(
                    agent_id,
                    prompt,
                    model=str(body.get("model") or "").strip() or None,
                    resume_mode=str(body.get("resume_mode") or "same_context").strip()
                    or "same_context",
                )
            elif action == "install-skills":
                return self.install_admin_agent_skills(
                    agent_id,
                    backend="libvirt_vm",
                    skill_names=self._agent_string_list(body, "codex_skills"),
                    vm_user=self._agent_optional_string(body, "vm_user") or "agent",
                )
            elif action == "destroy":
                result = self.destroy_vm(agent_id)
            else:
                raise LookupError(f"Unknown VM agent action: {action}")
            return {
                "success": result.success,
                "agent_id": result.vm_name,
                "backend": "libvirt_vm",
                "target_name": result.vm_name,
                "action": result.action,
                "message": result.message,
                "provider": result.provider,
                "provider_operation_id": result.provider_operation_id,
                "thread_id": result.thread_id,
                "target_dir": result.target_dir,
                "guest_repo_name": result.guest_repo_name,
                "remote_url": result.remote_url,
            }
        if selected_backend == "container_worker_vm":
            if action == "install-skills":
                return self.install_admin_agent_skills(
                    agent_id,
                    backend="container_worker_vm",
                    skill_names=self._agent_string_list(body, "codex_skills"),
                    vm_user=self._agent_optional_string(body, "vm_user") or "agent",
                )
            if action == "prompt-agent":
                return self._prompt_admin_agent(
                    agent_id,
                    backend=selected_backend,
                    payload=body,
                )
            session, record = self._resolve_container_agent_session(agent_id, require_session=False)
            is_session_target = session is not None and record is None
            session_id = (
                str(session.session_id).strip()
                if session is not None
                else str(getattr(record, "session_id", "") or "").strip()
            )
            execution_records = (
                self._container_execution_records_for_session(session_id) if session_id else []
            )
            worker_backend = self._container_worker_backend(
                directory_path=(
                    session.directory_path
                    if session is not None
                    else getattr(record, "directory_path", None)
                ),
                worker_url=self._container_worker_url_for_target(
                    session=session,
                    record=record,
                    records=execution_records,
                ),
            )
            if action == "stop":
                if session is not None and record is None:
                    active_records = [
                        candidate
                        for candidate in execution_records
                        if self._agent_status_is_active(
                            self._container_agent_status(candidate.last_status)
                        )
                    ]
                    if not active_records:
                        raise ValueError(
                            f"No active container executions for session {session.session_id}"
                        )
                    statuses = [
                        worker_backend.stop(candidate.execution_id) for candidate in active_records
                    ]
                    status = statuses[-1]
                    message = (
                        f"Stopped {len(statuses)} active container execution"
                        f"{'' if len(statuses) == 1 else 's'} for session {session.session_id}"
                    )
                else:
                    status = worker_backend.stop(agent_id)
                    message = status.message or "stop completed"
            elif action == "destroy":
                if session is not None and record is None:
                    if not execution_records:
                        raise ValueError(
                            f"No container executions for session {session.session_id}"
                        )
                    statuses = [
                        worker_backend.destroy(candidate.execution_id)
                        for candidate in execution_records
                    ]
                    status = statuses[-1]
                    message = (
                        f"Destroyed {len(statuses)} container execution"
                        f"{'' if len(statuses) == 1 else 's'} for session {session.session_id}"
                    )
                else:
                    status = worker_backend.destroy(agent_id)
                    message = status.message or "destroy completed"
            else:
                raise LookupError(f"Unknown container worker agent action: {action}")
            return {
                "success": True,
                "agent_id": session.session_id if is_session_target else status.execution_id,
                "backend": "container_worker_vm",
                "target_name": (
                    self._container_session_target_name(session)
                    if is_session_target
                    else status.target_name
                ),
                "action": action,
                "message": message,
                "status": status.status.value,
                "session_id": session.session_id if session is not None else status.session_id,
            }
        raise ValueError(f"Unknown agent backend: {selected_backend}")

    def _prompt_admin_agent(
        self,
        agent_id: str,
        *,
        backend: str,
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        overridden, result = self._admin_service_override(
            "_prompt_admin_agent", agent_id, backend=backend, payload=payload
        )
        if overridden:
            return result

        prompt = self._agent_optional_string(payload, "prompt")
        if not prompt:
            raise ValueError("prompt-agent requires a non-empty prompt")
        model = self._agent_optional_string(payload, "model")
        dispatch_id = self._agent_optional_string(payload, "dispatch_id")
        resume_thread_id = self._agent_optional_string(payload, "resume_thread_id")
        resume_mode = self._agent_optional_string(payload, "resume_mode")
        vm_user = self._agent_optional_string(payload, "vm_user") or "agent"

        if backend == "libvirt_vm":
            session = self._resolve_optional_vm_agent_session(agent_id)
            session_id = session.session_id if session is not None else None
            target_name = (
                str((session.vm_id if session is not None else None) or agent_id).strip()
                or agent_id
            )
            directory_path = (
                str((session.directory_path if session is not None else None) or "root").strip()
                or "root"
            )
            agent_response_id = target_name
        elif backend == "container_worker_vm":
            session, record = self._resolve_container_agent_session(agent_id, require_session=True)
            if session is None:
                raise ValueError(f"Unknown container worker agent target: {agent_id}")
            del record
            session_id = session.session_id
            target_name = self._container_session_target_name(session)
            directory_path = (
                str(getattr(session, "directory_path", None) or "root").strip() or "root"
            )
            agent_response_id = session_id
        else:
            raise ValueError(f"Unknown agent backend: {backend}")

        target = session_id or target_name
        effect_id = f"admin-agent-prompt:{backend}:{target}"
        effect = AgentEnqueuePromptEffectDefinition(
            id=effect_id,
            display_name="Admin prompt agent",
            type="effect:agent:enqueue-prompt",
            target=target,
            session_id=session_id,
            vm_name=target_name,
            prompt=prompt,
            model=model,
            resume_thread_id=resume_thread_id,
            resume_mode=resume_mode,
            dispatch_id=dispatch_id,
            vm_user=vm_user,
            queue_key=effect_id,
        )
        provider = EffectProviderFactory(
            provider_config_resolver=self.runtime.provider_config,
            admin_service=self,
        ).build(effect, directory_path=directory_path)
        dispatch_started_at = datetime.now(UTC)
        try:
            result = provider.execute()
        except RuntimeError as exc:
            queued_result = self._queued_admin_prompt_result_after_dispatch_error(
                queue_key=effect_id,
                target_name=target_name,
                session_id=session_id,
                backend=backend,
                attempted_at=dispatch_started_at,
                error=exc,
            )
            if queued_result is None:
                raise
            result = queued_result
        if (
            backend == "container_worker_vm"
            and not bool(result.get("queued"))
            and str(result.get("output_mode") or "").strip() == "on_completion"
        ):
            self._record_direct_container_admin_prompt_execution(
                effect_id=effect_id,
                target=target,
                target_name=target_name,
                session_id=session_id,
                prompt=prompt,
                resume_thread_id=resume_thread_id,
                vm_user=vm_user,
                directory_path=directory_path,
                result=result,
            )
        queued = bool(result.get("queued"))
        message = str(result.get("message") or "").strip()
        if not message:
            message = (
                f"Queued prompt for {target_name}"
                if queued
                else f"Dispatched prompt to {target_name}"
            )
        response = dict(result)
        response.update(
            {
                "success": bool(result.get("success", True)),
                "agent_id": agent_response_id,
                "backend": backend,
                "target_name": target_name,
                "action": "prompt-agent",
                "message": message,
                "status": "queued" if queued else "dispatched",
                "session_id": session_id,
            }
        )
        return response

    def _queued_admin_prompt_result_after_dispatch_error(
        self,
        *,
        queue_key: str,
        target_name: str,
        session_id: str | None,
        backend: str,
        attempted_at: datetime,
        error: RuntimeError,
    ) -> dict[str, Any] | None:
        from agent_sandbox.messaging.agent_prompt_queue import AgentPromptQueueStore

        queued = AgentPromptQueueStore(self.config.runtime_dir / "queued-agent-prompts.json").get(
            queue_key
        )
        if queued is None:
            return None
        queued_updated_at = queued.updated_at
        if queued_updated_at.tzinfo is None:
            queued_updated_at = queued_updated_at.replace(tzinfo=UTC)
        if queued_updated_at < attempted_at - timedelta(seconds=1):
            return None
        queued_session_id = str(queued.session_id or "").strip() or session_id
        queued_target_name = str(queued.vm_name or "").strip() or target_name
        return {
            "success": True,
            "queued": True,
            "queue_key": queued.queue_key,
            "vm_name": queued_target_name,
            "session_id": queued_session_id,
            "coalesced_count": queued.coalesced_count,
            "execution_backend": backend,
            "message": f"Queued prompt for {queued_target_name}",
            "dispatch_error": str(error),
        }

    def _record_direct_container_admin_prompt_execution(
        self,
        *,
        effect_id: str,
        target: str,
        target_name: str,
        session_id: str | None,
        prompt: str,
        resume_thread_id: str | None,
        vm_user: str,
        directory_path: str,
        result: Mapping[str, Any],
    ) -> None:
        dispatch_id = str(result.get("dispatch_id") or "").strip()
        worker_execution_id = str(result.get("execution_id") or "").strip()
        if not dispatch_id or not worker_execution_id or not session_id:
            raise RuntimeError(
                "prompt-agent container dispatch did not include execution tracking identifiers"
            )

        from agent_sandbox.daemons.effect_orchestrator import (
            EffectExecutionRecord,
            JsonExecutionStore,
            SqliteExecutionStore,
        )

        event_id = f"admin-agent-prompt:{session_id}:{dispatch_id}"
        idempotency_key = f"{event_id}::{effect_id}"
        stores = self._workflow_run_store_paths()
        if self._workflow_uses_sqlite_store(stores):
            execution_store = SqliteExecutionStore(Path(stores["orchestration_store_path"]))
        else:
            execution_store = JsonExecutionStore(Path(stores["effect_execution_store_path"]))
        if execution_store.find_execution_by_idempotency_key(idempotency_key) is not None:
            return

        resolved_config = {
            "target": target,
            "vm_name": target_name,
            "session_id": session_id,
            "prompt": prompt,
            "model": result.get("model"),
            "vm_user": vm_user,
            "resume_thread_id": resume_thread_id,
            "directory_path": directory_path,
            "execution_backend": "container_worker_vm",
            "queue_key": effect_id,
        }
        input_payload = {
            "target": target,
            "vm_name": target_name,
            "session_id": session_id,
            "prompt": prompt,
            "model": result.get("model"),
            "vm_user": vm_user,
            "resume_thread_id": resume_thread_id,
            "queue_key": effect_id,
        }
        outputs = dict(result)
        outputs.update(
            {
                "dispatch_id": dispatch_id,
                "thread_id": str(result.get("thread_id") or resume_thread_id or dispatch_id),
                "success": True,
                "queued": False,
                "output_mode": "on_completion",
                "vm_name": target_name,
                "session_id": session_id,
                "execution_backend": "container_worker_vm",
                "execution_id": worker_execution_id,
                "queue_key": effect_id,
            }
        )
        now = datetime.now(UTC)
        execution_store.record_execution(
            EffectExecutionRecord(
                execution_id=(
                    f"exec-{hashlib.sha1(idempotency_key.encode('utf-8')).hexdigest()[:16]}"
                ),
                idempotency_key=idempotency_key,
                event_id=event_id,
                trace_id=f"admin-agent-prompt:{session_id}",
                source_node_id=effect_id,
                source_trigger_type="admin:agent:prompt",
                effect_id=effect_id,
                effect_type="effect:agent:enqueue-prompt",
                status="waiting",
                started_at=now,
                finished_at=now,
                resolved_config_hash=self._stable_json_revision(resolved_config),
                resolved_config=resolved_config,
                input_payload=input_payload,
                outputs=outputs,
            )
        )

    def _resolve_optional_vm_agent_session(self, agent_id: str) -> SessionManifest | None:
        direct_session = self.runtime.session_repository.get_session(agent_id)
        if direct_session is not None:
            return direct_session
        matches = self.runtime.session_repository.list_sessions_for_vm(agent_id)
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            session_ids = ", ".join(sorted(session.session_id for session in matches))
            raise ValueError(f"VM {agent_id!r} is associated with multiple sessions: {session_ids}")
        return None

    def resolve_vm_defaults(self, directory_path: str) -> EffectiveVmDefaultsConfig:
        return self._resolve_vm_defaults(directory_path)

    def agent_create_options(self, directory_path: str | None = None) -> dict[str, Any]:
        normalized_directory = self._normalize_directory_path(directory_path)
        vm_defaults = self.resolve_vm_defaults(normalized_directory)
        provider_config = self.runtime.provider_config.resolve_for_directory(normalized_directory)
        libvirt_templates = [
            {"alias": alias, "image_ref": image_ref}
            for alias, image_ref in sorted(vm_defaults.libvirt_vm_templates.items())
        ]
        container_images = [
            {"alias": alias, "image_ref": image_ref}
            for alias, image_ref in sorted(provider_config.container_worker_images.items())
        ]
        return {
            "directory_path": normalized_directory,
            "backends": ["libvirt_vm", "container_worker_vm"],
            "agent_providers": [
                {
                    "value": "codex",
                    "label": "Codex",
                    "execution_backends": ["libvirt_vm", "container_worker_vm"],
                },
                {
                    "value": "claude",
                    "label": "Claude",
                    "execution_backends": ["libvirt_vm", "container_worker_vm"],
                },
                {
                    "value": "copilot",
                    "label": "Copilot",
                    "execution_backends": ["libvirt_vm"],
                },
            ],
            "model_provider_modes": [
                {"value": "openai_public", "label": "Public OpenAI"},
                {"value": "local_openai", "label": "Local OpenAI"},
                {"value": "azure_openai", "label": "Azure OpenAI"},
            ],
            "communication_targets": [
                option.model_dump(mode="json")
                for option in self._agent_communication_target_options(normalized_directory)
            ],
            "libvirt": {
                "default_template": vm_defaults.libvirt_vm_default_template,
                "templates": libvirt_templates,
            },
            "container_worker": {
                "default_image": provider_config.container_worker_default_image,
                "images": container_images,
            },
        }

    def _session_repository(self):
        runtime = self._deps.runtime
        return as_session_manifest_repository(runtime.session_repository)

    def _resolve_vm_name_from_target(self, target: str) -> str:
        repository = self._session_repository()
        try:
            resolved = repository.resolve_vm_name_from_target(target)
        except Exception:
            sessions = repository.read_all()
            return session_helpers.resolve_vm_name_from_target(target, sessions)
        normalized_target = str(target or "").strip()
        if resolved and resolved != normalized_target:
            return resolved
        sessions = repository.read_all()
        return session_helpers.resolve_vm_name_from_target(target, sessions)

    def resolve_chat_assistant_session(
        self,
        *,
        user_id: str,
        backend: str,
        assistant_scope: str = "chat_assistant",
        settings: dict[str, Any] | None = None,
        thread_id: str | None = None,
        user_message_id: str | None = None,
    ) -> dict[str, str]:
        normalized_backend = str(backend or "libvirt_vm").strip() or "libvirt_vm"
        normalized_user_id = str(user_id or "anonymous").strip() or "anonymous"
        scope = str(assistant_scope or "chat_assistant").strip() or "chat_assistant"
        safe_user_id = _safe_chat_assistant_identifier(normalized_user_id)

        mapping_user_id = normalized_user_id
        existing = self._assistant_session_repo.get_mapping(
            user_id=mapping_user_id,
            backend=normalized_backend,
            assistant_scope=scope,
        )
        if existing is not None:
            existing_chat_thread_id = str(existing.get("thread_id") or "").strip()
            requested_chat_thread_id = str(thread_id or "").strip()
            chat_thread_changed = (
                bool(existing_chat_thread_id)
                and bool(requested_chat_thread_id)
                and existing_chat_thread_id != requested_chat_thread_id
            )
            if chat_thread_changed:
                agent_thread_id = ""
                self._assistant_session_repo.upsert_mapping(
                    user_id=mapping_user_id,
                    backend=normalized_backend,
                    assistant_scope=scope,
                    session_id=existing["session_id"],
                    agent_thread_id="",
                    thread_id=requested_chat_thread_id,
                )
                self._clear_stored_chat_assistant_thread_id(existing["session_id"])
            else:
                stored_agent_thread_id = (
                    self._stored_chat_assistant_thread_id(existing["session_id"])
                    if normalized_backend == ExecutionBackendKind.CONTAINER_WORKER_VM.value
                    else ""
                )
                existing_agent_thread_id = existing.get("agent_thread_id") or ""
                if _is_synthetic_chat_assistant_thread_id(existing_agent_thread_id):
                    existing_agent_thread_id = ""
                agent_thread_id = stored_agent_thread_id or existing_agent_thread_id
            self._ensure_chat_assistant_manifest(
                session_id=existing["session_id"],
                backend=normalized_backend,
                user_id=normalized_user_id,
                safe_user_id=safe_user_id,
                agent_thread_id=agent_thread_id,
                settings=settings,
                thread_id=thread_id,
                user_message_id=user_message_id,
            )
            self._ensure_chat_assistant_target(
                session_id=existing["session_id"],
                backend=normalized_backend,
                safe_user_id=safe_user_id,
                settings=settings,
            )
            logging.getLogger(__name__).info(
                "metric_chat_session_mapping_reused backend=%s assistant_scope=%s mapping_user_id=%s session_id=%s",
                normalized_backend,
                scope,
                mapping_user_id,
                existing.get("session_id"),
            )
            return {
                "session_id": existing["session_id"],
                "agent_thread_id": agent_thread_id,
                "vm_name": _chat_assistant_vm_name(
                    backend=normalized_backend,
                    session_id=existing["session_id"],
                ),
                "execution_backend": normalized_backend,
                "capability_profile": _assistant_capability_profile(settings),
            }

        if normalized_backend == "libvirt_vm":
            session_id = "assistant-shared"
        else:
            session_id = f"assistant-{safe_user_id}"
        agent_thread_id = ""
        persisted = self._assistant_session_repo.upsert_mapping(
            user_id=mapping_user_id,
            backend=normalized_backend,
            assistant_scope=scope,
            session_id=session_id,
            agent_thread_id=agent_thread_id,
            thread_id=thread_id,
        )
        self._ensure_chat_assistant_manifest(
            session_id=session_id,
            backend=normalized_backend,
            user_id=normalized_user_id,
            safe_user_id=safe_user_id,
            agent_thread_id=agent_thread_id,
            settings=settings,
            thread_id=thread_id,
            user_message_id=user_message_id,
        )
        self._ensure_chat_assistant_target(
            session_id=session_id,
            backend=normalized_backend,
            safe_user_id=safe_user_id,
            settings=settings,
        )
        logging.getLogger(__name__).info(
            "metric_chat_session_mapping_created backend=%s assistant_scope=%s mapping_user_id=%s session_id=%s",
            normalized_backend,
            scope,
            mapping_user_id,
            session_id,
        )
        return {
            "session_id": persisted["session_id"],
            "agent_thread_id": agent_thread_id,
            "vm_name": _chat_assistant_vm_name(
                backend=normalized_backend,
                session_id=persisted["session_id"],
            ),
            "execution_backend": normalized_backend,
            "capability_profile": _assistant_capability_profile(settings),
        }

    def persist_chat_assistant_agent_thread_id(
        self,
        *,
        session_id: str,
        agent_thread_id: str,
        backend: str | None = None,
        assistant_scope: str | None = None,
        user_id: str | None = None,
        thread_id: str | None = None,
        queue_key: str | None = None,
    ) -> int:
        normalized_session_id = str(session_id or "").strip()
        normalized_thread_id = str(agent_thread_id or "").strip()
        if not normalized_session_id or not normalized_thread_id:
            return 0
        normalized_backend = str(backend or "").strip()
        normalized_scope = str(assistant_scope or "").strip() or "admin_chat"
        chat_thread_id = str(thread_id or "").strip() or _chat_thread_id_from_queue_key(queue_key)
        repository = self._session_repository()
        session = repository.get_session(normalized_session_id)
        metadata = dict(getattr(session, "metadata", {}) or {}) if session is not None else {}
        chat_threads = metadata.get("admin_chat_threads")
        if not isinstance(chat_threads, dict):
            chat_threads = {}
        normalized_user_id = str(user_id or "").strip()
        if not normalized_user_id and chat_thread_id:
            for raw_mapping in chat_threads.values():
                if not isinstance(raw_mapping, dict):
                    continue
                if str(raw_mapping.get("thread_id") or "").strip() != chat_thread_id:
                    continue
                normalized_user_id = str(raw_mapping.get("user_id") or "").strip()
                break

        updated = 0
        skip_repository_update = False
        if normalized_user_id:
            current_mapping = self._assistant_session_repo.get_mapping(
                user_id=normalized_user_id,
                backend=normalized_backend,
                assistant_scope=normalized_scope,
            )
            current_chat_thread_id = (
                str((current_mapping or {}).get("thread_id") or "").strip()
                if current_mapping is not None
                else ""
            )
            should_update_mapping = not (
                current_chat_thread_id
                and chat_thread_id
                and current_chat_thread_id != chat_thread_id
            )
            if not should_update_mapping:
                skip_repository_update = True
        if normalized_user_id and not skip_repository_update:
            self._assistant_session_repo.upsert_mapping(
                user_id=normalized_user_id,
                backend=normalized_backend,
                assistant_scope=normalized_scope,
                session_id=normalized_session_id,
                agent_thread_id=normalized_thread_id,
                thread_id=chat_thread_id,
            )
            updated += 1
        elif not skip_repository_update and not chat_thread_id:
            updated += self._assistant_session_repo.update_agent_thread_id_for_session(
                session_id=normalized_session_id,
                agent_thread_id=normalized_thread_id,
                backend=normalized_backend,
                assistant_scope=normalized_scope,
            )

        if session is not None and chat_thread_id:
            replacement: dict[str, Any] | None = None
            for raw_key, raw_mapping in list(chat_threads.items()):
                if not isinstance(raw_mapping, dict):
                    continue
                if str(raw_mapping.get("thread_id") or "").strip() != chat_thread_id:
                    continue
                replacement = dict(raw_mapping)
                if raw_key != normalized_thread_id:
                    chat_threads.pop(raw_key, None)
            if replacement is not None:
                replacement["thread_id"] = chat_thread_id
                if normalized_user_id:
                    replacement["user_id"] = normalized_user_id
                chat_threads[normalized_thread_id] = replacement
                metadata["admin_chat_threads"] = chat_threads
                repository.upsert_session(session.model_copy(update={"metadata": metadata}))
        return updated

    def reset_chat_assistant_thread_context(
        self,
        *,
        user_id: str,
        backend: str | None = None,
        assistant_scope: str | None = None,
        thread_id: str | None = None,
    ) -> dict[str, str]:
        normalized_backend = str(backend or "libvirt_vm").strip() or "libvirt_vm"
        normalized_user_id = str(user_id or "anonymous").strip() or "anonymous"
        normalized_scope = str(assistant_scope or "admin_chat").strip() or "admin_chat"
        normalized_chat_thread_id = str(thread_id or "").strip()
        existing = self._assistant_session_repo.get_mapping(
            user_id=normalized_user_id,
            backend=normalized_backend,
            assistant_scope=normalized_scope,
        )
        if existing is not None:
            session_id = existing["session_id"]
        elif normalized_backend == "libvirt_vm":
            session_id = "assistant-shared"
        else:
            session_id = f"assistant-{_safe_chat_assistant_identifier(normalized_user_id)}"

        self._assistant_session_repo.upsert_mapping(
            user_id=normalized_user_id,
            backend=normalized_backend,
            assistant_scope=normalized_scope,
            session_id=session_id,
            agent_thread_id="",
            thread_id=normalized_chat_thread_id,
        )

        repository = self._session_repository()
        session = repository.get_session(session_id)
        if session is not None:
            metadata = dict(getattr(session, "metadata", {}) or {})
            if "agent_thread_id" in metadata:
                metadata.pop("agent_thread_id", None)
                repository.upsert_session(session.model_copy(update={"metadata": metadata}))

        return {
            "session_id": session_id,
            "agent_thread_id": "",
            "thread_id": normalized_chat_thread_id,
        }

    def _stored_chat_assistant_thread_id(self, session_id: str) -> str:
        session = self._session_repository().get_session(session_id)
        metadata = getattr(session, "metadata", {}) if session is not None else {}
        if not isinstance(metadata, dict):
            return ""
        agent_thread_id = str(metadata.get("agent_thread_id") or "").strip()
        if _is_synthetic_chat_assistant_thread_id(agent_thread_id):
            return ""
        return agent_thread_id

    def _clear_stored_chat_assistant_thread_id(self, session_id: str) -> None:
        repository = self._session_repository()
        session = repository.get_session(session_id)
        if session is None:
            return
        metadata = dict(getattr(session, "metadata", {}) or {})
        if "agent_thread_id" not in metadata:
            return
        metadata.pop("agent_thread_id", None)
        repository.upsert_session(session.model_copy(update={"metadata": metadata}))

    def _ensure_chat_assistant_manifest(
        self,
        *,
        session_id: str,
        backend: str,
        user_id: str,
        safe_user_id: str,
        agent_thread_id: str,
        settings: dict[str, Any] | None,
        thread_id: str | None,
        user_message_id: str | None,
    ) -> None:
        repository = self._session_repository()
        existing = repository.get_session(session_id)
        profile = _assistant_capability_profile(settings)
        allowed_tools = _assistant_allowed_tools(profile)
        metadata = dict(getattr(existing, "metadata", {}) or {})
        metadata["capability_profile"] = profile
        metadata["assistant_scope"] = "admin_chat"
        metadata["execution_backend"] = backend
        metadata["assistant_read_only"] = bool((settings or {}).get("read_only", True))
        metadata["assistant_mutation_policy"] = _assistant_mutation_policy(
            profile=profile,
        )
        chat_threads = metadata.get("admin_chat_threads")
        if not isinstance(chat_threads, dict):
            chat_threads = {}
        if thread_id:
            chat_threads[
                _chat_thread_mapping_key(
                    agent_thread_id=agent_thread_id,
                    thread_id=thread_id,
                )
            ] = {
                "thread_id": thread_id,
                "user_id": user_id,
                "user_message_id": user_message_id or "",
            }
        metadata["admin_chat_threads"] = chat_threads
        labels = metadata.get("labels")
        if not isinstance(labels, dict):
            labels = {}
        labels.update({"system": "chat_assistant", "user": safe_user_id})
        metadata["labels"] = labels

        vm_name = _chat_assistant_vm_name(backend=backend, session_id=session_id)
        vm_provider = "libvirt" if backend == "libvirt_vm" else backend
        vm_provider_id = vm_name
        expires_at = datetime.now(UTC) + timedelta(days=30)
        if existing is not None:
            expires_at = max(getattr(existing, "expires_at", expires_at), expires_at)
            manifest = existing.model_copy(
                update={
                    "vm_id": vm_name if backend == "libvirt_vm" else None,
                    "vm_provider": vm_provider,
                    "vm_provider_id": vm_provider_id,
                    "model": str((settings or {}).get("default_model") or "").strip() or None,
                    "allowed_tools": allowed_tools,
                    "metadata": metadata,
                    "expires_at": expires_at,
                }
            )
            repository.upsert_session(manifest)
            return

        manifest = SessionManifest(
            session_id=session_id,
            vm_id=vm_name if backend == "libvirt_vm" else None,
            vm_provider=vm_provider,
            vm_provider_id=vm_provider_id,
            model=str((settings or {}).get("default_model") or "").strip() or None,
            directory_path="root",
            allowed_tools=allowed_tools,
            expires_at=expires_at,
            metadata=metadata,
        )
        repository.upsert_session(manifest)

    def _ensure_chat_assistant_target(
        self,
        *,
        session_id: str,
        backend: str,
        safe_user_id: str,
        settings: dict[str, Any] | None,
    ) -> None:
        if backend == ExecutionBackendKind.LIBVIRT_VM.value:
            self._ensure_libvirt_chat_assistant_target(
                session_id=session_id,
                safe_user_id=safe_user_id,
                settings=settings,
            )
            return
        if backend == ExecutionBackendKind.CONTAINER_WORKER_VM.value:
            self._ensure_container_chat_assistant_target(
                session_id=session_id,
                safe_user_id=safe_user_id,
                settings=settings,
            )

    def _ensure_libvirt_chat_assistant_target(
        self,
        *,
        session_id: str,
        safe_user_id: str,
        settings: dict[str, Any] | None,
    ) -> None:
        vm_name = _chat_assistant_vm_name(
            backend=ExecutionBackendKind.LIBVIRT_VM.value,
            session_id=session_id,
        )
        if vm_helpers.dominfo_field(vm_name, "State") is None:
            result = self.create_vm(
                provider="libvirt",
                vm_name=vm_name,
                session_id=session_id,
                labels={"system": "chat_assistant", "user": safe_user_id},
                model=str((settings or {}).get("default_model") or "").strip() or None,
                vm_user="agent",
                ttl_hours=24 * 30,
                allowed_tools=[
                    str(tool.value)
                    for tool in _assistant_allowed_tools(_assistant_capability_profile(settings))
                ],
                directory_path="root",
                allow_existing_session=True,
            )
            if not result.success and vm_helpers.dominfo_field(vm_name, "State") is None:
                raise RuntimeError(result.message or f"Failed to create assistant VM {vm_name}")
        self.vm_copy_files(
            session_id,
            files=_assistant_home_file_specs(self._deps.project_dir),
            vm_user="agent",
        )
        self._ensure_libvirt_chat_assistant_runtime(
            session_id=session_id,
            vm_name=vm_name,
            settings=settings,
        )

    def _ensure_libvirt_chat_assistant_runtime(
        self,
        *,
        session_id: str,
        vm_name: str,
        settings: dict[str, Any] | None,
    ) -> None:
        try:
            check = self.vm_exec(
                session_id,
                command=[
                    "bash",
                    "-lc",
                    'test -x "$HOME/bin/codex" || command -v codex >/dev/null 2>&1',
                ],
                vm_user="agent",
                timeout_seconds=20.0,
                check=False,
            )
            if int(check.get("exit_code", 1)) == 0:
                return
        except Exception:
            pass

        root_dir = Path(__file__).resolve().parents[4]
        bootstrap_script = root_dir / "scripts" / "bootstrap_codex_vm.sh"
        run_env = os.environ.copy()
        default_model = str((settings or {}).get("default_model") or "").strip()
        if default_model:
            run_env["AGENT_MODEL"] = default_model
        completed = process_helpers.run_subprocess(
            [str(bootstrap_script), vm_name, "agent"],
            capture_output=True,
            text=True,
            env=run_env,
        )
        if completed.returncode != 0:
            message = process_helpers.combined_process_message(completed.stdout, completed.stderr)
            raise RuntimeError(
                message or f"{bootstrap_script.name} exited with code {completed.returncode}"
            )

    def _ensure_container_chat_assistant_target(
        self,
        *,
        session_id: str,
        safe_user_id: str,
        settings: dict[str, Any] | None,
    ) -> None:
        backend = self._container_worker_backend(directory_path="root")
        backend.prepare_session(
            session_id=session_id,
            metadata={
                "execution_backend": ExecutionBackendKind.CONTAINER_WORKER_VM.value,
                "directory_path": "root",
                "target_name": _chat_assistant_vm_name(
                    backend=ExecutionBackendKind.CONTAINER_WORKER_VM.value,
                    session_id=session_id,
                ),
                "labels": {"system": "chat_assistant", "user": safe_user_id},
            },
        )
        from agent_sandbox.effects.providers.agent_session import build_container_session_files

        backend.write_files(
            session_id=session_id,
            files=[
                *build_container_session_files(
                    config=backend.config,
                    session_id=session_id,
                    model=str((settings or {}).get("default_model") or "").strip() or None,
                    dotnet=False,
                    nuget=False,
                    playwright=False,
                ),
                *_assistant_home_file_specs(self._deps.project_dir),
            ],
        )

    def _container_worker_backend(
        self,
        *,
        directory_path: str | None = None,
        worker_url: str | None = None,
    ):
        overridden, backend = self._admin_service_override(
            "_container_worker_backend",
            directory_path=directory_path,
            worker_url=worker_url,
        )
        if overridden:
            return backend

        from agent_sandbox.execution.container_worker_backend import (
            ContainerWorkerVmExecutionBackend,
        )

        resolver = getattr(self._deps.runtime, "provider_config", None)
        resolve_for_directory = getattr(resolver, "resolve_for_directory", None)
        if callable(resolve_for_directory):
            resolved_config = resolve_for_directory(
                self._deps.directory_path.normalize_directory_path(directory_path)
            )
        else:
            resolved_config = self._deps.config
        update: dict[str, Any] = {
            "execution_backend": ExecutionBackendKind.CONTAINER_WORKER_VM.value
        }
        normalized_worker_url = str(worker_url or "").strip()
        if normalized_worker_url:
            update["container_worker_base_url"] = normalized_worker_url
        config = resolved_config.model_copy(update=update)
        return ContainerWorkerVmExecutionBackend(config=config)

    def container_worker_backend(
        self,
        *,
        directory_path: str | None = None,
        worker_url: str | None = None,
    ):
        return self._container_worker_backend(directory_path=directory_path, worker_url=worker_url)

    def container_worker_execution_repository(self):
        from agent_sandbox.execution.container_worker_repository import (
            ContainerWorkerExecutionRepository,
        )

        return ContainerWorkerExecutionRepository(
            self._deps.config.runtime_dir / "container-worker-executions.json"
        )

    def container_execution_records_for_session(self, session_id: str) -> list[Any]:
        repository = self.container_worker_execution_repository()
        list_by_session = getattr(repository, "list_by_session", None)
        if callable(list_by_session):
            records = list_by_session(session_id)
        else:
            records = [
                record
                for record in repository.read_all().values()
                if record.session_id == session_id
            ]
        return sorted(records, key=self.container_execution_sort_key)

    def normalize_agent_record_directory_path(self, directory_path: str | None) -> str:
        try:
            return DirectoryPath(segments=str(directory_path or "root")).as_posix()
        except ValueError:
            return "root"

    @staticmethod
    def is_container_session(session: SessionManifest) -> bool:
        metadata = session.metadata if isinstance(session.metadata, dict) else {}
        return (
            str(session.vm_provider or "").strip() == "container_worker_vm"
            or str(metadata.get("execution_backend") or "").strip() == "container_worker_vm"
        )

    @staticmethod
    def container_worker_url_from_metadata(metadata: Any) -> str | None:
        if not isinstance(metadata, Mapping):
            return None
        for key in ("worker_url", "container_worker_base_url"):
            value = str(metadata.get(key) or "").strip()
            if value:
                return value
        return None

    def container_worker_url_for_target(
        self,
        *,
        session: SessionManifest | None = None,
        record: Any | None = None,
        records: list[Any] | None = None,
    ) -> str | None:
        candidates: list[Any] = []
        if record is not None:
            candidates.append(record)
        if records:
            candidates.extend(
                sorted(
                    records,
                    key=self.container_execution_sort_key,
                    reverse=True,
                )
            )
        for candidate in candidates:
            worker_url = str(getattr(candidate, "worker_url", "") or "").strip()
            if worker_url:
                return worker_url
            metadata_url = self.container_worker_url_from_metadata(
                getattr(candidate, "metadata", None)
            )
            if metadata_url:
                return metadata_url
        if session is not None:
            return self.container_worker_url_from_metadata(session.metadata)
        return None

    @staticmethod
    def container_execution_sort_key(record: Any) -> tuple[datetime, str]:
        timestamp = (
            getattr(record, "started_at", None)
            or getattr(record, "created_at", None)
            or getattr(record, "updated_at", None)
            or datetime.min.replace(tzinfo=UTC)
        )
        return timestamp, str(getattr(record, "execution_id", "") or "")

    def _resolve_session_target(self, target: str):
        repository = self._session_repository()
        try:
            session = repository.resolve_session_target(target)
        except Exception:
            session = None
        if session is not None:
            return str(getattr(session, "session_id", "") or "").strip(), session
        sessions = repository.read_all()
        return session_helpers.resolve_session_target(target, sessions)

    def _persist_session(self, session_id: str, manifest: Any) -> None:
        repository = self._session_repository()
        manifest_session_id = str(getattr(manifest, "session_id", "") or "").strip()
        target_session_id = manifest_session_id or str(session_id or "").strip()
        get_session = getattr(repository, "get_session", None)
        previous_session = (
            get_session(target_session_id) if callable(get_session) and target_session_id else None
        )
        if manifest_session_id:
            repository.upsert_session(manifest)
        else:
            put = getattr(repository, "put", None)
            if callable(put):
                put(session_id, manifest)
            else:
                repository.upsert_session(manifest)
        if isinstance(manifest, SessionManifest):
            session_helpers.sync_added_discord_session_cursors(
                self._deps.runtime,
                previous_session=previous_session,
                updated_session=manifest,
            )

    @staticmethod
    def _control_ssh_users(vm_user: str) -> list[str]:
        configured = (
            os.environ.get("AGENT_SANDBOX_VM_ADMIN_USER", "agent-admin").strip() or "agent-admin"
        )
        users = [configured]
        if vm_user and vm_user not in users:
            users.append(vm_user)
        return users

    def _resolve_control_ssh_access(self, vm_name: str, *, vm_user: str) -> tuple[str, str, Path]:
        errors: list[str] = []
        for ssh_user in self._control_ssh_users(vm_user):
            try:
                vm_ip, ssh_private_key_path = vm_helpers.resolve_vm_ssh_access(vm_name, ssh_user)
                return ssh_user, vm_ip, ssh_private_key_path
            except RuntimeError as exc:
                errors.append(str(exc))
        detail = errors[-1] if errors else f"Unable to resolve SSH access for {vm_name}"
        raise RuntimeError(detail)

    def _provider_registry(self) -> VmProviderRegistry:
        worker_deps = getattr(self._deps.worker_admin, "_deps", SimpleNamespace())
        agent_control_store = getattr(worker_deps, "agent_control_store", None)
        if agent_control_store is None:
            agent_control_store = _EnabledAgentControlStore()
        dependency_view = _VmProviderRegistryDependencies(
            runtime=self._deps.runtime,
            directory_path=self._deps.directory_path,
            resolve_vm_defaults=self._deps.resolve_vm_defaults,
            resolve_agent_model_for_session=self._deps.resolve_agent_model_for_session,
            privileged_helper=self._deps.privileged_helper,
            directory_ownership=getattr(
                worker_deps, "directory_ownership", _EmptyDirectoryOwnership()
            ),
            agent_control_store=agent_control_store,
        )
        return VmProviderRegistry(dependency_view)

    def _find_session_for_vm(self, vm_name: str) -> tuple[str | None, Any | None]:
        matches: list[tuple[str, Any]] = []
        for session in self._session_repository().list_sessions_for_vm(vm_name):
            session_id = str(getattr(session, "session_id", "") or "").strip()
            if str(getattr(session, "vm_id", "") or "").strip() == vm_name:
                matches.append((session_id, session))
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            session_ids = ", ".join(sorted(session_id for session_id, _ in matches))
            raise ValueError(f"VM {vm_name!r} is associated with multiple sessions: {session_ids}")
        return None, None

    def _resolve_provider_operation_target(self, target: str) -> tuple[Any, str, Any | None, str]:
        repository = self._session_repository()
        direct_session = repository.get_session(target)
        provider, vm_name, session = self._provider_registry().resolve_target(
            target=target,
            session_repository=repository,
            directory_path=str(getattr(direct_session, "directory_path", "") or "root"),
        )
        directory_path = str(getattr(session, "directory_path", "") or "root")
        return provider, vm_name, session, directory_path

    def list_vms(
        self,
        *,
        include_inactive: bool = True,
        omit_state: bool = False,
        vm_user: str = "agent",
        directory_path: str | None = None,
        direct_only: bool = False,
    ) -> list[VmSummary]:
        try:
            summaries = self._provider_registry().list_vms(
                include_inactive=include_inactive,
                omit_state=omit_state,
                vm_user=vm_user,
                directory_path=directory_path,
                direct_only=direct_only,
            )
            if summaries:
                return summaries
        except RuntimeError:
            pass
        return self._deps.worker_admin.list_vms(
            include_inactive=include_inactive,
            omit_state=omit_state,
            vm_user=vm_user,
            directory_path=directory_path,
            direct_only=direct_only,
        )

    def get_agent_log_snapshot(
        self,
        target: str,
        *,
        lines: int = 50,
        repo_name: str | None = None,
        vm_user: str = "agent",
    ) -> str:
        vm_name = self._resolve_vm_name_from_target(target)
        if not vm_name:
            return "Agent log unavailable: no VM target selected."
        vm_state = vm_helpers.dominfo_field(vm_name, "State")
        if vm_state != "running":
            return f"Agent log unavailable: {vm_name} is not running."
        vm_ip = vm_helpers.resolve_vm_ip(vm_name)
        if not vm_ip:
            return f"Agent log unavailable: could not determine IP for {vm_name}."
        ssh_private_key_path = vm_helpers.resolve_private_key_path()
        if ssh_private_key_path is None:
            return "Agent log unavailable: missing SSH private key for guest access."

        remote_script = r"""
set -euo pipefail

AGENT_USER="${AGENT_USER:-agent}"
AGENT_HOME="$(getent passwd "${AGENT_USER}" | cut -d: -f6)"
if [[ -z "${AGENT_HOME}" ]]; then
  echo "Unknown agent user: ${AGENT_USER}" >&2
  exit 1
fi
LOG_DIR="${AGENT_HOME}/.agent-sandbox"
RUN_LOG_DIR="${AGENT_RUN_LOG_DIR:-/var/log/agent-sandbox/agent-runs}"
RESOLVED_REPO_NAME="${REPO_NAME:-}"
TAIL_LINES="${TAIL_LINES:-50}"

if [[ -z "${RESOLVED_REPO_NAME}" ]]; then
  shopt -s nullglob
  candidates=()
  for path in "${LOG_DIR}"/agent-*.pid "${LOG_DIR}"/agent-*.state; do
    name="$(basename "${path}")"
    name="${name#agent-}"
    name="${name%.pid}"
    name="${name%.state}"
    if [[ -n "${name}" ]]; then
      candidates+=("${name}")
    fi
  done
  if [[ -f "${LOG_DIR}/codex-resume-last.state" || -f "${LOG_DIR}/codex-heartbeat.pid" ]]; then
    candidates+=("codex-heartbeat")
  fi
  shopt -u nullglob

  if [[ ${#candidates[@]} -eq 0 ]]; then
    echo "No guest agent state or pid files found under ${LOG_DIR}"
    exit 0
  fi

  mapfile -t unique_candidates < <(printf '%s\n' "${candidates[@]}" | sort -u)
  if [[ ${#unique_candidates[@]} -ne 1 ]]; then
    echo "Multiple guest agent logs found under ${LOG_DIR}; pass an explicit repo name."
    printf 'Candidates:\n'
    printf '  %s\n' "${unique_candidates[@]}"
    exit 0
  fi

  RESOLVED_REPO_NAME="${unique_candidates[0]}"
fi

if [[ "${RESOLVED_REPO_NAME}" == "codex-heartbeat" ]]; then
  LOG_PATH="${RUN_LOG_DIR}/codex-heartbeat-run.log"
  LEGACY_LOG_PATH="${LOG_DIR}/codex-heartbeat-run.log"
else
  LOG_PATH="${RUN_LOG_DIR}/agent-${RESOLVED_REPO_NAME}.log"
  LEGACY_LOG_PATH="${LOG_DIR}/agent-${RESOLVED_REPO_NAME}.log"
fi
if [[ ! -f "${LOG_PATH}" ]]; then
  if [[ -f "${LEGACY_LOG_PATH}" ]]; then
    LOG_PATH="${LEGACY_LOG_PATH}"
  else
    echo "No guest agent log found at ${LOG_PATH} or ${LEGACY_LOG_PATH}"
    exit 0
  fi
fi

echo "Repo: ${RESOLVED_REPO_NAME}"
echo "Log: ${LOG_PATH}"
echo
TAIL_PATH="$(mktemp)"
trap 'rm -f "${TAIL_PATH}"' EXIT
tail -n "${TAIL_LINES}" "${LOG_PATH}" > "${TAIL_PATH}"
cat "${TAIL_PATH}"
python3 - "${TAIL_PATH}" "${AGENT_HOME}" <<'PY' || true
import json
import re
import sys
from pathlib import Path

tail_path = Path(sys.argv[1])
agent_home = Path(sys.argv[2])
try:
    tail_text = tail_path.read_text(encoding="utf-8", errors="replace")
except OSError:
    raise SystemExit(0)

matches = re.findall(r"(?im)^session id:\s*(\S+)\s*$", tail_text)
if not matches:
    raise SystemExit(0)
thread_id = matches[-1].strip()
if not thread_id:
    raise SystemExit(0)


def first_text(*values):
    for value in values:
        if isinstance(value, str) and value:
            return value
    return ""


def content_text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for part in value:
            if isinstance(part, str):
                if part:
                    parts.append(part)
                continue
            if not isinstance(part, dict):
                continue
            part_type = str(part.get("type") or "")
            if part_type and part_type not in {"text", "output_text", "assistant_message"}:
                continue
            text = first_text(part.get("text"), part.get("content"), part.get("message"))
            if text:
                parts.append(text)
        return "\n".join(parts)
    if isinstance(value, dict):
        return first_text(value.get("text"), value.get("content"), value.get("message"))
    return ""


def messages_from_event(event):
    if not isinstance(event, dict):
        return []
    event_type = str(event.get("type") or "")
    payload = event.get("payload")
    if event_type == "event_msg" and isinstance(payload, dict):
        payload_type = str(payload.get("type") or "")
        if payload_type == "agent_message":
            return [first_text(payload.get("message"))]
        if payload_type == "task_complete":
            return [first_text(payload.get("last_agent_message"))]
        return []
    if event_type == "response_item" and isinstance(payload, dict):
        if str(payload.get("type") or "") != "message":
            return []
        if str(payload.get("role") or "") != "assistant":
            return []
        return [
            first_text(
                payload.get("text"),
                payload.get("message"),
                content_text(payload.get("content")),
                content_text(payload.get("formatted")),
            )
        ]
    return []


def is_artifact(message):
    normalized = " ".join(message.strip().split()).casefold()
    return normalized in {"no response requested", "no response requested."}


roots = [
    agent_home / ".config" / "agent-sandbox" / "codex" / "sessions",
    agent_home / ".codex" / "sessions",
]
candidates = []
for root in roots:
    if not root.exists():
        continue
    try:
        candidates.extend(root.rglob(f"*{thread_id}*.jsonl"))
    except OSError:
        continue
if not candidates:
    raise SystemExit(0)

try:
    session_path = max(candidates, key=lambda path: path.stat().st_mtime)
except OSError:
    raise SystemExit(0)

messages = []
try:
    with session_path.open("r", encoding="utf-8", errors="replace") as handle:
        for raw_line in handle:
            try:
                event = json.loads(raw_line)
            except ValueError:
                continue
            messages.extend(messages_from_event(event))
except OSError:
    raise SystemExit(0)

seen = set()
for message in messages:
    if not message or is_artifact(message):
        continue
    if message in seen or message in tail_text:
        continue
    seen.add(message)
    print(message, flush=True)
PY
"""
        result: subprocess.CompletedProcess[str] | None = None
        attempted_errors: list[str] = []
        for ssh_user in self._control_ssh_users(vm_user):
            try:
                result = process_helpers.run_subprocess(
                    [
                        "ssh",
                        *vm_helpers.ssh_options(vm_name, vm_ip, ssh_private_key_path),
                        f"{ssh_user}@{vm_ip}",
                        "sudo",
                        "-n",
                        "env",
                        f"AGENT_USER={vm_user}",
                        "AGENT_RUN_LOG_DIR=/var/log/agent-sandbox/agent-runs",
                        f"REPO_NAME={repo_name or ''}",
                        f"TAIL_LINES={lines}",
                        "bash",
                        "-s",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=20.0,
                    input=remote_script,
                )
            except subprocess.TimeoutExpired as exc:
                attempted_errors.append(
                    f"{ssh_user}@{vm_ip}: log read timed out after {exc.timeout:g} seconds"
                )
                result = None
                continue
            if result.returncode == 0:
                break
            attempted_errors.append((result.stderr or result.stdout or "").strip())
        if result is None:
            detail = next((candidate for candidate in reversed(attempted_errors) if candidate), "")
            return f"Agent log unavailable: {detail or 'no SSH users were configured.'}"
        output = (result.stdout or "").strip()
        error = (result.stderr or "").strip()
        if result.returncode != 0:
            detail = error or next(
                (candidate for candidate in reversed(attempted_errors) if candidate), ""
            )
            return f"Agent log unavailable: {detail or f'ssh exited with code {result.returncode}'}"
        return output or error or "Agent log is empty."

    def start_vm(self, target: str) -> VmActionResult:
        provider, vm_name, session, directory_path = self._resolve_provider_operation_target(target)
        return provider.start_vm(
            target=target, vm_name=vm_name, session=session, directory_path=directory_path
        )

    def shutdown_vm(self, target: str, *, timeout_seconds: int = 60) -> VmActionResult:
        provider, vm_name, session, directory_path = self._resolve_provider_operation_target(target)
        return provider.shutdown_vm(
            target=target,
            vm_name=vm_name,
            session=session,
            directory_path=directory_path,
            timeout_seconds=timeout_seconds,
        )

    def _run_script(
        self, script_name: str, args: list[str], *, env: dict[str, str] | None = None
    ) -> VmActionResult:
        script_path = Path(__file__).resolve().parents[4] / "scripts" / script_name
        completed = process_helpers.run_subprocess(
            [str(script_path), *args], capture_output=True, text=True, env=env
        )
        vm_name = ""
        if "--vm-name" in args:
            try:
                vm_name = args[args.index("--vm-name") + 1]
            except IndexError:
                vm_name = ""
        elif len(args) > 1:
            issue_target = args[1]
            issue_number = session_helpers.extract_issue_number(issue_target) or issue_target
            vm_name = session_helpers.resolve_vm_name(issue_number) if issue_number else ""
        message = (completed.stdout or completed.stderr or "").strip()
        return VmActionResult(
            success=completed.returncode == 0,
            vm_name=vm_name,
            action=script_name.removesuffix(".sh"),
            message=message or f"{script_name} exited with code {completed.returncode}",
        )

    def _run_privileged_host_script(
        self,
        script_path: Path,
        args: list[str],
        *,
        env: dict[str, str] | None = None,
        allowed_env_keys: tuple[str, ...] = (),
    ) -> tuple[subprocess.CompletedProcess[str], str]:
        run_env = env or os.environ.copy()
        base_cmd = [str(script_path), *args]
        completed = process_helpers.run_subprocess(
            process_helpers.sudo_env_command(base_cmd, run_env, allowed_env_keys),
            capture_output=True,
            text=True,
            env=run_env,
        )
        message = process_helpers.combined_process_message(completed.stdout, completed.stderr)
        if completed.returncode != 0 and process_helpers.can_prompt_for_privileges():
            completed = process_helpers.run_subprocess(
                process_helpers.pkexec_env_command(base_cmd, run_env, allowed_env_keys),
                capture_output=True,
                text=True,
                env=run_env,
            )
            message = process_helpers.combined_process_message(completed.stdout, completed.stderr)
        return completed, message

    def enable_agent(self, target: str, *, vm_user: str = "agent") -> VmActionResult:
        vm_name = self._resolve_vm_name_from_target(target)
        return self._run_script(
            "set_issue_agent_state.sh",
            [
                "--state",
                "enable",
                "--vm-name",
                vm_name,
                "--vm-user",
                vm_user,
                "--host-state-path",
                str(self._deps.config.host_agent_control_state_path),
            ],
        )

    def disable_agent(self, target: str, *, vm_user: str = "agent") -> VmActionResult:
        vm_name = self._resolve_vm_name_from_target(target)
        return self._run_script(
            "set_issue_agent_state.sh",
            [
                "--state",
                "disable",
                "--vm-name",
                vm_name,
                "--vm-user",
                vm_user,
                "--host-state-path",
                str(self._deps.config.host_agent_control_state_path),
            ],
        )

    def kill_agent(self, target: str, pid: int, *, vm_user: str = "agent") -> VmActionResult:
        vm_name = self._resolve_vm_name_from_target(target)
        if pid <= 0:
            return VmActionResult(
                success=False,
                vm_name=vm_name,
                action="kill_agent",
                message=f"Invalid agent pid: {pid}",
            )
        current_state = vm_helpers.dominfo_field(vm_name, "State")
        if current_state is None:
            return VmActionResult(
                success=False,
                vm_name=vm_name,
                action="kill_agent",
                message=f"VM not found: {vm_name}",
            )
        if current_state != "running":
            return VmActionResult(
                success=False,
                vm_name=vm_name,
                action="kill_agent",
                message=f"VM is not running: {vm_name}",
            )
        try:
            ssh_user, vm_ip, ssh_private_key_path = self._resolve_control_ssh_access(
                vm_name, vm_user=vm_user
            )
        except RuntimeError as exc:
            return VmActionResult(
                success=False, vm_name=vm_name, action="kill_agent", message=str(exc)
            )

        remote_script = r"""
set -euo pipefail

TARGET_PID="${TARGET_PID:-}"
AGENT_USER="${AGENT_USER:-agent}"
AGENT_HOME="$(getent passwd "${AGENT_USER}" | cut -d: -f6)"
if [[ -z "${AGENT_HOME}" ]]; then
  echo "Unknown agent user: ${AGENT_USER}" >&2
  exit 1
fi
LOG_DIR="${AGENT_HOME}/.agent-sandbox"

is_valid_agent_pid() {
  local pid="$1"
  local cmdline_path="/proc/${pid}/cmdline"
  local cmdline=""

  [[ -n "${pid}" && "${pid}" =~ ^[0-9]+$ ]] || return 1
  kill -0 "${pid}" >/dev/null 2>&1 || return 1
  [[ -r "${cmdline_path}" ]] || return 1
  cmdline="$(tr '\000' ' ' < "${cmdline_path}" 2>/dev/null || true)"
  [[ -n "${cmdline}" ]] || return 1
  [[ "${cmdline}" == *"agent_runner_exec"* || "${cmdline}" == *"agent_runner_resume_last"* ]]
}

read_state_value() {
  local state_path="$1"
  local key="$2"
  [[ -f "${state_path}" ]] || return 1
  awk -F= -v target_key="${key}" '$1 == target_key {print substr($0, index($0, "=") + 1); exit}' "${state_path}" 2>/dev/null
}

write_idle_state() {
  local state_path="$1"
  local repo_name="$2"
  local dispatch_id="$3"
  local now tmp_path

  now="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  tmp_path="${state_path}.tmp"
  {
    printf "status=idle\n"
    printf "phase=terminated_by_admin\n"
    printf "repo=%s\n" "${repo_name}"
    printf "pid=%s\n" "${TARGET_PID}"
    printf "updated_at=%s\n" "${now}"
    if [[ -n "${dispatch_id}" ]]; then
      printf "dispatch_id=%s\n" "${dispatch_id}"
    fi
    printf "exit_code=143\n"
  } > "${tmp_path}"
  mv "${tmp_path}" "${state_path}"
  chown "${AGENT_USER}:${AGENT_USER}" "${state_path}"
  chmod 0600 "${state_path}"
}

remove_matching_pid_file() {
  local pid_file="$1"
  local file_pid=""
  [[ -f "${pid_file}" ]] || return 0
  file_pid="$(cat "${pid_file}" 2>/dev/null || true)"
  if [[ "${file_pid}" == "${TARGET_PID}" ]]; then
    rm -f "${pid_file}"
  fi
}

if ! is_valid_agent_pid "${TARGET_PID}"; then
  echo "Guest agent pid is not active or not managed by agent-sandbox: ${TARGET_PID}" >&2
  exit 1
fi

matched_states=()
shopt -s nullglob
for state_path in "${LOG_DIR}"/agent-*.state "${LOG_DIR}/codex-resume-last.state"; do
  state_pid="$(read_state_value "${state_path}" "pid" || true)"
  if [[ "${state_pid}" == "${TARGET_PID}" ]]; then
    matched_states+=("${state_path}")
  fi
done

kill "${TARGET_PID}" >/dev/null 2>&1 || true
sleep 1
if kill -0 "${TARGET_PID}" >/dev/null 2>&1; then
  kill -9 "${TARGET_PID}" >/dev/null 2>&1 || true
fi

for state_path in "${matched_states[@]}"; do
  repo_name="$(read_state_value "${state_path}" "repo" || true)"
  dispatch_id="$(read_state_value "${state_path}" "dispatch_id" || true)"
  write_idle_state "${state_path}" "${repo_name}" "${dispatch_id}"
done

for pid_file in "${LOG_DIR}"/agent-*.pid "${LOG_DIR}/codex-heartbeat.pid"; do
  remove_matching_pid_file "${pid_file}"
done
shopt -u nullglob

printf 'terminated pid=%s state_files=%s\n' "${TARGET_PID}" "${#matched_states[@]}"
"""
        completed = process_helpers.run_subprocess(
            [
                "ssh",
                *vm_helpers.ssh_options(vm_name, vm_ip, ssh_private_key_path),
                f"{ssh_user}@{vm_ip}",
                "sudo",
                "-n",
                "env",
                f"AGENT_USER={vm_user}",
                f"TARGET_PID={pid}",
                "bash",
                "-s",
            ],
            capture_output=True,
            text=True,
            timeout=20.0,
            input=remote_script,
        )
        message = (completed.stdout or completed.stderr or "").strip()
        return VmActionResult(
            success=completed.returncode == 0,
            vm_name=vm_name,
            action="kill_agent",
            message=message or f"kill_agent exited with code {completed.returncode}",
        )

    def resume_agent(
        self,
        target: str,
        prompt: str,
        *,
        model: str | None = None,
        vm_user: str = "agent",
        dispatch_id: str | None = None,
        resume_thread_id: str | None = None,
        resume_mode: str = "new_context",
        persist_thread_id: bool = True,
        mcp_gateway_url: str | None = None,
    ) -> VmActionResult:
        container_session = None
        if getattr(self.runtime, "session_repository", None) is not None:
            try:
                container_session, _container_record = self._resolve_container_agent_session(
                    target,
                    require_session=False,
                )
            except ValueError as exc:
                if str(exc).startswith("Ambiguous container worker session target"):
                    raise
                container_session = None
        if container_session is not None and self._is_container_session(container_session):
            response = self._prompt_admin_agent(
                container_session.session_id,
                backend="container_worker_vm",
                payload={
                    "prompt": prompt,
                    "model": model,
                    "vm_user": vm_user,
                    "dispatch_id": dispatch_id,
                    "resume_thread_id": resume_thread_id,
                    "resume_mode": resume_mode,
                },
            )
            return VmActionResult(
                success=bool(response.get("success", True)),
                vm_name=str(
                    response.get("target_name")
                    or self._container_session_target_name(container_session)
                ),
                action=str(response.get("action") or "resume_issue_agent"),
                message=str(response.get("message") or ""),
                provider="container_worker_vm",
                thread_id=str(response.get("thread_id") or "") or None,
            )

        vm_name = self._resolve_vm_name_from_target(target)
        session_id: str | None = None
        directory_path: str | None = None
        manifest: Any | None = None
        effective_resume_thread_id: str | None = None
        web_search_mode = "disabled"
        agent_provider: str | None = None
        try:
            session_id, manifest = self._resolve_session_target(target)
            ensure_session_not_expired(manifest)
            resolved_model = str(model or "").strip() or self._deps.resolve_agent_model_for_session(
                manifest
            )
            directory_path = manifest.directory_path
            web_search_mode = (
                "live" if bool(getattr(manifest, "web_search_enabled", False)) else "disabled"
            )
            effective_resume_thread_id = str(resume_thread_id or "").strip() or None
            if _is_synthetic_chat_assistant_thread_id(effective_resume_thread_id):
                effective_resume_thread_id = None
            agent_provider = _agent_provider_from_session(manifest)
        except ValueError as exc:
            return VmActionResult(
                success=False,
                vm_name=vm_name,
                action="resume_issue_agent",
                message=str(exc),
            )
        script_path = Path(__file__).resolve().parents[4] / "scripts" / "resume_issue_agent.sh"
        normalized_resume_mode = str(resume_mode).strip() or "new_context"
        if normalized_resume_mode not in {"same_context", "new_context"}:
            return VmActionResult(
                success=False,
                vm_name=vm_name,
                action="resume_issue_agent",
                message=f"Unsupported resume mode: {resume_mode}",
            )
        args = [
            str(script_path),
            "--prompt",
            prompt,
            "--vm-name",
            vm_name,
            "--vm-user",
            vm_user,
            "--host-control-state-path",
            str(self._deps.config.host_agent_control_state_path),
            *(["--dispatch-id", dispatch_id] if dispatch_id else []),
            *(
                ["--resume-thread-id", effective_resume_thread_id]
                if effective_resume_thread_id
                else ["--resume-mode", normalized_resume_mode]
            ),
            *(["--mcp-gateway-url", mcp_gateway_url] if mcp_gateway_url else []),
        ]
        try:
            resume_timeout_seconds = max(
                1.0,
                float(os.environ.get("AGENT_SANDBOX_ADMIN_RESUME_TIMEOUT_SECONDS", "120") or "120"),
            )
        except ValueError:
            resume_timeout_seconds = 120.0
        resume_env = os.environ | {
            "AGENT_MODEL": resolved_model,
            "AGENT_WEB_SEARCH_MODE": web_search_mode,
        }
        if agent_provider:
            resume_env["AGENT_PROVIDER"] = agent_provider
        try:
            completed = process_helpers.run_subprocess(
                args,
                capture_output=True,
                text=True,
                env=resume_env,
                timeout=resume_timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return VmActionResult(
                success=False,
                vm_name=vm_name,
                action="resume_issue_agent",
                message=f"resume_issue_agent.sh timed out after {resume_timeout_seconds:g}s",
            )
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        thread_id: str | None = None
        message_lines: list[str] = []
        for line in stdout.splitlines():
            if line.startswith("thread_id="):
                candidate = line.partition("=")[2].strip()
                if candidate:
                    thread_id = candidate
                continue
            message_lines.append(line)
        message = (
            "\n".join(line for line in message_lines if line.strip()).strip() or stderr.strip()
        )
        resolved_thread_id = thread_id or effective_resume_thread_id
        if (
            completed.returncode == 0
            and persist_thread_id
            and session_id
            and manifest is not None
            and resolved_thread_id
        ):
            updated_metadata = dict(manifest.metadata)
            updated_metadata["agent_thread_id"] = resolved_thread_id
            self._persist_session(
                session_id, manifest.model_copy(update={"metadata": updated_metadata})
            )
        if completed.returncode == 0 and dispatch_id:
            self._spawn_agent_completion_watcher(
                vm_name=vm_name,
                dispatch_id=dispatch_id,
                vm_user=vm_user,
                session_id=session_id,
                directory_path=directory_path,
            )
        return VmActionResult(
            success=completed.returncode == 0,
            vm_name=vm_name,
            action="resume_issue_agent",
            message=message or f"resume_issue_agent.sh exited with code {completed.returncode}",
            thread_id=resolved_thread_id,
        )

    def _spawn_agent_completion_watcher(
        self,
        *,
        vm_name: str,
        dispatch_id: str,
        vm_user: str,
        session_id: str | None,
        directory_path: str | None,
    ) -> None:
        watcher_cmd = [
            sys.executable,
            "-m",
            "agent_sandbox.daemons.agent_completion_watcher",
            "--runtime-dir",
            str(self._deps.config.runtime_dir),
            "--vm-name",
            vm_name,
            "--dispatch-id",
            dispatch_id,
            "--vm-user",
            vm_user,
            "--poll-interval-seconds",
            "1.0",
            "--timeout-seconds",
            "172800",
        ]
        trigger_worker_config = self._deps.workflow_config.read_worker_global_config(
            "trigger-orchestrator"
        ).config
        workflow_runtime_dir = process_helpers.resolve_path_setting(
            base_dir=self._deps.project_dir,
            value=trigger_worker_config.get("runtime_dir"),
            fallback=self._deps.config.runtime_dir,
        )
        event_queue_store_path = process_helpers.resolve_path_setting(
            base_dir=self._deps.project_dir,
            value=trigger_worker_config.get("event_queue_store_path"),
            fallback=workflow_runtime_dir / "trigger-orchestrator-events.json",
        )
        workflow_event_journal = process_helpers.resolve_path_setting(
            base_dir=self._deps.project_dir,
            value=trigger_worker_config.get("workflow_event_journal_path"),
            fallback=workflow_event_journal_path(workflow_runtime_dir),
        )
        effect_lock_path = process_helpers.resolve_path_setting(
            base_dir=self._deps.project_dir,
            value=trigger_worker_config.get("effect_lock_path"),
            fallback=workflow_runtime_dir / "effect-orchestrator.lock",
        )
        orchestration_backend = (
            str(trigger_worker_config.get("orchestration_store_backend") or "sqlite")
            .strip()
            .lower()
        )
        if orchestration_backend not in {"sqlite", "json"}:
            orchestration_backend = "sqlite"
        orchestration_store_path = process_helpers.resolve_path_setting(
            base_dir=self._deps.project_dir,
            value=trigger_worker_config.get("orchestration_store_path"),
            fallback=workflow_runtime_dir / "orchestration.db",
        )
        orchestration_retention_days = trigger_worker_config.get("orchestration_retention_days")
        watcher_cmd.extend(["--event-queue-store-path", str(event_queue_store_path)])
        watcher_cmd.extend(["--workflow-event-journal-path", str(workflow_event_journal)])
        watcher_cmd.extend(["--effect-lock-path", str(effect_lock_path)])
        watcher_cmd.extend(["--orchestration-store-backend", orchestration_backend])
        watcher_cmd.extend(["--orchestration-store-path", str(orchestration_store_path)])
        if orchestration_retention_days is not None:
            watcher_cmd.extend(
                ["--orchestration-retention-days", str(orchestration_retention_days)]
            )
        if session_id:
            watcher_cmd.extend(["--session-id", session_id])
        if directory_path:
            watcher_cmd.extend(["--directory-path", directory_path])
        subprocess.Popen(
            watcher_cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            cwd=str(self._deps.project_dir),
            env=os.environ.copy(),
            text=True,
        )

    def vm_exec(
        self,
        target: str,
        *,
        command: str | list[str],
        vm_user: str = "agent",
        working_directory: str | None = None,
        environment: dict[str, str] | None = None,
        stdin: str | None = None,
        timeout_seconds: float | None = None,
        check: bool = True,
    ) -> dict[str, Any]:
        provider, vm_name, session, directory_path = self._resolve_provider_operation_target(target)
        return provider.vm_exec(
            target=target,
            vm_name=vm_name,
            session=session,
            vm_user=vm_user,
            command=command,
            working_directory=working_directory,
            environment=environment,
            stdin=stdin,
            timeout_seconds=timeout_seconds,
            check=check,
            directory_path=directory_path,
        )

    def vm_copy_files(
        self,
        target: str,
        *,
        files: list[dict[str, Any]],
        vm_user: str = "agent",
    ) -> dict[str, Any]:
        provider, vm_name, session, directory_path = self._resolve_provider_operation_target(target)
        return provider.vm_copy_files(
            target=target,
            vm_name=vm_name,
            session=session,
            files=files,
            vm_user=vm_user,
            directory_path=directory_path,
        )

    def vm_delete_files(
        self,
        target: str,
        *,
        paths: list[str],
        vm_user: str = "agent",
        recursive: bool = False,
        missing_ok: bool = True,
    ) -> dict[str, Any]:
        provider, vm_name, session, directory_path = self._resolve_provider_operation_target(target)
        return provider.vm_delete_files(
            target=target,
            vm_name=vm_name,
            session=session,
            paths=paths,
            vm_user=vm_user,
            recursive=recursive,
            missing_ok=missing_ok,
            directory_path=directory_path,
        )

    def create_issue_vm(
        self,
        *,
        issue: str,
        session_id: str | None = None,
        model: str | None = None,
        model_provider_mode: str | None = None,
        vm_user: str = "agent",
        ttl_hours: int | None = None,
        allowed_contact_ids: list[str] | None = None,
        discord_channels: list[str] | None = None,
        email_mailboxes: list[str] | None = None,
        email_folders: list[str] | None = None,
        dotnet: bool = False,
        nuget: str | None = None,
        mssql: bool = False,
        mssql_init_root: str | None = None,
        playwright: bool = False,
        codex_skills: list[str] | None = None,
        directory_path: str = "root",
    ) -> VmActionResult:
        issue_input = issue
        issue_number = session_helpers.resolve_issue_number(issue_input)
        local_issue: Any | None = None
        candidate_issue_path = Path(issue_input)
        if candidate_issue_path.is_file():
            local_issue = self._deps.resolve_local_issue_record(issue_input)
            issue_number = local_issue.issue_number
            issue_input = local_issue.yaml_path
        else:
            try:
                local_issue = self._deps.resolve_local_issue_record(issue_input)
                issue_number = local_issue.issue_number
                issue_input = local_issue.yaml_path
            except ValueError as exc:
                if not str(exc).startswith("Unknown local issue identifier: "):
                    return VmActionResult(
                        success=False,
                        vm_name=f"vm-{issue_number}",
                        action="create_issue_vm",
                        message=str(exc),
                    )
        resolved_session_id = session_id or session_helpers.default_issue_session_id(issue_number)
        resolved_vm_name = f"vm-{issue_number}"
        normalized_directory_path = self._deps.directory_path.normalize_directory_path(
            local_issue.directory_path
            if local_issue is not None and directory_path == "root"
            else directory_path
        )
        policy = self._deps.runtime.policy_config.resolve_for_directory(normalized_directory_path)
        session_repository = self._session_repository()
        if session_repository.get_session(resolved_session_id) is not None:
            return VmActionResult(
                success=False,
                vm_name=resolved_vm_name,
                action="create_issue_vm",
                message=f"Session already exists: {resolved_session_id}",
            )
        if mssql and not os.environ.get("MSSQL_SA_PASSWORD", "").strip():
            return VmActionResult(
                success=False,
                vm_name=resolved_vm_name,
                action="create_issue_vm",
                message="MSSQL_SA_PASSWORD must be set when --mssql is used.",
            )
        resolved_allowed_contact_ids = (
            session_helpers.validate_allowed_contact_ids_for_runtime_directory(
                self._deps.runtime,
                session_id=resolved_session_id,
                directory_path=normalized_directory_path,
                allowed_contact_ids=allowed_contact_ids,
            )
        )

        manifest = session_helpers.build_issue_session_manifest(
            policy=policy,
            session_id=resolved_session_id,
            vm_id=resolved_vm_name,
            issue_number=issue_number,
            directory_path=normalized_directory_path,
            ttl_hours=ttl_hours,
            allowed_contact_ids=resolved_allowed_contact_ids,
            discord_channels=discord_channels or [],
            email_mailboxes=email_mailboxes or [],
            email_folders=email_folders or [],
        )
        resolved_model_provider_mode = normalize_model_provider_mode_override(model_provider_mode)
        if resolved_model_provider_mode is not None:
            manifest.metadata = dict(manifest.metadata)
            manifest.metadata[MODEL_PROVIDER_MODE_METADATA_KEY] = resolved_model_provider_mode
        normalized_model = (model or "").strip() or None
        if normalized_model is not None:
            manifest.model = normalized_model
        else:
            try:
                manifest.model = self._deps.resolve_agent_model_for_session(manifest)
            except ValueError:
                manifest.model = None
        self._persist_session(resolved_session_id, manifest)

        def _issue_vm_failure(message: str) -> VmActionResult:
            try:
                preserved_vm = vm_helpers.dominfo_field(resolved_vm_name, "State") is not None
            except RuntimeError:
                preserved_vm = False
            if not preserved_vm:
                session_repository.delete_session(resolved_session_id)
            detail = message
            if preserved_vm:
                detail = (
                    f"{detail}\n"
                    f"VM {resolved_vm_name} and session {resolved_session_id} were preserved for inspection."
                )
            return VmActionResult(
                success=False,
                vm_name=resolved_vm_name,
                action="create_issue_vm",
                message=detail,
            )

        try:
            root_dir = Path(__file__).resolve().parents[4]
            setup_script = root_dir / "scripts" / "setup_vm.sh"
            vm_defaults = self._deps.resolve_vm_defaults(normalized_directory_path)
            issue_setup = self._deps.resolve_issue_setup(
                normalized_directory_path, root_dir=root_dir
            )
            run_env = os.environ.copy()
            run_env.update(vm_defaults.guest_environment())
            if manifest.model:
                run_env["AGENT_MODEL"] = manifest.model
            normalized_codex_skills = [
                item.strip() for item in (codex_skills or []) if item and item.strip()
            ]
            managed_skills_root = _managed_codex_skills_root(
                self._deps.config.runtime_dir,
                normalized_codex_skills,
            )
            if managed_skills_root is not None:
                run_env["HOST_REPO_CODEX_SKILLS_DIR"] = str(managed_skills_root)
            run_env.update(
                {
                    "AGENT_SANDBOX_GIT_ROOT": issue_setup.git_root,
                    "AGENT_SANDBOX_GIT_HOST": issue_setup.git_host,
                    "AGENT_SANDBOX_GIT_USER": issue_setup.git_user,
                    "AGENT_SANDBOX_GIT_SSH_ALIAS": issue_setup.git_ssh_alias,
                    "ENABLE_GUEST_HEARTBEAT": "1" if issue_setup.enable_guest_heartbeat else "0",
                    "GUEST_HEARTBEAT_PROMPT": issue_setup.guest_heartbeat_prompt,
                    "AGENT_SANDBOX_AZURE_DEVOPS_PR_PAT_ENV": issue_setup.issue_setup_azure_devops_pr_pat_env,
                }
            )
            if issue_setup.post_exec_verification_prompt is not None:
                run_env["AGENT_POST_EXEC_VERIFICATION_PROMPT"] = (
                    issue_setup.post_exec_verification_prompt
                )

            helper = (
                self._deps.privileged_helper.get_privileged_helper()
                if self._deps.privileged_helper.get_use_privileged_helper()
                else None
            )
            if helper is not None and not helper.is_running():
                helper.start()

            setup_args: list[str] = []
            if dotnet:
                setup_args.append("--dotnet")
            if nuget:
                setup_args.append("--nuget")
            if mssql:
                setup_args.append("--mssql")
                if mssql_init_root is not None:
                    setup_args.extend(["--mssql-init-root", mssql_init_root])
            if playwright:
                setup_args.append("--playwright")
            for skill_name in normalized_codex_skills:
                setup_args.extend(["--codex-skill", skill_name])
            setup_args.extend([resolved_vm_name, resolved_session_id, vm_user])
            if helper is not None:
                response = helper.call(
                    "run_script",
                    script_path=str(setup_script),
                    args=setup_args,
                    env=run_env,
                )
                message = process_helpers.combined_process_message(
                    str(response.get("stdout") or ""),
                    str(response.get("stderr") or ""),
                )
                completed = SimpleNamespace(returncode=int(response.get("returncode") or 0))
            else:
                allowed_env_keys = (
                    "SSH_PUBLIC_KEY_FILE",
                    "SSH_PRIVATE_KEY_PATH",
                    "GATEWAY_URL",
                    "LLM_BASE_URL",
                    "AGENT_MODEL",
                    "AGENT_PROVIDER",
                    "HOST_CLAUDE_BIN",
                    "NUGET_RELAY_BASE_URL",
                    "AGENT_SANDBOX_RUNTIME_DIR",
                    "VM_STORAGE_DIR",
                    "SHARED_NUGET_CACHE_DIR",
                    "SHARED_NUGET_CACHE_TAG",
                    "SHARED_NUGET_CACHE_MODE",
                    "RAM_MB",
                    "VCPUS",
                    "DISK_SIZE_GB",
                    "VM_WAIT_IP_TIMEOUT_SECONDS",
                    "VM_WAIT_SSH_TIMEOUT_SECONDS",
                    "MSSQL_SA_PASSWORD",
                    "AGENT_SANDBOX_GIT_ROOT",
                    "AGENT_SANDBOX_GIT_HOST",
                    "AGENT_SANDBOX_GIT_USER",
                    "AGENT_SANDBOX_GIT_SSH_ALIAS",
                    "AGENT_SANDBOX_AZURE_DEVOPS_PR_PAT_ENV",
                    "AGENT_POST_EXEC_VERIFICATION_PROMPT",
                    "ENABLE_GUEST_HEARTBEAT",
                    "GUEST_HEARTBEAT_PROMPT",
                    "HOST_REPO_CODEX_SKILLS_DIR",
                )
                launch_step = process_helpers.sudo_env_command(
                    [str(setup_script), *setup_args],
                    run_env,
                    allowed_env_keys,
                )
                completed = process_helpers.run_subprocess(
                    launch_step, capture_output=True, text=True, env=run_env
                )
                message = process_helpers.combined_process_message(
                    completed.stdout, completed.stderr
                )
                if completed.returncode != 0 and process_helpers.can_prompt_for_privileges():
                    launch_step = process_helpers.pkexec_env_command(
                        [str(setup_script), *setup_args],
                        run_env,
                        allowed_env_keys,
                    )
                    completed = process_helpers.run_subprocess(
                        launch_step, capture_output=True, text=True, env=run_env
                    )
                    message = process_helpers.combined_process_message(
                        completed.stdout, completed.stderr
                    )
        except Exception as exc:
            return _issue_vm_failure(str(exc) or f"Failed to create VM {resolved_vm_name}")

        if completed.returncode != 0:
            return _issue_vm_failure(
                message or f"{setup_script.name} exited with code {completed.returncode}"
            )
        return VmActionResult(
            success=True,
            vm_name=resolved_vm_name,
            action="create_issue_vm",
            message=message or f"Created issue VM {resolved_vm_name}",
        )

    def create_issue_vm_from_local_issue(
        self,
        issue_identifier: str,
        *,
        model: str | None = None,
        model_provider_mode: str | None = None,
        vm_user: str = "agent",
        dotnet: bool = False,
        nuget: str | None = None,
        mssql: bool = False,
        mssql_init_root: str | None = None,
        playwright: bool = False,
        codex_skills: list[str] | None = None,
    ) -> VmActionResult:
        try:
            issue = self._deps.resolve_local_issue_record(issue_identifier)
        except ValueError as exc:
            return VmActionResult(
                success=False,
                vm_name=f"vm-{issue_identifier}",
                action="create_issue_vm",
                message=str(exc),
            )
        return self.create_issue_vm(
            issue=issue.issue_id,
            model=model,
            model_provider_mode=model_provider_mode,
            vm_user=vm_user,
            dotnet=dotnet,
            nuget=nuget,
            mssql=mssql,
            mssql_init_root=mssql_init_root,
            playwright=playwright,
            codex_skills=codex_skills,
            directory_path=issue.directory_path,
        )

    def normalize_vm_creation_options(
        self,
        *,
        provider: str | None = None,
        vm_name: str,
        session_id: str | None = None,
        allowed_contact_ids: list[str] | None = None,
        teams_channel_allowlist: list[str] | None = None,
        teams_chat_allowlist: list[str] | None = None,
        teams_user_allowlist: list[str] | None = None,
        communication_file_receive_enabled: bool | None = None,
        communication_file_send_enabled: bool | None = None,
        communication_file_max_bytes: int | None = None,
        communication_file_require_approval_for_send: bool | None = None,
        web_search_enabled: bool | None = None,
        directory_path: str = "root",
        metadata: dict[str, Any] | None = None,
        model_provider_mode: str | None = None,
    ) -> NormalizedVmCreationOptions:
        payload: dict[str, Any] = {}
        if teams_channel_allowlist is not None:
            payload["teams_channel_allowlist"] = teams_channel_allowlist
        if teams_chat_allowlist is not None:
            payload["teams_chat_allowlist"] = teams_chat_allowlist
        if teams_user_allowlist is not None:
            payload["teams_user_allowlist"] = teams_user_allowlist
        if communication_file_receive_enabled is not None:
            payload["communication_file_receive_enabled"] = communication_file_receive_enabled
        if communication_file_send_enabled is not None:
            payload["communication_file_send_enabled"] = communication_file_send_enabled
        if communication_file_max_bytes is not None:
            payload["communication_file_max_bytes"] = communication_file_max_bytes
        if communication_file_require_approval_for_send is not None:
            payload["communication_file_require_approval_for_send"] = (
                communication_file_require_approval_for_send
            )
        if web_search_enabled is not None:
            payload["web_search_enabled"] = web_search_enabled
        if metadata is not None:
            payload["metadata"] = metadata

        resolved_model_provider_mode = normalize_model_provider_mode_override(model_provider_mode)
        if resolved_model_provider_mode is not None:
            payload["metadata"] = _deep_merge_mapping(
                payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
                {MODEL_PROVIDER_MODE_METADATA_KEY: resolved_model_provider_mode},
            )

        resolved_allowed_contact_ids = self._validate_allowed_contact_ids_for_directory(
            session_id=session_id or vm_name,
            directory_path=directory_path,
            allowed_contact_ids=allowed_contact_ids,
        )
        return NormalizedVmCreationOptions(
            provider=provider,
            allowed_contact_ids=resolved_allowed_contact_ids,
            payload=payload,
        )

    def _validate_allowed_contact_ids_for_directory(
        self,
        *,
        session_id: str,
        directory_path: str,
        allowed_contact_ids: object,
    ) -> list[str] | None:
        if allowed_contact_ids is None:
            return None
        provider_config = getattr(self._deps.runtime, "provider_config", None)
        resolve_contacts_for_directory = getattr(
            provider_config, "resolve_contacts_for_directory", None
        )
        if not callable(resolve_contacts_for_directory):
            raise RuntimeError("contacts config resolver is not available")
        contacts_config = resolve_contacts_for_directory(directory_path)
        return validate_allowed_contact_ids_for_directory(
            contacts_config,
            allowed_contact_ids,
            session_id=session_id,
            directory_path=directory_path,
        )

    def create_vm(
        self,
        *,
        provider: str | None = None,
        vm_name: str,
        session_id: str | None = None,
        labels: dict[str, str] | None = None,
        model: str | None = None,
        model_provider_mode: str | None = None,
        vm_user: str = "agent",
        ttl_seconds: int | None = None,
        ttl_hours: int | None = None,
        allowed_tools: list[str | ToolName] | None = None,
        tool_argument_policies: dict[str, list[Any]] | None = None,
        allowed_script_names: list[str] | None = None,
        allowed_contact_ids: list[str] | None = None,
        discord_channels: list[str] | None = None,
        teams_channel_allowlist: list[str] | None = None,
        teams_chat_allowlist: list[str] | None = None,
        teams_user_allowlist: list[str] | None = None,
        email_mailboxes: list[str] | None = None,
        email_folders: list[str] | None = None,
        communication_file_receive_enabled: bool | None = None,
        communication_file_send_enabled: bool | None = None,
        communication_file_max_bytes: int | None = None,
        communication_file_require_approval_for_send: bool | None = None,
        web_search_enabled: bool | None = None,
        dotnet: bool = False,
        nuget: bool = False,
        mssql: bool = False,
        mssql_init_root: str | None = None,
        playwright: bool = False,
        codex_skills: list[str] | None = None,
        directory_path: str = "root",
        metadata: dict[str, Any] | None = None,
        allow_existing_session: bool = False,
    ) -> VmActionResult:
        normalized_options = self.normalize_vm_creation_options(
            provider=provider,
            vm_name=vm_name,
            session_id=session_id,
            allowed_contact_ids=allowed_contact_ids,
            teams_channel_allowlist=teams_channel_allowlist,
            teams_chat_allowlist=teams_chat_allowlist,
            teams_user_allowlist=teams_user_allowlist,
            communication_file_receive_enabled=communication_file_receive_enabled,
            communication_file_send_enabled=communication_file_send_enabled,
            communication_file_max_bytes=communication_file_max_bytes,
            communication_file_require_approval_for_send=(
                communication_file_require_approval_for_send
            ),
            web_search_enabled=web_search_enabled,
            directory_path=directory_path,
            metadata=metadata,
            model_provider_mode=model_provider_mode,
        )
        directory_path = str(directory_path or "root")
        vm_defaults = self._deps.resolve_vm_defaults(directory_path)
        resolved_provider = (
            str(provider or getattr(vm_defaults, "provider", None) or "libvirt").strip()
            or "libvirt"
        )
        return (
            self._provider_registry()
            .get(resolved_provider)
            .create_vm(
                vm_name=vm_name,
                session_id=session_id,
                labels=labels,
                model=model,
                vm_user=vm_user,
                ttl_seconds=ttl_seconds,
                ttl_hours=ttl_hours,
                allowed_tools=allowed_tools,
                tool_argument_policies=tool_argument_policies,
                allowed_script_names=allowed_script_names,
                discord_channels=discord_channels,
                email_mailboxes=email_mailboxes,
                email_folders=email_folders,
                dotnet=dotnet,
                nuget=nuget,
                mssql=mssql,
                mssql_init_root=mssql_init_root,
                playwright=playwright,
                codex_skills=codex_skills,
                directory_path=directory_path,
                allow_existing_session=allow_existing_session,
                provider=resolved_provider,
                **normalized_options.provider_kwargs(),
            )
        )

    def clone_repo_into_vm(
        self,
        *,
        vm_name: str,
        session_id: str | None = None,
        repo_source: str,
        clone_branch: str | None = None,
        vm_user: str = "agent",
        directory_path: str = "root",
        git_author_name: str | None = None,
        git_author_email: str | None = None,
        git_committer_name: str | None = None,
        git_committer_email: str | None = None,
    ) -> VmActionResult:
        resolved_vm_name = vm_name.strip()
        if not resolved_vm_name:
            return VmActionResult(
                success=False,
                vm_name="",
                action="clone_repo_into_vm",
                message="VM name is required.",
            )

        normalized_repo_source = repo_source.strip()
        if not normalized_repo_source:
            return VmActionResult(
                success=False,
                vm_name=resolved_vm_name,
                action="clone_repo_into_vm",
                message="repo_source is required.",
            )
        normalized_clone_branch = clone_branch.strip() if clone_branch else ""

        resolved_session_id, session, normalized_directory_path, error_result = (
            self._resolve_vm_operation_context(
                vm_name=resolved_vm_name,
                session_id=session_id,
                action="clone_repo_into_vm",
                directory_path=directory_path,
            )
        )
        if error_result is not None:
            return error_result

        root_dir = Path(__file__).resolve().parents[4]
        issue_setup = self._deps.resolve_issue_setup(normalized_directory_path, root_dir=root_dir)
        vm_ip = vm_helpers.resolve_vm_ip(resolved_vm_name)

        try:
            resolved_repo_path, resolved_repo_name = self._deps.resolve_repo_source(
                normalized_repo_source
            )
            self._deps.sync_repo_into_bare_remote(
                resolved_repo_name,
                resolved_repo_path,
                issue_setup=issue_setup,
                repo_source=normalized_repo_source,
            )
            guest_repo_root = self._deps.clone_repo_into_vm(
                vm_name=resolved_vm_name,
                vm_user=vm_user,
                session_id=resolved_session_id,
                repo_name=resolved_repo_name,
                issue_setup=issue_setup,
                repo_source=normalized_repo_source,
                clone_branch=normalized_clone_branch or None,
                vm_ip=vm_ip,
                git_author_name=git_author_name,
                git_author_email=git_author_email,
                git_committer_name=git_committer_name,
                git_committer_email=git_committer_email,
            )
            guest_remote_url = (
                f"ssh://{issue_setup.git_ssh_alias}{issue_setup.git_root}/{resolved_repo_name}.git"
            )
        except Exception as exc:
            return VmActionResult(
                success=False,
                vm_name=resolved_vm_name,
                action="clone_repo_into_vm",
                message=f"Failed to clone repo into VM {resolved_vm_name}: {exc}",
            )

        return VmActionResult(
            success=True,
            vm_name=resolved_vm_name,
            action="clone_repo_into_vm",
            message=f"Cloned repo into {resolved_vm_name}",
            target_dir=guest_repo_root,
            guest_repo_name=resolved_repo_name,
            remote_url=guest_remote_url,
        )

    def bootstrap_python_in_vm(
        self,
        *,
        vm_name: str,
        session_id: str | None = None,
        host_checkout: str,
        target_dir: str,
        vm_user: str = "agent",
    ) -> VmActionResult:
        resolved_vm_name = vm_name.strip()
        if not resolved_vm_name:
            return VmActionResult(
                success=False,
                vm_name="",
                action="bootstrap_python_in_vm",
                message="VM name is required.",
            )

        normalized_host_checkout = host_checkout.strip()
        if not normalized_host_checkout:
            return VmActionResult(
                success=False,
                vm_name=resolved_vm_name,
                action="bootstrap_python_in_vm",
                message="host_checkout is required.",
            )

        normalized_target_dir = target_dir.strip()
        if not normalized_target_dir:
            return VmActionResult(
                success=False,
                vm_name=resolved_vm_name,
                action="bootstrap_python_in_vm",
                message="target_dir is required.",
            )

        _, _, _, error_result = self._resolve_vm_operation_context(
            vm_name=resolved_vm_name,
            session_id=session_id,
            action="bootstrap_python_in_vm",
            directory_path="root",
        )
        if error_result is not None:
            return error_result

        python_script = Path(__file__).resolve().parents[4] / "scripts" / "bootstrap_python_vm.sh"
        try:
            completed = process_helpers.run_subprocess(
                [
                    str(python_script),
                    "--vm-user",
                    vm_user,
                    resolved_vm_name,
                    normalized_host_checkout,
                    normalized_target_dir,
                ],
                capture_output=True,
                text=True,
                env=os.environ
                | {"HOST_PYTHON_BIN": process_helpers.resolve_host_python_with_pip()},
            )
            message = process_helpers.combined_process_message(completed.stdout, completed.stderr)
            if completed.returncode != 0:
                raise RuntimeError(
                    message or f"{python_script.name} exited with code {completed.returncode}"
                )
        except Exception as exc:
            return VmActionResult(
                success=False,
                vm_name=resolved_vm_name,
                action="bootstrap_python_in_vm",
                message=f"Failed to bootstrap Python in VM {resolved_vm_name}: {exc}",
            )

        return VmActionResult(
            success=True,
            vm_name=resolved_vm_name,
            action="bootstrap_python_in_vm",
            message=message or f"Bootstrapped Python in {resolved_vm_name}",
        )

    def bootstrap_frontend_in_vm(
        self,
        *,
        vm_name: str,
        session_id: str | None = None,
        host_checkout: str,
        install_roots: list[str] | None,
        vm_user: str = "agent",
    ) -> VmActionResult:
        resolved_vm_name = vm_name.strip()
        if not resolved_vm_name:
            return VmActionResult(
                success=False,
                vm_name="",
                action="bootstrap_frontend_in_vm",
                message="VM name is required.",
            )

        normalized_host_checkout = host_checkout.strip()
        if not normalized_host_checkout:
            return VmActionResult(
                success=False,
                vm_name=resolved_vm_name,
                action="bootstrap_frontend_in_vm",
                message="host_checkout is required.",
            )

        normalized_install_roots = [
            item.strip() for item in (install_roots or []) if item and item.strip()
        ]
        if not normalized_install_roots:
            return VmActionResult(
                success=False,
                vm_name=resolved_vm_name,
                action="bootstrap_frontend_in_vm",
                message="install_roots is required.",
            )

        _, _, _, error_result = self._resolve_vm_operation_context(
            vm_name=resolved_vm_name,
            session_id=session_id,
            action="bootstrap_frontend_in_vm",
            directory_path="root",
        )
        if error_result is not None:
            return error_result

        frontend_script = (
            Path(__file__).resolve().parents[4] / "scripts" / "bootstrap_frontend_deps_vm.sh"
        )
        try:
            completed = process_helpers.run_subprocess(
                [
                    str(frontend_script),
                    "--vm-user",
                    vm_user,
                    resolved_vm_name,
                    normalized_host_checkout,
                    *normalized_install_roots,
                ],
                capture_output=True,
                text=True,
                env=os.environ.copy(),
            )
            message = process_helpers.combined_process_message(completed.stdout, completed.stderr)
            if completed.returncode != 0:
                raise RuntimeError(
                    message or f"{frontend_script.name} exited with code {completed.returncode}"
                )
        except Exception as exc:
            return VmActionResult(
                success=False,
                vm_name=resolved_vm_name,
                action="bootstrap_frontend_in_vm",
                message=f"Failed to bootstrap frontend deps in VM {resolved_vm_name}: {exc}",
            )

        return VmActionResult(
            success=True,
            vm_name=resolved_vm_name,
            action="bootstrap_frontend_in_vm",
            message=message or f"Bootstrapped frontend deps in {resolved_vm_name}",
        )

    def _resolve_vm_operation_context(
        self,
        *,
        vm_name: str,
        session_id: str | None,
        action: str,
        directory_path: str,
    ) -> tuple[str, Any | None, str, VmActionResult | None]:
        try:
            inferred_session_id, inferred_session = self._find_session_for_vm(vm_name)
        except ValueError as exc:
            return (
                "",
                None,
                "root",
                VmActionResult(success=False, vm_name=vm_name, action=action, message=str(exc)),
            )

        resolved_session_id = (
            session_id.strip() if session_id and session_id.strip() else inferred_session_id
        )
        session = inferred_session
        if resolved_session_id:
            session = self._session_repository().get_session(resolved_session_id)
            if session is None:
                return (
                    "",
                    None,
                    "root",
                    VmActionResult(
                        success=False,
                        vm_name=vm_name,
                        action=action,
                        message=f"Unknown session: {resolved_session_id}",
                    ),
                )
        else:
            resolved_session_id = session_helpers.default_vm_session_id(vm_name)

        if session is None and vm_helpers.dominfo_field(vm_name, "State") is None:
            return (
                "",
                None,
                "root",
                VmActionResult(
                    success=False,
                    vm_name=vm_name,
                    action=action,
                    message=f"VM not found: {vm_name}",
                ),
            )

        normalized_directory_path = str(
            getattr(session, "directory_path", "") or ""
        ).strip() or self._deps.directory_path.normalize_directory_path(directory_path)
        return resolved_session_id, session, normalized_directory_path, None

    def destroy_vm(self, target: str) -> VmActionResult:
        vm_name = target.strip()
        if not vm_name:
            return VmActionResult(
                success=False, vm_name="", action="destroy_vm", message="VM name is required."
            )
        issue_number = session_helpers.extract_issue_number(vm_name)
        if issue_number is not None and vm_name == f"vm-{issue_number}":
            return self.destroy_issue_vm(issue_number)

        root_dir = Path(__file__).resolve().parents[4]
        destroy_script = root_dir / "scripts" / "destroy_agent_vm.sh"
        helper = (
            self._deps.privileged_helper.get_privileged_helper()
            if self._deps.privileged_helper.get_use_privileged_helper()
            else None
        )
        if helper is not None and not helper.is_running():
            helper.start()
        if helper is not None:
            response = helper.call("destroy_vm", script_path=str(destroy_script), vm_name=vm_name)
            message = str(response.get("stdout") or response.get("stderr") or "").strip()
        else:
            completed, message = self._run_privileged_host_script(destroy_script, [vm_name])
            if completed.returncode != 0:
                return VmActionResult(
                    success=False,
                    vm_name=vm_name,
                    action="destroy_vm",
                    message=message or f"destroy-vm exited with code {completed.returncode}",
                )

        session_repository = self._session_repository()
        session_ids = {
            session.session_id for session in session_repository.list_sessions_for_vm(vm_name)
        }
        for session_id in session_ids:
            session_repository.delete_session(session_id)
        removed_approvals = session_helpers.prune_approvals_for_sessions(
            self._deps.runtime, session_ids=session_ids
        )
        removed_pr_map_entries, removed_streams = git_helpers.prune_pr_map(
            self._deps.config.pr_map_path, session_ids=session_ids
        )
        removed_cursors, removed_feedback = git_helpers.prune_pr_feedback_store(
            self._deps.config.runtime_dir / "pr-feedback-store.json",
            streams=removed_streams,
        )
        summary = (
            f"Destroyed VM {vm_name}; "
            f"removed_sessions={len(session_ids)} "
            f"removed_pr_map_entries={removed_pr_map_entries} "
            f"removed_approvals={removed_approvals} "
            f"removed_feedback_cursors={removed_cursors} "
            f"removed_feedback_items={removed_feedback}"
        )
        if message:
            summary = f"{message}\n{summary}"
        return VmActionResult(success=True, vm_name=vm_name, action="destroy_vm", message=summary)

    def destroy_issue_vm(self, target: str) -> VmActionResult:
        issue_number = session_helpers.extract_issue_number(target) or target
        vm_name = f"vm-{issue_number}" if issue_number else target
        root_dir = Path(__file__).resolve().parents[4]
        destroy_script = root_dir / "scripts" / "destroy_agent_vm.sh"

        completed, message = self._run_privileged_host_script(destroy_script, [vm_name])
        if completed.returncode != 0:
            return VmActionResult(
                success=False,
                vm_name=vm_name,
                action="destroy_issue_vm",
                message=message or f"destroy-issue-vm exited with code {completed.returncode}",
            )

        session_repository = self._session_repository()
        session_ids = {
            session.session_id
            for session in session_repository.list_sessions_for_issue(issue_number)
        }
        for session_id in session_ids:
            session_repository.delete_session(session_id)
        removed_approvals = session_helpers.prune_approvals_for_sessions(
            self._deps.runtime, session_ids=session_ids
        )
        removed_pr_map_entries, removed_streams = git_helpers.prune_pr_map(
            self._deps.config.pr_map_path, session_ids=session_ids
        )
        removed_cursors, removed_feedback = git_helpers.prune_pr_feedback_store(
            self._deps.config.runtime_dir / "pr-feedback-store.json",
            streams=removed_streams,
        )
        bare_repo_removed, bare_repo_path = git_helpers.remove_issue_bare_repo(
            issue_number,
            root_dir=root_dir,
            runtime_dir=self._deps.config.runtime_dir,
        )
        summary = (
            f"Destroyed issue VM {vm_name}; "
            f"removed_sessions={len(session_ids)} "
            f"removed_pr_map_entries={removed_pr_map_entries} "
            f"removed_approvals={removed_approvals} "
            f"removed_feedback_cursors={removed_cursors} "
            f"removed_feedback_items={removed_feedback} "
            f"bare_repo_removed={bare_repo_removed} "
            f"bare_repo_path={bare_repo_path}"
        )
        if message:
            summary = f"{message}\n{summary}"
        return VmActionResult(
            success=True,
            vm_name=vm_name,
            action="destroy_issue_vm",
            message=summary,
        )

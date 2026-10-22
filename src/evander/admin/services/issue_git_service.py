from __future__ import annotations

import copy
import fcntl
import json
import os
import re
import yaml
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from evander.admin.services import issue_helpers, session_helpers, vm_helpers
from evander.admin.services.ports import DirectoryPathPort, DirectoryOwnershipPort

from evander.models import (
    LocalIssueRecord,
    RemoteIssueReference,
    VmActionResult,
)
from evander.repositories.session_manifests import as_session_manifest_repository
from evander.admin.services.local_issue_identity import LocalIssueIdentityResolver


def _extract_local_issue_id_candidate(value: str) -> str | None:
    normalized = value.strip()
    if not normalized:
        return None
    if normalized.isdigit():
        return normalized
    if normalized.startswith("vm-") and normalized[3:].isdigit():
        return normalized[3:]
    prefixed_match = re.match(r"^(?:local|issue)[:/\-]([0-9]+)$", normalized, flags=re.IGNORECASE)
    if prefixed_match:
        return prefixed_match.group(1)
    session_match = re.match(r"^issue-([0-9]+)(?:-|$)", normalized)
    if session_match:
        return session_match.group(1)
    return None


def _apply_issue_metadata_dot_patch(
    metadata: Mapping[str, Any], patch: Mapping[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    updated = copy.deepcopy(dict(metadata))
    touched: list[str] = []
    for raw_path, value in patch.items():
        path = str(raw_path).strip()
        if not path:
            raise ValueError("effect:issue:update-metadata patch contains an empty metadata path")
        parts = path.split(".")
        if any(part == "" for part in parts):
            raise ValueError(
                f"effect:issue:update-metadata patch path {path!r} contains an empty segment"
            )
        cursor: dict[str, Any] = updated
        for segment in parts[:-1]:
            existing = cursor.get(segment)
            if existing is None:
                next_value: dict[str, Any] = {}
                cursor[segment] = next_value
                cursor = next_value
                continue
            if not isinstance(existing, dict):
                raise ValueError(
                    "effect:issue:update-metadata cannot set "
                    f"{path!r} because metadata.{segment} is not an object"
                )
            cursor = existing
        cursor[parts[-1]] = copy.deepcopy(value)
        touched.append(path)
    return updated, touched


def _apply_json_merge_patch(
    metadata: Mapping[str, Any], merge_patch: Mapping[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    touched: list[str] = []

    def merge(base: Any, patch: Mapping[str, Any], prefix: str) -> dict[str, Any]:
        result = copy.deepcopy(base) if isinstance(base, Mapping) else {}
        for key, value in patch.items():
            normalized_key = str(key)
            path = f"{prefix}.{normalized_key}" if prefix else normalized_key
            touched.append(path)
            if value is None:
                result.pop(normalized_key, None)
                continue
            if isinstance(value, Mapping):
                result[normalized_key] = merge(result.get(normalized_key), value, path)
                continue
            result[normalized_key] = copy.deepcopy(value)
        return result

    return merge(metadata, merge_patch, ""), touched


def _remote_project_for_issue(issue: LocalIssueRecord) -> str | None:
    remote = issue.remote_reference
    if remote is not None:
        project = str(remote.project or "").strip()
        if project:
            return project
    metadata_remote = issue.metadata.get("remote") if isinstance(issue.metadata, Mapping) else None
    if isinstance(metadata_remote, Mapping):
        project = str(metadata_remote.get("project") or "").strip()
        if project:
            return project
    return None


def _issue_session_matches_issue(
    *,
    session_id: str,
    session: Any,
    issue_number: str,
    vm_name: str,
) -> bool:
    normalized_session_id = str(session_id or "").strip()
    session_vm_id = str(getattr(session, "vm_id", "") or "").strip()
    return (
        normalized_session_id == issue_number
        or normalized_session_id.startswith(f"issue-{issue_number}-")
        or session_vm_id == vm_name
    )


def _issue_session_backend(session: Any) -> str:
    metadata = getattr(session, "metadata", None)
    if not isinstance(metadata, Mapping):
        metadata = {}
    backend = str(
        metadata.get("execution_backend") or getattr(session, "vm_provider", None) or ""
    ).strip()
    if backend:
        return backend
    if str(getattr(session, "vm_id", "") or "").strip():
        return "libvirt_vm"
    return "session"


def _issue_session_agent_id(session_id: str, session: Any) -> str:
    metadata = getattr(session, "metadata", None)
    if not isinstance(metadata, Mapping):
        metadata = {}
    return (
        str(
            metadata.get("target_name")
            or getattr(session, "vm_provider_id", None)
            or getattr(session, "vm_id", None)
            or session_id
        ).strip()
        or session_id
    )


def _local_issue_delete_blocker_message(
    *,
    issue_number: str,
    vm_name: str,
    existing_vm_state: str | None,
    blocking_sessions: list[tuple[str, Any]],
) -> str:
    blocker_descriptions: list[str] = []
    if existing_vm_state is not None:
        state_detail = f"state: {existing_vm_state}" if existing_vm_state else "state unknown"
        blocker_descriptions.append(
            f"VM {vm_name!r} still exists ({state_detail}); destroy it from "
            "Admin Web > Agents or VMs & Issues"
        )

    for session_id, session in sorted(blocking_sessions, key=lambda item: item[0]):
        directory_path = str(getattr(session, "directory_path", "") or "root").strip() or "root"
        backend = _issue_session_backend(session)
        agent_id = _issue_session_agent_id(session_id, session)
        blocker_descriptions.append(
            f"session {session_id!r} (directory: {directory_path!r}; backend: {backend!r}; "
            f"agent id: {agent_id!r}); find it in Admin Web > Sessions or Agents after "
            f"selecting directory {directory_path!r}"
        )

    if not blocker_descriptions:
        return f"Cannot delete local issue {issue_number}: linked execution state still exists."

    blockers = "; ".join(blocker_descriptions)
    return (
        f"Cannot delete local issue {issue_number} because linked execution state still exists. "
        f"Remove these blockers first: {blockers}."
    )


def _issue_updated_at_matches(issue: LocalIssueRecord, expected_updated_at: str) -> bool:
    expected = str(expected_updated_at).strip()
    if expected == issue.updated_at.isoformat():
        return True
    normalized = expected[:-1] + "+00:00" if expected.endswith("Z") else expected
    try:
        expected_dt = datetime.fromisoformat(normalized)
    except ValueError:
        return False
    actual_dt = issue.updated_at
    if expected_dt.tzinfo is not None and actual_dt.tzinfo is not None:
        return expected_dt.astimezone(actual_dt.tzinfo) == actual_dt
    return expected_dt == actual_dt


@dataclass(frozen=True)
class IssueGitAdminDependencies:
    project_dir: Path
    runtime: Any
    config: Any
    local_issue_store: Any
    local_issue_yaml_dir: Path
    issue_service: Any
    directory_path: DirectoryPathPort
    local_issue_creation_lock: Any
    next_local_issue_number: Any
    canonical_local_issue_id: Any
    resolve_local_issue_record: Any
    resolve_local_issue_key: Any
    proxy_target_for_issue: Any
    refresh_proxy_cache_from_remote: Any
    write_local_issue_yaml: Any
    directory_ownership: DirectoryOwnershipPort
    managed_git_admin: Any | None = None


@dataclass
class IssueGitAdminSupport:
    project_dir: Path
    runtime: Any
    config: Any
    local_issue_store: Any
    local_issue_identity_resolver: LocalIssueIdentityResolver
    local_issue_counter_path: Path
    local_issue_create_lock_path: Path
    local_issue_yaml_dir: Path
    issue_service: Any
    directory_admin: Any
    directory_ownership: Any

    def normalize_directory_path(self, directory_path: str | None) -> str:
        if not directory_path:
            return "root"
        result = self.directory_admin.get(directory_path)
        if not result.ok or result.data is None:
            return "root"
        return result.data.details.directory.path

    @contextmanager
    def local_issue_creation_lock(self):
        self.local_issue_create_lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.local_issue_create_lock_path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    def next_local_issue_number(self) -> str:
        current = 0
        if self.local_issue_counter_path.exists():
            try:
                current = int(
                    self.local_issue_counter_path.read_text(encoding="utf-8").strip() or "0"
                )
            except ValueError:
                current = 0
        current += 1
        self.local_issue_counter_path.parent.mkdir(parents=True, exist_ok=True)
        self.local_issue_counter_path.write_text(f"{current}\n", encoding="utf-8")
        return str(current)

    def extract_local_issue_id_candidate(self, value: str) -> str | None:
        return _extract_local_issue_id_candidate(value)

    def canonical_local_issue_id(self, issue_number: str, directory_path: str) -> str:
        return issue_helpers.scoped_issue_id(
            issue_number, directory_path=directory_path, provider="local"
        )

    def canonical_issue_id(self, record: LocalIssueRecord) -> str:
        remote = record.remote_reference
        if remote is not None and remote.provider == "azure_devops" and remote.work_item_id:
            return issue_helpers.scoped_issue_id(
                remote.work_item_id,
                directory_path=record.directory_path,
                provider="azure_devops",
                organization=remote.organization,
                project=remote.project,
            )
        return self.canonical_local_issue_id(record.issue_number, record.directory_path)

    def resolve_local_issue_key(self, identifier: str) -> str | None:
        return self.local_issue_identity_resolver.resolve_key(identifier)

    def find_local_issue_by_remote_identifier(
        self, issue_identifier: str
    ) -> LocalIssueRecord | None:
        return self.local_issue_identity_resolver.find_by_remote_identifier(issue_identifier)

    def resolve_local_issue_record(self, issue_identifier: str) -> LocalIssueRecord:
        return self.local_issue_identity_resolver.resolve_record(issue_identifier)

    def proxy_target_for_issue(self, issue: LocalIssueRecord) -> tuple[str, str] | None:
        remote = issue.remote_reference
        if remote is not None and remote.provider and remote.work_item_id:
            return remote.provider, remote.work_item_id
        return None

    def refresh_proxy_cache_from_remote(
        self,
        issue: LocalIssueRecord,
        remote_payload: dict[str, object],
    ) -> LocalIssueRecord:
        issue.metadata = dict(issue.metadata)
        remote_comments = [
            dict(comment)
            for comment in list(remote_payload.get("comments") or [])
            if isinstance(comment, dict)
        ]
        issue.metadata["proxy_cache"] = {
            "title": str(remote_payload.get("title") or issue.title),
            "description": str(remote_payload.get("description") or issue.body),
            "state": str(remote_payload.get("state") or ""),
            "url": str(remote_payload.get("url") or ""),
            "updated_at": str(remote_payload.get("updated_at") or ""),
            "comments": remote_comments,
        }
        issue.title = str(remote_payload.get("title") or issue.title).strip() or issue.title
        issue.body = str(remote_payload.get("description") or issue.body).strip() or issue.body
        issue.state = str(remote_payload.get("state") or issue.state or "").strip() or None
        issue.url = str(remote_payload.get("url") or issue.url or "").strip() or None
        issue.comments = remote_comments
        if issue.remote_reference is not None:
            issue.remote_reference.last_synced_at = session_helpers.utc_now()
            issue.remote_reference.last_known_remote_state = issue.state
            issue.remote_reference.work_item_url = issue.url or issue.remote_reference.work_item_url
        issue.updated_at = session_helpers.utc_now()
        self.local_issue_store.put(issue.issue_id, issue)
        return issue

    def write_local_issue_yaml(
        self, issue: LocalIssueRecord, *, updated_at: datetime | None = None
    ) -> None:
        yaml_path = Path(issue.yaml_path)
        payload: dict[str, object] = {}
        if yaml_path.exists():
            try:
                loaded = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
            except Exception:
                loaded = {}
            if isinstance(loaded, dict):
                payload = dict(loaded)
        payload["issue_number"] = issue.issue_number
        payload["title"] = issue.title
        payload["body"] = issue.body
        payload["state"] = issue.state
        payload["source"] = issue.source or "local_gui"
        fields = issue_helpers.local_issue_fields(issue)
        if fields:
            payload["fields"] = fields
        else:
            payload.pop("fields", None)
        metadata_steps = issue.metadata.get("steps") if isinstance(issue.metadata, dict) else None
        if isinstance(metadata_steps, list) and all(
            isinstance(step, str) for step in metadata_steps
        ):
            payload["steps"] = list(metadata_steps)
        else:
            payload.pop("steps", None)
        metadata_plan_summary = (
            issue.metadata.get("plan_summary") if isinstance(issue.metadata, dict) else None
        )
        if isinstance(metadata_plan_summary, str) and metadata_plan_summary.strip():
            payload["plan_summary"] = metadata_plan_summary
        else:
            payload.pop("plan_summary", None)
        if updated_at is not None:
            payload["updated_at"] = updated_at.isoformat()
        yaml_path.parent.mkdir(parents=True, exist_ok=True)
        yaml_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    def direct_owned_entity_ids(self, directory_path: str, *, kind: str) -> set[str]:
        snapshot = self.directory_ownership.list_entities(directory_path, include_descendants=False)
        return {entity.entity_id for entity in snapshot.entities if entity.kind == kind}


class IssueGitAdminService:
    """Issue records and session metadata owned by the admin service."""

    def __init__(self, dependencies: IssueGitAdminDependencies) -> None:
        self._deps = dependencies

    def _session_repository(self):
        runtime = self._deps.runtime
        return as_session_manifest_repository(runtime.session_repository)

    def _resolve_session_target(self, target: str):
        repository = self._session_repository()
        try:
            session = repository.resolve_session_target(target)
        except Exception:
            session = None
        if session is not None:
            return str(getattr(session, "session_id", "") or "").strip(), session
        raise ValueError(f"Unknown session target: {target}")

    def _resolve_managed_repo_id(
        self,
        *,
        directory_path: str,
        managed_repo_id: str | None,
        repo_source: str | None,
    ) -> str | None:
        normalized_id = str(managed_repo_id or "").strip().lower() or None
        normalized_source = str(repo_source or "").strip() or None
        if normalized_id is None and normalized_source is None:
            return None
        managed_git = self._deps.managed_git_admin
        if managed_git is None:
            raise RuntimeError("managed Git service is not configured")
        if normalized_source is not None:
            record = managed_git.register_repository(
                directory_path=directory_path,
                repo_source=normalized_source,
            )
            registered_id = str(record.get("managed_repo_id") or "").strip()
            if not registered_id:
                raise RuntimeError("managed Git registration did not return managed_repo_id")
            if normalized_id is not None and normalized_id != registered_id:
                raise ValueError(
                    "managed_repo_id and repo_source resolve to different managed repositories"
                )
            return registered_id
        record = managed_git.get_repository(normalized_id, directory_path=directory_path)
        resolved_id = str(record.get("managed_repo_id") or "").strip()
        if not resolved_id:
            raise RuntimeError("managed Git lookup did not return managed_repo_id")
        return resolved_id

    def _bind_issue_repository(self, issue: LocalIssueRecord) -> None:
        if issue.managed_repo_id is None:
            return
        managed_git = self._deps.managed_git_admin
        if managed_git is None:
            raise RuntimeError("managed Git service is not configured")
        managed_git.bind(issue.managed_repo_id, kind="issues", binding_id=issue.issue_id)

    def _unbind_issue_repositories(self, issue_id: str) -> None:
        managed_git = self._deps.managed_git_admin
        if managed_git is None:
            return
        managed_git.unbind_all(kind="issues", binding_id=issue_id)

    def session_update_metadata(
        self, *, target: str, metadata: Mapping[str, Any]
    ) -> dict[str, Any]:
        session_repository = self._session_repository()
        session_id, manifest = self._resolve_session_target(target)
        merged_metadata = session_helpers.deep_merge_mapping(manifest.metadata, metadata)
        updated_manifest = manifest.model_copy(update={"metadata": merged_metadata})
        session_repository.upsert_session(updated_manifest)
        return {
            "success": True,
            "action": "session_update_metadata",
            "message": f"Updated metadata for {session_id}",
            "session_id": session_id,
            "vm_name": updated_manifest.vm_id,
            "directory_path": updated_manifest.directory_path,
            "metadata": dict(updated_manifest.metadata),
        }

    def session_set_image_generation_enabled(
        self, *, target: str, enabled: bool
    ) -> dict[str, Any]:
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be a boolean")
        session_repository = self._session_repository()
        session_id, manifest = self._resolve_session_target(target)
        updated_manifest = manifest.model_copy(update={"image_generation_enabled": enabled})
        session_repository.upsert_session(updated_manifest)
        return {
            "success": True,
            "action": "session_set_image_generation_enabled",
            "message": (
                f"{'Enabled' if enabled else 'Disabled'} image generation for {session_id}"
            ),
            "session_id": session_id,
            "vm_name": updated_manifest.vm_id,
            "directory_path": updated_manifest.directory_path,
            "image_generation_enabled": updated_manifest.image_generation_enabled,
        }

    def session_context_query(
        self, *, target: str, include_activity: bool = False
    ) -> dict[str, Any]:
        session_id, manifest = self._resolve_session_target(target)
        result = {
            "session_id": session_id,
            "vm_name": manifest.vm_id,
            "directory_path": manifest.directory_path,
            "metadata": dict(manifest.metadata),
            "tool_summary": self._deps.runtime.policy.allowed_tool_summary_for_session(manifest),
            "created_at": manifest.created_at.isoformat(),
            "expires_at": manifest.expires_at.isoformat(),
            "model": manifest.model,
        }
        if include_activity:
            from evander.admin.services.conversation_delivery_service import active_conversation
            from evander.execution.container_worker_backend import ContainerWorkerVmExecutionBackend

            result["activity"] = {"active": False}
            if manifest.metadata.get("execution_backend") == "container_worker_vm":
                try:
                    resolver = getattr(self._deps.runtime, "provider_config", None)
                    config = (
                        resolver.resolve_for_session_id(session_id)
                        if resolver
                        else self._deps.config
                    )
                    backend = ContainerWorkerVmExecutionBackend(config=config)
                    record = active_conversation(backend, session_id)
                    if not record and backend.repository.list_active_by_session(
                        session_id, {"created", "starting", "running"}
                    ):
                        result["activity"] = {"active": True, "available": False}
                    if record:
                        meta = record.metadata
                        result["activity"] = {
                            "active": True,
                            "execution_id": record.execution_id,
                            "dispatch_id": meta.get("dispatch_id"),
                            "conversation_key": meta.get("conversation_key"),
                            "thread_id": meta.get("codex_subscription_thread_id"),
                            "turn_id": meta.get("codex_subscription_turn_id"),
                        }
                except Exception:
                    result["activity"] = {"active": True, "available": False}
        return result

    def list_local_issues(
        self,
        *,
        directory_path: str | None = None,
        direct_only: bool = False,
        states: set[str] | None = None,
        exclude_states: set[str] | None = None,
    ) -> list[LocalIssueRecord]:
        return self.list_issues(
            directory_path=directory_path,
            direct_only=direct_only,
            states=states,
            exclude_states=exclude_states,
        )

    def list_issues(
        self,
        *,
        directory_path: str | None = None,
        direct_only: bool = False,
        states: set[str] | None = None,
        exclude_states: set[str] | None = None,
    ) -> list[LocalIssueRecord]:
        admin = self._deps
        normalized_directory_path = admin.directory_path.normalize_directory_path(directory_path)
        normalized_states = {value.strip() for value in (states or set()) if value.strip()} or None
        normalized_excluded_states = {
            value.strip() for value in (exclude_states or set()) if value.strip()
        } or None
        issues = sorted(
            admin.local_issue_store.read_all().values(), key=issue_helpers.issue_number_sort_key
        )
        filtered: list[LocalIssueRecord] = []
        for issue in issues:
            issue_state = (issue.state or "").strip()
            if normalized_states is not None and issue_state not in normalized_states:
                continue
            if normalized_excluded_states is not None and issue_state in normalized_excluded_states:
                continue
            filtered.append(issue)
        if direct_only or directory_path is not None:
            return [
                issue for issue in filtered if issue.directory_path == normalized_directory_path
            ]
        return filtered

    def list_actionable_local_issues(
        self,
        *,
        directory_path: str,
        states: set[str] | None = None,
    ) -> list[LocalIssueRecord]:
        admin = self._deps
        normalized_directory_path = admin.directory_path.normalize_directory_path(directory_path)
        issues = self.list_issues(directory_path=normalized_directory_path, direct_only=True)
        actionable: list[LocalIssueRecord] = []
        for issue in issues:
            if admin.proxy_target_for_issue(issue) is not None:
                continue
            if issue.source_type != "local_manual":
                continue
            if states is not None and (issue.state or "New") not in states:
                continue
            actionable.append(issue)
        return actionable

    def list_direct_owned_entity_ids(self, directory_path: str, *, kind: str) -> list[str]:
        return sorted(
            self._deps.directory_ownership.direct_owned_entity_ids(directory_path, kind=kind)
        )

    def create_local_issue(
        self,
        *,
        title: str = "",
        body: str = "",
        managed_repo_id: str | None = None,
        repo_source: str | None = None,
        yaml_path: str | None = None,
        directory_path: str = "root",
        metadata: dict[str, Any] | None = None,
    ) -> LocalIssueRecord:
        return self.create_issue(
            title=title,
            body=body,
            managed_repo_id=managed_repo_id,
            repo_source=repo_source,
            yaml_path=yaml_path,
            directory_path=directory_path,
            metadata=metadata,
        )

    def update_local_issue(
        self,
        issue_identifier: str,
        *,
        title: str,
        body: str,
        state: str,
        managed_repo_id: str | None = None,
        repo_source: str | None = None,
    ) -> LocalIssueRecord:
        admin = self._deps
        issue = admin.resolve_local_issue_record(issue_identifier)
        if issue.source_type != "local_manual":
            raise ValueError("Only local manual issues can be edited from the admin GUI.")
        normalized_title = title.strip()
        if not normalized_title:
            raise ValueError("title is required")
        normalized_state = issue_helpers.normalize_local_issue_state(state, allow_empty=False)
        now = session_helpers.utc_now()
        previous_managed_repo_id = issue.managed_repo_id
        resolved_managed_repo_id = self._resolve_managed_repo_id(
            directory_path=issue.directory_path,
            managed_repo_id=managed_repo_id,
            repo_source=repo_source,
        )
        issue.title = normalized_title
        issue.body = body.strip()
        issue.state = normalized_state
        issue.managed_repo_id = resolved_managed_repo_id
        issue.updated_at = now
        self._bind_issue_repository(issue)
        try:
            admin.local_issue_store.put(issue.issue_id, issue)
        except BaseException:
            if resolved_managed_repo_id != previous_managed_repo_id:
                self._unbind_issue_repositories(issue.issue_id)
                if previous_managed_repo_id is not None:
                    issue.managed_repo_id = previous_managed_repo_id
                    self._bind_issue_repository(issue)
            raise
        if previous_managed_repo_id != resolved_managed_repo_id:
            managed_git = admin.managed_git_admin
            if managed_git is not None and previous_managed_repo_id is not None:
                managed_git.unbind(
                    previous_managed_repo_id,
                    kind="issues",
                    binding_id=issue.issue_id,
                )
        admin.write_local_issue_yaml(issue, updated_at=now)
        return issue

    def create_issue(
        self,
        *,
        title: str = "",
        body: str = "",
        managed_repo_id: str | None = None,
        repo_source: str | None = None,
        yaml_path: str | None = None,
        directory_path: str = "root",
        metadata: dict[str, Any] | None = None,
    ) -> LocalIssueRecord:
        admin = self._deps
        now = session_helpers.utc_now()
        normalized_directory_path = admin.directory_path.normalize_directory_path(directory_path)
        merged_metadata: dict[str, Any] = dict(metadata or {})
        source_value = "local_gui"
        source_type = "local_manual"
        remote_reference: RemoteIssueReference | None = None
        state_value: str | None = "New"
        url_value: str | None = None
        comments_value: list[dict[str, str]] = []
        with admin.local_issue_creation_lock():
            if yaml_path:
                source_path = Path(yaml_path)
                source_text = source_path.read_text(encoding="utf-8")
                try:
                    yaml_payload = json.loads(json.dumps(yaml.safe_load(source_text) or {}))
                except Exception:
                    yaml_payload = {}
                issue_number = issue_helpers.extract_issue_number_from_payload(
                    yaml_payload
                ) or session_helpers.read_issue_number_from_yaml(source_path)
                target_yaml_path = admin.local_issue_yaml_dir / f"{issue_number}.yaml"
                target_yaml_path.write_text(source_text, encoding="utf-8")
                yaml_text = target_yaml_path.read_text(encoding="utf-8")
                try:
                    yaml_payload = json.loads(json.dumps(yaml.safe_load(yaml_text) or {}))
                except Exception:
                    yaml_payload = {}
                raw_source = yaml_payload.get("source")
                if isinstance(raw_source, str) and raw_source.strip():
                    source_value = raw_source.strip()
                elif isinstance(raw_source, dict):
                    source_value = "proxy_azure_devops"
                    source_type = "remote_proxy"
                    merged_metadata["remote"] = {
                        "provider": "azure_devops",
                        "organization": str(raw_source.get("organization") or "").strip(),
                        "project": str(raw_source.get("project") or "").strip(),
                        "work_item_url": str(raw_source.get("work_item_url") or "").strip(),
                        "issue_id": issue_number,
                    }
                    remote_reference = RemoteIssueReference(
                        provider="azure_devops",
                        organization=str(raw_source.get("organization") or "").strip() or None,
                        project=str(raw_source.get("project") or "").strip() or None,
                        work_item_id=issue_number,
                        work_item_url=str(raw_source.get("work_item_url") or "").strip() or None,
                        last_synced_at=now,
                    )
                if "url" in yaml_payload or "state" in yaml_payload or "comments" in yaml_payload:
                    if source_value == "local_gui":
                        source_value = "proxy_azure_devops"
                    source_type = "remote_proxy"
                    merged_metadata["proxy_cache"] = {
                        "url": str(yaml_payload.get("url") or "").strip(),
                        "state": str(yaml_payload.get("state") or "").strip(),
                        "comments": list(yaml_payload.get("comments") or []),
                        "updated_at": str(yaml_payload.get("updated_date") or "").strip(),
                    }
                    merged_metadata.setdefault(
                        "remote", {"provider": "azure_devops", "issue_id": issue_number}
                    )
                state_value = str(yaml_payload.get("state") or "").strip() or None
                url_value = str(yaml_payload.get("url") or "").strip() or None
                comments_value = [
                    dict(comment)
                    for comment in list(yaml_payload.get("comments") or [])
                    if isinstance(comment, dict)
                ]
                raw_steps = yaml_payload.get("steps")
                if isinstance(raw_steps, list):
                    normalized_steps = [
                        str(step).strip() for step in raw_steps if str(step).strip()
                    ]
                    if normalized_steps:
                        merged_metadata["steps"] = normalized_steps
                raw_plan_summary = yaml_payload.get("plan_summary")
                if isinstance(raw_plan_summary, str):
                    normalized_plan_summary = raw_plan_summary.strip()
                    if normalized_plan_summary:
                        merged_metadata["plan_summary"] = normalized_plan_summary
                if not title:
                    parsed_title = yaml_payload.get("title")
                    normalized_title = (
                        str(parsed_title or "").strip() if isinstance(parsed_title, str) else ""
                    )
                    if normalized_title:
                        title = normalized_title
                    else:
                        title_match = re.search(
                            r"(?m)^\s*title\s*:\s*[\"']?(.*?)[\"']?\s*$", yaml_text
                        )
                        title = title_match.group(1).strip() if title_match else ""
                    if not title:
                        title = f"Local issue {issue_number}"
                if not body:
                    parsed_body = yaml_payload.get("body")
                    normalized_body = (
                        str(parsed_body or "").strip() if isinstance(parsed_body, str) else ""
                    )
                    if normalized_body:
                        body = normalized_body
                    else:
                        body_match = re.search(
                            r"(?m)^\s*body\s*:\s*[\"']?(.*?)[\"']?\s*$", yaml_text
                        )
                        body = (
                            body_match.group(1).strip()
                            if body_match
                            else str(yaml_payload.get("description") or "").strip()
                        )
            else:
                if not title.strip():
                    raise ValueError("title is required when yaml_path is not provided")
                issue_number = admin.next_local_issue_number()
                target_yaml_path = admin.local_issue_yaml_dir / f"{issue_number}.yaml"
                yaml_text = (
                    f"issue_number: {issue_number}\n"
                    f"title: {json.dumps(title.strip())}\n"
                    f"body: {json.dumps(body.strip())}\n"
                    f"state: {json.dumps('New')}\n"
                    f"source: local_gui\n"
                    f"created_at: {json.dumps(now.isoformat())}\n"
                )
                target_yaml_path.write_text(yaml_text, encoding="utf-8")
            if remote_reference is not None and remote_reference.provider == "azure_devops":
                issue_id = issue_helpers.scoped_issue_id(
                    issue_number,
                    directory_path=normalized_directory_path,
                    provider="azure_devops",
                    organization=remote_reference.organization,
                    project=remote_reference.project,
                )
            else:
                issue_id = admin.canonical_local_issue_id(issue_number, normalized_directory_path)
            existing_record = admin.local_issue_store.get(issue_id)
            resolved_managed_repo_id = (
                existing_record.managed_repo_id
                if existing_record is not None and managed_repo_id is None and repo_source is None
                else self._resolve_managed_repo_id(
                    directory_path=normalized_directory_path,
                    managed_repo_id=managed_repo_id,
                    repo_source=repo_source,
                )
            )
            record = LocalIssueRecord(
                issue_id=issue_id,
                issue_number=issue_number,
                title=title.strip() or f"Local issue {issue_number}",
                body=body.strip(),
                yaml_path=str(target_yaml_path),
                directory_path=normalized_directory_path,
                managed_repo_id=resolved_managed_repo_id,
                source_type=source_type,
                remote_reference=remote_reference,
                state=(
                    state_value
                    if source_type == "remote_proxy"
                    else issue_helpers.normalize_local_issue_state(state_value, allow_empty=False)
                ),
                url=url_value,
                comments=comments_value,
                source=source_value,
                metadata=merged_metadata,
                created_at=existing_record.created_at if existing_record is not None else now,
                updated_at=now,
            )
            previous_managed_repo_id = (
                existing_record.managed_repo_id if existing_record is not None else None
            )
            self._bind_issue_repository(record)
            try:
                admin.local_issue_store.put(record.issue_id, record)
            except BaseException:
                if resolved_managed_repo_id != previous_managed_repo_id:
                    self._unbind_issue_repositories(record.issue_id)
                    if previous_managed_repo_id is not None:
                        previous = record.model_copy(
                            update={"managed_repo_id": previous_managed_repo_id}
                        )
                        self._bind_issue_repository(previous)
                raise
            if (
                previous_managed_repo_id is not None
                and previous_managed_repo_id != resolved_managed_repo_id
                and admin.managed_git_admin is not None
            ):
                admin.managed_git_admin.unbind(
                    previous_managed_repo_id,
                    kind="issues",
                    binding_id=record.issue_id,
                )
            return record

    def get_issue(
        self,
        issue_id: str,
        *,
        include_remote_comments: bool = True,
        sync_proxy_cache: bool = True,
    ) -> dict[str, object]:
        admin = self._deps
        issue = admin.resolve_local_issue_record(issue_id)
        base_payload = issue.model_dump(mode="json")
        proxy_target = admin.proxy_target_for_issue(issue)
        if proxy_target is None:
            return {
                **base_payload,
                "source_type": "local",
            }
        provider, remote_issue_id = proxy_target
        remote_payload = admin.issue_service.read_remote_issue(
            provider=provider,
            remote_issue_id=remote_issue_id,
            directory_path=issue.directory_path,
            project=_remote_project_for_issue(issue),
            include_comments=include_remote_comments,
        )
        if sync_proxy_cache:
            issue = admin.refresh_proxy_cache_from_remote(issue, remote_payload)
            base_payload = issue.model_dump(mode="json")
        return {
            **base_payload,
            "source_type": "proxy",
            "provider": provider,
            "remote_issue_id": remote_issue_id,
            **remote_payload,
        }

    def read_issue(
        self,
        issue_number: str,
        *,
        include_remote_comments: bool = True,
        sync_proxy_cache: bool = True,
    ) -> dict[str, object]:
        return self.get_issue(
            issue_number,
            include_remote_comments=include_remote_comments,
            sync_proxy_cache=sync_proxy_cache,
        )

    def add_issue_comment(
        self,
        issue_id: str,
        comment: str,
        *,
        sync_proxy_cache: bool = True,
    ) -> dict[str, object]:
        admin = self._deps
        normalized_comment = comment.strip()
        if not normalized_comment:
            raise ValueError("comment must be non-empty")
        issue = admin.resolve_local_issue_record(issue_id)
        proxy_target = admin.proxy_target_for_issue(issue)
        if proxy_target is None:
            issue.metadata = dict(issue.metadata)
            comments = list(issue.comments or issue.metadata.get("comments") or [])
            comments.append(
                {"text": normalized_comment, "created_at": session_helpers.utc_now().isoformat()}
            )
            issue.metadata["comments"] = comments
            issue.comments = comments
            issue.updated_at = session_helpers.utc_now()
            admin.local_issue_store.put(issue.issue_id, issue)
            return {
                "issue_number": issue.issue_number,
                "source_type": "local",
                "comment_added": True,
            }

        provider, remote_issue_id = proxy_target
        result = admin.issue_service.post_remote_comment(
            provider=provider,
            remote_issue_id=remote_issue_id,
            directory_path=issue.directory_path,
            project=_remote_project_for_issue(issue),
            comment=normalized_comment,
        )
        if sync_proxy_cache:
            remote_payload = admin.issue_service.read_remote_issue(
                provider=provider,
                remote_issue_id=remote_issue_id,
                directory_path=issue.directory_path,
                project=_remote_project_for_issue(issue),
                include_comments=True,
            )
            admin.refresh_proxy_cache_from_remote(issue, remote_payload)
        return {"issue_number": issue.issue_number, "source_type": "proxy", **result}

    def post_issue_comment(
        self,
        issue_number: str,
        comment: str,
        *,
        sync_proxy_cache: bool = True,
    ) -> dict[str, object]:
        return self.add_issue_comment(issue_number, comment, sync_proxy_cache=sync_proxy_cache)

    def set_issue_plan(
        self,
        issue_id: str,
        steps: list[str],
        summary: str | None = None,
    ) -> dict[str, object]:
        admin = self._deps
        normalized_steps = [str(step).strip() for step in steps if str(step).strip()]
        if not normalized_steps:
            raise ValueError("steps must contain at least one non-empty item")
        normalized_summary = str(summary or "").strip() or None

        issue = admin.resolve_local_issue_record(issue_id)
        issue.metadata = dict(issue.metadata)
        issue.metadata["steps"] = normalized_steps
        if normalized_summary is not None:
            issue.metadata["plan_summary"] = normalized_summary
        else:
            issue.metadata.pop("plan_summary", None)
        issue.updated_at = session_helpers.utc_now()
        admin.local_issue_store.put(issue.issue_id, issue)
        admin.write_local_issue_yaml(issue, updated_at=issue.updated_at)
        return {
            "issue_number": issue.issue_number,
            "source_type": "local",
            "updated": True,
            "steps": normalized_steps,
            "step_count": len(normalized_steps),
            "summary": normalized_summary,
        }

    def set_issue_state(
        self,
        issue_id: str,
        state: str,
        *,
        sync_proxy_cache: bool = True,
    ) -> dict[str, object]:
        return self.update_issue(
            issue_id,
            state=state,
            fields=None,
            sync_proxy_cache=sync_proxy_cache,
        )

    def update_issue(
        self,
        issue_id: str,
        *,
        state: str | None = None,
        fields: dict[str, object] | None = None,
        sync_proxy_cache: bool = True,
    ) -> dict[str, object]:
        admin = self._deps
        issue = admin.resolve_local_issue_record(issue_id)
        proxy_target = admin.proxy_target_for_issue(issue)
        if proxy_target is None:
            issue.metadata = dict(issue.metadata)
            if state is not None:
                normalized_state = issue_helpers.normalize_local_issue_state(
                    state, allow_empty=False
                )
                issue.metadata["state"] = normalized_state
                issue.state = normalized_state
            if fields:
                local_fields = dict(issue.metadata.get("fields") or {})
                local_fields.update(fields)
                issue.metadata["fields"] = local_fields
            issue.sync_derived_fields()
            issue.updated_at = session_helpers.utc_now()
            admin.local_issue_store.put(issue.issue_id, issue)
            admin.write_local_issue_yaml(issue, updated_at=issue.updated_at)
            return {"issue_number": issue.issue_number, "source_type": "local", "updated": True}

        provider, remote_issue_id = proxy_target
        result = admin.issue_service.update_remote_issue(
            provider=provider,
            remote_issue_id=remote_issue_id,
            directory_path=issue.directory_path,
            project=_remote_project_for_issue(issue),
            state=state,
            fields=fields,
        )
        if sync_proxy_cache:
            remote_payload = admin.issue_service.read_remote_issue(
                provider=provider,
                remote_issue_id=remote_issue_id,
                directory_path=issue.directory_path,
                project=_remote_project_for_issue(issue),
                include_comments=True,
            )
            admin.refresh_proxy_cache_from_remote(issue, remote_payload)
        return {
            "issue_number": issue.issue_number,
            "source_type": "proxy",
            **result,
            "directory_path": issue.directory_path,
        }

    def update_issue_metadata(
        self,
        *,
        issue_id: str,
        patch: Mapping[str, Any] | None = None,
        merge_patch: Mapping[str, Any] | None = None,
        expected_updated_at: str | None = None,
        sync_proxy_cache: bool = True,
    ) -> dict[str, object]:
        admin = self._deps
        if patch is None and merge_patch is None:
            raise ValueError("effect:issue:update-metadata requires patch or merge_patch")
        if patch is not None and merge_patch is not None:
            raise ValueError(
                "effect:issue:update-metadata patch and merge_patch are mutually exclusive"
            )
        if patch is not None and not isinstance(patch, Mapping):
            raise ValueError("effect:issue:update-metadata patch must be a JSON object")
        if merge_patch is not None and not isinstance(merge_patch, Mapping):
            raise ValueError("effect:issue:update-metadata merge_patch must be a JSON object")

        issue = admin.resolve_local_issue_record(issue_id)
        current_updated_at = issue.updated_at.isoformat()
        if expected_updated_at is not None and not _issue_updated_at_matches(
            issue, expected_updated_at
        ):
            raise RuntimeError(
                "effect:issue:update-metadata concurrency conflict for "
                f"{issue.issue_number}: expected updated_at {expected_updated_at}, "
                f"found {current_updated_at}"
            )

        if patch is not None:
            updated_metadata, touched_paths = _apply_issue_metadata_dot_patch(issue.metadata, patch)
            patch_kind = "dot_path"
        else:
            assert merge_patch is not None
            updated_metadata, touched_paths = _apply_json_merge_patch(issue.metadata, merge_patch)
            patch_kind = "json_merge_patch"

        issue.metadata = updated_metadata
        issue.sync_derived_fields()
        issue.updated_at = session_helpers.utc_now()
        admin.local_issue_store.put(issue.issue_id, issue)
        admin.write_local_issue_yaml(issue, updated_at=issue.updated_at)

        audit = getattr(getattr(admin, "runtime", None), "audit", None)
        log_decision = getattr(audit, "log_decision", None)
        if callable(log_decision):
            log_decision(
                event_type="issue_metadata_updated",
                decision="allowed",
                args={
                    "issue_id": issue.issue_id,
                    "issue_number": issue.issue_number,
                    "patch_kind": patch_kind,
                    "paths": touched_paths,
                },
                result={
                    "updated": True,
                    "updated_at": issue.updated_at.isoformat(),
                },
            )

        return {
            "success": True,
            "issue_number": issue.issue_number,
            "issue_id": issue.issue_id,
            "source_type": "proxy" if admin.proxy_target_for_issue(issue) is not None else "local",
            "updated": True,
            "updated_at": issue.updated_at.isoformat(),
            "metadata": dict(issue.metadata),
            "metadata_paths": touched_paths,
            "patch_kind": patch_kind,
        }

    def claim_next_issue(
        self,
        *,
        session_id: str,
    ) -> LocalIssueRecord | None:
        admin = self._deps
        session = self._session_repository().get_session(session_id)
        if session is None:
            raise ValueError(f"Unknown session: {session_id}")
        candidates = sorted(
            self.list_actionable_local_issues(
                directory_path=session.directory_path, states={"Committed"}
            ),
            key=issue_helpers.issue_priority_sort_key,
        )
        if not candidates:
            return None
        issue = candidates[0]
        issue.metadata = dict(issue.metadata)
        local_fields = issue_helpers.local_issue_fields(issue)
        local_fields["claimed_by_session_id"] = session_id
        if session.vm_id:
            local_fields["claimed_by_vm_id"] = session.vm_id
        local_fields["claimed_at"] = session_helpers.utc_now().isoformat()
        issue.metadata["fields"] = local_fields
        issue.state = "In Progress"
        issue.metadata["state"] = issue.state
        issue.updated_at = session_helpers.utc_now()
        admin.local_issue_store.put(issue.issue_id, issue)
        return issue

    def complete_issue(
        self,
        *,
        session_id: str,
        issue_id: str,
        summary: str,
    ) -> LocalIssueRecord:
        admin = self._deps
        session = self._session_repository().get_session(session_id)
        if session is None:
            raise ValueError(f"Unknown session: {session_id}")
        normalized_summary = summary.strip()
        if not normalized_summary:
            raise ValueError("summary must be non-empty")
        issue = admin.resolve_local_issue_record(issue_id)
        if admin.proxy_target_for_issue(issue) is not None:
            raise ValueError("Only local issues can be completed through the issue orchestrator.")
        if issue.state != "In Progress":
            raise ValueError(f"Issue {issue.issue_number} must be In Progress before completion.")
        issue.metadata = dict(issue.metadata)
        local_fields = issue_helpers.local_issue_fields(issue)
        local_fields["completed_summary"] = normalized_summary
        local_fields["completed_by_session_id"] = session_id
        if session.vm_id:
            local_fields["completed_by_vm_id"] = session.vm_id
        local_fields["completed_at"] = session_helpers.utc_now().isoformat()
        issue.metadata["fields"] = local_fields
        issue.state = "Completed"
        issue.metadata["state"] = issue.state
        issue.updated_at = session_helpers.utc_now()
        admin.local_issue_store.put(issue.issue_id, issue)
        return issue

    def delete_local_issue(self, issue_identifier: str) -> VmActionResult:
        admin = self._deps
        try:
            issue_key = admin.resolve_local_issue_key(issue_identifier)
        except ValueError as exc:
            return VmActionResult(
                success=False,
                vm_name=f"vm-{issue_identifier}",
                action="delete_local_issue",
                message=str(exc),
            )
        issue = admin.local_issue_store.get(issue_key) if issue_key is not None else None
        if issue is None:
            return VmActionResult(
                success=False,
                vm_name=f"vm-{issue_identifier}",
                action="delete_local_issue",
                message=f"Unknown local issue: {issue_identifier}",
            )
        issue_number = issue.issue_number

        vm_name = f"vm-{issue_number}"
        existing_vm_state = vm_helpers.dominfo_field(vm_name, "State")
        sessions = self._session_repository().read_all()
        blocking_sessions = [
            (session_id, session)
            for session_id, session in sessions.items()
            if _issue_session_matches_issue(
                session_id=session_id,
                session=session,
                issue_number=issue_number,
                vm_name=vm_name,
            )
        ]
        if existing_vm_state is not None or blocking_sessions:
            return VmActionResult(
                success=False,
                vm_name=vm_name,
                action="delete_local_issue",
                message=_local_issue_delete_blocker_message(
                    issue_number=issue_number,
                    vm_name=vm_name,
                    existing_vm_state=existing_vm_state,
                    blocking_sessions=blocking_sessions,
                ),
            )

        admin.local_issue_store.delete(issue.issue_id)
        self._unbind_issue_repositories(issue.issue_id)
        yaml_path = Path(issue.yaml_path)
        if yaml_path.exists():
            yaml_path.unlink()
        return VmActionResult(
            success=True,
            vm_name=vm_name,
            action="delete_local_issue",
            message=f"Deleted local issue {issue_number}",
        )

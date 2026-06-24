from __future__ import annotations

import base64
import copy
import fcntl
import json
import os
import re
import shutil
import tempfile
import urllib.parse
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from agent_sandbox.admin import service as _admin_service
from agent_sandbox.models import (
    ApprovalDecisionRecord,
    ApprovalQueueHealth,
    ApprovalRequest,
    ApprovalStatus,
    LocalIssueRecord,
    RemoteIssueReference,
    VmActionResult,
)
from agent_sandbox.repositories.session_manifests import as_session_manifest_repository
from agent_sandbox.admin.services.local_issue_identity import LocalIssueIdentityResolver


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
        metadata.get("execution_backend")
        or getattr(session, "vm_provider", None)
        or ""
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
    normalize_directory_path: Any
    local_issue_creation_lock: Any
    next_local_issue_number: Any
    canonical_local_issue_id: Any
    resolve_local_issue_record: Any
    resolve_local_issue_key: Any
    proxy_target_for_issue: Any
    refresh_proxy_cache_from_remote: Any
    write_local_issue_yaml: Any
    direct_owned_entity_ids: Any


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

    def canonical_local_issue_id(self, issue_number: str, directory_path: str) -> str:
        return _admin_service._scoped_issue_id(
            issue_number, directory_path=directory_path, provider="local"
        )

    def canonical_issue_id(self, record: LocalIssueRecord) -> str:
        remote = record.remote_reference
        if remote is not None and remote.provider == "azure_devops" and remote.work_item_id:
            return _admin_service._scoped_issue_id(
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
        return self.issue_service.resolve_proxy_target(
            issue_number=issue.issue_number,
            issue_source=issue.source,
            metadata=issue.metadata,
        )

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
            issue.remote_reference.last_synced_at = _admin_service._utc_now()
            issue.remote_reference.last_known_remote_state = issue.state
            issue.remote_reference.work_item_url = issue.url or issue.remote_reference.work_item_url
        issue.updated_at = _admin_service._utc_now()
        self.local_issue_store.put(issue.issue_id, issue)
        return issue

    def write_local_issue_yaml(
        self, issue: LocalIssueRecord, *, updated_at: datetime | None = None
    ) -> None:
        yaml_path = Path(issue.yaml_path)
        payload: dict[str, object] = {}
        if yaml_path.exists():
            try:
                loaded = _admin_service.yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
            except Exception:
                loaded = {}
            if isinstance(loaded, dict):
                payload = dict(loaded)
        payload["issue_number"] = issue.issue_number
        payload["title"] = issue.title
        payload["body"] = issue.body
        payload["state"] = issue.state
        payload["source"] = issue.source or "local_gui"
        fields = _admin_service._local_issue_fields(issue)
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
        yaml_path.write_text(
            _admin_service.yaml.safe_dump(payload, sort_keys=False), encoding="utf-8"
        )

    def direct_owned_entity_ids(self, directory_path: str, *, kind: str) -> set[str]:
        snapshot = self.directory_ownership.list_entities(directory_path, include_descendants=False)
        return {entity.entity_id for entity in snapshot.entities if entity.kind == kind}


class IssueGitAdminService:
    """Issue, approval, git, and PR operations owned by the admin service."""

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

    @staticmethod
    def _derive_review_branch(agent_branch: str) -> str:
        normalized = str(agent_branch).strip()
        if normalized.startswith("agents/") and normalized.count("/") >= 2:
            return f"review/{normalized.removeprefix('agents/')}"
        return f"review/{normalized.removeprefix('refs/heads/')}"

    @staticmethod
    def _promoted_commit_from_output(*outputs: str) -> str | None:
        for output in outputs:
            match = re.search(r"^Commit:\s*([0-9a-fA-F]{7,40})\s*$", output, re.MULTILINE)
            if match:
                return match.group(1)
        return None

    @staticmethod
    def _derive_local_pr_notes_ref(remote_url: str) -> str:
        repo_name = Path(remote_url.strip()).name
        if repo_name.endswith(".git"):
            repo_name = repo_name[:-4]
        repo_name = re.sub(r"[^a-z0-9._-]+", "-", repo_name.lower()).strip("-") or "bare-repo"
        return f"refs/notes/agent-pr-cache/{repo_name}"

    @staticmethod
    def _normalize_branch_ref(value: str | None) -> str:
        text = str(value or "").strip()
        for prefix in ("refs/heads/", "refs/head/", "refs/remotes/origin/", "origin/"):
            if text.startswith(prefix):
                return text.removeprefix(prefix)
        return text

    @staticmethod
    def _is_safe_branch_name(value: str) -> bool:
        if not value or value in {"@", ".", ".."}:
            return False
        if value.startswith(("-", "/", "refs/")) or value.endswith(("/", ".")):
            return False
        if ".." in value or "//" in value or "@{" in value:
            return False
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            return False
        if any(char in value for char in " ~^:?*[\\"):
            return False
        return all(
            part and not part.startswith(".") and not part.endswith(".lock")
            for part in value.split("/")
        )

    @staticmethod
    def _infer_git_provider(origin_url: str) -> str:
        if origin_url.startswith(("git@github.com:", "https://github.com/")):
            return "github"
        if origin_url.startswith(("git@ssh.dev.azure.com:", "https://dev.azure.com/")):
            return "azure_devops"
        if re.match(r"^https://[^.]+\.visualstudio\.com/", origin_url):
            return "azure_devops"
        return ""

    @staticmethod
    def _azure_https_origin_url(origin_url: str) -> str:
        match = (
            re.match(
                r"^git@ssh\.dev\.azure\.com:v3/([^/]+)/([^/]+)/([^/]+)$",
                origin_url,
            )
            or re.match(
                r"^https://(?:[^@/]+@)?dev\.azure\.com/([^/]+)/([^/]+)/_git/([^/]+)$",
                origin_url,
            )
            or re.match(
                r"^https://(?:[^@/]+@)?([^.]+)\.visualstudio\.com/([^/]+)/_git/([^/]+)$",
                origin_url,
            )
        )
        if not match:
            return ""
        organization, project, repo = (urllib.parse.unquote(part) for part in match.groups())
        return (
            "https://dev.azure.com/"
            f"{urllib.parse.quote(organization, safe='')}/"
            f"{urllib.parse.quote(project, safe='')}/_git/"
            f"{urllib.parse.quote(repo, safe='')}"
        )

    @staticmethod
    def _github_https_origin_url(origin_url: str) -> str:
        match = re.match(r"^git@github\.com:([^/]+/[^/]+?)(?:\.git)?$", origin_url) or re.match(
            r"^https://github\.com/([^/]+/[^/]+?)(?:\.git)?$",
            origin_url,
        )
        if not match:
            return ""
        return f"https://github.com/{match.group(1)}.git"

    @staticmethod
    def _combined_output(stdout: str, stderr: str) -> str:
        return "\n".join(part.strip() for part in (stderr, stdout) if part.strip())

    def _git_push_auth_prefix(
        self,
        *,
        provider: str,
        origin_url: str,
    ) -> tuple[list[str], str, bool]:
        push_remote_url = origin_url
        git_config_args: list[str] = []
        used_credential_helper = False

        if provider == "azure_devops":
            pat_env = (
                os.environ.get("AGENT_SANDBOX_AZURE_DEVOPS_PR_PAT_ENV")
                or getattr(self._deps.config, "azure_devops_pr_pat_env", "")
                or "AZURE_DEVOPS_PR_PAT"
            )
            pat_value = os.environ.get(pat_env, "").strip()
            https_origin_url = self._azure_https_origin_url(origin_url)
            if pat_value and https_origin_url:
                push_remote_url = https_origin_url
                auth_value = base64.b64encode(f":{pat_value}".encode("utf-8")).decode("ascii")
                git_config_args.extend(
                    ["-c", f"http.extraHeader=Authorization: Basic {auth_value}"]
                )
        elif provider == "github":
            https_origin_url = self._github_https_origin_url(origin_url)
            gh_status = None
            if shutil.which("gh"):
                gh_status = _admin_service._run_subprocess(
                    ["gh", "auth", "status"],
                    capture_output=True,
                    text=True,
                )
            if gh_status is not None and gh_status.returncode == 0 and https_origin_url:
                push_remote_url = https_origin_url
                git_config_args.extend(
                    [
                        "-c",
                        "credential.https://github.com.helper=!/usr/bin/gh auth git-credential",
                    ]
                )
                used_credential_helper = True

        return git_config_args, push_remote_url, used_credential_helper

    def _run_git(
        self,
        host_repo_path: str,
        git_config_args: list[str],
        args: list[str],
    ):
        return _admin_service._run_subprocess(
            ["git", "-C", host_repo_path, *git_config_args, *args],
            capture_output=True,
            text=True,
        )

    def _push_branch_failure_payload(
        self,
        *,
        reason: str,
        message: str,
        host_repo_path: str,
        local_branch: str,
        origin_branch: str,
        base_branch: str,
        session_id: str,
        work_item_id: str,
        remote_name: str,
        destination_commit: str = "",
        local_commit: str = "",
        destination_ahead_count: int = 0,
        local_ahead_count: int = 0,
        stdout: str = "",
        stderr: str = "",
    ) -> dict[str, Any]:
        return {
            "success": False,
            "action": "git_push_branch",
            "preflight_success": False,
            "reason": reason,
            "message": message,
            "host_repo_path": host_repo_path,
            "local_branch": local_branch,
            "origin_branch": origin_branch,
            "base_branch": base_branch,
            "session_id": session_id,
            "work_item_id": work_item_id,
            "remote_name": remote_name,
            "destination_ref": f"refs/heads/{origin_branch}",
            "destination_tracking_ref": f"refs/remotes/{remote_name}/{origin_branch}",
            "destination_commit": destination_commit,
            "local_commit": local_commit,
            "destination_ahead_count": destination_ahead_count,
            "local_ahead_count": local_ahead_count,
            "stdout": stdout,
            "stderr": stderr,
        }

    def push_branch(
        self,
        *,
        host_repo_path: str,
        local_branch: str,
        origin_branch: str,
        canonical_origin_url: str | None = None,
        provider: str | None = None,
        base_branch: str | None = None,
        session_id: str | None = None,
        work_item_id: str | None = None,
    ) -> dict[str, Any]:
        normalized_local_branch = self._normalize_branch_ref(local_branch)
        normalized_origin_branch = self._normalize_branch_ref(origin_branch)
        normalized_base_branch = self._normalize_branch_ref(base_branch)
        normalized_session_id = str(session_id or "").strip()
        normalized_work_item_id = str(work_item_id or "").strip()
        if not normalized_local_branch:
            raise ValueError("push_branch requires local_branch")
        if not normalized_origin_branch:
            raise ValueError("push_branch requires origin_branch")
        for branch_field, branch_name in (
            ("local_branch", normalized_local_branch),
            ("origin_branch", normalized_origin_branch),
            ("base_branch", normalized_base_branch),
        ):
            if branch_name and not self._is_safe_branch_name(branch_name):
                raise ValueError(f"push_branch requires a valid {branch_field}: {branch_name}")

        repo_path = Path(host_repo_path)
        if not (repo_path / ".git").exists():
            raise ValueError(f"Host repo path is not a Git working tree: {host_repo_path}")

        origin_url = str(canonical_origin_url or "").strip()
        remote_name = "agent-sandbox-pr-origin" if origin_url else "origin"
        if not origin_url:
            origin_result = _admin_service._run_subprocess(
                ["git", "-C", host_repo_path, "remote", "get-url", "origin"],
                capture_output=True,
                text=True,
            )
            if origin_result.returncode != 0:
                raise RuntimeError(
                    self._combined_output(origin_result.stdout or "", origin_result.stderr or "")
                    or "Failed to resolve origin remote URL"
                )
            origin_url = (origin_result.stdout or "").strip()

        push_provider = str(provider or "auto").strip() or "auto"
        if push_provider == "auto":
            push_provider = self._infer_git_provider(origin_url)

        git_config_args, push_remote_url, used_credential_helper = self._git_push_auth_prefix(
            provider=push_provider,
            origin_url=origin_url,
        )
        if remote_name == "origin" and push_remote_url != origin_url:
            remote_name = "agent-sandbox-pr-origin"

        if remote_name != "origin":
            current_result = _admin_service._run_subprocess(
                ["git", "-C", host_repo_path, "remote", "get-url", remote_name],
                capture_output=True,
                text=True,
            )
            if current_result.returncode == 0:
                if (current_result.stdout or "").strip() != push_remote_url:
                    set_result = _admin_service._run_subprocess(
                        [
                            "git",
                            "-C",
                            host_repo_path,
                            "remote",
                            "set-url",
                            remote_name,
                            push_remote_url,
                        ],
                        capture_output=True,
                        text=True,
                    )
                    if set_result.returncode != 0:
                        raise RuntimeError(
                            self._combined_output(
                                set_result.stdout or "",
                                set_result.stderr or "",
                            )
                            or f"Failed to update remote {remote_name}"
                        )
            else:
                add_result = _admin_service._run_subprocess(
                    ["git", "-C", host_repo_path, "remote", "add", remote_name, push_remote_url],
                    capture_output=True,
                    text=True,
                )
                if add_result.returncode != 0:
                    raise RuntimeError(
                        self._combined_output(add_result.stdout or "", add_result.stderr or "")
                        or f"Failed to add remote {remote_name}"
                    )

        local_rev_result = _admin_service._run_subprocess(
            [
                "git",
                "-C",
                host_repo_path,
                "rev-parse",
                "--verify",
                f"{normalized_local_branch}^{{commit}}",
            ],
            capture_output=True,
            text=True,
        )
        local_commit = (local_rev_result.stdout or "").strip()
        if local_rev_result.returncode != 0:
            message = (
                self._combined_output(local_rev_result.stdout or "", local_rev_result.stderr or "")
                or f"Failed to resolve local branch {normalized_local_branch}"
            )
            return self._push_branch_failure_payload(
                reason="local_branch_missing",
                message=message,
                host_repo_path=host_repo_path,
                local_branch=normalized_local_branch,
                origin_branch=normalized_origin_branch,
                base_branch=normalized_base_branch,
                session_id=normalized_session_id,
                work_item_id=normalized_work_item_id,
                remote_name=remote_name,
                local_commit=local_commit,
                stdout=local_rev_result.stdout or "",
                stderr=local_rev_result.stderr or "",
            )

        ls_result = self._run_git(
            host_repo_path,
            git_config_args,
            ["ls-remote", "--exit-code", remote_name, f"refs/heads/{normalized_origin_branch}"],
        )
        destination_tracking_ref = f"refs/remotes/{remote_name}/{normalized_origin_branch}"
        destination_commit = ""
        destination_exists = ls_result.returncode == 0
        if ls_result.returncode not in {0, 2}:
            message = (
                self._combined_output(ls_result.stdout or "", ls_result.stderr or "")
                or f"Could not verify destination branch {remote_name}/{normalized_origin_branch}"
            )
            return self._push_branch_failure_payload(
                reason="preflight_failed",
                message=message,
                host_repo_path=host_repo_path,
                local_branch=normalized_local_branch,
                origin_branch=normalized_origin_branch,
                base_branch=normalized_base_branch,
                session_id=normalized_session_id,
                work_item_id=normalized_work_item_id,
                remote_name=remote_name,
                local_commit=local_commit,
                stdout=ls_result.stdout or "",
                stderr=ls_result.stderr or "",
            )

        if destination_exists:
            fetch_result = self._run_git(
                host_repo_path,
                git_config_args,
                [
                    "fetch",
                    remote_name,
                    f"+refs/heads/{normalized_origin_branch}:{destination_tracking_ref}",
                ],
            )
            if fetch_result.returncode != 0:
                message = (
                    self._combined_output(fetch_result.stdout or "", fetch_result.stderr or "")
                    or f"Could not refresh destination branch {remote_name}/{normalized_origin_branch}"
                )
                return self._push_branch_failure_payload(
                    reason="preflight_failed",
                    message=message,
                    host_repo_path=host_repo_path,
                    local_branch=normalized_local_branch,
                    origin_branch=normalized_origin_branch,
                    base_branch=normalized_base_branch,
                    session_id=normalized_session_id,
                    work_item_id=normalized_work_item_id,
                    remote_name=remote_name,
                    local_commit=local_commit,
                    stdout=fetch_result.stdout or "",
                    stderr=fetch_result.stderr or "",
                )

            destination_rev_result = _admin_service._run_subprocess(
                [
                    "git",
                    "-C",
                    host_repo_path,
                    "rev-parse",
                    "--verify",
                    f"{destination_tracking_ref}^{{commit}}",
                ],
                capture_output=True,
                text=True,
            )
            destination_commit = (destination_rev_result.stdout or "").strip()
            merge_base_result = _admin_service._run_subprocess(
                [
                    "git",
                    "-C",
                    host_repo_path,
                    "merge-base",
                    "--is-ancestor",
                    destination_tracking_ref,
                    normalized_local_branch,
                ],
                capture_output=True,
                text=True,
            )
            if merge_base_result.returncode not in {0, 1}:
                message = self._combined_output(
                    merge_base_result.stdout or "",
                    merge_base_result.stderr or "",
                ) or (
                    f"Could not compare {normalized_local_branch} with "
                    f"{remote_name}/{normalized_origin_branch}."
                )
                return self._push_branch_failure_payload(
                    reason="preflight_failed",
                    message=message,
                    host_repo_path=host_repo_path,
                    local_branch=normalized_local_branch,
                    origin_branch=normalized_origin_branch,
                    base_branch=normalized_base_branch,
                    session_id=normalized_session_id,
                    work_item_id=normalized_work_item_id,
                    remote_name=remote_name,
                    destination_commit=destination_commit,
                    local_commit=local_commit,
                    stdout=merge_base_result.stdout or "",
                    stderr=merge_base_result.stderr or "",
                )
            if merge_base_result.returncode == 1:
                counts_result = _admin_service._run_subprocess(
                    [
                        "git",
                        "-C",
                        host_repo_path,
                        "rev-list",
                        "--left-right",
                        "--count",
                        f"{destination_tracking_ref}...{normalized_local_branch}",
                    ],
                    capture_output=True,
                    text=True,
                )
                destination_ahead_count = 0
                local_ahead_count = 0
                if counts_result.returncode == 0:
                    parts = (counts_result.stdout or "").strip().split()
                    if len(parts) >= 2:
                        try:
                            destination_ahead_count = int(parts[0])
                            local_ahead_count = int(parts[1])
                        except ValueError:
                            destination_ahead_count = 0
                            local_ahead_count = 0
                return self._push_branch_failure_payload(
                    reason="destination_branch_ahead",
                    message=(
                        f"Destination branch {remote_name}/{normalized_origin_branch} has "
                        f"commits that are not contained in {normalized_local_branch}; merge "
                        "the destination branch before pushing."
                    ),
                    host_repo_path=host_repo_path,
                    local_branch=normalized_local_branch,
                    origin_branch=normalized_origin_branch,
                    base_branch=normalized_base_branch,
                    session_id=normalized_session_id,
                    work_item_id=normalized_work_item_id,
                    remote_name=remote_name,
                    destination_commit=destination_commit,
                    local_commit=local_commit,
                    destination_ahead_count=destination_ahead_count,
                    local_ahead_count=local_ahead_count,
                    stdout=counts_result.stdout or "",
                    stderr=counts_result.stderr or "",
                )

        push_args = ["push"]
        if normalized_origin_branch.startswith("review/"):
            push_args.append("--force-with-lease")
        push_args.extend(
            [
                remote_name,
                f"{normalized_local_branch}:refs/heads/{normalized_origin_branch}",
            ]
        )
        push_result = self._run_git(host_repo_path, git_config_args, push_args)
        push_stdout = push_result.stdout or ""
        push_stderr = push_result.stderr or ""
        if push_result.returncode != 0:
            message = (
                self._combined_output(push_stdout, push_stderr)
                or f"git push exited with code {push_result.returncode}"
            )
            return self._push_branch_failure_payload(
                reason="push_failed",
                message=message,
                host_repo_path=host_repo_path,
                local_branch=normalized_local_branch,
                origin_branch=normalized_origin_branch,
                base_branch=normalized_base_branch,
                session_id=normalized_session_id,
                work_item_id=normalized_work_item_id,
                remote_name=remote_name,
                destination_commit=destination_commit,
                local_commit=local_commit,
                stdout=push_stdout,
                stderr=push_stderr,
            )

        message = (
            self._combined_output(push_stdout, push_stderr)
            or f"Pushed {normalized_local_branch} to {remote_name}/{normalized_origin_branch}"
        )
        return {
            "success": True,
            "action": "git_push_branch",
            "preflight_success": True,
            "reason": "",
            "message": message,
            "host_repo_path": host_repo_path,
            "local_branch": normalized_local_branch,
            "origin_branch": normalized_origin_branch,
            "base_branch": normalized_base_branch,
            "session_id": normalized_session_id,
            "work_item_id": normalized_work_item_id,
            "remote_name": remote_name,
            "destination_ref": f"refs/heads/{normalized_origin_branch}",
            "destination_tracking_ref": destination_tracking_ref,
            "destination_commit": destination_commit,
            "local_commit": local_commit,
            "destination_ahead_count": 0,
            "local_ahead_count": 0,
            "provider": push_provider,
            "push_remote_url": push_remote_url,
            "used_credential_helper": used_credential_helper,
            "stdout": push_stdout,
            "stderr": push_stderr,
        }

    def pr_create(
        self,
        *,
        host_repo_path: str,
        local_branch: str | None = None,
        origin_branch: str | None = None,
        canonical_origin_url: str | None = None,
        provider: str | None = None,
        project: str | None = None,
        base_branch: str | None = None,
        session_id: str | None = None,
        work_item_id: str | None = None,
        title: str | None = None,
        body: str | None = None,
        body_file: str | None = None,
        body_artifact_ids: list[str] | None = None,
        draft: bool | None = None,
        fill: bool = True,
    ) -> dict[str, Any]:
        if body and body_file:
            raise ValueError("Provide either body or body_file, not both")

        admin = self._deps
        script_path = admin.project_dir / "scripts" / "open_agent_pr.sh"
        cmd = [str(script_path), "--create-pr"]
        if draft is True:
            cmd.append("--draft")
        elif draft is False:
            cmd.append("--no-draft")
        if provider:
            cmd.extend(["--provider", provider])
        if project:
            cmd.extend(["--project", project])
        if base_branch:
            cmd.extend(["--base", base_branch])
        if session_id:
            cmd.extend(["--session-id", session_id])
        if work_item_id:
            cmd.extend(["--work-item-id", work_item_id])
        if title:
            cmd.extend(["--title", title])
        for artifact_id in body_artifact_ids or []:
            normalized_artifact_id = str(artifact_id or "").strip()
            if normalized_artifact_id:
                cmd.extend(["--body-artifact-id", normalized_artifact_id])
        if not fill:
            cmd.append("--no-fill")

        body_file_path: Path | None = None
        try:
            if body is not None:
                tmp = tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".md", delete=False)
                with tmp:
                    tmp.write(body)
                body_file_path = Path(tmp.name)
                cmd.extend(["--body-file", str(body_file_path)])
            elif body_file:
                cmd.extend(["--body-file", body_file])

            cmd.append(host_repo_path)
            if local_branch:
                cmd.append(local_branch)
            if origin_branch:
                cmd.append(origin_branch)

            env = os.environ.copy()
            if canonical_origin_url:
                env["AGENT_SANDBOX_CANONICAL_ORIGIN_URL"] = canonical_origin_url

            completed = _admin_service._run_subprocess(cmd, capture_output=True, text=True, env=env)
            stdout = completed.stdout or ""
            stderr = completed.stderr or ""
            if completed.returncode != 0:
                raise RuntimeError(
                    (stderr or stdout).strip()
                    or f"open_agent_pr.sh exited with code {completed.returncode}"
                )

            result_payload: dict[str, Any] = {}
            for line in stdout.splitlines():
                if not line.startswith("AGENT_PR_RESULT "):
                    continue
                raw_json = line.removeprefix("AGENT_PR_RESULT ").strip()
                parsed = json.loads(raw_json)
                if isinstance(parsed, dict):
                    result_payload = parsed
                    break

            message = (stderr or stdout).strip() or "PR prepared"
            return {
                "success": True,
                "action": "pr_create",
                "message": message,
                "stdout": stdout,
                "stderr": stderr,
                **result_payload,
            }
        finally:
            if body_file_path is not None:
                body_file_path.unlink(missing_ok=True)

    def promote_agent_branch(
        self,
        *,
        host_repo_path: str,
        bare_repo_path: str | None = None,
        bare_repo_name: str | None = None,
        agent_branch: str,
        review_branch: str | None = None,
        target_branch: str | None = None,
        remote_name: str | None = None,
    ) -> dict[str, Any]:
        if not bare_repo_path and not bare_repo_name:
            raise ValueError("Provide either bare_repo_path or bare_repo_name")
        if bare_repo_path and bare_repo_name:
            raise ValueError("Provide either bare_repo_path or bare_repo_name, not both")

        admin = self._deps
        script_path = admin.project_dir / "scripts" / "promote_agent_branch.sh"
        cmd = [str(script_path)]
        if remote_name:
            cmd.extend(["--remote-name", remote_name])
        cmd.append(host_repo_path)
        cmd.append(bare_repo_path or bare_repo_name or "")
        cmd.append(agent_branch)
        explicit_target_branch = target_branch or review_branch
        if explicit_target_branch:
            cmd.append(explicit_target_branch)

        completed = _admin_service._run_subprocess(cmd, capture_output=True, text=True)
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        if completed.returncode != 0:
            raise RuntimeError(
                (stderr or stdout).strip()
                or f"promote_agent_branch.sh exited with code {completed.returncode}"
            )

        resolved_target_branch = explicit_target_branch or self._derive_review_branch(agent_branch)
        commit_sha = self._promoted_commit_from_output(stdout, stderr)
        message = (stderr or stdout).strip() or "Agent branch promoted"
        return {
            "success": True,
            "action": "git_promote_agent_branch",
            "message": message,
            "stdout": stdout,
            "stderr": stderr,
            "host_repo_path": host_repo_path,
            "bare_repo_path": bare_repo_path or bare_repo_name,
            "agent_branch": agent_branch,
            "review_branch": resolved_target_branch,
            "target_branch": resolved_target_branch,
            "commit_sha": commit_sha,
            "remote_name": remote_name or "agent-dropbox-auto",
        }

    def git_pr_note_query(
        self,
        *,
        host_repo_path: str,
        local_branch: str,
        remote_name: str | None = None,
        notes_ref: str | None = None,
    ) -> dict[str, Any]:
        resolved_remote_name = (remote_name or "").strip() or "agent-dropbox-auto"
        resolved_notes_ref = (notes_ref or "").strip() or "refs/notes/agent-pr"

        remote_url_result = _admin_service._run_subprocess(
            ["git", "-C", host_repo_path, "remote", "get-url", resolved_remote_name],
            capture_output=True,
            text=True,
        )
        if remote_url_result.returncode != 0:
            raise RuntimeError(
                (remote_url_result.stderr or remote_url_result.stdout or "").strip()
                or f"Failed to resolve remote URL for {resolved_remote_name}"
            )
        remote_url = (remote_url_result.stdout or "").strip()
        local_notes_ref = self._derive_local_pr_notes_ref(remote_url)

        fetch_result = _admin_service._run_subprocess(
            [
                "git",
                "-C",
                host_repo_path,
                "fetch",
                resolved_remote_name,
                f"+{resolved_notes_ref}:{local_notes_ref}",
            ],
            capture_output=True,
            text=True,
        )
        if fetch_result.returncode != 0:
            return {
                "body": None,
                "has_note": False,
                "local_branch": local_branch,
                "host_repo_path": host_repo_path,
                "notes_ref": resolved_notes_ref,
                "local_notes_ref": local_notes_ref,
                "remote_name": resolved_remote_name,
                "commit_sha": None,
            }

        rev_parse_result = _admin_service._run_subprocess(
            ["git", "-C", host_repo_path, "rev-parse", local_branch],
            capture_output=True,
            text=True,
        )
        if rev_parse_result.returncode != 0:
            raise RuntimeError(
                (rev_parse_result.stderr or rev_parse_result.stdout or "").strip()
                or f"Failed to resolve branch {local_branch}"
            )
        commit_sha = (rev_parse_result.stdout or "").strip()

        def read_note(target_sha: str) -> str | None:
            note_result = _admin_service._run_subprocess(
                [
                    "git",
                    "-C",
                    host_repo_path,
                    "notes",
                    f"--ref={local_notes_ref}",
                    "show",
                    target_sha,
                ],
                capture_output=True,
                text=True,
            )
            if note_result.returncode != 0:
                return None
            return (note_result.stdout or "").strip() or None

        body = read_note(commit_sha)
        if body is None:
            rev_list_result = _admin_service._run_subprocess(
                [
                    "git",
                    "-C",
                    host_repo_path,
                    "rev-list",
                    "--first-parent",
                    "--max-count=25",
                    local_branch,
                ],
                capture_output=True,
                text=True,
            )
            if rev_list_result.returncode == 0:
                for candidate_sha in (
                    line.strip() for line in (rev_list_result.stdout or "").splitlines()
                ):
                    if not candidate_sha or candidate_sha == commit_sha:
                        continue
                    body = read_note(candidate_sha)
                    if body is not None:
                        break

        has_note = body is not None
        return {
            "body": body,
            "has_note": has_note,
            "local_branch": local_branch,
            "host_repo_path": host_repo_path,
            "notes_ref": resolved_notes_ref,
            "local_notes_ref": local_notes_ref,
            "remote_name": resolved_remote_name,
            "commit_sha": commit_sha,
        }

    def session_update_metadata(
        self, *, target: str, metadata: Mapping[str, Any]
    ) -> dict[str, Any]:
        session_repository = self._session_repository()
        session_id, manifest = self._resolve_session_target(target)
        merged_metadata = _admin_service._deep_merge_mapping(manifest.metadata, metadata)
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

    def session_context_query(self, *, target: str) -> dict[str, Any]:
        session_id, manifest = self._resolve_session_target(target)
        return {
            "session_id": session_id,
            "vm_name": manifest.vm_id,
            "directory_path": manifest.directory_path,
            "metadata": dict(manifest.metadata),
            "tool_summary": self._deps.runtime.policy.allowed_tool_summary_for_session(manifest),
            "created_at": manifest.created_at.isoformat(),
            "expires_at": manifest.expires_at.isoformat(),
            "model": manifest.model,
        }

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
        normalized_directory_path = admin.normalize_directory_path(directory_path)
        normalized_states = {value.strip() for value in (states or set()) if value.strip()} or None
        normalized_excluded_states = {
            value.strip() for value in (exclude_states or set()) if value.strip()
        } or None
        issues = sorted(
            admin.local_issue_store.read_all().values(), key=_admin_service._issue_number_sort_key
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

    def _refresh_approval_request(self, request: ApprovalRequest) -> ApprovalRequest:
        admin = self._deps
        session = self._session_repository().get_session(request.session_id)
        mutated = False
        if not request.directory_path and session is not None:
            request.directory_path = session.directory_path
            mutated = True
        if request.vm_id is None and session is not None and session.vm_id:
            request.vm_id = session.vm_id
            mutated = True
        if not request.tool_name:
            request.tool_name = request.action_type
            mutated = True
        if (
            request.status in {ApprovalStatus.PENDING, ApprovalStatus.APPROVED}
            and request.expires_at <= _admin_service._utc_now()
        ):
            request.status = ApprovalStatus.EXPIRED
            mutated = True
        if mutated:
            admin.runtime.approvals.put(request.request_id, request)
        return request

    def _approval_matches_filters(
        self,
        request: ApprovalRequest,
        *,
        statuses: set[ApprovalStatus] | None = None,
        directory_path: str | None = None,
        direct_only: bool = False,
        session_id: str | None = None,
        vm_id: str | None = None,
        tool_name: str | None = None,
        min_age_seconds: int | None = None,
    ) -> bool:
        admin = self._deps
        if statuses is not None and request.status not in statuses:
            return False
        if directory_path is not None:
            normalized_directory_path = admin.normalize_directory_path(directory_path)
            request_directory_path = admin.normalize_directory_path(request.directory_path)
            if not _admin_service._directory_path_matches_scope(
                request_directory_path,
                normalized_directory_path,
                direct_only=direct_only,
            ):
                return False
        if session_id is not None and request.session_id != session_id.strip():
            return False
        if vm_id is not None and (request.vm_id or "") != vm_id.strip():
            return False
        if (
            tool_name is not None
            and (request.tool_name or request.action_type) != tool_name.strip()
        ):
            return False
        if min_age_seconds is not None:
            age_seconds = int((_admin_service._utc_now() - request.created_at).total_seconds())
            if age_seconds < min_age_seconds:
                return False
        return True

    def list_approval_requests(
        self,
        *,
        statuses: set[ApprovalStatus] | None = None,
        directory_path: str | None = None,
        direct_only: bool = False,
        session_id: str | None = None,
        vm_id: str | None = None,
        tool_name: str | None = None,
        min_age_seconds: int | None = None,
    ) -> list[ApprovalRequest]:
        admin = self._deps
        requests = [
            self._refresh_approval_request(request)
            for request in admin.runtime.approvals.read_all().values()
        ]
        filtered = [
            request
            for request in requests
            if self._approval_matches_filters(
                request,
                statuses=statuses,
                directory_path=directory_path,
                direct_only=direct_only,
                session_id=session_id,
                vm_id=vm_id,
                tool_name=tool_name,
                min_age_seconds=min_age_seconds,
            )
        ]
        return sorted(filtered, key=_admin_service._approval_sort_key)

    def approval_queue_health(
        self,
        *,
        statuses: set[ApprovalStatus] | None = None,
        directory_path: str | None = None,
        direct_only: bool = False,
        session_id: str | None = None,
        vm_id: str | None = None,
        tool_name: str | None = None,
        min_age_seconds: int | None = None,
    ) -> ApprovalQueueHealth:
        requests = self.list_approval_requests(
            statuses=statuses,
            directory_path=directory_path,
            direct_only=direct_only,
            session_id=session_id,
            vm_id=vm_id,
            tool_name=tool_name,
            min_age_seconds=min_age_seconds,
        )
        health = ApprovalQueueHealth(total=len(requests))
        pending_ages: list[int] = []
        for request in requests:
            if request.status == ApprovalStatus.PENDING:
                health.pending += 1
                pending_ages.append(
                    int((_admin_service._utc_now() - request.created_at).total_seconds())
                )
            elif request.status == ApprovalStatus.APPROVED:
                health.approved += 1
            elif request.status == ApprovalStatus.DENIED:
                health.denied += 1
            elif request.status == ApprovalStatus.EXPIRED:
                health.expired += 1
            elif request.status == ApprovalStatus.EXHAUSTED:
                health.exhausted += 1
        if pending_ages:
            health.oldest_pending_age_seconds = max(pending_ages)
        return health

    def get_approval_request(self, request_id: str) -> ApprovalRequest:
        request = self._deps.runtime.approvals.get(request_id)
        if request is None:
            raise ValueError(f"Unknown approval request: {request_id}")
        return self._refresh_approval_request(request)

    def _approval_audit_context(
        self, request: ApprovalRequest, *, limit: int = 8
    ) -> list[dict[str, Any]]:
        admin = self._deps
        if not admin.config.audit_log_path.exists():
            return []
        try:
            lines = admin.config.audit_log_path.read_text(encoding="utf-8").splitlines()
        except Exception:
            return []
        matches: list[dict[str, Any]] = []
        for raw_line in reversed(lines):
            try:
                payload = json.loads(raw_line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict) and _admin_service._approval_audit_payload_matches_request(
                payload, request
            ):
                matches.append(payload)
            if len(matches) >= limit:
                break
        matches.reverse()
        return matches

    def get_approval_review(self, request_id: str) -> dict[str, Any]:
        request = self.get_approval_request(request_id)
        return _admin_service.build_approval_review_payload(
            request, self._approval_audit_context(request)
        )

    def decide_approval_request(
        self,
        request_id: str,
        *,
        approver: str,
        approve: bool,
        reason: str,
    ) -> ApprovalRequest:
        admin = self._deps
        request = self.get_approval_request(request_id)
        normalized_approver = approver.strip()
        normalized_reason = reason.strip()
        if not normalized_approver:
            raise ValueError("approver is required")
        if not normalized_reason:
            raise ValueError("reason is required")
        if request.status != ApprovalStatus.PENDING:
            raise ValueError(f"Approval request {request_id} is not pending")
        decision_status = ApprovalStatus.APPROVED if approve else ApprovalStatus.DENIED
        decided_at = _admin_service._utc_now()
        request.status = decision_status
        request.approver = normalized_approver
        request.decision_at = decided_at
        request.decision_reason = normalized_reason
        request.decision_history.append(
            ApprovalDecisionRecord(
                status=decision_status,
                approver=normalized_approver,
                reason=normalized_reason,
                decided_at=decided_at,
            )
        )
        admin.runtime.approvals.put(request.request_id, request)
        admin.runtime.audit.log_decision(
            event_type="approval_decision",
            session_id=request.session_id,
            vm_id=request.vm_id,
            approval_request_id=request.request_id,
            decision=request.status.value,
            reason=normalized_reason,
            args={
                "request_id": request.request_id,
                "tool_name": request.tool_name or request.action_type,
                "approver": normalized_approver,
                "payload_digest": request.payload_digest,
            },
            result={
                "status": request.status.value,
                "tool_name": request.tool_name or request.action_type,
                "payload_digest": request.payload_digest,
                "request_id": request.request_id,
            },
        )
        return request

    def revoke_approval_request(
        self,
        request_id: str,
        *,
        approver: str,
        reason: str,
    ) -> ApprovalRequest:
        admin = self._deps
        request = self.get_approval_request(request_id)
        normalized_approver = approver.strip()
        normalized_reason = reason.strip()
        if not normalized_approver:
            raise ValueError("approver is required")
        if not normalized_reason:
            raise ValueError("reason is required")
        if request.status != ApprovalStatus.APPROVED:
            raise ValueError(f"Approval request {request_id} is not approved")
        if request.expires_at <= _admin_service._utc_now():
            raise ValueError(f"Approval request {request_id} has expired")
        decided_at = _admin_service._utc_now()
        request.status = ApprovalStatus.DENIED
        request.approver = normalized_approver
        request.decision_at = decided_at
        request.decision_reason = normalized_reason
        request.decision_history.append(
            ApprovalDecisionRecord(
                status=ApprovalStatus.DENIED,
                approver=normalized_approver,
                reason=normalized_reason,
                decided_at=decided_at,
            )
        )
        admin.runtime.approvals.put(request.request_id, request)
        admin.runtime.audit.log_decision(
            event_type="approval_decision",
            session_id=request.session_id,
            vm_id=request.vm_id,
            approval_request_id=request.request_id,
            decision=request.status.value,
            reason=normalized_reason,
            args={
                "request_id": request.request_id,
                "tool_name": request.tool_name or request.action_type,
                "approver": normalized_approver,
                "payload_digest": request.payload_digest,
            },
            result={
                "status": request.status.value,
                "action": "revoke_approval",
                "tool_name": request.tool_name or request.action_type,
                "payload_digest": request.payload_digest,
                "request_id": request.request_id,
            },
        )
        return request

    def list_actionable_local_issues(
        self,
        *,
        directory_path: str,
        states: set[str] | None = None,
    ) -> list[LocalIssueRecord]:
        admin = self._deps
        normalized_directory_path = admin.normalize_directory_path(directory_path)
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
        return sorted(self._deps.direct_owned_entity_ids(directory_path, kind=kind))

    def create_local_issue(
        self,
        *,
        title: str = "",
        body: str = "",
        repo_source: str | None = None,
        yaml_path: str | None = None,
        directory_path: str = "root",
        metadata: dict[str, Any] | None = None,
    ) -> LocalIssueRecord:
        return self.create_issue(
            title=title,
            body=body,
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
        repo_source: str | None = None,
    ) -> LocalIssueRecord:
        admin = self._deps
        issue = admin.resolve_local_issue_record(issue_identifier)
        if issue.source_type != "local_manual":
            raise ValueError("Only local manual issues can be edited from the admin GUI.")
        normalized_title = title.strip()
        if not normalized_title:
            raise ValueError("title is required")
        normalized_state = _admin_service._normalize_local_issue_state(state, allow_empty=False)
        now = _admin_service._utc_now()
        issue.title = normalized_title
        issue.body = body.strip()
        issue.state = normalized_state
        issue.repo_source = repo_source.strip() if repo_source else None
        issue.updated_at = now
        admin.local_issue_store.put(issue.issue_id, issue)
        admin.write_local_issue_yaml(issue, updated_at=now)
        return issue

    def create_issue(
        self,
        *,
        title: str = "",
        body: str = "",
        repo_source: str | None = None,
        yaml_path: str | None = None,
        directory_path: str = "root",
        metadata: dict[str, Any] | None = None,
    ) -> LocalIssueRecord:
        admin = self._deps
        now = _admin_service._utc_now()
        normalized_directory_path = admin.normalize_directory_path(directory_path)
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
                    yaml_payload = json.loads(
                        json.dumps(_admin_service.yaml.safe_load(source_text) or {})
                    )
                except Exception:
                    yaml_payload = {}
                issue_number = _admin_service._extract_issue_number_from_payload(
                    yaml_payload
                ) or _admin_service._read_issue_number_from_yaml(source_path)
                target_yaml_path = admin.local_issue_yaml_dir / f"{issue_number}.yaml"
                target_yaml_path.write_text(source_text, encoding="utf-8")
                yaml_text = target_yaml_path.read_text(encoding="utf-8")
                try:
                    yaml_payload = json.loads(
                        json.dumps(_admin_service.yaml.safe_load(yaml_text) or {})
                    )
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
                issue_id = _admin_service._scoped_issue_id(
                    issue_number,
                    directory_path=normalized_directory_path,
                    provider="azure_devops",
                    organization=remote_reference.organization,
                    project=remote_reference.project,
                )
            else:
                issue_id = admin.canonical_local_issue_id(issue_number, normalized_directory_path)
            existing_record = admin.local_issue_store.get(issue_id)
            record = LocalIssueRecord(
                issue_id=issue_id,
                issue_number=issue_number,
                title=title.strip() or f"Local issue {issue_number}",
                body=body.strip(),
                yaml_path=str(target_yaml_path),
                directory_path=normalized_directory_path,
                repo_source=repo_source.strip() if repo_source else None,
                source_type=source_type,
                remote_reference=remote_reference,
                state=(
                    state_value
                    if source_type == "remote_proxy"
                    else _admin_service._normalize_local_issue_state(state_value, allow_empty=False)
                ),
                url=url_value,
                comments=comments_value,
                source=source_value,
                metadata=merged_metadata,
                created_at=existing_record.created_at if existing_record is not None else now,
                updated_at=now,
            )
            admin.local_issue_store.put(record.issue_id, record)
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
                {"text": normalized_comment, "created_at": _admin_service._utc_now().isoformat()}
            )
            issue.metadata["comments"] = comments
            issue.comments = comments
            issue.updated_at = _admin_service._utc_now()
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
        issue.updated_at = _admin_service._utc_now()
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
                normalized_state = _admin_service._normalize_local_issue_state(
                    state, allow_empty=False
                )
                issue.metadata["state"] = normalized_state
                issue.state = normalized_state
            if fields:
                local_fields = dict(issue.metadata.get("fields") or {})
                local_fields.update(fields)
                issue.metadata["fields"] = local_fields
            issue.sync_derived_fields()
            issue.updated_at = _admin_service._utc_now()
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
        return {"issue_number": issue.issue_number, "source_type": "proxy", **result}

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
        issue.updated_at = _admin_service._utc_now()
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
            key=_admin_service._issue_priority_sort_key,
        )
        if not candidates:
            return None
        issue = candidates[0]
        issue.metadata = dict(issue.metadata)
        local_fields = _admin_service._local_issue_fields(issue)
        local_fields["claimed_by_session_id"] = session_id
        if session.vm_id:
            local_fields["claimed_by_vm_id"] = session.vm_id
        local_fields["claimed_at"] = _admin_service._utc_now().isoformat()
        issue.metadata["fields"] = local_fields
        issue.state = "In Progress"
        issue.metadata["state"] = issue.state
        issue.updated_at = _admin_service._utc_now()
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
        local_fields = _admin_service._local_issue_fields(issue)
        local_fields["completed_summary"] = normalized_summary
        local_fields["completed_by_session_id"] = session_id
        if session.vm_id:
            local_fields["completed_by_vm_id"] = session.vm_id
        local_fields["completed_at"] = _admin_service._utc_now().isoformat()
        issue.metadata["fields"] = local_fields
        issue.state = "Completed"
        issue.metadata["state"] = issue.state
        issue.updated_at = _admin_service._utc_now()
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
        existing_vm_state = _admin_service._dominfo_field(vm_name, "State")
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
        yaml_path = Path(issue.yaml_path)
        if yaml_path.exists():
            yaml_path.unlink()
        return VmActionResult(
            success=True,
            vm_name=vm_name,
            action="delete_local_issue",
            message=f"Deleted local issue {issue_number}",
        )

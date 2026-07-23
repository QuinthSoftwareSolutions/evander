from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime
from fnmatch import fnmatchcase
import fcntl
import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import pwd
import re
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
from typing import Any
from urllib.parse import quote, unquote, urlsplit, urlunsplit
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

from evander.admin.payloads import AdminRevisionConflict
from evander.admin.services.managed_git_common import (
    DEFAULT_STALE_WORKTREE_SECONDS,
    ManagedGitConflictError,
    ManagedGitNotFoundError,
    _HEX_OBJECT_ID,
    _SAFE_OPERATION_ID,
    _delete_ref_cas as _delete_managed_ref_cas,
    _managed_bare_path,
    _normalize_object_id,
    _normalize_ref,
    _require_commit as _require_managed_commit,
    _resolve_ref as _resolve_managed_ref,
    _run_update_ref_transaction as _run_managed_update_ref_transaction,
    _utc_now,
    _with_git_config_environment,
    _write_json_atomic,
)
from evander.admin.services.managed_git_worktree_service import (
    ManagedGitWorktreeDependencies,
    ManagedGitWorktreeService,
)
from evander.admin.services.workflow_pack_history_service import WorkflowPackChangeActor
from evander.consultations import consultation_snapshot_ref
from evander.git_pr_notes import agent_pr_notes_ref_for_session
from evander.managed_git_grants import (
    LEGACY_MANAGED_GIT_GRANT_METADATA_KEYS,
    MANAGED_GIT_GRANTS_METADATA_KEY,
)
from evander.models import DirectoryPath, ToolName
from evander.pr_tracking import PrMapStore
from evander.repositories.session_manifests import JsonSessionManifestRepository
from evander.storage import JsonObjectStore


REGISTRY_SCHEMA = "evander.managed-git-repositories/v1"
REGISTRY_FILENAME = "managed-git-repositories.json"
DEFAULT_HOST_COMMIT_NAME = "Truman Bot"
DEFAULT_HOST_COMMIT_EMAIL = "truman@quinth.se"
DEFAULT_GIT_OPERATION_TIMEOUT_SECONDS = 5 * 60
_RESERVED_BRANCH_ROOTS = frozenset({"agents", "review", "team"})
_LEGACY_MANAGED_GIT_FIELDS = frozenset(
    {
        "host_repo_path",
        "trusted_repo_path",
        "bare_repo_path",
        "bare_repo_name",
    }
)
_LEGACY_MANAGED_GIT_ROUTING_FIELDS = frozenset(
    {*_LEGACY_MANAGED_GIT_FIELDS, "repo_path", "authorized_repo_path"}
)
_MANAGED_GIT_NODE_PREFIXES = (
    "trigger:git:",
    "effect:git:",
    "effect:pr:",
    "query:git:",
    "effect:agent:clone-repo",
    "effect:vm:clone-repo",
)
_B64_ASSIGNMENT_PATTERN = re.compile(
    r"(?P<name>EVANDER_GIT_(?:ROOT|AUTH_REPO|AUTH_REPO_PREFIX|AUTH_SESSION_ID)_B64)="
    r"(?P<value>[A-Za-z0-9+/=]+)"
)
_MAX_CONSULTATION_EVIDENCE_BLOB_BYTES = 5 * 1024 * 1024
_AZURE_PR_DESCRIPTION_MAX_CHARS = 4000
_AZURE_PR_SOURCE_CONFIRMATION_TIMEOUT_SECONDS = 10.0
_AZURE_PR_SOURCE_CONFIRMATION_INTERVAL_SECONDS = 1.0


@dataclass(frozen=True, slots=True)
class _SourceInfo:
    canonical_identity: str
    canonical_url: str
    display_name: str
    provider: str
    refresh_source: str
    refresh_source_kind: str
    username: str | None = None
    password: str | None = None


@dataclass(frozen=True, slots=True)
class _MigrationFileRewrite:
    path: Path
    kind: str
    before: bytes
    after: bytes
    workflow_pack: str | None = None
    workflow_before_revision: str | None = None
    workflow_after_revision: str | None = None

    @property
    def before_sha256(self) -> str:
        return hashlib.sha256(self.before).hexdigest()

    @property
    def after_sha256(self) -> str:
        return hashlib.sha256(self.after).hexdigest()


def _is_same_or_descendant(path: str, ancestor: str) -> bool:
    return path == ancestor or path.startswith(f"{ancestor}/")


def _normalize_branch(value: str, *, field_name: str) -> str:
    branch = str(value or "").strip()
    for prefix in ("refs/heads/", "refs/head/"):
        if branch.startswith(prefix):
            branch = branch[len(prefix) :]
            break
    if not branch:
        raise ValueError(f"{field_name} is required")
    result = subprocess.run(
        ["git", "check-ref-format", f"refs/heads/{branch}"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise ValueError(f"{field_name} is not a valid Git branch name: {branch}")
    return branch


def _safe_checkout_name(value: object, *, fallback: str) -> str:
    candidate = str(value or "").strip().replace("\\", "/").rsplit("/", 1)[-1]
    candidate = candidate[:-4] if candidate.casefold().endswith(".git") else candidate
    candidate = re.sub(r"[^A-Za-z0-9._-]+", "-", candidate).strip(".-")
    return candidate or fallback


def _manifest_path_names(entries: list[str]) -> set[str]:
    paths: set[str] = set()
    for entry in entries:
        parts = [part.strip() for part in str(entry).split("\t") if part.strip()]
        if not parts:
            continue
        paths.update(parts[1:] if len(parts) > 1 else parts)
    return paths


class ManagedGitAdminService:
    """Owns Evander-managed bare repositories and their transient worktrees.

    The public methods deliberately return registry identities and Git refs only. Internal
    bare-repository and worktree paths remain private implementation details.
    """

    def __init__(self, config: Any, runtime: Any, project_dir: str | Path) -> None:
        self.config = config
        self.runtime = runtime
        self.project_dir = Path(project_dir).expanduser().resolve(strict=False)
        runtime_dir = Path(getattr(config, "runtime_dir", "runtime")).expanduser()
        if not runtime_dir.is_absolute():
            runtime_dir = self.project_dir / runtime_dir
        self.runtime_dir = runtime_dir.resolve(strict=False)
        self._managed_root = self.runtime_dir / "managed-git"
        self._worktree_root = self.runtime_dir / "git-worktrees"
        self._lock_root = self.runtime_dir / "managed-git-locks"
        self._migration_root = self.runtime_dir / "managed-git-migrations"
        self._migration_preflight_context = threading.local()
        self._worktrees = ManagedGitWorktreeService(
            ManagedGitWorktreeDependencies(
                stale_worktree_seconds=float(
                    getattr(
                        config,
                        "managed_git_worktree_stale_seconds",
                        DEFAULT_STALE_WORKTREE_SECONDS,
                    )
                ),
                runtime_dir=self.runtime_dir,
                managed_root=self._managed_root,
                worktree_root=self._worktree_root,
                require_record=lambda managed_repo_id: self._require_record(managed_repo_id),
                require_migration_preflight=lambda: self._require_migration_preflight(),
                read_registry=lambda: self._read_registry(),
                repo_lock=lambda managed_repo_id, *, exclusive: self._repo_lock(
                    managed_repo_id,
                    exclusive=exclusive,
                ),
                run_git=lambda command, **kwargs: self._run_git(command, **kwargs),
            )
        )
        self._workflow_config_admin: Any | None = None
        self._agent_inventory_admin: Any | None = None
        self._registry_path = self.runtime_dir / REGISTRY_FILENAME
        self._registry: JsonObjectStore[dict[str, Any]] | None = None
        if self._registry_path.exists():
            self._validate_registry(self._registry_store().read_object())

    def set_workflow_config_admin(self, workflow_config_admin: Any) -> None:
        self._workflow_config_admin = workflow_config_admin

    def set_agent_inventory_admin(self, agent_inventory_admin: Any) -> None:
        self._agent_inventory_admin = agent_inventory_admin

    def preflight(self) -> dict[str, Any]:
        self._require_migration_preflight()
        registry = self._read_registry()
        return {
            "schema": registry["schema"],
            "repository_count": len(registry["repositories"]),
        }

    @staticmethod
    def _new_registry() -> dict[str, Any]:
        now = _utc_now()
        return {
            "schema": REGISTRY_SCHEMA,
            "schema_version": 1,
            "created_at": now,
            "updated_at": now,
            "repositories": {},
            "migrations": {},
        }

    @staticmethod
    def _validate_registry(payload: Mapping[str, Any]) -> None:
        marker = payload.get("schema")
        if marker != REGISTRY_SCHEMA:
            raise ValueError(
                "Managed Git registry has an unsupported or missing schema marker. "
                "Stop trigger/effect workers and run 'evander-cli git-repos migrate --dry-run'."
            )
        if not isinstance(payload.get("repositories"), dict):
            raise ValueError("Managed Git registry repositories must be an object")
        if not isinstance(payload.get("migrations"), dict):
            raise ValueError("Managed Git registry migrations must be an object")

    def _read_registry(self) -> dict[str, Any]:
        payload = self._registry_store().read_object()
        self._validate_registry(payload)
        return payload

    def _update_registry(self, updater):
        def update(payload: dict[str, Any]):
            self._validate_registry(payload)
            result = updater(payload)
            payload["updated_at"] = _utc_now()
            return result

        return self._registry_store().update_object(update)

    def _registry_store(self) -> JsonObjectStore[dict[str, Any]]:
        if self._registry is None:
            self._ensure_runtime_layout()
            self._registry = JsonObjectStore[dict[str, Any]](
                self._registry_path,
                self._new_registry(),
                file_mode=0o600,
                empty_file_factory=self._new_registry,
            )
        return self._registry

    def _ensure_runtime_layout(self) -> None:
        self.runtime_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        for directory in (
            self._managed_root,
            self._worktree_root,
            self._lock_root,
            self._migration_root,
        ):
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            directory.chmod(0o700)

    def _normalize_directory_path(self, value: str) -> str:
        normalized = DirectoryPath(segments=str(value or "").strip()).as_posix()
        directories = getattr(self.runtime, "directories", None)
        validate_path = getattr(directories, "validate_path", None)
        if callable(validate_path):
            normalized = validate_path(normalized)
        return normalized

    @staticmethod
    def _managed_repo_id(directory_path: str, canonical_identity: str) -> str:
        digest = hashlib.sha256(
            f"{directory_path}\0{canonical_identity}".encode("utf-8")
        ).hexdigest()[:20]
        return f"mr_{digest}"

    @staticmethod
    def _strip_dot_git(value: str) -> str:
        stripped = value.rstrip("/")
        return stripped[:-4] if stripped.casefold().endswith(".git") else stripped

    def _source_info(self, repo_source: str) -> _SourceInfo:
        source = str(repo_source or "").strip()
        if not source:
            raise ValueError("repo_source is required")

        local_path = self._resolve_local_source(source)
        if local_path is None:
            return self._remote_source_info(source)

        self._require_git_repository(local_path)
        origin = self._git_output(
            ["git", "-C", str(local_path), "remote", "get-url", "origin"],
            check=False,
        ).strip()
        if origin:
            origin_info = self._remote_source_info(origin, relative_to=local_path)
            return _SourceInfo(
                canonical_identity=origin_info.canonical_identity,
                canonical_url=origin_info.canonical_url,
                display_name=origin_info.display_name,
                provider=origin_info.provider,
                refresh_source=str(local_path),
                refresh_source_kind="local",
            )

        display_name = self._strip_dot_git(local_path.name) or "repository"
        canonical_path = str(local_path)
        return _SourceInfo(
            canonical_identity=f"local:{canonical_path}",
            canonical_url=canonical_path,
            display_name=display_name,
            provider="local",
            refresh_source=canonical_path,
            refresh_source_kind="local",
        )

    def _resolve_local_source(self, source: str) -> Path | None:
        if source.startswith("file://"):
            parsed = urlsplit(source)
            if parsed.netloc not in {"", "localhost"}:
                raise ValueError("file repo_source must refer to the local host")
            path = Path(unquote(parsed.path)).expanduser()
        elif re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", source):
            return None
        elif re.match(r"^[^/@:\s]+@[^:\s]+:.+", source):
            return None
        else:
            path = Path(source).expanduser()
        if not path.is_absolute():
            path = self.project_dir / path
        try:
            return path.resolve(strict=True)
        except FileNotFoundError as exc:
            raise ValueError("Local repo_source does not exist") from exc

    def _remote_source_info(
        self,
        source: str,
        *,
        relative_to: Path | None = None,
    ) -> _SourceInfo:
        text = str(source or "").strip()
        if not text:
            raise ValueError("repository URL is empty")

        if text.startswith("file://"):
            parsed_file = urlsplit(text)
            if parsed_file.netloc not in {"", "localhost"}:
                raise ValueError("file repository URL must refer to the local host")
            local = Path(unquote(parsed_file.path)).expanduser()
            if not local.is_absolute() and relative_to is not None:
                local = relative_to / local
            try:
                local = local.resolve(strict=True)
            except FileNotFoundError as exc:
                raise ValueError("Repository origin does not exist") from exc
            display_name = self._strip_dot_git(local.name) or "repository"
            return _SourceInfo(
                canonical_identity=f"local:{local}",
                canonical_url=str(local),
                display_name=display_name,
                provider="local",
                refresh_source=str(local),
                refresh_source_kind="local",
            )

        if not re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", text):
            scp_match = re.fullmatch(
                r"(?:(?P<user>[^/@:\s]+)@)?(?P<host>[^:\s]+):(?P<path>.+)", text
            )
            if scp_match:
                user = scp_match.group("user") or ""
                host = scp_match.group("host")
                path = scp_match.group("path")
                authority = f"{user}@{host}" if user else host
                text = f"ssh://{authority}/{path.lstrip('/')}"
            else:
                local = Path(text).expanduser()
                if not local.is_absolute() and relative_to is not None:
                    local = relative_to / local
                try:
                    local = local.resolve(strict=True)
                except FileNotFoundError as exc:
                    raise ValueError("Repository origin does not exist") from exc
                display_name = self._strip_dot_git(local.name) or "repository"
                return _SourceInfo(
                    canonical_identity=f"local:{local}",
                    canonical_url=str(local),
                    display_name=display_name,
                    provider="local",
                    refresh_source=str(local),
                    refresh_source_kind="local",
                )

        parsed = urlsplit(text)
        if not parsed.scheme or not parsed.hostname:
            raise ValueError("Unsupported repository URL")
        scheme = parsed.scheme.casefold()
        host = parsed.hostname.casefold()
        username = unquote(parsed.username) if parsed.username else None
        password = unquote(parsed.password) if parsed.password else None
        decoded_parts = [unquote(part) for part in PurePosixPath(parsed.path).parts if part != "/"]

        if host == "github.com" and len(decoded_parts) >= 2:
            owner = decoded_parts[-2]
            repo = self._strip_dot_git(decoded_parts[-1])
            if not owner or not repo:
                raise ValueError("Unsupported GitHub repository URL")
            display = f"{owner}/{repo}"
            canonical = f"https://github.com/{quote(owner, safe='')}/{quote(repo, safe='')}.git"
            refresh_source = canonical
            if scheme == "ssh":
                ssh_user = quote(username or "git", safe="")
                refresh_source = (
                    f"ssh://{ssh_user}@github.com/{quote(owner, safe='')}/"
                    f"{quote(repo, safe='')}.git"
                )
            return _SourceInfo(
                canonical_identity=f"github:{owner.casefold()}/{repo.casefold()}",
                canonical_url=canonical,
                display_name=display,
                provider="github",
                refresh_source=refresh_source,
                refresh_source_kind="remote",
                username=username,
                password=password,
            )

        azure_parts: tuple[str, str, str] | None = None
        if host == "ssh.dev.azure.com" and len(decoded_parts) >= 4 and decoded_parts[0] == "v3":
            azure_parts = (
                decoded_parts[1],
                decoded_parts[2],
                self._strip_dot_git(decoded_parts[3]),
            )
        elif (
            host == "dev.azure.com"
            and len(decoded_parts) >= 4
            and decoded_parts[2].casefold() == "_git"
        ):
            azure_parts = (
                decoded_parts[0],
                decoded_parts[1],
                self._strip_dot_git(decoded_parts[3]),
            )
        elif (
            host.endswith(".visualstudio.com")
            and len(decoded_parts) >= 3
            and decoded_parts[1].casefold() == "_git"
        ):
            azure_parts = (
                host[: -len(".visualstudio.com")],
                decoded_parts[0],
                self._strip_dot_git(decoded_parts[2]),
            )
        if azure_parts is not None:
            organization, project, repo = azure_parts
            if not all((organization, project, repo)):
                raise ValueError("Unsupported Azure DevOps repository URL")
            canonical = (
                f"https://dev.azure.com/{quote(organization, safe='')}/"
                f"{quote(project, safe='')}/_git/{quote(repo, safe='')}"
            )
            refresh_source = canonical
            if scheme == "ssh":
                ssh_user = quote(username or "git", safe="")
                refresh_source = (
                    f"ssh://{ssh_user}@ssh.dev.azure.com/v3/"
                    f"{quote(organization, safe='')}/{quote(project, safe='')}/"
                    f"{quote(repo, safe='')}"
                )
            return _SourceInfo(
                canonical_identity=(
                    f"azure_devops:{organization.casefold()}/{project.casefold()}/{repo.casefold()}"
                ),
                canonical_url=canonical,
                display_name=f"{organization}/{project}/{repo}",
                provider="azure_devops",
                refresh_source=refresh_source,
                refresh_source_kind="remote",
                username=username,
                password=password,
            )

        clean_path = self._strip_dot_git("/" + "/".join(decoded_parts))
        if not clean_path or clean_path == "/":
            raise ValueError("Repository URL has no repository path")
        port = parsed.port
        default_port = (
            (scheme == "https" and port == 443)
            or (scheme == "http" and port == 80)
            or (scheme == "ssh" and port == 22)
        )
        host_port = host if port is None or default_port else f"{host}:{port}"
        safe_path = "/".join(quote(part, safe="!$&'()*+,;=:@") for part in clean_path.split("/"))
        safe_user = username if scheme == "ssh" else None
        netloc = f"{quote(safe_user, safe='')}@{host_port}" if safe_user else host_port
        canonical = urlunsplit((scheme, netloc, safe_path, "", ""))
        display_name = self._strip_dot_git(decoded_parts[-1]) or host
        return _SourceInfo(
            canonical_identity=f"generic:{scheme}://{host_port}{clean_path}",
            canonical_url=canonical,
            display_name=display_name,
            provider="generic",
            refresh_source=canonical,
            refresh_source_kind="remote",
            username=username,
            password=password,
        )

    def register_repository(
        self,
        directory_path: str,
        repo_source: str,
        default_branch: str | None = None,
        refresh: bool = True,
        *,
        _canonical_source: str | None = None,
        authorize_existing_owner: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        self._require_migration_preflight()
        directory = self._normalize_directory_path(directory_path)
        refresh_source = self._source_info(repo_source)
        if _canonical_source:
            canonical_source = self._source_info(_canonical_source)
            source = _SourceInfo(
                canonical_identity=canonical_source.canonical_identity,
                canonical_url=canonical_source.canonical_url,
                display_name=canonical_source.display_name,
                provider=canonical_source.provider,
                refresh_source=refresh_source.refresh_source,
                refresh_source_kind=refresh_source.refresh_source_kind,
                username=refresh_source.username,
                password=refresh_source.password,
            )
        else:
            source = refresh_source
        requested_default = (
            _normalize_branch(default_branch, field_name="default_branch")
            if default_branch
            else None
        )
        # Serialize registrations for one canonical upstream across directory paths. The
        # registry visibility check still isolates siblings, while an ancestor registration
        # that wins the race can be reused by a concurrent descendant registration.
        identity_lock = hashlib.sha256(source.canonical_identity.encode("utf-8")).hexdigest()

        with self._file_lock(self._lock_root / f"registration-{identity_lock}.lock"):
            existing = self._find_accessible_identity(directory, source.canonical_identity)
            if existing is not None:
                override_changed = bool(
                    requested_default
                    and (
                        existing.get("default_branch") != requested_default
                        or existing.get("default_branch_source") != "override"
                    )
                )
                if override_changed:
                    existing_owner = str(existing["directory_path"])
                    if authorize_existing_owner is not None and existing_owner != directory:
                        authorize_existing_owner(existing_owner)
                    if refresh:
                        return self._refresh_repository_with_override(
                            str(existing["managed_repo_id"]),
                            requested_default,
                        )
                    self._update_record_fields(
                        existing["managed_repo_id"],
                        default_branch=requested_default,
                        default_branch_source="override",
                    )
                    self._run_git(
                        [
                            "git",
                            f"--git-dir={self._bare_path(existing)}",
                            "config",
                            "evander.baseBranch",
                            requested_default,
                        ]
                    )
                if refresh:
                    return self.refresh_repository(existing["managed_repo_id"])
                return self.get_repository(existing["managed_repo_id"], directory)

            descendant = self._find_descendant_identity(
                directory,
                source.canonical_identity,
            )
            if descendant is not None:
                descendant = self._promote_descendant_registration(
                    descendant,
                    directory_path=directory,
                    source=source,
                )
                override_changed = bool(
                    requested_default
                    and (
                        descendant.get("default_branch") != requested_default
                        or descendant.get("default_branch_source") != "override"
                    )
                )
                if override_changed:
                    if refresh:
                        return self._refresh_repository_with_override(
                            str(descendant["managed_repo_id"]),
                            requested_default,
                        )
                    self._update_record_fields(
                        descendant["managed_repo_id"],
                        default_branch=requested_default,
                        default_branch_source="override",
                    )
                    self._run_git(
                        [
                            "git",
                            f"--git-dir={self._bare_path(descendant)}",
                            "config",
                            "evander.baseBranch",
                            requested_default,
                        ]
                    )
                if refresh:
                    return self.refresh_repository(str(descendant["managed_repo_id"]))
                return self.get_repository(str(descendant["managed_repo_id"]), directory)

            managed_repo_id = self._managed_repo_id(directory, source.canonical_identity)
            existing_by_id = self._record_or_none(managed_repo_id)
            if existing_by_id is not None:
                if (
                    existing_by_id.get("directory_path") != directory
                    or existing_by_id.get("canonical_repository_identity")
                    != source.canonical_identity
                ):
                    raise ManagedGitConflictError(
                        f"Managed repository ID collision for {managed_repo_id}"
                    )
                return self.get_repository(managed_repo_id, directory)

            bare_path = self._managed_root / f"{managed_repo_id}.git"
            created_at = _utc_now()
            record: dict[str, Any] = {
                "managed_repo_id": managed_repo_id,
                "directory_path": directory,
                "canonical_repository_identity": source.canonical_identity,
                "canonical_repository_url": source.canonical_url,
                "provider": source.provider,
                "display_name": source.display_name,
                "default_branch": requested_default or "main",
                "default_branch_source": "override" if requested_default else "upstream",
                "refresh_source": source.refresh_source,
                "refresh_source_kind": source.refresh_source_kind,
                "internal_bare_path": str(bare_path),
                "host_commit_identity": self._commit_identity_for_directory(directory),
                "bindings": [],
                "created_at": created_at,
                "updated_at": created_at,
                "refreshed_at": None,
                "maintenance_at": None,
            }

            with self._repo_lock(managed_repo_id, exclusive=True):
                try:
                    self._initialize_bare_repository(record)
                    if refresh:
                        refresh_result = self._refresh_record(
                            record,
                            credential_override=(source.username, source.password),
                        )
                        record["refreshed_at"] = refresh_result["refreshed_at"]
                        if not requested_default and refresh_result.get("default_branch"):
                            record["default_branch"] = refresh_result["default_branch"]
                    self._put_new_record(record)
                except BaseException:
                    if bare_path.exists():
                        shutil.rmtree(bare_path, ignore_errors=True)
                    raise
            return self._public_record(record)

    def list_repositories(
        self,
        directory_path: str | None = None,
        include_descendants: bool = True,
    ) -> list[dict[str, Any]]:
        self._require_migration_preflight()
        records = list(self._read_registry()["repositories"].values())
        if directory_path is not None:
            directory = self._normalize_directory_path(directory_path)
            records = [
                record
                for record in records
                if _is_same_or_descendant(directory, str(record["directory_path"]))
                or (
                    include_descendants
                    and _is_same_or_descendant(str(record["directory_path"]), directory)
                )
            ]
        return [
            self._public_record(record)
            for record in sorted(
                records,
                key=lambda item: (
                    str(item.get("directory_path", "")),
                    str(item.get("display_name", "")).casefold(),
                    str(item.get("managed_repo_id", "")),
                ),
            )
        ]

    def get_repository(
        self,
        managed_repo_id: str,
        directory_path: str | None = None,
    ) -> dict[str, Any]:
        record = (
            self._require_scoped_record(managed_repo_id, directory_path)
            if directory_path is not None
            else self._require_record(managed_repo_id)
        )
        return self._public_record(record)

    def refresh_repository(self, managed_repo_id: str) -> dict[str, Any]:
        return self._refresh_repository_with_override(managed_repo_id, None)

    def _refresh_repository_with_override(
        self,
        managed_repo_id: str,
        default_branch_override: str | None,
    ) -> dict[str, Any]:
        record = self._require_record(managed_repo_id)
        effective_record = dict(record)
        if default_branch_override is not None:
            effective_record["default_branch"] = default_branch_override
            effective_record["default_branch_source"] = "override"
        with (
            self._repo_lock(managed_repo_id, exclusive=False),
            self._target_lock(managed_repo_id, "canonical-refresh"),
        ):
            result = self._refresh_record(effective_record)
        now = result["refreshed_at"]

        def update(payload: dict[str, Any]):
            current = payload["repositories"].get(managed_repo_id)
            if not isinstance(current, dict):
                raise ManagedGitNotFoundError(f"Unknown managed repository: {managed_repo_id}")
            current["refreshed_at"] = now
            current["updated_at"] = now
            if default_branch_override is not None:
                current["default_branch"] = default_branch_override
                current["default_branch_source"] = "override"
            elif current.get("default_branch_source") != "override" and result.get(
                "default_branch"
            ):
                current["default_branch"] = result["default_branch"]
            return dict(current)

        updated = self._update_registry(update)
        return self._public_record(updated)

    def delete_repository(self, managed_repo_id: str) -> dict[str, Any]:
        initial = self._require_record(managed_repo_id)
        if self._has_registered_worktrees(initial):
            raise ManagedGitConflictError(
                f"Managed repository {managed_repo_id} still has active or unreconciled worktrees"
            )
        with self._repo_lock(managed_repo_id, exclusive=True):
            current = self._require_record(managed_repo_id)
            bindings = list(current.get("bindings") or [])
            if bindings:
                raise ManagedGitConflictError(
                    f"Managed repository {managed_repo_id} still has active bindings",
                    details={
                        "bindings": [
                            f"{item.get('kind')}:{item.get('binding_id')}"
                            for item in bindings
                            if isinstance(item, dict)
                        ]
                    },
                )
            if self._has_unresolved_migration(managed_repo_id):
                raise ManagedGitConflictError(
                    f"Managed repository {managed_repo_id} is part of an unresolved migration"
                )
            if self._has_registered_worktrees(current):
                raise ManagedGitConflictError(
                    f"Managed repository {managed_repo_id} still has active or unreconciled worktrees"
                )
            if self._has_authorized_key_for_repository(current):
                raise ManagedGitConflictError(
                    f"Managed repository {managed_repo_id} still has authorized SSH keys"
                )
            bare_path = self._bare_path(current)
            if bare_path.exists():
                shutil.rmtree(bare_path)

            def remove(payload: dict[str, Any]):
                removed = payload["repositories"].pop(managed_repo_id, None)
                if removed is None:
                    raise ManagedGitNotFoundError(f"Unknown managed repository: {managed_repo_id}")

            self._update_registry(remove)
        return {"success": True, "managed_repo_id": managed_repo_id, "deleted": True}

    def maintenance(self, managed_repo_id: str | None = None) -> dict[str, Any]:
        self._require_migration_preflight()
        reconciliation = self.reconcile_stale_worktrees(managed_repo_id)
        records = (
            [self._require_record(managed_repo_id)]
            if managed_repo_id
            else list(self._read_registry()["repositories"].values())
        )
        maintained: list[str] = []
        failures: list[dict[str, str]] = []
        for record in records:
            repo_id = str(record["managed_repo_id"])
            try:
                with self._repo_lock(repo_id, exclusive=True):
                    bare_path = self._bare_path(record)
                    self._run_git(
                        ["git", f"--git-dir={bare_path}", "worktree", "prune", "--expire", "now"]
                    )
                    maintenance = self._run_git(
                        ["git", f"--git-dir={bare_path}", "maintenance", "run", "--auto"],
                        check=False,
                    )
                    if maintenance.returncode != 0:
                        self._run_git(["git", f"--git-dir={bare_path}", "gc", "--auto"])
                maintained.append(repo_id)
                self._update_record_fields(repo_id, maintenance_at=_utc_now())
            except Exception as exc:
                failures.append({"managed_repo_id": repo_id, "error": str(exc)})
        return {
            "success": not failures,
            "maintained_repository_ids": maintained,
            "failures": failures,
            "worktree_reconciliation": reconciliation,
        }

    def clone_info(
        self,
        managed_repo_id: str,
        directory_path: str,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        directory = self._normalize_directory_path(directory_path)
        record = self._require_scoped_record(managed_repo_id, directory)
        normalized_session = str(session_id or "").strip() or None
        if normalized_session is not None:
            if "/" in normalized_session or normalized_session in {".", ".."}:
                raise ValueError("session_id must be one non-empty Git ref path component")
        alias = os.environ.get("EVANDER_GIT_SSH_ALIAS", "").strip() or "evander-git-host"
        result: dict[str, Any] = {
            "managed_repo_id": managed_repo_id,
            "clone_url": f"ssh://{alias}{self._managed_root}/{managed_repo_id}.git",
            "display_name": record.get("display_name") or managed_repo_id,
            "repo_name": _safe_checkout_name(record.get("display_name"), fallback=managed_repo_id),
            "default_branch": record.get("default_branch"),
            "directory_path": directory,
        }
        if normalized_session is not None:
            result.update(
                {
                    "session_id": normalized_session,
                    "agent_ref_prefix": f"refs/heads/agents/{normalized_session}/",
                    "notes_ref": agent_pr_notes_ref_for_session(normalized_session),
                }
            )
        return result

    def resolve_internal_repository(
        self,
        managed_repo_id: str,
        directory_path: str,
    ) -> tuple[Path, Path]:
        """Resolve a repository for trusted host plumbing without exposing it to workflows."""
        directory = self._normalize_directory_path(directory_path)
        record = self._require_scoped_record(managed_repo_id, directory)
        bare_path = self._bare_path(record)
        return bare_path, self._managed_root

    def resolve_snapshot(
        self,
        managed_repo_id: str,
        directory_path: str,
        *,
        source_ref: str | None = None,
        commit_sha: str | None = None,
    ) -> dict[str, Any]:
        """Resolve and verify the immutable commit used by a read-only consumer."""

        directory = self._normalize_directory_path(directory_path)
        record = self._require_scoped_record(managed_repo_id, directory)
        bare_path = self._bare_path(record)
        requested_ref = str(source_ref or "").strip()
        if not requested_ref and not commit_sha:
            requested_ref = str(record.get("default_branch") or "").strip()
            if not requested_ref:
                raise ValueError(
                    "source_ref or commit_sha is required because the managed repository "
                    "has no default branch"
                )
        normalized_ref = (
            _normalize_ref(requested_ref, field_name="source_ref") if requested_ref else None
        )
        expected = _normalize_object_id(commit_sha, field_name="commit_sha") if commit_sha else None
        with self._repo_lock(managed_repo_id, exclusive=False):
            resolved = self._resolve_ref(bare_path, normalized_ref) if normalized_ref else expected
            if resolved is None:
                raise ManagedGitNotFoundError(
                    f"Unknown source ref {normalized_ref} in {managed_repo_id}"
                )
            if expected is not None and resolved != expected:
                raise ManagedGitConflictError(
                    f"Source ref {normalized_ref} resolves to {resolved}, not {expected}",
                    details={
                        "managed_repo_id": managed_repo_id,
                        "source_ref": normalized_ref,
                        "expected_commit_sha": expected,
                        "current_commit_sha": resolved,
                    },
                )
            self._require_commit(bare_path, resolved)
        return {
            "managed_repo_id": managed_repo_id,
            "directory_path": directory,
            "source_ref": normalized_ref,
            "commit_sha": resolved,
        }

    def pin_snapshot(
        self,
        managed_repo_id: str,
        directory_path: str,
        *,
        consultation_id: str,
        source_ref: str | None = None,
        commit_sha: str | None = None,
    ) -> dict[str, Any]:
        """Resolve a snapshot and retain its commit under a consultation-scoped ref."""

        directory = self._normalize_directory_path(directory_path)
        record = self._require_scoped_record(managed_repo_id, directory)
        binding_preexisting = any(
            isinstance(item, dict)
            and item.get("kind") == "consultations"
            and item.get("binding_id") == consultation_id
            for item in record.get("bindings") or []
        )
        bare_path = self._bare_path(record)
        pin_ref = consultation_snapshot_ref(consultation_id)
        requested_ref = str(source_ref or "").strip()
        if not requested_ref and not commit_sha:
            requested_ref = str(record.get("default_branch") or "").strip()
            if not requested_ref:
                raise ValueError(
                    "source_ref or commit_sha is required because the managed repository "
                    "has no default branch"
                )
        normalized_ref = (
            _normalize_ref(requested_ref, field_name="source_ref") if requested_ref else None
        )
        expected = _normalize_object_id(commit_sha, field_name="commit_sha") if commit_sha else None
        self.bind(managed_repo_id, "consultations", consultation_id)
        try:
            with (
                self._repo_lock(managed_repo_id, exclusive=False),
                self._target_lock(managed_repo_id, pin_ref),
            ):
                existing_pin = self._resolve_ref(bare_path, pin_ref)
                if existing_pin is not None:
                    if expected is not None and existing_pin != expected:
                        raise ManagedGitConflictError(
                            f"Consultation snapshot {pin_ref} is already pinned to {existing_pin}, "
                            f"not {expected}"
                        )
                    self._require_commit(bare_path, existing_pin)
                    resolved = existing_pin
                else:
                    resolved = (
                        self._resolve_ref(bare_path, normalized_ref) if normalized_ref else expected
                    )
                    if resolved is None:
                        raise ManagedGitNotFoundError(
                            f"Unknown source ref {normalized_ref} in {managed_repo_id}"
                        )
                    if expected is not None and resolved != expected:
                        raise ManagedGitConflictError(
                            f"Source ref {normalized_ref} resolves to {resolved}, not {expected}",
                            details={
                                "managed_repo_id": managed_repo_id,
                                "source_ref": normalized_ref,
                                "expected_commit_sha": expected,
                                "current_commit_sha": resolved,
                            },
                        )
                    self._require_commit(bare_path, resolved)
                    commands = [f"create {pin_ref} {resolved}"]
                    if normalized_ref is not None:
                        commands.insert(0, f"verify {normalized_ref} {resolved}")
                    try:
                        self._run_update_ref_transaction(bare_path, commands)
                    except RuntimeError as exc:
                        current_source = (
                            self._resolve_ref(bare_path, normalized_ref)
                            if normalized_ref is not None
                            else None
                        )
                        if normalized_ref is not None and current_source != resolved:
                            raise ManagedGitConflictError(
                                f"Source ref {normalized_ref} moved while pinning the consultation",
                                details={
                                    "managed_repo_id": managed_repo_id,
                                    "source_ref": normalized_ref,
                                    "expected_commit_sha": resolved,
                                    "current_commit_sha": current_source,
                                },
                            ) from exc
                        raise
        except Exception as exc:
            if not binding_preexisting:
                try:
                    self.unbind(managed_repo_id, "consultations", consultation_id)
                except Exception as rollback_exc:
                    exc.add_note(f"consultation binding rollback failed: {rollback_exc}")
            raise
        return {
            "managed_repo_id": managed_repo_id,
            "directory_path": directory,
            "source_ref": normalized_ref,
            "commit_sha": resolved,
            "pin_ref": pin_ref,
        }

    def release_snapshot_pin(
        self,
        managed_repo_id: str,
        directory_path: str,
        *,
        consultation_id: str,
        expected_commit_sha: str | None = None,
    ) -> dict[str, Any]:
        """Idempotently release a consultation snapshot ref after worker cleanup."""

        directory = self._normalize_directory_path(directory_path)
        record = self._require_scoped_record(managed_repo_id, directory)
        bare_path = self._bare_path(record)
        pin_ref = consultation_snapshot_ref(consultation_id)
        expected = (
            _normalize_object_id(expected_commit_sha, field_name="expected_commit_sha")
            if expected_commit_sha
            else None
        )
        released = False
        with (
            self._repo_lock(managed_repo_id, exclusive=False),
            self._target_lock(managed_repo_id, pin_ref),
        ):
            current = self._resolve_ref(bare_path, pin_ref)
            if current is not None:
                if expected is not None and current != expected:
                    raise ManagedGitConflictError(
                        f"Consultation snapshot {pin_ref} points to {current}, not {expected}"
                    )
                self._delete_ref_cas(bare_path, pin_ref, current)
                remaining = self._resolve_ref(bare_path, pin_ref)
                if remaining is not None:
                    raise ManagedGitConflictError(
                        f"Consultation snapshot {pin_ref} could not be released"
                    )
                released = True
        self.unbind(managed_repo_id, "consultations", consultation_id)
        return {
            "managed_repo_id": managed_repo_id,
            "consultation_id": consultation_id,
            "pin_ref": pin_ref,
            "released": released,
        }

    def validate_snapshot_evidence(
        self,
        managed_repo_id: str,
        directory_path: str,
        *,
        commit_sha: str,
        evidence: list[Mapping[str, Any]],
    ) -> list[dict[str, Any]]:
        """Validate cited paths and one-based line ranges against a pinned commit."""

        snapshot = self.resolve_snapshot(
            managed_repo_id,
            directory_path,
            commit_sha=commit_sha,
        )
        record = self._require_scoped_record(managed_repo_id, snapshot["directory_path"])
        bare_path = self._bare_path(record)
        validated: list[dict[str, Any]] = []
        for item in evidence:
            raw_path = str(item.get("path") or "").strip().replace("\\", "/")
            relative = PurePosixPath(raw_path)
            if (
                not raw_path
                or relative.is_absolute()
                or raw_path.startswith("./")
                or any(part in {"", ".", ".."} for part in relative.parts)
            ):
                raise ValueError(f"evidence path must be repository-relative: {raw_path!r}")
            line_start = int(item.get("line_start") or 0)
            line_end = int(item.get("line_end") or 0)
            object_spec = f"{snapshot['commit_sha']}:{relative.as_posix()}"
            object_type = self._run_git(
                ["git", f"--git-dir={bare_path}", "cat-file", "-t", object_spec],
                check=False,
            )
            if object_type.returncode != 0 or object_type.stdout.strip() != "blob":
                raise ValueError(
                    f"evidence path is not a file at {snapshot['commit_sha']}: "
                    f"{relative.as_posix()}"
                )
            object_size = self._run_git(
                ["git", f"--git-dir={bare_path}", "cat-file", "-s", object_spec],
                check=False,
            )
            try:
                blob_size = int(object_size.stdout.strip())
            except ValueError as exc:
                raise ValueError(
                    f"could not determine evidence file size: {relative.as_posix()}"
                ) from exc
            if blob_size > _MAX_CONSULTATION_EVIDENCE_BLOB_BYTES:
                raise ValueError(
                    f"evidence file exceeds the 5 MiB validation limit: {relative.as_posix()}"
                )
            blob = self._run_git(
                [
                    "git",
                    f"--git-dir={bare_path}",
                    "show",
                    object_spec,
                ],
                check=False,
            )
            if blob.returncode != 0:
                raise ValueError(
                    f"evidence path does not exist at {snapshot['commit_sha']}: "
                    f"{relative.as_posix()}"
                )
            line_count = len(blob.stdout.splitlines())
            if line_start < 1 or line_end < line_start or line_end > line_count:
                raise ValueError(
                    f"evidence range {line_start}-{line_end} is outside "
                    f"{relative.as_posix()} (1-{line_count})"
                )
            validated.append(
                {
                    "path": relative.as_posix(),
                    "line_start": line_start,
                    "line_end": line_end,
                    "explanation": str(item.get("explanation") or "").strip(),
                }
            )
        return validated

    def bind(self, managed_repo_id: str, kind: str, binding_id: str) -> dict[str, Any]:
        self._require_migration_preflight()
        normalized_kind = str(kind or "").strip().casefold()
        normalized_id = str(binding_id or "").strip()
        if not normalized_kind or not re.fullmatch(r"[a-z][a-z0-9_-]*", normalized_kind):
            raise ValueError("kind must be a non-empty lowercase identifier")
        if not normalized_id:
            raise ValueError("binding_id is required")
        now = _utc_now()

        def update(payload: dict[str, Any]):
            record = payload["repositories"].get(managed_repo_id)
            if not isinstance(record, dict):
                raise ManagedGitNotFoundError(f"Unknown managed repository: {managed_repo_id}")
            bindings = list(record.get("bindings") or [])
            if not any(
                item.get("kind") == normalized_kind and item.get("binding_id") == normalized_id
                for item in bindings
                if isinstance(item, dict)
            ):
                bindings.append(
                    {"kind": normalized_kind, "binding_id": normalized_id, "bound_at": now}
                )
                bindings.sort(key=lambda item: (item["kind"], item["binding_id"]))
                record["bindings"] = bindings
                record["updated_at"] = now
            return dict(record)

        with self._repo_lock(managed_repo_id, exclusive=False):
            return self._public_record(self._update_registry(update))

    def unbind(self, managed_repo_id: str, kind: str, binding_id: str) -> dict[str, Any]:
        self._require_migration_preflight()
        normalized_kind = str(kind or "").strip().casefold()
        normalized_id = str(binding_id or "").strip()
        if not normalized_kind or not normalized_id:
            raise ValueError("kind and binding_id are required")

        def update(payload: dict[str, Any]):
            record = payload["repositories"].get(managed_repo_id)
            if not isinstance(record, dict):
                raise ManagedGitNotFoundError(f"Unknown managed repository: {managed_repo_id}")
            bindings = [
                item
                for item in list(record.get("bindings") or [])
                if not (
                    isinstance(item, dict)
                    and item.get("kind") == normalized_kind
                    and item.get("binding_id") == normalized_id
                )
            ]
            if len(bindings) != len(record.get("bindings") or []):
                record["bindings"] = bindings
                record["updated_at"] = _utc_now()
            return dict(record)

        with self._repo_lock(managed_repo_id, exclusive=False):
            return self._public_record(self._update_registry(update))

    def unbind_all(self, kind: str, binding_id: str) -> list[str]:
        self._require_migration_preflight()
        normalized_kind = str(kind or "").strip().casefold()
        normalized_id = str(binding_id or "").strip()
        if not normalized_kind or not re.fullmatch(r"[a-z][a-z0-9_-]*", normalized_kind):
            raise ValueError("kind must be a non-empty lowercase identifier")
        if not normalized_id:
            raise ValueError("binding_id is required")

        def update(payload: dict[str, Any]) -> list[str]:
            changed: list[str] = []
            for repo_id, record in payload["repositories"].items():
                if not isinstance(record, dict):
                    continue
                existing = list(record.get("bindings") or [])
                kept = [
                    item
                    for item in existing
                    if not (
                        isinstance(item, dict)
                        and item.get("kind") == normalized_kind
                        and item.get("binding_id") == normalized_id
                    )
                ]
                if len(kept) == len(existing):
                    continue
                record["bindings"] = kept
                record["updated_at"] = _utc_now()
                changed.append(str(repo_id))
            return sorted(changed)

        return self._update_registry(update)

    def promote_agent_branch(
        self,
        managed_repo_id: str,
        agent_branch: str,
        target_branch: str,
        expected_commit_sha: str,
        directory_path: str,
    ) -> dict[str, Any]:
        source_branch = _normalize_branch(agent_branch, field_name="agent_branch")
        target = _normalize_branch(target_branch, field_name="target_branch")
        expected = _normalize_object_id(expected_commit_sha, field_name="expected_commit_sha")
        if not source_branch.startswith("agents/"):
            raise ValueError("agent_branch must be inside the agents/ namespace")
        if not target.startswith(("review/", "team/")):
            raise ValueError("target_branch must be inside the review/ or team/ namespace")
        source_ref = f"refs/heads/{source_branch}"
        target_ref = f"refs/heads/{target}"
        record = self._require_scoped_record(managed_repo_id, directory_path)
        bare_path = self._bare_path(record)
        operation_id = uuid4().hex
        operation_ref = f"refs/evander/promotions/{operation_id}"

        with (
            self._repo_lock(managed_repo_id, exclusive=False),
            self._target_lock(managed_repo_id, target_ref),
        ):
            current_source = self._resolve_ref(bare_path, source_ref)
            if current_source != expected:
                return {
                    "success": False,
                    "reason": "source_ref_moved",
                    "managed_repo_id": managed_repo_id,
                    "source_ref": source_ref,
                    "target_ref": target_ref,
                    "expected_commit_sha": expected,
                    "current_commit_sha": current_source,
                }
            self._require_commit(bare_path, expected)
            try:
                self._run_update_ref_transaction(
                    bare_path,
                    [
                        f"verify {source_ref} {expected}",
                        f"create {operation_ref} {expected}",
                    ],
                )
            except RuntimeError:
                moved_to = self._resolve_ref(bare_path, source_ref)
                if moved_to != expected:
                    return {
                        "success": False,
                        "reason": "source_ref_moved",
                        "managed_repo_id": managed_repo_id,
                        "source_ref": source_ref,
                        "target_ref": target_ref,
                        "expected_commit_sha": expected,
                        "current_commit_sha": moved_to,
                    }
                raise
            try:
                previous = self._resolve_ref(bare_path, target_ref)
                if previous and previous != expected:
                    if self._is_ancestor(bare_path, expected, previous):
                        return {
                            "success": False,
                            "reason": "target_already_ahead",
                            "managed_repo_id": managed_repo_id,
                            "source_ref": source_ref,
                            "target_ref": target_ref,
                            "expected_commit_sha": expected,
                            "current_commit_sha": previous,
                        }
                    if not self._is_ancestor(bare_path, previous, expected):
                        raise ManagedGitConflictError(
                            f"Target ref {target_ref} has diverged from the approved commit"
                        )
                if previous == expected:
                    changed = False
                elif previous:
                    self._run_update_ref_transaction(
                        bare_path, [f"update {target_ref} {expected} {previous}"]
                    )
                    changed = True
                else:
                    self._run_update_ref_transaction(bare_path, [f"create {target_ref} {expected}"])
                    changed = True
            finally:
                self._delete_ref_cas(bare_path, operation_ref, expected)
        return {
            "success": True,
            "managed_repo_id": managed_repo_id,
            "source_ref": source_ref,
            "target_ref": target_ref,
            "previous_commit_sha": previous,
            "commit_sha": expected,
            "changed": changed,
        }

    def read_pr_note(
        self,
        managed_repo_id: str,
        commit_sha: str,
        notes_ref: str,
        directory_path: str,
    ) -> dict[str, Any]:
        commit = _normalize_object_id(commit_sha, field_name="commit_sha")
        normalized_notes = _normalize_ref(notes_ref, field_name="notes_ref")
        if not normalized_notes.startswith("refs/notes/"):
            raise ValueError("notes_ref must be inside refs/notes/")
        record = self._require_scoped_record(managed_repo_id, directory_path)
        bare_path = self._bare_path(record)
        self._require_commit(bare_path, commit)
        result = self._run_git(
            [
                "git",
                f"--git-dir={bare_path}",
                "notes",
                f"--ref={normalized_notes}",
                "show",
                commit,
            ],
            check=False,
        )
        if result.returncode != 0:
            return {
                "managed_repo_id": managed_repo_id,
                "commit_sha": commit,
                "notes_ref": normalized_notes,
                "has_note": False,
                "body": None,
            }
        return {
            "managed_repo_id": managed_repo_id,
            "commit_sha": commit,
            "notes_ref": normalized_notes,
            "has_note": True,
            "body": result.stdout,
        }

    def push_branch(
        self,
        managed_repo_id: str,
        source_ref: str,
        expected_commit_sha: str,
        destination_branch: str,
        directory_path: str,
        *,
        _target_lock_held: bool = False,
    ) -> dict[str, Any]:
        normalized_source = _normalize_ref(source_ref, field_name="source_ref")
        if not normalized_source.startswith(("refs/heads/review/", "refs/heads/team/")):
            raise ValueError("source_ref must be a host-owned review or Team ref")
        destination = _normalize_branch(destination_branch, field_name="destination_branch")
        if destination.split("/", 1)[0] in _RESERVED_BRANCH_ROOTS:
            raise ValueError("destination_branch cannot use an Evander-reserved namespace")
        expected = _normalize_object_id(expected_commit_sha, field_name="expected_commit_sha")
        record = self._require_scoped_record(managed_repo_id, directory_path)
        bare_path = self._bare_path(record)
        destination_ref = f"refs/heads/{destination}"
        operation_id = uuid4().hex
        source_operation_ref = f"refs/evander/publication/{operation_id}/source"
        destination_operation_ref = f"refs/evander/publication/{operation_id}/destination"
        # Refresh may intentionally read from a trusted local checkout, but publication must
        # always address the canonical upstream recorded for the managed repository.
        remote_url = str(record["canonical_repository_url"])
        auth_env = self._git_environment(record)

        with (
            self._repo_lock(managed_repo_id, exclusive=False),
            nullcontext()
            if _target_lock_held
            else self._target_lock(managed_repo_id, f"upstream:{destination_ref}"),
        ):
            current_source = self._resolve_ref(bare_path, normalized_source)
            if current_source != expected:
                return {
                    "success": False,
                    "reason": "source_ref_moved",
                    "managed_repo_id": managed_repo_id,
                    "source_ref": normalized_source,
                    "destination_ref": destination_ref,
                    "expected_commit_sha": expected,
                    "current_commit_sha": current_source,
                }
            self._require_commit(bare_path, expected)
            try:
                self._run_update_ref_transaction(
                    bare_path,
                    [
                        f"verify {normalized_source} {expected}",
                        f"create {source_operation_ref} {expected}",
                    ],
                )
            except RuntimeError:
                moved_to = self._resolve_ref(bare_path, normalized_source)
                if moved_to != expected:
                    return {
                        "success": False,
                        "reason": "source_ref_moved",
                        "managed_repo_id": managed_repo_id,
                        "source_ref": normalized_source,
                        "destination_ref": destination_ref,
                        "expected_commit_sha": expected,
                        "current_commit_sha": moved_to,
                    }
                raise
            destination_commit: str | None = None
            try:
                fetch = self._run_git(
                    [
                        "git",
                        f"--git-dir={bare_path}",
                        "fetch",
                        "--no-tags",
                        "--no-write-fetch-head",
                        remote_url,
                        f"+{destination_ref}:{destination_operation_ref}",
                    ],
                    env=auth_env,
                    check=False,
                )
                if fetch.returncode == 0:
                    destination_commit = self._resolve_ref(bare_path, destination_operation_ref)
                else:
                    advertised = self._ls_remote_branch(remote_url, destination_ref, env=auth_env)
                    if advertised is not None:
                        self._raise_git_failure(fetch)
                if destination_commit and not self._is_ancestor(
                    bare_path, destination_commit, expected
                ):
                    counts = self._run_git(
                        [
                            "git",
                            f"--git-dir={bare_path}",
                            "rev-list",
                            "--left-right",
                            "--count",
                            f"{destination_operation_ref}...{source_operation_ref}",
                        ],
                        check=False,
                    )
                    destination_ahead_count = 0
                    source_ahead_count = 0
                    if counts.returncode == 0:
                        parts = counts.stdout.strip().split()
                        if len(parts) >= 2:
                            try:
                                destination_ahead_count = int(parts[0])
                                source_ahead_count = int(parts[1])
                            except ValueError:
                                pass
                    return {
                        "success": False,
                        "preflight_success": False,
                        "reason": "destination_branch_ahead",
                        "message": (
                            f"Destination branch {destination} has commits that are not "
                            "contained in the approved commit; merge the destination branch "
                            "before publishing."
                        ),
                        "managed_repo_id": managed_repo_id,
                        "source_ref": normalized_source,
                        "destination_branch": destination,
                        "destination_ref": destination_ref,
                        "expected_commit_sha": expected,
                        "destination_commit": destination_commit,
                        "local_commit": expected,
                        "destination_ahead_count": destination_ahead_count,
                        "local_ahead_count": source_ahead_count,
                    }
                lease = (
                    f"--force-with-lease={destination_ref}:{destination_commit}"
                    if destination_commit
                    else f"--force-with-lease={destination_ref}:"
                )
                self._run_git(
                    [
                        "git",
                        f"--git-dir={bare_path}",
                        "push",
                        lease,
                        remote_url,
                        f"{expected}:{destination_ref}",
                    ],
                    env=auth_env,
                )
            finally:
                if destination_commit:
                    self._delete_ref_cas(bare_path, destination_operation_ref, destination_commit)
                self._delete_ref_cas(bare_path, source_operation_ref, expected)
        return {
            "success": True,
            "managed_repo_id": managed_repo_id,
            "source_ref": normalized_source,
            "destination_ref": destination_ref,
            "previous_commit_sha": destination_commit,
            "commit_sha": expected,
        }

    def pr_create(
        self,
        *,
        managed_repo_id: str,
        source_ref: str,
        expected_commit_sha: str,
        destination_branch: str,
        directory_path: str,
        base_branch: str | None = None,
        session_id: str | None = None,
        work_item_id: str | None = None,
        title: str | None = None,
        body: str | None = None,
        draft: bool | None = None,
        fill: bool = True,
    ) -> dict[str, Any]:
        record = self._require_scoped_record(managed_repo_id, directory_path)
        destination = _normalize_branch(destination_branch, field_name="destination_branch")
        base = _normalize_branch(
            base_branch or str(record.get("default_branch") or "main"),
            field_name="base_branch",
        )
        expected = _normalize_object_id(expected_commit_sha, field_name="expected_commit_sha")
        normalized_session_id = str(session_id or "").strip()
        normalized_work_item_id = str(work_item_id or "").strip()
        normalized_title = str(title or "").strip() or f"Merge {destination} into {base}"
        title_explicit = bool(str(title or "").strip())
        normalized_body = str(body or "")
        provider = str(record.get("provider") or "").strip()
        if provider not in {"github", "azure_devops"}:
            raise ValueError(
                f"Managed PR creation is unsupported for provider {provider or 'generic'}"
            )
        tracking_session = self._resolve_pr_tracking_session(normalized_session_id)
        if provider == "azure_devops":
            normalized_work_item_id = self._azure_work_item_id(
                normalized_work_item_id,
                normalized_session_id,
            )
        destination_ref = f"refs/heads/{destination}"
        with self._target_lock(managed_repo_id, f"upstream:{destination_ref}"):
            published = self.push_branch(
                managed_repo_id,
                source_ref,
                expected,
                destination,
                directory_path,
                _target_lock_held=True,
            )
            if not published.get("success"):
                return dict(published)
            if provider == "github":
                pr = self._create_or_reuse_github_pr(
                    record,
                    destination_branch=destination,
                    base_branch=base,
                    expected_commit_sha=expected,
                    title=normalized_title,
                    body=normalized_body,
                    draft=bool(draft),
                    fill=fill,
                )
            elif provider == "azure_devops":
                pr = self._create_or_reuse_azure_pr(
                    record,
                    destination_branch=destination,
                    base_branch=base,
                    expected_commit_sha=expected,
                    title=normalized_title,
                    body=normalized_body,
                    draft=bool(draft),
                    title_explicit=title_explicit,
                    work_item_id=normalized_work_item_id,
                )
            self._persist_pr_tracking(
                record,
                pr=pr,
                source_branch=destination,
                target_branch=base,
                session_id=normalized_session_id,
                directory_path=directory_path,
                tracking_session=tracking_session,
            )
        return {
            "success": True,
            "managed_repo_id": managed_repo_id,
            "source_ref": _normalize_ref(source_ref, field_name="source_ref"),
            "destination_branch": destination,
            "expected_commit_sha": expected,
            "base_branch": base,
            "session_id": normalized_session_id or None,
            "work_item_id": normalized_work_item_id or None,
            **pr,
        }

    def _create_or_reuse_github_pr(
        self,
        record: Mapping[str, Any],
        *,
        destination_branch: str,
        base_branch: str,
        expected_commit_sha: str,
        title: str,
        body: str,
        draft: bool,
        fill: bool,
    ) -> dict[str, Any]:
        slug = self._github_slug(str(record["canonical_repository_url"]))
        env = self._git_environment(record)
        env.pop("GH_TOKEN", None)
        env.pop("GITHUB_TOKEN", None)
        _username, token = self._provider_credentials(record)
        if not token:
            raise RuntimeError("GitHub PR credentials are unavailable for this directory")
        env["GH_TOKEN"] = token
        listed = self._run_git(
            [
                "gh",
                "pr",
                "list",
                "--repo",
                slug,
                "--head",
                destination_branch,
                "--base",
                base_branch,
                "--state",
                "open",
                "--limit",
                "1",
                "--json",
                "number,url,title,headRefOid",
            ],
            env=env,
        )
        existing = json.loads(listed.stdout or "[]")
        if existing:
            item = existing[0]
            if not isinstance(item, Mapping):
                raise RuntimeError("GitHub PR API returned an invalid response")
            self._verify_provider_pr_source_commit(
                provider="GitHub",
                expected_commit_sha=expected_commit_sha,
                actual_commit_sha=item.get("headRefOid"),
            )
            return {
                "provider": "github",
                "pr_id": str(item.get("number") or ""),
                "pr_url": item.get("url"),
                "created": False,
            }
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", suffix=".md", delete=False
        ) as handle:
            handle.write(body)
            body_path = Path(handle.name)
        try:
            command = [
                "gh",
                "pr",
                "create",
                "--repo",
                slug,
                "--head",
                destination_branch,
                "--base",
                base_branch,
                "--title",
                title,
                "--body-file",
                str(body_path),
            ]
            if draft:
                command.append("--draft")
            created = self._run_git(command, env=env)
        finally:
            body_path.unlink(missing_ok=True)
        url = next(
            (line.strip() for line in created.stdout.splitlines() if line.startswith("http")),
            None,
        )
        confirmed = self._run_git(
            [
                "gh",
                "pr",
                "view",
                url or destination_branch,
                "--repo",
                slug,
                "--json",
                "number,url,headRefOid",
            ],
            env=env,
        )
        confirmed_item = json.loads(confirmed.stdout or "{}")
        if not isinstance(confirmed_item, Mapping):
            raise RuntimeError("GitHub PR API returned an invalid response")
        try:
            self._verify_provider_pr_source_commit(
                provider="GitHub",
                expected_commit_sha=expected_commit_sha,
                actual_commit_sha=confirmed_item.get("headRefOid"),
            )
        except ManagedGitConflictError as mismatch:
            created_identifier = str(
                confirmed_item.get("number") or confirmed_item.get("url") or url or ""
            ).strip()
            cleanup = self._run_git(
                [
                    "gh",
                    "pr",
                    "close",
                    created_identifier,
                    "--repo",
                    slug,
                ],
                env=env,
                check=False,
            )
            if not created_identifier or cleanup.returncode != 0:
                raise ManagedGitConflictError(
                    "GitHub pull request source moved and the newly created pull request "
                    "could not be closed",
                    details={**mismatch.details, "cleanup_failed": True},
                ) from mismatch
            raise
        return {
            "provider": "github",
            "pr_id": str(confirmed_item.get("number") or "") or None,
            "pr_url": confirmed_item.get("url") or url,
            "created": True,
        }

    def _create_or_reuse_azure_pr(
        self,
        record: Mapping[str, Any],
        *,
        destination_branch: str,
        base_branch: str,
        expected_commit_sha: str,
        title: str,
        body: str,
        draft: bool,
        title_explicit: bool = True,
        work_item_id: str = "",
    ) -> dict[str, Any]:
        organization, project, repository = self._azure_repository_parts(
            str(record["canonical_repository_url"])
        )
        resolved_config = self._resolved_config_for_record(record)
        pat_env = (
            str(getattr(resolved_config, "azure_devops_pr_pat_env", "") or "").strip()
            or "AZURE_DEVOPS_PR_PAT"
        )
        token = os.environ.get(pat_env, "").strip()
        if not token:
            raise RuntimeError(f"Azure DevOps PR credentials are unavailable in {pat_env}")
        api_root = (
            "https://dev.azure.com/"
            f"{quote(organization, safe='')}/{quote(project, safe='')}/_apis/git/"
            f"repositories/{quote(repository, safe='')}/pullrequests"
        )
        query = (
            "?searchCriteria.status=active"
            f"&searchCriteria.sourceRefName={quote(f'refs/heads/{destination_branch}', safe='')}"
            f"&searchCriteria.targetRefName={quote(f'refs/heads/{base_branch}', safe='')}"
            "&api-version=7.1"
        )
        auth = "Basic " + base64.b64encode(f":{token}".encode()).decode()
        bounded_body, truncation = self._bounded_azure_pr_description(body)
        existing = self._azure_request_json(api_root + query, auth=auth)
        values = existing.get("value") if isinstance(existing, dict) else None
        if isinstance(values, list) and values:
            item = values[0]
            if not isinstance(item, Mapping):
                raise RuntimeError("Azure DevOps PR API returned an invalid response")
            self._confirm_existing_azure_pr_source_commit(
                api_root=api_root,
                pr=item,
                expected_commit_sha=expected_commit_sha,
                auth=auth,
            )
            pr_id = str(item.get("pullRequestId") or "").strip()
            if not pr_id:
                raise RuntimeError("Azure DevOps PR API returned no pull request ID")
            update: dict[str, Any] = {"description": bounded_body}
            if title_explicit:
                update["title"] = title
            updated_item = self._azure_request_json(
                f"{api_root}/{quote(pr_id, safe='')}?api-version=7.1",
                auth=auth,
                payload=update,
                method="PATCH",
            )
            item = {**dict(item), **updated_item}
            link_result = self._ensure_azure_work_item_link(
                item,
                organization=organization,
                project=project,
                work_item_id=work_item_id,
                auth=auth,
            )
            return {
                "provider": "azure_devops",
                "pr_id": pr_id,
                "pr_url": self._azure_pr_web_url(item),
                "created": False,
                **truncation,
                **link_result,
            }
        created = self._azure_request_json(
            api_root + "?api-version=7.1",
            auth=auth,
            payload={
                "sourceRefName": f"refs/heads/{destination_branch}",
                "targetRefName": f"refs/heads/{base_branch}",
                "title": title,
                "description": bounded_body,
                "isDraft": draft,
            },
        )
        source_commit = created.get("lastMergeSourceCommit")
        try:
            self._verify_provider_pr_source_commit(
                provider="Azure DevOps",
                expected_commit_sha=expected_commit_sha,
                actual_commit_sha=(
                    source_commit.get("commitId") if isinstance(source_commit, Mapping) else None
                ),
            )
        except ManagedGitConflictError as mismatch:
            created_id = str(created.get("pullRequestId") or "").strip()
            try:
                if not created_id:
                    raise RuntimeError("Azure DevOps did not return a pull request ID")
                self._azure_request_json(
                    f"{api_root}/{quote(created_id, safe='')}?api-version=7.1",
                    auth=auth,
                    payload={"status": "abandoned"},
                    method="PATCH",
                )
            except Exception as cleanup_error:
                raise ManagedGitConflictError(
                    "Azure DevOps pull request source moved and the newly created pull "
                    "request could not be abandoned",
                    details={**mismatch.details, "cleanup_failed": True},
                ) from cleanup_error
            raise
        link_result = self._ensure_azure_work_item_link(
            created,
            organization=organization,
            project=project,
            work_item_id=work_item_id,
            auth=auth,
        )
        return {
            "provider": "azure_devops",
            "pr_id": str(created.get("pullRequestId") or ""),
            "pr_url": self._azure_pr_web_url(created),
            "created": True,
            **truncation,
            **link_result,
        }

    def _confirm_existing_azure_pr_source_commit(
        self,
        *,
        api_root: str,
        pr: Mapping[str, Any],
        expected_commit_sha: str,
        auth: str,
    ) -> None:
        source_commit = pr.get("lastMergeSourceCommit")
        try:
            self._verify_provider_pr_source_commit(
                provider="Azure DevOps",
                expected_commit_sha=expected_commit_sha,
                actual_commit_sha=(
                    source_commit.get("commitId") if isinstance(source_commit, Mapping) else None
                ),
            )
            return
        except ManagedGitConflictError as mismatch:
            latest_mismatch = mismatch

        pr_id = str(pr.get("pullRequestId") or "").strip()
        if not pr_id:
            raise RuntimeError("Azure DevOps PR API returned no pull request ID")
        iterations_url = (
            f"{api_root}/{quote(pr_id, safe='')}/iterations?api-version=7.1"
        )
        deadline = time.monotonic() + _AZURE_PR_SOURCE_CONFIRMATION_TIMEOUT_SECONDS
        while True:
            remaining_seconds = deadline - time.monotonic()
            if remaining_seconds <= 0:
                raise latest_mismatch
            iterations = self._azure_request_json(
                iterations_url,
                auth=auth,
                timeout_seconds=remaining_seconds,
            )
            current_commit_sha = self._latest_azure_pr_iteration_source_commit(iterations)
            try:
                self._verify_provider_pr_source_commit(
                    provider="Azure DevOps",
                    expected_commit_sha=expected_commit_sha,
                    actual_commit_sha=current_commit_sha,
                )
                return
            except ManagedGitConflictError as mismatch:
                latest_mismatch = mismatch
            remaining_seconds = deadline - time.monotonic()
            if remaining_seconds <= 0:
                raise latest_mismatch
            time.sleep(
                min(
                    _AZURE_PR_SOURCE_CONFIRMATION_INTERVAL_SECONDS,
                    remaining_seconds,
                )
            )

    @staticmethod
    def _latest_azure_pr_iteration_source_commit(
        iterations: Mapping[str, Any],
    ) -> str | None:
        values = iterations.get("value")
        if not isinstance(values, list):
            return None
        latest: tuple[int, Mapping[str, Any]] | None = None
        for item in values:
            if not isinstance(item, Mapping):
                continue
            try:
                iteration_id = int(str(item.get("id") or "").strip())
            except ValueError:
                continue
            if latest is None or iteration_id > latest[0]:
                latest = (iteration_id, item)
        if latest is None:
            return None
        source_commit = latest[1].get("sourceRefCommit")
        if not isinstance(source_commit, Mapping):
            return None
        return str(source_commit.get("commitId") or "").strip() or None

    @staticmethod
    def _bounded_azure_pr_description(body: str) -> tuple[str, dict[str, Any]]:
        original_length = len(body)
        truncated = original_length > _AZURE_PR_DESCRIPTION_MAX_CHARS
        bounded = body[:_AZURE_PR_DESCRIPTION_MAX_CHARS] if truncated else body
        return bounded, {
            "description_truncated": truncated,
            "description_original_length": original_length,
            "description_length": len(bounded),
        }

    @staticmethod
    def _azure_pr_web_url(pr: Mapping[str, Any]) -> Any:
        links = pr.get("_links")
        web = links.get("web") if isinstance(links, Mapping) else None
        href = web.get("href") if isinstance(web, Mapping) else None
        return href or pr.get("url")

    @staticmethod
    def _azure_work_item_id(explicit: str, session_id: str) -> str:
        for candidate in (explicit, session_id):
            normalized = str(candidate or "").strip()
            if normalized.isdigit():
                return normalized
            match = re.match(r"^(?:vm-|issue-)([0-9]+)(?:-|$)", normalized)
            if match:
                return match.group(1)
        return ""

    def _ensure_azure_work_item_link(
        self,
        pr: Mapping[str, Any],
        *,
        organization: str,
        project: str,
        work_item_id: str,
        auth: str,
    ) -> dict[str, Any]:
        if not work_item_id:
            return {"work_item_linked": False}
        repository = pr.get("repository")
        repository_id = (
            str(repository.get("id") or "").strip() if isinstance(repository, Mapping) else ""
        )
        project_info = repository.get("project") if isinstance(repository, Mapping) else None
        project_id = (
            str(project_info.get("id") or "").strip() if isinstance(project_info, Mapping) else ""
        )
        pr_id = str(pr.get("pullRequestId") or "").strip()
        if not repository_id or not project_id or not pr_id:
            return {
                "work_item_linked": False,
                "work_item_link_warning": (
                    "Azure DevOps PR response omitted repository identity required for "
                    "work-item association"
                ),
            }
        artifact_url = f"vstfs:///Git/PullRequestId/{project_id}%2F{repository_id}%2F{pr_id}"
        work_item_url = (
            "https://dev.azure.com/"
            f"{quote(organization, safe='')}/{quote(project, safe='')}/_apis/wit/"
            f"workitems/{quote(work_item_id, safe='')}"
        )
        try:
            item = self._azure_request_json(
                f"{work_item_url}?$expand=relations&api-version=7.1",
                auth=auth,
            )
            relations = item.get("relations")
            if isinstance(relations, list) and any(
                isinstance(relation, Mapping)
                and relation.get("rel") == "ArtifactLink"
                and relation.get("url") == artifact_url
                for relation in relations
            ):
                return {"work_item_linked": True}
            self._azure_request_json(
                f"{work_item_url}?api-version=7.1",
                auth=auth,
                payload=[
                    {
                        "op": "add",
                        "path": "/relations/-",
                        "value": {
                            "rel": "ArtifactLink",
                            "url": artifact_url,
                            "attributes": {"name": "Pull Request"},
                        },
                    }
                ],
                method="PATCH",
                content_type="application/json-patch+json",
            )
        except Exception as exc:
            return {
                "work_item_linked": False,
                "work_item_link_warning": str(exc),
            }
        return {"work_item_linked": True}

    def _persist_pr_tracking(
        self,
        record: Mapping[str, Any],
        *,
        pr: Mapping[str, Any],
        source_branch: str,
        target_branch: str,
        session_id: str,
        directory_path: str,
        tracking_session: tuple[Any, Any] | None = None,
    ) -> None:
        provider = str(pr.get("provider") or record.get("provider") or "").strip()
        pr_id = str(pr.get("pr_id") or "").strip()
        if not provider or not pr_id:
            raise RuntimeError("Pull request provider did not return tracking identity")
        canonical_url = str(record.get("canonical_repository_url") or "")
        if provider == "github":
            repo = self._github_slug(canonical_url)
        elif provider == "azure_devops":
            repo = "/".join(self._azure_repository_parts(canonical_url))
        else:
            raise ValueError(f"Unsupported PR tracking provider: {provider}")
        if session_id and tracking_session is None:
            tracking_session = self._resolve_pr_tracking_session(session_id)
        tracking_directory_path = directory_path
        if session_id:
            assert tracking_session is not None
            tracking_directory_path = (
                str(tracking_session[1].directory_path or "").strip() or "root"
            )
        PrMapStore(self._pr_map_path()).upsert_binding(
            provider=provider,
            repo=repo,
            pr_id=pr_id,
            pr_url=str(pr.get("pr_url") or "").strip() or None,
            source_branch=source_branch,
            target_branch=target_branch,
            session_id=session_id,
            directory_path=tracking_directory_path,
        )
        if not session_id:
            return
        assert tracking_session is not None
        repository, manifest = tracking_session
        stream = {"provider": provider, "repo": repo, "pr_id": pr_id}
        metadata = dict(manifest.metadata)
        streams = metadata.get("pr_feedback_streams")
        streams = list(streams) if isinstance(streams, list) else []
        if stream not in streams:
            streams.append(stream)
        metadata["pr_feedback_streams"] = streams
        allowed_tools = list(manifest.allowed_tools)
        for tool in (
            ToolName.HAS_PENDING_FEEDBACK,
            ToolName.LIST_PULL_REQUESTS,
            ToolName.GET_PULL_REQUEST,
            ToolName.LIST_UNREAD_FEEDBACK,
            ToolName.LIST_ALL_FEEDBACK,
            ToolName.POST_PR_FEEDBACK_REPLY,
        ):
            if tool not in allowed_tools:
                allowed_tools.append(tool)
        repository.upsert_session(
            manifest.model_copy(update={"metadata": metadata, "allowed_tools": allowed_tools})
        )

    def _resolve_pr_tracking_session(self, session_id: str) -> tuple[Any, Any] | None:
        if not session_id:
            return None
        repository = getattr(self.runtime, "session_repository", None)
        if repository is None:
            repository = JsonSessionManifestRepository(self._session_store_path())
        manifest = repository.get_session(session_id)
        if manifest is None:
            raise ValueError(f"Unknown session for PR feedback tracking: {session_id}")
        return repository, manifest

    def _pr_map_path(self) -> Path:
        configured = getattr(self.config, "pr_map_path", None)
        if configured:
            path = Path(configured).expanduser()
            if not path.is_absolute():
                path = self.project_dir / path
            return path.resolve(strict=False)
        return (self.runtime_dir / "pr-map.json").resolve(strict=False)

    @staticmethod
    def _verify_provider_pr_source_commit(
        *,
        provider: str,
        expected_commit_sha: str,
        actual_commit_sha: Any,
    ) -> None:
        expected = _normalize_object_id(
            expected_commit_sha,
            field_name="expected_commit_sha",
        )
        try:
            actual = _normalize_object_id(
                str(actual_commit_sha or ""),
                field_name="provider_pr_source_commit_sha",
            )
        except ValueError:
            raise ManagedGitConflictError(
                f"{provider} pull request source commit could not be verified",
                details={
                    "reason": "provider_pr_source_commit_unverified",
                    "provider": provider,
                    "expected_commit_sha": expected,
                    "current_commit_sha": None,
                },
            ) from None
        if actual != expected:
            raise ManagedGitConflictError(
                f"{provider} pull request source branch moved before confirmation",
                details={
                    "reason": "source_ref_moved",
                    "provider": provider,
                    "expected_commit_sha": expected,
                    "current_commit_sha": actual,
                },
            )

    @staticmethod
    def _azure_request_json(
        url: str,
        *,
        auth: str,
        payload: Mapping[str, Any] | list[Mapping[str, Any]] | None = None,
        method: str | None = None,
        content_type: str = "application/json",
        timeout_seconds: float = 30.0,
    ) -> dict[str, Any]:
        data = json.dumps(payload).encode() if payload is not None else None
        request = Request(
            url,
            data=data,
            method=method or ("POST" if data is not None else "GET"),
            headers={
                "Accept": "application/json",
                "Authorization": auth,
                "Content-Type": content_type,
            },
        )
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                parsed = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise RuntimeError(f"Azure DevOps PR request failed with HTTP {exc.code}") from exc
        except URLError as exc:
            raise RuntimeError("Azure DevOps PR request failed") from exc
        if not isinstance(parsed, dict):
            raise RuntimeError("Azure DevOps PR API returned an invalid response")
        return parsed

    @staticmethod
    def _github_slug(url: str) -> str:
        match = re.match(r"^git@github\.com:([^/]+/[^/]+?)(?:\.git)?$", url) or re.match(
            r"^https://github\.com/([^/]+/[^/]+?)(?:\.git)?$", url
        )
        if not match:
            raise ValueError("Managed GitHub repository URL is invalid")
        return match.group(1)

    @staticmethod
    def _azure_repository_parts(url: str) -> tuple[str, str, str]:
        match = (
            re.match(r"^git@ssh\.dev\.azure\.com:v3/([^/]+)/([^/]+)/([^/]+)$", url)
            or re.match(
                r"^https://(?:[^@/]+@)?dev\.azure\.com/([^/]+)/([^/]+)/_git/([^/]+)$",
                url,
            )
            or re.match(
                r"^https://(?:[^@/]+@)?([^.]+)\.visualstudio\.com/([^/]+)/_git/([^/]+)$",
                url,
            )
        )
        if not match:
            raise ValueError("Managed Azure DevOps repository URL is invalid")
        organization, project, repository = (unquote(part) for part in match.groups())
        return organization, project, repository

    def consolidate_branches(
        self,
        managed_repo_id: str,
        source_branches: list[str],
        target_branch: str,
        *,
        directory_path: str,
        base_branch: str | None = None,
        expected_source_commits: Mapping[str, str] | None = None,
        allowed_changed_paths: list[str] | None = None,
    ) -> dict[str, Any]:
        normalized_sources = [
            _normalize_branch(branch, field_name="source_branches") for branch in source_branches
        ]
        if not normalized_sources:
            raise ValueError("source_branches is required")
        if len(set(normalized_sources)) != len(normalized_sources):
            raise ValueError("source_branches cannot contain duplicates")
        target = _normalize_branch(target_branch, field_name="target_branch")
        if not target.startswith(("review/", "team/")):
            raise ValueError("target_branch must be inside the review/ or team/ namespace")
        supplied_expected = dict(expected_source_commits or {})
        if set(supplied_expected) != set(normalized_sources):
            raise ValueError(
                "expected_source_commits must contain every source branch exactly once"
            )
        expected = {
            branch: _normalize_object_id(
                supplied_expected[branch],
                field_name=f"expected_source_commits.{branch}",
            )
            for branch in normalized_sources
        }
        normalized_base = (
            _normalize_branch(base_branch, field_name="base_branch") if base_branch else None
        )
        allowed_paths = (
            _manifest_path_names(allowed_changed_paths)
            if allowed_changed_paths is not None
            else None
        )
        record = self._require_scoped_record(managed_repo_id, directory_path)
        bare_path = self._bare_path(record)
        target_ref = f"refs/heads/{target}"
        operation_id = uuid4().hex
        operation_root = f"refs/evander/consolidations/{operation_id}"
        merged: list[str] = []
        merge_output: list[str] = []

        with (
            self._repo_lock(managed_repo_id, exclusive=False),
            self._target_lock(managed_repo_id, target_ref),
        ):
            previous_target = self._resolve_ref(bare_path, target_ref)
            if normalized_base:
                base_ref = f"refs/heads/{normalized_base}"
            elif previous_target:
                base_ref = target_ref
            else:
                base_ref = f"refs/heads/{record['default_branch']}"
            base_commit = self._resolve_ref(bare_path, base_ref)
            if base_commit is None:
                raise ManagedGitNotFoundError(
                    f"Unknown consolidation base ref {base_ref} in {managed_repo_id}"
                )
            current_sources = {
                branch: self._resolve_ref(bare_path, f"refs/heads/{branch}")
                for branch in normalized_sources
            }
            moved = [
                branch
                for branch in normalized_sources
                if current_sources[branch] != expected[branch]
            ]
            if moved:
                unexpected = sorted(
                    {
                        current
                        for branch in moved
                        if (current := current_sources[branch]) is not None
                    }
                )
                return {
                    "success": False,
                    "managed_repo_id": managed_repo_id,
                    "target_branch": target,
                    "merged_branches": [],
                    "commit_sha": None,
                    "conflict_branch": None,
                    "output": None,
                    "error": "One or more source branches moved after review",
                    "error_code": "scope_mismatch",
                    "unexpected_commit_shas": unexpected,
                    "moved_source_branches": moved,
                    "base_commit_sha": base_commit,
                    "source_manifests": {},
                    "tree_hash": None,
                }
            for commit in expected.values():
                self._require_commit(bare_path, commit)

            base_operation_ref = f"{operation_root}/base"
            pin_commands = [
                f"verify {base_ref} {base_commit}",
                f"create {base_operation_ref} {base_commit}",
            ]
            source_operation_refs: dict[str, str] = {}
            for index, branch in enumerate(normalized_sources):
                source_ref = f"refs/heads/{branch}"
                operation_ref = f"{operation_root}/sources/{index}"
                source_operation_refs[branch] = operation_ref
                pin_commands.extend(
                    [
                        f"verify {source_ref} {expected[branch]}",
                        f"create {operation_ref} {expected[branch]}",
                    ]
                )
            try:
                self._run_update_ref_transaction(bare_path, pin_commands)
            except RuntimeError:
                repinned_sources = {
                    branch: self._resolve_ref(bare_path, f"refs/heads/{branch}")
                    for branch in normalized_sources
                }
                moved_during_pin = [
                    branch
                    for branch in normalized_sources
                    if repinned_sources[branch] != expected[branch]
                ]
                current_base = self._resolve_ref(bare_path, base_ref)
                if moved_during_pin or current_base != base_commit:
                    return {
                        "success": False,
                        "managed_repo_id": managed_repo_id,
                        "target_branch": target,
                        "merged_branches": [],
                        "commit_sha": None,
                        "conflict_branch": None,
                        "output": None,
                        "error": "The consolidation base or a source branch moved while pinning",
                        "error_code": "scope_mismatch",
                        "unexpected_commit_shas": sorted(
                            {
                                current
                                for branch in moved_during_pin
                                if (current := repinned_sources[branch]) is not None
                            }
                        ),
                        "moved_source_branches": moved_during_pin,
                        "base_ref_moved": current_base != base_commit,
                        "base_commit_sha": base_commit,
                        "source_manifests": {},
                        "tree_hash": None,
                    }
                raise
            try:
                source_manifests: dict[str, dict[str, Any]] = {}
                for branch in normalized_sources:
                    commits = self._git_output(
                        [
                            "git",
                            f"--git-dir={bare_path}",
                            "rev-list",
                            "--reverse",
                            f"{base_commit}..{expected[branch]}",
                        ]
                    )
                    changed_paths = self._git_output(
                        [
                            "git",
                            f"--git-dir={bare_path}",
                            "diff",
                            "--name-status",
                            f"{base_commit}...{expected[branch]}",
                            "--",
                        ]
                    )
                    source_tree = self._git_output(
                        [
                            "git",
                            f"--git-dir={bare_path}",
                            "rev-parse",
                            f"{expected[branch]}^{{tree}}",
                        ]
                    ).strip()
                    source_manifests[branch] = {
                        "base_commit_sha": base_commit,
                        "reviewed_commit_sha": expected[branch],
                        "tree_hash": source_tree,
                        "commit_shas": [line for line in commits.splitlines() if line],
                        "changed_paths": [line for line in changed_paths.splitlines() if line],
                    }
                if allowed_paths is not None:
                    manifest_paths: set[str] = set()
                    for manifest in source_manifests.values():
                        manifest_paths.update(_manifest_path_names(manifest["changed_paths"]))
                    unexpected_paths = sorted(manifest_paths - allowed_paths)
                    if unexpected_paths:
                        return {
                            "success": False,
                            "managed_repo_id": managed_repo_id,
                            "target_branch": target,
                            "merged_branches": [],
                            "commit_sha": None,
                            "conflict_branch": None,
                            "output": None,
                            "error": "Consolidation changes paths outside the reviewed manifest",
                            "error_code": "scope_mismatch",
                            "unexpected_paths": unexpected_paths,
                            "unexpected_commit_shas": [],
                            "base_commit_sha": base_commit,
                            "source_manifests": source_manifests,
                            "tree_hash": None,
                        }
                with self.temporary_worktree(
                    managed_repo_id,
                    source_ref=base_operation_ref,
                    commit_sha=base_commit,
                    operation_id=f"consolidate-{operation_id}",
                    purpose="team-release-consolidation",
                ) as worktree:
                    identity = dict(record.get("host_commit_identity") or {})
                    merge_env = self._safe_worktree_environment()
                    merge_env.update(
                        {
                            "GIT_AUTHOR_NAME": str(
                                identity.get("name") or DEFAULT_HOST_COMMIT_NAME
                            ),
                            "GIT_AUTHOR_EMAIL": str(
                                identity.get("email") or DEFAULT_HOST_COMMIT_EMAIL
                            ),
                            "GIT_COMMITTER_NAME": str(
                                identity.get("name") or DEFAULT_HOST_COMMIT_NAME
                            ),
                            "GIT_COMMITTER_EMAIL": str(
                                identity.get("email") or DEFAULT_HOST_COMMIT_EMAIL
                            ),
                            "GIT_MERGE_AUTOEDIT": "no",
                        }
                    )
                    for branch in normalized_sources:
                        merge = self._run_git(
                            [
                                "git",
                                "-C",
                                str(worktree),
                                "merge",
                                "--no-ff",
                                "--no-edit",
                                source_operation_refs[branch],
                            ],
                            env=merge_env,
                            check=False,
                        )
                        merge_output.append((merge.stdout or "") + (merge.stderr or ""))
                        if merge.returncode != 0:
                            self._run_git(
                                ["git", "-C", str(worktree), "merge", "--abort"],
                                env=merge_env,
                                check=False,
                            )
                            return {
                                "success": False,
                                "managed_repo_id": managed_repo_id,
                                "target_branch": target,
                                "merged_branches": merged,
                                "commit_sha": None,
                                "conflict_branch": branch,
                                "output": self._sanitize_managed_worktree_error(
                                    "".join(merge_output).strip(),
                                    operation_dir=worktree.parent,
                                )
                                or None,
                                "error": f"Merge conflict while consolidating {branch}",
                                "error_code": "merge_conflict",
                                "base_commit_sha": base_commit,
                                "source_manifests": source_manifests,
                                "tree_hash": None,
                            }
                        merged.append(branch)
                    result_commit = self._git_output(
                        ["git", "-C", str(worktree), "rev-parse", "HEAD"]
                    ).strip()
                    result_tree_hash = self._git_output(
                        ["git", "-C", str(worktree), "rev-parse", "HEAD^{tree}"]
                    ).strip()
                    self._require_commit(bare_path, result_commit)
                    current_target = self._resolve_ref(bare_path, target_ref)
                    if current_target != previous_target:
                        raise ManagedGitConflictError(
                            f"Target ref {target_ref} moved during consolidation"
                        )
                    if previous_target and not self._is_ancestor(
                        bare_path, previous_target, result_commit
                    ):
                        raise ManagedGitConflictError(
                            f"Consolidation would regress or rewrite target ref {target_ref}"
                        )
                    if previous_target:
                        self._run_update_ref_transaction(
                            bare_path,
                            [f"update {target_ref} {result_commit} {previous_target}"],
                        )
                    else:
                        self._run_update_ref_transaction(
                            bare_path, [f"create {target_ref} {result_commit}"]
                        )
                sanitized_merge_output = self._sanitize_managed_worktree_error(
                    "".join(merge_output).strip(),
                    operation_dir=worktree.parent,
                )
                return {
                    "success": True,
                    "managed_repo_id": managed_repo_id,
                    "target_branch": target,
                    "merged_branches": merged,
                    "commit_sha": result_commit,
                    "conflict_branch": None,
                    "output": sanitized_merge_output or None,
                    "error": None,
                    "error_code": None,
                    "base_commit_sha": base_commit,
                    "source_manifests": source_manifests,
                    "tree_hash": result_tree_hash,
                }
            finally:
                self._delete_namespace(bare_path, operation_root)

    @contextmanager
    def temporary_worktree(
        self,
        managed_repo_id: str,
        *,
        commit_sha: str | None = None,
        source_ref: str | None = None,
        operation_id: str | None = None,
        purpose: str = "managed-git-operation",
    ) -> Iterator[Path]:
        with self._worktrees.temporary_worktree(
            managed_repo_id,
            commit_sha=commit_sha,
            source_ref=source_ref,
            operation_id=operation_id,
            purpose=purpose,
        ) as worktree:
            yield worktree

    def cleanup_active_worktrees(self) -> dict[str, int]:
        return self._worktrees.cleanup_active_worktrees()

    def reconcile_stale_worktrees(
        self,
        managed_repo_id: str | None = None,
        *,
        older_than_seconds: float | None = None,
    ) -> dict[str, Any]:
        return self._worktrees.reconcile_stale_worktrees(
            managed_repo_id,
            older_than_seconds=older_than_seconds,
        )

    def migrate(
        self,
        stage: str,
        migration_id: str | None = None,
    ) -> dict[str, Any]:
        normalized_stage = str(stage or "").strip().removeprefix("--").replace("_", "-")
        if normalized_stage == "dry-run":
            with self._migration_preflight_bypass():
                return self._migrate(normalized_stage, migration_id=migration_id)
        with self._file_lock(self._lock_root / "migration.lock"):
            with self._migration_worker_lock_guard():
                with self._migration_preflight_bypass():
                    return self._migrate(normalized_stage, migration_id=migration_id)

    def _migrate(
        self,
        stage: str,
        migration_id: str | None = None,
    ) -> dict[str, Any]:
        normalized_stage = str(stage or "").strip().removeprefix("--").replace("_", "-")
        if normalized_stage not in {"dry-run", "apply", "finalize"}:
            raise ValueError("stage must be dry-run, apply, or finalize")
        if normalized_stage == "finalize":
            if not migration_id:
                raise ValueError("migration_id is required for finalize")
            return self._finalize_migration(str(migration_id))

        inventory = self._legacy_inventory()
        blockers = self._migration_blockers(inventory)
        if normalized_stage == "dry-run":
            return {
                "stage": "dry-run",
                "can_apply": not blockers,
                "blockers": blockers,
                "inventory": inventory,
            }
        if blockers:
            raise ManagedGitConflictError(
                "Managed Git migration is blocked: " + "; ".join(blockers),
                details={"blockers": blockers},
            )
        recovered = self._recover_incomplete_migration()
        if recovered is not None:
            return recovered
        restartable = self._find_restartable_applied_migration()
        if restartable is not None:
            return restartable
        verification_hash = hashlib.sha256(
            json.dumps(inventory, sort_keys=True).encode("utf-8")
        ).hexdigest()
        new_migration_id = migration_id or f"mgm_{verification_hash[:20]}"
        if not _SAFE_OPERATION_ID.fullmatch(new_migration_id):
            raise ValueError("migration_id contains unsupported characters")
        manifest_path = self._migration_root / f"{new_migration_id}.json"
        if manifest_path.exists():
            existing_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if existing_manifest.get("schema") != "evander.managed-git-migration/v1":
                raise ValueError(f"Invalid managed Git migration manifest: {new_migration_id}")
            if existing_manifest.get("verification_hash") != verification_hash:
                raise ManagedGitConflictError(
                    f"Migration ID {new_migration_id} already exists with different input"
                )
            if existing_manifest.get("status") == "failed":
                manifest_path.unlink()
            else:
                self._verify_migration_manifest(existing_manifest, require_file_hashes=True)
                self._enforce_rollback_artifacts_read_only(existing_manifest)
                self._record_applied_migration(existing_manifest)
                return {
                    "stage": "apply",
                    "migration_id": new_migration_id,
                    "status": existing_manifest.get("status") or "applied",
                    "inventory": inventory,
                    "verification_hash": verification_hash,
                    "managed_repo_ids": list(existing_manifest.get("managed_repo_ids") or []),
                    "restartable": True,
                }

        candidates = self._legacy_candidates()
        mappings: list[dict[str, str]] = []
        managed_repo_ids: list[str] = []
        verification_refs: dict[str, dict[str, str]] = {}
        for directory_path, _identity, grouped_candidates in self._legacy_candidate_groups(
            candidates
        ):
            registration_source = str(grouped_candidates[0]["registration_source"])
            repository = self.register_repository(
                directory_path,
                registration_source,
                refresh=True,
                _canonical_source=(str(grouped_candidates[0].get("identity_source") or "") or None),
            )
            repo_id = str(repository["managed_repo_id"])
            if repo_id not in managed_repo_ids:
                managed_repo_ids.append(repo_id)
            record = self._require_record(repo_id)
            imported = self._import_legacy_preserved_refs(
                record,
                grouped_candidates,
                migration_id=new_migration_id,
            )
            verification_refs.setdefault(repo_id, {}).update(imported)
            for candidate in grouped_candidates:
                mappings.append(
                    {
                        "legacy_repository": str(candidate["path"]),
                        "managed_repo_id": repo_id,
                    }
                )
        mapping_records = self._migration_mapping_records(candidates, mappings)
        state_rewrites, bindings = self._build_migration_state_rewrites(
            mapping_records=mapping_records,
            migration_id=new_migration_id,
            workflow_inventory=list(inventory.get("workflow_pack_files_with_legacy_fields") or []),
        )
        rollback_artifacts = sorted(
            {
                str(candidate["path"])
                for candidate in candidates
                if self._is_evander_owned_rollback_path(Path(str(candidate["path"])))
            }
            | {
                str(path.resolve(strict=False))
                for pattern in ("*.git", "*-worktree")
                for path in (self.runtime_dir / "repo-cache").glob(pattern)
                if path.is_dir()
            }
        )
        manifest = {
            "schema": "evander.managed-git-migration/v1",
            "migration_id": new_migration_id,
            "status": "applied",
            "created_at": _utc_now(),
            "inventory": inventory,
            "managed_repo_ids": sorted(managed_repo_ids),
            "mappings": mappings,
            "mapping_details": mapping_records,
            "verification_refs": verification_refs,
            "rewritten_files": [
                self._migration_rewrite_manifest_record(rewrite) for rewrite in state_rewrites
            ],
            "bindings": bindings,
            "rollback_artifacts": rollback_artifacts,
            "verification_hash": verification_hash,
        }
        manifest["state_verification_hash"] = hashlib.sha256(
            json.dumps(manifest["rewritten_files"], sort_keys=True).encode("utf-8")
        ).hexdigest()
        manifest["status"] = "prepared"
        self._write_json_atomic(manifest_path, manifest, mode=0o600)
        try:
            self._apply_migration_file_rewrites(state_rewrites)
            self._record_migration_bindings(bindings)
        except BaseException as exc:
            self._rollback_migration_file_rewrites(state_rewrites)
            manifest["status"] = "failed"
            manifest["error"] = f"{type(exc).__name__}: {exc}"
            self._write_json_atomic(manifest_path, manifest, mode=0o600)
            raise
        self._enforce_rollback_artifacts_read_only(manifest)
        manifest["status"] = "applied"
        manifest["applied_at"] = _utc_now()
        self._write_json_atomic(manifest_path, manifest, mode=0o600)
        self._record_applied_migration(manifest)
        return {
            "stage": "apply",
            "migration_id": new_migration_id,
            "status": "applied",
            "inventory": inventory,
            "verification_hash": manifest["verification_hash"],
            "managed_repo_ids": sorted(managed_repo_ids),
            "mappings": mappings,
            "rewritten_files": list(manifest["rewritten_files"]),
            "restartable": True,
        }

    def _recover_incomplete_migration(self) -> dict[str, Any] | None:
        candidates: list[tuple[float, Path, dict[str, Any]]] = []
        for path in self._migration_root.glob("*.json"):
            try:
                manifest = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if manifest.get("schema") == "evander.managed-git-migration/v1" and manifest.get(
                "status"
            ) in {"prepared", "failed"}:
                candidates.append((path.stat().st_mtime, path, manifest))
        if not candidates:
            return None
        _mtime, manifest_path, manifest = max(candidates, key=lambda item: item[0])
        rewritten_files = [
            item for item in manifest.get("rewritten_files") or [] if isinstance(item, dict)
        ]
        states: dict[str, str] = {}
        for item in rewritten_files:
            path = Path(str(item.get("path") or "")).resolve(strict=False)
            if not path.is_file():
                raise ManagedGitConflictError(f"Incomplete migration input disappeared: {path}")
            if item.get("kind") == "team_state_db":
                actual = self._team_database_state_hash_path(path)
                before_hash = item.get("before_state_sha256")
                after_hash = item.get("after_state_sha256")
            else:
                actual = self._file_sha256(path)
                before_hash = item.get("before_sha256")
                after_hash = item.get("after_sha256")
            if actual == before_hash:
                states[str(path)] = "before"
            elif actual == after_hash:
                states[str(path)] = "after"
            else:
                raise ManagedGitConflictError(
                    f"Incomplete migration state changed unexpectedly: {path}"
                )
        if manifest.get("status") == "failed":
            if any(state != "before" for state in states.values()):
                raise ManagedGitConflictError(
                    "Failed managed Git migration could not be rolled back completely"
                )
            manifest_path.unlink()
            return None
        if any(state == "before" for state in states.values()):
            rewrites, _bindings = self._build_migration_state_rewrites(
                mapping_records=[
                    item for item in manifest.get("mapping_details") or [] if isinstance(item, dict)
                ],
                migration_id=str(manifest["migration_id"]),
                workflow_inventory=list(
                    (manifest.get("inventory") or {}).get("workflow_pack_files_with_legacy_fields")
                    or []
                ),
            )
            expected_by_path = {
                str(Path(str(item["path"])).resolve(strict=False)): item for item in rewritten_files
            }
            pending = [rewrite for rewrite in rewrites if states.get(str(rewrite.path)) == "before"]
            for rewrite in pending:
                expected = expected_by_path.get(str(rewrite.path))
                if rewrite.kind == "team_state_db":
                    reproduced_hash = self._team_database_state_hash_bytes(rewrite.after)
                    expected_hash = (expected or {}).get("after_state_sha256")
                else:
                    reproduced_hash = rewrite.after_sha256
                    expected_hash = (expected or {}).get("after_sha256")
                if expected is None or expected_hash != reproduced_hash:
                    raise ManagedGitConflictError(
                        f"Incomplete migration could not reproduce staged output: {rewrite.path}"
                    )
            self._apply_migration_file_rewrites(pending)
        self._record_migration_bindings(
            [item for item in manifest.get("bindings") or [] if isinstance(item, dict)]
        )
        self._enforce_rollback_artifacts_read_only(manifest)
        manifest["status"] = "applied"
        manifest["applied_at"] = _utc_now()
        manifest.pop("error", None)
        self._write_json_atomic(manifest_path, manifest, mode=0o600)
        self._record_applied_migration(manifest)
        self._verify_migration_manifest(manifest, require_file_hashes=True)
        return {
            "stage": "apply",
            "migration_id": str(manifest["migration_id"]),
            "status": "applied",
            "inventory": manifest.get("inventory") or {},
            "verification_hash": manifest.get("verification_hash"),
            "managed_repo_ids": list(manifest.get("managed_repo_ids") or []),
            "mappings": list(manifest.get("mappings") or []),
            "rewritten_files": rewritten_files,
            "restartable": True,
            "recovered": True,
        }

    def _find_restartable_applied_migration(self) -> dict[str, Any] | None:
        manifests: list[tuple[float, Path, dict[str, Any]]] = []
        for path in self._migration_root.glob("*.json"):
            try:
                manifest = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if (
                manifest.get("schema") != "evander.managed-git-migration/v1"
                or manifest.get("status") != "applied"
            ):
                continue
            manifests.append((path.stat().st_mtime, path, manifest))
        for _mtime, _path, manifest in sorted(manifests, reverse=True):
            try:
                self._verify_migration_manifest(manifest, require_file_hashes=True)
            except (ManagedGitConflictError, ManagedGitNotFoundError, OSError, ValueError):
                continue
            if self._legacy_managed_git_artifacts_present(allow_declared_rollback=True):
                raise ManagedGitConflictError(
                    "New or unresolved legacy managed Git state appeared after migration apply; "
                    "resolve it before retrying or finalizing the migration"
                )
            self._enforce_rollback_artifacts_read_only(manifest)
            self._record_applied_migration(manifest)
            return {
                "stage": "apply",
                "migration_id": str(manifest["migration_id"]),
                "status": "applied",
                "inventory": manifest.get("inventory") or {},
                "verification_hash": manifest.get("verification_hash"),
                "managed_repo_ids": list(manifest.get("managed_repo_ids") or []),
                "mappings": list(manifest.get("mappings") or []),
                "rewritten_files": list(manifest.get("rewritten_files") or []),
                "restartable": True,
            }
        return None

    def _migration_rewrite_manifest_record(
        self,
        rewrite: _MigrationFileRewrite,
    ) -> dict[str, Any]:
        record: dict[str, Any] = {
            "path": str(rewrite.path),
            "kind": rewrite.kind,
            "before_sha256": rewrite.before_sha256,
            "after_sha256": rewrite.after_sha256,
        }
        if rewrite.kind == "team_state_db":
            record["before_state_sha256"] = self._team_database_state_hash_bytes(rewrite.before)
            record["after_state_sha256"] = self._team_database_state_hash_bytes(rewrite.after)
        if rewrite.kind == "workflow_pack_live":
            record.update(
                {
                    "workflow_pack": rewrite.workflow_pack,
                    "before_revision": rewrite.workflow_before_revision,
                    "after_revision": rewrite.workflow_after_revision,
                }
            )
        return record

    def _require_workflow_rewrite_admin(self) -> Any:
        admin = self._workflow_config_admin
        if admin is None:
            raise ManagedGitConflictError(
                "Live workflow pack migration requires the workflow configuration admin service"
            )
        return admin

    def _preflight_workflow_editor_rewrite(
        self,
        rewrite: _MigrationFileRewrite,
    ) -> None:
        admin = self._require_workflow_rewrite_admin()
        workflow_pack = str(rewrite.workflow_pack or "").strip()
        expected_revision = str(rewrite.workflow_before_revision or "").strip()
        if not workflow_pack or not expected_revision:
            raise ManagedGitConflictError(
                f"Live workflow migration metadata is incomplete: {rewrite.path}"
            )
        current = admin.read_workflow_pack_for_migration(workflow_pack=workflow_pack)
        current_revision = str(self._workflow_payload_value(current, "revision", "") or "").strip()
        current_path = self._workflow_payload_value(current, "path")
        if current_path is None or Path(str(current_path)).resolve(
            strict=False
        ) != rewrite.path.resolve(strict=False):
            raise ManagedGitConflictError(
                f"Live workflow pack routing changed before migration: {workflow_pack}",
                details={"path": str(rewrite.path), "current_path": current_path},
            )
        if current_revision != expected_revision:
            raise ManagedGitConflictError(
                f"Live workflow pack changed revision before migration: {workflow_pack}",
                details={
                    "expected_revision": expected_revision,
                    "current_revision": current_revision,
                },
            )

    def _save_workflow_editor_rewrite(
        self,
        rewrite: _MigrationFileRewrite,
        *,
        rollback: bool = False,
    ) -> Any:
        admin = self._require_workflow_rewrite_admin()
        workflow_pack = str(rewrite.workflow_pack or "").strip()
        before_revision = str(rewrite.workflow_before_revision or "").strip()
        after_revision = str(rewrite.workflow_after_revision or "").strip()
        if not workflow_pack or not before_revision or not after_revision:
            raise ManagedGitConflictError(
                f"Live workflow migration metadata is incomplete: {rewrite.path}"
            )
        expected_revision = after_revision if rollback else before_revision
        content = rewrite.before if rollback else rewrite.after
        try:
            return admin.rewrite_workflow_pack_for_migration(
                expected_revision=expected_revision,
                workflow_pack=workflow_pack,
                content=content,
                operation=(
                    "managed_git_migration_rollback" if rollback else "managed_git_migration_apply"
                ),
                actor=WorkflowPackChangeActor(
                    login="system",
                    display_name="Managed Git migration rollback"
                    if rollback
                    else "Managed Git migration",
                ),
            )
        except AdminRevisionConflict as exc:
            action = "rollback" if rollback else "apply"
            raise ManagedGitConflictError(
                f"Live workflow pack changed concurrently during migration {action}: "
                f"{workflow_pack}",
                details={
                    "expected_revision": expected_revision,
                    **dict(exc.details),
                },
            ) from exc

    def _apply_migration_file_rewrites(self, rewrites: list[_MigrationFileRewrite]) -> None:
        for rewrite in rewrites:
            if not rewrite.path.is_file():
                raise ManagedGitConflictError(
                    f"Migration input disappeared before apply: {rewrite.path}"
                )
            if rewrite.kind == "team_state_db":
                current_state = self._team_database_state_hash_path(rewrite.path)
                expected_state = self._team_database_state_hash_bytes(rewrite.before)
                if current_state != expected_state:
                    raise ManagedGitConflictError(
                        f"Migration input changed concurrently: {rewrite.path}"
                    )
                continue
            if rewrite.kind == "workflow_pack_live":
                self._preflight_workflow_editor_rewrite(rewrite)
            current_hash = self._file_sha256(rewrite.path)
            if current_hash != rewrite.before_sha256:
                raise ManagedGitConflictError(
                    f"Migration input changed concurrently: {rewrite.path}",
                    details={
                        "expected_sha256": rewrite.before_sha256,
                        "actual_sha256": current_hash,
                    },
                )
        replaced: list[_MigrationFileRewrite] = []
        try:
            for rewrite in rewrites:
                if rewrite.kind == "team_state_db":
                    self._apply_team_database_state_transition(
                        rewrite.path,
                        before=rewrite.before,
                        after=rewrite.after,
                    )
                elif rewrite.kind == "workflow_pack_live":
                    saved = self._save_workflow_editor_rewrite(rewrite)
                    replaced.append(rewrite)
                    saved_revision = str(
                        self._workflow_payload_value(saved, "revision", "") or ""
                    ).strip()
                    if saved_revision != rewrite.workflow_after_revision:
                        raise ManagedGitConflictError(
                            f"Live workflow pack returned an unexpected revision: "
                            f"{rewrite.workflow_pack}",
                            details={
                                "expected_revision": rewrite.workflow_after_revision,
                                "actual_revision": saved_revision,
                            },
                        )
                    if self._file_sha256(rewrite.path) != rewrite.after_sha256:
                        raise ManagedGitConflictError(
                            f"Live workflow pack persisted unexpected content: "
                            f"{rewrite.workflow_pack}"
                        )
                    continue
                else:
                    self._write_bytes_atomic(rewrite.path, rewrite.after)
                replaced.append(rewrite)
        except Exception:
            for rewrite in reversed(replaced):
                try:
                    if rewrite.kind == "team_state_db":
                        self._apply_team_database_state_transition(
                            rewrite.path,
                            before=rewrite.after,
                            after=rewrite.before,
                        )
                    elif rewrite.kind == "workflow_pack_live":
                        self._save_workflow_editor_rewrite(rewrite, rollback=True)
                    else:
                        self._write_bytes_atomic(rewrite.path, rewrite.before)
                except (OSError, sqlite3.Error, ManagedGitConflictError):
                    pass
            raise

    def _rollback_migration_file_rewrites(self, rewrites: list[_MigrationFileRewrite]) -> None:
        for rewrite in reversed(rewrites):
            try:
                if not rewrite.path.is_file():
                    continue
                if rewrite.kind == "team_state_db":
                    if self._team_database_state_hash_path(
                        rewrite.path
                    ) == self._team_database_state_hash_bytes(rewrite.after):
                        self._apply_team_database_state_transition(
                            rewrite.path,
                            before=rewrite.after,
                            after=rewrite.before,
                        )
                elif rewrite.kind == "workflow_pack_live":
                    current = (
                        self._require_workflow_rewrite_admin().read_workflow_pack_for_migration(
                            workflow_pack=rewrite.workflow_pack
                        )
                    )
                    current_revision = str(
                        self._workflow_payload_value(current, "revision", "") or ""
                    ).strip()
                    if current_revision == rewrite.workflow_after_revision:
                        self._save_workflow_editor_rewrite(rewrite, rollback=True)
                elif self._file_sha256(rewrite.path) == rewrite.after_sha256:
                    self._write_bytes_atomic(rewrite.path, rewrite.before)
            except (OSError, sqlite3.Error, ManagedGitConflictError):
                continue

    @staticmethod
    def _write_bytes_atomic(path: Path, payload: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temp_path = Path(temp_name)
        try:
            os.fchmod(fd, mode)
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, path)
            directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            temp_path.unlink(missing_ok=True)

    @staticmethod
    def _team_database_git_rows(path: Path) -> dict[str, str | None]:
        if not path.is_file():
            return {}
        with sqlite3.connect(path, timeout=5.0) as connection:
            table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'team_projects'"
            ).fetchone()
            if table is None:
                return {}
            return {
                str(project_id): (str(git_json) if git_json is not None else None)
                for project_id, git_json in connection.execute(
                    "SELECT id, git_json FROM team_projects ORDER BY id"
                )
            }

    @classmethod
    def _team_database_state_hash_path(cls, path: Path) -> str:
        rows = cls._team_database_git_rows(path)
        return hashlib.sha256(
            json.dumps(rows, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    @classmethod
    def _team_database_state_hash_bytes(cls, payload: bytes) -> str:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "state.db"
            path.write_bytes(payload)
            return cls._team_database_state_hash_path(path)

    @classmethod
    def _apply_team_database_state_transition(
        cls,
        path: Path,
        *,
        before: bytes,
        after: bytes,
    ) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            before_path = Path(temp_dir) / "before.db"
            after_path = Path(temp_dir) / "after.db"
            before_path.write_bytes(before)
            after_path.write_bytes(after)
            before_rows = cls._team_database_git_rows(before_path)
            after_rows = cls._team_database_git_rows(after_path)
        changed_ids = sorted(
            project_id
            for project_id in set(before_rows) | set(after_rows)
            if before_rows.get(project_id) != after_rows.get(project_id)
        )
        if not changed_ids:
            return
        connection = sqlite3.connect(path, timeout=30.0, isolation_level=None)
        try:
            connection.execute("PRAGMA busy_timeout=30000")
            connection.execute("BEGIN IMMEDIATE")
            current_rows = {
                str(project_id): (str(git_json) if git_json is not None else None)
                for project_id, git_json in connection.execute(
                    "SELECT id, git_json FROM team_projects ORDER BY id"
                )
            }
            if any(
                current_rows.get(project_id) != before_rows.get(project_id)
                for project_id in changed_ids
            ):
                raise ManagedGitConflictError(f"Team migration input changed concurrently: {path}")
            for project_id in changed_ids:
                if project_id not in after_rows:
                    raise ManagedGitConflictError(
                        f"Team migration cannot delete project state: {project_id}"
                    )
                result = connection.execute(
                    "UPDATE team_projects SET git_json = ? WHERE id = ? AND git_json IS ?",
                    (after_rows[project_id], project_id, before_rows.get(project_id)),
                )
                if result.rowcount != 1:
                    raise ManagedGitConflictError(
                        f"Team migration project changed concurrently: {project_id}"
                    )
            now = _utc_now()
            has_metadata = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'team_state_metadata'"
            ).fetchone()
            if has_metadata is not None:
                connection.execute(
                    "UPDATE team_state_metadata "
                    "SET value = CAST(value AS INTEGER) + 1 WHERE key = 'revision'"
                )
                connection.execute(
                    "UPDATE team_state_metadata SET value = ? WHERE key = 'updated_at'",
                    (now,),
                )
            connection.execute("COMMIT")
        except BaseException:
            try:
                connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        finally:
            connection.close()

    def _record_migration_bindings(self, bindings: list[dict[str, str]]) -> None:
        if not bindings:
            return
        now = _utc_now()

        def update(payload: dict[str, Any]) -> None:
            for binding in bindings:
                repo_id = str(binding["managed_repo_id"])
                record = payload["repositories"].get(repo_id)
                if not isinstance(record, dict):
                    raise ManagedGitNotFoundError(f"Unknown managed repository: {repo_id}")
                existing = list(record.get("bindings") or [])
                if not any(
                    isinstance(item, dict)
                    and item.get("kind") == binding["kind"]
                    and item.get("binding_id") == binding["binding_id"]
                    for item in existing
                ):
                    existing.append(
                        {
                            "kind": binding["kind"],
                            "binding_id": binding["binding_id"],
                            "bound_at": now,
                        }
                    )
                    existing.sort(key=lambda item: (item["kind"], item["binding_id"]))
                    record["bindings"] = existing
                    record["updated_at"] = now

        self._update_registry(update)

    def _verify_migration_manifest(
        self,
        manifest: Mapping[str, Any],
        *,
        require_file_hashes: bool,
    ) -> None:
        if manifest.get("schema") != "evander.managed-git-migration/v1":
            raise ValueError("Invalid managed Git migration manifest")
        if str(manifest.get("status") or "") not in {"applied", "finalized"}:
            raise ManagedGitConflictError(
                f"Managed Git migration {manifest.get('migration_id')} is not fully applied"
            )
        rewritten_files = list(manifest.get("rewritten_files") or [])
        expected_state_hash = hashlib.sha256(
            json.dumps(rewritten_files, sort_keys=True).encode("utf-8")
        ).hexdigest()
        recorded_state_hash = manifest.get("state_verification_hash")
        if recorded_state_hash is not None and recorded_state_hash != expected_state_hash:
            raise ManagedGitConflictError("Managed Git migration file manifest hash is invalid")

        records: dict[str, dict[str, Any]] = {}
        for repo_id in manifest.get("managed_repo_ids") or []:
            record = self._require_record(str(repo_id))
            records[str(repo_id)] = record
            bare_path = self._bare_path(record)
            expected_refs = (manifest.get("verification_refs") or {}).get(str(repo_id), {})
            for ref_name, expected_object_id in expected_refs.items():
                if self._resolve_ref(bare_path, str(ref_name)) != str(expected_object_id):
                    raise ManagedGitConflictError(
                        f"Managed repository {repo_id} failed migration ref verification",
                        details={"ref_name": ref_name},
                    )

        for mapping in manifest.get("mapping_details") or []:
            if not isinstance(mapping, dict):
                continue
            repo_id = str(mapping.get("managed_repo_id") or "")
            record = records.get(repo_id) or self._require_record(repo_id)
            if record.get("canonical_repository_identity") != mapping.get(
                "canonical_repository_identity"
            ):
                raise ManagedGitConflictError(
                    f"Managed repository {repo_id} canonical identity changed after migration"
                )

        for file_record in rewritten_files:
            if not isinstance(file_record, dict):
                raise ManagedGitConflictError("Managed Git migration file record is invalid")
            path = Path(str(file_record.get("path") or "")).resolve(strict=False)
            if not path.is_file():
                raise ManagedGitConflictError(f"Migrated state file is missing: {path}")
            if require_file_hashes:
                if file_record.get("kind") == "team_state_db":
                    actual_hash = self._team_database_state_hash_path(path)
                    expected_hash = file_record.get("after_state_sha256")
                else:
                    actual_hash = self._file_sha256(path)
                    expected_hash = file_record.get("after_sha256")
                if actual_hash != expected_hash:
                    raise ManagedGitConflictError(
                        f"Migrated state file changed after apply: {path}",
                        details={
                            "expected_sha256": expected_hash,
                            "actual_sha256": actual_hash,
                        },
                    )
            self._verify_migrated_file_has_no_legacy_fields(
                path,
                kind=str(file_record.get("kind") or ""),
                mapping_records=[
                    item for item in manifest.get("mapping_details") or [] if isinstance(item, dict)
                ],
            )

        registry = self._read_registry()
        bindings_to_verify = manifest.get("bindings") or []
        for binding in bindings_to_verify:
            if not isinstance(binding, dict):
                continue
            repo_id = str(binding.get("managed_repo_id") or "")
            record = registry["repositories"].get(repo_id)
            binding_present = isinstance(record, dict) and any(
                isinstance(item, dict)
                and item.get("kind") == binding.get("kind")
                and item.get("binding_id") == binding.get("binding_id")
                for item in record.get("bindings") or []
            )
            if not binding_present and self._migration_binding_is_live(binding):
                raise ManagedGitConflictError(
                    f"Managed repository {repo_id} lost a migrated binding",
                    details={
                        "kind": binding.get("kind"),
                        "binding_id": binding.get("binding_id"),
                    },
                )

    def _migration_binding_is_live(self, binding: Mapping[str, Any]) -> bool:
        kind = str(binding.get("kind") or "")
        binding_id = str(binding.get("binding_id") or "")
        repo_id = str(binding.get("managed_repo_id") or "")
        if kind == "issues":
            issues = self._read_json_mapping(self.runtime_dir / "local-issues.json") or {}
            return any(
                isinstance(issue, dict)
                and str(issue.get("issue_id") or issue.get("issue_number") or issue_key)
                == binding_id
                and str(issue.get("managed_repo_id") or "") == repo_id
                for issue_key, issue in issues.items()
            )
        if kind == "team_projects":
            team_path = self.runtime_dir / "addons" / "team-addon" / "state.json"
            projects = (self._read_json_mapping(team_path) or {}).get("projects") or {}
            if isinstance(projects, dict):
                for project_key, project in projects.items():
                    if not isinstance(project, dict):
                        continue
                    git = project.get("git")
                    if (
                        str(project.get("id") or project_key) == binding_id
                        and isinstance(git, dict)
                        and str(git.get("managed_repo_id") or "") == repo_id
                    ):
                        return True
            return any(
                project_id == binding_id and str(git.get("managed_repo_id") or "") == repo_id
                for project_id, git in self._read_team_database_git(
                    self.runtime_dir / "addons" / "team-addon" / "state.db"
                )
            )
        if kind in {"sessions", "authorized_sessions"}:
            sessions = self._read_json_mapping(self._session_store_path()) or {}
            for session_key, session in sessions.items():
                if not isinstance(session, dict):
                    continue
                if str(session.get("session_id") or session_key) != binding_id:
                    continue
                metadata = session.get("metadata")
                if not isinstance(metadata, dict):
                    return False
                if kind == "authorized_sessions" and not any(
                    key in metadata
                    for key in (
                        MANAGED_GIT_GRANTS_METADATA_KEY,
                        *LEGACY_MANAGED_GIT_GRANT_METADATA_KEYS,
                    )
                ):
                    return False
                return self._nested_value_contains_managed_repo_id(metadata, repo_id)
        return False

    @classmethod
    def _nested_value_contains_managed_repo_id(cls, value: object, repo_id: str) -> bool:
        if isinstance(value, dict):
            if str(value.get("managed_repo_id") or "") == repo_id:
                return True
            return any(
                cls._nested_value_contains_managed_repo_id(item, repo_id) for item in value.values()
            )
        if isinstance(value, list):
            return any(cls._nested_value_contains_managed_repo_id(item, repo_id) for item in value)
        return False

    def _verify_migrated_file_has_no_legacy_fields(
        self,
        path: Path,
        *,
        kind: str,
        mapping_records: list[dict[str, Any]],
    ) -> None:
        if kind == "team_state_db":
            for project_id, git in self._read_team_database_git(path):
                legacy = self._legacy_field_paths(
                    git, include_repo_path=True, include_repo_source=True
                )
                if legacy:
                    raise ManagedGitConflictError(
                        f"Team project {project_id} retains legacy managed Git fields",
                        details={"legacy_fields": legacy},
                    )
            return
        if kind == "authorized_keys":
            text = path.read_text(encoding="utf-8")
            if "EVANDER_GIT_AUTH_REPO_PREFIX_B64=" in text:
                raise ManagedGitConflictError(
                    f"Authorized key remains prefix-scoped after migration: {path}"
                )
            legacy_paths = {
                str(alias)
                for mapping in mapping_records
                for alias in mapping.get("aliases") or []
                if str(alias).startswith("/")
            }
            for match in _B64_ASSIGNMENT_PATTERN.finditer(text):
                try:
                    decoded = base64.b64decode(match.group("value"), validate=True).decode("utf-8")
                except (ValueError, UnicodeDecodeError):
                    raise ManagedGitConflictError(
                        f"Authorized key has invalid managed Git path encoding: {path}"
                    ) from None
                if decoded in legacy_paths:
                    raise ManagedGitConflictError(
                        f"Authorized key still targets a legacy repository: {path}"
                    )
            return
        payload = self._read_json_mapping(path)
        if payload is None:
            raise ManagedGitConflictError(f"Migrated JSON state is invalid: {path}")
        if kind == "team_state":
            projects = payload.get("projects")
            if not isinstance(projects, dict):
                return
            for project_id, project in projects.items():
                if not isinstance(project, dict) or not isinstance(project.get("git"), dict):
                    continue
                legacy = self._legacy_field_paths(
                    project["git"], include_repo_path=True, include_repo_source=True
                )
                if legacy:
                    raise ManagedGitConflictError(
                        f"Team project {project_id} retains legacy managed Git fields",
                        details={"legacy_fields": legacy},
                    )
            return
        if kind == "directory_issue_setup_config" and "git_root" in payload:
            raise ManagedGitConflictError(
                f"Directory configuration retains legacy issue_setup.git_root: {path}"
            )
        if kind in {"workflow_pack", "workflow_pack_live"}:
            legacy = self._workflow_legacy_field_paths(payload)
        else:
            include_repo_source = kind in {"local_issues", "sessions", "team_state"}
            legacy = self._legacy_field_paths(
                payload,
                include_repo_path=True,
                include_repo_source=include_repo_source,
            )
        if legacy:
            raise ManagedGitConflictError(
                f"Migrated state retains legacy managed Git fields: {path}",
                details={"legacy_fields": legacy},
            )

    def _record_applied_migration(self, manifest: Mapping[str, Any]) -> None:
        migration_id = str(manifest["migration_id"])

        def record_migration(payload: dict[str, Any]):
            existing = payload["migrations"].get(migration_id)
            if isinstance(existing, dict) and existing.get("verification_hash") != manifest.get(
                "verification_hash"
            ):
                raise ManagedGitConflictError(
                    f"Migration ID {migration_id} already exists with different input"
                )
            payload["migrations"][migration_id] = {
                "status": str(manifest.get("status") or "applied"),
                "created_at": manifest.get("created_at"),
                "managed_repo_ids": list(manifest.get("managed_repo_ids") or []),
                "verification_hash": manifest.get("verification_hash"),
            }

        self._update_registry(record_migration)

    def _finalize_migration(self, migration_id: str) -> dict[str, Any]:
        safe_id = str(migration_id or "").strip()
        if not _SAFE_OPERATION_ID.fullmatch(safe_id):
            raise ValueError("migration_id contains unsupported characters")
        manifest_path = self._migration_root / f"{safe_id}.json"
        if not manifest_path.exists():
            raise ManagedGitNotFoundError(f"Unknown managed Git migration: {safe_id}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("schema") != "evander.managed-git-migration/v1":
            raise ValueError(f"Invalid managed Git migration manifest: {safe_id}")
        # Finalization intentionally follows the post-apply smoke/acceptance window.
        # Runtime state may have changed legitimately by then, so revalidate the
        # semantic managed-ID/ref/binding contract without demanding snapshot bytes.
        self._verify_migration_manifest(manifest, require_file_hashes=False)
        if self._legacy_managed_git_artifacts_present(allow_declared_rollback=True):
            raise ManagedGitConflictError(
                "Managed Git migration cannot be finalized while unresolved legacy state remains"
            )
        deleted: list[str] = []
        for raw_path in manifest.get("rollback_artifacts") or []:
            path = Path(str(raw_path)).resolve(strict=False)
            if not self._is_evander_owned_rollback_path(path):
                raise ManagedGitConflictError(
                    f"Migration manifest contains a non-Evander rollback path: {path}"
                )
            if path.is_dir():
                self._make_tree_writable(path)
                shutil.rmtree(path)
                deleted.append(path.name)
            elif path.exists():
                path.unlink()
                deleted.append(path.name)
        finalized_at = _utc_now()
        manifest["status"] = "finalized"
        manifest["finalized_at"] = finalized_at
        self._write_json_atomic(manifest_path, manifest, mode=0o600)

        def finalize(payload: dict[str, Any]):
            migration = payload["migrations"].get(safe_id)
            if not isinstance(migration, dict):
                raise ManagedGitNotFoundError(f"Unknown managed Git migration: {safe_id}")
            migration["status"] = "finalized"
            migration["finalized_at"] = finalized_at

        self._update_registry(finalize)
        return {
            "stage": "finalize",
            "migration_id": safe_id,
            "status": "finalized",
            "deleted_rollback_artifacts": deleted,
        }

    def _legacy_candidates(self) -> list[dict[str, Any]]:
        legacy_git_roots = {
            self.project_dir / "git-remotes",
            self.runtime_dir / "repo-cache",
        }
        configured_git_root = os.environ.get("EVANDER_GIT_ROOT", "").strip()
        if configured_git_root:
            root = Path(configured_git_root).expanduser()
            if not root.is_absolute():
                root = self.project_dir / root
            legacy_git_roots.add(root.resolve(strict=False))
        candidates: list[dict[str, Any]] = []
        for root in sorted(legacy_git_roots, key=str):
            if root.resolve(strict=False) == self._managed_root.resolve(strict=False):
                continue
            if not root.exists():
                continue
            for candidate in sorted(root.glob("*.git")):
                if not candidate.is_dir():
                    continue
                source = self._legacy_git_config(candidate, "evander.canonicalOriginUrl")
                mirror_source = self._legacy_git_config(candidate, "evander.mirrorSource")
                source = source or mirror_source
                source = source or self._legacy_git_config(candidate, "remote.origin.url")
                trusted = self._legacy_git_config(candidate, "evander.trustedRepoPath")
                # A trusted checkout is the freshest operator-owned source during the
                # migration window. Its origin does not define identity when the legacy bare
                # repository already carries canonical metadata.
                registration_source = trusted or source
                identity_source = source or trusted
                raw_directory_path = self._legacy_git_config(candidate, "evander.directoryPath")
                has_directory_metadata = bool(raw_directory_path)
                directory_path = raw_directory_path or "root"
                try:
                    directory_path = self._normalize_directory_path(directory_path)
                except ValueError:
                    directory_path = "root"
                    has_directory_metadata = False
                source_info: _SourceInfo | None = None
                source_error: str | None = None
                if identity_source:
                    try:
                        source_info = self._source_info(identity_source)
                    except (OSError, RuntimeError, ValueError) as exc:
                        source_error = str(exc)
                refs = self._list_refs(
                    candidate,
                    ("refs/heads", "refs/notes", "refs/evander", "refs/pull"),
                )
                preserved_refs = {
                    ref: oid for ref, oid in refs.items() if self._is_preserved_migration_ref(ref)
                }
                candidates.append(
                    {
                        "name": candidate.name,
                        "path": str(candidate.resolve(strict=False)),
                        "directory_path": directory_path,
                        "has_directory_metadata": has_directory_metadata,
                        "registration_source": registration_source,
                        "identity_source": identity_source,
                        "trusted_repo_path": trusted,
                        "mirror_source": mirror_source,
                        "canonical_identity": (
                            source_info.canonical_identity if source_info is not None else None
                        ),
                        "canonical_url": (
                            source_info.canonical_url if source_info is not None else None
                        ),
                        "source_error": source_error,
                        "ref_count": len(refs),
                        "preserved_refs": preserved_refs,
                    }
                )
        return candidates

    def _legacy_inventory(self) -> dict[str, Any]:
        candidates = self._legacy_candidates()
        bare_repositories = [
            {
                "name": candidate["name"],
                "directory_path": candidate["directory_path"],
                "has_directory_metadata": candidate["has_directory_metadata"],
                "canonical_repository_identity": candidate["canonical_identity"],
                "canonical_repository_url": candidate["canonical_url"],
                "has_canonical_source": bool(candidate["canonical_identity"]),
                "source_error": candidate["source_error"],
                "ref_count": candidate["ref_count"],
                "preserved_ref_count": len(candidate["preserved_refs"]),
            }
            for candidate in candidates
        ]
        groups: list[dict[str, Any]] = []
        collisions: list[dict[str, Any]] = []
        for directory_path, identity, grouped_candidates in self._legacy_candidate_groups(
            candidates
        ):
            ref_values: dict[str, set[str]] = {}
            for candidate in grouped_candidates:
                for ref_name, object_id in candidate["preserved_refs"].items():
                    ref_values.setdefault(ref_name, set()).add(object_id)
            group_collisions = [
                {"ref_name": ref_name, "object_ids": sorted(object_ids)}
                for ref_name, object_ids in sorted(ref_values.items())
                if len(object_ids) > 1
            ]
            collisions.extend(
                {
                    "directory_path": directory_path,
                    "canonical_repository_identity": identity,
                    **collision,
                }
                for collision in group_collisions
            )
            groups.append(
                {
                    "directory_path": directory_path,
                    "canonical_repository_identity": identity,
                    "legacy_repositories": sorted(
                        candidate["name"] for candidate in grouped_candidates
                    ),
                    "preserved_ref_count": len(ref_values),
                    "collisions": group_collisions,
                }
            )
        repo_cache = self.runtime_dir / "repo-cache"
        cache_mirrors = []
        cache_worktrees = []
        if repo_cache.exists():
            cache_mirrors = sorted(path.name for path in repo_cache.glob("*.git") if path.is_dir())
            cache_worktrees = sorted(
                path.name for path in repo_cache.glob("*-worktree") if path.is_dir()
            )
        state_inventory = self._legacy_state_inventory()
        return {
            "bare_repositories": bare_repositories,
            "groups": groups,
            "preserved_ref_collisions": collisions,
            "cache_mirrors": cache_mirrors,
            "cache_worktrees": cache_worktrees,
            "active_session_ids": self._active_session_ids(),
            "active_git_operations": self._active_operation_locks(),
            "worker_service_states": self._worker_service_states(),
            "worker_lock_states": self._migration_worker_lock_states(),
            "custom_legacy_git_root": self._custom_legacy_git_root(),
            **state_inventory,
        }

    def _custom_legacy_git_root(self) -> str | None:
        configured = os.environ.get("EVANDER_GIT_ROOT", "").strip()
        if not configured:
            return None
        path = Path(configured).expanduser()
        if not path.is_absolute():
            path = self.project_dir / path
        resolved = path.resolve(strict=False)
        owned_roots = {
            (self.project_dir / "git-remotes").resolve(strict=False),
            (self.runtime_dir / "repo-cache").resolve(strict=False),
            self._managed_root.resolve(strict=False),
        }
        if resolved in owned_roots or not resolved.exists():
            return None
        if not any(candidate.is_dir() for candidate in resolved.glob("*.git")):
            return None
        return str(resolved)

    @staticmethod
    def _legacy_candidate_groups(
        candidates: list[dict[str, Any]],
    ) -> list[tuple[str, str, list[dict[str, Any]]]]:
        """Group legacy copies exactly as ancestor-scoped registration will coalesce them."""

        grouped_by_identity: dict[str, list[dict[str, Any]]] = {}
        for candidate in candidates:
            identity = str(candidate.get("canonical_identity") or "")
            if identity:
                grouped_by_identity.setdefault(identity, []).append(candidate)
        result: list[tuple[str, str, list[dict[str, Any]]]] = []
        for identity, identity_candidates in sorted(grouped_by_identity.items()):
            trust_groups: list[tuple[str, list[dict[str, Any]]]] = []
            for candidate in sorted(
                identity_candidates,
                key=lambda item: (
                    len(str(item["directory_path"]).split("/")),
                    str(item["directory_path"]),
                    str(item.get("name") or ""),
                ),
            ):
                directory = str(candidate["directory_path"])
                matches = [
                    group for group in trust_groups if _is_same_or_descendant(directory, group[0])
                ]
                if matches:
                    owner, members = max(matches, key=lambda group: len(group[0].split("/")))
                    members.append(candidate)
                else:
                    trust_groups.append((directory, [candidate]))
            result.extend((owner, identity, members) for owner, members in trust_groups)
        return sorted(result, key=lambda item: (item[0], item[1]))

    def _worker_service_states(self) -> dict[str, str]:
        states: dict[str, str] = {}
        for unit in (
            "evander-trigger-orchestrator.service",
            "evander-effect-orchestrator.service",
        ):
            result = subprocess.run(
                ["systemctl", "is-active", unit],
                check=False,
                capture_output=True,
                text=True,
            )
            state = result.stdout.strip().casefold() or "unavailable"
            if state in {"active", "activating", "reloading"}:
                details = subprocess.run(
                    [
                        "systemctl",
                        "show",
                        unit,
                        "--property=WorkingDirectory",
                        "--property=ExecStart",
                        "--value",
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                ).stdout
                target_markers = {
                    str(self.project_dir.resolve(strict=False)),
                    str(self.runtime_dir.resolve(strict=False)),
                }
                if details.strip() and not any(marker in details for marker in target_markers):
                    state = f"{state}-other-runtime"
            states[unit] = state
        return states

    def _migration_worker_lock_specs(self) -> list[tuple[str, Path]]:
        configured: dict[str, object] = {}
        config_path = self.project_dir / "config" / "trigger-orchestrator" / "config.json"
        if config_path.is_file():
            try:
                payload = json.loads(config_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                payload = {}
            if isinstance(payload, dict):
                configured = payload

        specs: list[tuple[str, Path]] = []
        for label, field, fallback in (
            ("trigger-orchestrator", "lock_path", self.runtime_dir / "trigger-orchestrator.lock"),
            (
                "effect-orchestrator",
                "effect_lock_path",
                self.runtime_dir / "effect-orchestrator.lock",
            ),
        ):
            raw_path = configured.get(field)
            path = Path(str(raw_path)).expanduser() if raw_path else fallback
            if not path.is_absolute():
                path = self.project_dir / path
            specs.append((label, path.resolve(strict=False)))
        return specs

    def _migration_worker_lock_states(self) -> dict[str, str]:
        guarded = set(getattr(self._migration_preflight_context, "guarded_worker_locks", set()))
        states: dict[str, str] = {}
        for label, path in self._migration_worker_lock_specs():
            if path in guarded or not path.exists():
                states[label] = "inactive"
                continue
            try:
                with path.open("r+", encoding="utf-8") as handle:
                    try:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        states[label] = "active"
                    else:
                        states[label] = "inactive"
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                states[label] = "unavailable"
        return states

    @contextmanager
    def _migration_worker_lock_guard(self) -> Iterator[None]:
        handles: list[Any] = []
        guarded_paths: set[Path] = set()
        try:
            for label, path in self._migration_worker_lock_specs():
                path.parent.mkdir(parents=True, exist_ok=True)
                handle = path.open("a+", encoding="utf-8")
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    handle.close()
                    raise ManagedGitConflictError(
                        "Managed Git migration requires trigger and effect workers to be stopped",
                        details={"active_worker": label},
                    ) from exc
                handles.append(handle)
                guarded_paths.add(path)
            self._migration_preflight_context.guarded_worker_locks = guarded_paths
            yield
        finally:
            self._migration_preflight_context.guarded_worker_locks = set()
            for handle in reversed(handles):
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                finally:
                    handle.close()

    @staticmethod
    def _workflow_payload_value(value: object, field: str, default: object = None) -> object:
        if isinstance(value, Mapping):
            return value.get(field, default)
        return getattr(value, field, default)

    def _live_workflow_editor_documents(self) -> dict[Path, dict[str, Any]]:
        admin = self._workflow_config_admin
        if admin is None:
            return {}
        worker_document = admin.read_worker_global_config("trigger-orchestrator")
        worker_config = self._workflow_payload_value(worker_document, "config", {})
        if not isinstance(worker_config, Mapping):
            raise ManagedGitConflictError(
                "Trigger orchestrator workflow configuration is invalid during migration"
            )
        raw_references = worker_config.get("workflow_packs")
        if not isinstance(raw_references, list):
            return {}
        documents: dict[Path, dict[str, Any]] = {}
        for raw_reference in raw_references:
            reference = str(raw_reference or "").strip()
            if not reference:
                continue
            document = admin.read_workflow_pack_for_migration(workflow_pack=reference)
            raw_path = self._workflow_payload_value(document, "path")
            selected_reference = str(
                self._workflow_payload_value(document, "reference", reference) or reference
            ).strip()
            revision = str(self._workflow_payload_value(document, "revision", "") or "").strip()
            if not raw_path or not selected_reference or not revision:
                raise ManagedGitConflictError(
                    f"Live workflow pack {reference!r} lacks path or revision metadata"
                )
            path = Path(str(raw_path)).expanduser().resolve(strict=False)
            documents[path] = {
                "reference": selected_reference,
                "revision": revision,
                "document": document,
            }
        return documents

    def _legacy_state_inventory(self) -> dict[str, Any]:
        live_workflows = self._live_workflow_editor_documents()
        workflow_packs: list[dict[str, Any]] = []
        for path in self._workflow_pack_paths():
            payload = self._read_json_mapping(path)
            if payload is None:
                continue
            legacy_fields = self._workflow_legacy_field_paths(payload)
            if legacy_fields:
                live = live_workflows.get(path.resolve(strict=False))
                workflow_packs.append(
                    {
                        "path": str(path.resolve(strict=False)),
                        "sha256": self._file_sha256(path),
                        "legacy_fields": legacy_fields,
                        "live": live is not None,
                        **(
                            {
                                "workflow_pack": str(live["reference"]),
                                "revision": str(live["revision"]),
                            }
                            if live is not None
                            else {}
                        ),
                    }
                )

        team_projects: list[dict[str, Any]] = []
        team_json_path = self.runtime_dir / "addons" / "team-addon" / "state.json"
        team_payload = self._read_json_mapping(team_json_path)
        if team_payload is not None:
            projects = team_payload.get("projects")
            if isinstance(projects, dict):
                for project_key, raw_project in sorted(projects.items()):
                    if not isinstance(raw_project, dict):
                        continue
                    git = raw_project.get("git")
                    legacy_fields = self._legacy_field_paths(
                        git, include_repo_path=True, include_repo_source=True
                    )
                    if legacy_fields:
                        team_projects.append(
                            {
                                "source": "state.json",
                                "path": str(team_json_path.resolve(strict=False)),
                                "project_id": str(raw_project.get("id") or project_key),
                                "legacy_fields": legacy_fields,
                            }
                        )
        team_db_path = self.runtime_dir / "addons" / "team-addon" / "state.db"
        for project_id, git in self._read_team_database_git(team_db_path):
            legacy_fields = self._legacy_field_paths(
                git, include_repo_path=True, include_repo_source=True
            )
            if legacy_fields:
                team_projects.append(
                    {
                        "source": "state.db",
                        "path": str(team_db_path.resolve(strict=False)),
                        "project_id": project_id,
                        "legacy_fields": legacy_fields,
                    }
                )

        sessions_path = self._session_store_path()
        sessions_payload = self._read_json_mapping(sessions_path)
        session_bindings: list[dict[str, Any]] = []
        authorized_paths: set[Path] = set()
        if sessions_payload is not None:
            for session_key, raw_session in sorted(sessions_payload.items()):
                if not isinstance(raw_session, dict):
                    continue
                metadata = raw_session.get("metadata")
                legacy_fields = self._legacy_field_paths(
                    metadata, include_repo_path=True, include_repo_source=True
                )
                session_id = str(raw_session.get("session_id") or session_key)
                if legacy_fields:
                    session_bindings.append(
                        {
                            "session_id": session_id,
                            "directory_path": str(raw_session.get("directory_path") or "root"),
                            "legacy_fields": legacy_fields,
                        }
                    )
                self._collect_authorized_keys_paths(metadata, authorized_paths)

        issues_path = self.runtime_dir / "local-issues.json"
        issues_payload = self._read_json_mapping(issues_path)
        issue_bindings: list[dict[str, Any]] = []
        if issues_payload is not None:
            for issue_key, raw_issue in sorted(issues_payload.items()):
                if not isinstance(raw_issue, dict):
                    continue
                legacy_fields = self._legacy_field_paths(
                    raw_issue,
                    include_repo_path=True,
                    include_repo_source=True,
                )
                if legacy_fields:
                    issue_bindings.append(
                        {
                            "issue_id": str(
                                raw_issue.get("issue_id")
                                or raw_issue.get("issue_number")
                                or issue_key
                            ),
                            "directory_path": str(raw_issue.get("directory_path") or "root"),
                            "legacy_fields": legacy_fields,
                        }
                    )

        configured_authorized_keys = str(
            getattr(self.config, "git_authorized_keys_path", "") or ""
        ).strip()
        if configured_authorized_keys:
            authorized_paths.add(Path(configured_authorized_keys).expanduser())
        env_authorized_keys = os.environ.get("EVANDER_GIT_AUTHORIZED_KEYS_PATH", "").strip()
        if env_authorized_keys:
            authorized_paths.add(Path(env_authorized_keys).expanduser())
        for config_path in self._legacy_issue_setup_config_paths():
            issue_setup = self._read_json_mapping(config_path)
            if not isinstance(issue_setup, dict) or "git_root" not in issue_setup:
                continue
            git_user = str(issue_setup.get("git_user") or "").strip()
            if not git_user:
                continue
            try:
                authorized_paths.add(
                    Path(pwd.getpwnam(git_user).pw_dir) / ".ssh" / "authorized_keys"
                )
            except KeyError:
                continue
        authorized_bindings = self._authorized_keys_inventory(authorized_paths)
        legacy_configuration_files: list[dict[str, Any]] = []
        for path in self._legacy_issue_setup_config_paths():
            payload = self._read_json_mapping(path)
            if payload is None or "git_root" not in payload:
                continue
            legacy_configuration_files.append(
                {
                    "path": str(path.resolve(strict=False)),
                    "sha256": self._file_sha256(path),
                    "legacy_fields": ["git_root"],
                }
            )

        return {
            "team_projects_with_legacy_git_fields": team_projects,
            "session_bindings_with_legacy_git_fields": session_bindings,
            "issue_bindings_with_legacy_git_fields": issue_bindings,
            "authorized_key_bindings": authorized_bindings,
            "workflow_packs_with_legacy_fields": [
                Path(str(item["path"])).name for item in workflow_packs
            ],
            "workflow_pack_files_with_legacy_fields": workflow_packs,
            "legacy_configuration_files": legacy_configuration_files,
        }

    def _workflow_pack_paths(self) -> list[Path]:
        paths: set[Path] = set()
        for root in (
            self.project_dir / "config" / "workflow-packs",
            self.runtime_dir / "workflow-packs",
        ):
            if root.exists():
                paths.update(path.resolve(strict=False) for path in root.glob("*.json"))
        return sorted(paths, key=str)

    def _session_store_path(self) -> Path:
        configured = getattr(self.config, "session_store_path", None)
        if configured:
            path = Path(configured).expanduser()
            if not path.is_absolute():
                path = self.project_dir / path
            return path.resolve(strict=False)
        return (self.runtime_dir / "sessions.json").resolve(strict=False)

    def _legacy_issue_setup_config_paths(self) -> list[Path]:
        root = self.runtime_dir / "directory-config"
        if not root.exists():
            return []
        paths = set(root.glob("**/issue-setup.json"))
        paths.update(root.glob("**/issue_setup.json"))
        return sorted((path.resolve(strict=False) for path in paths), key=str)

    @staticmethod
    def _read_json_mapping(path: Path) -> dict[str, Any] | None:
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _file_sha256(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    @classmethod
    def _legacy_field_paths(
        cls,
        value: object,
        *,
        include_repo_path: bool = False,
        include_repo_source: bool = False,
        prefix: str = "",
    ) -> list[str]:
        fields = set(_LEGACY_MANAGED_GIT_FIELDS)
        if include_repo_path:
            fields.update({"repo_path", "authorized_repo_path"})
        if include_repo_source:
            fields.add("repo_source")
        found: list[str] = []
        if isinstance(value, dict):
            for key, item in value.items():
                item_path = f"{prefix}.{key}" if prefix else str(key)
                if key in fields:
                    found.append(item_path)
                found.extend(
                    cls._legacy_field_paths(
                        item,
                        include_repo_path=include_repo_path,
                        include_repo_source=include_repo_source,
                        prefix=item_path,
                    )
                )
        elif isinstance(value, list):
            for index, item in enumerate(value):
                item_path = f"{prefix}[{index}]" if prefix else f"[{index}]"
                found.extend(
                    cls._legacy_field_paths(
                        item,
                        include_repo_path=include_repo_path,
                        include_repo_source=include_repo_source,
                        prefix=item_path,
                    )
                )
        return sorted(set(found))

    @classmethod
    def _workflow_legacy_field_paths(cls, value: object) -> list[str]:
        found: list[str] = []
        clone_node_ids = cls._workflow_clone_node_ids(value)

        def visit(
            item: object,
            path: str = "",
            inherited_node_type: str = "",
        ) -> None:
            if isinstance(item, str):
                for field in _LEGACY_MANAGED_GIT_ROUTING_FIELDS:
                    if re.search(rf"\.{re.escape(field)}(?=[^A-Za-z0-9_]|$)", item):
                        found.append(f"{path}<expression:{field}>")
                if any(
                    re.search(
                        rf"(?<![A-Za-z0-9_-]){re.escape(node_id)}\.repo_source"
                        rf"(?=[^A-Za-z0-9_]|$)",
                        item,
                    )
                    for node_id in clone_node_ids
                ):
                    found.append(f"{path}<expression:repo_source>")
                return
            if isinstance(item, list):
                for index, child in enumerate(item):
                    visit(child, f"{path}[{index}]", "")
                return
            if not isinstance(item, dict):
                return
            node_type = str(item.get("type") or item.get("provider") or inherited_node_type)
            clone_node = node_type in {
                "effect:agent:clone-repo",
                "effect:vm:clone-repo",
            }
            for key, child in item.items():
                child_path = f"{path}.{key}" if path else str(key)
                if key in _LEGACY_MANAGED_GIT_ROUTING_FIELDS or key == "bare_repo_name_glob":
                    found.append(child_path)
                elif key == "repo_source" and clone_node:
                    found.append(child_path)
                visit(
                    child,
                    child_path,
                    node_type if str(key) == "config" else "",
                )

        visit(value)
        return sorted(set(found))

    @classmethod
    def _workflow_clone_node_ids(cls, value: object) -> set[str]:
        node_ids: set[str] = set()

        def visit(item: object) -> None:
            if isinstance(item, list):
                for child in item:
                    visit(child)
                return
            if not isinstance(item, dict):
                return
            node_type = str(item.get("type") or item.get("provider") or "")
            if node_type in {"effect:agent:clone-repo", "effect:vm:clone-repo"}:
                node_id = str(item.get("id") or "").strip()
                if node_id:
                    node_ids.add(node_id)
            for child in item.values():
                visit(child)

        visit(value)
        return node_ids

    @staticmethod
    def _collect_authorized_keys_paths(value: object, paths: set[Path]) -> None:
        if isinstance(value, dict):
            raw_path = value.get("authorized_keys_path")
            if isinstance(raw_path, str) and raw_path.strip():
                paths.add(Path(raw_path).expanduser().resolve(strict=False))
            for item in value.values():
                ManagedGitAdminService._collect_authorized_keys_paths(item, paths)
        elif isinstance(value, list):
            for item in value:
                ManagedGitAdminService._collect_authorized_keys_paths(item, paths)

    def _known_authorized_keys_paths(self) -> set[Path]:
        paths: set[Path] = set()

        sessions = self._read_json_mapping(self._session_store_path()) or {}
        for raw_session in sessions.values():
            if isinstance(raw_session, dict):
                self._collect_authorized_keys_paths(raw_session.get("metadata"), paths)

        def add_path(raw_path: object) -> None:
            value = str(raw_path or "").strip()
            if not value:
                return
            path = Path(value).expanduser()
            if not path.is_absolute():
                path = self.project_dir / path
            paths.add(path.resolve(strict=False))

        add_path(getattr(self.config, "git_authorized_keys_path", ""))
        add_path(os.environ.get("EVANDER_GIT_AUTHORIZED_KEYS_PATH", ""))

        for config_path in self._legacy_issue_setup_config_paths():
            issue_setup = self._read_json_mapping(config_path) or {}
            git_user = str(issue_setup.get("git_user") or "").strip()
            if not git_user:
                continue
            try:
                paths.add(
                    (Path(pwd.getpwnam(git_user).pw_dir) / ".ssh" / "authorized_keys").resolve(
                        strict=False
                    )
                )
            except KeyError:
                continue

        for manifest_path in self._migration_root.glob("*.json"):
            manifest = self._read_json_mapping(manifest_path)
            if not isinstance(manifest, dict) or manifest.get("status") not in {
                "applied",
                "finalized",
            }:
                continue
            for rewrite in manifest.get("rewritten_files") or []:
                if not isinstance(rewrite, dict) or rewrite.get("kind") != "authorized_keys":
                    continue
                add_path(rewrite.get("path"))

        return {path.resolve(strict=False) for path in paths}

    def _has_authorized_key_for_repository(self, record: Mapping[str, Any]) -> bool:
        bare_path = self._bare_path(record).resolve(strict=False)
        for binding in self._authorized_keys_inventory(self._known_authorized_keys_paths()):
            repository_path = str(binding.get("repository_path") or "").strip()
            if repository_path and repository_path != "<invalid>":
                if Path(repository_path).expanduser().resolve(strict=False) == bare_path:
                    return True
            repository_prefix = str(binding.get("repository_prefix") or "").strip()
            if not repository_prefix or repository_prefix == "<invalid>":
                continue
            try:
                bare_path.relative_to(Path(repository_prefix).expanduser().resolve(strict=False))
            except ValueError:
                continue
            return True
        return False

    @staticmethod
    def _read_team_database_git(path: Path) -> list[tuple[str, dict[str, Any]]]:
        if not path.is_file():
            return []
        try:
            with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
                exists = connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='team_projects'"
                ).fetchone()
                if exists is None:
                    return []
                rows = connection.execute(
                    "SELECT id, git_json FROM team_projects WHERE git_json IS NOT NULL ORDER BY id"
                ).fetchall()
        except sqlite3.Error:
            return []
        result: list[tuple[str, dict[str, Any]]] = []
        for project_id, raw_git in rows:
            try:
                git = json.loads(str(raw_git))
            except json.JSONDecodeError:
                continue
            if isinstance(git, dict):
                result.append((str(project_id), git))
        return result

    @staticmethod
    def _authorized_keys_inventory(paths: set[Path]) -> list[dict[str, Any]]:
        inventory: list[dict[str, Any]] = []
        for path in sorted(paths, key=str):
            if not path.is_file():
                continue
            for line_number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1
            ):
                assignments = {
                    match.group("name"): match.group("value")
                    for match in _B64_ASSIGNMENT_PATTERN.finditer(line)
                }
                if not assignments:
                    continue
                decoded: dict[str, str] = {}
                for name, value in assignments.items():
                    try:
                        decoded[name] = base64.b64decode(value, validate=True).decode("utf-8")
                    except (ValueError, UnicodeDecodeError):
                        decoded[name] = "<invalid>"
                inventory.append(
                    {
                        "path": str(path.resolve(strict=False)),
                        "line": line_number,
                        "access": "write"
                        if "EVANDER_GIT_AUTH_ACCESS=write" in line
                        else "read-only",
                        "repository_path": decoded.get("EVANDER_GIT_AUTH_REPO_B64"),
                        "repository_prefix": decoded.get("EVANDER_GIT_AUTH_REPO_PREFIX_B64"),
                        "session_id": decoded.get("EVANDER_GIT_AUTH_SESSION_ID_B64"),
                    }
                )
        return inventory

    def _migration_mapping_records(
        self,
        candidates: list[dict[str, Any]],
        mappings: list[dict[str, str]],
    ) -> list[dict[str, Any]]:
        repo_id_by_path = {
            str(item["legacy_repository"]): str(item["managed_repo_id"]) for item in mappings
        }
        records: list[dict[str, Any]] = []
        for candidate in candidates:
            path = str(candidate["path"])
            repo_id = repo_id_by_path.get(path)
            if not repo_id:
                continue
            legacy_name = str(candidate.get("name") or "")
            aliases = {
                path,
                str(candidate.get("canonical_url") or ""),
            }
            aliases.update(self._legacy_repository_name_aliases(legacy_name, Path(path).name))
            for raw_alias in (
                candidate.get("registration_source"),
                candidate.get("trusted_repo_path"),
                candidate.get("mirror_source"),
            ):
                local_alias = self._safe_migration_local_alias(raw_alias)
                if local_alias:
                    aliases.add(local_alias)
            aliases.discard("")
            records.append(
                {
                    "legacy_repository": path,
                    "legacy_name": legacy_name,
                    "directory_path": str(candidate.get("directory_path") or "root"),
                    "canonical_repository_identity": candidate.get("canonical_identity"),
                    "canonical_repository_url": candidate.get("canonical_url"),
                    "managed_repo_id": repo_id,
                    "aliases": sorted(aliases),
                }
            )
        return records

    @staticmethod
    def _legacy_repository_name_aliases(*raw_names: object) -> set[str]:
        aliases: set[str] = set()
        for raw_name in raw_names:
            value = str(raw_name or "").strip()
            if not value:
                continue
            name = Path(value).name
            if not name or name in {".", ".."}:
                continue
            aliases.add(name)
            if name.endswith(".git") and len(name) > len(".git"):
                aliases.add(name[: -len(".git")])
        return aliases

    def _safe_migration_local_alias(self, raw_value: object) -> str | None:
        value = str(raw_value or "").strip()
        if not value:
            return None
        if value.startswith("file://"):
            parsed = urlsplit(value)
            if parsed.netloc not in {"", "localhost"}:
                return None
            return str(Path(unquote(parsed.path)).expanduser().resolve(strict=False))
        if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", value):
            return None
        if re.match(r"^[^/@:\s]+@[^:\s]+:.+", value):
            return None
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = self.project_dir / path
        return str(path.resolve(strict=False))

    @staticmethod
    def _safe_migration_detail_value(raw_value: object) -> str:
        """Return a useful migration diagnostic without URL credentials or query secrets."""

        value = str(raw_value or "").strip()
        if not value:
            return ""
        if value.startswith("${{"):
            return "<workflow-expression>"
        if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", value):
            parsed = urlsplit(value)
            hostname = parsed.hostname or ""
            if ":" in hostname and not hostname.startswith("["):
                hostname = f"[{hostname}]"
            try:
                port = f":{parsed.port}" if parsed.port is not None else ""
            except ValueError:
                port = ""
            return urlunsplit(
                (
                    parsed.scheme.casefold(),
                    f"{hostname}{port}",
                    parsed.path,
                    "",
                    "",
                )
            )
        scp_match = re.fullmatch(
            r"(?:(?:[^/@:\s]+)@)?(?P<host>[^:\s]+):(?P<path>.+)",
            value,
        )
        if scp_match:
            path = scp_match.group("path").split("?", 1)[0].split("#", 1)[0]
            return f"{scp_match.group('host')}:{path}"
        return value

    def _resolve_migration_repository(
        self,
        values: list[object],
        *,
        directory_path: str,
        mapping_records: list[dict[str, Any]],
        field_context: str,
        allow_single_fallback: bool = True,
        allow_ancestor_match: bool = False,
        ignore_directory_scope: bool = False,
    ) -> str:
        directory = self._normalize_directory_path(directory_path or "root")
        eligible = (
            list(mapping_records)
            if ignore_directory_scope
            else [
                record
                for record in mapping_records
                if _is_same_or_descendant(directory, str(record.get("directory_path") or "root"))
            ]
        )
        matched: set[str] = set()
        concrete_values: list[str] = []
        for raw_value in values:
            value = str(raw_value or "").strip()
            if not value or value.startswith("${{"):
                continue
            concrete_values.append(value)
            value_path: str | None = None
            try:
                possible_path = Path(value).expanduser()
                if possible_path.is_absolute() or value.startswith((".", "~")):
                    value_path = str(possible_path.resolve(strict=False))
            except (OSError, ValueError):
                value_path = None
            canonical_identity: str | None = None
            try:
                canonical_identity = self._source_info(value).canonical_identity
            except (OSError, RuntimeError, ValueError):
                pass
            for record in eligible:
                aliases = {str(alias) for alias in record.get("aliases") or []}
                if value in aliases or (value_path is not None and value_path in aliases):
                    matched.add(str(record["managed_repo_id"]))
                    continue
                if value_path is not None and allow_ancestor_match:
                    legacy_path = Path(str(record["legacy_repository"])).resolve(strict=False)
                    candidate_prefix = Path(value_path)
                    if candidate_prefix == legacy_path or candidate_prefix in legacy_path.parents:
                        matched.add(str(record["managed_repo_id"]))
                        continue
                if canonical_identity and canonical_identity == record.get(
                    "canonical_repository_identity"
                ):
                    matched.add(str(record["managed_repo_id"]))
        if (
            not matched
            and allow_single_fallback
            and not concrete_values
            and len({str(item["managed_repo_id"]) for item in eligible}) == 1
        ):
            matched = {str(eligible[0]["managed_repo_id"])}
        if len(matched) != 1:
            detail = "no matching repository" if not matched else "multiple matching repositories"
            raise ManagedGitConflictError(
                f"Could not rewrite {field_context}: {detail}",
                details={
                    "directory_path": directory,
                    "values": [
                        self._safe_migration_detail_value(value)
                        for value in values
                        if value is not None
                    ],
                    "managed_repo_ids": sorted(matched),
                },
            )
        return next(iter(matched))

    @staticmethod
    def _legacy_routing_values(value: Mapping[str, Any]) -> list[object]:
        fields = (
            "managed_repo_id",
            "bare_repo_path",
            "bare_repo_name",
            "host_repo_path",
            "trusted_repo_path",
            "repo_path",
            "authorized_repo_path",
            "repo_source",
            "canonical_origin_url",
            "canonical_repository_url",
        )
        return [value.get(field) for field in fields if value.get(field) not in (None, "")]

    @classmethod
    def _rewrite_legacy_expressions(
        cls,
        value: object,
        *,
        repo_source_node_ids: set[str] | None = None,
    ) -> object:
        if isinstance(value, str):
            rewritten = value
            for field in _LEGACY_MANAGED_GIT_ROUTING_FIELDS:
                rewritten = re.sub(
                    rf"\.{re.escape(field)}(?=[^A-Za-z0-9_]|$)",
                    ".managed_repo_id",
                    rewritten,
                )
            if repo_source_node_ids is None:
                rewritten = re.sub(
                    r"\.repo_source(?=[^A-Za-z0-9_]|$)",
                    ".managed_repo_id",
                    rewritten,
                )
            else:
                for node_id in repo_source_node_ids:
                    rewritten = re.sub(
                        rf"(?<![A-Za-z0-9_-]){re.escape(node_id)}\.repo_source"
                        rf"(?=[^A-Za-z0-9_]|$)",
                        f"{node_id}.managed_repo_id",
                        rewritten,
                    )
            return rewritten
        if isinstance(value, list):
            return [
                cls._rewrite_legacy_expressions(item, repo_source_node_ids=repo_source_node_ids)
                for item in value
            ]
        if isinstance(value, dict):
            return {
                key: cls._rewrite_legacy_expressions(
                    item, repo_source_node_ids=repo_source_node_ids
                )
                for key, item in value.items()
            }
        return value

    def _rewrite_git_mapping(
        self,
        raw: Mapping[str, Any],
        *,
        directory_path: str,
        mapping_records: list[dict[str, Any]],
        field_context: str,
        include_repo_source: bool,
        include_repo_path: bool,
        team_config: bool = False,
        repo_source_node_ids: set[str] | None = None,
    ) -> tuple[dict[str, Any], str | None]:
        result = {
            str(key): self._rewrite_legacy_expressions(
                item, repo_source_node_ids=repo_source_node_ids
            )
            for key, item in raw.items()
        }
        legacy_fields = set(_LEGACY_MANAGED_GIT_FIELDS)
        if include_repo_source:
            legacy_fields.add("repo_source")
        if include_repo_path:
            legacy_fields.add("repo_path")
        has_legacy = bool(legacy_fields.intersection(result))
        existing_id = str(result.get("managed_repo_id") or "").strip()
        managed_repo_id: str | None = existing_id or None
        if has_legacy:
            managed_repo_id = self._resolve_migration_repository(
                self._legacy_routing_values(result),
                directory_path=directory_path,
                mapping_records=mapping_records,
                field_context=field_context,
            )
            result["managed_repo_id"] = managed_repo_id
        for field in legacy_fields:
            result.pop(field, None)
        if team_config:
            for field in (
                "canonical_origin_url",
                "canonical_repository_url",
                "provider",
                "refresh_source",
            ):
                result.pop(field, None)
        return result, managed_repo_id

    def _rewrite_nested_session_metadata(
        self,
        value: object,
        *,
        directory_path: str,
        mapping_records: list[dict[str, Any]],
        context: str,
        in_git_context: bool = False,
    ) -> tuple[object, set[str]]:
        repo_ids: set[str] = set()
        if isinstance(value, list):
            rewritten_list = []
            for index, item in enumerate(value):
                rewritten, nested_ids = self._rewrite_nested_session_metadata(
                    item,
                    directory_path=directory_path,
                    mapping_records=mapping_records,
                    context=f"{context}[{index}]",
                    in_git_context=in_git_context,
                )
                rewritten_list.append(rewritten)
                repo_ids.update(nested_ids)
            return rewritten_list, repo_ids
        if not isinstance(value, dict):
            return self._rewrite_legacy_expressions(value), repo_ids

        git_context = (
            in_git_context
            or "git" in context.casefold()
            or bool(_LEGACY_MANAGED_GIT_FIELDS.intersection(value))
        )
        rewritten: dict[str, Any] = {}
        for key, item in value.items():
            nested, nested_ids = self._rewrite_nested_session_metadata(
                item,
                directory_path=directory_path,
                mapping_records=mapping_records,
                context=f"{context}.{key}",
                in_git_context=git_context or "git" in str(key).casefold(),
            )
            rewritten[str(key)] = nested
            repo_ids.update(nested_ids)
        if git_context:
            rewritten_mapping, repo_id = self._rewrite_git_mapping(
                rewritten,
                directory_path=directory_path,
                mapping_records=mapping_records,
                field_context=context,
                include_repo_source=True,
                include_repo_path=True,
            )
            rewritten_mapping.pop("authorized_repo_path", None)
            rewritten = rewritten_mapping
            if repo_id:
                repo_ids.add(repo_id)
        return rewritten, repo_ids

    def _workflow_editor_rewrite(
        self,
        *,
        workflow_path: Path,
        rewritten_payload: dict[str, Any],
        inventory_record: Mapping[str, Any],
        live_documents: Mapping[Path, Mapping[str, Any]],
    ) -> _MigrationFileRewrite:
        live = live_documents.get(workflow_path.resolve(strict=False))
        reference = str(inventory_record.get("workflow_pack") or "").strip()
        expected_revision = str(inventory_record.get("revision") or "").strip()
        if live is None or not reference or not expected_revision:
            raise ManagedGitConflictError(
                f"Live workflow pack changed configuration before migration: {workflow_path}"
            )
        current_revision = str(live.get("revision") or "").strip()
        if current_revision != expected_revision:
            raise ManagedGitConflictError(
                f"Live workflow pack changed revision before migration: {reference}",
                details={
                    "expected_revision": expected_revision,
                    "current_revision": current_revision,
                },
            )
        raw_rewrite = self._json_rewrite(
            workflow_path,
            "workflow_pack_live",
            rewritten_payload,
        )
        return _MigrationFileRewrite(
            path=raw_rewrite.path,
            kind=raw_rewrite.kind,
            before=raw_rewrite.before,
            after=raw_rewrite.after,
            workflow_pack=reference,
            workflow_before_revision=expected_revision,
            workflow_after_revision=raw_rewrite.after_sha256,
        )

    def _build_migration_state_rewrites(
        self,
        *,
        mapping_records: list[dict[str, Any]],
        migration_id: str,
        workflow_inventory: list[dict[str, Any]] | None = None,
    ) -> tuple[list[_MigrationFileRewrite], list[dict[str, str]]]:
        rewrites: list[_MigrationFileRewrite] = []
        bindings: list[dict[str, str]] = []
        live_workflow_inventory = {
            Path(str(item.get("path") or "")).resolve(strict=False): item
            for item in (workflow_inventory or [])
            if isinstance(item, dict) and item.get("live") and item.get("path")
        }
        live_workflow_documents = (
            self._live_workflow_editor_documents() if live_workflow_inventory else {}
        )

        issues_path = self.runtime_dir / "local-issues.json"
        issues_payload = self._read_json_mapping(issues_path)
        if issues_payload is not None:
            changed = False
            for issue_key, raw_issue in issues_payload.items():
                if not isinstance(raw_issue, dict):
                    continue
                directory = str(raw_issue.get("directory_path") or "root")
                direct_legacy = bool(
                    (_LEGACY_MANAGED_GIT_ROUTING_FIELDS | {"repo_source"}).intersection(raw_issue)
                )
                metadata = raw_issue.get("metadata")
                metadata_legacy = bool(
                    self._legacy_field_paths(
                        metadata, include_repo_path=True, include_repo_source=True
                    )
                )
                if not direct_legacy and not metadata_legacy:
                    continue
                values = self._legacy_routing_values(raw_issue)
                if isinstance(metadata, dict):
                    values.extend(self._legacy_routing_values(metadata))
                repo_id = self._resolve_migration_repository(
                    values,
                    directory_path=directory,
                    mapping_records=mapping_records,
                    field_context=f"local issue {issue_key}",
                )
                raw_issue["managed_repo_id"] = repo_id
                for field in _LEGACY_MANAGED_GIT_ROUTING_FIELDS | {"repo_source"}:
                    raw_issue.pop(field, None)
                if isinstance(metadata, dict):
                    rewritten_metadata, _ = self._rewrite_nested_session_metadata(
                        metadata,
                        directory_path=directory,
                        mapping_records=mapping_records,
                        context=f"local issue {issue_key}.metadata",
                    )
                    raw_issue["metadata"] = rewritten_metadata
                issue_id = str(
                    raw_issue.get("issue_id") or raw_issue.get("issue_number") or issue_key
                )
                bindings.append(
                    {"managed_repo_id": repo_id, "kind": "issues", "binding_id": issue_id}
                )
                changed = True
            if changed:
                rewrites.append(self._json_rewrite(issues_path, "local_issues", issues_payload))

        sessions_path = self._session_store_path()
        sessions_payload = self._read_json_mapping(sessions_path)
        authorized_paths: set[Path] = set()
        authorized_session_ids: dict[Path, set[str]] = {}
        if sessions_payload is not None:
            changed = False
            for session_key, raw_session in sessions_payload.items():
                if not isinstance(raw_session, dict):
                    continue
                metadata = raw_session.get("metadata")
                session_authorized_paths: set[Path] = set()
                self._collect_authorized_keys_paths(metadata, session_authorized_paths)
                authorized_paths.update(session_authorized_paths)
                legacy_fields = self._legacy_field_paths(
                    metadata, include_repo_path=True, include_repo_source=True
                )
                has_singular_grant = isinstance(metadata, dict) and any(
                    isinstance(metadata.get(key), dict)
                    for key in LEGACY_MANAGED_GIT_GRANT_METADATA_KEYS
                )
                if not legacy_fields and not has_singular_grant:
                    continue
                directory = str(raw_session.get("directory_path") or "root")
                if legacy_fields:
                    rewritten_metadata, repo_ids = self._rewrite_nested_session_metadata(
                        metadata,
                        directory_path=directory,
                        mapping_records=mapping_records,
                        context=f"session {session_key}.metadata",
                    )
                else:
                    rewritten_metadata = dict(metadata) if isinstance(metadata, dict) else {}
                    repo_ids = set()
                if not isinstance(rewritten_metadata, dict):
                    rewritten_metadata = {}
                top_level_repo_id = str(rewritten_metadata.get("managed_repo_id") or "").strip()
                for legacy_key in LEGACY_MANAGED_GIT_GRANT_METADATA_KEYS:
                    legacy_grant = rewritten_metadata.get(legacy_key)
                    if not isinstance(legacy_grant, dict):
                        continue
                    grant_repo_id = str(
                        legacy_grant.get("managed_repo_id") or top_level_repo_id
                    ).strip()
                    if not grant_repo_id:
                        raise ManagedGitConflictError(
                            f"Session {session_key} has a Git grant without managed_repo_id"
                        )
                    canonical_grant = dict(legacy_grant)
                    canonical_grant["managed_repo_id"] = grant_repo_id
                    canonical_grant.setdefault("lifetime", "session")
                    grants = rewritten_metadata.get(MANAGED_GIT_GRANTS_METADATA_KEY)
                    if not isinstance(grants, dict):
                        grants = {}
                    grants[grant_repo_id] = canonical_grant
                    rewritten_metadata[MANAGED_GIT_GRANTS_METADATA_KEY] = grants
                    rewritten_metadata.pop(legacy_key, None)
                    repo_ids.add(grant_repo_id)
                if len(repo_ids) == 1:
                    rewritten_metadata["managed_repo_id"] = next(iter(repo_ids))
                rewritten_metadata["managed_git_remote_reconciliation_required"] = True
                rewritten_metadata["managed_git_migration_id"] = migration_id
                raw_session["metadata"] = rewritten_metadata
                session_id = str(raw_session.get("session_id") or session_key)
                for repo_id in repo_ids:
                    bindings.append(
                        {
                            "managed_repo_id": repo_id,
                            "kind": "sessions",
                            "binding_id": session_id,
                        }
                    )
                    if isinstance(metadata, dict) and isinstance(
                        metadata.get("container_git_write_access"), dict
                    ):
                        bindings.append(
                            {
                                "managed_repo_id": repo_id,
                                "kind": "authorized_sessions",
                                "binding_id": session_id,
                            }
                        )
                for path in session_authorized_paths:
                    authorized_session_ids.setdefault(path, set()).add(session_id)
                changed = True
            if changed:
                rewrites.append(self._json_rewrite(sessions_path, "sessions", sessions_payload))

        team_json_path = self.runtime_dir / "addons" / "team-addon" / "state.json"
        team_payload = self._read_json_mapping(team_json_path)
        if team_payload is not None:
            changed = self._rewrite_team_projects_payload(
                team_payload,
                mapping_records=mapping_records,
                bindings=bindings,
                source="Team state.json",
            )
            if changed:
                rewrites.append(self._json_rewrite(team_json_path, "team_state", team_payload))

        team_db_path = self.runtime_dir / "addons" / "team-addon" / "state.db"
        team_db_rewrite, team_db_bindings = self._team_database_rewrite(
            team_db_path,
            mapping_records=mapping_records,
        )
        if team_db_rewrite is not None:
            rewrites.append(team_db_rewrite)
            bindings.extend(team_db_bindings)

        for workflow_path in self._workflow_pack_paths():
            workflow_payload = self._read_json_mapping(workflow_path)
            if workflow_payload is None:
                continue
            rewritten_workflow, changed = self._rewrite_workflow_payload(
                workflow_payload,
                mapping_records=mapping_records,
                context=workflow_path.name,
            )
            if changed:
                inventory_record = live_workflow_inventory.get(workflow_path.resolve(strict=False))
                if inventory_record is None:
                    rewrites.append(
                        self._json_rewrite(
                            workflow_path,
                            "workflow_pack",
                            rewritten_workflow,
                        )
                    )
                else:
                    rewrites.append(
                        self._workflow_editor_rewrite(
                            workflow_path=workflow_path,
                            rewritten_payload=rewritten_workflow,
                            inventory_record=inventory_record,
                            live_documents=live_workflow_documents,
                        )
                    )

        for config_path in self._legacy_issue_setup_config_paths():
            config_payload = self._read_json_mapping(config_path)
            if config_payload is None or "git_root" not in config_payload:
                continue
            git_user = str(config_payload.get("git_user") or "").strip()
            if git_user:
                try:
                    authorized_paths.add(
                        Path(pwd.getpwnam(git_user).pw_dir) / ".ssh" / "authorized_keys"
                    )
                except KeyError:
                    pass
            config_payload.pop("git_root", None)
            rewrites.append(
                self._json_rewrite(
                    config_path,
                    "directory_issue_setup_config",
                    config_payload,
                )
            )

        configured_authorized_keys = str(
            getattr(self.config, "git_authorized_keys_path", "") or ""
        ).strip()
        if configured_authorized_keys:
            authorized_paths.add(Path(configured_authorized_keys).expanduser())
        env_authorized_keys = os.environ.get("EVANDER_GIT_AUTHORIZED_KEYS_PATH", "").strip()
        if env_authorized_keys:
            authorized_paths.add(Path(env_authorized_keys).expanduser())

        for path in sorted(authorized_paths, key=str):
            rewrite, auth_bindings = self._authorized_keys_rewrite(
                path,
                mapping_records=mapping_records,
                session_ids=authorized_session_ids.get(path, set()),
            )
            if rewrite is not None:
                rewrites.append(rewrite)
                bindings.extend(auth_bindings)

        unique_bindings = {
            (item["managed_repo_id"], item["kind"], item["binding_id"]): item for item in bindings
        }
        rewrites.sort(key=lambda item: str(item.path))
        return rewrites, [unique_bindings[key] for key in sorted(unique_bindings)]

    @staticmethod
    def _json_rewrite(
        path: Path,
        kind: str,
        payload: Mapping[str, Any],
    ) -> _MigrationFileRewrite:
        before = path.read_bytes()
        after = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        return _MigrationFileRewrite(
            path=path.resolve(strict=False), kind=kind, before=before, after=after
        )

    def _rewrite_team_projects_payload(
        self,
        payload: dict[str, Any],
        *,
        mapping_records: list[dict[str, Any]],
        bindings: list[dict[str, str]],
        source: str,
    ) -> bool:
        projects = payload.get("projects")
        if not isinstance(projects, dict):
            return False
        changed = False
        for project_key, raw_project in projects.items():
            if not isinstance(raw_project, dict) or not isinstance(raw_project.get("git"), dict):
                continue
            raw_git = raw_project["git"]
            if not self._legacy_field_paths(
                raw_git, include_repo_path=True, include_repo_source=True
            ):
                continue
            directory = str(
                raw_project.get("directory_path") or raw_git.get("directory_path") or "root"
            )
            rewritten_git, repo_id = self._rewrite_git_mapping(
                raw_git,
                directory_path=directory,
                mapping_records=mapping_records,
                field_context=f"{source} project {project_key}",
                include_repo_source=True,
                include_repo_path=True,
                team_config=True,
            )
            if repo_id is None:
                continue
            rewritten_git.pop("directory_path", None)
            raw_project["git"] = rewritten_git
            bindings.append(
                {
                    "managed_repo_id": repo_id,
                    "kind": "team_projects",
                    "binding_id": str(raw_project.get("id") or project_key),
                }
            )
            changed = True
        return changed

    def _team_database_rewrite(
        self,
        path: Path,
        *,
        mapping_records: list[dict[str, Any]],
    ) -> tuple[_MigrationFileRewrite | None, list[dict[str, str]]]:
        rows = self._read_team_database_git(path)
        if not rows:
            return None, []
        updates: list[tuple[str, str]] = []
        bindings: list[dict[str, str]] = []
        for project_id, raw_git in rows:
            if not self._legacy_field_paths(
                raw_git, include_repo_path=True, include_repo_source=True
            ):
                continue
            directory = str(raw_git.get("directory_path") or "root")
            rewritten_git, repo_id = self._rewrite_git_mapping(
                raw_git,
                directory_path=directory,
                mapping_records=mapping_records,
                field_context=f"Team state.db project {project_id}",
                include_repo_source=True,
                include_repo_path=True,
                team_config=True,
            )
            if repo_id is None:
                continue
            rewritten_git.pop("directory_path", None)
            updates.append(
                (json.dumps(rewritten_git, separators=(",", ":"), sort_keys=True), project_id)
            )
            bindings.append(
                {
                    "managed_repo_id": repo_id,
                    "kind": "team_projects",
                    "binding_id": project_id,
                }
            )
        if not updates:
            return None, []
        with tempfile.TemporaryDirectory(dir=self._migration_root) as temp_dir:
            temp_path = Path(temp_dir) / "state.db"
            with sqlite3.connect(path) as source, sqlite3.connect(temp_path) as target:
                source.backup(target)
            before = temp_path.read_bytes()
            with sqlite3.connect(temp_path) as connection:
                connection.executemany(
                    "UPDATE team_projects SET git_json = ? WHERE id = ?",
                    updates,
                )
                connection.commit()
                connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            after = temp_path.read_bytes()
        return (
            _MigrationFileRewrite(
                path=path.resolve(strict=False),
                kind="team_state_db",
                before=before,
                after=after,
            ),
            bindings,
        )

    def _rewrite_workflow_payload(
        self,
        payload: dict[str, Any],
        *,
        mapping_records: list[dict[str, Any]],
        context: str,
    ) -> tuple[dict[str, Any], bool]:
        changed = False
        clone_node_ids = self._workflow_clone_node_ids(payload)

        def visit(
            value: object,
            path: str,
            directory_path: str = "root",
            inherited_node_type: str = "",
        ) -> object:
            nonlocal changed
            if isinstance(value, list):
                return [
                    visit(item, f"{path}[{index}]", directory_path)
                    for index, item in enumerate(value)
                ]
            if not isinstance(value, dict):
                rewritten = self._rewrite_legacy_expressions(
                    value, repo_source_node_ids=clone_node_ids
                )
                changed = changed or rewritten != value
                return rewritten
            node_type = str(value.get("type") or value.get("provider") or inherited_node_type)
            node_directory = str(value.get("directory_path") or directory_path or "root")
            rewritten = {
                str(key): visit(
                    item,
                    f"{path}.{key}",
                    node_directory,
                    node_type if str(key) == "config" else "",
                )
                for key, item in value.items()
            }
            is_git_node = node_type.startswith(_MANAGED_GIT_NODE_PREFIXES) or bool(
                _LEGACY_MANAGED_GIT_FIELDS.intersection(rewritten)
            )
            if is_git_node:
                legacy_trigger_glob = rewritten.pop("bare_repo_name_glob", None)
                if legacy_trigger_glob is not None:
                    rewritten["managed_repo_id_glob"] = self._managed_id_for_legacy_glob(
                        str(legacy_trigger_glob),
                        directory_path=node_directory,
                        mapping_records=mapping_records,
                        field_context=f"workflow {context} {path}.bare_repo_name_glob",
                    )
                    changed = True
                legacy_here = bool(_LEGACY_MANAGED_GIT_ROUTING_FIELDS.intersection(rewritten))
                clone_node = node_type in {
                    "effect:agent:clone-repo",
                    "effect:vm:clone-repo",
                }
                if legacy_here or (clone_node and "repo_source" in rewritten):
                    rewritten, _ = self._rewrite_git_mapping(
                        rewritten,
                        directory_path=node_directory,
                        mapping_records=mapping_records,
                        field_context=f"workflow {context} {path}",
                        include_repo_source=clone_node,
                        include_repo_path=True,
                        repo_source_node_ids=clone_node_ids,
                    )
                    if clone_node:
                        rewritten.pop("directory_path", None)
                    changed = True
            changed = changed or rewritten != value
            return rewritten

        rewritten_payload = visit(payload, context)
        assert isinstance(rewritten_payload, dict)
        return rewritten_payload, changed

    def _managed_id_for_legacy_glob(
        self,
        legacy_glob: str,
        *,
        directory_path: str,
        mapping_records: list[dict[str, Any]],
        field_context: str,
    ) -> str:
        pattern = str(legacy_glob or "").strip()
        if not pattern or "${{" in pattern:
            raise ManagedGitConflictError(
                f"Could not rewrite {field_context}: the legacy repository glob is not exact"
            )
        directory = self._normalize_directory_path(directory_path or "root")
        matches = {
            str(record["managed_repo_id"])
            for record in mapping_records
            if _is_same_or_descendant(directory, str(record.get("directory_path") or "root"))
            and any(
                fnmatchcase(candidate, pattern)
                for candidate in self._legacy_repository_name_aliases(
                    record.get("legacy_name"),
                    record.get("legacy_repository"),
                )
            )
        }
        if len(matches) != 1:
            raise ManagedGitConflictError(
                f"Could not rewrite {field_context}: legacy glob must identify exactly one repository",
                details={
                    "legacy_glob": self._safe_migration_detail_value(pattern),
                    "managed_repo_ids": sorted(matches),
                },
            )
        return next(iter(matches))

    def _authorized_keys_rewrite(
        self,
        path: Path,
        *,
        mapping_records: list[dict[str, Any]],
        session_ids: set[str],
    ) -> tuple[_MigrationFileRewrite | None, list[dict[str, str]]]:
        if not path.is_file():
            return None, []
        before = path.read_bytes()
        lines = before.decode("utf-8").splitlines()
        changed = False
        bindings: list[dict[str, str]] = []
        rewritten_lines: list[str] = []
        for line in lines:
            assignments = list(_B64_ASSIGNMENT_PATTERN.finditer(line))
            if not assignments:
                rewritten_lines.append(line)
                continue
            repo_values: list[str] = []
            has_prefix = False
            for match in assignments:
                if match.group("name") == "EVANDER_GIT_AUTH_REPO_PREFIX_B64":
                    has_prefix = True
                if match.group("name") not in {
                    "EVANDER_GIT_AUTH_REPO_B64",
                    "EVANDER_GIT_AUTH_REPO_PREFIX_B64",
                }:
                    continue
                try:
                    repo_values.append(
                        base64.b64decode(match.group("value"), validate=True).decode("utf-8")
                    )
                except (ValueError, UnicodeDecodeError):
                    raise ManagedGitConflictError(
                        f"Invalid managed Git path encoding in authorized_keys: {path}"
                    ) from None
            if not repo_values:
                rewritten_lines.append(line)
                continue
            repo_id = self._resolve_migration_repository(
                repo_values,
                directory_path="root",
                mapping_records=mapping_records,
                field_context=f"authorized_keys {path}",
                allow_single_fallback=False,
                allow_ancestor_match=has_prefix,
                ignore_directory_scope=True,
            )
            record = self._require_record(repo_id)
            repo_path = str(self._bare_path(record))
            root_path = str(self._managed_root)

            def replace_assignment(match: re.Match[str]) -> str:
                name = match.group("name")
                if name == "EVANDER_GIT_ROOT_B64":
                    replacement = root_path
                elif name in {
                    "EVANDER_GIT_AUTH_REPO_B64",
                    "EVANDER_GIT_AUTH_REPO_PREFIX_B64",
                }:
                    replacement = repo_path
                    name = "EVANDER_GIT_AUTH_REPO_B64"
                else:
                    return match.group(0)
                return f"{name}={base64.b64encode(replacement.encode()).decode('ascii')}"

            rewritten_line = _B64_ASSIGNMENT_PATTERN.sub(replace_assignment, line)
            if "EVANDER_GIT_REFRESH_REQUIRED=" not in rewritten_line:
                rewritten_line = rewritten_line.replace(
                    " EVANDER_GIT_AUTH_REPO_B64=",
                    " EVANDER_GIT_REFRESH_REQUIRED=1 EVANDER_GIT_AUTH_REPO_B64=",
                    1,
                )
            if has_prefix and "EVANDER_GIT_AUTH_REPO_PREFIX_B64" in rewritten_line:
                raise ManagedGitConflictError(
                    f"Could not narrow prefix-scoped authorized key in {path}"
                )
            changed = changed or rewritten_line != line
            rewritten_lines.append(rewritten_line)
            line_session_ids: set[str] = set()
            has_session_assignment = False
            for match in assignments:
                if match.group("name") != "EVANDER_GIT_AUTH_SESSION_ID_B64":
                    continue
                has_session_assignment = True
                try:
                    decoded_session = base64.b64decode(match.group("value"), validate=True).decode(
                        "utf-8"
                    )
                except (ValueError, UnicodeDecodeError):
                    raise ManagedGitConflictError(
                        f"Invalid managed Git session encoding in authorized_keys: {path}"
                    ) from None
                decoded_session = decoded_session.strip()
                if not decoded_session or "/" in decoded_session or decoded_session in {".", ".."}:
                    raise ManagedGitConflictError(
                        f"Invalid managed Git session id in authorized_keys: {path}"
                    )
                line_session_ids.add(decoded_session)
            if not has_session_assignment and len(session_ids) == 1:
                line_session_ids.update(session_ids)
            for session_id in line_session_ids:
                bindings.append(
                    {
                        "managed_repo_id": repo_id,
                        "kind": "authorized_sessions",
                        "binding_id": session_id,
                    }
                )
        if not changed:
            return None, []
        after = ("\n".join(rewritten_lines) + ("\n" if lines else "")).encode("utf-8")
        return (
            _MigrationFileRewrite(
                path=path.resolve(strict=False),
                kind="authorized_keys",
                before=before,
                after=after,
            ),
            bindings,
        )

    @staticmethod
    def _migration_blockers(inventory: Mapping[str, Any]) -> list[str]:
        blockers: list[str] = []
        active_sessions = list(inventory.get("active_session_ids") or [])
        if active_sessions:
            blockers.append("active agent sessions remain")
        if inventory.get("active_git_operations"):
            blockers.append("managed Git operations remain active")
        active_workers = sorted(
            unit
            for unit, state in (inventory.get("worker_service_states") or {}).items()
            if state in {"active", "activating", "reloading"}
        )
        if active_workers:
            blockers.append(
                "managed Git migration writer services must be stopped: "
                + ", ".join(active_workers)
            )
        active_worker_locks = sorted(
            worker
            for worker, state in (inventory.get("worker_lock_states") or {}).items()
            if state == "active"
        )
        if active_worker_locks:
            blockers.append(
                "managed Git migration worker processes must be stopped: "
                + ", ".join(active_worker_locks)
            )
        missing = [
            item["name"]
            for item in inventory.get("bare_repositories") or []
            if not item.get("has_canonical_source")
        ]
        if missing:
            blockers.append(
                "bare repositories are missing canonical metadata: " + ", ".join(missing)
            )
        missing_directories = [
            item["name"]
            for item in inventory.get("bare_repositories") or []
            if not item.get("has_directory_metadata")
        ]
        if missing_directories:
            blockers.append(
                "bare repositories are missing owning-directory metadata: "
                + ", ".join(missing_directories)
            )
        custom_git_root = str(inventory.get("custom_legacy_git_root") or "").strip()
        if custom_git_root:
            blockers.append(
                "EVANDER_GIT_ROOT points outside Evander-owned rollback roots; relocate "
                "those repositories or unset the override before migration"
            )
        collisions = list(inventory.get("preserved_ref_collisions") or [])
        if collisions:
            blockers.append("preserved refs collide across legacy repositories")
        state_references = sum(
            len(inventory.get(field) or [])
            for field in (
                "team_projects_with_legacy_git_fields",
                "session_bindings_with_legacy_git_fields",
                "issue_bindings_with_legacy_git_fields",
                "workflow_pack_files_with_legacy_fields",
            )
        )
        if state_references and not inventory.get("groups"):
            blockers.append(
                "legacy managed Git state is missing matching canonical repository metadata"
            )
        prefix_keys = [
            item
            for item in inventory.get("authorized_key_bindings") or []
            if item.get("repository_prefix")
        ]
        if prefix_keys and len(inventory.get("groups") or []) != 1:
            blockers.append(
                "prefix-scoped authorized keys cannot be narrowed to one managed repository"
            )
        return blockers

    @staticmethod
    def _is_preserved_migration_ref(ref_name: str) -> bool:
        return ref_name.startswith(
            (
                "refs/heads/agents/",
                "refs/heads/review/",
                "refs/heads/team/",
                "refs/notes/",
                "refs/pull/",
                "refs/evander/pr/",
            )
        )

    def _import_legacy_preserved_refs(
        self,
        record: Mapping[str, Any],
        candidates: list[dict[str, Any]],
        *,
        migration_id: str,
    ) -> dict[str, str]:
        bare_path = self._bare_path(record)
        desired: dict[str, str] = {}
        for candidate in candidates:
            for ref_name, object_id in candidate["preserved_refs"].items():
                previous = desired.get(ref_name)
                if previous is not None and previous != object_id:
                    raise ManagedGitConflictError(
                        f"Preserved ref collision for {ref_name}",
                        details={"object_ids": sorted({previous, object_id})},
                    )
                desired[ref_name] = object_id
        if not desired:
            return {}
        existing = self._list_refs(
            bare_path,
            ("refs/heads", "refs/notes", "refs/evander/pr", "refs/pull"),
        )
        for ref_name, object_id in desired.items():
            current = existing.get(ref_name)
            if current is not None and current != object_id:
                raise ManagedGitConflictError(
                    f"Managed repository already has a conflicting preserved ref {ref_name}",
                    details={"object_ids": sorted({current, object_id})},
                )
        stage_root = f"refs/evander/migration/{migration_id}/{uuid4().hex}"
        staged: dict[str, tuple[str, str]] = {}
        try:
            index = 0
            for candidate in candidates:
                legacy_path = str(candidate["path"])
                for ref_name, object_id in sorted(candidate["preserved_refs"].items()):
                    if ref_name in staged or existing.get(ref_name) == object_id:
                        continue
                    stage_ref = f"{stage_root}/{index}"
                    index += 1
                    self._run_git(
                        [
                            "git",
                            f"--git-dir={bare_path}",
                            "fetch",
                            "--no-tags",
                            "--no-write-fetch-head",
                            legacy_path,
                            f"+{ref_name}:{stage_ref}",
                        ]
                    )
                    fetched = self._resolve_ref(bare_path, stage_ref)
                    if fetched != object_id:
                        raise ManagedGitConflictError(
                            f"Legacy ref {ref_name} moved during migration"
                        )
                    staged[ref_name] = (stage_ref, object_id)
            creates = [
                f"create {ref_name} {object_id}"
                for ref_name, (_stage_ref, object_id) in sorted(staged.items())
                if ref_name not in existing
            ]
            if creates:
                self._run_update_ref_transaction(bare_path, creates)
            return dict(sorted(desired.items()))
        finally:
            self._delete_namespace(bare_path, stage_root)

    def _enforce_rollback_artifacts_read_only(self, manifest: Mapping[str, Any]) -> None:
        for raw_path in manifest.get("rollback_artifacts") or []:
            path = Path(str(raw_path)).resolve(strict=False)
            if not self._is_evander_owned_rollback_path(path):
                raise ManagedGitConflictError(
                    "Migration manifest contains a non-Evander rollback artifact"
                )
            self._make_tree_read_only(path)

    @staticmethod
    def _make_tree_read_only(path: Path) -> None:
        if not path.exists():
            return
        children = sorted(path.rglob("*"), key=lambda child: len(child.parts), reverse=True)
        targets = [*children, path]
        for child in targets:
            if child.is_symlink():
                continue
            try:
                mode = child.stat().st_mode
                child.chmod((mode & ~0o222) | (0o500 if child.is_dir() else 0o400))
                if child.stat().st_mode & 0o222:
                    raise OSError("write permissions remain set")
            except OSError as exc:
                raise ManagedGitConflictError(
                    "Could not make a managed Git migration rollback artifact read-only",
                    details={"path": str(path)},
                ) from exc

    @staticmethod
    def _make_tree_writable(path: Path) -> None:
        if not path.exists():
            return
        for child in path.rglob("*"):
            if child.is_symlink():
                continue
            try:
                child.chmod(child.stat().st_mode | 0o700)
            except OSError:
                continue
        try:
            path.chmod(path.stat().st_mode | 0o700)
        except OSError:
            pass

    def _active_session_ids(self) -> list[str]:
        repository = getattr(self.runtime, "session_repository", None)
        if repository is None:
            raw_sessions = self._read_json_mapping(self._session_store_path()) or {}
            sessions = list(raw_sessions.values())
        else:
            read_all = getattr(repository, "read_all", None)
            if callable(read_all):
                sessions = list(read_all().values())
            else:
                list_sessions = getattr(repository, "list_sessions", None)
                sessions = list_sessions() if callable(list_sessions) else []
        inventoried_statuses: dict[str, str] = {}
        inventory = self._agent_inventory_admin
        list_agents = getattr(inventory, "list_agents", None)
        if callable(list_agents):
            try:
                records = list_agents(
                    all_directories=True,
                    include_inactive=True,
                    reconcile_containers=False,
                    summary_only=True,
                )
            except Exception:
                records = []
            for record in records:
                record_session_id = str(
                    (
                        record.get("session_id")
                        if isinstance(record, dict)
                        else getattr(record, "session_id", "")
                    )
                    or ""
                ).strip()
                record_status = (
                    str(
                        (
                            record.get("status")
                            if isinstance(record, dict)
                            else getattr(record, "status", "")
                        )
                        or ""
                    )
                    .strip()
                    .casefold()
                )
                if record_session_id and record_status:
                    inventoried_statuses[record_session_id] = record_status
        active: list[str] = []
        now = datetime.now(UTC)
        for session in sessions:
            get_value = (
                session.get
                if isinstance(session, dict)
                else lambda key, default=None: getattr(session, key, default)
            )
            session_id = str(get_value("session_id", "") or "").strip()
            metadata = get_value("metadata", {})
            metadata = metadata if isinstance(metadata, Mapping) else {}
            container_lifecycle = metadata.get("container_lifecycle")
            container_lifecycle = (
                container_lifecycle if isinstance(container_lifecycle, Mapping) else {}
            )
            status = (
                inventoried_statuses.get(session_id)
                or str(
                    get_value("status", "")
                    or metadata.get("agent_status")
                    or metadata.get("session_status")
                    or container_lifecycle.get("state")
                    or ""
                )
                .strip()
                .casefold()
            )
            if status in {
                "deleted",
                "stopped",
                "idle",
                "prepared",
                "expired",
                "terminated",
                "failed",
                "stale",
                "destroyed",
                "cleaned_up",
                "abandoned",
                "cleanup_failed",
            }:
                continue
            expires_at = get_value("expires_at")
            if isinstance(expires_at, str):
                try:
                    expires_at = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
                except ValueError:
                    expires_at = None
            if isinstance(expires_at, datetime):
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=UTC)
                if expires_at <= now:
                    continue
            if session_id:
                active.append(session_id)
        return sorted(active)

    def _find_accessible_identity(
        self, directory_path: str, canonical_identity: str
    ) -> dict[str, Any] | None:
        records = self._read_registry()["repositories"].values()
        matches = [
            record
            for record in records
            if record.get("canonical_repository_identity") == canonical_identity
            and _is_same_or_descendant(directory_path, str(record.get("directory_path", "")))
        ]
        if not matches:
            return None
        return max(matches, key=lambda record: len(str(record["directory_path"]).split("/")))

    def _find_descendant_identity(
        self,
        directory_path: str,
        canonical_identity: str,
    ) -> dict[str, Any] | None:
        matches = [
            dict(record)
            for record in self._read_registry()["repositories"].values()
            if record.get("canonical_repository_identity") == canonical_identity
            and str(record.get("directory_path") or "") != directory_path
            and _is_same_or_descendant(
                str(record.get("directory_path") or ""),
                directory_path,
            )
        ]
        if not matches:
            return None
        if len(matches) > 1:
            raise ManagedGitConflictError(
                "The upstream is already registered in multiple descendant trust boundaries; "
                "delete or migrate those registrations before registering their ancestor",
                details={
                    "managed_repo_ids": sorted(str(record["managed_repo_id"]) for record in matches)
                },
            )
        return matches[0]

    def _promote_descendant_registration(
        self,
        record: Mapping[str, Any],
        *,
        directory_path: str,
        source: _SourceInfo,
    ) -> dict[str, Any]:
        """Move an unused child registration to its newly requested ancestor owner.

        The registry ID is derived from its owning directory, so widening a trust boundary
        requires an atomic re-home rather than silently retaining an ID whose hash no longer
        describes its owner. Registrations that have acquired any capability-bearing state are
        deliberately refused and must be migrated explicitly by an operator.
        """

        old_id = str(record["managed_repo_id"])
        new_id = self._managed_repo_id(directory_path, source.canonical_identity)
        if new_id == old_id:
            return dict(record)
        with self._repo_lock(old_id, exclusive=True):
            current = self._require_record(old_id)
            if current.get("bindings"):
                raise ManagedGitConflictError(
                    "The descendant managed repository has active bindings and cannot be "
                    "moved to an ancestor trust boundary",
                    details={"managed_repo_id": old_id},
                )
            if self._has_registered_worktrees(current):
                raise ManagedGitConflictError(
                    "The descendant managed repository has active or unreconciled worktrees",
                    details={"managed_repo_id": old_id},
                )
            if self._has_unresolved_migration(old_id):
                raise ManagedGitConflictError(
                    "The descendant managed repository is part of an unresolved migration",
                    details={"managed_repo_id": old_id},
                )
            bare_path = self._bare_path(current)
            capability_refs = self._list_refs(
                bare_path,
                (
                    "refs/heads/agents",
                    "refs/heads/review",
                    "refs/heads/team",
                    "refs/notes",
                    "refs/evander",
                ),
            )
            if capability_refs:
                raise ManagedGitConflictError(
                    "The descendant managed repository contains unpublished or operation refs "
                    "and cannot be moved to an ancestor trust boundary",
                    details={"managed_repo_id": old_id},
                )
            if self._record_or_none(new_id) is not None:
                raise ManagedGitConflictError(f"Managed repository ID collision for {new_id}")

            new_bare_path = self._managed_root / f"{new_id}.git"
            updated = dict(current)
            updated.update(
                {
                    "managed_repo_id": new_id,
                    "directory_path": directory_path,
                    "canonical_repository_identity": source.canonical_identity,
                    "canonical_repository_url": source.canonical_url,
                    "provider": source.provider,
                    "display_name": source.display_name,
                    "refresh_source": source.refresh_source,
                    "refresh_source_kind": source.refresh_source_kind,
                    "internal_bare_path": str(new_bare_path),
                    "host_commit_identity": self._commit_identity_for_directory(directory_path),
                    "updated_at": _utc_now(),
                }
            )
            bare_path.rename(new_bare_path)
            try:
                self._run_git(
                    [
                        "git",
                        f"--git-dir={new_bare_path}",
                        "config",
                        "evander.managedRepoId",
                        new_id,
                    ]
                )
                self._run_git(
                    [
                        "git",
                        f"--git-dir={new_bare_path}",
                        "config",
                        "evander.directoryPath",
                        directory_path,
                    ]
                )

                def replace(payload: dict[str, Any]):
                    stored = payload["repositories"].get(old_id)
                    if not isinstance(stored, dict) or stored != current:
                        raise ManagedGitConflictError(
                            f"Managed repository {old_id} changed during registration"
                        )
                    if new_id in payload["repositories"]:
                        raise ManagedGitConflictError(
                            f"Managed repository ID collision for {new_id}"
                        )
                    del payload["repositories"][old_id]
                    payload["repositories"][new_id] = dict(updated)

                self._update_registry(replace)
            except BaseException:
                try:
                    self._run_git(
                        [
                            "git",
                            f"--git-dir={new_bare_path}",
                            "config",
                            "evander.managedRepoId",
                            old_id,
                        ],
                        check=False,
                    )
                    self._run_git(
                        [
                            "git",
                            f"--git-dir={new_bare_path}",
                            "config",
                            "evander.directoryPath",
                            str(current["directory_path"]),
                        ],
                        check=False,
                    )
                finally:
                    if new_bare_path.exists() and not bare_path.exists():
                        new_bare_path.rename(bare_path)
                raise
        return updated

    def _record_or_none(self, managed_repo_id: str) -> dict[str, Any] | None:
        record = self._read_registry()["repositories"].get(str(managed_repo_id or "").strip())
        return dict(record) if isinstance(record, dict) else None

    def _require_record(self, managed_repo_id: str) -> dict[str, Any]:
        self._require_migration_preflight()
        normalized = str(managed_repo_id or "").strip()
        record = self._record_or_none(normalized)
        if record is None:
            raise ManagedGitNotFoundError(f"Unknown managed repository: {normalized}")
        return record

    def _require_scoped_record(
        self,
        managed_repo_id: str,
        directory_path: str,
    ) -> dict[str, Any]:
        directory = self._normalize_directory_path(directory_path)
        record = self._require_record(managed_repo_id)
        owner = str(record["directory_path"])
        if not _is_same_or_descendant(directory, owner):
            # Preserve not-found semantics so a sibling trust boundary cannot use
            # this check to enumerate managed repository IDs.
            raise ManagedGitNotFoundError(
                f"Managed repository {managed_repo_id!r} is not available to {directory!r}"
            )
        return record

    @contextmanager
    def _migration_preflight_bypass(self):
        depth = int(getattr(self._migration_preflight_context, "depth", 0))
        self._migration_preflight_context.depth = depth + 1
        try:
            yield
        finally:
            self._migration_preflight_context.depth = depth

    def _require_migration_preflight(self) -> None:
        if int(getattr(self._migration_preflight_context, "depth", 0)) > 0:
            return
        registry = self._read_registry()
        migration_applied = any(
            isinstance(migration, dict) and migration.get("status") in {"applied", "finalized"}
            for migration in registry.get("migrations", {}).values()
        )
        if not self._legacy_managed_git_artifacts_present(
            allow_declared_rollback=migration_applied
        ):
            return
        raise ManagedGitConflictError(
            "Legacy managed Git state requires explicit migration before managed Git can run. "
            "Stop trigger/effect workers, run "
            "'evander-cli git-repos migrate --dry-run', then "
            "'evander-cli git-repos migrate --apply'."
        )

    def _legacy_managed_git_artifacts_present(
        self,
        *,
        allow_declared_rollback: bool = False,
    ) -> bool:
        declared_rollback: set[Path] = set()
        if allow_declared_rollback:
            for manifest_path in self._migration_root.glob("*.json"):
                try:
                    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if manifest.get("status") != "applied":
                    continue
                declared_rollback.update(
                    Path(str(path)).resolve(strict=False)
                    for path in manifest.get("rollback_artifacts") or []
                )
        legacy_roots = [self.project_dir / "git-remotes", self.runtime_dir / "repo-cache"]
        configured_root = os.environ.get("EVANDER_GIT_ROOT", "").strip()
        if configured_root:
            path = Path(configured_root).expanduser()
            if not path.is_absolute():
                path = self.project_dir / path
            legacy_roots.append(path.resolve(strict=False))
        for root in legacy_roots:
            if root.resolve(strict=False) == self._managed_root:
                continue
            if root.exists():
                for path in root.iterdir():
                    if not path.is_dir():
                        continue
                    if path.resolve(strict=False) in declared_rollback:
                        continue
                    return True
        state = self._legacy_state_inventory()
        legacy_authorized_keys = any(
            item.get("repository_prefix")
            or not self._is_managed_authorized_repository_path(
                str(item.get("repository_path") or "")
            )
            for item in state.get("authorized_key_bindings") or []
        )
        return (
            any(
                state.get(field)
                for field in (
                    "team_projects_with_legacy_git_fields",
                    "session_bindings_with_legacy_git_fields",
                    "issue_bindings_with_legacy_git_fields",
                    "workflow_pack_files_with_legacy_fields",
                    "legacy_configuration_files",
                )
            )
            or legacy_authorized_keys
        )

    def _is_managed_authorized_repository_path(self, raw_path: str) -> bool:
        if not raw_path or raw_path == "<invalid>":
            return False
        path = Path(raw_path).resolve(strict=False)
        try:
            relative = path.relative_to(self._managed_root.resolve(strict=False))
        except ValueError:
            return False
        return (
            len(relative.parts) == 1
            and re.fullmatch(r"mr_[0-9a-f]{20}\.git", relative.name) is not None
        )

    def _put_new_record(self, record: dict[str, Any]) -> None:
        repo_id = str(record["managed_repo_id"])

        def put(payload: dict[str, Any]):
            existing = payload["repositories"].get(repo_id)
            if existing is not None and existing != record:
                raise ManagedGitConflictError(
                    f"Managed repository {repo_id} was registered concurrently"
                )
            payload["repositories"][repo_id] = dict(record)

        self._update_registry(put)

    def _update_record_fields(self, managed_repo_id: str, **fields: Any) -> None:
        def update(payload: dict[str, Any]):
            record = payload["repositories"].get(managed_repo_id)
            if not isinstance(record, dict):
                raise ManagedGitNotFoundError(f"Unknown managed repository: {managed_repo_id}")
            record.update(fields)
            record["updated_at"] = _utc_now()

        self._update_registry(update)

    @staticmethod
    def _public_record(record: Mapping[str, Any]) -> dict[str, Any]:
        bindings = [dict(item) for item in record.get("bindings") or [] if isinstance(item, dict)]
        bindings_by_kind: dict[str, list[str]] = {}
        for binding in bindings:
            bindings_by_kind.setdefault(str(binding["kind"]), []).append(str(binding["binding_id"]))
        return {
            "managed_repo_id": record.get("managed_repo_id"),
            "directory_path": record.get("directory_path"),
            "canonical_repository_identity": record.get("canonical_repository_identity"),
            "canonical_repository_url": record.get("canonical_repository_url"),
            "provider": record.get("provider"),
            "display_name": record.get("display_name"),
            "default_branch": record.get("default_branch"),
            "host_commit_identity": dict(record.get("host_commit_identity") or {}),
            "created_at": record.get("created_at"),
            "updated_at": record.get("updated_at"),
            "refreshed_at": record.get("refreshed_at"),
            "maintenance_at": record.get("maintenance_at"),
            "bindings": bindings,
            "bindings_by_kind": bindings_by_kind,
        }

    def _commit_identity_for_directory(self, directory_path: str) -> dict[str, str]:
        resolved_config = self.config
        resolver = getattr(self.runtime, "provider_config", None)
        resolve = getattr(resolver, "resolve_for_directory", None)
        if callable(resolve):
            try:
                resolved_config = resolve(directory_path)
            except Exception:
                resolved_config = self.config
        name = (
            str(
                getattr(resolved_config, "managed_git_commit_name", "")
                or getattr(resolved_config, "git_committer_name", "")
                or DEFAULT_HOST_COMMIT_NAME
            ).strip()
            or DEFAULT_HOST_COMMIT_NAME
        )
        email = (
            str(
                getattr(resolved_config, "managed_git_commit_email", "")
                or getattr(resolved_config, "git_committer_email", "")
                or DEFAULT_HOST_COMMIT_EMAIL
            ).strip()
            or DEFAULT_HOST_COMMIT_EMAIL
        )
        return {"name": name, "email": email}

    def _initialize_bare_repository(self, record: Mapping[str, Any]) -> None:
        bare_path = self._bare_path(record)
        script = self.project_dir / "scripts" / "create_git_remote.sh"
        if script.exists():
            env = os.environ.copy()
            env["EVANDER_GIT_ROOT"] = str(self._managed_root)
            env["EVANDER_RUNTIME_DIR"] = str(self.runtime_dir)
            self._run_git([str(script), str(record["managed_repo_id"]), "agents"], env=env)
        else:
            self._run_git(["git", "init", "--bare", str(bare_path)])
        if not (bare_path / "HEAD").exists():
            raise RuntimeError("Managed bare repository initialization did not create HEAD")
        self._run_git(
            [
                "git",
                f"--git-dir={bare_path}",
                "config",
                "evander.managedRepoId",
                str(record["managed_repo_id"]),
            ]
        )
        self._run_git(
            [
                "git",
                f"--git-dir={bare_path}",
                "config",
                "evander.directoryPath",
                str(record["directory_path"]),
            ]
        )
        self._run_git(
            [
                "git",
                f"--git-dir={bare_path}",
                "config",
                "evander.baseBranch",
                str(record["default_branch"]),
            ]
        )
        self._assert_secret_free_bare_config(bare_path)

    def _refresh_record(
        self,
        record: Mapping[str, Any],
        *,
        credential_override: tuple[str | None, str | None] | None = None,
    ) -> dict[str, Any]:
        bare_path = self._bare_path(record)
        source = str(record["refresh_source"])
        operation_id = uuid4().hex
        stage_root = f"refs/evander/refresh/{operation_id}"
        stage_heads = f"{stage_root}/heads"
        stage_tags = f"{stage_root}/tags"
        env = self._git_environment(record, credential_override=credential_override)
        try:
            self._run_git(
                [
                    "git",
                    f"--git-dir={bare_path}",
                    "fetch",
                    "--no-tags",
                    "--no-write-fetch-head",
                    source,
                    f"+refs/heads/*:{stage_heads}/*",
                    f"+refs/tags/*:{stage_tags}/*",
                ],
                env=env,
            )
            staged = self._list_refs(bare_path, (stage_heads, stage_tags))
            reserved = sorted(
                ref
                for ref in staged
                if ref.startswith(f"{stage_heads}/")
                and ref[len(stage_heads) + 1 :].split("/", 1)[0] in _RESERVED_BRANCH_ROOTS
            )
            if reserved:
                branches = ", ".join(ref[len(stage_heads) + 1 :] for ref in reserved)
                raise ManagedGitConflictError(
                    f"Upstream contains Evander-reserved branch namespaces: {branches}"
                )
            desired: dict[str, str] = {}
            for ref, oid in staged.items():
                if ref.startswith(f"{stage_heads}/"):
                    live_ref = f"refs/heads/{ref[len(stage_heads) + 1 :]}"
                elif ref.startswith(f"{stage_tags}/"):
                    live_ref = f"refs/tags/{ref[len(stage_tags) + 1 :]}"
                else:
                    continue
                desired[live_ref] = oid
            current_all = self._list_refs(bare_path, ("refs/heads", "refs/tags"))
            current = {
                ref: oid
                for ref, oid in current_all.items()
                if not (
                    ref.startswith("refs/heads/")
                    and ref[len("refs/heads/") :].split("/", 1)[0] in _RESERVED_BRANCH_ROOTS
                )
            }
            discovered_default = self._discover_default_branch(source, env=env)
            if not discovered_default:
                discovered_default = self._fallback_default_branch(desired)
            default_branch = (
                str(record.get("default_branch") or "").strip()
                if record.get("default_branch_source") == "override"
                else discovered_default
            )
            if (
                record.get("default_branch_source") == "override"
                and default_branch
                and f"refs/heads/{default_branch}" not in desired
            ):
                raise ManagedGitConflictError(
                    f"Configured default branch {default_branch!r} is not present upstream"
                )
            updates: list[str] = []
            for ref in sorted(current.keys() - desired.keys()):
                updates.append(f"delete {ref} {current[ref]}")
            for ref in sorted(desired):
                if current.get(ref) == desired[ref]:
                    continue
                if ref in current:
                    updates.append(f"update {ref} {desired[ref]} {current[ref]}")
                else:
                    updates.append(f"create {ref} {desired[ref]}")
            if updates:
                self._run_update_ref_transaction(bare_path, updates)
            if default_branch and f"refs/heads/{default_branch}" in desired:
                self._run_git(
                    [
                        "git",
                        f"--git-dir={bare_path}",
                        "symbolic-ref",
                        "HEAD",
                        f"refs/heads/{default_branch}",
                    ]
                )
                self._run_git(
                    [
                        "git",
                        f"--git-dir={bare_path}",
                        "config",
                        "evander.baseBranch",
                        default_branch,
                    ]
                )
            refreshed_at = _utc_now()
            return {
                "managed_repo_id": record["managed_repo_id"],
                "refreshed_at": refreshed_at,
                "default_branch": default_branch,
                "updated_ref_count": len(updates),
            }
        finally:
            self._delete_namespace(bare_path, stage_root)

    def _discover_default_branch(self, source: str, *, env: Mapping[str, str]) -> str | None:
        result = self._run_git(
            ["git", "ls-remote", "--symref", source, "HEAD"],
            env=env,
            check=False,
        )
        if result.returncode != 0:
            return None
        for line in result.stdout.splitlines():
            match = re.match(r"^ref:\s+refs/heads/(\S+)\s+HEAD$", line.strip())
            if match:
                return match.group(1)
        return None

    @staticmethod
    def _fallback_default_branch(desired: Mapping[str, str]) -> str | None:
        for candidate in ("main", "master"):
            if f"refs/heads/{candidate}" in desired:
                return candidate
        branches = sorted(
            ref[len("refs/heads/") :] for ref in desired if ref.startswith("refs/heads/")
        )
        return branches[0] if branches else None

    def _git_environment(
        self,
        record: Mapping[str, Any],
        *,
        credential_override: tuple[str | None, str | None] | None = None,
    ) -> dict[str, str]:
        env = os.environ.copy()
        env.pop("EVANDER_GIT_USERNAME", None)
        env.pop("EVANDER_GIT_PASSWORD", None)
        env.update(
            {
                "GIT_TERMINAL_PROMPT": "0",
                "GCM_INTERACTIVE": "never",
                "GIT_ASKPASS": str(self.project_dir / "scripts" / "managed_git_askpass.sh"),
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
            }
        )
        username, password = credential_override or (None, None)
        resolved_username, resolved_password = self._provider_credentials(record)
        username = username or resolved_username
        password = password or resolved_password
        if username:
            env["EVANDER_GIT_USERNAME"] = username
        if password:
            env["EVANDER_GIT_PASSWORD"] = password
        return self._with_git_config_environment(
            env,
            {
                "credential.helper": "",
                "core.askPass": str(self.project_dir / "scripts" / "managed_git_askpass.sh"),
            },
        )

    def _resolved_config_for_record(self, record: Mapping[str, Any]) -> Any:
        resolved_config = self.config
        resolver = getattr(self.runtime, "provider_config", None)
        resolve = getattr(resolver, "resolve_for_directory", None)
        if callable(resolve):
            resolved_config = resolve(str(record["directory_path"]))
        return resolved_config

    def _provider_credentials(
        self,
        record: Mapping[str, Any],
    ) -> tuple[str | None, str | None]:
        provider = str(record.get("provider") or "generic")
        resolved_config = self._resolved_config_for_record(record)
        username: str | None = None
        password: str | None = None
        if provider == "github":
            token_env = str(getattr(resolved_config, "github_token_env", "") or "GITHUB_TOKEN")
            password = os.environ.get(token_env) or os.environ.get("GH_TOKEN")
            username = "x-access-token"
        elif provider == "azure_devops":
            token_env = str(
                getattr(resolved_config, "azure_devops_pr_pat_env", "")
                or getattr(resolved_config, "azure_devops_pat_env", "")
                or "AZURE_DEVOPS_PR_PAT"
            )
            password = os.environ.get(token_env)
            username = "evander"
        return username, password

    def _safe_worktree_environment(self) -> dict[str, str]:
        return self._worktrees.safe_environment()

    @staticmethod
    def _with_git_config_environment(
        env: Mapping[str, str], config: Mapping[str, str]
    ) -> dict[str, str]:
        return _with_git_config_environment(env, config)

    def _sanitize_managed_worktree_error(
        self,
        message: str,
        *,
        operation_dir: Path,
    ) -> str:
        return self._worktrees.sanitize_error(message, operation_dir=operation_dir)

    def _has_registered_worktrees(self, record: Mapping[str, Any]) -> bool:
        return self._worktrees.has_registered_worktrees(record)

    def _has_unresolved_migration(self, managed_repo_id: str) -> bool:
        for migration in self._read_registry()["migrations"].values():
            if not isinstance(migration, dict) or migration.get("status") == "finalized":
                continue
            ids = migration.get("managed_repo_ids") or []
            if not ids or managed_repo_id in ids:
                return True
        return False

    def _active_operation_locks(self) -> bool:
        for lock_path in self._lock_root.glob("*.lock"):
            if lock_path.name == "migration.lock":
                continue
            with lock_path.open("a+", encoding="utf-8") as handle:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    return True
                finally:
                    try:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                    except OSError:
                        pass
        return False

    def _bare_path(self, record: Mapping[str, Any]) -> Path:
        return _managed_bare_path(self._managed_root, record)

    def _resolve_ref(self, bare_path: Path, ref_name: str) -> str | None:
        return _resolve_managed_ref(self._run_git, bare_path, ref_name)

    def _require_commit(self, bare_path: Path, object_id: str) -> None:
        _require_managed_commit(self._run_git, bare_path, object_id)

    def _is_ancestor(self, bare_path: Path, ancestor: str, descendant: str) -> bool:
        return (
            self._run_git(
                [
                    "git",
                    f"--git-dir={bare_path}",
                    "merge-base",
                    "--is-ancestor",
                    ancestor,
                    descendant,
                ],
                check=False,
            ).returncode
            == 0
        )

    def _list_refs(self, bare_path: Path, prefixes: tuple[str, ...]) -> dict[str, str]:
        result = self._run_git(
            [
                "git",
                f"--git-dir={bare_path}",
                "for-each-ref",
                "--format=%(refname) %(objectname)",
                *prefixes,
            ]
        )
        refs: dict[str, str] = {}
        for line in result.stdout.splitlines():
            ref, separator, object_id = line.partition(" ")
            if separator and ref and _HEX_OBJECT_ID.fullmatch(object_id):
                refs[ref] = object_id.lower()
        return refs

    def _run_update_ref_transaction(self, bare_path: Path, commands: list[str]) -> None:
        _run_managed_update_ref_transaction(self._run_git, bare_path, commands)

    def _delete_ref_cas(self, bare_path: Path, ref_name: str, object_id: str) -> None:
        _delete_managed_ref_cas(self._run_git, bare_path, ref_name, object_id)

    def _delete_namespace(self, bare_path: Path, prefix: str) -> None:
        refs = self._list_refs(bare_path, (prefix,)) if bare_path.exists() else {}
        if not refs:
            return
        self._run_update_ref_transaction(
            bare_path, [f"delete {ref} {oid}" for ref, oid in sorted(refs.items())]
        )

    def _ls_remote_branch(
        self,
        remote_url: str,
        ref_name: str,
        *,
        env: Mapping[str, str],
    ) -> str | None:
        result = self._run_git(["git", "ls-remote", "--refs", remote_url, ref_name], env=env)
        for line in result.stdout.splitlines():
            object_id, separator, advertised_ref = line.partition("\t")
            if separator and advertised_ref == ref_name and _HEX_OBJECT_ID.fullmatch(object_id):
                return object_id.lower()
        return None

    def _assert_secret_free_bare_config(self, bare_path: Path) -> None:
        result = self._run_git(["git", f"--git-dir={bare_path}", "config", "--local", "--list"])
        forbidden = ("credential.helper", "http.extraheader", "remote.")
        for line in result.stdout.splitlines():
            key = line.split("=", 1)[0].casefold()
            if key.startswith(forbidden):
                raise ManagedGitConflictError(
                    "Managed bare repository config contains persistent remote or credential configuration"
                )

    @staticmethod
    def _legacy_git_config(path: Path, key: str) -> str | None:
        result = subprocess.run(
            ["git", f"--git-dir={path}", "config", "--get", key],
            check=False,
            capture_output=True,
            text=True,
        )
        value = result.stdout.strip()
        return value or None if result.returncode == 0 else None

    def _is_evander_owned_rollback_path(self, path: Path) -> bool:
        allowed_roots = (
            (self.project_dir / "git-remotes").resolve(strict=False),
            (self.runtime_dir / "repo-cache").resolve(strict=False),
            (self.runtime_dir / "managed-git-rollback").resolve(strict=False),
        )
        return any(path != root and root in path.parents for root in allowed_roots)

    @contextmanager
    def _file_lock(self, path: Path, *, shared: bool = False):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_SH if shared else fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _repo_lock(self, managed_repo_id: str, *, exclusive: bool):
        return self._file_lock(
            self._lock_root / f"repo-{managed_repo_id}.lock", shared=not exclusive
        )

    def _target_lock(self, managed_repo_id: str, target: str):
        target_hash = hashlib.sha256(target.encode("utf-8")).hexdigest()[:20]
        return self._file_lock(self._lock_root / f"target-{managed_repo_id}-{target_hash}.lock")

    @staticmethod
    def _write_json_atomic(path: Path, payload: Mapping[str, Any], *, mode: int) -> None:
        _write_json_atomic(path, payload, mode=mode)

    @staticmethod
    def _require_git_repository(path: Path) -> None:
        result = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--git-dir"],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise ValueError(f"repo_source is not a Git repository: {path}")

    def _git_output(self, command: list[str], *, check: bool = True) -> str:
        return self._run_git(command, check=check).stdout

    def _run_git(
        self,
        command: list[str],
        *,
        env: Mapping[str, str] | None = None,
        input_text: str | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        timeout_seconds = float(
            getattr(
                self.config,
                "managed_git_operation_timeout_seconds",
                DEFAULT_GIT_OPERATION_TIMEOUT_SECONDS,
            )
        )
        try:
            result = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                input=input_text,
                env=dict(env) if env is not None else None,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            raise ManagedGitConflictError(
                f"Managed Git operation timed out after {timeout_seconds:g} seconds"
            ) from None
        if check and result.returncode != 0:
            self._raise_git_failure(result)
        return result

    @staticmethod
    def _raise_git_failure(result: subprocess.CompletedProcess[str]) -> None:
        message = (result.stderr or result.stdout or "Git operation failed").strip()
        raise RuntimeError(message)

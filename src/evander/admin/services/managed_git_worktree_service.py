from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import datetime
import fcntl
import json
import os
from pathlib import Path
import shutil
import threading
import time
from typing import Any, Protocol
from uuid import uuid4

from evander.admin.services.managed_git_common import (
    ManagedGitConflictError,
    ManagedGitNotFoundError,
    _HEX_OBJECT_ID,
    _MANIFEST_FILENAME,
    _RunGit,
    _SAFE_OPERATION_ID,
    _delete_ref_cas,
    _managed_bare_path,
    _normalize_object_id,
    _normalize_ref,
    _require_commit,
    _resolve_ref,
    _run_update_ref_transaction,
    _utc_now,
    _with_git_config_environment,
    _write_json_atomic,
)
from evander.signal_cleanup import (
    install_signal_cleanup_handlers,
    restore_signal_handlers,
)


class _RequireRecord(Protocol):
    def __call__(self, managed_repo_id: str) -> dict[str, Any]: ...


class _ReadRegistry(Protocol):
    def __call__(self) -> dict[str, Any]: ...


class _RequireMigrationPreflight(Protocol):
    def __call__(self) -> None: ...


class _RepoLock(Protocol):
    def __call__(self, managed_repo_id: str, *, exclusive: bool) -> AbstractContextManager[Any]: ...


@dataclass(frozen=True, slots=True)
class ManagedGitWorktreeDependencies:
    stale_worktree_seconds: float
    runtime_dir: Path
    managed_root: Path
    worktree_root: Path
    require_record: _RequireRecord
    require_migration_preflight: _RequireMigrationPreflight
    read_registry: _ReadRegistry
    repo_lock: _RepoLock
    run_git: _RunGit


class ManagedGitWorktreeService:
    """Owns temporary checkout creation, cleanup, and stale reconciliation."""

    def __init__(self, dependencies: ManagedGitWorktreeDependencies) -> None:
        self._deps = dependencies
        self._active_cleanups: dict[str, Callable[[], None]] = {}
        self._active_cleanups_lock = threading.RLock()

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
        record = self._deps.require_record(managed_repo_id)
        bare_path = self._bare_path(record)
        normalized_ref = _normalize_ref(source_ref, field_name="source_ref") if source_ref else None
        expected = _normalize_object_id(commit_sha, field_name="commit_sha") if commit_sha else None
        if normalized_ref is None and expected is None:
            raise ValueError("temporary_worktree requires source_ref or commit_sha")
        if normalized_ref is not None:
            current = self._resolve_ref(bare_path, normalized_ref)
            if current is None:
                raise ManagedGitNotFoundError(
                    f"Unknown source ref {normalized_ref} in {managed_repo_id}"
                )
            if expected is not None and current != expected:
                raise ManagedGitConflictError(
                    f"Source ref {normalized_ref} no longer equals {expected}"
                )
            expected = current
        assert expected is not None
        self._require_commit(bare_path, expected)
        op_id = str(operation_id or f"op-{uuid4().hex}").strip()
        if not _SAFE_OPERATION_ID.fullmatch(op_id):
            raise ValueError("operation_id contains unsupported characters")
        operation_ref = f"refs/evander/worktrees/{op_id}"
        repo_worktree_root = self._deps.worktree_root / managed_repo_id
        operation_dir = repo_worktree_root / op_id
        checkout_path = operation_dir / "checkout"
        lock_path = repo_worktree_root / f".{op_id}.lock"
        repo_worktree_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        repo_worktree_root.chmod(0o700)
        if operation_dir.exists():
            raise ManagedGitConflictError(f"Worktree operation already exists: {op_id}")

        with self._deps.repo_lock(managed_repo_id, exclusive=False):
            with lock_path.open("a+", encoding="utf-8") as lock_handle:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
                operation_dir.mkdir(mode=0o700)
                operation_dir.chmod(0o700)
                cleanup_done = False
                cleanup_lock = threading.Lock()
                cleanup_key = f"{managed_repo_id}:{op_id}"

                def cleanup() -> None:
                    nonlocal cleanup_done
                    with cleanup_lock:
                        if cleanup_done:
                            return
                        self._remove_worktree(
                            record,
                            operation_dir=operation_dir,
                            checkout_path=checkout_path,
                            operation_ref=operation_ref,
                            commit_sha=expected,
                        )
                        cleanup_done = True

                old_handlers: dict[int, Any] = {}
                self._register_active_cleanup(cleanup_key, cleanup)
                try:
                    transaction = [f"create {operation_ref} {expected}"]
                    if normalized_ref:
                        transaction.insert(0, f"verify {normalized_ref} {expected}")
                    self._run_update_ref_transaction(bare_path, transaction)
                    _write_json_atomic(
                        operation_dir / _MANIFEST_FILENAME,
                        {
                            "schema": "evander.managed-git-worktree/v1",
                            "managed_repo_id": managed_repo_id,
                            "operation_id": op_id,
                            "operation_ref": operation_ref,
                            "commit_sha": expected,
                            "source_ref": normalized_ref,
                            "purpose": str(purpose or "managed-git-operation")[:200],
                            "created_at": _utc_now(),
                        },
                        mode=0o600,
                    )
                    safe_env = self.safe_environment()
                    self._deps.run_git(
                        [
                            "git",
                            f"--git-dir={bare_path}",
                            "worktree",
                            "add",
                            "--detach",
                            "--no-checkout",
                            str(checkout_path),
                            operation_ref,
                        ],
                        env=safe_env,
                    )
                    self._deps.run_git(
                        [
                            "git",
                            "-C",
                            str(checkout_path),
                            "checkout",
                            "--detach",
                            expected,
                            "--",
                        ],
                        env=safe_env,
                    )
                    old_handlers = install_signal_cleanup_handlers(cleanup)
                    yield checkout_path
                except Exception as exc:
                    sanitized = self.sanitize_error(str(exc), operation_dir=operation_dir)
                    if sanitized != str(exc):
                        raise ManagedGitConflictError(sanitized) from None
                    raise
                finally:
                    restore_signal_handlers(old_handlers)
                    try:
                        cleanup()
                    finally:
                        self._unregister_active_cleanup(cleanup_key, cleanup)
                    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
            lock_path.unlink(missing_ok=True)

    def cleanup_active_worktrees(self) -> dict[str, int]:
        with self._active_cleanups_lock:
            cleanups = list(self._active_cleanups.values())
        cleaned = 0
        failed = 0
        for cleanup in cleanups:
            try:
                cleanup()
            except Exception:
                failed += 1
            else:
                cleaned += 1
        return {"cleaned": cleaned, "failed": failed}

    def reconcile_stale_worktrees(
        self,
        managed_repo_id: str | None = None,
        *,
        older_than_seconds: float | None = None,
    ) -> dict[str, Any]:
        self._deps.require_migration_preflight()
        threshold = (
            float(older_than_seconds)
            if older_than_seconds is not None
            else self._deps.stale_worktree_seconds
        )
        if threshold < 0:
            raise ValueError("older_than_seconds cannot be negative")
        records = self._deps.read_registry()["repositories"]
        repo_ids = [managed_repo_id] if managed_repo_id else sorted(records)
        removed: list[dict[str, str]] = []
        skipped_locked: list[dict[str, str]] = []
        skipped_recent: list[dict[str, str]] = []
        invalid: list[dict[str, str]] = []
        now = time.time()
        for repo_id in repo_ids:
            record = records.get(repo_id)
            if not isinstance(record, dict):
                if managed_repo_id:
                    raise ManagedGitNotFoundError(f"Unknown managed repository: {managed_repo_id}")
                continue
            repo_root = self._deps.worktree_root / repo_id
            if not repo_root.exists():
                continue
            for operation_dir in sorted(path for path in repo_root.iterdir() if path.is_dir()):
                manifest_path = operation_dir / _MANIFEST_FILENAME
                manifest: dict[str, Any] | None = None
                try:
                    loaded_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                    if (
                        not isinstance(loaded_manifest, dict)
                        or loaded_manifest.get("schema") != "evander.managed-git-worktree/v1"
                        or loaded_manifest.get("managed_repo_id") != repo_id
                        or loaded_manifest.get("operation_id") != operation_dir.name
                        or loaded_manifest.get("operation_ref")
                        != f"refs/evander/worktrees/{operation_dir.name}"
                        or not _HEX_OBJECT_ID.fullmatch(
                            str(loaded_manifest.get("commit_sha") or "")
                        )
                    ):
                        raise ValueError("manifest identity mismatch")
                    manifest = loaded_manifest
                    created = datetime.fromisoformat(str(manifest["created_at"]))
                    age = now - created.timestamp()
                except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
                    invalid.append(
                        {
                            "managed_repo_id": repo_id,
                            "operation_id": operation_dir.name,
                            "error": str(exc),
                        }
                    )
                    try:
                        age = now - operation_dir.stat().st_mtime
                    except OSError:
                        continue
                    if not _SAFE_OPERATION_ID.fullmatch(operation_dir.name):
                        continue
                if age < threshold:
                    skipped_recent.append(
                        {"managed_repo_id": repo_id, "operation_id": operation_dir.name}
                    )
                    continue
                lock_path = repo_root / f".{operation_dir.name}.lock"
                with lock_path.open("a+", encoding="utf-8") as lock_handle:
                    try:
                        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        skipped_locked.append(
                            {"managed_repo_id": repo_id, "operation_id": operation_dir.name}
                        )
                        continue
                    try:
                        with self._deps.repo_lock(repo_id, exclusive=False):
                            if manifest is None:
                                self._remove_unmanifested_worktree(
                                    record,
                                    operation_dir=operation_dir,
                                    operation_id=operation_dir.name,
                                )
                            else:
                                self._remove_worktree(
                                    record,
                                    operation_dir=operation_dir,
                                    checkout_path=operation_dir / "checkout",
                                    operation_ref=str(manifest["operation_ref"]),
                                    commit_sha=str(manifest["commit_sha"]),
                                )
                        removed.append(
                            {"managed_repo_id": repo_id, "operation_id": operation_dir.name}
                        )
                    finally:
                        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
                lock_path.unlink(missing_ok=True)
        return {
            "removed": removed,
            "skipped_locked": skipped_locked,
            "skipped_recent": skipped_recent,
            "invalid": invalid,
        }

    def has_registered_worktrees(self, record: Mapping[str, Any]) -> bool:
        repo_id = str(record["managed_repo_id"])
        repo_root = self._deps.worktree_root / repo_id
        if repo_root.exists() and any(path.is_dir() for path in repo_root.iterdir()):
            return True
        bare_path = self._bare_path(record)
        result = self._deps.run_git(
            ["git", f"--git-dir={bare_path}", "worktree", "list", "--porcelain"],
            check=False,
        )
        worktrees = [
            line.removeprefix("worktree ")
            for line in result.stdout.splitlines()
            if line.startswith("worktree ")
        ]
        return any(
            Path(path).resolve(strict=False) != bare_path.resolve(strict=False)
            for path in worktrees
        )

    def safe_environment(self) -> dict[str, str]:
        empty_hooks = self._deps.runtime_dir / "managed-git-empty-hooks"
        empty_hooks.mkdir(parents=True, exist_ok=True, mode=0o700)
        empty_hooks.chmod(0o700)
        env = os.environ.copy()
        env.pop("EVANDER_GIT_USERNAME", None)
        env.pop("EVANDER_GIT_PASSWORD", None)
        env.update(
            {
                "GIT_TERMINAL_PROMPT": "0",
                "GCM_INTERACTIVE": "never",
                "GIT_LFS_SKIP_SMUDGE": "1",
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": os.devnull,
            }
        )
        return _with_git_config_environment(
            env,
            {
                "credential.helper": "",
                "core.hooksPath": str(empty_hooks),
                "commit.gpgSign": "false",
                "tag.gpgSign": "false",
                "submodule.recurse": "false",
                "filter.lfs.smudge": "",
                "filter.lfs.required": "false",
            },
        )

    def sanitize_error(self, message: str, *, operation_dir: Path) -> str:
        sanitized = str(message or "Managed worktree operation failed")
        replacements = sorted(
            {
                str(self._deps.worktree_root),
                str(operation_dir),
                str(operation_dir / "checkout"),
            },
            key=len,
            reverse=True,
        )
        for path in replacements:
            sanitized = sanitized.replace(path, "<managed-worktree>")
        return sanitized

    def _remove_worktree(
        self,
        record: Mapping[str, Any],
        *,
        operation_dir: Path,
        checkout_path: Path,
        operation_ref: str,
        commit_sha: str,
    ) -> None:
        bare_path = self._bare_path(record)
        operation_id = operation_dir.name
        try:
            self._remove_checkout_and_registration(bare_path, checkout_path=checkout_path)
            self._delete_ref_cas(bare_path, operation_ref, commit_sha)
            if operation_dir.exists():
                shutil.rmtree(operation_dir)
        except Exception:
            raise ManagedGitConflictError(
                "Managed worktree cleanup failed; the operation remains available for "
                f"reconciliation ({record['managed_repo_id']}/{operation_id})"
            ) from None

    def _remove_unmanifested_worktree(
        self,
        record: Mapping[str, Any],
        *,
        operation_dir: Path,
        operation_id: str,
    ) -> None:
        bare_path = self._bare_path(record)
        checkout_path = operation_dir / "checkout"
        try:
            self._remove_checkout_and_registration(bare_path, checkout_path=checkout_path)
            operation_ref = f"refs/evander/worktrees/{operation_id}"
            commit_sha = self._resolve_ref(bare_path, operation_ref)
            if commit_sha is not None:
                self._delete_ref_cas(bare_path, operation_ref, commit_sha)
            if operation_dir.exists():
                shutil.rmtree(operation_dir)
        except BaseException:
            raise ManagedGitConflictError(
                "Managed worktree cleanup failed; the operation remains available for "
                f"reconciliation ({record['managed_repo_id']}/{operation_id})"
            ) from None

    def _remove_checkout_and_registration(
        self,
        bare_path: Path,
        *,
        checkout_path: Path,
    ) -> None:
        if checkout_path.exists():
            self._deps.run_git(
                [
                    "git",
                    f"--git-dir={bare_path}",
                    "worktree",
                    "remove",
                    "--force",
                    str(checkout_path),
                ],
                check=False,
            )
        if checkout_path.exists():
            shutil.rmtree(checkout_path)
        self._deps.run_git(
            ["git", f"--git-dir={bare_path}", "worktree", "prune", "--expire", "now"],
            check=False,
        )
        registered = self._deps.run_git(
            ["git", f"--git-dir={bare_path}", "worktree", "list", "--porcelain"],
            check=False,
        )
        checkout_real = checkout_path.resolve(strict=False)
        for line in registered.stdout.splitlines():
            if not line.startswith("worktree "):
                continue
            if Path(line.removeprefix("worktree ")).resolve(strict=False) == checkout_real:
                raise ManagedGitConflictError("managed worktree registration is still active")

    def _register_active_cleanup(self, key: str, cleanup: Callable[[], None]) -> None:
        with self._active_cleanups_lock:
            if key in self._active_cleanups:
                raise ManagedGitConflictError("Managed worktree cleanup is already registered")
            self._active_cleanups[key] = cleanup

    def _unregister_active_cleanup(self, key: str, cleanup: Callable[[], None]) -> None:
        with self._active_cleanups_lock:
            if self._active_cleanups.get(key) is cleanup:
                self._active_cleanups.pop(key, None)

    def _bare_path(self, record: Mapping[str, Any]) -> Path:
        return _managed_bare_path(self._deps.managed_root, record)

    def _resolve_ref(self, bare_path: Path, ref_name: str) -> str | None:
        return _resolve_ref(self._deps.run_git, bare_path, ref_name)

    def _require_commit(self, bare_path: Path, object_id: str) -> None:
        _require_commit(self._deps.run_git, bare_path, object_id)

    def _run_update_ref_transaction(self, bare_path: Path, commands: list[str]) -> None:
        _run_update_ref_transaction(self._deps.run_git, bare_path, commands)

    def _delete_ref_cas(self, bare_path: Path, ref_name: str, object_id: str) -> None:
        _delete_ref_cas(self._deps.run_git, bare_path, ref_name, object_id)

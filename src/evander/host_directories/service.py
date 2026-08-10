from __future__ import annotations

import base64
from bisect import bisect_right
from contextlib import contextmanager
import ctypes
from dataclasses import dataclass
from datetime import UTC, datetime
import errno
import gzip
from hashlib import sha256
import hmac
import io
import json
import mimetypes
import os
from pathlib import Path, PurePosixPath
import secrets
import stat
import tarfile
import time
from typing import Any, Callable, Iterator
from uuid import uuid4

from evander.host_directories.grants import validate_host_directory_grant_mapping
from evander.host_directories.models import (
    HostDirectoryDefinition,
    HostDirectoryDirectoryConfig,
)
from evander.host_directories.paths import (
    HOST_DIRECTORY_TRASH_NAME,
    host_directory_revision,
    normalize_relative_host_path,
)
from evander.models import SessionManifest


_DIRECTORY_OPEN_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_DIRECTORY", 0)
    | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_NOFOLLOW", 0)
)
_FILE_OPEN_FLAGS = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
_PATH_OPEN_FLAGS = (
    getattr(os, "O_PATH", os.O_RDONLY) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
)
_QUARANTINE_METADATA_NAME = "metadata.json"
_QUARANTINE_SCOPE_NAME = "scope.json"
_QUARANTINE_PAYLOAD_NAME = "payload"
_CURSOR_TTL_SECONDS = 15 * 60
_STALE_INTERNAL_QUARANTINE_SECONDS = 60 * 60
_RENAME_NOREPLACE = 1
_LIBC = ctypes.CDLL(None, use_errno=True)
_RENAMEAT2 = getattr(_LIBC, "renameat2", None)
if _RENAMEAT2 is not None:
    _RENAMEAT2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    _RENAMEAT2.restype = ctypes.c_int


class HostDirectoryError(RuntimeError):
    pass


class HostDirectoryPolicyError(HostDirectoryError):
    pass


def _rename_noreplace(
    *,
    source_directory_fd: int,
    source_name: str,
    destination_directory_fd: int,
    destination_name: str,
) -> None:
    if _RENAMEAT2 is None:
        raise HostDirectoryError("atomic no-replace restore is unavailable on this host")
    result = _RENAMEAT2(
        source_directory_fd,
        os.fsencode(source_name),
        destination_directory_fd,
        os.fsencode(destination_name),
        _RENAME_NOREPLACE,
    )
    if result == 0:
        return
    error_number = ctypes.get_errno()
    if error_number in {errno.EEXIST, errno.ENOTEMPTY}:
        raise HostDirectoryError("restore destination already exists")
    raise OSError(
        error_number,
        os.strerror(error_number),
        destination_name,
    )


def _same_file_state_after_rename(
    left: os.stat_result,
    right: os.stat_result,
) -> bool:
    # A rename updates ctime, so compare every other revision-bearing field plus
    # the complete mode. This catches name swaps, ordinary in-place writes,
    # permission changes, and directory child mutations across the move.
    return (
        left.st_dev == right.st_dev
        and left.st_ino == right.st_ino
        and left.st_mode == right.st_mode
        and left.st_size == right.st_size
        and left.st_mtime_ns == right.st_mtime_ns
    )


def _mount_id(file_descriptor: int) -> int:
    try:
        content = Path(f"/proc/self/fdinfo/{file_descriptor}").read_text(encoding="utf-8")
    except OSError as exc:
        raise HostDirectoryError(
            "host directory mount-boundary verification is unavailable"
        ) from exc
    for line in content.splitlines():
        key, separator, value = line.partition(":")
        if separator and key.strip() == "mnt_id":
            try:
                return int(value.strip())
            except ValueError:
                break
    raise HostDirectoryError("host directory mount-boundary verification is unavailable")


def _require_same_mount(
    reference_fd: int,
    candidate_fd: int,
    *,
    message: str = "host directory path crosses a filesystem boundary",
) -> None:
    if _mount_id(reference_fd) != _mount_id(candidate_fd):
        raise HostDirectoryError(message)


@dataclass(frozen=True)
class _ResolvedResource:
    directory_id: str
    definition: HostDirectoryDefinition


@dataclass(frozen=True)
class _Entry:
    relative_path: str
    name: str
    kind: str
    size: int | None
    modified_at: str
    revision: str

    def payload(self) -> dict[str, Any]:
        return {
            "path": self.relative_path,
            "name": self.name,
            "kind": self.kind,
            "size": self.size,
            "modified_at": self.modified_at,
            "revision": self.revision,
        }


@dataclass(frozen=True)
class _SnapshotEntry:
    relative_path: str
    kind: str
    mode: int
    size: int
    revision: str


class _ListCursorCodec:
    def __init__(self) -> None:
        self._secret = secrets.token_bytes(32)

    @staticmethod
    def _query_digest(*, directory_id: str, path: str, recursive: bool, limit: int) -> str:
        payload = json.dumps(
            {
                "directory_id": directory_id,
                "path": path,
                "recursive": recursive,
                "limit": limit,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return sha256(payload.encode("utf-8")).hexdigest()

    def issue(
        self,
        *,
        session_id: str,
        directory_id: str,
        path: str,
        recursive: bool,
        limit: int,
        after_path: str,
    ) -> str:
        payload = {
            "version": 1,
            "expires_at": int(time.time()) + _CURSOR_TTL_SECONDS,
            "session_id": session_id,
            "query_digest": self._query_digest(
                directory_id=directory_id,
                path=path,
                recursive=recursive,
                limit=limit,
            ),
            "after_path": after_path,
        }
        encoded = (
            base64.urlsafe_b64encode(
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
            )
            .decode("ascii")
            .rstrip("=")
        )
        signature = hmac.new(self._secret, encoded.encode("ascii"), sha256).hexdigest()
        return f"{encoded}.{signature}"

    def resolve(
        self,
        cursor: str,
        *,
        session_id: str,
        directory_id: str,
        path: str,
        recursive: bool,
        limit: int,
    ) -> str:
        try:
            encoded, signature = str(cursor).split(".", 1)
            expected = hmac.new(self._secret, encoded.encode("ascii"), sha256).hexdigest()
            if not hmac.compare_digest(signature, expected):
                raise ValueError("invalid host directory cursor")
            padding = "=" * (-len(encoded) % 4)
            payload = json.loads(base64.urlsafe_b64decode(f"{encoded}{padding}").decode("utf-8"))
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("invalid host directory cursor") from exc
        if (
            payload.get("version") != 1
            or payload.get("session_id") != session_id
            or int(payload.get("expires_at", 0)) < int(time.time())
            or payload.get("query_digest")
            != self._query_digest(
                directory_id=directory_id,
                path=path,
                recursive=recursive,
                limit=limit,
            )
        ):
            raise ValueError("invalid or expired host directory cursor")
        after_path = payload.get("after_path")
        if not isinstance(after_path, str) or not after_path:
            raise ValueError("invalid host directory cursor")
        return after_path


class HostDirectoryService:
    def __init__(
        self,
        *,
        runtime_dir: Path,
        provider_config: object,
        artifact_store_factory: Callable[[], Any],
    ) -> None:
        self.runtime_dir = Path(runtime_dir)
        self.provider_config = provider_config
        self.artifact_store_factory = artifact_store_factory
        self._cursor_codec = _ListCursorCodec()

    def config_for_directory(self, directory_path: str) -> HostDirectoryDirectoryConfig:
        resolver = getattr(self.provider_config, "resolve_host_directories_for_directory", None)
        if not callable(resolver):
            raise HostDirectoryError("host directory configuration is unavailable")
        return resolver(directory_path)

    def config_for_session(self, session: SessionManifest) -> HostDirectoryDirectoryConfig:
        return self.config_for_directory(session.directory_path)

    @staticmethod
    def validate_config(config: HostDirectoryDirectoryConfig) -> None:
        HostDirectoryDirectoryConfig.model_validate(config)

    def validate_grants(
        self,
        *,
        session_id: str,
        directory_path: str,
        grants: dict[str, list[str]],
    ) -> None:
        validate_host_directory_grant_mapping(
            self.config_for_directory(directory_path),
            grants,
            session_id=session_id,
        )

    def list_for_session(
        self,
        *,
        session: SessionManifest,
        directory_id: str,
        path: str = "",
        recursive: bool = False,
        limit: int = 100,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        resource = self.require_capability(session, directory_id, "list")
        if limit < 1 or limit > 1_000:
            raise ValueError("limit must be between 1 and 1000")
        parts = normalize_relative_host_path(path, allow_empty=True)
        normalized_path = PurePosixPath(*parts).as_posix() if parts else ""
        after_path = (
            self._cursor_codec.resolve(
                cursor,
                session_id=session.session_id,
                directory_id=resource.directory_id,
                path=normalized_path,
                recursive=bool(recursive),
                limit=limit,
            )
            if cursor
            else ""
        )
        with self._open_root(resource.definition) as (root_fd, root_dev):
            directory_fd = self._open_directory_parts(root_fd, parts, root_dev=root_dev)
            try:
                entries = self._list_entries(
                    directory_fd,
                    prefix=parts,
                    recursive=bool(recursive),
                    root_dev=root_dev,
                    max_entries=resource.definition.max_watch_entries,
                )
            finally:
                os.close(directory_fd)
        entries.sort(key=lambda entry: entry.relative_path)
        paths = [entry.relative_path for entry in entries]
        start = bisect_right(paths, after_path) if after_path else 0
        page = entries[start : start + limit]
        next_offset = start + len(page)
        next_cursor = (
            self._cursor_codec.issue(
                session_id=session.session_id,
                directory_id=resource.directory_id,
                path=normalized_path,
                recursive=bool(recursive),
                limit=limit,
                after_path=page[-1].relative_path,
            )
            if page and next_offset < len(entries)
            else None
        )
        return {
            "directory_id": resource.directory_id,
            "path": normalized_path,
            "recursive": bool(recursive),
            "entries": [entry.payload() for entry in page],
            "next_cursor": next_cursor,
        }

    def import_for_session(
        self,
        *,
        session: SessionManifest,
        directory_id: str,
        path: str,
    ) -> dict[str, Any]:
        resource = self.require_capability(session, directory_id, "import")
        parts = normalize_relative_host_path(path, allow_empty=False)
        relative_path = PurePosixPath(*parts).as_posix()
        with self._open_root(resource.definition) as (root_fd, root_dev):
            info = self._lstat_parts(root_fd, parts, root_dev=root_dev)
            if stat.S_ISREG(info.st_mode):
                content = self._read_regular_file(
                    root_fd,
                    parts,
                    root_dev=root_dev,
                    max_bytes=resource.definition.max_import_bytes,
                    expected_revision=host_directory_revision(info),
                )
                original_filename = parts[-1]
                kind = "file"
                entry_count = 1
            elif stat.S_ISDIR(info.st_mode):
                snapshot = self._snapshot_tree(
                    root_fd,
                    parts,
                    root_dev=root_dev,
                    max_entries=resource.definition.max_import_entries,
                    max_bytes=resource.definition.max_import_bytes,
                )
                content = self._archive_snapshot(
                    root_fd,
                    parts,
                    snapshot,
                    root_dev=root_dev,
                    max_bytes=resource.definition.max_import_bytes,
                )
                if snapshot != self._snapshot_tree(
                    root_fd,
                    parts,
                    root_dev=root_dev,
                    max_entries=resource.definition.max_import_entries,
                    max_bytes=resource.definition.max_import_bytes,
                ):
                    raise HostDirectoryError("host directory changed while it was being imported")
                original_filename = f"{parts[-1]}.tar.gz"
                kind = "directory"
                entry_count = len(snapshot)
                revision = snapshot[0].revision
            else:
                raise HostDirectoryError("host directory path is not a regular file or directory")
            if stat.S_ISREG(info.st_mode):
                revision = host_directory_revision(info)
        artifact = self.artifact_store_factory().stage(
            provider=f"host-directory:{resource.directory_id}",
            session_id=session.session_id,
            content=content,
            original_filename=original_filename,
            mime_type=(
                "application/gzip"
                if kind == "directory"
                else mimetypes.guess_type(original_filename)[0]
            ),
            metadata={
                "source": "host_directory",
                "directory_id": resource.directory_id,
                "relative_path": relative_path,
                "source_kind": kind,
                "source_revision": revision,
                "source_entry_count": entry_count,
            },
        )
        return {
            "directory_id": resource.directory_id,
            "path": relative_path,
            "kind": kind,
            "revision": revision,
            "entry_count": entry_count,
            "artifact": artifact.model_dump(mode="json"),
        }

    def delete_for_session(
        self,
        *,
        session: SessionManifest,
        directory_id: str,
        path: str,
        expected_revision: str,
        recursive: bool = False,
    ) -> dict[str, Any]:
        resource = self.require_capability(session, directory_id, "delete")
        parts = normalize_relative_host_path(path, allow_empty=False)
        relative_path = PurePosixPath(*parts).as_posix()
        expected = str(expected_revision or "").strip()
        if not expected:
            raise ValueError("expected_revision is required")
        recovery_id = f"trash_{uuid4().hex}"
        deleted_at = datetime.now(UTC)
        with self._open_root(resource.definition) as (root_fd, root_dev):
            info = self._lstat_parts(root_fd, parts, root_dev=root_dev)
            actual_revision = host_directory_revision(info)
            if not hmac.compare_digest(actual_revision, expected):
                raise HostDirectoryError("host directory entry changed after it was listed")
            if stat.S_ISDIR(info.st_mode):
                snapshot = self._snapshot_tree(
                    root_fd,
                    parts,
                    root_dev=root_dev,
                    max_entries=resource.definition.max_watch_entries,
                    max_bytes=None,
                )
                if len(snapshot) > 1 and not recursive:
                    raise HostDirectoryError(
                        "non-empty host directory deletion requires recursive=true"
                    )
                kind = "directory"
            elif stat.S_ISREG(info.st_mode):
                kind = "file"
            else:
                raise HostDirectoryError("host directory path is not a regular file or directory")
            trash_fd = self._ensure_trash_directory(root_fd, root_dev=root_dev)
            try:
                os.mkdir(recovery_id, mode=0o700, dir_fd=trash_fd)
                os.fsync(trash_fd)
                payload_moved = False
                try:
                    recovery_fd = os.open(
                        recovery_id,
                        _DIRECTORY_OPEN_FLAGS,
                        dir_fd=trash_fd,
                    )
                    try:
                        metadata = {
                            "version": 1,
                            "recovery_id": recovery_id,
                            "directory_id": resource.directory_id,
                            "directory_path": session.directory_path,
                            "original_path": relative_path,
                            "kind": kind,
                            "size": info.st_size if kind == "file" else None,
                            "revision": actual_revision,
                            "recursive": bool(recursive),
                            "session_id": session.session_id,
                            "deleted_at": deleted_at.isoformat(),
                        }
                        self._write_json_at(
                            recovery_fd,
                            _QUARANTINE_SCOPE_NAME,
                            {
                                "version": 1,
                                "recovery_id": recovery_id,
                                "directory_id": resource.directory_id,
                                "directory_path": session.directory_path,
                            },
                        )
                        self._write_json_at(
                            recovery_fd,
                            _QUARANTINE_METADATA_NAME,
                            metadata,
                        )
                        os.fsync(recovery_fd)
                        parent_fd = self._open_directory_parts(
                            root_fd, parts[:-1], root_dev=root_dev
                        )
                        try:
                            source_fd = -1
                            try:
                                source_fd = os.open(
                                    parts[-1],
                                    _PATH_OPEN_FLAGS,
                                    dir_fd=parent_fd,
                                )
                                _require_same_mount(root_fd, source_fd)
                                current = os.fstat(source_fd)
                                if not hmac.compare_digest(
                                    host_directory_revision(current),
                                    actual_revision,
                                ):
                                    raise HostDirectoryError(
                                        "host directory entry changed before quarantine"
                                    )
                                os.rename(
                                    parts[-1],
                                    _QUARANTINE_PAYLOAD_NAME,
                                    src_dir_fd=parent_fd,
                                    dst_dir_fd=recovery_fd,
                                )
                                payload_moved = True
                                os.fsync(parent_fd)
                                os.fsync(recovery_fd)
                                moved = os.stat(
                                    _QUARANTINE_PAYLOAD_NAME,
                                    dir_fd=recovery_fd,
                                    follow_symlinks=False,
                                )
                                if not _same_file_state_after_rename(current, moved):
                                    try:
                                        _rename_noreplace(
                                            source_directory_fd=recovery_fd,
                                            source_name=_QUARANTINE_PAYLOAD_NAME,
                                            destination_directory_fd=parent_fd,
                                            destination_name=parts[-1],
                                        )
                                        payload_moved = False
                                        os.fsync(parent_fd)
                                        os.fsync(recovery_fd)
                                    except Exception as rollback_error:
                                        raise HostDirectoryError(
                                            "host directory entry changed during quarantine; "
                                            f"{recovery_id} requires operator review"
                                        ) from rollback_error
                                    raise HostDirectoryError(
                                        "host directory entry changed during quarantine"
                                    )
                            except Exception:
                                if not payload_moved:
                                    self._unlink_quarantine_control_files(recovery_fd)
                                raise
                            finally:
                                if source_fd >= 0:
                                    os.close(source_fd)
                        finally:
                            os.close(parent_fd)
                    finally:
                        os.close(recovery_fd)
                except Exception:
                    if not payload_moved:
                        try:
                            cleanup_fd = os.open(
                                recovery_id,
                                _DIRECTORY_OPEN_FLAGS,
                                dir_fd=trash_fd,
                            )
                            try:
                                self._unlink_quarantine_control_files(cleanup_fd)
                            finally:
                                os.close(cleanup_fd)
                            os.rmdir(recovery_id, dir_fd=trash_fd)
                            os.fsync(trash_fd)
                        except OSError:
                            pass
                    raise
            finally:
                os.close(trash_fd)
        return {
            "status": "quarantined",
            "directory_id": resource.directory_id,
            "path": relative_path,
            "kind": kind,
            "revision": actual_revision,
            "recovery_id": recovery_id,
            "deleted_at": deleted_at.isoformat(),
        }

    def delete_requires_approval(self, session: SessionManifest, directory_id: str) -> bool:
        return self.require_capability(
            session, directory_id, "delete"
        ).definition.delete_requires_approval

    def require_capability(
        self,
        session: SessionManifest,
        directory_id: str,
        capability: str,
    ) -> _ResolvedResource:
        normalized_id = str(directory_id or "").strip().lower()
        granted = session.host_directory_grants.get(normalized_id, [])
        if capability not in granted:
            raise HostDirectoryPolicyError(
                f"host directory capability {normalized_id}.{capability} is not granted "
                f"to session {session.session_id}"
            )
        definition = self.config_for_session(session).host_directories.get(normalized_id)
        if definition is None or definition.disabled:
            raise HostDirectoryPolicyError(f"host directory {normalized_id!r} is unavailable")
        if capability not in definition.capabilities:
            raise HostDirectoryPolicyError(
                f"host directory capability {normalized_id}.{capability} is unavailable"
            )
        return _ResolvedResource(normalized_id, definition)

    def health(self, *, directory_path: str, directory_id: str) -> dict[str, Any]:
        normalized_id, definition = self._definition_for_directory(
            directory_path,
            directory_id,
            allow_disabled=True,
        )
        checks: dict[str, bool] = {
            "readable": False,
            "mount_boundary_verified": False,
            "quarantine_writable": "delete" not in definition.capabilities,
        }
        try:
            with self._open_root(definition) as (root_fd, root_device):
                checks["readable"] = True
                _mount_id(root_fd)
                checks["mount_boundary_verified"] = True
                if "delete" in definition.capabilities:
                    trash_fd = self._ensure_trash_directory(
                        root_fd,
                        root_dev=root_device,
                    )
                    probe_name = f".health-{uuid4().hex}"
                    try:
                        os.mkdir(probe_name, mode=0o700, dir_fd=trash_fd)
                        os.rmdir(probe_name, dir_fd=trash_fd)
                        checks["quarantine_writable"] = True
                    finally:
                        try:
                            try:
                                os.rmdir(probe_name, dir_fd=trash_fd)
                            except FileNotFoundError:
                                pass
                        finally:
                            os.close(trash_fd)
            return {
                "status": "ok",
                "available": True,
                "directory_id": normalized_id,
                "disabled": definition.disabled,
                "root_device": root_device,
                "checks": checks,
            }
        except (HostDirectoryError, OSError, ValueError) as exc:
            return {
                "status": "error",
                "available": False,
                "directory_id": normalized_id,
                "disabled": definition.disabled,
                "checks": checks,
                "error": str(exc),
                "error_class": type(exc).__name__,
            }

    def list_quarantine(self, *, directory_path: str, directory_id: str) -> list[dict[str, Any]]:
        normalized_id, definition = self._definition_for_directory(
            directory_path,
            directory_id,
            allow_disabled=True,
        )
        with self._open_root(definition) as (root_fd, root_dev):
            try:
                trash_fd = self._open_directory_parts(
                    root_fd, (HOST_DIRECTORY_TRASH_NAME,), root_dev=root_dev
                )
            except FileNotFoundError:
                return []
            records: list[dict[str, Any]] = []
            try:
                for entry in sorted(os.scandir(trash_fd), key=lambda item: item.name):
                    if not entry.name.startswith("trash_") or not entry.is_dir(
                        follow_symlinks=False
                    ):
                        continue
                    recovery_fd = os.open(entry.name, _DIRECTORY_OPEN_FLAGS, dir_fd=trash_fd)
                    remove_stale_empty = False
                    try:
                        opened = os.fstat(recovery_fd)
                        expected = entry.stat(follow_symlinks=False)
                        if (
                            opened.st_dev != expected.st_dev
                            or opened.st_ino != expected.st_ino
                            or not stat.S_ISDIR(opened.st_mode)
                        ):
                            raise HostDirectoryError(
                                "quarantine record changed while it was being listed"
                            )
                        _require_same_mount(
                            trash_fd,
                            recovery_fd,
                            message="quarantine record crosses a filesystem boundary",
                        )
                        scope = self._read_optional_json_at(
                            recovery_fd,
                            _QUARANTINE_SCOPE_NAME,
                        )
                        metadata = self._read_optional_json_at(
                            recovery_fd,
                            _QUARANTINE_METADATA_NAME,
                        )
                        owner = scope or metadata
                        if owner is None:
                            remove_stale_empty = self._reconcile_stale_unowned_quarantine(
                                recovery_fd
                            )
                        elif (
                            owner.get("directory_id") == normalized_id
                            and owner.get("directory_path") == directory_path
                        ):
                            metadata_valid = self._quarantine_metadata_is_valid(
                                metadata,
                                recovery_id=entry.name,
                                directory_id=normalized_id,
                                directory_path=directory_path,
                            )
                            payload_status = self._quarantine_payload_status(
                                recovery_fd,
                            )
                            status = payload_status if metadata_valid else "corrupt_metadata"
                            record = dict(metadata if metadata_valid else owner)
                            record.update(
                                {
                                    "recovery_id": entry.name,
                                    "recovery_status": status,
                                    "restore_available": status == "ready",
                                    "purge_available": True,
                                }
                            )
                            records.append(record)
                    finally:
                        os.close(recovery_fd)
                    if remove_stale_empty:
                        try:
                            os.rmdir(entry.name, dir_fd=trash_fd)
                        except OSError:
                            pass
                        else:
                            os.fsync(trash_fd)
            finally:
                os.close(trash_fd)
        return records

    def restore_quarantine(
        self,
        *,
        directory_path: str,
        directory_id: str,
        recovery_id: str,
    ) -> dict[str, Any]:
        normalized_id, definition = self._definition_for_directory(
            directory_path,
            directory_id,
            allow_disabled=True,
        )
        safe_recovery_id = self._normalize_recovery_id(recovery_id)
        with self._open_root(definition) as (root_fd, root_dev):
            trash_fd = self._open_directory_parts(
                root_fd, (HOST_DIRECTORY_TRASH_NAME,), root_dev=root_dev
            )
            try:
                recovery_fd = os.open(safe_recovery_id, _DIRECTORY_OPEN_FLAGS, dir_fd=trash_fd)
                try:
                    _require_same_mount(
                        trash_fd,
                        recovery_fd,
                        message="quarantine record crosses a filesystem boundary",
                    )
                    scope = self._read_optional_json_at(recovery_fd, _QUARANTINE_SCOPE_NAME)
                    metadata = self._read_optional_json_at(recovery_fd, _QUARANTINE_METADATA_NAME)
                    owner = scope or metadata
                    if (
                        owner is None
                        or owner.get("directory_id") != normalized_id
                        or owner.get("directory_path") != directory_path
                    ):
                        raise HostDirectoryError(
                            "quarantine record belongs to another directory scope"
                        )
                    if not self._quarantine_metadata_is_valid(
                        metadata,
                        recovery_id=safe_recovery_id,
                        directory_id=normalized_id,
                        directory_path=directory_path,
                    ):
                        raise HostDirectoryError(
                            "quarantine metadata is corrupt; the record can only be purged"
                        )
                    if self._quarantine_payload_status(recovery_fd) != "ready":
                        raise HostDirectoryError(
                            "quarantine payload is unavailable; the record can only be purged"
                        )
                    if metadata is None:
                        raise HostDirectoryError(
                            "quarantine metadata is corrupt; the record can only be purged"
                        )
                    parts = normalize_relative_host_path(
                        metadata.get("original_path"), allow_empty=False
                    )
                    parent_fd = self._open_directory_parts(root_fd, parts[:-1], root_dev=root_dev)
                    try:
                        _rename_noreplace(
                            source_directory_fd=recovery_fd,
                            source_name=_QUARANTINE_PAYLOAD_NAME,
                            destination_directory_fd=parent_fd,
                            destination_name=parts[-1],
                        )
                        os.fsync(parent_fd)
                        os.fsync(recovery_fd)
                    finally:
                        os.close(parent_fd)
                    self._unlink_quarantine_control_files(recovery_fd)
                    os.fsync(recovery_fd)
                finally:
                    os.close(recovery_fd)
                os.rmdir(safe_recovery_id, dir_fd=trash_fd)
                os.fsync(trash_fd)
            finally:
                os.close(trash_fd)
        return {
            "status": "restored",
            "directory_id": normalized_id,
            "recovery_id": safe_recovery_id,
            "path": str(metadata["original_path"]),
        }

    def purge_quarantine(
        self,
        *,
        directory_path: str,
        directory_id: str,
        recovery_id: str,
    ) -> dict[str, Any]:
        normalized_id, definition = self._definition_for_directory(
            directory_path,
            directory_id,
            allow_disabled=True,
        )
        safe_recovery_id = self._normalize_recovery_id(recovery_id)
        with self._open_root(definition) as (root_fd, root_dev):
            trash_fd = self._open_directory_parts(
                root_fd, (HOST_DIRECTORY_TRASH_NAME,), root_dev=root_dev
            )
            try:
                recovery_fd = os.open(safe_recovery_id, _DIRECTORY_OPEN_FLAGS, dir_fd=trash_fd)
                try:
                    _require_same_mount(
                        trash_fd,
                        recovery_fd,
                        message="quarantine record crosses a filesystem boundary",
                    )
                    scope = self._read_optional_json_at(recovery_fd, _QUARANTINE_SCOPE_NAME)
                    metadata = self._read_optional_json_at(recovery_fd, _QUARANTINE_METADATA_NAME)
                    owner = scope or metadata
                    if (
                        owner is None
                        or owner.get("directory_id") != normalized_id
                        or owner.get("directory_path") != directory_path
                    ):
                        raise HostDirectoryError(
                            "quarantine record belongs to another directory scope"
                        )
                    try:
                        self._remove_entry_at(
                            recovery_fd,
                            _QUARANTINE_PAYLOAD_NAME,
                            root_dev=root_dev,
                        )
                    except FileNotFoundError:
                        pass
                    self._unlink_quarantine_control_files(recovery_fd)
                    os.fsync(recovery_fd)
                finally:
                    os.close(recovery_fd)
                os.rmdir(safe_recovery_id, dir_fd=trash_fd)
                os.fsync(trash_fd)
            finally:
                os.close(trash_fd)
        return {
            "status": "purged",
            "directory_id": normalized_id,
            "recovery_id": safe_recovery_id,
            "path": str((metadata or {}).get("original_path") or ""),
        }

    def _definition_for_directory(
        self,
        directory_path: str,
        directory_id: str,
        *,
        allow_disabled: bool = False,
    ) -> tuple[str, HostDirectoryDefinition]:
        normalized = str(directory_id or "").strip().lower()
        definition = self.config_for_directory(directory_path).host_directories.get(normalized)
        if definition is None or (definition.disabled and not allow_disabled):
            raise HostDirectoryError(f"host directory {normalized!r} is unavailable")
        return normalized, definition

    @contextmanager
    def _open_root(self, definition: HostDirectoryDefinition) -> Iterator[tuple[int, int]]:
        root = definition.root
        try:
            info = root.lstat()
        except OSError as exc:
            raise HostDirectoryError("configured host directory root is unavailable") from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise HostDirectoryError(
                "configured host directory root must be a real directory, not a symlink"
            )
        try:
            fd = os.open(root, _DIRECTORY_OPEN_FLAGS)
        except OSError as exc:
            raise HostDirectoryError("configured host directory root is unavailable") from exc
        try:
            opened = os.fstat(fd)
            if opened.st_dev != info.st_dev or opened.st_ino != info.st_ino:
                raise HostDirectoryError("configured host directory root changed while opening")
            yield fd, opened.st_dev
        finally:
            os.close(fd)

    @staticmethod
    def _open_directory_parts(root_fd: int, parts: tuple[str, ...], *, root_dev: int) -> int:
        current_fd = os.dup(root_fd)
        try:
            for part in parts:
                child_fd = os.open(part, _DIRECTORY_OPEN_FLAGS, dir_fd=current_fd)
                child_info = os.fstat(child_fd)
                if child_info.st_dev != root_dev:
                    os.close(child_fd)
                    raise HostDirectoryError("host directory path crosses a filesystem boundary")
                try:
                    _require_same_mount(root_fd, child_fd)
                except Exception:
                    os.close(child_fd)
                    raise
                os.close(current_fd)
                current_fd = child_fd
            return current_fd
        except Exception:
            os.close(current_fd)
            raise

    def _lstat_parts(
        self, root_fd: int, parts: tuple[str, ...], *, root_dev: int
    ) -> os.stat_result:
        parent_fd = self._open_directory_parts(root_fd, parts[:-1], root_dev=root_dev)
        try:
            entry_fd = os.open(parts[-1], _PATH_OPEN_FLAGS, dir_fd=parent_fd)
            try:
                info = os.fstat(entry_fd)
                _require_same_mount(root_fd, entry_fd)
            finally:
                os.close(entry_fd)
        finally:
            os.close(parent_fd)
        if info.st_dev != root_dev:
            raise HostDirectoryError("host directory path crosses a filesystem boundary")
        if stat.S_ISLNK(info.st_mode):
            raise HostDirectoryError("host directory paths must not contain symlinks")
        return info

    def _list_entries(
        self,
        directory_fd: int,
        *,
        prefix: tuple[str, ...],
        recursive: bool,
        root_dev: int,
        max_entries: int,
    ) -> list[_Entry]:
        entries: list[_Entry] = []
        root_mount_id = _mount_id(directory_fd)

        def visit(fd: int, current_prefix: tuple[str, ...]) -> None:
            for child in sorted(os.scandir(fd), key=lambda item: item.name):
                if not current_prefix and child.name == HOST_DIRECTORY_TRASH_NAME:
                    continue
                entry_fd = os.open(child.name, _PATH_OPEN_FLAGS, dir_fd=fd)
                try:
                    info = os.fstat(entry_fd)
                    if info.st_dev != root_dev or _mount_id(entry_fd) != root_mount_id:
                        raise HostDirectoryError(
                            "host directory listing crosses a filesystem boundary"
                        )
                finally:
                    os.close(entry_fd)
                relative_parts = (*current_prefix, child.name)
                relative_path = PurePosixPath(*relative_parts).as_posix()
                if stat.S_ISREG(info.st_mode):
                    kind = "file"
                    size: int | None = info.st_size
                elif stat.S_ISDIR(info.st_mode):
                    kind = "directory"
                    size = None
                else:
                    continue
                entries.append(
                    _Entry(
                        relative_path=relative_path,
                        name=child.name,
                        kind=kind,
                        size=size,
                        modified_at=datetime.fromtimestamp(info.st_mtime, tz=UTC).isoformat(),
                        revision=host_directory_revision(info),
                    )
                )
                if len(entries) > max_entries:
                    raise HostDirectoryError(
                        f"host directory listing exceeded {max_entries} entries"
                    )
                if recursive and kind == "directory":
                    child_fd = os.open(child.name, _DIRECTORY_OPEN_FLAGS, dir_fd=fd)
                    try:
                        opened = os.fstat(child_fd)
                        if (
                            not _same_file_state_after_rename(info, opened)
                            or _mount_id(child_fd) != root_mount_id
                        ):
                            raise HostDirectoryError(
                                "host directory changed while it was being listed"
                            )
                        visit(child_fd, relative_parts)
                    finally:
                        os.close(child_fd)

        visit(directory_fd, prefix)
        return entries

    def _snapshot_tree(
        self,
        root_fd: int,
        parts: tuple[str, ...],
        *,
        root_dev: int,
        max_entries: int,
        max_bytes: int | None,
    ) -> tuple[_SnapshotEntry, ...]:
        root_info = self._lstat_parts(root_fd, parts, root_dev=root_dev)
        if not stat.S_ISDIR(root_info.st_mode):
            raise HostDirectoryError("host directory path is not a directory")
        result: list[_SnapshotEntry] = [
            _SnapshotEntry(
                relative_path="",
                kind="directory",
                mode=root_info.st_mode,
                size=0,
                revision=host_directory_revision(root_info),
            )
        ]
        total_bytes = 0
        directory_fd = self._open_directory_parts(root_fd, parts, root_dev=root_dev)
        opened_root = os.fstat(directory_fd)
        if not _same_file_state_after_rename(root_info, opened_root):
            os.close(directory_fd)
            raise HostDirectoryError("host directory changed while it was being inspected")
        root_mount_id = _mount_id(directory_fd)

        def visit(fd: int, relative_prefix: tuple[str, ...]) -> None:
            nonlocal total_bytes
            for child in sorted(os.scandir(fd), key=lambda item: item.name):
                entry_fd = os.open(child.name, _PATH_OPEN_FLAGS, dir_fd=fd)
                try:
                    info = os.fstat(entry_fd)
                    if info.st_dev != root_dev or _mount_id(entry_fd) != root_mount_id:
                        raise HostDirectoryError(
                            "host directory tree crosses a filesystem boundary"
                        )
                finally:
                    os.close(entry_fd)
                relative_parts = (*relative_prefix, child.name)
                relative_path = PurePosixPath(*relative_parts).as_posix()
                if stat.S_ISDIR(info.st_mode):
                    result.append(
                        _SnapshotEntry(
                            relative_path=relative_path,
                            kind="directory",
                            mode=info.st_mode,
                            size=0,
                            revision=host_directory_revision(info),
                        )
                    )
                    child_fd = os.open(child.name, _DIRECTORY_OPEN_FLAGS, dir_fd=fd)
                    try:
                        opened = os.fstat(child_fd)
                        if (
                            not _same_file_state_after_rename(info, opened)
                            or _mount_id(child_fd) != root_mount_id
                        ):
                            raise HostDirectoryError(
                                "host directory changed while it was being inspected"
                            )
                        visit(child_fd, relative_parts)
                    finally:
                        os.close(child_fd)
                elif stat.S_ISREG(info.st_mode):
                    total_bytes += info.st_size
                    result.append(
                        _SnapshotEntry(
                            relative_path=relative_path,
                            kind="file",
                            mode=info.st_mode,
                            size=info.st_size,
                            revision=host_directory_revision(info),
                        )
                    )
                else:
                    raise HostDirectoryError(
                        "host directory tree contains a symlink or unsupported entry"
                    )
                if len(result) > max_entries:
                    raise HostDirectoryError(f"host directory tree exceeded {max_entries} entries")
                if max_bytes is not None and total_bytes > max_bytes:
                    raise HostDirectoryError(f"host directory import exceeded {max_bytes} bytes")

        try:
            visit(directory_fd, ())
        finally:
            os.close(directory_fd)
        return tuple(result)

    def _archive_snapshot(
        self,
        root_fd: int,
        parts: tuple[str, ...],
        snapshot: tuple[_SnapshotEntry, ...],
        *,
        root_dev: int,
        max_bytes: int,
    ) -> bytes:
        buffer = io.BytesIO()
        top_name = parts[-1]
        with gzip.GzipFile(fileobj=buffer, mode="wb", mtime=0) as gzip_file:
            with tarfile.open(fileobj=gzip_file, mode="w", format=tarfile.PAX_FORMAT) as archive:
                for entry in snapshot:
                    archive_name = (
                        top_name if not entry.relative_path else f"{top_name}/{entry.relative_path}"
                    )
                    tar_info = tarfile.TarInfo(archive_name)
                    tar_info.uid = 0
                    tar_info.gid = 0
                    tar_info.uname = ""
                    tar_info.gname = ""
                    tar_info.mtime = 0
                    if entry.kind == "directory":
                        tar_info.type = tarfile.DIRTYPE
                        tar_info.mode = 0o755
                        archive.addfile(tar_info)
                        continue
                    file_parts = (*parts, *PurePosixPath(entry.relative_path).parts)
                    content = self._read_regular_file(
                        root_fd,
                        file_parts,
                        root_dev=root_dev,
                        max_bytes=max_bytes,
                        expected_revision=entry.revision,
                    )
                    tar_info.size = len(content)
                    tar_info.mode = 0o644
                    archive.addfile(tar_info, io.BytesIO(content))
        return buffer.getvalue()

    def _read_regular_file(
        self,
        root_fd: int,
        parts: tuple[str, ...],
        *,
        root_dev: int,
        max_bytes: int,
        expected_revision: str,
    ) -> bytes:
        parent_fd = self._open_directory_parts(root_fd, parts[:-1], root_dev=root_dev)
        try:
            fd = os.open(parts[-1], _FILE_OPEN_FLAGS, dir_fd=parent_fd)
        finally:
            os.close(parent_fd)
        try:
            before = os.fstat(fd)
            if (
                before.st_dev != root_dev
                or _mount_id(fd) != _mount_id(root_fd)
                or not stat.S_ISREG(before.st_mode)
                or host_directory_revision(before) != expected_revision
            ):
                raise HostDirectoryError("host directory file changed while it was being read")
            if before.st_size > max_bytes:
                raise HostDirectoryError(f"host directory import exceeded {max_bytes} bytes")
            with os.fdopen(os.dup(fd), "rb") as handle:
                content = handle.read(max_bytes + 1)
            if len(content) > max_bytes:
                raise HostDirectoryError(f"host directory import exceeded {max_bytes} bytes")
            after = os.fstat(fd)
            if host_directory_revision(after) != expected_revision:
                raise HostDirectoryError("host directory file changed while it was being read")
            return content
        finally:
            os.close(fd)

    @staticmethod
    def _ensure_trash_directory(root_fd: int, *, root_dev: int) -> int:
        created = False
        try:
            os.mkdir(HOST_DIRECTORY_TRASH_NAME, mode=0o700, dir_fd=root_fd)
            created = True
        except FileExistsError:
            pass
        if created:
            os.fsync(root_fd)
        trash_fd = os.open(HOST_DIRECTORY_TRASH_NAME, _DIRECTORY_OPEN_FLAGS, dir_fd=root_fd)
        info = os.fstat(trash_fd)
        if (
            info.st_dev != root_dev
            or not stat.S_ISDIR(info.st_mode)
            or _mount_id(trash_fd) != _mount_id(root_fd)
        ):
            os.close(trash_fd)
            raise HostDirectoryError("host directory quarantine is unsafe")
        return trash_fd

    @staticmethod
    def _write_json_at(directory_fd: int, name: str, payload: dict[str, Any]) -> None:
        flags = (
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        fd = os.open(name, flags, 0o600, dir_fd=directory_fd)
        try:
            encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
            remaining = memoryview(encoded)
            while remaining:
                written = os.write(fd, remaining)
                if written <= 0:
                    raise OSError("unable to write quarantine metadata")
                remaining = remaining[written:]
            os.fsync(fd)
        finally:
            os.close(fd)

    @staticmethod
    def _read_json_at(directory_fd: int, name: str) -> dict[str, Any]:
        fd = os.open(name, _FILE_OPEN_FLAGS, dir_fd=directory_fd)
        try:
            with os.fdopen(os.dup(fd), "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        finally:
            os.close(fd)
        if not isinstance(payload, dict):
            raise ValueError("quarantine metadata must be an object")
        return payload

    @classmethod
    def _read_optional_json_at(
        cls,
        directory_fd: int,
        name: str,
    ) -> dict[str, Any] | None:
        try:
            return cls._read_json_at(directory_fd, name)
        except (OSError, UnicodeDecodeError, ValueError):
            return None

    @staticmethod
    def _quarantine_metadata_is_valid(
        metadata: dict[str, Any] | None,
        *,
        recovery_id: str,
        directory_id: str,
        directory_path: str,
    ) -> bool:
        if (
            metadata is None
            or metadata.get("recovery_id") != recovery_id
            or metadata.get("directory_id") != directory_id
            or metadata.get("directory_path") != directory_path
            or metadata.get("kind") not in {"file", "directory"}
        ):
            return False
        try:
            normalize_relative_host_path(
                metadata.get("original_path"),
                allow_empty=False,
            )
        except (TypeError, ValueError):
            return False
        return True

    @staticmethod
    def _quarantine_payload_status(recovery_fd: int) -> str:
        try:
            payload_fd = os.open(
                _QUARANTINE_PAYLOAD_NAME,
                _PATH_OPEN_FLAGS,
                dir_fd=recovery_fd,
            )
        except FileNotFoundError:
            return "missing_payload"
        except OSError:
            return "unsafe_payload"
        try:
            info = os.fstat(payload_fd)
            try:
                _require_same_mount(
                    recovery_fd,
                    payload_fd,
                    message="quarantine payload crosses a filesystem boundary",
                )
            except HostDirectoryError:
                return "unsafe_payload"
            if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                return "unsafe_payload"
            return "ready"
        finally:
            os.close(payload_fd)

    @staticmethod
    def _reconcile_stale_unowned_quarantine(recovery_fd: int) -> bool:
        info = os.fstat(recovery_fd)
        age_seconds = time.time() - info.st_mtime
        if age_seconds < _STALE_INTERNAL_QUARANTINE_SECONDS:
            return False
        with os.scandir(recovery_fd) as entries:
            names = {entry.name for entry in entries}
        control_names = {
            _QUARANTINE_METADATA_NAME,
            _QUARANTINE_SCOPE_NAME,
        }
        if not names.issubset(control_names):
            return False
        for name in names:
            os.unlink(name, dir_fd=recovery_fd)
        os.fsync(recovery_fd)
        return True

    @staticmethod
    def _unlink_quarantine_control_files(recovery_fd: int) -> None:
        for name in (_QUARANTINE_METADATA_NAME, _QUARANTINE_SCOPE_NAME):
            try:
                os.unlink(name, dir_fd=recovery_fd)
            except FileNotFoundError:
                pass

    def _remove_entry_at(self, parent_fd: int, name: str, *, root_dev: int) -> None:
        entry_fd = os.open(name, _PATH_OPEN_FLAGS, dir_fd=parent_fd)
        try:
            info = os.fstat(entry_fd)
            if info.st_dev != root_dev or _mount_id(entry_fd) != _mount_id(parent_fd):
                raise HostDirectoryError("quarantine entry crosses a filesystem boundary")
        finally:
            os.close(entry_fd)
        if stat.S_ISDIR(info.st_mode):
            child_fd = os.open(name, _DIRECTORY_OPEN_FLAGS, dir_fd=parent_fd)
            try:
                opened = os.fstat(child_fd)
                if not _same_file_state_after_rename(info, opened) or _mount_id(
                    child_fd
                ) != _mount_id(parent_fd):
                    raise HostDirectoryError("quarantine entry changed during purge")
                for child in list(os.scandir(child_fd)):
                    self._remove_entry_at(child_fd, child.name, root_dev=root_dev)
            finally:
                os.close(child_fd)
            os.rmdir(name, dir_fd=parent_fd)
            return
        os.unlink(name, dir_fd=parent_fd)

    @staticmethod
    def _normalize_recovery_id(value: object) -> str:
        normalized = str(value or "").strip()
        if (
            not normalized.startswith("trash_")
            or len(normalized) != len("trash_") + 32
            or any(character not in "0123456789abcdef" for character in normalized[6:])
        ):
            raise ValueError("invalid host directory recovery id")
        return normalized

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import stat
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

from evander.events.models import TriggerEvent
from evander.events.provider_models import HostDirectoryFileAddedTriggerDefinition
from evander.host_directories.models import HostDirectoryDefinition
from evander.host_directories.paths import (
    HOST_DIRECTORY_TRASH_NAME,
    host_directory_revision,
)
from evander.triggers.providers._cursor_state import (
    TriggerCursorStore,
    merge_cursor_ids,
)
from evander.triggers.providers.base import TriggerProviderBase


_CURSOR_FILENAME = "trigger-cursors-host-directories.json"
_STATE_VERSION = 1
_ACKNOWLEDGED_EVENT_LIMIT = 2_048
_PATH_OPEN_FLAGS = (
    getattr(os, "O_PATH", os.O_RDONLY) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
)


class HostDirectoryScanError(RuntimeError):
    """The watched directory could not be scanned without crossing a safety boundary."""


class HostDirectoryFileAddedTriggerProvider(TriggerProviderBase):
    def initialize(self, trigger_definition: Any, runtime_context: Any) -> None:
        super().initialize(trigger_definition, runtime_context)
        self._trigger()
        self._resource()

    def poll(self) -> list[TriggerEvent]:
        trigger = self._trigger()
        resource = self._resource()
        scanned = _scan_regular_files(
            root=resource.root,
            relative_directory=trigger.path,
            recursive=trigger.recursive,
            include_globs=trigger.include_globs,
            exclude_globs=trigger.exclude_globs,
            limit=resource.max_watch_entries,
        )
        now = _utc_now()
        configuration_signature = self._configuration_signature(resource)
        state = self._cursor_store().update(
            trigger.id,
            lambda current: _advance_scan_state(
                current,
                scanned=scanned,
                configuration_signature=configuration_signature,
                now=now,
                settle_seconds=trigger.settle_seconds,
                trigger_id=trigger.id,
            ),
        )
        return _pending_events(trigger, state)

    def acknowledge_published_event(self, event: TriggerEvent) -> None:
        trigger = self._trigger()
        if event.trigger_id != trigger.id or event.trigger_type != trigger.type:
            return
        self._cursor_store().update(
            trigger.id,
            lambda state: _acknowledge_pending_event(state, event.event_id),
        )

    def _trigger(self) -> HostDirectoryFileAddedTriggerDefinition:
        if not isinstance(
            self.trigger_definition,
            HostDirectoryFileAddedTriggerDefinition,
        ):
            raise RuntimeError(
                "HostDirectoryFileAddedTriggerProvider initialized with the wrong definition"
            )
        return self.trigger_definition

    def _resource(self) -> HostDirectoryDefinition:
        if self.runtime_context is None:
            raise RuntimeError("HostDirectoryFileAddedTriggerProvider has no runtime context")
        resolver = self.runtime_context.provider_config_resolver
        resolve = getattr(resolver, "resolve_host_directories_for_directory", None)
        if not callable(resolve):
            raise RuntimeError("Host directory configuration is unavailable")
        directory_scope = (
            str(self.runtime_context.metadata.get("directory_path") or "root").strip() or "root"
        )
        configured = resolve(directory_scope)
        resource = configured.host_directories.get(self._trigger().directory_id)
        if resource is None:
            raise ValueError(
                f"Unknown host directory {self._trigger().directory_id!r} "
                f"for directory scope {directory_scope!r}"
            )
        if resource.disabled:
            raise ValueError(f"Host directory {self._trigger().directory_id!r} is disabled")
        return resource

    def _configuration_signature(self, resource: HostDirectoryDefinition) -> str:
        trigger = self._trigger()
        directory_scope = (
            str(self.runtime_context.metadata.get("directory_path") or "root").strip() or "root"
        )
        payload = {
            "directory_id": trigger.directory_id,
            "directory_scope": directory_scope,
            "exclude_globs": trigger.exclude_globs,
            "include_globs": trigger.include_globs,
            "path": trigger.path,
            "recursive": trigger.recursive,
            "root": str(resource.root),
        }
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def _cursor_store(self) -> TriggerCursorStore:
        if self.runtime_context is None:
            raise RuntimeError("HostDirectoryFileAddedTriggerProvider has no runtime context")
        return TriggerCursorStore(
            Path(self.runtime_context.resolved_config.runtime_dir) / _CURSOR_FILENAME
        )


def _advance_scan_state(
    state: dict[str, Any],
    *,
    scanned: dict[str, dict[str, Any]],
    configuration_signature: str,
    now: datetime,
    settle_seconds: float,
    trigger_id: str,
) -> dict[str, Any]:
    now_text = _format_datetime(now)
    if (
        state.get("version") != _STATE_VERSION
        or state.get("configuration_signature") != configuration_signature
        or state.get("initialized") is not True
    ):
        return {
            "version": _STATE_VERSION,
            "initialized": True,
            "configuration_signature": configuration_signature,
            "baseline_at": now_text,
            "last_scan_at": now_text,
            "known_files": dict(scanned),
            "candidates": {},
            "next_generation": 1,
            "acknowledged_event_ids": [],
        }

    known_files = _mapping_of_mappings(state.get("known_files"))
    candidates = _mapping_of_mappings(state.get("candidates"))
    next_generation = _coerce_int(state.get("next_generation"), default=0)
    if next_generation < 1:
        legacy_generations = _mapping_of_positive_ints(state.get("generations"))
        next_generation = max(legacy_generations.values(), default=0) + 1
    unclaimed = dict(scanned)

    for relative_path in list(known_files):
        current_snapshot = unclaimed.get(relative_path)
        if current_snapshot is None:
            known_files.pop(relative_path, None)
            continue
        # Changes and atomic replacements at an already-known path are updates, not
        # file-added events. A re-add is eligible only after a scan observes absence.
        known_files[relative_path] = current_snapshot
        unclaimed.pop(relative_path, None)

    for relative_path, candidate in list(candidates.items()):
        if _pending_candidate(candidate):
            # Once detected, an event is immutable until durable publication is acknowledged.
            unclaimed.pop(relative_path, None)
            continue

        current_snapshot = unclaimed.pop(relative_path, None)
        if current_snapshot is None:
            candidates.pop(relative_path, None)
            continue

        candidate_snapshot = candidate.get("snapshot")
        if not isinstance(candidate_snapshot, dict):
            candidates.pop(relative_path, None)
            unclaimed[relative_path] = current_snapshot
            continue

        if not _same_identity(candidate_snapshot, current_snapshot):
            generation = next_generation
            next_generation += 1
            candidates[relative_path] = _new_candidate(
                snapshot=current_snapshot,
                generation=generation,
                observed_at=now_text,
            )
            continue

        if not _same_stable_snapshot(candidate_snapshot, current_snapshot):
            candidate["snapshot"] = current_snapshot
            candidate["first_observed_at"] = now_text
            candidate["last_observed_at"] = now_text
            candidate["observations"] = 1
            candidate.pop("detected_at", None)
            candidate.pop("event_id", None)
            continue

        candidate["snapshot"] = current_snapshot
        candidate["last_observed_at"] = now_text
        candidate["observations"] = (
            max(
                1,
                _coerce_int(candidate.get("observations"), default=1),
            )
            + 1
        )
        first_observed_at = _parse_datetime(candidate.get("first_observed_at"))
        if (
            first_observed_at is not None
            and candidate["observations"] >= 2
            and (now - first_observed_at).total_seconds() >= settle_seconds
        ):
            candidate["detected_at"] = now_text
            candidate["event_id"] = _event_id(
                trigger_id=trigger_id,
                relative_path=relative_path,
                generation=_coerce_int(candidate.get("generation"), default=1),
                revision=str(current_snapshot.get("revision") or ""),
            )

    for relative_path, snapshot in unclaimed.items():
        generation = next_generation
        next_generation += 1
        candidates[relative_path] = _new_candidate(
            snapshot=snapshot,
            generation=generation,
            observed_at=now_text,
        )

    return {
        "version": _STATE_VERSION,
        "initialized": True,
        "configuration_signature": configuration_signature,
        "baseline_at": state.get("baseline_at") or now_text,
        "last_scan_at": now_text,
        "known_files": known_files,
        "candidates": candidates,
        "next_generation": next_generation,
        "acknowledged_event_ids": merge_cursor_ids(
            state.get("acknowledged_event_ids"),
            [],
            limit=_ACKNOWLEDGED_EVENT_LIMIT,
        ),
    }


def _acknowledge_pending_event(
    state: dict[str, Any],
    event_id: str,
) -> dict[str, Any]:
    if state.get("initialized") is not True:
        return state
    candidates = _mapping_of_mappings(state.get("candidates"))
    known_files = _mapping_of_mappings(state.get("known_files"))
    acknowledged = False
    for relative_path, candidate in list(candidates.items()):
        if candidate.get("event_id") != event_id or not _pending_candidate(candidate):
            continue
        snapshot = candidate.get("snapshot")
        if isinstance(snapshot, dict):
            known_files[relative_path] = dict(snapshot)
        candidates.pop(relative_path, None)
        acknowledged = True
        break
    if not acknowledged:
        return state
    state["candidates"] = candidates
    state["known_files"] = known_files
    state["acknowledged_event_ids"] = merge_cursor_ids(
        state.get("acknowledged_event_ids"),
        [event_id],
        limit=_ACKNOWLEDGED_EVENT_LIMIT,
    )
    return state


def _pending_events(
    trigger: HostDirectoryFileAddedTriggerDefinition,
    state: dict[str, Any],
) -> list[TriggerEvent]:
    pending: list[tuple[datetime, str, TriggerEvent]] = []
    for relative_path, candidate in _mapping_of_mappings(state.get("candidates")).items():
        if not _pending_candidate(candidate):
            continue
        snapshot = candidate.get("snapshot")
        detected_at = _parse_datetime(candidate.get("detected_at"))
        event_id = str(candidate.get("event_id") or "").strip()
        if not isinstance(snapshot, dict) or detected_at is None or not event_id:
            continue
        pending.append(
            (
                detected_at,
                relative_path,
                TriggerEvent(
                    event_id=event_id,
                    trigger_id=trigger.id,
                    trigger_type=trigger.type,
                    occurred_at=detected_at,
                    payload={
                        "directory_id": trigger.directory_id,
                        "relative_path": relative_path,
                        "size": _coerce_int(snapshot.get("size"), default=0),
                        "modified_at": str(snapshot.get("modified_at") or ""),
                        "revision": str(snapshot.get("revision") or ""),
                        "detected_at": _format_datetime(detected_at),
                    },
                ),
            )
        )
    pending.sort(key=lambda item: (item[0], item[1], item[2].event_id))
    return [item[2] for item in pending]


def _new_candidate(
    *,
    snapshot: dict[str, Any],
    generation: int,
    observed_at: str,
) -> dict[str, Any]:
    return {
        "snapshot": snapshot,
        "generation": generation,
        "first_observed_at": observed_at,
        "last_observed_at": observed_at,
        "observations": 1,
    }


def _pending_candidate(candidate: dict[str, Any]) -> bool:
    return bool(
        str(candidate.get("event_id") or "").strip()
        and _parse_datetime(candidate.get("detected_at")) is not None
    )


def _event_id(
    *,
    trigger_id: str,
    relative_path: str,
    generation: int,
    revision: str,
) -> str:
    source = json.dumps(
        {
            "generation": generation,
            "path": relative_path,
            "revision": revision,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"{trigger_id}:{hashlib.sha256(source).hexdigest()}"


def _scan_regular_files(
    *,
    root: Path,
    relative_directory: str,
    recursive: bool,
    include_globs: list[str],
    exclude_globs: list[str],
    limit: int,
) -> dict[str, dict[str, Any]]:
    watch_parts = tuple(PurePosixPath(relative_directory).parts) if relative_directory else ()
    scanned: dict[str, dict[str, Any]] = {}
    seen_entries = 0
    with _open_watch_directory(root, watch_parts) as (watch_fd, root_device):
        root_mount_id = _mount_id(watch_fd)

        def visit(directory_fd: int, parent_parts: tuple[str, ...]) -> None:
            nonlocal seen_entries
            try:
                with os.scandir(directory_fd) as iterator:
                    entries = sorted(iterator, key=lambda entry: entry.name)
            except OSError as exc:
                raise HostDirectoryScanError("Unable to enumerate host directory") from exc
            for entry in entries:
                if not parent_parts and entry.name == HOST_DIRECTORY_TRASH_NAME:
                    continue
                seen_entries += 1
                if seen_entries > limit:
                    raise HostDirectoryScanError(
                        f"Host directory scan exceeds its {limit}-entry limit"
                    )
                try:
                    entry_fd = os.open(
                        entry.name,
                        _PATH_OPEN_FLAGS,
                        dir_fd=directory_fd,
                    )
                except OSError as exc:
                    raise HostDirectoryScanError("Host directory changed during scan") from exc
                try:
                    info = os.fstat(entry_fd)
                    _ensure_same_mount(
                        entry_fd,
                        root_mount_id=root_mount_id,
                        entry_kind="entry",
                    )
                finally:
                    os.close(entry_fd)
                mode = info.st_mode
                if stat.S_ISLNK(mode):
                    continue
                relative_parts = (*parent_parts, entry.name)
                if stat.S_ISREG(mode):
                    _ensure_same_device(
                        info,
                        root_device=root_device,
                        entry_kind="file",
                    )
                    relative_path = PurePosixPath(*relative_parts).as_posix()
                    if _path_matches(
                        relative_path,
                        include_globs=include_globs,
                        exclude_globs=exclude_globs,
                    ):
                        scanned[relative_path] = _snapshot(info)
                    continue
                if not stat.S_ISDIR(mode):
                    continue
                _ensure_same_device(
                    info,
                    root_device=root_device,
                    entry_kind="directory",
                )
                if not recursive:
                    continue
                child_fd = _open_child_directory(
                    directory_fd,
                    entry.name,
                    expected=info,
                    root_device=root_device,
                    root_mount_id=root_mount_id,
                )
                try:
                    visit(child_fd, relative_parts)
                finally:
                    os.close(child_fd)

        visit(watch_fd, watch_parts)
    return scanned


@contextmanager
def _open_watch_directory(
    root: Path,
    relative_parts: tuple[str, ...],
) -> Iterator[tuple[int, int]]:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        current_fd = os.open(root, flags)
    except OSError as exc:
        raise HostDirectoryScanError("Host directory root is unavailable") from exc
    try:
        root_info = os.fstat(current_fd)
        if not stat.S_ISDIR(root_info.st_mode):
            raise HostDirectoryScanError("Host directory root is not a directory")
        root_device = root_info.st_dev
        root_mount_id = _mount_id(current_fd)
        for part in relative_parts:
            try:
                next_fd = os.open(part, flags, dir_fd=current_fd)
            except OSError as exc:
                raise HostDirectoryScanError(
                    "Configured host directory watch path is unavailable"
                ) from exc
            try:
                next_info = os.fstat(next_fd)
                if not stat.S_ISDIR(next_info.st_mode):
                    raise HostDirectoryScanError(
                        "Configured host directory watch path is not a directory"
                    )
                if next_info.st_dev != root_device:
                    raise HostDirectoryScanError(
                        "Host directory watch path crosses a mount boundary"
                    )
                _ensure_same_mount(
                    next_fd,
                    root_mount_id=root_mount_id,
                    entry_kind="watch path",
                )
            except Exception:
                os.close(next_fd)
                raise
            os.close(current_fd)
            current_fd = next_fd
        yield current_fd, root_device
    finally:
        os.close(current_fd)


def _open_child_directory(
    parent_fd: int,
    name: str,
    *,
    expected: os.stat_result,
    root_device: int,
    root_mount_id: int,
) -> int:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    try:
        child_fd = os.open(name, flags, dir_fd=parent_fd)
    except OSError as exc:
        raise HostDirectoryScanError("Host directory changed during scan") from exc
    actual = os.fstat(child_fd)
    if (
        not stat.S_ISDIR(actual.st_mode)
        or actual.st_dev != root_device
        or actual.st_dev != expected.st_dev
        or actual.st_ino != expected.st_ino
    ):
        os.close(child_fd)
        raise HostDirectoryScanError("Host directory changed during scan")
    try:
        _ensure_same_mount(
            child_fd,
            root_mount_id=root_mount_id,
            entry_kind="directory",
        )
    except Exception:
        os.close(child_fd)
        raise
    return child_fd


def _ensure_same_device(
    info: os.stat_result,
    *,
    root_device: int,
    entry_kind: str,
) -> None:
    if info.st_dev != root_device:
        raise HostDirectoryScanError(
            f"Host directory scan refuses a mount-boundary crossing for {entry_kind}"
        )


def _mount_id(file_descriptor: int) -> int:
    try:
        content = Path(f"/proc/self/fdinfo/{file_descriptor}").read_text(encoding="utf-8")
    except OSError as exc:
        raise HostDirectoryScanError(
            "Host directory mount-boundary verification is unavailable"
        ) from exc
    for line in content.splitlines():
        key, separator, value = line.partition(":")
        if separator and key.strip() == "mnt_id":
            try:
                return int(value.strip())
            except ValueError:
                break
    raise HostDirectoryScanError("Host directory mount-boundary verification is unavailable")


def _ensure_same_mount(
    file_descriptor: int,
    *,
    root_mount_id: int,
    entry_kind: str,
) -> None:
    if _mount_id(file_descriptor) != root_mount_id:
        raise HostDirectoryScanError(
            f"Host directory scan refuses a mount-boundary crossing for {entry_kind}"
        )


def _snapshot(info: os.stat_result) -> dict[str, Any]:
    return {
        "device": int(info.st_dev),
        "inode": int(info.st_ino),
        "size": int(info.st_size),
        "modified_ns": int(info.st_mtime_ns),
        "modified_at": _format_timestamp_ns(info.st_mtime_ns),
        "revision": host_directory_revision(info),
    }


def _same_identity(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return _coerce_int(left.get("device"), default=-1) == _coerce_int(
        right.get("device"), default=-2
    ) and _coerce_int(left.get("inode"), default=-1) == _coerce_int(right.get("inode"), default=-2)


def _same_stable_snapshot(left: dict[str, Any], right: dict[str, Any]) -> bool:
    return _same_identity(left, right) and str(left.get("revision") or "") == str(
        right.get("revision") or "__missing__"
    )


def _path_matches(
    relative_path: str,
    *,
    include_globs: list[str],
    exclude_globs: list[str],
) -> bool:
    if include_globs and not any(
        _glob_matches(relative_path, pattern) for pattern in include_globs
    ):
        return False
    return not any(_glob_matches(relative_path, pattern) for pattern in exclude_globs)


def _glob_matches(relative_path: str, pattern: str) -> bool:
    if fnmatch.fnmatchcase(relative_path, pattern):
        return True
    if pattern.startswith("**/") and fnmatch.fnmatchcase(
        relative_path,
        pattern.removeprefix("**/"),
    ):
        return True
    if "/" not in pattern:
        return fnmatch.fnmatchcase(PurePosixPath(relative_path).name, pattern)
    return False


def _mapping_of_mappings(value: object) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        return {}
    return {str(key): dict(item) for key, item in value.items() if isinstance(item, dict)}


def _mapping_of_positive_ints(value: object) -> dict[str, int]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, int] = {}
    for key, item in value.items():
        normalized = _coerce_int(item, default=0)
        if normalized > 0:
            result[str(key)] = normalized
    return result


def _coerce_int(value: object, *, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _format_datetime(value: datetime) -> str:
    normalized = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return normalized.isoformat().replace("+00:00", "Z")


def _format_timestamp_ns(value: int) -> str:
    seconds, nanoseconds = divmod(int(value), 1_000_000_000)
    timestamp = datetime.fromtimestamp(seconds, tz=UTC).replace(microsecond=nanoseconds // 1_000)
    return _format_datetime(timestamp)


def _utc_now() -> datetime:
    return datetime.now(UTC)

from __future__ import annotations

import fcntl
import json
import os
import stat
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evander.repositories.session_manifests import JsonSessionManifestRepository


_GENERIC_TERMINAL_PR_STATUSES = frozenset({"abandoned", "closed", "completed", "merged"})
_AZURE_DEVOPS_TERMINAL_PR_STATUSES = frozenset({"abandoned", "closed", "completed"})
_GITHUB_TERMINAL_PR_STATUSES = frozenset({"closed", "merged"})


@dataclass(frozen=True)
class PrMapEntry:
    key: str
    provider: str
    repo: str
    pr_id: str
    session_id: str
    vm_id: str
    directory_path: str
    raw: dict[str, Any]


@dataclass(frozen=True)
class PrStatusUpdateResult:
    key: str
    entry: dict[str, Any]
    previous_entry: dict[str, Any]
    changed: bool
    completed_ready: bool


def pr_map_provider_name(provider: object) -> str:
    normalized = str(provider or "").strip().lower()
    if normalized in {"ado", "azure-devops"}:
        return "azure_devops"
    return normalized


def normalize_pr_status(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    return normalized or None


def list_pr_map_entries(
    path: Path,
    *,
    provider: str | None = None,
    directory_path: str | None = None,
    runtime_dir: Path | None = None,
) -> list[PrMapEntry]:
    payload = _read_pr_map_payload(path)
    entries = payload.get("entries")
    if not isinstance(entries, dict):
        return []

    expected_provider = pr_map_provider_name(provider) if provider else None
    directory_scope = str(directory_path or "").strip()
    session_directories, session_vm_ids = _session_indexes(runtime_dir)

    mapped: list[PrMapEntry] = []
    for entry_key, raw in entries.items():
        if not isinstance(raw, dict):
            continue
        row_provider = pr_map_provider_name(raw.get("provider"))
        repo = str(raw.get("repo", "")).strip()
        pr_id = str(raw.get("pr_id", "")).strip()
        session_id = str(raw.get("session_id", "")).strip()
        mapped_directory_path = (
            session_directories.get(session_id)
            or str(raw.get("directory_path", "")).strip()
            or "root"
        )
        if expected_provider and row_provider != expected_provider:
            continue
        if not repo or not pr_id:
            continue
        if directory_scope and not (
            mapped_directory_path == directory_scope
            or mapped_directory_path.startswith(f"{directory_scope}/")
        ):
            continue
        mapped.append(
            PrMapEntry(
                key=str(entry_key),
                provider=row_provider,
                repo=repo,
                pr_id=pr_id,
                session_id=session_id,
                vm_id=session_vm_ids.get(session_id, ""),
                directory_path=mapped_directory_path,
                raw=dict(raw),
            )
        )
    mapped.sort(key=lambda entry: (entry.provider, entry.repo, entry.pr_id, entry.key))
    return mapped


class PrMapStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def upsert_binding(
        self,
        *,
        provider: str,
        repo: str,
        pr_id: str,
        pr_url: str | None,
        source_branch: str,
        target_branch: str,
        session_id: str,
        directory_path: str,
    ) -> dict[str, Any]:
        normalized_provider = pr_map_provider_name(provider)
        normalized_repo = str(repo or "").strip()
        normalized_pr_id = str(pr_id or "").strip()
        if not normalized_provider or not normalized_repo or not normalized_pr_id:
            raise ValueError("PR binding requires provider, repo, and pr_id")
        timestamp = _isoformat_utc(datetime.now(UTC))
        key = f"{normalized_provider}:{normalized_repo}:{normalized_pr_id}"
        with _locked_pr_map_payload(self.path) as payload:
            entries = payload.setdefault("entries", {})
            if not isinstance(entries, dict):
                entries = {}
                payload["entries"] = entries
            previous = entries.get(key)
            entry = dict(previous) if isinstance(previous, dict) else {}
            entry.update(
                {
                    "provider": normalized_provider,
                    "repo": normalized_repo,
                    "pr_id": normalized_pr_id,
                    "pr_url": str(pr_url or "").strip() or None,
                    "origin_branch": str(source_branch or "").strip(),
                    "base_branch": str(target_branch or "").strip(),
                    "session_id": str(session_id or "").strip(),
                    "directory_path": str(directory_path or "").strip() or "root",
                    "updated_at": timestamp,
                }
            )
            entries[key] = entry
            payload["updated_at"] = timestamp
        return entry

    def update_status(
        self,
        snapshot: dict[str, Any],
        *,
        trigger_id: str | None = None,
        checked_at: datetime | None = None,
    ) -> PrStatusUpdateResult:
        provider = pr_map_provider_name(snapshot.get("provider"))
        repo = str(snapshot.get("repo", "")).strip()
        pr_id = str(snapshot.get("pr_id", "")).strip()
        if not provider or not repo or not pr_id:
            raise ValueError("PR status snapshot requires provider, repo, and pr_id")
        timestamp = _isoformat_utc(checked_at or datetime.now(UTC))

        with _locked_pr_map_payload(self.path) as payload:
            entries = payload.setdefault("entries", {})
            if not isinstance(entries, dict):
                entries = {}
                payload["entries"] = entries

            entry_key = _find_pr_entry_key(entries, provider=provider, repo=repo, pr_id=pr_id)
            if entry_key is None:
                entry_key = f"{provider}:{repo}:{pr_id}"
                entries[entry_key] = {"provider": provider, "repo": repo, "pr_id": pr_id}
            raw_entry = entries.get(entry_key)
            entry = dict(raw_entry) if isinstance(raw_entry, dict) else {}
            previous = dict(entry)

            entry["provider"] = provider
            entry["repo"] = repo
            entry["pr_id"] = pr_id
            entry["status_checked_at"] = timestamp
            _set_optional(entry, "status", normalize_pr_status(snapshot.get("status")))
            _set_optional(entry, "merged", _coerce_bool(snapshot.get("merged")))
            _set_optional(entry, "pr_url", _optional_text(snapshot.get("pr_url")))
            _set_optional(entry, "title", _optional_text(snapshot.get("title")))
            _set_optional(entry, "source_branch", _optional_text(snapshot.get("source_branch")))
            _set_optional(entry, "target_branch", _optional_text(snapshot.get("target_branch")))
            _set_optional(entry, "source_commit", _optional_text(snapshot.get("source_commit")))
            _set_optional(entry, "target_commit", _optional_text(snapshot.get("target_commit")))
            _set_optional(entry, "completed_at", _normalize_datetime(snapshot.get("completed_at")))

            status_fields = (
                "status",
                "merged",
                "completed_at",
                "source_commit",
                "target_commit",
            )
            if any(previous.get(field) != entry.get(field) for field in status_fields):
                entry["pr_status_updated_at"] = timestamp

            completed_ready = False
            if (
                trigger_id
                and is_pr_completed(entry)
                and not completion_event_published(entry, trigger_id)
            ):
                completed_ready = True

            entries[entry_key] = entry
            payload["updated_at"] = timestamp
            changed = previous != entry

        return PrStatusUpdateResult(
            key=entry_key,
            entry=entry,
            previous_entry=previous,
            changed=changed,
            completed_ready=completed_ready,
        )

    def mark_completion_event_published(
        self,
        *,
        provider: str,
        repo: str,
        pr_id: str,
        trigger_id: str,
        event_id: str,
        published_at: datetime | None = None,
    ) -> None:
        provider = pr_map_provider_name(provider)
        timestamp = _isoformat_utc(published_at or datetime.now(UTC))
        with _locked_pr_map_payload(self.path) as payload:
            entries = payload.setdefault("entries", {})
            if not isinstance(entries, dict):
                return
            entry_key = _find_pr_entry_key(entries, provider=provider, repo=repo, pr_id=pr_id)
            if entry_key is None:
                return
            raw_entry = entries.get(entry_key)
            if not isinstance(raw_entry, dict):
                return
            entry = dict(raw_entry)
            events = entry.get("completion_events")
            if not isinstance(events, dict):
                events = {}
            events[trigger_id] = {"event_id": event_id, "published_at": timestamp}
            entry["completion_events"] = events
            entry["completion_triggered_at"] = timestamp
            entries[entry_key] = entry
            payload["updated_at"] = timestamp


def status_snapshot_from_pull_request(response: Any, *, repo: str, pr_id: str) -> dict[str, Any]:
    raw = getattr(response, "raw", None)
    if not isinstance(raw, dict):
        raw = {}
    provider = pr_map_provider_name(getattr(response, "provider", raw.get("provider", "")))
    status = normalize_pr_status(getattr(response, "status", raw.get("status")))
    merged = _response_merged(provider=provider, status=status, raw=raw)
    completed_at = _completed_at_from_response(
        provider=provider,
        status=status,
        merged=merged,
        raw=raw,
    )
    return {
        "provider": provider,
        "repo": repo,
        "pr_id": pr_id,
        "status": status,
        "merged": merged,
        "completed_at": completed_at,
        "pr_url": getattr(response, "url", None),
        "title": getattr(response, "title", None),
        "source_branch": getattr(response, "source_branch", None),
        "target_branch": getattr(response, "target_branch", None),
        "source_commit": getattr(response, "source_commit", None),
        "target_commit": getattr(response, "target_commit", None),
    }


def completion_event_id(trigger_id: str, entry: dict[str, Any]) -> str:
    completed_at = str(entry.get("completed_at") or "observed").strip() or "observed"
    return (
        f"{trigger_id}:{pr_map_provider_name(entry.get('provider'))}:"
        f"{entry.get('repo')}:{entry.get('pr_id')}:completed:{completed_at}"
    )


def completion_event_payload(entry: dict[str, Any]) -> dict[str, Any]:
    pr = {
        "provider": pr_map_provider_name(entry.get("provider")),
        "repo": str(entry.get("repo", "")).strip(),
        "pr_id": str(entry.get("pr_id", "")).strip(),
        "status": normalize_pr_status(entry.get("status")),
        "merged": _coerce_bool(entry.get("merged")),
        "completed_at": _normalize_datetime(entry.get("completed_at")),
        "pr_url": _optional_text(entry.get("pr_url")),
        "title": _optional_text(entry.get("title")),
        "source_branch": _optional_text(entry.get("source_branch")),
        "target_branch": _optional_text(entry.get("target_branch")),
        "source_commit": _optional_text(entry.get("source_commit")),
        "target_commit": _optional_text(entry.get("target_commit")),
    }
    payload: dict[str, Any] = {
        "pr": pr,
        "status": pr["status"],
        "merged": pr["merged"],
        "completed_at": pr["completed_at"],
    }
    session_id = str(entry.get("session_id", "")).strip()
    if session_id:
        payload["session_id"] = session_id
    return payload


def is_pr_completed(entry: dict[str, Any]) -> bool:
    provider = pr_map_provider_name(entry.get("provider"))
    status = normalize_pr_status(entry.get("status"))
    merged = _coerce_bool(entry.get("merged"))
    return _is_pr_terminal(provider=provider, status=status, merged=merged)


def _is_pr_terminal(*, provider: str, status: str | None, merged: bool) -> bool:
    if merged:
        return True
    if provider == "azure_devops":
        return status in _AZURE_DEVOPS_TERMINAL_PR_STATUSES
    if provider == "github":
        return status in _GITHUB_TERMINAL_PR_STATUSES
    return status in _GENERIC_TERMINAL_PR_STATUSES


def completion_event_published(entry: dict[str, Any], trigger_id: str) -> bool:
    events = entry.get("completion_events")
    if isinstance(events, dict):
        raw = events.get(trigger_id)
        if isinstance(raw, dict) and str(raw.get("event_id", "")).strip():
            return True
    return False


def _response_merged(*, provider: str, status: str | None, raw: dict[str, Any]) -> bool:
    if provider == "azure_devops":
        return status == "completed"
    if provider == "github":
        return _coerce_bool(raw.get("merged")) or bool(_optional_text(raw.get("merged_at")))
    return status in {"completed", "merged"} or _coerce_bool(raw.get("merged"))


def _completed_at_from_response(
    *, provider: str, status: str | None, merged: bool, raw: dict[str, Any]
) -> str | None:
    if not _is_pr_terminal(provider=provider, status=status, merged=merged):
        return None
    candidates = (
        "closedDate",
        "completionQueueTime",
        "merged_at",
        "closed_at",
        "updated_at",
        "lastUpdatedDate",
    )
    for key in candidates:
        normalized = _normalize_datetime(raw.get(key))
        if normalized:
            return normalized
    return None


def _session_indexes(runtime_dir: Path | None) -> tuple[dict[str, str], dict[str, str]]:
    if runtime_dir is None:
        return {}, {}
    sessions_path = Path(runtime_dir) / "sessions.json"
    if not sessions_path.exists():
        return {}, {}
    try:
        repository = JsonSessionManifestRepository(sessions_path)
        directories: dict[str, str] = {}
        vm_ids: dict[str, str] = {}
        for manifest in repository.list_sessions():
            session_id = str(manifest.session_id).strip()
            if not session_id:
                continue
            directories[session_id] = str(manifest.directory_path or "root").strip() or "root"
            vm_id = str(manifest.vm_id or "").strip()
            if vm_id:
                vm_ids[session_id] = vm_id
        return directories, vm_ids
    except Exception:
        return {}, {}


def _find_pr_entry_key(
    entries: dict[str, Any], *, provider: str, repo: str, pr_id: str
) -> str | None:
    preferred = f"{provider}:{repo}:{pr_id}"
    raw = entries.get(preferred)
    if isinstance(raw, dict):
        return preferred
    for key, value in entries.items():
        if not isinstance(value, dict):
            continue
        if (
            pr_map_provider_name(value.get("provider")) == provider
            and str(value.get("repo", "")).strip() == repo
            and str(value.get("pr_id", "")).strip() == pr_id
        ):
            return str(key)
    return None


def _read_pr_map_payload(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "entries": {}}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "entries": {}}
    return payload if isinstance(payload, dict) else {"version": 1, "entries": {}}


class _locked_pr_map_payload:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.payload: dict[str, Any] = {}
        self._lock_file = None

    def __enter__(self) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_name(f".{self.path.name}.lock")
        self._lock_file = lock_path.open("a+", encoding="utf-8")
        if os.geteuid() == 0:
            owner_stat = (self.path if self.path.exists() else self.path.parent).stat()
            os.fchown(self._lock_file.fileno(), owner_stat.st_uid, owner_stat.st_gid)
        fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_EX)
        self.payload = _read_pr_map_payload(self.path)
        return self.payload

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            if exc_type is None:
                _write_pr_map_payload(self.path, self.payload)
        finally:
            if self._lock_file is not None:
                fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
                self._lock_file.close()


def _write_pr_map_payload(path: Path, payload: dict[str, Any]) -> None:
    serialized = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    owner_source = path if path.exists() else path.parent
    owner_stat = owner_source.stat()
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else _default_file_mode()
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        text=True,
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_path, mode)
        if os.geteuid() == 0:
            os.chown(temp_path, owner_stat.st_uid, owner_stat.st_gid)
        os.replace(temp_path, path)
        dir_fd = os.open(path.parent, getattr(os, "O_DIRECTORY", 0) | os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        if temp_path.exists():
            temp_path.unlink()


def _default_file_mode() -> int:
    current_umask = os.umask(0)
    try:
        return 0o666 & ~current_umask
    finally:
        os.umask(current_umask)


def _set_optional(target: dict[str, Any], key: str, value: Any) -> None:
    if value is None:
        target.pop(key, None)
    else:
        target[key] = value


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _coerce_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    normalized = str(value).strip().lower()
    return normalized in {"1", "true", "yes", "y", "on", "completed", "merged"}


def _normalize_datetime(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _isoformat_utc(value)
    text = str(value).strip()
    if not text:
        return None
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return text
    return _isoformat_utc(parsed)


def _isoformat_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")

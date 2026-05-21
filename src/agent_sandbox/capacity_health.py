from __future__ import annotations

import os
import shutil
import stat as stat_module
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Literal, Mapping

from pydantic import BaseModel, Field, field_validator, model_validator


class TelemetryStatus(str, Enum):
    AVAILABLE = "available"
    MISSING = "missing"
    UNSUPPORTED = "unsupported"
    PERMISSION_DENIED = "permission_denied"


class HealthState(str, Enum):
    OK = "ok"
    WARNING = "warning"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


class DiskThresholds(BaseModel):
    source: str = "built_in_defaults"
    warning_free_percent: float = 15.0
    critical_free_percent: float = 5.0

    @field_validator("source", mode="before")
    @classmethod
    def normalize_source(cls, value: object) -> str:
        text = str(value or "").strip()
        return text or "built_in_defaults"

    @field_validator("warning_free_percent", "critical_free_percent", mode="before")
    @classmethod
    def normalize_percent(cls, value: object) -> float:
        if value is None:
            raise ValueError("threshold values must be provided")
        return float(value)

    @model_validator(mode="after")
    def validate_threshold_order(self) -> "DiskThresholds":
        if not 0 <= self.critical_free_percent <= 100:
            raise ValueError("critical_free_percent must be between 0 and 100")
        if not 0 <= self.warning_free_percent <= 100:
            raise ValueError("warning_free_percent must be between 0 and 100")
        if self.warning_free_percent < self.critical_free_percent:
            raise ValueError("warning_free_percent must be >= critical_free_percent")
        return self


class TelemetryError(BaseModel):
    status: TelemetryStatus
    message: str

    @field_validator("message", mode="before")
    @classmethod
    def normalize_message(cls, value: object) -> str:
        text = str(value or "").strip()
        return text or "telemetry collection failed"


class DiskUsageTelemetry(BaseModel):
    scope: str
    status: TelemetryStatus
    health: HealthState
    thresholds: DiskThresholds = Field(default_factory=DiskThresholds)
    total_bytes: int | None = None
    used_bytes: int | None = None
    free_bytes: int | None = None
    used_percent: float | None = None
    free_percent: float | None = None
    error_count: int = 0
    errors: list[TelemetryError] = Field(default_factory=list)


class DirectorySizeTelemetry(BaseModel):
    scope: str
    status: TelemetryStatus
    health: HealthState
    size_bytes: int = 0
    file_count: int = 0
    directory_count: int = 0
    symlink_count: int = 0
    error_count: int = 0
    errors: list[TelemetryError] = Field(default_factory=list)


class SessionStorageSample(BaseModel):
    session_id: str
    size_bytes: int = 0
    backend: str | None = None
    directory_path: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @field_validator("session_id", mode="before")
    @classmethod
    def normalize_session_id(cls, value: object) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("session_id is required")
        return text

    @field_validator("backend", "directory_path", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: object) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @field_validator("size_bytes", mode="before")
    @classmethod
    def normalize_size_bytes(cls, value: object) -> int:
        if value is None:
            return 0
        return max(0, int(value))


SessionStorageOrder = Literal["largest", "oldest"]


def default_disk_thresholds() -> DiskThresholds:
    return DiskThresholds()


def collect_disk_usage(
    path: Path | str,
    *,
    scope: str,
    thresholds: DiskThresholds | Mapping[str, Any] | None = None,
) -> DiskUsageTelemetry:
    normalized_path = Path(path)
    threshold_model = _coerce_thresholds(thresholds)
    try:
        usage = shutil.disk_usage(normalized_path)
    except FileNotFoundError:
        return _disk_usage_failure(
            scope=scope,
            status=TelemetryStatus.MISSING,
            message="path is missing",
            thresholds=threshold_model,
        )
    except PermissionError:
        return _disk_usage_failure(
            scope=scope,
            status=TelemetryStatus.PERMISSION_DENIED,
            message="permission denied",
            thresholds=threshold_model,
        )
    except OSError:
        return _disk_usage_failure(
            scope=scope,
            status=TelemetryStatus.UNSUPPORTED,
            message="disk usage is unavailable",
            thresholds=threshold_model,
        )

    free_percent = _percent(usage.free, usage.total)
    used_percent = _percent(usage.used, usage.total)
    health = _disk_health_from_thresholds(free_percent, threshold_model)
    return DiskUsageTelemetry(
        scope=scope,
        status=TelemetryStatus.AVAILABLE,
        health=health,
        thresholds=threshold_model,
        total_bytes=usage.total,
        used_bytes=usage.used,
        free_bytes=usage.free,
        used_percent=used_percent,
        free_percent=free_percent,
    )


def collect_recursive_directory_size(
    path: Path | str,
    *,
    scope: str,
) -> DirectorySizeTelemetry:
    root = Path(path)
    if root.is_symlink():
        return _directory_size_failure(
            scope=scope,
            status=TelemetryStatus.UNSUPPORTED,
            message="symlink roots are not followed",
        )
    try:
        root_stat = root.stat()
    except FileNotFoundError:
        return _directory_size_failure(
            scope=scope,
            status=TelemetryStatus.MISSING,
            message="path is missing",
        )
    except PermissionError:
        return _directory_size_failure(
            scope=scope,
            status=TelemetryStatus.PERMISSION_DENIED,
            message="permission denied",
        )
    except OSError:
        return _directory_size_failure(
            scope=scope,
            status=TelemetryStatus.UNSUPPORTED,
            message="directory size is unavailable",
        )

    if stat_module.S_ISREG(root_stat.st_mode):
        return DirectorySizeTelemetry(
            scope=scope,
            status=TelemetryStatus.AVAILABLE,
            health=HealthState.OK,
            size_bytes=max(0, int(root_stat.st_size)),
            file_count=1,
        )
    if not stat_module.S_ISDIR(root_stat.st_mode):
        return _directory_size_failure(
            scope=scope,
            status=TelemetryStatus.UNSUPPORTED,
            message="path is not a regular file or directory",
        )

    totals = {
        "size_bytes": 0,
        "file_count": 0,
        "directory_count": 0,
        "symlink_count": 0,
    }
    errors: list[TelemetryError] = []
    _accumulate_directory_size(root, totals, errors)
    health = HealthState.OK if not errors else HealthState.WARNING
    return DirectorySizeTelemetry(
        scope=scope,
        status=TelemetryStatus.AVAILABLE,
        health=health,
        size_bytes=totals["size_bytes"],
        file_count=totals["file_count"],
        directory_count=totals["directory_count"],
        symlink_count=totals["symlink_count"],
        error_count=len(errors),
        errors=errors,
    )


def top_session_storage(
    entries: Iterable[SessionStorageSample | Mapping[str, Any] | Any],
    *,
    limit: int = 10,
    order: SessionStorageOrder = "largest",
) -> list[SessionStorageSample]:
    normalized = [_coerce_session_storage_sample(entry) for entry in entries]
    if limit <= 0 or not normalized:
        return []
    if order == "oldest":
        normalized.sort(key=_oldest_session_sort_key)
    else:
        normalized.sort(key=_largest_session_sort_key, reverse=True)
    return normalized[:limit]


def _coerce_thresholds(
    thresholds: DiskThresholds | Mapping[str, Any] | None,
) -> DiskThresholds:
    if thresholds is None:
        return default_disk_thresholds()
    if isinstance(thresholds, DiskThresholds):
        return thresholds
    return DiskThresholds.model_validate(thresholds)


def _coerce_session_storage_sample(
    entry: SessionStorageSample | Mapping[str, Any] | Any,
) -> SessionStorageSample:
    if isinstance(entry, SessionStorageSample):
        return entry
    if isinstance(entry, Mapping):
        return SessionStorageSample.model_validate(entry)
    payload = {
        "session_id": getattr(entry, "session_id", ""),
        "size_bytes": getattr(entry, "size_bytes", 0),
        "backend": getattr(entry, "backend", None),
        "directory_path": getattr(entry, "directory_path", None),
        "created_at": getattr(entry, "created_at", None),
        "updated_at": getattr(entry, "updated_at", None),
    }
    return SessionStorageSample.model_validate(payload)


def _disk_usage_failure(
    *,
    scope: str,
    status: TelemetryStatus,
    message: str,
    thresholds: DiskThresholds,
) -> DiskUsageTelemetry:
    return DiskUsageTelemetry(
        scope=scope,
        status=status,
        health=HealthState.UNKNOWN,
        thresholds=thresholds,
        error_count=1,
        errors=[TelemetryError(status=status, message=message)],
    )


def _directory_size_failure(
    *,
    scope: str,
    status: TelemetryStatus,
    message: str,
) -> DirectorySizeTelemetry:
    return DirectorySizeTelemetry(
        scope=scope,
        status=status,
        health=HealthState.UNKNOWN,
        error_count=1,
        errors=[TelemetryError(status=status, message=message)],
    )


def _percent(part: int, whole: int) -> float:
    if whole <= 0:
        return 0.0
    return round((part / whole) * 100.0, 4)


def _disk_health_from_thresholds(
    free_percent: float | None,
    thresholds: DiskThresholds,
) -> HealthState:
    if free_percent is None:
        return HealthState.UNKNOWN
    if free_percent <= thresholds.critical_free_percent:
        return HealthState.CRITICAL
    if free_percent <= thresholds.warning_free_percent:
        return HealthState.WARNING
    return HealthState.OK


def _accumulate_directory_size(
    path: Path,
    totals: dict[str, int],
    errors: list[TelemetryError],
) -> None:
    try:
        entries = list(os.scandir(path))
    except FileNotFoundError:
        errors.append(
            TelemetryError(status=TelemetryStatus.MISSING, message="directory entry is missing")
        )
        return
    except PermissionError:
        errors.append(
            TelemetryError(
                status=TelemetryStatus.PERMISSION_DENIED,
                message="permission denied while scanning directory",
            )
        )
        return
    except NotADirectoryError:
        errors.append(
            TelemetryError(
                status=TelemetryStatus.UNSUPPORTED,
                message="path is not a directory",
            )
        )
        return
    except OSError:
        errors.append(
            TelemetryError(
                status=TelemetryStatus.UNSUPPORTED,
                message="directory size is unavailable",
            )
        )
        return

    for entry in entries:
        try:
            if entry.is_symlink():
                totals["symlink_count"] += 1
                continue
            if entry.is_dir(follow_symlinks=False):
                totals["directory_count"] += 1
                _accumulate_directory_size(Path(entry.path), totals, errors)
                continue

            entry_stat = entry.stat(follow_symlinks=False)
            if stat_module.S_ISREG(entry_stat.st_mode):
                totals["file_count"] += 1
                totals["size_bytes"] += max(0, int(entry_stat.st_size))
                continue

            errors.append(
                TelemetryError(
                    status=TelemetryStatus.UNSUPPORTED,
                    message="unsupported filesystem entry",
                )
            )
        except FileNotFoundError:
            errors.append(
                TelemetryError(status=TelemetryStatus.MISSING, message="directory entry is missing")
            )
        except PermissionError:
            errors.append(
                TelemetryError(
                    status=TelemetryStatus.PERMISSION_DENIED,
                    message="permission denied while scanning directory",
                )
            )
        except OSError:
            errors.append(
                TelemetryError(
                    status=TelemetryStatus.UNSUPPORTED,
                    message="directory entry is unavailable",
                )
            )


def _largest_session_sort_key(entry: SessionStorageSample) -> tuple[int, datetime, datetime, str]:
    created_at = entry.created_at or datetime.min.replace(tzinfo=UTC)
    updated_at = entry.updated_at or datetime.min.replace(tzinfo=UTC)
    return (entry.size_bytes, created_at, updated_at, entry.session_id)


def _oldest_session_sort_key(entry: SessionStorageSample) -> tuple[datetime, datetime, str]:
    created_at = entry.created_at or datetime.max.replace(tzinfo=UTC)
    updated_at = entry.updated_at or datetime.max.replace(tzinfo=UTC)
    return (created_at, updated_at, entry.session_id)


__all__ = [
    "DiskThresholds",
    "DiskUsageTelemetry",
    "DirectorySizeTelemetry",
    "HealthState",
    "SessionStorageOrder",
    "SessionStorageSample",
    "TelemetryError",
    "TelemetryStatus",
    "collect_disk_usage",
    "collect_recursive_directory_size",
    "default_disk_thresholds",
    "top_session_storage",
]

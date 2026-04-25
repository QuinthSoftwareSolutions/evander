from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class ExecutionBackendKind(str, Enum):
    LIBVIRT_VM = "libvirt_vm"
    CONTAINER_WORKER_VM = "container_worker_vm"


class ExecutionStatusKind(str, Enum):
    UNKNOWN = "unknown"
    PREPARING = "preparing"
    STARTING = "starting"
    RUNNING = "running"
    STOPPED = "stopped"
    FAILED = "failed"
    DESTROYED = "destroyed"


class ExecutionSpec(BaseModel):
    backend: ExecutionBackendKind = ExecutionBackendKind.LIBVIRT_VM
    target_name: str | None = None
    session_id: str | None = None
    directory_path: str = "root"
    vm_provider: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("target_name", "session_id", "vm_provider", mode="before")
    @classmethod
    def normalize_optional_strings(cls, value: object) -> object:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @field_validator("directory_path", mode="before")
    @classmethod
    def normalize_directory_path(cls, value: object) -> str:
        text = str(value or "").strip()
        return text or "root"


class PreparedExecution(BaseModel):
    backend: ExecutionBackendKind
    execution_id: str
    target_name: str
    session_id: str | None = None
    directory_path: str = "root"
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExecutionHandle(BaseModel):
    backend: ExecutionBackendKind
    execution_id: str
    target_name: str
    session_id: str | None = None
    directory_path: str = "root"
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExecutionStatus(BaseModel):
    backend: ExecutionBackendKind
    execution_id: str
    target_name: str
    status: ExecutionStatusKind = ExecutionStatusKind.UNKNOWN
    session_id: str | None = None
    message: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExecutionBackendCapabilities(BaseModel):
    supports_prepare: bool = True
    supports_stop: bool = True
    supports_destroy: bool = True
    supports_log_snapshot: bool = True

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent_sandbox.execution import ExecutionBackendKind


def normalize_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def effective_execution_backend_for_session(session: Any | None, *, fallback_backend: str | None = None) -> str:
    provider = normalize_optional_string(getattr(session, "vm_provider", None))
    if provider is None:
        metadata = getattr(session, "metadata", {}) if session is not None else {}
        if isinstance(metadata, Mapping):
            provider = normalize_optional_string(
                metadata.get("execution_backend") or metadata.get("vm_provider")
            )
    if provider == ExecutionBackendKind.CONTAINER_WORKER_VM.value:
        return ExecutionBackendKind.CONTAINER_WORKER_VM.value
    if provider in {ExecutionBackendKind.LIBVIRT_VM.value, "libvirt", "azure"}:
        return ExecutionBackendKind.LIBVIRT_VM.value
    if provider:
        return provider
    if fallback_backend is not None:
        return fallback_backend
    return ""


def session_is_container_worker_vm(session: Any | None) -> bool:
    return (
        effective_execution_backend_for_session(session)
        == ExecutionBackendKind.CONTAINER_WORKER_VM.value
    )


def session_has_vm_binding(session: Any | None) -> bool:
    return bool(normalize_optional_string(getattr(session, "vm_id", None)))

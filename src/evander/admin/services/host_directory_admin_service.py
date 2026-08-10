from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any, ContextManager, Protocol

from evander.admin.directory import (
    AdminResult,
    DirectoryConfigPayload,
    DirectoryConfigWritePayload,
    DirectoryEffectiveConfigPayload,
)
from evander.host_directories.models import (
    HostDirectoryDirectoryConfig,
    normalize_host_directory_id,
)
from evander.host_directories.service import HostDirectoryService


class HostDirectoryConfigRevisionConflict(RuntimeError):
    pass


class HostDirectoryConfigOperationsPort(Protocol):
    def read_directory_domain_config(
        self, directory_path: str, domain: str
    ) -> AdminResult[DirectoryConfigPayload]: ...

    def compute_directory_effective_config(
        self, directory_path: str, domain: str
    ) -> AdminResult[DirectoryEffectiveConfigPayload]: ...

    def write_directory_domain_config(
        self, directory_path: str, domain: str, payload: dict[str, Any]
    ) -> AdminResult[DirectoryConfigWritePayload]: ...

    def admin_config_mutation_lock(self) -> ContextManager[None]: ...


class HostDirectoryDirectoryInventoryPort(Protocol):
    def list_descendants(self, reference: str) -> list[Any]: ...


@dataclass(frozen=True, slots=True)
class HostDirectoryAdminDependencies:
    operations_admin: HostDirectoryConfigOperationsPort
    host_directory_service: HostDirectoryService
    directory_inventory: HostDirectoryDirectoryInventoryPort


class HostDirectoryAdminService:
    """Admin application service for host-directory configuration and recovery."""

    def __init__(self, dependencies: HostDirectoryAdminDependencies) -> None:
        self._deps = dependencies

    def snapshot(self, directory_path: str) -> dict[str, Any]:
        raw_result = self._deps.operations_admin.read_directory_domain_config(
            directory_path, "host_directories"
        )
        raw = (
            dict(raw_result.data.config)
            if raw_result.ok and raw_result.data is not None and raw_result.data.config is not None
            else {}
        )
        effective_result = self._deps.operations_admin.compute_directory_effective_config(
            directory_path, "host_directories"
        )
        if not effective_result.ok or effective_result.data is None:
            message = (
                effective_result.errors[0].message
                if effective_result.errors
                else "Unable to resolve host directory configuration"
            )
            raise ValueError(message)
        resolution = effective_result.data.resolution
        if resolution.validation_errors:
            raise ValueError("; ".join(resolution.validation_errors))
        effective = HostDirectoryDirectoryConfig.model_validate(
            dict(resolution.effective_config or {})
        )
        self._deps.host_directory_service.validate_config(effective)
        local_definitions = raw.get("host_directories")
        if not isinstance(local_definitions, dict):
            local_definitions = {}
        source_paths: dict[str, str] = {}
        for layer in resolution.applied_layers:
            definitions = layer.overlay.get("host_directories")
            if not isinstance(definitions, dict):
                continue
            for directory_id in definitions:
                source_paths[str(directory_id)] = layer.directory_path
        resources: list[dict[str, Any]] = []
        for directory_id, definition in sorted(effective.host_directories.items()):
            health: dict[str, Any] | None = (
                {
                    "status": "disabled",
                    "available": False,
                    "directory_id": directory_id,
                }
                if definition.disabled
                else None
            )
            resources.append(
                {
                    "directory_id": directory_id,
                    **definition.model_dump(mode="json"),
                    "defined_here": directory_id in local_definitions,
                    "source_directory_path": source_paths.get(directory_id),
                    "health": health,
                }
            )
        return {
            "directory_path": directory_path,
            "revision": _revision(raw),
            "config": raw,
            "effective_config": effective.model_dump(mode="json"),
            "host_directories": resources,
            "provenance": {
                key: value.model_dump(mode="json")
                for key, value in (resolution.provenance or {}).items()
            },
        }

    def save(
        self,
        directory_path: str,
        *,
        expected_revision: str,
        config_payload: object,
    ) -> dict[str, Any]:
        if not expected_revision:
            raise ValueError("expected_revision is required")
        if not isinstance(config_payload, dict):
            raise ValueError("config must be a JSON object")
        with self._deps.operations_admin.admin_config_mutation_lock():
            current = self.snapshot(directory_path)
            if current["revision"] != expected_revision:
                raise HostDirectoryConfigRevisionConflict(
                    "Host directory configuration changed before save. Reload before saving again."
                )
            current_resolution_result = (
                self._deps.operations_admin.compute_directory_effective_config(
                    directory_path, "host_directories"
                )
            )
            if not current_resolution_result.ok or current_resolution_result.data is None:
                raise ValueError("Unable to resolve parent host directory configuration")
            parent_effective: dict[str, Any] = {"host_directories": {}}
            for layer in current_resolution_result.data.resolution.applied_layers:
                if layer.directory_path == directory_path:
                    continue
                parent_effective = _deep_merge(parent_effective, layer.overlay)
            candidate = HostDirectoryDirectoryConfig.model_validate(
                _deep_merge(parent_effective, config_payload)
            )
            self._deps.host_directory_service.validate_config(candidate)
            current_effective = HostDirectoryDirectoryConfig.model_validate(
                current["effective_config"]
            )
            self._reject_orphaned_quarantine(
                directory_path=directory_path,
                current=current_effective,
                candidate=candidate,
            )
            result = self._deps.operations_admin.write_directory_domain_config(
                directory_path, "host_directories", config_payload
            )
            if not result.ok:
                message = (
                    result.errors[0].message if result.errors else "Unable to save host directories"
                )
                raise ValueError(message)
            return self.snapshot(directory_path)

    def health(self, directory_path: str, directory_id: str) -> dict[str, Any]:
        normalized_id = normalize_host_directory_id(directory_id)
        return {
            "directory_path": directory_path,
            "directory_id": normalized_id,
            "health": self._deps.host_directory_service.health(
                directory_path=directory_path,
                directory_id=normalized_id,
            ),
        }

    def list_quarantine(self, directory_path: str, directory_id: str) -> dict[str, Any]:
        normalized_id = normalize_host_directory_id(directory_id)
        return {
            "directory_path": directory_path,
            "directory_id": normalized_id,
            "entries": self._deps.host_directory_service.list_quarantine(
                directory_path=directory_path,
                directory_id=normalized_id,
            ),
        }

    def restore_quarantine(
        self, directory_path: str, directory_id: str, recovery_id: str
    ) -> dict[str, Any]:
        normalized_id = normalize_host_directory_id(directory_id)
        return {
            "directory_path": directory_path,
            **self._deps.host_directory_service.restore_quarantine(
                directory_path=directory_path,
                directory_id=normalized_id,
                recovery_id=recovery_id,
            ),
        }

    def purge_quarantine(
        self, directory_path: str, directory_id: str, recovery_id: str
    ) -> dict[str, Any]:
        normalized_id = normalize_host_directory_id(directory_id)
        return {
            "directory_path": directory_path,
            **self._deps.host_directory_service.purge_quarantine(
                directory_path=directory_path,
                directory_id=normalized_id,
                recovery_id=recovery_id,
            ),
        }

    def _reject_orphaned_quarantine(
        self,
        *,
        directory_path: str,
        current: HostDirectoryDirectoryConfig,
        candidate: HostDirectoryDirectoryConfig,
    ) -> None:
        directory_scopes = [
            directory_path,
            *[
                str(item.path)
                for item in self._deps.directory_inventory.list_descendants(directory_path)
            ],
        ]
        for directory_id, current_definition in current.host_directories.items():
            candidate_definition = candidate.host_directories.get(directory_id)
            if (
                candidate_definition is not None
                and candidate_definition.root == current_definition.root
            ):
                continue
            for scoped_directory_path in directory_scopes:
                scoped_definition = self._deps.host_directory_service.config_for_directory(
                    scoped_directory_path
                ).host_directories.get(directory_id)
                if scoped_definition is None or scoped_definition.root != current_definition.root:
                    continue
                quarantined = self._deps.host_directory_service.list_quarantine(
                    directory_path=scoped_directory_path,
                    directory_id=directory_id,
                )
                if quarantined:
                    raise ValueError(
                        f"Host directory {directory_id!r} has quarantined deletions in "
                        f"directory {scoped_directory_path!r}; restore or purge them "
                        "before removing the definition or changing its root"
                    )


def _revision(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return sha256(encoded).hexdigest()


def _deep_merge(current: Any, overlay: Any) -> Any:
    if isinstance(current, dict) and isinstance(overlay, dict):
        merged = dict(current)
        for key, value in overlay.items():
            merged[key] = _deep_merge(merged.get(key), value)
        return merged
    return overlay

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
from typing import Any, Callable, ContextManager, Protocol

from evander.admin.directory import (
    AdminResult,
    DirectoryConfigPayload,
    DirectoryConfigWritePayload,
    DirectoryEffectiveConfigPayload,
)
from evander.connectors.models import ConnectorDirectoryConfig, normalize_connector_id
from evander.connectors.service import ConnectorService
from evander.models import AuditEvent, SessionManifest, ToolName


class ConnectorConfigRevisionConflict(RuntimeError):
    pass


class ConnectorConfigOperationsPort(Protocol):
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


class ConnectorProviderConfigPort(Protocol):
    def resolve_connectors_for_directory(self, directory_path: str) -> ConnectorDirectoryConfig: ...


class ConnectorAuditPort(Protocol):
    def log(self, event: AuditEvent) -> None: ...


@dataclass(frozen=True, slots=True)
class ConnectorAdminDependencies:
    operations_admin: ConnectorConfigOperationsPort
    provider_config: ConnectorProviderConfigPort
    audit: ConnectorAuditPort
    connector_service: ConnectorService


class ConnectorAdminService:
    """Admin application service for connector configuration and health checks."""

    def __init__(self, dependencies: ConnectorAdminDependencies) -> None:
        self._deps = dependencies

    def snapshot(self, directory_path: str) -> dict[str, Any]:
        raw_result = self._deps.operations_admin.read_directory_domain_config(
            directory_path, "connectors"
        )
        raw = (
            dict(raw_result.data.config)
            if raw_result.ok and raw_result.data is not None and raw_result.data.config is not None
            else {}
        )
        effective_result = self._deps.operations_admin.compute_directory_effective_config(
            directory_path, "connectors"
        )
        if not effective_result.ok or effective_result.data is None:
            message = (
                effective_result.errors[0].message
                if effective_result.errors
                else "Unable to resolve connector configuration"
            )
            raise ValueError(message)
        resolution = effective_result.data.resolution
        effective = ConnectorDirectoryConfig.model_validate(dict(resolution.effective_config or {}))
        self._deps.connector_service.validate_config(effective)
        connectors: list[dict[str, Any]] = []
        for connector_id, connector in sorted(effective.connectors.items()):
            connectors.append(
                {
                    "connector_id": connector_id,
                    **connector.model_dump(mode="json", exclude_none=True),
                    "auth_status": self._deps.connector_service.auth_status(effective, connector, directory_path=directory_path),
                }
            )
        return {
            "directory_path": directory_path,
            "revision": _revision(raw),
            "config": raw,
            "effective_config": effective.model_dump(mode="json", exclude_none=True),
            "connectors": connectors,
            "driver_names": list(self._deps.connector_service.registry.driver_names),
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
        preflight: Callable[[], None] | None = None,
    ) -> dict[str, Any]:
        if not expected_revision:
            raise ValueError("expected_revision is required")
        if not isinstance(config_payload, dict):
            raise ValueError("config must be a JSON object")
        with self._deps.operations_admin.admin_config_mutation_lock():
            current = self.snapshot(directory_path)
            if current["revision"] != expected_revision:
                raise ConnectorConfigRevisionConflict(
                    "Connector configuration changed before save. Reload before saving again."
                )
            current_resolution_result = (
                self._deps.operations_admin.compute_directory_effective_config(
                    directory_path, "connectors"
                )
            )
            if not current_resolution_result.ok or current_resolution_result.data is None:
                raise ValueError("Unable to resolve parent connector configuration")
            parent_effective: dict[str, Any] = {"auth_profiles": {}, "connectors": {}}
            for layer in current_resolution_result.data.resolution.applied_layers:
                if layer.directory_path == directory_path:
                    continue
                parent_effective = _deep_merge(parent_effective, layer.overlay)
            candidate = ConnectorDirectoryConfig.model_validate(
                _deep_merge(parent_effective, config_payload)
            )
            # A child may inherit a reference, but must not redeclare another
            # directory's credential as its own. Changed consumer bindings remain
            # unavailable until the credential owner explicitly approves them.
            for profile_id, raw in config_payload.get("auth_profiles", {}).items():
                if isinstance(raw, dict) and raw.get("credential_ref"):
                    metadata = self._deps.connector_service.credentials.metadata(raw["credential_ref"])
                    if metadata["owner_directory"] != directory_path or metadata["kind"] != "connector":
                        raise ValueError("Credential reference must be declared in its owning directory")
            self._deps.connector_service.validate_config(candidate)
            if preflight is not None:
                # Credential conversion must recheck descendants under the same
                # mutation lock as publication, after the parent revision check.
                preflight()
            result = self._deps.operations_admin.write_directory_domain_config(
                directory_path, "connectors", config_payload
            )
            if not result.ok:
                message = result.errors[0].message if result.errors else "Unable to save connectors"
                raise ValueError(message)
            return self.snapshot(directory_path)

    async def test_connector(
        self,
        directory_path: str,
        connector_id: object,
        *,
        actor_user_id: str | None,
    ) -> dict[str, Any]:
        normalized_id = normalize_connector_id(connector_id)
        config = self._deps.provider_config.resolve_connectors_for_directory(directory_path)
        self._deps.connector_service.validate_config(config)
        connector = config.connectors.get(normalized_id)
        if connector is None or connector.disabled:
            raise ValueError(f"unknown or disabled connector: {normalized_id}")
        if connector.health_operation is None:
            raise ValueError("connector does not define health_operation")
        session = SessionManifest(
            session_id=f"admin-connector-test-{normalized_id}",
            directory_path=directory_path,
            allowed_tools=[ToolName.CALL_CONNECTOR],
            connector_grants={normalized_id: [connector.health_operation]},
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )
        try:
            result = await self._deps.connector_service.invoke(
                session=session,
                connector_id=normalized_id,
                operation_name=connector.health_operation,
                arguments=dict(connector.health_arguments),
            )
        except Exception as exc:
            self._audit_test(
                session=session,
                connector_id=normalized_id,
                operation=connector.health_operation,
                actor_user_id=actor_user_id,
                decision="error",
                result={"error_class": type(exc).__name__},
            )
            raise
        self._audit_test(
            session=session,
            connector_id=normalized_id,
            operation=connector.health_operation,
            actor_user_id=actor_user_id,
            decision="allowed",
            result={
                "result_mode": result.result_mode,
                "upstream_status": result.upstream_status,
                "duration_ms": result.duration_ms,
            },
        )
        return {
            "status": "ok",
            "connector_id": normalized_id,
            "operation": connector.health_operation,
            "result": result.model_dump(mode="json", exclude_none=True),
        }

    def _audit_test(
        self,
        *,
        session: SessionManifest,
        connector_id: str,
        operation: str,
        actor_user_id: str | None,
        decision: str,
        result: dict[str, Any],
    ) -> None:
        self._deps.audit.log(
            AuditEvent(
                event_type="connector_test",
                session_id=session.session_id,
                directory_path=session.directory_path,
                tool_name=ToolName.CALL_CONNECTOR.value,
                connector_id=connector_id,
                connector_operation=operation,
                actor_user_id=actor_user_id,
                decision=decision,
                result=result,
            )
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

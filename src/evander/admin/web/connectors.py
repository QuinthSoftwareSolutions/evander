from __future__ import annotations

from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import BaseRoute, Route

from evander.admin.composition import AdminRuntimeComponents
from evander.admin.web.rbac import ADMIN_OWNER_PERMISSION
from evander.admin.web.shared import AdminWebAuth, json_error, request_has_privileged_operations
from evander.communication_artifacts import CommunicationArtifactStore
from evander.connectors.models import ConnectorDirectoryConfig, normalize_connector_id
from evander.connectors.service import ConnectorService, connector_registry_from_addons
from evander.models import AuditEvent, SessionManifest, ToolName


def _revision(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return sha256(encoded).hexdigest()


def _deep_merge(current: Any, overlay: Any) -> Any:
    if isinstance(current, dict) and isinstance(overlay, dict):
        merged = {key: value for key, value in current.items()}
        for key, value in overlay.items():
            merged[key] = _deep_merge(merged.get(key), value)
        return merged
    return overlay


async def _json_body(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception as exc:
        raise ValueError("Request body must be valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object")
    return payload


def connector_routes(
    service: AdminRuntimeComponents,
    auth: AdminWebAuth,
) -> list[BaseRoute]:
    connector_service = ConnectorService(
        runtime_dir=service.config.runtime_dir,
        provider_config=service.runtime.provider_config,
        artifact_store_factory=lambda: CommunicationArtifactStore(service.config.runtime_dir),
        registry=connector_registry_from_addons(
            service.config.addon_loader,
            base_dir=service.project_dir,
        ),
    )

    def _directory_path(request: Request) -> str:
        return str(request.query_params.get("directory_path") or "root").strip() or "root"

    def _require_owner(request: Request, *, privileged: bool) -> dict[str, Any] | JSONResponse:
        try:
            session = auth.require(request)
            auth.require_permission(request, ADMIN_OWNER_PERMISSION)
        except PermissionError as exc:
            return json_error(str(exc), status_code=403)
        if privileged and not request_has_privileged_operations(
            auth=auth, service=service, request=request
        ):
            return json_error("Privileged operations are locked.", status_code=403)
        return session

    def _payload(directory_path: str) -> dict[str, Any]:
        raw_result = service.operations_admin.read_directory_domain_config(
            directory_path, "connectors"
        )
        raw = (
            dict(raw_result.data.config)
            if raw_result.ok
            and raw_result.data is not None
            and raw_result.data.config is not None
            else {}
        )
        effective_result = service.operations_admin.compute_directory_effective_config(
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
        effective_raw = dict(resolution.effective_config or {})
        effective = ConnectorDirectoryConfig.model_validate(effective_raw)
        connector_service.validate_config(effective)
        connectors: list[dict[str, Any]] = []
        for connector_id, connector in sorted(effective.connectors.items()):
            status = connector_service.auth_status(effective, connector)
            connectors.append(
                {
                    "connector_id": connector_id,
                    **connector.model_dump(mode="json", exclude_none=True),
                    "auth_status": status,
                }
            )
        return {
            "directory_path": directory_path,
            "revision": _revision(raw),
            "config": raw,
            "effective_config": effective.model_dump(mode="json", exclude_none=True),
            "connectors": connectors,
            "driver_names": list(connector_service.registry.driver_names),
            "provenance": {
                key: value.model_dump(mode="json")
                for key, value in (resolution.provenance or {}).items()
            },
        }

    async def get_connectors(request: Request) -> JSONResponse:
        owner = _require_owner(request, privileged=False)
        if isinstance(owner, JSONResponse):
            return owner
        try:
            return JSONResponse(_payload(_directory_path(request)))
        except ValueError as exc:
            return json_error(str(exc), status_code=400)

    async def put_connectors(request: Request) -> JSONResponse:
        owner = _require_owner(request, privileged=True)
        if isinstance(owner, JSONResponse):
            return owner
        directory_path = _directory_path(request)
        try:
            payload = await _json_body(request)
        except ValueError as exc:
            return json_error(str(exc), status_code=400)
        expected_revision = str(payload.get("expected_revision") or "").strip()
        if not expected_revision:
            return json_error("expected_revision is required", status_code=428)
        try:
            current = _payload(directory_path)
            if current["revision"] != expected_revision:
                return json_error(
                    "Connector configuration changed before save. Reload before saving again.",
                    status_code=409,
                )
            config_payload = payload.get("config")
            if not isinstance(config_payload, dict):
                raise ValueError("config must be a JSON object")
            current_resolution_result = service.operations_admin.compute_directory_effective_config(
                directory_path, "connectors"
            )
            if not current_resolution_result.ok or current_resolution_result.data is None:
                raise ValueError("Unable to resolve parent connector configuration")
            parent_effective: dict[str, Any] = {
                "auth_profiles": {},
                "connectors": {},
            }
            for layer in current_resolution_result.data.resolution.applied_layers:
                if layer.directory_path == directory_path:
                    continue
                parent_effective = _deep_merge(parent_effective, layer.overlay)
            candidate = ConnectorDirectoryConfig.model_validate(
                _deep_merge(parent_effective, config_payload)
            )
            connector_service.validate_config(candidate)
            result = service.operations_admin.write_directory_domain_config(
                directory_path, "connectors", config_payload
            )
            if not result.ok:
                message = result.errors[0].message if result.errors else "Unable to save connectors"
                raise ValueError(message)
            return JSONResponse(_payload(directory_path))
        except ValueError as exc:
            return json_error(str(exc), status_code=400)

    async def test_connector(request: Request) -> JSONResponse:
        owner = _require_owner(request, privileged=True)
        if isinstance(owner, JSONResponse):
            return owner
        directory_path = _directory_path(request)
        try:
            connector_id = normalize_connector_id(request.path_params.get("connector_id"))
            config = service.runtime.provider_config.resolve_connectors_for_directory(
                directory_path
            )
            connector_service.validate_config(config)
            connector = config.connectors.get(connector_id)
            if connector is None or connector.disabled:
                raise ValueError(f"unknown or disabled connector: {connector_id}")
            if connector.health_operation is None:
                raise ValueError("connector does not define health_operation")
            session = SessionManifest(
                session_id=f"admin-connector-test-{connector_id}",
                directory_path=directory_path,
                allowed_tools=[ToolName.CALL_CONNECTOR],
                connector_grants={connector_id: [connector.health_operation]},
                expires_at=datetime.now(UTC) + timedelta(minutes=5),
            )
            try:
                result = await connector_service.invoke(
                    session=session,
                    connector_id=connector_id,
                    operation_name=connector.health_operation,
                    arguments=dict(connector.health_arguments),
                )
            except Exception as exc:
                service.runtime.audit.log(
                    AuditEvent(
                        event_type="connector_test",
                        session_id=session.session_id,
                        directory_path=directory_path,
                        tool_name=ToolName.CALL_CONNECTOR.value,
                        connector_id=connector_id,
                        connector_operation=connector.health_operation,
                        actor_user_id=str(owner.get("login") or "").strip() or None,
                        decision="error",
                        result={"error_class": type(exc).__name__},
                    )
                )
                raise
            service.runtime.audit.log(
                AuditEvent(
                    event_type="connector_test",
                    session_id=session.session_id,
                    directory_path=directory_path,
                    tool_name=ToolName.CALL_CONNECTOR.value,
                    connector_id=connector_id,
                    connector_operation=connector.health_operation,
                    actor_user_id=str(owner.get("login") or "").strip() or None,
                    decision="allowed",
                    result={
                        "result_mode": result.result_mode,
                        "upstream_status": result.upstream_status,
                        "duration_ms": result.duration_ms,
                    },
                )
            )
            return JSONResponse(
                {
                    "status": "ok",
                    "connector_id": connector_id,
                    "operation": connector.health_operation,
                    "result": result.model_dump(mode="json", exclude_none=True),
                }
            )
        except Exception as exc:
            return json_error(str(exc), status_code=400)

    return [
        Route("/api/admin/connectors", get_connectors, methods=["GET"]),
        Route("/api/admin/connectors", put_connectors, methods=["PUT"]),
        Route(
            "/api/admin/connectors/{connector_id:str}/test",
            test_connector,
            methods=["POST"],
        ),
    ]

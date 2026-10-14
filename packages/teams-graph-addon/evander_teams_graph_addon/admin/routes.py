from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from threading import RLock
from typing import Any
from uuid import uuid4

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import BaseRoute, Route

from evander.admin.composition import AdminRuntimeComponents
from evander.admin.web.rbac import ADMIN_OWNER_PERMISSION
from evander.admin.web.shared import AdminWebAuth, json_error, request_has_privileged_operations
from evander_teams_graph_addon.auth import (
    AuthorizationPendingError,
    DeviceCodeExpiredError,
    DeviceCodeLoginSession,
    DeviceCodePollingTimeoutError,
    SlowDownError,
    TeamsAuthError,
)

_SENSITIVE_KEYS = {
    "access_token",
    "authorization",
    "cookie",
    "refresh_token",
    "client_secret",
    "device_code",
    "id_token",
    "password",
    "secret",
    "store_path",
    "token",
}
_DEFAULT_COMPLETE_TIMEOUT_SECONDS = 5.0


def _normalize_key_name(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


_SENSITIVE_KEY_MARKERS = tuple(_normalize_key_name(item) for item in _SENSITIVE_KEYS)


def _is_sensitive_key(key: str) -> bool:
    normalized = _normalize_key_name(key)
    return any(marker in normalized for marker in _SENSITIVE_KEY_MARKERS)


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_jsonable(item) for item in value]
    return value


def _sanitize_teams_auth_payload(value: Any) -> Any:
    normalized = _jsonable(value)
    if isinstance(normalized, Mapping):
        payload: dict[str, Any] = {}
        for key, item in normalized.items():
            if _is_sensitive_key(str(key)):
                continue
            payload[str(key)] = _sanitize_teams_auth_payload(item)
        return payload
    if isinstance(normalized, list):
        return [_sanitize_teams_auth_payload(item) for item in normalized]
    if isinstance(normalized, tuple):
        return [_sanitize_teams_auth_payload(item) for item in normalized]
    return normalized


def _directory_path_from_request(service: AdminRuntimeComponents, request: Request) -> str:
    requested = str(request.query_params.get("directory_path") or "root").strip() or "root"
    try:
        return service.teams_admin.normalize_directory_reference(requested)
    except ValueError:
        return "root"


def _bool_query_param(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    normalized = value.strip().casefold()
    if not normalized:
        return default
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Invalid boolean value: {value}")


def _login_session_payload(
    session: DeviceCodeLoginSession | Mapping[str, Any] | Any,
    *,
    login_session_id: str,
    directory_path: str,
) -> dict[str, Any]:
    normalized = _jsonable(session)
    if not isinstance(normalized, Mapping):
        raise ValueError("Teams auth login did not return a device-code session.")
    metadata = normalized.get("metadata")
    metadata_payload = metadata if isinstance(metadata, Mapping) else {}
    payload: dict[str, Any] = {
        "login_session_id": login_session_id,
        "directory_path": directory_path,
        "status": "device_code_pending",
        "authenticated": False,
        "tenant_id": normalized.get("tenant_id"),
        "scopes": normalized.get("scopes"),
        "message": normalized.get("message"),
        "verification_uri": normalized.get("verification_uri"),
        "verification_uri_complete": normalized.get("verification_uri_complete"),
        "user_code": normalized.get("user_code"),
        "interval_seconds": normalized.get("interval_seconds"),
        "created_at": normalized.get("created_at"),
        "expires_at": normalized.get("expires_at"),
    }
    identity_id = metadata_payload.get("identity_id")
    if identity_id is not None and str(identity_id).strip():
        payload["identity_id"] = str(identity_id).strip()
    return payload


def _sanitized_result_payload(
    value: Any,
    *,
    default_directory_path: str | None = None,
    default_status: str | None = None,
    default_authenticated: bool | None = None,
) -> dict[str, Any]:
    sanitized = _sanitize_teams_auth_payload(value)
    if not isinstance(sanitized, dict):
        sanitized = {"result": sanitized}
    if default_directory_path is not None and not sanitized.get("directory_path"):
        sanitized["directory_path"] = default_directory_path
    if default_status is not None and not sanitized.get("status"):
        sanitized["status"] = default_status
    if default_authenticated is not None and "authenticated" not in sanitized:
        sanitized["authenticated"] = default_authenticated
    return sanitized


def _teams_auth_polling_payload(
    session: DeviceCodeLoginSession | Mapping[str, Any] | Any,
    *,
    login_session_id: str,
    directory_path: str,
    message: str,
) -> dict[str, Any]:
    payload = _login_session_payload(
        session, login_session_id=login_session_id, directory_path=directory_path
    )
    payload["status"] = "device_code_pending"
    payload["polling"] = True
    payload["error"] = message
    return payload


def teams_auth_routes(service: AdminRuntimeComponents, auth: AdminWebAuth) -> list[BaseRoute]:
    login_sessions: dict[str, DeviceCodeLoginSession | Mapping[str, Any] | Any] = {}
    lock = RLock()

    def operation(request, directory, name, legacy_name):
        ref = request.query_params.get("credential_ref")
        if ref is None:
            return getattr(service.operations_admin, legacy_name), {}
        auth.require_permission(request, ADMIN_OWNER_PERMISSION, directory_path=directory)
        return getattr(service.teams_admin, name), {"credential_ref": ref}

    async def status(request: Request) -> JSONResponse:
        auth.require(request)
        directory_path = _directory_path_from_request(service, request)
        try:
            method, options = operation(request, directory_path, "status", "teams_auth_status")
            result = await run_in_threadpool(method, directory=directory_path, **options)
        except PermissionError:
            return json_error(
                "Teams credential management requires directory owner access", status_code=403
            )
        except (ValueError, TeamsAuthError) as exc:
            return json_error(str(exc), status_code=400)
        return JSONResponse(
            _sanitized_result_payload(
                result,
                default_directory_path=directory_path,
                default_status="reauth-required",
                default_authenticated=False,
            )
        )

    async def identities(request: Request) -> JSONResponse:
        auth.require(request)
        try:
            result = await run_in_threadpool(service.operations_admin.teams_auth_list)
        except ValueError as exc:
            return json_error(str(exc), status_code=400)
        return JSONResponse({"items": _sanitize_teams_auth_payload(result)})

    async def login(request: Request) -> JSONResponse:
        auth.require(request)
        directory_path = _directory_path_from_request(service, request)
        if not request_has_privileged_operations(
            auth=auth,
            service=service,
            request=request,
            required_permission=ADMIN_OWNER_PERMISSION,
            directory_path=directory_path,
        ):
            return json_error("Privileged operations are locked.", status_code=403)
        try:
            method, options = operation(request, directory_path, "login", "teams_auth_login")
            session = await run_in_threadpool(method, directory=directory_path, **options)
        except PermissionError:
            return json_error(
                "Teams credential management requires directory owner access", status_code=403
            )
        except ValueError as exc:
            return json_error(str(exc), status_code=400)
        except TeamsAuthError as exc:
            return json_error(str(exc), status_code=400)
        login_session_id = uuid4().hex
        with lock:
            login_sessions[login_session_id] = session
        return JSONResponse(
            _login_session_payload(
                session, login_session_id=login_session_id, directory_path=directory_path
            )
        )

    async def complete(request: Request) -> JSONResponse:
        auth.require(request)
        directory_path = _directory_path_from_request(service, request)
        if not request_has_privileged_operations(
            auth=auth,
            service=service,
            request=request,
            required_permission=ADMIN_OWNER_PERMISSION,
            directory_path=directory_path,
        ):
            return json_error("Privileged operations are locked.", status_code=403)
        try:
            payload = await request.json()
        except Exception:
            return json_error("Request body must be valid JSON")
        if not isinstance(payload, dict):
            return json_error("Request body must be a JSON object")
        login_session_id = str(payload.get("login_session_id") or "").strip()
        if not login_session_id:
            return json_error("login_session_id is required")
        timeout_seconds_raw = payload.get("timeout_seconds")
        timeout_seconds: float | None
        if timeout_seconds_raw is None or str(timeout_seconds_raw).strip() == "":
            timeout_seconds = _DEFAULT_COMPLETE_TIMEOUT_SECONDS
        else:
            try:
                timeout_seconds = float(timeout_seconds_raw)
            except (TypeError, ValueError):
                return json_error("timeout_seconds must be a number")
            if timeout_seconds <= 0:
                return json_error("timeout_seconds must be positive")
        with lock:
            login_session = login_sessions.get(login_session_id)
        if login_session is None:
            return json_error("Unknown login_session_id", status_code=404)
        try:
            method, options = operation(
                request, directory_path, "complete_login", "teams_auth_complete_login"
            )
            result = await run_in_threadpool(
                method,
                login_session,
                directory=directory_path,
                timeout_seconds=timeout_seconds,
                **options,
            )
        except PermissionError:
            return json_error(
                "Teams credential management requires directory owner access", status_code=403
            )
        except (AuthorizationPendingError, SlowDownError, DeviceCodePollingTimeoutError) as exc:
            return JSONResponse(
                _teams_auth_polling_payload(
                    login_session,
                    login_session_id=login_session_id,
                    directory_path=directory_path,
                    message=str(exc),
                ),
                status_code=202,
            )
        except DeviceCodeExpiredError as exc:
            return json_error(str(exc), status_code=400)
        except TeamsAuthError as exc:
            error_code = str(getattr(exc, "error_code", "") or "").strip().casefold()
            if error_code in {"authorization_pending", "slow_down"}:
                return JSONResponse(
                    _teams_auth_polling_payload(
                        login_session,
                        login_session_id=login_session_id,
                        directory_path=directory_path,
                        message=str(exc),
                    ),
                    status_code=202,
                )
            return json_error(str(exc), status_code=400)
        except ValueError as exc:
            return json_error(str(exc), status_code=400)
        with lock:
            login_sessions.pop(login_session_id, None)
        return JSONResponse(
            _sanitized_result_payload(
                result,
                default_directory_path=directory_path,
                default_status="authenticated",
                default_authenticated=True,
            )
        )

    async def logout(request: Request) -> JSONResponse:
        auth.require(request)
        directory_path = _directory_path_from_request(service, request)
        if not request_has_privileged_operations(
            auth=auth,
            service=service,
            request=request,
            required_permission=ADMIN_OWNER_PERMISSION,
            directory_path=directory_path,
        ):
            return json_error("Privileged operations are locked.", status_code=403)
        try:
            method, options = operation(request, directory_path, "logout", "teams_auth_logout")
            result = await run_in_threadpool(method, directory=directory_path, **options)
        except PermissionError:
            return json_error(
                "Teams credential management requires directory owner access", status_code=403
            )
        except (ValueError, TeamsAuthError) as exc:
            return json_error(str(exc), status_code=400)
        return JSONResponse(
            _sanitized_result_payload(
                result,
                default_directory_path=directory_path,
                default_status="logged_out",
                default_authenticated=False,
            )
        )

    async def channels(request: Request) -> JSONResponse:
        auth.require(request)
        directory_path = _directory_path_from_request(service, request)
        team_id = str(request.query_params.get("team_id") or "").strip()
        if not team_id:
            return json_error("team_id is required")
        channel_name = str(request.query_params.get("channel_name") or "").strip() or None
        include_all_raw = request.query_params.get("include_all")
        try:
            include_all = _bool_query_param(include_all_raw, default=False)
        except ValueError as exc:
            return json_error(str(exc))
        try:
            result = await run_in_threadpool(
                service.operations_admin.teams_auth_list_channels,
                directory=directory_path,
                team_id=team_id,
                channel_name=channel_name,
                include_all=include_all,
            )
        except ValueError as exc:
            return json_error(str(exc), status_code=400)
        return JSONResponse(_sanitize_teams_auth_payload(result))

    return [
        Route("/api/teams-auth/status", status, methods=["GET"]),
        Route("/api/teams-auth/identities", identities, methods=["GET"]),
        Route("/api/teams-auth/login", login, methods=["POST"]),
        Route("/api/teams-auth/complete", complete, methods=["POST"]),
        Route("/api/teams-auth/logout", logout, methods=["POST"]),
        Route("/api/teams-auth/channels", channels, methods=["GET"]),
    ]


__all__ = ["teams_auth_routes"]

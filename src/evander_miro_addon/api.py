from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from uuid import uuid4

from pydantic import ValidationError
from starlette.responses import JSONResponse, RedirectResponse
from starlette.routing import Route

from evander.admin.web.rbac import ADMIN_OWNER_PERMISSION, WORKFLOWS_READ_PERMISSION
from evander.admin.web.shared import request_has_privileged_operations
from evander.repositories.session_manifests import JsonSessionManifestRepository

from .auth import MiroOAuth
from .client import MiroClient
from .models import Connection, MiroError, in_directory
from .permissions import board_reference, session_instance
from .runtime import directory
from .service import Access, MiroService
from .state import MiroStore


async def body(request) -> dict:
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > 1_100_000:
            raise MiroError("invalid_input", "Request body is too large")
    try:
        value = json.loads(raw)
    except ValueError:
        raise MiroError("invalid_input", "Request must contain a JSON object") from None
    if not isinstance(value, dict):
        raise MiroError("invalid_input", "Request must contain a JSON object")
    return value


def admin_api_routes(context):
    def store():
        return MiroStore(context.runtime_dir)

    def scope(request):
        return directory(request.query_params.get("directory_path", "root"))

    def actor(request):
        principal = context.auth.require(request)
        return str(principal.get("login") or principal.get("sub"))

    def service(request):
        return MiroService(
            store(), Access(scope(request), "operator:" + actor(request), admin=True)
        )

    def owned(request):
        record = store().resolve_connection(scope(request), request.path_params["connection_id"])
        if record["config"]["directory_path"] != scope(request):
            raise MiroError("forbidden", "Manage inherited connections from their owning directory")
        return record

    def audit(request, action, record):
        store().put(
            "audit",
            uuid4().hex,
            {
                "action": action,
                "actor": actor(request),
                "directory_path": scope(request),
                "connection_key": record["key"],
                "at": time.time(),
            },
        )

    def public(record, repository):
        secret = repository.get("credentials", record["key"])
        return {
            "config": Connection.model_validate(record["config"]).model_dump(),
            "revision": record["revision"],
            "connected": bool(secret and secret["generation"] == record["generation"]),
            "calls_today": repository.usage(record),
            "capabilities": repository.get("capabilities", record["key"]),
        }

    async def connections(request):
        repository = store()
        if request.method == "GET":
            candidates = [
                record
                for record in repository.list("connection")
                if in_directory(record["config"]["directory_path"], scope(request))
            ]
            effective = {}
            for record in sorted(candidates, key=lambda r: len(r["config"]["directory_path"])):
                effective[record["config"]["id"]] = public(record, repository)
            return JSONResponse({"connections": list(effective.values())})
        payload = await body(request)
        config = Connection.model_validate(payload["config"])
        if config.directory_path != scope(request):
            raise MiroError("forbidden", "Connection directory must match the selected directory")
        record = repository.save_connection(config, int(payload["expected_revision"]))
        audit(request, "save_connection", record)
        return JSONResponse(public(record, repository))

    def sessions():
        runtime = context.service
        repository = getattr(runtime, "session_repository", None)
        if repository is None:
            repository = getattr(getattr(runtime, "runtime", None), "session_repository", None)
        return repository or JsonSessionManifestRepository(context.runtime_dir / "sessions.json")

    def public_grant(grant):
        return {k: v for k, v in grant.items() if k != "connection_key"}

    def target_session(request, session_id):
        session = sessions().get_session(session_id)
        if (
            session is None
            or not in_directory(scope(request), session.directory_path)
            or session.expires_at <= datetime.now(UTC)
        ):
            raise MiroError("forbidden", "Session is unavailable in this directory")
        return session

    async def grants(request):
        repository = store()
        record = repository.resolve_connection(scope(request), request.path_params["connection_id"])
        if request.method == "POST":
            payload = await body(request)
            session = target_session(request, payload["session_id"])
            if not payload.get("session_created_at"):
                raise MiroError("invalid_input", "Select the exact session instance to approve")
            if payload["session_created_at"] != session_instance(session):
                raise MiroError("conflict", "Session was replaced; reload before granting access")
            effective = repository.resolve_connection(
                session.directory_path, record["config"]["id"]
            )
            if effective["key"] != record["key"] or not effective["config"]["enabled"]:
                raise MiroError("forbidden", "Connection is unavailable to this session")
            grant = repository.save_board_grant(
                record,
                session,
                board_reference(payload["board_id"]),
                payload["access"],
                actor="operator:" + actor(request),
                source={"provider": "admin"},
                expected_revision=int(payload["expected_revision"]),
            )
            return JSONResponse(public_grant(grant))
        available = []
        for session in sessions().list_sessions():
            if not in_directory(
                scope(request), session.directory_path
            ) or session.expires_at <= datetime.now(UTC):
                continue
            try:
                effective = repository.resolve_connection(
                    session.directory_path, record["config"]["id"]
                )
            except MiroError:
                continue
            if effective["key"] == record["key"] and effective["config"]["enabled"]:
                available.append(
                    {
                        "session_id": session.session_id,
                        "directory_path": session.directory_path,
                        "created_at": session_instance(session),
                    }
                )
        valid = {(s["session_id"], s["created_at"]) for s in available}
        rows = [
            public_grant(g) | {"session_valid": (g["session_id"], g["session_created_at"]) in valid}
            for g in repository.list("board-grant")
            if g["connection_key"] == record["key"]
            and in_directory(scope(request), g["directory_path"])
        ]
        return JSONResponse({"grants": rows, "sessions": available})

    async def revoke_grant(request):
        repository = store()
        record = repository.resolve_connection(scope(request), request.path_params["connection_id"])
        grant = repository.get("board-grant", request.path_params["grant_id"])
        if (
            not grant
            or grant["connection_key"] != record["key"]
            or not in_directory(scope(request), grant["directory_path"])
        ):
            raise MiroError("forbidden", "Board grant is unavailable in this directory")
        payload = await body(request)
        revoked = repository.revoke_board_grant(
            grant["grant_id"],
            expected_revision=int(payload["expected_revision"]),
            actor="operator:" + actor(request),
        )
        return JSONResponse(public_grant(revoked))

    async def connect(request):
        record = owned(request)
        # Reauthorization invalidates pending callbacks and refreshes tied to an old account.
        store().disconnect(record)
        record = store().get("connection", record["key"])
        result = await MiroOAuth(store()).begin(record, actor(request))
        audit(request, "begin_authorization", record)
        return JSONResponse(result, headers={"Cache-Control": "no-store"})

    async def disconnect(request):
        record = owned(request)
        store().disconnect(record)
        audit(request, "disconnect", record)
        return JSONResponse({"status": "disconnected"})

    async def callback(request):
        code, state = request.query_params.get("code"), request.query_params.get("state")
        if not code or not state or request.query_params.get("error"):
            raise MiroError("authentication_failed", "Miro authorization was cancelled or rejected")
        # Authorize against the directory bound at initiation, not a caller-provided query.
        from .state import digest

        pending = store().get("oauth_pending", digest(state))
        if not pending:
            raise MiroError("invalid_callback", "Miro authorization expired or was already used")
        saved = store().unseal(pending["encrypted"])
        context.auth.require_permission(
            request,
            ADMIN_OWNER_PERMISSION,
            directory_path=saved["record"]["config"]["directory_path"],
        )
        result = await MiroOAuth(store()).complete(state, code, actor(request))
        from urllib.parse import urlencode

        response = RedirectResponse(
            "/addons/miro-addon/?"
            + urlencode(
                {
                    "directory_path": result["directory_path"],
                    "connected": "true",
                }
            ),
            status_code=303,
        )
        response.headers.update({"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
        return response

    async def health(request):
        record = service(request).authorize(request.path_params["connection_id"])
        async with MiroClient(store(), record).open() as client:
            return JSONResponse({"status": "connected", "tools": sorted(client.schemas)})

    async def activity(request):
        connection_id = request.path_params["connection_id"]
        runtime = service(request)
        record = runtime.authorize(connection_id)
        operations = [
            entry
            for entry in store().list("operation")
            if entry["connection_key"] == record["key"]
            and in_directory(scope(request), entry["directory_path"])
        ]
        operations.sort(key=lambda item: item["started_at"], reverse=True)
        subscriptions = [
            item
            for item in store().list("subscription")
            if item["connection_key"] == record["key"]
            and in_directory(scope(request), item["directory_path"])
        ]
        return JSONResponse(
            {
                "operations": [
                    runtime.saved_operation(record, entry["operation_id"])
                    for entry in operations[:100]
                ],
                "subscriptions": subscriptions,
            }
        )

    async def snapshot(request):
        runtime = service(request)
        record = runtime.authorize(request.path_params["connection_id"])
        return JSONResponse(runtime.saved_snapshot(record, request.path_params["snapshot_id"]))

    async def reconcile(request):
        return JSONResponse(
            await service(request).reconcile(
                request.path_params["connection_id"], request.path_params["operation_id"]
            )
        )

    async def resolve(request):
        payload = await body(request)
        result = await service(request).resolve_operation(
            request.path_params["connection_id"],
            request.path_params["operation_id"],
            str(payload.get("outcome", "")),
            str(payload.get("evidence", "")),
        )
        return JSONResponse(result)

    def guarded(handler, *, oauth_callback=False):
        async def route(request):
            if context.auth is None:
                return JSONResponse(
                    {"error": "Admin authentication is unavailable"}, status_code=503
                )
            try:
                if oauth_callback:
                    context.auth.require(request)
                else:
                    permission = (
                        ADMIN_OWNER_PERMISSION
                        if request.method != "GET"
                        else WORKFLOWS_READ_PERMISSION
                    )
                    context.auth.require_permission(
                        request, permission, directory_path=scope(request)
                    )
                    if request.method != "GET" and not request_has_privileged_operations(
                        auth=context.auth,
                        service=context.service,
                        request=request,
                        required_permission=permission,
                        directory_path=scope(request),
                    ):
                        raise MiroError("forbidden", "Privileged operations are locked")
                return await handler(request)
            except PermissionError:
                return JSONResponse({"error": "Permission denied"}, status_code=403)
            except MiroError as exc:
                status = (
                    403
                    if exc.code == "forbidden"
                    else 409
                    if exc.code in {"conflict", "busy"}
                    else 400
                )
                return JSONResponse(
                    {"error": str(exc), "code": exc.code},
                    status_code=status,
                    headers={"Cache-Control": "no-store"},
                )
            except (ValidationError, KeyError, TypeError, ValueError):
                return JSONResponse(
                    {"error": "Invalid Miro configuration or request"}, status_code=400
                )

        return route

    return [
        Route("/connections", guarded(connections), methods=["GET", "POST"]),
        Route(
            "/connections/{connection_id}/session-grants", guarded(grants), methods=["GET", "POST"]
        ),
        Route(
            "/connections/{connection_id}/session-grants/{grant_id}/revoke",
            guarded(revoke_grant),
            methods=["POST"],
        ),
        Route("/oauth/callback", guarded(callback, oauth_callback=True), methods=["GET"]),
        Route("/connections/{connection_id}/connect", guarded(connect), methods=["POST"]),
        Route("/connections/{connection_id}/disconnect", guarded(disconnect), methods=["POST"]),
        Route("/connections/{connection_id}/health", guarded(health), methods=["POST"]),
        Route("/connections/{connection_id}/activity", guarded(activity), methods=["GET"]),
        Route(
            "/connections/{connection_id}/snapshots/{snapshot_id}",
            guarded(snapshot),
            methods=["GET"],
        ),
        Route(
            "/connections/{connection_id}/operations/{operation_id}/reconcile",
            guarded(reconcile),
            methods=["POST"],
        ),
        Route(
            "/connections/{connection_id}/operations/{operation_id}/resolve",
            guarded(resolve),
            methods=["POST"],
        ),
    ]

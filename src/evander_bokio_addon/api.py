from __future__ import annotations

from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from evander.addons import AddonContext
from evander.admin.web.rbac import ADMIN_OWNER_PERMISSION
from evander.admin.web.shared import request_has_privileged_operations

from .client import BokioApiError, BokioClient, BokioConfigError, settings_from_context
from .evidence import normalize_stored_evidence_ids
from .execution_service import execute_prepared_action as _execute_prepared_action
from .operations import (
    BokioOperationStore,
    bokio_error_payload,
)
from .state import PreparedActionStore, upload_staging_dir


def admin_api_routes(context: AddonContext) -> list[Route]:
    async def health(request: Request) -> JSONResponse:
        settings = settings_from_context(context)
        payload: dict[str, Any] = {
            "configured": bool(settings.company_id and settings.integration_token),
            "settings": settings.redacted_payload(),
            "live_check": False,
        }
        if str(request.query_params.get("check") or "").lower() in {"1", "true", "yes"}:
            payload["live_check"] = True
            try:
                payload["company"] = _client(context).get_company()
                payload["status"] = "ok"
            except Exception as exc:
                return _error_response(exc)
        else:
            payload["status"] = "configured" if payload["configured"] else "missing_config"
        return JSONResponse(payload)

    async def company(_: Request) -> JSONResponse:
        return _json(lambda: _client(context).get_company())

    async def accounts(request: Request) -> JSONResponse:
        return _json(
            lambda: _client(context).list_accounts(
                query=str(request.query_params.get("query") or "").strip() or None
            )
        )

    async def journal_entries(request: Request) -> JSONResponse:
        return _json(lambda: _client(context).list_journal_entries(**_page_args(request)))

    async def invoices(request: Request) -> JSONResponse:
        return _json(lambda: _client(context).list_invoices(**_page_args(request)))

    async def supplier_invoices(request: Request) -> JSONResponse:
        return _json(lambda: _client(context).list_supplier_invoices(**_page_args(request)))

    async def uploads(request: Request) -> JSONResponse:
        if request.method == "GET":
            return _json(lambda: _client(context).list_uploads(**_page_args(request)))
        payload = await _json_body(request)
        upload_root = upload_staging_dir(context.runtime_dir, scope="admin")
        upload_root.mkdir(parents=True, exist_ok=True)
        return _json(
            lambda: _client(context).upload_document(
                file_path=_required(payload.get("file_path"), "file_path"),
                allowed_roots=[upload_root],
                description=_optional(payload.get("description")),
                journal_entry_id=_optional(
                    payload.get("journalEntryId") or payload.get("journal_entry_id")
                ),
            )
        )

    async def bank_payments(request: Request) -> JSONResponse:
        if request.method == "GET":
            return _json(lambda: _client(context).list_bank_payments(**_page_args(request)))
        payload = await _json_body(request)
        return _json(lambda: _client(context).create_bank_payment(payload))

    async def prepared_actions(request: Request) -> JSONResponse:
        store = PreparedActionStore(context.runtime_dir)
        if request.method == "GET":
            return JSONResponse({"actions": store.list_actions()})
        payload = await _json_body(request)
        kind = _required(payload.get("kind"), "kind")
        evidence_ids = normalize_stored_evidence_ids(
            context.runtime_dir,
            payload.get("evidence_ids"),
        )
        action = store.create_action(
            kind=kind,
            payload=_object(payload.get("payload"), "payload"),
            evidence_ids=evidence_ids,
            source=_optional(payload.get("source")) or "admin",
            note=_optional(payload.get("note")),
        )
        return JSONResponse({"action": action})

    async def operations(request: Request) -> JSONResponse:
        return JSONResponse(
            {
                "operations": BokioOperationStore(context.runtime_dir).list(
                    status=_optional(request.query_params.get("status")),
                    operation_kind=_optional(request.query_params.get("operation_kind")),
                    limit=int(request.query_params.get("limit") or 100),
                )
            }
        )

    async def approve_action(request: Request) -> JSONResponse:
        action_id = request.path_params["action_id"]
        action = PreparedActionStore(context.runtime_dir).approve_action(action_id)
        return JSONResponse({"action": action})

    async def reject_action(request: Request) -> JSONResponse:
        action_id = request.path_params["action_id"]
        payload = await _json_body(request, default={})
        action = PreparedActionStore(context.runtime_dir).reject_action(
            action_id,
            reason=_optional(payload.get("reason")),
        )
        return JSONResponse({"action": action})

    async def execute_action(request: Request) -> JSONResponse:
        action_id = request.path_params["action_id"]
        return _json(lambda: _execute_prepared_action(context, action_id))

    async def approve_and_execute_action(request: Request) -> JSONResponse:
        action_id = request.path_params["action_id"]
        return _json(lambda: _execute_prepared_action(context, action_id, approve=True))

    async def retry_action(request: Request) -> JSONResponse:
        action_id = request.path_params["action_id"]
        payload = await _json_body(request, default={})
        confirm_not_created = payload.get("confirm_not_created", False)
        if not isinstance(confirm_not_created, bool):
            raise ValueError("confirm_not_created must be a boolean")
        return _json(
            lambda: _execute_prepared_action(
                context,
                action_id,
                retry=True,
                confirm_not_created=confirm_not_created,
            )
        )

    async def sie_download(request: Request) -> Response:
        fiscal_year_id = _required(request.path_params.get("fiscal_year_id"), "fiscal_year_id")
        try:
            content = _client(context).download_sie(fiscal_year_id)
        except Exception as exc:
            return _error_response(exc)
        return Response(
            content,
            media_type="application/octet-stream",
            headers={"content-disposition": f'attachment; filename="bokio-{fiscal_year_id}.se"'},
        )

    route_specs = [
        ("/health", health, ["GET"]),
        ("/company", company, ["GET"]),
        ("/accounts", accounts, ["GET"]),
        ("/journal-entries", journal_entries, ["GET"]),
        ("/invoices", invoices, ["GET"]),
        ("/supplier-invoices", supplier_invoices, ["GET"]),
        ("/uploads", uploads, ["GET", "POST"]),
        ("/bank-payments", bank_payments, ["GET", "POST"]),
        ("/sie/{fiscal_year_id}/download", sie_download, ["GET"]),
        ("/prepared-actions", prepared_actions, ["GET", "POST"]),
        ("/operations", operations, ["GET"]),
        ("/prepared-actions/{action_id}/approve", approve_action, ["POST"]),
        ("/prepared-actions/{action_id}/reject", reject_action, ["POST"]),
        ("/prepared-actions/{action_id}/execute", execute_action, ["POST"]),
        (
            "/prepared-actions/{action_id}/approve-and-execute",
            approve_and_execute_action,
            ["POST"],
        ),
        ("/prepared-actions/{action_id}/retry", retry_action, ["POST"]),
    ]
    return [
        Route(
            path,
            _with_admin_guard(
                context, handler, mutating_methods={"POST", "PUT", "PATCH", "DELETE"}
            ),
            methods=methods,
        )
        for path, handler, methods in route_specs
    ]


def _client(context: AddonContext) -> BokioClient:
    return BokioClient.from_context(context)


def _json(producer: Any) -> JSONResponse:
    try:
        return JSONResponse({"result": producer()})
    except Exception as exc:
        return _error_response(exc)


async def _json_body(request: Request, *, default: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception:
        if default is not None:
            return default
        raise ValueError("Request body must be valid JSON")
    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object")
    return payload


def _error_response(exc: Exception) -> JSONResponse:
    status_code = 400
    payload = _exception_payload(exc)
    if isinstance(exc, BokioApiError):
        status_code = exc.status_code or 502
    elif isinstance(exc, BokioConfigError):
        status_code = 503
    elif isinstance(exc, FileNotFoundError):
        status_code = 404
    return JSONResponse(payload, status_code=status_code)


def _exception_payload(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, BokioApiError):
        return bokio_error_payload(exc)
    return {"error": str(exc), "error_class": exc.__class__.__name__}


def _page_args(request: Request) -> dict[str, Any]:
    return {
        "page": int(request.query_params.get("page") or 1),
        "page_size": int(
            request.query_params.get("pageSize") or request.query_params.get("page_size") or 25
        ),
        "query": str(request.query_params.get("query") or "").strip() or None,
    }


def _required(value: Any, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field_name} is required")
    return normalized


def _optional(value: Any) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _object(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field_name} must be a JSON object")
    return dict(value)


def _with_admin_guard(context: AddonContext, handler: Any, *, mutating_methods: set[str]):
    async def guarded(request: Request) -> Response:
        auth = context.auth
        if auth is None:
            return JSONResponse({"error": "Admin authentication is unavailable"}, status_code=503)
        try:
            if request.method.upper() in mutating_methods:
                auth.require_permission(request, ADMIN_OWNER_PERMISSION)
            else:
                auth.require(request)
        except PermissionError as exc:
            return JSONResponse({"error": str(exc)}, status_code=403)
        if request.method.upper() in mutating_methods and not request_has_privileged_operations(
            auth=auth,
            service=context.service,
            request=request,
            required_permission=ADMIN_OWNER_PERMISSION,
        ):
            return JSONResponse({"error": "Privileged operations are locked."}, status_code=403)
        try:
            return await handler(request)
        except Exception as exc:
            return _error_response(exc)

    return guarded

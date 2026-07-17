from __future__ import annotations

from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from evander.addons import AddonContext

from .client import BokioApiError, BokioClient, BokioConfigError, settings_from_context
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
        action = store.create_action(
            kind=_required(payload.get("kind"), "kind"),
            payload=_object(payload.get("payload"), "payload"),
            source=_optional(payload.get("source")) or "admin",
            note=_optional(payload.get("note")),
        )
        return JSONResponse({"action": action})

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

    return [
        Route("/health", health, methods=["GET"]),
        Route("/company", company, methods=["GET"]),
        Route("/accounts", accounts, methods=["GET"]),
        Route("/journal-entries", journal_entries, methods=["GET"]),
        Route("/invoices", invoices, methods=["GET"]),
        Route("/supplier-invoices", supplier_invoices, methods=["GET"]),
        Route("/uploads", uploads, methods=["GET", "POST"]),
        Route("/bank-payments", bank_payments, methods=["GET", "POST"]),
        Route("/sie/{fiscal_year_id}/download", sie_download, methods=["GET"]),
        Route("/prepared-actions", prepared_actions, methods=["GET", "POST"]),
        Route("/prepared-actions/{action_id}/approve", approve_action, methods=["POST"]),
        Route("/prepared-actions/{action_id}/reject", reject_action, methods=["POST"]),
        Route("/prepared-actions/{action_id}/execute", execute_action, methods=["POST"]),
    ]


def _client(context: AddonContext) -> BokioClient:
    return BokioClient.from_context(context)


def _execute_prepared_action(context: AddonContext, action_id: str) -> dict[str, Any]:
    store = PreparedActionStore(context.runtime_dir)
    action = store.get_action(action_id)
    if action is None:
        raise ValueError(f"Unknown Bokio prepared action: {action_id}")
    if action.get("status") != "approved":
        raise ValueError(f"Bokio prepared action {action_id} must be approved before execution")
    kind = str(action.get("kind") or "").strip()
    payload = _object(action.get("payload"), "payload")
    client = _client(context)
    try:
        if kind == "journal_entry":
            result = client.create_journal_entry(payload)
        elif kind == "supplier_invoice":
            result = client.create_supplier_invoice(payload)
        elif kind == "invoice":
            result = client.create_invoice(payload)
        elif kind == "bank_payment":
            result = client.create_bank_payment(payload)
        else:
            raise ValueError(f"Unsupported Bokio prepared action kind: {kind}")
    except Exception as exc:
        store.mark_failed(action_id, error=_exception_payload(exc))
        raise
    action = store.mark_executed(action_id, result=result)
    return {"action": action, "result": result}


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
        return exc.to_payload()
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

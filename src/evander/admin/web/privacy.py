"""Authenticated operator adapters for the privacy domain service.

This module intentionally has no store or filesystem mutation logic. A configured
web identity is required even when other local-development pages allow auth=none.
"""

from __future__ import annotations

import json

from starlette.requests import Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse, Response
from starlette.routing import BaseRoute, Route

from evander.admin.composition import AdminRuntimeComponents
from evander.admin.web.rbac import (
    ADMIN_OWNER_PERMISSION,
    PRIVACY_APPROVE_PERMISSION,
    PRIVACY_CASES_PERMISSION,
    PRIVACY_CONFIGURE_PERMISSION,
    PRIVACY_EXPORT_PERMISSION,
    PRIVACY_READ_PERMISSION,
)
from evander.admin.web.shared import AdminWebAuth, request_has_privileged_operations
from evander.privacy.models import PrivacyConflict, PrivacyError, PrivacyUnsupported


ACTION_PERMISSIONS = {
    "configure": PRIVACY_CONFIGURE_PERMISSION,
    "retention.preview": PRIVACY_CONFIGURE_PERMISSION,
    "jobs.approve": PRIVACY_APPROVE_PERMISSION,
    "jobs.cancel": PRIVACY_APPROVE_PERMISSION,
    "jobs.run": PRIVACY_CONFIGURE_PERMISSION,
    "holds.create": PRIVACY_APPROVE_PERMISSION,
    "holds.release": PRIVACY_APPROVE_PERMISSION,
    "cases.create": PRIVACY_CASES_PERMISSION,
    "cases.locate": PRIVACY_CASES_PERMISSION,
    "cases.preview": PRIVACY_CASES_PERMISSION,
    "cases.update": PRIVACY_CASES_PERMISSION,
    "followups.create": PRIVACY_CASES_PERMISSION,
    "followups.update": PRIVACY_CASES_PERMISSION,
    "incidents.create": PRIVACY_CASES_PERMISSION,
    "incidents.update": PRIVACY_CASES_PERMISSION,
    "exports.release": PRIVACY_APPROVE_PERMISSION,
}

_PRIVATE_HEADERS = {"Cache-Control": "no-store", "Pragma": "no-cache"}


def _response(value: dict, status: int = 200) -> JSONResponse:
    return JSONResponse(value, status_code=status, headers=_PRIVATE_HEADERS)


def privacy_routes(service: AdminRuntimeComponents, auth: AdminWebAuth) -> list[BaseRoute]:
    def authorize(request: Request, permission: str, *, mutation: bool = False) -> tuple[str, str]:
        if auth.config.auth_mode == "none":
            raise PermissionError("Privacy administration requires authenticated web access")
        session = auth.require(request)
        actor = str(session.get("login") or session.get("sub") or "").strip()
        if not actor:
            raise PermissionError("An authenticated operator identity is required")
        scope = service.teams_admin.normalize_directory_reference(
            request.query_params.get("directory_path") or "root"
        )
        auth.require_permission(request, permission, directory_path=scope)
        if scope == "root":
            auth.require_permission(request, ADMIN_OWNER_PERMISSION, directory_path="root")
        if mutation and not request_has_privileged_operations(
            auth=auth, service=service, request=request
        ):
            raise PermissionError("Privileged operations are locked.")
        return scope, actor

    async def snapshot(request: Request) -> JSONResponse:
        try:
            scope, _ = authorize(request, PRIVACY_READ_PERMISSION)
            return _response(
                {
                    **await run_in_threadpool(service.privacy_admin.snapshot, scope),
                    "permissions": [
                        permission
                        for permission in sorted(
                            {
                                PRIVACY_READ_PERMISSION,
                                PRIVACY_CONFIGURE_PERMISSION,
                                PRIVACY_CASES_PERMISSION,
                                PRIVACY_APPROVE_PERMISSION,
                                PRIVACY_EXPORT_PERMISSION,
                            }
                        )
                        if auth.can(request, permission, directory_path=scope)
                    ],
                }
            )
        except PermissionError as exc:
            return _response({"error": str(exc)}, 403)
        except (PrivacyError, ValueError) as exc:
            return _response({"error": str(exc)}, 400)
        except Exception:
            return _response(
                {"error": "Privacy operation failed; inspect operator diagnostics"}, 500
            )

    async def mutate(request: Request) -> JSONResponse:
        try:
            action = str(request.path_params["action"])
            permission = ACTION_PERMISSIONS.get(action)
            if permission is None:
                return _response({"error": "Unknown privacy action"}, 404)
            scope, actor = authorize(request, permission, mutation=True)
            raw = bytearray()
            async for chunk in request.stream():
                raw.extend(chunk)
                if len(raw) > 256 * 1024:
                    return _response({"error": "Privacy request exceeds 256 KiB"}, 413)
            try:
                body = json.loads(raw)
            except (ValueError, UnicodeDecodeError):
                return _response({"error": "Request body must be valid JSON"}, 400)
            if not isinstance(body, dict) or not isinstance(body.get("payload", {}), dict):
                return _response({"error": "Request and payload must be JSON objects"}, 400)
            revision = body.get("expected_revision")
            if not isinstance(revision, str) or not revision.strip():
                return _response({"error": "expected_revision is required"}, 428)
            payload = body.get("payload", {})
            if action == "exports.release" or (
                action == "cases.preview" and payload.get("operation") == "export"
            ):
                auth.require_permission(request, PRIVACY_EXPORT_PERMISSION, directory_path=scope)
            return _response(
                await run_in_threadpool(
                    service.privacy_admin.mutate,
                    action,
                    scope,
                    payload,
                    expected_revision=revision,
                    actor=actor,
                )
            )
        except PermissionError as exc:
            return _response({"error": str(exc)}, 403)
        except PrivacyConflict as exc:
            return _response({"error": str(exc)}, 409)
        except PrivacyUnsupported as exc:
            return _response({"error": str(exc), "unsupported": True}, 422)
        except (PrivacyError, ValueError) as exc:
            return _response({"error": str(exc)}, 400)
        except Exception:
            return _response(
                {"error": "Privacy operation failed; inspect operator diagnostics"}, 500
            )

    async def export_review(request: Request) -> Response:
        """Download for an authorized reviewer, never a public subject-delivery URL."""
        try:
            scope, actor = authorize(request, PRIVACY_EXPORT_PERMISSION)
            auth.require_permission(request, PRIVACY_CASES_PERMISSION, directory_path=scope)
            content = await run_in_threadpool(
                service.privacy_admin.export_bytes,
                scope,
                str(request.path_params["job_id"]),
                actor=actor,
            )
            return Response(
                content,
                media_type="application/json",
                headers={
                    **_PRIVATE_HEADERS,
                    "Content-Disposition": 'attachment; filename="evander-privacy-review.json"',
                    "X-Content-Type-Options": "nosniff",
                },
            )
        except PermissionError as exc:
            return _response({"error": str(exc)}, 403)
        except PrivacyError as exc:
            return _response({"error": str(exc)}, 400)
        except Exception:
            return _response({"error": "Privacy export could not be downloaded securely"}, 500)

    async def job_detail(request: Request) -> JSONResponse:
        try:
            scope, actor = authorize(request, PRIVACY_APPROVE_PERMISSION)
            return _response(
                await run_in_threadpool(
                    service.privacy_admin.job_detail,
                    scope,
                    str(request.path_params["job_id"]),
                    actor=actor,
                )
            )
        except PermissionError as exc:
            return _response({"error": str(exc)}, 403)
        except PrivacyError as exc:
            return _response({"error": str(exc)}, 400)
        except Exception:
            return _response({"error": "Privacy job selection could not be read securely"}, 500)

    return [
        Route("/api/privacy", snapshot, methods=["GET"]),
        Route("/api/privacy/actions/{action:str}", mutate, methods=["POST"]),
        Route("/api/privacy/exports/{job_id:str}", export_review, methods=["GET"]),
        Route("/api/privacy/jobs/{job_id:str}", job_detail, methods=["GET"]),
    ]

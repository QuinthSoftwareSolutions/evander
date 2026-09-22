from __future__ import annotations

import json
import math

from starlette.responses import JSONResponse
from starlette.routing import Route

from evander.admin.services.connector_admin_service import ConnectorConfigRevisionConflict
from evander.admin.services.credential_admin_service import CredentialConversionConflict
from evander.admin.web.rbac import ADMIN_OWNER_PERMISSION
from evander.admin.web.shared import request_has_privileged_operations
from evander.credentials import CredentialConflict, CredentialError, CredentialUnavailable


def credential_routes(service, auth):
    async def handle(request):
        def response(payload, status=200):
            return JSONResponse(payload, status_code=status, headers={"Cache-Control": "no-store"})

        try:
            directory = service.teams_admin.normalize_directory_reference(
                request.query_params.get("directory_path", "root")
            )
            session = auth.require(request)
            auth.require_permission(request, ADMIN_OWNER_PERMISSION, directory_path=directory)
            if request.method != "GET" and not request_has_privileged_operations(
                auth=auth, service=service, request=request
            ):
                return response({"error": "Privileged operations are locked."}, 403)
            admin = service.credential_admin
            if request.method == "GET":
                return response(admin.snapshot(directory))
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 131072:
                    return response({"error": "Credential request is too large"}, 413)
            try:
                payload = json.loads(body)
            except (ValueError, UnicodeError):
                return response({"error": "Request must contain valid JSON"}, 400)
            if not isinstance(payload, dict):
                return response({"error": "Request must be a JSON object"}, 400)
            expiry = payload.get("expires_at")
            if expiry is not None and (
                isinstance(expiry, bool)
                or not isinstance(expiry, (float, int))
                or not math.isfinite(expiry)
            ):
                return response({"error": "Expiry must be a timestamp or null"}, 400)
            actor = str(session.get("login") or session.get("user_id") or "admin")
            ref = request.path_params.get("credential_ref")
            if ref:
                if not payload.get("expected_revision"):
                    return response({"error": "expected_revision is required"}, 428)
                result = admin.update(directory, ref, payload, actor)
            elif payload.get("kind") == "teams_oauth":
                result = admin.prepare_teams(directory, payload, actor)
            else:
                if not payload.get("expected_config_revision"):
                    return response({"error": "expected_config_revision is required"}, 428)
                result = admin.create_connector(directory, payload, actor)
            return response(result)
        except PermissionError:
            return response({"error": "Credential management requires directory owner access"}, 403)
        except CredentialConversionConflict as exc:
            return response({"error": str(exc)}, 409)
        except (CredentialConflict, ConnectorConfigRevisionConflict):
            return response(
                {"error": "Credential or configuration changed. Reload before saving."}, 409
            )
        except CredentialUnavailable:
            return response({"error": "Credential storage or key is unavailable"}, 503)
        except CredentialError as exc:
            return response({"error": str(exc)}, 400)
        except Exception:
            # Pydantic/HTTP/SQLite exceptions can carry request values or tokens.
            return response({"error": "Credential operation failed"}, 400)

    return [
        Route("/api/admin/credentials", handle, methods=["GET", "POST"]),
        Route("/api/admin/credentials/{credential_ref}", handle, methods=["PUT"]),
    ]

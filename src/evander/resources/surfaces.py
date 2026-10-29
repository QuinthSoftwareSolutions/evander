"""Standard policy, operator and lifecycle surfaces for optional resource services."""

from pathlib import Path
from urllib.parse import urlsplit

from starlette.concurrency import run_in_threadpool
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Route

from evander.addons import AddonMcpTool
from evander.admin.web.rbac import ADMIN_OWNER_PERMISSION, WORKFLOWS_READ_PERMISSION
from evander.admin.web.shared import request_has_privileged_operations
from evander.gateway.session_context import _resolve_session_id_from_context
from evander.models import DirectoryPath

from .models import Conflict, ResourceError, receipt
from .worker import read_body


def catalog(prefix, operations):
    return tuple(
        AddonMcpTool(
            name=f"{prefix}_{op}",
            label=f"{prefix} {op.replace('_', ' ')}",
            group=prefix,
            description=f"{op.replace('_', ' ')} through configured {prefix} resources and owner scope.",
            discovery_for=tuple(
                f"{prefix}_{name}" for name in ("acquire", "get", "reset", "release", *operations)
            )
            if op == "discover"
            else (),
        )
        for op in ("discover", "acquire", "get", "reset", "release", *operations)
    )


def register_lifecycle(server, context, service_factory, prefix, *, control=False):
    from mcp.server.mcpserver import Context
    from mcp.server.mcpserver.exceptions import ToolError
    from mcp.types import ToolAnnotations

    def invoke(ctx, operation, provider, reference, method, *args):
        session = _resolve_session_id_from_context(
            None, ctx, transport_session_map=getattr(server, "_evander_transport_session_map", None)
        )
        try:
            service = service_factory(context)
            owner = service.owner(session, operation, provider, reference)
            return getattr(service, method)(owner, *args)
        except Exception as exc:
            raise ToolError(
                str(exc) if isinstance(exc, ResourceError) else "Resource operation failed"
            ) from None

    def discover(
        ctx: Context, owner_provider: str = "session", owner_reference: dict | None = None
    ) -> dict:
        return invoke(ctx, "discover", owner_provider, owner_reference, "discover")

    def acquire(
        profile_id: str,
        request_id: str,
        ctx: Context,
        owner_provider: str = "session",
        owner_reference: dict | None = None,
    ) -> dict:
        return invoke(
            ctx, "acquire", owner_provider, owner_reference, "acquire", profile_id, request_id
        )

    def get(
        lease_id: str,
        ctx: Context,
        owner_provider: str = "session",
        owner_reference: dict | None = None,
    ) -> dict:
        return invoke(ctx, "get", owner_provider, owner_reference, "get", lease_id)

    def reset(
        lease_id: str,
        request_id: str,
        ctx: Context,
        owner_provider: str = "session",
        owner_reference: dict | None = None,
    ) -> dict:
        return invoke(
            ctx,
            "reset",
            owner_provider,
            owner_reference,
            "queue",
            lease_id,
            "reset",
            None,
            request_id,
        )

    def release(
        lease_id: str,
        ctx: Context,
        owner_provider: str = "session",
        owner_reference: dict | None = None,
    ) -> dict:
        return invoke(ctx, "release", owner_provider, owner_reference, "queue", lease_id, "release")

    def broker_control(
        lease_id: str,
        action: str,
        request_id: str,
        ctx: Context,
        duration_seconds: int = 60,
        owner_provider: str = "session",
        owner_reference: dict | None = None,
    ) -> dict:
        return invoke(
            ctx,
            "control",
            owner_provider,
            owner_reference,
            "queue",
            lease_id,
            "control",
            {"action": action, "duration_seconds": duration_seconds},
            request_id,
        )

    functions = {
        "discover": discover,
        "acquire": acquire,
        "get": get,
        "reset": reset,
        "release": release,
    }
    if control:
        functions["control"] = broker_control
    for name, fn in functions.items():
        server.tool(
            name=f"{prefix}_{name}",
            description=f"{name.capitalize()} authorized {prefix} resource leases.",
            annotations=ToolAnnotations(
                readOnlyHint=name in {"discover", "get"},
                destructiveHint=name in {"reset", "release", "control"},
            ),
        )(fn)
    return invoke, list(functions)


def register_calls(
    server, context, service_factory, prefix, operations, argument_model=None, read_operations=()
):
    from mcp.server.mcpserver import Context
    from mcp.server.mcpserver.exceptions import ToolError
    from mcp.types import ToolAnnotations

    # Technology-owned validation rejects unknown fields; fixed operation names keep policy grants distinct.
    def factory(operation):
        def execute(
            arguments: dict,
            ctx: Context,
            connection_id: str = "",
            request_id: str = "",
            owner_provider: str = "session",
            owner_reference: dict | None = None,
            lease_id: str | None = None,
        ) -> dict:
            session = _resolve_session_id_from_context(
                None,
                ctx,
                transport_session_map=getattr(server, "_evander_transport_session_map", None),
            )
            try:
                if hasattr(arguments, "model_dump"):
                    arguments = arguments.model_dump(exclude_unset=True)
                service = service_factory(context)
                owner = service.owner(session, operation, owner_provider, owner_reference)
                if lease_id:
                    if connection_id:
                        raise ResourceError("Select a connection_id or lease_id")
                    return service.lease_call(owner, lease_id, operation, arguments, request_id)
                return service.call(owner, connection_id, operation, arguments, request_id)
            except Exception as exc:
                raise ToolError(
                    str(exc) if isinstance(exc, ResourceError) else "Resource operation failed"
                ) from None

        if argument_model is not None:
            execute.__annotations__["arguments"] = argument_model
        return execute

    for operation in operations:
        server.tool(
            name=f"{prefix}_{operation}",
            description=f"{operation} on a registered {prefix} connection. Mutation request IDs are durable; uncertain outcomes are never retried automatically.",
            annotations=ToolAnnotations(
                readOnlyHint=operation in read_operations,
                destructiveHint=operation not in read_operations,
            ),
        )(factory(operation))
    return [f"{prefix}_{op}" for op in operations]


def admin_routes(context, service_factory):
    def authorize(request, write):
        directory = DirectoryPath(segments=request.query_params.get("directory", "root")).as_posix()
        context.auth.require_permission(
            request,
            ADMIN_OWNER_PERMISSION if write else WORKFLOWS_READ_PERMISSION,
            directory_path=directory,
        )
        actor = context.auth.require(request).get("login", "operator")
        if write:
            if not request_has_privileged_operations(
                auth=context.auth, service=context.service, request=request
            ):
                raise PermissionError
            if request.headers.get("content-type", "").split(";", 1)[0] != "application/json":
                raise PermissionError
            origin = request.headers.get("origin")
            if origin:
                supplied, actual = urlsplit(origin), urlsplit(str(request.url))
                if (
                    (supplied.scheme, supplied.netloc) != (actual.scheme, actual.netloc)
                    or supplied.path
                    or supplied.query
                    or supplied.fragment
                ):
                    raise PermissionError
        return service_factory(context), directory, actor

    def execute(request, body):
        try:
            service, directory, actor = authorize(request, request.method != "GET")
            path = request.url.path
            revision, settings = service.store.settings(directory)
            if path.endswith("/settings"):
                if request.method == "PUT":
                    if type(body.get("expected_revision")) is not int:
                        return JSONResponse(
                            {"error": "expected_revision is required"}, status_code=428
                        )
                    candidate = service.validate_settings(body["settings"])
                    revision = service.store.save_settings(
                        directory, body["expected_revision"], candidate, actor
                    )
                    settings = candidate
                value = {"revision": revision, "settings": settings.model_dump()}
            elif path.endswith("/leases"):
                active = service.store.leases(active_only=True, directory=directory)
                recent = service.store.leases(directory=directory, limit=200)
                leases = {item.lease_id: item for item in [*recent, *active]}
                value = {
                    "leases": [receipt(item) for item in leases.values()],
                    "history_limit": 200,
                }
            elif path.endswith("/operations"):
                value = {"operations": service.store.operation_summaries(directory)}
            elif path.endswith("/release"):
                if type(body.get("expected_revision")) is not int:
                    return JSONResponse({"error": "expected_revision is required"}, status_code=428)
                value = service.operator_release(
                    directory,
                    request.path_params["lease_id"],
                    body["expected_revision"],
                    actor,
                )
            elif path.endswith("/credentials"):
                if type(body.get("expected_revision")) is not int:
                    return JSONResponse({"error": "expected_revision is required"}, status_code=428)
                with service.store.locked():
                    if (
                        body.get("expected_revision") != revision
                        or service.store.settings(directory)[0] != revision
                    ):
                        raise Conflict("Settings changed; refresh first")
                    connection_id, purpose = body["connection_id"], body["purpose"]
                    if purpose not in {"admin", "read", "write"}:
                        raise ResourceError("Unsupported credential purpose")
                    connection = settings.connections[connection_id]
                    value = service.credentials.create(
                        connection_id, connection, purpose, directory, body["values"]
                    )
                value = {
                    "credential_ref": value["id"],
                    "purpose": purpose,
                    "connection_id": connection_id,
                }
            else:
                value = service.readiness(
                    directory,
                    request.path_params.get("connection_id"),
                    request.path_params.get("profile_id"),
                )
            return JSONResponse(value, headers={"Cache-Control": "no-store"})
        except PermissionError:
            return JSONResponse({"error": "Operator permission required"}, status_code=403)
        except Conflict as exc:
            return JSONResponse({"error": str(exc)}, status_code=409)
        except (ValueError, KeyError):
            return JSONResponse({"error": "Invalid resource request"}, status_code=400)
        except Exception:
            return JSONResponse({"error": "Provider unavailable"}, status_code=503)

    async def endpoint(request):
        try:
            body = await read_body(request) if request.method != "GET" else {}
        except ValueError:
            return JSONResponse({"error": "Invalid resource request"}, status_code=400)
        # Settings and credential writes can wait on a provisioning intent's file lock.
        # Keep that wait, SQLite access and provider I/O off the Admin Web event loop.
        return await run_in_threadpool(execute, request, body)

    return [
        Route("/settings", endpoint, methods=["GET", "PUT"]),
        Route("/leases", endpoint),
        Route("/operations", endpoint),
        Route("/leases/{lease_id}/release", endpoint, methods=["POST"]),
        Route("/credentials", endpoint, methods=["POST"]),
        Route("/connections/{connection_id}/readiness", endpoint, methods=["POST"]),
        Route("/profiles/{profile_id}/readiness", endpoint, methods=["POST"]),
    ]


def ui_routes(context, root):
    async def file(request):
        context.auth.require(request)
        name = request.path_params.get("name", "index.html")
        if name not in {"index.html", "resources.js", "resources.css"}:
            return JSONResponse({"error": "Not found"}, status_code=404)
        return FileResponse(Path(root) / "ui_static" / name)

    return [Route("/", file), Route("/{name}", file)]

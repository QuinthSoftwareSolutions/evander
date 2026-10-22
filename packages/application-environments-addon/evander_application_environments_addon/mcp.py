from mcp.server.fastmcp import Context
from mcp.server.fastmcp.exceptions import ToolError
from evander.addons import AddonMcpTool
from evander.gateway.session_context import _resolve_session_id_from_context
from .models import CreateRequest
from .service import Service

TOOLS = {
    "application_environment_profiles": "List approved application launch profiles. Browsers and databases are optional independent services.",
    "application_environment_workspaces": "List this agent's opaque checkout references on its assigned worker.",
    "application_environment_create": "Launch an approved application recipe in a bounded environment independent of agent turns. Reuse request_id for identical retries. Does not create a browser.",
    "application_environment_status": "Read application readiness, expiry and leased HTTP endpoint references without renewing its lifetime.",
    "application_environment_close": "Stop an application, revoke its endpoints and release its resources. Cleanup uncertainty preserves checkout guards.",
}
CATALOG = tuple(
    AddonMcpTool(
        name=name,
        label=name.replace("_", " "),
        description=text,
        group="Application environments",
        discovery_for=("application_environment_create",)
        if name.endswith(("profiles", "workspaces"))
        else (),
    )
    for name, text in TOOLS.items()
)


def register_mcp(server, context):
    service = Service(context.runtime_dir, context.service)

    def call(ctx, name, operation):
        id = _resolve_session_id_from_context(
            None, ctx, transport_session_map=getattr(server, "_evander_transport_session_map", None)
        )
        try:
            service.parent(id, name)
            return operation(id)
        except Exception:
            raise ToolError(
                "Application environment authorization or operation failed; inspect its current status before retrying"
            ) from None

    @server.tool(description=TOOLS["application_environment_profiles"])
    def application_environment_profiles(ctx: Context) -> dict:
        return call(
            ctx,
            "application_environment_profiles",
            lambda id: {
                "profiles": [
                    {"id": key, "database_required": value.database_profile is not None}
                    for key, value in service.store.settings(service.parent(id).directory)[
                        1
                    ].profiles.items()
                ]
            },
        )

    @server.tool(description=TOOLS["application_environment_workspaces"])
    def application_environment_workspaces(ctx: Context) -> dict:
        return call(ctx, "application_environment_workspaces", service.workspaces)

    @server.tool(description=TOOLS["application_environment_create"])
    def application_environment_create(request: CreateRequest, ctx: Context) -> dict:
        return call(ctx, "application_environment_create", lambda id: service.create(id, request))

    @server.tool(description=TOOLS["application_environment_status"])
    def application_environment_status(environment_id: str, ctx: Context) -> dict:
        return call(
            ctx, "application_environment_status", lambda id: service.status(id, environment_id)
        )

    @server.tool(description=TOOLS["application_environment_close"])
    def application_environment_close(environment_id: str, ctx: Context) -> dict:
        def close(id):
            record = service.owned(id, environment_id, "application_environment_close")
            return service.close(environment_id, directory=record["session"]["directory"])

        return call(ctx, "application_environment_close", close)

    return list(TOOLS)

from mcp.server.fastmcp import Context
from mcp.server.fastmcp.exceptions import ToolError
from typing import Annotated
from pydantic import Field

from evander.addons import AddonMcpTool
from evander.gateway.session_context import _resolve_session_id_from_context

from .service import Service
from .models import Action, Profile

TOOLS = {
    "test_fixtures_acquire": "Acquire an approved task-owned SQL Server/RabbitMQ test fixture bundle. Reuse request_id for retries; use a fresh ID after release or expiry.",
    "test_fixtures_get": "Read fixture readiness and its private in-container configuration path. Does not renew the lease.",
    "test_fixtures_reset": "Reset only this task's databases and broker to empty fixtures.",
    "test_fixtures_control": "Start, stop, pause or resume this task's broker; faults recover within the requested 120 second limit.",
    "test_fixtures_release": "Release this task's fixture resources and revoke their bindings.",
}
MCP_TOOL_CATALOG = tuple(
    AddonMcpTool(
        name=name,
        label=name.replace("_", " "),
        description=description,
        group="Test fixtures",
        discovery_for=tuple(key for key in TOOLS if key != name)
        if name == "test_fixtures_get"
        else (),
    )
    for name, description in TOOLS.items()
)


def register_mcp_tools(server, context):
    service = Service(context.runtime_dir, context.service)

    def invoke(ctx, method, *args):
        session = _resolve_session_id_from_context(
            None, ctx, transport_session_map=getattr(server, "_evander_transport_session_map", None)
        )
        try:
            return method(session, *args)
        except Exception as exc:
            # Errors from policy/Team authority carry no fixture secrets. Keep
            # unexpected worker/client internals out of MCP responses.
            from .models import FixtureError

            if isinstance(exc, FixtureError):
                raise ToolError(str(exc)) from None
            raise ToolError("Task fixture authorization or request failed") from None

    @server.tool(description=TOOLS["test_fixtures_acquire"])
    def test_fixtures_acquire(
        task_id: str,
        profile: Profile,
        request_id: Annotated[str, Field(min_length=1, max_length=128)],
        ctx: Context,
    ) -> dict:
        return invoke(ctx, service.acquire_ready, task_id, profile, request_id)

    @server.tool(description=TOOLS["test_fixtures_get"])
    def test_fixtures_get(lease_id: str, ctx: Context) -> dict:
        return invoke(ctx, service.get, lease_id)

    @server.tool(description=TOOLS["test_fixtures_reset"])
    def test_fixtures_reset(lease_id: str, ctx: Context) -> dict:
        return invoke(ctx, service.queue, lease_id, "reset")

    @server.tool(description=TOOLS["test_fixtures_control"])
    def test_fixtures_control(
        lease_id: str,
        action: Action,
        ctx: Context,
        duration_seconds: Annotated[int, Field(ge=1, le=120)] = 60,
    ) -> dict:
        return invoke(
            ctx,
            service.control,
            lease_id,
            action,
            duration_seconds,
        )

    @server.tool(description=TOOLS["test_fixtures_release"])
    def test_fixtures_release(lease_id: str, ctx: Context) -> dict:
        return invoke(ctx, service.queue, lease_id, "release")

    return tuple(TOOLS)

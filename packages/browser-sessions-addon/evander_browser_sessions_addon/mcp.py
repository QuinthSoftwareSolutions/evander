"""Session-bound browser tools. Typed actions only; no arbitrary script or CDP."""

import json
import uuid
from typing import Annotated

from pydantic import Field

from mcp.server.fastmcp import Context
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import CallToolResult, ImageContent, TextContent

from evander.addons import AddonMcpTool
from evander.gateway.session_context import _resolve_session_id_from_context

from .models import IDENTIFIER, BrowserCommand, BrowserError, CreateRequest, RunRequest
from .service import Service

TOOLS = {
    "browser_session_profiles": "List approved preview profile names and modes for this agent's directory.",
    "browser_session_workspaces": "List opaque workspace references on this agent's pinned worker. Write a repo-local launch manifest before creating a hosted preview.",
    "browser_session_create": "Create a hosted demo, hosted development preview or local Chrome session for this agent. Local sharing starts with observation; the user must grant input. Reuse request_id for safe retries.",
    "browser_session_invite": "Issue a short-lived single-use invitation for the local Chrome host. Hosted links always require Admin UI sign-in.",
    "browser_session_status": "Read shared browser state, control generation, expiry and viewer URL. Does not renew activity.",
    "browser_session_observe": "Observe the current approved page: screenshot and accessible element references. Does not save a recording.",
    "browser_session_act": "Perform a bounded browser action using a fresh observation and control generation. Use type for character keystrokes (including scanner input), fill to replace a referenced text field, and key for Enter or other supported keys. Never replay an action whose outcome is unknown.",
    "browser_session_highlight": "Show the agent's pointer at an observed element or point without taking control.",
    "browser_session_reply": "Send progress updates and replies to a browser-chat message in its original transcript, as often as needed. Use the supplied message_id. Give each outgoing reply a fresh request_id; reuse it only to retry that identical reply to the same message. Omitting request_id deduplicates identical text for that message.",
    "browser_session_save_screenshot": "Explicitly retain one current screenshot as a normal expiring artifact.",
    "browser_session_run": "Execute a short multi-step browser goal using the directory decision provider. Prefer this over individual action calls for multi-step objectives when enabled. Supply exact named text values; never put secrets in the goal. Reuse request_id only to read its receipt, never replay uncertain actions. The PA must verify the final observation (or observe again if observation_current is false) before claiming success. Stop/pause cancels the objective; do not resume without a new user instruction.",
    "browser_session_cancel_run": "Stop this agent's active browser objective and pause agent input. Safe to repeat. Does not undo already issued input.",
    "browser_session_close": "Close this agent's preview and release its dependent resources.",
}
MCP_TOOL_CATALOG = tuple(
    AddonMcpTool(
        name=name,
        label=name.replace("_", " "),
        description=description,
        group="Shared browsers",
        audit_metadata_only=name == "browser_session_run",
        discovery_for=("browser_session_create",)
        if name in {"browser_session_profiles", "browser_session_workspaces"}
        else (),
    )
    for name, description in TOOLS.items()
)


def register_mcp_tools(server, context):
    service = Service(context.runtime_dir, context.service)

    def invoke(ctx, name, operation):
        agent = _resolve_session_id_from_context(
            None, ctx, transport_session_map=getattr(server, "_evander_transport_session_map", None)
        )
        try:
            service.bindings.parent(agent, name)
            return operation(agent)
        except BrowserError as exc:
            raise ToolError(str(exc)) from None
        except Exception:
            raise ToolError("Browser authorization or request failed") from None

    @server.tool(description=TOOLS["browser_session_profiles"])
    def browser_session_profiles(ctx: Context) -> dict:
        def run(agent):
            parent = service.bindings.parent(agent)
            settings = service.settings(parent.directory)[1]
            return {
                "profiles": [
                    {"id": key, "database_enabled": value.database_enabled}
                    for key, value in settings.profiles.items()
                ],
                "modes": ["hosted_demo", "hosted_development"]
                + (["user_hosted"] if settings.guest_enabled else []),
            }

        return invoke(ctx, "browser_session_profiles", run)

    @server.tool(description=TOOLS["browser_session_workspaces"])
    def browser_session_workspaces(ctx: Context) -> dict:
        return invoke(ctx, "browser_session_workspaces", service.workspaces)

    @server.tool(description=TOOLS["browser_session_create"])
    def browser_session_create(request: CreateRequest, ctx: Context) -> dict:
        return invoke(ctx, "browser_session_create", lambda agent: service.create(agent, request))

    @server.tool(description=TOOLS["browser_session_invite"])
    def browser_session_invite(browser_session_id: str, ctx: Context) -> dict:
        def run(agent):
            session = service.agent_session(agent, browser_session_id, "browser_session_invite")
            return service.invite(session.id, "agent:" + agent)

        return invoke(ctx, "browser_session_invite", run)

    @server.tool(description=TOOLS["browser_session_status"])
    def browser_session_status(browser_session_id: str, ctx: Context) -> dict:
        return invoke(
            ctx,
            "browser_session_status",
            lambda agent: service.receipt(
                service.agent_session(agent, browser_session_id, "browser_session_status")
            ),
        )

    def execute(agent, session_id, name, command):
        session = service.agent_session(agent, session_id, name)
        return service.command(session.id, "agent:" + agent, command, agent=True)

    @server.tool(description=TOOLS["browser_session_observe"])
    def browser_session_observe(
        browser_session_id: str, generation: int, ctx: Context
    ) -> CallToolResult:
        result = invoke(
            ctx,
            "browser_session_observe",
            lambda agent: execute(
                agent,
                browser_session_id,
                "browser_session_observe",
                BrowserCommand(id=str(uuid.uuid4()), generation=generation, action="observe"),
            ),
        )
        image = result.pop("image", None)
        content = [TextContent(type="text", text=json.dumps(result))]
        if image:
            content.append(ImageContent(type="image", data=image, mimeType="image/jpeg"))
        return CallToolResult(content=content)

    @server.tool(description=TOOLS["browser_session_act"])
    def browser_session_act(browser_session_id: str, command: BrowserCommand, ctx: Context) -> dict:
        if command.observing:
            raise ToolError("Use the observe or highlight tool")
        return invoke(
            ctx,
            "browser_session_act",
            lambda agent: execute(agent, browser_session_id, "browser_session_act", command),
        )

    @server.tool(description=TOOLS["browser_session_highlight"])
    def browser_session_highlight(
        browser_session_id: str, command: BrowserCommand, ctx: Context
    ) -> dict:
        if command.action != "highlight":
            raise ToolError("Only highlight actions are accepted")
        return invoke(
            ctx,
            "browser_session_highlight",
            lambda agent: execute(agent, browser_session_id, "browser_session_highlight", command),
        )

    @server.tool(description=TOOLS["browser_session_reply"])
    def browser_session_reply(
        browser_session_id: str,
        message_id: str,
        body: str,
        ctx: Context,
        request_id: Annotated[str, Field(pattern=IDENTIFIER)] | None = None,
    ) -> dict:
        if not body or len(body) > 16000:
            raise ToolError("Reply must contain between 1 and 16000 characters")
        return invoke(
            ctx,
            "browser_session_reply",
            lambda agent: service.reply(
                agent, browser_session_id, message_id, body, request_id=request_id
            ),
        )

    @server.tool(description=TOOLS["browser_session_save_screenshot"])
    def browser_session_save_screenshot(
        browser_session_id: str, generation: int, ctx: Context
    ) -> dict:
        def run(agent):
            session = service.agent_session(
                agent, browser_session_id, "browser_session_save_screenshot"
            )
            return service.save_screenshot(session.id, "agent:" + agent, generation)

        return invoke(ctx, "browser_session_save_screenshot", run)

    @server.tool(description=TOOLS["browser_session_run"])
    def browser_session_run(browser_session_id: str, request: RunRequest, ctx: Context) -> dict:
        return invoke(
            ctx,
            "browser_session_run",
            lambda agent: service.run(agent, browser_session_id, request),
        )

    @server.tool(description=TOOLS["browser_session_cancel_run"])
    def browser_session_cancel_run(browser_session_id: str, run_id: str, ctx: Context) -> dict:
        def run(agent):
            session = service.agent_session(agent, browser_session_id, "browser_session_cancel_run")
            return service.cancel_run(session.id, "agent:" + agent, run_id)

        return invoke(ctx, "browser_session_cancel_run", run)

    @server.tool(description=TOOLS["browser_session_close"])
    def browser_session_close(browser_session_id: str, ctx: Context) -> dict:
        def run(agent):
            session = service.agent_session(agent, browser_session_id, "browser_session_close")
            return service.close(session.id, "agent:" + agent)

        return invoke(ctx, "browser_session_close", run)

    return list(TOOLS)

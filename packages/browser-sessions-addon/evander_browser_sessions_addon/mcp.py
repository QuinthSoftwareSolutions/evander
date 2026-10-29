"""Session-bound browser tools. Typed actions and a separately granted private Playwright connection."""

import json
import uuid
from importlib.resources import files
from typing import Annotated

from pydantic import Field

from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, ImageContent, TextContent

from evander.addons import AddonMcpTool
from evander.gateway.session_context import _resolve_session_id_from_context

from .models import (
    IDENTIFIER,
    BrowserCommand,
    BrowserError,
    CreateRequest,
    RunRequest,
)
from .service import Service

TOOLS = {
    "browser_session_profiles": "List approved browser profile names and modes for this agent's directory, with the operating guide and browser-creation schema. Read this before sharing a browser.",
    "browser_session_create": "Create a browser for approved HTTP targets or application endpoints, or a local Chrome session for this agent. Local sharing starts with observation; the user must grant input. Returns agent_guidance for setup, sharing, control, chat and fast execution. Reuse request_id for safe retries.",
    "browser_session_connect": "Grant this job a private Playwright connection to the shared Chromium browser. Requires explicit permission and browser-profile enablement. Human takeover disconnects it; observe before reconnecting or retrying any action.",
    "browser_session_authenticate": "Open this browser with an explicitly granted managed website login for a selected HTTP target alias. Host passwords are not returned. Requires agent control.",
    "browser_session_invite": "Issue a short-lived single-use invitation for the local Chrome host. Hosted links always require Admin UI sign-in.",
    "browser_session_status": "Read this agent's shared browser state, control generation, active/last run, endpoint features, expiry and viewer URL. Set include_guidance=true when joining an existing task to read the add-on's complete operating guide. Does not renew activity.",
    "browser_session_observe": "Observe the current approved page: screenshot and accessible element references. Does not save a recording.",
    "browser_session_act": "Perform a bounded browser action using a fresh observation and control generation. Follow each target's allowed_actions. Use fill for text/numbers and native dates in YYYY-MM-DD format; select an exact observed native option label when advertised. Date fill/select apply one complete value with synthetic input/change events. Use type for character keystrokes (including scanner input), and key for Enter or other supported keys. Never replay an action whose outcome is unknown.",
    "browser_session_highlight": "Show the agent's pointer at an observed element or point without taking control.",
    "browser_session_reply": "Send progress updates and replies to a browser-chat message in its original transcript, as often as needed. Use the supplied message_id. Give each outgoing reply a fresh request_id; reuse it only to retry that identical reply to the same message. Omitting request_id deduplicates identical text for that message.",
    "browser_session_save_screenshot": "Explicitly retain one current screenshot as a normal expiring artifact.",
    "browser_session_run": "Execute a short multi-step UI objective when enabled and supported by the endpoint. Prefer request.plan for up to eight known actions grounded in your current request.observation_id: it skips intermediate model decisions, validates every target before input, and stops on unexpected page/target/state changes within at most five seconds. Group independent visible field edits using observed allowed_actions; put branch-changing actions last. Plan targets use observed refs or unique exact role/name/context. For fill/type/select, input is the KEY of request.values, never the literal text; native dates use ISO fill and native dropdowns use exact-label select when advertised. Key uses an enumerated key and scroll is bounded. Keep navigation and newly revealed controls in separate plans. Without plan, the directory decision provider chooses intermediate actions (default caps 20 steps/30 seconds, maxima 40/60). Use direct tools for one known action. Supply request.completion with the visible result, or explicitly null for manual verification. For navigation, a destination heading/control predicate waits for the landing page; a completed click alone does not prove arrival. Plans execute all explicit actions before checking completion; decision mode can stop when completion holds. Omission is rejected before new input. Resolve domain choices and author text first; supply every exact literal in request.values as {name: {description, value, sensitive}}, including an empty string to clear. Text in the goal alone cannot be entered. Ordered checkpoints wait for observation predicates after matching actions and retain metadata-only evidence; plans reject stop_after. A satisfied value/state checkpoint permits exactly that declared property change on another existing stable control, such as Save becoming enabled; undeclared changes still stop the plan. Use current control_generation. Reuse request_id with identical arguments after lost replies only to read its durable receipt: retries and restarts never resume or replay input. A running receipt retains its original deadline. Inspect needs_pa and outcome_unknown before deciding a new request. Verify the resulting page from the returned observation when observation_current is true, and reuse its observation_id for the next plan; observe again only when it is false, missing, or insufficient to verify the result. Stop/pause cancels the objective; wait for a new user instruction before resuming. Read browser_session_status with include_guidance=true for the full guide.",
    "browser_session_cancel_run": "Stop this agent's active browser objective and pause agent input. Safe to repeat. Does not undo already issued input.",
    "browser_session_close": "Close this browser and release its browser resources; applications remain independent.",
}
MCP_TOOL_CATALOG = tuple(
    AddonMcpTool(
        name=name,
        label=name.replace("_", " "),
        description=description,
        group="Shared browsers",
        audit_metadata_only=name == "browser_session_run",
        discovery_for=("browser_session_create",) if name == "browser_session_profiles" else (),
    )
    for name, description in TOOLS.items()
)


def with_guidance(result):
    return {
        **result,
        "agent_guidance": {
            "instructions": files(__package__)
            .joinpath("agent-guide.md")
            .read_text(encoding="utf-8"),
            "create_request_schema": CreateRequest.model_json_schema(),
        },
    }


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
            return with_guidance(
                {
                    "profiles": [
                        {"id": key, "playwright_enabled": value.playwright_enabled}
                        for key, value in settings.profiles.items()
                    ],
                    "modes": ["hosted"] + (["user_hosted"] if settings.guest_enabled else []),
                }
            )

        return invoke(ctx, "browser_session_profiles", run)

    @server.tool(description=TOOLS["browser_session_create"])
    def browser_session_create(request: CreateRequest, ctx: Context) -> dict:
        return invoke(
            ctx,
            "browser_session_create",
            lambda agent: with_guidance(service.create(agent, request)),
        )

    @server.tool(description=TOOLS["browser_session_connect"])
    def browser_session_connect(
        browser_session_id: str, observation_id: str, generation: int, ctx: Context
    ) -> dict:
        return invoke(
            ctx,
            "browser_session_connect",
            lambda agent: service.connect(agent, browser_session_id, observation_id, generation),
        )

    @server.tool(description=TOOLS["browser_session_authenticate"])
    def browser_session_authenticate(
        browser_session_id: str, target_alias: str, generation: int, ctx: Context
    ) -> dict:
        return invoke(
            ctx,
            "browser_session_authenticate",
            lambda agent: execute(
                agent,
                browser_session_id,
                "browser_session_authenticate",
                BrowserCommand(
                    id=str(uuid.uuid4()),
                    generation=generation,
                    action="authenticate",
                    target_alias=target_alias,
                ),
            ),
        )

    @server.tool(description=TOOLS["browser_session_invite"])
    def browser_session_invite(browser_session_id: str, ctx: Context) -> dict:
        def run(agent):
            session = service.agent_session(agent, browser_session_id, "browser_session_invite")
            return service.invite(session.id, "agent:" + agent)

        return invoke(ctx, "browser_session_invite", run)

    @server.tool(description=TOOLS["browser_session_status"])
    def browser_session_status(
        browser_session_id: str, ctx: Context, include_guidance: bool = False
    ) -> dict:
        def run(agent):
            result = service.receipt(
                service.agent_session(agent, browser_session_id, "browser_session_status")
            )
            return with_guidance(result) if include_guidance else result

        return invoke(
            ctx,
            "browser_session_status",
            run,
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
        if command.action in {"observe", "highlight"}:
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

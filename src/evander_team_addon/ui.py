from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from starlette.requests import Request
from starlette.responses import FileResponse, PlainTextResponse, Response
from starlette.routing import Route

from evander.addons import AddonContext

_STATIC_ASSET_NAMES = frozenset(
    {
        "admin-design.css",
        "admin-design.js",
        "team-ui.css",
        "team-ui.js",
        "team-ui-state.js",
        "team-ui-config.js",
        "team-ui-app.js",
        "team-ui-costs.js",
        "team-ui-operations.js",
        "team-ui-templates.js",
        "team-ui-timeline.js",
        "team-ui-chat.js",
        "team-ui-governance.js",
        "team-ui-access.js",
        "team-ui-planning.js",
        "team-ui-disclosures.js",
        "team-ui-org.js",
        "team-ui-projects.js",
        "team-ui-people.js",
        "team-ui-board.js",
        "team-ui-cases.js",
        "team-ui-voice.js",
        "team-ui-drafts.js",
        "team-ui-mutations.js",
        "team-ui-sessions.js",
        "team-ui-copy.js",
        "team-ui-dom.js",
        "team-ui-delivery.js",
        "team-ui-releases.js",
        "team-ui-workspace.js",
        "team-ui-human-reviews.js",
        "team-ui-tooltips.js",
        "team-ui-agents.js",
        "team-ui-events.js",
        "team-ui-selects.js",
        "team-ui-format.js",
        "team-ui-recovery.js",
        "team-ui-tasks.js",
        "team-ui-reviews.js",
        "team-ui-evidence.js",
        "team-ui-forms.js",
        "team-ui-avatars.js",
        "team-ui-agent-actions.js",
        "team-ui-policy.js",
        "team-ui-editor.js",
        "team-case-board.js",
        "team-case-context.js",
        "team-identifiers.js",
        "team-mail.js",
        "team-activity.js",
        "team-org-flow.css",
        "team-org-flow.js",
        "team-agent-create.css",
        "team-agent-create.js",
    }
)
_REVALIDATE_HEADERS = {"Cache-Control": "no-cache"}


def admin_ui_routes(_context: AddonContext) -> Sequence[Route]:
    static_dir = Path(__file__).with_name("ui_static")

    async def index(_request: Request) -> Response:
        return FileResponse(
            static_dir / "index.html",
            media_type="text/html; charset=utf-8",
            headers=_REVALIDATE_HEADERS,
        )

    async def asset(request: Request) -> Response:
        asset_name = str(request.path_params.get("asset_name") or "").strip()
        if asset_name not in _STATIC_ASSET_NAMES:
            return PlainTextResponse("Not found", status_code=404)
        return FileResponse(static_dir / asset_name, headers=_REVALIDATE_HEADERS)

    return [
        Route("/", index, methods=["GET"]),
        Route("/{asset_name}", asset, methods=["GET"]),
    ]


__all__ = ["admin_ui_routes"]

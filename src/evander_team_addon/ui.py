from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from starlette.requests import Request
from starlette.responses import FileResponse, PlainTextResponse, Response
from starlette.routing import Route

from agent_sandbox.addons import AddonContext

_STATIC_ASSET_NAMES = frozenset(
    {"team-ui.css", "team-ui.js", "team-org-flow.css", "team-org-flow.js"}
)


def admin_ui_routes(_context: AddonContext) -> Sequence[Route]:
    static_dir = Path(__file__).with_name("ui_static")

    async def index(_request: Request) -> Response:
        return FileResponse(static_dir / "index.html", media_type="text/html; charset=utf-8")

    async def asset(request: Request) -> Response:
        asset_name = str(request.path_params.get("asset_name") or "").strip()
        if asset_name not in _STATIC_ASSET_NAMES:
            return PlainTextResponse("Not found", status_code=404)
        return FileResponse(static_dir / asset_name)

    return [
        Route("/", index, methods=["GET"]),
        Route("/{asset_name}", asset, methods=["GET"]),
    ]


__all__ = ["admin_ui_routes"]

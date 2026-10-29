from __future__ import annotations

from pathlib import Path

from starlette.responses import FileResponse, Response
from starlette.routing import Route

from evander.addons import AddonContext


_STATIC_DIR = Path(__file__).resolve().parent / "ui_static"


def admin_ui_routes(_context: AddonContext) -> list[Route]:
    async def index(_request) -> FileResponse:
        return FileResponse(_STATIC_DIR / "index.html")

    async def asset(request) -> Response:
        asset_path = str(request.path_params.get("asset_path") or "").strip()
        candidate = (_STATIC_DIR / asset_path).resolve(strict=False)
        if _STATIC_DIR not in candidate.parents or not candidate.is_file():
            return Response("Not found", status_code=404)
        return FileResponse(candidate)

    return [
        Route("/", index, methods=["GET"]),
        Route("/{asset_path:path}", asset, methods=["GET"]),
    ]

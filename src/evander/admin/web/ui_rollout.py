"""Temporary Classic/New UI selection; no application or API behavior lives here."""

from __future__ import annotations

from pathlib import Path
from collections.abc import Sequence

from starlette.requests import Request
from starlette.responses import FileResponse, PlainTextResponse, RedirectResponse
from starlette.routing import Route

COOKIE = "admin_ui_version"
VERSIONS = {"classic", "new"}
CLASSIC_ADDONS = {
    "team-addon",
    "bokio-addon",
    "figma-addon",
    "miro-addon",
    "mssql-seed-addon",
    "skatteverket-addon",
}


def selected_ui(request: Request, *, default: str = "new") -> str:
    explicit = request.query_params.get("admin_ui")
    if explicit in VERSIONS:
        return explicit
    saved = request.cookies.get(COOKIE)
    return saved if saved in VERSIONS else default


def decorate_ui_html(html: str, version: str) -> str:
    assert version in VERSIONS
    # The application bundle has already been selected; this small shared control loads no UI bundles.
    control = (
        '<link rel="stylesheet" href="/ui-rollout/switcher.css">'
        f'<script type="module" src="/ui-rollout/switcher.js" data-admin-ui-version="{version}"></script>'
    )
    return html.replace("</head>", control + "</head>", 1)


def addon_rollout_routes(addon_id: str, routes: Sequence, dist_dir: Path, auth) -> list:
    if addon_id not in CLASSIC_ADDONS:
        return list(routes)
    root = (dist_dir.parent / ".classic-addons" / addon_id).resolve()
    index_route = next(
        (route for route in routes if isinstance(route, Route) and route.path == "/"), None
    )
    if index_route is None:
        return list(routes)

    async def index(request: Request):
        # Explicit per-frame URLs let Classic and New stay open in different tabs.
        if selected_ui(request, default="new") == "classic":
            auth.require(request)
            path = request.url.path.removesuffix("index.html").rstrip("/") + "/classic/"
            return RedirectResponse(str(request.url.replace(path=path)), status_code=307)
        return await index_route.endpoint(request)

    async def classic(request: Request):
        auth.require(request)
        name = request.path_params.get("asset_path") or "index.html"
        file = (root / name).resolve()
        if not file.is_relative_to(root) or not file.is_file():
            return PlainTextResponse(
                "Classic UI asset not found. Rebuild the Admin Web assets.", status_code=404
            )
        return FileResponse(file, headers={"Cache-Control": "private, no-cache"})

    async def classic_redirect(request: Request):
        return RedirectResponse(
            str(request.url.replace(path=request.url.path + "/")), status_code=307
        )

    return [
        Route("/classic", classic_redirect, methods=["GET"]),
        Route("/classic/{asset_path:path}", classic, methods=["GET"]),
        Route("/", index, methods=["GET"]),
        Route("/index.html", index, methods=["GET"]),
        *[route for route in routes if route is not index_route],
    ]

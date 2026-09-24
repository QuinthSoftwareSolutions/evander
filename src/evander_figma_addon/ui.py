from pathlib import Path

from starlette.responses import FileResponse, PlainTextResponse
from starlette.routing import Route


def admin_ui_routes(context):
    root = Path(__file__).with_name("ui_static")

    async def index(request):
        context.auth.require(request)
        return FileResponse(root / "index.html", media_type="text/html")

    async def asset(request):
        context.auth.require(request)
        name = request.path_params["name"]
        if name not in {"figma.js", "figma.css", "admin-design.css", "admin-design.js"}:
            return PlainTextResponse("Not found", status_code=404)
        return FileResponse(root / name)

    return [Route("/", index), Route("/{name}", asset)]

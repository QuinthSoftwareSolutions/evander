from pathlib import Path

from starlette.responses import FileResponse, Response
from starlette.routing import Route

ROOT = Path(__file__).resolve().parent / "ui_static"


def admin_ui_routes(_context):
    async def index(_request):
        return FileResponse(ROOT / "index.html")

    async def asset(request):
        name = request.path_params["name"]
        if name not in {"miro.js", "miro.css", "admin-design.css", "admin-design.js"}:
            return Response("Not found", status_code=404)
        return FileResponse(ROOT / name)

    return [Route("/", index), Route("/{name}", asset)]

from pathlib import Path
from starlette.responses import FileResponse, Response
from starlette.routing import Route


def admin_ui_routes(context):
    root = Path(__file__).with_name("ui_static")

    async def file(request):
        context.auth.require(request)
        name = request.path_params.get("name", "index.html")
        if name not in {"index.html", "wordpress.js", "wordpress.css"}:
            return Response(status_code=404)
        return FileResponse(root / name)

    return [Route("/", file), Route("/{name}", file)]

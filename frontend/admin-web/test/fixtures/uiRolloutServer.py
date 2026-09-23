"""Local synthetic browser fixture using the real Admin Web document and add-on routes."""

import importlib
import socket
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import uvicorn
from starlette.routing import Mount

from config_helpers import write_runtime_config
from evander.admin.composition import build_admin_runtime
from evander.admin.web.app import create_app
from evander.admin.web.ui_rollout import CLASSIC_ADDONS, addon_rollout_routes

repo = Path(__file__).resolve().parents[4]
with TemporaryDirectory(prefix="evander-ui-rollout-test-") as temporary:
    service = build_admin_runtime(write_runtime_config(Path(temporary)))
    app = create_app(service)
    # All API traffic is intercepted with synthetic responses in the browser tests.
    auth = SimpleNamespace(require=lambda request: {})
    for addon in CLASSIC_ADDONS:
        module = importlib.import_module("evander_" + addon.replace("-", "_") + ".ui")
        context = SimpleNamespace(auth=auth)
        routes = addon_rollout_routes(
            addon, module.admin_ui_routes(context), repo / "frontend/admin-web/dist", auth
        )
        app.router.routes.insert(0, Mount(f"/addons/{addon}", routes=routes))
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    print(f"http://127.0.0.1:{listener.getsockname()[1]}", flush=True)
    server = uvicorn.Server(uvicorn.Config(app, log_level="error"))
    server.run(sockets=[listener])

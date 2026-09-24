from types import SimpleNamespace

import pytest
from starlette.applications import Starlette
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.routing import Mount, Route
from starlette.testclient import TestClient

from config_helpers import write_runtime_config
from evander.admin.composition import build_admin_runtime
from evander.admin.web import app as web_app
from evander.admin.web.frontend_assets import admin_frontend_assets_ready
from evander.admin.web.ui_rollout import addon_rollout_routes, selected_ui


@pytest.mark.parametrize(
    ("query", "cookie", "expected"),
    [
        ("", "", "new"),
        ("", "admin_ui_version=new", "new"),
        ("admin_ui=classic", "admin_ui_version=new", "classic"),
        ("admin_ui=new", "admin_ui_version=classic", "new"),
        ("admin_ui=../secret", "admin_ui_version=invalid", "new"),
    ],
)
def test_ui_selection_is_explicit_then_remembered_then_defaults_to_new(query, cookie, expected):
    request = Request(
        {"type": "http", "query_string": query.encode(), "headers": [(b"cookie", cookie.encode())]}
    )
    assert selected_ui(request) == expected


def test_root_serves_only_selected_bundle_and_preserves_branding(tmp_path, monkeypatch):
    dist = tmp_path / "frontend/dist"
    for name, prefix in [("new", ""), ("classic", "classic/")]:
        directory = dist / prefix
        (directory / "assets").mkdir(parents=True)
        (directory / "assets" / f"{name}.js").write_text("export {}")
        (directory / "index.html").write_text(
            '<html><head><title></title></head><body><div id="root"></div>'
            f'<script type="module" src="/{prefix}assets/{name}.js"></script></body></html>'
        )
    monkeypatch.setattr(web_app, "_dist_dir", lambda: dist)
    client = TestClient(
        web_app.create_app(build_admin_runtime(write_runtime_config(tmp_path / "state")))
    )
    new = client.get("/")
    assert new.status_code == 200
    assert "/assets/new.js" in new.text
    assert "/classic/assets/classic.js" not in new.text
    assert 'data-admin-ui-version="new"' in new.text
    assert "__ADMIN_WEB_BRANDING__" in new.text
    assert new.headers["cache-control"] == "private, no-store"
    assert new.headers["vary"] == "Cookie"
    client.cookies.set("admin_ui_version", "classic")
    classic = client.get("/index.html")
    assert "/classic/assets/classic.js" in classic.text
    assert "/assets/new.js" not in classic.text
    assert 'data-admin-ui-version="classic"' in classic.text
    assert "/assets/new.js" in client.get("/?admin_ui=new").text


def test_classic_assets_readiness_checks_the_packaged_dependencies(tmp_path):
    import json

    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text('<script src="/assets/new.js"></script>')
    (dist / "assets/new.js").write_text("new")
    (dist / "ui-rollout.json").write_text(
        json.dumps({"required_files": [".classic-addons/team-addon/team-ui.js"]})
    )
    assert not admin_frontend_assets_ready(dist)
    # Deployment needs both versions, but a broken Classic dependency must not prevent New from opening.
    assert "not built" not in web_app._index_html(dist, branding_payload={})
    dependency = tmp_path / ".classic-addons/team-addon/team-ui.js"
    dependency.parent.mkdir(parents=True)
    dependency.write_text("classic")
    assert admin_frontend_assets_ready(dist)


def test_addons_keep_versions_separate_require_auth_and_reject_traversal(tmp_path):
    root = tmp_path / ".classic-addons/figma-addon"
    root.mkdir(parents=True)
    (root / "index.html").write_text('<script src="figma.js"></script>')
    (root / "figma.js").write_text("classic figma")
    (tmp_path / ".classic-addons/private.txt").write_text("not an asset")

    def require(request):
        if request.headers.get("x-test-auth") != "yes":
            raise HTTPException(401)

    async def new_index(request):
        require(request)
        return PlainTextResponse("new figma")

    routes = addon_rollout_routes(
        "figma-addon", [Route("/", new_index)], tmp_path / "dist", SimpleNamespace(require=require)
    )
    client = TestClient(Starlette(routes=[Mount("/addons/figma-addon", routes=routes)]))
    assert client.get("/addons/figma-addon/classic/").status_code == 401
    assert client.get("/addons/figma-addon/classic/figma.js").status_code == 401
    client.headers["x-test-auth"] = "yes"
    assert client.get("/addons/figma-addon/classic/figma.js").text == "classic figma"
    client.cookies.set("admin_ui_version", "classic")
    assert client.get("/addons/figma-addon/?admin_ui=new").text == "new figma"
    redirected = client.get(
        "/addons/figma-addon/?directory_path=root%2Fchild", follow_redirects=False
    )
    assert redirected.status_code == 307
    assert "/classic/?directory_path=root%2Fchild" in redirected.headers["location"]
    assert "figma.js" in client.get(redirected.headers["location"]).text
    assert client.get("/addons/figma-addon/classic/%2e%2e/private.txt").status_code == 404

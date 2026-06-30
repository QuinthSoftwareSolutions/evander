from pathlib import Path

from agent_sandbox.admin.web.app import _index_html
from agent_sandbox.admin.web.frontend_assets import admin_frontend_assets_ready


def test_admin_frontend_assets_ready_requires_referenced_files(tmp_path: Path) -> None:
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    (dist_dir / "index.html").write_text(
        '<script type="module" src="/assets/index-missing.js"></script>',
        encoding="utf-8",
    )

    assert admin_frontend_assets_ready(dist_dir) is False

    assets_dir = dist_dir / "assets"
    assets_dir.mkdir()
    (assets_dir / "index-missing.js").write_text("console.log('ok')", encoding="utf-8")

    assert admin_frontend_assets_ready(dist_dir) is True


def test_admin_web_index_falls_back_when_dist_index_references_missing_assets(
    tmp_path: Path,
) -> None:
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    (dist_dir / "index.html").write_text(
        '<html><head><title>old</title></head><body>'
        '<script type="module" src="/assets/index-missing.js"></script>'
        "</body></html>",
        encoding="utf-8",
    )

    html = _index_html(dist_dir, branding_payload={"page_title": "Evander"})

    assert "Admin frontend not built" in html
    assert "static frontend bundle was not found or is incomplete" in html

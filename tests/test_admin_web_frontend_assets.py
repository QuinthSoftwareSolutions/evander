import json
from pathlib import Path

from evander.admin.web.app import _index_html
from evander.admin.web.frontend_assets import (
    admin_frontend_assets_ready,
    admin_frontend_hashed_asset_url,
)


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


def test_admin_frontend_hashed_asset_url_resolves_manifest_assets(tmp_path: Path) -> None:
    dist_dir = tmp_path / "dist"
    assets_dir = dist_dir / "assets"
    manifest_dir = dist_dir / ".vite"
    assets_dir.mkdir(parents=True)
    manifest_dir.mkdir()
    (assets_dir / "evander-logo-light-a1B2c3D4.png").write_bytes(b"logo")
    (assets_dir / "qss-dark-a1B2c3D4.png").write_bytes(b"logo")
    (manifest_dir / "manifest.json").write_text(
        json.dumps(
            {
                "../branding/evander-logo-light.png": {
                    "file": "assets/evander-logo-light-a1B2c3D4.png",
                    "src": "../branding/evander-logo-light.png",
                },
                "branding/qss-light.png": {
                    "file": "assets/qss-dark-a1B2c3D4.png",
                    "src": "branding/qss-dark.png",
                },
            }
        ),
        encoding="utf-8",
    )

    assert (
        admin_frontend_hashed_asset_url(dist_dir, "evander-logo-light.png")
        == "/assets/evander-logo-light-a1B2c3D4.png"
    )
    assert (
        admin_frontend_hashed_asset_url(dist_dir, "qss-light.png")
        == "/assets/qss-dark-a1B2c3D4.png"
    )
    assert admin_frontend_hashed_asset_url(dist_dir, "missing.png") is None


def test_admin_web_index_falls_back_when_dist_index_references_missing_assets(
    tmp_path: Path,
) -> None:
    dist_dir = tmp_path / "dist"
    dist_dir.mkdir()
    (dist_dir / "index.html").write_text(
        "<html><head><title>old</title></head><body>"
        '<script type="module" src="/assets/index-missing.js"></script>'
        "</body></html>",
        encoding="utf-8",
    )

    html = _index_html(dist_dir, branding_payload={"page_title": "Evander"})

    assert "Admin frontend not built" in html
    assert "static frontend bundle was not found or is incomplete" in html

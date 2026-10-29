from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from PIL import Image, ImageStat

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs/evander-user-manual"
CHAPTERS = SOURCE / "chapters"
DIAGRAMS = SOURCE / "diagrams"
DIAGRAM_ASSETS = ROOT / "docs/assets/evander-user-manual/diagrams"
SCREENSHOT_ASSETS = ROOT / "docs/assets/evander-user-manual/screenshots"

_SPEC = importlib.util.spec_from_file_location(
    "evander_manual_builder", ROOT / "scripts/build_evander_user_manual.py"
)
assert _SPEC is not None and _SPEC.loader is not None
_BUILDER = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_BUILDER)
_assemble = _BUILDER._assemble


def _assert_dark_screenshot(path: Path) -> None:
    assert path.is_file() and path.suffix == ".png"
    with Image.open(path) as image:
        assert image.width >= 1_200 and image.height >= 750
        rgb = image.convert("RGB").resize((160, 100))
        mean = sum(ImageStat.Stat(rgb).mean) / 3
        assert mean < 150, f"{path.name} does not appear to use dark mode ({mean:.1f})"


def test_assembly_rewrites_chapter_relative_links_for_combined_manual() -> None:
    markdown, _manifest = _assemble()

    assert "](team-context-reduction-runbook.md)" in markdown
    assert "](../../team-context-reduction-runbook.md)" not in markdown
    assert "](assets/evander-user-manual/diagrams/13-team-wake-decision.svg)" in markdown


def test_every_diagram_source_has_both_outputs_and_is_used() -> None:
    sources = sorted(DIAGRAMS.glob("*.mmd"))
    chapter_text = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(CHAPTERS.glob("*.md"))
    )

    assert len(sources) >= 15
    for source in sources:
        assert (DIAGRAM_ASSETS / f"{source.stem}.svg").stat().st_size > 1_000
        assert (DIAGRAM_ASSETS / f"{source.stem}.png").stat().st_size > 5_000
        assert f"diagrams/{source.stem}.svg" in chapter_text, source.name


def test_screenshots_are_sanitized_dark_mode_publication_assets() -> None:
    manifest = json.loads((SCREENSHOT_ASSETS / "manifest.json").read_text(encoding="utf-8"))
    fixture = (ROOT / "scripts/capture_evander_user_manual_screenshots.mjs").read_text(
        encoding="utf-8"
    )

    assert manifest["schema_version"] == 1
    assert manifest["fixture"] == "northstar"
    assert manifest["dark_mode"] is True
    screenshots = manifest["screenshots"]
    assert len(screenshots) >= 6
    assert {item["surface"] for item in screenshots} == {"admin", "team"}

    for item in screenshots:
        path = SCREENSHOT_ASSETS / item["file"]
        _assert_dark_screenshot(path)

    assert "session-telemetry-implementation" in fixture
    assert "Create implementation session" in fixture
    assert 'const MANAGED_REPO_ID = "mr_0123456789abcdefabcd"' in fixture
    assert "estimated_cost: 18.42" in fixture
    assert "estimated_cost_usd" not in fixture
    assert 'projectSelect.selectOption("mission-control")' in fixture
    assert 'path.join(ROOT, "frontend", "admin-web", "dist")' in fixture
    assert 'path.join(ROOT, "frontend", "admin-web", "package.json")' in fixture
    assert '"frontend", "workflow-editor"' not in fixture
    for named_core_fixture in (
        "session-imani-telemetry",
        "vm-imani-telemetry",
        "Create Imani session",
    ):
        assert named_core_fixture not in fixture


def test_shared_admin_manual_screenshots_are_refreshed_in_dark_mode() -> None:
    names = {
        "manual-agents.png",
        "manual-approvals.png",
        "manual-artifacts.png",
        "manual-configuration.png",
        "manual-dashboard.png",
        "manual-directory-chooser.png",
        "manual-issues.png",
        "manual-reports.png",
        "manual-sessions.png",
        "manual-skills.png",
        "manual-tool-calls.png",
        "manual-users.png",
        "manual-vms.png",
        "manual-workers.png",
        "manual-workflow.png",
    }

    for name in sorted(names):
        _assert_dark_screenshot(ROOT / "docs/assets/admin-ui" / name)

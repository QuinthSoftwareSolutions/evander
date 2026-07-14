from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import zipfile

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
_book_layout = _BUILDER._book_layout


def _assert_dark_screenshot(path: Path) -> None:
    assert path.is_file() and path.suffix == ".png"
    with Image.open(path) as image:
        assert image.width >= 1_200 and image.height >= 750
        rgb = image.convert("RGB").resize((160, 100))
        mean = sum(ImageStat.Stat(rgb).mean) / 3
        assert mean < 150, f"{path.name} does not appear to use dark mode ({mean:.1f})"


def test_manual_manifest_covers_thirty_ordered_chapters() -> None:
    manifest, rows = _book_layout()

    assert manifest["title"] == "Evander Technical Manual"
    assert manifest["subtitle"] == "Core Platform and Team Add-on"
    assert len(rows) == 30
    assert [int(title.split(".", 1)[0]) for _part, _path, title in rows] == list(
        range(1, 31)
    )
    assert len({path.name for _part, path, _title in rows}) == 30


def test_every_chapter_is_layered_and_source_grounded() -> None:
    _manifest, rows = _book_layout()

    for _part, path, _title in rows:
        text = path.read_text(encoding="utf-8")
        assert "**At a glance.**" in text[:2_000], path.name
        assert text.count("## Source trail") == 1, path.name
        source_trail = text.split("## Source trail", 1)[1]
        assert source_trail.count("\n-") >= 2, path.name


def test_subject_index_is_large_linked_and_validated_by_assembly() -> None:
    index = json.loads((SOURCE / "index.json").read_text(encoding="utf-8"))
    entries = index["entries"]
    terms = [entry["term"].casefold() for entry in entries]

    assert len(entries) >= 100
    assert len(terms) == len(set(terms))

    markdown, _manifest = _assemble()
    assert "# 31. Subject Index" in markdown
    assert markdown.count("](#") >= len(entries)
    assert "# 30. Glossary" in markdown


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

    assert manifest["schema_version"] == 1
    assert manifest["fixture"] == "northstar"
    assert manifest["dark_mode"] is True
    screenshots = manifest["screenshots"]
    assert len(screenshots) >= 6
    assert {item["surface"] for item in screenshots} == {"admin", "team"}

    for item in screenshots:
        path = SCREENSHOT_ASSETS / item["file"]
        _assert_dark_screenshot(path)


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


def test_reference_chapter_tracks_installed_services_and_ports() -> None:
    reference = (CHAPTERS / "28-system-reference.md").read_text(encoding="utf-8")
    installer = (ROOT / "scripts/install_systemd_units.sh").read_text(encoding="utf-8")
    block = re.search(r"EVANDER_UNITS=\((.*?)\n\)", installer, re.DOTALL)
    assert block
    units = set(re.findall(r"evander-[a-z0-9-]+\.service", block.group(1)))
    missing_units = sorted(unit for unit in units if f"`{unit}`" not in reference)
    assert not missing_units

    firewall = (ROOT / "config/firewall.env.example").read_text(encoding="utf-8")
    allowed = re.search(r'^ALLOWED_TCP_PORTS="([0-9 ]+)"$', firewall, re.MULTILINE)
    assert allowed
    missing_ports = sorted(
        port for port in allowed.group(1).split() if not re.search(rf"\b{port}\b", reference)
    )
    assert not missing_ports


def test_current_contract_repairs_are_reflected_in_manual_and_authoritative_docs() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    operations = (ROOT / "docs/operations.md").read_text(encoding="utf-8")
    addons = (ROOT / "docs/addons.md").read_text(encoding="utf-8")
    configuration = (ROOT / "docs/configuration-reference.md").read_text(encoding="utf-8")
    wake_tree = (ROOT / "docs/team-addon-wake-decision-tree.md").read_text(encoding="utf-8")

    assert "evander-trigger-orchestrator --runtime-dir" not in readme
    assert "evander-trigger-orchestrator --runtime-dir" not in operations
    assert "evander-cli trigger-orchestrator --runtime-dir" in readme
    assert "evander-cli trigger-orchestrator --runtime-dir" in operations
    assert "src/evander/triggers/models.py" not in configuration
    assert "no separate top-level `Roles` view" in addons
    assert "chat-class tool `team_reply_to_chat`" in addons
    assert "High or normal<br/>pending inbox?" in wake_tree


def test_generated_publication_formats_are_nontrivial_and_current() -> None:
    outputs = {
        "markdown": ROOT / "docs/evander-user-manual.md",
        "html": ROOT / "docs/evander-user-manual.html",
        "pdf": ROOT / "docs/evander-user-manual.pdf",
        "docx": ROOT / "docs/evander-user-manual.docx",
    }

    assert outputs["markdown"].stat().st_size > 300_000
    assert outputs["html"].stat().st_size > 500_000
    assert outputs["pdf"].read_bytes()[:5] == b"%PDF-"
    assert outputs["pdf"].stat().st_size > 500_000
    assert zipfile.is_zipfile(outputs["docx"])
    assert outputs["docx"].stat().st_size > 500_000

    completed = subprocess.run(
        [sys.executable, "scripts/build_evander_user_manual.py", "--check"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_design_history_is_explicitly_non_normative() -> None:
    chapter = (CHAPTERS / "27-design-history.md").read_text(encoding="utf-8").lower()
    assembled, _manifest = _assemble()

    assert "non-normative" in chapter[:2_500]
    assert "current implementation is normative" in assembled.lower()


def test_northstar_named_cast_is_not_reused_as_human_operators() -> None:
    introduction = (CHAPTERS / "01-reading-paths.md").read_text(encoding="utf-8")
    chapter_text = "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(CHAPTERS.glob("*.md"))
    )

    assert "These five named characters are AI Team agents throughout the manual" in introduction
    assert "Human actions are attributed generically to **an operator**" in introduction
    for conflation in (
        "Priya or another authorized operator",
        "Priya's Admin Web",
        "Priya and Rowan inspect state through Admin Web",
        "Priya's graph",
        "route Imani's approval request to Priya",
        "Mira might configure",
        "Priya might index",
        "release operator Riley",
        "Riley as release operator",
    ):
        assert conflation not in chapter_text

    approvals = (CHAPTERS / "07-mcp-approvals-audit.md").read_text(encoding="utf-8")
    assert "evander-cli approve <request-id> <operator-id>" in approvals

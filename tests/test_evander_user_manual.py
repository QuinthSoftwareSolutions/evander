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
    assert [int(title.split(".", 1)[0]) for _part, _path, title in rows] == list(range(1, 31))
    assert len({path.name for _part, path, _title in rows}) == 30


def test_every_chapter_is_layered_and_source_grounded() -> None:
    _manifest, rows = _book_layout()

    for _part, path, _title in rows:
        text = path.read_text(encoding="utf-8")
        assert "**At a glance.**" in text[:2_000], path.name
        opening = text.split("\n\n## ", 1)[0].split("**At a glance.**", 1)[1]
        opening_words = re.findall(r"\b[\w'-]+\b", opening)
        assert 40 <= len(opening_words) <= 125, path.name
        assert "`" not in opening, path.name
        assert text.count("## Source trail") == 1, path.name
        source_trail = text.split("## Source trail", 1)[1]
        assert source_trail.count("\n-") >= 2, path.name


def test_end_to_end_story_has_plain_narrative_and_optional_sidebars() -> None:
    chapter = (CHAPTERS / "02-end-to-end-story.md").read_text(encoding="utf-8")
    opening = chapter.split("\n\n## ", 1)[0]
    css = (SOURCE / "manual.css").read_text(encoding="utf-8")

    assert "Imagine a piece of work passing through a well-run workshop" in opening
    for compressed_term in (
        "compiled workflow",
        "pinned commits",
        "isolated execution target",
    ):
        assert compressed_term not in opening

    assert chapter.count('class="sidebar-kicker"') >= 8
    assert "Evander is the guarded coordinator; the agent is a temporary worker" in chapter
    assert "Git calls that unique version identifier a **commit SHA**" in chapter
    assert "## A practical operator handoff" in chapter
    assert "Administrator runbook" in chapter
    assert "blockquote:has(.sidebar-kicker)" in css


def test_trust_model_leads_with_plain_promises_and_layers_security_detail() -> None:
    chapter = (CHAPTERS / "03-trust-model.md").read_text(encoding="utf-8")

    assert "## Three promises to keep in mind" in chapter
    for promise in (
        "The agent cannot freely use host credentials",
        "The agent cannot freely reach the internet",
        "The agent cannot exceed this job's permissions",
    ):
        assert promise in chapter

    assert "## What Evander protects—and from whom" in chapter
    assert "the temporary worker reaches useful capabilities through Evander" in chapter
    assert "every line must be true" in chapter
    assert chapter.count('class="sidebar-kicker"') >= 10
    assert "## If you are concerned about access or a secret" in chapter
    assert "Start with the observable job, not host firewall commands" in chapter
    assert "Administrator runbook" in chapter
    assert "sudo nft list ruleset" in chapter.split("Administrator runbook", 1)[1]


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
    delivery_chapter = (CHAPTERS / "22-team-delivery-review.md").read_text(encoding="utf-8")

    assert "evander-trigger-orchestrator --runtime-dir" not in readme
    assert "evander-trigger-orchestrator --runtime-dir" not in operations
    assert "evander-cli trigger-orchestrator --runtime-dir" in readme
    assert "evander-cli trigger-orchestrator --runtime-dir" in operations
    assert "src/evander/triggers/models.py" not in configuration
    assert "no separate top-level `Roles` view" in addons
    assert "chat-class tool `team_reply_to_chat`" in addons
    assert "non-low inbox" in wake_tree
    assert "context_handoff dispatch" in wake_tree
    assert "context_mode: retained_delta" in wake_tree
    assert "team_import_artifact" in delivery_chapter
    assert "/artifacts/team-artifact-imports/..." in delivery_chapter


def test_feature_branch_team_contracts_are_documented() -> None:
    addons = (ROOT / "docs/addons.md").read_text(encoding="utf-8")
    configuration = (ROOT / "docs/configuration-reference.md").read_text(encoding="utf-8")
    git_remotes = (ROOT / "docs/git-remotes.md").read_text(encoding="utf-8")
    managed_git = (CHAPTERS / "11-managed-git-artifacts-consultations.md").read_text(
        encoding="utf-8"
    )
    organization = (CHAPTERS / "18-team-organization-work.md").read_text(encoding="utf-8")
    wakes = (CHAPTERS / "20-team-wakes-context.md").read_text(encoding="utf-8")
    communication = (CHAPTERS / "21-team-communication.md").read_text(encoding="utf-8")
    delivery = (CHAPTERS / "22-team-delivery-review.md").read_text(encoding="utf-8")
    releases = (CHAPTERS / "24-team-git-releases.md").read_text(encoding="utf-8")

    for term in (
        "effect:team:create-review-cohort",
        "integration_boundary",
        "team_reopen_task_for_rework",
        "team_get_current_dispatch_context",
        "team.chat.external_reply_requested",
        "worktree_cleanup_dry_run",
    ):
        assert term in addons

    for term in (
        "integration_boundary",
        "deliverable_contract",
        "require_git_provenance",
        "required_contribution_task_ids",
    ):
        assert term in organization

    for term in (
        "fresh_full",
        "retained_current",
        "team_record_context_handoff",
        "team_get_current_dispatch_context",
        "worktree_cleanup_dry_run",
    ):
        assert term in wakes

    for term in (
        "team.chat.external_reply_requested",
        "attachment_ids",
        "team_chat_body",
        "provider-native file upload",
    ):
        assert term in communication
        assert term in configuration

    for term in (
        "effect:team:create-review-cohort",
        "git_refs.commit_sha",
        "team_retry_review",
        "team_reopen_task_for_rework",
    ):
        assert term in delivery

    assert "resource_limits" in releases
    assert "agent-requested" in releases
    assert "AZURE_DEVOPS_PR_PAT" in managed_git
    assert "AZURE_DEVOPS_PAT" in managed_git
    assert "worktree_cleanup_dry_run" in git_remotes


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


def test_northstar_names_are_reserved_for_team_chapters() -> None:
    introduction = (CHAPTERS / "01-reading-paths.md").read_text(encoding="utf-8")
    paths = sorted(CHAPTERS.glob("*.md"))
    core_text = "\n".join(path.read_text(encoding="utf-8") for path in paths[:15])
    team_text = "\n".join(path.read_text(encoding="utf-8") for path in paths[15:25])
    chapter_text = f"{core_text}\n{team_text}"

    assert "In Chapters 1–15, agents are described by what they do" in introduction
    assert "introduce a small named cast only when persistent identities" in introduction
    assert "Human actions are attributed generically to **an operator**" in introduction
    for name in ("Mira", "Priya", "Imani", "Rowan", "Riley"):
        assert re.search(rf"\b{name}\b", core_text, re.IGNORECASE) is None, name
        assert re.search(rf"\b{name}\b", team_text, re.IGNORECASE), name

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

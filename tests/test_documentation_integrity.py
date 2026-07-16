from __future__ import annotations

import html
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "node_modules",
    "runtime",
}
LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
HTML_LINK_RE = re.compile(r"(?:href|src)=[\"']([^\"']+)[\"']", re.IGNORECASE)
HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
EXPLICIT_ID_RE = re.compile(r"\bid=[\"']([^\"']+)[\"']", re.IGNORECASE)


def _markdown_files() -> list[Path]:
    files: list[Path] = []
    for directory, names, filenames in os.walk(ROOT):
        names[:] = [name for name in names if name not in EXCLUDED_DIRS]
        base = Path(directory)
        files.extend(base / name for name in filenames if name.lower().endswith(".md"))
    return sorted(files)


def _without_fenced_code(text: str) -> str:
    visible: list[str] = []
    fence: str | None = None
    for line in text.splitlines():
        marker = re.match(r"^\s*(`{3,}|~{3,})", line)
        if marker:
            token = marker.group(1)[0]
            fence = None if fence == token else token if fence is None else fence
            continue
        if fence is None:
            visible.append(line)
    return "\n".join(visible)


def _link_destination(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("<") and ">" in raw:
        return raw[1 : raw.index(">")]
    return raw.split(maxsplit=1)[0]


def _github_anchor(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"[`*_~]", "", text)
    text = re.sub(r"[^\w\- ]", "", text.lower())
    return text.strip().replace(" ", "-")


def _anchors(path: Path) -> set[str]:
    text = _without_fenced_code(path.read_text(encoding="utf-8"))
    anchors = set(EXPLICIT_ID_RE.findall(text))
    counts: dict[str, int] = {}
    for line in text.splitlines():
        match = HEADING_RE.match(line)
        if not match:
            continue
        base = _github_anchor(match.group(2))
        count = counts.get(base, 0)
        counts[base] = count + 1
        anchors.add(base if count == 0 else f"{base}-{count}")
    return anchors


def test_local_markdown_links_and_anchors_resolve() -> None:
    failures: list[str] = []
    anchor_cache: dict[Path, set[str]] = {}
    ignored_schemes = ("http://", "https://", "mailto:", "tel:", "data:", "javascript:")
    for source in _markdown_files():
        text = _without_fenced_code(source.read_text(encoding="utf-8"))
        destinations = [_link_destination(value) for value in LINK_RE.findall(text)]
        destinations.extend(HTML_LINK_RE.findall(text))
        for raw in destinations:
            destination = html.unescape(unquote(raw)).strip()
            if not destination or destination.lower().startswith(ignored_schemes):
                continue
            if destination.startswith("/") or any(char in destination for char in "<>{}*$"):
                continue
            path_part, separator, fragment = destination.partition("#")
            path_part = path_part.partition("?")[0]
            target = source if not path_part else (source.parent / path_part).resolve()
            if not target.exists():
                failures.append(f"{source.relative_to(ROOT)}: missing {raw}")
                continue
            if separator and fragment and target.suffix.lower() == ".md":
                if re.fullmatch(r"L\d+(?:-L\d+)?", fragment, re.IGNORECASE):
                    continue
                anchors = anchor_cache.setdefault(target, _anchors(target))
                if fragment not in anchors:
                    failures.append(
                        f"{source.relative_to(ROOT)}: missing anchor #{fragment} in "
                        f"{target.relative_to(ROOT)}"
                    )
    assert not failures, "\n" + "\n".join(failures)


def test_systemd_service_inventory_matches_installer() -> None:
    script = (ROOT / "scripts/install_systemd_units.sh").read_text(encoding="utf-8")
    block = re.search(r"EVANDER_UNITS=\((.*?)\n\)", script, re.DOTALL)
    assert block, "EVANDER_UNITS was not found in the installer"
    installed = set(re.findall(r"evander-[a-z0-9-]+\.service", block.group(1)))

    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    documented_block = agents.split("The current install script renders and enables:", 1)[1]
    documented_block = documented_block.split("Before install/start", 1)[0]
    documented = set(re.findall(r"evander-[a-z0-9-]+\.service", documented_block))
    assert documented == installed


def test_firewall_port_inventories_match_defaults() -> None:
    firewall = (ROOT / "config/firewall.env.example").read_text(encoding="utf-8")
    value = re.search(r"^ALLOWED_TCP_PORTS=(.*)$", firewall, re.MULTILINE)
    assert value
    configured = set(re.findall(r"\d+", value.group(1)))

    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    agents_block = agents.split("- Firewall default allowed host ports:", 1)[1]
    agents_block = agents_block.split("- Runtime configuration:", 1)[0]
    assert set(re.findall(r"`(\d+)`", agents_block)) == configured

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    paragraph = readme.split("The default firewall policy allows host ports", 1)[1]
    paragraph = paragraph.split("\n\n", 1)[0]
    assert configured <= set(re.findall(r"\b\d+\b", paragraph))


def test_bundled_addons_are_in_addon_reference() -> None:
    addon_ids = {
        json.loads(path.read_text(encoding="utf-8"))["addon_id"]
        for path in (ROOT / "src").glob("*/addon.json")
    }
    reference = (ROOT / "docs/addons.md").read_text(encoding="utf-8")
    missing = sorted(addon_id for addon_id in addon_ids if addon_id not in reference)
    assert not missing, f"Bundled add-ons missing from docs/addons.md: {missing}"


def test_admin_navigation_is_covered_by_manuals() -> None:
    routing = (ROOT / "frontend/workflow-editor/src/adminRouting.ts").read_text(encoding="utf-8")
    block = routing.split("export const TABS", 1)[1].split("];", 1)[0]
    labels = set(re.findall(r'label:\s*"([^"]+)"', block))
    assert labels
    for manual_path in (
        ROOT / "docs/admin-ui-user-manual.md",
        ROOT / "docs/evander-user-manual/chapters/28-system-reference.md",
    ):
        manual = manual_path.read_text(encoding="utf-8")
        missing = sorted(label for label in labels if label not in manual)
        assert not missing, f"{manual_path.relative_to(ROOT)} missing tabs: {missing}"


class _TeamViewParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.depth = 0
        self.parts: list[str] = []
        self.labels: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.depth:
            self.depth += 1
        elif any(name == "data-team-view" for name, _ in attrs):
            self.depth = 1
            self.parts = []

    def handle_data(self, data: str) -> None:
        if self.depth:
            self.parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if not self.depth:
            return
        self.depth -= 1
        if self.depth == 0:
            label = " ".join(" ".join(self.parts).split())
            if label:
                self.labels.add(label)


def test_team_navigation_is_covered_by_manuals() -> None:
    parser = _TeamViewParser()
    parser.feed((ROOT / "src/evander_team_addon/ui_static/index.html").read_text(encoding="utf-8"))
    assert parser.labels
    for manual_path in (
        ROOT / "docs/evander-user-manual.md",
        ROOT / "docs/evander-user-manual/chapters/28-system-reference.md",
    ):
        manual = " ".join(manual_path.read_text(encoding="utf-8").split())
        missing = sorted(label for label in parser.labels if label not in manual)
        assert not missing, f"{manual_path.relative_to(ROOT)} missing Team views: {missing}"


def test_workflow_gate_catalog_is_documented() -> None:
    models = (ROOT / "src/evander/events/models.py").read_text(encoding="utf-8")
    gates = set(re.findall(r'Literal\["(gate:[^"]+)"\]', models))
    assert gates
    for reference_path in (ROOT / "docs/architecture.md", ROOT / "docs/configuration-reference.md"):
        reference = reference_path.read_text(encoding="utf-8")
        missing = sorted(gate for gate in gates if gate not in reference)
        assert not missing, f"{reference_path.relative_to(ROOT)} missing gates: {missing}"

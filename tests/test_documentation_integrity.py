from __future__ import annotations

import html
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[1]
LINK_RE = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
HTML_LINK_RE = re.compile(r"(?:href|src)=[\"']([^\"']+)[\"']", re.IGNORECASE)
HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
EXPLICIT_ID_RE = re.compile(r"\bid=[\"']([^\"']+)[\"']", re.IGNORECASE)


def _markdown_files(root: Path = ROOT) -> list[Path]:
    # Include tracked documents and new non-ignored files, but not operator
    # worktrees, generated state, or other ignored content below the checkout.
    output = subprocess.check_output(
        [
            "git", "-C", str(root), "ls-files", "--cached", "--others",
            "--exclude-standard", "-z", "--", "*.[mM][dD]",
        ]
    )
    paths = {root / os.fsdecode(name) for name in output.split(b"\0") if name}
    return sorted(path for path in paths if path.exists() or path.is_symlink())


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


def _local_markdown_link_failures(sources: list[Path], root: Path) -> list[str]:
    failures: list[str] = []
    anchor_cache: dict[Path, set[str]] = {}
    ignored_schemes = ("http://", "https://", "mailto:", "tel:", "data:", "javascript:")
    for source in sources:
        try:
            text = _without_fenced_code(source.read_text(encoding="utf-8"))
        except OSError as exc:
            failures.append(f"{source.relative_to(root)}: cannot read ({type(exc).__name__})")
            continue
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
                failures.append(f"{source.relative_to(root)}: missing {raw}")
                continue
            if separator and fragment and target.suffix.lower() == ".md":
                if re.fullmatch(r"L\d+(?:-L\d+)?", fragment, re.IGNORECASE):
                    continue
                if target not in anchor_cache:
                    anchor_cache[target] = _anchors(target)
                anchors = anchor_cache[target]
                if fragment not in anchors:
                    failures.append(
                        f"{source.relative_to(root)}: missing anchor #{fragment} in "
                        f"{target.relative_to(root)}"
                    )
    return failures


def test_local_markdown_links_and_anchors_resolve() -> None:
    failures = _local_markdown_link_failures(_markdown_files(), ROOT)
    assert not failures, "\n" + "\n".join(failures)


def test_markdown_inventory_respects_git_ignores_and_includes_new_documents(tmp_path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text(".worktrees/\n", encoding="utf-8")
    tracked = tmp_path / "tracked.md"
    tracked.write_text("# Tracked\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "tracked.md"], check=True)
    new = tmp_path / "new.MD"
    new.write_text("# New\n", encoding="utf-8")
    ignored = tmp_path / ".worktrees"
    ignored.mkdir()
    (ignored / "broken.md").symlink_to("missing.md")

    assert _markdown_files(tmp_path) == [new, tracked]


def test_link_checker_parses_repeated_target_once_without_losing_missing_anchor(
    tmp_path, monkeypatch
) -> None:
    source = tmp_path / "source.md"
    target = tmp_path / "target.md"
    source.write_text(
        "[first](target.md#section)\n[repeat](target.md#section)\n[missing](target.md#missing)\n",
        encoding="utf-8",
    )
    target.write_text("# Section\n", encoding="utf-8")
    parsed = []
    original = _anchors

    def anchors(path):
        parsed.append(path)
        return original(path)

    monkeypatch.setattr(f"{__name__}._anchors", anchors)
    assert _local_markdown_link_failures([source, target], tmp_path) == [
        "source.md: missing anchor #missing in target.md"
    ]
    assert parsed == [target]

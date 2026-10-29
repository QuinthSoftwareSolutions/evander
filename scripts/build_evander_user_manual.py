#!/usr/bin/env python3
"""Build and verify every publication format of the Evander Technical Manual."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
SOURCE = DOCS / "evander-user-manual"
CHAPTERS = SOURCE / "chapters"
DIAGRAM_SOURCES = SOURCE / "diagrams"
ASSETS = DOCS / "assets" / "evander-user-manual"
DIAGRAM_ASSETS = ASSETS / "diagrams"
MANIFEST_PATH = SOURCE / "book.json"
INDEX_PATH = SOURCE / "index.json"
CSS_PATH = SOURCE / "manual.css"
MERMAID_CONFIG = SOURCE / "mermaid-config.json"
PUPPETEER_CONFIG = SOURCE / "puppeteer-config.json"
BUILD_RECORD = DOCS / "evander-user-manual-build.json"
OUTPUTS = {
    "markdown": DOCS / "evander-user-manual.md",
    "html": DOCS / "evander-user-manual.html",
    "pdf": DOCS / "evander-user-manual.pdf",
    "docx": DOCS / "evander-user-manual.docx",
}
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)
MARKUP_RE = re.compile(r"[`*_~]|<[^>]+>")
MARKDOWN_LINK_DESTINATION_RE = re.compile(
    r"(?P<prefix>!?\[[^\]\n]*\]\()(?P<destination><[^>\n]+>|[^)\s]+)"
)


class BuildError(RuntimeError):
    """Raised for an actionable manual build or verification failure."""


def _run(command: list[str], *, cwd: Path = ROOT, timeout: int = 300) -> None:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise BuildError(f"command timed out after {timeout}s: {' '.join(command)}") from exc
    if completed.returncode != 0:
        output = completed.stdout.strip()
        raise BuildError(
            f"command failed ({completed.returncode}): {' '.join(command)}"
            + (f"\n{output}" if output else "")
        )


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise BuildError(f"missing required manual source: {path.relative_to(ROOT)}") from exc
    except json.JSONDecodeError as exc:
        raise BuildError(f"invalid JSON in {path.relative_to(ROOT)}: {exc}") from exc


def _plain_heading(value: str) -> str:
    value = re.sub(r"\s+\{#[^}]+\}\s*$", "", value)
    value = re.sub(r"!\[([^]]*)\]\([^)]+\)", r"\1", value)
    value = re.sub(r"\[([^]]+)\]\([^)]+\)", r"\1", value)
    return " ".join(MARKUP_RE.sub("", value).split())


def _anchor(value: str) -> str:
    """Return the GFM/Pandoc identifier used by this manual's ASCII headings."""
    value = _plain_heading(value).lower()
    value = re.sub(r"[^a-z0-9 _-]", "", value)
    value = re.sub(r"\s+", "-", value.strip())
    return re.sub(r"-+", "-", value)


def _chapter_title(path: Path, expected_number: int) -> str:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"^#\s+(.+?)\s*$", text, re.MULTILINE)
    if not match:
        raise BuildError(f"{path.relative_to(ROOT)} must begin with one level-one heading")
    title = _plain_heading(match.group(1))
    if not title.startswith(f"{expected_number}. "):
        raise BuildError(
            f"{path.relative_to(ROOT)} must begin with chapter number {expected_number}"
        )
    if "**At a glance.**" not in text[:2000]:
        raise BuildError(f"{path.relative_to(ROOT)} is missing its At a glance opening")
    if "## Source trail" not in text:
        raise BuildError(f"{path.relative_to(ROOT)} is missing its Source trail")
    return title


def _book_layout() -> tuple[dict[str, Any], list[tuple[dict[str, Any], Path, str]]]:
    manifest = _load_json(MANIFEST_PATH)
    if not isinstance(manifest, dict) or not isinstance(manifest.get("parts"), list):
        raise BuildError("book.json must contain a parts array")
    rows: list[tuple[dict[str, Any], Path, str]] = []
    seen: set[str] = set()
    number = 1
    for part in manifest["parts"]:
        if not isinstance(part, dict) or not isinstance(part.get("chapters"), list):
            raise BuildError("every book part must contain a chapters array")
        for name in part["chapters"]:
            if not isinstance(name, str) or name in seen:
                raise BuildError(f"duplicate or invalid chapter entry: {name!r}")
            seen.add(name)
            path = CHAPTERS / name
            if not path.is_file():
                raise BuildError(f"missing chapter: {path.relative_to(ROOT)}")
            rows.append((part, path, _chapter_title(path, number)))
            number += 1
    extras = sorted(path.name for path in CHAPTERS.glob("*.md") if path.name not in seen)
    if extras:
        raise BuildError(f"chapter files are not listed in book.json: {', '.join(extras)}")
    return manifest, rows


def _rewrite_source_links(text: str, *, source_path: Path) -> str:
    """Make chapter-local links relative to the assembled manual in docs/."""

    def replace(match: re.Match[str]) -> str:
        raw_destination = match.group("destination")
        wrapped = raw_destination.startswith("<") and raw_destination.endswith(">")
        destination = raw_destination[1:-1] if wrapped else raw_destination
        if (
            not destination
            or destination.startswith(("#", "/"))
            or re.match(r"^[a-z][a-z0-9+.-]*:", destination, re.IGNORECASE)
        ):
            return match.group(0)
        path_part = re.split(r"[?#]", destination, maxsplit=1)[0]
        if not path_part:
            return match.group(0)
        resolved = (source_path.parent / path_part).resolve()
        try:
            rewritten_path = resolved.relative_to(DOCS.resolve()).as_posix()
        except ValueError:
            return match.group(0)
        rewritten = rewritten_path + destination[len(path_part) :]
        if wrapped:
            rewritten = f"<{rewritten}>"
        return match.group("prefix") + rewritten

    return MARKDOWN_LINK_DESTINATION_RE.sub(replace, text)


def _headings(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    counts: dict[str, int] = {}
    in_fence = False
    for line in text.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if not match:
            continue
        label = _plain_heading(match.group(2))
        base = _anchor(label)
        count = counts.get(base, 0)
        counts[base] = count + 1
        identifier = base if count == 0 else f"{base}-{count}"
        result[identifier] = label
    return result


def _render_index(heading_map: dict[str, str]) -> str:
    data = _load_json(INDEX_PATH)
    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, list) or not entries:
        raise BuildError("index.json must contain a non-empty entries array")
    normalized: list[dict[str, Any]] = []
    terms: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("term"), str):
            raise BuildError("every index entry must have a term")
        key = entry["term"].casefold()
        if key in terms:
            raise BuildError(f"duplicate subject-index term: {entry['term']}")
        terms.add(key)
        targets = entry.get("targets", [])
        if not isinstance(targets, list) or not targets:
            raise BuildError(f"index term has no targets: {entry['term']}")
        missing = [target for target in targets if target not in heading_map]
        if missing:
            raise BuildError(
                f"index term {entry['term']!r} has missing targets: {', '.join(missing)}"
            )
        normalized.append(entry)
    normalized.sort(key=lambda item: item["term"].casefold())
    lines = [
        "# 31. Subject Index",
        "",
        "> **At a glance.** Use this linked index when you know the concept but not the subsystem that owns it.",
        "",
    ]
    initial = ""
    for entry in normalized:
        next_initial = entry["term"][0].upper()
        if not next_initial.isalpha():
            next_initial = "#"
        if next_initial != initial:
            initial = next_initial
            lines.extend([f"## {initial}", ""])
        links = [f"[{heading_map[target]}](#{target})" for target in entry["targets"]]
        suffix = ""
        see_also = entry.get("see_also", [])
        if see_also:
            suffix = "; see also " + ", ".join(str(value) for value in see_also)
        lines.append(f"- **{entry['term']}** — {', '.join(links)}{suffix}.")
    lines.extend(
        [
            "",
            "## Source trail",
            "",
            "- `docs/evander-user-manual/index.json` — canonical term-to-section mappings.",
            "- `scripts/build_evander_user_manual.py` — target validation and index generation.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _assemble() -> tuple[str, dict[str, Any]]:
    manifest, rows = _book_layout()
    lines = [
        "---",
        f"pagetitle: {manifest['title']} — {manifest['subtitle']}",
        f"subtitle: {manifest['subtitle']}",
        f"date: {manifest['publication_date']}",
        f"lang: {manifest.get('language', 'en')}",
        "---",
        "",
        f"# {manifest['title']}",
        "",
        f"## {manifest['subtitle']}",
        "",
        '<div class="manual-edition">',
        "",
        f"**Edition {manifest['edition']}** · Evander {manifest['project_version']} · "
        f"verified against `{manifest['verified_commit'][:12]}` · {manifest['publication_date']}",
        "",
        "Current implementation is normative. Chapters explicitly labelled as design history are non-normative and describe how the present system evolved.",
        "",
        "</div>",
        "",
        "This manual explains the complete core Evander control plane and the Team Add-on from first principles through architecture, operation, failure recovery, and engineering extension. Readers can follow it front to back or use the glossary and subject index as a technical reference.",
        "",
        "## Contents",
        "",
    ]
    for part in manifest["parts"]:
        part_heading = f"Part {part['number']}: {part['title']}"
        lines.append(f"- [{part_heading}](#{_anchor(part_heading)})")
        for row_part, _path, title in rows:
            if row_part is part:
                lines.append(f"  - [{title}](#{_anchor(title)})")
    lines.append("  - [31. Subject Index](#31-subject-index)")
    lines.append("")

    chapter_index = 0
    for part in manifest["parts"]:
        lines.extend(
            [
                '<div class="page-break"></div>',
                "",
                f"# Part {part['number']}: {part['title']}",
                "",
                str(part.get("summary", "")).strip(),
                "",
            ]
        )
        for row_part, path, _title in rows:
            if row_part is not part:
                continue
            chapter_index += 1
            lines.append(
                _rewrite_source_links(path.read_text(encoding="utf-8"), source_path=path).rstrip()
            )
            lines.extend(["", ""])

    without_index = "\n".join(lines).rstrip() + "\n"
    heading_map = _headings(without_index)
    lines.extend(
        [
            '<div class="page-break"></div>',
            "",
            _render_index(heading_map).rstrip(),
            "",
        ]
    )
    return "\n".join(lines).rstrip() + "\n", manifest


def _atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)


def _require_command(name: str) -> str:
    value = shutil.which(name)
    if not value:
        raise BuildError(f"required command is not installed: {name}")
    return value


def _render_diagrams() -> None:
    if not DIAGRAM_SOURCES.exists():
        raise BuildError(f"missing diagram source directory: {DIAGRAM_SOURCES.relative_to(ROOT)}")
    cli = SOURCE / "node_modules" / ".bin" / "mmdc"
    if not cli.is_file():
        raise BuildError(
            "Mermaid CLI is not installed; run `npm ci --prefix docs/evander-user-manual`"
        )
    DIAGRAM_ASSETS.mkdir(parents=True, exist_ok=True)
    sources = sorted(DIAGRAM_SOURCES.glob("*.mmd"))
    if not sources:
        raise BuildError("the manual must contain at least one Mermaid diagram")
    expected: set[Path] = set()
    for source in sources:
        svg = DIAGRAM_ASSETS / f"{source.stem}.svg"
        png = DIAGRAM_ASSETS / f"{source.stem}.png"
        expected.update({svg, png})
        base = [
            str(cli),
            "--input",
            str(source),
            "--configFile",
            str(MERMAID_CONFIG),
            "--puppeteerConfigFile",
            str(PUPPETEER_CONFIG),
            "--theme",
            "neutral",
            "--backgroundColor",
            "transparent",
        ]
        _run([*base, "--output", str(svg)], cwd=SOURCE, timeout=120)
        _run([*base, "--output", str(png), "--scale", "2"], cwd=SOURCE, timeout=120)
    for stale in sorted(DIAGRAM_ASSETS.glob("*")):
        if stale.is_file() and stale.suffix in {".svg", ".png"} and stale not in expected:
            stale.unlink()


def _pandoc_base(markdown: Path) -> list[str]:
    resource_path = os.pathsep.join([str(DOCS), str(ROOT)])
    return [
        _require_command("pandoc"),
        str(markdown),
        "--from=gfm+yaml_metadata_block",
        f"--resource-path={resource_path}",
        "--standalone",
        "--toc",
        "--toc-depth=3",
    ]


def _build_html(markdown: Path, destination: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="evander-manual-html-") as directory:
        temporary = Path(directory) / destination.name
        _run(
            [
                *_pandoc_base(markdown),
                "--to=html5",
                "--embed-resources",
                f"--css={CSS_PATH}",
                "--highlight-style=tango",
                f"--output={temporary}",
            ],
            timeout=300,
        )
        temporary.replace(destination)


def _build_pdf(html: Path, destination: Path) -> None:
    chrome = _require_command("google-chrome")
    with tempfile.TemporaryDirectory(prefix="evander-manual-pdf-") as directory:
        temporary = Path(directory) / destination.name
        profile = Path(directory) / "chrome-profile"
        _run(
            [
                chrome,
                "--headless=new",
                "--no-sandbox",
                "--disable-gpu",
                "--disable-dev-shm-usage",
                f"--user-data-dir={profile}",
                "--no-pdf-header-footer",
                f"--print-to-pdf={temporary}",
                html.resolve().as_uri(),
            ],
            timeout=300,
        )
        if not temporary.is_file() or temporary.stat().st_size < 10_000:
            raise BuildError("Chrome did not produce a valid manual PDF")
        temporary.replace(destination)


def _build_docx(markdown: Path, destination: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="evander-manual-docx-") as directory:
        docx_markdown = Path(directory) / "manual.md"
        docx_source = re.sub(
            r'<p class="figure-caption">(.*?)</p>',
            r"*\1*",
            markdown.read_text(encoding="utf-8"),
            flags=re.DOTALL,
        )
        docx_source = re.sub(
            r"(assets/evander-user-manual/diagrams/[^)\s]+)\.svg",
            r"\1.png",
            docx_source,
        )
        docx_markdown.write_text(docx_source, encoding="utf-8")
        temporary = Path(directory) / destination.name
        command = [*_pandoc_base(docx_markdown), "--to=docx", f"--output={temporary}"]
        if destination.is_file():
            command.insert(-1, f"--reference-doc={destination}")
        _run(command, timeout=300)
        if not temporary.is_file() or temporary.stat().st_size < 10_000:
            raise BuildError("Pandoc did not produce a valid manual DOCX")
        temporary.replace(destination)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_files() -> list[Path]:
    files: set[Path] = set()
    for base in (SOURCE, ASSETS):
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if not path.is_file() or "node_modules" in path.parts:
                continue
            files.add(path)
    files.add(Path(__file__).resolve())
    capture_script = ROOT / "scripts" / "capture_evander_user_manual_screenshots.mjs"
    if capture_script.is_file():
        files.add(capture_script)
    return sorted(files)


def _source_fingerprint() -> tuple[str, dict[str, str]]:
    rows: dict[str, str] = {}
    digest = hashlib.sha256()
    for path in _source_files():
        relative = path.relative_to(ROOT).as_posix()
        value = _hash_file(path)
        rows[relative] = value
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(value.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest(), rows


def _write_build_record(manifest: dict[str, Any]) -> None:
    fingerprint, sources = _source_fingerprint()
    output_hashes = {
        path.relative_to(ROOT).as_posix(): _hash_file(path) for path in OUTPUTS.values()
    }
    record = {
        "schema_version": 1,
        "title": manifest["title"],
        "edition": manifest["edition"],
        "project_version": manifest["project_version"],
        "verified_commit": manifest["verified_commit"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_fingerprint": fingerprint,
        "sources": sources,
        "outputs": output_hashes,
    }
    _atomic_text(BUILD_RECORD, json.dumps(record, indent=2, sort_keys=True) + "\n")


def _check() -> None:
    _book_layout()
    record = _load_json(BUILD_RECORD)
    fingerprint, _sources = _source_fingerprint()
    if record.get("source_fingerprint") != fingerprint:
        raise BuildError(
            "manual sources or assets changed after publication; rebuild with "
            "`./.venv/bin/python scripts/build_evander_user_manual.py`"
        )
    expected_outputs = record.get("outputs")
    if not isinstance(expected_outputs, dict):
        raise BuildError("manual build record has no output hashes")
    for path in OUTPUTS.values():
        relative = path.relative_to(ROOT).as_posix()
        if not path.is_file():
            raise BuildError(f"missing generated manual output: {relative}")
        if expected_outputs.get(relative) != _hash_file(path):
            raise BuildError(f"generated manual output differs from build record: {relative}")
    for source in sorted(DIAGRAM_SOURCES.glob("*.mmd")):
        for suffix in (".svg", ".png"):
            output = DIAGRAM_ASSETS / f"{source.stem}{suffix}"
            if not output.is_file():
                raise BuildError(f"missing rendered diagram: {output.relative_to(ROOT)}")
    print(f"Evander manual outputs are current (source fingerprint {fingerprint[:12]}).")


def _build() -> None:
    _render_diagrams()
    markdown, manifest = _assemble()
    _atomic_text(OUTPUTS["markdown"], markdown)
    _build_html(OUTPUTS["markdown"], OUTPUTS["html"])
    _build_pdf(OUTPUTS["html"], OUTPUTS["pdf"])
    _build_docx(OUTPUTS["markdown"], OUTPUTS["docx"])
    _write_build_record(manifest)
    print(
        "Built Evander Technical Manual: "
        + ", ".join(path.relative_to(ROOT).as_posix() for path in OUTPUTS.values())
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify that tracked outputs match the canonical sources and build record",
    )
    args = parser.parse_args()
    try:
        if args.check:
            _check()
        else:
            _build()
    except BuildError as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

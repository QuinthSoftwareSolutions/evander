from __future__ import annotations

from dataclasses import dataclass
import importlib.metadata as importlib_metadata
import os
import re
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Final
from urllib.parse import quote

import tomllib


NEWS_ASSET_BASE_PATH: Final[str] = "/api/admin/news/assets"
NEWS_ASSET_DIRECTORY: Final[Path] = Path("docs/assets/news")
_RELEASE_KIND_PREFIXES: Final[dict[str, str]] = {
    "added": "feature",
    "expanded": "feature",
    "exposed": "feature",
    "fixed": "bugfix",
    "prevented": "bugfix",
    "stopped": "bugfix",
}
_MAX_CHANGELOG_ENTRIES: Final[int] = 5
_BUILD_ENV_VARS: Final[tuple[str, ...]] = (
    "AGENT_SANDBOX_REPO_VERSION",
    "agent_sandbox_repo_version",
    "AGENT_SANDBOX_BUILD_VERSION",
    "agent_sandbox_build_version",
)
_NEWS_IMAGE_RE: Final[re.Pattern[str]] = re.compile(
    r'^!\[(?P<alt>[^\]]*)\]\((?P<src>[^\s)]+)(?:\s+"(?P<title>[^"]*)")?\)$'
)
_NEWS_HEADING_RE: Final[re.Pattern[str]] = re.compile(r"^(?P<marks>#{3,6})\s+(?P<text>.+)$")
_NEWS_LIST_ITEM_RE: Final[re.Pattern[str]] = re.compile(r"^[-*]\s+(?P<text>.+)$")


@dataclass(frozen=True, slots=True)
class ReleaseChangelogEntryData:
    text: str
    kind: str | None = None


@dataclass(frozen=True, slots=True)
class ReleaseChangelogData:
    source: str | None
    path: str | None
    heading: str | None
    entries: list[ReleaseChangelogEntryData]


@dataclass(frozen=True, slots=True)
class ReleaseNewsBlockData:
    kind: str
    text: str | None = None
    level: int | None = None
    items: tuple[str, ...] = ()
    src: str | None = None
    dark_src: str | None = None
    alt: str | None = None
    title: str | None = None


@dataclass(frozen=True, slots=True)
class ReleaseNewsData:
    source: str | None
    path: str | None
    index: int
    heading: str | None
    blocks: list[ReleaseNewsBlockData]
    has_previous: bool
    has_next: bool
    previous_index: int | None
    next_index: int | None


@dataclass(frozen=True, slots=True)
class ReleaseMetadataData:
    version: str | None
    version_source: str | None
    build: str | None
    build_source: str | None
    changelog: ReleaseChangelogData | None
    news: ReleaseNewsData | None
    missing_reasons: list[str]


def read_release_metadata(
    project_dir: Path,
    *,
    env: Mapping[str, str] | None = None,
    news_index: int = 0,
) -> ReleaseMetadataData:
    runtime_env = env or os.environ
    missing_reasons: list[str] = []

    version, version_source, version_missing = _read_version(project_dir)
    if version_missing:
        missing_reasons.append(version_missing)

    build, build_source, build_missing = _read_build_identity(project_dir, runtime_env)
    if build_missing:
        missing_reasons.append(build_missing)

    changelog, changelog_missing = _read_changelog(project_dir)
    if changelog_missing:
        missing_reasons.append(changelog_missing)

    news, news_missing = _read_news(project_dir, news_index=news_index)
    if news_missing:
        missing_reasons.append(news_missing)

    return ReleaseMetadataData(
        version=version,
        version_source=version_source,
        build=build,
        build_source=build_source,
        changelog=changelog,
        news=news,
        missing_reasons=missing_reasons,
    )


def _read_version(project_dir: Path) -> tuple[str | None, str | None, str | None]:
    try:
        version = importlib_metadata.version("agent-sandbox")
    except importlib_metadata.PackageNotFoundError:
        version = None
    else:
        version = version.strip() or None
        if version is not None:
            return version, "importlib.metadata:agent-sandbox", None

    pyproject_path = project_dir / "pyproject.toml"
    if not pyproject_path.exists():
        return None, None, f"Version not found in importlib.metadata:agent-sandbox or {pyproject_path.name}."
    try:
        pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return None, None, f"{pyproject_path.name} is missing or invalid."
    project_section = pyproject.get("project")
    if not isinstance(project_section, dict):
        return None, None, f"{pyproject_path.name} is missing [project].version."
    version = project_section.get("version")
    if not isinstance(version, str) or not version.strip():
        return None, None, f"{pyproject_path.name} is missing [project].version."
    version = version.strip()
    return version, str(pyproject_path), None


def _read_build_identity(
    project_dir: Path,
    env: Mapping[str, str],
) -> tuple[str | None, str | None, str | None]:
    for env_name in _BUILD_ENV_VARS:
        value = env.get(env_name)
        if value is None:
            continue
        text = value.strip()
        if text:
            return text, f"env:{env_name}", None

    git_dir = project_dir / ".git"
    if not git_dir.exists():
        return (
            None,
            None,
            f"Build identity not found in {', '.join(_BUILD_ENV_VARS)} and {project_dir} is not a Git checkout.",
        )

    completed = subprocess.run(
        ["git", "-C", str(project_dir), "rev-parse", "--short", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return (
            None,
            None,
            f"Build identity not available from Git in {project_dir}.",
        )
    value = completed.stdout.strip()
    if not value:
        return None, None, f"Build identity not available from Git in {project_dir}."
    return value, "git:rev-parse --short HEAD", None


def _read_changelog(project_dir: Path) -> tuple[ReleaseChangelogData | None, str | None]:
    changelog_path = project_dir / "CHANGELOG.md"
    if not changelog_path.exists():
        return None, f"{changelog_path.name} is missing."
    try:
        text = changelog_path.read_text(encoding="utf-8")
    except OSError:
        return None, f"{changelog_path.name} could not be read."

    lines = text.splitlines()
    heading: str | None = None
    body_lines: list[str] = []
    in_release_section = False
    for line in lines:
        if re.match(r"^##\s+\S", line):
            if in_release_section:
                break
            heading = line.strip()[3:].strip()
            in_release_section = True
            continue
        if not in_release_section:
            continue
        if re.match(r"^#{1,6}\s+\S", line):
            break
        body_lines.append(line)

    if not in_release_section or heading is None:
        return None, f"{changelog_path.name} does not contain a release section headed with ##."

    entries = _parse_changelog_entries(body_lines)
    if not entries:
        return None, f"{changelog_path.name} release section has no bullet entries."

    return (
        ReleaseChangelogData(
            source=changelog_path.name,
            path=str(changelog_path),
            heading=heading,
            entries=entries[:_MAX_CHANGELOG_ENTRIES],
        ),
        None,
    )


def _read_news(
    project_dir: Path,
    *,
    news_index: int,
) -> tuple[ReleaseNewsData | None, str | None]:
    if news_index < 0:
        raise ValueError("news_index must be greater than or equal to 0.")

    news_path = project_dir / "NEWS.md"
    if not news_path.exists():
        return None, f"{news_path.name} is missing."

    found_any_entry = False
    current_index = -1
    heading: str | None = None
    body_lines: list[str] = []
    collecting = False
    has_next = False

    try:
        with news_path.open(encoding="utf-8") as news_file:
            for raw_line in news_file:
                line = raw_line.rstrip("\r\n")
                if re.match(r"^##\s+\S", line):
                    found_any_entry = True
                    if collecting:
                        has_next = True
                        break
                    current_index += 1
                    if current_index == news_index:
                        heading = line.strip()[3:].strip()
                        collecting = True
                    continue
                if collecting:
                    body_lines.append(line)
    except OSError:
        return None, f"{news_path.name} could not be read."

    if not found_any_entry:
        return None, f"{news_path.name} does not contain a news section headed with ##."
    if not collecting or heading is None:
        return None, f"{news_path.name} does not contain news entry {news_index + 1}."

    blocks = _parse_news_blocks(body_lines, project_dir=project_dir)
    if not blocks:
        return None, f"{news_path.name} news entry {news_index + 1} has no renderable content."

    return (
        ReleaseNewsData(
            source=news_path.name,
            path=str(news_path),
            index=news_index,
            heading=heading,
            blocks=blocks,
            has_previous=news_index > 0,
            has_next=has_next,
            previous_index=news_index - 1 if news_index > 0 else None,
            next_index=news_index + 1 if has_next else None,
        ),
        None,
    )


def _parse_news_blocks(lines: list[str], *, project_dir: Path) -> list[ReleaseNewsBlockData]:
    blocks: list[ReleaseNewsBlockData] = []
    current_parts: list[str] = []
    current_items: list[str] = []

    def flush_paragraph() -> None:
        if not current_parts:
            return
        text = " ".join(part.strip() for part in current_parts if part.strip()).strip()
        if text:
            blocks.append(ReleaseNewsBlockData(kind="paragraph", text=text))
        current_parts.clear()

    def flush_list() -> None:
        if not current_items:
            return
        blocks.append(ReleaseNewsBlockData(kind="list", items=tuple(current_items)))
        current_items.clear()

    for line in lines:
        stripped = line.strip()
        if not stripped:
            flush_paragraph()
            continue
        heading_match = _NEWS_HEADING_RE.match(stripped)
        if heading_match is not None:
            flush_paragraph()
            flush_list()
            blocks.append(
                ReleaseNewsBlockData(
                    kind="heading",
                    text=heading_match.group("text").strip(),
                    level=len(heading_match.group("marks")),
                )
            )
            continue
        list_item_match = _NEWS_LIST_ITEM_RE.match(stripped)
        if list_item_match is not None:
            flush_paragraph()
            current_items.append(list_item_match.group("text").strip())
            continue
        image_match = _NEWS_IMAGE_RE.match(stripped)
        if image_match is not None:
            flush_paragraph()
            flush_list()
            image_source = _news_image_source(image_match.group("src"))
            if image_source is None:
                current_parts.append(stripped)
                continue
            src, asset_relative = image_source
            title = image_match.group("title")
            blocks.append(
                ReleaseNewsBlockData(
                    kind="image",
                    src=src,
                    dark_src=_news_dark_image_src(project_dir, asset_relative),
                    alt=image_match.group("alt").strip(),
                    title=title.strip() if title is not None and title.strip() else None,
                )
            )
            continue
        flush_list()
        current_parts.append(stripped)
    flush_paragraph()
    flush_list()
    return blocks


def _news_image_source(raw_src: str) -> tuple[str, Path] | None:
    normalized = raw_src.strip()
    if not normalized or "\x00" in normalized:
        return None
    lowered = normalized.lower()
    if (
        lowered.startswith("http://")
        or lowered.startswith("https://")
        or lowered.startswith("data:")
        or lowered.startswith("//")
    ):
        return None

    relative = Path(normalized.lstrip("/"))
    if relative.is_absolute() or not relative.parts:
        return None
    if any(part in {"", ".", ".."} for part in relative.parts):
        return None

    news_asset_parts = NEWS_ASSET_DIRECTORY.parts
    if relative.parts[: len(news_asset_parts)] == news_asset_parts:
        asset_relative = Path(*relative.parts[len(news_asset_parts) :])
    else:
        asset_relative = relative
    if not asset_relative.parts or any(part in {"", ".", ".."} for part in asset_relative.parts):
        return None

    return _news_asset_public_src(asset_relative), asset_relative


def _news_dark_image_src(project_dir: Path, asset_relative: Path) -> str | None:
    stem = asset_relative.stem
    suffix = asset_relative.suffix
    parent = asset_relative.parent
    candidates = (
        parent / f"{stem}-dark{suffix}",
        parent / f"{stem}-inverse{suffix}",
    )
    root = project_dir / NEWS_ASSET_DIRECTORY
    for candidate in candidates:
        if (root / candidate).is_file():
            return _news_asset_public_src(candidate)
    return None


def _news_asset_public_src(asset_relative: Path) -> str:
    return f"{NEWS_ASSET_BASE_PATH}/{quote(asset_relative.as_posix(), safe='/._-~')}"


def _parse_changelog_entries(lines: list[str]) -> list[ReleaseChangelogEntryData]:
    entries: list[ReleaseChangelogEntryData] = []
    current_parts: list[str] = []

    def flush() -> None:
        if not current_parts:
            return
        text = " ".join(part.strip() for part in current_parts if part.strip()).strip()
        if not text:
            return
        entries.append(
            ReleaseChangelogEntryData(
                text=text,
                kind=_classify_changelog_entry(text),
            )
        )

    for line in lines:
        stripped = line.rstrip()
        if not stripped:
            if current_parts:
                current_parts.append("")
            continue
        bullet_match = re.match(r"^[-*]\s+(?P<text>.+\S)\s*$", stripped)
        if bullet_match:
            flush()
            current_parts = [bullet_match.group("text").strip()]
            continue
        if current_parts and stripped[:1].isspace():
            current_parts.append(stripped.strip())
    flush()
    return entries


def _classify_changelog_entry(text: str) -> str | None:
    normalized = re.sub(r"^[\s\-\*\d.()]+", "", text).strip().lower()
    if not normalized:
        return None
    first_word = re.split(r"[\s:;,-]", normalized, maxsplit=1)[0]
    return _RELEASE_KIND_PREFIXES.get(first_word)

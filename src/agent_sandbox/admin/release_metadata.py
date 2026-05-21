from __future__ import annotations

from dataclasses import dataclass
import importlib.metadata as importlib_metadata
import os
import re
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Final

import tomllib


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
class ReleaseNewsData:
    source: str | None
    path: str | None
    index: int
    heading: str | None
    paragraphs: list[str]
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

    paragraphs = _parse_news_paragraphs(body_lines)
    if not paragraphs:
        return None, f"{news_path.name} news entry {news_index + 1} has no prose paragraphs."

    return (
        ReleaseNewsData(
            source=news_path.name,
            path=str(news_path),
            index=news_index,
            heading=heading,
            paragraphs=paragraphs,
            has_previous=news_index > 0,
            has_next=has_next,
            previous_index=news_index - 1 if news_index > 0 else None,
            next_index=news_index + 1 if has_next else None,
        ),
        None,
    )


def _parse_news_paragraphs(lines: list[str]) -> list[str]:
    paragraphs: list[str] = []
    current_parts: list[str] = []

    def flush() -> None:
        if not current_parts:
            return
        text = " ".join(part.strip() for part in current_parts if part.strip()).strip()
        if text:
            paragraphs.append(text)
        current_parts.clear()

    for line in lines:
        stripped = line.strip()
        if not stripped:
            flush()
            continue
        current_parts.append(stripped)
    flush()
    return paragraphs


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

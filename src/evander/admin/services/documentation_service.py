from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any


_CURRENT_DOCUMENTATION_SECTIONS = {
    "Authoritative Entry Points": "authoritative",
    "Focused References": "focused",
}
_GENERATED_MANUAL_PATH = "docs/evander-user-manual.md"
_AGENT_EXCLUDED_MANUAL_CHAPTERS = {
    "01-reading-paths.md",
    "27-design-history.md",
}
_MARKDOWN_LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
_HEADING_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
_DEFAULT_READ_LINE_COUNT = 200
_MAX_READ_LINE_COUNT = 200
_DEFAULT_SEARCH_RESULTS = 20
_MAX_SEARCH_RESULTS = 50
_MAX_SEARCH_RESULTS_PER_DOCUMENT = 3
_MAX_SEARCH_QUERY_LENGTH = 200
_MAX_SEARCH_SNIPPET_LENGTH = 500


@dataclass(frozen=True, slots=True)
class DocumentationEntry:
    path: str
    title: str
    classification: str

    def as_payload(self) -> dict[str, str]:
        return {
            "path": self.path,
            "title": self.title,
            "classification": self.classification,
        }


class DocumentationService:
    """Read the current, explicitly cataloged project documentation."""

    def __init__(self, project_dir: Path) -> None:
        self._project_dir = Path(project_dir).expanduser().resolve()

    def list_documentation(self) -> dict[str, Any]:
        entries = self._catalog()
        return {
            "documents": [entry.as_payload() for entry in entries],
            "count": len(entries),
            "source": "docs/README.md",
        }

    def search_documentation(
        self,
        query: str,
        *,
        path: str | None = None,
        max_results: int = _DEFAULT_SEARCH_RESULTS,
    ) -> dict[str, Any]:
        normalized_query = str(query or "").strip()
        if not normalized_query:
            raise ValueError("query is required")
        if len(normalized_query) > _MAX_SEARCH_QUERY_LENGTH:
            raise ValueError(f"query must be at most {_MAX_SEARCH_QUERY_LENGTH} characters")
        normalized_limit = max(
            1, min(int(max_results or _DEFAULT_SEARCH_RESULTS), _MAX_SEARCH_RESULTS)
        )
        entries = self._selected_entries(path)
        needle = normalized_query.casefold()
        matches: list[dict[str, Any]] = []
        truncated = False

        for entry in entries:
            document_matches = 0
            text = self._read_entry_text(entry)
            for line_number, line in enumerate(text.splitlines(), start=1):
                if needle not in line.casefold():
                    continue
                if document_matches >= _MAX_SEARCH_RESULTS_PER_DOCUMENT:
                    truncated = True
                    continue
                if len(matches) >= normalized_limit:
                    truncated = True
                    continue
                snippet = line.strip()
                if len(snippet) > _MAX_SEARCH_SNIPPET_LENGTH:
                    snippet = snippet[: _MAX_SEARCH_SNIPPET_LENGTH - 1].rstrip() + "…"
                matches.append(
                    {
                        "path": entry.path,
                        "title": entry.title,
                        "classification": entry.classification,
                        "line_number": line_number,
                        "snippet": snippet,
                    }
                )
                document_matches += 1

        return {
            "query": normalized_query,
            "path": str(path or "").strip() or None,
            "matches": matches,
            "count": len(matches),
            "max_results": normalized_limit,
            "truncated": truncated,
        }

    def read_documentation(
        self,
        path: str,
        *,
        start_line: int = 1,
        line_count: int = _DEFAULT_READ_LINE_COUNT,
    ) -> dict[str, Any]:
        entry = self._entry_for_path(path)
        normalized_start = int(start_line)
        if normalized_start < 1:
            raise ValueError("start_line must be at least 1")
        normalized_count = max(
            1,
            min(int(line_count or _DEFAULT_READ_LINE_COUNT), _MAX_READ_LINE_COUNT),
        )
        lines = self._read_entry_text(entry).splitlines()
        start_index = min(len(lines), normalized_start - 1)
        end_index = min(len(lines), start_index + normalized_count)
        selected = lines[start_index:end_index]
        return {
            **entry.as_payload(),
            "content": "\n".join(selected) + ("\n" if selected else ""),
            "start_line": start_index + 1 if selected else 0,
            "end_line": end_index if selected else 0,
            "line_count": len(selected),
            "total_lines": len(lines),
            "has_more": end_index < len(lines),
        }

    def _selected_entries(self, path: str | None) -> list[DocumentationEntry]:
        if str(path or "").strip():
            return [self._entry_for_path(str(path))]
        return self._catalog()

    def _entry_for_path(self, path: str) -> DocumentationEntry:
        normalized = str(path or "").strip()
        if not normalized:
            raise ValueError("path is required")
        entries = {entry.path: entry for entry in self._catalog()}
        try:
            return entries[normalized]
        except KeyError as exc:
            raise ValueError(f"documentation path is not available: {normalized}") from exc

    def _catalog(self) -> list[DocumentationEntry]:
        map_path = self._validated_document_path(
            self._project_dir / "docs" / "README.md",
            expected_relative_path="docs/README.md",
        )
        map_text = map_path.read_text(encoding="utf-8")
        entries: list[DocumentationEntry] = [
            self._entry_from_path(map_path, classification="documentation_map")
        ]
        entries.extend(self._mapped_current_entries(map_path, map_text))
        entries.extend(self._technical_manual_entries())

        deduplicated: list[DocumentationEntry] = []
        seen: set[str] = set()
        for entry in entries:
            if entry.path in seen:
                continue
            seen.add(entry.path)
            deduplicated.append(entry)
        return deduplicated

    def _mapped_current_entries(
        self,
        map_path: Path,
        map_text: str,
    ) -> list[DocumentationEntry]:
        entries: list[DocumentationEntry] = []
        classification: str | None = None
        found_sections: set[str] = set()
        for line in map_text.splitlines():
            if line.startswith("## "):
                heading = line.removeprefix("## ").strip()
                classification = _CURRENT_DOCUMENTATION_SECTIONS.get(heading)
                if classification is not None:
                    found_sections.add(heading)
                continue
            if classification is None:
                continue
            for match in _MARKDOWN_LINK_RE.finditer(line):
                raw_target = match.group(1).split("#", 1)[0].strip()
                if not raw_target or not raw_target.casefold().endswith(".md"):
                    continue
                candidate = self._validated_document_path(map_path.parent / raw_target)
                relative = candidate.relative_to(self._project_dir).as_posix()
                if relative == _GENERATED_MANUAL_PATH:
                    continue
                entries.append(self._entry_from_path(candidate, classification=classification))

        missing_sections = set(_CURRENT_DOCUMENTATION_SECTIONS) - found_sections
        if missing_sections:
            raise ValueError(
                "documentation map is missing current sections: "
                + ", ".join(sorted(missing_sections))
            )
        return entries

    def _technical_manual_entries(self) -> list[DocumentationEntry]:
        manual_dir = self._project_dir / "docs" / "evander-user-manual"
        chapter_dir = manual_dir / "chapters"
        if not chapter_dir.is_dir():
            raise FileNotFoundError(
                f"technical manual chapter directory does not exist: {chapter_dir}"
            )
        manifest_path = manual_dir / "book.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict) or not isinstance(manifest.get("parts"), list):
            raise ValueError("technical manual book.json must contain a parts array")
        chapter_names: list[str] = []
        for part in manifest["parts"]:
            if not isinstance(part, dict) or not isinstance(part.get("chapters"), list):
                raise ValueError("every technical manual part must contain a chapters array")
            for name in part["chapters"]:
                if not isinstance(name, str) or not name.endswith(".md"):
                    raise ValueError("technical manual chapter names must be Markdown filenames")
                if name in chapter_names:
                    raise ValueError(f"technical manual chapter is listed more than once: {name}")
                chapter_names.append(name)

        entries: list[DocumentationEntry] = []
        for name in chapter_names:
            if name in _AGENT_EXCLUDED_MANUAL_CHAPTERS:
                continue
            validated = self._validated_document_path(chapter_dir / name)
            entries.append(self._entry_from_path(validated, classification="technical_manual"))
        if not entries:
            raise ValueError("technical manual chapter catalog is empty")
        return entries

    def _entry_from_path(self, path: Path, *, classification: str) -> DocumentationEntry:
        text = path.read_text(encoding="utf-8")
        heading = _HEADING_RE.search(text)
        if heading is None:
            raise ValueError(f"documentation file must contain a level-one heading: {path}")
        return DocumentationEntry(
            path=path.relative_to(self._project_dir).as_posix(),
            title=heading.group(1).strip(),
            classification=classification,
        )

    def _read_entry_text(self, entry: DocumentationEntry) -> str:
        path = self._validated_document_path(
            self._project_dir / entry.path,
            expected_relative_path=entry.path,
        )
        return path.read_text(encoding="utf-8")

    def _validated_document_path(
        self,
        candidate: Path,
        *,
        expected_relative_path: str | None = None,
    ) -> Path:
        resolved = Path(candidate).resolve(strict=True)
        try:
            relative = resolved.relative_to(self._project_dir)
        except ValueError as exc:
            raise ValueError(
                f"documentation path escapes the project directory: {candidate}"
            ) from exc
        if expected_relative_path is not None and relative.as_posix() != expected_relative_path:
            raise ValueError(f"documentation path resolved unexpectedly: {candidate}")
        if not resolved.is_file() or resolved.suffix.casefold() != ".md":
            raise ValueError(f"documentation path must be a Markdown file: {candidate}")
        return resolved


__all__ = ["DocumentationEntry", "DocumentationService"]

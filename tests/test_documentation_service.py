from __future__ import annotations

from pathlib import Path

import pytest

from evander.admin.services.documentation_service import DocumentationService


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    _write(project / "README.md", "# Project\n\nRoot reference.\n")
    _write(
        project / "docs" / "README.md",
        """# Documentation Map

## Authoritative Entry Points

- [`../README.md`](../README.md): overview
- [`operations.md`](operations.md): operations
- [`evander-user-manual.md`](evander-user-manual.md): generated manual

## Focused References

- [`focused.md`](focused.md): focused guide

## Historical Material

- [`history.md`](history.md): historical notes
""",
    )
    _write(project / "docs" / "operations.md", "# Operations\n\nCurrent operation.\n")
    _write(project / "docs" / "focused.md", "# Focused Guide\n\nFocused detail.\n")
    _write(project / "docs" / "history.md", "# History\n\nOld behavior.\n")
    _write(
        project / "docs" / "evander-user-manual.md",
        "# Generated Manual\n\nDuplicated content and history.\n",
    )
    _write(
        project / "docs" / "evander-user-manual" / "book.json",
        """{
  "parts": [
    {
      "chapters": [
        "01-reading-paths.md",
        "02-current.md",
        "27-design-history.md",
        "28-reference.md"
      ]
    }
  ]
}
""",
    )
    chapters = project / "docs" / "evander-user-manual" / "chapters"
    _write(chapters / "01-reading-paths.md", "# 1. Reading Paths\n\nHow to read.\n")
    _write(chapters / "02-current.md", "# 2. Current Detail\n\nTechnical needle.\n")
    _write(chapters / "27-design-history.md", "# 27. Design History\n\nHistorical detail.\n")
    _write(chapters / "28-reference.md", "# 28. Reference\n\nReference needle.\n")
    _write(chapters / "99-unlisted.md", "# 99. Unlisted\n\nNot in the manual manifest.\n")
    return project


def test_documentation_catalog_includes_current_sources_and_technical_chapters(
    tmp_path: Path,
) -> None:
    service = DocumentationService(_project(tmp_path))

    result = service.list_documentation()
    documents = {item["path"]: item for item in result["documents"]}

    assert result["source"] == "docs/README.md"
    assert documents["docs/README.md"]["classification"] == "documentation_map"
    assert documents["README.md"]["classification"] == "authoritative"
    assert documents["docs/operations.md"]["classification"] == "authoritative"
    assert documents["docs/focused.md"]["classification"] == "focused"
    assert (
        documents["docs/evander-user-manual/chapters/02-current.md"]["classification"]
        == "technical_manual"
    )
    assert "docs/evander-user-manual/chapters/28-reference.md" in documents
    assert "docs/evander-user-manual.md" not in documents
    assert "docs/history.md" not in documents
    assert "docs/evander-user-manual/chapters/01-reading-paths.md" not in documents
    assert "docs/evander-user-manual/chapters/27-design-history.md" not in documents
    assert "docs/evander-user-manual/chapters/99-unlisted.md" not in documents


def test_documentation_reads_and_searches_live_files_with_bounds(tmp_path: Path) -> None:
    project = _project(tmp_path)
    service = DocumentationService(project)
    operations = project / "docs" / "operations.md"
    operations.write_text(
        "# Operations\n" + "\n".join(f"line {index} live needle" for index in range(1, 251)),
        encoding="utf-8",
    )

    search = service.search_documentation("LIVE NEEDLE", max_results=50)
    operation_matches = [
        match for match in search["matches"] if match["path"] == "docs/operations.md"
    ]
    assert len(operation_matches) == 3
    assert search["truncated"] is True

    first_page = service.read_documentation(
        "docs/operations.md",
        start_line=1,
        line_count=999,
    )
    assert first_page["line_count"] == 200
    assert first_page["start_line"] == 1
    assert first_page["end_line"] == 200
    assert first_page["has_more"] is True

    operations.write_text("# Operations\n\nChanged after service construction.\n", encoding="utf-8")
    changed = service.read_documentation("docs/operations.md")
    assert "Changed after service construction." in changed["content"]


def test_documentation_path_filter_and_validation(tmp_path: Path) -> None:
    service = DocumentationService(_project(tmp_path))

    filtered = service.search_documentation(
        "needle",
        path="docs/evander-user-manual/chapters/28-reference.md",
    )
    assert [match["path"] for match in filtered["matches"]] == [
        "docs/evander-user-manual/chapters/28-reference.md"
    ]

    with pytest.raises(ValueError, match="not available"):
        service.read_documentation("docs/history.md")
    with pytest.raises(ValueError, match="not available"):
        service.read_documentation("../outside.md")
    with pytest.raises(ValueError, match="start_line"):
        service.read_documentation("README.md", start_line=0)
    with pytest.raises(ValueError, match="query is required"):
        service.search_documentation("  ")


def test_documentation_catalog_rejects_symlink_escape(tmp_path: Path) -> None:
    project = _project(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text("# Outside\n", encoding="utf-8")
    (project / "docs" / "escape.md").symlink_to(outside)
    map_path = project / "docs" / "README.md"
    map_path.write_text(
        map_path.read_text(encoding="utf-8").replace(
            "- [`operations.md`](operations.md): operations",
            "- [`operations.md`](operations.md): operations\n- [`escape.md`](escape.md): escape",
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="escapes the project directory"):
        DocumentationService(project).list_documentation()


def test_documentation_catalog_fails_on_invalid_utf8(tmp_path: Path) -> None:
    project = _project(tmp_path)
    (project / "docs" / "focused.md").write_bytes(b"# Focused\n\xff")

    with pytest.raises(UnicodeDecodeError):
        DocumentationService(project).list_documentation()

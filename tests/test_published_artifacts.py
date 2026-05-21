from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agent_sandbox.published_artifacts import (
    PublishedArtifactStore,
    filename_for_key,
    validate_artifact_key,
)


def test_published_artifact_store_publishes_immutable_archive(tmp_path: Path) -> None:
    archive = tmp_path / "source.tar.gz"
    archive.write_bytes(b"archive-bytes")
    store = PublishedArtifactStore(tmp_path)

    record = store.publish_archive(
        key="runs/session-1/report",
        session_id="session-1",
        archive_path=archive,
        source_artifacts_root="/artifacts",
        source_file_count=2,
        source_byte_count=123,
        execution_backend="container_worker_vm",
    )

    assert record.key == "runs/session-1/report"
    assert record.filename == "report.tar.gz"
    assert record.byte_count == len(b"archive-bytes")
    assert record.source_file_count == 2
    assert record.source_byte_count == 123
    assert record.execution_backend == "container_worker_vm"
    assert store.get("runs/session-1/report").blob_path.read_bytes() == b"archive-bytes"

    with pytest.raises(ValueError, match="already exists"):
        store.publish_archive(
            key="runs/session-1/report",
            session_id="session-1",
            archive_path=archive,
        )


def test_published_artifact_store_lists_by_time_with_cursor_inputs(tmp_path: Path) -> None:
    store = PublishedArtifactStore(tmp_path)
    archive = tmp_path / "source.tar.gz"
    archive.write_bytes(b"archive")
    old = datetime(2026, 5, 18, 10, 0, tzinfo=UTC)
    new = datetime(2026, 5, 19, 10, 0, tzinfo=UTC)

    store.publish_archive(
        key="old",
        session_id="session-1",
        archive_path=archive,
        published_at=old,
    )
    store.publish_archive(
        key="new",
        session_id="session-1",
        archive_path=archive,
        published_at=new,
    )

    assert [item.key for item in store.list_metadata()] == ["new", "old"]
    assert [
        item.key
        for item in store.list_metadata(published_after=new - timedelta(seconds=1))
    ] == ["new"]
    assert [item.key for item in store.list_metadata(key="old")] == ["old"]


def test_published_artifact_keys_are_not_filesystem_paths() -> None:
    assert validate_artifact_key("folder/name?query=true") == "folder/name?query=true"
    assert filename_for_key("folder/name?query=true") == "name-query-true.tar.gz"
    with pytest.raises(ValueError, match="required"):
        validate_artifact_key("")
    with pytest.raises(ValueError, match="control"):
        validate_artifact_key("bad\nkey")

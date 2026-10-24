from __future__ import annotations

import hashlib
import json
import sqlite3
import tracemalloc
import weakref
import zipfile
from array import array

import pytest

from evander.admin.services.repository_index_service import RepositoryIndexService
from evander.code_index import importer
from evander.code_index.export import export_snapshot
from evander.code_index.importer import import_artifact, validate_artifact
from evander.code_index.models import IndexArtifact, IndexSettings, Occurrence, SourceDocument
from test_repository_index import ManagedGit, REPO, SHA


def make_build(tmp_path):
    service = RepositoryIndexService(tmp_path, ManagedGit())
    service.save_settings(
        REPO,
        "root/app",
        IndexSettings(enabled=True, scip=True, semantic=False).model_dump(),
        expected_revision=0,
    )
    queued = service.ensure(REPO, "root/app", SHA)
    return service, service.store.get(queued["id"])


def test_occurrence_validation_reuses_document_widths_across_interleaved_ranges(tmp_path):
    _, build = make_build(tmp_path)
    splits = []

    class CountedText(str):
        def split(self, *args, **kwargs):
            splits.append(self)
            return super().split(*args, **kwargs)

    documents = [
        SourceDocument(path=path, text="").model_copy(update={"text": CountedText("é🙂\nAB\n")})
        for path in ("One.cs", "Two.cs")
    ]
    occurrences = [
        Occurrence(
            path=document.path,
            symbol=f"symbol-{index}",
            line=1,
            column=width,
            end_line=3,
            end_column=0,
            encoding=encoding,
        )
        for index in range(100)
        for document in documents
        for encoding, width in (("utf-8", 6), ("utf-16", 3), ("utf-32", 2))
    ]
    source = IndexArtifact(commit_sha=SHA, documents=documents, occurrences=occurrences)
    accepted_documents, accepted_occurrences = validate_artifact(source, build)

    assert accepted_documents == documents
    assert accepted_occurrences == occurrences
    # Work scales with document/encoding pairs, rather than symbol occurrences.
    assert len(splits) <= 6


@pytest.mark.performance
def test_occurrence_width_cache_has_bounded_memory_across_many_large_documents(tmp_path):
    document_count, text = 32, "\n" * 500000
    _, build = make_build(tmp_path)
    source = IndexArtifact(
        commit_sha=SHA,
        documents=[
            SourceDocument(path=f"File-{index}.cs", text=text) for index in range(document_count)
        ],
        occurrences=[
            Occurrence(
                path=f"File-{index}.cs",
                symbol="symbol",
                line=1,
                column=0,
                end_line=1,
                end_column=0,
                encoding="utf-32",
            )
            for index in range(document_count)
        ],
    )
    tracemalloc.start()
    try:
        _, accepted = validate_artifact(source, build)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert len(accepted) == document_count
    # Caching all line lists would retain over 128 MiB for this sparse workload.
    assert peak < 14 * 1024 * 1024


def test_occurrence_width_cache_bounds_live_entries_for_many_small_documents(tmp_path, monkeypatch):
    _, build = make_build(tmp_path)
    source = IndexArtifact(
        commit_sha=SHA,
        documents=[SourceDocument(path=f"File-{index}.cs", text="x") for index in range(600)],
        occurrences=[
            Occurrence(
                path=f"File-{index}.cs",
                symbol="symbol",
                line=1,
                column=0,
                end_line=1,
                end_column=1,
                encoding="utf-32",
            )
            for index in range(600)
        ],
    )
    live = peak = 0

    def released():
        nonlocal live
        live -= 1

    def counted_array(*args, **kwargs):
        nonlocal live, peak
        result = array(*args, **kwargs)
        live += 1
        peak = max(peak, live)
        weakref.finalize(result, released)
        return result

    monkeypatch.setattr(importer, "array", counted_array)
    _, accepted = validate_artifact(source, build)
    assert len(accepted) == 600
    # Even tiny line tables have object overhead; keep live tables independent
    # of the total number of files instead of relying on a byte-only budget.
    assert peak <= 256
    assert live == 0


@pytest.mark.parametrize("encoding,width", [("utf-8", 6), ("utf-16", 3), ("utf-32", 2)])
@pytest.mark.parametrize("invalid", ["column", "end_column", "end_line", "reversed"])
def test_cached_occurrence_widths_still_reject_invalid_unicode_ranges(
    tmp_path, encoding, width, invalid
):
    _, build = make_build(tmp_path)
    valid = Occurrence(
        path="One.cs",
        symbol="symbol",
        line=1,
        column=0,
        end_line=1,
        end_column=width,
        encoding=encoding,
    )
    changes = {
        "column": {"column": width + 1},
        "end_column": {"end_column": width + 1},
        "end_line": {"end_line": 4, "end_column": 0},
        "reversed": {"line": 2, "end_line": 1},
    }[invalid]
    source = IndexArtifact(
        commit_sha=SHA,
        documents=[SourceDocument(path="One.cs", text="é🙂\nAB\n")],
        occurrences=[valid, valid.model_copy(update=changes)],
    )
    with pytest.raises(ValueError, match="SCIP (column|range) is outside"):
        validate_artifact(source, build)


@pytest.mark.performance
def test_export_streams_large_members_and_preserves_all_rows_and_checksums(tmp_path):
    service, build = make_build(tmp_path)
    coverage = import_artifact(
        service.store,
        build,
        IndexArtifact(
            commit_sha=SHA,
            documents=[SourceDocument(path="Large.cs", text="class Example {}\n" * 20000)],
        ),
    )
    service.store.update(build["id"], status="ready", coverage=coverage)
    build = service.store.get(build["id"])
    blob_bytes = 12 * 1024 * 1024
    with service.store.connect(write=True) as db:
        db.execute(
            "INSERT INTO scip_files VALUES(?,?,zeroblob(?))",
            (build["id"], "large.scip", blob_bytes),
        )
        expected_rows = [
            tuple(row)
            for row in db.execute(
                "SELECT c.id,c.path,c.line,c.end_line,c.hash,t.text FROM chunks c "
                "JOIN contents t ON c.hash=t.hash WHERE snapshot=? ORDER BY c.id",
                (build["id"],),
            )
        ]
    assert len(expected_rows) > 128

    destination = tmp_path / "export.zip"
    tracemalloc.start()
    try:
        export_snapshot(service.store, build, destination)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    # A single stored SCIP blob is larger than the complete export's working set.
    assert peak < 8 * 1024 * 1024

    with zipfile.ZipFile(destination) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert archive.getinfo("scip/large.scip").file_size == blob_bytes
        assert (
            manifest["checksums"]["scip/large.scip"]
            == hashlib.sha256(b"\0" * blob_bytes).hexdigest()
        )
        for name, checksum in manifest["checksums"].items():
            assert hashlib.sha256(archive.read(name)).hexdigest() == checksum
        archive.extract("index.sqlite", tmp_path / "portable")
    with sqlite3.connect(tmp_path / "portable/index.sqlite") as db:
        assert db.execute("SELECT * FROM chunks ORDER BY id").fetchall() == expected_rows
        assert db.execute("SELECT count(*) FROM search").fetchone()[0] == len(expected_rows)
        assert db.execute("SELECT count(*) FROM search WHERE search MATCH 'Example'").fetchone()[
            0
        ] == len(expected_rows)


def test_streaming_export_keeps_one_snapshot_when_retired_during_archive_write(
    tmp_path, monkeypatch
):
    service, build = make_build(tmp_path)
    coverage = import_artifact(
        service.store,
        build,
        IndexArtifact(
            commit_sha=SHA, documents=[SourceDocument(path="One.cs", text="class One {}")]
        ),
    )
    service.store.update(build["id"], status="ready", coverage=coverage)
    build = service.store.get(build["id"])
    original_scip = b"original snapshot bytes"
    with service.store.connect(write=True) as db:
        db.execute("INSERT INTO scip_files VALUES(?,?,?)", (build["id"], "one.scip", original_scip))
    open_member = zipfile.ZipFile.open
    retired = []

    def retire_before_first_member(archive, name, mode="r", *args, **kwargs):
        if name == "index.sqlite" and mode == "w" and not retired:
            service.store.delete_snapshot(build["id"])
            retired.append(build["id"])
        return open_member(archive, name, mode, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "open", retire_before_first_member)
    destination = tmp_path / "snapshot.zip"
    export_snapshot(service.store, build, destination)
    assert retired == [build["id"]]
    with zipfile.ZipFile(destination) as archive:
        assert archive.read("scip/one.scip") == original_scip
        manifest = json.loads(archive.read("manifest.json"))
        assert manifest["checksums"]["scip/one.scip"] == hashlib.sha256(original_scip).hexdigest()
    with pytest.raises(LookupError):
        export_snapshot(service.store, build, tmp_path / "retired.zip")

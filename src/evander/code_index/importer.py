from __future__ import annotations

import hashlib
import base64
import json
from array import array
from collections import OrderedDict

from .models import IndexArtifact, IndexSettings, TOOLCHAIN, digest, source_path
from .scip import portable_scip
from .store import IndexStore, search_terms

_LINE_WIDTH_CACHE_BYTES = 4 * 1024 * 1024
_LINE_WIDTH_CACHE_ENTRIES = 128


def validate_artifact(
    artifact: IndexArtifact, build: dict, *, tracked_blobs: dict[str, str] | None = None
) -> list:
    if artifact.commit_sha != build["sha"] or artifact.toolchain != TOOLCHAIN:
        raise ValueError("Index artifact commit or toolchain does not match the requested snapshot")
    settings = IndexSettings.model_validate(build["config"])
    if not settings.scip and (
        artifact.occurrences or artifact.relationships or artifact.projects or artifact.scip_files
    ):
        raise ValueError("SCIP data requires scip=true in the requested snapshot")
    documents = {}
    for document in artifact.documents:
        if document.path in documents:
            raise ValueError("Duplicate source document")
        if document.generated or not settings.includes_file(document.path):
            continue
        encoded = document.text.encode("utf-8")
        if len(encoded) > settings.max_file_bytes or "\x00" in document.text:
            raise ValueError("Index document exceeds source limits")
        if tracked_blobs is not None:
            expected = tracked_blobs.get(document.path)
            constructor = hashlib.sha256 if expected and len(expected) == 64 else hashlib.sha1
            actual = constructor(f"blob {len(encoded)}\0".encode() + encoded).hexdigest()
            if expected != actual:
                raise ValueError(f"Indexed document does not match Git snapshot: {document.path}")
        documents[document.path] = document
    occurrences = []
    line_widths: OrderedDict[tuple[str, str], array] = OrderedDict()
    retained_width_bytes = 0
    for occurrence in artifact.occurrences:
        document = documents.get(occurrence.path)
        if document is None:
            continue
        key = (occurrence.path, occurrence.encoding)
        widths = line_widths.get(key)
        if widths is None:
            # A document can have thousands of occurrences. Retain only line widths,
            # rather than splitting and re-encoding the source for every range.
            if occurrence.encoding == "utf-16":
                widths = array(
                    "I", (len(line.encode("utf-16-le")) // 2 for line in document.text.split("\n"))
                )
            elif occurrence.encoding == "utf-8":
                widths = array(
                    "I", (len(line.encode("utf-8")) for line in document.text.split("\n"))
                )
            else:
                widths = array("I", (len(line) for line in document.text.split("\n")))
            width_bytes = len(widths) * widths.itemsize
            if width_bytes <= _LINE_WIDTH_CACHE_BYTES:
                while (
                    retained_width_bytes + width_bytes > _LINE_WIDTH_CACHE_BYTES
                    or len(line_widths) >= _LINE_WIDTH_CACHE_ENTRIES
                ):
                    _, retired = line_widths.popitem(last=False)
                    retained_width_bytes -= len(retired) * retired.itemsize
                    del retired
                line_widths[key] = widths
                retained_width_bytes += width_bytes
        else:
            line_widths.move_to_end(key)
        if occurrence.end_line < occurrence.line or occurrence.end_line > len(widths):
            raise ValueError("SCIP range is outside its source document")
        for number, column in (
            (occurrence.line, occurrence.column),
            (occurrence.end_line, occurrence.end_column),
        ):
            if column > widths[number - 1]:
                raise ValueError("SCIP column is outside its source line")
        occurrences.append(occurrence)
    return [list(documents.values()), occurrences]


def import_artifact(
    store: IndexStore,
    build: dict,
    artifact: IndexArtifact,
    *,
    tracked_blobs: dict[str, str] | None = None,
) -> dict:
    documents, occurrences = validate_artifact(artifact, build, tracked_blobs=tracked_blobs)
    by_path = {}
    for occurrence in occurrences:
        if occurrence.definition:
            by_path.setdefault(occurrence.path, set()).add(occurrence.line - 1)
    chunk_count = 0
    with store.connect(write=True) as db:
        row = db.execute("SELECT status FROM snapshots WHERE id=?", (build["id"],)).fetchone()
        if row is None or row[0] == "cancelled":
            return {}
        db.execute(
            "INSERT OR IGNORE INTO content_gc_candidates SELECT hash FROM chunks WHERE snapshot=?",
            (build["id"],),
        )
        for table in ("chunks", "occurrences", "relationships", "scip_files"):
            db.execute(f"DELETE FROM {table} WHERE snapshot=?", (build["id"],))
        for document in documents:
            lines = document.text.splitlines(keepends=True)
            # Group nearby definitions so fields and short methods do not each incur
            # a separate embedding. Prefer a symbol boundary at least 20 lines apart.
            boundaries = [0]
            for line in sorted(by_path.get(document.path, set())):
                if line - boundaries[-1] >= 20:
                    boundaries.append(line)
            if boundaries[-1] != len(lines):
                boundaries.append(len(lines))
            for start, end in zip(boundaries, boundaries[1:]):
                for first in range(start, end, 60):
                    last = min(first + 68, end)
                    text = "".join(lines[first:last])
                    if not text.strip():
                        continue
                    content_hash = digest([document.path, text])
                    inserted = db.execute(
                        "INSERT OR IGNORE INTO contents VALUES(?,?)", (content_hash, text)
                    ).rowcount
                    if inserted:
                        search_row = db.execute(
                            "INSERT INTO content_search(hash,terms) VALUES(?,?)",
                            (content_hash, search_terms(document.path + "\n" + text)),
                        )
                        db.execute(
                            "INSERT OR REPLACE INTO content_search_rows VALUES(?,?)",
                            (content_hash, search_row.lastrowid),
                        )
                    chunk_id = digest([build["id"], document.path, first, last])
                    db.execute(
                        "INSERT INTO chunks VALUES(?,?,?,?,?,?)",
                        (chunk_id, build["id"], document.path, first + 1, last, content_hash),
                    )
                    chunk_count += 1
        db.executemany(
            "INSERT OR IGNORE INTO occurrences VALUES(?,?,?,?,?,?,?,?,?,?)",
            (
                (
                    build["id"],
                    o.path,
                    o.symbol,
                    o.line,
                    o.column,
                    o.end_line,
                    o.end_column,
                    o.encoding,
                    o.definition,
                    o.generated,
                )
                for o in occurrences
            ),
        )
        db.executemany(
            "INSERT OR IGNORE INTO relationships VALUES(?,?,?,?)",
            ((build["id"], r.symbol, r.target, r.kind) for r in artifact.relationships),
        )
        for name, encoded in artifact.scip_files.items():
            source_path(name)
            if "/" in name or not name.endswith(".scip"):
                raise ValueError("Invalid SCIP artifact name")
            data = portable_scip(
                base64.b64decode(encoded, validate=True), {d.path for d in documents}
            )
            db.execute("INSERT INTO scip_files VALUES(?,?,?)", (build["id"], name, data))
        projects = artifact.projects
        navigation = bool(projects) and all(p.get("status") == "ready" for p in projects)
        scip_enabled = IndexSettings.model_validate(build["config"]).scip
        coverage = {
            "lexical": "ready",
            "semantic": "pending" if build["config"]["semantic"] else "disabled",
            "navigation": "disabled"
            if not scip_enabled
            else "ready"
            if navigation
            else "partial"
            if occurrences
            else "unavailable",
            "projects": projects,
            "documents": len(documents),
            "occurrences": len(occurrences),
            "chunks": chunk_count,
            "navigation_documents": len({item.path for item in occurrences}),
            "navigation_languages": ["cs", "ts", "tsx"] if scip_enabled else [],
            "templates": "lexical-only",
        }
        db.execute(
            "UPDATE snapshots SET coverage=?,diagnostics=? WHERE id=?",
            (json.dumps(coverage), json.dumps(artifact.diagnostics[:100]), build["id"]),
        )
    return coverage

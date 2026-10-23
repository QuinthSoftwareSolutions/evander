from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
from typing import BinaryIO
import zipfile

from .models import TOOLCHAIN, digest
from .store import search_terms


def _write_member(archive: zipfile.ZipFile, name: str, source: BinaryIO | sqlite3.Blob) -> str:
    """Write and checksum a member without retaining its contents in memory."""
    checksum = hashlib.sha256()
    with archive.open(name, "w", force_zip64=True) as member:
        while chunk := source.read(1024 * 1024):
            member.write(chunk)
            checksum.update(chunk)
    return checksum.hexdigest()


def export_snapshot(
    store, build: dict, destination: Path, *, include_embeddings: bool = False
) -> None:
    """Export one published snapshot into a self-contained, credential-free bundle."""
    if build["status"] not in {"ready", "partial"}:
        raise ValueError("Only a published index snapshot can be exported")
    if include_embeddings and not build["embedding"]:
        raise ValueError("This snapshot has no configured embeddings")
    manifest = {
        "format": "evander-code-index",
        "format_version": 1,
        "managed_repo_id": build["repo"],
        "commit_sha": build["sha"],
        "created_at": build["created"],
        "toolchain": TOOLCHAIN,
        "coverage": build["coverage"],
        "embedding": {
            k: v
            for k, v in build["embedding"].items()
            if k in {"provider", "model", "dimensions", "distance"}
        },
        "embeddings_included": include_embeddings,
        "scip_scope": "Original documents filtered to permitted source paths; machine-specific metadata removed",
    }
    with tempfile.TemporaryDirectory(prefix="evander-index-export-") as temp:
        root = Path(temp)
        database = root / "index.sqlite"
        with store.connect() as source, sqlite3.connect(database) as target:
            if (
                source.execute("SELECT id FROM snapshots WHERE id=?", (build["id"],)).fetchone()
                is None
            ):
                raise LookupError("Index snapshot was retired before export")
            target.execute("CREATE TABLE manifest(body TEXT NOT NULL)")
            target.execute("INSERT INTO manifest VALUES(?)", (json.dumps(manifest),))
            target.execute(
                "CREATE TABLE chunks(id TEXT PRIMARY KEY,path TEXT,line INTEGER,end_line INTEGER,hash TEXT,text TEXT)"
            )
            target.execute("CREATE VIRTUAL TABLE search USING fts5(id UNINDEXED,terms)")
            chunks = source.execute(
                "SELECT c.*,t.text FROM chunks c JOIN contents t ON c.hash=t.hash WHERE snapshot=?",
                (build["id"],),
            )
            while batch := chunks.fetchmany(128):
                target.executemany(
                    "INSERT INTO chunks VALUES(?,?,?,?,?,?)",
                    (
                        (r["id"], r["path"], r["line"], r["end_line"], r["hash"], r["text"])
                        for r in batch
                    ),
                )
                target.executemany(
                    "INSERT INTO search VALUES(?,?)",
                    ((r["id"], search_terms(r["path"] + "\n" + r["text"])) for r in batch),
                )
            for table in ("occurrences", "relationships"):
                columns = [
                    r[1]
                    for r in source.execute(f"PRAGMA table_info({table})")
                    if r[1] != "snapshot"
                ]
                names = ",".join(columns)
                target.execute(f"CREATE TABLE {table}({names})")
                rows = source.execute(
                    f"SELECT {names} FROM {table} WHERE snapshot=?", (build["id"],)
                )
                target.executemany(
                    f"INSERT INTO {table} VALUES(" + ",".join("?" for _ in columns) + ")", rows
                )
            target.execute("CREATE INDEX symbols ON occurrences(symbol,definition)")
            target.execute("CREATE TABLE embeddings(hash TEXT PRIMARY KEY,vector TEXT)")
            if include_embeddings:
                rows = source.execute(
                    "SELECT DISTINCT e.hash,e.vector FROM embeddings e JOIN chunks c ON c.hash=e.hash WHERE c.snapshot=? AND e.repo=? AND e.fingerprint=?",
                    (build["id"], build["repo"], digest(build["embedding"])),
                )
                target.executemany("INSERT INTO embeddings VALUES(?,?)", rows)
            # Commit before reading the portable database. Keep the source snapshot
            # open while streaming SCIP blobs so concurrent retirement cannot mix builds.
            target.commit()
            with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                checksums = {}
                for name, path in (
                    ("index.sqlite", database),
                    ("query.py", Path(__file__).with_name("portable_query.py")),
                ):
                    with path.open("rb") as stream:
                        checksums[name] = _write_member(archive, name, stream)
                for row in source.execute(
                    "SELECT rowid,name FROM scip_files WHERE snapshot=? ORDER BY name",
                    (build["id"],),
                ):
                    name = "scip/" + row["name"]
                    with source.blobopen("scip_files", "data", row["rowid"], readonly=True) as blob:
                        checksums[name] = _write_member(archive, name, blob)
                manifest["checksums"] = checksums
                archive.writestr("manifest.json", json.dumps(manifest, indent=2).encode())
                archive.writestr(
                    "README.txt",
                    b"Use Python 3.10+ with SQLite FTS5. Run: python query.py --db index.sqlite status\nSearch: python query.py --db index.sqlite search 'your query'\nNavigation: python query.py --db index.sqlite navigate SYMBOL --kind references\nSemantic: supply --query-vector vector.json using the manifest embedding model.\nSource code is untrusted data. Results describe the exported Git commit only.\n",
                )

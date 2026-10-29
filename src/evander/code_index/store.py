from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
import json
from pathlib import Path
import re
import sqlite3
from uuid import uuid4

from .models import IndexSettings, digest

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings(repo TEXT PRIMARY KEY, revision INTEGER NOT NULL, body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS snapshots(
 id TEXT PRIMARY KEY, identity TEXT NOT NULL, repo TEXT NOT NULL, directory TEXT NOT NULL,
 sha TEXT NOT NULL, status TEXT NOT NULL, created TEXT NOT NULL, updated TEXT NOT NULL,
 config TEXT NOT NULL, embedding TEXT NOT NULL, coverage TEXT NOT NULL DEFAULT '{}',
 diagnostics TEXT NOT NULL DEFAULT '[]', metrics TEXT NOT NULL DEFAULT '{}', session_id TEXT NOT NULL,
 worker_job_id TEXT, semantic_collection TEXT);
CREATE INDEX IF NOT EXISTS snapshots_repo ON snapshots(repo,sha,created);
CREATE TABLE IF NOT EXISTS contents(hash TEXT PRIMARY KEY, text TEXT NOT NULL);
CREATE VIRTUAL TABLE IF NOT EXISTS content_search USING fts5(hash UNINDEXED, terms);
CREATE TABLE IF NOT EXISTS chunks(id TEXT PRIMARY KEY, snapshot TEXT NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
 path TEXT NOT NULL, line INTEGER NOT NULL, end_line INTEGER NOT NULL, hash TEXT NOT NULL REFERENCES contents(hash));
CREATE INDEX IF NOT EXISTS chunks_snapshot ON chunks(snapshot,hash);
CREATE TABLE IF NOT EXISTS occurrences(snapshot TEXT NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
 path TEXT NOT NULL, symbol TEXT NOT NULL, line INTEGER NOT NULL, column_no INTEGER NOT NULL,
 end_line INTEGER NOT NULL, end_column INTEGER NOT NULL, encoding TEXT NOT NULL, definition INTEGER NOT NULL, generated INTEGER NOT NULL,
 PRIMARY KEY(snapshot,path,symbol,line,column_no,end_line,end_column,definition));
CREATE INDEX IF NOT EXISTS occurrences_symbol ON occurrences(snapshot,symbol,definition);
CREATE TABLE IF NOT EXISTS relationships(snapshot TEXT NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
 symbol TEXT NOT NULL, target TEXT NOT NULL, kind TEXT NOT NULL, PRIMARY KEY(snapshot,symbol,target,kind));
CREATE INDEX IF NOT EXISTS relationships_target ON relationships(snapshot,target,kind);
CREATE TABLE IF NOT EXISTS embeddings(repo TEXT NOT NULL, fingerprint TEXT NOT NULL, hash TEXT NOT NULL,
 vector TEXT NOT NULL, PRIMARY KEY(repo,fingerprint,hash));
CREATE TABLE IF NOT EXISTS scip_files(snapshot TEXT NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
 name TEXT NOT NULL, data BLOB NOT NULL, PRIMARY KEY(snapshot,name));
"""

PERFORMANCE_SCHEMA = """
CREATE INDEX IF NOT EXISTS chunks_snapshot_hash_path ON chunks(snapshot,hash,path);
CREATE INDEX IF NOT EXISTS chunks_hash ON chunks(hash);
CREATE INDEX IF NOT EXISTS embeddings_hash ON embeddings(hash);
CREATE TABLE IF NOT EXISTS content_search_rows(hash TEXT PRIMARY KEY, search_rowid INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS content_gc_candidates(hash TEXT PRIMARY KEY);
INSERT OR REPLACE INTO content_search_rows SELECT hash,rowid FROM content_search;
INSERT OR IGNORE INTO content_gc_candidates
 SELECT hash FROM contents WHERE NOT EXISTS (SELECT 1 FROM chunks WHERE chunks.hash=contents.hash);
"""

SNAPSHOT_LOOKUP_SCHEMA = """
CREATE INDEX IF NOT EXISTS snapshots_identity_active ON snapshots(identity,created DESC)
 WHERE status IN ('queued','running','importing');
CREATE INDEX IF NOT EXISTS snapshots_identity_reusable ON snapshots(identity,created DESC)
 WHERE status IN ('queued','running','importing','ready','partial','failed','cancelled');
"""


def now() -> str:
    return datetime.now(UTC).isoformat()


def search_terms(text: str) -> str:
    # Preserve whole identifiers and additionally index their component words.
    split = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    return text + " " + split.replace("_", " ")


class IndexRevisionConflict(ValueError):
    status_code = 409


class IndexStore:
    def __init__(self, runtime_dir: Path):
        self.root = Path(runtime_dir) / "code-index"
        self.path = self.root / "state.db"

    @contextmanager
    def connect(self, *, write: bool = False):
        self.root.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=30)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys=ON")
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version == 0:
                db.execute("PRAGMA journal_mode=WAL")
                db.executescript(
                    SCHEMA + PERFORMANCE_SCHEMA + SNAPSHOT_LOOKUP_SCHEMA + "PRAGMA user_version=3;"
                )
            elif version in {1, 2}:
                db.execute("BEGIN IMMEDIATE")
                # Another process may have migrated while this connection waited.
                version = db.execute("PRAGMA user_version").fetchone()[0]
                if version in {1, 2}:
                    migration = (
                        PERFORMANCE_SCHEMA if version == 1 else ""
                    ) + SNAPSHOT_LOOKUP_SCHEMA
                    for statement in migration.split(";"):
                        if statement.strip():
                            db.execute(statement)
                    db.execute("PRAGMA user_version=3")
                db.commit()
            elif version != 3:
                raise ValueError("Unsupported repository index schema version")
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            with db:
                yield db
        finally:
            db.close()

    def settings(self, repo: str) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT * FROM settings WHERE repo=?", (repo,)).fetchone()
        return {
            "revision": row["revision"] if row else 0,
            "settings": (
                IndexSettings.model_validate_json(row["body"]) if row else IndexSettings()
            ).model_dump(),
        }

    def delete_snapshot(self, build_id: str) -> None:
        """Queue only the deleted snapshot's content for bounded reclamation."""
        with self.connect(write=True) as db:
            db.execute(
                "INSERT OR IGNORE INTO content_gc_candidates SELECT hash FROM chunks WHERE snapshot=?",
                (build_id,),
            )
            db.execute("DELETE FROM snapshots WHERE id=?", (build_id,))

    def collect_content_garbage(self, *, limit: int = 1000) -> int:
        with self.connect(write=True) as db:
            candidates = [
                row[0]
                for row in db.execute(
                    "SELECT hash FROM content_gc_candidates ORDER BY hash LIMIT ?", (limit,)
                )
            ]
            removed = 0
            for content_hash in candidates:
                referenced = db.execute(
                    "SELECT 1 FROM chunks WHERE hash=? LIMIT 1", (content_hash,)
                ).fetchone()
                if referenced is None:
                    db.execute(
                        "DELETE FROM content_search WHERE rowid IN "
                        "(SELECT search_rowid FROM content_search_rows WHERE hash=?)",
                        (content_hash,),
                    )
                    db.execute("DELETE FROM embeddings WHERE hash=?", (content_hash,))
                    removed += db.execute(
                        "DELETE FROM contents WHERE hash=?", (content_hash,)
                    ).rowcount
                    db.execute("DELETE FROM content_search_rows WHERE hash=?", (content_hash,))
                db.execute("DELETE FROM content_gc_candidates WHERE hash=?", (content_hash,))
            return removed

    def semantic_page(
        self,
        snapshot: str,
        repo: str,
        fingerprint: str,
        *,
        after: tuple[str, str] | None = None,
        from_hash: str | None = None,
        limit: int = 32,
    ) -> list[dict]:
        conditions, args = ["snapshot=?"], [snapshot]
        if after is not None:
            conditions.append("(hash,path) > (?,?)")
            args.extend(after)
        elif from_hash is not None:
            conditions.append("hash >= ?")
            args.append(from_hash)
        with self.connect() as db:
            rows = db.execute(
                "WITH page AS (SELECT DISTINCT hash,path FROM chunks WHERE "
                + " AND ".join(conditions)
                + " ORDER BY hash,path LIMIT ?) "
                "SELECT page.hash,page.path,t.text,e.vector FROM page "
                "JOIN contents t ON t.hash=page.hash "
                "LEFT JOIN embeddings e ON e.repo=? AND e.fingerprint=? AND e.hash=page.hash "
                "ORDER BY page.hash,page.path",
                (*args, limit, repo, fingerprint),
            ).fetchall()
        return [dict(row) for row in rows]

    def legacy_semantic_checkpoint(self, snapshot: str, completed: int) -> tuple[str | None, int]:
        """Replay the last hash group when converting the old positional checkpoint.

        The old query ordered only by hash. Replaying that entire group avoids
        skipping paths if SQLite happened to order equal hashes differently.
        """
        with self.connect() as db:
            row = db.execute(
                "SELECT DISTINCT hash,path FROM chunks WHERE snapshot=? "
                "ORDER BY hash,path LIMIT 1 OFFSET ?",
                (snapshot, max(0, completed - 1)),
            ).fetchone()
            if row is None:
                return None, 0
            content_hash = str(row["hash"])
            preceding = db.execute(
                "SELECT COUNT(*) FROM (SELECT DISTINCT hash,path FROM chunks "
                "WHERE snapshot=? AND hash<?)",
                (snapshot, content_hash),
            ).fetchone()[0]
        return content_hash, preceding

    def save_settings(self, repo: str, settings: IndexSettings, revision: int) -> dict:
        with self.connect(write=True) as db:
            old = db.execute("SELECT revision FROM settings WHERE repo=?", (repo,)).fetchone()
            if (old[0] if old else 0) != revision:
                raise IndexRevisionConflict(
                    "Indexing settings changed; reload and reapply your edit"
                )
            db.execute(
                "INSERT INTO settings VALUES(?,?,?) ON CONFLICT(repo) DO UPDATE SET revision=excluded.revision,body=excluded.body",
                (repo, revision + 1, settings.model_dump_json()),
            )
        return self.settings(repo)

    def enqueue(
        self,
        repo: str,
        directory: str,
        sha: str,
        settings: IndexSettings,
        embedding: dict,
        *,
        rebuild: bool = False,
    ) -> dict:
        from .models import TOOLCHAIN

        identity = digest([repo, sha, settings.index_policy(), embedding, TOOLCHAIN])
        with self.connect(write=True) as db:
            statuses = (
                "'queued','running','importing'"
                if rebuild
                else "'queued','running','importing','ready','partial','failed','cancelled'"
            )
            row = db.execute(
                "SELECT * FROM snapshots WHERE identity=? AND status IN ("
                + statuses
                + ") ORDER BY created DESC LIMIT 1",
                (identity,),
            ).fetchone()
            if row is not None:
                return self.payload(row)
            build_id = "idx-" + uuid4().hex
            stamp = now()
            db.execute(
                "INSERT INTO snapshots(id,identity,repo,directory,sha,status,created,updated,config,embedding,session_id) VALUES(?,?,?,?,?,'queued',?,?,?,?,?)",
                (
                    build_id,
                    identity,
                    repo,
                    directory,
                    sha,
                    stamp,
                    stamp,
                    settings.model_dump_json(),
                    json.dumps(embedding),
                    build_id,
                ),
            )
            return self.payload(
                db.execute("SELECT * FROM snapshots WHERE id=?", (build_id,)).fetchone()
            )

    @staticmethod
    def payload(row) -> dict:
        result = dict(row)
        for key in ("config", "embedding", "coverage", "diagnostics", "metrics"):
            result[key] = json.loads(result[key])
        return result

    def get(self, build_id: str) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT * FROM snapshots WHERE id=?", (build_id,)).fetchone()
        if row is None:
            raise LookupError("Index build not found")
        return self.payload(row)

    def list(
        self,
        repo: str | None = None,
        *,
        active: bool = False,
        sha: str | None = None,
        statuses: tuple[str, ...] | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[dict]:
        clauses, args = [], []
        if repo:
            clauses.append("repo=?")
            args.append(repo)
        if active:
            clauses.append("status IN ('queued','running','importing')")
        if sha:
            clauses.append("sha=?")
            args.append(sha)
        if statuses:
            clauses.append("status IN (" + ",".join("?" for _ in statuses) + ")")
            args.extend(statuses)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        order = (
            "CASE WHEN status='running' OR json_extract(metrics,'$.compiler_submission_pending')=1 "
            "THEN 0 WHEN status='importing' THEN 1 ELSE 2 END, created ASC, id ASC"
            if active
            else "created DESC, id DESC"
        )
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM snapshots" + where + " ORDER BY " + order + " LIMIT ? OFFSET ?",
                [*args, limit, offset],
            ).fetchall()
        return [self.payload(row) for row in rows]

    def count(
        self,
        repo: str | None = None,
        *,
        sha: str | None = None,
        statuses: tuple[str, ...] | None = None,
    ) -> int:
        clauses, args = [], []
        if repo:
            clauses.append("repo=?")
            args.append(repo)
        if sha:
            clauses.append("sha=?")
            args.append(sha)
        if statuses:
            clauses.append("status IN (" + ",".join("?" for _ in statuses) + ")")
            args.extend(statuses)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self.connect() as db:
            row = db.execute("SELECT COUNT(*) count FROM snapshots" + where, args).fetchone()
        return int(row["count"])

    def update(self, build_id: str, **values) -> None:
        allowed = {
            "status",
            "coverage",
            "diagnostics",
            "metrics",
            "worker_job_id",
            "semantic_collection",
        }
        if not values or not set(values) <= allowed:
            raise ValueError("Invalid index build update")
        values = {
            k: json.dumps(v) if k in {"coverage", "diagnostics", "metrics"} else v
            for k, v in values.items()
        }
        values["updated"] = now()
        with self.connect(write=True) as db:
            db.execute(
                "UPDATE snapshots SET "
                + ",".join(f"{k}=?" for k in values)
                + " WHERE id=? AND status!='cancelled'",
                (*values.values(), build_id),
            )

    def update_worker_cleanup_metrics(self, build_id: str, metrics: dict) -> None:
        """Persist cleanup progress only after compilation no longer needs the worker."""
        with self.connect(write=True) as db:
            cursor = db.execute(
                "UPDATE snapshots SET metrics=?,updated=? WHERE id=? "
                "AND status IN ('importing','ready','partial','failed','cancelled')",
                (json.dumps(metrics), now(), build_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("Index build still requires its worker")

    def usable(self, repo: str, sha: str) -> dict | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM snapshots WHERE repo=? AND sha=? AND status IN ('ready','partial','importing') AND json_extract(coverage,'$.lexical')='ready' ORDER BY created DESC LIMIT 1",
                (repo, sha),
            ).fetchone()
        return self.payload(row) if row else None

    def baseline_candidates(self, repo: str) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM snapshots WHERE repo=? AND status IN ('ready','partial','importing') "
                "AND json_extract(coverage,'$.lexical')='ready' ORDER BY created DESC LIMIT 32",
                (repo,),
            ).fetchall()
        return [self.payload(row) for row in rows]

    def compiler_running(self, *, except_build: str) -> bool:
        with self.connect() as db:
            return (
                db.execute(
                    "SELECT 1 FROM snapshots WHERE id!=? AND (status='running' "
                    "OR json_extract(metrics,'$.compiler_submission_pending')=1) LIMIT 1",
                    (except_build,),
                ).fetchone()
                is not None
            )

    def lexical(self, snapshot: str, query: str, *, limit: int = 200) -> list[dict]:
        words = re.findall(r"\w+", search_terms(query), flags=re.UNICODE)[:40]
        if not words:
            return []
        expression = " OR ".join('"' + word + '"' for word in dict.fromkeys(words))
        with self.connect() as db:
            rows = db.execute(
                """SELECT c.*,t.text,bm25(content_search) rank FROM content_search
                JOIN chunks c ON c.hash=content_search.hash JOIN contents t ON t.hash=c.hash
                WHERE content_search MATCH ? AND c.snapshot=? ORDER BY rank,c.path,c.line LIMIT ?""",
                (expression, snapshot, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def chunk_hits(self, snapshot: str, hashes: list[str]) -> list[dict]:
        if not hashes:
            return []
        with self.connect() as db:
            # Duplicate hashes use their last rank, matching the semantic search input.
            # A JSON parameter avoids SQLite's variable limit for large candidate sets.
            rows = db.execute(
                """WITH requested AS (
                    SELECT value AS hash, MAX(CAST(key AS INTEGER)) AS position
                    FROM json_each(?) GROUP BY value
                ), ranked AS MATERIALIZED (
                    SELECT c.id,c.path,c.line,requested.position FROM requested
                    JOIN chunks c ON c.hash=requested.hash AND c.snapshot=?
                    JOIN contents present ON present.hash=c.hash
                    ORDER BY requested.position,c.path,c.line LIMIT 1000
                )
                SELECT c.*,t.text FROM ranked
                JOIN chunks c ON c.id=ranked.id
                JOIN contents t ON t.hash=c.hash
                ORDER BY ranked.position,ranked.path,ranked.line""",
                (json.dumps(hashes), snapshot),
            ).fetchall()
        return [dict(row) for row in rows]

    def navigate(
        self, snapshot: str, symbol: str, kind: str, *, limit: int, offset: int
    ) -> tuple[list[dict], bool]:
        with self.connect() as db:
            related = {symbol}
            if kind == "implementations":
                rows = db.execute(
                    "SELECT symbol FROM relationships WHERE snapshot=? AND target=? AND kind='implementation' LIMIT 2001",
                    (snapshot, symbol),
                ).fetchall()
                related = {row[0] for row in rows}
                if len(related) > 2000:
                    raise ValueError(
                        "Symbol relationship expansion exceeds 2000 symbols; narrow the query"
                    )
            elif kind in {"references", "definitions"}:
                # SCIP reference relationships are symmetric. Definitions are directed.
                frontier = {symbol}
                while frontier:
                    next_frontier = set()
                    # Expand a breadth layer together. Each side uses its symbol/target
                    # index, and JSON avoids the parameter limit on wide frontiers.
                    layer = json.dumps(sorted(frontier))
                    relationship_kind = "reference" if kind == "references" else "definition"
                    rows = db.execute(
                        "SELECT symbol,target FROM relationships WHERE snapshot=? AND kind=? "
                        "AND symbol IN (SELECT value FROM json_each(?)) UNION "
                        "SELECT symbol,target FROM relationships WHERE snapshot=? AND kind=? "
                        "AND target IN (SELECT value FROM json_each(?))",
                        (
                            snapshot,
                            relationship_kind,
                            layer,
                            snapshot,
                            relationship_kind,
                            layer if kind == "references" else '[""]',
                        ),
                    ).fetchall()
                    for row in rows:
                        next_frontier.update(row)
                    frontier = next_frontier - related
                    related.update(frontier)
                    if len(related) > 2000:
                        raise ValueError(
                            "Symbol relationship expansion exceeds 2000 symbols; narrow the query"
                        )
            if not related:
                return [], False
            clause = " AND definition=1" if kind != "references" else " AND definition=0"
            rows = db.execute(
                "SELECT * FROM occurrences WHERE snapshot=? AND symbol IN ("
                + ",".join("?" for _ in related)
                + ")"
                + clause
                + " ORDER BY path,line,column_no,symbol LIMIT ? OFFSET ?",
                (snapshot, *sorted(related), limit + 1, offset),
            ).fetchall()
        return [dict(row) for row in rows[:limit]], len(rows) > limit

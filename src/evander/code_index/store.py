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
                db.executescript(SCHEMA + "PRAGMA user_version=1;")
            elif version != 1:
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
            rows = db.execute(
                "SELECT * FROM snapshots WHERE identity=? ORDER BY created DESC", (identity,)
            ).fetchall()
            for row in rows:
                if row["status"] in {"queued", "running", "importing"} or (
                    not rebuild and row["status"] in {"ready", "partial", "failed", "cancelled"}
                ):
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
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        order = (
            "CASE WHEN status='running' OR json_extract(metrics,'$.compiler_submission_pending')=1 "
            "THEN 0 WHEN status='importing' THEN 1 ELSE 2 END, created ASC"
            if active
            else "created DESC"
        )
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM snapshots" + where + " ORDER BY " + order + " LIMIT ? OFFSET ?",
                [*args, limit, offset],
            ).fetchall()
        return [self.payload(row) for row in rows]

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
            rows = db.execute(
                "SELECT c.*,t.text FROM chunks c JOIN contents t ON t.hash=c.hash WHERE c.snapshot=? AND c.hash IN ("
                + ",".join("?" for _ in hashes)
                + ") ORDER BY c.path,c.line",
                (snapshot, *hashes),
            ).fetchall()
        rank = {value: i for i, value in enumerate(hashes)}
        return sorted((dict(row) for row in rows), key=lambda row: rank[row["hash"]])[:1000]

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
                    for current in frontier:
                        rows = db.execute(
                            "SELECT symbol,target FROM relationships WHERE snapshot=? AND kind=? AND (symbol=? OR target=?)",
                            (
                                snapshot,
                                "reference" if kind == "references" else "definition",
                                current,
                                current if kind == "references" else "",
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

"""Search an exported Evander code index using only Python's standard library.

Usage: python query.py --db index.sqlite search "durable event retries"
       python query.py --db index.sqlite navigate SYMBOL --kind references
       python query.py --db index.sqlite search QUERY --query-vector vector.json

Vectors are supplied by the caller using the embedding model in manifest.json.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import re
import sqlite3


def open_index(path: Path):
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    manifest = json.loads(db.execute("SELECT body FROM manifest").fetchone()[0])
    if manifest.get("format") != "evander-code-index" or manifest.get("format_version") != 1:
        db.close()
        raise ValueError("Unsupported exported index format")
    return db


def search(db, query: str, *, limit: int = 10, offset: int = 0, vector=None) -> dict:
    manifest = json.loads(db.execute("SELECT body FROM manifest").fetchone()[0])
    terms = re.findall(r"\w+", query + " " + re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", query))[:40]
    expression = " OR ".join('"' + word + '"' for word in dict.fromkeys(terms))
    rows = (
        db.execute(
            "SELECT c.*,bm25(search) rank FROM search JOIN chunks c ON c.id=search.id WHERE search MATCH ? ORDER BY rank,c.path,c.line LIMIT 1000",
            (expression,),
        ).fetchall()
        if expression
        else []
    )
    scores, hits = {}, {}
    for i, row in enumerate(rows):
        scores[row["id"]] = 1 / (61 + i)
        hits[row["id"]] = dict(row)
    if vector is not None:
        if not manifest.get("embeddings_included"):
            raise ValueError("This export does not include embeddings")
        dimensions = manifest["embedding"].get("dimensions")
        if len(vector) != dimensions or not all(
            isinstance(v, (int, float)) and math.isfinite(v) for v in vector
        ):
            raise ValueError("Query vector must match the manifest's embedding dimensions")
        vector_hits = []
        distance = manifest["embedding"].get("distance", "Cosine")
        for row in db.execute(
            "SELECT c.*,e.vector FROM chunks c JOIN embeddings e ON c.hash=e.hash"
        ):
            embedding = json.loads(row["vector"])
            if distance in {"Dot", "Cosine"}:
                score = sum(x * y for x, y in zip(vector, embedding))
                if distance == "Cosine":
                    norm = math.sqrt(sum(x * x for x in vector) * sum(y * y for y in embedding))
                    score = score / norm if norm else 0
            elif distance == "Euclid":
                score = -sum((x - y) ** 2 for x, y in zip(vector, embedding))
            else:
                score = -sum(abs(x - y) for x, y in zip(vector, embedding))
            vector_hits.append((score, dict(row)))
        for i, (_, row) in enumerate(sorted(vector_hits, key=lambda r: -r[0])[:1000]):
            scores[row["id"]] = scores.get(row["id"], 0) + 1 / (61 + i)
            hits[row["id"]] = row
    ordered = sorted(scores, key=lambda key: (-scores[key], hits[key]["path"], hits[key]["line"]))
    output = []
    for key in ordered[offset : offset + limit]:
        row = hits[key]
        output.append(
            {
                "path": row["path"],
                "line": row["line"],
                "end_line": row["end_line"],
                "snippet": row["text"][:1600],
                "symbols": [
                    r[0]
                    for r in db.execute(
                        "SELECT DISTINCT symbol FROM occurrences WHERE path=? AND line BETWEEN ? AND ? AND definition=1 LIMIT 8",
                        (row["path"], row["line"], row["end_line"]),
                    )
                ],
            }
        )
    return {
        "commit_sha": manifest["commit_sha"],
        "coverage": manifest["coverage"],
        "hits": output,
        "next_offset": offset + limit if offset + limit < len(ordered) else None,
    }


def navigate(
    db, symbol: str, *, kind: str = "definitions", limit: int = 20, offset: int = 0
) -> dict:
    symbols = {symbol}
    if kind == "implementations":
        symbols = {
            r[0]
            for r in db.execute(
                "SELECT symbol FROM relationships WHERE target=? AND kind='implementation' LIMIT 2001",
                (symbol,),
            )
        }
        if len(symbols) > 2000:
            raise ValueError("Relationship expansion exceeds 2000 symbols")
    else:
        frontier = {symbol}
        while frontier:
            found = set()
            for value in frontier:
                for row in db.execute(
                    "SELECT symbol,target FROM relationships WHERE kind=? AND (symbol=? OR target=?)",
                    (
                        "reference" if kind == "references" else "definition",
                        value,
                        value if kind == "references" else "",
                    ),
                ):
                    found.update(row)
            frontier = found - symbols
            symbols.update(frontier)
            if len(symbols) > 2000:
                raise ValueError("Relationship expansion exceeds 2000 symbols")
    rows = (
        db.execute(
            "SELECT * FROM occurrences WHERE symbol IN ("
            + ",".join("?" for _ in symbols)
            + ") AND definition=? ORDER BY path,line,column_no LIMIT ? OFFSET ?",
            (*sorted(symbols), int(kind != "references"), limit + 1, offset),
        ).fetchall()
        if symbols
        else []
    )
    manifest = json.loads(db.execute("SELECT body FROM manifest").fetchone()[0])
    return {
        "commit_sha": manifest["commit_sha"],
        "coverage": manifest["coverage"],
        "hits": [dict(r) for r in rows[:limit]],
        "next_offset": offset + limit if len(rows) > limit else None,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=Path("index.sqlite"))
    sub = parser.add_subparsers(dest="operation", required=True)
    sub.add_parser("status")
    for name in ("search", "navigate"):
        command = sub.add_parser(name)
        command.add_argument("query")
        command.add_argument("--limit", type=int, default=10)
        command.add_argument("--offset", type=int, default=0)
        if name == "search":
            command.add_argument("--query-vector", type=Path)
        else:
            command.add_argument(
                "--kind",
                choices=("definitions", "references", "implementations"),
                default="definitions",
            )
    args = parser.parse_args()
    with open_index(args.db) as db:
        if args.operation == "status":
            result = json.loads(db.execute("SELECT body FROM manifest").fetchone()[0])
        else:
            if not 1 <= args.limit <= 50 or args.offset < 0:
                parser.error("limit must be 1..50; offset must be nonnegative")
            if args.operation == "search":
                vector = json.loads(args.query_vector.read_text()) if args.query_vector else None
                result = search(db, args.query, limit=args.limit, offset=args.offset, vector=vector)
            else:
                result = navigate(
                    db, args.query, kind=args.kind, limit=args.limit, offset=args.offset
                )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Evaluate a worker artifact against its unchanged checkout; never compile on the host."""

from __future__ import annotations

import argparse
import fnmatch
import json
from pathlib import Path
import re
import statistics
import subprocess
import time

from evander.admin.services.repository_index_service import RepositoryIndexService
from evander.code_index.export import export_snapshot
from evander.code_index.importer import import_artifact
from evander.code_index.models import IndexArtifact, IndexSettings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--settings", type=Path, required=True)
    parser.add_argument(
        "--cases", type=Path, default=Path("tests/fixtures/code-index/app-queries.json")
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    artifact = IndexArtifact.model_validate_json(args.artifact.read_text())
    settings = IndexSettings.model_validate_json(args.settings.read_text()).model_copy(
        update={"enabled": True, "semantic": False}
    )
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=args.repo).decode().strip()
    if head != artifact.commit_sha or subprocess.check_output(
        ["git", "diff", "--name-only", "HEAD"], cwd=args.repo
    ):
        raise ValueError("Benchmark checkout must match the artifact's exact commit")
    repo_id = "mr_" + artifact.commit_sha[:20]

    class Git:
        def get_repository(self, repo, directory_path):
            if repo != repo_id or directory_path != "root":
                raise PermissionError("Outside benchmark")
            return {"directory_path": "root"}

    service = RepositoryIndexService(args.output / "runtime", Git())
    saved = service.store.settings(repo_id)
    service.save_settings(
        repo_id, "root", settings.model_dump(), expected_revision=saved["revision"]
    )
    build = service.store.enqueue(repo_id, "root", head, settings, {})
    blobs = {}
    for entry in subprocess.check_output(["git", "ls-tree", "-r", "-z", head], cwd=args.repo).split(
        b"\0"
    ):
        if not entry:
            continue
        metadata, path = entry.split(b"\t", 1)
        mode, kind, sha = metadata.split()
        if mode in {b"100644", b"100755"} and kind == b"blob":
            blobs[path.decode()] = sha.decode()
    started = time.monotonic()
    coverage = import_artifact(service.store, build, artifact, tracked_blobs=blobs)
    import_seconds = time.monotonic() - started
    service.store.update(
        build["id"], status="ready" if coverage["navigation"] == "ready" else "partial"
    )
    rows = []
    allowed = [
        path
        for path in blobs
        if settings.includes_file(path)
        and (args.repo / path).stat().st_size <= settings.max_file_bytes
    ]
    for case in json.loads(args.cases.read_text()):
        result = service.search(repo_id, "root", head, case["query"], mode="lexical", limit=5)
        patterns = case["expected_paths"]

        def matches(path):
            return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)

        expressions = [re.escape(word) for word in case["query"].split()]
        start = time.monotonic()
        grep = subprocess.run(
            ["rg", "-i", "-n", "--no-heading", "--", "|".join(expressions), *allowed],
            cwd=args.repo,
            capture_output=True,
            timeout=30,
        )
        if grep.returncode not in (0, 1):
            raise RuntimeError("ripgrep benchmark failed")
        baseline_ms = (time.monotonic() - start) * 1000
        symbols = [symbol for hit in result["hits"] for symbol in hit.get("symbols", [])]
        nav = service.navigate(repo_id, "root", head, symbols[0]) if symbols else {"hits": []}
        rows.append(
            {
                "query": case["query"],
                "top5_expected_path": any(matches(hit["path"]) for hit in result["hits"]),
                "paths": [hit["path"] for hit in result["hits"]],
                "index_ms": result["duration_ms"],
                "rg_ms": round(baseline_ms, 2),
                "index_response_bytes": len(json.dumps(result).encode()),
                "rg_all_matches_bytes": len(grep.stdout),
                "definition_available": bool(nav["hits"]),
            }
        )
    build = service.store.get(build["id"])
    export_snapshot(service.store, build, args.output / "index.zip")
    report = {
        "commit_sha": head,
        "mode": "lexical",
        "semantic_evaluated": False,
        "agent_savings_measured": False,
        "coverage": coverage,
        "compiler_project_ms": sum(row.get("duration_ms", 0) for row in artifact.projects),
        "import_seconds": round(import_seconds, 3),
        "artifact_bytes": args.artifact.stat().st_size,
        "sqlite_bytes": service.store.path.stat().st_size,
        "export_bytes": (args.output / "index.zip").stat().st_size,
        "expected_path_at_5": sum(row["top5_expected_path"] for row in rows),
        "cases": len(rows),
        "median_index_ms": statistics.median(row["index_ms"] for row in rows),
        "median_rg_ms": statistics.median(row["rg_ms"] for row in rows),
        "comparison_note": "Index returns five snippets; rg returns all matching lines. Byte counts are not measured agent token or cost savings.",
        "queries": rows,
    }
    (args.output / "report.json").write_text(json.dumps(report, indent=2))
    print(
        json.dumps(
            {key: value for key, value in report.items() if key not in {"queries", "coverage"}},
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

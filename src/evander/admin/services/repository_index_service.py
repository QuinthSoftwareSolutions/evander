from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
import fcntl
import logging
from pathlib import Path
import subprocess
import time

from evander.code_index.importer import import_artifact
from evander.code_index.models import (
    IndexArtifact,
    IndexSettings,
    MAX_ARTIFACT_BYTES,
    commit_id,
    digest,
    TOOLCHAIN,
)
from evander.code_index.store import IndexStore

logger = logging.getLogger(__name__)


class RepositoryIndexService:
    def __init__(
        self,
        runtime_dir: Path,
        managed_git,
        *,
        worker=None,
        semantic=None,
        protected_snapshots=None,
    ):
        self.store = IndexStore(runtime_dir)
        self.managed_git = managed_git
        self.worker = worker
        self.semantic = semantic
        self.protected_snapshots = protected_snapshots

    def authorize(self, repo: str, directory: str) -> dict:
        return self.managed_git.get_repository(repo, directory_path=directory)

    def settings(self, repo: str, directory: str) -> dict:
        self.authorize(repo, directory)
        return self.store.settings(repo)

    def save_settings(
        self, repo: str, directory: str, payload: dict, *, expected_revision: int
    ) -> dict:
        record = self.authorize(repo, directory)
        if record["directory_path"] != directory:
            raise PermissionError(
                "Index settings must be changed in the repository's owning directory"
            )
        settings = IndexSettings.model_validate(payload)
        return self.store.save_settings(repo, settings, expected_revision)

    def ensure(
        self,
        repo: str,
        directory: str,
        sha: str | None = None,
        *,
        rebuild: bool = False,
        for_task: bool = False,
    ) -> dict:
        record = self.authorize(repo, directory)
        settings = IndexSettings.model_validate(self.store.settings(repo)["settings"])
        if not settings.enabled:
            return {"status": "disabled", "managed_repo_id": repo}
        if for_task:
            if sha is None:
                raise ValueError("Team index reads require the assigned commit")
            return self.query_status(repo, directory, sha)
        pinned = self.managed_git.resolve_snapshot(
            repo,
            directory,
            commit_sha=commit_id(sha) if sha else None,
            source_ref=None if sha else settings.tracked_ref,
        )
        embedding = (
            self.semantic.fingerprint(record["directory_path"])
            if settings.semantic and self.semantic
            else {}
        )
        build = self.store.enqueue(
            repo,
            record["directory_path"],
            pinned["commit_sha"],
            settings,
            embedding,
            rebuild=rebuild,
        )
        if build["status"] not in {"cancelled", "failed"}:
            self.managed_git.pin_retained_snapshot(
                repo,
                record["directory_path"],
                binding_kind="code_indexes",
                binding_id=build["id"],
                pin_ref="refs/evander/code-index/" + build["id"],
                commit_sha=build["sha"],
            )
        return self.public(build)

    @staticmethod
    def public(build: dict) -> dict:
        return {
            key: value
            for key, value in build.items()
            if key
            not in {"embedding", "session_id", "worker_job_id", "semantic_collection", "identity"}
        }

    def status(self, repo: str, directory: str, sha: str | None = None) -> dict:
        self.authorize(repo, directory)
        builds = self.store.list(repo, sha=commit_id(sha) if sha is not None else None)
        result = {
            "managed_repo_id": repo,
            **self.store.settings(repo),
            "builds": [self.public(b) for b in builds],
            "status": builds[0]["status"] if builds else "unavailable",
        }
        if sha is not None:
            result["available_index"] = self.query_status(repo, directory, sha)
        return result

    def cancel(self, repo: str, directory: str, build_id: str) -> dict:
        self.authorize(repo, directory)
        with self._lifecycle_lock():
            return self._cancel(repo, build_id)

    def _cancel(self, repo: str, build_id: str) -> dict:
        build = self.store.get(build_id)
        if build["repo"] != repo:
            raise LookupError("Index build not found")
        pending = build["metrics"].get("compiler_submission_pending", False)
        if build["status"] in {"queued", "running", "importing"} or pending:
            if build["worker_job_id"] or pending:
                if self.worker is None:
                    raise RuntimeError("An indexing worker is required to confirm the job stopped")
                self.worker.stop(build)
            self.store.update(
                build_id,
                status="cancelled",
                metrics={**build["metrics"], "compiler_submission_pending": False},
            )
        return self.public(self.store.get(build_id))

    def _tracked_blobs(self, build: dict) -> dict[str, str]:
        bare, _ = self.managed_git.resolve_internal_repository(build["repo"], build["directory"])
        result = subprocess.run(
            ["git", "--git-dir", str(bare), "ls-tree", "-r", "-z", build["sha"]],
            check=True,
            capture_output=True,
            timeout=30,
        )
        blobs = {}
        for entry in result.stdout.split(b"\0"):
            if not entry:
                continue
            metadata, path = entry.split(b"\t", 1)
            mode, kind, sha = metadata.split()
            if mode in {b"100644", b"100755"} and kind == b"blob":
                blobs[path.decode("utf-8")] = sha.decode()
        return blobs

    @contextmanager
    def _lifecycle_lock(self, *, wait: bool = True):
        """Serialize submission, cancellation and cleanup across processes/instances."""
        self.store.root.mkdir(parents=True, exist_ok=True)
        with (self.store.root / "reconcile.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
            except BlockingIOError:
                yield False
                return
            yield True

    def reconcile(self, directory: str, *, limit: int = 4) -> dict:
        """One bounded pass. A filesystem lease prevents duplicate external submissions."""
        with self._lifecycle_lock(wait=False) as acquired:
            if not acquired:
                return {"status": "busy", "processed": 0}
            processed, repos = [], set()
            self._refresh_requested_refs(directory)
            for build in self.store.list(active=True):
                if len(processed) >= min(max(limit, 1), 20):
                    break
                try:
                    self.authorize(build["repo"], directory)
                except (ValueError, LookupError, PermissionError):
                    continue
                if build["repo"] in repos:
                    continue
                # One compiler job across the runtime, including other directories.
                if build["status"] == "queued" and self.store.compiler_running(
                    except_build=build["id"]
                ):
                    continue
                repos.add(build["repo"])
                try:
                    self._advance(build)
                except Exception as exc:
                    logger.exception("Repository index build %s failed", build["id"])
                    # Provider exception bodies can contain credentials or source text.
                    metrics = dict(self.store.get(build["id"])["metrics"])
                    metrics["failures"] = int(metrics.get("failures", 0)) + 1
                    if metrics["failures"] >= 3:
                        metrics["compiler_submission_pending"] = bool(
                            metrics.get("compiler_submission_pending") or build["worker_job_id"]
                        )
                        try:
                            if self.worker and metrics["compiler_submission_pending"]:
                                self.worker.stop(self.store.get(build["id"]))
                                metrics["compiler_submission_pending"] = False
                        except Exception:
                            logger.exception("Unable to stop failed index worker %s", build["id"])
                    self.store.update(
                        build["id"],
                        status="failed" if metrics["failures"] >= 3 else build["status"],
                        metrics=metrics,
                        diagnostics=[
                            f"Indexing failed ({type(exc).__name__}); attempt {metrics['failures']} of 3; build {build['id']}"
                        ],
                    )
                processed.append(build["id"])
            self._maintenance(directory)
            return {"status": "ok", "processed": len(processed), "build_ids": processed}

    def _refresh_requested_refs(self, directory: str) -> None:
        with self.store.connect() as db:
            records = db.execute("SELECT repo,body FROM settings").fetchall()
        for record in records:
            settings = IndexSettings.model_validate_json(record["body"])
            if not settings.enabled or not settings.refresh_on_git_change:
                continue
            try:
                # Resolve the latest ref only when another baseline build is due.
                # Intermediate main commits never accumulate compiler jobs.
                if self.store.list(record["repo"], active=True, limit=1):
                    continue
                recent = self.store.list(record["repo"], limit=1)
                if recent:
                    last = recent[0]
                    started = datetime.fromisoformat(
                        last["metrics"].get("started_at", last["created"])
                    )
                    if datetime.now(UTC) - started < timedelta(
                        seconds=settings.refresh_interval_seconds
                    ):
                        continue
                self.ensure(record["repo"], directory)
            except (ValueError, LookupError, PermissionError):
                continue

    def maintenance(self, directory: str) -> dict:
        with self._lifecycle_lock():
            return self._maintenance(directory)

    def _maintenance(self, directory: str) -> dict:
        from evander_team_addon.code_index import protected_bindings

        protected = (
            self.protected_snapshots()
            if self.protected_snapshots
            else protected_bindings(self.store.root.parent)
        )
        deleted = []
        latest = set()
        builds, offset = [], 0
        while page := self.store.list(offset=offset):
            builds.extend(page)
            offset += len(page)
        build_directories = {
            build["repo"]: build["directory"]
            for build in builds
            if build.get("directory")
        }
        resolved_protected = set(protected)
        for repo, requested_sha in protected:
            build_directory = build_directories.get(repo)
            if build_directory is None:
                continue
            try:
                resolved = self.query_snapshot(
                    repo,
                    build_directory,
                    requested_sha,
                    allow_baseline=True,
                )
            except (ValueError, LookupError, PermissionError):
                continue
            if resolved is not None:
                resolved_protected.add((repo, resolved["sha"]))
        protected = resolved_protected
        for build in builds:
            if build["status"] in {"ready", "partial"} and build["repo"] not in latest:
                latest.add(build["repo"])
                continue
            if (
                build["status"] in {"queued", "running", "importing"}
                or build["metrics"].get("compiler_submission_pending")
                or (build["repo"], build["sha"]) in protected
            ):
                continue
            age = datetime.now(UTC) - datetime.fromisoformat(build["created"])
            if age < timedelta(
                days=IndexSettings.model_validate(
                    self.store.settings(build["repo"])["settings"]
                ).retention_days
            ):
                continue
            try:
                self.authorize(build["repo"], directory)
            except (ValueError, LookupError, PermissionError):
                continue
            if build["semantic_collection"] and self.semantic:
                try:
                    self.semantic.delete(self.store, build)
                except Exception:
                    logger.warning("Deferring index cleanup for %s", build["id"])
                    continue
            if self.worker:
                try:
                    self.worker.cleanup(build)
                except Exception:
                    logger.warning("Deferring worker cleanup for %s", build["id"])
                    continue
            with self.store.connect(write=True) as db:
                db.execute("DELETE FROM snapshots WHERE id=?", (build["id"],))
                db.execute("DELETE FROM content_search WHERE hash NOT IN (SELECT hash FROM chunks)")
                db.execute("DELETE FROM embeddings WHERE hash NOT IN (SELECT hash FROM chunks)")
                db.execute("DELETE FROM contents WHERE hash NOT IN (SELECT hash FROM chunks)")
            deleted.append(build["id"])
        return {"deleted": deleted}

    def _advance(self, build: dict) -> None:
        if not self.worker:
            raise RuntimeError("No indexing worker configured")
        settings = IndexSettings.model_validate(build["config"])
        current = IndexSettings.model_validate(self.store.settings(build["repo"])["settings"])
        if not current.enabled or current.index_policy() != settings.index_policy():
            self._cancel(build["repo"], build["id"])
            return
        if build["status"] == "queued":
            # Apply the current CPU budget even to previously queued snapshots.
            build = {**build, "config": {**build["config"], "worker_cpus": current.worker_cpus}}
            self.store.update(
                build["id"], metrics={**build["metrics"], "worker_provisioning_started": True}
            )

            def reserve_submission():
                self.store.update(
                    build["id"],
                    metrics={
                        **self.store.get(build["id"])["metrics"],
                        "compiler_submission_pending": True,
                        "compiler_submission_attempted": True,
                    },
                )

            # Keep any earlier uncertain reservation during retries; fresh setup
            # failures do not reserve compiler capacity. Cancellation holds this lease too.
            job_id = self.worker.start(build, before_submit=reserve_submission)
            self.store.update(
                build["id"],
                status="running",
                worker_job_id=job_id,
                metrics={
                    **self.store.get(build["id"])["metrics"],
                    "started_at": datetime.now(UTC).isoformat(),
                    "compiler_submission_pending": False,
                },
            )
            return
        if build["status"] == "running":
            if datetime.now(UTC) - datetime.fromisoformat(
                build["metrics"].get("started_at", build["created"])
            ) > timedelta(seconds=settings.timeout_seconds + 120):
                self.worker.stop(build)
                raise TimeoutError("Repository indexing timed out")
            state = self.worker.status(build)
            if state in {"failed", "stopped", "cancelled"}:
                raise RuntimeError("Indexing worker failed")
            if state != "completed":
                return
            raw = self.worker.artifact(build)
            if len(raw.encode()) > MAX_ARTIFACT_BYTES:
                raise ValueError("Index artifact exceeds import limit")
            artifact = IndexArtifact.model_validate_json(raw)
            import_artifact(self.store, build, artifact, tracked_blobs=self._tracked_blobs(build))
            self.store.update(build["id"], status="importing")
            build = self.store.get(build["id"])
        if build["status"] == "importing":
            coverage = dict(build["coverage"])
            if settings.semantic:
                try:
                    if self.semantic is None:
                        raise RuntimeError("No semantic provider configured")
                    if not self.semantic.index_batch(self.store, build):
                        return
                    coverage["semantic"] = "ready"
                except Exception as exc:
                    coverage["semantic"] = "unavailable"
                    self.store.update(
                        build["id"],
                        diagnostics=[
                            *build["diagnostics"],
                            f"Semantic indexing unavailable ({type(exc).__name__}); rebuild to retry",
                        ],
                    )
            ready = coverage.get("navigation") == "ready" and coverage.get("semantic") in {
                "ready",
                "disabled",
            }
            self.store.update(
                build["id"], status="ready" if ready else "partial", coverage=coverage
            )

    def snapshot(self, repo: str, directory: str, sha: str) -> dict | None:
        self.authorize(repo, directory)
        if not self.store.settings(repo)["settings"]["enabled"]:
            return None
        build = self.store.usable(repo, commit_id(sha))
        return build if build and self.policy_matches(build) else None

    def policy_matches(self, build: dict) -> bool:
        current = IndexSettings.model_validate(self.store.settings(build["repo"])["settings"])
        return current.enabled and build["identity"] == digest(
            [build["repo"], build["sha"], current.index_policy(), build["embedding"], TOOLCHAIN]
        )

    def query_snapshot(
        self, repo: str, directory: str, sha: str, *, allow_baseline: bool = False
    ) -> dict | None:
        """Resolve existing coverage without ever submitting an indexing job."""
        sha = commit_id(sha)
        build = self.snapshot(repo, directory, sha)
        if build or not allow_baseline:
            return build
        candidates = [b for b in self.store.baseline_candidates(repo) if self.policy_matches(b)]
        if not candidates:
            return None
        bare, _ = self.managed_git.resolve_internal_repository(repo, directory)
        deadline = time.monotonic() + 2
        for candidate in candidates:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                result = subprocess.run(
                    [
                        "git",
                        "--git-dir",
                        str(bare),
                        "merge-base",
                        "--is-ancestor",
                        candidate["sha"],
                        sha,
                    ],
                    capture_output=True,
                    timeout=remaining,
                )
            except subprocess.TimeoutExpired:
                break
            if result.returncode == 0:
                return candidate
            if result.returncode != 1:
                break  # Missing objects and other Git errors cannot establish provenance.
        return None

    @staticmethod
    def query_provenance(build: dict, sha: str) -> dict:
        baseline = build["sha"] != sha
        return {
            "snapshot_id": build["id"],
            "managed_repo_id": build["repo"],
            "commit_sha": build["sha"],
            "requested_commit_sha": sha,
            "match": "baseline" if baseline else "exact",
            "coverage": build["coverage"],
            "warnings": [
                "Results use an indexed ancestor. Changed and deleted files are excluded; "
                "search new and changed code in the checkout. Symbol relationships describe "
                "the indexed commit and require verification even in unchanged files."
            ]
            if baseline
            else [],
        }

    def query_status(
        self, repo: str, directory: str, sha: str, *, allow_baseline: bool = True
    ) -> dict:
        sha = commit_id(sha)
        build = self.query_snapshot(repo, directory, sha, allow_baseline=allow_baseline)
        if build is None:
            return {"status": "unavailable", "managed_repo_id": repo, "requested_commit_sha": sha}
        return {"status": build["status"], **self.query_provenance(build, sha)}

    def _changed_paths(self, build: dict, directory: str, sha: str) -> set[str]:
        if build["sha"] == sha:
            return set()
        bare, _ = self.managed_git.resolve_internal_repository(build["repo"], directory)
        result = subprocess.run(
            [
                "git",
                "--git-dir",
                str(bare),
                "diff",
                "--name-only",
                "-z",
                "--no-renames",
                "--no-ext-diff",
                build["sha"],
                sha,
                "--",
            ],
            check=True,
            capture_output=True,
            timeout=5,
        )
        return {path.decode("utf-8") for path in result.stdout.split(b"\0") if path}

    def export(
        self,
        repo: str,
        directory: str,
        build_id: str,
        destination: Path,
        *,
        include_embeddings: bool = False,
    ) -> None:
        from evander.code_index.export import export_snapshot

        self.authorize(repo, directory)
        build = self.store.get(build_id)
        if build["repo"] != repo:
            raise LookupError("Index build not found")
        if not self.policy_matches(build):
            raise ValueError(
                "Index policy changed or indexing disabled; build a current snapshot before exporting"
            )
        export_snapshot(self.store, build, destination, include_embeddings=include_embeddings)

    @staticmethod
    def _page(limit: int, offset: int) -> None:
        if not 1 <= limit <= 50 or not 0 <= offset <= 100000:
            raise ValueError("limit must be 1..50 and offset must be 0..100000")

    def search(
        self,
        repo: str,
        directory: str,
        sha: str,
        query: str,
        *,
        mode: str = "hybrid",
        limit: int = 10,
        offset: int = 0,
        allow_baseline: bool = False,
    ) -> dict:
        sha = commit_id(sha)
        self._page(limit, offset)
        if not query.strip() or len(query) > 2000 or mode not in {"hybrid", "lexical", "semantic"}:
            raise ValueError(
                "Use a nonempty query up to 2000 characters and lexical, semantic, or hybrid mode"
            )
        started = time.monotonic()
        build = self.query_snapshot(repo, directory, sha, allow_baseline=allow_baseline)
        if build is None:
            return {"status": "unavailable", "commit_sha": sha, "hits": []}
        provenance = self.query_provenance(build, sha)
        changed = self._changed_paths(build, directory, sha)
        ranked, warnings = [], provenance["warnings"]
        if mode != "semantic":
            ranked.append(self.store.lexical(build["id"], query, limit=1000))
        if mode != "lexical":
            if build["coverage"].get("semantic") == "ready" and self.semantic:
                try:
                    ranked.append(
                        self.store.chunk_hits(
                            build["id"], self.semantic.search(build, query, limit=1000)
                        )
                    )
                except Exception:
                    warnings.append("Semantic search unavailable; lexical results only")
            else:
                warnings.append("Semantic coverage unavailable")
        scores, hits = {}, {}
        for results in ranked:
            for position, row in enumerate(results):
                if row["path"] in changed:
                    continue
                scores[row["id"]] = scores.get(row["id"], 0) + 1 / (61 + position)
                hits[row["id"]] = row
        ordered = sorted(
            scores, key=lambda key: (-scores[key], hits[key]["path"], hits[key]["line"])
        )
        result = []
        for key in ordered[offset : offset + limit]:
            row = hits[key]
            item = {k: row[k] for k in ("path", "line", "end_line")}
            item.update(
                {
                    "snippet": row["text"][:1600],
                    "snippet_truncated": len(row["text"]) > 1600,
                    "score": scores[key],
                    "file_matches_requested_commit": True,
                }
            )
            with self.store.connect() as db:
                item["symbols"] = [
                    r[0]
                    for r in db.execute(
                        "SELECT DISTINCT symbol FROM occurrences WHERE snapshot=? AND path=? AND line BETWEEN ? AND ? AND definition=1 LIMIT 8",
                        (build["id"], row["path"], row["line"], row["end_line"]),
                    )
                ]
            result.append(item)
        return {
            "status": build["status"],
            **provenance,
            "warnings": warnings,
            "hits": result,
            "next_offset": offset + limit if offset + limit < len(ordered) else None,
            "candidate_limit": 1000,
            "duration_ms": round((time.monotonic() - started) * 1000, 2),
        }

    def navigate(
        self,
        repo: str,
        directory: str,
        sha: str,
        symbol: str,
        *,
        kind: str = "definitions",
        limit: int = 20,
        offset: int = 0,
        allow_baseline: bool = False,
    ) -> dict:
        sha = commit_id(sha)
        self._page(limit, offset)
        if (
            kind not in {"definitions", "references", "implementations"}
            or not symbol
            or len(symbol) > 4096
        ):
            raise ValueError(
                "A symbol and definitions, references, or implementations kind are required"
            )
        build = self.query_snapshot(repo, directory, sha, allow_baseline=allow_baseline)
        if build is None:
            return {"status": "unavailable", "commit_sha": sha, "hits": []}
        provenance = self.query_provenance(build, sha)
        if build["sha"] != sha:
            changed = self._changed_paths(build, directory, sha)
            candidates, _ = self.store.navigate(build["id"], symbol, kind, limit=1000, offset=0)
            candidates = [hit for hit in candidates if hit["path"] not in changed]
            hits = candidates[offset : offset + limit]
            more = offset + limit < len(candidates)
            provenance["candidate_limit"] = 1000
        else:
            hits, more = self.store.navigate(build["id"], symbol, kind, limit=limit, offset=offset)
        for hit in hits:
            hit["file_matches_requested_commit"] = True
        return {
            "status": build["status"],
            **provenance,
            "hits": hits,
            "next_offset": offset + limit if more else None,
        }

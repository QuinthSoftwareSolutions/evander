from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
import subprocess

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
import pytest

from evander.admin.services.repository_index_service import RepositoryIndexService
from evander.code_index.importer import import_artifact
from evander.code_index.models import IndexSettings, SourceDocument
from evander.gateway.code_index_tools import register_code_index_tools
from evander.models import SessionManifest, ToolName
from evander_team_addon.code_index import prepare_indexes
from test_repository_index import ManagedGit, REPO, SHA, OTHER_SHA, TEXT, artifact


@pytest.fixture
def indexed_history(tmp_path):
    checkout = tmp_path / "source"
    checkout.mkdir()

    def git(*args):
        return subprocess.run(
            [
                "git",
                "-c",
                "core.hooksPath=/dev/null",
                "-c",
                "commit.gpgsign=false",
                "-c",
                "user.name=Index test",
                "-c",
                "user.email=index@example.test",
                *args,
            ],
            cwd=checkout,
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        ).stdout.strip()

    git("init", "--initial-branch=main")
    paths = ["Domain/Retry.cs", "Domain/Changed.cs", "Domain/Deleted.cs", "Domain/Renamed.cs"]
    (checkout / "Domain").mkdir()
    for path in paths:
        (checkout / path).write_text(TEXT)
    git("add", ".")
    git("commit", "-m", "Baseline")
    base = git("rev-parse", "HEAD")

    class Repository(ManagedGit):
        def resolve_internal_repository(self, repo, directory):
            self.get_repository(repo, directory)
            return checkout / ".git", "main"

        def resolve_snapshot(self, repo, directory, **kwargs):
            self.get_repository(repo, directory)
            return {"commit_sha": kwargs.get("commit_sha") or git("rev-parse", "main")}

    service = RepositoryIndexService(tmp_path / "runtime", Repository())
    service.save_settings(
        REPO,
        "root/app",
        IndexSettings(enabled=True, semantic=False, refresh_on_git_change=False).model_dump(),
        expected_revision=0,
    )
    build = service.store.get(service.ensure(REPO, "root/app", base)["id"])
    data = artifact(base)
    data.documents = [SourceDocument(path=path, text=TEXT) for path in paths]
    data.occurrences = [
        row.model_copy(update={"path": path}) for path in paths for row in data.occurrences
    ]
    coverage = import_artifact(
        service.store, build, data, tracked_blobs=service._tracked_blobs(build)
    )
    service.store.update(build["id"], status="ready", coverage=coverage)

    git("checkout", "-b", "review")
    (checkout / "Domain/Changed.cs").write_text(TEXT + "// Review change\n")
    (checkout / "Domain/Deleted.cs").unlink()
    (checkout / "Domain/Renamed.cs").rename(checkout / "Domain/Moved.cs")
    (checkout / "Domain/New.cs").write_text(TEXT)
    git("add", "-A")
    git("commit", "-m", "Review candidate")
    target = git("rev-parse", "HEAD")
    return SimpleNamespace(
        service=service, base=base, target=target, git=git, checkout=checkout, build=build
    )


def test_baseline_search_and_navigation_filter_changes_with_honest_provenance(indexed_history):
    history = indexed_history
    service = history.service
    assert service.search(REPO, "root/app", history.target, "retry")["status"] == "unavailable"
    for mode in ("lexical", "hybrid"):
        result = service.search(
            REPO, "root/app", history.target, "retry", mode=mode, allow_baseline=True
        )
        assert {hit["path"] for hit in result["hits"]} == {"Domain/Retry.cs"}
        assert result["commit_sha"] == history.base
        assert result["requested_commit_sha"] == history.target
        assert result["match"] == "baseline"
        assert result["warnings"]
        assert result["hits"][0]["file_matches_requested_commit"] is True
        assert result["next_offset"] is None
    for kind, symbol in [
        ("definitions", "DurableRetry#"),
        ("references", "DurableRetry#"),
        ("implementations", "IRetry#"),
    ]:
        result = service.navigate(
            REPO, "root/app", history.target, symbol, kind=kind, allow_baseline=True, limit=1
        )
        assert [hit["path"] for hit in result["hits"]] == ["Domain/Retry.cs"]
        assert result["match"] == "baseline"
        assert result["commit_sha"] == history.base
        assert result["next_offset"] is None
        assert "relationships" in result["warnings"][0]
    assert len(service.store.list(REPO)) == 1


def test_semantic_baseline_queries_existing_vectors_without_a_new_projection(indexed_history):
    history = indexed_history
    service = history.service
    with service.store.connect() as db:
        hashes = [
            row[0]
            for row in db.execute(
                "SELECT hash FROM chunks WHERE snapshot=?", (history.build["id"],)
            )
        ]
    queried = []

    def search(build, query, **kwargs):
        queried.append(build["id"])
        return hashes

    service.semantic = SimpleNamespace(search=search)
    build = service.store.get(history.build["id"])
    service.store.update(build["id"], coverage={**build["coverage"], "semantic": "ready"})
    result = service.search(
        REPO, "root/app", history.target.upper(), "retry", mode="semantic", allow_baseline=True
    )
    assert queried == [history.build["id"]]
    assert [hit["path"] for hit in result["hits"]] == ["Domain/Retry.cs"]
    assert result["requested_commit_sha"] == history.target
    assert len(service.store.list(REPO)) == 1


@pytest.mark.parametrize("legacy_setting", [False, True])
def test_team_preparation_never_queues_a_compiler_build(indexed_history, legacy_setting):
    history = indexed_history
    service = history.service
    settings = service.settings(REPO, "root/app")
    service.save_settings(
        REPO,
        "root/app",
        {**settings["settings"], "index_task_commits": legacy_setting},
        expected_revision=settings["revision"],
    )
    project_repo = {
        "managed_repo_id": REPO,
        "review_repository_snapshots": [{"commit_sha": history.target}],
    }
    context = SimpleNamespace(
        admin_service=SimpleNamespace(repository_index_admin=service),
        metadata={"directory_path": "root/app"},
    )
    prepare_indexes(context, project_repo)
    status = project_repo["code_indexes"][0]
    assert status["commit_sha"] == history.target
    assert status["indexed_commit_sha"] == history.base
    assert status["match"] == "baseline"
    assert status["snapshot_id"] == history.build["id"]
    assert service.reconcile("root/app")["processed"] == 0
    assert len(service.store.list(REPO)) == 1
    service.store.update(history.build["id"], status="failed")
    assert (
        service.ensure(REPO, "root/app", history.target, for_task=True)["status"] == "unavailable"
    )
    assert len(service.store.list(REPO)) == 1


def test_baseline_requires_proven_ancestry_and_current_policy(indexed_history):
    history = indexed_history
    service = history.service
    history.git("checkout", "--orphan", "unrelated")
    history.git("add", "-A")
    history.git("commit", "-m", "Unrelated history")
    unrelated = history.git("rev-parse", "HEAD")
    for target in (unrelated, "0" * 40):
        assert service.query_status(REPO, "root/app", target)["status"] == "unavailable"
    with pytest.raises(PermissionError):
        service.query_status(REPO, "root/other", history.target)
    settings = service.settings(REPO, "root/app")
    service.save_settings(
        REPO,
        "root/app",
        {**settings["settings"], "excludes": ["Domain/*.cs"]},
        expected_revision=settings["revision"],
    )
    assert service.query_status(REPO, "root/app", history.target)["status"] == "unavailable"


def test_gateway_keeps_review_scope_while_reusing_an_ancestor(
    indexed_history, monkeypatch, tmp_path
):
    history = indexed_history
    service = history.service
    session = SessionManifest(
        session_id="reviewer",
        directory_path="root/app",
        allowed_tools=[ToolName.CODE_SEARCH, ToolName.CODE_INDEX_STATUS, ToolName.CODE_NAVIGATE],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        metadata={"managed_git_access_grants": {REPO: {"access_mode": "read-only"}}},
    )
    components = SimpleNamespace(
        repository_index_admin=service, managed_git_admin=service.managed_git, runtime_dir=tmp_path
    )
    bindings = {(REPO, history.target)}
    monkeypatch.setattr("evander_team_addon.code_index.session_bindings", lambda *args: bindings)
    server = FastMCP("baseline-test")
    audits = []
    register_code_index_tools(
        server,
        SimpleNamespace(policy=SimpleNamespace(require_tool=lambda *args: session)),
        SimpleNamespace(admin_runtime_factory=lambda: components),
        audit_success=lambda *args: audits.append(args),
        audit_failure=lambda *args: None,
    )
    args = {"managed_repo_id": REPO, "session_id": "reviewer"}
    for tool, extra in [
        ("code_search", {"query": "retry"}),
        ("code_index_status", {}),
        ("code_navigate", {"symbol": "DurableRetry#"}),
    ]:
        asyncio.run(server.call_tool(tool, {**args, **extra}))
        assert audits[-1][-1]["commit_sha"] == history.base
        assert audits[-1][-1]["requested_commit_sha"] == history.target
        assert audits[-1][-1]["match"] == "baseline"
    with pytest.raises(ToolError, match="immutable Team"):
        asyncio.run(
            server.call_tool("code_search", {**args, "query": "retry", "commit_sha": history.base})
        )
    bindings.clear()
    with pytest.raises(ToolError, match="immutable Team"):
        asyncio.run(server.call_tool("code_search", {**args, "query": "retry"}))
    assert len(service.store.list(REPO)) == 1


def test_automatic_refresh_skips_intermediate_heads_and_respects_cooldown(tmp_path):
    managed = ManagedGit()
    head = SHA
    managed.resolve_snapshot = lambda *args, **kwargs: {"commit_sha": head}
    service = RepositoryIndexService(tmp_path, managed)
    service.save_settings(
        REPO,
        "root/app",
        IndexSettings(enabled=True, semantic=False).model_dump(),
        expected_revision=0,
    )
    service._refresh_requested_refs("root/app")
    first = service.store.list(REPO)[0]
    head = OTHER_SHA
    service._refresh_requested_refs("root/app")
    assert len(service.store.list(REPO)) == 1  # Active compiler work suppresses another request.
    service.store.update(first["id"], status="ready")
    service._refresh_requested_refs("root/app")
    assert len(service.store.list(REPO)) == 1  # The minimum interval still applies.
    head = "c" * 40
    service.store.update(
        first["id"], metrics={"started_at": (datetime.now(UTC) - timedelta(hours=2)).isoformat()}
    )
    service._refresh_requested_refs("root/app")
    assert {build["sha"] for build in service.store.list(REPO)} == {SHA, head}


def test_only_one_compiler_job_runs_across_repositories(tmp_path):
    other_repo = "mr_" + "2" * 20
    managed = ManagedGit()
    managed.get_repository = lambda repo, directory_path=None: {
        "managed_repo_id": repo,
        "directory_path": "root/app",
    }
    started = []
    worker = SimpleNamespace(
        start=lambda build: started.append(build) or build["id"],
        status=lambda build: "running",
    )
    service = RepositoryIndexService(tmp_path, managed, worker=worker)
    settings = IndexSettings(enabled=True, semantic=False, refresh_on_git_change=False).model_dump()
    for repo in (REPO, other_repo):
        service.save_settings(repo, "root/app", settings, expected_revision=0)
        service.ensure(repo, "root/app", SHA)
    service.save_settings(REPO, "root/app", {**settings, "worker_cpus": 0.5}, expected_revision=1)
    service.reconcile("root/app")
    assert len(started) == 1
    assert started[0]["config"]["worker_cpus"] == 0.5
    service.reconcile("root/app")
    assert len(started) == 1
    service.store.update(started[0]["id"], status="ready")
    service.reconcile("root/app")
    assert len(started) == 2


def test_uncertain_worker_submission_reserves_capacity_until_confirmed_stopped(tmp_path):
    other_repo = "mr_" + "2" * 20
    managed = ManagedGit()
    managed.get_repository = lambda repo, directory_path=None: {
        "managed_repo_id": repo,
        "directory_path": "root/app",
    }
    submitted = []

    def lost_response(build):
        submitted.append(build["id"])
        raise TimeoutError("Worker may have accepted this job")

    def failed_stop(build):
        raise TimeoutError("Stop could not be confirmed")

    worker = SimpleNamespace(start=lost_response, stop=failed_stop)
    service = RepositoryIndexService(tmp_path, managed, worker=worker)
    for repo in (REPO, other_repo):
        service.save_settings(
            repo,
            "root/app",
            IndexSettings(enabled=True, semantic=False, refresh_on_git_change=False).model_dump(),
            expected_revision=0,
        )
        service.ensure(repo, "root/app", SHA)
    for _ in range(3):
        service.reconcile("root/app")
    first = service.store.list(REPO)[0]
    assert submitted == [first["id"]] * 3
    assert first["status"] == "failed"
    with service.store.connect(write=True) as db:
        db.execute(
            "UPDATE snapshots SET created=? WHERE id=?",
            ((datetime.now(UTC) - timedelta(days=40)).isoformat(), first["id"]),
        )
    worker.cleanup = lambda build: None
    service.reconcile("root/app")
    assert len(submitted) == 3  # Another repository still cannot consume the reserved slot.
    assert service.store.get(first["id"])["metrics"]["compiler_submission_pending"]
    worker.stop = lambda build: None
    service.cancel(REPO, "root/app", first["id"])
    worker.start = lambda build: submitted.append(build["id"]) or build["id"]
    service.reconcile("root/app")
    assert len(submitted) == 4
    assert service.store.get(submitted[-1])["repo"] == other_repo


def test_running_compiler_remains_visible_past_a_large_queue(tmp_path):
    service = RepositoryIndexService(tmp_path, ManagedGit())
    settings = IndexSettings(enabled=True, semantic=False, refresh_on_git_change=False)
    for i in range(201):
        service.store.enqueue(REPO, "root/app", f"{i:040x}", settings, {})
    running = service.store.enqueue(REPO, "root/app", SHA, settings, {})
    service.store.update(running["id"], status="running")
    assert service.store.list(active=True)[0]["id"] == running["id"]

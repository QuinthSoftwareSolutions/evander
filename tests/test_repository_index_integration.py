from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import json
import subprocess
from types import SimpleNamespace
import zipfile

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
import pytest
from starlette.applications import Starlette

from evander.admin.web.repository_index import repository_index_routes
from evander.code_index.access import authorize_session
from evander.code_index.models import IndexSettings
from evander.code_index.semantic import IndexSemantic
from evander.code_index.worker import IndexWorker
from evander.effects.registry import EffectProviderFactory
from evander.events.models import WorkflowNodeEnvelope
from evander.execution.container_worker_client import (
    ContainerWorkerClient,
    ContainerWorkerClientError,
)
from evander.gateway.code_index_tools import register_code_index_tools
from evander.models import SessionManifest, ToolName
from evander.provider_config import EffectProviderConfig, QueryProviderConfig
from evander.queries.providers.base import QueryRuntimeContext
from evander.queries.registry import QueryProviderFactory
from evander.semantic.openai_embeddings import OpenAIEmbeddingClient
from evander.semantic.qdrant import QdrantClient
from test_repository_index import OTHER_SHA, REPO, SHA, publish, ManagedGit
from evander.admin.services.repository_index_service import RepositoryIndexService


@pytest.fixture
def service(tmp_path):
    result = RepositoryIndexService(tmp_path, ManagedGit())
    result.save_settings(
        REPO,
        "root/app",
        IndexSettings(enabled=True, scip=True, semantic=False, refresh_on_git_change=False).model_dump(),
        expected_revision=0,
    )
    return result


@pytest.mark.parametrize("disable_scip", [False, True])
def test_current_policy_applies_to_queries_exports_and_pending_builds(service, tmp_path, disable_scip):
    published = publish(service)
    pending = service.ensure(REPO, "root/app", OTHER_SHA)
    settings = service.settings(REPO, "root/app")
    if disable_scip:
        settings["settings"]["scip"] = False
    else:
        settings["settings"]["excludes"].append("Domain/**")
    service.save_settings(
        REPO, "root/app", settings["settings"], expected_revision=settings["revision"]
    )
    assert service.search(REPO, "root/app", SHA, "retry")["status"] == "unavailable"
    with pytest.raises(ValueError, match="policy changed"):
        service.export(REPO, "root/app", published["id"], tmp_path / "forbidden.zip")
    service.worker = SimpleNamespace(start=lambda _: pytest.fail("Obsolete build started"))
    service._advance(service.store.get(pending["id"]))
    assert service.store.get(pending["id"])["status"] == "cancelled"


def test_failed_build_requires_explicit_retry(service):
    build = service.ensure(REPO, "root/app", SHA)
    service.store.update(build["id"], status="failed")
    assert service.ensure(REPO, "root/app", SHA)["id"] == build["id"]
    assert service.ensure(REPO, "root/app", SHA, rebuild=True)["id"] != build["id"]


def test_retention_preserves_team_binding_and_newest_published_snapshot(service):
    protected = publish(service)
    newest = publish(service, OTHER_SHA)
    failed = service.ensure(REPO, "root/app", "c" * 40)
    service.store.update(failed["id"], status="failed")
    service.protected_snapshots = lambda: {(REPO, SHA)}
    with service.store.connect(write=True) as db:
        for age, build in ((47, protected), (46, newest), (45, failed)):
            db.execute(
                "UPDATE snapshots SET created=? WHERE id=?",
                ((datetime.now(UTC) - timedelta(days=age)).isoformat(), build["id"]),
            )
    assert service.maintenance("root/app")["deleted"] == [failed["id"]]
    assert service.store.get(protected["id"])
    assert service.store.get(newest["id"])


def test_provider_factories_materialize_index_nodes_and_enforce_directory_scope(service):
    publish(service)
    config = SimpleNamespace(
        resolve_query_provider_config=lambda _: QueryProviderConfig(
            runtime_dir=service.store.root.parent
        ),
        resolve_effect_provider_config=lambda _: EffectProviderConfig(
            runtime_dir=service.store.root.parent,
            session_store_path=service.store.root.parent / "sessions.json",
            default_session_ttl_seconds=3600,
        ),
    )
    admin = SimpleNamespace(repository_index_admin=service)
    factory = QueryProviderFactory(config, admin_service=admin)
    node = WorkflowNodeEnvelope(
        id="find",
        display_name="Find",
        type="query:code-index:search",
        config={"managed_repo_id": REPO, "commit_sha": SHA, "query": "retry"},
    )
    assert factory.build(node, directory_path="root/app").execute()["hits"]
    with pytest.raises(ValueError, match="outside workflow directory"):
        factory.build(node, directory_path="root/app/team").execute(
            resolved_config={"directory_path": "root/app"}
        )
    effect = WorkflowNodeEnvelope(
        id="ensure",
        display_name="Ensure",
        type="effect:code-index:ensure",
        config={"managed_repo_id": REPO, "commit_sha": SHA},
    )
    assert (
        EffectProviderFactory(config, admin_service=admin)
        .build(effect, directory_path="root/app")
        .execute()["status"]
        == "ready"
    )


@pytest.mark.parametrize("scip", [False, True])
def test_worker_uses_clone_path_pinned_commit_and_terminal_metadata(service, monkeypatch, scip):
    saved = service.settings(REPO, "root/app")
    service.save_settings(
        REPO, "root/app", {**saved["settings"], "scip": scip}, expected_revision=saved["revision"]
    )
    build = service.store.get(service.ensure(REPO, "root/app", SHA)["id"])
    effects, jobs, writes = [], [], []
    resolved_image = "registry.test/code-index@sha256:" + "1" * 64

    class Factory:
        def __init__(self, **kwargs):
            pass

        def build(self, definition, **kwargs):
            effects.append(definition)
            return SimpleNamespace(
                execute=lambda: {
                    "target_dir": "/home/agent/repos/APP",
                    "container_image": resolved_image,
                }
            )

    def missing(_):
        raise ContainerWorkerClientError("Missing", status_code=404)

    client = SimpleNamespace(
        get=missing,
        write_files=lambda *args: writes.append(args[-1]),
        create=lambda job: jobs.append(job) or SimpleNamespace(job_id=job.job_id),
    )
    worker = IndexWorker(
        SimpleNamespace(
            runtime=SimpleNamespace(provider_config=None),
            managed_git_admin=SimpleNamespace(
                pin_retained_snapshot=lambda *args, **kwargs: {"pin_ref": kwargs["pin_ref"]}
            ),
        )
    )
    monkeypatch.setattr("evander.code_index.worker.EffectProviderFactory", Factory)
    monkeypatch.setattr(worker, "client", lambda _: client)
    reservations = []
    assert (
        worker.start(build, before_submit=lambda: reservations.append(build["id"])) == build["id"]
    )
    assert effects[-1].pinned_commit_sha == SHA
    assert effects[-1].git_access_mode == "read-only"
    assert jobs[0].working_directory == "/home/agent/repos/APP"
    assert "/home/agent/repos/APP" in jobs[0].command[-1]
    assert not effects[0].allowed_tools
    assert effects[0].nuget is scip
    assert ("export DOTNET_ROOT=" in jobs[0].command[-1]) is scip
    assert effects[0].container_image == "code-index"
    assert jobs[0].image == resolved_image
    assert reservations == [build["id"]]
    client.get = lambda _: SimpleNamespace(status="exited", metadata={"exit_code": 0}, error=None)
    assert worker.status(build) == "completed"
    client.get = lambda _: SimpleNamespace(status="exited", metadata={"exit_code": 137}, error=None)
    assert worker.status(build) == "failed"
    client.get = lambda _: SimpleNamespace(job_id=build["id"])
    assert (
        worker.start(build, before_submit=lambda: pytest.fail("Adoption resubmitted"))
        == build["id"]
    )
    assert len(jobs) == 1  # Restart adopts the original job.
    assert jobs[0].resource_limits.cpus == 1
    runner_settings = json.loads(writes[0].files[0].content)
    assert runner_settings["scip"] is scip
    assert "worker_cpus" not in runner_settings
    assert "refresh_interval_seconds" not in runner_settings
    assert runner_settings["timeout_seconds"] == build["config"]["timeout_seconds"]


@pytest.mark.parametrize("selector", ["code-index", "custom-index", "unknown-index"])
def test_worker_provisions_approved_alias_and_submits_resolved_image(
    service, monkeypatch, tmp_path, selector
):
    from evander.config import GatewayConfig
    from evander.repositories.session_manifests import JsonSessionManifestRepository
    from test_effect_registry import _ContainerResolverStub, _FakeContainerBackend

    resolved_image = "registry.test/evander-index:approved"
    config = GatewayConfig(
        runtime_dir=tmp_path,
        container_worker_base_url="http://worker.test",
        container_worker_default_image="runner-base:latest",
        container_worker_images={"code-index": resolved_image, "custom-index": resolved_image},
    )
    resolver = _ContainerResolverStub(config)
    sessions = JsonSessionManifestRepository(config.session_store_path)
    backend = _FakeContainerBackend()
    monkeypatch.setattr(
        "evander.effects.providers.agent_create.container_backend", lambda *args: backend
    )
    monkeypatch.setattr(
        "evander.effects.providers.agent_clone_repo.container_backend", lambda *args: backend
    )
    clone_calls, bindings = [], []

    def clone_info(**kwargs):
        clone_calls.append(kwargs)
        return {"clone_url": "/managed/APP.git", "repo_name": "APP"}

    service.managed_git.clone_info = clone_info
    service.managed_git.bind = lambda **kwargs: bindings.append(kwargs)
    components = SimpleNamespace(
        runtime=SimpleNamespace(session_repository=sessions, provider_config=resolver),
        managed_git_admin=service.managed_git,
    )
    settings = service.settings(REPO, "root/app")
    settings["settings"]["image"] = selector
    service.save_settings(
        REPO, "root/app", settings["settings"], expected_revision=settings["revision"]
    )
    build = service.store.get(service.ensure(REPO, "root/app", SHA)["id"])
    jobs, reserved = [], []

    def missing(_):
        raise ContainerWorkerClientError("Missing", status_code=404)

    worker = IndexWorker(components)
    monkeypatch.setattr(
        worker,
        "client",
        lambda _: SimpleNamespace(
            get=missing,
            write_files=lambda *args: None,
            create=lambda job: jobs.append(job) or SimpleNamespace(job_id=job.job_id),
        ),
    )
    if selector == "unknown-index":
        with pytest.raises(ValueError, match="unknown container worker image selector"):
            worker.start(build, before_submit=lambda: reserved.append(True))
        assert not jobs and not reserved and not backend.prepared_sessions
        return
    assert worker.start(build, before_submit=lambda: reserved.append(True)) == build["id"]
    assert reserved == [True]
    assert clone_calls == [
        {"managed_repo_id": REPO, "directory_path": "root/app", "session_id": build["session_id"]}
    ]
    assert backend.cloned_repos[0]["repo_source"] == "/managed/APP.git"
    assert backend.cloned_repos[0]["clone_branch"] == "main"
    assert bindings == [
        {"managed_repo_id": REPO, "kind": "sessions", "binding_id": build["session_id"]}
    ]
    assert jobs[0].working_directory == "/home/agent/repos/APP"
    assert jobs[0].image == resolved_image
    assert sessions.get_session(build["session_id"]).metadata["container_worker"] == {
        "image": resolved_image,
        "image_alias": selector,
    }
    assert backend.prepared_sessions[0]["metadata"]["container_worker"]["image"] == jobs[0].image


def test_bounded_worker_artifact_stream(monkeypatch):
    monkeypatch.setenv("INDEX_TEST_TOKEN", "test-token")
    http = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"123456"))
    )
    worker = ContainerWorkerClient(
        base_url="http://worker", token_env="INDEX_TEST_TOKEN", client=http
    )
    with pytest.raises(ValueError, match="exceeds"):
        worker.read_session_artifact_text_bounded("index-1", "code-index.json", max_bytes=5)
    assert (
        worker.read_session_artifact_text_bounded("index-1", "code-index.json", max_bytes=6)
        == "123456"
    )
    http.close()


def test_embedding_batches_validate_order_dimensions_and_usage():
    requests = []

    def response(request):
        requests.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "data": [{"index": 1, "embedding": [3, 4]}, {"index": 0, "embedding": [1, 2]}],
                "usage": {"total_tokens": 9},
            },
        )

    with OpenAIEmbeddingClient(
        base_url="http://embed/v1",
        api_key=None,
        model="test",
        dimensions=2,
        transport=httpx.MockTransport(response),
    ) as client:
        assert client.embed_texts(["one", "two"]) == [[1, 2], [3, 4]]
        assert client.last_usage == {"total_tokens": 9}
        assert requests[0]["input"] == ["one", "two"]
        client.dimensions = 3
        with pytest.raises(RuntimeError, match="dimensions"):
            client.embed_texts(["one", "two"])


def test_qdrant_batch_keeps_snapshot_filters():
    requests = []

    def response(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"result": {"status": "completed", "operation_id": 1}})

    with QdrantClient("http://vectors", transport=httpx.MockTransport(response)) as client:
        client.upsert_points(
            "code_test",
            points=[
                {"id": 1, "vector": [1.0, 2.0], "payload": {"snapshot": "build-1"}},
                {"id": 2, "vector": [2.0, 1.0], "payload": {"snapshot": "build-2"}},
            ],
        )
    assert [p["payload"]["snapshot"] for p in requests[0]["points"]] == ["build-1", "build-2"]


@pytest.mark.parametrize("scip", [False, True])
def test_semantic_cache_reused_across_commits_and_portable_vectors(service, monkeypatch, tmp_path, scip):
    service.semantic = IndexSemantic(
        QueryRuntimeContext(
            provider_config={"semantic_embedding_dimensions": 2, "semantic_embedding_model": "test"}
        )
    )
    saved = service.settings(REPO, "root/app")
    saved["settings"]["semantic"] = True
    saved["settings"]["scip"] = scip
    service.save_settings(REPO, "root/app", saved["settings"], expected_revision=saved["revision"])
    requests, points = [], []

    class Embeddings:
        last_usage = {"total_tokens": 10}

        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def embed_texts(self, texts):
            requests.extend(texts)
            return [[1.0, 0.0] for _ in texts]

    class Vectors:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def ensure_collection(self, *args, **kwargs):
            pass

        def upsert_points(self, collection, **kwargs):
            points.extend(kwargs["points"])

    monkeypatch.setattr("evander.code_index.semantic.OpenAIEmbeddingClient", Embeddings)
    monkeypatch.setattr("evander.code_index.semantic.QdrantClient", Vectors)
    first = publish(service, scip=scip)
    assert service.semantic.index_batch(service.store, first)
    count = len(requests)
    second = publish(service, OTHER_SHA, scip=scip)
    assert service.semantic.index_batch(service.store, second)
    assert len(requests) == count
    assert service.store.get(second["id"])["metrics"]["embedding_cache_hits"] == count
    assert {p["payload"]["snapshot"] for p in points} == {first["id"], second["id"]}
    destination = tmp_path / "vectors.zip"
    service.export(REPO, "root/app", second["id"], destination, include_embeddings=True)
    with zipfile.ZipFile(destination) as bundle:
        manifest = json.loads(bundle.read("manifest.json"))
        assert manifest["embedding"]["dimensions"] == 2
        assert manifest["coverage"]["navigation"] == ("ready" if scip else "disabled")
        assert b"http://" not in bundle.read("manifest.json")
        bundle.extractall(tmp_path / "external-vectors")
    folder = tmp_path / "external-vectors"
    (folder / "vector.json").write_text("[1.0, 0.0]")
    output = subprocess.check_output(
        [
            "python3",
            "-I",
            str(folder / "query.py"),
            "--db",
            str(folder / "index.sqlite"),
            "search",
            "no lexical match",
            "--query-vector",
            str(folder / "vector.json"),
        ],
        timeout=10,
    )
    result = json.loads(output)
    assert result["commit_sha"] == OTHER_SHA
    assert result["hits"][0]["path"] == "Domain/Retry.cs"


def test_admin_controls_authorization_revision_cancel_and_export(service, monkeypatch, tmp_path):
    published = publish(service)
    queued = service.ensure(REPO, "root/app", OTHER_SHA)
    state = {"allowed": True, "unlocked": False}

    def require_permission(request, permission, directory_path):
        assert directory_path == "root/app"
        if not state["allowed"]:
            raise PermissionError("Forbidden")

    auth = SimpleNamespace(require=lambda request: None, require_permission=require_permission)
    components = SimpleNamespace(
        repository_index_admin=service, managed_git_admin=service.managed_git
    )
    monkeypatch.setattr(
        "evander.admin.web.repository_index.request_has_privileged_operations",
        lambda **kwargs: state["unlocked"],
    )
    app = Starlette(routes=repository_index_routes(components, auth))
    base = f"/api/admin/git-repositories/{REPO}/indexing"

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://test"
        ) as client:
            assert (await client.get(base)).status_code == 200
            assert (await client.post(base + "/builds", json={})).status_code == 403
            assert (await client.get(base + f"/builds/{queued['id']}/cancel")).status_code == 405
            assert service.store.get(queued["id"])["status"] == "queued"
            response = await client.get(base + f"/builds/{published['id']}/export")
            assert response.status_code == 200 and response.content.startswith(b"PK")
            state["allowed"] = False
            assert (await client.get(base + f"/builds/{published['id']}/export")).status_code == 403
            state.update(allowed=True, unlocked=True)
            assert (
                await client.patch(
                    base, json={"expected_revision": 0, "settings": IndexSettings().model_dump()}
                )
            ).status_code == 409
            assert (
                await client.post(base + f"/builds/{queued['id']}/cancel", json={})
            ).status_code == 200
            current = (await client.get(base)).json()
            assert current["settings"]["scip"] is True
            current["settings"]["scip"] = False
            assert (
                await client.patch(
                    base,
                    json={"expected_revision": current["revision"], "settings": current["settings"]},
                )
            ).status_code == 200
            assert (await client.get(base)).json()["settings"]["scip"] is False

    asyncio.run(exercise())


def test_gateway_requires_tool_grant_and_current_team_commit(service, monkeypatch, tmp_path):
    publish(service)
    session = SessionManifest(
        session_id="agent",
        directory_path="root/app",
        allowed_tools=[ToolName.CODE_SEARCH],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        metadata={"managed_git_access_grants": {REPO: {"access_mode": "read-only"}}},
    )
    components = SimpleNamespace(
        repository_index_admin=service, managed_git_admin=service.managed_git, runtime_dir=tmp_path
    )
    bindings = {(REPO, SHA)}
    monkeypatch.setattr("evander_team_addon.code_index.session_bindings", lambda *args: bindings)

    def require_tool(session_id, tool):
        if session_id != session.session_id or tool not in session.allowed_tools:
            raise PermissionError("Tool denied")
        return session

    server = FastMCP("index-test")
    audits = []
    register_code_index_tools(
        server,
        SimpleNamespace(policy=SimpleNamespace(require_tool=require_tool)),
        SimpleNamespace(admin_runtime_factory=lambda: components),
        audit_success=lambda *args: audits.append(args),
        audit_failure=lambda *args: audits.append(args),
    )
    args = {"managed_repo_id": REPO, "query": "retry", "session_id": "agent"}
    asyncio.run(server.call_tool("code_search", args))
    with pytest.raises(ToolError, match="immutable Team"):
        asyncio.run(server.call_tool("code_search", {**args, "commit_sha": OTHER_SHA}))
    session.metadata = {}
    with pytest.raises(ToolError, match="Git grant"):
        asyncio.run(server.call_tool("code_search", args))
    session.allowed_tools = []
    with pytest.raises(ToolError, match="Tool denied"):
        asyncio.run(server.call_tool("code_search", args))
    session.metadata = {
        "team_addon": {"agent_id": "retired"},
        "managed_git_access_grants": {REPO: {}},
    }
    bindings = None
    with pytest.raises(PermissionError, match="immutable Team"):
        authorize_session(components, session, REPO, SHA)
    assert audits


def test_team_scope_reads_persisted_dispatch_and_expires_with_lease(tmp_path):
    from test_team_dispatch_lifecycle import _seed_agent_with_task, MANAGED_REPO_ID
    from evander_team_addon.models import (
        AgentRuntime,
        GitWorkContext,
        TeamDispatch,
        TeamDispatchStatus,
    )
    from evander_team_addon.code_index import session_bindings, protected_bindings

    repository = _seed_agent_with_task(tmp_path)

    def prepare(state):
        state.dispatches["index-dispatch"] = TeamDispatch(
            id="index-dispatch",
            agent_id="worker",
            session_id="session-worker",
            cause_kind="task",
            task_ids=["task"],
            project_ids=["project"],
            status=TeamDispatchStatus.LEASED,
            leased_at=datetime.now(UTC),
            lease_expires_at=datetime.now(UTC) + timedelta(minutes=10),
        )
        state.agent_runtimes["worker"] = AgentRuntime(
            agent_id="worker", active_dispatch_id="index-dispatch"
        )
        state.git_work_contexts["index-context"] = GitWorkContext(
            id="index-context",
            agent_id="worker",
            task_id="task",
            agent_branch="agents/session-worker/task",
            dispatch_id="index-dispatch",
            base_commit=SHA,
            commit_sha=OTHER_SHA,
        )

    repository.update(prepare)
    assert session_bindings(tmp_path, "session-worker") == {(MANAGED_REPO_ID, OTHER_SHA)}
    assert {(MANAGED_REPO_ID, SHA), (MANAGED_REPO_ID, OTHER_SHA)} <= protected_bindings(tmp_path)
    repository.update(
        lambda state: setattr(
            state.dispatches["index-dispatch"],
            "lease_expires_at",
            datetime.now(UTC) - timedelta(seconds=1),
        )
    )
    assert session_bindings(tmp_path, "session-worker") == set()
    assert session_bindings(tmp_path, "not-a-team-session") is None

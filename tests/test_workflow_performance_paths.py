from __future__ import annotations

from datetime import UTC, datetime, timedelta
import sqlite3
from types import SimpleNamespace

import pytest

from evander.admin.services import vm_helpers
from evander.admin.services.repository_index_service import RepositoryIndexService
from evander.code_index.importer import import_artifact
from evander.code_index.models import IndexArtifact, IndexSettings, SourceDocument, digest
from evander.code_index.semantic import IndexSemantic
from evander.code_index.store import IndexStore, SCHEMA
from evander.messaging.discord_inbox_store import DiscordInboxStore
from evander.models import DiscordMessageEnvelope, SessionManifest
from evander.queries.providers.base import QueryRuntimeContext
from evander.triggers.providers.discord_new_message import DiscordNewMessageTriggerProvider
from evander.vm.provider import LibvirtVmProvider, VmProviderContext
from test_repository_index import ManagedGit, REPO, SHA
from test_trigger_conformance_suite import _build_runtime_context, _trigger_adapter


def test_targeted_libvirt_inventory_never_probes_other_vms(monkeypatch):
    session = SessionManifest(
        session_id="target-session",
        vm_id="target-vm",
        allowed_tools=[],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        metadata={"labels": {"team": "a"}},
    )
    repository = SimpleNamespace(
        get_session=lambda _: session,
        list_sessions=lambda: pytest.fail("Target lookup must not enumerate all sessions"),
        list_sessions_for_vm=lambda name: [session] if name == "target-vm" else [],
    )
    provider = LibvirtVmProvider(
        VmProviderContext(
            SimpleNamespace(
                runtime=SimpleNamespace(session_repository=repository),
                normalize_directory_path=lambda value: value or "root",
                agent_control_store=SimpleNamespace(status_for_vm=lambda _: "enabled"),
            )
        )
    )
    monkeypatch.setattr(vm_helpers, "virsh_lines", lambda _: pytest.fail("Fleet enumeration"))
    inspected, probed = [], []
    monkeypatch.setattr(
        vm_helpers, "dominfo_field", lambda name, _: inspected.append(name) or "running"
    )
    monkeypatch.setattr(vm_helpers, "resolve_vm_ip", lambda _: "192.0.2.1")
    monkeypatch.setattr(
        vm_helpers,
        "probe_agent_state",
        lambda name, ip, user: (
            probed.append((name, ip, user)) or "idle",
            ["repo"],
            datetime.now(UTC),
            "dispatch",
            None,
        ),
    )
    summaries = provider.list_vms(target_vm_name="target-vm", vm_user="agent")
    assert inspected == ["target-vm"]
    assert probed == [("target-vm", "192.0.2.1", "agent")]
    assert summaries[0].session_ids == ["target-session"]
    assert summaries[0].labels == {"team": "a"}
    assert summaries[0].dispatch_id == "dispatch"
    monkeypatch.setattr(vm_helpers, "dominfo_field", lambda *_: None)
    assert provider.list_vms(target_vm_name="removed-vm") == []


@pytest.mark.parametrize("filtered", [True, False])
def test_discord_pages_preserve_retries_and_large_timestamp_groups(tmp_path, monkeypatch, filtered):
    store = DiscordInboxStore(tmp_path / "discord-inbox-store.db")
    stamp = datetime.now(UTC)
    messages = [
        DiscordMessageEnvelope(
            message_id=f"message-{i:04}",
            channel_id="channel",
            author_id="author",
            author_name="Name",
            content="hello",
            created_at=stamp,
        )
        for i in range(650)
    ]
    with store._connect() as db:
        db.executemany(
            "INSERT INTO discord_inbox_messages VALUES(?,?,?,?,?,?,?)",
            (store._message_row(message) for message in messages),
        )
    provider = DiscordNewMessageTriggerProvider()
    provider.initialize(
        _trigger_adapter().validate_python(
            {
                "id": "discord-pages",
                "display_name": "Discord",
                "type": "trigger:discord:new-message",
                "discord_bot_token_env": "TOKEN",
                "channel_ids": ["channel"] if filtered else [],
                "user_ids": ["author"] if filtered else [],
            }
        ),
        _build_runtime_context(tmp_path),
    )
    monkeypatch.setattr(store.__class__, "read_raw", lambda _: pytest.fail("Whole inbox read"))
    first = provider.poll()
    assert len(first) == 200
    assert [e.event_id for e in provider.poll()] == [e.event_id for e in first]
    seen = []
    for _ in range(5):
        page = provider.poll()
        assert len(page) <= 200
        for event in page:
            seen.append(event.payload["message"]["message_id"])
            provider.acknowledge_published_event(event)
    assert seen == [message.message_id for message in messages]
    assert provider.poll() == []
    # A late arrival at the boundary must survive even if its ID sorts before the page.
    late = messages[0].model_copy(update={"message_id": "a-late"})
    store.add_message_if_new(late)
    page = provider.poll()
    assert [e.payload["message"]["message_id"] for e in page] == ["a-late"]
    provider.acknowledge_published_event(page[0])
    newer = late.model_copy(
        update={"message_id": "newer", "created_at": stamp + timedelta(seconds=1)}
    )
    store.add_message_if_new(newer)
    page = provider.poll()
    provider.acknowledge_published_event(page[0])
    assert provider._cursor_store().get("discord-pages")["recent_message_ids"] == ["newer"]
    assert provider.poll() == []


def _semantic_service(tmp_path, monkeypatch, *, count=80):
    semantic = IndexSemantic(
        QueryRuntimeContext(
            provider_config={
                "semantic_embedding_dimensions": 2,
                "semantic_embedding_model": "test",
            }
        )
    )
    service = RepositoryIndexService(tmp_path, ManagedGit(), semantic=semantic)
    service.save_settings(
        REPO,
        "root/app",
        IndexSettings(
            enabled=True,
            semantic=True,
            scip=False,
            refresh_on_git_change=False,
        ).model_dump(),
        expected_revision=0,
    )
    build = service.ensure(REPO, "root/app", SHA)
    build = service.store.get(build["id"])
    import_artifact(
        service.store,
        build,
        IndexArtifact(
            commit_sha=SHA,
            documents=[
                SourceDocument(path=f"file-{i}.cs", text=f"class Example{i} {{}}")
                for i in range(count)
            ],
        ),
    )
    points, embedded = [], []
    state = SimpleNamespace(fail_upsert=False)

    class Embeddings:
        last_usage = {}

        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def embed_texts(self, texts):
            embedded.extend(texts)
            return [[1.0, 0.0] for _ in texts]

    class Vectors(Embeddings):
        def __init__(self, *args, **kwargs):
            pass

        def ensure_collection(self, *args, **kwargs):
            pass

        def upsert_points(self, collection, **kwargs):
            if state.fail_upsert:
                raise TimeoutError("Ambiguous vector write")
            points.extend(kwargs["points"])

    monkeypatch.setattr("evander.code_index.semantic.OpenAIEmbeddingClient", Embeddings)
    monkeypatch.setattr("evander.code_index.semantic.QdrantClient", Vectors)
    return service, build, points, embedded, state


def test_discord_cursor_query_skips_old_and_filtered_messages_before_validation(
    tmp_path, monkeypatch
):
    store = DiscordInboxStore(tmp_path / "inbox.db")
    stamp = datetime.now(UTC)
    template = DiscordMessageEnvelope(
        message_id="new",
        channel_id="channel",
        author_id="allowed",
        author_name="Name",
        content="hello",
        created_at=stamp,
    )
    with store._connect() as db:
        db.executemany(
            "INSERT INTO discord_inbox_messages VALUES(?,?,?,?,?,?,?)",
            (
                store._message_row(
                    template.model_copy(
                        update={
                            "message_id": f"old-{i}",
                            "created_at": stamp - timedelta(days=1),
                        }
                    )
                )
                for i in range(250)
            ),
        )
        db.execute(
            "INSERT INTO discord_inbox_messages VALUES(?,?,?,?,?,?,?)", store._message_row(template)
        )
        db.execute(
            "INSERT INTO discord_inbox_messages VALUES(?,?,?,?,?,?,?)",
            store._message_row(
                template.model_copy(update={"message_id": "blocked", "author_id": "blocked"})
            ),
        )
        # Invalid retained payloads must not permanently hide later valid messages.
        invalid = list(store._message_row(template.model_copy(update={"message_id": "a-corrupt"})))
        invalid[-1] = "{}"
        db.execute("INSERT INTO discord_inbox_messages VALUES(?,?,?,?,?,?,?)", invalid)
    original, validated = store._validate_message, []
    monkeypatch.setattr(
        store, "_validate_message", lambda value: validated.append(value) or original(value)
    )
    messages = store.list_trigger_messages(
        channel_ids=["channel"],
        author_ids=["allowed"],
        created_at=stamp,
        acknowledged_ids=[],
        limit=1,
    )
    assert [message.message_id for message in messages] == ["new"]
    assert len(validated) == 2  # corrupt boundary row and current valid row only


def test_semantic_pages_resume_without_materializing_completed_vectors(tmp_path, monkeypatch):
    service, build, points, embedded, _ = _semantic_service(tmp_path, monkeypatch)
    original = service.store.semantic_page
    page_sizes = []

    def page(*args, **kwargs):
        rows = original(*args, **kwargs)
        page_sizes.append(len(rows))
        assert len(rows) <= 32
        return rows

    monkeypatch.setattr(service.store, "semantic_page", page)
    for _ in range(20):
        complete = service.semantic.index_batch(
            service.store, service.store.get(build["id"]), limit=7
        )
        if complete:
            break
    assert complete
    assert len(points) == len({p["id"] for p in points}) == len(embedded) == 80
    assert max(page_sizes) == 32
    assert service.store.get(build["id"])["metrics"]["embedded_chunks"] == 80
    assert service.semantic.index_batch(service.store, service.store.get(build["id"]), limit=7)
    assert len(points) == 80


def test_semantic_failed_upsert_retries_without_advancing_cursor(tmp_path, monkeypatch):
    service, build, points, embedded, state = _semantic_service(tmp_path, monkeypatch, count=5)
    state.fail_upsert = True
    with pytest.raises(TimeoutError):
        service.semantic.index_batch(service.store, build)
    saved = service.store.get(build["id"])
    assert "embedding_cursor" not in saved["metrics"]
    assert not saved["metrics"].get("embedded_chunks")
    state.fail_upsert = False
    assert service.semantic.index_batch(service.store, saved)
    assert len(points) == len(embedded) == 5


def test_cancelled_semantic_build_does_not_advance_or_embed(tmp_path, monkeypatch):
    service, build, points, embedded, _ = _semantic_service(tmp_path, monkeypatch, count=5)
    service.store.update(build["id"], status="cancelled")
    saved = service.store.get(build["id"])
    assert not service.semantic.index_batch(service.store, saved)
    assert points == embedded == []
    assert service.store.get(build["id"])["metrics"] == saved["metrics"]


def test_semantic_continuation_preserves_equal_hash_paths(tmp_path):
    store = IndexStore(tmp_path)
    build = store.enqueue(REPO, "root/app", SHA, IndexSettings(enabled=True), {})
    with store.connect(write=True) as db:
        db.execute("INSERT INTO contents VALUES('hash','same text')")
        db.executemany(
            "INSERT INTO chunks VALUES(?,?,?,?,?,?)",
            ((path, build["id"], path, 1, 1, "hash") for path in ("c", "a", "b")),
        )
    cursor, paths = None, []
    for _ in range(4):
        rows = store.semantic_page(build["id"], REPO, "fingerprint", after=cursor, limit=1)
        if rows:
            paths.append(rows[0]["path"])
            cursor = (rows[0]["hash"], rows[0]["path"])
    assert paths == ["a", "b", "c"]
    assert store.legacy_semantic_checkpoint(build["id"], completed=2) == ("hash", 0)


def test_semantic_legacy_checkpoint_replays_boundary_without_skipping(tmp_path, monkeypatch):
    service, build, points, _, _ = _semantic_service(tmp_path, monkeypatch)
    page = service.store.semantic_page(build["id"], REPO, digest(build["embedding"]), limit=80)
    service.store.update(build["id"], metrics={"embedded_chunks": 35})
    with service.store.connect(write=True) as db:
        db.executemany(
            "INSERT INTO embeddings VALUES(?,?,?,?)",
            ((REPO, digest(build["embedding"]), row["hash"], "[1.0,0.0]") for row in page[:35]),
        )
    assert service.semantic.index_batch(service.store, service.store.get(build["id"]))
    assert {p["payload"]["hash"] for p in points} == {row["hash"] for row in page[34:]}
    assert service.store.get(build["id"])["metrics"]["embedded_chunks"] == 80


def test_content_gc_is_bounded_and_preserves_shared_content(tmp_path):
    store = IndexStore(tmp_path)
    settings = IndexSettings(enabled=True)
    first = store.enqueue(REPO, "root/app", SHA, settings, {})
    second = store.enqueue(REPO, "root/app", "b" * 40, settings, {})
    shared = SourceDocument(path="shared.cs", text="class Shared {}")
    import_artifact(
        store,
        first,
        IndexArtifact(
            commit_sha=SHA,
            documents=[
                shared,
                *[
                    SourceDocument(path=f"unique-{i}.cs", text=f"class Unique{i} {{}}")
                    for i in range(5)
                ],
            ],
        ),
    )
    import_artifact(store, second, IndexArtifact(commit_sha="b" * 40, documents=[shared]))
    store.delete_snapshot(first["id"])
    store.collect_content_garbage(limit=2)
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM content_gc_candidates").fetchone()[0] == 4
    # Reopening must retain the pending work, and a second pass only reclaims orphans.
    store = IndexStore(tmp_path)
    store.collect_content_garbage(limit=10)
    assert store.lexical(second["id"], "Shared")
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM contents").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM content_search").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM content_search_rows").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM content_gc_candidates").fetchone()[0] == 0


def test_index_v1_migration_reclaims_existing_orphans_by_fts_rowid(tmp_path):
    store = IndexStore(tmp_path)
    store.root.mkdir()
    with sqlite3.connect(store.path) as db:
        db.executescript(SCHEMA + "PRAGMA user_version=1;")
        db.execute("INSERT INTO contents VALUES('orphan','text')")
        db.execute("INSERT INTO content_search(rowid,hash,terms) VALUES(99,'orphan','text')")
    assert store.collect_content_garbage() == 1
    with store.connect() as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 3
        assert db.execute("SELECT COUNT(*) FROM content_search").fetchone()[0] == 0


def test_retention_limits_deletions_per_pass(tmp_path):
    service = RepositoryIndexService(tmp_path, ManagedGit(), protected_snapshots=lambda: set())
    service.save_settings(
        REPO,
        "root/app",
        IndexSettings(
            enabled=True,
            semantic=False,
            refresh_on_git_change=False,
        ).model_dump(),
        expected_revision=0,
    )
    for i in range(25):
        build = service.ensure(REPO, "root/app", f"{i:040x}")
        service.store.update(build["id"], status="failed")
    with service.store.connect(write=True) as db:
        db.execute(
            "UPDATE snapshots SET created=?",
            ((datetime.now(UTC) - timedelta(days=90)).isoformat(),),
        )
    assert len(service.maintenance("root/app")["deleted"]) == 20
    assert len(service.maintenance("root/app")["deleted"]) == 5

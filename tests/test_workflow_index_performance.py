from __future__ import annotations

from contextlib import contextmanager
import sqlite3

import pytest

from evander.code_index.models import IndexSettings
from evander.code_index.store import IndexStore, PERFORMANCE_SCHEMA, SCHEMA
from evander.workflow.graph_metadata import resolve_workflow_graph_metadata
from evander.workflow.packs import WorkflowPackGraph


def _measure_sql(store, monkeypatch):
    original_connect = store.connect
    measure = {"instructions": 0, "statements": []}

    def progress():
        measure["instructions"] += 1
        return 0

    @contextmanager
    def connect(**kwargs):
        with original_connect(**kwargs) as db:
            db.set_progress_handler(progress, 1)
            db.set_trace_callback(measure["statements"].append)
            yield db

    monkeypatch.setattr(store, "connect", connect)
    return measure


def test_graph_metadata_normalizes_each_graph_once_and_preserves_component_scope():
    reads = []

    class CountedNodeIds(list):
        def __iter__(self):
            reads.append(self[0])
            return super().__iter__()

    nodes = {f"node-{i:04}": {"type": "query:state:get"} for i in range(250)}
    graphs = [
        WorkflowPackGraph.model_construct(
            id=f"graph-{i:04}",
            name=f"Graph {i}",
            node_ids=CountedNodeIds([f"node-{i:04}"]),
            directory_path=f"root/team-{i}",
        )
        for i in range(250)
    ]
    metadata = resolve_workflow_graph_metadata(nodes_by_id=nodes, edges=[], workflow_graphs=graphs)
    assert len(reads) == 250
    assert len(metadata) == 250
    assert metadata["node-0000"].graph_id == "graph-0000"
    assert metadata["node-0249"].directory_path == "root/team-249"


def test_graph_metadata_preserves_error_order_and_last_exact_metadata():
    nodes = {"a": {"type": "query:state:get"}, "b": {"type": "effect:state:update"}}
    # Components are checked in node order, then invalid graphs in input order.
    graphs = [
        {"id": "unknown-b", "name": "Unknown", "node_ids": ["b", "missing"]},
        {"id": "cross-component", "name": "Cross", "node_ids": ["a", "b"]},
    ]
    with pytest.raises(ValueError, match="Workflow graph cross-component node_ids"):
        resolve_workflow_graph_metadata(nodes_by_id=nodes, edges=[], workflow_graphs=graphs)
    with pytest.raises(ValueError, match="references unknown node IDs: missing"):
        resolve_workflow_graph_metadata(
            nodes_by_id=nodes,
            edges=[],
            workflow_graphs=graphs[:1] + [{"id": "exact-a", "name": "Exact", "node_ids": ["a"]}],
        )
    metadata = resolve_workflow_graph_metadata(
        nodes_by_id={"a": nodes["a"], "plain": {"type": "query:test"}},
        edges=[{"source_id": "a", "target_id": "plain"}],
        workflow_graphs=[
            {"id": "first", "name": "First", "node_ids": ["a", "plain"]},
            {
                "id": "last",
                "name": "Last",
                "node_ids": ["plain", "a"],
                "directory_path": "root/last",
            },
        ],
    )
    assert metadata["a"] == metadata["plain"]
    assert metadata["a"].graph_id == "last"
    assert metadata["a"].directory_path == "root/last"


@pytest.mark.parametrize("kind", ["references", "definitions"])
def test_navigation_batches_wide_layers_with_stable_pages(tmp_path, monkeypatch, kind):
    store = IndexStore(tmp_path)
    build = store.enqueue("repo", "root", "a" * 40, IndexSettings(), {})
    other = store.enqueue("repo", "root", "b" * 40, IndexSettings(), {})
    relationship_kind = "reference" if kind == "references" else "definition"
    with store.connect(write=True) as db:
        db.executemany(
            "INSERT INTO relationships VALUES(?,?,?,?)",
            [
                (build["id"], source, target, relationship_kind)
                for i in range(500)
                for source, target in (("root", f"alias-{i:04}"), (f"alias-{i:04}", "leaf"))
            ]
            + [(build["id"], "leaf", "root", relationship_kind)]
            + [(other["id"], "root", "isolated", relationship_kind)],
        )
        db.executemany(
            "INSERT INTO occurrences VALUES(?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    build["id"],
                    f"a-{i:04}.cs",
                    f"alias-{i:04}",
                    1,
                    1,
                    1,
                    2,
                    "utf-8",
                    int(kind == "definitions"),
                    0,
                )
                for i in range(500)
            ]
            + [
                (
                    other["id"],
                    "a-0000.cs",
                    "isolated",
                    1,
                    1,
                    1,
                    2,
                    "utf-8",
                    int(kind == "definitions"),
                    0,
                )
            ],
        )
    measured = _measure_sql(store, monkeypatch)
    rows, has_more = store.navigate(build["id"], "root", kind, limit=5, offset=2)
    assert [row["path"] for row in rows] == [f"a-{i:04}.cs" for i in range(2, 7)]
    assert has_more
    expansions = [sql for sql in measured["statements"] if "FROM relationships" in sql]
    assert len(expansions) <= 3


def test_navigation_preserves_directed_definitions_and_symmetric_references(tmp_path):
    store = IndexStore(tmp_path)
    build = store.enqueue("repo", "root", "a" * 40, IndexSettings(), {})
    with store.connect(write=True) as db:
        db.executemany(
            "INSERT INTO relationships VALUES(?,?,?,?)",
            [(build["id"], "parent", "child", kind) for kind in ("definition", "reference")]
            + [(build["id"], "empty-target", "", "definition")],
        )
        db.executemany(
            "INSERT INTO occurrences VALUES(?,?,?,?,?,?,?,?,?,?)",
            [
                (build["id"], f"{symbol}.cs", symbol, 1, 1, 1, 2, "utf-8", definition, 0)
                for symbol in ("parent", "child", "empty-target")
                for definition in (0, 1)
            ],
        )
    definitions, _ = store.navigate(build["id"], "child", "definitions", limit=10, offset=0)
    references, _ = store.navigate(build["id"], "child", "references", limit=10, offset=0)
    assert [row["symbol"] for row in definitions] == ["child", "empty-target"]
    assert [row["symbol"] for row in references] == ["child", "parent"]


@pytest.mark.parametrize("width", [1999, 2000])
def test_navigation_keeps_expansion_limit(tmp_path, width):
    store = IndexStore(tmp_path)
    build = store.enqueue("repo", "root", "a" * 40, IndexSettings(), {})
    with store.connect(write=True) as db:
        db.executemany(
            "INSERT INTO relationships VALUES(?,?,?,?)",
            ((build["id"], "root", f"child-{i}", "reference") for i in range(width)),
        )
    if width == 1999:
        assert store.navigate(build["id"], "root", "references", limit=1, offset=0) == ([], False)
    else:
        with pytest.raises(ValueError, match="Symbol relationship expansion exceeds 2000 symbols"):
            store.navigate(build["id"], "root", "references", limit=1, offset=0)


@pytest.mark.parametrize("rebuild,expected", [(False, "history-2999"), (True, "active")])
def test_enqueue_reads_one_reusable_build_from_large_history(
    tmp_path, monkeypatch, rebuild, expected
):
    store = IndexStore(tmp_path)
    settings = IndexSettings()
    seed = store.enqueue("repo", "root", "a" * 40, settings, {})
    with store.connect(write=True) as db:
        db.execute("UPDATE snapshots SET id='active',created='0000' WHERE id=?", (seed["id"],))
        db.executemany(
            "INSERT INTO snapshots(id,identity,repo,directory,sha,status,created,updated,config,embedding,session_id) "
            "SELECT ?,identity,repo,directory,sha,'ready',?,updated,config,embedding,? "
            "FROM snapshots WHERE id='active'",
            ((f"history-{i}", f"{i + 1:08}", f"session-{i}") for i in range(3000)),
        )
    measured = _measure_sql(store, monkeypatch)
    result = store.enqueue("repo", "root", "a" * 40, settings, {}, rebuild=rebuild)
    assert result["id"] == expected
    assert measured["instructions"] < 500
    assert store.count() == 3001


@pytest.mark.parametrize("version", [1, 2])
def test_index_migration_keeps_reuse_order_and_skips_repeated_ddl(tmp_path, monkeypatch, version):
    store = IndexStore(tmp_path)
    store.root.mkdir()
    with sqlite3.connect(store.path) as db:
        db.executescript(
            SCHEMA
            + (PERFORMANCE_SCHEMA if version == 2 else "")
            + f"PRAGMA user_version={version};"
        )
    settings = IndexSettings()
    first = store.enqueue("repo", "root", "a" * 40, settings, {})
    store.update(first["id"], status="failed")
    second = store.enqueue("repo", "root", "a" * 40, settings, {}, rebuild=True)
    store.update(second["id"], status="ready")
    with store.connect(write=True) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 3
        db.execute("UPDATE snapshots SET created='same'")
        db.execute("UPDATE snapshots SET status='future-status' WHERE id=?", (second["id"],))
    assert store.enqueue("repo", "root", "a" * 40, settings, {})["id"] == first["id"]
    with store.connect(write=True) as db:
        db.execute("UPDATE snapshots SET status='ready' WHERE id=?", (second["id"],))
    assert store.enqueue("repo", "root", "a" * 40, settings, {})["id"] == first["id"]
    # Install tracing before the connection opens so schema initialization is observed.
    original_sqlite_connect = sqlite3.connect
    statements = []

    def traced_connect(*args, **kwargs):
        db = original_sqlite_connect(*args, **kwargs)
        db.set_trace_callback(statements.append)
        return db

    monkeypatch.setattr(sqlite3, "connect", traced_connect)
    assert store.enqueue("repo", "root", "a" * 40, settings, {})["id"] == first["id"]
    assert not any(sql.startswith(("CREATE", "INSERT OR REPLACE")) for sql in statements)

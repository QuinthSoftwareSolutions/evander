from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_sandbox.storage import JsonObjectStore
from agent_sandbox.workflow.state_store import WorkflowStateStore


def test_workflow_state_store_uses_runtime_dir_and_isolates_scopes(tmp_path: Path) -> None:
    store = WorkflowStateStore(tmp_path)

    assert store.path == tmp_path / "workflow-state.json"

    base = store.set(
        "root",
        "graph-a",
        "workflow",
        "cursor",
        value={"scope": "base"},
    )
    other_directory = store.set(
        "root/child",
        "graph-a",
        "workflow",
        "cursor",
        value={"scope": "directory"},
    )
    other_graph = store.set(
        "root",
        "graph-b",
        "workflow",
        "cursor",
        value={"scope": "graph"},
    )
    other_namespace = store.set(
        "root",
        "graph-a",
        "other",
        "cursor",
        value={"scope": "namespace"},
    )
    other_key = store.set(
        "root",
        "graph-a",
        "workflow",
        "other",
        value={"scope": "key"},
    )

    assert base.record is not None
    assert base.record.version == 1
    assert other_directory.record is not None
    assert other_graph.record is not None
    assert other_namespace.record is not None
    assert other_key.record is not None

    assert store.get("root", "graph-a", "workflow", "cursor").record == base.record
    assert store.get("root/child", "graph-a", "workflow", "cursor").record == other_directory.record
    assert store.get("root", "graph-b", "workflow", "cursor").record == other_graph.record
    assert store.get("root", "graph-a", "other", "cursor").record == other_namespace.record
    assert store.get("root", "graph-a", "workflow", "other").record == other_key.record

    assert store.get("root/child", "graph-a", "workflow", "cursor").record.value == {
        "scope": "directory"
    }
    assert store.get("root", "graph-b", "workflow", "cursor").record.value == {"scope": "graph"}
    assert store.get("root", "graph-a", "other", "cursor").record.value == {
        "scope": "namespace"
    }
    assert store.get("root", "graph-a", "workflow", "other").record.value == {"scope": "key"}


def test_workflow_state_store_set_patch_delete_and_conflicts(tmp_path: Path) -> None:
    store = WorkflowStateStore(tmp_path)
    path = store.path

    created = store.set(
        "root",
        "graph-a",
        "workflow",
        "cursor",
        value={"outer": {"keep": 1, "remove": 2}, "unchanged": 3},
    )
    assert created.found is False
    assert created.previous_version == 0
    assert created.updated is True
    assert created.deleted is False
    assert created.conflict is False
    assert created.record is not None
    assert created.record.version == 1
    assert created.record.created_at == created.record.updated_at

    patched = store.patch(
        "root",
        "graph-a",
        "workflow",
        "cursor",
        patch={"outer": {"remove": None, "add": 4}},
        expected_version=1,
    )
    assert patched.found is True
    assert patched.previous_version == 1
    assert patched.updated is True
    assert patched.deleted is False
    assert patched.conflict is False
    assert patched.record is not None
    assert patched.record.version == 2
    assert patched.record.value == {"outer": {"keep": 1, "add": 4}, "unchanged": 3}

    replaced = store.set(
        "root",
        "graph-a",
        "workflow",
        "cursor",
        value={"status": "replaced"},
        expected_version=2,
    )
    assert replaced.found is True
    assert replaced.previous_version == 2
    assert replaced.record is not None
    assert replaced.record.version == 3
    assert replaced.record.created_at == created.record.created_at
    assert replaced.record.updated_at >= patched.record.updated_at

    deleted = store.delete("root", "graph-a", "workflow", "cursor", expected_version=3)
    assert deleted.found is True
    assert deleted.previous_version == 3
    assert deleted.updated is False
    assert deleted.deleted is True
    assert deleted.conflict is False
    assert deleted.record is None

    missing_delete = store.delete("root", "graph-a", "workflow", "cursor")
    assert missing_delete.found is False
    assert missing_delete.previous_version == 0
    assert missing_delete.updated is False
    assert missing_delete.deleted is False
    assert missing_delete.conflict is False
    assert missing_delete.record is None

    resurrected = store.patch(
        "root",
        "graph-a",
        "workflow",
        "cursor",
        patch={"resurrected": True, "nested": {"a": 1}},
    )
    assert resurrected.found is False
    assert resurrected.previous_version == 0
    assert resurrected.record is not None
    assert resurrected.record.version == 1
    assert resurrected.record.value == {"resurrected": True, "nested": {"a": 1}}

    before_conflict = json.loads(path.read_text(encoding="utf-8"))
    conflict = store.set(
        "root",
        "graph-a",
        "workflow",
        "cursor",
        value={"status": "conflict"},
        expected_version=0,
    )
    after_conflict = json.loads(path.read_text(encoding="utf-8"))

    assert conflict.conflict is True
    assert conflict.conflict_reason == "expected version 0 but current version is 1"
    assert conflict.found is True
    assert conflict.previous_version == 1
    assert conflict.updated is False
    assert conflict.deleted is False
    assert conflict.record is not None
    assert conflict.record.version == 1
    assert before_conflict == after_conflict
    assert store.get("root", "graph-a", "workflow", "cursor").record == resurrected.record


def test_workflow_state_store_routes_writes_through_json_object_store_update_object(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[Path] = []
    original_update_object = JsonObjectStore.update_object

    def spy(self: JsonObjectStore, updater):
        calls.append(self.path)
        return original_update_object(self, updater)

    monkeypatch.setattr(JsonObjectStore, "update_object", spy)

    store = WorkflowStateStore(tmp_path)
    store.set("root", "graph-a", "workflow", "cursor", value={"status": "created"})
    store.patch(
        "root",
        "graph-a",
        "workflow",
        "cursor",
        patch={"status": "patched"},
        expected_version=1,
    )
    store.delete("root", "graph-a", "workflow", "cursor", expected_version=2)

    assert calls == [store.path, store.path, store.path]

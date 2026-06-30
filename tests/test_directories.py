from pathlib import Path

import pytest

from evander.directories import DirectoryService, DirectoryStore
from evander.models import Directory, DirectoryPath, SessionManifest, ToolName


def test_directory_path_normalizes_and_validates() -> None:
    path = DirectoryPath(segments="root/a/./b")
    assert path.as_posix() == "root/a/b"

    with pytest.raises(ValueError, match="must begin with 'root'"):
        DirectoryPath(segments="a/b")


def test_directory_name_segment_safety_validation() -> None:
    with pytest.raises(ValueError, match="cannot contain '/'"):
        Directory(id="a", name="bad/name", parent_id="root")
    with pytest.raises(ValueError, match="cannot be '.' or '..'"):
        Directory(id="a", name="..", parent_id="root")


def test_directory_store_ensures_root_and_root_is_immutable(tmp_path: Path) -> None:
    store = DirectoryStore(tmp_path / "directories.json")
    store.ensure_root()
    items = store.read_all()
    assert items["root"].name == "root"

    with pytest.raises(ValueError, match="cannot be deleted"):
        store.delete("root")

    with pytest.raises(ValueError, match="identity is immutable"):
        store.put(Directory(id="root", name="renamed-root", parent_id=None))


def test_directory_store_rejects_missing_parent_and_duplicate_siblings(tmp_path: Path) -> None:
    store = DirectoryStore(tmp_path / "directories.json")
    store.ensure_root()

    with pytest.raises(ValueError, match="unknown parent"):
        store.put(Directory(id="child", name="child", parent_id="missing"))

    store.put(Directory(id="a", name="a", parent_id="root"))
    with pytest.raises(ValueError, match="duplicate sibling directory name"):
        store.put(Directory(id="a-2", name="a", parent_id="root"))


def test_directory_store_mutations_use_transactional_updates(tmp_path: Path, monkeypatch) -> None:
    store = DirectoryStore(tmp_path / "directories.json")

    def fail_write_all(*args, **kwargs) -> None:
        raise AssertionError("directory mutations must not use split read/write updates")

    monkeypatch.setattr(store._repository, "write_all", fail_write_all)

    store.ensure_root()
    store.put(Directory(id="a", name="a", parent_id="root"))
    store.delete("a")

    assert list(store.read_all()) == ["root"]


def test_directory_store_read_all_does_not_rewrite_existing_root(
    tmp_path: Path, monkeypatch
) -> None:
    store = DirectoryStore(tmp_path / "directories.json")
    store.ensure_root()

    def fail_update_all(*args, **kwargs) -> None:
        raise AssertionError("directory reads must not use transactional write updates")

    monkeypatch.setattr(store._repository, "update_all", fail_update_all)

    assert list(store.read_all()) == ["root"]


def test_session_manifest_directory_path_is_canonical() -> None:
    manifest = SessionManifest(
        session_id="session-1",
        directory_path="root/team/./sandbox",
        allowed_tools=[ToolName.DISCORD_GET_COMMANDS],
        expires_at="2030-01-01T00:00:00Z",
    )
    assert manifest.directory_path == "root/team/sandbox"


def test_directory_service_supports_create_show_rename_move_and_delete(tmp_path: Path) -> None:
    service = DirectoryService(DirectoryStore(tmp_path / "directories.json"))

    team = service.create(parent="root", name="team")
    sandbox = service.create(parent=team.path, name="sandbox")

    shown = service.show(sandbox.id)
    assert shown.directory.path == "root/team/sandbox"
    assert [item.path for item in shown.ancestry] == ["root", "root/team", "root/team/sandbox"]

    renamed = service.rename(sandbox.id, new_name="lab")
    assert renamed.path == "root/team/lab"

    moved = service.move(renamed.id, new_parent="root")
    assert moved.path == "root/lab"

    listed = service.list_children(parent="root")
    assert [item.path for item in listed] == ["root/lab", "root/team"]

    deleted = service.delete(moved.id)
    assert deleted == {"deleted_count": 1, "deleted_ids": [moved.id]}


def test_directory_service_mutations_use_transactional_updates(
    tmp_path: Path, monkeypatch
) -> None:
    store = DirectoryStore(tmp_path / "directories.json")
    service = DirectoryService(store)

    def fail_write_all(*args, **kwargs) -> None:
        raise AssertionError("directory service mutations must not use split read/write updates")

    monkeypatch.setattr(store, "write_all", fail_write_all)

    team = service.create(parent="root", name="team")
    sandbox = service.create(parent=team.id, name="sandbox")
    renamed = service.rename(sandbox.id, new_name="lab")
    moved = service.move(renamed.id, new_parent="root")
    deleted = service.delete(moved.id)

    assert deleted == {"deleted_count": 1, "deleted_ids": [moved.id]}
    assert [item.path for item in service.list_children(parent="root")] == ["root/team"]


def test_directory_service_rejects_move_cycles_and_non_empty_delete_without_force(
    tmp_path: Path,
) -> None:
    service = DirectoryService(DirectoryStore(tmp_path / "directories.json"))

    team = service.create(parent="root", name="team")
    sandbox = service.create(parent=team.id, name="sandbox")
    child = service.create(parent=sandbox.id, name="child")

    with pytest.raises(ValueError, match="cannot be moved into its descendant"):
        service.move(team.id, new_parent=child.id)

    with pytest.raises(ValueError, match="not empty"):
        service.delete(team.id)

    deleted = service.delete(team.id, force=True)
    assert deleted["deleted_count"] == 3
    assert deleted["deleted_ids"] == sorted([team.id, sandbox.id, child.id])


def test_directory_service_rejects_root_mutation_invariants(tmp_path: Path) -> None:
    service = DirectoryService(DirectoryStore(tmp_path / "directories.json"))

    with pytest.raises(ValueError, match="root directory cannot be renamed"):
        service.rename("root", new_name="other")

    with pytest.raises(ValueError, match="root directory cannot be moved"):
        service.move("root", new_parent="root")

    with pytest.raises(ValueError, match="root directory cannot be deleted"):
        service.delete("root", force=True)

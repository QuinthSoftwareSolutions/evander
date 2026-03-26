from pathlib import Path

import pytest

from agent_sandbox.directories import DirectoryStore
from agent_sandbox.models import Directory, DirectoryPath, SessionManifest, ToolName


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


def test_session_manifest_directory_path_is_canonical() -> None:
    manifest = SessionManifest(
        session_id="session-1",
        directory_path="root/team/./sandbox",
        allowed_tools=[ToolName.DISCORD_GET_COMMANDS],
        expires_at="2030-01-01T00:00:00Z",
    )
    assert manifest.directory_path == "root/team/sandbox"

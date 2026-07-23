from __future__ import annotations

from datetime import UTC, datetime, timedelta
import io
import os
from pathlib import Path
import tarfile

import pytest

from evander.communication_artifacts import CommunicationArtifactStore
from evander.host_directories.models import (
    HostDirectoryDirectoryConfig,
    normalize_host_directory_grants,
    validate_host_directory_directory_overlay,
)
from evander.host_directories.grants import host_directory_tool_names_for_grants
from evander.host_directories import service as service_module
from evander.host_directories.service import (
    HostDirectoryError,
    HostDirectoryPolicyError,
    HostDirectoryService,
)
from evander.models import SessionManifest


class _ProviderConfig:
    def __init__(self, config: HostDirectoryDirectoryConfig) -> None:
        self.config = config

    def resolve_host_directories_for_directory(
        self, _directory_path: str
    ) -> HostDirectoryDirectoryConfig:
        return self.config


def _service(tmp_path: Path, root: Path) -> HostDirectoryService:
    config = HostDirectoryDirectoryConfig(
        host_directories={
            "drop": {
                "display_name": "Drop",
                "description": "Controlled drop directory",
                "root": root,
                "capabilities": ["list", "import", "delete"],
                "max_import_bytes": 1024 * 1024,
                "max_import_entries": 100,
                "max_watch_entries": 100,
            }
        }
    )
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    return HostDirectoryService(
        runtime_dir=runtime_dir,
        provider_config=_ProviderConfig(config),
        artifact_store_factory=lambda: CommunicationArtifactStore(runtime_dir),
    )


def _session(*capabilities: str) -> SessionManifest:
    return SessionManifest(
        session_id="session-1",
        directory_path="root",
        allowed_tools=[f"host_directory_{capability}" for capability in capabilities],
        host_directory_grants={"drop": list(capabilities)},
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )


def test_host_directory_models_validate_overlay_and_grants(tmp_path: Path) -> None:
    assert validate_host_directory_directory_overlay(
        {
            "host_directories": {
                "Drop": {
                    "display_name": "Drop",
                    "description": "Drop files",
                    "root": str(tmp_path),
                    "capabilities": ["list", "delete", "list"],
                }
            }
        }
    ) == {
        "host_directories": {
            "drop": {
                "display_name": "Drop",
                "description": "Drop files",
                "root": str(tmp_path),
                "capabilities": ["list", "delete"],
            }
        }
    }
    assert normalize_host_directory_grants({"Drop": ["list", "import", "list"]}) == {
        "drop": ["list", "import"]
    }
    with pytest.raises(ValueError, match="absolute"):
        validate_host_directory_directory_overlay(
            {"host_directories": {"drop": {"root": "relative"}}}
        )
    with pytest.raises(ValueError, match="filesystem root"):
        validate_host_directory_directory_overlay(
            {"host_directories": {"drop": {"root": "/"}}}
        )
    with pytest.raises(ValueError, match="filesystem root"):
        validate_host_directory_directory_overlay(
            {"host_directories": {"drop": {"root": "/tmp/.."}}}
        )
    root = tmp_path / "paths"
    root.mkdir()
    service = _service(tmp_path, root)
    with pytest.raises(ValueError, match="non-empty"):
        service.import_for_session(
            session=_session("import"),
            directory_id="drop",
            path=".",
        )
    with pytest.raises(ValueError, match="duplicate"):
        validate_host_directory_directory_overlay(
            {
                "host_directories": {
                    "Drop": {"disabled": True},
                    "drop": {"disabled": False},
                }
            }
        )
    with pytest.raises(ValueError, match="capabilities must be a list"):
        validate_host_directory_directory_overlay(
            {"host_directories": {"drop": {"capabilities": None}}}
        )
    assert host_directory_tool_names_for_grants(
        {"drop": ["delete", "list"], "archive": ["list"]}
    ) == ["host_directory_list", "host_directory_delete"]
    with pytest.raises(ValueError, match="grant capabilities must be a list"):
        normalize_host_directory_grants({"drop": None})
    with pytest.raises(ValueError, match="grant capabilities must be a list"):
        normalize_host_directory_grants({"drop": "delete"})


def test_list_is_relative_paginated_and_hides_reserved_trash(tmp_path: Path) -> None:
    root = tmp_path / "drop"
    root.mkdir()
    (root / "a.txt").write_text("a", encoding="utf-8")
    (root / "nested").mkdir()
    (root / "nested" / "b.txt").write_text("b", encoding="utf-8")
    (root / ".evander-trash").mkdir()
    (root / ".evander-trash" / "secret.txt").write_text("secret", encoding="utf-8")
    service = _service(tmp_path, root)
    session = _session("list")

    first = service.list_for_session(
        session=session,
        directory_id="drop",
        recursive=True,
        limit=2,
    )
    assert [entry["path"] for entry in first["entries"]] == ["a.txt", "nested"]
    assert first["next_cursor"]
    second = service.list_for_session(
        session=session,
        directory_id="drop",
        recursive=True,
        limit=2,
        cursor=first["next_cursor"],
    )
    assert [entry["path"] for entry in second["entries"]] == ["nested/b.txt"]
    assert second["next_cursor"] is None
    assert all(".evander-trash" not in entry["path"] for entry in first["entries"])

    with pytest.raises(ValueError, match="cursor"):
        service.list_for_session(
            session=session,
            directory_id="drop",
            recursive=False,
            limit=2,
            cursor=first["next_cursor"],
        )


def test_list_cursor_continues_after_last_path_when_tree_changes(
    tmp_path: Path,
) -> None:
    root = tmp_path / "drop"
    root.mkdir()
    for name in ("a.txt", "b.txt", "c.txt"):
        (root / name).write_text(name, encoding="utf-8")
    service = _service(tmp_path, root)
    session = _session("list")

    first = service.list_for_session(
        session=session,
        directory_id="drop",
        limit=2,
    )
    assert [entry["path"] for entry in first["entries"]] == ["a.txt", "b.txt"]

    (root / "a.txt").unlink()
    (root / "d.txt").write_text("d", encoding="utf-8")
    second = service.list_for_session(
        session=session,
        directory_id="drop",
        limit=2,
        cursor=first["next_cursor"],
    )

    assert [entry["path"] for entry in second["entries"]] == ["c.txt", "d.txt"]
    assert second["next_cursor"] is None


def test_nested_trash_named_directory_is_normal_content(tmp_path: Path) -> None:
    root = tmp_path / "drop"
    root.mkdir()
    parent = root / "nested"
    nested_trash = parent / ".evander-trash"
    nested_trash.mkdir(parents=True)
    (nested_trash / "ordinary.txt").write_text("ordinary", encoding="utf-8")
    service = _service(tmp_path, root)
    session = _session("list", "delete")

    listing = service.list_for_session(
        session=session,
        directory_id="drop",
        recursive=True,
    )
    entries = {entry["path"]: entry for entry in listing["entries"]}
    assert "nested/.evander-trash" in entries
    assert "nested/.evander-trash/ordinary.txt" in entries

    with pytest.raises(HostDirectoryError, match="recursive=true"):
        service.delete_for_session(
            session=session,
            directory_id="drop",
            path="nested",
            expected_revision=entries["nested"]["revision"],
        )


def test_import_file_and_folder_stage_session_owned_artifacts(tmp_path: Path) -> None:
    root = tmp_path / "drop"
    root.mkdir()
    (root / "a.txt").write_text("hello", encoding="utf-8")
    folder = root / "bundle"
    folder.mkdir()
    (folder / "b.txt").write_text("world", encoding="utf-8")
    (folder / "empty").mkdir()
    service = _service(tmp_path, root)
    session = _session("import")

    imported_file = service.import_for_session(
        session=session, directory_id="drop", path="a.txt"
    )
    staged_file = CommunicationArtifactStore(tmp_path / "runtime").get(
        imported_file["artifact"]["artifact_id"]
    )
    assert staged_file.blob_path.read_bytes() == b"hello"
    assert staged_file.metadata.session_id == session.session_id
    assert staged_file.metadata.metadata["relative_path"] == "a.txt"

    imported_folder = service.import_for_session(
        session=session, directory_id="drop", path="bundle"
    )
    staged_folder = CommunicationArtifactStore(tmp_path / "runtime").get(
        imported_folder["artifact"]["artifact_id"]
    )
    with tarfile.open(fileobj=io.BytesIO(staged_folder.blob_path.read_bytes()), mode="r:gz") as tar:
        assert tar.getnames() == ["bundle", "bundle/b.txt", "bundle/empty"]
        assert tar.extractfile("bundle/b.txt").read() == b"world"  # type: ignore[union-attr]
        assert all(member.uid == 0 and member.gid == 0 for member in tar.getmembers())


def test_paths_with_leading_or_trailing_spaces_remain_exact(tmp_path: Path) -> None:
    root = tmp_path / "drop"
    root.mkdir()
    (root / "invoice.csv").write_text("wrong", encoding="utf-8")
    (root / " invoice.csv").write_text("right", encoding="utf-8")
    (root / "notes ").write_text("trailing", encoding="utf-8")
    service = _service(tmp_path, root)
    session = _session("list", "import")

    listing = service.list_for_session(
        session=session,
        directory_id="drop",
    )
    assert {entry["path"] for entry in listing["entries"]} == {
        " invoice.csv",
        "invoice.csv",
        "notes ",
    }

    imported = service.import_for_session(
        session=session,
        directory_id="drop",
        path=" invoice.csv",
    )
    staged = CommunicationArtifactStore(tmp_path / "runtime").get(
        imported["artifact"]["artifact_id"]
    )
    assert staged.blob_path.read_bytes() == b"right"


def test_folder_import_returns_revision_of_archived_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "drop"
    folder = root / "bundle"
    folder.mkdir(parents=True)
    service = _service(tmp_path, root)
    session = _session("list", "import")
    real_snapshot = service._snapshot_tree
    mutated = False

    def mutate_before_first_snapshot(*args: object, **kwargs: object) -> object:
        nonlocal mutated
        if not mutated:
            mutated = True
            (folder / "arrived.txt").write_text("arrived", encoding="utf-8")
        return real_snapshot(*args, **kwargs)

    monkeypatch.setattr(service, "_snapshot_tree", mutate_before_first_snapshot)
    imported = service.import_for_session(
        session=session,
        directory_id="drop",
        path="bundle",
    )
    current = {
        entry["path"]: entry["revision"]
        for entry in service.list_for_session(
            session=session,
            directory_id="drop",
        )["entries"]
    }

    assert imported["revision"] == current["bundle"]


def test_symlinks_cannot_be_listed_or_imported(tmp_path: Path) -> None:
    root = tmp_path / "drop"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    (root / "link").symlink_to(outside)
    service = _service(tmp_path, root)

    listing = service.list_for_session(
        session=_session("list"),
        directory_id="drop",
        recursive=True,
    )
    assert listing["entries"] == []
    with pytest.raises(HostDirectoryError, match="symlink"):
        service.import_for_session(
            session=_session("import"),
            directory_id="drop",
            path="link",
        )


def test_directory_traversal_rejects_a_distinct_mount_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "drop"
    child = root / "child"
    child.mkdir(parents=True)
    service = _service(tmp_path, root)
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    root_fd = os.open(root, flags)
    try:
        monkeypatch.setattr(
            service_module,
            "_mount_id",
            lambda descriptor: 100 if descriptor == root_fd else 200,
        )
        with pytest.raises(HostDirectoryError, match="filesystem boundary"):
            service._open_directory_parts(
                root_fd,
                ("child",),
                root_dev=os.fstat(root_fd).st_dev,
            )
    finally:
        os.close(root_fd)


def test_health_probes_delete_quarantine_writability(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "drop"
    root.mkdir()
    service = _service(tmp_path, root)

    healthy = service.health(directory_path="root", directory_id="drop")
    assert healthy["status"] == "ok"
    assert healthy["checks"] == {
        "readable": True,
        "mount_boundary_verified": True,
        "quarantine_writable": True,
    }

    def reject_quarantine(_root_fd: int, *, root_dev: int) -> int:
        raise PermissionError(f"device {root_dev} is read-only")

    monkeypatch.setattr(service, "_ensure_trash_directory", reject_quarantine)
    unhealthy = service.health(directory_path="root", directory_id="drop")
    assert unhealthy["status"] == "error"
    assert unhealthy["available"] is False
    assert unhealthy["checks"] == {
        "readable": True,
        "mount_boundary_verified": True,
        "quarantine_writable": False,
    }


def test_health_fails_when_mount_boundary_verification_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "drop"
    root.mkdir()
    service = _service(tmp_path, root)
    service.provider_config.config.host_directories["drop"].capabilities = ["list"]

    def unavailable(_file_descriptor: int) -> int:
        raise HostDirectoryError(
            "host directory mount-boundary verification is unavailable"
        )

    monkeypatch.setattr(service_module, "_mount_id", unavailable)
    health = service.health(directory_path="root", directory_id="drop")

    assert health["status"] == "error"
    assert health["checks"] == {
        "readable": True,
        "mount_boundary_verified": False,
        "quarantine_writable": True,
    }


def test_delete_quarantines_and_operator_can_restore_or_purge(tmp_path: Path) -> None:
    root = tmp_path / "drop"
    root.mkdir()
    (root / "a.txt").write_text("a", encoding="utf-8")
    folder = root / "folder"
    folder.mkdir()
    (folder / "nested.txt").write_text("nested", encoding="utf-8")
    service = _service(tmp_path, root)
    session = _session("list", "delete")
    listing = service.list_for_session(
        session=session, directory_id="drop", recursive=True
    )
    entries = {entry["path"]: entry for entry in listing["entries"]}

    deleted = service.delete_for_session(
        session=session,
        directory_id="drop",
        path="a.txt",
        expected_revision=entries["a.txt"]["revision"],
    )
    assert not (root / "a.txt").exists()
    assert service.list_quarantine(directory_path="root", directory_id="drop")[0][
        "recovery_id"
    ] == deleted["recovery_id"]
    assert service.list_quarantine(
        directory_path="root",
        directory_id="drop",
    )[0]["recovery_status"] == "ready"
    service.provider_config.config.host_directories["drop"].disabled = True
    assert service.health(directory_path="root", directory_id="drop")["disabled"] is True
    (root / "a.txt").write_text("replacement", encoding="utf-8")
    with pytest.raises(HostDirectoryError, match="destination already exists"):
        service.restore_quarantine(
            directory_path="root",
            directory_id="drop",
            recovery_id=deleted["recovery_id"],
        )
    assert (root / "a.txt").read_text(encoding="utf-8") == "replacement"
    (root / "a.txt").unlink()
    service.restore_quarantine(
        directory_path="root",
        directory_id="drop",
        recovery_id=deleted["recovery_id"],
    )
    assert (root / "a.txt").read_text(encoding="utf-8") == "a"
    service.provider_config.config.host_directories["drop"].disabled = False

    with pytest.raises(HostDirectoryError, match="recursive=true"):
        service.delete_for_session(
            session=session,
            directory_id="drop",
            path="folder",
            expected_revision=entries["folder"]["revision"],
        )
    folder_deleted = service.delete_for_session(
        session=session,
        directory_id="drop",
        path="folder",
        expected_revision=entries["folder"]["revision"],
        recursive=True,
    )
    service.purge_quarantine(
        directory_path="root",
        directory_id="drop",
        recovery_id=folder_deleted["recovery_id"],
    )
    assert service.list_quarantine(directory_path="root", directory_id="drop") == []


def test_delete_rejects_stale_revision_and_missing_grant(tmp_path: Path) -> None:
    root = tmp_path / "drop"
    root.mkdir()
    target = root / "a.txt"
    target.write_text("a", encoding="utf-8")
    service = _service(tmp_path, root)
    session = _session("list", "delete")
    revision = service.list_for_session(
        session=session, directory_id="drop"
    )["entries"][0]["revision"]
    target.write_text("changed", encoding="utf-8")

    with pytest.raises(HostDirectoryError, match="changed after"):
        service.delete_for_session(
            session=session,
            directory_id="drop",
            path="a.txt",
            expected_revision=revision,
        )
    with pytest.raises(HostDirectoryPolicyError, match="not granted"):
        service.import_for_session(
            session=session,
            directory_id="drop",
            path="a.txt",
        )


def test_delete_rolls_back_when_entry_changes_during_quarantine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "drop"
    root.mkdir()
    target = root / "a.txt"
    target.write_text("approved", encoding="utf-8")
    service = _service(tmp_path, root)
    session = _session("list", "delete")
    revision = service.list_for_session(
        session=session,
        directory_id="drop",
    )["entries"][0]["revision"]
    real_rename = os.rename
    mutated = False

    def mutate_then_rename(
        source: str,
        destination: str,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        nonlocal mutated
        if source == "a.txt" and destination == "payload" and not mutated:
            mutated = True
            target.write_text("changed during quarantine", encoding="utf-8")
        real_rename(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )

    monkeypatch.setattr(
        "evander.host_directories.service.os.rename",
        mutate_then_rename,
    )

    with pytest.raises(HostDirectoryError, match="changed during quarantine"):
        service.delete_for_session(
            session=session,
            directory_id="drop",
            path="a.txt",
            expected_revision=revision,
        )

    assert target.read_text(encoding="utf-8") == "changed during quarantine"
    assert service.list_quarantine(directory_path="root", directory_id="drop") == []


def test_quarantine_recovery_is_scoped_to_evander_directory(tmp_path: Path) -> None:
    root = tmp_path / "drop"
    root.mkdir()
    target = root / "a.txt"
    target.write_text("a", encoding="utf-8")
    service = _service(tmp_path, root)
    child_session = _session("list", "delete").model_copy(
        update={"directory_path": "root/team"}
    )
    revision = service.list_for_session(
        session=child_session,
        directory_id="drop",
    )["entries"][0]["revision"]

    deleted = service.delete_for_session(
        session=child_session,
        directory_id="drop",
        path="a.txt",
        expected_revision=revision,
    )

    assert service.list_quarantine(
        directory_path="root",
        directory_id="drop",
    ) == []
    assert [
        record["recovery_id"]
        for record in service.list_quarantine(
            directory_path="root/team",
            directory_id="drop",
        )
    ] == [deleted["recovery_id"]]
    with pytest.raises(HostDirectoryError, match="another directory scope"):
        service.restore_quarantine(
            directory_path="root",
            directory_id="drop",
            recovery_id=deleted["recovery_id"],
        )

    service.restore_quarantine(
        directory_path="root/team",
        directory_id="drop",
        recovery_id=deleted["recovery_id"],
    )
    assert target.read_text(encoding="utf-8") == "a"


@pytest.mark.parametrize(
    ("damage", "expected_status"),
    [
        ("remove-payload", "missing_payload"),
        ("corrupt-metadata", "corrupt_metadata"),
    ],
)
def test_incomplete_quarantine_records_remain_visible_and_purgeable(
    tmp_path: Path,
    damage: str,
    expected_status: str,
) -> None:
    root = tmp_path / "drop"
    root.mkdir()
    target = root / "a.txt"
    target.write_text("a", encoding="utf-8")
    service = _service(tmp_path, root)
    session = _session("list", "delete")
    revision = service.list_for_session(
        session=session,
        directory_id="drop",
    )["entries"][0]["revision"]
    deleted = service.delete_for_session(
        session=session,
        directory_id="drop",
        path="a.txt",
        expected_revision=revision,
    )
    recovery_root = (
        root / ".evander-trash" / deleted["recovery_id"]
    )
    if damage == "remove-payload":
        (recovery_root / "payload").unlink()
    else:
        (recovery_root / "metadata.json").write_text("{broken", encoding="utf-8")

    records = service.list_quarantine(
        directory_path="root",
        directory_id="drop",
    )
    assert records[0]["recovery_status"] == expected_status
    assert records[0]["restore_available"] is False
    assert records[0]["purge_available"] is True
    with pytest.raises(HostDirectoryError, match="can only be purged"):
        service.restore_quarantine(
            directory_path="root",
            directory_id="drop",
            recovery_id=deleted["recovery_id"],
        )

    service.purge_quarantine(
        directory_path="root",
        directory_id="drop",
        recovery_id=deleted["recovery_id"],
    )
    assert not recovery_root.exists()


def test_quarantine_recovery_directory_cannot_cross_a_mount_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "drop"
    root.mkdir()
    target = root / "a.txt"
    target.write_text("a", encoding="utf-8")
    service = _service(tmp_path, root)
    session = _session("list", "delete")
    revision = service.list_for_session(
        session=session,
        directory_id="drop",
    )["entries"][0]["revision"]
    deleted = service.delete_for_session(
        session=session,
        directory_id="drop",
        path="a.txt",
        expected_revision=revision,
    )
    real_mount_id = service_module._mount_id

    def distinct_recovery_mount(file_descriptor: int) -> int:
        mount_id = real_mount_id(file_descriptor)
        descriptor_path = os.readlink(f"/proc/self/fd/{file_descriptor}")
        if descriptor_path.endswith(deleted["recovery_id"]):
            return mount_id + 1
        return mount_id

    monkeypatch.setattr(service_module, "_mount_id", distinct_recovery_mount)
    with pytest.raises(HostDirectoryError, match="filesystem boundary"):
        service.list_quarantine(
            directory_path="root",
            directory_id="drop",
        )


def test_stale_empty_internal_quarantine_record_is_reconciled(
    tmp_path: Path,
) -> None:
    root = tmp_path / "drop"
    trash = root / ".evander-trash"
    trash.mkdir(parents=True)
    orphan = trash / f"trash_{'0' * 32}"
    orphan.mkdir()
    old = datetime.now(UTC).timestamp() - 2 * 60 * 60
    os.utime(orphan, (old, old))
    service = _service(tmp_path, root)

    assert service.list_quarantine(
        directory_path="root",
        directory_id="drop",
    ) == []
    assert not orphan.exists()


def test_stale_corrupt_control_only_quarantine_record_is_reconciled(
    tmp_path: Path,
) -> None:
    root = tmp_path / "drop"
    trash = root / ".evander-trash"
    trash.mkdir(parents=True)
    orphan = trash / f"trash_{'1' * 32}"
    orphan.mkdir()
    (orphan / "scope.json").write_text("{partial", encoding="utf-8")
    old = datetime.now(UTC).timestamp() - 2 * 60 * 60
    os.utime(orphan, (old, old))
    service = _service(tmp_path, root)

    assert service.list_quarantine(
        directory_path="root",
        directory_id="drop",
    ) == []
    assert not orphan.exists()

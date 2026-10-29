from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from starlette.testclient import TestClient

from evander.admin.composition import build_admin_runtime
from evander.admin.web.app import create_app
from evander.directories import DirectoryService
from evander.host_directories.paths import host_directory_revision
from evander.models import SessionManifest, ToolName
from config_helpers import write_runtime_config


def _runtime_dir(tmp_path: Path) -> Path:
    return write_runtime_config(tmp_path, enable_directory_scoped_config=True)


def _definition(root: Path) -> dict[str, object]:
    return {
        "display_name": "Shared inbox",
        "description": "Files shared with explicitly granted sessions.",
        "root": str(root),
        "capabilities": ["list", "import", "delete"],
        "delete_requires_approval": True,
        "max_import_bytes": 10 * 1024 * 1024,
        "max_import_entries": 1_000,
        "max_watch_entries": 10_000,
        "disabled": False,
    }


def test_host_directory_admin_api_is_structured_revisioned_and_health_checked(
    tmp_path: Path,
) -> None:
    host_root = tmp_path / "shared-inbox"
    host_root.mkdir()
    service = build_admin_runtime(_runtime_dir(tmp_path))
    client = TestClient(create_app(service))

    initial = client.get("/api/admin/host-directories?directory_path=root")
    assert initial.status_code == 200
    initial_state = initial.json()
    assert initial_state["host_directories"] == []
    assert initial_state["config"] == {}

    locked = client.put(
        "/api/admin/host-directories?directory_path=root",
        json={
            "expected_revision": initial_state["revision"],
            "config": {"host_directories": {"shared": _definition(host_root)}},
        },
    )
    assert locked.status_code == 403
    assert client.post("/api/admin/privileged/unlock").status_code == 200

    invalid_definition = _definition(host_root)
    invalid_definition["root"] = "relative/path"
    invalid = client.put(
        "/api/admin/host-directories?directory_path=root",
        json={
            "expected_revision": initial_state["revision"],
            "config": {"host_directories": {"shared": invalid_definition}},
        },
    )
    assert invalid.status_code == 400

    saved = client.put(
        "/api/admin/host-directories?directory_path=root",
        json={
            "expected_revision": initial_state["revision"],
            "config": {"host_directories": {"shared": _definition(host_root)}},
        },
    )
    assert saved.status_code == 200
    saved_state = saved.json()
    assert saved_state["revision"] != initial_state["revision"]
    assert saved_state["host_directories"] == [
        {
            "directory_id": "shared",
            **_definition(host_root),
            "defined_here": True,
            "source_directory_path": "root",
            "health": None,
        }
    ]

    stale = client.put(
        "/api/admin/host-directories?directory_path=root",
        json={
            "expected_revision": initial_state["revision"],
            "config": saved_state["config"],
        },
    )
    assert stale.status_code == 409

    health = client.post(
        "/api/admin/host-directories/shared/health?directory_path=root",
        json={},
    )
    assert health.status_code == 200
    assert health.json()["health"]["available"] is True


def test_host_directory_admin_quarantine_restore_and_purge(
    tmp_path: Path,
) -> None:
    host_root = tmp_path / "shared-inbox"
    host_root.mkdir()
    service = build_admin_runtime(_runtime_dir(tmp_path))
    assert service.operations_admin.write_directory_domain_config(
        "root",
        "host_directories",
        {"host_directories": {"shared": _definition(host_root)}},
    ).ok
    client = TestClient(create_app(service))
    assert client.post("/api/admin/privileged/unlock").status_code == 200

    host_directory_service = service.host_directory_admin._deps.host_directory_service
    session = SessionManifest(
        session_id="session-host-directory-admin",
        directory_path="root",
        allowed_tools=[ToolName.HOST_DIRECTORY_DELETE],
        host_directory_grants={"shared": ["delete"]},
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    first_file = host_root / "first.txt"
    first_file.write_text("restore me", encoding="utf-8")
    first_deleted = host_directory_service.delete_for_session(
        session=session,
        directory_id="shared",
        path="first.txt",
        expected_revision=host_directory_revision(first_file.lstat()),
    )
    quarantine = client.get("/api/admin/host-directories/shared/quarantine?directory_path=root")
    assert quarantine.status_code == 200
    assert [entry["recovery_id"] for entry in quarantine.json()["entries"]] == [
        first_deleted["recovery_id"]
    ]

    restored = client.post(
        "/api/admin/host-directories/shared/quarantine/"
        f"{first_deleted['recovery_id']}/restore?directory_path=root",
        json={},
    )
    assert restored.status_code == 200
    assert first_file.read_text(encoding="utf-8") == "restore me"

    second_file = host_root / "second.txt"
    second_file.write_text("purge me", encoding="utf-8")
    second_deleted = host_directory_service.delete_for_session(
        session=session,
        directory_id="shared",
        path="second.txt",
        expected_revision=host_directory_revision(second_file.lstat()),
    )
    purged = client.delete(
        "/api/admin/host-directories/shared/quarantine/"
        f"{second_deleted['recovery_id']}/purge?directory_path=root"
    )
    assert purged.status_code == 200
    assert not second_file.exists()
    assert (
        client.get("/api/admin/host-directories/shared/quarantine?directory_path=root").json()[
            "entries"
        ]
        == []
    )


def test_host_directory_admin_save_preserves_recoverability_until_quarantine_is_empty(
    tmp_path: Path,
) -> None:
    host_root = tmp_path / "shared-inbox"
    replacement_root = tmp_path / "replacement-inbox"
    host_root.mkdir()
    replacement_root.mkdir()
    service = build_admin_runtime(_runtime_dir(tmp_path))
    assert service.operations_admin.write_directory_domain_config(
        "root",
        "host_directories",
        {"host_directories": {"shared": _definition(host_root)}},
    ).ok
    client = TestClient(create_app(service))
    assert client.post("/api/admin/privileged/unlock").status_code == 200

    host_directory_service = service.host_directory_admin._deps.host_directory_service
    session = SessionManifest(
        session_id="session-host-directory-admin-config-guard",
        directory_path="root",
        allowed_tools=[ToolName.HOST_DIRECTORY_DELETE],
        host_directory_grants={"shared": ["delete"]},
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    target = host_root / "recoverable.txt"
    target.write_text("keep recoverable", encoding="utf-8")
    deleted = host_directory_service.delete_for_session(
        session=session,
        directory_id="shared",
        path=target.name,
        expected_revision=host_directory_revision(target.lstat()),
    )

    initial = client.get("/api/admin/host-directories?directory_path=root").json()
    same_root = _definition(host_root)
    same_root["description"] = "An allowed metadata-only update."
    metadata_update = client.put(
        "/api/admin/host-directories?directory_path=root",
        json={
            "expected_revision": initial["revision"],
            "config": {"host_directories": {"shared": same_root}},
        },
    )
    assert metadata_update.status_code == 200
    current = metadata_update.json()

    removal = client.put(
        "/api/admin/host-directories?directory_path=root",
        json={
            "expected_revision": current["revision"],
            "config": {"host_directories": {}},
        },
    )
    assert removal.status_code == 400
    assert "has quarantined deletions" in removal.json()["error"]

    replacement = _definition(replacement_root)
    root_change = client.put(
        "/api/admin/host-directories?directory_path=root",
        json={
            "expected_revision": current["revision"],
            "config": {"host_directories": {"shared": replacement}},
        },
    )
    assert root_change.status_code == 400
    assert "has quarantined deletions" in root_change.json()["error"]

    host_directory_service.restore_quarantine(
        directory_path="root",
        directory_id="shared",
        recovery_id=deleted["recovery_id"],
    )
    child = DirectoryService(service.runtime.directories).create(
        parent="root",
        name="team",
    )
    child_session = session.model_copy(
        update={
            "session_id": "session-host-directory-admin-child-guard",
            "directory_path": child.path,
        }
    )
    child_file = host_root / "child-recoverable.txt"
    child_file.write_text("keep child recoverable", encoding="utf-8")
    child_deleted = host_directory_service.delete_for_session(
        session=child_session,
        directory_id="shared",
        path=child_file.name,
        expected_revision=host_directory_revision(child_file.lstat()),
    )
    descendant_root_change = client.put(
        "/api/admin/host-directories?directory_path=root",
        json={
            "expected_revision": current["revision"],
            "config": {"host_directories": {"shared": replacement}},
        },
    )
    assert descendant_root_change.status_code == 400
    assert child.path in descendant_root_change.json()["error"]
    host_directory_service.restore_quarantine(
        directory_path=child.path,
        directory_id="shared",
        recovery_id=child_deleted["recovery_id"],
    )
    allowed_root_change = client.put(
        "/api/admin/host-directories?directory_path=root",
        json={
            "expected_revision": current["revision"],
            "config": {"host_directories": {"shared": replacement}},
        },
    )
    assert allowed_root_change.status_code == 200

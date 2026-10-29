from __future__ import annotations

from datetime import timedelta
import json
from pathlib import Path

import pytest

from evander.admin.composition import AdminRuntimeComponents, build_admin_runtime
from evander.models import SessionManifest, ToolName
from evander.repositories.session_manifests import JsonSessionManifestRepository


@pytest.fixture
def metadata_service(tmp_path: Path) -> AdminRuntimeComponents:
    service = build_admin_runtime(tmp_path / "runtime")
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            vm_id="vm-123",
            directory_path="root/team-a",
            allowed_tools=[ToolName.APPROVAL_CHECK],
            expires_at="2099-01-01T00:00:00Z",
            metadata={
                "issue_number": "123",
                "routing": {"enabled": True, "revision": 1},
                "tags": ["old"],
                "conversation": "thread-1",
            },
        ),
    )
    return service


@pytest.mark.parametrize("target", ["session-1", "vm-123", "123"])
def test_admin_service_session_update_metadata_merges_into_manifest(
    metadata_service: AdminRuntimeComponents, target: str
) -> None:
    result = metadata_service.issue_git_admin.session_update_metadata(
        target=target,
        metadata={
            "issue_title": "Fix App",
            "routing": {"enabled": False},
            "tags": ["new"],
            "conversation": None,
        },
    )

    expected_metadata = {
        "issue_number": "123",
        "issue_title": "Fix App",
        "routing": {"enabled": False, "revision": 1},
        "tags": ["new"],
        "conversation": None,
    }
    assert result["success"] is True
    assert result["action"] == "session_update_metadata"
    assert result["session_id"] == "session-1"
    assert result["vm_name"] == "vm-123"
    assert result["directory_path"] == "root/team-a"
    assert result["metadata"] == expected_metadata
    stored = metadata_service.runtime.session_repository.get_session("session-1")
    assert stored is not None
    assert stored.metadata == expected_metadata


@pytest.mark.parametrize("patch", [{}, {"routing": {"enabled": True}, "tags": ["old"]}])
def test_session_metadata_noop_does_not_rewrite_the_session_store(
    metadata_service: AdminRuntimeComponents, patch: dict[str, object]
) -> None:
    repository = metadata_service.runtime.session_repository
    before = repository.path.stat()
    content = repository.path.read_bytes()

    result = metadata_service.issue_git_admin.session_update_metadata(
        target="session-1", metadata=patch
    )

    after = repository.path.stat()
    assert (after.st_ino, after.st_mtime_ns, after.st_ctime_ns) == (
        before.st_ino,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    assert repository.path.read_bytes() == content
    assert result["success"] is True
    assert result["metadata"]["routing"] == {"enabled": True, "revision": 1}
    assert result["metadata"]["conversation"] == "thread-1"


@pytest.mark.parametrize("patch, enabled", [({}, True), ({"routing": {"enabled": False}}, False)])
def test_session_metadata_updates_accept_manifest_without_creation_timestamp(
    metadata_service: AdminRuntimeComponents, patch: dict[str, object], enabled: bool
) -> None:
    repository = metadata_service.runtime.session_repository
    payload = json.loads(repository.path.read_text())
    del payload["session-1"]["created_at"]
    repository.path.write_text(json.dumps(payload))
    content = repository.path.read_bytes()
    before = repository.path.stat()

    result = metadata_service.issue_git_admin.session_update_metadata(
        target="session-1", metadata=patch
    )

    assert result["success"] is True
    assert result["metadata"]["routing"] == {"enabled": enabled, "revision": 1}
    assert result["metadata"]["conversation"] == "thread-1"
    stored = JsonSessionManifestRepository(repository.path).get_session("session-1")
    assert stored is not None
    assert stored.metadata["routing"] == {"enabled": enabled, "revision": 1}
    if not patch:
        assert repository.path.read_bytes() == content
        after = repository.path.stat()
        assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)


def test_session_metadata_patch_preserves_concurrent_updates(
    metadata_service: AdminRuntimeComponents, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = metadata_service.runtime.session_repository
    other = JsonSessionManifestRepository(repository.path)
    resolve = repository.resolve_session_target

    def resolve_before_concurrent_update(target):
        original = resolve(target)
        assert original is not None
        other.upsert_session(
            original.model_copy(
                update={
                    "allowed_tools": [],
                    "metadata": {
                        **original.metadata,
                        "routing": {"enabled": False, "revision": 2},
                        "external": "preserved",
                    },
                }
            )
        )
        return original

    monkeypatch.setattr(repository, "resolve_session_target", resolve_before_concurrent_update)
    result = metadata_service.issue_git_admin.session_update_metadata(
        target="session-1", metadata={"routing": {"enabled": True}}
    )

    stored = other.get_session("session-1")
    assert stored is not None
    assert stored.allowed_tools == []
    assert stored.metadata == {
        "issue_number": "123",
        "routing": {"enabled": True, "revision": 2},
        "tags": ["old"],
        "conversation": "thread-1",
        "external": "preserved",
    }
    assert result["metadata"] == stored.metadata


@pytest.mark.parametrize("change", ["delete", "replace"])
def test_session_metadata_patch_rejects_a_removed_or_replaced_target(
    metadata_service: AdminRuntimeComponents, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    repository = metadata_service.runtime.session_repository
    other = JsonSessionManifestRepository(repository.path)
    resolve = repository.resolve_session_target

    def resolve_before_lifecycle_change(target):
        original = resolve(target)
        assert original is not None
        other.delete_session(original.session_id)
        if change == "replace":
            other.upsert_session(
                original.model_copy(
                    update={
                        "created_at": original.created_at + timedelta(seconds=1),
                        "metadata": {"replacement": True},
                    }
                )
            )
        return original

    monkeypatch.setattr(repository, "resolve_session_target", resolve_before_lifecycle_change)
    with pytest.raises(ValueError, match="Unknown session|replaced"):
        metadata_service.issue_git_admin.session_update_metadata(
            target="session-1", metadata={"issue_title": "Stale update"}
        )

    stored = other.get_session("session-1")
    if change == "delete":
        assert stored is None
    else:
        assert stored is not None
        assert stored.metadata == {"replacement": True}


def test_admin_service_session_context_query_returns_generic_fields(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    service = build_admin_runtime(runtime_dir)
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            vm_id="vm-1",
            model="gpt-5.4",
            directory_path="root",
            allowed_tools=[ToolName.APPROVAL_CHECK, ToolName.RUN_SCRIPT],
            approval_required_tools=[ToolName.RUN_SCRIPT],
            expires_at="2099-01-01T00:00:00Z",
            metadata={"issue_number": "14084", "issue_title": "Fix App"},
        ),
    )

    result = service.issue_git_admin.session_context_query(target="session-1")

    assert result["session_id"] == "session-1"
    assert result["vm_name"] == "vm-1"
    assert result["directory_path"] == "root"
    assert result["metadata"]["issue_number"] == "14084"
    assert result["model"] == "gpt-5.4"
    assert result["tool_summary"]["session_id"] == "session-1"
    assert result["tool_summary"]["approval_required_tools"] == ["run_script"]
    available_tools = {
        entry["tool_name"]: entry for entry in result["tool_summary"]["available_tools"]
    }
    assert available_tools["run_script"]["requires_approval"] is True
    assert "approval_check" in available_tools
    unavailable_tools = {
        entry["tool_name"]: entry for entry in result["tool_summary"]["unavailable_tools"]
    }
    assert unavailable_tools["get_workflow_snapshot"]["reason"] == (
        "Tool get_workflow_snapshot is not allowed for session session-1"
    )

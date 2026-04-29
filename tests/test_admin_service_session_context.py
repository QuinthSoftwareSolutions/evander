from __future__ import annotations

from pathlib import Path

from agent_sandbox.admin.service import AdminService
from agent_sandbox.models import SessionManifest


def test_admin_service_session_update_metadata_merges_into_manifest(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    service = AdminService(runtime_dir)
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            vm_id="vm-1",
            directory_path="root/team-a",
            allowed_tools=[],
            expires_at="2099-01-01T00:00:00Z",
            metadata={"issue_number": "14084"},
        ),
    )

    result = service.session_update_metadata(
        target="session-1", metadata={"issue_title": "Fix APP"}
    )

    assert result["success"] is True
    assert result["metadata"] == {"issue_number": "14084", "issue_title": "Fix APP"}
    stored = service.runtime.session_repository.get_session("session-1")
    assert stored is not None
    assert stored.metadata == {"issue_number": "14084", "issue_title": "Fix APP"}


def test_admin_service_session_context_query_returns_generic_fields(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    service = AdminService(runtime_dir)
    service.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="session-1",
            vm_id="vm-1",
            model="gpt-5.4",
            directory_path="root/team-a",
            allowed_tools=[],
            expires_at="2099-01-01T00:00:00Z",
            metadata={"issue_number": "14084", "issue_title": "Fix APP"},
        ),
    )

    result = service.session_context_query(target="session-1")

    assert result["session_id"] == "session-1"
    assert result["vm_name"] == "vm-1"
    assert result["directory_path"] == "root/team-a"
    assert result["metadata"]["issue_number"] == "14084"
    assert result["model"] == "gpt-5.4"

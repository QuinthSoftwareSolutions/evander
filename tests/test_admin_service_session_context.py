from __future__ import annotations

from pathlib import Path

from agent_sandbox.admin.composition import AdminRuntimeComponents, build_admin_runtime
from agent_sandbox.models import SessionManifest, ToolName


def test_admin_service_session_update_metadata_merges_into_manifest(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    service = build_admin_runtime(runtime_dir)
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

    result = service.issue_git_admin.session_update_metadata(
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
            metadata={"issue_number": "14084", "issue_title": "Fix APP"},
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

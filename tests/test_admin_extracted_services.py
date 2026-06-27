from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from agent_sandbox.admin.services import (
    CapacityHealthAdminService,
    ModelUsageAdminService,
    WorkflowGenerationAdminService,
    WorkflowReportsAdminService,
)


class RecordingHost:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def _read_capacity_health_impl(self):
        self.calls.append(("capacity", None))
        return "capacity-payload"

    def _list_model_usage_events_impl(self, **kwargs):
        self.calls.append(("list-usage", kwargs))
        return ["usage-event"]

    def _operator_steering_directory_path_impl(self, **kwargs):
        self.calls.append(("steering-dir", kwargs))
        return "root/team"

    def _active_operator_steering_context_impl(self, **kwargs):
        self.calls.append(("active-context", kwargs))
        return "context"

    def _create_operator_steering_note_impl(self, **kwargs):
        self.calls.append(("create-note", kwargs))
        return "note"

    def _submit_operator_steering_note_impl(self, **kwargs):
        self.calls.append(("submit-note", kwargs))
        return {"ok": True}

    async def _generate_workflow_editor_draft_public_impl(self, **kwargs):
        self.calls.append(("generate", kwargs))
        return "draft"

    def _read_workflow_graph_activity_report_public_impl(self, **kwargs):
        self.calls.append(("activity-report", kwargs))
        return "activity"

    def _read_workflow_failure_clusters_report_public_impl(self, **kwargs):
        self.calls.append(("failure-report", kwargs))
        return "failures"

    def _read_conformance_reports_public_impl(self):
        self.calls.append(("conformance", None))
        return "conformance"

    def _run_conformance_report_public_impl(self, kind):
        self.calls.append(("run-conformance", kind))
        return "run"


def test_capacity_health_service_delegates_to_extracted_host_logic() -> None:
    host = RecordingHost()
    assert CapacityHealthAdminService(host).read_capacity_health() == "capacity-payload"
    assert host.calls == [("capacity", None)]


def test_model_usage_service_forwards_filters_and_steering_operations() -> None:
    host = RecordingHost()
    service = ModelUsageAdminService(host)
    after = datetime(2026, 1, 1, tzinfo=UTC)

    assert service.list_model_usage_events(
        session_id="s1", provider="openai", occurred_after=after, limit=5
    ) == ["usage-event"]
    assert (
        service.operator_steering_directory_path(session_id="s1", dispatch_id="d1")
        == "root/team"
    )
    assert service.active_operator_steering_context(session_id="s1") == "context"
    assert (
        service.create_operator_steering_note(session_id="s1", dispatch_id="d1", message="go")
        == "note"
    )
    assert service.submit_operator_steering_note(
        session_id="s1", dispatch_id="d1", message="go"
    ) == {"ok": True}

    assert host.calls[0][0] == "list-usage"
    assert host.calls[0][1]["session_id"] == "s1"
    assert host.calls[0][1]["provider"] == "openai"
    assert host.calls[0][1]["occurred_after"] is after
    assert host.calls[0][1]["limit"] == 5
    assert [name for name, _ in host.calls[1:]] == [
        "steering-dir",
        "active-context",
        "create-note",
        "submit-note",
    ]


def test_workflow_generation_service_delegates_generation_request() -> None:
    host = RecordingHost()
    result = asyncio.run(
        WorkflowGenerationAdminService(host).generate_workflow_editor_draft(
            description="triage issues", directory_path="root/team", include_current_packs=False
        )
    )

    assert result == "draft"
    assert host.calls == [
        (
            "generate",
            {
                "description": "triage issues",
                "directory_path": "root/team",
                "include_current_packs": False,
            },
        )
    ]


def test_workflow_reports_service_delegates_report_operations() -> None:
    host = RecordingHost()
    service = WorkflowReportsAdminService(host)

    assert service.read_workflow_graph_activity_report(limit=3) == "activity"
    assert service.read_workflow_failure_clusters_report(recent_threshold_seconds=60) == "failures"
    assert service.read_conformance_reports() == "conformance"
    assert service.run_conformance_report("trigger") == "run"

    assert [name for name, _ in host.calls] == [
        "activity-report",
        "failure-report",
        "conformance",
        "run-conformance",
    ]
    assert host.calls[0][1]["limit"] == 3
    assert host.calls[1][1]["recent_threshold_seconds"] == 60

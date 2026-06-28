from __future__ import annotations

from datetime import UTC, datetime

from agent_sandbox.admin.services import (
    ModelUsageAdminService,
    WorkflowGenerationAdminService,
    WorkflowReportsAdminService,
)


class RecordingHost:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def _read_workflow_graph_activity_report(self, **kwargs):
        self.calls.append(("activity-report", kwargs))
        return "activity"

    def _read_workflow_failure_clusters_report(self, **kwargs):
        self.calls.append(("failure-report", kwargs))
        return "failures"

    def _read_conformance_reports(self):
        self.calls.append(("conformance", None))
        return "conformance"

    def _run_conformance_report(self, kind):
        self.calls.append(("run-conformance", kind))
        return "run"


def test_model_usage_service_validates_operator_steering_messages() -> None:
    assert ModelUsageAdminService._validate_operator_steering_message(" go ") == "go"

    try:
        ModelUsageAdminService._validate_operator_steering_message("   ")
    except ValueError as exc:
        assert "message is required" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("expected blank operator steering messages to be rejected")


def test_workflow_generation_service_enforces_in_flight_and_cooldown() -> None:
    host = RecordingHost()
    service = WorkflowGenerationAdminService(host)

    service._begin_workflow_generation_request()
    try:
        try:
            service._begin_workflow_generation_request()
        except ValueError as exc:
            assert "already in progress" in str(exc)
        else:  # pragma: no cover - defensive assertion
            raise AssertionError("expected in-flight workflow generation to be rejected")
    finally:
        service._finish_workflow_generation_request()

    try:
        service._begin_workflow_generation_request()
    except ValueError as exc:
        assert "cooling down" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("expected cooldown workflow generation to be rejected")


def test_workflow_reports_service_delegates_report_operations() -> None:
    host = RecordingHost()
    service = WorkflowReportsAdminService(host)

    assert service.read_workflow_graph_activity_report(limit=3) == "activity"
    assert (
        service.read_workflow_failure_clusters_report(recent_threshold_seconds=60) == "failures"
    )
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

from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace

from evander.admin.composition import AdminRuntimeComponents
from evander.admin.services import (
    ApprovalAuditAdminDependencies,
    ApprovalAuditAdminService,
    CapacityHealthAdminDependencies,
    CapacityHealthAdminService,
    IssueGitAdminService,
    IssueGitAdminSupport,
    ModelUsageAdminDependencies,
    ModelUsageAdminService,
    OperatorSteeringAdminDependencies,
    OperatorSteeringAdminService,
    OperationsAdminService,
    SessionAdminService,
    TeamsAdminService,
    WakeScheduleAdminService,
    WorkerControlAdminService,
    WorkflowGenerationAdminDependencies,
    WorkflowGenerationAdminService,
    WorkflowConfigAdminService,
    WorkflowReportsAdminDependencies,
    WorkflowReportsAdminService,
)
from evander.admin.services.teams_service import TeamsGraphClient

ADMIN_SERVICES_DIR = Path(__file__).resolve().parents[1] / "src" / "evander" / "admin" / "services"
ADMIN_SRC_DIR = Path(__file__).resolve().parents[1] / "src" / "evander" / "admin"


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


def test_extracted_capacity_and_model_usage_services_use_explicit_dependencies() -> None:
    expected = [
        (ModelUsageAdminService, ModelUsageAdminDependencies),
        (CapacityHealthAdminService, CapacityHealthAdminDependencies),
        (OperatorSteeringAdminService, OperatorSteeringAdminDependencies),
        (ApprovalAuditAdminService, ApprovalAuditAdminDependencies),
    ]
    for service_cls, dependencies_cls in expected:
        parameters = inspect.signature(service_cls).parameters
        assert list(parameters) == ["dependencies"]
        assert parameters["dependencies"].annotation in {
            dependencies_cls,
            dependencies_cls.__name__,
        }


def test_admin_extracted_services_do_not_keep_raw_admin_service_hosts() -> None:
    scanned_files = [
        path for path in ADMIN_SERVICES_DIR.glob("*.py") if path.name not in {"__init__.py"}
    ]
    offenders: dict[str, list[str]] = {}
    for path in scanned_files:
        text = path.read_text(encoding="utf-8")
        hits = [
            token
            for token in ("self._host", "_host = host", "Legacy" + "AdminRuntimeComponents")
            if token in text
        ]
        if hits:
            offenders[path.name] = hits
    assert offenders == {}


def test_admin_refactor_does_not_add_legacy_dumping_ground() -> None:
    paths = [
        *ADMIN_SRC_DIR.rglob("*.py"),
        *(Path(__file__).resolve().parents[1] / "tests").glob("test_*.py"),
    ]
    forbidden_tokens = (
        "service_" + "legacy",
        "Legacy" + "AdminRuntimeComponents",
        "AdminRuntimeComponents" + "WorkflowConfigPort",
    )
    offenders: dict[str, list[str]] = {}
    for path in paths:
        text = path.read_text(encoding="utf-8")
        hits = [token for token in forbidden_tokens if token in text]
        if hits:
            offenders[str(path.relative_to(Path(__file__).resolve().parents[1]))] = hits
    assert offenders == {}


def test_issue_git_local_issue_methods_live_on_issue_git_components() -> None:
    support_methods = (
        "next_local_issue_number",
        "local_issue_creation_lock",
        "canonical_local_issue_id",
        "canonical_issue_id",
        "write_local_issue_yaml",
        "refresh_proxy_cache_from_remote",
        "proxy_target_for_issue",
    )

    assert not hasattr(IssueGitAdminService, "import_git_repo")
    assert not hasattr(AdminRuntimeComponents, "import_git_repo")
    for method_name in support_methods:
        assert hasattr(IssueGitAdminSupport, method_name)
        assert not hasattr(AdminRuntimeComponents, method_name)


def test_admin_service_model_pricing_methods_are_thin_delegates() -> None:
    method_names = (
        "read_model_pricing_catalog_status",
        "_read_model_pricing_catalog_status_unlocked",
        "write_model_pricing_catalog",
        "_model_pricing_error_revision",
        "_model_pricing_rule_summary",
    )
    service_source = inspect.getsource(ModelUsageAdminService)
    for method_name in method_names:
        assert hasattr(ModelUsageAdminService, method_name)
        assert not hasattr(AdminRuntimeComponents, method_name)
    for token in (
        "default_model_pricing_catalog_path",
        "load_model_pricing_catalog_payload",
        "normalize_model_pricing_catalog_payload",
        "clear_model_pricing_catalog_cache",
        "admin_config_mutation_lock",
    ):
        assert token in service_source


def test_admin_service_model_usage_visibility_and_audit_methods_are_thin_delegates() -> None:
    method_names = (
        "list_model_usage_events",
        "tool_visibility_payload",
        "list_tool_call_audit_entries",
        "_list_tool_call_audit_entries_from_index",
    )
    for method_name in method_names:
        owner = (
            ModelUsageAdminService
            if method_name == "list_model_usage_events"
            else ApprovalAuditAdminService
        )
        assert hasattr(owner, method_name)
        assert not hasattr(AdminRuntimeComponents, method_name)


def test_tool_call_audit_index_is_owned_by_approval_audit_service() -> None:
    assert "tool_call_audit_index" in inspect.signature(ApprovalAuditAdminDependencies).parameters
    assert "tool_call_audit_index" in inspect.getsource(ApprovalAuditAdminService)
    assert "tool_call_audit_index" in AdminRuntimeComponents.__dataclass_fields__


def test_scheduled_wake_ledger_retry_methods_live_on_wake_schedule_service() -> None:
    method_names = (
        "normalize_wake_ledger_statuses",
        "_wake_ledger_entry_for_request",
        "list_scheduled_wake_ledger",
        "retry_scheduled_wake",
        "_active_wake_requests",
        "_enforce_wake_schedule_guardrails",
    )
    for method_name in method_names:
        owner = (
            OperationsAdminService
            if method_name in {"list_scheduled_wake_ledger", "retry_scheduled_wake"}
            else WakeScheduleAdminService
        )
        assert hasattr(owner, method_name)
        assert not hasattr(SessionAdminService, method_name)
        assert not hasattr(AdminRuntimeComponents, method_name)


def test_teams_auth_service_uses_graph_client_boundary() -> None:
    method_names = (
        "_teams_auth_provider_for_directory",
        "_teams_auth_payload",
        "_graph_client",
    )
    implementation_tokens = (
        "JsonTeamsIdentityCredentialStore",
        "MicrosoftDeviceCodeAuthConfig",
        "TeamsDeviceCodeTokenProvider(",
        "teams_delegated_client_id",
        "teams_delegated_credential_store_path",
        '"authenticated"',
        '"store_path"',
    )

    for method_name in method_names:
        source = inspect.getsource(getattr(TeamsAdminService, method_name))
        assert not hasattr(AdminRuntimeComponents, method_name)
        if method_name == "_graph_client":
            assert "TeamsGraphClient" in source
        else:
            assert any(token in source for token in implementation_tokens)

    service_source = inspect.getsource(TeamsAdminService)
    graph_client_source = inspect.getsource(TeamsGraphClient)
    assert "httpx.Client" not in service_source
    assert "httpx.Client" in graph_client_source


def test_worker_control_service_does_not_delegate_workflow_config() -> None:
    forbidden = (
        "read_workflow_editor_document",
        "read_workflow_run_search",
        "clear_workflow_run",
    )
    for method_name in forbidden:
        assert not hasattr(WorkerControlAdminService, method_name)


def test_workflow_config_service_does_not_expose_legacy_node_mutations() -> None:
    for method_name in (
        "upsert_trigger_config",
        "delete_trigger_config",
        "upsert_effect_config",
        "delete_effect_config",
    ):
        assert not hasattr(WorkflowConfigAdminService, method_name)


def test_workflow_generation_service_enforces_in_flight_and_cooldown() -> None:
    service = WorkflowGenerationAdminService(
        WorkflowGenerationAdminDependencies(
            config=SimpleNamespace(),
            runtime=SimpleNamespace(),
            workflow_config_admin=SimpleNamespace(),
            normalize_directory_reference=lambda value: value or "root",
            read_assistant_settings=lambda: {},
        )
    )

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
    service = WorkflowReportsAdminService(
        WorkflowReportsAdminDependencies(
            project_dir=Path("."),
            config=SimpleNamespace(runtime_dir=Path(".")),
            runtime=SimpleNamespace(),
            trigger_event_repository=SimpleNamespace(),
            workflow_config_admin=SimpleNamespace(),
            list_agents=lambda **kwargs: [],
        )
    )
    service._read_workflow_graph_activity_report = host._read_workflow_graph_activity_report
    service._read_workflow_failure_clusters_report = host._read_workflow_failure_clusters_report
    service._read_conformance_reports = host._read_conformance_reports
    service._run_conformance_report = host._run_conformance_report

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

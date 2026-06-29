from __future__ import annotations

from datetime import UTC, datetime
import inspect
from pathlib import Path
from types import SimpleNamespace

from agent_sandbox.admin.service import AdminService
from agent_sandbox.admin.services import (
    ApprovalAuditAdminDependencies,
    ApprovalAuditAdminService,
    CapacityHealthAdminDependencies,
    CapacityHealthAdminService,
    ModelUsageAdminDependencies,
    ModelUsageAdminService,
    OperatorSteeringAdminDependencies,
    OperatorSteeringAdminService,
    WorkflowGenerationAdminDependencies,
    WorkflowGenerationAdminService,
    WorkflowReportsAdminDependencies,
    WorkflowReportsAdminService,
)

ADMIN_SERVICES_DIR = Path(__file__).resolve().parents[1] / "src" / "agent_sandbox" / "admin" / "services"
ADMIN_SRC_DIR = Path(__file__).resolve().parents[1] / "src" / "agent_sandbox" / "admin"


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
        path
        for path in ADMIN_SERVICES_DIR.glob("*.py")
        if path.name not in {"__init__.py"}
    ]
    offenders: dict[str, list[str]] = {}
    for path in scanned_files:
        text = path.read_text(encoding="utf-8")
        hits = [
            token
            for token in ("self._host", "_host = host", "Legacy" + "AdminService")
            if token in text
        ]
        if hits:
            offenders[path.name] = hits
    assert offenders == {}


def test_admin_refactor_does_not_add_legacy_dumping_ground() -> None:
    paths = [*ADMIN_SRC_DIR.rglob("*.py"), *(Path(__file__).resolve().parents[1] / "tests").glob("test_*.py")]
    forbidden_tokens = (
        "service_" + "legacy",
        "Legacy" + "AdminService",
        "AdminService" + "WorkflowConfigPort",
    )
    offenders: dict[str, list[str]] = {}
    for path in paths:
        text = path.read_text(encoding="utf-8")
        hits = [token for token in forbidden_tokens if token in text]
        if hits:
            offenders[str(path.relative_to(Path(__file__).resolve().parents[1]))] = hits
    assert offenders == {}


def test_admin_service_issue_git_local_issue_methods_are_thin_delegates() -> None:
    method_names = (
        "import_git_repo",
        "_extract_local_issue_id_candidate",
        "_next_local_issue_number",
        "_local_issue_creation_lock",
        "_canonical_local_issue_id",
        "_canonical_issue_id",
        "_write_local_issue_yaml",
        "_refresh_proxy_cache_from_remote",
        "_proxy_target_for_issue",
    )
    delegate_expectations = {
        "import_git_repo": "issue_git_admin",
        "_extract_local_issue_id_candidate": "issue_git_support",
        "_next_local_issue_number": "issue_git_support",
        "_local_issue_creation_lock": "issue_git_support",
        "_canonical_local_issue_id": "issue_git_support",
        "_canonical_issue_id": "issue_git_support",
        "_write_local_issue_yaml": "issue_git_support",
        "_refresh_proxy_cache_from_remote": "issue_git_support",
        "_proxy_target_for_issue": "issue_git_support",
    }
    implementation_tokens = (
        "normalized_source =",
        "resolve_repo_source(",
        "sync_repo_into_bare_remote(",
        "_normalize_import_bare_repo_name",
        "_bare_repo_agent_refs",
        "_git_config_value",
        "_infer_git_provider_from_url",
        "GitImportConflictError(",
        "local_issue_counter_path",
        "local_issue_create_lock_path",
        "fcntl.flock",
        "os.open",
        "yaml.safe_load",
        "yaml.safe_dump",
        "_scoped_issue_id",
        "remote.work_item_id",
        "metadata_steps",
        '"proxy_cache"',
        "last_synced_at",
        "resolve_proxy_target(",
    )

    offenders: dict[str, list[str]] = {}
    for method_name in method_names:
        source = inspect.getsource(getattr(AdminService, method_name))
        assert delegate_expectations[method_name] in source
        hits = [token for token in implementation_tokens if token in source]
        if hits:
            offenders[method_name] = hits
    assert offenders == {}


def test_admin_service_model_pricing_methods_are_thin_delegates() -> None:
    method_names = (
        "read_model_pricing_catalog_status",
        "_read_model_pricing_catalog_status_unlocked",
        "write_model_pricing_catalog",
        "_model_pricing_error_revision",
        "_model_pricing_rule_summary",
    )
    implementation_tokens = (
        "default_model_pricing_catalog_path",
        "load_model_pricing_catalog_payload",
        "normalize_model_pricing_catalog_payload",
        "clear_model_pricing_catalog_cache",
        "admin_config_mutation_lock",
        "_write_json_file_atomic",
        "_stable_json_revision",
        "json.loads",
        "path.read_text",
        "path.parent.mkdir",
    )
    offenders: dict[str, list[str]] = {}
    for method_name in method_names:
        source = inspect.getsource(getattr(AdminService, method_name))
        assert "model_usage_admin" in source
        hits = [token for token in implementation_tokens if token in source]
        if hits:
            offenders[method_name] = hits
    assert offenders == {}


def test_admin_service_model_usage_visibility_and_audit_methods_are_thin_delegates() -> None:
    method_names = (
        "list_model_usage_events",
        "tool_visibility_payload",
        "list_tool_call_audit_entries",
        "_list_tool_call_audit_entries_from_index",
    )
    delegate_expectations = {
        "list_model_usage_events": "model_usage_admin.list_model_usage_events",
        "tool_visibility_payload": "approval_audit_admin.tool_visibility_payload",
        "list_tool_call_audit_entries": (
            "approval_audit_admin.list_tool_call_audit_entries"
        ),
        "_list_tool_call_audit_entries_from_index": (
            "approval_audit_admin._list_tool_call_audit_entries_from_index"
        ),
    }
    implementation_tokens = (
        "model_usage_store(",
        "build_tool_visibility_payload(",
        "runtime.session_repository",
        "Unknown session",
        "target is required",
        "tool_call_audit_index =",
    )

    offenders: dict[str, list[str]] = {}
    for method_name in method_names:
        source = inspect.getsource(getattr(AdminService, method_name))
        assert delegate_expectations[method_name] in source
        hits = [token for token in implementation_tokens if token in source]
        if hits:
            offenders[method_name] = hits
    assert offenders == {}


def test_admin_service_tool_call_audit_index_property_proxies_approval_service() -> None:
    service = AdminService.__new__(AdminService)
    initial_index = object()
    service.tool_call_audit_index = initial_index

    assert service.tool_call_audit_index is initial_index

    approval_audit_admin = SimpleNamespace(tool_call_audit_index=initial_index)
    service.approval_audit_admin = approval_audit_admin
    replacement_index = object()
    service.tool_call_audit_index = replacement_index

    assert service.tool_call_audit_index is replacement_index
    assert approval_audit_admin.tool_call_audit_index is replacement_index

    service.tool_call_audit_index = None

    assert service.tool_call_audit_index is None
    assert approval_audit_admin.tool_call_audit_index is None


def test_admin_service_scheduled_wake_ledger_retry_methods_are_thin_delegates() -> None:
    method_names = (
        "_normalize_wake_ledger_statuses",
        "_wake_ledger_entry_for_request",
        "list_scheduled_wake_ledger",
        "retry_scheduled_wake",
        "_active_wake_requests",
        "_enforce_wake_schedule_guardrails",
    )
    implementation_tokens = (
        "allowed_statuses",
        "Invalid wake ledger status",
        "latest_dispatch_by_request_id",
        "list_ledger_entries",
        "Wake schedule not found",
        "is not retryable in status",
        "retry_history.append",
        "previous_auto_retry_started_at",
        "auto_dispatch_attempts = 0",
        "list_requests(include_disabled=False)",
        "wake_schedule_max_active_per_session",
        "_cron_min_interval_seconds",
    )
    delegate_expectations = {
        "_normalize_wake_ledger_statuses": "_admin_service_normalize_wake_ledger_statuses",
        "_wake_ledger_entry_for_request": "session_admin",
        "list_scheduled_wake_ledger": "operations_admin",
        "retry_scheduled_wake": "operations_admin",
        "_active_wake_requests": "session_admin",
        "_enforce_wake_schedule_guardrails": "session_admin",
    }

    offenders: dict[str, list[str]] = {}
    for method_name in method_names:
        source = inspect.getsource(getattr(AdminService, method_name))
        assert delegate_expectations[method_name] in source
        hits = [token for token in implementation_tokens if token in source]
        if hits:
            offenders[method_name] = hits
    assert offenders == {}


def test_admin_service_teams_auth_private_helpers_are_thin_delegates() -> None:
    method_names = (
        "_teams_auth_provider_for_directory",
        "_teams_auth_payload",
        "_teams_graph_list_channels",
    )
    implementation_tokens = (
        "JsonTeamsIdentityCredentialStore",
        "MicrosoftDeviceCodeAuthConfig",
        "TeamsDeviceCodeTokenProvider(",
        "teams_delegated_client_id",
        "teams_delegated_credential_store_path",
        "teams_graph_api_base_url",
        "get_bearer_token",
        "httpx.Client",
        "quote(",
        '"authenticated"',
        '"store_path"',
    )

    offenders: dict[str, list[str]] = {}
    for method_name in method_names:
        source = inspect.getsource(getattr(AdminService, method_name))
        assert "teams_admin" in source
        hits = [token for token in implementation_tokens if token in source]
        if hits:
            offenders[method_name] = hits
    assert offenders == {}


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

from __future__ import annotations

import os
import fcntl
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from agent_sandbox.agent_control import AgentControlStore
from agent_sandbox.admin.directory import DirectoryAdminService
from agent_sandbox.admin.operations_helpers import PrivilegedHelperSession
from agent_sandbox.admin.payloads import AdminRevisionConflict
from agent_sandbox.admin.services import (
    ApprovalAuditAdminDependencies,
    ApprovalAuditAdminService,
    ArtifactAdminDependencies,
    ArtifactAdminService,
    CapacityHealthAdminDependencies,
    CapacityHealthAdminService,
    IssueGitAdminDependencies,
    IssueGitAdminService,
    IssueGitAdminSupport,
    ModelUsageAdminDependencies,
    ModelUsageAdminService,
    OperatorSteeringAdminDependencies,
    OperatorSteeringAdminService,
    OperationsAdminService,
    PrIntegrationPreflightAdminDependencies,
    PrIntegrationPreflightAdminService,
    SkillAdminService,
    SessionAdminDependencies,
    SessionAdminService,
    TeamsAdminDependencies,
    TeamsAdminService,
    VmAgentAdminDependencies,
    VmAgentAdminService,
    VmAgentAdminSupport,
    VmInventoryAdminDependencies,
    VmInventoryAdminService,
    WakeScheduleAdminDependencies,
    WakeScheduleAdminService,
    WorkerControlAdminService,
    WorkflowConfigAdminDependencies,
    WorkflowConfigAdminService,
    WorkflowGenerationAdminDependencies,
    WorkflowGenerationAdminService,
    WorkflowReportsAdminDependencies,
    WorkflowReportsAdminService,
)
from agent_sandbox.admin.services import project_helpers, session_helpers
from agent_sandbox.admin.services.local_issue_identity import LocalIssueIdentityResolver
from agent_sandbox.admin.services.vm_agent.common_models import (
    AGENT_CREATE_OPERATION_MAX_WORKERS,
    AgentCreateOperation,
)
from agent_sandbox.bootstrap import (
    load_host_bootstrap_config,
    load_runtime_gateway_config,
    resolve_project_dir,
    resolve_runtime_dir,
)
from agent_sandbox.communication_artifacts import CommunicationArtifactStore
from agent_sandbox.directory_config import DirectoryConfigStore
from agent_sandbox.directories import DirectoryService, DirectoryStore
from agent_sandbox.host_config import DEFAULT_RUNTIME_DIR
from agent_sandbox.issues.service import IssueService
from agent_sandbox.ownership import DirectoryOwnershipQueries
from agent_sandbox.provider_config import ProviderConfigResolver
from agent_sandbox.repositories.audit_index import ToolCallAuditIndex
from agent_sandbox.repositories.triggers import (
    JsonTriggerEventRepository,
    TriggerEventRepository,
    TriggerRepository,
    WorkerConfigTriggerRepository,
)
from agent_sandbox.repositories.skills_library import SkillLibraryRepository
from agent_sandbox.runtime import RuntimeContext, build_runtime
from agent_sandbox.storage import JsonStore
from agent_sandbox.models import LocalIssueRecord


_ADMIN_CONFIG_MUTATION_THREAD_LOCK = threading.RLock()


@contextmanager
def admin_config_mutation_file_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with _ADMIN_CONFIG_MUTATION_THREAD_LOCK:
        with path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class AdminRuntimeDirectoryOwnershipPort:
    def __init__(self, issue_git_support: Any) -> None:
        self._issue_git_support = issue_git_support

    def normalize_directory_path(self, directory_path: str | None) -> str:
        return self._issue_git_support.normalize_directory_path(directory_path)

    def direct_owned_entity_ids(self, directory_path: str, *, kind: str) -> set[str]:
        return self._issue_git_support.direct_owned_entity_ids(directory_path, kind=kind)


class AdminRuntimePrivilegedHelperPort:
    def __init__(self, host: Any) -> None:
        self._host = host

    def get_privileged_helper(self) -> Any | None:
        return self._host._privileged_helper

    def get_use_privileged_helper(self) -> bool:
        return bool(self._host.use_privileged_helper)

    def set_use_privileged_helper(self, enabled: bool) -> None:
        self._host.use_privileged_helper = bool(enabled)

    def set_privileged_helper(self, helper: Any | None) -> None:
        self._host._privileged_helper = helper


class AdminRuntimePrivilegedHelperFactoryPort:
    def __init__(self, helper_cls_provider: Any) -> None:
        self._helper_cls_provider = helper_cls_provider

    def create_privileged_helper(self, **kwargs: Any) -> Any:
        return self._helper_cls_provider()(**kwargs)


@dataclass(slots=True)
class AdminRuntimeComponents:
    runtime_dir: Path
    legacy_config_path: Path | None
    project_dir: Path
    config: Any
    runtime: RuntimeContext
    directory_store: DirectoryStore
    directory_config_store: DirectoryConfigStore
    tool_call_audit_index: ToolCallAuditIndex | None
    skill_library: SkillLibraryRepository
    agent_control_store: AgentControlStore
    local_issue_store: JsonStore
    local_issue_identity_resolver: Any
    local_issue_counter_path: Path
    local_issue_create_lock_path: Path
    admin_config_mutation_lock_path: Path
    local_issue_yaml_dir: Path
    workflow_generation_transport: httpx.AsyncBaseTransport | None
    _privileged_helper_port: AdminRuntimePrivilegedHelperPort
    directory_admin: DirectoryAdminService
    directory_ownership: DirectoryOwnershipQueries
    issue_service: IssueService
    session_admin: SessionAdminService
    wake_schedule_admin: WakeScheduleAdminService
    teams_admin: TeamsAdminService
    vm_inventory_admin: VmInventoryAdminService
    worker_control_admin: WorkerControlAdminService
    workflow_config_admin: WorkflowConfigAdminService
    skill_admin: SkillAdminService
    capacity_health_admin: CapacityHealthAdminService
    model_usage_admin: ModelUsageAdminService
    operator_steering_admin: OperatorSteeringAdminService
    workflow_generation_admin: WorkflowGenerationAdminService
    workflow_reports_admin: WorkflowReportsAdminService
    approval_audit_admin: ApprovalAuditAdminService
    artifact_admin: ArtifactAdminService
    issue_git_support: IssueGitAdminSupport
    vm_agent_support: VmAgentAdminSupport
    issue_git_admin: IssueGitAdminService
    vm_agent_admin: VmAgentAdminService
    pr_integration_preflight_admin: PrIntegrationPreflightAdminService
    operations_admin: OperationsAdminService
    trigger_repository: TriggerRepository
    trigger_event_repository: TriggerEventRepository
    agent_create_operations: JsonStore
    agent_create_operation_owner_id: str
    agent_create_operation_payloads: dict[str, dict[str, Any]]
    agent_create_operation_payloads_lock: threading.Lock
    agent_create_executor: ThreadPoolExecutor
    execution_backend_instance: Any | None

    @property
    def use_privileged_helper(self) -> bool:
        return self._privileged_helper_port.get_use_privileged_helper()

    @use_privileged_helper.setter
    def use_privileged_helper(self, enabled: bool) -> None:
        self._privileged_helper_port.set_use_privileged_helper(enabled)

    @property
    def privileged_helper(self) -> Any | None:
        return self._privileged_helper_port.get_privileged_helper()

    @privileged_helper.setter
    def privileged_helper(self, helper: Any | None) -> None:
        self._privileged_helper_port.set_privileged_helper(helper)


class _AdminRuntimeHost:
    def __init__(
        self,
        *,
        workflow_generation_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.workflow_generation_transport = workflow_generation_transport
        self.use_privileged_helper = False
        self._privileged_helper: Any | None = None
        self.components: AdminRuntimeComponents | None = None

    def _require_components(self) -> AdminRuntimeComponents:
        if self.components is None:
            raise RuntimeError("Admin runtime components are not initialized.")
        return self.components

    @property
    def project_dir(self) -> Path:
        return self._require_components().project_dir

    def admin_config_mutation_lock(self):
        return self._require_components().operations_admin.admin_config_mutation_lock()

    def list_agents(self, **kwargs: Any):
        return self._require_components().vm_agent_admin.list_agents(**kwargs)

    def compute_directory_effective_config(self, directory_path: str, domain: str):
        return self._require_components().operations_admin.compute_directory_effective_config(
            directory_path, domain
        )

    def workflow_run_store_paths(self):
        return self._require_components().workflow_config_admin.workflow_run_store_paths()

    def workflow_run_store_bundle(self, stores: dict[str, Any]):
        return self._require_components().workflow_config_admin.workflow_run_store_bundle(stores)


def _resolve_project_dir_for_admin_runtime(
    *,
    runtime_dir: Path,
    legacy_config_path: Path | None,
) -> Path:
    if legacy_config_path is not None:
        return resolve_project_dir(legacy_config_path=legacy_config_path)
    runtime_root = runtime_dir.expanduser()
    if not runtime_root.is_absolute():
        repo_relative_runtime = project_helpers.module_project_root() / runtime_root
        if repo_relative_runtime.exists() or (repo_relative_runtime.parent / "config").exists():
            runtime_root = repo_relative_runtime
        else:
            runtime_root = runtime_root.resolve()
    else:
        runtime_root = runtime_root.resolve()
    if runtime_root.name == "runtime":
        return runtime_root.parent
    return runtime_root.parent if runtime_root.parent != runtime_root else runtime_root


def build_admin_runtime(
    runtime_dir: str | Path = DEFAULT_RUNTIME_DIR,
    *,
    legacy_config_path: str | Path | None = None,
    config_path: str | Path | None = None,
    workflow_generation_transport: httpx.AsyncBaseTransport | None = None,
) -> AdminRuntimeComponents:
    host = _AdminRuntimeHost(workflow_generation_transport=workflow_generation_transport)
    components = _build_admin_runtime_components(
        host,
        runtime_dir=runtime_dir,
        legacy_config_path=legacy_config_path,
        config_path=config_path,
    )
    host.components = components
    return components


def _build_admin_runtime_components(
    host: _AdminRuntimeHost,
    runtime_dir: str | Path = DEFAULT_RUNTIME_DIR,
    *,
    legacy_config_path: str | Path | None = None,
    config_path: str | Path | None = None,
) -> AdminRuntimeComponents:
    candidate_runtime = Path(runtime_dir)
    explicit_legacy = (
        Path(legacy_config_path or config_path) if (legacy_config_path or config_path) else None
    )
    if explicit_legacy is None and candidate_runtime.suffix.casefold() in {".yaml", ".yml"}:
        explicit_legacy = candidate_runtime
        candidate_runtime = resolve_runtime_dir(legacy_config_path=explicit_legacy)
    runtime_dir_path = candidate_runtime
    project_dir = _resolve_project_dir_for_admin_runtime(
        runtime_dir=runtime_dir_path,
        legacy_config_path=explicit_legacy,
    )
    bootstrap_config = load_host_bootstrap_config(
        runtime_dir=runtime_dir_path,
        legacy_config_path=explicit_legacy,
    )
    directory_store = DirectoryStore(bootstrap_config.directories_store_path)
    directory_store.ensure_root()
    directory_config_store = DirectoryConfigStore(
        directory_store, runtime_dir=bootstrap_config.runtime_dir
    )
    directory_config_store.ensure_root()
    config = load_runtime_gateway_config(
        runtime_dir=bootstrap_config.runtime_dir,
        legacy_config_path=explicit_legacy,
    )
    runtime = build_runtime(config)
    tool_call_audit_index: ToolCallAuditIndex | None = ToolCallAuditIndex.for_audit_log(
        config.audit_log_path
    )
    try:
        tool_call_audit_index.ensure_schema()
        tool_call_audit_index.import_from_jsonl_tail(config.audit_log_path, max_lines=1_000)
    except Exception:
        tool_call_audit_index = None

    skill_library = SkillLibraryRepository(config.runtime_dir)
    agent_control_store = AgentControlStore(config.host_agent_control_state_path)
    local_issue_store = JsonStore(
        config.runtime_dir / "local-issues.json", LocalIssueRecord
    )
    local_issue_identity_resolver = LocalIssueIdentityResolver(
        local_issue_store=local_issue_store,
        read_issue_number_from_yaml=session_helpers.read_issue_number_from_yaml,
    )
    local_issue_counter_path = config.runtime_dir / "local-issue-counter.txt"
    local_issue_create_lock_path = config.runtime_dir / "local-issue-create.lock"
    admin_config_mutation_lock_path = config.runtime_dir / "admin-config-mutations.lock"
    local_issue_yaml_dir = config.runtime_dir / "issues"
    local_issue_yaml_dir.mkdir(parents=True, exist_ok=True)

    directory_admin = DirectoryAdminService.from_store(
        directory_store=runtime.directories,
        gateway_config=config,
    )
    normalize_directory_reference = (
        lambda reference: DirectoryService(runtime.directories).show(
            reference or "root"
        ).directory.path
    )
    directory_ownership = DirectoryOwnershipQueries(
        directories=directory_admin._directories,
        sessions=runtime.session_repository,
        pr_map_path=config.pr_map_path,
        ado_processed_store_path=config.runtime_dir / "ado-processed-work-items.json",
    )
    issue_service = IssueService(
        ProviderConfigResolver(
            config,
            directory_store=runtime.directories,
            session_store=runtime.session_repository,
        )
    )

    issue_git_support = IssueGitAdminSupport(
        project_dir=project_dir,
        runtime=runtime,
        config=config,
        local_issue_store=local_issue_store,
        local_issue_identity_resolver=local_issue_identity_resolver,
        local_issue_counter_path=local_issue_counter_path,
        local_issue_create_lock_path=local_issue_create_lock_path,
        local_issue_yaml_dir=local_issue_yaml_dir,
        issue_service=issue_service,
        directory_admin=directory_admin,
        directory_ownership=directory_ownership,
    )
    directory_ownership_port = AdminRuntimeDirectoryOwnershipPort(issue_git_support)
    privileged_helper_port = AdminRuntimePrivilegedHelperPort(host)

    session_admin = SessionAdminService(
        SessionAdminDependencies(
            runtime=runtime,
            config=config,
            directory_ownership=directory_ownership_port,
        )
    )
    wake_schedule_admin = WakeScheduleAdminService(
        WakeScheduleAdminDependencies(
            runtime=runtime,
            config=config,
        )
    )
    teams_admin = TeamsAdminService(
        TeamsAdminDependencies(
            runtime=runtime,
            normalize_directory_reference=normalize_directory_reference,
        )
    )
    workflow_config_admin = WorkflowConfigAdminService(
        WorkflowConfigAdminDependencies(
            project_dir=project_dir,
            config=config,
            skill_library=skill_library,
            directory_admin=directory_admin,
            admin_config_mutation_lock=lambda: host.admin_config_mutation_lock(),
            normalize_directory_reference=normalize_directory_reference,
        )
    )
    workflow_reports_admin = WorkflowReportsAdminService(
        WorkflowReportsAdminDependencies(
            project_dir=project_dir,
            config=config,
            runtime=runtime,
            trigger_event_repository=JsonTriggerEventRepository(),
            workflow_config_admin=workflow_config_admin,
            list_agents=host.list_agents,
            project_dir_provider=lambda: host.project_dir,
        )
    )
    workflow_config_admin.set_workflow_reports_admin(workflow_reports_admin)
    vm_inventory_admin = VmInventoryAdminService(
        VmInventoryAdminDependencies(
            runtime=runtime,
            agent_control_store=agent_control_store,
            directory_ownership=directory_ownership_port,
        )
    )
    worker_control_admin = WorkerControlAdminService()
    skill_admin = SkillAdminService(skill_library)
    approval_audit_admin = ApprovalAuditAdminService(
        ApprovalAuditAdminDependencies(
            runtime=runtime,
            config=config,
            tool_call_audit_index=tool_call_audit_index,
            directory_path=directory_ownership_port,
        )
    )
    artifact_admin = ArtifactAdminService(
        ArtifactAdminDependencies(runtime_dir=config.runtime_dir)
    )

    vm_agent_support = VmAgentAdminSupport(
        config=config,
        runtime=runtime,
        project_dir=project_dir,
        local_issue_store=local_issue_store,
        local_issue_identity_resolver=local_issue_identity_resolver,
        vm_inventory_admin=vm_inventory_admin,
        workflow_config_admin=workflow_config_admin,
        directory_admin=directory_admin,
        privileged_helper=privileged_helper_port,
        agent_control_store=agent_control_store,
        directory_ownership=directory_ownership_port,
    )

    issue_git_admin = IssueGitAdminService(
        IssueGitAdminDependencies(
            project_dir=issue_git_support.project_dir,
            runtime=issue_git_support.runtime,
            config=issue_git_support.config,
            local_issue_store=issue_git_support.local_issue_store,
            local_issue_yaml_dir=issue_git_support.local_issue_yaml_dir,
            issue_service=issue_git_support.issue_service,
            directory_path=issue_git_support,
            local_issue_creation_lock=issue_git_support.local_issue_creation_lock,
            next_local_issue_number=issue_git_support.next_local_issue_number,
            canonical_local_issue_id=issue_git_support.canonical_local_issue_id,
            resolve_local_issue_record=local_issue_identity_resolver.resolve_record,
            resolve_local_issue_key=local_issue_identity_resolver.resolve_key,
            proxy_target_for_issue=issue_git_support.proxy_target_for_issue,
            refresh_proxy_cache_from_remote=issue_git_support.refresh_proxy_cache_from_remote,
            write_local_issue_yaml=issue_git_support.write_local_issue_yaml,
            directory_ownership=issue_git_support,
            resolve_issue_setup=vm_agent_support.resolve_issue_setup,
            resolve_repo_source=vm_agent_support.resolve_repo_source,
            sync_repo_into_bare_remote=vm_agent_support.sync_repo_into_bare_remote,
        )
    )
    vm_agent_admin = VmAgentAdminService(
        VmAgentAdminDependencies(
            config=vm_agent_support.config,
            runtime=vm_agent_support.runtime,
            project_dir=vm_agent_support.project_dir,
            local_issue_store=vm_agent_support.local_issue_store,
            vm_inventory_admin=vm_agent_support.vm_inventory_admin,
            agent_control_store=vm_agent_support.agent_control_store,
            directory_ownership=vm_agent_support.directory_ownership,
            privileged_helper=privileged_helper_port,
            directory_path=vm_agent_support,
            workflow_config=workflow_config_admin,
            resolve_agent_model_for_session=vm_agent_support.resolve_agent_model_for_session,
            resolve_vm_defaults=vm_agent_support.resolve_vm_defaults,
            resolve_issue_setup=vm_agent_support.resolve_issue_setup,
            resolve_local_issue_record=local_issue_identity_resolver.resolve_record,
            resolve_local_issue_key=local_issue_identity_resolver.resolve_key,
            resolve_repo_source=vm_agent_support.resolve_repo_source,
            sync_repo_into_bare_remote=vm_agent_support.sync_repo_into_bare_remote,
            clone_repo_into_vm=vm_agent_support.clone_repo_into_vm,
        )
    )
    pr_integration_preflight_admin = PrIntegrationPreflightAdminService(
        PrIntegrationPreflightAdminDependencies(
            runtime=runtime,
            compute_directory_effective_config=host.compute_directory_effective_config,
            resolve_repo_source=vm_agent_support.resolve_repo_source,
        )
    )
    trigger_repository = WorkerConfigTriggerRepository(
        read_config=workflow_config_admin.read_trigger_document,
        write_config=workflow_config_admin.write_trigger_document,
    )
    operations_admin = OperationsAdminService(
        config=config,
        project_dir=project_dir,
        privileged_helper=privileged_helper_port,
        teams_admin=teams_admin,
        session_admin=session_admin,
        wake_schedule_admin=wake_schedule_admin,
        worker_control_admin=worker_control_admin,
        directory_admin=directory_admin,
        workflow_config_admin=workflow_config_admin,
        trigger_repository=trigger_repository,
        privileged_helper_factory=AdminRuntimePrivilegedHelperFactoryPort(
            lambda: PrivilegedHelperSession
        ),
        admin_config_mutation_lock=lambda: admin_config_mutation_file_lock(
            admin_config_mutation_lock_path
        ),
    )
    workflow_generation_admin = WorkflowGenerationAdminService(
        WorkflowGenerationAdminDependencies(
            config=config,
            runtime=runtime,
            workflow_config_admin=workflow_config_admin,
            normalize_directory_reference=normalize_directory_reference,
            read_assistant_settings=operations_admin.read_assistant_settings,
            workflow_generation_transport_provider=lambda: host.workflow_generation_transport,
        )
    )
    operator_steering_admin = OperatorSteeringAdminService(
        OperatorSteeringAdminDependencies(
            runtime_dir=config.runtime_dir,
            normalize_agent_record_directory_path=vm_agent_admin.normalize_agent_record_directory_path,
            workflow_run_store_paths=workflow_config_admin.workflow_run_store_paths,
            workflow_uses_sqlite_store=workflow_config_admin.workflow_uses_sqlite_store,
            container_execution_records_for_session=(
                vm_agent_admin.container_execution_records_for_session
            ),
            container_worker_backend=lambda **kwargs: vm_agent_admin.container_worker_backend(
                **kwargs
            ),
            container_worker_url_for_target=vm_agent_admin.container_worker_url_for_target,
        )
    )
    model_usage_admin = ModelUsageAdminService(
        ModelUsageAdminDependencies(
            runtime_dir=config.runtime_dir,
            session_repository=runtime.session_repository,
            normalize_agent_record_directory_path=vm_agent_admin.normalize_agent_record_directory_path,
            is_container_session=vm_agent_admin.is_container_session,
            interrupt_container_operator_steering_note=(
                operator_steering_admin.interrupt_container_operator_steering_note
            ),
            admin_config_mutation_lock=lambda: host.admin_config_mutation_lock(),
            stable_json_revision=workflow_config_admin.stable_json_revision,
            write_json_file_atomic=workflow_config_admin.write_json_file_atomic,
            revision_conflict_factory=lambda message, details: AdminRevisionConflict(
                message, details=details
            ),
        )
    )
    capacity_health_admin = CapacityHealthAdminService(
        CapacityHealthAdminDependencies(
            config=config,
            runtime=runtime,
            local_issue_yaml_dir=local_issue_yaml_dir,
            list_agents=lambda **kwargs: host.list_agents(**kwargs),
            normalize_agent_record_directory_path=vm_agent_admin.normalize_agent_record_directory_path,
            is_container_session=vm_agent_admin.is_container_session,
            container_worker_execution_repository=(
                vm_agent_admin.container_worker_execution_repository
            ),
            container_worker_url_from_metadata=vm_agent_admin.container_worker_url_from_metadata,
            communication_artifact_store=lambda: CommunicationArtifactStore(config.runtime_dir),
            published_artifact_store=artifact_admin.published_artifact_store,
            workflow_config_admin=workflow_config_admin,
            workflow_run_store_paths=lambda: host.workflow_run_store_paths(),
            workflow_run_store_bundle=lambda stores: host.workflow_run_store_bundle(stores),
        )
    )

    agent_create_operations = JsonStore(
        config.runtime_dir / "agent-create-operations.json",
        AgentCreateOperation,
    )
    agent_create_operation_owner_id = f"{os.getpid()}:{uuid4().hex}"
    agent_create_operation_payloads: dict[str, dict[str, Any]] = {}
    agent_create_operation_payloads_lock = threading.Lock()
    agent_create_executor = ThreadPoolExecutor(
        max_workers=AGENT_CREATE_OPERATION_MAX_WORKERS,
        thread_name_prefix="agent-create",
    )
    vm_agent_admin.configure_admin_collaborators(
        operations_admin=operations_admin,
        skill_admin=skill_admin,
        workflow_config_admin=workflow_config_admin,
        directory_admin=directory_admin,
        skill_library=skill_library,
        agent_create_operations=agent_create_operations,
        agent_create_operation_owner_id=agent_create_operation_owner_id,
        agent_create_operation_payloads=agent_create_operation_payloads,
        agent_create_operation_payloads_lock=agent_create_operation_payloads_lock,
        agent_create_executor=agent_create_executor,
        project_dir_provider=lambda: project_dir,
    )

    return AdminRuntimeComponents(
        runtime_dir=runtime_dir_path,
        legacy_config_path=explicit_legacy,
        project_dir=project_dir,
        config=config,
        runtime=runtime,
        directory_store=directory_store,
        directory_config_store=directory_config_store,
        tool_call_audit_index=tool_call_audit_index,
        skill_library=skill_library,
        agent_control_store=agent_control_store,
        local_issue_store=local_issue_store,
        local_issue_identity_resolver=local_issue_identity_resolver,
        local_issue_counter_path=local_issue_counter_path,
        local_issue_create_lock_path=local_issue_create_lock_path,
        admin_config_mutation_lock_path=admin_config_mutation_lock_path,
        local_issue_yaml_dir=local_issue_yaml_dir,
        workflow_generation_transport=host.workflow_generation_transport,
        _privileged_helper_port=privileged_helper_port,
        directory_admin=directory_admin,
        directory_ownership=directory_ownership,
        issue_service=issue_service,
        session_admin=session_admin,
        wake_schedule_admin=wake_schedule_admin,
        teams_admin=teams_admin,
        vm_inventory_admin=vm_inventory_admin,
        worker_control_admin=worker_control_admin,
        workflow_config_admin=workflow_config_admin,
        skill_admin=skill_admin,
        capacity_health_admin=capacity_health_admin,
        model_usage_admin=model_usage_admin,
        operator_steering_admin=operator_steering_admin,
        workflow_generation_admin=workflow_generation_admin,
        workflow_reports_admin=workflow_reports_admin,
        approval_audit_admin=approval_audit_admin,
        artifact_admin=artifact_admin,
        issue_git_support=issue_git_support,
        vm_agent_support=vm_agent_support,
        issue_git_admin=issue_git_admin,
        vm_agent_admin=vm_agent_admin,
        pr_integration_preflight_admin=pr_integration_preflight_admin,
        operations_admin=operations_admin,
        trigger_repository=trigger_repository,
        trigger_event_repository=JsonTriggerEventRepository(),
        agent_create_operations=agent_create_operations,
        agent_create_operation_owner_id=agent_create_operation_owner_id,
        agent_create_operation_payloads=agent_create_operation_payloads,
        agent_create_operation_payloads_lock=agent_create_operation_payloads_lock,
        agent_create_executor=agent_create_executor,
        execution_backend_instance=None,
    )

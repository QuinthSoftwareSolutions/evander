from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from agent_sandbox.agent_control import AgentControlStore
from agent_sandbox.admin.directory import DirectoryAdminService
from agent_sandbox.admin.facades import (
    SessionAdminDependencies,
    SessionAdminFacade,
    TeamsAdminDependencies,
    TeamsAdminFacade,
    WorkerAdminDependencies,
    WorkerAdminFacade,
)
from agent_sandbox.admin.services import (
    CapacityHealthAdminService,
    IssueGitAdminDependencies,
    IssueGitAdminService,
    IssueGitAdminSupport,
    ModelUsageAdminService,
    OperationsAdminService,
    PrIntegrationPreflightAdminDependencies,
    PrIntegrationPreflightAdminService,
    SkillAdminService,
    VmAgentAdminDependencies,
    VmAgentAdminService,
    VmAgentAdminSupport,
    WorkflowConfigAdminService,
    WorkflowGenerationAdminService,
    WorkflowReportsAdminService,
)
from agent_sandbox.bootstrap import (
    load_host_bootstrap_config,
    load_runtime_gateway_config,
    resolve_runtime_dir,
)
from agent_sandbox.directory_config import DirectoryConfigStore
from agent_sandbox.directories import DirectoryStore
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


@dataclass(slots=True)
class AdminServiceComponents:
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
    workflow_generation_lock: threading.Lock
    workflow_generation_in_flight: bool
    workflow_generation_last_finished_at_monotonic: float | None
    workflow_generation_transport: httpx.AsyncBaseTransport | None
    use_privileged_helper: bool
    privileged_helper: Any | None
    directory_admin: DirectoryAdminService
    directory_ownership: DirectoryOwnershipQueries
    issue_service: IssueService
    session_admin: SessionAdminFacade
    teams_admin: TeamsAdminFacade
    worker_admin: WorkerAdminFacade
    workflow_config_admin: WorkflowConfigAdminService
    skill_admin: SkillAdminService
    capacity_health_admin: CapacityHealthAdminService
    model_usage_admin: ModelUsageAdminService
    workflow_generation_admin: WorkflowGenerationAdminService
    workflow_reports_admin: WorkflowReportsAdminService
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


def build_admin_service_components(
    host: Any,
    runtime_dir: str | Path = DEFAULT_RUNTIME_DIR,
    *,
    legacy_config_path: str | Path | None = None,
    config_path: str | Path | None = None,
) -> AdminServiceComponents:
    service = import_module("agent_sandbox.admin.service")

    candidate_runtime = Path(runtime_dir)
    explicit_legacy = (
        Path(legacy_config_path or config_path)
        if (legacy_config_path or config_path)
        else None
    )
    if explicit_legacy is None and candidate_runtime.suffix.casefold() in {".yaml", ".yml"}:
        explicit_legacy = candidate_runtime
        candidate_runtime = resolve_runtime_dir(legacy_config_path=explicit_legacy)
    runtime_dir_path = candidate_runtime
    project_dir = service._resolve_project_dir_for_admin_service(
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
        config.runtime_dir / "local-issues.json", service.LocalIssueRecord
    )
    local_issue_identity_resolver = service.LocalIssueIdentityResolver(
        local_issue_store=local_issue_store,
        read_issue_number_from_yaml=service._read_issue_number_from_yaml,
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

    session_admin = SessionAdminFacade(
        SessionAdminDependencies(
            runtime=runtime,
            config=config,
            normalize_directory_path=lambda directory_path: (
                host.issue_git_support.normalize_directory_path(directory_path)
            ),
            direct_owned_entity_ids=lambda directory_path, **kwargs: (
                host.issue_git_support.direct_owned_entity_ids(directory_path, **kwargs)
            ),
        )
    )
    teams_admin = TeamsAdminFacade(
        TeamsAdminDependencies(
            runtime=runtime,
            normalize_directory_reference=host._normalize_directory_reference,
        )
    )
    worker_admin = WorkerAdminFacade(
        WorkerAdminDependencies(
            runtime=runtime,
            config=config,
            agent_control_store=agent_control_store,
            normalize_directory_path=lambda directory_path: (
                host.issue_git_support.normalize_directory_path(directory_path)
            ),
            direct_owned_entity_ids=lambda directory_path, **kwargs: (
                host.issue_git_support.direct_owned_entity_ids(directory_path, **kwargs)
            ),
            read_worker_global_config=host._read_worker_global_config,
            write_worker_global_config=host._write_worker_global_config,
            read_workflow_editor_document=host._read_workflow_editor_document,
            read_composed_workflow_editor_document=host._read_composed_workflow_editor_document,
            read_workflow_editor_catalog=host._read_workflow_editor_catalog,
            validate_workflow_editor_document=host._validate_workflow_editor_document,
            save_workflow_editor_document=host._save_workflow_editor_document,
            read_workflow_run_snapshot=host._read_workflow_run_snapshot,
            read_workflow_run_detail=host._read_workflow_run_detail,
            read_workflow_run_search=host._read_workflow_run_search,
            clear_workflow_run=host._clear_workflow_run,
            dismiss_workflow_node_failures=host._dismiss_workflow_node_failures,
            resume_workflow_run=host._resume_workflow_run,
            workflow_event_journal=host._workflow_event_journal,
            upsert_trigger_config=host._upsert_trigger_config,
            delete_trigger_config=host._delete_trigger_config,
            upsert_effect_config=host._upsert_effect_config,
            delete_effect_config=host._delete_effect_config,
        )
    )
    workflow_config_admin = WorkflowConfigAdminService(worker_admin)
    skill_admin = SkillAdminService(skill_library)
    capacity_health_admin = CapacityHealthAdminService(host)
    model_usage_admin = ModelUsageAdminService(host)
    workflow_generation_admin = WorkflowGenerationAdminService(host)
    workflow_reports_admin = WorkflowReportsAdminService(host)

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
    vm_agent_support = VmAgentAdminSupport(
        config=config,
        runtime=runtime,
        project_dir=project_dir,
        local_issue_store=local_issue_store,
        local_issue_identity_resolver=local_issue_identity_resolver,
        worker_admin=worker_admin,
        workflow_config_admin=workflow_config_admin,
        directory_admin=directory_admin,
        get_privileged_helper=lambda: host._privileged_helper,
        get_use_privileged_helper=lambda: host.use_privileged_helper,
    )

    issue_git_admin = IssueGitAdminService(
        IssueGitAdminDependencies(
            project_dir=issue_git_support.project_dir,
            runtime=issue_git_support.runtime,
            config=issue_git_support.config,
            local_issue_store=issue_git_support.local_issue_store,
            local_issue_yaml_dir=issue_git_support.local_issue_yaml_dir,
            issue_service=issue_git_support.issue_service,
            normalize_directory_path=issue_git_support.normalize_directory_path,
            local_issue_creation_lock=issue_git_support.local_issue_creation_lock,
            next_local_issue_number=issue_git_support.next_local_issue_number,
            canonical_local_issue_id=issue_git_support.canonical_local_issue_id,
            resolve_local_issue_record=local_issue_identity_resolver.resolve_record,
            resolve_local_issue_key=local_issue_identity_resolver.resolve_key,
            proxy_target_for_issue=issue_git_support.proxy_target_for_issue,
            refresh_proxy_cache_from_remote=issue_git_support.refresh_proxy_cache_from_remote,
            write_local_issue_yaml=issue_git_support.write_local_issue_yaml,
            direct_owned_entity_ids=issue_git_support.direct_owned_entity_ids,
        )
    )
    vm_agent_admin = VmAgentAdminService(
        VmAgentAdminDependencies(
            config=vm_agent_support.config,
            runtime=vm_agent_support.runtime,
            project_dir=vm_agent_support.project_dir,
            local_issue_store=vm_agent_support.local_issue_store,
            worker_admin=vm_agent_support.worker_admin,
            get_privileged_helper=vm_agent_support.get_privileged_helper,
            get_use_privileged_helper=vm_agent_support.get_use_privileged_helper,
            normalize_directory_path=vm_agent_support.normalize_directory_path,
            read_worker_global_config=vm_agent_support.read_worker_global_config,
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
    operations_admin = OperationsAdminService(
        project_dir=project_dir,
        get_privileged_helper=lambda: host._privileged_helper,
        get_use_privileged_helper=lambda: host.use_privileged_helper,
        set_privileged_helper=lambda helper: setattr(host, "_privileged_helper", helper),
        teams_admin=teams_admin,
        session_admin=session_admin,
        worker_admin=worker_admin,
        directory_admin=directory_admin,
        privileged_helper_factory=lambda **kwargs: service.PrivilegedHelperSession(**kwargs),
    )

    return AdminServiceComponents(
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
        workflow_generation_lock=threading.Lock(),
        workflow_generation_in_flight=False,
        workflow_generation_last_finished_at_monotonic=None,
        workflow_generation_transport=None,
        use_privileged_helper=False,
        privileged_helper=None,
        directory_admin=directory_admin,
        directory_ownership=directory_ownership,
        issue_service=issue_service,
        session_admin=session_admin,
        teams_admin=teams_admin,
        worker_admin=worker_admin,
        workflow_config_admin=workflow_config_admin,
        skill_admin=skill_admin,
        capacity_health_admin=capacity_health_admin,
        model_usage_admin=model_usage_admin,
        workflow_generation_admin=workflow_generation_admin,
        workflow_reports_admin=workflow_reports_admin,
        issue_git_support=issue_git_support,
        vm_agent_support=vm_agent_support,
        issue_git_admin=issue_git_admin,
        vm_agent_admin=vm_agent_admin,
        pr_integration_preflight_admin=pr_integration_preflight_admin,
        operations_admin=operations_admin,
        trigger_repository=WorkerConfigTriggerRepository(
            read_config=host._read_trigger_document,
            write_config=host._write_trigger_document,
        ),
        trigger_event_repository=JsonTriggerEventRepository(),
        agent_create_operations=JsonStore(
            config.runtime_dir / "agent-create-operations.json",
            service.AgentCreateOperation,
        ),
        agent_create_operation_owner_id=f"{os.getpid()}:{uuid4().hex}",
        agent_create_operation_payloads={},
        agent_create_operation_payloads_lock=threading.Lock(),
        agent_create_executor=ThreadPoolExecutor(
            max_workers=service._AGENT_CREATE_OPERATION_MAX_WORKERS,
            thread_name_prefix="agent-create",
        ),
        execution_backend_instance=None,
    )

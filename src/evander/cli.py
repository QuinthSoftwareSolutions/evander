"""Evander command entry point and compatibility exports.

Command implementations live in domain modules and register with cli_app.
"""

# Every import below is an intentional compatibility export.
# ruff: noqa: F401

import asyncio
import importlib
import inspect
import json
import logging
import os
import re
import subprocess
import time
import uuid
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timedelta, timezone
from itertools import count
from pathlib import Path
from typing import Annotated

import httpx
import typer
from pydantic import ValidationError
from typer.core import TyperGroup

from evander.admin.composition import AdminRuntimeComponents
from evander.admin.services.issue_setup_helpers import (
    resolve_agent_model_for_manifest,
    resolve_effective_issue_setup_config,
)
from evander.audit import AuditEventType, AuditLogger
from evander.bootstrap import load_host_bootstrap_config, load_runtime_gateway_config
from evander.code_index.cli import app as code_index_app
from evander.communication_artifacts import CommunicationArtifactStore
from evander.config import GatewayConfig
from evander.connectors.models import normalize_connector_id
from evander.connectors.service import ConnectorService, connector_registry_from_addons
from evander.daemons.ado_issue_worker import run_worker_command
from evander.daemons.effect_orchestrator import build_effect_orchestrator_worker
from evander.daemons.proxy_issue_sync import (
    DEFAULT_PROXY_ISSUE_SYNC_FRESHNESS_SECONDS,
    DEFAULT_PROXY_ISSUE_SYNC_INTERVAL_SECONDS,
    run_proxy_issue_sync_worker,
)
from evander.daemons.proxy_issue_sync import run_once_from_runtime as run_proxy_issue_sync_once
from evander.daemons.trigger_orchestrator import build_trigger_orchestrator_worker
from evander.directories import DirectoryService
from evander.email.providers import JsonEmailProvider, MultiMailboxEmailProvider
from evander.execution import (
    ContainerWorkerExecutionRepository,
    ContainerWorkerVmExecutionBackend,
    ExecutionBackendKind,
    ExecutionSpec,
    ExecutionStatusKind,
    build_execution_backend,
)
from evander.messaging.discord_wake import build_discord_wake_worker
from evander.messaging.scheduled_wake import build_scheduled_wake_worker
from evander.messaging.teams_auth import TeamsAuthError
from evander.migrations import (
    migrate_directory_ownership_defaults,
    migrate_issue_setup_env_to_root,
    migrate_pr_template_to_root,
)
from evander.model_usage import DEFAULT_MODEL_PRICING_FILENAME, seed_default_model_pricing_catalog
from evander.models import (
    ApprovalStatus,
    AuditEvent,
    DiscordCommandEnvelope,
    EmailMessage,
    SessionManifest,
    ToolName,
)
from evander.policy_config import EffectivePolicyConfig
from evander.pr_feedback.store import PrFeedbackStore
from evander.pr_tracking import PrMapStore
from evander.provider_config import (
    MODEL_PROVIDER_MODE_METADATA_KEY,
    normalize_model_provider_mode_override,
)
from evander.provider_runtime.adapters import AdminRuntimeProviderAdapter
from evander.repositories.audit_index import ToolCallAuditIndex, audit_index_path_for_log_path
from evander.repositories.session_manifests import JsonSessionManifestRepository
from evander.runtime_generation import read_runtime_generation_status
from evander.setup import setup_doctor_payload, setup_plan_payload
from evander.setup.tui import run_setup_tui
from evander.teams_meeting import TeamsMeetingService
from evander.vm.defaults_config import EffectiveVmDefaultsConfig, VmDefaultsConfigResolver
from evander.vm_to_container_migration import (
    build_session_migration_plan,
    copy_vm_session_state,
    create_migration_backup,
    cutover_sessions_to_container,
    default_local_worker_root,
    default_migration_backup_root,
    plan_payload,
    restore_selected_manifests_from_backup,
    select_sessions_for_migration,
    verify_worker_session_state,
)

from .cli_app import (
    _COMMAND_PANELS,
    GroupedHelpTyperGroup,
    app,
    connectors_app,
    directories_app,
    execution_app,
    git_repos_app,
    setup_app,
    team_app,
    teams_auth_app,
    teams_meeting_app,
    wake_schedules_app,
)
from .cli_auth import (
    _TEAMS_AUTH_SUMMARY_FIELDS,
    _teams_auth_call,
    _teams_auth_emit_result,
    _teams_auth_extract_fields,
    _teams_auth_is_sensitive_key,
    _teams_auth_normalize_value,
    _teams_auth_print_payload,
    _teams_auth_redact,
    _teams_auth_result_payload,
    _teams_auth_scalar_text,
    _teams_auth_service,
    teams_auth_channels,
    teams_auth_list,
    teams_auth_login,
    teams_auth_logout,
    teams_auth_status,
)
from .cli_common import (
    CLI_DEFAULT_RUNTIME_DIR,
    UTC,
    ConfigOption,
    DirectoryOption,
    SetupProjectDirOption,
    _echo_action_result_or_exit,
    _echo_admin_result_or_exit,
    _echo_model_json,
    _parse_utc_datetime,
    _resolve_root_vm_defaults,
    _run_subprocess_compat,
)
from .cli_connectors import (
    _connector_service_for_option,
    connectors_list,
    connectors_show,
    connectors_test,
    connectors_validate,
)
from .cli_directories import (
    directories_config_set,
    directories_config_show,
    directories_create,
    directories_delete,
    directories_list,
    directories_move,
    directories_rename,
    directories_show,
)
from .cli_execution import (
    BackendOption,
    _build_execution_backend_for_kind,
    _build_execution_backend_from_option,
    _container_mapping_payload,
    _container_worker_repository,
    _container_worker_target_exists,
    _parse_metadata_pairs,
    _poll_container_execution,
    _preflight_container_worker_config,
    _resource_limits,
    execution_destroy,
    execution_list_container_worker_executions,
    execution_logs,
    execution_pin_worker_assignments,
    execution_smoke_container_worker,
    execution_start_container_job,
    execution_status,
    execution_stop,
)
from .cli_git import (
    _build_managed_git_admin_for_cli,
    _echo_managed_git_json,
    _managed_git_call,
    _managed_git_jsonable,
    git_repos_clone_into_agent,
    git_repos_delete,
    git_repos_grant,
    git_repos_grants,
    git_repos_list,
    git_repos_maintenance,
    git_repos_refresh,
    git_repos_register,
    git_repos_revoke_grant,
    git_repos_set_host_identity,
    git_repos_show,
)
from .cli_health import (
    EndpointStatus,
    PathStatus,
    UnitStatus,
    _check_health_endpoint,
    _format_rows,
    _get_systemd_unit_state,
    _normalize_host_for_local_check,
    _path_status,
    status,
)
from .cli_issues import (
    add_issue_comment,
    create_issue,
    create_issue_vm,
    create_local_issue,
    destroy_issue_vm,
    get_issue,
    list_issues,
    list_local_issues,
    prune_orphaned_issue_state,
    set_issue_state,
)
from .cli_maintenance import (
    init_state,
    migrate_directory_ownership,
    migrate_internal_events_to_sqlite,
    migrate_issue_setup_env_root,
    migrate_pr_template_root,
    rebuild_audit_index,
)
from .cli_meetings import (
    teams_meeting_start,
    teams_meeting_stop,
)
from .cli_runtime import (
    _load_host_config_from_option,
    _load_runtime_config_from_option,
    _project_dir_for_config_option,
    _runtime_dir_for_config_option,
    _workspace_root_for_config_option,
    build_admin_runtime,
    build_runtime,
)
from .cli_seed import (
    _discord_seed_sequence,
    seed_discord_channel,
    seed_email,
)
from .cli_session_migration import (
    _echo_vm_to_container_plan,
    _echo_vm_to_container_report,
    migrate_vm_sessions_to_container,
)
from .cli_session_support import (
    _build_issue_session_manifest,
    _default_issue_session_id,
    _default_session_ttl,
    _extract_issue_number,
    _issue_numbers_from_pr_map,
    _issue_session_id_prefix,
    _list_existing_issue_vm_names,
    _load_optional_env_file,
    _matching_issue_session_ids,
    _matching_vm_session_ids,
    _parse_pr_feedback_streams,
    _parse_session_extension,
    _prune_approvals_for_sessions,
    _prune_pr_feedback_store,
    _prune_pr_map,
    _prune_pr_map_for_issue_numbers,
    _read_issue_number_from_yaml,
    _resolve_issue_number,
    _resolve_issue_number_or_raise,
    _resolve_session_target,
    _resolve_session_ttl,
    _resolve_vm_name,
    _runtime_session_repository,
    _runtime_sessions_by_id,
    _session_sort_key,
    _session_streams_for_session_ids,
)
from .cli_sessions import (
    add_tool_permission,
    approve,
    attach_pr_feedback,
    create_session,
    delete_expired_sessions,
    delete_session,
    list_approvals,
    list_sessions,
    prune_sessions,
    renew_session,
    revoke_session,
    revoke_tool_permission,
    set_session_model,
    set_session_model_provider_mode,
    show_approval,
)
from .cli_setup import (
    setup_callback,
    setup_doctor,
    setup_plan,
)
from .cli_team import (
    team_checkout_release_candidate,
    team_dispatch_tail,
    team_migrate_historical_lifecycle,
)
from .cli_vm import (
    _container_log_delta,
    _container_log_tail,
    _follow_agent_logs,
    _observe_container_agent_logs,
    _set_agent_control_state,
    bootstrap_frontend_in_vm,
    bootstrap_python_in_vm,
    clone_repo_into_vm,
    create_vm,
    destroy_vm,
    disable_agent,
    enable_agent,
    list_vms,
    logs,
    resume_agent,
    shutdown_vm,
    ssh,
    start_vm,
)
from .cli_wake import (
    wake_schedule_create,
    wake_schedule_delete,
    wake_schedule_edit,
    wake_schedule_list,
)
from .cli_workers import (
    _build_teams_ingest_worker,
    _build_teams_meeting_ingest_worker,
    discord_wake,
    force_complete_await_agent,
    poll_ado_backlog,
    proxy_issue_sync,
    scheduled_wake,
    teams_ingest,
    teams_meeting_ingest,
    trigger_orchestrator,
)

if __name__ == "__main__":
    app()

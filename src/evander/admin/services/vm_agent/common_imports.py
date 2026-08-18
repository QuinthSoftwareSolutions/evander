from __future__ import annotations
from collections.abc import Mapping
from importlib import resources
import hashlib
import json
import logging
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal
from uuid import uuid4
from pydantic import BaseModel, Field
from evander.admin.services.ports import (
    AgentCreatePort,
    AgentPromptDispatchPort,
    ContainerWorkerBackendPort,
    DirectoryPathPort,
    PrivilegedHelperPort,
    VmLifecyclePort,
    WorkflowConfigPort,
)
from evander.admin.services import (
    git_helpers,
    process_helpers,
    project_helpers,
    session_helpers,
    vm_helpers,
)
from evander.admin.services.issue_setup_helpers import (
    EffectiveIssueSetupConfig,
    resolve_agent_model_for_manifest,
    resolve_effective_issue_setup_config,
)
from evander.approval_notification_targets import (
    resolve_approval_notification_target_ids_for_directory,
)
from evander.admin.services.local_issue_identity import LocalIssueIdentityResolver
from evander.contact_allowlists import validate_allowed_contact_ids_for_directory
from evander.config import GatewayConfig
from evander.container_worker_images import resolve_container_worker_image_selector
from evander.directories import DirectoryPath, DirectoryStore
from evander.git_pr_notes import agent_pr_notes_ref_for_session
from evander.messaging.targets import load_communication_targets_config
from evander.effects.registry import EffectProviderFactory
from evander.events.provider_models import AgentEnqueuePromptEffectDefinition
from evander.execution import ExecutionBackendKind
from evander.execution.container_worker_backend import ContainerWorkerVmExecutionBackend
from evander.execution.libvirt_backend import LibvirtVmExecutionBackend
from evander.execution.container_worker_client import ContainerWorkerClientError
from evander.models import (
    AgentPromptQueueRecord,
    AgentPromptQueueSummary,
    AgentRecord,
    ContainerSessionLifecycle,
    ContainerSessionLifecycleAction,
    ContainerSessionLifecycleActions,
    ContainerSessionLifecycleState,
    SessionActionResult,
    SessionManifest,
    SessionSummary,
    ToolName,
    VmActionResult,
    VmSummary,
    tool_name_value,
)
from evander.provider_config import (
    MODEL_PROVIDER_MODE_METADATA_KEY,
    normalize_model_provider_mode_override,
)
from evander.repositories.chat_assistant_sessions import JsonChatAssistantSessionRepository
from evander.repositories.session_manifests import as_session_manifest_repository
from evander.repositories.skills_library import (
    SkillLibraryEntry,
    SkillLibraryRepository,
    normalize_skill_names,
)
from evander.session_expiry import ensure_session_not_expired
from evander.session_artifacts import (
    AGENT_ARTIFACTS_ENV,
    DEFAULT_AGENT_ARTIFACTS_DIR,
    normalize_artifact_relative_path,
)
from evander.runtime import RuntimeContext, allowed_tools_for_capability_profile
from evander.services import parse_teams_target
from evander.vm.defaults_config import EffectiveVmDefaultsConfig, VmDefaultsConfigResolver
from evander.vm.provider import VmProviderRegistry
from evander.vm.templates import resolve_libvirt_vm_template_selector
from evander.workflow.catalog import WorkflowEditorFieldOption
from evander.worker.security import sanitize_text

COMMUNICATION_FILE_SESSION_POLICY_FIELDS = session_helpers.COMMUNICATION_FILE_SESSION_POLICY_FIELDS
SESSION_BOOL_POLICY_FIELDS = session_helpers.SESSION_BOOL_POLICY_FIELDS
_build_issue_session_manifest = session_helpers.build_issue_session_manifest
_guest_agent_pr_note_helper_b64 = project_helpers.guest_agent_pr_note_helper_b64
_guest_push_agent_helper_b64 = project_helpers.guest_push_agent_helper_b64
_guest_sync_base_helper_b64 = project_helpers.guest_sync_base_helper_b64
_normalize_allowed_tools = session_helpers.normalize_allowed_tools
_normalize_plain_string_list = session_helpers.normalize_plain_string_list
_normalize_session_labels = session_helpers.normalize_session_labels
_normalize_session_policy_bool = session_helpers.normalize_session_policy_bool
_normalize_session_policy_non_negative_int_or_none = (
    session_helpers.normalize_session_policy_non_negative_int_or_none
)
_prune_approvals_for_sessions = session_helpers.prune_approvals_for_sessions
_prune_pr_feedback_store = git_helpers.prune_pr_feedback_store
_prune_pr_map = git_helpers.prune_pr_map
_resolve_private_key_path = vm_helpers.resolve_private_key_path
_resolve_session_target = session_helpers.resolve_session_target
_resolve_vm_ip = vm_helpers.resolve_vm_ip
_run_subprocess = process_helpers.run_subprocess
_session_policy_update_from_payload = session_helpers.session_policy_update_from_payload
_ssh_options = vm_helpers.ssh_options
_sync_added_discord_session_cursors = session_helpers.sync_added_discord_session_cursors
_utc_now = session_helpers.utc_now
_validate_allowed_contact_ids_for_directory = (
    session_helpers.validate_allowed_contact_ids_for_runtime_directory
)

__all__ = [
    "AGENT_ARTIFACTS_ENV",
    "AgentCreatePort",
    "AgentEnqueuePromptEffectDefinition",
    "AgentPromptDispatchPort",
    "AgentPromptQueueRecord",
    "AgentPromptQueueSummary",
    "AgentRecord",
    "Any",
    "BaseModel",
    "COMMUNICATION_FILE_SESSION_POLICY_FIELDS",
    "ContainerSessionLifecycle",
    "ContainerSessionLifecycleAction",
    "ContainerSessionLifecycleActions",
    "ContainerSessionLifecycleState",
    "ContainerWorkerBackendPort",
    "ContainerWorkerClientError",
    "ContainerWorkerVmExecutionBackend",
    "DEFAULT_AGENT_ARTIFACTS_DIR",
    "DirectoryPath",
    "DirectoryPathPort",
    "DirectoryStore",
    "EffectProviderFactory",
    "EffectiveIssueSetupConfig",
    "EffectiveVmDefaultsConfig",
    "ExecutionBackendKind",
    "Field",
    "GatewayConfig",
    "JsonChatAssistantSessionRepository",
    "LibvirtVmExecutionBackend",
    "Literal",
    "LocalIssueIdentityResolver",
    "MODEL_PROVIDER_MODE_METADATA_KEY",
    "Mapping",
    "Path",
    "PrivilegedHelperPort",
    "RuntimeContext",
    "SESSION_BOOL_POLICY_FIELDS",
    "SessionActionResult",
    "SessionManifest",
    "SessionSummary",
    "SimpleNamespace",
    "SkillLibraryEntry",
    "SkillLibraryRepository",
    "ToolName",
    "UTC",
    "VmActionResult",
    "VmDefaultsConfigResolver",
    "VmLifecyclePort",
    "VmProviderRegistry",
    "VmSummary",
    "WorkflowConfigPort",
    "WorkflowEditorFieldOption",
    "_build_issue_session_manifest",
    "_guest_agent_pr_note_helper_b64",
    "_guest_push_agent_helper_b64",
    "_guest_sync_base_helper_b64",
    "_normalize_allowed_tools",
    "_normalize_plain_string_list",
    "_normalize_session_labels",
    "_normalize_session_policy_bool",
    "_normalize_session_policy_non_negative_int_or_none",
    "_prune_approvals_for_sessions",
    "_prune_pr_feedback_store",
    "_prune_pr_map",
    "_resolve_private_key_path",
    "_resolve_session_target",
    "_resolve_vm_ip",
    "_run_subprocess",
    "_session_policy_update_from_payload",
    "_ssh_options",
    "_sync_added_discord_session_cursors",
    "_utc_now",
    "_validate_allowed_contact_ids_for_directory",
    "agent_pr_notes_ref_for_session",
    "allowed_tools_for_capability_profile",
    "annotations",
    "as_session_manifest_repository",
    "dataclass",
    "datetime",
    "ensure_session_not_expired",
    "git_helpers",
    "hashlib",
    "json",
    "load_communication_targets_config",
    "logging",
    "normalize_artifact_relative_path",
    "normalize_model_provider_mode_override",
    "normalize_skill_names",
    "os",
    "parse_teams_target",
    "process_helpers",
    "project_helpers",
    "re",
    "resolve_agent_model_for_manifest",
    "resolve_approval_notification_target_ids_for_directory",
    "resolve_container_worker_image_selector",
    "resolve_effective_issue_setup_config",
    "resolve_libvirt_vm_template_selector",
    "resources",
    "sanitize_text",
    "session_helpers",
    "sha256",
    "shlex",
    "subprocess",
    "sys",
    "timedelta",
    "tool_name_value",
    "uuid4",
    "validate_allowed_contact_ids_for_directory",
    "vm_helpers",
]

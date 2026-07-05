from __future__ import annotations

import json
import hashlib
from pathlib import Path
from types import UnionType
from typing import Any, get_args, get_origin

from pydantic import BaseModel
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import BaseRoute, Route

from evander.admin.composition import AdminRuntimeComponents
from evander.admin.payloads import AdminRevisionConflict
from evander.admin.services.session_helpers import COMMUNICATION_FILE_SESSION_POLICY_FIELDS
from evander.admin.web.cache import AdminWebJsonResponseCache
from evander.admin.config_tooltips import CONFIG_FIELD_TOOLTIPS
from evander.admin.web.rbac import ADMIN_OWNER_PERMISSION
from evander.admin.web.shared import AdminWebAuth, request_has_privileged_operations
from evander.config import (
    AdoWorkerGlobalConfig,
    AssistantSettingsConfig,
    DiscordWakeWorkerGlobalConfig,
    GatewayConfig,
    ProxyIssueSyncWorkerGlobalConfig,
    ScheduledWakeWorkerGlobalConfig,
    TeamsIngestWorkerGlobalConfig,
    TeamsMeetingIngestWorkerGlobalConfig,
    TriggerOrchestratorWorkerGlobalConfig,
)
from evander.directory_config import (
    AdoWorkerDirectoryConfig,
    AutomationDirectoryConfig,
    ContactDirectoryConfig,
    DIRECTORY_CONFIG_DOMAIN_FILENAMES,
    DIRECTORY_CONFIG_DOMAIN_FIELDS,
    IssueSetupDirectoryConfig,
    PrTemplateDirectoryConfig,
)
from evander.messaging.targets import COMMUNICATION_TARGETS_DOMAIN
from evander.vm.defaults_config import EffectiveVmDefaultsConfig

_DISCORD_CONFIG_FIELDS: tuple[tuple[str, str], ...] = (
    ("providers", "discord_backend"),
    ("providers", "discord_bot_token_env"),
    ("providers", "discord_api_base_url"),
    ("providers", "discord_poll_limit"),
    ("providers", "discord_timeout_seconds"),
    ("providers", "discord_retry_count"),
    ("providers", "discord_retry_base_backoff_seconds"),
    ("providers", "discord_retry_max_backoff_seconds"),
    ("providers", "discord_read_reaction_emoji"),
    ("policy", "discord_operator_channel_allowlist"),
    ("policy", "discord_operator_user_allowlist"),
    ("policy", "discord_poll_operator_dms"),
    ("wake", "discord_resume_prompt"),
)
_TEAMS_PROVIDER_CONFIG_FIELDS: tuple[tuple[str, str], ...] = tuple(
    ("providers", field_name)
    for field_name in sorted(
        name for name in DIRECTORY_CONFIG_DOMAIN_FIELDS["providers"] if name.startswith("teams_")
    )
)
_COMMUNICATION_FILE_POLICY_FIELDS: tuple[tuple[str, str], ...] = tuple(
    ("policy", field_name) for field_name in COMMUNICATION_FILE_SESSION_POLICY_FIELDS
)
_COMMUNICATION_ARTIFACT_PROVIDER_FIELDS: tuple[tuple[str, str], ...] = (
    ("providers", "communication_artifact_public_base_url"),
    ("providers", "communication_artifact_link_secret_env"),
    ("providers", "communication_artifact_link_ttl_seconds"),
)
_ASSISTANT_SETTINGS_DOMAIN = "assistant_settings"
_ASSISTANT_SETTINGS_FIELDS: tuple[tuple[str, str], ...] = (
    (_ASSISTANT_SETTINGS_DOMAIN, "enabled"),
    (_ASSISTANT_SETTINGS_DOMAIN, "show_launcher_when_disabled"),
    (_ASSISTANT_SETTINGS_DOMAIN, "read_only"),
    (_ASSISTANT_SETTINGS_DOMAIN, "default_model"),
    (_ASSISTANT_SETTINGS_DOMAIN, "workflow_generation_model"),
    (_ASSISTANT_SETTINGS_DOMAIN, "default_profile"),
    (_ASSISTANT_SETTINGS_DOMAIN, "custom_system_instructions"),
)
_COMMUNICATION_TARGET_FIELDS: tuple[tuple[str, str], ...] = (
    (COMMUNICATION_TARGETS_DOMAIN, "targets"),
)
_EMAIL_PROVIDER_FIELDS: tuple[tuple[str, str], ...] = (
    ("email", "email_mailboxes"),
    ("email", "email_provider_timeout_seconds"),
)
_EMAIL_POLICY_FIELDS: tuple[tuple[str, str], ...] = (
    ("policy", "email_draft_domain_allowlist"),
    ("policy", "email_draft_recipient_allowlist"),
    ("policy", "email_folder_allowlist_by_mailbox"),
    ("policy", "email_mailbox_allowlist"),
    ("policy", "email_send_enabled"),
)
_APPROVAL_TIMING_POLICY_FIELDS: tuple[tuple[str, str], ...] = (
    ("policy", "approval_ttl_seconds"),
    ("policy", "default_session_ttl_seconds"),
)
_APPROVAL_NOTIFICATION_POLICY_FIELDS: tuple[tuple[str, str], ...] = (
    ("policy", "approval_notification_target_ids"),
)
_APPROVAL_REQUIRED_POLICY_FIELDS: tuple[tuple[str, str], ...] = (
    ("policy", "approval_required_tools"),
)
_MCP_TOOL_POLICY_FIELDS: tuple[tuple[str, str], ...] = (
    ("policy", "mcp_tool_policies"),
)
_TOOL_ARGUMENT_POLICY_FIELDS: tuple[tuple[str, str], ...] = (
    ("policy", "tool_argument_policies"),
)
_AUDIT_POLICY_FIELDS: tuple[tuple[str, str], ...] = (
    ("policy", "redacted_keys"),
)
_PR_REPLY_POLICY_FIELDS: tuple[tuple[str, str], ...] = (
    ("policy", "pr_reply_require_approval"),
)
_AGENT_PROMPT_PROVIDER_FIELDS: tuple[tuple[str, str], ...] = (
    ("providers", "agent_prompt_token_secret_env"),
    ("providers", "agent_prompt_token_ttl_seconds"),
    ("providers", "agent_prompt_queue_max_retries"),
    ("providers", "agent_context_retry_enabled"),
    ("providers", "agent_context_retry_max_attempts"),
    ("providers", "agent_context_retry_target_chars"),
)
_AZURE_DEVOPS_PROVIDER_FIELDS: tuple[tuple[str, str], ...] = (
    ("providers", "azure_devops_organization_url"),
    ("providers", "azure_devops_pat_env"),
    ("providers", "azure_devops_pr_pat_env"),
    ("providers", "azure_devops_project"),
)
_AGENT_EXECUTION_PROVIDER_FIELDS: tuple[tuple[str, str], ...] = (
    ("providers", "execution_backend"),
)
_CONTAINER_WORKER_PROVIDER_FIELDS: tuple[tuple[str, str], ...] = (
    ("providers", "container_worker_base_url"),
    ("providers", "container_worker_token_env"),
    ("providers", "container_worker_default_image"),
    ("providers", "container_worker_images"),
    ("providers", "container_worker_job_timeout_seconds"),
    ("providers", "container_worker_request_timeout_seconds"),
    ("providers", "container_worker_workspace_persistence"),
    ("providers", "container_worker_guest_openai_base_url"),
    ("providers", "container_worker_guest_anthropic_base_url"),
    ("providers", "container_worker_guest_gateway_url"),
    ("providers", "container_worker_guest_http_target_relay_base_url"),
    ("providers", "container_worker_guest_nuget_relay_base_url"),
    ("providers", "container_worker_guest_pypi_index_url"),
    ("providers", "container_worker_guest_npm_registry_url"),
    ("providers", "container_worker_mssql_enabled"),
    ("providers", "container_worker_mssql_host"),
    ("providers", "container_worker_mssql_port"),
)
_GITHUB_PROVIDER_FIELDS: tuple[tuple[str, str], ...] = (
    ("providers", "github_token_env"),
)
_HTTP_TARGET_RELAY_PROVIDER_FIELDS: tuple[tuple[str, str], ...] = (
    ("providers", "http_target_relay_timeout_seconds"),
)
_NUGET_PROVIDER_FIELDS: tuple[tuple[str, str], ...] = (
    ("providers", "nuget_azure_feed_base_url"),
    ("providers", "nuget_azure_pat_env"),
    ("providers", "nuget_public_feed_base_url"),
    ("providers", "nuget_relay_timeout_seconds"),
)
_NPM_AUDIT_FACADE_PROVIDER_FIELDS: tuple[tuple[str, str], ...] = (
    ("providers", "npm_audit_facade_audit_registry_url"),
    ("providers", "npm_audit_facade_package_registry_url"),
    ("providers", "npm_audit_facade_timeout_seconds"),
)
_LLM_PROVIDER_FIELDS: tuple[tuple[str, str], ...] = (
    ("providers", "model_provider_mode"),
    ("providers", "openai_api_base_url"),
    ("providers", "openai_api_key_env"),
    ("providers", "openai_relay_timeout_seconds"),
    ("providers", "local_openai_api_base_url"),
    ("providers", "local_openai_api_key"),
    ("providers", "local_openai_provider_name"),
    ("providers", "azure_openai_endpoint"),
    ("providers", "azure_openai_auth_mode"),
    ("providers", "azure_openai_api_key_env"),
    ("providers", "azure_openai_api_version"),
    ("providers", "azure_openai_model_deployments"),
    ("providers", "anthropic_api_base_url"),
    ("providers", "anthropic_api_key_env"),
    ("providers", "anthropic_api_version"),
    ("providers", "anthropic_relay_timeout_seconds"),
    ("providers", "openai_realtime_model"),
    ("providers", "openai_realtime_api_key_env"),
    ("providers", "openai_realtime_api_base_url"),
    ("providers", "voice_media_controller_url"),
    ("providers", "voice_media_controller_token_env"),
    ("providers", "voice_media_controller_request_timeout_seconds"),
)
_SEMANTIC_PROVIDER_FIELDS: tuple[tuple[str, str], ...] = (
    ("providers", "semantic_store_provider"),
    ("providers", "qdrant_base_url"),
    ("providers", "qdrant_api_key_env"),
    ("providers", "semantic_embedding_provider"),
    ("providers", "semantic_embedding_model"),
    ("providers", "semantic_embedding_dimensions"),
    ("providers", "semantic_vector_distance"),
)
_PHONE_CALL_PROVIDER_FIELDS: tuple[tuple[str, str], ...] = (
    ("providers", "phone_call_backend"),
    ("providers", "retell_api_base_url"),
    ("providers", "retell_api_key_env"),
    ("providers", "retell_from_number"),
    ("providers", "retell_default_agent_id"),
    ("providers", "retell_request_timeout_seconds"),
    ("providers", "didww_sip_bridge_url"),
    ("providers", "didww_sip_bridge_token_env"),
    ("providers", "didww_from_number"),
    ("providers", "inbound_phone_routes"),
)
_PROVIDER_STATE_FIELDS: tuple[tuple[str, str], ...] = (
    ("providers", "provider_state"),
)
_VM_PROVIDER_FIELDS: tuple[tuple[str, str], ...] = (
    ("vm", "provider"),
    ("vm", "provider_defaults"),
    ("vm", "azure"),
)
_VM_LIBVIRT_TEMPLATE_FIELDS: tuple[tuple[str, str], ...] = (
    ("vm", "libvirt_vm_default_template"),
    ("vm", "libvirt_vm_templates"),
)
_VM_NETWORK_FIELDS: tuple[tuple[str, str], ...] = (
    ("vm", "bind_network_cidr"),
)
_VM_GATEWAY_FIELDS: tuple[tuple[str, str], ...] = (
    ("vm", "gateway_url"),
)
_VM_HTTP_TARGET_RELAY_FIELDS: tuple[tuple[str, str], ...] = (
    ("vm", "http_target_relay_host"),
    ("vm", "http_target_relay_port"),
)
_VM_NUGET_RELAY_FIELDS: tuple[tuple[str, str], ...] = (
    ("vm", "nuget_relay_host"),
    ("vm", "nuget_relay_port"),
)
_VM_NPM_AUDIT_FACADE_FIELDS: tuple[tuple[str, str], ...] = (
    ("vm", "npm_audit_facade_host"),
    ("vm", "npm_audit_facade_port"),
)
_VM_OPENAI_RELAY_FIELDS: tuple[tuple[str, str], ...] = (
    ("vm", "openai_relay_host"),
    ("vm", "openai_relay_port"),
)
_ISSUE_SETUP_AGENT_DEFAULT_FIELDS: tuple[tuple[str, str], ...] = (
    ("issue_setup", "default_model"),
    ("issue_setup", "post_exec_verification_prompt"),
)
_ISSUE_SETUP_GIT_FIELDS: tuple[tuple[str, str], ...] = (
    ("issue_setup", "git_host"),
    ("issue_setup", "git_root"),
    ("issue_setup", "git_ssh_alias"),
    ("issue_setup", "git_user"),
)
_ISSUE_SETUP_GUEST_HEARTBEAT_FIELDS: tuple[tuple[str, str], ...] = (
    ("issue_setup", "enable_guest_heartbeat"),
    ("issue_setup", "guest_heartbeat_prompt"),
)
_ISSUE_SETUP_AZURE_DEVOPS_FIELDS: tuple[tuple[str, str], ...] = (
    ("issue_setup", "issue_setup_azure_devops_pr_pat_env"),
)
_ADO_WORKER_FILTER_FIELDS: tuple[tuple[str, str], ...] = (
    ("ado_worker", "ado_area_filters"),
    ("ado_worker", "ado_team_filters"),
)
_ADO_WORKER_REPO_FIELDS: tuple[tuple[str, str], ...] = (
    ("ado_worker", "ado_repo_source"),
)
_ADO_WORKER_AUTOMATION_FIELDS: tuple[tuple[str, str], ...] = (
    ("ado_worker", "create_prs_as_draft"),
    ("ado_worker", "create_prs_automatically"),
)
_AUTOMATION_CAPACITY_FIELDS: tuple[tuple[str, str], ...] = (
    ("automation", "approved_issue_cap"),
)
_AUTOMATION_ROLE_FIELDS: tuple[tuple[str, str], ...] = (
    ("automation", "originator_roles"),
    ("automation", "prioritizer_roles"),
    ("automation", "worker_roles"),
)
_PR_TEMPLATE_FIELDS: tuple[tuple[str, str], ...] = (
    ("pr_template", "pr_template_markdown"),
)
_ASSISTANT_SETTINGS_FIELD_LABELS: dict[str, str] = {
    "default_profile": "assistant capability profile",
}
_DIRECTORY_CONFIG_FIELD_LABELS: dict[tuple[str, str], str] = {
    (COMMUNICATION_TARGETS_DOMAIN, "targets"): "Communication targets",
    ("providers", "execution_backend"): "Execution backend",
    ("providers", "phone_call_backend"): "Phone call backend",
    ("providers", "retell_api_base_url"): "Retell API base URL",
    ("providers", "retell_api_key_env"): "Retell API key env",
    ("providers", "retell_from_number"): "Retell from number",
    ("providers", "retell_default_agent_id"): "Retell default agent ID",
    ("providers", "retell_request_timeout_seconds"): "Retell request timeout seconds",
    ("providers", "didww_sip_bridge_url"): "DIDWW SIP bridge URL",
    ("providers", "didww_sip_bridge_token_env"): "DIDWW SIP bridge token env",
    ("providers", "didww_from_number"): "DIDWW from number",
    ("providers", "inbound_phone_routes"): "Inbound phone routes",
    ("providers", "model_provider_mode"): "Default model provider mode",
    ("providers", "openai_api_base_url"): "OpenAI API base URL",
    ("providers", "openai_api_key_env"): "OpenAI API key env",
    ("providers", "openai_relay_timeout_seconds"): "OpenAI relay timeout seconds",
    ("providers", "local_openai_api_base_url"): "Local OpenAI API base URL",
    ("providers", "local_openai_api_key"): "Local OpenAI API key",
    ("providers", "local_openai_provider_name"): "Local OpenAI provider name",
    ("providers", "openai_realtime_model"): "OpenAI Realtime model",
    ("providers", "openai_realtime_api_key_env"): "OpenAI Realtime API key env",
    ("providers", "openai_realtime_api_base_url"): "OpenAI Realtime API base URL",
    ("providers", "container_worker_images"): "Approved container image catalog",
    ("providers", "container_worker_guest_openai_base_url"): (
        "Container-visible OpenAI base URL"
    ),
    ("providers", "communication_artifact_public_base_url"): (
        "Communication artifact public base URL"
    ),
    ("providers", "communication_artifact_link_secret_env"): (
        "Communication artifact link secret env"
    ),
    ("providers", "communication_artifact_link_ttl_seconds"): (
        "Communication artifact link TTL seconds"
    ),
    ("policy", "mcp_tool_policies"): "MCP tool approval policy",
    ("policy", "tool_argument_policies"): "Tool argument policy",
}
_SOURCE_DOMAIN_CONFIG_FIELD_TOOLTIPS: dict[tuple[str, str], str] = {
    (COMMUNICATION_TARGETS_DOMAIN, "targets"): (
        "Directory-scoped communication targets available to workflow notification and messaging effects."
    ),
    ("policy", "tool_argument_policies"): (
        "Directory-scoped tool argument guardrails keyed by exact MCP tool name."
    ),
    ("policy", "mcp_tool_policies"): (
        "Directory-scoped MCP tool deny, approval requirement, approval default, and hard-limit rules."
    ),
    ("providers", "communication_artifact_public_base_url"): (
        "Public Admin Web or control-plane origin used to build signed download links for "
        "non-image Teams file sends. Teams recipients must be able to reach this URL."
    ),
    ("providers", "communication_artifact_link_secret_env"): (
        "Host environment variable name containing the HMAC secret for communication artifact "
        "download links. The value must be loaded by both gateway and Admin Web."
    ),
    ("providers", "communication_artifact_link_ttl_seconds"): (
        "Maximum lifetime for signed communication artifact download links, capped by the "
        "staged artifact expiry."
    ),
    (_ASSISTANT_SETTINGS_DOMAIN, "enabled"): (
        "Enables the admin web assistant launcher and chat endpoints."
    ),
    (_ASSISTANT_SETTINGS_DOMAIN, "show_launcher_when_disabled"): (
        "Keeps the assistant launcher visible even when the assistant itself is disabled."
    ),
    (_ASSISTANT_SETTINGS_DOMAIN, "read_only"): (
        "Forces assistant requests into read-only mode when enabled."
    ),
    (_ASSISTANT_SETTINGS_DOMAIN, "default_model"): (
        "Default model used for admin web assistant conversations when no request-specific model is selected."
    ),
    (_ASSISTANT_SETTINGS_DOMAIN, "workflow_generation_model"): (
        "Optional model used only for natural-language workflow graph generation."
    ),
    (_ASSISTANT_SETTINGS_DOMAIN, "default_profile"): (
        "Default assistant capability profile used to decide which admin actions the assistant may request."
    ),
    (_ASSISTANT_SETTINGS_DOMAIN, "custom_system_instructions"): (
        "Additional system instructions appended to the admin web assistant guide."
    ),
}
_DIRECTORY_CONFIG_UI_TAB_FIELD_SOURCES: dict[str, tuple[tuple[str, str], ...]] = {
    "people_communications": (
        ("contacts", "contacts"),
        *_COMMUNICATION_TARGET_FIELDS,
        *_APPROVAL_NOTIFICATION_POLICY_FIELDS,
        *_DISCORD_CONFIG_FIELDS,
        *_TEAMS_PROVIDER_CONFIG_FIELDS,
        *_EMAIL_PROVIDER_FIELDS,
        *_EMAIL_POLICY_FIELDS,
        *_PHONE_CALL_PROVIDER_FIELDS,
        *_COMMUNICATION_FILE_POLICY_FIELDS,
        *_COMMUNICATION_ARTIFACT_PROVIDER_FIELDS,
    ),
    "models_assistant_knowledge": (
        *_LLM_PROVIDER_FIELDS,
        *_ASSISTANT_SETTINGS_FIELDS,
        *_SEMANTIC_PROVIDER_FIELDS,
    ),
    "agent_runtime_launch": (
        *_AGENT_EXECUTION_PROVIDER_FIELDS,
        *_CONTAINER_WORKER_PROVIDER_FIELDS,
        *_AGENT_PROMPT_PROVIDER_FIELDS,
        *_ISSUE_SETUP_AGENT_DEFAULT_FIELDS,
        *_ISSUE_SETUP_GUEST_HEARTBEAT_FIELDS,
    ),
    "code_issues_prs": (
        *_AZURE_DEVOPS_PROVIDER_FIELDS,
        *_GITHUB_PROVIDER_FIELDS,
        *_ADO_WORKER_FILTER_FIELDS,
        *_ADO_WORKER_REPO_FIELDS,
        *_ADO_WORKER_AUTOMATION_FIELDS,
        *_ISSUE_SETUP_GIT_FIELDS,
        *_ISSUE_SETUP_AZURE_DEVOPS_FIELDS,
        *_PR_REPLY_POLICY_FIELDS,
        *_PR_TEMPLATE_FIELDS,
    ),
    "access_approvals_audit": (
        *_APPROVAL_REQUIRED_POLICY_FIELDS,
        *_APPROVAL_TIMING_POLICY_FIELDS,
        *_MCP_TOOL_POLICY_FIELDS,
        *_TOOL_ARGUMENT_POLICY_FIELDS,
        *_AUDIT_POLICY_FIELDS,
    ),
    "network_relays_packages": (
        *_VM_PROVIDER_FIELDS,
        *_VM_LIBVIRT_TEMPLATE_FIELDS,
        *_VM_NETWORK_FIELDS,
        *_VM_GATEWAY_FIELDS,
        *_VM_HTTP_TARGET_RELAY_FIELDS,
        *_HTTP_TARGET_RELAY_PROVIDER_FIELDS,
        *_VM_OPENAI_RELAY_FIELDS,
        *_VM_NUGET_RELAY_FIELDS,
        *_NUGET_PROVIDER_FIELDS,
        *_VM_NPM_AUDIT_FACADE_FIELDS,
        *_NPM_AUDIT_FACADE_PROVIDER_FIELDS,
    ),
    "automation_assignment": (
        *_AUTOMATION_CAPACITY_FIELDS,
        *_AUTOMATION_ROLE_FIELDS,
    ),
    "advanced_raw": (
        *_PROVIDER_STATE_FIELDS,
    ),
}
_DIRECTORY_CONFIG_UI_TAB_TITLES: dict[str, str] = {
    "people_communications": "People & Communications",
    "models_assistant_knowledge": "Models, Assistant & Knowledge",
    "agent_runtime_launch": "Agent Runtime & Launch",
    "code_issues_prs": "Code, Issues & Pull Requests",
    "access_approvals_audit": "Access, Approvals & Audit",
    "network_relays_packages": "Network, Relays & Package Access",
    "automation_assignment": "Automation & Assignment",
    "advanced_raw": "Advanced & Raw",
}
_DIRECTORY_CONFIG_TAB_SECTIONS: dict[
    str, tuple[tuple[str, tuple[tuple[str, str], ...], tuple[str, ...]], ...]
] = {
    "people_communications": (
        ("Contacts", (("contacts", "contacts"),), ()),
        ("Communication targets", _COMMUNICATION_TARGET_FIELDS, ()),
        (
            "Approval notification targets",
            _APPROVAL_NOTIFICATION_POLICY_FIELDS,
            (),
        ),
        ("Discord", _DISCORD_CONFIG_FIELDS, ("discord_",)),
        ("Teams", _TEAMS_PROVIDER_CONFIG_FIELDS, ("teams_",)),
        ("Email mailboxes", _EMAIL_PROVIDER_FIELDS, ("email_",)),
        (
            "Email access and sending policy",
            _EMAIL_POLICY_FIELDS,
            ("email_",),
        ),
        (
            "Phone calls",
            _PHONE_CALL_PROVIDER_FIELDS,
            ("retell_", "didww_", "phone_call_backend"),
        ),
        (
            "Communication files",
            _COMMUNICATION_FILE_POLICY_FIELDS,
            ("communication_file_",),
        ),
        (
            "Communication artifact links",
            _COMMUNICATION_ARTIFACT_PROVIDER_FIELDS,
            ("communication_artifact_",),
        ),
    ),
    "models_assistant_knowledge": (
        ("LLM providers", _LLM_PROVIDER_FIELDS, ("openai_", "voice_media_")),
        ("Assistant", _ASSISTANT_SETTINGS_FIELDS, ()),
        ("Semantic store", _SEMANTIC_PROVIDER_FIELDS, ("semantic_", "qdrant_")),
    ),
    "agent_runtime_launch": (
        ("Agent execution", _AGENT_EXECUTION_PROVIDER_FIELDS, ()),
        ("Container worker settings", _CONTAINER_WORKER_PROVIDER_FIELDS, ("container_worker_",)),
        (
            "Agent prompt",
            _AGENT_PROMPT_PROVIDER_FIELDS,
            ("agent_prompt_token_", "agent_prompt_queue_", "agent_context_retry_"),
        ),
        ("Agent defaults", _ISSUE_SETUP_AGENT_DEFAULT_FIELDS, ()),
        ("Guest heartbeat", _ISSUE_SETUP_GUEST_HEARTBEAT_FIELDS, ("guest_",)),
    ),
    "code_issues_prs": (
        ("Azure DevOps", _AZURE_DEVOPS_PROVIDER_FIELDS, ("azure_devops_",)),
        ("GitHub", _GITHUB_PROVIDER_FIELDS, ("github_",)),
        ("ADO worker filters", _ADO_WORKER_FILTER_FIELDS, ("ado_",)),
        ("ADO worker repo metadata", _ADO_WORKER_REPO_FIELDS, ("ado_",)),
        ("ADO PR automation", _ADO_WORKER_AUTOMATION_FIELDS, ("ado_",)),
        ("Git transport", _ISSUE_SETUP_GIT_FIELDS, ("git_",)),
        (
            "Issue setup Azure DevOps",
            _ISSUE_SETUP_AZURE_DEVOPS_FIELDS,
            ("issue_setup_", "azure_devops_"),
        ),
        ("PR feedback approval", _PR_REPLY_POLICY_FIELDS, ()),
        ("PR template", _PR_TEMPLATE_FIELDS, ()),
    ),
    "access_approvals_audit": (
        (
            "Approval requirements",
            _APPROVAL_REQUIRED_POLICY_FIELDS,
            (),
        ),
        (
            "MCP tool policy",
            _MCP_TOOL_POLICY_FIELDS,
            (),
        ),
        (
            "Tool argument policy",
            _TOOL_ARGUMENT_POLICY_FIELDS,
            (),
        ),
        (
            "Approval timing",
            _APPROVAL_TIMING_POLICY_FIELDS,
            (),
        ),
        ("Audit redaction", _AUDIT_POLICY_FIELDS, ()),
    ),
    "network_relays_packages": (
        ("VM provider", _VM_PROVIDER_FIELDS, ()),
        ("Libvirt templates", _VM_LIBVIRT_TEMPLATE_FIELDS, ("libvirt_vm_",)),
        ("Network", _VM_NETWORK_FIELDS, ()),
        ("Gateway", _VM_GATEWAY_FIELDS, ()),
        (
            "HTTP target relay",
            (*_VM_HTTP_TARGET_RELAY_FIELDS, *_HTTP_TARGET_RELAY_PROVIDER_FIELDS),
            ("http_target_relay_",),
        ),
        ("OpenAI relay", _VM_OPENAI_RELAY_FIELDS, ("openai_relay_",)),
        (
            "NuGet",
            (*_VM_NUGET_RELAY_FIELDS, *_NUGET_PROVIDER_FIELDS),
            ("nuget_", "nuget_relay_"),
        ),
        (
            "NPM audit facade",
            (*_VM_NPM_AUDIT_FACADE_FIELDS, *_NPM_AUDIT_FACADE_PROVIDER_FIELDS),
            ("npm_audit_facade_",),
        ),
    ),
    "automation_assignment": (
        ("Capacity", _AUTOMATION_CAPACITY_FIELDS, ()),
        ("Roles", _AUTOMATION_ROLE_FIELDS, ()),
    ),
    "advanced_raw": (
        ("Provider state", _PROVIDER_STATE_FIELDS, ()),
    ),
}
_DIRECTORY_CONFIG_SNAPSHOT_DOMAINS: tuple[str, ...] = (
    *DIRECTORY_CONFIG_DOMAIN_FILENAMES.keys(),
    COMMUNICATION_TARGETS_DOMAIN,
)
_WORKER_GLOBAL_CONFIG_MODELS: dict[str, type[BaseModel]] = {
    "ado": AdoWorkerGlobalConfig,
    "proxy-issue-sync": ProxyIssueSyncWorkerGlobalConfig,
    "discord-wake": DiscordWakeWorkerGlobalConfig,
    "scheduled-wake": ScheduledWakeWorkerGlobalConfig,
    "teams-ingest": TeamsIngestWorkerGlobalConfig,
    "teams-meeting-ingest": TeamsMeetingIngestWorkerGlobalConfig,
    "trigger-orchestrator": TriggerOrchestratorWorkerGlobalConfig,
}
_WORKER_GLOBAL_CONFIG_TOOLTIPS: dict[str, str] = {
    "runtime_dir": "Base runtime directory for the worker's state and default relative paths.",
    "poll_interval_seconds": "Base polling interval in seconds for the worker loop.",
    "directories": (
        "Exact canonical directory paths the worker should process on each cycle. "
        "Child directories must be listed separately."
    ),
    "yaml_output_dir": "Directory where fetched work items are rendered as YAML.",
    "processed_store_path": "JSON state file used to remember handled work items.",
    "fetch_retry_count": "How many times the worker retries Azure DevOps fetch calls.",
    "fetch_retry_backoff_seconds": "Backoff in seconds between Azure DevOps fetch retries.",
    "include_comments": "Whether fetched work item comments are attached to YAML payloads.",
    "comments_max_count": "Maximum number of comments fetched per work item.",
    "comment_text_max_chars": "Maximum number of comment characters kept per comment.",
    "freshness_window_seconds": "How recently a proxy issue can have synced before the worker skips it.",
    "host_busy_check_script_path": "Host script used to determine whether a session VM is busy before resuming it.",
    "host_resume_script_path": "Host script invoked to resume a session VM when feedback is waiting.",
    "jitter_seconds": "Random delay added to each worker sleep interval.",
    "backoff_seconds": "Sleep interval used after a worker cycle fails before retrying.",
    "cooldown_seconds": "Minimum cooldown before the worker retries the same session.",
    "lock_path": "Single-instance lock file for the worker.",
    "busy_check_script_path": "Host script used to detect whether a session VM is busy.",
    "resume_script_path": "Host script invoked to resume a session VM.",
    "wake_state_path": "Host-side JSON state file written by resume attempts.",
    "vm_user": "Guest username passed to resume scripts.",
    "resume_timeout_seconds": "Timeout in seconds for a resume invocation.",
    "session_cooldown_path": "JSON state file used for per-session cooldown tracking.",
    "scheduled_wake_prefix_text": "Static text prepended to scheduled resume prompts before dispatch.",
    "event_queue_store_path": "JSON queue file used to persist pending workflow events.",
    "event_queue_cursor_store_path": "JSON cursor state file tracking workflow poll progress.",
    "workflow_event_journal_path": "JSONL journal used to notify workflow editor streams about runtime state changes.",
    "effect_execution_store_path": "JSON execution log for effect-orchestrator runs.",
    "orchestration_wal_checkpoint_mode": "SQLite orchestration-store WAL checkpoint mode.",
    "orchestration_wal_checkpoint_min_bytes": "Minimum WAL size before explicit checkpoint maintenance runs.",
    "orchestration_wal_checkpoint_interval_seconds": "Minimum interval between explicit WAL checkpoint attempts.",
    "orchestration_wal_checkpoint_busy_timeout_ms": "Short busy timeout used for explicit WAL checkpoint attempts.",
    "effect_cursor_store_path": "JSON cursor state file written by the effect orchestrator.",
    "effect_lock_path": "Single-instance lock file for the effect orchestrator worker loop.",
    "triggers": "Configured trigger definitions for the trigger orchestrator graph.",
    "effects": "Configured effect definitions for the trigger orchestrator graph.",
}


def _json_payload(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def _error_response(
    message: str, *, status_code: int = 400, details: dict[str, Any] | None = None
) -> JSONResponse:
    payload: dict[str, Any] = {"error": message}
    if details:
        payload["details"] = details
    return JSONResponse(payload, status_code=status_code)


def _json_revision(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _expected_revision(payload: dict[str, Any]) -> str | None:
    expected = payload.get("expected_revision")
    if not isinstance(expected, str) or not expected.strip():
        return None
    return expected.strip()


async def _json_body(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except json.JSONDecodeError as exc:
        raise ValueError(f"Request body must be valid JSON: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object")
    return payload


def _directory_path_from_request(request: Request) -> str:
    return str(request.query_params.get("directory_path") or "root").strip() or "root"


def _normalize_worker_summary(summary: Any) -> dict[str, Any]:
    return {
        "worker_id": summary.worker_id,
        "name": summary.name,
        "unit": summary.unit,
        "active_state": summary.active_state,
        "sub_state": summary.sub_state,
        "result": summary.result,
        "unit_file_state": summary.unit_file_state,
        "description": summary.description,
        "main_pid": summary.main_pid,
        "running": summary.running,
        "failed": summary.failed,
        "detail": summary.detail,
    }


def _normalize_directory_tree(node: Any) -> dict[str, Any]:
    return {
        "directory": _json_payload(node.directory),
        "config_domains": list(node.config_domains),
        "children": [_normalize_directory_tree(child) for child in node.children],
    }


def _normalize_admin_result(result: Any) -> dict[str, Any] | None:
    if result is None:
        return None
    return {
        "ok": bool(result.ok),
        "data": _json_payload(result.data) if result.data is not None else None,
        "errors": [_json_payload(error) for error in result.errors],
        "validation_diagnostics": [_json_payload(item) for item in result.validation_diagnostics],
    }


def _parent_directory_path(directory_path: str) -> str | None:
    candidate = Path(directory_path)
    if len(candidate.parts) <= 1:
        return None
    return candidate.parent.as_posix()


def _pretty_config_field_name(name: str) -> str:
    return name.replace("_", " ")


def _config_field_display_name(field_name: str, *, prefixes: tuple[str, ...] = ()) -> str:
    display_name = field_name
    while True:
        for prefix in prefixes:
            if display_name.startswith(prefix):
                display_name = display_name.removeprefix(prefix)
                break
        else:
            break
    return _pretty_config_field_name(display_name)


def _unwrap_optional_annotation(annotation: Any) -> Any:
    origin = get_origin(annotation)
    if origin in {UnionType} or str(origin) == "typing.Union":
        non_none_args = tuple(arg for arg in get_args(annotation) if arg is not type(None))
        if len(non_none_args) == 1:
            return non_none_args[0]
    return annotation


def _is_string_list_annotation(annotation: Any) -> bool:
    annotation = _unwrap_optional_annotation(annotation)
    origin = get_origin(annotation)
    if origin is None:
        return False
    if origin not in {list, tuple} and str(origin) not in {"typing.List", "typing.Sequence"}:
        return False
    args = get_args(annotation)
    return len(args) == 1 and args[0] is str


def _field_kind(
    field_name: str, *, model_sets: tuple[dict[str, Any], ...]
) -> tuple[str, tuple[str, ...]]:
    annotation = None
    for model_fields in model_sets:
        field_info = model_fields.get(field_name)
        if field_info is not None:
            annotation = field_info.annotation
            break
    if annotation is None:
        return ("json", ())
    annotation = _unwrap_optional_annotation(annotation)
    origin = get_origin(annotation)
    if annotation is bool:
        return ("bool", ())
    if annotation is int:
        return ("int", ())
    if annotation is float:
        return ("float", ())
    if origin is not None and str(origin).endswith("Literal"):
        return ("literal", tuple(str(choice) for choice in get_args(annotation)))
    if _is_string_list_annotation(annotation):
        return ("string_list", ())
    if annotation is str and (
        field_name.endswith("_prompt")
        or field_name.endswith("_markdown")
        or field_name.endswith("_instructions")
    ):
        return ("multiline_text", ())
    if annotation in {str, Path}:
        return ("text", ())
    return ("json", ())


def _field_value_hint(kind: str, options: tuple[str, ...]) -> str:
    if kind == "bool":
        return "Value: boolean true or false."
    if kind == "int":
        return "Value: whole number."
    if kind == "float":
        return "Value: number; decimals are allowed."
    if kind == "literal":
        rendered_options = ", ".join(
            "default" if option == "" else option for option in options
        )
        return f"Value: one of {rendered_options}."
    if kind == "multiline_text":
        return "Value: free-form multi-line text."
    if kind == "string_list":
        return "Value: one string per line; blank lines are ignored."
    if kind == "json":
        return "Value: valid JSON matching this setting's schema."
    return "Value: text."


def _directory_config_field_tooltip(
    source_domain: str,
    field_name: str,
    *,
    label: str,
    kind: str,
    options: tuple[str, ...],
) -> str:
    description = (
        _SOURCE_DOMAIN_CONFIG_FIELD_TOOLTIPS.get((source_domain, field_name))
        or CONFIG_FIELD_TOOLTIPS.get(field_name)
        or f"Configures {label}."
    )
    if source_domain == "policy" and field_name == "approval_notification_target_ids":
        return (
            f"{description} Value: select one or more known communication target IDs "
            "declared with the notify capability."
        )
    return f"{description} {_field_value_hint(kind, options)}"


def _worker_config_schema(worker_id: str) -> dict[str, Any] | None:
    model = _WORKER_GLOBAL_CONFIG_MODELS.get(worker_id)
    if model is None:
        return None
    fields: list[dict[str, Any]] = []
    for field_name in model.model_fields:
        kind, options = _field_kind(
            field_name,
            model_sets=(
                GatewayConfig.model_fields,
                AdoWorkerGlobalConfig.model_fields,
                ProxyIssueSyncWorkerGlobalConfig.model_fields,
                DiscordWakeWorkerGlobalConfig.model_fields,
                ScheduledWakeWorkerGlobalConfig.model_fields,
                TeamsIngestWorkerGlobalConfig.model_fields,
                TeamsMeetingIngestWorkerGlobalConfig.model_fields,
                TriggerOrchestratorWorkerGlobalConfig.model_fields,
            ),
        )
        fields.append(
            {
                "field_name": field_name,
                "label": _pretty_config_field_name(field_name),
                "kind": kind,
                "options": list(options),
                "tooltip": _WORKER_GLOBAL_CONFIG_TOOLTIPS.get(field_name, ""),
            }
        )
    return {"worker_id": worker_id, "fields": fields}


def _worker_detail(service: AdminRuntimeComponents, worker_id: str) -> dict[str, Any]:
    summaries = service.operations_admin.list_background_workers()
    summary = next((entry for entry in summaries if entry.worker_id == worker_id), None)
    if summary is None:
        raise ValueError(f"Unknown worker: {worker_id}")
    config = service.workflow_config_admin.read_worker_global_config(worker_id)
    return {
        "summary": _normalize_worker_summary(summary),
        "config": _json_payload(config),
        "config_schema": _worker_config_schema(worker_id),
    }


def _directory_tab_underlying_domains(tab_key: str) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            source_domain
            for source_domain, _field_name in _DIRECTORY_CONFIG_UI_TAB_FIELD_SOURCES[tab_key]
        )
    )


def _directory_tab_help_text(tab_key: str) -> str | None:
    if tab_key == "people_communications":
        return (
            "Contacts, messaging targets, Teams and Discord, email, phone calls, "
            "notifications, and communication file controls."
        )
    if tab_key == "models_assistant_knowledge":
        return (
            "Model routing, Assistant behavior, pricing estimates, semantic store, "
            "and embedding settings."
        )
    if tab_key == "agent_runtime_launch":
        return (
            "Execution backend, container worker runtime, prompt delivery, launch defaults, "
            "and guest heartbeat behavior."
        )
    if tab_key == "code_issues_prs":
        return (
            "Git transport, Azure DevOps, GitHub, ADO worker filters, PR automation, "
            "and pull request templates."
        )
    if tab_key == "access_approvals_audit":
        return (
            "Core approval timing, MCP tool guardrails, tool argument policy, and audit redaction."
        )
    if tab_key == "network_relays_packages":
        return (
            "Host and guest network endpoints, relays, NuGet feeds, and npm audit facade settings."
        )
    if tab_key == "automation_assignment":
        return (
            "Role and capacity controls that govern automated issue intake and worker assignment."
        )
    if tab_key == "advanced_raw":
        return "Advanced provider-specific JSON state and support diagnostics."
    return None


def _approval_notification_target_options(
    service: AdminRuntimeComponents, directory_path: str
) -> tuple[str, ...]:
    try:
        result = service.operations_admin.compute_directory_effective_config(
            directory_path, COMMUNICATION_TARGETS_DOMAIN
        )
    except Exception:
        return ()
    if not result.ok or result.data is None:
        return ()
    targets = result.data.resolution.effective_config.get("targets")
    if not isinstance(targets, list):
        return ()
    options: list[str] = []
    for target in targets:
        if not isinstance(target, dict):
            continue
        target_id = str(target.get("id") or "").strip()
        policy = target.get("policy")
        capabilities = policy.get("capabilities") if isinstance(policy, dict) else []
        if (
            target_id
            and isinstance(capabilities, list)
            and "notify" in {str(capability) for capability in capabilities}
        ):
            options.append(target_id)
    return tuple(options)


def _directory_config_field_metadata(
    service: AdminRuntimeComponents,
    directory_path: str,
    tab_key: str,
    source_domain: str,
    field_name: str,
    prefixes: tuple[str, ...],
) -> dict[str, Any]:
    kind, options = _field_kind(
        field_name,
        model_sets=(
            GatewayConfig.model_fields,
            AdoWorkerGlobalConfig.model_fields,
            ProxyIssueSyncWorkerGlobalConfig.model_fields,
            DiscordWakeWorkerGlobalConfig.model_fields,
            ScheduledWakeWorkerGlobalConfig.model_fields,
            TeamsIngestWorkerGlobalConfig.model_fields,
            TeamsMeetingIngestWorkerGlobalConfig.model_fields,
            TriggerOrchestratorWorkerGlobalConfig.model_fields,
            AdoWorkerDirectoryConfig.model_fields,
            AutomationDirectoryConfig.model_fields,
            ContactDirectoryConfig.model_fields,
            IssueSetupDirectoryConfig.model_fields,
            PrTemplateDirectoryConfig.model_fields,
            AssistantSettingsConfig.model_fields,
            EffectiveVmDefaultsConfig.model_fields,
        ),
    )
    if source_domain == "policy" and field_name == "approval_notification_target_ids":
        options = _approval_notification_target_options(service, directory_path)
    label = (
        _ASSISTANT_SETTINGS_FIELD_LABELS.get(field_name)
        if source_domain == _ASSISTANT_SETTINGS_DOMAIN
        else None
    ) or _DIRECTORY_CONFIG_FIELD_LABELS.get(
        (source_domain, field_name)
    ) or _config_field_display_name(field_name, prefixes=prefixes)
    return {
        "source_domain": source_domain,
        "field_name": field_name,
        "label": label,
        "kind": kind,
        "options": list(options),
        "scope": "global" if source_domain == _ASSISTANT_SETTINGS_DOMAIN else "directory",
        "root_starts_editable": (
            (source_domain == "pr_template" and field_name == "pr_template_markdown")
            or (source_domain == COMMUNICATION_TARGETS_DOMAIN and field_name == "targets")
        ),
        "tooltip": _directory_config_field_tooltip(
            source_domain, field_name, label=label, kind=kind, options=options
        ),
    }


def _directory_tab_metadata(
    service: AdminRuntimeComponents, directory_path: str, tab_key: str
) -> dict[str, Any]:
    sections = []
    for title, field_sources, prefixes in _DIRECTORY_CONFIG_TAB_SECTIONS.get(tab_key, ()):
        sections.append(
            {
                "title": title,
                "fields": [
                    _directory_config_field_metadata(
                        service,
                        directory_path,
                        tab_key,
                        source_domain,
                        field_name,
                        prefixes,
                    )
                    for source_domain, field_name in field_sources
                ],
            }
        )
    if not sections:
        sections = [
            {
                "title": _DIRECTORY_CONFIG_UI_TAB_TITLES.get(tab_key, tab_key),
                "fields": [
                    _directory_config_field_metadata(
                        service,
                        directory_path,
                        tab_key,
                        source_domain,
                        field_name,
                        (),
                    )
                    for source_domain, field_name in _DIRECTORY_CONFIG_UI_TAB_FIELD_SOURCES[tab_key]
                ],
            }
        ]
    return {
        "key": tab_key,
        "title": _DIRECTORY_CONFIG_UI_TAB_TITLES.get(tab_key, tab_key),
        "help_text": _directory_tab_help_text(tab_key),
        "underlying_domains": list(_directory_tab_underlying_domains(tab_key)),
        "sections": sections,
    }


def _domain_snapshot(service: AdminRuntimeComponents, directory_path: str, domain: str) -> dict[str, Any]:
    raw_result = service.operations_admin.read_directory_domain_config(directory_path, domain)
    effective_result = service.operations_admin.compute_directory_effective_config(directory_path, domain)
    parent_directory_path = _parent_directory_path(directory_path)
    parent_result = (
        service.operations_admin.compute_directory_effective_config(parent_directory_path, domain)
        if parent_directory_path is not None
        else None
    )
    raw_config = (
        raw_result.data.config
        if raw_result.ok and raw_result.data is not None and raw_result.data.config is not None
        else {}
    )
    effective_config = (
        effective_result.data.resolution.effective_config
        if effective_result.ok and effective_result.data is not None
        else None
    )
    parent_effective = (
        parent_result.data.resolution.effective_config
        if parent_result is not None and parent_result.ok and parent_result.data is not None
        else None
    )
    return {
        "domain": domain,
        "revision": _json_revision(
            {
                "domain": domain,
                "exists": bool(
                    raw_result.ok and raw_result.data is not None and raw_result.data.exists
                ),
                "config": raw_config,
            }
        ),
        "raw": {
            "exists": bool(
                raw_result.ok and raw_result.data is not None and raw_result.data.exists
            ),
            "config": raw_config,
            "result": _normalize_admin_result(raw_result),
        },
        "effective": {
            "config": effective_config,
            "result": _normalize_admin_result(effective_result),
        },
        "parent_defaults": {
            "directory_path": parent_directory_path,
            "config": parent_effective,
            "result": _normalize_admin_result(parent_result),
        },
    }


def _assistant_settings_snapshot(service: AdminRuntimeComponents) -> dict[str, Any]:
    path = service.operations_admin.assistant_settings_path()
    config = service.operations_admin.read_assistant_settings()
    result = {
        "ok": True,
        "data": {
            "path": str(path),
            "exists": path.exists(),
            "config": config,
            "scope": "global",
        },
        "errors": [],
        "validation_diagnostics": [],
    }
    return {
        "domain": _ASSISTANT_SETTINGS_DOMAIN,
        "revision": _json_revision(
            {
                "domain": _ASSISTANT_SETTINGS_DOMAIN,
                "exists": path.exists(),
                "config": config,
            }
        ),
        "raw": {
            "exists": path.exists(),
            "config": config,
            "result": result,
        },
        "effective": {
            "config": config,
            "result": result,
        },
        "parent_defaults": {
            "directory_path": None,
            "config": {},
            "result": None,
        },
    }


def _aggregate_tab_snapshot(
    service: AdminRuntimeComponents,
    directory_path: str,
    tab_key: str,
    domain_snapshots: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    selected_path = directory_path
    parent_path = _parent_directory_path(selected_path)
    raw_config: dict[str, Any] = {}
    parent_effective_config: dict[str, Any] = {}
    effective_config: dict[str, Any] = {}
    validation_diagnostics: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    raw_exists = False
    payload_effective: dict[str, Any] = {"resolution": {}, "validation_diagnostics": []}
    payload_parent: dict[str, Any] = {
        "directory_path": parent_path,
        "resolution": {},
    }
    for source_domain in _directory_tab_underlying_domains(tab_key):
        snapshot = domain_snapshots[source_domain]
        raw = snapshot["raw"]
        effective = snapshot["effective"]
        parent_defaults = snapshot["parent_defaults"]
        raw_exists = raw_exists or bool(raw["exists"])
        raw_config.update(raw["config"])
        if parent_defaults["config"] is not None:
            parent_effective_config.update(parent_defaults["config"])
        if effective["config"] is not None:
            effective_config.update(effective["config"])
        if effective["result"] is not None:
            validation_diagnostics.extend(effective["result"]["validation_diagnostics"])
            errors.extend(effective["result"]["errors"])
            payload_effective["resolution"][source_domain] = effective["result"]["data"]
            payload_effective["validation_diagnostics"].extend(
                effective["result"]["validation_diagnostics"]
            )
        if parent_defaults["result"] is not None:
            payload_parent["resolution"][source_domain] = parent_defaults["result"]["data"]
    if parent_path is None:
        payload_parent = {
            "directory_path": None,
            "note": "Root directory has no parent directory. It is the top of the directory config chain.",
        }
    revision_payload = {
        "tab": tab_key,
        "domains": {
            source_domain: {
                "exists": bool(domain_snapshots[source_domain]["raw"]["exists"]),
                "config": domain_snapshots[source_domain]["raw"]["config"],
            }
            for source_domain in _directory_tab_underlying_domains(tab_key)
        },
    }
    return {
        "key": tab_key,
        "title": _DIRECTORY_CONFIG_UI_TAB_TITLES.get(tab_key, tab_key),
        "revision": _json_revision(revision_payload),
        "raw": {
            "exists": raw_exists,
            "config": raw_config,
        },
        "effective": {
            "config": effective_config,
            "validation_diagnostics": validation_diagnostics,
            "errors": errors,
            "payload": payload_effective,
        },
        "parent_defaults": {
            "directory_path": parent_path,
            "config": parent_effective_config,
            "payload": payload_parent,
        },
    }


def _directory_overrides_snapshot(
    directory_path: str, domain_snapshots: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    overrides: dict[str, dict[str, Any]] = {}
    errors: list[dict[str, Any]] = []
    for domain in _DIRECTORY_CONFIG_SNAPSHOT_DOMAINS:
        snapshot = domain_snapshots.get(domain)
        if snapshot is None:
            continue
        raw = snapshot["raw"]
        raw_result = raw.get("result")
        if raw_result is not None and not raw_result["ok"]:
            errors.extend(raw_result["errors"])
            continue
        if not raw["exists"]:
            continue
        overrides[domain] = raw["config"]
    payload = {
        "directory_path": directory_path,
        "overrides": overrides,
    }
    return {
        "directory_path": directory_path,
        "exists": bool(overrides),
        "domain_count": len(overrides),
        "field_count": sum(len(config) for config in overrides.values()),
        "overrides": overrides,
        "errors": errors,
        "payload": payload,
    }


def _removed_fields_from_payload(payload: dict[str, Any]) -> list[str]:
    raw_removed_fields = payload.get("removed_fields", [])
    if raw_removed_fields is None:
        return []
    if not isinstance(raw_removed_fields, list) or not all(
        isinstance(field_name, str) for field_name in raw_removed_fields
    ):
        raise ValueError("removed_fields must be a list of field names")
    return raw_removed_fields


def _split_tab_payload(
    tab_key: str, config: dict[str, Any], *, removed_fields: list[str] | None = None
) -> dict[str, dict[str, Any]]:
    field_sources = {
        field_name: source_domain
        for source_domain, field_name in _DIRECTORY_CONFIG_UI_TAB_FIELD_SOURCES[tab_key]
    }
    payloads_by_source: dict[str, dict[str, Any]] = {}
    for field_name, value in config.items():
        source_domain = field_sources.get(field_name)
        if source_domain is None:
            raise ValueError(f"Unknown field for {tab_key}: {field_name}")
        payloads_by_source.setdefault(source_domain, {})[field_name] = value
    for field_name in removed_fields or []:
        source_domain = field_sources.get(field_name)
        if source_domain is None:
            raise ValueError(f"Unknown removed field for {tab_key}: {field_name}")
        if source_domain == _ASSISTANT_SETTINGS_DOMAIN:
            raise ValueError(
                f"Global configuration field cannot be removed from {tab_key}: {field_name}"
            )
        payloads_by_source.setdefault(source_domain, {})
    return payloads_by_source


def _tab_fields_by_source_domain(tab_key: str) -> dict[str, set[str]]:
    fields_by_source: dict[str, set[str]] = {}
    for source_domain, field_name in _DIRECTORY_CONFIG_UI_TAB_FIELD_SOURCES[tab_key]:
        fields_by_source.setdefault(source_domain, set()).add(field_name)
    return fields_by_source


def worker_config_routes(
    service: AdminRuntimeComponents,
    auth: AdminWebAuth,
    cache: AdminWebJsonResponseCache | None = None,
) -> list[BaseRoute]:
    async def list_workers(_: Request) -> JSONResponse:
        workers = [
            _normalize_worker_summary(summary)
            for summary in service.operations_admin.list_background_workers()
        ]
        return JSONResponse({"workers": workers})

    async def get_worker(request: Request) -> JSONResponse:
        worker_id = str(request.path_params.get("worker_id") or "").strip()
        try:
            payload = _worker_detail(service, worker_id)
        except ValueError as exc:
            return _error_response(str(exc), status_code=404)
        return JSONResponse(payload)

    async def get_worker_log(request: Request) -> JSONResponse:
        worker_id = str(request.path_params.get("worker_id") or "").strip()
        try:
            lines = int(request.query_params.get("lines", "200"))
        except ValueError:
            return _error_response("lines must be an integer", status_code=400)
        try:
            detail = _worker_detail(service, worker_id)
            text = service.operations_admin.get_background_worker_log_snapshot(worker_id, lines=lines)
        except ValueError as exc:
            return _error_response(str(exc), status_code=404)
        return JSONResponse(
            {
                "worker_id": worker_id,
                "worker_name": detail["summary"]["name"],
                "unit": detail["summary"]["unit"],
                "lines": lines,
                "text": text,
            }
        )

    async def write_worker_config(request: Request) -> JSONResponse:
        worker_id = str(request.path_params.get("worker_id") or "").strip()
        try:
            payload = await _json_body(request)
        except ValueError as exc:
            return _error_response(str(exc), status_code=400)
        config = payload.get("config")
        if not isinstance(config, dict):
            return _error_response("config must be a JSON object", status_code=400)
        expected_revision = _expected_revision(payload)
        if expected_revision is None:
            return _error_response("expected_revision is required", status_code=428)
        try:
            service.workflow_config_admin.write_worker_global_config(
                worker_id, config, expected_revision=expected_revision
            )
            detail = _worker_detail(service, worker_id)
        except AdminRevisionConflict as exc:
            return _error_response(str(exc), status_code=409, details=exc.details)
        except ValueError as exc:
            message = str(exc)
            status_code = 404 if message.startswith("Unknown worker:") else 400
            return _error_response(message, status_code=status_code)
        return JSONResponse(detail)

    async def worker_action(request: Request) -> JSONResponse:
        worker_id = str(request.path_params.get("worker_id") or "").strip()
        action = str(request.path_params.get("action") or "").strip()
        if action == "start":
            result = service.operations_admin.start_background_worker(worker_id)
        elif action == "stop":
            result = service.operations_admin.stop_background_worker(worker_id)
        else:
            return _error_response(f"Unsupported worker action: {action}", status_code=404)
        status_code = (
            200
            if result.success
            else (404 if result.message.startswith("Unknown worker:") else 400)
        )
        payload = {
            "result": _json_payload(result),
            "worker": _worker_detail(service, worker_id) if result.success else None,
        }
        return JSONResponse(payload, status_code=status_code)

    async def configuration_state(request: Request) -> JSONResponse:
        directory_path = _directory_path_from_request(request)
        tree_result = service.operations_admin.list_directory_tree()
        directory_result = service.operations_admin.get_directory(directory_path)
        selected_path = (
            directory_result.data.details.directory.path
            if directory_result.ok and directory_result.data is not None
            else "root"
        )
        domain_snapshots = {
            domain: _domain_snapshot(service, selected_path, domain)
            for domain in _DIRECTORY_CONFIG_SNAPSHOT_DOMAINS
        }
        domain_snapshots[_ASSISTANT_SETTINGS_DOMAIN] = _assistant_settings_snapshot(service)
        tabs = [
            _directory_tab_metadata(service, selected_path, tab_key)
            for tab_key in _DIRECTORY_CONFIG_UI_TAB_FIELD_SOURCES
        ]
        tab_snapshots = {
            tab_key: _aggregate_tab_snapshot(service, selected_path, tab_key, domain_snapshots)
            for tab_key in _DIRECTORY_CONFIG_UI_TAB_FIELD_SOURCES
        }
        payload = {
            "directory_scoped_config_enabled": service.config.enable_directory_scoped_config,
            "selected_directory_path": selected_path,
            "execution_backend": service.vm_agent_admin.effective_execution_backend(selected_path),
            "selected_directory": {
                "path": selected_path,
                "parent_path": _parent_directory_path(selected_path),
                "can_create": bool(service.config.enable_directory_scoped_config),
                "can_delete": bool(
                    service.config.enable_directory_scoped_config and selected_path != "root"
                ),
            },
            "tree": _normalize_admin_result(tree_result),
            "directory": _normalize_admin_result(directory_result),
            "domains": list(_DIRECTORY_CONFIG_SNAPSHOT_DOMAINS),
            "tabs": tabs,
            "tab_snapshots": tab_snapshots,
            "directory_overrides": _directory_overrides_snapshot(
                selected_path, domain_snapshots
            ),
            "snapshots": domain_snapshots,
        }
        if tree_result.ok and tree_result.data is not None:
            payload["tree"]["data"] = _normalize_directory_tree(tree_result.data)
        return JSONResponse(payload)

    async def get_model_pricing_catalog(_: Request) -> JSONResponse:
        return JSONResponse(service.model_usage_admin.read_model_pricing_catalog_status())

    async def put_model_pricing_catalog(request: Request) -> JSONResponse:
        if not request_has_privileged_operations(
            auth=auth,
            service=service,
            request=request,
            required_permission=ADMIN_OWNER_PERMISSION,
        ):
            return _error_response("Privileged operations are locked.", status_code=403)
        try:
            payload = await _json_body(request)
        except ValueError as exc:
            return _error_response(str(exc), status_code=400)
        catalog = payload.get("catalog")
        if not isinstance(catalog, dict):
            return _error_response("catalog must be a JSON object", status_code=400)
        expected_revision = _expected_revision(payload)
        if expected_revision is None:
            return _error_response("expected_revision is required", status_code=428)
        try:
            status = service.model_usage_admin.write_model_pricing_catalog(
                catalog, expected_revision=expected_revision
            )
        except AdminRevisionConflict as exc:
            return _error_response(str(exc), status_code=409, details=exc.details)
        except ValueError as exc:
            return _error_response(str(exc), status_code=400)
        return JSONResponse(status)

    async def create_directory(request: Request) -> JSONResponse:
        try:
            payload = await _json_body(request)
        except ValueError as exc:
            return _error_response(str(exc), status_code=400)
        parent_path = str(payload.get("parent_path") or "root").strip() or "root"
        name = str(payload.get("name") or "").strip()
        if not name:
            return _error_response("name is required", status_code=400)
        result = service.operations_admin.create_directory_child(parent_path, name)
        status_code = 200 if result.ok else 400
        return JSONResponse(_normalize_admin_result(result), status_code=status_code)

    async def delete_directory(request: Request) -> JSONResponse:
        directory_path = _directory_path_from_request(request)
        result = service.directory_admin.delete(directory_path, recursive=False)
        status_code = 200 if result.ok else 400
        return JSONResponse(_normalize_admin_result(result), status_code=status_code)

    async def write_directory_domain(request: Request) -> JSONResponse:
        domain = str(request.path_params.get("domain") or "").strip()
        directory_path = _directory_path_from_request(request)
        try:
            payload = await _json_body(request)
        except ValueError as exc:
            return _error_response(str(exc), status_code=400)
        config = payload.get("config")
        if not isinstance(config, dict):
            return _error_response("config must be a JSON object", status_code=400)
        expected_revision = _expected_revision(payload)
        if expected_revision is None:
            return _error_response("expected_revision is required", status_code=428)

        if domain in _DIRECTORY_CONFIG_UI_TAB_FIELD_SOURCES:
            try:
                removed_fields = _removed_fields_from_payload(payload)
                payloads_by_source = _split_tab_payload(
                    domain, config, removed_fields=removed_fields
                )
            except ValueError as exc:
                return _error_response(str(exc), status_code=400)
            tab_fields_by_source = _tab_fields_by_source_domain(domain)
            with service.operations_admin.admin_config_mutation_lock():
                domain_snapshots = {
                    source_domain: _domain_snapshot(service, directory_path, source_domain)
                    for source_domain in _DIRECTORY_CONFIG_SNAPSHOT_DOMAINS
                }
                domain_snapshots[_ASSISTANT_SETTINGS_DOMAIN] = _assistant_settings_snapshot(
                    service
                )
                current_tab_snapshot = _aggregate_tab_snapshot(
                    service, directory_path, domain, domain_snapshots
                )
                if current_tab_snapshot["revision"] != expected_revision:
                    return _error_response(
                        "Configuration changed before save. Reload this tab before saving again.",
                        status_code=409,
                        details={
                            "current_revision": current_tab_snapshot["revision"],
                            "tab_snapshot": current_tab_snapshot,
                        },
                    )
                for source_domain, source_payload in payloads_by_source.items():
                    if source_domain == _ASSISTANT_SETTINGS_DOMAIN:
                        if not request_has_privileged_operations(
                            auth=auth,
                            service=service,
                            request=request,
                            required_permission=ADMIN_OWNER_PERMISSION,
                        ):
                            return _error_response(
                                "Privileged operations are locked.", status_code=403
                            )
                        existing_settings = domain_snapshots[source_domain]["raw"]["config"]
                        merged_settings = dict(existing_settings)
                        merged_settings.update(source_payload)
                        try:
                            service.operations_admin.write_assistant_settings(merged_settings)
                        except Exception as exc:
                            return _error_response(str(exc), status_code=400)
                        continue
                    existing_config = dict(domain_snapshots[source_domain]["raw"]["config"])
                    merged_payload = {
                        field_name: value
                        for field_name, value in existing_config.items()
                        if field_name not in tab_fields_by_source[source_domain]
                    }
                    merged_payload.update(source_payload)
                    result = service.operations_admin.write_directory_domain_config(
                        directory_path, source_domain, merged_payload
                    )
                    if not result.ok:
                        return JSONResponse(
                            {
                                "write_result": _normalize_admin_result(result),
                                "source_domain": source_domain,
                                "tab_snapshot": None,
                            },
                            status_code=400,
                        )
                refreshed_domain_snapshots = {
                    source_domain: _domain_snapshot(service, directory_path, source_domain)
                    for source_domain in _DIRECTORY_CONFIG_SNAPSHOT_DOMAINS
                }
                refreshed_domain_snapshots[_ASSISTANT_SETTINGS_DOMAIN] = (
                    _assistant_settings_snapshot(service)
                )
                return JSONResponse(
                    {
                        "write_result": {
                            "ok": True,
                            "data": None,
                            "errors": [],
                            "validation_diagnostics": [],
                        },
                        "tab_snapshot": _aggregate_tab_snapshot(
                            service, directory_path, domain, refreshed_domain_snapshots
                        ),
                    }
                )

        with service.operations_admin.admin_config_mutation_lock():
            try:
                current_snapshot = _domain_snapshot(service, directory_path, domain)
            except ValueError as exc:
                return _error_response(str(exc), status_code=400)
            if current_snapshot["revision"] != expected_revision:
                return _error_response(
                    "Configuration changed before save. Reload this domain before saving again.",
                    status_code=409,
                    details={
                        "current_revision": current_snapshot["revision"],
                        "snapshot": current_snapshot,
                    },
                )
            result = service.operations_admin.write_directory_domain_config(directory_path, domain, config)
            status_code = 200 if result.ok else 400
            response_payload = {
                "write_result": _normalize_admin_result(result),
                "snapshot": _domain_snapshot(service, directory_path, domain)
                if result.ok
                else None,
            }
            return JSONResponse(response_payload, status_code=status_code)

    routes = [
        Route(
            "/api/workers",
            cache.wrap("workers.list", list_workers) if cache else list_workers,
            methods=["GET"],
        ),
        Route("/api/workers/{worker_id:str}", get_worker, methods=["GET"]),
        Route("/api/workers/{worker_id:str}/log", get_worker_log, methods=["GET"]),
        Route("/api/workers/{worker_id:str}/config", write_worker_config, methods=["PUT"]),
        Route("/api/workers/{worker_id:str}/actions/{action:str}", worker_action, methods=["POST"]),
        Route("/api/configuration/state", configuration_state, methods=["GET"]),
        Route("/api/configuration/model-pricing", get_model_pricing_catalog, methods=["GET"]),
        Route("/api/configuration/model-pricing", put_model_pricing_catalog, methods=["PUT"]),
        Route("/api/configuration/directories", create_directory, methods=["POST"]),
        Route("/api/configuration/directories", delete_directory, methods=["DELETE"]),
        Route("/api/configuration/domains/{domain:str}", write_directory_domain, methods=["PUT"]),
    ]
    return routes


__all__ = ["worker_config_routes"]

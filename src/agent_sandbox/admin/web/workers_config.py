from __future__ import annotations

import json
from pathlib import Path
from types import UnionType
from typing import Any, get_args, get_origin

from pydantic import BaseModel
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import BaseRoute, Route

from agent_sandbox.admin.service import (
    COMMUNICATION_FILE_SESSION_POLICY_FIELDS,
    AdminService,
)
from agent_sandbox.admin.config_tooltips import CONFIG_FIELD_TOOLTIPS
from agent_sandbox.admin.web.shared import AdminWebAuth, request_has_privileged_operations
from agent_sandbox.config import (
    AdoWorkerGlobalConfig,
    AssistantSettingsConfig,
    DiscordWakeWorkerGlobalConfig,
    GatewayConfig,
    ScheduledWakeWorkerGlobalConfig,
    TeamsIngestWorkerGlobalConfig,
    TeamsMeetingIngestWorkerGlobalConfig,
    TriggerOrchestratorWorkerGlobalConfig,
)
from agent_sandbox.directory_config import (
    AdoWorkerDirectoryConfig,
    AutomationDirectoryConfig,
    ContactDirectoryConfig,
    DIRECTORY_CONFIG_DOMAIN_FILENAMES,
    DIRECTORY_CONFIG_DOMAIN_FIELDS,
    IssueSetupDirectoryConfig,
    PrTemplateDirectoryConfig,
)
from agent_sandbox.vm.defaults_config import EffectiveVmDefaultsConfig

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
    ("policy", "discord_inbox_routes"),
    ("policy", "discord_operator_user_allowlist"),
    ("policy", "discord_poll_operator_dms"),
    ("heartbeat", "discord_resume_prompt"),
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
_ASSISTANT_SETTINGS_DOMAIN = "assistant_settings"
_ASSISTANT_SETTINGS_FIELDS: tuple[tuple[str, str], ...] = (
    (_ASSISTANT_SETTINGS_DOMAIN, "enabled"),
    (_ASSISTANT_SETTINGS_DOMAIN, "show_launcher_when_disabled"),
    (_ASSISTANT_SETTINGS_DOMAIN, "read_only"),
    (_ASSISTANT_SETTINGS_DOMAIN, "default_model"),
    (_ASSISTANT_SETTINGS_DOMAIN, "default_profile"),
    (_ASSISTANT_SETTINGS_DOMAIN, "custom_system_instructions"),
)
_ASSISTANT_SETTINGS_FIELD_LABELS: dict[str, str] = {
    "default_profile": "assistant capability profile",
}
_SOURCE_DOMAIN_CONFIG_FIELD_TOOLTIPS: dict[tuple[str, str], str] = {
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
    (_ASSISTANT_SETTINGS_DOMAIN, "default_profile"): (
        "Default assistant capability profile used to decide which admin actions the assistant may request."
    ),
    (_ASSISTANT_SETTINGS_DOMAIN, "custom_system_instructions"): (
        "Additional system instructions appended to the admin web assistant guide."
    ),
}
_DIRECTORY_CONFIG_UI_TAB_FIELD_SOURCES: dict[str, tuple[tuple[str, str], ...]] = {
    "communications": (
        *_DISCORD_CONFIG_FIELDS,
        *_TEAMS_PROVIDER_CONFIG_FIELDS,
        *_ASSISTANT_SETTINGS_FIELDS,
    ),
    "access_policy": tuple(
        ("policy", field_name)
        for field_name in sorted(
            DIRECTORY_CONFIG_DOMAIN_FIELDS["policy"]
            - {name for source_domain, name in _DISCORD_CONFIG_FIELDS if source_domain == "policy"}
        )
    ),
    "providers": tuple(
        ("providers", field_name)
        for field_name in sorted(
            DIRECTORY_CONFIG_DOMAIN_FIELDS["providers"]
            - {
                name
                for source_domain, name in (*_DISCORD_CONFIG_FIELDS, *_TEAMS_PROVIDER_CONFIG_FIELDS)
                if source_domain == "providers"
            }
        )
    ),
    "email": tuple(
        ("email", field_name) for field_name in sorted(DIRECTORY_CONFIG_DOMAIN_FIELDS["email"])
    ),
    "contacts": (("contacts", "contacts"),),
    "vm_runtime": tuple(
        ("vm", field_name) for field_name in sorted(DIRECTORY_CONFIG_DOMAIN_FIELDS["vm"])
    ),
    "issue_setup": tuple(
        ("issue_setup", field_name)
        for field_name in sorted(DIRECTORY_CONFIG_DOMAIN_FIELDS["issue_setup"])
    ),
    "ado_worker": tuple(
        ("ado_worker", field_name)
        for field_name in sorted(DIRECTORY_CONFIG_DOMAIN_FIELDS["ado_worker"])
    ),
    "automation": tuple(
        ("automation", field_name)
        for field_name in sorted(DIRECTORY_CONFIG_DOMAIN_FIELDS["automation"])
    ),
    "pr_template": tuple(
        ("pr_template", field_name)
        for field_name in sorted(DIRECTORY_CONFIG_DOMAIN_FIELDS["pr_template"])
    ),
}
_DIRECTORY_CONFIG_UI_TAB_TITLES: dict[str, str] = {
    "communications": "Communications",
    "access_policy": "Access Policy",
    "providers": "Provider Integrations",
    "email": "Email",
    "contacts": "Contacts",
    "vm_runtime": "VM Runtime",
    "issue_setup": "Issue Setup",
    "ado_worker": "ADO Worker",
    "automation": "Automation",
    "pr_template": "PR Template",
}
_DIRECTORY_CONFIG_TAB_SECTIONS: dict[
    str, tuple[tuple[str, tuple[tuple[str, str], ...], tuple[str, ...]], ...]
] = {
    "communications": (
        ("Discord", _DISCORD_CONFIG_FIELDS, ("discord_",)),
        ("Teams", _TEAMS_PROVIDER_CONFIG_FIELDS, ("teams_",)),
        ("Assistant", _ASSISTANT_SETTINGS_FIELDS, ()),
    ),
    "access_policy": (
        (
            "Session defaults",
            (
                ("policy", "approval_ttl_seconds"),
                ("policy", "default_session_ttl_seconds"),
            ),
            (),
        ),
        (
            "Email",
            (
                ("policy", "email_draft_domain_allowlist"),
                ("policy", "email_draft_recipient_allowlist"),
                ("policy", "email_folder_allowlist_by_mailbox"),
                ("policy", "email_mailbox_allowlist"),
                ("policy", "email_send_enabled"),
            ),
            ("email_",),
        ),
        (
            "Communication files",
            _COMMUNICATION_FILE_POLICY_FIELDS,
            ("communication_file_",),
        ),
        ("PR feedback", (("policy", "pr_reply_require_approval"),), ()),
        ("Audit", (("policy", "redacted_keys"),), ()),
    ),
    "providers": (
        (
            "Agent prompt",
            (
                ("providers", "agent_prompt_token_secret_env"),
                ("providers", "agent_prompt_token_ttl_seconds"),
            ),
            ("agent_prompt_token_",),
        ),
        (
            "Azure DevOps",
            (
                ("providers", "azure_devops_organization_url"),
                ("providers", "azure_devops_pat_env"),
                ("providers", "azure_devops_pr_pat_env"),
                ("providers", "azure_devops_project"),
            ),
            ("azure_devops_",),
        ),
        (
            "Container worker",
            (
                ("providers", "execution_backend"),
                ("providers", "container_worker_base_url"),
                ("providers", "container_worker_token_env"),
                ("providers", "container_worker_default_image"),
                ("providers", "container_worker_job_timeout_seconds"),
                ("providers", "container_worker_request_timeout_seconds"),
                ("providers", "container_worker_workspace_persistence"),
                ("providers", "container_worker_guest_openai_base_url"),
                ("providers", "container_worker_guest_gateway_url"),
                ("providers", "container_worker_guest_nuget_relay_base_url"),
                ("providers", "container_worker_guest_pypi_index_url"),
                ("providers", "container_worker_guest_npm_registry_url"),
                ("providers", "container_worker_mssql_enabled"),
                ("providers", "container_worker_mssql_host"),
                ("providers", "container_worker_mssql_port"),
            ),
            ("container_worker_",),
        ),
        ("GitHub", (("providers", "github_token_env"),), ("github_",)),
        (
            "HTTP target relay",
            (("providers", "http_target_relay_timeout_seconds"),),
            ("http_target_relay_",),
        ),
        (
            "NuGet",
            (
                ("providers", "nuget_azure_feed_base_url"),
                ("providers", "nuget_azure_pat_env"),
                ("providers", "nuget_public_feed_base_url"),
                ("providers", "nuget_relay_timeout_seconds"),
            ),
            ("nuget_",),
        ),
        (
            "OpenAI",
            (
                ("providers", "openai_api_base_url"),
                ("providers", "openai_api_key_env"),
                ("providers", "openai_relay_timeout_seconds"),
            ),
            ("openai_",),
        ),
        (
            "Semantic store",
            (
                ("providers", "semantic_store_provider"),
                ("providers", "qdrant_base_url"),
                ("providers", "qdrant_api_key_env"),
                ("providers", "semantic_embedding_provider"),
                ("providers", "semantic_embedding_model"),
                ("providers", "semantic_embedding_dimensions"),
                ("providers", "semantic_vector_distance"),
            ),
            ("semantic_", "qdrant_"),
        ),
        (
            "Retell",
            (
                ("providers", "retell_api_base_url"),
                ("providers", "retell_api_key_env"),
                ("providers", "retell_from_number"),
                ("providers", "retell_default_agent_id"),
                ("providers", "retell_request_timeout_seconds"),
            ),
            ("retell_",),
        ),
        ("State", (("providers", "provider_state"),), ()),
    ),
    "contacts": (
        (
            "Contact list",
            (("contacts", "contacts"),),
            (),
        ),
    ),
    "email": (
        (
            "Mailboxes",
            (
                ("email", "email_mailboxes"),
                ("email", "email_provider_timeout_seconds"),
            ),
            ("email_",),
        ),
    ),
    "vm_runtime": (
        ("Network", (("vm", "bind_network_cidr"),), ()),
        ("Gateway", (("vm", "gateway_url"),), ()),
        (
            "HTTP target relay",
            (
                ("vm", "http_target_relay_host"),
                ("vm", "http_target_relay_port"),
            ),
            ("http_target_relay_",),
        ),
        (
            "NuGet relay",
            (
                ("vm", "nuget_relay_host"),
                ("vm", "nuget_relay_port"),
            ),
            ("nuget_relay_",),
        ),
        (
            "OpenAI relay",
            (
                ("vm", "openai_relay_host"),
                ("vm", "openai_relay_port"),
            ),
            ("openai_relay_",),
        ),
    ),
    "issue_setup": (
        (
            "Agent defaults",
            (
                ("issue_setup", "default_model"),
                ("issue_setup", "post_exec_verification_prompt"),
            ),
            (),
        ),
        (
            "Git transport",
            (
                ("issue_setup", "git_host"),
                ("issue_setup", "git_root"),
                ("issue_setup", "git_ssh_alias"),
                ("issue_setup", "git_user"),
            ),
            ("git_",),
        ),
        (
            "Guest heartbeat",
            (
                ("issue_setup", "enable_guest_heartbeat"),
                ("issue_setup", "guest_heartbeat_prompt"),
            ),
            ("guest_",),
        ),
        (
            "Azure DevOps",
            (("issue_setup", "issue_setup_azure_devops_pr_pat_env"),),
            ("issue_setup_", "azure_devops_"),
        ),
    ),
    "ado_worker": (
        (
            "Filters",
            (
                ("ado_worker", "ado_area_filters"),
                ("ado_worker", "ado_team_filters"),
            ),
            ("ado_",),
        ),
        (
            "Repo and prewarm",
            (
                ("ado_worker", "ado_project_or_solution"),
                ("ado_worker", "ado_prewarm_target"),
                ("ado_worker", "ado_repo_source"),
            ),
            ("ado_",),
        ),
        (
            "Automation",
            (
                ("ado_worker", "ado_setup_vms"),
                ("ado_worker", "create_prs_as_draft"),
                ("ado_worker", "create_prs_automatically"),
            ),
            ("ado_",),
        ),
        (
            "Bootstrap",
            (
                ("ado_worker", "ado_bootstrap_dotnet"),
                ("ado_worker", "ado_bootstrap_nuget"),
                ("ado_worker", "ado_bootstrap_playwright"),
                ("ado_worker", "ado_bootstrap_frontend_deps"),
            ),
            ("ado_bootstrap_",),
        ),
        (
            "Frontend",
            (
                ("ado_worker", "ado_frontend_host_checkout"),
                ("ado_worker", "ado_frontend_install_roots"),
            ),
            ("ado_frontend_",),
        ),
        (
            "MSSQL",
            (
                ("ado_worker", "ado_bootstrap_mssql"),
                ("ado_worker", "ado_mssql_init_root"),
                ("ado_worker", "ado_mssql_sa_password_env"),
            ),
            ("ado_bootstrap_", "ado_mssql_"),
        ),
    ),
    "automation": (
        ("Capacity", (("automation", "approved_issue_cap"),), ()),
        (
            "Roles",
            (
                ("automation", "originator_roles"),
                ("automation", "prioritizer_roles"),
                ("automation", "worker_roles"),
            ),
            (),
        ),
    ),
}
_WORKER_GLOBAL_CONFIG_MODELS: dict[str, type[BaseModel]] = {
    "ado": AdoWorkerGlobalConfig,
    "discord-wake": DiscordWakeWorkerGlobalConfig,
    "scheduled-wake": ScheduledWakeWorkerGlobalConfig,
    "teams-ingest": TeamsIngestWorkerGlobalConfig,
    "teams-meeting-ingest": TeamsMeetingIngestWorkerGlobalConfig,
    "trigger-orchestrator": TriggerOrchestratorWorkerGlobalConfig,
}
_WORKER_GLOBAL_CONFIG_TOOLTIPS: dict[str, str] = {
    "runtime_dir": "Base runtime directory for the worker's state and default relative paths.",
    "poll_interval_seconds": "Base polling interval in seconds for the worker loop.",
    "directories": "Canonical directory paths the worker should process on each cycle.",
    "yaml_output_dir": "Directory where fetched work items are rendered as YAML before setup runs.",
    "setup_log_dir": "Directory where setup invocation logs are written.",
    "processed_store_path": "JSON state file used to remember handled work items.",
    "setup_script_path": "Host script invoked to provision an issue VM for selected work items.",
    "setup_timeout_seconds": "Timeout in seconds for the issue VM setup script.",
    "setup_concurrency_limit": "Maximum number of setup jobs that may run at once.",
    "setup_lock_path": "Lock file used to serialize setup work when concurrency is limited.",
    "fetch_retry_count": "How many times the worker retries Azure DevOps fetch calls.",
    "fetch_retry_backoff_seconds": "Backoff in seconds between Azure DevOps fetch retries.",
    "include_comments": "Whether fetched work item comments are attached to YAML payloads.",
    "comments_max_count": "Maximum number of comments fetched per work item.",
    "comment_text_max_chars": "Maximum number of comment characters kept per comment.",
    "host_busy_check_script_path": "Host script used to determine whether a session VM is busy before resuming it.",
    "host_resume_script_path": "Host script invoked to resume a session VM when feedback is waiting.",
    "jitter_seconds": "Random delay added to each worker sleep interval.",
    "backoff_seconds": "Sleep interval used after an ingest cycle fails before retrying.",
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
    "effect_cursor_store_path": "JSON cursor state file written by the effect orchestrator.",
    "effect_lock_path": "Single-instance lock file for the effect orchestrator worker loop.",
    "triggers": "Configured trigger definitions for the trigger orchestrator graph.",
    "effects": "Configured effect definitions for the trigger orchestrator graph.",
}


def _json_payload(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def _error_response(message: str, *, status_code: int = 400) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status_code)


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


def _worker_detail(service: AdminService, worker_id: str) -> dict[str, Any]:
    summaries = service.list_background_workers()
    summary = next((entry for entry in summaries if entry.worker_id == worker_id), None)
    if summary is None:
        raise ValueError(f"Unknown worker: {worker_id}")
    config = service.read_worker_global_config(worker_id)
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
    if tab_key == "communications":
        return "Messaging integrations for Discord and Teams, including routing, read reactions, and wake prompts."
    if tab_key == "access_policy":
        return (
            "Directory-level guardrails for approvals, recipients, mailboxes, "
            "communication files, and audit redaction defaults."
        )
    if tab_key == "contacts":
        return "Directory contacts available to host-side communication integrations."
    if tab_key == "vm_runtime":
        return "VM network and relay endpoint settings used when launching and wiring guest runtime access."
    if tab_key == "automation":
        return (
            "Role and capacity controls that govern automated issue intake and worker assignment."
        )
    if tab_key == "issue_setup":
        return "Repository bootstrap and post-execution defaults applied during issue VM setup."
    if tab_key == "ado_worker":
        return "ADO ingestion and VM bootstrap behavior for directory-scoped issue provisioning."
    if tab_key == "pr_template":
        return "Default markdown scaffold prepended to automated pull request descriptions."
    return None


def _directory_config_field_metadata(
    tab_key: str, source_domain: str, field_name: str, prefixes: tuple[str, ...]
) -> dict[str, Any]:
    kind, options = _field_kind(
        field_name,
        model_sets=(
            GatewayConfig.model_fields,
            AdoWorkerGlobalConfig.model_fields,
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
    label = (
        _ASSISTANT_SETTINGS_FIELD_LABELS.get(field_name)
        if source_domain == _ASSISTANT_SETTINGS_DOMAIN
        else None
    ) or _config_field_display_name(field_name, prefixes=prefixes)
    return {
        "source_domain": source_domain,
        "field_name": field_name,
        "label": label,
        "kind": kind,
        "options": list(options),
        "scope": "global" if source_domain == _ASSISTANT_SETTINGS_DOMAIN else "directory",
        "root_starts_editable": tab_key == "pr_template" and field_name == "pr_template_markdown",
        "tooltip": _directory_config_field_tooltip(
            source_domain, field_name, label=label, kind=kind, options=options
        ),
    }


def _directory_tab_metadata(tab_key: str) -> dict[str, Any]:
    sections = []
    for title, field_sources, prefixes in _DIRECTORY_CONFIG_TAB_SECTIONS.get(tab_key, ()):
        sections.append(
            {
                "title": title,
                "fields": [
                    _directory_config_field_metadata(tab_key, source_domain, field_name, prefixes)
                    for source_domain, field_name in field_sources
                ],
            }
        )
    if not sections:
        sections = [
            {
                "title": _DIRECTORY_CONFIG_UI_TAB_TITLES.get(tab_key, tab_key),
                "fields": [
                    _directory_config_field_metadata(tab_key, source_domain, field_name, ())
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


def _domain_snapshot(service: AdminService, directory_path: str, domain: str) -> dict[str, Any]:
    raw_result = service.read_directory_domain_config(directory_path, domain)
    effective_result = service.compute_directory_effective_config(directory_path, domain)
    parent_directory_path = _parent_directory_path(directory_path)
    parent_result = (
        service.compute_directory_effective_config(parent_directory_path, domain)
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


def _assistant_settings_snapshot(service: AdminService) -> dict[str, Any]:
    path = service.assistant_settings_path()
    config = service.read_assistant_settings()
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
    service: AdminService,
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
    return {
        "key": tab_key,
        "title": _DIRECTORY_CONFIG_UI_TAB_TITLES.get(tab_key, tab_key),
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
    for domain in DIRECTORY_CONFIG_DOMAIN_FILENAMES:
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


def _split_tab_payload(tab_key: str, config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    field_sources = {
        field_name: source_domain
        for source_domain, field_name in _DIRECTORY_CONFIG_UI_TAB_FIELD_SOURCES[tab_key]
    }
    payloads_by_source: dict[str, dict[str, Any]] = {}
    if not config:
        payloads_by_source.update(
            {
                source_domain: {}
                for source_domain in _directory_tab_underlying_domains(tab_key)
                if source_domain != _ASSISTANT_SETTINGS_DOMAIN
            }
        )
    for field_name, value in config.items():
        source_domain = field_sources.get(field_name)
        if source_domain is None:
            raise ValueError(f"Unknown field for {tab_key}: {field_name}")
        payloads_by_source.setdefault(source_domain, {})[field_name] = value
    return payloads_by_source


def _tab_fields_by_source_domain(tab_key: str) -> dict[str, set[str]]:
    fields_by_source: dict[str, set[str]] = {}
    for source_domain, field_name in _DIRECTORY_CONFIG_UI_TAB_FIELD_SOURCES[tab_key]:
        fields_by_source.setdefault(source_domain, set()).add(field_name)
    return fields_by_source


def worker_config_routes(service: AdminService, auth: AdminWebAuth) -> list[BaseRoute]:
    async def list_workers(_: Request) -> JSONResponse:
        workers = [
            _normalize_worker_summary(summary) for summary in service.list_background_workers()
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
            text = service.get_background_worker_log_snapshot(worker_id, lines=lines)
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
        try:
            service.write_worker_global_config(worker_id, config)
            detail = _worker_detail(service, worker_id)
        except ValueError as exc:
            message = str(exc)
            status_code = 404 if message.startswith("Unknown worker:") else 400
            return _error_response(message, status_code=status_code)
        return JSONResponse(detail)

    async def worker_action(request: Request) -> JSONResponse:
        worker_id = str(request.path_params.get("worker_id") or "").strip()
        action = str(request.path_params.get("action") or "").strip()
        if action == "start":
            result = service.start_background_worker(worker_id)
        elif action == "stop":
            result = service.stop_background_worker(worker_id)
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
        tree_result = service.list_directory_tree()
        directory_result = service.get_directory(directory_path)
        selected_path = (
            directory_result.data.details.directory.path
            if directory_result.ok and directory_result.data is not None
            else "root"
        )
        domain_snapshots = {
            domain: _domain_snapshot(service, selected_path, domain)
            for domain in DIRECTORY_CONFIG_DOMAIN_FILENAMES
        }
        domain_snapshots[_ASSISTANT_SETTINGS_DOMAIN] = _assistant_settings_snapshot(service)
        tabs = [
            _directory_tab_metadata(tab_key) for tab_key in _DIRECTORY_CONFIG_UI_TAB_FIELD_SOURCES
        ]
        tab_snapshots = {
            tab_key: _aggregate_tab_snapshot(service, selected_path, tab_key, domain_snapshots)
            for tab_key in _DIRECTORY_CONFIG_UI_TAB_FIELD_SOURCES
        }
        payload = {
            "directory_scoped_config_enabled": service.directory_scoped_config_enabled,
            "selected_directory_path": selected_path,
            "execution_backend": service.effective_execution_backend(selected_path),
            "selected_directory": {
                "path": selected_path,
                "parent_path": _parent_directory_path(selected_path),
                "can_create": bool(service.directory_scoped_config_enabled),
                "can_delete": bool(
                    service.directory_scoped_config_enabled and selected_path != "root"
                ),
            },
            "tree": _normalize_admin_result(tree_result),
            "directory": _normalize_admin_result(directory_result),
            "domains": list(DIRECTORY_CONFIG_DOMAIN_FILENAMES.keys()),
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

    async def create_directory(request: Request) -> JSONResponse:
        try:
            payload = await _json_body(request)
        except ValueError as exc:
            return _error_response(str(exc), status_code=400)
        parent_path = str(payload.get("parent_path") or "root").strip() or "root"
        name = str(payload.get("name") or "").strip()
        if not name:
            return _error_response("name is required", status_code=400)
        result = service.create_directory_child(parent_path, name)
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

        if domain in _DIRECTORY_CONFIG_UI_TAB_FIELD_SOURCES:
            payloads_by_source = _split_tab_payload(domain, config)
            tab_fields_by_source = _tab_fields_by_source_domain(domain)
            for source_domain, source_payload in payloads_by_source.items():
                if source_domain == _ASSISTANT_SETTINGS_DOMAIN:
                    if not request_has_privileged_operations(
                        auth=auth,
                        service=service,
                        request=request,
                    ):
                        return _error_response("Privileged operations are locked.", status_code=403)
                    existing_settings = service.read_assistant_settings()
                    merged_settings = dict(existing_settings)
                    merged_settings.update(source_payload)
                    try:
                        service.write_assistant_settings(merged_settings)
                    except Exception as exc:
                        return _error_response(str(exc), status_code=400)
                    continue
                existing_result = service.read_directory_domain_config(
                    directory_path, source_domain
                )
                existing_config = (
                    dict(existing_result.data.config)
                    if existing_result.ok
                    and existing_result.data is not None
                    and existing_result.data.config is not None
                    else {}
                )
                merged_payload = {
                    field_name: value
                    for field_name, value in existing_config.items()
                    if field_name not in tab_fields_by_source[source_domain]
                }
                merged_payload.update(source_payload)
                result = service.write_directory_domain_config(
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
            domain_snapshots = {
                source_domain: _domain_snapshot(service, directory_path, source_domain)
                for source_domain in DIRECTORY_CONFIG_DOMAIN_FILENAMES
            }
            domain_snapshots[_ASSISTANT_SETTINGS_DOMAIN] = _assistant_settings_snapshot(service)
            return JSONResponse(
                {
                    "write_result": {
                        "ok": True,
                        "data": None,
                        "errors": [],
                        "validation_diagnostics": [],
                    },
                    "tab_snapshot": _aggregate_tab_snapshot(
                        service, directory_path, domain, domain_snapshots
                    ),
                }
            )

        result = service.write_directory_domain_config(directory_path, domain, config)
        status_code = 200 if result.ok else 400
        response_payload = {
            "write_result": _normalize_admin_result(result),
            "snapshot": _domain_snapshot(service, directory_path, domain) if result.ok else None,
        }
        return JSONResponse(response_payload, status_code=status_code)

    routes = [
        Route("/api/workers", list_workers, methods=["GET"]),
        Route("/api/workers/{worker_id:str}", get_worker, methods=["GET"]),
        Route("/api/workers/{worker_id:str}/log", get_worker_log, methods=["GET"]),
        Route("/api/workers/{worker_id:str}/config", write_worker_config, methods=["PUT"]),
        Route("/api/workers/{worker_id:str}/actions/{action:str}", worker_action, methods=["POST"]),
        Route("/api/configuration/state", configuration_state, methods=["GET"]),
        Route("/api/configuration/directories", create_directory, methods=["POST"]),
        Route("/api/configuration/directories", delete_directory, methods=["DELETE"]),
        Route("/api/configuration/domains/{domain:str}", write_directory_domain, methods=["PUT"]),
    ]
    return routes


__all__ = ["worker_config_routes"]

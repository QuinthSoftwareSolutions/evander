from __future__ import annotations

import json
import os
import shutil
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from evander.azure_devops import ado_organization_from_legacy_url
from evander.config import GatewayConfig
from evander.connectors.models import (
    ConnectorDirectoryConfig,
    validate_connector_directory_overlay,
)
from evander.directories import DirectoryService, DirectoryStore
from evander.host_config import HostConfig, build_runtime_gateway_config
from evander.host_directories.models import (
    HostDirectoryDirectoryConfig,
    validate_host_directory_directory_overlay,
)
from evander.models import DirectoryInfo, DirectoryPath
from evander.vm.templates import (
    DEFAULT_LIBVIRT_VM_DEFAULT_TEMPLATE,
    DEFAULT_LIBVIRT_VM_TEMPLATES,
)

DIRECTORY_CONFIG_OVERLAYS_KEY = "config_overlays"
DIRECTORY_CONFIG_METADATA_KEY = "directory_config"
DIRECTORY_CONFIG_METADATA_DOMAINS_KEY = "domains"
DIRECTORY_CONFIG_ROOT_DIRNAME = "directory-config"

DIRECTORY_CONFIG_DOMAIN_FILENAMES = {
    "policy": "policy.json",
    "providers": "providers.json",
    "contacts": "contacts.json",
    "email": "email.json",
    "wake": "wake.json",
    "ado_worker": "ado-worker.json",
    "vm": "vm.json",
    "issue_setup": "issue-setup.json",
    "pr_template": "pr-template.json",
    "automation": "automation.json",
    "connectors": "connectors.json",
    "host_directories": "host-directories.json",
}

LEGACY_DIRECTORY_CONFIG_DOMAIN_FILENAMES = {
    "policy": "policy.json",
    "providers": "providers.json",
    "workers": "workers.json",
    "vm-defaults": "vm-defaults.json",
}

LEGACY_DIRECTORY_DOMAIN_ALIASES = {
    "vm-defaults": "vm",
    "ado-worker": "ado_worker",
    "issue-setup": "issue_setup",
}

LEGACY_ADO_WORKER_BOOTSTRAP_FIELDS = frozenset(
    {
        "ado_project_or_solution",
        "ado_bootstrap_mssql",
        "ado_mssql_sa_password_env",
        "create_prs_automatically",
        "create_prs_as_draft",
    }
)

DIRECTORY_CONFIG_DOMAIN_FIELDS: dict[str, frozenset[str]] = {
    "policy": frozenset(
        {
            "approval_ttl_seconds",
            "approval_notification_target_ids",
            "default_session_ttl_seconds",
            "discord_operator_channel_allowlist",
            "discord_operator_user_allowlist",
            "discord_poll_operator_dms",
            "email_draft_domain_allowlist",
            "email_draft_recipient_allowlist",
            "email_folder_allowlist_by_mailbox",
            "email_mailbox_allowlist",
            "email_send_enabled",
            "communication_file_receive_enabled",
            "communication_file_send_enabled",
            "communication_file_max_bytes",
            "communication_file_allowed_mime_types",
            "communication_file_allowed_extensions",
            "communication_file_blocked_extensions",
            "communication_file_require_approval_for_send",
            "pr_reply_require_approval",
            "approval_required_tools",
            "mcp_tool_policies",
            "tool_argument_policies",
            "redacted_keys",
        }
    ),
    "providers": frozenset(
        {
            "ado_organization",
            "azure_devops_organization_url",
            "azure_devops_pat_env",
            "azure_devops_pr_pat_env",
            "azure_devops_project",
            "container_worker_base_url",
            "container_worker_default_image",
            "container_worker_images",
            "container_worker_job_timeout_seconds",
            "container_worker_request_timeout_seconds",
            "container_worker_token_env",
            "container_worker_workspace_persistence",
            "container_worker_codex_auth_mode",
            "container_worker_git_allowed_from",
            "managed_git_commit_name",
            "managed_git_commit_email",
            "managed_git_operation_timeout_seconds",
            "managed_git_worktree_stale_seconds",
            "container_worker_guest_openai_base_url",
            "container_worker_guest_anthropic_base_url",
            "container_worker_guest_gateway_url",
            "container_worker_guest_http_target_relay_base_url",
            "container_worker_guest_nuget_relay_base_url",
            "container_worker_guest_pypi_index_url",
            "container_worker_guest_npm_registry_url",
            "container_worker_mssql_enabled",
            "container_worker_mssql_host",
            "container_worker_mssql_port",
            "execution_backend",
            "discord_api_base_url",
            "discord_backend",
            "discord_bot_token_env",
            "discord_poll_limit",
            "discord_retry_base_backoff_seconds",
            "discord_retry_count",
            "discord_retry_max_backoff_seconds",
            "discord_timeout_seconds",
            "discord_read_reaction_emoji",
            "github_token_env",
            "agent_prompt_token_secret_env",
            "agent_prompt_token_ttl_seconds",
            "agent_prompt_queue_max_retries",
            "agent_context_retry_enabled",
            "agent_context_retry_max_attempts",
            "agent_context_retry_target_chars",
            "agent_consultation_profiles",
            "agent_consultation_default_profile",
            "agent_consultation_max_wait_seconds",
            "agent_consultation_retention_days",
            "agent_consultation_token_secret_env",
            "communication_artifact_link_secret_env",
            "communication_artifact_link_ttl_seconds",
            "communication_artifact_public_base_url",
            "nuget_azure_feed_base_url",
            "nuget_azure_pat_env",
            "nuget_public_feed_base_url",
            "nuget_relay_timeout_seconds",
            "npm_audit_facade_audit_registry_url",
            "npm_audit_facade_package_registry_url",
            "npm_audit_facade_timeout_seconds",
            "http_target_relay_timeout_seconds",
            "openai_api_base_url",
            "openai_api_key_env",
            "model_provider_mode",
            "local_openai_api_base_url",
            "local_openai_api_key",
            "local_openai_provider_name",
            "local_openai_max_concurrency",
            "azure_openai_endpoint",
            "azure_openai_auth_mode",
            "azure_openai_api_key_env",
            "azure_openai_api_version",
            "azure_openai_model_deployments",
            "openai_relay_timeout_seconds",
            "anthropic_api_base_url",
            "anthropic_api_key_env",
            "anthropic_api_version",
            "anthropic_relay_timeout_seconds",
            "qdrant_api_key_env",
            "qdrant_base_url",
            "retell_api_base_url",
            "retell_api_key_env",
            "retell_default_agent_id",
            "retell_from_number",
            "retell_request_timeout_seconds",
            "phone_call_backend",
            "openai_realtime_model",
            "openai_realtime_api_key_env",
            "openai_realtime_api_base_url",
            "voice_media_controller_url",
            "voice_media_controller_token_env",
            "voice_media_controller_request_timeout_seconds",
            "didww_sip_bridge_url",
            "didww_sip_bridge_token_env",
            "didww_from_number",
            "inbound_phone_routes",
            "semantic_embedding_dimensions",
            "semantic_embedding_model",
            "semantic_embedding_provider",
            "semantic_store_provider",
            "semantic_vector_distance",
            "teams_backend",
            "teams_delegated_authority_base_url",
            "teams_delegated_client_id",
            "teams_delegated_credential_store_path",
            "teams_delegated_identity_id",
            "teams_delegated_scopes",
            "teams_delegated_tenant_id",
            "teams_graph_access_token_env",
            "teams_graph_api_base_url",
            "teams_history_deadline_seconds",
            "teams_history_page_cap",
            "teams_poll_limit",
            "teams_retry_base_backoff_seconds",
            "teams_retry_count",
            "teams_retry_max_backoff_seconds",
            "teams_timeout_seconds",
            "teams_read_reaction_emoji",
            "provider_state",
        }
    ),
    "contacts": frozenset(
        {
            "contacts",
            "user_contact_links",
        }
    ),
    "email": frozenset(
        {
            "email_mailboxes",
            "email_provider_timeout_seconds",
        }
    ),
    "wake": frozenset(
        {
            "pr_feedback_resume_prompt",
            "discord_resume_prompt",
        }
    ),
    "vm": frozenset(
        {
            "provider",
            "provider_defaults",
            "azure",
            "bind_network_cidr",
            "libvirt_vm_default_template",
            "libvirt_vm_templates",
            "gateway_url",
            "http_target_relay_host",
            "http_target_relay_port",
            "nuget_relay_host",
            "nuget_relay_port",
            "npm_audit_facade_host",
            "npm_audit_facade_port",
            "openai_relay_host",
            "openai_relay_port",
        }
    ),
    "ado_worker": frozenset(
        {
            "ado_area_filters",
            "ado_team_filters",
            "ado_repo_source",
        }
    ),
    "issue_setup": frozenset(
        {
            "git_host",
            "git_user",
            "git_ssh_alias",
            "default_model",
            "post_exec_verification_prompt",
            "enable_guest_heartbeat",
            "guest_heartbeat_prompt",
            "issue_setup_azure_devops_pr_pat_env",
        }
    ),
    "automation": frozenset(
        {
            "approved_issue_cap",
            "originator_roles",
            "prioritizer_roles",
            "worker_roles",
        }
    ),
    "pr_template": frozenset({"pr_template_markdown"}),
    "connectors": frozenset({"auth_profiles", "connectors"}),
    "host_directories": frozenset({"host_directories"}),
}


class AdoWorkerDirectoryConfig(BaseModel):
    ado_area_filters: list[str] = Field(default_factory=list)
    ado_team_filters: list[str] = Field(default_factory=list)
    ado_repo_source: str | None = None


class IssueSetupDirectoryConfig(BaseModel):
    git_host: str | None = None
    git_user: str | None = None
    git_ssh_alias: str | None = None
    default_model: str | None = None
    post_exec_verification_prompt: str | None = None
    enable_guest_heartbeat: bool | None = None
    guest_heartbeat_prompt: str | None = None
    issue_setup_azure_devops_pr_pat_env: str | None = None


class EffectiveIssueSetupDirectoryConfig(BaseModel):
    git_host: str
    git_user: str
    git_ssh_alias: str
    default_model: str | None = None
    post_exec_verification_prompt: str | None = None
    enable_guest_heartbeat: bool
    guest_heartbeat_prompt: str
    issue_setup_azure_devops_pr_pat_env: str | None = None


class PrTemplateDirectoryConfig(BaseModel):
    pr_template_markdown: str = ""


class AutomationDirectoryConfig(BaseModel):
    approved_issue_cap: int = Field(default=10, ge=0)
    originator_roles: list[str] = Field(default_factory=list)
    prioritizer_roles: list[str] = Field(default_factory=list)
    worker_roles: list[str] = Field(default_factory=list)

    @field_validator("originator_roles", "prioritizer_roles", "worker_roles", mode="before")
    @classmethod
    def normalize_role_lists(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, (list, tuple)):
            raise ValueError("role lists must be JSON arrays")
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            role = str(item).strip()
            if not role:
                raise ValueError("role names cannot be empty")
            if role in seen:
                raise ValueError(f"duplicate role name: {role!r}")
            seen.add(role)
            normalized.append(role)
        return normalized


class EffectiveAutomationDirectoryConfig(BaseModel):
    approved_issue_cap: int = Field(default=10, ge=0)
    originator_roles: list[str] = Field(default_factory=list)
    prioritizer_roles: list[str] = Field(default_factory=list)
    worker_roles: list[str] = Field(default_factory=list)


class DirectoryContactPhoneNumber(BaseModel):
    label: str = Field(min_length=1)
    number: str = Field(min_length=1)
    primary: bool = False

    @field_validator("label", "number", mode="before")
    @classmethod
    def normalize_required_text(cls, value: object) -> object:
        text = str(value or "").strip()
        if not text:
            return None
        return text


class DirectoryContactEntry(BaseModel):
    name: str = Field(min_length=1)
    description: str = ""
    email_addresses: list[str] = Field(default_factory=list)
    phone_numbers: list[DirectoryContactPhoneNumber] = Field(default_factory=list)

    @field_validator("name", "description", mode="before")
    @classmethod
    def normalize_text(cls, value: object) -> object:
        if value is None:
            return ""
        return str(value).strip()

    @field_validator("email_addresses", mode="before")
    @classmethod
    def normalize_email_addresses(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, (list, tuple)):
            raise ValueError("email_addresses must be a JSON array of strings")
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            address = str(item or "").strip()
            if not address:
                raise ValueError("email_addresses cannot contain empty values")
            key = address.lower()
            if key in seen:
                raise ValueError(f"duplicate email address: {address!r}")
            seen.add(key)
            normalized.append(address)
        return normalized

    @model_validator(mode="after")
    def validate_phone_numbers(self) -> "DirectoryContactEntry":
        primary_count = sum(1 for phone in self.phone_numbers if phone.primary)
        if primary_count > 1:
            raise ValueError("contact phone_numbers can contain at most one primary number")
        seen_labels: set[str] = set()
        for phone in self.phone_numbers:
            key = phone.label.casefold()
            if key in seen_labels:
                raise ValueError(f"duplicate phone number label: {phone.label!r}")
            seen_labels.add(key)
        return self


class ContactDirectoryConfig(BaseModel):
    contacts: dict[str, DirectoryContactEntry] = Field(default_factory=dict)
    user_contact_links: dict[str, str | None] = Field(default_factory=dict)

    @field_validator("contacts", mode="before")
    @classmethod
    def normalize_contacts(cls, value: object) -> object:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("contacts must be a JSON object")
        normalized: dict[str, object] = {}
        seen: set[str] = set()
        for raw_contact_id, contact in value.items():
            contact_id = str(raw_contact_id or "").strip()
            if not contact_id:
                raise ValueError("contact ids must be non-empty")
            if contact_id in seen:
                raise ValueError(f"duplicate contact id: {contact_id!r}")
            seen.add(contact_id)
            normalized[contact_id] = contact
        return normalized

    @field_validator("user_contact_links", mode="before")
    @classmethod
    def normalize_user_contact_links(cls, value: object) -> object:
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("user_contact_links must be a JSON object")
        normalized: dict[str, str | None] = {}
        for raw_login, raw_contact_id in value.items():
            login = str(raw_login or "").strip().lower()
            if not login:
                raise ValueError("user_contact_links logins must be non-empty")
            if raw_contact_id is None:
                normalized[login] = None
                continue
            contact_id = str(raw_contact_id or "").strip()
            if not contact_id:
                raise ValueError("user_contact_links contact ids must be non-empty")
            normalized[login] = contact_id
        return normalized


class EffectiveContactDirectoryConfig(ContactDirectoryConfig):
    @model_validator(mode="after")
    def validate_user_links(self) -> "EffectiveContactDirectoryConfig":
        unknown_contacts = sorted(
            {
                contact_id
                for contact_id in self.user_contact_links.values()
                if contact_id is not None and contact_id not in self.contacts
            }
        )
        if unknown_contacts:
            raise ValueError(
                "user_contact_links reference unknown contacts: " + ", ".join(unknown_contacts)
            )
        return self


LEGACY_DOMAIN_FIELD_SUBSETS: dict[str, dict[str, frozenset[str]]] = {
    "providers": {
        "providers": DIRECTORY_CONFIG_DOMAIN_FIELDS["providers"],
        "email": DIRECTORY_CONFIG_DOMAIN_FIELDS["email"],
    },
    "vm-defaults": {
        "vm": DIRECTORY_CONFIG_DOMAIN_FIELDS["vm"],
    },
}


def get_domain_base_config(config: GatewayConfig, domain: str) -> dict[str, Any]:
    if domain == "gateway":
        return config.model_dump(mode="json")
    DirectoryConfigStore._validate_domain_name(domain)
    payload = config.model_dump(mode="json")
    allowed_fields = DIRECTORY_CONFIG_DOMAIN_FIELDS[domain]
    if domain == "automation":
        return {
            "approved_issue_cap": 10,
            "originator_roles": [],
            "prioritizer_roles": [],
            "worker_roles": [],
        }
    if domain == "issue_setup":
        return {
            "git_host": os.environ.get("EVANDER_GIT_HOST", "").strip() or "192.168.250.1",
            "git_user": os.environ.get("EVANDER_GIT_USER", "").strip()
            or os.environ.get("USER", "agent"),
            "git_ssh_alias": os.environ.get("EVANDER_GIT_SSH_ALIAS", "").strip()
            or "evander-git-host",
            "default_model": os.environ.get("AGENT_DEFAULT_MODEL", "").strip() or None,
            "enable_guest_heartbeat": (
                os.environ.get("ENABLE_GUEST_HEARTBEAT", "").strip().lower()
                in {"1", "true", "yes", "on"}
            ),
            "guest_heartbeat_prompt": (
                os.environ.get("GUEST_HEARTBEAT_PROMPT", "").strip()
                or "Resume work for this session. Check for pending feedback first, then continue the most recent task."
            ),
            "post_exec_verification_prompt": os.environ.get(
                "AGENT_POST_EXEC_VERIFICATION_PROMPT", ""
            ).strip()
            or None,
            "issue_setup_azure_devops_pr_pat_env": (
                os.environ.get("EVANDER_AZURE_DEVOPS_PR_PAT_ENV", "").strip()
                or "AZURE_DEVOPS_PR_PAT"
            ),
        }
    if domain == "vm":
        return {
            "bind_network_cidr": str(config.bind_network_cidr),
            "libvirt_vm_default_template": DEFAULT_LIBVIRT_VM_DEFAULT_TEMPLATE,
            "libvirt_vm_templates": dict(DEFAULT_LIBVIRT_VM_TEMPLATES),
            "gateway_url": config.gateway_url,
            "http_target_relay_host": config.http_target_relay_host,
            "http_target_relay_port": config.http_target_relay_port,
            "nuget_relay_host": config.nuget_relay_host,
            "nuget_relay_port": config.nuget_relay_port,
            "npm_audit_facade_host": config.npm_audit_facade_host,
            "npm_audit_facade_port": config.npm_audit_facade_port,
            "openai_relay_host": config.openai_relay_host,
            "openai_relay_port": config.openai_relay_port,
        }
    if domain == "contacts":
        return {"contacts": {}, "user_contact_links": {}}
    if domain == "connectors":
        return {"auth_profiles": {}, "connectors": {}}
    if domain == "host_directories":
        return {"host_directories": {}}
    if domain == "pr_template":
        return {"pr_template_markdown": ""}
    return {key: payload[key] for key in sorted(allowed_fields) if key in payload}


def validate_effective_domain_payload(domain: str, payload: dict[str, Any]) -> dict[str, Any]:
    if domain == "gateway":
        validated = GatewayConfig.model_validate(payload)
        return validated.model_dump(mode="json")
    if domain == "providers":
        return DirectoryConfigStore._validate_provider_domain_payload(
            payload,
            validation_scope="effective",
        )
    if domain == "automation":
        validated = EffectiveAutomationDirectoryConfig.model_validate(payload)
        return validated.model_dump(mode="json")
    if domain == "issue_setup":
        validated = EffectiveIssueSetupDirectoryConfig.model_validate(payload)
        return validated.model_dump(mode="json")
    if domain == "vm":
        from evander.vm.defaults_config import EffectiveVmDefaultsConfig

        validated = EffectiveVmDefaultsConfig.model_validate(payload)
        return validated.model_dump(mode="json")
    if domain == "contacts":
        validated = EffectiveContactDirectoryConfig.model_validate(payload)
        return validated.model_dump(mode="json")
    if domain == "connectors":
        validated = ConnectorDirectoryConfig.model_validate(payload)
        return validated.model_dump(mode="json")
    if domain == "host_directories":
        validated = HostDirectoryDirectoryConfig.model_validate(payload)
        return validated.model_dump(mode="json")
    if domain == "pr_template":
        validated = PrTemplateDirectoryConfig.model_validate(payload)
        return validated.model_dump(mode="json")
    return DirectoryConfigStore.validate_domain_payload(domain, payload)


class DirectoryConfigValueSource(BaseModel):
    source_type: str
    directory_id: str | None = None
    directory_path: str | None = None


class DirectoryConfigLayer(BaseModel):
    directory_id: str
    directory_path: str
    domain: str
    overlay: dict[str, Any]


class DirectoryConfigResolution(BaseModel):
    domain: str
    selected_directory: DirectoryInfo
    effective_config: dict[str, Any]
    provenance: dict[str, DirectoryConfigValueSource] = Field(default_factory=dict)
    applied_layers: list[DirectoryConfigLayer] = Field(default_factory=list)
    validation_errors: list[str] = Field(default_factory=list)


class DirectoryConfigStore:
    def __init__(self, directory_store: DirectoryStore, *, runtime_dir: Path) -> None:
        self._directory_store = directory_store
        self._service = DirectoryService(directory_store)
        self._root_path = runtime_dir / DIRECTORY_CONFIG_ROOT_DIRNAME

    @property
    def root_path(self) -> Path:
        return self._root_path

    def ensure_root(self) -> None:
        self._root_path.mkdir(parents=True, exist_ok=True)

    def list_domains(self, reference: str) -> list[str]:
        directory = self._service.show(reference).directory
        return self._domains_for_directory_info(directory, root_path=self._root_path)

    def read(self, reference: str, *, domain: str) -> dict[str, Any] | None:
        domain = self._normalize_domain_name(domain)
        directory = self._service.show(reference).directory
        if domain not in self._domains_for_directory_info(directory, root_path=self._root_path):
            return None
        path = self._domain_file_path(directory.path, domain=domain)
        if path.exists():
            payload = json.loads(path.read_text(encoding="utf-8"))
            return self.validate_domain_payload(domain, payload)
        legacy_payload = self._legacy_domain_payload(directory.path, domain=domain)
        if legacy_payload is not None:
            return self.validate_domain_payload(domain, legacy_payload)
        raise ValueError(
            f"directory config file is missing for {directory.path!r} domain {domain!r}"
        )

    def write(self, reference: str, *, domain: str, payload: dict[str, Any]) -> dict[str, Any]:
        original_domain = domain
        domain = self._normalize_domain_name(domain)
        directory = None
        if original_domain == "providers" and isinstance(payload, dict):
            payload = self._canonicalize_provider_domain_payload(payload)
            provider_payload = {
                key: value
                for key, value in payload.items()
                if key in DIRECTORY_CONFIG_DOMAIN_FIELDS["providers"]
            }
            email_payload = {
                key: value
                for key, value in payload.items()
                if key in DIRECTORY_CONFIG_DOMAIN_FIELDS["email"]
            }
            unknown_fields = sorted(
                set(payload)
                - DIRECTORY_CONFIG_DOMAIN_FIELDS["providers"]
                - DIRECTORY_CONFIG_DOMAIN_FIELDS["email"]
            )
            if unknown_fields:
                raise ValueError(
                    f"directory config domain 'providers' does not allow fields: {', '.join(unknown_fields)}"
                )
            normalized_provider_payload = self.validate_domain_payload(
                "providers", provider_payload
            )
            directory = self._service.show(reference).directory
            if (
                directory.path != "root"
                and "local_openai_max_concurrency" in normalized_provider_payload
            ):
                raise ValueError(
                    "local_openai_max_concurrency is host-global and may be configured only "
                    "in root providers.json"
                )
            if email_payload:
                self.validate_domain_payload("email", email_payload)
                self.write(reference, domain="email", payload=email_payload)
            payload = provider_payload
        normalized = self.validate_domain_payload(domain, payload)
        if directory is None:
            directory = self._service.show(reference).directory
        if (
            domain == "providers"
            and directory.path != "root"
            and "local_openai_max_concurrency" in normalized
        ):
            raise ValueError(
                "local_openai_max_concurrency is host-global and may be configured only "
                "in root providers.json"
            )
        path = self._domain_file_path(directory.path, domain=domain)
        if not normalized:
            if path.exists():
                path.unlink()
            legacy_path = self._legacy_domain_file_path(directory.path, domain=domain)
            if legacy_path is not None and legacy_path.exists():
                legacy_path.unlink()
            self._prune_empty_parent_dirs(path.parent)
            self._remove_domain(directory.id, domain=domain)
            return normalized
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(normalized, indent=2, sort_keys=True), encoding="utf-8")
        self._record_domain(directory.id, domain=domain)
        return normalized

    def delete_directory_tree(self, canonical_path: str) -> None:
        path = self._root_path.joinpath(*DirectoryPath(segments=canonical_path).segments)
        if path.exists():
            shutil.rmtree(path)

    @classmethod
    def validate_domain_payload(cls, domain: str, payload: dict[str, Any]) -> dict[str, Any]:
        domain = cls._normalize_domain_name(domain)
        if not isinstance(payload, dict):
            raise ValueError(
                f"directory config payload for domain {domain!r} must be a JSON object"
            )
        if domain == "ado_worker":
            # These retired settings may remain in pre-directory-config ado-worker
            # files. They no longer influence worker behavior, but retaining them must
            # not prevent the supported worker settings from loading.
            payload = {
                key: value
                for key, value in payload.items()
                if key not in LEGACY_ADO_WORKER_BOOTSTRAP_FIELDS
            }
        allowed_fields = DIRECTORY_CONFIG_DOMAIN_FIELDS[domain]
        if domain != "providers":
            unknown_fields = sorted(set(payload) - allowed_fields)
            if unknown_fields:
                raise ValueError(
                    f"directory config domain {domain!r} does not allow fields: {', '.join(unknown_fields)}"
                )
        if domain == "wake":
            validated = GatewayConfig.model_validate(payload)
            return validated.model_dump(mode="json", include=set(payload))
        if domain == "providers":
            return cls._validate_provider_domain_payload(
                payload,
                validation_scope="overlay",
            )
        if domain == "ado_worker":
            validated = AdoWorkerDirectoryConfig.model_validate(payload)
            return validated.model_dump(mode="json", include=set(payload))
        if domain == "vm":
            from evander.vm.defaults_config import EffectiveVmDefaultsConfig

            validated = EffectiveVmDefaultsConfig.model_validate(payload)
            return validated.model_dump(mode="json", include=set(payload))
        if domain == "contacts":
            validated = ContactDirectoryConfig.model_validate(payload)
            return validated.model_dump(mode="json", include=set(payload))
        if domain == "connectors":
            return validate_connector_directory_overlay(payload)
        if domain == "host_directories":
            return validate_host_directory_directory_overlay(payload)
        if domain == "issue_setup":
            validated = IssueSetupDirectoryConfig.model_validate(payload)
            return validated.model_dump(mode="json", include=set(payload))
        if domain == "automation":
            validated = AutomationDirectoryConfig.model_validate(payload)
            return validated.model_dump(mode="json", include=set(payload))
        if domain == "pr_template":
            validated = PrTemplateDirectoryConfig.model_validate(payload)
            return validated.model_dump(mode="json", include=set(payload))
        unknown_fields = sorted(set(payload) - allowed_fields)
        if unknown_fields:
            raise ValueError(
                f"directory config domain {domain!r} does not allow fields: {', '.join(unknown_fields)}"
            )
        validated = GatewayConfig.model_validate(payload)
        return validated.model_dump(mode="json", include=set(payload))

    @staticmethod
    def _validate_provider_domain_payload(
        payload: dict[str, Any], *, validation_scope: str
    ) -> dict[str, Any]:
        payload = DirectoryConfigStore._canonicalize_provider_domain_payload(payload)
        allowed_fields = DIRECTORY_CONFIG_DOMAIN_FIELDS["providers"]
        compatible_fields = allowed_fields | DIRECTORY_CONFIG_DOMAIN_FIELDS["email"]
        legacy_fields = {"openai_costs_api_key_env"}
        unknown_fields = sorted(set(payload) - compatible_fields - legacy_fields)
        if unknown_fields:
            raise ValueError(
                "directory config domain 'providers' does not allow fields: "
                + ", ".join(unknown_fields)
            )
        provider_payload = {key: value for key, value in payload.items() if key in allowed_fields}
        validated = GatewayConfig.model_validate(
            provider_payload,
            context={
                "directory_config_domain": "providers",
                "validation_scope": validation_scope,
            },
        )
        return validated.model_dump(mode="json", include=set(provider_payload))

    @staticmethod
    def _canonicalize_provider_domain_payload(payload: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(payload)
        if "openai_provider_mode" in normalized:
            normalized.setdefault("model_provider_mode", normalized["openai_provider_mode"])
            normalized.pop("openai_provider_mode", None)
        if "azure_devops_organization_url" in normalized:
            legacy_organization = ado_organization_from_legacy_url(
                normalized["azure_devops_organization_url"]
            )
            normalized.setdefault("ado_organization", legacy_organization)
            normalized.pop("azure_devops_organization_url", None)
        return normalized

    @classmethod
    def _validate_domain_name(cls, domain: str) -> None:
        normalized_domain = cls._normalize_domain_name(domain)
        if normalized_domain not in DIRECTORY_CONFIG_DOMAIN_FILENAMES:
            raise ValueError(f"unsupported directory config domain: {domain!r}")

    @staticmethod
    def _normalize_domain_name(domain: str) -> str:
        return LEGACY_DIRECTORY_DOMAIN_ALIASES.get(domain, domain)

    def _domain_file_path(self, canonical_path: str, *, domain: str) -> Path:
        domain = self._normalize_domain_name(domain)
        self._validate_domain_name(domain)
        segments = DirectoryPath(segments=canonical_path).segments
        return self._root_path.joinpath(*segments, DIRECTORY_CONFIG_DOMAIN_FILENAMES[domain])

    def _legacy_domain_file_path(self, canonical_path: str, *, domain: str) -> Path | None:
        legacy_domain = None
        if domain == "vm":
            legacy_domain = "vm-defaults"
        elif domain in {"policy", "providers", "email"}:
            legacy_domain = "providers" if domain in {"providers", "email"} else "policy"
        if legacy_domain is None:
            return None
        segments = DirectoryPath(segments=canonical_path).segments
        return self._root_path.joinpath(
            *segments, LEGACY_DIRECTORY_CONFIG_DOMAIN_FILENAMES[legacy_domain]
        )

    def _legacy_domain_payload(self, canonical_path: str, *, domain: str) -> dict[str, Any] | None:
        legacy_path = self._legacy_domain_file_path(canonical_path, domain=domain)
        if legacy_path is None or not legacy_path.exists():
            return None
        payload = json.loads(legacy_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return None
        if domain == "policy":
            return payload
        if domain in {"providers", "email"}:
            source_domain = "providers"
        elif domain == "vm":
            source_domain = "vm-defaults"
        else:
            return None
        if source_domain == "providers":
            payload = self._canonicalize_provider_domain_payload(payload)
        allowed_fields = LEGACY_DOMAIN_FIELD_SUBSETS[source_domain][domain]
        extracted = {key: payload[key] for key in sorted(allowed_fields) if key in payload}
        return extracted or None

    def _record_domain(self, directory_id: str, *, domain: str) -> None:
        directory = self._directory_store.get(directory_id)
        if directory is None:
            raise ValueError(f"directory {directory_id!r} does not exist")
        metadata = deepcopy(directory.metadata)
        config_metadata = metadata.get(DIRECTORY_CONFIG_METADATA_KEY)
        if not isinstance(config_metadata, dict):
            config_metadata = {}
        raw_domains = config_metadata.get(DIRECTORY_CONFIG_METADATA_DOMAINS_KEY, [])
        domains = {
            str(value).strip()
            for value in raw_domains
            if str(value).strip() in DIRECTORY_CONFIG_DOMAIN_FILENAMES
        }
        domains.add(domain)
        config_metadata[DIRECTORY_CONFIG_METADATA_DOMAINS_KEY] = sorted(domains)
        metadata[DIRECTORY_CONFIG_METADATA_KEY] = config_metadata
        self._directory_store.put(directory.model_copy(update={"metadata": metadata}))

    def _remove_domain(self, directory_id: str, *, domain: str) -> None:
        directory = self._directory_store.get(directory_id)
        if directory is None:
            raise ValueError(f"directory {directory_id!r} does not exist")
        metadata = deepcopy(directory.metadata)
        config_metadata = metadata.get(DIRECTORY_CONFIG_METADATA_KEY)
        if not isinstance(config_metadata, dict):
            return
        raw_domains = config_metadata.get(DIRECTORY_CONFIG_METADATA_DOMAINS_KEY, [])
        domains = {
            str(value).strip()
            for value in raw_domains
            if str(value).strip() in DIRECTORY_CONFIG_DOMAIN_FILENAMES
        }
        domains.discard(domain)
        if domains:
            config_metadata[DIRECTORY_CONFIG_METADATA_DOMAINS_KEY] = sorted(domains)
            metadata[DIRECTORY_CONFIG_METADATA_KEY] = config_metadata
        else:
            metadata.pop(DIRECTORY_CONFIG_METADATA_KEY, None)
        self._directory_store.put(directory.model_copy(update={"metadata": metadata}))

    def _prune_empty_parent_dirs(self, path: Path) -> None:
        current = path
        while current != self._root_path and current.exists():
            try:
                current.rmdir()
            except OSError:
                break
            current = current.parent

    @staticmethod
    def _domains_for_directory_info(directory: DirectoryInfo, *, root_path: Path) -> list[str]:
        config_metadata = directory.metadata.get(DIRECTORY_CONFIG_METADATA_KEY)
        raw_domains = (
            config_metadata.get(DIRECTORY_CONFIG_METADATA_DOMAINS_KEY, [])
            if isinstance(config_metadata, dict)
            else []
        )
        normalized_domains: set[str] = set()
        if isinstance(raw_domains, list):
            for raw_domain in raw_domains:
                value = str(raw_domain).strip()
                if value in DIRECTORY_CONFIG_DOMAIN_FILENAMES:
                    normalized_domains.add(value)
                    continue
                alias = LEGACY_DIRECTORY_DOMAIN_ALIASES.get(value)
                if alias is not None:
                    normalized_domains.add(alias)
                    continue
                if value == "providers":
                    normalized_domains.add("providers")
                    legacy_path = root_path.joinpath(
                        *DirectoryPath(segments=directory.path).segments, "providers.json"
                    )
                    if legacy_path.exists():
                        try:
                            payload = json.loads(legacy_path.read_text(encoding="utf-8"))
                        except (OSError, json.JSONDecodeError):
                            payload = {}
                        if isinstance(payload, dict) and any(
                            field in payload for field in DIRECTORY_CONFIG_DOMAIN_FIELDS["email"]
                        ):
                            normalized_domains.add("email")
        automation_path = root_path.joinpath(
            *DirectoryPath(segments=directory.path).segments, "automation.json"
        )
        if automation_path.exists():
            normalized_domains.add("automation")
        return sorted(normalized_domains)


class DirectoryConfigResolver:
    def __init__(
        self,
        service: DirectoryService,
        *,
        base_gateway_config: GatewayConfig | None = None,
        config_store: DirectoryConfigStore | None = None,
    ) -> None:
        self._service = service
        self._base_gateway_config = base_gateway_config
        self._config_store = config_store

    def resolve_gateway(self, reference: str) -> DirectoryConfigResolution:
        return self.resolve_domain(
            reference,
            domain="gateway",
            base_config=(self._base_gateway_config or GatewayConfig()).model_dump(mode="json"),
            validator=GatewayConfig.model_validate,
        )

    def resolve_root_gateway_config(
        self,
        host_config: HostConfig,
        *,
        base_gateway_config: GatewayConfig | None = None,
    ) -> GatewayConfig:
        root_bootstrap = base_gateway_config or build_runtime_gateway_config(host_config)
        payload = root_bootstrap.model_dump(mode="json")
        for domain in ("policy", "providers", "email", "wake", "vm"):
            resolution = self.resolve_domain(
                "root",
                domain=domain,
                base_config=get_domain_base_config(root_bootstrap, domain),
                validator=lambda candidate, domain=domain: validate_effective_domain_payload(
                    domain, candidate
                ),
            )
            payload.update(resolution.effective_config)
        return GatewayConfig.model_validate(payload)

    def resolve_domain(
        self,
        reference: str,
        *,
        domain: str,
        base_config: dict[str, Any] | None = None,
        validator: Callable[[Any], Any] | None = None,
    ) -> DirectoryConfigResolution:
        details = self._service.show(reference)
        effective = deepcopy(base_config) if base_config is not None else {}
        provenance: dict[str, DirectoryConfigValueSource] = {}
        if base_config is not None:
            self._mark_provenance(
                effective,
                (),
                provenance,
                DirectoryConfigValueSource(source_type="base"),
            )

        applied_layers: list[DirectoryConfigLayer] = []
        for directory in details.ancestry:
            overlay = self._overlay_for_directory(directory, domain=domain)
            if overlay is None:
                continue
            if domain == "providers":
                overlay = DirectoryConfigStore._canonicalize_provider_domain_payload(overlay)
                if directory.path != "root" and "local_openai_max_concurrency" in overlay:
                    raise ValueError(
                        "local_openai_max_concurrency is host-global and may be configured only "
                        "in root providers.json"
                    )
            applied_layers.append(
                DirectoryConfigLayer(
                    directory_id=directory.id,
                    directory_path=directory.path,
                    domain=domain,
                    overlay=deepcopy(overlay),
                )
            )
            effective = self._merge_values(
                effective,
                overlay,
                (),
                provenance,
                DirectoryConfigValueSource(
                    source_type="directory",
                    directory_id=directory.id,
                    directory_path=directory.path,
                ),
            )

        validation_errors: list[str] = []
        if validator is not None:
            try:
                validator(effective)
            except ValidationError as exc:
                validation_errors = [error["msg"] for error in exc.errors()]
            except ValueError as exc:
                validation_errors = [str(exc)]

        return DirectoryConfigResolution(
            domain=domain,
            selected_directory=details.directory,
            effective_config=effective,
            provenance=dict(sorted(provenance.items())),
            applied_layers=applied_layers,
            validation_errors=validation_errors,
        )

    def _overlay_for_directory(
        self, directory: DirectoryInfo, *, domain: str
    ) -> dict[str, Any] | None:
        if self._config_store is not None and domain in self._config_store.list_domains(
            directory.path
        ):
            return self._config_store.read(directory.path, domain=domain)
        overlays = directory.metadata.get(DIRECTORY_CONFIG_OVERLAYS_KEY)
        if not isinstance(overlays, dict):
            return None
        overlay = overlays.get(domain)
        if overlay is None:
            return None
        if not isinstance(overlay, dict):
            raise ValueError(
                f"directory {directory.path!r} metadata.{DIRECTORY_CONFIG_OVERLAYS_KEY}.{domain} must be an object"
            )
        return overlay

    def _merge_values(
        self,
        current: Any,
        overlay: Any,
        path: tuple[str, ...],
        provenance: dict[str, DirectoryConfigValueSource],
        source: DirectoryConfigValueSource,
    ) -> Any:
        if overlay is None:
            self._mark_provenance(None, path, provenance, source)
            return None

        if isinstance(current, dict) and isinstance(overlay, dict):
            merged = deepcopy(current)
            for key in sorted(overlay):
                merged[key] = self._merge_values(
                    merged.get(key), overlay[key], (*path, key), provenance, source
                )
            return merged

        replacement = deepcopy(overlay)
        self._mark_provenance(replacement, path, provenance, source)
        return replacement

    def _mark_provenance(
        self,
        value: Any,
        path: tuple[str, ...],
        provenance: dict[str, DirectoryConfigValueSource],
        source: DirectoryConfigValueSource,
    ) -> None:
        key = ".".join(path)
        if isinstance(value, dict):
            if not value and key:
                provenance[key] = source
            for child_key in sorted(value):
                self._mark_provenance(value[child_key], (*path, child_key), provenance, source)
            return
        provenance[key] = source


class DirectoryScopeResolver:
    def __init__(self, service: DirectoryService) -> None:
        self._service = service

    def resolve_directory_path(self, directory_path: str | None) -> str:
        if not directory_path:
            return "root"
        return self._service.show(directory_path).directory.path

    def resolve_session_directory_path(
        self,
        *,
        context: str,
        session_id: str,
        directory_path: str | None,
    ) -> str:
        try:
            return self.resolve_directory_path(directory_path)
        except ValueError as exc:
            raise ValueError(
                f"{context} session {session_id!r} references missing directory_path "
                f"{directory_path!r}: {exc}"
            ) from exc


def load_root_gateway_config(
    directory_store: DirectoryStore,
    *,
    runtime_dir: Path,
    host_config: HostConfig,
    base_gateway_config: GatewayConfig | None = None,
) -> GatewayConfig:
    service = DirectoryService(directory_store)
    resolver = DirectoryConfigResolver(
        service,
        config_store=DirectoryConfigStore(directory_store, runtime_dir=runtime_dir),
    )
    return resolver.resolve_root_gateway_config(
        host_config, base_gateway_config=base_gateway_config
    )

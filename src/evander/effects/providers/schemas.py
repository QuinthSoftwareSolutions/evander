from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from evander.codex_auth import normalize_subscription_profile_id
from evander.effects.providers.agent_session import (
    normalize_optional_http_url,
    normalize_optional_string,
)
from evander.events.internal import validate_internal_event_type
from evander.events.models import ConfigValue, EffectDefinition
from evander.fast_mode import normalize_fast_mode
from evander.cyber_access import normalize_cyber_access_program
from evander.reasoning_effort import LEVELS, normalize_reasoning_effort


def _normalize_managed_git_access_mode(value: object, *, effect_type: str) -> ConfigValue | None:
    if value is None:
        return None
    if isinstance(value, str) and "${{" in value:
        return value
    normalized = str(value).strip().casefold().replace("_", "-")
    if not normalized:
        return None
    if normalized in {"read-only", "readonly", "read"}:
        return "read-only"
    if normalized in {"write", "writable"}:
        return "write"
    raise ValueError(f"{effect_type} git_access_mode must be write or read-only")


def _normalize_managed_git_access_lifetime(
    value: object, *, effect_type: str
) -> ConfigValue | None:
    if value is None:
        return None
    if isinstance(value, str) and "${{" in value:
        return value
    normalized = str(value).strip().casefold().replace("_", "-")
    if not normalized:
        return None
    if normalized in {"session", "persistent"}:
        return "session"
    if normalized in {"operation", "temporary", "one-shot", "oneshot"}:
        return "operation"
    raise ValueError(f"{effect_type} git_access_lifetime must be session or operation")


class SecurityScanEffectDefinition(EffectDefinition):
    type: Literal["effect:security:scan-repo"]
    session_id: ConfigValue
    repo_path: ConfigValue
    provider: ConfigValue = "codex-security"
    mode: ConfigValue = "standard"
    from_commit: ConfigValue | None = None
    to_commit: ConfigValue | None = None
    coverage_policy: ConfigValue = "provider"
    managed_repo_id: ConfigValue | None = None
    resume_from_scan_id: ConfigValue | None = None
    resume_subscription_profile_id: ConfigValue | None = None
    max_batch_text_bytes: ConfigValue | None = None
    timeout_seconds: ConfigValue | None = None

    @model_validator(mode="after")
    def validate_static_inputs(self):
        from evander.security_scans import SecurityScanRequest

        payload = {key: getattr(self, key) for key in SecurityScanRequest.model_fields}
        if not any(isinstance(value, str) and "${{" in value for value in payload.values()):
            SecurityScanRequest.model_validate(payload)
        return self


class EventEmitEffectDefinition(EffectDefinition):
    type: Literal["effect:event:emit"]
    event_type: ConfigValue
    correlation_key: ConfigValue | None = None
    payload: ConfigValue = Field(default_factory=dict)

    @field_validator("event_type")
    @classmethod
    def validate_static_event_type(cls, value: ConfigValue) -> ConfigValue:
        if isinstance(value, str) and "${{" not in value:
            validate_internal_event_type(value)
        return value

    @field_validator("payload")
    @classmethod
    def validate_payload_shape(cls, value: ConfigValue) -> ConfigValue:
        if not isinstance(value, (Mapping, str)):
            raise ValueError("effect:event:emit payload must be a JSON object")
        return value


class EventScheduleEffectDefinition(EffectDefinition):
    type: Literal["effect:event:schedule"]
    event_type: ConfigValue
    correlation_key: ConfigValue | None = None
    payload: ConfigValue = Field(default_factory=dict)
    run_at: ConfigValue | None = None
    delay_seconds: ConfigValue | None = None
    idempotency_key: ConfigValue | None = None

    @field_validator("event_type")
    @classmethod
    def validate_static_event_type(cls, value: ConfigValue) -> ConfigValue:
        if isinstance(value, str) and "${{" not in value:
            validate_internal_event_type(value)
        return value

    @field_validator("payload")
    @classmethod
    def validate_payload_shape(cls, value: ConfigValue) -> ConfigValue:
        if not isinstance(value, (Mapping, str)):
            raise ValueError("effect:event:schedule payload must be a JSON object")
        return value

    @model_validator(mode="after")
    def validate_time_configuration(self) -> "EventScheduleEffectDefinition":
        has_run_at = self.run_at is not None
        has_delay_seconds = self.delay_seconds is not None
        if has_run_at == has_delay_seconds:
            raise ValueError(
                "effect:event:schedule requires exactly one of run_at or delay_seconds"
            )
        return self


class ArtifactSpaceCreateEffectDefinition(EffectDefinition):
    type: Literal["effect:artifact-space:create"]
    path: ConfigValue
    display_name: ConfigValue | None = None
    metadata: ConfigValue | None = None
    grants: ConfigValue | None = None
    subject: ConfigValue | None = None
    expected_revision: ConfigValue | None = None

    @field_validator("metadata", "grants")
    @classmethod
    def validate_object_fields(cls, value: ConfigValue | None, info) -> ConfigValue | None:
        if value is None or isinstance(value, (Mapping, str)):
            return value
        raise ValueError(f"effect:artifact-space:create {info.field_name} must be a JSON object")


class ArtifactSpacePublishVersionEffectDefinition(EffectDefinition):
    type: Literal["effect:artifact-space:publish-version"]
    space_path: ConfigValue
    artifact_id: ConfigValue
    artifact_key: ConfigValue
    subject: ConfigValue | None = None
    metadata: ConfigValue | None = None
    expected_revision: ConfigValue | None = None
    require_existing_artifact: ConfigValue | None = None

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: ConfigValue | None) -> ConfigValue | None:
        if value is None or isinstance(value, (Mapping, str)):
            return value
        raise ValueError("effect:artifact-space:publish-version metadata must be a JSON object")


class VmCreateEffectDefinition(EffectDefinition):
    type: Literal["effect:vm:create"]
    provider: ConfigValue | None = None
    vm_name: ConfigValue | None = None
    session_id: ConfigValue | None = None
    labels: ConfigValue | None = None
    model: ConfigValue | None = None
    model_provider_mode: ConfigValue | None = None
    vm_user: ConfigValue | None = None
    ttl_hours: ConfigValue | None = None
    allowed_tools: list[str] | None = None
    connector_grants: ConfigValue | None = None
    host_directory_grants: ConfigValue | None = None
    tool_argument_policies: ConfigValue | None = None
    allowed_script_names: list[str] | None = None
    allowed_contact_ids: list[str] | None = None
    discord_channels: list[str] | None = None
    email_mailboxes: list[str] | None = None
    email_folders: list[str] | None = None
    dotnet: ConfigValue | None = None
    nuget: ConfigValue | None = None
    mssql: ConfigValue | None = None
    mssql_init_root: ConfigValue | None = None
    playwright: ConfigValue | None = None
    codex_skills: ConfigValue | None = None
    directory_path: ConfigValue | None = None
    metadata: ConfigValue | None = None

    @field_validator("metadata")
    @classmethod
    def validate_metadata_is_object(cls, value: ConfigValue | None) -> ConfigValue | None:
        if value is not None and not isinstance(value, (Mapping, str)):
            raise ValueError("effect:vm:create metadata must be a JSON object")
        return value


class VmCloneRepoEffectDefinition(EffectDefinition):
    type: Literal["effect:vm:clone-repo"]
    target: ConfigValue | None = None
    session_id: ConfigValue | None = None
    vm_name: ConfigValue | None = None
    managed_repo_id: ConfigValue
    clone_branch: ConfigValue | None = None
    vm_user: ConfigValue | None = None
    git_access_mode: ConfigValue | None = None
    git_access_lifetime: ConfigValue | None = None

    @model_validator(mode="after")
    def validate_target_selector(self) -> "VmCloneRepoEffectDefinition":
        selectors = [self.target, self.session_id, self.vm_name]
        if not any(value is not None for value in selectors):
            raise ValueError("effect:vm:clone-repo requires target, session_id, or vm_name")
        return self

    @field_validator("git_access_mode", mode="before")
    @classmethod
    def normalize_git_access_mode(cls, value: object) -> ConfigValue | None:
        return _normalize_managed_git_access_mode(value, effect_type="effect:vm:clone-repo")

    @field_validator("git_access_lifetime", mode="before")
    @classmethod
    def normalize_git_access_lifetime(cls, value: object) -> ConfigValue | None:
        return _normalize_managed_git_access_lifetime(value, effect_type="effect:vm:clone-repo")


class VmBootstrapPythonEffectDefinition(EffectDefinition):
    type: Literal["effect:vm:bootstrap-python"]
    target: ConfigValue | None = None
    session_id: ConfigValue | None = None
    vm_name: ConfigValue | None = None
    host_checkout: ConfigValue
    target_dir: ConfigValue
    vm_user: ConfigValue | None = None

    @model_validator(mode="after")
    def validate_target_selector(self) -> "VmBootstrapPythonEffectDefinition":
        selectors = [self.target, self.session_id, self.vm_name]
        if not any(value is not None for value in selectors):
            raise ValueError("effect:vm:bootstrap-python requires target, session_id, or vm_name")
        return self


class VmBootstrapFrontendEffectDefinition(EffectDefinition):
    type: Literal["effect:vm:bootstrap-frontend"]
    target: ConfigValue | None = None
    session_id: ConfigValue | None = None
    vm_name: ConfigValue | None = None
    host_checkout: ConfigValue
    install_roots: ConfigValue
    vm_user: ConfigValue | None = None

    @model_validator(mode="after")
    def validate_target_selector(self) -> "VmBootstrapFrontendEffectDefinition":
        selectors = [self.target, self.session_id, self.vm_name]
        if not any(value is not None for value in selectors):
            raise ValueError("effect:vm:bootstrap-frontend requires target, session_id, or vm_name")
        return self


class AgentCreateEffectDefinition(EffectDefinition):
    type: Literal["effect:agent:create"]
    provider: ConfigValue | None = None
    agent_provider: ConfigValue | None = None
    execution_backend: ConfigValue | None = None
    worker_id: ConfigValue | None = None
    vm_name: ConfigValue | None = None
    session_id: ConfigValue | None = None
    existing_agent_behavior: ConfigValue | None = None
    labels: ConfigValue | None = None
    model: ConfigValue | None = None
    cyber_access_program: ConfigValue | None = Field(
        default=None,
        description="Cyber access program for subsequent Codex turns. Omit to preserve an existing session choice.",
        json_schema_extra={"examples": ["standard", "daybreak_blue", "daybreak_red"]},
    )
    cost_category: ConfigValue | None = Field(
        default=None,
        description="Default inference cost category for the session. Blank means Uncategorized.",
    )
    reasoning_effort: ConfigValue | None = Field(
        default=None,
        description="Native reasoning level. Omit to inherit the session default.",
        json_schema_extra={"examples": list(LEVELS)},
    )
    fast_mode: ConfigValue | None = Field(
        default=None,
        description="Codex speed. True enables Fast; false selects Standard; ultrafast selects Ultrafast; omit/null inherits the session default.",
        json_schema_extra={"examples": [True, False, "ultrafast"]},
    )
    model_provider_mode: ConfigValue | None = None
    codex_auth_mode: ConfigValue | None = None
    # Bindings select a profile for this session; resolved values remain validated slugs.
    subscription_profile_id: str | None = None
    vm_user: ConfigValue | None = None
    vm_image: ConfigValue | None = None
    ttl_hours: ConfigValue | None = None
    capability_profile: ConfigValue | None = None
    allowed_tools: list[str] | None = None
    connector_grants: ConfigValue | None = None
    host_directory_grants: ConfigValue | None = None
    tool_argument_policies: ConfigValue | None = None
    allowed_script_names: list[str] | None = None
    allowed_contact_ids: list[str] | None = None
    communication_target_ids: list[str] | None = None
    email_mailbox_grants: ConfigValue | None = None
    discord_channels: list[str] | None = None
    teams_channel_allowlist: list[str] | None = None
    teams_chat_allowlist: list[str] | None = None
    teams_user_allowlist: list[str] | None = None
    email_mailboxes: list[str] | None = None
    email_folders: list[str] | None = None
    communication_file_receive_enabled: ConfigValue | None = None
    communication_file_send_enabled: ConfigValue | None = None
    communication_file_max_bytes: ConfigValue | None = None
    communication_file_require_approval_for_send: ConfigValue | None = None
    web_search_enabled: ConfigValue | None = None
    image_generation_enabled: ConfigValue | None = None
    dotnet: ConfigValue | None = None
    nuget: ConfigValue | None = None
    mssql: ConfigValue | None = None
    mssql_init_root: ConfigValue | None = None
    playwright: ConfigValue | None = None
    codex_skills: ConfigValue | None = None
    container_image: ConfigValue | None = None
    npm_registry_url: ConfigValue | None = None
    directory_path: ConfigValue | None = None
    metadata: ConfigValue | None = None

    @field_validator("metadata")
    @classmethod
    def validate_metadata_is_object(cls, value: ConfigValue | None) -> ConfigValue | None:
        if value is not None and not isinstance(value, (Mapping, str)):
            raise ValueError("effect:agent:create metadata must be a JSON object")
        return value

    @field_validator("subscription_profile_id", mode="before")
    @classmethod
    def normalize_subscription_profile_id_field(cls, value: object) -> str | None:
        if isinstance(value, str) and "${{" in value:
            return value
        return normalize_subscription_profile_id(value)

    @field_validator("npm_registry_url", mode="before")
    @classmethod
    def validate_npm_registry_url(cls, value: ConfigValue | None) -> ConfigValue | None:
        if value is None:
            return None
        if isinstance(value, str) and "${{" in value:
            return value
        return normalize_optional_http_url(value, field_name="npm_registry_url")

    @field_validator("cyber_access_program")
    @classmethod
    def validate_cyber_access_program_value(cls, value: ConfigValue | None) -> ConfigValue | None:
        if isinstance(value, dict) or isinstance(value, str) and "${{" in value:
            return value
        return normalize_cyber_access_program(value)

    @field_validator("fast_mode")
    @classmethod
    def validate_fast_mode_value(cls, value: ConfigValue | None) -> ConfigValue | None:
        if isinstance(value, str) and "${{" in value:
            return value
        if isinstance(value, dict):
            return value
        return normalize_fast_mode(value)

    @field_validator("reasoning_effort")
    @classmethod
    def validate_reasoning_effort_value(cls, value: ConfigValue | None) -> ConfigValue | None:
        if isinstance(value, str) and "${{" not in value:
            return normalize_reasoning_effort(value)
        if value is not None and not isinstance(value, (str, dict)):
            raise ValueError("reasoning_effort must be a native level or binding")
        return value


class AgentDestroyEffectDefinition(EffectDefinition):
    type: Literal["effect:agent:destroy"]
    target: ConfigValue | None = None
    session_id: ConfigValue | None = None
    vm_name: ConfigValue | None = None
    execution_backend: ConfigValue | None = None
    directory_path: ConfigValue | None = None

    @model_validator(mode="after")
    def validate_target_selector(self) -> "AgentDestroyEffectDefinition":
        selectors = [self.target, self.session_id, self.vm_name]
        if not any(value is not None for value in selectors):
            raise ValueError("effect:agent:destroy requires target, session_id, or vm_name")
        return self


class AgentCloneRepoEffectDefinition(EffectDefinition):
    type: Literal["effect:agent:clone-repo"]
    target: ConfigValue | None = None
    session_id: ConfigValue | None = None
    vm_name: ConfigValue | None = None
    managed_repo_id: ConfigValue
    clone_branch: ConfigValue | None = None
    vm_user: ConfigValue | None = None
    git_author_name: ConfigValue | None = None
    git_author_email: ConfigValue | None = None
    git_committer_name: ConfigValue | None = None
    git_committer_email: ConfigValue | None = None
    git_access_mode: ConfigValue | None = None
    git_access_lifetime: ConfigValue | None = None
    pinned_ref: ConfigValue | None = None
    pinned_commit_sha: ConfigValue | None = None

    @model_validator(mode="after")
    def validate_target_selector(self) -> "AgentCloneRepoEffectDefinition":
        selectors = [self.target, self.session_id, self.vm_name]
        if not any(value is not None for value in selectors):
            raise ValueError("effect:agent:clone-repo requires target, session_id, or vm_name")
        if (self.pinned_ref is None) != (self.pinned_commit_sha is None):
            raise ValueError(
                "effect:agent:clone-repo requires pinned_ref and pinned_commit_sha together"
            )
        return self

    @field_validator("git_access_mode", mode="before")
    @classmethod
    def normalize_git_access_mode(cls, value: object) -> ConfigValue | None:
        return _normalize_managed_git_access_mode(value, effect_type="effect:agent:clone-repo")

    @field_validator("git_access_lifetime", mode="before")
    @classmethod
    def normalize_git_access_lifetime(cls, value: object) -> ConfigValue | None:
        return _normalize_managed_git_access_lifetime(value, effect_type="effect:agent:clone-repo")


class AgentPrepareReferenceRepositoriesEffectDefinition(EffectDefinition):
    type: Literal["effect:agent:prepare-reference-repositories"]
    target: ConfigValue | None = None
    session_id: ConfigValue | None = None
    vm_name: ConfigValue | None = None
    directory_path: ConfigValue
    repositories: ConfigValue
    refresh_upstream: ConfigValue = False

    @model_validator(mode="after")
    def validate_target_selector(self) -> "AgentPrepareReferenceRepositoriesEffectDefinition":
        if not any(value is not None for value in (self.target, self.session_id, self.vm_name)):
            raise ValueError(
                "effect:agent:prepare-reference-repositories requires target, session_id, or vm_name"
            )
        if isinstance(self.repositories, list):
            if not self.repositories:
                raise ValueError(
                    "effect:agent:prepare-reference-repositories requires at least one repository"
                )
            for index, repository in enumerate(self.repositories):
                if not isinstance(repository, Mapping):
                    raise ValueError(f"repositories[{index}] must be an object")
                if "repo_source" in repository:
                    raise ValueError(
                        f"repositories[{index}].repo_source is not supported; "
                        "select a managed_repo_id"
                    )
                managed_repo_id = repository.get("managed_repo_id")
                if not isinstance(managed_repo_id, str) or not managed_repo_id.strip():
                    raise ValueError(f"repositories[{index}].managed_repo_id is required")
        return self


class AgentGrantRepoAccessEffectDefinition(EffectDefinition):
    type: Literal["effect:agent:grant-repo-access"]
    target: ConfigValue | None = None
    session_id: ConfigValue | None = None
    vm_name: ConfigValue | None = None
    managed_repo_id: ConfigValue
    git_access_mode: ConfigValue | None = None
    vm_user: ConfigValue | None = None

    @model_validator(mode="after")
    def validate_target_selector(self) -> "AgentGrantRepoAccessEffectDefinition":
        if not any(value is not None for value in (self.target, self.session_id, self.vm_name)):
            raise ValueError(
                "effect:agent:grant-repo-access requires target, session_id, or vm_name"
            )
        return self

    @field_validator("git_access_mode", mode="before")
    @classmethod
    def normalize_git_access_mode(cls, value: object) -> ConfigValue | None:
        return _normalize_managed_git_access_mode(
            value, effect_type="effect:agent:grant-repo-access"
        )


class AgentRevokeRepoAccessEffectDefinition(EffectDefinition):
    type: Literal["effect:agent:revoke-repo-access"]
    target: ConfigValue | None = None
    session_id: ConfigValue | None = None
    vm_name: ConfigValue | None = None
    managed_repo_id: ConfigValue

    @model_validator(mode="after")
    def validate_target_selector(self) -> "AgentRevokeRepoAccessEffectDefinition":
        if not any(value is not None for value in (self.target, self.session_id, self.vm_name)):
            raise ValueError(
                "effect:agent:revoke-repo-access requires target, session_id, or vm_name"
            )
        return self


class AgentBootstrapFrontendEffectDefinition(EffectDefinition):
    type: Literal["effect:agent:bootstrap-frontend"]
    target: ConfigValue | None = None
    session_id: ConfigValue | None = None
    vm_name: ConfigValue | None = None
    host_checkout: ConfigValue
    install_roots: ConfigValue
    vm_user: ConfigValue | None = None

    @model_validator(mode="after")
    def validate_target_selector(self) -> "AgentBootstrapFrontendEffectDefinition":
        selectors = [self.target, self.session_id, self.vm_name]
        if not any(value is not None for value in selectors):
            raise ValueError(
                "effect:agent:bootstrap-frontend requires target, session_id, or vm_name"
            )
        return self


class AgentPromptEffectDefinition(EffectDefinition):
    type: Literal["effect:agent:prompt"]
    target: ConfigValue | None = None
    session_id: ConfigValue | None = None
    vm_name: ConfigValue | None = None
    prompt: ConfigValue
    model: ConfigValue | None = None
    cost_category: ConfigValue | None = Field(
        default=None,
        description="Inference cost category. Omit/null to inherit; an empty prompt override means Uncategorized.",
    )
    reasoning_effort: ConfigValue | None = Field(
        default=None,
        description="Native reasoning level. Omit to inherit the session default.",
        json_schema_extra={"examples": list(LEVELS)},
    )
    fast_mode: ConfigValue | None = Field(
        default=None,
        description="Codex speed. True enables Fast; false selects Standard; ultrafast selects Ultrafast; omit/null inherits the session default.",
        json_schema_extra={"examples": [True, False, "ultrafast"]},
    )
    vm_user: ConfigValue | None = None
    resume_thread_id: ConfigValue | None = None
    resume_thread_metadata_key: ConfigValue | None = None
    resume_thread_metadata_collection: ConfigValue | None = None
    mcp_tool_approval_lease_id: ConfigValue | None = None
    reduced_context_prompt: ConfigValue | None = None
    context_retry_prompt: ConfigValue | None = None
    agent_context_retry_enabled: ConfigValue | None = None
    agent_context_retry_max_attempts: ConfigValue | None = None
    agent_context_retry_target_chars: ConfigValue | None = None

    @field_validator("fast_mode")
    @classmethod
    def validate_fast_mode_value(cls, value: ConfigValue | None) -> ConfigValue | None:
        if isinstance(value, str) and "${{" in value:
            return value
        if isinstance(value, dict):
            return value
        return normalize_fast_mode(value)

    @field_validator("reasoning_effort")
    @classmethod
    def validate_reasoning_effort_value(cls, value: ConfigValue | None) -> ConfigValue | None:
        if isinstance(value, str) and "${{" not in value:
            return normalize_reasoning_effort(value)
        if value is not None and not isinstance(value, (str, dict)):
            raise ValueError("reasoning_effort must be a native level or binding")
        return value


class AgentEnqueuePromptEffectDefinition(EffectDefinition):
    type: Literal["effect:agent:enqueue-prompt"]
    target: ConfigValue | None = None
    session_id: ConfigValue | None = None
    vm_name: ConfigValue | None = None
    prompt: ConfigValue
    delivery_mode: ConfigValue = "queue"
    conversation_key: ConfigValue | None = None
    model: ConfigValue | None = None
    cost_category: ConfigValue | None = Field(
        default=None,
        description="Inference cost category. Omit/null to inherit; an empty prompt override means Uncategorized.",
    )
    reasoning_effort: ConfigValue | None = Field(
        default=None,
        description="Native reasoning level. Omit to inherit the session default.",
        json_schema_extra={"examples": list(LEVELS)},
    )
    fast_mode: ConfigValue | None = Field(
        default=None,
        description="Codex speed. True enables Fast; false selects Standard; ultrafast selects Ultrafast; omit/null inherits the session default.",
        json_schema_extra={"examples": [True, False, "ultrafast"]},
    )
    vm_user: ConfigValue | None = None
    dispatch_id: ConfigValue | None = None
    resume_thread_id: ConfigValue | None = None
    resume_mode: ConfigValue | None = None
    resume_thread_metadata_key: ConfigValue | None = None
    resume_thread_metadata_collection: ConfigValue | None = None
    queue_key: ConfigValue | None = None
    mcp_tool_approval_lease_id: ConfigValue | None = None
    reduced_context_prompt: ConfigValue | None = None
    context_retry_prompt: ConfigValue | None = None
    agent_context_retry_enabled: ConfigValue | None = None
    agent_context_retry_max_attempts: ConfigValue | None = None
    agent_context_retry_target_chars: ConfigValue | None = None

    @field_validator("delivery_mode")
    @classmethod
    def validate_delivery_mode(cls, value):
        if (
            isinstance(value, str)
            and "${{" not in value
            and value not in {"queue", "steer_if_running"}
        ):
            raise ValueError("delivery_mode must be queue or steer_if_running")
        return value

    @field_validator("fast_mode")
    @classmethod
    def validate_fast_mode_value(cls, value: ConfigValue | None) -> ConfigValue | None:
        if isinstance(value, str) and "${{" in value:
            return value
        if isinstance(value, dict):
            return value
        return normalize_fast_mode(value)

    @field_validator("reasoning_effort")
    @classmethod
    def validate_reasoning_effort_value(cls, value: ConfigValue | None) -> ConfigValue | None:
        if isinstance(value, str) and "${{" not in value:
            return normalize_reasoning_effort(value)
        if value is not None and not isinstance(value, (str, dict)):
            raise ValueError("reasoning_effort must be a native level or binding")
        return value


class CodeConsultationEffectDefinition(EffectDefinition):
    type: Literal["effect:agent:code-consultation"]
    consultation_id: ConfigValue
    operation: ConfigValue
    outcome: ConfigValue | None = None
    status: ConfigValue | None = None
    reason: ConfigValue | None = None

    @field_validator("operation")
    @classmethod
    def validate_operation(cls, value: ConfigValue) -> ConfigValue:
        if isinstance(value, str) and "${{" not in value:
            normalized = value.strip().casefold()
            if normalized not in {"prepare", "finalize", "terminate"}:
                raise ValueError(
                    "effect:agent:code-consultation operation must be prepare, finalize, or terminate"
                )
            return normalized
        return value

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: ConfigValue | None) -> ConfigValue | None:
        if isinstance(value, str) and "${{" not in value:
            normalized = value.strip().casefold()
            if normalized not in {"timed_out", "cancelled"}:
                raise ValueError(
                    "effect:agent:code-consultation status must be timed_out or cancelled"
                )
            return normalized
        return value


class ChatAssistantEnsureSessionEffectDefinition(EffectDefinition):
    type: Literal["effect:chat-assistant:ensure-session"]
    user_id: ConfigValue
    thread_id: ConfigValue
    user_message_id: ConfigValue
    content: ConfigValue
    backend: ConfigValue | None = None
    assistant_capability_mode: ConfigValue | None = None
    assistant_scope: ConfigValue | None = None


class AgentExecEffectDefinition(EffectDefinition):
    type: Literal["effect:agent:exec"]
    target: ConfigValue | None = None
    session_id: ConfigValue | None = None
    vm_name: ConfigValue | None = None
    vm_user: ConfigValue | None = None
    command: ConfigValue
    working_directory: ConfigValue | None = None
    environment: ConfigValue | None = None
    stdin: ConfigValue | None = None
    timeout_seconds: ConfigValue | None = None
    check: ConfigValue | None = True


class SemanticEnsureCollectionEffectDefinition(EffectDefinition):
    type: Literal["effect:semantic:ensure-collection"]
    collection_name: ConfigValue
    directory_path: ConfigValue | None = None


class SemanticUpsertEffectDefinition(EffectDefinition):
    type: Literal["effect:semantic:upsert"]
    collection_name: ConfigValue
    point_id: ConfigValue
    text: ConfigValue
    payload: ConfigValue | None = None
    directory_path: ConfigValue | None = None
    wait: ConfigValue | None = None


class SemanticDeleteEffectDefinition(EffectDefinition):
    type: Literal["effect:semantic:delete"]
    collection_name: ConfigValue
    point_ids: ConfigValue
    directory_path: ConfigValue | None = None
    wait: ConfigValue | None = None


class StateUpdateEffectDefinition(EffectDefinition):
    type: Literal["effect:state:update"]
    namespace: ConfigValue
    key: ConfigValue
    operation: ConfigValue
    value: ConfigValue | None = None
    patch: ConfigValue | None = None
    expected_version: ConfigValue | None = None

    @field_validator("operation")
    @classmethod
    def validate_operation(cls, value: ConfigValue) -> ConfigValue:
        if isinstance(value, str) and "${{" not in value:
            normalized = value.strip().lower()
            if normalized not in {"set", "patch", "delete"}:
                raise ValueError("effect:state:update operation must be set, patch, or delete")
            return normalized
        return value

    @field_validator("patch")
    @classmethod
    def validate_patch(cls, value: ConfigValue | None) -> ConfigValue | None:
        if value is None:
            return value
        if isinstance(value, dict):
            return value
        if isinstance(value, str) and "${{" in value:
            return value
        raise ValueError("effect:state:update patch must be a JSON object or binding")


class HttpRequestEffectDefinition(EffectDefinition):
    type: Literal["effect:http:request"]
    method: ConfigValue = "GET"
    url: ConfigValue
    allowed_url_patterns: ConfigValue | None = Field(default_factory=lambda: ["*"])
    allow_private_networks: ConfigValue | None = False
    payload: ConfigValue | None = None
    headers: ConfigValue | None = None
    timeout_seconds: ConfigValue | None = None
    check: ConfigValue | None = False

    @field_validator("method")
    @classmethod
    def validate_method(cls, value: ConfigValue) -> ConfigValue:
        if not isinstance(value, str):
            raise ValueError("effect:http:request method must be a string or binding")
        normalized = value.strip()
        if "${{" in normalized:
            return normalized
        if not normalized:
            raise ValueError("effect:http:request method must be GET, POST, or PUT")
        normalized = normalized.upper()
        if normalized not in {"GET", "POST", "PUT"}:
            raise ValueError("effect:http:request method must be GET, POST, or PUT")
        return normalized

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: ConfigValue) -> ConfigValue:
        if not isinstance(value, str):
            raise ValueError("effect:http:request url must be a string or binding")
        normalized = value.strip()
        if "${{" in normalized:
            return normalized
        if not normalized:
            raise ValueError("effect:http:request url must be a non-empty http or https URL")
        from urllib.parse import urlparse

        parsed = urlparse(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("effect:http:request url must be a non-empty http or https URL")
        return normalized

    @field_validator("allowed_url_patterns")
    @classmethod
    def validate_allowed_url_patterns(cls, value: ConfigValue | None) -> ConfigValue | None:
        if value is None:
            return value
        if isinstance(value, str):
            if value.strip():
                return value
            raise ValueError("effect:http:request allowed_url_patterns must include a pattern")
        if not isinstance(value, list):
            raise ValueError(
                "effect:http:request allowed_url_patterns must be a string, string list, or binding"
            )
        if not value:
            raise ValueError("effect:http:request allowed_url_patterns must include a pattern")
        for item in value:
            if not isinstance(item, str) or not item.strip():
                raise ValueError(
                    "effect:http:request allowed_url_patterns must be a string, string list, or binding"
                )
        return value

    @field_validator("headers")
    @classmethod
    def validate_headers(cls, value: ConfigValue | None) -> ConfigValue | None:
        if value is None:
            return None
        if isinstance(value, str):
            text = value.strip()
            if "${{" in text:
                return text
            raise ValueError("effect:http:request headers must be a mapping or binding")
        if not isinstance(value, Mapping):
            raise ValueError("effect:http:request headers must be a mapping or binding")
        return value


class RuntimeDiagnosticsSnapshotEffectDefinition(EffectDefinition):
    type: Literal["effect:runtime:diagnostics-snapshot"]
    since: ConfigValue | None = "24 hours ago"
    units: ConfigValue | None = None
    include_failed_units: ConfigValue | None = True
    include_journal: ConfigValue | None = True
    include_runtime_files: ConfigValue | None = True
    max_journal_lines_per_unit: ConfigValue | None = 200
    max_runtime_file_bytes: ConfigValue | None = 120000
    command_timeout_seconds: ConfigValue | None = 10

    @field_validator("units")
    @classmethod
    def validate_units(cls, value: ConfigValue | None) -> ConfigValue | None:
        if value is None:
            return None
        if isinstance(value, str):
            if "${{" in value or value.strip():
                return value
            raise ValueError("effect:runtime:diagnostics-snapshot units must not be empty")
        if not isinstance(value, list):
            raise ValueError(
                "effect:runtime:diagnostics-snapshot units must be a string list or binding"
            )
        for item in value:
            if not isinstance(item, str) or not item.strip():
                raise ValueError(
                    "effect:runtime:diagnostics-snapshot units must be a string list or binding"
                )
        return value


class VmStartEffectDefinition(EffectDefinition):
    type: Literal["effect:vm:start"]
    target: ConfigValue | None = None
    session_id: ConfigValue | None = None
    vm_name: ConfigValue | None = None


class VmStopEffectDefinition(EffectDefinition):
    type: Literal["effect:vm:stop"]
    target: ConfigValue | None = None
    session_id: ConfigValue | None = None
    vm_name: ConfigValue | None = None
    timeout_seconds: ConfigValue | None = None


class VmExecEffectDefinition(EffectDefinition):
    type: Literal["effect:vm:exec"]
    target: ConfigValue | None = None
    session_id: ConfigValue | None = None
    vm_name: ConfigValue | None = None
    vm_user: ConfigValue | None = None
    command: ConfigValue
    working_directory: ConfigValue | None = None
    environment: ConfigValue | None = None
    stdin: ConfigValue | None = None
    timeout_seconds: ConfigValue | None = None
    check: ConfigValue | None = True


class VmCopyFilesItemDefinition(BaseModel):
    source_path: ConfigValue | None = None
    source_format: ConfigValue | None = None
    content: ConfigValue | None = None
    content_format: ConfigValue | None = None
    destination_path: ConfigValue
    mode: ConfigValue | None = None
    overwrite: ConfigValue | None = True
    merge_strategy: ConfigValue | None = None
    section_id: ConfigValue | None = None
    remove_section_ids: ConfigValue | None = None

    @model_validator(mode="after")
    def validate_source_or_content(self) -> "VmCopyFilesItemDefinition":
        if self.source_path is None and self.content is None:
            raise ValueError("either source_path or content is required")
        if self.source_path is not None and self.content is not None:
            raise ValueError("source_path and content are mutually exclusive")
        if self.merge_strategy == "managed_section":
            if self.source_path is not None:
                raise ValueError("managed_section merge requires inline content")
            if self.section_id is None:
                raise ValueError("managed_section merge requires section_id")
        elif self.remove_section_ids is not None and self.merge_strategy is None:
            raise ValueError("remove_section_ids requires managed_section merge")
        return self


class VmCopyFilesEffectDefinition(EffectDefinition):
    type: Literal["effect:vm:copy-files"]
    target: ConfigValue | None = None
    session_id: ConfigValue | None = None
    vm_name: ConfigValue | None = None
    vm_user: ConfigValue | None = None
    allowed_source_roots: ConfigValue | None = None
    files: list[VmCopyFilesItemDefinition]

    @field_validator("allowed_source_roots")
    @classmethod
    def validate_allowed_source_roots(cls, value: ConfigValue | None) -> ConfigValue | None:
        return _validate_allowed_source_roots(value, effect_type="effect:vm:copy-files")


class AgentCopyFilesEffectDefinition(EffectDefinition):
    type: Literal["effect:agent:copy-files"]
    target: ConfigValue | None = None
    session_id: ConfigValue | None = None
    vm_name: ConfigValue | None = None
    vm_user: ConfigValue | None = None
    allowed_source_roots: ConfigValue | None = None
    files: list[VmCopyFilesItemDefinition]

    @field_validator("allowed_source_roots")
    @classmethod
    def validate_allowed_source_roots(cls, value: ConfigValue | None) -> ConfigValue | None:
        return _validate_allowed_source_roots(value, effect_type="effect:agent:copy-files")


def _validate_allowed_source_roots(
    value: ConfigValue | None,
    *,
    effect_type: str,
) -> ConfigValue | None:
    if value is None:
        return value
    if isinstance(value, str):
        if value.strip():
            return value
        raise ValueError(f"{effect_type} allowed_source_roots must include a path")
    if not isinstance(value, list):
        raise ValueError(f"{effect_type} allowed_source_roots must be a string list or binding")
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{effect_type} allowed_source_roots must be a string list or binding")
    return value


class ArtifactStageEffectDefinition(EffectDefinition):
    type: Literal["effect:artifact:stage"]
    target: ConfigValue | None = None
    session_id: ConfigValue | None = None
    vm_name: ConfigValue | None = None
    path: ConfigValue
    destination_path: ConfigValue | None = None
    vm_user: ConfigValue | None = None

    @model_validator(mode="after")
    def validate_target_selector(self) -> "ArtifactStageEffectDefinition":
        selectors = [self.target, self.session_id, self.vm_name]
        if not any(value is not None for value in selectors):
            raise ValueError("effect:artifact:stage requires target, session_id, or vm_name")
        return self


class ArtifactPublishArtifactsEffectDefinition(EffectDefinition):
    type: Literal["effect:artifact:publish-artifacts"]
    target: ConfigValue | None = None
    session_id: ConfigValue | None = None
    vm_name: ConfigValue | None = None
    key: ConfigValue
    source_paths: ConfigValue | None = None
    vm_user: ConfigValue | None = None

    @model_validator(mode="after")
    def validate_target_selector(self) -> "ArtifactPublishArtifactsEffectDefinition":
        selectors = [self.target, self.session_id, self.vm_name]
        if not any(value is not None for value in selectors):
            raise ValueError(
                "effect:artifact:publish-artifacts requires target, session_id, or vm_name"
            )
        return self


class ArtifactImportPublishedEffectDefinition(EffectDefinition):
    type: Literal["effect:artifact:import-published"]
    target: ConfigValue | None = None
    session_id: ConfigValue | None = None
    vm_name: ConfigValue | None = None
    key: ConfigValue
    destination_path: ConfigValue | None = None
    overwrite: ConfigValue | None = False
    vm_user: ConfigValue | None = None

    @model_validator(mode="after")
    def validate_target_selector(self) -> "ArtifactImportPublishedEffectDefinition":
        selectors = [self.target, self.session_id, self.vm_name]
        if not any(value is not None for value in selectors):
            raise ValueError(
                "effect:artifact:import-published requires target, session_id, or vm_name"
            )
        return self


class VmDeleteFilesEffectDefinition(EffectDefinition):
    type: Literal["effect:vm:delete-files"]
    target: ConfigValue | None = None
    session_id: ConfigValue | None = None
    vm_name: ConfigValue | None = None
    vm_user: ConfigValue | None = None
    paths: list[ConfigValue]
    recursive: ConfigValue | None = False
    missing_ok: ConfigValue | None = True


class IssueUpdateStateEffectDefinition(EffectDefinition):
    type: Literal["effect:issue:update-state"]
    issue_id: ConfigValue
    state: ConfigValue
    sync_proxy_cache: ConfigValue | None = True


class IssueUpdateMetadataEffectDefinition(EffectDefinition):
    type: Literal["effect:issue:update-metadata"]
    issue_id: ConfigValue
    patch: ConfigValue | None = None
    merge_patch: ConfigValue | None = None
    expected_updated_at: ConfigValue | None = None
    sync_proxy_cache: ConfigValue | None = True

    @field_validator("patch", "merge_patch")
    @classmethod
    def validate_patch_is_object(cls, value: ConfigValue | None) -> ConfigValue | None:
        if value is None:
            return None
        if not isinstance(value, (Mapping, str)):
            raise ValueError(
                "effect:issue:update-metadata patch and merge_patch must be JSON objects"
            )
        return value

    @model_validator(mode="after")
    def validate_patch_selector(self) -> "IssueUpdateMetadataEffectDefinition":
        if self.patch is None and self.merge_patch is None:
            raise ValueError("effect:issue:update-metadata requires patch or merge_patch")
        if self.patch is not None and self.merge_patch is not None:
            raise ValueError(
                "effect:issue:update-metadata patch and merge_patch are mutually exclusive"
            )
        return self


class IssueUpsertAdoProxyEffectDefinition(EffectDefinition):
    type: Literal["effect:issue:upsert-ado-proxy"]
    directory_path: ConfigValue | None = None
    organization_url: ConfigValue | None = None
    project: ConfigValue | None = None
    managed_repo_id: ConfigValue | None = None
    repo_source: ConfigValue | None = None
    yaml_output_dir: ConfigValue | None = None
    item: ConfigValue | None = None
    items: ConfigValue | None = None

    @model_validator(mode="after")
    def validate_item_selector(self) -> "IssueUpsertAdoProxyEffectDefinition":
        if self.item is None and self.items is None:
            raise ValueError("effect:issue:upsert-ado-proxy requires item or items")
        if self.item is not None and self.items is not None:
            raise ValueError("effect:issue:upsert-ado-proxy item and items are mutually exclusive")
        return self


class AdoUpdateWorkItemEffectDefinition(EffectDefinition):
    type: Literal["effect:ado:update-work-item"]
    directory_path: ConfigValue
    project: ConfigValue
    work_item_id: ConfigValue
    state: ConfigValue | None = None
    fields: ConfigValue | None = None
    expected_states: list[ConfigValue] | ConfigValue | None = None
    sync_proxy_cache: ConfigValue | None = False

    @field_validator("fields")
    @classmethod
    def validate_fields_is_object(cls, value: ConfigValue | None) -> ConfigValue | None:
        if value is None:
            return None
        if not isinstance(value, (Mapping, str)):
            raise ValueError("effect:ado:update-work-item fields must be a JSON object")
        return value


class AdoAddWorkItemTagEffectDefinition(EffectDefinition):
    type: Literal["effect:ado:add-work-item-tag"]
    directory_path: ConfigValue
    project: ConfigValue
    work_item_id: ConfigValue
    tag: ConfigValue
    sync_proxy_cache: ConfigValue | None = False


class AdoRemoveWorkItemTagEffectDefinition(EffectDefinition):
    type: Literal["effect:ado:remove-work-item-tag"]
    directory_path: ConfigValue
    project: ConfigValue
    work_item_id: ConfigValue
    tag: ConfigValue
    sync_proxy_cache: ConfigValue | None = False


class AdoPostWorkItemCommentEffectDefinition(EffectDefinition):
    type: Literal["effect:ado:post-work-item-comment"]
    directory_path: ConfigValue
    project: ConfigValue
    work_item_id: ConfigValue
    comment: ConfigValue
    mentions: ConfigValue | None = None
    idempotency_key: ConfigValue | None = None

    @field_validator("mentions")
    @classmethod
    def validate_mentions(cls, value: ConfigValue | None) -> ConfigValue | None:
        if value is None or isinstance(value, (str, list)):
            return value
        raise ValueError(
            "effect:ado:post-work-item-comment mentions must be a string or list"
        )


class AdoBuildWaitEffectDefinition(EffectDefinition):
    type: Literal["effect:ado:build-wait"]
    directory_path: ConfigValue
    run_id: ConfigValue
    project: ConfigValue | None = None
    timeout_seconds: ConfigValue = 3600
    poll_interval_seconds: ConfigValue = 10


class AgentInstallSkillsEffectDefinition(EffectDefinition):
    type: Literal["effect:agent:install-skills"]
    target: ConfigValue
    codex_skills: ConfigValue
    vm_user: ConfigValue | None = None


class AdoPipelineRunEffectDefinition(EffectDefinition):
    type: Literal["effect:ado:pipeline-run"]
    directory_path: ConfigValue
    pipeline_id: ConfigValue
    project: ConfigValue | None = None
    branch: ConfigValue | None = None
    variables: ConfigValue | None = None
    template_parameters: ConfigValue | None = None
    reject_running_parameters: ConfigValue | None = None
    timeout_seconds: ConfigValue | None = None
    poll_interval_seconds: ConfigValue | None = None
    check: ConfigValue | None = True

    @field_validator("variables", "template_parameters", "reject_running_parameters")
    @classmethod
    def validate_mapping_field(cls, value: ConfigValue | None) -> ConfigValue | None:
        if value is None:
            return None
        if not isinstance(value, (Mapping, str)):
            raise ValueError(
                "effect:ado:pipeline-run parameter and variable fields must be JSON objects"
            )
        return value


def _normalize_static_pull_request_provider(value: ConfigValue) -> ConfigValue:
    if isinstance(value, str) and "${{" not in value:
        normalized = value.strip()
        if normalized in {"ado", "azure-devops", "azure_devops"}:
            return "azure_devops"
    return value


def _migrate_legacy_pull_request_organization(data: object) -> object:
    if not isinstance(data, Mapping):
        return data
    payload = dict(data)
    if "ado_organization" not in payload and "organization_url" in payload:
        payload["ado_organization"] = payload["organization_url"]
    payload.pop("organization_url", None)
    return payload


class PullRequestAddTagEffectDefinition(EffectDefinition):
    type: Literal["effect:pull-request:add-tag"]
    provider: ConfigValue
    repo: ConfigValue
    pr_id: ConfigValue
    tag: ConfigValue
    directory_path: ConfigValue | None = None
    ado_organization: ConfigValue | None = None
    project: ConfigValue | None = None

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_organization_url(cls, data: object) -> object:
        return _migrate_legacy_pull_request_organization(data)

    @field_validator("provider")
    @classmethod
    def normalize_static_provider(cls, value: ConfigValue) -> ConfigValue:
        return _normalize_static_pull_request_provider(value)


class PullRequestRemoveTagEffectDefinition(EffectDefinition):
    type: Literal["effect:pull-request:remove-tag"]
    provider: ConfigValue
    repo: ConfigValue
    pr_id: ConfigValue
    tag: ConfigValue
    directory_path: ConfigValue | None = None
    ado_organization: ConfigValue | None = None
    project: ConfigValue | None = None

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_organization_url(cls, data: object) -> object:
        return _migrate_legacy_pull_request_organization(data)

    @field_validator("provider")
    @classmethod
    def normalize_static_provider(cls, value: ConfigValue) -> ConfigValue:
        return _normalize_static_pull_request_provider(value)


class PullRequestReplyEffectDefinition(EffectDefinition):
    type: Literal["effect:pull-request:reply"]
    provider: ConfigValue
    repo: ConfigValue
    pr_id: ConfigValue
    comment_id: ConfigValue
    feedback_kind: ConfigValue | None = None
    body_text: ConfigValue
    idempotency_key: ConfigValue
    thread_id: ConfigValue | None = None
    directory_path: ConfigValue | None = None
    ado_organization: ConfigValue | None = None
    project: ConfigValue | None = None

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_organization_url(cls, data: object) -> object:
        return _migrate_legacy_pull_request_organization(data)

    @field_validator("provider")
    @classmethod
    def normalize_static_provider(cls, value: ConfigValue) -> ConfigValue:
        return _normalize_static_pull_request_provider(value)


class PullRequestUpdateEffectDefinition(EffectDefinition):
    type: Literal["effect:pull-request:update"]
    provider: ConfigValue
    repo: ConfigValue
    pr_id: ConfigValue
    directory_path: ConfigValue | None = None
    ado_organization: ConfigValue | None = None
    project: ConfigValue | None = None
    title: ConfigValue | None = None
    description: ConfigValue | None = None
    status: ConfigValue | None = None
    add_tags: ConfigValue | None = None
    remove_tags: ConfigValue | None = None
    expected_statuses: ConfigValue | None = None

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_organization_url(cls, data: object) -> object:
        return _migrate_legacy_pull_request_organization(data)

    @field_validator("provider")
    @classmethod
    def normalize_static_provider(cls, value: ConfigValue) -> ConfigValue:
        return _normalize_static_pull_request_provider(value)

    @field_validator("add_tags", "remove_tags", "expected_statuses")
    @classmethod
    def validate_string_list_fields(cls, value: ConfigValue | None) -> ConfigValue | None:
        if value is None or isinstance(value, (str, list)):
            return value
        raise ValueError("effect:pull-request:update tag/status guards must be a string or list")


class CommunicationSendMessageEffectDefinition(EffectDefinition):
    type: Literal["effect:communication:send-message"]
    communication_target_id: str
    message: ConfigValue
    published_artifacts: ConfigValue = Field(default_factory=list)
    thread_id: ConfigValue | None = None
    subject: ConfigValue | None = None
    sender_session_id: ConfigValue | None = None

    @field_validator("communication_target_id", mode="before")
    @classmethod
    def normalize_communication_target_id(cls, value: object) -> str:
        text = str(value or "").strip().lower()
        if not text:
            raise ValueError("effect:communication:send-message requires communication_target_id")
        return text


class NotificationSendEffectDefinition(EffectDefinition):
    type: Literal["effect:notification:send"]
    communication_target_id: str
    message: ConfigValue
    severity: Literal["info", "success", "warning", "error"] = "info"
    subject: ConfigValue | None = None
    thread_id: ConfigValue | None = None

    @field_validator("communication_target_id", mode="before")
    @classmethod
    def normalize_communication_target_id(cls, value: object) -> str:
        text = str(value or "").strip().lower()
        if not text:
            raise ValueError("effect:notification:send requires communication_target_id")
        return text


class WebsiteSessionEnsureProfileEffectDefinition(EffectDefinition):
    type: Literal["effect:website-session:ensure-profile"]
    template_id: str = Field(min_length=1, max_length=4096)
    browser_origin: str
    login_origin: str
    directory_path: str | None = None

    @field_validator("template_id")
    @classmethod
    def validate_template_id(cls, value: str) -> str:
        import re
        from evander.website_profile_templates import PROFILE_ID_PATTERN

        value = value.strip()
        if "${{" not in value and not re.fullmatch(PROFILE_ID_PATTERN, value):
            raise ValueError("template_id must name a host-approved website profile template")
        return value

    @field_validator("browser_origin", "login_origin")
    @classmethod
    def validate_origin(cls, value: str) -> str:
        from evander.website_profile_templates import profile_origin

        return value if "${{" in value else profile_origin(value)


class SessionReconcilePolicyEffectDefinition(EffectDefinition):
    type: Literal["effect:session:reconcile-policy"]
    session_id: ConfigValue
    directory_path: ConfigValue | None = None
    policy: ConfigValue

    @field_validator("policy")
    @classmethod
    def validate_policy(cls, value: ConfigValue) -> ConfigValue:
        if not isinstance(value, (Mapping, str)):
            raise ValueError("policy must be a JSON object or workflow binding")
        return value


class SessionUpdateMetadataEffectDefinition(EffectDefinition):
    type: Literal["effect:session:update-metadata"]
    target: ConfigValue | None = None
    session_id: ConfigValue | None = None
    vm_name: ConfigValue | None = None
    metadata: ConfigValue

    @field_validator("metadata")
    @classmethod
    def validate_metadata_is_object(cls, value: ConfigValue) -> ConfigValue:
        if not isinstance(value, (Mapping, str)):
            raise ValueError("effect:session:update-metadata metadata must be a JSON object")
        return value

    @model_validator(mode="after")
    def validate_target_selector(self) -> "SessionUpdateMetadataEffectDefinition":
        selectors = [self.target, self.session_id, self.vm_name]
        if not any(value is not None for value in selectors):
            raise ValueError(
                "effect:session:update-metadata requires target, session_id, or vm_name"
            )
        return self


class SessionSetReasoningEffortEffectDefinition(EffectDefinition):
    type: Literal["effect:session:set-reasoning-effort"]
    target: ConfigValue | None = None
    session_id: ConfigValue | None = None
    vm_name: ConfigValue | None = None
    reasoning_effort: ConfigValue | None

    @field_validator("reasoning_effort", mode="before")
    @classmethod
    def validate_effort(cls, value: object) -> ConfigValue | None:
        if isinstance(value, str) and "${{" in value:
            return value
        return normalize_reasoning_effort(value)

    @model_validator(mode="after")
    def validate_target_selector(self) -> "SessionSetReasoningEffortEffectDefinition":
        selectors = [
            value for value in (self.target, self.session_id, self.vm_name) if value is not None
        ]
        if len(selectors) != 1 or not isinstance(selectors[0], str) or not selectors[0].strip():
            raise ValueError("Provide exactly one non-empty target, session_id, or vm_name")
        return self


class SessionSetImageGenerationEnabledEffectDefinition(EffectDefinition):
    type: Literal["effect:session:set-image-generation-enabled"]
    target: ConfigValue | None = None
    session_id: ConfigValue | None = None
    vm_name: ConfigValue | None = None
    enabled: ConfigValue

    @model_validator(mode="after")
    def validate_target_selector(self) -> "SessionSetImageGenerationEnabledEffectDefinition":
        selectors = [self.target, self.session_id, self.vm_name]
        if not any(value is not None for value in selectors):
            raise ValueError(
                "effect:session:set-image-generation-enabled requires target, session_id, or vm_name"
            )
        return self


class SessionApproveMcpToolsForRunEffectDefinition(EffectDefinition):
    type: Literal["effect:session:approve-mcp-tools-for-run"]
    target: ConfigValue | None = None
    session_id: ConfigValue | None = None
    vm_name: ConfigValue | None = None
    tools: ConfigValue
    expires_in_seconds: ConfigValue | None = None
    approval_notification_target_ids: ConfigValue | None = None

    @field_validator("tools")
    @classmethod
    def validate_tools_shape(cls, value: ConfigValue) -> ConfigValue:
        if not isinstance(value, (list, str)):
            raise ValueError("effect:session:approve-mcp-tools-for-run tools must be a list")
        return value

    @model_validator(mode="after")
    def validate_target_selector(self) -> "SessionApproveMcpToolsForRunEffectDefinition":
        selectors = [self.target, self.session_id, self.vm_name]
        if not any(value is not None for value in selectors):
            raise ValueError(
                "effect:session:approve-mcp-tools-for-run requires target, session_id, or vm_name"
            )
        return self


class SessionRevokeMcpToolApprovalLeaseEffectDefinition(EffectDefinition):
    type: Literal["effect:session:revoke-mcp-tool-approval-lease"]
    mcp_tool_approval_lease_id: ConfigValue | None = None
    lease_id: ConfigValue | None = None
    session_id: ConfigValue | None = None
    workflow_run_id: ConfigValue | None = None

    @field_validator("mcp_tool_approval_lease_id", "lease_id", mode="before")
    @classmethod
    def normalize_lease_id(cls, value: object) -> ConfigValue | None:
        return normalize_optional_string(value)

    @model_validator(mode="after")
    def validate_lease_selector(self) -> "SessionRevokeMcpToolApprovalLeaseEffectDefinition":
        lease_ids = [
            normalize_optional_string(self.mcp_tool_approval_lease_id),
            normalize_optional_string(self.lease_id),
        ]
        if not any(value is not None for value in lease_ids):
            raise ValueError(
                "effect:session:revoke-mcp-tool-approval-lease requires mcp_tool_approval_lease_id or lease_id"
            )
        return self


class SessionRenewIfExpiredEffectDefinition(EffectDefinition):
    type: Literal["effect:session:renew-if-expired"]
    target: ConfigValue | None = None
    session_id: ConfigValue | None = None
    vm_name: ConfigValue | None = None
    extend_by: ConfigValue = "1d"
    renew_if_expires_within: ConfigValue | None = None
    directory_path: ConfigValue | None = None

    @field_validator("renew_if_expires_within")
    @classmethod
    def validate_renew_if_expires_within(cls, value: ConfigValue | None) -> ConfigValue | None:
        if value is None:
            return None
        if isinstance(value, str) and "${{" in value:
            return normalize_optional_string(value)
        from evander.effects.providers.session_renew_if_expired import (
            parse_session_renew_if_expires_within,
        )

        parse_session_renew_if_expires_within(value)
        return normalize_optional_string(value)

    @model_validator(mode="after")
    def validate_target_selector(self) -> "SessionRenewIfExpiredEffectDefinition":
        selectors = [
            normalize_optional_string(self.target),
            normalize_optional_string(self.session_id),
            normalize_optional_string(self.vm_name),
        ]
        if sum(value is not None for value in selectors) != 1:
            raise ValueError(
                "effect:session:renew-if-expired requires exactly one of target, session_id, or vm_name"
            )
        return self


class GitEnsureManagedRepoEffectDefinition(EffectDefinition):
    type: Literal["effect:git:ensure-managed-repo"]
    directory_path: ConfigValue
    repo_source: ConfigValue


class GitPromoteAgentBranchEffectDefinition(EffectDefinition):
    type: Literal["effect:git:promote-agent-branch"]
    managed_repo_id: ConfigValue
    agent_branch: ConfigValue
    target_branch: ConfigValue
    expected_commit_sha: ConfigValue


class GitPushBranchEffectDefinition(EffectDefinition):
    type: Literal["effect:git:push-branch"]
    managed_repo_id: ConfigValue
    directory_path: ConfigValue | None = None
    source_ref: ConfigValue
    expected_commit_sha: ConfigValue
    expected_destination_commit_sha: ConfigValue | None = None
    destination_branch: ConfigValue


class PrCreateEffectDefinition(EffectDefinition):
    type: Literal["effect:pr:create"]
    managed_repo_id: ConfigValue
    directory_path: ConfigValue | None = None
    source_ref: ConfigValue
    expected_commit_sha: ConfigValue
    destination_branch: ConfigValue
    fallback_destination_branch: ConfigValue | None = None
    base_branch: ConfigValue | None = None
    session_id: ConfigValue | None = None
    work_item_id: ConfigValue | None = None
    publication_context_id: ConfigValue | None = None
    publication_policy: Literal["allow_bot_fallback", "require_personal"] = "allow_bot_fallback"
    title: ConfigValue | None = None
    fallback_title: ConfigValue | None = None
    body: ConfigValue | None = None
    draft: ConfigValue | None = None
    fill: ConfigValue | None = True

    @model_validator(mode="after")
    def validate_publication_policy(self):
        if self.publication_policy == "require_personal" and not self.publication_context_id:
            raise ValueError("require_personal needs publication_context_id")
        return self

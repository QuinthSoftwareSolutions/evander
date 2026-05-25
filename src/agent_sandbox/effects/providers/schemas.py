from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from agent_sandbox.events.models import ConfigValue, EffectDefinition
from agent_sandbox.events.internal import validate_internal_event_type


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


class VmCreateEffectDefinition(EffectDefinition):
    type: Literal["effect:vm:create"]
    provider: ConfigValue | None = None
    vm_name: ConfigValue | None = None
    session_id: ConfigValue | None = None
    labels: ConfigValue | None = None
    model: ConfigValue | None = None
    vm_user: ConfigValue | None = None
    ttl_hours: ConfigValue | None = None
    allowed_tools: list[str] | None = None
    tool_argument_policies: ConfigValue | None = None
    allowed_script_names: list[str] | None = None
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


class VmCloneRepoEffectDefinition(EffectDefinition):
    type: Literal["effect:vm:clone-repo"]
    target: ConfigValue | None = None
    session_id: ConfigValue | None = None
    vm_name: ConfigValue | None = None
    repo_source: ConfigValue
    clone_branch: ConfigValue | None = None
    vm_user: ConfigValue | None = None
    directory_path: ConfigValue | None = None

    @model_validator(mode="after")
    def validate_target_selector(self) -> "VmCloneRepoEffectDefinition":
        selectors = [self.target, self.session_id, self.vm_name]
        if not any(value is not None for value in selectors):
            raise ValueError("effect:vm:clone-repo requires target, session_id, or vm_name")
        return self


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
    execution_backend: ConfigValue | None = None
    vm_name: ConfigValue | None = None
    session_id: ConfigValue | None = None
    existing_agent_behavior: ConfigValue | None = None
    labels: ConfigValue | None = None
    model: ConfigValue | None = None
    vm_user: ConfigValue | None = None
    ttl_hours: ConfigValue | None = None
    capability_profile: ConfigValue | None = None
    allowed_tools: list[str] | None = None
    tool_argument_policies: ConfigValue | None = None
    allowed_script_names: list[str] | None = None
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
    dotnet: ConfigValue | None = None
    nuget: ConfigValue | None = None
    mssql: ConfigValue | None = None
    mssql_init_root: ConfigValue | None = None
    playwright: ConfigValue | None = None
    codex_skills: ConfigValue | None = None
    directory_path: ConfigValue | None = None


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
    repo_source: ConfigValue
    clone_branch: ConfigValue | None = None
    vm_user: ConfigValue | None = None
    directory_path: ConfigValue | None = None

    @model_validator(mode="after")
    def validate_target_selector(self) -> "AgentCloneRepoEffectDefinition":
        selectors = [self.target, self.session_id, self.vm_name]
        if not any(value is not None for value in selectors):
            raise ValueError("effect:agent:clone-repo requires target, session_id, or vm_name")
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
    vm_user: ConfigValue | None = None
    resume_thread_id: ConfigValue | None = None
    resume_thread_metadata_key: ConfigValue | None = None
    resume_thread_metadata_collection: ConfigValue | None = None
    mcp_tool_approval_lease_id: ConfigValue | None = None


class AgentEnqueuePromptEffectDefinition(EffectDefinition):
    type: Literal["effect:agent:enqueue-prompt"]
    target: ConfigValue | None = None
    session_id: ConfigValue | None = None
    vm_name: ConfigValue | None = None
    prompt: ConfigValue
    model: ConfigValue | None = None
    vm_user: ConfigValue | None = None
    resume_thread_id: ConfigValue | None = None
    resume_thread_metadata_key: ConfigValue | None = None
    resume_thread_metadata_collection: ConfigValue | None = None
    queue_key: ConfigValue | None = None
    mcp_tool_approval_lease_id: ConfigValue | None = None


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


class BashScriptEffectDefinition(EffectDefinition):
    type: Literal["effect:bash:script"]
    script_path: ConfigValue
    args: list[ConfigValue] = Field(default_factory=list)
    working_directory: ConfigValue | None = None
    environment: ConfigValue | None = None
    stdin: ConfigValue | None = None
    timeout_seconds: ConfigValue | None = None
    check: ConfigValue | None = True


class HttpRequestEffectDefinition(EffectDefinition):
    type: Literal["effect:http:request"]
    method: ConfigValue = "GET"
    url: ConfigValue
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

    @model_validator(mode="after")
    def validate_source_or_content(self) -> "VmCopyFilesItemDefinition":
        if self.source_path is None and self.content is None:
            raise ValueError("either source_path or content is required")
        if self.source_path is not None and self.content is not None:
            raise ValueError("source_path and content are mutually exclusive")
        return self


class VmCopyFilesEffectDefinition(EffectDefinition):
    type: Literal["effect:vm:copy-files"]
    target: ConfigValue | None = None
    session_id: ConfigValue | None = None
    vm_name: ConfigValue | None = None
    vm_user: ConfigValue | None = None
    files: list[VmCopyFilesItemDefinition]


class AgentCopyFilesEffectDefinition(EffectDefinition):
    type: Literal["effect:agent:copy-files"]
    target: ConfigValue | None = None
    session_id: ConfigValue | None = None
    vm_name: ConfigValue | None = None
    vm_user: ConfigValue | None = None
    files: list[VmCopyFilesItemDefinition]


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
    vm_user: ConfigValue | None = None

    @model_validator(mode="after")
    def validate_target_selector(self) -> "ArtifactPublishArtifactsEffectDefinition":
        selectors = [self.target, self.session_id, self.vm_name]
        if not any(value is not None for value in selectors):
            raise ValueError(
                "effect:artifact:publish-artifacts requires target, session_id, or vm_name"
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
    repo_source: ConfigValue | None = None
    yaml_output_dir: ConfigValue | None = None
    item: ConfigValue | None = None
    items: ConfigValue | None = None

    @model_validator(mode="after")
    def validate_item_selector(self) -> "IssueUpsertAdoProxyEffectDefinition":
        if self.item is None and self.items is None:
            raise ValueError("effect:issue:upsert-ado-proxy requires item or items")
        if self.item is not None and self.items is not None:
            raise ValueError(
                "effect:issue:upsert-ado-proxy item and items are mutually exclusive"
            )
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


class PullRequestUpdateEffectDefinition(EffectDefinition):
    type: Literal["effect:pull-request:update"]
    provider: ConfigValue
    repo: ConfigValue
    pr_id: ConfigValue
    directory_path: ConfigValue | None = None
    organization_url: ConfigValue | None = None
    project: ConfigValue | None = None
    title: ConfigValue | None = None
    description: ConfigValue | None = None
    status: ConfigValue | None = None
    add_tags: ConfigValue | None = None
    remove_tags: ConfigValue | None = None
    expected_statuses: ConfigValue | None = None

    @field_validator("provider")
    @classmethod
    def normalize_static_provider(cls, value: ConfigValue) -> ConfigValue:
        if isinstance(value, str) and "${{" not in value:
            normalized = value.strip()
            if normalized in {"ado", "azure-devops", "azure_devops"}:
                return "azure_devops"
        return value

    @field_validator("add_tags", "remove_tags", "expected_statuses")
    @classmethod
    def validate_string_list_fields(cls, value: ConfigValue | None) -> ConfigValue | None:
        if value is None or isinstance(value, (str, list)):
            return value
        raise ValueError(
            "effect:pull-request:update tag/status guards must be a string or list"
        )


class CommunicationSendMessageEffectDefinition(EffectDefinition):
    type: Literal["effect:communication:send-message"]
    communication_target_id: str
    message: ConfigValue
    thread_id: ConfigValue | None = None

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


class SessionApproveMcpToolsForRunEffectDefinition(EffectDefinition):
    type: Literal["effect:session:approve-mcp-tools-for-run"]
    target: ConfigValue | None = None
    session_id: ConfigValue | None = None
    vm_name: ConfigValue | None = None
    tools: ConfigValue
    expires_in_seconds: ConfigValue | None = None

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


class SessionRenewIfExpiredEffectDefinition(EffectDefinition):
    type: Literal["effect:session:renew-if-expired"]
    target: ConfigValue | None = None
    session_id: ConfigValue | None = None
    vm_name: ConfigValue | None = None
    extend_by: ConfigValue = "1d"
    directory_path: ConfigValue | None = None

    @model_validator(mode="after")
    def validate_target_selector(self) -> "SessionRenewIfExpiredEffectDefinition":
        selectors = [self.target, self.session_id, self.vm_name]
        if not any(value is not None for value in selectors):
            raise ValueError(
                "effect:session:renew-if-expired requires target, session_id, or vm_name"
            )
        return self


class GitPromoteAgentBranchEffectDefinition(EffectDefinition):
    type: Literal["effect:git:promote-agent-branch"]
    host_repo_path: ConfigValue
    bare_repo_path: ConfigValue | None = None
    bare_repo_name: ConfigValue | None = None
    agent_branch: ConfigValue
    review_branch: ConfigValue | None = None
    remote_name: ConfigValue | None = None

    @model_validator(mode="after")
    def validate_bare_repo_selector(self) -> "GitPromoteAgentBranchEffectDefinition":
        if self.bare_repo_path is None and self.bare_repo_name is None:
            raise ValueError(
                "effect:git:promote-agent-branch requires bare_repo_path or bare_repo_name"
            )
        if self.bare_repo_path is not None and self.bare_repo_name is not None:
            raise ValueError(
                "effect:git:promote-agent-branch bare_repo_path and bare_repo_name are mutually exclusive"
            )
        return self


class PrCreateEffectDefinition(EffectDefinition):
    type: Literal["effect:pr:create"]
    host_repo_path: ConfigValue
    local_branch: ConfigValue | None = None
    origin_branch: ConfigValue | None = None
    canonical_origin_url: ConfigValue | None = None
    provider: ConfigValue | None = None
    project: ConfigValue | None = None
    base_branch: ConfigValue | None = None
    session_id: ConfigValue | None = None
    work_item_id: ConfigValue | None = None
    title: ConfigValue | None = None
    body: ConfigValue | None = None
    body_file: ConfigValue | None = None
    body_artifact_ids: ConfigValue | None = None
    draft: ConfigValue | None = None
    fill: ConfigValue | None = True

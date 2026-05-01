from __future__ import annotations

from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from agent_sandbox.events.models import ConfigValue, EffectDefinition


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
    labels: ConfigValue | None = None
    model: ConfigValue | None = None
    vm_user: ConfigValue | None = None
    ttl_hours: ConfigValue | None = None
    capability_profile: ConfigValue | None = None
    allowed_tools: list[str] | None = None
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
    output_mode: Literal["immediate", "on_completion"] = "immediate"


class AgentEnqueuePromptEffectDefinition(EffectDefinition):
    type: Literal["effect:agent:enqueue-prompt"]
    target: ConfigValue | None = None
    session_id: ConfigValue | None = None
    vm_name: ConfigValue | None = None
    prompt: ConfigValue
    model: ConfigValue | None = None
    vm_user: ConfigValue | None = None
    resume_thread_id: ConfigValue | None = None


class ChatAssistantEnsureSessionEffectDefinition(EffectDefinition):
    type: Literal["effect:chat-assistant:ensure-session"]
    user_id: ConfigValue
    thread_id: ConfigValue
    user_message_id: ConfigValue
    content: ConfigValue
    backend: ConfigValue | None = None
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


class BashScriptEffectDefinition(EffectDefinition):
    type: Literal["effect:bash:script"]
    script_path: ConfigValue
    args: list[ConfigValue] = Field(default_factory=list)
    working_directory: ConfigValue | None = None
    environment: ConfigValue | None = None
    stdin: ConfigValue | None = None
    timeout_seconds: ConfigValue | None = None
    check: ConfigValue | None = True


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
    draft: ConfigValue | None = None
    fill: ConfigValue | None = True

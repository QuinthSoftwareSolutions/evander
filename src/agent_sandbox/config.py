from __future__ import annotations

from collections import Counter
from ipaddress import ip_network
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import (
    AliasChoices,
    AnyHttpUrl,
    BaseModel,
    Field,
    IPvAnyNetwork,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

from .models import DirectoryPath
from .events.models import (
    EdgeDefinition,
    EffectDefinitionDiscriminated,
    GateDefinitionDiscriminated,
    QueryDefinitionDiscriminated,
    TriggerDefinitionDiscriminated,
    effective_node_output_schema,
)
from .workflow_graph import build_inbound_edge_index, index_declared_outputs, merge_declared_output_intersection

_HTTP_URL_ADAPTER = TypeAdapter(AnyHttpUrl)


class MailboxConfig(BaseModel):
    mailbox_id: str
    provider: Literal["json_store", "gmail", "graph"] = "json_store"
    account_identifier: str = Field(
        default="default",
        validation_alias=AliasChoices("account_identifier", "account", "address"),
    )
    auth_secret_refs: dict[str, str] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("auth_secret_refs", "auth_env_var_names"),
    )
    folder_mapping: dict[str, str] = Field(default_factory=lambda: {"inbox": "inbox", "drafts": "drafts"})
    folder_allowlist_defaults: list[str] = Field(default_factory=lambda: ["inbox", "drafts"])


class GatewayConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"
    approval_ttl_seconds: int = 3600
    runtime_dir: Path = Path("/var/lib/agent-sandbox")
    audit_log_path: Path = Path("/var/log/agent-sandbox/audit.jsonl")
    session_store_path: Path = Path("/var/lib/agent-sandbox/sessions.json")
    approval_store_path: Path = Path("/var/lib/agent-sandbox/approvals.json")
    draft_store_path: Path = Path("/var/lib/agent-sandbox/drafts.json")
    email_store_path: Path = Path("/var/lib/agent-sandbox/emails.json")
    email_cursor_store_path: Path = Path("/var/lib/agent-sandbox/email_cursors.json")
    discord_command_store_path: Path = Path("/var/lib/agent-sandbox/discord_commands.json")
    teams_message_store_path: Path = Path("/var/lib/agent-sandbox/teams_messages.json")
    directories_store_path: Path = Path("/var/lib/agent-sandbox/directories.json")
    enable_directory_scoped_config: bool = False
    discord_backend: Literal["stub", "bot_api"] = "stub"
    discord_bot_token_env: str = Field(default="DISCORD_BOT_TOKEN", min_length=1)
    discord_api_base_url: str = "https://discord.com/api/v10"
    discord_poll_limit: int = Field(default=50, ge=1, le=100)
    discord_timeout_seconds: float = Field(default=15.0, gt=0)
    discord_retry_count: int = Field(default=3, ge=0, le=10)
    discord_retry_base_backoff_seconds: float = Field(default=0.5, gt=0)
    discord_retry_max_backoff_seconds: float = Field(default=5.0, gt=0)
    discord_operator_user_allowlist: list[str] = Field(default_factory=list)
    discord_operator_channel_allowlist: list[str] = Field(default_factory=list)
    discord_poll_operator_dms: bool = False
    teams_backend: Literal["stub", "graph_api"] = "stub"
    teams_graph_access_token_env: str = Field(default="GRAPH_TEAMS_ACCESS_TOKEN", min_length=1)
    teams_graph_api_base_url: str = "https://graph.microsoft.com/v1.0"
    teams_poll_limit: int = Field(default=50, ge=1, le=100)
    teams_timeout_seconds: float = Field(default=15.0, gt=0)
    teams_retry_count: int = Field(default=3, ge=0, le=10)
    teams_retry_base_backoff_seconds: float = Field(default=0.5, gt=0)
    teams_retry_max_backoff_seconds: float = Field(default=5.0, gt=0)
    discord_inbox_routes: dict[str, list[str]] = Field(default_factory=dict)
    bind_network_cidr: IPvAnyNetwork = "192.168.250.0/24"
    gateway_url: str = "http://192.168.250.1:8000/mcp"
    openai_relay_host: str = "0.0.0.0"
    openai_relay_port: int = 11434
    openai_api_base_url: str = "https://api.openai.com/v1"
    openai_api_key_env: str = "OPENAI_API_KEY"
    openai_relay_timeout_seconds: float = 300.0
    nuget_relay_host: str = "0.0.0.0"
    nuget_relay_port: int = 11666
    nuget_public_feed_base_url: str = "https://api.nuget.org"
    nuget_azure_feed_base_url: str = (
        "https://pkgs.dev.azure.com/example/example/_packaging/example/nuget"
    )
    nuget_azure_pat_env: str = "AZURE_ARTIFACTS_PAT"
    nuget_relay_timeout_seconds: float = 300.0
    default_session_ttl_seconds: int = 28800
    email_mailbox_allowlist: list[str] = Field(default_factory=list)
    email_folder_allowlist_by_mailbox: dict[str, list[str]] = Field(default_factory=dict)
    email_draft_recipient_allowlist: list[str] = Field(default_factory=list)
    email_draft_domain_allowlist: list[str] = Field(default_factory=list)
    email_send_enabled: bool = False
    pr_reply_require_approval: bool = False
    github_token_env: str = "GITHUB_TOKEN"
    azure_devops_pat_env: str = "AZURE_DEVOPS_PAT"
    azure_devops_pr_pat_env: str = "AZURE_DEVOPS_PR_PAT"
    azure_devops_organization_url: str = "https://dev.azure.com"
    azure_devops_project: str = "default"
    ado_area_filters: list[str] = Field(default_factory=list)
    ado_team_filters: list[str] = Field(default_factory=list)
    ado_project_or_solution: str = ""
    ado_prewarm_target: str | None = None
    ado_bootstrap_dotnet: bool = True
    ado_bootstrap_nuget: bool = True
    ado_bootstrap_mssql: bool = True
    ado_mssql_init_root: str | None = None
    ado_bootstrap_playwright: bool = False
    ado_bootstrap_frontend_deps: bool = False
    ado_frontend_host_checkout: str | None = None
    ado_frontend_install_roots: list[str] = Field(default_factory=list)
    ado_mssql_sa_password_env: str | None = None
    email_provider_timeout_seconds: float = Field(default=30.0, gt=0)
    host_heartbeat_poll_interval_seconds: float = Field(default=30.0, gt=0)
    host_heartbeat_jitter_seconds: float = Field(default=5.0, ge=0)
    host_heartbeat_cooldown_seconds: float = Field(default=60.0, ge=0)
    host_heartbeat_http_timeout_seconds: float = Field(default=30.0, gt=0)
    host_heartbeat_lock_path: Path = Path("/var/lib/agent-sandbox/host-heartbeat.lock")
    host_heartbeat_session_cooldown_path: Path = Path(
        "/var/lib/agent-sandbox/host-heartbeat-session-cooldown.json"
    )
    host_agent_control_state_path: Path = Path("/var/lib/agent-sandbox/host-agent-control-state.json")
    host_busy_check_script_path: Path = Path("scripts/check_session_vm_busy.sh")
    host_resume_script_path: Path = Path("scripts/resume_issue_agent.sh")
    host_heartbeat_wake_state_path: Path = Path("/var/lib/agent-sandbox/host-heartbeat-wake-state.json")
    host_heartbeat_pr_resume_prompt: str = (
        "You have unread PR comments. Please fetch them with list_unread_feedback."
    )
    host_heartbeat_discord_resume_prompt: str = (
        "You have unread Discord messages. Please fetch them with "
        "list_unread_discord_messages and respond with discord_post_status if needed."
    )
    host_heartbeat_vm_user: str = "agent"
    host_heartbeat_resume_timeout_seconds: float = Field(default=1200.0, gt=0)
    host_heartbeat_stream_conflict_policy: Literal["fanout", "reject"] = "fanout"
    host_heartbeat_scheduled_wake_prefix_text: str = (
        "You have unread PR comments. Please fetch them with list_unread_feedback."
    )
    host_heartbeat_schedule_max_active_per_session: int | None = Field(default=None, ge=1)
    host_heartbeat_schedule_max_active_total: int | None = Field(default=None, ge=1)
    host_heartbeat_schedule_min_interval_seconds: float | None = Field(default=None, gt=0)
    pr_map_path: Path = Path("/var/lib/agent-sandbox/pr-map.json")
    email_mailboxes: list[MailboxConfig] = Field(
        default_factory=lambda: [
            MailboxConfig(
                mailbox_id="default",
                provider="json_store",
                account_identifier="default",
                folder_mapping={"inbox": "inbox", "drafts": "drafts"},
                folder_allowlist_defaults=["inbox", "drafts"],
            )
        ]
    )
    redacted_keys: set[str] = Field(
        default_factory=lambda: {"body", "body_text", "prompt", "authorization", "token"}
    )

    @field_validator(
        "gateway_url",
        "discord_api_base_url",
        "teams_graph_api_base_url",
        "openai_api_base_url",
        "nuget_public_feed_base_url",
        "nuget_azure_feed_base_url",
        mode="before",
    )
    @classmethod
    def normalize_http_url_fields(cls, value: object, info) -> object:
        if not isinstance(value, str):
            return value
        normalized = value.strip().rstrip("/")
        if not normalized:
            raise ValueError(f"{info.field_name} cannot be empty")
        return normalized

    @field_validator(
        "gateway_url",
        "discord_api_base_url",
        "teams_graph_api_base_url",
        "openai_api_base_url",
        "nuget_public_feed_base_url",
        "nuget_azure_feed_base_url",
    )
    @classmethod
    def validate_http_url_fields(cls, value: str, info) -> str:
        try:
            _HTTP_URL_ADAPTER.validate_python(value)
        except ValidationError as exc:
            raise ValueError(
                f"{info.field_name} must be a valid http(s) URL, got {value!r}"
            ) from exc
        return value

    @field_validator("bind_network_cidr", mode="before")
    @classmethod
    def normalize_bind_network_cidr(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        if not normalized:
            raise ValueError("bind_network_cidr cannot be empty")
        try:
            network = ip_network(normalized, strict=True)
        except ValueError as exc:
            raise ValueError(
                "bind_network_cidr must be a valid CIDR network (for example: 192.168.250.0/24)"
            ) from exc
        return str(network)

    @model_validator(mode="after")
    def apply_runtime_path_defaults(self) -> GatewayConfig:
        default_runtime_dir = Path("/var/lib/agent-sandbox")
        default_paths = {
            "host_heartbeat_lock_path": default_runtime_dir / "host-heartbeat.lock",
            "host_heartbeat_session_cooldown_path": default_runtime_dir
            / "host-heartbeat-session-cooldown.json",
            "host_agent_control_state_path": default_runtime_dir / "host-agent-control-state.json",
            "host_heartbeat_wake_state_path": default_runtime_dir / "host-heartbeat-wake-state.json",
            "session_store_path": default_runtime_dir / "sessions.json",
            "approval_store_path": default_runtime_dir / "approvals.json",
            "draft_store_path": default_runtime_dir / "drafts.json",
            "email_store_path": default_runtime_dir / "emails.json",
            "email_cursor_store_path": default_runtime_dir / "email_cursors.json",
            "discord_command_store_path": default_runtime_dir / "discord_commands.json",
            "teams_message_store_path": default_runtime_dir / "teams_messages.json",
            "directories_store_path": default_runtime_dir / "directories.json",
            "pr_map_path": default_runtime_dir / "pr-map.json",
        }
        for field_name, default_path in default_paths.items():
            if getattr(self, field_name) == default_path:
                setattr(self, field_name, self.runtime_dir / default_path.name)
        if self.audit_log_path == Path("/var/log/agent-sandbox/audit.jsonl"):
            self.audit_log_path = self.runtime_dir / "audit.jsonl"
        return self

    @model_validator(mode="after")
    def apply_email_mailbox_defaults(self) -> GatewayConfig:
        mailbox_ids = [mailbox.mailbox_id for mailbox in self.email_mailboxes]
        duplicates = sorted(mailbox_id for mailbox_id, count in Counter(mailbox_ids).items() if count > 1)
        if duplicates:
            duplicate_list = ", ".join(duplicates)
            raise ValueError(f"Duplicate email mailbox IDs are not allowed: {duplicate_list}")

        if not self.email_mailbox_allowlist:
            self.email_mailbox_allowlist = mailbox_ids

        if not self.email_folder_allowlist_by_mailbox:
            self.email_folder_allowlist_by_mailbox = {
                mailbox.mailbox_id: mailbox.folder_allowlist_defaults
                for mailbox in self.email_mailboxes
            }

        return self


class AdoWorkerGlobalConfig(BaseModel):
    runtime_dir: Path = Path("runtime")
    poll_interval_seconds: float = Field(default=30.0, gt=0)
    directories: list[str] = Field(default_factory=list)
    yaml_output_dir: Path | None = None
    setup_log_dir: Path | None = None
    processed_store_path: Path | None = None
    setup_script_path: Path = Path("scripts/setup_issue_vm.sh")
    ado_work_items_azure_devops_pat_env: str = "ADO_WORK_ITEMS_AZURE_DEVOPS_PAT"
    setup_timeout_seconds: float = Field(default=1800.0, gt=0)
    setup_concurrency_limit: int = Field(default=1, ge=1)
    setup_lock_path: Path | None = None
    fetch_retry_count: int = Field(default=2, ge=0)
    fetch_retry_backoff_seconds: float = Field(default=2.0, ge=0.0)
    include_comments: bool = True
    comments_max_count: int = Field(default=20, ge=1)
    comment_text_max_chars: int = Field(default=4000, ge=1)

    @field_validator("directories", mode="before")
    @classmethod
    def normalize_directories(cls, value: object) -> object:
        if value is None:
            return []
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, (list, tuple)):
            return value
        normalized: list[str] = []
        for item in value:
            text = str(item).strip()
            if not text:
                continue
            normalized.append(DirectoryPath(segments=text).as_posix())
        return normalized

    @model_validator(mode="after")
    def apply_runtime_path_defaults(self) -> AdoWorkerGlobalConfig:
        if self.yaml_output_dir is None:
            self.yaml_output_dir = self.runtime_dir / "ado-work-items"
        if self.setup_log_dir is None:
            self.setup_log_dir = self.runtime_dir / "setup-logs"
        if self.processed_store_path is None:
            self.processed_store_path = self.runtime_dir / "ado-processed-work-items.json"
        if self.setup_lock_path is None:
            self.setup_lock_path = self.runtime_dir / "setup-issue-vm.lock"
        return self


class HostHeartbeatWorkerGlobalConfig(BaseModel):
    runtime_dir: Path = Path("runtime")
    host_heartbeat_directory_paths: list[str] = Field(default_factory=list)
    host_heartbeat_poll_interval_seconds: float = Field(default=30.0, gt=0)
    host_heartbeat_jitter_seconds: float = Field(default=5.0, ge=0)
    host_heartbeat_cooldown_seconds: float = Field(default=60.0, ge=0)
    host_heartbeat_http_timeout_seconds: float = Field(default=30.0, gt=0)
    host_heartbeat_lock_path: Path | None = None
    host_heartbeat_session_cooldown_path: Path | None = None
    host_busy_check_script_path: Path = Path("scripts/check_session_vm_busy.sh")
    host_resume_script_path: Path = Path("scripts/resume_issue_agent.sh")
    host_heartbeat_wake_state_path: Path | None = None
    host_heartbeat_vm_user: str = "agent"
    host_heartbeat_resume_timeout_seconds: float = Field(default=1200.0, gt=0)
    host_heartbeat_stream_conflict_policy: Literal["fanout", "reject"] = "fanout"
    host_heartbeat_scheduled_wake_prefix_text: str = (
        "You have unread PR comments. Please fetch them with list_unread_feedback."
    )
    host_heartbeat_schedule_max_active_per_session: int | None = Field(default=None, ge=1)
    host_heartbeat_schedule_max_active_total: int | None = Field(default=None, ge=1)
    host_heartbeat_schedule_min_interval_seconds: float | None = Field(default=None, gt=0)

    @field_validator("host_heartbeat_directory_paths", mode="before")
    @classmethod
    def normalize_host_heartbeat_directory_paths(cls, value: object) -> object:
        if value is None:
            return []
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, (list, tuple)):
            return value
        normalized: list[str] = []
        for item in value:
            text = str(item).strip()
            if not text:
                continue
            normalized.append(DirectoryPath(segments=text).as_posix())
        return normalized

    @model_validator(mode="after")
    def apply_runtime_path_defaults(self) -> HostHeartbeatWorkerGlobalConfig:
        if self.host_heartbeat_lock_path is None:
            self.host_heartbeat_lock_path = self.runtime_dir / "host-heartbeat.lock"
        if self.host_heartbeat_session_cooldown_path is None:
            self.host_heartbeat_session_cooldown_path = (
                self.runtime_dir / "host-heartbeat-session-cooldown.json"
            )
        if self.host_heartbeat_wake_state_path is None:
            self.host_heartbeat_wake_state_path = self.runtime_dir / "host-heartbeat-wake-state.json"
        return self


class IssueOrchestratorWorkerGlobalConfig(BaseModel):
    runtime_dir: Path = Path("runtime")
    directories: list[str] = Field(default_factory=list)
    poll_interval_seconds: float = Field(default=30.0, gt=0)
    jitter_seconds: float = Field(default=5.0, ge=0)
    cooldown_seconds: float = Field(default=60.0, ge=0)
    lock_path: Path | None = None
    busy_check_script_path: Path = Path("scripts/check_session_vm_busy.sh")
    resume_script_path: Path = Path("scripts/resume_issue_agent.sh")
    wake_state_path: Path | None = None
    vm_user: str = "agent"
    resume_timeout_seconds: float = Field(default=1200.0, gt=0)

    @field_validator("directories", mode="before")
    @classmethod
    def normalize_directories(cls, value: object) -> object:
        if value is None:
            return []
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, (list, tuple)):
            return value
        normalized: list[str] = []
        for item in value:
            text = str(item).strip()
            if not text:
                continue
            normalized.append(DirectoryPath(segments=text).as_posix())
        return normalized

    @model_validator(mode="after")
    def apply_runtime_path_defaults(self) -> IssueOrchestratorWorkerGlobalConfig:
        if self.lock_path is None:
            self.lock_path = self.runtime_dir / "issue-orchestrator.lock"
        if self.wake_state_path is None:
            self.wake_state_path = self.runtime_dir / "issue-orchestrator-wake-state.json"
        return self


class DiscordIngestWorkerGlobalConfig(BaseModel):
    runtime_dir: Path = Path("runtime")
    directories: list[str] = Field(default_factory=list)
    poll_interval_seconds: float = Field(default=30.0, gt=0)
    jitter_seconds: float = Field(default=5.0, ge=0)
    backoff_seconds: float = Field(default=60.0, ge=0)
    lock_path: Path | None = None

    @field_validator("directories", mode="before")
    @classmethod
    def normalize_directories(cls, value: object) -> object:
        if value is None:
            return []
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, (list, tuple)):
            return value
        normalized: list[str] = []
        for item in value:
            text = str(item).strip()
            if not text:
                continue
            normalized.append(DirectoryPath(segments=text).as_posix())
        return normalized

    @model_validator(mode="after")
    def apply_runtime_path_defaults(self) -> DiscordIngestWorkerGlobalConfig:
        if self.lock_path is None:
            self.lock_path = self.runtime_dir / "discord-ingest.lock"
        return self


class TriggerOrchestratorWorkerGlobalConfig(BaseModel):
    runtime_dir: Path = Path("runtime")
    poll_interval_seconds: float = Field(default=30.0, gt=0)
    jitter_seconds: float = Field(default=5.0, ge=0)
    lock_path: Path | None = None
    event_queue_store_path: Path | None = None
    event_queue_cursor_store_path: Path | None = None
    gate_execution_store_path: Path | None = None
    gate_cursor_store_path: Path | None = None
    effect_execution_store_path: Path | None = None
    effect_cursor_store_path: Path | None = None
    effect_lock_path: Path | None = None
    triggers: list[TriggerDefinitionDiscriminated] = Field(default_factory=list)
    gates: list[GateDefinitionDiscriminated] = Field(default_factory=list)
    edges: list[EdgeDefinition] = Field(default_factory=list)
    effects: list[EffectDefinitionDiscriminated] = Field(default_factory=list)
    queries: list[QueryDefinitionDiscriminated] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unique_graph_node_ids(self) -> TriggerOrchestratorWorkerGlobalConfig:
        node_ids = (
            [node.id for node in self.triggers]
            + [node.id for node in self.gates]
            + [node.id for node in self.effects]
            + [node.id for node in self.queries]
        )
        duplicates = sorted(node_id for node_id, count in Counter(node_ids).items() if count > 1)
        if duplicates:
            duplicate_list = ", ".join(duplicates)
            raise ValueError(
                f"Node IDs must be globally unique across triggers, gates, effects, and queries: {duplicate_list}"
            )
        return self

    @model_validator(mode="after")
    def validate_graph_edges(self) -> TriggerOrchestratorWorkerGlobalConfig:
        nodes_by_id = {node.id: node for node in (*self.triggers, *self.gates, *self.effects, *self.queries)}
        node_outputs = index_declared_outputs(self.triggers, self.gates, self.effects, self.queries)
        inbound_edges = build_inbound_edge_index(self.edges)

        missing_endpoints: list[str] = []
        self_edges: list[str] = []
        for edge in self.edges:
            source_known = edge.source_id in nodes_by_id
            target_known = edge.target_id in nodes_by_id
            if edge.source_id == edge.target_id:
                self_edges.append(edge.source_id)
            if not source_known:
                missing_endpoints.append(edge.source_id)
            if not target_known:
                missing_endpoints.append(edge.target_id)

        missing_endpoints = sorted(set(missing_endpoints))
        if missing_endpoints:
            missing_list = ", ".join(missing_endpoints)
            raise ValueError(f"Edge endpoints must reference existing node IDs: {missing_list}")

        self_edges = sorted(set(self_edges))
        if self_edges:
            self_edge_list = ", ".join(self_edges)
            raise ValueError(f"Edges cannot connect a node to itself: {self_edge_list}")

        inbound_errors: list[str] = []
        for node in self.triggers:
            inbound_count = len(inbound_edges.get(node.id, ()))
            if inbound_count != 0:
                inbound_errors.append(f"trigger {node.id} must have 0 inbound edges, found {inbound_count}")
        for gate in self.gates:
            inbound_count = len(inbound_edges.get(gate.id, ()))
            if gate.type == "gate:filter" and inbound_count != 1:
                inbound_errors.append(f"gate:filter {gate.id} must have exactly 1 inbound edge, found {inbound_count}")
            elif gate.type == "gate:map" and inbound_count != 1:
                inbound_errors.append(f"gate:map {gate.id} must have exactly 1 inbound edge, found {inbound_count}")
            elif gate.type == "gate:merge" and inbound_count < 1:
                inbound_errors.append(f"gate:merge {gate.id} must have at least 1 inbound edge, found {inbound_count}")
            elif gate.type == "gate:await-agent" and inbound_count != 1:
                inbound_errors.append(f"gate:await-agent {gate.id} must have exactly 1 inbound edge, found {inbound_count}")
            elif gate.type == "gate:for":
                if inbound_count < 1:
                    inbound_errors.append(f"gate:for {gate.id} must have at least 1 inbound edge, found {inbound_count}")
                entry_edges = [
                    edge for edge in inbound_edges.get(gate.id, ()) if not bool(getattr(edge, "allow_cycle", False))
                ]
                if len(entry_edges) != 1:
                    inbound_errors.append(
                        f"gate:for {gate.id} must have exactly 1 non-cyclic inbound edge, found {len(entry_edges)}"
                    )
        for effect in self.effects:
            inbound_count = len(inbound_edges.get(effect.id, ()))
            if inbound_count != 1:
                inbound_errors.append(f"effect {effect.id} must have exactly 1 inbound edge, found {inbound_count}")
        for query in self.queries:
            inbound_count = len(inbound_edges.get(query.id, ()))
            if inbound_count != 1:
                inbound_errors.append(f"query {query.id} must have exactly 1 inbound edge, found {inbound_count}")
        if inbound_errors:
            raise ValueError("; ".join(inbound_errors))

        for gate in self.gates:
            upstream_ids = [edge.source_id for edge in inbound_edges.get(gate.id, ())]
            if gate.type == "gate:filter":
                upstream_outputs = node_outputs.get(upstream_ids[0], {})
                if gate.outputs is None:
                    gate.outputs = upstream_outputs
                actual_outputs = dict(gate.outputs or {})
                if not actual_outputs:
                    gate.outputs = upstream_outputs
                    actual_outputs = upstream_outputs
                if actual_outputs != upstream_outputs:
                    raise ValueError(f"gate:filter {gate.id} outputs must match its single upstream outputs")
            elif gate.type == "gate:await-agent":
                upstream_node = nodes_by_id[upstream_ids[0]]
                if upstream_node.type != "effect:agent:prompt":
                    raise ValueError(f"gate:await-agent {gate.id} inbound edge must come from effect:agent:prompt")
                completion_outputs = effective_node_output_schema({"type": "trigger:agent:run:completed"})
                if gate.outputs is None:
                    gate.outputs = completion_outputs
                actual_outputs = dict(gate.outputs or {})
                if not actual_outputs:
                    gate.outputs = completion_outputs
                    actual_outputs = completion_outputs
                if actual_outputs != completion_outputs:
                    raise ValueError(f"gate:await-agent {gate.id} outputs must match agent completion outputs")
            elif gate.type == "gate:merge":
                upstream_outputs = [node_outputs.get(upstream_id, {}) for upstream_id in upstream_ids]
                expected_outputs = merge_declared_output_intersection(*upstream_outputs)
                if gate.outputs is None:
                    gate.outputs = expected_outputs
                actual_outputs = dict(gate.outputs or {})
                if not actual_outputs:
                    gate.outputs = expected_outputs
                    actual_outputs = expected_outputs
                if actual_outputs != expected_outputs:
                    raise ValueError(
                        f"gate:merge {gate.id} outputs must equal the compatible intersection of upstream outputs"
                    )
            elif gate.type == "gate:for":
                expected_outputs = effective_node_output_schema(gate)
                if gate.outputs is None:
                    gate.outputs = expected_outputs
                actual_outputs = dict(gate.outputs or {})
                if not actual_outputs:
                    gate.outputs = expected_outputs
                    actual_outputs = expected_outputs
                if actual_outputs != expected_outputs:
                    raise ValueError(f"gate:for {gate.id} outputs must match for-loop outputs")

                outbound_edges = [edge for edge in self.edges if edge.source_id == gate.id]
                body_edges = [edge for edge in outbound_edges if edge.source_port == "body"]
                done_edges = [edge for edge in outbound_edges if edge.source_port == "done"]
                unlabeled_edges = [edge for edge in outbound_edges if edge.source_port is None]
                invalid_ports = [
                    edge.source_port for edge in outbound_edges if edge.source_port not in {None, "body", "done"}
                ]
                if len(body_edges) != 1:
                    raise ValueError(f"gate:for {gate.id} must have exactly 1 outbound body edge")
                if len(done_edges) != 1:
                    raise ValueError(f"gate:for {gate.id} must have exactly 1 outbound done edge")
                if unlabeled_edges:
                    raise ValueError(f"gate:for {gate.id} outbound edges must declare source_port=body or source_port=done")
                if invalid_ports:
                    invalid = ", ".join(sorted({port for port in invalid_ports if port is not None}))
                    raise ValueError(f"gate:for {gate.id} uses invalid source_port value(s): {invalid}")
        return self

    @model_validator(mode="after")
    def apply_runtime_path_defaults(self) -> TriggerOrchestratorWorkerGlobalConfig:
        if self.lock_path is None:
            self.lock_path = self.runtime_dir / "trigger-orchestrator.lock"
        if self.event_queue_store_path is None:
            self.event_queue_store_path = self.runtime_dir / "trigger-orchestrator-events.json"
        if self.event_queue_cursor_store_path is None:
            self.event_queue_cursor_store_path = self.runtime_dir / "trigger-orchestrator-cursors.json"
        if self.gate_execution_store_path is None:
            self.gate_execution_store_path = self.runtime_dir / "gate-orchestrator-executions.json"
        if self.gate_cursor_store_path is None:
            self.gate_cursor_store_path = self.runtime_dir / "gate-orchestrator-cursors.json"
        if self.effect_execution_store_path is None:
            self.effect_execution_store_path = self.runtime_dir / "effect-orchestrator-executions.json"
        if self.effect_cursor_store_path is None:
            self.effect_cursor_store_path = self.runtime_dir / "effect-orchestrator-cursors.json"
        if self.effect_lock_path is None:
            self.effect_lock_path = self.runtime_dir / "effect-orchestrator.lock"
        return self


def load_config(path: str | Path) -> GatewayConfig:
    config_path = Path(path)
    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    return GatewayConfig.model_validate(data)

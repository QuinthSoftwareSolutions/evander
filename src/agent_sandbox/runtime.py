from __future__ import annotations

import os
from datetime import UTC, datetime
from dataclasses import dataclass
from typing import Callable
from pathlib import Path

from agent_sandbox.messaging.agent_prompt_store import AgentPromptStore
from agent_sandbox.audit import AuditLogger
from agent_sandbox.directories import DirectoryStore
from agent_sandbox.models import (
    ApprovalRequest,
    DraftMessage,
    ToolName,
)
from agent_sandbox.migrations import migrate_local_issues_to_unified_schema
from agent_sandbox.config import GatewayConfig, MailboxConfig
from agent_sandbox.directory_config import load_root_gateway_config
from agent_sandbox.repositories.cursors import EmailCursorRepository, JsonEmailCursorRepository
from agent_sandbox.email.gmail_provider import GmailEmailProvider
from agent_sandbox.email.graph_provider import GraphEmailProvider
from agent_sandbox.email.providers import JsonEmailProvider, MultiMailboxEmailProvider
from agent_sandbox.policy import PolicyEngine
from agent_sandbox.policy_config import PolicyConfigResolver
from agent_sandbox.provider_config import ProviderConfigResolver
from agent_sandbox.host_config import HostConfig
from agent_sandbox.retell_phone import RetellPhoneCallService, RetellPhoneCallStore
from agent_sandbox.reviews.request_service import (
    ReviewRequestService,
    _build_review_request_service,
)
from agent_sandbox.repositories.records import (
    DiscordRecordsRepository,
    EmailRecordsRepository,
    JsonDiscordRecordsRepository,
    JsonEmailRecordsRepository,
    JsonTeamsRecordsRepository,
    TeamsRecordsRepository,
)
from agent_sandbox.repositories.session_manifests import (
    JsonSessionManifestRepository,
    SessionManifestRepository,
    as_session_manifest_repository,
)
from agent_sandbox.repositories.session_tool_approval_leases import (
    JsonSessionToolApprovalLeaseRepository,
    SessionToolApprovalLeaseRepository,
)
from agent_sandbox.repositories.script_run_requests import (
    ScriptRunRequestRepository,
    ScriptRunRequestStore,
)
from agent_sandbox.services import (
    AzureDevOpsPrReplyProvider,
    AzureDevOpsPrQueryProvider,
    AzureDevOpsPrPublishProvider,
    DiscordBotApiService,
    DiscordInboxService,
    DiscordService,
    EmailService,
    GraphTeamsService,
    GitHubPrReplyProvider,
    GitHubPrPublishProvider,
    PrFeedbackService,
    PrReplyService,
    PrQueryService,
    PrPublishService,
    StubDiscordService,
    StubTeamsService,
    TeamsService,
)
from agent_sandbox.storage import JsonStore
from agent_sandbox.messaging.teams_auth import (
    JsonTeamsIdentityCredentialStore,
    MicrosoftDeviceCodeAuthConfig,
    TeamsDeviceCodeTokenProvider,
)
from agent_sandbox.messaging.wake_store import WakeRequestRepository, WakeRequestStore
from agent_sandbox.teams_meeting import (
    TeamsMeetingBridgeClient,
    TeamsMeetingStore,
    build_teams_meeting_bridge_client,
)

CAPABILITY_PROFILE_ALLOWED_TOOLS: dict[str, tuple[ToolName, ...]] = {
    "read-only": (
        ToolName.ASSISTANT_READ_STATUS,
        ToolName.ASSISTANT_LIST_AGENTS,
        ToolName.ASSISTANT_INSPECT_WORKFLOWS,
        ToolName.ASSISTANT_READ_CONFIG,
        ToolName.ASSISTANT_POST_CHAT_REPLY,
    ),
    "operator-limited": (
        ToolName.ASSISTANT_READ_STATUS,
        ToolName.ASSISTANT_LIST_AGENTS,
        ToolName.ASSISTANT_INSPECT_WORKFLOWS,
        ToolName.ASSISTANT_READ_CONFIG,
        ToolName.ASSISTANT_UPSERT_CONFIG,
        ToolName.ASSISTANT_CREATE_WORKFLOW,
        ToolName.ASSISTANT_POST_CHAT_REPLY,
    ),
    "full-admin": (
        ToolName.ASSISTANT_READ_STATUS,
        ToolName.ASSISTANT_LIST_AGENTS,
        ToolName.ASSISTANT_INSPECT_WORKFLOWS,
        ToolName.ASSISTANT_READ_CONFIG,
        ToolName.ASSISTANT_UPSERT_CONFIG,
        ToolName.ASSISTANT_CREATE_WORKFLOW,
        ToolName.ASSISTANT_RUN_SAFE_OPERATION,
        ToolName.ASSISTANT_POST_CHAT_REPLY,
    ),
}


def allowed_tools_for_capability_profile(profile: str | None) -> list[ToolName]:
    normalized = str(profile or "").strip().lower()
    if not normalized:
        return []
    return list(CAPABILITY_PROFILE_ALLOWED_TOOLS.get(normalized, ()))


@dataclass(slots=True)
class RuntimeContext:
    config: GatewayConfig
    policy_config: PolicyConfigResolver
    provider_config: ProviderConfigResolver
    audit: AuditLogger
    policy: PolicyEngine
    email_service: EmailService
    discord_service: DiscordService
    teams_service: TeamsService
    discord_inbox_service: DiscordInboxService
    pr_feedback_service: PrFeedbackService
    pr_reply_service: PrReplyService
    pr_query_service: PrQueryService
    pr_publish_service: PrPublishService
    review_request_service: ReviewRequestService
    session_repository: SessionManifestRepository
    approvals: JsonStore[ApprovalRequest]
    directories: DirectoryStore
    drafts: JsonStore[DraftMessage]
    emails: EmailRecordsRepository
    email_cursors: EmailCursorRepository
    discord_commands: DiscordRecordsRepository
    teams_messages: TeamsRecordsRepository
    teams_user_chat_cache: dict[str, str]
    wake_store: WakeRequestRepository
    agent_prompt_store: AgentPromptStore
    script_run_request_store: ScriptRunRequestRepository
    retell_phone_call_store: RetellPhoneCallStore
    teams_meeting_store: TeamsMeetingStore
    teams_meeting_bridge_client: TeamsMeetingBridgeClient
    tool_approval_leases: SessionToolApprovalLeaseRepository | None = None

    def resolve_provider_config_for_session(self, session_id: str) -> GatewayConfig:
        return self.provider_config.resolve_for_session_id(session_id)

    def email_service_for_session(self, session_id: str) -> EmailService:
        if not isinstance(self.email_service, EmailService):
            return self.email_service
        resolved_config = self.resolve_provider_config_for_session(session_id)
        if resolved_config == self.config:
            return self.email_service
        return EmailService(
            _build_email_provider(resolved_config, self.emails, self.drafts, self.email_cursors)
        )

    def discord_service_for_session(self, session_id: str) -> DiscordService:
        if not isinstance(self.discord_service, (StubDiscordService, DiscordBotApiService)):
            return self.discord_service
        resolved_config = self.resolve_provider_config_for_session(session_id)
        return _build_discord_service(
            resolved_config,
            self.discord_commands,
            self.session_repository,
            self.policy_config,
            session_id=session_id,
        )

    def discord_inbox_service_for_session(self, session_id: str) -> DiscordInboxService:
        return self.discord_inbox_service

    def pr_reply_service_for_session(self, session_id: str) -> PrReplyService:
        if not isinstance(self.pr_reply_service, PrReplyService):
            return self.pr_reply_service
        resolved_config = self.resolve_provider_config_for_session(session_id)
        if resolved_config == self.config:
            return self.pr_reply_service
        return _build_pr_reply_service(resolved_config)

    def pr_publish_service_for_session(self, session_id: str) -> PrPublishService:
        if not isinstance(self.pr_publish_service, PrPublishService):
            return self.pr_publish_service
        resolved_config = self.resolve_provider_config_for_session(session_id)
        if resolved_config == self.config:
            return self.pr_publish_service
        return _build_pr_publish_service(resolved_config)

    def pr_query_service_for_session(self, session_id: str) -> PrQueryService:
        if not isinstance(self.pr_query_service, PrQueryService):
            return self.pr_query_service
        resolved_config = self.resolve_provider_config_for_session(session_id)
        if resolved_config == self.config:
            return self.pr_query_service
        return _build_pr_query_service(resolved_config)

    def review_request_service_for_session(self, session_id: str) -> ReviewRequestService:
        if not isinstance(self.review_request_service, ReviewRequestService):
            return self.review_request_service
        resolved_config = self.resolve_provider_config_for_session(session_id)
        if resolved_config == self.config:
            return self.review_request_service
        return _build_review_request_service(resolved_config, self.session_repository)

    def retell_phone_call_service_for_session(self, session_id: str) -> RetellPhoneCallService:
        resolved_config = self.resolve_provider_config_for_session(session_id)
        contacts_config = self.provider_config.resolve_contacts_for_session_id(session_id)
        return RetellPhoneCallService(
            config=resolved_config,
            contacts_config=contacts_config,
            store=self.retell_phone_call_store,
        )

    def teams_service_for_session(self, session_id: str) -> TeamsService:
        if not isinstance(self.teams_service, (StubTeamsService, GraphTeamsService)):
            return self.teams_service
        session = self.session_repository.get_session(session_id)
        if session is None:
            raise ValueError(f"unknown session: {session_id}")
        resolved_config = self.resolve_provider_config_for_session(session_id)
        if resolved_config == self.config and not isinstance(self.teams_service, GraphTeamsService):
            return self.teams_service
        return _build_teams_service(
            resolved_config,
            self.teams_messages,
            self.session_repository,
            directory_path=session.directory_path,
            allowed_channel_targets_provider=lambda: (
                _resolve_allowed_teams_channel_targets_for_session(
                    self.session_repository,
                    session_id,
                    policy_config=self.policy_config,
                )
            ),
            allowed_user_targets_provider=lambda: (
                _resolve_allowed_teams_user_targets_for_session(
                    self.session_repository,
                    session_id,
                    policy_config=self.policy_config,
                )
            ),
            user_chat_cache=self.teams_user_chat_cache,
        )


def _build_runtime_context(config: GatewayConfig) -> RuntimeContext:
    migrate_local_issues_to_unified_schema(config)
    directories = DirectoryStore(config.directories_store_path)
    directories.ensure_root()
    host_config = HostConfig.from_legacy_gateway_config(config)
    config = load_root_gateway_config(
        directories,
        runtime_dir=config.runtime_dir,
        host_config=host_config,
        base_gateway_config=config,
    )
    session_repository = JsonSessionManifestRepository(config.session_store_path)
    approvals = JsonStore(config.approval_store_path, ApprovalRequest)
    tool_approval_leases = JsonSessionToolApprovalLeaseRepository.from_runtime_dir(
        config.runtime_dir
    )
    drafts = JsonStore(config.draft_store_path, DraftMessage)
    emails = JsonEmailRecordsRepository(config.email_store_path)
    email_cursors = JsonEmailCursorRepository(config.email_cursor_store_path)
    policy_config = PolicyConfigResolver(
        config, directory_store=directories, session_store=session_repository
    )
    provider_config = ProviderConfigResolver(
        config, directory_store=directories, session_store=session_repository
    )
    discord_commands = JsonDiscordRecordsRepository(config.discord_command_store_path)
    teams_messages = JsonTeamsRecordsRepository(config.teams_message_store_path)
    teams_user_chat_cache: dict[str, str] = {}
    discord_inbox_service = DiscordInboxService(config.runtime_dir / "discord-inbox-store.json")
    wake_store = WakeRequestStore(config.runtime_dir / "wake-requests-store.json")
    agent_prompt_store = AgentPromptStore(config.runtime_dir / "agent-prompt-store.json")
    script_run_request_store = ScriptRunRequestStore(
        config.runtime_dir / "script-run-requests-store.json"
    )
    retell_phone_call_store = RetellPhoneCallStore(
        config.runtime_dir / "retell-phone-calls-store.json"
    )
    teams_meeting_store = TeamsMeetingStore(config.teams_meeting_store_path)
    teams_meeting_bridge_client = build_teams_meeting_bridge_client(config)
    root_provider_config = provider_config.resolve_for_directory("root")
    discord_service = _build_discord_service(
        root_provider_config, discord_commands, session_repository, policy_config
    )
    teams_service = _build_teams_service(
        root_provider_config,
        teams_messages,
        session_repository,
        directory_path="root",
        policy_config=policy_config,
        user_chat_cache=teams_user_chat_cache,
    )

    runtime_context = RuntimeContext(
        config=config,
        policy_config=policy_config,
        provider_config=provider_config,
        audit=AuditLogger(config.audit_log_path),
        policy=PolicyEngine(
            config,
            session_repository,
            approvals,
            policy_config=policy_config,
            tool_approval_leases=tool_approval_leases,
        ),
        email_service=EmailService(
            _build_email_provider(root_provider_config, emails, drafts, email_cursors)
        ),
        discord_service=discord_service,
        teams_service=teams_service,
        discord_inbox_service=discord_inbox_service,
        pr_feedback_service=PrFeedbackService(
            config.runtime_dir / "pr-feedback-store.json",
            ignored_feedback_store_path=config.runtime_dir / "ignored-feedback-ids.json",
        ),
        pr_reply_service=_build_pr_reply_service(root_provider_config),
        pr_query_service=_build_pr_query_service(root_provider_config),
        pr_publish_service=_build_pr_publish_service(root_provider_config),
        review_request_service=_build_review_request_service(
            root_provider_config, session_repository
        ),
        session_repository=session_repository,
        approvals=approvals,
        directories=directories,
        drafts=drafts,
        emails=emails,
        email_cursors=email_cursors,
        discord_commands=discord_commands,
        teams_messages=teams_messages,
        teams_user_chat_cache=teams_user_chat_cache,
        wake_store=wake_store,
        agent_prompt_store=agent_prompt_store,
        script_run_request_store=script_run_request_store,
        retell_phone_call_store=retell_phone_call_store,
        teams_meeting_store=teams_meeting_store,
        teams_meeting_bridge_client=teams_meeting_bridge_client,
        tool_approval_leases=tool_approval_leases,
    )
    from agent_sandbox.approval_notifications import ApprovalNotificationDispatcher

    runtime_context.policy.set_approval_notification_dispatcher(
        ApprovalNotificationDispatcher(runtime=runtime_context, project_dir=Path.cwd())
    )
    return runtime_context


def build_runtime(config: GatewayConfig) -> RuntimeContext:

    from agent_sandbox.app_bootstrap import build_runtime as build_bootstrapped_runtime

    return build_bootstrapped_runtime(config)


def _build_email_provider(
    config: GatewayConfig,
    emails: EmailRecordsRepository,
    drafts: JsonStore[DraftMessage],
    email_cursors: EmailCursorRepository,
) -> JsonEmailProvider | MultiMailboxEmailProvider:
    mailbox_configs = config.email_mailboxes or [
        MailboxConfig(mailbox_id="default", provider="json_store", account_identifier="default")
    ]

    if all(mailbox.provider == "json_store" for mailbox in mailbox_configs):
        return JsonEmailProvider(emails, drafts)

    providers_by_mailbox: dict[
        str, JsonEmailProvider | GmailEmailProvider | GraphEmailProvider
    ] = {}
    for mailbox in mailbox_configs:
        if mailbox.provider == "json_store":
            providers_by_mailbox[mailbox.mailbox_id] = JsonEmailProvider(emails, drafts)
            continue
        if mailbox.provider == "gmail":
            _validate_mailbox_access_token_env_var(mailbox)
            providers_by_mailbox[mailbox.mailbox_id] = GmailEmailProvider(
                account_identifier=mailbox.account_identifier,
                folder_mapping=mailbox.folder_mapping,
                auth_secret_refs=mailbox.auth_secret_refs,
                timeout_seconds=config.email_provider_timeout_seconds,
                cursor_store=email_cursors,
            )
            continue
        if mailbox.provider == "graph":
            _validate_mailbox_access_token_env_var(mailbox)
            providers_by_mailbox[mailbox.mailbox_id] = GraphEmailProvider(
                account_identifier=mailbox.account_identifier,
                folder_mapping=mailbox.folder_mapping,
                auth_secret_refs=mailbox.auth_secret_refs,
                timeout_seconds=config.email_provider_timeout_seconds,
                cursor_store=email_cursors,
            )
            continue
        raise RuntimeError(f"Unsupported email provider: {mailbox.provider!r}")

    return MultiMailboxEmailProvider(providers_by_mailbox)


def _validate_mailbox_access_token_env_var(mailbox: MailboxConfig) -> None:
    env_var_name = mailbox.auth_secret_refs.get("access_token", "").strip()
    if not env_var_name:
        raise RuntimeError(
            f"email mailbox {mailbox.mailbox_id!r} using provider {mailbox.provider!r} "
            "requires auth_secret_refs.access_token to be configured"
        )

    access_token = os.environ.get(env_var_name, "").strip()
    if not access_token:
        raise RuntimeError(
            f"email mailbox {mailbox.mailbox_id!r} using provider {mailbox.provider!r} "
            f"requires a non-empty access token in environment variable {env_var_name!r}"
        )


def _build_discord_service(
    config: GatewayConfig,
    command_store: DiscordRecordsRepository,
    sessions: SessionManifestRepository,
    policy_config: PolicyConfigResolver,
    *,
    session_id: str | None = None,
) -> DiscordService:
    if config.discord_backend == "stub":
        return StubDiscordService(
            command_store,
            read_reaction_emoji=config.discord_read_reaction_emoji,
        )
    if config.discord_backend == "bot_api":
        env_var_name = config.discord_bot_token_env.strip()
        if not env_var_name:
            raise RuntimeError(
                "discord backend 'bot_api' requires config.discord_bot_token_env to be set"
            )
        token = os.environ.get(env_var_name, "").strip()
        if not token:
            raise RuntimeError(
                "discord backend 'bot_api' requires a bot token in environment variable "
                f"{env_var_name!r}"
            )
        return DiscordBotApiService(
            command_store,
            token=token,
            api_base_url=config.discord_api_base_url,
            poll_limit=config.discord_poll_limit,
            timeout_seconds=config.discord_timeout_seconds,
            retry_count=config.discord_retry_count,
            retry_base_backoff_seconds=config.discord_retry_base_backoff_seconds,
            retry_max_backoff_seconds=config.discord_retry_max_backoff_seconds,
            allowed_channel_ids_provider=lambda: _resolve_allowed_discord_channel_ids(
                sessions,
                config,
                policy_config=policy_config,
                session_id=session_id,
            ),
            operator_user_ids_provider=(
                policy_config.operator_user_ids_for_active_sessions
                if session_id is None
                else lambda: policy_config.operator_user_ids_for_session_id(session_id)
            ),
            include_operator_dms_provider=(
                policy_config.include_operator_dms_for_active_sessions
                if session_id is None
                else lambda: policy_config.include_operator_dms_for_session_id(session_id)
            ),
            read_reaction_emoji=config.discord_read_reaction_emoji,
        )
    raise RuntimeError(
        f"Unsupported discord backend {config.discord_backend!r}. Expected one of: 'stub', 'bot_api'."
    )


def _build_teams_service(
    config: GatewayConfig,
    message_store: TeamsRecordsRepository,
    sessions: SessionManifestRepository,
    *,
    directory_path: str | None = None,
    policy_config: PolicyConfigResolver | None = None,
    allowed_channel_targets_provider: Callable[[], list[str]] | None = None,
    allowed_user_targets_provider: Callable[[], list[str]] | None = None,
    user_chat_cache: dict[str, str] | None = None,
) -> TeamsService:
    if config.teams_backend == "stub":
        return StubTeamsService(
            message_store,
            allowed_user_targets_provider=allowed_user_targets_provider or (lambda: []),
            read_reaction_emoji=config.teams_read_reaction_emoji,
        )
    if config.teams_backend == "graph_api":
        env_var_name = config.teams_graph_access_token_env.strip()
        if not env_var_name:
            raise RuntimeError(
                "teams backend 'graph_api' requires config.teams_graph_access_token_env to be set"
            )
        token = os.environ.get(env_var_name, "").strip()
        if not token:
            raise RuntimeError(
                "teams backend 'graph_api' requires an access token in environment variable "
                f"{env_var_name!r}"
            )
        return GraphTeamsService(
            message_store,
            token=token,
            api_base_url=config.teams_graph_api_base_url,
            poll_limit=config.teams_poll_limit,
            history_page_cap=config.teams_history_page_cap,
            history_deadline_seconds=config.teams_history_deadline_seconds,
            timeout_seconds=config.teams_timeout_seconds,
            retry_count=config.teams_retry_count,
            retry_base_backoff_seconds=config.teams_retry_base_backoff_seconds,
            retry_max_backoff_seconds=config.teams_retry_max_backoff_seconds,
            allowed_channel_targets_provider=allowed_channel_targets_provider
            or (
                lambda: _resolve_allowed_teams_channel_targets(
                    sessions,
                    policy_config=policy_config,
                )
            ),
            allowed_user_targets_provider=allowed_user_targets_provider or (lambda: []),
            user_chat_cache=user_chat_cache,
            user_chat_cache_namespace=(
                f"graph_api:{config.teams_graph_access_token_env}:{directory_path or 'root'}"
            ),
            read_reaction_emoji=config.teams_read_reaction_emoji,
        )
    if config.teams_backend == "delegated_graph_api":
        token_provider = _build_delegated_teams_token_provider(config)
        return GraphTeamsService(
            message_store,
            token_provider=token_provider.get_bearer_token,
            api_base_url=config.teams_graph_api_base_url,
            poll_limit=config.teams_poll_limit,
            history_page_cap=config.teams_history_page_cap,
            history_deadline_seconds=config.teams_history_deadline_seconds,
            timeout_seconds=config.teams_timeout_seconds,
            retry_count=config.teams_retry_count,
            retry_base_backoff_seconds=config.teams_retry_base_backoff_seconds,
            retry_max_backoff_seconds=config.teams_retry_max_backoff_seconds,
            allowed_channel_targets_provider=allowed_channel_targets_provider
            or (
                lambda: _resolve_allowed_teams_channel_targets(
                    sessions,
                    policy_config=policy_config,
                )
            ),
            allowed_user_targets_provider=allowed_user_targets_provider or (lambda: []),
            user_chat_cache=user_chat_cache,
            user_chat_cache_namespace=(
                f"delegated:{config.teams_delegated_identity_id or 'default'}:"
                f"{directory_path or 'root'}"
            ),
            read_reaction_emoji=config.teams_read_reaction_emoji,
        )
    raise RuntimeError(
        f"Unsupported teams backend {config.teams_backend!r}. Expected one of: 'stub', 'graph_api', 'delegated_graph_api'."
    )


def _build_delegated_teams_token_provider(config: GatewayConfig) -> TeamsDeviceCodeTokenProvider:
    identity_id = str(config.teams_delegated_identity_id or "").strip()
    if not identity_id:
        raise RuntimeError(
            "teams backend 'delegated_graph_api' requires teams_delegated_identity_id"
        )
    client_id = str(config.teams_delegated_client_id or "").strip()
    if not client_id:
        raise RuntimeError("teams backend 'delegated_graph_api' requires teams_delegated_client_id")

    auth_config = MicrosoftDeviceCodeAuthConfig(
        client_id=client_id,
        tenant_id=config.teams_delegated_tenant_id,
        scopes=config.teams_delegated_scopes,
        authority_base_url=config.teams_delegated_authority_base_url,
        timeout_seconds=config.teams_timeout_seconds,
    )
    credential_store = JsonTeamsIdentityCredentialStore(
        config.teams_delegated_credential_store_path,
        identity_id,
    )
    return TeamsDeviceCodeTokenProvider(auth_config, credential_store)


def _build_pr_reply_service(config: GatewayConfig) -> PrReplyService:
    github_token = os.environ.get(config.github_token_env, "").strip()
    azure_pat = os.environ.get(config.azure_devops_pr_pat_env, "").strip()

    providers = {}
    if github_token:
        providers["github"] = GitHubPrReplyProvider(
            token=github_token,
            timeout_seconds=config.email_provider_timeout_seconds,
        )
    if azure_pat:
        providers["azure_devops"] = AzureDevOpsPrReplyProvider(
            organization_url=config.azure_devops_organization_url,
            project=config.azure_devops_project,
            pat=azure_pat,
            timeout_seconds=config.email_provider_timeout_seconds,
        )
    return PrReplyService(
        providers=providers,
        idempotency_store_path=config.runtime_dir / "pr-reply-idempotency.json",
        ignored_feedback_store_path=config.runtime_dir / "ignored-feedback-ids.json",
    )


def _build_pr_publish_service(config: GatewayConfig) -> PrPublishService:
    github_token = os.environ.get(config.github_token_env, "").strip()
    azure_pat = os.environ.get(config.azure_devops_pr_pat_env, "").strip()

    providers = {}
    if github_token:
        providers["github"] = GitHubPrPublishProvider(
            token=github_token,
            timeout_seconds=config.email_provider_timeout_seconds,
        )
    if azure_pat:
        providers["azure_devops"] = AzureDevOpsPrPublishProvider(
            organization_url=config.azure_devops_organization_url,
            project=config.azure_devops_project,
            pat=azure_pat,
            timeout_seconds=config.email_provider_timeout_seconds,
        )
    return PrPublishService(
        providers=providers,
        idempotency_store_path=config.runtime_dir / "pr-publish-idempotency.json",
        ignored_feedback_store_path=config.runtime_dir / "ignored-feedback-ids.json",
    )


def _build_pr_query_service(config: GatewayConfig) -> PrQueryService:
    azure_pat = os.environ.get(config.azure_devops_pr_pat_env, "").strip()
    providers = {}
    if azure_pat:
        providers["azure_devops"] = AzureDevOpsPrQueryProvider(
            organization_url=config.azure_devops_organization_url,
            project=config.azure_devops_project,
            pat=azure_pat,
            timeout_seconds=config.email_provider_timeout_seconds,
        )
    return PrQueryService(providers=providers)


def _resolve_allowed_discord_channel_ids(
    session_store: SessionManifestRepository,
    config: GatewayConfig,
    *,
    policy_config: PolicyConfigResolver | None = None,
    session_id: str | None = None,
) -> list[str]:
    session_store = as_session_manifest_repository(session_store)
    if session_id is not None:
        if policy_config is not None:
            return policy_config.allowed_discord_channel_ids_for_session_id(session_id)
        session = session_store.get_session(session_id)
        if session is None:
            raise ValueError(f"unknown session: {session_id}")
        channel_ids = set(config.discord_operator_channel_allowlist)
        if ToolName.DISCORD_GET_COMMANDS in session.allowed_tools:
            channel_ids.update(session.discord_channel_allowlist)
        return sorted(channel_id.strip() for channel_id in channel_ids if channel_id.strip())

    now = datetime.now(UTC)
    if policy_config is None:
        channel_ids: set[str] = set()
        channel_ids.update(config.discord_operator_channel_allowlist)
        for session in session_store.list_active_sessions(now=now):
            if ToolName.DISCORD_GET_COMMANDS not in session.allowed_tools:
                continue
            channel_ids.update(session.discord_channel_allowlist)
        return sorted({channel_id.strip() for channel_id in channel_ids if channel_id.strip()})

    return policy_config.allowed_discord_channel_ids_for_active_sessions()


def _resolve_allowed_teams_channel_targets(
    session_store: SessionManifestRepository,
    *,
    policy_config: PolicyConfigResolver | None = None,
) -> list[str]:
    session_store = as_session_manifest_repository(session_store)
    now = datetime.now(UTC)
    targets: set[str] = set()
    for session in session_store.list_active_sessions(now=now):
        if ToolName.TEAMS_GET_MESSAGES not in session.allowed_tools:
            continue
        if policy_config is not None:
            targets.update(
                policy_config.resolved_teams_channel_targets_for_session(
                    session,
                    capability="read",
                )
            )
        else:
            targets.update(session.teams_channel_allowlist)
            targets.update(session.teams_chat_allowlist)
    return sorted(targets)


def _resolve_allowed_teams_channel_targets_for_session(
    session_store: SessionManifestRepository,
    session_id: str,
    *,
    policy_config: PolicyConfigResolver | None = None,
) -> list[str]:
    session_store = as_session_manifest_repository(session_store)
    session = session_store.get_session(session_id)
    if session is None:
        return []
    if session.expires_at <= datetime.now(UTC):
        return []
    if ToolName.TEAMS_GET_MESSAGES not in session.allowed_tools:
        return []
    if policy_config is not None:
        return policy_config.resolved_teams_channel_targets_for_session(
            session,
            capability="read",
        )
    return sorted(
        {
            target.strip()
            for target in list(session.teams_channel_allowlist) + list(session.teams_chat_allowlist)
            if target.strip()
        }
    )


def _resolve_allowed_teams_user_targets_for_session(
    session_store: SessionManifestRepository,
    session_id: str,
    *,
    policy_config: PolicyConfigResolver | None = None,
) -> list[str]:
    session_store = as_session_manifest_repository(session_store)
    session = session_store.get_session(session_id)
    if session is None:
        return []
    if session.expires_at <= datetime.now(UTC):
        return []
    if policy_config is not None:
        return policy_config.resolved_teams_user_targets_for_session(
            session,
            capability="write",
        )
    return sorted(target.strip() for target in session.teams_user_allowlist if target.strip())

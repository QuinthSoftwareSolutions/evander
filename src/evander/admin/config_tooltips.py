from __future__ import annotations

CONFIG_FIELD_TOOLTIPS: dict[str, str] = {
    "default_session_ttl_seconds": (
        "Default session lifetime in seconds when a new session is created without an explicit TTL."
    ),
    "approval_ttl_seconds": (
        "Default approval-request lifetime in seconds before pending approval prompts expire."
    ),
    "approval_notification_target_ids": (
        "Host-side operator target IDs that receive approval request notifications. Select from known notify-capable communication targets; directory policy sets the inherited baseline, session policy can add more IDs for a specific session, and a live workflow-run approval lease can add run-scoped targets. Lists are normalized and deduped, and delivery failures are audited without changing approval state."
    ),
    "discord_operator_channel_allowlist": (
        "Channel IDs that the host Discord bot may poll for operator commands in addition to any "
        "session-specific Discord channel allowlists."
    ),
    "discord_operator_user_allowlist": (
        "Trusted Discord operator user IDs. Commands from these users are accepted even when they "
        "arrive outside a session's channel allowlist."
    ),
    "discord_poll_operator_dms": (
        "When enabled, the host Discord bot also polls direct messages from allowlisted operator users."
    ),
    "email_draft_domain_allowlist": (
        "Allowed recipient domains for draft and send operations. Recipients outside these domains "
        "are rejected by policy."
    ),
    "email_draft_recipient_allowlist": (
        "Allowed full recipient addresses for draft and send operations. If non-empty, every "
        "recipient must match this allowlist."
    ),
    "email_folder_allowlist_by_mailbox": (
        "JSON object mapping mailbox IDs to the folder names sessions may access. Used by session "
        "creation defaults and mailbox-specific policy checks."
    ),
    "email_mailbox_allowlist": ("Mailbox IDs that new sessions may access by default."),
    "email_send_enabled": (
        "Enables actual email send operations. Leave disabled to keep email access in read-and-draft mode."
    ),
    "communication_file_receive_enabled": (
        "Allows sessions to list and download inbound communication attachments from allowlisted "
        "Discord channels, Teams targets, or email mailboxes."
    ),
    "communication_file_send_enabled": (
        "Allows sessions to send staged communication file artifacts to allowlisted destinations."
    ),
    "communication_file_max_bytes": (
        "Maximum allowed size in bytes for each inbound or outbound communication file. Use null "
        "for no size limit."
    ),
    "communication_file_allowed_mime_types": (
        "Optional MIME type allowlist for communication files. Leave empty to allow any MIME type "
        "not otherwise blocked by extension policy."
    ),
    "communication_file_allowed_extensions": (
        "Optional filename extension allowlist for communication files, for example .txt or .pdf."
    ),
    "communication_file_blocked_extensions": (
        "Filename extensions that are always blocked for communication files."
    ),
    "communication_file_require_approval_for_send": (
        "Requires an approval request before outbound file sends are allowed."
    ),
    "pr_reply_require_approval": (
        "Requires an approval request before posting PR feedback replies through the gateway."
    ),
    "redacted_keys": (
        "Request and response field names whose values should be redacted in gateway audit logs."
    ),
    "azure_devops_organization_url": (
        "Base Azure DevOps organization URL used by PR reply and feedback clients, for example "
        "https://dev.azure.com/<org>."
    ),
    "azure_devops_pat_env": (
        "Name of the host environment variable that holds the Azure DevOps personal access token."
    ),
    "azure_devops_pr_pat_env": (
        "Name of the host environment variable that holds the Azure DevOps PR personal access token."
    ),
    "ado_area_filters": (
        "Optional Azure DevOps area path allowlist used by the ADO worker when selecting work items."
    ),
    "ado_team_filters": (
        "Optional Azure DevOps team filter list used by the ADO worker when selecting work items."
    ),
    "ado_repo_source": (
        "Transient Git URL or local checkout registered as a managed repository for ADO proxy issues."
    ),
    "azure_devops_project": (
        "Default Azure DevOps project name used by host-side PR feedback and reply integrations."
    ),
    "agent_prompt_token_secret_env": (
        "Name of the host environment variable that holds the signing secret for queued agent prompt tokens."
    ),
    "agent_prompt_token_ttl_seconds": (
        "Lifetime in seconds for generated agent prompt tokens before the gateway rejects them."
    ),
    "container_worker_base_url": (
        "Base HTTP URL for the container worker service used when execution_backend is container_worker_vm."
    ),
    "container_worker_default_image": (
        "Fallback runner container image used when a container session does not select an approved image alias. If the selector exactly matches the default image reference, it resolves here with no alias recorded."
    ),
    "container_worker_images": (
        "Approved container image catalog that maps operator-safe aliases to exact runner image references. Session creation resolves selectors against this catalog, and runtime dispatch never pulls or builds images."
    ),
    "container_worker_job_timeout_seconds": (
        "Maximum runtime in seconds allowed for one container worker job before it is treated as "
        "timed out. If unset in directory config, the built-in default is 1800 seconds "
        "(30 minutes); set a positive override to extend it."
    ),
    "container_worker_request_timeout_seconds": (
        "HTTP timeout in seconds for host requests sent to the container worker service."
    ),
    "container_worker_token_env": (
        "Name of the host environment variable that holds the bearer token for the container worker API."
    ),
    "container_worker_workspace_persistence": (
        "Workspace persistence policy requested for container-worker sessions, for example session-scoped persistence."
    ),
    "execution_backend": (
        "Execution backend used for new agent work in this directory: libvirt VMs or the container worker service."
    ),
    "discord_api_base_url": (
        "Base URL for the Discord Bot API client used by the host runtime when discord_backend is bot_api."
    ),
    "discord_backend": (
        "Discord adapter mode. 'stub' uses the local JSON command store only; 'bot_api' talks to Discord's "
        "HTTP API from the host."
    ),
    "discord_bot_token_env": (
        "Name of the host environment variable that contains the Discord bot token for bot_api mode."
    ),
    "discord_poll_limit": ("Maximum number of Discord events fetched in each polling request."),
    "discord_retry_base_backoff_seconds": (
        "Initial backoff in seconds between retried Discord API requests."
    ),
    "discord_retry_count": (
        "How many times the host Discord client retries a failed API request before giving up."
    ),
    "discord_retry_max_backoff_seconds": ("Upper bound in seconds for Discord API retry backoff."),
    "discord_timeout_seconds": ("HTTP timeout in seconds for Discord API requests."),
    "discord_read_reaction_emoji": (
        "Reaction emoji that the gateway applies when unread Discord messages are fetched."
    ),
    "teams_history_page_cap": (
        "Maximum number of Microsoft Graph Teams message pages read across one bounded history scan."
    ),
    "teams_history_deadline_seconds": (
        "Wall-clock deadline in seconds for one bounded Microsoft Graph Teams history scan."
    ),
    "teams_backend": (
        "Teams adapter mode. 'stub' uses local stored messages, 'graph_api' uses a host token, and "
        "'delegated_graph_api' uses delegated Microsoft identity credentials."
    ),
    "teams_delegated_authority_base_url": (
        "Microsoft identity authority base URL used by delegated Teams authentication."
    ),
    "teams_delegated_client_id": (
        "Microsoft Entra application client ID used for delegated Teams Graph access."
    ),
    "teams_delegated_credential_store_path": (
        "Host JSON file path where delegated Teams credentials are cached."
    ),
    "teams_delegated_identity_id": (
        "Stable operator identity key used to select delegated Teams credentials."
    ),
    "teams_delegated_scopes": (
        "Microsoft Graph delegated permission scopes requested for Teams access."
    ),
    "teams_delegated_tenant_id": (
        "Microsoft Entra tenant identifier used for delegated Teams authentication."
    ),
    "teams_graph_access_token_env": (
        "Name of the host environment variable that holds a Microsoft Graph access token for Teams."
    ),
    "teams_graph_api_base_url": (
        "Base URL for Microsoft Graph Teams API calls made by the host runtime."
    ),
    "teams_poll_limit": ("Maximum number of Teams messages fetched in each polling request."),
    "teams_retry_base_backoff_seconds": (
        "Initial backoff in seconds between retried Microsoft Graph Teams API requests."
    ),
    "teams_retry_count": (
        "How many times the host Teams client retries a failed API request before giving up."
    ),
    "teams_retry_max_backoff_seconds": ("Upper bound in seconds for Teams API retry backoff."),
    "teams_timeout_seconds": ("HTTP timeout in seconds for Microsoft Graph Teams API requests."),
    "teams_read_reaction_emoji": (
        "Reaction emoji that the gateway applies when unread Teams messages are fetched."
    ),
    "email_mailboxes": (
        "JSON array of mailbox definitions. Each entry supports mailbox_id, provider "
        "(json_store/gmail/graph), account or address, auth_env_var_names/auth_secret_refs, "
        "folder_mapping, and folder_allowlist_defaults."
    ),
    "email_provider_timeout_seconds": (
        "HTTP timeout in seconds for provider-backed email and PR reply clients."
    ),
    "github_token_env": (
        "Name of the host environment variable that holds the GitHub token for PR reply and feedback operations."
    ),
    "managed_git_commit_name": (
        "Host-controlled author and committer name used for managed Git merges and release consolidation."
    ),
    "managed_git_commit_email": (
        "Host-controlled author and committer email used for managed Git merges and release consolidation."
    ),
    "managed_git_operation_timeout_seconds": (
        "Maximum runtime in seconds for each managed Git subprocess before it is terminated and cleaned up."
    ),
    "managed_git_worktree_stale_seconds": (
        "Minimum age in seconds before an unlocked leftover managed worktree can be reconciled after an interrupted operation."
    ),
    "agent_consultation_profiles": (
        "Named request-scoped container worker profiles for consult_codebase, including model, provider mode, image, timeout, and concurrency."
    ),
    "agent_consultation_default_profile": (
        "Profile used by consult_codebase when the caller does not select one explicitly."
    ),
    "agent_consultation_max_wait_seconds": (
        "Maximum time a gateway consultation call may wait before returning a pollable nonterminal result."
    ),
    "agent_consultation_retention_days": (
        "Days to retain terminal codebase consultation records before request-time compaction."
    ),
    "agent_consultation_token_secret_env": (
        "Host environment variable containing the signing secret for one-time consultation result tokens."
    ),
    "container_worker_guest_openai_base_url": (
        "Container-visible OpenAI relay base URL used inside runner sessions."
    ),
    "container_worker_guest_anthropic_base_url": (
        "Container-visible Anthropic relay base URL used inside Claude runner sessions."
    ),
    "container_worker_guest_gateway_url": (
        "Container-visible MCP gateway URL used inside runner sessions."
    ),
    "container_worker_guest_http_target_relay_base_url": (
        "Container-visible HTTP target relay base URL used inside runner sessions."
    ),
    "container_worker_guest_nuget_relay_base_url": (
        "Container-visible NuGet relay base URL used inside runner sessions."
    ),
    "container_worker_guest_pypi_index_url": (
        "Container-visible PyPI index URL used inside runner sessions."
    ),
    "container_worker_guest_npm_registry_url": (
        "Container-visible npm registry URL used inside runner sessions."
    ),
    "container_worker_mssql_enabled": (
        "Enables session-scoped SQL Server database provisioning tools for container-backed agents."
    ),
    "container_worker_mssql_host": (
        "Container-visible SQL Server host returned in provisioned database connection details."
    ),
    "container_worker_mssql_port": (
        "Container-visible SQL Server TCP port returned in provisioned database connection details."
    ),
    "nuget_azure_feed_base_url": (
        "Base URL for the Azure Artifacts NuGet feed proxied by the host relay."
    ),
    "nuget_azure_pat_env": (
        "Name of the host environment variable that holds the Azure Artifacts PAT for the NuGet relay."
    ),
    "nuget_public_feed_base_url": ("Base URL for the public NuGet feed proxied by the host relay."),
    "nuget_relay_timeout_seconds": ("Upstream request timeout in seconds for the NuGet relay."),
    "npm_audit_facade_audit_registry_url": (
        "Base URL for the public npm registry used by the npm audit facade's audit endpoints."
    ),
    "npm_audit_facade_package_registry_url": (
        "Optional package registry URL used by the npm audit facade for package metadata and tarballs. "
        "Leave unset to derive the Nexus npm group URL from the gateway host."
    ),
    "npm_audit_facade_timeout_seconds": (
        "Upstream request timeout in seconds for the npm audit facade."
    ),
    "model_provider_mode": (
        "Default provider mode for relay-backed model requests. Use openai_public for public OpenAI, "
        "local_openai for a host-local OpenAI-compatible endpoint, azure_openai for Azure OpenAI, "
        "or anthropic for Claude Messages API routing. "
        "Individual sessions can override this default with session metadata model_provider_mode."
    ),
    "openai_api_base_url": (
        "Upstream OpenAI API base URL used by the host relay in public OpenAI mode."
    ),
    "openai_api_key_env": (
        "Name of the host environment variable that holds the upstream OpenAI API key in public OpenAI mode."
    ),
    "openai_relay_timeout_seconds": ("Upstream request timeout in seconds for the OpenAI relay."),
    "local_openai_api_base_url": (
        "Host-local OpenAI-compatible API base URL used by the relay in local_openai mode."
    ),
    "local_openai_api_key": (
        "Placeholder API key sent to the local OpenAI-compatible endpoint in local_openai mode."
    ),
    "local_openai_provider_name": (
        "Provider name stored in model-usage records for local_openai relay traffic."
    ),
    "anthropic_api_base_url": (
        "Upstream Anthropic-compatible API base URL used by the host relay in anthropic mode."
    ),
    "anthropic_api_key_env": (
        "Name of the host environment variable that holds the Anthropic API key."
    ),
    "anthropic_api_version": (
        "Anthropic-Version header value appended by the host relay when guests omit it."
    ),
    "anthropic_relay_timeout_seconds": (
        "Upstream request timeout in seconds for Anthropic relay requests."
    ),
    "azure_openai_endpoint": (
        "Azure OpenAI resource endpoint used by the host relay when provider mode is azure_openai."
    ),
    "azure_openai_auth_mode": (
        "Authentication mode for Azure OpenAI requests. Currently only api_key is supported."
    ),
    "azure_openai_api_key_env": (
        "Name of the host environment variable that holds the Azure OpenAI API key."
    ),
    "azure_openai_api_version": (
        "Azure OpenAI api-version query parameter appended to upstream requests."
    ),
    "azure_openai_model_deployments": (
        "JSON object mapping logical model names to Azure deployment names used by the relay."
    ),
    "openai_realtime_model": (
        "OpenAI Realtime model used for projected live voice sessions."
    ),
    "openai_realtime_api_key_env": (
        "Host environment variable used to mint browser Realtime client secrets for Team "
        "Add-on direct agent calls. SIP and meeting bridges still use bridge-local key "
        "configuration."
    ),
    "openai_realtime_api_base_url": (
        "OpenAI-compatible Realtime API base URL used for Team Add-on browser direct "
        "agent calls. SIP and meeting bridges still use bridge-local upstream URL "
        "configuration."
    ),
    "voice_media_controller_url": (
        "Optional host-owned voice media controller URL passed to phone or meeting bridges."
    ),
    "voice_media_controller_token_env": (
        "Name of the host environment variable that holds the voice media controller token."
    ),
    "voice_media_controller_request_timeout_seconds": (
        "HTTP timeout in seconds for voice media controller and bridge requests."
    ),
    "semantic_store_provider": (
        "Semantic store backend used by workflow semantic primitives. Currently only qdrant is supported."
    ),
    "qdrant_base_url": (
        "Host-side Qdrant HTTP base URL used for semantic store management and later semantic data effects."
    ),
    "qdrant_api_key_env": (
        "Optional host environment variable name containing the Qdrant API key. Leave unset for unauthenticated local Qdrant."
    ),
    "semantic_embedding_provider": (
        "Embedding provider used by semantic workflows. Currently only openai is supported."
    ),
    "semantic_embedding_model": (
        "OpenAI embedding model used when semantic data effects generate vectors."
    ),
    "semantic_embedding_dimensions": (
        "Vector dimensions expected for semantic collections and embedding requests."
    ),
    "semantic_vector_distance": (
        "Qdrant vector distance used for semantic collections, for example Cosine."
    ),
    "retell_api_base_url": (
        "Optional override for Retell API requests made by the host gateway. "
        "Leave the default for the public Retell API."
    ),
    "retell_api_key_env": (
        "Name of the host environment variable that holds the Retell API key."
    ),
    "retell_from_number": (
        "Retell-owned outbound caller number used by make_phone_call. Stored host-side only."
    ),
    "retell_default_agent_id": (
        "Retell agent id used as override_agent_id for directory-contact phone calls. "
        "The Retell prompt should reference {{agent_question}}."
    ),
    "retell_request_timeout_seconds": (
        "HTTP timeout in seconds for host-side Retell create-call and get-call requests."
    ),
    "phone_call_backend": (
        "Phone backend used by make_phone_call: existing Retell calls or OpenAI Realtime via DIDWW bridge."
    ),
    "didww_sip_bridge_url": (
        "Host-owned DIDWW SIP bridge URL for OpenAI Realtime phone calls."
    ),
    "didww_sip_bridge_token_env": (
        "Name of the host environment variable containing the DIDWW SIP bridge bearer token."
    ),
    "didww_from_number": (
        "Optional caller ID passed to the DIDWW SIP bridge. Stored host-side only."
    ),
    "inbound_phone_routes": (
        "Fixed inbound DID or called-number routes that map a caller policy and projection "
        "prompt to an existing Truman session. Caller and number fields are normalized and "
        "stored host-side only."
    ),
    "contacts": (
        "Directory-scoped contact entries keyed by stable contact id. Entries include name, "
        "description, email_addresses, and phone_numbers with one optional primary phone number."
    ),
    "user_contact_links": (
        "Directory-scoped mapping from admin user login to one contact id."
    ),
    "http_target_relay_timeout_seconds": (
        "Upstream request timeout in seconds for the generic HTTP target relay."
    ),
    "provider_state": (
        "Advanced provider-specific JSON state consumed by integration providers."
    ),
    "issue_setup_azure_devops_pr_pat_env": (
        "Optional override for the Azure DevOps PR PAT env-var name used by issue setup and PR scripts."
    ),
    "default_model": (
        "Default agent model for sessions in this directory when no per-session model override is set."
    ),
    "workflow_generation_model": (
        "Optional model used by the Workflow tab natural-language graph generator."
    ),
    "pr_template_markdown": (
        "Markdown boilerplate prepended to automated PR descriptions for this directory."
    ),
    "git_host": ("Host address or name the guest uses to reach git remotes during issue setup."),
    "git_user": ("Host username used by guest SSH/git commands during issue setup."),
    "git_ssh_alias": ("SSH host alias configured in the guest for host git operations."),
    "post_exec_verification_prompt": (
        "Optional one-time follow-up prompt injected after the primary post-exec run."
    ),
    "enable_guest_heartbeat": (
        "When enabled, issue VM setup writes guest heartbeat integration settings for Codex sessions."
    ),
    "guest_heartbeat_prompt": (
        "Prompt used by guest heartbeat wake-ups when enable_guest_heartbeat is true."
    ),
    "host_busy_check_script_path": (
        "Host script used by wake workers to detect whether a session VM is currently busy "
        "before attempting to resume it."
    ),
    "wake_cooldown_seconds": (
        "Minimum cooldown in seconds before wake workers will try to resume the same session again."
    ),
    "wake_jitter_seconds": (
        "Random delay added to each wake polling sleep interval to avoid perfectly synchronized polling."
    ),
    "wake_poll_interval_seconds": (
        "Base polling interval in seconds for host-side wake workers."
    ),
    "pr_feedback_resume_prompt": (
        "Prompt text passed to the resume script when waking an agent session for unread PR comments."
    ),
    "agent_prompt_queue_max_retries": (
        "Maximum durable queue attempts allowed for the same agent prompt before prompt enqueue fails."
    ),
    "agent_context_retry_enabled": (
        "Automatically retry Codex agent prompts with reduced context when a run exhausts the model context before producing a final response."
    ),
    "agent_context_retry_max_attempts": (
        "Maximum automatic reduced-context retries after context-exhausted agent prompt runs."
    ),
    "agent_context_retry_target_chars": (
        "Target character budget for deterministic reduced-context retry prompts."
    ),
    "discord_resume_prompt": (
        "Prompt text passed to the Discord wake worker when waking an agent session for unread Discord messages."
    ),
    "wake_resume_timeout_seconds": (
        "Timeout in seconds for the host resume script invocation."
    ),
    "wake_session_cooldown_path": (
        "JSON state file path where wake workers track per-session cooldowns."
    ),
    "scheduled_wake_prefix_text": (
        "Static text prepended to scheduled resume prompts before dispatch (prefix + scheduled prompt)."
    ),
    "wake_schedule_max_active_per_session": (
        "Optional guardrail: maximum number of active scheduled wake requests allowed per session."
    ),
    "wake_schedule_max_active_total": (
        "Optional guardrail: maximum number of active scheduled wake requests allowed across all sessions."
    ),
    "wake_schedule_min_interval_seconds": (
        "Optional guardrail: minimum allowed interval, in seconds, between scheduled runs for recurring wakes."
    ),
    "wake_vm_user": (
        "Guest username passed to the resume script when reconnecting to a VM."
    ),
    "wake_dispatch_state_path": (
        "Host-side JSON state file written by the resume script to track recent wake attempts."
    ),
    "host_resume_script_path": (
        "Host script invoked by wake workers to resume an agent VM when unread feedback is available."
    ),
    "bind_network_cidr": (
        "Network CIDR assigned to the guest bind network. Must be a real network, for example 192.168.250.0/24."
    ),
    "provider": ("VM provider name used for new VM targets, for example libvirt or azure."),
    "provider_defaults": (
        "Provider-specific default VM options merged into new VM creation requests."
    ),
    "azure": (
        "Azure VM provider defaults such as subscription, resource group, region, network, image, size, and tags."
    ),
    "gateway_url": ("MCP gateway URL exposed to guests and used by CLI health checks."),
    "http_target_relay_host": ("Host address the generic HTTP target relay listens on."),
    "http_target_relay_port": ("Port the generic HTTP target relay listens on."),
    "nuget_relay_host": ("Host address the NuGet relay listens on."),
    "nuget_relay_port": ("Port the NuGet relay listens on."),
    "npm_audit_facade_host": ("Host address the npm audit facade listens on."),
    "npm_audit_facade_port": ("Port the npm audit facade listens on."),
    "openai_relay_host": ("Host address the OpenAI relay listens on."),
    "openai_relay_port": ("Port the OpenAI relay listens on."),
    "approved_issue_cap": (
        "Maximum number of approved but incomplete issues this directory may keep active at once."
    ),
    "originator_roles": (
        "Role names that can originate work for this directory's automation flow."
    ),
    "prioritizer_roles": (
        "Role names that can prioritize or approve work for this directory's automation flow."
    ),
    "worker_roles": (
        "Role names that can execute worker assignments for this directory's automation flow."
    ),
}

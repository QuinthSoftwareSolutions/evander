from __future__ import annotations

CONFIG_FIELD_TOOLTIPS: dict[str, str] = {
    "default_session_ttl_seconds": (
        "Default session lifetime in seconds when a new session is created without an explicit TTL."
    ),
    "approval_ttl_seconds": (
        "Default approval-request lifetime in seconds before pending approval prompts expire."
    ),
    "discord_operator_channel_allowlist": (
        "Channel IDs that the host Discord bot may poll for operator commands in addition to any "
        "session-specific Discord channel allowlists."
    ),
    "discord_inbox_routes": (
        "Directory-scoped mapping from watched Discord channel IDs to target session IDs for "
        "always-on inbox delivery."
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
    "ado_mssql_sa_password_env": (
        "Name of the host environment variable that holds the MSSQL SA password used by ADO issue setup."
    ),
    "ado_area_filters": (
        "Optional Azure DevOps area path allowlist used by the ADO worker when selecting work items."
    ),
    "ado_team_filters": (
        "Optional Azure DevOps team filter list used by the ADO worker when selecting work items."
    ),
    "ado_project_or_solution": (
        "Project or solution path handed to issue VM setup for NuGet prewarm tasks."
    ),
    "ado_prewarm_target": (
        "Optional override target for prewarm steps; falls back to ado_project_or_solution when unset."
    ),
    "ado_repo_source": (
        "Default host repo path or remote URL associated with ADO-created issue records."
    ),
    "create_prs_automatically": (
        "When enabled, post-receive automation opens upstream pull requests for promoted agent branches."
    ),
    "create_prs_as_draft": (
        "When enabled, new Azure DevOps pull requests opened for this directory are created as drafts."
    ),
    "ado_setup_vms": (
        "Enable or disable VM setup in the legacy ADO worker for this directory while still allowing Azure issue ingestion."
    ),
    "ado_bootstrap_dotnet": (
        "Enable or disable dotnet/bootstrap steps for ADO worker-created VMs."
    ),
    "ado_bootstrap_nuget": ("Enable or disable NuGet bootstrap setup for ADO worker-created VMs."),
    "ado_bootstrap_mssql": ("Enable or disable MSSQL bootstrap setup for ADO worker-created VMs."),
    "ado_mssql_init_root": (
        "Optional host path containing SQL initialization scripts copied during MSSQL bootstrap."
    ),
    "ado_bootstrap_playwright": (
        "Enable or disable Playwright bootstrap setup for ADO worker-created VMs."
    ),
    "ado_bootstrap_frontend_deps": (
        "Enable or disable frontend dependency bootstrap setup for ADO worker-created VMs."
    ),
    "ado_frontend_host_checkout": (
        "Optional host checkout path used by frontend dependency bootstrap."
    ),
    "ado_frontend_install_roots": (
        "Optional list of install roots used by frontend dependency bootstrap."
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
        "Default runner container image requested for container-backed agent executions."
    ),
    "container_worker_job_timeout_seconds": (
        "Maximum runtime in seconds allowed for one container worker job before it is treated as timed out."
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
    "container_worker_guest_openai_base_url": (
        "Container-visible OpenAI relay base URL used inside runner sessions."
    ),
    "container_worker_guest_gateway_url": (
        "Container-visible MCP gateway URL used inside runner sessions."
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
    "openai_api_base_url": ("Upstream OpenAI-compatible API base URL used by the host relay."),
    "openai_api_key_env": (
        "Name of the host environment variable that holds the upstream OpenAI API key."
    ),
    "openai_relay_timeout_seconds": ("Upstream request timeout in seconds for the OpenAI relay."),
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
        "Default Retell agent id used as override_agent_id unless a phonebook contact sets agent_id. "
        "The Retell prompt should reference {{agent_question}}."
    ),
    "retell_request_timeout_seconds": (
        "HTTP timeout in seconds for host-side Retell create-call and get-call requests."
    ),
    "retell_phonebook": (
        "Directory-scoped contact aliases for Retell calls. Each alias maps to display_name, "
        "phone_number, and optional agent_id."
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
    "pr_template_markdown": (
        "Markdown boilerplate prepended to automated PR descriptions for this directory."
    ),
    "git_root": ("Host root directory that stores bare git remotes used during issue setup."),
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
    "host_heartbeat_cooldown_seconds": (
        "Minimum cooldown in seconds before wake workers will try to resume the same session again."
    ),
    "host_heartbeat_http_timeout_seconds": (
        "HTTP client timeout in seconds for each host-side wake polling cycle."
    ),
    "host_heartbeat_jitter_seconds": (
        "Random delay added to each wake polling sleep interval to avoid perfectly synchronized polling."
    ),
    "host_heartbeat_lock_path": (
        "Single-instance lock file path shared by host-side wake workers."
    ),
    "host_heartbeat_poll_interval_seconds": (
        "Base polling interval in seconds for host-side wake workers."
    ),
    "host_heartbeat_pr_resume_prompt": (
        "Prompt text passed to the resume script when waking an agent session for unread PR comments."
    ),
    "discord_resume_prompt": (
        "Prompt text passed to the Discord wake worker when waking an agent session for unread Discord messages."
    ),
    "host_heartbeat_resume_timeout_seconds": (
        "Timeout in seconds for the host resume script invocation."
    ),
    "host_heartbeat_session_cooldown_path": (
        "JSON state file path where the heartbeat worker tracks per-session cooldowns."
    ),
    "host_heartbeat_stream_conflict_policy": (
        "How the heartbeat worker handles one PR feedback stream matching multiple sessions: "
        "'fanout' polls anyway, 'reject' skips that stream."
    ),
    "host_heartbeat_scheduled_wake_prefix_text": (
        "Static text prepended to MCP-scheduled resume prompts before dispatch (prefix + scheduled prompt)."
    ),
    "host_heartbeat_schedule_max_active_per_session": (
        "Optional guardrail: maximum number of active scheduled wake requests allowed per session."
    ),
    "host_heartbeat_schedule_max_active_total": (
        "Optional guardrail: maximum number of active scheduled wake requests allowed across all sessions."
    ),
    "host_heartbeat_schedule_min_interval_seconds": (
        "Optional guardrail: minimum allowed interval, in seconds, between scheduled runs for recurring wakes."
    ),
    "host_heartbeat_vm_user": (
        "Guest username passed to the resume script when reconnecting to a VM."
    ),
    "host_heartbeat_wake_state_path": (
        "Host-side JSON state file written by the resume script to track recent wake attempts."
    ),
    "host_resume_script_path": (
        "Host script invoked by the heartbeat worker to resume an agent VM when unread feedback is available."
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

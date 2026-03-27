# Configuration Reference

This document lists the **user-configurable parameters** in this repository, where each parameter is defined, and where it is consumed.

Scope included:

- Environment-variable based configuration (`config/*.env.example`, plus generated guest env files).
- Gateway YAML configuration keys (`config/gateway.yaml` / `config/gateway.example.yaml`).
- Cloud-init template placeholders used by VM launch/bootstrap.

## 1) Service environment files (`config/*.env.example`)

## `config/gateway.env.example`

| Parameter | Found in | Consumed by |
|---|---|---|
| `AZURE_DEVOPS_PAT` | `config/gateway.env.example` | `src/agent_sandbox/runtime.py` / `src/agent_sandbox/gateway.py` when PR reply providers use the default `azure_devops_pat_env`. |

## `config/openai-relay.env.example`

| Parameter | Found in | Consumed by |
|---|---|---|
| `OPENAI_API_KEY` | `config/openai-relay.env.example` | `src/agent_sandbox/openai_relay.py` (looked up using config key `openai_api_key_env`, defaulting to `OPENAI_API_KEY`). |

## `config/nuget-relay.env.example`

| Parameter | Found in | Consumed by |
|---|---|---|
| `AZURE_ARTIFACTS_PAT` | `config/nuget-relay.env.example` | `src/agent_sandbox/nuget_relay.py` (looked up via config key `nuget_azure_pat_env`, defaulting to `AZURE_ARTIFACTS_PAT`). |

## `config/firewall.env.example`

| Parameter | Found in | Consumed by |
|---|---|---|
| `ALLOWED_TCP_PORTS` | `config/firewall.env.example` | `scripts/apply_firewall.sh` (nftables allow-list for guest->host TCP ports). |

## `config/host-heartbeat.env.example`

| Parameter | Found in | Consumed by |
|---|---|---|
| `HOST_HEARTBEAT_LOG_LEVEL` | `config/host-heartbeat.env.example` | `scripts/run_host_heartbeat.sh` (`--log-level` passed to worker). |
| `GITHUB_TOKEN` | `config/host-heartbeat.env.example` | `src/agent_sandbox/runtime.py` + `src/agent_sandbox/host_heartbeat_worker.py` using `github_token_env` from YAML config. |
| `AZURE_DEVOPS_PAT` | `config/host-heartbeat.env.example` | `src/agent_sandbox/runtime.py` + `src/agent_sandbox/host_heartbeat_worker.py` using `azure_devops_pat_env` from YAML config. |

## `config/ado-worker.env.example`

| Parameter | Found in | Consumed by |
|---|---|---|
| `ADO_POLL_INTERVAL_SECONDS` | `config/ado-worker.env.example` | `src/agent_sandbox/ado_issue_worker.py`. |
| `ADO_AREA_FILTERS` | `config/ado-worker.env.example` | `src/agent_sandbox/ado_issue_worker.py`. |
| `ADO_TEAM_FILTERS` | `config/ado-worker.env.example` | `src/agent_sandbox/ado_issue_worker.py`. |
| `ADO_RUNTIME_DIR` | `config/ado-worker.env.example` | `src/agent_sandbox/ado_issue_worker.py`. |
| `ADO_YAML_OUTPUT_DIR` | `config/ado-worker.env.example` | `src/agent_sandbox/ado_issue_worker.py`. |
| `ADO_SETUP_LOG_DIR` | `config/ado-worker.env.example` | `src/agent_sandbox/ado_issue_worker.py`. |
| `ADO_PROCESSED_STORE_PATH` | `config/ado-worker.env.example` | `src/agent_sandbox/ado_issue_worker.py`. |
| `ADO_PROJECT_OR_SOLUTION` | `config/ado-worker.env.example` | `src/agent_sandbox/ado_issue_worker.py` (required for setup script hand-off). |
| `ADO_PREWARM_TARGET` | `config/ado-worker.env.example` | `src/agent_sandbox/ado_issue_worker.py` (optional bootstrap arg). |
| `ADO_SETUP_ISSUE_VM_SCRIPT` | `config/ado-worker.env.example` | `src/agent_sandbox/ado_issue_worker.py`. |
| `ADO_BOOTSTRAP_DOTNET` | `config/ado-worker.env.example` | `src/agent_sandbox/ado_issue_worker.py` (forwarded to `setup_issue_vm.sh`). |
| `ADO_BOOTSTRAP_NUGET` | `config/ado-worker.env.example` | `src/agent_sandbox/ado_issue_worker.py`. |
| `ADO_BOOTSTRAP_MSSQL` | `config/ado-worker.env.example` | `src/agent_sandbox/ado_issue_worker.py`. |
| `ADO_MSSQL_SA_PASSWORD` | `config/ado-worker.env.example` | `src/agent_sandbox/ado_issue_worker.py` (required when MSSQL bootstrap enabled). |
| `ADO_MSSQL_INIT_ROOT` | `config/ado-worker.env.example` | `src/agent_sandbox/ado_issue_worker.py` (optional bootstrap arg). |
| `ADO_BOOTSTRAP_PLAYWRIGHT` | `config/ado-worker.env.example` | `src/agent_sandbox/ado_issue_worker.py`. |
| `ADO_BOOTSTRAP_FRONTEND_DEPS` | `config/ado-worker.env.example` | `src/agent_sandbox/ado_issue_worker.py`. |
| `ADO_FRONTEND_HOST_CHECKOUT` | `config/ado-worker.env.example` | `src/agent_sandbox/ado_issue_worker.py` (optional bootstrap arg). |
| `ADO_FRONTEND_INSTALL_ROOTS` | `config/ado-worker.env.example` | `src/agent_sandbox/ado_issue_worker.py` (optional bootstrap arg). |
| `ADO_SETUP_TIMEOUT_SECONDS` | `config/ado-worker.env.example` | `src/agent_sandbox/ado_issue_worker.py`. |
| `ADO_SETUP_CONCURRENCY_LIMIT` | `config/ado-worker.env.example` | `src/agent_sandbox/ado_issue_worker.py`. |
| `ADO_SETUP_LOCK_PATH` | `config/ado-worker.env.example` | `src/agent_sandbox/ado_issue_worker.py`. |
| `ADO_REPO_PATH` | `config/ado-worker.env.example` | `src/agent_sandbox/ado_issue_worker.py` (optional passthrough to setup script). |
| `ADO_SESSION_ID` | `config/ado-worker.env.example` | `src/agent_sandbox/ado_issue_worker.py` (optional passthrough). |
| `ADO_VM_USER` | `config/ado-worker.env.example` | `src/agent_sandbox/ado_issue_worker.py` (optional passthrough). |
| `ADO_FETCH_RETRY_COUNT` | `config/ado-worker.env.example` | `src/agent_sandbox/ado_issue_worker.py`. |
| `ADO_FETCH_RETRY_BACKOFF_SECONDS` | `config/ado-worker.env.example` | `src/agent_sandbox/ado_issue_worker.py`. |
| `ADO_INCLUDE_COMMENTS` | `config/ado-worker.env.example` | `src/agent_sandbox/ado_issue_worker.py`. |
| `ADO_COMMENTS_MAX_COUNT` | `config/ado-worker.env.example` | `src/agent_sandbox/ado_issue_worker.py`. |
| `ADO_COMMENT_TEXT_MAX_CHARS` | `config/ado-worker.env.example` | `src/agent_sandbox/ado_issue_worker.py`. |

The ADO worker now loads `azure_devops_organization_url`, `azure_devops_project`, and
`azure_devops_pat_env` from `config/gateway.yaml` (including any root-level provider overlay)
instead of duplicating them in `config/ado-worker.env`.

## `config/git-server.env.example`

| Parameter | Found in | Consumed by |
|---|---|---|
| `AGENT_SANDBOX_GIT_ROOT` | `config/git-server.env.example` | `src/agent_sandbox/cli.py` and git automation scripts (`create_git_remote.sh`, `git_ssh_command_wrapper.sh`, `promote_agent_branch.sh`). |
| `AGENT_SANDBOX_GIT_HOST` | `config/git-server.env.example` | `scripts/bootstrap_git_vm.sh`, `scripts/setup_issue_vm.sh`. |
| `AGENT_SANDBOX_GIT_USER` | `config/git-server.env.example` | `scripts/bootstrap_git_vm.sh`, `scripts/setup_issue_vm.sh`. |
| `AGENT_SANDBOX_GIT_SSH_ALIAS` | `config/git-server.env.example` | `scripts/bootstrap_git_vm.sh`, `scripts/setup_issue_vm.sh`. |
| `AGENT_POST_EXEC_VERIFICATION_PROMPT` | `config/git-server.env.example` | `scripts/setup_issue_vm.sh` (post-exec resume pass prompt). |
| `AZURE_DEVOPS_PAT` | `config/git-server.env.example` | `scripts/open_agent_pr.sh` (when provider is Azure DevOps). |
| `AGENT_SANDBOX_AZURE_DEVOPS_PAT_ENV` | `config/git-server.env.example` | `scripts/open_agent_pr.sh` (alternate env var name for Azure PAT). |
| `AGENT_SANDBOX_PR_NOTES_REF` | `config/git-server.env.example` | `scripts/handle_agent_push.sh` (PR notes ref). |
| `AGENT_SANDBOX_PR_FIXED_BODY_FILE` | `config/git-server.env.example` | `scripts/handle_agent_push.sh` (prepend markdown to PR body). |
| `AGENT_SANDBOX_PR_BODY_MAX_BYTES` | `config/git-server.env.example` | `scripts/handle_agent_push.sh` (body truncation limit). |
| `AGENT_SANDBOX_NOTIFY_COMMAND` | `config/git-server.env.example` | `scripts/handle_agent_push.sh` (post-promotion host notification command). |

## 2) Gateway YAML keys (`config/gateway.yaml`, `config/gateway.example.yaml`)

All keys are defined in `src/agent_sandbox/config.py` (`GatewayConfig` + `MailboxConfig`) and loaded by `load_config()`.

### Core server/runtime keys

| Key | Found in | Consumed by |
|---|---|---|
| `host`, `port` | `config/gateway*.yaml` | `src/agent_sandbox/gateway.py` server bind settings. |
| `log_level` | `config/gateway*.yaml` | `src/agent_sandbox/gateway.py` logging setup for gateway process. |
| `runtime_dir` | `config/gateway*.yaml` | `src/agent_sandbox/runtime.py` (store locations), `src/agent_sandbox/host_heartbeat_worker.py` (feedback worker store root). |
| `audit_log_path` | `config/gateway*.yaml` | `src/agent_sandbox/runtime.py` audit logger destination. |
| `session_store_path`, `approval_store_path`, `draft_store_path`, `email_store_path`, `email_cursor_store_path`, `discord_command_store_path` | `config/gateway*.yaml` | `src/agent_sandbox/runtime.py` persistent JSON stores. |
| `directories_store_path` | `config/gateway*.yaml` | `src/agent_sandbox/runtime.py`, `src/agent_sandbox/directories.py`, directory admin/config services. |
| `enable_directory_scoped_config` | `config/gateway*.yaml` | `src/agent_sandbox/directory_admin.py`, `src/agent_sandbox/admin_app.py`, `src/agent_sandbox/ado_issue_worker.py`, `src/agent_sandbox/host_heartbeat_worker.py`, `src/agent_sandbox/pr_feedback_worker.py` (transition flag for directory edit surfaces and worker shadow resolution). |
| `approval_ttl_seconds` | `config/gateway*.yaml` | `src/agent_sandbox/policy.py` approval expiry computation. |
| `default_session_ttl_seconds` | `config/gateway*.yaml` | `src/agent_sandbox/cli.py` session creation defaults. |
| `bind_network_cidr` | `config/gateway*.yaml` | `src/agent_sandbox/config.py` validation/normalization (policy-bound network constraints). |
| `gateway_url` | `config/gateway*.yaml` | `src/agent_sandbox/cli.py` health checks / external endpoint references. |
| `redacted_keys` | `config/gateway*.yaml` (optional) | `src/agent_sandbox/gateway.py` request/response redaction in logs. |

### OpenAI relay keys

| Key | Found in | Consumed by |
|---|---|---|
| `openai_relay_host`, `openai_relay_port` | `config/gateway*.yaml` | `src/agent_sandbox/openai_relay.py` listener bind. |
| `openai_api_base_url` | `config/gateway*.yaml` | `src/agent_sandbox/openai_relay.py` upstream URL base. |
| `openai_api_key_env` | `config/gateway*.yaml` | `src/agent_sandbox/openai_relay.py` env-var name lookup for API key. |
| `openai_relay_timeout_seconds` | `config/gateway*.yaml` | `src/agent_sandbox/openai_relay.py` upstream request timeout. |

### NuGet relay keys

| Key | Found in | Consumed by |
|---|---|---|
| `nuget_relay_host`, `nuget_relay_port` | `config/gateway*.yaml` | `src/agent_sandbox/nuget_relay.py` listener bind. |
| `nuget_public_feed_base_url` | `config/gateway*.yaml` | `src/agent_sandbox/nuget_relay.py` public feed proxy target. |
| `nuget_azure_feed_base_url` | `config/gateway*.yaml` | `src/agent_sandbox/nuget_relay.py` Azure feed proxy target. |
| `nuget_azure_pat_env` | `config/gateway*.yaml` | `src/agent_sandbox/nuget_relay.py` env-var name lookup for Azure Artifacts PAT. |
| `nuget_relay_timeout_seconds` | `config/gateway*.yaml` | `src/agent_sandbox/nuget_relay.py` upstream timeout. |

### Discord keys

| Key | Found in | Consumed by |
|---|---|---|
| `discord_backend` | `config/gateway*.yaml` | `src/agent_sandbox/runtime.py` adapter selection (`stub`/`bot_api`). |
| `discord_bot_token_env` | `config/gateway*.yaml` | `src/agent_sandbox/runtime.py` token env lookup. |
| `discord_api_base_url` | `config/gateway*.yaml` | `src/agent_sandbox/runtime.py` bot API client base URL. |
| `discord_poll_limit` | `config/gateway*.yaml` | `src/agent_sandbox/runtime.py` polling window size. |
| `discord_timeout_seconds` | `config/gateway*.yaml` | `src/agent_sandbox/runtime.py` HTTP timeout. |
| `discord_retry_count` | `config/gateway*.yaml` | `src/agent_sandbox/runtime.py` retry behavior. |
| `discord_retry_base_backoff_seconds` | `config/gateway*.yaml` | `src/agent_sandbox/runtime.py` retry backoff floor. |
| `discord_retry_max_backoff_seconds` | `config/gateway*.yaml` | `src/agent_sandbox/runtime.py` retry backoff ceiling. |
| `discord_operator_user_allowlist` | `config/gateway*.yaml` | `src/agent_sandbox/runtime.py` and `src/agent_sandbox/policy.py` authorization checks. |
| `discord_operator_channel_allowlist` | `config/gateway*.yaml` | `src/agent_sandbox/runtime.py` channel filter for operator events. |
| `discord_poll_operator_dms` | `config/gateway*.yaml` | `src/agent_sandbox/runtime.py` DM polling toggle. |

### Email keys

| Key | Found in | Consumed by |
|---|---|---|
| `email_mailboxes[]` | `config/gateway*.yaml` | `src/agent_sandbox/runtime.py` mailbox provider adapter construction. |
| `email_mailboxes[].mailbox_id` | `config/gateway*.yaml` | `src/agent_sandbox/runtime.py`, `src/agent_sandbox/policy.py`, validation in `src/agent_sandbox/config.py`. |
| `email_mailboxes[].provider` | `config/gateway*.yaml` | `src/agent_sandbox/runtime.py` provider type dispatch (`json_store`/`gmail`/`graph`). |
| `email_mailboxes[].account_identifier` (`account`/`address` aliases) | `config/gateway*.yaml` | `src/agent_sandbox/email/gmail_provider.py`, `src/agent_sandbox/email/graph_provider.py`. |
| `email_mailboxes[].auth_secret_refs` (`auth_env_var_names` alias) | `config/gateway*.yaml` | `src/agent_sandbox/runtime.py` + provider token resolution. |
| `email_mailboxes[].folder_mapping` | `config/gateway*.yaml` | Gmail/Graph provider folder mapping. |
| `email_mailboxes[].folder_allowlist_defaults` | `config/gateway*.yaml` | Defaulting logic in `src/agent_sandbox/config.py`. |
| `email_mailbox_allowlist` | `config/gateway*.yaml` | `src/agent_sandbox/cli.py` session manifest policy surface. |
| `email_folder_allowlist_by_mailbox` | `config/gateway*.yaml` | `src/agent_sandbox/cli.py` / `src/agent_sandbox/policy.py`. |
| `email_draft_recipient_allowlist` | `config/gateway*.yaml` | `src/agent_sandbox/policy.py` recipient checks. |
| `email_draft_domain_allowlist` | `config/gateway*.yaml` | `src/agent_sandbox/policy.py` domain checks. |
| `email_send_enabled` | `config/gateway*.yaml` | `src/agent_sandbox/cli.py`, `src/agent_sandbox/gateway.py`, `src/agent_sandbox/policy.py`. |
| `email_provider_timeout_seconds` | `config/gateway*.yaml` | `src/agent_sandbox/runtime.py` email + PR provider HTTP client timeouts. |

### PR/feedback + host-heartbeat keys

| Key | Found in | Consumed by |
|---|---|---|
| `pr_reply_require_approval` | `config/gateway*.yaml` | `src/agent_sandbox/gateway.py` PR-reply approval gate. |
| `github_token_env` | `config/gateway*.yaml` | `src/agent_sandbox/runtime.py` and `src/agent_sandbox/host_heartbeat_worker.py`. |
| `azure_devops_pat_env` | `config/gateway*.yaml` | `src/agent_sandbox/runtime.py` and `src/agent_sandbox/host_heartbeat_worker.py`. |
| `azure_devops_organization_url` | `config/gateway*.yaml` | `src/agent_sandbox/runtime.py` and `src/agent_sandbox/host_heartbeat_worker.py`. |
| `azure_devops_project` | `config/gateway*.yaml` | `src/agent_sandbox/runtime.py` and `src/agent_sandbox/host_heartbeat_worker.py`. |
| `host_heartbeat_poll_interval_seconds` | `config/gateway*.yaml` | `src/agent_sandbox/host_heartbeat_worker.py`. |
| `host_heartbeat_jitter_seconds` | `config/gateway*.yaml` | `src/agent_sandbox/host_heartbeat_worker.py`. |
| `host_heartbeat_cooldown_seconds` | `config/gateway*.yaml` | `src/agent_sandbox/host_heartbeat_worker.py`. |
| `host_heartbeat_http_timeout_seconds` | `config/gateway*.yaml` | `src/agent_sandbox/host_heartbeat_worker.py`. |
| `host_heartbeat_lock_path` | `config/gateway*.yaml` | `src/agent_sandbox/host_heartbeat_worker.py`. |
| `host_heartbeat_session_cooldown_path` | `config/gateway*.yaml` | `src/agent_sandbox/host_heartbeat_worker.py`. |
| `host_busy_check_script_path` | `config/gateway*.yaml` | `src/agent_sandbox/host_heartbeat_worker.py` subprocess call site. |
| `host_resume_script_path` | `config/gateway*.yaml` | `src/agent_sandbox/host_heartbeat_worker.py` subprocess call site. |
| `host_heartbeat_wake_state_path` | `config/gateway*.yaml` | `src/agent_sandbox/host_heartbeat_worker.py`. |
| `host_heartbeat_resume_prompt` | `config/gateway*.yaml` | `src/agent_sandbox/host_heartbeat_worker.py`. |
| `host_heartbeat_vm_user` | `config/gateway*.yaml` | `src/agent_sandbox/host_heartbeat_worker.py`. |
| `host_heartbeat_resume_timeout_seconds` | `config/gateway*.yaml` | `src/agent_sandbox/host_heartbeat_worker.py`. |
| `host_heartbeat_stream_conflict_policy` | `config/gateway*.yaml` | `src/agent_sandbox/host_heartbeat_worker.py`. |
| `pr_map_path` | `config/gateway*.yaml` | `src/agent_sandbox/host_heartbeat_worker.py` PR map loading. |

## 3) Cloud-init template placeholders (`config/cloud-init-user-data.yaml`)

These placeholders are substituted by `scripts/launch_agent_vm.sh` before VM boot.

| Placeholder / variable | Found in | Consumed by |
|---|---|---|
| `__SSH_PUBLIC_KEY__` | `config/cloud-init-user-data.yaml` | `scripts/launch_agent_vm.sh` injects selected SSH public key. |
| `__GATEWAY_URL__` | `config/cloud-init-user-data.yaml` | `scripts/launch_agent_vm.sh`; written to guest `/etc/agent-sandbox/session.env` as `AGENT_SANDBOX_GATEWAY_URL`. |
| `__SESSION_ID__` | `config/cloud-init-user-data.yaml` | `scripts/launch_agent_vm.sh`; written as `AGENT_SANDBOX_SESSION_ID`. |
| `__LLM_BASE_URL__` | `config/cloud-init-user-data.yaml` | `scripts/launch_agent_vm.sh`; written as `AGENT_SANDBOX_LLM_BASE_URL` and `OPENAI_BASE_URL`. |
| `__NUGET_FALLBACK_PACKAGES__` | `config/cloud-init-user-data.yaml` | `scripts/launch_agent_vm.sh`; written as `AGENT_SANDBOX_NUGET_FALLBACK_PACKAGES`. |
| `__NUGET_PACKAGES__` | `config/cloud-init-user-data.yaml` | `scripts/launch_agent_vm.sh`; written as `NUGET_PACKAGES`. |
| `__NUGET_HTTP_CACHE_PATH__` | `config/cloud-init-user-data.yaml` | `scripts/launch_agent_vm.sh`; written as `NUGET_HTTP_CACHE_PATH`. |
| `__NUGET_CACHE_FS_TYPE__`, `__NUGET_CACHE_MOUNT_OPTIONS__`, `__NUGET_CACHE_TAG__` | `config/cloud-init-user-data.yaml` | `scripts/launch_agent_vm.sh`; mount + `/etc/fstab` entry for shared NuGet cache. |
| `OPENAI_API_KEY` fallback in profile (`relay-placeholder`) | `config/cloud-init-user-data.yaml` | Guest runtime bootstrap fallback for relay-based sessions. |

## Directory Config Domains And Storage

Directory-scoped config is additive and host-side. It does not replace the existing global
gateway YAML or `config/*.env*` files during transition.

Storage paths:

| Path | Source of truth | Used by |
|---|---|---|
| `runtime/directories.json` (or `directories_store_path`) | Directory tree metadata, canonical ownership, configured domains | `src/agent_sandbox/directories.py`, `src/agent_sandbox/directory_admin.py`, `src/agent_sandbox/ownership.py` |
| `runtime/directory-config/root/.../<domain>.json` | Raw per-domain overlay payloads for directories listed in `directories.json` metadata | `src/agent_sandbox/directory_config.py` |

Supported directory config domains:

| Domain file | Purpose | YAML fields allowed |
|---|---|---|
| `policy.json` | Session/policy defaults and operator restrictions | `default_session_ttl_seconds`, Discord/email allowlists, `email_send_enabled`, `pr_reply_require_approval`, `redacted_keys` |
| `providers.json` | Upstream/provider endpoints and credentials-by-reference | Discord, OpenAI, NuGet, GitHub, Azure DevOps, mailbox provider settings |
| `workers.json` | Host worker polling/resume/busy-check behavior | host heartbeat timing, scripts, conflict policy, wake-state paths |
| `vm-defaults.json` | Guest-facing relay/network defaults | `bind_network_cidr`, `gateway_url`, OpenAI relay host/port, NuGet relay host/port |

Merge behavior in `src/agent_sandbox/directory_config.py`:

- scalars: last write wins
- maps/objects: deep merge
- lists: replace
- nullable fields: explicit `null` clears inherited value when validation permits it

Inheritance path:

- effective config is resolved in ancestor order: `root -> ... -> selected`
- if present, `runtime/directory-config/root/*.json` is the top of the directory-scoped inheritance chain
- provenance metadata records which directory last set each effective value
- when `enable_directory_scoped_config` is disabled, workers and admin edit surfaces keep the
  current global behavior and directory config stays in inspection/transition mode

## 4) Additional env vars consumed directly in code paths

These are not primarily defined in `*.env.example` but are explicit configuration points in code:

| Parameter | Found in | Consumed by |
|---|---|---|
| `AGENT_SANDBOX_GIT_ROOT` | `src/agent_sandbox/cli.py` | `agent-sandbox` CLI git-remote helpers (`resolve_git_root`). |
| `MSSQL_SA_PASSWORD` | `src/agent_sandbox/cli.py`, `scripts/setup_issue_vm.sh`, bootstrap scripts | Required when MSSQL bootstrap paths are enabled. |
| Provider mailbox tokens (`GMAIL_*_ACCESS_TOKEN`, `GRAPH_*_ACCESS_TOKEN`, etc.) | Referenced indirectly in `config/gateway*.yaml` under `auth_secret_refs.access_token` | `src/agent_sandbox/runtime.py`, `src/agent_sandbox/email/gmail_provider.py`, `src/agent_sandbox/email/graph_provider.py`. |

---

If you update configuration behavior, also update:

- `config/*.env.example`
- `config/gateway.example.yaml`
- `docs/operations.md` / `README.md`
- tests validating defaults and docs.

## 5) Script-level environment override parameters

The following environment variables are consumed directly by scripts as runtime overrides (typically via `${VAR:-default}` patterns).

| Script | Environment variables consumed as overrides |
|---|---|
| `scripts/apply_firewall.sh` | `ALLOWED_TCP_PORTS` |
| `scripts/bootstrap_codex_vm.sh` | `SSH_KEY_PATH`, `HOST_CODEX_ROOT`, `LLM_BASE_URL`, `ENABLE_GUEST_HEARTBEAT`, `GUEST_HEARTBEAT_PROMPT`, `OPENAI_API_KEY` |
| `scripts/bootstrap_dotnet_vm.sh` | `SSH_KEY_PATH`, `HOST_DOTNET_ROOT`, `HOST_DOTNET_TOOLS_ROOT`, `HOST_NUGET_PACKAGES_ROOT` |
| `scripts/bootstrap_frontend_deps_vm.sh` | `SSH_KEY_PATH`, `HOST_NODE_ROOT` |
| `scripts/bootstrap_git_vm.sh` | `SSH_KEY_PATH` |
| `scripts/bootstrap_mssql_server_vm.sh` | `MSSQL_SA_PASSWORD`, `MSSQL_PID`, `HOST_MSSQL_SERVER_ROOT`, `HOST_MSSQL_SERVICE_FILE`, `SSH_KEY_PATH` |
| `scripts/bootstrap_mssql_tools_vm.sh` | `SSH_KEY_PATH`, `HOST_MSSQL_TOOLS_ROOT`, `HOST_MSSQL_ODBC_ROOT` |
| `scripts/bootstrap_mssql_vm.sh` | `MSSQL_PID`, `MSSQL_SA_PASSWORD`, `SSH_KEY_PATH` |
| `scripts/bootstrap_nuget_vm.sh` | `SSH_KEY_PATH`, `NUGET_RELAY_BASE_URL`, `NUGET_FALLBACK_PACKAGES`, `NUGET_PACKAGES_DIR`, `NUGET_HTTP_CACHE_DIR` |
| `scripts/bootstrap_playwright_vm.sh` | `SSH_KEY_PATH`, `HOST_NODE_ROOT`, `HOST_PLAYWRIGHT_NODE_MODULES`, `HOST_PLAYWRIGHT_BROWSERS` |
| `scripts/check_issue_vm.sh` | `SSH_PRIVATE_KEY_PATH`, `VM_WAIT_IP_TIMEOUT_SECONDS`, `VM_WAIT_SSH_TIMEOUT_SECONDS` |
| `scripts/check_session_vm_busy.sh` | `SSH_PRIVATE_KEY_PATH`, `VM_AGENT_BUSY_CONNECT_TIMEOUT_SECONDS` |
| `scripts/create_base_vm.sh` | `VM_STORAGE_DIR`, `DOWNLOAD_URL` |
| `scripts/destroy_agent_vm.sh` | `VM_STORAGE_DIR` |
| `scripts/inspect_nuget_cache.sh` | `SHARED_NUGET_CACHE_DIR` |
| `scripts/launch_agent_vm.sh` | `VM_STORAGE_DIR`, `GATEWAY_URL`, `SSH_PUBLIC_KEY_FILE`, `LLM_BASE_URL`, `SHARED_NUGET_CACHE_DIR`, `SHARED_NUGET_CACHE_TAG`, `SHARED_NUGET_CACHE_MODE`, `RAM_MB`, `VCPUS`, `DISK_SIZE_GB` |
| `scripts/lib/agent_status.sh` | `VM_AGENT_STATE_MAX_AGE_SECONDS` |
| `scripts/list_running_vms.sh` | `SSH_PRIVATE_KEY_PATH`, `VM_AGENT_LIST_CONNECT_TIMEOUT_SECONDS` |
| `scripts/prewarm_nuget_cache.sh` | `SHARED_NUGET_CACHE_DIR`, `NUGET_CONFIG_FILE`, `NUGET_HTTP_CACHE_DIR`, `NUGET_TEMP_DIR` |
| `scripts/resume_issue_agent.sh` | `SSH_PRIVATE_KEY_PATH`, `AGENT_PROVIDER`, `AGENT_MODEL`, `VM_WAIT_IP_TIMEOUT_SECONDS`, `VM_WAIT_SSH_TIMEOUT_SECONDS`, `RESUME_COMMAND_TIMEOUT_SECONDS` |
| `scripts/setup_issue_vm.sh` | `SSH_PUBLIC_KEY_FILE`, `VM_WAIT_IP_TIMEOUT_SECONDS`, `VM_WAIT_SSH_TIMEOUT_SECONDS`, `SSH_PRIVATE_KEY_PATH`, `AGENT_PROVIDER`, `AGENT_MODEL` |
| `scripts/tail_issue_vm_log.sh` | `SSH_PRIVATE_KEY_PATH` |

### Notes on completeness

- Section 1 captures all parameters from `config/*.env.example`.
- Section 2 captures all `GatewayConfig`/`MailboxConfig` YAML keys.
- Section 3 captures all cloud-init template placeholders currently substituted by VM launch.
- Section 5 captures script-level env overrides used directly in shell scripts.

# Configuration Reference

This document lists the **user-configurable parameters** in this repository, where each parameter is defined, and where it is consumed.

Scope included:

- Host bootstrap configuration in `runtime/host-config.json`.
- Environment-variable based configuration (`config/*.env.example`, plus generated guest env files).
- Legacy gateway YAML import keys (from a legacy YAML file), used only to seed `root` during migration.
- Cloud-init template placeholders used by VM launch/bootstrap.

## 1) Service environment files (`config/*.env.example`)

These example files document env vars that services may still consume. The rendered systemd units
load the matching per-service `config/*.env` files as optional inputs, so startup no longer fails
if a file is absent. Runtime configuration comes from `runtime/host-config.json` plus
`runtime/directory-config/root/...`, and secret values can also be supplied through the service
manager environment or optional repo-root `secrets.env`.

## `config/gateway.env.example`

| Parameter | Found in | Consumed by |
|---|---|---|
| `AZURE_DEVOPS_PR_PAT` | `config/gateway.env.example` | `src/agent_sandbox/runtime.py` / `src/agent_sandbox/gateway.py` when PR reply providers use the default `azure_devops_pr_pat_env`. |

## `config/openai-relay.env.example`

| Parameter | Found in | Consumed by |
|---|---|---|
| `OPENAI_API_KEY` | `config/openai-relay.env.example` | `src/agent_sandbox/relays/openai.py` (looked up using config key `openai_api_key_env`, defaulting to `OPENAI_API_KEY`). |

## `config/nuget-relay.env.example`

| Parameter | Found in | Consumed by |
|---|---|---|
| `AZURE_ARTIFACTS_PAT` | `config/nuget-relay.env.example` | `src/agent_sandbox/relays/nuget.py` (looked up via config key `nuget_azure_pat_env`, defaulting to `AZURE_ARTIFACTS_PAT`). |

## `config/firewall.env.example`

| Parameter | Found in | Consumed by |
|---|---|---|
| `ALLOWED_TCP_PORTS` | `config/firewall.env.example` | `scripts/apply_firewall.sh` (nftables allow-list for guest->host TCP ports; current defaults include `22`, `8000`, `11434`, `11666`, and `11777` for the HTTP target relay port). |

## `config/ado-worker.env.example`

| Parameter | Found in | Consumed by |
|---|---|---|
| `ADO_WORK_ITEMS_AZURE_DEVOPS_PAT` | `config/ado-worker.env.example` | `src/agent_sandbox/daemons/ado_issue_worker.py` using `ado_work_items_azure_devops_pat_env` from `config/ado-worker/config.json`. |

## `config/ado-worker/config.json`

This JSON file stores the ADO worker's global settings. The admin GUI's Workers tab edits this
file directly for the `ado` worker.

| Parameter | Found in | Consumed by |
|---|---|---|
| `runtime_dir` | `config/ado-worker/config.json` | `src/agent_sandbox/daemons/ado_issue_worker.py` and runtime path defaults. |
| `poll_interval_seconds` | `config/ado-worker/config.json` | `src/agent_sandbox/daemons/ado_issue_worker.py`. |
| `directories` | `config/ado-worker/config.json` | `src/agent_sandbox/daemons/ado_issue_worker.py` (canonical directories to poll once per cycle; empty list means process nothing). |
| `yaml_output_dir` | `config/ado-worker/config.json` | `src/agent_sandbox/daemons/ado_issue_worker.py`. |
| `setup_log_dir` | `config/ado-worker/config.json` | `src/agent_sandbox/daemons/ado_issue_worker.py`. |
| `processed_store_path` | `config/ado-worker/config.json` | `src/agent_sandbox/daemons/ado_issue_worker.py`. |
| `setup_script_path` | `config/ado-worker/config.json` | `src/agent_sandbox/daemons/ado_issue_worker.py`. |
| `ado_work_items_azure_devops_pat_env` | `config/ado-worker/config.json` | `src/agent_sandbox/daemons/ado_issue_worker.py` (name of the host env var that holds the ADO work-item PAT). |
| `setup_timeout_seconds` | `config/ado-worker/config.json` | `src/agent_sandbox/daemons/ado_issue_worker.py`. |
| `setup_concurrency_limit` | `config/ado-worker/config.json` | `src/agent_sandbox/daemons/ado_issue_worker.py`. |
| `setup_lock_path` | `config/ado-worker/config.json` | `src/agent_sandbox/daemons/ado_issue_worker.py`. |
| `fetch_retry_count` | `config/ado-worker/config.json` | `src/agent_sandbox/daemons/ado_issue_worker.py`. |
| `fetch_retry_backoff_seconds` | `config/ado-worker/config.json` | `src/agent_sandbox/daemons/ado_issue_worker.py`. |
| `include_comments` | `config/ado-worker/config.json` | `src/agent_sandbox/daemons/ado_issue_worker.py`. |
| `comments_max_count` | `config/ado-worker/config.json` | `src/agent_sandbox/daemons/ado_issue_worker.py`. |
| `comment_text_max_chars` | `config/ado-worker/config.json` | `src/agent_sandbox/daemons/ado_issue_worker.py`. |

`ADO_*` environment variables still work as compatibility fallbacks for one release, but the new
persisted source of truth is `config/ado-worker/config.json`. Secret values such as
`MSSQL_SA_PASSWORD` remain host-supplied via the shell or `secrets.env`. The work-item Azure
DevOps PAT value is read from the host env var named by `ado_work_items_azure_devops_pat_env`,
defaulting to `ADO_WORK_ITEMS_AZURE_DEVOPS_PAT`.

## `config/issue-orchestrator/config.json`

This JSON file stores the retired issue orchestrator worker's global settings. The worker is
obsolete, `scripts/install_systemd_units.sh` no longer installs/enables its systemd unit, and new
deployments should leave this config unused unless they are intentionally reviving that legacy
scaffold.

| Parameter | Found in | Consumed by |
|---|---|---|
| `runtime_dir` | `config/issue-orchestrator/config.json` | Future issue orchestrator worker runtime path defaults. |
| `directories` | `config/issue-orchestrator/config.json` | Future issue orchestrator worker directory list. |
| `poll_interval_seconds` | `config/issue-orchestrator/config.json` | Future issue orchestrator worker sleep cadence. |
| `jitter_seconds` | `config/issue-orchestrator/config.json` | Future issue orchestrator worker jitter. |
| `cooldown_seconds` | `config/issue-orchestrator/config.json` | Future issue orchestrator worker per-session cooldown. |
| `lock_path` | `config/issue-orchestrator/config.json` | Future issue orchestrator single-instance lock. |
| `busy_check_script_path` | `config/issue-orchestrator/config.json` | Future issue orchestrator guest-busy check hook. |
| `resume_script_path` | `config/issue-orchestrator/config.json` | Future issue orchestrator VM resume hook. |
| `wake_state_path` | `config/issue-orchestrator/config.json` | Future issue orchestrator wake state file. |
| `vm_user` | `config/issue-orchestrator/config.json` | Future issue orchestrator guest user name. |
| `resume_timeout_seconds` | `config/issue-orchestrator/config.json` | Future issue orchestrator resume timeout. |

## `config/trigger-orchestrator/config.json`

This JSON file stores the workflow orchestrator worker's global settings.
Trigger definitions are currently global (host-wide) and are **not** directory-scoped; this keeps
provider secret resolution and cross-directory event ingestion centralized until a directory-resolved
workflow inheritance model is introduced. Triggers, gates, and effects all live in the same graph
config, with explicit edges describing routing between node ids. Domain workflows can now be
provided via `workflow_packs` + optional inline `workflow_ir`, which compose into executable
`triggers`/`gates`/`effects`/`queries`/`edges` at load time.
Provider plugin loading is configured separately under `provider_loader`.
The live `config.json` is an operator-local file ignored by Git; use
`config/trigger-orchestrator/config-example.json` as the checked-in starting point.

### Orchestration-core runtime profile template

For orchestration-only deployments, use `config/trigger-orchestrator/orchestration-core.config.json.example` as the starting point. It sets an explicit runtime path (`runtime/orchestration-core`) and defaults provider loading to `profiles/core-minimal` (no built-ins) with entry-point loading disabled.

| Parameter | Found in | Consumed by |
|---|---|---|
| `runtime_dir` | `config/trigger-orchestrator/config.json` | `src/agent_sandbox/config.py` runtime path defaults for trigger orchestrator state files. |
| `poll_interval_seconds` | `config/trigger-orchestrator/config.json` | Trigger orchestrator worker polling cadence. |
| `jitter_seconds` | `config/trigger-orchestrator/config.json` | Trigger orchestrator worker jitter between cycles. |
| `lock_path` | `config/trigger-orchestrator/config.json` | Trigger orchestrator single-instance lock file path. |
| `event_queue_store_path` | `config/trigger-orchestrator/config.json` | Trigger orchestrator persisted event queue store path. |
| `event_queue_cursor_store_path` | `config/trigger-orchestrator/config.json` | Trigger orchestrator event queue cursor/checkpoint store path. |
| `workflow_packs` | `config/trigger-orchestrator/config.json` | Ordered workflow-pack filenames under `config/workflow-packs/` to compose into executable IR. |
| `workflow_ir` | `config/trigger-orchestrator/config.json` | Optional inline IR overlay (`version`, `nodes`, `edges`) merged after pack composition. |
| `workflow_transforms` | `config/trigger-orchestrator/config.json` | Optional ordered transform plugin names (for example `await_agent_completion`) applied after structural compile/validation. |
| `triggers` | `config/trigger-orchestrator/config.json` | Trigger orchestrator trigger definition list (`src/agent_sandbox/triggers/models.py`). |
| `gate_execution_store_path` | `config/trigger-orchestrator/config.json` | Gate execution log path (idempotency keys, merge state, resolved config snapshots, outputs, failures). |
| `gate_cursor_store_path` | `config/trigger-orchestrator/config.json` | Gate orchestrator cursor/checkpoint path. |
| `gate_lock_path` | `config/trigger-orchestrator/config.json` | Gate orchestrator single-instance lock file path. |
| `effects` | `config/trigger-orchestrator/config.json` | Effect definition list (`src/agent_sandbox/events/models.py`). Effects no longer take `upstream_id`; they are wired with edges. |
| `gates` | `config/trigger-orchestrator/config.json` | Gate definition list (filter/map/merge nodes). |
| `edges` | `config/trigger-orchestrator/config.json` | Explicit graph wiring between triggers, gates, and effects. |
| `max_graph_depth` | `config/trigger-orchestrator/config.json` | Maximum allowed downstream graph depth during workflow execution safety checks. |
| `max_events_per_cycle` | `config/trigger-orchestrator/config.json` | Maximum workflow events processed per effect-orchestrator cycle. |
| `provider_loader.provider_profile` | `config/trigger-orchestrator/config.json` | Selects a named provider loading profile (for example `profiles/core-minimal` or compatibility `profiles/agent-default`). |
| `provider_loader.include_builtin` | `config/trigger-orchestrator/config.json` | Optional override for implicit built-in provider loading; defaults from selected profile. |
| `provider_loader.provider_modules` | `config/trigger-orchestrator/config.json` | Additional provider plugin Python modules to load after selected profile modules. |
| `provider_loader.provider_entry_points_enabled` | `config/trigger-orchestrator/config.json` | Enables provider plugin discovery via Python entry points. |

### Trigger definition schema keys

Each trigger entry requires the shared envelope keys:

- `id` (string, required, unique across all triggers)
- `display_name` (string, required)
- `type` (required discriminator)

Type-specific keys:

| `type` | Required keys | Optional keys |
|---|---|---|
| `trigger:cron` | `expression` | — |
| `trigger:ticket:created` | `directories` | — |
| `trigger:ticket:changed` | `directories` | — |
| `trigger:script-run-request` | — | — |
| `trigger:discord:new-message` | `discord_bot_token_env` | `channel_ids`, `user_ids` |
| `trigger:email:new-message` | `provider`, `account_identifier`, `mailbox_ids` | `auth_secret_refs` |
| `trigger:pull-request:new-feedback` | `provider` | `organization_url`, `project` (but `organization_url` becomes required when `provider=ado`) |
| `trigger:review:new-request` | — | — |
| `trigger:git:push` | `path_to_repo` | — |

The current trigger set also includes `trigger:teams:new-message`, `trigger:review:new-request`,
and `trigger:script-run-request`. Teams is fed by a host-side ingest worker that reads the same
delegated Teams auth settings described above. Review requests are fed from the host-owned
`runtime/review-request-store.json` inbox populated by `request_pr_review(...)`. Script run
requests are fed from `runtime/script-run-requests-store.json`, which the `run_script(...)` MCP
tool appends to after validating that the calling session is allowed to invoke the requested
script name. The checked-in global workflow currently filters that trigger down to
`provision_exampleindustry_app_demo_environment.sh`, which wraps the existing host-side Example
Industry APP Azure provisioning script and resumes the originating session with the JSON result.
All three flows are independent from the wake workers; Discord inbox waking and scheduled resumes are covered by the dedicated wake workers while
PR/Discord feedback wakeups.

Example:

```json
{
  "runtime_dir": "runtime",
  "poll_interval_seconds": 5,
  "jitter_seconds": 5,
  "triggers": [
    {
      "id": "cron-15m",
      "display_name": "Every 15 minutes",
      "type": "trigger:cron",
      "expression": "*/15 * * * *"
    },
    {
      "id": "mailbox-ops",
      "display_name": "Ops inbox",
      "type": "trigger:email:new-message",
      "provider": "graph",
      "account_identifier": "ops@example.com",
      "mailbox_ids": ["primary"],
      "auth_secret_refs": {
        "access_token": "GRAPH_ACCESS_TOKEN"
      }
    },
    {
      "id": "repo-main",
      "display_name": "Repo push",
      "type": "trigger:git:push",
      "path_to_repo": "/var/lib/git/my-repo.git"
    }
  ]
}
```

### Gate definition schema keys

Each gate entry requires the shared envelope keys:

- `id` (string, required, unique across all gates)
- `display_name` (string, required)
- `type` (required discriminator)
- `gate_type` (string, required; one of `filter`, `map`, `merge`)

Type-specific keys:

| `type` | Required keys | Optional keys |
|---|---|---|
| `gate:filter` | `predicate` | `outputs` |
| `gate:map` | `mapping` | `outputs` |
| `gate:merge` | _none beyond envelope_ | `outputs` |

`gate:merge` uses Reactive Extensions `merge` semantics: every inbound event is forwarded as it
arrives. When `outputs` is declared, the gate projects each inbound payload to that shared key set.
The runtime also records the latest inbound payload seen from each upstream source in the gate
execution log for debugging.

Edge wiring records connect upstream node ids to downstream node ids and replace any node-local
`upstream_id` field.

Example edge shape:

```json
{
  "from": "ticket-created",
  "to": "effect-provision"
}
```

### Effect definition schema keys

Each effect entry requires the shared envelope keys:

- `id` (string, required, unique across all effects)
- `display_name` (string, required)
- `type` (required discriminator)

Type-specific keys:

| `type` | Required keys | Optional keys |
|---|---|---|
| `effect:vm:create` | _none beyond envelope_ | `vm_name`, `session_id`, `model`, `vm_user`, `ttl_hours`, `allowed_tools`, `allowed_script_names`, `discord_channels`, `email_mailboxes`, `email_folders`, `dotnet`, `nuget`, `mssql`, `mssql_init_root`, `playwright`, `codex_skills`, `directory_path`, `outputs` |
| `effect:vm:clone-repo` | `repo_source` | `target`, `session_id`, `vm_name`, `vm_user`, `directory_path`, `outputs` |
| `effect:vm:bootstrap-python` | `host_checkout`, `target_dir` | `target`, `session_id`, `vm_name`, `vm_user`, `outputs` |
| `effect:vm:bootstrap-frontend` | `host_checkout`, `install_roots` | `target`, `session_id`, `vm_name`, `vm_user`, `outputs` |
| `effect:vm:start` | _none beyond envelope_ | `target`, `session_id`, `vm_name`, `outputs` |
| `effect:vm:stop` | _none beyond envelope_ | `target`, `session_id`, `vm_name`, `timeout_seconds`, `outputs` |
| `effect:agent:prompt` | `prompt` | `target`, `session_id`, `vm_name`, `vm_user`, `resume_thread_id`, `output_mode`, `outputs` |
| `effect:agent:exec` | `command` | `target`, `session_id`, `vm_name`, `vm_user`, `working_directory`, `environment`, `stdin`, `timeout_seconds`, `check`, `outputs` |
| `effect:bash:script` | `script_path` | `args`, `working_directory`, `environment`, `stdin`, `timeout_seconds`, `check`, `outputs` |
| `effect:vm:exec` | `command` | `target`, `session_id`, `vm_name`, `vm_user`, `working_directory`, `environment`, `stdin`, `timeout_seconds`, `check`, `outputs` |
| `effect:vm:copy-files` | `files` | `target`, `session_id`, `vm_name`, `vm_user`, `outputs` |
| `effect:issue:update-state` | `issue_id`, `state` | `sync_proxy_cache`, `outputs` |
| `effect:pr:create` | `host_repo_path` | `local_branch`, `origin_branch`, `provider`, `base_branch`, `session_id`, `work_item_id`, `title`, `body`, `body_file`, `draft`, `fill`, `outputs` |

Effect schema envelope example:

```json
{
  "id": "effect-provision",
  "display_name": "Provision VM for ticket",
  "type": "effect:vm:create",
  "vm_name": "issue-${{trigger.ticket_number}}",
  "outputs": {
    "session_id": { "type": "string" }
  }
}
```

The checked-in APP provisioning workflow uses `effect:bash:script` with this repo's
`scripts/provision_exampleindustry_app_demo_environment.sh`, projects `stdout` plus parsed JSON fields such as `app_base_url`, `app_public_url`,
`app_api_alias`, `app_ui_alias`, and `public_domain` as declared outputs, stores those values in
session metadata, and then resumes the originating agent session.

### Graph examples

Filter graph:

```json
{
  "triggers": [
    {
      "id": "ticket-created",
      "display_name": "Ticket created",
      "type": "trigger:ticket:created",
      "directories": ["root/team-a"]
    }
  ],
  "gates": [
    {
      "id": "high-priority-only",
      "display_name": "High priority only",
      "type": "gate:filter",
      "gate_type": "filter",
      "predicate": "${{trigger.priority}} >= 2"
    }
  ],
  "effects": [
    {
      "id": "effect-provision",
      "display_name": "Create VM",
      "type": "effect:vm:create",
      "vm_name": "issue-${{trigger.ticket_number}}"
    }
  ],
  "edges": [
    {"from": "ticket-created", "to": "high-priority-only"},
    {"from": "high-priority-only", "to": "effect-provision"}
  ]
}
```

Map graph:

```json
{
  "gates": [
    {
      "id": "normalize-ticket",
      "display_name": "Normalize ticket fields",
      "type": "gate:map",
      "gate_type": "map",
      "mapping": {
        "session_id": "${{trigger.ticket_session_id}}",
        "ticket_number": "${{trigger.ticket_id}}"
      }
    }
  ],
  "edges": [
    {"from": "ticket-created", "to": "normalize-ticket"},
    {"from": "normalize-ticket", "to": "effect-resume"}
  ]
}
```

Merge graph:

```json
{
  "gates": [
    {
      "id": "merge-pr-and-ticket",
      "display_name": "Merge PR and ticket payloads",
      "type": "gate:merge",
      "gate_type": "merge",
      "outputs": {
        "session_id": { "type": "string" },
        "ticket_number": { "type": "integer" }
      }
    }
  ],
  "edges": [
    {"from": "ticket-created", "to": "merge-pr-and-ticket"},
    {"from": "pr-feedback", "to": "merge-pr-and-ticket"},
    {"from": "merge-pr-and-ticket", "to": "effect-resume"}
  ]
}
```

Mixed graph:

```json
{
  "gates": [
    {
      "id": "filter-inbox",
      "display_name": "Filter inbox events",
      "type": "gate:filter",
      "gate_type": "filter",
      "predicate": "${{trigger.folder}} == \"inbox\""
    },
    {
      "id": "map-inbox",
      "display_name": "Map inbox fields",
      "type": "gate:map",
      "gate_type": "map",
      "mapping": {
        "session_id": "${{trigger.thread_session_id}}",
        "ticket_number": "${{trigger.issue_number}}"
      }
    },
    {
      "id": "merge-context",
      "display_name": "Merge inbox and PR context",
      "type": "gate:merge",
      "gate_type": "merge",
      "outputs": {
        "session_id": { "type": "string" }
      }
    }
  ],
  "edges": [
    {"from": "email-new-message", "to": "filter-inbox"},
    {"from": "filter-inbox", "to": "map-inbox"},
    {"from": "map-inbox", "to": "merge-context"},
    {"from": "pr-feedback", "to": "merge-context"},
    {"from": "merge-context", "to": "effect-resume"}
  ]
}
```

### Binding/interpolation examples (`${{node.output_name}}`)

- Exact output reference (type-preserving): `"session_id": "${{node.session_id}}"`
- Embedded string interpolation (stringified): `"prompt": "Continue ${{trigger.ticket_number}} in ${{node.session_id}}"`

### Wake scheduling examples

- One-time (MCP `schedule_resume`) payload shape:

```json
{
  "session_id": "issue-11-session",
  "resume_at": "2026-04-01T10:30:00Z",
  "prompt": "Resume pending feedback triage.",
  "resume_mode": "same_context",
  "source": "mcp_schedule_resume"
}
```

- Recurring cron schedule (host CLI):

```bash
agent-sandbox-cli wake-schedules create \
  --runtime-dir runtime \
  --session-id issue-11-session \
  --cron "*/15 * * * *" \
  --prompt "Periodic wake: check unread feedback." \
  --mode new_context \
  --source admin_cli
```

## Root issue setup and git automation config

The retired git automation env file used to collect these values. They now come from
root directory-scoped config and host environment:

| Parameter | Found in | Consumed by |
|---|---|---|
| `AGENT_SANDBOX_GIT_ROOT` | `runtime/directory-config/root/issue-setup.json` | `src/agent_sandbox/cli.py` and git automation scripts (`create_git_remote.sh`, `git_ssh_command_wrapper.sh`, `promote_agent_branch.sh`). |
| `AGENT_SANDBOX_GIT_HOST` | `runtime/directory-config/root/issue-setup.json` | `scripts/bootstrap_git_vm.sh`, `clone_repo_into_vm(...)`. |
| `AGENT_SANDBOX_GIT_USER` | `runtime/directory-config/root/issue-setup.json` | `scripts/bootstrap_git_vm.sh`, `clone_repo_into_vm(...)`. |
| `AGENT_SANDBOX_GIT_SSH_ALIAS` | `runtime/directory-config/root/issue-setup.json` | `scripts/bootstrap_git_vm.sh`, `clone_repo_into_vm(...)`. |
| `ENABLE_GUEST_HEARTBEAT` | `runtime/directory-config/root/issue-setup.json` | `create_issue_vm(...)` (guest heartbeat toggle). |
| `GUEST_HEARTBEAT_PROMPT` | `runtime/directory-config/root/issue-setup.json` | `create_issue_vm(...)` (guest heartbeat prompt). |
| `AGENT_POST_EXEC_VERIFICATION_PROMPT` | `runtime/directory-config/root/issue-setup.json` | `create_issue_vm(...)` (post-exec resume prompt metadata). |
| `AGENT_SANDBOX_AZURE_DEVOPS_PR_PAT_ENV` | `runtime/directory-config/root/providers.json` or host shell | `scripts/open_agent_pr.sh`, `scripts/resolve_repo_source.sh`, and `scripts/git_ssh_command_wrapper.sh` (alternate env var name for Azure DevOps PAT). |
| `AZURE_DEVOPS_PR_PAT` | host shell or `secrets.env` | `scripts/open_agent_pr.sh`, `scripts/resolve_repo_source.sh`, and `scripts/git_ssh_command_wrapper.sh` when Azure DevOps remotes are accessed over HTTPS. |
| `AGENT_SANDBOX_PR_NOTES_REF` | host shell | `scripts/handle_agent_push.sh` (PR notes ref). |
| `AGENT_SANDBOX_PR_FIXED_BODY_FILE` | host shell | `scripts/handle_agent_push.sh` (host-wide override that prepends markdown to PR body). |
| `AGENT_SANDBOX_PR_BODY_MAX_BYTES` | host shell | `scripts/handle_agent_push.sh` (body truncation limit). |
| `AGENT_SANDBOX_NOTIFY_COMMAND` | host shell | `scripts/handle_agent_push.sh` (post-promotion host notification command). |

## 2) Host bootstrap config (`runtime/host-config.json`)

This file is the live host-global bootstrap configuration. It contains process-level bind settings,
runtime/store paths, and feature flags that are intentionally not inherited per directory.

Key bootstrap fields include:

- `runtime_dir`
- `log_level`
- `gateway_host`, `gateway_port`
- `openai_relay_host`, `openai_relay_port`
- `nuget_relay_host`, `nuget_relay_port`
- `audit_log_path`
- `session_store_path`, `approval_store_path`, `draft_store_path`, `email_store_path`, `email_cursor_store_path`
- `discord_command_store_path`, `directories_store_path`, `pr_map_path`, `host_agent_control_state_path`
- `enable_directory_scoped_config`

## 3) Legacy gateway import keys

These keys are defined in `src/agent_sandbox/config.py` (`GatewayConfig` + `MailboxConfig`) and were historically loaded by `load_config()`.
In the root-only model, this file is an import source for seeding `runtime/directory-config/root/...` and is not the live runtime baseline.

### Core server/runtime keys

| Key | Found in | Consumed by |
|---|---|---|
| `host`, `port` | legacy gateway YAML | `src/agent_sandbox/gateway.py` server bind settings. |
| `log_level` | legacy gateway YAML | `src/agent_sandbox/gateway.py` logging setup for gateway process. |
| `runtime_dir` | legacy gateway YAML | `src/agent_sandbox/runtime.py` store locations. |
| `audit_log_path` | legacy gateway YAML | `src/agent_sandbox/runtime.py` audit logger destination. |
| `session_store_path`, `approval_store_path`, `draft_store_path`, `email_store_path`, `email_cursor_store_path`, `discord_command_store_path` | legacy gateway YAML | `src/agent_sandbox/runtime.py` persistent JSON stores. |
| `directories_store_path` | legacy gateway YAML | `src/agent_sandbox/runtime.py`, `src/agent_sandbox/directories.py`, directory admin/config services. |
| `enable_directory_scoped_config` | legacy gateway YAML | `src/agent_sandbox/admin/directory.py`, `src/agent_sandbox/admin/app.py`, `src/agent_sandbox/daemons/ado_issue_worker.py`, and `src/agent_sandbox/daemons/pr_feedback_worker.py` (transition flag for directory edit surfaces and worker shadow resolution). |
| `approval_ttl_seconds` | legacy gateway YAML | `src/agent_sandbox/policy.py` approval expiry computation. |
| `default_session_ttl_seconds` | legacy gateway YAML | `src/agent_sandbox/cli.py` session creation defaults. |
| `bind_network_cidr` | legacy gateway YAML | `src/agent_sandbox/config.py` validation/normalization (policy-bound network constraints). |
| `gateway_url` | legacy gateway YAML | `src/agent_sandbox/cli.py` health checks / external endpoint references. |
| `execution_backend`, `container_worker_base_url`, `container_worker_token_env`, `container_worker_default_image`, `container_worker_job_timeout_seconds`, `container_worker_request_timeout_seconds`, `container_worker_workspace_persistence` | root `providers.json` | `src/agent_sandbox/execution/container_worker_backend.py` and portable `effect:agent:*` container worker routing. |
| `container_worker_guest_openai_base_url`, `container_worker_guest_gateway_url`, `container_worker_guest_nuget_relay_base_url`, `container_worker_guest_pypi_index_url`, `container_worker_guest_npm_registry_url` | root `providers.json` | Container session bootstrap files under `/home/agent` for Codex, MCP gateway, NuGet restore traffic, and Nexus-backed PyPI/npm restores. |
| `redacted_keys` | legacy gateway YAML (optional) | `src/agent_sandbox/gateway.py` request/response redaction in logs. |

### OpenAI relay keys

| Key | Found in | Consumed by |
|---|---|---|
| `openai_relay_host`, `openai_relay_port` | legacy gateway YAML | `src/agent_sandbox/relays/openai.py` listener bind. |
| `openai_api_base_url` | legacy gateway YAML | `src/agent_sandbox/relays/openai.py` upstream URL base. |
| `openai_api_key_env` | legacy gateway YAML | `src/agent_sandbox/relays/openai.py` env-var name lookup for API key. |
| `openai_relay_timeout_seconds` | legacy gateway YAML | `src/agent_sandbox/relays/openai.py` upstream request timeout. |

### HTTP target relay keys

| Key | Found in | Consumed by |
|---|---|---|
| `http_target_relay_host`, `http_target_relay_port` | root `host-config.json`, provider config overlays | `src/agent_sandbox/relays/http_target.py` listener bind and `src/agent_sandbox/cli.py` status checks. |
| `http_target_relay_timeout_seconds` | provider config overlays | `src/agent_sandbox/relays/http_target.py` upstream request timeout. |

The current HTTP target relay is auth-transparent: it allowlists the upstream destination by
session and per-target alias, but it forwards guest `Authorization` headers and cookies instead of
injecting host-side credentials. The APP demo workflow is one consumer layered on top of this
generic relay pattern and can register separate UI and API aliases for the same provisioned
environment.

### NuGet relay keys

| Key | Found in | Consumed by |
|---|---|---|
| `nuget_relay_host`, `nuget_relay_port` | legacy gateway YAML | `src/agent_sandbox/relays/nuget.py` listener bind. |
| `nuget_public_feed_base_url` | legacy gateway YAML | `src/agent_sandbox/relays/nuget.py` public feed proxy target. |
| `nuget_azure_feed_base_url` | legacy gateway YAML | `src/agent_sandbox/relays/nuget.py` Azure feed proxy target. |
| `nuget_azure_pat_env` | legacy gateway YAML | `src/agent_sandbox/relays/nuget.py` env-var name lookup for Azure Artifacts PAT. |
| `nuget_relay_timeout_seconds` | legacy gateway YAML | `src/agent_sandbox/relays/nuget.py` upstream timeout. |

### Discord keys

| Key | Found in | Consumed by |
|---|---|---|
| `discord_backend` | legacy gateway YAML | `src/agent_sandbox/runtime.py` adapter selection (`stub`/`bot_api`). |
| `discord_bot_token_env` | legacy gateway YAML | `src/agent_sandbox/runtime.py` token env lookup. |
| `discord_api_base_url` | legacy gateway YAML | `src/agent_sandbox/runtime.py` bot API client base URL. |
| `discord_poll_limit` | legacy gateway YAML | `src/agent_sandbox/runtime.py` polling window size. |
| `discord_timeout_seconds` | legacy gateway YAML | `src/agent_sandbox/runtime.py` HTTP timeout. |
| `discord_retry_count` | legacy gateway YAML | `src/agent_sandbox/runtime.py` retry behavior. |
| `discord_retry_base_backoff_seconds` | legacy gateway YAML | `src/agent_sandbox/runtime.py` retry backoff floor. |
| `discord_retry_max_backoff_seconds` | legacy gateway YAML | `src/agent_sandbox/runtime.py` retry backoff ceiling. |
| `discord_read_reaction_emoji` | legacy gateway YAML | Legacy adapter config retained for compatibility. The gateway no longer applies unread-message read reactions automatically; use `add_message_reaction` explicitly. |
| `discord_operator_user_allowlist` | legacy gateway YAML | `src/agent_sandbox/runtime.py` and `src/agent_sandbox/policy.py` authorization checks. |
| `discord_operator_channel_allowlist` | legacy gateway YAML | `src/agent_sandbox/runtime.py` channel filter for operator events. |
| `discord_poll_operator_dms` | legacy gateway YAML | `src/agent_sandbox/runtime.py` DM polling toggle. |

### Email keys

| Key | Found in | Consumed by |
|---|---|---|
| `email_mailboxes[]` | legacy gateway YAML | `src/agent_sandbox/runtime.py` mailbox provider adapter construction. |
| `email_mailboxes[].mailbox_id` | legacy gateway YAML | `src/agent_sandbox/runtime.py`, `src/agent_sandbox/policy.py`, validation in `src/agent_sandbox/config.py`. |
| `email_mailboxes[].provider` | legacy gateway YAML | `src/agent_sandbox/runtime.py` provider type dispatch (`json_store`/`gmail`/`graph`). |
| `email_mailboxes[].account_identifier` (`account`/`address` aliases) | legacy gateway YAML | `src/agent_sandbox/email/gmail_provider.py`, `src/agent_sandbox/email/graph_provider.py`. |
| `email_mailboxes[].auth_secret_refs` (`auth_env_var_names` alias) | legacy gateway YAML | `src/agent_sandbox/runtime.py` + provider token resolution. |
| `email_mailboxes[].folder_mapping` | legacy gateway YAML | Gmail/Graph provider folder mapping. |
| `email_mailboxes[].folder_allowlist_defaults` | legacy gateway YAML | Defaulting logic in `src/agent_sandbox/config.py`. |
| `email_mailbox_allowlist` | legacy gateway YAML | `src/agent_sandbox/cli.py` session manifest policy surface. |
| `email_folder_allowlist_by_mailbox` | legacy gateway YAML | `src/agent_sandbox/cli.py` / `src/agent_sandbox/policy.py`. |
| `email_draft_recipient_allowlist` | legacy gateway YAML | `src/agent_sandbox/policy.py` recipient checks. |
| `email_draft_domain_allowlist` | legacy gateway YAML | `src/agent_sandbox/policy.py` domain checks. |
| `email_send_enabled` | legacy gateway YAML | `src/agent_sandbox/cli.py`, `src/agent_sandbox/gateway.py`, `src/agent_sandbox/policy.py`. |
| `email_provider_timeout_seconds` | legacy gateway YAML | `src/agent_sandbox/runtime.py` email + PR provider HTTP client timeouts. |

### PR/feedback keys

| Key | Found in | Consumed by |
|---|---|---|
| `pr_reply_require_approval` | legacy gateway YAML | `src/agent_sandbox/gateway.py` PR-reply approval gate. |
| `github_token_env` | legacy gateway YAML | `src/agent_sandbox/runtime.py`, `src/agent_sandbox/reviews/request_service.py`, and PR feedback trigger providers. |
| `azure_devops_pr_pat_env` | legacy gateway YAML | `src/agent_sandbox/runtime.py`, `src/agent_sandbox/daemons/ado_issue_worker.py`, `src/agent_sandbox/reviews/request_service.py`, and PR feedback trigger providers. |
| `azure_devops_organization_url` | legacy gateway YAML | `src/agent_sandbox/runtime.py`, `src/agent_sandbox/daemons/ado_issue_worker.py`, `src/agent_sandbox/issues/service.py`, and PR feedback trigger providers. |
| `azure_devops_project` | legacy gateway YAML | `src/agent_sandbox/runtime.py`, `src/agent_sandbox/daemons/ado_issue_worker.py`, `src/agent_sandbox/issues/service.py`, and PR feedback trigger providers. |
| `host_heartbeat_pr_resume_prompt` | legacy gateway YAML | Legacy prompt key retained for directory-config compatibility; the `agent-sandbox-host-heartbeat.service` consumer is retired. |
| `discord_resume_prompt` | legacy gateway YAML | `src/agent_sandbox/messaging/discord_wake.py`. |
| `pr_map_path` | legacy gateway YAML | `src/agent_sandbox/daemons/ado_issue_worker.py`, PR feedback trigger providers, ownership cleanup, and admin/session pruning. |

## 4) Cloud-init template placeholders (`config/cloud-init-user-data.yaml`)

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

Directory-scoped config is additive and host-side. `root` is the live top-level baseline.
Legacy gateway YAML is only an import source for existing installs; it is not the live
runtime baseline.

Storage paths:

| Path | Source of truth | Used by |
|---|---|---|
| `runtime/directories.json` (or `directories_store_path`) | Directory tree metadata, canonical ownership, configured domains | `src/agent_sandbox/directories.py`, `src/agent_sandbox/admin/directory.py`, `src/agent_sandbox/ownership.py` |
| `runtime/directory-config/root/.../<domain>.json` | Raw per-domain overlay payloads for directories listed in `directories.json` metadata | `src/agent_sandbox/directory_config.py` |

Supported directory config domains:

| Domain file | Purpose | YAML fields allowed |
|---|---|---|
| `policy.json` | Session/policy defaults and operator restrictions | `approval_ttl_seconds`, `default_session_ttl_seconds`, Discord/email allowlists, `email_send_enabled`, `pr_reply_require_approval`, `redacted_keys` |
| `providers.json` | Upstream/provider endpoints and credentials-by-reference | Discord, OpenAI, HTTP target relay, NuGet, GitHub, Azure DevOps, Teams, container worker |
| `email.json` | Mailbox provider definitions and email provider runtime settings | `email_mailboxes`, `email_provider_timeout_seconds` |
| `heartbeat.json` | Directory-scoped wake prompt behavior | `host_heartbeat_pr_resume_prompt`, `discord_resume_prompt` |
| `ado-worker.json` | Azure DevOps backlog worker setup/bootstrap behavior | `ado_area_filters`, `ado_team_filters`, `ado_project_or_solution`, `ado_prewarm_target`, `ado_repo_source`, `create_prs_automatically`, `create_prs_as_draft`, `ado_bootstrap_*`, `ado_mssql_sa_password_env`, frontend bootstrap fields |
| `issue-setup.json` | VM issue setup/git hand-off defaults | `git_root`, `git_host`, `git_user`, `git_ssh_alias`, optional `default_model`, `post_exec_verification_prompt`, `enable_guest_heartbeat`, `guest_heartbeat_prompt`, optional `issue_setup_azure_devops_pr_pat_env` |
| `pr-template.json` | Directory-scoped markdown boilerplate prepended to automated PR bodies | `pr_template_markdown` |
| `vm.json` | Guest-facing relay/network defaults | `bind_network_cidr`, `gateway_url`, OpenAI relay host/port, NuGet relay host/port, HTTP target relay host/port |

Merge behavior in `src/agent_sandbox/directory_config.py`:

- scalars: last write wins
- maps/objects: deep merge
- lists: replace
- nullable fields: explicit `null` clears inherited value when validation permits it

Inheritance path:

- effective config is resolved in ancestor order: `root -> ... -> selected`
- `runtime/directory-config/root/*.json` is the top of the directory-scoped inheritance chain
- provenance metadata records which directory last set each effective value

Teams delegated auth follows the same host-side model. The preferred operator flow is to sign in on
the host with device code flow and keep the credential host-only. Directory-scoped `providers.json`
overlays can bind a subtree to that host-managed Teams identity, while the legacy static env-token
mode remains supported for compatibility through `teams_graph_access_token_env` (default
`GRAPH_TEAMS_ACCESS_TOKEN`).
The same delegated Teams identity/configuration is also used by the host-side Teams ingest worker
that feeds `trigger:teams:new-message` for both channel threads and group chats.

Key delegated Teams provider settings now include:

- `teams_backend: delegated_graph_api`
- `teams_delegated_identity_id`
- `teams_delegated_client_id`
- optional `teams_delegated_tenant_id`
- optional `teams_delegated_scopes` (default includes `offline_access`,
  `ChannelMessage.Read.All`, `ChannelMessage.Send`, and `Chat.ReadWrite`)
- optional `teams_delegated_authority_base_url`
- optional `teams_delegated_credential_store_path`
- optional `teams_history_page_cap` (default `1`) and `teams_history_deadline_seconds` (default
  `5.0`) bound Microsoft Graph history pagination for `teams_get_messages` and Teams ingest scans
- optional `teams_read_reaction_emoji` (legacy compatibility field; read-side automatic reactions are disabled)
- The app registration referenced by `teams_delegated_client_id` must support public client /
  device-code auth. If Microsoft returns `AADSTS7000218`, the registration currently requires a
  client secret or assertion and is incompatible with this flow.
- Changing `teams_delegated_scopes` affects only newly issued delegated credentials; rerun the
  host `teams-auth login` flow for the directory after scope changes.

## 4) Additional env vars consumed directly in code paths

These are not primarily defined in `*.env.example` but are explicit configuration points in code:

| Parameter | Found in | Consumed by |
|---|---|---|
| `AGENT_SANDBOX_GIT_ROOT` | `src/agent_sandbox/cli.py` | `agent-sandbox` CLI git-remote helpers (`resolve_git_root`). |
| `MSSQL_SA_PASSWORD` | `src/agent_sandbox/cli.py`, `scripts/setup_vm.sh`, bootstrap scripts | Required when MSSQL bootstrap paths are enabled. |
| Provider mailbox tokens (`GMAIL_*_ACCESS_TOKEN`, `GRAPH_*_ACCESS_TOKEN`, etc.) | Referenced indirectly in effective root directory config under `auth_secret_refs.access_token` | `src/agent_sandbox/runtime.py`, `src/agent_sandbox/email/gmail_provider.py`, `src/agent_sandbox/email/graph_provider.py`. |
| Teams delegated Graph token env name (`teams_graph_access_token_env`, default `GRAPH_TEAMS_ACCESS_TOKEN`) | Referenced indirectly in effective provider config under `teams_graph_access_token_env` | `src/agent_sandbox/runtime.py`, `src/agent_sandbox/services.py`. |

For issue setup, `AGENT_SANDBOX_GIT_*`, `AGENT_DEFAULT_MODEL`, and `AGENT_POST_EXEC_VERIFICATION_PROMPT` are now
read from the root `issue_setup` directory config when present, with host environment variables
still acting as overrides. `AGENT_SANDBOX_AZURE_DEVOPS_PR_PAT_ENV` remains a provider-level setting.
GitHub Copilot CLI host-mediated auth also uses the effective provider `github_token_env` on the
host when the guest `gh` shim calls `/copilot-auth/token`.

Session model resolution behavior:

- `session.model` is always first priority.
- If `session.model` is empty, the effective directory value for `issue_setup.default_model` is used (including inherited parent overlays).
- If neither value is set, host-side start/resume flows fail fast with an explicit model-resolution error rather than silently falling back to an implicit runtime default.

---

If you update configuration behavior, also update:

- `config/*.env.example`
- `docs/operations.md` / `README.md`
- tests validating defaults and docs.

## 5) Script-level environment override parameters

The following environment variables are consumed directly by scripts as runtime overrides (typically via `${VAR:-default}` patterns).

| Script | Environment variables consumed as overrides |
|---|---|
| `scripts/apply_firewall.sh` | `ALLOWED_TCP_PORTS` |
| `scripts/bootstrap_codex_vm.sh` | `SSH_KEY_PATH`, `HOST_CODEX_ROOT`, `HOST_CODEX_SKILLS_DIR`, `HOST_REPO_CODEX_SKILLS_DIR`, `HOST_CODEX_MODELS_CACHE_PATH`, `LLM_BASE_URL`, `VM_SSH_USER`, `GUEST_BOOTSTRAP_WAIT_SECONDS`, `AGENT_MODEL`, `ENABLE_GUEST_HEARTBEAT`, `GUEST_HEARTBEAT_PROMPT`, `OPENAI_API_KEY` |
| `scripts/bootstrap_copilot_vm.sh` | `SSH_KEY_PATH`, `HOST_COPILOT_BIN`, `ENABLE_GUEST_HEARTBEAT`, `GUEST_HEARTBEAT_PROMPT` |
| `scripts/bootstrap_dotnet_vm.sh` | `SSH_KEY_PATH`, `HOST_DOTNET_ROOT`, `HOST_DOTNET_TOOLS_ROOT`, `HOST_NUGET_PACKAGES_ROOT` |
| `scripts/bootstrap_frontend_deps_vm.sh` | `SSH_KEY_PATH`, `HOST_NODE_ROOT` |
| `scripts/bootstrap_git_vm.sh` | `SSH_KEY_PATH` |
| `scripts/bootstrap_mssql_server_vm.sh` | `MSSQL_SA_PASSWORD`, `MSSQL_PID`, `HOST_MSSQL_SERVER_ROOT`, `HOST_MSSQL_SERVICE_FILE`, `SSH_KEY_PATH` |
| `scripts/bootstrap_mssql_tools_vm.sh` | `SSH_KEY_PATH`, `HOST_MSSQL_TOOLS_ROOT`, `HOST_MSSQL_ODBC_ROOT` |
| `scripts/bootstrap_mssql_vm.sh` | `MSSQL_PID`, `MSSQL_SA_PASSWORD`, `SSH_KEY_PATH` |
| `scripts/bootstrap_nuget_vm.sh` | `SSH_KEY_PATH`, `NUGET_RELAY_BASE_URL`, `NUGET_FALLBACK_PACKAGES`, `NUGET_PACKAGES_DIR`, `NUGET_HTTP_CACHE_DIR` |
| `scripts/bootstrap_playwright_vm.sh` | `SSH_KEY_PATH`, `HOST_NODE_ROOT`, `HOST_PLAYWRIGHT_NODE_MODULES`, `HOST_PLAYWRIGHT_BROWSERS` |
| `scripts/bootstrap_python_packages_vm.sh` | `SSH_KEY_PATH`, `HOST_PYTHON_BIN`, `GUEST_PYTHON_BIN`, `GUEST_WHEELHOUSE_DIR` |
| `scripts/check_issue_vm.sh` | `SSH_PRIVATE_KEY_PATH`, `VM_WAIT_IP_TIMEOUT_SECONDS`, `VM_WAIT_SSH_TIMEOUT_SECONDS` |
| `scripts/check_session_vm_busy.sh` | `SSH_PRIVATE_KEY_PATH`, `VM_AGENT_BUSY_CONNECT_TIMEOUT_SECONDS` |
| `scripts/create_base_vm.sh` | `VM_STORAGE_DIR`, `DOWNLOAD_URL` |
| `scripts/destroy_agent_vm.sh` | `VM_STORAGE_DIR` |
| `scripts/inspect_nuget_cache.sh` | `SHARED_NUGET_CACHE_DIR` |
| `scripts/launch_agent_vm.sh` | `VM_STORAGE_DIR`, `GATEWAY_URL`, `SSH_PUBLIC_KEY_FILE`, `SSH_PRIVATE_KEY_PATH`, `LLM_BASE_URL`, `SHARED_NUGET_CACHE_DIR`, `SHARED_NUGET_CACHE_TAG`, `SHARED_NUGET_CACHE_MODE`, `RAM_MB`, `VCPUS`, `DISK_SIZE_GB` |
| `scripts/lib/agent_status.sh` | `VM_AGENT_STATE_MAX_AGE_SECONDS` |
| `scripts/list_running_vms.sh` | `SSH_PRIVATE_KEY_PATH`, `VM_AGENT_LIST_CONNECT_TIMEOUT_SECONDS` |
| `scripts/prewarm_nuget_cache.sh` | `SHARED_NUGET_CACHE_DIR`, `NUGET_CONFIG_FILE`, `NUGET_HTTP_CACHE_DIR`, `NUGET_TEMP_DIR` |
| `scripts/resume_issue_agent.sh` | `SSH_PRIVATE_KEY_PATH`, `AGENT_PROVIDER`, `AGENT_MODEL`, `VM_WAIT_IP_TIMEOUT_SECONDS`, `VM_WAIT_SSH_TIMEOUT_SECONDS`, `RESUME_COMMAND_TIMEOUT_SECONDS` |
| `scripts/setup_vm.sh` | `SSH_PUBLIC_KEY_FILE`, `VM_WAIT_IP_TIMEOUT_SECONDS`, `VM_WAIT_SSH_TIMEOUT_SECONDS`, `SSH_PRIVATE_KEY_PATH`, `AGENT_PROVIDER`, `AGENT_MODEL` |
| `scripts/tail_issue_vm_log.sh` | `SSH_PRIVATE_KEY_PATH` |

### Notes on completeness

- Section 1 captures all parameters from `config/*.env.example`.
- Section 2 captures all `GatewayConfig`/`MailboxConfig` YAML keys.
- Section 3 captures all cloud-init template placeholders currently substituted by VM launch.
- Section 5 captures script-level env overrides used directly in shell scripts.

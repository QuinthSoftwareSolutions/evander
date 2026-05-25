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

Retell phone-call tools also require the configured `retell_api_key_env` value, which defaults to
`RETELL_API_KEY`, to exist in the host process environment. For systemd deployments, put the actual
secret value in repo-root `secrets.env` or the service manager environment rather than
`config/gateway.env`.

## `config/openai-relay.env.example`

| Parameter | Found in | Consumed by |
|---|---|---|
| `OPENAI_API_KEY` | `config/openai-relay.env.example` | `src/agent_sandbox/relays/openai.py` model relay traffic (looked up using config key `openai_api_key_env`, defaulting to `OPENAI_API_KEY`). |

## `config/nuget-relay.env.example`

| Parameter | Found in | Consumed by |
|---|---|---|
| `AZURE_ARTIFACTS_PAT` | `config/nuget-relay.env.example` | `src/agent_sandbox/relays/nuget.py` (looked up via config key `nuget_azure_pat_env`, defaulting to `AZURE_ARTIFACTS_PAT`). |

## `config/firewall.env.example`

| Parameter | Found in | Consumed by |
|---|---|---|
| `ALLOWED_TCP_PORTS` | `config/firewall.env.example` | `scripts/apply_firewall.sh` (nftables allow-list for guest->host TCP ports; current defaults include `22`, `8000`, `11434`, `11666`, `11777` for the HTTP target relay port, `8081` for Nexus PyPI/npm, and `3142` for apt-cacher-ng). |

## `config/ado-worker.env.example`

| Parameter | Found in | Consumed by |
|---|---|---|
| `ADO_WORK_ITEMS_AZURE_DEVOPS_PAT` | `config/ado-worker.env.example` | Legacy work-item helper commands only. Workflow ADO work-item queries use the normal Azure DevOps provider config/PAT path. |

## `config/ado-worker/config.json`

This JSON file stores the ADO worker's global settings. The admin GUI's Workers tab edits this
file directly for the `ado` worker. The worker now owns Azure DevOps PR feedback/completion
polling only; work-item intake should be modeled with workflow nodes.

| Parameter | Found in | Consumed by |
|---|---|---|
| `runtime_dir` | `config/ado-worker/config.json` | `src/agent_sandbox/daemons/ado_issue_worker.py` and runtime path defaults. |
| `poll_interval_seconds` | `config/ado-worker/config.json` | `src/agent_sandbox/daemons/ado_issue_worker.py`. |
| `directories` | `config/ado-worker/config.json` | `src/agent_sandbox/daemons/ado_issue_worker.py` (canonical directories to inspect for PR feedback/completion streams; empty list means process nothing). |
| `yaml_output_dir` | `config/ado-worker/config.json` | Legacy work-item helper path; workflow `effect:issue:upsert-ado-proxy` defaults to `runtime/ado-work-items`. |
| `processed_store_path` | `config/ado-worker/config.json` | Legacy processed-store cleanup path for `poll-ado-backlog --forget`. |
| `ado_work_items_azure_devops_pat_env` | `config/ado-worker/config.json` | Legacy work-item helper env-var name. The running ADO worker does not require it. |
| `fetch_retry_count` | `config/ado-worker/config.json` | Legacy work-item helper retry setting. |
| `fetch_retry_backoff_seconds` | `config/ado-worker/config.json` | Legacy work-item helper retry setting. |
| `include_comments` | `config/ado-worker/config.json` | Legacy work-item helper comment setting. |
| `comments_max_count` | `config/ado-worker/config.json` | Legacy work-item helper comment setting. |
| `comment_text_max_chars` | `config/ado-worker/config.json` | Legacy work-item helper comment setting. |

`ADO_*` environment variables for worker runtime settings still work as compatibility fallbacks
for one release, but the new persisted source of truth is `config/ado-worker/config.json`.

## `config/trigger-orchestrator/config.json`

This JSON file stores the workflow orchestrator worker's global settings.
Trigger definitions are currently global (host-wide) and are **not** directory-scoped; this keeps
provider secret resolution and cross-directory event ingestion centralized until a directory-resolved
workflow inheritance model is introduced. Triggers, gates, and effects all live in the same graph
config, with explicit edges describing routing between node ids. Domain workflows can now be
provided via `workflow_packs` + optional inline `workflow_ir`, which compose into executable
`triggers`/`gates`/`effects`/`queries`/`edges` at load time.
Provider plugin loading is configured separately under `provider_loader`.
Changes to `provider_loader` are picked up by the long-running trigger and effect
orchestrator workers on config reload; they rebuild provider factories before using
new graph nodes from the updated provider catalog.
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
| `orchestration_store_backend` | `config/trigger-orchestrator/config.json` | Hot orchestration store backend. Defaults to `sqlite`; set to `json` for rollback to legacy files. |
| `orchestration_store_path` | `config/trigger-orchestrator/config.json` | SQLite database path for trigger events, effect executions, gate executions, and workflow events. Defaults to `runtime/orchestration.db`. |
| `orchestration_retention_days` | `config/trigger-orchestrator/config.json` | Completed orchestration history retention window for the SQLite backend. Defaults to `30`. |
| `event_queue_store_path` | `config/trigger-orchestrator/config.json` | Legacy JSON trigger queue path used when `orchestration_store_backend` is `json` and as the migration source. |
| `event_queue_cursor_store_path` | `config/trigger-orchestrator/config.json` | Trigger orchestrator event queue cursor/checkpoint store path. |
| `workflow_event_journal_path` | `config/trigger-orchestrator/config.json` | Legacy JSONL workflow event journal path used when `orchestration_store_backend` is `json` and as the migration source. |
| `workflow_packs` | `config/trigger-orchestrator/config.json` | Ordered workflow-pack filenames under `config/workflow-packs/` to compose into executable IR. |
| `workflow_ir` | `config/trigger-orchestrator/config.json` | Optional inline IR overlay (`version`, `nodes`, `edges`) merged after pack composition. |
| `workflow_graphs` | `config/trigger-orchestrator/config.json` | Optional editor metadata for connected graphs when using inline/non-pack workflows (`id`, `name`, `description`, `directory_paths`, `user_ids`, `node_ids`). `user_ids` are ownership/audit metadata, not a visibility boundary. Pack-backed workflows store the same metadata as `graphs` inside the pack file. |
| `workflow_transforms` | `config/trigger-orchestrator/config.json` | Optional ordered transform plugin names (for example `await_agent_completion`) applied after structural compile/validation. |
| `triggers` | `config/trigger-orchestrator/config.json` | Trigger orchestrator trigger definition list (`src/agent_sandbox/triggers/models.py`). |
| `gate_execution_store_path` | `config/trigger-orchestrator/config.json` | Legacy JSON gate execution log path used when `orchestration_store_backend` is `json` and as the migration source. |
| `gate_cursor_store_path` | `config/trigger-orchestrator/config.json` | Gate orchestrator cursor/checkpoint path. |
| `gate_lock_path` | `config/trigger-orchestrator/config.json` | Gate orchestrator single-instance lock file path. |
| `effect_execution_store_path` | `config/trigger-orchestrator/config.json` | Legacy JSON effect execution log path used when `orchestration_store_backend` is `json` and as the migration source. |
| `effect_cursor_store_path` | `config/trigger-orchestrator/config.json` | Effect orchestrator cursor/checkpoint path. |
| `effects` | `config/trigger-orchestrator/config.json` | Effect definition list (`src/agent_sandbox/events/models.py`). Effects no longer take `upstream_id`; they are wired with edges. |
| `gates` | `config/trigger-orchestrator/config.json` | Gate definition list (filter/map/merge nodes). |
| `edges` | `config/trigger-orchestrator/config.json` | Explicit graph wiring between triggers, gates, and effects. |
| `max_graph_depth` | `config/trigger-orchestrator/config.json` | Maximum allowed downstream graph depth during workflow execution safety checks. |
| `max_events_per_cycle` | `config/trigger-orchestrator/config.json` | Maximum workflow events processed per effect-orchestrator cycle. |
| `provider_loader.provider_profile` | `config/trigger-orchestrator/config.json` | Selects a named provider loading profile (for example `profiles/core-minimal` or compatibility `profiles/agent-default`). |
| `provider_loader.include_builtin` | `config/trigger-orchestrator/config.json` | Optional override for implicit built-in provider loading; defaults from selected profile. |
| `provider_loader.provider_modules` | `config/trigger-orchestrator/config.json` | Additional provider plugin Python modules to load after selected profile modules. |
| `provider_loader.provider_entry_points_enabled` | `config/trigger-orchestrator/config.json` | Enables provider plugin discovery via Python entry points. |

Wake sockets are internal runtime artifacts, not operator-facing config fields. The trigger worker
listens on the path derived from `lock_path` with `.wake.sock`, and the effect worker listens on the
path derived from `effect_lock_path` with `.wake.sock`.

### Trigger definition schema keys

Each trigger entry requires the shared envelope keys:

- `id` (string, required, unique across all triggers)
- `display_name` (string, required)
- `type` (required discriminator)
- `enabled` (boolean, optional; only `false` disables the trigger)

Disabled triggers remain in the graph for editing, but trigger polling and the legacy ADO/Git
direct publishers skip them. Events already queued before a trigger is disabled are still processed
by the effect worker.

Type-specific keys:

| `type` | Required keys | Optional keys |
|---|---|---|
| `trigger:event` | `event_type` | `correlation_key` |
| `trigger:http:webhook` | `slug`, `secret_env` | `event_type` |
| `trigger:cron` | `expression` | — |
| `trigger:ticket:created` | `directories` | — |
| `trigger:ticket:changed` | `directories` | — |
| `trigger:issue:comment-created` | — | `provider`, `organization_url`, `project`, `directories` |
| `trigger:ado:work-item-matching` | — | `directory_path`, `project`, `wiql`, `current_iteration`, `team`, `iteration`, `area_path`, `states`, `types`, `tags_any`, `text_search`, `top` |
| `trigger:script-run-request` | — | — |
| `trigger:discord:new-message` | `discord_bot_token_env` | `channel_ids`, `user_ids` |
| `trigger:teams:new-message` | `targets` | `user_ids` |
| `trigger:teams:direct-message` | `sender_user_ids` | `message_filter` |
| `trigger:email:new-message` | `provider`, `account_identifier`, `mailbox_ids` | `auth_secret_refs` |
| `trigger:pull-request:new-feedback` | `provider` | `organization_url`, `project` (but `organization_url` becomes required when `provider=ado`) |
| `trigger:pull-request:completed` | `provider` | `organization_url`, `project` (but `organization_url` becomes required when `provider=ado`) |
| `trigger:review:new-request` | — | — |
| `trigger:git:push` | `path_to_repo` | — |

`trigger:cron.expression` uses a standard 5-field cron expression. Field syntax supports
ranges, stepped ranges, lists, and month/weekday names. When a cron trigger is overdue, the
trigger orchestrator queues only the latest due occurrence. The provider stages that occurrence
as pending and advances `last_fired_at` only after the trigger event is durably published and
acknowledged, so a queue publish failure can retry the same scheduled occurrence. Editing a
trigger to a higher frequency still does not replay historical runs.

`trigger:pull-request:completed` emits `session_id`, `status`, `merged`, `completed_at`, and a
normalized `pr` object when the tracked PR reaches a terminal state such as merged, completed,
closed, or abandoned. Wire `session_id` into `effect:agent:destroy.session_id` when a workflow
should delete the agent after the tracked PR is terminal.

`trigger:ado:work-item-matching` uses the same filter/query surface as `query:ado:work-items`
without writing local issue state. It emits `work_item` on first match and again when the remote
changed date advances. Use `effect:issue:upsert-ado-proxy` downstream when the workflow should
materialize or refresh a local proxy issue.

`trigger:event` consumes durable internal workflow events. The Admin Web HTTP ingress publishes
caller-supplied event names under the reserved `external.` prefix, so a POST with
`event_type=deploy_requested` should be matched with `event_type=external.deploy_requested`.
The HTTP ingress requires either a bearer token minted by a signed-in Admin Web user or a
configured machine token from `AGENT_SANDBOX_WORKFLOW_EVENT_MACHINE_TOKENS`. Machine tokens are
configured as comma-separated `source-name:token` entries; the matching source name is recorded on
the published event. Token values must not contain commas or colons. The ingress accepts arbitrary
JSON-object payloads for workflow-specific data.

`trigger:http:webhook` exposes a public `POST /api/workflow-webhooks/{slug}` ingress for a single
active webhook trigger. The configured `slug` must match `[A-Za-z0-9_.-]+`, be unique across the
active composed config, and is used as the public path segment. `secret_env` names the host
environment variable that contains the HMAC secret. `event_type` is optional and defaults to
`webhook.<slug>`. Requests must include
`X-Agent-Sandbox-Signature-256: sha256=<hex>` and the signature must be computed over the exact raw
request body bytes using HMAC-SHA256. The webhook ingress accepts JSON object bodies only. The
durable event stored for the trigger is `external.<event_type>`, and downstream trigger output is
the webhook envelope with `slug`, `headers`, `query`, `body`, `received_at`, `remote_addr`, and
`signature_header`.

Use `gate:filter`, `gate:map`, `gate:deserialize`, and `gate:dedupe` downstream when you need to
reshape or suppress webhook deliveries after the trigger fires.

The current trigger set also includes `trigger:teams:new-message`,
`trigger:teams:direct-message`, `trigger:review:new-request`, and `trigger:script-run-request`.
Teams is fed by a host-side ingest worker that reads the same delegated Teams auth settings
described above. Review requests are fed from the host-owned
`runtime/review-request-store.json` inbox populated by `request_pr_review(...)`. Script run
requests are fed from `runtime/script-run-requests-store.json`, which the `run_script(...)` MCP
tool appends to after validating that the calling session is allowed to invoke the requested
script name. The checked-in global workflow currently filters that trigger down to
`provision_exampleindustry_app_demo_environment.sh`, which wraps the existing host-side Example
Industry APP Azure provisioning script and resumes the originating session with the JSON result.
These flows are independent from the wake workers. Discord inbox waking and scheduled resumes are
covered by dedicated wake workers, while Azure DevOps PR feedback and completion pickup live in the
ADO worker.

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
    },
    {
      "id": "deploy-webhook",
      "display_name": "Deploy webhook",
      "type": "trigger:http:webhook",
      "slug": "deploy",
      "secret_env": "WEBHOOK_SECRET"
    }
  ]
}
```

### Gate definition schema keys

Each gate entry requires the shared envelope keys:

- `id` (string, required, unique across all gates)
- `display_name` (string, required)
- `type` (required discriminator)
- `gate_type` (string, required; one of `filter`, `map`, `merge`, `debounce`, `dedupe`)

Type-specific keys:

| `type` | Required keys | Optional keys |
|---|---|---|
| `gate:filter` | `predicate` | `outputs` |
| `gate:map` | `mapping` | `outputs` |
| `gate:deserialize` | `content` | `format`, `outputs` |
| `gate:serialize` | `value` | `format`, `outputs` |
| `gate:merge` | _none beyond envelope_ | `outputs` |
| `gate:debounce` | `key`, `quiet_period`, `max_delay` | `outputs` |
| `gate:dedupe` | `key`, `ttl` | `outputs` |

`gate:dedupe` resolves `key` with the same `${{node.output}}` binding syntax used by other
nodes, then atomically records the key for the ISO-8601 `ttl` window (for example `P7D`
or `PT1H`). The first activation for a key is forwarded; duplicate activations inside the
TTL are recorded as dropped and do not publish downstream completion events. Expired keys are
cleaned up opportunistically during later dedupe checks.

`gate:deserialize` resolves `content` with workflow bindings, parses it as JSON or YAML
(`format` defaults to `auto`), and emits the decoded object's top-level keys as downstream
outputs. Literal object content is used to infer bindable outputs automatically; for dynamic
content, declare `outputs` so downstream references such as `${{gate-deserialize-1.title}}`
validate in the editor.

`gate:serialize` resolves `value` with workflow bindings, serializes it as JSON or YAML
(`format` defaults to `json`), and emits `content` plus `format` for downstream nodes.

`gate:merge` uses Reactive Extensions `merge` semantics: every inbound event is forwarded as it
arrives. When `outputs` is declared, the gate projects each inbound payload to that shared key set.
The runtime also records the latest inbound payload seen from each upstream source in the gate
execution log for debugging.

`gate:debounce` groups inbound activations by its bindable `key`, waits until no newer
activation for that key has arrived for the ISO-8601 `quiet_period`, and caps each burst by
ISO-8601 `max_delay`. It forwards the latest payload in the burst and records debounce
diagnostics including key, first/last seen times, scheduled release time, and release reason.

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
| `effect:agent:create` | _none beyond envelope_ | `provider`, `execution_backend`, `vm_name`, `session_id`, `existing_agent_behavior`, `labels`, `model`, `vm_user`, `ttl_hours`, `allowed_tools`, `allowed_script_names`, `discord_channels`, `teams_channel_allowlist`, `teams_chat_allowlist`, `teams_user_allowlist`, `email_mailboxes`, `email_folders`, `communication_file_receive_enabled`, `communication_file_send_enabled`, `communication_file_require_approval_for_send`, `communication_file_max_bytes`, `dotnet`, `nuget`, `mssql`, `mssql_init_root`, `playwright`, `codex_skills`, `directory_path`, `outputs` |
| `effect:agent:prompt` | `prompt` | `target`, `session_id`, `vm_name`, `vm_user`, `resume_thread_id`, `resume_thread_metadata_key`, `resume_thread_metadata_collection`, `mcp_tool_approval_lease_id`, `outputs` |
| `effect:agent:enqueue-prompt` | `prompt` | `target`, `session_id`, `vm_name`, `model`, `vm_user`, `resume_thread_id`, `resume_thread_metadata_key`, `resume_thread_metadata_collection`, `mcp_tool_approval_lease_id`, `queue_key`, `outputs` |
| `effect:agent:exec` | `command` | `target`, `session_id`, `vm_name`, `vm_user`, `working_directory`, `environment`, `stdin`, `timeout_seconds`, `check`, `outputs` |
| `effect:artifact:stage` | one of `target`, `session_id`, `vm_name`; `path` | `destination_path`, `vm_user`, `outputs` |
| `effect:artifact:publish-artifacts` | one of `target`, `session_id`, `vm_name`; `key` | `vm_user`, `outputs` |
| `effect:semantic:ensure-collection` | `collection_name` | `directory_path`, `outputs` |
| `effect:semantic:upsert` | `collection_name`, `point_id`, `text` | `payload`, `directory_path`, `wait`, `outputs` |
| `effect:semantic:delete` | `collection_name`, `point_ids` | `directory_path`, `wait`, `outputs` |
| `effect:agent:destroy` | _none beyond envelope_ | `target`, `session_id`, `vm_name`, `execution_backend`, `directory_path`, `outputs` |
| `effect:bash:script` | `script_path` | `args`, `working_directory`, `environment`, `stdin`, `timeout_seconds`, `check`, `outputs` |
| `effect:http:request` | `url` | `method`, `allowed_url_patterns`, `payload`, `headers`, `timeout_seconds`, `check`, `outputs` |
| `effect:vm:exec` | `command` | `target`, `session_id`, `vm_name`, `vm_user`, `working_directory`, `environment`, `stdin`, `timeout_seconds`, `check`, `outputs` |
| `effect:vm:copy-files` | `files` | `target`, `session_id`, `vm_name`, `vm_user`, `outputs` |
| `effect:issue:update-state` | `issue_id`, `state` | `sync_proxy_cache`, `outputs` |
| `effect:issue:upsert-ado-proxy` | one of `item`, `items` | `directory_path`, `organization_url`, `project`, `repo_source`, `yaml_output_dir`, `outputs` |
| `effect:pull-request:update` | `provider`, `repo`, `pr_id` | `directory_path`, `organization_url`, `project`, `title`, `description`, `status`, `add_tags`, `remove_tags`, `expected_statuses`, `outputs` |
| `effect:communication:send-message` | `communication_target_id`, `message` | `thread_id`, `outputs` |
| `effect:notification:send` | `communication_target_id`, `message` | `severity`, `subject`, `thread_id`, `outputs` |
| `effect:session:approve-mcp-tools-for-run` | one of `target`, `session_id`, `vm_name`; `tools` | `expires_in_seconds`, `outputs` |
| `effect:session:renew-if-expired` | one of `target`, `session_id`, `vm_name` | `extend_by`, `directory_path`, `outputs` |
| `effect:pr:create` | `host_repo_path` | `local_branch`, `origin_branch`, `canonical_origin_url`, `provider`, `base_branch`, `session_id`, `work_item_id`, `title`, `body`, `body_file`, `body_artifact_ids`, `draft`, `fill`, `outputs` |

`effect:http:request` performs a host-side raw HTTP or HTTPS request from the
effect orchestrator. It supports `GET`, `POST`, and `PUT`; sends `payload` as a
JSON request body when present; treats non-2xx responses as normal workflow
outputs by default; and raises on non-2xx only when `check` is true. When
`check` raises, response outputs are still recorded on the failed run.
`allowed_url_patterns` defaults to `["*"]` and is checked before the host request
is opened. Patterns are shell-style globs matched against either the full URL or
origin when they include a scheme, for example `https://api.example.test/*`, and
against the host or `host:port` when they do not, for example `*.example.test`.
This effect does not use the guest HTTP target relay or its destination
allowlist.

`effect:pr:create` preflights the destination branch before pushing. If the
destination branch exists and its tip is not contained in the local branch, the
effect returns `success: false` with `reason: destination_branch_ahead` and does
not push. Use a downstream `gate:filter` on `success == false` when an agent
should be prompted to fetch and merge the destination branch.

`effect:agent:prompt` and `effect:agent:enqueue-prompt` have two named outgoing
ports: `started` emits after the prompt is dispatched, and `completed` emits
after the agent run completes.

When `resume_thread_metadata_key` is set on either prompt effect, the effect
resumes from and persists the Codex thread ID under a session metadata object.
The object name defaults to `agent_thread_ids` and can be overridden per effect
with `resume_thread_metadata_collection`. For example, key
`teams:channel:thread:agent_thread_id` writes
`metadata.agent_thread_ids["teams:channel:thread:agent_thread_id"]`. To clear all
keyed resume contexts for the default collection, update session metadata with
`{"agent_thread_ids": null}`.

`effect:issue:upsert-ado-proxy` accepts either one raw Azure DevOps work item
(`item`) or an array/batch (`items`). It writes or refreshes the local proxy
ticket in `runtime/local-issues.json`, writes YAML under
`runtime/ado-work-items/work-item-*.yaml` by default, and returns `ticket` for a
single item or `tickets` plus created/updated/skipped counts for batches.

`effect:session:renew-if-expired` resolves a host-side session by `target`,
`session_id`, or `vm_name`. If the session has already expired, it sets
`expires_at` to now plus `extend_by` (for example `8h`, `2d`, or `PT8H`);
active sessions are left unchanged.

`effect:session:approve-mcp-tools-for-run` writes a workflow-run-scoped MCP
tool approval lease for the selected session. The approval is not persisted in
the session manifest, and `effect:agent:prompt` / `effect:agent:enqueue-prompt`
carry the lease into the agent invocation through `mcp_tool_approval_lease_id`.
Direct upstream approval outputs are detected automatically.

`effect:artifact:stage` copies a file or directory that resolves under
`/home/agent` into the session `/artifacts` folder. `destination_path`, when
set, is relative to `/artifacts` and cannot contain dot or parent segments.
`effect:artifact:publish-artifacts` compresses the entire `/artifacts` folder
as a `tar.gz` archive and stores it in the host durable artifact store under an
immutable caller-provided `key`. Reusing a key fails.

### Query definition schema keys

Each query entry uses the same `id`, `display_name`, `type`, and optional
`outputs` envelope as effects.

| `type` | Required keys | Optional keys |
|---|---|---|
| `query:agent:artifact-text` | `session_id`, `path` | `vm_user`, `outputs` |
| `query:ado:work-items` | _none beyond envelope_ | `directory_path`, `project`, `wiql`, `current_iteration`, `team`, `iteration`, `area_path`, `states`, `types`, `tags_any`, `text_search`, `top`, `skip`, `outputs` |
| `query:git:pr-note` | `host_repo_path`, `local_branch` | `remote_name`, `notes_ref`, `outputs` |
| `query:issue:get` | `issue_id` | `outputs` |
| `query:pull-request:feedback` | `provider`, `repo`, `pr_id` | `directory_path`, `organization_url`, `project`, `cursor`, `outputs` |
| `query:pull-request:list` | `provider`, `repo` | `directory_path`, `organization_url`, `project`, `status`, `creator_id`, `reviewer_id`, `source_ref_name`, `target_ref_name`, `source_repository_id`, `repository_id`, `min_time`, `max_time`, `query_time_range_type`, `include_links`, `max_comment_length`, `tags`, `top`, `skip`, `cursor`, `outputs` |
| `query:semantic:store-status` | _none beyond envelope_ | `collection_name`, `directory_path`, `outputs` |
| `query:semantic:search` | `collection_name`, `query_text` | `filter`, `limit`, `score_threshold`, `directory_path`, `outputs` |
| `query:semantic:get` | `collection_name`, `point_ids` | `directory_path`, `outputs` |
| `query:session-context` | one of `target`, `session_id`, `vm_name` | `outputs` |
| `query:session:list` | _none beyond envelope_ | `directory_path`, `metadata_equals`, `metadata_exists`, `metadata_prefix`, `state`, `active_only`, `limit`, `outputs` |
| `query:vm:run-state` | one of `session_id`, `vm_name` | `outputs` |

`query:agent:artifact-text` reads a UTF-8 text file from the resolved session's
artifacts folder. The `path` value must be relative and stay inside that folder.

`query:session:list` reads host-side session manifests and filters by generic
session metadata. `metadata_equals` and `metadata_prefix` are JSON objects keyed
by metadata key or dotted metadata path. `metadata_exists` may be a string, array,
or object of key -> boolean. `directory_path` scopes to that directory and its
descendants. `active_only=true` or `state=active` excludes expired sessions.

`query:pull-request:list`, `query:pull-request:feedback`, and
`effect:pull-request:update` accept Azure DevOps `provider` as `azure_devops`,
`azure-devops`, or `ado`. `query:pull-request:feedback` fetches normalized
feedback for one provider/repo/PR stream and returns `feedback` plus
`next_cursor`; workflows own where that cursor is stored. For Azure DevOps,
`project` can be explicit, read from directory provider config, or derived from
a composite repo slug such as `organization/project/repo`.

Azure DevOps PR tag filters are applied client-side after the PR list call. The
`tags` filter accepts tag names with case-insensitive `and`, `or`, `not`, and
parentheses, for example `(TagA or TagB) and not TagC`. Quote tag names that
contain spaces or collide with an operator. Cron polling workflows should bound
the candidate set with `status`, branch/time filters, `top`, `skip`, or
`cursor`.

`effect:communication:send-message` sends a plain message through a declared
directory-scoped communication target managed from Admin Web Configuration ->
Communications and stored in `runtime/communication-targets.db`. The target must
include the `write` capability for a top-level message, or the `reply` capability
when `thread_id` is set. Teams targets can address
`team-id/channel-id`, `chat:<chat-id>`, or a declared one-on-one user as
`user:<user-id-or-upn>`. Discord targets post to the configured channel.

`effect:notification:send` delivers through a declared communication target in
the same directory-scoped SQLite store. The target must include the `notify`
capability. Teams and Discord targets post a status message; email targets create
a draft using `policy.auth_profile_ref` as the mailbox selector. Teams targets
can address `team-id/channel-id`, `chat:<chat-id>`, or a declared one-on-one user
as `user:<user-id-or-upn>`.

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
| `RETELL_API_KEY` | host shell or `secrets.env` | `src/agent_sandbox/retell_phone.py` / `src/agent_sandbox/gateway.py` when Retell phone-call tools use the default `retell_api_key_env`. |
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
| `enable_directory_scoped_config` | legacy gateway YAML | `src/agent_sandbox/admin/directory.py`, Admin Web directory edit surfaces, `src/agent_sandbox/daemons/ado_issue_worker.py`, and `src/agent_sandbox/daemons/pr_feedback_worker.py` (transition flag for directory edit surfaces and worker shadow resolution). |
| `approval_ttl_seconds` | legacy gateway YAML | `src/agent_sandbox/policy.py` approval expiry computation. |
| `default_session_ttl_seconds` | legacy gateway YAML | `src/agent_sandbox/cli.py` session creation defaults. |
| `bind_network_cidr` | legacy gateway YAML | `src/agent_sandbox/config.py` validation/normalization (policy-bound network constraints). |
| `gateway_url` | legacy gateway YAML | `src/agent_sandbox/cli.py` health checks / external endpoint references. |
| `execution_backend`, `container_worker_base_url`, `container_worker_token_env`, `container_worker_default_image`, `container_worker_job_timeout_seconds`, `container_worker_request_timeout_seconds`, `container_worker_workspace_persistence` | root `providers.json` | `src/agent_sandbox/execution/container_worker_backend.py` and portable `effect:agent:*` container worker routing. |
| `container_worker_guest_openai_base_url`, `container_worker_guest_gateway_url`, `container_worker_guest_http_target_relay_base_url`, `container_worker_guest_nuget_relay_base_url`, `container_worker_guest_pypi_index_url`, `container_worker_guest_npm_registry_url` | root `providers.json` | Container session bootstrap files under `/home/agent` for Codex, MCP gateway, HTTP target relay, NuGet restore traffic, and Nexus-backed PyPI/npm restores. |
| `container_worker_mssql_enabled`, `container_worker_mssql_host`, `container_worker_mssql_port` | root `providers.json` | Gateway-side enablement and advertised worker SQL Server endpoint for `mssql_*` MCP provisioning tools. |
| `retell_api_base_url`, `retell_api_key_env`, `retell_from_number`, `retell_default_agent_id`, `retell_request_timeout_seconds` | root or directory `providers.json` | `src/agent_sandbox/retell_phone.py` and `src/agent_sandbox/gateway.py` for host-side Retell phone calls and webhook verification. |
| `contacts`, `user_contact_links` | root or directory `contacts.json` | `src/agent_sandbox/directory_config.py`, `src/agent_sandbox/retell_phone.py`, and the admin Users API for directory-scoped contacts and user attachments. |
| `redacted_keys` | legacy gateway YAML (optional) | `src/agent_sandbox/gateway.py` request/response redaction in logs. |

### OpenAI relay keys

| Key | Found in | Consumed by |
|---|---|---|
| `openai_relay_host`, `openai_relay_port` | legacy gateway YAML | `src/agent_sandbox/relays/openai.py` listener bind. |
| `openai_api_base_url` | legacy gateway YAML | `src/agent_sandbox/relays/openai.py` upstream URL base. |
| `openai_api_key_env` | legacy gateway YAML | `src/agent_sandbox/relays/openai.py` env-var name lookup for API key. |
| `openai_relay_timeout_seconds` | legacy gateway YAML | `src/agent_sandbox/relays/openai.py` upstream request timeout. |

### Semantic store keys

| Key | Found in | Consumed by |
|---|---|---|
| `semantic_store_provider` | root or directory `providers.json` | Semantic collection/data effects and queries; currently only `qdrant` is supported. |
| `qdrant_base_url` | root or directory `providers.json` | Host-side Qdrant HTTP client for semantic store management; default `http://127.0.0.1:6333`. |
| `qdrant_api_key_env` | root or directory `providers.json` | Optional host env-var name lookup for a Qdrant API key. The key value itself remains host-side. |
| `semantic_embedding_provider` | root or directory `providers.json` | Embedding backend used by semantic upsert/search; currently only `openai` is supported. |
| `semantic_embedding_model` | root or directory `providers.json` | OpenAI embedding model, default `text-embedding-3-small`. |
| `semantic_embedding_dimensions` | root or directory `providers.json` | Vector size used when ensuring Qdrant collections and when requesting embeddings; default `1536`. |
| `semantic_vector_distance` | root or directory `providers.json` | Qdrant vector distance for managed collections; default `Cosine`. |

`effect:semantic:ensure-collection` creates a missing Qdrant collection and treats an existing
matching collection as success. `effect:semantic:upsert` embeds one text string, stores it as one
unnamed-vector Qdrant point using the caller-supplied point ID, and adds reserved payload key
`_semantic_text`. Point IDs must be unsigned 64-bit integers or UUID strings. Caller payloads may not
set that reserved key. `query:semantic:search` embeds `query_text` and returns payload-bearing
points without vectors. `query:semantic:get` and `effect:semantic:delete` operate only on explicit
point IDs; filter-based delete is intentionally not supported. If an existing collection uses
different dimensions, distance, or named vectors, schema-sensitive operations fail and do not delete
or recreate anything.

### Retell phone-call keys

| Key | Found in | Consumed by |
|---|---|---|
| `retell_api_base_url` | provider config overlays | Optional override for Retell create-call, get-call, and webhook signature configuration; defaults to `https://api.retellai.com` and normally does not need to be set. |
| `retell_api_key_env` | provider config overlays | Host env-var name lookup for the Retell API key; default `RETELL_API_KEY`. |
| `retell_from_number` | provider config overlays | Outbound Retell caller number used only by the host-side create-call request. |
| `retell_default_agent_id` | provider config overlays | Retell `override_agent_id` used for directory-contact phone calls. |
| `retell_request_timeout_seconds` | provider config overlays | HTTP timeout in seconds for Retell create-call and get-call requests. |

### Directory contact keys

| Key | Found in | Consumed by |
|---|---|---|
| `contacts` | contact config overlays | Directory-scoped contact map keyed by stable contact id. Entries include `name`, `description`, optional `email_addresses`, and `phone_numbers`; `list_contacts` exposes only safe summaries, and Retell requires exactly one primary phone number on callable contacts. |
| `user_contact_links` | contact config overlays | Directory-scoped mapping from normalized admin user login to one contact id; a `null` value clears an inherited attachment for that directory. |

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
| `azure_devops_pr_pat_env` | legacy gateway YAML | `src/agent_sandbox/runtime.py`, `src/agent_sandbox/daemons/ado_issue_worker.py`, `src/agent_sandbox/reviews/request_service.py`, and PR feedback/completion trigger providers. |
| `azure_devops_organization_url` | legacy gateway YAML | `src/agent_sandbox/runtime.py`, `src/agent_sandbox/daemons/ado_issue_worker.py`, `src/agent_sandbox/issues/service.py`, and PR feedback trigger providers. |
| `azure_devops_project` | legacy gateway YAML | `src/agent_sandbox/runtime.py`, `src/agent_sandbox/daemons/ado_issue_worker.py`, `src/agent_sandbox/issues/service.py`, and PR feedback trigger providers. |
| `pr_feedback_resume_prompt` | legacy gateway YAML / directory wake config | Prompt used when resuming for unread PR feedback. |
| `discord_resume_prompt` | legacy gateway YAML | `src/agent_sandbox/messaging/discord_wake.py`. |
| `pr_map_path` | legacy gateway YAML | `src/agent_sandbox/daemons/ado_issue_worker.py`, PR feedback/completion trigger providers, PR status tracking, ownership cleanup, and admin/session pruning. |

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
| `runtime/assistant-settings.json` | Admin assistant and workflow generation settings | Admin Web Chat and Workflow generator |
| `runtime/communication-targets.db` | SQLite-backed communication target overlays keyed by directory and target ID | `src/agent_sandbox/messaging/targets.py`, Admin Web Configuration -> Communications, workflow notification/message effects |

Supported directory config domains:

| Domain file | Purpose | YAML fields allowed |
|---|---|---|
| `policy.json` | Session/policy defaults and operator restrictions | `approval_ttl_seconds`, `approval_notification_target_ids`, `default_session_ttl_seconds`, Discord/email allowlists, `email_send_enabled`, `pr_reply_require_approval`, `redacted_keys`, `tool_argument_policies` |
| `providers.json` | Upstream/provider endpoints and credentials-by-reference | Discord, OpenAI, HTTP target relay, NuGet, GitHub, Azure DevOps, Teams, container worker, Retell |
| `contacts.json` | Directory contact list and admin-user contact attachments | `contacts`, `user_contact_links` |
| `email.json` | Mailbox provider definitions and email provider runtime settings | `email_mailboxes`, `email_provider_timeout_seconds` |
| `wake.json` | Directory-scoped wake prompt behavior | `pr_feedback_resume_prompt`, `discord_resume_prompt` |
| `ado-worker.json` | Azure DevOps PR automation metadata and optional workflow repo metadata | `ado_area_filters`, `ado_team_filters`, `ado_repo_source`, `create_prs_automatically`, `create_prs_as_draft` |
| `issue-setup.json` | VM issue setup/git hand-off defaults | `git_root`, `git_host`, `git_user`, `git_ssh_alias`, optional `default_model`, `post_exec_verification_prompt`, `enable_guest_heartbeat`, `guest_heartbeat_prompt`, optional `issue_setup_azure_devops_pr_pat_env` |
| `pr-template.json` | Directory-scoped markdown boilerplate prepended to automated PR bodies | `pr_template_markdown` |
| `vm.json` | Guest-facing relay/network defaults | `bind_network_cidr`, `gateway_url`, OpenAI relay host/port, NuGet relay host/port, HTTP target relay host/port |
| `communication_targets` (SQLite) | Directory-scoped communication targets inherited by target ID | `targets` entries with `id`, `provider`, `kind`, `address`, `policy.capabilities`, optional `policy.auth_profile_ref`, or `disabled: true` for inherited target removal |

Assistant settings fields:

| Field | Purpose |
|---|---|
| `default_model` | Default model for the Admin assistant and fallback model for workflow generation. |
| `workflow_generation_model` | Optional model used only by the Workflow tab natural-language graph generator. |
| `enabled`, `show_launcher_when_disabled`, `default_profile`, `read_only`, `custom_system_instructions` | Admin assistant launch, capability, and prompt controls. |

Merge behavior in `src/agent_sandbox/directory_config.py`:

- scalars: last write wins
- maps/objects: deep merge
- lists: replace
- nullable fields: explicit `null` clears inherited value when validation permits it

Inheritance path:

- effective config is resolved in ancestor order: `root -> ... -> selected`
- `runtime/directory-config/root/*.json` is the top of the directory-scoped inheritance chain
- provenance metadata records which directory last set each effective value

Resolution behavior:

- An omitted or empty directory path resolves to `root` for backward compatibility.
- A non-empty directory path must exist in `runtime/directories.json`; missing path segments are
  treated as configuration errors rather than aliases for `root`.
- Session-scoped provider, contacts, policy, and VM defaults resolution use the session manifest's
  `directory_path`. If that path is stale, mistyped, or deleted, the session-specific call is
  rejected with the session id and missing directory path in the error. Active-session policy and
  Discord allowlist aggregation skip stale sessions and log the same limited identifiers.

Approval notification policy:

- `approval_notification_target_ids` is a list of host-owned communication target IDs. The targets
  are configured in Admin Web under Configuration -> Communications and stored in
  `runtime/communication-targets.db`; each selected target must include the `notify` capability.
- In Admin Web, this field is presented as a selectable list of known notify-capable communication
  targets with an `Add communication target` action.
- The default is an empty list, which disables approval notifications.
- Lists replace inherited values, so set the complete target list on the directory overlay that
  should own notifications.
- This is not a session manifest setting and does not grant guests Discord, Teams, or email send
  permission. The host sends notifications for newly created approval records, and operators still
  decide through Admin Web, the admin service, or `agent-sandbox-cli approve`.

Approval-required tool policy:

- `approval_required_tools` is a list of MCP tool names that require approval before execution.
  Values can come from inherited directory `policy.json` overlays or from the session manifest.
- This list does not grant access. A tool must also be present in `allowed_tools`, and normal policy
  checks run before approval gating.
- The gateway creates `mcp_tool_invocation` approval requests for gated calls made without
  `approval_request_id`; approved retries must use the exact same payload and the generated request
  id.
- `approval_submit` and `approval_check` are excluded from generic invocation gating so agents can
  request and inspect approvals.

Tool argument policy:

- `tool_argument_policies` is a tool-keyed map of additive argument rules. Keys must match a known
  MCP `ToolName`, such as `create_ado_backlog_item`.
- Values can come from inherited directory `policy.json` overlays or from the session manifest. The
  effective policy appends session rules after inherited directory rules; it does not replace or
  subtract them.
- This list narrows argument shapes only. It does not grant access, so `allowed_tools` must still
  contain the tool name before the gateway will consider the argument rules.
- Each tool entry contains one or more rules with a unique `id`, optional `name`, and a
  `constraints` list. Keep rule and constraint ids unique within the tool entry; duplicate ids are
  rejected during validation.
- Each constraint uses:
  - `id`
  - `path`
  - `operator`
  - optional `value`
  - optional `values`
  - optional `pattern`
  - optional `case_sensitive` (defaults to `true`)
- Supported operators are `required`, `allowlist`, `denylist`, `fixed`, `pattern`, and `enum`.
- Paths use dotted object access with `[]` for list flattening, for example `project`,
  `metadata.labels[].name`, or `tags[]`.
- `required` fails when the path is absent or when all extracted values are null, blank, or empty.
- `allowlist` and `enum` require every present value to appear in `values`.
- `denylist` fails when any present value appears in `values`.
- `fixed` requires the extracted scalar to equal `value`.
- `pattern` requires present string values to match `pattern`; regexes are validated when the model is
  loaded.
- Missing optional paths pass for every operator except `required` and `fixed`.
- Set `case_sensitive: false` for case-insensitive string comparisons and regex matching.

Example `policy.json` overlay:

```json
{
  "tool_argument_policies": {
    "create_ado_backlog_item": [
      {
        "id": "app-ado-work-item-constraints",
        "name": "APP backlog item constraints",
        "constraints": [
          {
            "id": "project-fixed",
            "path": "project",
            "operator": "fixed",
            "value": "Example Industry APP"
          },
          {
            "id": "type-allowed",
            "path": "type",
            "operator": "enum",
            "values": ["Task", "Product Backlog Item"]
          },
          {
            "id": "title-required",
            "path": "title",
            "operator": "required"
          },
          {
            "id": "iteration-required",
            "path": "iteration",
            "operator": "required"
          },
          {
            "id": "tags-denylist",
            "path": "tags[]",
            "operator": "denylist",
            "values": ["auto-dev-forbidden"]
          }
        ]
      }
    ]
  }
}
```

This policy narrows `create_ado_backlog_item` only after the tool is already allowed. A session or
agent still needs `allowed_tools` to include `create_ado_backlog_item` before the gateway will
evaluate the argument constraints.

Teams delegated auth follows the same host-side model. The preferred operator flow is to sign in on
the host with device code flow and keep the credential host-only. Directory-scoped `providers.json`
overlays can bind a subtree to that host-managed Teams identity, while the legacy static env-token
mode remains supported for compatibility through `teams_graph_access_token_env` (default
`GRAPH_TEAMS_ACCESS_TOKEN`).
The same delegated Teams identity/configuration is also used by the host-side Teams ingest worker
that feeds `trigger:teams:new-message` for both channel threads and group chats, plus
`trigger:teams:direct-message` for one-on-one direct messages sent by configured sender user ids.

Session and `effect:agent:create` manifests can use either legacy raw Teams/Discord IDs or
directory-scoped communication target IDs in their communication allowlists. Declared target IDs are
resolved at use time from the session directory, take precedence over raw IDs when a value collides,
and must expose the capability required by the operation: `read` for message reads, inbound
trigger polling, and attachment list/download; `write` for top-level sends; and `reply` for thread
replies and reactions. Raw legacy IDs keep the existing allowlist behavior.

Teams channel targets can still be declared directly with `teams_channel_allowlist`, Teams chat
targets with `teams_chat_allowlist`, and one-on-one user send targets with
`teams_user_allowlist`. Teams `user:` communication targets are send-only; they are not valid for
Teams reads, reactions, attachment downloads, or `trigger:teams:new-message` polling.
Direct-message trigger polling is configured with `sender_user_ids` on
`trigger:teams:direct-message`.

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
| `agent-sandbox-worker-daemon --mssql-enabled` | `AGENT_SANDBOX_WORKER_MSSQL_ADMIN_PASSWORD` |
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

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

Phone-call tools require provider secrets to exist in the host process environment. Retell uses the
configured `retell_api_key_env`, which defaults to `RETELL_API_KEY`. The OpenAI Realtime/DIDWW
backend uses `didww_sip_bridge_token_env`; the DIDWW bridge service uses its own
`OPENAI_REALTIME_API_KEY_ENV` setting for upstream Realtime credentials and, when inbound routing
is enabled, `DIDWW_SIP_BRIDGE_INBOUND_GATEWAY_URL` plus
`DIDWW_SIP_BRIDGE_INBOUND_GATEWAY_TOKEN_ENV` for gateway provisioning and terminal bridge-result
sync. For systemd deployments, put actual secret values in repo-root `secrets.env` or the service
manager environment rather than `config/gateway.env`.

## `config/openai-relay.env.example`

| Parameter | Found in | Consumed by |
|---|---|---|
| `OPENAI_API_KEY` | `config/openai-relay.env.example` | `src/agent_sandbox/relays/openai.py` model relay traffic (looked up using config key `openai_api_key_env`, defaulting to `OPENAI_API_KEY`). |

## `config/didww-sip-bridge.env.example`

| Parameter | Found in | Consumed by |
|---|---|---|
| `DIDWW_SIP_BRIDGE_HOST`, `DIDWW_SIP_BRIDGE_PORT` | `config/didww-sip-bridge.env.example` | `src/agent_sandbox/didww_sip_bridge.py` bridge API listener bind. |
| `DIDWW_SIP_BRIDGE_TOKEN_ENV` | `config/didww-sip-bridge.env.example` | `src/agent_sandbox/didww_sip_bridge.py` env-var name for the bridge API bearer token. |
| `DIDWW_SIP_BRIDGE_ENABLE_INBOUND_PHONE_CALLS` | `config/didww-sip-bridge.env.example` | `src/agent_sandbox/didww_sip_bridge.py` enables the long-lived inbound `StasisStart` listener for fixed DID routes; defaults to `false`. |
| `DIDWW_SIP_BRIDGE_INBOUND_GATEWAY_URL`, `DIDWW_SIP_BRIDGE_INBOUND_GATEWAY_TOKEN_ENV` | `config/didww-sip-bridge.env.example` | `src/agent_sandbox/didww_sip_bridge.py` gateway provisioning endpoint and bearer-token env-var name for accepted inbound calls; the token env default is `VOICE_MEDIA_CONTROLLER_TOKEN`. |
| `OPENAI_REALTIME_*` | `config/didww-sip-bridge.env.example` | `src/agent_sandbox/didww_sip_bridge.py` server-side OpenAI Realtime WebSocket session setup. |
| `ASTERISK_*` | `config/didww-sip-bridge.env.example` | `src/agent_sandbox/didww_sip_bridge.py` Asterisk ARI and PJSIP endpoint control. |
| `DIDWW_SIP_BRIDGE_RTP_*`, `DIDWW_SIP_BRIDGE_MEDIA_FORMAT`, `DIDWW_SIP_BRIDGE_SAMPLE_RATE_HZ`, `DIDWW_SIP_BRIDGE_FRAME_DURATION_MS` | `config/didww-sip-bridge.env.example` | `src/agent_sandbox/didww_sip_bridge.py` Asterisk `ExternalMedia` RTP settings. |

## `config/nuget-relay.env.example`

| Parameter | Found in | Consumed by |
|---|---|---|
| `AZURE_ARTIFACTS_PAT` | `config/nuget-relay.env.example` | `src/agent_sandbox/relays/nuget.py` (looked up via config key `nuget_azure_pat_env`, defaulting to `AZURE_ARTIFACTS_PAT`). |

## `config/npm-audit-facade.env.example`

This host-local env file is optional and may stay empty. The npm audit facade currently reads
its bind and upstream settings from runtime config, so there are no required env parameters in
the checked-in example.

## `config/firewall.env.example`

| Parameter | Found in | Consumed by |
|---|---|---|
| `ALLOWED_TCP_PORTS` | `config/firewall.env.example` | `scripts/apply_firewall.sh` (nftables allow-list for guest->host TCP ports; current defaults include `22`, `8000`, `11434`, `11666`, `11777` for the HTTP target relay port, `11881` for the npm audit facade, `8081` for Nexus PyPI/npm, and `3142` for apt-cacher-ng). |

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
Optional add-on workflow provider contributions are configured under `addon_loader`.
Changes to `provider_loader` or `addon_loader` are picked up by the long-running
trigger and effect orchestrator workers on config reload; they rebuild provider
factories before using new graph nodes from the updated provider catalog.
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
| `orchestration_wal_checkpoint_mode` | `config/trigger-orchestrator/config.json` | SQLite WAL checkpoint mode for bounded orchestration-store maintenance. Defaults to `truncate`; set to `disabled` to skip explicit checkpoints. |
| `orchestration_wal_checkpoint_min_bytes` | `config/trigger-orchestrator/config.json` | Minimum `orchestration.db-wal` size before explicit checkpointing. Defaults to `67108864` bytes. |
| `orchestration_wal_checkpoint_interval_seconds` | `config/trigger-orchestrator/config.json` | Minimum interval between explicit WAL checkpoint attempts. Defaults to `300`. |
| `orchestration_wal_checkpoint_busy_timeout_ms` | `config/trigger-orchestrator/config.json` | Short busy timeout used only for explicit WAL checkpoint attempts. Defaults to `1000`. |
| `event_queue_store_path` | `config/trigger-orchestrator/config.json` | Legacy JSON trigger queue path used when `orchestration_store_backend` is `json` and as the migration source. |
| `event_queue_cursor_store_path` | `config/trigger-orchestrator/config.json` | Trigger orchestrator event queue cursor/checkpoint store path. |
| `workflow_event_journal_path` | `config/trigger-orchestrator/config.json` | Legacy JSONL workflow event journal path used when `orchestration_store_backend` is `json` and as the migration source. |
| `scheduled_internal_event_store_path` | `config/trigger-orchestrator/config.json` | Durable one-shot scheduled internal event store path used by `effect:event:schedule`. Defaults to `runtime/scheduled-internal-events.json`. |
| `workflow_packs` | `config/trigger-orchestrator/config.json` | Ordered workflow-pack filenames under `config/workflow-packs/` to compose into executable IR. |
| `workflow_ir` | `config/trigger-orchestrator/config.json` | Optional inline IR overlay (`version`, `nodes`, `edges`) merged after pack composition. |
| `workflow_graphs` | `config/trigger-orchestrator/config.json` | Optional editor metadata for connected graphs when using inline/non-pack workflows (`id`, `name`, `description`, `directory_path`, `user_ids`, `node_ids`). Legacy one-item `directory_paths` values are accepted and emitted during the compatibility window, but each graph has exactly one canonical directory. `user_ids` are ownership/audit metadata, not a visibility boundary; Admin Web group assignments and direct workflow grants control graph visibility. Pack-backed workflows store the same metadata as `graphs` inside the pack file. |
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
| `addon_loader.modules` | Host config and `config/trigger-orchestrator/config.json` | Explicit add-on Python modules. Admin Web uses host config for add-on routes/navigation; workflow workers use their config to include add-on provider contributions. |
| `addon_loader.enabled_entry_points` | Host config and `config/trigger-orchestrator/config.json` | Enables add-on discovery through Python entry points in `agent_sandbox.addons`. Defaults to `false`. |
| `addon_loader.enabled_addon_ids` | Host config and `config/trigger-orchestrator/config.json` | Optional allowlist of add-on ids to load from configured modules/entry points. |

For the bundled Team Add-on, prefer explicit module loading:
`addon_loader.modules: ["truman_team_addon"]`. Configure this in the host
config to expose Admin Web routes/navigation, and in
`config/trigger-orchestrator/config.json` to make `effect:team:*` and
`query:team:*` workflow providers available to the trigger/effect workers. The
team runtime loop is still opt-in; materialize
`truman_team_addon.workflow_packs.team_runtime_v1()` into an operator-owned
workflow pack and list that file in `workflow_packs` only on deployments that
want autonomous team wakeups.

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
| `trigger:ado:work-item-comment-created` | — | `directory_path`, `project`, `wiql`, `current_iteration`, `team`, `iteration`, `area_path`, `states`, `types`, `tags_any`, `text_search`, `top`, `comment_contains`, `comment_contains_any`, `comment_author_exclude_names`, `comment_author_exclude_ids`, `comment_case_sensitive`, `include_existing` |
| `trigger:script-run-request` | — | — |
| `trigger:discord:new-message` | `discord_bot_token_env` | `channel_ids`, `user_ids` |
| `trigger:communication:new-message` | `communication_target_ids` | `directory_path`, `author_ids`, `content_contains`, `case_sensitive` |
| `trigger:teams:new-message` | `targets` | `user_ids` |
| `trigger:teams:direct-message` | `sender_user_ids` | `message_filter` |
| `trigger:email:new-message` | `provider`, `account_identifier`, `mailbox_ids` | `auth_secret_refs` |
| `trigger:pull-request:new-feedback` | `provider` | `repo`, `directory_path`, `organization_url`, `project`, `status`, `creator_id`, `reviewer_id`, `source_ref_name`, `target_ref_name`, `source_repository_id`, `repository_id`, `min_time`, `max_time`, `query_time_range_type`, `include_links`, `max_comment_length`, `tags`, `top`, `skip`, `cursor`, `agent_author_names`, `agent_mention_names`, `agent_author_ids`, `agent_mention_ids`, `suppress_agent_authored_feedback` |
| `trigger:pull-request:completed` | `provider` | `repo`, `directory_path`, `organization_url`, `project`, `status`, `creator_id`, `reviewer_id`, `source_ref_name`, `target_ref_name`, `source_repository_id`, `repository_id`, `min_time`, `max_time`, `query_time_range_type`, `include_links`, `max_comment_length`, `tags`, `top`, `skip`, `cursor` |
| `trigger:ci:run:completed` | `provider` | `repo` (required when `provider=github_actions`), `workflow`, `organization_url`, `project`, `pipeline_id`, `pipeline_name`, `branches`, `conclusions`, `top`, `include_existing` |
| `trigger:review:new-request` | — | — |
| `trigger:approval:pending-created` | — | `directory_path`, `action_types`, `tool_names` |
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

For Azure DevOps PR triggers, `repo` is the explicit polling mode switch. When `repo` is set,
`agent-sandbox-ado-worker` lists candidate PRs with the same criteria surface as
`query:pull-request:list` and stores trigger-owned cursors under the runtime trigger cursor files.
When `repo` is omitted, the trigger stays in legacy `runtime/pr-map.json` compatibility mode.
Explicit-mode events include normalized `pr` plus feedback/completion payloads and do not attach a
`session_id`; route to sessions with `query:session:list` metadata filters when needed.

`trigger:ci:run:completed` is the polling-only CI completion trigger. It accepts canonical
providers `github_actions` and `ado_pipelines`, plus aliases such as `github`, `github-actions`,
`github_actions`, `ado`, `azure_devops`, `azure-devops`, `ado_pipelines`, and `azure-pipelines`.
GitHub Actions configs require `repo`; Azure DevOps configs may omit `organization_url` and
`project` when directory provider config can resolve them. Blank strings normalize to `null`,
branch selectors are stored as plain branch names, and conclusions normalize to
`success`, `failure`, `cancelled`, `timed_out`, and `skipped` with provider-specific spellings
folded into those canonical values. The default conclusion filter includes all terminal outcomes,
and `include_existing` defaults to `false` so the first poll suppresses historical runs unless an
operator explicitly opts into backfill.

Normalized CI completion events expose the following fields for downstream bindings:
`provider`, `repo`, `run_id`, `run_number`, `attempt`, `workflow_id`, `workflow_name`,
`pipeline_id`, `pipeline_name`, `branch`, `commit_sha`, `status`, `conclusion`, `run_url`,
`started_at`, `completed_at`, `duration_seconds`, `actor`, `pull_requests`, and `metadata`.
Provider-specific fields that do not apply to a given source remain `null`, and the provider keeps
its cursor state in `runtime/trigger-cursors-ci-runs.json`.

`trigger:ado:work-item-matching` uses the same filter/query surface as `query:ado:work-items`
without writing local issue state. It emits `work_item` on first match and again when the remote
changed date advances. Use `effect:issue:upsert-ado-proxy` downstream when the workflow should
materialize or refresh a local proxy issue.

`trigger:ado:work-item-comment-created` uses the same work-item filter/query surface, then reads
matching work-item details with comments. It stores comment cursors in
`runtime/trigger-cursors-ado-work-item-comments.json`, keyed by trigger id and work-item stream.
Existing comments are baselined without emitting by default; set `include_existing=true` only for
explicit backfill workflows. Bot names, mention text, tags, and cleanup states are workflow config,
not built-in defaults.

`trigger:event` consumes durable internal workflow events. The Admin Web HTTP ingress publishes
caller-supplied event names under the reserved `external.` prefix, so a POST with
`event_type=deploy_requested` should be matched with `event_type=external.deploy_requested`.
The HTTP ingress requires either a bearer token minted by a signed-in Admin Web user or a
configured machine token from `AGENT_SANDBOX_WORKFLOW_EVENT_MACHINE_TOKENS`. Machine tokens are
configured as comma-separated `source-name:token` entries; the matching source name is recorded on
the published event. Token values must not contain commas or colons. The ingress accepts arbitrary
JSON-object payloads for workflow-specific data. Scheduled internal events are first written to
`scheduled_internal_event_store_path` and then published into the normal internal event store by
the trigger orchestrator when they become due, so the end-to-end delay is bounded by the trigger
orchestrator poll interval.

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
`trigger:teams:direct-message`, `trigger:review:new-request`,
`trigger:approval:pending-created`, and `trigger:script-run-request`.
Teams is fed by a host-side ingest worker that reads the same delegated Teams auth settings
described above. Review requests are fed from the host-owned
`runtime/review-request-store.json` inbox populated by `request_pr_review(...)`. Script run
requests are fed from `runtime/script-run-requests-store.json`, which the `run_script(...)` MCP
tool appends to after validating that the calling session is allowed to invoke the requested
script name. The checked-in global workflow currently filters that trigger down to
`provision_exampleindustry_app_demo_environment.sh`, queues a typed Azure DevOps pipeline run,
maps the derived demo-environment relay metadata with `gate:map`, and resumes the originating
session after `effect:session:update-metadata` stores that metadata.

`trigger:approval:pending-created` watches the durable approval store for pending requests,
scopes by `directory_path` with descendant matching, and accepts optional `action_types` and
`tool_names` filters. Pending approval instances remain eligible until the queued trigger event
is acknowledged, so a publish retry can replay the same approval without creating a duplicate
source item.

`trigger:communication:new-message` is a provider-neutral chat trigger over declared
directory-scoped communication targets. Its `communication_target_ids` list is required and must
name at least one target from the selected `directory_path` (default `root`); omitted target IDs do
not subscribe to all targets. Version 1 accepts only Teams channel/chat and Discord channel targets
with the `read` capability, rejects Teams `user:` targets, and intentionally excludes email. The
optional `author_ids` list filters by provider-native author/user ID, `content_contains` applies a
substring filter to message content, and `case_sensitive` defaults to `false`. The trigger reads
the existing host-owned Teams and Discord inbox stores and emits `provider`,
`communication_target_id`, `message_id`, `thread_id`, `content`, `author_id`, `author_name`,
`created_at`, `received_at`, `target`, and a nested `message` object with safe provider metadata;
communication target `policy.auth_profile_ref` values are not emitted.

These flows are independent from the wake workers. Discord inbox waking and scheduled resumes are
covered by dedicated wake workers, while Azure DevOps PR feedback and completion pickup live in the
ADO worker.

### Scheduled wake worker keys

The scheduled wake worker reads `config/scheduled-wake/config.json`. The keys below shape the
worker's polling and one-time completion-confirmation retry behavior.

| Key | Found in | Consumed by |
|---|---|---|
| `directories` | `config/scheduled-wake/config.json` | Directory scopes the worker may dispatch for. |
| `poll_interval_seconds` | `config/scheduled-wake/config.json` | Cycle cadence for the scheduled wake worker. |
| `jitter_seconds` | `config/scheduled-wake/config.json` | Random sleep jitter added to each poll cycle. |
| `backoff_seconds` | `config/scheduled-wake/config.json` | Whole-cycle exception backoff for the worker loop. |
| `cooldown_seconds` | `config/scheduled-wake/config.json` | Per-session cooldown after a handled wake dispatch. |
| `completion_confirmation_timeout_seconds` | `config/scheduled-wake/config.json` | How long a one-time dispatched wake stays in the confirmation window before retry logic can requeue it. |
| `completion_confirmation_retry_max_attempts` | `config/scheduled-wake/config.json` | Maximum automatic confirmation retries for a one-time wake batch. |
| `completion_confirmation_retry_window_seconds` | `config/scheduled-wake/config.json` | Maximum age of a confirmation retry batch before it is exhausted. |
| `completion_confirmation_retry_base_backoff_seconds` | `config/scheduled-wake/config.json` | Initial backoff used when a one-time wake is requeued for confirmation retry. |
| `completion_confirmation_retry_max_backoff_seconds` | `config/scheduled-wake/config.json` | Ceiling for the confirmation retry backoff. |
| `late_threshold_seconds` | `config/scheduled-wake/config.json` | Maximum lateness before the worker/API marks a wake `missed_or_late`. |
| `scheduled_wake_prefix_text` | `config/scheduled-wake/config.json` | Optional operator-supplied prompt prefix prepended to scheduled wake resume text; defaults to empty. |

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
- `gate_type` (string, required; one of `filter`, `map`, `merge`, `debounce`, `dedupe`, `switch`)

Type-specific keys:

| `type` | Required keys | Optional keys |
|---|---|---|
| `gate:filter` | `predicate` | `outputs` |
| `gate:switch` | `cases` | `default_port`, `outputs` |
| `gate:for` | `items` | `outputs` |
| `gate:while` | `when`, `max_iterations` | `outputs` |
| `gate:map` | `mapping` | `outputs` |
| `gate:aggregate` | `items`, `metrics` | `outputs` |
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

`gate:switch` evaluates its `cases` in list order and forwards the first case whose `when` or
`match` condition matches the inbound payload. Each case must define a non-blank `port`, optional
`label`, and exactly one of `when` or `match`. `when` uses the same filter-expression object shape
as `gate:filter`, while `match` uses the same recursive JSON-object subset matcher as
`gate:filter.match`. Duplicate case ports are allowed, so several cases can intentionally route
to the same downstream branch. When no case matches, `default_port` receives the activation if it
is present; otherwise the gate records the activation as dropped. The runtime passes through the
upstream payload unchanged apart from runtime-only bookkeeping fields, and branch selection is
published downstream through `source_port`.

`gate:while` evaluates its `when` predicate on each activation and routes to `body` while the
predicate is true and `max_iterations` has not been reached. It routes to `done` when the predicate
is false, and to `exhausted` when the predicate remains true after the iteration cap. Body paths
that return to the while gate must use an `allow_cycle` edge back to the gate.

`gate:aggregate` resolves `items` to an array and emits `count` plus one flat output for each
configured metric. `metrics` is an object keyed by output name; each metric sets `operation` to
`sum`, `min`, `max`, or `avg` and `path` to a dotted field inside each item. Missing or null values
are ignored, non-numeric values fail the gate, `sum` returns `0` when no numeric values are present,
and `min`, `max`, and `avg` return `null` in that case.

`gate:deserialize` resolves `content` with workflow bindings, parses it as JSON or YAML
(`format` defaults to `auto`), and emits the decoded object's top-level keys as downstream
outputs. Literal object content is used to infer bindable outputs automatically; for dynamic
content, declare `outputs` so downstream references such as `${{gate-deserialize-1.title}}`
validate in the editor.

`gate:serialize` resolves `value` with workflow bindings, serializes it as JSON or YAML
(`format` defaults to `json`), and emits `content` plus `format` for downstream nodes.

`gate:merge` uses Reactive Extensions `merge` semantics: every inbound event is forwarded as it
arrives. When `outputs` is declared, the gate projects each inbound payload to that shared key set.
Downstream nodes can bind to the merge outputs and to upstream nodes that are guaranteed to have
executed on every path to the merge. Branch-local outputs should be mapped into a shared shape
before the merge and read from the merge node. The runtime also records the latest inbound payload
seen from each upstream source in the gate execution log for debugging.

`gate:debounce` groups inbound activations by its bindable `key`, waits until no newer
activation for that key has arrived for the ISO-8601 `quiet_period`, and caps each burst by
ISO-8601 `max_delay`. It forwards the latest payload in the burst and records debounce
diagnostics including key, first/last seen times, scheduled release time, and release reason.
Delayed debounce releases preserve the inherited workflow binding context.

Edge wiring records connect upstream node ids to downstream node ids and replace any node-local
`upstream_id` field.

Example edge shape:

```json
{
  "source_id": "ticket-created",
  "target_id": "effect-provision",
  "source_port": "ready"
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
| `effect:agent:create` | _none beyond envelope_ | `provider`, `execution_backend`, `vm_image`, `vm_name`, `session_id`, `existing_agent_behavior`, `labels`, `model`, `vm_user`, `ttl_hours`, `allowed_tools`, `allowed_script_names`, `communication_target_ids`, `discord_channels`, `teams_channel_allowlist`, `teams_chat_allowlist`, `teams_user_allowlist`, `email_mailboxes`, `email_folders`, `communication_file_receive_enabled`, `communication_file_send_enabled`, `communication_file_require_approval_for_send`, `communication_file_max_bytes`, `web_search_enabled`, `dotnet`, `nuget`, `mssql`, `mssql_init_root`, `playwright`, `codex_skills`, `directory_path`, `metadata`, `outputs` |
| `effect:agent:clone-repo` | `repo_source` | `target`, `session_id`, `vm_name`, `clone_branch`, `vm_user`, `directory_path`, `git_author_name`, `git_author_email`, `git_committer_name`, `git_committer_email`, `outputs` |
| `effect:agent:prompt` | `prompt` | `target`, `session_id`, `vm_name`, `vm_user`, `resume_thread_id`, `resume_thread_metadata_key`, `resume_thread_metadata_collection`, `mcp_tool_approval_lease_id`, `outputs` |
| `effect:agent:enqueue-prompt` | `prompt` | `target`, `session_id`, `vm_name`, `model`, `vm_user`, `resume_thread_id`, `resume_thread_metadata_key`, `resume_thread_metadata_collection`, `mcp_tool_approval_lease_id`, `queue_key`, `outputs` |
| `effect:agent:exec` | `command` | `target`, `session_id`, `vm_name`, `vm_user`, `working_directory`, `environment`, `stdin`, `timeout_seconds`, `check`, `outputs` |
| `effect:artifact:stage` | one of `target`, `session_id`, `vm_name`; `path` | `destination_path`, `vm_user`, `outputs` |
| `effect:artifact:publish-artifacts` | one of `target`, `session_id`, `vm_name`; `key` | `vm_user`, `outputs` |
| `effect:artifact:import-published` | one of `target`, `session_id`, `vm_name`; `key` | `destination_path`, `overwrite`, `vm_user`, `outputs` |
| `effect:artifact-space:create` | `path` | `display_name`, `metadata`, `grants`, `subject`, `expected_revision`, `outputs` |
| `effect:artifact-space:publish-version` | `space_path`, `artifact_id`, `artifact_key` | `subject`, `metadata`, `expected_revision`, `require_existing_artifact`, `outputs` |
| `effect:semantic:ensure-collection` | `collection_name` | `directory_path`, `outputs` |
| `effect:semantic:upsert` | `collection_name`, `point_id`, `text` | `payload`, `directory_path`, `wait`, `outputs` |
| `effect:semantic:delete` | `collection_name`, `point_ids` | `directory_path`, `wait`, `outputs` |
| `effect:state:update` | `namespace`, `key`, `operation` | `value`, `patch`, `expected_version`, `outputs` |
| `effect:agent:destroy` | _none beyond envelope_ | `target`, `session_id`, `vm_name`, `execution_backend`, `directory_path`, `outputs` |
| `effect:http:request` | `url` | `method`, `allowed_url_patterns`, `payload`, `headers`, `timeout_seconds`, `check`, `outputs` |
| `effect:runtime:diagnostics-snapshot` | _none beyond envelope_ | `since`, `units`, `include_failed_units`, `include_journal`, `include_runtime_files`, `max_journal_lines_per_unit`, `max_runtime_file_bytes`, `command_timeout_seconds`, `outputs` |
| `effect:vm:exec` | `command` | `target`, `session_id`, `vm_name`, `vm_user`, `working_directory`, `environment`, `stdin`, `timeout_seconds`, `check`, `outputs` |
| `effect:vm:copy-files` | `files` | `target`, `session_id`, `vm_name`, `vm_user`, `outputs` |
| `effect:event:schedule` | `event_type`, `payload`; exactly one of `run_at`, `delay_seconds` | `correlation_key`, `idempotency_key`, `outputs` |
| `effect:issue:update-state` | `issue_id`, `state` | `sync_proxy_cache`, `outputs` |
| `effect:issue:upsert-ado-proxy` | one of `item`, `items` | `directory_path`, `organization_url`, `project`, `repo_source`, `yaml_output_dir`, `outputs` |
| `effect:ado:add-work-item-tag` | `directory_path`, `project`, `work_item_id`, `tag` | `sync_proxy_cache`, `outputs` |
| `effect:ado:remove-work-item-tag` | `directory_path`, `project`, `work_item_id`, `tag` | `sync_proxy_cache`, `outputs` |
| `effect:ado:pipeline-run` | `directory_path`, `pipeline_id` | `project`, `branch`, `variables`, `template_parameters`, `timeout_seconds`, `poll_interval_seconds`, `check`, `outputs` |
| `effect:pull-request:add-tag` | `provider`, `repo`, `pr_id`, `tag` | `directory_path`, `organization_url`, `project`, `outputs` |
| `effect:pull-request:remove-tag` | `provider`, `repo`, `pr_id`, `tag` | `directory_path`, `organization_url`, `project`, `outputs` |
| `effect:pull-request:update` | `provider`, `repo`, `pr_id` | `directory_path`, `organization_url`, `project`, `title`, `description`, `status`, `add_tags`, `remove_tags`, `expected_statuses`, `outputs` |
| `effect:communication:send-message` | `communication_target_id`, `message` | `thread_id`, `outputs` |
| `effect:notification:send` | `communication_target_id`, `message` | `severity`, `subject`, `thread_id`, `outputs` |
| `effect:session:approve-mcp-tools-for-run` | one of `target`, `session_id`, `vm_name`; `tools` | `expires_in_seconds`, `outputs` |
| `effect:session:revoke-mcp-tool-approval-lease` | one of `mcp_tool_approval_lease_id` (preferred), `lease_id`; optional `session_id`, `workflow_run_id` | `outputs` |
| `effect:session:renew-if-expired` | one of `target`, `session_id`, `vm_name` | `extend_by`, `renew_if_expires_within`, `directory_path`, `outputs` |
| `effect:git:push-branch` | `host_repo_path`, `local_branch`, `origin_branch` | `canonical_origin_url`, `provider`, `base_branch`, `session_id`, `work_item_id`, `outputs` |
| `effect:pr:create` | `host_repo_path` | `local_branch`, `origin_branch`, `canonical_origin_url`, `provider`, `base_branch`, `session_id`, `work_item_id`, `title`, `body`, `body_file`, `body_artifact_ids`, `draft`, `fill`, `outputs` |

For `effect:agent:clone-repo`, set both `git_author_name` and
`git_author_email` when a workflow should set a specific Git author. Set both
`git_committer_name` and `git_committer_email` when the Git committer should be
different. Leave the committer pair unset to use the author/default identity for
both roles.

`effect:agent:create` accepts `metadata` as a JSON object patch. The workflow
runtime deep-merges that patch into the created session manifest after built-in
create metadata such as labels, model, execution backend, and worker URL are
assembled.

`effect:agent:create` also accepts `vm_image` as a libvirt-only VM template
selector. The selector resolves against the directory-scoped `vm.json`
`libvirt_vm_templates` catalog, falls back to `libvirt_vm_default_template`
when omitted on a new session, and requires an approved template alias when set
explicitly. On an existing-session upsert, omitting `vm_image` preserves the
session's recorded `metadata.libvirt_vm_template`; provide `vm_image` explicitly
to replace that record. Use `container_image` instead for `container_worker_vm`.

`web_search_enabled` defaults to `false`. When it is false, the OpenAI relay
rejects Responses requests that declare hosted web search tools. Set it to
`true` only for sessions that should be allowed to receive OpenAI-hosted web
content in model context. Codex VM resume and container-worker prompt commands
also set Codex `web_search` to `disabled` or `live` from this session flag.

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

`effect:runtime:diagnostics-snapshot` captures a host-side diagnostic snapshot
without accepting arbitrary commands or paths from workflow config. It reads only
the configured runtime directory, fixed orchestration failure files, and an
allowlisted subset of `agent-sandbox-*` systemd units. Service log collection is
bounded by `since`, `units`, `max_journal_lines_per_unit`, and
`command_timeout_seconds`; runtime file reads are bounded by
`max_runtime_file_bytes`. Redaction is always enabled before the snapshot is
returned in `snapshot_text`, `stdout`, and `message`.

`effect:pr:create` preflights the destination branch before pushing. If the
destination branch exists and its tip is not contained in the local branch, the
effect returns `success: false` with `reason: destination_branch_ahead` and does
not push. Use a downstream `gate:filter` on `success == false` when an agent
should be prompted to fetch and merge the destination branch.

`effect:git:push-branch` performs only the trusted host checkout branch push
portion of that PR automation path. It normalizes ref-prefixed branch names,
rejects refspec-shaped branch values, uses the canonical origin URL when
provided, preflights the destination branch, and returns the same
`destination_branch_ahead` shape without creating or updating a pull request.

`effect:event:schedule` stores a one-shot pending scheduled internal event
instead of publishing immediately. It validates the event type, keeps the
payload as a JSON object, and uses either an absolute `run_at` timestamp or a
relative `delay_seconds` value to compute the due time. Duplicate
`idempotency_key` values reuse the original schedule record unchanged, which
keeps retries from mutating the first submission. The trigger orchestrator
publishes due scheduled events during its normal cycle, so delivery latency is
bounded by its `poll_interval_seconds`.

`effect:agent:prompt` and `effect:agent:enqueue-prompt` have three named
outgoing ports: `started` emits after the prompt is dispatched, `completed`
emits after the agent run completes, and `error` emits only for supported
prompt dispatch failures when the workflow wires an outbound error edge. Use
the `error` branch for notification, retry, or cleanup paths that should see a
redacted failure payload. When no `error` edge exists, the prompt node remains
failed as before, and successful `started` / `completed` paths do not populate
error fields.
For `effect:agent:enqueue-prompt`, routed dispatch failures include the queued
retry context (`queued`, `queue_key`, and `coalesced_count`) when the prompt was
written to the durable retry queue before dispatch failed. The prompt queue also
tracks durable retry attempts by queue key and prompt digest, capped by
`agent_prompt_queue_max_retries` (default `3`). Once the cap is exceeded,
enqueue fails with `failed_phase: retry_limit` and includes `retry_count` and
`max_retries` in the error outputs.

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

`effect:session:renew-if-expired` resolves a host-side session by exactly one
of `target`, `session_id`, or `vm_name`, plus optional `directory_path`. If the
session has already expired, it sets `expires_at` to now plus `extend_by` (for
example `8h`, `2d`, or `PT8H`). If `renew_if_expires_within` is configured and
the active session expires within that pre-expiry window, it also renews to now
plus `extend_by`; otherwise active sessions are left unchanged. For example,
`extend_by: 2d` with `renew_if_expires_within: 4h` renews active sessions that
expire in the next four hours. This node is the explicit renewal mechanism
before `effect:agent:prompt` or `effect:agent:enqueue-prompt`; those nodes do
not renew sessions on their own.

Expired sessions are execution-gated until renewed. Admin resume, workflow
agent prompt effects, queued prompt replay, wake dispatch, and OpenAI relay
model calls fail with a session-expired message that includes `session_id` and
`expires_at`. Host-local Git SSH pull/push access is intentionally not gated so
operators can recover code or push work before renewing the session.

`effect:session:approve-mcp-tools-for-run` writes a workflow-run-scoped MCP
tool approval lease for the selected session. The approval is not persisted in
the session manifest, and `effect:agent:prompt` / `effect:agent:enqueue-prompt`
carry the lease into the agent invocation through `mcp_tool_approval_lease_id`.
Direct upstream approval outputs are detected automatically.

`effect:session:revoke-mcp-tool-approval-lease` revokes a previously created
workflow-run-scoped MCP tool approval lease. Use `mcp_tool_approval_lease_id`
in workflow bindings when possible; `lease_id` is accepted as an alias. The
revoke effect is idempotent, preserves the lease record for audit history via
`revoked_at`, and prevents future tool invocations under that lease without
undoing any work already performed. Optional `session_id` and
`workflow_run_id` guards must match the stored lease when supplied.

`effect:artifact:stage` copies a file or directory that resolves under
`/home/agent` into the session `/artifacts` folder. `destination_path`, when
set, is relative to `/artifacts` and cannot contain dot or parent segments.
`effect:artifact:publish-artifacts` compresses the entire `/artifacts` folder
as a `tar.gz` archive and stores it in the host durable artifact store under an
immutable caller-provided `key`. Reusing a key fails.
`effect:artifact:import-published` reads an immutable published artifact by
`key`, copies its archive into the selected session, and extracts it under that
session's `/artifacts` folder. `destination_path`, when set, is a relative
prefix under `/artifacts`; existing files cause the import to fail unless
`overwrite` is true.
Artifact spaces add a generic hierarchical catalogue over immutable published
artifact keys. Spaces live in `runtime/artifact-spaces.json`, use opaque
subjects such as `session:<id>` or add-on-defined subjects for access checks,
and create new version records instead of overwriting existing published
artifacts.

### Query definition schema keys

Each query entry uses the same `id`, `display_name`, `type`, and optional
`outputs` envelope as effects.

| `type` | Required keys | Optional keys |
|---|---|---|
| `query:agent:artifact-text` | `session_id`, `path` | `vm_user`, `outputs` |
| `query:ado:work-items` | _none beyond envelope_ | `directory_path`, `project`, `wiql`, `current_iteration`, `team`, `iteration`, `area_path`, `states`, `types`, `tags_any`, `text_search`, `top`, `skip`, `outputs` |
| `query:git:pr-note` | `host_repo_path`, `local_branch` | `remote_name`, `notes_ref`, `outputs` |
| `query:issue:get` | `issue_id` | `outputs` |
| `query:issue:list` | _none beyond envelope_ | `directory_path`, `direct_only`, `states`, `exclude_states`, `source`, `source_type`, `tags_any`, `tags_all`, `metadata_equals`, `metadata_exists`, `metadata_prefix`, `limit`, `outputs` |
| `query:pull-request:get` | `provider`, `repo`, `pr_id` | `directory_path`, `organization_url`, `project`, `include_reviewers`, `include_commits`, `outputs` |
| `query:pull-request:feedback` | `provider`, `repo`, `pr_id` | `directory_path`, `organization_url`, `project`, `cursor`, `outputs` |
| `query:communication:messages` | `communication_target_id` | `directory_path`, `limit`, `cursor`, `before_cursor`, `thread_id`, `outputs` |
| `query:pull-request:list` | `provider`, `repo` | `directory_path`, `organization_url`, `project`, `status`, `creator_id`, `reviewer_id`, `source_ref_name`, `target_ref_name`, `source_repository_id`, `repository_id`, `min_time`, `max_time`, `query_time_range_type`, `include_links`, `max_comment_length`, `tags`, `top`, `skip`, `cursor`, `outputs` |
| `query:semantic:store-status` | _none beyond envelope_ | `collection_name`, `directory_path`, `outputs` |
| `query:semantic:search` | `collection_name`, `query_text` | `filter`, `limit`, `score_threshold`, `directory_path`, `outputs` |
| `query:semantic:get` | `collection_name`, `point_ids` | `directory_path`, `outputs` |
| `query:state:get` | `namespace`, `key` | `outputs` |
| `query:artifact-space:list` | _none beyond envelope_ | `path`, `recursive`, `subject`, `include_versions`, `outputs` |
| `query:artifact-space:latest` | `space_path` | `artifact_id`, `subject`, `outputs` |
| `query:session-context` | one of `target`, `session_id`, `vm_name` | `outputs` |
| `query:session:list` | _none beyond envelope_ | `directory_path`, `metadata_equals`, `metadata_exists`, `metadata_prefix`, `state`, `active_only`, `limit`, `outputs` |
| `query:vm:run-state` | one of `session_id`, `vm_name` | `outputs` |

`query:agent:artifact-text` reads a UTF-8 text file from the resolved session's
artifacts folder. The `path` value must be relative and stay inside that folder.

`query:session:list` reads host-side session manifests and filters by generic
session metadata. `metadata_equals` and `metadata_prefix` are JSON objects keyed
by metadata key or dotted metadata path. `metadata_exists` may be a string, array,
or object of key -> boolean. `directory_path` scopes to that directory and its
descendants; when omitted, it defaults to the workflow graph directory context,
falling back to `root`. `active_only=true` or `state=active` excludes expired
sessions.

`query:issue:list` reads tracked local and proxy issues from the local issue
store. It defaults to the workflow runtime directory and `direct_only=true`,
returns `issues` plus `count`, and uses the same directory and metadata filter
semantics as `query:session:list`. Use `query:ado:work-items` for remote Azure
DevOps discovery.

`query:pull-request:get`, `query:pull-request:list`, `query:pull-request:feedback`,
`effect:pull-request:update`, `effect:pull-request:add-tag`, and
`effect:pull-request:remove-tag` accept Azure DevOps `provider` as
`azure_devops`, `azure-devops`, or `ado`. `query:pull-request:get` also accepts
GitHub `provider` as `github`. It returns normalized pull request metadata and
does not store cursor or workflow state. For Azure DevOps, `project` can be
explicit, read from directory provider config, or derived from a composite repo
slug such as `organization/project/repo`. For `query:pull-request:get`,
Azure DevOps uses the directory-scoped `azure_devops_organization_url`,
`azure_devops_project`, and `azure_devops_pr_pat_env` unless `organization_url`
or `project` is supplied on the node. GitHub uses directory-scoped
`github_token_env` when configured and can fetch public data without a token.
Set `include_commits=true` to fetch commit details and `include_reviewers=true`
to fetch review API data; `include_reviewers=false` returns an empty reviewer
list. `query:pull-request:feedback` fetches normalized feedback for one
provider/repo/PR stream and returns `feedback` plus `next_cursor`; workflows own
where that cursor is stored.

Azure DevOps comment tools with `mentions` also use the configured ADO PAT to
call the identities API; that PAT must be allowed to read identities, equivalent
to Azure DevOps OAuth `vso.identity`.

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

`query:communication:messages` reads normalized message history from the local
Teams or Discord inbox stores populated by the ingest workers. It requires a
declared communication target with the `read` capability, supports Teams and
Discord only, does not call live provider APIs, and returns normalized
`messages` plus `next_cursor` so the workflow owns cursor persistence. The
query does not advance or persist read cursors on the host.

`trigger:communication:new-message` uses the same communication target store for inbound reads.
Targets selected for this trigger must include `read`, must be Teams channel/chat or Discord
channel targets, and are resolved for the trigger's `directory_path` before the provider polls the
host-owned inbox stores.

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

The checked-in APP provisioning workflow uses `effect:ado:pipeline-run` to queue an Azure DevOps
deployment pipeline, then uses `gate:map` to derive fields such as `app_base_url`,
`app_public_url`, `app_api_alias`, `app_ui_alias`, and `public_domain`. The mapped
`session_metadata_patch` is stored with `effect:session:update-metadata` before the originating
agent session is resumed.

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

Switch graph:

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
      "id": "normalize-ticket",
      "display_name": "Normalize ticket fields",
      "type": "gate:map",
      "gate_type": "map",
      "mapping": {
        "session_id": "${{trigger.ticket_session_id}}",
        "state": "${{trigger.state}}",
        "ticket_number": "${{trigger.ticket_id}}"
      }
    },
    {
      "id": "route-ticket",
      "display_name": "Route ticket",
      "type": "gate:switch",
      "cases": [
        {
          "port": "ready",
          "label": "Ready path",
          "when": {
            "left": "${{normalize-ticket.state}}",
            "equals": "ready"
          }
        },
        {
          "port": "blocked",
          "label": "Blocked path",
          "match": {
            "state": "blocked"
          }
        }
      ],
      "default_port": "fallback"
    }
  ],
  "effects": [
    {
      "id": "effect-ready",
      "display_name": "Ready effect",
      "type": "effect:agent:prompt",
      "session_id": "session-1",
      "prompt": "Ready: ${{normalize-ticket.ticket_number}}"
    },
    {
      "id": "effect-blocked",
      "display_name": "Blocked effect",
      "type": "effect:agent:prompt",
      "session_id": "session-1",
      "prompt": "Blocked: ${{normalize-ticket.ticket_number}}"
    },
    {
      "id": "effect-fallback",
      "display_name": "Fallback effect",
      "type": "effect:agent:prompt",
      "session_id": "session-1",
      "prompt": "Fallback: ${{normalize-ticket.ticket_number}}"
    }
  ],
  "edges": [
    {
      "source_id": "ticket-created",
      "target_id": "normalize-ticket"
    },
    {
      "source_id": "normalize-ticket",
      "target_id": "route-ticket"
    },
    {
      "source_id": "route-ticket",
      "source_port": "ready",
      "target_id": "effect-ready"
    },
    {
      "source_id": "route-ticket",
      "source_port": "blocked",
      "target_id": "effect-blocked"
    },
    {
      "source_id": "route-ticket",
      "source_port": "fallback",
      "target_id": "effect-fallback"
    }
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

If the pending wake becomes obsolete, the session can cancel it with `cancel_scheduled_wake`
instead of deleting the request:

```json
{
  "session_id": "issue-11-session",
  "request_id": "wake-3f2d8a5c8d2e4b2cb8f0f0d4e9b8b7c1",
  "reason": "No longer needed.",
  "metadata": {
    "source": "agent"
  }
}
```

Successful cancellation returns `status="cancelled"` and preserves the request in wake history:

```json
{
  "session_id": "issue-11-session",
  "request_id": "wake-3f2d8a5c8d2e4b2cb8f0f0d4e9b8b7c1",
  "status": "cancelled",
  "request": {
    "status": "cancelled",
    "cancelled_at": "2026-04-01T09:55:00Z",
    "cancelled_by_session_id": "issue-11-session",
    "cancel_reason": "No longer needed.",
    "cancel_metadata": {
      "source": "agent"
    }
  }
}
```

MCP-created schedules always use `resume_mode=same_context`; on container workers that means the
prompt command resumes the last Codex context when no explicit thread id is available. Admin
created or CLI-created recurring schedules can set `resume_mode=new_context` to start a fresh
container Codex exec session.

Cancelled requests remain visible in admin history. A session can cancel its own one-time wake
while it is pending, due, dispatching, queued, dispatched and awaiting confirmation, failed, or
missed/late. Completed and already-cancelled one-time wakes remain immutable, and other sessions
cannot cancel them. The tool also returns clear terminal statuses for `already_cancelled`,
`already_completed`, `not_found`, `missing_request_id`, and `permission_denied`; older clients may
still understand `already_dispatched`, but dispatched/awaiting-confirmation wakes now cancel
successfully.

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

Busy container-worker wake requests are still treated as handled for one-shot consumption and cron
advancement after they queue a prompt, so the same schedule rules apply whether the session is
resumed immediately or deferred by the prompt queue.

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
| `DIDWW_SIP_BRIDGE_TOKEN` | host shell or `secrets.env` | `src/agent_sandbox/voice_sessions.py` when `phone_call_backend` is `openai_realtime_didww` and the default `didww_sip_bridge_token_env` is used. |
| `VOICE_MEDIA_CONTROLLER_TOKEN` | host shell or `secrets.env` | `src/agent_sandbox/gateway.py` voice media controller endpoints when Realtime bridges create delegated origin-agent queries. |
| `ASTERISK_ARI_PASSWORD` | host shell or `secrets.env` | `src/agent_sandbox/didww_sip_bridge.py` for Asterisk ARI control of DIDWW outbound calls. |
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
- `addon_loader`
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
| `addon_loader` | host config, legacy gateway YAML, and trigger-orchestrator config | Optional add-on module/entry-point loading for Admin Web routes/navigation and workflow-provider contributions. Add-ons are disabled by default. |
| `enable_directory_scoped_config` | legacy gateway YAML | `src/agent_sandbox/admin/directory.py`, Admin Web directory edit surfaces, `src/agent_sandbox/daemons/ado_issue_worker.py`, and `src/agent_sandbox/daemons/pr_feedback_worker.py` (transition flag for directory edit surfaces and worker shadow resolution). |
| `approval_ttl_seconds` | legacy gateway YAML | `src/agent_sandbox/policy.py` approval expiry computation. |
| `default_session_ttl_seconds` | legacy gateway YAML | `src/agent_sandbox/cli.py` session creation defaults. |
| `bind_network_cidr` | legacy gateway YAML | `src/agent_sandbox/config.py` validation/normalization (policy-bound network constraints). |
| `gateway_url` | legacy gateway YAML | `src/agent_sandbox/cli.py` health checks / external endpoint references. |
| `execution_backend`, `container_worker_base_url`, `container_worker_token_env`, `container_worker_default_image`, `container_worker_images`, `container_worker_job_timeout_seconds`, `container_worker_request_timeout_seconds`, `container_worker_workspace_persistence` | root or directory `providers.json` | `src/agent_sandbox/execution/container_worker_backend.py` and portable `effect:agent:*` container worker routing. `container_worker_default_image` is the fallback image; `container_worker_images` maps approved aliases to exact image refs such as the separate Codex and Claude runner images. |
| `container_worker_guest_openai_base_url`, `container_worker_guest_anthropic_base_url`, `container_worker_guest_gateway_url`, `container_worker_guest_http_target_relay_base_url`, `container_worker_guest_nuget_relay_base_url`, `container_worker_guest_pypi_index_url`, `container_worker_guest_npm_registry_url` | root `providers.json` | Container session bootstrap files under `/home/agent` for Codex/Claude model relays, MCP gateway, HTTP target relay, NuGet restore traffic, and Nexus-backed PyPI/npm restores. |
| `container_worker_mssql_enabled`, `container_worker_mssql_host`, `container_worker_mssql_port` | root `providers.json` | Gateway-side enablement and advertised worker SQL Server endpoint for `mssql_*` MCP provisioning tools. |
| `model_provider_mode`, `openai_api_base_url`, `openai_api_key_env`, `openai_relay_timeout_seconds`, `azure_openai_endpoint`, `azure_openai_auth_mode`, `azure_openai_api_key_env`, `azure_openai_api_version`, `azure_openai_model_deployments`, `anthropic_api_base_url`, `anthropic_api_key_env`, `anthropic_api_version`, `anthropic_relay_timeout_seconds` | root or directory `providers.json` | `src/agent_sandbox/relays/openai.py` host relay mode selection and upstream routing. Public mode is the default; Azure mode requires the endpoint, host env-var name, api-version, and logical-model-to-deployment mapping. Anthropic mode forwards supported Messages API HTTP endpoints and injects host-side `x-api-key`. `azure_openai_auth_mode` currently accepts only `api_key`. Legacy `openai_provider_mode` is accepted as an alias for `model_provider_mode`. |
| `communication_artifact_public_base_url`, `communication_artifact_link_secret_env`, `communication_artifact_link_ttl_seconds` | root or directory `providers.json` | Signed public download links for staged communication artifacts, used by `teams_post_file` when a non-image Teams file has no SharePoint/OneDrive `contentUrl`. The secret env var must be loaded by both gateway and Admin Web; the link TTL is also capped by the staged artifact expiry. |
| `phone_call_backend`, `retell_api_base_url`, `retell_api_key_env`, `retell_from_number`, `retell_default_agent_id`, `retell_request_timeout_seconds`, `openai_realtime_model`, `openai_realtime_api_key_env`, `openai_realtime_api_base_url`, `voice_media_controller_url`, `voice_media_controller_token_env`, `voice_media_controller_request_timeout_seconds`, `didww_sip_bridge_url`, `didww_sip_bridge_token_env`, `didww_from_number`, `inbound_phone_routes` | root or directory `providers.json` | `src/agent_sandbox/retell_phone.py`, `src/agent_sandbox/voice_sessions.py`, and `src/agent_sandbox/gateway.py` for host-side phone calls. |
| `contacts`, `user_contact_links` | root or directory `contacts.json` | `src/agent_sandbox/directory_config.py`, `src/agent_sandbox/retell_phone.py`, `src/agent_sandbox/voice_sessions.py`, and the admin Users API for directory-scoped contacts and user attachments. |
| `redacted_keys` | legacy gateway YAML (optional) | `src/agent_sandbox/gateway.py` request/response redaction in logs. |

### OpenAI relay keys

| Key | Found in | Consumed by |
|---|---|---|
| `openai_relay_host`, `openai_relay_port` | legacy gateway YAML | `src/agent_sandbox/relays/openai.py` listener bind. |
| `openai_api_base_url` | legacy gateway YAML | `src/agent_sandbox/relays/openai.py` upstream URL base. |
| `openai_api_key_env` | legacy gateway YAML | `src/agent_sandbox/relays/openai.py` env-var name lookup for API key. |
| `openai_relay_timeout_seconds` | legacy gateway YAML | `src/agent_sandbox/relays/openai.py` upstream request timeout. |
| `anthropic_api_base_url` | legacy gateway YAML or directory provider config | `src/agent_sandbox/relays/openai.py` Anthropic upstream URL base. |
| `anthropic_api_key_env` | legacy gateway YAML or directory provider config | `src/agent_sandbox/relays/openai.py` env-var name lookup for Anthropic API key. |
| `anthropic_api_version` | legacy gateway YAML or directory provider config | `src/agent_sandbox/relays/openai.py` default `anthropic-version` header. |
| `anthropic_relay_timeout_seconds` | legacy gateway YAML or directory provider config | `src/agent_sandbox/relays/openai.py` upstream Anthropic request timeout. |

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

### Workflow state

`effect:state:update` and `query:state:get` persist graph-local workflow state in
`runtime/workflow-state.json`. Records are scoped by the tuple
`(directory_path, graph_id, namespace, key)`, so the same namespace/key pair can be reused safely
in different persisted workflow graphs.

`effect:state:update` supports `set`, `patch`, and `delete`. `set` replaces the stored JSON value
and starts at version `1` when the record is new. `patch` applies JSON Merge Patch semantics, so
`null` removes object members. `expected_version` is an optimistic-concurrency guard: when it does
not match the current version, the write is rejected without mutating the file and the response
reports the conflict. Missing records compare as version `0`.

`query:state:get` returns `found: false` and `value: null` when no record exists for the current
scope. Both nodes require persisted workflow graph metadata at runtime. The worker resolves
`graph_id` from `workflow_graphs` in inline config or `graphs` in workflow packs and does not
invent fallback graph IDs for state nodes.

### Phone-call keys

| Key | Found in | Consumed by |
|---|---|---|
| `phone_call_backend` | provider config overlays | Selects `retell` or `openai_realtime_didww` for `make_phone_call`; defaults to `retell`. |
| `retell_api_base_url` | provider config overlays | Optional override for Retell create-call, get-call, and webhook signature configuration; defaults to `https://api.retellai.com` and normally does not need to be set. |
| `retell_api_key_env` | provider config overlays | Host env-var name lookup for the Retell API key; default `RETELL_API_KEY`. |
| `retell_from_number` | provider config overlays | Outbound Retell caller number used only by the host-side create-call request. |
| `retell_default_agent_id` | provider config overlays | Retell `override_agent_id` used for directory-contact phone calls. |
| `retell_request_timeout_seconds` | provider config overlays | HTTP timeout in seconds for Retell create-call and get-call requests. |
| `openai_realtime_model` | provider config overlays | Realtime model passed to projected voice-session bridges; defaults to `gpt-realtime-2`. |
| `openai_realtime_api_key_env` | provider config overlays | Legacy provider setting. Host-owned Realtime bridges use bridge-local API key env configuration instead of accepting per-call env-var names. |
| `openai_realtime_api_base_url` | provider config overlays | Legacy provider setting. Host-owned Realtime bridges use bridge-local upstream base URL configuration instead of accepting per-call base URL overrides. |
| `voice_media_controller_url` | provider config overlays | Optional voice media controller URL passed to DIDWW or Teams bridges. |
| `voice_media_controller_token_env` | provider config overlays | Host env-var name for the voice media controller token. |
| `voice_media_controller_request_timeout_seconds` | provider config overlays | HTTP timeout in seconds for voice media controller and bridge requests. |
| `didww_sip_bridge_url` | provider config overlays | Host-owned SIP bridge URL used by `openai_realtime_didww` calls. |
| `didww_sip_bridge_token_env` | provider config overlays | Host env-var name for the DIDWW SIP bridge bearer token; default `DIDWW_SIP_BRIDGE_TOKEN`. |
| `didww_from_number` | provider config overlays | Optional caller ID sent to the DIDWW SIP bridge. |
| `inbound_phone_routes` | provider config overlays | Fixed inbound DID or called-number routes that bind a caller policy, directory scope, and Realtime projection prompt to an existing Truman session. |

### Inbound phone route keys

Each `inbound_phone_routes` entry is a JSON object with these keys.

| Key | Found in | Consumed by |
|---|---|---|
| `id` | within each `inbound_phone_routes` entry | Stable route identifier used for matching and idempotent provisioning. |
| `did` | within each `inbound_phone_routes` entry | Normalized inbound DID match value; numbers are reduced to `+` and digits only. |
| `called_number` | within each `inbound_phone_routes` entry | Optional normalized called-number match value; numbers are reduced to `+` and digits only. |
| `directory_path` | within each `inbound_phone_routes` entry | Directory scope used to resolve provider overlays and route-specific context. |
| `session_id` | within each `inbound_phone_routes` entry | Active Truman session used to project the inbound call and receive reconciliation wakes. |
| `allowed_callers` | within each `inbound_phone_routes` entry | Allow-list of normalized caller numbers. |
| `blocked_callers` | within each `inbound_phone_routes` entry | Optional deny-list of normalized caller numbers. |
| `route_prompt` | within each `inbound_phone_routes` entry | Route prompt used to build the projected live voice session. |
| `startup_text` | within each `inbound_phone_routes` entry | Optional caller-facing startup text for the initial greeting. |

Inbound routes are matched on normalized numbers, so the gateway and bridge compare digits plus an
optional leading `+` rather than the raw formatted input. The runtime stores and audit logs keep
redacted or hashed DID/caller forms only; raw numbers are not returned to agents or written to the
JSON stores. Accepted inbound calls are first provisioned through
`POST /voice-sessions/inbound-phone-calls`; when the bridge reaches a terminal state it syncs
sanitized status and transcript data back through
`POST /voice-sessions/{voice_session_id}/bridge-result` so the gateway-owned voice-session store can
schedule the origin-session reconciliation wake.

### DIDWW SIP bridge service keys

These keys live in `config/didww-sip-bridge.env` or host environment and are consumed by
`agent-sandbox-didww-sip-bridge`. They are service-local settings, not directory-scoped provider
overlays.

| Key | Found in | Consumed by |
|---|---|---|
| `DIDWW_SIP_BRIDGE_HOST`, `DIDWW_SIP_BRIDGE_PORT` | bridge env | HTTP listener bind for the bridge API. |
| `DIDWW_SIP_BRIDGE_TOKEN_ENV` | bridge env | Env-var name containing the bridge API bearer token; default `DIDWW_SIP_BRIDGE_TOKEN`. |
| `DIDWW_SIP_BRIDGE_ENABLE_INBOUND_PHONE_CALLS` | bridge env | Enables the long-lived inbound `StasisStart` listener for fixed DID routes; defaults to `false`. |
| `DIDWW_SIP_BRIDGE_INBOUND_GATEWAY_URL` | bridge env | Gateway controller URL used by accepted inbound calls for provisioning and terminal bridge-result sync; required when inbound phone calls are enabled. |
| `DIDWW_SIP_BRIDGE_INBOUND_GATEWAY_TOKEN_ENV` | bridge env | Env-var name containing the gateway bearer token used for inbound provisioning and terminal bridge-result sync; default `VOICE_MEDIA_CONTROLLER_TOKEN`. |
| `OPENAI_REALTIME_API_KEY_ENV`, `OPENAI_REALTIME_MODEL`, `OPENAI_REALTIME_API_BASE_URL`, `OPENAI_REALTIME_VOICE`, `OPENAI_REALTIME_IDLE_TIMEOUT_MS`, `OPENAI_REALTIME_TRANSCRIPTION_MODEL`, `OPENAI_REALTIME_INPUT_NOISE_REDUCTION_TYPE`, `OPENAI_REALTIME_TURN_DETECTION_TYPE`, `OPENAI_REALTIME_VAD_THRESHOLD`, `OPENAI_REALTIME_VAD_PREFIX_PADDING_MS`, `OPENAI_REALTIME_VAD_SILENCE_DURATION_MS` | bridge env | Server-side OpenAI Realtime WebSocket configuration, including built-in input noise reduction and bridge-local turn-detection tuning. |
| `ASTERISK_ARI_URL`, `ASTERISK_ARI_USER`, `ASTERISK_ARI_PASSWORD_ENV`, `ASTERISK_ARI_APP` | bridge env | Asterisk ARI HTTP/WebSocket control connection. |
| `ASTERISK_PJSIP_ENDPOINT`, `ASTERISK_ENDPOINT_TEMPLATE` | bridge env | Asterisk endpoint used to originate DIDWW outbound calls; default template is `PJSIP/{number}@{trunk}`. |
| `DIDWW_SIP_BRIDGE_EXTERNAL_MEDIA_HOST`, `DIDWW_SIP_BRIDGE_RTP_BIND_HOST`, `DIDWW_SIP_BRIDGE_RTP_PORT_MIN`, `DIDWW_SIP_BRIDGE_RTP_PORT_MAX`, `DIDWW_SIP_BRIDGE_RTP_PAYLOAD_TYPE`, `DIDWW_SIP_BRIDGE_MEDIA_FORMAT`, `DIDWW_SIP_BRIDGE_SAMPLE_RATE_HZ`, `DIDWW_SIP_BRIDGE_FRAME_DURATION_MS` | bridge env | Asterisk `ExternalMedia` RTP settings for bidirectional audio between Asterisk and the Python bridge. Use static G.711 RTP: `alaw` with payload type `8` by default, or `ulaw` with payload type `0`; both use `DIDWW_SIP_BRIDGE_SAMPLE_RATE_HZ=8000`. |
| `DIDWW_SIP_BRIDGE_CALL_TIMEOUT_SECONDS`, `DIDWW_SIP_BRIDGE_MAX_CALL_SECONDS`, `DIDWW_SIP_BRIDGE_REQUEST_TIMEOUT_SECONDS`, `DIDWW_SIP_BRIDGE_MAX_CONCURRENT_CALLS` | bridge env | Bridge control-plane timeout and concurrency limits. |
| `DIDWW_SIP_BRIDGE_VOICE_QUERY_POLL_INTERVAL_SECONDS`, `DIDWW_SIP_BRIDGE_VOICE_QUERY_TIMEOUT_SECONDS` | bridge env | Polling and timeout behavior for async `query_origin_agent` delegation back to the originating session. |
| `DIDWW_SIP_BRIDGE_BARGE_IN_DELAY_MS`, `DIDWW_SIP_BRIDGE_BACKCHANNEL_MAX_WORDS`, `DIDWW_SIP_BRIDGE_BACKCHANNEL_PHRASES` | bridge env | Delayed bridge-controlled barge-in for DIDWW Realtime calls; short configured backchannels are transcribed but do not cancel assistant audio. |

### Teams meeting media bridge service keys

These keys live in the Windows-hosted Teams media bridge process environment and are consumed by the
C# bridge under `integrations/teams-meeting-media-bridge`.

| Key | Found in | Consumed by |
|---|---|---|
| `TEAMS_BRIDGE_TOKEN`, `TEAMS_MEETING_BRIDGE_TOKEN` | bridge env | Bearer token required by the bridge HTTP API; `TEAMS_MEETING_BRIDGE_TOKEN` remains a compatibility alias. |
| `BRIDGE_ADAPTER_MODE` | bridge env | Selects `InMemory` or `Graph`; production Teams media uses `Graph`. |
| `BRIDGE_SPEECH_MODE` | bridge env | Selects `AzureSpeech` or `OpenAIRealtime`; defaults to `AzureSpeech` for Graph mode. |
| `TEAMS_BOT_APP_ID`, `TEAMS_BOT_APP_SECRET`, `TEAMS_BOT_TENANT_ID`, `TEAMS_BOT_PUBLIC_CALLBACK_BASE_URL`, `TEAMS_BOT_SERVICE_FQDN`, `TEAMS_BOT_PUBLIC_IP`, `TEAMS_BOT_PUBLIC_PORT`, `TEAMS_BOT_INTERNAL_PORT`, `TEAMS_BOT_CERT_THUMBPRINT`, `TEAMS_ONLINE_MEETING_LOOKUP_USER_ID`, `TEAMS_BOT_MEDIA_PORT_RANGE_START`, `TEAMS_BOT_MEDIA_PORT_RANGE_END` | bridge env | Microsoft Graph/application-hosted media setup used to join and exchange PCM audio with Teams meetings. |
| `AZURE_SPEECH_KEY`, `AZURE_SPEECH_REGION`, `AZURE_SPEECH_ENDPOINT`, `AZURE_SPEECH_RECOGNITION_LANGUAGE`, `AZURE_SPEECH_DEFAULT_VOICE` | bridge env | Azure Speech recognition/synthesis settings used when `BRIDGE_SPEECH_MODE=AzureSpeech`. |
| `OPENAI_REALTIME_API_KEY`, `OPENAI_API_KEY`, `OPENAI_REALTIME_MODEL`, `OPENAI_REALTIME_BASE_URL`, `OPENAI_REALTIME_VOICE`, `OPENAI_REALTIME_TURN_DETECTION`, `OPENAI_REALTIME_TRANSCRIPTION_MODEL` | bridge env | Server-side OpenAI Realtime WebSocket settings used when `BRIDGE_SPEECH_MODE=OpenAIRealtime`; `OPENAI_API_KEY` is a fallback for the Realtime API key. |

### Directory contact keys

| Key | Found in | Consumed by |
|---|---|---|
| `contacts` | contact config overlays | Directory-scoped contact map keyed by stable contact id. Entries include `name`, `description`, optional `email_addresses`, and `phone_numbers`; `list_contacts` exposes only safe summaries, and phone-call backends require exactly one primary phone number on callable contacts. |
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
| `github_token_env` | legacy gateway YAML | `src/agent_sandbox/runtime.py`, `src/agent_sandbox/reviews/request_service.py`, PR feedback trigger providers, and GitHub PR get query providers. |
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
| `__ANTHROPIC_BASE_URL__` | `config/cloud-init-user-data.yaml` | `scripts/launch_agent_vm.sh`; written as `AGENT_SANDBOX_ANTHROPIC_BASE_URL` and `ANTHROPIC_BASE_URL`. |
| `__NUGET_FALLBACK_PACKAGES__` | `config/cloud-init-user-data.yaml` | `scripts/launch_agent_vm.sh`; written as `AGENT_SANDBOX_NUGET_FALLBACK_PACKAGES`. |
| `__NUGET_PACKAGES__` | `config/cloud-init-user-data.yaml` | `scripts/launch_agent_vm.sh`; written as `NUGET_PACKAGES`. |
| `__NUGET_HTTP_CACHE_PATH__` | `config/cloud-init-user-data.yaml` | `scripts/launch_agent_vm.sh`; written as `NUGET_HTTP_CACHE_PATH`. |
| `__NUGET_CACHE_FS_TYPE__`, `__NUGET_CACHE_MOUNT_OPTIONS__`, `__NUGET_CACHE_TAG__` | `config/cloud-init-user-data.yaml` | `scripts/launch_agent_vm.sh`; mount + `/etc/fstab` entry for shared NuGet cache. |
| `OPENAI_API_KEY` fallback in profile (`relay-placeholder`) | `config/cloud-init-user-data.yaml` | Guest runtime bootstrap fallback for OpenAI-compatible relay sessions. |
| `ANTHROPIC_AUTH_TOKEN` fallback in profile (`relay-placeholder`) | `config/cloud-init-user-data.yaml` | Guest runtime bootstrap fallback for Anthropic-compatible relay sessions. |

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
| `policy.json` | Session/policy defaults and operator restrictions | `approval_ttl_seconds`, `approval_notification_target_ids`, `default_session_ttl_seconds`, Discord/email allowlists, `email_send_enabled`, `pr_reply_require_approval`, `redacted_keys`, `mcp_tool_policies`, `tool_argument_policies` |
| `providers.json` | Upstream/provider endpoints and credentials-by-reference | Discord, OpenAI, HTTP target relay, NuGet, GitHub, Azure DevOps, Teams, container worker, Retell |
| `contacts.json` | Directory contact list and admin-user contact attachments | `contacts`, `user_contact_links` |
| `email.json` | Mailbox provider definitions and email provider runtime settings | `email_mailboxes`, `email_provider_timeout_seconds` |
| `wake.json` | Directory-scoped wake prompt behavior | `pr_feedback_resume_prompt`, `discord_resume_prompt` |
| `ado-worker.json` | Azure DevOps PR automation metadata and optional workflow repo metadata | `ado_area_filters`, `ado_team_filters`, `ado_repo_source`, `create_prs_automatically`, `create_prs_as_draft` |
| `issue-setup.json` | VM issue setup/git hand-off defaults | `git_root`, `git_host`, `git_user`, `git_ssh_alias`, optional `default_model`, `post_exec_verification_prompt`, `enable_guest_heartbeat`, `guest_heartbeat_prompt`, optional `issue_setup_azure_devops_pr_pat_env` |
| `pr-template.json` | Directory-scoped markdown boilerplate prepended to automated PR bodies | `pr_template_markdown` |
| `vm.json` | Guest-facing relay/network defaults and libvirt VM template selection data | `bind_network_cidr`, `gateway_url`, OpenAI relay host/port, NuGet relay host/port, HTTP target relay host/port, `libvirt_vm_default_template`, `libvirt_vm_templates` |
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

The VM template catalog in `vm.json` follows the same map merge rule, so child directories inherit parent `libvirt_vm_templates` aliases and can override individual aliases without redefining the whole catalog. The catalog values are cloud-init-compatible qcow2 image refs, not Docker images. `effect:agent:create.vm_image` resolves against this catalog, `libvirt_vm_default_template` may resolve either an approved alias or a direct non-empty image ref for new sessions, while explicit selectors resolve only approved aliases. Existing-session upserts preserve the recorded template metadata when `vm_image` is omitted. Use `container_image` for `container_worker_vm`. This configuration is validated and resolved before any VM launch path, and the selected image is passed through to `scripts/launch_agent_vm.sh` via `LIBVIRT_BASE_IMAGE` with `BASE_IMAGE` as a manual override fallback.

For operator prep, smoke validation, and failure diagnosis, see [libvirt-vm-template-operations.md](libvirt-vm-template-operations.md).

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
- Session manifests may also set `allowed_contact_ids` to narrow the effective directory contact
  list for phone tools. `null` or omission keeps all directory contacts, `[]` keeps none, and the
  runtime rejects ids that are not present in the session directory's effective `contacts.json`
  overlay.

Approval notification policy:

- `approval_notification_target_ids` is a list of host-owned communication target IDs. The targets
  are configured in Admin Web under Configuration -> Communications and stored in
  `runtime/communication-targets.db`; each selected target must include the `notify` capability.
- Directory policy sets the inherited baseline. Session policy can add more target IDs for one
  session, and a live workflow-run approval lease can add run-scoped targets for that run. The
  runtime resolves the final list in that order and deduplicates first-seen IDs.
- In Admin Web, this field is presented as a selectable list of known notify-capable communication
  targets with an `Add communication target` action.
- Lists are normalized by trimming, lowercasing, and dropping duplicates. The default is an empty
  list, which disables approval notifications.
- This field does not grant guests Discord, Teams, or email send permission. The host sends
  notifications for newly created approval records, and delivery failures are audited without
  changing the approval state. Operators still decide through Admin Web, the admin service, or
  `agent-sandbox-cli approve`.

Approval-required tool policy:

- `approval_required_tools` is a list of MCP tool names that require approval before execution.
  Values can come from inherited directory `policy.json` overlays or from the session manifest.
- `semantic_delete_points` is always approval-required by code-level policy when the session is
  otherwise allowed to call it. Session or directory config can still deny the tool, but cannot make
  semantic point deletion approval-free.
- This list does not grant access. A tool must also be present in `allowed_tools`, and normal policy
  checks run before approval gating.
- Session-scoped MCP `tools/list` responses and policy summaries still show approval-required tools
  as available, but mark them as requiring approval. The visibility layer is informational only and
  does not replace server-side enforcement.
- The gateway creates `mcp_tool_invocation` approval requests for gated calls made without
  `approval_request_id`; approved retries must use the exact same payload and the generated request
  id.
- `approval_submit` and `approval_check` are excluded from generic invocation gating so agents can
  request and inspect approvals.
- `delete_ado_backlog_item` follows the same gate: an allowed session still gets a pending
  `mcp_tool_invocation` approval for each delete call, and approved retries must use the exact same
  payload.

MCP tool approval policy:

- `mcp_tool_policies` is a tool-keyed map of directory rules. Keys must match a known MCP
  `ToolName`.
- Each rule can set:
  - `denied`: fully disallow the tool in this directory and descendants.
  - `require_approval`: force per-invocation approval for the tool.
  - `default_settings`: default grant settings with `ttl_seconds`, `max_uses` (`null` means
    unlimited), and `payload_mode` (`exact` or `any`).
  - `max_ttl_seconds`: hard upper bound for any approval grant TTL.
  - `max_uses`: hard upper bound for finite or unlimited approval grants.
  - `require_exact_payload`: hard rule that forbids payload variance.
- Approval grants default to `ttl_seconds: 3600`, `max_uses: 1`, and `payload_mode: exact`.
- Session-specific approval settings may relax those defaults only until a directory hard rule
  clamps them. Directory `denied` rules are inherited downward and cannot be undone by descendant
  directories or session policy. Session-scoped MCP manifests hide denied tools, but direct
  invocation still enforces the denial and writes the usual policy error and audit record.
- `effect:session:approve-mcp-tools-for-run` respects these rules: denied tools, tools that require
  per-invocation approval, and tools that force exact payload matching cannot be approved by a
  workflow-run lease.

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
`trigger:teams:direct-message` for one-on-one direct messages sent by configured sender user ids,
UPNs, or mail addresses.

Session and `effect:agent:create` manifests can use either legacy raw Teams/Discord IDs or
directory-scoped communication target IDs in their communication allowlists. Declared target IDs are
resolved at use time from the session directory, take precedence over raw IDs when a value collides,
and must expose the capability required by the operation: `read` for message reads, inbound
trigger polling, and attachment list/download; `write` for top-level sends; and `reply` for thread
replies and reactions. Raw legacy IDs keep the existing allowlist behavior.
`effect:agent:create.communication_target_ids` is an additive shorthand for known Teams, Discord,
and mailbox-backed email targets in the session directory. The workflow editor presents it as a
catalogue-backed multi-select, and execution maps the selected target IDs into the existing Discord,
Teams channel/chat/user, or email mailbox allowlist fields. Email target folders still resolve from
the effective email policy defaults unless `email_folders` is provided directly.

Teams channel targets can still be declared directly with `teams_channel_allowlist`, Teams chat
targets with `teams_chat_allowlist`, and one-on-one user send targets with
`teams_user_allowlist`. Teams `user:` communication targets are send-only; they are not valid for
Teams reads, reactions, attachment downloads, or `trigger:teams:new-message` polling.
`teams_post_status` can mention Graph users in channel posts, channel thread replies, and chat
posts; user targets are resolved into one-on-one chats before sending.
Direct-message trigger polling is configured with `sender_user_ids` on
`trigger:teams:direct-message`; entries can be Graph user IDs, UPNs, or mail addresses.

Key delegated Teams provider settings now include:

- `teams_backend: delegated_graph_api`
- `teams_delegated_identity_id`
- `teams_delegated_client_id`
- optional `teams_delegated_tenant_id`
- optional `teams_delegated_scopes` (default includes `offline_access`,
  `ChannelMessage.Read.All`, `ChannelMessage.Send`, and `Chat.ReadWrite`)
- Resolving Teams direct-message sender mail addresses and outbound mention targets may require
  adding a Graph user-read scope such as `User.ReadBasic.All` or `User.Read.All` and rerunning
  Teams auth.
- optional `teams_delegated_authority_base_url`
- optional `teams_delegated_credential_store_path`
- optional `teams_history_page_cap` (default `1`) and `teams_history_deadline_seconds` (default
  `5.0`) bound Microsoft Graph history pagination for target-scoped `teams_get_messages` and
  Teams ingest scans
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
| `AGENT_SANDBOX_MODEL_PRICING_PATH` | `src/agent_sandbox/model_usage/pricing.py` and Admin Web Configuration | Optional override for the local model pricing catalog used by `query:model-usage:list` cost estimates. Defaults to `config/model-pricing.openai.json`; Admin Web displays and edits the active catalog after privileged unlock with revision checks. |
| `AGENT_SANDBOX_ADMIN_WEB_PAGE_TITLE`, `AGENT_SANDBOX_ADMIN_WEB_HEADER_TITLE`, `AGENT_SANDBOX_ADMIN_WEB_*_LOGO_*`, `AGENT_SANDBOX_ADMIN_WEB_BRANDING_ASSET_DIR` | `config/admin-web.env` | Optional Admin Web branding. The Python service exposes `/api/admin/branding` and `/api/admin/branding/assets/...`; the React shell uses them for the browser title, header title, header logo, product logo, and sign-in company logo without rebuilding per environment. |
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
| `scripts/bootstrap_claude_vm.sh` | `HOST_CLAUDE_BIN`, `ANTHROPIC_BASE_URL`, `GATEWAY_URL`, `SSH_PRIVATE_KEY_PATH` |
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
| `scripts/launch_agent_vm.sh` | `VM_STORAGE_DIR`, `GATEWAY_URL`, `SSH_PUBLIC_KEY_FILE`, `SSH_PRIVATE_KEY_PATH`, `LLM_BASE_URL`, `ANTHROPIC_BASE_URL`, `LIBVIRT_BASE_IMAGE`, `BASE_IMAGE`, `SHARED_NUGET_CACHE_DIR`, `SHARED_NUGET_CACHE_TAG`, `SHARED_NUGET_CACHE_MODE`, `RAM_MB`, `VCPUS`, `DISK_SIZE_GB` |
| `scripts/lib/agent_status.sh` | `VM_AGENT_STATE_MAX_AGE_SECONDS` |
| `scripts/list_running_vms.sh` | `SSH_PRIVATE_KEY_PATH`, `VM_AGENT_LIST_CONNECT_TIMEOUT_SECONDS` |
| `scripts/prewarm_container_worker_nuget_cache.sh` | `CONTAINER_WORKER_NUGET_CACHE_PATH`, `DOTNET_COMMAND`, `NUGET_RELAY_BASE_URL` |
| `scripts/prewarm_nuget_cache.sh` | `SHARED_NUGET_CACHE_DIR`, `NUGET_CONFIG_FILE`, `NUGET_HTTP_CACHE_DIR`, `NUGET_TEMP_DIR` |
| `scripts/resume_issue_agent.sh` | `SSH_PRIVATE_KEY_PATH`, `AGENT_PROVIDER`, `AGENT_MODEL`, `VM_WAIT_IP_TIMEOUT_SECONDS`, `VM_WAIT_SSH_TIMEOUT_SECONDS`, `RESUME_COMMAND_TIMEOUT_SECONDS` |
| `scripts/setup_vm.sh` | `SSH_PUBLIC_KEY_FILE`, `VM_WAIT_IP_TIMEOUT_SECONDS`, `VM_WAIT_SSH_TIMEOUT_SECONDS`, `SSH_PRIVATE_KEY_PATH`, `AGENT_PROVIDER`, `AGENT_MODEL` |
| `scripts/tail_issue_vm_log.sh` | `SSH_PRIVATE_KEY_PATH` |

### Notes on completeness

- Section 1 captures all parameters from `config/*.env.example`.
- Section 2 captures all `GatewayConfig`/`MailboxConfig` YAML keys.
- Section 3 captures all cloud-init template placeholders currently substituted by VM launch.
- Section 5 captures script-level env overrides used directly in shell scripts.

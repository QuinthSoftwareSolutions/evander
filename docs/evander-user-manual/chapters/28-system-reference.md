# 28. System Reference

> **At a glance.** This is the manual's lookup chapter. Use it when you remember that Evander has a service, command, port, file, or screen for something but cannot remember its exact name or owner. The earlier chapters explain why the pieces exist; the tables here help you find the right one quickly.

## Find the right map

You do not need to read this chapter from beginning to end. Start with the question you are trying to answer:

| If you are asking… | Go to… |
|---|---|
| “What is this background service for?” | [Persistent control-plane services](#282-persistent-control-plane-services) |
| “What does this port provide?” | [Default guest-reachable ports](#283-default-guest-reachable-ports) |
| “Which saved record is authoritative?” | [Runtime source-of-truth map](#284-selected-runtime-source-of-truth-map) |
| “What command starts this task?” | [Core command map](#285-core-command-map) |
| “Where do I find this in the interface?” | [Admin Web area map](#286-admin-web-area-map) or [Team Add-on view map](#287-team-add-on-view-map) |
| “Which part of the code owns this behavior?” | [Developer reference](#288-developer-reference-source-code-ownership-map) |
| “Which host checks should an administrator run?” | [Administrator runbook](#289-administrator-runbook-fast-orientation-commands) |

For ordinary operation, the screen maps and the plain responsibilities in the service tables are usually enough. Paths, ports, process names, and source locations become useful when an administrator or developer needs to verify the layer behind what the interface shows.

## 28.1 Reference conventions

All paths are relative to the repository root unless an absolute path is shown. Defaults describe the checked-in configuration for this edition; an installed system may override them. A row marked **optional** is part of the supported platform but is only useful when its integration is configured.

There are three names worth keeping distinct:

- a **console entry point**, such as `evander-gateway`;
- a **systemd unit**, such as `evander-gateway.service`;
- a **CLI subcommand**, such as `evander-cli trigger-orchestrator`.

The trigger worker illustrates why the distinction matters: its persistent unit is `evander-trigger-orchestrator.service`, while its executable interface is the `trigger-orchestrator` subcommand of `evander-cli`. There is no standalone `evander-trigger-orchestrator` console script.

## 28.2 Persistent control-plane services

`scripts/install_systemd_units.sh` is the installation inventory. It renders and enables the following units, then prints explicit start commands; installation itself does not start them.

| Unit | Process or command | Responsibility | Start when |
|---|---|---|---|
| `evander-firewall.service` | `scripts/apply_firewall.sh` | Applies the guest-to-host allowlist | Before isolated execution |
| `evander-gateway.service` | `evander-gateway` | MCP gateway, identity, policy, approval, and audit boundary | Agents need host tools |
| `evander-admin-web.service` | `evander-admin-web` | Operator UI and Admin API | Operators need the supported GUI/API |
| `evander-openai-relay.service` | `evander-openai-relay` | Host-owned OpenAI/Anthropic-compatible model access | Agents need configured models |
| `evander-nuget-relay.service` | `evander-nuget-relay` | Allowlisted NuGet feeds and cache | .NET workloads need packages |
| `evander-npm-audit-facade.service` | `evander-npm-audit-facade` | Narrow npm audit/package-cache surface | Front-end workloads need it |
| `evander-http-target-relay.service` | `evander-http-target-relay` | Session-scoped, allowlisted external HTTP targets | A workflow grants an HTTP target |
| `evander-didww-sip-bridge.service` | `evander-didww-sip-bridge` | DIDWW/ARI telephony and realtime voice bridge | Telephony is configured |
| `evander-ado-worker.service` | `evander-ado-worker` | Azure DevOps PR-feedback and completion polling | ADO PR automation is configured; work-item intake is workflow-owned |
| `evander-proxy-issue-sync.service` | `evander-proxy-issue-sync` | Refreshes proxy issue status | Proxy issue providers are configured |
| `evander-discord-ingest.service` | `evander-discord-ingest` | Ingests allowlisted Discord commands/messages | Discord is configured |
| `evander-discord-wake.service` | `evander-discord-wake` | Turns eligible Discord inbox activity into wakes | Discord wake is enabled |
| `evander-scheduled-wake.service` | `evander-scheduled-wake` | Resumes sessions whose scheduled wake is due | Scheduled work is enabled |
| `evander-managed-git-refresh.service` | `evander-managed-git-refresh` | Refreshes the exact authorized managed repository before Git reads | Agents clone, fetch, or pull managed repositories |
| `evander-teams-ingest.service` | `evander-cli teams-ingest` via its runner script | Ingests Microsoft Teams messages | Teams is configured |
| `evander-teams-meeting-ingest.service` | `evander-teams-meeting-ingest` | Ingests queued meeting transcript and speech-status events | Meeting integration is configured |
| `evander-trigger-orchestrator.service` | `evander-cli trigger-orchestrator` | Polls trigger providers, validates/deduplicates, and publishes durable trigger events | Workflow triggers are enabled |
| `evander-effect-orchestrator.service` | `evander-effect-orchestrator` | Traverses downstream workflow nodes and executes gates, queries, and effects | Workflows may run beyond their trigger |

The container worker is intentionally absent from this list. `evander-worker-daemon` runs on a dedicated worker VM deployed by `ansible/worker.yml`, not as another control-plane service installed by the script above.

## 28.3 Default guest-reachable ports

The firewall's checked-in default is `ALLOWED_TCP_PORTS="22 8000 11434 11666 11777 11881 8081 3142"`. Allowing a port makes a narrow host surface reachable; it does not grant arbitrary host access.

| Port | Surface | Typical guest use | Important qualification |
|---:|---|---|---|
| 22 | Restricted SSH/Git | Clone and push through managed Git | The command wrapper and scoped key enforce allowed repositories and refs |
| 8000 | MCP gateway | Invoke host tools at `/mcp` | A plain `GET /mcp` may return `406` and still prove reachability |
| 11434 | Model relay | OpenAI-compatible `/v1` and configured Anthropic surface | The host keeps upstream credentials |
| 11666 | NuGet relay | Restore from configured NuGet feeds | It is not a general HTTP proxy; health is `/healthz` |
| 11777 | HTTP target relay | Reach a session allowlist of external targets | Guest-supplied auth can pass through; host secrets are not injected |
| 11881 | npm audit facade | Narrow npm audit/cache operations | It is not general npm internet access |
| 8081 | Nexus cache | PyPI/npm package cache when enabled | Optional host package-cache service |
| 3142 | apt-cacher-ng | Worker provisioning package cache | Optional and primarily for provisioning |

Admin Web defaults to host port `8100`; it is an operator surface, not part of the default guest allowlist.

## 28.4 Selected runtime source-of-truth map

The runtime tree contains both live configuration and operational state. The table highlights the stores most useful for orientation; it is not an exhaustive file inventory. Back up related stores consistently, avoid ad-hoc concurrent edits, and use the service/API responsible for each path.

| Path | Canonical content | Primary owner or reader |
|---|---|---|
| `runtime/host-config.json` | Host-global bootstrap settings | Host configuration and runtime composition |
| `runtime/directories.json` | Directory tree and ownership metadata | Directory service |
| `runtime/directory-config/root/.../<domain>.json` | Root baseline and directory overlays | Directory configuration service |
| `runtime/admin-web.db` | Admin Web users and authentication state | Admin Web auth service |
| `runtime/sessions.json` | Core session manifests and lifecycle metadata | Session repository |
| `runtime/local-issues.json` | Unified local and proxy issue records | Issue service and provider workflows |
| `runtime/audit.jsonl` | Append-only tool and denial audit journal | MCP gateway and audit writers |
| `runtime/audit.db` | Rebuildable query index over audit entries | Admin Web Tool Calls |
| `runtime/orchestration.db` | Trigger events, internal events/subscriptions/schedules, workflow/effect/gate executions | Trigger and effect orchestrators |
| `runtime/workflow-pack-history/` | Pack revisions and append-only change ledger | Workflow pack service |
| `runtime/workflow-state.json` | Workflow-scoped state records | Workflow state providers |
| `runtime/wake-requests-store.db` | Scheduled wake requests and dispatch history | Scheduled-wake and reconciliation services |
| `runtime/model-usage.db` | Normalized model-usage observations and attribution | Model usage service and Team cost aggregation |
| `runtime/teams-meetings.json` | Teams meeting session/projection state | Teams meeting services |
| `runtime/managed-git-repositories.json` | Stable managed repository identities and bindings | Managed Git service |
| `runtime/managed-git/` | Host-owned bare repositories | Managed Git service and restricted SSH |
| `runtime/git-worktrees/` | Ephemeral operation worktrees | Managed Git effects; safe cleanup is service-owned |
| `runtime/artifact-spaces.json` | Artifact-space metadata and published versions | Artifact-space service |
| `runtime/published-artifacts/` | Host-published artifact blobs | Artifact effects |
| `runtime/communication-targets.db` | Directory-scoped communication targets | Messaging target service |
| `runtime/communication-artifacts/` | Staged message attachments | Communication effects |
| `runtime/review-request-store.json` | PR review-request inbox | Review request provider |
| `runtime/agent-consultations.db` | Consultation lifecycle and results | Consultation service |
| `runtime/host-agent-control-state.json` | Durable enabled/disabled agent state | Wake and execution control paths |
| `runtime/voice-sessions-store.json` | Phone/meeting/realtime session projection | Voice and meeting integrations |
| `runtime/addons/<addon-id>/` | Add-on-private state | The named add-on only |
| `runtime/addons/team-addon/state.db` | Team's canonical relational state | Team repository and Team Admin API |

`runtime/addons/team-addon/state.json` is a legacy import source, not the current Team database. The Team add-on imports it once when appropriate; ongoing state belongs in `state.db`.

## 28.5 Core command map

Run `evander-cli --help` and a subcommand's `--help` for the precise option set in the installed edition. These are the stable operator intentions behind the most common commands.

| Intent | Command or entry point |
|---|---|
| Initialize runtime state | `evander-cli init-state --runtime-dir runtime` |
| Broad machine-readable status | `evander-cli status --runtime-dir runtime --json` |
| Run the trigger loop interactively | `evander-cli trigger-orchestrator` |
| Register or refresh a managed repository | `evander-cli git-repos register ...` |
| Inspect managed repositories | `evander-cli git-repos list ...` |
| Set one managed repository's host commit identity | `evander-cli git-repos set-host-identity ...` |
| Create an issue VM | `evander-cli create-issue-vm <issue-id> ...` |
| Clone by managed identity | `evander-cli clone-repo-into-vm <vm-name> --managed-repo-id <id>` |
| Smoke-test a container worker | `evander-cli execution smoke-container-worker --json` |
| Start the MCP gateway outside systemd | `evander-gateway` |
| Start Admin Web outside systemd | `evander-admin-web` |
| Start the effect loop outside systemd | `evander-effect-orchestrator` |

Service installation has its own preflight contract:

```bash
./scripts/install_systemd_units.sh --preflight
sudo ./scripts/install_systemd_units.sh
```

## 28.6 Admin Web area map

Admin Web is the supported operator GUI. Its core areas answer different questions rather than presenting interchangeable views.

The navigation keeps all areas visible in six permanently expanded groups: **Overview** contains
Dashboard and Reports; **Operations** contains Agents, Sessions, Issues, Approvals, and Artifacts;
**Automation** contains Workflow and Skills; **Extensions** contains Add-ons, Connectors, Host Directories, and
add-on-contributed areas; **Infrastructure** contains VMs and Workers; and **Governance** contains
Tool Visibility, MCP Tools, Tool Calls, Configuration, and Users & Groups. Use **Find page** or
`Ctrl+K` / `Command+K` to search the currently visible built-in and add-on areas without changing
their hash routes or directory scope.

Inside **Configuration**, a second compact navigator exposes the active area's settings and
utilities. **Find setting** searches operator labels and help alongside technical domains and field
keys, then opens and highlights the selected field. Configuration URLs retain the selected area,
section, and optional field so operators can share a precise destination without changing the
underlying save or inheritance rules.

The URL contract uses `config_tab` for the area key, `config_section` for a section or utility
destination key, and optional `config_field` for an exact field name. The
`GET /api/configuration/state` response supplies area identifiers as `tabs[].key` and supplies a
deterministic, URL-safe `key` beside every entry in `tabs[].sections[]`; clients should use that
section key rather than deriving an identifier from the display title. Section keys are unique
within their area. Unknown or stale destinations fall back to the first available section, and an
unknown field is ignored.

| Area | Operator question |
|---|---|
| Dashboard | Is the control plane broadly healthy, and what needs attention? |
| Agents | Which active and historical execution targets represent agent work? |
| Approvals | Which gated tool calls require an operator decision? |
| Artifacts | Which durable agent archives can be resolved or downloaded? |
| Add-ons | Which runtime extensions are available, enabled, or awaiting refresh/restart? |
| Skills | Which packaged agent capabilities are visible and managed? |
| VMs | Which libvirt machines exist, and what is their lifecycle state? |
| Issues | Which local/provider issue records are available for work intake? |
| Sessions | Which agent sessions are active, paused, terminal, or awaiting intervention? |
| Tool Visibility | Which tools does a session actually see, and what binding or denial explains that result? |
| MCP Tools | Which directory policy controls approval and denial for each tool? |
| Connectors | Which named host-side integrations and operations are available in this directory? |
| Host Directories | Which approved host roots, agent capabilities, health checks, and quarantined entries are managed here? |
| Git Repositories | Which managed repositories are registered, when were they refreshed, and what host commit identity do they use? |
| Tool Calls | What was invoked, allowed, denied, or failed? |
| Configuration | Which host and directory-scoped values resolve here? |
| Reports | What happened during orchestration and operations, including effects and gates? |
| Users & Groups | Who can authenticate, and which groups/permissions govern operator access? |
| Workers | Which host/background systemd workers are configured, active, or reporting journal errors? |
| Workflow | Which pack revision is active, what do its graphs define, and what is the runtime activity? |

Workflow pack changes made in Admin Web are revision- and lock-aware. Do not bypass those semantics by rewriting a live pack file from a stale checkout.

## 28.7 Team Add-on view map

The Team Add-on contributes one navigation area with fifteen current views. There is no separate top-level **Roles** view; roles are edited in the **Teams** view.

| View | What it projects |
|---|---|
| Workspace | Cross-cutting attention, delivery, team, and work-status summary |
| Operations | Active leases, dispatches, sessions, direct-agent/subtree stand-down controls, and operational exceptions |
| Delivery | Attention-first, project-focused deliverables with review, release, verification, and publication evidence |
| Memory | Durable Team memory records and artifact references |
| Projects | Project definitions, memberships, leads, and goals |
| Task board | Project tasks arranged by workflow lane |
| Cases | Structured operational cases and their lifecycle |
| Teams | Teams, agents, memberships, responsibilities, roles, and models |
| Chat | Built-in and workflow-bridged external Team Chat conversations, messages, and external delivery state |
| Mail | Sent/received projections over the durable Team inbox |
| Activity | Redacted dispatch lifecycle and Team tool outcome events |
| Governance | Governance groups, goals, agenda, oversight, and cost rollups |
| Planning | Goals, deliverables, and status-filtered task context for planning work |
| Activity timeline | Time-ordered domain activity |
| Org chart | Visual team, reporting, and governance relationships |

UI state is a projection. For a disputed operational fact, prefer the Team Admin API payload and then `runtime/addons/team-addon/state.db` over what a stale browser tab last rendered.

## 28.8 Developer reference: source-code ownership map

| Concern | Start here |
|---|---|
| Runtime assembly | `src/evander/admin/composition.py` (`build_admin_runtime`, `AdminRuntimeComponents`) |
| Admin domain logic | `src/evander/admin/services/` |
| Admin Web routes | `src/evander/admin/web/app.py` and domain route modules |
| MCP gateway | `src/evander/gateway/server.py`, `app.py`, and `cli.py` |
| Policy and approval | `src/evander/policy.py`, approval services under `src/evander/admin/services/` |
| Trigger runtime | `src/evander/orchestration/`, `src/evander/triggers/`, and `src/evander/daemons/trigger_orchestrator.py` |
| Effects | `src/evander/effects/` and `src/evander/daemons/effect_orchestrator.py` |
| Event models | `src/evander/events/models.py` |
| Execution abstraction | `src/evander/execution/` |
| VM lifecycle | `src/evander/vm/` plus `scripts/*_vm.sh` |
| Container worker | `src/evander/worker_daemon.py`, `ansible/worker.yml` |
| Managed Git | `src/evander/admin/services/managed_git_service.py`, `managed_git_runtime_service.py`, `managed_git_worktree_service.py`, and `managed_git_common.py` |
| Code consultations | `src/evander/code_consultation_service.py`, `src/evander/gateway/consultation_tools.py`, and `src/evander/effects/providers/code_consultation.py` |
| Relays | `src/evander/relays/` |
| Communications | `src/evander/messaging/` |
| Core providers | `src/evander/providers/` |
| Add-on discovery | `src/evander/addons.py` and Python `evander.addons` entry points |
| Team domain/repository | `src/evander_team_addon/models.py`, `src/evander_team_addon/repository.py`, and `src/evander_team_addon/services/domain/` |
| Team commands | `src/evander_team_addon/services/commands/` |
| Team providers and wake context | `src/evander_team_addon/providers.py` |
| Team MCP tools | `src/evander_team_addon/mcp.py` |
| Team UI | `src/evander_team_addon/ui_static/` |

Route handlers and compatibility adapters should stay thin. New domain behavior belongs in a narrow service or domain module and should enter Admin surfaces through the supported runtime composition layer.

## 28.9 Administrator runbook: fast orientation commands

If an operator is handing off a problem, the useful starting packet is the selected directory, the affected Admin Web area, what was expected, what appeared instead, and any visible run, session, event, approval, or Team work-item ID. The administrator can then choose the narrow check below that corresponds to the reported layer.

These commands avoid intentional business-state mutation. `evander-cli status` is bootstrap-aware: it may create or migrate missing runtime configuration, and it reports a useful core subset rather than every installed service or endpoint. Run it as the intended host operator against the intended runtime directory.

```bash
# Basic control-plane summary; may initialize/migrate missing runtime configuration
evander-cli status --runtime-dir runtime --json

# Installed unit state
systemctl --no-pager --full status evander-gateway.service

# MCP endpoint reachability: 406 is a valid liveness signal for a plain GET
curl -i http://127.0.0.1:8000/mcp

# NuGet relay health
curl -fsS http://127.0.0.1:11666/healthz

# Team database shape without creating a missing database or exposing row contents
test -f runtime/addons/team-addon/state.db && \
  sqlite3 -readonly runtime/addons/team-addon/state.db '.tables'

# Repository and generated-document state
git status --short
python scripts/build_evander_user_manual.py --check
```

When a command reveals secrets or user content, prefer field names, counts, redacted identifiers, and status codes in incident notes. Do not paste raw runtime databases or environment files into tickets or documentation.

## Source trail

- `pyproject.toml` — installed console entry points and add-on registrations.
- `scripts/install_systemd_units.sh` — persistent control-plane unit inventory.
- `config/firewall.env.example` — default guest-reachable ports.
- `docs/configuration-reference.md` — runtime stores and configuration ownership.
- `frontend/admin-web/src/adminRouting.ts` and `frontend/admin-web/src/features/workers/WorkersScreen.tsx` — current Admin navigation and Workers semantics.
- `src/evander_team_addon/ui_static/index.html` and `src/evander_team_addon/ui_static/team-ui.js` — current Team view inventory and projections.
- `src/evander/admin/composition.py` and `src/evander/admin/services/` — supported Admin runtime assembly and domain services.

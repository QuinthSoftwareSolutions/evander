# Changelog

This project does not currently use release tags. The historical sections below
were reconstructed from the `main` history through `0680fb85` on 2026-05-04,
using non-merge commit messages and changed-file clusters as the source
material. Newer entries are added incrementally as changes land.

## 2026-05-21 - Rejected Issue Listing Filter

- MCP `list_issues(status=["Rejected"])` now returns closed local issues with a
  rejection reason while preserving the existing closed/completed filtering
  semantics.

## 2026-05-21 - Workflow Tool Policy Editor Fixes

- Allowed valid workflow saves to proceed after unrelated workflow-editor UI
  errors while still blocking invalid inspector drafts.
- Stopped browser-side JavaScript regex validation for tool argument policy
  pattern constraints so Python backend regex syntax remains editable and
  saveable from structured mode.

## 2026-05-21 - Workflow Smoke Test Hardening

- Hardened the workflow tool-argument-policies smoke coverage so effect-node
  selection uses Playwright locator actionability and waits for the specific
  tool policy inspector content instead of relying on cached click coordinates.

## 2026-05-21 - Container Git Mirror Concurrency

- Serialized container-worker Git mirror creation and refresh through the same per-mirror
  `agent-sandbox-sync.lock` used by the restricted SSH upload-pack wrapper, preventing concurrent
  `effect:agent:clone-repo` executions from mutating a shared mirror while another worker clone is
  reading it.

## 2026-05-21 - Dashboard Layout and 0.2.1 Version

- Bumped the project and workflow-editor package metadata to version `0.2.1`.
- Reworked the operations dashboard into a two-column layout with the Version and changelog panel
  open by default as the left-side product context anchor.
- Replaced dashboard action button text with icon-only controls that retain accessible labels and
  hover tooltips.

## 2026-05-21 - Container Cleanup Review Fixes

- Restored destructive Agents delete behavior for prepared and active
  `container_worker_vm` sessions while keeping manual session cleanup as a
  tombstone-preserving lifecycle action.
- Returned error-shaped Admin Web cleanup failure responses and made worker job
  delete retries no-op successfully for already-removed job tombstones.

## 2026-05-21 - Test Lint Hygiene

- Imported `pytest` in the Admin Web sessions and approvals test module so
  `pytest.MonkeyPatch` type annotations pass Ruff undefined-name checks.

## 2026-05-21 - ADO Worker Bootstrap Removal

- Removed ADO worker issue VM creation, setup script invocation, and guest bootstrap configuration.
  The worker now ingests Azure DevOps work items into local proxy issue/YAML state and leaves
  agent/session creation to workflows and effects.
- Trimmed ADO worker runtime, directory, Admin Web, and migration configuration surfaces to polling
  settings, filters, repo metadata, and PR automation flags.
- Stopped repeated ADO worker proxy issue/YAML rewrites for already-ingested work items whose
  Azure DevOps changed date has not advanced, and removed retired bootstrap keys from Ansible
  worker defaults.

## 2026-05-21 - Dashboard News and Changelog Download

- Added a root `NEWS.md` operator news feed, surfaced the latest news entry in
  the dashboard product context panel, and replaced the previous recent
  changelog-entry list with prose release guidance.
- Added an authenticated Admin Web changelog download endpoint and dashboard
  download button so operators can retrieve the full `CHANGELOG.md` from the
  Version and changelog panel.
- Added `news_index` paging for dashboard news so the release-info endpoint
  returns one `NEWS.md` post with previous/next metadata instead of returning
  the whole future news archive.

## 2026-05-19 to 2026-05-21 - Workflow Runtime, Policy, and Admin Hardening

Touched areas: Admin Web workflow APIs, MCP gateway policy enforcement,
workflow editor, workflow runtime providers, worker clients, deployment config,
docs, tests.

- Added authenticated external workflow-event ingress through Admin Web, including
  short-lived user tokens, configured machine tokens, durable `external.*`
  workflow events, idempotency handling, source attribution for machine tokens,
  and reference docs/env examples (`a73f1af`, `a2eea1f`, `52427a3`,
  `85c07c0`).
- Added the Admin Web capacity-health endpoint plus worker telemetry plumbing so
  operators can inspect control-plane storage, per-directory session lifecycle
  counts, worker/session health, queue age, and prompt pressure with explicit
  missing and unsupported states instead of inferred values.
- Added approval-gated MCP tool invocation support for sessions and directory
  policy. Operators can mark allowed tools as approval-required, the gateway
  creates audited `mcp_tool_invocation` approval requests, and retries are
  accepted only for the same session/tool/payload digest. Admin Web Approvals,
  Sessions, and Tool Calls gained the related review and filtering surfaces
  (`f39b0ed`).
- Added tool argument policies to narrow already-allowed MCP calls with
  `required`, `fixed`, `allowlist`, `denylist`, `pattern`, and `enum`
  constraints. Directory/session policy now validates, merges, displays, and
  enforces these rules before approval gating, with audited denials and a fix
  for session policy upserts (`fe9891a`, `43e17f4`, `d57371d`, `01652a8`,
  `803fcf5`, `8ebe99f`).
- Added workflow-run-scoped MCP approval leases and
  `effect:session:approve-mcp-tools-for-run`, allowing a workflow run to grant
  selected tools to a target session without mutating the session manifest.
  Agent prompt/enqueue effects carry the lease into the invocation and the
  gateway checks run, session, tool, expiry, and revocation (`f8d8a70`,
  `b92c5b9`).
- Added workflow artifact publishing support: shared `/artifacts` handling,
  `query:agent:artifact-text`, `effect:artifact:stage`,
  `effect:artifact:publish-artifacts`, an immutable published-artifact store,
  Admin Web artifact list/detail/download APIs, and an Artifacts tab
  (`f8d8a70`, `b92c5b9`).
- Expanded workflow run inspection with lookup/search by run, event, trigger
  event, trace, session, or communication artifact id; run-detail payloads now
  include tool-call audit links, agent/session/log references, approvals,
  communication artifacts, binding traces, missing-data signals, and Admin Web
  hash deep links into related tabs. Added a seed script and docs for the demo
  run-inspector fixture (`fa220d9`, `7217773`, `a33ed3e`, `d731011`,
  `05bec89`, `d68a495`, `ba374fb`, `bf02212`, `77dc5da`).
- Fixed workflow run-detail regressions around audit-link targeting and editor
  save-status display, with tests requiring real audit identifiers and preserving
  visible missing-data states instead of silently omitting unavailable links
  (`21dad2b`, `103c81a`).
- Improved workflow editor draft safety: saved baselines, immediate inspector
  edit staging, dirty-node tracking, per-node unsaved badges, selected-node
  reset, before-unload/tab/directory navigation guards, and unsaved-change smoke
  coverage (`66ac113`, `5abb233`, `4977971`, `80943de`, `81c42c2`,
  `0f4ed23`).
- Restored workflow editor Ctrl/Cmd-click multi-selection and added smoke
  coverage proving connected selected nodes copy and paste with their edge
  intact.
- Fixed compact/mobile workflow canvas behavior so one-finger gestures scroll the
  page, two-finger gestures pan the graph, and unlocked node drags still move and
  save node positions. Added mobile smoke coverage for scrolling, panning, and
  node drag persistence (`1a1e510`, `1d8e98a`, `35c559d`, `25e0101`,
  `ad5d76b`, `e2eface`).
- Improved `gate:for` authoring and runtime validation with JSON-array defaults
  for `items`, loopback validation for `body` paths, domain diagnostics that are
  preserved through Admin Web validation/save, and orchestrator coverage for
  loop execution behavior (`98afa69`, `476d01d`, `23d8033`).
- Prevented duplicate agent branch promotion by serializing
  `promote_agent_branch.sh` with a host-repo lock and tightened workflow-pack
  branch policy examples to keep agent pushes under `agents/` refs (`0cbbed3`,
  `b64f762`).
- Reduced workflow latency for fast in-process effect chains by hot-draining
  completed futures in the effect orchestrator cycle and scheduling another
  immediate pass while completions are being published (`71efbca`, `bb9176d`).
- Added retry/backoff handling for container-worker connect failures, including
  safe error text and focused client tests (`bb36bbf`, `b92c5b9`).
- Added an operator field-help inventory with taxonomy and regression checks,
  relabeled selected configuration fields, polished Admin Web styling, and added
  repo guidance to use existing APIs/locks when editing workflow packs because
  other users may be modifying those packs concurrently (`7cef1e4`, `0a880cb`,
  `6c0a718`, `8b35721`, `02e76ae`, `cac2d0d`, `71efbca`, `e3ea236`,
  `0b5b635`).
- Added a dashboard-scoped Admin Web JSON response cache so repeated operations
  dashboard loads reuse recent workers, agents, workflow, approval, session, and
  audit reads while detail tabs keep live reads and manual dashboard refreshes
  bypass the cache.
- Made operations dashboard top-level panels consistently collapsible, including
  the release metadata panel, and aligned the release panel styling with the
  existing dashboard panel treatment.
- Made the managed Admin Web Caddy post-deploy verification retry the local
  HTTPS/SNI probe with configurable timeouts, so transient TLS/listener readiness
  lag does not fail an otherwise healthy deploy.
- Added `resume_thread_metadata_key` to `effect:agent:enqueue-prompt` so queued
  prompts can resolve and persist per-target agent thread IDs without duplicating
  enqueue nodes in Teams workflow graphs.
- Added a comprehensive Admin UI user manual with task-oriented procedures and
  screenshots covering the shared shell, dashboard, agents, approvals,
  artifacts, skills, VMs, issues, sessions, tool calls, configuration, reports,
  users, workers, workflow editing, and common operator workflows.
- Added Admin Web agent prompt actions for operators to send prompts to VM and
  container-backed agents, including durable queued retry responses and
  container execution tracking for later thread resumption.
- Added a structured workflow editor form for agent/VM creation
  `tool_argument_policies`, including field-level validation for tool rules and
  constraints while preserving raw JSON editing for advanced or future policy
  shapes.

## 2026-05-18 - Pull Request Workflow Nodes

Touched areas: workflow providers, PR service layer, Admin Web catalog, docs,
tests.

- Added `query:pull-request:list` and `effect:pull-request:update` for
  provider-shaped pull request automation, with Azure DevOps support for list
  criteria, client-side PR tag filters, tag add/remove, metadata updates, and
  status guards.

## 2026-05-18 - Workflow Run Inspect Mode

Touched areas: `frontend/workflow-editor/`.

- Changed workflow run inspection into a read-only canvas mode that renders the
  selected run's visited graph path, shows failed-run unvisited nodes as
  transparent ghosts, and opens node-level run inputs/outputs instead of the
  workflow node editor.

## 2026-05-17 - Admin Chat Assistant Session Prewarm

Touched areas: Admin Web chat routes, assistant workflow tests, docs.

- Prewarmed durable assistant session resolution when users open or reset the
  Admin Web assistant chat, so first-time users get the same backend-aware
  assistant target setup that existing users already reuse before their first
  message is posted.
- Added regressions for read-only/full-access capability handling, hidden chat
  workflow publication, resume-thread propagation, and chat-thread metadata
  updates.

## 2026-05-15 - Workflow Editor Alignment Snapping

Touched areas: `frontend/workflow-editor/`.

- Increased the workflow canvas alignment snap threshold and made dropped nodes
  persist the snapped coordinates, so layouts stay aligned after save/reload
  instead of only showing temporary guide lines while dragging.

## 2026-05-14 - Workflow Editor Node Palette Grouping

Touched areas: `frontend/workflow-editor/`.

- Grouped workflow node types within the existing Triggers, Gates, Effects, and
  Queries create sections by related functionality, with namespace-based
  fallback grouping for future catalog entries.

## 2026-05-14 - Agent Artifact Text Query

Touched areas: workflow query providers, Admin Web catalog, worker API,
VM/container bootstrap, docs, tests.

- Added `query:agent:artifact-text`, which reads a UTF-8 text file from an
  agent session's artifacts folder by `session_id` and relative `path`.
- Standardized `/artifacts` as the agent-visible artifacts folder for both
  container workers and VM guests. Container workers expose a session artifact
  read endpoint; VM setup creates an agent-writable `/artifacts` directory and
  exports `AGENT_SANDBOX_ARTIFACTS_DIR`.

## 2026-05-06 - Queued Prompt Thread ID Handling

Touched areas: `src/agent_sandbox/effects/providers/lifecycle_hooks.py`,
effect orchestrator tests.

- Stopped queued libvirt `effect:agent:enqueue-prompt` dispatch from publishing
  synthetic dispatch IDs as Codex `thread_id` outputs when `resume_agent`
  succeeds without returning a real thread id. The queued dispatch path now
  omits `thread_id` in that case, allowing downstream fallback bindings to keep
  using existing session metadata instead of persisting a non-existent Codex
  thread.

## 2026-05-06 - Azure PR Review Publish Failure Handling

Touched areas: `src/agent_sandbox/services.py`, PR publish tests.

- Changed Azure DevOps `submit_pr_review` publishing to post inline comments
  before the summary comment, so invalid inline anchors no longer leave behind a
  successful summary thread when the inline comments fail. Azure PR publish
  errors now include extracted provider response details plus the failed inline
  comment index/path/line and are logged with the same context for diagnostics.

## 2026-05-06 - Workflow Editor Node Creation UX

Touched areas: `frontend/workflow-editor/`.

- Improved workflow node creation in the Admin Web workflow editor by sorting
  node type lists alphabetically, allowing drag-and-drop from the create list to
  the canvas, and placing click-created nodes at the current canvas center
  (`8c137cd1`).

## 2026-05-06 - Agent Prompt Lifecycle Ports

Touched areas: `src/agent_sandbox/effects/`, `src/agent_sandbox/workflow/`,
`frontend/workflow-editor/`, workflow pack config, orchestration tests.

- Added named `started` and `completed` lifecycle ports to
  `effect:agent:enqueue-prompt`, matching the existing `effect:agent:prompt`
  contract. The runtime now emits `started` after dispatch and `completed` when
  the queued agent run reconciles; legacy unported enqueue edges migrate to
  `started` (`095e0604`).

## 2026-05-03 to 2026-05-04 - Worker Isolation, Package Cache, and Private Git

Touched areas: `ansible/`, `src/agent_sandbox/worker/`,
`docs/container-worker-backend.md`, worker deployment tests.

- Added a control-plane apt relay and worker apt proxy path so worker hosts can
  install packages through managed infrastructure instead of arbitrary egress
  (`2b1db362`).
- Routed worker repository checkout and Git host resolution through the control
  plane, including Azure container-worker private Git access (`f7dcc37e`,
  `10a59da9`, `49519f30`, `5ba91722`).
- Added Nexus package-cache support for worker Python installs, anonymous package
  reads, package asset preflight checks, and Nexus EULA handling (`73d88ba4`,
  `3d2faed7`, `76f86a8e`, `ed8ad243`, `3b26dbb3`).
- Moved container agents to a home-root layout and seeded a Node toolchain for
  runner containers (`e1050db0`, `0680fb85`).
- Fixed assistant prompt thread resumption and targeted Teams prompt reads by
  trigger source (`1b23accf`, `9274c977`).
- Added worker user modeling and daemon coverage (`622bd2b8`).

## 2026-05-01 to 2026-05-02 - Admin Chat Assistant and Relay Hardening

Touched areas: `src/agent_sandbox/admin/`, `src/agent_sandbox/effects/`,
`frontend/workflow-editor/`, `docs/execution-backends.md`, relay and policy
tests.

- Added the Admin Web chat launcher, chat session/thread APIs, and backend-aware
  chat assistant orchestration (`017e1315`, `606a404f`).
- Added durable chat assistant session resolution by backend/user and a shared
  prompt enqueue adapter for chat routing metadata (`ebf4713f`, `2a2d7749`).
- Added assistant capability profiles, deny-by-default tool gating, persisted
  assistant settings, rollout gates, mutation guardrails, and audit metadata
  (`d537fc0d`, `d0b8f232`, `4aac2772`).
- Hid and protected the system-managed chat assistant workflow from normal
  workflow editing (`b0a09993`).
- Exposed assistant settings in Admin Web and fixed chat assistant privilege and
  queue handling (`fbee2911`, `5aa42992`).
- Added orchestration observability markers and runbooks, then continued relay
  work across backend and deployment paths (`7ebe8c21`, `803184be`).

## 2026-04-29 to 2026-04-30 - Container Agents, Workflow Packs, and Agents UI

Touched areas: `src/agent_sandbox/execution/`, `src/agent_sandbox/worker/`,
`frontend/workflow-editor/`, `config/workflow-packs/`, `ansible/`, container
worker tests.

- Refactored runtime modules and service entry points, and removed local path
  assumptions from docs and tests (`0551cb62`, `013e75af`).
- Added Teams meeting ingest support and fixed Azure worker Ansible deployment
  behavior (`fd439ed7`, `342740fe`).
- Added container-worker agent workflow support and updated workflow pack config
  examples for the container execution model (`26df2d2f`, `e7bd2ad9`,
  `3054fb57`).
- Scoped workflow editor layouts to packs and added agent admin workflow support
  in the frontend and backend (`dc8c9b1b`, `7d176f98`).
- Added create, update, and delete operations on the Agents page and made agent
  listings backend-aware (`93c8db3c`, `ffbf8c4c`).
- Preserved worker config during Azure deploys and made worker config files
  operator-local (`46bddbd6`, `3dc8a092`).
- Fixed container worker admin config handling, prompt enqueue container paths,
  and worker container network mode (`fc3eb229`, `c8d93660`, `d6a24b92`).
- Added the runner image build path and hardened container prompt dispatch
  (`e98acc13`).

## 2026-04-25 to 2026-04-28 - Container Worker Backend and Deployment Hardening

Touched areas: `src/agent_sandbox/execution/`, `src/agent_sandbox/worker/`,
`ansible/`, `scripts/`, communication target config, container worker tests.

- Added the execution backend abstraction and the first container worker daemon
  backend (`f321f1b6`, `582caca5`).
- Added explicit inline PR comments and split the worker daemon into focused
  modules (`7d44a0d3`, `24721688`).
- Added the container worker execution backend, CLI surface, execution spec
  fields, and sandbox profiles (`b211b3f2`, `d31ba5fa`, `fb0686f6`,
  `6b257928`).
- Added communication target config models and runtime validation for workflow
  routing (`4284f813`).
- Cleaned up container worker config and deployment updates, hardened Azure
  Ansible control-plane deploys, and respected preflight warning severity
  (`11d6f1ff`, `3ed7c6bd`, `bf5c64c2`).
- Allowed the ADO worker to idle cleanly without configured directories and
  deployed Admin Web assets through Ansible (`7cef91f7`, `61cd8315`).
- Reset generated deploy files before Ansible checkout and removed accidental
  secrets/tmp artifacts (`efdb8399`, `1fb5cd3c`, `d15bb8ed`, `e24d2a90`).
- Fixed worker provisioning and review regressions (`d149abbc`).

## 2026-04-21 to 2026-04-23 - Admin Deployment, VM Abstraction, and Repository Semantics

Touched areas: `src/agent_sandbox/admin/`, `src/agent_sandbox/repositories/`,
`src/agent_sandbox/vm/`, `frontend/workflow-editor/`, `ansible/`, admin tests.

- Split issue VM repository bootstrap into discrete workflow steps (`c61b1764`).
- Expanded and polished the Admin Web UI, issue views, wake worker support,
  branding, and login experience (`3637207a`, `45c9617a`, `49e73288`,
  `4da5c441`).
- Added Ansible host deployment playbooks and role task definitions, including
  Azure SSO support and managed Admin Web deployment (`068c57f6`, `0769c817`,
  `c844d43d`, `7dc167a3`).
- Added a VM provider abstraction and updated the workflow editor around provider
  selection (`bccd4622`).
- Introduced repository interfaces for PR feedback, sessions, directories,
  cursors, triggers, orchestration executions, review state, wake requests,
  script run requests, and cursors (`6d385232`, `e884be7f`, `05077470`,
  `50550650`, `4919efb8`, `a2425f29`, `f47d7872`, `e867d85c`).
- Refactored runtime access around repository/domain semantics and completed
  session repository adoption (`897d7c15`, `f713835c`, `898621e1`,
  `6a49dc8b`, `0fcafccc`).
- Tightened `request_pr_review` provider schema and hardened review repo prep
  against stale refs (`1f97d3cb`, `5982a610`).

## 2026-04-19 to 2026-04-20 - Workflow Pack Architecture and Live Reload

Touched areas: `src/agent_sandbox/workflow/`, `src/agent_sandbox/effects/`,
`src/agent_sandbox/triggers/`, workflow pack config, orchestration tests.

- Switched workflow editor runtime updates to SSE and made workflow run updates
  event-driven (`1c3483ec`, `f82c258a`).
- Added a shared workflow config reloader, per-cycle config snapshots, graph
  revision handling, atomic editor saves, and revision metadata (`d3ffaf2f`,
  `00fff15d`, `5e643a8f`, `df34c041`).
- Added directory-specific ticket routing maps and carried ticket payloads into
  PR creation metadata (`42519de0`, `856317fe`, `8bed2be7`, `59e146e1`).
- Added mixed-scope Teams settings and reorganized Admin Web configuration tabs
  into user-facing groups (`4d8c1823`, `08ad9eb5`).
- Added gate provider registry support and an await-agent gate provider
  (`be0c3c96`).
- Introduced workflow IR packs, composition loading, provider-owned schemas,
  registry-based node execution, compiler extraction, schema materialization,
  protocol-backed runtime context, plugin/module loading, provider profiles, and
  effect lifecycle hooks (`8e963ef4`, `9e823bab`, `5287fe27`, `d73a99dc`,
  `c4dc5b31`, `a9034b1e`, `83dad124`, `cae7fe45`, `07225d5e`).
- Decoupled core graph validation, event contracts, orchestration provider
  wiring, admin service responsibilities, and provider capability checks
  (`bc59514d`, `67c5867f`, `a0ca70f3`, `8a3742da`, `651fbb0d`).
- Fixed workflow editor pack-backed configs, effect orchestrator startup, ADO PR
  feedback trigger loading, and chained workflow binding fallbacks (`bc09d44a`,
  `c48be93c`, `dbe273cb`, `c4b578ee`).

## 2026-04-11 to 2026-04-18 - Teams, ADO Backlog, Agent Prompts, and Reactions

Touched areas: `src/agent_sandbox/gateway.py`, `src/agent_sandbox/services.py`,
`src/agent_sandbox/models.py`, Teams/ADO tests, prompt store, frontend/admin
integration.

- Added Teams ingest workflow support, Teams chat support, Teams message limits,
  delegated Teams auth, and Teams status handling fixes (`f3d2f906`,
  `2b843575`, `d183d35d`, `27ad9f31`, `313a0f19`, `32230153`).
- Added ADO backlog and issue-creation gateway tooling, including enum-backed
  policy gates, parent relation patching, current-iteration queries, backlog item
  detail, and provider-routed creation (`1a24b25f`, `011dbcc4`, `7d8c677d`,
  `08ba9b6d`, `3f3d6d68`, `8ae718f4`, `df9ddebe`).
- Added PR review request primitives and pull request list/get MCP tools with
  Azure DevOps support (`47f8c27b`, `b2dc884f`, `6a12682c`).
- Added prompt-agent request/response models, durable prompt store, runtime
  wiring, policy/audit controls, signed one-time response tokens, and default
  session permissions (`f2e083a0`, `f9f23a40`, `00ea2a1b`, `5dcc2158`,
  `3906a56b`, `bde9ce04`).
- Fixed PR repo access, ADO URL normalization, agent prompt retry handling,
  branch promotion, and PR creation wiring (`45a79149`, `c11cd9d0`,
  `464fa860`).
- Added agent prompt trigger support (`89656fa0`).
- Added Discord and Teams reaction adapter methods, best-effort read reactions,
  Teams markdown-to-HTML status rendering, configurable read reaction emoji, and
  focused reaction tests (`e14eca89`, `081e992c`, `4f7853aa`, `9188abb7`,
  `b917cb8`, `d9ab7bc8`, `0fbc5e10`).
- Updated admin controls and workflow orchestration (`1f611b29`).

## 2026-04-03 to 2026-04-09 - Trigger/Effect Orchestration and Workflow Editor

Touched areas: `src/agent_sandbox/triggers/`, `src/agent_sandbox/effects/`,
`src/agent_sandbox/workflow/`, `frontend/workflow-editor/`, orchestration docs
and conformance tests.

- Added discriminated trigger models, event envelopes, trigger provider
  protocols, registry/factory wiring, durable trigger event queues, concrete
  provider modules, CLI/systemd worker wiring, and post-receive Git push trigger
  helpers (`126bd197`, `438741cd`, `4647659d`, `4179b0f1`, `1253589b`,
  `66f2b872`, `c26077b2`).
- Added trigger config mutation APIs, trigger tests/docs, and trigger spec
  conformance validation (`265e047e`, `b48da08c`, `3d72e670`).
- Fixed trigger orchestrator regressions and removed a temporary
  `triggers.models` compatibility shim (`deb59cae`, `cb5512a7`).
- Added deterministic effect config binding, an effect provider framework,
  non-blocking orchestration with async completion reaping, graph validation,
  effect conformance coverage, gates, workflow VM effects, and completion
  routing (`1c1fc02f`, `e1e41a92`, `686771b9`, `ba048ddc`, `de80b07e`,
  `8234e5d7`, `69a58037`).
- Fixed async effect acknowledgements and Git push queue races (`76ec8119`).
- Added the workflow editor web app, improved node interactions, switched
  bindings to node-scoped references, added query nodes and VM run-state query
  support, and propagated workflow trace IDs (`072b2905`, `b1bd69b4`,
  `ea2bf251`, `a25dd4e0`, `15358136`).
- Added schema-driven workflow editor improvements, issue planning and loop gate
  groundwork, await-agent defaults, draft save, and copy/paste (`a35c902e`,
  `ce758901`, `453bcaa7`, `e13d3969`, `8dc96b7c`).

## 2026-03-31 to 2026-04-02 - Scheduled Wake, Provider Models, and Copilot Provider

Touched areas: `src/agent_sandbox/gateway.py`, `src/agent_sandbox/runtime.py`,
`src/agent_sandbox/admin/`, `scripts/`, model and heartbeat tests.

- Added scheduled resume gateway models, durable wake request storage, heartbeat
  dispatch, guardrail config, and wake schedule CRUD across CLI/AdminService
  (`d5099786`, `7c2bc179`, `f83bb77b`, `81a5b36b`, `d90090ce`).
- Removed speculative scheduled-wake compatibility fallback and fixed wake resume
  guardrails (`49185d2b`, `f048f48c`).
- Added session and issue setup default model fields, resolved session models in
  setup/resume scripts, and required explicit `AGENT_MODEL` for session-bound
  runs (`4ac14041`, `e745c569`, `0d44c7f2`).
- Selected VM assets by agent provider and added GitHub Copilot CLI provider
  bootstrap with host-mediated auth relay and session authorization controls
  (`3abac098`, `4b3cf319`, `9f1c2473`, `2b6f7f5e`).
- Added per-session model controls in CLI and Admin Web, default model migration
  coverage, and session editor fixes (`c2c1074b`, `4f790c05`, `5d9e9a22`,
  `4c6c51a8`, `fbaaf53b`).
- Added per-session PR automation overrides and fixed Azure DevOps heartbeat auth
  handling, VM target resolution, MCP lifespan startup, and issue orchestrator
  model wake environment (`96a4938a`, `e26abab4`, `bdbb886f`, `d0e751b6`,
  `fd44298a`).

## 2026-03-28 to 2026-03-30 - Directory Config, Local Issues, Approvals, and Ingest

Touched areas: `src/agent_sandbox/admin/`, `src/agent_sandbox/runtime.py`,
`src/agent_sandbox/gateway.py`, issue services, worker config, Teams/Discord
ingest workers.

- Added directory-scoped ADO worker and issue setup domains, worker config panels,
  directory-aware polling, scoped ADO repo sources, and secret propagation fixes
  (`8ebb63fe`, `b2415e97`, `5681877a`, `309266e3`, `0474217e`).
- Added unified local issue identity, remote metadata schema, ADO work-item
  upserts, proxy-aware issue service integration, unified issue APIs, safe schema
  migration, scoped issue IDs, and YAML viewing (`33e94aeb`, `26416845`,
  `61318017`, `465721e0`, `a05f3115`, `69744944`, `2f0e8910`).
- Added canonical issue states, local issue editing in Admin Web, issue creation
  and approval/rejection MCP tools, list/get issue tools, reason fields, detail
  views, YAML synchronization, and issue-directory preservation (`6368a6f2`,
  `752f7694`, `a9949d83`, `3de73e15`, `16ad1ba3`, `67ab050f`, `c8015cf4`,
  `e1699ef3`, `41a5a28b`).
- Added standalone VM setup, issue orchestrator workflow support, VM agent
  fixes, session tool management controls, and gateway tools for committing
  issues plus VM status/log inspection (`c092f864`, `386615c8`, `126b542d`,
  `5263fadd`).
- Hardened MCP transport handling, relay websocket proxying, JSON store atomic
  mutations, ambiguous issue ID rejection, failed-session cleanup, and issue push
  automation (`fc7040b5`, `006ec470`, `50f7b86d`, `a8fa6498`, `02130143`,
  `3884c91c`, `bc6b9f3c`).
- Added an approval inbox workflow, directory-configured draft ADO PRs, issue
  filtering, approval review security fixes, and block scalar preservation
  (`1dc10e10`, `fe5ec3b2`, `9f728ca5`, `3cb3b561`, `a7a44a0b`).
- Added Teams Graph ingestion, Discord ingest worker and inbox routing, Teams
  pagination/session-scope fixes, outbound Teams status tooling, and
  directory-scoped agent inspection (`6d879623`, `c3f0a4b9`, `578df485`,
  `00cb1d03`, `1d553b21`).

## 2026-03-24 to 2026-03-27 - Heartbeat, Admin UI, and Directory Foundations

Touched areas: `scripts/`, `src/agent_sandbox/admin/`,
`src/agent_sandbox/cli.py`, `src/agent_sandbox/runtime.py`, `docs/`, `systemd/`.

- Added the host heartbeat worker loop, systemd wiring, service env overrides,
  wake-idle-session behavior, audit/status summaries, observe mode, retry
  hardening, and stale running-state reconciliation (`1632c956`, `800698ee`,
  `923fed6a`, `e135a723`, `206fa516`, `60041806`, `23330531`).
- Removed or gated legacy guest heartbeat behavior and migrated docs toward the
  host-worker model (`71f764b9`, `5816f6ab`).
- Added provider-agnostic agent runner scripts and provider-neutral agent run
  artifacts, removed legacy `codex-*` status names, and made cloud-init
  provider-neutral for guest config (`dd8b8d60`, `3d50acac`, `a35860c2`,
  `b6fc4b10`, `417e5e0a`).
- Improved VM setup/status/log scripts, inactive VM listing, agent state controls,
  host-key handling, cleanup commands, and auth feedback polling (`85b53cb5`,
  `f20cc778`, `e22cf2f7`, `f4b0528e`, `e6efe811`, `5299e967`, `b4d4e9e0`).
- Added a desktop/admin console, worker status controls, async Admin GUI refresh,
  session management commands, session tool permission commands, and full PR
  feedback history tooling (`944a86a3`, `8f3c44d9`, `3c6ff60e`, `e3aa98ee`,
  `a3ac1d69`, `381ab2ac`, `a04af79c`).
- Introduced directory metadata models, durable directory store, config overlay
  resolver, directory CLI/AdminService layers, ownership metadata, ownership
  queries, direct-ownership admin views, worker shadow context resolution, rollout
  flags, and scoped-config docs/tests (`513e5807`, `9c5bf6d5`, `2156074e`,
  `7c4f9ed9`, `da0af5b2`, `1746021a`, `f07086b5`).

## 2026-03-21 to 2026-03-23 - Issue VM Automation, ADO Worker, Email, and PR Feedback

Touched areas: `scripts/`, `src/agent_sandbox/ado_issue_worker.py`,
`src/agent_sandbox/email/`, `src/agent_sandbox/gateway.py`, Git automation docs,
ADO/PR feedback tests.

- Refactored email into provider abstractions with mailbox routing, mailbox and
  folder policy controls, Gmail and Microsoft Graph providers, durable cursors,
  shared retry behavior, token/config validation, and nested audit redaction
  (`338c9a37`, `9f2c1187`, `ea95a0a4`, `d53d2986`, `2d326217`, `3d89c273`,
  `ee6dbada`).
- Added guest bootstrap, relay, NuGet, Git remote, push-agent, promotion, PR
  creation, shared cache, smoke-test, and helper script tooling (`be456db5`).
- Enhanced issue VM setup with SSH waiting, automated agent startup, status/log
  helpers, configurable repo sources, mirror freshness controls, optional
  bootstraps, Playwright/browser/MSSQL support, and guest helper fixes
  (`6cedf065`, `3a4c5977`, `de2e292e`, `d28ccaad`, `95a2d719`, `65629c0d`,
  `cc0e678e`, `4d2ce087`, `22fc3a95`, `64a45ae7`).
- Added ADO issue worker polling, deterministic task YAML serialization,
  persistent processed-item state, setup execution, CLI/systemd integration,
  eligibility tests, work item comments, acceptance criteria, and unprocessed
  work-item helper (`4e658188`, `5b4549ff`, `20cbd7d8`, `15b78a65`,
  `b3ad45ff`, `62fd964e`, `70c440a2`, `9b67de74`, `4d2ce087`).
- Added PR feedback worker support for GitHub/Azure, gateway read tools, reply
  API with provider posting and idempotency, heartbeat/reply regression coverage,
  and unread feedback handling (`e61a979b`, `52efbddf`, `2f09142a`,
  `6f5911bb`, `de20ebf7`).
- Added and hardened agent push promotion/PR creation: structured PR output,
  route maps, fixed PR body templates, PR notes, unique branch suffixes, Azure PR
  descriptions/titles, and git-note namespacing (`c81bc272`, `ba8ce00d`,
  `a10bfb37`, `c847b4bb`, `99b23224`, `dfd51d21`, `013ff5c6`, `c25f66db`).
- Added relay stream diagnostics, gateway URL/CIDR validation, systemd installer
  preflight, CLI status JSON output, remote source resolution fixes, and push
  automation hardening (`f9f5ab9d`, `d93b839c`, `788b4bfd`, `e9dea7d3`,
  `5b7d56a8`, `8b9f969c`).

## 2026-03-20 - Initial Platform, Discord, and Core Relays

Touched areas: `src/agent_sandbox/`, `scripts/`, `config/`, `systemd/`,
`README.md`, `docs/`, bootstrap and relay tests.

- Created the initial Agent Sandbox scaffold: config, runtime storage, audit,
  policy, MCP gateway, OpenAI relay, firewall/systemd templates, VM lifecycle
  scripts, cloud-init, Git remote helpers, docs, and tests (`b53b28ed`).
- Added VM bootstrap improvements, script help coverage, optional .NET and
  Playwright bootstraps, and operations docs (`f0cac234`).
- Added a selectable Discord backend with bot API runtime wiring, token config
  hardening, command-source policy checks, adapter tests, cursor handling,
  explicit poll targets, adapter failure auditing, bounded retries, and
  ingestion boundary docs (`e559b750`, `848b4e17`, `58a4dd90`, `6c23a9b0`,
  `b35c2b2f`, `d6add3fd`, `8457fffa`, `fb4b7ac0`, `0bb196f7`, `7281c552`,
  `0251930d`, `abd26579`).
- Added the host-side NuGet relay, VM NuGet bootstrap support, firewall default
  updates, systemd template, docs, and tests (`4d4ffa50`).

# Changelog

## 2026-07-16 - Bokio Connector Evidence Association

- Added durable, content-deduplicated Bokio evidence records imported from
  session-owned connector or communication artifacts, preserving source provenance
  without exposing host paths or credentials.
- Added agent tools to inspect, stage, and attach retained receipt/invoice
  evidence to existing Bokio journal entries, or create a journal entry and
  attach evidence in one tracked operation.

## 2026-07-16 - Directory-Scoped Agent Connectors

- Added named, directory-scoped connectors with inherited configuration, exact per-session
  operation grants, JSON Schema input validation, explicit approval gates, and connector-aware
  audit records.
- Added host-authenticated REST and confined filesystem drivers, including bounded responses,
  redirect controls, artifact-backed file retrieval, static environment-secret profiles, and
  cached OAuth 2 client-credentials tokens.
- Added `list_connectors` and `call_connector` MCP tools plus Admin Web, Admin API, and CLI
  surfaces for configuration, auth readiness, health checks, agent/session grants, and add-on
  connector-driver registration.

## 2026-07-16 - Expanded Architectural Design History

- Expanded the illustrated manual's design-history chapter from a compressed chronology into a
  source-backed account of the pressures, invariants, and compatibility consequences behind
  directory ownership, typed workflows, execution backends, selective SQLite adoption, add-ons,
  Team command transactions, focused Admin composition, proof-bearing authority, and managed Git.
- Added plain-language turning-point sidebars, a preserved-invariants narrative, an exact
  chronology, compatibility-direction and fail-closed tables, and a revised evolution diagram;
  corrected incremental repository/backend claims and the current `runtime/pr-map.json` role.

## 2026-07-14 - Scoped Release And Relay Integrity Fixes

- Failed closed when directory-scoped managed-Git credential configuration cannot be resolved,
  revalidated reviewer/profile scope on release-gate retry, and reset and cleaned replayed release
  verifier checkouts before executing frozen commands.
- Preserved exact top-level local OpenAI tool calls ahead of namespace inference and classified SSH
  and container execution timeouts as timed-out release verification runs.
- Preserved upgrades from ADO worker configurations containing retired automatic-PR settings and
  made the managed-Git operation timeout configurable through host and directory provider config.

## 2026-07-13 - Managed PR And Immutable Recovery Hardening

- Kept consultation snapshot fetches inside the scoped read-credential lifecycle and rejected
  sibling directory overrides across generic and Team managed-Git publication effects.
- Restored merge-required PR preflight results, PR map/session feedback bindings, Azure work-item
  links, browser URLs, bounded descriptions, and metadata refresh when reusing pull requests.
- Preserved explicit standalone PR destination branches, aligned conflict bindings with the
  `destination_branch` output contract, and based verification retries on frozen release commands.
- Validated default-branch overrides before persisting refreshed repository metadata so failed
  registrations cannot poison later managed-Git refreshes.
- Froze release-review criteria when each gate is issued and routed consultation setup failures
  through snapshot cleanup so failed requests cannot bypass gates or retain pins indefinitely.
- Rejected unsupported PR providers and missing tracking sessions before pushing branches or
  creating provider pull requests.

## 2026-07-13 - Illustrated Evander And Team Technical Manual

- Replaced the former operator-manual draft set with one 30-chapter canonical source covering
  core Evander and the Team Add-on from first principles through architecture, operations,
  troubleshooting, extension, glossary, and a generated linked subject index.
- Added source-controlled Mermaid diagrams and deterministic sanitized dark-mode screenshots,
  with one repeatable build that publishes matching Markdown, self-contained HTML, PDF, and DOCX
  editions plus a source/output provenance record.
- Added publication-integrity checks tied to the implemented service, firewall, Admin navigation,
  Team navigation, current CLI, screenshot, and diagram inventories, and corrected stale trigger,
  Team role, Agent Mail, chat-tool, and wake-eligibility documentation found during source review.
- Recast every chapter's At a glance passage as a plain-language introduction and layered the
  end-to-end chapter with optional technical and administrator sidebars, keeping the complete
  implementation detail without forcing it into the first-read narrative. Core chapters now use
  functional agent labels, reserving the named Northstar cast for Team Add-on relationships where
  stable identities carry useful meaning.
- Reframed the trust-model chapter around three operator-facing security promises, everyday
  examples, and a practical incident handoff, while retaining protocol, network, session, Git,
  worker, approval, and host-response mechanics in clearly labeled reference sidebars. Concise
  distributed-systems and security terms now replace repeated paraphrases where useful, with
  brief footnotes plus matching glossary and subject-index entries.
- Extended that layered reading model through the remaining core, Team, engineering, history,
  reference, and troubleshooting chapters: plain orientation and concrete examples now lead into
  optional deeper detail, operator handoffs, and clearly labeled administrator runbooks, while
  preserving the full commands, invariants, failure behavior, storage contracts, and source trails.

## 2026-07-13 - Documentation Accuracy And Drift Checks

- Refreshed operator, architecture, backend, add-on, Admin Web, and user-manual documentation
  against the current implementation.
- Classified historical design material and managed upstream documentation so readers can
  distinguish current references from retained planning records.
- Added automated local-link, anchor, source-inventory, navigation, workflow-gate, and manual
  synchronization checks to prevent future documentation drift.

## 2026-07-13 - Scoped Git And Release Gate Hardening

- Reauthorized managed-repository overrides against the repository owner and preserved the owning
  directory through Team project Git operations, including child-scoped worker clones.
- Executed release verification and review gates from the frozen candidate manifest, supported
  verification-only policies, and kept retries blocked while another current review still blocks.
- Resolved consultation credentials from the worker directory and made incomplete failed cleanup
  retryable so leaked workers cannot permanently consume consultation capacity.
- Treated verifier deletion results that report failure as infrastructure failures instead of
  advancing a release while disposable resources remain allocated.

## 2026-07-13 - Team Release Gate Retry Semantics

- Classified release verification outcomes as candidate, infrastructure, or
  timeout failures using container exit metadata, making nonzero build/test
  exits terminal while leaving only transient execution failures retryable.
- Added immutable verification and release-review attempt lineage, typed gate
  failures, frozen candidate evidence manifests, and rejection of unchanged
  content after a candidate or release-review failure.
- Added an audited `team_retry_release_gate` MCP command plus Admin API and UI
  actions for eligible retries against the unchanged pinned candidate SHA,
  persisting the reason and requesting agent/operator identity on the successor.
- Migrated legacy unpinned nonterminal releases to rejected state and persisted
  review lineage and gate-failure details in the Team SQLite store.

## 2026-07-13 - Request-Scoped Codebase Consultations

- Added generic MCP tools for bounded codebase consultation, polling, cancellation, and structured
  result submission without requiring a persistent junior agent or Team Add-on identity.
- Added named container-only model profiles, per-directory/profile admission control, bounded
  synchronous waits, durable SQLite state, one-time worker-bound result tokens, and retention.
- Pinned every request to an exact directory-scoped managed-Git commit under a temporary bound ref,
  cloned and fetched it with read-only Git access, detached the workspace at that commit, and
  validated cited paths and line ranges before accepting results.
- Added a hidden durable workflow for request-time agent/workspace materialization and guaranteed
  cleanup after completion, failure, timeout, or cancellation, releasing queue capacity only after
  cleanup.

## 2026-07-13 - Managed Bare Repositories And Ephemeral Worktrees

- Replaced issue-specific mirror/persistent-checkout routing with one directory-scoped managed
  bare repository per canonical upstream, addressed everywhere by deterministic `managed_repo_id`.
- Added the locked, atomically replaced managed-repository registry and privileged `git-repos`
  CLI/Admin lifecycle for registration, refresh, inspection, maintenance, guarded deletion, and
  explicit dry-run/apply/finalize migration.
- Made canonical refresh transactional while preserving quarantined agent, review, Team, notes,
  and operation refs, and enforced disjoint guest/host/upstream namespaces.
- Pinned exact agent commits for promotion, note lookup, Team consolidation, upstream publication,
  and PR creation; added compare-and-swap targets and explicit destination leases so stale events
  cannot publish or regress a newer branch.
- Moved merge and PR preparation into unique detached operation worktrees with hardened checkout
  settings, guaranteed cleanup, stale-process reconciliation, and no temporary paths or credentials
  in workflow output or persistent state.
- Converted VM, container-worker, workflow-pack, Team project/release, Admin, and UI contracts from
  checkout/bare paths to managed IDs; issue and session deletion now removes bindings and SSH
  authorization without deleting shared repositories or unpublished refs.
- Retired the path-based mirror/promotion helper flow as an operator interface and documented the
  queue-driven managed repository workflow, breaking migration, smoke acceptance, explicit
  retention, and manifest-scoped finalization.
- Hardened migration restartability with manual-worker lock detection, credential-safe diagnostics,
  strict read-only rollback artifacts, exact orphan-key bindings, and defensive SSH-key deletion
  checks.
- Revoked container-worker host and guest Git keys when a clone fails after provisioning, while
  retaining the authorization binding whenever complete revocation cannot be proven.

## 2026-07-10 - Team Release Evidence And Scope Gates

- Required every mandatory review-profile criterion to pass, carried rejected
  findings across rework deliverables, and limited finding resolution to the
  matching review profile or legacy reviewer role.
- Required audited lead, top-level manager, or authenticated-operator waivers
  for incomplete verification while making failed verification unwaivable.
- Pinned reviewed and feature commits, recorded reviewed change manifests, and
  hard-blocked moved source tips or release paths outside the reviewed scope.
- Added candidate-commit verification and release-review gates so canonical
  publication occurs only after the configured checks approve the same commit.
- Prepared deterministic task-specific branches and Git worktrees for persistent
  sessions, preventing unrelated task commits from leaking through one shared checkout.
- Ran release checks in a disposable container-worker session, added exact-SHA
  canonical push preflight, and failed closed for missing policies and legacy
  nonterminal unpinned releases.
- Exposed criteria, carried findings, waiver audit details, and scope diagnostics
  in the Team API and Delivery UI.

## 2026-07-10 - Team Agent Mail Archive

- Added a read-only Agent Mail view for browsing current and historical Team inbox
  correspondence with sender, recipient, direction, state, type, full message payload,
  search, and pagination filters.
- Added a paginated Team inbox-history API so handled correspondence remains available
  beyond the bounded state payload used by the operational views.
- Rendered historical `body` and failure `error` fields in the main Mail reader, with a
  generic formatted-payload fallback for less common system notice shapes.

## 2026-07-10 - Team Wake Feedback And Terminal Task Repair

- Included concise failed-action feedback in the wake prompt and marked it delivered only
  after the agent resume succeeds, preventing acknowledged failures from repeatedly waking
  an otherwise idle agent.
- Treated rejected tasks as terminal in both Team wake paths, matching task validation,
  project status, and the Team UI.

## 2026-07-10 - Team Chat Permission Handoff

- Kept reply-only Team Chat messages queued while their target session has an
  active agent prompt, preventing a queued chat dispatch from revoking the
  still-running implementation wake's Team MCP permissions.
- Also excluded busy sessions from regular Team wake leasing, preventing a
  redundant queued wake from delaying the pending chat handoff.

## 2026-07-10 - Team Chat Stale Revision Tolerance

- Let operators open Team chats and queue chat messages after unrelated Team state changes;
  chat still enforces the current agent status, conversation access, and privilege policy.
- Prevented queued Team chat dispatches from waking paused or stood-down agents, including
  messages queued before the wake blocker was applied.

## 2026-07-10 - Container Git Write Alias Repair

- Configured container-worker Git write keys under a dedicated SSH host alias
  and routed the `git push-agent` remote through it, leaving the clone/fetch
  alias bound to its read-only key instead of relying on duplicate OpenSSH
  host entries.

## 2026-07-10 - GPT-5.6 Pricing Catalog

- Added standard and batch token pricing for GPT-5.6 Sol, Terra, Luna, and the
  GPT-5.6 alias, effective from the start of the release day so existing usage
  events can be estimated retroactively.
- Accounted for GPT-5.6 cache-write tokens at their separate 1.25x input rate
  without also charging those tokens as ordinary input.
- Moved the active pricing catalog into operator-owned runtime state, with
  `init-state` seeding new runtimes from a tracked example without overwriting
  existing operator edits and migrating customized legacy catalogs.

## 2026-07-10 - Codex Unsupported Tool Failure Classification

- Added local OpenAI Responses compatibility for Codex MCP namespaces: namespace
  tools are exposed to local models as ordinary functions, then returned function
  calls are restored to Codex's namespace/name shape before routing.
- Normalized common local-model MCP separators in streamed and non-streamed tool
  calls, and restored a uniquely identifiable MCP namespace when a local model
  omitted it entirely, while leaving hosted OpenAI and Anthropic requests unchanged.
- Classified completed Codex runs with unsupported router calls as tool-interface
  failures before context-limit inference, preventing reduced-context retries from
  repeating an incompatible MCP/tool invocation loop.
- Preserved the rejected tool names in sanitized worker outcome metadata and
  surfaced the specific failure in reconstructed execution logs.
- Refreshed and persisted terminal container logs before classifying Codex outcomes
  so unsupported router calls cannot be mistaken for context-limit failures.
- Flushed buffered local-model SSE data when a tolerated upstream transport error
  ends the stream without a trailing newline.

## 2026-07-10 - Team Release And Wake Review Hardening

- Preserved trusted canonical origins and used detached temporary source refs
  when publishing bare-backed release branches.
- Made release recovery promotion-specific, synchronized recovered targets back
  to the bare mirror after SHA verification, and retained the current remaining
  failure when several promotions are blocked.
- Retired only blocked duplicate releases, handled their obsolete failure inbox
  items, ignored late publication callbacks for rejected releases, and preserved
  canonical push failure reason codes.
- Abandoned partial Git work contexts before retrying an exhausted wake-state
  conflict so a replacement dispatch cannot expose stale active contexts.

## 2026-07-10 - Team Wake Conflict Recovery

- Refreshed Team state after external repository preparation and retried
  idempotent Git work-context commits when concurrent state writes advance the
  revision during an immediate agent wake.
- Rescheduled exhausted transient wake conflicts without counting them as agent
  no-progress failures, while exposing conflict and retry diagnostics.
- Completed wake outbox rows after MCP wake events are durably scheduled so the
  immediate path does not leave orphan pending records for the periodic
  publisher.

## 2026-07-09 - Team Release Promotion Hardening

- Reused active Team release packages for duplicate release requests over the
  same deliverable set, returning the existing package instead of queuing
  another promotion, and retired older blocked duplicates once a replacement
  release published successfully.
- Allowed blocked releases to recover failed prerequisite feature promotions,
  clear the recovered blocker, and continue into release promotion.
- Ran Team Git consolidation from a temporary checkout when a bare repository is
  available, leaving the trusted host checkout untouched by release automation.
- Pushed isolated release refs to the canonical remote from a temporary checkout
  of the trusted bare mirror instead of requiring the ref in the host checkout,
  and carried the configured bare-repository selector into canonical promotion
  events while recording the actual pushed commit for new remote branches.
- Added release promotion failure reason codes and outbox attempt metadata to
  release payloads so blocked releases show whether they are waiting on stale
  locks, dirty checkouts, missing refs, or merge conflicts, while failed
  promotions whose task, deliverable, or release is already terminal remain
  historical instead of current runtime attention.
- Published debounced immediate-wake events for operator-created Team directives
  so managers do not wait for the periodic wake sweep to receive urgent work.
- Flushed Team outbox records after persistent agent dispatch finalization so
  review, recovery, and promotion workflows start without waiting for the cron.

## 2026-07-09 - Local Container Worker Toolchain Mounts

- Made guided same-host container-worker setup create the local worker
  toolchain cache directories and mount them read-only into worker containers so
  generated dotnet, Node, Playwright, browser, and NuGet profile paths resolve
  at runtime.

## 2026-07-09 - Team Late Review State Guard

- Reopened accepted deliverables and tasks when a new pending review request is
  added after acceptance, keeping late release-blocking reviews from being
  ignored by accepted state.
- Hardened reply-only Team Chat prompts so agents must state that no mutation
  occurred instead of promising or implying queued state changes.

## 2026-07-09 - Team Review Profile Wake Fixes

- Auto-selected a single active matching Team review profile when review
  requests omit `review_profile_id`, while reporting ambiguous profile matches
  instead of choosing arbitrarily.
- Preserved reviewer wake events from Team review-request commands so newly
  assigned reviewers are queued through the immediate wake outbox path.

## 2026-07-09 - Skill Delete Workflow Reference Guard

- Blocked deletion of managed Codex skills that are still referenced by workflow
  create-agent/create-VM effects, returning conflict details instead of silently
  changing workflow behavior.

## 2026-07-09 - Same-Host Worker Git Setup Repair

- Made guided same-host container-worker setup install and authorize the worker
  read-only Git key with repo-prefix metadata, including repair for legacy
  `agent-sandbox-git` key filenames.

## 2026-07-09 - Team Project Mirror Wake Prep

- Kept explicit Team project Git mirrors on local host-checkout refresh during
  container-worker wake prep, recording canonical upstream URLs as metadata
  without fetching the checkout's upstream remote.

## 2026-07-09 - Team Add-on Chat Mutation Guidance

- Clarified Team Chat wake prompts when mutations are allowed so agents do not
  treat stale prior reply-only transcript text as applying to the current
  dispatch.
- Refreshed Admin shell privilege state when add-on iframes load so an Admin Web
  restart cannot leave Team Chat looking unlocked after the server has reset
  privileged operations to locked.

## 2026-07-09 - Team Add-on Chat Notification Sound

- Added an optional built-in Team chat notification chime for newly received
  agent messages when the operator window is not focused, with a compact
  per-browser sound toggle and debounce.

## 2026-07-09 - Team Add-on Locked Chat Access

- Allowed the built-in Team chat client to start conversations and send
  messages while privileged operations are locked, while marking those queued
  chat wakes as reply-only so locked-mode chat cannot grant Team mutation tools.

## 2026-07-09 - Team Add-on Chat Scroll Stability

- Kept the built-in Team chat client anchored to the current thread while
  sending messages and while same-conversation refreshes arrive, avoiding
  transient jumps to the beginning of long chats.

## 2026-07-09 - ADO Activity Add-on

- Added the opt-in `ado-activity-addon`, with host-side MCP tools and workflow
  providers for syncing exhaustive Azure DevOps work-item, PR comment, and PR
  commit activity into a durable SQLite index.
- Added paged `query:ado:activity` and `query_ado_activity_index` surfaces for
  fast indexed weekly activity reads without rerunning the slow live ADO scans.

## 2026-07-08 - Configurable Shell Helper Secrets File

- Added `EVANDER_SECRETS_ENV_FILE` for host-side shell helpers, preserving
  repo-root `secrets.env` as the default while allowing deployments to point Git
  mirror refresh and PR helpers at another host-local secrets file.
- Stored configured secrets-file paths on bare mirrors as `evander.secretsEnvFile`
  so Git SSH forced commands can reload mirror-refresh credentials without
  per-agent key repair.
- Granted the configured Git SSH user least-privilege access to migrated local
  secrets files so stamped mirrors can refresh from private canonical remotes.
- Refused to serve stale Git mirror refs when `mirrorSyncRemote` requires a
  trusted checkout remote fetch and that fetch fails.
- Fixed PAT-backed Azure DevOps mirror refreshes so remote-name fetches rewrite
  the existing SSH remote to HTTPS instead of appending an unused second URL.

## 2026-07-08 - ADO Activity Attribution And Scan Bounds

- Corrected Azure DevOps work-item update activity attribution to prefer each
  revision's semantic `System.ChangedBy` and `System.ChangedDate` field values,
  while suppressing metadata-only update rows.
- Bounded default ADO work-item, PR comment, and PR commit activity scans to 50
  candidates with `max_candidates`/`exhaustive` controls and explicit partial
  scan metadata in paging responses.

## 2026-07-08 - Admin Web Configuration Permission Errors

- Returned explicit `admin.owner` permission errors for unlocked Admin Web
  configuration mutations when the user lacks owner access, instead of
  reporting that privileged operations are locked.

## 2026-07-07 - Container Worker Git Write Access

- Restored writable `git push-agent` support for container-worker agents by
  provisioning session-scoped Git SSH keys authorized only for the session's
  `refs/heads/agents/<session-id>/...` namespace.
- Kept read-only Git access available through `effect:agent:clone-repo`
  `git_access_mode: read-only`, while default container clone behavior now
  matches VM agents with writable agent-branch pushes.
- Authorized the session PR notes ref alongside the agent branch namespace so
  `git push-agent` can push both refs in one operation.
- Made read-only clone reruns remove prior container Git write-key authorization
  and scrub stale `push-agent` checkout configuration.
- Hardened the container Git write-key lifecycle so worker-managed SSH files
  reject guest-created symlinks, write-mode key rotation revokes the previous
  host `authorized_keys` entry, and destroy by execution/container target
  revokes write access before session metadata is deleted.
- Moved container Git write-key revocation into the shared Admin delete path and
  fixed dotted worker Git write-key names so temporary public keys are resolved
  by appending `.pub` to the generated private-key path.
- Rejected multiline container Git write public keys before composing host
  `authorized_keys` entries, and scrubbed stale worker write-key files before
  read-only clone reruns fetch from the control-plane mirror.
- Serialized host `authorized_keys` updates and rolled back newly installed
  write keys when session metadata persistence fails.

## 2026-07-07 - Container Worker Empty Codex Output Handling

- Failed container-worker Codex prompt jobs that emit only a session id and no
  agent message, preventing Teams wakeups from persisting unusable resume
  thread ids as successful responses.
- Wrote tokenized Codex relay config into the legacy container `CODEX_HOME`
  path used by older runner images so Teams wakeups authenticate correctly.
- Limited Codex JSONL fallback replay to events from the current invocation so
  resumed container sessions do not reuse stale assistant text from older runs.
- Treated Codex failure diagnostics as empty-response logs so failed empty turns
  do not persist unusable resume thread ids.

## 2026-07-06 - Container Worker Codex Relay Auth

- Added the MCP session token to container worker Codex OpenAI relay URLs so
  token-protected sessions authenticate with the relay even when Codex does not
  emit an OpenAI authorization header for the custom provider.
- Rewrote tokenized relay auth paths before routing and logging so MCP session
  tokens do not persist in request paths.
- Fixed the remaining container-worker regression assertion to expect the
  tokenized Codex relay URL produced during session bootstrap.

## 2026-07-06 - Team Memory Status Fixes

- Made explicit archived Team memory status filters include archived records in
  public search results.
- Cleared stale supersession links when a memory record is reactivated through
  record/update paths.

## 2026-07-06 - ADO Workflow Query Modes

- Added explicit `query_mode` support for Azure DevOps work-item workflow
  queries and triggers, distinguishing Structured filters from Manual WIQL
  while preserving legacy WIQL inference for existing configs.
- Updated the workflow editor catalog and inspector so ADO WIQL and structured
  filter fields are presented as separate modes instead of silently competing.
- Hid the inactive ADO WIQL or structured-filter fields for the selected query
  mode and pruned hidden structured-filter drafts when switching to Manual WIQL.

## 2026-07-06 - Team Add-on Memory

- Added canonical Team memory records in the Team SQLite state store with
  team/project/codebase/agent scoping, status, tags, source refs, artifact refs,
  supersession, and compact broad payloads that omit long bodies.
- Added Team MCP memory tools, Admin API memory routes, a compact Team Memory UI
  tab, and workflow nodes for compact memory query plus per-team Qdrant
  indexing/reindexing from canonical state.
- Kept normal Team wake prompts free of memory content; agents and workflows
  must use explicit memory search/query paths for recall.
- Restricted Team memory command updates, archives, and supersession links to
  memories visible to the calling agent.

## 2026-07-06 - Worker Git Mirror Scope Fix

- Authorized the shared container-worker Git SSH key for read-only access to the
  configured control-plane mirror root so runtime sessions can clone any
  allowed mirror while deploy checkout metadata stays on the deployment mirror.
- Passed that configured mirror root into the forced-command wrapper so custom
  worker Git roots do not fall back to the deployment checkout default.

## 2026-07-06 - Agent Prompt Dispatch Lock

- Serialized `effect:agent:enqueue-prompt` dispatch with a durable per-agent
  lock so direct and queued prompts cannot start concurrently before waiting
  execution state is visible.
- Routed Admin Web prompt-agent dispatches through the same durable lock,
  recorded libvirt direct dispatches as waiting executions for lock release,
  and keyed libvirt locks by VM so multi-session targets serialize correctly.

## 2026-07-06 - Agent Prompt Queue Race Fix

- Queued racing `effect:agent:enqueue-prompt` prompts when another prompt for
  the same agent is already running or waiting, preventing concurrent prompt
  delivery before VM inventory reflects the active run.

## 2026-07-06 - Admin Web Privileged Unlock Sync

- Synced the Admin Web Lock/Unlock header control with the backend privileged
  operation session state so workflow saves and other privileged mutations
  succeed after a confirmed unlock.
- Treat pending Admin Web Lock requests as locally locked before the backend
  responds so privileged UI actions close immediately when an operator locks.

## 2026-07-06 - Security Audit Remediation

- Passed repo/session metadata into Admin Web Git bootstrap calls, restricted learned Azure
  Artifacts feed GUIDs to configured service-index resource URLs, and returned controlled errors
  for malformed Admin Web session/approval directory filters, including built-in admin auth paths.
- Bound Admin Web webhook signatures to query parameters and allowed Azure Artifacts NuGet
  resource paths when the configured feed base URL is the service-index URL.
- Preserved existing `authorized_keys` ownership during Git bootstrap atomic replacement and
  resolved relative Azure NuGet package paths containing `_packaging` under the configured feed.
- Required token proof when MCP callers select an existing session over HTTP,
  while keeping direct in-process gateway calls compatible for trusted control-plane paths.
- Required privileged unlock before Team add-on GET cost requests can run transcript backfills.
- Rejected raw and encoded dot-segment escapes in the OpenAI, NuGet, and HTTP target relays,
  constrained NuGet Azure Packaging shortcuts to the configured feed scope, and limited the
  OpenAI relay to known OpenAI-compatible endpoints before forwarding host credentials.
- Hardened worker session file writes and artifact text reads with descriptor-based no-follow
  traversal so symlink swaps cannot escape session roots.
- Scoped Admin Web session and approval reads to authorized directories instead of exposing
  cross-directory session or approval data.
- Required timestamped webhook signatures over `timestamp.query.body` and rejected stale webhook
  deliveries to close replay windows.
- Documented the canonical query JSON component in signed workflow webhook HMAC examples,
  including the empty-query `{}` case.
- Included webhook query parameters in Admin Web webhook idempotency keys so same-body deliveries
  with distinct query envelopes are published separately.
- Enforced privileged unlock checks on Team add-on mutation routes.
- Restricted guest Git keys to their authorized bare repository for read operations and preserved
  the existing session branch write scope.
- Required read-only Git key bootstrap to carry authorized repository metadata and rejected
  unscoped read-only forced-command entries.
- Scoped the shared worker VM Git SSH key to read-only control-plane mirror-root access while
  keeping deploy checkout metadata on the generated deployment mirror.
- Allowed Azure Artifacts NuGet resource URLs that use feed GUID paths after the relay observes
  those IDs in the configured feed service index.
- Kept the workflow-editor setup build path compatible with apt-installed Node 18 by pinning Vite
  tooling to the Node 18-compatible major line.
- Validated guest Git public keys before rewriting `authorized_keys` so malformed keys cannot
  erase existing entries.
- Updated vulnerable workflow-editor development dependencies and lockfile entries.

## 2026-07-05 - Review Follow-up Security Fixes

- Blocked IPv4-mapped metadata IP addresses in workflow HTTP request destination checks.
- Scoped Admin Web approval decision and revoke authorization to the approval request directory.
- Scoped Admin Web VM, issue, agent, and directory-creation owner checks to the
  target directory so child-directory owners can mutate their own scope after
  unlock.
- Tried every vetted public DNS address for workflow HTTP requests so an
  unreachable first record does not block later approved addresses.
- Scoped Admin Web session and wake-schedule mutation owner checks to the
  target session directory.
- Required Admin Web VM actions to authorize every linked directory, preserved
  the ambiguous libvirt agent-delete error path, and kept orphaned wake
  schedules cleanable through their stored directory.

## 2026-07-05 - Tokenized Relay Security Fixes

- Stripped referrer headers from tokenized HTTP target relay requests before
  proxying to upstream targets.
- Pinned workflow HTTP request effects to an address approved by destination
  policy checks so later DNS rebinding cannot change the outbound connection.
- Updated Copilot, Codex, and Claude guest bootstraps to preserve MCP
  session-token auth on rebootstrap.

## 2026-07-05 - Relay Session Token Compatibility

- Generated tokenized agent model and HTTP target relay URLs that carry the
  MCP session token without forwarding it to upstream providers or targets.
- Resolved hostname and numeric HTTP request destinations before approving
  public-only workflow requests so DNS aliases to private or metadata
  addresses are blocked.

## 2026-07-05 - Workflow Effect Source Guards

- Blocked `effect:http:request` private, loopback, link-local, and metadata
  destinations by default, including URLs resolved from workflow bindings.
- Required bound HTTP request URLs to use explicit host-restricted allowlists
  and added an `allow_private_networks` opt-in for trusted private endpoints.
- Restricted copy-file `source_path` reads to repo deployment assets and
  runtime artifact stores by default, with `allowed_source_roots` for explicit
  additional host roots.

## 2026-07-05 - Git SSH Repo And Session Push Scope

- Bound writable guest Git SSH keys to one bare repo and one
  `refs/heads/agents/<session-id>/...` namespace, with read-only bootstrap
  fallback when repo/session metadata is unavailable.

## 2026-07-05 - VM Exec Environment Validation

- Rejected VM exec and guest environment variable names that are not strict
  shell identifiers before rendering shell export or cloud-init environment
  lines.

## 2026-07-05 - Admin Web Secret Placement

- Defaulted the Admin Web listener to localhost and documented keeping active
  Admin Web password/session secrets outside repo-local config files.
- Loaded `/etc/evander/local-secrets.env` from the Admin Web systemd unit so
  deployments can keep local secrets out of `config/admin-web.env`.

## 2026-07-05 - Admin Web Privileged Lock Enforcement

- Required the current Admin Web privileged unlock state before helper-gated
  backend routes accept privileged mutations.

## 2026-07-05 - Dashboard Directory Cache Scope

- Scoped Admin Web dashboard conditional-response payloads to the same request
  path as their ETags so directory switches cannot display stale data from a
  different directory after a `304 Not Modified` response.

## 2026-07-05 - Admin Web Review Fixes

- Derived artifact-space grant subjects from the authenticated Admin Web
  session instead of caller-supplied request fields.
- Rolled back grouped configuration tab writes when a later source domain
  rejects the save, preventing partial persistence on failed saves.

## 2026-07-05 - Admin Web Bundle and Team Stream Fixes

- Pointed the checked-in Admin Web dist index at the generated JavaScript
  bundle that exists under `dist/assets`.
- Kept revision-scoped Team Add-on state stream payloads stable between
  revisions so unchanged clients receive keepalives instead of snapshots.

## 2026-07-05 - Add-on Discovery and MSSQL Seed Guards

- Preserved already-imported bundled add-on module identities during manifest
  discovery so future imports and monkeypatches keep hitting the public package.
- Wrapped MSSQL seed worker provisioning failures in audited `ToolError`
  responses for both provision and reset tool calls.
- Treated missing Admin Web permission adapters as unavailable privileged
  contexts instead of raising `AttributeError`.

## 2026-07-04 - Team Add-on Payload Windowing

- Added server-side pagination for the Team Add-on timeline API and tab so the
  UI no longer downloads the full derived timeline before paging.
- Windowed bulky Team state history collections in `/state` and scoped view
  payloads while preserving current/actionable records.
- Kept Team UI mutation recovery on scoped active-view refreshes instead of
  falling back to full-state reloads.
- Invalidated cached Team views after conflict recovery so previously visited
  tabs reload against the latest revision.
- Refetched the corrected timeline page after server-windowed view payloads
  clamp an out-of-range page.

## 2026-07-04 - Admin Dashboard Poll Backpressure

- Paused Admin Web dashboard and shell background polling while the document is
  hidden, and suppressed unrelated shell polls while operators are inside
  add-on views.
- Added weak ETags to the opt-in dashboard JSON cache so unchanged dashboard
  requests can return `304 Not Modified` instead of retransmitting large
  payloads.
- Compacted dashboard agent-inventory responses to omit deep inventory,
  execution, metadata, and session-policy details outside non-dashboard
  `summary_only=true` reads.

## 2026-07-04 - Admin Web Branding Asset Caching

- Resized and compressed the bundled Evander and Quinth Software Solutions
  branding PNGs so header and sign-in logos no longer ship multi-megabyte
  source images.
- Taught Admin Web to use Vite manifest-resolved hashed default branding assets
  when the frontend build is available and to serve hashed `/assets/...` files
  with long immutable cache headers.
- Kept manifest rewrites scoped to the bundled branding asset directory so
  deployments with custom `EVANDER_ADMIN_WEB_BRANDING_ASSET_DIR` files keep
  serving their configured logo assets.

## 2026-07-04 - Admin Sessions List Payload

- Reduced `GET /api/admin/sessions` to a compact single `items` list with
  counts and active/expired session ids, moving policy, tool, notification, and
  runtime-image details to the session detail payload.
- Cached per-session policy summary fragments for repeated Admin Web session
  detail reads.
- Kept compact sessions rows provider-aware while refreshing notification target
  options and selected-session status from live list data.

## 2026-07-04 - Tool-Call Audit Index Catch-Up

- Batched tool-call audit JSONL imports through one SQLite transaction per
  import run and kept completed import-source metadata current as audit events
  are appended.
- Moved Admin Web audit-index catch-up out of startup's synchronous tail import
  path and into a best-effort background importer.
- Changed dashboard tool-call audit requests to read from the SQLite index only
  instead of scanning or importing the `runtime/audit.jsonl` tail.

## 2026-07-04 - Worker Storage Telemetry Cache

- Cached container-worker session storage telemetry inside the worker daemon
  with stale-while-revalidate behavior so Admin capacity-health reads do not
  block on cold deep scans of large worker session trees.
- Reworked worker session storage telemetry to compute total storage and
  largest/oldest session samples in one filesystem pass, with explicit
  blocking refresh available through `GET /v1/telemetry?refresh=1`.
- Guarded worker storage telemetry cache writes so an older background scan
  cannot overwrite a newer explicit refresh result.

## 2026-07-04 - Configuration Page Reorganization

- Reorganized Admin Web Configuration into operator-facing areas for people and
  communications, models and Assistant behavior, agent runtime, code/issues/PRs,
  access and audit policy, network/package relays, automation assignment, and
  advanced state.
- Moved email, approval notification targets, phone calls, communication files,
  and communication artifact link settings into People & Communications so
  related messaging settings are no longer split across unrelated tabs.
- Moved model pricing into the Models, Assistant & Knowledge area alongside
  model routing and Assistant settings.
- Kept approval-required tool policy, VM provider defaults, and libvirt VM
  template fields editable in the reorganized Configuration areas.

## 2026-07-04 - Team Add-on First-Load Responsiveness

- Reduced Team Add-on first-load pressure by building scoped view payloads
  directly instead of serializing full Team state and filtering afterward.
- Changed the Team Add-on state stream to publish lightweight revision events
  so connected UI tabs refresh only the active view instead of forcing full
  state snapshots on every poll.
- Queued Team Add-on active-view reloads when newer revision events arrive
  during an in-flight refresh so the UI does not settle on skipped stale data.
- Invalidated all cached Team view slices on revision-only events and preserved
  active form drafts during the forced refresh.
- Kept Admin Web Lock/Unlock changes from remounting add-on iframes and taught
  the Team Add-on UI to update locked controls from same-origin privilege
  messages while preserving selected Teams records.
- Stopped Team Add-on cost loads from running transcript backfill on page load,
  added an explicit transcript-cost refresh path, and circuit-broke repeated
  remote transcript scans for stale hosts.

## 2026-07-04 - Add-on Package Discovery

- Added local add-on package discovery from the git-ignored project-root
  `addons/` directory so copied packages appear on the Admin Web Add-ons page
  without hand-editing runtime config.
- Kept copied add-on packages available but disabled by default, preserving
  legacy load-all behavior for explicitly configured modules and entry points.
- Documented `addon_loader.package_dirs` and kept `runtime/addons/<addon_id>/`
  reserved for add-on private state.
- Fixed local add-on package imports so packages that reuse module names such as
  `addon` load from their own package directories instead of `sys.modules`
  cache entries from earlier packages.
- Fixed scanned package-style add-ons so absolute imports inside the declared
  package work while preserving same-name package isolation.
- Kept scanned package aliases available after registration so route and MCP
  handlers can perform lazy absolute imports from their package.
- Added `addon.json` manifests to bundled add-ons and scanned source-tree
  manifests from `src/` so bundled packages model the same registration contract
  future local add-ons should use.
- Resolved add-on package scans from the project base in gateway and workflow
  provider runtime paths so manifest-enabled bundled add-ons do not depend on
  the supervisor process working directory.

## 2026-07-04 - MSSQL Seed Add-on

- Added an optional MSSQL Seed add-on with Admin Web configuration for
  sidecars and approved `.bak` seed aliases.
- Added `evander-mssql-seed-sidecar` so container-worker MSSQL databases can be
  restored or reset from approved backups without adding seed-restore semantics
  to the Core worker API.
- Added worker Ansible installation for `evander-mssql-seed-sidecar.service`,
  including env rendering, seed backup directory creation, and health
  verification.
- Added MCP tools for seeded MSSQL provision, reset, and listing that reuse Core
  container-worker MSSQL provisioning before restoring seed data.
- Fixed MSSQL Seed MCP tools so connected MCP callers can omit the legacy
  `session_id` argument and use the session bound to their MCP transport.
- Fixed MSSQL Seed settings writes so malformed payloads are rejected without
  replacing state and sidecar/token environment changes require Admin Owner
  permission.
- Fixed MSSQL Seed UI locked mode so settings controls cannot save while the
  Admin shell is read-only, and made seed restore timeouts configurable with a
  one-hour default for larger backups.
- Fixed idempotent MSSQL seed provision calls so they preserve tracked
  `last_reset_at` metadata after a database has been reset.
- Fixed MSSQL Seed sidecar verification to probe the configured concrete bind
  host, including IPv6 hosts, while keeping wildcard binds probed through
  localhost.

## 2026-07-04 - Admin Web UI-Only Lock

- Changed the Admin Web Lock/Unlock control into a local UI read-only guard so
  toggling it no longer performs a backend privilege round trip or grants a
  single global editing session.
- Kept backend writes governed by authentication, permissions, and revision
  checks, with the compatibility privilege endpoints now recording lightweight
  per-session UI state only.
- Preserved the `privileged_unlocked` add-on iframe query parameter for bundled
  add-ons that still read unlock state from the initial URL.
- Synchronized bundled add-on lock state through Admin shell privilege messages
  so MSSQL Seeds and Team agent-create update without iframe reloads.
- Added explicit Admin Owner permission gates to helper-only global/sensitive
  writes that previously relied only on authenticated UI lock state.
- Aligned workflow-pack and add-on runtime pack refresh coverage with the
  UI-only lock behavior while preserving revision and disabled-add-on checks.

## 2026-07-04 - Artifact Space Filtering

- Added an artifact-space tree to the Admin Web Artifacts filter sidebar and
  let operators filter published artifacts by selected formal artifact space.
- Extended the Admin Web published-artifacts API with recursive artifact-space
  filtering so paging totals and result pages match the selected space.

## 2026-07-04 - Team Add-on Locked Mode

- Fixed Team Add-on locked mode so mutation entry points, including org chart
  context menu actions, create panels, edit saves, chat sends, voice calls,
  stand-downs, avatar changes, and governance/action handling, are disabled and
  guarded until privileged operations are unlocked.

## 2026-07-03 - Agent Create Dialog Layout

- Grouped create-time capabilities in the agent create dialog, moved Web
  search into that group, nested the MSSQL init root behind the MSSQL toggle,
  kept .NET, NuGet, and Playwright available for container workers, and
  replaced communication target checkboxes with a scan-friendly table.

## 2026-07-03 - Team Org Chart Viewport

- Kept the Team Add-on org chart canvas viewport stable across data-only edits
  such as agent label saves, while still refitting when the graph layout
  changes.

## 2026-07-03 - Team Agent Session Registration

- Let Team Add-on agent registration select available core VM and container
  sessions instead of requiring manual session ID entry.
- Added an inline core agent creation dialog to Team registration and
  auto-register the created session as a Team agent when creation succeeds.
- Guarded inline Team core-session creation with the Admin privileged-operation
  lock and required an explicit session id before Team auto-registration.
- Blocked Team-scoped core-session creation until the registration role,
  project, and superior selections are valid for the target team.
- Preserved the revision-conflict retry action when Team auto-registration
  conflicts after the core session has already been created.

## 2026-07-03 - Team Org Chart Context Menus

- Added right-click org chart actions for creating teams from the canvas,
  adding subordinate Team agents from agent cards, and adding or removing
  teams from governance oversight.
- Reused the Team agent session picker/create-session bridge from org-chart
  subordinate registration so new subordinates can link existing sessions or
  create a core session before Team registration.
- Fixed revision-conflict retry routing for org chart governance oversight
  actions.
- Kept nested core-session creation dialogs above org chart action dialogs.

## 2026-07-03 - Claude MCP Registration

- Reconciled generated Claude MCP config into Claude Code's user MCP registry
  before container and VM Claude prompt runs so resumed sessions keep Evander
  MCP tools in their available toolset.

## 2026-07-02 - Team Add-on Read Tools And Compact Context

- Added Team MCP read tools for visible tasks, inbox, cases, deliverables,
  reviews, releases, agents, projects, and runtime attention so agents can
  fetch detail without receiving the full Team state in every wake prompt.
- Added a Team MCP identity read tool that returns the caller's full role
  description, permissions, and named hierarchy without exposing unrelated Team
  state.
- Scoped Team agent read-tool visibility by team rather than project and kept
  direct superior identity available to the caller's own identity read.
- Added per-session MCP proof tokens for newly provisioned sessions so
  `session_id` alone cannot impersonate tokenized agents, and kept other Team
  agents' backing session ids out of read-tool details.
- Kept the waking agent's full role description in compact Team wake prompts
  while leaving other identity detail available through the identity read tool.
- Reduced Team wake prompt context to current and salient compact summaries,
  preserving detailed state behind policy-gated read tools.

## 2026-07-02 - Add-on Runtime Workflow Packs

- Made Admin Web add-on enablement materialize advertised runtime workflow
  packs, list them in the trigger-orchestrator workflow pack config, and expose
  an explicit refresh action that overwrites existing operator pack files only
  after confirmation.
- Blocked runtime workflow pack refreshes when the trigger-orchestrator add-on
  loader would not load the add-on, avoiding active workflow packs with
  unavailable add-on providers.
- Removed disabled add-ons' runtime workflow pack references from
  trigger-orchestrator config while leaving materialized pack files intact.

## 2026-07-02 - Team Git Work Contexts

- Added optional Team Git work contexts so Git deliverables must bind to a
  server-owned branch context, while artifact-only and non-coding deliverables
  continue to work without Git metadata.
- Created Git work contexts during project-repo wakes, surfaced their
  `branch_context_id` values in wake outputs, prompt context, and task read
  tools, and scoped state/query output so unrelated contexts are not exposed.
- Tightened Git deliverable binding so dispatch-scoped contexts must match the
  active dispatch even through the legacy action path.

## 2026-07-01 - Codex Optional Feature Compatibility

- Made Codex `non_prefixed_mcp_tool_names` enablement conditional on the
  installed CLI advertising that feature, avoiding agent-run startup failures
  on hosts or worker images with different Codex feature sets.
- Stopped writing that optional feature into generated container Codex config
  files; runtime invocations now probe support and add the flag only when safe.

## 2026-06-30 - Refactoring, renaming and UI improvements

- Changed the default Admin Web title and login/shell branding to the bundled
  Evander and Quinth Software Solutions assets under
  `frontend/workflow-editor/branding`.

- Replaced hand-crafted Admin Web action icons with Lucide icons and made edit
  and delete actions use canonical shared glyphs across the workflow editor.

- Added first-class Admin Web add-on management for restart-gated add-on
  enablement, loaded/pending status, workflow-reference warnings, and aligned
  host/trigger add-on loader config writes.

- Fixed Evander rename upgrade handling for add-on enablement saves, legacy
  Admin Web auth env files, legacy VM session env files, and worker apt proxy
  cleanup.

- Fixed Evander rename upgrade handling for legacy Azure SSO env files, Claude
  and Copilot guest session fallbacks, legacy agent resume state, and stale
  Codex profile `CODEX_HOME` exports.

- Fixed additional Evander rename/setup compatibility issues for legacy Admin
  Web session and workflow-event token env vars, legacy Git mirror metadata,
  sudo-owned setup secret files, and same-host container worker launch flags.

- Fixed same-host container worker setup so the worker daemon unit binds to the
  configured `container_worker_base_url`, and moved the default local worker
  URL to `127.0.0.1:11890` to avoid the DIDWW bridge default port.

- Fixed the Admin Web frontend-not-built fallback page so it preserves the
  injected branding bootstrap payload.

- Fixed Evander rename upgrade handling for legacy nftables firewall tables and
  libvirt networks, and omitted `state:all` from GitHub issue search queries.

- Fixed agent Git push hooks to fall back to and migrate legacy
  `agent-sandbox.*` bare-repo promotion metadata.

- Renamed the operational project identity to Evander: the Python package is
  now `evander`, console scripts and systemd units use the `evander-*` prefix,
  environment variables use `EVANDER_*`, and guest state/config paths now use
  `.evander` and `/etc/evander`.

- Added systemd install migration cleanup for the Evander rename so legacy
  `agent-sandbox-*` units are stopped, disabled, and removed while preserving
  host-local secrets under `/etc/evander/local-secrets.env`.

- Added an internal workflow event catalog so known stable event names can appear
  as `trigger:event.event_type` suggestions in Admin Web, including Team Add-on
  runtime events when the add-on is loaded.

- Added provider-neutral remote issue workflow primitives for querying, matching,
  and comment polling across Azure DevOps work items and GitHub issues, while
  keeping the existing ADO node types as compatibility surfaces.

- Fixed GitHub remote issue queries so `labels_any` pagination continues
  scanning until enough matching issues are found.

- Added cursor-key navigation to the guided `evander-cli setup` checklist and
  interactive single/multi-select menus while preserving typed prompts for
  non-TTY setup runs.

- Fixed guided setup arrow-key handling so raw terminal reads collect complete
  Up/Down escape sequences before updating the highlighted row.

- Fixed guided setup install preflight failures so expected non-zero preflight
  results return to the checklist instead of raising a traceback, and made the
  setup profile treat unrelated integration config as warnings.

- Fixed guided setup launch for container-worker execution by generating the
  worker bearer token in the host secret file, loading it for the launch smoke
  check, and reporting launch failures without a traceback.

- Added guided setup provisioning for the same-host container worker daemon and
  made setup preflight require Docker before that execution model can launch.

- Fixed guided setup/Admin Web launch by building the Admin Web frontend bundle
  when hashed assets are missing or stale, asking before installing apt-provided
  Node.js/npm when needed, and by showing the frontend-not-built fallback page
  when `dist/index.html` references missing assets.

- Moved Admin Web workflow pack selection and history into compact controls
  overlaid on the workflow canvas, with add/edit/remove pack actions kept to
  icon buttons and the edit dialog.

- Moved the Admin Web workflow generator launcher into a top-right workflow
  canvas overlay with a wand-sparkles icon, and kept generation controls in a
  floating canvas panel so draft workflows remain inspectable before acceptance.

- Fixed worker MSSQL deployment so the SQL Server admin connectivity check waits
  for TCP login readiness after service start or restart.

- Added cursor paging to the Admin Web workflow pack history popover.

- Fixed the Admin Web workflow canvas layout so workflow focus mode fits inside
  the visible viewport without adding a page scrollbar.

- Added internal workflow pack change history for Admin Web/API edits, with
  runtime-local JSONL audit records, immutable before/after snapshots, actor
  attribution, history endpoints, and a Workflow editor history panel.

- Kept workflow pack removal usable as a recovery path for malformed pack or
  layout files by recording snapshot capture errors without blocking unlisting.

- Fixed workflow pack management authorization so pack create, metadata edit,
  and removal require root-scoped `workflow_packs.manage` instead of
  directory-scoped `workflows.write`, and refreshed saved pack descriptors in
  the editor to avoid stale metadata or pack-list revision conflicts after
  workflow saves.

- Added first-class Admin Web workflow pack management so operators can create
  blank packs, edit pack metadata, and remove packs from the active
  `workflow_packs` list without deleting pack files.

- Restored privileged-helper mode after the admin facade removal by returning
  helper responses, restoring JSON request handling, and wiring the public
  runtime flag to the focused services that consume it.

- Preserved caller-supplied provider runtime admin services while keeping
  provider effects on narrow VM/issue ports, and updated tests to patch focused
  admin services or concrete collaborators directly after the facade removal.

- Updated CLI and gateway tests for the admin facade removal so fakes patch
  `build_admin_runtime` and expose only the focused runtime component under
  test.

- Restored Admin Web add-on service compatibility after the admin facade removal
  by passing add-on routes a narrow adapter for project Git imports, agent
  inventory summaries, and Team chat dispatch helpers.

- Fixed provider-runtime adapter regressions from the admin facade removal so
  narrow VM providers no longer advertise unavailable skill validation, model
  usage query providers delegate through `model_usage_admin`, and tests no
  longer import the removed `admin.service` module.

- Split the remaining admin facade concerns into focused Teams, session, wake
  schedule, VM inventory, and worker-control services, and replaced private
  composition wiring with public workflow-config service ports.

- Removed the legacy `AdminService` facade and routed Admin Web, gateway, CLI,
  provider runtime, and tests through the explicit `build_admin_runtime`
  component graph and focused admin services.

- Added Team Add-on workflow primitives for deterministic review routing:
  `trigger:team:deliverable-created` and
  `effect:team:create-review-request`.

- Fixed VM-agent package exports so the mixins advertised by `__all__` support direct imports and
  star imports.

- Split `VmAgentAdminService` internals into focused VM-agent mixins for container sessions,
  inventory, create operations, agent actions, chat assistant setup, container backend access, and
  VM lifecycle behavior while keeping the public admin facade compatible.

- Split the Team Add-on domain helper module into focused domain helper modules and updated
  Team services, providers, API/MCP surfaces, and tests to import those helpers directly.

- Removed remaining Team Add-on imports from the legacy domain helper module so provider,
  service, command, API, MCP, and test code target the focused domain helper modules directly.

- Refactored the Team Add-on state repository into a storage-only persistence facade, moving
  command, admin, chat, runtime, ephemeral, agent-action, and Git/release workflows into
  focused Team services.

- Split Team Add-on command handling into command-family services and wired MCP callers
  directly to those families instead of keeping an all-command facade.

- Restored Team Add-on UI smoke coverage for exact revision payloads, chat
  scroll preservation, and post-review timeline refreshes, and kept chat
  scroll application stable across layout settling.

- Moved workflow config, editor, run-store control, and trigger/effect mutation
  logic into `WorkflowConfigAdminService` with explicit composition dependencies.

- Moved workflow reports and workflow generation ownership into explicit admin
  services, leaving `AdminService` as the compatibility facade for those APIs.

- Moved VM, agent, session execution, git mirror, and guest SSH/subprocess helper
  ownership into focused VM admin helper modules while keeping `AdminService` compatibility.

- Moved issue/git helper ownership, approval/tool-call audit operations, and published
  artifact/artifact-space operations into focused admin services while keeping `AdminService`
  compatibility delegates.

- Finished the final AdminService extraction slice by moving capacity health, model usage
  dependencies, operator steering interrupts, assistant settings/chat, release, and deployment
  status ownership behind extracted admin services.

- Removed the VM-agent `admin_service_override` callback, routed VM-agent orchestration through
  explicit service ports, and stopped exposing selected private VM/container helpers through
  `AdminService`.

- Completed the model-usage, tool-visibility, and tool-call audit facade cleanup so
  `AdminService` delegates those APIs while preserving `tool_call_audit_index` compatibility.

- Restored home-directory expansion for extracted workflow path settings so
  trigger-orchestrator runtime and store paths continue to support `~`.

- Added `effect:team:create-task` so workflows can directly create or update
  operator-authored Team Add-on tasks for a project owner.

- Cleaned up the Team Add-on Projects tab with horizontal project tabs, hidden
  create forms, collapsed paged deliverable/release sections, and default-hidden
  rejected delivery records.

- Preserved Team Add-on Projects deliverable/release collapsed-section state
  across normal Projects tab re-renders.

- Reworked the Team Add-on Workspace tab into a compact dashboard for team status,
  attention items, blockers, delivery counts, and drill-down links to focused tabs.

- Reduced stale Team Add-on Operations blockers by showing failed action attention only for each
  agent's current failed dispatch while preserving undismissed failures in that dispatch.

- Removed the Team Add-on Overview tab and its dedicated view endpoint so operators use the
  workspace and focused detail tabs instead.

- Kept the Admin Web built-in chat anchored when SSE snapshots or operator scroll changes race an
  older-history fetch.

- Kept Teams inbox retention compaction from deleting historical messages before
  a channel cursor has advanced into the retained window.

- Preserved lightweight `AdminService.list_model_usage_events` and legacy
  `VmAgentAdminSupport` privileged-helper construction compatibility after the AdminService
  extraction.

- Kept Admin Web approvals, session policy, wake schedule, worker/config, user-contact,
  privileged-operation routes, CLI directory/wake commands, and admin MCP config tools on the
  `AdminService` compatibility facade instead of extracted subservice attributes.

- Restored Admin Web workflow report endpoints after AdminService report extraction by keeping
  runtime report payload classes available to the extracted workflow reports service.

- Extracted shared MCP gateway communication, inline media, email draft, artifact, attachment,
  and VM visibility helper implementations into focused modules while preserving compatibility
  exports and behavior.

- Fixed MCP gateway refactor regressions that prevented server construction, miswired extracted
  communication and ADO helpers, and pointed the gateway CLI at `src/runtime`.

- Moved approval MCP gateway tools into `evander.gateway.approval_tools` while preserving behavior.

- Moved communication MCP gateway tools into `evander.gateway.communication_tools` while preserving behavior.

- Moved PR/feedback MCP gateway tools into `evander.gateway.pr_tools` while preserving behavior.

- Moved Azure DevOps backlog, work-item, report, identity, and activity MCP gateway tools into `evander.gateway.ado_tools` while preserving behavior.

- Moved agent status and log MCP gateway tools into `evander.gateway.agent_tools` while preserving behavior.

- Moved issue lifecycle MCP gateway tools into `evander.gateway.issue_tools` while preserving behavior.

- Kept gateway VM visibility, VM-oriented CLI commands, issue/approval CLI commands, and Admin
  Web Teams auth routes on the `AdminService` compatibility facade instead of extracted
  subservice attributes.

- Replaced dynamic workflow report helper imports with explicit imports and kept workflow
  Admin Web/gateway entry points on the `AdminService` compatibility facade.

- Moved admin MCP gateway tools into `evander.gateway.admin_tools` while preserving behavior.

- Moved chat and agent-prompt MCP gateway tools into `evander.gateway.chat_tools` while preserving behavior.
- Moved semantic MCP gateway tool registration into `evander.gateway.semantic_tools` while preserving existing behavior.
- Moved MSSQL MCP gateway tool registration into `evander.gateway.mssql_tools` and added registration coverage while preserving existing tool names and request/response behavior.
- Split the MCP gateway Starlette app and CLI bootstrap into canonical `gateway.app` and `gateway.cli` modules while retaining compatibility exports.
- Added a shared MCP gateway dependency object for common admin, issue, chat, semantic, and communication artifact services.
- Split MCP gateway request, session, tool audit, and approval diagnostic helpers into focused modules while preserving existing helper names.
- Refactored the MCP gateway into a package with a `server` module and domain registration module placeholders while preserving existing public imports and tool names.

## 2026-06-28 - First-Run Setup TUI

- Added `evander-cli setup` as an interactive first-run checklist for choosing
  host secret storage, Admin Web access, execution model, AI providers, default
  model routing, install preflight, and launch verification.
- Added `evander-cli setup doctor --json` and `evander-cli setup plan --json`
  for scriptable setup readiness and launch-plan inspection.
- Added `./scripts/setup` as a one-step checkout bootstrap that installs Python
  prerequisites, creates `.venv`, installs Evander, and starts the guided setup
  checklist.
- Added inline guidance before first-run setup decisions so new users understand
  what each choice means before selecting an option.
- Cleared the terminal between first-run setup menu selections so each checklist
  or decision screen starts cleanly.
- Added an explicit discouraged Admin Web no-auth setup option for isolated
  evaluation hosts.
- Removed the setup profile prompt because it did not yet change setup behavior.
- Stored setup draft state under `runtime/setup-wizard/state.json` while keeping
  entered secret values in a host-local secret env file.

## 2026-06-28 - Team Subordinate Task Finish Permission

- Allowed Team supervisors to mark claimable subordinate tasks `finished`
  directly, including in the same `team_update_task` call that claims the task,
  while keeping other non-owner state/detail edits blocked.
- Bumped live Team agent guidance so persistent supervisor agents are reseeded
  with the new subordinate task finish permission.

## 2026-06-28 - Git Mirror Pinned Branch Fast-Forward Fix

- Preferred explicit pinned mirror branches and stored mirror base branches
  before the current checkout fallback when fast-forwarding stale local refs,
  preventing pinned `main` mirrors from publishing stale refs while another
  remote branch is checked out.

## 2026-06-28 - Team MCP Artifact Publisher Config Fix

- Resolved effect provider configuration from gateway MCP runtime contexts so
  Team deliverable artifact publication for container-worker sessions keeps the
  configured `container_worker_base_url` instead of failing with a missing
  worker URL.

## 2026-06-28 - Git Mirror Fast-Forward Sync

- Fast-forwarded stale local branches before refreshing container-worker and
  SSH-backed Git mirrors when the remote has only additive commits, while still
  refusing divergent local branches that would require a merge decision.

## 2026-06-28 - Admin Compatibility Contract Fixes

- Restored Admin Web VM creation through the `AdminService` facade so wrappers,
  subclasses, and compatibility logic remain honored.
- Routed Admin Web VM and local-issue operations through the `AdminService`
  facade instead of direct subservice calls.
- Delegated workflow report public methods through the host contract and made
  privileged-helper construction resolve the helper class lazily.

## 2026-06-28 - Admin Session Facade Integration Fixes

- Kept Admin Web and CLI session deletion on the `AdminService` facade so
  associated agent-session guards and detached worker/chat/approval cleanup run.
- Restored Admin Web session listing and detail lookup through the facade so
  container-worker lifecycle summaries remain available to the UI.

## 2026-06-28 - Admin Service Composition Startup Fixes

- Fixed extracted admin service imports and composition locking so `AdminService`
  can be constructed by web, gateway, and CLI callers.
- Routed issue/Git directory normalization through the injected directory path
  port and updated focused service tests for the refactored dependency shape.

## 2026-06-27 - Workflow Run Report Reader Extraction

- Moved workflow run snapshot, search, detail, graph activity, failure cluster, and conformance report readers out of `AdminService` and into `WorkflowReportsAdminService`.
- Left small `AdminService` compatibility delegates for existing workflow configuration ports and callers.

## 2026-06-27 - Model Usage Admin Service Extraction

- Moved model-usage listing and operator-steering validation/note submission logic out of `AdminService` and into `ModelUsageAdminService`.
- Tightened workflow report service wiring to call the report implementations directly instead of using extra `AdminService` public-impl shims.

## 2026-06-27 - Capacity Health Service Extraction

- Moved capacity and health telemetry calculation out of `AdminService` and into `CapacityHealthAdminService`.
- Updated focused capacity-health tests to exercise the extracted service implementation while keeping `AdminService.read_capacity_health()` as the compatibility entry point.

## 2026-06-27 - Extracted Admin Service Focused Tests

- Expanded direct tests for extracted operations and workflow configuration admin services using small fake dependencies instead of full runtime configuration.
- Kept coverage explicit for each delegated method while preserving `AdminService` as the integration/compatibility facade.

## 2026-06-27 - Admin Service Facade Guidance

- Documented `AdminService` as the legacy/stable integration facade and clarified that new admin domain logic belongs in narrower services under `src/evander/admin/services/`.

## 2026-06-27 - Admin Service Protocol Ports

- Replaced lambda-based admin service wiring for directory ownership, privileged helper state, and workflow configuration callbacks with small protocol-backed port objects.
- Added focused unit coverage for services and facades using fake protocol implementations instead of a full `AdminService`.

## 2026-06-27 - VM Agent Creation Option Normalization

- Moved AdminService VM creation payload assembly, model-provider metadata normalization, and allowed-contact validation into `VmAgentAdminService`.
- Kept `AdminService.create_vm` as a compatibility delegate and added direct VM agent service coverage for normalized creation options.

## 2026-06-27 - Workflow Reporting Service Ownership

- Moved workflow generation request execution and cooldown state into the extracted workflow generation admin service.
- Kept AdminService workflow generation/reporting APIs as compatibility delegates and added focused service coverage for cooldown and report dispatch behavior.

## 2026-06-27 - Skill Admin Service Extraction

- Moved managed Codex skill admin operations into a dedicated `SkillAdminService` backed directly by `SkillLibraryRepository`.
- Kept `AdminService` skill APIs as compatibility delegates and added focused service tests.

## 2026-06-27 - Admin Service Narrow Interface Callers

- Moved Admin Web, gateway, and CLI callers off broad `AdminService` pass-through
  wrappers where practical and onto narrower admin component interfaces.
- Marked the remaining compatibility wrappers that delegate to operations,
  workflow config, VM agent, and issue/Git admin services as deprecated for new
  internal code.

## 2026-06-27 - Admin Service Composition Builder

- Extracted AdminService runtime/config/store/facade construction into a dedicated composition module while preserving public AdminService construction for existing callers.

## 2026-06-27 - Admin Service Domain Extraction

- Kept `AdminService` as the backwards-compatible public facade while routing
  low-risk domains through narrower services for skills, workflow reports,
  capacity/health telemetry, model usage, and workflow generation.
- Added focused unit coverage for the extracted admin service delegation layer.

## 2026-06-28 - Team Queued Retry Finalization Guard

- Ignored queued prompt retry markers when finalizing Team dispatch leases, so
  normal queue replay can remove queued records without causing old failed
  enqueue attempts to close the active dispatch.

## 2026-06-28 - Team Chat Live Lease Guard

- Kept active Team Chat dispatch leases live after `team_reply_to_chat` marks an
  operator message replied, preventing scheduled or inbox wakes from minting an
  overlapping dispatch before finalization releases the original lease.

## 2026-06-28 - Evander Manual Operational Corrections

- Corrected Evander user manual guidance for Workers-managed services, Bokio
  prepared-action approvals, and add-on loader configuration boundaries.

## 2026-06-28 - Team Duplicate Review Progress Accounting

- Classified duplicate-only Team review request commands as non-progress in
  dispatch activity records, keeping dispatch finalization from resetting
  no-progress counters when no review or inbox item was created.

## 2026-06-27 - Team Review Request Idempotency

- Made Team review requests idempotent for an active deliverable/reviewer pair
  so duplicate handoffs return the existing review instead of creating another
  reviewer inbox item.

## 2026-06-27 - Team Wake Lease Release Fix

- Released Team wake leases when standalone agent prompt executions finish, so
  completed container-worker wakes no longer block follow-up inbox work until the
  lease TTL expires.

## 2026-06-26 - Team Normal Inbox Wake Fix

- Treated normal/high priority Team inbox items as wakeable even when they do
  not request a reply, keeping low-priority FYI updates batched.
- Cleared completed Team chat dispatch leases before leasing later work so
  replied chat wakes cannot suppress subsequent inbox wakes.

## 2026-06-26 - Queued Dispatch Correlation Fixes

- Preserved caller-supplied dispatch ids when busy agent prompts are queued and
  reused them during queued libvirt and container dispatch replay.
- Let lightweight AdminService facades delegate libvirt resume calls without
  requiring container session repository support.
- Kept Team session-health container and execution details out of rows that do
  not have real container metadata or execution records.

## 2026-06-26 - Queued Reduced-Context Retry Metadata

- Preserved reduced-context retry prompts and per-effect retry settings when
  container agent prompts are queued for later dispatch.
- Restored queued workflow and MCP approval metadata onto container waiting
  records so reduced-context retries keep using the same approved MCP tools.

## 2026-06-26 - Codex Final-Message Detection Corrections

- Avoided marking Codex runs as failed when `task_complete.last_agent_message`
  is empty but assistant output was already emitted in the same stream.
- Parsed nested `event_msg` token-count events in worker outcome detection and
  live Codex log filtering so context-limit diagnostics keep token details.

## 2026-06-26 - Container Codex Native MCP Tools

- Enabled Codex `non_prefixed_mcp_tool_names` for generated container session
  configs and container Codex invocations so Team MCP tools can appear as
  native callable tools instead of requiring manual MCP HTTP calls.
- Covered existing VM-to-container refresh tooling so repaired container
  session homes preserve Codex history while receiving the native MCP feature
  flag in both active and legacy Codex config locations.

## 2026-06-26 - Reduced-Context Retry For Context-Exhausted Agents

- Added a generic reduced-context retry path for Codex container-worker agent
  prompts whose completed worker execution is marked as context exhausted.
- Kept workflow agent-prompt records waiting while the bounded retry runs, so
  downstream completion events are published only after the retry settles.
- Added conservative provider config knobs for enabling the retry, limiting
  attempts, and setting the deterministic reduced-prompt character budget.

## 2026-06-26 - Codex Missing Final Message Failure Detection

- Marked completed Codex container-worker executions as failed when Codex emits
  `task_complete` without a final agent message, including suspected local
  context-limit diagnostics when local-model token usage supports that cause.
- Added the same missing-final-message diagnostic to live filtered Codex logs,
  reconstructed worker logs, container execution metadata, and Admin
  container-agent status presentation.

## 2026-06-26 - Evander CLI Rename

- Added `evander-cli` as the preferred host CLI entry point while keeping
  `evander-cli` as a compatibility alias.
- Updated service templates, helper scripts, operator notifications, tests, and
  docs to use `evander-cli` for new installs and examples.

## 2026-06-26 - Team Add-on Container Roster Status

- Hid the Team Add-on roster `active` pill for container-backed agents unless
  they have an active Team runtime dispatch, while preserving paused/archived
  roster states and VM-backed status display.
- Added session backend, container lifecycle, and Agents-page execution details
  to Team runtime session health payloads so the UI can distinguish roster
  state from execution state.
- Passed Team project canonical Git URLs into container project-repo preparation
  and let container mirrors prefer a matching local remote, avoiding SSH-only
  origin fetches from systemd services when an HTTPS remote is configured.
- Honored Team project bare Git remote names during container project-repo
  preparation, keeping VM and container sessions on the same project remote
  instead of creating a separate hashed mirror.
- Preserved Team-owned branch refs during container Git SSH mirror refresh and
  fell back to generated mirrors for host-only bare repo paths that are not
  directly worker-addressable under the configured Git root.
- Fixed systemd unit rendering under `pkexec` so services run as the repository
  owner instead of root when `SUDO_USER` is unavailable.
- Routed generic agent resume calls for container-backed sessions through the
  container prompt dispatch path, preserving Team dispatch ids and resume mode
  instead of falling through to VM-only resume handling.

## 2026-06-26 - Team Add-on MCP Wake Request Publishing

- Fixed Team MCP command handoffs so generated `team.agent.wake_requested`
  events are durably queued and immediately scheduled with their debounce delay.
- Fixed duplicate debounced wake requests so command responses and idempotency
  records report the stored outbox row after idempotency dedupe.

## 2026-06-26 - VM Session To Container Migration Tooling

- Added a guarded VM-to-container session migration command with dry-run,
  copy-only, verify-only, cutover, and rollback phases for moving libvirt agent
  state into durable container worker session directories.
- Preserved existing session manifests during cutover by cloning each manifest
  and changing only the backend identity fields while retaining model, approval,
  tool, Team/add-on, and agent thread metadata.
- Copied VM `/home/agent`, `/artifacts`, optional run logs, and Codex history
  into the worker root, mirroring legacy `.codex/sessions` into the active
  container Codex state path before refreshing container session config files.

## 2026-06-26 - Codex Relay Bootstrap And Team Guidance Cleanup

- Changed Codex VM and container session bootstrap to use a relay-specific
  `evander_openai_relay` provider with websocket support disabled, preventing
  startup websocket prewarm failures against the host OpenAI relay.
- Bumped Team Add-on agent guidance seeding so active agents remove stale
  `truman-team-addon` managed guidance sections when current
  `evander-team-addon` MCP guidance is reseeded.

## 2026-06-26 - Team Add-on Debounced Wake Requests

- Added debounced `team.agent.wake_requested` events so high-signal Team
  handoffs can lease an agent outside the shared 15-minute cron while preserving
  active lease checks, cost guards, low-priority batching, and no-progress
  suppression.

## 2026-06-26 - Built-In Chat Scroll Anchoring

- Fixed the built-in assistant chat so sending a message keeps the transcript
  anchored near the newest messages instead of jumping to the oldest messages.
- Loaded built-in assistant chat transcripts in bounded pages and fetches older
  messages when scrolling upward, avoiding full-thread loads for long chats.
- Replaced stale live transcript windows when the latest streamed page no longer
  overlaps the loaded messages, preventing unrecoverable gaps after long tab
  sleeps or disconnects.

## 2026-06-26 - Local Container Worker Host-Network Opt-In

- Added an explicit unsafe host-network opt-in for single-host local container
  worker experiments, keeping Docker host networking refused by default while
  allowing operator-owned workstation deployments to bypass blocked bridge
  traffic.
- Fixed effect-provider runtime metadata seeding so the effect orchestrator can
  start while passing internal event and workflow journal paths to effect
  providers.

## 2026-06-25 - Team Add-on Live UI and Chat Unread Indicators

- Added a Team Add-on state stream so the UI receives revision snapshots
  without manual refresh and restores in-progress form drafts after live
  renders.
- Highlighted chats with unread incoming messages, surfaced a Chat tab unread
  count, and clears unread markers when conversations are opened.
- Fixed unread chat detection for previously opened conversations by preserving
  fresh newest-message summary metadata from conversation-list refreshes.

## 2026-06-25 - Team Add-on Clean MCP Cutover

- Seeded `rg` into current and future libvirt agent VMs: new VMs still request
  `ripgrep` through cloud-init, provider bootstraps now copy a host `rg`
  binary into `~/bin/rg`, and `bootstrap_ripgrep_vm.sh` repairs existing VMs
  without relying on guest internet.
- Documented the clean Team MCP cutover from Team action artifacts to
  Team-owned MCP tools, including tool visibility, idempotency, finalization,
  deliverable artifact publication, and ephemeral worker reporting semantics.
- Clarified deliverable Git/review-branch sequencing in Team MCP tool
  descriptions and prompts, added next-action hints after deliverable
  submissions, and added optional structured deliverable verification status.
- Propagated deliverable verification into superior notice and review-request
  inbox payloads so review wakes see failed, partial, or skipped verification.
- Added a local Team MCP cutover runbook covering service quiescence, active
  lease inspection, pending artifact disposition, state backup/migration,
  restart, smoke checks, and common post-cutover troubleshooting.
- Removed the legacy Team action-artifact workflow providers from the Team
  Add-on registration surface now that ordinary Team actions report through
  Team-owned MCP tools.
- Added a Team runtime outbox publisher effect and wired it to the Team cron
  graph so MCP-recorded async intents publish their internal `team.*` workflow
  events and complete or retry their outbox rows.
- Taught Team Git feature and release consolidation to fetch missing
  `agents/...` source branches from the configured dropbox bare repo before
  merging in the trusted host checkout.
- Passed ephemeral assignment `allowed_tools` into the built-in
  `effect:agent:create` node so spawned workers receive the
  `team_report_ephemeral_result` MCP tool required to report completion.
- Preserved Team MCP tools required by overlapping active governance and
  ordinary Team dispatches that share a persistent session.
- Fixed the Team Chat conversation-list pagination fixture so the seeded
  conversation belongs to the default `admin` operator used by the API test.
- Added Team Add-on bootstrap and view-scoped read APIs, moved the built-in UI
  off full-state boot for normal tab loads, and let UI mutations request
  scoped invalidation envelopes instead of full Team state payloads.
- Fixed scoped Team Add-on loading edge cases so Chat preserves full cached
  agent records, Org receives governance overlays, and bootstrap chat counts
  respect the current operator's chat scope.
- Trimmed non-actionable Team Add-on view history from scoped payloads so
  handled inbox rows and stale action records do not slow default tab loads.
- Renamed the bundled Team Add-on Python package to `evander_team_addon` across
  source, tests, docs, frontend build paths, and local add-on loader
  configuration.

## 2026-06-25 - ADO Daily Status Elapsed Time Guard

- Required `get_ado_daily_status_report_data` callers to opt into
  `elapsed_time_mode: manual_override` before `elapsed_sprint_percent` can
  replace the elapsed-time value computed from Azure DevOps iteration dates.
- Recorded ignored elapsed-time override values in the report source metadata
  so suspicious sprint-time percentages are auditable.

## 2026-06-24 - Team Add-on Blocked Release Guidance

- Added explicit next-action hints to blocked release packages and runtime
  attention rows so agents and operators resolve the existing release branch
  instead of creating duplicate release requests.
- Improved duplicate release-package action feedback with the existing package
  state, branch, blocker, and correction guidance.
- Clarified Team agent release prompts and schemas that `git_release_requests[]`
  creates new packages only and blocked packages should follow their
  `next_action_hint`.
- Scoped release package recovery hints into normal agent wake prompts and made
  blocked-release guidance point at the failed promotion target branch instead
  of assuming the release branch is always the recovery branch.
- Required `git_release_recoveries[]` to include the resolved `commit_sha`,
  included that SHA in recovery idempotency, and validated it before the trusted
  release target branch is updated.
- Stopped advertising release recovery for blocked packages caused by failed
  feature or canonical promotions; those hints now point at the failed promotion
  target and reserve `git_release_recoveries[]` for failed release promotions.

## 2026-06-24 - Team Add-on Release Prefix Normalization

- Normalized Team project canonical release branch prefixes to one trailing
  slash so `release` and `release/` both publish under `release/<id>`.

## 2026-06-24 - Team Add-on Case Workflow Effects

- Added `effect:team:create-case` and `effect:team:update-case` workflow nodes
  so ticket/accounting intake workflows can create, update, and task-link Team
  cases without routing through a persistent agent action.
- Exposed the new Team case effects in the add-on workflow editor catalog with
  output schemas and focused runtime/catalog coverage.
- Returned generated `effect:team:create-case` ids from inside the repository
  mutation so concurrent case creation cannot make a committed workflow-created
  case look like a failed create.

## 2026-06-24 - Team Add-on Release Flow Recovery

- Attached successful feature promotions back to matching accepted Git
  deliverables instead of creating extra pending synthetic deliverables when the
  source review branch can be identified.
- Added a safe repair path for existing synthetic feature-promotion deliverables
  and made manager fallback wake on active deliverables even when their tasks are
  already terminal.
- Clarified Team Chat wake prompts and docs so explicit operator requests may
  include state-changing Team action fields alongside `chat_replies`.
- Let `git_release_requests[]` enqueue prerequisite feature promotions for
  accepted review/agent branches and automatically continue the release package
  after those promotions succeed, removing the old manual feature-then-release
  agent workflow.
- Added `git_release_recoveries[]` and a Team runtime recovery graph so blocked
  release merge conflicts can resume from a resolved `agents/...` ref without
  creating a duplicate release package.

## 2026-06-24 - Team Add-on Git Release Sequencing

- Rejected Team actions that combine non-empty `git_feature_updates[]` and
  `git_release_requests[]` before they can enqueue partial Git promotion events.
- Clarified the seeded Team action schema, wake prompt, and durable agent
  guidance so agents request feature promotion first, then release on a later
  wake after `git_refs.feature_branch` is visible.
- Added action-feedback hints for the combined Git release mistake and bumped
  the Team action contract and agent guidance versions.

## 2026-06-24 - Team Add-on Review Artifact Routing

- Clarified that reviewer artifact imports only happen for `review_requests[]`
  inbox items, and added coverage so status requests with copied artifact paths
  cannot be mistaken for review artifact delivery.
- Bumped the Team agent guidance version so existing persistent agents receive
  the corrected review-artifact routing instruction on their next wake.

## 2026-06-24 - Team Add-on Setup Tab Deprecation

- Removed the Team Add-on `Setup` top-level tab and made old stored `setup`
  view preferences fall back to the `Teams` tab while keeping the temporary
  Workspace setup rail available for flows that still need contextual homes.

## 2026-06-24 - Team Add-on Agent Edit Filtering

- Scoped agent edit Project, Role, and Reports-to dropdowns to the currently
  selected team so changing the Team field no longer exposes invalid cross-team
  choices.

## 2026-06-24 - Team Add-on Agent Removal History

- Allowed removing agents from a team when the only remaining references are
  terminal deliverable producer or inbox sender records, while preserving
  blockers for active deliverables and pending inbox items addressed to the
  agent.

## 2026-06-24 - Team Add-on Case Update Fixes

- Rolled back failed agent `case_updates[]` items so invalid task links cannot
  persist partial case edits, inserts, or task associations.
- Preserved explicit blank case descriptions through repository and API update
  paths so optional case text can be cleared.

## 2026-06-24 - Team Add-on Activity Summaries

- Added host-derived per-agent activity summaries to the Team Add-on payload so
  the org overview can show concise natural-language current work and waiting
  state without prompting agents or calling a classifier model.
- Filtered scoped Team state payload summaries so hidden tasks, inbox items, and
  other reason refs cannot leak through agent-visible activity summaries.
- Wired Team org cards to prefer the derived `activity_summary` while retaining
  existing task, inbox, runtime, and session details for drill-down.
- Made Team Add-on view tabs use delegated click handling and live DOM queries
  so tab switching remains responsive if tab elements are rendered or captured
  after the initial static element cache is built.
- Removed a duplicate Team UI `prioritySortValue` declaration that aborted the
  page script before Team Add-on tab handlers could initialize.

## 2026-06-24 - Team Add-on Case Domain

- Added first-class Team cases for intake and ticket lifecycle tracking, with
  optional external `source`/`source_ref`, owner, project scope, status,
  priority, tags, and linked task ids.
- Exposed case create/update API routes, `task.case_id`, wake/query context,
  agent `case_updates[]`, and Team UI case views/forms/editing.
- Bumped the Team action contract and agent guidance versions for the new case
  action surface.

## 2026-06-24 - Team Add-on Setup Panel Defaults

- Collapsed the Setup from template and Create team forms by default, and moved
  the template selector above the setup preview content.

## 2026-06-24 - Team Add-on Governance Agenda Defaults

- Hid handled Governance agenda items by default behind a Governance-tab toggle,
  and moved governance goals above member teams with a subtle gold card
  treatment that does not show routine goal states.

## 2026-06-24 - Team Add-on Task Ownership Handoff

- Allowed superior Team agents to set `owner_agent_id` in `task_updates[]` to
  claim or reassign non-terminal tasks currently owned by subordinate agents,
  while preserving owner-only task state/title/priority/description edits.
- Kept task handoff targets inside the task project and routed rejected-review
  rework to the current task owner so handed-off tasks can be revised cleanly.
- Bumped the Team action contract and agent guidance versions so persistent
  agents receive the new task handoff schema and instructions.

## 2026-06-24 - Admin Stream Connection Hygiene

- Closed Team Add-on chat, workflow-run, and Admin chat EventSource streams while
  their browser tab is hidden, and reopened them with a fresh snapshot when the
  tab becomes visible.
- Routed Team Add-on UI requests through a timeout wrapper so queued or stalled
  async operations report an error and release busy UI state instead of hanging
  indefinitely.

## 2026-06-24 - Team Add-on Inline Git Import

- Added the Git repo import action to team-scoped inline project creation so all
  Team Add-on project create/edit surfaces with Git fields can seed bare mirror
  config from the UI.

## 2026-06-24 - Team Add-on Accounting Template

- Added an Accounting Department Team template with bookkeeping roles, starter
  tasks, review criteria, and accounting-oriented role permissions for
  Bokio/Skatteverket-assisted workflows.
- Made the Team setup form render role/session fields from the selected
  template and hide Git setup for non-Git templates.

## 2026-06-24 - Team Add-on Review Artifact Imports

- Imported Team review-request `artifact-space:` refs into reviewer sessions
  during wake preparation so review prompts include concrete
  `/artifacts/team-review-inputs/...` paths and structured import errors.
- Enforced artifact-space read grants before review wake imports and isolated
  each imported ref/version under its own destination path.

## 2026-06-24 - Team Add-on Manager Fallback Active Lease Guard

- Suppressed manager fallback wakes while another active team member still has
  an unexpired wake lease, avoiding oversight prompts for work already in
  progress.
- Applied the active-lease guard before agent-specific wake scoping so targeted
  manager wake runs follow the same fallback suppression.

## 2026-06-24 - Team Add-on Revision Conflict Recovery

- Added Team state resource tokens and opt-in unchanged-target rebasing for
  operator UI writes so unrelated agent revision bumps no longer force common
  Team Add-on form saves to fail.
- Included agent project assignments and context-clearing settings in agent
  retry tokens, and rejected stale generated-id creates instead of duplicating
  records with suffixed ids.
- Preserved Team UI form drafts across conflict reloads and added a retry-save
  action after revision conflicts so operators can resubmit without re-entering
  form data.

## 2026-06-24 - Team Add-on Fallback Review Routing Fixes

- Preserved compact artifact and Git evidence for pending-review deliverables
  in manager fallback summaries, and excluded paused, leased, or stood-down
  reviewers from fallback routing-gap recommendations.
- Made fallback reviewer eligibility explicit through review profiles, added a
  guardrail against manager self-review when an idle profile-eligible reviewer
  can take the review, and improved duplicate review-id recovery hints.
- Fixed fallback review routing edge cases for blank review-profile role filters,
  busy reviewers, and reviewers who already handled the deliverable.

## 2026-06-24 - Team Add-on Manager Fallback Summary

- Replaced the manager fallback wake context dump with a compact summary of
  active team work, assignments, review routing, and routing gaps so managers
  get enough state to act without oversized wake prompts.

## 2026-06-24 - Team Add-on Chat Wake Retry

- Re-emitted queued Team chat wake events when an agent finishes its current
  Team dispatch, so chat messages queued while the agent is leased wake
  promptly instead of waiting for the cron fallback.

## 2026-06-24 - Local OpenAI Websocket Rejection

- Rejected `local_openai` websocket/realtime relay requests before dialing the
  upstream Ollama-compatible endpoint, preventing unsupported websocket upgrades
  from surfacing as retryable relay 500s during Codex startup.

## 2026-06-24 - Team Add-on Manager Fallback Context

- Added team-scoped manager fallback wake context with open team tasks,
  deliverables, pending reviews, related inbox items, action records, and runtime
  attention signals so stalled teams can be routed from the fallback prompt.
- Added manager fallback prompt guidance to use that team context instead of
  returning a no-op when the manager has no personally owned tasks or inbox.
- Generated fallback runtime attention snapshots from the lease timestamp so
  explicit scheduler times and prompt runtime state stay consistent.

## 2026-06-24 - Team Add-on Chat Status

- Stopped showing a separate queued-success status card after operators send
  Team chat messages while keeping dispatch-failure warnings visible.
- Published a Team chat queued event so chat sends trigger the async Team wake
  dispatcher immediately instead of waiting for the next 15-minute runtime tick.

## 2026-06-24 - Team Add-on Projects Tab

- Added a top-level Team Add-on Projects tab that centers project creation,
  selection, details, goals, tasks, deliverables, releases, agents, and roles.
- Added project-scoped review-profile creation from the selected project while
  preserving existing project visibility and editing under Teams and Planning.

## 2026-06-24 - Team Add-on Review Profile Criteria Editor

- Replaced the Team Add-on review-profile criteria textarea with structured
  criteria rows for label, description, optional key, and required status.
- Reused the structured criteria editor in review-profile edit dialogs while
  preserving the existing Admin API criteria payload shape.

## 2026-06-24 - Team Add-on Manager Fallback Wake Serialization

- Serialized pending manager inbox items in fallback wake leases, preventing
  prompt construction from crashing when throttled managers are woken for team
  oversight while holding non-wakeable inbox items.
- Tightened the manager fallback wake regression test so it explicitly creates
  a waiting-review task and verifies the fallback lease remains JSON-safe.

## 2026-06-23 - Team Add-on Chat Dispatch Queueing

- Queued built-in Team chat sends for asynchronous dispatch instead of resuming
  the target agent inline from the Starlette request path, preventing powered-off
  VM dispatch attempts from freezing Admin Web.

## 2026-06-23 - Team Add-on Org Chart Inbox Badge Hover

- Restored hover and focus popovers for status-request and progress-report
  badges in the Team Add-on ReactFlow org chart.
- Kept those org-chart badge popovers scrollable without triggering ReactFlow
  canvas panning.

## 2026-06-23 - Team Add-on Duplicate Wake Suppression

- Stopped Team agents from being scheduled back onto `waiting_for_review`
  tasks after they have already submitted an active deliverable.
- Changed Team wake eligibility so pending inbox items with
  `response_requested=false` stay visible but do not wake the recipient.
- Aligned runtime Operations `wake_due` badges with the same deliverable and
  no-reply inbox wake rules.

## 2026-06-23 - Team Add-on Action Handoff Hardening

- Reseeded Team action contract files on every persistent wake and avoided
  stale VM-IP metadata when copying Team guidance or action contract files.
- Added a per-dispatch `team-finish` helper in Team agent sessions to fill
  `schema_version`, `agent_id`, and `dispatch_id` before writing
  `/artifacts/team-action.json`.
- Added a one-shot missing-action recovery prompt that keeps the active Team
  lease open for the same dispatch before falling back to the normal failure
  path.
- Cleared Team runtime leases and recorded the original missing-action failure
  when a one-shot recovery resume cannot be dispatched.
- Rejected self-superseding review-rework deliverable IDs before mutating Team
  state.
- Tightened wake prompts with explicit deliverable staging, relative
  `artifact_paths`, and owned `task_id` guidance.

## 2026-06-23 - Team Add-on Expired Session Visibility

- Surfaced expired Team agent sessions in Team Add-on runtime operations,
  scoped Team state queries, and wake prompt context so leads and agents can
  escalate stale member sessions.
- Kept multi-project expired-session attention visible in each assigned
  project scope and expanded wake prompt session health to visible members.
- Added Team Add-on UI indicators for expired sessions in the Operations view
  and agent details.

## 2026-06-23 - Agent Create Session TTL

- Added a Session TTL duration field to the Admin Web Agents create dialog,
  defaulting to `7d` and accepting seconds, minutes, hours, days, and weeks.
- Added `ttl_seconds` support to agent creation so UI-created sessions can use
  precise duration values while existing `ttl_hours` callers continue to work.

## 2026-06-23 - Discord Target Cursor Fast-Forward

- Fast-forwarded per-session Discord inbox cursors when a Discord communication
  target becomes wake-eligible for an agent, preventing historical channel
  messages from waking newly created or newly updated agents.

## 2026-06-23 - Team Add-on Chat Lease Retry

- Queued Team Add-on chat messages when the target agent already has an active
  Team dispatch, instead of marking the operator message failed immediately.
- Retried queued Team chat messages from the wake cycle once the agent's active
  Team lease is released, giving queued chat precedence over ordinary due work.
- Kept queued Team chat retries behind the wake loop limit and Team cost-cap
  guard so they do not bypass paused or over-cap wake configurations.

## 2026-06-23 - Team Add-on Review Rework Semantics

- Stopped rejected reviews from marking tasks and projects as blocked; rejected
  deliverables now return their task to in-progress rework.
- Added a review-rework inbox item for the deliverable producer so findings are
  handed back to the implementer while true blocked reviews still use blocker
  handling.
- Added explicit deliverable supersession for review rework so accepted revised
  deliverables can complete a task without losing rejected-deliverable history.

## 2026-06-23 - Team Add-on Role-Aware Wake Context

- Added subordinate role summaries to Team wake prompts so managers can see
  direct reports' role names, descriptions, permissions, and project context
  before assigning tasks.
- Added role-aware delegation guidance to reduce implementation tasks being
  assigned to reviewer-role agents.

## 2026-06-23 - Team Add-on Chat Load Performance

- Optimized the Team Add-on Chat load path so the conversation list can inline
  the selected thread's first message page, letting the client render a usable
  chat view without a redundant detail request.
- Grouped conversation messages once while building chat collection payloads so
  large chat histories no longer rescan all messages for every conversation row.
- Preserved the current Chat conversation selection when a refresh response
  arrives after the operator has selected a different conversation.

## 2026-06-23 - Workflow Editor Undo Review Fixes

- Limited workflow editor undo snapshots to editable workflow content so
  validation diagnostics, output schemas, and revision-only refreshes do not
  create or restore undo history entries.
- Added smoke coverage proving validation metadata refreshes leave undo disabled
  and save revision refreshes clear undo availability.

## 2026-06-23 - Team Add-on Governance Membership Wakes

- Recorded governance member-team additions and removals as pending
  `policy_decision` agenda items so governance managers wake with fresh member
  summaries after their oversight scope changes.

## 2026-06-23 - Team Add-on Context Clearing Policy

- Added per-agent Team Add-on context clearing policies: `off`, `idle`, and
  `every_wake`, with legacy automatic context clearing preserved as `idle`.
- Updated Team wake leasing so `every_wake` starts each otherwise-eligible Team
  wake with a fresh context without bypassing low-priority batching or waking
  idle agents.

## 2026-06-23 - Team Add-on Agent Edit Project Selection

- Fixed the Team Add-on agent edit dialog so the Project select is populated
  from assignment-backed `project_ids` and `project_assignments` instead of the
  removed legacy agent `project_id` field.
- Stopped unchanged agent edit saves from sending `project_id`, preserving
  multi-project memberships during unrelated edits.

## 2026-06-23 - Team Add-on Permission Help

- Added Team Add-on permission tooltips for role permission options and saved
  role permission labels, including the `request_release` release-package
  authorization.

## 2026-06-23 - Team Add-on Git Repo Import

- Added a Team Add-on Git import action that creates or refreshes a
  control-plane bare mirror from a host repo path or canonical URL and fills the
  project Git config fields in setup, create, and edit forms.
- Exposed the import through a Team Add-on API endpoint backed by the existing
  host Git mirror setup semantics.
- Blocked refreshes into existing bare mirrors that already contain
  `refs/heads/agents/**` work, and moved the import operation off the Admin Web
  event loop.
- Preserved local `refs/heads/agents/**` and `refs/heads/review/**` output refs
  during import mirror refreshes while still pruning stale source branches.

## 2026-06-23 - Team Add-on Governance Agenda Handling

- Added a governance-manager MCP tool that lets active governance managers mark
  agenda items handled for groups they manage.
- Granted the tool during governance wakes and updated guidance so managers can
  close agenda items after deciding or routing follow-up.

## 2026-06-23 - Team Add-on Governance Org Labels

- Removed the extra `Manager` prefix from governance nodes in the Team Add-on org chart so governance managers display by their agent label only.

## 2026-06-23 - Team Add-on Project Assignment Model

- Replaced direct agent project membership with explicit project assignment records so one team agent can participate in or lead multiple projects.
- Added assignment-aware effective permissions to Team Add-on wake payloads and guidance so project leads are told they can create top-level tasks.
- Added local SQLite backfill from legacy agent project membership and project lead fields into project assignments.

## 2026-06-23 - Team Add-on Project Lead Overlay

- Allowed project leadership to be assigned as a project-level overlay so a team
  manager can also lead a project without losing manager role or reporting
  semantics.

## 2026-06-23 - Team Add-on Teams Tab Creation

- Added inline Teams-tab creation for projects, agents, and roles scoped to the
  selected team, reusing the existing Setup create endpoint semantics without a
  visible Team selector.

## 2026-06-23 - Agent Create Operation Safety

- Kept async agent-create request payloads in Admin Web process memory only so
  operation status records no longer persist or return arbitrary submitted JSON.
- Moved abandoned create-operation recovery out of generic AdminService
  construction and tied it to Admin Web startup with operation owner liveness
  checks.

## 2026-06-23 - Team Add-on Existing Project Leads

- Changed Team Add-on project creation so the UI assigns an existing registered
  team agent as project lead instead of collecting new lead session, display,
  or role-name fields inline.

## 2026-06-23 - Async Agents Page Create Operations

- Added reload-safe Admin Web create-operation tracking so multiple agent creates
  can be submitted from the Agents page while earlier VM or container setup is
  still running.
- Preserved the existing synchronous agent-create API while adding polling
  endpoints and UI status for queued, running, succeeded, and failed creates.

## 2026-06-23 - Team Add-on Governance Escalation Validation

- Validated optional manager escalation `project_id` values before creating
  governance agenda items so typoed or stale project IDs become action errors
  instead of aborting the apply-agent-action effect.

## 2026-06-23 - Team Add-on Stand-Down Prompt

- Allowed Admin Web add-on iframes to open modal prompts so the Team Add-on
  stand-down action can collect its required reason instead of failing with a
  missing-reason error.

## 2026-06-23 - Local Multi-User Admin Password Auth

- Broadened Admin Web `local_password` authentication to support multiple local users with
  per-user hashed passwords managed through Users & Groups, while preserving the configured
  username/password as first-start bootstrap and break-glass access.
- Updated local password sessions, user payloads, and docs so Admin Web identity, permissions,
  and credential state reflect the actual signed-in local user.

## 2026-06-23 - Managed DIDWW Asterisk Deployment

- Added an opt-in Ansible role that installs Asterisk, renders ARI/RTP/PJSIP/dialplan
  configuration for the DIDWW SIP bridge, and verifies local ARI plus the managed DIDWW endpoint
  before the bridge service is checked.
- Documented the Vault-backed DIDWW SIP variables, Azure NAT settings, DIDWW source CIDRs, and the
  relationship between the Python bridge env and managed Asterisk ARI/PJSIP names.
- Kept the Azure Vault example as blank template values while rendering required bridge secrets
  into `secrets.env`, and made systemd service reconciliation tolerate older service-toggle maps.
- Fixed the managed Asterisk `s` fallback extension so configured inbound DIDWW routes are passed
  to the bridge instead of treating the fallback extension name as the route ID.

## 2026-06-22 - Admin Login Oversized Branding

- Doubled the desktop Admin Web login page branding dimensions again and
  adjusted the login-page top spacing and mobile brandbar layout so larger
  environment-specific logos have room without changing the in-app header.

## 2026-06-22 - Admin Login Branding Scale

- Increased the Admin Web login page logo sizing so environment-specific
  product and company branding has stronger presence on the unauthenticated
  landing page without changing the in-app header logo.

## 2026-06-22 - Admin Web Branding Asset Deployment

- Added Ansible variables and service-config tasks for copying operator-owned
  Admin Web branding image files into the configured runtime branding asset
  directory, so environment-specific deployments can use local logo sets without
  changing bundled product assets.

## 2026-06-22 - Ansible Current Service Config

- Updated the Ansible deployment bundle to render Teams meeting ingest and
  DIDWW SIP bridge config files expected by systemd preflight, manage their
  systemd units through service toggles, and hide secret-bearing env template
  task output.
- Routed the bootstrap `uv` pip install through the configured Python package
  environment so private worker VMs can use the control-plane package cache.

## 2026-06-22 - ADO Worker Exact Directory Polling

- Changed ADO worker directory matching for PR-map feedback and completion
  streams to require exact directory paths, so configuring a parent directory no
  longer implicitly observes child-directory sessions.

## 2026-06-22 - Team Add-on Org Chart Edge Fix

- Restored ReactFlow org-chart relationship edges by rendering hidden custom-node
  handles, and widened the graph layout so governance, Team, root-agent, and
  sibling-agent cards no longer overlap.

## 2026-06-22 - Trigger Cursor File Locking

- Added process-level file locking around trigger cursor JSON stores so ADO,
  Teams, Discord, ticket, and other trigger cursor updates cannot lose writes
  when multiple worker processes or operator tools touch the same cursor file.

## 2026-06-22 - Team Add-on Org Chart Canvas

- Replaced the focused Team Add-on Org chart tab with an add-on-owned
  ReactFlow canvas while preserving agent avatars, runtime indicators, work
  summaries, hover details, and edit interactions.
- Added governance group nodes above the member Teams they oversee, including
  related governance visibility when filtering the org chart to one Team.

## 2026-06-22 - Team Add-on Runtime Due Badge

- Fixed Team Add-on runtime payloads so idle agents with `next_wake_at = null`
  are not shown as due unless they also have real wake-eligible work such as
  pending inbox, action feedback, context rotation, or ready task work.

## 2026-06-22 - Team Add-on Governance Directive Tool

- Added an add-on-owned `team_create_operator_directive` MCP tool so governance
  managers can route durable directives to member-team managers from governance
  wakes without reintroducing Team-specific core gateway tool ownership.
- Governance wakes now grant the directive MCP tool to the governance manager's
  session before resuming it.
- Allowed active governance managers to create operator directives for Teams in
  their governance group while preserving manager-inbox-only delivery.
- Rejected governance-manager directive requests after the governance manager
  agent becomes paused or archived, even if its MCP session is still valid.

## 2026-06-22 - Test Suite Expectation Refresh

- Updated stale Admin Web, CLI, VM delegation, Discord inbox, and PR feedback
  tests to match current composed workflow documents, Team chat permissions,
  session provider overrides, and retention behavior.

## 2026-06-22 - Team Add-on Cost Attribution Guard

- Persisted Team Add-on model-usage attribution at wake dispatch time so moving
  agents between Teams or Governance does not rewrite historical spend.
- Registered Team wake attribution before resuming agents so the first relay
  request in a same-context wake can be correlated.
- Made Team cost aggregation prefer persisted usage/correlation attribution,
  including transcript rows that supersede relay rows for Anthropic pricing.

## 2026-06-22 - Team Add-on Per-Operator Chat

- Scoped Team Add-on built-in chat conversations to one human operator and one
  agent instead of one global conversation per agent.
- Added read-only cross-operator chat inspection for users with
  `team_chats.read_all`, plus explicit human-operator identity in agent chat
  prompts and action metadata.
- Added `operator_chat_messages[]` to the Team action contract so agents can
  send a new Team Chat message to a known human operator.
- Filtered Team state payload chat data for operators without
  `team_chats.read_all`, and normalized migrated legacy chat operator IDs.
- Normalized already-migrated Team chat operator IDs so existing mixed-case
  chats remain visible under lower-case Admin Web logins.

## 2026-06-22 - Admin Web Local User Permissions

- Fixed local-password Admin Web permission checks so mixed-case configured
  usernames retain built-in owner permissions after login.

## 2026-06-22 - Team Add-on Chat Focus Preservation

- Preserved the Team Add-on chat composer draft and focus across live
  conversation rerenders so operators can continue typing while chat snapshots
  stream in.

## 2026-06-22 - Team Add-on Governance Agent Guidance

- Added durable managed `AGENTS.md` guidance for Team Add-on governance
  managers, including teamless governance managers, and seed it before
  governance wakes.
- Superseded normal Team-agent `AGENTS.md` guidance for teamless governance
  managers with an inactive notice so stale task/action instructions do not
  apply.
- Cleared inactive guidance metadata when normal Team guidance is seeded again,
  preventing repeated reseeds after a governance-only agent rejoins a Team.

## 2026-06-22 - Team Add-on Manager Reassignment

- Added Team manager reassignment through the Team update API and edit UI,
  moving existing direct reports to the new manager while preserving the old
  manager as an active Team agent.
- Preserved team-wide manager authority when a project-scoped lead is promoted
  to Team manager.

## 2026-06-22 - Team Add-on Teamless Agent Review Fixes

- Kept terminal task ownership as historical attribution when detaching agents
  from teams while preserving active-task team ownership validation.
- Restored read-only Team fields on scoped edit forms whose save paths do not
  support team reassignment.

## 2026-06-22 - Team Add-on Teamless Agents

- Allowed Team Add-on persistent agents to be registered without a Team, project,
  role, or reporting parent, while preserving metadata, runtime records, avatars,
  voice profiles, and chat history.
- Added guarded Team removal for agents so operators can detach unreferenced
  agents without deleting their record, with blocking errors for structural Team
  references that must be reassigned first.

This project does not currently use release tags. The historical sections below
were reconstructed from the `main` history through `0680fb85` on 2026-05-04,
using non-merge commit messages and changed-file clusters as the source
material. Newer entries are added incrementally as changes land.

## 2026-06-22 - Team Add-on Teams Tab Team Creation

- Added a top-level Teams tab `Create team` action with an in-tab team creation
  form that reuses the existing Team Add-on `/teams` API payload.

## 2026-06-22 - Team Add-on Navigation Persistence

- Fixed Team Add-on top-level navigation so the selected view is remembered
  across page refreshes.

## 2026-06-22 - Team Add-on Teams Tab Headers

- Changed Team Add-on Teams tab record groups so the full team header toggles
  expansion, removed the separate caret indicator, dropped the header divider,
  kept header hover styling local to the Teams tab surface, and removed the
  extra parent panel around the team groups.
- Left-aligned the Teams tab selected-agent detail header's name, role, and ID
  block beside the avatar.
- Moved governance group creation out of the Setup tab into a top-level
  Governance tab button and inline create panel.

## 2026-06-21 - Team Add-on Teams Tab Collapsible Lists

- Changed the Team Add-on Teams tab so each team starts collapsed and expands into a single active records tab for agents, projects, or roles.
- Added per-team record paging controls to keep long team lists compact.

## 2026-06-21 - Team Add-on Chat Virtual Scrolling

- Bounded the Team Add-on Chat panel so long histories scroll inside the chat
  message list, open at the latest message, and load older messages on upward
  scroll instead of rendering the full transcript at once.
- Removed the redundant global success banner after sending a chat message.

## 2026-06-21 - Team Add-on Chat Revision Safety

- Kept partial Team Chat live-update snapshots from advancing the page-wide
  Team state revision, preserving optimistic conflict checks for non-chat edits.

## 2026-06-21 - Team Add-on Chat Live Updates

- Added a Team Chat conversation event stream and subscribed the Chat tab to
  selected-conversation snapshots so agent replies appear without a page reload.

## 2026-06-21 - Team Add-on Chat Lifecycle Fixes

- Fixed Team Chat reply-application errors so the source operator message is
  marked failed instead of staying running after the dispatch lease is cleared.
- Made chat-linked browser voice-call history idempotent for retried
  `request_id` values, and synced Team UI revisions after chat mutations.

## 2026-06-21 - Team Add-on Teams Tab Archived Counts

- Fixed Teams tab record counts so active projects, roles, or agents owned by
  hidden archived teams are not included while archived records are hidden.

## 2026-06-21 - Team Add-on Chat Tab

- Added a Team Add-on Chat tab with recent per-agent conversations, operator
  message dispatch, persistent agent `chat_replies`, and chat-linked browser
  voice calls.
- Fixed dynamically rendered chat composers so Send uses the chat dispatch flow,
  and reopened existing conversations without Team state revision churn.

## 2026-06-21 - Team Add-on Task Board

- Added a Team Add-on Task board tab with Team/Project selection, Planned,
  In Progress, Reviewing, and Done lanes, compact assignment cards, blocked
  card highlighting, and exact task lane-entry timestamps.

## 2026-06-21 - Team Add-on Governance Groups

- Added Team Add-on governance groups for portfolio-style managers that oversee
  separate member teams without changing those teams' internal reporting chains.
- Added governance agenda items, advisory group cost rollups, and a separate
  governance-manager wake effect for agenda, cost, blocker, and check-in
  signals.
- Added Governance UI forms and a focused Governance tab with member-team,
  agenda, goal, and cost-rollup views.

## 2026-06-21 - Team Add-on Governance Review Fixes

- Fixed governance manager leasing to avoid double-dispatching managers that
  already have active agent runtime leases.
- Changed governance cost-cap and blocked-team signals to wake only on status
  changes instead of repeatedly waking for unchanged conditions.
- Tightened governance agenda scoping so project and agent references must
  resolve to member teams, and removing a member team handles affected agenda.

## 2026-06-21 - Admin Web Wordmark Header Branding

- Added Admin Web branding controls for hiding the visible shell header title
  and using a wider wordmark-sized header logo.

## 2026-06-21 - Team Add-on People List Cleanup

- Simplified the Team Add-on People tab list to show each agent's avatar,
  display name, and role only, keeping status, inbox, and work details in the
  selected-agent detail pane.
- Sorted People tab agents by team and display name so the list is easier to
  scan across multi-team workspaces.
- Added People tab team filters so operators can switch between an all-team
  list and per-team agent lists.

## 2026-06-21 - Team Add-on Teams Tab Records

- Renamed the focused People tab to Teams and grouped team-specific agents,
  projects, and roles under their owning teams.
- Added selectable project and role rows to the Teams tab with detail-pane views
  alongside the existing selectable agent detail flow.

## 2026-06-21 - Team Add-on Agent Identity Guidance

- Added each persistent Team agent's current ID and display name to the managed
  `/home/agent/AGENTS.md` Team guidance section.
- Made Team guidance reseed when an agent's display name changes while
  preserving the rest of `AGENTS.md` through the existing managed-section merge.

## 2026-06-21 - Team Add-on Voice Profile Review Fixes

- Kept Team agent voice profiles out of regular wake, ephemeral, and agent-visible
  Team state payloads while leaving them available in Admin/browser-call metadata.
- Fixed the People tab voice panel so configured agent voices and speaking styles
  are shown instead of always falling back to the default label.

## 2026-06-21 - Team Add-on Cost Warning Fix

- Fixed Team Add-on draft restoration so avatar upload file inputs no longer
  surface browser file-input security exceptions as estimated cost warnings.

## 2026-06-21 - Team Add-on People Row Alignment

- Left-aligned Team Add-on People tab agent names, roles, and IDs directly
  after their avatar column so rows no longer appear staggered by badge width.

## 2026-06-21 - Team Add-on Agent Voice Profiles

- Added per-agent Team Add-on voice profiles with built-in OpenAI Realtime
  voice selection and spoken delivery instructions for browser direct agent
  calls.
- Persisted voice profiles in add-on state and included the selected voice and
  style in voice-session metadata without exposing them to agent runtime
  prompts.

## 2026-06-20 - Team Add-on Direct Agent Voice Calls

- Added browser WebRTC voice calls from the Team Add-on People tab to selected
  persistent agents, backed by OpenAI Realtime ephemeral client secrets instead
  of DIDWW, Asterisk, or SIP.
- Projected the selected agent's current team, role, task, inbox, review,
  deliverable, runtime, and visible tool context into the voice session.
- Persisted direct browser calls in the shared voice-session store, scheduled
  reconciliation wakes when they end, and wired durable `query_origin_agent`
  delegation back to the originating text agent.

## 2026-06-20 - Team Add-on Agent Avatars

- Added optional Team Add-on agent avatars with add-on-private WebP storage,
  upload normalization, generated-preview acceptance, and removal routes.
- Added OpenAI-backed generated avatar previews for persistent Team agents
  without exposing avatar metadata to agent runtime prompts or core Truman
  session identity.
- Rendered avatars with initials fallbacks in Team agent lists, People details,
  org-chart nodes, and agent edit dialogs.

## 2026-06-20 - Skatteverket Connection Editor

- Added editable Skatteverket Add-on Admin UI controls for connection metadata,
  secret environment-variable names, submission endpoint, and enabled APIs.
- Added hover/focus help and explicit API-gateway client credential fields for
  Skatteverket connections so operators can see what each setting expects
  without leaving the page.
- Report missing Skatteverket secret references as the configured environment
  variable names in connection status payloads.

## 2026-06-20 - Add-on Dark Mode Polish

- Matched the bundled Bokio and Skatteverket add-on iframe UIs to the Admin Web
  dark theme so their pages no longer render as bright light panels in dark mode.

## 2026-06-20 - Per-Agent Model Relay Overrides

- Added session-level `model_provider_mode` overrides so Codex agents in the
  same directory can choose public OpenAI, local OpenAI-compatible, Azure
  OpenAI relay routing without splitting directory scopes.
- Exposed the override in Create Agent as a Codex-focused **Model relay** field,
  added CLI/workflow creation support, and documented same-directory public vs
  local OpenAI routing.
- Wired the relay override through legacy `effect:vm:create` workflows and kept
  Anthropic out of Codex-facing relay selectors; Claude agents still route via
  the Anthropic relay by selecting the Claude agent provider.

## 2026-06-20 - Team Add-on People Agent Details

- Split the focused People tab into a two-column desktop layout with selectable
  agent cards on the left and selected-agent details on the right.
- Kept selected-agent inbox hover popovers clamped inside the viewport on the
  People tab.
- Kept scrollable selected-agent inbox popovers reachable while moving from the
  icon into the popover body.
- Removed the redundant selected-agent panel title so the selected agent name is
  the detail heading.
- Stopped showing the successful-load revision status card because the header
  revision badge already carries that state.
- Let People-list inbox popovers overflow their agent row instead of being
  clipped by the compact card.
- Clamped inbox popovers with real layout positioning so edge-aligned detail
  tooltips no longer introduce horizontal page scrolling.
- Applied the same clamp offset to the tooltip hover bridge so the bridge itself
  cannot create horizontal overflow.

## 2026-06-20 - ADO Activity And Buffer Gate Fixes

- Applied ADO work-item activity pagination after actor/activity filtering so
  early non-matching WIQL candidates no longer create empty or shifted pages.
- Based buffer-gate timeout windows on when an activation is buffered while
  preserving each item's original upstream occurrence timestamp.

## 2026-06-20 - Team Wake Guidance Seeding

- Seeded static Team Add-on operating rules into a managed
  `/home/agent/AGENTS.md` section during persistent wakes, leaving the repeated
  wake prompt focused on dynamic dispatch context and action artifacts.
- Moved the top-level task creation mandate into dynamic wake prompts based on
  each agent role's `create_root_task` permission.
- Added managed-section file writes for agent/VM copy-file effects and container
  worker session file writes.

## 2026-06-20 - Bokio Add-on Safety Fixes

- Made registered add-on MCP tools fail closed in the gateway before dispatch
  when the caller lacks a session, session allowlist entry, or valid workflow
  tool-approval lease.
- Consumed workflow tool-approval lease uses after separately approved
  invocation-gated calls so lease `max_uses` is exhausted correctly.
- Restricted Bokio document uploads to add-on-owned staging directories and
  enforced PDF, JPEG, and PNG content checks before the control plane opens a
  file for upload.
- Namespaced Bokio upload staging by caller type so session IDs cannot collide
  with reserved Admin API or workflow upload scopes.
- Fixed Bokio workflow providers to resolve the full directory provider config
  so `provider_state.bokio.*` environment overrides take effect.

## 2026-06-19 - Bokio Add-on

- Added the bundled `evander_bokio_addon` package with Admin API/UI routes,
  optional workflow providers, host-side MCP tools, and prepared-action state
  under `runtime/addons/bokio-addon/`.
- Added Bokio private-integration client support for company info, chart of
  accounts, journal entries, invoices, supplier invoices, uploads, SIE download,
  and bank payments while keeping integration tokens in host environment
  variables.
- Allowed session, directory policy, and workflow-run MCP approval leases to
  carry add-on MCP tool names without adding Bokio-specific tool names to core.

## 2026-06-19 - Skatteverket Add-on

- Added the bundled `evander_skatteverket_addon` package with add-on-owned
  Admin API/UI routes, Skatteverket API catalog metadata, private SQLite state,
  workflow providers, and host-side MCP tools.
- Added a generic add-on MCP tool registration seam so enabled add-ons can
  expose policy-gated tools without adding domain-specific tool names to core.
- Implemented approval-bound Skatteverket draft submission flow that ties
  operator approval to the exact stored draft revision.

## 2026-06-19 - Workflow Catalog Loader Resilience

- Kept the Admin Web workflow node catalog available when the saved
  trigger-orchestrator workflow contains invalid nodes by reading only provider
  and add-on loader settings for catalog discovery.

## 2026-06-19 - Add-on Workflow Catalog Entries

- Allowed workflow provider registrations, including add-on-owned providers, to
  contribute Admin Web workflow-editor node catalog entries.
- Added Team Add-on catalog entries for its `effect:team:*` and `query:team:*`
  nodes so they appear in the workflow editor only when the add-on is enabled.

## 2026-06-19 - Buffer Workflow Gate

- Added `gate:buffer`, a stateful workflow gate that batches activations by
  bindable key and releases `items`, `count`, and `release_reason` by count or
  optional timeout.
- Exposed Buffer in the Admin Web workflow catalog and editor Timing group with
  fixed downstream binding outputs.

## 2026-06-19 - Local OpenAI Model Backend

- Added `local_openai` as a relay model provider mode for host-local
  OpenAI-compatible endpoints such as Ollama, with configurable upstream URL,
  placeholder API key, and usage provider name.
- Kept Codex sessions on the existing session-scoped OpenAI relay while allowing
  local traffic to avoid host API key env-var requirements and record usage as
  `local_openai`.
- Rejected `local_openai` effective configs whose upstream URL points back at
  the relay listener, including the default `0.0.0.0:11434` relay bind with the
  default Ollama URL.
- Documented the Ollama `qwen3-coder:30b` setup path and local-provider
  directory config fields.

## 2026-06-19 - Workflow Pack Selection

- Made Admin Web workflow editing pack-scoped when multiple workflow packs are
  configured, with pack metadata returned by the document API and selected-pack
  saves updating only that pack and its layout.
- Kept runtime workflow run/report permission lookups on composed workflow
  metadata so authoring pack selection does not narrow operational views.
- Added a workflow-pack selector to the workflow editor save panels and sent the
  selected pack on validate/save requests.
- Used the selected workflow pack reference when generating read-only workflow
  node IDs so multi-pack configs do not fall back to colliding generic IDs.
- Preserved composed workflow reads for omitted-pack service callers, required
  explicit pack saves for multi-pack configs, and validated selected-pack edits
  against sibling packs.
- Exposed workflow-pack selection on the `assistant_create_workflow` MCP tool so
  public workflow saves can target a pack in multi-pack deployments.

## 2026-06-19 - ADO Activity Pagination

- Fixed ADO PR activity reporting pagination so closed/multi-status PR lists page after status
  merging, and PR comment/commit activity tools page after scanning candidate pull requests.

## 2026-06-19 - Generated Operator IDs

- Generated stable Team Add-on IDs from names when operator create/template/API
  payloads omit IDs, while preserving explicit API IDs for compatibility.
- Removed editable Team Add-on and workflow-editor node ID fields from normal UI
  flows, leaving generated IDs visible as read-only metadata.
- Rejected workflow-pack node, graph, webhook slug, and cross-pack edge
  collisions during workflow IR composition.

## 2026-06-19 - Team Add-on Priority Batching

- Added Team inbox priorities and low-priority wake-cycle batching so FYI-style
  messages can wait for a configurable number of Team wake evaluations unless
  another wake reason delivers them earlier.

## 2026-06-19 - ADO Personal Activity Reporting

- Added host-side Azure DevOps personal activity reporting tools for resolving ADO identities,
  listing PR comment and commit activity by author/date, querying work-item created/changed/commented
  activity, and listing closed PRs through a `closed` status alias.

## 2026-06-19 - Team Add-on Reply Intent

- Added durable `response_requested` metadata to Team Add-on inbox items so
  agents can suppress unnecessary acknowledgements for FYI-style messages while
  still requesting replies for questions, status requests, and action-oriented
  notices.

## 2026-06-17 - Team Add-on Boundary Cleanup

- Removed the Team-specific manager directive MCP tool, Team metadata policy
  special-case, and Team guidance from the core Discord wake prompt so
  operator-directive behavior stays on add-on-owned Admin API and workflow
  effect surfaces.

## 2026-06-17 - Email Send And Communication Integration

- Implemented real email draft sending for JSON-store, Gmail, and Microsoft Graph
  providers, with host-recorded draft snapshots validating mailbox, session,
  recipients, subject, and attachment IDs before send.
- Added neutral `email_send` and `email_send_with_attachments` MCP tools while
  retaining the older approved-tool names as compatibility aliases.
- Switched email triggers to configured mailbox provider definitions and added
  email support to generic communication message query, trigger, and send flows.
- Fixed review follow-ups so email attachment sends enforce exact file-send
  approval, Gmail draft sends use the provider `drafts/send` endpoint, and
  trigger/query email reads do not advance durable Gmail/Graph cursors before
  workflow acknowledgement.
- Blocked plain email sends for drafts with attachments, split email attachment
  file-send approval IDs from generic MCP approval IDs, and exposed email targets
  in generic communication workflow pickers.

## 2026-06-17 - Team Add-on Timeline UTC Sorting

- Kept Team Add-on timeline rows in server order for legacy offset-naive ISO
  timestamps by treating those timestamps as UTC in the static UI sort path.

## 2026-06-17 - Team Add-on Release And Revision Hardening

- Allowed artifact-only Team release packages to be created without project Git
  configuration, leaving release/canonical branches unset and emitting no Git
  promotions.
- Required `expected_revision` on Team Add-on Admin API write routes while
  keeping workflow effects able to omit it for single-writer runtime steps.
- Clarified Team cost caps as estimated operational wake guards and surfaced the
  estimate language in Team UI copy.

## 2026-06-17 - Team Add-on Focused Tab Follow-Ups

- Added task-status filters to the focused Planning tab, defaulting to current
  non-terminal work while letting terminal task states be shown on demand.
- Made focused People and Operations rows compact and collapsible, removed the
  duplicate Hierarchy panel from focused People, and added smoke coverage for
  two-team Org chart rendering.
- Added Org chart team tabs so operators can switch between the aggregate view
  and a single team's reporting tree.

## 2026-06-17 - Team Add-on Focused Tab Density

- Sorted the derived Team Add-on timeline newest-first and paged the focused
  Timeline tab into compact expandable rows.
- Collapsed focused Delivery and Planning records into summary-first drill-down
  cards while keeping the existing Workspace tab layout unchanged.

## 2026-06-17 - Team Add-on Batched Task Mandates

- Enforced ordinary-agent task mandate checks against batch-local parent tasks
  so agents cannot create child tasks under newly-created subordinate-owned
  parents in the same action.

## 2026-06-17 - Team Add-on Focused Workspace Tabs

- Added focused Team Add-on tabs for overview, operations, delivery, people,
  planning, setup, and timeline workflows while keeping the existing Workspace
  tab available as the all-in-one view.
- Reused the existing Workspace renderers and setup forms across the focused
  tabs so runtime filters, review decisions, edit controls, timeline filters,
  and setup submissions keep the same behavior.

## 2026-06-17 - Team Add-on Release Promotion Guidance

- Made `git_release_requests` failures for accepted Git deliverables without
  `git_refs.feature_branch` explain the required review-to-feature promotion
  step before retrying release publication.
- Added matching wake-prompt and action-feedback guidance so release operators
  do not retry canonical release publication from raw agent or review branches.

## 2026-06-17 - Team Add-on Stand-Down And Task Mandates

- Added `create_root_task` role permission and granted it to manager and
  project-lead roles, while requiring ordinary agents to create child tasks
  under their own active parent tasks.
- Added operator stand-down/revoke support for Team agents and subordinate
  subtrees, with wake leasing skips and apply-time rejection for already-running
  dispatches that submit Team actions after the hold lands.
- Added `stand_downs` and `stand_down_revocations` to the Team agent action
  contract so direct superiors can issue structured holds instead of prose-only
  status requests.
- Surfaced stand-down state and controls in the Team Admin UI.

## 2026-06-17 - Team Add-on Idle Context Wake Loop

- Stopped status-report-only idle context rotations from immediately re-arming
  automatic context clearing and repeatedly waking agents with no assigned work.
- Made disabling automatic context clearing clear already-pending idle rotations
  while preserving manual context-rotation requests.

## 2026-06-17 - Team Add-on Header Indicators

- Added persistent-agent card header indicators for pending status requests and
  progress reports, using down/up speech-bubble icons with per-item hover
  bubbles for each matching inbox item.
- Made teams blocked by the lifetime cost cap explicit in Team UI headers with
  a `Blocked: cost cap reached` notice and cost-blocked status pill.

## 2026-06-17 - Team Add-on Action Feedback

- Added one-shot Team action feedback to persistent-agent wake prompts when an
  agent's most recent dispatch produced validation errors.
- Persisted feedback-delivery markers on action records so the same correction
  is not repeated and older failed dispatches are not replayed after newer
  action attempts.

## 2026-06-17 - Team Add-on Artifact Handoffs

- Added scoped artifact publishing for Team deliverables: agents can list
  `/artifacts`-relative `artifact_paths`, which are published into the
  control-plane artifact store and indexed through artifact spaces.
- Kept Team state refs-only by recording stable `artifact-space:` refs on
  deliverables and surfacing those refs in deliverable/review inbox payloads.
- Persisted Team action failures when deliverable artifact publication fails
  before applying the action, instead of returning only a transient effect
  error after consuming the dispatch.
- Encoded unsafe Team, project, deliverable, and dispatch IDs in Team artifact
  space paths so otherwise valid identifiers with spaces still publish.
- Resolved Team deliverable artifact publishing through the action session
  fallback and treated empty `artifact_paths` as omitted so Git-only
  deliverables from schema-driven agents do not fail publication preflight.

## 2026-06-17 - Team Add-on Focused Org Chart

- Added a top-level Team Add-on tab for a focused org-chart view that keeps the
  existing dense workspace intact.
- Made focused org-chart agent cards show awake/due/idle state, current work,
  waiting/blocker context, and open the existing agent edit dialog when
  selected.

## 2026-06-17 - Team Add-on SQLite State Store

- Migrated canonical Team Add-on state from `runtime/addons/team-addon/state.json`
  to relational SQLite at `runtime/addons/team-addon/state.db`.
- Added one-time legacy JSON import on first database initialization while
  keeping the existing `TeamStateRepository` and `TeamState` domain API.

## 2026-06-17 - Playwright VM Profile Repair

- Fixed `bootstrap_playwright_vm.sh` so rerunning it removes only its managed
  Playwright shell block instead of stripping unrelated `fi` lines from
  `.profile`.
- Added repair handling for profiles already damaged by the old Playwright
  bootstrap rewrite.

## 2026-06-17 - Team Add-on First-Class Roles

- Added first-class Team role records with role IDs, display names, project
  scope, default ephemeral policy, and configurable permissions.
- Moved agent assignment and review-profile filtering from raw role strings to
  `role_id` / `applicable_role_ids`, and changed release authorization to use
  the `request_release` role permission instead of an exact role name.
- Added Admin API and bundled UI support for creating and editing roles, plus a
  migration script for existing Team Add-on state files.

## 2026-06-17 - Team Add-on Context Clearing

- Added opt-in automatic context clearing for persistent Team agents when they
  become truly idle, with per-agent controls disabled by default.
- Added manual context-clearing requests that generate a Team-state handoff and
  rotate the agent onto a fresh thread on its next wake without interrupting an
  active dispatch.
- Prevented idle auto-clearing from immediately re-arming after a fresh context
  produces no progress and no new work has arrived.

## 2026-06-16 - Team Add-on Release Operator Authorization

- Allowed a project's scoped `release_operator` agent to create Team release
  packages through `git_release_requests`, matching the Team template's release
  handoff role while keeping unrelated implementers blocked from release
  requests.
- Tightened the seeded Team action schema for `git_release_requests` so agents
  use `project_id` plus `deliverable_ids` and do not invent source branch or
  commit fields for release package creation.

## 2026-06-16 - Agents Page Execution Status Badges

- Changed the Agents page status badge and Status filter to use actual agent
  execution state instead of VM power state, while showing VM or worker state
  as separate target metadata in the list and selected-agent details.
- Kept the Agents page's visible summary refresh from omitting VM guest state,
  avoiding `Unknown` badges for reachable VM-backed agents.

## 2026-06-16 - Team Add-on Dispatch Routing Guard

- Required completed persistent Team sessions to match the active Team dispatch
  lease before routing into action application, including completions that omit
  a dispatch id while a Team lease is active.

## 2026-06-16 - Team Add-on Cost Counter

- Added a Team Add-on cost API and UI counters that show lifetime team-level
  model cost totals first, with project breakdowns only when usage can be
  confidently tied to a project agent or project-scoped session.
- Added Claude transcript usage backfill for Team agents, importing usage-only
  metadata into `model-usage.db` with stable request deduplication and no
  prompt or response content.
- Fixed Claude transcript backfill for libvirt Team agents by scanning
  VM-local transcripts via the session's last-known VM IP with the configured
  VM SSH key and evander known-hosts alias, and made transcript rows
  supersede duplicate Anthropic relay rows only for the same provider request.
- Made Team cost loading non-blocking so the Team page renders state and
  templates immediately while cost counters fill in asynchronously.
- Added an operator-configurable lifetime Team max-cost guard that marks
  over-budget teams in cost payloads and prevents future Team wake dispatches
  once the limit is reached.
- Improved relay metering coverage by preserving provider request IDs and
  keeping streaming usage-recording failures warning-only.
- Omitted absent provider request IDs from relay usage metadata instead of
  serializing them as `null`.

## 2026-06-16 - Team Add-on Operator Directives

- Added durable Team operator directives that turn out-of-band operator chat
  requests into pending manager inbox items, waking the manager through the
  normal Team runtime instead of requiring ad hoc `team-action.json` writes.
- Exposed the directive path through the Team Admin API,
  `effect:team:create-operator-directive`, and the manager-facing
  MCP tool. The MCP tool was later removed to keep Team-specific behavior out
  of the core gateway.

## 2026-06-16 - Team Add-on Project Repo Wake Prep

- Prepared configured Team project repositories during persistent-agent wakes
  through `effect:agent:clone-repo`, so agents receive a guest checkout path
  instead of trying to work against host-only repo paths.
- Exposed Team wake repo-preparation results through `prepared_project_repos`
  and `failed_project_repo_preparations` workflow outputs for run inspection.
- Made the libvirt repo clone helper retry-safe when the guest checkout already
  exists, refreshing remotes/helpers without resetting or switching active work.

## 2026-06-16 - Review Regression Fixes

- Ignored agent-run completion comparisons when VM state summaries are missing
  a usable `last_state_update_at`, avoiding trigger poll crashes on stale or
  incomplete idle summaries.
- Sent the Team Add-on create-task form's selected creator agent in the API
  payload, keeping task and timeline attribution aligned with the visible form.

## 2026-06-16 - Team Add-on Failed Action Resolution

- Resolved failed-action runtime attention rows after any later successful action
  from the same agent, avoiding permanently sticky critical cards after a failed
  dispatch has closed.
- Added operator dismissal for failed-action runtime attention cards while
  preserving the action record for audit.

## 2026-06-16 - Team Add-on Review Action Guidance

- Clarified the Team agent wake prompt and seeded action contract so agents use
  `review_requests` and `review_decisions` for deliverable acceptance instead of
  trying to accept another agent's task through owner-only `task_updates`.

## 2026-06-16 - Agent Completion Dispatch Identity

- Tracked `trigger:agent:run:completed` cursor emissions by dispatch id as well
  as timestamp, so overlapping ordinary wakes and Team Add-on wakes for the same
  VM cannot cause the later Team dispatch completion to be skipped.

## 2026-06-16 - Team Add-on Review Delivery Layout

- Flattened the Review & Delivery workspace styling so team, project, and
  status groups read as sections instead of nested cards, and made filtered
  single-status deliverables expand to the available workspace width.

## 2026-06-16 - Team Add-on Task Wake UX

- Reset a persistent Team agent's no-progress throttle when an operator creates
  or updates non-terminal work for that agent, so adding a task in the Teams UI
  is enough to make the next wake cycle pick it up.
- Let operator-created Team tasks be attributed to the Admin operator instead
  of forcing the UI to choose an agent as "Created by."
- Treat malformed entries inside Team agent action lists as per-item errors
  instead of aborting `effect:team:apply-agent-action`, so valid delegation
  items in the same action still apply.
- Let a Team agent create a batch of tasks whose dependency graph references
  sibling tasks in the same action, then validate the combined graph before
  applying it. `task_creations` now accepts `task_id` as the preferred task
  identifier field, with `id` retained as a compatibility alias. Runtime
  attention now also hides an old failed action once a later successful retry
  for the same agent dispatch exists.
- Seed `team-action.schema.json` and `team-action.example.json` into Team agent
  sessions before their first Team wake, so agents have a local action contract
  instead of guessing the artifact shape from prose. Team wakes also persist and
  reuse a last-known VM IP for the seed copy, so schema seeding does not depend
  on a fresh libvirt lookup when a running agent is already reachable.

## 2026-06-16 - Team Runtime Pack Materialization

- Made `evander_team_addon.workflow_packs.team_runtime_v1()` emit a current-schema
  workflow pack directly, so operators can write it to `config/workflow-packs/`
  and list it in `workflow_packs` without local schema conversion.
- Allowed workflow bindings in communication target references and Git promotion
  bare-repo selectors, while preserving static validation for unknown
  communication targets and mutually exclusive static repo selectors.
- Loaded configured add-on provider output schemas before serving workflow
  editor documents, so Team runtime nodes do not show false missing-output
  binding diagnostics in Admin Web.
- Renamed Team Git result metadata config from reserved `outputs` to
  `result_outputs` in the generated runtime pack, while still accepting legacy
  `outputs` in existing Team packs.
- Routed completed persistent Team sessions only when the completed dispatch id
  matches the active Team runtime lease, preventing ordinary Discord wake
  replies from being recorded as missing `team-action.json` failures.

## 2026-06-16 - DIDWW Barge-In Fade

- Added a configurable `DIDWW_SIP_BRIDGE_BARGE_IN_FADE_MS` setting to the DIDWW SIP bridge
  with a 120 ms default and a 0 to 500 ms validation window.
- Routed confirmed barge-in cancels through a short faded queued-RTP tail instead of dropping
  pending assistant audio immediately, while keeping prompt Realtime response cancellation and
  assistant audio truncation.
- Removed the fade helper's deprecated `audioop` dependency so the DIDWW bridge imports cleanly on
  Python 3.13 while preserving G.711 A-law and u-law fade behavior.
- Documented the fade window in the DIDWW bridge operations guide, configuration reference, and
  sample env file so operators can distinguish a taper from a hard cut.

## 2026-06-16 - DIDWW Audio Quality Diagnostics

- Refreshed call-id audio-quality diagnostics after active tracker flushes and
  attached RTP trackers before call startup media begins so early packets and
  malformed RTP are included in persisted quality metrics.

## 2026-06-16 - DIDWW Backchannel Fuzzy Matching

- Added conservative fuzzy matching for short DIDWW backchannel transcripts so
  minor ASR corruption like `yea`, `yeh`, `okey`, or `thank yiu` still classifies
  as a backchannel when the full phrase stays within the configured word limit.
- Extended the default backchannel phrase list with common short affirmations
  such as `right`, `yep`, `yup`, `sure`, and `mm-hmm`.
- Added a service-configurable `DIDWW_SIP_BRIDGE_BACKCHANNEL_FUZZY_THRESHOLD`
  knob so operators can tighten or loosen the fuzzy match cutoff without code
  changes.
- Queued an interruption recovery response when a classified backchannel arrives
  after assistant audio has already been canceled, avoiding dead air while the
  caller keeps the floor.
- Deferred interruption recovery while a caller transcript is still pending so
  slow substantive ASR results answer the caller instead of resuming over them.

## 2026-06-16 - DIDWW Origin Query Patience

- Kept DIDWW origin-agent queries polling until the backend timeout after the
  caller-facing patience response, so late answers can still be injected into
  the live call.
- Suppressed the delayed origin-query hold prompt after the caller-facing
  patience response has already been queued.
- Avoided treating normal inbound greetings like "thanks for calling, how can I
  help?" as goodbye-idle hangup closings.

## 2026-06-15 - Agents Thread Log Loading

- Bounded the initial Agents page thread-log load for container worker sessions
  with large execution histories, avoiding a worker log probe for every past
  execution before the first page can render.
- Rebuilt grouped large-history thread chunks after resumed executions are
  associated with the same stored thread id, so fallback logs and header counts
  match the grouped history.
- Reused stored container thread ids from execution metadata where available and
  recorded resume thread ids on new container prompt executions.

## 2026-06-15 - Sessions Metadata Editor Layout

- Tightened the Sessions metadata key/value editor into aligned row cards,
  reduced oversized JSON snippet fields, removed redundant unchanged-value
  previews, and switched metadata row removal to the shared icon button style.

## 2026-06-15 - Discord Inbound Target Discovery

- Removed the separate Discord inbox route policy surface; Discord ingest now
  discovers channels from active session Discord allowlists and read-capable
  `trigger:communication:new-message` Discord targets.
- Updated Discord wake to resume sessions from unread inbox messages on each
  active session's read-capable Discord allowlist.
- Clarified that the Team Add-on operator communication target remains an
  outbound escalation destination, not an inbound wake subscription.

## 2026-06-15 - Agent Prompt Log Recovery

- Recovered Codex final messages from guest session JSONL when a libvirt VM run
  log only captured the shell preamble and session id.
- Repaired malformed guest profile session-env and Claude settings blocks before
  resume and during provider bootstrap so future prompts do not start with shell
  syntax errors.

## 2026-06-15 - Create Agent Model Override

- Added a Create Agent model field that stores an explicit session model for VM
  and container agents, including Claude model overrides.
- Resolved omitted VM Claude create models to the Claude provider default instead
  of the directory Codex default.

## 2026-06-15 - Team Add-on Archive Consistency

- Hid archived teams, agents, projects, and review profiles from Team Add-on
  entity views by default, with an explicit Show archived toggle to reveal them.
- Archived all persistent agents belonging to a team when that team is archived.

## 2026-06-15 - MCP Tool Descriptions

- Added descriptions for every MCP tool in the Admin Web tool option payloads,
  including `approval_submit` and `approval_check`.
- Showed tool descriptions directly in each MCP Tools directory-policy list row
  so operators can scan policy impact without opening every tool.

## 2026-06-15 - Session Artifact Staging Tool

- Added a `stage_artifact` MCP tool that lets agents copy files or directories
  from `/home/agent` into the session `/artifacts` folder without leaving their
  workspace.
- Granted the tool by default to newly created agent sessions and updated the
  standard workflow pack examples and Admin UI tool descriptions.

## 2026-06-15 - Create Agent Dialog Submit Behavior

- Closed the Create Agent dialog immediately after a valid submit while the
  longer-running VM/container creation request continues and reports through
  page-level status and action logs.
- Summarized successful Create Agent completions instead of showing raw
  bootstrap stdout in the success callout.

## 2026-06-15 - Claude VM Bootstrap Readiness

- Resolved caller-local NVM Claude Code installs during VM bootstrap when the
  setup flow is launched through sudo, and preserved explicit `HOST_CLAUDE_BIN`
  values across sudo/pkexec and privileged-helper launch boundaries.
- Waited for guest cloud-init and repaired `/home/agent` ownership before the
  Claude provider bootstrap writes files as the runtime user.

## 2026-06-15 - Create Agent Labels Help

- Removed the redundant visible Labels helper text from the Create Agent dialog
  while keeping the tooltip and empty-state guidance.

## 2026-06-15 - Agent Email Target Capability Enforcement

- Required mailbox-backed email communication targets selected for agent
  creation to declare both read and write capabilities, avoiding broader
  session mailbox allowlists for read-only or write-only targets.
- Filtered Create Agent and workflow editor agent-session email target options
  to only show mailbox targets that satisfy the full session capability
  requirement.

## 2026-06-15 - Create Agent Communication Target Picker

- Replaced the Create agent dialog's separate Discord, Teams, and email
  allowlist inputs with one Communication targets selector populated from the
  selected directory's registered session-capable communication targets.
- Extended the Agents create-options endpoint to return the directory-scoped
  Teams, Discord, and mailbox-backed email communication target catalog used by
  the picker, and mapped selected email targets into existing session mailbox
  allowlists.

## 2026-06-15 - Team Add-on Edit Modal Focus Fix

- Kept Team Add-on edit dialogs from reopening themselves on modal control
  clicks, preserving in-progress edits and checkbox toggles while the dialog is
  open.
- Focused the first editable field when opening an edit dialog and skipped
  global select refreshes for controls inside the active edit form.

## 2026-06-15 - Workflow Directory Draft Reset

- Cleared discarded workflow editor draft state when switching directories
  after the unsaved-changes warning, so the save panel and future saves reflect
  only the current saved workflow state.

## 2026-06-15 - Create Agent Sudo Environment Fix

- Passed Create Agent `agent_provider` and selected libvirt VM template choices
  to `setup_vm.sh` as explicit arguments instead of sudo-preserved environment
  variables, avoiding sudoers rejections for `AGENT_PROVIDER` and
  `LIBVIRT_BASE_IMAGE`.

## 2026-06-14 - Team Add-on Editability

- Added revision-checked editing for Team Add-on teams, projects, persistent
  agents, tasks, and review profiles through the Admin Web Team Add-on UI.
- Simplified Team Add-on project git setup with a single bare repo field and a
  provider dropdown, while keeping the backend canonical bare repo name/path
  model.
- Added setup-time ID suggestions and existing-agent default pickers so team
  and project setup forms no longer require hand-typing every identifier.
- Added reusable field tooltips across Team Add-on setup, mutation, and edit
  forms to explain IDs, sessions, git settings, hierarchy links, and review
  criteria formats in place.
- Fixed review profile edits so clearing the optional description persists
  instead of leaving the old description behind.

## 2026-06-14 - Create Agent Tooltip Help

- Added inline tooltip help to the Admin Web Create agent dialog for backend,
  provider, image selector, bootstrap, and initial-policy fields so operators
  can distinguish VM provider, agent provider, and model routing choices.
- Raised shared Admin Web tooltip overlays above modal layers so create-dialog
  field help is visible while the dialog is open.

## 2026-06-14 - Scheduled Wake Config Hot Reload

- Made the scheduled-wake worker reload `config/scheduled-wake/config.json`
  between cycles so edits such as `scheduled_wake_prefix_text` take effect
  without restarting the service.
- Rebuilt the scheduled-wake runtime context when a hot reload changes
  `runtime_dir`, keeping wake stores, session repositories, queues, and
  dispatch paths on the same runtime directory.

## 2026-06-14 - Approval Grant Clamp Generalization

- Generalized the exact-payload, single-use approval grant clamp to every tool
  in `ALWAYS_APPROVAL_REQUIRED_TOOLS`, including `semantic_delete_points`.
- Preserved the existing `delete_ado_backlog_item` approval behavior and TTL
  handling while keeping the policy clamp limited to mandatory-approval tools.

## 2026-06-14 - Scheduled Wake Prefix Cleanup

- Removed the false PR-comments default from the scheduled-wake example
  config and operator docs so fresh installs start with an empty optional
  prefix.
- Kept the legacy `wake_scheduled_prefix_text` input normalized through the
  canonical `scheduled_wake_prefix_text` field for backcompat.

## 2026-06-14 - Semantic Delete Approval Lock

- Locked `semantic_delete_points` behind the policy engine's mandatory approval
  gate so session and directory approval config cannot disable it.

## 2026-06-14 - Tool Visibility Approval Locks

- Surfaced host-locked approval requirements in the Admin Web Tool Visibility
  API and inspector so operators can distinguish policy-mandated approvals from
  configurable per-session approval settings.
- Kept locked approval badges valid in the mobile Tool Visibility cards by
  avoiding nested focus targets inside selectable records.

## 2026-06-14 - Session Contact Allowlist Plumbing

- Plumbed `allowed_contact_ids` through workflow `effect:vm:create` and
  `effect:agent:create`, admin session policy payload/update surfaces, and the
  admin session policy field catalog.
- Preserved existing container-worker session contact restrictions when
  `effect:agent:create` upserts omit `allowed_contact_ids`.
- Preserved contact allowlist resolution for provider-session fallback records
  used by directory-scoped provider config resolution.
- Documented that omitted or `null` allowlists keep the full directory contact
  set, while explicit lists narrow the contacts visible to phone tools.

## 2026-06-14 - Azure DevOps Backlog Title Updates

- Added the `update_ado_backlog_title` MCP tool for renaming Azure DevOps
  backlog items by numeric work-item id.
- Required title validation now rejects blank or whitespace-only values before
  the ADO update call and writes the trimmed value to `System.Title`.

## 2026-06-14 - Azure DevOps Backlog Deletion

- Added the approval-gated `delete_ado_backlog_item` contract to the operator
  docs so Azure DevOps backlog deletion is documented alongside the existing
  backlog write and query tools.
- Documented the soft-delete default and explicit `destroy=true` permanent
  delete path, plus the fact that allowed sessions still receive a per-call
  approval request before deletion runs.
- Recorded the focused model, issue-service, gateway, policy, and admin
  visibility tests that cover numeric-id validation, soft delete, explicit
  destroy, approval gating, and audit behavior.
- Forced delete approvals to remain exact-payload and single-use even when a
  session or directory config grants looser approval settings.

## 2026-06-14 - Sessions Save Feedback Cleanup

- Consolidated the Sessions page save feedback into the existing top-level
  success and error callouts so model, tools, and policy saves show one visible
  status message with section context.
- Made the approval-settings modal support a real discard flow so operators can
  close it without applying local edits to the session policy draft.
- Made `Reset to inherited defaults` clear explicit per-session approval
  setting overrides instead of restoring stale effective values.
- Added an explanatory tooltip for the disabled `Add notification target`
  control when all notify-capable targets are already selected.

## 2026-06-14 - Admin UI Polish

- Switched session model suggestions to catalog-driven options so the Sessions
  page keeps free entry without hardcoded model ids.
- Moved dashboard spacing and disk-dial tone values into shared CSS and theme
  tokens to reduce inline style duplication.
- Added visible feedback for collapsed pricing edits, deep-linked approval
  loading, and artifact upload success so state changes stay observable after
  the related panel or modal closes.

## 2026-06-14 - Structured Policy Inputs

- Added shared structured editors for list, map, JSON-object, and regex policy
  fields, then wired them into the Sessions, Agents, configuration, workers,
  and model-pricing admin screens.
- Added an optional structured Initial policy section to the Admin Web Create
  agent dialog so labels and list-style policy fields can be seeded without
  typing raw newline/key-value text.
- Preserved the existing backend payload shapes while adding inline validation
  for metadata JSON, label maps, leading-dot extensions, and regex rows.
- Expanded the focused Node and smoke coverage to assert structured hydration,
  malformed-input blocking, and round-trip payload assertions.

## 2026-06-14 - Admin Approval UX Updates

- Clarified Admin Web approval labels and tooltips across visible approvals,
  issue VM bootstrap controls, session approval settings, and approval
  decision panel behavior.

## 2026-06-13 - Admin Agents Status Performance

- Reduced Admin Web all-directory agent status expansion work by using
  lightweight agent summaries for capacity health and reusing resolved policy
  config while building effective tool-policy payloads.

## 2026-06-13 - Agents Create Dialog Cleanup

- Removed edit-after-create policy fields from the Admin Web Create agent dialog
  and centered the generated caret on wrapped create-dialog selects.

## 2026-06-13 - Sessions Toolbar Icon Fix

- Replaced the Sessions page cleanup action label with an icon-only toolbar
  button so it no longer overflows adjacent status and VM controls.

## 2026-06-12 - Wake And Approval Diagnostic Fixes

- Prevented libvirt wake timeout summaries from storing resume prompt text from
  timed-out command lines in wake dispatch errors or metadata.
- Made approval payload diagnostics derive `payload_match` from canonical
  payload digest equality while keeping redacted diffs as explanatory detail.
- Removed duplicate scheduled-wake status definitions so legacy empty statuses
  normalize without Pydantic override warnings.
- Added a durable retry cap for queued agent prompts so repeated busy-session
  redispatch loops fail with `retry_limit` diagnostics instead of running
  indefinitely.
- Exposed the queued agent prompt retry cap in the Admin Web Provider
  Integrations tab's Agent prompt section.
- Preserved `gate:for` and `gate:while` loop state across queued agent prompt
  dispatch boundaries so async `started` loopbacks advance existing loops
  instead of restarting one-item loops.
- Migrated legacy trace-scoped `gate:for` and `gate:while` state keys during
  loop execution so in-progress loops resume cleanly after upgrade.
- Added opt-in PR feedback suppression for comments authored by the configured
  agent identity while leaving agent-authored feedback publishable by default.

## 2026-06-14 - Scheduled Wake Retry And Cancellation Bounds

- Added a bounded automatic retry policy for one-time scheduled wakes that fail
  dispatch or remain unconfirmed, including persisted attempt state,
  configurable retry window/backoff, and terminal failed/missed ledger reasons.
- Allowed sessions to cancel their own one-time wakes while dispatching,
  queued, dispatched/awaiting confirmation, failed, or missed/late; completed
  and already-cancelled wakes remain immutable.
- Recorded model-unavailable resume failures as specific wake ledger failure
  reasons instead of generic transient dispatch errors.
- Reset the automatic retry counters for one-time scheduled wakes when Admin
  Web or the API retries them, so an exhausted confirmation batch starts fresh
  while the previous retry state stays available in retry history metadata.
- Fixed stale `scheduled-wake:*` prompt queue entries so cancelled or exhausted
  one-time wakes are not delivered later, and retry backoff now starts from the
  actual dispatch completion time.
- Documented the one-time wake confirmation retry policy keys, cancellation
  behavior, and manual retry reset behavior in the operations and configuration
  references.

## 2026-06-12 - Scheduled Wake Delivery Ledger

- Added a durable scheduled-wake ledger over the existing wake request store,
  including pending, due, dispatching, dispatched, failed, missed/late, and
  completed/acknowledged states with dispatch evidence and redacted operator
  failure summaries.
- Preserved scheduled-wake dispatch ledger fields while advancing handled wake
  requests, and marked matching wake requests completed when agent completion
  watchers publish completion events.
- Added Admin API and Sessions page views for filtering recent scheduled wakes
  and retrying or rescheduling failed and missed wake requests.
- Added a scheduled-wake lateness threshold so overdue wakes are visible and
  recoverable without reading host logs.

## 2026-06-12 - PR Integration Preflight Hardening

- Made pairwise PR merge preflight evaluate each PR pair against the target
  commit so target conflicts cannot report a clean result.
- Required directory-scoped workflow write permission before Admin Web PR
  integration preflight resolves host repos or Azure DevOps credentials.

## 2026-06-12 - Tool Visibility Inspector

- Added a read-only Admin Web Tool Visibility inspector for diagnosing a
  session's effective MCP tool set from policy visibility, transport binding
  telemetry, and denied-at-execution evidence.
- Added Tool Visibility API and persistence support for MCP transport tool-list
  bindings so operators can identify bound, mismatched, unknown, and possibly
  stale client manifests without exposing raw tool arguments or results.

## 2026-06-12 - Approval Retry Diagnostics

- Stopped approval retry diagnostics from reporting redacted stored fields as
  changed when the attempted payload still matches the canonical approved
  payload digest.
- Changed approval detail diagnostics to prioritize the latest blocked retry so
  operators see the most recent failed attempt first.

## 2026-06-12 - Admin Web Workflow Failure Cluster Docs

- Documented the new workflow failure cluster report added to the Admin
  Web/API surface in the operations guide and user manual as a read-only
  triage view over stored run activity.
- Noted that normalized failure signatures remove volatile IDs and timestamps
  and that the active/recent/resolved labels are heuristic signals that should
  be validated against representative workflow run detail before remediation.
- Kept root/unassigned failure clusters visible in the Admin Web/API report for
  users with root workflow-run read access when workflow graphs exist.

## 2026-06-12 - Scheduled Wake Cancellation Hardening

- Kept cancelled wake schedules historical and non-dispatchable by rejecting
  Admin Web/API edits, hiding the enable action for cancelled rows, and ignoring
  stale cancelled rows during due-wake scans.

## 2026-06-12 - Admin Agent Prompt Queue Visibility

- Added read-only prompt queue summaries to Admin Web agent records and surfaced
  queued prompt counts, coalesced counts, timestamps, queue keys, and prompt
  previews on the Agents page.
- Restricted prompt queue enrichment to Admin Web agent listing requests and
  stripped prompt queue fields from assistant MCP agent listings.

## 2026-06-12 - Container Worker Log Artifact Filtering

- Stopped reconstructed Codex and Claude session logs from surfacing synthetic
  `No response requested.` messages in container-worker execution logs, so real
  live output no longer appears above a stale terminal artifact in Admin Web.

## 2026-06-12 - Voice Wake Cleanup

- Disabled pending `voice_session_query` wake requests when
  `submit_voice_query_response` answers or fails the delegated query, preventing
  stale query wakes after the active voice session already received the answer.
- Kept that cleanup active for retryable voice query wakes after failed or
  timed-out dispatch attempts.
- Stopped automatic `voice_session_reconciliation` wake scheduling from phone
  result polling, voice bridge sync, and Teams meeting completion paths.

## 2026-06-12 - Ansible Worker Config Preservation

- Changed the Ansible control-plane deploy role so worker/global JSON config
  files are created only when missing and are never overwritten on rerun.
- Stopped pre-checkout cleanup from deleting ignored worker config files before
  the non-overwriting render step can preserve Admin Web edits.

## 2026-06-12 - Queued Claude Container Prompts

- Fixed queued container-worker prompt dispatch so Claude-backed sessions resume
  with the Claude runner command instead of falling back to the Codex command
  when a busy-session prompt drains from `queued-agent-prompts.json`.
- Resolved queued Claude prompts with the Claude default model when no queued or
  session model is configured, and registered their model-usage correlation with
  the selected agent harness.

## 2026-06-12 - Container Agent Steering Interrupts

- Changed Admin Web Operator steering for container worker agents to interrupt
  the active worker execution and immediately start a replacement execution with
  the steering instruction, while keeping the same logical workflow waiting
  record and dispatch.
- Added interrupt-in-progress workflow reconciliation protection so stopping the
  old worker job does not make prompt effects look completed or failed before
  the replacement job is registered. Replacement startup failures now fail the
  original waiting record so workflows see the real steering failure.
- Persisted delivered container steering interrupts in `operator-steering-notes.db`
  so Admin Web history survives refreshes.

## 2026-06-12 - Team Add-on Template and Timeline Fixes

- Applied template-defined ephemeral concurrency policies when materializing
  persistent agents from the Auto-Dev Squad template.
- Hardened derived timeline ordering for offset-naive timestamps and kept
  completed Git promotions out of pending/attention-needed timeline filters.
- Refreshed the Timeline / Audit panel after successful Team Add-on UI
  mutations so operators do not see stale derived activity.

## 2026-06-11 - Team Add-on Org Chart View

- Added an org-chart hierarchy view to the Team Add-on UI with visible
  supervisor/subordinate connector lines, display-name fallback to agent id,
  active/paused/archived state pills, and hover/focus operational details.
- Added optional persistent-agent display names through the team manager,
  project lead, and register-agent create flows while keeping legacy state files
  without display names valid.
- Added Team Add-on smoke coverage for three-level org charts, malformed
  hierarchy warnings, runtime/task/inbox/ephemeral detail rendering, and
  display-name form payloads.

## 2026-06-11 - Team Add-on Runtime Blockers Presentation

- Tightened the Team Add-on Runtime / Blockers panel styling so long section
  headings, filter controls, and runtime summary text wrap cleanly on narrow
  viewports.
- Kept derived runtime operations scoped in `query:team:state` responses so
  project- and agent-filtered workflow queries do not expose unrelated teams'
  runtime rows.
- Kept the bundled add-on packaging contract unchanged while documenting the
  new operator-facing runtime view in the project history.

## 2026-06-11 - Team Add-on Review & Delivery Workspace

- Added a dedicated Team Add-on Review & Delivery workspace that groups
  deliverables by team, project, and delivery state while surfacing task,
  producer, artifact, Git promotion, review, criterion, and release-package
  details.
- Added workspace-local review decision submission through the existing review
  decision endpoint, including findings and criterion result payloads.
- Extended focused smoke coverage for pending, accepted, rejected, and blocked
  review/delivery states, status filters, Git refs, release inclusion, criterion
  results, and workspace review decision submission.
- Documented the dedicated Review & Delivery workspace in the Team Add-ons guide
  while keeping the existing setup forms and Admin Web integration model intact.

## 2026-06-11 - Team Add-on Timeline / Audit View

- Added the derived Team Add-on timeline API and bundled UI view so operators
  can inspect current-state activity for tasks, inbox items, deliverables,
  reviews, action records, ephemeral assignments, Git promotions, and release
  packages.
- Exposed team, project, agent, type, status, and attention-needed filters, and
  kept timeline ordering deterministic even when legacy review requests or
  other records have no timestamp.
- Documented the derived-current-state boundary, timestamp-missing behavior,
  and the fact that the view is not a persisted audit log.

## 2026-06-11 - DIDWW SIP Bridge Inbound Calls

- Added bridge-side inbound call handling for marked Asterisk `StasisStart`
  events so the DIDWW SIP bridge can provision fixed inbound routes through the
  gateway, answer the inbound channel, create the `ExternalMedia` bridge, and
  send a route-specific opening to OpenAI Realtime.
- Synced terminal inbound bridge results back to the gateway so accepted calls
  update the gateway-owned voice-session store and schedule the originating
  agent's reconciliation wake.
- Collapsed fixed inbound route configuration to one `session_id` field for the
  active Truman session that owns projection and reconciliation.
- Extended the bridge store and call record payloads to keep inbound route
  metadata redacted, including DID/caller hashes and route identifiers, while
  preserving outbound call behavior.
- Added focused bridge tests covering listener filtering, inbound provisioning,
  inbound bridge setup, and the failure path that hangs up rejected calls.

## 2026-06-11 - DIDWW SIP Bridge Inbound Documentation

- Documented the inbound Asterisk `Stasis` dialplan shape, the bridge-to-gateway
  provisioning path, and the new bridge env vars used to enable fixed inbound
  DID routes.
- Added an operator-facing inbound route example with caller allow/block policy
  and clarified the failure handling for route misses, caller rejection, missing
  sessions, bridge setup failures, and hangup/reconciliation cases.

## 2026-06-11 - Agents Page Detail Layout

- Moved selected-agent labels into the VM host / worker session details panel
  and changed Operator steering from a persistent panel into a toolbar-launched
  dialog.

## 2026-06-11 - Admin Web Communication Artifact Configuration

- Exposed Teams control-plane communication artifact link settings in Admin Web
  under Configuration -> Provider Integrations -> Communication artifacts.
- Updated managed Admin Web Caddy config to proxy `/public/*` routes so signed
  communication artifact links are not treated as static frontend assets.

## 2026-06-11 - Admin Web Configuration Panel

- Fixed the Directory override JSON panel header so the collapse toggle no
  longer inherits pill-button styling while sitting next to the copy action.

## 2026-06-11 - Admin Web Model Pricing Tests

- Relaxed the default model-pricing catalog status test so it validates the
  endpoint contract without pinning dynamic catalog version dates or provider
  ordering.

## 2026-06-11 - PR Feedback History

- Changed `list_all_feedback` to include agent-authored PR replies that are
  intentionally suppressed from wake and unread-feedback checks.

## 2026-06-11 - Teams Control-Plane Artifact Links

- Added signed public download links for staged communication artifacts so
  `teams_post_file` can share non-image files through the control plane without
  requiring SharePoint/OneDrive upload scopes. Teams image artifacts continue to
  use inline Graph hosted content.
- Marked signed public communication artifact downloads as `Cache-Control:
  no-store` so bearer-token artifact URLs are not cacheable past their expiry.

## 2026-06-11 - Agents Page Steering Refresh Stability

- Kept existing Operator steering content rendered while refreshes are in
  flight, avoiding the loading-placeholder layout jump during periodic Agents
  page updates.
- Restored the checked-in Admin Web entrypoint to the bundled workflow-editor
  asset already present in `dist/assets`, and made approval notification deep
  links fall back to `config/admin-web.env` when non-admin services do not carry
  `EVANDER_ADMIN_WEB_PUBLIC_BASE_URL` in their process environment.

## 2026-06-10 - Agents Page Create Modal Immutable Configuration

- The Sessions page now hides past one-time wake schedules by default, shows how
  many were hidden, and keeps an explicit toggle for inspecting older one-time
  wake history without making long-lived sessions overly tall.
- `POST /api/admin/agents` with `backend=libvirt_vm` now accepts an optional
  `vm_image` template alias. `AdminService.create_admin_agent` resolves it against
  the directory's approved libvirt template catalog via
  `resolve_libvirt_vm_template_selector` and records the selection in
  `metadata["libvirt_vm_template"]` so VM creation sets `LIBVIRT_BASE_IMAGE`.
  Omitting `vm_image` records the directory default template, and an unknown alias
  raises `ValueError` (HTTP 400) listing the configured aliases.
- Added read-only `GET /api/admin/agents/create-options?directory_path=<path>`
  returning supported execution backends, agent provider options with
  per-backend constraints (Codex/Claude on both backends, Copilot on
  `libvirt_vm` only), the directory's libvirt VM template catalog, and the
  container worker image catalog.
- The Agents page direct-create modal now exposes an agent provider select
  (filtered to the selected backend) and backend-aware runtime image
  selection: a VM image template-alias dropdown on the libvirt path and an
  approved-alias dropdown for the container image selector on the container
  worker path. Direct create keeps failing clearly on duplicate VM names or
  session ids; the modal adds no upsert/no-op mode and no raw metadata JSON.
- The Agents page direct-create modal now always creates in the current page
  directory, removes the editable directory field, and reloads directory-scoped
  provider/image options from that selected directory without changing tracked
  dist asset hashes.

## 2026-06-10 - Operator Steering Stability

- Preserved Agents-page Operator steering drafts across live refreshes unless the
  selected steering session or dispatch changes.
- Fixed Anthropic/Claude operator steering delivery so injected notes are placed
  before trailing `system` messages, avoiding invalid Anthropic message ordering.

## 2026-06-10 - Team Add-on Task Graphs And Review Profiles

- Added Team Add-on task graph fields for parent tasks and dependency ordering,
  including derived API/query payload fields for child tasks, dependency status,
  blocked dependencies, and subtask counts.
- Added review profiles with role/project applicability, review instructions,
  and per-criterion review decision results.
- Exposed the new task graph and review profile controls in the bundled Team
  Add-on UI, and documented the updated agent action protocol.

## 2026-06-10 - Model Usage Pricing

- Added model-usage cost support for providers that report prompt cache reads
  and cache writes as separate usage counters, including per-rule 5-minute and
  1-hour cache-write rates in the pricing catalog.
- Exposed cache-write token and cost totals from `query:model-usage:list` so
  Claude/Anthropic usage reports include cache creation costs instead of only
  base input, cache-read, and output costs.

## 2026-06-09 - Team Add-on Hierarchy, Reviews, and Git Releases

- Tightened Team Add-on task creation and Git review promotion validation so
  agents cannot self-create tasks into unrelated projects or attach review
  branches to deliverables outside the assignment task/project.
- Added an Agents-page Operator steering panel that loads the selected
  session's active dispatch, queues steering notes, and lists/cancels pending
  notes without requiring operators to open Workflow inspect mode first.
- Extended the Team Add-on agent action protocol with `task_creations`,
  `status_requests`, `operator_escalations`, `review_requests`,
  `review_decisions`, and `goal_updates`, enforcing hierarchy permissions for
  task delegation, downward status requests, direct-superior-only questions and
  status reports, and manager-only operator escalation.
- Added action-driven reviews with a deterministic decision cascade across
  deliverable, task, and project state, plus project-status recalculation after
  goal updates, review decisions, task changes, and releases.
- Added a Git-first delivery flow with deterministic branch naming
  (`agents/<session>/<assignment>`, `team/<team>/reviews/<assignment>`,
  `team/<team>/features/<task>-<agent>`, `team/<team>/releases/<release>`, and
  canonical `releases/<project>/<release>`), `GitPromotionRecord` lifecycle, and
  `team.git.promotion_requested` events for review, feature, release, and
  canonical-push promotions.
- Captured ephemeral worker `agent_branch`/`commit_sha`, validated agent
  branches against the deterministic ephemeral namespace, and kept non-Git
  deliverables supported through `artifact_refs`.
- Added the `effect:team:record-git-promotion`,
  `effect:team:mark-release-published`, and `effect:team:consolidate-git-branches`
  effect providers, and extended the `effect:team:apply-agent-action` output
  schema with the new id/event result keys.
- Routed `team.operator.escalation_requested` to
  `effect:communication:send-message` and `team.git.promotion_requested` to the
  matching Git effects in the packaged `TEAM_RUNTIME_V1` workflow, keeping the
  only `effect:git:push-branch` node on the canonical-push path so internal
  branches never reach the canonical remote.
- Extended the Team Add-on Admin API and UI to manage the operator
  communication target, project Git config, review decisions, release packages,
  and Git promotion records.
- Fixed release promotion sequencing so canonical pushes are emitted only after
  release branch consolidation succeeds, ensured Admin-created release packages
  publish their Git promotion events, and expanded wake prompts with the full
  agent action schema.

## 2026-06-09 - Team Runtime Failure Handling

- Made Team runtime wake effects treat normal `resume_agent` failure results as
  failed dispatches and clear the leased wake through the Team state failure
  path.
- Defaulted ephemeral Team assignments to the container worker backend, wired
  create results into spawned-state tracking, and gated ephemeral prompts on a
  successful spawned marker.
- Filtered scoped Team state query payloads across dependent records so
  project- and agent-scoped views do not include unrelated Teams state.

## 2026-06-09 - Team Runtime Review Fixes

- Passed leased Team runtime dispatch IDs into agent resume calls so completion
  watchers can emit the action-application event for persistent agents.
- Hardened artifact-space creation by rejecting double-slash roots, preserving
  ancestor grants on implicit parents, and returning 409 revision-conflict
  payloads from Admin API mutations.

## 2026-06-09 - Team Add-on Packaging

- Documented the bundled-but-disabled Team Add-on enablement path for Admin
  Web, workflow workers, and the optional runtime workflow pack.
- Added packaging-boundary tests that pin the Team Add-on entry point, packaged
  static UI assets, and opt-in workflow provider loading.

## 2026-06-09 - Admin Log Auth Refresh

- Made the Admin Web Agents page refresh global auth state when background
  agent-list or log polling receives a 401, returning operators to sign-in
  with an explicit expired-session message instead of leaving stale log output.

## 2026-06-09 - Team Runtime Loop And Artifact Spaces

- Added generic hierarchical artifact spaces over immutable published artifact
  keys, including workflow effects, queries, and Admin API routes for space
  creation, version registration, listing, and latest-version lookup.
- Extended the optional Team Add-on with runtime leases, inbox items, structured
  agent action records, workflow providers for waking due persistent agents and
  applying action artifacts, and a packaged runtime workflow definition.
- Added event-mediated ephemeral worker assignments to the Team Add-on, including
  per-parent concurrency limits, add-on-owned lifecycle state, completion
  routing, and cleanup through existing Truman agent create/prompt/destroy nodes.
- Kept team runtime state canonical under the add-on while mirroring only a
  compact `metadata.team_addon` index into Truman session metadata.
- Added an optional package-owned Team Add-on Admin Web UI mounted under
  `/addons/team-addon/`, with state overview and setup forms backed by the
  add-on API.

## 2026-06-09 - Issue Delete Blocker Messages

- Made local issue deletion failures name the blocking VM or linked agent
  sessions, including session directory and backend, so container-backed
  issue sessions no longer produce misleading "destroy vm-..." guidance.

## 2026-06-09 - Team Add-on State Model

- Added the optional `evander_team_addon` package with private add-on state for
  hierarchical teams, projects, persistent agents, prioritized tasks,
  deliverables, review requests, and release packages.
- Exposed initial Team Add-on Admin API routes under `/api/addons/team-addon`
  while keeping canonical state under `runtime/addons/team-addon/state.json`
  and leaving Truman core session/workflow schemas team-agnostic.

## 2026-06-09 - Optional Add-on Boundary

- Added a generic add-on loader with explicit module/entry-point discovery,
  runtime storage namespacing, Admin Web API/UI mount hooks, and workflow
  provider bridging for optional opinionated modules.
- Kept add-ons disabled by default and documented that domain-specific concepts
  such as hierarchical agent teams belong outside Truman core.
- Required Admin Web authentication for add-on UI routes mounted under
  `/addons/<addon_id>/` when Admin Web auth is enabled.
- Made workflow validation preserve `effect:agent:create` execution-backend
  checks after provider-specific fields are normalized under node `config`.

## 2026-06-09 - Workflow Mobile Dark Canvas

- Kept the compact workflow canvas on dark theme backgrounds instead of
  reverting the mobile canvas surface to a light panel.

## 2026-06-09 - Admin Chat Viewport Anchor

- Moved the Admin Web assistant launcher out of the header so expanded chat
  panels stay anchored to the viewport instead of clipping against the header.

## 2026-06-09 - Claude Commit Attribution

- Wrote Claude Code settings for container-worker and VM-backed Claude agents
  so generated commit messages no longer include Claude co-author trailers.

## 2026-06-09 - Admin Chat Reenable Prewarm

- Made Admin Web chat sessions that were marked disabled while the assistant
  was off schedule assistant prewarm again after the assistant is re-enabled.

## 2026-06-09 - Admin Mobile Shell Density

- Made the Admin Web shell use a compact two-row mobile header and horizontal
  section bar so dashboard content appears in the first mobile viewport even
  when section navigation was previously collapsed.

## 2026-06-09 - Workflow Mobile Authoring

- Added a compact workflow authoring shell with mobile toolbar controls,
  bottom-sheet Create, Inspector, Runs, Graphs, and Save panels, plus
  tap-to-connect edge creation for phone-sized screens.
- Preserved mobile page scrolling in Select mode while adding explicit
  one-finger canvas panning in Pan mode and keeping two-finger graph gestures.

## 2026-06-09 - Admin Button Styling

- Standardized Admin Web action and icon buttons across tool calls, users,
  agents, workers, artifacts, and reports so feature pages no longer expose
  browser-default button styling.

## 2026-06-09 - Admin Assistant Background Prewarm

- Made Admin Web chat session creation return a lightweight thread immediately
  while assistant VM/session preparation runs in a deduped background task.
- Added per-thread assistant preparation status, retry API support, and launcher
  UI states for Preparing, Ready, and Failed without blocking page load.

## 2026-06-09 - Tool Calls Recent First Load

- Made the Admin Web Tool Calls page request a bounded recent audit slice by
  default, flag partial recent results in the UI, and keep targeted filters on
  exact full-history reads.

## 2026-06-09 - Admin Mobile Table Cards

- Added mobile record-card layouts for Admin Web Artifacts, Tool Calls,
  Reports, and Users tables below 720px while preserving the desktop table
  markup and selection behavior.

## 2026-06-09 - Admin Web Section Navigation

- Made Admin Web section navigation expanded by default for browsers without
  a saved collapse preference, while preserving stored collapsed/expanded
  choices.
- Kept collapsed navigation usable by rendering compact section buttons instead
  of an empty rail.

## 2026-06-09 - Dashboard Agent Inventory Polling

- Made the dashboard capacity agent-inventory poll use summary-only agent
  listing without container reconciliation or VM state refreshes.

## 2026-06-09 - Remove Legacy Host Script Workflow Effect

- Removed the deprecated host-side script workflow effect and its obsolete
  push-only helper in favor of typed workflow effects.

## 2026-06-09 - Typed Git Branch Push Effect

- Added `effect:git:push-branch` for typed host-side trusted checkout branch pushes,
  preserving destination-branch preflight outputs without allowing workflow-configured
  shell scripts or creating/updating pull requests.

## 2026-06-09 - Admin Web Branding

- Made the Admin Web browser title, shell header title, header logo, product
  logo, and sign-in company logo configurable from `config/admin-web.env`,
  with API-served local branding assets for Caddy-backed deployments.
- Proxied managed Caddy HTML entrypoints through Admin Web so configured
  branding is injected before the React app starts; static/no-bootstrap
  frontend paths now show blank branding until runtime branding loads instead
  of briefly showing the default title or logos.

## 2026-06-09 - Workflow Catalog Organization

- Collapsed workflow-editor create-panel subgroups by default and reorganized
  node catalogue families so Azure DevOps, issue, session, artifact, and usage
  nodes are easier to find.
- Added dedicated Azure DevOps work-item and pull-request tag effects that add
  or remove a single tag idempotently.

## 2026-06-09 - Clone Repo Git Committer Identity

- Added optional `git_committer_name` and `git_committer_email` inputs to
  `effect:agent:clone-repo` so workflows can configure Git author and
  committer identities separately while preserving existing author-only
  behavior.

## 2026-06-08 - Operator Steering Notes

- Added Admin Web operator steering notes for inspected prompt dispatches, with
  create/list/cancel APIs guarded by workflow-run permissions and privileged
  unlock.
- Delivered one pending note through the OpenAI/Azure OpenAI or Anthropic relay
  on the next eligible model-generation request while leaving compact,
  token-count, and websocket traffic untouched.

## 2026-06-08 - Container Worker Timeout Help

- Clarified the Admin Web tooltip for `container_worker_job_timeout_seconds`
  so operators see the built-in 1800-second default when the field is unset.

## 2026-06-08 - Agent Create Backend Diagnostics

- Recomputed the workflow editor `effect:agent:create` missing execution
  backend diagnostic on the client so the Problems panel tracks the current
  inspector value instead of a cached server validation result.

## 2026-06-08 - Agent Create Metadata Upserts

- Preserved existing container-worker session metadata during
  `effect:agent:create` upserts, while still honoring labels, explicit metadata
  patches, selected container images, and requested model/provider fields.

## 2026-06-08 - Agent Create Communication Target Picker

- Added `effect:agent:create.communication_target_ids` as a catalogue-backed
  multi-select for known Teams and Discord communication targets, mapping
  selected IDs into the existing session communication allowlists.

## 2026-06-08 - Ambient MCP Session IDs

- Bound self-scoped MCP tool calls to the session id carried by the MCP
  connection, made exposed `session_id` parameters optional legacy fields, and
  rejected conflicting caller-supplied session ids before provider dispatch.

## 2026-06-08 - Claude Container Thread Logs

- Rendered Claude project JSONL transcripts in container worker log modes so
  Admin Web thread logs show Claude harness output instead of only metadata.

## 2026-06-08 - Dashboard Missing Details Dark Mode

- Fixed the Dashboard release-metadata missing-details card so missing
  `NEWS.md` warnings use a dark warning surface with readable text in dark mode.

## 2026-06-08 - Claude Session Provider Routing

- Honored one-off Claude session metadata when resolving relay provider mode
  while preserving directory-scoped Anthropic endpoint and API key settings.
- Clarified Admin Web directory-configuration labels for default model provider
  mode and OpenAI-specific provider fields.

## 2026-06-07 - Agent Create Workflow Editor UX

- Split the workflow editor Agent create form by execution backend, exposed the
  agent provider field, and flagged missing execution backend selections as
  validation errors.

## 2026-06-07 - Claude MCP Prompt Dispatch

- Sent Claude runner prompts through stdin so the variadic `--mcp-config`
  option cannot consume the prompt as another MCP config path.

## 2026-06-07 - Local Assistant VM Responsiveness

- Moved Admin Web chat assistant session setup and issue VM creation requests
  off the async request loop so slow libvirt/SSH work does not block unrelated
  Admin Web requests.
- Ensured libvirt-backed chat assistant VMs bootstrap Codex when the VM exists
  but the Codex executable is missing, and made Codex VM runner logs emit
  session IDs from JSON output.
- Reported early guest agent process exits as resume failures with recent guest
  log output instead of leaving enqueue effects with a missing thread ID.

## 2026-06-06 - Libvirt VM Template Operations Runbook

- Added an operator runbook for preparing, registering, and selecting libvirt
  VM templates, including the qcow2/cloud-init terminology and the distinction
  from Docker or container images.
- Documented a non-default-template smoke path that verifies the selected
  alias through `runtime/sessions.json` and the libvirt disk backing file.
- Added failure guidance for missing images, unknown aliases, SSH readiness,
  and bootstrap failures so operators can diagnose template-selection issues
  without creating unnecessary new images.

## 2026-06-06 - Operator VM Template Visibility

- Added a normalized runtime-image payload for admin session and agent records
  so operators can see the effective libvirt VM template or container-worker
  image without parsing raw metadata.
- Exposed explicit legacy-missing labeling for sessions that lack preserved
  libvirt template metadata, keeping the UI and API distinction visible.
- Sanitized libvirt display image refs that look like host paths while
  preserving the original session metadata for compatibility.
- Kept container-worker image details distinct from libvirt VM template details
  so the two backends remain clearly separated in operator surfaces.

## 2026-06-06 - Agent Create VM Image Docs

- Added `vm_image` to the `effect:agent:create` schema reference and clarified
  that it is a libvirt-only VM template selector resolved against the approved
  `vm.json` catalog.
- Documented the libvirt template catalog and execution-backend guidance so
  operators do not confuse `vm_image` with container-worker `container_image`.
- Extended the operator field-help inventory with the new `effect:agent:create`
  VM template selector wording.
- Preserved existing `metadata.libvirt_vm_template` values on libvirt
  existing-session upserts when `vm_image` is omitted, replacing them only for
  explicit `vm_image` selections, and documented that the resolved libvirt
  image ref is forwarded to `scripts/launch_agent_vm.sh` via
  `LIBVIRT_BASE_IMAGE`.

## 2026-06-06 - Libvirt VM Template Catalog

- Added `libvirt_vm_default_template` and `libvirt_vm_templates` to the VM
  defaults config path and directory-config domain allowlist.
- Documented the libvirt VM template catalog as cloud-init-compatible qcow2
  image refs that deep-merge across directory overlays.
- Clarified that the new template selection metadata is validated before VM
  launch and consumed by `scripts/launch_agent_vm.sh` through
  `LIBVIRT_BASE_IMAGE`.

## 2026-06-06 - Claude Container Runner Image

- Added a separate Claude container runner Docker image and build script that
  packages a host-resolved Claude Code command without adding Claude to the
  default Codex runner image.
- Extended the worker Ansible deployment to copy, load, and verify multiple
  local runner image archives by default so Codex and Claude images can be
  preloaded together.
- Documented the `claude` container-worker image alias and Anthropic-backed
  Claude session configuration.

## 2026-06-05 - Container Worker Image Catalog Docs

- Documented operator-managed container image catalogs and fallback defaults
  across the container-worker backend docs, execution-backends reference,
  Ansible deployment guidance, and configuration-reference tables.
- Clarified that directory-scoped `providers.json` overlays can inherit or
  override the container-worker image catalog and fallback default together.
- Stated that runtime dispatch does not pull or build images and that approved
  image refs should be preloaded on worker VMs ahead of use.

## 2026-06-05 - ADO Daily Report Not Started Snapshot

- Show the daily report snapshot card as Not started using the status bucket
  instead of showing the separate unassigned ownership metric.

## 2026-06-05 - Claude Agent Provider Planning Support

- Added an Anthropic relay mode to the host OpenAI relay so Claude Code can use
  session-scoped `/v1/messages`, `/v1/messages/count_tokens`, and `/v1/models`
  paths while the real Anthropic API key stays host-side.
- Added Claude provider metadata, container session files, runner commands,
  guest bootstrap script, cloud-init relay env, Admin Web config fields, model
  usage attribution, and docs for the first Claude agent-model support slice.
- Fixed Claude VM bootstrap key resolution, libvirt resume provider dispatch,
  provider-neutral resume thread capture, and provider-aware container prompt
  model defaults.
- Preserved Claude libvirt resume thread IDs and approval-scoped MCP gateway
  overrides, and made Anthropic container configs infer the Claude harness when
  no agent provider is explicitly requested.
- Renamed relay upstream selection to `model_provider_mode` while preserving
  `openai_provider_mode` as a backwards-compatible config alias.

## 2026-06-05 - Deserialize Output Persistence

- Kept editable `gate:deserialize` outputs ahead of stale empty backend schema
  records for bound content while preserving backend-inferred literal-content
  schemas during frontend output-schema derivation.

## 2026-06-05 - Compact Model Usage Attribution

- Filled omitted `/responses/compact` model names from the request model or
  active model-usage correlation context so compact usage rows can receive
  query-time cost estimates.

## 2026-06-05 - Standalone PR Title Overrides

- Added `effect:pr:create` fallback title support so standalone workflow PRs can
  use concise explicit metadata titles without losing the existing session/branch
  fallback for workflows that do not set one.

## 2026-06-05 - npm Audit Facade Encoding Fix

- Preserved request `Content-Encoding` when forwarding npm audit payloads so
  gzipped audit requests are not sent to the public npm registry as undecoded
  JSON.
- Avoided double-prefixing Nexus npm group paths when npm rewrites cached
  tarball URLs back through the audit facade.

## 2026-06-05 - ADO Daily Status Report Item Floor

- Normalized daily status report `max_items` values below 1000 up to 1000 so
  agent-selected small caps do not truncate sprint totals.
- Scoped daily status reports to Product Backlog Items and Bugs by default so
  feature work items do not inflate sprint delivery counts.

## 2026-06-05 - ADO Daily Report Status Distribution

- Split daily report ownership and status metrics so unassigned items no longer
  overlap the status distribution totals.
- Treated Committed work items as not started/committed instead of active work
  in the default daily report status buckets.

## 2026-06-05 - Caller-Friendly Voice Lookup Holds

- Reworded DIDWW voice lookup holds and result prompts to say caller-friendly
  phrases such as "look that up" instead of exposing internal agent handoffs.
- Treated short affirmative replies like `Yes, please.` during a pending lookup
  hold as acknowledgements instead of new requests, avoiding duplicate lookups.

## 2026-06-04 - DIDWW Origin Query Hold Policy

- Replaced forced pending-origin-query filler with a short hold offer that lets
  the caller choose between waiting and continuing with relevant call content.
- Removed queued projected-task fallback responses when a real origin query is
  pending, and ignored short caller wait acknowledgements instead of queueing an
  extra reply.

## 2026-06-04 - DIDWW Voice Turn-Taking Follow-Up

- Treated in-progress Realtime responses as interruptible before their first
  audio delta so caller speech can cancel pre-audio assistant generation.
- Ignored short standalone acknowledgements such as `Okay, great.` as
  backchannels instead of requesting near-empty model replies.

## 2026-06-04 - Short Standalone PR Branch Override

- Let standalone workflow PR creation honor `metadata.pr_origin_branch` so
  scheduled/no-issue agents can publish to concise provider branch names instead
  of defaulting to the full promoted review branch.

## 2026-06-04 - DIDWW Voice Startup Retry Hardening

- Answered empty startup `query_origin_agent` tool calls from the authoritative
  projected call objective instead of queueing generic caller clarification.
- Made `response_cancel_not_active` a non-fatal Realtime cancellation race and
  skipped `response.cancel` when barge-in only needs to stop leftover RTP
  playback after the response is already done.

## 2026-06-04 - Outbound Voice Startup Contract

- Treated complete `make_phone_call` prompts as authoritative first-turn task
  state for DIDWW/OpenAI Realtime voice projections, preventing startup
  script/objective origin queries and generic context-check narration.
- Suppressed spoken pending-origin-query fallback while the origin answer is
  outstanding, leaving only terminal answer/timeout responses caller-facing.

## 2026-06-04 - Explicit Clone Repo Git Author Inputs

- Added explicit `git_author_name` and `git_author_email` inputs to
  `effect:agent:clone-repo`, replacing the hidden `metadata.issue_owner`
  lookup for Git attribution.
- Bound the container-worker issue workflow's resolved issue owner into the new
  clone-repo inputs and documented the paired-field behavior.

## 2026-06-04 - Workflow Dedupe Loopbacks and Pricing Source Layout

- Added an explicit `gate:dedupe` dropped branch for suppressed duplicates and
  wired the auto-dev missing-owner workflow path back to its `gate:for` loop.
- Fixed the Admin Web model pricing source URL row styling so its grid layout
  is not overridden by shared inline action styles.

## 2026-06-04 - Truman Auto-dev Issue Owner Attribution

- Added auto-dev workflow owner gating with `gate:filter`, requiring a valid
  Azure DevOps `System.AssignedTo` identity before Truman claims a work item.
- Frozen the resolved `issue_owner` into created issue-session metadata and
  configured agent repo Git identity from that frozen owner for commit/blame
  attribution.
- Added an ADO work-item comment effect so the workflow can leave an assignment
  request on unowned auto-dev items without creating an agent session.

## 2026-06-04 - Model Usage Cost Estimates

- Added a versioned local OpenAI pricing catalog and query-time cost estimates
  to `query:model-usage:list`, including cached-input token handling and
  aggregate token/cost totals without rewriting stored usage events.
- Exposed the active model pricing catalog path, version, source URLs, rules,
  and load errors in the Admin Web Configuration panel.
- Made the Admin Web model pricing catalog panel editable with structured rule
  controls, privileged-operation enforcement, revision conflict checks, catalog
  validation, and query-cost cache invalidation after saves.
- Fixed pricing catalog concurrency and estimate correctness by keeping dirty
  Admin Web pricing drafts tied to their original revision, reloading cached
  catalog data when the file changes, and preferring matching service-tier
  pricing rules over generic rules with the same effective date.
- Made the Admin Web model pricing catalog panel collapsed by default, widened
  source URL fields, and aligned pricing add buttons with the page action
  styling.

## 2026-06-04 - RTP Playback Accounting and Relay Usage Isolation

- Counted DIDWW RTP playback only after payload samples are actually handed to
  the transport, including partial multi-frame sends interrupted by barge-in.
- Kept successful non-streaming OpenAI relay responses from failing when model
  usage recording raises.

## 2026-06-04 - OpenAI Relay Model Usage Coverage

- Recorded model usage metadata for OpenAI relay WebSocket Responses traffic,
  Azure OpenAI non-stream Responses traffic, and `/responses/compact`, keeping
  prompt and completion text out of the persisted usage store.

## 2026-06-04 - DIDWW Realtime Playback Truncation

- Truncated interrupted DIDWW/OpenAI Realtime assistant audio to the RTP-played
  prefix when substantive caller barge-in cancels playback, keeping future model
  turns from assuming the caller heard queued or generated-but-unsent audio.

## 2026-06-04 - Issue Session Start Guard

- Added active-session guards to the issue-start workflow packs so repeated
  Azure DevOps work-item revisions for the same issue do not create another
  issue agent while an active issue session already exists.
- Added a short issue-keyed dedupe gate ahead of the active-session guard to
  suppress rapid duplicate starts before session creation has finished.
- Allowed `effect:vm:create` workflow nodes to stamp session metadata at create
  time, matching the existing `effect:agent:create` behavior used by the guard.

## 2026-06-04 - Azure DevOps Work Item Comment HTML Formatting

- Rendered Azure DevOps work-item comments as ADO HTML, including resolved
  `data-vss-mention` anchors, and posted them with explicit `format=html` so
  agent-authored Markdown-like text formats consistently in Azure Boards.

## 2026-06-04 - Container Worker NuGet Cache Prewarm

- Added a control-plane script for prewarming container-worker NuGet fallback
  caches through the host NuGet relay and additively copying the resulting
  global-packages archive to worker VMs.
- Added worker deployment aliases for prebuilt NuGet fallback archives while
  preserving the older restore-support archive variable names.

## 2026-06-04 - Session List Query Graph Directory Scope

- Fixed `query:session:list` so omitted `directory_path` inherits the workflow
  graph directory context instead of searching every session globally.

## 2026-06-04 - Session Metadata Rule Editor Controls

- Hid the `query:session:list` metadata filter mode dropdowns from the
  structured metadata rule editor and kept raw JSON/binding controls behind an
  explicit raw-filter action.

## 2026-06-03 - Workflow Trigger Graph Directory Scope

- Fixed trigger provider initialization so graph-scoped triggers inherit their
  workflow graph directory when the trigger node does not set an explicit
  `directory_path`, while preserving node-level directory overrides.

## 2026-06-03 - Azure DevOps Comment Mentions

- Added first-class `mentions` support to Azure DevOps work-item comments, PR
  comments, inline review comments, batched PR review comments, and PR feedback
  replies, with ADO identity lookup, unresolved/ambiguous mention failures, and
  Markdown `@<identity-id>` rendering.

## 2026-06-03 - Teams Auth Dark Mode

- Fixed the Admin Web Configuration Teams delegated auth device-code panel so
  the active sign-in card, code row, metadata, and links use readable dark-mode
  colors.

## 2026-06-03 - Teams Status Mentions

- Added explicit `mentions` support to `teams_post_status` for Teams channel
  posts, chat posts, and channel thread replies, with Graph user lookup,
  workflow prompt guidance, and documentation for required delegated user-read
  scopes.

## 2026-06-03 - ADO Work Item Comment Trigger

- Added `trigger:ado:work-item-comment-created` for workflow-owned Azure DevOps
  work-item comment polling with configurable work-item/comment filters,
  trigger-owned comment cursors, default no-backfill initialization, and
  detailed work-item payloads suitable for proxy issue upserts.
- Exposed `trigger:ado:work-item-comment-created` in the Admin Web workflow
  editor catalog so operators can create and configure it from the GUI.

## 2026-06-03 - Workflow Create Panel Trigger Defaults

- Stopped default-expanding the Triggers group when operators open the Workflow
  Editor Create panel.

## 2026-06-03 - Workflow Query Catalogue Session Group

- Moved the Session context query from the Workflow Editor query catalogue's
  Runtime state group into the Sessions group.

## 2026-06-02 - Workflow Editor Target Selector Regression Fixes

- Fixed collapsed workflow target selectors so choosing `Session ID` or
  `VM name` sticks before a target value is entered, and kept independent run
  snapshot refreshes from clearing document-load errors.

## 2026-06-02 - Workflow Definition-Only Access

- Fixed the workflow editor so users with `workflows.read` but without
  `workflow_runs.read` can still open workflow definitions; run snapshots,
  streaming, search, and inspect controls now stay behind workflow-run read
  permission.

## 2026-06-02 - Workflow Editor Cursor Paste Placement

- Changed workflow editor copy/paste so pasted nodes are centered on the cursor
  when the cursor is over the canvas, falling back to the current canvas center
  otherwise. Added regression coverage for cursor paste placement and
  click-created catalogue node centering.

## 2026-06-02 - Users Directory Assignment Selectors

- Changed the Admin Web Users & Groups access assignment directory fields from
  free-form path inputs to directory dropdowns backed by the current directory
  tree, while preserving existing assignments whose directories are no longer
  listed.

## 2026-06-02 - Workflow Target Selector Cleanup

- Collapsed workflow editor `target`/`session_id`/`vm_name` triplets into a
  single Target control across target-capable nodes while keeping the target
  type selector so operators can disambiguate session IDs from VM names.

## 2026-06-02 - Workflow Generator Schema Fix

- Included the legacy `directory_paths` graph field in the strict OpenAI
  workflow draft response schema requirements so generated workflow drafts are
  accepted by Responses API schema validation.

## 2026-06-02 - Configuration Override Removal

- Fixed Admin Web configuration tab saves so unchecking an existing directory override removes
  that field even when other fields on the tab are still saved, allowing child Teams delegated
  scopes to fall back to a parent directory.

## 2026-05-31 - Playback-Aware DIDWW Idle Prompting

- Moved DIDWW/OpenAI Realtime idle prompting into the bridge so the idle timer
  starts only after assistant RTP playback drains, caller speech cancels stale
  prompts, and silent periods receive at most one bridge-owned check-in prompt.

## 2026-05-31 - DIDWW Caller Turn Response Ownership

- Completed the DIDWW bridge-owned Realtime turn takeover by explicitly queuing
  bridge `response.create` requests when caller transcription completes, including
  caller turns that arrive while an assistant response is still active.

## 2026-05-31 - DIDWW Origin Query State

- Added DIDWW bridge-local origin-query state so delayed checking acknowledgements
  are skipped or superseded when answers arrive, duplicate questions reuse the
  existing pending or answered result, and projected calling-task questions are
  answered without waking the origin agent.
- Fixed repeated DIDWW origin-query retries after terminal failures so stale
  duplicate-query cache entries do not keep returning an already-failed query as
  pending.

## 2026-05-31 - Realtime Voice Prompt Tightening

- Tightened projected OpenAI Realtime voice instructions so DIDWW calls handle
  short acknowledgements as backchannels, avoid spoken bookkeeping phrasing, keep
  answers concise, and speak about origin-agent checks only once before giving
  the result directly.

## 2026-05-31 - DIDWW Bridge Turn Instrumentation

- Added compact DIDWW SIP/OpenAI Realtime bridge debug events for Realtime
  lifecycle, turn/audio markers, response queue decisions, origin-agent query
  flow, and Asterisk call lifecycle without changing call behavior.

## 2026-05-31 - Teams Message Target Scoping

- Required `channel_target` for `teams_get_messages`, scoped bare Teams message-id cursors to that
  target, and rejected cursors whose embedded target disagrees with the requested target.

## 2026-05-31 - DIDWW Bridge Delayed Barge-In

- Disabled immediate OpenAI Realtime auto-interruption for DIDWW calls while keeping server VAD,
  automatic caller-turn responses, and idle prompting enabled.
- Added bridge-side delayed barge-in cancellation, backchannel classification, and RTP output queue
  clearing so brief acknowledgements do not cut off assistant playback.

## 2026-05-31 - DIDWW Bridge Realtime Audio Transport

- Changed the DIDWW SIP/OpenAI Realtime bridge to use static G.711
  ExternalMedia audio (`alaw`/PCMA by default) instead of dynamic signed-linear
  RTP payloads that Asterisk does not inject reliably in bidirectional
  ExternalMedia calls.
- Paced OpenAI audio deltas into 20 ms RTP frames before injecting them into
  Asterisk, avoiding bursty playback on the PSTN leg.
- Queued Realtime `response.create` requests until the active response finishes
  and deduplicated repeated origin-agent voice queries so tool-call delegation no
  longer terminates calls with `conversation_already_has_active_response`.
- Requeued retryable `conversation_already_has_active_response` errors instead
  of treating them as fatal, keeping calls alive when Realtime auto-generates a
  response while the bridge is ready to speak.
- Added Azure NSG template rules for DIDWW SIP signaling and Asterisk RTP media
  to the control-plane public interface using a dedicated narrow RTP port range.

## 2026-05-31 - Tool Call Selection Keys

- Fixed Admin Web Tool Calls row selection so audit entries sharing a
  `request_id` no longer highlight together or show details for the wrong row.

## 2026-05-31 - DIDWW Bridge Stasis Readiness

- Waited for outbound and ExternalMedia Asterisk channels to enter the ARI
  Stasis app before adding them to the DIDWW bridge, avoiding early
  `addChannel` failures while a call is still being originated.

## 2026-05-31 - DIDWW Bridge ARI Registration

- Opened the Asterisk ARI event stream before originating DIDWW call channels so
  Asterisk registers the `evander_didww` Stasis app before channels enter
  it.

## 2026-05-31 - Phone Config Labels

- Clarified Admin Web phone-call configuration labels so Retell and DIDWW
  settings no longer collapse to duplicate names like `from number`.

## 2026-05-31 - Workflow Aggregate Gate

- Added `gate:aggregate` for deriving flat `count`, `sum`, `min`, `max`, and
  `avg` outputs from an array of workflow items.
- Registered aggregate gate runtime wiring, editor catalog metadata, output
  schema derivation, docs, and tests.

## 2026-05-31 - Workflow While Gate

- Added a bounded `gate:while` workflow gate with `body`, `done`, and
  `exhausted` routes for deterministic retry loops.
- Registered while-loop catalog metadata, validation, output schemas, and
  workflow editor cycle handling.

## 2026-05-31 - Model Usage Query Help

- Added Admin Web workflow editor help text for every `query:model-usage:list`
  filter, including current-run, effect, dispatch, directory, and time-scope
  behavior.
- Added catalog suggestions for known model usage provider, harness, and
  execution backend filter values.

## 2026-05-31 - Workflow Run Search Latency

- Made Admin Web Latest runs search use SQLite event-id slicing before hydrating
  run cards, avoiding full trigger/effect/gate history scans on SQLite-backed
  orchestration stores.
- Changed the workflow editor Latest runs filter to include agent-session runs
  by default instead of starting with the expensive no-session-only filter.
- Fixed SQLite-backed Latest runs searches with `without_session=true` so recent
  session-backed runs cannot hide older no-session runs from the result slice.

## 2026-05-31 - Users Tabs Dark Mode

- Fixed the Admin Web Users & Groups mode tabs so the segmented control uses
  dark-theme colors instead of light surfaces.

## 2026-05-30 - Users And Groups Page Modes

- Split Admin Web Users & Groups into separate Users and Groups modes and made
  `/#users/groups` open directly to group management.

## 2026-05-30 - Users Permission Tooltips

- Added Users & Groups tooltips for Admin Web RBAC permissions so operators can
  distinguish owner, user/group management, workflow, and workflow-run access.

## 2026-05-30 - Workflow Directory Canvas Scope

- Restored exact directory filtering in the workflow editor so opening `root`
  shows only root-owned graphs instead of descendant directory graphs on the
  same canvas.

## 2026-05-30 - Effective Azure Provider Validation

- Made effective directory provider validation run full Azure OpenAI required-field
  checks while keeping partial provider overlay writes permissive.

## 2026-05-30 - Model Usage Docs

- Documented the provider-neutral model usage store, `query:model-usage:list`
  workflow node, and the privacy-preserving OpenAI relay capture path in the
  operator-facing docs.
- Added the durable runtime store location `runtime/model-usage.db` to the
  troubleshooting guidance so operators can find the persisted usage records.
- Clarified that persisted usage records are correlation and usage metadata
  only; prompt and completion text remain unpersisted.

## 2026-05-30 - Realtime Voice Bridge Hardening

- Kept host Realtime API keys and upstream base URLs under bridge-local configuration instead of
  accepting per-call or per-meeting payload overrides.
- Updated Teams OpenAI Realtime speech responses to request audio with the current
  `output_modalities` response shape.
- Cleared stale voice-call failure fields when retrying a failed request and classified common
  ARI busy/no-answer hangup causes as failed calls.

## 2026-05-29 - Session Expiry Execution Gates

- Added a shared UTC-normalizing expiry guard so expired sessions cannot start
  or continue agent execution through admin resume, workflow prompt effects,
  queued prompt replay, wake dispatch, or the OpenAI relay.
- Wake dispatches for expired sessions now record failed dispatch history instead
  of silently starting work; scheduled requests remain retryable until the
  session is renewed.
- Kept host-local Git SSH pull/push access available for expired sessions so
  recovery work can continue without re-enabling agent execution.

## 2026-05-29 - Workflow Run Snapshot I/O Fix

- Kept JSON-backed workflow run snapshots on the raw-file read path so Admin Web
  reads do not initialize runtime store or journal files.
- Added SQLite activity timestamp indexes for recent workflow run snapshots.

## 2026-05-29 - Prompt Error Route Docs

- Documented the third `error` lifecycle port for `effect:agent:prompt` and
  `effect:agent:enqueue-prompt` in the configuration reference, operations
  guide, and README workflow shape notes.
- Clarified that the `error` route is for supported prompt dispatch failures
  with an outbound error edge, while successful `started` and `completed`
  paths stay error-free and unrouted failures still surface as normal prompt
  failures.
- Preserved enqueue retry context (`queued`, `queue_key`, and
  `coalesced_count`) in routed prompt error payloads.

## 2026-05-29 - Live Workflow Node Pulse

- Added `recent_node_activity` to workflow run snapshots so Admin Web can
  surface recent trigger, effect, and gate activity without changing existing
  active run cards or node activity counts.
- Made the workflow editor show a short live-only pulse on visible nodes for
  newly streamed backend activity keys, while keeping inspect/tutorial views
  and duplicate keys from replaying.
- Kept the pulse one-shot and motion-safe, with bounded timers and reduced
  motion behavior for users who prefer less animation.

## 2026-05-29 - Container NPM Registry URL Validation

- Rejected control characters in container npm registry URLs before generating
  session `.npmrc` content.

## 2026-05-29 - Session Web Search Policy

- Added a first-class `web_search_enabled` session flag for agent create and
  policy update flows.
- Made the OpenAI relay deny hosted Responses web search tool declarations
  unless the session explicitly enables the flag.
- Wrote Codex bootstrap, VM resume, and container prompt config with
  `web_search` set from the session flag so agents default to disabled search.
- Fixed agent-create upserts so omitting `web_search_enabled` preserves the
  existing session policy while explicitly setting `false` disables it.

## 2026-05-29 - Agent Create Session Metadata

- Added an optional `metadata` JSON patch to `effect:agent:create` so workflow
  authors can initialize session metadata without a separate
  `effect:session:update-metadata` node.
- Wired the metadata patch through libvirt and container-worker agent creation
  and exposed it in the workflow editor catalog.

## 2026-05-29 - npm Audit Facade Deployment Wiring

- Added the host-side npm audit facade to the persistent systemd install flow and start list.
- Rendered the facade's optional env file through Ansible, enabled it in the managed service map,
  and included its `11881` listener in the control-plane firewall and worker egress allowlists.
- Documented the facade's health endpoint, upstream routing, and manual smoke checks in the
  operator guides.

## 2026-05-28 - Workflow Editor Bindable Selectors

- Kept bindable enum workflow fields, including `query:session:list` State,
  editable through either a finite literal selector or an upstream binding.
- Kept `query:session:list` metadata filter bindings reachable from the
  structured metadata rule editor.
- Exposed PR-feedback agent identity fields in the workflow editor catalog.

## 2026-05-28 - PR Feedback Agent-Directed Metadata

- Added Azure DevOps PR feedback metadata for prior same-thread authors and ADO
  mention names, plus optional PR-feedback trigger configuration that annotates
  whether a comment is directed at an agent.
- Shared agent-directed feedback annotation between the trigger provider and the
  ADO worker publisher so workflow gates see the same metadata on live ADO
  feedback events.
- Added support for ADO compact mention identity IDs, including trigger config
  fields for matching raw agent author and mention GUIDs.

## 2026-05-28 - Session Metadata Equals Flattening

- Made the workflow-editor `query:session:list` metadata rule editor expand
  nested `metadata_equals` objects into dot-path Equals rows.

## 2026-05-28 - Session Metadata Rule Editor

- Added a structured workflow-editor metadata rule editor for
  `query:session:list`, replacing separate raw JSON controls for equals,
  exists, and prefix filters when the existing configuration can be represented
  as simple rules.

## 2026-05-28 - Session List State Selector

- Changed the workflow-editor `query:session:list` State field from free text to
  an enum selector for the accepted state values.

## 2026-05-28 - Session List Field Help

- Added workflow-editor tooltips for every `query:session:list` configuration
  field.

## 2026-05-28 - Scheduled Wake Queue Failure Fix

- Fixed busy container scheduled wakes so queue write failures record a failed
  dispatch with the original request id instead of crashing the wake worker.

## 2026-05-28 - Scheduled Wake Container Docs

- Documented that scheduled wakes cover both VM-bound `libvirt_vm` sessions and
  `container_worker_vm` sessions.
- Noted that MCP-created scheduled resumes stay `resume_mode=same_context`,
  which uses Codex resume-last behavior for container workers when no explicit
  thread id is available.
- Clarified that busy container wakes queue the prompt, still count as handled,
  and can advance one-shot or cron schedules accordingly.

## 2026-05-28 - Container Session Idempotency

- Skipped container-worker file rewrites when the destination content and mode
  already match the requested file.
- Avoided starting a short-lived Git configuration container when
  `effect:agent:clone-repo` finds the repo already prepared for the session.

## 2026-05-28 - Agent Log View Corrections

- Made container-worker execution logs include only execution-scoped Codex
  messages, avoiding restored-thread history while still showing assistant
  progress that is not present in raw container stdout.
- Changed Admin Web thread logs to page from the oldest thread forward and
  stopped auto-polling the expensive thread transcript view.
- Rendered the Admin Web execution/thread log switch as attached tabs so the
  active log view is clearer.

## 2026-05-28 - Workflow Binding Context

- Made workflow binding diagnostics distinguish guaranteed upstream references
  from path-conditional branch references.
- Preserved guaranteed pre-branch context across merge gates while keeping
  branch-local data available through explicit merge projections.
- Kept debounce gate releases from dropping inherited workflow binding context.
- Scoped workflow editor binding validation to visible graphs and scheduled
  graph validation progressively so large workflow packs do not block initial
  rendering.

## 2026-05-28 - Approval Queue Review Fixes

- Kept any-payload MCP approval grants from consuming uses until the retry
  matches the approved session and tool.
- Made Admin Web approval directory filters include descendant queues when
  direct-only matching is disabled.
- Kept per-session approval requirements removable in the Sessions tool editor
  while preserving inherited admin-required gates.

## 2026-05-28 - Configurable MCP Tool Approvals

- Added configurable MCP invocation approval grants with restrictive defaults:
  single use, 3600 second TTL, and exact-payload matching.
- Added directory-scoped MCP tool policies for admin defaults, hard approval
  limits, forced approval, and inherited tool deny rules.
- Added Admin Web MCP Tools policy editing and per-session approval settings
  controls, and made workflow-run MCP tool leases honor directory rules.

## 2026-05-28 - Admin Approval Directory Scope

- Defaulted the Approvals page to include descendant directories instead of
  limiting the queue to direct directory matches.

## 2026-05-28 - Admin Issues Copy

- Reworded the Admin Web Issues tab summary so it describes issue intake and
  records instead of presenting VM creation as the page purpose.

## 2026-05-28 - Admin Approval Decisions

- Used the signed-in Admin Web user as the approval decision actor when
  authentication is enabled, leaving the editable approver field only for
  anonymous/no-auth operation.
- Added an Admin Web revoke action for still-valid approved requests.

## 2026-05-28 - Admin Approval List Layout

- Kept approval status badges inside approval list cards when long request
  summaries wrap in the Admin Web sidebar.

## 2026-05-28 - Approvals Review Panel Layout

- Removed the Admin Web Approvals `Queue health` panel.
- Promoted approval `Summary` content out of its nested card so it reads as a
  root section inside the `Review` panel.
- Added more vertical spacing between Approvals page cards.
- Fixed decision-history cards so they use dark-mode surfaces and metadata
  colors.

## 2026-05-27 - Thread Log Pagination

- Fixed container worker thread-log paging so older transcript lines are fetched
  before applying the Admin Web page window.
- Made thread-log search inspect full session transcripts instead of only the
  current tail page.

## 2026-05-27 - Teams Trigger Wake Notifications

- Woke the trigger orchestrator immediately after Teams ingest stores new inbox
  messages, reducing healthy-path `trigger:teams:new-message` latency without
  increasing Teams Graph polling.
- Resolved relative trigger lock paths against the project root before deriving
  the Teams ingest wake socket, so `scripts/run_teams_ingest.sh` works from any
  caller directory.
- Kept missing trigger wake sockets non-fatal so the existing trigger polling
  interval remains the fallback path.

## 2026-05-27 - Discord Inbox SQLite Integration

- Batched Discord command-envelope persistence so a successful poll cycle can
  store all fetched commands with one repository write.
- Kept Discord inbox construction compatible with the legacy
  `runtime/discord-inbox-store.json` path while the live store uses
  `runtime/discord-inbox-store.db`.
- Preserved first-use import from legacy Discord inbox JSON and retained the
  SQLite message compaction rules for old messages once channel cursors move
  past them.

## 2026-05-27 - Worker Job Store SQLite

- Moved the worker daemon job store from `worker-jobs.json` full-store rewrites
  to `worker-jobs.db` SQLite row updates while preserving `WorkerJobStore` as
  the public entry point.
- Added first-use import from legacy `worker-jobs.json` files when the SQLite
  table is empty, leaving the JSON source untouched.
- Updated worker session cleanup and diagnostics to use indexed SQLite job
  reads instead of scanning or tailing the legacy JSON store.
- Made worker telemetry degrade gracefully when SQLite job-store reads fail.

## 2026-05-27 - Wake Store SQLite Migration

- Moved the scheduled wake store to SQLite while keeping the public repository
  API stable for gateway, admin, and worker callers.
- Preserved legacy `wake-requests-store.json` compatibility as import-only
  input, with sibling `.db` storage used for active persistence.
- Updated gateway-facing wake schedule coverage to assert repository behavior
  instead of treating the legacy JSON file as the live datastore.

## 2026-05-27 - Orchestration SQLite WAL Maintenance

- Added configurable SQLite WAL checkpoint maintenance for the workflow
  orchestration store, defaulting to truncated checkpoints when
  `orchestration.db-wal` reaches 64 MiB and at most once every five minutes.
- Ran WAL checkpoint maintenance from active trigger/effect worker cycles so
  long-running SQLite deployments are not limited to startup cleanup.
- Removed full-history effect execution scans from the effect-orchestrator hot
  loop by using indexed waiting/completion-pending queries and running
  waiting/gate maintenance after event dispatch.
- Avoided rewriting unchanged workflow config snapshots on every effect worker
  cycle.
- Moved the Teams ingest inbox from JSON-object rewrites to SQLite with
  first-use import from existing `runtime/teams-inbox-store.json` files, and
  targeted Teams trigger queries by channel target and cursor timestamp.
- Stored Teams inbox SQLite comparison timestamps in fixed-width UTC form so
  same-second fractional messages are not skipped by cursor filters.

## 2026-05-26 - Teams Message Store SQLite

- Moved the durable Teams message store from per-write JSON rewrites to SQLite,
  with first-use import from existing `runtime/teams_messages.json` files.
- Batched `teams_get_messages` persistence so each fetch stores refreshed Teams
  messages in one repository transaction.

## 2026-05-26 - Secret File Cleanup

- Removed local env files from the tracked repository surface.
- Ignored generated `config/*.env` files while keeping checked-in `.env.example` files.
- Moved client-specific Azure Pipeline, inventory, group vars, and Vault material out of the
  product repo and added non-secret Ansible examples.

## 2026-05-26 - Teams Status List Rendering

- Rendered unindented bullet details following numbered Teams status items as
  nested lists inside the same ordered list item, preventing Teams from
  restarting numbering at 1 for each item.

## 2026-05-26 - Teams Direct Message Sender Emails

- Allowed `trigger:teams:direct-message` `sender_user_ids` entries to be
  Microsoft Graph user IDs, UPNs, or mail addresses, resolving email-style
  entries to Graph user IDs during host-side Teams ingest.
- Preserved resolved direct-message sender aliases in the Teams inbox so trigger
  polling can emit events for workflows configured with UPNs or mail addresses.
- Seeded new Teams direct-message trigger cursors on first poll so enabling a
  sender does not replay already-ingested one-on-one chat history.

## 2026-05-26 - Azure DevOps Pipeline Run Effect

- Added `effect:ado:pipeline-run` for typed Azure DevOps pipeline queue/poll
  workflows without invoking host-side Bash.
- Preserved ADO pipeline run outputs such as `run_id` and `run_url` when checked
  failed, canceled, or timed-out runs are recorded as failed effects.
- Updated checked-in APP demo workflow examples to queue the provisioning
  pipeline, derive relay metadata with `gate:map`, and store it through
  `effect:session:update-metadata`.
- Replaced the checked-in synthetic workflow's host Bash example with a safe
  internal event emit.
- Added dynamic object-key bindings and `gate:filter` `matches_regex` support so
  workflows can build nested metadata maps while rejecting unsafe request names.

## 2026-05-26 - Runtime Diagnostics Snapshot Effect

- Added `effect:runtime:diagnostics-snapshot` for bounded, redacted host
  diagnostics collection without allowing workflow-configured shell scripts or
  arbitrary host paths.

## 2026-05-26 - Tool Call Audit SQLite Index

- Added a SQLite read index for Admin Web Tool Calls while keeping
  `runtime/audit.jsonl` as the canonical append-only audit journal.
- Added `evander-cli rebuild-audit-index` for rebuilding `runtime/audit.db`
  from the JSONL audit log.
- Fixed directory-filtered audit queries so incomplete indexes fall back to the
  canonical JSONL log and directory prefixes containing SQLite wildcard
  characters do not match unrelated paths.
- Kept default Tool Calls reads from treating bounded, incomplete tail imports as
  authoritative when the SQLite audit index has not been fully imported.

## 2026-05-26 - Agent Exec Command Editor

- Rendered the workflow editor `effect:agent:exec` Command field as a
  multiline text area so long commands are easier to read and edit.

## 2026-05-26 - Artifact Upload Dialog

- Moved manual artifact uploads into a toolbar dialog and defaulted uploaded
  archives to the selected file's own filename instead of asking for an
  internal artifact path.

## 2026-05-26 - Workflow Editor Viewport Stability

- Preserved the workflow editor canvas viewport across node field edits,
  validation, resets, and workflow saves instead of refitting the graph after
  routine document patches.

## 2026-05-25 - Published Artifact Import And Upload

- Added `effect:artifact:import-published` for extracting immutable published
  artifact archives into a selected session's `/artifacts` folder.
- Added Admin Web artifact uploads so operators can publish a single local file
  into the durable artifact store for later workflow use.

## 2026-05-25 - Admin Sign-In Branding

- Moved sign-in branding into a top bar with the company logo on the left,
  the theme toggle centered, and a smaller evander logo on the right.

## 2026-05-25 - Hybrid Voice Delegation

- Added durable live voice query records so Realtime phone sessions can delegate
  file/tool/context questions back to the originating Truman agent without
  blocking the call.
- Added the explicit `submit_voice_query_response` tool, voice media controller
  endpoints, Realtime `query_origin_agent` function-call handling, and
  same-context wake scheduling for delegated voice queries.

## 2026-05-25 - Teams Meeting OpenAI Realtime Speech Mode

- Added a selectable `OpenAIRealtime` speech mode to the Teams meeting media
  bridge, keeping the existing Azure Speech mode as the default fallback.
- Streamed Teams PCM audio through an in-process OpenAI Realtime WebSocket,
  including projection instructions, input transcription events, Realtime audio
  playback, interruption handling, and PCM sample-rate conversion.
- Documented the new bridge environment keys and added focused C# bridge tests
  for Realtime configuration and WebSocket audio/transcript behavior.
- Restored configured `AzureSpeech:DefaultVoice` handling for the default
  InMemory bridge mode.

## 2026-05-25 - DIDWW SIP Realtime Media Bridge

- Added `evander-didww-sip-bridge`, a host-side Asterisk ARI bridge that
  originates DIDWW trunk calls and streams RTP audio to OpenAI Realtime.
- Added the bridge API, durable call state, RTP packet handling, Realtime event
  handling, systemd/config examples, and focused contract tests.
- Kept DIDWW calls in `connecting` until Asterisk reports answer, chunked
  Realtime audio into correctly timestamped RTP frames, and contained voice
  query controller failures inside Realtime tool output.

## 2026-05-25 - Projected Realtime Voice Sessions

- Added durable projected voice-session state for OpenAI Realtime-backed phone
  calls and Teams meetings, including transcript capture and same-context
  reconciliation wake scheduling for the originating agent.
- Added a selectable `phone_call_backend` so existing Retell calls remain the
  default while `openai_realtime_didww` can route directory-contact calls
  through a host-owned DIDWW SIP bridge.
- Extended Teams meeting startup with `--origin-session-id` and projection
  metadata so an existing agent session can be represented in a live meeting.
- Preserved completed projected-agent Teams speech in voice-session transcripts
  so reconciliation includes what the live voice agent said.

## 2026-05-25 - Communication Messages Query

- Added the `query:communication:messages` workflow query for reading
  normalized Teams and Discord inbox messages through declared read-capable
  communication targets.
- Fixed Discord query pagination so messages that share the same `created_at`
  timestamp are preserved across limited pages.

## 2026-05-25 - Pull Request Get Workflow Query

- Added `query:pull-request:get` for normalized provider pull request snapshots,
  including GitHub PR get support.
- Paginated optional GitHub PR commit and review detail fetches before
  populating normalized `commits`, `reviewers`, and `raw` data.

## 2026-05-25 - Workflow Switch Lazy Routing Fix

- Fixed `gate:switch` routing so cases are resolved in order and later
  case/default binding errors cannot prevent an earlier matching branch from
  running.

## 2026-05-25 - Workflow Switch Documentation

- Documented `gate:switch` in the configuration reference with ordered cases,
  `default_port`, `source_port`-based branch wiring, and pass-through payload
  behavior.
- Added a switch workflow example that uses current `source_id`/`target_id`
  edges and shows ready, blocked, and fallback routing.
- Clarified the architecture notes so `dropped` now covers filter, dedupe, and
  switch suppressions in the workflow runtime.

## 2026-05-25 - Issue List Workflow Query

- Added the `query:issue:list` workflow query node for listing tracked local and
  proxy issues with state, source, tag, directory, and metadata filters.

## 2026-05-25 - CI Run Completed Trigger Fixes

- Persisted initialized empty CI run cursors on first polls when history
  suppression is enabled, so the first later completed run is emitted.
- Rejected CI runs with unknown conclusions when applying configured conclusion
  allowlists.

## 2026-05-25 - CI Run Completed Trigger Documentation

- Documented `trigger:ci:run:completed` in the configuration reference,
  operations runbook, trigger conformance guide, and changelog, including the
  polling cursor file and first-poll history suppression behavior.

## 2026-05-25 - Workflow State Runtime Coverage

- Added end-to-end regression coverage for persisted workflow-state reads and
  writes across multiple graph IDs, including the fast-fail path when state
  nodes do not have persisted graph metadata.
- Registered workflow state node schemas in runtime config materialization and
  rejected duplicate persisted workflow graph IDs before state scope resolution.
- Documented the graph-local workflow-state file, scoping tuple, merge-patch
  behavior, and optimistic-concurrency conflict handling in the configuration
  reference.

## 2026-05-25 - Scheduled Event Hot Reload Fixes

- Fixed `effect:event:schedule` so hot-reloaded
  `scheduled_internal_event_store_path` values are honored without restarting
  the effect orchestrator.
- Rejected non-finite or out-of-range `delay_seconds` values before schedule
  timestamp construction so invalid workflow input records a failed effect
  instead of aborting the worker cycle.

## 2026-05-25 - Scheduled Internal Event Documentation

- Documented `effect:event:schedule` as a one-shot scheduled internal event
  effect with `event_type`, `payload`, and exactly one of `run_at` or
  `delay_seconds`.
- Clarified the durable `scheduled_internal_event_store_path` default and noted
  that the trigger orchestrator publishes due schedule records during its
  normal poll cycle.

## 2026-05-25 - Workflow Webhook Idempotency Fix

- Changed signed workflow webhook ingress to publish every valid delivery as a
  fresh internal event instead of using the body HMAC signature as a global
  idempotency key, avoiding repeated-payload and shared-secret cross-slug
  suppression.

## 2026-05-25 - Signed Workflow Webhook Docs

- Documented the `trigger:http:webhook` trigger shape, public ingress path,
  signature header format, and raw-body HMAC requirements.
- Added an operator-facing signed request example and noted that webhook
  deliveries should be reshaped downstream with the standard gate nodes.

## 2026-05-25 - Container Worker Browser Env

- Exported `CHROME_BIN` and `CHROMIUM_BIN` from Playwright-enabled
  `container_worker_vm` sessions so Karma-style browser test launchers can use
  the seeded Chromium binary mounted from the worker cache.

## 2026-05-25 - Workflow Trigger Disable Toggle

- Added an `enabled: false` workflow trigger state that skips trigger polling and
  the legacy ADO/Git direct publishers while preserving already queued work.
- Added a Workflow editor trigger toggle and ghosted disabled-trigger styling.

## 2026-05-25 - Teams Direct Message Trigger

- Added `trigger:teams:direct-message` for allowlisted one-on-one Microsoft
  Teams direct messages to the delegated Teams identity, backed by host-side
  Teams ingest polling.
- Documented the Teams direct-message trigger and updated example Truman packs
  with a separate direct-message prompt path.
- Fixed Teams direct-message ingest so enabled triggers poll on cold start
  without active sessions, disabled triggers are ignored, and one-on-one chats
  already allowlisted for normal Teams reads still publish DM trigger events.

## 2026-05-25 - Workflow HTTP Request Effect

- Added the host-side `effect:http:request` workflow effect with Pydantic
  schema, runtime provider, registry wiring, and admin/workflow editor
  exposure.
- Returned request metadata and response outputs including status, headers,
  body text, parsed JSON when available, content type, and elapsed time.
- Kept the effect JSON-body-only, disabled redirects by default, and preserved
  the default non-2xx success semantics unless `check` is enabled.
- Added per-effect `allowed_url_patterns` URL allowlist enforcement, defaulting
  to `["*"]`, and preserved response outputs when checked non-2xx responses fail
  the run.

## 2026-05-25 - Workflow Editor Inspector Layout

- Moved selected-node declared and derived output contracts below configuration
  fields so large output lists no longer push configuration down the inspector.
- Fixed the Configuration communications Teams channel lookup card so it uses
  dark-mode surfaces, labels, disabled controls, and result rows.
- Clarified that communication targets are provider-generic and the Teams channel
  lookup is only an optional helper.
- Allowed session Teams/Discord communication allowlists, inbound polling routes,
  and guest messaging tools to use directory-scoped communication target IDs while
  preserving raw legacy IDs and enforcing target capabilities per operation.

## 2026-05-25 - Workflow Tutorial Cleanup

- Removed the obsolete workflow editor in-memory first-run walkthrough, its
  sidebar controls, and its Admin Web API routes now that Tutorials owns the
  onboarding flow.
- Moved Admin Web users to `runtime/admin-web.db` with one-time legacy JSON
  import, and stored workflow tutorial completion/skipped state as an
  account-scoped preference instead of browser-local state.
- Marked the workflow editor first-steps tutorial complete when an operator
  exits or resets from the final step, while preserving the explicit skip path.
- Removed the obsolete duplicate issue-comment workflow trigger alias from the
  runtime, node catalogue, documentation, and tests.
- Added internal wake sockets for trigger/effect orchestration so host-owned
  trigger ingress and direct queue publishers can hot-drain without waiting for
  the next poll interval.
- Woke await-agent completion drains through the trigger worker's configured
  effect lock path, so custom workflow runtime directories still hot-drain
  effect work.
- Added `trigger:communication:new-message`, a provider-neutral inbound chat
  trigger over read-capable Teams channel/chat and Discord communication
  targets, with Admin Web catalog support and normalized message outputs.
- Tightened communication trigger reload/runtime validation so Teams `user:`
  targets stay send-only and custom directory stores are preserved on workflow
  config reloads.
- Fixed explicit Azure DevOps PR trigger polling so completion triggers without
  a status scan terminal PR states, and explicit feedback events stay unbound in
  mixed legacy/explicit configurations.

## 2026-05-24 - Container Worker Manifest Persistence Fix

- Delayed workflow container-agent session manifest persistence until worker
  preparation and bootstrap file writes succeed.
- Prevented newly requested container sessions from leaving a stale active
  manifest behind when bootstrap fails before the worker is ready.

## 2026-05-23 - Runner Cache and Workflow Tutorial Fixes

- Pulled moving Docker base-image tags during forced runner-image rebuilds and
  saved runner archives through a temporary tarball before publishing the
  manifest.
- Kept the workflow editor first-steps tutorial isolated from the live graph
  state, preserved the target-node selection after tutorial connections, and
  stabilized tutorial camera effects.
- Opened the tutorial create controls against the tutorial catalog when the
  walkthrough asks the operator to add a node.

## 2026-05-23 - Cached Runner Archive Reuse

- Added a sidecar manifest contract for `dist/evander-runner.tar` so
  unchanged runner-image inputs reuse the cached archive instead of rebuilding
  Docker output.
- Documented the `RUNNER_FORCE_REBUILD=1` and `--force` refresh paths for
  operators who need to invalidate the cache, including moving base-image tags
  such as `ubuntu:24.04`.
- Kept the worker deployment model as a tarball copy/load flow and deferred
  worker-side remote manifest comparison to a later slice.

## 2026-05-23 - Dashboard Agent Inventory Polling

- Added a dashboard-scoped Agent Inventory poll that refreshes visible agent
  data about every 10 seconds while the dashboard is mounted.
- Coalesced concurrent dashboard JSON cache misses for the same request so
  shared polling clients do not start duplicate backend reads.

## 2026-05-23 - Workflow Generator Draft Safety Fixes

- Guarded generated workflow draft layout against cyclic graphs so invalid
  model output reaches validation instead of re-enqueueing nodes forever.
- Returned JSON generator errors when upstream OpenAI Responses API transport
  requests fail before an HTTP response is available.
- Restricted workflow generator draft acceptance to ready generated drafts so
  stale previews cannot be accepted after prompt changes or generation failures.
- Replaced pause-driven workflow generation with an explicit **Generate draft**
  action and a longer minimum description before model requests are allowed.
- Added a backend in-flight guard and cooldown around workflow generator OpenAI
  calls so browser request timing is not the only rate-control layer.
- Remapped generated draft output schemas when imported node ids collide with
  existing workflow nodes.
- Kept the first-run walkthrough controls visible while inspecting a walkthrough
  run so onboarding can reset and complete from inspect mode.

## 2026-05-23 - Retry-Safe Workflow Completion Publication

- Added durable `completion_pending` effect and gate execution state so
  completion-event enqueue failures can be retried without rerunning completed
  providers or gate logic.
- Kept deterministic completion-event payload validation failures as failed
  executions instead of retrying them as transient publish failures.
- Preserved pending `started` branch publication for waiting agent effects so a
  transient enqueue failure does not mark the source event delivered before the
  branch activation is queued.
- Treated `completion_pending` workflow execution records as active in admin
  workflow-run snapshots, searches, and manual clear operations.
- Added the pending-completion status to the workflow editor node ordering so
  stalled completion publication remains legible in the UI.

## 2026-05-23 - Effect Store Rebind Drain

- Deferred effect-worker store hot reloads while the current queue still has
  pending events, so completion events published during a JSON-to-SQLite rebind
  are processed before the worker swaps to the new store.

## 2026-05-23 - Workflow Inspector Outputs Placement

- Moved the workflow editor node `Outputs` field to the bottom of the selected-node
  inspector so node configuration stays grouped ahead of derived output schemas.

## 2026-05-23 - Workflow Graph Generator Draft Mode

- Added an experimental Admin Web workflow generator mode that opens as a small
  Workflow-tab control, drafts a blank-canvas graph from natural language through
  the host-side OpenAI Responses API, validates the generated graph, and imports
  accepted drafts as unsaved workflow edits.
- Added `assistant_settings.workflow_generation_model` as an optional model
  override for generator calls, falling back to `assistant_settings.default_model`.

## 2026-05-23 - Progressive Dashboard Loading

- Changed the Admin Web dashboard to update panels as their own requests settle, and kept
  release metadata, version, news, and changelog reads out of directory-change reloads.

## 2026-05-23 - Dashboard System Health Cleanup

- Moved dashboard capacity, descendant-scoped agent inventory, and collapsible full worker
  coverage into the System Health panel, with disk dials for control-plane and worker hosts.

## 2026-05-23 - Workflow Run Detail Lookup Guardrail

- Changed SQLite-backed workflow run detail lookups by event or trace id to
  query indexed orchestration rows directly instead of loading the full
  trigger/effect/gate execution history, and bounded tool-call audit scanning
  for the detail view.

## 2026-05-22 - Agent Branch Promotion FETCH_HEAD Fix

- Updated agent branch promotion to fetch into a stable local ref, resolve that
  ref to a commit, and update the review branch from the resolved SHA so
  `FETCH_HEAD` no longer causes refresh failures.

## 2026-05-22 - Workflow Run Control Alignment

- Fixed the workflow editor Current runs and Latest runs controls so run
  lookup/search select text is not clipped and action buttons align with the
  adjacent fields.

## 2026-05-22 - Unobtrusive Workflow Tutorial

- Collapsed the workflow editor first-run walkthrough into a small tutorial
  launcher by default, with a skip/mark-complete path that removes the first-run
  highlight without launching the walkthrough.

## 2026-05-22 - Explicit Agent Resume Threads

- Stopped agent resume calls from implicitly falling back to stored
  `agent_thread_id` metadata, and made PR-feedback queries prefer configured
  Azure DevOps projects over repo-slug inference.

## 2026-05-22 - Ansible Service Lock Ownership Repair

- Made the control-plane Ansible runtime bootstrap repair ownership of managed
  service lock files before service startup, so stale root-owned wake locks do
  not keep Discord or scheduled wake workers in restart loops.

## 2026-05-22 - Workflow-Owned ADO Work-Item Intake

- Added `trigger:ado:work-item-matching` for side-effect-free Azure DevOps
  work-item polling on first match and remote changed-date advances.
- Added `effect:issue:upsert-ado-proxy` to materialize single or batched ADO
  work items into local proxy issues and `runtime/ado-work-items` YAML.
- Removed ADO work-item/backlog polling from the live ADO worker loop; it now
  remains responsible for Azure DevOps PR feedback and completion events.

## 2026-05-22 - Prompt Thread Metadata Collections

- Added keyed agent prompt resume-thread storage under session metadata
  collections, defaulting to `agent_thread_ids`, with per-effect collection
  overrides for `effect:agent:prompt` and `effect:agent:enqueue-prompt`.
- Stopped keyed prompt resume metadata from writing dynamic thread keys at the
  top level of session metadata.

## 2026-05-22 - Generic Session Metadata PR Routing

- Added `query:session:list` for generic session metadata matching, directory
  scoping, active/expired filtering, and bounded session-list outputs.
- Added `query:pull-request:feedback` so workflows can poll provider PR
  feedback and own cursor storage outside the ADO worker.
- Documented the incremental migration path from PR-map-driven ADO worker
  feedback routing toward workflow-owned session metadata conventions.

## 2026-05-22 - Dashboard Worker Disk Visibility

- Added worker-root disk usage to the Admin Web dashboard Capacity card using existing
  container-worker telemetry, including a worker-disk health pill when telemetry is available.

## 2026-05-21 - Rejected Issue Listing Filter

- MCP `list_issues(status=["Rejected"])` now returns closed local issues with a
  rejection reason while preserving the existing closed/completed filtering
  semantics.

## 2026-05-21 - Container Agent Status Cleanup Fix

- Stopped per-execution container cleanup tombstones from making active or reusable
  container-worker sessions appear as `cleaned_up` on the Agents page.

## 2026-05-21 - Admin Dashboard Latency Fixes

- Cached dashboard-scoped capacity-health reads, stopped capacity health from probing
  historical container-worker execution URLs, and moved slow dashboard audit/activity reads off
  the Admin Web event loop.
- Added a dashboard-only tail scan for recent tool-call audit entries so the operations dashboard
  no longer parses the full audit log just to render the latest calls.
- Made workflow graph activity reports read SQLite activity columns directly instead of
  deserializing full orchestration payloads for dashboard summaries.
- Initialized the orchestration SQLite schema before direct workflow graph activity queries so
  fresh default stores return empty reports instead of failing on missing tables.

## 2026-05-21 - Dashboard Product Context Layout and 0.2.2 Version

- Bumped the project and workflow-editor package metadata to version `0.2.2`.
- Kept Product context as the dedicated left dashboard column, stacked the other top-level
  dashboard panels in the right column, and moved News above Release metadata.

## 2026-05-21 - Runner Image Codex Binary Resolution

- Forced optional Codex platform packages during Azure runner-image preparation and made the
  runner-image build script resolve nested, hoisted, and Node-resolved npm layouts before failing.
- Installed the Linux Codex platform package explicitly in the Azure worker archive step using the
  version declared by the installed Codex package, so hosted agents do not depend on npm optional
  dependency behavior to provide the executable.
- Resolved both the current `vendor/<target>/bin/codex` package layout and the older
  `vendor/<target>/codex/codex` layout when copying the native Codex binary into the runner image.

## 2026-05-21 - Dashboard Capacity Health

- Wired the existing Admin Web capacity-health endpoint into the operations dashboard so operators
  can see host disk usage, known storage, session storage, worker telemetry, queue age, and unread
  prompt pressure from the default landing view.

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
  `evander-sync.lock` used by the restricted SSH upload-pack wrapper, preventing concurrent
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
  exports `EVANDER_ARTIFACTS_DIR`.

## 2026-05-06 - Queued Prompt Thread ID Handling

Touched areas: `src/evander/effects/providers/lifecycle_hooks.py`,
effect orchestrator tests.

- Stopped queued libvirt `effect:agent:enqueue-prompt` dispatch from publishing
  synthetic dispatch IDs as Codex `thread_id` outputs when `resume_agent`
  succeeds without returning a real thread id. The queued dispatch path now
  omits `thread_id` in that case, allowing downstream fallback bindings to keep
  using existing session metadata instead of persisting a non-existent Codex
  thread.

## 2026-05-06 - Azure PR Review Publish Failure Handling

Touched areas: `src/evander/services.py`, PR publish tests.

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

Touched areas: `src/evander/effects/`, `src/evander/workflow/`,
`frontend/workflow-editor/`, workflow pack config, orchestration tests.

- Added named `started` and `completed` lifecycle ports to
  `effect:agent:enqueue-prompt`, matching the existing `effect:agent:prompt`
  contract. The runtime now emits `started` after dispatch and `completed` when
  the queued agent run reconciles; legacy unported enqueue edges migrate to
  `started` (`095e0604`).

## 2026-05-03 to 2026-05-04 - Worker Isolation, Package Cache, and Private Git

Touched areas: `ansible/`, `src/evander/worker/`,
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

Touched areas: `src/evander/admin/`, `src/evander/effects/`,
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

Touched areas: `src/evander/execution/`, `src/evander/worker/`,
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

Touched areas: `src/evander/execution/`, `src/evander/worker/`,
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

Touched areas: `src/evander/admin/`, `src/evander/repositories/`,
`src/evander/vm/`, `frontend/workflow-editor/`, `ansible/`, admin tests.

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

Touched areas: `src/evander/workflow/`, `src/evander/effects/`,
`src/evander/triggers/`, workflow pack config, orchestration tests.

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

Touched areas: `src/evander/gateway.py`, `src/evander/services.py`,
`src/evander/models.py`, Teams/ADO tests, prompt store, frontend/admin
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

Touched areas: `src/evander/triggers/`, `src/evander/effects/`,
`src/evander/workflow/`, `frontend/workflow-editor/`, orchestration docs
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

Touched areas: `src/evander/gateway.py`, `src/evander/runtime.py`,
`src/evander/admin/`, `scripts/`, model and heartbeat tests.

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

Touched areas: `src/evander/admin/`, `src/evander/runtime.py`,
`src/evander/gateway.py`, issue services, worker config, Teams/Discord
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

Touched areas: `scripts/`, `src/evander/admin/`,
`src/evander/cli.py`, `src/evander/runtime.py`, `docs/`, `systemd/`.

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

Touched areas: `scripts/`, `src/evander/ado_issue_worker.py`,
`src/evander/email/`, `src/evander/gateway.py`, Git automation docs,
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

Touched areas: `src/evander/`, `scripts/`, `config/`, `systemd/`,
`README.md`, `docs/`, bootstrap and relay tests.

- Created the initial Evander scaffold: config, runtime storage, audit,
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

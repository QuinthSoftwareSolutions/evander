# Changelog

## 2026-10-24 - Control unfinished tracked reviews on continuation (0.128.0)

- Add workflow inputs for exact tracked-file exclusions, rebuilding unfinished file batches, and bounded retries that retain their scope. Preserve accepted review credit, findings, immutable native evidence, snapshot identity, route checks, cleanup and fixed run budgets.
- Release Codex Security add-on 0.5.0. Excluded files stay explicitly unreviewed in progress and reports, and any exclusion keeps overall coverage partial. Original source journals remain unchanged; rebuilt batches retain the existing byte and file limits.
- Keep repository-wide outstanding obligations with their assigned boundary reviews instead of attaching the entire queue to every ordinary file review. Required questions remain retained; partial native results still cannot grant file coverage.

## 2026-10-24 - Retire obsolete polling wakes and configuration (0.127.3)

- Remove the standalone Discord wake worker, guest heartbeat timers, and unused wake and automation directory configuration. Discord message workflows continue to dispatch their configured prompt effects; scheduled wakes retain their shared dispatcher.
- Remove retired settings from the operator UI, CLI, health reporting, and deployment registrations while preserving Managed Git configuration and model fallback behavior.

## 2026-10-24 - Keep coverage journal readers from blocking review progress (0.127.2)

- Release Codex Security add-on 0.4.1 with write-ahead logging for new and existing coverage journals, allowing review updates to commit while status or retention readers hold a snapshot.
- Read only retention metadata when checking continuation lifetimes and protected ancestors. Load full journals only for actual expiry candidates, preserving accepted evidence, cleanup-based retention and snapshot pins without materializing historical finding payloads.

## 2026-10-24 - Repair Docker apt access during worker deployment (0.127.1)

- Permit HTTPS tunnels only to the Docker and Microsoft package hosts in the default apt-cache allowlist.
- Normalize existing Docker apt source URLs to HTTPS before the worker's first apt refresh, preserving source options and signing keys so legacy HTTP redirects cannot block deployment. Keep worker package traffic on the private proxy.

## 2026-10-24 - Configure tracked-review parallelism per scan (0.127.0)

- Add the bindable workflow input `max_parallel_reviews`, accepting integers from one through four. New tracked scans default to two; linked continuations inherit their saved limit unless explicitly changed. Unsupported providers and coverage modes reject explicit limits before launch.
- Release Codex Security add-on 0.4.0 with frozen per-scan scheduling limits in journals, worker inputs, progress and aggregate reports. Architecture mapping remains serial, and uncertain launches, report retention and cleanup continue occupying slots across restarts. Historical scans retain two-review scheduling; active requests cannot change their limit.
- Keep original snapshots, accepted coverage, findings, native proofs, batching, deadline and access-route checks unchanged. This host-side change does not require a scanner-image rebuild or a different SDK.

## 2026-10-23 - Avoid redundant session metadata writes (0.126.2)

- Merge session metadata against the latest manifest under the repository lock and skip unchanged writes on the canonical JSON session store, preserving existing deep-merge rules and response fields.
- Preserve concurrent manifest updates and reject stale metadata updates after session deletion or replacement. Continue accepting legacy manifests without a saved creation timestamp.

## 2026-10-23 - Reduce control-plane processing and memory overhead (0.126.1)

- Copy only the requested directory from immutable configuration snapshots, and retain only the requested PR feedback page while selecting the oldest matching messages.
- Stream atomic JSON writes and portable index exports instead of retaining complete serialized files, source rows, and SCIP blobs in memory.
- Reuse source-line widths during code-index range validation and use a linear cycle check for valid workflow graphs.
- Defer workflow binding graph analysis until bindings need it, reuse cached worker log projections during append and outcome evaluation, and bound raw log pagination allocations to the requested page.

## 2026-10-23 - Reconcile session policy from workflow settings (0.126.0)

- Add `effect:session:reconcile-policy` to apply settings-owned tool grants, argument restrictions, approvals, connector grants, and metadata to an existing session without replacing its identity, model, conversation, or workspace.
- Reconcile atomically against the latest manifest, enforce workflow directory scope, preserve unmentioned fields, and avoid writes when the policy already matches.

## 2026-10-23 - Configure tracked-review text budgets per scan (0.125.0)

- Add the bindable workflow input `max_batch_text_bytes` for tracked security scans: integer budgets from 1 MiB to 2 MiB, with 1 MiB for new scans and the saved budget inherited on resume when omitted. Reject unsupported providers and coverage modes before launching work.
- Release Codex Security add-on 0.3.0 with a frozen version-4 budget in progress, coverage ledgers and manifests. Explicit continuation changes regroup only untouched pending root scopes; accepted coverage, findings, native proofs, attempted/split scopes, original snapshots, cleanup and idempotency checks remain intact. Active historical plans retain their original limits; the pinned SDK and scanner image are unchanged.

## 2026-10-23 - Larger tracked-coverage batches on continuation (0.124.2)

- Raise the optional Codex Security add-on's tracked-review text budget to 1 MiB while preserving the 100-entry cap, intact oversized files and two-child concurrency limit.
- On explicit continuation, upgrade unstarted root scopes from earlier batching policies without changing accepted coverage, native proofs, attempted scopes, split refinements or source journals. Record batching version 3 and retain strict snapshot, route, cleanup and idempotency checks. This host-only policy change does not require a scanner-image rebuild.

## 2026-10-23 - Preserve resumed scan subscriptions and admit purchased capacity (0.124.1)

- Allow agent-creation workflows to bind the saved subscription profile when resuming a scan. Validate the resolved profile on its selected worker and reject missing bindings instead of falling back to the legacy default; ordinary resumes retain strict scan configuration compatibility.
- Use one capacity decision for worker admission and queued-prompt recovery: existing purchased usage credits can cover exhausted included windows in the same rate-limit bucket. Explicit provider rate/spend blocks, account pins and profile isolation remain enforced; no reset credit is read or redeemed by this decision.

## 2026-10-23 - Discover Azure DevOps builds by number (0.124.0)

- Add the read-only `list_ado_builds` MCP tool with build-number, pipeline, repository and branch filters, bounded pages and continuation tokens. Return build IDs, pipeline and repository identities, source details and URLs so callers can distinguish builds that share a number.
- Require an explicit tool grant and use the session directory's existing Azure DevOps configuration and host credential. Existing agent grants are unchanged; the updated Azure DevOps add-on requires Evander 0.124.0.

## 2026-10-23 - Support MCP SDK 2 and modern protocol requests (0.123.0)

- Migrate the gateway and add-on MCP surfaces to SDK 2.3 while retaining legacy clients. Enforce session proof, current tool grants, workflow leases, exact invocation approvals and auditing on modern requests without a transport session ID.
- Update the Figma and Miro MCP clients for HTTPX 2, explicit pagination and the SDK's response models, preserving authenticated destinations and bounded timeouts. Set bounded SSE wire limits that accommodate base64 screenshots and paired structured/text results while retaining the existing decoded-content limits. Constrain the SDK to the supported major version and refresh the dependency lock.
- Preserve actionable policy and domain-validation errors without Pydantic input dumps. Unexpected adapter failures retain their operator audit details and use the SDK's sanitized caller-facing errors.
- Retain support for existing Elasticsearch bulk and Kibana import sizes through an explicit bounded gateway HTTP wire limit, without changing their logical payload limits.
- Require Evander 0.123.0 for the five migrated optional add-ons so dependency resolution rejects core releases that still use MCP SDK 1.

## 2026-10-23 - Retry temporary model-capacity failures (0.122.1)

- Recognize temporary Codex model-capacity and explicit server-overload errors separately from subscription quota exhaustion. Durably resume the same session and native conversation without changing the selected model, billing route or dispatch identity.
- Retry up to eight times within one hour with jittered exponential backoff capped at fifteen minutes. Preserve the retry budget across restarts and repeated reconciliation; model overload does not pause other models on the same subscription.
- Report exhausted retries as failed workflow executions, publish failure completion, and use the configured error branch. Expire overdue queued retries before launching another worker job.
- Activate retries on older workers through their last explicit native capacity error so worker upgrades can wait for live model brokers to become idle.
- Preserve scheduled-wake completion and directory scope when queued retries expire. Restore accepted worker executions and their real outcomes after interruption before reporting deadline failure, and use effective models for subscription FIFO ordering.

## 2026-10-23 - Explicit subscription handoff for tracked scan resumes (0.122.0)

- Add the optional, bindable `resume_subscription_profile_id` input for a deliberately requested ChatGPT-subscription handoff on a linked tracked-coverage resume. It must match the new scanner session; ordinary resumes still require the original profile.
- Preserve exact snapshot, inventory, repository access, policy/SDK, model, reasoning, Cyber program and image compatibility checks, plus repository reservations, cleanup and idempotent recovery. Reject API-billing conversions and route changes during a run.
- Release Codex Security add-on 0.2.0 with immutable-source reuse and a handoff receipt in the new aggregate manifest and coverage ledger. The pinned SDK and scanner-image runtime are unchanged.

## 2026-10-22 - Pack tracked security reviews across folders (0.121.2)

- Pack adjacent paths and small non-text entries into bounded review scopes instead of starting a job for each folder or non-text file. Preserve the 100-entry and 512 KiB text limits, intact oversized files and explicit non-text review requirements.
- On explicit continuation, regroup only untouched legacy scopes while retaining accepted coverage, findings, attempted scopes, split refinements and outstanding boundary assignments. Keep active runs and source reports unchanged, and record the batching policy in aggregate reports.
- Preserve completed and split scopes when cancellation accepts a saved result; reuse already accepted map and file coverage when continuing older journals with stale scheduling flags.

## 2026-10-22 - Keep gateway installs on the compatible MCP SDK (0.121.1)

- Bound the MCP Python SDK to the supported 1.x release line while retaining the 1.30.0 security floor. Fresh and upgraded installations no longer select MCP 2.x, which removes the FastMCP API used by the gateway.

## 2026-10-22 - Preserve workflow scope, tool grants and inference attribution (0.121.0)

- Let shared PR-publication workflows select the owning project directory while preserving repository, commit and publication-identity checks and rejecting scope escapes.
- Preserve canonical built-in tool names when creating or updating agents, reject serialized enum names in new requests, and keep existing stored add-on grants and approval settings readable.
- Add bindable Smart-switch inference cost categories, retaining inherited or explicit Uncategorized attribution across queued execution and restarts without changing provider input or token accounting.

## 2026-10-22 - Decompose control-plane orchestration (0.120.4)

- Separate worker reference checkout, task worktree preparation, and dependency composition into per-invocation stages while retaining Git validation, recovery, locking, and persistence order.
- Give workflow binding resolution, active-run snapshots, and graph activity reporting explicit invocation state and focused stages, preserving reference traces, report projections, and SQLite read budgets.
- Separate Team blocker updates, task cancellation, and dispatch activity finalization into ordered applications inside their existing command and repository transactions.
- Extract relay HTTP routing and provider request preparation, and share Azure/OpenAI-compatible forwarding while preserving request policy, streaming, steering delivery and retry, and usage accounting.

## 2026-10-22 - Simplify control-plane ownership boundaries (0.120.3)

- Separate agent creation and VM log services, PR publication and feedback binding, workflow target preparation, and Bokio execution while retaining existing entry points, permissions, saved formats, and Save behavior without migrations.
- Run workflow workers directly, distinguish empty provider catalogs from omitted defaults, and centralize managed tool audit emission while preserving summaries, cancellation handling, durable ordering, and legacy Team action compatibility.
- Consolidate workflow inspector draft state and split pricing and Teams authentication out of the configuration screen, preserving incomplete drafts, revision checks, and navigation behavior.

## 2026-10-22 - Update vulnerable dependencies and toolchains (0.120.2)

- Raise Python runtime, development and build dependency security floors across the core and independent add-ons, and refresh the Python and affected npm lockfiles.
- Update Node to 24.22.1, the code-index .NET SDK to 10.0.113, Nexus to 3.97.1 and Qdrant to 1.19.2 across deployment defaults, bootstrap paths and toolchain metadata.
- Preserve existing container session Node paths and guest profile upgrades and make Nexus mandatory telemetry acknowledgement an explicit operator setting.
- Synchronize the hosted tab-viewing regression with the live tab picker before waiting for selection responses.

## 2026-10-22 - Allow clean task ownership handoffs (0.120.1)

- Inspect historical task checkouts through their original sessions before preparing work for a new owner. Allow a fresh checkout only when every previous owner's checkout is clean and unchanged from its committed preparation receipt.
- Preserve foreign commits, dirty or missing work, incompatible provenance and active task writers for explicit recovery. Reuse the existing preparation and continuity flow without adding prompt instructions or changing review and release gates.
- Give existing checkout-preparation blockers one corrected retry after deployment without changing checkout identities, accepted recoveries or composition-conflict bindings. Suppress a persistent failure again after that attempt.

## 2026-10-22 - Configure persistent reasoning controls in workflows (0.120.0)

- Add a directory-scoped workflow effect for setting or clearing an existing session's native reasoning default, using the existing provider/model validation and preserving running turns and other session settings.
- Expose the saved default in session-context queries and publish the node in the workflow editor, so operator-owned chat commands can override automatic routing until explicitly reset without hard-coded command syntax.

## 2026-10-22 - View shared browser tabs without taking control (0.119.1)

- Allow hosted viewers to switch shared tabs while the agent or another viewer retains control, including while input is paused. Keep generation and expiry checks and require control for browser input.
- Publish Browser Sessions 0.9.4 with the corrected hosted controller and regression coverage for tab viewing and control handover.

## 2026-10-22 - Honor fresh-context assistant requests (0.119.0)

- Honor explicit `new_context` on immediate and queued prompts even when a saved or explicit resume thread is available. Reuse the existing queue, delivery receipts and thread persistence for workflow reset requests.
- Refresh queue-recovery fixtures with standard message metadata so batching and recovery during VM inventory failures reach their existing assertions.

## 2026-10-22 - Preserve correction sources beside planning evidence (0.118.2)

- Require a deliverable's own Git provenance before automatically including it as replacement or additional code for a rejected release. An inherited checkout base on artifact-only planning evidence no longer displaces the accepted coding correction or adds the plan back into its release.
- Preserve immutable review and release validation for partial Git claims and real code recoveries, including those marked as not planned for an initial release.

## 2026-10-22 - Bind shared-browser credentials to approved connections (0.118.1)

- Replace directory-editable environment credential selectors with encrypted platform-managed references bound to the complete worker, relay or speech destination and directory scope. Require owner access and unlocked privileges for connection or credential changes, while preserving ordinary browser management and agent tools.
- Resolve authorization before each HTTP, WebSocket, speech and cleanup request. Retain original session placement and reject legacy credential-bearing snapshots until explicitly migrated.
- Add a reviewed, maintenance-only migration with protected recovery backups for settings, saved connections and cleanup callbacks. Publish Browser Sessions 0.9.3 with owner-only credential controls and relay key delivery; Chrome extension and native helper versions are unchanged.

## 2026-10-22 - Delete unused subscription profiles (0.118.0)

- Add a privileged Delete action and worker/Admin APIs that remove a profile and its saved login. Refuse deletion while it has active jobs, queued prompts, or unexpired session bindings, and reject a changed worker selection.
- Stop creating an empty Default profile on worker startup. Preserve existing legacy logins, allow their profile to be deleted, and retain other profiles and recorded usage history.
- Normalize worker deletion IDs before checking active jobs, and coordinate deletion with new, reassigned, or renewed session bindings across control-plane processes. Revalidate profiles before saving a new live binding.

## 2026-10-22 - Preserve symlink identity in tracked security reviews (0.117.8)

- Review assigned symlinks through deterministic Git metadata projections so the pinned SDK can assess internal, broken, external, cyclic and directory links without following their destinations.
- Independently verify the projected revision and original link objects before granting coverage; retain explicit dispositions, immutable native reports and projection provenance across interruption and resume.
- Package the projection runtime in scanner images and include it in cache invalidation.

## 2026-10-22 - Preserve committed task work across preparation changes (0.117.7)

- Carry clean, compatible committed work into a fresh task checkout after instruction changes. Verify the task owner, session, repository, directory, original base and exact dependency pins; retain commit authorship and normal acceptance and release gates.
- Persist the selected source before transferring it, seal the restored HEAD in the normal preparation receipt, and expose its unreviewed provenance in the fresh Git context. Preserve dirty, divergent, missing or incompatible work for explicit recovery, and reuse the exact selected commit after interruption.
- Give reactivated specifications a fresh, stable preparation identity so returning to earlier instructions retains newer compatible work without changing old receipts. Carry only compact restored-work provenance into final wake prompts, trim repeated guidance, guard prompt-size budgets, and refresh installed guidance for existing sessions.

## 2026-10-22 - Preserve security-scan cleanup when saved evidence is unavailable (0.117.6)

- Stop safely if older coverage evidence is missing or corrupt: prevent further reviews, continue worker cleanup, and hold the repository reservation until cleanup is confirmed. Report the archive as unavailable without publishing unverifiable evidence, and retain the failure details in private host logs.

## 2026-10-22 - Keep advisory security questions separate from unfinished review (0.117.5)

- Accept complete, sealed static security reviews that contain advisory open questions while continuing to require exact scope, revision, file dispositions and resolution of genuine deferred work. Retain the questions and their source receipts in aggregate reports and coverage ledgers.
- Repair question-derived blockers in active or resumed legacy coverage journals using verified preserved reports. Preserve ambiguous and genuinely deferred work, existing file credit, original deadlines and immutable reports from earlier runs.

## 2026-10-22 - Share failed verification evidence from blocked Team tasks (0.117.4)

- Let the existing Team deliverable handoff publish retained failed or incomplete verification artifacts with `evidence_only=true`, returning immutable receipts and notifying the superior and task requesters while preserving task blockers, Git work contexts, acceptance, review, and release gates.
- Authorize only that evidence operation from an owned task-specific request in the current dispatch. Retain ordinary task mutation boundaries, artifact path checks, and idempotent publication and wake recovery across context rotations.

## 2026-10-22 - Ground browser decisions in control affordances (0.117.3)

- Describe generic text replacement, character typing, picker activation and keyboard operations separately. Offer text entry only when an exact input and writable field exist, and reject incompatible action/target combinations before input. Keep operation, target and input selection in one bounded provider request.
- Return metadata-only diagnostics for ambiguous choices, including the selected option, probability, threshold and up to three competing option IDs. Preserve them in retry/status receipts without retaining page labels or input literals.
- Use snapshot-local target aliases and omit transport receipt IDs from classification evidence and no-progress detection; map commands back to the original browser references and preserve their execution fences. Allow supported keys to target the single observed focused control when no explicit target was selected.
- Complete native picker clicks when mouse-down changes that control's selection/expansion, retaining checks on other form fields, target identity, document, origin and control generation. Publish Browser Sessions server package 0.9.2 and Chrome extension 0.6.2; native helper 0.4.0 remains compatible.

## 2026-10-22 - Use generic agent wording throughout Browser Sessions (0.117.2)

- Use generic agent labels and Evander platform naming in browser viewers, the Chrome extension, workflow help, and documentation.
- Publish Browser Sessions server package 0.9.1 and Chrome extension 0.6.1 with refreshed viewer assets and extension download.

## 2026-10-22 - Show whole subscription credits (0.117.1)

- Round subscription credit balances to the nearest whole credit for display.

## 2026-10-22 - Separate application environments from shared browsers (0.117.0)

- Add an independent optional Application Environments project with bounded application leases, private HTTP endpoints, safe checkout mounts and SQL Server add-on resource binding beyond agent turns.
- Make hosted Browser Sessions target approved external sites or leased application endpoints without requiring application recipes or hosting.
- Add explicitly granted Playwright connections, retained browser contexts, managed-login tools and shared page selection with generation-fenced human takeover.
- Supply independent deployment assets and a read-only coordinated migration checker; legacy creation calls must migrate after existing hosted previews drain.
- Confirm expired application cleanup before reusing quota, require a fresh connection credential after browser detach, and drain or cancel hosted login commands before granting human control in every hosted profile, including when Playwright is disabled.

## 2026-10-21 - Support bounded Azure DevOps review votes (0.116.0)

- Let `submit_pr_review` approve, approve with suggestions, wait for the author, or reset the authenticated host identity's reviewer vote after publishing its comments. Preserve comment-only submissions and GitHub review behavior; reject unsupported votes, including Reject.
- Restrict voting to session-bound PRs where the host identity is already a reviewer, confirm the returned identity and vote, and include the vote in tool results and audit records.
- Mark positive votes as reapprovals so reviewing a new PR iteration records fresh approval even when the numeric vote is unchanged.

## 2026-10-21 - Show subscription credit balances (0.115.0)

- Show provider-reported credits beside seven-day remaining usage and banked resets on the Subscriptions page, including zero, unlimited, and unavailable balances.
- Carry only the approved credit fields through worker telemetry, and keep the profile list readable on narrow screens.

## 2026-10-21 - Keep browser decisions focused on page state and the next action (0.114.2)

- Exclude random observation receipts, document epochs and command receipt IDs from decision-model evidence. Preserve current element references, values, context, action outcomes, browser fences, returned observations and usage attribution.
- Ask for the next unfinished action and its target using current field state and the task's ordering dependencies. Clarify ordered objectives and observed control names in the shared agent guide and request schema, without application-specific selectors or workflows.
- Publish Browser Sessions server package 0.8.2. Chrome extension and native helper versions are unchanged.

## 2026-10-21 - Preserve valid browser decisions and publish agent operating guidance (0.114.1)

- Offer explicit no-key/no-scroll choices for irrelevant batched decision questions, avoiding refusals caused by missing applicable answers. Keep probability thresholds and provider validation, and reject missing keys/directions before issuing input.
- Explain exact named input values, prewritten text, empty-string clearing, bounded objectives and final verification in tool descriptions, argument schemas and missing-input results. Identify the relevant choice and probability when a decision falls below its threshold.
- Package an application-neutral operating guide with the add-on. Any authorized agent receives it through profile discovery and session creation, or requests it from status without depending on personal-assistant prompts. Include the launch-manifest schema and cover hosting, local Chrome, control handover, chat, recovery and cleanup.
- Publish Browser Sessions server package 0.8.1. Chrome extension and native helper versions are unchanged.

## 2026-10-21 - Add hands-free browser voice and smooth speech playback (0.114.0)

- Add opt-in hands-free voice in the Chrome sidebar and shared viewers, with local speech detection, a short lead-in, lossless boundaries between bounded clips, ordered transcription, and spoken-reply interruption when the user starts speaking. Idle periods produce no audio uploads.
- Keep an enabled microphone connected through idle periods and window focus changes. Continue cancelling partial push-to-talk recordings on focus loss, and release the device when explicitly disabled, hidden, or disconnected.
- Buffer streamed speech before playback and after underruns to avoid playing a tiny fragment ahead of a delayed response. Use Onyx as the default speech voice while preserving configured overrides.
- Publish Browser Sessions server package 0.8.0 and Chrome extension 0.6.0.

## 2026-10-21 - Execute short browser objectives with decision models (0.113.0)

- Add opt-in PA-owned multi-step browser execution through the directory’s Jev or OpenAI Decisions provider. Reuse the live shared browser, exact PA-supplied input values, existing permissions, provider privacy checks and usage attribution; return results for PA verification.
- Add metadata-only gateway auditing for sensitive add-on tools, durable run receipts, single-session automation leases, execution budgets, target revalidation, and Stop controls in hosted viewers and the Chrome sidebar. Never replay uncertain mutations or resume interrupted runs. Retire interrupted command leases so a fresh authorized objective can proceed, preserve pre-input stale-target rejections across the relay, and tolerate lock contention during concurrent database initialization.
- Enrich screenshot-free observations with field state, page text and row/form context across hosted Chrome, compatibility mode and the native helper. Publish Browser Sessions server package 0.7.0, Chrome extension 0.5.0 and native helper 0.4.0, with explicit capability negotiation for older installations. Discard queued frames from older capture settings before adapting native stream resolution again.

## 2026-10-21 - Allow browser-chat follow-ups and prevent duplicate display (0.112.0)

- Let the creating agent send any number of progress updates and replies to a browser message. Give outgoing replies their own retry identities, retaining session/channel authorization and compatibility with existing callers and stored replies.
- Serialize hosted-viewer and legacy voice-page transcript refreshes, and follow an in-flight poll with a fresh read after Send. Render each message ID once while preserving delivery-status updates.
- Publish Browser Sessions server package 0.6.0. The Chrome extension remains 0.4.1.

## 2026-10-21 - Stream shared-browser speech and simplify the viewer (0.111.0)

- Fill the hosted viewer with its browser, compact toolbar and chat sidebar. Move session details into the toolbar, indicate control ownership with one takeover/handback button, and add a saved light/dark toggle for standalone viewers.
- Align browser-session settings in grouped responsive sections, with worker and launch-profile names above their fields.
- Show dismissible inline errors without covering chat controls. Treat observe-only clicks as brief reminders that also clear on takeover. Send non-empty chat with Enter and retain Shift+Enter for newlines and normal input-method composition.
- Keep an enabled microphone ready between push-to-talk clips, avoid browser gain/noise processing that can suppress phrase onsets, and provide an explicit microphone-off control. Stop capture on focus loss, hiding, session changes and disposal, including capture that finishes opening after focus was lost.
- Start spoken replies from bounded PCM chunks while synthesis is still running, retain authorization and cancellation throughout streaming, and preserve WAV compatibility for older clients. Publish Browser Sessions server package 0.5.0 and Chrome extension 0.4.1.

## 2026-10-21 - Respect container isolation for brokered SDK reviews (0.110.2)

- Let an operator-owned Codex runtime profile select `sandbox_policy: container` when Docker already provides the execution boundary and nested native namespaces are unavailable. Apply that policy to SDK exec and preflight processes, including resumed reviews, while keeping native sandboxing as the default.
- Keep subscription routing, worker-held credentials, container privileges, mounts and egress restrictions unchanged. Record the selected sandbox policy in private runtime diagnostics.

## 2026-10-21 - Keep shared browser views focused and follow Admin Web themes (0.110.1)

- Apply the shared Admin Web light/dark palette to hosted browser viewers, session lists, settings, chat, voice controls and status/error surfaces. Follow live host theme changes and the saved or system preference in standalone viewers.
- Hide closed and expired hosted sessions by default, with a Show closed sessions option for history. Keep failed and cleanup-pending sessions visible.
- Package the shared theme assets with Browser Sessions server package 0.4.1. The Chrome extension remains 0.4.0.

## 2026-10-20 - Steer active conversations from chat (0.110.0)

- Add opt-in native Codex steering for Teams personal-assistant follow-ups and shared-browser messages, preserving the running job, conversation and reply destination. Queue unsupported runners without interrupting them.
- Persist message and worker delivery receipts, reconcile uncertain acknowledgements without blind retries, preserve ordered queued messages, and cancel undelivered browser requests when sharing ends.
- Show per-message delivery status in the hosted viewer and Chrome sidebar. Preserve delivery receipts during concurrent retries, coordinate cancellation with native sends, and rotate status polling across unresolved messages. Publish Browser Sessions and Chrome extension 0.4.0.
- Add an activity query and offline workflow transformation for a busy follow-up route that skips idle preparation. Document worker-first rollout and revision-checked workflow activation.

## 2026-10-20 - Provision fresh browser-preview SQL databases (0.109.1)

- Finish creating a disposable SQL Server database before compiling the batch that selects it, so first-time hosted previews can initialize their schema. Preserve scoped credentials and retryable cleanup.
- Publish Browser Sessions server package 0.3.1; the Chrome extension remains 0.3.0. Add an opt-in real SQL regression check for provisioning, isolation and database/login cleanup.

## 2026-10-20 - Add sidebar voice and Chrome extension coexistence (0.109.0)

- Add optional push-to-talk recording and interruptible spoken replies beside typed chat in the Chrome sharing sidebar, without opening a separate voice tab.
- Add an optional standalone native-messaging helper for full Chrome input, accessibility and screencasting while retaining the extension sidebar and other extensions. Include per-user installation, release packaging and helper downloads.
- Fall back to tab-specific video capture and isolated application DOM actions when another extension prevents debugger access; leave protected extension frames intact and keep local input grants and origin checks.
- Use a one-time microphone permission window when needed; keep recordings, playback and the shared transcript in the sidebar.
- Bind voice requests to the current sharing session and stop recording, playback and pending transcription when the sidebar or sharing session closes or changes.
- Keep voice capabilities separate from input control, preserve hosted-viewer and older-extension voice access, and publish Browser Sessions package and Chrome extension 0.3.0.

## 2026-10-20 - Track and continue repository security coverage (0.108.0)

- Add opt-in tracked-file coverage to the optional Codex Security provider for managed, full-snapshot Standard scans on container workers. Persist the pinned inventory, bounded scopes, review receipts, retained findings and cleanup ownership in protected host storage.
- Schedule at most two scoped SDK reviews with a six-hour deadline, restart recovery and bounded retries. Explicit continuation starts a linked run at the original retained snapshot, preserving accepted work and immutable earlier reports.
- Add the directory-scoped scan metadata query, live workflow progress and deterministic Evander aggregate reports with the original native reports. Keep coverage retention independent of private diagnostics and preserve repository reservations during uncertain worker cleanup.
- Package and fingerprint the new scanner runtime files. Existing provider-policy, commit-range and deep scans retain their behavior; existing agent sessions are unchanged.
- Keep parallel boundary reviews independent through durable work assignments, recheck every retained sealed native artifact after transport, and preserve valid finding writeups with native SDK filenames.

## 2026-10-20 - Improve shared browser keyboard and pointer input (0.106.1)

- Send character key events for shared-browser typing so scanner input and page keyboard listeners receive the same characters as editable fields.
- Generate Enter and Space text events so scanner submission and normal form submission work through the bounded key action.
- Scroll referenced click/hover targets into view inside tables and other containers; return bounded recovery errors instead of hiding browser failures behind a generic conflict.
- Preserve per-event control and origin checks, direct field filling, and Unicode input. Publish Browser Sessions server package 0.2.5 and Chrome extension 0.2.4.

## 2026-10-20 - Scope Miro board approvals to agent sessions (0.106.0)

- Require exact session board grants for agent tools and Miro workflow operations, invalidate former shared approvals, and grant newly created boards only to their creating session.
- Enforce workflow tool argument restrictions on live and cached operations, and record authorization denials before dispatch as not applied while retaining uncertain outcomes after possible writes.
- Add revision-checked session permission controls bound to the selected session instance in Admin Web and workflow-only board access checks and authenticated chat approval effects.
- Provide an opt-in Smart switch approval workflow with explicit user consent, pending-request isolation, durable replay protection, confirmations and task continuation.

## 2026-10-20 - Keep local browser sharing with the creating agent (0.105.4)

- Let the agent that creates a local Chrome sharing session observe and interact with it using its existing conversation context and browser tool grants.
- Route extension messages, input grants and saved screenshots to that same agent, without creating a separate support agent.
- Pin browser feedback to the creating conversation, including channel-scoped Teams threads, and wait for its recorded native thread instead of resuming unrelated recent work.
- Preserve explicit local control, origin and ownership checks, and cleanup of support agents from older sessions. Browser Sessions server package 0.2.4 remains compatible with Chrome extension 0.2.3.

## 2026-10-20 - Diagnose Chrome sharing attachment failures (0.105.3)

- Show the original browser error and failing setup step when attaching a shared application tab.
- Preserve session cleanup on attachment failure and publish the updated Chrome extension as version 0.2.3.

## 2026-10-20 - Clarify browser extension tab sharing (0.105.2)

- Use application-neutral instructions throughout Chrome tab sharing.
- Distinguish missing tab access from an absent application tab or an unsupported page, with specific recovery guidance before consuming an invitation.
- Publish the refreshed Chrome extension as version 0.2.2.

## 2026-10-20 - Preserve configured browser deployment (0.105.1)

- Retain public browser relay routing, refresh the optional package on provisioned hosts, and restart running browser relay and supervisor services during managed deployments.
- Allow host-approved preview container UIDs and GIDs, pin them per preview, and match output ownership to the selected identity.
- Hand Docker a protected host bind mount of the checked checkout inode, and keep deletion guards active until that mount is released.
- Create local browser support agents through the configured core worker selection, without treating browser supervisor labels as registered worker IDs.

## 2026-10-19 - Talk during shared browser sessions (0.105.0)

- Add optional push-to-talk transcription and interruptible spoken replies to hosted viewers and invited local browser support.
- Open local voice chat from Chrome using a narrow expiring companion-page capability; preserve session transcripts, page annotations and browser control grants.
- Keep speech credentials on the host, bound provider requests and retain text outcomes without saving audio.
- Include guided voice settings, packaged extension/UI assets and synthetic speech validation in real Chrome.
- Cancel recordings when Talk loses keyboard focus, keep long transcripts in the scrollable chat and enforce clip duration through bounded host audio decoding before transcription.

## 2026-10-19 - Share hosted previews and local Chrome sessions (0.104.0)

- Add an optional Browser Sessions add-on for hosted demonstrations, live development previews and explicitly shared user-hosted Chrome tabs.
- Require Admin UI sign-in and directory permissions for hosted viewers; add single-use local invitations, browser-only support agents and fenced input handover.
- Keep chat transcripts distinct while resuming the authoring task; enforce preview leases, approved profiles, isolated worker resources and cleanup before checkout deletion.
- Include opt-in deployment assets, extension packaging and focused Python/Chrome validation.

## 2026-10-19 - Configure Cyber access programs for agents and scanner runtimes (0.103.0)

- Add Cyber access program selectors to agent creation, existing agent usage settings, and session model settings. Persist per-session choices and apply them to subsequent VM, metered container, subscription, and scanner turns.
- Expose the same optional selection on workflow Create agent nodes, preserving existing session choices when omitted and supporting explicit reset to Automatic.
- Allow worker-owned Codex runtime profiles to select a default Cyber access program for scanner turns, including resumed and SDK child executions; preserve explicit per-turn selections and leave program selection unspecified when no default is configured.
- Translate CLI Cyber program names into the native App Server protocol so explicit Daybreak requests are accepted instead of rejected as unknown values.
- Retain the native OpenAI provider identity so Codex includes the selected program on upstream requests, with worker-owned loopback routing and subscription authentication still enforced.
- Apply explicit Cyber programs to metered VM and container turns using the native provider and existing scoped relay URL/token, including resumed legacy sessions; fail before model execution when that relay configuration is unavailable.
- Record the requested program in private job diagnostics while retaining worker-owned subscription credentials, permission boundaries, and server-side access checks. Selecting a program does not grant enrollment or change the model.

## 2026-10-19 - Edit session-owned Teams messages (0.102.0)

- Add the optional Teams/Graph `teams_update_message` tool and deterministic `effect:teams:update-message` workflow effect for channel posts, replies, and existing chats.
- Record durable, session-owned send receipts separately from inbox refreshes; preserve rich content while replacing the recorded text region, and reject unowned or externally changed messages.
- Add guarded revisions, per-message process locks, pending-write reconciliation, audit events, guided workflow fields, and local stub post/edit behavior. Existing session grants and Microsoft scopes remain unchanged.
- Add opt-in replacement and clearing of one session-owned PNG region, shared file publication/approval checks, and a real-session `effect:teams:post-file` with durable creation idempotency. Other images, mentions, files and cards stay protected.
- Render deterministic progress PNGs with a bundled licensed Unicode font. Add optional render/sync effects with durable snapshots, coalescing, expiry renewal, notification-only recovery and verified Word/PDF delivery links; enablement defaults off.
- Serve signed, generated progress PNGs inline with `nosniff`, retain ordinary attachment downloads, and protect Teams receipt/cache files containing image bearer references. Channel consent, authenticated client acceptance and operator rollout remain separate from local implementation.
- Preserve source verification when text edits race image recovery, route timers through configured storage and trigger wake paths, and use executable workflow bindings in the progress examples.

## 2026-10-17 - Allow public login identifiers to overlap passwords (0.101.1)

- Accept complete approved public identifiers in managed website browser storage and JWT claims when they contain a private password substring.
- Continue rejecting separately reflected passwords, encoded credentials and credential-bearing cookies; retain existing connector checks.

## 2026-10-17 - Provision managed website logins in workflows (0.101.0)

- Add `effect:website-session:ensure-profile` to create exact-origin login profiles for disposable environments from host-approved source profiles and destination patterns.
- Keep credentials in encrypted host storage, return only safe profile metadata, and preserve directory isolation, concurrent retry idempotency, operator changes and credential revocation.
- Fail profile saves when the destination directory disappears, preserving root configuration and rolling back newly copied credentials.
- Include workflow editor fields, output schemas, host policy setup and credential-snapshot lifecycle guidance.

## 2026-10-17 - View daily inference categories by token usage (0.100.0)

- Add a Cost / Tokens selector to the daily category chart, including recorded API and subscription inference without requiring pricing. Keep dashboard and monetary summaries unchanged.
- Record new subscription App Server usage on workers, including child turns, and import credential-free receipts through authenticated, paginated execution reconciliation. Preserve dispatch-time categories and deduplicate repeated counters and deliveries.
- Retain worker receipts and pending writes through restarts and session cleanup, and retry prospective delivery after execution finishes. Show unavailable token counts as partial coverage; historical subscription imports remain manual, with no automatic migration or recovery.

## 2026-10-16 - Azure pipeline task callbacks and live agent logs (0.99.0)

- Add optional Azure DevOps effects for host-authenticated task completion and forwarding rendered agent progress into live and saved pipeline logs.
- Persist run ownership, log checkpoints, deadlines and bounded callback retries in existing workflow state; retried waiting tasks reuse the original test and result.
- Include generic event-driven pipeline examples. Pipeline results can depend on published test evidence while report delivery remains independent.
- Use supported editor field types, stable worker transcript snapshots and bounded final-log recovery after completion. Preserve callbacks for sessions without supported logs and order the example after deployment.
- Route live feeds through the Azure job record and keep worker-assignment and execution-database failures within bounded log recovery without blocking completion callbacks.
- Use project GUIDs for distributed-task writes and recover final logs after a lost callback acknowledgement or process interruption.
- Reserve terminal retry budgets before external requests so interruptions cannot reset callback or final-log limits.
- Preserve callbacks for missing or expired sessions and reconcile Azure's receipt after the final uncertain callback response.

## 2026-10-16 - Resolve Team resource ownership from persisted sessions (0.98.3)

- Preserve the canonical directory string from the session manifest when resolving Team ownership for SQL Server and RabbitMQ resources, avoiding an attribute error before acquisition.
- Verify both add-ons against reloaded session manifests while preserving task, dispatch and cancellation authority checks.

## 2026-10-16 - Keep Team session activity checks out of execution history (0.98.2)

- Check active prompt ownership through the existing status/type index and a lightweight target projection, avoiding retained failure history and the shared execution repository lock during Team wake planning.
- Preserve pending-completion status, session/VM/target evidence, FIFO admission and prompt-lock ownership rules for legacy and encoded payloads.

## 2026-10-16 - Validate queued prompt recovery with scoped bookkeeping (0.98.1)

- Check that queued ordinary and Chat prompts preserve durable reservations and concurrent edits when scoped bookkeeping reads current rows without unnecessary snapshot retries.

## 2026-10-16 - Generic SQL Server and RabbitMQ integrations (0.98.0)

- Add independently installed SQL Server and RabbitMQ projects with directory-scoped registered connections, bounded data/management tools, operator pages, workflow providers and disposable resource profiles.
- Add provider-neutral resource authority, durable intents and lifecycle notices, optional Team ownership validation, exact worker-job private tmpfs configuration and independent expiry/fault recovery. Withdraw obsolete job access before provider calls, preserve scoped grants across worker policy reloads and retain independent cleanup retries. Serialize worker operations per lease so unrelated calls cannot hold up recovery, preserving fault deadlines until recovery succeeds. Persistent resources have no automatic deletion; uncertain data writes are not replayed.
- Add Test Fixtures 0.1.4 migration previews and drain mode, preserving existing leases and cleanup until provider absence is verified. Production server lifecycle and deployment remain operator-owned.
- Isolate trusted worker helpers from job executables and Python configuration, select network grants using the job's destination route, and keep managed SQL creation identities under host control.

## 2026-10-16 - Deliver published workflow files to Teams (0.97.0)

- Let host workflow messages send directory-owned published archives and selected report or image files directly through the existing Teams file-delivery path, without a delivery agent or model call.
- Embed screenshots and send expiring Evander downloads for other files, preserving declared target capabilities and existing agent file-send policy.
- Verify publication ownership, archive checksums, regular-file selection and file limits before staging; persist directory ownership with new publications.
- Apply directory policy to delivered filenames, bound sparse-file reads, and retain workflow-directory signing for download verification.

## 2026-10-16 - Restore WAL and bound Team lifecycle recovery (0.96.7)

- Restore WAL during runtime initialization of current-schema databases, including closed single-file history conversions, without replaying data migrations or changing steady-state connection behavior.
- Prepare Team activation, completion, correction and wake changes from revision-pinned working records, validate affected relationships, and atomically commit deltas with durable activity and outbox evidence.
- Page historical recovery IDs before hydration, read prompt lifecycle receipts without unrelated inputs and configuration, and add supporting indexes through an index-only Team upgrade.
- Report Team storage, correction and worker-preparation phases separately, with regression coverage for actionable completion work concurrent with foreground delivery.

## 2026-10-16 - Continue command-blocked releases with reviewed source corrections (0.96.6)

- Allow an accepted, reviewed Git correction of the exact command-blocked candidate to create a successor in the same release lineage, including tasks pinned through the candidate's accepted producer branch.
- Preserve failed verifier evidence, immutable correction aggregation, normal reviews and verification; reject predecessor gate retries once a successor exists, and resolve superseded ancestor blockers only after the successor is published successfully.

## 2026-10-16 - Clone private Team release candidates by pinned ref (0.96.5)

- Prepare disposable release verifiers through the existing pinned-ref clone path, resolving logical Team branches to their managed private refs and checking the exact frozen candidate commit before verification commands can run.
- Preserve read-only Git access, workspace cleanup, infrastructure failure classification and existing release review and approval gates.

## 2026-10-16 - Make Team escalations easier to read (0.96.4)

- Guide Team managers and governance agents to write short, plain-language escalations with Need, Why and Next lines, an explicit operator action and a clear owner for follow-up.
- Aim for fewer than 120 words, retain technical evidence in Team records, and give concise recommendations and reply choices when a decision is needed.
- Send a short summary and Team record reference for automatic blocker escalations, retaining the full technical details and history in Team.
- Refresh managed instructions for existing sessions on their next eligible wake; preserve routing and delivery receipts.

## 2026-10-16 - Keep completion recovery indexed after history conversion (0.96.3)

- Read pending gate and effect completions through existing status indexes with ordered continuation pages, keeping recovery work independent of execution-history size even after optimizer analysis.
- Analyze only rebuilt history tables during offline conversion, preserving unrelated optimizer statistics and leaving statistics unchanged for copies that retain their storage format.

## 2026-10-16 - Preserve Team Git checkout identifiers and fixture reconciliation (0.96.2)

- Persist canonical Team checkout IDs across database reloads and expose them in compact wake prompts, task reads and dispatch reads so agents can select the intended Git submission checkout.
- Preserve registered IDs during preparation replay and repair legacy missing IDs on the next normal preparation without changing retained worktree provenance or authored work.
- Publish Test Fixtures add-on 0.1.3 with private writable temporary directories for its worker and reaper services, allowing SQLite to sort larger worker-job histories under strict filesystem protection.

## 2026-10-16 - Retain private security scan diagnostics (0.96.1)

- Capture brokered parent and child events, native stderr, token usage, terminal errors and credential-free HTTP status/request metadata in worker-owned storage outside session artifacts.
- Preserve bounded native history and execution outcomes before scanner cleanup; retain evidence through session deletion and worker restart, with age and size limits.
- Drain native broker writers before sealing diagnostics; defer finalization after a bounded shutdown timeout so late terminal stderr and exit records survive stop and removal.
- Publish only canonical reports and scan receipts, excluding raw diagnostics and logs from archives delivered by workflows. Diagnostic capture failures do not block scans or report delivery.

## 2026-10-15 - Resolve environment website logins in workflows (0.96.0)

- Add `query:website-session:resolve-profile` to select one configured JSON login by exact browser and login API origins within the workflow directory scope. Return only readiness, profile ID and safe errors, without authenticating or decrypting credentials.
- Expose the query and its output contract in the workflow editor so workflows can validate access before binding the selected profile and dispatching an agent. Existing exact-origin profiles and encrypted credential bindings remain unchanged.
- Allow writes to any concrete ordinary Elasticsearch index by default. Explicit index patterns and empty read-only policies remain supported, with existing request and document limits.

## 2026-10-15 - Apply RabbitMQ fixture vhost limits (0.95.3)

- Publish Test Fixtures add-on 0.1.2 with the supported `--vhost` argument for RabbitMQ limits, allowing healthy fixture brokers to finish provisioning.
- Add an opt-in native broker regression covering queue limits, AMQP traffic, pause/stop recovery, reset and repeated cleanup with a pre-seeded image.

## 2026-10-15 - Recognize absent Docker fixture networks (0.95.2)

- Publish Test Fixtures add-on 0.1.1 with support for Docker's `network <name> not found` inspection response, allowing first allocation and cleanup of partially provisioned bundles.
- Preserve daemon health checks and reject unrelated inspection errors.

## 2026-10-15 - Reduce scanner image build disk usage (0.95.1)

- Support streamed gzip scanner image archives and release build staging before export, while preserving verified cache reuse and the previous archive on failure.
- Install runtime dependencies directly in temporary staging and set permissions before image creation to avoid duplicate runtime files and layers.

## 2026-10-15 - Disposable task test fixtures (0.95.0)

- Add the separately installed Test Fixtures add-on for task-owned SQL Server
  source/target databases and isolated RabbitMQ brokers, private job configuration,
  guarded MCP operations, durable quotas, bounded faults and outage cleanup.
- Expose a narrow Team task resource authority that checks current ownership,
  exact live dispatch scope and project membership, including blocked recovery.
- Persist SQL resource intent before provisioning so partial failures remain
  retryable and cleanable. Existing session database interfaces remain supported.
- Include opt-in worker deployment assets, an operator lease view and a disabled
  allocation configuration. Remote deployment and pilot activation are separate.
- Deliver private configuration without container capabilities and resume queued
  resets through task continuations after their requesting jobs exit.
- Serialize fixture admission with policy changes and bind operator updates to
  the directory whose settings and revision were loaded.

## 2026-10-15 - Cache scanner images for deployment (0.94.1)

- Add an optional Codex Security worker-image builder for deployment pipelines, keyed by the prepared base image archive, pinned runtime and operator build inputs.
- Reuse checksum-verified scanner archives on unchanged deployments and preserve the previous cache when a rebuild fails.

## 2026-10-15 - Submit committed work through MCP (0.94.0)

- Add the session-scoped `submit_git_changes` capability for VM and container agents, including verbatim complete Markdown summaries, opaque checkout selection, exact-SHA verification, and safe retries.
- Preserve managed Git write grants and publication policy; publish commit and notes atomically and coalesce their workflow notifications.
- Make MCP submission the default in coding workflows and Team guidance while retaining the Git helper commands for compatibility.

## 2026-10-15 - Accept sealed committed security diff coverage (0.93.1)

- Accept the native scanner's `branch_diff` and `commit` coverage modes alongside
  generic `diff` coverage for committed security scans. Continue validating the
  sealed Git target and exact requested base and head revisions, and reject
  repository or working-tree coverage for a committed range.

## 2026-10-15 - Query Codex subscription quota in workflows (0.93.0)

- Add `query:codex:subscription-quota` to read a named profile's remaining quota,
  usage percentage, reset time, and observation time through its directory's worker.
- Default to the seven-day Codex window, with explicit bucket and duration selectors
  for other limits. Return unavailable data for missing, malformed, expired, or
  unreachable telemetry instead of inventing remaining capacity.
- Keep worker credentials and account metadata out of workflow outputs, enforce
  workflow directory scope, and leave queued subscription work untouched by quota reads.

## 2026-10-15 - Keep subscription scans running through child audits (0.92.2)

- Wait for the requested Codex thread to finish instead of ending a brokered
  scan when an independent child audit completes. Keep child messages and token
  usage out of the parent execution stream.
- Bind job-private runtime and model-relay sockets through their parent directory
  descriptors so long session and job paths do not prevent subscription scans.

## 2026-10-15 - Reuse deployment artifacts and report timings (0.92.1)

- Make Codex and Claude runner archive fingerprints portable between build agents,
  verify archive checksums before reuse, and expose fingerprints for pipeline caches.
- Add optional Ansible task and role timing reports without recording task results
  or changing validation, service restarts, or runtime migration behavior.
- Remove the duplicate Ansible systemd preflight; the installer retains its own
  preflight before changing units.

## 2026-10-15 - Repository security scan workflows (0.92.0)

- Add a provider-neutral repository security scan effect with required session
  and repository path, optional commit bounds, durable waiting, and report outputs.
- Add an independently installed Codex Security provider for standard and deep
  scans against clean snapshots, including worker-brokered subscription runtimes.
- Preserve scan findings and private report artifacts across workflow restarts,
  and keep scan failures distinct from completed scans that find vulnerabilities.
- Keep real subscription credentials on the worker, attach them through a
  job-scoped relay with account-pinned routing, and restrict the scan runtime
  protocol. Make cancellation terminal across startup and cleanup.

## 2026-10-15 - Explain workflow graphs with the assistant (0.91.0)

- Add **Explain graph** to the workflow editor's graph details on desktop and mobile.
  Open the built-in assistant and request an explanation of the selected graph's
  current nodes, routes, bindings, and effects, including unsaved node changes.
- Redact credentials before handing graph context to the assistant while retaining
  workflow prompts, message bodies, and parameter mappings needed for explanations.
- Retain the assistant conversation and pending graph explanation when the initial
  transcript request fails, and recover through the existing chat stream.
- Allow graph selection and explanation with workflow read access, retain existing
  chat history and message drafts, and show when the assistant is disabled.

## 2026-10-14 - Repair modular-release test isolation (0.90.2)

- Keep Azure DevOps query result-shape coverage with the extracted add-on and
  retain provider-independent query coverage in core.
- Isolate tests from a parent service's systemd invocation so retry tests exercise
  their temporary runtime, while explicit service-generation guard tests remain active.
- Load optional add-on source for their own test suites and subprocesses without
  requiring package-path overrides for the complete test runner.
- Keep ADO comment and publication tests with their integration, align Git-command
  checks with private refs, and test chat ownership through one shared application.
- Isolate simulated polling clocks and wait for observed asynchronous outcomes
  instead of assuming a fixed number of cycles or a short scheduling delay.
- Keep navigation and tutorial checks unambiguous when workflow starters are available.

## 2026-10-14 - Honor disabled Azure DevOps feedback integration (0.90.1)

- Check the resolved directory's Azure DevOps enablement before the legacy pull-request
  feedback query reads credentials or makes requests. Preserve GitHub feedback behavior.

## 0.90.0

- Extract Azure DevOps work items, pull requests, builds, personal connections and workers into the separately installed `packages/azure-devops-addon` project. Preserve legacy workflow IDs, configuration and durable state; core startup no longer needs the integration.
- Add an editable single-issue delivery starter with a managed repository picker, exact-commit CI and review checks, bounded repair attempts, existing approval-queue integration and verified merge completion.
- Preserve graph metadata when previewing/installing stateful starters and support completion evidence from later workflow events.
- Keep GitHub issue and comment normalization independent of Azure DevOps. Install the optional Azure DevOps package for selected Ansible services and skip absent disabled units during service reconciliation.
- Keep delivery stages within the default worker execution depth and validate configured limits before activation. Preserve unchanged starter verification and manual tests when other workflows are installed, while retaining revision checks for edits.

## 2026-10-14 - Guided workflow starters (0.89.0)

- Add an outcome-based starter catalog, short setup forms, dependency checks,
  workflow preview, explicit activation, and first-run verification in Admin Web.
  Link the starter experience from the operations dashboard.
- Include a provider-independent scheduled agent task and a Teams direct-message
  assistant contributed by the separately installed Teams/Graph add-on 0.2.
- Generate ordinary editable workflow packs with scoped agents, existing permissions,
  revision checks, cursor seeding, and change history. Preserve customizations and
  make activation retries idempotent; upgrades never overwrite generated workflows.
- Distinguish queued tasks from completion and require correlated successful reply
  evidence for Teams verification. Keep the existing editor and arbitrary workflows.
- Follow persisted continuation links when checking completion or downstream failures.
  Include integration-owned workers in restart guidance and exit Teams workers cleanly
  when the add-on is disabled.

## 2026-10-14 - Edit workflows in a lower workbench (0.89.0)

- Give the workflow canvas a full-width lower editor with a remembered, resizable
  split, expanded editing, and Configuration, Outputs, and History tabs.
- Move graph selection and the node catalog into toolbar popovers, and keep runs
  and validation checks available in the workbench.
- Keep tutorial node actions reachable from the toolbar and move tutorial hints
  beside their target when the shorter canvas leaves no room above or below it.
- Edit map outputs through typed rows and upstream binding selectors, retaining
  raw JSON, nested values, existing layouts, and revision-checked workflow saves.

## 2026-10-14 - Compact execution-history scalar storage (0.88.1)

- Add explicit conversion to a separate verified database with shared execution,
  generation and field-path dictionaries, retaining every scalar value and row ID.
  Existing databases keep their original format until an operator converts them.
- Support transactional indexing, direct integer-ID field queries, bounded retention
  and dictionary collection in the compact format. Restrict field-catalog aggregation
  to the requested execution window before reading scalar values.
- Provide a separate legacy-format output for rollback and reject payload downgrade
  until the scalar index has been converted back.
- Reuse bounded read-only history-report connections to avoid repeated schema
  parsing, releasing cursors and snapshots between reports and renewing deadlines.

## 2026-10-14 - Optional Teams and Graph integration (0.88.0)

- Extract Teams/Graph network clients, authentication, workers, admin and MCP handlers,
  and deployment assets into the independently installed `evander-teams-graph-addon` project.
  Register settings, credentials, readiness, and worker factories through the integration contract.
- Preserve existing workflow and tool IDs, directory settings, credential references, stored
  messages, meeting state, legacy imports, and CLI commands. Core remains usable without the
  package; dependent operations report that the integration is unavailable.
- Install Teams services only when the integration is available and enabled. Keep Ansible's
  existing selected-service behavior and move provider tests into the optional project.
- Keep service preflight read-only on uninitialized runtimes and include the gateway in
  restart guidance when enabling or disabling Teams/Graph.

## 2026-10-14 - Show active agent run settings (0.88.0)

- Show the model, speed, and reasoning settings of active container and VM
  agent runs in Admin Web, preserving per-prompt overrides and identifying
  unavailable values without substituting session defaults.
- Refresh the displayed settings when the selected agent's execution changes.
- Read settings from the active execution records after worker reconciliation,
  excluding runs that have completed and retaining refreshed settings.

## 2026-10-14 - Anchor workflow graph labels beside their first node (0.87.1)

- Place each graph label just left of the uppermost node in its leftmost column,
  keeping labels beside the entry node as other branches grow or move.

## 2026-10-14 - Guide website login setup (0.87.0)

- Add step-by-step website login setup with WordPress and custom JSON API paths,
  a review before saving, and explicit connection checks after saving.
- Reuse configured WordPress connections without asking for their credentials or
  response mappings. Explain missing prerequisites and distinguish configuration
  checks from authenticated browser access.
- Keep the advanced editor available, preserve drafts after failed saves, and
  support token, JSON-object and cookie response mappings in guided setup.
- Allow returning from the advanced editor to the WordPress wizard without
  losing the current step, selected connection, or edited settings.

## 2026-10-14 - Optional integration extension contracts (0.87.0)

- Add opt-in, versioned add-on contracts for settings schemas, host credential
  providers, readiness checks, background worker factories, and editable workflow
  starters. Preserve existing add-on registrations and workflow loading.
- Expose an owner-only integration inventory and explicit directory-scoped
  readiness checks. Report unavailable dependencies without executing callbacks;
  keep provider error details and credential material out of readiness responses.
- Keep worker supervision, integration extraction, configuration writes and
  starter installation outside this foundational stage.

## 2026-10-12 - Isolate private Git refs from upstream branches (0.86.8)

- Store session, review, and Team refs under `refs/evander/` so upstream
  `agents/`, `review/`, and `team/` branches no longer block repository refresh.
- Migrate existing private refs atomically at their exact commits, reject
  conflicting destinations, and retain pinned pre-migration receipts. Add an
  operator preview/apply command and preserve exact-repository, session-scoped
  SSH write restrictions across the transport and workflow paths.
- Preserve upstream branch identity when preparing reference repositories,
  including default and explicitly selected branches with matching private names.

## 2026-10-10 - Share internal platform contracts (0.86.7)

- Refactor ten duplicated concerns: log tail pagination, Admin Web JSON response
  conversion and request decoding, gate state paths, literal environment parsing,
  Azure DevOps repository identifiers, workflow node materialization, provider
  reload signatures, voice startup objectives, and ADO query list validation.
- Preserve existing payloads, defaults, validation order, exception chaining,
  persistence paths, reload behavior, and side effects. Add characterization
  coverage checked against the unchanged implementation before refactoring.

## 2026-10-09 - Reduce worker, wake, lookup and workflow validation costs (0.86.6)

- Fix ten additional performance issues in worker telemetry, restart recovery,
  subscription profile counts, wake dispatch selection and ledger paging, Teams
  message lookup, model-usage context lookup, and workflow binding/schema validation.
- Aggregate status fields, bound payload reads, batch scoped dispatch selection,
  add lookup/order indexes, and share graph indexes with topological dominator
  evaluation. Preserve payloads, ordering, retry and capacity deferral behavior,
  corrupt-record fallback, and diagnostics for invalid draft graphs.
- Add deterministic work-bound regressions and a reproducible synthetic benchmark;
  see `docs/performance-hotpaths.md` for findings, measurements and validation.

## 2026-10-09 - Consolidate shared control-plane logic (0.86.5)

- Share session selection, loop graph access, Azure DevOps polling requests,
  matching-trigger markers, and provider result projection.
- Reuse workflow report traversal and relay path validation; consolidate worker
  logging, optional worker loading, and prompt capacity reconciliation.
- Preserve existing APIs, payloads, validation order, cursor behavior, and side
  effects. Add characterization coverage for session precedence, result shapes,
  report ordering, worker failures, and inference capacity retention.

## 2026-10-09 - Simplify internal service and provider code (0.86.4)

- Share configuration merge/revision helpers and workflow activity collection;
  consolidate steering validation and dispatch.
- Consolidate pull-request query input handling, model-usage totals, and Azure
  DevOps build collection.
- Share Git effect result conversion and VM power handling, and separate VM
  execution result validation and email HTTP request construction from dispatch.
- Preserve existing payloads, validation and error ordering, permissions,
  pagination, and side effects; add focused behavior characterization coverage.

## 2026-10-08 - Reduce repeated platform read work (0.86.3)

- Fix ten additional performance issues in Discord inbox paging and unread checks,
  cost-report identity lookups, Team memory permissions, wake assessments and attention
  projections, workflow graph metadata, repository-index navigation and enqueue lookups,
  and approval audit review.
- Preserve scoped visibility, cursor updates, payload ordering and recovery behavior.
  Upgrade repository-index databases to schema 3 with indexed snapshot reuse lookups.
  See `docs/performance-hotpaths.md` for measured work reductions and verification.

## 2026-10-08 - Consolidate internal control-plane helpers (0.86.2)

- Share artifact read selection and audit-line decoding, and separate active-session
  discovery from skill propagation.
- Consolidate local-ticket trigger polling, strict timestamp parsing, and Teams
  meeting cursor normalization.
- Extract shared HTTP address checks, VM copy permission handling, and Azure DevOps
  pipeline template-parameter resolution.
- Share streaming and non-streaming relay usage metadata construction while preserving
  payloads, request correlation, validation, and side-effect ordering.
- Update Team template and Teams routing test setup to match the current service dependencies.

## 2026-10-08 - Bound platform read and orchestration work (0.86.1)

- Fix ten performance issues across cost attribution updates, Teams notification
  status, semantic code hits, Team pagination, reporting visibility, supervisory
  summaries, workflow depth and merge context, scheduled publication, and internal
  event retries. Preserve scoped visibility, ordering, counts, and durable delivery.
- Bound expensive payload reads and page projections; replace repeated graph and
  collection walks with indexed or single-pass algorithms. Add regression checks
  for work bounds and behavior; see `docs/performance-hotpaths.md` for the findings.

## 2026-10-08 - Delete comments and resolve PR threads (0.86.0)

- Add session-scoped tools to delete individual Azure DevOps work-item comments and
  Azure DevOps or GitHub PR comments.
- Add PR comment resolution for Azure DevOps threads and GitHub review threads,
  including lookup by GitHub review comment ID.
- Use existing directory credentials, explicit tool grants, configured approval and
  argument policies, and audit records; verify GitHub PR ownership before mutations.

## 2026-10-08 - Direct PR description attachments (0.85.0)

- Add staged `artifact_ids` to `update_pull_request` so agents can upload files
  directly to an Azure DevOps PR and include their image/download links in its description.
- Append links to the current description when no replacement text is supplied,
  and return uploaded attachment metadata and URLs.
- Refresh the current description after uploads to preserve edits made while
  files were uploading; stop the save if that refresh fails.
- Apply existing file ownership, outbound file policy, exact file-send approval,
  and tool/PR restrictions; check expected PR status before uploading files.

## 2026-10-07 - OpenAI Decisions classification (0.84.0)

- Add an opt-in host-side OpenAI Decisions provider using the published `/v1/decisions`
  contract and `gpt-6-luna`, shared across workflow queries, Smart switches, agent tools,
  and Admin Web with host-owned credentials and directory-scoped configuration.
- Preserve native predicate probabilities, choice distributions, and fractional rubric
  scores. Reject refusals and malformed answers before downstream workflow actions.
- Bound HTTP requests, responses, retries, and total deadlines; check privacy before each
  attempt and retain native OpenAI usage attribution without input or answer content.
- Scope model pricing rules by API endpoint and include Decisions input-only pricing while
  retaining endpoint qualifiers in Admin Web edits.
- Keep Admin Web sample evaluations aligned with saved profile defaults, preserving the
  difference between an omitted model and explicit provider-default selection.
- Add real-adapter HTTP conformance and shared-path tests plus an explicit provider smoke.

## 2026-10-07 - Codex Ultrafast mode (0.83.0)

- Add Ultrafast to session defaults and prompt overrides in Admin Web, workflow
  nodes, and CLI speed selection while preserving existing boolean Fast settings.
- Pass the selected tier through VM, container, and subscription runs, including
  queues, wakes, retries, and steering. Explicit Standard restores the default tier.
- Advertise worker Ultrafast support and require it during worker deployment
  verification; account and model eligibility remain provider-controlled.
- Seed GPT-6 Astra Ultrafast pricing, including cache and long-context rates,
  and document how to update existing operator catalogs for accurate estimates.

## 2026-10-05 - Central event schemas and manual event forms (0.82.0)

- Define event payload schemas in directory configuration and Admin Web, with
  inherited definitions and local overrides.
- Generate structured manual-trigger forms from registered schemas, validate
  submissions, and keep JSON entry available for events without definitions.
- Add `gate:event:validate` to route valid and invalid event payloads through
  workflows without changing automatic event publication.
- Include payload schemas for core and bundled Team add-on events.
- Allow schema-valid empty strings in manual forms, preserve reference sibling
  constraints through JSON entry, and check references in nested resource scopes
  before saving event definitions.

## 2026-10-05 - Restore workflow catalog card padding (0.81.1)

- Add horizontal padding inside the Triggers, Gates, Effects, and Queries catalog
  headers so their labels and node counts sit clear of the card borders.

## 2026-10-04 - Route phone callers to agent sessions (0.81.0)

- Add root caller maps keyed by the called DID and caller phone number, with an Admin Web
  editor that selects any active agent session and retains unavailable destinations for repair.
- Reuse inbound route greetings and caller access policies, reject unmapped callers, and keep
  existing fixed routing for numbers without a caller map. Apply map edits to new calls without
  restart while preserving the origin of admitted calls.
- Clear cached inbound routing grants before applying live configuration so removing an
  override cannot leave its startup route active.
- Keep root configuration and valid phone route choices available when another directory has
  invalid provider settings. Require controller authentication before exposing caller-map or
  routing errors and honor live root controller-token changes, while retaining destination-scoped
  credentials for mapped and fixed calls.

## 2026-10-02 - Bound execution-history payload cleanup (0.80.4)

- Scan execution-history payloads in bounded key ranges with a durable cursor,
  so sparse or empty orphan sets finish without repeatedly scanning retained blobs.
- Commit reference checks, deletions and scan progress in the same writer transaction.
  Fix each sweep's upper boundary; collect newly orphaned blobs behind the cursor
  or beyond that boundary in the next daily retention sweep. Existing tables and
  indexes suffice; no offline storage migration is required.

## 2026-10-02 - Align Admin Web spacing and navigation (0.80.3)

- Align dashboard metric labels, toolbar actions, navigation icons, and quick
  navigation results with consistent padding and vertical centering.
- Correct form spacing and responsive alignment across Privacy, Classification,
  managed Git, Subscriptions, and bundled add-on interfaces.

## 2026-10-02 - Fix the Approvals All filter (0.80.2)

- Include exhausted approval grants in queue sorting so selecting All, or filtering
  for exhausted requests through the API, returns the queue without a server error.

## 2026-10-02 - Give artifact-space selection room to browse (0.80.1)

- Open the artifact-space tree in a wider, scrollable dialog from a compact sidebar
  selector. Keep the selected space visible, apply recursive filtering on selection,
  and preserve the current filter when the dialog is dismissed.
- Keep keyboard focus inside the picker and return it to the selector on close;
  support Escape, backdrop dismissal, and narrow screens in light and dark themes.

## 2026-10-02 - Camera and screen context in Teams calls (0.80.0)

- Receive caller camera video and screen sharing with optional automatic snapshots per spoken
  turn. Bind delegated visual queries to the same images, deliver them through a session-scoped
  MCP image tool, and bound temporary storage with quotas, completion cleanup, and expiry.
- Add inherited operator configuration and audio fallback when visual capabilities are
  unavailable. Visual context starts disabled; no outgoing video or media recordings are created.
- Honor removal of the live root visual opt-in for new calls without restarting the gateway.

## 2026-10-02 - Hide the Subscriptions worker URL (0.79.2)

- Remove the internal worker URL from the Subscriptions page header while keeping
  loading, error, and missing-worker messages.

## 2026-10-02 - Keep retention progressing after indexing deadlines (0.79.1)

- Select expired workflow, internal and scheduled events in timestamp-index order,
  avoiding backlog scans and sorts when cleanup batches shrink to one record.
- Persist history field-index batch backoff after rolled-back execution timeouts
  and recover fully used fast batches, preserving concurrent reductions and atomic
  publication of complete searchable generations.
- Continue independent maintenance phases after execution deadlines while still
  preserving the one-second retry pause and yielding immediately to occupied
  writer or repository locks. Existing indexes and retention policies are
  unchanged; no offline index build is required.

## 2026-10-01 - Native Teams assistant calls (0.79.0)

- Route incoming personal Teams audio calls through an explicit root tenant/user map,
  independently authorize the selected existing assistant, and preserve admitted bindings
  across configuration changes. Add revision-checked Admin Web route editing with assistant names.
- Extend the Windows media bridge to answer calls and delegate realtime function queries
  into the original assistant context, with interruption, idempotent queries, durable capacity
  reservations, terminal reconciliation, and bridge-loss recovery.
- Validate communications notification signing keys, audience, and tenant; update the Graph
  media SDK. Include local tests, app and environment templates, Windows service installation,
  and an activation runbook. Calling remains disabled by default.
- Preserve valid Realtime session configuration for existing meetings and serialize native
  caller turns with delegated replies, waiting for cancelled responses to finish before restarting.

## 2026-10-02 - Enforce WordPress resource permissions (0.78.2)

- Release WordPress Add-on 0.1.3 and companion 1.0.2 with object, operation,
  metadata and taxonomy authorization for inspection, publication and browser edits.
- Reject ACF aliases of protected metadata and mismatched field references while
  preserving conditional restoration after editing permissions are revoked.
- Core runtime behavior is unchanged; the add-on remains separately installed and enabled.

## 2026-10-01 - Bound retention candidate searches with dedicated indexes (0.78.1)

- Select the oldest eligible sources through five limited partial-index ranges,
  avoiding backlog scans and sorts even when retention batches shrink to one row.
- Calculate payload byte budgets through reachable blob lookups, avoiding a
  scan of every stored blob for each selected source.
- Defer source cleanup until the required index definitions are ready; preserve
  eligibility rules, payload byte limits, and atomic history-before-source deletion.
- Add an offline indexes-only maintenance mode that builds and validates deferred
  indexes without converting payloads, with safe reruns after an interrupted build.

## 2026-10-01 - Simplify operator workspaces (0.78.0)

- Prioritize dashboard attention and capacity over release news, flatten service
  health details, and open a failed service directly from its attention shortcut.
- Separate session monitoring, model/access settings, and wake schedules, and
  worker status/logs from settings while retaining unsaved drafts and revision checks.
- Keep common agent actions visible with text labels and group secondary actions
  into a named menu. Flatten approval review and VM setup panels, and disclose
  secondary filters, audit history, and technical metadata.
- Open records directly from audit, approval, artifact, and Team attention links;
  resolve artifact keys with Enter and simplify archive and report inventories.
- Search workflow node types directly on desktop and mobile, flatten node families,
  prioritize current runs, save from the compact toolbar, and show selected pack
  history changes inline.
- Remove duplicate add-on and user inventories, label configuration/policy save
  actions, search users and pricing rules, and prioritize repository and subscription
  actions. Preserve privilege gates, approval requirements, and reset confirmations.
- Show Miro operation details inline and disclose supporting Bokio evidence and
  history while retaining visible approval payloads and warnings.

## 2026-10-01 - Reduce control-plane polling and query payloads (0.77.0)

- Select scoped Team deliverable identities before constructing event payloads,
  skip full state hydration for idle and initial-baseline polls, and share task
  assessment across pending events from the same SQLite read snapshot.
- Add opt-in `record_fields` to `query:team:state` for compact task and case
  results. Read only required state collections, preserve scope and assessment
  dependencies, support nested workflow bindings with strict resolved-selector
  validation, and retain the existing full response when selection is omitted.
- Reuse one directory-bound HTTP client throughout ADO comment polling and
  obtain complete work-item fields and relations through the existing batch,
  removing individual detail requests while preserving comment cursors and retries.
- Keep fast recovery passes without work at DEBUG, retain slow and active passes
  at INFO, and avoid provider completion wrappers for inherited empty callbacks
  while preserving custom lifecycle hooks.

## 2026-10-01 - Recover retention batch limits after successful cleanup (0.76.5)

- Gradually restore persisted record and index-row batch limits after fully used
  batches finish with time to spare, preventing retention from remaining at one
  row per batch after earlier deadlines.
- Commit recovery with cleanup, retain existing batch caps and deadline backoff,
  and preserve concurrent reductions without growing empty or deferred phases.

## 2026-10-01 - Reload worker code during deployment (0.76.4)

- Restart container worker daemons when their running revision differs from the deployed
  checkout, including retries after an interrupted deployment.
- Verify the running revision and Codex Fast mode support before accepting a worker
  deployment, preventing stale request schemas from rejecting new turn fields.

## 2026-10-01 - Preserve untracked files during reference preparation (0.76.3)

- Allow persistent reference repositories to be reused or refreshed with unrelated
  untracked files, including generated database sidecars, while retaining tracked
  and staged change checks and trusted commit validation.
- Reject checkout updates that would overwrite untracked or ignored local files,
  preserving the existing checkout and trusted state on a path conflict.
- Reject staged additions in prepared reference checkouts with an unborn HEAD,
  preserving the local branch, staged files, and trusted state.

## 2026-10-01 - Clarify control-plane transaction stages (0.76.2)

- Apply Team agent action batches through named stages with invocation-local
  progress and project-recalculation state, preserving stage order, partial-error
  handling, transactional events, and dispatch completion.
- Split ordinary dispatch leasing into eligibility, work selection, wake timing,
  lineage selection, reservation, and payload phases while retaining role
  allocation recovery, manager fallback order, and revision-conflict retries.
- Build release packages through explicit lineage, provenance, input, destination,
  persistence, and promotion stages shared by agent and operator requests, keeping
  duplicate reuse and correction failure cleanup in their established order.
- Separate task-update validation, edits, preparation invalidation, and response
  projection, removing unreachable direct blocker-transition handling while
  preserving blocker reassignment events and command response fields.
- Apply administrative task upserts through a typed request with explicit edit
  flags and separate existing-task and new-task stages, preserving graph policy,
  inherited provenance, immutable Git base checks, and transactional wake events.
- Dispatch wake reservations through separate repository, capability, artifact,
  prompt, delivery, and recovery stages with fresh state for each lease, retaining
  queue activation rules and durable failure and conflict retries.
- Project scoped Team state through ordered membership, work, delivery, dispatch,
  assessment, and reference-closure stages, removing a superseded task projection
  while retaining scope intersections and globally derived task assessments.
- Consolidate agent role updates so explicit role clearing uses a defined value:
  teamless agents may clear it, while team members receive the existing validation
  error with their attempted edits rolled back.
- Prepare task Git contexts through named base-selection, worktree, receipt,
  provenance, and commit stages, making recovery strategy priority explicit while
  retaining cleanup locks, durable receipts, and state-conflict retry metrics.
- Collect case-history evidence through ordered scope, source, note, artifact,
  review, conversation, and temporal-annotation phases, preserving record and
  warning order and removing an unused review-ID accumulator.
- Apply administrative task edits through a typed request and ordered graph,
  contract, lifecycle, preparation, blocker-repair, and wake stages, preserving
  explicit clears and revision-checked transactional events.
- Separate Teams credential migration inspection, import, reference switching,
  and source retirement, retaining one maintenance lock and the encrypted backup
  boundary so interrupted migrations reuse durable imports safely.
- Apply administrative agent edits through a typed request and named team,
  project, role, session, context-policy, and reporting stages, retaining explicit
  clear rules, membership validation, the patchable clock, and wake-event outbox.
- Build workflow failure reports through explicit metadata, activity, clustering,
  classification, and pagination phases, preserving one SQLite read transaction,
  state priority, newest-first examples, and the operational-payload loader seam.
- Create libvirt VMs through named session, policy, setup, and recovery stages.
  Check setup exit status for both privileged-helper and subprocess execution so
  helper failures report failure and retain only recoverable VM or session state.
- Build workflow run searches through separate event selection, activity grouping,
  session enrichment, filtering, and pagination stages, retaining SQLite counts,
  bounded record hydration, shared-trace sessions, and the patchable report clock.
- Replay queued prompts through explicit batch and VM delivery stages, retaining
  container-first dispatch, maintenance budgets, claim order, and queue replacement
  guards. Keep delivery claims and pending steering when a waiting receipt fails.
- Plan manager fallback through separate team eligibility, lineage selection,
  authority, scope, reservation, and runtime stages. Replace positional work tuples
  with named fields while retaining full work fingerprints and wake suppression.
- Execute release verification through explicit run recovery, workspace, command,
  evidence, persistence, and cleanup stages. Keep generation checks, single-flight
  execution, infrastructure precedence, and cleanup of partial setup in their order.
- Separate release-state validation into ordered package checks for ownership,
  cohorts, inputs, verification, reviews, and frozen evidence. Report missing runs
  and reviews as validation errors and continue collecting later diagnostics.

- Refactor the standalone Team UI into feature controllers with local state,
  rendering, and action handlers. Keep the entry point focused on composition,
  preserve delegated event order and mutation guards, and remove unused legacy
  renderers. Verify module publication and exercise the actual controllers.
- Extract Team copying and template materialization into typed requests and
  ordered application stages. Keep revision checks and managed repository binding
  locks in the admin facade, retaining configuration selection, ID allocation,
  reporting links, cost budgets, and atomic graph creation.

## 2026-10-01 - Reduce control-plane polling and bookkeeping cost (0.76.1)

- Reuse revision-aware compiled workflow configuration in polling consumers and skip
  expensive Teams notification preparation when no queue item is ready, while retaining
  bounded periodic cleanup of expired terminal notifications.
- Batch empty trigger cursor telemetry while preserving immediate published-event
  checkpoints and storage-busy retry classification, and copy only the retained event ID tail.
- Update existing Team dispatch receipts and runtime bookkeeping through bounded,
  revision-checked transactions without reconstructing unrelated historical state;
  preserve accepted queued prompts when unrelated revision changes exhaust snapshot retries.
- Skip completed history-backfill queue probes, use indexed pending/inflight checks,
  and space completed garbage-collection sweeps while history and retention progress.
  Persist each collector's completion independently and finish deletion follow-up sweeps
  before the idle pause.
- Default Teams fallback polling to four concurrent targets and defer background
  timeout retries to durable per-target backoff, preserving interactive retry behavior.

## 2026-09-30 - Independent add-on projects and website login adapters (0.76.0)

- Establish self-contained, independently installed add-on projects under `packages/`.
  Include their tests in repository collection and document ownership for future add-ons.
  [WordPress 0.1.0](packages/wordpress-addon/CHANGELOG.md) is the first project;
  installing core alone does not install it or grant the relay credential-key access.
- Add generic website-session adapter registration, adapter-managed profile editing
  and adapter-aware restart metadata, with ordinary JSON profiles remaining the default.
- Supply trusted connector drivers with host services and callbacks that recheck current
  session, tool, argument and connector policy before durable host operations.
- Provide host-only connector authentication snapshots and reject reflected raw or encoded
  managed credentials before returning protocol responses or persisting results.
- Support optional temporary cookies in generic JSON logins while retaining existing
  JSON-login storage limits and positional add-on registration compatibility.
- Reject encoded host credentials in JSON-login cookies and storage through the
  shared credential sanitizer, preserving explicitly public storage identifiers.
  Check composite Basic/JWT values without buffering complete decoded artifacts.
- Discover website adapters only when needed so unrelated add-on imports cannot interrupt
  ordinary logins. Align frozen installation metadata with this release without changing
  dependency versions.

## 2026-09-30 - Manually trigger workflow event nodes (0.75.0)

- Run a saved, enabled `trigger:event` node from its inspector with a JSON-object
  payload and optional correlation key, without broadcasting to other listeners.
- Check workflow run-control permissions and saved revisions, record the operator,
  and deduplicate retries before showing the queued run in existing run history.
- Preserve accepted attempts across workflow revision changes and compare retry
  payloads as canonical JSON, keeping boolean and numeric values distinct.

## 2026-09-30 - Bound control-plane system log storage (0.74.1)

- Manage daily rsyslog rotation in Ansible with 28 archives and delayed compression,
  preserving about four weeks of system log history while reducing uncompressed storage.
- Validate and back up the rotation policy before replacement without forcing rotation
  or restarting the logging service.

## 2026-09-30 - Configure Codex Fast mode per session and prompt (0.74.0)

- Add nullable `fast_mode` session defaults and per-prompt overrides to agent creation,
  workflow nodes, Admin Web, and CLI controls; explicit false selects standard speed.
- Forward the resolved tier through Codex VM, metered container, and subscription
  execution, preserving the selection through queues, wakes, context retries, and steering.
- Restore the session speed on later turns and expose the chosen mode and its source
  in execution and queue records. Fast requires a supported public OpenAI Codex route.
- Price metered Fast usage with the returned service tier across HTTP, streaming,
  and WebSocket responses, falling back to the requested tier when unavailable.
- Preserve an existing VM session's speed when creation omits the setting, and
  clear it when an upsert explicitly supplies null, matching container workers.

## 2026-09-30 - Display and redeem banked subscription resets (0.73.0)

- Show each subscription profile's banked reset count beside remaining weekly usage,
  distinguishing unavailable telemetry from zero available resets.
- Let privileged root owners redeem one reset, refresh account limits, and wake queued
  work when capacity recovers. Preserve account pins and retry identity to avoid
  consuming another reset after an uncertain response.
- Bind redemption to the original worker and account, and retain unresolved attempts
  across tab changes and reloads until the provider returns a definitive outcome.

## 2026-09-29 - Reuse Azure DevOps activity scans across pages (0.72.8)

- Reuse completed gateway activity scans for five minutes and share overlapping
  queries, with a 64 MiB serialized-data budget and bounded cache entries.
- Fetch full work-item records in batches while preserving custom fields, activity
  filtering, candidate omissions, and paging completeness.
- Recheck policy and provider credentials on each request, invalidate cached scans
  after gateway work-item and proxy-issue mutations, and expose content-free cache
  and scan timings.

## 2026-09-29 - Reduce event and retention writer contention (0.72.7)

- Check pending retention sources through queued history jobs and source primary keys,
  keeping maintenance work proportional to pending jobs instead of execution history.
- Skip unchanged subscription updates during event polling and retain known configuration
  revisions, while preserving filter changes, reactivation, and durable event delivery.
- Retry contended event-delivery writes through the worker's bounded storage retry path.

## 2026-09-29 - Bound Team case-history memory during lifecycle work (0.72.6)

- Read immutable case-context snapshots through indexed history queries instead of
  loading their payloads into every Team state read, validation, and mutation.
- Commit new snapshots atomically with dispatch state, command receipts, and outbox
  records; retain snapshot IDs, evidence, grants, and bounded retrieval contracts.
- Backfill history indexes in bounded batches and recover metadata after rollback
  to older writers. Preserve archived snapshots during operational state replacement.
- Use narrow completion and Git-index reads, and skip empty global chat planning
  while retaining expired preparation recovery.

## 2026-09-29 - Use square corners throughout Admin Web (0.72.5)

- Remove rounded corners from Admin Web and standalone add-on surfaces, dialogs,
  forms, ordinary buttons, chat messages, and graph nodes using shared radius tokens.
- Preserve pills and badges, avatars, circular controls and indicators, dials, and
  switches where their shape conveys their purpose.

## 2026-09-29 - Simplify Team task board cards (0.72.4)

- Open full task briefs from a bottom-row button in a modal on the board and hierarchy.
- Flatten unresolved descendants into a collapsible list of task links and remove
  nested lane, execution-status, and cancellation panels while retaining technical details.

## 2026-09-29 - Open workflow canvases at the first lane (0.72.3)

- Open each workflow pack and directory at the start of its topmost workflow at a
  readable zoom, including large canvases with many horizontal lanes.

## 2026-09-29 - Retire the Classic Admin Web UI (0.72.2)

- Serve the current Admin Web and add-on UIs directly, removing the temporary version
  selector, frozen Classic bundle, and Classic-only deployment checks.
- Block stale Classic bundle paths left by in-place upgrades, even when the current
  frontend assets are already present and setup skips rebuilding them.

## 2026-09-29 - Clarify inference cost reports and correct categories (0.72.1)

- Support bounded, atomic administrative category corrections with period and previous-label
  checks, preserving token counts, billing provenance, and immutable ingestion behavior.
- Refresh cost reports after historical corrections without changing monetary totals.
- Use distinct category and account colors, and show excluded-request counts so partial
  costs clearly indicate missing usage or pricing data.

## 2026-09-28 - Inference cost categories and analytics (0.72.0)

- Tag sessions with a cost category and override it on individual prompt effects, preserving
  attribution through queues, retries, and later session changes.
- Report API inference costs by provider, configured API key, and category with daily stacked
  bars, cumulative comparison lines, browser-local periods, and explicit pricing coverage.
- Add the MTD cumulative cost chart and report link to the dashboard. Keep subscription and
  local inference outside API monetary totals; retain safe credential names rather than secrets.

## 2026-09-28 - Queue workflow tasks by Team role (0.71.0)

- Add `effect:team:create-task-for-role` to create durable role-queued work through
  workflows, using existing delegation checks and dispatch-time agent selection.
- Reuse stable task IDs without resetting progress, ownership, or accepted work;
  commit creation and role-queue wake obligations together and reject identity collisions.
- Expose the node's inputs and outputs in the workflow editor and document retries,
  directory scope, dependency handling, and availability behavior.

## 2026-09-28 - Show remaining weekly subscription usage (0.70.0)

- Show the percentage remaining in the seven-day Codex rate-limit window for each subscription
  profile in Admin Web, with an unavailable state when the worker does not report that window.

## 2026-09-28 - Separate SQLite lock waits from operation deadlines (0.69.2)

- Remove the inferred 100 ms execution deadline from ordinary SQLite reads, writes, and
  repository locks so query execution and record hydration can complete under load.
- Restore normal callers' 30-second lock allowance; trigger and effect workers explicitly
  retain short lock waits and contention recovery without interrupting healthy queries.
- Preserve explicit reporting and maintenance execution deadlines independently of lock waits.

## 2026-09-28 - Honor the active model in standalone web search (0.69.1)

- Use the model in each Codex search request for hosted web search, allowing
  model changes within a session without changing its stored default model.

## 2026-09-28 - Enable metered Codex web search (0.69.0)

- Add session-gated `POST /v1/alpha/search` compatibility to the OpenAI relay,
  translating Codex web operations to hosted Responses web search and returning
  the answer with direct source URLs while accounting for upstream model usage.
- Advertise standalone web search to container Codex only for web-enabled public
  OpenAI sessions. Unsupported link-click and PDF screenshot operations return
  explicit errors.
- Reject batches that would apply a per-query domain filter to other operations,
  and preserve direct image and source-page URLs from structured search results.

## 2026-09-28 - Bound orchestration storage and recover completions (0.68.1)

- Retain completed provider results across transient storage failures and atomically publish
  effect and gate successors, with bounded fair retry and independent delivery recovery.
- Cache schema readiness, retain idle SQLite WAL anchors, and move passive checkpoints,
  incremental indexing, retention, and garbage collection to isolated bounded maintenance.
- Store large operational payloads as shared immutable compressed objects; keep lifecycle
  metadata small and preserve legacy reads, prompt receipts, and ancestor bindings.
- Keep sanitized execution details immediately available while field search indexes complete
  generations asynchronously, with a visible pending count.
- Return compact Team finalization receipts, reuse lazy event publishers, and perform
  chat busy checks outside write transactions with revision-checked dispatch planning.
- Add explicit offline storage migration, verification, index preparation, and inline rollback.
- Preserve scalar provider scope outputs and long lifecycle errors, including reads and updates
  of previously referenced errors. Give report aggregation its own read deadline.
- Recover trigger-worker storage contention and retain Team automatic WAL checkpoints.
- Resume dense history retention with bounded index deletion and durable batch reduction after
  deadlines; restore complete indexes if retiring records change or storage is downgraded.
- Apply the separate reporting deadline to execution-history endpoints, and discard completion
  retry alarms after their pending receipts are cleared so idle workers can sleep.

## 2026-09-28 - Add agent reads for Azure DevOps builds (0.68.0)

- Add separately grantable, read-only MCP tools to fetch one configured-project build by ID and
  list its associated same-project work-item IDs. Include the build source commit and explicit
  truncation for bounded work-item results.
- Reuse directory-scoped host ADO credentials and keep upstream response bodies out of tool errors.

## 2026-09-28 - Recover Team Git incidents autonomously (0.67.0)

- Add durable typed Git incidents, blocker-scoped preview/apply tools, project recovery
  policy, baseline intent and excluded ancestry, and bounded retry budgets across successors.
- Verify pinned inputs before workspace preparation, preserve failed attempts, prepare new
  physical identities, and route reviewed replacement or same-branch renewal to project agents.
- Capture staged and working content through scoped Git refs on VM and container backends,
  retain original work against cleanup and teardown, and require compatible worker capabilities.
- Adopt reviewed successors through task lifecycle rules, preserve submitted consumer history,
  and recover publication destination races through fresh release reintegration and gates.
- Fence adoption against changed requirements, wait for the complete reviewed task and all its
  products, and close superseded publication failures after the replacement successfully publishes.
- Expose recovery evidence, policy controls, retained work, and cause/latency counts in Admin Web
  and agent reads. Legacy ambiguous pins remain conservative; historical incidents are not replayed.

## 2026-09-28 - Add privacy controls and managed credentials (0.66.0)

- Add scoped privacy inventory, revisioned policies, cases, holds, reviewed local jobs,
  encrypted reviewer exports, external follow-ups, and incident records through Admin Web
  and the trusted-host CLI. Explicitly report unsupported lifecycle operations and incomplete
  coverage; installation-wide readiness and enforcement are not claimed.
- Schedule bounded, opt-in attachment-staging retention and honor holds in existing
  opportunistic artifact expiry, with durable retry evidence and stale-selection checks.
- Serialize voice, meeting, and DIDWW JSON transactions across processes. Move Teams and
  connector credentials into a shared encrypted, revisioned store with explicit ownership,
  consumer bindings, maintenance migration, recovery snapshots, and protected key delivery.
- Add fresh, endpoint-bound provider approval checks to model relay, classifier, and Codex
  subscription egress paths, including retries and persistent-connection revocation. Other
  outbound paths remain explicit coverage gaps; report-only remains the upgrade default.
- Apply the same dispatch and streaming checks to OpenRouter model routes.
- Add metadata-only credential administration, managed Teams sign-in, safe refresh locking,
  connector credential references, key rotation, and fail-closed migration/recovery behavior.
- Read proxy privacy policy without filesystem writes and allow empty legacy Teams stores
  during first-time managed setup. Resume retention beyond held records, count deletion
  failures independently of successful batches, and prevent schedules from duplicating
  unresolved selections. Expose scoped, audited detail reads for saved privacy evidence.
- Preserve ordinary connector content after credential migration, validate child privacy
  overrides against inherited policy, and keep unavailable Teams identities visible to
  administrators. Resolve subscription policy once per bounded shared snapshot instead of
  repeatedly walking the directory registry for every tunnel read.
- Preflight descendant connector profiles before parent credential conversion and report
  actionable UI/CLI conflicts without changing independent child credentials. Move SSE
  policy/session checks off the relay event loop and bound them to one check per stream
  interval while preserving idle-stream revocation and upstream cleanup.
- Include technical privacy review drafts and deployment/restore caveats. No production
  policies, provider contracts, legal approvals, or customer-data cleanup are applied.

## 2026-09-27 - Configure agent reasoning effort (0.65.0)

- Add session reasoning defaults and per-prompt overrides in Admin Web, the CLI, and agent creation, prompt, and enqueue-prompt workflow effects.
- Forward native effort settings through Codex, Claude Code, and Copilot on their supported execution backends, including Codex subscription turns and OpenAI-compatible relays.
- Validate known model capabilities, preserve provider defaults, and retain the resolved effort and its source across queues, subscription deferrals, and context retries.
- Apply session effort to scheduled and Discord wakes, retain it through wake and steering replays, and validate local-model effort against the effective provider route.

## 2026-09-27 - Reduce gateway connection and workflow history write overhead (0.64.3)

- Store MCP connection bindings in SQLite and transactionally update one connection at a time,
  preserving binding identity, tool snapshots, conflict diagnostics and creation timestamps.
- Import existing JSON connection history once in a transaction; retain the original snapshot
  and keep gateway, Admin Web and Team review readers on the same authoritative database.
- Reuse existing compressed execution-history payloads and scalar indexes when only lifecycle
  metadata changes, while continuing to update status, scope and source provenance.
- Keep reused history payloads in a write transaction so concurrent retention cannot remove
  them before backfill commits their references.
- Add opt-in DEBUG execution-persistence logs with trace IDs and separate record/history
  durations, preserving the existing application logging defaults.

## 2026-09-27 - Reduce Teams context and workflow preparation overhead (0.64.2)

- Return compact Teams history metadata by default, retaining message content, sender/session
  attribution, reply and attachment references, card content, mentions, reactions and edits.
  Full provider metadata remains available with `include_metadata: true`.
- Reuse the execution graph for an unchanged configuration revision and read only graph membership
  from configuration snapshots when recording historical execution scope.
- Skip display-only account-usage telemetry during subscription turn preflight, retaining fresh
  authentication, account pinning and capacity checks. Operator status still includes usage.
- Record subscription startup phase durations in worker logs and job metadata to distinguish
  process initialization, login, thread opening and turn submission.
- Add a repeatable synthetic workflow and Teams response-size benchmark.

## 2026-09-26 - Keep request storage off the event loop (0.64.1)

- Offload MCP authentication, transport persistence, discovery, approval checks and dispatch
  audit writes through the bounded worker pool while preserving request context and ordering.
- Recheck transport ownership after asynchronous authentication so concurrent requests cannot
  bind the same connection to different sessions.
- Preserve final tool-call audits when an MCP request is cancelled during tool execution, and
  atomically create or renew approvals so concurrent duplicate calls notify only once.
- Offload Admin Web authentication, session and bootstrap responses, and blocking Azure sign-in
  operations so storage and identity-provider waits leave unrelated requests responsive.
- Reuse a bounded session-manifest snapshot keyed by file identity and revision; observe external
  writes and revocations on the next lookup and return detached session models.
- Add a repeatable concurrent MCP/Admin/workflow benchmark and regression coverage for blocked
  storage, concurrent approval consumption and session-cache invalidation.

## 2026-09-26 - Improve Admin Web readability and usability (0.64.0)

- Refresh the documentation map and first-run path; align operator, configuration, execution,
  Team, and recovery guidance with current behavior and publish an updated technical manual.

- Keep populated record lists from overlapping, align checkbox filters with adjacent fields,
  and remove duplicate search borders across the shared compact design.
- Keep the page finder search visible and scroll keyboard-selected results into view.
- Label configuration and worker inputs, use readable boolean choices, and prevent edits or
  misleading validation on inherited and cleared settings.
- Clarify loading states, issue and VM statuses, session context, and long artifact identifiers;
  make report records keyboard-accessible and show zero activity accurately.
- Improve Team navigation, project and memory layouts, and integration connection summaries,
  selection feedback, and form alignment at desktop and narrow widths. Refresh Activity
  when returning to the view.
- Condense opaque identifiers and commit hashes into hoverable, copyable badges in Team chat,
  case context, and other narratives, including existing records. Guide future agent messages
  to use meaningful names and mark exact identifiers with inline backticks.

## 2026-09-25 - Harden state writes and audit visibility (0.63.9)

- Write Teams credentials atomically with cross-process coordination; share a unique-temp JSON writer
  with cursor, assistant-session, and PR-feedback stores.
- Log audit index and session thread metadata failures, and return an error when the tool-call audit
  log is unreadable instead of reporting an empty history.
- Separate lease reconciliation and manager fallback, extract team-copy session validation, remove
  stale imports, and replace route assertions with explicit error responses.

## 2026-09-25 - Speed up Azure DevOps work-item activity queries (0.63.8)

- Reuse one HTTP connection pool while fetching candidate work items and their activity history.
- Fetch activity for up to eight candidates concurrently, preserving result order and paging behavior.
- Run the candidate WIQL query once per activity scan instead of once per candidate page.
- Advance pagination and scan limits by consumed candidate IDs when Azure DevOps omits deleted items.
- Use concurrent fetching for partial pages containing two to seven candidates as well.

## 2026-09-24 - Remove tests without distinct regression value (0.63.7)

- Remove 169 Python test definitions and 111 Admin Web cases that only checked
  fixtures, source wording, trivial value echoes, or behavior covered more directly elsewhere.
- Preserve the complete test-by-test review and rationale in `docs/test-suite-review-2026-09-24.*`.
- Remove stale references to deleted tests and refresh the published user manual.

## 2026-09-24 - Restore Codex subscription auth availability (0.63.6)

- Give each subscription auth container an ephemeral writable home so Codex can inspect and
  create legacy workspace configuration when the container runs as the worker service account.
- Keep durable credentials isolated in the existing private profile mount.

## 2026-09-24 - Correct release news pager arrows (0.63.5)

- Match the Newer and Older arrow icons to their button labels in the dashboard and article reader.

## 2026-09-24 - Reduce test-suite duplication and execution overhead (0.63.4)

- Add a bounded complete Python runner with four parallel workers, a separate phase for
  elapsed-time contracts, and execution ledgers that detect omitted or repeated cases.
- Collect conformance evidence per test and publish reports after execution, preserving
  complete traceability checks across reordered tests and parallel workers.
- Consolidate duplicate tests and setup while retaining distinct authorization, boundary,
  failure, and lifecycle scenarios; replace ineffective Git-ignore publication checks.
- Reuse isolated Figma and browser harnesses, cache documentation anchor parsing, and stub
  unnecessary SSH discovery. Keep fresh mutable state for each case.
- Separate frontend test lanes with complete aggregate commands, replace selected source-text
  assertions with behavior checks, and fix timer and asynchronous polling cleanup.
- Retry SQLite's busy WAL initialization within the existing timeout, preserving concurrent
  event-bus startup and publication when independent constructors race.

## 2026-09-24 - Guard pinned Team integration bases (0.63.3)

- Inherit parent commit pins when creating children in the same Git workstream,
  including nested and batch-created tasks, so later branch advances cannot change their base.
- Reject accepted contributions whose recorded starting history is outside the pinned
  integration base, including nested compositions and missing source provenance.
- Recheck contribution bases at submission and require server verification before integration
  acceptance, preserving existing accepted history and requiring explicit recovery for mismatches.

## 2026-09-24 - Make New UI the default (0.63.2)

- Make New UI the default while retaining the Classic UI picker and saved preference.
- Widen the collapsed navigation so section labels stay on one line.

## 2026-09-24 - Align provider catalog regression expectations (0.63.1)

- Include OpenRouter in Admin Web provider-mode catalog assertions so the checked
  options match the supported agent and session creation modes.

## 2026-09-24 - Pinned OpenRouter Codex routing (0.63.0)

- Add an opt-in OpenRouter provider for Codex HTTP Responses requests and
  compaction passthrough, with exact session model allowlists, one configured
  provider route, mandatory ZDR and parameter support, and disabled fallbacks.
- Keep provider credentials on the host, reject guest routing overrides and
  OpenRouter server tools, and expose provider settings in Admin Web.
- Preserve implicit Anthropic routing when creating or reprovisioning Claude
  agents in OpenRouter-default directories; explicit Claude/OpenRouter routing remains rejected.
- Capture routing metadata and provider-reported USD charges without retaining
  prompt or response content. Separate reported charges from catalog estimates
  and include both in Team budgets, durable cost reports, and cost displays while
  preserving fractional dollars.
- Leave OpenRouter routes empty and public OpenAI as the default; live provider
  compatibility and credentials require operator configuration.

## 2026-09-24 - Open Admin Web details directly (0.62.5)

- Open Agents, Sessions, and the other record management pages directly in their
  sidebar and detail layout, with a selection prompt until an item is chosen.
- Open complete details with one sidebar click; preserve filters, shareable links,
  creation actions, and unsaved-change confirmation without an intermediate preview.
- Keep upload, registration, and directory configuration forms available in compact
  disclosures. Existing inspector links open details directly in the New UI.

## 2026-09-24 - Reduce Admin Web read overhead (0.62.4)

- Let execution-history clients omit exact match counts while retaining cursor pagination;
  show loaded rows and whether more are available in the workflow editor.
- Find execution-history availability bounds with index seeks and move history reads and
  response serialization off the web event loop, preserving authorization checks.
- Filter session inventories before sorting so narrow views only sort their matching rows.
- Read workflow run and recent-activity display fields directly from SQLite instead of
  hydrating and serializing full execution payloads; keep control operations on full records.
- Resolve communication targets once per workflow catalog request, retaining directory and
  capability filtering without introducing a shared configuration cache.
- Keep cleanup audit tests independent of the current date by aligning the journal's
  retention clock with their simulated cleanup timestamps.

## 2026-09-24 - Stable Team copy previews (0.62.3)

- Keep Team copy previews valid while tasks, inbox items, dispatches, runtime state, cost history,
  and other excluded operational records continue changing.
- Reject a stale copy only when the stable source configuration graph changed, including Team
  settings, projects, roles, agents, assignments, review profiles, and outcome kinds.

## 2026-09-24 - Improve Team chat navigation and stability (0.62.2)

- Preserve the new-chat agent picker position through Team state refreshes, so operators can
  select agents far down the list without being returned to its start.
- Add direct Chat actions in the Teams agent detail and Org chart agent menu.

## 2026-09-24 - Make workflow node types easier to distinguish (0.62.1)

- Give triggers, gates, effects, and queries distinct amber, green, blue, and purple
  type bands, tinted backgrounds, and clearer labels in both New UI themes.
- Keep selection, error, disabled, unsaved, and execution indicators visible alongside
  node type styling.

## 2026-09-24 - Resolve Teams direct chats from UPNs (0.62.0)

- Let workflow editors choose direct-chat setup by UPN or retain explicit Teams chat and sender
  IDs. Resolve direct-chat UPNs through host-owned Microsoft Graph access during revision-gated
  saves, ensure each one-to-one chat, and persist canonical user and chat IDs while keeping the
  readable UPNs in the form.

## 2026-09-24 - Separate oversized maintenance responsibilities (0.61.1)

- Move Team agent leasing, action application, task administration, and task-update
  execution into named domain modules while retaining service constructors, method
  signatures, revision checks, clock behavior, and outbox transactions.
- Give worker Git workspace preparation, dependency composition, and cleanup a dedicated
  implementation boundary while preserving established exception and helper imports.
- Replace numbered Team MCP registration slices with domain names without changing tool
  registration order, names, descriptions, or handlers.
- Separate Team UI state and routing, session approval policy and dialogs, DIDWW delegated
  origin queries, and OpenAI usage accounting from their oversized entry points.
- Document the compatibility contracts for the new boundaries and keep their implementation
  bodies mechanically equivalent to the prior code.

## 2026-09-23 - Unify the Admin Web design (0.61.0)

- Offer a temporary Classic / New UI selector, defaulting to a frozen main UI until
  testing is complete. Load only the chosen host and add-on assets, remember the
  browser preference, and confirm before leaving unsaved work.
- Use page-specific actions such as View agent, View session, and View repository,
  with simpler list navigation and add-on labels.
- Introduce shared light/dark colors, typography, controls, and consistent compact spacing
  across Admin Web and all six standalone add-ons, with larger touch targets where needed.
- Replace repeated record cards with comparable inventories and closable inspectors,
  retaining full forms, deep links, filtering context, and narrow-screen dialogs.
- Add sortable inventory headers with text, numeric, and chronological ordering;
  fit full-record sidebar filters and actions within the compact layout, with space
  between back navigation and record panels.
- Refine workflow commands, configuration, reports, Team workspaces and boards,
  integration settings, login, notifications, and the assistant with the same visual system.
- Keep release news prominent, with readable articles, images, archive navigation,
  and build/changelog details. Preserve configuration fields and revision-gated saves.
- Apply the existing privilege-lock state consistently to skill, add-on, and integration
  mutation controls, while preserving drafts and read access.
- Keep record selection and deep links synchronized during hash navigation and sidebar changes;
  preserve inherited Miro operation recovery and the requested row counts of select listboxes.
- Preserve fractional Team cost totals, refresh the Chat revision indicator, and disable
  transitions under reduced motion so Org chart nodes remain visible.

## 2026-09-23 - Make private package routing directory scoped (0.60.0)

- Read optional private NuGet package patterns from directory provider state when preparing
  container sessions, with no customer-specific package mapping in the default configuration.
- Read retired Team guidance section IDs from directory provider state when reseeding agents.
- Preserve live Teams notification settings when deployments stop using checked-in host overrides.
- Remove customer-specific demo examples and retired metadata handling after migrating live
  workflow writes to generic HTTP targets.
- Use Evander branding in guest guidance, assistant prompts, Admin Web, and the landing page.
- Move deployment-specific host and website settings out of tracked defaults.

## 2026-09-23 - Optimize control-plane hot paths (0.59.2)

- Paginate Team task reads before enrichment, reuse task and project assessments, index
  deliverable coverage, and skip assessment work for agents without assigned tasks.
- Shorten Team write preparation and use revision-checked dispatch lease updates while
  preserving command callbacks, outbox atomicity, lifecycle validation, and lease ownership.
- Move relay persistence and steering off the event loop, preserving usage recording and
  upstream cleanup when streams disconnect.
- Batch and cache worker inventory, bound Admin response and chat transcript caches, and
  use direct session lookups with compact batched lifecycle inputs.
- Preserve partial worker inventory when the systemctl sudo fallback fails or returns
  only a different subset of units.
- Incrementally project worker transcripts, read log tails from the end, and merge overlapping
  output in linear time with bounded retained projection state.
- Poll only the VM being watched, query Discord trigger messages using indexed cursors, page
  semantic indexing with durable continuation keys, and reclaim index content in bounded batches.
- Add regression coverage for concurrency, cancellation, cache invalidation, transcript rotation,
  cursor ties, legacy checkpoints, and repository index schema migration.

## 2026-09-22 - Separate control-plane implementation responsibilities (0.59.1)

- Split Team API routes, workflow providers, domain models, derived views, and SQLite
  persistence into cohesive modules while retaining public imports and migration order.
- Separate integration services, communication tool groups, and CLI commands with stable
  tool registration, command help, request schemas, and operator behavior.
- Give workflow run reporting and realtime voice playback explicit per-invocation state
  and smaller processing stages, preserving correlation, cancellation, and cleanup behavior.
- Extract workflow editor components, state hooks, and pure helpers with typed interfaces
  while preserving editing, inspection, validation, and save interactions.
- Align model and payload assertions and the Team tool inventory with the merged release.

## 2026-09-22 - Recover releases with pinned integration and verifier evidence (0.59.0)

- Add contextual, revision-gated release reintegration in Admin Web and MCP, preserving
  accepted contributions and work lineage. Project policy controls ownership and retirement.
- Restart interrupted verifier command sequences in bounded fresh workspace generations,
  preserving history and rejecting stale progress/results.
- Separate raw execution outcomes from optional structured verification policy, including
  explicit baseline exceptions and configurable correction versus blocking decisions.
- Retain redacted verifier output and local evidence before session cleanup, with historical
  log lookup and visible toolchain identities. Immediately wake operator dependency recovery.
- Stage and validate immutable worker .NET bundles, atomically switch selectors and pin
  resolved mounts per session. Keep project-specific evaluators in operator-owned
  configuration outside this repository.
- Make repeated verifier cleanup safe, allow operator retries of command-blocked gates,
  reset idle no-progress counters for new integration work, enforce evidence byte limits
  at the worker read and transport, and omit disabled .NET toolchain mounts.

## 2026-09-22 - Use GPT-6 for new agent defaults (0.58.3)

- Use GPT-6 Sol for Codex agent fallbacks, new OpenAI setup, and guest bootstrap defaults.
- Seed new installations with the verified GPT-6 Sol, Luna, and Astra pricing while retaining
  historical rates for past usage.
- Update inactive workflow examples to select GPT-6 Sol or Luna according to their prior tier.
- Omit older GPT generations from the session model picker without removing their pricing history.

## 2026-09-22 - Isolate Team lifecycle maintenance from message dispatch (0.58.2)

- Run Team lease and completion reconciliation on a dedicated single-flight lane so
  database contention cannot hold up unrelated assistant workflow events.
- Reuse initialized Team repositories, discover lifecycle candidates through query-only
  snapshots, and defer contended maintenance writes after a bounded lock wait.
- Coalesce completion hints while retaining durable receipt recovery, serialize lifecycle
  cursors, and drain maintenance before runtime rebinding or worker shutdown.
- Keep receipt reads independent of foreground execution locks, drain maintenance on every
  locked single-cycle exit, and preserve running maintenance during graph-only configuration edits.
- Add phase timings and contention, recovery, and foreground-latency regression coverage.

## 2026-09-22 - Reduce Admin Web read latency (0.58.1)

- Maintain an incremental Team cost report with exact request deduplication, attribution,
  pricing and budget-window invalidation; coalesce and throttle UI cost refreshes.
- Read only the Team collections needed by each view, and move projection, workflow reads,
  and JSON serialization off the web event loop while preserving permission filtering.
- Bound and parallelize worker health probes, share short-lived capacity and rolling workflow
  report snapshots, and aggregate workflow activity once per report.
- Cover directory-scoped audit status counts without fetching large audit payload rows.
- Separate SSE startup latency from subscription lifetime in slow-request logs, include response
  timing phases, and record failures that occur after response headers have been sent.

## 2026-09-22 - Review Miro layouts and iterate on repairs (0.58.0)

- Add advisory layout reports to canvas snapshots and new completed writes, naming shared exits,
  overlapping reconstructed routes, and possible node crossings by stable item IDs.
- Let agents view a bounded reconstructed PNG through `miro_get_snapshot(include_preview=true)`
  under the existing tool grant. Label it as a diagnostic, not a Miro screenshot, and disclose
  unsupported geometry, unknown routing, and incomplete coverage.
- Guide agents and workflows through read/view/repair iterations with distinct branch exits,
  empty bypass lanes, fresh operation keys for changed repairs, and preservation of human edits.
  Keep layout findings separate from write success and snapshot conflict detection.

## 2026-09-22 - Restore projects after terminal blocker recovery (0.57.3)

- Recalculate every directly affected project's status when an operator resolves a terminal
  blocker, including terminal-release review blockers that the project-status display omits.
- Resolve project targets from blocker, task, and source relationships without recomputing all
  projects' task assessments while the state write is locked.
- Preserve execution-stall quarantine for post-release reports while covering the operator path
  that cancels unnecessary triage, closes its feedback case, and resolves the stale stall.

## 2026-09-21 - Keep Miro diagrams individually editable (0.57.2)

- Make separate native shapes, text and connectors the default for human-agent collaboration,
  including flowcharts. Request free-form diagram guidance when no notation is supplied and
  the provider supports it; retain explicit notation choices and older provider schemas.
- Reserve Mermaid widgets for explicitly requested code-based diagrams. Update agent and workflow
  guidance to verify individual item IDs after creation and preserve human edits during revisions,
  while retaining the native connector checks for arrow directions and labels.

## 2026-09-21 - Validate Miro diagram connectors (0.57.1)

- Follow Miro's current canvas-composer workflow through its design, edit, and DSL steps, and
  expose diagram notation and repository context to agents and workflows.
- Direct flowcharts toward native Mermaid diagrams and free-form diagrams toward attached Miro
  connectors whose direction and label remain bound to the connector.
- Reject lines and paths without valid widget endpoints before contacting Miro, with corrective
  guidance for native arrowheads and connector labels. Keep old snapshots readable and preserve
  previously recorded write outcomes on retries.
- Accept HTML slides, embeds, and prototype screens as connector endpoints while continuing to
  reject frames, structural groups, and deleted widgets.

## 2026-09-21 - Read scoped Miro canvases (0.57.0)

- Add `miro_search_canvas` and `query:miro:search-canvas` for board overviews, areas, item
  matches, and pagination under normal session and board permissions.
- Support explicit canvas areas and frame/item selections across reads, snapshots, updates,
  creation readback, recovery, and workflow polling. Verify new empty boards with search and
  a retained initial area; populated boards require an explicit selection.
- Use Miro's current frame URL and widget selection contract, and preserve durable retries
  recorded before the new read parameters were introduced.
- Let operators confirm an acknowledged board creation using its returned ID and explicit
  evidence when a later deletion or sharing change makes readback unavailable. Other writes
  still require readback before resolution.

## 2026-09-21 - Recognize Miro board creation links (0.56.1)

- Extract board IDs from Miro's `miro_url` response in the shared creation service used by
  `miro_create_board` and `effect:miro:create-board`, retaining existing ID and URL formats.
- Let successful creation register the new board with read/write access and read it back instead
  of leaving the operation uncertain because its board reference was not recognized.
- Preserve literal `=` padding in Miro board links so subsequent upstream calls address the
  created board correctly instead of receiving a misleading permission error.
- Cover both entry points with structured and JSON-text MCP responses, plus subsequent board
  access, durable retries, and URL validation.

## 2026-09-21 - Discover Miro connections and Figma files (0.56.0)

- Add `miro_list_connections` so agents can discover enabled connection IDs, directory scope,
  board counts, and board-creation settings without guessing an ID or contacting Miro.
- Make Miro connection discovery and Figma's existing `figma_list_targets` available through
  existing operation tool grants. Preserve directory denials, approval requirements, Miro
  connection inheritance and overrides, and Figma's exact-directory file permissions.
- Keep discovery limited to public local configuration without exposing credentials or granting
  additional board/file operations. Document the discovery-first agent flow for both add-ons.

## 2026-09-21 - Use normal session permissions for Figma (0.55.2)

- Remove Figma's separate session access list. Configure capabilities and editable roots per
  file, while agent tools and workflow operations use the normal session tool policy.
- Migrate existing grants to shared file permissions, preserving allowed editable areas and
  OAuth connections. Let the operator edit file permissions directly without entering session IDs.

## 2026-09-21 - Follow Admin Web settings in Miro (0.55.1)

- Make the Miro page follow Admin Web's light and dark theme on load and when toggled, preserving
  unsaved connection settings and styling form controls, links, and activity details consistently.
- Use normal per-session Miro tool permissions instead of a second connection session list.
  Preserve directory, board, and workflow grants, and accept existing connections without
  requiring reauthorization.

## 2026-09-20 - Diagnose slow Admin Web requests (0.55.0)

- Log Admin Web requests that cross an operator-configured duration threshold and every 5xx
  response with canonical route, method, status, and duration. Exclude query strings, cookies,
  and request bodies so journal review does not expose request contents.

## 2026-09-20 - Manage multi-project Team assignments (0.54.0)

- Let the Team agent editor manage the full set of project memberships, including clear
  project-lead marking and a reminder that lead transfers remain project-scoped.
- Accept revision-aware multi-project agent updates through the Team Admin API while retaining
  existing project-lead assignments, and make lead transfers safe when the former lead is already
  a member of that project.
- Preserve multi-project choices during dynamic editor refreshes and keep project-scoped roles
  limited to assignments for their own project.

## 2026-09-20 - Discover granted email mailboxes (0.53.0)

- Add `email_list_mailboxes` so agents can discover their authorized mailbox IDs, accounts,
  access levels and folders before reading mail or creating unsent drafts.
- Make discovery available to existing email-enabled sessions while preserving directory tool
  restrictions, explicit empty grants and legacy mailbox policy. Return only session-scoped
  public metadata without contacting providers or exposing credential references.

## 2026-09-20 - Lay out declarative workflow patches (0.52.0)

- Allow declarative workflow document patches to position every namespaced node while preserving
  unrelated graph positions and replacing only patch-owned coordinates when reapplied.
- Include layout changes in preview, validation, no-op detection, and revision-safe saves, and
  provide a readable horizontal-lane layout for the App PR-testing workflow.

## 2026-09-20 - Make Admin Web lists easier to scan (0.51.0)

- Show communication targets, contacts, and email mailboxes as compact, searchable, sortable
  summaries with expandable editors; add provider filtering and retain inherited target actions.
- Reduce shared navigation, form, panel, and table spacing; remove repeated single-setting
  headings and collapse optional Teams lookup and directory metadata.
- Preserve configuration drafts and original record identity while filtering and sorting,
  including failed-save recovery, keyboard access, and read-only inspection. Close editors when
  a saved snapshot changes so sorted contact readback cannot silently select another record.

## 2026-09-19 - Preserve subscription-capacity replay routing (0.50.1)

- Preserve the selected ChatGPT subscription profile when an initially unprofiled queued replay
  is deferred for capacity, while retaining stale-reroute protection.
- Align workflow catalog, schema-migration, Team lifecycle, and gate-documentation coverage with
  the current explicit contracts.

## 2026-09-19 - Route workflows with Smart switch (0.50.0)

- Add Smart switch (`gate:classifier:switch`) with described branches, an explicit default,
  and an optional probability cutoff using the shared classification service.
- Run classification asynchronously with durable recovery and preserve the original payload
  on the selected branch. Resolve provider settings from the workflow's trusted directory.
- Add structured workflow editing, sample evaluation, an assistant-level routing example,
  and an explicit opt-in live smoke check.

## 2026-09-19 - Collaborate on native Figma mockups (0.49.0)

- Add an optional Figma add-on with shared agent tools, workflow providers, directory-scoped
  file grants, and an operator page for host-held OAuth connections, previews, and operation history.
- Support separate proposals, author/reviewer workflows, and coordinated edits to shared screens;
  retain immutable design snapshots and compose only unchanged snapshot sources.
- Serialize file writes across processes, reject stale revisions, and retain uncertain outcomes
  for readback and explicit recovery instead of replaying writes. Poll external comments and named
  versions with durable delivery acknowledgements and self-feedback suppression.
- Keep authoring disabled until an approved Figma client passes a live unattended
  create/edit/render/readback test. Figma client approval and credentials remain deployment prerequisites.
- Prevent settings writes during directory changes, reject batches that would exceed the design
  inspection limit before mutation, and verify comment content and account identity during recovery.
- Keep large text nodes readable during inspection, snapshot capture, and uncertain-write recovery;
  interrupted snapshots no longer block canvas authors after a process restart.
- Suppress self-authored comment feedback across registrations of the same file, and preserve
  unspecified font attributes while loading fonts introduced by existing text styles.

## 2026-09-19 - Collaborate visually through Miro (0.48.0)

- Add an opt-in Miro integration with editable canvas diagrams, scoped snapshots, comment
  collaboration, agent tools, and matching workflow queries, effects, and polling triggers.
- Manage named, directory-scoped OAuth connections, board/session grants, call budgets,
  capability checks, and uncertain-write recovery through Admin Web. Credentials stay encrypted
  on the host; overlapping writes are serialized and stale snapshots cannot overwrite observed edits.
- Keep publishing, feedback routing, monitoring cadence, and agent coordination in operator-authored
  workflows, with optional examples and durable polling checkpoints that survive restarts.
- Use Miro's published tool parameters, offset comment pagination, and explicit SVG deletion
  markers. Serialize operation keys across boards, preserve completed writes during readback,
  and keep concurrent human edits visible to monitoring.
- Enforce operation directory ownership during recovery, reset polling safely when connections
  are overridden, and preserve idempotent board-creation retries at the connection's board limit.

## 2026-09-19 - Core classification with Jev (0.47.0)

- Add a provider-neutral classification service with Jev as its first implementation,
  supporting Choice, Score, and Boolean probability questions on shared input.
- Add directory-scoped provider settings and fixed reusable profiles, the built-in
  `query:classification:evaluate` workflow node, and ordinary grantable agent tools
  `classification_evaluate` and `classification_list_profiles`.
- Add structured Admin Web profile/workflow editors, sample evaluations, revision-aware
  saves, atomic profile inheritance, and known answer fields in workflow binding suggestions.
- Persist resolved classification definitions before workflow execution and attribute
  bounded provider attempts through the existing model usage store. Keep classification
  disabled by default, with host-owned credentials and no automatic session grants.
- Document setup, recovery, an example workflow, and an explicit opt-in live smoke command.

## 2026-09-18 - Browse repository index history (0.46.0)

- Show repository index creation times, build IDs, exact commits and a clear newest marker in
  Admin Web; default to ready snapshots while retaining filters for partial and operational builds.
- Page index history newest-first through the Admin API so retained snapshots beyond the previous
  200-entry display ceiling remain reachable.

## 2026-09-17 - Separate coordination from Git integration (0.45.1)

- Add an explicit coordination task kind that supervises child work but cannot own a
  deliverable, integration contract, or prepared Git context.
- Require coordinated Git work to hand off into a fresh top-level, SHA-pinned integration task,
  while retaining direct-child aggregation only for historical source-bearing deliverables.
- Reject integration tasks parented to coordination even when the caller omits the provenance
  link, expose that link in Task Board creation, and require coordinated contributions to name
  their coordination origin.
- Upgrade eligible pre-0.44.1 system-created human-review aggregation parents to coordination
  tasks during reconciliation so existing correction workflows gain the same protections.

## 2026-09-17 - Test App feature branches from PR comments (0.45.0)

- Add an opt-in App `!TEST <environment>` workflow migration: acknowledge the pinned PR build,
  wait for it, redeploy an existing named demo, and resume the same PR reviewer with managed demo
  skills to prepare data and explain how to test. Missing, stale, failed and busy deployments
  receive deterministic replies; duplicate comment delivery is suppressed.
- Add directory-scoped ADO build lookup/wait providers and additive agent skill installation.
- Keep command syntax, environment rules, routing, messages and prompts in declarative workflow
  configuration. Provide generic text-regex and build-filter queries and a revision-safe patch
  installer instead of a App-specific runtime provider or Python workflow builder.
- Route terminal agent execution failures and timeouts through an explicitly connected prompt
  `error` port, while preserving legacy behavior for workflows without one.

## 2026-09-17 - Switch Codex usage without replacing sessions (0.44.0)

- Let operators move a container Codex agent between metered API usage and enabled ChatGPT
  subscription profiles while retaining the Evander session, and release prompts deferred by the
  previous subscription route so they can run under the new selection. Profile choices come from
  the session's assigned worker, and route reconciliation is safe to retry during concurrent queue
  dispatch.

## 2026-09-17 - Propagate managed skill replacements (0.43.3)

- Reinstall a replaced managed Codex skill into every active VM and container session that already
  declares it, including skills installed during VM creation; preserve each skill's VM user and
  serialize replacements so subsequent agent turns use the latest centrally administered content.
- Preserve the updated library copy while reporting partial propagation failures with exact failed
  sessions, allowing operators to retry reconciliation safely with another replacement.

## 2026-09-16 - Resolve PR attachment identities (0.43.2)

- Resolve Azure DevOps project and repository GUIDs from the authorized pull request when
  validating PR-feedback attachment URLs, including for PRs that are not present in
  `pr-map.json`, while retaining canonical route and feedback-stream enforcement.

## 2026-09-16 - Pin final integration provenance (0.43.1)

- Snapshot the managed base-branch commit when a Git-backed integration boundary is created,
  validate caller-supplied pins against managed branch ancestry, and keep the immutable review
  baseline distinct from both the dependency-composed checkout receipt and prepared head.
- Guide managers to coordinate long-running work separately and create the final integration task
  only after its required contributions have current accepted deliverables; reject converting an
  existing Git-backed coordination task into an unpinned integration boundary.

## 2026-09-16 - Read PR feedback attachments (0.43.0)

- Add policy-controlled tools that list and download files linked from authorized Azure DevOps PR
  feedback, using host credentials, canonical feedback-scoped attachment routes, and opaque staged
  artifacts for guest workspace copies.
- Grant the attachment flow to PR-tracked sessions and document it in the built-in agent workflow
  prompts without exposing provider URLs or credentials to agents.

## 2026-09-16 - Readable Team case context (0.42.4)

- Present case context as a scannable case file with plain-language warnings, named sections,
  record titles, status and timestamps, narrative fields, safe source links, and responsive
  styling while retaining exact fields, provenance and raw JSON in secondary disclosures.

## 2026-09-16 - Accept large Freshservice attachment IDs (0.42.3)

- Accept positive Freshservice ticket and attachment identifiers up to the signed 64-bit limit,
  including attachment IDs larger than the previous 32-bit ceiling.

## 2026-09-16 - Reuse retained reference repositories (0.42.2)

- Keep the managed-mirror commit requested for reference preparation separate from the trusted
  checkout commit retained by a persistent worker, allowing receipt reuse when the mirror has
  advanced but `refresh_upstream` remains disabled.

## 2026-09-15 - Prioritize live communication workflows (0.42.1)

- Consume durable Teams notifications in the notification service independently of serial
  fallback polling, with processor health and queue-lag visibility.
- Pace recovery continuations behind foreground events while reserving bounded progress for both
  urgent lease renewal and ordinary recovery every five seconds.
- Commit terminal gate records and their successor events atomically, then continue short FIFO
  gate chains within a bounded foreground burst.
- Reuse worker-verified file and reference-repository preparation receipts while invalidating
  them on guest changes, configuration changes, refreshes, or session deletion; verify requested
  file content and modes before recording receipts, and inspect both cached worktrees and staged
  state with a persistent host-owned Git index so guest-defined filters cannot run on the worker
  host.
- Stop copying dynamic Team state into core session metadata and remove its mirror-due recovery
  projection; authoritative wake context and permissions continue to come from Team state, while
  stable guidance and repository-preparation receipts remain session-local.

## 2026-09-15 - General case context (0.42.0)

- Evolve Team notes into an attributed, linked case journal with decision rationale,
  append-only supersession, scoped retrieval and conservative legacy backfill.
- Assemble case requirements, captured sources, plans, decisions, delivery, reviews,
  corrections and verification through configurable, revisioned context views.
- Add read-only workflow queries, immutable context snapshots and exact-session grants
  for detached consumers, plus bounded host-side ADO discussion capture.
- Supply bounded baseline briefs on fresh wakes and record-level changes on retained
  wakes, using completed dispatches on the same native thread as the comparison baseline.
- Add an operator context panel with recipe preview/version history and snapshot browsing.
  Keep note-taking advisory, existing completion gates unchanged and Team Memory separate.
- Preserve resolved/closed case evidence in review wakes and freeze the displayed saved
  recipe version when browsing history.
- Enforce release-directory scope before collecting associated review/verification evidence,
  and include contributed child task history in contexts captured before a release exists.
- Document the staged App integration; deployment and live workflow edits remain separate.

## 2026-09-15 - Lower orchestration handoff latency (0.41.1)

- Drain ready effect completions through the durable event queue between bounded event slices;
  schedule recovery separately, with cooperative candidate budgets and lease priority.
- Read revision-consistent Team lifecycle candidate pages and indexed prompt receipts instead of
  reconstructing full Team state and terminal execution history on routine no-op passes.
- Record provider, executor-queue, reaping and completion-publication timing separately, and log
  per-hook recovery work, continuations and budget overruns without message contents.
- Preserve transactional lifecycle checks, queue claims and receipt-before-finalization ordering;
  add scheduler, history-load, migration and latency regression coverage.
- Keep queue recovery moving past failed VM inventory pages and retry them on the next pass;
  share one lazily loaded VM inventory per waiting-reconciliation slice.

## 2026-09-14 - Scheduled Freshservice reporting (0.41.0)

- Add a bundled, opt-in Freshservice add-on with a read-only connector driver for exact-subject
  ticket discovery, ticket and conversation reads, and bounded attachment staging with exact-host
  and file-type policy.
- Evaluate workflow cron triggers in an optional IANA timezone, preserving local morning schedules
  across daylight-saving changes while recording both UTC and local scheduled times.
- Provide a complete Freshservice connector example and operator guidance for the scheduled report
  workflow.
- Restore full-suite coverage for current repository indexing, worker assignment, Team migration,
  Admin catalog, and generated-manual contracts.

## 2026-09-14 - Reliable Azure PR feedback history (0.40.5)

- Resolve Azure DevOps short and scoped repository names through the authoritative PR map so an
  allowlisted agent can read the complete comment history regardless of which form polling stored.
- Share session read cursors across unambiguous repository aliases, preserving existing workflow
  payloads without replaying older feedback after the compatibility fix.

## 2026-09-12 - Index-worker cleanup and in-place Team task-board selection (0.40.4)

- Keep Team task-board Team, project, and case selection in the existing embedded dashboard
  while silently updating its deep link, avoiding a full dashboard reload for each change.
- Retire disposable indexing sessions, containers, workspaces and session-scoped Git access
  after artifact import or a terminal failure/cancellation, while retaining the imported index
  and its Git snapshot pin.
- Retry incomplete terminal cleanup during reconciliation and keep uncertain compiler jobs
  reserved until their shutdown is confirmed.

## 2026-09-11 - Azure runner package mirror (0.40.3)

- Route the shared Ubuntu runner base image's release, update, backport, and security package
  downloads through `azure.archive.ubuntu.com` to avoid the degraded public archive path from
  Azure-hosted deployment agents.

## 2026-09-11 - Reliable runner image builds (0.40.2)

- Install the common Codex and Claude runner operating-system dependencies once in a shared base
  image, avoiding a second full apt install in sequential archive builds.
- Bound Ubuntu base-image pulls and shared-base builds, retry stalled pulls, and allow operators to
  select a registry mirror or cache with `RUNNER_OS_BASE_IMAGE`.

## 2026-09-11 - Faster repeat repository indexes (0.40.1)

- Index the App .NET workspace through its tracked solution instead of starting a
  separate restore and Roslyn process for every project.
- Drain up to 16,384 cached semantic vectors per reconciliation pass while retaining
  the existing 512-chunk bound for new embeddings and the 30-second time budget.

## 2026-09-11 - Opt-in SCIP symbol navigation (0.40.0)

- Make SCIP symbol navigation opt-in per repository with an Admin Web setting,
  defaulting to off for new and existing configurations that omit `scip`.
- Skip compiler tools and NuGet bootstrap when SCIP is disabled, and report
  successful text/semantic-only snapshots as ready with navigation disabled.
- Run SCIP's Roslyn semantic analysis directly after dependency restore, removing
  the separate `dotnet build` step from C# repository indexing.
- Preserve partial navigation for workspace failures and text retrieval when
  dependency restore or SCIP execution fails.

## 2026-09-11 - Session copy image selectors (0.39.1)

- Use stored VM template and container image aliases when provisioning copied
  sessions, allowing session and Team copies to use configured images.
- Cover default and custom images through the session copy provisioning path.

## 2026-09-11 - Copy Team session configurations (0.39.0)

- Default Team copying to automatic provisioning of fresh sessions with the
  source model, capabilities, tool approvals, access grants, and limits.
- Preserve configured images and recorded toolchain/skill settings while creating
  fresh session identities, authentication tokens, and lifetime windows.
- Add source-directory-authorized session copy operations, progress reporting,
  and reuse of completed session selections when retrying a Team copy.

## 2026-09-11 - Case progress task boards (0.38.0)

- Add case filters, source links and shareable task-board permalinks, including
  completed work and follow-up/correction lineage while preserving normal access checks.
- Inherit task case membership across operator, workflow and agent task creation,
  including out-of-order batches; preserve explicitly selected correction cases.
- Add atomic source-based reuse to the create-case workflow action.
- Add an optional revision-checked App backfill helper for linking existing work
  to intake cases without posting comments or dispatching work.
- Preserve Team/project selection through embedded-board navigation; cache shared
  case-less lineage and retain valid agent-action creations when other plans have
  invalid task graphs. Cover case permalinks through local and Microsoft sign-in.
- Load task boards from their selected Team/project/case scope and keep cost
  aggregation off the task-board path, avoiding full-state transfers and rerenders.

## 2026-09-10 - Reliable .NET repository navigation (0.37.2)

- Disable SourceLink metadata generation inside isolated indexing subprocesses so
  managed-Git transport URLs cannot break an otherwise valid project build.
- Suppress the .NET 10 `NU1510` package-pruning advisory during SCIP analysis so
  successful workspace loads are not conservatively reported as partial coverage.
- Preserve partial navigation status for genuine compilation failures.

## 2026-09-10 - Repository indexing runtime wiring (0.37.1)

- Supply the standard admin provider adapter when preparing indexing sessions so
  managed Git cloning and repository bindings work in the deployed runtime.
- Exercise real session creation and clone providers together in indexing coverage.

## 2026-09-10 - Copy Team configurations (0.37.0)

- Add a revision-checked Team copy operation and Teams-view wizard that clones
  the current organization configuration while assigning a distinct registered
  core session to every copied persistent agent.
- Copy active and blocked projects, active and paused agents, current roles,
  review profiles, outcome kinds, release policy, goals, project assignments,
  budget settings, and managed Git bindings under collision-safe new IDs.
- Reset copied agents and projects to active, goals to pending, and budget
  history to a fresh window. Disable governance escalation because governance
  membership is not copied. Exclude operational work and historical records,
  completed projects, project-only agents, and archived configuration.
- Require directory-owner permission for every destination session, preserve
  the copy-preview revision through submission, and hide copying for archived
  Teams.
- Preserve the concrete indexed ancestor used by active Team work during
  repository-index retention.

## 2026-09-10 - Portable repository indexing (0.36.0)

- Add opt-in managed repository indexes with text and semantic search, C#/TypeScript
  symbol navigation, assigned-commit Team scope and incremental embedding reuse.
- Let tasks and reviewers reuse unchanged files from a proven indexed ancestor with
  explicit source/target provenance, without queuing per-task compiler builds. Update
  Team guidance to version 43 for baseline coverage and local-change verification.
- Bound automatic branch indexing to a one-hour default interval and one CPU, with
  one compiler indexing job across the runtime; skip intermediate branch heads.
- Build compiler indexes in isolated container workers, verify imported Git blobs,
  report partial coverage, and reconcile jobs and retention through workflow nodes.
- Bootstrap relay-aware NuGet, honor npm workspace root lockfiles, reserve compiler
  capacity at submission, and serialize cancellation with reconciliation. Retain
  failed worker cleanup for retry and reclaim cancelled, unprovisioned snapshots.
- Resolve approved image aliases before compiler submission, revoke session Git access
  and bindings on retirement, and allow the default packages-directory exclusion to be
  removed for tracked npm workspace source.
- Add revision-checked Admin Web controls, gateway tools and operator CLI commands.
- Reconcile Team indexing tool grants with dispatch scope, removing stale grants
  when project authorization is lost and avoiding duplicates on repeated wakes.
- Export versioned SQLite/SCIP bundles with a standalone Python query tool and optional
  embeddings for external coding workflows. Include a pinned worker image build,
  App profile, retrieval benchmark and operational documentation.
- Allow release cancellation to retire its reviews while retaining the frozen
  reviewer membership and recorded decisions as history. Continue validating the
  required reviewer slots and excluding superseded review attempts.
- Cover pending, blocked, accepted and retried reviews, persisted cancellation,
  and rejection of incomplete cohorts before and after cancellation.

## 2026-09-10 - Workflow-managed release publication (0.35.0)

- Add a project release-policy switch that can accept an approved Git release
  without publishing its generated release branch to the canonical remote.
- Emit the durable `team.release.approved` event after all frozen gates pass,
  independent of canonical publication, while retaining `team.release.published`
  exclusively for completed canonical pushes.
- Bind approval events and their Azure publication identity to the candidate's
  frozen managed-repository scope rather than mutable project Git settings.
- Add Admin Web controls for choosing automatic or workflow-managed branch
  publication.

## 2026-09-10 - Restrict website credential environment access (0.34.2)

- Require explicit host-controlled grants before managed website profiles can resolve environment
  variables. Bind each grant to its directory subtree, profile, request fields, browser origin,
  and login URL, and load the same grant configuration in Admin Web and the HTTP target relay.

## 2026-09-10 - Direct Team wake reads (0.34.1)

- Start ordinary and mutation-enabled chat work from the supplied wake context,
  fetching missing details directly through record-specific tools. Reserve dispatch
  summary reads for recovery or a specific refresh instead of routinely repeating
  the working set already delivered in the wake.
- Update managed Team guidance to version 41 and align the manual and pilot runbook.
  Preserve full specification, review, evidence, and overflow retrieval requirements.

## 2026-09-09 - Terminal Team release cancellation (0.34.0)

- Add an audited Cancel release action for directory owners in release details and
  human review, with an optional reason and revision-checked API.
- Close outstanding reviews, verification runs, release blockers and exclusive
  correction work without creating rework or an automatic successor. Preserve
  candidates, decisions, findings and completed or shared work as history.
- Ignore late gate and promotion results for cancelled releases. Refuse cancellation
  once canonical publication has been queued or attempted, or delivery has completed.
- Persist cancellation audit data with additive Team SQLite migration 77.

## 2026-09-09 - Bounded Team thread reconciliation (0.33.1)

- Reconcile at most 32 eligible native-thread receipts in one state transaction per
  maintenance pass, prioritizing active dispatches and current lineage owners so
  historical backfills cannot monopolize workflow dispatch.
- Skip unrecordable and already handled history before opening a transaction;
  retain timestamp and ownership checks under the write lock. Resume backfills
  from durable receipts after restart and defer completion until its receipt is saved.
- Capture the finalizing dispatch's durable receipt in the same transaction as
  lease release, including independent completion workflows and missing-thread
  failures, so batch boundaries cannot lose same-lineage resume context.

## 2026-09-09 - Configurable Team release gates (0.33.0)

- Let projects enable or disable automated verification, agent review, and human review
  independently and arrange the enabled stages in any order. Freeze that order with each
  release candidate and preserve disabled-stage settings for later reuse.
- Allow an explicit policy with every stage disabled to publish without gates. Preserve the
  historical verification, agent-review, human-review order when loading older policies.
- Require a directory owner to record an audited, candidate-bound waiver when a correction
  successor omits the gate that rejected its predecessor.
- Honor the frozen human-gate setting and applicable waivers when recording publication,
  persist waiver audit records across state updates, and resolve current reviewer eligibility
  when a waiver advances into human review.

## 2026-09-09 - Native Team thread resumption (0.32.1)

- Keep asynchronous dispatch and execution identities separate from observed provider
  thread IDs. Capture native threads from worker metadata or bounded execution-log
  searches, including queued prompts and verbose runs.
- Persist native receipts against each Team dispatch and bind only its own work lineage.
  Guard delayed completions, newer dispatches, session changes, and policy resets.
- Treat old synthetic lineage bindings as unbound so recovery uses current durable Team
  context. Add additive persistence migration and recovery/rollback guidance.
- Skip handled or superseded missing-thread receipts before opening a state transaction.
  Preserve actionable session-health fields in wake overflow without an unusable read reference.

## 2026-09-09 - Focused Team contexts (0.32.0)

- Make every ordinary Team wake independently actionable with current identity,
  instructions, immutable scope, urgent operator directives, and retrieval references
  for bounded detail. Share exact Git bindings and review snapshots within each payload
  while preserving evidence versions, release candidates, and approval SHAs.
- Add opt-in dispatch summary reads with complete scope IDs and a scoped individual
  source-evidence read. Preserve the default full-read response and existing controls.
- Update managed Team guidance to version 40 with focused reads and bounded shell
  output, and move repeated review and management procedures into that guidance.
- Supply managers with current supervisory counts without widening dispatch authority.
  Exercise the existing work-lineage policy locally; leave operator policies unchanged.
- Include synthetic offline comparisons that count additional retrieval and guidance
  overhead, plus a deployment, pilot-measurement, and rollback runbook.

## 2026-09-09 - Task board column layout (0.31.1)

- Hide the Done column until Show completed is enabled, giving active tasks more room
  while retaining access to completed history and pagination.
- Adapt the number of columns to the available space and wrap long task titles, briefs,
  links, and worktree paths inside their cards.

## 2026-09-09 - Managed website sessions (0.31.0)

- Add generic, directory-scoped JSON login profiles with host-held credential references
  and explicit authentication grants on session HTTP targets.
- Renew temporary browser storage on demand through the existing HTTP target relay,
  checking session proof, current grants, expiry, and public HTTPS destinations.
- Extend the seeded Playwright helper to create authenticated browser contexts without
  placing passwords or persistent browser state in guest files.
- Add Admin Web website profile editing, encrypted host credential entry and rotation,
  scoped assistant grants, and sanitized login tests/status. Preserve authentication
  bindings when editing existing HTTP targets and use revision checks for profile changes.
- Resolve profiles from current saved layers so root deletion revokes cached access;
  replace inherited profiles completely when editing credential or storage mappings.
- Enforce one cancellable login deadline across DNS, connection and response streaming,
  while allowing unrelated website sessions to proceed independently.
- Preserve HTTP target serialization for unbound targets, and allow worker-default
  changes to reuse saved assignments after historical job directories are deleted.

## 2026-09-09 - Durable container worker assignments (0.30.0)

- Keep existing container agents on their assigned worker when the directory default
  changes, while new agents start on the new default. Persist assignments before
  provisioning, including worker authentication references, relay and Git settings,
  images, and database endpoints; retain assignments through retries and cleanup.
- Route resumed work, execution inspection, logs, files, Git, and cleanup to the
  session's saved connection settings, including when agents on the same worker have
  different defaults, without falling back to another host when it is unavailable.
- Pin legacy agents before Admin configuration changes and add
  `execution pin-worker-assignments` with a preview and explicit apply mode for upgrades.
  Include jobs that have execution records without session manifests.
  Refuse a cutover when an existing worker's connection settings cannot be recovered.
- Add directory-scoped `container_workers` choices and a `worker_id` override for
  workflow and manual agent creation, including Admin Web, Team, and the container-job
  CLI. Load images and subscription profiles from the selected worker, and reject attempts to move an
  existing session through a creation override.
- Report inherited Codex authentication from the saved worker assignment so billing
  mode and policy editing stay consistent after worker defaults change.

## 2026-09-08 - Release PR work-item links (0.29.0)

- Capture originating ADO work-item IDs with the immutable release publication context and
  link them to newly created or reused pull requests, including nested release contributions.
- Preserve personal creator policy, avoid duplicate links on retries, and report which items
  linked successfully when Azure DevOps rejects an association. Legacy publication contexts
  retain their existing attribution without inferring missing work-item IDs.

## 2026-09-08 - Release history visibility (0.28.1)

- Keep recent accepted and rejected releases in the history window, with active releases
  retained separately so an older backlog cannot hide newly published releases.
- Show the loaded and total release counts when history is partial, and clarify that state
  and activity-date filters apply to the loaded releases.

## 2026-09-08 - Required personal PR creators (0.28.0)

- Add an optional `require_personal` policy to PR publication. Require captured ADO assignee
  attribution and a usable personal connection before pushing; reject an existing PR owned
  by another identity and verify the creator returned by ADO before reporting success.
- Keep same-account OAuth-to-PAT fallback and duplicate-creation guards while preventing bot
  fallback under this policy. Record explicit reasons for missing context, creator mismatches,
  and bot reconciliation when fallback is allowed.
- Let the App attribution migration enable the policy on both PR paths after deployment.
  Use configured pack references, create valid intake tasks, and preserve existing validation
  warnings during repair.

## 2026-09-08 - Operator verification recovery and live logs (0.27.0)

- Let operators retry candidate-failed release verification from Admin Web with a comment.
  Preserve the failed attempt and pinned candidate, retire unused generated correction work,
  and retain all release gates; refuse retries that conflict with a successor or active work.
- Reject verification retries when a cancelled correction task links to a replacement task,
  including replacements with preparing or leased dispatches.
- Open live release-verification logs at their newest output, continue following while the viewer
  remains near the bottom, and preserve manual log and page scrolling across live updates.

## 2026-09-07 - Team, ADO and runtime improvements (0.26.0)

- Explain Bokio validation failures with upstream field details and retain structured audit data.
- Show partial journal-entry outcomes, attachment counts, and the scope of evidence retries.
- Replace host-wide completion polling with events from the prompt effect or wake monitor;
  route ephemeral cleanup from prompt completion and preserve legacy transform names as no-ops.
- Deliver those events to existing completion-trigger nodes within their configured scope,
  including reconciled runs, with durable duplicate suppression across retries and restarts.
- Add the permission-gated `update_ado_backlog_state` MCP tool for updating a work item's
  state through the host-owned ADO connection, with an optional atomic expected-state guard.
- Expose the tool in Admin Web permission controls; existing sessions require an explicit grant.

- Give accepted dependency merge recoveries distinct, retry-stable Git branches and worktrees,
  preserving checkouts left by failed preparation; reject preparation reuse with a changed path.
- Preserve accepted dependency recovery provenance across role claims and releases before execution.
- Retain accepted recovery worktrees across task execution transitions while rejecting changed
  provenance; cover fresh preparation after accepted child inputs become an explicit contract.
- Route reassigned child blockers through the new owner's superior when their parent is unclaimed.
- Queue new role-based tasks until their dependencies are ready and an eligible agent has capacity.
- Reserve one new role task per agent atomically with its dispatch, preserving ownership after execution starts.
- Route child acceptance to the producer's superior while a parent is queued,
  retain uncertain delivery claims across retries, and select peers whose wake interval is due.
- Show role targets and queue reasons in Team task views; retain all existing concrete-owner assignments.
- Update runtime regression expectations for supervision scope validation and release publication context.
- Include bounded conversation history and reply links when operator follow-ups interrupt ongoing work.
- Reject unlinked notifications to an operator awaiting an active Team Chat reply, returning the
  correct reply tool and message IDs before posting.
- Expose description and acceptance criteria in ADO queries, detail reads, and batch summaries,
  preserving raw fields and supplying empty strings for absent optional content.
- Keep matching-work-item trigger content bindings valid when descriptions or criteria are absent.

## 2026-09-07 - ADO Markdown image access (0.25.2)

- Recognize Markdown images in Azure DevOps work-item fields and comments, including
  reference-style images and mixed HTML, while ignoring Markdown code examples.
- Explain image-retrieval permissions in work-item responses and the existing host-download,
  workspace-copy and local-view steps in tool guidance. File reception remains opt-in.

## 2026-09-07 - Personal ADO connection feedback (0.25.1)

- Clarified that the Azure DevOps organization is required for both PAT and Microsoft OAuth
  connections, with an organization URL example and password-manager autofill hints.
- Reject invalid organization values before submitting credentials and show prominent,
  focused error messages and explicit PAT save confirmation.

## 2026-09-05 - ADO assignee attribution (0.25.0)

- Added personal Azure DevOps PAT and Microsoft Entra OAuth connections for signed-in Admin Web
  users, with encrypted host-only storage, verified ADO identity, and independent disconnect controls.
- Added browser-bound OAuth consent and automatic token refresh; publication prefers OAuth,
  then a saved PAT for the same account, then the bot, without duplicating uncertain PR creation.
- Captured work-item assignees on task intake and preserved their attribution through task
  handoffs, corrections, and nested release contributions.
- Created release PRs through the assignee's personal connection when available; mixed-owner
  releases and unavailable or rejected PATs use the bot, with recorded attribution and fallback reasons.
- Added commit-bound publication contexts and durable reconciliation for uncertain PR creation.

## 2026-09-05 - Per-task Git authors (0.24.0)

- Added optional task `authored_by` name/email settings through the operator UI, API, Team tools,
  and task-creation workflows, with the project author as fallback.
- Copied authors into child, follow-up, correction, and recovery tasks; mixed-author aggregate
  corrections use the project default.
- Applied authors independently to task worktrees on both execution backends, including retained
  checkouts, without rewriting existing commits or changing the project committer.

## 2026-09-04 - Mobile Team Chat

- Reworked Team Chat into a mobile conversation-list and message-thread flow so agent messages,
  thread actions, and the composer retain a usable full-height viewport on phones.
- Replaced the multi-row Team navigation wall with a compact horizontal tab strip on small screens.
- Kept embedded add-on frames tall enough to remain usable when the Admin shell switches to its
  document-scrolling mobile layout.

## 2026-09-04 - Named Workflow Validation

- Added a generic `gate:validate` primitive that evaluates all ordered named predicates and routes
  through explicit `valid` or `invalid` ports.
- Preserved upstream outputs while emitting deterministic `valid`, `failure_count`, `failed_codes`,
  and structured `failures` diagnostics for downstream correction workflows.
- Added workflow editor metadata, output-schema inference, graph-port validation, documentation,
  and focused runtime and configuration coverage.

## 2026-09-04 - Retry-Safe ADO Work-Item Comments

- Added workflow-configurable Azure DevOps mentions to the work-item comment effect.
- Added optional target-scoped idempotency keys, non-rendered remote correlation markers, and
  read-before-retry reconciliation so ambiguous provider success does not duplicate a comment.
- Preserved resolved mention identities in outputs reconstructed after an ambiguous remote success.
- Rejected same-target idempotency-key reuse when the comment or mention set differs.

## 2026-09-04 - Durable Codex Subscription Egress

- Added a control-plane-managed, source- and destination-restricted HTTPS CONNECT proxy so
  private container workers can perform Codex device login without receiving general internet
  access.
- Wired the control-plane proxy, worker URL, Docker egress allowlist, Azure NSG port, deployment
  verification, and operator documentation into the supported Ansible deployment path.
- Corrected DNS-worker client CIDRs, proxy-derived firewall endpoints, implicit HTTP port probing,
  and the documented proxy allowlist environment names.
- Preserved actionable Codex App Server failures as sanitized worker gateway errors instead of
  leaking through as opaque HTTP 500 responses.

## 2026-09-03 - Immediate Operator Directive Wakes

- Made operator directives from the Admin API, workflow effects, and governance MCP commands
  atomically record a debounced Team wake alongside the inbox item.
- Added immediate post-commit wake publication with outbox acknowledgement, diagnostics, and
  periodic recovery when publication is unavailable.

## 2026-09-03 - Workflow Pull-Request Feedback Replies

- Added a generic workflow effect that posts a host-owned, idempotent reply to a GitHub or Azure
  DevOps pull-request feedback comment without waking an agent or using agent MCP permissions.
- Routed GitHub review comments, issue comments, and whole-review feedback through valid reply
  surfaces, preserved trigger-selected Azure DevOps targets, suppressed effect-authored feedback,
  and made reply idempotency process-safe and target-scoped.

## 2026-09-03 - Team Release and Directive Reliability

- Added an Agent Mail composer for authenticated operators to create durable, manager-routed Team
  directives with optional project context, reliable filter behavior, and reset-safe Team and
  project selection.
- Kept terminal-task directives as inbox-only work without stale preparation blockers, while
  revalidating the complete project and managed-repository scope before prepared dispatches
  activate, including when referenced tasks reopen.
- Preserved targeted Team wake liveness when optimistic lease planning exhausts its state-revision
  retries by recording success only after a durable delayed successor wake exists.
- Derived release and promotion bases for dependency-composed deliverables from their
  server-verified composition root instead of the already-composed worktree commit, preventing
  valid canonical publication from being rejected as a destination-base mismatch.
- Prevented Team agents, including managers and project leads, from overriding a release
  package's canonical branch. Agent requests now derive it from project Git policy, while
  authenticated operator release creation retains an explicit override for exceptional cases.
- Reconciled nonterminal root and configuration-retry releases created with the old
  dependency-composed checkout base from their immutable composition manifests, retiring stale
  canonical attempts and durably queueing a repository-pinned corrected publication attempt while
  preserving succeeded promotion history.
- Completed expired claimed outbox intents for promotions retired by migration so obsolete
  publications cannot be reclaimed and replayed.
- Preserved candidate-correction-specific rejected-candidate diagnostics when validating
  inconsistent composition evidence.

## 2026-09-02 - Named Codex Subscription Profiles

- Added worker-owned named ChatGPT subscription profiles with independent device-login homes,
  immutable account pinning, safe account/rate-limit/usage status, compatibility aliases for the
  legacy `default` profile, and root-owner Admin management.
- Added immutable profile selection to container Codex agent creation and workflow nodes while
  preserving existing profile-less subscription sessions as `default`.
- Added subscription-capacity preflight and mid-turn deferral handling with durable, reset-aware,
  per-profile FIFO prompt replay, cross-profile concurrency, visible queue reasons, manual
  cancellation, and no metered API fallback.
- Hardened deferred prompt identity, worker-local profile recovery, replay cancellation conflicts,
  queued-session cancellation authorization, refresh-time account pinning, selector telemetry,
  context-retry deferral, preflight queue FIFO, and workflow validation for named subscription
  selections.

## 2026-09-02 - Archived Team Agent Session Restoration

- Added a workflow-only `effect:team:restore-agent` primitive that atomically restores a fully
  archived logical Team agent against a distinct, live, in-scope core session while recreating
  its role, project, and reporting edges, preserving operator-owned delegation and context policy,
  and resetting transient runtime/context lineage.
- Enforced old-session, active retained-Team, session-uniqueness, expiry, directory-scope, and Team
  state revision guards with transactionally idempotent exact-binding retries and structured
  workflow failure results.

## 2026-09-02 - Team Work-Lineage Context Routing

- Added a per-agent `work_lineage` context policy that gives each independent task family its own
  resumable model thread, carries the lineage through successor and release-correction work, and
  dispatches only one lineage at a time while leaving unrelated work queued.
- Captured exact Team Chat source-message anchors when Chat creates root tasks, added an
  agent-owned paginated conversation-history tool for later recall, and kept taskless wakes fresh.
- Updated Admin Web context controls and observability so lineage-routed agents do not show a
  misleading global reset age or token count, and disabled manual clearing for policies that
  already determine context per wake.
- Preserved single-lineage routing through pending manual resets and recovered recreated agents
  from stale lineage thread bindings by retrying the affected wake with a fresh context.

## 2026-09-02 - Deployment Service Reconciliation Reliability

- Deferred systemd service changes until automatic apt maintenance is idle, consolidated the
  manager reload, and retried transient service-manager calls so a concurrent package-triggered
  systemd re-exec cannot falsely fail an otherwise successful control-plane deployment.
- Updated the Azure CLI repository template to use namespaced Ansible facts for compatibility with
  newer ansible-core releases.

## 2026-09-02 - Portable Team Release-verifier CPU Limits

- Stopped applying a fixed eight-CPU quota when a Team release policy omits `cpus`, allowing
  disposable verification jobs to use the worker's available CPU capacity while retaining the
  elevated 2,048 process/thread safeguard. Explicit project CPU, memory, and PID limits continue
  to override the defaults for new verification sessions and infrastructure retries.

## 2026-09-02 - Agent Pull Request Metadata Updates

- Added the policy-controlled `update_pull_request` MCP tool for Azure DevOps title, description,
  status, and tag updates using directory-scoped host credentials, optional current-status guards,
  normalized provider aliases, audited results, and approval-policy support.

## 2026-09-02 - Dispatch-Scoped Team Wake Context

- Limited full case detail in Team wake prompts to cases structurally linked to the immutable
  dispatch while retaining a bounded five-field case index for fresh management and Chat wakes.
- Replaced non-actionable per-agent session rows with a compact health summary while preserving
  explicit missing, expired, unhealthy, failed, stalled, stopped, and exited rows.

## 2026-08-31 - Durable Team Preparation Recovery

- Persisted deterministic Team Git preparation attempts and host-owned container-worker receipts
  so an exact pristine checkout can be resumed after a state conflict without trusting guest Git
  configuration; invalidated, resolved-task, and reassigned attempts now enter audited worktree
  cleanup. Successful physical removal retires receipts and attempts across the cleaned worktree
  lineage without equating the prepared and terminal HEADs, operator retries restore invalidated
  cleanup eligibility, interrupted pending attempts can clean against their worker-owned receipt,
  reused deterministic IDs reactivate prior terminal candidates, and cleanup remains compatible
  with workers deployed before receipt IDs were supported.
- Made operator resolution of an explicit preparation blocker enqueue the affected actionable task
  owner through the existing durable wake outbox while suppressing wakes when another gate remains.
- Detect coherent-but-stale Team service registrations and fail every systemd Team wake, including
  governance-manager wakes, before state access or leasing until the grouped runtime restart loads
  the current checkout.

## 2026-08-31 - Session-Gated Image Generation

- Added an opt-in session policy and workflow effect for OpenAI image generation and editing,
  with direct Images API and Responses tool enforcement across HTTP and WebSocket relay paths.
- Added bounded JSON and multipart request handling, host-owned authentication, and usage auditing
  that records hashes and sizes without retaining prompts, uploads, or generated image data.
- Enabled the capability only in the App demo and demo-modification workflows while keeping all
  other sessions disabled by default, and documented the guest image-generation handoff.

## 2026-08-31 - On-Demand Managed Reference Repositories

- Added a session-bound `prepare_reference_repository` MCP tool that lets container agents
  materialize only policy-allowlisted managed repositories when needed.
- Reused the verified-refresh reference checkout path so newly provisioned access stays read-only,
  refreshes through the host-owned mirror, reconciles repeat calls, and keeps upstream credentials
  on the control plane.
- Preserved an existing ordinary checkout and its writable grant as an in-place no-op instead of
  converting it into a detached read-only reference checkout.

## 2026-08-31 - Exclusive Orchestration Retention

- Serialized daily orchestration retention across host processes so concurrent repository startup
  cannot pin a growing SQLite WAL and exhaust the control-plane filesystem.
- Kept failed or interrupted maintenance retryable by releasing the process lock automatically and
  recording the completed retention date only after all bounded batches finish.

## 2026-08-31 - Resumable App Demo Modifications

- Added an operator workflow example for direct post-deployment App demo changes that reuses the
  deterministic demo agent, refreshes its session for eight hours, and queues the request on the
  existing Codex thread without starting another Azure DevOps deployment.

## 2026-08-30 - Alexa Agent Bridge Example

- Added a signed, deduplicated Alexa webhook workflow example that dispatches bounded personal
  questions to one operator-pinned agent session.
- Added a host-authenticated, write-only reply connector example plus setup, trust-boundary, and
  end-to-end verification guidance for the external Alexa mailbox service.

## 2026-08-30 - Browser Proxy And Team Review Hardening

- Bound browser proxy HTTP and TLS application authorities to the session-authorized target,
  rejected excess connections without queueing sockets, and added a short unauthenticated
  header and TLS ClientHello deadline.
- Kept cancelled Team dependencies out of policy-v2 composition manifest requirements and
  missing-manifest reconciliation, matching preparation and acceptance assessment.
- Scoped Planning and Task board inbox payloads to the latest preparation invalidation per task
  and indexed those records once for status rendering.

## 2026-08-30 - Bounded Elasticsearch Demo Seeding

- Added an allowlist-gated, size- and document-bounded Elasticsearch bulk-index tool and workflow
  effect with deterministic document IDs for idempotent synthetic demo-history seeding.
- Extended the App demo operating guidance to populate three weeks of production-metrics history
  before creating and verifying Kibana dashboards.

## 2026-08-30 - Session-scoped Browser Target Access

- Added an authenticated forward proxy that lets seeded browsers use the real origins of a
  session's configured HTTP targets without weakening VM or container default-deny egress.
- Wired the browser proxy into both execution backends and updated App demo workflows to browse
  the public UI origin while retaining the path-based relay for programmatic API traffic.

## 2026-08-30 - Team Preparation Generation Safety

- Persisted a semantic preparation generation on Team Git work contexts and scoped prepared
  branches and worktrees to it, preventing stale checkout metadata from crossing task edits.
- Invalidated active Git preparation after relevant task edits, queued fresh preparation, and
  exposed queued, leased, and review-dispatch status in Planning and Task board.
- Rejected Git-backed policy-v2 integration submissions without the current server composition
  manifest before persistence, and added idempotent recovery for retained malformed candidates.
- Allowed safe cleanup of invalidated unresolved-task worktrees after active dispatches finish
  while preserving submitted Git evidence and normal dirty-worktree safeguards.

## 2026-08-29 - Elastic Stack 9 Compatibility

- Normalized both detailed and redacted Kibana status responses so the bounded Elastic Stack
  tools remain useful with Kibana 9 while preserving exact version and readiness details when the
  authenticated deployment exposes them.
- Documented the safe loopback-only detailed-status configuration validated with matching
  Elasticsearch and Kibana 9.5.2 containers.

## 2026-08-29 - Task Board Task Editing

- Added the existing task Edit action to Task Board cards so operators can update a task without
  navigating to the separate Planning view.

## 2026-08-28 - Elastic Stack Proxy Compatibility

- Requested identity-encoded Elastic Stack API responses so bounded streamed reads remain
  compatible with reverse proxies that advertise already-decoded compressed responses.

## 2026-08-28 - Elastic Stack Add-on

- Added a bundled, opt-in Elastic Stack add-on with narrowly scoped Elasticsearch read tools and
  Kibana data-view, saved-object import, and default-data-view operations for agents and workflows.
- Added directory-scoped static targets plus session metadata targets so demo deployments can
  expose derived Kibana and Elasticsearch URLs without storing credentials in session state.
- Kept authentication host-side through named basic, API-key, and bearer environment profiles,
  with an explicit origin allowlist checked before any secret is resolved or sent.
- Added bounded response and NDJSON import validation, add-on MCP catalog metadata, workflow
  editor catalog entries, focused tests, and operator configuration guidance.

## 2026-08-28 - Role-Based Team Task Allocation

- Added single-task Team MCP commands that resolve an exact base or project-assignment role to a
  concrete active project member using a fixed, work-aware least-loaded policy.
- Preserved existing assignment authority and reassignment cleanup, failed atomically when no
  candidate is eligible, and returned the selected owner and candidate scores for audit and
  idempotent replay.

## 2026-08-27 - External Team Chat Attachments And Shared Teams Subscriptions

- Added an opt-in `import_attachments` bridge setting for Teams, Discord, and email messages that
  validates provider files with the effective Team Chat attachment policy, copies accepted bytes
  into private Team storage, and preserves external-source provenance.
- Delivered valid files when other imports fail, while surfacing bounded failure details in Team
  prompts and the operator UI so agents cannot silently claim to have inspected missing content.
- Resolved email attachment mailboxes from each target's authentication profile and reduced
  URL-bearing provider failures to credential-free error classes before persistence.
- Reconciled Graph change-notification subscriptions by credential identity, tenant, and target
  across directory scopes, converging legacy duplicates onto one shared subscription and retrying
  failed duplicate cleanup with bounded backoff.
- Routed shared-subscription callbacks through an active consumer directory when the stored
  representative is no longer active, preserving delayed exact-message delivery.
- Mirrored sanitized Teams and Discord ingress envelopes into their attachment adapter stores,
  resolved imports with the source workflow directory credentials, and preserved Discord thread
  channels for attachment listing and download.
- Enforced Team Chat attachment byte caps while streaming Teams and Discord downloads, selected
  callback consumers only when their target and provider identity still match, and deleted fully
  orphaned shared subscriptions in one reconciliation pass.

## 2026-08-27 - Scoped Manager Fallback Supervision

- Froze displayed actionable subordinate tasks into dedicated manager-fallback supervision scope,
  allowing direct status requests and bounded linked-Case updates without transferring task
  ownership or granting generic authority over the subordinate task's project.
- Required the matching bounded fallback fingerprint during deferred dispatch activation so an
  unrelated subordinate task cannot be added to the frozen scope.

## 2026-08-27 - Teams Notification Wake Latency

- Signalled the Teams ingest worker immediately after a Microsoft Graph callback is durably queued,
  avoiding the next polling-sleep delay before exact-message retrieval.
- Preserved `202 Accepted` callback handling and periodic durable-queue draining when the wake
  socket is unavailable.

## 2026-08-27 - Incremental Team Cost Balances

- Replaced wake-time lifetime usage aggregation with persisted per-Team budget-window balances.
- Applied usage corrections transactionally as bounded request-group pricing deltas, including
  cached input reads and 5-minute or 1-hour cache-write token rates, while preserving relay and
  transcript deduplication.
- Serialized bounded reconciliation with incoming event deltas, and retained separately
  incremental lifetime Team balances where governance context requires pre-window spend.
- Limited cost-guard bootstrap, dirty-state, repricing, and periodic consolidation reads to the
  active budget window, with content-fingerprinted pricing snapshots preventing silent balance
  drift.

## 2026-08-26 - Teams Graph Change Notifications

- Added optional Microsoft Graph change notifications for authorized Teams channels, chats, and
  discovered direct chats, with owned-subscription renewal and lifecycle handling.
- Added a loopback-only durable notification listener and queue that validates tenant,
  subscription, and constant-time client state before exact-message ingestion.
- Kept two-second per-target polling as the disabled or degraded fallback, with 60-second
  reconciliation polling for healthy subscriptions and monotonic cursor advancement.
- Preserved out-of-order verified deliveries with deterministic trigger-event deduplication,
  resolved opaque-token tenants through concrete configuration or authenticated credential
  metadata, preserved existing refresh grants, and replaced subscriptions when callback URLs change.
- Added secret-free Admin and health status, IPv4/IPv6 listener verification, callback-host-aware
  Caddy routing, systemd and Ansible deployment wiring, and a CI/CD-owned Evander configuration while
  local deployments remain polling-only by default.

## 2026-08-26 - SQLite Events, Team Chat, And Release Reliability

- Stopped the host-observable agent-completion trigger from polling libvirt inventory on
  container-worker deployments; container completions remain event-driven.
- Migrated configured non-default legacy event stores before retiring their path settings, and
  allowed empty deployments to complete config-only SQLite cutovers.
- Rebound trigger and effect provider metadata, internal-event executors, and wake notifications
  whenever hot reload changes the orchestration database or trigger lock.
- Kept later release successors beyond the inherited deliverable frontier and chained
  configuration retries from the rejected successor instead of forking its predecessor.
- Kept idle Team Chat conversation streams on their revision-only cache path while reconciling
  mid-dispatch relay receipts against the already-cached Team state.
- Expired delivered completion envelopes now leave the durable trigger queue in bounded batches
  without creating duplicate execution-history entries.
- Aligned status, Team directory-scope, and packaged workflow-pack tests with their current
  runtime contracts, and made the concurrent internal-event test less timing-sensitive.
- Moved internal events, event-trigger subscription and delivery state, and scheduled internal
  events into the shared orchestration SQLite database with indexed future-only subscriptions,
  atomic scheduled publication, and retention that preserves pending work.
- Preserved pending internal-event deliveries when a configured trigger provider fails to
  initialize transiently, so reconciliation cannot mistake the trigger for removed configuration.
- Woke the trigger worker after durable event publication and scheduled-event creation so
  discovery-to-support handoffs no longer wait for the normal polling interval.
- Added a fail-closed, restartable offline migration that validates, imports, archives, and removes
  the retired JSON stores and config keys while preserving pending work for configured triggers.
- Closed shared orchestration SQLite connections at context exit so increased event activity does
  not accumulate database descriptors.
- Serialized Team agent lease planning per database outside SQLite, moved full-state validation
  and row-delta discovery before the writer lock, and committed with revision-guarded retries.
- Bound correction and remediation work to exact release-candidate provenance and automatically
  joined it to one persisted release lineage instead of allowing another initial package.
- Reused terminal packages for identical deliverables, enforced a unique successor frontier, and
  stopped verification or review while explicitly bound work remained unsettled.
- Added deterministic project-lead recovery tasks and typed lineage blockers for convergence
  failures, with conservative schema backfill that leaves ambiguous historical forks untouched.
- Queued text-only operator messages for relay injection on a busy Team agent's next model call,
  without interrupting its container or consuming the durable Team Chat fallback.
- Added source-linked operator replies and explicit acted-without-reply acknowledgements, with
  delivered-but-unhandled messages requeued when the active dispatch completes.
- Kept attachment messages on dedicated Team Chat wakes and made in-dispatch delivery visible as
  `Delivered` in the operator UI.
- Restricted in-dispatch delivery to authorization-compatible leases, requeued unhandled messages
  on abnormal lease termination, and kept external source batches on one return path.
- Retried only transient managed-Git publication transport failures with bounded exponential
  backoff, while reconciling the remote ref after ambiguous failures and preserving the exact
  source and destination lease checks.
- Stopped showing persistent `ERROR_ROUTED` badges on workflow canvas nodes after their configured
  error paths have handled the execution; routed-failure details remain available in run history
  and inspection views.

## 2026-08-25 - Teams Channel Poll Recovery

- Added deadline-aware retries for bounded Teams history reads so a full request timeout no longer
  consumes the separate retry-backoff allowance.
- Applied a short capped cooldown to transient Graph timeouts, transport failures, and `5xx`
  responses while preserving `Retry-After` throttling and longer authentication cooldowns.
- Scoped `Retry-After` deferral to ingest polling and stopped timed-out outbound writes from being
  replayed when Graph may already have accepted the message.

## 2026-08-25 - Managed Reference Repository Inputs

- Removed the legacy `repo_source` form from persistent-assistant reference repository inputs;
  workflow validation and execution now require registered `managed_repo_id` rows so the runtime
  and structured workflow editor use one canonical representation.

## 2026-08-25 - Governance Cost Threshold Authority

- Synchronized active Team budget-window caps when operators edit the Team cap, and made
  governance wake context expose the authoritative current cap, warning ratio, and computed
  warning threshold instead of inviting agents to infer policy from historical window metadata.

## 2026-08-25 - Release PR Scope Pinning

- Added a read-only managed-Git merge-base query that refreshes and pins the PR target branch
  separately from canonical publication leases.
- Based True App release PR titles and descriptions on the complete candidate branch against the
  pinned `main` target, including correction successors, and moved Tricia's packaging runs to
  `gpt-5.6-terra`.

## 2026-08-24 - Rejected Candidate Successor Recovery

- Recovered automatic successor creation for reviewed correction aggregates whose retained
  worktree starts at the correction commit, while requiring immutable server composition evidence
  to prove the exact rejected candidate branch, commit, tree, and reviewed correction.

## 2026-08-24 - Prepared Git Helper Reconciliation

- Pinned guest pushes to their immutable prepared base and atomically refreshed generated Git
  helpers through descriptor-anchored reads and replacements whenever task worktrees are prepared,
  preserving other worktree-local control state.

## 2026-08-24 - Rejected Deliverable Continuation

- Continued same-task rejected-deliverable rework from its immutable retained candidate across
  agent reassignment, using a fresh commit-suffixed branch and worktree instead of the task's
  original base; pinned preparations now retain the verified base tree, retries reuse the recorded
  composed base, composed candidates recover their pre-composition root, and multi-product tasks
  retain every unsuperseded rejected candidate.

## 2026-08-24 - Team Runtime Reliability And Recovery

- Added immutable external-source evidence snapshots with directory-scoped access, freshness
  metadata, bounded prompt projection, deterministic task-preparation invalidation on refresh,
  late-evidence fences for work-committing dispatch actions, reviewer handoff hydration, and
  natural-identity replay that adds only missing attachments.
- Added a provider-neutral directory scope to every project, including non-Git projects, with a
  legacy Git-derived migration so operational teams use the same ACL model.
- Scoped questions and blocker diagnostics to explicit task, case, project-lead, or manager
  authority instead of relying on development-specific roles or provider semantics.
- Retained accepted reviews when acceptance-boundary remediation is required, added typed retry
  paths, and locked workflow-owned correction topology against ordinary agent mutation.
- Normalized the exact legacy accepted-correction wrapper that lacks composition evidence,
  replaying retained review evidence without rewriting child, deliverable, review, or Git history.
- Added bounded cleanup retries with explicit operator disposition, coherent multi-table SQLite
  reads, atomic failure audit/disposition, enqueue-time repository/directory-scope snapshots for
  physical cleanup, per-Git-context cleanup generations with stale mismatch supersession,
  pre-freeze project repository-scope change guards, and coordinated runtime-generation checks
  across the Team-serving processes.
- Snapshotted verifier images and resource limits per release-verification run while preserving
  the configured project policy when a nonterminal pre-upgrade run resumes.
- Pinned preparation and guest pushes to an immutable server-authorized Git base, including after
  the named base branch advances, and rejected reused branches or pre-existing worktrees without
  authoritative provenance before they can be mutated.
- Added audited, policy-aware dependency repair after task replacement and fresh integration-
  boundary recovery that preserves submitted review, delivery, and provenance history; frozen
  affected sets, dispatch scope, Team membership, directory ownership, and fresh high-priority
  context now fail closed.

## 2026-08-24 - Correction Dependency Composition

- Composed newly accepted correction-task Git inputs on top of the rejected candidate and
  recorded those exact immutable inputs in the dependency-composition manifest, so reviewed
  correction aggregates can pass acceptance and create their deterministic successor releases.

## 2026-08-24 - Task-Scoped Team Directives

- Included an owned task explicitly selected by an operator directive in that
  directive's ordinary Team dispatch scope, even while normal task readiness is
  invalid. This preserves narrow task authority for workflow repair without
  granting authority from ordinary handoff or observation inbox items.

## 2026-08-23 - Durable Team Composition And Acceptance Gates

- Retained every newly submitted Git candidate under an immutable Team deliverable ref before
  either direct or formal handoff, retained formal review snapshots separately, and derived both
  explicit task dependencies and required contributions from accepted provenance so later branch
  movement cannot invalidate an accepted input.
- Recorded ancestry-reduced dependency-composition manifests on prepared contexts and submitted
  deliverables, including the exact base, source fingerprint, frontier, and composed commit/tree.
- Made Git-context creation idempotent and resource-scoped so unrelated Team revisions do not
  force a reclone, and expanded stable preparation generations to cover contribution provenance.
- Exposed immutable refs and composition evidence in Team Admin UI and documented the prospective
  policy-version boundary for historical accepted records.
- Scoped composition evidence to Git-backed inputs, retained its original root across dispatches,
  revalidated dependency generations transactionally, and materialized immutable review refs before
  dependency checkout with legacy branch fallback.
- Verified composed Git ancestry against the submitted candidate, rechecked manifest dependency
  scope against the current task graph, and normalized optional provenance during context replay.
- Kept immutable review identities and retained refs, and aligned composition validation with
  preparation when task dependencies are cancelled.
- Required agents to publish local commits before deliverable submission, made provenance
  mismatches point directly to `git push-agent`, and taught the helper to preserve an already
  session-owned Team task branch instead of double-prefixing it.
- Retired hidden deliverable acceptance verification and verification waivers. Required review
  criteria still need explicit pass/fail decisions and rejections still need explanations, but
  criterion evidence, agent-reported checks, logs, and links are advisory.
- Created each release package before its frozen candidate is verified, and gave every new
  candidate a fresh visible server-executed verification run before release-cohort and human
  review. Legacy waiver inputs and queued acceptance-verification effects remain safe no-ops for
  compatibility without changing persisted historical audit data.
- Published a generic `team.release.published` event from the generated Team runtime pack only
  after a successful canonical push is recorded without a conflicting immutable release result,
  so operator-owned publication and PR workflows can remain in the main pack and survive add-on
  runtime-pack refreshes.
- Merged concurrent managed PR-note histories before publication and retried racing note updates,
  so a stale worktree note ref cannot block an otherwise verified Team branch handoff.

## 2026-08-23 - Single-Review Team Dispatches

- Limited ordinary Team reviewer dispatches to the oldest actionable review and excluded inbox
  notices for later reviews, so divergent immutable candidates cannot be batched into one checkout.
- Added a durable one-second follow-up wake after a successful review when another review remains,
  bypassing the periodic Team heartbeat even when the next review has no wakeable inbox notice.

## 2026-08-23 - Responsive MCP Gateway Tool Execution

- Kept synchronous MCP tools off the gateway transport event loop so slow Team commands no
  longer block unrelated client initialization and trigger 30-second startup failures.
- Removed redundant full-state copies and validation round trips from atomic Team command
  transactions while preserving aggregate validation, revision, idempotency, outbox, and
  dispatch-activity semantics.
- Added MCP request-duration logging and regression coverage for event-loop isolation and typed
  transaction rollback.

## 2026-08-23 - Stateful Workflow Pack Validation

- Preserved named graph metadata while reading and validating individual workflow packs, so graph-scoped state query and update nodes work correctly in multi-pack configurations.

## 2026-08-23 - Workflow History Desktop Inspector

- Restored the selected-node **Execution history** control in the desktop Workflow inspector,
  matching the existing compact inspector behavior.

## 2026-08-21 - Workflow Node Execution History

- Added a generic selected-node execution-history query in Admin Web with inclusive-start,
  exclusive-end date windows, status filters, up to eight typed JSON Pointer predicates,
  keyset pagination, execution detail, and links into the existing run inspector.
- Added sanitized, compressed, content-addressed compact inputs and outputs, typed scalar indexes,
  and source provenance with a configurable 365-day default retention period. Full execution
  records continue to default to 30 days.
- Kept existing orchestration rows unchanged: the effect worker copies them oldest-first in bounded
  idle batches, live writes update compact history transactionally, and retention guarantees a
  compact copy before full-record deletion. No automatic database vacuum is performed.
- Bound archived payload access to each execution's recorded directory, expanded credential
  redaction, preserved exact numeric equality, and kept pagination and timestamp windows stable.

## 2026-08-20 - Workflow-Paused Team Tasks

- Added the non-terminal Team task state `waiting_for_input` and the narrowly
  scoped `effect:team:set-task-work-state` workflow node. Paused tasks do not
  generate ordinary owner wakes or manager fallback; a workflow can resume one
  to `in_progress` and immediately wake its owner without embedding timer
  policy in Team runtime.
- Limited workflow work-state transitions to tasks already in that lifecycle,
  prevented stale agent dispatches from resuming paused tasks, and kept agents
  from creating tasks in the workflow-only paused state.
- Prevented stand-down revocation and other immediate-wake paths from treating
  workflow-paused tasks as newly actionable owner work.
- Kept all five task-board lifecycle lanes on one desktop grid row.

## 2026-08-20 - Reference Repository Workflow Editor

- Replaced the reference-repository effect's raw JSON editor with repeatable managed-repository
  selection rows and a separate per-repository main-branch field. Existing `repo_source` workflow
  configuration remains compatible while new rows store the exact managed repository ID.
- Preserved exact upstream-array bindings through structured repository inspector edits and exposed
  an outer literal/binding mode for the repository list.

## 2026-08-20 - Release Exception And Gate Error Recovery

- Prevented rejected-review exceptions from reopening a predecessor after a successor exists,
  retained consolidated correction work while other cohort rejections remain, and safely retired
  active correction review/acceptance handoffs and Git contexts before cancellation.
- Cancelled obsolete merge-recovery tasks when reviewed correction rework replaces their failed
  aggregate promotion.
- Reapplied normal release-package intake and verification-waiver policy before replacing a failed
  correction aggregate with newly reviewed evidence.
- Removed operator-excepted review findings from retained consolidated correction tasks and their
  pending inbox handoffs, and hid rejected-review exception actions after a successor exists.
- Allowed `error` edges on branch-gate capabilities and routed failures recorded directly by gate
  providers through the configured error branch.
- Reserved the switch gate's `error` port for lifecycle failures so business cases and defaults
  cannot intercept structured gate-error routing.
- Reactivated deterministic correction and merge-recovery tasks when later failures require fresh
  work, and persisted recorded gate-error completion intent so daemon replay cannot skip recovery.

## 2026-08-19 - Immutable Candidate Correction Guidance

- Preserved precise Git ancestry errors for failed correction aggregates and made generated
  correction work explicitly forbid rewriting the pinned release candidate while requiring new
  correction commit metadata to be checked before it is frozen by promotion.
- Rebuilt a pre-candidate blocked correction successor when its failed source is formally
  superseded by a newly reviewed correction deliverable, while retaining the failed promotion
  as retired audit history.
- Added an explicit operator-only retry for a rejected release review whose finding cannot be
  corrected without rewriting the unchanged pinned candidate; the original rejection remains
  immutable and the replacement reviewer must still evaluate every non-excepted criterion.
- Routed that exception through the release-retry API and Team UI, kept multi-review cohorts
  rejected until every rejected slot is replaced, and retired the obsolete correction task and
  inbox work when the exception is accepted.
- Preserved failed correction successors as actionable blockers when aggregate rebuild validation
  rejects replacement sources instead of crashing and rolling back reconciliation.

## 2026-08-19 - Workflow Gate Error Routing

- Allowed gate runtime and binding failures to publish a structured `error` branch when one is
  configured, so operator workflows can recover malformed or otherwise correctable inputs without
  leaving the source task silently stalled.
- Exposed the standard gate `error` route and its `success`, `error_message`, and `error_class`
  outputs through the workflow catalog and graph-aware binding schemas.
- Routed Factory Support handoff contracts with missing fields into the same bounded agent-repair
  loop as present-but-invalid fields.

## 2026-08-19 - Admin Web Workflow Pack Deep Links

- Preserve the selected workflow pack in the workflow page URL so refreshing the page restores
  that pack.

## 2026-08-19 - Team Release-Verification Single Flight

- Coalesced overlapping execution of one Team release-verification run within an effect worker and
  serialized the run across separate effect-worker processes, so duplicate delivery cannot reset
  and clean the same mutable checkout concurrently. Conflicting followers now validate their
  immutable release, run, and candidate identity before joining. Independent verification runs
  still execute in parallel.

## 2026-08-19 - Admin Web Add-on Caddy Routing

- Proxied `/addons/*` through managed Admin Web Caddy deployments so add-on iframes reach their
  server-owned UI routes instead of the static-file 404 handler.

## 2026-08-19 - Team Review-Correction Recovery

- Made typed candidate-correction recovery actions work consistently for review- and
  verification-origin releases. Operators can now create a pinned verified replacement or authorize
  an audited waiver from the current correction evidence, while failed verification remains
  ineligible for waiver.
- Recognized accepted, lineage-pinned downstream replacements when creating successors for rejected
  release reviews, so a verified follow-up is promoted instead of retrying the incomplete correction.
- Treated recovery-created correction tasks as automatic candidate corrections, preventing their
  accepted resolution notice from asking the project lead to request a duplicate release package.

## 2026-08-18 - Compatibility Surface Retirement

- Removed the retired Azure DevOps work-item helper and environment/configuration fallback surface;
  the ADO worker now handles pull-request feedback and completion from canonical configuration only.
- Rejected the retired workflow `directory_paths` field now that live packs use `directory_path`.
- Made Admin Web group assignments the sole authorization source and removed stored/API/UI user
  roles after both supported deployment databases were rebuilt and verified.
- Removed the remaining `agent-sandbox` rename readers and cleanup paths after normalizing guest,
  worker, host-network, SSH-key, service, and configuration state on both supported deployments.

## 2026-08-18 - Agent Artifact-Staged Workflow Trigger

- Added `trigger:agent:artifact-staged`, which reacts only to durable control-plane events emitted
  after a successful agent `stage_artifact` call. Operators can restrict it to an exact artifact
  path or a path prefix, then use the originating session metadata and artifact-text query for
  workflow-specific validation.
- Scoped staged-artifact activations to the workflow graph directory and its descendants so a
  sibling directory's session and artifact identifiers cannot enter the graph.

## 2026-08-18 - Team Ephemeral Session Directory Inheritance

- Made Team ephemeral workers inherit the logical configuration directory from their persistent
  parent agent's backing core session. A conflicting requested directory is rejected, so generated
  Team workflow-pack refreshes cannot redirect the worker into another configuration scope.

## 2026-08-18 - Orchestration JSON Backend Removal

- Made SQLite the unconditional orchestration queue, execution, snapshot, and workflow-journal
  backend, while rejecting stale configuration that still requests `json`.
- Removed the completed JSON migration/finalization commands and their compatibility tests after
  both supported deployments were archived, marked, and repaired.
- Removed JSON-backend worker, Admin, wake, Teams, ADO, and managed-Git hook branches together with
  their obsolete configuration settings and operator documentation.

## 2026-08-18 - Direct Communication Workflow Validation

- Prevented workflow communication-target validation from treating generated node output schemas
  as literal target references, allowing direct communication-message triggers to reload safely.

## 2026-08-18 - Migration And Test Maintenance

- Restored the directory-config filename dependency used by the supported root configuration
  migration commands after legacy heartbeat cleanup removed it accidentally.
- Updated CLI help, Discord conformance, and worker-daemon tests to reflect Rich line wrapping and
  the current SQLite-only Discord inbox and worker-job stores.

## 2026-08-17 - Team Operator Communication Target Selection

- Changed the Team editor's operator communication target field into a dropdown populated with
  every communication target registered for the root directory, while retaining an unavailable
  existing selection so operators can repair or clear it safely.

## 2026-08-17 - Support Workflow Closure Safety

- Added a closed-Case snapshot to the durable `team.case.closed` event so a
  workflow can send a deterministic, source-thread-specific outcome message
  without racing later Case edits.

## 2026-08-17 - JSON Store Migration Retirement

- Retired completed first-use JSON-to-SQLite imports for Admin Web users, Team state, Teams and
  Discord inboxes, Teams messages, scheduled wakes, container-worker executions, and worker jobs.
- Updated runtime defaults and production callers to use their canonical `.db` stores directly.
- Routed internal-event workflow notifications through `orchestration.db` whenever the SQLite
  orchestration backend is active, removing the last live `workflow-events.jsonl` writer on that
  backend while preserving the explicit JSON rollback path.
- Added a fail-closed orchestration JSON finalizer with read-only `--check` and lock-protected
  `--apply` modes. It blocks malformed or missing nonterminal state, imports only retained terminal
  history, archives expired history with hashes, and writes a recoverable one-way marker.
- Fixed the finalizer to bind completed markers to the exact SQLite database, load persisted
  maintenance settings without initializing runtime state, and document the runtime-directory CLI
  argument correctly.
- Made cached workflow journals honor the completed one-way marker dynamically, so a service that
  was already running during finalization switches to SQLite instead of recreating
  `workflow-events.jsonl` on its next notification.
- Serialized workflow-event appends with the JSON finalizer, made incomplete or invalid markers
  fail closed, translated live-reader cursors across the JSON-to-SQLite cutover, and recorded the
  actual configured journal, marker, and database paths in an adjacent durable tombstone.

## 2026-08-17 - Discord Forum And Public Thread Communication

- Added explicit Discord communication-target conversation scopes for flat channels, public child
  threads, or both, preserving flat-channel behavior for existing targets.
- Added durable public-thread discovery and parent bindings to Discord ingest, with child-aware
  communication triggers, message queries, attachment authorization, direct session policy, and
  Team Chat source provenance.
- Added validated Discord thread replies for workflow messages and notifications, including
  parent-owned resolved-identity webhooks, structured archived/locked/unauthorized failures, and
  no automatic unarchive or private-thread access.
- Added Admin Web configuration and workflow-editor guidance for Discord thread-aware targets.
- Fixed thread ingest provenance, order-independent conversation-scope merging, parent webhook
  authorization for resolved identities, and read access to archived or locked public threads.
- Fixed Admin Web conversation-scope draft round-tripping, excluded archived public threads from
  live Discord API polling while retaining local history and attachment access, and routed
  resolved-identity file replies through the parent channel webhook.

## 2026-08-17 - Prompt Case Closure Publication And Explicit Throttle Routes

- Removed the completed heartbeat-directory migration command and its legacy `heartbeat.json`
  conversion code after both supported deployments were migrated to registered `wake.json`
  directory configuration.
- Retired workflow-graph `directory_paths` from graph configuration, generated drafts, activity
  reports, and Admin Web writes. The pack loader still normalizes a legacy one-item value on read
  because operator-owned ignored packs can outlive a product release; all model output is canonical
  `directory_path` and multiple directories remain invalid.
- Prompted the durable Team outbox publisher after a Case closure is committed through Admin API,
  Team MCP, or a workflow effect, carrying the exact pending outbox record IDs into a targeted
  immediate claim so older backlog cannot displace the closure record. Publication failures remain
  non-fatal because the committed outbox and periodic Team runtime tick retain recovery.
- Standardized `gate:throttle` on explicit `default` and `dropped` outbound ports, emitted the
  selected port at runtime, and preserved legacy unported edges as the default route while Admin Web
  now serializes newly created default edges explicitly.

## 2026-08-16 - Workflow-Controlled Team Agent Lifecycle

- Added workflow-only `effect:team:register-agent` and
  `effect:team:retire-agent` primitives for attaching an in-scope core session
  as an ordinary persistent Team agent, then safely detaching and archiving it.
  Registration is idempotent for an exact binding; retirement enforces existing
  Team work and hierarchy blockers, preserves the agent/session audit record,
  and permits a safe retry after the separately owned core-session destruction.
- Added transactional `team.case.closed` outbox events for real Case transitions
  to `closed`, across Admin, workflow, ordinary Team-agent, and protected
  post-release closure paths. The event is correlated by Case and has a stable
  Case/closure-time idempotency key; initial closed records, resolved records,
  and closed no-op updates do not publish cleanup work.
- Fixed lifecycle effects to use the production session-lookup contract,
  preserve closed-Case and Team-memory references to retired agents, block
  retirement while an owned Case is still open, and reject archived roles or
  sessions still bound to non-cleaned ephemeral assignments.
- Made revision-guarded registration and retirement retries recognize an exact
  completed replay, blocked retirement for governance management or dispatches
  and non-terminal release ownership, preserved rejected-task and terminal
  release history, and ignored retired agents during persistent session routing.

## 2026-08-16 - Retire Completed Runtime Compatibility Paths

- Removed automatic local-issue schema migration and legacy proxy metadata inference now that both
  deployments persist canonical `remote_reference` records and unified issue state.
- Removed gateway YAML bootstrap/import support and the `migrate-global-config-to-root` command;
  runtime processes now start only from `host-config.json` plus directory-scoped configuration.
- Removed automatic workflow prompt-lifecycle migration and now reject legacy top-level provider
  fields in workflow node envelopes at every boundary. Admin Web state and saves, generated
  workflow drafts, typed in-process node construction, packs, and runtime configuration now all use
  the same nested `config` payload; the former flat editor and programmatic adapters are removed.

## 2026-08-15 - Immediate Team Wake After Terminal Blocker Resolution

- Prompted Team's durable outbox publisher after Admin Web resolves a task-backed terminal
  blocker that creates `task_ready` wake work, so the one-second debounced wake no longer waits
  for the next 15-minute runtime tick. The committed Team outbox and periodic reconciliation
  remain authoritative recovery paths if prompt publication fails.

## 2026-08-15 - Immutable Team Review Repository Snapshots

- Bound every newly created Git-backed Team review, including retries and release reviews, to its
  exact deliverable, task-base, or release-candidate commit before a reviewer can be prompted.
- Persisted the server-owned review snapshot and retained one managed-Git ref per review. Legacy
  reviews remain readable but cannot be dispatched from a mutable checkout; create a retry or
  replacement review to obtain current immutable provenance.
- Made review preparation pin and verify detached reviewer checkouts, reject unavailable,
  conflicting, or cross-review commit provenance, and include the exact ref, commit, and checkout
  in reviewer context. Normal task checkout behavior is unchanged.

## 2026-08-15 - First-Class Session Model Relay

- Added a dedicated session-level model relay selector for public, local, Azure, Anthropic, or
  directory-default routing without editing raw metadata JSON.
- Rejected invalid relay modes written through nested session metadata while rendering legacy
  invalid values as no override, keeping affected sessions visible and repairable in Admin Web.

## 2026-08-15 - Searchable Agents Page

- Added a search field to the Agents page for filtering agents by name, ID, session, or directory.
- Cleared the search together with backend and status filters when an explicit agent hash deep-link
  is applied, so an existing query cannot hide the requested agent.

## 2026-08-15 - Retire Completed Managed-Git Migrator

- Removed the legacy `git-repos migrate` CLI and Admin API, migration manifests, recovery and
  rewrite implementation, workflow-pack migration hooks, and obsolete migration tests now that no
  supported installation retains unmigrated path-based managed-Git repositories.
- Removed active migration instructions and state from operator documentation while leaving current
  managed repository registration, refresh, authorization, worktree, and integrity behavior intact.

## 2026-08-14 - Persistent Assistant Self-Refresh Recovery

- Reconciled clean persistent-assistant reference checkouts after an agent-requested refresh by
  verifying, in the control-plane managed mirror, that the previous trusted commit is an ancestor
  of the checkout and the checkout is an ancestor of the cached configured-branch tip.
- Kept the normal cached preparation path free of additional Git work and ran mismatch-only
  ancestry verification inside the existing per-repository parallel tasks, while retaining
  fail-closed rejection for dirty checkouts, source changes, divergent history, and commits outside
  the configured branch.

## 2026-08-14 - Bokio Multi-Evidence Journal Attachments

- Compiled multiple prepared journal-entry evidence records into one PDF before the single Bokio
  upload that can be linked to a journal entry, while retaining each original PDF, JPEG, or PNG
  evidence record and its provenance locally.
- Exposed the combined-attachment behavior in the prepared-action review UI and Bokio operator
  guidance, preventing a second evidence upload from leaving a newly created journal entry partial,
  and fail-closed retries of legacy partial attachments that require manual reconciliation.
- Normalized EXIF image orientation and rejected images above 16,000,000 pixels before rendering a
  combined evidence PDF, preserving receipt orientation while bounding host memory use.

## 2026-08-14 - Direct Teams Trigger Publication

- Published matching Teams-backed workflow trigger events atomically from Teams ingest immediately
  after inbox persistence, while retaining trigger-orchestrator cursor reconciliation and periodic
  recovery.
- Captured one workflow configuration revision per ingest cycle for polling, sender resolution, and
  matching, and added direct-publication, deduplication, failure, and wake diagnostics.
- Added `publish_trigger_events_directly` as a default-on Teams ingest setting with a rollback path to
  the prior persist-and-wake behavior, plus atomic queue publish results for race-free wake decisions.
- Kept fast-path polling on each workflow graph's directory-scoped Teams configuration, preserved
  inbox ingestion when direct queue initialization fails, and deferred the effect wake until all
  event publications for the cycle have completed.

## 2026-08-14 - Remove Expired Compatibility Surfaces

- Removed unreachable or unreferenced gateway, workflow-graph, schema-bootstrap, review-store, and
  Git-identity aliases after migrating the remaining internal and test callers to canonical names.
- Removed the deprecated ADO work-item listing script and rejected `--reprocess` /
  `--list-unprocessed` options; workflow-owned ADO intake and the narrowly scoped `--forget`
  migration cleanup remain supported.
- Removed the retired Assistant `allowed_mcp_tool_profile` input and the unused per-trigger and
  per-effect workflow mutation service API, including its legacy flattened response payloads.
- Re-authored the built-in Team runtime workflow pack directly in canonical IR form instead of
  rebuilding it from a legacy flat-node document on every import.

## 2026-08-14 - Remove Managed-Git Legacy Preflight From Runtime Paths

- Removed the legacy managed-Git migration inventory from repository operations and orchestrator
  startup/reload paths now that supported installations have completed migration.
- Retained the explicit `git-repos migrate` dry-run, apply, recovery, verification, and finalize
  safeguards for operators who deliberately invoke the migration workflow.
- Kept managed registry schema validation, scoped repository authorization, repository locking,
  worktree reconciliation, and all Git integrity checks unchanged.

## 2026-08-14 - Globally Bounded Teams-Ingest Polling

- Added `max_concurrent_target_polls` to the Teams ingest worker configuration. It defaults to
  `1` for serial compatibility and bounds concurrent Graph channel/chat polls to `1`–`32` while
  directories and prerequisite discovery remain serial.
- Reused each directory's Graph client across concurrent target polls, synchronized per-cycle
  accounting and trigger wake delivery, and made delegated credential refresh single-flight to
  prevent concurrent refresh-token requests.
- Coalesced credential-wide authentication backoff updates per polling cycle and drained successful
  concurrent target results before propagating sibling errors, preserving trigger wake delivery for
  messages that were already persisted.

## 2026-08-14 - Keyed Fixed-Window Workflow Counter Gate

- Added `gate:counter`, which forwards each event with a keyed fixed-window `count` for friendly
  activity tracking without throttling or dropping events.
- Kept counter output-schema derivation isolated from its upstream schema, so direct bindings to
  a trigger or another upstream node cannot incorrectly validate a synthetic `count` output.

## 2026-08-14 - Fixed-Window Workflow Throttle Gate

- Added `gate:throttle`, a keyed fixed-window rate limiter that passes a configured number of
  activations through its default route and can forward overflow unchanged through a `dropped`
  route.
- Made quota decisions durable and cross-process atomic, freeze bound limits for an open window,
  and expose window, quota, and decision diagnostics in gate execution records.
- Added the Admin Web catalog fields and explicit Default/Dropped connection routes, alongside
  validation, registry, documentation, and focused concurrency coverage.

## 2026-08-14 - Opt-In Assistant Repository Refresh

- Made upstream refresh opt-in for persistent-assistant reference repository preparation. The
  default path reuses cached managed-mirror state and retains an existing clean worker checkout;
  `refresh_upstream: true` preserves branch probing, changed-mirror refresh, and exact-commit
  advancement.
- Kept first-time cached checkout materialization, read-only configuration, source validation,
  access provisioning, parallel setup, and dirty-checkout failure behavior in both modes.
- Persisted each successfully prepared commit in worker-owned state outside the writable agent home;
  cached reuse now rejects clean assistant-authored commits whose SHA differs from that trusted
  record without adding a Git network request to the steady-state path.
- Made first checkout materialization pin the commit actually served after the managed read
  endpoint's mandatory mirror refresh, avoiding false stale-pin failures when upstream advanced
  while preserving exact requested-SHA enforcement for `refresh_upstream: true`.

## 2026-08-13 - Branch-Probed Reference Repository Refresh

- Replaced unconditional full managed-Git fetches during persistent-assistant reference repository
  preparation with live branch probes. Unchanged requested refs reuse the verified canonical mirror;
  changed, missing, default-branch-moved, and first-use repositories retain the existing full
  transactional refresh and validation path.
- Recorded branch-scoped probe evidence separately from repository-wide refresh timestamps and
  exposed probe, coalescing, and full-refresh decisions in preparation outputs and logs.
- Preserved repositories whose upstream `HEAD` is detached or otherwise lacks a symbolic-ref
  advertisement by routing the valid SHA-only response through the existing full-refresh default
  fallback.

## 2026-08-13 - Immediate Effect Completion Wakeups

- Woke the effect orchestrator as soon as an in-process effect or query future completes, including
  futures resubmitted during recovery. Durable execution state and periodic polling remain the
  fallback when the best-effort Unix wake signal cannot be delivered. Wake notifications use
  nonblocking datagram sends so a full socket queue cannot stall provider executor threads or the
  orchestrator cycle.

## 2026-08-13 - Required Codex MCP Startup

- Marked the Evander MCP server as required in container, subscription, and VM Codex
  configuration, and repeated the requirement on session-scoped prompt commands. Fast model turns
  now wait for tool discovery instead of incorrectly concluding that Teams and other MCP tools are
  unavailable while optional startup is still in progress.

## 2026-08-13 - Codex Code-Mode Host Runner Packaging

- Packaged the matching `codex-code-mode-host` executable with the Codex runner and made runner
  builds fail closed when the binary pair is incomplete. The artifact manifest now fingerprints
  both executables so a changed tool host invalidates the cached image archive, and worker
  deployment verifies the pair inside the loaded image.

## 2026-08-13 - Teams Direct-Message Trigger Targeting

- Required explicit one-on-one Teams chat targets for direct-message triggers and filtered their
  trigger-side polling to those targets before sender or content matching, preventing an
  allowlisted sender's message in an unrelated direct chat from waking an agent.

## 2026-08-13 - Worker-Local MSSQL Client Toolchain Packaging

- Build the default runner `sqlcmd`/ODBC archive on the worker after its Microsoft client packages
  are installed, removing the unintended SQL client prerequisite from the Ansible control machine.
  Prebuilt archives remain supported, and standalone client-toolchain deployments can explicitly
  retain controller-side packaging.

## 2026-08-13 - Polling and Reference Repository Review Hardening

- Prevented persistent assistants from redirecting host Git through symlinked administrative
  metadata or executing checkout hooks, filesystem monitors, and configured content filters;
  tracked `.evander/` edits now also block reference-repository refreshes.
- Kept removed debounce gates out of effect-worker deadlines, bounded Teams retry arithmetic,
  separated directory credential failures from resource-level authorization failures, and scoped
  delegated polling state to the selected identity.
- Published managed-Git refresh freshness while the coalescing lock is still held so concurrent
  callers reuse the completed fetch.

## 2026-08-13 - Isolated Teams Polling and Faster Assistant Preparation

- Persisted Microsoft Graph retry cooldowns per directory credential scope and logical target so
  one unhealthy chat, channel, discovery endpoint, or user lookup no longer delays healthy Teams
  intake; healthy messages now wake trigger orchestration before the remaining scan completes.
- Made effect-worker sleeps honor the nearest persisted gate deadline, preserving debounce quiet
  periods and maximum delays without adding the ordinary polling interval as scheduler slack.
- Added batched, pull-time-fresh read-only reference-repository preparation for persistent
  container assistants, including concurrent refresh, cross-process refresh coalescing, exact
  detached commits, reusable session Git grants, and fail-closed dirty-checkout handling.

## 2026-08-13 - Dependency-Preserving Verification Recovery

- Allowed an accepted, fully verified downstream remediation to create a rejected-candidate
  successor when its sole task dependency and immutable Git work context both name the exact
  accepted verification-correction deliverable, branch, and commit. Extra, manual, or unpinned
  dependency lineage remains ineligible.

## 2026-08-13 - Bounded Team Inbox and Review Replays

- Stopped unchanged pending inbox and review work from bypassing the Team
  no-progress limit. Exhausted agents now receive at most one attempt for a
  newly changed work snapshot while context rotation and recovery wakes remain
  available.
- Kept immediate inbox follow-up events policy-neutral so the configured
  `max_no_progress_count`, including custom and unlimited values, is enforced by
  the lease decision instead of a hard-coded finalization default.
- Applied the same edge-triggered limit to manager-fallback work using a
  separate, full-fidelity team-wide snapshot; direct manager work remains in
  the ordinary wake domain.
- Hardened assigned-review prompts to hydrate deferred Team tools by exact
  logical name and retry before reporting the review interface unavailable.

## 2026-08-13 - Inspectable Bokio Duplicate-Check Evidence

- Added the read-only `bokio_get_duplicate_check` add-on MCP tool so an independent
  reviewer can dereference a durable `bokio-duplicate-check:...` result without
  rerunning or trusting a producer's prose summary.
- Documented that existing bookkeeping and review sessions must be granted the
  duplicate-check creation and read tools explicitly.

## 2026-08-11 - Release Candidate Input Validation

- Reject release packages for Git-backed projects when their direct inputs contain only
  supporting artifacts or source-deliverable lineage and therefore cannot produce a pinned
  candidate. The error now directs managers to create an explicit Git integration task for
  mixed-base contributions, and the release UI identifies historical dead-end packages as
  blocked instead of indefinitely waiting for a candidate.
- Treat policy-v2 artifact-only releases for projects without Git as candidate-not-required in
  both release progress and detail views instead of mislabeling them as blocked Git releases.

## 2026-08-11 - Container Worker MSSQL Client Toolchain

- Added an optional host-seeded, read-only `sqlcmd` and ODBC Driver 18 toolchain cache for
  container workers. It is enabled with worker MSSQL by default, is verified inside the runner
  image during deployment, and lets agents query only databases provisioned through existing
  session-scoped MSSQL tools without putting a SQL Server engine in each container.
- Documented snapshot-backed query-plan evidence using the existing MSSQL Seed add-on, including
  local `.bak` placement, versioned aliases, Query Store warmup, artifact capture, and explicit
  fixture-derived evidence labeling.

## 2026-08-11 - GitHub Managed Git Refresh Authentication

- Rewrote token-backed GitHub SSH refresh sources to their canonical HTTPS URLs at Git invocation
  time, so the host-owned refresh broker uses its configured GitHub token while preserving
  registered SSH source identity and fail-closed stale-ref behavior.

## 2026-08-11 - Workflow Graph Label Legibility

- Reduced workflow-canvas graph labels, kept each name on one line, and scaled labels with the
  canvas zoom so adjacent graph names no longer crowd one another when viewing a broad workflow.

## 2026-08-11 - Bounded Team Test Execution

- Versioned Team agent guidance to require explicit timeouts on every test command, recommend a
  five-minute initial budget for focused tests, and prohibit indefinite unchanged retries after a
  timeout. Future Team wakes now reseed the managed guidance section before execution.
- Added a container-worker active-tool watchdog (five minutes by default), safe worker-side
  interruption for recognized test process groups on PID-limit events or an operator action,
  visible active-tool execution metadata and interruption control in Admin Web, and retained the
  thirty-minute whole-job timeout as the final fallback. The worker never accepts a caller-supplied
  command, PID, or process group for this control path.

## 2026-08-11 - Team Wake and UI Latency

- Reduced high-signal Team wake debounce from ten seconds to one second and made newly scheduled
  internal events notify the trigger worker, whose sleep now tracks the earliest durable deadline
  and whose trigger sweep rechecks deadlines before it sleeps. Events that become due during a
  provider sweep are now delivered to internal-event listeners in that same cycle without polling
  unrelated external providers twice.
- Made Team state and conversation streams poll only the lightweight revision metadata between
  changes and moved their SQLite reads off the Admin Web event loop.
- Coalesced bursts of identical, revision-matched Team agent and governance wakes into generational
  passes per state database without serializing unrelated agents. A trailing pass preserves
  wall-clock and external-session eligibility changes that occur during an active wake. Added
  versioned, bounded persisted wake lease summaries so full release and task context stays
  prompt-local. The existing `leases` output is retained as a bounded-summary compatibility alias;
  live workflow packs do not bind its former raw internals.
- Refreshed each bounded Team wake summary from final durable dispatch or governance-runtime state,
  including queued, completed, and failed outcomes rather than the initial reservation snapshot.
- Removed redundant Team-state validation, deep-copy, and serialization passes from reads and
  atomic updates; targeted wakes also reuse their initial state snapshot and skip no-op
  budget/chat transactions.

## 2026-08-11 - Team Escalation Delivery and Wake Limits

- Sent workflow communication through the supported host-owned `workflow` identity when an effect has no agent session, carry a Team manager's real session on new operator escalations, and retry a failed manager-identity send immediately with that host-owned sender. Host-owned Discord sends now authorize only the selected, capability-checked communication target instead of depending on the separate operator-command polling allowlist.
- Recorded operator-escalation delivery outcomes durably, surfaced final send failures to the responsible manager, and reset no-progress state only after confirmed delivery rather than after queued intent.
- Stopped unevidenced `team_update_blocker` follow-ups from resetting the three-wake no-progress cap; route, resolution, and evidence-bearing updates still count as material progress.

## 2026-08-11 - Team Task Delegation

- Allowed the current owner of a non-terminal task to delegate it to an eligible subordinate, while retaining the existing hierarchy, team, project-assignment, and terminal-task safeguards across both Team command surfaces.

## 2026-08-11 - Historical Team Lifecycle Reconciliation

- Added a revision-checked `evander-cli team migrate-historical-lifecycle` migration that dry-runs by default, repairs only audited terminal historical lifecycle contradictions, retires orphaned pending deliverables without altering their artifacts or evidence, and writes a resolved audit record for every repaired task.

## 2026-08-11 - Team Git Context Preparation Safety

- Kept an active Git work context alive while its owning dispatch has an unexpired preparation reservation, preventing delayed local-inference prompt delivery from abandoning the context before the agent can submit its work.

## 2026-08-10 - App Dev Team Discord Routing

- Added a `content_starts_with` filter to provider-neutral communication-message triggers, enabling a shared Discord channel to route only explicit, leading agent-address messages into the durable Team Chat lifecycle.
- Fixed session-scoped Discord replies so outbound Team Chat delivery checks the replying agent's `write` grant instead of an unrelated inbox-read allowlist.
- Strengthened and versioned Team Chat wake guidance so agent final text cannot replace the required durable operator reply tool call.
- Made Codex code-mode Team Chat wakes resolve deferred MCP reply callables through `ALL_TOOLS` and computed bracket access, avoiding false `not a function` failures from dotted tool names.
- Made a missing reply tool in the first code-mode `ALL_TOOLS` snapshot an explicit hydration pass that must be retried, preventing agents from replacing the required tool call with plain output during deferred-tool startup.
- Initialized Team MCP managed-Git authority before command transactions begin, preventing deliverable submission from deadlocking while lazily assembling another Team-aware admin runtime.
- Published clean task base branches when worktrees are prepared, allowing artifact-only deliverables to resolve their authoritative managed-Git candidate without requiring a synthetic commit.

## 2026-08-10 - Discord Agent Personas

- Added an add-on-extensible outbound identity resolver and per-communication-target Discord
  persona opt-in without introducing a platform-owned persona registry.
- Added host-managed Discord webhooks keyed by channel and avatar digest that apply a resolved
  sender name with a Discord-hosted default avatar, lazily provision and repair credentials, and
  fall back to normal bot posts on definite webhook failures.
- Connected Team Add-on persistent agents to the resolver so their configured display names and
  avatars appear on opted-in Discord sends, and added Admin Web status, provision, test, and
  recreate controls for the channel webhook pool.
- Kept reusable Discord webhook tokens out of rendered failure tracebacks and authorized webhook
  mutations against the selected directory so directory-scoped owners can manage their targets.
- Kept Admin Web private by storing avatar image data as each cached webhook's default avatar and
  bounded the per-channel persona cache with least-recently-used cleanup.
- Made webhook send bookkeeping, stale-404 repair, and cache eviction identity-checked so an
  in-flight send cannot restore or delete a concurrently replaced webhook credential.
- Preserved the resolved-sender checkbox value across structured communication-target draft
  rerenders so the Admin editor no longer immediately clears the operator's selection.
- Carried the replying Team agent's session through the external-chat workflow and made Discord
  communication effects honor resolved sender identity, so replies use the agent's webhook name
  and avatar instead of the Evander bot identity.

## 2026-08-10 - Team Agent Context Editor

- Show Idle grace minutes only when an agent uses idle context clearing, update it immediately
  when the policy changes, and leave the stored grace unchanged for other policies.

## 2026-08-10 - Team Project-Lead Handoff

- Added a revision-safe Team admin handoff for an existing project's lead overlay, preserving the
  former lead as a project member so their later retirement can be validated without orphaning the
  project's lead or assignment records.
- Exposed that handoff through a revision-aware Admin API endpoint and a dedicated project editor
  action, so operators can transfer lead authority without combining it with unrelated edits.

## 2026-08-10 - Team Chat Dispatch Latency

- Made Team prompt-completion reconciliation query only terminal prompt effects and reuse that
  result within a cycle, rather than deserializing the full effect-execution history before chat
  dispatches can be leased.

## 2026-08-10 - Team Blocker Recovery Wake

- Restored a task owner's immediate durable wake whenever a task-backed blocker is resolved,
  including manager and operator resolution paths, so recovery does not wait for the periodic
  Team scheduler tick.

## 2026-08-10 - Team Chat Send Latency

- Persisted a direct operator chat message and its queued asynchronous dispatch in one Team-state
  transaction, avoiding a second complete state read, validation, and write before returning the
  chat API response.

## 2026-08-10 - Team Cost Budget Window Corrections

- Kept an active manual cost window intact when an operator changes it to UTC-monthly renewal;
  the first automatic renewal now occurs only at a later UTC month boundary.
- Treated a zero-valued active budget window as authoritative for governance warnings, preventing
  lifetime spend from producing a stale warning after a reset or renewal.
- Roll overdue UTC-monthly windows forward before cost reads and inside budget mutations, so
  reporting and cap status reflect the current month even when no Team wake has run.

## 2026-08-10 - Graph Email Trigger Reliability

- Used Microsoft Graph's supported message-delta date filter for fallback polling and kept the
  unread check client-side, allowing unread email workflow triggers to poll successfully.
- Baseline newly registered communication, email, Discord, Teams, and Teams-transcript triggers in
  the shared trigger-orchestrator config writer, including trigger type changes, so every supported
  configuration path avoids replaying message history.
- Filter Teams meeting transcripts by the saved registration time as well as the host sequence, so
  a pre-registration bridge transcript that reaches the ingest store late is not replayed.

## 2026-08-10 - Resettable Team Cost Budgets

- Changed Team cost-cap enforcement from a lifetime-only guard to audited budget windows. Teams
  can start a new window with a required operator reason or opt into no-carryover UTC-monthly
  renewal, while lifetime spend remains visible for reporting.
- Preserved existing caps as lifetime-based until their first explicit reset, avoiding an upgrade
  that would silently authorize additional Team wakes.

## 2026-08-10 - Per-Team Governance Cost Warnings

- Moved governance cost-warning policy out of the generated Team Runtime pack and into each
  Team's durable configuration. Teams default to an 80% warning ratio, may set an individual
  ratio greater than zero and no more than one, and retain that policy across runtime-pack
  refreshes.

## 2026-08-10 - Local Inference Launch Accounting

- Retained local-inference capacity after a successful VM launch that has not yet emitted its
  thread identifier, allowing backend termination reconciliation to release the slot instead of
  admitting overlapping work while the original agent still runs.
- Heartbeat long-running launch reservations until their blocking launcher returns, release
  container leases when the worker authoritatively reports a destroyed job, and preserve raw
  partial-line appends while following container logs.

## 2026-08-09 - Queue-Aware Dispatch Correctness

- Preserved same-context behavior through CLI, immediate libvirt tracking, and queued replay,
  including successful resumes that do not emit a new thread identifier, and kept CLI log
  observation following named, session-based, and container-worker targets without dropping
  bursts larger than the initial tail window.
- Kept durable Team dispatches distinct in the prompt queue and retained resolved VM identity,
  workflow and loop context, MCP approval leases, and Team model-usage attribution across delayed
  delivery.
- Bounded deferred Team, Team Chat, and governance preparation reservations through durable
  queueing, discarded queue records that no longer own a reservation before replay, and made full
  post-delivery activation plus session-metadata mirroring atomic or retryable across worker
  restarts.
- Enforced host-global FIFO admission across new arrivals and both local-inference replay backends,
  while discarding expired-session and removed-effect queue heads so they cannot starve the host
  indefinitely.
- Made queued replay restart- and concurrency-safe with stable dispatch identities, atomic
  in-flight claims and identity-checked deletion, preserving ordinary retry coalescing while
  repairing each delivered prompt's started branch before terminal reconciliation.
- Validated root-only local-inference capacity settings before compatibility provider writes can
  persist a child directory's email-domain changes.

## 2026-08-09 - Local Inference Admission Reliability

- Preserved every capacity-blocked prompt with a dispatch-specific durable queue key, including
  multiple prompts for the same target, while retaining coalescing for ordinary retry waits.
- Reconciled persisted running capacity leases against authoritative VM and container state so
  lost completion monitoring cannot leave the host-global pool permanently exhausted.
- Routed CLI resumes through queue-aware admission and made queued replay use the current fully
  resolved provider mode before launching a turn.

## 2026-08-09 - Team Idle Context Grace Reliability

- Cancelled a pending idle-grace rotation when work makes the agent active, including when the
  expiry wake is processed after its persisted deadline.
- Enqueued the precise expiry wake for Admin and agent-command idle transitions, and preserved
  the absolute grace deadline through durable outbox publication and retries.

## 2026-08-09 - Team Idle Context Grace

- Added a per-agent `context_clearing_idle_grace_seconds` setting for the existing `idle`
  context-clearing policy, preserving the current context when new work arrives during a
  continuous idle window and using the existing bounded handoff once that window expires.
- Persisted the idle start and next expiry for reconciliation and operator visibility, scheduled
  a precise expiry wake with the Team runtime tick as fallback, and kept a zero-second migrated
  default so existing agents retain their current behavior.
- Added Admin API and Team UI controls with bounds from zero through seven days; the editor
  suggests 60 minutes when an agent is newly switched to idle clearing.

## 2026-08-09 - Local Inference Concurrency Admission

- Added an optional root provider setting, `local_openai_max_concurrency`, that limits concurrent
  agent turns using the fully resolved `local_openai` model provider across VM and container
  backends; omitting it preserves unlimited concurrency.
- Queued excess local-inference prompts durably in FIFO order and tied capacity release to the
  prompt-completion lifecycle, including recovery of abandoned launch reservations.
- Kept Team Runtime, Team Chat, and governance dispatches unleased while their local-inference
  prompts wait for capacity, starting the dispatch lease only after prompt delivery is accepted.

## 2026-08-09 - Team Context Reset Usage

- Added reset-bounded input-token totals and the latest completed reset time to selected Team
  agents, while excluding agents that reset context on every wake.
- Rebased token boundaries when agents move to another session and report usage as unavailable
  whenever any post-reset request lacks normalized token counts.

## 2026-08-09 - Review MCP Capability Receipts

- Recorded a compact, persisted proof of the review-decision MCP capability for each review
  dispatch, using only the required tool, a transport fingerprint, and status rather than a full
  tool catalogue.
- Confirmed the dynamic policy grant before resuming a reviewer, retried a verified capability
  mismatch once without consuming no-progress allowance, and then terminally routed a durable
  blocker while suppressing further wakes for that review.
- Kept pre-execution capability failures from completing unrelated blocker or Git scopes, and
  applied review suppression and capability receipts consistently to manager-fallback dispatches.
- Stored capability blocker identity structurally so valid delimiter-bearing IDs remain safe, and
  started transport evidence at the confirmed policy-grant time.

## 2026-08-07 - Trudy Discord Team Chat Bridge

- Added a mutation-capable, author-restricted Discord bridge that queues messages for Trudy through the normal Team Chat lease, scope, transcript, and reply lifecycle.

## 2026-08-07 - Cancelled Aggregate Assessment Reconciliation

- Kept authorized task cancellation resolved when an abandoned historical aggregate deliverable
  has stale contribution metadata or a pre-cancellation rework request, without rewriting its
  accepted provenance or creating successor work.

## 2026-08-07 - Legacy Workflow Pack Graph Validation

- Normalized unchanged legacy workflow-pack graph metadata before validating a save to another
  pack, so unrelated disconnected graphs no longer reject an otherwise valid workflow edit.

## 2026-08-07 - Add-on Workflow Layout Regeneration

- Made Team Runtime add-on materialization supply a complete, validated node layout and replace
  the layout alongside the pack on refresh, preventing new or stale nodes from falling back into
  the workflow editor's default overlapping position.

## 2026-08-07 - Workflow Error Route Rendering

- Added a standard canvas handle for each workflow node's runtime error route, so persisted
  error-path edges render even when their catalog entry has no configurable error output.

## 2026-08-07 - Safe Batched Team Chat Reply Validation

- Rejected agent chat replies with neither a non-empty `message_ids` batch nor a legacy
  `message_id`, recording the malformed reply as a failed chat item instead of aborting the
  agent-action completion.

## 2026-08-07 - Prompt Team Outbox Publication

- Woke the Team workflow immediately after operator candidate-correction recovery commits its
  durable outbox records, while retaining the quarter-hour Team runtime tick as a recovery path.

## 2026-08-07 - Historical Attention Classification

- Moved escalation notices for superseded or retired reviews and resolved source blockers out of
  active operational obligations and into retained history, while preserving their audit trail.

## 2026-08-07 - Sealed Release Review Cohorts

- Sealed each new release's complete reviewer/profile set before dispatching it,
  and deferred candidate-correction work until every cohort member has reached
  an accepted or rejected decision.
- Consolidated all rejected cohort findings into one correction task and wake,
  while keeping blocked reviews retryable in the same frozen slot and leaving
  existing uncohorted release reviews on their legacy behavior.

## 2026-08-07 - Contracted Leaf Boundaries

- Prevented contracted non-boundary tasks from acquiring child work or accepting
  aggregate contribution sources, while preserving source-bearing legacy aggregates.

## 2026-08-07 - Blocker Recovery Git Context

- Included blocker-recovery tasks in the immutable dispatch task payload, so recovery dispatches
  prepare the same task-scoped Git work context that their authorized scope exposes.

## 2026-08-07 - Team Chat Attention And Coalescing

- Surfaced plain-text operator Chat steering once in the next Team MCP response for an agent with
  an active lease, atomically recording the message as seen without requiring a chat reply.
- Coalesced queued messages from the same conversation and external return path into one Chat
  dispatch and one covering reply, while leaving attachment-bearing messages on the normal Chat
  dispatch path.

## 2026-08-07 - Release History Filters

- Added state and inclusive activity-date filters to Delivery release history. The date range uses
  the published date when present and otherwise the prepared date, matching the release card.

## 2026-08-06 - Runtime Attention Filter

- Kept routine due or leased agents out of the Operations “Needs attention” view; they remain
  available through the dedicated “Due / leased agents” filter.

## 2026-08-06 - Visible Awake-Agent Halo

- Moved the Org chart awake pulse from the already-bordered avatar to a restrained glow around
  the full agent card, keeping the awake state legible in both light and dark themes.
- Kept message particles on the exact hierarchy connector through their destination, including
  progress reports that travel in the reverse direction along the same line.

## 2026-08-06 - Candidate Recovery Instructions

- Added an optional operator-instruction prompt when creating a verified
  candidate-correction replacement. The instruction is stored in the pinned
  recovery task so the assignee receives its scope with the normal task wake.

## 2026-08-06 - Calm Live Team Org Chart

- Added restrained live activity to the Team org chart: awake-agent halos, short-lived derived
  activity bubbles, and one-shot message pulses for newly observed status requests and progress
  reports.
- Kept structural edges static, capped simultaneous effects, avoided replaying existing inbox
  history, preserved the operator's viewport, and respected reduced-motion preferences.

## 2026-08-06 - Resilient Agent Prompt Queue Details

- Kept the Agents-page detail view responsive while a selected agent's full
  queued-prompt records load, even though the performance-oriented list summary
  intentionally omits those records.
## 2026-08-07 - JSON-Safe Team Admin API Responses

- Serialize every Team Admin API response at the shared boundary so a derived raw
  datetime cannot prevent a Delivery view, release detail, or review decision
  from completing.

## 2026-08-06 - Admin Web Frontend Naming

- Renamed the full Admin Web frontend project from `frontend/workflow-editor` to
  `frontend/admin-web`, including setup, deployment, branding, worker-browser,
  test, and operator-documentation references.
- Kept the established `/api/workflow-editor/*` routes, workflow editor UI
  labels, and browser storage keys unchanged as compatibility contracts.
- Completed the rename migration for the default branding root and manual
  screenshot tooling, while retaining the legacy tracked bundle path in Ansible's
  pre-update reset so existing deployments can upgrade with a dirty generated entrypoint.

## 2026-08-06 - Recoverable Deterministic Dependency Composition

- Made divergent dependency composition commits reproducible across VM and
  container-worker backends by deriving both Git timestamps from the newest
  parent timestamp plus one second.
- Persisted exact base, source commits, failed input, and conflict paths when
  automatic dependency composition conflicts, and added a typed supervisor or
  Admin recovery that creates one pinned integration task for a selected resolver.
- Required the recovery deliverable to name every exact source and pass managed-Git
  ancestry verification before acceptance; accepting it resolves the blocker and
  makes the reviewed commit the blocked target task's authoritative composed base.
- Exposed recovery status and assignment in Team reads, MCP, Admin API, and the
  blocker UI, while refusing generic resolution of an unresolved composition conflict.
- Made recovery preparation use the captured base commit instead of a moving branch,
  preserved libvirt conflict evidence without treating other Git merge failures as
  conflicts, rejected stale recoveries at review acceptance, and released cancelled
  recovery tasks for reassignment.

## 2026-08-06 - Contracted Leaf Task Controls

- Preserved **Require Git provenance** on task creation and editing even when
  **Integration boundary** is not selected, matching the backend's contracted-leaf
  lifecycle used by Git-backed correction and recovery tasks.
- Clarified that contracted leaves require an accepted, server-verified Git
  deliverable but cannot aggregate required contribution tasks.

## 2026-08-06 - Spaced Team Task Board

- Restored consistent spacing between Task board context, create, view-switch, filter, and lane or
  hierarchy sections, while keeping the Board and Hierarchy tabs visibly separated.

## 2026-08-06 - Readable Team Role Descriptions

- Preserved line breaks and paragraph spacing in Team role descriptions without changing their
  normal proportional text styling.

## 2026-08-06 - Complete Team Git Configuration Surfaces

- Exposed agent author and committer identities in the primary Projects-page creation form so
  operators can apply project Git identities before the first checkout.
- Exposed consolidation commit messages in the workflow editor inspector catalog.

## 2026-08-05 - Compact Team Git Integration History

- Fast-forwarded Team promotions and dependency preparation when one reviewed commit already
  contains the complete validated change set, avoiding redundant merge commits.
- Collapsed genuinely divergent inputs into one meaningful multi-parent integration commit while
  preserving exact reviewed-commit ancestry, provenance, manifest checks, and configured Git
  identities across VM and container-worker backends.

## 2026-08-05 - Bounded Queued-Prompt Activity Lookup

- Stopped queued-prompt dispatch from materializing the full effect-execution history when the
  execution repository can query active records directly, keeping post-completion dispatch
  responsive as execution history grows.

## 2026-08-05 - Configurable Team Git Identities

- Added project-scoped agent author and committer identities, with an Evander Bot fallback, and reconciled them into both new and retained Team project checkouts.
- Attributed dependency-composition merges to the project author while retaining `Evander Team Dependency Composer` as the specialized committer across VM and container-worker backends.

## 2026-08-05 - Direct Team Chat File Downloads

- Made ordinary clicks on non-image Team Chat attachments download the selected file directly, while image attachments retain their new-tab preview behavior.

## 2026-08-05 - Session Repository Grant Labels

- Show each Session repository grant's managed-repository display name, canonical URL, and stable ID in a compact, default-collapsed table sorted by repository name, so operators can identify access without cross-referencing the catalogue.

## 2026-08-05 - Pull Request Work-Item Workflow Query

- Added Azure DevOps `query:pull-request:work-items`, which returns work items
  linked to one pull request with their normalized assignee identities for
  workflow-controlled commit authorship and routing.

## 2026-08-05 - Manager Fallback Role-Description Delivery

- Included role-description change metadata and its SHA-256 fingerprint in
  manager-fallback leases, allowing successful fallback resumes to record the
  delivered role context instead of failing post-resume.

## 2026-08-05 - Live Release Verification Progress and Logs

- Persisted release-verifier start, command transitions, and worker job IDs as
  each frozen check runs, so release progress names the active check and retries
  resume past already-passed commands.
- Added a directory-owner-only Admin Web tail for the active verifier command;
  the browser receives a bounded log excerpt through the control plane and never
  receives the worker bearer credential.
- Finalized restarted verification effects from persisted terminal command outcomes,
  preserving candidate, infrastructure, and timeout classification, and kept live
  output polling after transient request failures while the run remains active.

## 2026-08-05 - Durable Candidate-Correction Successor Recovery

- Made automatic successor-packaging blockers typed, downstream-scoped incidents so
  they no longer invalidate a terminal correction task or recursively stall its dependents.
- Added Admin Web/API actions to retry packaging, create a dependency-free recovery task
  pinned to the accepted correction branch and commit, or authorize an audited verification
  waiver; generic blocker resolution cannot bypass this lifecycle.
- Kept a created verification-replacement task visible on its blocker with task, assignee,
  commit, dispatch, and status details; replaced duplicate creation with a task-board link and
  recorded creation as durable blocker progress.
- Let reconciliation retry while its own blocker remains open, consume passed downstream
  recovery deliverables, and resolve stale blockers when a direct successor already exists.
- Migrated legacy `candidate-correction-successor:*` blockers and persisted optional immutable
  task base-commit pins that are enforced when Git work is prepared.
- Excluded candidate-correction recovery deliverables from ordinary initial-release tracking so
  they remain attached to the rejected release lineage during successor reconciliation.

## 2026-08-05 - Human Review Checkbox Accessibility

- Enlarged human-review checklist and carried-finding checkboxes, removing inherited
  form-control padding so their visible controls match their clickable labels.

## 2026-08-05 - Managed Git Administration Page

- Added a **Git Repositories** Admin Web page for directory-scoped managed-Git catalog search and inspection.
- Let unlocked operators register repositories, refresh upstream refs, run repository maintenance,
  change host commit identity, and delete unbound repositories without falling back to the CLI.

## 2026-08-05 - Focused Agent Codex Usage Editor

- Removed session-owned model, expiry, labels, tools, grants, communication access,
  web search, and HTTP target controls from the Agents page editor; those settings
  remain editable on the Sessions page.
- Limited the remaining agent-specific editor to container Codex usage mode and
  hid the edit action for agents without that setting.

## 2026-08-05 - Durable Initial Release Decisions

- Tracked newly accepted ordinary root deliverables in release-configured
  projects as durable pending initial-release decisions without automatically
  creating the first release package.
- Added exact-scope manager-fallback recovery until a project lead or Team
  manager requests a release or records an audited not-planned reason, with an
  audited reopen path and compatible-deliverable batching guidance.
- Exposed initial-release disposition and audit evidence in Team Delivery while
  leaving legacy accepted deliverables untracked to avoid migration wake floods.

## 2026-08-04 - Retained Wake Role-Description Updates

- Included a persistent agent's full updated role description in retained Team
  wake context, while continuing to omit it when unchanged.

## 2026-08-04 - Fail-closed Team Chat Authorization

- Failed mutation-authorized Chat messages before agent resume when their
  snapshotted project scope narrows to empty at lease time, revoked stale Team
  tool grants, and rejected empty-scope targetless mutations at command
  validation.
- Made reply-only Chat text-only: attachment staging and previously staged file
  sends now require a non-empty project-scoped Chat dispatch.

## 2026-08-04 - Focused Workspace Delivery

- Removed the redundant Workspace detailed-tab shortcut grid and placed the
  Delivery summary directly below Current workload in the status column.

## 2026-08-04 - Workspace Attention Pagination

- Added Previous and Next controls to the Team Workspace attention list, with a
  visible item range and current-page indicator.

## 2026-08-04 - RBAC-scoped Team Chat Mutations

- Preserved broad project and managed-repository scopes for mutation-enabled
  Team Chat while requiring the human sender to hold the new directory-scoped
  `team_chats.mutate` permission over both the recipient agent and included
  projects; built-in Owners and Operators retain this capability.
- Snapshotted server-derived chat mutation grants at send time, made missing or
  legacy mutation metadata reply-only, bounded external bridges to their
  workflow directory, and exposed reply-only versus mutation-enabled mode in
  the Chat UI.

## 2026-08-04 - Current Delivery Attention

- Excluded retired and superseded review requests, and superseded deliverables,
  from Workspace and Delivery attention while retaining their audit history.

## 2026-08-04 - Focused Team Workspace Attention

- Removed operator and governance directives from the Team Workspace attention
  dashboard while retaining them in its neutral inbox totals and the receiving
  agent and team detail views.

## 2026-08-03 - Browser Voice Reconciliation Context

- Bound browser voice sessions to the authenticated operator, identify transcript
  turns by the operator's display name and user ID, and reject another operator's
  event, query, or end request for that call.
- Added the current compact Team working set to the agent's terminal browser-voice
  reconciliation wake.
- Kept terminal voice reconciliation reliable for agents without runtime rows,
  prioritized open blockers and actionable reviews before context caps, and
  preserved ISO timestamps while redacting sensitive context values.

## 2026-08-03 - Stand-down Revocation Conflict Safety

- Included direct stand-down lifecycle in Team agent resource tokens so a stale
  operator request cannot revoke an unseen new hold or enqueue a resulting wake.

## 2026-08-03 - Session Contact Allowlist Editor

- Added nullable contact-access controls to the Admin Sessions policy editor so
  operators can retain all directory contacts, deny every contact, or persist
  an explicit contact-ID allowlist without editing session manifests directly.

## 2026-08-03 - Operator Revocation of Agent-Issued Stand-downs

- Allowed human operators to revoke a direct Team agent-issued stand-down from
  the existing Admin control, while retaining agent-to-agent ownership limits
  and root-only revocation for inherited subtree holds.

## 2026-08-03 - Scrollable Team Chat Recent Conversations

- Kept the recent-conversation sidebar within its available panel height so long chat lists scroll independently without overflowing the page.

## 2026-08-03 - Exact Agent Detail Authorization

- Authorized Admin Web agent detail responses against the exact backend record
  returned, including every directory associated with a shared VM.

## 2026-08-03 - Canonical Publication Lineage Repair

- Persisted the canonical destination's pre-release commit separately from a
  correction successor's rejected-candidate merge base, so reviewed correction
  releases publish against the unchanged canonical branch.
- Allowed a missing generated canonical branch to be created directly at the
  approved commit only when the pinned destination base is its ancestor, using
  an empty-ref lease to reject concurrent branch creation.

## 2026-08-03 - Explicit Human Review Finding Scope

- Required every new human-review change finding to select either the entire
  release candidate or one or more named affected deliverables, with a
  multi-select UI and matching server-side validation.
- Cleared stale affected-work validation when a reviewer switches from an
  invalid request-changes attempt to approval.
- Routed whole-candidate, multi-owner, and unavailable-owner findings directly
  to the project lead without ambiguous-routing stalls; retained the ambiguous
  flag only for recovery of legacy scope-less findings.

## 2026-08-03 - Completed Human Correction Reconciliation

- Reconciled stale ambiguous-routing metadata once its generated correction
  task is resolved with exactly one accepted, review-ready deliverable, allowing
  Team to create the required human-review successor release automatically.
- Preserved explicit routing for ambiguous corrections that are unresolved,
  unaccepted, or not review-ready.

## 2026-08-03 - Team Canonical Publication Base Pinning

- Bound Team canonical publication to the immutable release base commit so a
  release reviewed relative to an alternate task base cannot carry that base's
  pre-existing changes onto a different canonical destination.
- Kept `effect:git:push-branch` backward compatible for existing workflows by
  making destination-base verification additive, while requiring the packaged
  Team canonical-push route to supply the release base commit.

## 2026-08-03 - Azure DevOps Organization Identity Hardening

- Restored per-node Azure DevOps `project` overrides for pull-request update and tag effects,
  while replacing caller-controlled provider URLs with `ado_organization` identifiers that always
  construct a trusted `https://dev.azure.com/<org>` origin before a PAT-bearing request.
- Preserved safe legacy configuration and persisted workflow compatibility by migrating only
  public Azure DevOps organization URLs; arbitrary origins are rejected across PR queries,
  workflow effects, Azure DevOps feedback/completion polling, and Azure Pipelines completion
  polling.

## 2026-08-03 - Workflow PR Provider Target Hardening

- Prevented pull-request update and tag effects from combining workflow-controlled Azure DevOps
  targets or out-of-scope directories with directory-scoped PAT credentials.

## 2026-08-03 - Container Worker Git Grant Preservation

- Preserved session-scoped SSH alias blocks when refreshing the host-only worker
  Git credential, and reserved the legacy `evander-git` filename so new grants
  cannot be mistaken for migrated broad credentials.

## 2026-08-02 - Team Artifact Import Authorization

- Required imported artifact spaces and published versions to belong to the caller's exact Team,
  preventing copied deliverable references and default-open grants from exposing cross-team data.
- Kept same-Team artifacts importable without an explicit deliverable handoff while preserving
  stricter per-space read grants.

## 2026-08-02 - Team Recovery Authorization Hardening

- Scoped deliverable-replacement and blocked-review recovery manager authority,
  reviewer selection, and review-profile validation to the affected project's team.

## 2026-08-02 - Rejected Deliverable Review Integrity

- Prevented formal review from overriding a direct superior rejection and added
  model-level validation that terminally rejected handoffs cannot become accepted.
- Added an upgrade migration that restores historically overridden rejections,
  retires their invalid formal-review paths, reopens replacement work, and flags
  downstream release history for explicit reconciliation.

## 2026-08-02 - Bound Task Worktree Cleanup

- Bound ignored-file cleanup to an open descriptor for the worktree validated under the
  repository lock, preventing path replacement and symlink races from redirecting `git clean`.
- Anchored repository and worktree traversal at the stable session home, refusing symlinked
  parent components and retaining the validated descriptors through Git cleanup and removal.
- Refused cleanup when an absent task worktree appears during final validation, so no mutation
  can proceed without a descriptor bound to the validated directory.

## 2026-08-02 - Team Review Cohort Authorization

- Scoped review-cohort creation by team managers to deliverables belonging to
  their own team's projects, preventing cross-team cohort mutation and review
  context disclosure.

## 2026-08-02 - Agent Detail Directory Authorization

- Required directory-scoped owner authorization before returning sensitive agent
  detail records or container execution history from the Admin Web API.

## 2026-08-02 - PR Preflight Provider Target Hardening

- Removed request-level Azure DevOps organization and project overrides from the Admin Web PR
  integration preflight, ensuring directory-scoped PATs are sent only to directory-configured
  provider endpoints.

## 2026-08-02 - Container Worker Git Credential Isolation

- Kept the same-host worker's broad Git SSH credential in a host-only session
  directory so container jobs cannot read it through the agent-home mount.

## 2026-08-02 - Team Chat Notification Deep Links

- Open notification clicks directly in Team Chat with the specific conversation
  that produced the message.

## 2026-08-02 - Team Chat Keyboard Sending

- Send Team Chat messages with Enter while preserving Shift+Enter for multiline
  messages.

## 2026-08-02 - Team Dependency-Wait Labels

- Distinguished tasks waiting on unfinished dependencies from tasks carrying
  assigned blockers in Task board summaries, cards, hierarchy badges, details,
  and technical metadata.
- Counted blocked prerequisites individually in mixed dependency waits and
  suppressed active wait labels once a task is terminal or otherwise resolved.

## 2026-08-02 - Rejected-Candidate Reconciliation Follow-ups

- Waited for every current release review before creating an automatic
  correction successor, excluded cancelled correction tasks, and rejected
  deduplicated packages that do not directly supersede the failed release.
- Preserved fresh actionable release notices when an ordinary root deliverable
  is reopened and reaccepted, while retaining handled automatic-correction
  history.
- Aligned verification and agent-review correction task prompts with automatic
  successor creation.

## 2026-08-02 - Lossless Retained Team Wake Deduplication

- Removed retained-wake runtime-attention rows only after carrying their scoped
  project and team associations into the included inbox item, preserving links
  derived from compacted nested or legacy payloads.
- Removed only outer Git fields that exactly duplicate a sole active work
  context; unique repository and dependency context remains in the prompt.

## 2026-08-02 - Automatic Rejected-Candidate Correction Releases

- Created one deterministic successor release atomically when a generated
  verification or agent-review correction resolves with accepted, reviewed Git
  content, without requiring a project-lead release request.
- Added idempotent scheduled and post-dispatch reconciliation for stranded
  corrections, informational handled manager history, and a durable owner-routed
  blocker when automatic successor validation or packaging fails.
- Kept initial releases and post-release-feedback successors explicit while
  documenting the automatic generated-correction boundary for agents and
  operators.

## 2026-08-02 - Human Correction Case Reconciliation

- Resolved human-review correction cases when their deterministic successor
  release has been created, including one-time repair of stale active cases
  whose successor and coordination task already exist.
- Kept correction cases active when successor creation only deduplicates against
  an active package that does not supersede the rejected predecessor.

## 2026-08-02 - Terminal Release Attention Cleanup

- Kept accepted and rejected Team release packages in immutable release history
  while excluding their stale failures from Workspace attention and agent wake
  prompts. Active blocked releases remain actionable without duplicating their
  recovery context in runtime attention.

## 2026-08-02 - Per-Task Git Base Snapshots

- Added durable Git base-branch snapshots to Team tasks, releases, and promotion
  records so work based on `main` and feature branches can proceed concurrently
  without later project-default changes retargeting preparation or publication.
- Added task/API/MCP/Admin controls, exact base-commit propagation, mixed-base
  release rejection, correction/recovery provenance, and SQLite migration for
  active legacy workstreams.
- Pinned verification and release-review correction tasks to the rejected
  candidate branch, repaired still-unprepared correction tasks created with an
  upstream base, and invalidated preparation blockers when that base changes.
- Preferred a legacy release's complete candidate-manifest branch over its
  upstream Git base when repairing verification and release-review corrections.
- Preserved manifest-only candidate branches when creating human-review and
  post-release correction tasks and when building correction successors.
- Resolved unset project bases through the managed repository default before
  persisting task snapshots, and hardened the SQLite backfill for correction
  tasks, correction successors, conflicting legacy contexts, and JSON imports.

## 2026-08-02 - Team Chat Membership Scope

- Restricted agent wake context and outbound Team Chat messages to direct
  conversations the calling agent already belongs to. Other agents' operator
  conversations can no longer appear as recipients or be used to create a new
  direct chat.
- Made each wake's participant list an explicit complete replacement, including
  an empty list, so same-context sessions revoke stale recipients without a
  context rotation.

## 2026-08-02 - Team Review Routing

- Prevented manager-fallback routing gaps from minting fresh review-request
  authority for deliverables that already belong to a review cohort. Sealed
  cohort reviews now remain with their assigned member, while blocked members
  continue through the explicit retry/replacement path.

## 2026-08-02 - Workflow Mailbox Grant Policy Enforcement

- Validated workflow-created agent mailbox grants against the effective directory policy instead
  of provider mailbox defaults, preventing workflows from granting unauthorized mailbox, folder,
  or send access.

## 2026-07-31 - Review Follow-up Fixes

- Kept selected Agent detail views live by merging each fresh polled summary over
  the richer loaded detail, including status, capabilities, execution counts,
  and prompt-queue aggregates.
- Preserved compact Agents-page Codex usage badges by retaining only the
  effective authentication mode, its source, and the agent provider in summary
  payloads; Dashboard-only inventory remains policy-free.
- Reserved `EVANDER-REVIEW.json` in human-review source bundles so candidate
  archives cannot supply a conflicting manifest entry or normalized alias.
- Updated the Team wake-node catalog contract for the global-maintenance field.

## 2026-07-31 - Admin Inventory And Dashboard Read Performance

- Allowed downloads from add-on iframes in the Admin Web shell, with browser
  coverage for downloading a review bundle from the Team add-on.
- Made summary Agents inventory reads use per-session execution aggregates and
  omit policy metadata and execution histories. The selected agent now loads
  its detail separately, with cursor-paginated container execution history.
- Restored selected container-agent logs by deriving their availability from
  the persisted execution summary, rather than the omitted history payload.
- Switched normal Agents-page polling to cached, state-omitting summaries;
  explicit refreshes still perform live reconciliation and VM-state reads.
- Kept the Dashboard's 24-hour workflow-activity semantics while aggregating
  SQLite activity by graph, node, source, and status before building the
  response instead of materializing every raw orchestration record.

## 2026-07-31 - Team Wake Latency And Bounded Cleanup Evidence

- Replaced routine full Team-state rewrites with record-level SQLite deltas;
  no-op eligibility and Chat claim checks now preserve the state revision and
  issue no normalized state writes.
- Kept global correction, human-review assignment, and task-worktree cleanup
  maintenance on scheduled wakes while targeted Chat and requested-agent wakes
  proceed directly to eligibility and dispatch.
- Bounded persisted ignored-file cleanup evidence to a deterministic 200-path
  sample plus the exact file count, full-list SHA-256, byte totals, and a
  truncation marker, including automatic migration of existing candidates.

## 2026-07-31 - Fresh Team Task Bases

- Made first-time libvirt Team task worktrees fetch their configured managed-Git
  base before pinning it, matching the container-worker backend. New independent
  tasks now start from the current canonical base while reused worktrees retain
  their original immutable pin.

## 2026-07-31 - Human Review Source Bundles

- Added a **Download review bundle** action to Team human-review cards. It
  serves an authorized reviewer a source-only ZIP for the exact pinned release
  candidate, with an `EVANDER-REVIEW.json` provenance manifest and no Git
  history or credentials.

## 2026-07-31 - Agent Release-Review Corrections

- Restored configured-policy review routing for candidate-correction leaves: top-level
  agents can submit normally without inventing a superior, while contracted leaf tasks
  still require an accepted Git deliverable before a replacement release can begin;
  generated correction instructions leave routing decisions to team-specific guidance.
- Kept true integration boundaries outside leaf-review automation, allowed workflow
  policy to attach fresh reviews to rejected-deliverable replacements, and retained
  explicit manual review submission for deliberately manual teams.
- Corrected the accepted-task manager-fallback regression to assert suppression of
  non-actionable wakes and to evaluate eligibility after its test state is created.
- Made rejected agent release reviews create one high-priority, candidate-rooted
  correction task for the recorded release owner, with an immediate durable
  wake and project-lead or team-manager fallback when needed.
- Persisted the package-to-correction-task link, aggregated concurrent reviewer
  findings into that task, and issued a fresh durable wake for each new rejection.
- Extended rejected-candidate worktree validation to agent release-review
  corrections so implementation starts from the reviewed candidate snapshot.
- Required candidate corrections to resolve through an accepted Git deliverable,
  extended successor-release validation and aggregation to rejected agent reviews,
  and made preparation recovery cover every candidate-rooted correction route.
- Routed source-provenance mismatches through the standard terminal recovery
  inbox, and scoped candidate-correction branches and worktree identities to
  the pinned rejected candidate.
- Persisted verification infrastructure and timeout wakes, retained high-priority
  Team visibility when human-review recovery routes to an Admin owner, and routed
  configuration or invariant gate rejections through a durable Team blocker.

## 2026-07-31 - Team Correction Worktree Recovery

- Made persistent container-worker clones fetch newly created Team release
  branches before preparing correction worktrees, while verifying the exact
  pinned candidate commit and tree before creating the task branch.
- Released the scheduled wake interval when dispatch preparation fails before
  inference, and versioned preparation generations so tasks blocked by the
  repaired worktree contract receive one immediate bounded retry.

## 2026-07-31 - Team Task Board Briefs

- Made each Team Task Board card and hierarchy node expose its task brief; long
  instructions stay compact until the operator opens **Show full task brief**.

## 2026-07-31 - Release Verification Failure Evidence

- Surfaced the failed verification command, exit code or worker failure cause,
  and a concise decisive error directly in failed release progress.
- Added readable, expandable command-log excerpts to release detail and
  Delivery Pipeline gate evidence so operators can diagnose exact failures
  without searching other views.

## 2026-07-30 - Candidate-Rooted Verification Corrections

- Made candidate verification failures create one high-priority correction task
  automatically, with the failed command results, release branch, candidate
  commit and tree, and assignment rationale recorded in the task and inbox.
- Routed the task to the unique producer whose reviewed paths appear in the
  verifier evidence, then to the recorded release owner, project lead, or team
  manager when provenance is ambiguous or unavailable.
- Linked verification runs durably to their correction tasks and made worker
  worktree preparation verify and start from the exact rejected candidate
  snapshot, without composing dependencies onto current `main`.
- Added explicit release ownership to new release packages and persisted it
  through the SQLite schema.

## 2026-07-30 - Subscription Team Tool Attachment

- Added the session-scoped Evander MCP server to the worker-owned ChatGPT
  subscription config before Codex app-server startup. Subscription turns now
  receive Team action tools instead of relying on a late thread-resume override
  that does not initialize the MCP connection.
- Preserved the baseline session gateway for container prompts without a
  temporary run-scoped approval lease, while retaining lease-qualified gateway
  URLs when a workflow grants additional tools for one run.
- Kept the session credential out of app-server process arguments and logs by
  retaining the generated config as a worker-owned, read-only mount.

## 2026-07-30 - Empty Manager Fallback Suppression

- Stopped periodic manager-fallback dispatches when a team appears open only
  because non-superseded pending deliverables remain attached to terminal
  tasks, with no nonterminal work, pending review or inbox, failed action, or
  review-routing gap that the manager can advance.
- Preserved manager fallback wakes for genuinely actionable team oversight and
  review-routing work.

## 2026-07-30 - UI Release Recovery Resumption

- Replaced the misleading generic blocker-resolution action for routed release
  blockers with a lifecycle-aware **Resume release recovery** control.
- Made that control queue a task-linked operator directive and immediate wake
  for the existing merge-recovery owner. The release recovery effect remains
  responsible for resolving the blocker and finishing the recovery task.
- Suppressed duplicate UI recovery wakes while an earlier recovery directive
  remains pending.

## 2026-07-30 - Workflow Generator Model Compatibility

- Stopped sending the unsupported `temperature` parameter from workflow-generation
  Responses API requests, allowing models that reject sampling controls to generate
  draft workflows.

## 2026-07-30 - Agent Subscription Scope Clarity

- Moved the shared worker ChatGPT subscription account out of the selected-agent
  detail region and labeled its page-wide scope explicitly.
- Added each container Codex agent's effective Metered API or ChatGPT
  subscription mode, including whether it comes from an agent override,
  directory default, or provider-route constraint, to the agent list and detail
  view.
- Warned when the shared worker account is signed in while the selected agent
  remains on metered API usage.

## 2026-07-30 - Automatic Release Merge Recovery Work

- Materialized one idempotent package-level task when an active feature,
  correction-aggregate, or release promotion boundary reports merge conflicts,
  preserving promotion IDs, branches, conflict details, and supported recovery
  instructions.
- Routed unambiguous conflict work directly to its active source coder with an
  immediate durable wake when runnable, while mixed or unavailable ownership
  falls back to the project lead or team manager. Active stand-downs retain the
  work without waking until revocation.
- Granted the active recovery-task owner narrow authority to submit the
  resolved agents ref through the existing release recovery command.
- Linked the recovery task to the existing release blocker and preserved the
  blocked package and promotion audit trail without retrying or resolving Git
  conflicts automatically.

## 2026-07-30 - Package-Level Human Correction Successors

- Replaced per-finding feature promotions for rejected human-review successors
  with one deterministic correction aggregate rooted at the immutable rejected
  candidate, while retaining every finding, deliverable, review, and manifest
  as separate evidence.
- Added pinned-source ancestor enforcement and rejected-candidate-derived
  manifests before one final integration with current `main`; release gates now
  evaluate that final candidate.
- Added idempotent reconciliation for candidate-less legacy successors:
  per-leaf failures remain as retired audit records, obsolete recovery
  tasks/blockers are safely closed, and one aggregate promotion is queued
  without trusting legacy main-based manifests.
- Kept correction-aggregation and aggregate-to-main conflicts as distinct
  serial recovery boundaries, each reusing its failed promotion's pinned base.

## 2026-07-30 - Durable Review Manifests on Promotion Failure

- Persisted the pinned source manifest produced by Managed Git even when a
  feature promotion ends in a merge conflict, preventing the conflict from
  making its own reviewed evidence unavailable to recovery.
- Added a revision-guarded repair service for legacy blocked packages whose
  failed promotions contain one unambiguous manifest matching the deliverable's
  accepted immutable review; retired and superseded reviews remain excluded.
- Kept release recovery promotion-specific. Human-correction successors expose
  one active aggregate or release boundary; generic multi-feature packages
  retain one-at-a-time recovery only where those feature boundaries genuinely
  remain independent.

## 2026-07-30 - Team UI Record Ordering

- Sorted Delivery releases and related release/review history newest-first by
  their available publication, update, or creation timestamps instead of
  opaque record IDs.
- Audited Team UI collection ordering and aligned named record lists, member
  lists, hierarchy roots, and selectors with their displayed labels; inbox
  items now retain the workspace's priority-first order.

## 2026-07-30 - Team Correction Guidance Alignment

- Updated versioned Team-agent guidance for generated human-review correction
  graphs: finding children now explicitly require one accepted, reviewed
  deliverable rather than direct support-task completion, while the
  coordination parent, derived finding resolution, and changed successor
  release remain system-managed.
- Aligned the Team Add-on reference and release manual with that automatic
  correction flow and added documentation and prompt regression coverage.

## 2026-07-30 - Session Container Resource-limit Documentation

- Documented how operators configure per-session container CPU, memory, and
  process/thread limits through Admin Web session metadata, including accepted
  fields, new-job timing, Playwright precedence, worker defaults, and the
  separate Team release-verifier policy.

## 2026-07-30 - Quiet Subscription App-Server Startup

- Isolated ChatGPT-subscription app-server configuration from the metered relay while preserving
  the session's durable Codex threads and skills, eliminating project-local provider warnings and
  API-key model-catalog refresh failures without injecting credentials or changing billing mode.
- Added the system Bubblewrap package to the managed runner image so Codex no longer warns before
  falling back to its bundled sandbox helper.

## 2026-07-30 - Automatic Human Review Correction Graphs

- Replaced lead-authored correction reconstruction with one deterministic
  coordination parent and one immutable-text child task per human finding.
  Single-candidate and single-owner findings route directly; only genuinely
  ambiguous findings require an explicit owner route.
- Made affected deliverables optional in the human review form. The platform
  derives a sole candidate automatically and presents named candidate work
  choices instead of opaque deliverable-ID text fields when a choice exists.
- Pinned correction worktrees to the rejected release commit and tree, with
  authoritative managed-Git verification and idempotent preparation recovery.
- Rejected retained libvirt correction worktrees whose current head no longer
  descends from the pinned rejected candidate.
- Added automatic resolution evidence derivation and changed-successor release
  packaging after all correction children have accepted, reviewed work.
- Preserved cancelled and other terminal correction coordination parents when
  reconciliation creates or observes the successor release.

## 2026-07-30 - Local Container Worker Subscription Login

- Made same-host container-worker setup units start the managed Codex
  authentication app-server from the configured runner image. Device login no
  longer falls back to a missing `codex` executable on the worker host.
- Exported that image through the service environment too, so an operator
  drop-in that replaces only `ExecStart` retains the managed auth container.

## 2026-07-29 - Explicit Review Closure Steering

- Interrupted and resumed a matching leased container-worker review dispatch
  when an authorized withdrawal or retirement makes that review obsolete, with
  a bounded instruction to stop its testing while preserving unrelated
  dispatch scope.
- Added atomic single-winner, one-shot steering for container and legacy
  executions, preventing concurrent closure replays from interrupting the same
  dispatch twice while keeping durable review closure authoritative when
  post-commit execution steering is unavailable or fails.
- Kept human-supplied closure reasons in the audit record instead of forwarding
  them into the privileged reviewer steering prompt; the prompt now contains
  only a fixed, bounded closure instruction.

## 2026-07-29 - Immediate Human-review Correction Wakes

- Published durable correction wake events immediately from human-review
  changes-requested and post-release issue API responses, while retaining the
  transactional outbox for recovery. Idempotent request replays reuse the
  original event identity instead of waiting for the quarter-hour Team cron or
  creating duplicate wake events.

## 2026-07-29 - Structured Human-review Findings

- Replaced the changes-requested free-text convention with a flat repeatable
  findings editor. Reviewers now explicitly add or remove findings and enter
  each description, optional target deliverable IDs, and optional evidence
  references independently.
- Preserved every finding row and its unnormalized in-progress field values,
  focus, and text selection across background refreshes. Submission rejects
  blank or metadata-only rows, retains commas in finding prose, and includes
  structured findings only for changes-requested decisions.

## 2026-07-29 - Team UI Draft-safe Background Refresh

- Preserved unsaved, request-scoped human-review decisions across Team state
  stream, cost, release-detail, and partial view renders, including text,
  checklist and carried-finding confirmations, open fields, focus, and caret.
- Extended the same dirty-form boundary to scoped project/release forms and
  unsubmitted case/task searches, protected drafts during in-flight saves, and
  retained unsent Team Chat text independently per conversation. Successful
  submissions still clear only the completed draft.

## 2026-07-29 - Feature-branch Documentation Alignment

- Updated the Team Add-on, managed-Git, configuration, operations, and canonical
  manual sources to match the branch's current delivery, review, context,
  stand-down, cleanup, chat, and release-recovery contracts.
- Refreshed the Team Delivery manual screenshot for the attention-first,
  project-focused workspace and brought its deterministic browser fixture up to
  date with the current Admin Sessions API.

## 2026-07-29 - Persistent Team UI Disclosures

- Preserved every dynamic Team Add-on disclosure across state-stream, cost, and
  view re-renders, including operations, planning, projects, task board,
  release and human-review details, delivery pipeline, timeline, and mail.
  Operator-opened panels now retain their own stable record key instead of
  inheriting the browser's replaced DOM state.
- Scoped shared-renderer disclosure keys by Team view and project-owned goal
  keys by project, preventing hidden copies and repeated local goal IDs from
  overwriting one another during background renders.

## 2026-07-29 - Team Lifecycle Review Repairs

- Continued manual and idle context rotation in a fresh context when a bounded
  handoff run completes without recording its advisory summary, preventing
  repeated handoff-preparation dispatches.
- Preserved self-observed child-resolution transitions as handled parent
  history without scheduling a redundant wake.
- Included prepared Team Chat attachment IDs and metadata in external-reply
  events and rendered explicit attachment references into the outbound body,
  so attachment-only replies remain deliverable and files are not silently
  omitted from bridged text.

## 2026-07-29 - Supported Human-review Candidate Checkout

- Added an authorization-filtered **Test this candidate** panel to Team release
  detail with the canonical repository identity, managed repository ID,
  immutable release ref, exact candidate commit/tree, and a copyable supported
  host command.
- Added `evander-cli team checkout-release-candidate`, which resolves private
  managed-Git paths internally, revalidates the pinned ref, commit, and tree,
  and creates a fresh detached checkout without retaining an internal path.
  Existing targets and paths inside the Evander runtime are refused. The
  generated command carries the absolute runtime root and assembles only the
  narrow managed-Git service, so it works outside the Evander checkout without
  loading unrelated provider credentials.

## 2026-07-29 - Immediate Team Outbox Drain After Standalone Dispatch

- Reused the Team outbox publisher after lifecycle-hook finalization of
  standalone agent prompt dispatches, so Git promotion and other durable intents
  created during those runs no longer wait up to 15 minutes for the Team cron.

## 2026-07-29 - Deterministic Team Correction Git Sources

- Clarified the Team task-creation and release-failure contracts so correction
  lineage (`originating_task_id`) is never mistaken for Git inheritance.
  Project leads must name accepted Git predecessors in `dependency_task_ids`,
  which makes the runtime verify and compose their exact commits before work
  starts; failed-candidate notices now require the candidate SHA and prohibit
  silently delegating correction work from a base-branch-only checkout. The
  assignee must verify that the prepared worktree has the candidate's exact
  tree before editing rather than selecting or merging a source ad hoc.

## 2026-07-29 - Accurate Operator Escalation Receipts

- Marked operator and governance escalation receipts explicitly as pending and
  told Team agents to report them as queued, not delivered, until downstream
  delivery evidence exists. Tool discovery and runtime descriptions now make
  the same asynchronous boundary explicit.

## 2026-07-28 - Focused Review and Delivery Pipeline

- Changed Review & Delivery to open on attention-requiring work, added a
  project focus selector, and collapsed project groups until an operator opens
  or selects one, while retaining status filters and an explicit all-records
  view for complete history.
- Added an evidence-backed deliverable pipeline from submitted implementation
  evidence through deliverable acceptance, release inclusion, automated
  verification, release-agent review, human review, and publication. The
  drill-down names the current stage and active blocker and can load the exact
  permission-filtered release gate attempts on demand.

## 2026-07-28 - Bounded Agent Context Self-handoffs

- Added a 120-second, best-effort same-context handoff preparation for manual
  and idle Team context rotations. Agents can record one server-capped advisory
  summary before the fresh context starts; failure or timeout never blocks the
  reset. Every-wake agents retain only the existing deterministic checkpoint.

## 2026-07-28 - Bounded Agent Release-verification Retry

- Preserved one project-lead retry for transient release-verifier failures, but
  prevented agents from repeatedly retrying an unchanged candidate after an
  agent-requested verification attempt also fails. Operator recovery remains
  available, while blocker, prompt, MCP, and UI guidance now requires concrete
  recovery evidence and directs agents to escalate when none exists.

## 2026-07-28 - Project-lead Wake on Candidate Verification Failure

- Turned a release candidate verification rejection into actionable project-lead
  inbox work with a transactional wake intent, directing the lead to inspect
  the frozen evidence and delegate changed reviewed work without waking the
  original coder directly.
- Included the failed command result and its bounded log context directly in
  that project-lead notice, and changed long verifier excerpts from tail-only
  capture to balanced beginning-and-end capture so an early compiler/test
  diagnostic is not hidden by later suite output.

## 2026-07-28 - Retryable Release-verifier Resource Exhaustion

- Gave Team release-verification container jobs a bounded 8-CPU quota and a
  2,048 process/thread budget, preventing large MSBuild solution runs from
  multiplying nodes across every host CPU while accommodating their observed
  parallel test-host thread count above the strict worker profile's default
  512-PID ceiling.
- Added validated per-project `release_policy.resource_limits` configuration
  for worker memory, CPUs, and process/thread limits. Explicit values override
  the bounded verifier defaults and are applied to each new verification
  session, including infrastructure retries.
- Added a worker-owned, structured `pids_limit_exhausted` failure cause based
  on the job cgroup's kernel `pids.events` counter and propagated it through
  container execution into release-verification evidence. Verification no
  longer guesses infrastructure failure from ambiguous command OOM text;
  Docker's causal OOM-kill signal is likewise exposed as
  `memory_limit_exhausted`.

## 2026-07-28 - Non-blocking Worker Worktree Cleanup

- Moved container-worker task-worktree cleanup off the async request loop, so
  slow ignored-file deletion cannot starve unrelated session and file
  preparation requests.
- Moved the worker's remaining synchronous session, Git, file, Docker, MSSQL,
  telemetry, authentication, job, log, and artifact operations off the async
  request loop as well, keeping health and unrelated requests responsive while
  a slow lifecycle operation completes.
- Batched the final inspection's proven ignored paths into bounded Git cleanup
  commands and applied a subprocess timeout, avoiding one full Git scan per
  ignored file while preserving the no-force, exact-path cleanup contract.

## 2026-07-28 - Immediate Team Wake on Operator Stand-up

- Made operator stand-down revocation atomically enqueue targeted
  `team.agent.wake_requested` intents for affected agents that become eligible
  for work, so an explicit stand-up no longer waits for the next 15-minute Team
  cron tick. Agents still covered by another hold remain suppressed.
- Suppressed handoff, rejection, and resolution parent-task notices when the
  command's effective sender is also the parent owner, avoiding inference runs
  whose only action is to acknowledge a transition the agent just caused.
  Actionable integration-ready and blocker notices remain wakeable.
- Made direct deliverable acceptance requests normal-priority actionable work
  and enqueue the same targeted wake intent used by formal review requests, so
  an idle superior no longer waits for the periodic Team scheduler.
- Fixed final deliverable-review acceptance so parent resolution and
  integration-ready notices retain their wake-request events and wake an idle
  parent owner without waiting for scheduled polling.
- Returned accepted root-task boundaries to the project lead with an actionable
  release-ready notice and wake, so the release package and required human
  review cannot stall merely because the resolved task has no parent.
- Added structured verification-waiver correction context to rejected release
  requests, including whether the current caller is authorized, the exact
  input shape, and the fact that candidate verification and review gates still
  apply.
- Refreshed the Team runtime workflow pack to retain Git promotion manifests,
  base commits, changed-path validation, and tree evidence through the
  recorder effects, preventing a successfully reviewed candidate from being
  blocked later by evidence dropped at the workflow boundary.
- Fixed feature-promotion result recording to attach that immutable reviewed
  source manifest when a promotion is already linked directly to its accepted
  deliverable, preventing valid releases from being blocked as though their
  reviewed manifest were missing.

## 2026-07-28 - Team Stand-down Completion and State Upgrades

- Allowed a support-task dispatch leased before a stand-down to record its
  exact scoped `finished` transition while continuing to reject task edits,
  new work, and dispatches created after the hold.
- Upgraded persisted Team state metadata to the current state schema during
  SQLite initialization and legacy JSON import instead of retaining an older
  advertised schema version after the new fields are available.

## 2026-07-28 - Hierarchy-aware Team Review Wakes

- Required assigned deliverable reviewers to fetch and read the complete task
  specification before deciding. Leaf reviews compare directly against that
  task, while composite and integration reviews recursively inspect declared
  contribution tasks, their current accepted source deliverables, and actual
  integrated behavior rather than relying on source-ID coverage alone.

## 2026-07-28 - Workflow-created Team Review Cohorts

- Added an atomic Team workflow effect that creates and seals the complete
  reviewer set for an existing immutable deliverable in one state transaction.
  Exact workflow retries are idempotent, while mismatched membership and
  pre-existing independent reviews fail closed.
- Moved the deliverable task into `waiting_for_review` when the cohort is
  sealed, preventing its owner from being dispatched against a candidate under
  review, and recalculated completed projects when accepted work is reopened.
- Preserved accepted and rejected cohort lifecycle state on exact workflow
  retries instead of reopening immutable terminal reviews with no remaining
  decision capable of advancing the task.
- Exposed the cohort effect and its outputs in the workflow catalog so
  integration-boundary deliverables can request one coordinated code-and-test
  acceptance pass without reviewing every intermediate contribution.

## 2026-07-28 - Lifecycle-aware Team Wake Contexts

- Made first, manually rotated, `every_wake`, and explicitly new-context Team
  wakes receive a compact complete working set, while successful same-session
  retained wakes receive current-dispatch deltas. The choice is based on durable
  context lifecycle and resume behavior, never agent display names or provider
  naming.
- Added `team_get_current_dispatch_context` to hydrate the caller's immutable
  dispatch scope, scoped runtime attention to that dispatch, preserved exact
  task integration contracts and ephemeral artifact import paths, and removed
  duplicate repository/worktree representations.
- Kept Team Chat current messages exact and singular, bounded historical
  transcript bodies, and applied reset/`every_wake` semantics to chat. Only a
  successful mutation-enabled chat seeds ordinary Team context; reply-only chat
  leaves a pending ordinary reset intact. Mutation-enabled chat retains its
  role-visible project and managed-repository scope so the operator can select
  exact work at tool-call time without pre-claiming current task or review IDs.
- Reduced context-rotation handoffs to bounded irreducible continuity, retaining
  the manual operator note and pending ephemeral acceptances without repeating
  identity, task, inbox, review, deliverable, or action history.

## 2026-07-28 - Sealed Team Review Cohorts

- Made atomic multi-review deliverable submission create one persisted, sealed
  review cohort for the immutable candidate. Late members and duplicate reviewer
  slots are rejected, while a blocked member's authorized retry remains in the
  original cohort.
- Kept reviewer decisions independent but held implementer rework until every
  active cohort member has accepted or rejected. Rejections and carried findings
  are then consolidated into one replacement-deliverable cycle; blocked members
  remain durable unresolved blockers without preventing other reviewers from
  recording their decisions.
- Prevented the final cohort member from being silently withdrawn, validated
  review-profile outcome requirements before external artifact publication, and
  added persistence and cross-record cohort invariants.
- Required every replacement of a sealed-cohort candidate to wait for all
  current slots and atomically preserve the cohort's profile multiplicity and
  unprofiled reviewers; plain, legacy, and Admin replacement paths now fail
  closed instead of bypassing fresh review.

## 2026-07-28 - Explicit Team Integration Deliverable Contracts

- Added an explicit integration-boundary designation for Team tasks, independent
  of task titles and hierarchy depth, with a persisted deliverable contract that
  can require authoritative pinned Git provenance and explicitly name direct
  child or dependency contribution tasks.
- Prevented integration boundaries from using direct finish or artifact-only
  escape paths. Submission, acceptance, and derived resolution now require a
  current accepted deliverable satisfying the Git and contribution contract,
  including exact accepted `source_deliverable_ids`.
- Exposed the contract through Team MCP and Admin API task creation/update
  surfaces while defaulting existing support and outcome tasks to the prior
  lifecycle and contribution behavior.
- Closed legacy action, Admin deliverable, ephemeral-result, and synthesized
  feature-deliverable bypasses; acceptance now verifies the persisted submitted
  Git context and exact context commit. The task editor exposes the integration
  designation, Git requirement, and explicit contribution IDs.

## 2026-07-28 - Cause-specific Team Preparation Retry Suppression

- Converted repeated task repository/dependency preparation failures into one
  durable blocker per stable cause and task generation. Unchanged work no
  longer receives another inference attempt, while explicit blocker recovery
  and meaningful task, dependency, project-Git, or candidate changes permit a
  fresh preparation generation.
- Failed review artifact and repository preparation before model inference,
  recorded the exact review as a system-blocked immutable attempt, and required
  the existing authorized review-retry/replacement lifecycle before it can run
  again. Recovery-owner blocker wakes no longer repeat the unavailable
  repository setup merely to inspect or resolve the incident.
- Kept due, delegated, and future-follow-up preparation blockers from
  reintroducing their failed task/repository setup into recovery or unrelated
  dispatches. Task-generation fingerprints now use an explicit semantic
  allowlist, excluding priority, timestamps, creator/case bookkeeping, and
  denormalized indexes so only relevant task, dependency, Git, or candidate
  changes unlock another setup attempt.

## 2026-07-27 - Retained Team Worktree Rework Recovery

- Allowed a reopened task to reuse its clean retained worktree when the
  authoritative prior Team base still matches, every exact accepted dependency
  commit is already an ancestor of its unchanged branch tip, and remote
  dependency refs still resolve to those commits. The worker records the missing
  legacy provenance without resetting the branch or overwriting dirty work.
- Kept mismatched bases, missing dependency ancestry, and dirty retained
  worktrees fail-closed, and prevented an open dependency-preparation blocker
  from repeatedly leasing the same task for another identical setup attempt.
  Failed preparation dispatches remain terminal with their exact error.

## 2026-07-27 - Direct-agent Team Stand-downs

- Added an explicit `agent` stand-down scope across operator UI/API and manager
  MCP/action surfaces so a manager can be held without implicitly holding their
  subordinates. The existing `subtree` scope remains the default for stored
  records and callers that omit the field.
- Applied the selected scope consistently to activity state, chat and command
  authorization, wake eligibility, wake cancellation, and revocation recovery.
  The UI makes the direct-agent hold its default “Stand down” action, exposes
  the broader “Stand down subtree” separately, and requires an additional
  breadth-specific confirmation before a subtree hold can proceed to its reason.

## 2026-07-27 - Accepted Team Deliverable Rework Recovery

- Added an audited `team_reopen_task_for_rework` transition for task owners,
  project leads, and same-team managers when an accepted deliverable later proves
  invalid. The original acceptance and reviews remain immutable while the task,
  ancestors, release scope, and project resolution return to unresolved work.
- Wakes the original owner once with the exact predecessor lineage and requires a
  same-task replacement through fresh formal reviews before the task can resolve
  again. Direct finish, unrelated submissions, direct acceptance, cross-project
  recovery, and foreign-agent recovery remain blocked.

## 2026-07-27 - Playwright Worker Preflight and UI Test Capacity

- Staged and verified each seeded Playwright package/browser-cache pair before
  replacing the worker's live read-only caches. Provisioning now reports the
  package's expected Chromium executable and available cached builds when they
  differ, while agent bootstrap fails actionably instead of silently omitting
  `PLAYWRIGHT_MCP_EXECUTABLE_PATH`.
- Recorded Playwright capability on container sessions and gave only those
  agents an explicit `pids_limit: 1024` job budget for full-stack UI testing.
  The strict worker default remains unchanged for unrelated jobs.
- Added capability-aware prompt guidance and a dependency-neutral launch helper
  so repository-local Playwright imports can launch the seeded offline Chromium
  through `PLAYWRIGHT_MCP_EXECUTABLE_PATH` without replacing project packages or
  hard-coding cache revisions.

## 2026-07-27 - Stand-down-safe Team Dispatch Completion

- Kept operator and manager-subtree stand-downs authoritative for new wakes,
  leases, task starts, and unrelated mutations while allowing an already-leased
  immutable dispatch to record its assigned terminal result. Reviewers can now
  accept, reject, or block their exact pre-hold review instead of stranding it
  when a stand-down is applied during execution.

## 2026-07-27 - Assigned Blocker Dispatch Scope

- Separated blocker follow-up scheduling from immutable dispatch authorization.
  Every exact open blocker currently owned by or delegated to the waking agent
  is now retained in that dispatch's scope even when its next follow-up is not
  yet due, while only due or explicitly requested blockers are reserved for
  progress enforcement.
- Kept blocker commands bounded by the frozen blocker IDs and the current
  owner/delegate assignment. Team-manager status does not admit another agent's
  blocker, and a blocker reserved by a different dispatch remains stale.

## 2026-07-27 - Git-backed Deliverable Provenance Enforcement

- Prevented an agent with an active task Git work context from bypassing immutable
  provenance by retrying an advanced branch submission as artifact-only. The
  server now resolves the current dispatch's task branch and binds its exact tip
  automatically when it differs from the pinned base, while unchanged worktrees
  and tasks without Git contexts can still produce legitimate artifact-only
  deliverables.
- Failed closed with actionable correction details when the active context is
  ambiguous or its branch/base cannot be verified. Existing provenance-less
  accepted work remains retained for explicit replacement and review instead of
  being silently rewritten or weakening cleanup's exact-tip safety check.

## 2026-07-27 - Team Dependency Git Inheritance

- Composed server-verified accepted Git deliverables from direct dependency
  tasks into a successor's task worktree before leasing it, and retained the
  ordered dependency task, deliverable, branch, and commit provenance with the
  dispatch-bound Git context.
- Failed preparation closed on missing, ambiguous, unaccepted, moved, or
  conflicting dependency Git state and routed one high-priority blocker without
  leasing or waking the successor. Reused pristine worktrees can be composed
  safely, while existing authored work without matching provenance is preserved
  for explicit correction instead of being silently rewritten.

## 2026-07-27 - Staged Team Worktree Cleanup

- Made omitted cleanup mode perform one audited dry run and then remove the
  worktree on a later eligible pass after repeating every safety check, while
  preserving explicit dry-run-only and immediate-cleanup operator settings.

## 2026-07-27 - Live Codebot Execution Progress

- Streamed bounded Codex reasoning summaries and tool start/completion status into
  container-worker execution logs, and reconstructed the same progress from the
  session transcript when raw container output is sparse. Arbitrary structured
  tool arguments and tool output remain excluded from the operator log.

## 2026-07-27 - Communication-to-Team Chat Bridge

- Added generic communication ingress for persistent Team Chat: an authorized
  provider message now records durable provenance and idempotency state, queues
  the normal `team-chat:<message-id>` dispatch, and retains the existing Team
  transcript, lease, tools, and busy-agent ordering.
- `team_reply_to_chat` now emits a transactional external-reply request only
  for externally originated messages. Delivery outcome is retained beside the
  persisted Team reply, so a communication-send failure cannot erase or
  duplicate the internal answer.
- Published external ingress wake events immediately after their transactional
  outbox write, rejected bridge targets that cannot send the corresponding
  top-level or threaded reply, and exposed eligible targets in the workflow
  editor.
- Routed communication send exceptions through an explicit workflow error
  branch so the external delivery record always captures a failed attempt.

## 2026-07-27 - Authoritative Team Git Review Retries

- Failed closed when retrying a blocked Git review unless its immutable pinned
  candidate still matches the server-resolved task branch, requiring a
  replacement deliverable instead of reissuing a review for invalid provenance.

## 2026-07-27 - Stale Team Dispatch Recovery

- Replaced contradictory stale-terminal-action guidance with an explicit end-turn
  recovery contract: immutable dispatch scope remains unchanged, dispatch
  finalization requests a fresh wake, and only the replacement dispatch can
  include high-priority inbox work that arrived after the original snapshot.

## 2026-07-27 - Server-authoritative Team Git Deliverable Commits

- Resolved each submitted Team task branch through its directory-authorized
  managed bare repository and rejected agent-supplied commit SHAs that do not
  match that authoritative branch tip, including when the Git work context did
  not already record a commit.
- Gateway-hosted Team MCP tools now lazily obtain that managed-Git authority
  from the supported admin runtime instead of assuming the gateway runtime
  directly exposes the admin domain.

## 2026-07-27 - Assigned Blocker Recovery Task Scope

- Included a blocked source or affected task in an assigned blocker's immutable
  dispatch scope only when the recipient still owns that active task and can
  see its project, allowing corrective task commands to reach their lifecycle
  policy without granting access to foreign related tasks.

## 2026-07-27 - Team Worktree Cleanup Retry Backoff

- Preserved each cleanup candidate's durable retry schedule and failure evidence
  while maintenance refreshes its expected Git commit, preventing cron and
  agent-completion reconciliation from retrying a failed cleanup before
  `eligible_at`.

## 2026-07-27 - Immediate Team Wake Propagation

- Team review retries now publish the actionable reviewer-inbox wake intent that
  they already produced, and command transitions immediately request a
  deduplicated wake for owners whose dependency-gated tasks just became ready.
  The existing runtime eligibility, stand-down, lease, wake-budget, and
  no-progress gates remain authoritative for actual dispatch.

## 2026-07-27 - Inbox-referenced Team Blocker Scope

- Included exact durable blocker IDs named by actionable questions in the recipient's immutable
  dispatch scope only when that agent currently owns or holds delegation for the open blocker,
  allowing the requested blocker action without granting access to arbitrary referenced blockers.

## 2026-07-27 - Independent Parallel Review Decisions

- Persisted each assigned reviewer's decision independently when another parallel review, blocker,
  dependency, or descendant keeps aggregate deliverable and task acceptance blocked; aggregate
  promotion remains gated until every acceptance boundary is clear.

## 2026-07-27 - Pinned Team Git Deliverable Provenance

- Rejected unknown agent-supplied Git reference fields with an explicit `commit_sha` correction,
  required every Git-backed deliverable and formal review to pin the submitted commit, and
  preserved that submitted SHA through persistence. Reused legacy task worktrees now recover their
  base only from consistent prior Team provenance and fail safely instead of mistaking the task's
  current implementation commit for its base.

## 2026-07-27 - Portable Container-worker Task Worktrees

- Made each task checkout's Git pointer relative so it works through both its host storage path and
  `/home/agent` container mount, safely normalized the host registration back-pointer on reuse,
  and retained checkout contents with an actionable error when the corresponding Git
  administrative record is genuinely missing or invalid.

## 2026-07-27 - Tool-completed Codex Chat Turns

- Treated an empty final Codex message as successful only when the same completed turn recorded a
  successful `team_reply_to_chat` result, while retaining failures for ordinary tool calls,
  unsuccessful replies, incomplete turns, explicit failures, and context-limit exits.

## 2026-07-27 - Live Team Dispatch Lease Renewal

- Renewed the durable Team dispatch and agent-runtime lease together, in bounded
  increments near expiry, only while the exact tracked worker execution remains
  demonstrably active; terminal, missing, or unreachable jobs still fall through
  to existing completion or stale-dispatch recovery without permitting a
  replacement dispatch to overlap a healthy long-running turn.

## 2026-07-27 - Fresh Container-worker Task Bases

- Refreshed and pinned the managed remote base before creating a new container-worker task branch,
  recorded that exact base separately from the task's current HEAD, and kept existing task
  branches and worktrees immobile when their persistent session checkout is reused.

## 2026-07-27 - Team Planning Detail Persistence

- Preserved open Planning project, task, hierarchy, full-brief, worktree, and technical-detail
  disclosures across background cost and state refresh renders, so operators can keep reading a
  task body while live data updates.

## 2026-07-27 - Team Chat Reader Scroll Preservation

- Preserved an operator's reading position through Team state-stream and automatic chat-refresh
  updates, while keeping intentional thread changes, sends, and already-bottom views anchored to
  the newest message.

## 2026-07-27 - Repository-scoped Managed Git Commit Identity

- Added a privileged managed-repository operation and CLI command that update the author and
  committer identity used for subsequent host-created consolidation commits without changing
  directory defaults, other repositories, agent checkout identities, or existing commits.

## 2026-07-27 - Managed Git Refresh Authentication and Diagnostics

- Rewrote PAT-backed Azure DevOps SSH refresh sources to their canonical HTTPS URLs at Git
  invocation time, allowing the systemd refresh broker to use its configured non-interactive PAT
  without persisting credentials or changing registered repository identity.
- Fell back to the general Azure DevOps PAT when the preferred PR PAT environment variable is
  configured but unpopulated, covering both supported managed Git authentication configurations.
- Preserved fail-closed stale-ref behavior while returning and logging stable, sanitized refresh
  failure codes and operator-readable categories instead of reducing every upstream failure to a
  generic `RuntimeError`.

## 2026-07-27 - Team Task Cancellation and Operator Chat Authority

- Added a dedicated audited task-cancellation dialog with a required reason, optional same-project
  successor, visible disabled actions that explain lifecycle blockers on hover, and no generic
  edit-state shortcut into accepted or cancelled.
- Gave mutation-enabled authenticated operator chat dispatches the recipient's frozen,
  role-visible project and managed-repository scope so natural-language requests can select exact
  targets at tool-call time while existing domain authority checks remain decisive; kept
  reply-only chat and autonomous dispatches narrowly scoped.

## 2026-07-27 - Team Human Review Checklist Editing

- Replaced pipe-delimited human release acceptance-checklist text with structured, reorderable
  checklist rows that preserve stable keys, support optional checks and descriptions containing
  punctuation, validate missing labels and duplicate keys inline, and survive revision-conflict
  draft restoration.

## 2026-07-27 - Team Stand-down Revocation Visibility

- Kept the Revoke stand-down action visible for superior-issued and inherited stand-downs, disabled
  it when the operator cannot revoke the underlying record, and explained the responsible
  revocation path on hover.

## 2026-07-27 - Team Operational History and Delivery Clarity

- Added coordinated Board and Hierarchy task views with named lineage navigation, relationship
  highlighting, tree controls, operational badges, active-first defaults, completed-history
  filters, search, and bounded paging.
- Reworked deliverable and release cards around task-derived subjects, named producers and
  projects, structured outcome/evidence/review groups, staged release progress, prominent included
  work, durable release-request notes, current unsuperseded verification attempts, and copyable
  identifiers behind technical details.
- Kept resolved and closed cases durable while excluding them from active counts and wake context,
  and added explicit case-history counts, search, status filtering whose all-status selection
  includes history, and pagination.
- Separated active runtime obligations, failed attempts, and retained history; added
  occurrence age, terminal-work and inbox-reconciliation context, attention facets, bounded pages,
  multi-team and multi-project scope matching, named ownership and related work, kind-specific
  cards and remediation guidance, passive-correspondence history labels, Agent Mail routing,
  actor-scoped linked metadata and narratives, primary-target reconciliation, agent-facing
  failed-promotion commit details, and structured technical details while keeping recorded failure
  attempts in workspace attention.
- Structured expanded Planning tasks into scannable overview, lifecycle, hierarchy, contribution,
  task-brief, and technical sections, with named relationships, contribution-state checklists,
  formal review status, operator-readable lifecycle reasons, collapsed long briefs, safe
  lightweight Markdown rendering, explicit cancelled and skipped lifecycle stages, and
  not-applicable review stages for completed support work.
- Replaced governance group storage-oriented editing with name-first manager choices, searchable
  team selection and removable chips, independently validated and reorderable named goal rows in
  both creation and editing, editable goal lifecycle states, copyable technical IDs, and lossless
  backward-compatible persisted goal names.
- Renamed the snapshot-derived history to Activity timeline, made its completeness and retention
  limits explicit, and added human-readable subjects, actors, scopes, domain changes, reasons,
  name-first filters, snapshot-only update descriptions, and deduplicated copyable identifiers
  without claiming audit-log evidence.

## 2026-07-27 - Optional Godot Runner Images

- Added host-seeded Godot, Godot native-development, and software-rendering container-runner
  images with matching export templates and explicit local-only build controls.
- Kept every Godot image outside the default Ansible archive and preload allowlists and the
  documented deployment pipeline, with regression coverage for that opt-in boundary.

## 2026-07-27 - Team Review Profile Readability

- Reworked review-profile cards around display names and clear Purpose, Reviewers, Required
  outcomes, and Criteria groups, with internal identifiers moved behind optional technical
  details.
- Summarized unresolved task descendants with named previews and an expandable structured list
  instead of printing long comma-separated ID sequences on task-board cards.

## 2026-07-27 - Team Project Catalog Card Layout

- Let project catalog cards grow across available desktop space, keep the project name and status
  together in the primary row, show only the human-readable team name beneath it, and preserve
  ordinary project-name words instead of breaking them mid-word.

## 2026-07-27 - Team Governance Cost Roll-up Clarity

- Distinguished governance portfolio cost roll-ups from team, project, and agent spend by giving
  them a dedicated aggregate treatment, identifying the member-team sum as not governance-manager
  spend, and making partial pricing coverage explicit in the cost details.

## 2026-07-26 - Team Review Follow-up Fixes

- Restricted manager-fallback review routing authority to exact prompt-visible pairs, revalidated
  target and reviewer eligibility before activation and use, made each authority one-shot while
  preserving idempotent replay, and kept terminal dispatch history from pinning current Team
  membership.
- Persisted exact manager-fallback reviewer and deliverable routing authority so empty-scope
  deadlock-recovery wakes can request the reviews they are instructed to create, and allowed the
  worker API to serialize the third validation from a failed non-force worktree removal.
- Removed only proven ignored worktree paths before non-force Git cleanup, rejected new child
  edges to terminal tasks, and preserved every deliverable and review reference in coalesced
  parent-transition notices.
- Re-resolved human-review authorization after project Git-directory changes,
  reserved deterministic human-review case and task IDs, made terminal task
  graph edges immutable, and validated proposed dependencies in legacy finish
  updates.
- Required a team-scoped role grant for team-wide outcome-catalog changes, migrated legacy JSON
  outcome contracts and snapshots before strict validation, and kept mixed stored/legacy thread
  fallback records pageable until every execution has been scanned.
- Routed historical human-review corrections away from detached owners, enforced one active
  successor per predecessor, made cleanup success and its audit atomic, pinned cleanup to the
  originating dispatch session, and migrated review outcome snapshots with project overrides.
- Rejected deliverable submissions after task resolution, kept post-release case source,
  release provenance, and unresolved task membership protected across agent and Admin updates,
  and made evidence-backed post-release closure terminal.
- Normalized human-review preview expiry timestamps to UTC and anchored container thread-log
  cursors to the first stable execution so newly arriving work cannot shift older pages.
- Validated direct task completion against proposed dependency edits, reserved post-release case
  provenance for the evidence-backed report path, and selected cleanup expectations from the
  newest work context rather than opaque ID order.
- Prevented completed legacy session transcripts from replaying across bounded log-scan windows
  and kept sibling direct-acceptance handoffs decidable after one product is rejected.
- Rejected artifact-only correction successors that cannot enter release gates and kept outcome
  definitions reserved for rejected-task recovery and team-wide profiles using project overrides.
- Allowed stale review attempts on superseded deliverables to close their reviewer inboxes and
  blockers without recalculating historical deliverable or current task state.
- Revalidated release scope when the asynchronous candidate manifest is built, accepted human
  findings against recursively included contribution evidence, and routed single-source findings
  to that contribution's owning task.
- Loaded execution-mode content for unthreaded container logs before applying legacy cursorless
  offsets instead of paginating the one-line thread probe.
- Bound ID-only memory, outcome-catalog, and release-blocker mutations to the projects frozen into
  the active dispatch, including multi-project agents.
- Allowed one task dispatch to submit multiple independently reviewed products and to report
  deliverable blockers from the preserved producer task or exact recipient handoff scope.
- Reconciled cancelled-child blockers before evaluating parent resolution transitions so newly
  integration-ready parent work is notified immediately.

## 2026-07-26 - Team Review and Guided Tour Fixes

- Kept rejected tasks unresolved for parent, dependency, release, and project completion; rejected
  work now remains blocked until recovery, replacement, or authorized cancellation.
- Restricted required human-review checklist completion to approvals (including the Team UI),
  rejected common signed-URL `token` and `sig` credentials, and enforced the review request's
  immutable outcome snapshot when a profile changes while review is pending.
- Allowed agent session rebinding after terminal dispatches, rejected mixed hierarchy/dependency
  cycles during task creation, and retained same-thread legacy execution fallbacks across bounded
  log cursor windows.
- Synchronized Admin theme and privilege changes into the mounted Team Add-on, repaired project
  release-detail refresh, aligned the team-wide outcome checkbox, and escaped runtime errors
  exactly once.
- Excluded retired or superseded reviews from immutable release evidence and rejected human
  findings that target work outside the reviewed release candidate.
- Preserved cursorless offset paging for thread logs and kept grouped thread responses within the
  requested line budget, including one-line pages.
- Restored Team project mutation controls in both directions as Admin privileges change, required
  browser revalidation of Team static assets, and populated timeline type and status filters from
  full-history facets instead of the current page while keeping release-detail DOM identities
  unique to the active surface.

## 2026-07-26 - Team Stand-down UI Clarity

- Relabeled the Team agent action to “Revoke stand-down” so its effect remains clear wherever the
  control is rendered.

## 2026-07-26 - Team Lifecycle Review Fixes

- Carried open human findings through the full successor-release lineage, made reviewer-request
  notifications transactional through the Team outbox, and kept provider publication on the
  outbox path so Admin notifications are durable.
- Added human-review and execution-stall blocker projection, authorized accepted replacement links
  from project or delivered intervention scope, and rejected task cancellation while direct
  acceptance or review handoffs remain active.
- Treated cancelled tasks as terminal across activity, runtime, API, access, outcome-catalog, and
  Git-work-context projections, and reused one task-assessment evaluator per project-status pass.
- Made workflow-preparation requests transactional, preserved persisted rejected-task history for
  explicit recovery, and reconciled obsolete sibling review blockers when a release becomes
  terminal.
- Allowed authorized task cancellation to retire blocker-backed blocked review attempts while
  continuing to reject cancellation across pending acceptance or review handoffs.

## 2026-07-25 - Unified Team Hierarchy and Human Release Review

- Added one recursive `TaskAssessment` for support completion, accepted outcomes and evidenced
  no-ops, authorized cancellation, dependency and descendant readiness, invalid legacy topology,
  and direct-child contribution coverage at every submit, accept, review, release, Admin, MCP,
  workflow, and ephemeral-prompt boundary. Task reads now project each unique inherited upstream
  blocker with its exact reason and routing context without persisting a duplicate blocker.
- Added task provenance, immutable cancellation dispositions, exact source-deliverable lineage,
  authority-scoped root creation, source-aware blockers that reconcile linked local and shared
  incidents during reassignment or cancellation without rewriting formal review decisions,
  transition-driven parent supervision, and project-completion blocking for unresolved hierarchy
  and review work.
- Added SHA/tree-pinned human release requests, current Admin-directory reviewer reconciliation,
  strict session identity and RBAC, audited reassignment, retryable notifications and deep links,
  blocked/resume recovery, workflow-prepared previews, and expiry handling.
- Added immutable human decisions and findings, deterministic correction cases and independent
  roots, predecessor-based successor releases with carried findings, complete gate reruns, and
  exactly-once canonical publication of the approved candidate.
- Added published-release regression or adjustment intake that preserves the published package,
  creates one idempotent triage case, and requires an explicit open case plus accepted correction
  scope to authorize an optional successor; reporting never implies rollback.
- Added evidence-required post-release case closure plus supported Team MCP and Admin API
  operations to retarget ambiguous findings to independent correction roots and propose
  resolution from accepted correction deliverables without replacing the human decision.
- Added Admin and Team UI policy, hierarchy, blocker, worktree, human-review, release-detail, and
  post-release reporting surfaces, plus updated agent guidance and canonical operator manuals.
- Upgraded Team SQLite state to schema 33 with normalized hierarchy, dispatch, human-review, and
  cleanup relationships plus idempotent v32 backfill and round-trip validation.
- Added one routed execution-stall incident when no-progress allowance is exhausted, reason-required
  audited Admin repair for legacy task transitions into or out of `blocked`, Admin-accountable
  empty-reviewer recovery, and preview URL rejection for userinfo or secret-bearing parameters.
- Made the Admin Sessions repository-grant summary tolerate legacy detail payloads without a
  `git_grants` collection and refreshed its browser contracts for repository and Codex-auth reads.

## 2026-07-25 - Hierarchical Team Dispatch and Parent Supervision

- Made agent dispatch a durable two-phase lifecycle: work is first reserved as `PREPARING`,
  exact task, blocker, review, inbox, project, and repository scope is frozen, and the dispatch
  becomes `LEASED` only after every scoped worktree is ready.
- Required agent commands to present a current durable dispatch and rejected resources that were
  not captured in its immutable scope or stopped being actionable before mutation.
- Routed child blockers and deliverable acceptance to the owning parent task, and emitted
  coalesced transition-driven parent supervision for handoff, rejection, resolution, and
  integration readiness while retaining periodic readiness scans as a recovery backstop.

## 2026-07-25 - Safe Task Worktree Cleanup Backends

- Added structured container-worker task-worktree cleanup with a repository lock, repeated
  branch, commit, registration, tracked-change, and untracked-file validation immediately before
  removal.
- Preserved refused cleanup evidence and ignored-file byte inventories, supported dry runs and
  idempotent already-absent cleanup, and pruned Git registrations only after safe removal.
- Added a self-contained libvirt guest cleanup command using the same locked validation contract.
- Added durable Team cleanup candidates and periodic reconciliation for resolved or reassigned
  task checkouts, with configurable dry-run, grace, retry, recovery, and batch limits.
- Re-read Team safety state twice before backend mutation, retained active-dispatch/context
  deferrals, and recorded refused cleanup as auditable recovery-required work with exact
  path, branch, failed-check, ignored-file, and byte evidence.
- Prevented Git-context metadata pruning until physical cleanup succeeds, then pruned only
  obsolete unreferenced context rows after the backend confirms removal.

## 2026-07-24 - Team Git Work Context Lifecycle

- Marked Git work contexts as submitted when deliverables consume them and abandoned remaining
  contexts when their dispatch ends, task becomes terminal, or task ownership changes.
- Rejected terminal-task context creation and binding, closed every context when legacy actions
  end their dispatch, and treated missing or expired matching dispatch leases as stale.
- Added automatic stale-context reconciliation and 30-day pruning for unreferenced abandoned
  metadata while retaining deliverable-linked provenance.

## 2026-07-24 - Agent Log Run and Thread Paging

- Kept the Executions log focused on the latest agent run while rendering artifact imports,
  staging, and publishing as compact inline events instead of separate log-owning executions.
- Made Threads history load newest-first through bounded continuation cursors so opening the tab
  no longer downloads every historical execution or a complete long transcript.
- Kept fallback thread history reachable beyond 25 executions, prevented recurring thread IDs
  from being emitted twice across record windows, and recovered execution logs when session-log
  reads fail.
- Preserved failed artifact-command outcomes after worker cleanup and excluded known setup and
  utility effects from the legacy current-agent-run fallback.

## 2026-07-24 - Team Review Closure Reliability

- Kept sibling task blockers authoritative when the last review on another deliverable closes.
- Limited review inbox cleanup to reviewer assignments so owner rework notices remain actionable.
- Restored an actionable owner notice and wake eligibility when retiring a blocked review exposes
  open-finding rework.

## 2026-07-23 - Team Review Lifecycle Hardening

- Scoped review withdrawal and retirement by Team so managers cannot mutate another Team's work.
- Preserved active task and deliverable blockers when closing a review would otherwise restore
  prior acceptance.
- Allowed terminal operator-created reviews without a decision timestamp to be retired, and
  included deliverable verification evidence when withdrawal reroutes work to superior acceptance.

## 2026-07-23 - Outcome Catalog Review Fixes

- Prevented archival of outcome kinds that are still required or forbidden by nonterminal tasks.
- Snapshotted team-wide review profile requirements from the reviewed project so project outcome
  overrides remain authoritative for deliverable and release reviews.
- Kept review-profile outcome selections scoped to the profile being edited, including configured
  values that are no longer active, so unrelated project focus cannot silently clear them.

## 2026-07-23 - Outcome Catalog Migration Repair

- Added the missing SQLite column migration for review outcome-kind snapshots so existing Team
  databases can upgrade to the configurable outcome catalog.

## 2026-07-23 - Agent Review Recovery

- Added authorized pending-review withdrawal and project-lead/manager retirement of terminal
  non-release reviews without deleting decisions or findings.
- Recorded review request provenance, closed stale reviewer inbox work, and returned review-free
  deliverables to direct superior acceptance.
- Added compact agent guidance for retry, withdrawal, retirement, rework lineage, and carried
  findings, with detail kept in the relevant tools and review records.

## 2026-07-23 - Configurable Team Outcome Catalog

- Added first-class team-wide and project-specific outcome-kind definitions with names,
  plain-language semantics, durable-reference requirements, evidence requirements, project
  override behavior, and archival.
- Made the effective catalog visible to every project agent through Team read tools, lease and
  wake context, and manager guidance; unknown or archived outcome kinds are rejected instead of
  silently becoming ad hoc conventions.
- Added the configurable `manage_outcome_kinds` role permission. Only a structurally matching
  Team manager or project lead with that permission may mutate definitions through Team tools;
  unlocked operators retain catalog administration through the Team API and Projects UI.
- Snapshotted effective definitions into task outcome contracts, deliverable outcomes, and review
  requests so later catalog edits do not change the historical meaning of existing work.
- Migrated pre-catalog local task, deliverable, and review data into explicit definitions and
  renamed the unreleased `forbidden_effect_kinds` contract field to
  `forbidden_outcome_kinds`.

## 2026-07-23 - Superior Deliverable Decisions

- Added `team_decide_deliverable` so the direct superior recorded on a plain deliverable handoff
  can accept it or reject it with a concrete reason, atomically handle the notice, and preserve
  the decision in Team state.
- Kept formal review authoritative once attached, returned rejected handoffs to the task owner as
  actionable rework, and added wake-specific guidance for accepting, rejecting, requesting
  specialist review, or reporting a blocker.
- Preserved pending pre-upgrade handoffs by deriving their recorded superior from the original
  deliverable notice when the new recipient field has not yet been populated.
- Preserved direct-handoff decisions as historical facts across later review and promotion state
  transitions, automatically consumed matching pending handoff notices with the decision, and
  allowed direct decisions for a selected deliverable even when a sibling follows formal review.

## 2026-07-23 - Branch Documentation Audit

- Aligned the Team operator guide and technical manual with outcome contracts, superior acceptance,
  review rejection/retry/replacement/retirement behavior, and Bokio duplicate-check pagination and
  cache scope.
- Documented cross-execution agent-log continuity, session-owned PR routing and cleanup, reused
  Azure PR source confirmation, live Host Directory grant reconciliation, and the refactored
  managed-Git and consultation service boundaries.

## 2026-07-23 - Outcome Contract and Host Grant Review Fixes

- Applied outcome-contract kind and durable-reference validation to both agent and Admin
  deliverable submission, and kept evidence-only no-ops on the explicit `evidenced_noop`
  completion path instead of sending them into review.
- Enabled the capability-derived Host Directory tools when an existing libvirt session receives
  new Host Directory grants.

## 2026-07-23 - Review and Bokio Duplicate-Check Correctness

- Kept deliverables pending when retired reviews leave unresolved carried findings, and restored
  outcome-aware implicit review-profile selection for atomic deliverable submissions.
- Made Bokio duplicate checks follow reported pagination totals and scoped cached durable evidence
  to the configured company and API endpoint.

## 2026-07-23 - Scoped Host Directory Resources

- Added directory-scoped Host Directory definitions and structured session grants for dedicated
  relative list, bounded file/folder import, and recoverable delete tools without exposing absolute
  host roots.
- Added revision-checked, approval-by-default quarantine deletes with Admin health checks,
  restore, and explicit permanent purge; imports produce session-owned artifacts and deterministic
  directory archives.
- Added the baseline-and-settle `trigger:host-directory:file-added` provider to the existing
  trigger orchestrator, plus structured Admin configuration and Agent/Sessions capability
  selectors.
- Hardened descriptor-relative traversal against bind-mount crossings, made pagination resilient
  to live-tree removals, probed delete-quarantine writability, and retained scoped cleanup records
  for interrupted quarantine operations.

## 2026-07-23 - Structured Session HTTP Targets

- Added structured HTTP target alias and base-URL editors to agent creation, agent policy editing,
  and Sessions policy editing so operators no longer need to modify raw session metadata.
- Validated and narrowly merged canonical HTTP target updates, preserved workflow-owned target
  metadata, supported legacy App target conversion, and made an explicitly empty target set deny
  the legacy fallback.

## 2026-07-23 - Team Outcome And Recovery Reliability

- Added explicit outcome contracts for tasks, structured deliverable outcomes,
  review-profile outcome requirements, and evidence-backed no-op completion so
  operational work cannot finish with a report in place of its required result.
- Added immutable blocked-review retry, deliverable replacement lineage,
  operator review retirement, exact project blocking sources, blocked-task
  completion guards, late high-priority handoff protection, and idempotent inbox
  and blocker replay behavior.
- Included project goals and blockers in wake context, preserved sole urgent
  directive detail, bounded generated artifact path segments, and added a
  separate redacted Activity view/API/CLI tail with 30-day retention.
- Added paginated Bokio journal duplicate checks with durable evidence,
  source fingerprints, matching entry IDs, and a 15-minute reuse window.

## 2026-07-23 - Session-Owned Pull Request Tracking

- Recorded session-backed pull requests under the session's canonical directory instead of the
  workflow graph's execution directory.
- Kept persisted PR routing authoritative while resolving legacy bindings without a directory
  through the configured session manifest store.
- Rejected duplicate `create-session` IDs and pruned PR routing plus feedback state whenever
  sessions are deleted, preventing historical bindings from following a reused session ID.

## 2026-07-23 - Focused Worker Navigation

- Replaced the Workers fleet summary and tall service cards with worker search, status filtering,
  compact name/status rows, and hover or focus diagnostics.
- Kept the independently scrollable list and expanded the selected-worker overview with unit-file
  state and diagnostic detail.

## 2026-07-23 - Azure DevOps PR Source Confirmation

- Confirmed reused Azure DevOps pull request source revisions through the latest PR iteration when
  `lastMergeSourceCommit` still reflects the preceding push.
- Selected the highest iteration ID, bounded confirmation by elapsed time and per-request timeout,
  and preserved the existing conflict result when the source branch genuinely moves.

## 2026-07-23 - Agent Log Continuity Across Artifact Operations

- Kept filling the Agents page's latest execution-log window from preceding session executions
  when a newer, short artifact operation would otherwise replace the visible agent transcript.
- Preserved bounded line-window pagination so older execution history remains available through
  **Load older** without eagerly reading the whole session history.
- Bounded live-refresh transfers as well as rendered output by reading only each execution's
  required tail through the worker's paged log API.

## 2026-07-23 - Team Artifact Handoffs

- Added `team_import_artifact` so an active Team agent can permission-check an
  `artifact-space:` reference and import its latest published bundle into that agent's own
  `/artifacts/team-artifact-imports/...` tree during ordinary task and inbox handoffs.
- Kept the import target caller-bound and host-generated, preserved artifact-space read grants,
  and returned concrete imported file paths instead of treating deliverable metadata as file
  access.
- Wired ordinary imports through the supported admin provider adapter so default libvirt Team
  sessions have the session lookup, VM copy, and VM execution ports required to materialize files.
- Moved the artifact-import guidance into the manual's canonical Chapter 22 source and regenerated
  every published format so the checked-in build record remains current.

## 2026-07-23 - Team Task Completion and Review Semantics

- Directed administrative, delegation, coordination, and acknowledgement-only Team work through
  direct `finished` task completion instead of manufacturing deliverables for review.
- Distinguished superior acceptance from formal review in derived task payloads and Team UI labels
  while preserving the existing persisted task-state contract.
- Kept terminal formal-review history and unresolved carried findings classified as review work,
  while excluding superior-acceptance-only deliverables from reviewer-routing gaps.
- Required a direct superior before accepting a direct deliverable submission, preventing top-level
  agents from leaving work waiting for an acceptance notice that cannot be delivered.
- Split superior acceptance from formal review in Delivery grouping and filters, while retaining
  deliverable artifacts and audit history.

## 2026-07-22 - Bokio Evidence Recovery and Teamless Chat Attachments

- Reopened evidence-bearing journal creation after an operator confirms an uncertain create is
  absent, and kept definite Bokio create rejections failed and explicitly retryable.
- Allowed active teamless agents to stage attachments during their Team Chat dispatch while
  preserving the existing Team-assigned staging path for other active dispatches.

## 2026-07-22 - Team Chat Relative Runtime Attachment Delivery

- Resolved Team Chat attachment source files and their private allowed source root to absolute
  host paths before copying them into an agent workspace.
- Added regression coverage for attachment delivery when the configured runtime directory is
  relative to the control-plane project directory.

## 2026-07-22 - Evidence-Aware Bokio Prepared Journals

- Added managed evidence IDs to prepared Bokio journal entries so the receipt or invoice is part
  of the exact action an operator reviews and approves.
- Routed approved evidence-bearing journals through the durable create-and-attach ledger, marking
  the action executed only after all evidence is attached and resuming failed uploads without
  recreating an already-created journal entry.
- Exposed prepared evidence IDs and the final evidence result in the Bokio Admin review workspace.

## 2026-07-22 - Bokio Failed Action Rejection

- Allowed operators to reject failed prepared Bokio actions instead of requiring a retry, while
  preserving the failed payload, error details, rejection reason, and transition history.
- Exposed **Reject** alongside **Retry** in the Bokio review workspace and restored rejection for
  approved actions in the same UI.

## 2026-07-22 - Container Worker Managed Git Alias Validation

- Allowed repository-specific managed Git SSH aliases through the worker's source policy when the
  alias grant slot matches the managed repository named in the clone URL.
- Kept SSH clone validation fail-closed for mismatched aliases, lookalike hosts, paths outside the
  configured managed Git root, path traversal, credentials, query strings, and fragments.

## 2026-07-22 - Teamless Agent Chat Replies

- Allowed active teamless persistent agents, including governance managers, to answer a human
  operator during their active Team Chat dispatch.
- Kept the exception limited to `team_reply_to_chat`; teamless agents remain unable to invoke
  ordinary Team state mutations.

## 2026-07-22 - Cross-Domain Service Boundary Refactoring

- Extracted managed-Git worktree/lifecycle handling and code-consultation orchestration behind
  typed services instead of coordinating them through daemon, gateway, and provider internals.
- Centralized connector and mailbox grant policy, moved Connector and Codex account workflows out
  of Admin Web routes, and shared one add-on catalog across Admin runtime consumers.
- Split Team release verification/review handoff, Bokio durable-operation mechanics, ADO activity
  pagination, local-OpenAI tool compatibility, and mailbox/Team Mail UI features into focused
  collaborators while preserving their existing public contracts.
- Made the live documentation catalog consume the technical manual's authoritative chapter
  manifest instead of independently discovering another chapter list.

## 2026-07-22 - Hierarchical Team Blocker Escalation

- Added durable Team blocker records and event history for explicit blockers and blocked tasks,
  deliverables, reviews, release packages, and promotion or release failures.
- Routed each blocker to the nearest available superior immediately, transferred accountability
  one level after an owner dispatch without blocker-specific progress, and returned stalled
  delegated work to its accountable owner before any further escalation.
- Added terminal governance and operator routing, durable wake outbox handling for normal and
  expired dispatches, unavailable-assignee reconciliation, and retained the periodic manager
  fallback as a safety net.
- Added MCP report, update, list, and detail tools plus wake context, API, runtime-attention, and
  Team UI visibility for blocker ownership, delegation, route, and escalation depth.
- Kept manual resolution available to active agents on the reporter-to-manager escalation line
  after ownership transfers or terminal routing, excluded peers and temporary delegates, and
  retained source-driven automatic resolution.
- Added privileged, audited Admin API and Team UI resolution for blockers that have reached
  governance or operator routing without allowing operators to preempt locally routed blockers.

## 2026-07-22 - Multi-Repository Agent Git Grants

- Added first-class, repository-keyed read-only and write grants so one persistent VM or container
  agent can access multiple managed repositories without replacing an earlier repository key.
- Made read-only grants session-persistent by default for repeated clone, fetch, and pull, while
  retaining explicit operation-scoped credentials for one-shot clone and consultation flows.
- Added targeted grant, revoke, and grant-and-clone workflow, CLI, Admin API, and Admin Web
  surfaces, including exact worker-key revocation that preserves unrelated repository grants.
- Migrated legacy singular session Git metadata into the plural grant model and added focused
  multi-repository, lifecycle, migration, worker, and operator-surface coverage.
- Preserved repository-specific worker SSH aliases across session preparation, revoked legacy VM
  credentials before canonicalization, reconciled persisted grants against real credentials, and
  propagated failed libvirt clone actions to operator surfaces.

## 2026-07-22 - Pull-Time Managed Git Refresh

- Added a host-owned Unix-socket refresh broker so managed clone, fetch, pull, `ls-remote`, and
  archive reads refresh their exact authorized upstream before serving refs.
- Made broker and upstream failures fail closed instead of silently serving stale managed refs,
  while keeping provider credentials out of the restricted SSH wrapper.
- Added generic systemd and Ansible lifecycle management for
  `evander-managed-git-refresh.service`.
- Made guided setup start the broker with its secret drop-in, bounded concurrent request handling,
  coalesced overlapping repository refreshes, and authorized all effective directory Git users.

## 2026-07-22 - Tool Call Audit Search Performance

- Added a trigram full-text index for Tool Calls free-text searches while preserving the canonical
  audit JSONL and the existing SQLite filtering fallback.
- Migrated existing audit indexes through a storage-efficient batched background backfill after
  JSONL import catches up, avoiding a large synchronous database rewrite or long write lock during
  Admin Web startup.
- Debounced free-text searches, stopped polling exact filtered result sets, and removed a redundant
  aggregate scan from each indexed query.
- Cached successful schema initialization per audit-index instance so steady-state reads do not
  re-enter migrations or acquire SQLite write locks.

## 2026-07-22 - Managed Worker Deployment Checkout

- Moved private worker deployment checkouts from the retired `git-remotes/evander-deploy.git`
  mirror to the control plane's root-owned managed repository.
- Scoped each worker's read-only SSH key to that exact managed repository and pinned deployments
  under worker-specific `refs/evander/deploy/` refs.
- Explicitly fetched each worker's private deployment ref before checking out its pinned commit
  SHA so the object is present even though the ref is outside `refs/heads/`.
- Removed stale worker authorizations before managed-repository resolution so interrupted legacy
  deployments cannot reintroduce path-based managed Git state.

## 2026-07-22 - Live Agent Documentation Tools

- Added policy-gated tools to list, search, and read current host documentation without copying
  the documentation tree into agent execution targets.
- Granted the read-only tools to Admin assistant profiles by default while keeping them explicitly
  grantable to any other agent session through the normal MCP policy surfaces.
- Limited the live catalog to current authoritative, focused, and canonical technical-manual
  sources, excluding historical, generated-duplicate, managed-upstream, and arbitrary files.

## 2026-07-22 - Empty Admin Login Username

- Leave the Admin Web username field empty on the login page instead of pre-filling `admin`.

## 2026-07-21 - Subscription Completion And Policy Conversion Hardening

- Launched subscription turns through the image-owned `/usr/local/bin/codex` executable instead
  of resolving `codex` through the persistent agent home's writable `PATH` entry.
- Moved Codex subscription completion signals out of the persistent agent home and exposed them
  to job containers through a worker-owned read-only mount, preventing forged success markers and
  agent-created marker symlinks from blocking worker restart.
- Rejected reuse of a staged Team Chat attachment after another message has claimed it while
  preserving same-request idempotent replay.
- Corrected legacy communication conversion to use current tool and form drafts, recognize every
  email draft/send variant, preserve mailbox-specific folders, and keep device-login polling and
  cancellation bound to the originating worker directory.

## 2026-07-21 - Bokio Customer And Supplier Reads

- Added separately permissioned Bokio MCP tools for listing and retrieving customers and
  suppliers, including bounded pagination, query filtering, and safe resource-ID handling.
- Marked supplier tools as preview in the agent-facing catalog and documented the supported
  customer and supplier query fields.

## 2026-07-21 - Microsoft Graph Attachment Retrieval

- Restored Graph message-detail and attachment-list retrieval by limiting attachment metadata
  selections to properties supported by the base Microsoft Graph attachment type.
- Resolved Graph's opaque message parent-folder IDs against configured mailbox folders before
  enforcing session folder grants.
- Allowed that resolution to continue past stale folder mappings without hiding authentication,
  permission, throttling, or transient Graph failures.

## 2026-07-21 - Scrollable Admin Detail Lists

- Made card-list sidebars independently scrollable across Admin Web list/detail pages so operators
  can browse long collections without moving the selected detail workspace.
- Reduced Agents list rows to the agent identifier and execution-status badge, with the previous
  session, directory, target, runtime-image, and prompt-queue summary available on hover or focus
  and in the selected-agent detail.

## 2026-07-21 - Concurrency And Lifecycle Review Hardening

- Prevented active Bokio supplier-invoice and prepared-action executions from being reclaimed by
  confirmation alone; interrupted executions now require both operator reconciliation and proof
  that the recorded owner process has exited.
- Hardened Codex subscription completion and authentication startup so hostile marker directories
  cannot block daemon recovery and failed app-server initialization always tears down its child.
- Preserved legacy Teams channel, chat, and user grants during session-policy conversion, and kept
  concurrent Team Chat attachment rollback from deleting files already committed by another send.

## 2026-07-21 - Focused Configuration Navigation

- Replaced the Configuration page's dense area cards and nested collapsed panels with a compact
  area/section navigator and a focused one-section editing workspace.
- Added friendly and technical setting search, explicit configuration utilities, stable deep links,
  field focus/highlighting, responsive selectors, and unsaved-area indicators without changing
  configuration inheritance or save semantics.
- Documented the `config_tab`, `config_section`, and `config_field` deep-link contract and the
  stable section keys exposed by the configuration-state API.

## 2026-07-21 - Team Chat File Attachments

- Added two-way file and image attachments to the Team Add-on's built-in operator Chat, including
  multipart uploads, authenticated downloads, inline image rendering, and agent workspace delivery.
- Added add-on-owned, directory-inherited Team Chat attachment policy with independent direction,
  type, size, count, and exact agent-send approval controls; Microsoft Teams and core communication
  file policies remain separate.
- Added session-owned agent attachment staging and attachment-aware reply/proactive Chat MCP tools,
  with durable add-on SQLite metadata and private add-on file storage.

## 2026-07-21 - Grouped Admin Navigation

- Reorganized every Admin Web destination into permanently expanded Overview, Operations,
  Automation, Extensions, Infrastructure, and Governance groups without changing page routes or
  backend-specific visibility.
- Replaced tall navigation cards with compact icon rows and description tooltips while preserving
  the collapsed desktop navigation and horizontal mobile page strip.
- Added a searchable **Find page** dialog for built-in and add-on destinations, available from the
  navigation or with `Ctrl+K` / `Command+K` and protected by the existing unsaved-workflow guard.

## 2026-07-21 - Bokio Remote Upload MCP Tools

- Added separately granted MCP tools to list remote Bokio uploads, read one upload's metadata, and
  download its bounded file into a session-owned communication artifact.
- Kept remote downloads transient and the existing local evidence inventory distinct, requiring an
  explicit evidence import when a downloaded Bokio upload should be retained and deduplicated.
- Rejected partial, redirect, and other non-200 upload downloads before artifact staging, and bounded
  streamed error-body capture so malformed upstream failures cannot bypass the download memory cap.

## 2026-07-21 - Switchable Codex Subscription Usage

- Added per-directory defaults and per-session overrides for choosing metered API or ChatGPT
  subscription usage on container-worker Codex turns, including Admin Web creation, policy edit,
  account sign-in/status/logout controls, and next-turn switching that preserves thread history.
- Added one worker-owned Codex auth broker as the sole durable `auth.json` writer, with exclusive
  ownership locking, serialized/coalesced refresh, external in-memory job authentication, token
  redaction, audit events, and fail-closed behavior with no metered fallback.
- Added an allowlisted HTTPS CONNECT proxy for subscription traffic and Ansible worker settings;
  job containers receive no account file, environment token, command-line token, or direct public
  egress grant.
- Hardened subscription completion and teardown with no-follow marker creation, immediate
  app-server-exit detection, cancellation-safe runner joins, and notification handling that cannot
  block the sole protocol reader.
- Kept local and Azure OpenAI sessions on their configured private API-key routes whenever the
  stored Codex preference is subscription auth, without rewriting that preference, and stopped
  applying Codex defaults to Claude turns or clearing omitted per-session auth overrides during
  workflow upserts.
- Matched subscription turns to the existing container-owned Codex sandbox policy so managed Git,
  package relays, and `/artifacts` remain available, while bounding restricted-proxy connections
  and expiring idle tunnels to protect worker host resources.
- Restricted worker-global subscription account management to root owners instead of accepting
  ownership of any one directory that shares the worker.

## 2026-07-21 - Automatic Microsoft Graph Mailbox Tokens

- Added host-side Microsoft Entra client-credentials authentication for Graph mailboxes, with
  a credential-keyed process cache shared across provider rebuilds, thread-safe acquisition, and
  automatic renewal shortly before token expiry.
- Added structured Admin Web inputs for Graph tenant ID, client ID, and client-secret environment
  references while retaining static access-token authentication as an explicit legacy mode.
- Kept application credentials and acquired tokens out of guests and session manifests, validated
  incomplete host credential configuration at runtime startup, and redacted secrets from failures.

## 2026-07-21 - Structured Email Policy Configuration

- Replaced the email-target auth-profile text field with a sending-mailbox selector populated from
  configured mailbox accounts while retaining canonical `email.<mailbox-id>` storage.
- Replaced raw JSON mailbox-folder policy editing with mailbox rows and folder checkboxes, and made
  the mailbox allowlist select configured mailbox IDs while preserving explicit empty folder grants.
- Kept effective host-config mailboxes available to the selectors when the root directory has no
  explicit mailbox override.

## 2026-07-21 - Workflow Existing-Target Suggestions

- Added compact existing-session and VM suggestions to Literal workflow target fields while
  preserving free-form values, templates, and upstream-output bindings.
- Included expired sessions in the directory-scoped suggestions with an `(expired)` label that
  does not alter the stored target ID.
- Served suggestions from a workflow-authorized target catalog, so workflow operators and
  maintainers no longer depend on the owner-only Sessions inventory route.
- Preserved session or VM suggestions when the other inventory source is temporarily unavailable.

## 2026-07-21 - Session Communication Grant Hardening

- Made canonical communication-target IDs fail closed when their catalog entries are disabled or
  deleted, while retaining raw destination resolution for legacy allowlists.
- Preserved explicit empty mailbox-folder grants and limited mailbox defaults returned to Admin Web
  to folders allowed by effective directory policy.
- Added an actionable Sessions-page empty state that opens Configuration for the session directory
  when no mailbox accounts are available under its policy.

## 2026-07-20 - Session Communication And Mailbox Grants

- Added durable, nullable session `communication_target_ids` and structured
  `email_mailbox_grants`, keeping missing/null manifests on legacy behavior while treating explicit
  empty lists as no access.
- Separated mailbox read/draft/send capability from Teams, Discord, and outbound email recipient
  destinations. Email send now requires a send-capable mailbox grant plus a write-capable recipient
  target bound to the same mailbox profile; draft-only access needs no send target.
- Added shared Agent/Sessions editors, secret-free mailbox option catalogs, explicit legacy-session
  conversion, workflow support, audit target IDs, and policy/API regression coverage.

## 2026-07-20 - Structured Email Mailbox Configuration

- Replaced the raw JSON editor for Admin Web email-mailbox configuration with a row-based editor
  for provider, mailbox identity, host access-token environment-variable reference, and folder
  mappings/defaults. The editor keeps OAuth token values out of Admin Web while supporting Gmail,
  Microsoft Graph, and the local development provider.
- Preserved mailbox draft fields across editor renders, retained intentionally empty default-folder
  allowlists, and refreshed untouched folder mappings when changing providers.

## 2026-07-20 - Paginated Email Search MCP Tool

- Added the explicitly granted `email_list_messages` MCP tool for paginated mailbox-folder
  listing across Gmail, Microsoft Graph, and the local provider, with read-state, date, sender,
  recipient, subject, full-text, and attachment filters.
- Kept `email_list_unread` as the separate incremental unread workflow; standard email-enabled
  sessions do not receive the broader listing capability automatically.
- Corrected Graph query composition and Gmail label filtering so structured and textual searches
  use provider-supported request shapes, including mapped custom folders and recipient searches.

## 2026-07-20 - Sessions MCP Tool Groups

- Moved the codebase-consultation and connector MCP tools out of the generic **Other** group on
  the Sessions page, including when a stale catalog labels them as **Other**.
- Separated Team Add-on MCP tools from the Microsoft Teams application tools by giving add-on
  tool descriptors an optional group override.
- Clarified the `team_reply_to_chat` tooltip: it is granted only for the active human Team Chat
  dispatch, while `team_send_operator_chat_message` is the proactive operator-message tool.

## 2026-07-20 - Team Session Navigation

- Added **Open session** actions for Team agents in the Teams detail views and org-chart context
  menus, taking operators directly to the linked Sessions record.
- Kept add-on-to-shell navigation origin- and iframe-source-validated before updating the existing
  session deep link.

## 2026-07-20 - Bokio Recovery And Retry Hardening

- Allowed reconciliation-confirmed recovery of interrupted direct operations and prepared actions
  left in `executing`, while keeping ordinary concurrent calls blocked.
- Treated unparsable successful write responses as uncertain, while keeping DNS, TCP-connect, and
  connection-establishment timeout failures on the explicit retry path.
- Kept prepared actions on the explicit retry path when payload validation or Bokio client setup
  fails locally before any remote request can occur.
- Kept the mounted Bokio review frame synchronized with live privileged-operation lock changes and
  exposed confirmed recovery for interrupted prepared actions.

## 2026-07-17 - Strict Worker Temporary Storage

- Raised the strict container-worker profile's bounded noexec `/tmp` tmpfs from 512 MiB to 4 GiB,
  so dependency-heavy browser and full-stack validation can complete without consuming the host
  filesystem or weakening the strict sandbox boundary.

## 2026-07-17 - Add-on MCP Catalog And Bokio Review Workspace

- Added validated static MCP tool descriptors to add-on registration and merged enabled add-on
  tools into the Admin Web Sessions and MCP Tools catalogs, workflow tool selectors, and runtime
  Tool Visibility policy, grouped by add-on.
- Added a narrowly autonomous `bokio_create_supplier_invoice` MCP tool with caller-supplied durable
  operation keys, exact-payload replay, atomic execution claims, and reconciliation-gated retry for
  uncertain Bokio outcomes; no direct autonomous bank-payment tool was added.
- Turned the Bokio tab into a prepared-action review workspace with filtering, payload/result/error
  inspection, transition history, atomic **Approve & execute**, rejection, and explicit retry
  states, protected by Admin-owner permission and the privileged-operations lock.

## 2026-07-17 - Repository Readiness And Admin Frontend Reliability

- Restored a clean repository-wide Ruff lint and formatting baseline, while preserving the
  historical gateway compatibility exports and strengthening previously unchecked workflow
  reload assertions.
- Kept the trigger-cursor cross-process serialization regression concurrent while using spawned,
  synchronized workers instead of forking the multithreaded test runner.
- Updated Admin Web browser-smoke fixtures and selectors for branding, session policy labels,
  connector-aware navigation, and current success feedback so the suite exercises the current UI
  contract without ambiguous or stale expectations.
- Made the Sessions policy and wake-schedule panels stack at constrained content widths so the
  neighboring panel cannot cover the policy Save button.
- Resynchronized workflow inspector drafts after undo and made compact-canvas Pan mode take
  precedence over node dragging, keeping restored values and touch gestures consistent with the
  selected controls.
- Split Admin Web operational views into lazy-loaded route bundles, reducing the initial JavaScript
  payload below the Vite warning threshold without suppressing the warning.
- Refreshed the generated technical-manual publication metadata and binary editions after
  formatting the checked-in publication generator.

## 2026-07-17 - Connector And Bokio Review Hardening

- Authorized connector reads, edits, and health checks against the resolved requested directory,
  allowing child-directory owners to manage the connectors in their scope without root access.
- Rejected composed connector input schemas whose references, boolean applicators, or negations
  cannot accept the object-shaped arguments supplied by MCP.
- Cleared omitted connector grants when either container or libvirt agent upserts move a session
  into a different directory, and reset the Admin Web create dialog's grants when its directory
  changes, preventing directory-scoped connector capabilities from crossing trust boundaries.
- Rejected connector schemas that exclude object arguments or use remote references, disabled
  runtime schema retrieval, and clamped operation-required approvals to exact single-use calls.
- Preserved spaces and non-ASCII filesystem names without permitting placeholder traversal, and
  limited connector-operation audit fallback metadata to actual connector calls.
- Added one-time Admin Web reload recovery for lazy chunks made stale by a frontend deployment.
- Kept definite pre-request Bokio journal-creation failures retryable and prevented delayed
  evidence failures from regressing an already completed operation.
- Marked Bokio evidence associations failed when their retained blob cannot be opened, allowing a
  deliberate retry after the evidence is restored.
- Restored the tracked Admin Web entrypoint to the mainline bundle reference instead of retaining
  an unresolved generated hash.

## 2026-07-16 - Connector, Managed-Git, And Bokio Review Hardening

- Followed REST `303 See Other` responses with bodyless GET requests, rejected protected API-key
  headers, refreshed OAuth tokens when effective profiles or credentials change, and preserved
  provider-advertised short token lifetimes.
- Prevented stale Admin connector responses and mutations from crossing directory selections.
- Serialized content-deduplicated Bokio evidence ingestion, atomically claimed explicit failed
  upload retries, and kept evidence visible for attention while any association remains failed.
- Atomically claimed Bokio journal-entry creation before the external request, retained unknown
  outcomes for manual inspection, and rejected idempotency keys reused with a different payload or
  evidence set.
- Kept container-worker Git write identities container-visible while using a separate host-only SSH
  configuration for worker-side clone and reconciliation commands.
- Preserved existing connector grants when container-agent upserts omit the field, while retaining
  an explicit empty grant object as the supported way to clear access.
- Read filesystem connector artifacts through one symlink-safe, bounded file descriptor; serialized
  connector revision checks with configuration writes; evicted OAuth tokens rejected on writes
  without retrying the write; and rejected malformed fixed REST headers during configuration.

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
- Scoped Team-state migration verification to active project Git configurations, so historical
  promotion output may retain audit-only legacy path fields without blocking migration finalization.
- Fixed host-side container-worker managed-Git reconciliation to use a host-only SSH configuration
  with absolute session-key paths.
- Kept the local container-worker setup unit’s Git source allowlist aligned with the restricted
  write alias used during managed-Git reconciliation.
- Recorded a uniquely resolved managed repository on migrated session metadata, allowing
  reconciliation to dispatch Team agents instead of leaving their repository identity nested.

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
  `evander-team-addon` managed guidance sections when current
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
  without exposing avatar metadata to agent runtime prompts or core Evander
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
  active Evander session that owns projection and reconciliation.
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
  routing, and cleanup through existing Evander agent create/prompt/destroy nodes.
- Kept team runtime state canonical under the add-on while mirroring only a
  compact `metadata.team_addon` index into Evander session metadata.
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
  and leaving Evander core session/workflow schemas team-agnostic.

## 2026-06-09 - Optional Add-on Boundary

- Added a generic add-on loader with explicit module/entry-point discovery,
  runtime storage namespacing, Admin Web API/UI mount hooks, and workflow
  provider bridging for optional opinionated modules.
- Kept add-ons disabled by default and documented that domain-specific concepts
  such as hierarchical agent teams belong outside Evander core.
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

## 2026-06-04 - Evander Auto-dev Issue Owner Attribution

- Added auto-dev workflow owner gating with `gate:filter`, requiring a valid
  Azure DevOps `System.AssignedTo` identity before Evander claims a work item.
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
- Updated checked-in App demo workflow examples to queue the provisioning
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
  the theme toggle centered, and a smaller Evander logo on the right.

## 2026-05-25 - Hybrid Voice Delegation

- Added durable live voice query records so Realtime phone sessions can delegate
  file/tool/context questions back to the originating Evander agent without
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
- Documented the Teams direct-message trigger and updated example Evander packs
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

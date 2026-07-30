# 29. Troubleshooting Decision Tables

> **At a glance.** When Evander appears stuck, resist the urge to restart or delete things immediately. First identify the boundary where progress stopped and collect one trustworthy observation from that boundary. The tables in this chapter turn common symptoms into a short investigation and a safe next step.

## 29.1 Start with three questions

An operator can make a useful first report without knowing which database or service owns the work. Begin with:

1. **What did you expect to happen?** For example, “the assigned agent should have started work.”
2. **What is the last thing you can see?** The item arrived, the run started, an approval appeared, a session was created, or the agent finished.
3. **Which identifiers are visible?** Record the directory and any source-item, run, session, approval, task, or release ID shown in Admin Web.

Then find the closest symptom in the tables below. The first column helps describe the problem; the final column gives the safest next action. If that action requires host access, hand the captured details to an Evander administrator rather than deleting records or restarting unrelated services.

> <span class="sidebar-kicker">Deeper detail</span> **The diagnostic discipline**
>
> A technical investigation becomes much simpler when the evidence is read in the following order. These boundaries and records let an administrator follow the same work beyond what Admin Web projects.
>
> 1. **Name the intended outcome.** “The agent should have been woken” is more useful than “Team is broken.”
> 2. **Locate the boundary.** Browser/API, command transaction, durable store, orchestrator, execution backend, relay, or provider.
> 3. **Read the source of truth.** Use live service status, canonical store rows, and exact event/effect records before local speculation.
> 4. **Correlate identities.** Keep the session ID, directory, event ID, workflow run, effect execution, Team task/lease, and Git SHA together.
> 5. **Choose the smallest reversible action.** Retry through the owning API or worker; do not hand-edit a database or live workflow pack.
> 6. **Verify the outcome and the side effects.** A successful HTTP response is not proof that the downstream event, effect, wake, or release happened.

> <span class="sidebar-kicker">For administrators</span> **The first broad host check**
>
> ```bash
> evander-cli status --runtime-dir runtime --json
> ```
>
> Then narrow the investigation. Avoid running broad smoke suites without an explicit timeout; UI and browser processes can retain open handles after nearby assertions have passed.

## 29.2 Core control-plane symptoms

An operator can scan the **Symptom** and **Next safe action** columns first. The middle columns name the host evidence an administrator uses to distinguish similar-looking failures.

| Symptom | First authoritative observation | Likely boundary | Next safe action |
|---|---|---|---|
| Admin Web does not load | `systemctl status evander-admin-web.service` and its journal | Process, bind address, or `config/admin-web.env` | Correct the service/config problem, restart only that unit, then fetch its health/UI URL |
| Admin Web loads but a panel is stale | Compare the panel's API response with its canonical runtime store | Browser cache, API projection, or store/index lag | Hard-refresh; if the API is stale, inspect the owning service rather than rebuilding unrelated state |
| MCP plain `GET` returns `406` | Response is from `/mcp` on the expected host/port | Usually no fault: transport negotiation is missing | Treat it as liveness; test an actual initialized MCP client for protocol behavior |
| Guest cannot reach MCP | Guest `curl -i` to the configured gateway URL, then host firewall/unit status | Guest environment, network, firewall, or gateway | Verify `/etc/evander/session.env`, source `~/.profile`, and compare the configured host IP/port with firewall rules |
| Tool call is denied | Matching entry in `runtime/audit.jsonl` with subject, directory, tool, and reason | Identity, capability, policy, or approval | Correct the narrow policy/capability or approve through Admin Web; do not broaden unrelated tools |
| Approval appears stuck | Approval record and corresponding audit/tool-call state | Approval lifecycle or client retry | Decide/cancel through the approval surface; confirm the resumed call uses the intended request identity |
| Tool call succeeded but no workflow ran | Published event record plus the relevant provider/worker cursor (often `runtime/trigger-cursors-*.json`), not just the tool response | Event emission, trigger match, or cursor | Trace the event type and trigger predicates; then inspect the exact pack revision used |
| Trigger worker is inactive | `evander-trigger-orchestrator.service` status and journal | Worker service/config | Remember the executable is `evander-cli trigger-orchestrator`; run preflight and correct its config before restart |
| Workflow matched but a downstream node did not run | Trigger event, workflow event, and gate/effect execution rows in `runtime/orchestration.db` | Queueing, gate, lease, or effect worker | Trace the node/event identities and inspect `evander-effect-orchestrator.service`; retry only when idempotency semantics permit |
| Workflow edit reports a revision conflict | Current pack revision/lock from Admin API | Concurrent operator edit | Reload, reapply the intended change to the new revision, and save through the API/UI |
| Audit UI disagrees with journal | Tail of `runtime/audit.jsonl` versus query result in `runtime/audit.db` | Rebuildable audit index | Use the documented audit-index refresh; preserve the append-only journal |

## 29.3 Execution and relay symptoms

These symptoms all mean that coordination reached, or tried to reach, the temporary workspace. The table separates a workspace problem from failure in one of its controlled routes to a model, package source, tool, or approved target.

| Symptom | First authoritative observation | Likely boundary | Next safe action |
|---|---|---|---|
| VM creation fails before boot | Exact output of `launch_agent_vm.sh`, libvirt domain/network state | Host prerequisites, storage, libvirt network, or permissions | Keep disks under `/var/lib/libvirt/images/evander`; fix the named prerequisite and rerun the reconciliatory script |
| Guest tool is missing or stale | Tool version inside the guest and relevant bootstrap log | Optional bootstrap layer | Rerun the specific bootstrap script; do not rebuild the base image for one optional toolchain |
| Codex asks for login | `OPENAI_API_KEY`, relay base URL, and current shell profile inside guest | Guest bootstrap/profile | `source ~/.profile`; if values remain absent, rerun `bootstrap_codex_vm.sh` and verify relay-aware config |
| Codex calls `api.openai.com` directly | Effective client base URL inside guest | Relay configuration overwritten or absent | Restore the relay URL from session/bootstrap configuration; do not place the real upstream key in the guest |
| Stream starts and then truncates | OpenAI relay journal plus upstream/downstream status | Streaming relay lifetime, client disconnect, or upstream error | Preserve raw status/timing evidence; verify the relay keeps its upstream client open for the whole stream |
| NuGet restore cannot connect | `curl -fsS http://<host>:11666/healthz` from the relevant network | Firewall, relay service, or feed allowlist | Restore relay health first, then inspect the named feed; do not add general guest egress |
| HTTP target request is rejected | Session's effective target allowlist and relay response | Target grant, method/path policy, or expired session | Reissue the narrow session grant or correct the target; never inject a host secret as a shortcut |
| Container worker smoke fails | `evander-cli execution smoke-container-worker --json` plus worker journal | Control-plane URL/token, worker daemon, image, or network | Correct the exact failed hop; remember worker deployment is owned by `ansible/worker.yml` |
| Browser smoke hangs | Last useful output and process list after command timeout | Pending promise, `networkidle`, unclosed browser/server, or missing UI condition | Stop the process, retain artifacts, and run the focused scenario with explicit assertions and timeout |

## 29.4 Managed Git and artifact symptoms

The everyday question here is whether the agent received the intended code or files, and whether the exact reviewed result was the one published. The identifiers in the middle columns let an administrator prove that chain.

| Symptom | First authoritative observation | Likely boundary | Next safe action |
|---|---|---|---|
| Guest clone is denied | Restricted-SSH log plus managed repository ID/binding | Unknown ID, directory binding, key scope, or wrapper rule | Register/refresh through `evander-cli git-repos`; clone by returned `managed_repo_id` |
| Guest push is denied | Attempted ref and session-scoped allowed prefix | Ref protection or stale scoped key | Push to `refs/heads/agents/<session-id>/<topic>` with the provisioned restricted key |
| Published branch is not the reviewed commit | Review candidate SHA, approved SHA, lease expectation, and remote ref | Exact-SHA/lease gate | Stop publication; re-establish review evidence and promote the exact approved SHA through the managed effect |
| Managed Git operation left a worktree | Operation manifest/lock under `runtime/git-worktrees/` and process liveness | Interrupted operation or stale process | Use the service's reconciliation/cleanup path; do not blindly delete an active worktree |
| Resolved Team task worktree remains | `WorktreeCleanupCandidate` state, exact task/repository/path/branch, last safety check, and active dispatch/context references | Task-worktree cleanup safety or recovery | Preserve the checkout; repair the named state, commit, path, or dirty-file mismatch and retry the candidate through the reconciler |
| Cleanup is `dry_run_safe` but no bytes are reclaimed yet | Candidate `last_safety_check`, `eligible_at`, and whether live `worktree_cleanup_dry_run` is omitted, `true`, or `false` | Staged default or explicit audit-only mode | If omitted, wait through the retry interval for the later eligible pass, which repeats every check before removal. Explicit `true` never removes; change it deliberately only after reviewing evidence. Never substitute a recursive delete |
| Artifact reference exists but reviewer cannot read it | Artifact-space ACL and latest published version | Visibility or version publication | Grant the intended reader at the space boundary or republish correctly; do not copy private blobs into a public path |
| Review session lacks expected files | Review import list/errors in the lease wake context | Artifact publish/import effect | Resolve the reported import error and redispatch; reviewers should use imported `/artifacts` paths, not open `artifact-space:` refs directly |

## 29.5 Provider, communication, and wake symptoms

First establish whether the external item or message was saved inside Evander. Only then investigate whether it was eligible to start or resume work.

| Symptom | First authoritative observation | Likely boundary | Next safe action |
|---|---|---|---|
| Azure DevOps work item never enters a workflow | Matching trigger/query run, workflow-owned cursor, and effective ADO provider config | Trigger/query filters, credentials, cursor, or downstream proxy-issue effect | Trace `trigger:ado:work-item-matching` or `query:ado:work-items`; the ADO worker owns PR feedback/completion, not work-item intake |
| PR feedback is present but agent does not resume | `runtime/pr-feedback-store.json`, agent control state, and ADO worker journal | Ingest, dedupe, disabled agent, or wake | Correlate comment/PR/session IDs; enable only the intended agent or correct the failed wake |
| Scheduled wake is overdue | Scheduled wake record, host agent control state, and worker journal | Due-time parsing, disabled agent, session terminality, or worker | Confirm UTC/time zone and eligibility, then wake through the owning worker/API |
| Discord or Teams message is visible externally but absent internally | Ingest cursor/store and integration worker journal | External permission, poll/webhook, allowlist, or cursor | Repair ingest first; do not debug wake logic until a canonical inbox/message record exists |
| Communication trigger accepted a message but Team Chat has no corresponding row | Trigger run, fixed bridge configuration, target capabilities, and `ExternalChatIngressRecord` keyed by provider/target/source message | Communication-to-Team-Chat effect, fixed identity, author authority, or ingress persistence | Repair the exact bridge failure and replay the same source message ID; do not invent a new operator identity or duplicate dispatch |
| Message is stored but no wake occurs | Message/inbox record, wake eligibility, and wake worker log | Priority, handled state, disabled agent, dedupe, or active-session policy | Evaluate current wake rules and idempotency key; avoid duplicating a successful wake |
| Team Chat reply is visible but never reached the external source | `ExternalChatDeliveryRecord`, `team.chat.external_reply_requested`, send effect, success/failure recorder, and target capability | Persisted external delivery or communication provider | Keep the Team reply; inspect `pending`, `sending`, `sent`, `failed`, or `unknown` state and retry only when the persisted evidence makes that safe |
| Outbound notification fails | Effect execution and configured target in `runtime/communication-targets.db` | Target capability, credential, attachment, or provider | Correct the named target/provider and retry the effect only when its idempotency record permits |

## 29.6 Team command and UI symptoms

The Team interface is a view of durable Team state. A stale screen and a failed Team command can look similar, so compare what the interface shows with the response or record named in the table before attempting recovery.

| Symptom | First authoritative observation | Likely boundary | Next safe action |
|---|---|---|---|
| Team view shows old data | Current Team state API revision and browser network response | UI refresh/projection | Reload the view; if API state is current, capture a UI defect without changing `state.db` |
| Team mutation returns conflict | Current resource revision/token and submitted expected revision | Optimistic concurrency | Refresh, reassess intent against the current record, and resubmit with the new expected revision |
| Command says success but derived work is missing | Command receipt, outbox rows, and projection rows in `state.db` | Command transaction, outbox dispatch, or projection | Trace the command ID through outbox processing; never manufacture the missing projection by hand |
| Task looks terminal but project remains active or invalid | Recursive `TaskAssessment`, unresolved descendant IDs, active acceptance/review IDs, contribution coverage, and blocker IDs | Hierarchy or outcome-contract invariant | Repair the exact child, acceptance, contribution, or cancellation transition; do not overwrite project status |
| A dependency or parent appears blocked twice | Canonical blocker source plus descendant/dependency assessment | Duplicate presentation rather than two domain blockers | Follow the source blocker and its route; waiting ancestors should reference it rather than receive copied blockers |
| Role cannot be found in navigation | Teams view for the relevant team | UI mental model | Manage roles inside **Teams**; there is no top-level Roles view |
| Mail appears inconsistent | Durable inbox rows and sent/received projections | Status/filter misunderstanding | Treat Mail as views over one Team inbox collection; compare direction, recipient, pending, and handled fields |
| Team tool is unavailable to an agent | MCP tool catalog for that identity, session, and Team membership | Tool class, add-on registration, capability, or Team scope | Verify the add-on loaded and inspect the specific identity/capability decision |
| Agent is shown busy forever | Active lease, dispatch/session state, and active enqueue-prompt activity | Lease finalization, stuck prompt activity, or lost session | Reconcile through lease/session lifecycle operations; preserve the dispatch and prompt-activity evidence for diagnosis |

## 29.7 Team wake and delivery symptoms

The presence of a task, message, or deliverable does not by itself prove that the next assignment, wake, review, or release transition occurred. Follow the linked records in order and stop at the first missing transition.

| Symptom | First authoritative observation | Likely boundary | Next safe action |
|---|---|---|---|
| Pending Team inbox item does not wake an agent | Inbox priority/handled state, active lease/session, and wake decision payload | Eligibility or duplicate suppression | Current behavior treats non-low pending inbox work as wakeable; inspect the actual decision reason rather than assuming `response_requested` is required |
| Agent wakes with irrelevant or apparently incomplete context | Runtime operations payload, `context_mode`, and immutable dispatch ID for that exact wake | Context compaction, retained delta, scope, or stale related records | For `retained_delta`, use `team_get_current_dispatch_context` to hydrate the frozen scope. If the hydrated membership is wrong, fix the selector/relationship; do not expand every wake with the entire Team database |
| Task remains queued | Task assessment, assignment, unresolved descendants/dependencies, canonical blockers, Team capacity, active dispatches | Planning/dispatch eligibility | Follow the assessment's exact next action and confirm a durable dispatch was actually prepared and leased |
| Dispatch fails before prompt, then identical setup is not attempted again | Durable `TeamDispatch`, cause-specific preparation blocker, semantic generation, and every required repository/worktree/context | Repository, dependency-Git, or review-artifact preparation suppression | Fix the recorded cause and resolve its blocker, or make a meaningful task/dependency/project-Git/current-candidate change. Priority, timestamps, and dispatch churn do not unlock the same failure |
| Repeated empty wakes stop task dispatch | Open blocker with `source_kind=agent_execution_stall`, its events, exhausted dispatch, and affected task IDs | Agent-wide no-progress policy | Address the cause and resolve the canonical stall blocker through source-aware recovery; do not edit task states or counters |
| Deliverable was submitted but no review begins | Deliverable, review request, reviewer assignment, and outbox/event state | Review cascade creation or reviewer capacity | Trace deliverable ID to review request and dispatch; requeue only if no idempotent equivalent exists |
| Reviewer rejects but implementer is not reactivated | Review decision, deliverable cohort ID, every active cohort slot, follow-up inbox, and wake | Independent cascade failure or a sealed cohort still settling | For a sealed cohort, this is expected until every pending/blocked slot settles; recover blocked slots and let the round finish. For an independent review with no follow-up, trace the ordinary rejection cascade |
| Ephemeral delegate never exits | Delegation task, lease, child session, terminal result | Cleanup/finalization | Preserve result/artifacts, then run the supported terminal cleanup; do not silently convert it into a persistent agent |
| Release is waiting | Release package, verification records, approved candidate SHA, and gate status | Missing evidence, failed job, or lease mismatch | Satisfy the named gate; only partial, not-run, unknown, or missing verification can receive an authorized reasoned waiver—failed verification is unwaivable and requires a corrected reviewed candidate |
| Release verifier reports `pids_limit_exhausted` or `memory_limit_exhausted` | Structured worker failure cause, live project `resource_limits`, current verification run, and `retry_requested_by` | Container resource exhaustion, not a test-output guess | Repair or deliberately resize the live verifier policy and cite evidence before retrying. If an agent-requested retry already failed, escalate; another agent retry is forbidden |
| Human release review is blocked with no assignee | Human request, assignment generation, snapshotted groups, current Admin membership/RBAC, and assignment-unavailable blocker | Human assignment reconciliation | Repair membership or use audited reassignment on the same request; never skip the gate or create a replacement attempt |
| Human preview failed or expired | Human request candidate SHA/tree, preparation generation, safe target metadata, and blocker reason | Preview preparation | Repair the host-owned workflow target and resume the same request to create a new preparation generation |
| Human requested changes but no publication follows | Predecessor decision/findings, correction coordination parent and children, preparation blockers, accepted correction deliverables, and successor link | Human correction lifecycle | Route only ambiguous children, repair any pinned-candidate preparation blocker, and complete reviewed correction work. Team derives proposals and creates the changed successor automatically |
| Successor is approved but publication is refused | Candidate/approved SHA, human approval, confirmed carried finding IDs, and linked canonical-push promotion | Successor closure or exact-SHA publication guard | Confirm every carried finding and prove one succeeded canonical push for the same SHA/tree and target |
| Release reports success but remote ref differs | Remote ref readback and recorded published SHA | Publish/readback failure | Treat as failed publication, stop follow-on completion, and reconcile the remote with the exact approved SHA |

## 29.8 What to include when asking for help

An effective handoff contains a small, correlated packet. Capture what is available; you do not need every ID before asking for help:

- expected outcome and actual outcome;
- UTC time window and host/service identity;
- directory, session, workflow run/event/effect IDs;
- for Team: team, project, task, dispatch, lease, review, and release package IDs as applicable;
- for Git: managed repository ID, candidate/approved/published SHA, and target ref;
- relevant status rows or redacted JSON fields;
- exact command, exit status, timeout, and last useful output;
- what was already retried and why that retry was safe.

Do not include API keys, cookies, bearer tokens, private key material, raw environment files, or unrestricted database dumps. A screenshot should be dark-mode when practical and sanitized just like text evidence.

## 29.9 Before a recovery changes or deletes state

Stop and obtain a deliberate operator decision before any action that would:

- rewrite or discard live workflow-pack revisions;
- delete a runtime database, audit journal, managed bare repository, or active worktree;
- recursively delete a Team task worktree that has a pending or
  <code>recovery_required</code> cleanup candidate;
- reset a provider cursor that could replay external actions;
- broaden guest internet access or place a host credential inside a guest;
- bypass an approval, review, verification, or exact-SHA publication gate;
- synthesize Team projection rows instead of repairing the owning command/outbox path.

Recovery is complete only when the original outcome is verified **and** the control evidence agrees: store state, worker status, audit trail, and external readback where applicable.

## Source trail

- `docs/operations.md` — supported operational procedures and live-state locations.
- `docs/live-smoke-tests.md` — bounded end-to-end verification patterns.
- `docs/git-remotes.md` — restricted Git, exact-SHA promotion, and worktree operations.
- `docs/addons.md` — Team state, UI, and MCP boundaries.
- `src/evander_team_addon/providers.py` — current Team wake context, review, artifact, and release behavior.
- `src/evander_team_addon/services/commands/` and `src/evander_team_addon/repository.py` — Team command transactions, revisions, and outbox persistence.

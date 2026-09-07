# Team Add-on Wake Decision Tree

This is a visual companion to the Team Add-on wake logic. It separates the
durable paths that can lead to inference:

- debounced persistent-agent wake requests,
- queued Team Chat claims, which consume capacity before ordinary leases,
- normal persistent-agent wake leasing,
- role tasks assigned atomically with a dispatch when exact-role capacity is free,
- bounded `context_handoff` preparation before manual or idle rotation, and
- governance-manager wake leasing.

The same runtime cron tick drains the Team outbox. Case closure paths also emit a
`team.outbox.publish_requested` event after commit with the exact outbox record IDs; the immediate
publisher targets those records so an older backlog cannot consume its bounded batch. The cron
drain remains the recovery path. Both paths publish internal `team.*` events recorded by MCP tools.
External Team Chat reply delivery also runs from one of those events, but neither outbox
publication nor external send is itself a wake lease decision.

## Runtime Entrypoints

```mermaid
flowchart TD
    start([Team runtime pack enabled])

    start --> cron["team-cron<br/>default: every 15 minutes"]
    start --> wake_event["team.agent.wake_requested event<br/>default debounce: 1s"]
    start --> role_event["team.role_queue.wake_requested event<br/>creation, dependencies ready, capacity available"]
    start --> chat_event["team.chat.message_queued event"]
    start --> gov_event["team.governance.wake_requested event"]
    start --> outbox_request["team.outbox.publish_requested event<br/>exact record IDs when known"]

    cron --> normal_effect["effect:team:wake-due-agents<br/>limit 10, lease 900s, interval 900s"]
    wake_event --> requested_effect["effect:team:wake-due-agents<br/>agent_id from wake event, limit 1<br/>respect_wake_interval=false"]
    role_event --> role_effect["effect:team:wake-due-agents<br/>project and team from event, limit 10<br/>respect_wake_interval=false"]
    chat_event --> chat_effect["effect:team:wake-due-agents<br/>agent_id from chat event, limit 1"]
    cron --> gov_effect["effect:team:wake-governance-managers<br/>limit 10, lease 900s, interval 3600s"]
    gov_event --> gov_requested_effect["effect:team:wake-governance-managers<br/>group from event, limit 1"]
    cron --> outbox_effect["effect:team:publish-outbox-events<br/>limit 25"]
    outbox_request --> requested_outbox_effect["effect:team:publish-outbox-events<br/>requested record IDs, limit 25"]

    normal_effect --> wake_invocation
    requested_effect --> wake_invocation
    role_effect --> wake_invocation
    chat_effect --> wake_invocation
    wake_invocation["One wake-due-agents invocation"] --> cost_guard{"Team cost cap<br/>reached?"}
    cost_guard -->|Yes| cost_skip["Skip Team-agent wakes<br/>queued chat remains queued"]
    cost_guard -->|No| chat_first["Claim and dispatch queued Team Chat first<br/>within this invocation's limit"]
    chat_first --> capacity{"Lease capacity remains?"}
    capacity -->|No| wake_done["Invocation complete"]
    capacity -->|Yes| normal_tree["Evaluate normal persistent agents<br/>with remaining capacity"]

    gov_effect --> gov_tree["Evaluate governance groups"]
    gov_requested_effect --> gov_tree
    outbox_effect --> outbox["Publish pending outbox<br/>team.* runtime events"]
    requested_outbox_effect --> outbox

    external_message["Authenticated external communication message"] --> bridge["effect:team:enqueue-operator-chat-message<br/>fixed operator and conversation/agent"]
    bridge --> chat_event

    agent_reply["team_reply_to_chat<br/>persist Team reply first"] --> external_reply["team.chat.external_reply_requested"]
    external_reply --> send_external["effect:communication:send-message"]
    send_external -->|success| record_sent["effect:team:record-external-chat-delivery<br/>sent"]
    send_external -->|error| record_failed["effect:team:record-external-chat-delivery<br/>failed; Team reply retained"]

    classDef wake fill:#e8fff0,stroke:#208a42,color:#102a16;
    classDef skip fill:#fff1f0,stroke:#b42318,color:#3a0b07;
    classDef process fill:#f5f8ff,stroke:#3867d6,color:#102044;
    class normal_effect,requested_effect,chat_effect,gov_effect,gov_requested_effect,outbox_effect,requested_outbox_effect,wake_invocation,chat_first,normal_tree,gov_tree,outbox,cron,wake_event,chat_event,gov_event,outbox_request,bridge,external_reply,send_external process;
    class record_sent,wake_done wake;
    class cost_skip,record_failed skip;
```

`effect:team:enqueue-operator-chat-message` accepts exactly one fixed
`conversation_id` or `agent_id`, plus a fixed `operator_user_id`. Its
provider/target/source-message tuple makes ingress replay-idempotent.
`allow_team_mutations` defaults to false and requires an authenticated author
allowlist when enabled. A queued external message otherwise follows the same
Chat claim, lease, and `team_reply_to_chat` rules as a built-in operator
message.

Text messages delivered during an active dispatch include bounded earlier exchanges and reply
links from that conversation, both in MCP attention results and relay steering notes. Follow-ups
such as "that" should be resolved from the conversation; agents can read more history with
`team_list_conversation_messages` when needed.

An unlinked `team_send_operator_chat_message` to the operator awaiting the current Chat batch's
reply is rejected before posting. The error supplies `team_reply_to_chat`, the conversation ID,
and the batch message IDs. Replies to separately injected messages continue to use exact
`source_message_ids`; ordinary proactive messages remain available outside an outstanding batch.

Only a successfully resumed mutation-enabled Chat dispatch can seed ordinary
Team context and consume a pending ordinary rotation. A reply-only dispatch
stays exact to the claimed message and leaves that ordinary reset pending, even
when the underlying session uses `new_context`.

## Normal Persistent-Agent Wake Leasing

Before evaluating ordinary agents, the scheduler considers pending ownerless role
tasks in priority, creation-time, and ID order. A task needs satisfied dependencies,
passing execution gates, and an authorized active creator. Candidates must hold the
exact role in the project and pass the existing availability and authorization
checks, including the wake interval when the scan respects it. Existing ready tasks,
inbox responses, reviews, and recovery work take precedence for that agent. Each
dispatch can claim at most one new role task.

The provisional owner is retained only when that task enters the reserved dispatch;
both records and the scored assignment event commit in the same transaction.
Remaining tasks stay queued. Delivered tasks retain their owners across subsequent
wakes. A confirmed preparation failure before prompt submission can return only a
task newly claimed by that dispatch to the queue. Uncertain delivery and started
work retain ownership across later failed attempts. The periodic
scan remains the recovery path for missed queue wakes.

```mermaid
flowchart TD
    start([Candidate persistent agent])

    start --> limit{"Remaining ordinary<br/>lease capacity?"}
    limit -->|No| skip_limit["Skip"]
    limit -->|Yes| active{"Agent status is active<br/>and Team-assigned?"}

    active -->|No| skip_inactive["Skip"]
    active -->|Yes| stood_down{"Effective active stand-down?<br/>direct-agent hold or inherited subtree hold"}

    stood_down -->|Yes| skip_stood_down["Skip"]
    stood_down -->|No| filters{"Effect filters match?<br/>team_id, project_id, agent_id"}

    filters -->|No| skip_filter["Skip"]
    filters -->|Yes| cost{"Agent team<br/>cost-blocked?"}

    cost -->|Yes| skip_cost["Skip"]
    cost -->|No| busy{"Underlying session busy?"}

    busy -->|Yes| skip_busy["Skip"]
    busy -->|No| lease{"Existing wake lease<br/>still active?"}

    lease -->|Yes| skip_lease["Skip"]
    lease -->|No| gather["Gather actionable tasks, wakeable inbox,<br/>current reviews, due/assigned blockers,<br/>action feedback, and context rotation"]

    gather --> prep_recovery{"Due preparation-recovery<br/>blocker?"}
    prep_recovery -->|Yes| isolate["Isolate recovery scope:<br/>preparation blockers plus referencing inbox only;<br/>no task, review, project, repo, or worktree setup"]
    prep_recovery -->|No| work
    isolate --> work{"Any wake-eligible signal?"}

    work -->|No| clear_low["Clear low-priority batch<br/>and skip"]
    work -->|Yes| handoff{"Rotation pending and<br/>handoff preparation pending?"}

    handoff -->|Yes| lease_handoff["Reserve empty-scope context_handoff dispatch;<br/>lease 120s in retained context;<br/>grant only team_record_context_handoff"]
    lease_handoff --> handoff_terminal["Recorded, completed without summary,<br/>timed out, or resume failed"]
    handoff_terminal --> fresh_due["Keep rotation pending; clear handoff preparation;<br/>make fresh work dispatch due now"]

    handoff -->|No| immediate_inbox{"High or normal<br/>pending inbox?"}
    immediate_inbox -->|No| other_immediate{"Other immediate reason?<br/>rotation pending; due blocker; review;<br/>action feedback; or task work due"}
    other_immediate -->|No| low_only{"Wakeable low-priority<br/>inbox present?"}
    low_only -->|No| skip_not_due["Skip until task interval<br/>or another signal"]
    low_only -->|Yes| advance_low["Advance low-priority<br/>batch counter"]

    advance_low --> low_due{"Counter reached<br/>low_priority_wake_after_cycles?"}
    low_due -->|No| skip_low["Skip for now"]
    low_due -->|Yes| cause

    immediate_inbox -->|Yes| task_only{"Task work is the<br/>only immediate reason?"}
    other_immediate -->|Yes| task_only
    task_only -->|Yes| progress_throttle{"No-progress count<br/>at limit?"}
    progress_throttle -->|Yes| skip_throttle["Open/reuse execution-stall blocker;<br/>skip task-only wake"]
    progress_throttle -->|No| cause
    task_only -->|No| cause{"Choose cause in priority order:<br/>preparation recovery, non-low inbox,<br/>due blocker, review, low inbox,<br/>action feedback, scheduled"}

    cause --> reserve["Create immutable TeamDispatch in preparing<br/>with exact task, blocker, review, inbox,<br/>project, and managed-repository IDs"]
    reserve --> prepare{"Scoped repository/worktree and<br/>review-artifact preparation succeeds?"}
    prepare -->|No| prep_failed["Fail complete dispatch before inference;<br/>release reservation and record targeted incident"]
    prepare -->|Yes| leased["Mark dispatch leased"]

    leased --> lineage{"work_lineage policy?"}
    lineage -->|Yes; bound| bound_thread{"Thread already bound<br/>to this lineage?"}
    bound_thread -->|Yes| retained_delta
    bound_thread -->|No| fresh_full
    lineage -->|Yes; unbound| fresh_full
    lineage -->|No| context_mode{"Fresh lifecycle context?<br/>rotation/every_wake, first successful<br/>ordinary-context wake, or explicit new_context"}
    context_mode -->|Yes| fresh_full["Resume new context<br/>context_mode: fresh_full"]
    context_mode -->|No| retained_delta["Resume retained context<br/>context_mode: retained_delta"]

    fresh_full --> prompt["Prompt exact dispatch scope"]
    retained_delta --> prompt

    classDef wake fill:#e8fff0,stroke:#208a42,color:#102a16;
    classDef skip fill:#fff1f0,stroke:#b42318,color:#3a0b07;
    classDef process fill:#f5f8ff,stroke:#3867d6,color:#102044;
    class lease_handoff,fresh_due,leased,fresh_full,retained_delta,prompt wake;
    class skip_limit,skip_inactive,skip_stood_down,skip_filter,skip_cost,skip_busy,skip_lease,clear_low,skip_throttle,skip_not_due,skip_low,prep_failed skip;
    class gather,isolate,advance_low,reserve,handoff_terminal,lineage,bound_thread process;
```

The dispatch scope is immutable membership, not a stale copy of every record.
On a `retained_delta` wake, stable identity, role, subordinate, project,
outcome-catalog, and session detail is intentionally omitted. The agent can call
`team_get_current_dispatch_context` to hydrate current permitted detail for the
tasks, inbox items, blockers, reviews, projects, and Git contexts named by that
frozen scope; hydration cannot add a newly created record to the active
dispatch.

### Stand-Down Lease And Completion Boundary

```mermaid
flowchart TD
    set_hold["Set stand-down with required reason"] --> scope{"scope"}
    scope -->|agent| direct["Hold only the named agent"]
    scope -->|subtree| subtree["Hold named agent plus<br/>all reporting-line descendants"]

    direct --> future["Effective hold"]
    subtree --> future
    future --> candidate["Future wake candidate"]
    candidate --> no_lease["No new Team lease"]

    future --> running{"Dispatch already active?"}
    running -->|No| done["Hold remains durable"]
    running -->|Yes| predates{"Dispatch was leased before<br/>every effective hold was set?"}
    predates -->|No| reject["Reject Team mutation"]
    predates -->|Yes| exact{"Exact in-scope terminal completion?"}
    exact -->|No| reject
    exact -->|Yes| allow["Allow narrow completion only;<br/>do not clear the hold"]

    classDef wake fill:#e8fff0,stroke:#208a42,color:#102a16;
    classDef skip fill:#fff1f0,stroke:#b42318,color:#3a0b07;
    classDef process fill:#f5f8ff,stroke:#3867d6,color:#102044;
    class allow wake;
    class no_lease,reject skip;
    class set_hold,direct,subtree,future,done process;
```

The UI's **Stand down** action sends direct-agent scope and **Stand down
subtree** sends subtree scope. The Admin API and `team_stand_down_agent` use
`subtree` when scope is omitted. The pre-hold exception still enforces frozen
dispatch membership and allows only deliverable/review/ephemeral/chat completion
operations, or a support-task update whose complete mutation is exactly
`{"state": "finished"}`. Starting work, widening that update, touching an
out-of-scope record, or using a dispatch leased after the hold is rejected.

## Normal-Agent Wake Signal Sources

```mermaid
flowchart LR
    subgraph TaskWork["Task work"]
        new_task["Non-terminal task<br/>created or updated"]
        deps["Dependencies satisfied"]
        children["Open child tasks<br/>need supervision"]
        rework["Rejected or accepted-deliverable<br/>rework becomes actionable"]
    end

    subgraph Inbox["Wakeable inbox"]
        question["question<br/>default normal"]
        status_request["status_request<br/>default normal"]
        operator_directive["operator_directive<br/>normal"]
        review_request["review request<br/>normal"]
        review_rejected["review rejected<br/>normal"]
        low_notice["deliverable notice or<br/>ephemeral result notice<br/>low"]
        status_report["status_report<br/>normal wakes regardless of reply intent;<br/>low wakes when a reply is requested"]
    end

    subgraph Review["Review work"]
        review_pending["Current assigned review<br/>pending or in progress"]
    end

    subgraph Blocker["Blocker work"]
        blocker_due["Owned/delegated blocker<br/>due for action"]
        prep_recovery["Preparation-recovery blocker<br/>isolates the dispatch"]
    end

    subgraph Feedback["Action feedback"]
        invalid_action["Latest action failed,<br/>not dismissed,<br/>not feedback-delivered"]
    end

    subgraph Rotation["Context rotation"]
        manual["Manual request<br/>handoff preparation first"]
        idle["idle policy after real work and truly idle<br/>start configured continuous grace"]
        every_wake["every_wake policy<br/>direct fresh context on otherwise eligible wake"]
        work_lineage["work_lineage policy<br/>select one task family and resume its bound thread"]
    end

    TaskWork --> normal_due["Normal-agent due evaluation"]
    Inbox --> normal_due
    Review --> normal_due
    Blocker --> normal_due
    Feedback --> normal_due
    Rotation --> normal_due

    classDef source fill:#f5f8ff,stroke:#3867d6,color:#102044;
    classDef sink fill:#e8fff0,stroke:#208a42,color:#102a16;
    class new_task,deps,children,rework,question,status_request,operator_directive,review_request,review_rejected,low_notice,status_report,review_pending,blocker_due,prep_recovery,invalid_action,manual,idle,every_wake,work_lineage source;
    class normal_due sink;
```

### Context-Handoff Ordering

Manual and expired-idle rotation are deliberately two dispatches. The advisory handoff
must happen before the conversation is reset:

```mermaid
flowchart LR
    idle_start["Idle policy becomes truly idle<br/>persist idle start and expiry"] --> before{"New work before expiry?"}
    before -->|Yes| retain["Cancel idle window<br/>retain current context"]
    before -->|No; deadline reached| pending
    request["Manual request"] --> pending["context_rotation_pending<br/>context_handoff_preparation_pending"]
    pending --> handoff["context_handoff dispatch<br/>packaged mode: same_context<br/>120-second lease"]
    handoff --> sole["Sole Team tool:<br/>team_record_context_handoff<br/>one summary, max 1,200 chars"]
    sole --> outcome{"Summary recorded?"}
    outcome -->|Yes| recorded["Persist advisory summary<br/>complete preparation lease"]
    outcome -->|No; completion, timeout, or failure| failed["Record failed/timed_out status<br/>release preparation lease"]
    recorded --> due["Keep rotation pending<br/>next wake due now"]
    failed --> due
    due --> fresh["Next eligible work dispatch<br/>resume_mode: new_context<br/>context_mode: fresh_full"]

    every["every_wake policy"] --> eligible["Otherwise eligible work wake"]
    eligible --> fresh

    lineage["work_lineage policy"] --> bound{"Wake has one selected lineage?"}
    bound -->|No| fresh
    bound -->|Yes; first wake| fresh
    bound -->|Yes; known binding| resume["Resume that lineage's thread"]

    classDef wake fill:#e8fff0,stroke:#208a42,color:#102a16;
    classDef skip fill:#fff1f0,stroke:#b42318,color:#3a0b07;
    classDef process fill:#f5f8ff,stroke:#3867d6,color:#102044;
    class fresh,recorded,retain,resume wake;
    class failed skip;
    class idle_start,before,request,pending,handoff,sole,due,every,eligible,lineage,bound process;
```

An invalid or oversized handoff call does not release the active preparation
lease; it can still complete or time out. Durable Team state remains
authoritative, and the advisory summary never replaces task, review, blocker,
or deliverable records.

### Preparation Failure Retry Suppression

Preparation failures are keyed to the target and stable cause instead of
feeding an unchanged setup back into every cron tick:

```mermaid
flowchart TD
    failed["Scoped preparation fails before inference<br/>fail complete dispatch and release reservation"] --> target{"Failure target"}

    target -->|Task, repository, or dependency Git| task_key["Key incident by task + semantic<br/>preparation generation + cause fingerprint"]
    task_key --> task_blocker["Open or reuse routed blocker<br/>suppress that generation"]
    task_blocker --> semantic{"Meaningful task/project/dependency<br/>candidate state changed, or blocker resolved?"}
    semantic -->|No; scheduling-only edit| task_wait["Keep task preparation suppressed"]
    semantic -->|Yes| task_retry["Auto-resolve stale incident when applicable<br/>allow one fresh preparation attempt"]

    target -->|Inbox item| inbox_key["Key blocker by exact inbox item + cause"]
    inbox_key --> inbox_wait["Suppress only that item<br/>until the incident is resolved"]

    target -->|Review artifact or repository| review_blocked["Mark exact immutable review attempt blocked<br/>block_kind: system"]
    review_blocked --> review_retry["Recover through a valid retry successor;<br/>do not re-wake the failed attempt"]

    task_blocker --> recovery["Preparation-recovery wake"]
    inbox_key --> recovery
    review_blocked --> recovery
    recovery --> isolated["Freeze only recovery blocker(s)<br/>and referencing inbox items"]
    isolated --> no_setup["No task, review, project, repository,<br/>worktree, or artifact setup is rerun"]

    classDef wake fill:#e8fff0,stroke:#208a42,color:#102a16;
    classDef skip fill:#fff1f0,stroke:#b42318,color:#3a0b07;
    classDef process fill:#f5f8ff,stroke:#3867d6,color:#102044;
    class task_retry,recovery wake;
    class task_wait,inbox_wait,review_blocked skip;
    class failed,target,task_key,task_blocker,semantic,inbox_key,review_retry,isolated,no_setup process;
```

The recovery dispatch lets the accountable owner resolve, delegate, or
escalate the durable incident without retriggering the setup that failed.

## Manager Fallback

```mermaid
flowchart TD
    start([After normal-agent pass])

    start --> open_work{"Team has open work?<br/>non-terminal tasks, active deliverables,<br/>or pending initial-release decisions"}
    open_work -->|No| skip_no_open["No manager fallback"]
    open_work -->|Yes| team_ok{"Team active, not cost-blocked,<br/>and no Team agent has<br/>an active/current tick lease?"}

    team_ok -->|No| skip_team["No manager fallback"]
    team_ok -->|Yes| manager_ok{"Manager exists,<br/>active, not stood down,<br/>not leased?"}

    manager_ok -->|No| skip_manager["No manager fallback"]
    manager_ok -->|Yes| interval{"Manager next_wake_at<br/>missing or due?"}

    interval -->|No| skip_interval["No manager fallback yet"]
    interval -->|Yes| lease_manager["Lease manager<br/>wake_reason: manager_fallback<br/>include stalled team context"]

    classDef wake fill:#e8fff0,stroke:#208a42,color:#102a16;
    classDef skip fill:#fff1f0,stroke:#b42318,color:#3a0b07;
    class lease_manager wake;
    class skip_no_open,skip_team,skip_manager,skip_interval skip;
```

A newly accepted ordinary root deliverable is tracked as a pending
initial-release decision when its project has release policy. That durable row
keeps the Team eligible for manager fallback even if the project lead handled
the root-resolution inbox notice. The fallback prompt and dispatch carry the
same bounded deliverable ID set. A release request changes each included row to
`release_requested`; `team_decide_initial_release` changes it to audited
`not_planned`. Either transition removes the row from later fallback wakes, and
an audited `reopen` makes it pending again. Legacy accepted deliverables have no
disposition row and do not generate retroactive fallback work.

## Governance-Manager Wake Leasing

```mermaid
flowchart TD
    start([Candidate governance group])

    start --> limit{"Lease limit<br/>already reached?"}
    limit -->|Yes| skip_limit["Skip"]
    limit -->|No| group_active{"Group not archived<br/>and filter matches?"}

    group_active -->|No| skip_group["Skip"]
    group_active -->|Yes| manager_active{"Manager exists<br/>and is active?"}

    manager_active -->|No| skip_manager["Skip"]
    manager_active -->|Yes| manager_lease{"Manager already has<br/>active normal-agent lease?"}

    manager_lease -->|Yes| skip_agent_lease["Skip"]
    manager_lease -->|No| gov_lease{"Governance runtime<br/>lease still active?"}

    gov_lease -->|Yes| skip_gov_lease["Skip"]
    gov_lease -->|No| gather["Gather signals:<br/>pending agenda,<br/>cost cap signals,<br/>blocked-team signals"]

    gather --> interval{"Governance next_wake_at<br/>missing or at/past now?"}
    interval -->|Yes| agenda{"Pending agenda items?"}
    interval -->|No| changed_signals{"Cost or blocked-team<br/>signal changed?"}

    agenda -->|Yes| lease_agenda["Lease manager<br/>wake_reason: first agenda item type"]
    agenda -->|No| changed_signals

    changed_signals -->|No| update_or_skip["Update signal fingerprints<br/>if they changed to empty;<br/>otherwise skip"]
    changed_signals -->|Yes| cost_changed{"Changed cost signal?"}

    cost_changed -->|Yes| lease_cost["Lease manager<br/>wake_reason: cost_cap_warning<br/>or cost_cap_reached"]
    cost_changed -->|No| lease_blocked["Lease manager<br/>wake_reason: blocked_team"]

    lease_agenda --> lease_side_effects
    lease_cost --> lease_side_effects
    lease_blocked --> lease_side_effects

    lease_side_effects["Set governance runtime lease:<br/>active_dispatch_id, wake_reason,<br/>last_wake_at, wake_leased_until,<br/>next_wake_at; persist signal fingerprints"]

    classDef wake fill:#e8fff0,stroke:#208a42,color:#102a16;
    classDef skip fill:#fff1f0,stroke:#b42318,color:#3a0b07;
    classDef process fill:#f5f8ff,stroke:#3867d6,color:#102044;
    class lease_agenda,lease_cost,lease_blocked,lease_side_effects wake;
    class skip_limit,skip_group,skip_manager,skip_agent_lease,skip_gov_lease,update_or_skip skip;
    class gather process;
```

## Governance Signal Sources

```mermaid
flowchart LR
    subgraph Agenda["Pending governance agenda"]
        manual_agenda["Operator/Admin-created<br/>agenda item"]
        team_escalation["Team manager escalation<br/>when team escalates to governance"]
        membership["Governance member teams<br/>added or removed"]
    end

    subgraph Cost["Cost signals"]
        warning["Member Team estimated cost<br/>at its warning ratio"]
        reached["Member Team estimated cost<br/>at or over max_cost_usd"]
    end

    subgraph Blockers["Blocked-team signals"]
        blocked_project["Member Team project<br/>status blocked"]
        blocked_task["Member Team task<br/>state blocked"]
    end

    Agenda --> gov_due["Governance-manager<br/>due evaluation"]
    Cost --> gov_due
    Blockers --> gov_due

    classDef source fill:#f5f8ff,stroke:#3867d6,color:#102044;
    classDef sink fill:#e8fff0,stroke:#208a42,color:#102a16;
    class manual_agenda,team_escalation,membership,warning,reached,blocked_project,blocked_task source;
    class gov_due sink;
```

## UI Badge Caveat

```mermaid
flowchart TD
    ui["UI runtime row<br/>wake_due flag"] --> mirrors["Mirrors normal-agent due logic<br/>with default thresholds"]
    mirrors --> not_final["Not the final lease decision"]
    not_final --> missing["Provider-level guards may still block:<br/>cost cap, limit, scoped effect filter,<br/>active lease, queued chat priority,<br/>governance path"]

    classDef caveat fill:#fff7e6,stroke:#c77700,color:#3d2500;
    class ui,mirrors,not_final,missing caveat;
```

# Team Add-on Wake Decision Tree

This is a visual companion to the Team Add-on wake logic. It separates the four
wake paths:

- debounced persistent-agent wake requests,
- queued Team Chat wake dispatches,
- normal persistent-agent wake leasing,
- governance-manager wake leasing.

The same runtime cron tick also drains the Team outbox. That path publishes
internal `team.*` events recorded by MCP tools, but it is not itself a wake
lease decision.

## Runtime Entrypoints

```mermaid
flowchart TD
    start([Team runtime pack enabled])

    start --> cron["team-cron<br/>default: every 15 minutes"]
    start --> wake_event["team.agent.wake_requested event<br/>default debounce: 10s"]
    start --> chat_event["team.chat.message_queued event"]

    cron --> normal_effect["effect:team:wake-due-agents<br/>limit 10, lease 900s, interval 900s"]
    wake_event --> requested_effect["effect:team:wake-due-agents<br/>agent_id from wake event, limit 1<br/>respect_wake_interval=false"]
    chat_event --> chat_effect["effect:team:wake-due-agents<br/>agent_id from chat event, limit 1"]
    cron --> gov_effect["effect:team:wake-governance-managers<br/>limit 10, lease 900s, interval 3600s"]
    cron --> outbox_effect["effect:team:publish-outbox-events<br/>limit 25"]

    normal_effect --> cost_guard{"Team cost cap<br/>reached?"}
    requested_effect --> cost_guard
    chat_effect --> cost_guard
    cost_guard -->|Yes| cost_skip["Skip Team-agent wakes<br/>queued chat remains queued"]
    cost_guard -->|No| normal_tree["Evaluate normal persistent agents"]

    gov_effect --> gov_tree["Evaluate governance groups"]
    outbox_effect --> outbox["Publish pending outbox<br/>team.* runtime events"]

    classDef wake fill:#e8fff0,stroke:#208a42,color:#102a16;
    classDef skip fill:#fff1f0,stroke:#b42318,color:#3a0b07;
    classDef process fill:#f5f8ff,stroke:#3867d6,color:#102044;
    class normal_effect,requested_effect,chat_effect,gov_effect,outbox_effect,normal_tree,gov_tree,outbox,cron,wake_event,chat_event process;
    class cost_skip skip;
```

## Normal Persistent-Agent Wake Leasing

```mermaid
flowchart TD
    start([Candidate persistent agent])

    start --> limit{"Lease limit<br/>already reached?"}
    limit -->|Yes| skip_limit["Skip"]
    limit -->|No| active{"Agent status<br/>is active?"}

    active -->|No| skip_inactive["Skip"]
    active -->|Yes| stood_down{"Active stand-down<br/>on agent or subtree?"}

    stood_down -->|Yes| skip_stood_down["Skip"]
    stood_down -->|No| filters{"Effect filters match?<br/>team_id, project_id, agent_id"}

    filters -->|No| skip_filter["Skip"]
    filters -->|Yes| cost{"Agent team<br/>cost-blocked?"}

    cost -->|Yes| skip_cost["Skip"]
    cost -->|No| lease{"Existing wake lease<br/>still active?"}

    lease -->|Yes| skip_lease["Skip"]
    lease -->|No| gather["Gather wake signals:<br/>active tasks, pending inbox,<br/>action feedback, context rotation"]

    gather --> work{"Any wakeable work?"}
    work -->|No| clear_low["Clear low-priority batch<br/>and skip"]
    work -->|Yes| rotation{"Context rotation<br/>pending?"}

    rotation -->|Yes| lease_now_rotation["Lease now<br/>resume_mode becomes new_context"]
    rotation -->|No| immediate_inbox{"High or normal<br/>response-requested inbox?"}

    immediate_inbox -->|Yes| lease_inbox["Lease now<br/>wake_reason: inbox"]
    immediate_inbox -->|No| action_feedback{"Latest failed action<br/>needs feedback?"}

    action_feedback -->|Yes| lease_feedback["Lease now<br/>wake_reason: action_feedback"]
    action_feedback -->|No| due_time{"respect_wake_interval=false<br/>or next_wake_at due?"}

    due_time -->|Yes| task_ready{"Task work ready?<br/>dependencies satisfied<br/>or open child tasks"}
    due_time -->|No| low_only{"Only delayed<br/>low-priority inbox?"}

    task_ready -->|Yes| progress_throttle{"Task-only wake and<br/>no-progress count at limit?"}
    task_ready -->|No| low_only

    progress_throttle -->|Yes| skip_throttle["Skip task-only wake"]
    progress_throttle -->|No| lease_scheduled["Lease now<br/>wake_reason: scheduled"]

    low_only -->|No| skip_not_due["Skip until interval"]
    low_only -->|Yes| advance_low["Advance low-priority<br/>batch counter"]

    advance_low --> low_due{"Counter reached<br/>low_priority_wake_after_cycles?"}
    low_due -->|No| skip_low["Skip for now"]
    low_due -->|Yes| lease_low["Lease now<br/>wake_reason: low_priority_inbox"]

    lease_now_rotation --> lease_side_effects
    lease_inbox --> lease_side_effects
    lease_feedback --> lease_side_effects
    lease_scheduled --> lease_side_effects
    lease_low --> lease_side_effects

    lease_side_effects["Set runtime lease:<br/>active_dispatch_id, wake_reason,<br/>last_wake_at, wake_leased_until,<br/>next_wake_at; clear low-priority batch"]

    classDef wake fill:#e8fff0,stroke:#208a42,color:#102a16;
    classDef skip fill:#fff1f0,stroke:#b42318,color:#3a0b07;
    classDef process fill:#f5f8ff,stroke:#3867d6,color:#102044;
    class lease_now_rotation,lease_inbox,lease_feedback,lease_scheduled,lease_low,lease_side_effects wake;
    class skip_limit,skip_inactive,skip_stood_down,skip_filter,skip_cost,skip_lease,clear_low,skip_throttle,skip_not_due,skip_low skip;
    class gather,advance_low process;
```

## Normal-Agent Wake Signal Sources

```mermaid
flowchart LR
    subgraph TaskWork["Task work"]
        new_task["Non-terminal task<br/>created or updated"]
        deps["Dependencies satisfied"]
        children["Open child tasks<br/>need supervision"]
    end

    subgraph Inbox["Requested inbox"]
        question["question<br/>default normal"]
        status_request["status_request<br/>default normal"]
        operator_directive["operator_directive<br/>normal"]
        review_request["review request<br/>normal"]
        review_rejected["review rejected<br/>normal"]
        low_notice["deliverable notice or<br/>ephemeral result notice<br/>low"]
        requested_report["status_report with<br/>response_requested true"]
    end

    subgraph Feedback["Action feedback"]
        invalid_action["Latest action failed,<br/>not dismissed,<br/>not feedback-delivered"]
    end

    subgraph Rotation["Context rotation"]
        manual["Manual request"]
        idle["idle policy after real work<br/>and truly idle"]
        every_wake["every_wake policy<br/>on otherwise eligible wake"]
    end

    TaskWork --> normal_due["Normal-agent due evaluation"]
    Inbox --> normal_due
    Feedback --> normal_due
    Rotation --> normal_due

    classDef source fill:#f5f8ff,stroke:#3867d6,color:#102044;
    classDef sink fill:#e8fff0,stroke:#208a42,color:#102a16;
    class new_task,deps,children,question,status_request,operator_directive,review_request,review_rejected,low_notice,requested_report,invalid_action,manual,idle,every_wake source;
    class normal_due sink;
```

## Manager Fallback

```mermaid
flowchart TD
    start([After normal-agent pass])

    start --> open_work{"Team has open work?<br/>non-terminal tasks or<br/>active deliverables"}
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
        warning["Member Team estimated cost<br/>at warning ratio"]
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

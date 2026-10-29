// Agent labels, runtime status, inbox, and work summaries.
import {
  escapeAttr,
  escapeHtml,
  formatCount,
  humanizeToken,
  isFutureTimestamp,
  numberOrNull,
  numberOrZero,
  taskStateLabel,
  text,
  uniqueTextList,
  values,
} from "./team-ui-format.js";
import { API_BASE, inboxItemLabels, terminalTaskStates } from "./team-ui-config.js";

export function createTeamAgentsController(peers) {

  function bindInboxPopoverPositioning() {
    document.addEventListener("pointerover", (event) => {
      scheduleInboxPopoverPosition(event.target);
    });
    document.addEventListener("focusin", (event) => {
      scheduleInboxPopoverPosition(event.target);
    });
    window.addEventListener("resize", () => {
      document.querySelectorAll(".agent-inbox-indicator").forEach((indicator) => {
        scheduleInboxPopoverPosition(indicator);
      });
    });
  }

  function scheduleInboxPopoverPosition(target) {
    if (!(target instanceof Element)) {
      return;
    }
    const indicator = target.closest(".agent-inbox-indicator");
    const popover = indicator?.querySelector(".agent-inbox-popover");
    if (!indicator || !popover) {
      return;
    }

    indicator.style.setProperty("--agent-inbox-popover-offset-x", "0px");
    popover.style.setProperty("--agent-inbox-popover-offset-x", "0px");
    window.requestAnimationFrame(() => {
      clampInboxPopoverToViewport(indicator, popover);
    });
  }

  function clampInboxPopoverToViewport(indicator, popover) {
    if (!indicator.isConnected || !popover.isConnected) {
      return;
    }

    const viewportWidth = document.documentElement.clientWidth || window.innerWidth;
    const viewportPadding = 12;
    const rect = popover.getBoundingClientRect();
    let offsetX = 0;
    const maxRight = viewportWidth - viewportPadding;

    if (rect.right > maxRight) {
      offsetX -= rect.right - maxRight;
    }
    if (rect.left + offsetX < viewportPadding) {
      offsetX += viewportPadding - (rect.left + offsetX);
    }

    const offsetValue = `${Math.round(offsetX)}px`;
    indicator.style.setProperty("--agent-inbox-popover-offset-x", offsetValue);
    popover.style.setProperty("--agent-inbox-popover-offset-x", offsetValue);
  }

  function navigateToAgentSession(sessionId) {
    const normalizedSessionId = text(sessionId);
    if (!normalizedSessionId || !window.parent?.postMessage) {
      return;
    }
    window.parent.postMessage(
      {
        type: "admin-navigate-to-session",
        session_id: normalizedSessionId,
      },
      window.location.origin,
    );
  }

  function agentRuntimeDetailRows(details) {
    const sessionLine = sessionHealthRuntimeLine(details.sessionHealth, details.sessionId);
    const runtime = details.runtime || {};
    if (!runtime.present) {
      return [sessionLine, "No runtime snapshot"].filter(Boolean);
    }
    const rows = [sessionLine, runtime.summary || runtime.status || "Runtime idle"].filter(Boolean);
    if (runtime.lastHandoffSummary) {
      rows.push(`Last handoff ${runtime.lastHandoffSummary}`);
    }
    if (runtime.contextClearingPolicy !== "off" || runtime.contextRotationPending) {
      rows.push(runtime.contextClearingPolicy === "work_lineage"
        ? "Context routing: Work lineage"
        : `Context clearing ${peers.selects.contextClearingPolicyLabel(runtime.contextClearingPolicy)}${
          runtime.contextRotationPending ? " - pending" : ""
        }${runtime.contextIdleRotationDueAt ? ` - idle grace until ${runtime.contextIdleRotationDueAt}` : ""}`,
      );
    }
    if (!["every_wake", "work_lineage"].includes(runtime.contextClearingPolicy)) {
      if (!runtime.lastContextRotatedAt) {
        rows.push("Latest context reset: no completed context reset yet");
      } else {
        rows.push(`Latest context reset: ${peers.app.formatTimelineTimestamp(runtime.lastContextRotatedAt)}`);
        rows.push(
          runtime.inputTokensSinceContextReset === null
            ? "Input tokens since latest reset: unavailable"
            : `Input tokens since latest reset: ${formatCount(runtime.inputTokensSinceContextReset)}`,
        );
      }
    }
    return rows;
  }

  function renderAgentOpenTasks(openTasks) {
    if (!openTasks.length) {
      return "";
    }
    return `
      <div class="meta-line">Open tasks:</div>
      <ul class="inline-list">
        ${openTasks
          .map((task) => {
            return `<li>${escapeHtml(task.title)} <span class="state-pill ${escapeHtml(task.state)}">${escapeHtml(taskStateLabel(task))}</span></li>`;
          })
          .join("")}
      </ul>
    `;
  }

  function pendingInboxForAgent(agentId) {
    return values(peers.app.state.inbox)
      .filter(
        (item) =>
          item.recipient_agent_id === agentId &&
          item.state !== "handled" &&
          pendingInboxItemIsCurrent(item),
      )
      .sort(peers.workspace.compareWorkspaceInboxItems);
  }

  function pendingInboxItemIsCurrent(item) {
    const runtimeRow = (peers.app.state.runtime_operations?.attention || []).find(
      (row) => text(row.inbox_item_id) === text(item.id),
    );
    if (runtimeRow) {
      return peers.operations.runtimeAttentionGroup(runtimeRow) === "active_obligation";
    }
    const task = item.task_id ? peers.app.state.tasks?.[item.task_id] : null;
    if (task && terminalTaskStates.has(text(task.state))) {
      return false;
    }
    return item.response_requested !== false;
  }

  function agentTaskStats(agentId) {
    const tasks = peers.tasks.orderedTasksForAgent(agentId);
    const openTasks = tasks.filter((task) => !terminalTaskStates.has(task.state));
    return {
      tasks,
      openTasks,
      openCount: openTasks.length,
      blockedWaitingCount: openTasks.filter((task) => task.state === "blocked" || task.state === "waiting_for_review").length,
      totalCount: tasks.length,
    };
  }

  function ephemeralCapacity(agent) {
    return numberOrZero(agent?.ephemeral_policy?.max_concurrent);
  }

  function projectLabel(projectOrId) {
    const projectId = text(projectOrId?.id ?? projectOrId);
    const project =
      projectOrId && typeof projectOrId === "object"
        ? projectOrId
        : peers.app.state.projects[projectId];
    if (!project) {
      return projectId;
    }
    return text(project.name) || text(project.id) || projectId;
  }

  function agentProjectIds(agent) {
    const directIds = uniqueTextList(agent?.project_ids);
    if (directIds.length) {
      return directIds;
    }
    const assignmentIds = Array.isArray(agent?.project_assignments)
      ? agent.project_assignments.map((assignment) => text(assignment?.project_id)).filter(Boolean)
      : [];
    if (assignmentIds.length) {
      return uniqueTextList(assignmentIds);
    }
    const legacyProjectId = text(agent?.project_id);
    return legacyProjectId ? [legacyProjectId] : [];
  }

  function agentLeadProjectIds(agent) {
    const directIds = uniqueTextList(agent?.lead_project_ids);
    if (directIds.length) {
      return directIds;
    }
    const assignmentIds = Array.isArray(agent?.project_assignments)
      ? agent.project_assignments
          .filter((assignment) => text(assignment?.kind) === "project_lead")
          .map((assignment) => text(assignment?.project_id))
          .filter(Boolean)
      : [];
    return uniqueTextList(assignmentIds);
  }

  function agentProjectSummary(agent) {
    return agentProjectIds(agent).map(projectLabel).filter(Boolean).join(", ");
  }

  function agentStatus(agent) {
    return text(agent?.status) || "active";
  }

  function agentSessionBackend(agentData) {
    const health = agentData?.sessionHealth || {};
    return text(health.agentExecutionBackend || health.executionBackend || health.vmProvider).toLowerCase();
  }

  function agentUsesContainerBackend(agentData) {
    return agentSessionBackend(agentData) === "container_worker_vm";
  }

  function agentHasActiveRuntime(agentData) {
    const runtimeRow = runtimeOperationForAgent(agentData?.id);
    const activeDispatchId = text(runtimeRow?.active_dispatch_id || agentData?.runtime?.activeDispatchId);
    if (activeDispatchId) {
      return true;
    }
    const health = agentData?.sessionHealth || {};
    const activeExecutionCount = Number(health.activeExecutionCount);
    if (Number.isFinite(activeExecutionCount) && activeExecutionCount > 0) {
      return true;
    }
    const executionStatus = text(health.agentExecutionStatus).toLowerCase();
    return ["preparing", "starting", "running"].includes(executionStatus);
  }

  function renderAgentRosterStatusPill(agentData) {
    const status = text(agentData?.status) || "active";
    if (!status) {
      return "";
    }
    if (status === "active" && agentUsesContainerBackend(agentData) && !agentHasActiveRuntime(agentData)) {
      return "";
    }
    return `<span class="state-pill ${escapeHtml(status)}">${escapeHtml(status)}</span>`;
  }

  function agentCardData(agent) {
    const taskStats = agentTaskStats(agent.id);
    const runtime = runtimeSummary(agent.id);
    const directSubordinateIds = uniqueTextList(agent?.subordinate_agent_ids);
    const projectIds = agentProjectIds(agent);
    const projectName = projectIds.map(projectLabel).join(", ");
    const reportsToId = text(agent?.reports_to_agent_id);
    const ephemeralCurrentCount = activeEphemeralAssignmentsForAgent(agent.id).length;
    const inboxItems = pendingInboxForAgent(agent.id);
    const standDowns = effectiveStandDowns(agent);
    const inboxIndicatorItems = {
      status_request: inboxItems.filter((item) => text(item.item_type) === "status_request"),
      progress_report: inboxItems.filter((item) => text(item.item_type) === "progress_report"),
    };
    const activitySummary = normalizeActivitySummary(
      agent?.activity_summary || peers.app.state.agent_activity_summaries?.[text(agent?.id)],
    );

    return {
      id: text(agent?.id),
      label: agentLabel(agent),
      role: agentRoleLabel(agent),
      roleId: text(agent?.role_id),
      avatar: agent?.avatar || null,
      voice_profile: agent?.voice_profile || null,
      status: agentStatus(agent),
      sessionId: text(agent?.session_id),
      projectId: projectIds[0] || null,
      projectIds,
      projectLabel: projectName || null,
      reportsToLabel: reportsToId || "operator",
      directSubordinateCount: directSubordinateIds.length,
      taskStats,
      inboxCount: inboxItems.length,
      inboxIndicatorItems,
      runtime,
      sessionHealth: agentSessionHealth(agent.id),
      activitySummary,
      effectiveStandDowns: standDowns,
      standDownActive: Boolean(agent?.stand_down_active) || standDowns.length > 0,
      ephemeralCapacity: ephemeralCapacity(agent),
      ephemeralCurrentCount,
    };
  }

  function normalizeActivitySummary(summary) {
    if (!summary || typeof summary !== "object") {
      return null;
    }
    const headline = text(summary.headline);
    const workingOn = text(summary.working_on);
    const waitingFor = text(summary.waiting_for);
    if (!headline && !workingOn && !waitingFor) {
      return null;
    }
    return {
      headline,
      working_on: workingOn,
      waiting_for: waitingFor,
      tone: activityToneClass(summary.tone),
      source: text(summary.source) || "derived",
      generated_at: text(summary.generated_at),
      reason_refs: Array.isArray(summary.reason_refs) ? summary.reason_refs : [],
    };
  }

  function activityToneClass(tone) {
    const normalized = text(tone).toLowerCase();
    return ["working", "waiting", "blocked", "idle", "attention", "offline"].includes(normalized)
      ? normalized
      : "";
  }

  function effectiveStandDowns(record) {
    const values = Array.isArray(record?.effective_stand_downs)
      ? record.effective_stand_downs
      : Array.isArray(record?.effectiveStandDowns)
        ? record.effectiveStandDowns
        : [];
    return values.filter((item) => item && typeof item === "object");
  }

  function revocableRootStandDown(agentId, record) {
    const normalizedAgentId = text(agentId);
    return effectiveStandDowns(record).find((item) => {
      return ["operator", "superior"].includes(text(item.source))
        && text(item.agent_id) === normalizedAgentId
        && text(item.root_agent_id || item.agent_id) === normalizedAgentId
        && !Boolean(item.inherited);
    }) || null;
  }

  function renderAgentStandDownControls(agentId, record) {
    const normalizedAgentId = text(agentId);
    if (!normalizedAgentId) {
      return "";
    }
    const standDowns = effectiveStandDowns(record);
    const active = Boolean(record?.standDownActive || record?.stand_down_active) || standDowns.length > 0;
    if (!active) {
      return `
        <button class="record-action" type="button" data-stand-down-agent-id="${escapeAttr(normalizedAgentId)}" data-stand-down-scope="agent">Stand down</button>
        <button class="record-action" type="button" data-stand-down-agent-id="${escapeAttr(normalizedAgentId)}" data-stand-down-scope="subtree">Stand down subtree</button>
      `;
    }
    const revocableRoot = revocableRootStandDown(normalizedAgentId, record);
    const summary = agentStandDownSummary({ effectiveStandDowns: standDowns });
    const unavailableReason = standDownRevokeUnavailableReason(normalizedAgentId, standDowns);
    return `
      <span class="state-pill blocked" title="${escapeAttr(summary)}">stood down</span>
      ${
        revocableRoot
          ? `<button class="record-action" type="button" data-stand-down-revoke-agent-id="${escapeAttr(normalizedAgentId)}">Revoke stand-down</button>`
          : `<button class="record-action" type="button" data-stand-down-revoke-agent-id="${escapeAttr(normalizedAgentId)}" disabled aria-disabled="true" title="${escapeAttr(unavailableReason)}">Revoke stand-down</button>`
      }
    `;
  }

  function standDownRevokeUnavailableReason(agentId, standDowns) {
    const primary = standDowns[standDowns.length - 1];
    if (!primary) {
      return "This stand-down cannot be revoked here because its source details are unavailable.";
    }
    const rootAgentId = text(primary.root_agent_id || primary.agent_id);
    const rootAgent = peers.app.state.agents?.[rootAgentId];
    const rootLabel = rootAgent ? agentLabel(rootAgent) : rootAgentId || "another agent";
    const inherited = Boolean(primary.inherited) || (rootAgentId && rootAgentId !== text(agentId));
    if (text(primary.source) === "superior") {
      return inherited
        ? `This stand-down is inherited from ${rootLabel}. Revoke it on ${rootLabel}.`
        : "This stand-down cannot be revoked here. Revoke it at its source.";
    }
    if (inherited) {
      return `This stand-down is inherited from ${rootLabel}. Revoke the stand-down on that agent.`;
    }
    return "This stand-down cannot be revoked here. Revoke it at its source.";
  }

  function agentStandDownSummary(record) {
    const standDowns = effectiveStandDowns(record);
    const primary = standDowns[standDowns.length - 1] || {};
    const source = text(primary.source) || "stand-down";
    const scope = text(primary.scope) || "subtree";
    const root = text(primary.root_agent_id || primary.agent_id);
    const reason = text(primary.reason);
    const inherited = Boolean(primary.inherited) && root ? ` inherited from ${root}` : "";
    const direct = scope === "agent" ? " (agent only)" : "";
    return `${humanizeToken(source)} stand-down${direct}${inherited}${reason ? `: ${reason}` : ""}`;
  }

  function runtimeSummary(agentId) {
    const runtime = peers.app.state.agent_runtimes?.[agentId];
    if (!runtime) {
      return {
        present: false,
        status: "",
        summary: "",
        activeDispatchId: null,
        wakeReason: null,
        nextWakeAt: null,
        wakeLeasedUntil: null,
        lastWakeAt: null,
        lowPriorityBatchStartedAt: null,
        lowPriorityBatchCycleCount: 0,
        lowPriorityBatchDueAfterCycles: null,
        contextClearingPolicy: "off",
        contextClearingIdleGraceSeconds: 0,
        autoContextClearingEnabled: false,
        contextRotationPending: false,
        contextIdleRotationDueAt: null,
        lastContextRotatedAt: null,
        inputTokensSinceContextReset: null,
        lastHandoffSummary: null,
      };
    }
    const activeDispatchId = text(runtime.active_dispatch_id);
    const wakeReason = text(runtime.wake_reason);
    const nextWakeAt = text(runtime.next_wake_at);
    const wakeLeasedUntil = text(runtime.wake_leased_until);
    const lastWakeAt = text(runtime.last_wake_at);
    const lowPriorityBatchStartedAt = text(runtime.low_priority_batch_started_at);
    const lowPriorityBatchCycleCount = numberOrZero(runtime.low_priority_batch_cycle_count);
    const lowPriorityBatchDueAfterCycles = numberOrNull(runtime.low_priority_batch_due_after_cycles);
    const contextRotationPending = Boolean(runtime.context_rotation_pending);
    const contextClearingPolicy = peers.selects.contextClearingPolicyValue(runtime);
    const contextClearingIdleGraceSeconds = numberOrZero(
      runtime.context_clearing_idle_grace_seconds,
    );
    const contextIdleRotationDueAt = text(runtime.context_idle_rotation_due_at);
    const autoContextClearingEnabled = contextClearingPolicy !== "off";
    const lastContextRotatedAt = text(runtime.last_context_rotated_at);
    const inputTokensSinceContextReset = numberOrNull(
      runtime.context_reset_usage?.input_tokens_since_reset,
    );
    const parts = [];
    if (activeDispatchId) {
      parts.push(`dispatch ${activeDispatchId}`);
    }
    if (wakeReason) {
      parts.push(`wake ${wakeReason}`);
    }
    if (nextWakeAt) {
      parts.push(`next wake ${nextWakeAt}`);
    }
    if (wakeLeasedUntil) {
      parts.push(`leased until ${wakeLeasedUntil}`);
    }
    if (lastWakeAt) {
      parts.push(`last wake ${lastWakeAt}`);
    }
    if (lowPriorityBatchStartedAt && lowPriorityBatchDueAfterCycles !== null) {
      parts.push(`low-priority batch ${lowPriorityBatchCycleCount}/${lowPriorityBatchDueAfterCycles}`);
    }
    if (contextRotationPending) {
      parts.push("context clearing pending");
    } else if (contextIdleRotationDueAt) {
      parts.push(`idle context grace until ${contextIdleRotationDueAt}`);
    }
    if (lastContextRotatedAt && !["every_wake", "work_lineage"].includes(contextClearingPolicy)) {
      parts.push(`last context reset ${lastContextRotatedAt}`);
    }
    return {
      present: true,
      status: activeDispatchId ? "busy" : wakeReason ? `wake ${wakeReason}` : "idle",
      summary: parts.join(" - "),
      activeDispatchId: activeDispatchId || null,
      wakeReason: wakeReason || null,
      nextWakeAt: nextWakeAt || null,
      wakeLeasedUntil: wakeLeasedUntil || null,
      lastWakeAt: lastWakeAt || null,
      lowPriorityBatchStartedAt: lowPriorityBatchStartedAt || null,
      lowPriorityBatchCycleCount,
      lowPriorityBatchDueAfterCycles,
      contextClearingPolicy,
      contextClearingIdleGraceSeconds,
      autoContextClearingEnabled,
      contextRotationPending,
      contextIdleRotationDueAt: contextIdleRotationDueAt || null,
      lastContextRotatedAt: lastContextRotatedAt || null,
      inputTokensSinceContextReset,
      lastHandoffSummary: text(runtime.last_handoff_summary) || null,
    };
  }

  function runtimeOperationForAgent(agentId) {
    return (peers.app.state.runtime_operations?.agents || []).find((row) => {
      return text(row?.id || row?.agent_id) === text(agentId);
    }) || null;
  }

  function agentSessionHealth(agentId) {
    const normalizedAgentId = text(agentId);
    const health = (peers.app.state.runtime_operations?.session_health || []).find((row) => {
      return text(row?.agent_id || row?.id) === normalizedAgentId;
    });
    if (health) {
      return normalizeAgentSessionHealth(health);
    }
    const runtimeRow = runtimeOperationForAgent(normalizedAgentId);
    if (runtimeRow && runtimeRow.session_is_expired !== undefined) {
      return normalizeAgentSessionHealth(runtimeRow);
    }
    return {
      available: false,
      present: null,
      isExpired: false,
      expiresAt: "",
      status: "",
    };
  }

  function normalizeAgentSessionHealth(row) {
    const status = text(row?.status || row?.session_status);
    return {
      available: true,
      present: row?.session_present === undefined ? null : Boolean(row.session_present),
      isExpired: Boolean(row?.is_expired || row?.session_is_expired || status === "expired"),
      expiresAt: text(row?.expires_at || row?.session_expires_at),
      vmProvider: text(row?.vm_provider || row?.vmProvider),
      executionBackend: text(row?.execution_backend || row?.executionBackend),
      containerLifecycleState: text(row?.container_lifecycle_state || row?.containerLifecycleState),
      agentExecutionBackend: text(row?.agent_execution_backend || row?.agentExecutionBackend),
      agentExecutionStatus: text(row?.agent_execution_status || row?.agentExecutionStatus),
      activeExecutionCount: row?.agent_active_execution_count ?? row?.agentActiveExecutionCount ?? null,
      latestExecutionId: text(row?.agent_latest_execution_id || row?.agentLatestExecutionId),
      status,
    };
  }

  function sessionHealthRuntimeLine(health, sessionId) {
    if (!health?.available) {
      return "";
    }
    const sessionLabel = text(sessionId) || "session";
    if (health.isExpired) {
      return `Session ${sessionLabel} expired${health.expiresAt ? ` at ${health.expiresAt}` : ""}`;
    }
    if (health.present === false) {
      return `Session ${sessionLabel} not found in the session store`;
    }
    if (health.expiresAt) {
      return `Session ${sessionLabel} expires ${health.expiresAt}`;
    }
    return "";
  }

  function renderAgentSessionPill(agentData) {
    const health = agentData.sessionHealth || {};
    if (!health.available || !health.isExpired) {
      return "";
    }
    const detail = sessionHealthRuntimeLine(health, agentData.sessionId) || "Session expired";
    return `<span class="state-pill critical" title="${escapeAttr(detail)}">session expired</span>`;
  }

  function agentAwakeState(agentData) {
    if (agentData.sessionHealth?.isExpired) {
      return {
        kind: "expired",
        label: "Expired",
        detail: sessionHealthRuntimeLine(agentData.sessionHealth, agentData.sessionId) || "session expired",
      };
    }
    const executionState = agentExecutionAwakeState(agentData);
    const runtimeRow = runtimeOperationForAgent(agentData.id);
    const activeDispatchId = text(runtimeRow?.active_dispatch_id || agentData.runtime?.activeDispatchId);
    if (executionState.known && !executionState.active && activeDispatchId) {
      return {
        kind: "idle",
        label: "Idle",
        detail: `${executionState.detail} - stale dispatch ${activeDispatchId}`,
      };
    }
    if (activeDispatchId) {
      return {
        kind: "awake",
        label: "Awake",
        detail: `dispatch ${activeDispatchId}`,
      };
    }
    if (executionState.active) {
      return {
        kind: "awake",
        label: "Awake",
        detail: executionState.detail,
      };
    }
    if (Boolean(runtimeRow?.lease_active) || isFutureTimestamp(agentData.runtime?.wakeLeasedUntil)) {
      if (executionState.known && !executionState.active) {
        return {
          kind: "idle",
          label: "Idle",
          detail: `${executionState.detail} - stale lease`,
        };
      }
      return {
        kind: "awake",
        label: "Awake",
        detail: "lease active",
      };
    }
    if (Boolean(runtimeRow?.wake_due)) {
      return {
        kind: "due",
        label: "Due",
        detail: "wake due",
      };
    }
    if (agentData.runtime?.present) {
      return {
        kind: "idle",
        label: "Idle",
        detail: "runtime idle",
      };
    }
    return {
      kind: "unknown",
      label: "No runtime",
      detail: "no runtime snapshot",
    };
  }

  function agentExecutionAwakeState(agentData) {
    const health = agentData?.sessionHealth || {};
    const activeExecutionCount = Number(health.activeExecutionCount);
    if (Number.isFinite(activeExecutionCount) && activeExecutionCount > 0) {
      return {
        known: true,
        active: true,
        detail: `${activeExecutionCount} active execution${activeExecutionCount === 1 ? "" : "s"}`,
      };
    }
    const executionStatus = text(health.agentExecutionStatus).toLowerCase();
    if (!executionStatus) {
      return { known: false, active: false, detail: "" };
    }
    if (["preparing", "starting", "running"].includes(executionStatus)) {
      return {
        known: true,
        active: true,
        detail: `execution ${executionStatus}`,
      };
    }
    return {
      known: true,
      active: false,
      detail: `execution ${executionStatus}`,
    };
  }

  function renderAgentAwakePill(agentData) {
    const awake = agentAwakeState(agentData);
    return `
      <span class="awake-indicator awake-indicator--${escapeAttr(awake.kind)}" title="${escapeAttr(awake.detail)}">
        <span class="awake-dot" aria-hidden="true"></span>
        ${escapeHtml(awake.label)}
      </span>
    `;
  }

  function renderAgentInboxHeaderIndicators(agentData, { interactive = true } = {}) {
    const indicatorItems = agentData.inboxIndicatorItems || {};
    return `
      <span class="agent-inbox-indicators" aria-label="Pending inbox summary">
        ${renderAgentInboxHeaderIndicator({
          itemType: "status_request",
          direction: "down",
          label: "Status requests",
          items: indicatorItems.status_request || [],
          interactive,
        })}
        ${renderAgentInboxHeaderIndicator({
          itemType: "progress_report",
          direction: "up",
          label: "Progress reports",
          items: indicatorItems.progress_report || [],
          interactive,
        })}
      </span>
    `;
  }

  function renderAgentInboxHeaderIndicator({ itemType, direction, label, items, interactive = true }) {
    const rows = Array.isArray(items) ? items : [];
    const count = rows.length;
    const stateClass = count ? "agent-inbox-indicator--active" : "agent-inbox-indicator--empty";
    return `
      <span
        class="agent-inbox-indicator agent-inbox-indicator--${escapeAttr(itemType)} ${stateClass}"
        ${interactive ? `tabindex="0"` : ""}
        aria-label="${escapeAttr(`${label}: ${count}`)}"
      >
        ${renderAgentInboxIndicatorIcon(direction)}
        <strong>${count}</strong>
        ${count ? renderAgentInboxIndicatorPopover(label, rows) : ""}
      </span>
    `;
  }

  function renderAgentInboxIndicatorIcon(direction) {
    const arrow =
      direction === "up"
        ? `
          <path d="M10 8V3"></path>
          <path d="M7.3 5.7 10 3l2.7 2.7"></path>
        `
        : `
          <path d="M10 3v5"></path>
          <path d="M7.3 5.3 10 8l2.7-2.7"></path>
        `;
    return `
      <svg class="agent-inbox-icon agent-inbox-icon--${escapeAttr(direction)}" viewBox="0 0 20 20" aria-hidden="true" focusable="false">
        ${arrow}
        <path d="M5.5 8.2h9a2.2 2.2 0 0 1 2.2 2.2v2.6a2.2 2.2 0 0 1-2.2 2.2H9.3L5 18v-2.8a2.2 2.2 0 0 1-1.7-2.1v-2.7a2.2 2.2 0 0 1 2.2-2.2Z"></path>
      </svg>
    `;
  }

  function renderAgentInboxIndicatorPopover(label, items) {
    return `
      <span class="agent-inbox-popover" role="tooltip" aria-label="${escapeAttr(label)}">
        ${items.map((item) => renderAgentInboxBubble(item)).join("")}
      </span>
    `;
  }

  function renderAgentInboxBubble(item) {
    const detail = inboxItemDetail(item) || item.id;
    const metaParts = [];
    if (item.task_id) {
      metaParts.push(`task ${item.task_id}`);
    }
    if (item.sender_agent_id) {
      metaParts.push(`from ${item.sender_agent_id}`);
    }
    metaParts.push(inboxPriorityLabel(item));
    metaParts.push(inboxReplyIntentLabel(item));
    return `
      <span class="agent-inbox-bubble">
        <strong>${escapeHtml(detail)}</strong>
        ${metaParts.length ? `<small>${escapeHtml(metaParts.join(" - "))}</small>` : ""}
      </span>
    `;
  }

  function agentWorkSnapshot(agentData) {
    const activitySummary = agentData.activitySummary || null;
    const openTasks = agentData.taskStats.openTasks || [];
    const waitingTasks = openTasks.filter(isTaskWaitingOrBlocked);
    const currentTask = openTasks.find((task) => !isTaskWaitingOrBlocked(task)) || openTasks[0] || null;
    const inboxItems = pendingInboxForAgent(agentData.id);
    const runtimeAttention = agentRuntimeAttentionRows(agentData.id);
    const activeAssignments = activeEphemeralAssignmentsForAgent(agentData.id);
    const primary = text(activitySummary?.headline) || text(activitySummary?.working_on) || (currentTask
      ? `${currentTask.title} (${taskStateLabel(currentTask)})`
      : inboxItems[0]
        ? `${inboxItemLabels[inboxItems[0].item_type] || inboxItems[0].item_type}: ${inboxItemDetail(inboxItems[0]) || inboxItems[0].id}`
        : activeAssignments[0]
          ? `${activeAssignments[0].title || activeAssignments[0].id} (${humanizeToken(activeAssignments[0].state)})`
          : "No open work");
    const waiting = [];
    const summaryWaiting = text(activitySummary?.waiting_for);
    if (summaryWaiting && summaryWaiting.toLowerCase() !== "clear") {
      waiting.push(summaryWaiting);
    }
    waitingTasks.slice(0, 2).forEach((task) => {
      const label = `${task.title} (${taskStateLabel(task)})`;
      if (!waiting.includes(label)) {
        waiting.push(label);
      }
    });
    runtimeAttention.slice(0, 2).forEach((row) => {
      const label = row.title || row.id;
      if (label && !waiting.includes(label)) {
        waiting.push(`${label} (${humanizeToken(row.kind)})`);
      }
    });
    return {
      primary,
      waiting,
      inboxItems,
      runtimeAttention,
      activeAssignments,
    };
  }

  function isTaskWaitingOrBlocked(task) {
    return (
      task.state === "blocked" ||
      task.state === "waiting_for_input" ||
      task.state === "waiting_for_review" ||
      task.dependency_status === "waiting" ||
      task.dependency_status === "blocked" ||
      Boolean((task.blocked_by_task_ids || []).length)
    );
  }

  function agentRuntimeAttentionRows(agentId) {
    return (peers.app.state.runtime_operations?.attention || []).filter((row) => {
      return (
        text(row.agent_id) === text(agentId) &&
        peers.operations.runtimeAttentionGroup(row) === "active_obligation"
      );
    });
  }

  function renderOrgNodeWorkSummary(agentData) {
    const summary = orgNodeWorkSummaryData(agentData);
    return `
      <div class="org-node-work">
        <div class="org-node-work-line org-node-work-line--now">
          <span>Now</span>
          <strong>${escapeHtml(summary.primary)}</strong>
        </div>
        <div class="org-node-work-line org-node-work-line--waiting">
          <span>Waiting</span>
          <strong>${escapeHtml(summary.waiting)}</strong>
        </div>
      </div>
    `;
  }

  function orgNodeWorkSummaryData(agentData) {
    const snapshot = agentWorkSnapshot(agentData);
    return {
      primary: snapshot.primary,
      waiting: snapshot.waiting.length
        ? snapshot.waiting[0]
        : snapshot.inboxItems.length
          ? `${snapshot.inboxItems.length} pending inbox`
          : "Clear",
    };
  }

  function hierarchyDetailLines(agentData, { focused = false } = {}) {
    const lines = [];
    const awake = agentAwakeState(agentData);
    const snapshot = agentWorkSnapshot(agentData);

    if (focused) {
      if (agentData.activitySummary?.source) {
        lines.push(`Activity summary ${agentData.activitySummary.source} - ${agentData.activitySummary.tone || "unknown"}`);
      }
      lines.push(`Awake status ${awake.label} - ${awake.detail}`);
      lines.push(`Current work ${snapshot.primary}`);
      if (snapshot.waiting.length) {
        lines.push(`Waiting on ${snapshot.waiting.join("; ")}`);
      }
      if (snapshot.inboxItems.length) {
        lines.push(
          `Inbox ${snapshot.inboxItems
            .slice(0, 2)
            .map((item) => `${inboxItemLabels[item.item_type] || item.item_type}: ${inboxPriorityLabel(item)}: ${inboxReplyIntentLabel(item)}: ${inboxItemDetail(item) || item.id}`)
            .join("; ")}`,
        );
      }
    }

    if (agentData.id) {
      lines.push(`Agent ${agentData.id}`);
    }
    if (agentData.sessionId) {
      lines.push(
        sessionHealthRuntimeLine(agentData.sessionHealth, agentData.sessionId) ||
          `Session ${agentData.sessionId}`,
      );
    }
    if (agentData.projectIds.length) {
      lines.push(`Projects ${agentData.projectLabel || agentData.projectIds.join(", ")}`);
    }
    lines.push(`Reports to ${agentData.reportsToLabel || "operator"}`);
    lines.push(`Direct subordinates ${agentData.directSubordinateCount}`);
    lines.push(`Open tasks ${agentData.taskStats.openCount}`);
    lines.push(`Blocked/waiting ${agentData.taskStats.blockedWaitingCount}`);
    lines.push(`Pending inbox ${agentData.inboxCount}`);
    if (agentData.runtime?.present) {
      const runtimeParts = [`Runtime ${agentData.runtime.status}`];
      if (agentData.runtime.summary) {
        runtimeParts.push(agentData.runtime.summary);
      }
      lines.push(runtimeParts.join(" - "));
    }
    lines.push(
      `Context clearing ${peers.selects.contextClearingPolicyLabel(agentData.runtime?.contextClearingPolicy || "off")}${
        agentData.runtime?.contextRotationPending ? " - pending" : ""
      }`,
    );
    lines.push(`Ephemeral ${agentData.ephemeralCurrentCount} / ${agentData.ephemeralCapacity}`);

    return lines;
  }

  function renderHierarchyDetails(agentData, { focused = false } = {}) {
    return hierarchyDetailLines(agentData, { focused })
      .map((line) => `<div class="meta-line">${escapeHtml(line)}</div>`)
      .join("");
  }

  function activeEphemeralAssignmentsForAgent(agentId) {
    return values(peers.app.state.ephemeral_assignments).filter((assignment) => {
      return (
        assignment?.parent_agent_id === agentId &&
        ["requested", "spawn_pending", "running"].includes(text(assignment?.state))
      );
    });
  }

  function renderAgentInbox(agentId) {
    const items = pendingInboxForAgent(agentId);
    if (!items.length) {
      return "";
    }
    return `
      <div class="meta-line">Inbox:</div>
      <ul class="inline-list">
        ${items
          .map((item) => {
            const label = inboxItemLabels[item.item_type] || item.item_type;
            const detail = inboxItemDetail(item);
            const sender = item.sender_agent_id ? ` from ${escapeHtml(item.sender_agent_id)}` : "";
            const replyIntent = inboxReplyIntentLabel(item);
            return `
              <li>
                <span class="state-pill ${escapeHtml(item.item_type)}">${escapeHtml(label)}</span>
                <span class="state-pill neutral">${escapeHtml(inboxPriorityLabel(item))}</span>
                <span class="state-pill ${item.response_requested === false ? "neutral" : "info"}">${escapeHtml(replyIntent)}</span>
                ${detail ? `<span> ${escapeHtml(detail)}</span>` : ""}${sender}
              </li>
            `;
          })
          .join("")}
      </ul>
    `;
  }

  function inboxItemDetail(item) {
    const payload = item.payload || {};
    return text(payload.question || payload.message || payload.title || payload.summary || "");
  }

  function inboxReplyIntentLabel(item) {
    return item.response_requested === false ? "no reply needed" : "reply requested";
  }

  function inboxPriorityLabel(item) {
    const priority = text(item.priority) || "normal";
    return `${humanizeToken(priority)} priority`;
  }

  function agentLabel(agent) {
    return text(agent?.display_name) || text(agent?.name) || text(agent?.label) || text(agent?.id);
  }

  function avatarUrl(agentOrDetails) {
    const avatar = agentOrDetails?.avatar;
    const assetId = text(avatar?.asset_id);
    if (!assetId) {
      return "";
    }
    return text(avatar.url) || `${API_BASE}/avatars/${encodeURIComponent(assetId)}`;
  }

  function agentInitials(agentOrDetails) {
    const label = agentLabel(agentOrDetails);
    const parts = label.split(/[\s._-]+/).filter(Boolean);
    const letters = parts.length > 1
      ? `${parts[0][0] || ""}${parts[1][0] || ""}`
      : label.slice(0, 2);
    return (letters || "A").toUpperCase();
  }

  function renderAgentAvatar(agentOrDetails, extraClass = "") {
    const src = avatarUrl(agentOrDetails);
    const label = agentLabel(agentOrDetails);
    const className = `agent-avatar ${extraClass}`.trim();
    if (src) {
      return `<span class="${escapeAttr(className)}"><img src="${escapeAttr(src)}" alt="${escapeAttr(`${label} avatar`)}" loading="lazy" /></span>`;
    }
    return `<span class="${escapeAttr(`${className} agent-avatar-fallback`)}" aria-hidden="true">${escapeHtml(agentInitials(agentOrDetails))}</span>`;
  }

  function roleLabel(roleOrId) {
    const roleId = text(roleOrId?.id ?? roleOrId);
    const role =
      roleOrId && typeof roleOrId === "object"
        ? roleOrId
        : peers.app.state.roles[roleId];
    if (!role) {
      return roleId;
    }
    return text(role.name) || text(role.id) || roleId;
  }

  function agentRoleLabel(agent) {
    const roleId = text(agent?.role_id);
    return text(agent?.role_name) || (roleId ? roleLabel(roleId) : "No role");
  }
  function handleOpenSessionClick(event) {
    const openSessionButton = event.target.closest("[data-open-session-id]");
    if (openSessionButton) {
      event.preventDefault();
      navigateToAgentSession(openSessionButton.dataset.openSessionId);
      return true;
    }
    return false;
  }

  return {
    activityToneClass,
    agentAwakeState,
    agentCardData,
    agentInitials,
    agentLabel,
    agentLeadProjectIds,
    agentProjectIds,
    agentProjectSummary,
    agentRoleLabel,
    agentRuntimeAttentionRows,
    agentRuntimeDetailRows,
    agentStandDownSummary,
    agentStatus,
    agentWorkSnapshot,
    avatarUrl,
    bindInboxPopoverPositioning,
    ephemeralCapacity,
    handleOpenSessionClick,
    hierarchyDetailLines,
    inboxItemDetail,
    inboxPriorityLabel,
    inboxReplyIntentLabel,
    isTaskWaitingOrBlocked,
    navigateToAgentSession,
    normalizeAgentSessionHealth,
    orgNodeWorkSummaryData,
    pendingInboxForAgent,
    pendingInboxItemIsCurrent,
    projectLabel,
    renderAgentAvatar,
    renderAgentAwakePill,
    renderAgentInbox,
    renderAgentInboxHeaderIndicators,
    renderAgentOpenTasks,
    renderAgentRosterStatusPill,
    renderAgentSessionPill,
    renderAgentStandDownControls,
    renderHierarchyDetails,
    renderOrgNodeWorkSummary,
    roleLabel,
    sessionHealthRuntimeLine,
  };
}

// Planning hierarchy, readiness, task briefs, and contribution presentation.
import { defaultPlanningTaskStates, taskAssessmentResolutionReasonMessages, taskStates, terminalTaskStates } from "./team-ui-config.js";
import {
  byPriority,
  escapeAttr,
  escapeHtml,
  humanizeToken,
  taskStateLabel,
  text,
  uniqueTextList,
  values,
} from "./team-ui-format.js";
import { isOpaqueIdentifier, renderIdentifierText } from "./team-identifiers.js";

export function createTeamPlanningController(peers) {

  let selectedPlanningTaskStates = new Set(defaultPlanningTaskStates);

  function bindPlanningFilters() {
    document.querySelectorAll("[data-planning-task-status]").forEach((input) => {
      input.addEventListener("change", () => {
        const status = input.dataset.planningTaskStatus;
        if (!taskStates.includes(status)) {
          return;
        }
        if (input.checked) {
          selectedPlanningTaskStates.add(status);
        } else {
          selectedPlanningTaskStates.delete(status);
        }
        peers.projects.renderProjects();
      });
    });
    updatePlanningFilterControls();
  }

  function updatePlanningFilterControls() {
    document.querySelectorAll("[data-planning-task-status]").forEach((input) => {
      const status = input.dataset.planningTaskStatus;
      input.checked = selectedPlanningTaskStates.has(status);
    });
  }

  function planningTasksForProject(projectTasks) {
    return projectTasks
      .filter((task) => selectedPlanningTaskStates.has(task.state))
      .sort(comparePlanningTasks);
  }

  function comparePlanningTasks(left, right) {
    return (
      planningTaskStatusRank(left.state) - planningTaskStatusRank(right.state) ||
      byPriority(left, right)
    );
  }

  function planningTaskStatusRank(status) {
    const order = {
      blocked: 0,
      waiting_for_input: 1,
      waiting_for_review: 2,
      in_progress: 3,
      pending: 4,
      finished: 5,
      accepted: 6,
      rejected: 7,
      cancelled: 8,
    };
    return order[status] ?? 99;
  }

  function comparePlanningProjectCards(left, right) {
    const leftBlocked = left.visibleTasks.filter(peers.agents.isTaskWaitingOrBlocked).length;
    const rightBlocked = right.visibleTasks.filter(peers.agents.isTaskWaitingOrBlocked).length;
    return (
      rightBlocked - leftBlocked ||
      right.visibleTasks.length - left.visibleTasks.length ||
      peers.delivery.projectSortKey(left.project).localeCompare(peers.delivery.projectSortKey(right.project))
    );
  }

  function renderPlanningProjectBlock(project, projectTasks, visibleTasks, projectDeliverables) {
    const openTasks = visibleTasks.filter((task) => !terminalTaskStates.has(task.state));
    const blockedTasks = visibleTasks.filter((task) => task.state === "blocked");
    const waitingForInputTasks = visibleTasks.filter(
      (task) => task.state === "waiting_for_input",
    );
    const waitingTasks = visibleTasks.filter((task) => task.state === "waiting_for_review");
    const goals = project.goals || [];
    const hiddenCount = projectTasks.length - visibleTasks.length;
    const detailBits = [
      `${visibleTasks.length} shown`,
      `${openTasks.length} open`,
      `${projectDeliverables.length} deliverable${projectDeliverables.length === 1 ? "" : "s"}`,
    ];
    if (blockedTasks.length) {
      detailBits.push(`${blockedTasks.length} blocked`);
    }
    if (waitingForInputTasks.length) {
      detailBits.push(`${waitingForInputTasks.length} waiting input`);
    }
    if (waitingTasks.length) {
      const acceptanceCount = waitingTasks.filter(
        (task) => task.waiting_reason === "superior_acceptance",
      ).length;
      const reviewCount = waitingTasks.length - acceptanceCount;
      if (acceptanceCount) {
        detailBits.push(`${acceptanceCount} waiting acceptance`);
      }
      if (reviewCount) {
        detailBits.push(`${reviewCount} waiting review`);
      }
    }
    if (hiddenCount > 0) {
      detailBits.push(`${hiddenCount} hidden`);
    }
    return `
      <article class="project-block planning-project-card" data-planning-project-id="${escapeAttr(project.id)}">
        <details ${planningDisclosureAttributes("project", project.id)}>
          <summary class="planning-project-summary">
            <span class="planning-project-main">
              <strong>${escapeHtml(project.name)}</strong>
              <span>${escapeHtml(project.id)} - lead ${escapeHtml(project.lead_agent_id)}</span>
            </span>
            <span class="planning-project-meta">
              <span class="state-pill ${escapeHtml(project.status)}">${escapeHtml(project.status)}</span>
              <span>${escapeHtml(detailBits.join(" - "))}</span>
            </span>
          </summary>
          <div class="planning-project-body">
            <div class="project-header">
              <div>
                <strong>${escapeHtml(project.name)}</strong>
                <div class="meta-line">${escapeHtml(project.id)} - lead ${escapeHtml(project.lead_agent_id)}</div>
                ${peers.costs.renderProjectCostCounter(project.id)}
                ${peers.projects.renderProjectGit(project.git)}
                ${peers.projects.renderProjectReleasePolicy(project.release_policy)}
              </div>
              <div class="record-actions">
                <span class="state-pill ${escapeHtml(project.status)}">${escapeHtml(project.status)}</span>
                ${peers.access.editButton("project", project.id)}
              </div>
            </div>
            <div class="project-columns planning-project-columns">
              <div>
                <p class="subheading">Goals</p>
                ${peers.org.renderGoals(project.id, goals, { compact: true })}
                <p class="subheading">Deliverables</p>
                ${peers.reviews.renderDeliverables(projectDeliverables, { compact: true })}
              </div>
              <div>
                <p class="subheading">Tasks</p>
                ${renderTasks(visibleTasks, { compact: true, structuredPlanning: true })}
              </div>
            </div>
          </div>
        </details>
      </article>
    `;
  }

  function planningDisclosureKey(kind, recordId) {
    return peers.disclosures.teamDisclosureKey("planning", kind, recordId);
  }

  function planningDisclosureAttributes(kind, recordId) {
    return peers.disclosures.teamDisclosureAttributes(planningDisclosureKey(kind, recordId));
  }

  function renderTasks(tasks, { compact = false, structuredPlanning = false } = {}) {
    if (!tasks.length) {
      return `<p class="empty-state">No tasks.</p>`;
    }
    const rows = taskHierarchyRows(tasks);
    return `
      <ul class="task-list${compact ? " compact-record-list" : ""}">
        ${rows.map((row) => renderTaskHierarchyRow(row, { compact, structuredPlanning })).join("")}
      </ul>
    `;
  }

  function taskHierarchyRows(tasks) {
    const byTaskId = new Map(tasks.map((task) => [text(task.id), task]));
    const children = new Map();
    tasks.forEach((task) => {
      const parentId = text(task.parent_task_id);
      if (!parentId || !byTaskId.has(parentId) || parentId === text(task.id)) {
        return;
      }
      const rows = children.get(parentId) || [];
      rows.push(task);
      children.set(parentId, rows);
    });
    children.forEach((rows) => rows.sort(byPriority));
    const roots = tasks
      .filter((task) => {
        const parentId = text(task.parent_task_id);
        return !parentId || !byTaskId.has(parentId) || parentId === text(task.id);
      })
      .sort(byPriority);
    const rows = [];
    const visited = new Set();
    const visit = (task, depth, path) => {
      const taskId = text(task.id);
      if (visited.has(taskId)) {
        return;
      }
      visited.add(taskId);
      const cycle = path.has(taskId);
      rows.push({ task, depth, hierarchyWarning: cycle ? "cycle" : "" });
      if (cycle) {
        return;
      }
      const nextPath = new Set(path);
      nextPath.add(taskId);
      (children.get(taskId) || []).forEach((child) => visit(child, depth + 1, nextPath));
    };
    roots.forEach((task) => visit(task, 0, new Set()));
    tasks
      .filter((task) => !visited.has(text(task.id)))
      .sort(byPriority)
      .forEach((task) => visit(task, 0, new Set([text(task.id)])));
    return rows;
  }

  function renderTaskHierarchyRow(
    { task, depth, hierarchyWarning },
    { compact, structuredPlanning = false },
  ) {
    if (structuredPlanning) {
      return peers.tasks.renderStructuredPlanningTask(task, {
        depth,
        hierarchyWarning,
      });
    }
    const invalid = Boolean(hierarchyWarning || (task.invalid_reason_codes || []).length);
    const classes = [
      "task-item",
      compact ? "compact-record-card" : "",
      depth ? "task-hierarchy-child" : "task-hierarchy-root",
      invalid ? "task-hierarchy-invalid" : "",
    ].filter(Boolean).join(" ");
    const body = `
      <div class="record-actions">
        ${peers.access.editButton("task", task.id)}
        ${peers.access.taskCancellationButton(task)}
      </div>
      <div class="meta-line">${escapeHtml(task.id)} - priority ${Number(task.priority ?? 0)} - owner ${escapeHtml(peers.board.taskAssignmentLabel(task))}</div>
      ${peers.tasks.renderTaskGraph(task)}
      <div class="meta-line">${renderIdentifierText(task.description)}</div>
    `;
    if (compact) {
      return `
        <li class="${classes}" style="--task-depth: ${Math.max(0, Number(depth) || 0)}">
          <details ${peers.disclosures.teamDisclosureAttributes(peers.disclosures.teamDisclosureKey("task", task.id, "detail"))}>
            <summary class="compact-record-summary">
              <span class="compact-record-main">
                <strong>${escapeHtml(task.title)}</strong>
                <span>${escapeHtml(task.id)} - owner ${escapeHtml(peers.board.taskAssignmentLabel(task))} - depth ${depth}</span>
              </span>
              <span class="state-pill ${escapeHtml(task.state)}">${escapeHtml(taskStateLabel(task))}</span>
            </summary>
            <div class="compact-record-body">${body}</div>
          </details>
        </li>
      `;
    }
    return `
      <li class="${classes}" style="--task-depth: ${Math.max(0, Number(depth) || 0)}">
        <div class="task-line">
          <strong>${escapeHtml(task.title)}</strong>
          <div class="record-actions">
            <span class="state-pill ${escapeHtml(task.state)}">${escapeHtml(taskStateLabel(task))}</span>
            ${peers.access.editButton("task", task.id)}
            ${peers.access.taskCancellationButton(task)}
          </div>
        </div>
        <div class="meta-line">${escapeHtml(task.id)} - priority ${Number(task.priority ?? 0)} - owner ${escapeHtml(peers.board.taskAssignmentLabel(task))}</div>
        ${peers.tasks.renderTaskGraph(task)}
        <div class="meta-line">${renderIdentifierText(task.description)}</div>
      </li>
    `;
  }

  function taskRelativeAge(timestamp) {
    const parsed = Date.parse(text(timestamp));
    if (!Number.isFinite(parsed)) {
      return "age unavailable";
    }
    const seconds = Math.max(0, Math.floor((Date.now() - parsed) / 1000));
    if (seconds < 60) {
      return "just now";
    }
    if (seconds < 3600) {
      const minutes = Math.floor(seconds / 60);
      return `${minutes} minute${minutes === 1 ? "" : "s"}`;
    }
    if (seconds < 86400) {
      const hours = Math.floor(seconds / 3600);
      return `${hours} hour${hours === 1 ? "" : "s"}`;
    }
    const days = Math.floor(seconds / 86400);
    return `${days} day${days === 1 ? "" : "s"}`;
  }

  function taskAssessmentReasonCodePhrase(value) {
    const words = text(value)
      .replace(/[_-]+/g, " ")
      .replace(/\s+/g, " ")
      .toLowerCase();
    return words ? `${words[0].toUpperCase()}${words.slice(1)}` : "";
  }

  function taskAssessmentResolutionReasonLabel(value) {
    const reason = text(value);
    if (!reason) {
      return "";
    }
    if (taskAssessmentResolutionReasonMessages[reason]) {
      return taskAssessmentResolutionReasonMessages[reason];
    }
    if (reason.startsWith("invalid:")) {
      const invalidReasons = reason
        .slice("invalid:".length)
        .split(",")
        .map(taskAssessmentReasonCodePhrase)
        .filter(Boolean);
      return invalidReasons.length
        ? `Lifecycle data needs repair: ${invalidReasons.join("; ")}.`
        : "Lifecycle data needs repair.";
    }
    if (/^[a-z0-9]+(?:[_-][a-z0-9]+)*$/.test(reason)) {
      const fallback = taskAssessmentReasonCodePhrase(reason);
      return fallback ? `${fallback}.` : "";
    }
    return reason;
  }

  function planningTaskLifecycleReason(task) {
    const assessment = task.assessment || {};
    if (assessment.resolution_reason) {
      return taskAssessmentResolutionReasonLabel(assessment.resolution_reason);
    }
    if (task.waiting_reason) {
      return `Waiting for ${humanizeToken(task.waiting_reason)}.`;
    }
    const blocker = peers.recovery.activeBlockersForRecord("task", task.id, task.id)[0];
    if (blocker?.summary) {
      return blocker.summary;
    }
    if (task.dependency_status === "waiting") {
      return "Waiting for prerequisite work.";
    }
    if (task.dependency_status === "blocked") {
      return "A prerequisite is blocked.";
    }
    return terminalTaskStates.has(text(task.state))
      ? `Task is ${taskStateLabel(task).toLowerCase()}.`
      : "Task is ready for its next lifecycle step.";
  }

  function planningTaskLifecycleStages(task) {
    const assessment = task.assessment || {};
    const dependencyStatus = text(task.dependency_status || "satisfied");
    const stateValue = text(task.state);
    const resolution = text(task.resolution_status || assessment.resolution_status || "unresolved");
    const executionComplete = ["finished", "waiting_for_review", "accepted", "rejected"].includes(stateValue);
    const reviewComplete = ["accepted", "rejected"].includes(stateValue);
    const reviewNotRequired = ["support_task_finished", "coordination_task_finished"].includes(
      text(assessment.resolution_reason),
    );
    return [
      {
        label: "Created",
        state: "complete",
        detail: task.created_at ? peers.app.formatTimelineTimestamp(task.created_at) : "Recorded",
      },
      {
        label: "Dependencies",
        state: dependencyStatus === "satisfied" ? "complete" : dependencyStatus === "blocked" ? "blocked" : "current",
        detail: humanizeToken(dependencyStatus),
      },
      {
        label: "Execution",
        state: stateValue === "cancelled" ? "cancelled" : executionComplete ? "complete" : stateValue === "blocked" ? "blocked" : ["in_progress"].includes(stateValue) ? "current" : "pending",
        detail: taskStateLabel(task),
      },
      {
        label: "Review / acceptance",
        state: stateValue === "cancelled" ? "skipped" : reviewNotRequired ? "skipped" : reviewComplete ? (stateValue === "rejected" ? "blocked" : "complete") : stateValue === "waiting_for_review" || stateValue === "finished" ? "current" : "pending",
        detail: stateValue === "cancelled" ? "Skipped — task cancelled" : reviewNotRequired ? "Not required — support task finished" : stateValue === "waiting_for_review" ? humanizeToken(task.waiting_reason || "awaiting review") : reviewComplete ? taskStateLabel(task) : "Not reached",
      },
      {
        label: "Resolved",
        state: resolution === "resolved" ? "complete" : resolution === "invalid" ? "blocked" : "pending",
        detail: humanizeToken(resolution),
      },
    ];
  }

  function renderPlanningTaskLifecycle(task) {
    return `
      <ol class="planning-task-lifecycle" aria-label="Task lifecycle">
        ${planningTaskLifecycleStages(task).map((stage, index) => `
          <li class="planning-lifecycle-stage planning-lifecycle-${escapeAttr(stage.state)}">
            <span class="planning-lifecycle-marker" aria-hidden="true">${stage.state === "complete" ? "✓" : stage.state === "cancelled" ? "×" : stage.state === "skipped" ? "–" : index + 1}</span>
            <span><strong>${escapeHtml(stage.label)}</strong><small>${escapeHtml(stage.detail)}</small></span>
          </li>
        `).join("")}
      </ol>
    `;
  }

  function planningTaskChildren(taskId) {
    return values(peers.app.state.tasks)
      .filter((candidate) => text(candidate.parent_task_id) === text(taskId))
      .sort(byPriority);
  }

  function renderPlanningTaskTree(taskId, unresolvedIds, path = new Set()) {
    const normalizedTaskId = text(taskId);
    if (!normalizedTaskId || path.has(normalizedTaskId)) {
      return "";
    }
    const nextPath = new Set(path);
    nextPath.add(normalizedTaskId);
    const children = planningTaskChildren(normalizedTaskId);
    if (!children.length) {
      return "";
    }
    const unresolved = new Set((unresolvedIds || []).map(text));
    return `
      <ul class="planning-task-tree">
        ${children.map((child) => `
          <li class="${unresolved.has(text(child.id)) ? "planning-task-tree-unresolved" : ""}">
            <span class="planning-task-tree-row">
              <strong>${escapeHtml(child.title)}</strong>
              <span class="state-pill ${escapeAttr(child.state)}">${escapeHtml(taskStateLabel(child))}</span>
            </span>
            ${renderPlanningTaskTree(child.id, unresolvedIds, nextPath)}
          </li>
        `).join("")}
      </ul>
    `;
  }

  function renderPlanningTaskHierarchy(task) {
    const assessment = task.assessment || {};
    const childIds = uniqueTextList(task.child_task_ids || assessment.direct_child_ids || []);
    const unresolvedIds = uniqueTextList(assessment.unresolved_descendant_ids || []);
    const parent = task.parent_task_id ? peers.app.state.tasks?.[task.parent_task_id] : null;
    const dependencies = uniqueTextList(task.dependency_task_ids || [])
      .map((id) => peers.app.state.tasks?.[id])
      .filter(Boolean);
    const hasTree = childIds.length || unresolvedIds.length;
    return `
      <div class="planning-hierarchy-summary">
        <span><strong>${childIds.length}</strong> direct child${childIds.length === 1 ? "" : "ren"}</span>
        <span><strong>${unresolvedIds.length}</strong> unresolved descendant${unresolvedIds.length === 1 ? "" : "s"}</span>
      </div>
      ${parent ? `<div class="planning-named-relation"><span>Parent</span><strong>${escapeHtml(parent.title)}</strong><span class="state-pill ${escapeAttr(parent.state)}">${escapeHtml(taskStateLabel(parent))}</span></div>` : ""}
      ${dependencies.length ? `<div class="planning-named-relations"><strong>Dependencies</strong>${dependencies.map((dependency) => `<span>${escapeHtml(dependency.title)} <span class="state-pill ${escapeAttr(dependency.state)}">${escapeHtml(taskStateLabel(dependency))}</span></span>`).join("")}</div>` : ""}
      ${
        hasTree
          ? `
            <details class="planning-hierarchy-details" ${planningDisclosureAttributes("hierarchy", task.id)}>
              <summary>Show named hierarchy (${unresolvedIds.length} unresolved)</summary>
              ${renderPlanningTaskTree(task.id, unresolvedIds)}
            </details>
          `
          : `<p class="empty-state">No child tasks.</p>`
      }
    `;
  }

  function planningContributionState(deliverableId, contribution) {
    const missing = new Set(contribution.missing_source_deliverable_ids || []);
    if (missing.has(deliverableId)) {
      return "missing";
    }
    const deliverable = peers.app.state.deliverables?.[deliverableId];
    if (!deliverable) {
      return "missing";
    }
    if (text(deliverable.state) === "accepted") {
      return "accepted";
    }
    const reviews = uniqueTextList(deliverable.review_request_ids || [])
      .map((id) => peers.app.state.review_requests?.[id])
      .filter(Boolean);
    if (reviews.some((review) => text(review.state) === "accepted")) {
      return "reviewed";
    }
    if ((contribution.provided_source_deliverable_ids || []).includes(deliverableId)) {
      return "submitted";
    }
    return "submitted";
  }

  function renderPlanningContributionChecklist(task) {
    const assessment = task.assessment || {};
    const contribution = assessment.contribution_coverage || {};
    const requiredIds = uniqueTextList(contribution.required_source_deliverable_ids || []);
    const currentDeliverables = values(peers.app.state.deliverables)
      .filter((deliverable) => text(deliverable.task_id) === text(task.id))
      .filter((deliverable) => !deliverable.superseded_by_deliverable_id);
    const unexpectedIds = uniqueTextList(contribution.unexpected_source_deliverable_ids || []);
    const rows = requiredIds.map((deliverableId) => {
      const deliverable = peers.app.state.deliverables?.[deliverableId];
      const sourceTask = deliverable ? peers.reviews.deliverableTask(deliverable) : null;
      const status = planningContributionState(deliverableId, contribution);
      return `
        <li class="planning-contribution-${escapeAttr(status)}">
          <span class="planning-contribution-marker" aria-hidden="true">${status === "accepted" ? "✓" : status === "missing" ? "!" : "•"}</span>
          <span><strong>${escapeHtml(sourceTask?.title || (deliverable ? peers.reviews.deliverableDisplaySubject(deliverable) : "Required child contribution"))}</strong><small>${escapeHtml(humanizeToken(status))}</small></span>
        </li>
      `;
    });
    if (!rows.length && !currentDeliverables.length) {
      return `<p class="empty-state">No contribution composition is required for this task.</p>`;
    }
    return `
      <div class="planning-contribution-status">
        <span class="state-pill ${contribution.complete ? "accepted" : contribution.status === "incomplete" ? "blocked" : "neutral"}">${escapeHtml(humanizeToken(contribution.status || "not applicable"))}</span>
        ${currentDeliverables.length ? `<span>${currentDeliverables.length} submitted task deliverable${currentDeliverables.length === 1 ? "" : "s"}</span>` : ""}
      </div>
      ${rows.length ? `<ul class="planning-contribution-checklist">${rows.join("")}</ul>` : ""}
      ${unexpectedIds.length ? `<div class="task-invalid-warning">${unexpectedIds.length} unexpected source contribution${unexpectedIds.length === 1 ? "" : "s"} require review.</div>` : ""}
    `;
  }

  function renderPlanningReviewStatus(task) {
    const assessment = task.assessment || {};
    const reviewIds = uniqueTextList(assessment.active_review_request_ids || []);
    const reviews = reviewIds.map((id) => peers.app.state.review_requests?.[id]).filter(Boolean);
    const acceptanceCount = uniqueTextList(assessment.active_acceptance_ids || []).length;
    if (!reviews.length && !acceptanceCount) {
      return "";
    }
    return `
      <div class="planning-delivery-review">
        ${acceptanceCount ? `<span>${acceptanceCount} direct acceptance${acceptanceCount === 1 ? "" : "s"} pending</span>` : ""}
        ${reviews.map((review) => {
          const reviewer = peers.app.state.agents?.[review.reviewer_agent_id];
          const dispatch = peers.recovery.latestDispatchForReview(review.id);
          const runtime = peers.app.state.agent_runtimes?.[review.reviewer_agent_id];
          const active = dispatch && ["preparing", "leased"].includes(text(dispatch.status));
          const lease = active && runtime?.wake_leased_until
            ? ` · lease until ${peers.app.formatTimelineTimestamp(runtime.wake_leased_until)}`
            : "";
          const dispatchStatus = dispatch
            ? ` · dispatch ${humanizeToken(dispatch.status || "unknown")}`
            : " · awaiting dispatch";
          return `<span>Review by ${escapeHtml(reviewer ? peers.agents.agentLabel(reviewer) : "assigned reviewer")} <span class="state-pill ${escapeAttr(review.state)}">${escapeHtml(humanizeToken(review.state))}</span>${escapeHtml(dispatchStatus + lease)}</span>`;
        }).join("")}
      </div>
    `;
  }

  function renderPlanningTaskBlockers(task) {
    const blockers = peers.recovery.activeBlockersForRecord("task", task.id, task.id);
    const inherited = peers.recovery.inheritedTaskBlockers(task);
    const rows = [
      ...blockers.map((blocker) => ({
        summary: blocker.summary || blocker.reason || "Task blocker",
        ownerId: blocker.delegate_agent_id || blocker.owner_agent_id,
        next: peers.tasks.blockerNextAction(blocker),
      })),
      ...inherited.map((blocker) => ({
        summary: blocker.summary || blocker.reason || "Upstream blocker",
        ownerId: blocker.delegate_agent_id || blocker.owner_agent_id,
        next: blocker.relationship ? `Resolve the ${humanizeToken(blocker.relationship)} blocker upstream.` : "Resolve the upstream blocker.",
      })),
    ];
    if (!rows.length) {
      return "";
    }
    return `
      <ul class="planning-task-blockers">
        ${rows.map((row) => {
          const owner = row.ownerId ? peers.app.state.agents?.[row.ownerId] : null;
          return `<li><strong>${renderIdentifierText(row.summary)}</strong><span>Owner: ${escapeHtml(owner ? peers.agents.agentLabel(owner) : "Operator")}</span><span>Next: ${renderIdentifierText(row.next)}</span></li>`;
        }).join("")}
      </ul>
    `;
  }

  function renderPlanningInlineMarkdown(value) {
    const source = String(value ?? "");
    const output = [];
    let offset = 0;
    for (const match of source.matchAll(/(`+)([^\n]+?)\1(?!`)|\*\*([^*\n]+)\*\*/g)) {
      output.push(renderIdentifierText(source.slice(offset, match.index)));
      if (match[1]) {
        output.push(isOpaqueIdentifier(match[2], { explicit: true })
          ? renderIdentifierText(match[0])
          : `<code>${escapeHtml(match[2])}</code>`);
      } else {
        output.push(`<strong>${renderPlanningInlineMarkdown(match[3])}</strong>`);
      }
      offset = match.index + match[0].length;
    }
    output.push(renderIdentifierText(source.slice(offset)));
    return output.join("");
  }

  function planningBriefHeading(line) {
    const markdown = line.match(/^(#{1,6})\s+(.+)$/);
    if (markdown) {
      return markdown[2].trim();
    }
    const framed = line.match(/^={2,}\s*(.+?)\s*={2,}$/);
    if (framed) {
      return framed[1].trim();
    }
    const normalized = line.trim();
    if (
      normalized.length >= 3 &&
      normalized.length <= 90 &&
      /[A-Z]/.test(normalized) &&
      normalized === normalized.toUpperCase() &&
      !/[.!?]$/.test(normalized)
    ) {
      return normalized;
    }
    return "";
  }

  function renderPlanningBriefMarkdown(value) {
    const lines = text(value).split(/\r?\n/);
    const output = [];
    let paragraph = [];
    let listType = "";
    let listItems = [];
    let codeFence = "";
    let codeLines = [];
    const flushParagraph = () => {
      if (!paragraph.length) return;
      output.push(`<p>${renderPlanningInlineMarkdown(paragraph.join(" "))}</p>`);
      paragraph = [];
    };
    const flushList = () => {
      if (!listItems.length) return;
      output.push(`<${listType}>${listItems.map((item) => `<li>${renderPlanningInlineMarkdown(item)}</li>`).join("")}</${listType}>`);
      listType = "";
      listItems = [];
    };
    const flushCode = () => {
      output.push(`<pre><code>${escapeHtml(codeLines.join("\n"))}</code></pre>`);
      codeFence = "";
      codeLines = [];
    };
    lines.forEach((rawLine) => {
      const line = rawLine.trim();
      if (codeFence) {
        const closing = line.match(/^(`{3,}|~{3,})\s*$/);
        if (closing && closing[1][0] === codeFence[0] && closing[1].length >= codeFence.length) flushCode();
        else codeLines.push(rawLine);
        return;
      }
      const fence = line.match(/^(`{3,}|~{3,})/);
      if (fence) {
        flushParagraph();
        flushList();
        const inline = line.match(/^(`{3,}|~{3,})(.*?)\1\s*$/);
        if (inline) {
          codeLines = [inline[2]];
          flushCode();
        } else {
          codeFence = fence[1];
        }
        return;
      }
      const heading = planningBriefHeading(line);
      const unordered = line.match(/^[-*•]\s+(.+)$/);
      const ordered = line.match(/^\d+[.)]\s+(.+)$/);
      if (!line) {
        flushParagraph();
        flushList();
        return;
      }
      if (heading) {
        flushParagraph();
        flushList();
        output.push(`<h5>${renderPlanningInlineMarkdown(heading)}</h5>`);
        return;
      }
      if (unordered || ordered) {
        flushParagraph();
        const nextType = unordered ? "ul" : "ol";
        if (listType && listType !== nextType) {
          flushList();
        }
        listType = nextType;
        listItems.push((unordered || ordered)[1]);
        return;
      }
      flushList();
      paragraph.push(line);
    });
    flushParagraph();
    flushList();
    if (codeFence) flushCode();
    return output.join("");
  }

  function renderPlanningTaskBrief(task) {
    const brief = text(task.description);
    if (!brief) {
      return `<p class="empty-state">No task brief.</p>`;
    }
    const isLong = brief.length > 320 || brief.split(/\r?\n/).length > 6;
    const body = `<div class="planning-task-brief-content">${renderPlanningBriefMarkdown(brief)}</div>`;
    if (!isLong) {
      return body;
    }
    return `
      <details class="planning-task-brief-details" ${planningDisclosureAttributes("brief", task.id)}>
        <summary>Show full brief</summary>
        ${body}
      </details>
    `;
  }

  function renderPlanningTaskTechnicalDetails(task) {
    const assessment = task.assessment || {};
    const worktreeContexts = peers.tasks.taskGitWorkContexts(task);
    const blockerIds = [
      ...peers.recovery.activeBlockersForRecord("task", task.id, task.id).map((blocker) => blocker.id),
      ...peers.recovery.inheritedTaskBlockers(task).map((blocker) => blocker.blocker_id),
    ];
    return `
      ${peers.reviews.renderTechnicalDetails([
        ["Task ID", task.id],
        ["Project ID", task.project_id],
        ["Assigned to", peers.board.taskAssignmentLabel(task)],
        ["Owner ID", task.owner_agent_id],
        ["Assignment role", task.assignment_role_id],
        ["Assignment wait reason", task.assignment_wait_reason],
        ["Case ID", task.case_id],
        ["Parent task ID", task.parent_task_id],
        ["Originating task ID", task.originating_task_id],
        ["Dependency task IDs", task.dependency_task_ids || []],
        ["Integration boundary", task.integration_boundary ? "Yes" : null],
        ["Requires Git provenance", task.deliverable_contract?.require_git_provenance ? "Yes" : null],
        ["Required contribution task IDs", task.deliverable_contract?.required_contribution_task_ids || []],
        ["Child task IDs", task.child_task_ids || assessment.direct_child_ids || []],
        ["Unresolved task IDs", assessment.unresolved_descendant_ids || []],
        ["Deliverable IDs", task.deliverable_ids || []],
        ["Acceptance IDs", assessment.active_acceptance_ids || []],
        ["Review IDs", assessment.active_review_request_ids || []],
        ["Blocker IDs", blockerIds],
      ], {
        summary: "Identifiers and lifecycle references",
        disclosureKey: planningDisclosureKey("technical", task.id),
      })}
      ${worktreeContexts.length ? `<details class="technical-details planning-worktree-details" ${planningDisclosureAttributes("worktree", task.id)}><summary>Worktree mapping</summary>${peers.tasks.renderTaskWorktreeMapping(task)}</details>` : ""}
    `;
  }

  return {
    bindPlanningFilters,
    comparePlanningProjectCards,
    comparePlanningTasks,
    planningDisclosureAttributes,
    planningTaskLifecycleReason,
    planningTasksForProject,
    renderPlanningContributionChecklist,
    renderPlanningProjectBlock,
    renderPlanningReviewStatus,
    renderPlanningTaskBlockers,
    renderPlanningTaskBrief,
    renderPlanningTaskHierarchy,
    renderPlanningTaskLifecycle,
    renderPlanningTaskTechnicalDetails,
    renderTasks,
    taskAssessmentResolutionReasonLabel,
    taskRelativeAge,
    updatePlanningFilterControls,
  };
}

// Task updates, cancellation availability, blockers, and worktree recovery.
import {
  byId,
  byPriority,
  escapeAttr,
  escapeHtml,
  formData,
  humanizeToken,
  readablePreview,
  responseText,
  splitList,
  taskStateLabel,
  text,
  uniqueTextList,
  values,
} from "./team-ui-format.js";
import { API_BASE, directlyCancellableTaskStates, terminalTaskStates } from "./team-ui-config.js";
import { renderIdentifierText } from "./team-identifiers.js";

export function createTeamTasksController(peers) {

  function cancelledTaskDependencyRepairEvidence(predecessorTaskId, successorTaskId) {
    return values(peers.app.state.inbox)
      .filter(
        (item) =>
          text(item.payload?.kind) === "task_dependency_graph_repair_audit" &&
          text(item.payload?.predecessor_task_id) === text(predecessorTaskId) &&
          text(item.payload?.successor_task_id) === text(successorTaskId),
      )
      .sort((left, right) =>
        text(right.created_at).localeCompare(text(left.created_at)),
      )[0] || null;
  }

  function cancelledTaskDependencyRepairAffectedIds(predecessorTaskId) {
    const normalizedPredecessorId = text(predecessorTaskId);
    return values(peers.app.state.tasks)
      .filter((task) => !terminalTaskStates.has(text(task.state)))
      .filter(
        (task) =>
          (task.dependency_task_ids || []).some(
            (dependencyId) => text(dependencyId) === normalizedPredecessorId,
          ) ||
          (Boolean(task.integration_boundary) &&
            (task.deliverable_contract?.required_contribution_task_ids || []).some(
              (dependencyId) => text(dependencyId) === normalizedPredecessorId,
            )),
      )
      .map((task) => text(task.id))
      .filter(Boolean)
      .sort();
  }

  function cancelledTaskDependencyRepairAction(task, cancellation) {
    const successorTaskId = text(cancellation?.successor_task_id);
    if (
      !task ||
      text(task.state) !== "cancelled" ||
      !successorTaskId ||
      Boolean(cancellation?.rebind_dependents)
    ) {
      return "";
    }
    const affectedIds = cancelledTaskDependencyRepairAffectedIds(task.id);
    const evidence = cancelledTaskDependencyRepairEvidence(task.id, successorTaskId);
    if (!affectedIds.length && !evidence) {
      return "";
    }
    const repairedIds = evidence?.payload?.rebound_dependent_task_ids || [];
    return `
      <div class="record-actions task-dependency-repair-action">
        ${
          evidence
            ? `<span class="meta-line">Last audited repair: ${escapeHtml(
                repairedIds.length
                  ? `${repairedIds.length} dependent task${repairedIds.length === 1 ? "" : "s"}`
                  : "no graph changes",
              )}${evidence.payload?.repaired_at ? ` · ${escapeHtml(peers.app.formatTimelineTimestamp(evidence.payload.repaired_at))}` : ""}</span>`
            : `<span class="meta-line">${affectedIds.length} active dependent task${affectedIds.length === 1 ? "" : "s"} still reference this predecessor.</span>`
        }
        <button
          type="button"
          class="record-action"
          data-repair-cancelled-task-dependencies="${escapeAttr(task.id)}"
          data-successor-task-id="${escapeAttr(successorTaskId)}"
          ${peers.access.lockedAttrs("repair cancelled-task dependencies")}
        >${evidence && !affectedIds.length ? "Verify dependency repair" : "Repair stranded dependents"}</button>
      </div>
    `;
  }

  function renderPlanningTaskCancellation(cancellation, task) {
    const successor = cancellation.successor_task_id
      ? peers.app.state.tasks?.[cancellation.successor_task_id]
      : null;
    const actor = cancellation.actor_id ? peers.app.state.agents?.[cancellation.actor_id] : null;
    const details = [
      cancellation.reason,
      cancellation.reason_code ? humanizeToken(cancellation.reason_code) : "",
      successor ? `Successor: ${successor.title}` : "",
      cancellation.rebind_dependents
        ? `${(cancellation.rebound_dependent_task_ids || []).length} active dependencies rebound`
        : "",
      (cancellation.rebound_integration_boundary_task_ids || []).length
        ? `${cancellation.rebound_integration_boundary_task_ids.length} integration preparations re-armed`
        : "",
      (cancellation.invalidated_git_work_context_ids || []).length
        ? `${cancellation.invalidated_git_work_context_ids.length} stale Git contexts abandoned`
        : "",
      actor ? `By ${peers.agents.agentLabel(actor)}` : cancellation.actor_kind ? `By ${humanizeToken(cancellation.actor_kind)}` : "",
      cancellation.cancelled_at ? `Cancelled ${peers.app.formatTimelineTimestamp(cancellation.cancelled_at)}` : "",
    ].filter(Boolean);
    return `<div class="task-cancellation"><strong>Cancelled:</strong> ${escapeHtml(details.join(" · "))}${cancelledTaskDependencyRepairAction(task, cancellation)}</div>`;
  }

  function renderStructuredPlanningTask(task, { depth = 0, hierarchyWarning = "" } = {}) {
    const ownerName = peers.board.taskAssignmentLabel(task);
    const assessment = task.assessment || {};
    const reason = peers.planning.planningTaskLifecycleReason(task);
    const readiness = text(task.readiness || assessment.readiness || "unknown");
    const resolution = text(task.resolution_status || assessment.resolution_status || "unknown");
    const stateSince = task.state_entered_at || task.updated_at || task.created_at;
    const invalidCodes = task.invalid_reason_codes || assessment.invalid_reason_codes || [];
    const classes = [
      "task-item",
      "compact-record-card",
      "planning-task-card",
      depth ? "task-hierarchy-child" : "task-hierarchy-root",
      hierarchyWarning || invalidCodes.length ? "task-hierarchy-invalid" : "",
    ].filter(Boolean).join(" ");
    return `
      <li class="${classes}" style="--task-depth: ${Math.max(0, Number(depth) || 0)}" data-planning-task-id="${escapeAttr(task.id)}">
        <details class="planning-task-details" ${peers.planning.planningDisclosureAttributes("task", task.id)}>
          <summary class="compact-record-summary planning-task-summary">
            <span class="compact-record-main">
              <strong>${escapeHtml(task.title)}</strong>
              <span>${escapeHtml(ownerName)} · state for ${escapeHtml(peers.planning.taskRelativeAge(stateSince))}</span>
              <span class="planning-task-reason-preview">${escapeHtml(readablePreview(reason, 140))}</span>
            </span>
            <span class="planning-task-summary-meta">
              <span class="state-pill ${escapeAttr(task.state)}">${escapeHtml(taskStateLabel(task))}</span>
              <span class="state-pill ${escapeAttr(taskReadinessPill(readiness))}">${escapeHtml(humanizeToken(readiness))}</span>
            </span>
          </summary>
          <div class="compact-record-body planning-task-body">
            <section class="planning-task-section">
              <div class="planning-task-section-heading"><h4>Overview</h4><div class="record-actions">${peers.access.editButton("task", task.id)}${peers.access.taskCancellationButton(task)}</div></div>
              <div class="planning-task-overview">
                <div><span>Owner</span><strong>${escapeHtml(ownerName)}</strong></div>
                <div><span>State</span><strong>${escapeHtml(taskStateLabel(task))}</strong></div>
                <div><span>State age</span><strong>${escapeHtml(peers.planning.taskRelativeAge(stateSince))}</strong></div>
                <div><span>Priority</span><strong>${Number(task.priority ?? 0)}</strong></div>
              </div>
              <p class="planning-lifecycle-reason"><strong>Lifecycle reason:</strong> ${renderIdentifierText(reason)}</p>
              ${peers.recovery.renderPlanningExecutionStatus(task)}
              ${peers.planning.renderPlanningTaskBlockers(task)}
            </section>
            <section class="planning-task-section">
              <h4>Lifecycle</h4>
              ${peers.planning.renderPlanningTaskLifecycle(task)}
              ${task.cancellation ? renderPlanningTaskCancellation(task.cancellation, task) : ""}
              ${invalidCodes.length ? `<div class="task-invalid-warning"><strong>Lifecycle repair required:</strong> ${escapeHtml(invalidCodes.map(humanizeToken).join(", "))} ${peers.access.editButton("task", task.id, "Repair task")}</div>` : ""}
            </section>
            <section class="planning-task-section">
              <h4>Hierarchy</h4>
              ${peers.planning.renderPlanningTaskHierarchy(task)}
            </section>
            <section class="planning-task-section">
              <h4>Delivery / contribution</h4>
              ${peers.planning.renderPlanningContributionChecklist(task)}
              ${peers.planning.renderPlanningReviewStatus(task)}
            </section>
            <section class="planning-task-section planning-task-brief-section">
              <h4>Task brief</h4>
              ${peers.planning.renderPlanningTaskBrief(task)}
            </section>
            <section class="planning-task-section planning-task-technical-section">
              <h4>Technical details</h4>
              ${peers.planning.renderPlanningTaskTechnicalDetails(task)}
            </section>
          </div>
        </details>
      </li>
    `;
  }

  function renderTaskGraph(task) {
    const assessment = task.assessment || {};
    const lines = [];
    if (task.case_id) {
      const item = peers.app.state.cases[task.case_id];
      lines.push(`case ${item ? item.title : task.case_id}`);
    }
    if (task.parent_task_id) {
      lines.push(`parent ${task.parent_task_id}`);
    }
    if (task.originating_task_id) {
      lines.push(`originating task ${task.originating_task_id}`);
    }
    if ((task.dependency_task_ids || []).length) {
      lines.push(`depends on ${task.dependency_task_ids.join(", ")}`);
    }
    const dependencyWaitIds = peers.board.taskBoardDependencyWaitIds(task);
    if (peers.board.taskBoardWaitsOnDependencies(task) && dependencyWaitIds.length) {
      lines.push(`waiting on dependencies ${dependencyWaitIds.join(", ")}`);
    }
    peers.recovery.activeBlockersForRecord("task", task.id, task.id).forEach((blocker) => {
      lines.push(peers.recovery.blockerRoutingLabel(blocker));
    });
    const childIds = task.child_task_ids || assessment.direct_child_ids || [];
    if (childIds.length) {
      lines.push(`children ${childIds.join(", ")}`);
    }
    const unresolvedIds = assessment.unresolved_descendant_ids || [];
    if (unresolvedIds.length) {
      lines.push(`unresolved descendants ${unresolvedIds.join(", ")}`);
    }
    const counts = task.subtask_counts || {};
    const subtaskLine = counts.total
      ? `subtasks ${counts.accepted || 0} accepted, ${counts.finished || 0} finished, ${counts.open || 0} open, ${counts.blocked || 0} blocked`
      : "";
    const graphLines = lines.length
      ? `<div class="meta-line">${escapeHtml(lines.join(" - "))}</div>`
      : "";
    const countLine = subtaskLine ? `<div class="meta-line">${escapeHtml(subtaskLine)}</div>` : "";
    const readiness = text(task.readiness || assessment.readiness || "unknown");
    const resolution = text(task.resolution_status || assessment.resolution_status || "unknown");
    const integrationReady = Boolean(task.integration_ready ?? assessment.integration_ready);
    const acceptanceIds = assessment.active_acceptance_ids || [];
    const reviewIds = assessment.active_review_request_ids || [];
    const contribution = assessment.contribution_coverage || {};
    const coverageBits = [
      text(contribution.status) ? `coverage ${text(contribution.status)}` : "",
      (contribution.missing_source_deliverable_ids || []).length
        ? `missing ${contribution.missing_source_deliverable_ids.join(", ")}`
        : "",
      (contribution.unexpected_source_deliverable_ids || []).length
        ? `unexpected ${contribution.unexpected_source_deliverable_ids.join(", ")}`
        : "",
    ].filter(Boolean);
    const invalidCodes = task.invalid_reason_codes || assessment.invalid_reason_codes || [];
    const cancellation = task.cancellation || null;
    return `
      <div class="task-lifecycle-summary">
        <span>Readiness <span class="state-pill ${escapeAttr(taskReadinessPill(readiness))}">${escapeHtml(readiness)}</span></span>
        <span>Resolution <span class="state-pill ${escapeAttr(taskResolutionPill(resolution))}">${escapeHtml(resolution)}</span></span>
        <span>Integration <span class="state-pill ${integrationReady ? "accepted" : "neutral"}">${integrationReady ? "ready" : "not ready"}</span></span>
        <span>Dependencies <span class="state-pill ${escapeHtml(task.dependency_status || "satisfied")}">${escapeHtml(task.dependency_status || "satisfied")}</span></span>
      </div>
      ${assessment.resolution_reason ? `<div class="meta-line">Lifecycle reason: ${escapeHtml(peers.planning.taskAssessmentResolutionReasonLabel(assessment.resolution_reason))}</div>` : ""}
      ${graphLines}${countLine}
      ${acceptanceIds.length ? `<div class="meta-line">Direct acceptance: ${escapeHtml(acceptanceIds.join(", "))}</div>` : ""}
      ${reviewIds.length ? `<div class="meta-line">Formal review: ${escapeHtml(reviewIds.join(", "))}</div>` : ""}
      ${coverageBits.length ? `<div class="meta-line">Contribution composition: ${escapeHtml(coverageBits.join(" - "))}</div>` : ""}
      ${cancellation ? renderTaskCancellation(cancellation, task) : ""}
      ${renderTaskBlockerDetails(task)}
      ${renderTaskWorktreeMapping(task)}
      ${
        invalidCodes.length
          ? `<div class="task-invalid-warning"><strong>Legacy lifecycle repair required:</strong> ${escapeHtml(invalidCodes.join(", "))} ${peers.access.editButton("task", task.id, "Repair task")}</div>`
          : ""
      }
    `;
  }

  function taskReadinessPill(readiness) {
    if (readiness === "actionable") return "accepted";
    if (readiness === "blocked" || readiness === "invalid") return "blocked";
    if (readiness === "resolved") return "neutral";
    return "info";
  }

  function taskResolutionPill(resolution) {
    if (resolution === "resolved") return "accepted";
    if (resolution === "invalid") return "blocked";
    return "info";
  }

  function renderTaskCancellation(cancellation, task) {
    const parts = [
      cancellation.reason_code ? `reason ${cancellation.reason_code}` : "",
      cancellation.reason,
      cancellation.actor_kind && cancellation.actor_id
        ? `by ${cancellation.actor_kind} ${cancellation.actor_id}`
        : "",
      cancellation.successor_task_id ? `successor ${cancellation.successor_task_id}` : "",
      cancellation.rebind_dependents
        ? `rebound dependents ${(cancellation.rebound_dependent_task_ids || []).join(", ") || "none"}`
        : "",
      (cancellation.rebound_integration_boundary_task_ids || []).length
        ? `re-armed integration boundaries ${cancellation.rebound_integration_boundary_task_ids.join(", ")}`
        : "",
      (cancellation.invalidated_git_work_context_ids || []).length
        ? `abandoned stale Git contexts ${cancellation.invalidated_git_work_context_ids.join(", ")}`
        : "",
      cancellation.cancelled_at ? `at ${cancellation.cancelled_at}` : "",
    ].filter(Boolean);
    return `<div class="task-cancellation"><strong>Cancelled:</strong> ${escapeHtml(parts.join(" - "))}${cancelledTaskDependencyRepairAction(task, cancellation)}</div>`;
  }

  function renderTaskBlockerDetails(task) {
    const blockers = peers.recovery.activeBlockersForRecord("task", task.id, task.id);
    const inheritedBlockers = peers.recovery.inheritedTaskBlockers(task);
    if (!blockers.length && !inheritedBlockers.length) {
      return "";
    }
    return `
      <div class="task-blocker-details">
        ${blockers.map((blocker) => renderTaskBlockerDetail(blocker)).join("")}
        ${inheritedBlockers.map((blocker) => renderInheritedTaskBlockerDetail(blocker)).join("")}
      </div>
    `;
  }

  function renderInheritedTaskBlockerDetail(blocker) {
    const route = [
      blocker.owner_agent_id ? `owner ${blocker.owner_agent_id}` : "no visible agent owner",
      blocker.delegate_agent_id ? `delegate ${blocker.delegate_agent_id}` : "",
      blocker.route ? `route ${blocker.route}` : "",
      `escalation level ${Number(blocker.escalation_depth || 0)}`,
    ].filter(Boolean).join(" - ");
    const timing = [
      blocker.created_at ? `blocked since ${blocker.created_at}` : "",
      blocker.follow_up_at ? `follow up ${blocker.follow_up_at}` : "",
      blocker.last_progress_at ? `last progress ${blocker.last_progress_at}` : "",
    ].filter(Boolean).join(" - ");
    return `
      <section class="task-blocker-card task-inherited-blocker-card">
        <div><strong>Upstream ${escapeHtml(blocker.relationship || "task")} ${escapeHtml(blocker.upstream_task_id || "unknown")}</strong></div>
        <div><strong>Blocker ${escapeHtml(blocker.blocker_id)}</strong> <span class="state-pill blocked">${escapeHtml(blocker.reason_code || "unspecified")}</span></div>
        <div>${escapeHtml(blocker.reason || blocker.summary || "No reason supplied")}</div>
        ${blocker.details ? `<div>${escapeHtml(blocker.details)}</div>` : ""}
        <div class="meta-line">Source ${escapeHtml(`${blocker.source_kind || "unknown"} ${blocker.source_id || ""}`.trim())} - ${escapeHtml(route)}</div>
        ${timing ? `<div class="meta-line">${escapeHtml(timing)}</div>` : ""}
      </section>
    `;
  }

  function renderTaskBlockerDetail(blocker) {
    const latestEvent = latestBlockerEvent(blocker.id);
    const source = `${text(blocker.source_kind) || "unknown"} ${text(blocker.source_id) || ""}`.trim();
    const accountable = [
      blocker.owner_agent_id ? `owner ${blocker.owner_agent_id}` : "no agent owner",
      blocker.delegate_agent_id ? `delegate ${blocker.delegate_agent_id}` : "",
    ].filter(Boolean).join(" - ");
    const timing = [
      blocker.created_at ? `blocked since ${blocker.created_at}` : "",
      blocker.follow_up_at ? `follow up ${blocker.follow_up_at}` : "",
      blocker.last_progress_at ? `last progress ${blocker.last_progress_at}` : "",
    ].filter(Boolean).join(" - ");
    return `
      <section class="task-blocker-card">
        <div><strong>Blocker ${escapeHtml(blocker.id)}</strong> <span class="state-pill blocked">${escapeHtml(blocker.reason_code || "unspecified")}</span></div>
        <div>${escapeHtml(blocker.summary || "No summary")}</div>
        ${blocker.details ? `<div>${escapeHtml(blocker.details)}</div>` : ""}
        <div class="meta-line">Source ${escapeHtml(source)} - ${escapeHtml(accountable)} - route ${escapeHtml(blocker.route || "unknown")} - escalation level ${Number(blocker.escalation_depth || 0)}</div>
        ${timing ? `<div class="meta-line">${escapeHtml(timing)}</div>` : ""}
        ${
          latestEvent
            ? `<div class="meta-line">Latest event ${escapeHtml(latestEvent.event_type)}${latestEvent.note ? `: ${escapeHtml(latestEvent.note)}` : ""}</div>`
            : ""
        }
        <div class="meta-line">Next action: ${escapeHtml(blockerNextAction(blocker))}</div>
        ${peers.recovery.renderTerminalBlockerResolveAction(blocker)}
      </section>
    `;
  }

  function latestBlockerEvent(blockerId) {
    return values(peers.app.state.blocker_events)
      .filter((event) => text(event.blocker_id) === text(blockerId))
      .sort((left, right) =>
        String(right.created_at || "").localeCompare(String(left.created_at || "")) ||
        String(right.id || "").localeCompare(String(left.id || "")),
      )[0] || null;
  }

  function blockerNextAction(blocker) {
    if (blocker.delegate_agent_id) {
      return `delegate ${blocker.delegate_agent_id} reports progress; owner ${blocker.owner_agent_id || "unassigned"} remains accountable`;
    }
    if (blocker.follow_up_at) {
      return `owner ${blocker.owner_agent_id || blocker.route || "operator"} follows up at ${blocker.follow_up_at}`;
    }
    if (["governance", "operator"].includes(text(blocker.route))) {
      return `${humanizeToken(blocker.route)} verifies the blocking condition and resolves or escalates it`;
    }
    return `owner ${blocker.owner_agent_id || "unassigned"} records progress, delegates, resolves, or escalates`;
  }

  function taskGitWorkContexts(task) {
    const rows = [
      ...(task.git_work_contexts || []),
      ...values(peers.app.state.git_work_contexts).filter((context) => text(context.task_id) === text(task.id)),
    ];
    const unique = new Map();
    rows.forEach((row) => {
      const id = text(row.id || row.branch_context_id);
      if (id) unique.set(id, row);
    });
    return [...unique.values()].sort(byId);
  }

  function renderTaskWorktreeMapping(task) {
    const contexts = taskGitWorkContexts(task);
    const cleanupRows = values(peers.app.state.worktree_cleanup_candidates)
      .filter((candidate) => text(candidate.task_id) === text(task.id))
      .sort((left, right) => String(right.updated_at || "").localeCompare(String(left.updated_at || "")));
    if (!contexts.length && !cleanupRows.length) {
      return "";
    }
    const project = peers.app.state.projects[task.project_id] || {};
    const repository = project.git?.managed_repo_id || "not configured";
    const rows = contexts.map((context) => {
      const dispatch = peers.app.state.dispatches?.[context.dispatch_id] || null;
      return `
        <tr>
          <td>${escapeHtml(task.id)}</td>
          <td>${escapeHtml(task.project_id)}</td>
          <td>${escapeHtml(repository)}</td>
          <td><code>${escapeHtml(context.worktree_path || "control checkout")}</code></td>
          <td><code>${escapeHtml(context.agent_branch || context.branch || project.git?.base_branch || "")}</code></td>
          <td>${escapeHtml(context.id || context.branch_context_id || "")}${dispatch ? ` - ${escapeHtml(dispatch.status)}` : ""}</td>
        </tr>
      `;
    }).join("");
    return `
      <div class="task-worktree-mapping">
        <div class="meta-line">Repository base ${escapeHtml(project.git?.base_branch || "unknown")} is the control checkout; edit only in the task worktree below.</div>
        ${
          rows
            ? `<div class="task-worktree-scroll"><table><thead><tr><th>Task</th><th>Project</th><th>Repository</th><th>Worktree</th><th>Branch</th><th>Context / dispatch</th></tr></thead><tbody>${rows}</tbody></table></div>`
            : ""
        }
        ${cleanupRows.map(renderTaskCleanupCandidate).join("")}
      </div>
    `;
  }

  function renderTaskCleanupCandidate(candidate) {
    const operatorAction = text(candidate.state) === "operator_action_required";
    const recovery = operatorAction || text(candidate.state) === "recovery_required";
    const ignoredCount = Number(candidate.ignored_file_count || 0);
    const ignoredSample = (candidate.ignored_file_inventory || []).slice(0, 5);
    const details = [
      `cleanup ${candidate.state}`,
      candidate.last_safety_check ? `check ${candidate.last_safety_check}` : "",
      candidate.last_error,
      Number(candidate.automatic_retry_count || 0)
        ? `${Number(candidate.automatic_retry_count)} consecutive automatic attempts`
        : "",
      candidate.operator_action_reason
        ? `operator action ${humanizeToken(candidate.operator_action_reason)}`
        : "",
      candidate.operator_action_required_at
        ? `halted ${candidate.operator_action_required_at}`
        : "",
      candidate.last_operator_retry_by
        ? `last requeued by ${candidate.last_operator_retry_by}${candidate.last_operator_retry_reason ? `: ${candidate.last_operator_retry_reason}` : ""}`
        : "",
      candidate.worktree_path,
      candidate.branch,
      ignoredCount
        ? `${ignoredCount} ignored file${ignoredCount === 1 ? "" : "s"}${candidate.ignored_file_inventory_truncated ? " (sampled)" : ""}`
        : "",
      candidate.ignored_file_inventory_sha256
        ? `inventory sha256 ${candidate.ignored_file_inventory_sha256}`
        : "",
      ignoredSample.length ? `sample ${ignoredSample.join(", ")}` : "",
      Number(candidate.reclaimed_bytes || 0) ? `reclaimed ${candidate.reclaimed_bytes} bytes` : "",
    ].filter(Boolean).join(" - ");
    return `
      <div class="${recovery ? "task-cleanup-recovery" : "meta-line"}">
        <div>${escapeHtml(details)}</div>
        ${
          operatorAction
            ? `<div class="record-actions">
                <button
                  type="button"
                  class="record-action"
                  data-worktree-cleanup-retry="${escapeAttr(candidate.id)}"
                  ${peers.access.lockedAttrs("retry worktree cleanup")}
                >Retry cleanup</button>
              </div>`
            : ""
        }
      </div>
    `;
  }

  async function retryWorktreeCleanup(button, reason) {
    const candidateId = text(button?.dataset?.worktreeCleanupRetry);
    if (!candidateId) {
      peers.app.setStatus("Worktree cleanup retry context is incomplete.", "error");
      return;
    }
    button.disabled = true;
    peers.app.setStatus("Requeueing worktree cleanup.", "loading");
    try {
      const response = await peers.access.teamFetch(
        `${API_BASE}/worktree-cleanup-candidates/${encodeURIComponent(candidateId)}/retry`,
        {
          method: "POST",
          headers: {
            Accept: "application/json",
            "Content-Type": "application/json",
            "X-Team-Addon-Response-Scope": "view",
          },
          body: JSON.stringify({
            reason,
            expected_revision: peers.app.state.revision,
          }),
        },
      );
      if (!response.ok) {
        throw new Error(await responseText(response));
      }
      const result = await response.json();
      await peers.mutations.applyMutationResultPayload(result);
      peers.app.setStatus(`Cleanup ${candidateId} was requeued for a full safety pass.`, "ok");
    } catch (error) {
      peers.app.setStatus(error instanceof Error ? error.message : "Worktree cleanup retry failed.", "error");
    } finally {
      button.disabled = false;
    }
  }

  async function repairCancelledTaskDependencies(button, reason) {
    const predecessorTaskId = text(
      button?.dataset?.repairCancelledTaskDependencies,
    );
    const successorTaskId = text(button?.dataset?.successorTaskId);
    const predecessor = peers.app.state.tasks?.[predecessorTaskId] || null;
    if (
      !predecessor ||
      text(predecessor.state) !== "cancelled" ||
      !successorTaskId ||
      text(predecessor.cancellation?.successor_task_id) !== successorTaskId
    ) {
      peers.app.setStatus("Cancelled-task dependency repair context is incomplete.", "error");
      return;
    }
    const currentAffectedIds = cancelledTaskDependencyRepairAffectedIds(
      predecessorTaskId,
    );
    const priorEvidence = cancelledTaskDependencyRepairEvidence(
      predecessorTaskId,
      successorTaskId,
    );
    const expectedDependentTaskIds = currentAffectedIds.length
      ? currentAffectedIds
      : uniqueTextList(
          priorEvidence?.payload?.expected_dependent_task_ids || [],
        ).sort();
    button.disabled = true;
    peers.app.setStatus("Validating and repairing the frozen dependent-task set.", "loading");
    try {
      const response = await peers.access.teamFetch(
        `${API_BASE}/tasks/${encodeURIComponent(predecessorTaskId)}/repair-cancelled-dependencies`,
        {
          method: "POST",
          headers: {
            Accept: "application/json",
            "Content-Type": "application/json",
            "X-Team-Addon-Response-Scope": "view",
          },
          body: JSON.stringify({
            successor_task_id: successorTaskId,
            expected_dependent_task_ids: expectedDependentTaskIds,
            reason,
            expected_revision: peers.app.state.revision,
          }),
        },
      );
      if (!response.ok) {
        throw new Error(await responseText(response));
      }
      const result = await response.json();
      const repair = result.task_dependency_repair || {};
      await peers.mutations.applyMutationResultPayload(result);
      const changedCount = (repair.rebound_dependent_task_ids || []).length;
      peers.app.setStatus(
        repair.audit_evidence_reused
          ? "The prior dependency repair was verified; no graph history was rewritten."
          : `Dependency repair recorded; ${changedCount} dependent task${changedCount === 1 ? "" : "s"} rebound.`,
        "ok",
      );
    } catch (error) {
      peers.app.setStatus(
        error instanceof Error ? error.message : "Cancelled-task dependency repair failed.",
        "error",
      );
    } finally {
      button.disabled = false;
    }
  }

  async function submitTaskUpdate(form) {
    if (!peers.access.guardTeamMutation("update tasks")) {
      return;
    }
    const data = formData(form);
    const taskId = text(data.task_id);
    if (!taskId) {
      peers.app.setStatus("Task is required.", "error");
      return;
    }
    const payload = {};
    const stateValue = text(data.state);
    const priority = text(data.priority);
    const title = text(data.title);
    const description = text(data.description);
    const caseId = text(data.case_id);
    const parentTaskId = text(data.parent_task_id);
    if (stateValue) payload.state = stateValue;
    payload.case_id = caseId || null;
    if (priority) payload.priority = Number(priority);
    if (title) payload.title = title;
    if (description) payload.description = description;
    payload.parent_task_id = parentTaskId || null;
    if (form.elements.task_author_name) {
      payload.authored_by = taskAuthoredByForForm(data);
    }
    if (form.elements.git_base_branch) {
      payload.git_base_branch = text(data.git_base_branch) || null;
    }
    payload.dependency_task_ids = splitList(data.dependency_task_ids);
    const integrationBoundary = Boolean(
      form.elements.integration_boundary?.checked,
    );
    const requireGitProvenance = Boolean(
      form.elements.require_git_provenance?.checked,
    );
    const requiredContributionTaskIds = splitList(
      data.required_contribution_task_ids,
    );
    payload.integration_boundary = integrationBoundary;
    payload.deliverable_contract = (
      integrationBoundary
      || requireGitProvenance
      || requiredContributionTaskIds.length > 0
    )
      ? {
          require_git_provenance: requireGitProvenance,
          required_contribution_task_ids: requiredContributionTaskIds,
        }
      : null;
    const saved = await peers.mutations.submitMutation(form, `/tasks/${encodeURIComponent(taskId)}`, "PATCH", payload, {
      resetOnSuccess: false,
    });
    if (saved) {
      showTaskPreparationInvalidationStatus(taskId);
    }
  }

  function showTaskPreparationInvalidationStatus(taskId) {
    const item = peers.recovery.latestTaskPreparationInvalidation(taskId);
    const task = peers.app.state.tasks?.[text(taskId)];
    if (
      !item ||
      text(item.state) !== "pending" ||
      (task?.updated_at && String(item.created_at || "") < String(task.updated_at))
    ) {
      return false;
    }
    const contextCount = (item.payload?.invalidated_git_work_context_ids || []).length;
    peers.app.setStatus(
      `Saved revision ${peers.app.state.revision}. ${contextCount} stale Git preparation${contextCount === 1 ? " was" : "s were"} retired; fresh preparation is queued.`,
      "ok",
    );
    return true;
  }

  function taskAuthoredByForForm(data) {
    const name = text(data.task_author_name);
    const email = text(data.task_author_email);
    return name || email ? { name, email } : null;
  }

  function orderedTasksForAgent(agentId) {
    return values(peers.app.state.tasks)
      .filter((task) => task.owner_agent_id === agentId)
      .sort(byPriority);
  }

  function taskCancellationAvailability(task) {
    const stateValue = text(task?.state);
    if (!directlyCancellableTaskStates.has(stateValue)) {
      return {
        allowed: false,
        explanation: stateValue
          ? `This task is already ${taskStateLabel(task)} and cannot be cancelled.`
          : "This task is unavailable.",
      };
    }
    const assessment = task?.assessment || {};
    const unresolvedIds = uniqueTextList(
      task?.unresolved_descendant_ids || assessment.unresolved_descendant_ids || [],
    );
    if (unresolvedIds.length) {
      return {
        allowed: false,
        explanation: `Resolve or cancel ${unresolvedIds.length} descendant task${unresolvedIds.length === 1 ? "" : "s"} first.`,
      };
    }
    const acceptanceIds = uniqueTextList(assessment.active_acceptance_ids || []);
    const blockingReviewIds = uniqueTextList(
      assessment.active_review_request_ids || [],
    ).filter((reviewId) => {
      const review = peers.app.state.review_requests?.[reviewId];
      return !(
        text(review?.state) === "blocked" &&
        peers.recovery.activeBlockersForRecord("review_request", reviewId).length
      );
    });
    if (acceptanceIds.length || blockingReviewIds.length) {
      return {
        allowed: false,
        explanation: "Finish or retire the active acceptance and review handoffs first.",
      };
    }
    return {
      allowed: true,
      explanation: "Cancel this task with an audited reason.",
    };
  }
  function bindTaskUpdateForm() {

    const updateTaskForm = document.querySelector("#update-task-form");
    updateTaskForm?.addEventListener("submit", (event) => {
      event.preventDefault();
      void submitTaskUpdate(updateTaskForm);
    });
    updateTaskForm?.elements.task_id?.addEventListener("change", () => {
      peers.editor.syncTaskUpdateForm(updateTaskForm);
    });
  }
  function bindCleanupRetries() {

    document.addEventListener("click", (event) => {
      const button = event.target?.closest?.("[data-worktree-cleanup-retry]");
      if (!button) {
        return;
      }
      event.preventDefault();
      if (!peers.access.guardTeamMutation("retry worktree cleanup")) {
        return;
      }
      const reason = window.prompt(
        "What changed since cleanup was halted? The retry will run every safety check again.",
        "",
      );
      if (!text(reason)) {
        peers.app.setStatus("A cleanup retry reason is required.", "error");
        return;
      }
      void retryWorktreeCleanup(button, text(reason));
    });
  }
  function bindDependencyRepair() {

    document.addEventListener("click", (event) => {
      const button = event.target?.closest?.(
        "[data-repair-cancelled-task-dependencies]",
      );
      if (!button) {
        return;
      }
      event.preventDefault();
      if (!peers.access.guardTeamMutation("repair cancelled-task dependencies")) {
        return;
      }
      const reason = window.prompt(
        "Why is this graph repair authorized? The original cancellation will remain unchanged.",
        "",
      );
      if (!text(reason)) {
        peers.app.setStatus("A dependency repair reason is required.", "error");
        return;
      }
      void repairCancelledTaskDependencies(button, text(reason));
    });
  }

  return {
    bindCleanupRetries,
    bindDependencyRepair,
    bindTaskUpdateForm,
    blockerNextAction,
    orderedTasksForAgent,
    renderStructuredPlanningTask,
    renderTaskCancellation,
    renderTaskGraph,
    renderTaskWorktreeMapping,
    showTaskPreparationInvalidationStatus,
    submitTaskUpdate,
    taskAuthoredByForForm,
    taskCancellationAvailability,
    taskGitWorkContexts,
    taskReadinessPill,
    taskResolutionPill,
  };
}

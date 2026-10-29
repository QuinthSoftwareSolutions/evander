// recovery
import {
  byId,
  escapeAttr,
  escapeHtml,
  humanizeToken,
  responseText,
  text,
  uniqueTextList,
  values,
} from "./team-ui-format.js";
import { API_BASE, terminalTaskStates } from "./team-ui-config.js";

export function createTeamRecoveryController(peers) {

  function inheritedTaskBlockers(task) {
    return Array.isArray(task?.inherited_blockers)
      ? task.inherited_blockers.filter((blocker) => blocker && blocker.blocker_id)
      : [];
  }

  function activeBlockersForRecord(sourceKind, sourceId, taskId = null) {
    return values(peers.app.state.blockers)
      .filter((blocker) => {
        if (text(blocker.state) !== "open") {
          return false;
        }
        if (text(blocker.source_kind) === sourceKind && text(blocker.source_id) === sourceId) {
          return true;
        }
        return Boolean(taskId && text(blocker.task_id) === taskId);
      })
      .sort(byId);
  }

  function blockerRoutingLabel(blocker) {
    const parts = [
      `blocker ${text(blocker.id)}`,
      blocker.owner_agent_id ? `owner ${text(blocker.owner_agent_id)}` : "",
      blocker.delegate_agent_id ? `delegate ${text(blocker.delegate_agent_id)}` : "",
      blocker.route ? `route ${humanizeToken(blocker.route)}` : "",
      blocker.resolution_scope ? `scope ${humanizeToken(blocker.resolution_scope)}` : "",
      Number.isFinite(Number(blocker.escalation_depth))
        ? `level ${Number(blocker.escalation_depth)}`
        : "",
    ].filter(Boolean);
    return parts.join(" - ");
  }

  function releaseRecoveryTaskForBlocker(blocker) {
    if (
      !blocker ||
      text(blocker.source_kind) !== "release_package" ||
      !["governance", "operator"].includes(text(blocker.route))
    ) {
      return null;
    }
    const terminalStates = new Set(["finished", "accepted", "rejected", "cancelled"]);
    const candidates = uniqueTextList([
      blocker.task_id,
      ...(blocker.affected_task_ids || []),
    ])
      .map((taskId) => peers.app.state.tasks?.[taskId])
      .filter(
        (task) =>
          task &&
          text(task.created_by_actor) === "system:git-merge-recovery" &&
          !terminalStates.has(text(task.state)),
      );
    return candidates.length === 1 ? candidates[0] : null;
  }

  function candidateCorrectionRecoveryTaskForBlocker(blocker) {
    if (!blocker || text(blocker.source_kind) !== "candidate_correction_successor") {
      return null;
    }
    const correctionTaskIds = new Set(uniqueTextList([
      blocker.task_id,
      ...(blocker.affected_task_ids || []),
    ]));
    return values(peers.app.state.tasks)
      .filter(
        (task) =>
          text(task.created_by_actor) === "system:candidate-correction-recovery" &&
          correctionTaskIds.has(text(task.originating_task_id)) &&
          (!blocker.project_id || text(task.project_id) === text(blocker.project_id)),
      )
      .sort(
        (left, right) =>
          String(right.created_at || "").localeCompare(String(left.created_at || "")) ||
          byId(left, right),
      )[0] || null;
  }

  function dependencyCompositionRecoveryForBlocker(blocker) {
    return values(peers.app.state.dependency_composition_recoveries)
      .filter((recovery) => text(recovery.blocker_id) === text(blocker?.id))
      .sort(
        (left, right) =>
          String(right.created_at || "").localeCompare(String(left.created_at || "")) ||
          byId(left, right),
      )[0] || null;
  }

  function renderDependencyCompositionRecovery(blocker, recovery, extraClass = "") {
    const recoveryTask = peers.app.state.tasks?.[text(recovery.recovery_task_id)];
    const projectAgents = values(peers.app.state.agents)
      .filter((agent) =>
        text(agent.status) === "active" &&
        peers.agents.agentProjectIds(agent).includes(text(recovery.project_id)),
      )
      .sort(peers.people.comparePeopleAgents);
    const sourceSummary = (recovery.dependency_git_sources || [])
      .map((source) => `${text(source.dependency_task_id)}@${text(source.commit_sha).slice(0, 12)}`)
      .join(", ");
    const conflictSummary = uniqueTextList(recovery.conflict_paths).join(", ") || "paths unavailable";
    return `
      <div class="dependency-composition-recovery-status" role="status">
        <div>
          <strong>Dependency merge recovery</strong>
          <span class="state-pill ${escapeAttr(recovery.state || "awaiting_assignment")}">${escapeHtml(humanizeToken(recovery.state || "awaiting_assignment"))}</span>
        </div>
        <div class="meta-line">Pinned base ${escapeHtml(text(recovery.base_commit_sha).slice(0, 12))} - conflict ${escapeHtml(conflictSummary)}</div>
        <div class="meta-line">Exact inputs: ${escapeHtml(sourceSummary)}</div>
        ${
          recoveryTask
            ? `<div class="record-actions ${escapeAttr(extraClass)}">
                <button type="button" class="record-action"
                  data-dependency-composition-recovery-task-id="${escapeAttr(recoveryTask.id)}"
                >View merge recovery task</button>
              </div>`
            : `<div class="record-actions ${escapeAttr(extraClass)}">
                <label>
                  Resolver
                  <select data-dependency-composition-resolver>
                    <option value="">Select project agent</option>
                    ${projectAgents.map((agent) => `<option value="${escapeAttr(agent.id)}">${escapeHtml(peers.agents.agentLabel(agent))}</option>`).join("")}
                  </select>
                </label>
                <button type="button" class="record-action"
                  data-start-dependency-composition-recovery-id="${escapeAttr(blocker.id)}"
                  ${peers.access.lockedAttrs("start dependency composition recoveries")}
                >Create merge recovery task</button>
              </div>`
        }
      </div>
    `;
  }

  function latestDispatchForTask(taskId) {
    const normalizedTaskId = text(taskId);
    return values(peers.app.state.dispatches)
      .filter((dispatch) =>
        (dispatch.task_ids || []).some((candidateId) => text(candidateId) === normalizedTaskId),
      )
      .sort(
        (left, right) =>
          String(right.created_at || "").localeCompare(String(left.created_at || "")) ||
          byId(left, right),
      )[0] || null;
  }

  function latestDispatchForReview(reviewId) {
    const normalizedReviewId = text(reviewId);
    return values(peers.app.state.dispatches)
      .filter((dispatch) =>
        (dispatch.review_request_ids || []).some(
          (candidateId) => text(candidateId) === normalizedReviewId,
        ),
      )
      .sort(
        (left, right) =>
          String(right.created_at || "").localeCompare(String(left.created_at || "")) ||
          byId(left, right),
      )[0] || null;
  }

  function latestTaskPreparationInvalidation(taskId) {
    return peers.app.taskPreparationInvalidationByTaskId.get(text(taskId)) || null;
  }

  function renderPlanningExecutionStatus(task) {
    const blockers = activeBlockersForRecord("task", task.id, task.id);
    if (blockers.length) {
      return `<div class="planning-execution-status" role="status"><strong>Blocked</strong><span>${escapeHtml(blockers[0].summary || "Resolve the active task blocker.")}</span></div>`;
    }
    const invalidation = latestTaskPreparationInvalidation(task.id);
    const dispatch = latestDispatchForTask(task.id);
    const dispatchIsFresh = Boolean(
      dispatch &&
      (!invalidation || String(dispatch.created_at || "") >= String(invalidation.created_at || "")),
    );
    if (
      dispatchIsFresh &&
      dispatch &&
      ["preparing", "leased"].includes(text(dispatch.status))
    ) {
      const runtime = peers.app.state.agent_runtimes?.[dispatch.agent_id];
      const lease = runtime?.wake_leased_until
        ? ` Lease until ${peers.app.formatTimelineTimestamp(runtime.wake_leased_until)}.`
        : "";
      return `<div class="planning-execution-status" role="status"><strong>Agent active</strong><span>${escapeHtml(`${humanizeToken(dispatch.status)}.${lease}`)}</span></div>`;
    }
    if (invalidation && text(invalidation.state) === "pending") {
      const dispatchLabel = dispatch
        ? `Latest dispatch: ${humanizeToken(dispatch.status || "unknown")}.`
        : "Fresh preparation is queued.";
      return `<div class="planning-execution-status" role="status"><strong>Repreparing after task edit</strong><span>${escapeHtml(dispatchLabel)}</span></div>`;
    }
    if (dispatch && ["preparing", "leased"].includes(text(dispatch.status))) {
      const runtime = peers.app.state.agent_runtimes?.[dispatch.agent_id];
      const lease = runtime?.wake_leased_until
        ? ` Lease until ${peers.app.formatTimelineTimestamp(runtime.wake_leased_until)}.`
        : "";
      return `<div class="planning-execution-status" role="status"><strong>Agent active</strong><span>${escapeHtml(`${humanizeToken(dispatch.status)}.${lease}`)}</span></div>`;
    }
    return "";
  }

  function renderCandidateCorrectionRecoveryStatus(task) {
    const dispatch = latestDispatchForTask(task.id);
    const assignee = peers.app.state.agents?.[text(task.owner_agent_id)];
    const details = [
      `Task ${text(task.id)}`,
      `assigned to ${assignee ? peers.agents.agentLabel(assignee) : text(task.owner_agent_id) || "unassigned"}`,
      task.git_base_commit_sha ? `pinned commit ${text(task.git_base_commit_sha)}` : "",
      dispatch ? `dispatch ${humanizeToken(dispatch.status || "unknown")}` : "awaiting dispatch",
    ].filter(Boolean);
    return `
      <div class="candidate-correction-recovery-status" role="status">
        <div>
          <strong>Verified replacement task</strong>
          <span class="state-pill ${escapeAttr(task.state || "pending")}">${escapeHtml(humanizeToken(task.state || "pending"))}</span>
        </div>
        <div>${escapeHtml(task.title || task.id)}</div>
        <div class="meta-line">${escapeHtml(details.join(" - "))}</div>
      </div>
    `;
  }

  function renderTerminalBlockerResolveAction(blocker, extraClass = "") {
    const dependencyRecovery = dependencyCompositionRecoveryForBlocker(blocker);
    if (
      blocker &&
      text(blocker.state) === "open" &&
      dependencyRecovery &&
      ["awaiting_assignment", "in_progress"].includes(text(dependencyRecovery.state))
    ) {
      return renderDependencyCompositionRecovery(blocker, dependencyRecovery, extraClass);
    }
    if (
      blocker &&
      text(blocker.state) === "open" &&
      text(blocker.source_kind) === "candidate_correction_successor"
    ) {
      const recoveryTask = candidateCorrectionRecoveryTaskForBlocker(blocker);
      if (recoveryTask) {
        const replacementStillActive = !terminalTaskStates.has(text(recoveryTask.state));
        return `
          ${renderCandidateCorrectionRecoveryStatus(recoveryTask)}
          <div class="record-actions ${escapeAttr(extraClass)}">
            <button type="button" class="record-action"
              data-candidate-correction-recovery-task-id="${escapeAttr(recoveryTask.id)}"
            >View replacement task</button>
            ${
              replacementStillActive
                ? ""
                : `<button type="button" class="record-action"
                    data-candidate-correction-recovery-id="${escapeAttr(blocker.id)}"
                    data-candidate-correction-recovery-action="retry"
                    ${peers.access.lockedAttrs("retry candidate correction recovery")}
                  >Retry packaging</button>`
            }
          </div>
        `;
      }
      return `
        <div class="record-actions ${escapeAttr(extraClass)}">
          <button type="button" class="record-action"
            data-candidate-correction-recovery-id="${escapeAttr(blocker.id)}"
            data-candidate-correction-recovery-action="retry"
            ${peers.access.lockedAttrs("retry candidate correction recovery")}
          >Retry packaging</button>
          <button type="button" class="record-action"
            data-candidate-correction-recovery-id="${escapeAttr(blocker.id)}"
            data-candidate-correction-recovery-action="create_verified_replacement"
            ${peers.access.lockedAttrs("create verified replacement tasks")}
          >Create verified replacement</button>
        </div>
      `;
    }
    if (
      !blocker ||
      text(blocker.state) !== "open" ||
      !["governance", "operator"].includes(text(blocker.route))
    ) {
      return "";
    }
    if (text(blocker.source_kind) === "release_package") {
      const recoveryTask = releaseRecoveryTaskForBlocker(blocker);
      if (!recoveryTask) {
        return "";
      }
      const pendingDirective = Object.values(peers.app.state.inbox || {}).find(
        (item) =>
          text(item.item_type) === "operator_directive" &&
          text(item.state) === "pending" &&
          text(item.task_id) === text(recoveryTask.id) &&
          text(item.payload?.source) === "admin_ui_release_recovery",
      );
      return `
        <div class="record-actions ${escapeAttr(extraClass)}">
          <button
            type="button"
            class="record-action"
            data-resume-release-recovery-blocker-id="${escapeAttr(blocker.id)}"
            data-resume-release-recovery-task-id="${escapeAttr(recoveryTask.id)}"
            ${pendingDirective ? "disabled aria-disabled=\"true\"" : peers.access.lockedAttrs("resume release recoveries")}
          >${pendingDirective ? "Recovery wake queued" : "Resume release recovery"}</button>
        </div>
      `;
    }
    return `
      <div class="record-actions ${escapeAttr(extraClass)}">
        <button
          type="button"
          class="record-action"
          data-resolve-terminal-blocker-id="${escapeAttr(blocker.id)}"
          ${peers.access.lockedAttrs("resolve terminal blockers")}
        >Resolve blocker</button>
      </div>
    `;
  }

  function renderGitRecoveryAction(blocker) {
    const incident = values(peers.app.state.git_recovery_incidents).find((row) => row.blocker_id === blocker.id);
    if (!incident) return "";
    const label = `${text(incident.cause).replaceAll("_", " ")} · ${text(incident.state).replaceAll("_", " ")}`;
    return `<span>${escapeHtml(label)}</span><button type="button" data-git-recovery-blocker="${escapeHtml(blocker.id)}">Review recovery</button>`;
  }

  async function previewGitRecovery(blockerId) {
    if (!peers.access.guardTeamMutation("recover Git preparation")) return;
    const endpoint = `${API_BASE}/blockers/${encodeURIComponent(blockerId)}/git-recovery`;
    const dialog = document.createElement("dialog");
    dialog.className = "team-reintegration-dialog";
    dialog.innerHTML = `<h3>Git recovery</h3><div data-recovery-content></div><p role="status"></p><div class="actions"><button type="button" data-recovery-apply disabled>Start recovery</button><button type="button" data-recovery-refresh>Refresh</button><button type="button" data-recovery-close>Close</button></div>`;
    document.body.append(dialog);
    dialog.addEventListener("close", () => dialog.remove(), {once: true});
    dialog.querySelector("[data-recovery-close]").onclick = () => dialog.close();
    const content = dialog.querySelector("[data-recovery-content]");
    const status = dialog.querySelector('[role="status"]');
    const apply = dialog.querySelector("[data-recovery-apply]");
    const refresh = dialog.querySelector("[data-recovery-refresh]");
    let preview = null, sequence = 0, busy = false, requestId = crypto.randomUUID();
    async function load() {
      if (busy) return;
      const current = ++sequence;
      apply.disabled = true;
      status.textContent = "Checking recovery evidence…";
      try {
        const response = await peers.access.teamFetch(endpoint, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({action: "preview"})});
        if (!response.ok) throw new Error(await responseText(response));
        const body = await response.json();
        if (!dialog.open || current !== sequence) return;
        preview = body.preview;
        requestId = crypto.randomUUID();
        const base = preview.inputs?.base_commit_sha || "Unresolved";
        const candidate = preview.renewed_base_commit_sha;
        const points = preview.git_points || {};
        const additionalPoints = [["Composed starting commit", points.composed_start], ["Candidate commit", points.candidate], ["Observed publication destination", points.observed_destination]].filter(([, sha]) => sha).map(([label, sha]) => `<dt>${label}</dt><dd>${escapeHtml(sha)}</dd>`).join("");
        const sources = preview.reintegration?.contributions || preview.inputs?.sources || [];
        content.innerHTML = `<p>${escapeHtml(text(preview.cause).replaceAll("_", " "))}</p><dl><dt>Original baseline</dt><dd>${escapeHtml(base)}</dd>${candidate ? `<dt>Proposed baseline</dt><dd>${escapeHtml(candidate)}</dd>` : ""}${additionalPoints}<dt>Required contributions</dt><dd>${escapeHtml(sources.map((row) => `${row.id || row.deliverable_id}: ${row.git_refs?.commit_sha || "unresolved"}`).join(", ") || "None")}</dd><dt>Retained preparation attempts</dt><dd>${(preview.preserved_attempt_ids || []).length}</dd><dt>Preparation retries / reviewed generations</dt><dd>${preview.preparation_retries} / ${preview.candidate_generations}</dd></dl>`;
        const action = preview.actions?.[0];
        apply.textContent = action ? text(action.id).replaceAll("_", " ") : "Waiting";
        apply.disabled = !action;
        status.textContent = preview.waiting_reason || preview.continuation_waiting_reason || (action?.requires_review ? "Creates new work requiring the configured review and verification." : action?.id === "reprepare" ? "Recreates preparation from the same verified inputs. Existing work remains retained." : "Continues recovery with the assigned project agent.");
      } catch (error) { if (dialog.open && current === sequence) status.textContent = error.message; }
    }
    refresh.onclick = () => void load();
    apply.onclick = async () => {
      if (busy || !preview?.actions?.length) return;
      busy = true; apply.disabled = true; refresh.disabled = true;
      try {
        const response = await peers.access.teamFetch(endpoint, {method: "POST", headers: {"Content-Type": "application/json", "X-Team-Addon-Response-Scope": "view"}, body: JSON.stringify({action: "apply", action_id: preview.actions[0].id, preview_token: preview.preview_token, request_id: requestId})});
        if (!response.ok) throw new Error(await responseText(response));
        await peers.mutations.applyMutationResultPayload(await response.json());
        dialog.close();
        peers.app.setStatus("Recovery started. Completion is recorded when the affected work resumes.", "ok");
      } catch (error) { status.textContent = `${error.message} Retry the same request, or refresh changed evidence.`; }
      finally { busy = false; apply.disabled = false; refresh.disabled = false; }
    };
    dialog.showModal();
    await load();
  }

  function renderRecordBlockers(sourceKind, sourceId, taskId = null) {
    const blockers = activeBlockersForRecord(sourceKind, sourceId, taskId);
    if (!blockers.length) {
      return "";
    }
    return blockers
      .map((blocker) => `
        <div class="meta-line team-blocker-line">
          <span>${escapeHtml(blockerRoutingLabel(blocker))}</span>
          ${renderGitRecoveryAction(blocker)}
          ${renderTerminalBlockerResolveAction(blocker)}
        </div>
      `)
      .join("");
  }

  async function resolveTerminalBlocker(button, blockerId, note) {
    if (!peers.access.guardTeamMutation("resolve terminal blockers")) {
      return;
    }
    const normalizedBlockerId = text(blockerId);
    if (!normalizedBlockerId) {
      peers.app.setStatus("No blocker selected.", "error");
      return;
    }
    const drafts = peers.drafts.captureFormDrafts();
    button.disabled = true;
    peers.app.setStatus(`Resolving blocker ${normalizedBlockerId}.`, "loading");
    const endpoint = `/blockers/${encodeURIComponent(normalizedBlockerId)}/resolve`;
    try {
      const response = await peers.access.teamFetch(`${API_BASE}${endpoint}`, {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
          "X-Team-Addon-Response-Scope": "view",
        },
        body: JSON.stringify(
          peers.mutations.mutationBodyWithConflictMetadata(endpoint, "POST", { note }),
        ),
      });

      if (response.status === 409) {
        const conflict = await response.json().catch(() => ({}));
        await peers.app.reloadActiveTeamView({ preserveForms: true });
        peers.drafts.restoreFormDrafts(drafts);
        const current = conflict.current_revision ?? peers.app.state.revision;
        peers.app.setStatus(
          `Revision conflict. Reloaded revision ${current}; verify the blocker and resolve again.`,
          "warning",
        );
        return;
      }
      if (!response.ok) {
        throw new Error(await responseText(response));
      }

      await peers.mutations.applyMutationResultPayload(await response.json());
      peers.drafts.restoreFormDrafts(drafts);
      peers.app.setStatus(`Resolved blocker ${normalizedBlockerId}.`, "ok");
    } catch (error) {
      peers.app.setStatus(error instanceof Error ? error.message : "Blocker resolution failed.", "error");
    } finally {
      if (button.isConnected) {
        button.disabled = false;
      }
    }
  }

  async function showRetainedVerificationLog(button) {
    button.disabled = true;
    try {
      const response = await peers.access.teamFetch(`${API_BASE}/release-packages/${encodeURIComponent(button.dataset.releaseId)}/verification-live?job_id=${encodeURIComponent(button.dataset.retainedVerifierJob)}`);
      if (!response.ok) throw new Error(await responseText(response));
      const body = await response.json();
      const pre = document.createElement("pre");
      pre.textContent = body.log.text;
      button.replaceWith(pre);
    } catch (error) {
      peers.app.setStatus(error.message, "error");
      button.disabled = false;
    }
  }

  async function previewCaseReintegration(caseId) {
    if (!peers.access.guardTeamMutation("reintegrate accepted work")) return;
    try {
      const response = await peers.access.teamFetch(`${API_BASE}/cases/${encodeURIComponent(caseId)}/reintegration-candidates`);
      if (!response.ok) throw new Error(await responseText(response));
      const { candidates } = await response.json();
      if (!candidates.length) throw new Error("This case has no current accepted release contributions to reintegrate.");
      if (candidates.length === 1) return previewReleaseReintegration(candidates[0].release_package_id);
      const dialog = document.createElement("dialog");
      dialog.className = "team-reintegration-dialog";
      dialog.innerHTML = `<h3>Choose accepted work from this case</h3><p>Each option shows the full release scope, including any contributions from other cases.</p><ul>${candidates.map((row, index) => `<li><button type="button" data-choose-reintegration-release="${index}">${escapeHtml(row.contribution_titles.join(", "))} — ${escapeHtml(humanizeToken(row.state))}, ${escapeHtml(row.created_at)}</button></li>`).join("")}</ul><button type="button" data-close-preview>Close</button>`;
      document.body.append(dialog);
      dialog.addEventListener("close", () => dialog.remove(), { once: true });
      dialog.querySelector("[data-close-preview]").onclick = () => dialog.close();
      dialog.querySelectorAll("[data-choose-reintegration-release]").forEach(button => {
        button.onclick = () => {
          dialog.close();
          void previewReleaseReintegration(candidates[Number(button.dataset.chooseReintegrationRelease)].release_package_id);
        };
      });
      dialog.showModal();
    } catch (error) { peers.app.setStatus(error.message, "error"); }
  }

  async function previewReleaseReintegration(releaseId) {
    if (!peers.access.guardTeamMutation("reintegrate accepted work")) return;
    const endpoint = `${API_BASE}/release-packages/${encodeURIComponent(releaseId)}/reintegration`;
    const dialog = document.createElement("dialog");
    dialog.className = "team-reintegration-dialog";
    dialog.innerHTML = `<h3>Reintegrate accepted work</h3><div data-preview-content>Refreshing the configured base…</div><p role="status" data-preview-status></p><div class="actions"><button type="button" data-submit-preview disabled>Create integration task</button><button type="button" data-refresh-preview>Refresh preview</button><button type="button" data-close-preview>Close</button></div>`;
    document.body.append(dialog);
    dialog.addEventListener("close", () => dialog.remove(), { once: true });
    dialog.querySelector("[data-close-preview]").onclick = () => dialog.close();
    dialog.showModal();
    let preview = null;
    let previewSequence = 0;
    let submitting = false;
    let requestId = crypto.randomUUID();
    const submit = dialog.querySelector("[data-submit-preview]");
    const status = dialog.querySelector("[data-preview-status]");
    const refresh = dialog.querySelector("[data-refresh-preview]");
    refresh.onclick = () => void load(preview?.owner_agent_id || null);
    async function load(ownerId = null) {
      if (submitting) return;
      const sequence = ++previewSequence;
      submit.disabled = true;
      status.textContent = "Refreshing preview…";
      try {
        const response = await peers.access.teamFetch(endpoint, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ action: "preview", owner_agent_id: ownerId }) });
        if (!response.ok) throw new Error(await responseText(response));
        const nextPreview = (await response.json()).preview;
        if (sequence !== previewSequence || !dialog.open) return;
        preview = nextPreview;
        requestId = crypto.randomUUID();
        dialog.querySelector("[data-preview-content]").innerHTML = `
          <p>Base: <strong>${escapeHtml(preview.base_branch)}</strong> at ${escapeHtml(preview.base_commit_sha.slice(0, 12))}</p>
          <ul>${preview.contributions.map(row => `<li>${escapeHtml(row.title)}</li>`).join("")}</ul>
          <label>Integration owner <select data-preview-owner><option value="">Select an owner</option>${preview.eligible_owners.map(owner => `<option value="${escapeAttr(owner.id)}" ${owner.id === preview.owner_agent_id ? "selected" : ""}>${escapeHtml(owner.name)}</option>`).join("")}</select></label>
          <p>${escapeHtml(preview.instructions)}</p>
          <p>${preview.retire_source_release ? "The source release will be retired with its exclusively owned pending correction work." : "The source release and existing work will be retained."}</p>
          ${preview.retiring_task_ids.length ? `<ul>${preview.retiring_task_ids.map(id => `<li>${escapeHtml(peers.app.state.tasks?.[id]?.title || "Source release correction")}</li>`).join("")}</ul>` : ""}
          <p>Existing review and release policies apply to the new integration.</p>`;
        dialog.querySelector("[data-preview-owner]").onchange = event => void load(event.target.value || null);
        submit.disabled = !preview.owner_agent_id;
        status.textContent = "Ready to create the integration task.";
      } catch (error) {
        if (sequence === previewSequence && dialog.open) status.textContent = error.message;
      }
    }
    submit.onclick = async () => {
      if (submitting || !preview?.owner_agent_id) return;
      submitting = true;
      submit.disabled = true;
      refresh.disabled = true;
      dialog.querySelector("[data-preview-owner]").disabled = true;
      status.textContent = "Creating integration task…";
      try {
        const response = await peers.access.teamFetch(endpoint, { method: "POST", headers: { "Content-Type": "application/json", "X-Team-Addon-Response-Scope": "view" }, body: JSON.stringify({ action: "submit", preview, request_id: requestId }) });
        if (!response.ok) throw new Error(await responseText(response));
        await peers.mutations.applyMutationResultPayload(await response.json());
        dialog.close();
        peers.app.setStatus("Integration task created. Progress appears with the project's work.", "ok");
      } catch (error) {
        status.textContent = `${error.message} Retry this request, or refresh to review a new preview.`;
        submit.disabled = false;
      } finally {
        submitting = false;
        refresh.disabled = false;
        const owner = dialog.querySelector("[data-preview-owner]");
        if (owner) owner.disabled = false;
      }
    };
    await load();
  }

  async function startDependencyCompositionRecovery(button, blockerId, resolverAgentId) {
    if (!peers.access.guardTeamMutation("start dependency composition recoveries")) {
      return;
    }
    const normalizedBlockerId = text(blockerId);
    const normalizedResolverId = text(resolverAgentId);
    if (!normalizedBlockerId || !normalizedResolverId) {
      peers.app.setStatus("A blocker and resolver agent are required.", "error");
      return;
    }
    const drafts = peers.drafts.captureFormDrafts();
    button.disabled = true;
    peers.app.setStatus(`Creating merge recovery for ${normalizedBlockerId}.`, "loading");
    const endpoint = `/blockers/${encodeURIComponent(normalizedBlockerId)}/dependency-composition-recovery`;
    try {
      const response = await peers.access.teamFetch(`${API_BASE}${endpoint}`, {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
          "X-Team-Addon-Response-Scope": "view",
        },
        body: JSON.stringify(
          peers.mutations.mutationBodyWithConflictMetadata(endpoint, "POST", {
            resolver_agent_id: normalizedResolverId,
          }),
        ),
      });
      if (response.status === 409) {
        const conflict = await response.json().catch(() => ({}));
        await peers.app.reloadActiveTeamView({ preserveForms: true });
        peers.drafts.restoreFormDrafts(drafts);
        peers.app.setStatus(
          `Revision conflict. Reloaded revision ${conflict.current_revision ?? peers.app.state.revision}; verify the recovery and retry.`,
          "warning",
        );
        return;
      }
      if (!response.ok) {
        throw new Error(await responseText(response));
      }
      const body = await response.json();
      await peers.mutations.applyMutationResultPayload(body);
      peers.drafts.restoreFormDrafts(drafts);
      const taskId = text(body.dependency_composition_recovery?.recovery_task_id);
      peers.app.setStatus(
        taskId
          ? `Created dependency merge recovery task ${taskId}.`
          : `Started dependency merge recovery for ${normalizedBlockerId}.`,
        "ok",
      );
    } catch (error) {
      peers.app.setStatus(
        error instanceof Error ? error.message : "Dependency merge recovery failed.",
        "error",
      );
    } finally {
      if (button.isConnected) {
        button.disabled = false;
      }
    }
  }

  async function recoverCandidateCorrectionSuccessor(
    button,
    blockerId,
    action,
    reason = null,
    instruction = null,
  ) {
    if (!peers.access.guardTeamMutation("recover candidate correction successors")) {
      return;
    }
    const normalizedBlockerId = text(blockerId);
    const normalizedAction = text(action);
    if (!normalizedBlockerId || !normalizedAction) {
      peers.app.setStatus("No candidate-correction recovery action was selected.", "error");
      return;
    }
    const drafts = peers.drafts.captureFormDrafts();
    button.disabled = true;
    peers.app.setStatus(`${humanizeToken(normalizedAction)} for ${normalizedBlockerId}.`, "loading");
    const endpoint = `/candidate-correction-recoveries/${encodeURIComponent(normalizedBlockerId)}`;
    try {
      const payload = { action: normalizedAction };
      if (text(reason)) {
        payload.reason = text(reason);
      }
      if (text(instruction)) {
        payload.instruction = text(instruction);
      }
      const response = await peers.access.teamFetch(`${API_BASE}${endpoint}`, {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
          "X-Team-Addon-Response-Scope": "view",
        },
        body: JSON.stringify(
          peers.mutations.mutationBodyWithConflictMetadata(endpoint, "POST", payload),
        ),
      });
      if (response.status === 409) {
        const conflict = await response.json().catch(() => ({}));
        await peers.app.reloadActiveTeamView({ preserveForms: true });
        peers.drafts.restoreFormDrafts(drafts);
        peers.app.setStatus(
          `Revision conflict. Reloaded revision ${conflict.current_revision ?? peers.app.state.revision}; verify the recovery and retry.`,
          "warning",
        );
        return;
      }
      if (!response.ok) {
        throw new Error(await responseText(response));
      }
      const body = await response.json();
      await peers.mutations.applyMutationResultPayload(body);
      peers.drafts.restoreFormDrafts(drafts);
      const recovery = body.candidate_correction_recovery || {};
      const recoveryTaskId = text(
        recovery.created_recovery_task_id ||
        recovery.existing_recovery_task_id ||
        recovery.recovery_task_id,
      );
      const releaseId = text(
        recovery.release_package_id ||
        recovery.created_successor_release_package_ids?.[0] ||
        recovery.existing_successor_release_package_ids?.[0],
      );
      peers.app.setStatus(
        releaseId
          ? `Candidate recovery created or found successor ${releaseId}.`
          : recoveryTaskId
            ? `Candidate recovery created or found pinned task ${recoveryTaskId}.`
            : `Candidate recovery ${humanizeToken(normalizedAction)} completed.`,
        "ok",
      );
    } catch (error) {
      peers.app.setStatus(
        error instanceof Error ? error.message : "Candidate correction recovery failed.",
        "error",
      );
    } finally {
      if (button.isConnected) {
        button.disabled = false;
      }
    }
  }

  async function resumeReleaseRecovery(button, blockerId, taskId) {
    if (!peers.access.guardTeamMutation("resume release recoveries")) {
      return;
    }
    const blocker = peers.app.state.blockers?.[text(blockerId)];
    const recoveryTask = peers.app.state.tasks?.[text(taskId)];
    if (
      !blocker ||
      text(blocker.state) !== "open" ||
      text(blocker.source_kind) !== "release_package" ||
      !["governance", "operator"].includes(text(blocker.route))
    ) {
      peers.app.setStatus("This release blocker is no longer eligible for operator recovery.", "error");
      return;
    }
    if (
      !recoveryTask ||
      text(recoveryTask.created_by_actor) !== "system:git-merge-recovery" ||
      text(recoveryTask.owner_agent_id) === "" ||
      text(recoveryTask.project_id) === ""
    ) {
      peers.app.setStatus("The release blocker has no active merge-recovery task to resume.", "error");
      return;
    }
    const drafts = peers.drafts.captureFormDrafts();
    button.disabled = true;
    const releaseId = text(blocker.source_id);
    peers.app.setStatus(`Queuing release recovery ${recoveryTask.id}.`, "loading");
    const endpoint = "/operator-directives";
    try {
      const response = await peers.access.teamFetch(`${API_BASE}${endpoint}`, {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
          "X-Team-Addon-Response-Scope": "view",
        },
        body: JSON.stringify(
          peers.mutations.mutationBodyWithConflictMetadata(endpoint, "POST", {
            team_id: blocker.team_id,
            recipient_agent_id: recoveryTask.owner_agent_id,
            project_id: recoveryTask.project_id,
            task_id: recoveryTask.id,
            title: `Resume release recovery for ${releaseId}`,
            message: (
              `Resume the existing recovery task ${recoveryTask.id} for release ${releaseId}. ` +
              "The control-plane schema is current. Reuse the already resolved agents/... ref when valid, " +
              "then call team_recover_release with its exact commit SHA. Do not create a replacement release package."
            ),
            source: "admin_ui_release_recovery",
            source_message_id: `release-recovery:${blocker.id}:${recoveryTask.id}`,
            created_by: "operator:admin-ui",
          }),
        ),
      });

      if (response.status === 409) {
        const conflict = await response.json().catch(() => ({}));
        await peers.app.reloadActiveTeamView({ preserveForms: true });
        peers.drafts.restoreFormDrafts(drafts);
        const current = conflict.current_revision ?? peers.app.state.revision;
        peers.app.setStatus(
          `Revision conflict. Reloaded revision ${current}; verify the release blocker and resume again.`,
          "warning",
        );
        return;
      }
      if (!response.ok) {
        throw new Error(await responseText(response));
      }

      await peers.mutations.applyMutationResultPayload(await response.json());
      peers.drafts.restoreFormDrafts(drafts);
      peers.app.setStatus(`Queued release recovery ${recoveryTask.id} for its assigned agent.`, "ok");
    } catch (error) {
      peers.app.setStatus(error instanceof Error ? error.message : "Release recovery could not be resumed.", "error");
    } finally {
      if (button.isConnected) {
        button.disabled = false;
      }
    }
  }
  function handleRecoveryPreviewClick(event) {
    const gitRecoveryButton = event.target.closest("[data-git-recovery-blocker]");
    if (gitRecoveryButton) {
      event.preventDefault();
      void previewGitRecovery(gitRecoveryButton.dataset.gitRecoveryBlocker);
      return true;
    }
    const reintegrationButton = event.target.closest("[data-reintegrate-release-id]");
    if (reintegrationButton) {
      event.preventDefault();
      void previewReleaseReintegration(reintegrationButton.dataset.reintegrateReleaseId);
      return true;
    }
    const caseReintegrationButton = event.target.closest("[data-reintegrate-case-id]");
    if (caseReintegrationButton) {
      event.preventDefault();
      void previewCaseReintegration(caseReintegrationButton.dataset.reintegrateCaseId);
      return true;
    }
    const retainedLogButton = event.target.closest("[data-retained-verifier-job]");
    if (retainedLogButton) {
      event.preventDefault();
      void showRetainedVerificationLog(retainedLogButton);
      return true;
    }
    return false;
  }
  function handleRecoveryActionsClick(event) {
    const resumeReleaseRecoveryButton = event.target.closest(
      "[data-resume-release-recovery-blocker-id]",
    );
    if (resumeReleaseRecoveryButton) {
      event.preventDefault();
      if (!peers.access.guardTeamMutation("resume release recoveries")) {
        return true;
      }
      void resumeReleaseRecovery(
        resumeReleaseRecoveryButton,
        resumeReleaseRecoveryButton.dataset.resumeReleaseRecoveryBlockerId,
        resumeReleaseRecoveryButton.dataset.resumeReleaseRecoveryTaskId,
      );
      return true;
    }

    const candidateCorrectionRecoveryTaskButton = event.target.closest(
      "[data-candidate-correction-recovery-task-id]",
    );
    if (candidateCorrectionRecoveryTaskButton) {
      event.preventDefault();
      peers.board.openCandidateCorrectionRecoveryTask(
        candidateCorrectionRecoveryTaskButton.dataset.candidateCorrectionRecoveryTaskId,
      );
      return true;
    }

    const candidateCorrectionRecoveryButton = event.target.closest(
      "[data-candidate-correction-recovery-id]",
    );
    if (candidateCorrectionRecoveryButton) {
      event.preventDefault();
      const action = text(
        candidateCorrectionRecoveryButton.dataset.candidateCorrectionRecoveryAction,
      );
      let reason = null;
      let instruction = null;
      if (action === "create_verified_replacement") {
        instruction = window.prompt(
          "Add instructions for the verified replacement task (optional). These become part of its durable task scope.",
          "",
        );
        if (instruction === null) {
          return true;
        }
      }
      void recoverCandidateCorrectionSuccessor(
        candidateCorrectionRecoveryButton,
        candidateCorrectionRecoveryButton.dataset.candidateCorrectionRecoveryId,
        action,
        text(reason) || null,
        text(instruction) || null,
      );
      return true;
    }

    const dependencyCompositionTaskButton = event.target.closest(
      "[data-dependency-composition-recovery-task-id]",
    );
    if (dependencyCompositionTaskButton) {
      event.preventDefault();
      peers.board.openCandidateCorrectionRecoveryTask(
        dependencyCompositionTaskButton.dataset.dependencyCompositionRecoveryTaskId,
      );
      return true;
    }

    const dependencyCompositionStartButton = event.target.closest(
      "[data-start-dependency-composition-recovery-id]",
    );
    if (dependencyCompositionStartButton) {
      event.preventDefault();
      const container = dependencyCompositionStartButton.closest(
        ".dependency-composition-recovery-status",
      );
      const resolverAgentId = text(
        container?.querySelector("[data-dependency-composition-resolver]")?.value,
      );
      if (!resolverAgentId) {
        peers.app.setStatus("Select an active project agent to resolve the merge.", "error");
        return true;
      }
      void startDependencyCompositionRecovery(
        dependencyCompositionStartButton,
        dependencyCompositionStartButton.dataset.startDependencyCompositionRecoveryId,
        resolverAgentId,
      );
      return true;
    }

    const resolveBlockerButton = event.target.closest("[data-resolve-terminal-blocker-id]");
    if (resolveBlockerButton) {
      event.preventDefault();
      if (!peers.access.guardTeamMutation("resolve terminal blockers")) {
        return true;
      }
      const note = window.prompt(
        "Why is this blocker resolved? Include the evidence or external change that cleared it.",
        "The blocking condition has been verified as resolved.",
      );
      if (!text(note)) {
        peers.app.setStatus("A blocker resolution explanation is required.", "error");
        return true;
      }
      void resolveTerminalBlocker(
        resolveBlockerButton,
        resolveBlockerButton.dataset.resolveTerminalBlockerId,
        text(note),
      );
      return true;
    }
    return false;
  }

  return {
    previewGitRecovery,
    previewReleaseReintegration,
    activeBlockersForRecord,
    blockerRoutingLabel,
    handleRecoveryPreviewClick,
    handleRecoveryActionsClick,
    inheritedTaskBlockers,
    latestDispatchForReview,
    latestTaskPreparationInvalidation,
    releaseRecoveryTaskForBlocker,
    renderGitRecoveryAction,
    renderPlanningExecutionStatus,
    renderRecordBlockers,
    renderTerminalBlockerResolveAction,
  };
}

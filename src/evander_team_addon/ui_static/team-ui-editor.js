// Record and task cancellation dialogs, edit fields, and edit payloads.
import {
  byPriority,
  escapeAttr,
  escapeHtml,
  formData,
  goalsToText,
  numberOrNull,
  numberOrZero,
  parseGoals,
  splitList,
  taskStateLabel,
  text,
  uniqueTextList,
  values,
} from "./team-ui-format.js";
import { elements } from "./team-ui-dom.js";
import { avatarStylePresets, directlyEditableTaskStates, noTeamRecordId, terminalTaskStates } from "./team-ui-config.js";

export function createTeamEditorController(peers) {

  function openTaskCancellationDialog(taskId) {
    if (!peers.access.guardTeamMutation("cancel tasks")) {
      return;
    }
    const task = peers.app.state.tasks?.[text(taskId)] || null;
    const availability = peers.tasks.taskCancellationAvailability(task);
    if (!task || !availability.allowed) {
      peers.app.setStatus(availability.explanation, "warning");
      return;
    }
    const form = elements.taskCancellationForm;
    const modal = elements.taskCancellationModal;
    if (!form || !modal) {
      peers.app.setStatus("The task cancellation dialog is unavailable.", "error");
      return;
    }

    form.reset();
    form.dataset.taskId = task.id;
    if (elements.taskCancellationModalTitle) {
      elements.taskCancellationModalTitle.textContent = `Cancel ${task.title}`;
    }
    if (elements.taskCancellationSubject) {
      elements.taskCancellationSubject.textContent =
        `${task.title} · ${taskStateLabel(task)} · owner ${peers.board.taskAssignmentLabel(task)}`;
    }
    const successorSelect = form.elements.successor_task_id;
    if (successorSelect instanceof HTMLSelectElement) {
      const successorOptions = values(peers.app.state.tasks)
        .filter(
          (candidate) =>
            candidate.id !== task.id &&
            text(candidate.project_id) === text(task.project_id),
        )
        .sort(byPriority)
        .map(
          (candidate) =>
            `<option value="${escapeAttr(candidate.id)}">${escapeHtml(candidate.title)} · ${escapeHtml(taskStateLabel(candidate))}</option>`,
        )
        .join("");
      successorSelect.innerHTML = `<option value="">No successor</option>${successorOptions}`;
    }
    modal.hidden = false;
    form.elements.reason_code?.focus();
  }

  function closeTaskCancellationDialog() {
    const form = elements.taskCancellationForm;
    if (elements.taskCancellationModal) {
      elements.taskCancellationModal.hidden = true;
    }
    if (elements.taskCancellationModalTitle) {
      elements.taskCancellationModalTitle.textContent = "Cancel task";
    }
    if (elements.taskCancellationSubject) {
      elements.taskCancellationSubject.textContent = "";
    }
    if (form) {
      peers.drafts.clearFormDraft(form);
      form.reset();
      form.dataset.taskId = "";
    }
  }

  async function submitTaskCancellation(form) {
    if (!peers.access.guardTeamMutation("cancel tasks")) {
      return;
    }
    const taskId = text(form?.dataset?.taskId);
    const task = peers.app.state.tasks?.[taskId] || null;
    const availability = peers.tasks.taskCancellationAvailability(task);
    if (!task || !availability.allowed) {
      peers.app.setStatus(availability.explanation, "warning");
      return;
    }
    const data = formData(form);
    const successorTaskId = text(data.successor_task_id) || null;
    const rebindDependents = Boolean(data.rebind_dependents);
    if (rebindDependents && !successorTaskId) {
      peers.app.setStatus("Select a successor before replacing active dependencies.", "warning");
      return;
    }
    const expectedDependentTaskIds = values(peers.app.state.tasks)
      .filter((candidate) => {
        if (terminalTaskStates.has(candidate.state)) return false;
        if ((candidate.dependency_task_ids || []).includes(task.id)) return true;
        return Boolean(
          candidate.integration_boundary
          && (candidate.deliverable_contract?.required_contribution_task_ids || [])
            .includes(task.id),
        );
      })
      .map((candidate) => candidate.id)
      .sort();
    const payload = {
      reason_code: text(data.reason_code),
      reason: text(data.reason),
      successor_task_id: successorTaskId,
      rebind_dependents: rebindDependents,
    };
    if (rebindDependents) {
      payload.expected_dependent_task_ids = expectedDependentTaskIds;
    }
    const saved = await peers.mutations.submitMutation(
      form,
      `/tasks/${encodeURIComponent(task.id)}/cancel`,
      "POST",
      payload,
      { resetOnSuccess: false },
    );
    if (saved) {
      closeTaskCancellationDialog();
    }
  }

  function openEditDialog(type, id) {
    if (!peers.access.guardTeamMutation("edit Team records")) {
      return;
    }
    const record = recordForEdit(type, id);
    if (!record || !elements.editModal || !elements.editForm || !elements.editFormBody) {
      peers.app.setStatus("Record is no longer available.", "error");
      return;
    }

    elements.editForm.dataset.editType = type;
    elements.editForm.dataset.editId = id;
    elements.editModalTitle.textContent = editDialogTitle(type, record);
    elements.editFormBody.innerHTML = renderEditFields(type, record);
    peers.selects.fillSelectsIn(elements.editFormBody);
    peers.tooltips.decorateFieldTooltips(elements.editFormBody);
    if (type === "governance-group") {
      peers.governance.syncGovernanceMemberTeamEditor(
        elements.editFormBody.querySelector("[data-governance-member-teams-editor]"),
      );
      peers.governance.syncGovernanceGoalEditor(
        elements.editFormBody.querySelector("[data-governance-goals-editor]"),
      );
    }
    if (type === "project") {
      peers.policy.syncHumanReviewChecklistEditor(
        elements.editFormBody.querySelector("[data-human-review-checklist-editor]"),
      );
    }
    elements.editModal.hidden = false;
    const firstEditableField = elements.editFormBody.querySelector(
      "input:not([readonly]), select, textarea",
    );
    firstEditableField?.focus();
  }

  function closeEditDialog() {
    if (!elements.editModal || !elements.editForm || !elements.editFormBody) {
      return;
    }
    elements.editModal.hidden = true;
    elements.editForm.dataset.editType = "";
    elements.editForm.dataset.editId = "";
    elements.editFormBody.innerHTML = "";
  }

  async function submitEditForm(form) {
    if (!peers.access.guardTeamMutation("save changes")) {
      return;
    }
    const type = text(form.dataset.editType);
    const id = text(form.dataset.editId);
    if (!type || !id) {
      peers.app.setStatus("No edit record selected.", "error");
      return;
    }
    const endpoint = editEndpoint(type, id);
    if (type === "governance-group" && !peers.governance.validateGovernanceGroupEditor(form)) {
      return;
    }
    if (type === "project" && !peers.policy.validateHumanReviewPolicyEditor(form)) {
      return;
    }
    const payload = editPayloadForForm(type, form);
    const saved = await peers.mutations.submitMutation(form, endpoint, "PATCH", payload, {
      resetOnSuccess: false,
    });
    if (saved) {
      if (type === "task") {
        peers.tasks.showTaskPreparationInvalidationStatus(id);
      }
      peers.drafts.clearFormDraft(form);
      closeEditDialog();
    } else if (type === "governance-group") {
      peers.governance.syncGovernanceGoalEditor(
        form.querySelector("[data-governance-goals-editor]"),
      );
    }
  }

  function recordForEdit(type, id) {
    if (type === "team") return peers.app.state.teams[id] || null;
    if (type === "governance-group") return peers.app.state.governance_groups[id] || null;
    if (type === "project") return peers.app.state.projects[id] || null;
    if (type === "role") return peers.app.state.roles[id] || null;
    if (type === "agent") return peers.app.state.agents[id] || null;
    if (type === "case") return peers.app.state.cases[id] || null;
    if (type === "task") return peers.app.state.tasks[id] || null;
    if (type === "review-profile") return peers.app.state.review_profiles[id] || null;
    if (type === "outcome-kind") return peers.app.state.outcome_kind_definitions[id] || null;
    return null;
  }

  function editEndpoint(type, id) {
    if (type === "team") return `/teams/${encodeURIComponent(id)}`;
    if (type === "governance-group") return `/governance-groups/${encodeURIComponent(id)}`;
    if (type === "project") return `/projects/${encodeURIComponent(id)}`;
    if (type === "role") return `/roles/${encodeURIComponent(id)}`;
    if (type === "agent") return `/agents/${encodeURIComponent(id)}`;
    if (type === "case") return `/cases/${encodeURIComponent(id)}`;
    if (type === "task") return `/tasks/${encodeURIComponent(id)}`;
    if (type === "review-profile") return `/review-profiles/${encodeURIComponent(id)}`;
    if (type === "outcome-kind") return `/outcome-kinds/${encodeURIComponent(id)}`;
    throw new Error(`Unknown edit type ${type}`);
  }

  function editDialogTitle(type, record) {
    const labels = {
      team: "team",
      "governance-group": "governance group",
      project: "project",
      role: "role",
      agent: "agent",
      case: "case",
      task: "task",
      "review-profile": "review profile",
      "outcome-kind": "outcome kind",
    };
    if (type === "governance-group") {
      return `Edit ${text(record.name) || "governance group"}`;
    }
    return `Edit ${labels[type] || "record"} ${record.id || ""}`.trim();
  }

  function renderEditFields(type, record) {
    if (type === "team") {
      return `
        ${readonlyField("Team ID", record.id)}
        ${textField("Name", "name", record.name, { required: true })}
        ${selectField("Manager", "manager_agent_id", "agents", record.manager_agent_id || "", { required: true })}
        ${selectField("Operator communication target", "operator_communication_target_id", "communication-targets", record.operator_communication_target_id || "", { placeholder: "No operator target" })}
        ${checkboxField("Escalate manager escalations to governance", "escalate_to_governance", Boolean(record.escalate_to_governance))}
        ${numberField("Estimated max cost USD", "max_cost_usd", record.max_cost_usd ?? "", { min: 0, step: "0.01" })}
        ${numberField("Governance cost warning threshold", "cost_warning_ratio", record.cost_warning_ratio ?? 0.8, { min: 0.01, max: 1, step: "0.01" })}
        ${selectField("Budget renewal", "cost_budget_renewal_mode", "cost-budget-renewal-modes", record.cost_budget_renewal_mode || "manual")}
        ${checkboxField("Archived", "archived", Boolean(record.archived))}
      `;
    }
    if (type === "governance-group") {
      return `
        ${textField("Name", "name", record.name, { required: true })}
        ${selectField("Manager", "manager_agent_id", "governance-manager-agents", record.manager_agent_id || "", { required: true })}
        ${peers.governance.renderGovernanceMemberTeamsEditor(record.member_team_ids || [])}
        ${peers.governance.renderGovernanceGoalsEditor(record.goals || [])}
        ${numberField("Estimated max cost USD", "max_cost_usd", record.max_cost_usd ?? "", { min: 0, step: "0.01" })}
        ${checkboxField("Archived", "archived", Boolean(record.archived))}
        ${peers.reviews.renderTechnicalDetails([
          ["Governance group ID", record.id],
          ["Manager agent ID", record.manager_agent_id],
        ], {
          disclosureKey: peers.disclosures.teamDisclosureKey("edit", "governance-group", record.id, "technical"),
        })}
      `;
    }
    if (type === "project") {
      return `
        ${readonlyField("Project ID", record.id)}
        ${readonlyField("Team", record.team_id)}
        ${textField("Name", "name", record.name, { required: true })}
        ${textField("Directory scope", "directory_path", record.directory_path || "root", { required: true })}
        ${selectField("Status", "status", "project-statuses", record.status || "active", { required: true })}
        ${textareaField("Goals", "goals", goalsToText(record.goals || []), {
          rows: 4,
          placeholder: "id | description | state",
        })}
        <fieldset>
          <legend>Project lead handoff</legend>
          ${selectField("Project lead", "lead_agent_id", "project-lead-agents", record.lead_agent_id || "", { required: true, placeholder: "Select team agent" })}
          <p class="field-help">Transfer authority separately from other project edits. One agent may lead several projects; the former lead remains a project member.</p>
          <div class="record-actions">
            <button type="button" data-transfer-project-lead>Transfer project lead</button>
          </div>
        </fieldset>
        <p class="subheading">Git config</p>
        ${textField("Repository source", "repo_source", "", {
          placeholder: "Git URL or local checkout",
        })}
        ${gitImportControls()}
        ${textField("Managed repository ID", "managed_repo_id", record.git?.managed_repo_id || "", {
          placeholder: "mr_...",
        })}
        ${textField("Default base branch for new tasks", "base_branch", record.git?.base_branch || "main")}
        ${textField("Canonical release branch prefix", "canonical_release_branch_prefix", record.git?.canonical_release_branch_prefix || "")}
        <input type="hidden" name="git_recovery_configuration" value="1" />
        ${checkboxField("Allow agents to recover Git incidents", "git_recovery_enabled", record.git?.recovery?.enabled !== false)}
        ${checkboxField("Allow reviewed renewal on the same base branch", "git_recovery_renewal", record.git?.recovery?.automatic_same_branch_renewal !== false)}
        ${numberField("Preparation retries per work lineage", "git_recovery_retry_limit", record.git?.recovery?.preparation_retry_limit ?? 3, { min: 0, max: 10, required: true })}
        ${numberField("Reviewed recovery generations per work lineage", "git_recovery_candidate_limit", record.git?.recovery?.candidate_generation_limit ?? 3, { min: 0, max: 10, required: true })}
        ${textField("Agent commit author name", "agent_author_name", record.git?.agent_author_identity?.name || "", { placeholder: "Evander Bot" })}
        ${textField("Agent commit author email", "agent_author_email", record.git?.agent_author_identity?.email || "", { placeholder: "evander@quinth.se" })}
        ${textField("Agent commit committer name", "agent_committer_name", record.git?.agent_committer_identity?.name || "", { placeholder: "Evander Bot" })}
        ${textField("Agent commit committer email", "agent_committer_email", record.git?.agent_committer_identity?.email || "", { placeholder: "evander@quinth.se" })}
        <p class="subheading">Release gates</p>
        ${checkboxField("Configure release policy", "release_policy_enabled", Boolean(record.release_policy))}
        ${checkboxField("Automatically publish the release branch to the canonical remote", "automatic_release_branch_publication", record.release_policy?.automatic_release_branch_publication !== false)}
        ${peers.policy.renderReleaseGateSequenceEditor(record.release_policy)}
        ${textareaField("Automated and agent-review policy JSON", "release_policy_json", peers.policy.releasePolicyWithoutHumanJson(record.release_policy), {
          rows: 12,
          placeholder: '{"verification_commands": [...], "review_requirements": [...]}',
        })}
        <p class="field-help">Human review fields are intentionally excluded from the JSON editor and configured only through the bounded controls below.</p>
        ${peers.policy.renderHumanReviewPolicyEditor(record.release_policy?.human_review_requirement)}
      `;
    }
    if (type === "agent") {
      const runtime = peers.app.state.agent_runtimes?.[record.id] || {};
      const contextPolicy = peers.selects.contextClearingPolicyValue(runtime);
      const contextIdleGraceMinutes = peers.selects.contextClearingIdleGraceMinutesForEdit(
        runtime,
      );
      return `
        ${readonlyField("Agent ID", record.id)}
        ${selectField("Team", "team_id", "teams", record.team_id || "", { placeholder: "No team" })}
        ${multiSelectField("Projects", "project_ids", "agent-edit-projects", peers.agents.agentProjectIds(record), { size: 5 })}
        <p class="field-help">Use this list for project memberships. Projects marked “Project lead” stay assigned until you transfer the lead from that project's editor.</p>
        ${selectField("Role", "role_id", "agent-edit-roles", record.role_id || "", { placeholder: "No role" })}
        ${textField("Session ID", "session_id", record.session_id, { required: true })}
        ${textField("Display name", "display_name", record.display_name || "")}
        ${renderAvatarEditControls(record)}
        ${renderVoiceProfileEditControls(record)}
        ${selectField("Reports to", "reports_to_agent_id", "agent-edit-superiors", record.reports_to_agent_id || "", { placeholder: "No superior" })}
        ${numberField("Max ephemeral agents", "max_concurrent", peers.agents.ephemeralCapacity(record), { min: 0 })}
        ${selectField("Status", "status", "agent-statuses", record.status || "active", { required: true })}
        ${selectField("Context clearing", "context_clearing_policy", "context-clearing-policies", contextPolicy, { required: true })}
        <div data-agent-context-idle-grace${contextPolicy === "idle" ? "" : " hidden"}>
          ${numberField("Idle grace minutes", "context_clearing_idle_grace_minutes", contextIdleGraceMinutes, { min: 0, max: 10080, step: "any", required: true })}
        </div>
      `;
    }
    if (type === "role") {
      return `
        ${readonlyField("Role ID", record.id)}
        ${readonlyField("Team", record.team_id)}
        ${selectField("Project", "project_id", "projects", record.project_id || "", { placeholder: "Team-wide" })}
        ${textField("Name", "name", record.name, { required: true })}
        ${textareaField("Description", "description", record.description || "", { rows: 3 })}
        ${multiSelectField("Permissions", "permissions", "role-permissions", record.permissions || [], { size: 3 })}
        ${numberField("Max ephemeral agents", "max_concurrent", record.ephemeral_policy?.max_concurrent ?? 0, { min: 0 })}
        ${checkboxField("Archived", "archived", Boolean(record.archived))}
      `;
    }
    if (type === "case") {
      return `
        ${readonlyField("Case ID", record.id)}
        ${readonlyField("Team", record.team_id)}
        ${selectField("Project", "project_id", "projects", record.project_id || "", { placeholder: "Team-wide" })}
        ${selectField("Owner", "owner_agent_id", "agents", record.owner_agent_id || "", { placeholder: "No owner" })}
        ${selectField("Status", "status", "case-statuses", record.status || "open", { required: true })}
        ${selectField("Priority", "priority", "inbox-priorities", record.priority || "normal", { required: true })}
        ${textField("Title", "title", record.title, { required: true })}
        ${textareaField("Description", "description", record.description || "", { rows: 3 })}
        ${textField("Source", "source", record.source || "")}
        ${textField("Source ref", "source_ref", record.source_ref || "")}
        ${textareaField("Tags", "tags", (record.tags || []).join("\n"), { rows: 2 })}
        ${textareaField("Linked tasks", "task_ids", (record.task_ids || []).join("\n"), { rows: 2 })}
      `;
    }
    if (type === "task") {
      const gitBaseLocked = taskGitBaseBranchLocked(record);
      return `
        ${readonlyField("Task ID", record.id)}
        ${readonlyField("Project", record.project_id)}
        ${readonlyField("Assigned to", peers.board.taskAssignmentLabel(record))}
        ${taskEditStateField(record)}
        ${selectField("Case", "case_id", "cases", record.case_id || "", { placeholder: "No case" })}
        ${numberField("Priority", "priority", record.priority ?? 0, { min: 0, required: true })}
        ${textField("Title", "title", record.title, { required: true })}
        ${textareaField("Description", "description", record.description, { rows: 3, required: true })}
        ${textField("Commit author name", "task_author_name", record.authored_by?.name || "", { placeholder: "Project author" })}
        ${textField("Commit author email", "task_author_email", record.authored_by?.email || "", { placeholder: "Project author" })}
        ${gitBaseLocked
          ? readonlyField("Git base branch (locked)", record.git_base_branch || "Not recorded (legacy task)")
          : textField("Git base branch", "git_base_branch", record.git_base_branch || "", { placeholder: "Inherit project default" })}
        ${selectField("Parent task", "parent_task_id", "tasks", record.parent_task_id || "", { placeholder: "No parent" })}
        ${textareaField("Dependency tasks", "dependency_task_ids", (record.dependency_task_ids || []).join("\n"), { rows: 2 })}
        ${checkboxField("Integration boundary", "integration_boundary", Boolean(record.integration_boundary))}
        ${checkboxField("Require Git provenance", "require_git_provenance", Boolean(record.deliverable_contract?.require_git_provenance))}
        ${textareaField("Required contribution task IDs", "required_contribution_task_ids", (record.deliverable_contract?.required_contribution_task_ids || []).join("\n"), { rows: 2 })}
      `;
    }
    if (type === "review-profile") {
      return `
        ${readonlyField("Profile ID", record.id)}
        ${readonlyField("Team", record.team_id)}
        ${selectField("Project", "project_id", "projects", record.project_id || "", { placeholder: "Team-wide" })}
        ${textField("Name", "name", record.name, { required: true })}
        ${textareaField("Description", "description", record.description || "", { rows: 3 })}
        ${multiSelectField("Applicable roles", "applicable_role_ids", "roles", record.applicable_role_ids || [], { size: 4 })}
        ${multiSelectField("Required outcome kinds", "required_outcome_kinds", "review-profile-edit-outcome-kinds", record.required_outcome_kinds || [], { size: 4 })}
        ${peers.reviews.renderReviewProfileCriteriaEditor(record.criteria || [])}
        ${checkboxField("Archived", "archived", Boolean(record.archived))}
      `;
    }
    if (type === "outcome-kind") {
      return `
        ${readonlyField("Definition ID", record.id)}
        ${readonlyField("Kind", record.kind)}
        ${readonlyField("Team", record.team_id)}
        ${readonlyField("Project", record.project_id || "Team-wide")}
        ${textField("Name", "name", record.name, { required: true })}
        ${textareaField("Description", "description", record.description || "", { rows: 3 })}
        ${textareaField("Reference requirements", "reference_requirements", record.reference_requirements || "", { rows: 3 })}
        ${textareaField("Evidence requirements", "evidence_requirements", record.evidence_requirements || "", { rows: 3 })}
        ${checkboxField("Archived", "archived", Boolean(record.archived))}
      `;
    }
    return "";
  }

  function taskEditStateField(task) {
    const stateValue = text(task?.state) || "pending";
    if (task.owner_agent_id && directlyEditableTaskStates.includes(stateValue)) {
      return selectField(
        "State",
        "state",
        "task-edit-states",
        stateValue,
        { required: true },
      );
    }
    return `
      ${readonlyField("State", taskStateLabel(task))}
      <input type="hidden" name="state" value="${escapeAttr(stateValue)}" />
    `;
  }

  function taskGitBaseBranchLocked(task) {
    const deliverableIds = new Set([
      ...(task?.deliverable_ids || []).map(text).filter(Boolean),
      ...Object.values(peers.app.state.deliverables || {})
        .filter((deliverable) => text(deliverable.task_id) === text(task?.id))
        .map((deliverable) => text(deliverable.id))
        .filter(Boolean),
    ]);
    if (deliverableIds.size) return true;
    if (Object.values(peers.app.state.git_work_contexts || {}).some((context) => text(context.task_id) === text(task?.id))) {
      return true;
    }
    return Object.values(peers.app.state.git_promotions || {}).some((promotion) => (
      text(promotion.task_id) === text(task?.id)
      || deliverableIds.has(text(promotion.deliverable_id))
    ));
  }

  function renderAvatarEditControls(record) {
    const hasAvatar = Boolean(record?.avatar?.asset_id);
    const styleOptions = avatarStylePresets
      .map(([value, label]) => `<option value="${escapeAttr(value)}"${value === "professional" ? " selected" : ""}>${escapeHtml(label)}</option>`)
      .join("");
    return `
      <fieldset class="avatar-editor">
        <legend>Avatar</legend>
        <div class="avatar-editor-current">
          ${peers.agents.renderAgentAvatar(record, "agent-avatar-large")}
          <div>
            <div class="meta-line">${hasAvatar ? `Current avatar - ${escapeHtml(record.avatar.source || "custom")}` : "No avatar set"}</div>
            <div class="record-actions avatar-editor-actions">
              <input type="file" name="avatar_file" accept="image/png,image/jpeg,image/webp" />
              <button class="record-action" type="button" data-avatar-upload-agent-id="${escapeAttr(record.id)}">Upload</button>
              ${hasAvatar ? `<button class="record-action" type="button" data-avatar-remove-agent-id="${escapeAttr(record.id)}">Remove</button>` : ""}
            </div>
          </div>
        </div>
        <div class="avatar-generation-grid">
          <label>Style preset
            <select name="avatar_style_preset">${styleOptions}</select>
          </label>
          <label>Generation notes
            <textarea name="avatar_prompt_note" rows="2" placeholder="Optional visual hints"></textarea>
          </label>
        </div>
        <div class="record-actions avatar-editor-actions">
          <button class="record-action" type="button" data-avatar-generate-agent-id="${escapeAttr(record.id)}">Generate preview</button>
          <button class="record-action" type="button" data-avatar-accept-agent-id="${escapeAttr(record.id)}" disabled>Accept preview</button>
        </div>
        <div class="avatar-preview-slot" data-avatar-preview-slot></div>
      </fieldset>
    `;
  }

  function renderVoiceProfileEditControls(record) {
    const profile = record?.voice_profile || {};
    return `
      <fieldset class="voice-profile-editor">
        <legend>Voice</legend>
        ${selectField("Base voice", "voice", "realtime-voices", profile.voice || "", { placeholder: "Default (marin)" })}
        ${textareaField("Speaking style", "speaking_style", profile.speaking_style || "", {
          rows: 3,
          placeholder: "Calm, concise, lightly upbeat",
        })}
      </fieldset>
    `;
  }

  function editPayloadForForm(type, form) {
    const data = formData(form);
    if (type === "team") {
      return {
        name: text(data.name),
        manager_agent_id: text(data.manager_agent_id),
        operator_communication_target_id: text(data.operator_communication_target_id) || null,
        escalate_to_governance: Boolean(form.elements.escalate_to_governance?.checked),
        max_cost_usd: numberOrNull(data.max_cost_usd),
        cost_warning_ratio: numberOrNull(data.cost_warning_ratio),
        cost_budget_renewal_mode: text(data.cost_budget_renewal_mode) || "manual",
        archived: Boolean(form.elements.archived?.checked),
      };
    }
    if (type === "governance-group") {
      return {
        name: text(data.name),
        manager_agent_id: text(data.manager_agent_id),
        member_team_ids: peers.governance.governanceMemberTeamIdsFromForm(form),
        goals: peers.governance.governanceGoalsFromForm(form),
        max_cost_usd: numberOrNull(data.max_cost_usd),
        archived: Boolean(form.elements.archived?.checked),
      };
    }
    if (type === "project") {
      return {
        name: text(data.name),
        directory_path: text(data.directory_path) || "root",
        status: text(data.status),
        goals: parseGoals(data.goals),
        git: peers.projects.gitConfigForForm(data),
        release_policy: peers.policy.releasePolicyForForm(data, form),
      };
    }
    if (type === "agent") {
      const selectedProjectIds = uniqueTextList(data.project_ids);
      const currentRecord = recordForEdit("agent", text(form.dataset.editId));
      const currentProjectIds = currentRecord ? peers.agents.agentProjectIds(currentRecord) : [];
      const requestedTeamId = text(data.team_id);
      const normalizedTeamId = requestedTeamId === noTeamRecordId ? null : requestedTeamId || null;
      const contextClearingPolicy = text(data.context_clearing_policy) || "off";
      const payload = {
        team_id: normalizedTeamId,
        role_id: text(data.role_id) || null,
        session_id: text(data.session_id),
        reports_to_agent_id: text(data.reports_to_agent_id) || null,
        display_name: text(data.display_name) || null,
        voice_profile: {
          voice: text(data.voice) || null,
          speaking_style: text(data.speaking_style) || null,
        },
        ephemeral_policy: { max_concurrent: numberOrZero(data.max_concurrent) },
        status: text(data.status),
        context_clearing_policy: contextClearingPolicy,
      };
      if (contextClearingPolicy === "idle") {
        payload.context_clearing_idle_grace_seconds = Math.round(
          numberOrZero(data.context_clearing_idle_grace_minutes) * 60,
        );
      }
      if (
        selectedProjectIds.length !== currentProjectIds.length
        || selectedProjectIds.some((projectId) => !currentProjectIds.includes(projectId))
      ) {
        payload.project_ids = selectedProjectIds;
      }
      return payload;
    }
    if (type === "role") {
      return {
        project_id: text(data.project_id) || null,
        name: text(data.name),
        description: text(data.description),
        permissions: splitList(data.permissions),
        ephemeral_policy: { max_concurrent: numberOrZero(data.max_concurrent) },
        archived: Boolean(form.elements.archived?.checked),
      };
    }
    if (type === "case") {
      return {
        project_id: text(data.project_id) || null,
        owner_agent_id: text(data.owner_agent_id) || null,
        status: text(data.status),
        priority: text(data.priority) || "normal",
        title: text(data.title),
        description: text(data.description),
        source: text(data.source) || null,
        source_ref: text(data.source_ref) || null,
        tags: splitList(data.tags),
        task_ids: splitList(data.task_ids),
      };
    }
    if (type === "task") {
      const integrationBoundary = Boolean(
        form.elements.integration_boundary?.checked,
      );
      const requireGitProvenance = Boolean(
        form.elements.require_git_provenance?.checked,
      );
      const requiredContributionTaskIds = splitList(
        data.required_contribution_task_ids,
      );
      const deliverableContract = (
        integrationBoundary
        || requireGitProvenance
        || requiredContributionTaskIds.length > 0
      )
        ? {
            require_git_provenance: requireGitProvenance,
            required_contribution_task_ids: requiredContributionTaskIds,
          }
        : null;
      const payload = {
        state: text(data.state),
        case_id: text(data.case_id) || null,
        priority: numberOrZero(data.priority),
        title: text(data.title),
        description: text(data.description),
        parent_task_id: text(data.parent_task_id) || null,
        dependency_task_ids: splitList(data.dependency_task_ids),
      };
      const currentTask = peers.app.state.tasks?.[text(form.dataset.editId)];
      const authoredBy = peers.tasks.taskAuthoredByForForm(data);
      if (JSON.stringify(authoredBy) !== JSON.stringify(currentTask?.authored_by || null)) {
        payload.authored_by = authoredBy;
      }
      if (form.elements.git_base_branch) {
        const nextGitBaseBranch = text(data.git_base_branch) || null;
        if (!currentTask || nextGitBaseBranch !== (currentTask.git_base_branch || null)) {
          payload.git_base_branch = nextGitBaseBranch;
        }
      }
      const currentContract = currentTask?.deliverable_contract || null;
      if (
        !currentTask
        || Boolean(currentTask.integration_boundary) !== integrationBoundary
        || JSON.stringify(currentContract) !== JSON.stringify(deliverableContract)
      ) {
        payload.integration_boundary = integrationBoundary;
        payload.deliverable_contract = deliverableContract;
      }
      return payload;
    }
    if (type === "review-profile") {
      return {
        project_id: text(data.project_id) || null,
        name: text(data.name),
        description: text(data.description),
        applicable_role_ids: splitList(data.applicable_role_ids),
        required_outcome_kinds: splitList(data.required_outcome_kinds),
        criteria: peers.reviews.reviewProfileCriteriaFromForm(form),
        archived: Boolean(form.elements.archived?.checked),
      };
    }
    if (type === "outcome-kind") {
      return {
        name: text(data.name),
        description: text(data.description),
        reference_requirements: text(data.reference_requirements),
        evidence_requirements: text(data.evidence_requirements),
        archived: Boolean(form.elements.archived?.checked),
      };
    }
    return {};
  }

  function readonlyField(label, value) {
    return `<label>${escapeHtml(label)}<input value="${escapeAttr(value)}" readonly /></label>`;
  }

  function textField(label, name, value, { required = false, placeholder = "" } = {}) {
    return `<label>${escapeHtml(label)}<input name="${escapeAttr(name)}" value="${escapeAttr(value)}"${required ? " required" : ""}${placeholder ? ` placeholder="${escapeAttr(placeholder)}"` : ""} /></label>`;
  }

  function numberField(label, name, value, { min = null, max = null, step = null, required = false } = {}) {
    return `<label>${escapeHtml(label)}<input name="${escapeAttr(name)}" type="number" value="${escapeAttr(value)}"${min !== null ? ` min="${escapeAttr(min)}"` : ""}${max !== null ? ` max="${escapeAttr(max)}"` : ""}${step !== null ? ` step="${escapeAttr(step)}"` : ""}${required ? " required" : ""} /></label>`;
  }

  function textareaField(label, name, value, { rows = 3, required = false, placeholder = "" } = {}) {
    return `<label>${escapeHtml(label)}<textarea name="${escapeAttr(name)}" rows="${escapeAttr(rows)}"${required ? " required" : ""}${placeholder ? ` placeholder="${escapeAttr(placeholder)}"` : ""}>${escapeHtml(value)}</textarea></label>`;
  }

  function selectField(label, name, optionsKey, value, { required = false, placeholder = "Default" } = {}) {
    return `<label>${escapeHtml(label)}<select name="${escapeAttr(name)}" data-options="${escapeAttr(optionsKey)}" data-current-value="${escapeAttr(value)}" data-placeholder="${escapeAttr(placeholder)}"${required ? " required" : ""}></select></label>`;
  }

  function multiSelectField(label, name, optionsKey, values, { size = 4 } = {}) {
    return `<label>${escapeHtml(label)}<select name="${escapeAttr(name)}" multiple size="${escapeAttr(size)}" data-options="${escapeAttr(optionsKey)}" data-current-value="${escapeAttr((values || []).join("\n"))}"></select></label>`;
  }

  function checkboxField(label, name, checked) {
    return `<label class="checkbox-label"><input name="${escapeAttr(name)}" type="checkbox"${checked ? " checked" : ""} /> ${escapeHtml(label)}</label>`;
  }

  function gitImportControls() {
    return `
      <div class="git-import-actions">
        <button type="button" data-git-import-button>Import Git repo</button>
        <span class="meta-line" data-git-import-status></span>
      </div>
    `;
  }

  function syncTaskUpdateForm(form, { keepEditedValues = false } = {}) {
    const taskId = form.elements.task_id?.value;
    const task = taskId ? peers.app.state.tasks[taskId] : null;
    if (!task) {
      return;
    }
    if (!keepEditedValues || !form.elements.state.value) {
      form.elements.state.value = task.state || "";
    }
    if (!keepEditedValues || !form.elements.case_id.value) {
      form.elements.case_id.value = task.case_id || "";
    }
    if (!keepEditedValues || !form.elements.priority.value) {
      form.elements.priority.value = String(task.priority ?? "");
    }
    if (!keepEditedValues || !form.elements.title.value) {
      form.elements.title.value = task.title || "";
    }
    if (!keepEditedValues || !form.elements.description.value) {
      form.elements.description.value = task.description || "";
    }
    if (!keepEditedValues || !form.elements.parent_task_id.value) {
      form.elements.parent_task_id.value = task.parent_task_id || "";
    }
    if (!keepEditedValues || !form.elements.dependency_task_ids.value) {
      form.elements.dependency_task_ids.value = (task.dependency_task_ids || []).join("\n");
    }
    if (
      form.elements.integration_boundary
      && (!keepEditedValues || !form.elements.integration_boundary.checked)
    ) {
      form.elements.integration_boundary.checked = Boolean(
        task.integration_boundary,
      );
    }
    if (
      form.elements.require_git_provenance
      && (!keepEditedValues || !form.elements.require_git_provenance.checked)
    ) {
      form.elements.require_git_provenance.checked = Boolean(
        task.deliverable_contract?.require_git_provenance,
      );
    }
    if (
      form.elements.required_contribution_task_ids
      && (
        !keepEditedValues
        || !form.elements.required_contribution_task_ids.value
      )
    ) {
      form.elements.required_contribution_task_ids.value = (
        task.deliverable_contract?.required_contribution_task_ids || []
      ).join("\n");
    }
  }
  function handleRecordDialogClick(event) {
    const taskCancellationClose = event.target.closest("[data-task-cancellation-close]");
    if (taskCancellationClose) {
      if (
        taskCancellationClose.classList.contains("edit-modal-backdrop") &&
        event.target !== taskCancellationClose
      ) {
        return true;
      }
      closeTaskCancellationDialog();
      return true;
    }

    if (elements.taskCancellationModal?.contains(event.target)) {
      return true;
    }

    const taskCancellationControl = event.target.closest("[data-task-cancel-id]");
    if (taskCancellationControl) {
      event.preventDefault();
      if (
        taskCancellationControl.matches(":disabled") ||
        taskCancellationControl.getAttribute("aria-disabled") === "true"
      ) {
        return true;
      }
      openTaskCancellationDialog(taskCancellationControl.dataset.taskCancelId);
      return true;
    }

    const closeTarget = event.target.closest("[data-edit-close]");
    if (closeTarget) {
      if (closeTarget.classList.contains("edit-modal-backdrop") && event.target !== closeTarget) {
        return true;
      }
      closeEditDialog();
      return true;
    }

    if (elements.editModal?.contains(event.target)) {
      return true;
    }

    const editButton = event.target.closest("[data-edit-type][data-edit-id]");
    if (!editButton) {
      return true;
    }
    if (!peers.access.guardTeamMutation("edit Team records")) {
      return true;
    }
    openEditDialog(editButton.dataset.editType, editButton.dataset.editId);
    return false;
  }
  function bindDialogKeyboard() {

    document.addEventListener("keydown", (event) => {
      if (
        event.key === "Escape" &&
        elements.taskCancellationModal &&
        !elements.taskCancellationModal.hidden
      ) {
        closeTaskCancellationDialog();
        return;
      }
      if (event.key === "Escape" && elements.editModal && !elements.editModal.hidden) {
        closeEditDialog();
        return;
      }
      if (event.key !== "Enter" && event.key !== " ") {
        return;
      }
      if (elements.editModal?.contains(event.target)) {
        return;
      }
      const editTarget = event.target.closest("[data-edit-type][data-edit-id]");
      if (!editTarget || editTarget.tagName === "BUTTON") {
        return;
      }
      event.preventDefault();
      if (!peers.access.guardTeamMutation("edit Team records")) {
        return;
      }
      openEditDialog(editTarget.dataset.editType, editTarget.dataset.editId);
    });
  }
  function bindEditSubmission() {

    elements.editForm?.addEventListener("submit", (event) => {
      event.preventDefault();
      void submitEditForm(elements.editForm);
    });
  }
  function bindCancellationSubmission() {

    elements.taskCancellationForm?.addEventListener("submit", (event) => {
      event.preventDefault();
      void submitTaskCancellation(elements.taskCancellationForm);
    });
  }

  return {
    bindDialogKeyboard,
    bindEditSubmission,
    bindCancellationSubmission,
    checkboxField,
    closeEditDialog,
    gitImportControls,
    handleRecordDialogClick,
    openEditDialog,
    submitEditForm,
    syncTaskUpdateForm,
    textareaField,
  };
}

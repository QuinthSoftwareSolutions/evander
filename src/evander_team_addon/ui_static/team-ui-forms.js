// Create form submission and endpoint payload contracts.
import {
  formData,
  formSemanticIdentifier,
  numberOrNull,
  numberOrZero,
  splitList,
  text,
  values,
} from "./team-ui-format.js";
import { noTeamRecordId } from "./team-ui-config.js";

export function createTeamFormsController(peers) {

  async function submitCreateForm(form, { detailedResult = false } = {}) {
    if (!peers.access.guardTeamMutation("save changes")) {
      return peers.mutations.submitMutationResult(false, "locked", detailedResult);
    }
    const data = formData(form);
    const endpoint = endpointForForm(form, data);
    const method = form.dataset.method || "POST";
    if (!endpoint) {
      return peers.mutations.submitMutationResult(false, "validation", detailedResult);
    }
    const formId = formSemanticIdentifier(form);
    if (
      formId === "create-governance-group-form" &&
      !peers.governance.validateGovernanceGroupEditor(form)
    ) {
      return peers.mutations.submitMutationResult(false, "validation", detailedResult);
    }
    if (
      formId === "create-project-form" &&
      !peers.policy.validateHumanReviewPolicyEditor(form)
    ) {
      return peers.mutations.submitMutationResult(false, "validation", detailedResult);
    }
    const existingProjectIds =
      formId === "create-project-form"
        ? new Set(values(peers.app.state.projects).map((project) => project.id))
        : null;
    const payload = payloadForForm(form, data);
    const result = await peers.mutations.submitMutation(form, endpoint, method, payload, { detailedResult });
    const saved = peers.mutations.mutationResultSaved(result);
    if (saved && existingProjectIds) {
      peers.projects.completeProjectCreation(form, existingProjectIds);
    }
    if (saved && formId === "create-review-profile-form" && form.hasAttribute("data-project-review-profile-create-form")) {
      peers.projects.completeReviewProfileCreation();
    }
    if (saved && formId === "create-team-from-teams-form") {
      peers.people.closeTeamsCreateTeamPanel({ reset: true });
      peers.people.renderAgents();
    }
    if (saved && formId === "create-governance-group-form") {
      peers.governance.closeGovernanceCreateGroupPanel({ reset: true });
    }
    if (saved && form.hasAttribute("data-team-inline-create-form")) {
      peers.people.closeTeamInlineCreateFormFor(form);
    }
    if (saved && form.hasAttribute("data-org-action-form")) {
      peers.org.closeOrgActionDialog();
    }
    return result;
  }

  function endpointForForm(form, data) {
    const templateEndpoint = form.dataset.templateEndpoint;
    if (templateEndpoint) {
      const templateId = text(data.template_id);
      if (!templateId) {
        peers.app.setStatus("Template is required.", "error");
        return null;
      }
      return templateEndpoint.replace("{template_id}", encodeURIComponent(templateId));
    }
    return form.dataset.endpoint || null;
  }

  function payloadForForm(form, data = formData(form)) {
    const formId = formSemanticIdentifier(form);
    if (formId === "setup-template-form") {
      const template = peers.templates.selectedTemplate();
      const roles = {};
      (template?.roles || []).forEach((role) => {
        const roleKey = text(role.key);
        if (!roleKey) {
          return;
        }
        roles[roleKey] = {
          session_id: text(data[`${roleKey}_session_id`]),
        };
      });
      const payload = {
        team: {
          name: text(data.team_name),
        },
        project: {
          name: text(data.project_name),
        },
        roles,
      };
      const operatorTarget = text(data.operator_communication_target_id);
      if (operatorTarget) {
        payload.team.operator_communication_target_id = operatorTarget;
      }
      payload.team.escalate_to_governance = Boolean(
        form.elements.escalate_to_governance?.checked,
      );
      const maxCost = numberOrNull(data.max_cost_usd);
      if (maxCost !== null) {
        payload.team.max_cost_usd = maxCost;
      }
      const costWarningRatio = numberOrNull(data.cost_warning_ratio);
      if (costWarningRatio !== null) {
        payload.team.cost_warning_ratio = costWarningRatio;
      }
      payload.team.cost_budget_renewal_mode = text(data.cost_budget_renewal_mode) || "manual";
      if (peers.templates.templateUsesGit(template)) {
        const git = peers.projects.gitConfigForForm(data);
        if (git) {
          payload.project.git = git;
        }
      }
      return payload;
    }
    if (formId === "create-team-form" || formId === "create-team-from-teams-form") {
      const payload = {
        name: text(data.name),
        manager_session_id: text(data.manager_session_id),
      };
      const managerRoleName = text(data.manager_role_name);
      if (managerRoleName) {
        payload.manager_role_name = managerRoleName;
      }
      const managerDisplayName = text(data.manager_display_name);
      if (managerDisplayName) {
        payload.manager_display_name = managerDisplayName;
      }
      const operatorTarget = text(data.operator_communication_target_id);
      if (operatorTarget) {
        payload.operator_communication_target_id = operatorTarget;
      }
      payload.escalate_to_governance = Boolean(
        form.elements.escalate_to_governance?.checked,
      );
      const maxCost = numberOrNull(data.max_cost_usd);
      if (maxCost !== null) {
        payload.max_cost_usd = maxCost;
      }
      const costWarningRatio = numberOrNull(data.cost_warning_ratio);
      if (costWarningRatio !== null) {
        payload.cost_warning_ratio = costWarningRatio;
      }
      payload.cost_budget_renewal_mode = text(data.cost_budget_renewal_mode) || "manual";
      return payload;
    }
    if (formId === "create-governance-group-form") {
      const payload = {
        name: text(data.name),
        manager_agent_id: text(data.manager_agent_id),
        member_team_ids: splitList(data.member_team_ids),
        goals: peers.governance.governanceGoalsFromForm(form),
      };
      const maxCost = numberOrNull(data.max_cost_usd);
      if (maxCost !== null) {
        payload.max_cost_usd = maxCost;
      }
      return payload;
    }
    if (formId === "create-governance-agenda-form") {
      const payload = {
        governance_group_id: text(data.governance_group_id),
        item_type: text(data.item_type),
        priority: text(data.priority) || "normal",
        title: text(data.title),
        summary: text(data.summary),
      };
      for (const key of ["team_id", "project_id", "agent_id"]) {
        const value = text(data[key]);
        if (value) {
          payload[key] = value;
        }
      }
      return payload;
    }
    if (formId === "create-project-form") {
      const goals = [];
      const goalDescription = text(data.goal_description);
      if (goalDescription) {
        goals.push({
          description: goalDescription,
        });
      }
      const payload = {
        team_id: text(data.team_id),
        name: text(data.name),
        lead_agent_id: text(data.lead_agent_id),
        directory_path: text(data.directory_path) || "root",
        goals,
      };
      const git = peers.projects.gitConfigForForm(data);
      if (git) {
        payload.git = git;
      }
      const releasePolicy = peers.policy.releasePolicyForForm(data, form);
      if (releasePolicy) {
        payload.release_policy = releasePolicy;
      }
      return payload;
    }
    if (formId === "create-role-form") {
      const payload = {
        team_id: text(data.team_id),
        name: text(data.name),
        description: text(data.description),
        permissions: splitList(data.permissions),
        ephemeral_policy: { max_concurrent: numberOrZero(data.max_concurrent) },
      };
      const projectId = text(data.project_id);
      if (projectId) {
        payload.project_id = projectId;
      }
      return payload;
    }
    if (formId === "create-case-form") {
      const payload = {
        team_id: text(data.team_id),
        title: text(data.title),
        description: text(data.description),
        status: text(data.status) || "open",
        priority: text(data.priority) || "normal",
        tags: splitList(data.tags),
        task_ids: splitList(data.task_ids),
      };
      const projectId = text(data.project_id);
      if (projectId) {
        payload.project_id = projectId;
      }
      const ownerAgentId = text(data.owner_agent_id);
      if (ownerAgentId) {
        payload.owner_agent_id = ownerAgentId;
      }
      const source = text(data.source);
      if (source) {
        payload.source = source;
      }
      const sourceRef = text(data.source_ref);
      if (sourceRef) {
        payload.source_ref = sourceRef;
      }
      return payload;
    }
    if (formId === "register-agent-form") {
      const requestedTeamId = text(data.team_id);
      const normalizedTeamId = requestedTeamId === noTeamRecordId ? null : requestedTeamId || null;
      const payload = {
        team_id: normalizedTeamId,
        project_id: text(data.project_id) || null,
        role_id: text(data.role_id) || null,
        session_id: text(data.session_id),
        reports_to_agent_id: text(data.reports_to_agent_id) || null,
        ephemeral_policy: { max_concurrent: numberOrZero(data.max_concurrent) },
      };
      const displayName = text(data.display_name);
      if (displayName) {
        payload.display_name = displayName;
      }
      return payload;
    }
    if (formId === "create-task-form") {
      const taskKind = text(data.task_kind) || "support";
      const payload = {
        owner_agent_id: text(data.owner_agent_id),
        project_id: text(data.project_id),
        priority: numberOrZero(data.priority),
        title: text(data.title),
        description: text(data.description),
        task_kind: taskKind,
        created_by_agent_id: text(data.created_by_agent_id),
      };
      const authoredBy = peers.tasks.taskAuthoredByForForm(data);
      if (authoredBy) payload.authored_by = authoredBy;
      const caseId = text(data.case_id);
      if (caseId) {
        payload.case_id = caseId;
      }
      if (text(data.state)) {
        payload.state = text(data.state);
      }
      if (text(data.git_base_branch)) {
        payload.git_base_branch = text(data.git_base_branch);
      }
      const parentTaskId = text(data.parent_task_id);
      if (parentTaskId) {
        payload.parent_task_id = parentTaskId;
      }
      const originatingTaskId = text(data.originating_task_id);
      if (originatingTaskId) {
        payload.originating_task_id = originatingTaskId;
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
      if (
        taskKind !== "coordination"
        && (
        integrationBoundary
        || requireGitProvenance
        || requiredContributionTaskIds.length > 0
        )
      ) {
        payload.integration_boundary = integrationBoundary;
        payload.deliverable_contract = {
          require_git_provenance: requireGitProvenance,
          required_contribution_task_ids: requiredContributionTaskIds,
        };
      }
      return payload;
    }
    if (formId === "create-review-profile-form") {
      const payload = {
        team_id: text(data.team_id),
        name: text(data.name),
        description: text(data.description),
        applicable_role_ids: splitList(data.applicable_role_ids),
        required_outcome_kinds: splitList(data.required_outcome_kinds),
        criteria: peers.reviews.reviewProfileCriteriaFromForm(form),
      };
      const projectId = text(data.project_id);
      if (projectId) {
        payload.project_id = projectId;
      }
      return payload;
    }
    if (formId === "create-outcome-kind-form") {
      return {
        team_id: text(data.team_id),
        project_id: form.elements.team_wide?.checked ? null : text(data.project_id) || null,
        kind: text(data.kind),
        name: text(data.name),
        description: text(data.description),
        reference_requirements: text(data.reference_requirements),
        evidence_requirements: text(data.evidence_requirements),
      };
    }
    if (formId === "record-deliverable-form") {
      const payload = {
        task_id: text(data.task_id),
        producer_agent_id: text(data.producer_agent_id),
        summary: text(data.summary),
        artifact_refs: splitList(data.artifact_refs),
      };
      if (text(data.state)) {
        payload.state = text(data.state);
      }
      return payload;
    }
    if (formId === "record-review-form") {
      const payload = {
        deliverable_id: text(data.deliverable_id),
        reviewer_agent_id: text(data.reviewer_agent_id),
        findings: splitList(data.findings),
      };
      const profileId = text(data.review_profile_id);
      if (profileId) {
        payload.review_profile_id = profileId;
      }
      const instructions = text(data.instructions);
      if (instructions) {
        payload.instructions = instructions;
      }
      if (text(data.state)) {
        payload.state = text(data.state);
      }
      return payload;
    }
    return {};
  }
  function bindCreateForms() {

    document.querySelectorAll("form[data-endpoint], form[data-template-endpoint]").forEach((form) => {
      form.addEventListener("submit", (event) => {
        event.preventDefault();
        void submitCreateForm(form);
      });
    });
  }
  function bindInlineCreation() {

    document.addEventListener("submit", (event) => {
      const form = event.target?.closest?.("form[data-team-inline-create-form]");
      if (!form) {
        return;
      }
      event.preventDefault();
      void submitCreateForm(form);
    });
  }
  function bindDelegatedCreation() {

    document.addEventListener("submit", (event) => {
      const form = event.target?.closest?.("form[data-delegated-create-form]");
      if (!form) {
        return;
      }
      event.preventDefault();
      void submitCreateForm(form);
    });
  }

  return {
    bindCreateForms,
    bindInlineCreation,
    bindDelegatedCreation,
    endpointForForm,
    payloadForForm,
    submitCreateForm,
  };
}

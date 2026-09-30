// Scoped record options and select reconciliation.
import {
  byId,
  byPriority,
  escapeAttr,
  escapeHtml,
  formSemanticIdentifier,
  humanizeToken,
  numberOrZero,
  permissionLabel,
  splitList,
  text,
  values,
} from "./team-ui-format.js";
import { elements } from "./team-ui-dom.js";
import {
  agentStatuses,
  caseStatuses,
  contextClearingPolicies,
  contextClearingPolicyLabels,
  decisionStates,
  directlyEditableTaskStates,
  gitProviders,
  governanceAgendaTypes,
  inboxPriorities,
  noTeamRecordId,
  projectStatuses,
  realtimeVoiceOptions,
  recordStates,
  taskStates,
} from "./team-ui-config.js";

export function createTeamSelectsController(peers) {

  function bindProjectLeadSelects() {
    document.addEventListener("change", (event) => {
      const teamSelect = event.target?.closest?.('form [name="team_id"]');
      if (!teamSelect) {
        return;
      }
      if (formSemanticIdentifier(teamSelect.form) !== "create-project-form") {
        return;
      }
      refreshProjectLeadSelect(teamSelect.form);
    });
  }

  function bindAgentEditTeamScopedSelects() {
    document.addEventListener("change", (event) => {
      const contextPolicySelect = event.target?.closest?.(
        'select[name="context_clearing_policy"]',
      );
      if (
        contextPolicySelect?.form === elements.editForm
        && text(elements.editForm?.dataset.editType) === "agent"
      ) {
        syncAgentEditContextIdleGraceField(contextPolicySelect.form, {
          suggestDefaultGrace: true,
        });
      }
      const teamSelect = event.target?.closest?.('select[name="team_id"]');
      if (!teamSelect || teamSelect.form !== elements.editForm) {
        return;
      }
      if (text(elements.editForm?.dataset.editType) !== "agent") {
        return;
      }
      refreshAgentEditTeamScopedSelects(teamSelect.form);
    });
  }

  function syncAgentEditContextIdleGraceField(form, { suggestDefaultGrace = false } = {}) {
    const contextPolicySelect = form?.elements?.context_clearing_policy;
    const idleGraceField = form?.querySelector?.("[data-agent-context-idle-grace]");
    if (!contextPolicySelect || !idleGraceField) {
      return;
    }
    const isIdle = text(contextPolicySelect.value) === "idle";
    idleGraceField.hidden = !isIdle;
    if (!isIdle || !suggestDefaultGrace) {
      return;
    }
    const graceInput = form.elements.context_clearing_idle_grace_minutes;
    if (graceInput && numberOrZero(graceInput.value) === 0) {
      graceInput.value = "60";
    }
  }

  function refreshAgentEditTeamScopedSelects(form) {
    if (!form || text(form.dataset.editType) !== "agent") {
      return;
    }
    const options = buildSelectOptionSets();
    form.querySelectorAll('select[data-options^="agent-edit-"]').forEach((select) => {
      fillSelect(select, optionsForSelect(select, options));
    });
  }

  function refreshProjectLeadSelect(form) {
    const select = form?.elements?.lead_agent_id;
    if (!select?.matches?.('select[data-options="project-lead-agents"]')) {
      return;
    }
    fillSelect(select, projectLeadAgentOptions(select));
  }

  function teamProjectOptions(teamId) {
    const normalizedTeamId = text(teamId);
    return values(peers.app.state.projects)
      .filter((project) => peers.app.isVisibleProject(project) && text(project.team_id) === normalizedTeamId)
      .sort(peers.people.compareTeamProjects)
      .map((project) => [project.id, `${peers.agents.projectLabel(project)} (${project.id})`]);
  }

  function teamRoleOptions(teamId) {
    const normalizedTeamId = text(teamId);
    return values(peers.app.state.roles)
      .filter((role) => peers.app.isVisibleRole(role) && text(role.team_id) === normalizedTeamId)
      .sort(peers.people.compareTeamRoles)
      .map((role) => [role.id, `${peers.agents.roleLabel(role)} (${role.id})`]);
  }

  function rolePermissionOptions() {
    return (peers.app.state.available_role_permissions || []).map((permission) => [
      permission,
      permissionLabel(permission),
      peers.tooltips.rolePermissionTooltip(permission),
    ]);
  }

  function contextClearingPolicyValue(runtime) {
    const policy = text(runtime?.context_clearing_policy);
    if (policy) {
      return policy;
    }
    return Boolean(runtime?.auto_context_clearing_enabled) ? "idle" : "off";
  }

  function contextClearingPolicyLabel(policy) {
    return contextClearingPolicyLabels[text(policy)] || "Off";
  }

  function contextClearingIdleGraceMinutesForEdit(runtime) {
    const configuredSeconds = numberOrZero(runtime?.context_clearing_idle_grace_seconds);
    return configuredSeconds / 60;
  }

  function refreshSelects({ includeEditForm = false } = {}) {
    const options = buildSelectOptionSets();
    document.querySelectorAll("select[data-options]").forEach((select) => {
      if (!includeEditForm && elements.editForm?.contains(select)) {
        return;
      }
      fillSelect(select, optionsForSelect(select, options));
    });

    const updateTaskForm = document.querySelector("#update-task-form");
    if (updateTaskForm) {
      peers.editor.syncTaskUpdateForm(updateTaskForm, { keepEditedValues: true });
    }
  }

  function buildSelectOptionSets() {
    const teams = values(peers.app.state.teams).filter(peers.app.isVisibleTeam).sort(peers.people.comparePeopleTeams).map((team) => [team.id, `${team.name} (${team.id})`]);
    const governanceGroups = values(peers.app.state.governance_groups).filter(peers.app.isVisibleGovernanceGroup).sort(peers.people.compareGovernanceGroups).map((group) => [group.id, `${group.name} (${group.id})`]);
    const projects = values(peers.app.state.projects).filter(peers.app.isVisibleProject).sort(peers.people.compareTeamProjects).map((project) => [project.id, `${project.name} (${project.id})`]);
    const roles = values(peers.app.state.roles).filter(peers.app.isVisibleRole).sort(peers.people.compareTeamRoles).map((role) => [role.id, `${role.name} (${role.id})`]);
    const agents = values(peers.app.state.agents).filter(peers.app.isVisibleAgent).sort(peers.people.comparePeopleAgents).map((agent) => [agent.id, `${agent.id} - ${peers.agents.agentRoleLabel(agent)}`]);
    const agentsByDisplayName = values(peers.app.state.agents)
      .filter(peers.app.isVisibleAgent)
      .map((agent) => [agent.id, text(agent.display_name) || text(agent.id)])
      .sort((left, right) => text(left[1]).localeCompare(text(right[1])) || text(left[0]).localeCompare(text(right[0])));
    const governanceManagerAgents = values(peers.app.state.agents)
      .filter(peers.app.isVisibleAgent)
      .map((agent) => [
        agent.id,
        `${peers.agents.agentLabel(agent)} — ${peers.agents.agentRoleLabel(agent)} · ${agent.id}`,
      ])
      .sort(
        (left, right) =>
          text(left[1]).localeCompare(text(right[1])) ||
          text(left[0]).localeCompare(text(right[0])),
      );
    const tasks = values(peers.app.state.tasks).sort(byPriority).map((task) => [task.id, `${task.title} (${task.id})`]);
    const cases = values(peers.app.state.cases).filter(peers.app.isVisibleCase).sort(peers.cases.compareCases).map((item) => [item.id, peers.cases.caseLabel(item)]);
    const deliverables = values(peers.app.state.deliverables)
      .sort((left, right) => text(left.summary || left.id).localeCompare(text(right.summary || right.id)) || byId(left, right))
      .map((deliverable) => [deliverable.id, `${deliverable.summary} (${deliverable.id})`]);
    const reviewRequests = values(peers.app.state.review_requests)
      .sort(byId)
      .map((review) => [review.id, `${review.id} - ${review.state}`]);
    const reviewProfiles = values(peers.app.state.review_profiles)
      .filter(peers.app.isVisibleReviewProfile)
      .sort(peers.people.compareReviewProfiles)
      .map((profile) => [profile.id, `${profile.name} (${profile.id})`]);
    const selectedProject = peers.app.state.projects[peers.projects.selectedProjectFocusId] || null;
    const projectOutcomeKinds = selectedProject
      ? peers.projects.effectiveOutcomeKindsForProject(selectedProject).map((definition) => [
          definition.kind,
          `${definition.name} (${definition.kind})`,
        ])
      : [];
    const teamOutcomeKinds = selectedProject
      ? values(peers.app.state.outcome_kind_definitions)
          .filter((definition) =>
            definition.team_id === selectedProject.team_id
            && !definition.project_id
            && !definition.archived
          )
          .sort((left, right) => text(left.kind).localeCompare(text(right.kind)) || byId(left, right))
          .map((definition) => [
            definition.kind,
            `${definition.name} (${definition.kind})`,
          ])
      : [];
    const releasePackages = values(peers.app.state.release_packages)
      .sort(peers.projects.compareProjectReleasesLatest)
      .map((release) => [release.id, `${release.id} - ${release.state}`]);
    const timelineOptions = peers.timeline.buildTimelineSelectOptions();
    return {
      teams,
      projects,
      roles,
      "project-review-roles": projectReviewRoleOptionsForProject(peers.projects.selectedProjectFocusId),
      "project-outcome-kinds": projectOutcomeKinds,
      "team-outcome-kinds": teamOutcomeKinds,
      agents,
      "agents-by-display-name": agentsByDisplayName,
      "governance-manager-agents": governanceManagerAgents,
      cases,
      tasks,
      deliverables,
      "review-profiles": reviewProfiles,
      "review-requests": reviewRequests,
      "release-packages": releasePackages,
      "governance-groups": governanceGroups,
      "governance-agenda-types": governanceAgendaTypes.map((item) => [item, humanizeToken(item)]),
      "inbox-priorities": inboxPriorities.map((item) => [item, humanizeToken(item)]),
      "case-statuses": caseStatuses.map((item) => [item, humanizeToken(item)]),
      "task-states": taskStates.map((item) => [item, item]),
      "task-edit-states": directlyEditableTaskStates.map((item) => [item, item]),
      "record-states": recordStates.map((item) => [item, item]),
      "agent-statuses": agentStatuses.map((item) => [item, item]),
      "context-clearing-policies": contextClearingPolicies,
      "cost-budget-renewal-modes": [
        ["manual", "Manual reset"],
        ["monthly_utc", "Monthly (UTC)"],
      ],
      "communication-targets": peers.app.communicationTargets,
      "realtime-voices": realtimeVoiceOptions,
      "project-statuses": projectStatuses.map((item) => [item, item]),
      "decision-states": decisionStates.map((item) => [item, item]),
      "git-providers": gitProviders,
      "role-permissions": rolePermissionOptions(),
      "timeline-teams": timelineOptions.teams,
      "timeline-projects": timelineOptions.projects,
      "timeline-agents": timelineOptions.agents,
      "timeline-types": timelineOptions.types,
      "timeline-statuses": timelineOptions.statuses,
      "timeline-attention": [
        ["true", "Yes"],
        ["false", "No"],
      ],
    };
  }

  function optionsForSelect(select, optionSets) {
    if (select.dataset.options === "communication-targets") {
      const options = optionSets[select.dataset.options] || [];
      const currentValue = text(select.dataset.currentValue ?? select.value);
      if (currentValue && !options.some(([value]) => value === currentValue)) {
        return [[currentValue, `${currentValue} (no longer registered)`], ...options];
      }
      return options;
    }
    if (select.dataset.options === "review-profile-edit-outcome-kinds") {
      return reviewProfileEditOutcomeOptions(select);
    }
    if (select.dataset.options === "project-lead-agents") {
      return projectLeadAgentOptions(select);
    }
    if (select.dataset.options === "agent-edit-projects") {
      return agentEditProjectOptions(select);
    }
    if (select.dataset.options === "agent-edit-roles") {
      return agentEditRoleOptions(select);
    }
    if (select.dataset.options === "agent-edit-superiors") {
      return agentEditSuperiorOptions(select);
    }
    return optionSets[select.dataset.options] || [];
  }

  function normalizeCommunicationTargetOptions(value) {
    const optionsByValue = new Map();
    (Array.isArray(value) ? value : []).forEach((option) => {
      if (!option || typeof option !== "object") {
        return;
      }
      const targetId = text(option.value);
      if (!targetId || optionsByValue.has(targetId)) {
        return;
      }
      optionsByValue.set(targetId, [targetId, text(option.label) || targetId]);
    });
    return Array.from(optionsByValue.values()).sort(
      (left, right) => text(left[1]).localeCompare(text(right[1])) || text(left[0]).localeCompare(text(right[0])),
    );
  }

  function reviewProfileEditOutcomeOptions(select) {
    const profileId = text(select.form?.dataset.editId);
    const profile = peers.app.state.review_profiles?.[profileId];
    if (!profile) {
      return [];
    }
    const project = peers.app.state.projects[text(profile.project_id)] || null;
    const definitions = project
      ? peers.projects.effectiveOutcomeKindsForProject(project)
      : values(peers.app.state.outcome_kind_definitions)
          .filter((definition) =>
            text(definition.team_id) === text(profile.team_id)
            && !definition.project_id
            && !definition.archived
          )
          .sort((left, right) => text(left.kind).localeCompare(text(right.kind)) || byId(left, right));
    const definitionsByKind = new Map(
      definitions.map((definition) => [text(definition.kind), definition]),
    );
    (profile.required_outcome_kinds || []).forEach((kind) => {
      const normalizedKind = text(kind);
      if (!normalizedKind || definitionsByKind.has(normalizedKind)) {
        return;
      }
      definitionsByKind.set(normalizedKind, {
        kind: normalizedKind,
        name: normalizedKind,
        archived: true,
      });
    });
    return Array.from(definitionsByKind.values())
      .sort((left, right) => text(left.kind).localeCompare(text(right.kind)) || byId(left, right))
      .map((definition) => [
        definition.kind,
        `${definition.name} (${definition.kind})${definition.archived ? " - archived" : ""}`,
      ]);
  }

  function agentEditTeamId(select) {
    const teamId = text(select.form?.elements?.team_id?.value);
    return teamId === noTeamRecordId ? "" : teamId;
  }

  function agentEditProjectOptions(select) {
    const teamId = agentEditTeamId(select);
    if (!teamId) {
      return [];
    }
    const agent = peers.app.state.agents?.[text(select.form?.dataset?.editId)] || null;
    const leadProjectIds = new Set(peers.agents.agentLeadProjectIds(agent));
    return teamProjectOptions(teamId).map(([projectId, label]) => [
      projectId,
      leadProjectIds.has(text(projectId)) ? `${label} — Project lead` : label,
    ]);
  }

  function agentEditRoleOptions(select) {
    const teamId = agentEditTeamId(select);
    return teamId ? teamRoleOptions(teamId) : [];
  }

  function agentEditSuperiorOptions(select) {
    const teamId = agentEditTeamId(select);
    if (!teamId) {
      return [];
    }
    const editedAgentId = text(select.form?.dataset?.editId);
    return peers.people.teamAgentOptions(teamId).filter(([agentId]) => text(agentId) !== editedAgentId);
  }

  function projectLeadAgentOptions(select) {
    const project = peers.app.state.projects?.[text(select.form?.dataset?.editId)] || null;
    const teamId = text(select.form?.elements?.team_id?.value) || text(project?.team_id);
    return projectLeadAgentOptionsForTeam(teamId);
  }

  function projectLeadAgentOptionsForTeam(teamId) {
    const normalizedTeamId = text(teamId);
    const team = normalizedTeamId ? peers.app.state.teams?.[normalizedTeamId] : null;
    if (!team) {
      return [];
    }
    return values(peers.app.state.agents)
      .filter(peers.app.isVisibleAgent)
      .filter((agent) => text(agent.team_id) === normalizedTeamId)
      .sort(peers.people.comparePeopleAgents)
      .map((agent) => [agent.id, `${agent.id} - ${peers.agents.agentRoleLabel(agent)}`]);
  }

  function projectReviewRoleOptionsForProject(projectId) {
    const project = peers.app.state.projects?.[text(projectId)];
    if (!project) {
      return [];
    }
    return values(peers.app.state.roles)
      .filter(peers.app.isVisibleRole)
      .filter((role) => {
        if (text(role.team_id) !== text(project.team_id)) {
          return false;
        }
        const roleProjectId = text(role.project_id);
        return !roleProjectId || roleProjectId === text(project.id);
      })
      .sort(peers.people.compareTeamRoles)
      .map((role) => [role.id, `${role.name} (${role.id})`]);
  }

  function fillSelect(select, options) {
    const currentValue = select.dataset.currentValue ?? select.value;
    const currentValues = select.multiple
      ? new Set(
          select.dataset.currentValue !== undefined
            ? splitList(select.dataset.currentValue)
            : Array.from(select.selectedOptions).map((option) => option.value),
        )
      : null;
    const includePlaceholder = !select.multiple;
    const placeholder = select.dataset.placeholder || (select.required ? "Select" : "Default");
    select.innerHTML = `${includePlaceholder ? `<option value="">${placeholder}</option>` : ""}${options
      .map(([value, label, tooltip]) => {
        const optionTooltip = text(tooltip);
        return `<option value="${escapeAttr(value)}"${optionTooltip ? ` title="${escapeAttr(optionTooltip)}"` : ""}>${escapeHtml(label)}</option>`;
      })
      .join("")}`;
    if (select.multiple) {
      Array.from(select.options).forEach((option) => {
        option.selected = currentValues.has(option.value);
      });
      delete select.dataset.currentValue;
      return;
    }
    if (options.some(([value]) => value === currentValue)) {
      select.value = currentValue;
    } else if (!currentValue) {
      select.value = "";
    }
    delete select.dataset.currentValue;
  }

  function fillSelectsIn(root) {
    const options = buildSelectOptionSets();
    root.querySelectorAll("select[data-options]").forEach((select) => {
      fillSelect(select, optionsForSelect(select, options));
    });
  }

  return {
    bindAgentEditTeamScopedSelects,
    bindProjectLeadSelects,
    contextClearingIdleGraceMinutesForEdit,
    contextClearingPolicyLabel,
    contextClearingPolicyValue,
    fillSelect,
    fillSelectsIn,
    normalizeCommunicationTargetOptions,
    projectLeadAgentOptionsForTeam,
    refreshProjectLeadSelect,
    refreshSelects,
    rolePermissionOptions,
    teamProjectOptions,
    teamRoleOptions,
  };
}

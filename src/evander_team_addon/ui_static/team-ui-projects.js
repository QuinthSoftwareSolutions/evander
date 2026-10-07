// Project focus, delivery history, Git configuration, and review profile setup.
import {
  byId,
  byPriority,
  clampPage,
  escapeAttr,
  escapeHtml,
  formData,
  responseText,
  text,
  uniqueTextList,
  values,
} from "./team-ui-format.js";
import { API_BASE, RELEASE_GATE_LABELS, projectRecordPageSize } from "./team-ui-config.js";

export function createTeamProjectsController(peers) {

  let selectedProjectFocusId = null;

  let projectCreateFormOpen = false;

  let projectReviewProfileCreateProjectId = null;

  const projectRecordSectionPages = {};

  const projectRecordShowRejected = new Set();

  function bindProjectFocus() {
    document.addEventListener("click", (event) => {
      const createToggle = event.target?.closest?.("[data-project-create-toggle]");
      if (createToggle) {
        event.preventDefault();
        if (!peers.access.guardTeamMutation("create projects")) {
          return;
        }
        projectCreateFormOpen = !projectCreateFormOpen;
        syncProjectCreatePanel();
        if (projectCreateFormOpen) {
          const form = document.querySelector("[data-project-create-form]");
          if (form) {
            peers.selects.fillSelectsIn(form);
            peers.tooltips.decorateFieldTooltips(form);
          }
          form?.querySelector("select, input, textarea")?.focus();
        }
        return;
      }

      const createClose = event.target?.closest?.("[data-project-create-close]");
      if (createClose) {
        event.preventDefault();
        projectCreateFormOpen = false;
        syncProjectCreatePanel({ reset: true });
        return;
      }

      const button = event.target?.closest?.("[data-project-focus-id]");
      if (button) {
        event.preventDefault();
        selectedProjectFocusId = text(button.dataset.projectFocusId) || null;
        projectReviewProfileCreateProjectId = null;
        renderProjectFocus();
        return;
      }

      const reviewProfileCreateToggle = event.target?.closest?.("[data-project-review-profile-create-toggle]");
      if (reviewProfileCreateToggle) {
        event.preventDefault();
        if (!peers.access.guardTeamMutation("create review profiles")) {
          return;
        }
        const projectId = text(reviewProfileCreateToggle.dataset.projectId);
        projectReviewProfileCreateProjectId =
          text(projectReviewProfileCreateProjectId) === projectId ? null : projectId;
        renderProjectFocus();
        const form = document.querySelector("[data-project-review-profile-create-form]");
        if (form) {
          peers.selects.fillSelectsIn(form);
          peers.tooltips.decorateFieldTooltips(form);
          form.querySelector("input, select, textarea")?.focus();
        }
        return;
      }

      const reviewProfileCreateClose = event.target?.closest?.("[data-project-review-profile-create-close]");
      if (reviewProfileCreateClose) {
        event.preventDefault();
        projectReviewProfileCreateProjectId = null;
        renderProjectFocus();
        return;
      }

      const rejectedToggle = event.target?.closest?.("[data-project-section-show-rejected]");
      if (rejectedToggle) {
        event.preventDefault();
        const key = projectRecordSectionKey(
          rejectedToggle.dataset.projectId,
          rejectedToggle.dataset.projectSection,
        );
        if (projectRecordShowRejected.has(key)) {
          projectRecordShowRejected.delete(key);
        } else {
          projectRecordShowRejected.add(key);
        }
        projectRecordSectionPages[key] = 1;
        peers.disclosures.openDisclosure(peers.disclosures.teamDisclosureStateKey(key));
        renderProjectFocus();
        return;
      }

      const pageButton = event.target?.closest?.("[data-project-section-page]");
      if (pageButton) {
        event.preventDefault();
        const key = projectRecordSectionKey(
          pageButton.dataset.projectId,
          pageButton.dataset.projectSection,
        );
        projectRecordSectionPages[key] = Math.max(1, Math.floor(Number(pageButton.dataset.projectSectionPage) || 1));
        peers.disclosures.openDisclosure(peers.disclosures.teamDisclosureStateKey(key));
        renderProjectFocus();
      }
    });
  }

  function renderProjects() {
    peers.disclosures.rememberOpenTeamDisclosures();
    const allProjects = values(peers.app.state.projects).sort(peers.people.compareTeamProjects);
    const projects = allProjects.filter(peers.app.isVisibleProject);
    if (!allProjects.length) {
      peers.app.renderIntoActiveTargets("[data-projects-view]", `<p class="empty-state">No projects registered.</p>`);
      return;
    }
    if (!projects.length) {
      peers.app.renderIntoActiveTargets("[data-projects-view]", `<p class="empty-state">No visible projects.</p>`);
      return;
    }

    if (peers.app.activeTeamView === "planning") {
      peers.planning.updatePlanningFilterControls();
      const projectCards = projects
        .map((project) => {
          const projectTasks = values(peers.app.state.tasks)
            .filter((task) => task.project_id === project.id)
            .sort(byPriority);
          const visibleTasks = peers.planning.planningTasksForProject(projectTasks);
          if (!visibleTasks.length) {
            return null;
          }
          const visibleTaskIds = new Set(visibleTasks.map((task) => task.id));
          const projectDeliverables = values(peers.app.state.deliverables).filter((deliverable) => {
            return visibleTaskIds.has(deliverable.task_id);
          });
          return {
            project,
            projectTasks,
            visibleTasks,
            projectDeliverables,
          };
        })
        .filter(Boolean)
        .sort(peers.planning.comparePlanningProjectCards);
      if (!projectCards.length) {
        peers.app.renderIntoActiveTargets(
          "[data-projects-view]",
          `<p class="empty-state">No project task cards match the selected statuses.</p>`,
        );
        return;
      }
      peers.app.renderIntoActiveTargets(
        "[data-projects-view]",
        projectCards
          .map(({ project, projectTasks, visibleTasks, projectDeliverables }) =>
            peers.planning.renderPlanningProjectBlock(project, projectTasks, visibleTasks, projectDeliverables),
          )
          .join(""),
      );
      return;
    }

    peers.app.renderIntoActiveTargets(
      "[data-projects-view]",
      projects
        .map((project) => {
          const projectTasks = values(peers.app.state.tasks)
            .filter((task) => task.project_id === project.id)
            .sort(byPriority);
          const projectDeliverables = values(peers.app.state.deliverables).filter((deliverable) => {
            const task = peers.app.state.tasks[deliverable.task_id];
            return task?.project_id === project.id;
          });
          return `
          <article class="project-block">
              <div class="project-header">
                <div>
                  <strong>${escapeHtml(project.name)}</strong>
                  <div class="meta-line">${escapeHtml(project.id)} - lead ${escapeHtml(project.lead_agent_id)}</div>
                  ${peers.costs.renderProjectCostCounter(project.id)}
                  ${renderProjectGit(project.git)}
                  ${renderProjectReleasePolicy(project.release_policy)}
                </div>
                <div class="record-actions">
                  <span class="state-pill ${escapeHtml(project.status)}">${escapeHtml(project.status)}</span>
                  ${peers.access.editButton("project", project.id)}
                </div>
              </div>
            <div class="project-columns">
              <div>
                <p class="subheading">Goals</p>
                ${peers.org.renderGoals(project.id, project.goals || [])}
                <p class="subheading">Deliverables</p>
                ${peers.reviews.renderDeliverables(projectDeliverables)}
              </div>
              <div>
                <p class="subheading">Tasks</p>
                ${peers.planning.renderTasks(projectTasks)}
              </div>
            </div>
          </article>
        `;
        })
        .join(""),
    );
  }

  function renderProjectFocus() {
    const drafts = peers.drafts.captureFormDrafts();
    peers.disclosures.rememberOpenTeamDisclosures();
    if (!document.querySelector("[data-projects-focus-list]")) {
      return;
    }
    const projectTargets = Array.from(
      document.querySelectorAll(
        "[data-projects-focus-list], [data-projects-focus-detail]",
      ),
    );
    if (!projectTargets.some(peers.app.targetIsActive)) {
      projectTargets.forEach((target) => {
        target.innerHTML = "";
      });
      return;
    }
    syncProjectCreatePanel();
    const allProjects = values(peers.app.state.projects).sort(peers.people.compareTeamProjects);
    const projects = allProjects.filter(peers.app.isVisibleProject);
    if (!projects.some((project) => project.id === selectedProjectFocusId)) {
      selectedProjectFocusId = projects[0]?.id || null;
      projectReviewProfileCreateProjectId = null;
    }
    const selectedProject = selectedProjectFocusId ? peers.app.state.projects[selectedProjectFocusId] : null;

    document.querySelectorAll("[data-projects-focus-list]").forEach((target) => {
      if (!allProjects.length) {
        target.innerHTML = `<p class="empty-state">No projects registered.</p>`;
        return;
      }
      if (!projects.length) {
        target.innerHTML = `<p class="empty-state">No visible projects.</p>`;
        return;
      }
      target.innerHTML = `
        <div class="projects-focus-list-header">
          <span>${projects.length} visible project${projects.length === 1 ? "" : "s"}</span>
          ${peers.app.showArchived ? `<span>Archived included</span>` : ""}
        </div>
        <div class="projects-focus-list-items">
          ${projects.map((project) => renderProjectFocusListItem(project)).join("")}
        </div>
      `;
    });

    document.querySelectorAll("[data-projects-focus-detail]").forEach((target) => {
      target.innerHTML = selectedProject
        ? renderProjectFocusDetail(selectedProject)
        : `<p class="empty-state">Select or create a project.</p>`;
      peers.selects.fillSelectsIn(target);
      peers.tooltips.decorateFieldTooltips(target);
    });
    syncProjectCreatePanel();
    peers.access.applyLockedMutationControls();
    peers.drafts.restoreFormDrafts(drafts);
  }

  function renderProjectFocusListItem(project) {
    const active = project.id === selectedProjectFocusId;
    const team = peers.app.state.teams[project.team_id];
    return `
      <button
        type="button"
        class="projects-focus-row${active ? " active" : ""}"
        role="tab"
        data-project-focus-id="${escapeAttr(project.id)}"
        aria-selected="${active ? "true" : "false"}"
      >
        <span class="projects-focus-row-primary">
          <strong>${escapeHtml(project.name)}</strong>
          <span class="state-pill ${escapeHtml(project.status)}">${escapeHtml(project.status)}</span>
        </span>
        ${team ? `<span class="projects-focus-row-team">${escapeHtml(team.name)}</span>` : ""}
      </button>
    `;
  }

  function renderProjectFocusDetail(project) {
    const team = peers.app.state.teams[project.team_id];
    const leadAgent = peers.app.state.agents[project.lead_agent_id];
    const tasks = values(peers.app.state.tasks)
      .filter((task) => task.project_id === project.id)
      .sort(byPriority);
    const taskIds = new Set(tasks.map((task) => task.id));
    const deliverables = values(peers.app.state.deliverables)
      .filter((deliverable) => taskIds.has(deliverable.task_id))
      .sort(compareProjectDeliverablesLatest);
    const releases = values(peers.app.state.release_packages)
      .filter((release) => release.project_id === project.id)
      .sort(compareProjectReleasesLatest);
    const projectProfiles = projectSpecificReviewProfiles(project);
    const teamProfiles = teamWideReviewProfilesForProject(project);
    const outcomeKinds = outcomeKindDefinitionsForProject(project);
    const projectAgents = values(peers.app.state.agents)
      .filter(peers.app.isVisibleAgent)
      .filter((agent) => peers.agents.agentProjectIds(agent).includes(project.id))
      .sort(peers.people.comparePeopleAgents);
    const projectRoles = values(peers.app.state.roles)
      .filter(peers.app.isVisibleRole)
      .filter((role) => role.project_id === project.id)
      .sort(peers.people.compareTeamRoles);

    return `
      <div class="projects-detail-header">
        <div>
          <p class="section-label">Selected project</p>
          <h2>${escapeHtml(project.name)}</h2>
          <div class="meta-line">${escapeHtml(project.id)} - ${escapeHtml(team ? `${team.name} (${team.id})` : project.team_id)}</div>
        </div>
        <div class="record-actions">
          <span class="state-pill ${escapeHtml(project.status)}">${escapeHtml(project.status)}</span>
          ${peers.access.editButton("project", project.id)}
        </div>
      </div>
      <div class="project-focus-metrics">
        ${renderProjectFocusMetric("Lead", leadAgent ? peers.agents.agentLabel(leadAgent) : project.lead_agent_id)}
        ${renderProjectFocusMetric("Tasks", tasks.length)}
        ${renderProjectFocusMetric("Deliverables", deliverables.length)}
        ${renderProjectFocusMetric("Review profiles", projectProfiles.length)}
        ${renderProjectFocusMetric("Agents", projectAgents.length)}
        ${renderProjectFocusMetric("Releases", releases.length)}
      </div>
      ${peers.costs.renderProjectCostCounter(project.id)}
      ${renderProjectGit(project.git)}
      ${renderProjectReleasePolicy(project.release_policy)}
      <div class="projects-detail-grid">
        <section>
          <h3>Goals</h3>
          ${peers.org.renderGoals(project.id, project.goals || [], { compact: true })}
        </section>
        <section>
          <h3>Project roles</h3>
          ${renderProjectFocusRoles(projectRoles)}
        </section>
        <section>
          <h3>Project agents</h3>
          ${renderProjectFocusAgents(projectAgents)}
        </section>
      </div>
      <div class="project-record-sections">
        ${renderProjectRecordSection(project, "deliverables", "Deliverables", deliverables, renderProjectDeliverablePage)}
        ${renderProjectRecordSection(project, "releases", "Releases", releases, renderProjectReleasePage)}
      </div>
      <section class="project-review-profile-panel">
        <div class="projects-detail-section-header">
          <div>
            <p class="section-label">Outcome catalog</p>
            <h3>Defined project outcomes</h3>
          </div>
        </div>
        ${renderProjectOutcomeKinds(outcomeKinds)}
        ${renderProjectOutcomeKindCreateForm(project)}
      </section>
      <section class="project-review-profile-panel">
        <div class="projects-detail-section-header">
          <div>
            <p class="section-label">Review profile</p>
            <h3>Project review profiles</h3>
          </div>
        </div>
        ${renderProjectReviewProfiles(projectProfiles, teamProfiles)}
        ${renderProjectReviewProfileCreatePanel(project)}
      </section>
    `;
  }

  function effectiveOutcomeKindsForProject(project) {
    const byKind = new Map();
    values(peers.app.state.outcome_kind_definitions)
      .filter((definition) => definition.team_id === project.team_id && !definition.project_id)
      .sort(byId)
      .forEach((definition) => {
        if (!definition.archived) byKind.set(definition.kind, { ...definition, inherited: true });
      });
    values(peers.app.state.outcome_kind_definitions)
      .filter((definition) => definition.project_id === project.id)
      .sort(byId)
      .forEach((definition) => {
        if (definition.archived) byKind.delete(definition.kind);
        else byKind.set(definition.kind, { ...definition, inherited: false });
      });
    return Array.from(byKind.values()).sort((left, right) =>
      text(left.kind).localeCompare(text(right.kind)) || byId(left, right),
    );
  }

  function outcomeKindDefinitionsForProject(project) {
    const effectiveIds = new Set(
      effectiveOutcomeKindsForProject(project).map((definition) => definition.id),
    );
    return values(peers.app.state.outcome_kind_definitions)
      .filter((definition) =>
        definition.team_id === project.team_id
        && (!definition.project_id || definition.project_id === project.id)
      )
      .map((definition) => ({
        ...definition,
        inherited: !definition.project_id,
        effective: effectiveIds.has(definition.id),
      }))
      .sort((left, right) =>
        text(left.kind).localeCompare(text(right.kind))
        || Number(left.inherited) - Number(right.inherited)
        || byId(left, right),
      );
  }

  function renderProjectOutcomeKinds(definitions) {
    if (!definitions.length) {
      return `<p class="empty-state">No outcome kinds are defined for this project.</p>`;
    }
    return `
      <ul class="review-list compact-record-list">
        ${definitions.map((definition) => `
          <li class="record-item compact-record-card">
            <details ${peers.disclosures.teamDisclosureAttributes(peers.disclosures.teamDisclosureKey("projects", "outcome-kind", definition.id, "detail"))}>
              <summary class="compact-record-summary">
                <span class="compact-record-main">
                  <strong>${escapeHtml(definition.name)}</strong>
                  <span>
                    ${escapeHtml(definition.kind)}
                    - ${definition.inherited ? "team-wide" : "project"}
                    - ${definition.archived ? "archived" : definition.effective ? "effective" : "overridden"}
                  </span>
                </span>
              </summary>
              <div class="compact-record-body">
                <div class="record-actions">${peers.access.editButton("outcome-kind", definition.id)}</div>
                ${definition.description ? `<div class="meta-line">${escapeHtml(definition.description)}</div>` : ""}
                ${definition.reference_requirements ? `<div class="meta-line"><strong>References:</strong> ${escapeHtml(definition.reference_requirements)}</div>` : ""}
                ${definition.evidence_requirements ? `<div class="meta-line"><strong>Evidence:</strong> ${escapeHtml(definition.evidence_requirements)}</div>` : ""}
              </div>
            </details>
          </li>
        `).join("")}
      </ul>
    `;
  }

  function renderProjectOutcomeKindCreateForm(project) {
    return `
      <details class="project-outcome-create" ${peers.disclosures.teamDisclosureAttributes(peers.disclosures.teamDisclosureKey("projects", project.id, "create-outcome-kind"))}>
      <summary>Add outcome kind</summary>
      <form
        class="project-review-profile-form"
        data-form-id="create-outcome-kind-form"
        data-form-draft-scope="${escapeAttr(project.id)}"
        data-endpoint="/outcome-kinds"
        data-method="POST"
        data-delegated-create-form
      >
        <input type="hidden" name="team_id" value="${escapeAttr(project.team_id)}" />
        <input type="hidden" name="project_id" value="${escapeAttr(project.id)}" />
        <div class="project-review-profile-fields">
          <label>Kind <input name="kind" required placeholder="durable_result_kind" /></label>
          <label>Name <input name="name" required /></label>
          <label class="checkbox-label"><input type="checkbox" name="team_wide" /><span>Team-wide definition</span></label>
          <label>Description <textarea name="description" rows="2" required></textarea></label>
          <label>Reference requirements <textarea name="reference_requirements" rows="2" required></textarea></label>
          <label>Evidence requirements <textarea name="evidence_requirements" rows="2"></textarea></label>
        </div>
        <button type="submit">Add outcome kind</button>
      </form>
      </details>
    `;
  }

  function syncProjectCreatePanel({ reset = false } = {}) {
    const form = document.querySelector("[data-project-create-form]");
    const button = document.querySelector("[data-project-create-toggle]");
    if (form) {
      form.hidden = !projectCreateFormOpen;
      if (reset) {
        form.reset();
        peers.policy.restoreHumanReviewChecklistDraft(form, []);
      }
      if (projectCreateFormOpen) {
        peers.selects.fillSelectsIn(form);
        peers.tooltips.decorateFieldTooltips(form);
        peers.policy.syncHumanReviewChecklistEditor(
          form.querySelector("[data-human-review-checklist-editor]"),
        );
      }
    }
    if (button) {
      button.setAttribute("aria-expanded", projectCreateFormOpen ? "true" : "false");
    }
  }

  function renderProjectRecordSection(project, section, title, records, renderPageRecords) {
    const key = projectRecordSectionKey(project.id, section);
    const rejectedRecords = records.filter(isRejectedProjectRecord);
    const showRejected = projectRecordShowRejected.has(key);
    const visibleRecords = showRejected
      ? records
      : records.filter((record) => !isRejectedProjectRecord(record));
    const totalPages = Math.max(1, Math.ceil(visibleRecords.length / projectRecordPageSize));
    const page = clampPage(projectRecordSectionPages[key] || 1, totalPages);
    projectRecordSectionPages[key] = page;
    const start = (page - 1) * projectRecordPageSize;
    const pageRecords = visibleRecords.slice(start, start + projectRecordPageSize);
    const summaryParts = [
      `${visibleRecords.length} shown`,
      rejectedRecords.length && !showRejected ? `${rejectedRecords.length} rejected hidden` : "",
      visibleRecords.length > projectRecordPageSize ? `page ${page} of ${totalPages}` : "",
    ].filter(Boolean);
    return `
      <details
        class="project-record-section"
        data-project-record-section
        data-project-id="${escapeAttr(project.id)}"
        data-project-section="${escapeAttr(section)}"
        ${peers.disclosures.teamDisclosureAttributes(key)}
      >
        <summary class="project-record-section-summary">
          <span class="compact-record-main">
            <strong>${escapeHtml(title)}</strong>
            <span>${escapeHtml(summaryParts.join(" - ") || "No records")}</span>
          </span>
        </summary>
        <div class="project-record-section-body">
          <div class="project-record-section-controls">
            ${
              rejectedRecords.length
                ? `<button
                    type="button"
                    class="record-action"
                    aria-pressed="${showRejected ? "true" : "false"}"
                    data-project-id="${escapeAttr(project.id)}"
                    data-project-section="${escapeAttr(section)}"
                    data-project-section-show-rejected
                  >${showRejected ? "Hide rejected" : "Show rejected"}</button>`
                : ""
            }
          </div>
          ${renderPageRecords(pageRecords)}
          ${renderProjectRecordPager(project.id, section, page, totalPages, visibleRecords.length, start, pageRecords.length)}
        </div>
      </details>
    `;
  }

  function renderProjectDeliverablePage(deliverables) {
    return peers.reviews.renderDeliverables(deliverables, { compact: true });
  }

  function renderProjectReleasePage(releases) {
    if (!releases.length) {
      return `<p class="empty-state">No release packages.</p>`;
    }
    return `<ul class="release-list compact-record-list">${releases.map(peers.releases.renderCompactRelease).join("")}</ul>`;
  }

  function renderProjectRecordPager(projectId, section, page, totalPages, totalRecords, start, pageLength) {
    if (totalRecords <= projectRecordPageSize) {
      return `<div class="team-record-pager-summary">Showing ${escapeHtml(totalRecords)} ${escapeHtml(section)}</div>`;
    }
    const first = start + 1;
    const last = start + pageLength;
    return `
      <div class="team-record-pager" aria-label="${escapeAttr(section)} pages">
        <button
          type="button"
          data-project-id="${escapeAttr(projectId)}"
          data-project-section="${escapeAttr(section)}"
          data-project-section-page="${page - 1}"
          ${page <= 1 ? "disabled" : ""}
        >Previous</button>
        <span>${escapeHtml(first)}-${escapeHtml(last)} of ${escapeHtml(totalRecords)}</span>
        <button
          type="button"
          data-project-id="${escapeAttr(projectId)}"
          data-project-section="${escapeAttr(section)}"
          data-project-section-page="${page + 1}"
          ${page >= totalPages ? "disabled" : ""}
        >Next</button>
      </div>
    `;
  }

  function projectRecordSectionKey(projectId, section) {
    return peers.disclosures.teamDisclosureKey("projects", "record-section", projectId, section);
  }

  function isRejectedProjectRecord(record) {
    return text(record?.state) === "rejected";
  }

  function compareProjectDeliverablesLatest(left, right) {
    return compareProjectRecordsLatest(left, right, ["created_at"]);
  }

  function compareProjectReleasesLatest(left, right) {
    return compareProjectRecordsLatest(left, right, ["published_at", "updated_at", "created_at"]);
  }

  function compareReviewRequestsLatest(left, right) {
    return compareProjectRecordsLatest(left, right, ["updated_at", "created_at"]);
  }

  function compareProjectRecordsLatest(left, right, fields) {
    const leftTime = projectRecordSortTime(left, fields);
    const rightTime = projectRecordSortTime(right, fields);
    if (leftTime !== rightTime) {
      return rightTime - leftTime;
    }
    for (const field of fields) {
      const leftText = text(left?.[field]);
      const rightText = text(right?.[field]);
      if (leftText !== rightText) {
        return rightText.localeCompare(leftText);
      }
    }
    return text(right?.id).localeCompare(text(left?.id));
  }

  function projectRecordSortTime(record, fields) {
    for (const field of fields) {
      const value = text(record?.[field]);
      if (!value) {
        continue;
      }
      const parsed = Date.parse(peers.timeline.normalizeTimelineTimestampForSort(value));
      if (Number.isFinite(parsed)) {
        return parsed;
      }
    }
    return Number.NEGATIVE_INFINITY;
  }

  function renderProjectFocusMetric(label, value) {
    return `
      <div class="project-focus-metric">
        <span>${escapeHtml(label)}</span>
        <strong>${escapeHtml(value)}</strong>
      </div>
    `;
  }

  function renderProjectFocusRoles(roles) {
    if (!roles.length) {
      return `<p class="empty-state">No project-specific roles.</p>`;
    }
    return `
      <ul class="compact-record-list">
        ${roles
          .map((role) => `
            <li class="record-item compact-record-card">
              <div class="record-line">
                <strong>${escapeHtml(role.name)}</strong>
                <div class="record-actions">${peers.access.editButton("role", role.id)}</div>
              </div>
              <div class="meta-line">${escapeHtml(role.id)}</div>
              ${role.description ? `<div class="meta-line">${escapeHtml(role.description)}</div>` : ""}
            </li>
          `)
          .join("")}
      </ul>
    `;
  }

  function renderProjectFocusAgents(agents) {
    if (!agents.length) {
      return `<p class="empty-state">No project agents.</p>`;
    }
    return `
      <ul class="compact-record-list">
        ${agents
          .map((agent) => `
            <li class="record-item compact-record-card">
              <div class="record-line">
                <strong>${escapeHtml(peers.agents.agentLabel(agent))}</strong>
                <div class="record-actions">${peers.access.editButton("agent", agent.id)}</div>
              </div>
              <div class="meta-line">${escapeHtml(agent.id)} - ${escapeHtml(peers.agents.agentRoleLabel(agent))}</div>
            </li>
          `)
          .join("")}
      </ul>
    `;
  }

  function renderProjectReviewProfileCreatePanel(project) {
    const open = text(projectReviewProfileCreateProjectId) === text(project.id);
    if (!open) {
      return `
        <div class="project-review-profile-create-actions">
          <button
            type="button"
            class="record-action"
            aria-expanded="false"
            data-project-id="${escapeAttr(project.id)}"
            data-project-review-profile-create-toggle
          >
            Add review profile
          </button>
        </div>
      `;
    }
    return renderProjectReviewProfileCreateForm(project);
  }

  function renderProjectReviewProfileCreateForm(project) {
    return `
      <form
        id="create-review-profile-for-project-form"
        class="project-review-profile-form"
        data-form-id="create-review-profile-form"
        data-form-draft-scope="${escapeAttr(project.id)}"
        data-endpoint="/review-profiles"
        data-method="POST"
        data-delegated-create-form
        data-project-review-profile-create-form
      >
        <div class="project-review-profile-form-header">
          <div>
            <p class="section-label">Review profile</p>
            <h3>Add review profile</h3>
          </div>
          <button type="button" class="record-action" data-project-review-profile-create-close>Close</button>
        </div>
        <input type="hidden" name="team_id" value="${escapeAttr(project.team_id)}" />
        <input type="hidden" name="project_id" value="${escapeAttr(project.id)}" />
        <div class="project-review-profile-fields">
          <label>Name <input name="name" required /></label>
          <label>Description <textarea name="description" rows="2"></textarea></label>
          <label>Applicable roles <select name="applicable_role_ids" multiple size="4" data-options="project-review-roles"></select></label>
          <label>Required outcome kinds <select name="required_outcome_kinds" multiple size="4" data-options="project-outcome-kinds"></select></label>
        </div>
        ${peers.reviews.renderReviewProfileCriteriaEditor()}
        <button type="submit">Create profile</button>
      </form>
    `;
  }

  function renderProjectReviewProfiles(projectProfiles, teamProfiles) {
    const projectList = projectProfiles.length
      ? `<ul class="review-list compact-record-list">${projectProfiles.map(peers.reviews.renderCompactReviewProfile).join("")}</ul>`
      : `<p class="empty-state">No project-specific review profiles.</p>`;
    const teamList = teamProfiles.length
      ? `
        <details class="team-wide-review-profiles" ${peers.disclosures.teamDisclosureAttributes(peers.disclosures.teamDisclosureKey("projects", "team-review-profiles", project.id))}>
          <summary>Team-wide profiles available to this project</summary>
          <ul class="review-list compact-record-list">${teamProfiles.map(peers.reviews.renderCompactReviewProfile).join("")}</ul>
        </details>
      `
      : "";
    return `
      <div class="project-review-profile-list">
        ${projectList}
        ${teamList}
      </div>
    `;
  }

  function projectSpecificReviewProfiles(project) {
    return values(peers.app.state.review_profiles)
      .filter(peers.app.isVisibleReviewProfile)
      .filter((profile) => profile.project_id === project.id)
      .sort(peers.people.compareReviewProfiles);
  }

  function teamWideReviewProfilesForProject(project) {
    return values(peers.app.state.review_profiles)
      .filter(peers.app.isVisibleReviewProfile)
      .filter((profile) => !profile.project_id && profile.team_id === project.team_id)
      .sort(peers.people.compareReviewProfiles);
  }

  function renderProjectGit(git) {
    if (!git || !git.managed_repo_id) {
      return "";
    }
    const parts = [`managed ${git.managed_repo_id}`];
    if (git.base_branch) parts.push(`base ${git.base_branch}`);
    if (git.agent_author_identity) parts.push(`author ${git.agent_author_identity.name}`);
    if (git.agent_committer_identity) parts.push(`committer ${git.agent_committer_identity.name}`);
    return `<div class="meta-line">Git: ${escapeHtml(parts.join(" - "))}</div>`;
  }

  function renderProjectReleasePolicy(policy) {
    if (!policy) {
      return `<div class="meta-line">Release gates: not configured (Git releases are blocked)</div>`;
    }
    const commands = (policy.verification_commands || []).map((command) => command.name);
    const reviewers = (policy.review_requirements || []).map((requirement) => requirement.reviewer_agent_id);
    const human = policy.human_review_requirement;
    const enabledOrder = peers.policy.normalizedReleaseGateSequence(policy)
      .filter((stage) => stage.enabled)
      .map((stage) => RELEASE_GATE_LABELS[stage.kind] || stage.kind)
      .join(" → ");
    const humanLabel = human
      ? `human review via ${(human.reviewer_group_ids || []).join(", ")} (${human.preparation_mode || "manual"})`
      : "no human review";
    const publicationLabel = policy.automatic_release_branch_publication === false
      ? "workflow-managed branch publication"
      : "automatic canonical branch publication";
    return `<div class="meta-line">Release gates: ${escapeHtml(enabledOrder || "ungated")} - ${escapeHtml(commands.length ? `${commands.length} verification checks configured` : "no verification checks configured")} - ${escapeHtml(reviewers.length ? `reviewers ${reviewers.join(", ")}` : "no release reviewers configured")} - ${escapeHtml(humanLabel)} - ${escapeHtml(publicationLabel)}</div>`;
  }

  async function importGitRepoForForm(form, button) {
    if (!peers.access.guardTeamMutation("import Git repositories")) {
      return false;
    }
    const data = formData(form);
    const repoSource = text(data.repo_source);
    if (!repoSource) {
      peers.app.setStatus("Repository source is required before import.", "error");
      return false;
    }
    const payload = { repo_source: repoSource };
    const directoryPath = text(form.dataset.directoryPath);
    if (directoryPath) {
      payload.directory_path = directoryPath;
    }
    const statusTarget = form.querySelector("[data-git-import-status]");
    setGitImportStatus(statusTarget, "Importing.");
    peers.drafts.setFormBusy(form, true);
    button.disabled = true;
    peers.app.setStatus("Importing Git repo.", "loading");
    try {
      const response = await peers.access.teamFetch(`${API_BASE}/projects/git/import`, {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
        },
        body: JSON.stringify(payload),
      });
      if (!response.ok) {
        throw new Error(await responseText(response));
      }
      const body = await response.json();
      applyImportedGitConfig(form, body.git || body.imported?.git || {});
      const imported = body.imported || {};
      setGitImportStatus(statusTarget, imported.managed_repo_id || "Imported.");
      peers.app.setStatus("Imported Git repo. Review the populated Git fields, then save.", "ok");
      return true;
    } catch (error) {
      const message = error instanceof Error ? error.message : "Git import failed.";
      setGitImportStatus(statusTarget, message);
      peers.app.setStatus(message, "error");
      return false;
    } finally {
      peers.drafts.setFormBusy(form, false);
      button.disabled = false;
    }
  }

  function applyImportedGitConfig(form, git) {
    const fields = {
      managed_repo_id: git.managed_repo_id,
      directory_path: git.directory_path,
      base_branch: git.base_branch,
      canonical_release_branch_prefix: git.canonical_release_branch_prefix,
    };
    Object.entries(fields).forEach(([name, value]) => {
      if (value === undefined || value === null) {
        return;
      }
      const field = form.elements[name];
      if (!field) {
        return;
      }
      field.value = value;
      field.dispatchEvent(new Event("change", { bubbles: true }));
    });
  }

  function setGitImportStatus(element, message) {
    if (!element) {
      return;
    }
    element.textContent = message || "";
  }

  function gitConfigForForm(data) {
    const managedRepoId = text(data.managed_repo_id);
    if (!managedRepoId) {
      return null;
    }
    const git = { managed_repo_id: managedRepoId };
    if (data.git_recovery_configuration === "1") {
      git.recovery = {
        enabled: Boolean(data.git_recovery_enabled),
        automatic_same_branch_renewal: Boolean(data.git_recovery_renewal),
        preparation_retry_limit: Number(data.git_recovery_retry_limit),
        candidate_generation_limit: Number(data.git_recovery_candidate_limit),
      };
    }
    const directoryPath = text(data.directory_path) || "root";
    git.directory_path = directoryPath;
    const optionalFields = [
      "base_branch",
      "canonical_release_branch_prefix",
    ];
    optionalFields.forEach((field) => {
      const value = text(data[field]);
      if (value) {
        git[field] = value;
      }
    });
    const authorName = text(data.agent_author_name);
    const authorEmail = text(data.agent_author_email);
    if (authorName || authorEmail) {
      git.agent_author_identity = { name: authorName, email: authorEmail };
    }
    const committerName = text(data.agent_committer_name);
    const committerEmail = text(data.agent_committer_email);
    if (committerName || committerEmail) {
      git.agent_committer_identity = { name: committerName, email: committerEmail };
    }
    return git;
  }

  async function transferProjectLead(form) {
    if (!peers.access.guardTeamMutation("transfer project leads")) {
      return;
    }
    const projectId = text(form?.dataset?.editId);
    const project = peers.app.state.projects?.[projectId];
    const leadAgentId = text(form?.elements?.lead_agent_id?.value);
    if (!project || !leadAgentId) {
      peers.app.setStatus("Select a project lead to transfer authority.", "error");
      return;
    }
    if (leadAgentId === text(project.lead_agent_id)) {
      peers.app.setStatus("That agent is already the project lead.", "warning");
      return;
    }
    const transferred = await peers.mutations.submitMutation(
      form,
      `/projects/${encodeURIComponent(projectId)}/lead`,
      "POST",
      { lead_agent_id: leadAgentId },
      { resetOnSuccess: false },
    );
    if (transferred) {
      peers.drafts.clearFormDraft(form);
      peers.editor.closeEditDialog();
    }
  }
  function bindGitImport() {

    document.addEventListener("click", (event) => {
      const button = event.target?.closest?.("[data-git-import-button]");
      if (!button) {
        return;
      }
      event.preventDefault();
      if (!peers.access.guardTeamMutation("import Git repositories")) {
        return;
      }
      const form = button.closest("form");
      if (!form) {
        peers.app.setStatus("Git import form is unavailable.", "error");
        return;
      }
      void importGitRepoForForm(form, button);
    });
  }
  function handleLeadTransferClick(event) {
    const transferProjectLeadButton = event.target.closest("[data-transfer-project-lead]");
    if (transferProjectLeadButton) {
      event.preventDefault();
      void transferProjectLead(transferProjectLeadButton.form);
      return true;
    }
    return false;
  }

  function completeProjectCreation(form, existingProjectIds) {
    const createdProjects = values(peers.app.state.projects)
      .filter((project) => !existingProjectIds.has(project.id))
      .sort((left, right) =>
        String(right.created_at || "").localeCompare(String(left.created_at || "")) || byId(left, right),
      );
    if (form.hasAttribute("data-project-create-form")) {
      projectCreateFormOpen = false;
    }
    if (createdProjects.length) {
      selectedProjectFocusId = createdProjects[0].id;
      renderProjectFocus();
      peers.selects.refreshSelects();
    } else {
      syncProjectCreatePanel();
    }
  }

  function completeReviewProfileCreation() {
    projectReviewProfileCreateProjectId = null;
    renderProjectFocus();
  }

  return {
    completeProjectCreation,
    completeReviewProfileCreation,
    bindProjectFocus,
    bindGitImport,
    compareProjectReleasesLatest,
    compareReviewRequestsLatest,
    effectiveOutcomeKindsForProject,
    gitConfigForForm,
    handleLeadTransferClick,
    renderProjectFocus,
    renderProjectGit,
    renderProjectReleasePolicy,
    renderProjects,
    get selectedProjectFocusId() { return selectedProjectFocusId; },
  };
}

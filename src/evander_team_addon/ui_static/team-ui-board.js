// Task board scope, filters, lanes, hierarchy, and navigation.
import {
  initialTaskBoardDeepLink,
  taskBoardHierarchyPageSize,
  taskBoardLanePageSize,
  taskBoardLanes,
  taskBoardReadinessValues,
  taskBoardStateToLane,
  taskBoardViewModes,
  taskStates,
  terminalTaskStates,
} from "./team-ui-config.js";
import {
  byId,
  escapeAttr,
  escapeHtml,
  humanizeToken,
  readablePreview,
  renderSelectOptions,
  taskStateLabel,
  text,
  values,
} from "./team-ui-format.js";
import { caseBoardPermalink, caseTaskIds, replaceCaseBoardLocation } from "./team-case-board.js";

export function createTeamBoardController(peers) {

  let selectedTaskBoardTeamId = initialTaskBoardDeepLink.get("view") === "task-board"
    ? text(initialTaskBoardDeepLink.get("team_id")) : "";

  let selectedTaskBoardProjectId = initialTaskBoardDeepLink.get("view") === "task-board"
    ? text(initialTaskBoardDeepLink.get("project_id")) : "";

  let taskBoardViewMode = "board";

  let selectedTaskBoardTaskId = "";

  let taskBoardCaseId = initialTaskBoardDeepLink.get("view") === "task-board"
    ? text(initialTaskBoardDeepLink.get("case_id")) : "";

  let taskBoardShowCompleted = Boolean(taskBoardCaseId);

  let taskBoardQuery = "";

  let taskBoardAssigneeId = "";

  let taskBoardStateFilter = "";

  let taskBoardReadinessFilter = "";

  let taskBoardRootTaskId = "";

  let taskBoardCompletedSince = "";

  let taskBoardCompletedThrough = "";

  const taskBoardLanePages = new Map();

  const expandedTaskBoardTaskIds = new Set();

  const taskBoardHierarchyChildLimits = new Map();

  let taskBoardHierarchyRootLimit = taskBoardHierarchyPageSize;

  let taskBoardHierarchyInitialized = false;

  function bindTaskBoardSelectors() {
    const teamSelect = document.querySelector("[data-task-board-team]");
    const projectSelect = document.querySelector("[data-task-board-project]");
    const updateTeam = () => {
      const nextTeamId = text(teamSelect.value);
      if (nextTeamId === selectedTaskBoardTeamId) {
        return;
      }
      selectedTaskBoardTeamId = nextTeamId;
      selectedTaskBoardProjectId = "";
      resetTaskBoardProjectState();
      renderTaskBoard();
    };
    const updateProject = () => {
      const nextProjectId = text(projectSelect.value);
      if (nextProjectId === selectedTaskBoardProjectId) {
        return;
      }
      selectedTaskBoardProjectId = nextProjectId;
      resetTaskBoardProjectState();
      renderTaskBoard();
      void peers.app.loadView("task-board", { force: true, preserveForms: true });
    };
    teamSelect?.addEventListener("input", updateTeam);
    teamSelect?.addEventListener("change", updateTeam);
    projectSelect?.addEventListener("input", updateProject);
    projectSelect?.addEventListener("change", updateProject);
    document.querySelectorAll("[data-task-board-create-form]").forEach((form) => {
      syncTaskBoardTaskKindControls(form);
    });

    document.addEventListener("submit", (event) => {
      const createForm = event.target?.closest?.("[data-task-board-create-form]");
      if (createForm) {
        event.preventDefault();
        void submitTaskBoardCreateForm(createForm);
        return;
      }
      const form = event.target?.closest?.("[data-task-board-search-form]");
      if (!form) {
        return;
      }
      event.preventDefault();
      taskBoardQuery = text(new FormData(form).get("task_board_query"));
      resetTaskBoardPagination();
      renderTaskBoard();
    });

    document.addEventListener("change", (event) => {
      const taskKindSelect = event.target?.closest?.(
        '[data-task-board-create-form] [name="task_kind"]',
      );
      if (taskKindSelect) {
        syncTaskBoardTaskKindControls(taskKindSelect.form);
        return;
      }
      const completedToggle = event.target?.closest?.("[data-task-board-show-completed]");
      if (completedToggle instanceof HTMLInputElement) {
        taskBoardShowCompleted = completedToggle.checked;
        resetTaskBoardPagination();
        renderTaskBoard();
        return;
      }
      const filter = event.target?.closest?.("[data-task-board-filter]");
      if (!filter) {
        return;
      }
      const value = text(filter.value);
      switch (filter.dataset.taskBoardFilter) {
        case "case":
          selectTaskBoardCase(value);
          return;
        case "assignee":
          taskBoardAssigneeId = value;
          break;
        case "state":
          taskBoardStateFilter = taskStates.includes(value) ? value : "";
          if (terminalTaskStates.has(taskBoardStateFilter)) {
            taskBoardShowCompleted = true;
          }
          break;
        case "readiness":
          taskBoardReadinessFilter = taskBoardReadinessValues.includes(value) ? value : "";
          break;
        case "root":
          taskBoardRootTaskId = value;
          break;
        case "completed-since":
          taskBoardCompletedSince = value;
          break;
        case "completed-through":
          taskBoardCompletedThrough = value;
          break;
        default:
          return;
      }
      resetTaskBoardPagination();
      renderTaskBoard();
    });

    document.addEventListener("click", (event) => {
      const caseButton = event.target?.closest?.("[data-open-case-board]");
      if (caseButton) {
        event.preventDefault();
        selectTaskBoardCase(text(caseButton.dataset.openCaseBoard));
        peers.app.setTeamView("task-board");
        return;
      }
      if (event.target?.closest?.("[data-copy-case-board]")) {
        event.preventDefault();
        void copyCaseBoardLink();
        return;
      }
      if (event.target?.closest?.("[data-clear-case-board]")) {
        event.preventDefault();
        selectTaskBoardCase("");
        return;
      }
      const modeButton = event.target?.closest?.("[data-task-board-mode]");
      if (modeButton) {
        event.preventDefault();
        const nextMode = text(modeButton.dataset.taskBoardMode);
        if (taskBoardViewModes.has(nextMode)) {
          taskBoardViewMode = nextMode;
          prepareSelectedTaskBoardTaskForDisplay();
          renderTaskBoard();
          focusSelectedTaskBoardTask();
        }
        return;
      }
      const selectButton = event.target?.closest?.("[data-task-board-select-task-id]");
      if (selectButton) {
        event.preventDefault();
        selectTaskBoardTask(text(selectButton.dataset.taskBoardSelectTaskId), {
          reveal: selectButton.hasAttribute("data-task-board-reveal-task"),
        });
        return;
      }
      const briefButton = event.target?.closest?.("[data-task-brief-id]");
      if (briefButton) {
        event.preventDefault();
        openTaskBoardBrief(briefButton);
        return;
      }
      if (event.target?.closest?.("[data-task-board-clear-focus]")) {
        event.preventDefault();
        selectedTaskBoardTaskId = "";
        renderTaskBoard();
        return;
      }
      if (event.target?.closest?.("[data-task-board-clear-filters]")) {
        event.preventDefault();
        resetTaskBoardFilters();
        renderTaskBoard();
        return;
      }
      const hierarchyToggle = event.target?.closest?.("[data-task-board-toggle-children-id]");
      if (hierarchyToggle) {
        event.preventDefault();
        const taskId = text(hierarchyToggle.dataset.taskBoardToggleChildrenId);
        if (expandedTaskBoardTaskIds.has(taskId)) {
          expandedTaskBoardTaskIds.delete(taskId);
        } else {
          expandedTaskBoardTaskIds.add(taskId);
        }
        renderTaskBoard();
        focusTaskBoardControl(taskId, "[data-task-board-toggle-children-id]");
        return;
      }
      const lanePageButton = event.target?.closest?.("[data-task-board-lane-page]");
      if (lanePageButton) {
        event.preventDefault();
        const laneId = text(lanePageButton.dataset.taskBoardLaneId);
        const page = Math.max(1, Math.floor(Number(lanePageButton.dataset.taskBoardLanePage) || 1));
        taskBoardLanePages.set(laneId, page);
        renderTaskBoard();
        focusTaskBoardControl(laneId, "[data-task-board-lane-id]");
        return;
      }
      if (event.target?.closest?.("[data-task-board-more-roots]")) {
        event.preventDefault();
        taskBoardHierarchyRootLimit += taskBoardHierarchyPageSize;
        renderTaskBoard();
        return;
      }
      const moreChildrenButton = event.target?.closest?.("[data-task-board-more-children-id]");
      if (moreChildrenButton) {
        event.preventDefault();
        const taskId = text(moreChildrenButton.dataset.taskBoardMoreChildrenId);
        const currentLimit =
          taskBoardHierarchyChildLimits.get(taskId) || taskBoardHierarchyPageSize;
        taskBoardHierarchyChildLimits.set(taskId, currentLimit + taskBoardHierarchyPageSize);
        renderTaskBoard();
        focusTaskBoardControl(taskId, "[data-task-board-toggle-children-id]");
      }
    });
  }

  function syncTaskBoardTaskKindControls(form) {
    if (!form) return;
    const coordination = text(form.elements.task_kind?.value) === "coordination";
    for (const fieldName of [
      "integration_boundary",
      "require_git_provenance",
      "required_contribution_task_ids",
    ]) {
      const control = form.elements[fieldName];
      if (!control) continue;
      control.disabled = coordination;
      if (coordination) {
        if (control instanceof HTMLInputElement && control.type === "checkbox") {
          control.checked = false;
        } else {
          control.value = "";
        }
      }
    }
  }

  async function submitTaskBoardCreateForm(form) {
    const result = await peers.forms.submitCreateForm(form);
    if (peers.mutations.mutationResultSaved(result)) {
      renderTaskBoard();
    }
  }

  function resetTaskBoardProjectState() {
    taskBoardCaseId = "";
    persistCaseBoardLocation();
    resetTaskBoardFilters();
    selectedTaskBoardTaskId = "";
    expandedTaskBoardTaskIds.clear();
    taskBoardHierarchyChildLimits.clear();
    taskBoardHierarchyInitialized = false;
  }

  function resetTaskBoardFilters() {
    taskBoardShowCompleted = Boolean(taskBoardCaseId);
    taskBoardQuery = "";
    taskBoardAssigneeId = "";
    taskBoardStateFilter = "";
    taskBoardReadinessFilter = "";
    taskBoardRootTaskId = "";
    taskBoardCompletedSince = "";
    taskBoardCompletedThrough = "";
    resetTaskBoardPagination();
  }

  function resetTaskBoardPagination() {
    taskBoardLanePages.clear();
    taskBoardHierarchyRootLimit = taskBoardHierarchyPageSize;
    taskBoardHierarchyChildLimits.clear();
  }

  function caseBoardUrl() {
    const base = window.parent !== window ? window.parent.location.href : `${window.location.origin}/`;
    return caseBoardPermalink(base, taskBoardCaseId);
  }

  function persistCaseBoardLocation() {
    replaceCaseBoardLocation(window, taskBoardCaseId, {
      teamId: selectedTaskBoardTeamId,
      projectId: selectedTaskBoardProjectId,
    });
  }

  function selectTaskBoardCase(caseId) {
    taskBoardCaseId = caseId;
    selectedTaskBoardTaskId = "";
    resetTaskBoardFilters();
    persistCaseBoardLocation();
    renderTaskBoard();
    if (peers.app.activeTeamView === "task-board") {
      void peers.app.loadView("task-board", { force: true, preserveForms: true });
    }
  }

  async function copyCaseBoardLink() {
    try {
      await navigator.clipboard.writeText(caseBoardUrl());
      peers.app.setStatus("Case task board link copied.", "success");
    } catch {
      peers.app.setStatus(`Copy this case task board link: ${caseBoardUrl()}`, "warning");
    }
  }

  function renderTaskBoard() {
    const selectedCase = taskBoardCaseId ? peers.app.state.cases[taskBoardCaseId] : null;
    if (taskBoardCaseId) {
      const caseProject = selectedCase && peers.app.state.projects[selectedCase.project_id];
      if (!selectedCase || !peers.app.isVisibleCase(selectedCase) || !caseProject || !peers.app.isVisibleProject(caseProject)) {
        peers.app.renderIntoActiveTargets("[data-task-board-view]", `<p class="empty-state">This case is unavailable in your current scope. <button type="button" data-clear-case-board>Clear case filter</button></p>`);
        return;
      }
      selectedTaskBoardTeamId = selectedCase.team_id;
      selectedTaskBoardProjectId = selectedCase.project_id;
    }
    const visibleTeams = values(peers.app.state.teams).filter(peers.app.isVisibleTeam).sort(peers.people.comparePeopleTeams);
    const selectedTeam = visibleTeams.find((team) => text(team.id) === text(selectedTaskBoardTeamId)) || null;
    if (selectedTaskBoardTeamId && !selectedTeam) {
      selectedTaskBoardTeamId = "";
      resetTaskBoardProjectState();
    }
    const visibleProjectsForTeam = selectedTaskBoardTeamId
      ? values(peers.app.state.projects)
          .filter((project) => peers.app.isVisibleProject(project) && text(project.team_id) === text(selectedTaskBoardTeamId))
          .sort(peers.people.compareTeamProjects)
      : [];
    const selectedProject =
      visibleProjectsForTeam.find((project) => text(project.id) === text(selectedTaskBoardProjectId)) || null;
    if (selectedTaskBoardProjectId && !selectedProject) {
      selectedTaskBoardProjectId = "";
      resetTaskBoardProjectState();
    }
    syncTaskBoardSelectors(visibleTeams, visibleProjectsForTeam);

    if (!visibleTeams.length) {
      peers.app.renderIntoActiveTargets("[data-task-board-view]", `<p class="empty-state">No visible teams.</p>`);
      return;
    }
    if (!selectedTaskBoardTeamId) {
      peers.app.renderIntoActiveTargets(
        "[data-task-board-view]",
        `<p class="empty-state">Select a team and project to view its task board.</p>`,
      );
      return;
    }
    if (!visibleProjectsForTeam.length) {
      peers.app.renderIntoActiveTargets(
        "[data-task-board-view]",
        `<p class="empty-state">No visible projects for the selected team.</p>`,
      );
      return;
    }
    if (!selectedTaskBoardProjectId) {
      peers.app.renderIntoActiveTargets(
        "[data-task-board-view]",
        `<p class="empty-state">Select a project to view its task board.</p>`,
      );
      return;
    }

    const projectTasks = selectedTaskBoardProjectTasks();
    const hierarchy = buildTaskBoardHierarchyIndex(projectTasks);
    if (taskBoardRootTaskId && !hierarchy.roots.some((task) => text(task.id) === taskBoardRootTaskId)) {
      taskBoardRootTaskId = "";
    }
    if (selectedTaskBoardTaskId && !hierarchy.byId.has(selectedTaskBoardTaskId)) {
      selectedTaskBoardTaskId = "";
    }
    const matchingTasks = projectTasks
      .filter((task) => taskMatchesTaskBoardFilters(task, hierarchy))
      .sort(compareTaskBoardTasks);
    const focus = taskBoardFocusContext(hierarchy);
    const activeCount = projectTasks.filter((task) => !terminalTaskStates.has(text(task.state))).length;
    const terminalCount = projectTasks.length - activeCount;
    const blockedCount = projectTasks.filter(isTaskBoardBlocked).length;
    const dependencyWaitCount = projectTasks.filter(taskBoardWaitsOnDependencies).length;
    const boardMeta = [
      `${projectTasks.length} task${projectTasks.length === 1 ? "" : "s"}`,
      `${activeCount} active`,
      `${terminalCount} completed`,
      `${blockedCount} blocked`,
      `${dependencyWaitCount} waiting on dependencies`,
    ];
    peers.app.renderIntoActiveTargets(
      "[data-task-board-view]",
      `
        <div class="task-board-context">
          <strong>${escapeHtml(selectedCase?.title || selectedProject.name || selectedProject.id)}</strong>
          <span>${escapeHtml(selectedTeam.name || selectedTeam.id)} - ${escapeHtml(boardMeta.join(" - "))}</span>
          ${selectedCase ? `<span>Includes linked tasks and follow-up work.</span><div class="record-actions"><button type="button" class="secondary" data-copy-case-board>Copy link</button><button type="button" class="secondary" data-clear-case-board>Clear case filter</button>${renderCaseSourceLink(selectedCase)}</div>` : ""}
        </div>
        ${renderTaskBoardCreateForm(selectedProject, projectTasks)}
        ${renderTaskBoardControls(projectTasks, hierarchy, terminalCount)}
        ${renderTaskBoardFocusSummary(focus)}
        ${
          taskBoardViewMode === "hierarchy"
            ? renderTaskBoardHierarchy(projectTasks, matchingTasks, hierarchy, focus)
            : renderTaskBoardLanes(matchingTasks, focus)
        }
      `,
    );
    document.querySelectorAll("[data-task-board-view]").forEach((target) => {
      if (!peers.app.targetIsActive(target)) return;
      peers.tooltips.decorateFieldTooltips(target);
    });
    peers.access.applyLockedMutationControls();
  }

  function renderTaskBoardCreateForm(project, projectTasks) {
    const ownerOptions = values(peers.app.state.agents)
      .filter(peers.app.isVisibleAgent)
      .filter((agent) => peers.agents.agentProjectIds(agent).includes(project.id))
      .sort(peers.people.comparePeopleAgents)
      .map((agent) => [agent.id, peers.agents.agentLabel(agent)]);
    const caseOptions = values(peers.app.state.cases)
      .filter((teamCase) => (
        text(teamCase.project_id) === text(project.id)
        || (!text(teamCase.project_id) && text(teamCase.team_id) === text(project.team_id))
      ))
      .sort(byId)
      .map((teamCase) => [teamCase.id, text(teamCase.title) || teamCase.id]);
    const taskOptions = projectTasks
      .slice()
      .sort(compareTaskBoardTasks)
      .map((task) => [task.id, taskBoardTaskReferenceLabel(task.id)]);
    return `
      <details
        class="task-board-create"
        ${peers.disclosures.teamDisclosureAttributes(peers.disclosures.teamDisclosureKey("task-board", project.id, "create-task"))}
      >
        <summary>Create task</summary>
        <form
          class="team-inline-create-form task-board-create-form"
          data-task-board-create-form
          data-form-id="create-task-form"
          data-form-draft-scope="${escapeAttr(project.id)}"
          data-endpoint="/tasks"
          data-method="POST"
        >
          <input type="hidden" name="project_id" value="${escapeAttr(project.id)}" />
          <div class="team-inline-create-fields task-board-create-fields">
            <label>Owner
              <select name="owner_agent_id" required>${renderSelectOptions(ownerOptions, { placeholder: "Select project agent" })}</select>
            </label>
            <label>Case
              <select name="case_id">${renderSelectOptions(caseOptions, { selectedValue: taskBoardCaseId, placeholder: "Inherit from parent or origin" })}</select>
            </label>
            <label>Priority <input name="priority" type="number" min="0" value="0" required /></label>
            <label>Title <input name="title" required /></label>
            <label>Description <textarea name="description" rows="3" required></textarea></label>
            <label>Task kind
              <select name="task_kind">
                <option value="support">Support work</option>
                <option value="coordination">Coordination only</option>
              </select>
            </label>
            <label>Commit author name <input name="task_author_name" placeholder="Inherit task or project author" /></label>
            <label>Commit author email <input name="task_author_email" type="email" placeholder="Inherit task or project author" /></label>
            <label>Git base branch
              <input name="git_base_branch" placeholder="Inherit parent or ${escapeAttr(project.git?.base_branch || "project default")}" />
            </label>
            <label>Parent task
              <select name="parent_task_id">${renderSelectOptions(taskOptions, { placeholder: "No parent" })}</select>
            </label>
            <label>Originating task
              <select name="originating_task_id">${renderSelectOptions(taskOptions, { placeholder: "No provenance origin" })}</select>
            </label>
            <label>Dependency task IDs
              <textarea name="dependency_task_ids" rows="2" placeholder="One per line or comma-separated"></textarea>
            </label>
            <label class="checkbox-label"><input name="integration_boundary" type="checkbox" /> Integration boundary</label>
            <label class="checkbox-label"><input name="require_git_provenance" type="checkbox" /> Require Git provenance</label>
            <label>Required contribution task IDs
              <textarea name="required_contribution_task_ids" rows="2" placeholder="One per line or comma-separated"></textarea>
            </label>
          </div>
          <button type="submit" ${peers.access.lockedAttrs("create tasks")}>Create task</button>
        </form>
      </details>
    `;
  }

  function taskAssignmentKey(task) {
    return text(task.owner_agent_id) || (task.assignment_mode === "role" ? `role:${task.assignment_role_id}` : "");
  }

  function taskAssignmentLabel(task) {
    const owner = peers.app.state.agents[text(task.owner_agent_id)];
    if (owner) return peers.agents.agentLabel(owner);
    if (task.assignment_mode === "role" && !task.owner_agent_id) {
      const roleName = task.assignment_role_name || peers.app.state.roles?.[task.assignment_role_id]?.name || task.assignment_role_id;
      return `Waiting for ${roleName}`;
    }
    return text(task.owner_agent_id) || "Unavailable assignee";
  }

  function renderRoleTaskQueueStatus(task) {
    if (task.owner_agent_id || !task.assignment_wait_reason || task.state !== "pending") return "";
    const reasons = {
      waiting_for_dependencies: "Waiting for dependencies",
      waiting_for_execution_gates: "Waiting for execution prerequisites",
      waiting_for_assignment_authority: "Waiting for an authorized assignment",
      waiting_for_eligible_role_holder: "No eligible role holder",
      waiting_for_execution_capacity: "Waiting for an available agent",
    };
    const reason = reasons[task.assignment_wait_reason] || humanizeToken(task.assignment_wait_reason);
    return `<span class="state-pill neutral">${escapeHtml(reason)}</span>`;
  }

  function renderTaskBoardControls(projectTasks, hierarchy, terminalCount) {
    const caseOptions = values(peers.app.state.cases)
      .filter((item) => item.project_id === selectedTaskBoardProjectId && peers.app.isVisibleCase(item))
      .sort(peers.cases.compareCases).map((item) => [item.id, item.title]);
    const assignees = [...new Map(projectTasks
      .filter((task) => taskAssignmentKey(task))
      .map((task) => [taskAssignmentKey(task), taskAssignmentLabel(task)])).entries()]
      .sort((left, right) => left[1].localeCompare(right[1]) || left[0].localeCompare(right[0]));
    const rootOptions = hierarchy.roots.map((task) => [
      task.id,
      `${taskBoardTaskReferenceLabel(task.id)} - ${taskStateLabel(task)}`,
    ]);
    const completionFiltersEnabled = taskBoardViewMode === "hierarchy" || taskBoardShowCompleted;
    return `
      <div class="task-board-toolbar">
        <div class="task-board-mode-switch" role="tablist" aria-label="Task board view">
          ${["board", "hierarchy"].map((mode) => `
            <button
              type="button"
              class="runtime-filter-button${taskBoardViewMode === mode ? " active" : ""}"
              role="tab"
              aria-selected="${taskBoardViewMode === mode ? "true" : "false"}"
              data-task-board-mode="${mode}"
            >${mode === "board" ? "Board" : "Hierarchy"}</button>
          `).join("")}
        </div>
        ${
          taskBoardViewMode === "board"
            ? `
              <label class="task-board-completed-toggle">
                <input type="checkbox" data-task-board-show-completed${taskBoardShowCompleted ? " checked" : ""} />
                Show completed (${terminalCount})
              </label>
            `
            : `<span class="task-board-history-note">${terminalCount} completed task${terminalCount === 1 ? "" : "s"} included in hierarchy</span>`
        }
      </div>
      <details class="task-board-filter-disclosure" ${peers.disclosures.teamDisclosureAttributes("task-board-filters", { defaultOpen: window.matchMedia("(min-width: 801px)").matches })}>
      <summary>Filters${[taskBoardCaseId, taskBoardQuery, taskBoardAssigneeId, taskBoardStateFilter, taskBoardReadinessFilter, taskBoardRootTaskId, taskBoardCompletedSince, taskBoardCompletedThrough].filter(Boolean).length ? " · active" : ""}</summary>
      <form class="task-board-filters" data-task-board-search-form data-form-id="task-board-search-form" data-form-draft-scope="${escapeAttr(`${selectedTaskBoardTeamId}:${selectedTaskBoardProjectId}`)}" role="search">
        <label>Case<select data-task-board-filter="case">${renderSelectOptions(caseOptions, { selectedValue: taskBoardCaseId, placeholder: "All cases" })}</select></label>
        <label class="task-board-search-filter">
          Search
          <span class="task-board-search-controls">
            <input
              type="search"
              name="task_board_query"
              value="${escapeAttr(taskBoardQuery)}"
              placeholder="Title, description, assignee, task ID…"
            />
            <button type="submit">Search</button>
          </span>
        </label>
        <label>
          Assignee
          <select data-task-board-filter="assignee">
            ${renderSelectOptions(assignees, {
              selectedValue: taskBoardAssigneeId,
              placeholder: "Any assignee",
            })}
          </select>
        </label>
        <label>
          State
          <select data-task-board-filter="state">
            ${renderSelectOptions(
              taskStates.map((taskState) => [taskState, humanizeToken(taskState)]),
              { selectedValue: taskBoardStateFilter, placeholder: "Any state" },
            )}
          </select>
        </label>
        <label>
          Readiness
          <select data-task-board-filter="readiness">
            ${renderSelectOptions(
              taskBoardReadinessValues.map((readiness) => [readiness, humanizeToken(readiness)]),
              { selectedValue: taskBoardReadinessFilter, placeholder: "Any readiness" },
            )}
          </select>
        </label>
        <label>
          Hierarchy root
          <select data-task-board-filter="root">
            ${renderSelectOptions(rootOptions, {
              selectedValue: taskBoardRootTaskId,
              placeholder: "Any hierarchy",
            })}
          </select>
        </label>
        <label>
          Completed from
          <input
            type="date"
            value="${escapeAttr(taskBoardCompletedSince)}"
            data-task-board-filter="completed-since"
            ${completionFiltersEnabled ? "" : "disabled"}
          />
        </label>
        <label>
          Completed through
          <input
            type="date"
            value="${escapeAttr(taskBoardCompletedThrough)}"
            data-task-board-filter="completed-through"
            ${completionFiltersEnabled ? "" : "disabled"}
          />
        </label>
        <button type="button" class="secondary task-board-clear-filters" data-task-board-clear-filters>
          Clear filters
        </button>
      </form>
      </details>
    `;
  }

  function taskMatchesTaskBoardFilters(task, hierarchy) {
    if (taskBoardAssigneeId && taskAssignmentKey(task) !== taskBoardAssigneeId) {
      return false;
    }
    if (taskBoardStateFilter && text(task.state) !== taskBoardStateFilter) {
      return false;
    }
    if (taskBoardReadinessFilter && taskBoardReadiness(task) !== taskBoardReadinessFilter) {
      return false;
    }
    if (taskBoardRootTaskId && hierarchy.rootByTaskId.get(text(task.id)) !== taskBoardRootTaskId) {
      return false;
    }
    if (taskBoardCompletedSince || taskBoardCompletedThrough) {
      if (!terminalTaskStates.has(text(task.state))) {
        return false;
      }
      const completedAt = taskBoardCompletionTime(task);
      const from = dateInputLocalBoundary(taskBoardCompletedSince, false);
      const through = dateInputLocalBoundary(taskBoardCompletedThrough, true);
      if (!Number.isFinite(completedAt)) {
        return false;
      }
      if (Number.isFinite(from) && completedAt < from) {
        return false;
      }
      if (Number.isFinite(through) && completedAt > through) {
        return false;
      }
    }
    if (!taskBoardQuery) {
      return true;
    }
    const parent = hierarchy.byId.get(hierarchy.parentById.get(text(task.id))) || null;
    const root = hierarchy.byId.get(hierarchy.rootByTaskId.get(text(task.id))) || null;
    const linkedCase = peers.app.state.cases[text(task.case_id)] || null;
    const relatedTaskTitles = [
      ...(task.blocked_by_task_ids || []),
      ...(task.dependency_task_ids || []),
    ].map(taskBoardTaskReferenceLabel);
    const haystack = [
      task.id,
      task.title,
      task.description,
      task.owner_agent_id,
      task.assignment_role_id,
      taskAssignmentLabel(task),
      parent?.title,
      root?.title,
      linkedCase?.title,
      ...relatedTaskTitles,
    ]
      .map(text)
      .join("\n")
      .toLocaleLowerCase();
    return haystack.includes(taskBoardQuery.toLocaleLowerCase());
  }

  function taskBoardReadiness(task) {
    return text(task?.readiness || task?.assessment?.readiness) || "unknown";
  }

  function taskBoardCompletionTime(task) {
    const timestamp =
      (text(task?.state) === "cancelled" ? task?.cancellation?.cancelled_at : null) ||
      task?.state_entered_at ||
      task?.updated_at ||
      task?.created_at;
    if (!timestamp) {
      return Number.NaN;
    }
    const parsed = Date.parse(peers.timeline.normalizeTimelineTimestampForSort(timestamp));
    return Number.isFinite(parsed) ? parsed : Number.NaN;
  }

  function dateInputLocalBoundary(value, endOfDay) {
    const parts = text(value).split("-").map(Number);
    if (
      parts.length !== 3 ||
      !Number.isInteger(parts[0]) ||
      !Number.isInteger(parts[1]) ||
      !Number.isInteger(parts[2])
    ) {
      return Number.NaN;
    }
    return new Date(
      parts[0],
      parts[1] - 1,
      parts[2],
      endOfDay ? 23 : 0,
      endOfDay ? 59 : 0,
      endOfDay ? 59 : 0,
      endOfDay ? 999 : 0,
    ).getTime();
  }

  function taskBoardHasFilters() {
    return Boolean(
      taskBoardQuery ||
        taskBoardAssigneeId ||
        taskBoardStateFilter ||
        taskBoardReadinessFilter ||
        taskBoardRootTaskId ||
        taskBoardCompletedSince ||
        taskBoardCompletedThrough
    );
  }

  function renderTaskBoardFocusSummary(focus) {
    if (!focus.task) {
      return "";
    }
    const parts = [
      `${focus.ancestors.size} ancestor${focus.ancestors.size === 1 ? "" : "s"}`,
      `${focus.directChildren.size} direct child${focus.directChildren.size === 1 ? "" : "ren"}`,
      `${focus.descendants.size} descendant${focus.descendants.size === 1 ? "" : "s"}`,
    ];
    return `
      <div class="task-board-focus-summary" role="status">
        <span><strong>Focused:</strong> ${escapeHtml(taskBoardTaskReferenceLabel(focus.task.id))} - ${escapeHtml(parts.join(" - "))}</span>
        <button type="button" class="secondary" data-task-board-clear-focus>Clear focus</button>
      </div>
    `;
  }

  function buildTaskBoardHierarchyIndex(tasks) {
    const orderedTasks = [...tasks].sort(compareTaskBoardTasks);
    const byId = new Map(orderedTasks.map((task) => [text(task.id), task]));
    const parentById = new Map();
    const warnings = new Map();
    const addWarning = (taskId, warning) => {
      const taskWarnings = warnings.get(taskId) || [];
      if (!taskWarnings.includes(warning)) {
        taskWarnings.push(warning);
        warnings.set(taskId, taskWarnings);
      }
    };
    orderedTasks.forEach((task) => {
      const taskId = text(task.id);
      const parentId = text(task.parent_task_id);
      if (!parentId) {
        parentById.set(taskId, "");
        return;
      }
      if (parentId === taskId) {
        parentById.set(taskId, "");
        addWarning(taskId, "Task refers to itself as its parent.");
        return;
      }
      if (!byId.has(parentId)) {
        parentById.set(taskId, "");
        addWarning(taskId, "Parent task is unavailable in this project.");
        return;
      }
      parentById.set(taskId, parentId);
    });

    const resolved = new Set();
    orderedTasks.forEach((task) => {
      const startId = text(task.id);
      if (resolved.has(startId)) {
        return;
      }
      const path = [];
      const positionById = new Map();
      let currentId = startId;
      while (currentId) {
        if (positionById.has(currentId)) {
          const cycleIds = path.slice(positionById.get(currentId));
          cycleIds.forEach((taskId) => addWarning(taskId, "Task hierarchy contains a cycle."));
          const breakId = [...cycleIds].sort()[0];
          parentById.set(breakId, "");
          break;
        }
        if (resolved.has(currentId)) {
          break;
        }
        positionById.set(currentId, path.length);
        path.push(currentId);
        currentId = parentById.get(currentId) || "";
      }
      path.forEach((taskId) => resolved.add(taskId));
    });

    const childrenById = new Map(orderedTasks.map((task) => [text(task.id), []]));
    orderedTasks.forEach((task) => {
      const taskId = text(task.id);
      const parentId = parentById.get(taskId);
      if (parentId) {
        childrenById.get(parentId).push(task);
      }
    });
    childrenById.forEach((children) => children.sort(compareTaskBoardTasks));
    const roots = orderedTasks.filter((task) => !parentById.get(text(task.id)));
    const rootByTaskId = new Map();
    const assignRoot = (taskId, rootId) => {
      if (rootByTaskId.has(taskId)) {
        return;
      }
      rootByTaskId.set(taskId, rootId);
      (childrenById.get(taskId) || []).forEach((child) => assignRoot(text(child.id), rootId));
    };
    roots.forEach((task) => assignRoot(text(task.id), text(task.id)));
    return { byId, parentById, childrenById, roots, rootByTaskId, warnings };
  }

  function taskBoardFocusContext(hierarchy) {
    const task = hierarchy.byId.get(selectedTaskBoardTaskId) || null;
    const ancestors = new Set();
    const directChildren = new Set();
    const descendants = new Set();
    if (!task) {
      return { task: null, ancestors, directChildren, descendants };
    }
    let parentId = hierarchy.parentById.get(text(task.id)) || "";
    while (parentId && !ancestors.has(parentId)) {
      ancestors.add(parentId);
      parentId = hierarchy.parentById.get(parentId) || "";
    }
    (hierarchy.childrenById.get(text(task.id)) || []).forEach((child) => {
      directChildren.add(text(child.id));
    });
    const visitDescendants = (taskId) => {
      (hierarchy.childrenById.get(taskId) || []).forEach((child) => {
        const childId = text(child.id);
        if (descendants.has(childId)) {
          return;
        }
        descendants.add(childId);
        visitDescendants(childId);
      });
    };
    visitDescendants(text(task.id));
    return { task, ancestors, directChildren, descendants };
  }

  function taskBoardRelationClasses(taskId, focus) {
    const normalizedTaskId = text(taskId);
    const classes = [];
    if (text(focus.task?.id) === normalizedTaskId) {
      classes.push("task-board-task-selected");
    } else if (focus.ancestors.has(normalizedTaskId)) {
      classes.push("task-board-task-ancestor");
    } else if (focus.directChildren.has(normalizedTaskId)) {
      classes.push("task-board-task-direct-child");
    } else if (focus.descendants.has(normalizedTaskId)) {
      classes.push("task-board-task-descendant");
    }
    return classes.join(" ");
  }

  function taskBoardRelationLabel(taskId, focus) {
    const normalizedTaskId = text(taskId);
    if (text(focus.task?.id) === normalizedTaskId) {
      return "Focused";
    }
    if (focus.ancestors.has(normalizedTaskId)) {
      return "Ancestor";
    }
    if (focus.directChildren.has(normalizedTaskId)) {
      return "Direct child";
    }
    if (focus.descendants.has(normalizedTaskId)) {
      return "Descendant";
    }
    return "";
  }

  function renderTaskBoardLanes(matchingTasks, focus) {
    const lanes = taskBoardLanes
      .filter((lane) => lane.id !== "done" || taskBoardShowCompleted)
      .map((lane) => ({
        ...lane,
        tasks: matchingTasks
          .filter((task) => taskBoardLaneForTask(task) === lane.id)
          .sort(compareTaskBoardTasks),
      }));
    return `
      <div class="task-board-lanes" aria-label="Task board lanes">
        ${lanes.map((lane) => renderTaskBoardLane(lane, focus)).join("")}
      </div>
    `;
  }

  function taskBoardHierarchyDisplayContext(projectTasks, matchingTasks, hierarchy, focus) {
    const displayIds = taskBoardHasFilters()
      ? new Set(matchingTasks.map((task) => text(task.id)))
      : new Set(projectTasks.map((task) => text(task.id)));
    const forceExpandedIds = new Set();
    const includeAncestors = (taskId, forceExpanded) => {
      let parentId = hierarchy.parentById.get(taskId) || "";
      while (parentId) {
        displayIds.add(parentId);
        if (forceExpanded) {
          forceExpandedIds.add(parentId);
        }
        parentId = hierarchy.parentById.get(parentId) || "";
      }
    };
    if (taskBoardHasFilters()) {
      matchingTasks.forEach((task) => includeAncestors(text(task.id), true));
    }
    if (focus.task) {
      const focusedTaskId = text(focus.task.id);
      displayIds.add(focusedTaskId);
      includeAncestors(focusedTaskId, true);
      focus.directChildren.forEach((taskId) => displayIds.add(taskId));
    }
    return { displayIds, forceExpandedIds };
  }

  function renderTaskBoardHierarchy(projectTasks, matchingTasks, hierarchy, focus) {
    if (!taskBoardHierarchyInitialized) {
      hierarchy.roots
        .slice(0, taskBoardHierarchyRootLimit)
        .forEach((task) => expandedTaskBoardTaskIds.add(text(task.id)));
      taskBoardHierarchyInitialized = true;
    }
    const display = taskBoardHierarchyDisplayContext(projectTasks, matchingTasks, hierarchy, focus);
    const roots = hierarchy.roots.filter((task) => display.displayIds.has(text(task.id)));
    if (!roots.length) {
      return `<p class="empty-state">No tasks match the current hierarchy filters.</p>`;
    }
    const visibleRoots = roots.slice(0, taskBoardHierarchyRootLimit);
    return `
      <section class="task-board-hierarchy" aria-label="Task hierarchy">
        <div class="task-board-hierarchy-summary">
          <span>${matchingTasks.length} matching task${matchingTasks.length === 1 ? "" : "s"} across ${roots.length} hierarchy root${roots.length === 1 ? "" : "s"}.</span>
          <span>Completed history is included.</span>
        </div>
        <ol class="task-board-hierarchy-tree">
          ${visibleRoots
            .map((task) =>
              renderTaskBoardHierarchyNode(task, hierarchy, focus, display, new Set()),
            )
            .join("")}
        </ol>
        ${
          visibleRoots.length < roots.length
            ? `<button type="button" class="secondary task-board-more-hierarchy" data-task-board-more-roots>Show ${Math.min(taskBoardHierarchyPageSize, roots.length - visibleRoots.length)} more roots</button>`
            : ""
        }
      </section>
    `;
  }

  function renderTaskBoardHierarchyNode(task, hierarchy, focus, display, path) {
    const taskId = text(task.id);
    if (path.has(taskId)) {
      return "";
    }
    const nextPath = new Set(path);
    nextPath.add(taskId);
    const childTasks = (hierarchy.childrenById.get(taskId) || []).filter((child) =>
      display.displayIds.has(text(child.id)),
    );
    const expanded =
      childTasks.length > 0 &&
      (expandedTaskBoardTaskIds.has(taskId) || display.forceExpandedIds.has(taskId));
    const childLimit = taskBoardHierarchyChildLimits.get(taskId) || taskBoardHierarchyPageSize;
    const visibleChildren = expanded ? childTasks.slice(0, childLimit) : [];
    const assignedTo = taskAssignmentLabel(task);
    const readiness = taskBoardReadiness(task);
    const assessment = task.assessment || {};
    const resolution = text(task.resolution_status || assessment.resolution_status) || "unknown";
    const integrationReady = Boolean(task.integration_ready ?? assessment.integration_ready);
    const blockerCount = taskBoardBlockerCount(task);
    const upstreamBlockerCount = peers.recovery.inheritedTaskBlockers(task).length;
    const relation = taskBoardRelationLabel(taskId, focus);
    const relationClasses = taskBoardRelationClasses(taskId, focus);
    const warnings = hierarchy.warnings.get(taskId) || [];
    const worktreeContexts = peers.tasks.taskGitWorkContexts(task);
    const unresolvedIds = assessment.unresolved_descendant_ids || [];
    return `
      <li class="task-board-hierarchy-branch ${escapeAttr(relationClasses)}" data-task-board-task-id="${escapeAttr(taskId)}">
        <article class="task-board-hierarchy-node ${escapeAttr(relationClasses)}">
          <div class="task-board-hierarchy-node-header">
            ${
              childTasks.length
                ? `
                  <button
                    type="button"
                    class="task-board-hierarchy-toggle"
                    aria-expanded="${expanded ? "true" : "false"}"
                    aria-label="${expanded ? "Collapse" : "Expand"} children of ${escapeAttr(taskBoardTaskReferenceLabel(taskId))}"
                    data-task-board-toggle-children-id="${escapeAttr(taskId)}"
                  >${expanded ? "−" : "+"}</button>
                `
                : `<span class="task-board-hierarchy-toggle-spacer" aria-hidden="true"></span>`
            }
            <strong>
              <button
                type="button"
                class="task-board-task-link task-board-task-title"
                data-task-board-select-task-id="${escapeAttr(taskId)}"
                data-task-board-primary-task-id="${escapeAttr(taskId)}"
                ${text(focus.task?.id) === taskId ? 'aria-current="true"' : ""}
              >${escapeHtml(taskBoardTaskReferenceLabel(taskId))}</button>
            </strong>
            <span class="state-pill ${escapeAttr(task.state)}">${escapeHtml(taskStateLabel(task))}</span>
          </div>
          <div class="task-board-hierarchy-badges">
            <span class="state-pill ${escapeAttr(peers.tasks.taskReadinessPill(readiness))}">Readiness: ${escapeHtml(humanizeToken(readiness))}</span>
            <span class="state-pill neutral">Assignee: ${escapeHtml(assignedTo)}</span>
            <span class="state-pill ${escapeAttr(peers.tasks.taskResolutionPill(resolution))}">Resolution: ${escapeHtml(humanizeToken(resolution))}</span>
            <span class="state-pill ${integrationReady ? "accepted" : "neutral"}">Integration: ${integrationReady ? "ready" : "not ready"}</span>
            ${renderTaskBoardDependencyWaitBadge(task)}
          ${renderRoleTaskQueueStatus(task)}
            ${
              blockerCount
                ? `<span class="state-pill blocked">${blockerCount} assigned blocker${blockerCount === 1 ? "" : "s"}</span>`
                : ""
            }
            ${
              upstreamBlockerCount
                ? `<span class="state-pill warning">${upstreamBlockerCount} upstream blocker${upstreamBlockerCount === 1 ? "" : "s"}</span>`
                : ""
            }
            ${relation ? `<span class="task-board-relation-badge">${escapeHtml(relation)}</span>` : ""}
          </div>
          ${warnings.length ? `<div class="task-board-hierarchy-warning">${escapeHtml(warnings.join(" "))}</div>` : ""}
          ${peers.recovery.renderPlanningExecutionStatus(task)}
          ${renderTaskBoardBrief(task)}
          ${renderTaskBoardBlockerLine(task)}
          ${renderTaskBoardTechnicalDetails(task, unresolvedIds, worktreeContexts)}
          <div class="record-actions">
            ${renderTaskBoardBriefButton(task)}
          </div>
        </article>
        ${
          visibleChildren.length
            ? `
              <ol class="task-board-hierarchy-children">
                ${visibleChildren
                  .map((child) =>
                    renderTaskBoardHierarchyNode(child, hierarchy, focus, display, nextPath),
                  )
                  .join("")}
                ${
                  visibleChildren.length < childTasks.length
                    ? `
                      <li class="task-board-hierarchy-more">
                        <button
                          type="button"
                          class="secondary"
                          data-task-board-more-children-id="${escapeAttr(taskId)}"
                        >Show ${Math.min(taskBoardHierarchyPageSize, childTasks.length - visibleChildren.length)} more children</button>
                      </li>
                    `
                    : ""
                }
              </ol>
            `
            : ""
        }
      </li>
    `;
  }

  function taskBoardBlockerCount(task) {
    const blockerKeys = new Set();
    peers.recovery.activeBlockersForRecord("task", task.id, task.id).forEach((blocker) =>
      blockerKeys.add(`blocker:${text(blocker.id)}`),
    );
    return blockerKeys.size;
  }

  function selectedTaskBoardProjectTasks() {
    const tasks = values(peers.app.state.tasks)
      .filter((task) => text(task.project_id) === text(selectedTaskBoardProjectId));
    const members = taskBoardCaseId ? caseTaskIds(tasks, taskBoardCaseId) : null;
    return tasks.filter((task) => taskBoardLaneForTask(task) !== null && (!members || members.has(task.id)));
  }

  function renderCaseSourceLink(item) {
    const candidate = item.metadata?.work_item_url || item.source_ref;
    try {
      const url = new URL(candidate);
      if (!["https:", "http:"].includes(url.protocol)) return "";
      return `<a class="record-action secondary" href="${escapeAttr(url.href)}" target="_blank" rel="noopener noreferrer">Source work item</a>`;
    } catch { return ""; }
  }

  function selectTaskBoardTask(taskId, { reveal = false } = {}) {
    const projectTasks = selectedTaskBoardProjectTasks();
    let hierarchy = buildTaskBoardHierarchyIndex(projectTasks);
    const task = hierarchy.byId.get(text(taskId)) || null;
    if (!task) {
      return;
    }
    if (reveal && !taskMatchesTaskBoardFilters(task, hierarchy)) {
      resetTaskBoardFilters();
      hierarchy = buildTaskBoardHierarchyIndex(projectTasks);
    }
    selectedTaskBoardTaskId = text(task.id);
    revealTaskBoardHierarchyPath(task, hierarchy);
    revealTaskBoardLanePage(task, projectTasks, hierarchy);
    renderTaskBoard();
    focusSelectedTaskBoardTask();
  }

  function prepareSelectedTaskBoardTaskForDisplay() {
    if (!selectedTaskBoardTaskId) {
      return;
    }
    const projectTasks = selectedTaskBoardProjectTasks();
    const hierarchy = buildTaskBoardHierarchyIndex(projectTasks);
    const task = hierarchy.byId.get(selectedTaskBoardTaskId) || null;
    if (!task) {
      selectedTaskBoardTaskId = "";
      return;
    }
    revealTaskBoardHierarchyPath(task, hierarchy);
    revealTaskBoardLanePage(task, projectTasks, hierarchy);
  }

  function revealTaskBoardHierarchyPath(task, hierarchy) {
    const taskId = text(task.id);
    expandedTaskBoardTaskIds.add(taskId);
    const rootId = hierarchy.rootByTaskId.get(taskId);
    if (rootId) {
      const rootIndex = hierarchy.roots.findIndex((root) => text(root.id) === rootId);
      if (rootIndex >= taskBoardHierarchyRootLimit) {
        taskBoardHierarchyRootLimit =
          (Math.floor(rootIndex / taskBoardHierarchyPageSize) + 1) *
          taskBoardHierarchyPageSize;
      }
    }
    let childId = taskId;
    let parentId = hierarchy.parentById.get(childId) || "";
    while (parentId) {
      expandedTaskBoardTaskIds.add(parentId);
      const siblings = hierarchy.childrenById.get(parentId) || [];
      const childIndex = siblings.findIndex((child) => text(child.id) === childId);
      const currentLimit =
        taskBoardHierarchyChildLimits.get(parentId) || taskBoardHierarchyPageSize;
      if (childIndex >= currentLimit) {
        taskBoardHierarchyChildLimits.set(
          parentId,
          (Math.floor(childIndex / taskBoardHierarchyPageSize) + 1) *
            taskBoardHierarchyPageSize,
        );
      }
      childId = parentId;
      parentId = hierarchy.parentById.get(parentId) || "";
    }
  }

  function revealTaskBoardLanePage(task, projectTasks, hierarchy) {
    if (terminalTaskStates.has(text(task.state))) {
      taskBoardShowCompleted = true;
    }
    if (!taskMatchesTaskBoardFilters(task, hierarchy)) {
      return;
    }
    const laneId = taskBoardLaneForTask(task);
    if (!laneId) {
      return;
    }
    const laneTasks = projectTasks
      .filter((candidate) => taskMatchesTaskBoardFilters(candidate, hierarchy))
      .filter((candidate) => taskBoardLaneForTask(candidate) === laneId)
      .sort(compareTaskBoardTasks);
    const taskIndex = laneTasks.findIndex((candidate) => text(candidate.id) === text(task.id));
    if (taskIndex >= 0) {
      taskBoardLanePages.set(laneId, Math.floor(taskIndex / taskBoardLanePageSize) + 1);
    }
  }

  function focusSelectedTaskBoardTask() {
    if (!selectedTaskBoardTaskId) {
      return;
    }
    window.requestAnimationFrame(() => {
      const primary = [...document.querySelectorAll("[data-task-board-primary-task-id]")].find(
        (element) => text(element.dataset.taskBoardPrimaryTaskId) === selectedTaskBoardTaskId,
      );
      const fallback = [...document.querySelectorAll("[data-task-board-select-task-id]")].find(
        (element) => text(element.dataset.taskBoardSelectTaskId) === selectedTaskBoardTaskId,
      );
      const target = primary || fallback;
      target?.focus({ preventScroll: true });
      target
        ?.closest("[data-task-board-task-id]")
        ?.scrollIntoView({ block: "nearest", inline: "nearest" });
    });
  }

  function focusTaskBoardControl(value, selector) {
    window.requestAnimationFrame(() => {
      const target = [...document.querySelectorAll(selector)].find((element) =>
        Object.values(element.dataset).some((candidate) => text(candidate) === text(value)),
      );
      target?.focus({ preventScroll: true });
    });
  }

  function syncTaskBoardSelectors(teams, projects) {
    const teamSelect = document.querySelector("[data-task-board-team]");
    const projectSelect = document.querySelector("[data-task-board-project]");
    if (teamSelect) {
      teamSelect.dataset.currentValue = selectedTaskBoardTeamId;
      teamSelect.dataset.placeholder = "Select team";
      peers.selects.fillSelect(
        teamSelect,
        teams.map((team) => [team.id, `${team.name || team.id} (${team.id})`]),
      );
    }
    if (projectSelect) {
      projectSelect.dataset.currentValue = selectedTaskBoardProjectId;
      projectSelect.dataset.placeholder = selectedTaskBoardTeamId ? "Select project" : "Select team first";
      projectSelect.disabled = !selectedTaskBoardTeamId;
      peers.selects.fillSelect(
        projectSelect,
        projects.map((project) => [project.id, `${project.name || project.id} (${project.id})`]),
      );
    }
  }

  function renderTaskBoardLane(lane, focus) {
    const total = lane.tasks.length;
    const pageCount = Math.max(1, Math.ceil(total / taskBoardLanePageSize));
    const requestedPage = taskBoardLanePages.get(lane.id) || 1;
    const page = Math.min(pageCount, Math.max(1, requestedPage));
    taskBoardLanePages.set(lane.id, page);
    const startIndex = (page - 1) * taskBoardLanePageSize;
    const pageTasks = lane.tasks.slice(startIndex, startIndex + taskBoardLanePageSize);
    return `
      <section
        class="task-board-lane task-board-lane-${escapeAttr(lane.id)}"
        aria-label="${escapeAttr(lane.label)}"
        data-task-board-lane-id="${escapeAttr(lane.id)}"
      >
        <div class="task-board-lane-header">
          <h3>${escapeHtml(lane.label)}</h3>
          <span>${total}</span>
        </div>
        ${
          pageTasks.length
            ? `
              <ul class="task-board-card-list">
                ${pageTasks.map((task) => renderTaskBoardCard(task, focus)).join("")}
              </ul>
              ${renderTaskBoardLanePager(lane.id, page, pageCount, startIndex, total)}
            `
            : `<p class="empty-state task-board-empty">No tasks.</p>`
        }
      </section>
    `;
  }

  function renderTaskBoardLanePager(laneId, page, pageCount, startIndex, total) {
    if (!total) {
      return "";
    }
    const endIndex = Math.min(total, startIndex + taskBoardLanePageSize);
    return `
      <nav class="task-board-lane-pagination" aria-label="${escapeAttr(humanizeToken(laneId))} lane pages">
        <span>Showing ${startIndex + 1}–${endIndex} of ${total}</span>
        <div>
          <button
            type="button"
            class="secondary"
            data-task-board-lane-id="${escapeAttr(laneId)}"
            data-task-board-lane-page="${page - 1}"
            ${page <= 1 ? "disabled" : ""}
          >Previous</button>
          <span>Page ${page} of ${pageCount}</span>
          <button
            type="button"
            class="secondary"
            data-task-board-lane-id="${escapeAttr(laneId)}"
            data-task-board-lane-page="${page + 1}"
            ${page >= pageCount ? "disabled" : ""}
          >Next</button>
        </div>
      </nav>
    `;
  }

  function renderTaskBoardCard(task, focus) {
    const assignedTo = taskAssignmentLabel(task);
    const blocked = isTaskBoardBlocked(task);
    const stateLabel = taskStateLabel(task);
    const laneEntered = taskBoardLaneEnteredText(task);
    const assessment = task.assessment || {};
    const unresolvedIds = assessment.unresolved_descendant_ids || [];
    const worktreeContexts = peers.tasks.taskGitWorkContexts(task);
    const relation = taskBoardRelationLabel(task.id, focus);
    const relationClasses = taskBoardRelationClasses(task.id, focus);
    return `
      <li
        class="task-board-card${blocked ? " task-board-card-blocked" : ""}${relationClasses ? ` ${escapeAttr(relationClasses)}` : ""}"
        data-task-board-task-id="${escapeAttr(task.id)}"
      >
        <div class="task-board-card-title-row">
          <strong>
            <button
              type="button"
              class="task-board-task-link task-board-task-title"
              data-task-board-select-task-id="${escapeAttr(task.id)}"
              data-task-board-primary-task-id="${escapeAttr(task.id)}"
              ${text(focus.task?.id) === text(task.id) ? 'aria-current="true"' : ""}
            >${escapeHtml(task.title)}</button>
          </strong>
          <span class="state-pill ${escapeAttr(task.state)}">${escapeHtml(stateLabel)}</span>
        </div>
        <div class="task-board-card-badges">
          ${blocked && task.state !== "blocked" ? `<span class="state-pill blocked">Blocked</span>` : ""}
          ${renderTaskBoardDependencyWaitBadge(task)}
          ${renderRoleTaskQueueStatus(task)}
          ${relation ? `<span class="task-board-relation-badge">${escapeHtml(relation)}</span>` : ""}
        </div>
        <div class="task-board-card-meta">Priority ${Number(task.priority ?? 0)}</div>
        ${task.parent_task_id ? `<div class="task-board-card-meta">Parent: ${renderTaskBoardTaskLink(task.parent_task_id, { reveal: true })}</div>` : ""}
        ${task.originating_task_id ? `<div class="task-board-card-meta">Provenance: ${renderTaskBoardTaskLink(task.originating_task_id, { reveal: true })}</div>` : ""}
        <div class="task-board-card-meta">Assigned to: ${escapeHtml(assignedTo || "Unassigned")}</div>
        <div class="task-board-card-meta">Entered lane: ${escapeHtml(laneEntered)}</div>
        <div class="task-board-card-meta">Readiness: ${escapeHtml(task.readiness || assessment.readiness || "unknown")} - resolution: ${escapeHtml(task.resolution_status || assessment.resolution_status || "unknown")} - integration ${task.integration_ready ?? assessment.integration_ready ? "ready" : "not ready"}</div>
        ${peers.recovery.renderPlanningExecutionStatus(task)}
        ${renderTaskBoardBrief(task)}
        ${renderTaskBoardUnresolvedDescendants(task.id, unresolvedIds)}
        ${worktreeContexts.length ? `<div class="task-board-card-meta">Worktree: ${escapeHtml(worktreeContexts.map((row) => row.worktree_path || "Control-plane context").join(", "))}</div>` : ""}
        ${task.cancellation ? peers.tasks.renderTaskCancellation(task.cancellation, task) : ""}
        ${renderTaskBoardBlockerLine(task)}
        ${renderTaskBoardTechnicalDetails(task, unresolvedIds, worktreeContexts)}
        <div class="record-actions">
          ${renderTaskBoardBriefButton(task)}
          ${peers.access.editButton("task", task.id)}
          ${peers.access.taskCancellationButton(task)}
        </div>
      </li>
    `;
  }

  function renderTaskBoardTaskLink(taskId, { reveal = false } = {}) {
    const normalizedTaskId = text(taskId);
    const linkedTask = peers.app.state.tasks[normalizedTaskId] || null;
    if (!linkedTask) {
      return `<span>Unavailable task</span>`;
    }
    return `
      <button
        type="button"
        class="task-board-task-link task-board-relationship-link"
        data-task-board-select-task-id="${escapeAttr(normalizedTaskId)}"
        ${reveal ? "data-task-board-reveal-task" : ""}
      >${escapeHtml(taskBoardTaskReferenceLabel(normalizedTaskId))}</button>
    `;
  }

  function taskBoardTaskReferenceLabel(taskId) {
    const linkedTask = peers.app.state.tasks[text(taskId)];
    return text(linkedTask?.title) || "Unavailable task";
  }

  function renderTaskBoardUnresolvedDescendants(taskId, unresolvedIds) {
    const ids = [...new Set((unresolvedIds || []).map(text).filter(Boolean))];
    if (!ids.length) {
      return "";
    }
    const descendants = ids.map((id) => {
      const task = peers.app.state.tasks[id] || null;
      return {
        id,
        label: text(task?.title) || "Unavailable descendant",
        state: text(task?.state),
        stateLabel: task ? taskStateLabel(task) : "Unavailable",
      };
    });
    return `
      <details class="task-board-descendant-details" ${peers.disclosures.teamDisclosureAttributes(peers.disclosures.teamDisclosureKey("task-board", "task", taskId, "descendants"))}>
        <summary>${descendants.length} unresolved descendant${descendants.length === 1 ? "" : "s"}</summary>
        <ul class="task-board-descendant-list">
          ${descendants.map((descendant) => `
            <li>
              ${
                peers.app.state.tasks[descendant.id] && taskBoardLaneForTask(peers.app.state.tasks[descendant.id])
                  ? renderTaskBoardTaskLink(descendant.id, { reveal: true })
                  : `<span>${escapeHtml(descendant.label)}</span>`
              }
              <span class="state-pill ${escapeAttr(descendant.state || "neutral")}">${escapeHtml(descendant.stateLabel)}</span>
            </li>
          `).join("")}
        </ul>
      </details>
    `;
  }

  function renderTaskBoardTechnicalDetails(task, unresolvedIds, worktreeContexts) {
    const worktreeContextIds = worktreeContexts
      .map((row) => text(row.id || row.branch_context_id))
      .filter(Boolean);
    const blockerIds = [
      ...peers.recovery.activeBlockersForRecord("task", task.id, task.id).map((blocker) => text(blocker.id)),
      ...peers.recovery.inheritedTaskBlockers(task).map((blocker) => text(blocker.blocker_id)),
    ].filter(Boolean);
    const rows = [
      ["Task ID", text(task.id)],
      ["Hierarchy depth", String(taskHierarchyDepth(task))],
      ["Parent task ID", text(task.parent_task_id)],
      ["Originating task ID", text(task.originating_task_id)],
      ["Commit author", task.authored_by ? `${task.authored_by.name} <${task.authored_by.email}>` : "Project default"],
      ["Git base branch", text(task.git_base_branch)],
      ["Git base commit pin", text(task.git_base_commit_sha)],
      ["Baseline intent", text(task.git_constraints?.intent || "unknown")],
      ["Excluded ancestry", (task.git_constraints?.excluded_ancestor_shas || []).join(", ")],
      ["Dependency task IDs", (task.dependency_task_ids || []).map(text).filter(Boolean).join(", ")],
      ["Integration boundary", task.integration_boundary ? "Yes" : ""],
      ["Requires Git provenance", task.deliverable_contract?.require_git_provenance ? "Yes" : ""],
      ["Required contribution task IDs", (task.deliverable_contract?.required_contribution_task_ids || []).map(text).filter(Boolean).join(", ")],
      ["Unresolved dependency task IDs", taskBoardDependencyWaitIds(task).join(", ")],
      ["Blocker IDs", [...new Set(blockerIds)].join(", ")],
      ["Worktree context IDs", worktreeContextIds.join(", ")],
      ["Unresolved descendant IDs", (unresolvedIds || []).map(text).filter(Boolean).join(", ")],
    ].filter(([, value]) => value);
    return `
      <details class="technical-details task-board-technical-details" ${peers.disclosures.teamDisclosureAttributes(peers.disclosures.teamDisclosureKey("task-board", "task", task.id, "technical"))}>
        <summary>Technical details</summary>
        <dl class="technical-details-list">
          ${rows.map(([label, value]) => `
            <div>
              <dt>${escapeHtml(label)}</dt>
              <dd>${escapeHtml(value)}</dd>
            </div>
          `).join("")}
        </dl>
      </details>
    `;
  }

  function taskHierarchyDepth(task) {
    let depth = 0;
    let current = task;
    const seen = new Set([text(task?.id)]);
    while (current?.parent_task_id && depth < 32) {
      const parentId = text(current.parent_task_id);
      if (!parentId || seen.has(parentId) || !peers.app.state.tasks[parentId]) {
        break;
      }
      seen.add(parentId);
      depth += 1;
      current = peers.app.state.tasks[parentId];
    }
    return depth;
  }

  function taskBoardLaneForTask(task) {
    return taskBoardStateToLane.get(task.state) || null;
  }

  function compareTaskBoardTasks(left, right) {
    const leftPriority = Number(left.priority ?? 0);
    const rightPriority = Number(right.priority ?? 0);
    if (leftPriority !== rightPriority) {
      return leftPriority - rightPriority;
    }
    const leftEntered = taskBoardSortTime(left);
    const rightEntered = taskBoardSortTime(right);
    if (leftEntered !== rightEntered) {
      return rightEntered - leftEntered;
    }
    return (
      String(left.title || "").localeCompare(String(right.title || "")) ||
      byId(left, right)
    );
  }

  function taskBoardSortTime(task) {
    const timestamp = task.state_entered_at || task.updated_at || task.created_at;
    if (!timestamp) {
      return Number.NEGATIVE_INFINITY;
    }
    const parsed = Date.parse(peers.timeline.normalizeTimelineTimestampForSort(timestamp));
    return Number.isFinite(parsed) ? parsed : Number.NEGATIVE_INFINITY;
  }

  function taskBoardLaneEnteredText(task) {
    const timestamp = task.state_entered_at || task.updated_at || task.created_at;
    return timestamp ? peers.app.formatTimelineTimestamp(timestamp) : "Unknown";
  }

  function renderTaskBoardBrief(task) {
    const brief = text(task?.description);
    return brief
      ? `<div class="task-board-task-brief">${escapeHtml(readablePreview(brief, 160))}</div>`
      : "";
  }

  function renderTaskBoardBriefButton(task) {
    return text(task?.description)
      ? `<button type="button" class="record-action secondary" data-task-brief-id="${escapeAttr(task.id)}" aria-haspopup="dialog">Show full task brief</button>`
      : "";
  }

  function openTaskBoardBrief(button) {
    const taskId = text(button.dataset.taskBriefId);
    const task = peers.app.state.tasks[taskId];
    const brief = text(task?.description);
    if (!brief) {
      return;
    }
    const dialog = document.createElement("dialog");
    dialog.className = "task-board-brief-dialog";
    dialog.setAttribute("aria-labelledby", "task-board-brief-title");
    dialog.innerHTML = `
      <header class="task-board-brief-header">
        <h2 id="task-board-brief-title">Task brief</h2>
        <p>${escapeHtml(task.title)}</p>
      </header>
      <div class="task-board-brief-content" tabindex="0">${escapeHtml(brief)}</div>
      <footer class="task-board-brief-actions">
        <button type="button" class="secondary" autofocus>Close</button>
      </footer>
    `;
    const content = dialog.querySelector(".task-board-brief-content");
    const closeButton = dialog.querySelector("button");
    closeButton.addEventListener("click", () => dialog.close());
    dialog.addEventListener("keydown", (event) => {
      event.stopPropagation();
      if (event.key === "Tab") {
        const target = event.shiftKey ? closeButton : content;
        const boundary = event.shiftKey ? content : closeButton;
        if (document.activeElement === boundary) {
          event.preventDefault();
          target.focus();
        }
      }
    });
    dialog.addEventListener("close", () => {
      dialog.remove();
      const returnTarget = button.isConnected ? button :
        [...document.querySelectorAll("[data-task-brief-id]")].find(
          (candidate) => text(candidate.dataset.taskBriefId) === taskId,
        );
      returnTarget?.focus({ preventScroll: true });
    }, { once: true });
    document.body.append(dialog);
    dialog.showModal();
  }

  function isTaskBoardBlocked(task) {
    const readiness = taskBoardReadiness(task);
    return (
      task.state === "blocked" ||
      readiness === "blocked" ||
      readiness === "invalid" ||
      Boolean(peers.recovery.activeBlockersForRecord("task", task.id, task.id).length)
    );
  }

  function taskBoardDependencyWaitIds(task) {
    const assessmentIds = task?.assessment?.unsatisfied_dependency_ids || [];
    const derivedIds = task?.blocked_by_task_ids || [];
    return [...new Set([...assessmentIds, ...derivedIds].map(text).filter(Boolean))];
  }

  function taskBoardBlockedDependencyIds(task) {
    return taskBoardDependencyWaitIds(task).filter((taskId) =>
      ["blocked", "rejected"].includes(text(peers.app.state.tasks[taskId]?.state)),
    );
  }

  function taskBoardWaitsOnDependencies(task) {
    const readiness = taskBoardReadiness(task);
    const dependencyStatus = text(task?.dependency_status);
    const resolution = text(task?.resolution_status || task?.assessment?.resolution_status);
    if (terminalTaskStates.has(text(task?.state)) || resolution === "resolved") {
      return false;
    }
    return (
      readiness === "waiting_on_dependencies" ||
      ((dependencyStatus === "waiting" || dependencyStatus === "blocked") &&
        taskBoardDependencyWaitIds(task).length > 0)
    );
  }

  function renderTaskBoardDependencyWaitBadge(task) {
    if (!taskBoardWaitsOnDependencies(task)) {
      return "";
    }
    const count = taskBoardDependencyWaitIds(task).length;
    const dependencyLabel = count === 1 ? "dependency" : "dependencies";
    const blockedCount = taskBoardBlockedDependencyIds(task).length;
    const blockedDetail = blockedCount
      ? `${blockedCount} blocked`
      : text(task.dependency_status) === "blocked"
        ? "at least one blocked"
        : "";
    const label = `${count ? `Waiting on ${count} ${dependencyLabel}` : "Waiting on dependencies"}${
      blockedDetail ? `; ${blockedDetail}` : ""
    }`;
    return `<span class="state-pill warning">${escapeHtml(label)}</span>`;
  }

  function renderTaskBoardBlockerLine(task) {
    const blockers = [];
    const dependencyWaitIds = taskBoardDependencyWaitIds(task);
    if (taskBoardWaitsOnDependencies(task)) {
      const waitLabel = "Waiting on dependencies:";
      blockers.push(
        dependencyWaitIds.length
          ? `<span><strong>${waitLabel}</strong> ${dependencyWaitIds
              .map((taskId) => renderTaskBoardTaskLink(taskId, { reveal: true }))
              .join(", ")}</span>`
          : `<span><strong>${waitLabel}</strong> Prerequisite work is not yet resolved.</span>`,
      );
    }
    const routedBlockers = peers.recovery.activeBlockersForRecord("task", task.id, task.id);
    routedBlockers.forEach((blocker) => {
      const summary =
        text(blocker.summary || blocker.reason || blocker.details) ||
        humanizeToken(blocker.reason_code || "Open blocker");
      const owner = peers.app.state.agents[text(blocker.owner_agent_id)] || null;
      const ownerLabel = owner ? peers.agents.agentLabel(owner) : "";
      blockers.push(
        `${peers.recovery.renderGitRecoveryAction(blocker)}<span><strong>Assigned blocker:</strong> ${escapeHtml(summary)}${ownerLabel ? ` - owner ${escapeHtml(ownerLabel)}` : ""}</span>`,
      );
    });
    peers.recovery.inheritedTaskBlockers(task).forEach((blocker) => {
      const relationship = humanizeToken(blocker.relationship || "upstream");
      const reason = text(blocker.reason || blocker.summary) || "No reason supplied";
      blockers.push(
        `<span><strong>Blocker on ${escapeHtml(relationship.toLocaleLowerCase())}:</strong> ${renderTaskBoardTaskLink(
          blocker.upstream_task_id,
          { reveal: true },
        )} <span aria-hidden="true">—</span> <span class="task-board-blocker-reason">${escapeHtml(reason)}</span></span>`,
      );
    });
    if (!blockers.length) {
      return "";
    }
    return `<div class="task-board-card-blockers">${blockers.join("")}</div>`;
  }

  function openTaskBoardTask(taskId, { unavailableMessage = "This task is unavailable in the current scope." } = {}) {
    const task = peers.app.state.tasks?.[text(taskId)];
    const project = peers.app.state.projects?.[text(task?.project_id)];
    if (!task || !project || !peers.app.isVisibleProject(project)) {
      peers.app.setStatus(unavailableMessage, "warning");
      return;
    }
    resetTaskBoardProjectState();
    selectedTaskBoardTeamId = text(project.team_id);
    selectedTaskBoardProjectId = text(project.id);
    selectedTaskBoardTaskId = text(task.id);
    persistCaseBoardLocation();
    if (terminalTaskStates.has(text(task.state))) {
      taskBoardShowCompleted = true;
    }
    prepareSelectedTaskBoardTaskForDisplay();
    const refreshBoard = peers.app.hasLoadedView("task-board");
    peers.app.setTeamView("task-board");
    void peers.app.loadView("task-board", { force: refreshBoard, preserveForms: true })
      .then(() => {
        if (peers.app.activeTeamView === "task-board" && selectedTaskBoardTaskId === text(task.id)) {
          prepareSelectedTaskBoardTaskForDisplay();
          renderTaskBoard();
          focusSelectedTaskBoardTask();
        }
      })
      .catch((error) => peers.app.setStatus(error.message || "The task board could not be loaded.", "error"));
  }

  function openCandidateCorrectionRecoveryTask(taskId) {
    openTaskBoardTask(taskId, { unavailableMessage: "The verified replacement task is not visible in the current scope." });
  }

  return {
    renderTaskBoardDependencyWaitBadge,
    bindTaskBoardSelectors,
    dateInputLocalBoundary,
    openCandidateCorrectionRecoveryTask,
    openTaskBoardTask,
    renderTaskBoard,
    get selectedTaskBoardProjectId() { return selectedTaskBoardProjectId; },
    get selectedTaskBoardTeamId() { return selectedTaskBoardTeamId; },
    taskAssignmentLabel,
    get taskBoardCaseId() { return taskBoardCaseId; },
    taskBoardDependencyWaitIds,
    taskBoardWaitsOnDependencies,
  };
}

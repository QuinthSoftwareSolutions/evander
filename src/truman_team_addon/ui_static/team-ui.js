const API_BASE = "/api/addons/team-addon";

const taskStates = [
  "pending",
  "in_progress",
  "blocked",
  "waiting_for_review",
  "finished",
  "accepted",
  "rejected",
];
const recordStates = ["pending", "in_progress", "accepted", "rejected", "blocked"];
const terminalTaskStates = new Set(["finished", "accepted", "rejected"]);
const terminalRecordStates = new Set(["accepted", "rejected"]);

let state = emptyState();

const elements = {
  revisionBadge: document.querySelector("#revision-badge"),
  statusMessage: document.querySelector("#status-message"),
  summaryGrid: document.querySelector("#summary-grid"),
  reviewDeliveryView: document.querySelector("#review-delivery-view"),
  hierarchyView: document.querySelector("#hierarchy-view"),
  agentsView: document.querySelector("#agents-view"),
  projectsView: document.querySelector("#projects-view"),
  reviewProfilesView: document.querySelector("#review-profiles-view"),
  releasesView: document.querySelector("#releases-view"),
  refreshButton: document.querySelector("#refresh-button"),
};

const deliveryStatusOrder = [
  "pending",
  "waiting_for_review",
  "accepted",
  "rejected",
  "blocked",
];
const deliveryFilterOptions = new Set(["all", ...deliveryStatusOrder]);
const deliveryStatusLabels = {
  pending: "Pending",
  waiting_for_review: "Waiting for review",
  accepted: "Accepted",
  rejected: "Rejected",
  blocked: "Blocked",
  all: "All",
};

let selectedDeliveryFilter = "all";

document.addEventListener("DOMContentLoaded", () => {
  bindForms();
  bindDeliveryFilters();
  elements.refreshButton?.addEventListener("click", () => {
    void loadState({ preserveForms: true });
  });
  void loadState();
});

function emptyState() {
  return {
    revision: 0,
    teams: {},
    projects: {},
    agents: {},
    agent_runtimes: {},
    tasks: {},
    deliverables: {},
    review_requests: {},
    review_profiles: {},
    inbox: {},
    action_records: {},
    release_packages: {},
    ephemeral_assignments: {},
    git_promotions: {},
  };
}

const decisionStates = ["accepted", "rejected", "blocked"];
const inboxItemLabels = {
  question: "Question",
  status_request: "Status request",
  progress_report: "Progress report",
  deliverable_notice: "Deliverable notice",
  escalation: "Escalation",
};

function bindForms() {
  document.querySelectorAll("form[data-endpoint]").forEach((form) => {
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      void submitCreateForm(form);
    });
  });

  const updateTaskForm = document.querySelector("#update-task-form");
  updateTaskForm?.addEventListener("submit", (event) => {
    event.preventDefault();
    void submitTaskUpdate(updateTaskForm);
  });
  updateTaskForm?.elements.task_id?.addEventListener("change", () => {
    syncTaskUpdateForm(updateTaskForm);
  });

  const decideReviewForm = document.querySelector("#decide-review-form");
  decideReviewForm?.addEventListener("submit", (event) => {
    event.preventDefault();
    void submitReviewDecision(decideReviewForm);
  });
}

function bindDeliveryFilters() {
  document.querySelectorAll(".delivery-filter").forEach((button) => {
    button.addEventListener("click", () => {
      const filter = button.dataset.filter || "all";
      setDeliveryFilter(filter);
    });
  });
}

async function loadState({ preserveForms = false } = {}) {
  const drafts = preserveForms ? captureFormDrafts() : null;
  setStatus("Loading team state.", "loading");
  try {
    const response = await fetch(`${API_BASE}/state`, {
      headers: { Accept: "application/json" },
    });
    if (!response.ok) {
      throw new Error(await responseText(response));
    }
    state = normalizeState(await response.json());
    render();
    if (drafts) {
      restoreFormDrafts(drafts);
    }
    setStatus(`Loaded revision ${state.revision}.`, "ok");
  } catch (error) {
    setStatus(error instanceof Error ? error.message : "Team state failed to load.", "error");
  }
}

function normalizeState(payload) {
  return { ...emptyState(), ...(payload || {}) };
}

function render() {
  elements.revisionBadge.textContent = `Revision ${state.revision}`;
  renderSummary();
  renderReviewDeliveryWorkspace();
  renderHierarchy();
  renderAgents();
  renderProjects();
  renderReviewProfiles();
  renderReleases();
  refreshSelects();
}

function setDeliveryFilter(filter) {
  const nextFilter = deliveryFilterOptions.has(filter) ? filter : "all";
  if (selectedDeliveryFilter === nextFilter) {
    updateDeliveryFilterButtons();
    return;
  }
  selectedDeliveryFilter = nextFilter;
  renderReviewDeliveryWorkspace();
}

function updateDeliveryFilterButtons() {
  document.querySelectorAll(".delivery-filter").forEach((button) => {
    const active = (button.dataset.filter || "all") === selectedDeliveryFilter;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-pressed", active ? "true" : "false");
  });
}

function renderReviewDeliveryWorkspace() {
  if (!elements.reviewDeliveryView) {
    return;
  }
  updateDeliveryFilterButtons();
  const workspaceRows = buildReviewDeliveryRows();
  const visibleRows =
    selectedDeliveryFilter === "all"
      ? workspaceRows
      : workspaceRows.filter((row) => row.effectiveStatus === selectedDeliveryFilter);

  if (!visibleRows.length) {
    elements.reviewDeliveryView.innerHTML = `
      <p class="empty-state">
        No deliverables match the ${escapeHtml(deliveryStatusLabels[selectedDeliveryFilter] || selectedDeliveryFilter)} filter.
      </p>
    `;
    return;
  }

  const teamGroups = groupReviewDeliveryRows(visibleRows);
  const bucketStatuses =
    selectedDeliveryFilter === "all" ? deliveryStatusOrder : [selectedDeliveryFilter];

  elements.reviewDeliveryView.innerHTML = teamGroups
    .map((teamGroup) => renderDeliveryTeamGroup(teamGroup, bucketStatuses))
    .join("");
}

function renderSummary() {
  const teams = values(state.teams);
  const projects = values(state.projects);
  const agents = values(state.agents).filter((agent) => agent.status !== "archived");
  const tasks = values(state.tasks);
  const deliverables = values(state.deliverables);
  const reviews = values(state.review_requests);
  const reviewProfiles = values(state.review_profiles);
  const ephemeral = values(state.ephemeral_assignments);

  const metrics = [
    ["Teams", teams.length],
    ["Projects", projects.filter((project) => project.status !== "archived").length],
    ["Agents", agents.length],
    ["Open tasks", tasks.filter((task) => !terminalTaskStates.has(task.state)).length],
    ["Pending reviews", reviews.filter((review) => !terminalRecordStates.has(review.state)).length],
    ["Review profiles", reviewProfiles.filter((profile) => !profile.archived).length],
    ["Ephemeral", ephemeral.filter((assignment) => assignment.state !== "cleaned_up").length],
  ];

  elements.summaryGrid.innerHTML = metrics
    .map(([label, value]) => {
      return `<div class="metric"><strong>${value}</strong><span>${escapeHtml(label)}</span></div>`;
    })
    .join("");

  if (!teams.length) {
    elements.hierarchyView.innerHTML = `<p class="empty-state">No teams registered.</p>`;
  }
}

function renderHierarchy() {
  const teams = values(state.teams);
  if (!teams.length) {
    elements.hierarchyView.innerHTML = `<p class="empty-state">No teams registered.</p>`;
    return;
  }

  elements.hierarchyView.innerHTML = teams
    .map((team) => {
      const manager = state.agents[team.manager_agent_id];
      return `
        <article class="hierarchy-team">
          <p class="section-label">${escapeHtml(team.id)}</p>
          <h3>${escapeHtml(team.name)}</h3>
          ${
            manager
              ? `<ul class="hierarchy-list">${renderAgentBranch(manager.id, new Set())}</ul>`
              : `<p class="empty-state">Manager missing.</p>`
          }
        </article>
      `;
    })
    .join("");
}

function renderAgentBranch(agentId, seen) {
  const agent = state.agents[agentId];
  if (!agent || seen.has(agentId)) {
    return "";
  }
  seen.add(agentId);
  const children = (agent.subordinate_agent_ids || [])
    .map((childId) => renderAgentBranch(childId, new Set(seen)))
    .filter(Boolean)
    .join("");
  return `
    <li>
      <div class="hierarchy-node">
        <div class="agent-line">
          <strong>${escapeHtml(agent.id)}</strong>
          <span class="state-pill">${escapeHtml(agent.role)}</span>
        </div>
        <div class="meta-line">${escapeHtml(agent.session_id)}</div>
      </div>
      ${children ? `<ul>${children}</ul>` : ""}
    </li>
  `;
}

function renderAgents() {
  const agents = values(state.agents).sort(byId);
  if (!agents.length) {
    elements.agentsView.innerHTML = `<p class="empty-state">No agents registered.</p>`;
    return;
  }
  elements.agentsView.innerHTML = `
    <ul class="agent-list">
      ${agents
        .map((agent) => {
          const tasks = orderedTasksForAgent(agent.id);
          const openTasks = tasks.filter((task) => !terminalTaskStates.has(task.state));
          const maxConcurrent = agent.ephemeral_policy?.max_concurrent ?? 0;
          return `
            <li class="agent-item">
              <div class="agent-line">
                <strong>${escapeHtml(agent.id)}</strong>
                <span class="state-pill ${escapeHtml(agent.status || "active")}">${escapeHtml(agent.status || "active")}</span>
              </div>
              <div class="meta-line">${escapeHtml(agent.role)} - ${escapeHtml(agent.session_id)}</div>
              <div class="meta-line">Reports to ${escapeHtml(agent.reports_to_agent_id || "operator")} - ${openTasks.length} open task(s) - max ephemeral ${maxConcurrent}</div>
              ${renderAgentOpenTasks(openTasks)}
              ${renderAgentInbox(agent.id)}
            </li>
          `;
        })
        .join("")}
    </ul>
  `;
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
          return `<li>${escapeHtml(task.title)} <span class="state-pill ${escapeHtml(task.state)}">${escapeHtml(task.state)}</span></li>`;
        })
        .join("")}
    </ul>
  `;
}

function pendingInboxForAgent(agentId) {
  return values(state.inbox)
    .filter((item) => item.recipient_agent_id === agentId && item.state !== "handled")
    .sort(byId);
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
          return `
            <li>
              <span class="state-pill ${escapeHtml(item.item_type)}">${escapeHtml(label)}</span>
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

function renderProjects() {
  const projects = values(state.projects).sort(byId);
  if (!projects.length) {
    elements.projectsView.innerHTML = `<p class="empty-state">No projects registered.</p>`;
    return;
  }

  elements.projectsView.innerHTML = projects
    .map((project) => {
      const projectTasks = values(state.tasks)
        .filter((task) => task.project_id === project.id)
        .sort(byPriority);
      const projectDeliverables = values(state.deliverables).filter((deliverable) => {
        const task = state.tasks[deliverable.task_id];
        return task?.project_id === project.id;
      });
      return `
        <article class="project-block">
          <div class="project-header">
            <div>
              <strong>${escapeHtml(project.name)}</strong>
              <div class="meta-line">${escapeHtml(project.id)} - lead ${escapeHtml(project.lead_agent_id)}</div>
              ${renderProjectGit(project.git)}
            </div>
            <span class="state-pill ${escapeHtml(project.status)}">${escapeHtml(project.status)}</span>
          </div>
          <div class="project-columns">
            <div>
              <p class="subheading">Goals</p>
              ${renderGoals(project.goals || [])}
              <p class="subheading">Deliverables</p>
              ${renderDeliverables(projectDeliverables)}
            </div>
            <div>
              <p class="subheading">Tasks</p>
              ${renderTasks(projectTasks)}
            </div>
          </div>
        </article>
      `;
    })
    .join("");
}

function renderProjectGit(git) {
  if (!git || !git.host_repo_path) {
    return "";
  }
  const bare = git.bare_repo_name || git.bare_repo_path || "";
  const parts = [`host ${git.host_repo_path}`];
  if (bare) parts.push(`bare ${bare}`);
  if (git.base_branch) parts.push(`base ${git.base_branch}`);
  if (git.provider) parts.push(`provider ${git.provider}`);
  return `<div class="meta-line">Git: ${escapeHtml(parts.join(" - "))}</div>`;
}

function renderGoals(goals) {
  if (!goals.length) {
    return `<p class="empty-state">No goals.</p>`;
  }
  return `
    <ul class="goal-list">
      ${goals
        .map((goal) => {
          return `
            <li class="record-item">
              <div class="record-line">
                <strong>${escapeHtml(goal.description)}</strong>
                <span class="state-pill ${escapeHtml(goal.state)}">${escapeHtml(goal.state)}</span>
              </div>
              <div class="meta-line">${escapeHtml(goal.id)}</div>
            </li>
          `;
        })
        .join("")}
    </ul>
  `;
}

function renderTasks(tasks) {
  if (!tasks.length) {
    return `<p class="empty-state">No tasks.</p>`;
  }
  return `
    <ul class="task-list">
      ${tasks
        .map((task) => {
          return `
            <li class="task-item">
              <div class="task-line">
                <strong>${escapeHtml(task.title)}</strong>
                <span class="state-pill ${escapeHtml(task.state)}">${escapeHtml(task.state)}</span>
              </div>
              <div class="meta-line">${escapeHtml(task.id)} - priority ${task.priority} - owner ${escapeHtml(task.owner_agent_id)}</div>
              ${renderTaskGraph(task)}
              <div class="meta-line">${escapeHtml(task.description)}</div>
            </li>
          `;
        })
        .join("")}
    </ul>
  `;
}

function renderTaskGraph(task) {
  const lines = [];
  if (task.parent_task_id) {
    lines.push(`parent ${task.parent_task_id}`);
  }
  if ((task.dependency_task_ids || []).length) {
    lines.push(`depends on ${task.dependency_task_ids.join(", ")}`);
  }
  if ((task.blocked_by_task_ids || []).length) {
    lines.push(`blocked by ${task.blocked_by_task_ids.join(", ")}`);
  }
  if ((task.child_task_ids || []).length) {
    lines.push(`children ${task.child_task_ids.join(", ")}`);
  }
  const counts = task.subtask_counts || {};
  const subtaskLine = counts.total
    ? `subtasks ${counts.accepted || 0} accepted, ${counts.finished || 0} finished, ${counts.open || 0} open, ${counts.blocked || 0} blocked`
    : "";
  const graphLines = lines.length
    ? `<div class="meta-line">${escapeHtml(lines.join(" - "))}</div>`
    : "";
  const countLine = subtaskLine ? `<div class="meta-line">${escapeHtml(subtaskLine)}</div>` : "";
  return `
    <div class="meta-line">
      Dependency status <span class="state-pill ${escapeHtml(task.dependency_status || "satisfied")}">${escapeHtml(task.dependency_status || "satisfied")}</span>
    </div>
    ${graphLines}${countLine}
  `;
}

function renderDeliverables(deliverables) {
  if (!deliverables.length) {
    return `<p class="empty-state">No deliverables.</p>`;
  }
  return `
    <ul class="deliverable-list">
      ${deliverables
        .map((deliverable) => {
          return `
            <li class="record-item">
              <div class="record-line">
                <strong>${escapeHtml(deliverable.summary)}</strong>
                <span class="state-pill ${escapeHtml(deliverable.state)}">${escapeHtml(deliverable.state)}</span>
              </div>
              <div class="meta-line">${escapeHtml(deliverable.id)} - task ${escapeHtml(deliverable.task_id)}</div>
              ${renderReviewSummary(deliverable.review_request_ids || [])}
              ${renderArtifactRefs(deliverable.artifact_refs || [])}
              ${renderGitRefs(deliverable.git_refs)}
            </li>
          `;
        })
        .join("")}
    </ul>
  `;
}

function renderReviewSummary(reviewIds) {
  if (!reviewIds.length) {
    return "";
  }
  const reviews = reviewIds.map((reviewId) => state.review_requests[reviewId]).filter(Boolean);
  if (!reviews.length) {
    return "";
  }
  return `
    <ul class="review-list">
      ${reviews
        .map((review) => {
          return `
            <li class="record-item">
              <div class="record-line">
                <strong>${escapeHtml(review.id)}</strong>
                <span class="state-pill ${escapeHtml(review.state)}">${escapeHtml(review.state)}</span>
              </div>
              <div class="meta-line">Reviewer ${escapeHtml(review.reviewer_agent_id)}${review.review_profile_id ? ` - profile ${escapeHtml(review.review_profile_id)}` : ""}${review.decision_at ? ` - decided ${escapeHtml(review.decision_at)}` : ""}</div>
              ${review.instructions ? `<div class="meta-line">Instructions: ${escapeHtml(review.instructions)}</div>` : ""}
              ${(review.findings || []).length ? `<div class="meta-line">Findings: ${review.findings.map((finding) => escapeHtml(finding)).join("; ")}</div>` : ""}
              ${renderCriterionResults(review.criteria || [])}
            </li>
          `;
        })
        .join("")}
    </ul>
  `;
}

function renderCriterionResults(criteria) {
  if (!criteria.length) {
    return "";
  }
  return `
    <ul class="inline-list">
      ${criteria
        .map((criterion) => {
          const finding = criterion.finding ? ` - ${escapeHtml(criterion.finding)}` : "";
          return `<li>${escapeHtml(criterion.criterion_id)}: ${escapeHtml(criterion.state)}${finding}</li>`;
        })
        .join("")}
    </ul>
  `;
}

function renderArtifactRefs(refs) {
  if (!refs.length) {
    return "";
  }
  return `<div class="meta-line">Artifacts: ${refs.map((ref) => escapeHtml(ref)).join(", ")}</div>`;
}

function renderGitRefs(gitRefs, extraPromotionIds = []) {
  const refs = gitRefs || {};
  const branchLabels = [
    ["agent_branch", "agent"],
    ["review_branch", "review"],
    ["feature_branch", "feature"],
    ["release_branch", "release"],
  ];
  const parts = branchLabels
    .filter(([key]) => refs[key])
    .map(([key, label]) => `${label} ${refs[key]}`);
  if (refs.commit_sha) {
    parts.push(`commit ${refs.commit_sha}`);
  }
  const promotionIds = uniqueTextList([
    ...(refs.promotion_record_ids || []),
    ...extraPromotionIds,
  ]);
  const promotions = promotionIds
    .map((promotionId) => state.git_promotions[promotionId])
    .filter(Boolean);
  if (!parts.length && !promotions.length) {
    return "";
  }
  const branchLine = parts.length
    ? `<div class="meta-line">Git: ${escapeHtml(parts.join(" - "))}</div>`
    : "";
  const promotionLine = promotions.length
    ? `<div class="meta-line">Promotions: ${promotions
        .map(
          (promotion) =>
            `${escapeHtml(promotion.id)} ${escapeHtml(promotion.promotion_kind)}:${escapeHtml(promotion.state)}`,
        )
        .join(", ")}</div>`
    : "";
  return `${branchLine}${promotionLine}`;
}

function renderReviewProfiles() {
  if (!elements.reviewProfilesView) {
    return;
  }
  const profiles = values(state.review_profiles).sort(byId);
  if (!profiles.length) {
    elements.reviewProfilesView.innerHTML = `<p class="empty-state">No review profiles.</p>`;
    return;
  }
  elements.reviewProfilesView.innerHTML = `
    <ul class="review-list">
      ${profiles
        .map((profile) => {
          const scope = profile.project_id ? `project ${profile.project_id}` : `team ${profile.team_id}`;
          const roles = (profile.applicable_roles || []).length
            ? `roles ${profile.applicable_roles.join(", ")}`
            : "all reviewer roles";
          return `
            <li class="record-item">
              <div class="record-line">
                <strong>${escapeHtml(profile.name)}</strong>
                <span class="state-pill ${profile.archived ? "blocked" : "accepted"}">${profile.archived ? "archived" : "active"}</span>
              </div>
              <div class="meta-line">${escapeHtml(profile.id)} - ${escapeHtml(scope)} - ${escapeHtml(roles)}</div>
              ${profile.description ? `<div class="meta-line">${escapeHtml(profile.description)}</div>` : ""}
              ${renderProfileCriteria(profile.criteria || [])}
            </li>
          `;
        })
        .join("")}
    </ul>
  `;
}

function renderProfileCriteria(criteria) {
  if (!criteria.length) {
    return "";
  }
  return `
    <ul class="inline-list">
      ${criteria
        .map((criterion) => {
          const required = criterion.required === false ? "optional" : "required";
          const description = criterion.description ? ` - ${escapeHtml(criterion.description)}` : "";
          return `<li>${escapeHtml(criterion.id)}: ${escapeHtml(criterion.label)} (${required})${description}</li>`;
        })
        .join("")}
    </ul>
  `;
}

function renderReleases() {
  if (!elements.releasesView) {
    return;
  }
  const releases = values(state.release_packages).sort(byId);
  if (!releases.length) {
    elements.releasesView.innerHTML = `<p class="empty-state">No release packages.</p>`;
    return;
  }
  elements.releasesView.innerHTML = `
    <ul class="release-list">
      ${releases
        .map((release) => {
          const deliverables = (release.deliverable_ids || []).join(", ");
          const lines = [];
          if (release.release_branch) lines.push(`release ${release.release_branch}`);
          if (release.canonical_branch) lines.push(`canonical ${release.canonical_branch}`);
          if (release.published_commit_sha) lines.push(`commit ${release.published_commit_sha}`);
          return `
            <li class="record-item">
              <div class="record-line">
                <strong>${escapeHtml(release.id)}</strong>
                <span class="state-pill ${escapeHtml(release.state)}">${escapeHtml(release.state)}</span>
              </div>
              <div class="meta-line">Project ${escapeHtml(release.project_id)}${deliverables ? ` - deliverables ${escapeHtml(deliverables)}` : ""}</div>
              ${lines.length ? `<div class="meta-line">${escapeHtml(lines.join(" - "))}</div>` : ""}
              ${release.published_at ? `<div class="meta-line">Published ${escapeHtml(release.published_at)}</div>` : ""}
              ${release.error ? `<div class="meta-line">Error: ${escapeHtml(release.error)}</div>` : ""}
            </li>
          `;
        })
        .join("")}
    </ul>
  `;
}

function buildReviewDeliveryRows() {
  const taskByDeliverable = new Map();
  const projectByTask = new Map();
  const teamByProject = new Map();
  const reviewsByDeliverable = new Map();
  const releasePackagesByDeliverable = new Map();
  const gitPromotionsById = new Map();

  values(state.tasks).forEach((task) => {
    taskByDeliverable.set(task.id, task);
    projectByTask.set(task.id, state.projects[task.project_id] || null);
  });

  values(state.projects).forEach((project) => {
    teamByProject.set(project.id, state.teams[project.team_id] || null);
  });

  values(state.review_requests).forEach((review) => {
    const reviews = reviewsByDeliverable.get(review.deliverable_id) || [];
    reviews.push(review);
    reviewsByDeliverable.set(review.deliverable_id, reviews);
  });

  values(state.release_packages).forEach((release) => {
    (release.deliverable_ids || []).forEach((deliverableId) => {
      const releasePackages = releasePackagesByDeliverable.get(deliverableId) || [];
      releasePackages.push(release);
      releasePackagesByDeliverable.set(deliverableId, releasePackages);
    });
  });

  values(state.git_promotions).forEach((promotion) => {
    gitPromotionsById.set(promotion.id, promotion);
  });

  return values(state.deliverables)
    .map((deliverable) => {
      const task = taskByDeliverable.get(deliverable.task_id) || null;
      const project = task ? projectByTask.get(task.id) || null : null;
      const team = project ? teamByProject.get(project.id) || null : null;
      const reviewRequests = (reviewsByDeliverable.get(deliverable.id) || []).slice().sort(byId);
      const releasePackages = (releasePackagesByDeliverable.get(deliverable.id) || []).slice().sort(byId);
      const gitPromotionIds = [
        ...(deliverable.git_refs?.promotion_record_ids || []),
        ...releasePackages.flatMap((release) => release.git_promotion_record_ids || []),
      ];
      const gitPromotions = uniqueTextList(gitPromotionIds)
        .map((promotionId) => gitPromotionsById.get(promotionId))
        .filter(Boolean);
      const effectiveStatus = deriveDeliveryStatus(
        deliverable,
        task,
        reviewRequests,
        releasePackages,
        gitPromotions,
      );
      return {
        deliverable,
        task,
        project,
        team,
        reviewRequests,
        releasePackages,
        gitPromotions,
        effectiveStatus,
      };
    })
    .sort(compareReviewDeliveryRows);
}

function compareReviewDeliveryRows(left, right) {
  return (
    deliveryStatusRank(left.effectiveStatus) - deliveryStatusRank(right.effectiveStatus) ||
    teamSortKey(left.team).localeCompare(teamSortKey(right.team)) ||
    projectSortKey(left.project).localeCompare(projectSortKey(right.project)) ||
    byId(left.deliverable, right.deliverable)
  );
}

function deliveryStatusRank(status) {
  const index = deliveryStatusOrder.indexOf(status);
  return index === -1 ? deliveryStatusOrder.length : index;
}

function teamSortKey(team) {
  return String(team?.name || team?.id || "unknown-team");
}

function projectSortKey(project) {
  return String(project?.name || project?.id || "unknown-project");
}

function groupReviewDeliveryRows(rows) {
  const teamGroups = new Map();

  rows.forEach((row) => {
    const teamId = row.team?.id || "__unknown_team__";
    const projectId = row.project?.id || "__unknown_project__";
    let teamGroup = teamGroups.get(teamId);
    if (!teamGroup) {
      teamGroup = {
        team: row.team || null,
        projects: new Map(),
      };
      teamGroups.set(teamId, teamGroup);
    }
    let projectGroup = teamGroup.projects.get(projectId);
    if (!projectGroup) {
      projectGroup = {
        project: row.project || null,
        rows: [],
      };
      teamGroup.projects.set(projectId, projectGroup);
    }
    projectGroup.rows.push(row);
  });

  return Array.from(teamGroups.values())
    .sort((left, right) => teamSortKey(left.team).localeCompare(teamSortKey(right.team)))
    .map((teamGroup) => {
      return {
        team: teamGroup.team,
        projects: Array.from(teamGroup.projects.values())
          .sort((left, right) => projectSortKey(left.project).localeCompare(projectSortKey(right.project)))
          .map((projectGroup) => ({
            project: projectGroup.project,
            rows: projectGroup.rows.sort(compareReviewDeliveryRows),
          })),
      };
    });
}

function renderDeliveryTeamGroup(teamGroup, bucketStatuses) {
  const teamLabel = teamGroup.team
    ? `${escapeHtml(teamGroup.team.name)} (${escapeHtml(teamGroup.team.id)})`
    : "Unknown team";
  const visibleRowCount = teamGroup.projects.reduce((count, projectGroup) => {
    return count + projectGroup.rows.length;
  }, 0);
  return `
    <section class="delivery-team">
      <div class="delivery-team-title">
        <div>
          <p class="section-label">Team</p>
          <h3>${teamLabel}</h3>
        </div>
        <span class="delivery-bucket-count">${visibleRowCount} deliverable${visibleRowCount === 1 ? "" : "s"}</span>
      </div>
      <div class="delivery-project-list">
        ${teamGroup.projects.map((projectGroup) => renderDeliveryProjectGroup(projectGroup, bucketStatuses)).join("")}
      </div>
    </section>
  `;
}

function renderDeliveryProjectGroup(projectGroup, bucketStatuses) {
  const project = projectGroup.project;
  const projectLabel = project
    ? `${escapeHtml(project.name)} (${escapeHtml(project.id)})`
    : "Unknown project";
  const projectCount = projectGroup.rows.length;
  const bucketMarkup = bucketStatuses
    .map((status) => {
      const rows = projectGroup.rows.filter((row) => row.effectiveStatus === status);
      if (!rows.length) {
        return "";
      }
      return renderDeliveryBucket(status, rows);
    })
    .filter(Boolean)
    .join("");
  if (!bucketMarkup) {
    return "";
  }
  return `
    <article class="delivery-project">
      <div class="delivery-project-title">
        <div>
          <p class="section-label">Project</p>
          <h4>${projectLabel}</h4>
          ${project ? `<div class="meta-line">Team ${escapeHtml(project.team_id)}</div>` : ""}
        </div>
        <span class="delivery-bucket-count">${projectCount} deliverable${projectCount === 1 ? "" : "s"}</span>
      </div>
      <div class="delivery-buckets">
        ${bucketMarkup}
      </div>
    </article>
  `;
}

function renderDeliveryBucket(status, rows) {
  const label = deliveryStatusLabels[status] || status;
  return `
    <section class="delivery-bucket" data-status="${escapeAttr(status)}">
      <div class="delivery-bucket-title">
        <strong>${escapeHtml(label)}</strong>
        <span class="delivery-bucket-count">${rows.length} deliverable${rows.length === 1 ? "" : "s"}</span>
      </div>
      <div class="delivery-row-list">
        ${rows.map((row) => renderDeliveryRow(row)).join("")}
      </div>
    </section>
  `;
}

function renderDeliveryRow(row) {
  const deliverable = row.deliverable;
  const task = row.task;
  const project = row.project;
  const team = row.team;
  const projectLabel = project
    ? `${escapeHtml(project.name)} (${escapeHtml(project.id)})`
    : "Unknown project";
  const teamLabel = team ? `${escapeHtml(team.name)} (${escapeHtml(team.id)})` : "Unknown team";
  const reviewIds = row.reviewRequests.map((review) => review.id);
  const releaseSummaries = renderReleaseSummary(row.releasePackages);
  const gitPromotionIds = uniqueTextList([
    ...(deliverable.git_refs?.promotion_record_ids || []),
    ...row.releasePackages.flatMap((release) => release.git_promotion_record_ids || []),
  ]);
  return `
    <article class="delivery-row" data-deliverable-id="${escapeAttr(deliverable.id)}" data-status="${escapeAttr(row.effectiveStatus)}">
      <div class="delivery-row-header">
        <div class="delivery-row-title">
          <strong>${escapeHtml(deliverable.id)}</strong>
          <span class="state-pill ${escapeAttr(row.effectiveStatus)}">${escapeHtml(row.effectiveStatus)}</span>
        </div>
        <span class="delivery-bucket-count">${escapeHtml(teamLabel)} - ${escapeHtml(projectLabel)}</span>
      </div>
      <div class="delivery-row-body">
        <div class="delivery-detail-grid">
          <div>
            <div class="delivery-detail-label">Deliverable</div>
            <div class="delivery-detail-value">${escapeHtml(deliverable.id)}</div>
          </div>
          <div>
            <div class="delivery-detail-label">Task</div>
            <div class="delivery-detail-value">
              ${task ? `${escapeHtml(task.id)} - ${escapeHtml(task.title)} - ${escapeHtml(task.state)}` : "missing"}
            </div>
          </div>
          <div>
            <div class="delivery-detail-label">Producer</div>
            <div class="delivery-detail-value">${escapeHtml(deliverable.producer_agent_id)}</div>
          </div>
          <div>
            <div class="delivery-detail-label">Deliverable state</div>
            <div class="delivery-detail-value">
              <span class="state-pill ${escapeAttr(deliverable.state)}">${escapeHtml(deliverable.state)}</span>
            </div>
          </div>
        </div>
        <div class="delivery-row-meta">${escapeHtml(deliverable.summary)}</div>
        ${renderArtifactRefs(deliverable.artifact_refs || [])}
        ${renderGitRefs(deliverable.git_refs, gitPromotionIds)}
        ${
          reviewIds.length
            ? `<div class="delivery-row-reviews"><div class="meta-line">Reviews: ${escapeHtml(reviewIds.join(", "))}</div>${renderReviewSummary(reviewIds)}</div>`
            : ""
        }
        ${releaseSummaries}
      </div>
    </article>
  `;
}

function renderReleaseSummary(releasePackages) {
  if (!releasePackages.length) {
    return "";
  }
  return `
    <div class="delivery-row-releases">
      <div class="meta-line">Release packages:</div>
      <ul class="release-list">
        ${releasePackages
          .map((release) => {
            const deliverables = (release.deliverable_ids || []).join(", ");
            const promotionIds = uniqueTextList(release.git_promotion_record_ids || []);
            const promotionText = promotionIds.length ? promotionIds.join(", ") : "";
            const artifactText = (release.artifact_refs || []).length
              ? release.artifact_refs.map((ref) => escapeHtml(ref)).join(", ")
              : "";
            return `
              <li class="record-item">
                <div class="record-line">
                  <strong>${escapeHtml(release.id)}</strong>
                  <span class="state-pill ${escapeAttr(release.state)}">${escapeHtml(release.state)}</span>
                </div>
                <div class="meta-line">Project ${escapeHtml(release.project_id)}${deliverables ? ` - deliverables ${escapeHtml(deliverables)}` : ""}</div>
                ${release.release_branch ? `<div class="meta-line">Release ${escapeHtml(release.release_branch)}</div>` : ""}
                ${release.canonical_branch ? `<div class="meta-line">Canonical ${escapeHtml(release.canonical_branch)}</div>` : ""}
                ${release.published_commit_sha ? `<div class="meta-line">Commit ${escapeHtml(release.published_commit_sha)}</div>` : ""}
                ${promotionText ? `<div class="meta-line">Git promotions: ${escapeHtml(promotionText)}</div>` : ""}
                ${artifactText ? `<div class="meta-line">Artifact refs: ${artifactText}</div>` : ""}
                ${release.published_at ? `<div class="meta-line">Published ${escapeHtml(release.published_at)}</div>` : ""}
                ${release.error ? `<div class="meta-line">Error: ${escapeHtml(release.error)}</div>` : ""}
              </li>
            `;
          })
          .join("")}
      </ul>
    </div>
  `;
}

function deriveDeliveryStatus(deliverable, task, reviews, releasePackages, gitPromotions) {
  if (deliverable.state === "blocked") {
    return "blocked";
  }
  if (task && (task.state === "blocked" || task.state === "rejected")) {
    return "blocked";
  }
  if (reviews.some((review) => review.state === "blocked")) {
    return "blocked";
  }
  if (releasePackages.some((release) => release.state === "blocked" || release.state === "rejected")) {
    return "blocked";
  }
  if (gitPromotions.some((promotion) => promotion.state === "failed")) {
    return "blocked";
  }
  if (deliverable.state === "rejected" || reviews.some((review) => review.state === "rejected")) {
    return "rejected";
  }
  const reviewsPending = reviews.some((review) => review.state === "pending");
  if (deliverable.state === "accepted" && reviews.every((review) => review.state === "accepted")) {
    return "accepted";
  }
  if ((task && task.state === "waiting_for_review") || reviewsPending) {
    return "waiting_for_review";
  }
  return "pending";
}

function refreshSelects() {
  const teams = values(state.teams).sort(byId).map((team) => [team.id, `${team.name} (${team.id})`]);
  const projects = values(state.projects).sort(byId).map((project) => [project.id, `${project.name} (${project.id})`]);
  const agents = values(state.agents).sort(byId).map((agent) => [agent.id, `${agent.id} - ${agent.role}`]);
  const tasks = values(state.tasks).sort(byPriority).map((task) => [task.id, `${task.title} (${task.id})`]);
  const deliverables = values(state.deliverables).sort(byId).map((deliverable) => [deliverable.id, `${deliverable.summary} (${deliverable.id})`]);
  const reviewRequests = values(state.review_requests)
    .sort(byId)
    .map((review) => [review.id, `${review.id} - ${review.state}`]);
  const reviewProfiles = values(state.review_profiles)
    .filter((profile) => !profile.archived)
    .sort(byId)
    .map((profile) => [profile.id, `${profile.name} (${profile.id})`]);
  const releasePackages = values(state.release_packages)
    .sort(byId)
    .map((release) => [release.id, `${release.id} - ${release.state}`]);
  const options = {
    teams,
    projects,
    agents,
    tasks,
    deliverables,
    "review-profiles": reviewProfiles,
    "review-requests": reviewRequests,
    "release-packages": releasePackages,
    "task-states": taskStates.map((item) => [item, item]),
    "record-states": recordStates.map((item) => [item, item]),
    "decision-states": decisionStates.map((item) => [item, item]),
  };

  document.querySelectorAll("select[data-options]").forEach((select) => {
    const key = select.dataset.options;
    fillSelect(select, options[key] || []);
  });

  const updateTaskForm = document.querySelector("#update-task-form");
  if (updateTaskForm) {
    syncTaskUpdateForm(updateTaskForm, { keepEditedValues: true });
  }
}

function fillSelect(select, options) {
  const currentValue = select.value;
  const placeholder = select.required ? "Select" : "Default";
  select.innerHTML = `<option value="">${placeholder}</option>${options
    .map(([value, label]) => `<option value="${escapeAttr(value)}">${escapeHtml(label)}</option>`)
    .join("")}`;
  if (options.some(([value]) => value === currentValue)) {
    select.value = currentValue;
  }
}

async function submitCreateForm(form) {
  const endpoint = form.dataset.endpoint;
  const method = form.dataset.method || "POST";
  const payload = payloadForForm(form);
  await submitMutation(form, endpoint, method, payload);
}

async function submitTaskUpdate(form) {
  const data = formData(form);
  const taskId = text(data.task_id);
  if (!taskId) {
    setStatus("Task is required.", "error");
    return;
  }
  const payload = {};
  const stateValue = text(data.state);
  const priority = text(data.priority);
  const title = text(data.title);
  const description = text(data.description);
  const parentTaskId = text(data.parent_task_id);
  if (stateValue) payload.state = stateValue;
  if (priority) payload.priority = Number(priority);
  if (title) payload.title = title;
  if (description) payload.description = description;
  payload.parent_task_id = parentTaskId || null;
  payload.dependency_task_ids = splitList(data.dependency_task_ids);
  await submitMutation(form, `/tasks/${encodeURIComponent(taskId)}`, "PATCH", payload, {
    resetOnSuccess: false,
  });
}

async function submitMutation(form, endpoint, method, payload, { resetOnSuccess = true } = {}) {
  const drafts = captureFormDrafts();
  const body = { ...payload, expected_revision: state.revision };
  setFormBusy(form, true);
  setStatus("Saving.", "loading");
  try {
    const response = await fetch(`${API_BASE}${endpoint}`, {
      method,
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
    });

    if (response.status === 409) {
      const conflict = await response.json().catch(() => ({}));
      await loadState();
      restoreFormDrafts(drafts);
      const current = conflict.current_revision ?? state.revision;
      setStatus(`Revision conflict. Reloaded revision ${current}; review and submit again.`, "warning");
      return;
    }

    if (!response.ok) {
      throw new Error(await responseText(response));
    }

    state = normalizeState(await response.json());
    if (resetOnSuccess) {
      form.reset();
    }
    render();
    setStatus(`Saved revision ${state.revision}.`, "ok");
  } catch (error) {
    setStatus(error instanceof Error ? error.message : "Save failed.", "error");
  } finally {
    setFormBusy(form, false);
  }
}

function payloadForForm(form) {
  const formId = formIdentifier(form);
  const data = formData(form);
  if (formId === "create-team-form") {
    const payload = {
      id: text(data.id),
      name: text(data.name),
      manager_agent_id: text(data.manager_agent_id),
      manager_session_id: text(data.manager_session_id),
      manager_role: text(data.manager_role) || "manager",
    };
    const operatorTarget = text(data.operator_communication_target_id);
    if (operatorTarget) {
      payload.operator_communication_target_id = operatorTarget;
    }
    return payload;
  }
  if (formId === "create-project-form") {
    const goals = [];
    const goalDescription = text(data.goal_description);
    if (goalDescription) {
      goals.push({
        id: text(data.goal_id) || `${text(data.id)}-goal`,
        description: goalDescription,
      });
    }
    const payload = {
      id: text(data.id),
      team_id: text(data.team_id),
      name: text(data.name),
      lead_agent_id: text(data.lead_agent_id),
      lead_session_id: text(data.lead_session_id),
      lead_role: text(data.lead_role) || "project_lead",
      goals,
    };
    const git = gitConfigForForm(data);
    if (git) {
      payload.git = git;
    }
    return payload;
  }
  if (formId === "register-agent-form") {
    return {
      id: text(data.id),
      team_id: text(data.team_id),
      project_id: text(data.project_id),
      role: text(data.role),
      session_id: text(data.session_id),
      reports_to_agent_id: text(data.reports_to_agent_id),
      ephemeral_policy: { max_concurrent: numberOrZero(data.max_concurrent) },
    };
  }
  if (formId === "create-task-form") {
    const payload = {
      id: text(data.id),
      owner_agent_id: text(data.owner_agent_id),
      project_id: text(data.project_id),
      priority: numberOrZero(data.priority),
      title: text(data.title),
      description: text(data.description),
      created_by_agent_id: text(data.created_by_agent_id),
    };
    if (text(data.state)) {
      payload.state = text(data.state);
    }
    const parentTaskId = text(data.parent_task_id);
    if (parentTaskId) {
      payload.parent_task_id = parentTaskId;
    }
    payload.dependency_task_ids = splitList(data.dependency_task_ids);
    return payload;
  }
  if (formId === "create-review-profile-form") {
    const payload = {
      id: text(data.id),
      team_id: text(data.team_id),
      name: text(data.name),
      description: text(data.description),
      applicable_roles: splitList(data.applicable_roles),
      criteria: parseCriteria(data.criteria),
    };
    const projectId = text(data.project_id);
    if (projectId) {
      payload.project_id = projectId;
    }
    return payload;
  }
  if (formId === "record-deliverable-form") {
    const payload = {
      id: text(data.id),
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
      id: text(data.id),
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

function gitConfigForForm(data) {
  const hostRepoPath = text(data.host_repo_path);
  if (!hostRepoPath) {
    return null;
  }
  const git = { host_repo_path: hostRepoPath };
  const optionalFields = [
    "bare_repo_name",
    "bare_repo_path",
    "base_branch",
    "canonical_origin_url",
    "provider",
    "canonical_release_branch_prefix",
  ];
  optionalFields.forEach((field) => {
    const value = text(data[field]);
    if (value) {
      git[field] = value;
    }
  });
  return git;
}

async function submitReviewDecision(form) {
  const data = formData(form);
  const reviewId = text(data.review_request_id);
  if (!reviewId) {
    setStatus("Review is required.", "error");
    return;
  }
  const stateValue = text(data.state);
  if (!stateValue) {
    setStatus("Decision is required.", "error");
    return;
  }
  const payload = {
    state: stateValue,
    findings: splitList(data.findings),
    criteria: parseCriterionResults(data.criteria),
  };
  await submitMutation(
    form,
    `/review-requests/${encodeURIComponent(reviewId)}/decision`,
    "POST",
    payload,
    { resetOnSuccess: false },
  );
}

function syncTaskUpdateForm(form, { keepEditedValues = false } = {}) {
  const taskId = form.elements.task_id?.value;
  const task = taskId ? state.tasks[taskId] : null;
  if (!task) {
    return;
  }
  if (!keepEditedValues || !form.elements.state.value) {
    form.elements.state.value = task.state || "";
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
}

function formData(form) {
  return Object.fromEntries(new FormData(form).entries());
}

function formIdentifier(form) {
  return form.getAttribute("id") || "";
}

function captureFormDrafts() {
  const drafts = {};
  document.querySelectorAll("form").forEach((form) => {
    const formId = formIdentifier(form);
    if (formId) {
      drafts[formId] = formData(form);
    }
  });
  return drafts;
}

function restoreFormDrafts(drafts) {
  Object.entries(drafts || {}).forEach(([formId, values]) => {
    const form = document.getElementById(formId);
    if (!form) {
      return;
    }
    Object.entries(values).forEach(([name, value]) => {
      const field = form.elements[name];
      if (!field) {
        return;
      }
      field.value = value;
    });
  });
  const updateTaskForm = document.querySelector("#update-task-form");
  if (updateTaskForm) {
    syncTaskUpdateForm(updateTaskForm, { keepEditedValues: true });
  }
}

function setFormBusy(form, busy) {
  form.querySelectorAll("button, input, select, textarea").forEach((field) => {
    field.disabled = busy;
  });
}

function setStatus(message, level) {
  elements.statusMessage.textContent = message;
  elements.statusMessage.className = `status-message ${level || ""}`.trim();
}

async function responseText(response) {
  const textValue = await response.text();
  if (!textValue) {
    return `${response.status} ${response.statusText}`;
  }
  try {
    const payload = JSON.parse(textValue);
    return payload.error || textValue;
  } catch {
    return textValue;
  }
}

function orderedTasksForAgent(agentId) {
  return values(state.tasks)
    .filter((task) => task.owner_agent_id === agentId)
    .sort(byPriority);
}

function byId(left, right) {
  return String(left.id || "").localeCompare(String(right.id || ""));
}

function byPriority(left, right) {
  return (
    Number(left.priority ?? 0) - Number(right.priority ?? 0) ||
    String(left.created_at || "").localeCompare(String(right.created_at || "")) ||
    byId(left, right)
  );
}

function values(collection) {
  return Object.values(collection || {});
}

function text(value) {
  return String(value ?? "").trim();
}

function numberOrZero(value) {
  const parsed = Number.parseInt(text(value), 10);
  return Number.isFinite(parsed) ? parsed : 0;
}

function splitList(value) {
  return text(value)
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function uniqueTextList(values) {
  return Array.from(new Set((values || []).map((value) => text(value)).filter(Boolean)));
}

function parseCriteria(value) {
  return text(value)
    .split(/\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const [id, label, description = "", required = "true"] = line
        .split("|")
        .map((part) => part.trim());
      return {
        id,
        label: label || id,
        description,
        required: !["false", "no", "0", "optional"].includes(required.toLowerCase()),
      };
    });
}

function parseCriterionResults(value) {
  return text(value)
    .split(/\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const [criterion_id, stateValue = "passed", finding = ""] = line
        .split("|")
        .map((part) => part.trim());
      return {
        criterion_id,
        state: stateValue || "passed",
        finding,
      };
    });
}

function escapeHtml(value) {
  return text(value).replace(/[&<>"']/g, (char) => {
    return {
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    }[char];
  });
}

function escapeAttr(value) {
  return escapeHtml(value);
}

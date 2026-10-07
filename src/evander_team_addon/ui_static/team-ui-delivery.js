// Delivery filters, grouping, pipelines, and progressive evidence controls.
import { deliveryFilterOptions, deliveryStatusLabels, deliveryStatusOrder } from "./team-ui-config.js";
import {
  byId,
  escapeAttr,
  escapeHtml,
  humanizeToken,
  readablePreview,
  splitList,
  taskStateLabel,
  text,
  uniqueTextList,
  values,
} from "./team-ui-format.js";
import { renderIdentifierText } from "./team-identifiers.js";

export function createTeamDeliveryController(peers) {

  let selectedDeliveryFilter = "attention";

  let selectedDeliveryProjectId = "";

  function bindWorkspaceReviewDecisionForms() {
    document.querySelectorAll("form[data-review-decision-form]").forEach((form) => {
      if (form.dataset.boundReviewDecision === "true") {
        return;
      }
      form.dataset.boundReviewDecision = "true";
      form.addEventListener("submit", (event) => {
        event.preventDefault();
        void peers.reviews.submitReviewDecision(form);
      });
    });
  }

  function bindDeliveryFilters() {
    document.querySelectorAll(".delivery-filter").forEach((button) => {
      if (button.matches("[data-my-human-reviews]")) {
        return;
      }
      button.addEventListener("click", () => {
        const filter = button.dataset.filter || "all";
        setDeliveryFilter(filter);
      });
    });
    document.querySelector("[data-my-human-reviews]")?.addEventListener("click", () => {
      peers.humanReviews.toggleAssignedReviews();
    });
    document.addEventListener("change", (event) => {
      const releaseHistoryFilter = event.target?.closest?.("[data-release-history-filter]");
      if (releaseHistoryFilter) {
        peers.releases.changeHistoryFilter(releaseHistoryFilter);
        return;
      }
      const projectSelect = event.target?.closest?.("[data-delivery-project-filter]");
      if (!projectSelect) {
        return;
      }
      selectedDeliveryProjectId = text(projectSelect.value);
      if (selectedDeliveryProjectId) {
        peers.disclosures.openDisclosure(
          peers.disclosures.teamDisclosureStateKey(
            peers.disclosures.teamDisclosureKey("delivery", "project", selectedDeliveryProjectId),
          ),
        );
      }
      renderReviewDeliveryWorkspace();
    });
    document.addEventListener("click", (event) => {
      const copyButton = event.target?.closest?.("[data-copy-identifier]");
      if (copyButton) {
        event.preventDefault();
        void peers.reviews.copyTechnicalIdentifier(copyButton);
        return;
      }
      const retryButton = event.target?.closest?.("[data-my-human-reviews-retry]");
      if (retryButton) {
        event.preventDefault();
        void peers.humanReviews.loadMyHumanReviews();
        return;
      }
      const pipelineDetailButton = event.target?.closest?.("[data-delivery-pipeline-detail-id]");
      if (pipelineDetailButton) {
        event.preventDefault();
        const releaseId = text(pipelineDetailButton.dataset.deliveryPipelineDetailId);
        if (releaseId) {
          void peers.releases.loadReleaseDetail(releaseId);
        }
        return;
      }
      const detailButton = event.target?.closest?.("[data-release-detail-id]");
      if (detailButton) {
        event.preventDefault();
        void peers.releases.toggleReleaseDetail(text(detailButton.dataset.releaseDetailId));
        return;
      }
      if (event.target?.closest?.("[data-clear-release-history-filters]")) {
        event.preventDefault();
        peers.releases.resetReleaseHistoryFilters();
        peers.releases.renderReleases();
        return;
      }
      const resumeButton = event.target?.closest?.("[data-human-review-resume-id]");
      if (resumeButton) {
        event.preventDefault();
        if (peers.access.guardTeamMutation("resume human reviews")) {
          void peers.humanReviews.resumeHumanReview(resumeButton, text(resumeButton.dataset.humanReviewResumeId));
        }
        return;
      }
      const reassignButton = event.target?.closest?.("[data-human-review-reassign-id]");
      if (!reassignButton) {
        return;
      }
      event.preventDefault();
      if (!peers.access.guardTeamMutation("reassign human reviews")) {
        return;
      }
      const userIds = window.prompt(
        "Assign this human review to Admin Web user IDs (comma or newline separated).",
        "",
      );
      if (userIds === null) {
        return;
      }
      void peers.humanReviews.reassignHumanReview(
        reassignButton,
        text(reassignButton.dataset.humanReviewReassignId),
        splitList(userIds),
      );
    });
    document.addEventListener("submit", (event) => {
      const postReleaseReportForm = event.target?.closest?.("[data-post-release-report-form]");
      if (postReleaseReportForm) {
        event.preventDefault();
        void peers.releases.submitPostReleaseReport(postReleaseReportForm, event.submitter);
        return;
      }
      const humanReviewForm = event.target?.closest?.("[data-human-review-decision-form]");
      if (!humanReviewForm) {
        return;
      }
      event.preventDefault();
      void peers.humanReviews.submitHumanReviewDecision(humanReviewForm, event.submitter);
    });
  }

  function setDeliveryFilter(filter) {
    const nextFilter = deliveryFilterOptions.has(filter) ? filter : "attention";
    if (selectedDeliveryFilter === nextFilter) {
      updateDeliveryFilterButtons();
      return;
    }
    const drafts = peers.drafts.captureFormDrafts();
    selectedDeliveryFilter = nextFilter;
    renderReviewDeliveryWorkspace();
    peers.drafts.restoreFormDrafts(drafts);
  }

  function updateDeliveryFilterButtons() {
    document.querySelectorAll(".delivery-filter").forEach((button) => {
      if (button.matches("[data-my-human-reviews]")) {
        return;
      }
      const active = (button.dataset.filter || "all") === selectedDeliveryFilter;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-pressed", active ? "true" : "false");
    });
    updateMyHumanReviewsFilterButton();
  }

  function updateMyHumanReviewsFilterButton() {
    const button = document.querySelector("[data-my-human-reviews]");
    if (!button) {
      return;
    }
    button.classList.toggle("is-active", peers.humanReviews.myHumanReviewsOnly);
    button.setAttribute("aria-pressed", peers.humanReviews.myHumanReviewsOnly ? "true" : "false");
    const count = peers.humanReviews.humanReviewQueue.loaded ? peers.humanReviews.humanReviewQueue.requests.length : null;
    button.textContent =
      count === null ? "My Human Reviews" : `My Human Reviews (${count})`;
  }

  function renderReviewDeliveryWorkspace() {
    if (!document.querySelector("[data-review-delivery-view]")) {
      return;
    }
    updateDeliveryFilterButtons();
    const workspaceRows = buildReviewDeliveryRows();
    const statusRows =
      selectedDeliveryFilter === "all"
        ? workspaceRows
        : selectedDeliveryFilter === "attention"
          ? workspaceRows.filter(deliveryRowNeedsAttention)
          : workspaceRows.filter((row) => row.effectiveStatus === selectedDeliveryFilter);
    const visibleRows = selectedDeliveryProjectId
      ? statusRows.filter((row) => text(row.project?.id) === selectedDeliveryProjectId)
      : statusRows;
    const controls = renderDeliveryWorkspaceControls(workspaceRows, statusRows, visibleRows);

    if (!visibleRows.length) {
      peers.app.renderIntoActiveTargets("[data-review-delivery-view]", `
        ${controls}
        <p class="empty-state">
          No deliverables match the selected project and ${escapeHtml(deliveryStatusLabels[selectedDeliveryFilter] || selectedDeliveryFilter)} filter.
        </p>
      `);
      bindWorkspaceReviewDecisionForms();
      return;
    }

    const teamGroups = groupReviewDeliveryRows(visibleRows);
    const bucketStatuses =
      ["all", "attention"].includes(selectedDeliveryFilter)
        ? deliveryStatusOrder
        : [selectedDeliveryFilter];

    peers.app.renderIntoActiveTargets(
      "[data-review-delivery-view]",
      `
        ${controls}
        <div class="delivery-focused-results">
          ${teamGroups.map((teamGroup) => renderDeliveryTeamGroup(teamGroup, bucketStatuses)).join("")}
        </div>
      `,
    );
    bindWorkspaceReviewDecisionForms();
  }

  function deliveryRowNeedsAttention(row) {
    if (!peers.workspace.isCurrentDeliverable(row.deliverable)) {
      return false;
    }
    if (text(row.deliverable?.initial_release_disposition?.state) === "pending") {
      return true;
    }
    if (
      currentDeliveryReleasePackages(row.releasePackages).some(
        (release) => !["accepted", "cancelled"].includes(text(release.state)),
      )
    ) {
      return true;
    }
    if (
      ["accepted", "cancelled"].includes(text(row.task?.state)) &&
      ["rejected", "blocked"].includes(row.effectiveStatus)
    ) {
      return false;
    }
    return row.effectiveStatus !== "accepted";
  }

  function renderDeliveryWorkspaceControls(allRows, statusRows, visibleRows) {
    const projects = new Map();
    allRows.forEach((row) => {
      const projectId = text(row.project?.id);
      if (!projectId) {
        return;
      }
      let entry = projects.get(projectId);
      if (!entry) {
        entry = { project: row.project, rows: [], total: 0, attention: 0 };
        projects.set(projectId, entry);
      }
      entry.rows.push(row);
      entry.total += 1;
      if (deliveryRowNeedsAttention(row)) {
        entry.attention += 1;
      }
    });
    if (selectedDeliveryProjectId && !projects.has(selectedDeliveryProjectId)) {
      selectedDeliveryProjectId = "";
    }
    const projectOptions = [...projects.entries()]
      .sort((left, right) => projectSortKey(left[1].project).localeCompare(projectSortKey(right[1].project)))
      .map(([projectId, entry]) => {
        const count =
          selectedDeliveryFilter === "attention"
            ? entry.attention
            : selectedDeliveryFilter === "all"
              ? entry.total
              : entry.rows.filter((row) => row.effectiveStatus === selectedDeliveryFilter).length;
        const countLabel = `${count} matching`;
        return `<option value="${escapeAttr(projectId)}"${selectedDeliveryProjectId === projectId ? " selected" : ""}>${escapeHtml(entry.project?.name || projectId)} (${escapeHtml(countLabel)})</option>`;
      })
      .join("");
    const activeCount = visibleRows.filter(deliveryRowNeedsAttention).length;
    return `
      <div class="delivery-focus-bar">
        <label class="delivery-project-filter">
          <span>Focus project</span>
          <select data-delivery-project-filter>
            <option value="">All projects (collapsed)</option>
            ${projectOptions}
          </select>
        </label>
        <div class="delivery-focus-summary" role="status">
          <strong>${visibleRows.length} matching deliverable${visibleRows.length === 1 ? "" : "s"}</strong>
          <span>${activeCount} still need attention. ${selectedDeliveryProjectId ? "The selected project is expanded." : "Open a project to inspect its deliverables."}</span>
        </div>
      </div>
    `;
  }

  function buildReviewDeliveryRows() {
    const taskByDeliverable = new Map();
    const projectByTask = new Map();
    const teamByProject = new Map();
    const reviewsByDeliverable = new Map();
    const releasePackagesByDeliverable = new Map();
    const gitPromotionsById = new Map();

    values(peers.app.state.tasks).forEach((task) => {
      taskByDeliverable.set(task.id, task);
      projectByTask.set(task.id, peers.app.state.projects[task.project_id] || null);
    });

    values(peers.app.state.projects).forEach((project) => {
      teamByProject.set(project.id, peers.app.state.teams[project.team_id] || null);
    });

    values(peers.app.state.review_requests).forEach((review) => {
      const reviews = reviewsByDeliverable.get(review.deliverable_id) || [];
      reviews.push(review);
      reviewsByDeliverable.set(review.deliverable_id, reviews);
    });

    values(peers.app.state.release_packages).forEach((release) => {
      (release.deliverable_ids || []).forEach((deliverableId) => {
        const releasePackages = releasePackagesByDeliverable.get(deliverableId) || [];
        releasePackages.push(release);
        releasePackagesByDeliverable.set(deliverableId, releasePackages);
      });
    });

    values(peers.app.state.git_promotions).forEach((promotion) => {
      gitPromotionsById.set(promotion.id, promotion);
    });

    return values(peers.app.state.deliverables)
      .map((deliverable) => {
        const task = taskByDeliverable.get(deliverable.task_id) || null;
        const project = task ? projectByTask.get(task.id) || null : null;
        const team = project ? teamByProject.get(project.id) || null : null;
        const reviewRequests = (reviewsByDeliverable.get(deliverable.id) || [])
          .slice()
          .sort(peers.projects.compareReviewRequestsLatest);
        const releasePackages = (releasePackagesByDeliverable.get(deliverable.id) || [])
          .slice()
          .sort(peers.projects.compareProjectReleasesLatest);
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
      .filter((row) => peers.app.showArchived || (!peers.app.isArchivedTeam(row.team) && !peers.app.isArchivedProject(row.project)))
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
      ? escapeHtml(teamGroup.team.name)
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
            ${teamGroup.team ? peers.costs.renderTeamCostCounter(teamGroup.team.id) : ""}
            ${teamGroup.team ? peers.costs.renderTeamCostBlockedNotice(teamGroup.team.id) : ""}
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
      ? escapeHtml(project.name)
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
    const expanded = Boolean(project && selectedDeliveryProjectId === text(project.id));
    return `
      <details class="delivery-project" ${peers.disclosures.teamDisclosureAttributes(peers.disclosures.teamDisclosureKey("delivery", "project", project?.id || projectGroup.project_id || "unknown"))}>
        <summary class="delivery-project-title">
          <span class="delivery-project-heading-main">
            <span class="section-label">Project</span>
            <strong>${projectLabel}</strong>
            ${project ? `<span class="meta-line">Team ${escapeHtml(peers.app.state.teams?.[project.team_id]?.name || "Unknown team")}</span>` : ""}
          </span>
          <span class="delivery-bucket-count">${projectCount} deliverable${projectCount === 1 ? "" : "s"} · ${expanded ? "focused" : "open project"}</span>
        </summary>
        ${project ? peers.costs.renderProjectCostCounter(project.id) : ""}
        <div class="delivery-buckets delivery-project-body">
          ${bucketMarkup}
        </div>
      </details>
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
    if (peers.app.activeTeamView === "delivery") {
      return renderCompactDeliveryRow(row);
    }
    const deliverable = row.deliverable;
    const task = row.task;
    const project = row.project;
    const team = row.team;
    const projectLabel = project
      ? project.name
      : "Unknown project";
    const teamLabel = team ? team.name : "Unknown team";
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
            <strong>${escapeHtml(peers.reviews.deliverableDisplaySubject(deliverable))}</strong>
            <span class="state-pill ${escapeAttr(row.effectiveStatus)}">${escapeHtml(row.effectiveStatus)}</span>
          </div>
          <span class="delivery-bucket-count">${escapeHtml(teamLabel)} - ${escapeHtml(projectLabel)}</span>
        </div>
        <div class="delivery-row-body">
          ${renderDeliveryRowDetailBody(row, { reviewIds, releaseSummaries, gitPromotionIds })}
        </div>
      </article>
    `;
  }

  function renderCompactDeliveryRow(row) {
    const deliverable = row.deliverable;
    const task = row.task;
    const project = row.project;
    const team = row.team;
    const reviewIds = row.reviewRequests.map((review) => review.id);
    const releaseSummaries = renderReleaseSummary(row.releasePackages);
    const gitPromotionIds = uniqueTextList([
      ...(deliverable.git_refs?.promotion_record_ids || []),
      ...row.releasePackages.flatMap((release) => release.git_promotion_record_ids || []),
    ]);
    const scope = [
      team ? team.name || team.id : "Unknown team",
      project ? project.name || project.id : "Unknown project",
    ].join(" - ");
    const reviewText = `${reviewIds.length} review${reviewIds.length === 1 ? "" : "s"}`;
    const releaseText = `${row.releasePackages.length} release${row.releasePackages.length === 1 ? "" : "s"}`;
    return `
      <details class="delivery-row delivery-row-compact" data-deliverable-id="${escapeAttr(deliverable.id)}" data-status="${escapeAttr(row.effectiveStatus)}" ${peers.disclosures.teamDisclosureAttributes(peers.disclosures.teamDisclosureKey("delivery", "deliverable", deliverable.id, "detail"))}>
        <summary class="delivery-row-summary">
          <span class="delivery-summary-main">
            <strong>${escapeHtml(peers.reviews.deliverableDisplaySubject(deliverable))}</strong>
            <span>${escapeHtml(peers.reviews.deliverableProducerLabel(deliverable))} · ${peers.reviews.renderRecordDate(deliverable.created_at)}</span>
            ${deliverable.summary ? `<span class="deliverable-summary-preview">${escapeHtml(readablePreview(deliverable.summary))}</span>` : ""}
          </span>
          <span class="delivery-summary-meta">
            <span class="state-pill ${escapeAttr(row.effectiveStatus)}">${escapeHtml(row.effectiveStatus)}</span>
            <span>${escapeHtml(reviewText)}</span>
            <span>${escapeHtml(releaseText)}</span>
          </span>
        </summary>
        <div class="delivery-row-body">
          <div class="meta-line">${escapeHtml(scope)}</div>
          ${renderDeliveryRowDetailBody(row, { reviewIds, releaseSummaries, gitPromotionIds })}
        </div>
      </details>
    `;
  }

  function renderDeliveryRowDetailBody(row, { reviewIds, releaseSummaries, gitPromotionIds }) {
    const deliverable = row.deliverable;
    const task = row.task;
    return `
      ${renderDeliverablePipeline(row)}
      <section class="deliverable-detail-group">
        <h4>Summary / outcome</h4>
        <p class="deliverable-full-summary">${renderIdentifierText(deliverable.summary)}</p>
        ${peers.reviews.renderDeliverableOutcomes(deliverable)}
      </section>
      <section class="deliverable-detail-group">
        <h4>Task and producer</h4>
        <div class="delivery-detail-grid">
          <div><div class="delivery-detail-label">Task</div><div class="delivery-detail-value">${escapeHtml(task?.title || "Task unavailable")}${task ? ` <span class="state-pill ${escapeAttr(task.state)}">${escapeHtml(taskStateLabel(task))}</span>` : ""}</div></div>
          <div><div class="delivery-detail-label">Producer</div><div class="delivery-detail-value">${escapeHtml(peers.reviews.deliverableProducerLabel(deliverable))}</div></div>
          <div><div class="delivery-detail-label">Recorded</div><div class="delivery-detail-value">${peers.reviews.renderRecordDate(deliverable.created_at)}</div></div>
          <div><div class="delivery-detail-label">Deliverable state</div><div class="delivery-detail-value"><span class="state-pill ${escapeAttr(deliverable.state)}">${escapeHtml(humanizeToken(deliverable.state))}</span></div></div>
        </div>
      </section>
      <section class="deliverable-detail-group">
        <h4>Evidence and artifacts</h4>
        ${peers.reviews.renderDeliverableEvidence(deliverable)}
      </section>
      ${peers.reviews.renderDeliverableAcceptanceVerification(deliverable)}
      ${peers.reviews.renderInitialReleaseDisposition(deliverable)}
      <section class="deliverable-detail-group delivery-row-reviews">
        <h4>Reviews and decision</h4>
        ${reviewIds.length ? peers.reviews.renderReviewSummary(reviewIds, { interactive: true }) : `<p class="empty-state">No reviews recorded.</p>`}
      </section>
      ${releaseSummaries}
      <section class="deliverable-detail-group">
        <h4>Lineage / technical details</h4>
        ${peers.reviews.renderDeliverableTechnicalDetails({
          ...deliverable,
          git_refs: {
            ...(deliverable.git_refs || {}),
            promotion_record_ids: uniqueTextList([
              ...(deliverable.git_refs?.promotion_record_ids || []),
              ...gitPromotionIds,
            ]),
          },
        })}
      </section>
    `;
  }

  function currentReleaseForDeliveryRow(row) {
    if (!row.releasePackages.length) {
      return null;
    }
    const currentReleases = currentDeliveryReleasePackages(row.releasePackages);
    return (currentReleases.length ? currentReleases : row.releasePackages)
      .slice()
      .sort((left, right) => {
        const leftTimestamp = Date.parse(left.updated_at || left.created_at || "") || 0;
        const rightTimestamp = Date.parse(right.updated_at || right.created_at || "") || 0;
        return rightTimestamp - leftTimestamp || text(right.id).localeCompare(text(left.id));
      })[0];
  }

  function currentDeliveryReleasePackages(releasePackages) {
    const supersededReleaseIds = new Set(
      releasePackages
        .map((release) => text(release.supersedes_release_package_id))
        .filter(Boolean),
    );
    return releasePackages.filter(
      (release) => !supersededReleaseIds.has(text(release.id)),
    );
  }

  function deliverableAcceptanceStage(row) {
    const deliverable = row.deliverable;
    const activeReviews = row.reviewRequests.filter(peers.workspace.isCurrentReviewRequest);
    const label = activeReviews.length
      ? "Deliverable agent review"
      : "Deliverable acceptance";
    if (text(deliverable.state) === "accepted") {
      return {
        label,
        state: "complete",
        detail: activeReviews.length
          ? `${activeReviews.length} deliverable review${activeReviews.length === 1 ? "" : "s"} accepted`
          : "Deliverable accepted",
      };
    }
    const blockedReview = activeReviews.find(
      (review) => ["blocked", "rejected"].includes(text(review.state)),
    );
    if (
      ["blocked", "rejected"].includes(text(deliverable.state)) ||
      blockedReview
    ) {
      return {
        label,
        state: "blocked",
        detail: blockedReview
          ? `Deliverable review ${humanizeToken(blockedReview.state)}`
          : `Deliverable ${humanizeToken(deliverable.state)}`,
      };
    }
    if (activeReviews.length) {
      const decidedCount = activeReviews.filter((review) => text(review.state) === "accepted").length;
      return {
        label,
        state: "pending",
        detail: `${decidedCount} of ${activeReviews.length} deliverable reviews accepted`,
      };
    }
    return {
      label,
      state: "pending",
      detail:
        text(row.task?.waiting_reason) === "superior_acceptance"
          ? "Waiting for direct superior acceptance"
          : "Submission recorded; no acceptance decision yet",
    };
  }

  function deliverableEvidenceStage(deliverable) {
    const evidenceCount =
      (deliverable.artifact_refs || []).length +
      (deliverable.outcomes || []).flatMap(
        (outcome) => [...(outcome.reference_ids || []), ...(outcome.evidence_refs || [])],
      ).length;
    const hasGitEvidence = Boolean(
      text(deliverable.git_refs?.branch_context_id) ||
      text(deliverable.git_refs?.commit_sha) ||
      text(deliverable.git_refs?.feature_commit_sha),
    );
    const evidenceParts = [
      evidenceCount ? `${evidenceCount} evidence reference${evidenceCount === 1 ? "" : "s"}` : "",
      hasGitEvidence ? "Git evidence pinned" : "",
    ].filter(Boolean);
    return {
      label: "Implementation evidence",
      state: "complete",
      detail: evidenceParts.length ? evidenceParts.join(" · ") : "Deliverable submission recorded",
    };
  }

  function deliverablePipelineStages(row, release) {
    const stages = [
      deliverableEvidenceStage(row.deliverable),
      deliverableAcceptanceStage(row),
    ];
    if (!release) {
      const disposition = row.deliverable.initial_release_disposition;
      const dispositionState = text(disposition?.state);
      if (dispositionState === "pending") {
        stages.push({
          label: "Initial release decision",
          state: "waiting",
          detail: "Waiting for the project lead or team manager to request a release or mark it not planned",
        });
        return stages;
      }
      if (dispositionState === "not_planned") {
        stages.push({
          label: "Initial release decision",
          state: "complete",
          detail: disposition.reason
            ? `Not planned for an initial release: ${disposition.reason}`
            : "Not planned for an initial release",
        });
        return stages;
      }
      if (dispositionState === "release_requested") {
        stages.push({
          label: "Release inclusion",
          state: "blocked",
          detail: `Recorded release package ${text(disposition.release_package_id) || "is"} unavailable`,
        });
        return stages;
      }
      stages.push({
        label: "Release inclusion",
        state: "waiting",
        detail: "No release package includes this deliverable yet",
      });
      return stages;
    }
    const manifest = release.manifest || {};
    const candidatePinned = Boolean(
      text(release.candidate_commit_sha || manifest.candidate_commit_sha),
    );
    stages.push({
      label: "Release inclusion",
      state: "complete",
      detail: candidatePinned
        ? "Included in a release with a pinned candidate"
        : "Included in a release; candidate preparation is pending",
    });
    stages.push(
      ...peers.releases.releaseProgressStages(release).filter(
        (stage) => stage.label !== "Candidate prepared",
      ),
    );
    return stages;
  }

  function deliveryPipelineCurrentStage(stages) {
    return (
      stages.find((stage) => stage.state === "blocked") ||
      stages.find((stage) => ["pending", "waiting"].includes(stage.state)) ||
      stages[stages.length - 1]
    );
  }

  function deliveryPipelineBlockers(row, release) {
    const blockers = [
      ...peers.recovery.activeBlockersForRecord("deliverable", row.deliverable.id, row.task?.id),
      ...(release ? peers.recovery.activeBlockersForRecord("release_package", release.id) : []),
    ];
    return [...new Map(blockers.map((blocker) => [blocker.id, blocker])).values()];
  }

  function renderDeliverablePipeline(row) {
    const compactRelease = currentReleaseForDeliveryRow(row);
    const cached = compactRelease
      ? peers.releases.cachedReleaseDetail(compactRelease.id)
      : null;
    const release = cached?.release || compactRelease;
    const stages = deliverablePipelineStages(row, release);
    const currentStage = deliveryPipelineCurrentStage(stages);
    const blockers = deliveryPipelineBlockers(row, release);
    return `
      <section class="deliverable-detail-group deliverable-pipeline" aria-label="Deliverable delivery pipeline">
        <div class="deliverable-pipeline-heading">
          <div>
            <p class="section-label">Delivery pipeline</p>
            <h4>Current: ${escapeHtml(currentStage?.label || "Implementation evidence")}</h4>
            <p>${escapeHtml(currentStage?.detail || "Deliverable recorded")}</p>
          </div>
          <span class="state-pill ${escapeAttr(currentStage?.state || "pending")}">${escapeHtml(humanizeToken(currentStage?.state || "pending"))}</span>
        </div>
        ${
          blockers.length
            ? `<div class="deliverable-pipeline-blocker"><strong>Active blocker:</strong> ${escapeHtml(blockers.map((blocker) => blocker.summary || blocker.reason_code || blocker.id).join(" · "))}</div>`
            : release?.error
              ? `<div class="deliverable-pipeline-blocker"><strong>Release needs attention:</strong> ${escapeHtml(release.error)}</div>`
              : ""
        }
        <ol class="release-progress deliverable-progress" aria-label="Deliverable progress">
          ${stages.map((stage, index) => `
            <li class="release-progress-stage release-progress-${escapeAttr(stage.state)}">
              <span class="release-progress-marker" aria-hidden="true">${stage.state === "complete" ? "✓" : index + 1}</span>
              <span class="release-progress-copy">
                <strong>${escapeHtml(stage.label)}</strong>
                <span>${escapeHtml(stage.detail)}</span>
              </span>
            </li>
          `).join("")}
        </ol>
        ${renderDeliveryPipelineEvidenceControl(compactRelease, cached)}
        ${renderDeliveryPipelineGateEvidence(cached?.release)}
      </section>
    `;
  }

  function renderDeliveryPipelineEvidenceControl(release, cached) {
    if (!release) {
      return "";
    }
    if (cached?.loading) {
      return `<p class="meta-line">Loading exact release gate evidence.</p>`;
    }
    if (cached?.error) {
      return `
        <div class="task-invalid-warning">
          ${escapeHtml(cached.error)}
          <button type="button" class="record-action" data-delivery-pipeline-detail-id="${escapeAttr(release.id)}">Retry</button>
        </div>
      `;
    }
    if (cached?.release) {
      return `<p class="meta-line">Showing exact release gate evidence available to this operator.</p>`;
    }
    return `
      <div class="record-actions">
        <button type="button" class="record-action" data-delivery-pipeline-detail-id="${escapeAttr(release.id)}">
          Load exact gate evidence
        </button>
      </div>
    `;
  }

  function renderDeliveryPipelineGateEvidence(release) {
    if (!release) {
      return "";
    }
    const verificationRuns = release.verification_runs || [];
    const releaseReviews = release.release_reviews || [];
    const humanRequests = release.human_review?.requests || [];
    if (!verificationRuns.length && !releaseReviews.length && !humanRequests.length) {
      return `<p class="meta-line">No individual gate attempts have been recorded yet.</p>`;
    }
    return `
      <details class="delivery-pipeline-evidence" ${peers.disclosures.teamDisclosureAttributes(peers.disclosures.teamDisclosureKey("delivery", "release", release.id, "gate-evidence"))}>
        <summary>Gate attempts and decisions</summary>
        <ul class="delivery-detail-list">
          ${verificationRuns.map((run) => `
            <li>
              <strong>Automated verification attempt ${Number(run.attempt_number || 1)}</strong>
              <span class="state-pill ${escapeAttr(run.state)}">${escapeHtml(humanizeToken(run.state))}</span>
              ${run.failure_kind ? `<div class="meta-line">Failure type: ${escapeHtml(humanizeToken(run.failure_kind))}</div>` : ""}
              ${peers.evidence.renderReleaseVerificationCommandResults(run, `delivery:${release.id}:verification`)}
            </li>
          `).join("")}
          ${releaseReviews.map((review) => `
            <li>
              <strong>Release review by ${escapeHtml(peers.app.state.agents?.[review.reviewer_agent_id] ? peers.agents.agentLabel(peers.app.state.agents[review.reviewer_agent_id]) : "assigned reviewer")}</strong>
              <span class="state-pill ${escapeAttr(review.state)}">${escapeHtml(humanizeToken(review.state))}</span>
            </li>
          `).join("")}
          ${humanRequests.map((request) => `
            <li>
              <strong>Human review request</strong>
              <span class="state-pill ${escapeAttr(request.state)}">${escapeHtml(humanizeToken(request.state))}</span>
            </li>
          `).join("")}
        </ul>
      </details>
    `;
  }

  function renderReleaseSummary(releasePackages) {
    if (!releasePackages.length) {
      return "";
    }
    return `
      <section class="deliverable-detail-group delivery-row-releases">
        <h4>Related releases</h4>
        <ul class="release-list release-related-list">
          ${releasePackages
            .map((release) => {
              return `
                <li class="record-item release-related-card" data-release-id="${escapeAttr(release.id)}">
                  <div class="record-line">
                    <strong>${escapeHtml(peers.releases.releaseDisplayLabel(release))}</strong>
                    <span class="state-pill ${escapeAttr(release.state)}">${escapeHtml(humanizeToken(release.state))}</span>
                  </div>
                  <div class="meta-line">${escapeHtml(peers.releases.releaseProjectLabel(release))}${release.published_at ? ` · ${peers.reviews.renderRecordDate(release.published_at, "published")}` : ""}</div>
                  ${peers.releases.renderReleaseProgress(release)}
                  <details class="release-related-details" ${peers.disclosures.teamDisclosureAttributes(peers.disclosures.teamDisclosureKey("delivery", "related-release", release.id, "detail"))}>
                    <summary>Release notes, included work, and technical details</summary>
                    ${peers.releases.renderReleaseNotes(release)}
                    ${peers.releases.renderReleaseIncludedWork(release)}
                    ${peers.releases.renderReleaseTechnicalDetails(release, {
                      disclosureKey: peers.disclosures.teamDisclosureKey("delivery", "related-release", release.id, "technical"),
                    })}
                  </details>
                  ${peers.recovery.renderRecordBlockers("release_package", release.id)}
                </li>
              `;
            })
            .join("")}
        </ul>
      </section>
    `;
  }

  function deriveDeliveryStatus(deliverable, task, reviews, releasePackages, gitPromotions) {
    const currentReviews = reviews.filter(peers.workspace.isCurrentReviewRequest);
    const currentReleases = currentDeliveryReleasePackages(releasePackages);
    const currentPromotionIds = new Set(
      currentReleases.flatMap((release) => release.git_promotion_record_ids || []),
    );
    const relevantGitPromotions = currentPromotionIds.size
      ? gitPromotions.filter((promotion) => currentPromotionIds.has(text(promotion.id)))
      : gitPromotions;
    if (deliverable.state === "blocked") {
      return "blocked";
    }
    if (task && (task.state === "blocked" || task.state === "rejected")) {
      return "blocked";
    }
    if (currentReviews.some((review) => review.state === "blocked")) {
      return "blocked";
    }
    if (currentReleases.some((release) => release.state === "blocked" || release.state === "rejected")) {
      return "blocked";
    }
    if (relevantGitPromotions.some((promotion) => promotion.state === "failed")) {
      return "blocked";
    }
    if (deliverable.state === "rejected" || currentReviews.some((review) => review.state === "rejected")) {
      return "rejected";
    }
    if (
      currentReleases.some((release) =>
        ["pending", "in_progress"].includes(text(release.state)),
      )
    ) {
      return "pending";
    }
    const reviewsPending = currentReviews.some(
      (review) => review.state === "pending" || review.state === "in_progress",
    );
    if (
      deliverable.state === "accepted" &&
      currentReviews.every((review) => review.state === "accepted")
    ) {
      return "accepted";
    }
    if (
      reviewsPending ||
      (task && task.state === "waiting_for_review" && task.waiting_reason !== "superior_acceptance")
    ) {
      return "waiting_for_review";
    }
    if (task && task.state === "waiting_for_review" && task.waiting_reason === "superior_acceptance") {
      return "waiting_for_acceptance";
    }
    return "pending";
  }

  return {
    bindDeliveryFilters,
    buildReviewDeliveryRows,
    projectSortKey,
    renderReviewDeliveryWorkspace,
    teamSortKey,
    updateMyHumanReviewsFilterButton,
  };
}

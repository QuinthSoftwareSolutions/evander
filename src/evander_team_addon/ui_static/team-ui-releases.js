// Release lifecycle presentation, history, gate actions, and live evidence polling.
import {
  escapeAttr,
  escapeHtml,
  formData,
  humanizeToken,
  readablePreview,
  renderSelectOptions,
  responseText,
  splitList,
  text,
  uniqueTextList,
  values,
} from "./team-ui-format.js";
import {
  API_BASE,
  RELEASE_GATE_LABELS,
  RELEASE_VERIFICATION_LIVE_FOLLOW_THRESHOLD_PX,
  RELEASE_VERIFICATION_LIVE_POLL_MS,
  releaseHistoryStates,
} from "./team-ui-config.js";
import { renderIdentifierText } from "./team-identifiers.js";

export function createTeamReleasesController(peers) {

  let selectedReleaseHistoryState = "";

  let releaseHistoryDateFrom = "";

  let releaseHistoryDateThrough = "";

  const releaseDetailCache = new Map();

  const expandedReleaseDetailIds = new Set();

  const releaseVerificationLiveCache = new Map();

  const releaseVerificationLivePollTimers = new Map();

  const postReleaseReportResults = new Map();

  function releaseIncludedWork(release) {
    const names = [];
    const deliverableIds = uniqueTextList([
      ...(release.deliverable_ids || []),
      ...((release.manifest?.deliverables || []).map((row) => row.deliverable_id)),
    ]);
    deliverableIds.forEach((deliverableId) => {
      const deliverable = peers.app.state.deliverables?.[deliverableId];
      if (!deliverable) {
        return;
      }
      const task = peers.reviews.deliverableTask(deliverable);
      names.push(task?.title || peers.reviews.deliverableDisplaySubject(deliverable));
    });
    return uniqueTextList(names);
  }

  function releaseDisplayLabel(release) {
    const work = releaseIncludedWork(release);
    if (work.length) {
      return `Release: ${work[0]}${work.length > 1 ? ` + ${work.length - 1} more` : ""}`;
    }
    const timestamp = release.published_at || release.updated_at || release.created_at;
    if (timestamp) {
      return `${release.published_at ? "Published" : "Release prepared"} ${peers.app.formatTimelineTimestamp(timestamp)}`;
    }
    return "Project release";
  }

  function releaseProjectLabel(release) {
    return peers.app.state.projects?.[text(release.project_id)]?.name || text(release.project_id) || "Unknown project";
  }

  function releaseAgentReviews(release) {
    const embedded = release.release_reviews || [];
    if (embedded.length) {
      return embedded.filter((review) => !review.superseded_by_review_request_id);
    }
    return uniqueTextList(release.review_request_ids || [])
      .map((reviewId) => peers.app.state.review_requests?.[reviewId])
      .filter((review) => review && !review.superseded_by_review_request_id);
  }

  function releaseVerificationCommandFailed(result) {
    const resultState = text(result?.state);
    const hasNonzeroExitCode =
      result?.exit_code !== null &&
      result?.exit_code !== undefined &&
      Number(result.exit_code) !== 0;
    return (
      ["failed", "timed_out", "blocked", "rejected"].includes(resultState) ||
      hasNonzeroExitCode ||
      Boolean(text(result?.failure_cause))
    );
  }

  function releaseVerificationDecisiveError(result, run = null) {
    const excerpt = text(result?.log_excerpt || run?.error);
    if (!excerpt) {
      return "";
    }
    const lines = excerpt
      .replace(/\r\n?/g, "\n")
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean);
    if (!lines.length) {
      return "";
    }
    const diagnosticPattern =
      /\b(?:error|failed|failure|fatal|exception|traceback|timed?\s*out|exit(?:ed)?)\b|(?:TS|CS)\d{3,}|ERR!/i;
    const diagnosticLines = lines.filter((line) => diagnosticPattern.test(line));
    return readablePreview(diagnosticLines.at(-1) || lines.at(-1), 220);
  }

  function releaseVerificationFailureSummary(runs) {
    const failedRuns = (runs || []).filter((run) =>
      ["failed", "timed_out", "blocked", "rejected"].includes(text(run.state)),
    );
    const run = failedRuns.at(-1);
    if (!run) {
      return "";
    }
    const failedResults = (run.command_results || []).filter(
      releaseVerificationCommandFailed,
    );
    const result = failedResults.at(-1);
    if (!result) {
      return run.error
        ? `Checks need attention · Error: ${readablePreview(run.error, 190)}`
        : "Checks need attention";
    }
    const parts = [`Failed command: ${text(result.name) || "Unnamed command"}`];
    if (result.exit_code !== null && result.exit_code !== undefined) {
      parts.push(`exit code ${Number(result.exit_code)}`);
    }
    if (result.failure_cause) {
      parts.push(`failure cause: ${humanizeToken(result.failure_cause)}`);
    }
    const decisiveError = releaseVerificationDecisiveError(result, run);
    if (decisiveError) {
      parts.push(`Error: ${decisiveError}`);
    }
    return readablePreview(parts.join(" · "), 260);
  }

  function releaseRequiresGitCandidate(release) {
    return Boolean(peers.app.state.projects?.[text(release.project_id)]?.git);
  }

  function releaseCandidateInputsMissing(release) {
    const manifest = release.manifest || null;
    const modern = Number(release.policy_version || 1) >= 2 || Boolean(manifest);
    const candidateSha = text(release.candidate_commit_sha || manifest?.candidate_commit_sha);
    return Boolean(
      modern
      && releaseRequiresGitCandidate(release)
      && !candidateSha
      && !(release.git_promotion_record_ids || []).length
      && !(release.feature_branches || []).length
      && !text(release.release_branch)
      && !text(release.canonical_branch)
    );
  }

  function releaseCandidateDetail(release) {
    const manifest = release.manifest || null;
    if (release.candidate_commit_sha || manifest?.candidate_commit_sha) {
      return "Pinned candidate prepared";
    }
    if (Number(release.policy_version || 1) < 2) {
      return "Not recorded (legacy release)";
    }
    if (!releaseRequiresGitCandidate(release)) {
      return "Not required (artifact-only release)";
    }
    return releaseCandidateInputsMissing(release)
      ? "Blocked: no Git-backed release input"
      : "Not prepared";
  }

  function releaseProgressStages(release) {
    if (release.state === "cancelled") {
      return [{ label: "Release cancelled", state: "not-applicable", detail: release.cancellation?.reason || "This release attempt has ended." }];
    }
    const manifest = release.manifest || null;
    const modern = Number(release.policy_version || 1) >= 2 || Boolean(manifest);
    const candidateSha = text(release.candidate_commit_sha || manifest?.candidate_commit_sha);
    const gitCandidateRequired = releaseRequiresGitCandidate(release);
    const verificationCommands = manifest?.verification_commands || [];
    const verificationRuns = (release.verification_runs || []).filter(
      (run) => !run.superseded_by_verification_run_id,
    );
    const reviews = releaseAgentReviews(release);
    const reviewRequirements = manifest?.review_requirements || [];
    const humanSummary = release.human_review || null;
    const humanRequired = Boolean(manifest?.human_review_requirement || humanSummary);
    const candidateInputsMissing = releaseCandidateInputsMissing(release);
    const gateStages = {};
    const gateSequence = peers.policy.normalizedReleaseGateSequence(
      manifest || peers.app.state.projects?.[text(release.project_id)]?.release_policy || null,
    );

    const stages = [];
    stages.push(
      !modern
        ? { label: "Candidate prepared", state: "legacy", detail: "Not recorded (legacy release)" }
        : candidateSha
          ? { label: "Candidate prepared", state: "complete", detail: "Pinned candidate ready" }
          : !gitCandidateRequired
            ? { label: "Candidate prepared", state: "not-applicable", detail: "No Git candidate required" }
          : candidateInputsMissing
            ? { label: "Candidate prepared", state: "blocked", detail: "No Git-backed release input" }
          : { label: "Candidate prepared", state: release.state === "blocked" ? "blocked" : "pending", detail: "Waiting for a pinned candidate" },
    );

    if (!modern) {
      gateStages.verification = { label: "Automated verification", state: "legacy", detail: "Not recorded (legacy release)" };
    } else if (!verificationCommands.length && !verificationRuns.length) {
      gateStages.verification = { label: "Automated verification", state: "not-applicable", detail: "Disabled" };
    } else {
      const runStates = verificationRuns.map((run) => text(run.state));
      const failed = runStates.some((stateValue) => ["failed", "timed_out", "blocked", "rejected"].includes(stateValue));
      const completed = runStates.length && runStates.every((stateValue) => ["passed", "succeeded", "accepted"].includes(stateValue));
      const runningRun = verificationRuns.find((run) => text(run.state) === "running");
      const currentCommandIndex = Number(runningRun?.current_command_index || 0);
      const currentCommand = runningRun?.command_results?.[currentCommandIndex]
        || runningRun?.command_results?.find((result) => text(result.state) === "running");
      const runningDetail = currentCommand?.name
        ? `Running: ${text(currentCommand.name)}`
        : runningRun
          ? "Preparing verifier"
          : "";
      gateStages.verification = {
        label: "Automated verification",
        state: failed ? "blocked" : completed ? "complete" : "pending",
        detail: failed ? releaseVerificationFailureSummary(verificationRuns) || "Checks need attention" : completed ? "Checks passed" : runningDetail || `${verificationCommands.length || verificationRuns.length} check${(verificationCommands.length || verificationRuns.length) === 1 ? "" : "s"} pending`,
      };
    }

    if (!modern && !reviews.length) {
      gateStages.agent_review = { label: "Release agent review", state: "legacy", detail: "Not recorded (legacy release)" };
    } else if (!reviewRequirements.length && !reviews.length) {
      gateStages.agent_review = { label: "Release agent review", state: "not-applicable", detail: "Disabled" };
    } else {
      const reviewStates = reviews.map((review) => text(review.state));
      const failed = reviewStates.some((stateValue) => ["blocked", "rejected"].includes(stateValue));
      const completed = reviewStates.length && reviewStates.every((stateValue) => stateValue === "accepted");
      gateStages.agent_review = {
        label: "Release agent review",
        state: failed ? "blocked" : completed ? "complete" : "pending",
        detail: failed ? "Review needs attention" : completed ? `${reviews.length} review${reviews.length === 1 ? "" : "s"} accepted` : "Awaiting agent review",
      };
    }

    if (!humanRequired) {
      gateStages.human_review = { label: "Human review", state: "not-applicable", detail: "Disabled" };
    } else {
      const humanState = text(humanSummary?.state || "required");
      const complete = ["approved", "accepted"].includes(humanState);
      const failed = ["blocked", "changes_requested", "rejected"].includes(humanState);
      gateStages.human_review = {
        label: "Human review",
        state: complete ? "complete" : failed ? "blocked" : "pending",
        detail: complete ? "Approved" : failed ? humanizeToken(humanState) : "Awaiting human decision",
      };
    }

    gateSequence.forEach((stage) => {
      const rendered = gateStages[stage.kind] || {
        label: RELEASE_GATE_LABELS[stage.kind] || stage.kind,
        state: "not-applicable",
        detail: "Disabled",
      };
      stages.push(stage.enabled ? rendered : { ...rendered, state: "not-applicable", detail: "Disabled" });
    });

    const published = Boolean(release.published_at);
    const workflowManagedPublication = (
      release.manifest?.automatic_release_branch_publication === false
      && release.approved_commit_sha
      && release.approved_commit_sha === release.candidate_commit_sha
    );
    stages.push({
      label: "Publication",
      state: published ? "complete" : workflowManagedPublication ? "not-applicable" : ["blocked", "rejected"].includes(text(release.state)) ? "blocked" : "pending",
      detail: published ? `Published ${peers.app.formatTimelineTimestamp(release.published_at)}` : workflowManagedPublication ? "Handled by approval workflow" : release.error ? "Publication needs attention" : "Not published",
    });
    return stages;
  }

  function renderReleaseProgress(release) {
    const stages = releaseProgressStages(release);
    return `
      <ol class="release-progress" aria-label="Release progress">
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
    `;
  }

  function renderReleaseIncludedWork(release) {
    const deliverableIds = uniqueTextList([
      ...(release.deliverable_ids || []),
      ...((release.manifest?.deliverables || []).map((row) => row.deliverable_id)),
    ]);
    if (!deliverableIds.length) {
      return `<p class="empty-state">No included work recorded.</p>`;
    }
    return `
      <ul class="release-included-work">
        ${deliverableIds.map((deliverableId) => {
          const deliverable = peers.app.state.deliverables?.[deliverableId];
          const task = deliverable ? peers.reviews.deliverableTask(deliverable) : null;
          return `
            <li>
              <strong>${escapeHtml(task?.title || (deliverable ? peers.reviews.deliverableDisplaySubject(deliverable) : "Included deliverable"))}</strong>
              ${deliverable?.summary ? `<p>${escapeHtml(readablePreview(deliverable.summary, 260))}</p>` : ""}
              ${deliverable ? `<span>${escapeHtml(peers.reviews.deliverableProducerLabel(deliverable))} · ${escapeHtml(humanizeToken(deliverable.state))}</span>` : ""}
            </li>
          `;
        }).join("")}
      </ul>
    `;
  }

  function renderReleaseNotes(release) {
    const summary = text(release.summary);
    if (!summary) {
      return `<p class="empty-state release-notes-empty">No release notes were recorded for this package.</p>`;
    }
    return `<div class="release-notes">${renderIdentifierText(summary)}</div>`;
  }

  function renderReleaseTechnicalDetails(
    release,
    { disclosureKey = peers.disclosures.teamDisclosureKey("release", release.id, "technical") } = {},
  ) {
    const manifest = release.manifest || {};
    return peers.reviews.renderTechnicalDetails([
      ["Release ID", release.id],
      ["Project ID", release.project_id],
      ["Deliverable IDs", release.deliverable_ids || []],
      ["Supersedes release ID", release.supersedes_release_package_id],
      ["Predecessor release ID", manifest.predecessor_release_package_id],
      ["Release branch", release.release_branch],
      ["Canonical branch", release.canonical_branch],
      ["Feature branches", release.feature_branches || []],
      ["Git base branch", release.git_base_branch],
      ["Base commit", release.base_commit_sha],
      ["Canonical destination base", release.canonical_base_commit_sha],
      ["Candidate commit", release.candidate_commit_sha || manifest.candidate_commit_sha],
      ["Candidate tree", release.candidate_tree_hash || manifest.candidate_tree_hash],
      ["Approved commit", release.approved_commit_sha],
      ["Published commit", release.published_commit_sha],
      ["Review IDs", release.review_request_ids || []],
      ["Verification run IDs", release.verification_run_ids || []],
      ["Promotion IDs", release.git_promotion_record_ids || []],
    ], { disclosureKey });
  }

  function renderReleaseCardBody(release) {
    return `
      <div class="release-card-body">
        <section class="release-card-section">
          <h4>Release progress</h4>
          ${renderReleaseProgress(release)}
        </section>
        <section class="release-card-section">
          <h4>Release notes</h4>
          ${renderReleaseNotes(release)}
        </section>
        <section class="release-card-section">
          <h4>Included work</h4>
          ${renderReleaseIncludedWork(release)}
        </section>
        ${release.error ? `<div class="task-invalid-warning"><strong>Release needs attention:</strong> ${escapeHtml(release.error)}</div>` : ""}
        ${peers.recovery.renderRecordBlockers("release_package", release.id)}
        ${renderReleaseGateAction(release)}
        ${renderReleaseCancellation(release)}
        ${renderPostReleaseReportControl(release)}
        ${renderReleaseDetailControl(release)}
        ${renderCachedReleaseDetail(release.id)}
        ${renderReleasePolicyDetails(release) ? `<details class="technical-details release-policy-details" ${peers.disclosures.teamDisclosureAttributes(peers.disclosures.teamDisclosureKey("release-card", release.id, "policy"))}><summary>Policy and gate details</summary><div class="release-policy-body">${renderReleasePolicyDetails(release)}</div></details>` : ""}
        ${renderReleaseTechnicalDetails(release, {
          disclosureKey: peers.disclosures.teamDisclosureKey("release-card", release.id, "technical"),
        })}
      </div>
    `;
  }

  function renderReleaseCard(release, { compact = false } = {}) {
    const work = releaseIncludedWork(release);
    const scope = [
      releaseProjectLabel(release),
      work.length ? `${work.length} included work item${work.length === 1 ? "" : "s"}` : "No included work named",
      release.published_at ? `Published ${peers.app.formatTimelineTimestamp(release.published_at)}` : release.created_at ? `Prepared ${peers.app.formatTimelineTimestamp(release.created_at)}` : "",
    ].filter(Boolean);
    return `
      <li class="record-item compact-record-card release-card${compact ? " release-card-compact" : ""}" data-release-id="${escapeAttr(release.id)}">
        <details ${peers.disclosures.teamDisclosureAttributes(peers.disclosures.teamDisclosureKey("release-card", release.id, "detail"))}>
          <summary class="compact-record-summary release-card-summary">
            <span class="compact-record-main">
              <strong>${escapeHtml(releaseDisplayLabel(release))}</strong>
              <span>${escapeHtml(scope.join(" · "))}</span>
            </span>
            <span class="state-pill ${escapeAttr(release.state)}">${escapeHtml(humanizeToken(release.state))}</span>
          </summary>
          <div class="compact-record-body">${renderReleaseCardBody(release)}</div>
        </details>
      </li>
    `;
  }

  function renderReleases() {
    if (!document.querySelector("[data-releases-view]")) {
      return;
    }
    peers.delivery.updateMyHumanReviewsFilterButton();
    const allReleases = values(peers.app.state.release_packages).sort(peers.projects.compareProjectReleasesLatest);
    const focusedRequest = peers.humanReviews.focusedHumanReviewRequestId
      ? peers.humanReviews.humanReviewQueue.requests.find(
          (request) => text(request.id) === peers.humanReviews.focusedHumanReviewRequestId,
        )
      : null;
    const assignedReleaseIds = new Set(
      (focusedRequest ? [focusedRequest] : peers.humanReviews.humanReviewQueue.requests)
        .map((request) => text(request.release_package_id))
        .filter(Boolean),
    );
    const scopedReleases = peers.humanReviews.myHumanReviewsOnly
      ? allReleases.filter((release) => assignedReleaseIds.has(text(release.id)))
      : allReleases;
    if (
      peers.humanReviews.myHumanReviewsOnly &&
      (peers.humanReviews.humanReviewQueue.loading ||
        (!peers.humanReviews.humanReviewQueue.loaded && !peers.humanReviews.humanReviewQueue.error))
    ) {
      peers.app.renderIntoActiveTargets("[data-releases-view]", `<p class="empty-state">Loading your human reviews.</p>`);
      return;
    }
    if (peers.humanReviews.myHumanReviewsOnly && peers.humanReviews.humanReviewQueue.error) {
      peers.app.renderIntoActiveTargets(
        "[data-releases-view]",
        `<div class="task-invalid-warning">${escapeHtml(peers.humanReviews.humanReviewQueue.error)} <button type="button" class="record-action" data-my-human-reviews-retry>Retry</button></div>`,
      );
      return;
    }
    const releases = scopedReleases.filter(releaseMatchesHistoryFilters);
    const filters = renderReleaseHistoryFilters({
      visibleCount: releases.length,
      scopedCount: scopedReleases.length,
    });
    if (!releases.length) {
      peers.app.renderIntoActiveTargets(
        "[data-releases-view]",
        `
          ${filters}
          <p class="empty-state">${peers.humanReviews.myHumanReviewsOnly && !scopedReleases.length ? "No human reviews are assigned to you." : peers.humanReviews.myHumanReviewsOnly ? "No assigned release packages match these filters." : "No release packages match these filters."}</p>
        `,
      );
      return;
    }
    if (peers.app.activeTeamView === "delivery") {
      peers.app.renderIntoActiveTargets("[data-releases-view]", `
        ${filters}
        <ul class="release-list compact-record-list">
          ${releases.map((release) => renderReleaseCard(release, { compact: true })).join("")}
        </ul>
      `);
      return;
    }
    peers.app.renderIntoActiveTargets("[data-releases-view]", `
      ${filters}
      <ul class="release-list compact-record-list">
        ${releases.map((release) => renderReleaseCard(release)).join("")}
      </ul>
    `);
  }

  function renderReleaseHistoryFilters({ visibleCount, scopedCount }) {
    const hasFilters = Boolean(
      selectedReleaseHistoryState || releaseHistoryDateFrom || releaseHistoryDateThrough,
    );
    const summary = hasFilters
      ? `${visibleCount} of ${scopedCount} release package${scopedCount === 1 ? "" : "s"} shown`
      : `${scopedCount} release package${scopedCount === 1 ? "" : "s"} in history`;
    const historyWindow = !peers.humanReviews.myHumanReviewsOnly && peers.app.state.history_windows?.release_packages;
    const historySummary = historyWindow?.windowed
      ? hasFilters
        ? `${visibleCount} of ${scopedCount} loaded release packages shown (${historyWindow.total_count} in history). Filters apply to the loaded releases`
        : `${scopedCount} of ${historyWindow.total_count} release packages loaded. Filters apply to the loaded releases`
      : summary;
    return `
      <div class="release-history-filters" data-release-history-filters role="group" aria-label="Release history filters">
        <label>
          State
          <select data-release-history-filter="state">
            ${renderSelectOptions(
              releaseHistoryStates.map((stateValue) => [stateValue, humanizeToken(stateValue)]),
              { selectedValue: selectedReleaseHistoryState, placeholder: "Any state" },
            )}
          </select>
        </label>
        <label>
          Activity date from
          <input type="date" value="${escapeAttr(releaseHistoryDateFrom)}" data-release-history-filter="date-from" />
        </label>
        <label>
          Activity date through
          <input type="date" value="${escapeAttr(releaseHistoryDateThrough)}" data-release-history-filter="date-through" />
        </label>
        <button type="button" class="secondary release-history-clear-filters" data-clear-release-history-filters>
          Clear filters
        </button>
        <p class="release-history-filter-summary">${escapeHtml(historySummary)}. Activity date is published when available; otherwise prepared.</p>
      </div>
    `;
  }

  function resetReleaseHistoryFilters() {
    selectedReleaseHistoryState = "";
    releaseHistoryDateFrom = "";
    releaseHistoryDateThrough = "";
  }

  function releaseMatchesHistoryFilters(release) {
    if (selectedReleaseHistoryState && text(release.state) !== selectedReleaseHistoryState) {
      return false;
    }
    if (!releaseHistoryDateFrom && !releaseHistoryDateThrough) {
      return true;
    }
    const activityTimestamp = Date.parse(
      peers.timeline.normalizeTimelineTimestampForSort(release.published_at || release.created_at || ""),
    );
    if (!Number.isFinite(activityTimestamp)) {
      return false;
    }
    const from = peers.board.dateInputLocalBoundary(releaseHistoryDateFrom, false);
    const through = peers.board.dateInputLocalBoundary(releaseHistoryDateThrough, true);
    return (
      (!Number.isFinite(from) || activityTimestamp >= from) &&
      (!Number.isFinite(through) || activityTimestamp <= through)
    );
  }

  function renderCompactRelease(release) {
    return renderReleaseCard(release, { compact: true });
  }

  function renderPostReleaseReportControl(release) {
    if (
      text(release.state) !== "accepted" ||
      (!text(release.published_at) || !text(release.published_commit_sha))
    ) {
      return "";
    }
    const priorResult = postReleaseReportResults.get(text(release.id));
    const caseRecord = priorResult?.case || null;
    const triageTaskId = text(priorResult?.triageTaskId);
    const triageTask = triageTaskId ? peers.app.state.tasks?.[triageTaskId] : null;
    const resultMarkup = caseRecord
      ? `
        <div class="post-release-report-result" role="status">
          <strong>Follow-up recorded.</strong>
          <div>Case <code>${escapeHtml(caseRecord.id)}</code>${caseRecord.title ? ` - ${escapeHtml(caseRecord.title)}` : ""}${caseRecord.status ? ` - ${escapeHtml(caseRecord.status)}` : ""}</div>
          ${
            triageTaskId
              ? `<div>Triage task <code>${escapeHtml(triageTaskId)}</code>${triageTask?.title ? ` - ${escapeHtml(triageTask.title)}` : ""}${triageTask?.state ? ` - ${escapeHtml(triageTask.state)}` : ""}</div>`
              : ""
          }
        </div>
      `
      : "";
    return `
      <div class="post-release-report-control">
        ${resultMarkup}
        <details ${peers.disclosures.teamDisclosureAttributes(peers.disclosures.teamDisclosureKey("release-card", release.id, "post-release-report"))}>
          <summary>Report regression or adjustment</summary>
          <form
            class="post-release-report-form"
            data-post-release-report-form
            data-form-id="post-release-report-form"
            data-form-draft-scope="${escapeAttr(release.id)}"
            data-release-id="${escapeAttr(release.id)}"
          >
            <p class="post-release-report-boundary">
              Reporting creates a follow-up case and triage task. It does not roll back this published release and does not automatically create or authorize a successor.
            </p>
            <label>
              Reason or adjustment needed
              <textarea
                name="report"
                rows="3"
                required
                aria-required="true"
                placeholder="Describe the regression, operational issue, or adjustment that needs triage"
              ></textarea>
            </label>
            <label>
              Evidence references
              <textarea
                name="evidence_refs"
                rows="2"
                required
                aria-required="true"
                placeholder="artifact-space:..., one per line"
              ></textarea>
            </label>
            <div class="record-actions">
              <button type="submit" class="record-action">Create follow-up case and triage task</button>
            </div>
          </form>
        </details>
      </div>
    `;
  }

  function renderReleaseDetailControl(release) {
    const expanded = expandedReleaseDetailIds.has(text(release.id));
    return `
      <div class="record-actions release-detail-actions">
        <button
          type="button"
          class="record-action"
          data-release-detail-id="${escapeAttr(release.id)}"
          aria-expanded="${expanded ? "true" : "false"}"
        >${expanded ? "Close release detail" : "Open release detail"}</button>
      </div>
    `;
  }

  function renderCachedReleaseDetail(releaseId) {
    if (!expandedReleaseDetailIds.has(text(releaseId))) {
      return "";
    }
    const cached = releaseDetailCache.get(text(releaseId));
    if (!cached || cached.loading) {
      return `<div class="release-review-detail"><p class="empty-state">Loading release detail.</p></div>`;
    }
    if (cached.error) {
      return `<div class="release-review-detail task-invalid-warning">${escapeHtml(cached.error)}</div>`;
    }
    const release = mergeReleaseDetailWithCurrentState(cached.release);
    ensureReleaseVerificationLivePolling(release);
    return peers.evidence.renderReleaseDetail(release);
  }

  async function toggleReleaseDetail(releaseId) {
    if (!releaseId) {
      return;
    }
    if (expandedReleaseDetailIds.has(releaseId)) {
      expandedReleaseDetailIds.delete(releaseId);
      clearReleaseVerificationLivePolling(releaseId);
      peers.disclosures.closeDisclosure(
        peers.disclosures.teamDisclosureStateKey(peers.disclosures.teamDisclosureKey("release-card", releaseId, "detail")),
      );
      renderReleaseDetailSurfaces();
      return;
    }
    expandedReleaseDetailIds.add(releaseId);
    peers.disclosures.openDisclosure(
      peers.disclosures.teamDisclosureStateKey(peers.disclosures.teamDisclosureKey("release-card", releaseId, "detail")),
    );
    renderReleaseDetailSurfaces();
    const cached = releaseDetailCache.get(releaseId);
    if (!cached?.release) {
      await loadReleaseDetail(releaseId);
    } else {
      ensureReleaseVerificationLivePolling(mergeReleaseDetailWithCurrentState(cached.release));
    }
  }

  function renderReleaseDetailSurfaces() {
    const releaseVerificationScrollState = captureReleaseVerificationScrollState();
    renderReleases();
    peers.projects.renderProjectFocus();
    peers.delivery.renderReviewDeliveryWorkspace();
    restoreReleaseVerificationScrollState(releaseVerificationScrollState);
  }

  function releaseVerificationLiveLogScrollKey(element) {
    return [
      text(element?.dataset?.releaseId),
      text(element?.dataset?.verificationRunId),
      text(element?.dataset?.commandIndex),
    ].join(":");
  }

  function captureReleaseVerificationScrollState() {
    const logs = new Map();
    document.querySelectorAll("pre[data-release-verification-live-log]").forEach((element) => {
      const key = releaseVerificationLiveLogScrollKey(element);
      const snapshots = logs.get(key) || [];
      const maxScrollTop = Math.max(0, element.scrollHeight - element.clientHeight);
      snapshots.push({
        scrollTop: element.scrollTop,
        followsTail:
          maxScrollTop - element.scrollTop <= RELEASE_VERIFICATION_LIVE_FOLLOW_THRESHOLD_PX,
      });
      logs.set(key, snapshots);
    });
    return {
      view: peers.app.activeTeamView,
      windowScrollX: window.scrollX,
      windowScrollY: window.scrollY,
      logs,
    };
  }

  function restoreReleaseVerificationScrollState(snapshot) {
    if (!snapshot || snapshot.view !== peers.app.activeTeamView) {
      return;
    }
    const restoredCounts = new Map();
    document.querySelectorAll("pre[data-release-verification-live-log]").forEach((element) => {
      const key = releaseVerificationLiveLogScrollKey(element);
      const index = restoredCounts.get(key) || 0;
      const previous = snapshot.logs.get(key)?.[index];
      const maxScrollTop = Math.max(0, element.scrollHeight - element.clientHeight);
      element.scrollTop = !previous || previous.followsTail
        ? maxScrollTop
        : Math.min(previous.scrollTop, maxScrollTop);
      restoredCounts.set(key, index + 1);
    });
    window.scrollTo(snapshot.windowScrollX, snapshot.windowScrollY);
  }

  async function loadReleaseDetail(releaseId) {
    releaseDetailCache.set(releaseId, { loading: true, error: null, release: null });
    renderReleaseDetailSurfaces();
    try {
      const response = await peers.access.teamFetch(`${API_BASE}/release-packages/${encodeURIComponent(releaseId)}`, {
        headers: { Accept: "application/json" },
      });
      if (!response.ok) {
        throw new Error(await responseText(response));
      }
      const release = await response.json();
      releaseDetailCache.set(releaseId, { loading: false, error: null, release });
    } catch (error) {
      releaseDetailCache.set(releaseId, {
        loading: false,
        error: error instanceof Error ? error.message : "Release detail could not be loaded.",
        release: null,
      });
    }
    renderReleaseDetailSurfaces();
  }

  function mergeReleaseDetailWithCurrentState(release) {
    const current = peers.app.state.release_packages?.[text(release?.id)];
    if (!current) {
      return release;
    }
    return {
      ...release,
      state: current.state ?? release.state,
      error: current.error ?? release.error,
      updated_at: current.updated_at ?? release.updated_at,
      manifest: current.manifest ?? release.manifest,
      verification_runs: current.verification_runs ?? release.verification_runs,
    };
  }

  function activeReleaseVerificationRun(release) {
    return (release?.verification_runs || []).find(
      (run) => !run.superseded_by_verification_run_id && text(run.state) === "running",
    ) || null;
  }

  function releaseVerificationRunStillActive(releaseId) {
    const normalizedId = text(releaseId);
    const detailedRelease = releaseDetailCache.get(normalizedId)?.release;
    const release = detailedRelease
      ? mergeReleaseDetailWithCurrentState(detailedRelease)
      : peers.app.state.release_packages?.[normalizedId];
    return Boolean(activeReleaseVerificationRun(release));
  }

  function clearReleaseVerificationLivePolling(releaseId) {
    const normalizedId = text(releaseId);
    const timer = releaseVerificationLivePollTimers.get(normalizedId);
    if (timer) {
      window.clearTimeout(timer);
    }
    releaseVerificationLivePollTimers.delete(normalizedId);
    releaseVerificationLiveCache.delete(normalizedId);
  }

  function ensureReleaseVerificationLivePolling(release) {
    const releaseId = text(release?.id);
    if (!releaseId || !expandedReleaseDetailIds.has(releaseId)) {
      return;
    }
    if (!activeReleaseVerificationRun(release)) {
      clearReleaseVerificationLivePolling(releaseId);
      return;
    }
    const cached = releaseVerificationLiveCache.get(releaseId);
    if (!cached || (!cached.loading && !cached.payload && !cached.error)) {
      void loadReleaseVerificationLive(releaseId);
    }
  }

  function scheduleReleaseVerificationLivePoll(releaseId, payload, retryWhileActive = false) {
    const normalizedId = text(releaseId);
    const previous = releaseVerificationLivePollTimers.get(normalizedId);
    if (previous) {
      window.clearTimeout(previous);
    }
    if (
      !expandedReleaseDetailIds.has(normalizedId)
      || (
        text(payload?.run?.state) !== "running"
        && !(retryWhileActive && releaseVerificationRunStillActive(normalizedId))
      )
    ) {
      releaseVerificationLivePollTimers.delete(normalizedId);
      return;
    }
    releaseVerificationLivePollTimers.set(normalizedId, window.setTimeout(() => {
      releaseVerificationLivePollTimers.delete(normalizedId);
      void loadReleaseVerificationLive(normalizedId);
    }, RELEASE_VERIFICATION_LIVE_POLL_MS));
  }

  async function loadReleaseVerificationLive(releaseId) {
    const normalizedId = text(releaseId);
    const existing = releaseVerificationLiveCache.get(normalizedId);
    if (existing?.loading) {
      return;
    }
    releaseVerificationLiveCache.set(normalizedId, {
      loading: true,
      error: null,
      payload: existing?.payload || null,
    });
    renderReleaseDetailSurfaces();
    try {
      const response = await peers.access.teamFetch(
        `${API_BASE}/release-packages/${encodeURIComponent(normalizedId)}/verification-live?limit=160`,
        { headers: { Accept: "application/json" } },
      );
      if (!response.ok) {
        throw new Error(await responseText(response));
      }
      const payload = await response.json();
      releaseVerificationLiveCache.set(normalizedId, { loading: false, error: null, payload });
      scheduleReleaseVerificationLivePoll(normalizedId, payload);
    } catch (error) {
      releaseVerificationLiveCache.set(normalizedId, {
        loading: false,
        error: error instanceof Error ? error.message : "Live verifier output could not be loaded.",
        payload: existing?.payload || null,
      });
      scheduleReleaseVerificationLivePoll(normalizedId, existing?.payload, true);
    }
    renderReleaseDetailSurfaces();
  }

  async function submitPostReleaseReport(form, submitter) {
    if (!peers.access.guardTeamMutation("report post-release issues")) {
      return;
    }
    const releaseId = text(form.dataset.releaseId);
    const release = peers.app.state.release_packages?.[releaseId];
    if (
      !releaseId ||
      text(release?.state) !== "accepted" ||
      (!text(release?.published_at) || !text(release?.published_commit_sha))
    ) {
      peers.app.setStatus("Post-release reporting is only available for a published release.", "error");
      return;
    }
    const data = formData(form);
    const report = text(data.report);
    const evidenceRefs = splitList(data.evidence_refs);
    if (!report) {
      peers.app.setStatus("A reason for the regression or adjustment is required.", "error");
      form.elements.report?.focus();
      return;
    }
    if (!evidenceRefs.length) {
      peers.app.setStatus("At least one evidence reference is required.", "error");
      form.elements.evidence_refs?.focus();
      return;
    }
    if (submitter) {
      submitter.disabled = true;
    }
    peers.app.setStatus(`Reporting follow-up for published release ${releaseId}.`, "loading");
    try {
      const response = await peers.access.teamFetch(
        `${API_BASE}/release-packages/${encodeURIComponent(releaseId)}/report-issue`,
        {
          method: "POST",
          headers: { Accept: "application/json", "Content-Type": "application/json" },
          body: JSON.stringify({
            report,
            evidence_refs: evidenceRefs,
            request_id: peers.mutations.teamUiMutationRequestId(releaseId, "report-issue"),
            expected_revision: Number(peers.app.state.revision || 0),
          }),
        },
      );
      if (!response.ok) {
        throw new Error(await responseText(response));
      }
      const payload = await response.json();
      peers.drafts.clearFormDraft(form);
      form.reset();
      peers.app.state.revision = payload.revision ?? peers.app.state.revision;
      postReleaseReportResults.set(releaseId, {
        case: payload.case || null,
        triageTaskId: text(payload.result?.triage_task_id),
      });
      peers.disclosures.openDisclosure(
        peers.disclosures.teamDisclosureStateKey(peers.disclosures.teamDisclosureKey("release-card", releaseId, "detail")),
      );
      releaseDetailCache.delete(releaseId);
      await peers.app.loadView("delivery", { force: true, preserveForms: false });
      if (expandedReleaseDetailIds.has(releaseId)) {
        await loadReleaseDetail(releaseId);
      }
      renderReleases();
      peers.app.setStatus(
        "Post-release follow-up recorded. The published release remains unchanged pending triage.",
        "success",
      );
    } catch (error) {
      peers.app.setStatus(
        error instanceof Error ? error.message : "Post-release follow-up could not be recorded.",
        "error",
      );
    } finally {
      if (submitter) {
        submitter.disabled = false;
      }
    }
  }

  function renderReleasePolicyDetails(release) {
    const waivers = release.verification_waivers || [];
    const scope = release.scope_validation || null;
    const waiverMarkup = waivers.length
      ? `<div class="meta-line"><strong>Verification waivers:</strong><ul>${waivers
          .map((waiver) => `<li>${escapeHtml(waiver.deliverable_id)} (${escapeHtml(waiver.verification_status)}): ${renderIdentifierText(waiver.reason)} - ${escapeHtml(waiver.actor_kind)} ${escapeHtml(waiver.actor_id)}</li>`)
          .join("")}</ul></div>`
      : "";
    const gateWaivers = release.gate_obligation_waivers || [];
    const gateWaiverMarkup = gateWaivers.length
      ? `<div class="meta-line"><strong>Gate obligation waivers:</strong><ul>${gateWaivers
          .map((waiver) => `<li>${escapeHtml(humanizeToken(waiver.gate_kind))}: ${renderIdentifierText(waiver.reason)} - ${escapeHtml(waiver.authorized_by)}</li>`)
          .join("")}</ul></div>`
      : "";
    const scopeMarkup = scope
      ? `<div class="meta-line">Scope validation: ${escapeHtml(scope.state)}${(scope.unexpected_commit_shas || []).length ? ` - unexpected commits ${escapeHtml(scope.unexpected_commit_shas.join(", "))}` : ""}${(scope.unexpected_paths || []).length ? ` - unexpected paths ${escapeHtml(scope.unexpected_paths.join(", "))}` : ""}</div>`
      : "";
    const humanRequirement = release.manifest?.human_review_requirement;
    const humanMarkup = humanRequirement
      ? `<div class="meta-line">Human gate: ${escapeHtml(humanRequirement.preparation_mode || "manual")} - groups ${escapeHtml((humanRequirement.configured_group_ids || []).join(", ") || "redacted")} - ${(humanRequirement.checklist || []).length} checklist item${(humanRequirement.checklist || []).length === 1 ? "" : "s"}</div>`
      : "";
    return `${waiverMarkup}${gateWaiverMarkup}${scopeMarkup}${humanMarkup}`;
  }

  function releaseHasSuccessor(releaseId) {
    const normalizedReleaseId = text(releaseId);
    return Boolean(normalizedReleaseId) && values(peers.app.state.release_packages).some(
      (candidate) => text(candidate?.supersedes_release_package_id) === normalizedReleaseId,
    );
  }

  function renderReleaseCancellation(release) {
    if (release.state === "cancelled") {
      const audit = release.cancellation || {};
      return `<div class="meta-line">Cancelled${audit.actor_id ? ` by ${escapeHtml(audit.actor_id)}` : ""}${audit.reason ? `: ${escapeHtml(audit.reason)}` : ""}. No further release work is scheduled.</div>`;
    }
    if (!release.cancellation_available) return "";
    return `<div class="record-actions"><button class="record-action" type="button"
      data-release-cancel data-release-id="${escapeAttr(release.id)}"
      ${peers.access.lockedAttrs("cancel releases")}>Cancel release</button></div>`;
  }

  async function cancelRelease(button, reason) {
    const releaseId = text(button?.dataset?.releaseId);
    if (!releaseId) return;
    button.disabled = true;
    peers.app.setStatus("Cancelling release.", "loading");
    try {
      const response = await peers.access.teamFetch(`${API_BASE}/release-packages/${encodeURIComponent(releaseId)}/cancel`, {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
          "X-Team-Addon-Response-Scope": "view",
        },
        body: JSON.stringify({ reason, expected_revision: peers.app.state.revision }),
      });
      if (!response.ok) throw new Error(await responseText(response));
      await peers.mutations.applyMutationResultPayload(await response.json());
      peers.app.setStatus("Release cancelled. No replacement work was requested.", "ok");
    } catch (error) {
      peers.app.setStatus(error instanceof Error ? error.message : "Release cancellation failed.", "error");
    } finally {
      button.disabled = false;
    }
  }

  function renderReleaseGateAction(release) {
    if (release.state === "cancelled") return "";
    const failure = release.gate_failure || null;
    const gateId = text(failure?.source_id);
    if (release.state === "blocked" && failure?.stage === "gate_obligation") {
      const predecessor = peers.app.state.release_packages?.[text(release.supersedes_release_package_id)];
      const gateKind = {
        verification: "verification",
        review: "agent_review",
        human_review: "human_review",
      }[text(predecessor?.gate_failure?.stage)];
      if (!gateKind || !release.candidate_commit_sha) return "";
      return `
        <div class="record-actions">
          <button class="record-action" type="button"
            data-release-gate-obligation-waiver
            data-release-id="${escapeAttr(release.id)}"
            data-gate-kind="${escapeAttr(gateKind)}"
            data-candidate-commit-sha="${escapeAttr(release.candidate_commit_sha)}"
          >Waive carried ${escapeHtml(humanizeToken(gateKind))} obligation</button>
        </div>
      `;
    }
    const retryableVerification =
      release.state === "blocked" &&
      failure?.stage === "verification" &&
      ["infrastructure", "timeout", "blocked"].includes(failure?.kind);
    const retryableReview =
      release.state === "blocked" &&
      failure?.stage === "review" &&
      failure?.kind === "blocked";
    const rejectedVerificationRetry =
      release.state === "rejected" &&
      failure?.stage === "verification" &&
      failure?.kind === "candidate" &&
      !releaseHasSuccessor(release.id);
    const rejectedReviewException =
      release.state === "rejected" &&
      failure?.stage === "review" &&
      failure?.kind === "rejected" &&
      !releaseHasSuccessor(release.id);
    if (!gateId || !(
      retryableVerification || retryableReview || rejectedReviewException || rejectedVerificationRetry
    )) {
      return "";
    }
    const gateKind = retryableVerification || rejectedVerificationRetry ? "verification" : "review";
    const label = rejectedReviewException
      ? "Retry rejected review by exception"
      : `Retry ${gateKind} gate`;
    return `
      <div class="record-actions">
        <button
          class="record-action"
          type="button"
          data-release-gate-retry
          data-release-id="${escapeAttr(release.id)}"
          data-gate-kind="${escapeAttr(gateKind)}"
          data-gate-id="${escapeAttr(gateId)}"
          data-rejected-review-exception="${rejectedReviewException ? "true" : "false"}"
          data-rejected-verification-retry="${rejectedVerificationRetry ? "true" : "false"}"
        >${escapeHtml(label)}</button>
      </div>
    `;
  }

  async function retryReleaseGate(button, reason) {
    const releaseId = text(button?.dataset?.releaseId);
    const gateKind = text(button?.dataset?.gateKind);
    const gateId = text(button?.dataset?.gateId);
    const rejectedReviewException = button?.dataset?.rejectedReviewException === "true";
    const rejectedVerificationRetry = button?.dataset?.rejectedVerificationRetry === "true";
    if (!releaseId || !gateKind || !gateId) {
      peers.app.setStatus("Release gate retry context is incomplete.", "error");
      return;
    }
    button.disabled = true;
    peers.app.setStatus(`Retrying ${gateKind} gate.`, "loading");
    try {
      const response = await peers.access.teamFetch(
        `${API_BASE}/release-packages/${encodeURIComponent(releaseId)}/retry-gate`,
        {
          method: "POST",
          headers: {
            Accept: "application/json",
            "Content-Type": "application/json",
            "X-Team-Addon-Response-Scope": "view",
          },
          body: JSON.stringify({
            gate_kind: gateKind,
            gate_id: gateId,
            reason,
            ...(rejectedReviewException ? { allow_rejected_review_exception: true } : {}),
            ...(rejectedVerificationRetry ? { allow_rejected_verification_retry: true } : {}),
            expected_revision: peers.app.state.revision,
          }),
        },
      );
      if (!response.ok) {
        throw new Error(await responseText(response));
      }
      const result = await response.json();
      await peers.mutations.applyMutationResultPayload(result);
      peers.app.setStatus(`Release ${releaseId} ${gateKind} retry recorded.`, "ok");
    } catch (error) {
      peers.app.setStatus(error instanceof Error ? error.message : "Release gate retry failed.", "error");
    } finally {
      button.disabled = false;
    }
  }

  async function waiveReleaseGateObligation(button, reason) {
    const releaseId = text(button?.dataset?.releaseId);
    const gateKind = text(button?.dataset?.gateKind);
    const candidateCommitSha = text(button?.dataset?.candidateCommitSha);
    if (!releaseId || !gateKind || !candidateCommitSha) {
      peers.app.setStatus("Release gate waiver context is incomplete.", "error");
      return;
    }
    button.disabled = true;
    peers.app.setStatus(`Waiving carried ${gateKind} obligation.`, "loading");
    try {
      const response = await peers.access.teamFetch(
        `${API_BASE}/release-packages/${encodeURIComponent(releaseId)}/gate-obligation-waivers`,
        {
          method: "POST",
          headers: { Accept: "application/json", "Content-Type": "application/json" },
          body: JSON.stringify({
            gate_kind: gateKind,
            candidate_commit_sha: candidateCommitSha,
            reason,
            expected_revision: peers.app.state.revision,
          }),
        },
      );
      if (!response.ok) throw new Error(await responseText(response));
      await peers.mutations.applyMutationResultPayload(await response.json());
      peers.app.setStatus(`Release ${releaseId} gate obligation waived.`, "ok");
    } catch (error) {
      peers.app.setStatus(error instanceof Error ? error.message : "Release gate waiver failed.", "error");
    } finally {
      button.disabled = false;
    }
  }
  function bindReleaseCancelAndRetry() {

    document.addEventListener("click", (event) => {
      const cancelButton = event.target?.closest?.("[data-release-cancel]");
      if (cancelButton) {
        event.preventDefault();
        if (!peers.access.guardTeamMutation("cancel releases")) return;
        const reason = window.prompt(
          "Cancel this release permanently? Its reviews and exclusive correction work will stop. No replacement work will be requested. Reason (optional):",
          "",
        );
        if (reason !== null) void cancelRelease(cancelButton, text(reason));
        return;
      }
      const button = event.target?.closest?.("[data-release-gate-retry]");
      if (!button) {
        return;
      }
      event.preventDefault();
      if (!peers.access.guardTeamMutation("retry release gates")) {
        return;
      }
      const rejectedReviewException = button.dataset.rejectedReviewException === "true";
      const rejectedVerificationRetry = button.dataset.rejectedVerificationRetry === "true";
      const reason = window.prompt(
        rejectedVerificationRetry
          ? "Add a comment for retrying verification on this candidate. Its unused generated correction task will be cancelled."
          : rejectedReviewException
            ? "Why is an operator exception justified for this rejected review on the unchanged candidate?"
            : "What concrete recovery occurred since this gate failed? Cancel and escalate if none.",
        "",
      );
      if (!text(reason)) {
        peers.app.setStatus("A retry reason is required.", "error");
        return;
      }
      void retryReleaseGate(button, text(reason));
    });
  }
  function bindGateWaivers() {

    document.addEventListener("click", (event) => {
      const button = event.target?.closest?.("[data-release-gate-obligation-waiver]");
      if (!button) return;
      event.preventDefault();
      if (!peers.access.guardTeamMutation("waive a carried release-gate obligation")) return;
      const reason = window.prompt(
        "Why may this successor omit the gate that rejected its predecessor? This waiver is pinned to the current candidate and retained in release history.",
        "",
      );
      if (!text(reason)) {
        peers.app.setStatus("A waiver reason is required.", "error");
        return;
      }
      void waiveReleaseGateObligation(button, text(reason));
    });
  }

  function changeHistoryFilter(control) {
    const value = text(control.value);
    switch (control.dataset.releaseHistoryFilter) {
      case "state":
        selectedReleaseHistoryState = releaseHistoryStates.includes(value) ? value : "";
        break;
      case "date-from":
        releaseHistoryDateFrom = value;
        break;
      case "date-through":
        releaseHistoryDateThrough = value;
        break;
      default:
        return;
    }
    renderReleases();
  }

  function expandReleaseDetail(releaseId) {
    expandedReleaseDetailIds.add(releaseId);
  }

  function releaseDetailIsExpanded(releaseId) {
    return expandedReleaseDetailIds.has(text(releaseId));
  }

  function cachedReleaseDetail(releaseId) {
    return releaseDetailCache.get(text(releaseId));
  }

  function cachedReleaseDetails() {
    return releaseDetailCache.values();
  }

  function invalidateReleaseDetail(releaseId) {
    releaseDetailCache.delete(text(releaseId));
  }

  function cachedVerificationLive(releaseId) {
    return releaseVerificationLiveCache.get(releaseId);
  }

  return {
    expandReleaseDetail,
    releaseDetailIsExpanded,
    cachedReleaseDetail,
    cachedReleaseDetails,
    invalidateReleaseDetail,
    cachedVerificationLive,
    renderReleaseGateAction,
    loadReleaseVerificationLive,
    cancelRelease,
    changeHistoryFilter,
    bindReleaseCancelAndRetry,
    bindGateWaivers,
    captureReleaseVerificationScrollState,
    loadReleaseDetail,
    releaseCandidateDetail,
    releaseDisplayLabel,
    releaseIncludedWork,
    releaseProgressStages,
    releaseProjectLabel,
    releaseVerificationCommandFailed,
    releaseVerificationDecisiveError,
    renderCompactRelease,
    renderReleaseCancellation,
    renderReleaseIncludedWork,
    renderReleaseNotes,
    renderReleaseProgress,
    renderReleaseTechnicalDetails,
    renderReleases,
    resetReleaseHistoryFilters,
    restoreReleaseVerificationScrollState,
    submitPostReleaseReport,
    toggleReleaseDetail,
  };
}

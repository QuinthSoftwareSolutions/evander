// Pinned release candidate, verification, review, and contribution evidence.
import {
  escapeAttr,
  escapeHtml,
  humanizeToken,
  readablePreview,
  text,
} from "./team-ui-format.js";
import { renderIdentifierText } from "./team-identifiers.js";

export function createTeamEvidenceController(peers) {

  function renderReleaseDetail(release) {
    if (!release) {
      return "";
    }
    const manifest = release.manifest || {};
    const project = peers.app.state.projects[release.project_id] || {};
    const contributionGraph = manifest.contribution_graph || {};
    const deliverableRows = manifest.deliverables || [];
    const verificationRuns = release.verification_runs || [];
    const releaseReviews = release.release_reviews || [];
    const humanRequests = release.human_review?.requests || [];
    const includedWork = peers.releases.releaseIncludedWork(release);
    return `
      <section class="release-review-detail" id="release-detail-${escapeAttr(release.id)}">
        <div class="release-review-heading">
          <div>
            <p class="section-label">Release detail</p>
            <h3>${escapeHtml(peers.releases.releaseDisplayLabel(release))}</h3>
          </div>
          <span class="state-pill ${escapeAttr(release.state)}">${escapeHtml(release.state)}</span>
        </div>
        <div class="delivery-detail-grid">
          <div><div class="delivery-detail-label">Project</div><div class="delivery-detail-value">${escapeHtml(project.name || "Unknown project")}</div></div>
          <div><div class="delivery-detail-label">Included work</div><div class="delivery-detail-value">${escapeHtml(includedWork.join(", ") || "No named work")}</div></div>
          <div><div class="delivery-detail-label">Release lineage</div><div class="delivery-detail-value">${release.supersedes_release_package_id || manifest.predecessor_release_package_id ? "Successor release attempt" : "Initial release attempt"}</div></div>
          <div><div class="delivery-detail-label">Candidate</div><div class="delivery-detail-value">${escapeHtml(peers.releases.releaseCandidateDetail(release))}</div></div>
          <div><div class="delivery-detail-label">Publication</div><div class="delivery-detail-value">${release.published_at ? peers.reviews.renderRecordDate(release.published_at, "Published") : manifest.automatic_release_branch_publication === false && release.approved_commit_sha === release.candidate_commit_sha ? "Handled by approval workflow" : escapeHtml(release.next_action_hint || release.next_action || "Not published")}</div></div>
          <div><div class="delivery-detail-label">Policy</div><div class="delivery-detail-value">Version ${Number(release.policy_version || 1)}</div></div>
        </div>
        ${renderReleaseCandidateCheckout(release)}
        ${project.git && ["active", "blocked"].includes(project.status) && project.release_policy?.reintegration?.enabled !== false ? `<button type="button" data-reintegrate-release-id="${escapeAttr(release.id)}">Reintegrate onto updated base</button>` : ""}
        ${renderReleaseDeliverableEvidence(deliverableRows, release)}
        ${renderReleaseContributionGraph(contributionGraph, release.id)}
        ${renderReleaseVerificationRuns(verificationRuns, manifest.verification_commands || [], release.id)}
        ${renderReleaseAgentReviews(releaseReviews, release.id, release.release_review_cohort)}
        ${peers.humanReviews.renderReleaseHumanRequests(humanRequests, manifest, release.id)}
        ${renderReleaseEvidence(release, deliverableRows, humanRequests)}
      </section>
    `;
  }

  function renderReleaseCandidateCheckout(release) {
    const checkout = release.candidate_checkout;
    if (!checkout?.checkout_command) {
      return "";
    }
    const repositoryLabel =
      text(checkout.repository_display_name) ||
      text(checkout.canonical_repository_identity) ||
      "Managed repository";
    return `
      <div class="release-detail-section release-candidate-checkout">
        <h4>Test this candidate</h4>
        <p class="release-candidate-checkout-intro">Create a new detached checkout of the exact candidate that passed this release pipeline. The command verifies the immutable release ref, commit, and tree before writing anything.</p>
        <dl class="release-candidate-checkout-metadata">
          <div><dt>Repository</dt><dd><strong>${escapeHtml(repositoryLabel)}</strong><code>${escapeHtml(checkout.canonical_repository_identity)}</code></dd></div>
          <div><dt>Managed repository ID</dt><dd>${peers.reviews.renderIdentifierCopy(checkout.managed_repo_id, { label: "Copy ID" })}</dd></div>
          <div><dt>Immutable release ref</dt><dd>${peers.reviews.renderIdentifierCopy(checkout.release_ref, { label: "Copy ref" })}</dd></div>
          <div><dt>Candidate commit</dt><dd>${peers.reviews.renderIdentifierCopy(checkout.candidate_commit_sha, { label: "Copy SHA" })}</dd></div>
          <div><dt>Candidate tree</dt><dd>${peers.reviews.renderIdentifierCopy(checkout.candidate_tree_hash, { label: "Copy tree" })}</dd></div>
        </dl>
        <div class="release-candidate-checkout-command">
          <div class="delivery-detail-label">Supported host command</div>
          ${peers.reviews.renderIdentifierCopy(checkout.checkout_command, { label: "Copy command" })}
        </div>
        <p class="meta-line">The target directory must be new. Existing or non-empty paths are never overwritten.</p>
      </div>
    `;
  }

  function renderReleaseDeliverableEvidence(rows, release) {
    if (!rows.length) {
      const deliverableIds = release.deliverable_ids || [];
      return deliverableIds.length
        ? `<div class="release-detail-section"><h4>Included work evidence</h4><ul class="delivery-detail-list">${deliverableIds.map((id) => {
            const deliverable = peers.app.state.deliverables[id] || {};
            const task = peers.reviews.deliverableTask(deliverable);
            return `<li><strong>${escapeHtml(task?.title || peers.reviews.deliverableDisplaySubject(deliverable))}</strong>${deliverable.summary ? `<div>${escapeHtml(readablePreview(deliverable.summary, 320))}</div>` : ""}${peers.reviews.renderTechnicalDetails([["Deliverable ID", id], ["Task ID", deliverable.task_id]], {
              disclosureKey: peers.disclosures.teamDisclosureKey("release-detail", release.id, "deliverable", id, "technical"),
            })}</li>`;
          }).join("")}</ul></div>`
        : "";
    }
    return `
      <div class="release-detail-section">
        <h4>Included work evidence</h4>
        <ul class="delivery-detail-list">
          ${rows.map((row) => {
            const deliverable = peers.app.state.deliverables[row.deliverable_id] || {};
            const task = peers.reviews.deliverableTask(deliverable);
            const reviews = row.accepted_reviews || [];
            const verification = row.verification || {};
            return `<li>
              <strong>${escapeHtml(task?.title || peers.reviews.deliverableDisplaySubject(deliverable))}</strong>
              ${deliverable.summary ? `<div>${escapeHtml(readablePreview(deliverable.summary, 320))}</div>` : ""}
              <div class="meta-line">Verification ${escapeHtml(humanizeToken(verification.status || "not recorded"))}${reviews.length ? ` · ${reviews.length} accepted agent review${reviews.length === 1 ? "" : "s"}` : ""}</div>
              ${peers.reviews.renderTechnicalDetails([
                ["Deliverable ID", row.deliverable_id],
                ["Task ID", deliverable.task_id],
                ["Feature branch", row.feature_branch],
                ["Feature commit", row.feature_commit_sha],
                ["Review IDs", reviews.map((review) => review.review_request_id)],
              ], {
                disclosureKey: peers.disclosures.teamDisclosureKey("release-detail", release.id, "deliverable", row.deliverable_id, "technical"),
              })}
            </li>`;
          }).join("")}
        </ul>
      </div>
    `;
  }

  function renderReleaseContributionGraph(graph, releaseId) {
    const rows = Object.entries(graph || {});
    if (!rows.length) {
      return "";
    }
    return `
      <div class="release-detail-section">
        <h4>Contribution lineage</h4>
        <ul class="delivery-detail-list">
          ${rows.map(([deliverableId, sourceIds]) => {
            const deliverable = peers.app.state.deliverables?.[deliverableId];
            const sources = (sourceIds || []).map((sourceId) => peers.app.state.deliverables?.[sourceId]).filter(Boolean);
            return `<li>
              <strong>${escapeHtml(deliverable ? peers.reviews.deliverableDisplaySubject(deliverable) : "Included work")}</strong>
              <div class="meta-line">${sources.length ? `Built from ${escapeHtml(sources.map(peers.reviews.deliverableDisplaySubject).join(", "))}` : "Direct contribution"}</div>
              ${peers.reviews.renderTechnicalDetails([["Deliverable ID", deliverableId], ["Source deliverable IDs", sourceIds || []]], {
                disclosureKey: peers.disclosures.teamDisclosureKey("release-detail", releaseId, "contribution", deliverableId, "technical"),
              })}
            </li>`;
          }).join("")}
        </ul>
      </div>
    `;
  }

  function renderReleaseVerificationCommandResult(
    result,
    run,
    disclosureKey,
  ) {
    const failed = peers.releases.releaseVerificationCommandFailed(result);
    const decisiveError = failed
      ? peers.releases.releaseVerificationDecisiveError(result, run)
      : "";
    const metadata = [];
    if (result.exit_code !== null && result.exit_code !== undefined) {
      metadata.push(`Exit code: ${Number(result.exit_code)}`);
    }
    if (result.failure_cause) {
      metadata.push(`Failure cause: ${humanizeToken(result.failure_cause)}`);
    }
    if (result.duration_seconds !== null && result.duration_seconds !== undefined) {
      metadata.push(`Duration: ${Number(result.duration_seconds)} seconds`);
    }
    const logExcerpt = text(result.log_excerpt);
    return `
      <li class="release-verification-command-result${failed ? " release-verification-command-failed" : ""}">
        <div class="release-verification-command-heading">
          <strong>${failed ? "Failed command" : "Command"}: ${escapeHtml(result.name || "Unnamed command")}</strong>
          <span class="state-pill ${escapeAttr(result.state)}">${escapeHtml(result.policy_outcome === "baseline_exception" ? "Accepted with baseline exceptions" : humanizeToken(result.state))}</span>
        </div>
        ${metadata.length ? `<div class="meta-line">${escapeHtml(metadata.join(" · "))}</div>` : ""}
        ${result.evidence?.explanation ? `<p>${escapeHtml(result.evidence.explanation)}</p>` : ""}
        ${result.toolchain_mounts?.length ? `<details ${peers.disclosures.teamDisclosureAttributes(peers.disclosures.teamDisclosureKey(disclosureKey, "toolchain"))}><summary>Verifier toolchain identity</summary><ul>${result.toolchain_mounts.map(mount => `<li>${escapeHtml(mount.target)} ← ${escapeHtml(mount.source)}</li>`).join("")}</ul></details>` : ""}
        ${result.log_artifact && result.job_id ? `<button type="button" data-retained-verifier-job="${escapeAttr(result.job_id)}" data-release-id="${escapeAttr(run.release_package_id)}">Read retained log</button>` : ""}
        ${decisiveError ? `<div class="release-verification-decisive-error"><strong>Decisive error:</strong> ${escapeHtml(decisiveError)}</div>` : ""}
        ${
          logExcerpt
            ? `<details class="release-verification-log" ${peers.disclosures.teamDisclosureAttributes(disclosureKey)}>
                <summary>Show captured command log</summary>
                <pre>${escapeHtml(logExcerpt)}</pre>
              </details>`
            : ""
        }
      </li>
    `;
  }

  function renderReleaseVerificationLiveOutput(run, result, index, releaseId) {
    if (text(run.state) !== "running" || text(result.state) !== "running") {
      return "";
    }
    const live = peers.releases.cachedVerificationLive(text(releaseId));
    const liveRun = live?.payload?.run;
    const liveCommand = liveRun?.current_command;
    const matchesCurrentCommand = text(liveRun?.id) === text(run.id)
      && Number(liveCommand?.index) === index;
    if (!matchesCurrentCommand && !live?.loading && !live?.error) {
      return "";
    }
    const log = live?.payload?.log;
    const message = live?.error
      || log?.message
      || (live?.loading ? "Connecting to the live verifier output." : "Waiting for verifier output.");
    const logText = text(log?.text);
    return `
      <li class="release-verification-live-output">
        <details class="release-verification-log" ${peers.disclosures.teamDisclosureAttributes(peers.disclosures.teamDisclosureKey("release-detail", releaseId, "verification-run", run.id, "live", String(index)), { defaultOpen: true })}>
          <summary>Live verifier output · updates every 2 seconds</summary>
          ${log?.has_more_before ? `<p class="meta-line">Earlier log lines are omitted from this bounded tail.</p>` : ""}
          ${logText ? `<pre
            data-release-verification-live-log
            data-release-id="${escapeAttr(releaseId)}"
            data-verification-run-id="${escapeAttr(run.id)}"
            data-command-index="${Number(index)}"
          >${escapeHtml(logText)}</pre>` : `<p class="empty-state">${escapeHtml(message)}</p>`}
        </details>
      </li>
    `;
  }

  function renderReleaseVerificationCommandResults(run, disclosureKeyPrefix, releaseId) {
    const results = run.command_results || [];
    if (!results.length) {
      return "";
    }
    return `
      <ul class="release-verification-command-list">
        ${results.map((result, index) =>
          renderReleaseVerificationCommandResult(
            result,
            run,
            peers.disclosures.teamDisclosureKey(disclosureKeyPrefix, run.id, "command", String(index), "log"),
          ) + renderReleaseVerificationLiveOutput(run, result, index, releaseId)).join("")}
      </ul>
    `;
  }

  function renderReleaseVerificationRuns(runs, commands, releaseId) {
    if (!runs.length && !commands.length) {
      return "";
    }
    return `
      <div class="release-detail-section">
        <h4>Automated verification</h4>
        ${
          runs.length
            ? `<ul class="delivery-detail-list">${runs.map((run) => `<li>
                <strong>Verification attempt ${Number(run.attempt_number || 1)}</strong> <span class="state-pill ${escapeAttr(run.state)}">${escapeHtml(humanizeToken(run.state))}</span>
                <div class="meta-line">Workspace generation ${Number(run.execution_generation || 0)} · Last recorded progress: ${escapeHtml(run.updated_at || "Unavailable")}${run.state === "running" ? " · Running status does not confirm continuing progress." : ""}</div>
                ${run.failure_kind ? `<div class="meta-line">Failure type: ${escapeHtml(humanizeToken(run.failure_kind))}</div>` : ""}
                ${run.retry_reason ? `<div class="meta-line"><strong>Retry comment:</strong> ${renderIdentifierText(run.retry_reason)}</div>` : ""}
                ${run.retry_requested_by ? `<div class="meta-line">Requested by ${escapeHtml(run.retry_requested_by)}</div>` : ""}
                ${renderReleaseVerificationCommandResults(run, `release-detail:${releaseId}:verification`, releaseId)}
                ${(run.generation_history || []).map(generation => `<details ${peers.disclosures.teamDisclosureAttributes(peers.disclosures.teamDisclosureKey("release-detail", releaseId, "verification-run", run.id, "generation", String(generation.generation)))}><summary>Interrupted workspace generation ${Number(generation.generation)}</summary>${renderReleaseVerificationCommandResults({ ...run, state: "interrupted", command_results: generation.command_results }, `release-detail:${releaseId}:verification:g${generation.generation}`, releaseId)}</details>`).join("")}
                ${peers.reviews.renderTechnicalDetails([["Verification run ID", run.id], ["Candidate commit", run.candidate_commit_sha]], {
                  disclosureKey: peers.disclosures.teamDisclosureKey("release-detail", releaseId, "verification-run", run.id, "technical"),
                })}
              </li>`).join("")}</ul>`
            : `<div class="meta-line">Configured commands: ${escapeHtml(commands.map((command) => command.name).join(", "))}</div>`
        }
      </div>
    `;
  }

  function renderReleaseAgentReviews(reviews, releaseId, cohort = null) {
    if (!reviews.length) {
      return "";
    }
    return `
      <div class="release-detail-section">
        <h4>Agent review decisions</h4>
        ${cohort ? `
          <div class="meta-line">
            <strong>Sealed cohort:</strong>
            ${escapeHtml(humanizeToken(cohort.status || "collecting"))} ·
            ${Number(cohort.resolved || 0)} of ${Number(cohort.total || reviews.length)} resolved.
            Correction work starts only after every cohort slot is accepted or rejected.
          </div>
        ` : ""}
        <ul class="delivery-detail-list">
          ${reviews.map((review) => {
            const reviewer = peers.app.state.agents?.[review.reviewer_agent_id];
            return `<li>
            <strong>Review by ${escapeHtml(reviewer ? peers.agents.agentLabel(reviewer) : "Assigned reviewer")}</strong> <span class="state-pill ${escapeAttr(review.state)}">${escapeHtml(humanizeToken(review.state))}</span>
            ${review.instructions ? `<div class="meta-line">${renderIdentifierText(review.instructions)}</div>` : ""}
            ${renderReleaseReviewCriteria(review)}
            ${(review.findings || []).length ? `<div class="meta-line">Findings: ${renderIdentifierText(review.findings.join(", "))}</div>` : ""}
            ${peers.reviews.renderTechnicalDetails([["Review ID", review.id], ["Review cohort ID", review.review_cohort_id], ["Reviewer ID", review.reviewer_agent_id], ["Reviewed commit", review.reviewed_commit_sha]], {
              disclosureKey: peers.disclosures.teamDisclosureKey("release-detail", releaseId, "agent-review", review.id, "technical"),
            })}
          </li>`;
          }).join("")}
        </ul>
      </div>
    `;
  }

  function renderReleaseReviewCriteria(review) {
    const snapshots = review.criteria_snapshot || [];
    const results = review.criteria || [];
    const resultsById = new Map(
      results.map((result) => [text(result.criterion_id || result.id), result]),
    );
    const rows = snapshots.length
      ? snapshots.map((criterion) => {
          const result = resultsById.get(text(criterion.id || criterion.criterion_id));
          return {
            label: criterion.label || criterion.criterion_id || criterion.id,
            state:
              result?.state ||
              criterion.state ||
              (criterion.required === false ? "optional" : "required"),
            finding: result?.finding || criterion.finding || "",
          };
        })
      : results.map((result) => ({
          label: result.label || result.criterion_id || result.id,
          state: result.state || "not recorded",
          finding: result.finding || "",
        }));
    if (!rows.length) {
      return "";
    }
    return `<ul class="inline-list">${rows.map((row) => `<li>${escapeHtml(row.label)}: ${escapeHtml(row.state)}${row.finding ? ` - ${escapeHtml(row.finding)}` : ""}</li>`).join("")}</ul>`;
  }

  function renderReleaseEvidence(release, deliverableRows, humanRequests) {
    const refs = new Set(release.artifact_refs || []);
    deliverableRows.forEach((row) => (row.artifact_refs || []).forEach((ref) => refs.add(ref)));
    humanRequests.forEach((request) => {
      (request.test_target?.evidence_refs || []).forEach((ref) => refs.add(ref));
      (request.decisions || []).forEach((decision) =>
        (decision.evidence_refs || []).forEach((ref) => refs.add(ref)),
      );
      (request.findings || []).forEach((finding) => {
        (finding.evidence_refs || []).forEach((ref) => refs.add(ref));
        (finding.resolution_evidence_refs || []).forEach((ref) => refs.add(ref));
      });
    });
    if (!refs.size) {
      return "";
    }
    return `<div class="release-detail-section"><h4>Evidence and attachments</h4><ul class="delivery-detail-list">${[...refs].sort().map((ref) => `<li><code>${escapeHtml(ref)}</code></li>`).join("")}</ul></div>`;
  }

  return {
    renderReleaseDetail,
    renderReleaseVerificationCommandResults,
  };
}

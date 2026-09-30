// Deliverables, review decisions, criteria, and review profiles.
import {
  byId,
  escapeAttr,
  escapeHtml,
  formData,
  humanizeToken,
  readablePreview,
  splitList,
  taskStateLabel,
  text,
  uniqueTextList,
  values,
} from "./team-ui-format.js";
import { renderIdentifierText } from "./team-identifiers.js";
import { decisionStates, terminalRecordStates } from "./team-ui-config.js";

export function createTeamReviewsController(peers) {

  function deliverableTask(deliverable) {
    return peers.app.state.tasks?.[text(deliverable?.task_id)] || null;
  }

  function deliverableDisplaySubject(deliverable) {
    const task = deliverableTask(deliverable);
    if (task?.title) {
      return task.title;
    }
    const firstLine = text(deliverable?.summary)
      .split(/\r?\n/)
      .map((line) => line.trim())
      .find(Boolean);
    return readablePreview(firstLine || "Deliverable outcome", 90);
  }

  function deliverableProducerLabel(deliverable) {
    const producer = peers.app.state.agents?.[text(deliverable?.producer_agent_id)];
    return producer ? peers.agents.agentLabel(producer) : text(deliverable?.producer_agent_id) || "Unassigned";
  }

  function renderRecordDate(timestamp, prefix = "") {
    if (!timestamp) {
      return "Date unavailable";
    }
    const label = peers.app.formatTimelineTimestamp(timestamp);
    return `${prefix ? `${prefix} ` : ""}<time datetime="${escapeAttr(timestamp)}">${escapeHtml(label)}</time>`;
  }

  function renderIdentifierCopy(value, { label = "Copy ID" } = {}) {
    const normalized = text(value);
    if (!normalized) {
      return "";
    }
    return `
      <span class="technical-identifier">
        <code>${escapeHtml(normalized)}</code>
        <button
          type="button"
          class="copy-identifier-button"
          data-copy-identifier="${escapeAttr(normalized)}"
          data-copy-label="${escapeAttr(label)}"
          aria-label="${escapeAttr(`${label}: ${normalized}`)}"
        >${escapeHtml(label)}</button>
      </span>
    `;
  }

  function renderTechnicalDetails(entries, { summary = "Technical details", disclosureKey = "" } = {}) {
    const rows = entries
      .map(([label, value, copyLabel]) => {
        const values = Array.isArray(value) ? uniqueTextList(value) : [text(value)].filter(Boolean);
        if (!values.length) {
          return "";
        }
        return `
          <div>
            <dt>${escapeHtml(label)}</dt>
            <dd>${values.map((item) => renderIdentifierCopy(item, { label: copyLabel || (label.toLowerCase().includes("id") ? "Copy ID" : "Copy") })).join("")}</dd>
          </div>
        `;
      })
      .filter(Boolean);
    if (!rows.length) {
      return "";
    }
    return `
      <details class="technical-details" ${peers.disclosures.teamDisclosureAttributes(disclosureKey)}>
        <summary>${escapeHtml(summary)}</summary>
        <dl class="technical-details-list">${rows.join("")}</dl>
      </details>
    `;
  }

  async function copyTechnicalIdentifier(button) {
    const value = text(button.dataset.copyIdentifier);
    if (!value) {
      return;
    }
    const originalLabel = text(button.dataset.copyLabel) || "Copy";
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(value);
      } else {
        const field = document.createElement("textarea");
        field.value = value;
        field.setAttribute("readonly", "");
        field.style.position = "fixed";
        field.style.opacity = "0";
        document.body.appendChild(field);
        field.select();
        if (!document.execCommand("copy")) {
          throw new Error("Clipboard copy is unavailable.");
        }
        field.remove();
      }
      button.textContent = "Copied";
      button.dataset.copyState = "copied";
      window.setTimeout(() => {
        button.textContent = originalLabel;
        delete button.dataset.copyState;
      }, 1600);
    } catch {
      peers.app.setStatus(`Could not copy ${value}.`, "error");
    }
  }

  function renderDeliverableOutcomes(deliverable) {
    const outcomes = deliverable.outcomes || [];
    if (!outcomes.length && !deliverable.completion) {
      return "";
    }
    const outcomeRows = outcomes.map((outcome) => {
      const name =
        outcome.definition_snapshot?.name ||
        values(peers.app.state.outcome_kind_definitions).find((definition) => definition.kind === outcome.kind)?.name ||
        humanizeToken(outcome.kind);
      return `<li><strong>${escapeHtml(name)}</strong>${(outcome.reference_ids || []).length ? ` · ${outcome.reference_ids.length} reference${outcome.reference_ids.length === 1 ? "" : "s"}` : ""}${(outcome.evidence_refs || []).length ? ` · ${outcome.evidence_refs.length} evidence item${outcome.evidence_refs.length === 1 ? "" : "s"}` : ""}</li>`;
    });
    if (deliverable.completion) {
      outcomeRows.push(`<li><strong>${escapeHtml(humanizeToken(deliverable.completion.kind || "completion"))}</strong></li>`);
    }
    return `<ul class="deliverable-outcome-list">${outcomeRows.join("")}</ul>`;
  }

  function renderDeliverableEvidence(deliverable) {
    const refs = new Set(deliverable.artifact_refs || []);
    (deliverable.outcomes || []).forEach((outcome) => {
      (outcome.evidence_refs || []).forEach((ref) => refs.add(ref));
    });
    if (!refs.size) {
      return `<p class="empty-state">No evidence or artifacts recorded.</p>`;
    }
    return `<ul class="deliverable-evidence-list">${[...refs].map((ref) => `<li><code>${escapeHtml(ref)}</code></li>`).join("")}</ul>`;
  }

  function renderDeliverableAcceptanceVerification(deliverable) {
    const verification = deliverable.verification;
    if (!verification) {
      return "";
    }
    const status = text(verification.status || "unknown");
    const commands = verification.command_results || [];
    const waiver = verification.waiver;
    return `
      <section class="deliverable-detail-group deliverable-acceptance-verification">
        <h4>Deliverable verification (diagnostic)</h4>
        <div class="delivery-detail-grid">
          <div><div class="delivery-detail-label">Status</div><div class="delivery-detail-value"><span class="state-pill ${escapeAttr(status)}">${escapeHtml(humanizeToken(status))}</span></div></div>
          <div><div class="delivery-detail-label">Provenance</div><div class="delivery-detail-value">${escapeHtml(humanizeToken(verification.provenance || "agent_reported"))}</div></div>
          ${verification.run_id ? `<div><div class="delivery-detail-label">Run</div><div class="delivery-detail-value">${renderIdentifierCopy(verification.run_id, { label: "Copy ID" })}</div></div>` : ""}
          ${verification.candidate_commit_sha ? `<div><div class="delivery-detail-label">Candidate</div><div class="delivery-detail-value"><code>${escapeHtml(verification.candidate_commit_sha)}</code></div></div>` : ""}
        </div>
        ${verification.summary ? `<p class="meta-line">${renderIdentifierText(verification.summary)}</p>` : ""}
        ${commands.length ? `<ul>${commands.map((command) => `<li><strong>${escapeHtml(command.name)}</strong> - ${escapeHtml(humanizeToken(command.state || "pending"))}${command.failure_kind ? ` (${escapeHtml(humanizeToken(command.failure_kind))})` : ""}</li>`).join("")}</ul>` : ""}
        ${(verification.evidence_refs || []).length ? `<p class="meta-line"><strong>Evidence:</strong> ${(verification.evidence_refs || []).map((ref) => `<code>${escapeHtml(ref)}</code>`).join(" ")}</p>` : ""}
        ${waiver ? `<p class="meta-line"><strong>Waived by ${escapeHtml(waiver.actor_id)}:</strong> ${renderIdentifierText(waiver.reason)}</p>` : ""}
      </section>
    `;
  }

  function renderDeliverableTechnicalDetails(deliverable) {
    const gitRefs = deliverable.git_refs || {};
    const initialRelease = deliverable.initial_release_disposition || {};
    return renderTechnicalDetails([
      ["Deliverable ID", deliverable.id],
      ["Task ID", deliverable.task_id],
      ["Producer ID", deliverable.producer_agent_id],
      ["Supersedes deliverable ID", deliverable.supersedes_deliverable_id],
      ["Superseded by deliverable ID", deliverable.superseded_by_deliverable_id],
      ["Source deliverable IDs", deliverable.source_deliverable_ids || []],
      ["Branch context ID", gitRefs.branch_context_id],
      ["Agent branch", gitRefs.agent_branch],
      ["Review branch", gitRefs.review_branch],
      ["Feature branch", gitRefs.feature_branch],
      ["Release branch", gitRefs.release_branch],
      ["Commit", gitRefs.commit_sha],
      ["Submitted commit", gitRefs.submitted_commit_sha],
      ["Reviewed commit", gitRefs.reviewed_commit_sha],
      ["Accepted immutable ref", gitRefs.accepted_pinned_ref],
      ["Acceptance policy version", deliverable.acceptance_policy_version],
      ["Composition source fingerprint", deliverable.dependency_composition_manifest?.source_fingerprint],
      ["Composition base commit", deliverable.dependency_composition_manifest?.base_commit_sha],
      ["Composition frontier commits", deliverable.dependency_composition_manifest?.frontier_commit_shas || []],
      ["Promotion IDs", gitRefs.promotion_record_ids || []],
      ["Initial release package ID", initialRelease.release_package_id],
    ], {
      disclosureKey: peers.disclosures.teamDisclosureKey("deliverable", deliverable.id, "technical"),
    });
  }

  function renderInitialReleaseDisposition(deliverable) {
    const disposition = deliverable.initial_release_disposition;
    if (!disposition) {
      return "";
    }
    const dispositionState = text(disposition.state || "pending");
    return `
      <section class="deliverable-detail-group deliverable-initial-release-disposition">
        <h4>Initial release decision</h4>
        <div class="delivery-detail-grid">
          <div><div class="delivery-detail-label">Disposition</div><div class="delivery-detail-value"><span class="state-pill ${escapeAttr(dispositionState)}">${escapeHtml(humanizeToken(dispositionState))}</span></div></div>
          ${disposition.decided_by_actor ? `<div><div class="delivery-detail-label">Decided by</div><div class="delivery-detail-value">${escapeHtml(disposition.decided_by_actor)}</div></div>` : ""}
          ${disposition.decided_at ? `<div><div class="delivery-detail-label">Decided</div><div class="delivery-detail-value">${renderRecordDate(disposition.decided_at)}</div></div>` : ""}
          ${disposition.release_package_id ? `<div><div class="delivery-detail-label">Release package</div><div class="delivery-detail-value">${renderIdentifierCopy(disposition.release_package_id, { label: "Copy ID" })}</div></div>` : ""}
        </div>
        ${disposition.reason ? `<p class="meta-line"><strong>Reason:</strong> ${renderIdentifierText(disposition.reason)}</p>` : ""}
        ${disposition.reopen_reason ? `<p class="meta-line"><strong>Last reopened:</strong> ${renderIdentifierText(disposition.reopen_reason)}${disposition.reopened_by_actor ? ` · ${escapeHtml(disposition.reopened_by_actor)}` : ""}${disposition.reopened_at ? ` · ${renderRecordDate(disposition.reopened_at)}` : ""}</p>` : ""}
      </section>
    `;
  }

  function renderDeliverableCard(deliverable, { compact = false } = {}) {
    const task = deliverableTask(deliverable);
    const subject = deliverableDisplaySubject(deliverable);
    const producer = deliverableProducerLabel(deliverable);
    const preview = readablePreview(deliverable.summary);
    const reviewIds = deliverable.review_request_ids || [];
    return `
      <li class="record-item compact-record-card deliverable-card${compact ? " deliverable-card-compact" : ""}" data-deliverable-id="${escapeAttr(deliverable.id)}">
        <details class="deliverable-card-details" ${peers.disclosures.teamDisclosureAttributes(peers.disclosures.teamDisclosureKey("deliverable", deliverable.id, "detail"))}>
          <summary class="compact-record-summary deliverable-card-summary">
            <span class="compact-record-main">
              <strong>${escapeHtml(subject)}</strong>
              <span>${escapeHtml(producer)} · ${renderRecordDate(deliverable.created_at)}</span>
              ${preview && preview !== subject ? `<span class="deliverable-summary-preview">${escapeHtml(preview)}</span>` : ""}
            </span>
            <span class="state-pill ${escapeAttr(deliverable.state)}">${escapeHtml(humanizeToken(deliverable.state))}</span>
          </summary>
          <div class="compact-record-body deliverable-card-body">
            <section class="deliverable-detail-group">
              <h4>Summary / outcome</h4>
              <p class="deliverable-full-summary">${renderIdentifierText(deliverable.summary)}</p>
              ${renderDeliverableOutcomes(deliverable)}
            </section>
            <section class="deliverable-detail-group">
              <h4>Task and producer</h4>
              <div class="delivery-detail-grid">
                <div><div class="delivery-detail-label">Task</div><div class="delivery-detail-value">${escapeHtml(task?.title || "Task unavailable")}${task ? ` <span class="state-pill ${escapeAttr(task.state)}">${escapeHtml(taskStateLabel(task))}</span>` : ""}</div></div>
                <div><div class="delivery-detail-label">Producer</div><div class="delivery-detail-value">${escapeHtml(producer)}</div></div>
                <div><div class="delivery-detail-label">Recorded</div><div class="delivery-detail-value">${renderRecordDate(deliverable.created_at)}</div></div>
                <div><div class="delivery-detail-label">Decision</div><div class="delivery-detail-value">${escapeHtml(humanizeToken(deliverable.acceptance_decision?.decision || deliverable.state))}</div></div>
              </div>
            </section>
            <section class="deliverable-detail-group">
              <h4>Evidence and artifacts</h4>
              ${renderDeliverableEvidence(deliverable)}
            </section>
            ${renderDeliverableAcceptanceVerification(deliverable)}
            ${renderInitialReleaseDisposition(deliverable)}
            <section class="deliverable-detail-group">
              <h4>Reviews and decision</h4>
              ${reviewIds.length ? renderReviewSummary(reviewIds) : `<p class="empty-state">No reviews recorded.</p>`}
              ${peers.recovery.renderRecordBlockers("deliverable", deliverable.id, deliverable.task_id)}
            </section>
            <section class="deliverable-detail-group deliverable-lineage-group">
              <h4>Lineage / technical details</h4>
              ${renderDeliverableTechnicalDetails(deliverable)}
            </section>
          </div>
        </details>
      </li>
    `;
  }

  function renderDeliverables(deliverables, { compact = false } = {}) {
    if (!deliverables.length) {
      return `<p class="empty-state">No deliverables.</p>`;
    }
    return `
      <ul class="deliverable-list${compact ? " compact-record-list" : ""}">
        ${deliverables.map((deliverable) => renderDeliverableCard(deliverable, { compact })).join("")}
      </ul>
    `;
  }

  function renderReviewSummary(reviewIds, { interactive = false } = {}) {
    if (!reviewIds.length) {
      return "";
    }
    const reviews = reviewIds.map((reviewId) => peers.app.state.review_requests[reviewId]).filter(Boolean);
    if (!reviews.length) {
      return "";
    }
    return `
      <ul class="review-list">
        ${reviews
          .map((review) => {
            const profile = review.review_profile_id
              ? peers.app.state.review_profiles[review.review_profile_id] || null
              : null;
            const deliverable = peers.app.state.deliverables[review.deliverable_id] || null;
            const carriedFindings = (deliverable?.carried_findings || []).filter(
              (finding) => finding.state === "open",
            );
            const reviewer = peers.app.state.agents[review.reviewer_agent_id] || null;
            const reviewerName = reviewer ? peers.agents.agentLabel(reviewer) : review.reviewer_agent_id;
            return `
              <li class="record-item">
                <div class="record-line">
                  <strong>Review by ${escapeHtml(reviewerName)}</strong>
                  <span class="state-pill ${escapeHtml(review.state)}">${escapeHtml(review.state)}</span>
                </div>
                <div class="meta-line">${profile?.name ? `Profile ${escapeHtml(profile.name)}` : "No review profile"}${review.decision_at ? ` · decided ${renderRecordDate(review.decision_at)}` : review.created_at ? ` · requested ${renderRecordDate(review.created_at)}` : ""}</div>
                ${review.instructions ? `<div class="meta-line">Instructions: ${renderIdentifierText(review.instructions)}</div>` : ""}
                ${(review.findings || []).length ? `<div class="meta-line">Findings: ${review.findings.map((finding) => renderIdentifierText(finding)).join("; ")}</div>` : ""}
                ${peers.recovery.renderRecordBlockers("review_request", review.id)}
                ${renderCriterionResults(review.criteria || [])}
                ${renderCarriedFindings(carriedFindings)}
                ${interactive ? renderReviewDecisionForm(review, profile, carriedFindings) : ""}
                ${renderTechnicalDetails([
                  ["Review ID", review.id],
                  ["Reviewer ID", review.reviewer_agent_id],
                  ["Review profile ID", review.review_profile_id],
                  ["Deliverable ID", review.deliverable_id],
                  ["Release ID", review.release_package_id],
                  ["Reviewed commit", review.reviewed_commit_sha],
                ], {
                  disclosureKey: peers.disclosures.teamDisclosureKey("review-request", review.id, "technical"),
                })}
              </li>
            `;
          })
          .join("")}
      </ul>
    `;
  }

  function renderReviewDecisionForm(review, profile, carriedFindings = []) {
    if (terminalRecordStates.has(review.state) || review.decision_at) {
      return "";
    }
    const selectedState = decisionStates.includes(review.state) ? review.state : "";
    const criteriaHelp = renderReviewCriteriaHelp(profile?.criteria || []);
    return `
      <form
        class="review-decision-form"
        id="review-decision-form-${escapeAttr(review.id)}"
        data-review-decision-form="true"
        data-review-request-id="${escapeAttr(review.id)}"
      >
        <label>
          Decision
          <select name="state" required>
            <option value="">Select</option>
            ${decisionStates
              .map((stateValue) => {
                const selected = stateValue === selectedState ? " selected" : "";
                return `<option value="${escapeAttr(stateValue)}"${selected}>${escapeHtml(stateValue)}</option>`;
              })
              .join("")}
          </select>
        </label>
        <label>
          Findings
          <textarea name="findings" rows="2"></textarea>
        </label>
        ${renderReviewCriterionInputs(profile?.criteria || [])}
        ${renderFindingResolutionInputs(carriedFindings)}
        ${criteriaHelp}
        <button type="submit">Submit decision</button>
      </form>
    `;
  }

  function renderReviewCriterionInputs(criteria) {
    if (!criteria.length) return "";
    return `
      <fieldset data-review-criterion-inputs>
        <legend>Criteria results</legend>
        ${criteria.map((criterion) => `
          <div class="criteria-editor-row" data-review-criterion-id="${escapeAttr(criterion.id)}">
            <label>${escapeHtml(criterion.label)}${criterion.required === false ? " (optional)" : " (required)"}
              <select name="criterion_state__${escapeAttr(criterion.id)}"${criterion.required === false ? "" : " required"}>
                <option value="">Select</option>
                <option value="passed">Passed</option>
                <option value="failed">Failed</option>
                <option value="skipped">Skipped</option>
              </select>
            </label>
            <label>Finding
              <input name="criterion_finding__${escapeAttr(criterion.id)}" type="text" />
            </label>
            <label>Evidence references (optional)
              <textarea name="criterion_evidence_refs__${escapeAttr(criterion.id)}" rows="2" placeholder="Optional diagnostic references"></textarea>
            </label>
          </div>
        `).join("")}
      </fieldset>
    `;
  }

  function renderCarriedFindings(findings) {
    if (!findings.length) return "";
    return `<div class="meta-line"><strong>Open carried findings:</strong><ul>${findings
      .map((finding) => `<li>${escapeHtml(finding.id)} - ${renderIdentifierText(finding.text)}</li>`)
      .join("")}</ul></div>`;
  }

  function renderFindingResolutionInputs(findings) {
    if (!findings.length) return "";
    return `
      <fieldset data-finding-resolution-inputs>
        <legend>Carried-finding resolutions</legend>
        ${findings.map((finding) => `
          <label>${escapeHtml(finding.text)}
            <input name="finding_resolution__${escapeAttr(finding.id)}" type="text" placeholder="Resolution and evidence" />
          </label>
        `).join("")}
      </fieldset>
    `;
  }

  function renderReviewCriteriaHelp(criteria) {
    if (!criteria.length) {
      return "";
    }
    return `
      <div class="meta-line">Profile criteria:</div>
      ${renderProfileCriteria(criteria)}
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
            const evidence = (criterion.evidence_refs || []).length
              ? ` · evidence: ${(criterion.evidence_refs || []).map((ref) => escapeHtml(ref)).join(", ")}`
              : "";
            return `<li>${escapeHtml(criterion.criterion_id)}: ${escapeHtml(criterion.state)}${finding}${evidence}</li>`;
          })
          .join("")}
      </ul>
    `;
  }

  function renderReviewProfiles() {
    if (!document.querySelector("[data-review-profiles-view]")) {
      return;
    }
    const allProfiles = values(peers.app.state.review_profiles).sort(peers.people.compareReviewProfiles);
    const profiles = allProfiles.filter(peers.app.isVisibleReviewProfile);
    if (!allProfiles.length) {
      peers.app.renderIntoActiveTargets("[data-review-profiles-view]", `<p class="empty-state">No review profiles.</p>`);
      return;
    }
    if (!profiles.length) {
      peers.app.renderIntoActiveTargets("[data-review-profiles-view]", `<p class="empty-state">No visible review profiles.</p>`);
      return;
    }
    peers.app.renderIntoActiveTargets("[data-review-profiles-view]", `
      <ul class="review-list compact-record-list">
        ${profiles.map((profile) => renderCompactReviewProfile(profile)).join("")}
      </ul>
    `);
  }

  function renderCompactReviewProfile(profile) {
    const criteria = profile.criteria || [];
    const scope = reviewProfileScope(profile);
    const reviewerLabels = reviewProfileReviewerLabels(profile);
    const outcomeKinds = profile.required_outcome_kinds || [];
    const outcomeLabels = outcomeKinds.map((kind) => reviewProfileOutcomeLabel(profile, kind));
    return `
      <li class="record-item compact-record-card review-profile-card">
        <div class="review-profile-card-header">
          <span class="review-profile-card-title">
            <strong>${escapeHtml(profile.name)}</strong>
            <span>${escapeHtml(scope.summary)} · ${criteria.length} ${criteria.length === 1 ? "criterion" : "criteria"}</span>
          </span>
          <div class="record-actions">
            <span class="state-pill ${profile.archived ? "blocked" : "accepted"}">${profile.archived ? "archived" : "active"}</span>
            ${peers.access.editButton("review-profile", profile.id)}
          </div>
        </div>
        <details class="review-profile-card-details" ${peers.disclosures.teamDisclosureAttributes(peers.disclosures.teamDisclosureKey("review-profile", profile.id, "detail"))}>
          <summary class="review-profile-detail-toggle">View profile</summary>
          <div class="review-profile-card-body">
            <div class="review-profile-section-grid">
              <section class="review-profile-section">
                <h4>Purpose</h4>
                <p>${escapeHtml(profile.description || "No purpose description provided.")}</p>
              </section>
              <section class="review-profile-section">
                <h4>Reviewers</h4>
                ${renderReviewProfileChips(reviewerLabels, "All reviewer roles")}
              </section>
              <section class="review-profile-section">
                <h4>Required outcomes</h4>
                ${renderReviewProfileChips(outcomeLabels, "No required outcomes")}
              </section>
              <section class="review-profile-section review-profile-criteria-section">
                <h4>Criteria</h4>
                ${renderProfileCriteria(criteria)}
              </section>
            </div>
            ${renderReviewProfileTechnicalDetails(profile, scope, outcomeKinds)}
          </div>
        </details>
      </li>
    `;
  }

  function reviewProfileScope(profile) {
    if (profile.project_id) {
      const project = peers.app.state.projects[text(profile.project_id)];
      return {
        idLabel: "Project ID",
        id: text(profile.project_id),
        summary: `Project · ${text(project?.name) || "Unavailable project"}`,
      };
    }
    const team = peers.app.state.teams[text(profile.team_id)];
    return {
      idLabel: "Team ID",
      id: text(profile.team_id),
      summary: `Team-wide · ${text(team?.name) || "Unavailable team"}`,
    };
  }

  function reviewProfileReviewerLabels(profile) {
    return (profile.applicable_role_ids || []).map((roleId) => {
      const role = peers.app.state.roles[text(roleId)];
      if (role) {
        return peers.agents.roleLabel(role);
      }
      const roleKey = text(roleId).split("__").pop() || "";
      return roleKey ? humanizeToken(roleKey.replace(/-/g, "_")) : "Unavailable role";
    });
  }

  function reviewProfileOutcomeLabel(profile, kind) {
    const normalizedKind = text(kind);
    if (!normalizedKind) {
      return "";
    }
    const project = peers.app.state.projects[text(profile.project_id)];
    const definition = project
      ? peers.projects.effectiveOutcomeKindsForProject(project).find((candidate) => text(candidate.kind) === normalizedKind)
      : values(peers.app.state.outcome_kind_definitions)
          .filter((candidate) =>
            text(candidate.team_id) === text(profile.team_id)
            && !candidate.project_id
            && !candidate.archived
            && text(candidate.kind) === normalizedKind
          )
          .sort(byId)
          .at(-1);
    return text(definition?.name) || humanizeToken(normalizedKind);
  }

  function renderReviewProfileChips(labels, emptyLabel) {
    const visibleLabels = labels.map(text).filter(Boolean);
    if (!visibleLabels.length) {
      return `<p class="review-profile-empty">${escapeHtml(emptyLabel)}</p>`;
    }
    return `
      <ul class="review-profile-chip-list">
        ${visibleLabels.map((label) => `<li class="review-profile-chip">${escapeHtml(label)}</li>`).join("")}
      </ul>
    `;
  }

  function renderProfileCriteria(criteria) {
    if (!criteria.length) {
      return `<p class="review-profile-empty">No criteria defined.</p>`;
    }
    return `
      <ul class="review-profile-criteria-list">
        ${criteria
          .map((criterion) => {
            const required = criterion.required !== false;
            return `
              <li class="review-profile-criterion">
                <div class="review-profile-criterion-header">
                  <strong>${escapeHtml(criterion.label || "Unnamed criterion")}</strong>
                  <span class="criterion-requirement-badge ${required ? "required" : "optional"}">${required ? "Required" : "Optional"}</span>
                </div>
                ${criterion.description ? `<p>${escapeHtml(criterion.description)}</p>` : ""}
              </li>
            `;
          })
          .join("")}
      </ul>
    `;
  }

  function renderReviewProfileTechnicalDetails(profile, scope, outcomeKinds) {
    const criteria = profile.criteria || [];
    return `
      <details class="technical-details review-profile-technical-details" ${peers.disclosures.teamDisclosureAttributes(peers.disclosures.teamDisclosureKey("review-profile", profile.id, "technical"))}>
        <summary>Technical details</summary>
        <dl class="technical-details-list">
          <div>
            <dt>Profile ID</dt>
            <dd><code>${escapeHtml(profile.id)}</code></dd>
          </div>
          <div>
            <dt>${escapeHtml(scope.idLabel)}</dt>
            <dd><code>${escapeHtml(scope.id)}</code></dd>
          </div>
          ${(profile.applicable_role_ids || []).length ? `
            <div>
              <dt>Reviewer role IDs</dt>
              <dd>${profile.applicable_role_ids.map((roleId) => `<code>${escapeHtml(roleId)}</code>`).join(" ")}</dd>
            </div>
          ` : ""}
          ${outcomeKinds.length ? `
            <div>
              <dt>Outcome kinds</dt>
              <dd>${outcomeKinds.map((kind) => `<code>${escapeHtml(kind)}</code>`).join(" ")}</dd>
            </div>
          ` : ""}
          ${criteria.length ? `
            <div>
              <dt>Criterion IDs</dt>
              <dd>${criteria.map((criterion) => `<code>${escapeHtml(criterion.id)}</code>`).join(" ")}</dd>
            </div>
          ` : ""}
        </dl>
      </details>
    `;
  }

  function renderReviewProfileCriteriaEditor(criteria = []) {
    const rows = criteria.length ? criteria : [{}];
    return `
      <fieldset class="criteria-editor" data-review-profile-criteria-editor>
        <legend>Criteria</legend>
        <div class="criteria-editor-rows" data-review-profile-criteria-rows>
          ${rows.map((criterion) => renderReviewProfileCriterionRow(criterion)).join("")}
        </div>
        <button class="record-action criteria-editor-add" type="button" data-review-profile-criterion-add>Add criterion</button>
      </fieldset>
    `;
  }

  function renderReviewProfileCriterionRow(criterion = {}) {
    const required = criterion.required !== false;
    return `
      <div class="criteria-editor-row" data-review-profile-criterion-row>
        <label>Label <input name="criterion_label" value="${escapeAttr(criterion.label || "")}" placeholder="Authorization" /></label>
        <label>Description <input name="criterion_description" value="${escapeAttr(criterion.description || "")}" placeholder="What reviewers should check" /></label>
        <label>Key <input name="criterion_id" value="${escapeAttr(criterion.id || "")}" placeholder="Auto from label" /></label>
        <label class="checkbox-label criteria-editor-required"><input name="criterion_required" type="checkbox"${required ? " checked" : ""} /> Required</label>
        <button class="record-action criteria-editor-remove" type="button" data-review-profile-criterion-remove>Remove</button>
      </div>
    `;
  }

  function addReviewProfileCriterionRow(editor) {
    const rows = editor?.querySelector?.("[data-review-profile-criteria-rows]");
    if (!rows) {
      return;
    }
    const wrapper = document.createElement("div");
    wrapper.innerHTML = renderReviewProfileCriterionRow({});
    const row = wrapper.firstElementChild;
    if (!row) {
      return;
    }
    rows.appendChild(row);
    peers.tooltips.decorateFieldTooltips(row);
    peers.drafts.rememberFormDraft(editor.closest("form"));
    row.querySelector('input[name="criterion_label"]')?.focus();
  }

  function removeReviewProfileCriterionRow(button) {
    const editor = button.closest("[data-review-profile-criteria-editor]");
    const row = button.closest("[data-review-profile-criterion-row]");
    const rows = editor?.querySelector?.("[data-review-profile-criteria-rows]");
    if (!row || !rows) {
      return;
    }
    row.remove();
    if (!rows.querySelector("[data-review-profile-criterion-row]")) {
      addReviewProfileCriterionRow(editor);
      return;
    }
    peers.drafts.rememberFormDraft(editor.closest("form"));
  }

  function reviewProfileCriteriaFromForm(form) {
    const rows = Array.from(form.querySelectorAll("[data-review-profile-criterion-row]"));
    const usedIds = new Set();
    return rows
      .map((row, index) => reviewProfileCriterionFromRow(row, index, usedIds))
      .filter(Boolean);
  }

  function reviewProfileCriteriaDraftFromForm(form) {
    const rows = Array.from(form.querySelectorAll("[data-review-profile-criterion-row]"));
    if (!rows.length) {
      return null;
    }
    return rows.map((row) => ({
      id: text(row.querySelector('[name="criterion_id"]')?.value),
      label: text(row.querySelector('[name="criterion_label"]')?.value),
      description: text(row.querySelector('[name="criterion_description"]')?.value),
      required: row.querySelector('[name="criterion_required"]')?.checked !== false,
    }));
  }

  function restoreReviewProfileCriteriaDraft(form, criteria) {
    const rows = form.querySelector("[data-review-profile-criteria-rows]");
    if (!rows || !Array.isArray(criteria)) {
      return;
    }
    rows.innerHTML = (criteria.length ? criteria : [{}])
      .map((criterion) => renderReviewProfileCriterionRow(criterion))
      .join("");
    peers.tooltips.decorateFieldTooltips(rows);
  }

  function reviewProfileCriterionFromRow(row, index, usedIds) {
    const label = text(row.querySelector('[name="criterion_label"]')?.value);
    const description = text(row.querySelector('[name="criterion_description"]')?.value);
    const explicitId = text(row.querySelector('[name="criterion_id"]')?.value);
    if (!label && !description && !explicitId) {
      return null;
    }
    const baseId = explicitId || criterionIdFromText(label || description || `criterion ${index + 1}`);
    const id = uniqueCriterionId(baseId, usedIds);
    usedIds.add(id);
    return {
      id,
      label: label || id,
      description,
      required: row.querySelector('[name="criterion_required"]')?.checked !== false,
    };
  }

  function criterionIdFromText(value) {
    const slug = text(value)
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "");
    return slug || "criterion";
  }

  function uniqueCriterionId(baseId, usedIds) {
    const normalized = text(baseId) || "criterion";
    if (!usedIds.has(normalized)) {
      return normalized;
    }
    let suffix = 2;
    while (usedIds.has(`${normalized}-${suffix}`)) {
      suffix += 1;
    }
    return `${normalized}-${suffix}`;
  }

  async function submitReviewDecision(form) {
    if (!peers.access.guardTeamMutation("record review decisions")) {
      return;
    }
    const data = formData(form);
    const reviewId = text(data.review_request_id || form.dataset.reviewRequestId);
    if (!reviewId) {
      peers.app.setStatus("Review is required.", "error");
      return;
    }
    const stateValue = text(data.state);
    if (!stateValue) {
      peers.app.setStatus("Decision is required.", "error");
      return;
    }
    const payload = {
      state: stateValue,
      findings: splitList(data.findings),
      criteria: reviewCriterionResultsFromForm(form),
      finding_resolutions: findingResolutionsFromForm(form),
    };
    await peers.mutations.submitMutation(
      form,
      `/review-requests/${encodeURIComponent(reviewId)}/decision`,
      "POST",
      payload,
      { resetOnSuccess: false },
      );
  }

  function reviewCriterionResultsFromForm(form) {
    return [...form.querySelectorAll("[data-review-criterion-id]")]
      .map((row) => {
        const criterionId = text(row.dataset.reviewCriterionId);
        const stateValue = text(row.querySelector("select")?.value);
        if (!stateValue) return null;
        return {
          criterion_id: criterionId,
          state: stateValue,
          finding: text(row.querySelector('input[type="text"]')?.value),
          evidence_refs: splitList(
            row.querySelector('[name^="criterion_evidence_refs__"]')?.value,
          ),
        };
      })
      .filter(Boolean);
  }

  function findingResolutionsFromForm(form) {
    return [...form.querySelectorAll('[name^="finding_resolution__"]')]
      .map((input) => {
        const resolution = text(input.value);
        if (!resolution) return null;
        return {
          finding_id: input.name.slice("finding_resolution__".length),
          resolution,
          evidence_refs: [],
        };
      })
      .filter(Boolean);
  }
  function bindReviewDecisionForm() {

    const decideReviewForm = document.querySelector("#decide-review-form");
    decideReviewForm?.addEventListener("submit", (event) => {
      event.preventDefault();
      void submitReviewDecision(decideReviewForm);
    });
  }
  function handleCriteriaClick(event) {
    const addButton = event.target?.closest?.("[data-review-profile-criterion-add]");
    if (addButton) {
      event.preventDefault();
      if (!peers.access.guardTeamMutation("edit review profile criteria")) {
        return true;
      }
      addReviewProfileCriterionRow(addButton.closest("[data-review-profile-criteria-editor]"));
      return true;
    }

    const removeButton = event.target?.closest?.("[data-review-profile-criterion-remove]");
    if (!removeButton) {
      return true;
    }
    event.preventDefault();
    if (!peers.access.guardTeamMutation("edit review profile criteria")) {
      return true;
    }
    removeReviewProfileCriterionRow(removeButton);
    return false;
  }

  return {
    bindReviewDecisionForm,
    copyTechnicalIdentifier,
    criterionIdFromText,
    deliverableDisplaySubject,
    deliverableProducerLabel,
    deliverableTask,
    handleCriteriaClick,
    renderCompactReviewProfile,
    renderDeliverableAcceptanceVerification,
    renderDeliverableEvidence,
    renderDeliverableOutcomes,
    renderDeliverableTechnicalDetails,
    renderDeliverables,
    renderIdentifierCopy,
    renderInitialReleaseDisposition,
    renderRecordDate,
    renderReviewProfileCriteriaEditor,
    renderReviewProfiles,
    renderReviewSummary,
    renderTechnicalDetails,
    restoreReviewProfileCriteriaDraft,
    reviewProfileCriteriaDraftFromForm,
    reviewProfileCriteriaFromForm,
    submitReviewDecision,
  };
}

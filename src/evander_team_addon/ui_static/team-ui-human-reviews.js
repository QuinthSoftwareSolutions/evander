// Human review queue, findings editor, and audited review decisions.
import { API_BASE, initialHumanReviewDeepLink } from "./team-ui-config.js";
import {
  escapeAttr,
  escapeHtml,
  formData,
  humanizeToken,
  readablePreview,
  responseText,
  splitList,
  text,
} from "./team-ui-format.js";
import { renderIdentifierText } from "./team-identifiers.js";

export function createTeamHumanReviewsController(peers) {

  let myHumanReviewsOnly = initialHumanReviewDeepLink.enabled;

  let focusedHumanReviewRequestId = initialHumanReviewDeepLink.requestId;

  let humanReviewQueue = {
    loaded: false,
    loading: false,
    error: null,
    requests: [],
  };

  async function loadMyHumanReviews() {
    if (humanReviewQueue.loading) {
      return;
    }
    humanReviewQueue = { ...humanReviewQueue, loading: true, error: null };
    peers.releases.renderReleases();
    try {
      const query = new URLSearchParams({ assigned_to: "me" });
      const response = await peers.access.teamFetch(`${API_BASE}/human-review-requests?${query.toString()}`, {
        headers: { Accept: "application/json" },
      });
      if (!response.ok) {
        throw new Error(await responseText(response));
      }
      const payload = await response.json();
      humanReviewQueue = {
        loaded: true,
        loading: false,
        error: null,
        requests: payload.human_review_requests || [],
      };
      peers.app.state.revision = payload.revision ?? peers.app.state.revision;
    } catch (error) {
      humanReviewQueue = {
        ...humanReviewQueue,
        loaded: false,
        loading: false,
        error: error instanceof Error ? error.message : "Human reviews could not be loaded.",
      };
    }
    peers.app.renderShell();
    peers.releases.renderReleases();
  }

  async function openHumanReviewDeepLink(requestId) {
    const normalizedRequestId = text(requestId);
    myHumanReviewsOnly = true;
    focusedHumanReviewRequestId = normalizedRequestId;
    await loadMyHumanReviews();
    if (!focusedHumanReviewRequestId) {
      return true;
    }
    const request = humanReviewQueue.requests.find(
      (item) => text(item.id) === focusedHumanReviewRequestId,
    );
    const releaseId = text(request?.release_package_id);
    if (!request || !releaseId || !peers.app.state.release_packages?.[releaseId]) {
      focusedHumanReviewRequestId = "";
      peers.releases.renderReleases();
      return false;
    }
    peers.releases.expandReleaseDetail(releaseId);
    peers.disclosures.openDisclosure(
      peers.disclosures.teamDisclosureStateKey(peers.disclosures.teamDisclosureKey("release-card", releaseId, "detail")),
    );
    peers.releases.renderReleases();
    await peers.releases.loadReleaseDetail(releaseId);
    focusHumanReviewRequestElement(focusedHumanReviewRequestId);
    return true;
  }

  function focusHumanReviewRequestElement(requestId) {
    const element = document.getElementById(`human-review-${text(requestId)}`);
    if (!element) {
      return;
    }
    const disclosure = element.closest("details[data-team-disclosure-key]");
    if (disclosure) {
      disclosure.open = true;
      const disclosureKey = text(disclosure.dataset.teamDisclosureKey);
      if (disclosureKey) {
        peers.disclosures.openDisclosure(disclosureKey);
      }
    }
    element.focus({ preventScroll: true });
    element.scrollIntoView({ block: "center", behavior: "auto" });
  }

  function renderReleaseHumanRequests(requests, manifest, releaseId) {
    const carriedFindings = manifest.carried_human_findings || [];
    if (!requests.length) {
      return manifest.human_review_requirement
        ? `<div class="release-detail-section"><h4>Human acceptance</h4><p class="empty-state">Human review is required, but detailed requests are unavailable to this user or have not been created yet.</p></div>`
        : "";
    }
    return `
      <div class="release-detail-section">
        <h4>Human request and decision history</h4>
        ${carriedFindings.length ? `<div class="release-carried-findings"><strong>Carried findings requiring explicit confirmation:</strong> ${escapeHtml(carriedFindings.map((finding) => finding.finding_id || finding.id).join(", "))}</div>` : ""}
        <div class="human-review-request-list">
          ${requests.map((request) => renderHumanReviewRequest(request, carriedFindings, releaseId)).join("")}
        </div>
      </div>
    `;
  }

  function renderHumanReviewRequest(request, carriedFindings, releaseId = "") {
    const assignment = request.current_assignment || {};
    const target = request.test_target || null;
    const checklist = request.requirement_snapshot?.checklist || [];
    const decisions = request.decisions || [];
    const findings = request.findings || [];
    return `
      <article class="human-review-request${text(request.id) === focusedHumanReviewRequestId ? " human-review-request-focused" : ""}" id="human-review-${escapeAttr(request.id)}" tabindex="-1">
        <div class="record-line">
          <strong>Human acceptance request</strong>
          <span class="state-pill ${escapeAttr(request.state)}">${escapeHtml(request.state)}</span>
        </div>
        <div class="meta-line">${request.ready_at ? `Ready ${peers.reviews.renderRecordDate(request.ready_at)}` : "Preparation pending"} · request revision ${Number(request.revision || 0)}</div>
        <div class="meta-line">Assigned reviewers: ${escapeHtml([...(assignment.explicitly_assigned_user_ids || []), ...(assignment.eligible_user_ids || [])].filter((value, index, values_) => values_.indexOf(value) === index).length || "none")}</div>
        <div class="meta-line">Preparation mode ${escapeHtml(humanizeToken(request.requirement_snapshot?.preparation_mode || "manual"))} · ${checklist.length} checklist item${checklist.length === 1 ? "" : "s"}</div>
        ${request.requirement_snapshot?.test_instructions ? `<div class="human-test-instructions"><strong>Test instructions:</strong> ${renderIdentifierText(request.requirement_snapshot.test_instructions)}</div>` : ""}
        ${renderHumanReviewBundleDownload(request)}
        ${renderHumanReviewTarget(target)}
        ${renderHumanChecklistHistory(checklist)}
        ${renderHumanDecisionHistory(decisions)}
        ${renderHumanFindingHistory(
          findings,
          request.correction_tasks || [],
          request.target_deliverable_options || [],
        )}
        ${request.state === "pending" ? renderHumanReviewDecisionForm(request, checklist, carriedFindings) : ""}
        ${renderHumanReviewOwnerActions(request)}
        ${peers.releases.renderReleaseCancellation(request.release || peers.app.state.release_packages?.[request.release_package_id] || {})}
        ${peers.reviews.renderTechnicalDetails([
          ["Human review request ID", request.id],
          ["Release ID", request.release_package_id],
          ["Candidate commit", request.candidate_commit_sha],
          ["Candidate tree", request.candidate_tree_hash],
        ], {
          disclosureKey: peers.disclosures.teamDisclosureKey("human-review", releaseId || request.release_package_id, request.id, "technical"),
        })}
      </article>
    `;
  }

  function renderHumanReviewBundleDownload(request) {
    const requestId = text(request.id);
    if (!requestId) {
      return "";
    }
    const href = `${API_BASE}/human-review-requests/${encodeURIComponent(requestId)}/review-bundle`;
    return `
      <div class="human-review-bundle">
        <a class="human-review-bundle-download" href="${escapeAttr(href)}" download>Download review bundle</a>
        <span class="meta-line">Source ZIP for this pinned candidate. Unpack it, then run the normal setup such as <code>dotnet restore</code> and <code>npm ci</code> where applicable.</span>
      </div>
    `;
  }

  function renderHumanReviewTarget(target) {
    if (!target) {
      return `<div class="meta-line">Preview/test target: manual instructions only or preparation still pending.</div>`;
    }
    const targetName = target.url || target.alias || "prepared target";
    return `
      <div class="human-review-target">
        <strong>Prepared test target:</strong> <code>${escapeHtml(targetName)}</code>
        ${target.expires_at ? `<div class="meta-line">Expires ${escapeHtml(target.expires_at)}</div>` : ""}
        ${target.instructions ? `<div>${renderIdentifierText(target.instructions)}</div>` : ""}
        ${(target.evidence_refs || []).length ? `<div class="meta-line">Target evidence: ${escapeHtml(target.evidence_refs.join(", "))}</div>` : ""}
      </div>
    `;
  }

  function renderHumanChecklistHistory(checklist) {
    if (!checklist.length) {
      return `<div class="meta-line">Human checklist: no configured items.</div>`;
    }
    return `<ul class="human-review-checklist">${checklist.map((item) => `<li><strong>${escapeHtml(item.label)}</strong> (${item.required === false ? "optional" : "required"})${item.description ? ` - ${renderIdentifierText(item.description)}` : ""}</li>`).join("")}</ul>`;
  }

  function renderHumanDecisionHistory(decisions) {
    if (!decisions.length) {
      return "";
    }
    return `<ul class="delivery-detail-list">${decisions.map((decision) => `<li>
      <strong>${escapeHtml(decision.decision)}</strong> by ${escapeHtml(decision.reviewer_display_name_snapshot || decision.reviewer_user_id)} at ${escapeHtml(decision.decided_at || "")}
      ${decision.summary ? `<div>${renderIdentifierText(decision.summary)}</div>` : ""}
      ${(decision.evidence_refs || []).length ? `<div class="meta-line">Evidence: ${escapeHtml(decision.evidence_refs.join(", "))}</div>` : ""}
    </li>`).join("")}</ul>`;
  }

  function humanReviewTargetOptionLabel(option) {
    const title =
      text(option?.task_title) ||
      text(option?.summary) ||
      text(option?.deliverable_id) ||
      "Included work";
    const producer =
      text(option?.producer?.display_name) ||
      text(option?.producer?.agent_id);
    const parts = [
      title,
      producer ? `by ${producer}` : "",
      option?.is_top_level ? "included deliverable" : "contribution",
    ].filter(Boolean);
    return parts.join(" · ");
  }

  function humanReviewTargetLabels(targetIds, targetOptions) {
    const optionsById = new Map(
      (targetOptions || []).map((option) => [text(option.deliverable_id), option]),
    );
    return (targetIds || []).map((deliverableId) => {
      const option = optionsById.get(text(deliverableId));
      return option
        ? humanReviewTargetOptionLabel(option)
        : text(deliverableId);
    });
  }

  function renderHumanFindingHistory(findings, correctionTasks, targetOptions = []) {
    if (!findings.length && !correctionTasks.length) {
      return "";
    }
    return `
      <div class="human-finding-history">
        <strong>Findings and correction work</strong>
        <ul class="delivery-detail-list">
          ${findings.map((finding) => `<li>
            <strong>${escapeHtml(finding.id)}</strong> <span class="state-pill ${escapeAttr(finding.state)}">${escapeHtml(finding.state)}</span> - ${renderIdentifierText(finding.text)}
            <div class="meta-line">Correction task ${escapeHtml(finding.correction_task_id)}${text(finding.target_scope) === "release_candidate" ? " · affected work: Entire release candidate" : finding.target_deliverable_ids?.length ? ` · affected work: ${escapeHtml(humanReviewTargetLabels(finding.target_deliverable_ids, targetOptions).join(", "))}` : ""}</div>
            ${finding.proposed_resolution ? `<div>Resolution proposal: ${renderIdentifierText(finding.proposed_resolution)}</div>` : ""}
            ${(finding.resolution_evidence_refs || []).length ? `<div class="meta-line">Resolution evidence: ${escapeHtml(finding.resolution_evidence_refs.join(", "))}</div>` : ""}
          </li>`).join("")}
          ${correctionTasks.map((task) => `<li><strong>${escapeHtml(task.id)}</strong> - ${escapeHtml(task.title)} <span class="state-pill ${escapeAttr(task.state)}">${escapeHtml(task.state)}</span>${task.originating_task_id ? ` - origin ${escapeHtml(task.originating_task_id)}` : ""}</li>`).join("")}
        </ul>
      </div>
    `;
  }

  function humanReviewFindingListFieldValue(value) {
    if (Array.isArray(value)) {
      return value.join("\n");
    }
    return value === null || value === undefined ? "" : String(value);
  }

  function renderHumanReviewTargetSelector(
    targetOptions,
    selectedTargetIds,
    selectedTargetScope,
  ) {
    const selected = new Set(splitList(selectedTargetIds));
    const scope = text(selectedTargetScope)
      || (selected.size ? "deliverables" : "");
    return `
      <div class="human-review-finding-targets" role="group" aria-label="Affected work">
        <span>Affected work</span>
        <div class="human-review-finding-target-options">
          <label class="checkbox-label">
            <input
              type="checkbox"
              name="human_finding_target_release_candidate"
              ${scope === "release_candidate" ? "checked" : ""}
            />
            <span>
              <strong>Entire release candidate</strong>
              <small>Route release-wide correction work to the project lead.</small>
            </span>
          </label>
          ${(targetOptions || []).map((option) => `
            <label class="checkbox-label">
              <input
                type="checkbox"
                name="human_finding_target_deliverable_ids"
                value="${escapeAttr(option.deliverable_id)}"
                ${selected.has(text(option.deliverable_id)) ? "checked" : ""}
              />
              <span>
                <strong>${escapeHtml(humanReviewTargetOptionLabel(option))}</strong>
                ${option.summary && text(option.summary) !== text(option.task_title) ? `<small>${escapeHtml(readablePreview(option.summary, 180))}</small>` : ""}
              </span>
            </label>
          `).join("")}
        </div>
      </div>
    `;
  }

  function renderHumanReviewFindingRow(finding = {}, targetOptions = []) {
    return `
      <div class="human-review-finding-row" data-human-review-finding-row>
        <div class="human-review-finding-row-heading">
          <strong data-human-review-finding-number>Finding</strong>
          <button
            class="record-action human-review-finding-remove"
            type="button"
            data-human-review-finding-remove
          >Remove</button>
        </div>
        <label class="human-review-finding-description">
          <span>Description</span>
          <textarea
            name="human_finding_text"
            rows="3"
            placeholder="Describe one concrete, actionable problem"
          >${escapeHtml(finding.text || "")}</textarea>
        </label>
        <div class="human-review-finding-metadata">
          ${renderHumanReviewTargetSelector(
            targetOptions,
            finding.target_deliverable_ids,
            finding.target_scope,
          )}
          <label>
            <span>Evidence references <small>(optional)</small></span>
            <textarea
              name="human_finding_evidence_refs"
              rows="2"
              placeholder="artifact-space:..., one per line"
            >${escapeHtml(humanReviewFindingListFieldValue(finding.evidence_refs))}</textarea>
          </label>
        </div>
      </div>
    `;
  }

  function renderHumanReviewFindingsEditor(targetOptions = []) {
    return `
      <div
        class="human-review-findings-editor"
        data-human-review-findings-editor
        data-human-review-target-options="${escapeAttr(JSON.stringify(targetOptions))}"
      >
        <div class="human-review-findings-heading">
          <strong>Findings</strong>
          <span>Required only when requesting changes.</span>
        </div>
        <div class="human-review-finding-rows" data-human-review-finding-rows></div>
        <p class="human-review-findings-empty" data-human-review-findings-empty>No findings added.</p>
        <button
          class="record-action human-review-finding-add"
          type="button"
          data-human-review-finding-add
        >Add finding</button>
      </div>
    `;
  }

  function humanReviewTargetOptionsFromEditor(editor) {
    try {
      const options = JSON.parse(editor?.dataset?.humanReviewTargetOptions || "[]");
      return Array.isArray(options) ? options : [];
    } catch {
      return [];
    }
  }

  function syncHumanReviewFindingEditor(editor) {
    if (!editor) {
      return;
    }
    const rows = Array.from(editor.querySelectorAll("[data-human-review-finding-row]"));
    rows.forEach((row, index) => {
      const number = row.querySelector("[data-human-review-finding-number]");
      if (number) {
        number.textContent = `Finding ${index + 1}`;
      }
      const remove = row.querySelector("[data-human-review-finding-remove]");
      if (remove) {
        remove.setAttribute("aria-label", `Remove finding ${index + 1}`);
      }
    });
    const empty = editor.querySelector("[data-human-review-findings-empty]");
    if (empty) {
      empty.hidden = rows.length > 0;
    }
  }

  function addHumanReviewFindingRow(editor, finding = {}) {
    const rows = editor?.querySelector?.("[data-human-review-finding-rows]");
    if (!rows) {
      return;
    }
    const wrapper = document.createElement("div");
    wrapper.innerHTML = renderHumanReviewFindingRow(
      finding,
      humanReviewTargetOptionsFromEditor(editor),
    );
    const row = wrapper.firstElementChild;
    if (!row) {
      return;
    }
    rows.appendChild(row);
    peers.tooltips.decorateFieldTooltips(row);
    syncHumanReviewFindingEditor(editor);
    peers.drafts.rememberFormDraft(editor.closest("form"));
    row.querySelector('[name="human_finding_text"]')?.focus();
  }

  function removeHumanReviewFindingRow(button) {
    const editor = button.closest("[data-human-review-findings-editor]");
    button.closest("[data-human-review-finding-row]")?.remove();
    syncHumanReviewFindingEditor(editor);
    peers.drafts.rememberFormDraft(editor?.closest("form"));
  }

  function humanReviewFindingFromRow(row) {
    const targetDeliverableIds = Array.from(
      row.querySelectorAll(
        'input[name="human_finding_target_deliverable_ids"]:checked',
      ),
    ).map((field) => text(field.value)).filter(Boolean);
    const targetsEntireRelease = Boolean(
      row.querySelector(
        'input[name="human_finding_target_release_candidate"]:checked',
      ),
    );
    return {
      text: text(row.querySelector('[name="human_finding_text"]')?.value),
      target_scope: targetsEntireRelease
        ? "release_candidate"
        : targetDeliverableIds.length
          ? "deliverables"
          : "",
      target_deliverable_ids: targetDeliverableIds,
      evidence_refs: splitList(
        row.querySelector('[name="human_finding_evidence_refs"]')?.value,
      ),
    };
  }

  function humanReviewFindingsFromForm(form, { validate = false } = {}) {
    const rows = Array.from(form.querySelectorAll("[data-human-review-finding-row]"));
    const findings = [];
    let valid = true;
    rows.forEach((row) => {
      const description = row.querySelector('[name="human_finding_text"]');
      const releaseTarget = row.querySelector(
        '[name="human_finding_target_release_candidate"]',
      );
      description?.setCustomValidity("");
      releaseTarget?.setCustomValidity("");
      const finding = humanReviewFindingFromRow(row);
      const hasMetadata =
        Boolean(finding.target_scope)
        || finding.target_deliverable_ids.length > 0
        || finding.evidence_refs.length > 0;
      if (!finding.text) {
        if (validate) {
          description?.setCustomValidity(
            hasMetadata
              ? "Describe the finding before attaching targets or evidence."
              : "Describe this finding or remove the empty row.",
          );
          valid = false;
        }
        return;
      }
      if (!finding.target_scope) {
        if (validate) {
          releaseTarget?.setCustomValidity(
            "Select the entire release candidate or at least one affected deliverable.",
          );
          valid = false;
        }
        return;
      }
      findings.push(finding);
    });
    if (validate && !rows.length) {
      peers.app.setStatus("Request changes requires at least one actionable finding.", "error");
      form.querySelector("[data-human-review-finding-add]")?.focus();
      return null;
    }
    if (validate && !valid) {
      form.reportValidity();
      peers.app.setStatus(
        "Complete or remove every added finding before requesting changes.",
        "error",
      );
      return null;
    }
    return findings;
  }

  function humanReviewFindingDraftFromRow(row) {
    const finding = humanReviewFindingFromRow(row);
    return {
      text: row.querySelector('[name="human_finding_text"]')?.value || "",
      target_scope: finding.target_scope,
      target_deliverable_ids: finding.target_deliverable_ids,
      evidence_refs:
        row.querySelector('[name="human_finding_evidence_refs"]')?.value || "",
    };
  }

  function humanReviewFindingsDraftFromForm(form) {
    const editor = form.querySelector("[data-human-review-findings-editor]");
    if (!editor) {
      return null;
    }
    return Array.from(editor.querySelectorAll("[data-human-review-finding-row]")).map(
      humanReviewFindingDraftFromRow,
    );
  }

  function restoreHumanReviewFindingsDraft(form, findings) {
    const editor = form.querySelector("[data-human-review-findings-editor]");
    const rows = editor?.querySelector?.("[data-human-review-finding-rows]");
    if (!rows || !Array.isArray(findings)) {
      return;
    }
    const targetOptions = humanReviewTargetOptionsFromEditor(editor);
    rows.innerHTML = findings
      .map((finding) => renderHumanReviewFindingRow(finding, targetOptions))
      .join("");
    peers.tooltips.decorateFieldTooltips(rows);
    syncHumanReviewFindingEditor(editor);
  }

  function renderHumanReviewDecisionForm(request, checklist, carriedFindings) {
    return `
      <form class="human-review-decision-form" data-human-review-decision-form data-form-id="human-review-decision-form" data-form-draft-scope="${escapeAttr(request.id)}" data-human-review-request-id="${escapeAttr(request.id)}" data-release-id="${escapeAttr(request.release_package_id)}" data-request-revision="${Number(request.revision || 0)}">
        <fieldset>
          <legend>Human acceptance checklist</legend>
          ${checklist.map((item) => `<label class="checkbox-label"><input type="checkbox" data-human-checklist-id="${escapeAttr(item.id)}"${item.required === false ? "" : " required"} /> ${escapeHtml(item.label)}${item.required === false ? " (optional)" : ""}</label>`).join("") || `<p class="empty-state">No checklist items.</p>`}
        </fieldset>
        ${
          carriedFindings.length
            ? `<fieldset><legend>Confirm carried findings</legend>${carriedFindings.map((finding) => `<label class="checkbox-label"><input type="checkbox" data-confirm-finding-id="${escapeAttr(finding.finding_id || finding.id)}" /> ${escapeHtml(finding.finding_id || finding.id)} - ${escapeHtml(finding.text || "")}</label>`).join("")}</fieldset>`
            : ""
        }
        <label>Decision summary<textarea name="summary" rows="3" placeholder="What was tested and why this decision is appropriate"></textarea></label>
        <label>Evidence references<textarea name="evidence_refs" rows="2" placeholder="artifact-space:..., one per line"></textarea></label>
        ${renderHumanReviewFindingsEditor(request.target_deliverable_options || [])}
        <div class="record-actions">
          <button type="submit" class="record-action human-review-approve" data-human-review-decision="approved">Approve</button>
          <button type="submit" class="record-action" data-human-review-decision="changes_requested" formnovalidate>Request changes</button>
          <button type="submit" class="record-action" data-human-review-decision="blocked" formnovalidate>Blocked</button>
        </div>
      </form>
    `;
  }

  function renderHumanReviewOwnerActions(request) {
    if (["approved", "changes_requested", "cancelled"].includes(text(request.state))) {
      return "";
    }
    return `
      <div class="record-actions human-review-owner-actions">
        ${request.state === "blocked" ? `<button type="button" class="record-action" data-human-review-resume-id="${escapeAttr(request.id)}">Resume unchanged candidate</button>` : ""}
        <button type="button" class="record-action" data-human-review-reassign-id="${escapeAttr(request.id)}">Reassign</button>
      </div>
    `;
  }

  function humanReviewRequestById(requestId) {
    const queued = humanReviewQueue.requests.find((request) => text(request.id) === text(requestId));
    if (queued) {
      return queued;
    }
    for (const cached of peers.releases.cachedReleaseDetails()) {
      const request = cached.release?.human_review?.requests?.find(
        (item) => text(item.id) === text(requestId),
      );
      if (request) {
        return request;
      }
    }
    return null;
  }

  async function submitHumanReviewDecision(form, submitter) {
    if (!peers.access.guardTeamMutation("record human review decisions")) {
      return;
    }
    const requestId = text(form.dataset.humanReviewRequestId);
    const releaseId = text(form.dataset.releaseId);
    const decision = text(submitter?.dataset?.humanReviewDecision);
    if (!requestId || !decision) {
      peers.app.setStatus("Human review decision context is incomplete.", "error");
      return;
    }
    const checklistResults = {};
    form.querySelectorAll("[data-human-checklist-id]").forEach((input) => {
      checklistResults[text(input.dataset.humanChecklistId)] = Boolean(input.checked);
    });
    const confirmedFindingIds = [...form.querySelectorAll("[data-confirm-finding-id]:checked")]
      .map((input) => text(input.dataset.confirmFindingId))
      .filter(Boolean);
    const data = formData(form);
    let findings = [];
    if (decision === "changes_requested") {
      findings = humanReviewFindingsFromForm(form, { validate: true });
      if (!findings) {
        return;
      }
    }
    const payload = {
      decision,
      request_id: humanReviewMutationRequestId(requestId, decision),
      expected_revision: Number(form.dataset.requestRevision || 0),
      checklist_results: checklistResults,
      confirmed_finding_ids: confirmedFindingIds,
      summary: text(data.summary),
      evidence_refs: splitList(data.evidence_refs),
    };
    if (decision === "changes_requested") {
      payload.findings = findings;
    }
    await mutateHumanReview(
      submitter,
      `/human-review-requests/${encodeURIComponent(requestId)}/decisions`,
      payload,
      { requestId, releaseId, successMessage: `Human review ${decision.replaceAll("_", " ")} recorded.` },
    );
  }

  async function resumeHumanReview(button, requestId) {
    const request = humanReviewRequestById(requestId);
    if (!request) {
      peers.app.setStatus("Human review request is no longer available.", "error");
      return;
    }
    await mutateHumanReview(
      button,
      `/human-review-requests/${encodeURIComponent(requestId)}/resume`,
      {
        request_id: humanReviewMutationRequestId(requestId, "resume"),
        expected_revision: Number(request.revision || 0),
      },
      {
        requestId,
        releaseId: request.release_package_id,
        successMessage: "Human review resumed against the unchanged candidate.",
      },
    );
  }

  async function reassignHumanReview(button, requestId, userIds) {
    const request = humanReviewRequestById(requestId);
    if (!request) {
      peers.app.setStatus("Human review request is no longer available.", "error");
      return;
    }
    if (!userIds.length) {
      peers.app.setStatus("Reassignment requires at least one Admin Web user ID.", "error");
      return;
    }
    await mutateHumanReview(
      button,
      `/human-review-requests/${encodeURIComponent(requestId)}/reassign`,
      {
        request_id: humanReviewMutationRequestId(requestId, "reassign"),
        expected_revision: Number(request.revision || 0),
        user_ids: userIds,
      },
      {
        requestId,
        releaseId: request.release_package_id,
        successMessage: "Human review reassigned.",
      },
    );
  }

  async function mutateHumanReview(control, endpoint, payload, { requestId, releaseId, successMessage }) {
    if (control) {
      control.disabled = true;
    }
    peers.app.setStatus("Saving human review action.", "loading");
    try {
      const response = await peers.access.teamFetch(`${API_BASE}${endpoint}`, {
        method: "POST",
        headers: { Accept: "application/json", "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!response.ok) {
        throw new Error(await responseText(response));
      }
      const result = await response.json();
      const decisionForm = control?.closest?.("[data-human-review-decision-form]");
      if (decisionForm) {
        peers.drafts.clearFormDraft(decisionForm);
        decisionForm.reset();
      }
      peers.app.state.revision = result.revision ?? peers.app.state.revision;
      const updatedRequest = result.human_review_request;
      if (updatedRequest) {
        humanReviewQueue.requests = humanReviewQueue.requests.map((request) =>
          text(request.id) === text(requestId) ? updatedRequest : request,
        );
      }
      peers.releases.invalidateReleaseDetail(releaseId);
      await Promise.all([
        peers.app.loadView("delivery", { force: true, preserveForms: false }),
        loadMyHumanReviews(),
      ]);
      if (peers.releases.releaseDetailIsExpanded(releaseId)) {
        await peers.releases.loadReleaseDetail(text(releaseId));
      }
      peers.app.setStatus(successMessage, "success");
    } catch (error) {
      peers.app.setStatus(error instanceof Error ? error.message : "Human review action failed.", "error");
    } finally {
      if (control) {
        control.disabled = false;
      }
    }
  }

  function humanReviewMutationRequestId(requestId, action) {
    return peers.mutations.teamUiMutationRequestId(requestId, action);
  }
  function handleFindingsClick(event) {
    const humanReviewApprove = event.target?.closest?.(
      '[data-human-review-decision="approved"]',
    );
    if (humanReviewApprove) {
      humanReviewApprove
        .closest("[data-human-review-decision-form]")
        ?.querySelectorAll?.(
          '[name="human_finding_text"], [name="human_finding_target_release_candidate"]',
        )
        .forEach((field) => field.setCustomValidity(""));
    }

    const findingAdd = event.target?.closest?.("[data-human-review-finding-add]");
    if (findingAdd) {
      event.preventDefault();
      if (!peers.access.guardTeamMutation("edit human review findings")) {
        return true;
      }
      addHumanReviewFindingRow(
        findingAdd.closest("[data-human-review-findings-editor]"),
      );
      return true;
    }

    const findingRemove = event.target?.closest?.("[data-human-review-finding-remove]");
    if (findingRemove) {
      event.preventDefault();
      if (!peers.access.guardTeamMutation("edit human review findings")) {
        return true;
      }
      removeHumanReviewFindingRow(findingRemove);
      return true;
    }
    return false;
  }
  function handleFindingInput(event) {
    const humanReviewFindingField = event.target?.closest?.(
      '[name="human_finding_text"], [name="human_finding_target_release_candidate"], [name="human_finding_target_deliverable_ids"], [name="human_finding_evidence_refs"]',
    );
    if (
      humanReviewFindingField instanceof HTMLTextAreaElement
      || humanReviewFindingField instanceof HTMLInputElement
    ) {
      const findingRow = humanReviewFindingField.closest(
        "[data-human-review-finding-row]",
      );
      const releaseTarget = findingRow?.querySelector?.(
        '[name="human_finding_target_release_candidate"]',
      );
      if (
        humanReviewFindingField.matches(
          '[name="human_finding_target_release_candidate"]',
        )
        && humanReviewFindingField.checked
      ) {
        findingRow
          ?.querySelectorAll?.(
            '[name="human_finding_target_deliverable_ids"]',
          )
          .forEach((field) => {
            field.checked = false;
          });
      } else if (
        humanReviewFindingField.matches(
          '[name="human_finding_target_deliverable_ids"]',
        )
        && humanReviewFindingField.checked
        && releaseTarget
      ) {
        releaseTarget.checked = false;
      }
      releaseTarget?.setCustomValidity("");
      findingRow
        ?.querySelector?.('[name="human_finding_text"]')
        ?.setCustomValidity("");
    }
    return false;
  }

  function toggleAssignedReviews() {
    myHumanReviewsOnly = !myHumanReviewsOnly;
    if (!myHumanReviewsOnly) {
      focusedHumanReviewRequestId = "";
    }
    peers.delivery.updateMyHumanReviewsFilterButton();
    if (myHumanReviewsOnly) {
      void loadMyHumanReviews();
      return;
    }
    peers.releases.renderReleases();
  }

  return {
    toggleAssignedReviews,
    get focusedHumanReviewRequestId() { return focusedHumanReviewRequestId; },
    handleFindingsClick,
    handleFindingInput,
    humanReviewFindingsDraftFromForm,
    get humanReviewQueue() { return humanReviewQueue; },
    loadMyHumanReviews,
    get myHumanReviewsOnly() { return myHumanReviewsOnly; },
    openHumanReviewDeepLink,
    reassignHumanReview,
    renderReleaseHumanRequests,
    restoreHumanReviewFindingsDraft,
    resumeHumanReview,
    submitHumanReviewDecision,
    syncHumanReviewFindingEditor,
  };
}

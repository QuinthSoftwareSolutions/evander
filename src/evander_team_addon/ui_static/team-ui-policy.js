// Release gate order, human review policy, and checklist editing.
import { escapeAttr, escapeHtml, splitList, text } from "./team-ui-format.js";
import { RELEASE_GATE_LABELS } from "./team-ui-config.js";

export function createTeamPolicyController(peers) {

  function releasePolicyForForm(data, form = null) {
    if (form?.elements?.release_policy_enabled && !form.elements.release_policy_enabled.checked) {
      return null;
    }
    const raw = text(data.release_policy_json);
    const gateSequence = releaseGateSequenceFromForm(form);
    if (
      !form?.elements?.release_policy_enabled
      && !raw
      && !form?.elements?.human_review_enabled?.checked
      && !gateSequence.some((stage) => stage.enabled)
    ) {
      return null;
    }
    const policy = raw ? JSON.parse(raw) : {};
    if (!policy || Array.isArray(policy) || typeof policy !== "object") {
      throw new Error("Release policy must be a JSON object.");
    }
    policy.schema_version = 3;
    policy.gate_sequence = gateSequence;
    policy.automatic_release_branch_publication = form?.elements
      ?.automatic_release_branch_publication
      ? Boolean(form.elements.automatic_release_branch_publication.checked)
      : policy.automatic_release_branch_publication !== false;
    if (form?.elements?.human_review_enabled) {
      delete policy.human_review_requirement;
      if (form.elements.human_review_enabled.checked) {
        const reviewerGroupIds = splitList(data.human_reviewer_group_ids);
        if (!reviewerGroupIds.length) {
          throw new Error("Human review requires at least one reviewer group ID.");
        }
        policy.human_review_requirement = {
          reviewer_group_ids: reviewerGroupIds,
          checklist: humanReviewChecklistFromForm(form),
          test_instructions: text(data.human_review_test_instructions),
          preparation_mode: text(data.human_review_preparation_mode) || "manual",
        };
      }
    }
    if (!Object.keys(policy).length) {
      return null;
    }
    return policy;
  }

  function releasePolicyWithoutHumanJson(policy) {
    if (!policy) {
      return "";
    }
    const bounded = { ...policy };
    delete bounded.human_review_requirement;
    delete bounded.gate_sequence;
    delete bounded.schema_version;
    delete bounded.automatic_release_branch_publication;
    return JSON.stringify(bounded, null, 2);
  }

  function normalizedReleaseGateSequence(policy = null) {
    const configured = Array.isArray(policy?.gate_sequence) ? policy.gate_sequence : null;
    if (configured?.length) {
      return configured.map((stage) => ({ kind: text(stage.kind), enabled: Boolean(stage.enabled) }));
    }
    if (policy) {
      return [
        { kind: "verification", enabled: Boolean(policy.verification_commands?.length) },
        { kind: "agent_review", enabled: Boolean(policy.review_requirements?.length) },
        { kind: "human_review", enabled: Boolean(policy.human_review_requirement) },
      ];
    }
    return [
      { kind: "agent_review", enabled: false },
      { kind: "verification", enabled: false },
      { kind: "human_review", enabled: false },
    ];
  }

  function renderReleaseGateSequenceEditor(policy = null) {
    const sequence = normalizedReleaseGateSequence(policy);
    const rows = sequence.map((stage, index) => `
      <div class="human-review-checklist-editor-row" data-release-gate-row data-release-gate-kind="${escapeAttr(stage.kind)}">
        ${peers.editor.checkboxField(RELEASE_GATE_LABELS[stage.kind] || stage.kind, `release_gate_enabled_${stage.kind}`, stage.enabled)}
        <div class="record-actions">
          <button type="button" data-release-gate-move="up" aria-label="Move ${escapeAttr(RELEASE_GATE_LABELS[stage.kind] || stage.kind)} earlier"${index === 0 ? " disabled" : ""}>Up</button>
          <button type="button" data-release-gate-move="down" aria-label="Move ${escapeAttr(RELEASE_GATE_LABELS[stage.kind] || stage.kind)} later"${index === sequence.length - 1 ? " disabled" : ""}>Down</button>
        </div>
      </div>
    `).join("");
    return `
      <fieldset data-release-gate-sequence>
        <legend>Gate order</legend>
        <div data-release-gate-rows>${rows}</div>
        <p class="field-help">Enabled gates run from top to bottom. Disabled gates keep their settings. If all gates are disabled, the release publishes without gates.</p>
      </fieldset>
    `;
  }

  function releaseGateSequenceFromForm(form) {
    const rows = Array.from(form?.querySelectorAll?.("[data-release-gate-row]") || []);
    if (!rows.length) {
      return normalizedReleaseGateSequence();
    }
    return rows.map((row) => {
      const kind = text(row.dataset.releaseGateKind);
      const control = row.querySelector(`[name="release_gate_enabled_${kind}"]`);
      return { kind, enabled: Boolean(control?.checked) };
    });
  }

  function refreshReleaseGateSequenceEditor(editor) {
    const rows = Array.from(editor?.querySelectorAll?.("[data-release-gate-row]") || []);
    rows.forEach((row, index) => {
      const up = row.querySelector('[data-release-gate-move="up"]');
      const down = row.querySelector('[data-release-gate-move="down"]');
      if (up) up.disabled = index === 0;
      if (down) down.disabled = index === rows.length - 1;
    });
  }

  function renderHumanReviewPolicyEditor(requirement = null) {
    return `
      <fieldset class="human-review-policy-editor">
        <legend>Human release review</legend>
        ${peers.editor.checkboxField("Require authenticated human approval", "human_review_enabled", Boolean(requirement))}
        ${peers.editor.textareaField("Reviewer group IDs", "human_reviewer_group_ids", (requirement?.reviewer_group_ids || []).join("\n"), {
          rows: 3,
          placeholder: "release-approvers",
        })}
        ${renderHumanReviewChecklistEditor(requirement?.checklist || [])}
        ${peers.editor.textareaField("Test instructions", "human_review_test_instructions", requirement?.test_instructions || "", {
          rows: 4,
          placeholder: "Open the preview, execute the release scenario, and attach evidence.",
        })}
        <label>Preparation mode
          <select name="human_review_preparation_mode">
            <option value="manual"${requirement?.preparation_mode !== "workflow" ? " selected" : ""}>Manual</option>
            <option value="workflow"${requirement?.preparation_mode === "workflow" ? " selected" : ""}>Workflow-prepared preview</option>
          </select>
        </label>
        <p class="field-help">Reviewer identity and eligible users are resolved by the Admin Web directory. This editor cannot set users, identity snapshots, quorums, or bypasses.</p>
      </fieldset>
    `;
  }

  function renderHumanReviewChecklistEditor(checklist = []) {
    return `
      <fieldset class="human-review-checklist-editor" data-human-review-checklist-editor>
        <legend>Acceptance checklist</legend>
        <div class="human-review-checklist-editor-rows" data-human-review-checklist-rows>
          ${(checklist || []).map((item) => renderHumanReviewChecklistRow(item)).join("")}
        </div>
        <p class="human-review-checklist-empty" data-human-review-checklist-empty>No acceptance checks configured.</p>
        <button
          class="record-action human-review-checklist-add"
          type="button"
          data-human-review-checklist-add
        >Add check</button>
        <p class="field-help">Each release snapshots these checks in this order. Stable keys are generated from labels unless you set one explicitly.</p>
      </fieldset>
    `;
  }

  function renderHumanReviewChecklistRow(item = {}) {
    return `
      <div class="human-review-checklist-editor-row" data-human-review-checklist-row>
        <label>
          <span>Label</span>
          <input
            name="human_review_checklist_label"
            value="${escapeAttr(item.label || "")}"
            placeholder="Primary operator flow works"
          />
        </label>
        <label class="human-review-checklist-description">
          <span>Description</span>
          <textarea
            name="human_review_checklist_description"
            rows="2"
            placeholder="Explain what the reviewer should verify."
          >${escapeHtml(item.description || "")}</textarea>
        </label>
        <label>
          <span>Stable key</span>
          <input
            name="human_review_checklist_id"
            value="${escapeAttr(item.id || "")}"
            placeholder="Auto from label"
          />
          <small data-human-review-checklist-key-preview></small>
        </label>
        <label class="checkbox-label human-review-checklist-required">
          <input
            name="human_review_checklist_required"
            type="checkbox"
            ${item.required === false ? "" : "checked"}
          />
          Required
        </label>
        <div class="human-review-checklist-row-actions" aria-label="Checklist order and removal">
          <button
            class="record-action"
            type="button"
            data-human-review-checklist-move="up"
            aria-label="Move checklist item up"
          >Move up</button>
          <button
            class="record-action"
            type="button"
            data-human-review-checklist-move="down"
            aria-label="Move checklist item down"
          >Move down</button>
          <button
            class="record-action human-review-checklist-remove"
            type="button"
            data-human-review-checklist-remove
          >Remove</button>
        </div>
      </div>
    `;
  }

  function syncHumanReviewChecklistEditor(editor) {
    if (!editor) {
      return;
    }
    const rows = Array.from(editor.querySelectorAll("[data-human-review-checklist-row]"));
    const mutationLocked = peers.access.teamMutationLocked();
    rows.forEach((row, index) => {
      const up = row.querySelector('[data-human-review-checklist-move="up"]');
      const down = row.querySelector('[data-human-review-checklist-move="down"]');
      if (up) {
        up.disabled = mutationLocked || index === 0;
        up.setAttribute("aria-disabled", String(up.disabled));
      }
      if (down) {
        down.disabled = mutationLocked || index === rows.length - 1;
        down.setAttribute("aria-disabled", String(down.disabled));
      }
      const explicitId = text(
        row.querySelector('[name="human_review_checklist_id"]')?.value,
      );
      const preview = row.querySelector("[data-human-review-checklist-key-preview]");
      if (preview) {
        preview.textContent = explicitId
          ? "Using the stored key."
          : `Generated key: ${humanReviewChecklistItemId(row, index)}`;
      }
    });
    const empty = editor.querySelector("[data-human-review-checklist-empty]");
    if (empty) {
      empty.hidden = rows.length > 0;
    }
  }

  function addHumanReviewChecklistRow(editor) {
    const rows = editor?.querySelector?.("[data-human-review-checklist-rows]");
    if (!rows) {
      return;
    }
    const wrapper = document.createElement("div");
    wrapper.innerHTML = renderHumanReviewChecklistRow({});
    const row = wrapper.firstElementChild;
    if (!row) {
      return;
    }
    rows.appendChild(row);
    peers.tooltips.decorateFieldTooltips(row);
    syncHumanReviewChecklistEditor(editor);
    peers.drafts.rememberFormDraft(editor.closest("form"));
    row.querySelector('[name="human_review_checklist_label"]')?.focus();
  }

  function removeHumanReviewChecklistRow(button) {
    const editor = button.closest("[data-human-review-checklist-editor]");
    button.closest("[data-human-review-checklist-row]")?.remove();
    syncHumanReviewChecklistEditor(editor);
    peers.drafts.rememberFormDraft(editor?.closest("form"));
  }

  function moveHumanReviewChecklistRow(button) {
    const editor = button.closest("[data-human-review-checklist-editor]");
    const row = button.closest("[data-human-review-checklist-row]");
    const direction = text(button.dataset.humanReviewChecklistMove);
    if (!editor || !row) {
      return;
    }
    if (direction === "up" && row.previousElementSibling) {
      row.parentElement.insertBefore(row, row.previousElementSibling);
    } else if (direction === "down" && row.nextElementSibling) {
      row.parentElement.insertBefore(row.nextElementSibling, row);
    }
    syncHumanReviewChecklistEditor(editor);
    peers.drafts.rememberFormDraft(editor.closest("form"));
    button.focus();
  }

  function humanReviewChecklistItemId(row, index) {
    const explicitId = text(
      row.querySelector('[name="human_review_checklist_id"]')?.value,
    );
    const label = text(
      row.querySelector('[name="human_review_checklist_label"]')?.value,
    );
    return explicitId || peers.reviews.criterionIdFromText(label || `check ${index + 1}`);
  }

  function humanReviewChecklistFromForm(form) {
    return Array.from(form.querySelectorAll("[data-human-review-checklist-row]")).map(
      (row, index) => ({
        id: humanReviewChecklistItemId(row, index),
        label: text(
          row.querySelector('[name="human_review_checklist_label"]')?.value,
        ),
        description:
          text(
            row.querySelector('[name="human_review_checklist_description"]')?.value,
          ) || null,
        required:
          row.querySelector('[name="human_review_checklist_required"]')?.checked !==
          false,
      }),
    );
  }

  function humanReviewChecklistDraftFromForm(form) {
    const editor = form.querySelector("[data-human-review-checklist-editor]");
    if (!editor) {
      return null;
    }
    return Array.from(editor.querySelectorAll("[data-human-review-checklist-row]")).map(
      (row) => ({
        id: text(row.querySelector('[name="human_review_checklist_id"]')?.value),
        label: text(
          row.querySelector('[name="human_review_checklist_label"]')?.value,
        ),
        description: text(
          row.querySelector('[name="human_review_checklist_description"]')?.value,
        ),
        required:
          row.querySelector('[name="human_review_checklist_required"]')?.checked !==
          false,
      }),
    );
  }

  function restoreHumanReviewChecklistDraft(form, checklist) {
    const editor = form.querySelector("[data-human-review-checklist-editor]");
    const rows = editor?.querySelector?.("[data-human-review-checklist-rows]");
    if (!rows || !Array.isArray(checklist)) {
      return;
    }
    rows.innerHTML = checklist
      .map((item) => renderHumanReviewChecklistRow(item))
      .join("");
    peers.tooltips.decorateFieldTooltips(rows);
    syncHumanReviewChecklistEditor(editor);
  }

  function validateHumanReviewPolicyEditor(form) {
    const enabled = Boolean(form.elements.human_review_enabled?.checked);
    const groupField = form.elements.human_reviewer_group_ids;
    groupField?.setCustomValidity("");
    const rows = Array.from(
      form.querySelectorAll("[data-human-review-checklist-row]"),
    );
    rows.forEach((row) => {
      row
        .querySelector('[name="human_review_checklist_id"]')
        ?.setCustomValidity("");
      row
        .querySelector('[name="human_review_checklist_label"]')
        ?.setCustomValidity("");
    });
    if (!enabled) {
      return true;
    }

    let valid = true;
    if (!splitList(groupField?.value).length) {
      groupField?.setCustomValidity(
        "Choose at least one Admin reviewer group for human approval.",
      );
      valid = false;
    }

    const usedIds = new Map();
    rows.forEach((row, index) => {
      const labelField = row.querySelector(
        '[name="human_review_checklist_label"]',
      );
      const idField = row.querySelector('[name="human_review_checklist_id"]');
      const label = text(labelField?.value);
      if (!label) {
        labelField?.setCustomValidity("Give this acceptance check a label.");
        valid = false;
      }
      const id = humanReviewChecklistItemId(row, index);
      const earlierIdField = usedIds.get(id);
      if (earlierIdField) {
        const message = `Checklist keys must be unique. "${id}" is used more than once.`;
        earlierIdField.setCustomValidity(message);
        idField?.setCustomValidity(message);
        valid = false;
      } else {
        usedIds.set(id, idField);
      }
    });
    if (!form.checkValidity()) {
      valid = false;
    }
    if (!valid) {
      form.reportValidity();
      peers.app.setStatus("Fix the highlighted human review requirement before saving.", "error");
    }
    return valid;
  }
  function handlePolicyEditorClick(event) {
    const checklistAdd = event.target?.closest?.("[data-human-review-checklist-add]");
    if (checklistAdd) {
      event.preventDefault();
      if (!peers.access.guardTeamMutation("edit human review checklist")) {
        return true;
      }
      addHumanReviewChecklistRow(
        checklistAdd.closest("[data-human-review-checklist-editor]"),
      );
      return true;
    }

    const checklistRemove = event.target?.closest?.("[data-human-review-checklist-remove]");
    if (checklistRemove) {
      event.preventDefault();
      if (!peers.access.guardTeamMutation("edit human review checklist")) {
        return true;
      }
      removeHumanReviewChecklistRow(checklistRemove);
      return true;
    }

    const checklistMove = event.target?.closest?.("[data-human-review-checklist-move]");
    if (checklistMove) {
      event.preventDefault();
      if (!peers.access.guardTeamMutation("reorder human review checklist")) {
        return true;
      }
      moveHumanReviewChecklistRow(checklistMove);
      return true;
    }
    const gateMove = event.target?.closest?.("[data-release-gate-move]");
    if (gateMove) {
      const row = gateMove.closest("[data-release-gate-row]");
      const direction = gateMove.dataset.releaseGateMove;
      if (row && direction === "up" && row.previousElementSibling) {
        row.parentElement.insertBefore(row, row.previousElementSibling);
      } else if (row && direction === "down" && row.nextElementSibling) {
        row.parentElement.insertBefore(row.nextElementSibling, row);
      }
      refreshReleaseGateSequenceEditor(gateMove.closest("[data-release-gate-sequence]"));
      return true;
    }
    return false;
  }
  function handleChecklistInput(event) {
    const checklistField = event.target?.closest?.(
      '[name="human_review_checklist_id"], [name="human_review_checklist_label"], [name="human_review_checklist_description"]',
    );
    if (
      checklistField instanceof HTMLInputElement ||
      checklistField instanceof HTMLTextAreaElement
    ) {
      const editor = checklistField.closest("[data-human-review-checklist-editor]");
      editor
        ?.querySelectorAll?.(
          '[name="human_review_checklist_id"], [name="human_review_checklist_label"]',
        )
        .forEach((field) => field.setCustomValidity(""));
      syncHumanReviewChecklistEditor(editor);
    }
    return false;
  }

  return {
    handlePolicyEditorClick,
    handleChecklistInput,
    humanReviewChecklistDraftFromForm,
    normalizedReleaseGateSequence,
    releasePolicyForForm,
    releasePolicyWithoutHumanJson,
    renderHumanReviewPolicyEditor,
    renderReleaseGateSequenceEditor,
    restoreHumanReviewChecklistDraft,
    syncHumanReviewChecklistEditor,
    validateHumanReviewPolicyEditor,
  };
}

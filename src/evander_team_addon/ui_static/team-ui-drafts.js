// Form drafts, focus restoration, and unavailable selection handling.
import {
  formData,
  formIdentifier,
  formSemanticIdentifier,
  splitList,
  text,
} from "./team-ui-format.js";
import { formDraftFocusKey } from "./team-ui-config.js";

export function createTeamDraftsController(peers) {

  const formDraftRegistry = new Map();

  let pendingFormFocusDraft = null;

  function formDraftKey(form) {
    if (!form || !shouldTrackFormDraft(form)) {
      return "";
    }
    const formId = formIdentifier(form);
    const semanticId = formSemanticIdentifier(form);
    const parts = [formId || semanticId || "form"];
    if (semanticId && semanticId !== formId) {
      parts.push(`semantic:${semanticId}`);
    }
    const draftScope = text(form.dataset.formDraftScope);
    if (draftScope) {
      parts.push(`scope:${draftScope}`);
    }
    const editType = text(form.dataset.editType);
    const editId = text(form.dataset.editId);
    if (editType || editId) {
      parts.push(`edit:${editType || "record"}:${editId || "new"}`);
    }
    const groupId = text(form.dataset.teamRecordGroupId);
    const groupKind = text(form.dataset.teamRecordKind);
    if (groupId || groupKind) {
      parts.push(`group:${groupKind || "record"}:${groupId || "all"}`);
    }
    const reviewRequestId = text(form.dataset.reviewRequestId);
    if (reviewRequestId) {
      parts.push(`review:${reviewRequestId}`);
    }
    return parts.join("|");
  }

  function shouldTrackFormDraft(form) {
    return Boolean(
      form
      && (formIdentifier(form) || formSemanticIdentifier(form))
      && !form.matches("[data-chat-message-form]"),
    );
  }

  function rememberFormDraftFromEvent(event) {
    const form = event.target?.closest?.("form");
    if (form) {
      rememberFormDraft(form);
    }
  }

  function rememberFormDraft(form) {
    const key = formDraftKey(form);
    if (!key) {
      return null;
    }
    const values = formValuesForDraft(form);
    formDraftRegistry.set(key, values);
    return [key, values];
  }

  function clearFormDraft(form) {
    const key = formDraftKey(form);
    if (key) {
      formDraftRegistry.delete(key);
      if (pendingFormFocusDraft?.formKey === key) {
        pendingFormFocusDraft = null;
      }
    }
  }

  function formValuesForDraft(form) {
    const values = formData(form);
    form.querySelectorAll('input[type="file"][name]').forEach((field) => {
      delete values[field.name];
    });
    form.querySelectorAll('input[type="checkbox"][name]').forEach((field) => {
      values[field.name] = field.checked ? "true" : "false";
    });
    const criteriaDraft = peers.reviews.reviewProfileCriteriaDraftFromForm(form);
    if (criteriaDraft) {
      values.__review_profile_criteria = criteriaDraft;
    }
    if (form.querySelector("[data-governance-goals-editor]")) {
      values.__governance_goals = peers.governance.governanceGoalsDraftFromForm(form);
    }
    const humanReviewChecklistDraft = peers.policy.humanReviewChecklistDraftFromForm(form);
    if (humanReviewChecklistDraft) {
      values.__human_review_checklist = humanReviewChecklistDraft;
    }
    if (form.matches("[data-human-review-decision-form]")) {
      values.__human_review_findings = peers.humanReviews.humanReviewFindingsDraftFromForm(form) || [];
      values.__human_review_checklist_results = Object.fromEntries(
        Array.from(form.querySelectorAll("[data-human-checklist-id]")).map((field) => [
          text(field.dataset.humanChecklistId),
          Boolean(field.checked),
        ]),
      );
      values.__human_review_confirmed_finding_ids = Array.from(
        form.querySelectorAll("[data-confirm-finding-id]:checked"),
      )
        .map((field) => text(field.dataset.confirmFindingId))
        .filter(Boolean);
    }
    return values;
  }

  function findFormByDraftKey(key) {
    return Array.from(document.querySelectorAll("form")).find((form) => formDraftKey(form) === key) || null;
  }

  function captureFormDrafts() {
    const drafts = Object.fromEntries(formDraftRegistry.entries());
    document.querySelectorAll("form").forEach((form) => {
      if (form.dataset.formBusy === "true") {
        return;
      }
      const key = formDraftKey(form);
      if (!key || !formDraftRegistry.has(key)) {
        return;
      }
      const remembered = rememberFormDraft(form);
      if (remembered) {
        drafts[remembered[0]] = remembered[1];
      }
    });
    const focusDraft = captureFormFocusDraft() || pendingFormFocusDraft;
    if (focusDraft) {
      drafts[formDraftFocusKey] = focusDraft;
    }
    return drafts;
  }

  function restoreFormDrafts(drafts) {
    const focusDraft = drafts?.[formDraftFocusKey] || null;
    Object.entries(drafts || {}).forEach(([formId, values]) => {
      if (formId === formDraftFocusKey) {
        return;
      }
      const form = findFormByDraftKey(formId) || document.getElementById(formId);
      if (!form) {
        return;
      }
      peers.reviews.restoreReviewProfileCriteriaDraft(form, values.__review_profile_criteria);
      peers.governance.restoreGovernanceGoalsDraft(form, values.__governance_goals);
      peers.policy.restoreHumanReviewChecklistDraft(form, values.__human_review_checklist);
      if (form.matches("[data-human-review-decision-form]")) {
        peers.humanReviews.restoreHumanReviewFindingsDraft(form, values.__human_review_findings);
        const checklistResults = values.__human_review_checklist_results || {};
        form.querySelectorAll("[data-human-checklist-id]").forEach((field) => {
          field.checked = Boolean(checklistResults[text(field.dataset.humanChecklistId)]);
        });
        const confirmedFindingIds = new Set(
          values.__human_review_confirmed_finding_ids || [],
        );
        form.querySelectorAll("[data-confirm-finding-id]").forEach((field) => {
          field.checked = confirmedFindingIds.has(text(field.dataset.confirmFindingId));
        });
      }
      if (formSemanticIdentifier(form) === "setup-template-form") {
        const templateField = form.elements.template_id;
        if (templateField && values.template_id) {
          templateField.value = text(values.template_id);
        }
        peers.templates.renderTemplateSetupFields({ preserveValues: false });
      }
      Object.entries(values).forEach(([name, value]) => {
        if (
          name === "__review_profile_criteria" ||
          name === "__governance_goals" ||
          name === "__human_review_checklist" ||
          name === "__human_review_findings" ||
          name === "__human_review_checklist_results" ||
          name === "__human_review_confirmed_finding_ids" ||
          name.startsWith("criterion_") ||
          name.startsWith("governance_goal_") ||
          name.startsWith("human_review_checklist_") ||
          name.startsWith("human_finding_")
        ) {
          return;
        }
        const field = form.elements[name];
        if (!field) {
          return;
        }
        if (field instanceof HTMLInputElement && field.type === "file") {
          return;
        }
        if (field instanceof HTMLInputElement && field.type === "checkbox") {
          field.checked = value === true || value === "true" || value === "on";
          return;
        }
        if (field instanceof HTMLSelectElement && field.multiple) {
          const selected = new Set(Array.isArray(value) ? value.map(text) : splitList(value));
          const available = new Set(Array.from(field.options).map((option) => option.value));
          const missing = Array.from(selected).filter((item) => item && !available.has(item));
          Array.from(field.options).forEach((option) => {
            option.selected = selected.has(option.value);
          });
          markUnavailableDraftSelections(field, missing);
          return;
        }
        if (field instanceof HTMLSelectElement) {
          const normalizedValue = text(value);
          const available = new Set(Array.from(field.options).map((option) => option.value));
          field.value = normalizedValue;
          markUnavailableDraftSelections(
            field,
            normalizedValue && !available.has(normalizedValue) ? [normalizedValue] : [],
          );
          return;
        }
        field.value = value;
      });
      const leadSelect = form.elements.lead_agent_id;
      if (leadSelect?.matches?.('select[data-options="project-lead-agents"]')) {
        leadSelect.dataset.currentValue = text(values.lead_agent_id);
        peers.selects.refreshProjectLeadSelect(form);
      }
      peers.governance.syncGovernanceMemberTeamEditor(
        form.querySelector("[data-governance-member-teams-editor]"),
      );
      peers.governance.syncGovernanceGoalEditor(
        form.querySelector("[data-governance-goals-editor]"),
      );
      peers.policy.syncHumanReviewChecklistEditor(
        form.querySelector("[data-human-review-checklist-editor]"),
      );
      peers.humanReviews.syncHumanReviewFindingEditor(
        form.querySelector("[data-human-review-findings-editor]"),
      );
      rememberFormDraft(form);
    });
    const updateTaskForm = document.querySelector("#update-task-form");
    if (updateTaskForm) {
      peers.editor.syncTaskUpdateForm(updateTaskForm, { keepEditedValues: true });
    }
    peers.templates.renderTemplatePreview();
    restoreFormFocusDraft(focusDraft);
  }

  function captureFormFocusDraft() {
    const field = document.activeElement;
    if (
      !(
        field instanceof HTMLInputElement
        || field instanceof HTMLTextAreaElement
        || field instanceof HTMLSelectElement
      )
    ) {
      return null;
    }
    const form = field.form;
    const formKey = formDraftKey(form);
    if (!formKey) {
      return null;
    }
    const identity = {
      id: text(field.id),
      name: text(field.name),
      nameIndex: -1,
      humanChecklistId: text(field.dataset.humanChecklistId),
      confirmFindingId: text(field.dataset.confirmFindingId),
    };
    if (identity.name) {
      identity.nameIndex = Array.from(form.elements)
        .filter((candidate) => text(candidate.name) === identity.name)
        .indexOf(field);
    }
    if (
      ![
        identity.id,
        identity.name,
        identity.humanChecklistId,
        identity.confirmFindingId,
      ].some(Boolean)
    ) {
      return null;
    }
    let selectionStart = null;
    let selectionEnd = null;
    let selectionDirection = null;
    if (field instanceof HTMLInputElement || field instanceof HTMLTextAreaElement) {
      try {
        selectionStart = field.selectionStart;
        selectionEnd = field.selectionEnd;
        selectionDirection = field.selectionDirection;
      } catch {
        // Some input types do not expose text selection.
      }
    }
    return {
      formKey,
      identity,
      selectionStart,
      selectionEnd,
      selectionDirection,
    };
  }

  function restoreFormFocusDraft(focusDraft) {
    if (!focusDraft?.formKey || !focusDraft.identity) {
      return;
    }
    pendingFormFocusDraft = focusDraft;
    const form = findFormByDraftKey(focusDraft.formKey);
    if (!form) {
      return;
    }
    const identity = focusDraft.identity;
    const fields = Array.from(form.elements).filter(
      (candidate) =>
        candidate instanceof HTMLInputElement
        || candidate instanceof HTMLTextAreaElement
        || candidate instanceof HTMLSelectElement,
    );
    let field = identity.id
      ? fields.find((candidate) => text(candidate.id) === identity.id)
      : null;
    if (!field && identity.humanChecklistId) {
      field = fields.find(
        (candidate) =>
          text(candidate.dataset.humanChecklistId) === identity.humanChecklistId,
      );
    }
    if (!field && identity.confirmFindingId) {
      field = fields.find(
        (candidate) =>
          text(candidate.dataset.confirmFindingId) === identity.confirmFindingId,
      );
    }
    if (!field && identity.name) {
      const namedFields = fields.filter(
        (candidate) => text(candidate.name) === identity.name,
      );
      field = namedFields[Math.max(0, Number(identity.nameIndex) || 0)] || null;
    }
    if (!field || field.disabled) {
      return;
    }
    pendingFormFocusDraft = null;
    field.focus({ preventScroll: true });
    if (
      (field instanceof HTMLInputElement || field instanceof HTMLTextAreaElement)
      && typeof focusDraft.selectionStart === "number"
      && typeof focusDraft.selectionEnd === "number"
    ) {
      const start = Math.min(Math.max(focusDraft.selectionStart, 0), field.value.length);
      const end = Math.min(
        Math.max(focusDraft.selectionEnd, start),
        field.value.length,
      );
      try {
        field.setSelectionRange(
          start,
          end,
          focusDraft.selectionDirection || undefined,
        );
      } catch {
        // Ignore selection restoration for unsupported input types.
      }
    }
  }

  function markUnavailableDraftSelections(field, missingValues) {
    const missing = missingValues.filter(Boolean);
    if (!missing.length) {
      delete field.dataset.unavailableDraftValues;
      field.removeAttribute("title");
      return;
    }
    field.dataset.unavailableDraftValues = missing.join(",");
    field.title = `Draft kept unavailable selection: ${missing.join(", ")}`;
  }

  function setFormBusy(form, busy) {
    if (busy) {
      form.dataset.formBusy = "true";
    } else {
      delete form.dataset.formBusy;
    }
    form.querySelectorAll("button, input, select, textarea").forEach((field) => {
      field.disabled = busy;
    });
    if (!busy) {
      peers.access.applyLockedMutationControls(form);
    }
  }
  function bindDraftInput() {

    document.addEventListener("input", rememberFormDraftFromEvent);
  }
  function bindDraftChange() {

    document.addEventListener("change", rememberFormDraftFromEvent);
  }

  return {
    bindDraftChange,
    bindDraftInput,
    captureFormDrafts,
    clearFormDraft,
    findFormByDraftKey,
    formDraftKey,
    get formDraftRegistry() { return formDraftRegistry; },
    rememberFormDraft,
    restoreFormDrafts,
    setFormBusy,
  };
}

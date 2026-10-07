// Revision and resource guards, mutation results, and conflict retry.
import { API_BASE } from "./team-ui-config.js";
import { formIdentifier, responseText, text } from "./team-ui-format.js";
import { normalizedTeamView } from "./team-ui-state.js";
import { elements } from "./team-ui-dom.js";

export function createTeamMutationsController(peers) {

  let pendingConflictRetry = null;

  function teamUiMutationRequestId(scopeId, action) {
    const randomPart =
      typeof globalThis.crypto?.randomUUID === "function"
        ? globalThis.crypto.randomUUID()
        : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
    return `team-ui:${scopeId}:${action}:${randomPart}`;
  }

  async function submitMutation(form, endpoint, method, payload, { resetOnSuccess = true, detailedResult = false } = {}) {
    if (!peers.access.guardTeamMutation("save changes")) {
      return submitMutationResult(false, "locked", detailedResult);
    }
    peers.drafts.rememberFormDraft(form);
    const drafts = peers.drafts.captureFormDrafts();
    const body = mutationBodyWithConflictMetadata(endpoint, method, payload);
    const retryDraftKey = peers.drafts.formDraftKey(form);
    peers.drafts.setFormBusy(form, true);
    peers.app.setStatus("Saving.", "loading");
    try {
      const response = await peers.access.teamFetch(`${API_BASE}${endpoint}`, {
        method,
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
          "X-Team-Addon-Response-Scope": "view",
        },
        body: JSON.stringify(body),
      });

      if (response.status === 409) {
        const conflict = await response.json().catch(() => ({}));
        await peers.app.reloadActiveTeamView({ preserveForms: true });
        peers.drafts.restoreFormDrafts(drafts);
        const current = conflict.current_revision ?? peers.app.state.revision;
        showRevisionConflictRecoveryStatus({
          form,
          draftKey: retryDraftKey,
          endpoint,
          method,
          payload,
          options: { resetOnSuccess },
          currentRevision: current,
        });
        return submitMutationResult(false, "conflict", detailedResult);
      }

      if (!response.ok) {
        throw new Error(await responseText(response));
      }

      const result = await response.json();
      const rebasedFromRevision = result.rebased_from_revision;
      const scoped = isScopedMutationResponse(result);
      if (scoped) {
        applyScopedMutationResponse(result);
      } else {
        peers.app.replaceState(result);
        peers.app.resetLoadedViewsToActive();
      }
      if (resetOnSuccess) {
        peers.drafts.clearFormDraft(form);
        form.reset();
      }
      if (scoped) {
        await refreshInvalidatedViews(result.invalidated_views || [peers.app.activeTeamView]);
      } else {
        peers.app.render();
        void peers.timeline.loadTimeline();
      }
      if (rebasedFromRevision !== undefined && rebasedFromRevision !== null) {
        peers.app.setStatus(
          `Saved revision ${peers.app.state.revision} after reloading from revision ${rebasedFromRevision}.`,
          "ok",
        );
      } else {
        peers.app.setStatus(`Saved revision ${peers.app.state.revision}.`, "ok");
      }
      return submitMutationResult(true, "", detailedResult);
    } catch (error) {
      peers.app.setStatus(error instanceof Error ? error.message : "Save failed.", "error");
      return submitMutationResult(false, "error", detailedResult);
    } finally {
      peers.drafts.setFormBusy(form, false);
    }
  }

  function submitMutationResult(saved, failureReason, detailedResult) {
    const result = {
      saved: Boolean(saved),
      failureReason: saved ? "" : text(failureReason),
    };
    return detailedResult ? result : result.saved;
  }

  function mutationResultSaved(result) {
    return typeof result === "object" && result !== null ? Boolean(result.saved) : Boolean(result);
  }

  function isScopedMutationResponse(result) {
    return result?.response_scope === "view" || Array.isArray(result?.invalidated_views);
  }

  function applyScopedMutationResponse(result) {
    peers.app.replaceState({
      ...peers.app.state,
      revision: result.revision ?? peers.app.state.revision,
      resource_tokens: peers.app.mergeResourceTokens(peers.app.state.resource_tokens, result.resource_tokens || {}),
    });
    (result.invalidated_views || []).forEach((view) => {
      peers.app.forgetLoadedView(view);
    });
  }

  async function refreshInvalidatedViews(invalidatedViews) {
    const normalizedInvalidated = new Set(
      (invalidatedViews || []).map(normalizedTeamView),
    );
    if (!normalizedInvalidated.size || normalizedInvalidated.has(peers.app.activeTeamView)) {
      await peers.app.loadView(peers.app.activeTeamView, { force: true });
      return;
    }
    peers.app.render();
  }

  async function applyMutationResultPayload(result) {
    if (isScopedMutationResponse(result)) {
      applyScopedMutationResponse(result);
      await refreshInvalidatedViews(result.invalidated_views || [peers.app.activeTeamView]);
      return;
    }
    peers.app.replaceState(result);
    peers.app.resetLoadedViewsToActive();
    peers.app.render();
    void peers.timeline.loadTimeline();
  }

  function mutationBodyWithConflictMetadata(endpoint, method, payload) {
    const body = { expected_revision: peers.app.state.revision, ...payload };
    const context = mutationResourceContext(endpoint, method, payload);
    if (!context) {
      return body;
    }
    if (context.kind === "create") {
      body.conflict_policy = "target_unchanged";
      return body;
    }
    const token = resourceToken(context.collection, context.id);
    if (token) {
      body.conflict_policy = "target_unchanged";
      body.base_resource_token = token;
    }
    return body;
  }

  function mutationResourceContext(endpoint, method, payload = {}) {
    const normalizedMethod = text(method).toUpperCase();
    const path = text(endpoint).split("?")[0];
    const directTargets = [
      [/^\/teams\/([^/]+)$/, "teams"],
      [/^\/governance-groups\/([^/]+)$/, "governance_groups"],
      [/^\/governance-agenda\/([^/]+)\/handle$/, "governance_agenda"],
      [/^\/projects\/([^/]+)$/, "projects"],
      [/^\/projects\/([^/]+)\/lead$/, "projects"],
      [/^\/roles\/([^/]+)$/, "roles"],
      [/^\/agents\/([^/]+)$/, "agents"],
      [/^\/agents\/([^/]+)\/context-rotation$/, "agents"],
      [/^\/agents\/([^/]+)\/stand-down$/, "agents"],
      [/^\/agents\/([^/]+)\/stand-down\/revoke$/, "agents"],
      [/^\/agents\/([^/]+)\/avatar(?:\/accept-preview)?$/, "agents"],
      [/^\/cases\/([^/]+)$/, "cases"],
      [/^\/tasks\/([^/]+)$/, "tasks"],
      [/^\/review-requests\/([^/]+)\/decision$/, "review_requests"],
      [/^\/action-records\/([^/]+)\/dismiss$/, "action_records"],
      [/^\/review-profiles\/([^/]+)$/, "review_profiles"],
      [/^\/outcome-kinds\/([^/]+)$/, "outcome_kind_definitions"],
    ];
    for (const [pattern, collection] of directTargets) {
      const match = path.match(pattern);
      if (match) {
        return { kind: "target", collection, id: decodeURIComponent(match[1]) };
      }
    }
    if (normalizedMethod !== "POST") {
      return null;
    }
    const createCollections = {
      "/teams": ["teams", payload.id || payload.team_id],
      "/governance-groups": ["governance_groups", payload.id || payload.governance_group_id],
      "/governance-agenda": ["governance_agenda", payload.id || payload.agenda_item_id],
      "/projects": ["projects", payload.id || payload.project_id],
      "/roles": ["roles", payload.id || payload.role_id],
      "/agents": ["agents", payload.id || payload.agent_id],
      "/cases": ["cases", payload.id || payload.case_id],
      "/tasks": ["tasks", payload.id || payload.task_id],
      "/deliverables": ["deliverables", payload.id || payload.deliverable_id],
      "/review-requests": ["review_requests", payload.id || payload.review_request_id],
      "/review-profiles": ["review_profiles", payload.id || payload.review_profile_id],
      "/outcome-kinds": ["outcome_kind_definitions", payload.id || payload.definition_id],
    };
    const create = createCollections[path];
    if (!create) {
      return null;
    }
    return { kind: "create", collection: create[0], id: text(create[1]) };
  }

  function resourceToken(collection, id) {
    const normalizedCollection = text(collection);
    const normalizedId = text(id);
    if (!normalizedCollection || !normalizedId) {
      return "";
    }
    return text(
      peers.app.state.resource_tokens?.[normalizedCollection]?.[normalizedId]
        || peers.app.state[normalizedCollection]?.[normalizedId]?.resource_token,
    );
  }

  function resourceExists(collection, id) {
    const normalizedCollection = text(collection);
    const normalizedId = text(id);
    return Boolean(normalizedCollection && normalizedId && peers.app.state[normalizedCollection]?.[normalizedId]);
  }

  function showRevisionConflictRecoveryStatus({
    form,
    draftKey,
    endpoint,
    method,
    payload,
    options,
    currentRevision,
  }) {
    const context = mutationResourceContext(endpoint, method, payload);
    pendingConflictRetry = {
      draftKey,
      endpoint,
      method,
      options,
      mode: retryModeForForm(form),
      editType: text(form?.dataset?.editType),
      editId: text(form?.dataset?.editId),
    };
    if (context?.kind === "target" && !resourceExists(context.collection, context.id)) {
      pendingConflictRetry = null;
      peers.app.setStatus(
        `Revision conflict. Draft preserved, but the target record is no longer available at revision ${currentRevision}.`,
        "warning",
      );
      return;
    }
    peers.app.setStatus(
      `Revision conflict. Draft preserved and revision ${currentRevision} loaded; review and retry save.`,
      "warning",
      { label: "Retry save", onClick: () => void retryPendingConflictSave() },
    );
  }

  function retryModeForForm(form) {
    if (form === elements.editForm || text(form?.dataset?.editType)) {
      return "edit";
    }
    if (form?.hasAttribute?.("data-org-action-form")) {
      return "org-action";
    }
    if (form?.hasAttribute?.("data-team-copy-form")) {
      return "team-copy";
    }
    const formId = formIdentifier(form);
    if (formId === "update-task-form") {
      return "task-update";
    }
    if (formId === "decide-review-form" || form?.hasAttribute?.("data-review-decision-form")) {
      return "review-decision";
    }
    return "create";
  }

  async function retryPendingConflictSave() {
    if (!peers.access.guardTeamMutation("retry saves")) {
      return;
    }
    const retry = pendingConflictRetry;
    if (!retry) {
      peers.app.setStatus("No preserved save is available to retry.", "error");
      return;
    }
    let form = peers.drafts.findFormByDraftKey(retry.draftKey);
    if (!form && retry.mode === "edit" && retry.editType && retry.editId) {
      peers.editor.openEditDialog(retry.editType, retry.editId);
      form = peers.drafts.findFormByDraftKey(retry.draftKey) || elements.editForm;
      const draft = peers.drafts.formDraftRegistry.get(retry.draftKey);
      if (draft) {
        peers.drafts.restoreFormDrafts({ [retry.draftKey]: draft });
      }
    }
    if (!form) {
      peers.app.setStatus("The preserved draft form is no longer open.", "error");
      return;
    }
    pendingConflictRetry = null;
    if (retry.mode === "edit") {
      await peers.editor.submitEditForm(form);
    } else if (retry.mode === "task-update") {
      await peers.tasks.submitTaskUpdate(form);
    } else if (retry.mode === "review-decision") {
      await peers.reviews.submitReviewDecision(form);
    } else if (retry.mode === "org-action") {
      await peers.org.submitOrgActionForm(form);
    } else if (retry.mode === "team-copy") {
      await peers.copy.submitTeamCopyForm(form);
    } else {
      await peers.forms.submitCreateForm(form);
    }
  }

  function clearConflictRetry() {
    pendingConflictRetry = null;
  }

  return {
    clearConflictRetry,
    applyMutationResultPayload,
    mutationBodyWithConflictMetadata,
    mutationResultSaved,
    submitMutation,
    submitMutationResult,
    teamUiMutationRequestId,
  };
}

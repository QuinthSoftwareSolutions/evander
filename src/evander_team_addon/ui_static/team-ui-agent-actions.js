// Context rotation, stand-down, and failed action dismissal.
import { API_BASE } from "./team-ui-config.js";
import { responseText, text } from "./team-ui-format.js";

export function createTeamAgentActionsController(peers) {

  async function requestContextRotation(button, agentId) {
    if (!peers.access.guardTeamMutation("clear agent context")) {
      return;
    }
    const normalizedAgentId = text(agentId);
    if (!normalizedAgentId) {
      peers.app.setStatus("No agent selected.", "error");
      return;
    }
    const drafts = peers.drafts.captureFormDrafts();
    button.disabled = true;
    peers.app.setStatus("Requesting context clearing.", "loading");
    try {
      const endpoint = `/agents/${encodeURIComponent(normalizedAgentId)}/context-rotation`;
      const response = await peers.access.teamFetch(
        `${API_BASE}${endpoint}`,
        {
          method: "POST",
          headers: {
            Accept: "application/json",
            "Content-Type": "application/json",
            "X-Team-Addon-Response-Scope": "view",
          },
          body: JSON.stringify(peers.mutations.mutationBodyWithConflictMetadata(endpoint, "POST", {
            reason: "manual operator request",
          })),
        },
      );

      if (response.status === 409) {
        const conflict = await response.json().catch(() => ({}));
        await peers.app.reloadActiveTeamView({ preserveForms: true });
        peers.drafts.restoreFormDrafts(drafts);
        const current = conflict.current_revision ?? peers.app.state.revision;
        peers.app.setStatus(`Revision conflict. Reloaded revision ${current}; request context clearing again.`, "warning");
        return;
      }

      if (!response.ok) {
        throw new Error(await responseText(response));
      }

      await peers.mutations.applyMutationResultPayload(await response.json());
      peers.drafts.restoreFormDrafts(drafts);
      peers.app.setStatus(`Context clearing queued for ${normalizedAgentId}.`, "ok");
    } catch (error) {
      peers.app.setStatus(error instanceof Error ? error.message : "Context clearing request failed.", "error");
    } finally {
      if (button.isConnected) {
        button.disabled = false;
      }
    }
  }

  async function requestAgentStandDown(button, agentId, requestedScope) {
    if (!peers.access.guardTeamMutation("stand down agents")) {
      return;
    }
    const normalizedAgentId = text(agentId);
    if (!normalizedAgentId) {
      peers.app.setStatus("No agent selected.", "error");
      return;
    }
    const scope = text(requestedScope).toLowerCase();
    if (!["agent", "subtree"].includes(scope)) {
      peers.app.setStatus("Stand-down scope must be agent or subtree.", "error");
      return;
    }
    if (
      scope === "subtree"
      && !window.confirm(
        `Stand down ${normalizedAgentId} and every subordinate in that agent's subtree?`,
      )
    ) {
      return;
    }
    const reason = text(window.prompt(`Reason to stand down ${normalizedAgentId}`));
    if (!reason) {
      peers.app.setStatus("Stand-down reason is required.", "error");
      return;
    }
    const drafts = peers.drafts.captureFormDrafts();
    button.disabled = true;
    peers.app.setStatus(scope === "agent" ? "Standing down agent." : "Standing down agent subtree.", "loading");
    try {
      const endpoint = `/agents/${encodeURIComponent(normalizedAgentId)}/stand-down`;
      const response = await peers.access.teamFetch(
        `${API_BASE}${endpoint}`,
        {
          method: "POST",
          headers: {
            Accept: "application/json",
            "Content-Type": "application/json",
            "X-Team-Addon-Response-Scope": "view",
          },
          body: JSON.stringify(peers.mutations.mutationBodyWithConflictMetadata(endpoint, "POST", {
            reason,
            scope,
          })),
        },
      );

      if (response.status === 409) {
        const conflict = await response.json().catch(() => ({}));
        await peers.app.reloadActiveTeamView({ preserveForms: true });
        peers.drafts.restoreFormDrafts(drafts);
        const current = conflict.current_revision ?? peers.app.state.revision;
        peers.app.setStatus(`Revision conflict. Reloaded revision ${current}; stand down again.`, "warning");
        return;
      }

      if (!response.ok) {
        throw new Error(await responseText(response));
      }

      await peers.mutations.applyMutationResultPayload(await response.json());
      peers.drafts.restoreFormDrafts(drafts);
      peers.app.setStatus(`Stood down ${normalizedAgentId}.`, "ok");
    } catch (error) {
      peers.app.setStatus(error instanceof Error ? error.message : "Stand-down request failed.", "error");
    } finally {
      if (button.isConnected) {
        button.disabled = false;
      }
    }
  }

  async function revokeAgentStandDown(button, agentId) {
    if (!peers.access.guardTeamMutation("revoke stand-downs")) {
      return;
    }
    const normalizedAgentId = text(agentId);
    if (!normalizedAgentId) {
      peers.app.setStatus("No agent selected.", "error");
      return;
    }
    const drafts = peers.drafts.captureFormDrafts();
    button.disabled = true;
    peers.app.setStatus("Revoking stand-down.", "loading");
    try {
      const endpoint = `/agents/${encodeURIComponent(normalizedAgentId)}/stand-down/revoke`;
      const response = await peers.access.teamFetch(
        `${API_BASE}${endpoint}`,
        {
          method: "POST",
          headers: {
            Accept: "application/json",
            "Content-Type": "application/json",
            "X-Team-Addon-Response-Scope": "view",
          },
          body: JSON.stringify(peers.mutations.mutationBodyWithConflictMetadata(endpoint, "POST", {})),
        },
      );

      if (response.status === 409) {
        const conflict = await response.json().catch(() => ({}));
        await peers.app.reloadActiveTeamView({ preserveForms: true });
        peers.drafts.restoreFormDrafts(drafts);
        const current = conflict.current_revision ?? peers.app.state.revision;
        peers.app.setStatus(`Revision conflict. Reloaded revision ${current}; revoke again.`, "warning");
        return;
      }

      if (!response.ok) {
        throw new Error(await responseText(response));
      }

      await peers.mutations.applyMutationResultPayload(await response.json());
      peers.drafts.restoreFormDrafts(drafts);
      peers.app.setStatus(`Revoked stand-down for ${normalizedAgentId}.`, "ok");
    } catch (error) {
      peers.app.setStatus(error instanceof Error ? error.message : "Stand-down revoke failed.", "error");
    } finally {
      if (button.isConnected) {
        button.disabled = false;
      }
    }
  }

  async function dismissActionRecordAttention(button, actionRecordId) {
    if (!peers.access.guardTeamMutation("dismiss failed actions")) {
      return;
    }
    if (!actionRecordId) {
      peers.app.setStatus("No action record selected.", "error");
      return;
    }
    const drafts = peers.drafts.captureFormDrafts();
    button.disabled = true;
    peers.app.setStatus("Dismissing failed action.", "loading");
    try {
      const endpoint = `/action-records/${encodeURIComponent(actionRecordId)}/dismiss`;
      const response = await peers.access.teamFetch(
        `${API_BASE}${endpoint}`,
        {
          method: "POST",
          headers: {
            Accept: "application/json",
            "Content-Type": "application/json",
            "X-Team-Addon-Response-Scope": "view",
          },
          body: JSON.stringify(peers.mutations.mutationBodyWithConflictMetadata(endpoint, "POST", {
            dismissed_by: "operator",
          })),
        },
      );

      if (response.status === 409) {
        const conflict = await response.json().catch(() => ({}));
        await peers.app.reloadActiveTeamView({ preserveForms: true });
        peers.drafts.restoreFormDrafts(drafts);
        const current = conflict.current_revision ?? peers.app.state.revision;
        peers.app.setStatus(`Revision conflict. Reloaded revision ${current}; review and dismiss again.`, "warning");
        return;
      }

      if (!response.ok) {
        throw new Error(await responseText(response));
      }

      await peers.mutations.applyMutationResultPayload(await response.json());
      peers.drafts.restoreFormDrafts(drafts);
      peers.app.setStatus(`Dismissed failed action ${actionRecordId}.`, "ok");
    } catch (error) {
      peers.app.setStatus(error instanceof Error ? error.message : "Dismiss failed.", "error");
    } finally {
      if (button.isConnected) {
        button.disabled = false;
      }
    }
  }
  function handleAgentActionsClick(event) {
    const contextRotationButton = event.target.closest("[data-context-rotation-agent-id]");
    if (contextRotationButton) {
      event.preventDefault();
      if (!peers.access.guardTeamMutation("clear agent context")) {
        return true;
      }
      void requestContextRotation(
        contextRotationButton,
        contextRotationButton.dataset.contextRotationAgentId,
      );
      return true;
    }

    const standDownButton = event.target.closest("[data-stand-down-agent-id]");
    if (standDownButton) {
      event.preventDefault();
      if (!peers.access.guardTeamMutation("stand down agents")) {
        return true;
      }
      void requestAgentStandDown(
        standDownButton,
        standDownButton.dataset.standDownAgentId,
        standDownButton.dataset.standDownScope,
      );
      return true;
    }

    const standDownRevokeButton = event.target.closest("[data-stand-down-revoke-agent-id]");
    if (standDownRevokeButton) {
      event.preventDefault();
      if (
        standDownRevokeButton.matches(":disabled")
        || standDownRevokeButton.getAttribute("aria-disabled") === "true"
      ) {
        return true;
      }
      if (!peers.access.guardTeamMutation("revoke stand-downs")) {
        return true;
      }
      void revokeAgentStandDown(
        standDownRevokeButton,
        standDownRevokeButton.dataset.standDownRevokeAgentId,
      );
      return true;
    }

    const dismissActionButton = event.target.closest("[data-dismiss-action-id]");
    if (dismissActionButton) {
      event.preventDefault();
      if (!peers.access.guardTeamMutation("dismiss failed actions")) {
        return true;
      }
      void dismissActionRecordAttention(
        dismissActionButton,
        dismissActionButton.dataset.dismissActionId,
      );
      return true;
    }
    return false;
  }

  return {
    handleAgentActionsClick,
  };
}

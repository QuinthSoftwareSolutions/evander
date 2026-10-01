// Agent avatar upload, preview, acceptance, and removal.
import {
  escapeAttr,
  escapeHtml,
  formData,
  responseText,
  text,
} from "./team-ui-format.js";
import { API_BASE } from "./team-ui-config.js";

export function createTeamAvatarsController(peers) {

  async function uploadAgentAvatar(button) {
    if (!peers.access.guardTeamMutation("upload avatars")) {
      return;
    }
    const form = button.closest("form");
    const agentId = text(button.dataset.avatarUploadAgentId);
    const file = form?.elements.avatar_file?.files?.[0] || null;
    if (!form || !agentId || !file) {
      peers.app.setStatus("Choose an avatar image to upload.", "error");
      return;
    }
    const body = new FormData();
    body.append("expected_revision", String(peers.app.state.revision));
    body.append("file", file);
    await submitAvatarFormData(form, `/agents/${encodeURIComponent(agentId)}/avatar/upload`, body);
  }

  async function generateAvatarPreview(button) {
    if (!peers.access.guardTeamMutation("generate avatar previews")) {
      return;
    }
    const form = button.closest("form");
    const agentId = text(button.dataset.avatarGenerateAgentId);
    if (!form || !agentId) {
      peers.app.setStatus("No agent selected.", "error");
      return;
    }
    const data = formData(form);
    peers.drafts.setFormBusy(form, true);
    peers.app.setStatus("Generating avatar preview.", "loading");
    try {
      const response = await peers.access.teamFetch(`${API_BASE}/agents/${encodeURIComponent(agentId)}/avatar/previews`, {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          style_preset: text(data.avatar_style_preset) || "professional",
          prompt_note: text(data.avatar_prompt_note) || null,
        }),
      });
      if (!response.ok) {
        throw new Error(await responseText(response));
      }
      const payload = await response.json();
      const preview = payload.preview || {};
      const slot = form.querySelector("[data-avatar-preview-slot]");
      if (slot) {
        slot.innerHTML = `
          <div class="avatar-preview-card">
            <img src="${escapeAttr(preview.url)}" alt="Generated avatar preview" />
            <div class="meta-line">Preview ready - ${escapeHtml(preview.style_preset || "generated")}</div>
          </div>
        `;
      }
      const acceptButton = form.querySelector("[data-avatar-accept-agent-id]");
      if (acceptButton) {
        acceptButton.disabled = false;
        acceptButton.dataset.avatarPreviewId = preview.preview_id || "";
      }
      peers.app.setStatus("Avatar preview generated.", "ok");
    } catch (error) {
      peers.app.setStatus(error instanceof Error ? error.message : "Avatar generation failed.", "error");
    } finally {
      peers.drafts.setFormBusy(form, false);
    }
  }

  async function acceptAvatarPreview(button) {
    if (!peers.access.guardTeamMutation("accept avatar previews")) {
      return;
    }
    const form = button.closest("form");
    const agentId = text(button.dataset.avatarAcceptAgentId);
    const previewId = text(button.dataset.avatarPreviewId);
    if (!form || !agentId || !previewId) {
      peers.app.setStatus("Generate a preview before accepting it.", "error");
      return;
    }
    await submitAvatarJson(
      form,
      `/agents/${encodeURIComponent(agentId)}/avatar/accept-preview`,
      "POST",
      { preview_id: previewId },
      agentId,
    );
  }

  async function removeAgentAvatar(button) {
    if (!peers.access.guardTeamMutation("remove avatars")) {
      return;
    }
    const form = button.closest("form");
    const agentId = text(button.dataset.avatarRemoveAgentId);
    if (!form || !agentId) {
      peers.app.setStatus("No agent selected.", "error");
      return;
    }
    await submitAvatarJson(
      form,
      `/agents/${encodeURIComponent(agentId)}/avatar`,
      "DELETE",
      {},
      agentId,
    );
  }

  async function submitAvatarFormData(form, endpoint, body) {
    if (!peers.access.guardTeamMutation("upload avatars")) {
      return false;
    }
    const drafts = peers.drafts.captureFormDrafts();
    peers.drafts.setFormBusy(form, true);
    peers.app.setStatus("Saving avatar.", "loading");
    try {
      const response = await peers.access.teamFetch(`${API_BASE}${endpoint}`, {
        method: "POST",
        headers: {
          Accept: "application/json",
          "X-Team-Addon-Response-Scope": "view",
        },
        body,
      });
      if (response.status === 409) {
        const conflict = await response.json().catch(() => ({}));
        await peers.app.reloadActiveTeamView({ preserveForms: true });
        peers.drafts.restoreFormDrafts(drafts);
        const current = conflict.current_revision ?? peers.app.state.revision;
        peers.app.setStatus(`Revision conflict. Reloaded revision ${current}; review and submit again.`, "warning");
        return false;
      }
      if (!response.ok) {
        throw new Error(await responseText(response));
      }
      await peers.mutations.applyMutationResultPayload(await response.json());
      peers.editor.openEditDialog("agent", text(form.dataset.editId));
      peers.app.setStatus(`Saved avatar at revision ${peers.app.state.revision}.`, "ok");
      return true;
    } catch (error) {
      peers.app.setStatus(error instanceof Error ? error.message : "Avatar save failed.", "error");
      return false;
    } finally {
      peers.drafts.setFormBusy(form, false);
    }
  }

  async function submitAvatarJson(form, endpoint, method, payload, agentId) {
    const saved = await peers.mutations.submitMutation(
      form,
      endpoint,
      method,
      payload,
      { resetOnSuccess: false },
    );
    if (saved && agentId) {
      peers.editor.openEditDialog("agent", agentId);
    }
    return saved;
  }
  function handleAvatarClick(event) {
    const avatarGenerateButton = event.target.closest("[data-avatar-generate-agent-id]");
    if (avatarGenerateButton) {
      event.preventDefault();
      if (!peers.access.guardTeamMutation("generate avatar previews")) {
        return true;
      }
      void generateAvatarPreview(avatarGenerateButton);
      return true;
    }

    const avatarAcceptButton = event.target.closest("[data-avatar-accept-agent-id]");
    if (avatarAcceptButton) {
      event.preventDefault();
      if (!peers.access.guardTeamMutation("accept avatar previews")) {
        return true;
      }
      void acceptAvatarPreview(avatarAcceptButton);
      return true;
    }

    const avatarUploadButton = event.target.closest("[data-avatar-upload-agent-id]");
    if (avatarUploadButton) {
      event.preventDefault();
      if (!peers.access.guardTeamMutation("upload avatars")) {
        return true;
      }
      void uploadAgentAvatar(avatarUploadButton);
      return true;
    }

    const avatarRemoveButton = event.target.closest("[data-avatar-remove-agent-id]");
    if (avatarRemoveButton) {
      event.preventDefault();
      if (!peers.access.guardTeamMutation("remove avatars")) {
        return true;
      }
      void removeAgentAvatar(avatarRemoveButton);
      return true;
    }
    return false;
  }

  return {
    handleAvatarClick,
  };
}

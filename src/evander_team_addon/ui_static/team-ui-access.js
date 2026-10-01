// Privilege messages, mutation controls, and bounded HTTP requests.
import { escapeAttr, escapeHtml, text } from "./team-ui-format.js";
import { lockedMutationControlSelectors, teamRequestTimeoutMs } from "./team-ui-config.js";

export function createTeamAccessController(peers) {

  let teamPrivilegeUnlocked = readTeamPrivilegedUnlockedFromLocation();

  const lockedMutationControlStates = new WeakMap();

  function readTeamPrivilegedUnlockedFromLocation() {
    return new URLSearchParams(window.location.search).get("privileged_unlocked") === "true";
  }

  function teamPrivilegedUnlocked() {
    return teamPrivilegeUnlocked;
  }

  function setTeamPrivilegedUnlocked(unlocked) {
    const nextUnlocked = Boolean(unlocked);
    if (teamPrivilegeUnlocked === nextUnlocked) {
      return;
    }
    const drafts = peers.drafts.captureFormDrafts();
    teamPrivilegeUnlocked = nextUnlocked;
    peers.app.render();
    peers.drafts.restoreFormDrafts(drafts);
  }

  function setTeamTheme(theme) {
    const normalizedTheme = text(theme);
    if (normalizedTheme !== "light" && normalizedTheme !== "dark") {
      return;
    }
    document.documentElement.dataset.theme = normalizedTheme;
    document.documentElement.style.colorScheme = normalizedTheme;
  }

  function bindPrivilegeMessages() {
    window.addEventListener("message", (event) => {
      if (
        event.origin !== window.location.origin
        || event.source !== window.parent
      ) {
        return;
      }
      const data = event.data || {};
      if (data.type === "admin-addon-context") {
        setTeamTheme(data.theme);
        setTeamPrivilegedUnlocked(Boolean(data.privileged_operations?.unlocked));
        return;
      }
      if (data.type === "admin-privileged-operations") {
        setTeamPrivilegedUnlocked(Boolean(data.privileged_operations?.unlocked));
      }
    });
    if (window.parent !== window) {
      window.parent.postMessage(
        { type: "admin-addon-ready" },
        window.location.origin,
      );
    }
  }

  function teamMutationLocked() {
    return !teamPrivilegedUnlocked();
  }

  function lockedMutationMessage(action = "make changes") {
    const normalizedAction = text(action) || "make changes";
    return `Unlock privileged operations to ${normalizedAction}.`;
  }

  function lockedAttrs(action = "make changes") {
    if (!teamMutationLocked()) {
      return "";
    }
    const message = lockedMutationMessage(action);
    return `disabled aria-disabled="true" title="${escapeAttr(message)}"`;
  }

  function guardTeamMutation(action = "make changes") {
    if (!teamMutationLocked()) {
      return true;
    }
    peers.app.setStatus(lockedMutationMessage(action), "warning");
    return false;
  }

  function applyLockedMutationControls(root = document) {
    if (!teamMutationLocked()) {
      const controls = [];
      if (root?.matches?.(".is-locked")) {
        controls.push(root);
      }
      root?.querySelectorAll?.(".is-locked").forEach((control) => controls.push(control));
      controls.forEach(unlockMutationControl);
      return;
    }
    for (const [selector, action] of lockedMutationControlSelectors) {
      const controls = [];
      if (root?.matches?.(selector)) {
        controls.push(root);
      }
      root?.querySelectorAll?.(selector).forEach((control) => controls.push(control));
      controls.forEach((control) => lockMutationControl(control, action));
    }
  }

  function lockMutationControl(control, action = "make changes") {
    if (!control) {
      return;
    }
    if (!lockedMutationControlStates.has(control)) {
      lockedMutationControlStates.set(control, {
        disabled:
          control instanceof HTMLButtonElement ||
          control instanceof HTMLInputElement ||
          control instanceof HTMLSelectElement ||
          control instanceof HTMLTextAreaElement
            ? control.disabled
            : null,
        ariaDisabled: control.getAttribute("aria-disabled"),
        title: control.getAttribute("title"),
      });
    }
    const message = lockedMutationMessage(action);
    if (
      control instanceof HTMLButtonElement ||
      control instanceof HTMLInputElement ||
      control instanceof HTMLSelectElement ||
      control instanceof HTMLTextAreaElement
    ) {
      control.disabled = true;
    }
    control.setAttribute("aria-disabled", "true");
    control.setAttribute("title", message);
    control.classList?.add?.("is-locked");
  }

  function unlockMutationControl(control) {
    const previous = lockedMutationControlStates.get(control);
    if (!control || !previous) {
      return;
    }
    if (previous.disabled !== null) {
      control.disabled = previous.disabled;
    }
    restoreOptionalAttribute(control, "aria-disabled", previous.ariaDisabled);
    restoreOptionalAttribute(control, "title", previous.title);
    control.classList?.remove?.("is-locked");
    lockedMutationControlStates.delete(control);
  }

  function restoreOptionalAttribute(control, name, value) {
    if (value === null) {
      control.removeAttribute(name);
    } else {
      control.setAttribute(name, value);
    }
  }

  function teamFetch(input, init = {}, { timeoutMs = teamRequestTimeoutMs } = {}) {
    const timeout = Number(timeoutMs);
    if (!Number.isFinite(timeout) || timeout <= 0 || typeof AbortController === "undefined") {
      return window.fetch(input, init);
    }

    const controller = new AbortController();
    const upstreamSignal = init?.signal || null;
    let timedOut = false;
    const abortFromUpstream = () => {
      controller.abort(upstreamSignal?.reason);
    };

    if (upstreamSignal) {
      if (upstreamSignal.aborted) {
        controller.abort(upstreamSignal.reason);
      } else {
        upstreamSignal.addEventListener("abort", abortFromUpstream, { once: true });
      }
    }

    const timer = window.setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, timeout);

    return window.fetch(input, { ...init, signal: controller.signal })
      .catch((error) => {
        if (timedOut && error?.name === "AbortError") {
          throw new Error(`Request timed out after ${Math.round(timeout / 1000)}s.`);
        }
        throw error;
      })
      .finally(() => {
        window.clearTimeout(timer);
        upstreamSignal?.removeEventListener?.("abort", abortFromUpstream);
      });
  }

  function editButton(type, id, label = "Edit") {
    return `<button type="button" class="record-action" data-edit-type="${escapeAttr(type)}" data-edit-id="${escapeAttr(id)}">${escapeHtml(label)}</button>`;
  }

  function taskCancellationButton(task) {
    const availability = peers.tasks.taskCancellationAvailability(task);
    return `
      <button
        type="button"
        class="record-action task-cancellation-action"
        data-task-cancel-id="${escapeAttr(task?.id)}"
        title="${escapeAttr(availability.explanation)}"
        ${availability.allowed ? "" : 'disabled aria-disabled="true"'}
      >Cancel task</button>
    `;
  }

  return {
    applyLockedMutationControls,
    bindPrivilegeMessages,
    editButton,
    guardTeamMutation,
    lockMutationControl,
    lockedAttrs,
    lockedMutationMessage,
    taskCancellationButton,
    teamFetch,
    teamMutationLocked,
    teamPrivilegedUnlocked,
  };
}

// Native disclosure state across view rendering.
import { escapeAttr, text } from "./team-ui-format.js";
import { normalizedTeamView } from "./team-ui-state.js";

export function createTeamDisclosuresController(peers) {

  // Native disclosure state belongs to the DOM, so keep its intentional state
  // separately whenever a view renderer replaces that DOM. Rendered keys include
  // their Team view so shared renderers cannot couple hidden view copies.
  const openTeamDisclosures = new Set();

  const defaultedTeamDisclosures = new Set();

  function bindTeamDisclosures() {
    document.addEventListener("toggle", (event) => {
      const disclosure = event.target;
      if (!disclosure?.matches?.("details[data-team-disclosure-key]")) {
        return;
      }
      const key = text(disclosure.dataset.teamDisclosureKey);
      if (!key) {
        return;
      }
      if (disclosure.open) {
        openTeamDisclosures.add(key);
      } else {
        openTeamDisclosures.delete(key);
      }
    }, true);
  }

  function teamDisclosureKey(...parts) {
    return parts.map(text).filter(Boolean).join(":");
  }

  function teamDisclosureStateKey(key, view = peers.app.activeTeamView) {
    const normalizedKey = text(key);
    if (!normalizedKey) {
      return "";
    }
    return teamDisclosureKey(normalizedTeamView(view), normalizedKey);
  }

  function teamDisclosureAttributes(key, { defaultOpen = false } = {}) {
    const normalizedKey = teamDisclosureStateKey(key);
    if (!normalizedKey) {
      return "";
    }
    if (defaultOpen && !defaultedTeamDisclosures.has(normalizedKey)) {
      defaultedTeamDisclosures.add(normalizedKey);
      openTeamDisclosures.add(normalizedKey);
    }
    return `data-team-disclosure-key="${escapeAttr(normalizedKey)}"${openTeamDisclosures.has(normalizedKey) ? " open" : ""}`;
  }

  function rememberOpenTeamDisclosures(root = document) {
    root.querySelectorAll?.("details[data-team-disclosure-key]").forEach((disclosure) => {
      const key = text(disclosure.dataset.teamDisclosureKey);
      if (!key) {
        return;
      }
      if (disclosure.open) {
        openTeamDisclosures.add(key);
      } else {
        openTeamDisclosures.delete(key);
      }
    });
  }

  function openDisclosure(key) {
    openTeamDisclosures.add(key);
  }

  function closeDisclosure(key) {
    openTeamDisclosures.delete(key);
  }

  return {
    openDisclosure,
    closeDisclosure,
    bindTeamDisclosures,
    rememberOpenTeamDisclosures,
    teamDisclosureAttributes,
    teamDisclosureKey,
    teamDisclosureStateKey,
  };
}

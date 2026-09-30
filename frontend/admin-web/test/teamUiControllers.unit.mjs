import assert from "node:assert/strict";
import test from "node:test";

test("delegated record clicks stop when a guarded feature consumes the event", async () => {
  const previousDocument = globalThis.document;
  const previousWindow = globalThis.window;
  const listeners = new Map();
  globalThis.document = {
    querySelector: () => null,
    querySelectorAll: () => [],
    addEventListener: (type, handler) => listeners.set(type, handler),
  };
  globalThis.window = { location: { search: "", hash: "" } };
  try {
    const { createTeamEventsController } = await import(
      "../../../src/evander_team_addon/ui_static/team-ui-events.js"
    );
    const { createTeamCopyController } = await import(
      "../../../src/evander_team_addon/ui_static/team-ui-copy.js"
    );
    let edits = 0;
    let guardedCopies = 0;
    const skip = () => false;
    const noop = () => {};
    const peers = {
      access: { guardTeamMutation: () => { guardedCopies += 1; return false; } },
      chat: { bindConversationDraftCapture: noop, handleOpenAgentChatClick: skip, handleChatNavigationClick: skip },
      people: { handleTeamCreationClick: skip, handlePeopleNavigationClick: skip, handleProjectRoleSelectionClick: skip },
      governance: { handleGroupCreationClick: skip, handleAgendaClick: skip },
      agents: { handleOpenSessionClick: skip },
      voice: { handleStartVoiceCallClick: skip, handleVoiceControlsClick: skip },
      costs: { handleBackfillClick: skip },
      recovery: { handleRecoveryPreviewClick: skip, handleRecoveryActionsClick: skip },
      agentActions: { handleAgentActionsClick: skip },
      projects: { handleLeadTransferClick: skip },
      avatars: { handleAvatarClick: skip },
      editor: {
        bindDialogKeyboard: noop,
        bindEditSubmission: noop,
        bindCancellationSubmission: noop,
        handleRecordDialogClick: () => { edits += 1; return true; },
      },
    };
    peers.copy = createTeamCopyController(peers);
    createTeamEventsController(peers).bindRecordEditing();
    const click = listeners.get("click");
    assert.equal(typeof click, "function");

    // A copy action inside an editable record must stop at its denied guard.
    const copyButton = { dataset: { teamCopyOpen: "team" } };
    click({
      preventDefault() {},
      target: { closest: (selector) => selector === "[data-team-copy-open]" ? copyButton : null },
    });
    assert.equal(guardedCopies, 1);
    assert.equal(edits, 0);

    // An unrelated record click still reaches the final editor handler.
    click({ preventDefault() {}, target: { closest: () => null } });
    assert.equal(guardedCopies, 1);
    assert.equal(edits, 1);
  } finally {
    if (previousDocument === undefined) delete globalThis.document;
    else globalThis.document = previousDocument;
    if (previousWindow === undefined) delete globalThis.window;
    else globalThis.window = previousWindow;
  }
});

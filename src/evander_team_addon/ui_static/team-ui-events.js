// Register delegated listeners in their original order. A handler returning true consumes the event.

export function createTeamEventsController(peers) {
  function bindForms() {
    peers.drafts.bindDraftInput();
    peers.drafts.bindDraftChange();
    peers.forms.bindCreateForms();
    peers.tasks.bindTaskUpdateForm();
    peers.reviews.bindReviewDecisionForm();
    peers.copy.bindTeamCopySubmission();
    peers.chat.bindMessageSubmission();
    peers.chat.bindComposerKeyboard();
    peers.chat.bindAttachmentPolicySubmission();
    peers.chat.bindAttachmentPolicyControls();
    peers.forms.bindInlineCreation();
    peers.forms.bindDelegatedCreation();
    peers.projects.bindGitImport();
    peers.releases.bindReleaseCancelAndRetry();
    peers.releases.bindGateWaivers();
    peers.tasks.bindCleanupRetries();
    peers.tasks.bindDependencyRepair();
    peers.costs.bindBudgetReset();
    document.addEventListener("click", (event) => {
      if (peers.governance.handleGovernanceEditorClick(event)) return;
      if (peers.policy.handlePolicyEditorClick(event)) return;
      if (peers.humanReviews.handleFindingsClick(event)) return;
      if (peers.reviews.handleCriteriaClick(event)) return;
    });
    document.addEventListener("change", (event) => {
      if (peers.governance.handleMemberTeamChange(event)) return;
      if (peers.chat.handleChatSettingsChange(event)) return;
    });
    document.addEventListener("input", (event) => {
      if (peers.chat.handleComposerInput(event)) return;
      if (peers.governance.handleGoalInput(event)) return;
      if (peers.policy.handleChecklistInput(event)) return;
      if (peers.humanReviews.handleFindingInput(event)) return;
      if (peers.governance.handleMemberTeamSearch(event)) return;
    });
    peers.chat.bindAttachmentRemoval();
  }
  function bindRecordEditing() {
    peers.chat.bindConversationDraftCapture();
    document.addEventListener("click", (event) => {
      if (peers.copy.handleCopyTeamClick(event)) return;
      if (peers.people.handleTeamCreationClick(event)) return;
      if (peers.governance.handleGroupCreationClick(event)) return;
      if (peers.people.handlePeopleNavigationClick(event)) return;
      if (peers.agents.handleOpenSessionClick(event)) return;
      if (peers.chat.handleOpenAgentChatClick(event)) return;
      if (peers.people.handleProjectRoleSelectionClick(event)) return;
      if (peers.voice.handleStartVoiceCallClick(event)) return;
      if (peers.chat.handleChatNavigationClick(event)) return;
      if (peers.costs.handleBackfillClick(event)) return;
      if (peers.voice.handleVoiceControlsClick(event)) return;
      if (peers.recovery.handleRecoveryPreviewClick(event)) return;
      if (peers.agentActions.handleAgentActionsClick(event)) return;
      if (peers.recovery.handleRecoveryActionsClick(event)) return;
      if (peers.governance.handleAgendaClick(event)) return;
      if (peers.projects.handleLeadTransferClick(event)) return;
      if (peers.avatars.handleAvatarClick(event)) return;
      if (peers.editor.handleRecordDialogClick(event)) return;
    });
    peers.editor.bindDialogKeyboard();
    peers.editor.bindEditSubmission();
    peers.editor.bindCancellationSubmission();
  }

  return {
    bindForms,
    bindRecordEditing,
  };
}

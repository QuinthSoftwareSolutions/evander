// Compose feature controllers; peer calls are deferred until the graph is complete.
import { attachIdentifierCopy } from "./team-identifiers.js";
import { elements } from "./team-ui-dom.js";
import { API_BASE, inboxItemLabels } from "./team-ui-config.js";
import {
  byId,
  escapeAttr,
  escapeHtml,
  humanizeToken,
  renderSelectOptions,
  responseText,
  taskStateLabel,
  text,
  values,
} from "./team-ui-format.js";
import { createTeamMailController } from "./team-mail.js";
import { createTeamActivityController } from "./team-activity.js";
import { createCaseContextController } from "./team-case-context.js";
import { createTeamAppController } from "./team-ui-app.js";
import { createTeamCostsController } from "./team-ui-costs.js";
import { createTeamOperationsController } from "./team-ui-operations.js";
import { createTeamTemplatesController } from "./team-ui-templates.js";
import { createTeamTimelineController } from "./team-ui-timeline.js";
import { createTeamChatController } from "./team-ui-chat.js";
import { createTeamGovernanceController } from "./team-ui-governance.js";
import { createTeamAccessController } from "./team-ui-access.js";
import { createTeamPlanningController } from "./team-ui-planning.js";
import { createTeamDisclosuresController } from "./team-ui-disclosures.js";
import { createTeamOrgController } from "./team-ui-org.js";
import { createTeamProjectsController } from "./team-ui-projects.js";
import { createTeamPeopleController } from "./team-ui-people.js";
import { createTeamBoardController } from "./team-ui-board.js";
import { createTeamCasesController } from "./team-ui-cases.js";
import { createTeamVoiceController } from "./team-ui-voice.js";
import { createTeamDraftsController } from "./team-ui-drafts.js";
import { createTeamMutationsController } from "./team-ui-mutations.js";
import { createTeamSessionsController } from "./team-ui-sessions.js";
import { createTeamCopyController } from "./team-ui-copy.js";
import { createTeamDeliveryController } from "./team-ui-delivery.js";
import { createTeamReleasesController } from "./team-ui-releases.js";
import { createTeamWorkspaceController } from "./team-ui-workspace.js";
import { createTeamHumanReviewsController } from "./team-ui-human-reviews.js";
import { createTeamTooltipsController } from "./team-ui-tooltips.js";
import { createTeamAgentsController } from "./team-ui-agents.js";
import { createTeamEventsController } from "./team-ui-events.js";
import { createTeamSelectsController } from "./team-ui-selects.js";
import { createTeamRecoveryController } from "./team-ui-recovery.js";
import { createTeamTasksController } from "./team-ui-tasks.js";
import { createTeamReviewsController } from "./team-ui-reviews.js";
import { createTeamEvidenceController } from "./team-ui-evidence.js";
import { createTeamFormsController } from "./team-ui-forms.js";
import { createTeamAvatarsController } from "./team-ui-avatars.js";
import { createTeamAgentActionsController } from "./team-ui-agent-actions.js";
import { createTeamPolicyController } from "./team-ui-policy.js";
import { createTeamEditorController } from "./team-ui-editor.js";

const peers = {};
peers.app = createTeamAppController(peers);
peers.costs = createTeamCostsController(peers);
peers.operations = createTeamOperationsController(peers);
peers.templates = createTeamTemplatesController(peers);
peers.timeline = createTeamTimelineController(peers);
peers.chat = createTeamChatController(peers);
peers.governance = createTeamGovernanceController(peers);
peers.access = createTeamAccessController(peers);
peers.planning = createTeamPlanningController(peers);
peers.disclosures = createTeamDisclosuresController(peers);
peers.org = createTeamOrgController(peers);
peers.projects = createTeamProjectsController(peers);
peers.people = createTeamPeopleController(peers);
peers.board = createTeamBoardController(peers);
peers.cases = createTeamCasesController(peers);
peers.voice = createTeamVoiceController(peers);
peers.drafts = createTeamDraftsController(peers);
peers.mutations = createTeamMutationsController(peers);
peers.sessions = createTeamSessionsController(peers);
peers.copy = createTeamCopyController(peers);
peers.delivery = createTeamDeliveryController(peers);
peers.releases = createTeamReleasesController(peers);
peers.workspace = createTeamWorkspaceController(peers);
peers.humanReviews = createTeamHumanReviewsController(peers);
peers.tooltips = createTeamTooltipsController(peers);
peers.agents = createTeamAgentsController(peers);
peers.events = createTeamEventsController(peers);
peers.selects = createTeamSelectsController(peers);
peers.recovery = createTeamRecoveryController(peers);
peers.tasks = createTeamTasksController(peers);
peers.reviews = createTeamReviewsController(peers);
peers.evidence = createTeamEvidenceController(peers);
peers.forms = createTeamFormsController(peers);
peers.avatars = createTeamAvatarsController(peers);
peers.agentActions = createTeamAgentActionsController(peers);
peers.policy = createTeamPolicyController(peers);
peers.editor = createTeamEditorController(peers);

attachIdentifierCopy(document);

document.addEventListener("DOMContentLoaded", () => {
  peers.governance.initializeGovernanceCreateGroupGoals();
  peers.app.bindTopLevelTabs();
  peers.events.bindForms();
  peers.events.bindRecordEditing();
  peers.sessions.bindExistingAgentPickers();
  peers.sessions.bindTeamAgentSessionControls();
  peers.selects.bindProjectLeadSelects();
  peers.selects.bindAgentEditTeamScopedSelects();
  peers.operations.bindRuntimeFilters();
  peers.workspace.bindWorkspaceAttentionPagination();
  peers.delivery.bindDeliveryFilters();
  peers.planning.bindPlanningFilters();
  peers.disclosures.bindTeamDisclosures();
  peers.projects.bindProjectFocus();
  peers.board.bindTaskBoardSelectors();
  peers.cases.bindCaseFilters();
  peers.org.bindOrgFocusTabs();
  peers.org.bindOrgFlowEvents();
  peers.app.bindArchivedVisibilityToggle();
  peers.governance.bindGovernanceAgendaVisibilityToggle();
  peers.agents.bindInboxPopoverPositioning();
  peers.mail.bind();
  peers.activity.bind();
  elements.templateSelect?.addEventListener("change", () => {
    peers.templates.renderTemplateSetupFields();
  });
  peers.timeline.bindTimelineFilters();
  peers.timeline.bindTimelinePagination();
  peers.app.bindPageLifecycle();
  peers.access.bindPrivilegeMessages();
  peers.tooltips.decorateFieldTooltips(document);
  void peers.app.bootTeamAddon();
});

peers.mail = createTeamMailController({
  apiBase: API_BASE,
  documentRoot: document,
  elements,
  getState: () => peers.app.state,
  isActive: () => peers.app.activeTeamView === "mail",
  teamFetch: peers.access.teamFetch,
  responseText,
  text,
  values,
  byId,
  agentLabel: peers.agents.agentLabel,
  renderSelectOptions,
  renderAgentAvatar: peers.agents.renderAgentAvatar,
  escapeHtml,
  escapeAttr,
  humanizeToken,
  taskStateLabel,
  inboxItemLabels,
  disclosureAttributes: peers.disclosures.teamDisclosureAttributes,
  captureDisclosures: peers.disclosures.rememberOpenTeamDisclosures,
  canMutate: peers.access.guardTeamMutation,
  mutationLocked: peers.access.teamMutationLocked,
  lockedMessage: peers.access.lockedMutationMessage,
  setStatus: peers.app.setStatus,
  mutationBody: (payload) =>
    peers.mutations.mutationBodyWithConflictMetadata("/operator-directives", "POST", payload),
  applyMutationResult: peers.mutations.applyMutationResultPayload,
  reloadActiveView: peers.app.reloadActiveTeamView,
});

peers.activity = createTeamActivityController({
  apiBase: API_BASE,
  documentRoot: document,
  elements,
  getState: () => peers.app.state,
  isActive: () => peers.app.activeTeamView === "activity",
  teamFetch: peers.access.teamFetch,
  responseText,
  text,
  values,
  byId,
  agentLabel: peers.agents.agentLabel,
  renderSelectOptions,
  escapeHtml,
  escapeAttr,
  humanizeToken,
});

createCaseContextController({ documentRoot: document, teamFetch: peers.access.teamFetch, apiBase: API_BASE,
  getState: () => peers.app.state, canMutate: peers.access.guardTeamMutation });

import assert from "node:assert/strict";
import { createServer as createHttpServer } from "node:http";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";
import test from "node:test";

import { chromium } from "playwright";
import { createServer as createViteServer } from "vite";
import { fulfillAdminShellBackgroundApiRequest } from "./adminShellHelpers.mjs";

const staticRoot = path.resolve(
  fileURLToPath(new URL("../../../src/evander_team_addon/ui_static", import.meta.url)),
);
const teamViewStorageKey = "team-addon:active-view";
const teamViewIds = [
  "workspace",
  "operations",
  "delivery",
  "projects",
  "task-board",
  "cases",
  "people",
  "chat",
  "mail",
  "governance",
  "planning",
  "timeline",
  "org",
];

test("team add-on static UI exposes runtime control hooks", async () => {
  const markup = await readFile(path.join(staticRoot, "index.html"), "utf8");
  const script = await readFile(path.join(staticRoot, "team-ui.js"), "utf8");
  const mailScript = await readFile(path.join(staticRoot, "team-mail.js"), "utf8");
  const stylesheet = await readFile(path.join(staticRoot, "team-ui.css"), "utf8");
  const orgFlowScript = await readFile(path.join(staticRoot, "team-org-flow.js"), "utf8");
  const orgFlowStylesheet = await readFile(path.join(staticRoot, "team-org-flow.css"), "utf8");

  assert.match(markup, /team-org-flow\.css/);
  assert.match(markup, /team-org-flow\.js/);
  assert.match(script, /context_clearing_policy/);
  assert.match(script, /contextClearingPolicies/);
  assert.match(script, /data-context-rotation-agent-id/);
  assert.match(script, /context-rotation/);
  assert.match(script, /data-stand-down-agent-id/);
  assert.match(script, /data-stand-down-scope="agent"/);
  assert.match(script, /data-stand-down-scope="subtree"/);
  assert.match(script, /data-stand-down-scope="agent">Stand down</);
  assert.match(script, />Stand down subtree</);
  assert.match(script, /and every subordinate in that agent's subtree/);
  assert.match(script, /data-stand-down-revoke-agent-id/);
  assert.match(script, /data-resume-release-recovery-blocker-id/);
  assert.match(script, /Resume release recovery/);
  assert.match(script, /admin_ui_release_recovery/);
  assert.match(script, /resumeReleaseRecovery/);
  assert.match(markup, /id="task-cancellation-modal"/);
  assert.match(script, /data-task-cancel-id/);
  assert.match(script, /\/cancel/);
  assert.match(stylesheet, /\.task-cancellation-action/);
  assert.match(script, /data-open-session-id/);
  assert.match(script, /admin-navigate-to-session/);
  assert.match(script, /stand-down/);
  assert.match(script, /Handoff summary/);
  assert.match(script, /data-team-view/);
  assert.match(script, /team-addon:active-view/);
  assert.doesNotMatch(markup, /data-team-view="setup"/);
  assert.doesNotMatch(markup, /team-setup-page/);
  assert.doesNotMatch(markup, /data-team-view="overview"/);
  assert.doesNotMatch(markup, /team-overview-page/);
  assert.doesNotMatch(script, /data-summary-grid/);
  assert.match(script, /data-review-delivery-view/);
  assert.match(script, /data-timeline-filter/);
  assert.match(markup, />\s*Activity timeline\s*</);
  assert.match(markup, /reconstructed from records still present in the current Team/);
  assert.match(markup, /not a compliance-grade audit log/);
  assert.match(markup, /data-team-view="mail"/);
  assert.match(markup, /id="team-mail-page"/);
  assert.match(markup, /data-mail-directive-open/);
  assert.match(markup, /data-mail-directive-form/);
  assert.match(markup, /data-mail-directive-recipient/);
  assert.match(script, /createTeamMailController/);
  assert.match(mailScript, /\/inbox\?/);
  assert.match(mailScript, /\/operator-directives/);
  assert.match(mailScript, /openDirectiveComposer/);
  assert.match(mailScript, /admin_ui_operator_directive/);
  assert.match(mailScript, /function renderReader/);
  assert.match(stylesheet, /\.mail-layout/);
  assert.match(stylesheet, /\.mail-directive-composer/);
  assert.match(script, /data-planning-task-status/);
  assert.match(script, /data-task-board-team/);
  assert.match(script, /renderTaskBoard/);
  assert.match(script, /taskBoardLanes/);
  assert.match(markup, /data-team-view="projects"/);
  assert.match(markup, /id="team-projects-page"/);
  assert.match(markup, /id="create-project-from-projects-form"/);
  assert.match(markup, /data-project-create-toggle/);
  assert.match(script, /renderProjectFocus/);
  assert.match(script, /projectReviewRoleOptionsForProject/);
  assert.match(script, /create-review-profile-for-project-form/);
  assert.match(script, /data-project-section-show-rejected/);
  assert.match(script, /projectRecordPageSize/);
  assert.match(script, /data-select-agent-id/);
  assert.match(script, /data-select-project-id/);
  assert.match(script, /data-select-role-id/);
  assert.match(script, /renderSelectableAgent/);
  assert.match(script, /renderSelectableProject/);
  assert.match(script, /renderSelectableRole/);
  assert.match(script, /create-team-from-teams-form/);
  assert.match(script, /data-team-inline-create-toggle/);
  assert.match(script, /data-team-copy-open/);
  assert.match(script, /data-team-copy-session-select/);
  assert.match(script, /\/teams\/\$\{encodeURIComponent\(sourceTeamId\)\}\/copy/);
  assert.match(script, /data-team-inline-create-form/);
  assert.match(script, /data-git-import-button/);
  assert.match(script, /data-review-profile-criterion-add/);
  assert.match(script, /reviewProfileCriteriaFromForm/);
  assert.match(script, /data-human-review-checklist-editor/);
  assert.match(script, /data-human-review-checklist-move/);
  assert.doesNotMatch(script, /parseHumanReviewChecklist/);
  assert.match(script, /rolePermissionTooltips/);
  assert.match(script, /Allows a same-project role holder to request Team release packages/);
  assert.match(script, /data-release-gate-retry/);
  assert.match(script, /\/retry-gate/);
  assert.match(script, /renderRolePermissionToken/);
  assert.match(script, /projectLeadAgentOptionsForTeam/);
  assert.match(script, /agentLeadProjectIds/);
  assert.match(script, /"project_ids"/);
  assert.match(script, /One agent may lead several projects/);
  assert.match(stylesheet, /\.projects-workbench/);
  assert.match(stylesheet, /\.projects-focus-row/);
  assert.match(stylesheet, /\.project-record-section/);
  assert.match(stylesheet, /\.team-inline-create-form/);
  assert.match(stylesheet, /\.team-copy-session-row/);
  assert.match(stylesheet, /\.criteria-editor-row/);
  assert.match(stylesheet, /\.human-review-checklist-editor-row/);
  assert.match(stylesheet, /\.git-import-actions/);
  assert.match(stylesheet, /\.permission-token/);
  assert.match(
    stylesheet,
    /\.human-review-decision-form \.checkbox-label > input\[type="checkbox"\] \{[\s\S]*?width: 1\.25rem;[\s\S]*?height: 1\.25rem;[\s\S]*?padding: 0;/,
  );
  assert.match(markup, /data-teams-create-team-open/);
  assert.match(markup, /data-git-import-button/);
  assert.match(script, /data-review-profile-criteria-editor/);
  assert.match(markup, /id="create-team-from-teams-form"/);
  assert.doesNotMatch(script, /Chat message sent\./);
  assert.doesNotMatch(script, /Chat message queued until the agent finishes current work/);
  assert.match(script, /data-chat-scroll-conversation-id/);
  assert.doesNotMatch(script, /chat-message-list[^`]+data-chat-conversation-id/);
  assert.match(script, /ensureStateStream/);
  assert.match(script, /\/state\/stream/);
  assert.match(script, /applyStateSnapshot/);
  assert.match(script, /openTeamDisclosures/);
  assert.match(script, /rememberOpenTeamDisclosures/);
  assert.match(mailScript, /captureDisclosures\?\.\(\)/);
  assert.match(mailScript, /mail-reader-envelope" \$\{disclosureAttributes/);
  const dynamicDisclosureTags = [...script.matchAll(/<details\b[^>]*>/g)].map(
    ([tag]) => tag,
  );
  assert.ok(dynamicDisclosureTags.length > 0, "expected dynamic disclosure markup");
  for (const tag of dynamicDisclosureTags) {
    assert.match(
      tag,
      /(teamDisclosureAttributes|planningDisclosureAttributes)/,
      `dynamic disclosure is not tracked: ${tag}`,
    );
  }
  assert.match(script, /teamViewCacheGeneration \+= 1/);
  assert.match(script, /loadedTeamViews\.clear\(\)/);
  assert.match(script, /loadView\(activeTeamView, \{ force: true, preserveForms: true \}\)/);
  assert.match(script, /pendingForcedTeamViewLoads/);
  assert.match(script, /return loadView\(normalizedView, \{ force: true, preserveForms: pendingLoad\.preserveForms \}\)/);
  assert.match(script, /chatReadMarkers/);
  assert.match(script, /chatConversationHasUnread/);
  assert.match(script, /team-view-tab-badge/);
  assert.match(script, /chatNotificationSoundStorageKey/);
  assert.match(script, /data-chat-notification-sound/);
  assert.match(script, /data-chat-attachment-input/);
  assert.match(script, /pendingChatAttachments/);
  assert.match(script, /chat\/attachment-policy/);
  assert.match(script, /renderChatAttachment/);
  assert.match(markup, /id="chat-attachment-policy-modal"/);
  assert.match(stylesheet, /\.chat-message-attachment/);
  assert.match(script, /maybePlayChatNotificationSound/);
  assert.match(script, /maybePlayChatCollectionNotificationSound/);
  assert.match(script, /document\.hasFocus/);
  assert.match(script, /AudioContext/);
  assert.match(
    stylesheet,
    /\.chat-sidebar\s*\{[\s\S]*?grid-template-rows:\s*auto auto minmax\(0, auto\) minmax\(0, 1fr\);/,
  );
  assert.match(
    stylesheet,
    /\.chat-conversations-view\s*\{[\s\S]*?min-height:\s*0;[\s\S]*?overflow:\s*hidden;/,
  );
  assert.match(stylesheet, /\.chat-sound-toggle/);
  assert.match(stylesheet, /\.chat-conversation-row\.has-unread/);
  assert.match(stylesheet, /\.chat-unread-badge/);
  assert.match(stylesheet, /\.team-view-tab\.has-unread/);
  assert.match(
    stylesheet,
    /\.chat-new-picker\s*\{[\s\S]*?overflow:\s*hidden;/,
  );
  assert.match(
    stylesheet,
    /\.chat-agent-picker\s*\{[\s\S]*?max-height:\s*min\(18rem, 45vh\);[\s\S]*?overflow-y:\s*auto;/,
  );
  assert.match(script, /renderTeamProjectDetail/);
  assert.match(script, /renderTeamRoleDetail/);
  assert.match(script, /class="meta-line role-description"/);
  assert.match(
    stylesheet,
    /\.role-description\s*\{[\s\S]*?white-space:\s*pre-wrap;/,
  );
  assert.match(script, /peopleViewTeams/);
  assert.match(script, /data-team-record-group-id/);
  assert.match(script, /data-team-record-toggle/);
  assert.match(script, /renderPeopleAgentDetail/);
  assert.match(script, /renderCompactRuntimeAgentRow/);
  assert.match(script, /agentAwakeState/);
  assert.match(script, /session_is_expired/);
  assert.match(script, /expired_agent_session/);
  assert.match(script, /buildOrgFlowGraph/);
  assert.match(script, /orgFlowLayoutFitKey\(nodes, edges\)/);
  assert.match(script, /team-org-flow:edit-agent/);
  assert.match(script, /team-org-flow:canvas-menu/);
  assert.match(script, /data-org-context-action/);
  assert.match(script, /action: "open-session"/);
  assert.match(script, /governance-add-team/);
  assert.match(script, /data-core-agent-session-create/);
  assert.match(script, /aria-describedby/);
  assert.match(script, /Agent ID \$\{escapeHtml\(node\.agentData\.id\)\}\. Role/);
  assert.match(script, /orgInboxIndicatorItemData/);
  assert.match(script, /items: statusRequestItems\.map\(orgInboxIndicatorItemData\)/);
  assert.match(orgFlowScript, /team-org-flow:edit-agent/);
  assert.match(orgFlowScript, /team-org-flow:canvas-menu/);
  assert.match(orgFlowScript, /team-org-flow:agent-menu/);
  assert.match(orgFlowScript, /team-org-flow:governance-menu/);
  assert.match(orgFlowScript, /aria-describedby/);
  assert.match(orgFlowScript, /"Agent ID "/);
  assert.match(orgFlowScript, /agent-inbox-popover/);
  assert.match(orgFlowScript, /nowheel/);
  assert.match(orgFlowScript, /addEventListener\("wheel"/);
  assert.match(orgFlowScript, /stopPropagation\(\)/);
  assert.match(stylesheet, /\.agent-inbox-popover\s*{[\s\S]*pointer-events:\s*auto;/);
  assert.match(orgFlowScript, /data-team-org-flow-canvas/);
  assert.match(orgFlowScript, /org-flow-activity-bubble/);
  assert.match(orgFlowScript, /org-flow-message-pulse/);
  assert.match(orgFlowStylesheet, /org-flow-active-halo/);
  assert.match(orgFlowStylesheet, /prefers-reduced-motion:reduce/);
  assert.match(orgFlowStylesheet, /\.team-org-flow-canvas/);
  assert.match(orgFlowStylesheet, /\.org-flow-governance-node/);
  assert.match(stylesheet, /\.org-context-menu/);
  assert.match(stylesheet, /\.org-action-form/);
  assert.match(stylesheet, /max-width:\s*min\(17rem,\s*calc\(100vw - 2rem\)\);/);
  assert.doesNotMatch(stylesheet, /min-width:\s*max-content/);
  assert.match(markup, /<section class="people-agents-panel" aria-labelledby="focused-teams-heading">/);
  assert.doesNotMatch(markup, /content-panel people-agents-panel/);
  assert.match(markup, /data-governance-create-toggle/);
  assert.match(markup, /show-handled-governance-agenda-toggle/);
  assert.match(markup, /id="governance-create-panel"[\s\S]*id="create-governance-group-form"/);
  assert.match(markup, /data-governance-create-goals/);
  assert.doesNotMatch(markup, /name="goals"/);
  assert.doesNotMatch(markup, /<summary>Create governance group<\/summary>/);
  assert.match(script, /showHandledGovernanceAgenda/);
  assert.match(script, /bindGovernanceAgendaVisibilityToggle/);
  assert.match(stylesheet, /\.governance-goals-subsection/);
  assert.match(stylesheet, /\.governance-goal-list li\s*{[\s\S]*linear-gradient/);
  assert.match(
    stylesheet,
    /button\.people-agent-select\s*{[\s\S]*grid-template-columns: auto minmax\(0, 1fr\);/,
  );
  assert.match(stylesheet, /\.task-board-lanes/);
  assert.match(stylesheet, /\.task-board-card-blocked/);
  assert.match(
    stylesheet,
    /\.task-board-view\s*\{[\s\S]*?display:\s*grid;[\s\S]*?gap:\s*0\.85rem;/,
  );
  assert.match(
    stylesheet,
    /\.team-record-group-header:hover,[\s\S]*?background: var\(--panel-alt\);/,
  );
  assert.match(stylesheet, /\.teams-create-team-panel/);
  assert.doesNotMatch(
    stylesheet.match(/\.team-record-group-header\s*\{[\s\S]*?\}/)?.[0] || "",
    /border-bottom:\s*1px/,
  );
  assert.match(script, /comparePeopleAgents/);
  assert.doesNotMatch(script, /class="agent-summary-meta"/);
  assert.match(stylesheet, /\.people-agent-role\s*{/);
  assert.match(
    stylesheet,
    /\.agent-detail-title\s*\{[\s\S]*?flex:\s*1 1 auto;[\s\S]*?text-align:\s*left;/,
  );
});

function emptyState() {
  return {
    revision: 0,
    teams: {},
    projects: {},
    roles: {},
    available_role_permissions: ["create_root_task", "request_release"],
    agents: {},
    agent_runtimes: {},
    tasks: {},
    deliverables: {},
    review_requests: {},
    review_profiles: {},
    governance_groups: {},
    governance_agenda: {},
    inbox: {},
    action_records: {},
    release_packages: {},
    human_review_requests: {},
    dispatches: {},
    worktree_cleanup_candidates: {},
    git_work_contexts: {},
    ephemeral_assignments: {},
    git_promotions: {},
    stand_downs: {},
    conversations: {},
    conversation_messages: {},
  };
}

function roleId(scopeId, roleKey) {
  return `${scopeId}__${roleKey.replaceAll("_", "-")}`;
}

function roleName(roleKey) {
  return roleKey
    .replaceAll("_", " ")
    .replaceAll("-", " ")
    .split(" ")
    .filter(Boolean)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function slugIdPart(value, fallback = "record") {
  const slug = String(value ?? "")
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return slug || fallback;
}

function existingIdSet(existingIds) {
  if (existingIds instanceof Set) return new Set(existingIds);
  if (Array.isArray(existingIds)) return new Set(existingIds);
  return new Set(Object.keys(existingIds || {}));
}

function generatedId(prefix, labels, existingIds) {
  const base = [
    slugIdPart(prefix),
    ...labels.map((label) => slugIdPart(label, "")).filter(Boolean),
  ].join("-");
  const used = existingIdSet(existingIds);
  let candidate = base;
  let suffix = 2;
  while (used.has(candidate)) {
    candidate = `${base}-${suffix}`;
    suffix += 1;
  }
  return candidate;
}

function uniqueIdFromBase(base, existingIds) {
  const used = existingIdSet(existingIds);
  let candidate = String(base ?? "").trim();
  let suffix = 2;
  while (used.has(candidate)) {
    candidate = `${base}-${suffix}`;
    suffix += 1;
  }
  return candidate;
}

function putRole(
  state,
  {
    id,
    teamId,
    projectId = null,
    roleKey,
    name = roleName(roleKey),
    description = "",
    permissions = [],
    ephemeralPolicy = { max_concurrent: 0 },
  },
) {
  state.roles[id] = {
    id,
    team_id: teamId,
    project_id: projectId,
    name,
    description,
    permissions,
    ephemeral_policy: ephemeralPolicy,
    archived: false,
    created_at: "2026-06-09T00:00:00Z",
    updated_at: "2026-06-09T00:00:00Z",
  };
  return id;
}

function autoDevSquadTemplate() {
  return {
    id: "auto-dev-squad",
    name: "Auto-Dev Squad",
    description:
      "Opinionated manager/lead/implementer/reviewer/release-operator squad for reviewed auto-dev delivery.",
    root_role_key: "manager",
    required_role_keys: [
      "manager",
      "project_lead",
      "implementer",
      "reviewer",
      "release_operator",
    ],
    roles: [
      {
        key: "manager",
        label: "Manager",
        description: "Owns operator communication and overall coordination.",
        reports_to_role_key: null,
        permissions: ["create_root_task"],
        ephemeral_policy: { max_concurrent: 0 },
      },
      {
        key: "project_lead",
        label: "Project Lead",
        description: "Owns the project plan and task routing.",
        reports_to_role_key: "manager",
        permissions: ["create_root_task"],
        ephemeral_policy: { max_concurrent: 1 },
      },
      {
        key: "implementer",
        label: "Implementer",
        description: "Builds the scoped change and keeps the implementation tight.",
        reports_to_role_key: "project_lead",
        ephemeral_policy: { max_concurrent: 2 },
      },
      {
        key: "reviewer",
        label: "Reviewer",
        description: "Checks scope, tests, and risk before delivery.",
        reports_to_role_key: "project_lead",
        ephemeral_policy: { max_concurrent: 1 },
      },
      {
        key: "release_operator",
        label: "Release Operator",
        description: "Handles release handoff and publication.",
        reports_to_role_key: "project_lead",
        permissions: ["request_release"],
        ephemeral_policy: { max_concurrent: 1 },
      },
    ],
    required_fields: [
      {
        path: "team.name",
        label: "Team Name",
        description: "Human-readable team name.",
        required: true,
        example: "Auto-Dev Squad",
      },
      {
        path: "team.operator_communication_target_id",
        label: "Operator Communication Target",
        description: "Optional target for manager escalations.",
        required: false,
        example: "slack://ops-channel",
      },
      {
        path: "project.name",
        label: "Project Name",
        description: "Human-readable project name.",
        required: true,
        example: "Reviewed Auto-Dev Delivery",
      },
      {
        path: "project.git.managed_repo_id",
        label: "Managed Git Repository ID",
        description: "Registered Evander managed repository.",
        required: false,
        example: "mr_0123456789abcdef0123",
      },
      {
        path: "project.git.base_branch",
        label: "Git Base Branch",
        description: "Optional upstream branch to mirror.",
        required: false,
        example: "main",
      },
      {
        path: "project.git.provider",
        label: "Git Provider",
        description: "Optional provider label for the canonical origin.",
        required: false,
        example: "github",
      },
      {
        path: "manager.session_id",
        label: "Manager Session ID",
        description: "Persistent manager session identifier.",
        required: true,
        example: "session-manager-auto-dev-squad",
      },
      {
        path: "project_lead.session_id",
        label: "Project Lead Session ID",
        description: "Persistent project lead session identifier.",
        required: true,
        example: "session-project-lead-auto-dev-squad",
      },
      {
        path: "implementer.session_id",
        label: "Implementer Session ID",
        description: "Persistent implementer session identifier.",
        required: true,
        example: "session-implementer-auto-dev-squad",
      },
      {
        path: "reviewer.session_id",
        label: "Reviewer Session ID",
        description: "Persistent reviewer session identifier.",
        required: true,
        example: "session-reviewer-auto-dev-squad",
      },
      {
        path: "release_operator.session_id",
        label: "Release Operator Session ID",
        description: "Persistent release operator session identifier.",
        required: true,
        example: "session-release-operator-auto-dev-squad",
      },
    ],
    project_goals: [
      {
        id: "auto-dev-reviewed-delivery",
        description: "Ship a reviewed auto-dev increment through implementation, review, and handoff.",
        state: "pending",
      },
    ],
    tasks: [
      {
        id: "implement-scoped-slice",
        title: "Implement the scoped slice",
        description:
          "Build the bounded change, add tests, and keep the implementation narrow enough for review.",
        owner_role_key: "implementer",
        created_by_role_key: "project_lead",
        priority: 10,
        state: "pending",
        parent_task_id: null,
        dependency_task_ids: [],
      },
      {
        id: "release-handoff",
        title: "Prepare release handoff",
        description:
          "Package release notes, verify artifacts, and hand off the reviewed result.",
        owner_role_key: "release_operator",
        created_by_role_key: "project_lead",
        priority: 20,
        state: "pending",
        parent_task_id: null,
        dependency_task_ids: ["implement-scoped-slice"],
      },
    ],
    review_profiles: [
      {
        id: "auto-dev-review",
        name: "Auto-Dev Delivery Review",
        description:
          "Checks that the slice stays in scope, automated tests exist, risk is understood, and the release handoff is ready.",
        project_scoped: true,
        applicable_role_keys: ["reviewer"],
        criteria: [
          {
            id: "scope",
            label: "Scope",
            description: "The deliverable stays inside the requested slice.",
            required: true,
          },
          {
            id: "tests",
            label: "Tests",
            description: "Automated checks were added or updated and pass.",
            required: true,
          },
          {
            id: "risk",
            label: "Risk",
            description: "Edge cases and rollback risk are called out.",
            required: true,
          },
          {
            id: "handoff",
            label: "Release Handoff",
            description: "Release notes and artifacts are ready to publish.",
            required: true,
          },
        ],
      },
    ],
  };
}

function jsonResponse(response, payload, status = 200) {
  response.writeHead(status, { "content-type": "application/json" });
  response.end(JSON.stringify(payload));
}

async function requestJson(request) {
  const chunks = [];
  for await (const chunk of request) {
    chunks.push(chunk);
  }
  const text = Buffer.concat(chunks).toString("utf8");
  return text ? JSON.parse(text) : {};
}

function buildTimelinePayload(state, url) {
  const filters = {
    team_id: queryText(url.searchParams, "team_id"),
    project_id: queryText(url.searchParams, "project_id"),
    agent_id: queryText(url.searchParams, "agent_id"),
    type: normalizedQueryValues(url.searchParams, "type"),
    status: normalizedQueryValues(url.searchParams, "status"),
    attention_needed: queryBool(url.searchParams, "attention_needed"),
  };
  const allEntries = collectTimelineEntries(state);
  const entries = allEntries.filter((entry) =>
    matchesTimelineFilters(entry, filters),
  );
  const typeFacetEntries = allEntries.filter((entry) =>
    matchesTimelineFilters(entry, { ...filters, type: null }),
  );
  const statusFacetEntries = allEntries.filter((entry) =>
    matchesTimelineFilters(entry, { ...filters, status: null }),
  );
  entries.sort(compareTimelineEntries);
  const totalCount = entries.length;
  const limit = Math.max(1, queryInt(url.searchParams, "limit", 50));
  const offset = Math.max(0, queryInt(url.searchParams, "offset", 0));
  const windowedEntries = entries.slice(offset, offset + limit);
  return {
    revision: state.revision,
    derived: true,
    audit_log: false,
    source: "derived_from_current_team_state",
    description:
      "Activity reconstructed from records retained in the current Team snapshot; this is not an audit log.",
    filters: compactFilters(filters),
    timestampless_count: entries.filter((entry) => entry.timestamp === null).length,
    total_count: totalCount,
    limit,
    offset,
    has_more: offset + windowedEntries.length < totalCount,
    facets: {
      types: unique(typeFacetEntries.map((entry) => entry.type)).sort(),
      statuses: unique(statusFacetEntries.map((entry) => entry.status).filter(Boolean)).sort(),
    },
    entries: windowedEntries,
  };
}

function buildMailPayload(state, url) {
  const filters = {
    team_id: queryText(url.searchParams, "team_id"),
    agent_id: queryText(url.searchParams, "agent_id"),
    direction: queryText(url.searchParams, "direction"),
    item_type: queryText(url.searchParams, "item_type"),
    state: queryText(url.searchParams, "state"),
    q: queryText(url.searchParams, "q"),
  };
  let items = Object.values(state.inbox || {});
  if (filters.team_id) {
    const teamAgentIds = new Set(
      Object.values(state.agents || {})
        .filter((agent) => agent.team_id === filters.team_id)
        .map((agent) => agent.id),
    );
    items = items.filter(
      (item) => teamAgentIds.has(item.sender_agent_id) || teamAgentIds.has(item.recipient_agent_id),
    );
  }
  if (filters.agent_id) {
    if (filters.direction === "received") {
      items = items.filter((item) => item.recipient_agent_id === filters.agent_id);
    } else if (filters.direction === "sent") {
      items = items.filter((item) => item.sender_agent_id === filters.agent_id);
    } else {
      items = items.filter(
        (item) => item.sender_agent_id === filters.agent_id || item.recipient_agent_id === filters.agent_id,
      );
    }
  }
  if (filters.item_type) {
    items = items.filter((item) => item.item_type === filters.item_type);
  }
  if (filters.q) {
    const needle = filters.q.toLowerCase();
    items = items.filter((item) => JSON.stringify(item).toLowerCase().includes(needle));
  }
  const stateCounts = {
    pending: items.filter((item) => (item.state || "pending") === "pending").length,
    handled: items.filter((item) => item.state === "handled").length,
  };
  if (filters.state) {
    items = items.filter((item) => (item.state || "pending") === filters.state);
  }
  items.sort((left, right) => {
    return (
      (Date.parse(right.created_at || "") || 0) - (Date.parse(left.created_at || "") || 0) ||
      String(right.id || "").localeCompare(String(left.id || ""))
    );
  });
  const totalCount = items.length;
  const limit = Math.max(1, queryInt(url.searchParams, "limit", 50));
  const offset = Math.max(0, queryInt(url.searchParams, "offset", 0));
  const page = items.slice(offset, offset + limit).map((item) => {
    const sender = mailAgentPayload(state, item.sender_agent_id);
    const recipient = mailAgentPayload(state, item.recipient_agent_id);
    const task = item.task_id ? state.tasks?.[item.task_id] : null;
    return {
      response_requested: true,
      priority: "normal",
      state: "pending",
      ...item,
      sender,
      recipient,
      task: task
        ? { id: task.id, title: task.title, project_id: task.project_id, state: task.state }
        : null,
      direction:
        filters.agent_id && item.sender_agent_id === filters.agent_id
          ? "sent"
          : filters.agent_id && item.recipient_agent_id === filters.agent_id
            ? "received"
            : item.sender_agent_id
              ? "internal"
              : "system",
    };
  });
  return {
    revision: state.revision,
    generated_at: "2026-06-09T00:00:00Z",
    filters: compactFilters(filters),
    state_counts: stateCounts,
    total_count: totalCount,
    limit,
    offset,
    has_more: offset + page.length < totalCount,
    items: page,
  };
}

function mailAgentPayload(state, agentId) {
  if (!agentId) return null;
  const agent = state.agents?.[agentId];
  if (!agent) return { id: agentId, display_name: agentId };
  const role = agent.role_id ? state.roles?.[agent.role_id] : null;
  const team = agent.team_id ? state.teams?.[agent.team_id] : null;
  return {
    id: agent.id,
    display_name: agent.display_name || agent.id,
    team_id: agent.team_id,
    team_name: team?.name || null,
    role_name: role?.name || null,
    avatar: agent.avatar || null,
  };
}

function collectTimelineEntries(state) {
  const entries = [];
  const taskIds = Object.keys(state.tasks || {}).sort();
  taskIds.forEach((taskId) => {
    const task = state.tasks[taskId];
    const project = state.projects[task.project_id];
    const teamId = project?.team_id ?? null;
    const relatedAgents = unique([
      task.owner_agent_id,
      task.created_by_agent_id,
      task.parent_task_id ? state.tasks[task.parent_task_id]?.owner_agent_id : null,
    ]);
    entries.push(
      timelineEntry({
        id: `task:${task.id}:created`,
        type: "task",
        category: "created",
        timestamp: task.created_at || null,
        timestamp_source: "created_at",
        actor_agent_id: task.created_by_agent_id,
        actor_subject: task.created_by_agent_id || task.created_by_actor || null,
        related_agent_ids: relatedAgents,
        team_id: teamId,
        project_id: task.project_id,
        task_id: task.id,
        status: task.state,
        attention_needed: taskNeedsAttention(task.state),
        summary: `Task ${task.id} created: ${task.title}`,
      }),
    );
    if (task.updated_at && task.updated_at !== task.created_at) {
      entries.push(
        timelineEntry({
          id: `task:${task.id}:updated`,
          type: "task",
          category: "updated",
          timestamp: task.updated_at,
          timestamp_source: "updated_at",
          actor_agent_id: task.owner_agent_id,
          related_agent_ids: relatedAgents,
          team_id: teamId,
          project_id: task.project_id,
          task_id: task.id,
          status: task.state,
          attention_needed: taskNeedsAttention(task.state),
          summary: `Task ${task.id} updated: ${task.title}`,
        }),
      );
    }
  });

  const inboxIds = Object.keys(state.inbox || {}).sort();
  inboxIds.forEach((inboxId) => {
    const item = state.inbox[inboxId];
    const recipient = state.agents[item.recipient_agent_id];
    const task = item.task_id ? state.tasks[item.task_id] : null;
    const project = task ? state.projects[task.project_id] : null;
    const teamId = recipient?.team_id ?? project?.team_id ?? null;
    const relatedAgents = unique([item.recipient_agent_id, item.sender_agent_id]);
    entries.push(
      timelineEntry({
        id: `inbox:${item.id}:created`,
        type: "inbox_item",
        category: "created",
        timestamp: item.created_at || null,
        timestamp_source: "created_at",
        actor_agent_id: item.sender_agent_id,
        related_agent_ids: relatedAgents,
        team_id: teamId,
        project_id: task?.project_id ?? null,
        task_id: item.task_id ?? null,
        inbox_item_id: item.id,
        status: item.state,
        attention_needed: item.state === "pending",
        summary: `Inbox item ${item.id} created for ${item.recipient_agent_id}`,
      }),
    );
    if (item.updated_at && item.updated_at !== item.created_at) {
      entries.push(
        timelineEntry({
          id: `inbox:${item.id}:handled`,
          type: "inbox_item",
          category: item.state === "handled" ? "handled" : "updated",
          timestamp: item.updated_at,
          timestamp_source: "updated_at",
          actor_agent_id: item.sender_agent_id,
          related_agent_ids: relatedAgents,
          team_id: teamId,
          project_id: task?.project_id ?? null,
          task_id: item.task_id ?? null,
          inbox_item_id: item.id,
          status: item.state,
          attention_needed: item.state === "pending",
          summary: `Inbox item ${item.id} ${item.state}`,
        }),
      );
    }
  });

  Object.keys(state.deliverables || {})
    .sort()
    .forEach((deliverableId) => {
      const deliverable = state.deliverables[deliverableId];
      const task = state.tasks[deliverable.task_id];
      const project = task ? state.projects[task.project_id] : null;
      const relatedAgents = unique([
        deliverable.producer_agent_id,
        task?.owner_agent_id,
        task?.created_by_agent_id,
      ]);
      entries.push(
        timelineEntry({
          id: `deliverable:${deliverable.id}:created`,
          type: "deliverable",
          category: "created",
          timestamp: deliverable.created_at || null,
          timestamp_source: "created_at",
          actor_agent_id: deliverable.producer_agent_id,
          related_agent_ids: relatedAgents,
          team_id: project?.team_id ?? null,
          project_id: task?.project_id ?? null,
          task_id: deliverable.task_id,
          deliverable_id: deliverable.id,
          status: deliverable.state,
          attention_needed: recordNeedsAttention(deliverable.state),
          summary: `Deliverable ${deliverable.id} created: ${deliverable.summary}`,
        }),
      );
    });

  Object.keys(state.review_requests || {})
    .sort()
    .forEach((reviewId) => {
      const review = state.review_requests[reviewId];
      const deliverable = state.deliverables[review.deliverable_id];
      const task = deliverable ? state.tasks[deliverable.task_id] : null;
      const project = task ? state.projects[task.project_id] : null;
      const relatedAgents = unique([
        review.reviewer_agent_id,
        deliverable?.producer_agent_id,
        task?.owner_agent_id,
        task?.created_by_agent_id,
      ]);
      entries.push(
        timelineEntry({
          id: `review:${review.id}:created`,
          type: "review",
          category: "requested",
          timestamp: review.created_at || null,
          timestamp_source: "created_at",
          timestamp_missing: review.created_at == null,
          actor_agent_id: review.reviewer_agent_id,
          related_agent_ids: relatedAgents,
          team_id: project?.team_id ?? null,
          project_id: task?.project_id ?? null,
          task_id: task?.id ?? null,
          deliverable_id: review.deliverable_id,
          review_request_id: review.id,
          status: review.state,
          attention_needed: recordNeedsAttention(review.state),
          summary: `Review requested for deliverable ${review.deliverable_id}`,
        }),
      );
      if (review.decision_at || review.state !== "pending") {
        entries.push(
          timelineEntry({
            id: `review:${review.id}:decision`,
            type: "review",
            category: "decision",
            timestamp: review.decision_at || null,
            timestamp_source: "decision_at",
            timestamp_missing: review.decision_at == null,
            actor_agent_id: review.reviewer_agent_id,
            related_agent_ids: relatedAgents,
            team_id: project?.team_id ?? null,
            project_id: task?.project_id ?? null,
            task_id: task?.id ?? null,
            deliverable_id: review.deliverable_id,
            review_request_id: review.id,
            status: review.state,
            attention_needed: review.state === "blocked" || review.state === "rejected",
            summary: `Review ${review.id} ${review.state}`,
          }),
        );
      }
    });

  Object.keys(state.action_records || {})
    .sort()
    .forEach((actionId) => {
      const record = state.action_records[actionId];
      const agent = state.agents[record.agent_id];
      entries.push(
        timelineEntry({
          id: `action:${record.id}:created`,
          type: "action",
          category: "created",
          timestamp: record.created_at || null,
          timestamp_source: "created_at",
          actor_agent_id: record.agent_id,
          related_agent_ids: unique([record.agent_id]),
          team_id: agent?.team_id ?? null,
          project_id: agent?.project_id ?? null,
          action_record_id: record.id,
          status: record.success ? "succeeded" : "failed",
          attention_needed: !record.success,
          summary: `Action ${record.id} ${record.success ? "succeeded" : "failed"}`,
        }),
      );
    });

  Object.keys(state.ephemeral_assignments || {})
    .sort()
    .forEach((assignmentId) => {
      const assignment = state.ephemeral_assignments[assignmentId];
      const project = state.projects[assignment.project_id];
      const relatedAgents = unique([assignment.parent_agent_id]);
      entries.push(
        timelineEntry({
          id: `ephemeral:${assignment.id}:requested`,
          type: "ephemeral_assignment",
          category: "requested",
          timestamp: assignment.created_at || null,
          timestamp_source: "created_at",
          actor_agent_id: assignment.parent_agent_id,
          related_agent_ids: relatedAgents,
          team_id: project?.team_id ?? null,
          project_id: assignment.project_id,
          task_id: assignment.task_id,
          ephemeral_assignment_id: assignment.id,
          status: assignment.state,
          attention_needed: ephemeralNeedsAttention(assignment.state),
          summary: `Ephemeral assignment ${assignment.id} requested for task ${assignment.task_id}`,
        }),
      );
      if (assignment.updated_at && assignment.updated_at !== assignment.created_at) {
        entries.push(
          timelineEntry({
            id: `ephemeral:${assignment.id}:state`,
            type: "ephemeral_assignment",
            category: "state",
            timestamp: assignment.updated_at,
            timestamp_source: "updated_at",
            actor_agent_id: assignment.parent_agent_id,
            related_agent_ids: relatedAgents,
            team_id: project?.team_id ?? null,
            project_id: assignment.project_id,
            task_id: assignment.task_id,
            ephemeral_assignment_id: assignment.id,
            status: assignment.state,
            attention_needed: ephemeralNeedsAttention(assignment.state),
            summary: `Ephemeral assignment ${assignment.id} ${assignment.state}`,
          }),
        );
      }
    });

  Object.keys(state.git_promotions || {})
    .sort()
    .forEach((promotionId) => {
      const promotion = state.git_promotions[promotionId];
      const project = state.projects[promotion.project_id];
      entries.push(
        timelineEntry({
          id: `git-promotion:${promotion.id}:requested`,
          type: "git_promotion",
          category: "requested",
          timestamp: promotion.created_at || null,
          timestamp_source: "created_at",
          actor_agent_id: promotion.actor_agent_id,
          related_agent_ids: unique([promotion.actor_agent_id]),
          team_id: project?.team_id ?? null,
          project_id: promotion.project_id,
          task_id: promotion.task_id || null,
          deliverable_id: promotion.deliverable_id || null,
          release_package_id: promotion.release_package_id || null,
          git_promotion_id: promotion.id,
          status: "pending",
          attention_needed: true,
          summary: `Git promotion ${promotion.id} requested from ${promotion.source_branch}`,
        }),
      );
      if (promotion.state !== "pending") {
        entries.push(
          timelineEntry({
            id: `git-promotion:${promotion.id}:result`,
            type: "git_promotion",
            category: "result",
            timestamp: promotion.updated_at || null,
            timestamp_source: "updated_at",
            actor_agent_id: promotion.actor_agent_id,
            related_agent_ids: unique([promotion.actor_agent_id]),
            team_id: project?.team_id ?? null,
            project_id: promotion.project_id,
            task_id: promotion.task_id || null,
            deliverable_id: promotion.deliverable_id || null,
            release_package_id: promotion.release_package_id || null,
            git_promotion_id: promotion.id,
            status: promotion.state,
            attention_needed: promotion.state === "failed",
            summary: `Git promotion ${promotion.id} ${promotion.state}`,
          }),
        );
      }
    });

  Object.keys(state.release_packages || {})
    .sort()
    .forEach((releaseId) => {
      const release = state.release_packages[releaseId];
      const project = state.projects[release.project_id];
      const relatedAgents = unique([
        project?.lead_agent_id,
        ...(release.deliverable_ids || []).map((deliverableId) =>
          state.deliverables[deliverableId]?.producer_agent_id,
        ),
      ]);
      entries.push(
        timelineEntry({
          id: `release:${release.id}:created`,
          type: "release_package",
          category: "created",
          timestamp: release.created_at || null,
          timestamp_source: "created_at",
          actor_agent_id: project?.lead_agent_id ?? null,
          related_agent_ids: relatedAgents,
          team_id: project?.team_id ?? null,
          project_id: release.project_id,
          release_package_id: release.id,
          status: release.state,
          attention_needed: releaseNeedsAttention(release.state),
          summary: `Release package ${release.id} created for project ${release.project_id}`,
        }),
      );
      if (release.state === "accepted") {
        const timestamp = release.published_at || release.updated_at || null;
        entries.push(
          timelineEntry({
            id: `release:${release.id}:published`,
            type: "release_package",
            category: "published",
            timestamp,
            timestamp_source: release.published_at ? "published_at" : "updated_at",
            timestamp_missing: timestamp == null,
            actor_agent_id: project?.lead_agent_id ?? null,
            related_agent_ids: relatedAgents,
            team_id: project?.team_id ?? null,
            project_id: release.project_id,
            release_package_id: release.id,
            status: release.state,
            attention_needed: releaseNeedsAttention(release.state),
            summary: `Release package ${release.id} published`,
          }),
        );
      } else if (release.state === "blocked") {
        entries.push(
          timelineEntry({
            id: `release:${release.id}:blocked`,
            type: "release_package",
            category: "blocked",
            timestamp: release.updated_at || null,
            timestamp_source: "updated_at",
            timestamp_missing: release.updated_at == null,
            actor_agent_id: project?.lead_agent_id ?? null,
            related_agent_ids: relatedAgents,
            team_id: project?.team_id ?? null,
            project_id: release.project_id,
            release_package_id: release.id,
            status: release.state,
            attention_needed: releaseNeedsAttention(release.state),
            summary: release.error ? `Release blocked: ${release.error}` : `Release ${release.id} blocked`,
          }),
        );
      }
    });

  return entries;
}

function matchesTimelineFilters(entry, filters) {
  if (filters.team_id && entry.team_id !== filters.team_id) {
    return false;
  }
  if (filters.project_id && entry.project_id !== filters.project_id) {
    return false;
  }
  if (filters.agent_id) {
    const related = new Set([entry.actor_agent_id, ...(entry.related_agent_ids || [])]);
    if (!related.has(filters.agent_id)) {
      return false;
    }
  }
  if (filters.type && !filters.type.includes(entry.type)) {
    return false;
  }
  if (filters.status && (!entry.status || !filters.status.includes(entry.status))) {
    return false;
  }
  if (filters.attention_needed !== null && entry.attention_needed !== filters.attention_needed) {
    return false;
  }
  return true;
}

function compareTimelineEntries(left, right) {
  const leftMissing = left.timestamp == null;
  const rightMissing = right.timestamp == null;
  if (leftMissing !== rightMissing) {
    return leftMissing ? 1 : -1;
  }
  const leftTime = timelineSortTime(left.timestamp);
  const rightTime = timelineSortTime(right.timestamp);
  if (leftTime !== rightTime) {
    return rightTime - leftTime;
  }
  return (
    String(left.category || "").localeCompare(String(right.category || "")) ||
    String(left.type || "").localeCompare(String(right.type || "")) ||
    String(left.id || "").localeCompare(String(right.id || ""))
  );
}

function timelineSortTime(timestamp) {
  if (!timestamp) {
    return Number.NEGATIVE_INFINITY;
  }
  const text = String(timestamp).trim();
  const normalized = isOffsetNaiveIsoDateTime(text) ? `${text}Z` : text;
  const parsed = Date.parse(normalized);
  return Number.isFinite(parsed) ? parsed : Number.NEGATIVE_INFINITY;
}

function isOffsetNaiveIsoDateTime(timestamp) {
  return (
    /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?$/.test(timestamp) &&
    !/(?:[zZ]|[+-]\d{2}:?\d{2})$/.test(timestamp)
  );
}

function timelineEntry(entry) {
  return {
    ...entry,
    timestamp_missing: entry.timestamp_missing ?? entry.timestamp == null,
    actor_subject: entry.actor_subject || entry.actor_agent_id || null,
    related_agent_ids: unique(entry.related_agent_ids || []),
  };
}

function compactFilters(filters) {
  const result = {};
  Object.entries(filters).forEach(([key, value]) => {
    if (value === null || value === undefined) {
      return;
    }
    if (Array.isArray(value) && !value.length) {
      return;
    }
    result[key] = value;
  });
  return result;
}

function queryText(searchParams, key) {
  const value = searchParams.get(key);
  if (!value) {
    return null;
  }
  const text = value.trim();
  return text || null;
}

function queryInt(searchParams, key, fallback) {
  const value = queryText(searchParams, key);
  if (value === null) {
    return fallback;
  }
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function normalizedQueryValues(searchParams, key) {
  const values = [];
  searchParams.getAll(key).forEach((value) => {
    value.split(",").forEach((part) => {
      const text = part.trim();
      if (text) {
        values.push(text);
      }
    });
  });
  return values.length ? [...new Set(values)] : null;
}

function queryBool(searchParams, key) {
  const value = queryText(searchParams, key);
  if (value === null) {
    return null;
  }
  if (["true", "1", "yes", "on"].includes(value.toLowerCase())) {
    return true;
  }
  if (["false", "0", "no", "off"].includes(value.toLowerCase())) {
    return false;
  }
  return null;
}

function unique(values) {
  return [...new Set(values.filter(Boolean).map((value) => String(value).trim()).filter(Boolean))];
}

function compareChatMessages(left, right) {
  return (
    String(left.created_at || "").localeCompare(String(right.created_at || "")) ||
    String(left.id || "").localeCompare(String(right.id || ""))
  );
}

function chatMessagesForConversation(state, conversationId) {
  return Object.values(state.conversation_messages || {})
    .filter((message) => message.conversation_id === conversationId)
    .sort(compareChatMessages);
}

function chatMessagePage(state, conversationId, { before = null, limit = 50 } = {}) {
  const messages = chatMessagesForConversation(state, conversationId);
  let end = messages.length;
  if (before) {
    const index = messages.findIndex((message) => message.id === before);
    end = index >= 0 ? index : end;
  }
  const boundedLimit = Math.max(1, Math.min(Number.parseInt(String(limit), 10) || 50, 100));
  const start = Math.max(0, end - boundedLimit);
  const page = messages.slice(start, end);
  return {
    messages: page,
    message_count: messages.length,
    has_older: start > 0,
    oldest_message_id: page[0]?.id || null,
    newest_message_id: page[page.length - 1]?.id || null,
  };
}

function chatAgentPayload(state, agentId) {
  const agent = state.agents?.[agentId];
  if (!agent) {
    return null;
  }
  return {
    id: agent.id,
    display_name: agent.display_name || agent.id,
    team_id: agent.team_id,
    project_id: agent.project_id || null,
    role_id: agent.role_id || null,
    role_name: null,
    status: agent.status || "active",
    session_id: agent.session_id || null,
    avatar: agent.avatar || null,
  };
}

function chatConversationPayload(state, conversation, { includeMessages = false, limit = 50 } = {}) {
  const page = chatMessagePage(state, conversation.id, { limit });
  const payload = {
    id: conversation.id,
    agent_id: conversation.agent_id,
    agent: chatAgentPayload(state, conversation.agent_id),
    created_by: conversation.created_by || null,
    mutation_mode: conversation.mutation_mode || "reply_only",
    last_message_preview: conversation.last_message_preview || "",
    created_at: conversation.created_at || null,
    updated_at: conversation.updated_at || null,
    last_interaction_at: conversation.last_interaction_at || null,
    message_count: page.message_count,
  };
  if (includeMessages) {
    Object.assign(payload, page, { messages_limit: limit });
  }
  return payload;
}

function chatCollectionPayload(state, { selectedConversationId = null, includeSelectedMessages = false } = {}) {
  const conversations = Object.values(state.conversations || {}).sort((left, right) => {
    return (
      String(right.last_interaction_at || "").localeCompare(String(left.last_interaction_at || "")) ||
      String(right.id || "").localeCompare(String(left.id || ""))
    );
  });
  let resolvedSelectedConversationId = selectedConversationId;
  if (
    !conversations.some((conversation) => conversation.id === resolvedSelectedConversationId)
  ) {
    resolvedSelectedConversationId = includeSelectedMessages ? conversations[0]?.id || null : selectedConversationId;
  }
  const eligibleAgents = Object.values(state.agents || {})
    .filter((agent) => (agent.status || "active") !== "archived")
    .sort((left, right) => String(left.display_name || left.id).localeCompare(String(right.display_name || right.id)))
    .map((agent) => chatAgentPayload(state, agent.id));
  return {
    revision: state.revision,
    selected_conversation_id: resolvedSelectedConversationId,
    eligible_agents: eligibleAgents,
    conversations: conversations.map((conversation) =>
      chatConversationPayload(state, conversation, {
        includeMessages: includeSelectedMessages && conversation.id === resolvedSelectedConversationId,
      }),
    ),
  };
}

function chatConversationSnapshotPayload(state, conversationId) {
  const conversation = state.conversations?.[conversationId];
  if (!conversation) {
    return null;
  }
  return {
    revision: state.revision,
    conversation: chatConversationPayload(state, conversation, {
      includeMessages: true,
      limit: 50,
    }),
  };
}

function writeChatStreamSnapshot(streamRecord, state) {
  const payload = chatConversationSnapshotPayload(state, streamRecord.conversationId);
  if (!payload) {
    return;
  }
  streamRecord.response.write("event: snapshot\n");
  streamRecord.response.write(`data: ${JSON.stringify(payload)}\n\n`);
}

function bootstrapPayload(state, communicationTargets = null) {
  return {
    revision: state.revision,
    views: teamViewIds,
    default_view: "people",
    generated_at: "2026-06-09T00:00:00Z",
    current_operator: { user_id: "admin", display_name: "Admin" },
    selected_operator: { user_id: "admin", display_name: "Admin" },
    can_view_all_operators: true,
    operator_options: [{ user_id: "admin", display_name: "Admin" }],
    communication_targets: communicationTargets ?? [
      {
        value: "team-alerts",
        label: "team-alerts (teams/teams, read, reply, write)",
      },
    ],
    counts: {
      teams: Object.keys(state.teams || {}).length,
      governance_groups: Object.keys(state.governance_groups || {}).length,
      projects: Object.keys(state.projects || {}).length,
      agents: Object.keys(state.agents || {}).length,
      cases: Object.keys(state.cases || {}).length,
      tasks: Object.keys(state.tasks || {}).length,
      deliverables: Object.keys(state.deliverables || {}).length,
      review_requests: Object.keys(state.review_requests || {}).length,
      conversations: Object.keys(state.conversations || {}).length,
    },
  };
}

function viewPayload(state, view, url, template, timelinePayload) {
  const viewState = view === "mail"
    ? {
        revision: state.revision,
        teams: state.teams,
        projects: state.projects,
        roles: state.roles,
        agents: state.agents,
        resource_tokens: state.resource_tokens,
      }
    : state;
  const data = { state: viewState };
  if (view === "chat") {
    data.chat = chatCollectionPayload(state, {
      selectedConversationId: queryText(url.searchParams, "selected_conversation_id"),
      includeSelectedMessages: queryBool(url.searchParams, "include_selected_messages") === true,
    });
  }
  if (view === "workspace") {
    data.templates = [template];
  }
  if (view === "timeline") {
    data.timeline =
      typeof timelinePayload === "function"
        ? timelinePayload(state, url)
        : timelinePayload || buildTimelinePayload(state, url);
  }
  return {
    revision: state.revision,
    view,
    generated_at: "2026-06-09T00:00:00Z",
    data,
    resource_tokens: state.resource_tokens || {},
  };
}

function taskNeedsAttention(state) {
  return ["pending", "blocked", "waiting_for_review", "rejected"].includes(state);
}

function recordNeedsAttention(state) {
  return ["pending", "blocked", "rejected", "in_progress"].includes(state);
}

function ephemeralNeedsAttention(state) {
  return ["requested", "spawn_pending", "running", "cleanup_pending", "failed"].includes(state);
}

function releaseNeedsAttention(state) {
  return ["pending", "blocked"].includes(state);
}

async function serveStatic(response, assetName, contentType) {
  const assetPath = path.join(staticRoot, assetName);
  const body = await readFile(assetPath);
  response.writeHead(200, { "content-type": contentType });
  response.end(body);
}

async function createTeamAddonServer(
  t,
  {
    conflictOnFirstTeamCreate = false,
    conflictOnFirstAgentRegistration = false,
    conflictOnFirstGovernanceGroupUpdate = false,
    conversationsBeforeResponse = null,
    chatMessageBeforeResponse = null,
    costsBeforeResponse = null,
    costsDelayMs = 0,
    costsPayload = null,
    coreAgentItems = null,
    communicationTargets = null,
    initialState = null,
    releaseVerificationLivePayloads = null,
    timelinePayload = null,
  } = {},
) {
  const state = initialState ? { ...emptyState(), ...initialState } : emptyState();
  const requests = [];
  const viewRequests = [];
  const costRequests = [];
  const costResponses = [];
  const timelineRequests = [];
  const chatRequests = [];
  const openChatStreams = new Set();
  const openStateStreams = new Set();
  let conflictSent = false;
  let agentRegistrationConflictSent = false;
  let governanceGroupUpdateConflictSent = false;
  let releaseVerificationLiveRequestCount = 0;
  const template = autoDevSquadTemplate();
  const coreAgents = coreAgentItems ?? [
    {
      agent_id: "vm-existing-backend",
      backend: "libvirt_vm",
      target_name: "vm-existing-backend",
      session_id: "session-existing-backend",
      directory_path: "root",
      status: "running",
      updated_at: "2026-06-09T00:00:00Z",
      capabilities: {},
      host: {},
    },
    {
      agent_id: "worker-existing-reviewer",
      backend: "container_worker_vm",
      target_name: "worker-existing-reviewer",
      session_id: "session-existing-reviewer",
      directory_path: "root",
      status: "running",
      updated_at: "2026-06-09T00:00:00Z",
      capabilities: {},
      host: {},
    },
  ];
  const coreCreateOperations = new Map();

  const server = createHttpServer(async (request, response) => {
    try {
      const url = new URL(request.url ?? "/", "http://127.0.0.1");
      const { pathname } = url;

      if (request.method === "GET" && (pathname === "/" || pathname === "/addons/team-addon/")) {
        await serveStatic(response, "index.html", "text/html; charset=utf-8");
        return;
      }
      if (request.method === "GET" && pathname === "/addons/team-addon/team-ui.js") {
        await serveStatic(response, "team-ui.js", "application/javascript; charset=utf-8");
        return;
      }
      if (request.method === "GET" && pathname === "/addons/team-addon/team-mail.js") {
        await serveStatic(response, "team-mail.js", "application/javascript; charset=utf-8");
        return;
      }
      if (request.method === "GET" && pathname === "/addons/team-addon/team-case-board.js") {
        await serveStatic(response, "team-case-board.js", "application/javascript; charset=utf-8");
        return;
      }
      if (request.method === "GET" && pathname === "/addons/team-addon/team-activity.js") {
        await serveStatic(response, "team-activity.js", "application/javascript; charset=utf-8");
        return;
      }
      if (request.method === "GET" && pathname === "/addons/team-addon/team-ui.css") {
        await serveStatic(response, "team-ui.css", "text/css; charset=utf-8");
        return;
      }
      if (request.method === "GET" && pathname === "/addons/team-addon/team-org-flow.js") {
        await serveStatic(response, "team-org-flow.js", "application/javascript; charset=utf-8");
        return;
      }
      if (request.method === "GET" && pathname === "/addons/team-addon/team-org-flow.css") {
        await serveStatic(response, "team-org-flow.css", "text/css; charset=utf-8");
        return;
      }
      if (request.method === "GET" && pathname === "/addons/team-addon/team-agent-create.js") {
        await serveStatic(response, "team-agent-create.js", "application/javascript; charset=utf-8");
        return;
      }
      if (request.method === "GET" && pathname === "/addons/team-addon/team-agent-create.css") {
        await serveStatic(response, "team-agent-create.css", "text/css; charset=utf-8");
        return;
      }
      if (request.method === "GET" && pathname === "/api/addons/team-addon/state") {
        jsonResponse(response, state);
        return;
      }
      if (request.method === "GET" && pathname === "/api/addons/team-addon/state/stream") {
        response.writeHead(200, {
          "content-type": "text/event-stream",
          "cache-control": "no-cache",
          connection: "keep-alive",
        });
        response.write("event: snapshot\n");
        response.write(`data: ${JSON.stringify(state)}\n\n`);
        const interval = setInterval(() => {
          response.write(": keepalive\n\n");
        }, 1000);
        const streamRecord = { response, interval };
        openStateStreams.add(streamRecord);
        request.on("close", () => {
          clearInterval(interval);
          openStateStreams.delete(streamRecord);
        });
        return;
      }
      if (request.method === "GET" && pathname === "/api/addons/team-addon/bootstrap") {
        jsonResponse(response, bootstrapPayload(state, communicationTargets));
        return;
      }
      if (
        request.method === "GET" &&
        pathname === "/api/addons/team-addon/human-review-requests"
      ) {
        requests.push({ method: request.method, pathname, search: url.search });
        jsonResponse(response, {
          revision: state.revision,
          human_review_requests: Object.values(state.human_review_requests || {}),
        });
        return;
      }
      const releaseDetailMatch = pathname.match(
        /^\/api\/addons\/team-addon\/release-packages\/([^/]+)$/,
      );
      if (request.method === "GET" && releaseDetailMatch) {
        requests.push({ method: request.method, pathname, search: url.search });
        const releaseId = decodeURIComponent(releaseDetailMatch[1]);
        const release = state.release_packages?.[releaseId];
        if (!release) {
          jsonResponse(response, { error: "release package not found" }, 404);
          return;
        }
        jsonResponse(response, release.detail || release);
        return;
      }
      const releaseVerificationLiveMatch = pathname.match(
        /^\/api\/addons\/team-addon\/release-packages\/([^/]+)\/verification-live$/,
      );
      if (request.method === "GET" && releaseVerificationLiveMatch) {
        requests.push({ method: request.method, pathname, search: url.search });
        if (!releaseVerificationLivePayloads?.length) {
          jsonResponse(response, { error: "live verification output not configured" }, 404);
          return;
        }
        const payloadIndex = Math.min(
          releaseVerificationLiveRequestCount,
          releaseVerificationLivePayloads.length - 1,
        );
        releaseVerificationLiveRequestCount += 1;
        jsonResponse(response, releaseVerificationLivePayloads[payloadIndex]);
        return;
      }
      if (request.method === "GET" && pathname === "/api/addons/team-addon/inbox") {
        requests.push({ method: request.method, pathname, search: url.search });
        jsonResponse(response, buildMailPayload(state, url));
        return;
      }
      const viewMatch = pathname.match(/^\/api\/addons\/team-addon\/views\/([^/]+)$/);
      if (request.method === "GET" && viewMatch) {
        const view = decodeURIComponent(viewMatch[1]);
        viewRequests.push({ view, search: url.search });
        if (view === "chat") {
          chatRequests.push({ method: request.method, pathname, search: url.search });
          if (conversationsBeforeResponse) {
            await conversationsBeforeResponse({ request, url, chatRequests });
          }
        }
        if (view === "timeline") {
          timelineRequests.push(url.search);
        }
        if (!teamViewIds.includes(view)) {
          jsonResponse(response, { error: "view not found" }, 404);
          return;
        }
        jsonResponse(response, viewPayload(state, view, url, template, timelinePayload));
        return;
      }
      if (request.method === "GET" && pathname === "/api/addons/team-addon/costs") {
        costRequests.push(url.search);
        if (costsBeforeResponse) {
          await costsBeforeResponse;
        }
        if (costsDelayMs > 0) {
          await new Promise((resolve) => setTimeout(resolve, costsDelayMs));
        }
        jsonResponse(response, costsPayload || {
          generated_at: "2026-06-09T00:00:00Z",
          coverage_status: "complete",
          coverage_reasons: [],
          team_costs: {},
          project_costs: {},
          unattributed_costs: null,
          source_counts: {},
          events_count: 0,
          priced_events_count: 0,
          unpriced_events_count: 0,
          backfill: null,
        });
        costResponses.push(url.search);
        return;
      }
      if (request.method === "GET" && pathname === "/api/addons/team-addon/templates") {
        jsonResponse(response, { templates: [template] });
        return;
      }
      if (request.method === "GET" && pathname === "/api/addons/team-addon/conversations") {
        chatRequests.push({ method: request.method, pathname, search: url.search });
        if (conversationsBeforeResponse) {
          await conversationsBeforeResponse({ request, url, chatRequests });
        }
        jsonResponse(
          response,
          chatCollectionPayload(state, {
            selectedConversationId: queryText(url.searchParams, "selected_conversation_id"),
            includeSelectedMessages: queryBool(url.searchParams, "include_selected_messages") === true,
          }),
        );
        return;
      }
      if (
        request.method === "GET" &&
        pathname === "/api/addons/team-addon/templates/auto-dev-squad"
      ) {
        jsonResponse(response, template);
        return;
      }
      if (
        request.method === "POST" &&
        pathname === "/api/addons/team-addon/templates/auto-dev-squad/materialize"
      ) {
        const payload = await requestJson(request);
        requests.push({ method: request.method, pathname, payload });
        const duplicateError = materializationDuplicateError(state, payload, template);
        if (duplicateError) {
          jsonResponse(response, { error: duplicateError }, 400);
          return;
        }
        const materialized = applyTemplateMaterialization(state, payload, template);
        jsonResponse(response, { ...state, materialized }, 201);
        return;
      }
      if (request.method === "GET" && pathname === "/api/addons/team-addon/timeline") {
        timelineRequests.push(url.search);
        jsonResponse(
          response,
          typeof timelinePayload === "function"
            ? timelinePayload(state, url)
            : timelinePayload || buildTimelinePayload(state, url),
        );
        return;
      }
      if (request.method === "POST" && pathname === "/api/addons/team-addon/projects/git/import") {
        const payload = await requestJson(request);
        requests.push({ method: request.method, pathname, payload });
        const repoSource = String(payload.repo_source || "").trim();
        if (!repoSource) {
          jsonResponse(response, { error: "repo_source is required" }, 400);
          return;
        }
        const git = {
          managed_repo_id: "mr_0123456789abcdef0123",
          base_branch: "main",
        };
        jsonResponse(
          response,
          {
            imported: {
              success: true,
              repo_source: repoSource,
              managed_repo_id: git.managed_repo_id,
              base_branch: git.base_branch,
              git,
            },
            git,
          },
          201,
        );
        return;
      }
      if (request.method === "GET" && pathname === "/api/addons/team-addon/conversations") {
        requests.push({ method: request.method, pathname, search: url.search });
        jsonResponse(response, chatCollectionPayload(state));
        return;
      }
      const conversationDetailMatch = pathname.match(
        /^\/api\/addons\/team-addon\/conversations\/([^/]+)$/,
      );
      if (request.method === "GET" && conversationDetailMatch) {
        requests.push({ method: request.method, pathname, search: url.search });
        const conversationId = decodeURIComponent(conversationDetailMatch[1]);
        const conversation = state.conversations?.[conversationId];
        if (!conversation) {
          jsonResponse(response, { error: "conversation not found" }, 404);
          return;
        }
        jsonResponse(response, {
          revision: state.revision,
          conversation: chatConversationPayload(state, conversation, {
            includeMessages: true,
            limit: 50,
          }),
        });
        return;
      }
      const conversationMessagesMatch = pathname.match(
        /^\/api\/addons\/team-addon\/conversations\/([^/]+)\/messages$/,
      );
      if (request.method === "POST" && conversationMessagesMatch) {
        const payload = await requestJson(request);
        requests.push({ method: request.method, pathname, payload });
        const conversationId = decodeURIComponent(conversationMessagesMatch[1]);
        const conversation = state.conversations?.[conversationId];
        if (!conversation) {
          jsonResponse(response, { error: "conversation not found" }, 404);
          return;
        }
        state.revision += 1;
        const messageId = `chatmsg-${String(Object.keys(state.conversation_messages || {}).length + 1).padStart(3, "0")}`;
        const timestamp = new Date(Date.UTC(2026, 5, 9, 3, state.revision, 0)).toISOString();
        const message = {
          id: messageId,
          conversation_id: conversationId,
          sender: "operator",
          message_type: "text",
          body: String(payload.body || ""),
          status: "queued",
          dispatch_id: `team-chat:${messageId}`,
          voice_session_id: null,
          source_message_id: null,
          error: null,
          metadata: {
            sender_kind: "human_operator",
            operator_user_id: "admin",
            operator_display_name: "Admin",
            created_by: "admin",
          },
          created_at: timestamp,
          updated_at: timestamp,
        };
        state.conversation_messages[messageId] = message;
        conversation.last_message_preview = message.body;
        conversation.updated_at = timestamp;
        conversation.last_interaction_at = timestamp;
        for (const stream of openChatStreams) {
          if (stream.conversationId === conversationId) {
            writeChatStreamSnapshot(stream, state);
          }
        }
        if (chatMessageBeforeResponse) {
          await chatMessageBeforeResponse({ request, url, payload, state, message });
        }
        jsonResponse(response, {
          revision: state.revision,
          conversation: chatConversationPayload(state, conversation, {
            includeMessages: true,
            limit: 50,
          }),
          message,
          dispatch: {
            success: false,
            queued: true,
            dispatch_id: message.dispatch_id,
            message: "Chat message queued for asynchronous agent dispatch.",
          },
          published_event_ids: [`team-chat-message-queued:${messageId}`],
        }, 202);
        return;
      }
      if (request.method === "GET" && conversationMessagesMatch) {
        requests.push({ method: request.method, pathname, search: url.search });
        const conversationId = decodeURIComponent(conversationMessagesMatch[1]);
        if (!state.conversations?.[conversationId]) {
          jsonResponse(response, { error: "conversation not found" }, 404);
          return;
        }
        const page = chatMessagePage(state, conversationId, {
          before: queryText(url.searchParams, "before"),
          limit: queryText(url.searchParams, "limit") || 50,
        });
        jsonResponse(response, {
          revision: state.revision,
          conversation_id: conversationId,
          before_message_id: queryText(url.searchParams, "before"),
          messages_limit: Number.parseInt(queryText(url.searchParams, "limit") || "50", 10),
          ...page,
        });
        return;
      }
      const conversationStreamMatch = pathname.match(
        /^\/api\/addons\/team-addon\/conversations\/([^/]+)\/stream$/,
      );
      if (request.method === "GET" && conversationStreamMatch) {
        requests.push({ method: request.method, pathname, search: url.search });
        const conversationId = decodeURIComponent(conversationStreamMatch[1]);
        const conversation = state.conversations?.[conversationId];
        if (!conversation) {
          jsonResponse(response, { error: "conversation not found" }, 404);
          return;
        }
        response.writeHead(200, {
          "content-type": "text/event-stream",
          "cache-control": "no-cache",
          connection: "keep-alive",
        });
        const interval = setInterval(() => {
          response.write(": keepalive\n\n");
        }, 1000);
        const streamRecord = { response, interval, conversationId };
        openChatStreams.add(streamRecord);
        writeChatStreamSnapshot(streamRecord, state);
        request.on("close", () => {
          clearInterval(interval);
          openChatStreams.delete(streamRecord);
        });
        return;
      }

      if (request.method === "GET" && pathname === "/api/admin/agents") {
        requests.push({ method: request.method, pathname, search: url.search });
        jsonResponse(response, {
          items: coreAgents,
          directory_path: url.searchParams.get("directory_path") || "root",
          direct_only: true,
          backend: "all",
          summary_only: false,
          reconcile_containers: true,
          omit_vm_state: false,
        });
        return;
      }
      if (request.method === "GET" && pathname === "/api/admin/agents/create-options") {
        requests.push({ method: request.method, pathname, search: url.search });
        jsonResponse(response, {
          directory_path: url.searchParams.get("directory_path") || "root",
          backends: ["libvirt_vm", "container_worker_vm"],
          agent_providers: [
            { value: "codex", label: "Codex", execution_backends: ["libvirt_vm", "container_worker_vm"] },
            { value: "claude", label: "Claude", execution_backends: ["libvirt_vm"] },
          ],
          model_provider_modes: [
            { value: "openai", label: "OpenAI relay" },
            { value: "local_openai", label: "Local OpenAI" },
          ],
          communication_targets: [
            {
              value: "team-alerts",
              label: "team-alerts (teams, read, reply, write)",
              metadata: {
                provider: "teams",
                target_shape: "teams_channel",
                policy_capabilities: ["read", "reply", "write"],
              },
            },
          ],
          libvirt: {
            default_template: "default",
            templates: [{ alias: "default", image_ref: "/var/lib/libvirt/images/default.qcow2" }],
          },
          container_worker: {
            default_image: "runner",
            images: [{ alias: "runner", image_ref: "evander-runner:latest" }],
          },
        });
        return;
      }
      if (request.method === "POST" && ["/api/admin/agents/create-operations", "/api/admin/agents/copy-operations"].includes(pathname)) {
        const payload = await requestJson(request);
        requests.push({ method: request.method, pathname, payload });
        const operationId = `create-${coreCreateOperations.size + 1}`;
        const backend = payload.backend || "libvirt_vm";
        const targetName = payload.vm_name || payload.target_name || payload.session_id
          || (payload.source_session_id ? `copy-${payload.source_session_id}` : "agent-created");
        const sessionId = payload.session_id || `session-${targetName}`;
        const operation = {
          operation_id: operationId,
          status: "succeeded",
          backend,
          target_name: targetName,
          session_id: sessionId,
          directory_path: payload.directory_path || "root",
          owner_id: null,
          owner_pid: null,
          result: {
            success: true,
            agent_id: targetName,
            session_id: sessionId,
            message: `Created ${targetName}`,
          },
          error: null,
          created_at: "2026-06-09T00:00:00Z",
          started_at: "2026-06-09T00:00:00Z",
          finished_at: "2026-06-09T00:00:01Z",
          updated_at: "2026-06-09T00:00:01Z",
        };
        coreCreateOperations.set(operationId, operation);
        coreAgents.unshift({
          agent_id: targetName,
          backend,
          target_name: targetName,
          session_id: sessionId,
          directory_path: payload.directory_path || "root",
          status: "running",
          updated_at: "2026-06-09T00:00:01Z",
          capabilities: {},
          host: {},
        });
        jsonResponse(response, { operation }, 202);
        return;
      }
      const coreCreateOperationMatch = pathname.match(/^\/api\/admin\/agents\/create-operations\/([^/]+)$/);
      if (request.method === "GET" && coreCreateOperationMatch) {
        const operationId = decodeURIComponent(coreCreateOperationMatch[1]);
        requests.push({ method: request.method, pathname, search: url.search });
        const operation = coreCreateOperations.get(operationId);
        if (!operation) {
          jsonResponse(response, { error: "operation not found" }, 404);
          return;
        }
        jsonResponse(response, { operation });
        return;
      }

      if (!pathname.startsWith("/api/addons/team-addon/")) {
        jsonResponse(response, { error: `unexpected route ${pathname}` }, 404);
        return;
      }

      const payload = await requestJson(request);
      requests.push({ method: request.method, pathname, payload });

      const humanDecisionMatch = pathname.match(
        /^\/api\/addons\/team-addon\/human-review-requests\/([^/]+)\/decisions$/,
      );
      if (request.method === "POST" && humanDecisionMatch) {
        const humanRequestId = decodeURIComponent(humanDecisionMatch[1]);
        const humanRequest = state.human_review_requests?.[humanRequestId];
        if (!humanRequest) {
          jsonResponse(response, { error: "human review request not found" }, 404);
          return;
        }
        state.revision += 1;
        humanRequest.state = payload.decision;
        humanRequest.revision = Number(humanRequest.revision || 0) + 1;
        humanRequest.decisions = [
          ...(humanRequest.decisions || []),
          {
            id: `human-decision-${state.revision}`,
            human_review_request_id: humanRequestId,
            decision: payload.decision,
            reviewer_user_id: "admin",
            reviewer_display_name_snapshot: "Admin",
            summary: payload.summary || "",
            evidence_refs: payload.evidence_refs || [],
            checklist_results: payload.checklist_results || {},
            decided_at: "2026-06-09T16:10:00Z",
          },
        ];
        const release = state.release_packages?.[humanRequest.release_package_id];
        if (release?.human_review) {
          release.human_review.state = payload.decision;
          release.human_review.next_action = null;
        }
        if (release?.detail?.human_review) {
          release.detail.human_review.state = payload.decision;
          release.detail.human_review.next_action = null;
          const detailedRequest = release.detail.human_review.requests?.find(
            (item) => item.id === humanRequestId,
          );
          if (detailedRequest && detailedRequest !== humanRequest) {
            Object.assign(detailedRequest, humanRequest);
          }
        }
        jsonResponse(response, {
          revision: state.revision,
          result: { decision_id: `human-decision-${state.revision}` },
          human_review_request: humanRequest,
        });
        return;
      }

      const postReleaseReportMatch = pathname.match(
        /^\/api\/addons\/team-addon\/release-packages\/([^/]+)\/report-issue$/,
      );
      if (request.method === "POST" && postReleaseReportMatch) {
        const releaseId = decodeURIComponent(postReleaseReportMatch[1]);
        const release = state.release_packages?.[releaseId];
        if (!release || release.state !== "accepted") {
          jsonResponse(response, { error: "post-release feedback requires a published release" }, 400);
          return;
        }
        state.revision += 1;
        const caseId = `post-release-case-${state.revision}`;
        const triageTaskId = `post-release-triage-${state.revision}`;
        const caseRecord = {
          id: caseId,
          team_id: state.projects?.[release.project_id]?.team_id || "team-alpha",
          project_id: release.project_id,
          title: `Post-release feedback for ${releaseId}`,
          description: payload.report,
          status: "triage",
          priority: "high",
          owner_agent_id: state.projects?.[release.project_id]?.lead_agent_id || "alpha-lead",
          source: "post_release_feedback",
          source_ref: `${releaseId}:${payload.request_id}`,
          metadata: {
            reporter_user_id: "admin",
            reporter_display_name: "Admin",
            release_package_id: releaseId,
            evidence_refs: payload.evidence_refs || [],
          },
          created_at: "2026-06-09T16:20:00Z",
          updated_at: "2026-06-09T16:20:00Z",
        };
        const triageTask = {
          id: triageTaskId,
          owner_agent_id: caseRecord.owner_agent_id,
          project_id: release.project_id,
          priority: 0,
          state: "pending",
          title: `Triage post-release feedback for ${releaseId}`,
          description: payload.report,
          created_by_agent_id: caseRecord.owner_agent_id,
          case_id: caseId,
          created_at: "2026-06-09T16:20:00Z",
          updated_at: "2026-06-09T16:20:00Z",
        };
        state.cases ||= {};
        state.tasks ||= {};
        state.cases[caseId] = caseRecord;
        state.tasks[triageTaskId] = triageTask;
        jsonResponse(response, {
          revision: state.revision,
          result: {
            case_id: caseId,
            triage_task_id: triageTaskId,
          },
          case: caseRecord,
        });
        return;
      }

      if (
        conflictOnFirstTeamCreate &&
        pathname === "/api/addons/team-addon/teams" &&
        !conflictSent
      ) {
        conflictSent = true;
        state.revision = 1;
        state.teams.existing = {
          id: "existing",
          name: "Existing",
          manager_agent_id: "existing-manager",
          project_ids: [],
          archived: false,
        };
        putRole(state, {
          id: roleId("existing", "manager"),
          teamId: "existing",
          roleKey: "manager",
          name: "Manager",
        });
        state.agents["existing-manager"] = {
          id: "existing-manager",
          team_id: "existing",
          project_id: null,
          role_id: roleId("existing", "manager"),
          session_id: "session-existing-manager",
          reports_to_agent_id: null,
          subordinate_agent_ids: [],
          ephemeral_policy: { max_concurrent: 0 },
          status: "active",
        };
        jsonResponse(
          response,
          {
            error: "stale team add-on state revision: expected 0, current 1",
            expected_revision: 0,
            current_revision: 1,
          },
          409,
        );
        return;
      }

      if (
        conflictOnFirstGovernanceGroupUpdate &&
        request.method === "PATCH" &&
        /^\/api\/addons\/team-addon\/governance-groups\/[^/]+$/.test(pathname) &&
        !governanceGroupUpdateConflictSent
      ) {
        governanceGroupUpdateConflictSent = true;
        const previousRevision = payload.expected_revision;
        state.revision += 1;
        const groupId = decodeURIComponent(pathname.split("/").pop() || "");
        if (state.governance_groups[groupId]) {
          state.governance_groups[groupId].goals = [
            ...(state.governance_groups[groupId].goals || []),
            { id: "operator-conflict-goal", description: "Concurrent governance edit" },
          ];
        }
        jsonResponse(
          response,
          {
            error: `stale team add-on state revision: expected ${previousRevision}, current ${state.revision}`,
            expected_revision: previousRevision,
            current_revision: state.revision,
          },
          409,
        );
        return;
      }

      if (
        conflictOnFirstAgentRegistration &&
        request.method === "POST" &&
        pathname === "/api/addons/team-addon/agents" &&
        !agentRegistrationConflictSent
      ) {
        agentRegistrationConflictSent = true;
        const previousRevision = payload.expected_revision;
        state.revision += 1;
        jsonResponse(
          response,
          {
            error: `stale team add-on state revision: expected ${previousRevision}, current ${state.revision}`,
            expected_revision: previousRevision,
            current_revision: state.revision,
          },
          409,
        );
        return;
      }

      if (
        request.method === "POST"
        && /^\/api\/addons\/team-addon\/teams\/[^/]+\/copy$/.test(pathname)
        && payload.expected_revision !== state.revision
      ) {
        jsonResponse(
          response,
          {
            error: `stale team add-on state revision: expected ${payload.expected_revision}, current ${state.revision}`,
            expected_revision: payload.expected_revision,
            current_revision: state.revision,
          },
          409,
        );
        return;
      }

      applyMutation(state, pathname, payload);
      jsonResponse(response, state, request.method === "POST" ? 201 : 200);
    } catch (error) {
      jsonResponse(
        response,
        { error: error instanceof Error ? error.message : String(error) },
        500,
      );
    }
  });

  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  await t.after(async () => {
    for (const stream of openStateStreams) {
      clearInterval(stream.interval);
      stream.response.end();
      stream.response.destroy?.();
    }
    openStateStreams.clear();
    for (const stream of openChatStreams) {
      clearInterval(stream.interval);
      stream.response.end();
      stream.response.destroy?.();
    }
    openChatStreams.clear();
    await new Promise((resolve) => server.close(resolve));
  });
  const address = server.address();
  assert.ok(address && typeof address === "object" && "port" in address);
  const emitChatSnapshots = (conversationId = null) => {
    for (const stream of openChatStreams) {
      if (conversationId === null || stream.conversationId === conversationId) {
        writeChatStreamSnapshot(stream, state);
      }
    }
  };
  const emitStateSnapshot = (payload) => {
    const serialized = JSON.stringify(payload);
    for (const stream of openStateStreams) {
      stream.response.write(`event: snapshot\ndata: ${serialized}\n\n`);
    }
    return openStateStreams.size;
  };
  return {
    port: address.port,
    requests,
    viewRequests,
    state,
    timelineRequests,
    costRequests,
    costResponses,
    chatRequests,
    emitChatSnapshots,
    emitStateSnapshot,
    releaseVerificationLiveRequestCount: () => releaseVerificationLiveRequestCount,
    stateStreamCount: () => openStateStreams.size,
  };
}

function materializationDuplicateError(state, payload, template) {
  const ids = resolveTemplateMaterializationIds(state, payload, template);
  const duplicates = [];
  if (payload?.team?.id && state.teams[ids.teamId]) duplicates.push(ids.teamId);
  if (payload?.project?.id && state.projects[ids.projectId]) duplicates.push(ids.projectId);
  for (const [roleKey, roleInput] of Object.entries(payload?.agents || {})) {
    const agentId = roleInput?.agent_id;
    if (agentId && state.agents[ids.agentIds[roleKey]]) duplicates.push(ids.agentIds[roleKey]);
  }
  return duplicates.length
    ? `duplicate ids: ${Array.from(new Set(duplicates)).join(", ")}`
    : null;
}

function materializedTemplateRecordId(projectId, templateRecordId) {
  return `${projectId}-${templateRecordId}`;
}

function resolveTemplateMaterializationIds(state, payload, template) {
  const team = payload.team || {};
  const project = payload.project || {};
  const agents = payload.agents || {};
  const teamId = team.id || generatedId("team", [team.name], state.teams);
  const projectId = project.id || generatedId("project", [teamId, project.name], state.projects);

  const roleIds = {};
  const usedRoleIds = existingIdSet(state.roles);
  for (const role of template.roles) {
    const scopeId = role.key === template.root_role_key ? teamId : projectId;
    const roleIdentifier = uniqueIdFromBase(roleId(scopeId, role.key), usedRoleIds);
    usedRoleIds.add(roleIdentifier);
    roleIds[role.key] = roleIdentifier;
  }

  const agentIds = {};
  const usedAgentIds = existingIdSet(state.agents);
  for (const role of template.roles) {
    const roleInput = agents[role.key] || {};
    const scopeId = role.key === template.root_role_key ? teamId : projectId;
    const agentIdentifier =
      roleInput.agent_id || generatedId("agent", [scopeId, role.label], usedAgentIds);
    usedAgentIds.add(agentIdentifier);
    agentIds[role.key] = agentIdentifier;
  }

  const reviewProfileIds = template.review_profiles.map((profile) =>
    uniqueIdFromBase(materializedTemplateRecordId(projectId, profile.id), state.review_profiles),
  );
  const taskIds = {};
  const usedTaskIds = existingIdSet(state.tasks);
  for (const task of template.tasks) {
    const taskId = uniqueIdFromBase(materializedTemplateRecordId(projectId, task.id), usedTaskIds);
    usedTaskIds.add(taskId);
    taskIds[task.id] = taskId;
  }

  return { teamId, projectId, roleIds, agentIds, reviewProfileIds, taskIds };
}

function applyTemplateMaterialization(state, payload, template) {
  const team = payload.team || {};
  const project = payload.project || {};
  const ids = resolveTemplateMaterializationIds(state, payload, template);
  const manager = ids.agentIds.manager;
  const projectLead = ids.agentIds.project_lead;
  const implementer = ids.agentIds.implementer;
  const reviewer = ids.agentIds.reviewer;
  const releaseOperator = ids.agentIds.release_operator;

  state.revision += 1;
  for (const role of template.roles) {
    putRole(state, {
      id: ids.roleIds[role.key],
      teamId: ids.teamId,
      projectId: role.key === template.root_role_key ? null : ids.projectId,
      roleKey: role.key,
      name: role.label,
      description: role.description,
      permissions: role.permissions || [],
      ephemeralPolicy: role.ephemeral_policy,
    });
  }
  state.teams[ids.teamId] = {
    id: ids.teamId,
    name: team.name,
    manager_agent_id: manager,
    operator_communication_target_id: team.operator_communication_target_id ?? null,
    project_ids: [ids.projectId],
    archived: false,
  };
  state.agents[manager] = {
    id: manager,
    team_id: ids.teamId,
    project_id: null,
    role_id: ids.roleIds.manager,
    session_id: team.manager_session_id,
    reports_to_agent_id: null,
    subordinate_agent_ids: [projectLead],
    ephemeral_policy: { max_concurrent: 0 },
    status: "active",
  };
  state.projects[ids.projectId] = {
    id: ids.projectId,
    team_id: ids.teamId,
    name: project.name,
    lead_agent_id: projectLead,
    goals: template.project_goals.map((goal) => ({
      ...goal,
      id: materializedTemplateRecordId(ids.projectId, goal.id),
    })),
    git: project.git || null,
    status: "active",
  };
  state.agents[projectLead] = {
    id: projectLead,
    team_id: ids.teamId,
    project_id: ids.projectId,
    role_id: ids.roleIds.project_lead,
    session_id: project.lead_session_id,
    reports_to_agent_id: manager,
    subordinate_agent_ids: [implementer, reviewer, releaseOperator],
    ephemeral_policy: { max_concurrent: 1 },
    status: "active",
  };
  state.agents[implementer] = {
    id: implementer,
    team_id: ids.teamId,
    project_id: ids.projectId,
    role_id: ids.roleIds.implementer,
    session_id: payload.agents?.implementer?.session_id,
    reports_to_agent_id: projectLead,
    subordinate_agent_ids: [],
    ephemeral_policy: { max_concurrent: 2 },
    status: "active",
  };
  state.agents[reviewer] = {
    id: reviewer,
    team_id: ids.teamId,
    project_id: ids.projectId,
    role_id: ids.roleIds.reviewer,
    session_id: payload.agents?.reviewer?.session_id,
    reports_to_agent_id: projectLead,
    subordinate_agent_ids: [],
    ephemeral_policy: { max_concurrent: 1 },
    status: "active",
  };
  state.agents[releaseOperator] = {
    id: releaseOperator,
    team_id: ids.teamId,
    project_id: ids.projectId,
    role_id: ids.roleIds.release_operator,
    session_id: payload.agents?.release_operator?.session_id,
    reports_to_agent_id: projectLead,
    subordinate_agent_ids: [],
    ephemeral_policy: { max_concurrent: 1 },
    status: "active",
  };
  const reviewProfileId = ids.reviewProfileIds[0];
  const implementTaskId = ids.taskIds["implement-scoped-slice"];
  const releaseTaskId = ids.taskIds["release-handoff"];
  state.review_profiles[reviewProfileId] = {
    id: reviewProfileId,
    team_id: ids.teamId,
    project_id: ids.projectId,
    name: "Auto-Dev Delivery Review",
    description:
      "Checks that the slice stays in scope, automated tests exist, risk is understood, and the release handoff is ready.",
    applicable_role_ids: [ids.roleIds.reviewer],
    criteria: template.review_profiles[0].criteria.map((criterion) => ({ ...criterion })),
    archived: false,
    created_at: "2026-06-09T00:00:00Z",
    updated_at: "2026-06-09T00:00:00Z",
  };
  state.tasks[implementTaskId] = {
    id: implementTaskId,
    owner_agent_id: implementer,
    project_id: ids.projectId,
    priority: 10,
    state: "pending",
    title: "Implement the scoped slice",
    description:
      "Build the bounded change, add tests, and keep the implementation narrow enough for review.",
    created_by_agent_id: projectLead,
    parent_task_id: null,
    dependency_task_ids: [],
    deliverable_ids: [],
    created_at: "2026-06-09T00:00:00Z",
    updated_at: "2026-06-09T00:00:00Z",
  };
  state.tasks[releaseTaskId] = {
    id: releaseTaskId,
    owner_agent_id: releaseOperator,
    project_id: ids.projectId,
    priority: 20,
    state: "pending",
    title: "Prepare release handoff",
    description:
      "Package release notes, verify artifacts, and hand off the reviewed result.",
    created_by_agent_id: projectLead,
    parent_task_id: null,
    dependency_task_ids: [implementTaskId],
    deliverable_ids: [],
    created_at: "2026-06-09T00:00:00Z",
    updated_at: "2026-06-09T00:00:00Z",
  };

  return {
    success: true,
    template_id: template.id,
    template_name: template.name,
    team_id: ids.teamId,
    project_id: ids.projectId,
    agent_ids: ids.agentIds,
    role_ids: ids.roleIds,
    review_profile_ids: [reviewProfileId],
    task_ids: [implementTaskId, releaseTaskId],
    revision: state.revision,
  };
}

function applyTeamCopyMutation(state, sourceTeamId, payload) {
  const sourceTeam = state.teams[sourceTeamId];
  const destinationTeamId = payload.id || generatedId("team", [payload.name], state.teams);
  const sourceProjects = Object.values(state.projects).filter(
    (project) => project.team_id === sourceTeamId && ["active", "blocked"].includes(project.status),
  );
  const sourceProjectIds = new Set(sourceProjects.map((project) => project.id));
  const sourceRoles = Object.values(state.roles).filter(
    (role) => role.team_id === sourceTeamId
      && !role.archived
      && (!role.project_id || sourceProjectIds.has(role.project_id)),
  );
  const sourceRoleIds = new Set(sourceRoles.map((role) => role.id));
  const sourceAgents = Object.values(state.agents).filter(
    (agent) => agent.team_id === sourceTeamId
      && agent.status !== "archived"
      && sourceRoleIds.has(agent.role_id),
  );
  const projectIds = Object.fromEntries(sourceProjects.map((project) => [
    project.id,
    generatedId("project", [destinationTeamId, project.name], state.projects),
  ]));
  const roleIds = Object.fromEntries(sourceRoles.map((role) => [
    role.id,
    generatedId("role", [projectIds[role.project_id] || destinationTeamId, role.name], state.roles),
  ]));
  const agentIds = Object.fromEntries(sourceAgents.map((agent) => [
    agent.id,
    generatedId("agent", [destinationTeamId, agent.display_name || agent.id], state.agents),
  ]));
  const profileIds = Object.fromEntries(
    Object.values(state.review_profiles).filter(
      (profile) => profile.team_id === sourceTeamId
        && !profile.archived
        && (!profile.project_id || sourceProjectIds.has(profile.project_id)),
    ).map((profile) => [
      profile.id,
      generatedId("profile", [projectIds[profile.project_id] || destinationTeamId, profile.name], state.review_profiles),
    ]),
  );

  sourceRoles.forEach((role) => {
    state.roles[roleIds[role.id]] = {
      ...JSON.parse(JSON.stringify(role)),
      id: roleIds[role.id],
      team_id: destinationTeamId,
      project_id: projectIds[role.project_id] || null,
      archived: false,
    };
  });
  sourceAgents.forEach((agent) => {
    state.agents[agentIds[agent.id]] = {
      ...JSON.parse(JSON.stringify(agent)),
      id: agentIds[agent.id],
      team_id: destinationTeamId,
      role_id: roleIds[agent.role_id],
      session_id: payload.agent_session_ids[agent.id],
      reports_to_agent_id: agentIds[agent.reports_to_agent_id] || null,
      subordinate_agent_ids: (agent.subordinate_agent_ids || [])
        .filter((agentId) => agentIds[agentId])
        .map((agentId) => agentIds[agentId]),
      status: "active",
    };
  });
  Object.entries(profileIds).forEach(([sourceProfileId, destinationProfileId]) => {
    const profile = state.review_profiles[sourceProfileId];
    state.review_profiles[destinationProfileId] = {
      ...JSON.parse(JSON.stringify(profile)),
      id: destinationProfileId,
      team_id: destinationTeamId,
      project_id: projectIds[profile.project_id] || null,
      applicable_role_ids: (profile.applicable_role_ids || []).map((roleId) => roleIds[roleId]),
      archived: false,
    };
  });
  sourceProjects.forEach((project) => {
    state.projects[projectIds[project.id]] = {
      ...JSON.parse(JSON.stringify(project)),
      id: projectIds[project.id],
      team_id: destinationTeamId,
      lead_agent_id: agentIds[project.lead_agent_id],
      goals: (project.goals || []).map((goal) => ({
        ...goal,
        id: generatedId("goal", [projectIds[project.id], goal.description], new Set()),
        state: "pending",
      })),
      status: "active",
    };
  });
  state.teams[destinationTeamId] = {
    ...JSON.parse(JSON.stringify(sourceTeam)),
    id: destinationTeamId,
    name: payload.name,
    manager_agent_id: agentIds[sourceTeam.manager_agent_id],
    project_ids: sourceTeam.project_ids.filter((projectId) => projectIds[projectId]).map(
      (projectId) => projectIds[projectId],
    ),
    cost_budget_windows: [],
    escalate_to_governance: false,
    archived: false,
  };
}

function applyMutation(state, pathname, payload) {
  state.revision += 1;
  if (pathname === "/api/addons/team-addon/operator-directives") {
    const team = state.teams[payload.team_id];
    const managerId = team?.manager_agent_id;
    if (!team || !managerId) {
      return;
    }
    const directiveId = `operator-directive-${state.revision}`;
    state.inbox[directiveId] = {
      id: directiveId,
      recipient_agent_id: managerId,
      sender_agent_id: null,
      task_id: null,
      item_type: "operator_directive",
      response_requested: true,
      priority: "normal",
      state: "pending",
      payload: {
        title: payload.title || "Operator directive",
        message: payload.message,
        team_id: payload.team_id,
        ...(payload.project_id ? { project_id: payload.project_id } : {}),
        ...(payload.source ? { source: payload.source } : {}),
      },
      created_at: "2026-06-09T16:10:00Z",
      updated_at: "2026-06-09T16:10:00Z",
    };
    return;
  }
  const releaseGateRetryMatch = pathname.match(
    /^\/api\/addons\/team-addon\/release-packages\/(.+)\/retry-gate$/,
  );
  if (releaseGateRetryMatch) {
    const releaseId = decodeURIComponent(releaseGateRetryMatch[1]);
    const release = state.release_packages[releaseId];
    if (release) {
      release.state = "pending";
      release.error = null;
      release.gate_failure = null;
      release.updated_at = "2026-06-09T16:10:00Z";
    }
    return;
  }
  const decisionMatch = pathname.match(
    /^\/api\/addons\/team-addon\/review-requests\/(.+)\/decision$/,
  );
  if (decisionMatch) {
    const reviewId = decodeURIComponent(decisionMatch[1]);
    const review = state.review_requests[reviewId];
    if (review) {
      review.state = payload.state;
      review.findings = payload.findings || [];
      review.criteria = payload.criteria || [];
      review.decision_at = "2026-06-09T00:00:00Z";
    }
    return;
  }
  if (pathname === "/api/addons/team-addon/teams") {
    const teamId = payload.id || generatedId("team", [payload.name], state.teams);
    const managerRoleId =
      payload.manager_role_id || uniqueIdFromBase(roleId(teamId, "manager"), state.roles);
    const managerAgentId =
      payload.manager_agent_id ||
      generatedId(
        "agent",
        [teamId, payload.manager_display_name || payload.manager_role_name || "Manager"],
        state.agents,
      );
    putRole(state, {
      id: managerRoleId,
      teamId,
      roleKey: "manager",
      name: payload.manager_role_name || "Manager",
    });
    state.teams[teamId] = {
      id: teamId,
      name: payload.name,
      manager_agent_id: managerAgentId,
      operator_communication_target_id: payload.operator_communication_target_id ?? null,
      project_ids: [],
      archived: false,
    };
    state.agents[managerAgentId] = {
      id: managerAgentId,
      team_id: teamId,
      project_id: null,
      display_name: payload.manager_display_name ?? null,
      role_id: managerRoleId,
      session_id: payload.manager_session_id,
      reports_to_agent_id: null,
      subordinate_agent_ids: [],
      ephemeral_policy: { max_concurrent: 0 },
      status: "active",
    };
    return;
  }
  const teamCopyMatch = pathname.match(/^\/api\/addons\/team-addon\/teams\/([^/]+)\/copy$/);
  if (teamCopyMatch) {
    applyTeamCopyMutation(state, decodeURIComponent(teamCopyMatch[1]), payload);
    return;
  }
  if (pathname === "/api/addons/team-addon/governance-groups") {
    const groupId =
      payload.id || generatedId("governance-group", [payload.name], state.governance_groups);
    state.governance_groups[groupId] = {
      id: groupId,
      name: payload.name,
      manager_agent_id: payload.manager_agent_id,
      member_team_ids: payload.member_team_ids || [],
      goals: payload.goals || [],
      max_cost_usd: payload.max_cost_usd ?? null,
      archived: false,
      created_at: "2026-06-09T00:00:00Z",
      updated_at: "2026-06-09T00:00:00Z",
    };
    return;
  }
  const governanceGroupUpdateMatch = pathname.match(
    /^\/api\/addons\/team-addon\/governance-groups\/(.+)$/,
  );
  if (governanceGroupUpdateMatch) {
    const groupId = decodeURIComponent(governanceGroupUpdateMatch[1]);
    const group = state.governance_groups[groupId];
    if (group) {
      for (const key of [
        "name",
        "manager_agent_id",
        "member_team_ids",
        "goals",
        "max_cost_usd",
        "archived",
      ]) {
        if (Object.hasOwn(payload, key)) {
          group[key] = payload[key];
        }
      }
    }
    return;
  }
  if (pathname === "/api/addons/team-addon/projects") {
    const projectId =
      payload.id || generatedId("project", [payload.team_id, payload.name], state.projects);
    const leadRoleId =
      payload.lead_role_id || uniqueIdFromBase(roleId(projectId, "project_lead"), state.roles);
    const leadAgentId =
      payload.lead_agent_id ||
      generatedId(
        "agent",
        [projectId, payload.lead_display_name || payload.lead_role_name || "Project Lead"],
        state.agents,
      );
    const usedGoalIds = new Set();
    const goals = (payload.goals || []).map((goal) => {
      const goalId =
        goal.id || generatedId("goal", [projectId, goal.description], usedGoalIds);
      usedGoalIds.add(goalId);
      return { ...goal, id: goalId };
    });
    putRole(state, {
      id: leadRoleId,
      teamId: payload.team_id,
      projectId,
      roleKey: "project_lead",
      name: payload.lead_role_name || "Project Lead",
      ephemeralPolicy: { max_concurrent: 0 },
    });
    state.projects[projectId] = {
      id: projectId,
      team_id: payload.team_id,
      name: payload.name,
      lead_agent_id: leadAgentId,
      goals,
      git: payload.git || null,
      status: "active",
    };
    state.teams[payload.team_id].project_ids.push(projectId);
    const managerId = state.teams[payload.team_id].manager_agent_id;
    const existingLead = state.agents[leadAgentId];
    if (!existingLead) {
      state.agents[leadAgentId] = {
        id: leadAgentId,
        team_id: payload.team_id,
        project_id: projectId,
        display_name: payload.lead_display_name ?? null,
        role_id: leadRoleId,
        session_id: payload.lead_session_id,
        reports_to_agent_id: managerId,
        subordinate_agent_ids: [],
        ephemeral_policy: { max_concurrent: 0 },
        status: "active",
      };
      const manager = state.agents[managerId];
      if (!manager.subordinate_agent_ids.includes(leadAgentId)) {
        manager.subordinate_agent_ids.push(leadAgentId);
      }
    }
    return;
  }
  if (pathname === "/api/addons/team-addon/roles") {
    const roleIdentifier =
      payload.id ||
      payload.role_id ||
      generatedId("role", [payload.project_id || payload.team_id, payload.name], state.roles);
    putRole(state, {
      id: roleIdentifier,
      teamId: payload.team_id,
      projectId: payload.project_id || null,
      roleKey: payload.name || roleIdentifier,
      name: payload.name,
      description: payload.description || "",
      permissions: payload.permissions || [],
      ephemeralPolicy: payload.ephemeral_policy || { max_concurrent: 0 },
    });
    return;
  }
  if (pathname === "/api/addons/team-addon/agents") {
    const role = state.roles[payload.role_id];
    const agentId =
      payload.id ||
      generatedId(
        "agent",
        [payload.project_id, payload.display_name || role?.name || payload.session_id],
        state.agents,
      );
    state.agents[agentId] = {
      id: agentId,
      team_id: payload.team_id,
      project_id: payload.project_id,
      display_name: payload.display_name ?? null,
      role_id: payload.role_id,
      session_id: payload.session_id,
      reports_to_agent_id: payload.reports_to_agent_id,
      subordinate_agent_ids: [],
      ephemeral_policy:
        payload.ephemeral_policy ||
        state.roles[payload.role_id]?.ephemeral_policy || { max_concurrent: 0 },
      status: "active",
    };
    state.agents[payload.reports_to_agent_id].subordinate_agent_ids.push(agentId);
    return;
  }
  if (pathname === "/api/addons/team-addon/tasks") {
    const taskId =
      payload.id || generatedId("task", [payload.project_id, payload.title], state.tasks);
    state.tasks[taskId] = {
      id: taskId,
      owner_agent_id: payload.owner_agent_id,
      project_id: payload.project_id,
      priority: payload.priority,
      state: payload.state || "pending",
      title: payload.title,
      description: payload.description,
      created_by_agent_id: payload.created_by_agent_id || null,
      created_by_actor: payload.created_by_agent_id ? null : "operator:admin",
      deliverable_ids: [],
      created_at: "2026-06-09T00:00:00Z",
      updated_at: "2026-06-09T00:00:00Z",
    };
    return;
  }
  const taskCancellationMatch = pathname.match(
    /^\/api\/addons\/team-addon\/tasks\/([^/]+)\/cancel$/,
  );
  if (taskCancellationMatch) {
    const taskId = decodeURIComponent(taskCancellationMatch[1]);
    const task = state.tasks[taskId];
    if (task) {
      task.state = "cancelled";
      task.state_entered_at = "2026-07-27T12:00:00Z";
      task.updated_at = "2026-07-27T12:00:00Z";
      task.cancellation = {
        reason_code: payload.reason_code,
        reason: payload.reason,
        successor_task_id: payload.successor_task_id || null,
        actor_kind: "operator",
        actor_id: "operator:admin",
        cancelled_at: "2026-07-27T12:00:00Z",
      };
      task.readiness = "resolved";
      task.resolution_status = "resolved";
      task.assessment = {
        ...(task.assessment || {}),
        readiness: "resolved",
        resolution_status: "resolved",
        resolution_reason: "The task was explicitly cancelled.",
        unresolved_descendant_ids: [],
        active_acceptance_ids: [],
        active_review_request_ids: [],
      };
    }
    return;
  }
  const taskUpdateMatch = pathname.match(/^\/api\/addons\/team-addon\/tasks\/(.+)$/);
  if (taskUpdateMatch) {
    const taskId = decodeURIComponent(taskUpdateMatch[1]);
    Object.assign(state.tasks[taskId], payload);
    return;
  }
  if (pathname === "/api/addons/team-addon/review-profiles") {
    const profileId =
      payload.id ||
      generatedId("profile", [payload.project_id || payload.team_id, payload.name], state.review_profiles);
    state.review_profiles[profileId] = {
      id: profileId,
      team_id: payload.team_id,
      project_id: payload.project_id || null,
      name: payload.name,
      description: payload.description || "",
      applicable_role_ids: payload.applicable_role_ids || [],
      criteria: payload.criteria || [],
      archived: false,
      created_at: "2026-06-09T00:00:00Z",
      updated_at: "2026-06-09T00:00:00Z",
    };
    return;
  }
  if (pathname === "/api/addons/team-addon/deliverables") {
    const deliverableId =
      payload.id ||
      generatedId("deliverable", [payload.task_id, payload.summary], state.deliverables);
    state.deliverables[deliverableId] = {
      id: deliverableId,
      task_id: payload.task_id,
      producer_agent_id: payload.producer_agent_id,
      artifact_refs: payload.artifact_refs || [],
      summary: payload.summary,
      state: payload.state || "pending",
      review_request_ids: [],
    };
    state.tasks[payload.task_id].deliverable_ids.push(deliverableId);
    return;
  }
  if (pathname === "/api/addons/team-addon/review-requests") {
    const reviewRequestId =
      payload.id ||
      generatedId(
        "review",
        [payload.deliverable_id, payload.profile_id || payload.reviewer_agent_id],
        state.review_requests,
      );
    state.review_requests[reviewRequestId] = {
      id: reviewRequestId,
      deliverable_id: payload.deliverable_id,
      reviewer_agent_id: payload.reviewer_agent_id,
      state: payload.state || "pending",
      findings: payload.findings || [],
      decision_at: null,
    };
    state.deliverables[payload.deliverable_id].review_request_ids.push(reviewRequestId);
  }
}

async function openBrowserPage(
  t,
  port,
  contextOptions = {},
  path = "/addons/team-addon/?privileged_unlocked=true",
) {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext(contextOptions);
  const page = await context.newPage();
  await t.after(async () => {
    await page.close().catch(() => {});
    await context.close().catch(() => {});
    await browser.close().catch(() => {});
  });
  await page.goto(`http://127.0.0.1:${port}${path}`, {
    waitUntil: "domcontentloaded",
  });
  return page;
}

async function openCaseBoardAdminShell(t, fixturePort, { hash = "#addon:team-addon", authMode = "none" } = {}) {
  const target = `http://127.0.0.1:${fixturePort}`;
  const server = await createViteServer({
    root: path.resolve(fileURLToPath(new URL("..", import.meta.url))),
    server: { host: "127.0.0.1", port: 0, proxy: { "/addons/": target, "/api/addons/": target } },
    logLevel: "error",
  });
  await server.listen();
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  page.setDefaultTimeout(10000);
  t.after(async () => {
    await browser.close();
    await server.close();
  });
  let authenticated = authMode === "none";
  const loginTargets = [];
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  const session = () => ({
    auth_mode: authMode, auth_required: authMode !== "none", authenticated,
    username: authenticated ? "operator" : null, display_name: "Test Operator",
    permissions: ["admin.owner"], effective_permissions: ["admin.owner"],
    can_manage_users: true, session_id: authenticated ? "test-session" : null,
    available_login_methods: [authMode], azure_sso_login_url: "/api/auth/azure-sso/start",
    privileged_operations: { unlocked: false },
  });
  await page.route("**/api/**", async route => {
    const url = new URL(route.request().url());
    const { pathname } = url;
    const json = (value) => route.fulfill({ contentType: "application/json", body: JSON.stringify(value) });
    if (pathname.startsWith("/api/addons/")) return route.continue();
    if (pathname === "/api/auth/session") return json(session());
    if (pathname === "/api/auth/login") {
      authenticated = true;
      return json(session());
    }
    if (pathname === "/api/auth/azure-sso/start") {
      const next = url.searchParams.get("next");
      loginTargets.push(next);
      authenticated = true;
      // Simulate the identity-provider round trip; backend callback coverage checks
      // that the signed pending-login cookie preserves this same target.
      return route.fulfill({ status: 303, headers: { location: next || "/" }, body: "" });
    }
    if (await fulfillAdminShellBackgroundApiRequest(route, pathname)) return;
    if (pathname === "/api/admin/state") return json({
      selected_directory_path: "root", directory_scoped_config_enabled: false,
      execution_backend: "libvirt_vm", permissions: ["admin.owner"], effective_permissions: ["admin.owner"],
      privileged_operations: { unlocked: false }, assistant: { enabled: false, show_launcher_when_disabled: false },
      addons: [{ addon_id: "team-addon", tab_key: "addon:team-addon", label: "Teams",
        ui_path: "/addons/team-addon/", api_base_path: "/api/addons/team-addon" }],
    });
    if (pathname === "/api/configuration/state") return json({
      selected_directory_path: "root", selected_directory: { parent_path: null },
      directory_scoped_config_enabled: false, execution_backend: "libvirt_vm", tree: null,
    });
    return json({});
  });
  await page.goto(`http://127.0.0.1:${server.httpServer.address().port}/${hash}`, { waitUntil: "domcontentloaded" });
  return { page, board: page.frameLocator('iframe[title="Teams"]'), loginTargets, errors };
}

function caseBoardState() {
  const state = seededGitState();
  state.cases = { "case A&42": { id: "case A&42", team_id: "team-alpha", project_id: "alpha-project",
    title: "Case progress regression", status: "open", task_ids: ["task-planned"] } };
  state.tasks["task-planned"].case_id = "case A&42";
  state.projects["alpha-other"] = { ...state.projects["alpha-project"], id: "alpha-other", name: "Other Alpha Project" };
  return state;
}

test("case board updates Team and project selection in place and preserves it across a shell reload", { timeout: 45000 }, async (t) => {
  const { port } = await createTeamAddonServer(t, { initialState: caseBoardState() });
  const { page, board, errors } = await openCaseBoardAdminShell(t, port);
  async function afterBoardSelection(action, settled) {
    const previousDocument = await board.locator("body").evaluate(() => performance.timeOrigin);
    await action();
    await settled();
    await page.waitForTimeout(250);
    assert.equal(
      await board.locator("body").evaluate(() => performance.timeOrigin),
      previousDocument,
      "changing the task-board selection must not recreate the embedded Teams dashboard",
    );
  }
  await board.getByRole("tab", { name: "Task board", exact: true }).click();
  await afterBoardSelection(
    () => board.locator("[data-task-board-team]").selectOption("team-alpha"),
    () => board.locator('[data-task-board-project] option[value="alpha-project"]').waitFor({ state: "attached" }),
  );
  assert.equal(await board.locator("[data-task-board-team]").inputValue(), "team-alpha");
  await afterBoardSelection(
    () => board.locator("[data-task-board-project]").selectOption("alpha-project"),
    () => board.locator(".task-board-context strong", { hasText: "Alpha Project" }).waitFor(),
  );
  await afterBoardSelection(
    () => board.locator('[data-task-board-filter="case"]').selectOption("case A&42"),
    () => board.getByRole("button", { name: "Clear case filter", exact: true }).waitFor(),
  );
  await afterBoardSelection(
    () => board.getByRole("button", { name: "Clear case filter", exact: true }).click(),
    () => board.locator(".task-board-context strong", { hasText: "Alpha Project" }).waitFor(),
  );
  await board.locator(".task-board-context strong", { hasText: "Alpha Project" }).waitFor();
  assert.equal(await board.locator("[data-task-board-team]").inputValue(), "team-alpha");
  assert.equal(await board.locator("[data-task-board-project]").inputValue(), "alpha-project");
  await page.reload({ waitUntil: "domcontentloaded" });
  await board.locator('[data-task-board-filter="case"]').waitFor();
  assert.equal(await board.locator("[data-task-board-project]").inputValue(), "alpha-project");
  await afterBoardSelection(
    () => board.locator('[data-task-board-filter="case"]').selectOption("case A&42"),
    () => board.getByRole("button", { name: "Clear case filter", exact: true }).waitFor(),
  );
  await board.getByRole("button", { name: "Clear case filter", exact: true }).waitFor();
  await afterBoardSelection(
    () => board.locator("[data-task-board-project]").selectOption("alpha-other"),
    () => board.locator(".task-board-context strong", { hasText: "Other Alpha Project" }).waitFor(),
  );
  assert.equal(await board.locator("[data-task-board-project]").inputValue(), "alpha-other");
  await afterBoardSelection(
    () => board.locator("[data-task-board-team]").selectOption("team-beta"),
    () => board.locator('[data-task-board-project] option[value="beta-project"]').waitFor({ state: "attached" }),
  );
  assert.equal(await board.locator("[data-task-board-team]").inputValue(), "team-beta");
  assert.deepEqual(errors, []);
});

test("case board loads from its selected server scope without requesting costs", { timeout: 45000 }, async (t) => {
  let releaseCosts = () => {};
  const costsGate = new Promise((resolve) => {
    releaseCosts = resolve;
  });
  t.after(() => {
    releaseCosts();
  });
  const {
    port,
    viewRequests,
    costRequests,
  } = await createTeamAddonServer(t, {
    initialState: caseBoardState(),
    costsBeforeResponse: costsGate,
  });
  const hash = "#addon:team-addon?view=task-board&case_id=case+A%2642";
  const { board, errors } = await openCaseBoardAdminShell(t, port, { hash });

  await board.locator(".task-board-context strong", { hasText: "Case progress regression" }).waitFor();
  const taskBoardRequest = viewRequests.find((request) => request.view === "task-board");
  assert.ok(taskBoardRequest);
  const params = new URLSearchParams(taskBoardRequest.search);
  assert.equal(params.get("scope"), "selection");
  assert.equal(params.get("case_id"), "case A&42");
  assert.deepEqual(costRequests, []);
  assert.deepEqual(errors, []);
  releaseCosts();
});

for (const authMode of ["local_password", "azure_sso"]) {
  test(`case board permalink survives ${authMode} sign-in in the Admin shell`, { timeout: 45000 }, async (t) => {
    const { port } = await createTeamAddonServer(t, { initialState: caseBoardState() });
    const hash = "#addon:team-addon?view=task-board&case_id=case+A%2642";
    const { page, board, loginTargets, errors } = await openCaseBoardAdminShell(t, port, { hash, authMode });
    await page.getByRole("heading", { name: "Admin Sign In", exact: true }).waitFor();
    if (authMode === "local_password") {
      await page.getByRole("textbox", { name: "Username", exact: true }).fill("operator");
      await page.getByLabel("Password", { exact: true }).fill("test-only-password");
      await page.getByRole("button", { name: "Sign in", exact: true }).click();
    } else {
      await page.getByRole("button", { name: "Sign in with Microsoft", exact: true }).click();
    }
    await board.locator(".task-board-context strong", { hasText: "Case progress regression" }).waitFor();
    assert.equal(await board.locator("[data-task-board-team]").inputValue(), "team-alpha");
    assert.equal(await board.locator("[data-task-board-project]").inputValue(), "alpha-project");
    assert.equal(await board.locator('[data-task-board-filter="case"]').inputValue(), "case A&42");
    assert.equal(new URL(page.url()).hash, hash);
    if (authMode === "azure_sso") assert.deepEqual(loginTargets, [`/${hash}`]);
    assert.deepEqual(errors, []);
  });
}

async function waitForRevision(page, revision) {
  await page.locator("#revision-badge", { hasText: `Revision ${revision}` }).waitFor({
    state: "visible",
  });
}

async function waitForSelectOption(page, selector, value) {
  await page.locator(`${selector} option[value="${value}"]`).waitFor({ state: "attached" });
}

async function orgChartViewportTransform(page) {
  const viewport = page.locator("#team-org-page .react-flow__viewport");
  await viewport.waitFor({ state: "visible" });
  return viewport.evaluate((element) => getComputedStyle(element).transform);
}

async function zoomOrgChartCanvas(page) {
  const canvas = page.locator("#team-org-page [data-team-org-flow-canvas]");
  const box = await canvas.boundingBox();
  assert.ok(box, "expected org chart canvas bounds");
  const beforeTransform = await orgChartViewportTransform(page);
  await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2);
  await page.mouse.wheel(0, -600);
  await page.waitForFunction(
    (expectedTransform) => {
      const viewport = document.querySelector("#team-org-page .react-flow__viewport");
      return Boolean(viewport) && getComputedStyle(viewport).transform !== expectedTransform;
    },
    beforeTransform,
  );
  return orgChartViewportTransform(page);
}

async function rightClickBareOrgCanvas(page) {
  const point = await page.locator("#team-org-page [data-team-org-flow-canvas]").evaluate((canvas) => {
    const rect = canvas.getBoundingClientRect();
    const left = Math.max(rect.left + 12, 0);
    const right = Math.min(rect.right - 12, window.innerWidth - 1);
    const top = Math.max(rect.top + 12, 0);
    const bottom = Math.min(rect.bottom - 12, window.innerHeight - 1);
    const isBareCanvasPoint = (x, y) => {
      const target = document.elementFromPoint(x, y);
      return (
        target &&
        canvas.contains(target) &&
        !target.closest(".react-flow__node, .react-flow__controls, .react-flow__panel")
      );
    };
    const candidates = [
      [0.88, 0.88],
      [0.5, 0.88],
      [0.88, 0.5],
      [0.5, 0.5],
      [0.12, 0.88],
      [0.88, 0.12],
      [0.12, 0.5],
      [0.5, 0.12],
    ];
    for (const [xFraction, yFraction] of candidates) {
      const x = left + (right - left) * xFraction;
      const y = top + (bottom - top) * yFraction;
      if (isBareCanvasPoint(x, y)) {
        return { x, y };
      }
    }
    for (let row = 0; row <= 8; row += 1) {
      for (let column = 0; column <= 8; column += 1) {
        const x = left + ((right - left) * column) / 8;
        const y = top + ((bottom - top) * row) / 8;
        if (isBareCanvasPoint(x, y)) {
          return { x, y };
        }
      }
    }
    throw new Error("No visible bare org canvas point found");
  });
  await page.mouse.click(point.x, point.y, { button: "right" });
}

async function waitForCondition(predicate, message, { timeoutMs = 2000, intervalMs = 25 } = {}) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (predicate()) {
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
  if (predicate()) {
    return;
  }
  throw new Error(message);
}

async function waitForStatusText(page, textValue, requests = [], { timeout = 5000 } = {}) {
  try {
    await page.getByText(textValue).waitFor({ state: "visible", timeout });
  } catch (error) {
    const status = await page.locator("#status-message").textContent().catch(() => "");
    throw new Error(
      `${error instanceof Error ? error.message : String(error)}; status=${status}; requests=${JSON.stringify(requests, null, 2)}`,
    );
  }
}

async function openDeliveryRow(row) {
  await row.waitFor({ state: "visible" });
  const isOpen = await row.evaluate((element) => !(element instanceof HTMLDetailsElement) || element.open);
  if (!isOpen) {
    await row.locator(":scope > summary").click();
  }
  await row.locator(".delivery-row-body").waitFor({ state: "visible" });
}

async function openWorkspace(page) {
  await page.getByRole("tab", { name: "Workspace" }).click();
  await page.locator("#team-workspace-page").waitFor({ state: "visible" });
}

async function openProjects(page) {
  await page.getByRole("tab", { name: "Projects" }).click();
  await page.locator("#team-projects-page").waitFor({ state: "visible" });
}

async function openTeams(page) {
  await page.getByRole("tab", { name: "Teams" }).click();
  await page.locator("#team-people-page").waitFor({ state: "visible" });
}

async function openOrgChart(page) {
  await page.getByRole("tab", { name: "Org chart" }).click();
  await page.locator("#team-org-page").waitFor({ state: "visible" });
}

test("team add-on remembers the selected top-level view across refresh", async (t) => {
  const { port } = await createTeamAddonServer(t);
  const page = await openBrowserPage(t, port);

  await page.getByRole("tab", { name: "Activity timeline", exact: true }).click();
  await page.locator("#team-timeline-page").waitFor({ state: "visible" });
  assert.equal(
    await page.evaluate((storageKey) => window.localStorage.getItem(storageKey), teamViewStorageKey),
    "timeline",
  );

  await page.reload({ waitUntil: "domcontentloaded" });
  await page.locator("#team-timeline-page").waitFor({ state: "visible" });
  await page.locator('#team-view-timeline-tab[aria-selected="true"]').waitFor({ state: "visible" });
  await page.locator("#team-workspace-page").waitFor({ state: "hidden" });
});

test("team add-on normalizes deprecated Setup view storage to Teams", async (t) => {
  const { port } = await createTeamAddonServer(t);
  const page = await openBrowserPage(t, port);

  await page.evaluate((storageKey) => window.localStorage.setItem(storageKey, "setup"), teamViewStorageKey);
  await page.reload({ waitUntil: "domcontentloaded" });

  await page.locator('#team-view-people-tab[aria-selected="true"]').waitFor({ state: "visible" });
  await page.locator("#team-people-page").waitFor({ state: "visible" });
  await page.locator("#team-workspace-page").waitFor({ state: "hidden" });
  assert.equal(await page.getByRole("tab", { name: "Setup" }).count(), 0);
  assert.equal(
    await page.evaluate((storageKey) => window.localStorage.getItem(storageKey), teamViewStorageKey),
    "people",
  );
});

test("team add-on Teams tab creates teams from the top-level action", async (t) => {
  const { port, requests } = await createTeamAddonServer(t);
  const page = await openBrowserPage(t, port);

  await page.getByRole("tab", { name: "Teams" }).click();
  await page.locator("#team-people-page").waitFor({ state: "visible" });
  await page.locator("#teams-create-team-panel").waitFor({ state: "hidden" });

  await page.locator("#team-tab-create-team-action").click();
  const teamForm = page.locator("#create-team-from-teams-form");
  await teamForm.waitFor({ state: "visible" });
  await page.waitForFunction(() =>
    document.activeElement?.matches('#create-team-from-teams-form [name="name"]'),
  );

  await teamForm.locator('[name="name"]').fill("Launch Team");
  await teamForm.locator('[name="manager_session_id"]').fill("session-launch-manager");
  await teamForm.locator('[name="manager_display_name"]').fill("Launch Manager");
  await teamForm.locator('[name="operator_communication_target_id"]').fill("ops-launch");
  await teamForm.getByRole("button", { name: "Create team" }).click();

  await page.locator("#status-message", { hasText: /Saved revision \d+\./ }).waitFor({ state: "visible" });
  await page.locator('#team-view-people-tab[aria-selected="true"]').waitFor({ state: "visible" });
  await page.locator("#team-people-page").waitFor({ state: "visible" });
  await page.locator("#teams-create-team-panel").waitFor({ state: "hidden" });
  await page
    .locator('#team-people-page [data-team-record-group-id="team-launch-team"]')
    .getByText("Launch Team")
    .waitFor({ state: "visible" });

  const teamRequest = requests.find((request) =>
    request.method === "POST" && request.pathname === "/api/addons/team-addon/teams"
  );
  assert.ok(teamRequest);
  assert.equal(typeof teamRequest.payload.expected_revision, "number");
  assert.equal(teamRequest.payload.name, "Launch Team");
  assert.equal(teamRequest.payload.manager_session_id, "session-launch-manager");
  assert.equal(teamRequest.payload.manager_display_name, "Launch Manager");
  assert.equal(teamRequest.payload.manager_role_name, "Manager");
  assert.equal(teamRequest.payload.operator_communication_target_id, "ops-launch");
});

test("team add-on copies a clean team configuration with existing and new core sessions", async (t) => {
  const initialState = seededOrgChartState();
  initialState.projects.project.directory_path = "root/team-project";
  initialState.projects.project.git.directory_path = "root/team-project";
  initialState.projects.historical = {
    id: "historical",
    team_id: "team",
    name: "Historical project",
    lead_agent_id: "historical-lead",
    goals: [],
    status: "completed",
  };
  initialState.roles["historical__project-lead"] = {
    id: "historical__project-lead",
    team_id: "team",
    project_id: "historical",
    name: "Historical lead",
    permissions: [],
    archived: false,
  };
  initialState.agents["historical-lead"] = {
    id: "historical-lead",
    team_id: "team",
    role_id: "historical__project-lead",
    session_id: "session-historical-lead",
    reports_to_agent_id: "manager",
    subordinate_agent_ids: [],
    status: "active",
  };
  initialState.teams.team.project_ids.push("historical");
  initialState.agents.manager.subordinate_agent_ids.push("historical-lead");
  const originalTaskIds = Object.keys(initialState.tasks);
  const destinationSessions = ["manager", "lead", "backend", "worker"].map((agentId) => ({
    agent_id: `core-copy-${agentId}`,
    backend: agentId === "backend" ? "container_worker_vm" : "libvirt_vm",
    target_name: `core-copy-${agentId}`,
    session_id: `session-copy-${agentId}`,
    directory_path: agentId === "manager" ? "root" : "root/team-project",
    status: "running",
    updated_at: "2026-06-09T00:00:00Z",
    capabilities: {},
    host: {},
  }));
  const { port, requests, state: serverState } = await createTeamAddonServer(t, {
    initialState,
    coreAgentItems: destinationSessions,
  });
  const page = await openBrowserPage(t, port);

  await waitForRevision(page, 8);
  await openTeams(page);
  await page.locator('#people-agent-detail-view [data-team-copy-open="team"]').click();

  const dialog = page.locator("#org-action-modal");
  await dialog.getByRole("heading", { name: "Copy Team" }).waitFor({ state: "visible" });
  await dialog.getByText("Clean configuration copy").waitFor({ state: "visible" });
  await dialog.getByText(/Tasks, cases, conversations, memories/).waitFor({ state: "visible" });
  const copyForm = dialog.locator("form[data-team-copy-form]");
  assert.equal(await copyForm.locator("[data-team-copy-agent-row]").count(), 4);
  await copyForm.locator('[name="name"]').fill("Delivery Team Copy");

  await copyForm
    .locator('[data-team-copy-agent-row="manager"]')
    .getByRole("button", { name: "Configure manually" })
    .click();
  const createDialog = page.locator('[role="dialog"][aria-label="Create and register agent"]');
  await createDialog.waitFor({ state: "visible" });
  await createDialog.locator("label", { hasText: "VM name" }).locator("input").fill("vm-copy-manager");
  await createDialog.locator('input[placeholder="Required"]').fill("session-new-copy-manager");
  await createDialog.getByRole("button", { name: "Create and register" }).click();
  await copyForm
    .locator('[data-source-agent-id="manager"]')
    .waitFor({ state: "visible" });
  await page.waitForFunction(() =>
    document.querySelector('[data-source-agent-id="manager"]')?.value === "session-new-copy-manager",
  );
  assert.equal(
    requests.some((request) => request.pathname === "/api/addons/team-addon/teams/team/copy"),
    false,
  );

  for (const agentId of ["lead", "backend", "worker"]) {
    await copyForm
      .locator(`[data-team-copy-session-select][data-source-agent-id="${agentId}"]`)
      .selectOption(`session-copy-${agentId}`);
  }
  assert.equal(
    await copyForm
      .locator('[data-source-agent-id="lead"] option[value="session-new-copy-manager"]')
      .evaluate((option) => option.disabled),
    true,
  );
  await copyForm.getByRole("button", { name: "Copy team" }).click();

  await waitForRevision(page, 9);
  await dialog.waitFor({ state: "hidden" });
  await page.locator("#people-agent-detail-view").getByRole("heading", {
    name: "Delivery Team Copy",
  }).waitFor({ state: "visible" });

  const copyRequest = requests.find(
    (request) => request.method === "POST"
      && request.pathname === "/api/addons/team-addon/teams/team/copy",
  );
  assert.ok(copyRequest);
  assert.equal(copyRequest.payload.expected_revision, 8);
  assert.equal(copyRequest.payload.name, "Delivery Team Copy");
  assert.deepEqual(copyRequest.payload.agent_session_ids, {
    manager: "session-new-copy-manager",
    lead: "session-copy-lead",
    backend: "session-copy-backend",
    worker: "session-copy-worker",
  });
  assert.deepEqual(Object.keys(serverState.tasks), originalTaskIds);
  assert.ok(
    requests.some((request) =>
      request.method === "POST"
      && request.pathname === "/api/admin/agents/create-operations"
      && request.payload.session_id === "session-new-copy-manager"),
  );
  assert.ok(
    requests.some((request) =>
      request.method === "GET"
      && request.pathname === "/api/admin/agents"
      && request.search.includes("directory_path=root%2Fteam-project"),
    ),
  );
});

test("team add-on copies all source sessions by default without individual setup", async (t) => {
  const { port, requests, state: serverState } = await createTeamAddonServer(t, {
    initialState: seededOrgChartState(),
  });
  const page = await openBrowserPage(t, port);
  await waitForRevision(page, 8);
  await openTeams(page);
  await page.locator('#people-agent-detail-view [data-team-copy-open="team"]').click();
  const form = page.locator("form[data-team-copy-form]");
  const selects = form.locator("[data-team-copy-session-select]");
  await form.waitFor({ state: "visible" });
  assert.equal(await selects.count(), 4);
  for (const select of await selects.all()) {
    assert.equal(await select.inputValue(), "");
    assert.match(await select.locator("option:checked").innerText(), /Copy source session configuration/);
  }
  await form.locator('[name="name"]').fill("Automatic Copy");
  await form.getByRole("button", { name: "Copy team" }).click();
  await waitForRevision(page, 9);
  const copies = requests.filter((request) => request.pathname === "/api/admin/agents/copy-operations");
  assert.equal(copies.length, 4);
  for (const request of copies) assert.deepEqual(Object.keys(request.payload), ["source_session_id"]);
  const copyRequest = requests.find((request) => request.pathname === "/api/addons/team-addon/teams/team/copy");
  assert.equal(new Set(Object.values(copyRequest.payload.agent_session_ids)).size, 4);
  for (const [agentId, sessionId] of Object.entries(copyRequest.payload.agent_session_ids)) {
    assert.notEqual(sessionId, serverState.agents[agentId].session_id);
  }
  assert.equal(requests.some((request) => request.pathname === "/api/admin/agents/create-operations"), false);
});

test("team add-on retains completed session copies when another session fails", async (t) => {
  const { port, requests } = await createTeamAddonServer(t, { initialState: seededOrgChartState() });
  const page = await openBrowserPage(t, port);
  let attempts = 0;
  await page.route("**/api/admin/agents/copy-operations", async (route) => {
    attempts += 1;
    if (attempts === 2) {
      await route.fulfill({ status: 400, contentType: "application/json", body: JSON.stringify({ error: "Worker temporarily unavailable" }) });
    } else {
      await route.continue();
    }
  });
  await waitForRevision(page, 8);
  await openTeams(page);
  await page.locator('#people-agent-detail-view [data-team-copy-open="team"]').click();
  const form = page.locator("form[data-team-copy-form]");
  await form.getByRole("button", { name: "Copy team" }).click();
  await page.locator("#status-message", { hasText: "Worker temporarily unavailable" }).waitFor({ state: "visible" });
  const selected = await form.locator("[data-team-copy-session-select]").evaluateAll((selects) => selects.map((select) => select.value).filter(Boolean));
  assert.equal(selected.length, 1);
  assert.equal(requests.some((request) => request.pathname === "/api/addons/team-addon/teams/team/copy"), false);
  await form.getByRole("button", { name: "Copy team" }).click();
  await waitForRevision(page, 9);
  const copies = requests.filter((request) => request.pathname === "/api/admin/agents/copy-operations");
  assert.equal(copies.length, 4);
  assert.equal(new Set(copies.map((request) => request.payload.source_session_id)).size, 4);
});

test("team add-on submits the source revision shown by the copy preview", async (t) => {
  const initialState = seededOrgChartState();
  const destinationSessions = ["manager", "lead", "backend", "worker"].map((agentId) => ({
    agent_id: `core-copy-${agentId}`,
    backend: "libvirt_vm",
    target_name: `core-copy-${agentId}`,
    session_id: `session-copy-${agentId}`,
    directory_path: "root",
    status: "running",
    updated_at: "2026-06-09T00:00:00Z",
    capabilities: {},
    host: {},
  }));
  const {
    port,
    requests,
    state: serverState,
    emitStateSnapshot,
    stateStreamCount,
  } = await createTeamAddonServer(t, {
    initialState,
    coreAgentItems: destinationSessions,
  });
  const page = await openBrowserPage(t, port);

  await waitForRevision(page, 8);
  await waitForCondition(
    () => stateStreamCount() > 0,
    "expected Team state stream to be open before testing the copy preview",
  );
  await openTeams(page);
  await page.locator('#people-agent-detail-view [data-team-copy-open="team"]').click();
  const copyForm = page.locator("form[data-team-copy-form]");
  await copyForm.waitFor({ state: "visible" });
  assert.equal(await copyForm.getAttribute("data-source-revision"), "8");

  serverState.revision = 9;
  serverState.teams.team.name = "Concurrently changed Team";
  assert.ok(
    emitStateSnapshot({ response_scope: "revision", revision: 9 }) > 0,
    "expected the concurrent revision to reach the Team client",
  );
  await waitForRevision(page, 9);
  for (const agentId of ["manager", "lead", "backend", "worker"]) {
    await copyForm
      .locator(`[data-team-copy-session-select][data-source-agent-id="${agentId}"]`)
      .selectOption(`session-copy-${agentId}`);
  }
  await copyForm.getByRole("button", { name: "Copy team" }).click();

  await page.locator("#status-message", {
    hasText: "The source Team changed after this copy preview was opened.",
  }).waitFor({ state: "visible" });
  const copyRequest = requests.find(
    (request) => request.method === "POST"
      && request.pathname === "/api/addons/team-addon/teams/team/copy",
  );
  assert.ok(copyRequest);
  assert.equal(copyRequest.payload.expected_revision, 8);
  assert.deepEqual(Object.keys(serverState.teams), ["team"]);
});

test("team add-on hides copy for archived teams", async (t) => {
  const initialState = seededOrgChartState();
  initialState.teams.team.archived = true;
  Object.values(initialState.agents).forEach((agent) => {
    if (agent.team_id === "team") agent.status = "archived";
  });
  const { port } = await createTeamAddonServer(t, { initialState });
  const page = await openBrowserPage(t, port);

  await waitForRevision(page, 8);
  await page.locator("#show-archived-toggle").check();
  await openTeams(page);
  await page.locator("#people-agent-detail-view").getByText("archived", { exact: true }).waitFor({
    state: "visible",
  });
  assert.equal(
    await page.locator('#people-agent-detail-view [data-team-copy-open="team"]').count(),
    0,
  );
});

test("team add-on team editor selects from all registered communication targets", async (t) => {
  const initialState = seededOrgChartState();
  initialState.teams.team.operator_communication_target_id = "team-alerts";
  const { port, requests } = await createTeamAddonServer(t, {
    initialState,
    communicationTargets: [
      {
        value: "team-alerts",
        label: "team-alerts (teams/teams, read, reply, write)",
      },
      {
        value: "ops-notify-only",
        label: "ops-notify-only (teams/teams, notify)",
      },
    ],
  });
  const page = await openBrowserPage(t, port);

  await openTeams(page);
  await page.locator('#people-agent-detail-view [data-edit-type="team"][data-edit-id="team"]').click();

  const form = page.locator("#edit-record-form");
  const target = form.locator('select[name="operator_communication_target_id"]');
  await target.waitFor({ state: "visible" });
  assert.deepEqual(
    await target.locator("option").evaluateAll((options) => options.map((option) => option.value)),
    ["", "ops-notify-only", "team-alerts"],
  );
  assert.equal(await target.inputValue(), "team-alerts");

  await target.selectOption("ops-notify-only");
  await form.getByRole("button", { name: "Save changes" }).click();
  await page.locator("#status-message", { hasText: /Saved revision \d+\./ }).waitFor({ state: "visible" });

  const update = requests.find(
    (request) =>
      request.method === "PATCH"
      && request.pathname === "/api/addons/team-addon/teams/team",
  );
  assert.ok(update);
  assert.equal(update.payload.operator_communication_target_id, "ops-notify-only");
});

test("team add-on UI imports Git repo config into project forms", async (t) => {
  const { port, requests } = await createTeamAddonServer(t);
  const page = await openBrowserPage(t, port);

  await openProjects(page);
  await page.locator("[data-project-create-toggle]").click();
  const projectForm = page.locator("#create-project-from-projects-form");
  await projectForm.waitFor({ state: "visible" });
  await projectForm.locator("summary", { hasText: "Git config" }).click();
  await projectForm.locator('[name="repo_source"]').fill("/srv/repos/imported-project");
  await projectForm.getByRole("button", { name: "Import Git repo" }).click();

  await page
    .locator("#status-message", {
      hasText: "Imported Git repo. Review the populated Git fields, then save.",
    })
    .waitFor({ state: "visible" });
  assert.equal(await projectForm.locator('[name="repo_source"]').inputValue(), "/srv/repos/imported-project");
  assert.equal(
    await projectForm.locator('[name="managed_repo_id"]').inputValue(),
    "mr_0123456789abcdef0123",
  );
  assert.equal(await projectForm.locator('[name="base_branch"]').inputValue(), "main");

  const importRequest = requests.find(
    (request) =>
      request.method === "POST" &&
      request.pathname === "/api/addons/team-addon/projects/git/import",
  );
  assert.ok(importRequest);
  assert.deepEqual(importRequest.payload, {
    repo_source: "/srv/repos/imported-project",
  });
});

test("team add-on UI creates review profile criteria with structured rows", async (t) => {
  const { port, requests } = await createTeamAddonServer(t, {
    initialState: seededOrgChartState(),
  });
  const page = await openBrowserPage(t, port);

  await waitForRevision(page, 8);
  await openProjects(page);
  await page.getByRole("button", { name: "Add review profile" }).click();
  const profileForm = page.locator("#create-review-profile-for-project-form");
  await profileForm.waitFor({ state: "visible" });
  await profileForm.locator('[name="name"]').fill("Security review");
  await profileForm.locator('[name="description"]').fill("Checklist for security-sensitive work.");
  await profileForm.locator('[name="applicable_role_ids"]').selectOption(["project__backend"]);

  const firstCriterion = profileForm.locator("[data-review-profile-criterion-row]").first();
  await firstCriterion.locator('[name="criterion_label"]').fill("Authorization");
  await firstCriterion.locator('[name="criterion_description"]').fill("Scopes are enforced");
  await profileForm.getByRole("button", { name: "Add criterion" }).click();
  const secondCriterion = profileForm.locator("[data-review-profile-criterion-row]").nth(1);
  await secondCriterion.locator('[name="criterion_label"]').fill("Logging");
  await secondCriterion.locator('[name="criterion_id"]').fill("audit-logs");
  await secondCriterion.locator('[name="criterion_description"]').fill("No secrets in logs");
  await secondCriterion.locator('[name="criterion_required"]').evaluate((field) => {
    field.checked = false;
    field.dispatchEvent(new Event("change", { bubbles: true }));
  });

  await profileForm.getByRole("button", { name: "Create profile" }).click();
  await waitForStatusText(page, "Saved revision", requests);

  const profileRequest = requests.find((request) =>
    request.method === "POST" && request.pathname === "/api/addons/team-addon/review-profiles"
  );
  assert.ok(profileRequest);
  assert.equal(profileRequest.payload.expected_revision, 8);
  assert.deepEqual(profileRequest.payload.criteria, [
    {
      id: "authorization",
      label: "Authorization",
      description: "Scopes are enforced",
      required: true,
    },
    {
      id: "audit-logs",
      label: "Logging",
      description: "No secrets in logs",
      required: false,
    },
  ]);
});

test("team add-on Projects tab reveals project and review profile forms on demand", async (t) => {
  const { port, requests } = await createTeamAddonServer(t, {
    initialState: seededOrgChartState(),
  });
  const page = await openBrowserPage(t, port);

  await waitForRevision(page, 8);
  await openProjects(page);
  const panel = page.locator("#team-projects-page");
  const teamWideOutcomeLabel = panel.locator(
    'form[data-form-id="create-outcome-kind-form"] label.checkbox-label',
    { hasText: "Team-wide definition" },
  );
  await teamWideOutcomeLabel.waitFor({ state: "visible" });
  assert.equal(
    await teamWideOutcomeLabel.locator('input[name="team_wide"]').count(),
    1,
  );
  await panel.locator("#create-project-from-projects-form").waitFor({ state: "hidden" });
  assert.equal(await panel.locator("#create-review-profile-for-project-form").count(), 0);

  await panel.getByRole("button", { name: "Add review profile" }).click();
  const profileForm = panel.locator("#create-review-profile-for-project-form");
  await profileForm.waitFor({ state: "visible" });
  await profileForm.locator('[name="name"]').fill("Project security review");
  await profileForm.locator('[name="description"]').fill("Review project-sensitive changes.");
  await profileForm.locator('[name="applicable_role_ids"]').selectOption(["project__backend"]);
  await profileForm.locator('[name="criterion_label"]').fill("Authorization");
  await profileForm.getByRole("button", { name: "Create profile" }).click();
  await page.getByText(/Saved revision \d+\./).waitFor({ state: "visible" });
  assert.equal(await panel.locator("#create-review-profile-for-project-form").count(), 0);

  await panel.locator("[data-project-create-toggle]").click();
  const projectForm = panel.locator("#create-project-from-projects-form");
  await projectForm.waitFor({ state: "visible" });
  await projectForm.locator('[name="team_id"]').selectOption("team");
  await waitForSelectOption(page, '#create-project-from-projects-form [name="lead_agent_id"]', "lead");
  await projectForm.locator('[name="lead_agent_id"]').selectOption("lead");
  await projectForm.locator('[name="name"]').fill("Follow-up project");
  await projectForm.locator('[name="goal_description"]').fill("Ship a follow-up slice.");
  await projectForm.locator("summary", { hasText: "Git config" }).click();
  await projectForm.locator('[name="managed_repo_id"]').fill("mr_primary_project");
  await projectForm.locator('[name="agent_author_name"]').fill("Project Author");
  await projectForm.locator('[name="agent_author_email"]').fill("author@example.test");
  await projectForm.locator('[name="agent_committer_name"]').fill("Project Committer");
  await projectForm.locator('[name="agent_committer_email"]').fill("committer@example.test");
  await projectForm.getByRole("button", { name: "Create project" }).click();
  await projectForm.waitFor({ state: "hidden" });

  const profileRequest = requests.find((request) =>
    request.method === "POST" && request.pathname === "/api/addons/team-addon/review-profiles"
  );
  assert.ok(profileRequest);
  assert.equal(profileRequest.payload.expected_revision, 8);
  assert.equal(profileRequest.payload.project_id, "project");

  const projectRequest = requests.find((request) =>
    request.method === "POST" && request.pathname === "/api/addons/team-addon/projects"
  );
  assert.ok(projectRequest);
  assert.equal(projectRequest.payload.expected_revision, 9);
  assert.equal(projectRequest.payload.name, "Follow-up project");
  assert.deepEqual(projectRequest.payload.git, {
    managed_repo_id: "mr_primary_project",
    agent_author_identity: {
      name: "Project Author",
      email: "author@example.test",
    },
    agent_committer_identity: {
      name: "Project Committer",
      email: "committer@example.test",
    },
  });
});

test("team add-on Projects tab collapses, filters, sorts, and pages delivery records", async (t) => {
  const initialState = seededOrgChartState();
  initialState.tasks.task_open.deliverable_ids = [];
  initialState.deliverables = {};
  for (let index = 1; index <= 12; index += 1) {
    const suffix = String(index).padStart(2, "0");
    const deliverableId = `deliverable-${suffix}`;
    initialState.deliverables[deliverableId] = {
      id: deliverableId,
      task_id: "task_open",
      producer_agent_id: "backend",
      artifact_refs: [],
      summary: `Visible deliverable ${index}`,
      state: "accepted",
      review_request_ids: [],
      created_at: `2026-06-11T${suffix}:00:00Z`,
    };
    initialState.tasks.task_open.deliverable_ids.push(deliverableId);
  }
  initialState.deliverables["deliverable-rejected"] = {
    id: "deliverable-rejected",
    task_id: "task_open",
    producer_agent_id: "backend",
    artifact_refs: [],
    summary: "Rejected deliverable",
    state: "rejected",
    review_request_ids: [],
    created_at: "2026-06-12T00:00:00Z",
  };
  initialState.tasks.task_open.deliverable_ids.push("deliverable-rejected");

  initialState.release_packages = {};
  for (let index = 1; index <= 12; index += 1) {
    const suffix = String(index).padStart(2, "0");
    initialState.release_packages[`release-${suffix}`] = {
      id: `release-${suffix}`,
      project_id: "project",
      deliverable_ids: [`deliverable-${suffix}`],
      state: "published",
      published_at: `2026-06-11T${suffix}:30:00Z`,
      updated_at: `2026-06-11T${suffix}:30:00Z`,
      created_at: `2026-06-11T${suffix}:00:00Z`,
    };
  }
  initialState.release_packages["release-rejected"] = {
    id: "release-rejected",
    project_id: "project",
    deliverable_ids: ["deliverable-rejected"],
    state: "rejected",
    policy_version: 2,
    candidate_commit_sha: "rejected-candidate-sha",
    manifest: {
      candidate_commit_sha: "rejected-candidate-sha",
      verification_commands: [{ name: "solution tests" }],
    },
    verification_runs: [
      {
        id: "verification-rejected-1",
        state: "failed",
        attempt_number: 1,
        failure_kind: "infrastructure",
        command_results: [
          {
            name: "solution tests",
            state: "failed",
            exit_code: 17,
            failure_cause: "pids_limit_exhausted",
            log_excerpt: [
              "Starting solution tests",
              "runner: error: process limit exhausted",
            ].join("\n"),
          },
        ],
      },
    ],
    published_at: "2026-06-12T00:30:00Z",
    updated_at: "2026-06-12T00:30:00Z",
    created_at: "2026-06-12T00:00:00Z",
  };

  const { port } = await createTeamAddonServer(t, { initialState });
  const page = await openBrowserPage(t, port);

  await waitForRevision(page, 8);
  await openProjects(page);
  const panel = page.locator("#team-projects-page");
  assert.equal(await panel.locator(".task-list .task-item").count(), 0);

  const projectTab = panel.locator('[data-project-focus-id="project"]');
  const deliverablesSection = panel.locator('details.project-record-section[data-project-section="deliverables"]').first();
  await deliverablesSection.locator("summary.project-record-section-summary").click();
  assert.equal(await deliverablesSection.evaluate((element) => element.open), true);
  await projectTab.click();
  assert.equal(await deliverablesSection.evaluate((element) => element.open), true);
  assert.equal(await deliverablesSection.locator(".deliverable-list .record-item").count(), 10);
  assert.equal(
    await deliverablesSection.locator(".deliverable-list .record-item").first().getAttribute("data-deliverable-id"),
    "deliverable-12",
  );
  assert.equal(await deliverablesSection.getByText("Rejected deliverable").count(), 0);

  await deliverablesSection.getByRole("button", { name: "Next" }).click();
  assert.equal(await deliverablesSection.locator(".deliverable-list .record-item").count(), 2);
  assert.equal(
    await deliverablesSection.locator(".deliverable-list .record-item").first().getAttribute("data-deliverable-id"),
    "deliverable-02",
  );

  await deliverablesSection.getByRole("button", { name: "Show rejected" }).click();
  assert.equal(
    await deliverablesSection.locator(".deliverable-list .record-item").first().getAttribute("data-deliverable-id"),
    "deliverable-rejected",
  );

  await deliverablesSection.locator("summary.project-record-section-summary").click();
  assert.equal(await deliverablesSection.evaluate((element) => element.open), false);
  await projectTab.click();
  assert.equal(await deliverablesSection.evaluate((element) => element.open), false);

  const releasesSection = panel.locator('details.project-record-section[data-project-section="releases"]').first();
  await releasesSection.locator("summary.project-record-section-summary").click();
  assert.equal(await releasesSection.locator(".release-list .record-item").count(), 10);
  assert.equal(
    await releasesSection.locator(".release-list .record-item").first().getAttribute("data-release-id"),
    "release-12",
  );
  assert.equal(await releasesSection.getByText("release-rejected").count(), 0);

  await releasesSection.getByRole("button", { name: "Show rejected" }).click();
  assert.equal(
    await releasesSection.locator(".release-list .record-item").first().getAttribute("data-release-id"),
    "release-rejected",
  );
  const rejectedReleaseCard = releasesSection.locator(
    '[data-release-id="release-rejected"]',
  );
  await rejectedReleaseCard.locator("summary.compact-record-summary").click();
  const failedVerificationStage = rejectedReleaseCard
    .locator(".release-progress > .release-progress-stage")
    .filter({ hasText: "Automated verification" });
  await failedVerificationStage
    .getByText(
      "Failed command: solution tests · exit code 17 · failure cause: Pids Limit Exhausted · Error: runner: error: process limit exhausted",
      { exact: true },
    )
    .waitFor({ state: "visible" });

  const releaseCard = releasesSection.locator('[data-release-id="release-12"]');
  await releaseCard.locator("summary.compact-record-summary").click();
  await releaseCard.getByRole("button", { name: "Open release detail" }).click();
  await releaseCard
    .getByRole("button", { name: "Close release detail" })
    .waitFor({ state: "visible" });
  await releaseCard.locator(".release-review-detail").waitFor({ state: "visible" });

  await page.getByRole("tab", { name: "Delivery" }).click();
  const deliveryReleases = page.locator("#team-delivery-page [data-releases-view]");
  await deliveryReleases.locator(".release-list .record-item").first().waitFor({ state: "visible" });
  assert.equal(
    await deliveryReleases.locator(".release-list .record-item").first().getAttribute("data-release-id"),
    "release-rejected",
  );
});

async function checkReleaseHistoryFilters(t, { windowed = false } = {}) {
  const initialState = seededGitState();
  initialState.release_packages["release-pending-filter"] = {
    id: "release-pending-filter",
    project_id: "alpha-project",
    summary: "Prepare the pending filter candidate.",
    deliverable_ids: [],
    state: "pending",
    created_at: "2026-06-07T12:00:00Z",
    updated_at: "2026-06-07T12:00:00Z",
  };
  initialState.release_packages["release-rejected-filter"] = {
    id: "release-rejected-filter",
    project_id: "alpha-project",
    summary: "Record the rejected filter attempt.",
    deliverable_ids: [],
    state: "rejected",
    created_at: "2026-06-05T12:00:00Z",
    updated_at: "2026-06-05T12:00:00Z",
  };
  initialState.history_windows = {
    release_packages: {
      total_count: windowed ? 150 : 3,
      returned_count: 3,
      limit: 100,
      windowed,
    },
  };
  const { port } = await createTeamAddonServer(t, { initialState });
  const page = await openBrowserPage(t, port);

  await page.getByRole("tab", { name: "Delivery" }).click();
  await page
    .locator("#team-delivery-page")
    .getByRole("heading", { name: "Release history" })
    .waitFor({ state: "visible" });
  const releaseHistory = page.locator("#team-delivery-page [data-releases-view]");
  const filters = releaseHistory.getByRole("group", { name: "Release history filters" });
  await filters.waitFor({ state: "visible" });
  const stateFilter = filters.locator('[data-release-history-filter="state"]');

  assert.equal(await releaseHistory.locator(".release-card").count(), 3);
  const historySummary = releaseHistory.locator(".release-history-filter-summary");
  const unfilteredSummary = windowed
    ? /^3 of 150 release packages loaded\. Filters apply to the loaded releases\./
    : /^3 release packages in history\./;
  assert.match(await historySummary.innerText(), unfilteredSummary);
  await stateFilter.selectOption("accepted");
  await releaseHistory
    .locator('li.release-card[data-release-id="release-accepted"]')
    .waitFor({ state: "visible" });
  assert.equal(await releaseHistory.locator(".release-card").count(), 1);
  assert.match(
    await historySummary.innerText(),
    windowed
      ? /^1 of 3 loaded release packages shown \(150 in history\)\. Filters apply to the loaded releases\./
      : /^1 of 3 release packages shown\./,
  );
  await stateFilter.selectOption("rejected");
  await releaseHistory
    .locator('li.release-card[data-release-id="release-rejected-filter"]')
    .waitFor({ state: "visible" });
  assert.equal(await releaseHistory.locator(".release-card").count(), 1);

  await stateFilter.selectOption("");
  const dateFrom = filters.locator('[data-release-history-filter="date-from"]');
  const dateThrough = filters.locator('[data-release-history-filter="date-through"]');
  await dateFrom.fill("2026-06-07");
  await dateFrom.press("Tab");
  await dateThrough.fill("2026-06-07");
  await dateThrough.press("Tab");
  await releaseHistory
    .locator('li.release-card[data-release-id="release-pending-filter"]')
    .waitFor({ state: "visible" });
  assert.equal(await releaseHistory.locator(".release-card").count(), 1);
  assert.match(
    await releaseHistory.locator(".release-history-filter-summary").innerText(),
    /Activity date is published when available; otherwise prepared\./,
  );

  await filters.getByRole("button", { name: "Clear filters" }).click();
  await dateFrom.fill("2026-06-09");
  await dateFrom.press("Tab");
  await dateThrough.fill("2026-06-09");
  await dateThrough.press("Tab");
  await releaseHistory
    .locator('li.release-card[data-release-id="release-accepted"]')
    .waitFor({ state: "visible" });
  assert.equal(await releaseHistory.locator(".release-card").count(), 1);

  await filters.getByRole("button", { name: "Clear filters" }).click();
  assert.equal(await stateFilter.inputValue(), "");
  assert.equal(await dateFrom.inputValue(), "");
  assert.equal(await dateThrough.inputValue(), "");
  assert.equal(await releaseHistory.locator(".release-card").count(), 3);
  assert.match(await historySummary.innerText(), unfilteredSummary);
}

test("team add-on release history filters by state and activity date", async (t) => {
  await checkReleaseHistoryFilters(t);
});

test("team add-on release history filters explain partial history counts", async (t) => {
  await checkReleaseHistoryFilters(t, { windowed: true });
});

test("team add-on delivery records lead with human subjects and progressive technical detail", async (t) => {
  const initialState = seededOrgChartState();
  const deliverableId = "deliverable-rich-internal-42";
  const releaseId = "release-rich-internal-84";
  const longSummary = [
    "Implemented the bounded operator workflow with clearer review checkpoints.",
    "",
    "The second paragraph records verification, rollout notes, and supporting context without becoming the card title. ".repeat(
      4,
    ),
  ].join("\n");

  initialState.tasks.task_open.deliverable_ids = [deliverableId];
  initialState.deliverables = {
    [deliverableId]: {
      id: deliverableId,
      task_id: "task_open",
      producer_agent_id: "backend",
      artifact_refs: ["artifact-space:delivery/rich-result"],
      summary: longSummary,
      state: "accepted",
      review_request_ids: ["review-rich-1"],
      initial_release_disposition: {
        state: "release_requested",
        created_at: "2026-06-11T09:26:00Z",
        updated_at: "2026-06-11T09:30:00Z",
        decided_at: "2026-06-11T09:30:00Z",
        decided_by_actor: "agent:lead",
        reason: null,
        release_package_id: releaseId,
      },
      git_refs: {
        branch_context_id: "branch-context-rich-1",
        agent_branch: "agents/backend/rich-result",
        feature_branch: "feature/rich-result",
        commit_sha: "abc123rich",
        promotion_record_ids: ["promotion-rich-1"],
      },
      created_at: "2026-06-11T09:15:00Z",
    },
  };
  initialState.review_profiles = {
    "profile-rich-1": {
      id: "profile-rich-1",
      project_id: "project",
      name: "Release readiness",
      description: "Confirms the bounded delivery is ready.",
      applicable_role_ids: ["project__project-lead"],
      criteria: [],
      archived: false,
    },
  };
  initialState.review_requests = {
    "review-rich-1": {
      id: "review-rich-1",
      deliverable_id: deliverableId,
      reviewer_agent_id: "lead",
      review_profile_id: "profile-rich-1",
      release_package_id: releaseId,
      state: "accepted",
      findings: ["Ready to publish."],
      created_at: "2026-06-11T09:20:00Z",
      decision_at: "2026-06-11T09:25:00Z",
    },
  };
  initialState.release_packages = {
    [releaseId]: {
      id: releaseId,
      project_id: "project",
      summary: "Publish the bounded operator workflow with verified review checkpoints.",
      deliverable_ids: [deliverableId],
      state: "accepted",
      policy_version: 2,
      candidate_commit_sha: "abc123rich",
      published_commit_sha: "abc123rich",
      review_request_ids: ["review-rich-1"],
      verification_run_ids: [
        "verification-rich-failed",
        "verification-rich-1",
      ],
      git_promotion_record_ids: ["promotion-rich-1"],
      manifest: {
        candidate_commit_sha: "abc123rich",
        deliverables: [{ deliverable_id: deliverableId }],
        verification_commands: [{ name: "focused-smoke" }],
        review_requirements: [{ reviewer_role_id: "project__project-lead" }],
        human_review_requirement: {
          reviewer_group_ids: ["release-approvers"],
        },
      },
      verification_runs: [
      {
        id: "verification-rich-failed",
        state: "failed",
        attempt_number: 1,
        failure_kind: "candidate",
        candidate_commit_sha: "abc123rich",
        superseded_by_verification_run_id: "verification-rich-1",
        command_results: [
          {
            name: "focused-smoke",
            state: "failed",
            exit_code: 2,
            log_excerpt: [
              "> npm run focused-smoke",
              "src/operator.ts(42,17): error TS2339: Property 'gate' does not exist.",
            ].join("\n"),
          },
        ],
      },
      {
        id: "verification-rich-1",
          state: "passed",
          candidate_commit_sha: "abc123rich",
          supersedes_verification_run_id: "verification-rich-failed",
        },
      ],
      human_review: {
        state: "approved",
        request_id: "human-review-rich-1",
      },
      created_at: "2026-06-11T09:30:00Z",
      updated_at: "2026-06-11T09:45:00Z",
      published_at: "2026-06-11T09:45:00Z",
    },
  };

  const { port } = await createTeamAddonServer(t, { initialState });
  const page = await openBrowserPage(t, port, {
    permissions: ["clipboard-read", "clipboard-write"],
  });

  await waitForRevision(page, 8);
  await openProjects(page);
  const panel = page.locator("#team-projects-page");
  const deliverablesSection = panel
    .locator('details.project-record-section[data-project-section="deliverables"]')
    .first();
  await deliverablesSection.locator("summary.project-record-section-summary").click();

  const deliverableCard = deliverablesSection.locator(
    `[data-deliverable-id="${deliverableId}"]`,
  );
  await deliverableCard.getByText("Build API", { exact: true }).waitFor({
    state: "visible",
  });
  await deliverableCard.getByText(/^Backend Worker ·/).waitFor({ state: "visible" });
  assert.equal(
    await deliverableCard
      .locator('summary.deliverable-card-summary time[datetime="2026-06-11T09:15:00Z"]')
      .count(),
    1,
  );
  const preview = await deliverableCard.locator(".deliverable-summary-preview").innerText();
  assert.match(
    preview,
    /^Implemented the bounded operator workflow with clearer review checkpoints\./,
  );
  assert.equal(preview.includes("\n"), false);
  assert.ok(preview.length <= 220, `expected a bounded summary preview, got ${preview.length} characters`);

  await deliverableCard.locator("summary.deliverable-card-summary").click();
  for (const heading of [
    "Summary / outcome",
    "Task and producer",
    "Evidence and artifacts",
    "Initial release decision",
    "Reviews and decision",
    "Lineage / technical details",
  ]) {
    await deliverableCard.getByRole("heading", { name: heading, exact: true }).waitFor({
      state: "visible",
    });
  }
  await deliverableCard
    .getByText("artifact-space:delivery/rich-result", { exact: true })
    .waitFor({ state: "visible" });
  await deliverableCard.getByText("Release Requested", { exact: true }).waitFor({
    state: "visible",
  });
  await deliverableCard.getByText("agent:lead", { exact: true }).waitFor({
    state: "visible",
  });
  const deliverableTechnicalDetails = deliverableCard.locator(
    ".deliverable-lineage-group > details.technical-details",
  );
  assert.equal(
    await deliverableTechnicalDetails.getByText(deliverableId, { exact: true }).isVisible(),
    false,
  );
  await deliverableTechnicalDetails.locator("summary").click();
  await deliverableTechnicalDetails.getByText(deliverableId, { exact: true }).waitFor({
    state: "visible",
  });
  const deliverableCopyButton = deliverableTechnicalDetails.getByRole("button", {
    name: `Copy ID: ${deliverableId}`,
  });
  await deliverableCopyButton.click();
  await deliverableCopyButton.getByText("Copied", { exact: true }).waitFor({
    state: "visible",
  });
  assert.equal(await page.evaluate(() => navigator.clipboard.readText()), deliverableId);

  const releasesSection = panel
    .locator('details.project-record-section[data-project-section="releases"]')
    .first();
  await releasesSection.locator("summary.project-record-section-summary").click();
  const releaseCard = releasesSection.locator(`[data-release-id="${releaseId}"]`);
  await releaseCard.getByText("Release: Build API", { exact: true }).waitFor({
    state: "visible",
  });
  await releaseCard.getByText(/Project · 1 included work item/).waitFor({
    state: "visible",
  });
  assert.equal(await releaseCard.getByText(releaseId, { exact: true }).isVisible(), false);

  await releaseCard.locator("summary.release-card-summary").click();
  const releaseStages = releaseCard.locator(".release-progress > .release-progress-stage");
  assert.equal(await releaseStages.count(), 5);
  for (const stage of [
    "Candidate prepared",
    "Automated verification",
    "Release agent review",
    "Human review",
    "Publication",
  ]) {
    await releaseStages.getByText(stage, { exact: true }).waitFor({ state: "visible" });
  }
  assert.equal(
    await releaseCard.locator(
      ".release-progress > .release-progress-stage.release-progress-complete",
    ).count(),
    5,
  );
  const verificationStage = releaseStages.filter({
    hasText: "Automated verification",
  });
  assert.match(
    (await verificationStage.getAttribute("class")) || "",
    /release-progress-complete/,
  );
  await verificationStage.getByText("Checks passed", { exact: true }).waitFor({
    state: "visible",
  });
  await releaseCard
    .getByRole("heading", { name: "Release notes", exact: true })
    .waitFor({ state: "visible" });
  await releaseCard
    .getByText(
      "Publish the bounded operator workflow with verified review checkpoints.",
      { exact: true },
    )
    .waitFor({ state: "visible" });
  await releaseCard
    .getByRole("heading", { name: "Included work", exact: true })
    .waitFor({ state: "visible" });
  await releaseCard.locator(".release-included-work").getByText("Build API", { exact: true }).waitFor({
    state: "visible",
  });

  const releaseTechnicalDetails = releaseCard.locator("details.technical-details", {
    hasText: "Release ID",
  });
  assert.equal(
    await releaseTechnicalDetails.getByText(releaseId, { exact: true }).isVisible(),
    false,
  );
  await releaseTechnicalDetails.locator("summary").click();
  const releaseCopyButton = releaseTechnicalDetails.getByRole("button", {
    name: `Copy ID: ${releaseId}`,
  });
  await releaseCopyButton.click();
  await releaseCopyButton.getByText("Copied", { exact: true }).waitFor({
    state: "visible",
  });
  assert.equal(await page.evaluate(() => navigator.clipboard.readText()), releaseId);

  await releaseCard.getByRole("button", { name: "Open release detail" }).click();
  const releaseDetail = releaseCard.locator(".release-review-detail");
  await releaseDetail.waitFor({ state: "visible" });
  const failedCommand = releaseDetail.locator(
    ".release-verification-command-failed",
  );
  await failedCommand
    .getByText("Failed command: focused-smoke", { exact: true })
    .waitFor({ state: "visible" });
  await failedCommand.getByText("Exit code: 2", { exact: true }).waitFor({
    state: "visible",
  });
  await failedCommand
    .getByText(
      "Decisive error: src/operator.ts(42,17): error TS2339: Property 'gate' does not exist.",
      { exact: true },
    )
    .waitFor({ state: "visible" });
  const failedCommandLog = failedCommand.locator(
    "details.release-verification-log",
  );
  assert.equal(await failedCommandLog.locator("pre").isVisible(), false);
  await failedCommandLog
    .getByText("Show captured command log", { exact: true })
    .click();
  assert.equal(
    await failedCommandLog.locator("pre").innerText(),
    [
      "> npm run focused-smoke",
      "src/operator.ts(42,17): error TS2339: Property 'gate' does not exist.",
    ].join("\n"),
  );
});

test("team add-on live verification logs follow the tail without overriding manual scrolling", async (t) => {
  const initialState = seededGitState();
  const releaseId = "release-live-verification";
  const verificationRunId = "verification-live-1";
  const release = {
    id: releaseId,
    project_id: "alpha-project",
    summary: "Run the live release verification checks.",
    deliverable_ids: ["deliverable-accepted"],
    state: "pending",
    base_commit_sha: "base-live",
    candidate_commit_sha: "candidate-live",
    candidate_tree_hash: "tree-live",
    manifest: {
      candidate_commit_sha: "candidate-live",
      candidate_tree_hash: "tree-live",
      deliverables: [{ deliverable_id: "deliverable-accepted" }],
      verification_commands: [{ name: "client-tests" }],
    },
    verification_runs: [
      {
        id: verificationRunId,
        state: "running",
        attempt_number: 1,
        candidate_commit_sha: "candidate-live",
        command_results: [{ name: "client-tests", state: "running" }],
      },
    ],
    created_at: "2026-09-08T08:00:00Z",
    updated_at: "2026-09-08T08:00:00Z",
  };
  initialState.release_packages = {
    [releaseId]: { ...release, detail: release },
  };
  const liveLog = (lastLine) => Array.from(
    { length: lastLine },
    (_, index) => `live verifier line ${String(index + 1).padStart(3, "0")}`,
  ).join("\n");
  const releaseVerificationLivePayloads = [80, 81, 82].map((lastLine) => ({
    release_package_id: releaseId,
    run: {
      id: verificationRunId,
      state: "running",
      current_command: { index: 0, name: "client-tests" },
    },
    log: {
      text: liveLog(lastLine),
      has_more_before: true,
    },
  }));
  const {
    port,
    requests,
    releaseVerificationLiveRequestCount,
  } = await createTeamAddonServer(t, {
    initialState,
    releaseVerificationLivePayloads,
  });
  const page = await openBrowserPage(t, port, { viewport: { width: 1280, height: 720 } });

  await waitForRevision(page, 4);
  await page.getByRole("tab", { name: "Delivery" }).click();
  const releaseCard = page.locator(
    `#team-delivery-page li.release-card[data-release-id="${releaseId}"]`,
  );
  await releaseCard.locator("summary.compact-record-summary").click();
  await releaseCard.getByRole("button", { name: "Open release detail" }).click();
  const logSelector = [
    "pre[data-release-verification-live-log]",
    `[data-release-id="${releaseId}"]`,
    `[data-verification-run-id="${verificationRunId}"]`,
    '[data-command-index="0"]',
  ].join("");
  const liveLogElement = releaseCard.locator(logSelector);
  await page.waitForFunction(
    (selector) => document.querySelector(selector)?.textContent?.includes("live verifier line 080"),
    logSelector,
  );
  assert.equal(releaseVerificationLiveRequestCount(), 1);
  assert.equal(
    new URLSearchParams(
      requests.find((request) => request.pathname.endsWith("/verification-live"))?.search || "",
    ).get("limit"),
    "160",
  );
  const initialLogScroll = await liveLogElement.evaluate((element) => ({
    scrollTop: element.scrollTop,
    maxScrollTop: element.scrollHeight - element.clientHeight,
  }));
  assert.ok(initialLogScroll.maxScrollTop > 0);
  assert.ok(
    Math.abs(initialLogScroll.maxScrollTop - initialLogScroll.scrollTop) <= 1,
    `expected the live log to open at its tail; scroll=${JSON.stringify(initialLogScroll)}`,
  );

  const manualLogScrollTop = await liveLogElement.evaluate((element) => {
    const maxScrollTop = element.scrollHeight - element.clientHeight;
    element.scrollTop = Math.max(1, Math.min(96, Math.floor(maxScrollTop / 2)));
    return element.scrollTop;
  });
  assert.ok(manualLogScrollTop > 0);
  const pageScrollBeforeUpdate = await page.evaluate((selector) => {
    document.querySelector(selector)?.scrollIntoView({ block: "end" });
    return window.scrollY;
  }, logSelector);
  assert.ok(pageScrollBeforeUpdate > 0);

  await page.waitForFunction(
    (selector) => document.querySelector(selector)?.textContent?.includes("live verifier line 081"),
    logSelector,
  );
  const preservedScroll = await liveLogElement.evaluate((element) => element.scrollTop);
  assert.ok(
    Math.abs(preservedScroll - manualLogScrollTop) <= 1,
    `expected manual log scrolling to survive an update; before=${manualLogScrollTop} after=${preservedScroll}`,
  );
  assert.ok(
    Math.abs((await page.evaluate(() => window.scrollY)) - pageScrollBeforeUpdate) <= 1,
    "expected the page scroll position to survive a live-log update",
  );

  await liveLogElement.evaluate((element) => {
    element.scrollTop = element.scrollHeight;
  });
  await page.waitForFunction(
    (selector) => document.querySelector(selector)?.textContent?.includes("live verifier line 082"),
    logSelector,
  );
  const followedLogScroll = await liveLogElement.evaluate((element) => ({
    scrollTop: element.scrollTop,
    maxScrollTop: element.scrollHeight - element.clientHeight,
  }));
  assert.ok(
    Math.abs(followedLogScroll.maxScrollTop - followedLogScroll.scrollTop) <= 1,
    `expected a tailed live log to follow new output; scroll=${JSON.stringify(followedLogScroll)}`,
  );
});

test("team add-on Cases defaults to active work and pages durable case history", async (t) => {
  const initialState = seededOrgChartState();
  const activeStatuses = ["open", "triage", "in_progress", "waiting"];
  initialState.cases = {};
  activeStatuses.forEach((status, index) => {
    const suffix = String(index + 1).padStart(2, "0");
    initialState.cases[`case-active-${suffix}`] = {
      id: `case-active-${suffix}`,
      team_id: "team",
      project_id: "project",
      title: `Active ${status} case`,
      description: `Current ${status} work.`,
      source: "operator",
      source_ref: `active-${suffix}`,
      status,
      priority: "normal",
      owner_agent_id: "backend",
      task_ids: ["task_open"],
      tags: ["active"],
      created_at: `2026-06-10T0${index}:00:00Z`,
      updated_at: `2026-06-10T0${index}:30:00Z`,
    };
  });
  for (let index = 1; index <= 21; index += 1) {
    const suffix = String(index).padStart(2, "0");
    const durable = index === 21;
    initialState.cases[`case-resolved-${suffix}`] = {
      id: `case-resolved-${suffix}`,
      team_id: "team",
      project_id: "project",
      title: durable ? "Durable record sentinel" : `Resolved archive ${suffix}`,
      description: durable
        ? "durable-record-sentinel keeps the linked terminal record searchable"
        : `Resolved historical record ${suffix}.`,
      source: "post_release_feedback",
      source_ref: `release-${suffix}`,
      status: "resolved",
      priority: "normal",
      owner_agent_id: "backend",
      task_ids: durable ? ["task_open"] : [],
      tags: durable ? ["durable-record-sentinel"] : ["history"],
      created_at: `2026-06-09T${suffix}:00:00Z`,
      updated_at: `2026-06-11T${suffix}:00:00Z`,
    };
  }
  initialState.cases["case-closed-01"] = {
    id: "case-closed-01",
    team_id: "team",
    project_id: "project",
    title: "Closed historical case",
    description: "Retained closed history.",
    source: "operator",
    source_ref: "closed-01",
    status: "closed",
    priority: "normal",
    owner_agent_id: "backend",
    task_ids: ["task_waiting"],
    tags: ["history"],
    created_at: "2026-06-08T00:00:00Z",
    updated_at: "2026-06-08T01:00:00Z",
  };

  const { port, state } = await createTeamAddonServer(t, { initialState });
  const page = await openBrowserPage(t, port);

  await waitForRevision(page, 8);
  await page.getByRole("tab", { name: "Cases" }).click();
  const casesPage = page.locator("#team-cases-page");
  await casesPage.waitFor({ state: "visible" });
  const caseList = casesPage.locator("[data-case-page-list] .task-item");
  await casesPage.locator('[data-case-id="case-active-01"]').waitFor({ state: "visible" });
  assert.equal(await caseList.count(), 4);
  await casesPage
    .locator(".case-history-summary")
    .getByText("4 active", { exact: true })
    .waitFor({ state: "visible" });
  await casesPage.getByText("21 resolved", { exact: true }).first().waitFor({
    state: "visible",
  });
  await casesPage.getByText("1 closed", { exact: true }).first().waitFor({
    state: "visible",
  });
  await casesPage
    .locator(".case-team-counts")
    .getByText("4 active", { exact: true })
    .waitFor({ state: "visible" });
  assert.equal(await casesPage.locator('[data-case-id^="case-resolved-"]').count(), 0);
  assert.equal(await casesPage.locator('[data-case-id="case-closed-01"]').count(), 0);

  const statusFilter = casesPage.locator("[data-case-status-filter]");
  await statusFilter.selectOption("all");
  assert.equal(await caseList.count(), 20);
  await casesPage
    .getByRole("button", { name: "Hide case history" })
    .waitFor({ state: "visible" });
  assert.ok(
    (await casesPage.locator('[data-case-id^="case-resolved-"]').count()) > 0,
  );
  await casesPage.getByRole("button", { name: "Hide case history" }).click();
  assert.equal(await caseList.count(), 4);

  await casesPage.getByRole("button", { name: "Show case history (22)" }).click();
  assert.equal(await caseList.count(), 20);
  await statusFilter.selectOption("active");
  assert.equal(await caseList.count(), 4);
  await casesPage
    .getByRole("button", { name: "Show case history (22)" })
    .waitFor({ state: "visible" });
  const activeSearchForm = casesPage.locator("[data-case-search-form]");
  await activeSearchForm.locator('[name="query"]').fill("Active");
  await activeSearchForm.getByRole("button", { name: "Search" }).click();
  await casesPage.getByRole("button", { name: "Clear filters" }).click();
  assert.equal(await statusFilter.inputValue(), "active");
  await statusFilter.selectOption("all");
  assert.equal(await caseList.count(), 20);
  await casesPage.getByText(/Showing 1.20 of 26/).waitFor({ state: "visible" });
  await casesPage.getByRole("button", { name: "Next" }).click();
  assert.equal(await caseList.count(), 6);
  await casesPage.locator('[data-case-id="case-closed-01"]').waitFor({ state: "visible" });

  await statusFilter.selectOption("resolved");
  assert.equal(await caseList.count(), 20);
  await casesPage.getByText(/Showing 1.20 of 21/).waitFor({ state: "visible" });
  await casesPage.getByRole("button", { name: "Next" }).click();
  assert.equal(await caseList.count(), 1);

  const searchForm = casesPage.locator("[data-case-search-form]");
  await searchForm.locator('[name="query"]').fill("durable-record-sentinel");
  await searchForm.getByRole("button", { name: "Search" }).click();
  assert.equal(await caseList.count(), 1);
  const durableRecord = casesPage.locator('[data-case-id="case-resolved-21"]');
  await durableRecord.getByText("Durable record sentinel", { exact: true }).waitFor({
    state: "visible",
  });
  await durableRecord.getByText("Tasks Build API (task_open, In Progress)", { exact: true }).waitFor({
    state: "visible",
  });
  assert.equal(state.cases["case-resolved-21"].status, "resolved");
  assert.deepEqual(state.cases["case-resolved-21"].task_ids, ["task_open"]);

  await casesPage.getByRole("button", { name: "Clear filters" }).click();
  await statusFilter.selectOption("closed");
  assert.equal(await caseList.count(), 1);
  await casesPage.locator('[data-case-id="case-closed-01"]').waitFor({ state: "visible" });

  await casesPage.getByRole("button", { name: "Hide case history" }).click();
  assert.equal(await caseList.count(), 4);
  assert.equal(await casesPage.locator('[data-case-id^="case-resolved-"]').count(), 0);
  assert.equal(await casesPage.locator('[data-case-id="case-closed-01"]').count(), 0);
});

test("team add-on UI retries verification failures with an operator comment", async (t) => {
  const initialState = seededGitState();
  initialState.release_packages["release-transient"] = {
    id: "release-transient",
    project_id: "alpha-project",
    deliverable_ids: ["deliverable-accepted"],
    artifact_refs: [],
    feature_branches: ["team/alpha/features/accepted"],
    release_branch: "team/alpha/releases/transient",
    canonical_branch: "releases/alpha/transient",
    candidate_commit_sha: "candidate-sha",
    state: "blocked",
    error: "verification worker disconnected",
    gate_failure: {
      stage: "verification",
      kind: "infrastructure",
      source_id: "verification-attempt-1",
      message: "verification worker disconnected",
    },
    created_at: "2026-06-09T16:00:00Z",
    updated_at: "2026-06-09T16:05:00Z",
  };
  initialState.release_packages["release-code-failure"] = {
    ...initialState.release_packages["release-transient"],
    id: "release-code-failure",
    state: "rejected",
    error: "operator-client tests failed",
    gate_failure: {
      stage: "verification",
      kind: "candidate",
      source_id: "candidate-attempt-1",
      message: "operator-client tests failed",
    },
    created_at: "2026-06-09T15:50:00Z",
    updated_at: "2026-06-09T15:55:00Z",
  };
  const { port, requests, state: serverState } = await createTeamAddonServer(t, { initialState });
  const page = await openBrowserPage(t, port);

  await waitForRevision(page, 4);
  await openProjects(page);
  const releases = page.locator(
    '#team-projects-page details.project-record-section[data-project-section="releases"]',
  ).first();
  await releases.locator("summary.project-record-section-summary").click();
  const transient = releases.locator('li.release-card[data-release-id="release-transient"]');
  await transient.locator("summary.compact-record-summary").click();
  const retry = transient.getByRole("button", { name: "Retry verification gate" });
  await retry.waitFor({ state: "visible" });

  page.once("dialog", (dialog) => dialog.accept("Worker host was repaired by release-admin."));
  await retry.click();
  await waitForRevision(page, 5);

  const request = requests.find(
    (item) => item.method === "POST" &&
      item.pathname === "/api/addons/team-addon/release-packages/release-transient/retry-gate",
  );
  assert.ok(request, "expected a release gate retry request");
  assert.deepEqual(request.payload, {
    gate_kind: "verification",
    gate_id: "verification-attempt-1",
    reason: "Worker host was repaired by release-admin.",
    expected_revision: 4,
  });

  await releases.getByRole("button", { name: "Show rejected" }).click();
  const failed = releases.locator('li.release-card[data-release-id="release-code-failure"]');
  await failed.locator("summary.compact-record-summary").click();
  const operatorRetry = failed.getByRole("button", { name: "Retry verification gate" });
  await operatorRetry.waitFor({ state: "visible" });
  page.once("dialog", (dialog) => {
    assert.match(dialog.message(), /Add a comment/);
    return dialog.dismiss();
  });
  await operatorRetry.click();
  page.once("dialog", (dialog) => dialog.accept("   "));
  await operatorRetry.click();
  assert.equal(requests.filter((item) => item.pathname.endsWith("release-code-failure/retry-gate")).length, 0);
  page.once("dialog", (dialog) => dialog.accept("Retry after repairing the toolchain."));
  await operatorRetry.click();
  await waitForRevision(page, 6);
  const candidateRequest = requests.find((item) => item.method === "POST" &&
    item.pathname.endsWith("release-code-failure/retry-gate"));
  assert.deepEqual(candidateRequest?.payload, {
    gate_kind: "verification",
    gate_id: "candidate-attempt-1",
    reason: "Retry after repairing the toolchain.",
    allow_rejected_verification_retry: true,
    expected_revision: 5,
  });
  serverState.release_packages["release-code-failure"].verification_runs = [{
    id: "candidate-attempt-2", attempt_number: 2, state: "pending",
    candidate_commit_sha: "candidate-sha",
    retry_reason: candidateRequest.payload.reason,
    retry_requested_by: "operator:jonathan",
  }];
  await failed.getByRole("button", { name: "Open release detail" }).click();
  const detail = failed.locator(".release-review-detail");
  await detail.getByText("Retry comment: Retry after repairing the toolchain.", { exact: true })
    .waitFor({ state: "visible" });
  await detail.getByText("Requested by operator:jonathan", { exact: true })
    .waitFor({ state: "visible" });
});

test("team add-on UI submits an explicit rejected-review exception", async (t) => {
  const initialState = seededGitState();
  initialState.release_packages = {
    "release-review-exception": {
      id: "release-review-exception",
      project_id: "alpha-project",
      deliverable_ids: ["deliverable-accepted"],
      artifact_refs: [],
      feature_branches: ["team/alpha/features/accepted"],
      release_branch: "team/alpha/releases/review-exception",
      canonical_branch: "releases/alpha/review-exception",
      candidate_commit_sha: "candidate-sha",
      state: "rejected",
      error: "sealed candidate metadata cannot be rewritten",
      gate_failure: {
        stage: "review",
        kind: "rejected",
        source_id: "review-rejected-1",
        message: "sealed candidate metadata cannot be rewritten",
      },
      created_at: "2026-06-09T16:20:00Z",
      updated_at: "2026-06-09T16:20:00Z",
    },
  };
  const { port, requests } = await createTeamAddonServer(t, { initialState });
  const page = await openBrowserPage(t, port);

  await waitForRevision(page, 4);
  await page.getByRole("tab", { name: "Delivery" }).click();
  await page
    .locator("#team-delivery-page")
    .getByRole("heading", { name: "Release history" })
    .waitFor({ state: "visible" });
  const releaseHistory = page.locator("#team-delivery-page [data-releases-view]");
  const reviewException = releaseHistory.locator(
    'li.release-card[data-release-id="release-review-exception"]',
  );
  await reviewException.locator("summary.compact-record-summary").click();
  const exceptionRetry = reviewException.getByRole("button", {
    name: "Retry rejected review by exception",
  });
  await exceptionRetry.waitFor({ state: "visible" });
  page.once("dialog", (dialog) => dialog.accept("The immutable inherited metadata is accepted."));
  await exceptionRetry.click();
  await waitForRevision(page, 5);

  const exceptionRequest = requests.find(
    (item) => item.method === "POST" &&
      item.pathname === "/api/addons/team-addon/release-packages/release-review-exception/retry-gate",
  );
  assert.ok(exceptionRequest, "expected a rejected release-review exception request");
  assert.deepEqual(exceptionRequest.payload, {
    gate_kind: "review",
    gate_id: "review-rejected-1",
    reason: "The immutable inherited metadata is accepted.",
    allow_rejected_review_exception: true,
    expected_revision: 4,
  });
});

test("team add-on focused tabs submit retained create forms with revisions", async (t) => {
  const { port, requests } = await createTeamAddonServer(t);
  const page = await openBrowserPage(t, port);
  const teamId = "team-team";
  const managerId = "agent-team-team-team-manager";
  const projectId = "project-team-team-project";
  const roleId = "role-project-team-team-project-backend";
  const backendId = "agent-project-team-team-project-backend-agent";

  await openWorkspace(page);
  await page.locator("#team-workspace-page").getByRole("heading", { name: "Team Dashboard" }).waitFor({
    state: "visible",
  });
  await page.locator("#team-workspace-page").getByText("No active teams are registered.").waitFor({
    state: "visible",
  });
  for (const removedFormId of [
    "setup-template-form",
    "create-task-form",
    "update-task-form",
    "record-deliverable-form",
    "record-review-form",
    "create-case-form",
  ]) {
    assert.equal(await page.locator(`#team-workspace-page #${removedFormId}`).count(), 0);
  }

  await openTeams(page);
  await page.locator("#team-tab-create-team-action").click();
  const teamForm = page.locator("#create-team-from-teams-form");
  await teamForm.waitFor({ state: "visible" });
  await teamForm.locator('[name="name"]').fill("Team");
  await teamForm.locator('[name="manager_session_id"]').fill("session-manager");
  await teamForm.locator('[name="manager_display_name"]').fill("Team Manager");
  await teamForm.locator('[name="operator_communication_target_id"]').fill("ops-channel");
  await teamForm.getByRole("button", { name: "Create team" }).click();
  await waitForStatusText(page, "Saved revision", requests);

  await openProjects(page);
  await page.locator("[data-project-create-toggle]").click();
  const projectForm = page.locator("#create-project-from-projects-form");
  await projectForm.waitFor({ state: "visible" });
  await projectForm.locator('[name="team_id"]').selectOption(teamId);
  await waitForSelectOption(page, '#create-project-from-projects-form [name="lead_agent_id"]', managerId);
  await projectForm.locator('[name="name"]').fill("Project");
  await projectForm.locator('[name="lead_agent_id"]').selectOption(managerId);
  await projectForm.locator('[name="goal_description"]').fill("Ship reviewed work");
  await projectForm.locator("summary", { hasText: "Git config" }).click();
  await projectForm.locator('[name="managed_repo_id"]').fill("mr_0123456789abcdef0123");
  await projectForm.locator('[name="base_branch"]').fill("main");
  await projectForm.getByRole("button", { name: "Create project" }).click();
  await waitForCondition(
    () => requests.filter((request) => request.method !== "GET").length >= 2,
    "expected project create request",
  );

  await openTeams(page);
  const teamGroup = page.locator(`#team-people-page [data-team-record-group-id="${teamId}"]`);
  await teamGroup.waitFor({ state: "visible" });
  if (!(await teamGroup.locator(".team-record-group-body").isVisible().catch(() => false))) {
    await teamGroup.locator(".team-record-group-header").click();
  }
  await teamGroup.locator(".team-record-group-body").waitFor({ state: "visible" });

  await teamGroup.locator('[data-team-record-tab="role"]').click();
  await teamGroup.getByRole("button", { name: "Add role" }).click();
  const inlineRoleForm = teamGroup.locator('form[data-form-id="create-role-form"]');
  await inlineRoleForm.waitFor({ state: "visible" });
  await inlineRoleForm.locator('[name="project_id"]').selectOption(projectId);
  await inlineRoleForm.locator('[name="name"]').fill("Backend");
  await inlineRoleForm.locator('[name="max_concurrent"]').fill("2");
  await inlineRoleForm.getByRole("button", { name: "Create role" }).click();
  await waitForCondition(
    () => requests.filter((request) => request.method !== "GET").length >= 3,
    "expected role create request",
  );

  await teamGroup.locator('[data-team-record-tab="agent"]').click();
  await teamGroup.getByRole("button", { name: "Add agent" }).click();
  const inlineAgentForm = teamGroup.locator('form[data-form-id="register-agent-form"]');
  await inlineAgentForm.waitFor({ state: "visible" });
  await inlineAgentForm.locator('[name="project_id"]').selectOption(projectId);
  await inlineAgentForm.locator('[name="role_id"]').selectOption(roleId);
  await inlineAgentForm.locator('[name="session_id"]').fill("session-backend");
  await inlineAgentForm.locator('[name="display_name"]').fill("Backend Agent");
  await inlineAgentForm.locator('[name="reports_to_agent_id"]').selectOption(managerId);
  await inlineAgentForm.locator('[name="max_concurrent"]').fill("2");
  await inlineAgentForm.getByRole("button", { name: "Register agent" }).click();
  await waitForCondition(
    () => requests.filter((request) => request.method !== "GET").length >= 4,
    "expected agent create request",
  );
  await teamGroup.locator(`[data-agent-id="${backendId}"]`).waitFor({ state: "visible" });

  const mutationRequests = requests.filter((request) => request.method !== "GET");
  assert.deepEqual(
    mutationRequests.map((request) => [request.method, request.pathname, request.payload.expected_revision]),
    [
      ["POST", "/api/addons/team-addon/teams", 0],
      ["POST", "/api/addons/team-addon/projects", 1],
      ["POST", "/api/addons/team-addon/roles", 2],
      ["POST", "/api/addons/team-addon/agents", 3],
    ],
  );
  assert.equal(mutationRequests[0].payload.manager_display_name, "Team Manager");
  assert.equal(mutationRequests[0].payload.operator_communication_target_id, "ops-channel");
  assert.equal(mutationRequests[1].payload.team_id, teamId);
  assert.equal(mutationRequests[1].payload.lead_agent_id, managerId);
  assert.deepEqual(mutationRequests[1].payload.git, {
    managed_repo_id: "mr_0123456789abcdef0123",
    base_branch: "main",
  });
  assert.equal(mutationRequests[2].payload.team_id, teamId);
  assert.equal(mutationRequests[2].payload.project_id, projectId);
  assert.equal(mutationRequests[2].payload.ephemeral_policy.max_concurrent, 2);
  assert.equal(mutationRequests[3].payload.team_id, teamId);
  assert.equal(mutationRequests[3].payload.project_id, projectId);
  assert.equal(mutationRequests[3].payload.role_id, roleId);
  assert.equal(mutationRequests[3].payload.display_name, "Backend Agent");
  assert.equal(mutationRequests[3].payload.reports_to_agent_id, managerId);
  assert.equal(mutationRequests[3].payload.ephemeral_policy.max_concurrent, 2);
});

test("team add-on agent registration can select an existing core session", async (t) => {
  const { port, requests } = await createTeamAddonServer(t, {
    initialState: seededOrgChartState(),
  });
  const page = await openBrowserPage(t, port);

  await waitForRevision(page, 8);
  await openTeams(page);
  const teamGroup = page.locator('#team-people-page [data-team-record-group-id="team"]');
  await teamGroup.waitFor({ state: "visible" });
  if (!(await teamGroup.locator(".team-record-group-body").isVisible().catch(() => false))) {
    await teamGroup.locator(".team-record-group-header").click();
  }
  await teamGroup.locator(".team-record-group-body").waitFor({ state: "visible" });

  await teamGroup.locator('[data-team-record-tab="agent"]').click();
  await teamGroup.getByRole("button", { name: "Add agent" }).click();
  const inlineAgentForm = teamGroup.locator('form[data-form-id="register-agent-form"]');
  await inlineAgentForm.waitFor({ state: "visible" });
  await waitForSelectOption(page, 'form[data-form-id="register-agent-form"] [name="existing_session_id"]', "session-existing-backend");

  await inlineAgentForm.locator('[name="project_id"]').selectOption("project");
  await inlineAgentForm.locator('[name="role_id"]').selectOption("project__backend");
  await inlineAgentForm.locator('[name="existing_session_id"]').selectOption("session-existing-backend");
  await inlineAgentForm.locator('[name="display_name"]').fill("Existing Core Agent");
  await inlineAgentForm.locator('[name="reports_to_agent_id"]').selectOption("lead");
  await inlineAgentForm.getByRole("button", { name: "Register agent" }).click();

  await waitForCondition(
    () => requests.some((request) => request.method === "POST" && request.pathname === "/api/addons/team-addon/agents"),
    "expected Team agent registration request",
  );

  const registerRequest = requests.find((request) =>
    request.method === "POST" && request.pathname === "/api/addons/team-addon/agents"
  );
  assert.ok(registerRequest);
  assert.equal(registerRequest.payload.session_id, "session-existing-backend");
  assert.equal(registerRequest.payload.display_name, "Existing Core Agent");
  assert.ok(
    requests.some((request) =>
      request.method === "GET" &&
      request.pathname === "/api/admin/agents" &&
      request.search.includes("directory_path=root")
    ),
  );
});

test("team add-on agent registration can create and auto-register a core session", async (t) => {
  const { port, requests } = await createTeamAddonServer(t, {
    initialState: seededOrgChartState(),
  });
  const page = await openBrowserPage(t, port);

  await waitForRevision(page, 8);
  await openTeams(page);
  const teamGroup = page.locator('#team-people-page [data-team-record-group-id="team"]');
  await teamGroup.waitFor({ state: "visible" });
  if (!(await teamGroup.locator(".team-record-group-body").isVisible().catch(() => false))) {
    await teamGroup.locator(".team-record-group-header").click();
  }
  await teamGroup.locator(".team-record-group-body").waitFor({ state: "visible" });

  await teamGroup.locator('[data-team-record-tab="agent"]').click();
  await teamGroup.getByRole("button", { name: "Add agent" }).click();
  const inlineAgentForm = teamGroup.locator('form[data-form-id="register-agent-form"]');
  await inlineAgentForm.waitFor({ state: "visible" });
  await inlineAgentForm.locator('[name="project_id"]').selectOption("project");
  await inlineAgentForm.locator('[name="role_id"]').selectOption("project__backend");
  await inlineAgentForm.locator('[name="display_name"]').fill("Created Core Agent");
  await inlineAgentForm.locator('[name="reports_to_agent_id"]').selectOption("lead");

  await inlineAgentForm.getByRole("button", { name: "Create new session" }).click();
  const createDialog = page.locator('[role="dialog"][aria-label="Create and register agent"]');
  await createDialog.waitFor({ state: "visible" });
  await createDialog.locator(".agent-capability-group").waitFor({ state: "visible" });
  assert.equal(await createDialog.locator(".agent-capability-option").count(), 5);
  for (const label of [".NET", "NuGet", "MSSQL", "Playwright", "Web search"]) {
    await createDialog.locator(".agent-capability-option", { hasText: label }).waitFor({ state: "visible" });
  }
  const backendSelect = createDialog.locator("label", { hasText: "Backend" }).locator("select");
  await backendSelect.selectOption("container_worker_vm");
  assert.equal(await createDialog.locator(".agent-capability-option").count(), 4);
  for (const label of [".NET", "NuGet", "Playwright", "Web search"]) {
    await createDialog.locator(".agent-capability-option", { hasText: label }).waitFor({ state: "visible" });
  }
  assert.equal(await createDialog.locator(".agent-capability-option", { hasText: "MSSQL" }).count(), 0);
  await backendSelect.selectOption("libvirt_vm");
  assert.equal(await createDialog.locator(".agent-capability-option").count(), 5);
  assert.equal(await createDialog.getByRole("textbox", { name: /MSSQL init root/ }).isVisible().catch(() => false), false);
  const mssqlCapability = createDialog.locator(".agent-capability-option", { hasText: "MSSQL" });
  await mssqlCapability.locator('input[type="checkbox"]').check();
  await createDialog.locator(".agent-capability-detail-row").getByRole("textbox", { name: /MSSQL init root/ }).waitFor({ state: "visible" });
  await mssqlCapability.locator('input[type="checkbox"]').uncheck();
  await createDialog.getByRole("textbox", { name: /MSSQL init root/ }).waitFor({ state: "hidden" });
  const communicationTargetTable = createDialog.locator(".agent-communication-target-table");
  await communicationTargetTable.waitFor({ state: "visible" });
  await communicationTargetTable.getByRole("columnheader", { name: "Provider" }).waitFor({ state: "visible" });
  await communicationTargetTable.getByRole("cell", { name: "Teams", exact: true }).waitFor({ state: "visible" });
  assert.equal(await communicationTargetTable.locator('tbody input[type="checkbox"]').count(), 1);
  await createDialog.locator("label", { hasText: "VM name" }).locator("input").fill("vm-team-created");
  await createDialog.getByRole("button", { name: "Create and register" }).click();
  await createDialog.locator(".error-callout", { hasText: "Session id is required" }).waitFor({ state: "visible" });
  assert.equal(
    requests.some((request) => request.method === "POST" && request.pathname === "/api/admin/agents/create-operations"),
    false,
  );
  await createDialog.locator('input[placeholder="Required"]').fill("session-team-created");
  await createDialog.getByRole("button", { name: "Create and register" }).click();

  await waitForCondition(
    () =>
      requests.some((request) => request.method === "POST" && request.pathname === "/api/admin/agents/create-operations") &&
      requests.some((request) => request.method === "POST" && request.pathname === "/api/addons/team-addon/agents"),
    "expected core create and Team registration requests",
    { timeoutMs: 5000 },
  );

  const createRequest = requests.find((request) =>
    request.method === "POST" && request.pathname === "/api/admin/agents/create-operations"
  );
  assert.ok(createRequest);
  assert.equal(createRequest.payload.backend, "libvirt_vm");
  assert.equal(createRequest.payload.vm_name, "vm-team-created");
  assert.equal(createRequest.payload.session_id, "session-team-created");

  const registerRequest = requests.find((request) =>
    request.method === "POST" && request.pathname === "/api/addons/team-addon/agents"
  );
  assert.ok(registerRequest);
  assert.equal(registerRequest.payload.session_id, "session-team-created");
  assert.equal(registerRequest.payload.display_name, "Created Core Agent");
});

test("team add-on agent registration blocks core creation until Team fields are valid", async (t) => {
  const { port, requests } = await createTeamAddonServer(t, {
    initialState: seededOrgChartState(),
  });
  const page = await openBrowserPage(t, port);

  await waitForRevision(page, 8);
  await openTeams(page);
  const teamGroup = page.locator('#team-people-page [data-team-record-group-id="team"]');
  await teamGroup.waitFor({ state: "visible" });
  if (!(await teamGroup.locator(".team-record-group-body").isVisible().catch(() => false))) {
    await teamGroup.locator(".team-record-group-header").click();
  }
  await teamGroup.locator(".team-record-group-body").waitFor({ state: "visible" });

  await teamGroup.locator('[data-team-record-tab="agent"]').click();
  await teamGroup.getByRole("button", { name: "Add agent" }).click();
  const inlineAgentForm = teamGroup.locator('form[data-form-id="register-agent-form"]');
  await inlineAgentForm.waitFor({ state: "visible" });
  await inlineAgentForm.getByRole("button", { name: "Create new session" }).click();

  await page.getByText("Select a role before creating a core agent session.").waitFor({ state: "visible" });
  await page.waitForTimeout(100);
  assert.equal(
    await page.locator('[role="dialog"][aria-label="Create and register agent"]').isVisible().catch(() => false),
    false,
  );
  assert.equal(
    requests.some((request) => request.method === "POST" && request.pathname === "/api/admin/agents/create-operations"),
    false,
  );
});

test("team add-on agent auto-registration preserves conflict retry after core creation", async (t) => {
  const { port, requests } = await createTeamAddonServer(t, {
    initialState: seededOrgChartState(),
    conflictOnFirstAgentRegistration: true,
  });
  const page = await openBrowserPage(t, port);

  await waitForRevision(page, 8);
  await openTeams(page);
  const teamGroup = page.locator('#team-people-page [data-team-record-group-id="team"]');
  await teamGroup.waitFor({ state: "visible" });
  if (!(await teamGroup.locator(".team-record-group-body").isVisible().catch(() => false))) {
    await teamGroup.locator(".team-record-group-header").click();
  }
  await teamGroup.locator(".team-record-group-body").waitFor({ state: "visible" });

  await teamGroup.locator('[data-team-record-tab="agent"]').click();
  await teamGroup.getByRole("button", { name: "Add agent" }).click();
  const inlineAgentForm = teamGroup.locator('form[data-form-id="register-agent-form"]');
  await inlineAgentForm.waitFor({ state: "visible" });
  await inlineAgentForm.locator('[name="project_id"]').selectOption("project");
  await inlineAgentForm.locator('[name="role_id"]').selectOption("project__backend");
  await inlineAgentForm.locator('[name="display_name"]').fill("Conflict Core Agent");
  await inlineAgentForm.locator('[name="reports_to_agent_id"]').selectOption("lead");

  await inlineAgentForm.getByRole("button", { name: "Create new session" }).click();
  const createDialog = page.locator('[role="dialog"][aria-label="Create and register agent"]');
  await createDialog.waitFor({ state: "visible" });
  await createDialog.locator("label", { hasText: "VM name" }).locator("input").fill("vm-team-conflict");
  await createDialog.locator('input[placeholder="Required"]').fill("session-team-conflict");
  await createDialog.getByRole("button", { name: "Create and register" }).click();

  await page.getByText(/Revision conflict/).waitFor({ state: "visible" });
  await page.getByRole("button", { name: "Retry save" }).waitFor({ state: "visible" });
  await page.getByRole("button", { name: "Retry save" }).click();
  await waitForRevision(page, 10);

  const createRequests = requests.filter(
    (request) => request.method === "POST" && request.pathname === "/api/admin/agents/create-operations",
  );
  assert.equal(createRequests.length, 1);
  const registrationRequests = requests.filter(
    (request) => request.method === "POST" && request.pathname === "/api/addons/team-addon/agents",
  );
  assert.equal(registrationRequests.length, 2);
  assert.equal(registrationRequests[0].payload.session_id, "session-team-conflict");
  assert.equal(registrationRequests[0].payload.expected_revision, 8);
  assert.equal(registrationRequests[1].payload.session_id, "session-team-conflict");
  assert.equal(registrationRequests[1].payload.expected_revision, 9);
});

test("team add-on agent registration keeps core creation locked with privileges locked", async (t) => {
  const { port, requests } = await createTeamAddonServer(t, {
    initialState: seededOrgChartState(),
  });
  const page = await openBrowserPage(t, port, {}, "/addons/team-addon/?privileged_unlocked=false");

  await waitForRevision(page, 8);
  await openTeams(page);
  const teamGroup = page.locator('#team-people-page [data-team-record-group-id="team"]');
  await teamGroup.waitFor({ state: "visible" });
  if (!(await teamGroup.locator(".team-record-group-body").isVisible().catch(() => false))) {
    await teamGroup.locator(".team-record-group-header").click();
  }
  await teamGroup.locator(".team-record-group-body").waitFor({ state: "visible" });

  await teamGroup.locator('[data-team-record-tab="agent"]').click();
  const addAgentButton = teamGroup.getByRole("button", { name: "Add agent" });
  await addAgentButton.waitFor({ state: "visible" });
  assert.equal(await addAgentButton.isDisabled(), true);
  assert.equal(await addAgentButton.getAttribute("title"), "Unlock privileged operations to add Team records.");
  await addAgentButton.dispatchEvent("click");
  await page.waitForTimeout(100);
  assert.equal(await teamGroup.locator('form[data-form-id="register-agent-form"]').count(), 0);

  await page.evaluate(() => {
    window.dispatchEvent(
      new CustomEvent("team-agent-create:open", {
        detail: { contextId: "locked-create", directoryPath: "root" },
      }),
    );
  });
  const createDialog = page.locator('[role="dialog"][aria-label="Create and register agent"]');
  await createDialog.waitFor({ state: "visible" });
  assert.equal(await createDialog.getByRole("button", { name: "Create and register" }).isDisabled(), true);
  await page.waitForTimeout(100);
  assert.equal(
    requests.some((request) => request.method === "POST" && request.pathname === "/api/admin/agents/create-operations"),
    false,
  );
});

test("team add-on locked mode disables mutation entry points", async (t) => {
  const { port, requests } = await createTeamAddonServer(t, {
    initialState: seededOrgChartState(),
  });
  const page = await openBrowserPage(t, port, {}, "/addons/team-addon/?privileged_unlocked=false");

  await waitForRevision(page, 8);
  await openTeams(page);

  const createTeamButton = page.locator("#team-tab-create-team-action");
  await createTeamButton.waitFor({ state: "visible" });
  assert.equal(await createTeamButton.isDisabled(), true);
  assert.equal(await createTeamButton.getAttribute("title"), "Unlock privileged operations to create teams.");
  await createTeamButton.dispatchEvent("click");
  await page.waitForTimeout(100);
  await page.locator("#teams-create-team-panel").waitFor({ state: "hidden" });

  const copyTeamButton = page.locator('#people-agent-detail-view [data-team-copy-open="team"]');
  await copyTeamButton.waitFor({ state: "visible" });
  assert.equal(await copyTeamButton.isDisabled(), true);
  assert.equal(await copyTeamButton.getAttribute("title"), "Unlock privileged operations to copy teams.");
  await copyTeamButton.dispatchEvent("click");
  await page.waitForTimeout(100);
  assert.equal(await page.locator("#org-action-modal").isVisible().catch(() => false), false);

  const teamGroup = page.locator('#team-people-page [data-team-record-group-id="team"]');
  await teamGroup.waitFor({ state: "visible" });
  if (!(await teamGroup.locator(".team-record-group-body").isVisible().catch(() => false))) {
    await teamGroup.locator(".team-record-group-header").click();
  }
  await teamGroup.locator(".team-record-group-body").waitFor({ state: "visible" });
  await teamGroup.locator('[data-team-record-tab="agent"]').click();
  const addAgentButton = teamGroup.getByRole("button", { name: "Add agent" });
  await addAgentButton.waitFor({ state: "visible" });
  assert.equal(await addAgentButton.isDisabled(), true);
  assert.equal(await addAgentButton.getAttribute("title"), "Unlock privileged operations to add Team records.");
  await addAgentButton.dispatchEvent("click");
  await page.waitForTimeout(100);
  assert.equal(await teamGroup.locator('form[data-form-id="register-agent-form"]').count(), 0);

  await page.locator("#team-view-projects-tab").click();
  await page.locator("#team-projects-page").waitFor({ state: "visible" });
  const projectCreateButton = page.locator("[data-project-create-toggle]");
  await projectCreateButton.waitFor({ state: "visible" });
  assert.equal(await projectCreateButton.isDisabled(), true);
  assert.equal(await projectCreateButton.getAttribute("title"), "Unlock privileged operations to create projects.");
  await projectCreateButton.dispatchEvent("click");
  await page.waitForTimeout(100);
  await page.locator("#create-project-from-projects-form").waitFor({ state: "hidden" });

  await openOrgChart(page);
  const backendCard = page.locator(".org-focus-node-card").filter({
    has: page.locator(".hierarchy-node-title strong", { hasText: /^Backend Worker$/ }),
  });
  await backendCard.waitFor({ state: "visible" });
  await backendCard.click({ button: "right" });
  const subordinateMenuItem = page.getByRole("menuitem", { name: "Add subordinate agent" });
  await subordinateMenuItem.waitFor({ state: "visible" });
  assert.equal(await subordinateMenuItem.isDisabled(), true);
  assert.equal(
    await subordinateMenuItem.getAttribute("title"),
    "Unlock privileged operations to add subordinate agents.",
  );
  await subordinateMenuItem.dispatchEvent("click");
  await page.waitForTimeout(100);
  assert.equal(await page.locator("#org-action-modal").isVisible().catch(() => false), false);

  const mutationRequests = requests.filter((request) => request.method !== "GET");
  assert.deepEqual(mutationRequests, []);
});

test("team add-on opens linked sessions from Teams and Org chart agents", async (t) => {
  const { port } = await createTeamAddonServer(t, {
    initialState: seededOrgChartState(),
  });
  const page = await openBrowserPage(t, port);

  await page.evaluate(() => {
    window.__teamSessionNavigation = [];
    window.addEventListener("message", (event) => {
      if (event.data?.type === "admin-navigate-to-session") {
        window.__teamSessionNavigation.push(event.data.session_id);
      }
    });
  });

  await waitForRevision(page, 8);
  await openTeams(page);
  const teamGroup = page.locator('#team-people-page [data-team-record-group-id="team"]');
  await teamGroup.waitFor({ state: "visible" });
  if (!(await teamGroup.locator(".team-record-group-body").isVisible().catch(() => false))) {
    await teamGroup.locator(".team-record-group-header").click();
  }
  await teamGroup.locator(".team-record-group-body").waitFor({ state: "visible" });
  await teamGroup.locator('[data-select-agent-id="manager"]').click();
  const managerDetail = page.locator("#team-people-page .agent-detail", { hasText: "Ops Manager" });
  await managerDetail.getByRole("button", { name: "Open session" }).click();
  await page.waitForFunction(() => window.__teamSessionNavigation?.includes("session-manager"));

  await openOrgChart(page);
  const backendCard = page.locator(".org-focus-node-card").filter({
    has: page.locator(".hierarchy-node-title strong", { hasText: /^Backend Worker$/ }),
  });
  await backendCard.waitFor({ state: "visible" });
  await backendCard.click({ button: "right" });
  await page.getByRole("menuitem", { name: "Open session" }).click();
  await page.waitForFunction(() => window.__teamSessionNavigation?.includes("session-backend"));
  assert.deepEqual(
    await page.evaluate(() => window.__teamSessionNavigation),
    ["session-manager", "session-backend"],
  );
});

test("team add-on privilege messages update controls without losing selected manager", async (t) => {
  const initialState = seededOrgChartState();
  const standDown = {
    id: "stand-down-manager",
    agent_id: "manager",
    source: "operator",
    reason: "Pause manager wakeups",
    set_by: "operator",
    set_at: "2026-06-11T08:50:00Z",
    cleared_by: null,
    cleared_at: null,
    active: true,
    inherited: false,
    root_agent_id: "manager",
  };
  initialState.stand_downs = { [standDown.id]: standDown };
  initialState.agents.manager.stand_down_active = true;
  initialState.agents.manager.effective_stand_downs = [standDown];
  const { port, requests } = await createTeamAddonServer(t, {
    initialState,
  });
  const page = await openBrowserPage(t, port, {}, "/addons/team-addon/?privileged_unlocked=false");

  await waitForRevision(page, 8);
  await openTeams(page);
  const teamGroup = page.locator('#team-people-page [data-team-record-group-id="team"]');
  await teamGroup.waitFor({ state: "visible" });
  if (!(await teamGroup.locator(".team-record-group-body").isVisible().catch(() => false))) {
    await teamGroup.locator(".team-record-group-header").click();
  }
  await teamGroup.locator(".team-record-group-body").waitFor({ state: "visible" });
  await teamGroup.locator('[data-select-agent-id="manager"]').click();

  const detail = page.locator("#team-people-page .agent-detail", { hasText: "Ops Manager" });
  await detail.waitFor({ state: "visible" });
  const revokeButton = detail.locator('[data-stand-down-revoke-agent-id="manager"]');
  await revokeButton.waitFor({ state: "visible" });
  await assert.doesNotReject(() => detail.getByRole("button", { name: "Revoke stand-down" }).waitFor({ state: "visible" }));
  assert.equal(await revokeButton.isDisabled(), true);
  const requestCountBeforeMessage = requests.length;

  await page.evaluate(() => {
    window.postMessage(
      {
        type: "admin-privileged-operations",
        privileged_operations: { unlocked: true },
      },
      window.location.origin,
    );
  });
  await page.waitForFunction(() => {
    const detail = document.querySelector("#team-people-page .agent-detail");
    const button = detail?.querySelector('[data-stand-down-revoke-agent-id="manager"]');
    return Boolean(detail?.textContent?.includes("Ops Manager") && button && !button.disabled);
  });
  assert.equal(await detail.locator(".agent-detail-title h3").innerText(), "Ops Manager");
  assert.equal(requests.length, requestCountBeforeMessage);

  await page.evaluate(() => {
    window.postMessage(
      {
        type: "admin-privileged-operations",
        privileged_operations: { unlocked: false },
      },
      window.location.origin,
    );
  });
  await page.waitForFunction(() => {
    const detail = document.querySelector("#team-people-page .agent-detail");
    const button = detail?.querySelector('[data-stand-down-revoke-agent-id="manager"]');
    return Boolean(detail?.textContent?.includes("Ops Manager") && button?.disabled);
  });
});

test("team add-on lets operators revoke direct agent-issued stand-downs and explains inherited holds", async (t) => {
  const initialState = seededOrgChartState();
  initialState.agents.lead.display_name = "Project Lead";
  const standDown = {
    id: "stand-down-backend",
    agent_id: "backend",
    source: "superior",
    reason: "Wait for upstream work",
    set_by: "lead",
    set_at: "2026-06-11T08:50:00Z",
    cleared_by: null,
    cleared_at: null,
    active: true,
    inherited: false,
    root_agent_id: "backend",
  };
  initialState.stand_downs = { [standDown.id]: standDown };
  initialState.agents.backend.stand_down_active = true;
  initialState.agents.backend.effective_stand_downs = [standDown];
  initialState.agents.worker.stand_down_active = true;
  initialState.agents.worker.effective_stand_downs = [
    {
      ...standDown,
      inherited: true,
      root_agent_id: "backend",
    },
  ];
  const { port, requests } = await createTeamAddonServer(t, {
    initialState,
  });
  const page = await openBrowserPage(t, port, {}, "/addons/team-addon/?privileged_unlocked=true");

  await waitForRevision(page, 8);
  await openTeams(page);
  const teamGroup = page.locator('#team-people-page [data-team-record-group-id="team"]');
  await teamGroup.waitFor({ state: "visible" });
  if (!(await teamGroup.locator(".team-record-group-body").isVisible().catch(() => false))) {
    await teamGroup.locator(".team-record-group-header").click();
  }
  await teamGroup.locator(".team-record-group-body").waitFor({ state: "visible" });
  await teamGroup.locator('[data-select-agent-id="backend"]').click();

  const detail = page.locator("#team-people-page .agent-detail", { hasText: "Backend Worker" });
  await detail.waitFor({ state: "visible" });
  const revokeButton = detail.getByRole("button", { name: "Revoke stand-down" });
  await revokeButton.waitFor({ state: "visible" });
  assert.equal(await revokeButton.isDisabled(), false);
  assert.equal(await revokeButton.getAttribute("aria-disabled"), null);

  const revokeRequestCountBeforeClick = requests.filter(
    (request) =>
      request.method === "POST"
      && request.pathname === "/api/addons/team-addon/agents/backend/stand-down/revoke",
  ).length;
  await revokeButton.click();
  await waitForCondition(
    () => requests.filter(
      (request) =>
        request.method === "POST"
        && request.pathname === "/api/addons/team-addon/agents/backend/stand-down/revoke",
    ).length === revokeRequestCountBeforeClick + 1,
    "expected an operator revocation request for the direct agent-issued hold",
  );
  assert.equal(
    requests.filter(
      (request) =>
        request.method === "POST"
        && request.pathname === "/api/addons/team-addon/agents/backend/stand-down/revoke",
    ).length,
    revokeRequestCountBeforeClick + 1,
  );

  await teamGroup.locator('[data-select-agent-id="worker"]').click();
  const inheritedDetail = page.locator("#team-people-page .agent-detail", { hasText: "worker" });
  await inheritedDetail.waitFor({ state: "visible" });
  const inheritedRevokeButton = inheritedDetail.getByRole("button", { name: "Revoke stand-down" });
  await inheritedRevokeButton.waitFor({ state: "visible" });
  await inheritedRevokeButton.hover();
  assert.equal(await inheritedRevokeButton.isDisabled(), true);
  assert.equal(
    await inheritedRevokeButton.getAttribute("title"),
    "This stand-down is inherited from Backend Worker. Revoke it on Backend Worker.",
  );
});

test("team add-on project catalog uses growing cards with concise project metadata", async (t) => {
  const initialState = seededOrgChartState();
  initialState.teams.team.name = "Accounting Department";
  initialState.projects.project.name = "Bookkeeping Operations";
  for (const [id, name, teamId, teamName] of [
    ["launch-operations", "Launch Operations", "launch-team", "Launch Team"],
    ["platform-reliability", "Platform Reliability", "platform-team", "Platform Team"],
    ["customer-operations", "Customer Operations", "customer-team", "Customer Team"],
  ]) {
    initialState.teams[teamId] = {
      id: teamId,
      name: teamName,
      manager_agent_id: "manager",
      operator_communication_target_id: "ops-channel",
      project_ids: [id],
      archived: false,
    };
    initialState.projects[id] = {
      id,
      team_id: teamId,
      name,
      lead_agent_id: "lead",
      goals: [],
      git: { managed_repo_id: `mr_${id}`, base_branch: "main" },
      status: "active",
    };
  }

  const { port } = await createTeamAddonServer(t, { initialState });
  const page = await openBrowserPage(t, port, { viewport: { width: 1280, height: 720 } });

  await waitForRevision(page, 8);
  await openProjects(page);
  const cards = page.locator("#team-projects-page button.projects-focus-row");
  await cards.first().waitFor({ state: "visible" });
  assert.equal(await cards.count(), 4);

  const bookkeepingCard = page.locator('[data-project-focus-id="project"]');
  assert.equal(await bookkeepingCard.locator(".projects-focus-row-primary").count(), 1);
  assert.equal(await bookkeepingCard.locator(".projects-focus-row-team").innerText(), "Accounting Department");
  assert.equal(await bookkeepingCard.getByText("team", { exact: true }).count(), 0);
  assert.equal(await bookkeepingCard.getByText("profiles", { exact: false }).count(), 0);
  assert.equal(await bookkeepingCard.locator(".state-pill.active").count(), 1);

  const layout = await cards.evaluateAll((elements) =>
    elements.map((element) => {
      const title = element.querySelector(".projects-focus-row-primary strong");
      const titleRange = document.createRange();
      titleRange.selectNodeContents(title);
      return {
        width: element.getBoundingClientRect().width,
        titleLineCount: titleRange.getClientRects().length,
      };
    }),
  );
  assert.ok(layout.every(({ width }) => width > 250), `expected growing cards: ${JSON.stringify(layout)}`);
  assert.equal(layout[0].titleLineCount, 1, `expected Bookkeeping Operations to stay intact: ${JSON.stringify(layout[0])}`);
});

test("team add-on review profiles and task descendants prioritize display names", async (t) => {
  const initialState = seededOrgChartState();
  initialState.teams.team.name = "Accounting Department";
  initialState.projects.project.name = "Bookkeeping Operations";
  initialState.roles.project__backend.name = "Bookkeeping Reviewer";
  initialState.outcome_kind_definitions = {
    "bokio-prepared-action": {
      id: "bokio-prepared-action",
      team_id: "team",
      project_id: "project",
      kind: "bokio_prepared_action",
      name: "Bokio Prepared Action",
      description: "A reviewed draft that is ready for operator approval.",
      archived: false,
    },
  };
  initialState.review_profiles = {
    "bookkeeping-review-profile": {
      id: "bookkeeping-review-profile",
      team_id: "team",
      project_id: "project",
      name: "Bookkeeping preparation review",
      description: "Confirm the draft is supported and safe to hand to an operator.",
      applicable_role_ids: ["project__backend"],
      required_outcome_kinds: ["bokio_prepared_action"],
      criteria: [
        {
          id: "supporting_documentation",
          label: "Supporting Documentation",
          description: "The source document and bookkeeping rationale are attached.",
          required: true,
        },
        {
          id: "duplicate_check",
          label: "Duplicate Check",
          description: "Existing Bokio records were checked before preparation.",
          required: false,
        },
      ],
      archived: false,
    },
  };
  const descendantIds = Array.from(
    { length: 26 },
    (_, index) => `task-descendant-${String(index + 1).padStart(2, "0")}`,
  );
  initialState.tasks.task_open.child_task_ids = descendantIds;
  initialState.tasks.task_open.assessment = {
    direct_child_ids: descendantIds,
    unresolved_descendant_ids: descendantIds,
  };
  descendantIds.forEach((id, index) => {
    initialState.tasks[id] = {
      id,
      owner_agent_id: "worker",
      project_id: "project",
      priority: index + 10,
      state: "in_progress",
      title: `Bookkeeping follow-up ${index + 1}`,
      description: "Unresolved descendant used to exercise task-board hierarchy.",
      created_by_agent_id: "backend",
      parent_task_id: "task_open",
      created_at: "2026-06-11T08:30:00Z",
      updated_at: "2026-06-11T08:45:00Z",
    };
  });

  const { port } = await createTeamAddonServer(t, { initialState });
  const page = await openBrowserPage(t, port, { viewport: { width: 1280, height: 900 } });

  await waitForRevision(page, 8);
  await openProjects(page);
  const profileCard = page.locator("#team-projects-page .review-profile-card", {
    hasText: "Bookkeeping preparation review",
  });
  await profileCard.waitFor({ state: "visible" });
  await profileCard.getByText("Bookkeeping Operations", { exact: false }).waitFor({
    state: "visible",
  });
  await profileCard.getByText("2 criteria", { exact: false }).waitFor({ state: "visible" });
  await profileCard.getByText("active", { exact: true }).waitFor({ state: "visible" });
  await profileCard.getByRole("button", { name: "Edit" }).waitFor({ state: "visible" });
  assert.equal(await profileCard.getByText("bookkeeping-review-profile", { exact: true }).isVisible(), false);
  assert.equal(await profileCard.getByText("project", { exact: true }).isVisible(), false);

  await profileCard.getByText("View profile", { exact: true }).click();
  for (const heading of ["Purpose", "Reviewers", "Required outcomes", "Criteria"]) {
    await profileCard.getByRole("heading", { name: heading }).waitFor({ state: "visible" });
  }
  await profileCard.getByText("Bookkeeping Reviewer", { exact: true }).waitFor({ state: "visible" });
  await profileCard.getByText("Bokio Prepared Action", { exact: true }).waitFor({ state: "visible" });
  await profileCard.getByText("Supporting Documentation", { exact: true }).waitFor({ state: "visible" });
  await profileCard.getByText("Required", { exact: true }).waitFor({ state: "visible" });
  await profileCard.getByText("Optional", { exact: true }).waitFor({ state: "visible" });
  assert.equal(await profileCard.getByText("bokio_prepared_action", { exact: true }).isVisible(), false);
  assert.equal(await profileCard.getByText("supporting_documentation", { exact: true }).isVisible(), false);

  await profileCard.getByText("Technical details", { exact: true }).click();
  await profileCard.getByText("bookkeeping-review-profile", { exact: true }).waitFor({
    state: "visible",
  });
  await profileCard.getByText("bokio_prepared_action", { exact: true }).waitFor({
    state: "visible",
  });
  await profileCard.getByText("supporting_documentation", { exact: true }).waitFor({
    state: "visible",
  });

  const projectsProfileDisclosure = profileCard.locator(
    ":scope > details.review-profile-card-details",
  );
  const projectsProfileKey = await projectsProfileDisclosure.getAttribute(
    "data-team-disclosure-key",
  );
  await page.getByRole("tab", { name: "Delivery" }).click();
  const deliveryProfileCard = page.locator(
    "#team-delivery-page .review-profile-card",
    { hasText: "Bookkeeping preparation review" },
  );
  await deliveryProfileCard.waitFor({ state: "visible" });
  const deliveryProfileDisclosure = deliveryProfileCard.locator(
    ":scope > details.review-profile-card-details",
  );
  assert.notEqual(
    await deliveryProfileDisclosure.getAttribute("data-team-disclosure-key"),
    projectsProfileKey,
    "shared review-profile renderers should use view-scoped disclosure keys",
  );
  assert.equal(
    await deliveryProfileDisclosure.getAttribute("open"),
    null,
    "opening a Projects review profile should not open its Delivery copy",
  );
  await openProjects(page);
  const restoredProjectsProfile = page.locator(
    "#team-projects-page .review-profile-card",
    { hasText: "Bookkeeping preparation review" },
  );
  assert.notEqual(
    await restoredProjectsProfile
      .locator(":scope > details.review-profile-card-details")
      .getAttribute("open"),
    null,
    "the Projects review profile should retain its own open state",
  );

  await page.getByRole("tab", { name: "Task board" }).click();
  await page.locator("#task-board-team-select").selectOption("team");
  await page.locator("#task-board-project-select").selectOption("project");
  const rootTaskCard = page.locator(
    '.task-board-card[data-task-board-task-id="task_open"]',
  );
  await rootTaskCard.waitFor({ state: "visible" });
  await rootTaskCard.getByText("26 unresolved descendants", { exact: true }).waitFor({
    state: "visible",
  });
  await rootTaskCard
    .getByText("Bookkeeping follow-up 1, Bookkeeping follow-up 2, Bookkeeping follow-up 3, +23 more", {
      exact: true,
    })
    .waitFor({ state: "visible" });
  assert.equal(await rootTaskCard.getByText("task-descendant-26", { exact: true }).isVisible(), false);
  assert.equal(await rootTaskCard.getByText("task_open", { exact: true }).isVisible(), false);

  await rootTaskCard.getByText("View all 26 descendants", { exact: true }).click();
  assert.equal(await rootTaskCard.locator(".task-board-descendant-list > li").count(), 26);
  await rootTaskCard.getByText("Bookkeeping follow-up 26", { exact: true }).waitFor({
    state: "visible",
  });
  assert.equal(await rootTaskCard.getByText("task-descendant-26", { exact: true }).isVisible(), false);

  await rootTaskCard.getByText("Technical details", { exact: true }).click();
  await rootTaskCard.getByText("task_open", { exact: true }).waitFor({ state: "visible" });
  await rootTaskCard.getByText("task-descendant-26", { exact: false }).waitFor({
    state: "visible",
  });
});

test("team add-on project controls follow privilege changes and partial rerenders", async (t) => {
  const { port } = await createTeamAddonServer(t, {
    initialState: seededOrgChartState(),
  });
  const page = await openBrowserPage(t, port, {}, "/addons/team-addon/?privileged_unlocked=false");

  await waitForRevision(page, 8);
  await openProjects(page);
  const projectsPage = page.locator("#team-projects-page");
  const createProjectButton = projectsPage.getByRole("button", { name: "Create project" }).first();
  const addOutcomeButton = projectsPage.getByRole("button", { name: "Add outcome kind" });
  const addReviewProfileButton = projectsPage.getByRole("button", { name: "Add review profile" });

  await addOutcomeButton.waitFor({ state: "visible" });
  assert.equal(await createProjectButton.isDisabled(), true);
  assert.equal(await addOutcomeButton.isDisabled(), true);
  assert.equal(await addReviewProfileButton.isDisabled(), true);

  await page.evaluate(() => {
    window.postMessage(
      {
        type: "admin-privileged-operations",
        privileged_operations: { unlocked: true },
      },
      window.location.origin,
    );
  });
  await page.waitForFunction(() => {
    const projectButton = document.querySelector("[data-project-create-toggle]");
    const outcomeButton = document.querySelector(
      '#team-projects-page form[data-form-id="create-outcome-kind-form"] button[type="submit"]',
    );
    const profileButton = document.querySelector(
      "#team-projects-page [data-project-review-profile-create-toggle]",
    );
    return Boolean(
      projectButton && !projectButton.disabled
      && outcomeButton && !outcomeButton.disabled
      && profileButton && !profileButton.disabled
    );
  });

  await page.evaluate(() => {
    window.postMessage(
      {
        type: "admin-privileged-operations",
        privileged_operations: { unlocked: false },
      },
      window.location.origin,
    );
  });
  await page.waitForFunction(() => {
    const projectButton = document.querySelector("[data-project-create-toggle]");
    const outcomeButton = document.querySelector(
      '#team-projects-page form[data-form-id="create-outcome-kind-form"] button[type="submit"]',
    );
    const profileButton = document.querySelector(
      "#team-projects-page [data-project-review-profile-create-toggle]",
    );
    return Boolean(
      projectButton?.disabled
      && outcomeButton?.disabled
      && profileButton?.disabled
    );
  });

  await projectsPage.locator("[data-project-focus-id]").first().click();

  assert.equal(await createProjectButton.isDisabled(), true);
  assert.equal(await projectsPage.getByRole("button", { name: "Add outcome kind" }).isDisabled(), true);
  assert.equal(await projectsPage.getByRole("button", { name: "Add review profile" }).isDisabled(), true);
});

test("team add-on state refresh preserves scoped no-id form and unsubmitted search drafts", async (t) => {
  const initialState = seededGitState();
  initialState.cases ||= {};
  initialState.cases["case-draft-preservation"] = {
    id: "case-draft-preservation",
    team_id: "team-alpha",
    project_id: "alpha-project",
    title: "Draft preservation case",
    description: "Keeps the case search form rendered.",
    status: "open",
    priority: "normal",
    tags: [],
    task_ids: [],
    created_at: "2026-06-09T00:00:00Z",
    updated_at: "2026-06-09T00:00:00Z",
  };
  const {
    port,
    state,
    emitStateSnapshot,
    stateStreamCount,
  } = await createTeamAddonServer(t, {
    initialState,
  });
  const page = await openBrowserPage(
    t,
    port,
    {},
    "/addons/team-addon/?privileged_unlocked=true",
  );

  await waitForRevision(page, 4);
  await waitForCondition(
    () => stateStreamCount() > 0,
    "expected Team state stream to be open before testing form drafts",
  );
  await openProjects(page);
  const alphaOutcomeForm = page.locator(
    '#team-projects-page form[data-form-id="create-outcome-kind-form"][data-form-draft-scope="alpha-project"]',
  );
  await alphaOutcomeForm.locator('[name="kind"]').fill("release_readiness");
  await alphaOutcomeForm.locator('[name="name"]').fill("Release readiness");
  await alphaOutcomeForm
    .locator('[name="description"]')
    .fill("Confirm release readiness before handoff.");
  await alphaOutcomeForm.locator('[name="team_wide"]').check();

  state.revision += 1;
  assert.ok(
    emitStateSnapshot({ response_scope: "revision", revision: state.revision }) > 0,
    "expected the project draft refresh to reach the Team client",
  );
  await waitForRevision(page, state.revision);
  assert.equal(
    await alphaOutcomeForm.locator('[name="description"]').inputValue(),
    "Confirm release readiness before handoff.",
  );
  assert.equal(await alphaOutcomeForm.locator('[name="team_wide"]').isChecked(), true);

  await page.locator('[data-project-focus-id="beta-project"]').click();
  const betaOutcomeForm = page.locator(
    '#team-projects-page form[data-form-id="create-outcome-kind-form"][data-form-draft-scope="beta-project"]',
  );
  assert.equal(
    await betaOutcomeForm.locator('[name="description"]').inputValue(),
    "",
    "a project-scoped draft must not leak into a different project",
  );

  await page.getByRole("tab", { name: "Cases" }).click();
  const caseQuery = page.locator(
    '#team-cases-page form[data-form-id="case-history-search-form"] [name="query"]',
  );
  await caseQuery.fill("unsent case query");
  state.revision += 1;
  emitStateSnapshot({ response_scope: "revision", revision: state.revision });
  await waitForRevision(page, state.revision);
  assert.equal(await caseQuery.inputValue(), "unsent case query");

  await page.getByRole("tab", { name: "Task board" }).click();
  await page.locator("#task-board-team-select").selectOption("team-alpha");
  await page.locator("#task-board-project-select").selectOption("alpha-project");
  const taskQuery = page.locator(
    '#team-task-board-page form[data-form-id="task-board-search-form"] [name="task_board_query"]',
  );
  await taskQuery.fill("unsent task query");
  state.revision += 1;
  emitStateSnapshot({ response_scope: "revision", revision: state.revision });
  await waitForRevision(page, state.revision);
  assert.equal(await taskQuery.inputValue(), "unsent task query");
});

test("team add-on admin context overrides the browser color scheme", async (t) => {
  const { port } = await createTeamAddonServer(t, {
    initialState: seededOrgChartState(),
  });
  const page = await openBrowserPage(t, port, { colorScheme: "light" });

  await waitForRevision(page, 8);
  await page.evaluate(() => {
    window.postMessage(
      {
        type: "admin-addon-context",
        theme: "dark",
        privileged_operations: { unlocked: true },
      },
      window.location.origin,
    );
  });
  await page.waitForFunction(() => document.documentElement.dataset.theme === "dark");
  assert.equal(
    await page.evaluate(() =>
      getComputedStyle(document.documentElement)
        .getPropertyValue("--surface")
        .trim(),
    ),
    "#121817",
  );

  await page.emulateMedia({ colorScheme: "dark" });
  await page.evaluate(() => {
    window.postMessage(
      {
        type: "admin-addon-context",
        theme: "light",
        privileged_operations: { unlocked: true },
      },
      window.location.origin,
    );
  });
  await page.waitForFunction(() => document.documentElement.dataset.theme === "light");
  assert.equal(
    await page.evaluate(() =>
      getComputedStyle(document.documentElement)
        .getPropertyValue("--surface")
        .trim(),
    ),
    "#f7f9f7",
  );
});

test("team add-on UI renders org charts, details, and hierarchy warnings", async (t) => {
  const initialState = seededOrgChartState();
  initialState.teams.team.project_ids.push("other-project");
  initialState.projects["other-project"] = {
    id: "other-project",
    team_id: "team",
    name: "Other Project",
    lead_agent_id: "lead",
    goals: [],
    git: null,
    status: "active",
  };
  delete initialState.agents.backend.project_id;
  initialState.agents.backend.project_ids = ["project", "other-project"];
  initialState.agents.backend.project_assignments = [
    {
      id: "project__backend__project_member",
      team_id: "team",
      project_id: "project",
      agent_id: "backend",
      role_id: "project__backend",
      kind: "project_member",
    },
    {
      id: "other-project__backend__project_member",
      team_id: "team",
      project_id: "other-project",
      agent_id: "backend",
      role_id: "project__backend",
      kind: "project_member",
    },
  ];
  const { port, requests } = await createTeamAddonServer(t, {
    initialState,
  });
  const page = await openBrowserPage(t, port);

  await waitForRevision(page, 8);
  await openOrgChart(page);
  await page.locator("#team-workspace-page").waitFor({ state: "hidden" });
  await page.locator("#team-org-page [data-team-org-flow-canvas]").waitFor({ state: "visible" });
  await page.locator("#team-org-page .org-flow-governance-node", { hasText: "Portfolio Oversight" }).waitFor({
    state: "visible",
  });
  assert.equal(await page.locator(".org-focus-node-card").count(), 4);
  await page.locator(".org-focus-summary").getByText("Awake").waitFor({ state: "visible" });
  await page.waitForFunction(() => document.querySelectorAll("#team-org-page .react-flow__edge-path").length >= 5);

  const orgPage = page.locator("#team-org-page");
  const governanceBox = await page.locator("#team-org-page .org-flow-governance-node").boundingBox();
  const teamBox = await page.locator("#team-org-page .org-focus-team").boundingBox();
  assert.ok(governanceBox && teamBox && governanceBox.y < teamBox.y, "governance should render above its team");
  const focusedManagerBox = await orgPage.locator(".org-focus-node-card", { hasText: "Ops Manager" }).boundingBox();
  assert.ok(
    governanceBox && teamBox && governanceBox.y + governanceBox.height <= teamBox.y - 1,
    "governance should not overlap its team",
  );
  assert.ok(
    teamBox && focusedManagerBox && teamBox.y + teamBox.height <= focusedManagerBox.y - 1,
    "team should not overlap its root agent",
  );

  const focusedBackendCard = page.locator(".org-focus-node-card").filter({
    has: page.locator(".hierarchy-node-title strong", { hasText: /^Backend Worker$/ }),
  });
  await focusedBackendCard.locator(".awake-indicator", { hasText: "Awake" }).waitFor({
    state: "visible",
  });
  await focusedBackendCard.locator(".org-node-work-line--now").getByText("Build API").waitFor({
    state: "hidden",
  });
  await focusedBackendCard.locator(".org-node-work").getByText("Resolve dependency").waitFor({
    state: "visible",
  });
  const focusedBackendDescriptionId = await focusedBackendCard.getAttribute("aria-describedby");
  assert.ok(focusedBackendDescriptionId);
  assert.match(await focusedBackendCard.locator(".visually-hidden").textContent(), /Agent ID backend\. Role Backend\./);

  await focusedBackendCard.focus();
  await focusedBackendCard.locator(".hierarchy-node-avatar-wrap .hierarchy-node-avatar-card").waitFor({ state: "visible" });
  await page.mouse.move(0, 0);
  await page.getByRole("tab", { name: "Org chart" }).focus();
  await focusedBackendCard.locator(".hierarchy-node-avatar-wrap .hierarchy-node-avatar-card").waitFor({ state: "hidden" });

  await focusedBackendCard.locator(".hierarchy-node-avatar-wrap").hover();
  await focusedBackendCard.locator(".hierarchy-node-avatar-wrap .hierarchy-node-avatar-card").waitFor({ state: "visible" });
  await focusedBackendCard.locator(".org-node-work-line--now").getByText("Build API").waitFor({
    state: "visible",
  });
  const focusedBackendDetails = focusedBackendCard.locator(".org-node-hover-panel");
  await focusedBackendDetails.getByText("Awake status Awake - dispatch dispatch-42").waitFor({
    state: "visible",
  });
  await focusedBackendDetails.getByText("Current work Build API (In Progress)").waitFor({
    state: "visible",
  });
  await focusedBackendDetails.getByText("Waiting on Resolve dependency (Blocked)").waitFor({
    state: "visible",
  });

  const zoomedOrgViewport = await zoomOrgChartCanvas(page);
  await focusedBackendCard.click();
  await page.locator("#edit-modal-title", { hasText: "Edit agent backend" }).waitFor({
    state: "visible",
  });
  assert.deepEqual(
    await page.locator('#edit-record-form select[name="project_ids"]').evaluate((select) =>
      Array.from(select.selectedOptions).map((option) => option.value).sort(),
    ),
    ["other-project", "project"],
  );
  await page.locator('#edit-record-form input[name="display_name"]').fill("Backend Worker Updated");
  await page.locator("#edit-record-form").getByRole("button", { name: "Save changes" }).click();
  await page.locator("#status-message", { hasText: /Saved revision \d+\./ }).waitFor({
    state: "visible",
  });
  const editRequest = requests.find(
    (request) =>
      request.method === "PATCH" &&
      request.pathname === "/api/addons/team-addon/agents/backend",
  );
  assert.ok(editRequest);
  assert.equal(editRequest.payload.display_name, "Backend Worker Updated");
  assert.equal(Object.hasOwn(editRequest.payload, "project_ids"), false);
  await page.waitForTimeout(350);
  assert.equal(
    await orgChartViewportTransform(page),
    zoomedOrgViewport,
    "saving agent edits should not refit the org chart viewport",
  );
});

test("team add-on Org chart animates only newly observed activity without refitting", async (t) => {
  const initialState = seededOrgChartState();
  initialState.agents.backend.activity_summary = {
    headline: "Working on Build API",
    working_on: "Build API",
    waiting_for: "Clear",
    tone: "working",
    source: "derived",
    generated_at: "2026-06-11T08:55:00Z",
    reason_refs: [{ kind: "task", id: "task_open" }],
  };
  const {
    port,
    state,
    emitStateSnapshot,
    stateStreamCount,
  } = await createTeamAddonServer(t, { initialState });
  const page = await openBrowserPage(t, port);

  await waitForRevision(page, 8);
  await openOrgChart(page);
  await page.locator("#team-org-page [data-team-org-flow-canvas]").waitFor({ state: "visible" });
  await waitForCondition(
    () => stateStreamCount() > 0,
    "expected the Team state stream before testing live org activity",
  );
  assert.equal(await page.locator(".org-flow-activity-bubble").count(), 0);
  assert.equal(await page.locator(".org-flow-message-pulse").count(), 0);

  const backendCard = page.locator(".org-focus-node-card").filter({
    has: page.locator(".hierarchy-node-title strong", { hasText: /^Backend Worker$/ }),
  });
  const haloStyle = await backendCard.evaluate((element) => {
    const style = getComputedStyle(element, "::before");
    return {
      animationName: style.animationName,
      borderWidth: style.borderTopWidth,
      boxShadow: style.boxShadow,
      insetTop: style.top,
    };
  });
  assert.equal(haloStyle.animationName, "org-flow-active-halo");
  assert.equal(haloStyle.borderWidth, "2px");
  assert.notEqual(haloStyle.boxShadow, "none");
  assert.ok(Number.parseFloat(haloStyle.insetTop) < 0, `expected an outer card halo, got ${haloStyle.insetTop}`);

  const zoomedOrgViewport = await zoomOrgChartCanvas(page);
  state.revision += 1;
  state.agents.backend.activity_summary = {
    ...state.agents.backend.activity_summary,
    headline: "Reviewing API handoff",
    working_on: "API handoff",
    generated_at: "2026-06-11T08:56:00Z",
  };
  state.inbox["inbox-live-status"] = {
    id: "inbox-live-status",
    recipient_agent_id: "worker",
    item_type: "status_request",
    sender_agent_id: "backend",
    task_id: "task_waiting",
    payload: { question: "Please confirm review readiness." },
    state: "pending",
    created_at: "2026-06-11T08:56:00Z",
    updated_at: "2026-06-11T08:56:00Z",
  };
  emitStateSnapshot(state);

  await waitForRevision(page, state.revision);
  await backendCard.locator(".org-flow-activity-bubble", { hasText: "Reviewing API handoff" }).waitFor({
    state: "visible",
  });
  const forwardPulse = page.locator('.react-flow__edge[data-id="message-pulse:inbox-live-status"]');
  await forwardPulse.waitFor({ state: "attached" });
  const hierarchyPath = await page
    .locator('.react-flow__edge[data-id="edge:agent:backend:agent:worker:manager"] .react-flow__edge-path')
    .getAttribute("d");
  assert.ok(hierarchyPath);
  assert.equal(await forwardPulse.locator(".org-flow-message-guide").getAttribute("d"), hierarchyPath);
  assert.equal(await forwardPulse.locator("animateMotion").getAttribute("keyPoints"), "0;1");
  assert.equal(
    await orgChartViewportTransform(page),
    zoomedOrgViewport,
    "live activity should not refit the org chart viewport",
  );

  await page.locator(".org-flow-message-pulse").waitFor({ state: "detached", timeout: 3000 });

  state.revision += 1;
  state.inbox["inbox-live-progress"] = {
    id: "inbox-live-progress",
    recipient_agent_id: "backend",
    item_type: "progress_report",
    sender_agent_id: "worker",
    task_id: "task_waiting",
    payload: { summary: "The review is ready." },
    state: "pending",
    created_at: "2026-06-11T08:57:00Z",
    updated_at: "2026-06-11T08:57:00Z",
  };
  emitStateSnapshot(state);

  await waitForRevision(page, state.revision);
  const reversePulse = page.locator('.react-flow__edge[data-id="message-pulse:inbox-live-progress"]');
  await reversePulse.waitFor({ state: "attached" });
  assert.equal(await reversePulse.locator(".org-flow-message-guide").getAttribute("d"), hierarchyPath);
  assert.equal(await reversePulse.locator("animateMotion").getAttribute("keyPoints"), "1;0");
  await reversePulse.waitFor({ state: "detached", timeout: 3000 });
  await backendCard.locator(".org-flow-activity-bubble").waitFor({ state: "detached", timeout: 7000 });
});

test("team add-on Org chart respects reduced-motion preferences", async (t) => {
  const initialState = seededOrgChartState();
  initialState.agents.backend.activity_summary = {
    headline: "Working on Build API",
    working_on: "Build API",
    waiting_for: "Clear",
    tone: "working",
    source: "derived",
    generated_at: "2026-06-11T08:55:00Z",
    reason_refs: [{ kind: "task", id: "task_open" }],
  };
  const { port, state, emitStateSnapshot, stateStreamCount } = await createTeamAddonServer(t, {
    initialState,
  });
  const page = await openBrowserPage(t, port, { reducedMotion: "reduce" });

  await waitForRevision(page, 8);
  await openOrgChart(page);
  await page.locator("#team-org-page [data-team-org-flow-canvas]").waitFor({ state: "visible" });
  await waitForCondition(
    () => stateStreamCount() > 0,
    "expected the Team state stream before testing reduced motion",
  );
  const backendCard = page.locator(".org-focus-node-card").filter({
    has: page.locator(".hierarchy-node-title strong", { hasText: /^Backend Worker$/ }),
  });
  assert.equal(
    await backendCard.evaluate((element) => getComputedStyle(element, "::before").animationName),
    "none",
  );

  state.revision += 1;
  state.agents.backend.activity_summary = {
    ...state.agents.backend.activity_summary,
    headline: "Preparing a calm handoff",
    working_on: "Calm handoff",
    generated_at: "2026-06-11T08:56:00Z",
  };
  state.inbox["inbox-reduced-motion"] = {
    id: "inbox-reduced-motion",
    recipient_agent_id: "worker",
    item_type: "status_request",
    sender_agent_id: "backend",
    task_id: "task_waiting",
    payload: { question: "Please confirm without animation." },
    state: "pending",
    created_at: "2026-06-11T08:56:00Z",
    updated_at: "2026-06-11T08:56:00Z",
  };
  emitStateSnapshot(state);

  await waitForRevision(page, state.revision);
  await backendCard.locator(".org-flow-activity-bubble", { hasText: "Preparing a calm handoff" }).waitFor({
    state: "visible",
  });
  await page.waitForTimeout(100);
  assert.equal(await page.locator(".org-flow-message-pulse").count(), 0);
});

test("team add-on Org chart draws edges and separates sibling agents", async (t) => {
  const initialState = seededOrgChartState();
  initialState.revision = 9;
  initialState.agents.lead.subordinate_agent_ids.push("frontend");
  initialState.agents.frontend = {
    id: "frontend",
    team_id: "team",
    project_id: "project",
    display_name: "Frontend Worker",
    role_id: "project__worker",
    session_id: "session-frontend",
    reports_to_agent_id: "lead",
    subordinate_agent_ids: [],
    ephemeral_policy: { max_concurrent: 0 },
    status: "active",
  };
  const { port } = await createTeamAddonServer(t, {
    initialState,
  });
  const page = await openBrowserPage(t, port);

  await waitForRevision(page, 9);
  await page.getByRole("tab", { name: "Org chart" }).click();
  const orgPage = page.locator("#team-org-page");
  await orgPage.locator("[data-team-org-flow-canvas]").waitFor({ state: "visible" });
  await page.waitForFunction(() => document.querySelectorAll("#team-org-page .react-flow__edge-path").length >= 6);

  const governanceBox = await orgPage.locator(".org-flow-governance-node").boundingBox();
  const teamBox = await orgPage.locator(".org-focus-team").boundingBox();
  const managerBox = await orgPage.locator(".org-focus-node-card", { hasText: "Ops Manager" }).boundingBox();
  assert.ok(
    governanceBox && teamBox && managerBox && governanceBox.y + governanceBox.height <= teamBox.y - 1,
    "governance should be separated from the team row",
  );
  assert.ok(
    teamBox && managerBox && teamBox.y + teamBox.height <= managerBox.y - 1,
    "team row should be separated from the root agent row",
  );

  const backendBox = await orgPage.locator(".org-focus-node-card", { hasText: "Backend Worker" }).boundingBox();
  const frontendBox = await orgPage.locator(".org-focus-node-card", { hasText: "Frontend Worker" }).boundingBox();
  assert.ok(backendBox && frontendBox, "expected both sibling agent boxes");
  const lateralOverlap =
    Math.min(backendBox.x + backendBox.width, frontendBox.x + frontendBox.width) -
    Math.max(backendBox.x, frontendBox.x);
  assert.ok(lateralOverlap <= 0.5, `same-level agents should not overlap laterally: ${lateralOverlap}`);
});

test("team add-on Org chart keeps governance groups above related team filters", async (t) => {
  const { port } = await createTeamAddonServer(t, {
    initialState: seededGitState(),
  });
  const page = await openBrowserPage(t, port);

  await waitForRevision(page, 4);
  await page.getByRole("tab", { name: "Org chart" }).click();
  await page.locator("#team-org-page [data-team-org-flow-canvas]").waitFor({ state: "visible" });

  const governanceNode = page.locator("#team-org-page .org-flow-governance-node", {
    hasText: "Platform Oversight",
  });
  await governanceNode.waitFor({ state: "visible" });
  assert.equal(await page.locator("#team-org-page .org-focus-team").count(), 2);
  assert.equal(await page.locator("#team-org-page .org-flow-governance-node").count(), 1);
  const governanceBox = await governanceNode.boundingBox();
  const alphaBox = await page.locator("#team-org-page .org-focus-team", { hasText: "Alpha Team" }).boundingBox();
  assert.ok(governanceBox && alphaBox && governanceBox.y < alphaBox.y, "governance should render above member teams");

  await page.locator('#team-org-page [data-org-team-id="team-alpha"]').click();
  assert.equal(await page.locator("#team-org-page .org-focus-team").count(), 1);
  await governanceNode.waitFor({ state: "visible" });
  await page.locator("#team-org-page").getByRole("heading", { name: "Alpha Team" }).waitFor({
    state: "visible",
  });
  assert.equal(await page.locator("#team-org-page").getByRole("heading", { name: "Beta Team" }).count(), 0);

  await page.locator('#team-org-page [data-org-team-id="team-beta"]').click();
  assert.equal(await page.locator("#team-org-page .org-focus-team").count(), 1);
  await governanceNode.waitFor({ state: "visible" });
  await page.locator("#team-org-page").getByRole("heading", { name: "Beta Team" }).waitFor({
    state: "visible",
  });
  assert.equal(await page.locator("#team-org-page").getByRole("heading", { name: "Alpha Team" }).count(), 0);
});

test("team add-on Org chart context menu creates teams from the bare canvas", async (t) => {
  const { port, requests } = await createTeamAddonServer(t, {
    initialState: seededOrgChartState(),
  });
  const page = await openBrowserPage(t, port);

  await waitForRevision(page, 8);
  await openOrgChart(page);

  await rightClickBareOrgCanvas(page);
  await page.getByRole("menuitem", { name: "Create team" }).waitFor({ state: "visible" });
  await page.keyboard.press("Escape");
  await page.locator("#org-context-menu").waitFor({ state: "hidden" });

  await rightClickBareOrgCanvas(page);
  await page.getByRole("menuitem", { name: "Create team" }).click();
  const dialog = page.locator("#org-action-modal");
  await dialog.locator("#org-action-modal-title", { hasText: "Create team" }).waitFor({
    state: "visible",
  });
  await dialog.locator('[name="name"]').fill("Context Team");
  await dialog.locator('[name="manager_session_id"]').fill("session-context-manager");
  await dialog.locator('[name="manager_display_name"]').fill("Context Manager");
  await dialog.getByRole("button", { name: "Create team" }).click();

  await waitForStatusText(page, "Saved revision 9.", requests);
  await waitForRevision(page, 9);
  await dialog.waitFor({ state: "hidden" });
  const request = requests.find(
    (entry) => entry.method === "POST" && entry.pathname === "/api/addons/team-addon/teams",
  );
  assert.ok(request);
  assert.equal(request.payload.name, "Context Team");
  assert.equal(request.payload.manager_session_id, "session-context-manager");
  assert.equal(request.payload.manager_display_name, "Context Manager");
});

test("team add-on Org chart context menu edits governance team oversight", async (t) => {
  const initialState = seededGitState();
  initialState.governance_groups["platform-oversight"].member_team_ids = ["team-alpha"];
  const { port, requests } = await createTeamAddonServer(t, { initialState });
  const page = await openBrowserPage(t, port);

  await waitForRevision(page, 4);
  await openOrgChart(page);
  const governanceNode = page.locator("#team-org-page .org-flow-governance-node", {
    hasText: "Platform Oversight",
  });
  await governanceNode.waitFor({ state: "visible" });

  await governanceNode.click({ button: "right" });
  await page.getByRole("menuitem", { name: "Add team to oversight" }).click();
  let dialog = page.locator("#org-action-modal");
  await dialog.locator("#org-action-modal-title", { hasText: "Add team oversight" }).waitFor({
    state: "visible",
  });
  await dialog.locator('[name="team_id"]').selectOption("team-beta");
  await dialog.getByRole("button", { name: "Add team" }).click();
  await waitForRevision(page, 5);

  let patchRequests = requests.filter(
    (entry) =>
      entry.method === "PATCH" &&
      entry.pathname === "/api/addons/team-addon/governance-groups/platform-oversight",
  );
  assert.equal(patchRequests.length, 1);
  assert.deepEqual(patchRequests[0].payload.member_team_ids, ["team-alpha", "team-beta"]);

  await governanceNode.click({ button: "right" });
  await page.getByRole("menuitem", { name: "Remove team from oversight" }).click();
  dialog = page.locator("#org-action-modal");
  await dialog.locator("#org-action-modal-title", { hasText: "Remove team oversight" }).waitFor({
    state: "visible",
  });
  await dialog.locator('[name="team_id"]').selectOption("team-beta");
  await dialog.getByRole("button", { name: "Remove team" }).click();
  await waitForRevision(page, 6);

  patchRequests = requests.filter(
    (entry) =>
      entry.method === "PATCH" &&
      entry.pathname === "/api/addons/team-addon/governance-groups/platform-oversight",
  );
  assert.equal(patchRequests.length, 2);
  assert.deepEqual(patchRequests[1].payload.member_team_ids, ["team-alpha"]);
});

test("team add-on Org chart governance context menu retries after revision conflict", async (t) => {
  const initialState = seededGitState();
  initialState.governance_groups["platform-oversight"].member_team_ids = ["team-alpha"];
  const { port, requests } = await createTeamAddonServer(t, {
    initialState,
    conflictOnFirstGovernanceGroupUpdate: true,
  });
  const page = await openBrowserPage(t, port);

  await waitForRevision(page, 4);
  await openOrgChart(page);
  const governanceNode = page.locator("#team-org-page .org-flow-governance-node", {
    hasText: "Platform Oversight",
  });
  await governanceNode.waitFor({ state: "visible" });

  await governanceNode.click({ button: "right" });
  await page.getByRole("menuitem", { name: "Add team to oversight" }).click();
  const dialog = page.locator("#org-action-modal");
  const form = dialog.locator('form[data-org-action="governance-add-team"]');
  await form.locator('[name="team_id"]').selectOption("team-beta");
  await dialog.getByRole("button", { name: "Add team" }).click();

  await page.getByText(/Revision conflict/).waitFor({ state: "visible" });
  await waitForRevision(page, 5);
  await dialog.waitFor({ state: "visible" });
  assert.equal(await form.locator('[name="team_id"]').inputValue(), "team-beta");

  await page.getByRole("button", { name: "Retry save" }).click();
  await waitForRevision(page, 6);
  await dialog.waitFor({ state: "hidden" });

  const patchRequests = requests.filter(
    (entry) =>
      entry.method === "PATCH" &&
      entry.pathname === "/api/addons/team-addon/governance-groups/platform-oversight",
  );
  assert.equal(patchRequests.length, 2);
  assert.deepEqual(patchRequests[0].payload.member_team_ids, ["team-alpha", "team-beta"]);
  assert.equal(patchRequests[0].payload.expected_revision, 4);
  assert.deepEqual(patchRequests[1].payload.member_team_ids, ["team-alpha", "team-beta"]);
  assert.equal(patchRequests[1].payload.expected_revision, 5);
});

test("team add-on Org chart context menu adds subordinate agents", async (t) => {
  const { port, requests } = await createTeamAddonServer(t, {
    initialState: seededOrgChartState(),
  });
  const page = await openBrowserPage(t, port);

  await waitForRevision(page, 8);
  await openOrgChart(page);
  const backendCard = page.locator(".org-focus-node-card").filter({
    has: page.locator(".hierarchy-node-title strong", { hasText: /^Backend Worker$/ }),
  });
  await backendCard.waitFor({ state: "visible" });

  await backendCard.click({ button: "right" });
  await page.getByRole("menuitem", { name: "Add subordinate agent" }).click();
  const dialog = page.locator("#org-action-modal");
  await dialog.locator("#org-action-modal-title", { hasText: "Add subordinate agent" }).waitFor({
    state: "visible",
  });
  const form = dialog.locator('form[data-org-action="add-subordinate-agent"]');
  await waitForSelectOption(page, '#org-register-agent-form [name="existing_session_id"]', "session-existing-backend");
  assert.equal(await form.locator('[name="reports_to_agent_id"]').inputValue(), "backend");
  assert.equal(await form.locator('[name="team_id"]').inputValue(), "team");
  assert.equal(await form.locator('[name="project_id"]').inputValue(), "project");

  await form.locator('[name="role_id"]').selectOption("project__worker");
  await form.getByRole("button", { name: "Create new session" }).click();
  const createDialog = page.locator('[role="dialog"][aria-label="Create and register agent"]');
  await createDialog.waitFor({ state: "visible" });
  const createDialogBox = await createDialog.boundingBox();
  assert.ok(createDialogBox);
  const createDialogIsTopmost = await page.evaluate(
    ({ x, y }) => {
      const target = document.elementFromPoint(x, y);
      return Boolean(target?.closest?.('[role="dialog"][aria-label="Create and register agent"]'));
    },
    {
      x: createDialogBox.x + createDialogBox.width / 2,
      y: createDialogBox.y + Math.min(60, createDialogBox.height / 2),
    },
  );
  assert.equal(createDialogIsTopmost, true);
  await createDialog.getByRole("button", { name: "Close" }).click();
  await createDialog.waitFor({ state: "hidden" });
  await dialog.waitFor({ state: "visible" });

  await form.locator('[name="existing_session_id"]').selectOption("session-existing-backend");
  await form.locator('[name="display_name"]').fill("Context Worker");
  await form.locator('[name="max_concurrent"]').fill("1");
  await form.getByRole("button", { name: "Register agent" }).click();

  await waitForRevision(page, 9);
  const request = requests.find(
    (entry) => entry.method === "POST" && entry.pathname === "/api/addons/team-addon/agents",
  );
  assert.ok(request);
  assert.equal(request.payload.team_id, "team");
  assert.equal(request.payload.project_id, "project");
  assert.equal(request.payload.role_id, "project__worker");
  assert.equal(request.payload.session_id, "session-existing-backend");
  assert.equal(request.payload.display_name, "Context Worker");
  assert.equal(request.payload.reports_to_agent_id, "backend");
  assert.equal(request.payload.ephemeral_policy.max_concurrent, 1);
});

test("team add-on cost refresh ignores avatar file input drafts", async (t) => {
  let releaseCosts = () => {};
  const costsGate = new Promise((resolve) => {
    releaseCosts = resolve;
  });
  t.after(() => {
    releaseCosts();
  });
  const { port, costResponses } = await createTeamAddonServer(t, {
    costsBeforeResponse: costsGate,
    costsPayload: {
      generated_at: "2026-06-09T00:00:00Z",
      coverage_status: "complete",
      coverage_reasons: [],
      team_costs: {
        team: {
          team_id: "team",
          estimated_cost: 0,
          max_cost_usd: null,
          remaining_cost_usd: null,
          cost_limit_reached: false,
          coverage_status: "complete",
          events_count: 1,
          unpriced_events_count: 0,
          source_counts: { openai: 1 },
        },
      },
      project_costs: {},
      unattributed_costs: null,
      source_counts: { openai: 1 },
      events_count: 1,
      priced_events_count: 1,
      unpriced_events_count: 0,
      backfill: null,
    },
    initialState: seededOrgChartState(),
  });
  const page = await openBrowserPage(t, port);
  const pageErrors = [];
  page.on("pageerror", (error) => {
    pageErrors.push(error.message);
  });

  await waitForRevision(page, 8);
  await page.getByRole("tab", { name: "Org chart" }).click();
  await page.locator("#team-org-page").waitFor({ state: "visible" });
  const focusedBackendCard = page.locator(".org-focus-node-card").filter({
    has: page.locator(".hierarchy-node-title strong", { hasText: /^Backend Worker$/ }),
  });
  await focusedBackendCard.click();
  await page.locator("#edit-modal-title", { hasText: "Edit agent backend" }).waitFor({
    state: "visible",
  });
  await page.locator('#edit-record-form input[name="display_name"]').fill("Backend draft");
  await page.setInputFiles('#edit-record-form input[name="avatar_file"]', {
    name: "avatar.png",
    mimeType: "image/png",
    buffer: Buffer.from([0x89, 0x50, 0x4e, 0x47]),
  });

  releaseCosts();
  await waitForCondition(
    () => costResponses.length >= 1,
    "expected the delayed costs response to complete",
  );
  await page.locator(".cost-counter", { hasText: "$0.00" }).first().waitFor({
    state: "visible",
  });

  assert.equal(
    await page.locator(".cost-counter--warning", { hasText: "HTMLInputElement" }).count(),
    0,
  );
  assert.deepEqual(
    pageErrors.filter((message) => message.includes("HTMLInputElement")),
    [],
  );
  assert.equal(
    await page.locator('#edit-record-form input[name="display_name"]').inputValue(),
    "Backend draft",
  );
});

test("team add-on distinguishes governance portfolio roll-ups from individual spend", async (t) => {
  const initialState = seededGitState();
  initialState.teams["team-alpha"].name = "Starship Team";
  initialState.teams["team-beta"].name = "Accounting Department";
  initialState.teams["team-gamma"] = {
    id: "team-gamma",
    name: "Evander Dev Team",
    manager_agent_id: null,
    operator_communication_target_id: null,
    project_ids: [],
    archived: false,
  };
  initialState.teams["team-delta"] = {
    id: "team-delta",
    name: "APP Dev Team",
    manager_agent_id: null,
    operator_communication_target_id: null,
    project_ids: [],
    archived: false,
  };
  initialState.governance_groups["platform-oversight"] = {
    ...initialState.governance_groups["platform-oversight"],
    name: "Evander Governance",
    member_team_ids: ["team-alpha", "team-beta", "team-gamma", "team-delta"],
    max_cost_usd: 900,
  };
  const memberCosts = {
    "team-alpha": { estimated_cost: 474.9 },
    "team-beta": { estimated_cost: 120 },
    "team-gamma": { estimated_cost: 123.02 },
    "team-delta": { estimated_cost: 187.84 },
  };
  const teamCosts = Object.fromEntries(
    Object.entries(memberCosts).map(([teamId, values]) => [
      teamId,
      {
        team_id: teamId,
        ...values,
        max_cost_usd: null,
        remaining_cost_usd: null,
        cost_limit_reached: false,
        coverage_status: "partial",
        events_count: 1,
        priced_events_count: 1,
        unpriced_events_count: 0,
        source_counts: { openai: 1 },
      },
    ]),
  );
  const { port } = await createTeamAddonServer(t, {
    initialState,
    costsPayload: {
      generated_at: "2026-07-27T00:00:00Z",
      coverage_status: "partial",
      coverage_reasons: ["unpriced model usage"],
      team_costs: teamCosts,
      project_costs: {},
      governance_group_costs: {
        "platform-oversight": {
          governance_group_id: "platform-oversight",
          estimated_cost: 905.76,
          max_cost_usd: 900,
          remaining_cost_usd: 0,
          cost_limit_reached: true,
          coverage_status: "partial",
          coverage_reasons: ["unpriced model usage"],
          events_count: 7759,
          priced_events_count: 5914,
          unpriced_events_count: 1845,
          source_counts: { openai: 7759 },
          member_team_ids: ["team-alpha", "team-beta", "team-gamma", "team-delta"],
          member_team_costs: teamCosts,
          member_team_cap_reached_ids: [],
          member_team_cap_warning_ids: [],
        },
      },
      unattributed_costs: null,
      source_counts: { openai: 7759 },
      events_count: 7759,
      priced_events_count: 5914,
      unpriced_events_count: 1845,
      backfill: null,
    },
  });
  const page = await openBrowserPage(t, port);

  await waitForRevision(page, 4);
  await openWorkspace(page);
  const costAlerts = page.locator("#team-workspace-page .workspace-metric-card", {
    hasText: "Cost alerts",
  });
  await costAlerts.getByText("1", { exact: true }).waitFor({ state: "visible" });
  await costAlerts.getByText("team caps and portfolio roll-ups", { exact: true }).waitFor({
    state: "visible",
  });
  await costAlerts.click();

  const aggregate = page.locator("#team-governance-page .cost-counter--aggregate");
  await aggregate.waitFor({ state: "visible" });
  const aggregateText = await aggregate.innerText();
  assert.match(aggregateText, /Governed portfolio roll-up/i);
  assert.match(aggregateText, /\$905\.76/);
  assert.match(aggregateText, /Sum of 4 member teams; not governance manager spend\./);
  assert.match(aggregateText, /7,759 events/);
  assert.match(aggregateText, /1,845 unpriced/);
  assert.match(aggregateText, /partial pricing coverage/);
  assert.equal(
    await page.locator("#team-governance-page .cost-counter", { hasText: "Estimated group cost" }).count(),
    0,
  );

  await page.getByRole("tab", { name: "Org chart" }).click();
  await page.locator("#team-org-page").waitFor({ state: "visible" });
  await page.locator("#team-org-page .org-flow-governance-node .cost-counter--aggregate").waitFor({
    state: "visible",
  });
  assert.equal(await page.locator("#team-org-page .org-flow-team-node .cost-counter--aggregate").count(), 0);
});

test("team add-on UI hides archived entities by default and reveals them on request", async (t) => {
  const initialState = seededOrgChartState();
  initialState.teams.team.archived = true;
  initialState.projects.project.status = "archived";
  initialState.review_profiles = {
    profile: {
      id: "profile",
      team_id: "team",
      project_id: "project",
      name: "Archived profile",
      description: "Hidden by default.",
      applicable_role_ids: ["project__backend"],
      criteria: [],
      archived: true,
    },
  };
  for (const agent of Object.values(initialState.agents)) {
    agent.status = "archived";
  }
  const { port } = await createTeamAddonServer(t, { initialState });
  const page = await openBrowserPage(t, port);

  await waitForRevision(page, 8);
  await openWorkspace(page);
  await page.locator("#team-workspace-page").getByText("No active teams are registered.").waitFor({
    state: "visible",
  });

  await openOrgChart(page);
  await page.locator("#org-focus-view").getByText("No active team.").waitFor({
    state: "visible",
  });
  await openTeams(page);
  await page.locator("#team-people-page").getByText("No visible teams.").waitFor({ state: "visible" });
  await openProjects(page);
  await page.locator("#team-projects-page").getByText("No visible projects.").waitFor({ state: "visible" });
  await page.getByRole("tab", { name: "Delivery" }).click();
  await page.locator("#team-delivery-page").waitFor({ state: "visible" });
  await page.locator("#team-delivery-page").getByText("No visible review profiles.").waitFor({
    state: "visible",
  });

  await page.locator("#show-archived-toggle").check();

  await openOrgChart(page);
  await page.locator("#team-org-page .org-focus-team", { hasText: "Team" }).waitFor({
    state: "visible",
  });
  await page.locator("#team-org-page .org-focus-node-card", { hasText: "Ops Manager" }).waitFor({
    state: "visible",
  });
  await page.locator("#team-org-page .org-focus-node-card", { hasText: "Backend Worker" }).waitFor({
    state: "visible",
  });

  await openTeams(page);
  await page.locator('#team-people-page [data-team-record-group-id="team"]', { hasText: "Team" }).waitFor({
    state: "visible",
  });

  await openProjects(page);
  const archivedProjectRow = page.locator('#team-projects-page [data-project-focus-id="project"]', {
    hasText: "Project",
  });
  await archivedProjectRow.waitFor({
    state: "visible",
  });
  await archivedProjectRow.locator(".state-pill.archived", { hasText: "archived" }).waitFor({
    state: "visible",
  });

  await page.getByRole("tab", { name: "Delivery" }).click();
  const profileRow = page.locator("#team-delivery-page .review-profiles-view .record-item", {
    hasText: "Archived profile",
  });
  await profileRow.locator(".state-pill", { hasText: "archived" }).waitFor({
    state: "visible",
  });
});

test("team add-on UI renders hierarchy warnings for missing and cyclical references", async (t) => {
  const { port } = await createTeamAddonServer(t, {
    initialState: seededMalformedHierarchyState(),
  });
  const page = await openBrowserPage(t, port);

  await waitForRevision(page, 3);
  await openOrgChart(page);

  await page.getByText("Missing manager ghost-manager").waitFor({ state: "visible" });
  await page.getByText("Cycle detected at alpha").waitFor({ state: "visible" });
  await page.getByText("Missing subordinate missing-subordinate").waitFor({ state: "visible" });
  assert.ok(await page.locator("#team-org-page .hierarchy-node-card--warning").count() >= 2);
});

test("team add-on UI reloads state and preserves form values on revision conflict", async (t) => {
  const { port, requests } = await createTeamAddonServer(t, { conflictOnFirstTeamCreate: true });
  const page = await openBrowserPage(t, port);

  await openTeams(page);
  await page.locator("#team-tab-create-team-action").click();
  const teamForm = page.locator("#create-team-from-teams-form");
  await teamForm.waitFor({ state: "visible" });
  await teamForm.locator('[name="name"]').fill("Team");
  await teamForm.locator('[name="manager_session_id"]').fill("session-manager");
  await teamForm.getByRole("button", { name: "Create team" }).click();

  await page.getByText(/Revision conflict/).waitFor({ state: "visible" });
  assert.equal(await teamForm.locator('[name="name"]').inputValue(), "Team");
  assert.equal(await teamForm.locator('[name="manager_session_id"]').inputValue(), "session-manager");
  await page
    .locator('#team-people-page [data-team-record-group-id="existing"] .team-record-group-title strong', {
      hasText: "Existing",
    })
    .waitFor({ state: "visible" });

  await page.getByRole("button", { name: "Retry save" }).click();
  await waitForRevision(page, 2);
  await page
    .locator('#team-people-page [data-team-record-group-id="team-team"] .team-record-group-title strong', {
      hasText: "Team",
    })
    .waitFor({ state: "visible" });
  const createRequests = requests.filter((request) => request.pathname === "/api/addons/team-addon/teams");
  assert.equal(createRequests.length, 2);
  assert.equal(createRequests[1].payload.name, "Team");
  assert.equal(createRequests[1].payload.manager_session_id, "session-manager");
  assert.equal(createRequests[1].payload.expected_revision, 1);
  assert.equal(createRequests[1].payload.conflict_policy, "target_unchanged");
});

test("team add-on Governance tab creates governance groups from the top-level button", async (t) => {
  const { port, requests } = await createTeamAddonServer(t, {
    initialState: seededGitState(),
  });
  const page = await openBrowserPage(t, port);

  await waitForRevision(page, 4);
  await page.getByRole("tab", { name: "Governance" }).click();
  await page.locator("#team-governance-page").waitFor({ state: "visible" });
  await page.locator("#governance-create-panel").waitFor({ state: "hidden" });

  await page.locator("[data-governance-create-toggle]").click();
  await page.locator('#governance-create-panel:not([hidden])').waitFor({ state: "visible" });
  await page.locator('[data-governance-create-toggle][aria-expanded="true"]').waitFor({
    state: "visible",
  });

  const form = page.locator("#create-governance-group-form");
  await waitForSelectOption(page, '#create-governance-group-form [name="manager_agent_id"]', "alpha-manager");
  await waitForSelectOption(page, '#create-governance-group-form [name="member_team_ids"]', "team-beta");
  assert.equal(await form.locator("[data-governance-goal-row]").count(), 0);
  await form.locator('[name="name"]').fill("Goal-free Oversight");
  await form.locator('[name="manager_agent_id"]').selectOption("alpha-manager");
  await form.locator('[name="member_team_ids"]').selectOption(["team-alpha"]);
  await form.getByRole("button", { name: "Create governance group" }).click();

  await waitForRevision(page, 5);
  await page.locator("#governance-create-panel").waitFor({ state: "hidden" });
  const emptyGoalRequest = requests.find(
    (entry) =>
      entry.pathname === "/api/addons/team-addon/governance-groups",
  );
  assert.ok(emptyGoalRequest);
  assert.equal(emptyGoalRequest.payload.expected_revision, 4);
  assert.deepEqual(emptyGoalRequest.payload.goals, []);

  await page.locator("[data-governance-create-toggle]").click();
  await page.locator('#governance-create-panel:not([hidden])').waitFor({ state: "visible" });
  assert.equal(await form.locator("[data-governance-goal-row]").count(), 0);
  await form.locator('[name="name"]').fill("Platform Oversight");
  await form.locator('[name="manager_agent_id"]').selectOption("alpha-manager");
  await form.locator('[name="member_team_ids"]').selectOption(["team-alpha", "team-beta"]);
  await form.locator('[name="max_cost_usd"]').fill("42.50");
  await form.getByRole("button", { name: "Add goal" }).click();
  const goalRow = form.locator("[data-governance-goal-row]");
  assert.equal(await goalRow.count(), 1);
  await goalRow.locator('[name="governance_goal_name"]').fill("Cost guardrail");
  assert.equal(
    await goalRow.locator('[name="governance_goal_name"]').getAttribute("required"),
    "",
  );
  assert.equal(
    await goalRow
      .locator('[name="governance_goal_description"]')
      .getAttribute("required"),
    "",
  );
  assert.deepEqual(
    await goalRow
      .locator('[name="governance_goal_state"] option')
      .allTextContents(),
    ["Pending", "In Progress", "Accepted", "Rejected", "Blocked"],
  );
  await goalRow
    .locator('[name="governance_goal_state"]')
    .selectOption("in_progress");
  await form.getByRole("button", { name: "Create governance group" }).click();
  assert.equal(
    requests.filter(
      (entry) =>
        entry.pathname === "/api/addons/team-addon/governance-groups",
    ).length,
    1,
  );
  await goalRow
    .locator('[name="governance_goal_description"]')
    .fill("Keep spend within guardrails");
  await form.getByRole("button", { name: "Create governance group" }).click();

  await waitForRevision(page, 6);
  await page.locator("#governance-create-panel").waitFor({ state: "hidden" });
  await page.locator(".governance-item", { hasText: "governance-group-platform-oversight" }).waitFor({
    state: "visible",
  });

  const request = requests.filter(
    (entry) => entry.pathname === "/api/addons/team-addon/governance-groups",
  )[1];
  assert.ok(request);
  assert.equal(request.method, "POST");
  assert.equal(request.payload.expected_revision, 5);
  assert.equal(request.payload.name, "Platform Oversight");
  assert.equal(request.payload.manager_agent_id, "alpha-manager");
  assert.deepEqual(request.payload.member_team_ids, ["team-alpha", "team-beta"]);
  assert.equal(request.payload.max_cost_usd, 42.5);
  assert.deepEqual(request.payload.goals, [
    {
      name: "Cost guardrail",
      description: "Keep spend within guardrails",
      state: "in_progress",
    },
  ]);
});

test("team add-on Governance tab hides handled agenda by default and emphasizes goals", async (t) => {
  const initialState = seededGitState();
  initialState.governance_groups["platform-oversight"].goals = [
    {
      id: "spend",
      description: "Keep platform spend inside the agreed guardrails",
      state: "pending",
    },
  ];
  initialState.governance_agenda["platform-handled-agenda"] = {
    id: "platform-handled-agenda",
    governance_group_id: "platform-oversight",
    item_type: "cost_cap_warning",
    priority: "normal",
    team_id: "team-beta",
    project_id: null,
    agent_id: null,
    title: "Retired cost review",
    summary: "Already handled.",
    payload: {},
    state: "handled",
    created_at: "2026-06-08T00:00:00Z",
    updated_at: "2026-06-08T00:00:00Z",
  };
  const { port } = await createTeamAddonServer(t, { initialState });
  const page = await openBrowserPage(t, port);

  await waitForRevision(page, 4);
  await page.getByRole("tab", { name: "Governance" }).click();
  await page.locator("#team-governance-page").waitFor({ state: "visible" });
  await page.locator("#show-handled-governance-agenda-toggle").waitFor({ state: "visible" });
  assert.equal(await page.locator("#show-handled-governance-agenda-toggle").isChecked(), false);

  await page.locator("#team-governance-page").getByText("Review Alpha blockers").waitFor({
    state: "visible",
  });
  assert.equal(await page.locator("#team-governance-page").getByText("Retired cost review").count(), 0);

  const subsectionHeadings = await page
    .locator("#team-governance-page .governance-subsection > .meta-line")
    .evaluateAll((nodes) => nodes.map((node) => node.textContent.trim()));
  assert.deepEqual(subsectionHeadings.slice(0, 3), ["Goals", "Member teams", "Agenda - 1 pending"]);
  await page.locator("#team-governance-page .governance-goals-subsection .governance-goal-list").waitFor({
    state: "visible",
  });
  assert.equal(
    await page
      .locator("#team-governance-page .governance-goals-subsection .governance-goal-list")
      .getByText("pending", { exact: true })
      .count(),
    0,
  );

  await page.locator("#show-handled-governance-agenda-toggle").check();
  await page.locator("#team-governance-page").getByText("Retired cost review").waitFor({
    state: "visible",
  });
});

test("team add-on governance editor uses named controls and preserves structured changes across retry", async (t) => {
  const initialState = seededGitState();
  initialState.agents["alpha-manager"].display_name = "Ada Manager";
  initialState.agents["beta-manager"].display_name = "Bea Manager";
  initialState.teams["team-gamma"] = {
    id: "team-gamma",
    name: "Gamma Team",
    manager_agent_id: "beta-manager",
    operator_communication_target_id: "ops-gamma",
    project_ids: [],
    archived: false,
  };
  initialState.governance_groups["platform-oversight"].goals = [
    {
      id: "delivery-signal",
      name: "delivery-signal",
      description: "Keep release evidence current.",
      state: "in_progress",
    },
    {
      id: "cost-guardrail",
      name: "Cost guardrail",
      description: "Keep portfolio spend inside the agreed cap.",
      state: "pending",
    },
  ];

  const { port, requests } = await createTeamAddonServer(t, {
    conflictOnFirstGovernanceGroupUpdate: true,
    initialState,
  });
  const page = await openBrowserPage(t, port);

  await waitForRevision(page, 4);
  await page.getByRole("tab", { name: "Governance" }).click();
  const groupCard = page.locator("#team-governance-page .governance-item", {
    hasText: "Platform Oversight",
  });
  await groupCard.getByRole("button", { name: "Edit" }).click();

  const dialog = page.locator("#edit-modal");
  const form = dialog.locator("#edit-record-form");
  await dialog.getByRole("heading", { name: "Edit Platform Oversight", exact: true }).waitFor({
    state: "visible",
  });
  assert.equal(
    await dialog.getByRole("heading", { name: "Edit governance group platform-oversight" }).count(),
    0,
  );

  const manager = form.locator('[name="manager_agent_id"]');
  const alphaManagerLabel = await manager.locator('option[value="alpha-manager"]').textContent();
  const betaManagerLabel = await manager.locator('option[value="beta-manager"]').textContent();
  assert.equal(alphaManagerLabel?.trim(), "Ada Manager — Manager · alpha-manager");
  assert.equal(betaManagerLabel?.trim(), "Bea Manager — Manager · beta-manager");

  const technicalDetails = form.locator("details.technical-details");
  assert.equal(await technicalDetails.getAttribute("open"), null);
  const groupIdText = form.getByText("platform-oversight", { exact: true });
  assert.equal(await groupIdText.count(), 1);
  assert.equal(await groupIdText.isVisible(), false);
  assert.ok(
    await groupIdText.evaluate((node) =>
      Boolean(node.closest("details.technical-details")),
    ),
  );
  await technicalDetails.locator("summary", { hasText: "Technical details" }).click();
  await groupIdText.waitFor({ state: "visible" });
  await technicalDetails
    .getByRole("button", { name: "Copy ID: platform-oversight", exact: true })
    .waitFor({ state: "visible" });
  await technicalDetails.locator("summary", { hasText: "Technical details" }).click();

  const teamEditor = form.locator("[data-governance-member-teams-editor]");
  await teamEditor.getByText("Alpha Team", { exact: true }).first().waitFor({
    state: "visible",
  });
  await teamEditor.getByText("Beta Team", { exact: true }).first().waitFor({
    state: "visible",
  });
  assert.equal(
    await teamEditor.locator('[data-governance-member-team-id="team-alpha"]').isChecked(),
    true,
  );
  assert.equal(
    await teamEditor.locator('[data-governance-member-team-id="team-beta"]').isChecked(),
    true,
  );
  await teamEditor.getByRole("button", { name: "Remove Beta Team" }).click();
  assert.equal(
    await teamEditor.locator('[data-governance-member-team-id="team-beta"]').isChecked(),
    false,
  );
  assert.equal(
    await teamEditor
      .locator("[data-governance-selected-teams]")
      .getByText("Beta Team", { exact: true })
      .count(),
    0,
  );

  await teamEditor.getByRole("searchbox", { name: "Search teams" }).fill("Gamma");
  await teamEditor
    .locator('[data-governance-team-choice]:has([data-governance-member-team-id="team-gamma"])')
    .waitFor({ state: "visible" });
  assert.equal(
    await teamEditor
      .locator('[data-governance-team-choice]:has([data-governance-member-team-id="team-beta"])')
      .isVisible(),
    false,
  );
  await teamEditor.locator('[data-governance-member-team-id="team-gamma"]').check();
  await teamEditor
    .locator("[data-governance-selected-teams]")
    .getByText("Gamma Team", { exact: true })
    .waitFor({ state: "visible" });

  const goalsEditor = form.locator("[data-governance-goals-editor]");
  const goalRows = goalsEditor.locator("[data-governance-goal-row]");
  assert.deepEqual(
    await goalRows
      .locator('[name="governance_goal_name"]')
      .evaluateAll((inputs) => inputs.map((input) => input.value)),
    ["delivery-signal", "Cost guardrail"],
  );
  assert.deepEqual(
    await goalRows
      .locator('[name="governance_goal_description"]')
      .evaluateAll((inputs) => inputs.map((input) => input.value)),
    [
      "Keep release evidence current.",
      "Keep portfolio spend inside the agreed cap.",
    ],
  );
  assert.deepEqual(
    await goalRows
      .locator('[name="governance_goal_state"]')
      .evaluateAll((inputs) => inputs.map((input) => input.value)),
    ["in_progress", "pending"],
  );

  await goalsEditor.getByRole("button", { name: "Add goal" }).click();
  const newGoal = goalRows.last();
  await newGoal.evaluate((row) => {
    row.dataset.testGoal = "risk-review";
  });
  const riskGoal = goalsEditor.locator('[data-test-goal="risk-review"]');
  await newGoal.locator('[name="governance_goal_name"]').fill("Risk review");
  await form.getByRole("button", { name: "Save changes" }).click();
  assert.equal(
    requests.filter(
      (entry) =>
        entry.method === "PATCH" &&
        entry.pathname === "/api/addons/team-addon/governance-groups/platform-oversight",
    ).length,
    0,
  );

  await riskGoal
    .locator('[name="governance_goal_description"]')
    .fill("Review operational risks before release.");
  await riskGoal.locator('[name="governance_goal_state"]').selectOption("blocked");
  await riskGoal.locator('[name="governance_goal_name"]').fill("DELIVERY-SIGNAL");
  await form.getByRole("button", { name: "Save changes" }).click();
  await page
    .getByText("Fix the highlighted governance goal before saving.", { exact: true })
    .waitFor({ state: "visible" });
  assert.equal(
    requests.filter(
      (entry) =>
        entry.method === "PATCH" &&
        entry.pathname === "/api/addons/team-addon/governance-groups/platform-oversight",
    ).length,
    0,
  );

  await riskGoal.locator('[name="governance_goal_name"]').fill("Risk review");
  await riskGoal.getByRole("button", { name: "Move goal up" }).click();
  await riskGoal.getByRole("button", { name: "Move goal up" }).click();
  const costGoal = goalRows.filter({
    has: page.locator('[name="governance_goal_name"][value="Cost guardrail"]'),
  });
  await costGoal.getByRole("button", { name: "Remove" }).click();
  const deliveryGoal = goalRows.filter({
    has: page.locator(
      '[name="governance_goal_name"][value="delivery-signal"]',
    ),
  });
  await deliveryGoal
    .locator('[name="governance_goal_state"]')
    .selectOption("accepted");
  assert.deepEqual(
    await goalRows
      .locator('[name="governance_goal_name"]')
      .evaluateAll((inputs) => inputs.map((input) => input.value)),
    ["Risk review", "delivery-signal"],
  );

  await form.locator('[name="name"]').fill("Platform Council");
  await manager.selectOption("beta-manager");
  await form.getByRole("button", { name: "Save changes" }).click();
  await page
    .getByText(/Revision conflict\. Draft preserved and revision 5 loaded/)
    .waitFor({ state: "visible" });

  assert.equal(await form.locator('[name="name"]').inputValue(), "Platform Council");
  assert.equal(await manager.inputValue(), "beta-manager");
  assert.deepEqual(
    await goalRows
      .locator('[name="governance_goal_name"]')
      .evaluateAll((inputs) => inputs.map((input) => input.value)),
    ["Risk review", "delivery-signal"],
  );
  assert.deepEqual(
    await goalRows
      .locator('[name="governance_goal_description"]')
      .evaluateAll((inputs) => inputs.map((input) => input.value)),
    [
      "Review operational risks before release.",
      "Keep release evidence current.",
    ],
  );
  assert.deepEqual(
    await goalRows
      .locator('[name="governance_goal_state"]')
      .evaluateAll((inputs) => inputs.map((input) => input.value)),
    ["blocked", "accepted"],
  );
  assert.equal(
    await teamEditor.locator('[data-governance-member-team-id="team-alpha"]').isChecked(),
    true,
  );
  assert.equal(
    await teamEditor.locator('[data-governance-member-team-id="team-beta"]').isChecked(),
    false,
  );
  assert.equal(
    await teamEditor.locator('[data-governance-member-team-id="team-gamma"]').isChecked(),
    true,
  );

  await page.getByRole("button", { name: "Retry save" }).click();
  await waitForRevision(page, 6);
  await dialog.waitFor({ state: "hidden" });

  const patches = requests.filter(
    (entry) =>
      entry.method === "PATCH" &&
      entry.pathname === "/api/addons/team-addon/governance-groups/platform-oversight",
  );
  assert.equal(patches.length, 2);
  assert.equal(patches[0].payload.expected_revision, 4);
  assert.equal(patches[1].payload.expected_revision, 5);
  for (const patch of patches) {
    assert.equal(patch.payload.name, "Platform Council");
    assert.equal(patch.payload.manager_agent_id, "beta-manager");
    assert.deepEqual(patch.payload.member_team_ids, ["team-alpha", "team-gamma"]);
    assert.deepEqual(patch.payload.goals, [
      {
        name: "Risk review",
        description: "Review operational risks before release.",
        state: "blocked",
      },
      {
        id: "delivery-signal",
        name: "delivery-signal",
        description: "Keep release evidence current.",
        state: "accepted",
      },
    ]);
  }
});

function seededGitState() {
  return {
    revision: 4,
    teams: {
      "team-alpha": {
        id: "team-alpha",
        name: "Alpha Team",
        manager_agent_id: "alpha-manager",
        operator_communication_target_id: "ops-alpha",
        project_ids: ["alpha-project"],
        archived: false,
      },
      "team-beta": {
        id: "team-beta",
        name: "Beta Team",
        manager_agent_id: "beta-manager",
        operator_communication_target_id: "ops-beta",
        project_ids: ["beta-project"],
        archived: false,
      },
    },
    projects: {
      "alpha-project": {
        id: "alpha-project",
        team_id: "team-alpha",
        name: "Alpha Project",
        lead_agent_id: "alpha-lead",
        goals: [{ id: "alpha-goal", description: "Ship alpha work" }],
        git: {
          managed_repo_id: "mr_aaaaaaaaaaaaaaaaaaaa",
          base_branch: "main",
        },
        status: "active",
      },
      "beta-project": {
        id: "beta-project",
        team_id: "team-beta",
        name: "Beta Project",
        lead_agent_id: "beta-lead",
        goals: [{ id: "beta-goal", description: "Ship beta work" }],
        git: {
          managed_repo_id: "mr_bbbbbbbbbbbbbbbbbbbb",
          base_branch: "main",
        },
        status: "active",
      },
    },
    roles: {
      "team-alpha__manager": {
        id: "team-alpha__manager",
        team_id: "team-alpha",
        project_id: null,
        name: "Manager",
        description: "",
        permissions: ["create_root_task"],
        ephemeral_policy: { max_concurrent: 0 },
        archived: false,
      },
      "alpha-project__project-lead": {
        id: "alpha-project__project-lead",
        team_id: "team-alpha",
        project_id: "alpha-project",
        name: "Project Lead",
        description: "",
        permissions: ["create_root_task"],
        ephemeral_policy: { max_concurrent: 0 },
        archived: false,
      },
      "team-beta__manager": {
        id: "team-beta__manager",
        team_id: "team-beta",
        project_id: null,
        name: "Manager",
        description: "",
        permissions: ["create_root_task"],
        ephemeral_policy: { max_concurrent: 0 },
        archived: false,
      },
      "beta-project__project-lead": {
        id: "beta-project__project-lead",
        team_id: "team-beta",
        project_id: "beta-project",
        name: "Project Lead",
        description: "",
        permissions: ["create_root_task"],
        ephemeral_policy: { max_concurrent: 0 },
        archived: false,
      },
    },
    agents: {
      "alpha-manager": {
        id: "alpha-manager",
        team_id: "team-alpha",
        project_id: null,
        role_id: "team-alpha__manager",
        session_id: "session-alpha-manager",
        reports_to_agent_id: null,
        subordinate_agent_ids: ["alpha-lead"],
        ephemeral_policy: { max_concurrent: 0 },
        status: "active",
      },
      "alpha-lead": {
        id: "alpha-lead",
        team_id: "team-alpha",
        project_id: "alpha-project",
        role_id: "alpha-project__project-lead",
        session_id: "session-alpha-lead",
        reports_to_agent_id: "alpha-manager",
        subordinate_agent_ids: [],
        ephemeral_policy: { max_concurrent: 0 },
        status: "active",
      },
      "beta-manager": {
        id: "beta-manager",
        team_id: "team-beta",
        project_id: null,
        role_id: "team-beta__manager",
        session_id: "session-beta-manager",
        reports_to_agent_id: null,
        subordinate_agent_ids: ["beta-lead"],
        ephemeral_policy: { max_concurrent: 0 },
        status: "active",
      },
      "beta-lead": {
        id: "beta-lead",
        team_id: "team-beta",
        project_id: "beta-project",
        role_id: "beta-project__project-lead",
        session_id: "session-beta-lead",
        reports_to_agent_id: "beta-manager",
        subordinate_agent_ids: [],
        ephemeral_policy: { max_concurrent: 0 },
        status: "active",
      },
    },
    governance_groups: {
      "platform-oversight": {
        id: "platform-oversight",
        name: "Platform Oversight",
        manager_agent_id: "alpha-manager",
        member_team_ids: ["team-alpha", "team-beta"],
        goals: [],
        max_cost_usd: 42.5,
        archived: false,
      },
    },
    governance_agenda: {
      "platform-alpha-agenda": {
        id: "platform-alpha-agenda",
        governance_group_id: "platform-oversight",
        item_type: "blocked_team",
        priority: "high",
        team_id: "team-alpha",
        project_id: null,
        agent_id: null,
        title: "Review Alpha blockers",
        summary: "Alpha needs oversight.",
        payload: {},
        state: "pending",
        created_at: "2026-06-09T00:00:00Z",
        updated_at: "2026-06-09T00:00:00Z",
      },
    },
    tasks: {
      "task-planned": {
        id: "task-planned",
        owner_agent_id: "alpha-lead",
        project_id: "alpha-project",
        priority: 0,
        state: "pending",
        state_entered_at: "2026-06-09T00:05:00Z",
        title: "Plan alpha delivery",
        description: "Work not started yet.",
        created_by_agent_id: "alpha-manager",
        created_at: "2026-06-09T00:00:00Z",
        updated_at: "2026-06-09T00:05:00Z",
      },
      "task-pending": {
        id: "task-pending",
        owner_agent_id: "alpha-lead",
        project_id: "alpha-project",
        priority: 1,
        state: "in_progress",
        state_entered_at: "2026-06-09T00:20:00Z",
        title: "Draft pending delivery",
        description: "Pending work in progress.",
        created_by_agent_id: "alpha-manager",
        deliverable_ids: ["deliverable-pending"],
        created_at: "2026-06-09T00:00:00Z",
        updated_at: "2026-06-09T00:00:00Z",
      },
      "task-active-later": {
        id: "task-active-later",
        owner_agent_id: "alpha-lead",
        project_id: "alpha-project",
        priority: 1,
        state: "in_progress",
        state_entered_at: "2026-06-09T00:25:00Z",
        title: "Review new runtime signal",
        description: "Newer in-progress work at the same priority.",
        created_by_agent_id: "alpha-manager",
        created_at: "2026-06-09T00:01:00Z",
        updated_at: "2026-06-09T00:25:00Z",
      },
      "task-waiting": {
        id: "task-waiting",
        owner_agent_id: "alpha-lead",
        project_id: "alpha-project",
        priority: 2,
        state: "waiting_for_review",
        state_entered_at: "2026-06-09T00:30:00Z",
        title: "Await review",
        description: "Work ready for review.",
        created_by_agent_id: "alpha-manager",
        deliverable_ids: ["deliverable-waiting"],
        created_at: "2026-06-09T00:00:00Z",
        updated_at: "2026-06-09T00:00:00Z",
      },
      "task-accepted": {
        id: "task-accepted",
        owner_agent_id: "alpha-lead",
        project_id: "alpha-project",
        priority: 3,
        state: "accepted",
        state_entered_at: "2026-06-09T00:40:00Z",
        title: "Ship accepted delivery",
        description: "Completed and reviewed work.",
        created_by_agent_id: "alpha-manager",
        deliverable_ids: ["deliverable-accepted"],
        created_at: "2026-06-09T00:00:00Z",
        updated_at: "2026-06-09T00:00:00Z",
      },
      "task-rejected": {
        id: "task-rejected",
        owner_agent_id: "beta-lead",
        project_id: "beta-project",
        priority: 4,
        state: "in_progress",
        state_entered_at: "2026-06-09T00:10:00Z",
        title: "Redo rejected delivery",
        description: "Work that needs revision.",
        created_by_agent_id: "beta-manager",
        deliverable_ids: ["deliverable-rejected"],
        created_at: "2026-06-09T00:00:00Z",
        updated_at: "2026-06-09T00:00:00Z",
      },
      "task-blocked": {
        id: "task-blocked",
        owner_agent_id: "beta-lead",
        project_id: "beta-project",
        priority: 5,
        state: "blocked",
        state_entered_at: "2026-06-09T00:50:00Z",
        title: "Blocked delivery",
        description: "Work blocked by an upstream issue.",
        created_by_agent_id: "beta-manager",
        deliverable_ids: ["deliverable-blocked"],
        created_at: "2026-06-09T00:00:00Z",
        updated_at: "2026-06-09T00:00:00Z",
      },
    },
    deliverables: {
      "deliverable-pending": {
        id: "deliverable-pending",
        task_id: "task-pending",
        producer_agent_id: "alpha-lead",
        artifact_refs: ["artifacts/pending.tar.gz"],
        summary: "Pending delivery artifact.",
        state: "pending",
        review_request_ids: [],
        git_refs: {
          agent_branch: "agents/alpha-lead/pending",
          review_branch: "team/alpha/reviews/pending",
          feature_branch: "feature/pending",
          release_branch: null,
          commit_sha: "aaa111",
          promotion_record_ids: ["promo-pending"],
        },
      },
      "deliverable-waiting": {
        id: "deliverable-waiting",
        task_id: "task-waiting",
        producer_agent_id: "alpha-lead",
        artifact_refs: ["artifacts/waiting.tar.gz"],
        summary: "Waiting for review.",
        state: "pending",
        review_request_ids: ["review-waiting"],
        carried_findings: [
          {
            id: "finding-ui",
            source_review_request_id: "review-prior",
            source_deliverable_id: "deliverable-prior",
            owner_review_profile_id: "profile-alpha",
            text: "Remove the stale station-tool spec.",
            state: "open",
          },
        ],
        git_refs: {
          agent_branch: "agents/alpha-lead/waiting",
          review_branch: "team/alpha/reviews/waiting",
          feature_branch: "feature/waiting",
          release_branch: null,
          commit_sha: "bbb222",
          promotion_record_ids: ["promo-waiting"],
        },
      },
      "deliverable-accepted": {
        id: "deliverable-accepted",
        task_id: "task-accepted",
        producer_agent_id: "alpha-lead",
        artifact_refs: ["artifacts/accepted.tar.gz"],
        summary: "Accepted delivery.",
        state: "accepted",
        review_request_ids: ["review-accepted"],
        git_refs: {
          agent_branch: "agents/alpha-lead/accepted",
          review_branch: "team/alpha/reviews/accepted",
          feature_branch: "feature/accepted",
          release_branch: "team/alpha/releases/accepted",
          commit_sha: "ccc333",
          promotion_record_ids: ["promo-accepted"],
        },
      },
      "deliverable-rejected": {
        id: "deliverable-rejected",
        task_id: "task-rejected",
        producer_agent_id: "beta-lead",
        artifact_refs: ["artifacts/rejected.tar.gz"],
        summary: "Rejected delivery.",
        state: "rejected",
        review_request_ids: ["review-rejected"],
        git_refs: {
          agent_branch: "agents/beta-lead/rejected",
          review_branch: "team/beta/reviews/rejected",
          feature_branch: "feature/rejected",
          release_branch: null,
          commit_sha: "ddd444",
          promotion_record_ids: ["promo-rejected"],
        },
      },
      "deliverable-blocked": {
        id: "deliverable-blocked",
        task_id: "task-blocked",
        producer_agent_id: "beta-lead",
        artifact_refs: ["artifacts/blocked.tar.gz"],
        summary: "Blocked delivery.",
        state: "blocked",
        review_request_ids: ["review-blocked"],
        git_refs: {
          agent_branch: "agents/beta-lead/blocked",
          review_branch: "team/beta/reviews/blocked",
          feature_branch: "feature/blocked",
          release_branch: null,
          commit_sha: "eee555",
          promotion_record_ids: ["promo-blocked"],
        },
      },
    },
    review_requests: {
      "review-waiting": {
        id: "review-waiting",
        deliverable_id: "deliverable-waiting",
        reviewer_agent_id: "alpha-manager",
        review_profile_id: "profile-alpha",
        state: "pending",
        findings: [],
        instructions: "Check the pending delivery.",
        criteria: [],
        decision_at: null,
      },
      "review-accepted": {
        id: "review-accepted",
        deliverable_id: "deliverable-accepted",
        reviewer_agent_id: "alpha-manager",
        review_profile_id: "profile-alpha",
        state: "accepted",
        findings: ["Looks good."],
        instructions: "Check the accepted delivery.",
        criteria: [
          {
            criterion_id: "tests",
            state: "passed",
            finding: "Targeted tests passed",
          },
          {
            criterion_id: "docs",
            state: "passed",
            finding: "Docs updated",
          },
        ],
        decision_at: "2026-06-09T12:00:00Z",
      },
      "review-rejected": {
        id: "review-rejected",
        deliverable_id: "deliverable-rejected",
        reviewer_agent_id: "beta-manager",
        review_profile_id: "profile-alpha",
        state: "rejected",
        findings: ["Needs more work."],
        instructions: "Check the rejected delivery.",
        criteria: [
          {
            criterion_id: "tests",
            state: "failed",
            finding: "Regression detected",
          },
        ],
        decision_at: "2026-06-09T13:00:00Z",
      },
      "review-blocked": {
        id: "review-blocked",
        deliverable_id: "deliverable-blocked",
        reviewer_agent_id: "beta-manager",
        review_profile_id: "profile-alpha",
        state: "blocked",
        findings: ["Blocked by upstream work."],
        instructions: "Check the blocked delivery.",
        criteria: [
          {
            criterion_id: "upstream",
            state: "blocked",
            finding: "Waiting on dependency",
          },
        ],
        decision_at: "2026-06-09T14:00:00Z",
      },
    },
    git_promotions: {
      "promo-pending": {
        id: "promo-pending",
        project_id: "alpha-project",
        actor_agent_id: "alpha-lead",
        source_branch: "agents/alpha-lead/pending",
        target_branch: "team/alpha/reviews/pending",
        promotion_kind: "review",
        state: "succeeded",
        commit_sha: "aaa111",
        outputs: {},
        error: null,
      },
      "promo-waiting": {
        id: "promo-waiting",
        project_id: "alpha-project",
        actor_agent_id: "alpha-lead",
        source_branch: "agents/alpha-lead/waiting",
        target_branch: "team/alpha/reviews/waiting",
        promotion_kind: "review",
        state: "succeeded",
        commit_sha: "bbb222",
        outputs: {},
        error: null,
      },
      "promo-accepted": {
        id: "promo-accepted",
        project_id: "alpha-project",
        actor_agent_id: "alpha-lead",
        source_branch: "agents/alpha-lead/accepted",
        target_branch: "team/alpha/reviews/accepted",
        promotion_kind: "review",
        state: "succeeded",
        commit_sha: "ccc333",
        outputs: {},
        error: null,
      },
      "promo-rejected": {
        id: "promo-rejected",
        project_id: "beta-project",
        actor_agent_id: "beta-lead",
        source_branch: "agents/beta-lead/rejected",
        target_branch: "team/beta/reviews/rejected",
        promotion_kind: "review",
        state: "succeeded",
        commit_sha: "ddd444",
        outputs: {},
        error: null,
      },
      "promo-blocked": {
        id: "promo-blocked",
        project_id: "beta-project",
        actor_agent_id: "beta-lead",
        source_branch: "agents/beta-lead/blocked",
        target_branch: "team/beta/reviews/blocked",
        promotion_kind: "review",
        state: "failed",
        commit_sha: "eee555",
        outputs: {},
        error: "Blocked by upstream dependency",
      },
      "promo-release": {
        id: "promo-release",
        project_id: "alpha-project",
        actor_agent_id: "alpha-lead",
        source_branch: "team/alpha/releases/accepted",
        target_branch: "releases/alpha/accepted",
        promotion_kind: "release",
        state: "succeeded",
        commit_sha: "ccc333",
        outputs: {},
        error: null,
      },
    },
    release_packages: {
      "release-accepted": {
        id: "release-accepted",
        project_id: "alpha-project",
        summary: "Ship the accepted delivery after targeted verification.",
        deliverable_ids: ["deliverable-accepted"],
        artifact_refs: ["artifacts/release-accepted.zip"],
        feature_branches: ["team/alpha/features/accepted"],
        release_branch: "team/alpha/releases/accepted",
        canonical_branch: "releases/alpha/accepted",
        published_commit_sha: "ccc333",
        verification_waivers: [
          {
            deliverable_id: "deliverable-accepted",
            verification_status: "partial",
            reason: "Operator environment unavailable.",
            actor_kind: "operator",
            actor_id: "release-admin",
          },
        ],
        scope_validation: {
          state: "passed",
          unexpected_commit_shas: [],
          unexpected_paths: [],
        },
        git_promotion_record_ids: ["promo-release"],
        state: "accepted",
        published_at: "2026-06-09T15:00:00Z",
        error: null,
      },
    },
    inbox: {
      "inbox-1": {
        id: "inbox-1",
        recipient_agent_id: "alpha-lead",
        item_type: "status_request",
        sender_agent_id: "alpha-manager",
        task_id: "task-waiting",
        payload: { question: "What is the status?" },
        state: "pending",
      },
    },
    runtime_operations: {
      generated_at: "2026-06-09T16:00:00Z",
      agents: [
        {
          id: "alpha-lead",
          team_id: "team-alpha",
          project_id: "alpha-project",
          session_id: "session-alpha-lead",
          session_is_expired: true,
          session_expires_at: "2026-06-09T15:45:00Z",
          session_status: "expired",
          wake_due: true,
          lease_active: false,
          active_dispatch_id: "",
          wake_reason: "status_request",
          last_wake_at: "2026-06-09T15:30:00Z",
          next_wake_at: "2026-06-09T16:05:00Z",
          wake_leased_until: "",
          no_progress_count: 1,
          context_clearing_policy: "idle",
          context_rotation_pending: false,
          auto_context_clearing_enabled: true,
          last_context_rotated_at: "2026-06-09T15:00:00Z",
        },
      ],
      attention: [
        {
          id: "alpha-lead:expired-session",
          kind: "expired_agent_session",
          severity: "critical",
          title: "Expired session for alpha-lead",
          team_id: "team-alpha",
          project_id: "alpha-project",
          agent_id: "alpha-lead",
          session_id: "session-alpha-lead",
          state: "expired",
          detail: "agent alpha-lead; session session-alpha-lead expired at 2026-06-09T15:45:00Z",
        },
        {
          id: "attention-blocked",
          kind: "blocked_task",
          severity: "warning",
          title: "Blocked delivery",
          team_id: "team-beta",
          project_id: "beta-project",
          agent_id: "beta-lead",
          task_id: "task-blocked",
          state: "blocked",
          detail: "Work blocked by an upstream issue.",
        },
      ],
      session_health: [
        {
          agent_id: "alpha-lead",
          team_id: "team-alpha",
          project_ids: ["alpha-project"],
          lead_project_ids: ["alpha-project"],
          role_id: "alpha-project__project-lead",
          session_id: "session-alpha-lead",
          session_present: true,
          expires_at: "2026-06-09T15:45:00Z",
          is_expired: true,
          status: "expired",
        },
      ],
    },
    review_profiles: {
      "profile-alpha": {
        id: "profile-alpha",
        team_id: "team-alpha",
        project_id: "alpha-project",
        name: "Alpha review profile",
        description: "Checklist for alpha deliverables.",
        applicable_role_ids: ["alpha-project__project-lead"],
        criteria: [
          {
            id: "tests",
            label: "Tests",
            description: "Run targeted checks",
            required: true,
          },
          {
            id: "docs",
            label: "Docs",
            description: "Update review notes",
            required: false,
          },
        ],
        archived: false,
      },
    },
  };
}

test("team add-on Teams tab groups records and opens detail panes", async (t) => {
  const initialState = seededGitState();
  initialState.agents["alpha-manager"].display_name = "Ada Manager";
  initialState.agents["alpha-lead"].display_name = "Grace Lead";
  initialState.agents["beta-manager"].display_name = "Zed Manager";
  initialState.agents["beta-lead"].display_name = "Beta Lead";
  initialState.teams["team-archived"] = {
    id: "team-archived",
    name: "Archived Team",
    manager_agent_id: "archived-agent",
    operator_communication_target_id: "ops-archived",
    project_ids: ["archived-project"],
    archived: true,
  };
  initialState.projects["archived-project"] = {
    id: "archived-project",
    team_id: "team-archived",
    name: "Archived Project",
    lead_agent_id: "archived-agent",
    goals: [],
    git: {},
    status: "active",
  };
  initialState.roles["archived-project__project-lead"] = {
    id: "archived-project__project-lead",
    team_id: "team-archived",
    project_id: "archived-project",
    name: "Archived Project Lead",
    description: "",
    permissions: ["create_root_task"],
    ephemeral_policy: { max_concurrent: 0 },
    archived: false,
  };
  initialState.agents["archived-agent"] = {
    id: "archived-agent",
    team_id: "team-archived",
    project_id: "archived-project",
    role_id: "archived-project__project-lead",
    session_id: "session-archived-agent",
    reports_to_agent_id: null,
    subordinate_agent_ids: [],
    ephemeral_policy: { max_concurrent: 0 },
    status: "active",
  };
  const { port } = await createTeamAddonServer(t, {
    initialState,
  });
  const page = await openBrowserPage(t, port);

  await waitForRevision(page, 4);
  await page.getByRole("tab", { name: "Teams" }).click();
  await page.locator("#team-people-page").waitFor({ state: "visible" });

  assert.equal(await page.locator("#team-people-page .team-record-group").count(), 2);
  assert.equal(await page.locator("#team-people-page .team-record-group.is-collapsed").count(), 2);
  assert.equal(await page.locator("#team-people-page .team-record-section").count(), 0);
  assert.equal(await page.locator('#team-people-page [data-team-record-group-id="team-archived"]').count(), 0);
  assert.equal(await page.locator('#team-people-page [data-project-id="archived-project"]').count(), 0);
  assert.equal(await page.locator('#team-people-page [data-role-id="archived-project__project-lead"]').count(), 0);
  const alphaGroup = page.locator('#team-people-page [data-team-record-group-id="team-alpha"]');
  const betaGroup = page.locator('#team-people-page [data-team-record-group-id="team-beta"]');
  const alphaCounts = await alphaGroup.locator(".team-record-group-counts").innerText();
  assert.match(alphaCounts, /2 agents/);
  assert.match(alphaCounts, /1 project/);
  assert.match(alphaCounts, /2 roles/);
  const betaCounts = await betaGroup.locator(".team-record-group-counts").innerText();
  assert.match(betaCounts, /2 agents/);
  assert.match(betaCounts, /1 project/);
  assert.match(betaCounts, /2 roles/);

  const betaTeamHeader = betaGroup.locator(".team-record-group-header");
  assert.equal(
    await betaTeamHeader.evaluate((element) => getComputedStyle(element).borderBottomWidth),
    "0px",
  );
  await betaTeamHeader.hover();
  const betaHoverColors = await betaTeamHeader.evaluate((element) => {
    const resolveColor = (value) => {
      const probe = document.createElement("span");
      probe.style.color = value;
      document.body.append(probe);
      const resolved = getComputedStyle(probe).color;
      probe.remove();
      return resolved;
    };
    const rootStyles = getComputedStyle(document.documentElement);
    return {
      background: getComputedStyle(element).backgroundColor,
      panelAlt: resolveColor(rootStyles.getPropertyValue("--panel-alt").trim()),
    };
  });
  assert.equal(betaHoverColors.background, betaHoverColors.panelAlt);
  await betaTeamHeader.click();
  await betaGroup.locator(".team-record-group-body").waitFor({ state: "visible" });
  assert.equal(await betaGroup.locator('[data-project-id="beta-project"]').count(), 0);
  await betaGroup.locator('[data-team-record-tab="project"]').click();
  assert.equal(await betaGroup.locator('[data-project-id="beta-project"]').count(), 1);
  assert.equal(await betaGroup.locator('[data-role-id="beta-project__project-lead"]').count(), 0);
  await betaGroup.locator('[data-team-record-tab="role"]').click();
  assert.equal(await betaGroup.locator('[data-role-id="beta-project__project-lead"]').count(), 1);

  await alphaGroup.locator(".team-record-group-header").click();
  await alphaGroup.locator(".team-record-group-body").waitFor({ state: "visible" });
  await page.locator("#people-agent-detail-view").getByRole("heading", { name: "Alpha Team" }).waitFor({
    state: "visible",
  });

  await alphaGroup.locator('[data-team-record-tab="project"]').click();
  const alphaProjectRecord = alphaGroup.locator('[data-project-id="alpha-project"]');
  await alphaProjectRecord.getByRole("button").click();
  await alphaProjectRecord.locator('.team-record-select[aria-pressed="true"]').waitFor({
    state: "visible",
  });
  await page.locator("#people-agent-detail-view").getByRole("heading", { name: "Alpha Project" }).waitFor({
    state: "visible",
  });
  await page
    .locator("#people-agent-detail-view .agent-detail-section strong")
    .filter({ hasText: /^Ship alpha work$/ })
    .waitFor({
      state: "visible",
    });

  await alphaGroup.locator('[data-team-record-tab="role"]').click();
  const alphaLeadRoleCard = alphaGroup.locator('[data-role-id="alpha-project__project-lead"]');
  await alphaLeadRoleCard.getByRole("button").click();
  await alphaLeadRoleCard.locator('.team-record-select[aria-pressed="true"]').waitFor({
    state: "visible",
  });
  await page.locator("#people-agent-detail-view").getByRole("heading", { name: "Project Lead" }).waitFor({
    state: "visible",
  });
  await page.locator("#people-agent-detail-view").getByText("Create Root Task").waitFor({
    state: "visible",
  });

  await alphaGroup.locator('[data-team-record-tab="agent"]').click();
  const alphaLeadCard = alphaGroup.locator('[data-agent-id="alpha-lead"]');
  await alphaLeadCard.getByRole("button").click();
  await alphaLeadCard.locator('.people-agent-select[aria-pressed="true"]').waitFor({
    state: "visible",
  });
  await page.locator("#people-agent-detail-view").getByRole("heading", { name: "Grace Lead" }).waitFor({
    state: "visible",
  });
  assert.equal((await alphaLeadCard.innerText()).includes("alpha-lead"), false);
});

test("team add-on Teams tab creates records inside the selected team", async (t) => {
  const initialState = seededGitState();
  initialState.agents["beta-candidate"] = {
    id: "beta-candidate",
    team_id: "team-beta",
    project_id: null,
    display_name: "Beta Candidate",
    role_id: "team-beta__manager",
    session_id: "session-beta-candidate",
    reports_to_agent_id: "beta-manager",
    subordinate_agent_ids: [],
    ephemeral_policy: { max_concurrent: 0 },
    status: "active",
  };
  initialState.agents["beta-manager"].subordinate_agent_ids.push("beta-candidate");
  const { port, requests } = await createTeamAddonServer(t, {
    initialState,
  });
  const page = await openBrowserPage(t, port);

  await waitForRevision(page, 4);
  await page.getByRole("tab", { name: "Teams" }).click();
  await page.locator("#team-people-page").waitFor({ state: "visible" });

  const betaGroup = page.locator('#team-people-page [data-team-record-group-id="team-beta"]');
  await betaGroup.waitFor({ state: "visible" });
  await betaGroup.locator(".team-record-group-header").click();
  await betaGroup.locator(".team-record-group-body").waitFor({ state: "visible" });

  await betaGroup.getByRole("button", { name: "Add agent" }).click();
  const inlineAgentForm = betaGroup.locator('form[data-form-id="register-agent-form"]');
  await inlineAgentForm.waitFor({ state: "visible" });
  assert.equal(await inlineAgentForm.locator('select[name="team_id"]').count(), 0);
  await inlineAgentForm.locator('[name="role_id"]').selectOption("team-beta__manager");
  await inlineAgentForm.locator('[name="session_id"]').fill("session-beta-ops");
  await inlineAgentForm.locator('[name="display_name"]').fill("Beta Ops");
  await inlineAgentForm.locator('[name="reports_to_agent_id"]').selectOption("beta-manager");
  await inlineAgentForm.locator('[name="max_concurrent"]').fill("1");
  await inlineAgentForm.getByRole("button", { name: "Register agent" }).click();
  await page.getByText("Saved revision 5.").waitFor({ state: "visible" });

  await betaGroup.locator('[data-team-record-tab="role"]').click();
  await betaGroup.getByRole("button", { name: "Add role" }).click();
  const inlineRoleForm = betaGroup.locator('form[data-form-id="create-role-form"]');
  await inlineRoleForm.waitFor({ state: "visible" });
  assert.equal(await inlineRoleForm.locator('select[name="team_id"]').count(), 0);
  assert.equal(
    await inlineRoleForm.locator('[name="permissions"] option[value="request_release"]').getAttribute("title"),
    "Allows a same-project role holder to request Team release packages from accepted deliverables. Project leads and managers can already do this through authority.",
  );
  await inlineRoleForm.locator('[name="project_id"]').selectOption("beta-project");
  await inlineRoleForm.locator('[name="name"]').fill("QA");
  await inlineRoleForm.locator('[name="permissions"]').selectOption(["request_release"]);
  await inlineRoleForm.locator('[name="max_concurrent"]').fill("1");
  await inlineRoleForm.getByRole("button", { name: "Create role" }).click();
  await page.getByText("Saved revision 6.").waitFor({ state: "visible" });

  await betaGroup.locator('[data-team-record-tab="project"]').click();
  await betaGroup.getByRole("button", { name: "Add project" }).click();
  const inlineProjectForm = betaGroup.locator('form[data-form-id="create-project-form"]');
  await inlineProjectForm.waitFor({ state: "visible" });
  assert.equal(await inlineProjectForm.locator('select[name="team_id"]').count(), 0);
  assert.equal(await inlineProjectForm.locator('[name="lead_agent_id"] option[value="beta-manager"]').count(), 1);
  await inlineProjectForm.locator("summary", { hasText: "Git config" }).click();
  assert.equal(await inlineProjectForm.getByRole("button", { name: "Import Git repo" }).count(), 1);
  await inlineProjectForm.locator('[name="repo_source"]').fill("/srv/repos/second-project");
  await inlineProjectForm.getByRole("button", { name: "Import Git repo" }).click();
  await page
    .locator("#status-message", {
      hasText: "Imported Git repo. Review the populated Git fields, then save.",
    })
    .waitFor({ state: "visible" });
  assert.equal(await inlineProjectForm.locator('[name="base_branch"]').inputValue(), "main");
  assert.equal(
    await inlineProjectForm.locator('[name="managed_repo_id"]').inputValue(),
    "mr_0123456789abcdef0123",
  );
  await inlineProjectForm.locator('[name="name"]').fill("Second Project");
  await inlineProjectForm.locator('[name="lead_agent_id"]').selectOption("beta-manager");
  await inlineProjectForm.locator('[name="goal_description"]').fill("Ship beta follow-up");
  await inlineProjectForm.getByRole("button", { name: "Create project" }).click();
  await page.getByText("Saved revision 7.").waitFor({ state: "visible" });

  const mutationRequests = requests.filter(
    (request) =>
      request.pathname.startsWith("/api/addons/team-addon/") &&
      request.pathname !== "/api/addons/team-addon/projects/git/import",
  );
  assert.deepEqual(
    mutationRequests.map((request) => [request.method, request.pathname, request.payload.expected_revision]),
    [
      ["POST", "/api/addons/team-addon/agents", 4],
      ["POST", "/api/addons/team-addon/roles", 5],
      ["POST", "/api/addons/team-addon/projects", 6],
    ],
  );
  const inlineImportRequest = requests.find(
    (request) =>
      request.pathname === "/api/addons/team-addon/projects/git/import" &&
      request.payload.repo_source === "/srv/repos/second-project",
  );
  assert.ok(inlineImportRequest);
  assert.deepEqual(inlineImportRequest.payload, {
    repo_source: "/srv/repos/second-project",
  });
  assert.equal(mutationRequests[0].payload.team_id, "team-beta");
  assert.equal(mutationRequests[0].payload.display_name, "Beta Ops");
  assert.equal(mutationRequests[1].payload.team_id, "team-beta");
  assert.equal(mutationRequests[1].payload.project_id, "beta-project");
  assert.deepEqual(mutationRequests[1].payload.permissions, ["request_release"]);
  assert.equal(mutationRequests[2].payload.team_id, "team-beta");
  assert.equal(mutationRequests[2].payload.lead_agent_id, "beta-manager");
  assert.deepEqual(mutationRequests[2].payload.goals, [{ description: "Ship beta follow-up" }]);
  assert.deepEqual(mutationRequests[2].payload.git, {
    managed_repo_id: "mr_0123456789abcdef0123",
    base_branch: "main",
  });
});

test("team add-on agent edit scopes project, role, and superior choices to the selected team", async (t) => {
  const initialState = seededGitState();
  initialState.teams["team-alpha"].project_ids.push("alpha-second-project");
  initialState.projects["alpha-second-project"] = {
    id: "alpha-second-project",
    team_id: "team-alpha",
    name: "Alpha Second Project",
    lead_agent_id: "alpha-manager",
    goals: [],
    git: null,
    status: "active",
  };
  const { port, requests } = await createTeamAddonServer(t, {
    initialState,
  });
  const page = await openBrowserPage(t, port);

  await waitForRevision(page, 4);
  await page.getByRole("tab", { name: "Teams" }).click();
  await page.locator("#team-people-page").waitFor({ state: "visible" });

  const alphaGroup = page.locator('#team-people-page [data-team-record-group-id="team-alpha"]');
  await alphaGroup.waitFor({ state: "visible" });
  if (!(await alphaGroup.locator(".team-record-group-body").isVisible().catch(() => false))) {
    await alphaGroup.locator(".team-record-group-header").click();
  }
  await alphaGroup.locator(".team-record-group-body").waitFor({ state: "visible" });

  await alphaGroup.locator('[data-agent-id="alpha-lead"]').getByRole("button").click();
  await page.locator("#people-agent-detail-view").getByRole("heading", { name: "alpha-lead" }).waitFor({
    state: "visible",
  });
  await page.locator('#people-agent-detail-view [data-edit-type="agent"][data-edit-id="alpha-lead"]').click();
  await page.locator("#edit-modal-title", { hasText: "Edit agent alpha-lead" }).waitFor({
    state: "visible",
  });

  const form = page.locator("#edit-record-form");
  const projectSelect = form.locator('select[name="project_ids"]');
  assert.equal(await projectSelect.getAttribute("multiple"), "");
  assert.equal(await projectSelect.locator('option[value="alpha-project"]').count(), 1);
  assert.equal(await projectSelect.locator('option[value="alpha-second-project"]').count(), 1);
  assert.equal(await projectSelect.locator('option[value="beta-project"]').count(), 0);
  assert.equal(await form.locator('select[name="role_id"] option[value="alpha-project__project-lead"]').count(), 1);
  assert.equal(await form.locator('select[name="role_id"] option[value="beta-project__project-lead"]').count(), 0);
  assert.equal(await form.locator('select[name="reports_to_agent_id"] option[value="alpha-manager"]').count(), 1);
  assert.equal(await form.locator('select[name="reports_to_agent_id"] option[value="alpha-lead"]').count(), 0);
  assert.equal(await form.locator('select[name="reports_to_agent_id"] option[value="beta-manager"]').count(), 0);

  await projectSelect.selectOption(["alpha-project", "alpha-second-project"]);
  await form.locator('select[name="team_id"]').evaluate((select) => {
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
  assert.deepEqual(
    await projectSelect.evaluate((select) =>
      Array.from(select.selectedOptions).map((option) => option.value).sort(),
    ),
    ["alpha-project", "alpha-second-project"],
  );

  await form.locator('select[name="team_id"]').selectOption("team-beta");
  await waitForSelectOption(page, '#edit-record-form select[name="project_ids"]', "beta-project");
  await waitForSelectOption(page, '#edit-record-form select[name="role_id"]', "beta-project__project-lead");
  await waitForSelectOption(page, '#edit-record-form select[name="reports_to_agent_id"]', "beta-manager");

  assert.equal(await projectSelect.locator('option[value="alpha-project"]').count(), 0);
  assert.equal(await form.locator('select[name="role_id"] option[value="alpha-project__project-lead"]').count(), 0);
  assert.equal(await form.locator('select[name="reports_to_agent_id"] option[value="alpha-manager"]').count(), 0);
  assert.equal(await projectSelect.inputValue(), "");
  assert.equal(await form.locator('select[name="role_id"]').inputValue(), "");
  assert.equal(await form.locator('select[name="reports_to_agent_id"]').inputValue(), "");

  await form.locator('select[name="team_id"]').selectOption("team-alpha");
  await waitForSelectOption(page, '#edit-record-form select[name="project_ids"]', "alpha-second-project");
  await projectSelect.selectOption(["alpha-project", "alpha-second-project"]);
  await form.locator('select[name="role_id"]').selectOption("alpha-project__project-lead");
  await form.locator('select[name="reports_to_agent_id"]').selectOption("alpha-manager");
  await form.getByRole("button", { name: "Save changes" }).click();
  await page.locator("#status-message", { hasText: /Saved revision \d+\./ }).waitFor({
    state: "visible",
  });

  const editRequest = requests.find(
    (request) =>
      request.method === "PATCH" &&
      request.pathname === "/api/addons/team-addon/agents/alpha-lead",
  );
  assert.ok(editRequest);
  assert.deepEqual([...editRequest.payload.project_ids].sort(), [
    "alpha-project",
    "alpha-second-project",
  ]);
  assert.equal(Object.hasOwn(editRequest.payload, "project_id"), false);
});

function seededLongChatState(messageCount = 120) {
  const state = seededGitState();
  state.revision = 9;
  state.agents["alpha-lead"].display_name = "Ada";
  for (let index = 1; index <= 30; index += 1) {
    const suffix = String(index).padStart(2, "0");
    state.agents[`scroll-agent-${suffix}`] = {
      id: `scroll-agent-${suffix}`,
      team_id: "team-alpha",
      project_id: "alpha-project",
      role_id: "alpha-project__implementer",
      display_name: `Scrollable Agent ${suffix}`,
      session_id: `session-scroll-agent-${suffix}`,
      reports_to_agent_id: "alpha-lead",
      subordinate_agent_ids: [],
      ephemeral_policy: { max_concurrent: 0 },
      status: "active",
    };
  }
  state.conversations = {
    "conversation-alpha-lead": {
      id: "conversation-alpha-lead",
      agent_id: "alpha-lead",
      created_by: "operator",
      last_message_preview: `Message ${String(messageCount - 1).padStart(3, "0")}`,
      created_at: "2026-06-09T00:00:00Z",
      updated_at: "2026-06-09T02:00:00Z",
      last_interaction_at: "2026-06-09T02:00:00Z",
    },
  };
  state.conversation_messages = {};
  for (let index = 0; index < messageCount; index += 1) {
    const timestamp = new Date(Date.UTC(2026, 5, 9, 0, index, 0)).toISOString();
    state.conversation_messages[`msg-${String(index).padStart(3, "0")}`] = {
      id: `msg-${String(index).padStart(3, "0")}`,
      conversation_id: "conversation-alpha-lead",
      sender: index % 2 === 0 ? "operator" : "agent",
      message_type: "text",
      body: `Message ${String(index).padStart(3, "0")}`,
      status: "sent",
      dispatch_id: null,
      voice_session_id: null,
      source_message_id: null,
      error: null,
      metadata: {},
      created_at: timestamp,
      updated_at: timestamp,
    };
  }
  return state;
}

function seededTwoConversationChatState() {
  const state = seededLongChatState(80);
  state.agents["beta-lead"].display_name = "Bea";
  state.conversations["conversation-beta-lead"] = {
    id: "conversation-beta-lead",
    agent_id: "beta-lead",
    created_by: "operator",
    last_message_preview: "Beta message 019",
    created_at: "2026-06-09T00:00:00Z",
    updated_at: "2026-06-09T01:00:00Z",
    last_interaction_at: "2026-06-09T01:00:00Z",
  };
  for (let index = 0; index < 20; index += 1) {
    const timestamp = new Date(Date.UTC(2026, 5, 9, 0, index, 30)).toISOString();
    state.conversation_messages[`beta-msg-${String(index).padStart(3, "0")}`] = {
      id: `beta-msg-${String(index).padStart(3, "0")}`,
      conversation_id: "conversation-beta-lead",
      sender: index % 2 === 0 ? "operator" : "agent",
      message_type: "text",
      body: `Beta message ${String(index).padStart(3, "0")}`,
      status: "sent",
      dispatch_id: null,
      voice_session_id: null,
      source_message_id: null,
      error: null,
      metadata: {},
      created_at: timestamp,
      updated_at: timestamp,
    };
  }
  return state;
}

function seededLongConversationListState(conversationCount = 30) {
  const state = seededLongChatState(4);
  state.conversations = {};
  state.conversation_messages = {};
  for (let index = 1; index <= conversationCount; index += 1) {
    const suffix = String(index).padStart(2, "0");
    const agentId = `scroll-agent-${suffix}`;
    const timestamp = new Date(Date.UTC(2026, 5, 9, 1, index, 0)).toISOString();
    state.conversations[`conversation-${agentId}`] = {
      id: `conversation-${agentId}`,
      agent_id: agentId,
      created_by: "operator",
      last_message_preview: `Message ${suffix}`,
      created_at: timestamp,
      updated_at: timestamp,
      last_interaction_at: timestamp,
    };
  }
  return state;
}

test("team add-on Chat keeps recent conversations in an internal scroll region", async (t) => {
  const { port } = await createTeamAddonServer(t, {
    initialState: seededLongConversationListState(),
  });
  const page = await openBrowserPage(t, port, { viewport: { width: 1280, height: 720 } });

  await waitForRevision(page, 9);
  await page.locator("#team-view-chat-tab").click();
  const conversationList = page.locator(".chat-conversation-list");
  await conversationList.locator('[data-chat-conversation-id="conversation-scroll-agent-01"]').waitFor({
    state: "attached",
  });

  const metrics = await page.evaluate(() => {
    const list = document.querySelector(".chat-conversation-list");
    const sidebar = document.querySelector(".chat-sidebar");
    const pageRoot = document.documentElement;
    if (!(list instanceof HTMLElement) || !(sidebar instanceof HTMLElement)) {
      return null;
    }
    const listBox = list.getBoundingClientRect();
    const sidebarBox = sidebar.getBoundingClientRect();
    return {
      listBottom: listBox.bottom,
      overflowY: getComputedStyle(list).overflowY,
      scrollHeight: list.scrollHeight,
      clientHeight: list.clientHeight,
      sidebarBottom: sidebarBox.bottom,
      documentClientHeight: pageRoot.clientHeight,
      documentScrollHeight: pageRoot.scrollHeight,
    };
  });
  assert.ok(metrics, "expected recent conversation list metrics");
  assert.match(metrics.overflowY, /auto|scroll/);
  assert.ok(
    metrics.scrollHeight > metrics.clientHeight,
    `expected recent conversations to scroll internally: ${JSON.stringify(metrics)}`,
  );
  assert.ok(
    metrics.listBottom <= metrics.sidebarBottom + 2,
    `expected recent conversation list to stay inside sidebar: ${JSON.stringify(metrics)}`,
  );
  assert.ok(
    metrics.documentScrollHeight <= metrics.documentClientHeight + 4,
    `expected recent conversation list not to overflow the page: ${JSON.stringify(metrics)}`,
  );

  await conversationList.evaluate((element) => {
    element.scrollTop = element.scrollHeight;
    element.dispatchEvent(new Event("scroll"));
  });
  await conversationList.locator('[data-chat-conversation-id="conversation-scroll-agent-01"]').scrollIntoViewIfNeeded();
  assert.ok(
    (await conversationList.evaluate((element) => element.scrollTop)) > 0,
    "expected scrolling to reveal older recent conversations",
  );
});

test("team add-on Chat uses a usable mobile conversation and thread flow", async (t) => {
  const { port } = await createTeamAddonServer(t, {
    initialState: seededLongChatState(),
  });
  const page = await openBrowserPage(t, port, { viewport: { width: 390, height: 844 } });

  await waitForRevision(page, 9);
  await page.locator("#team-view-chat-tab").click();
  await page.locator('[data-chat-conversation-id="conversation-alpha-lead"]').waitFor({
    state: "visible",
  });

  const chatPage = page.locator("#team-chat-page");
  const sidebar = page.locator(".chat-sidebar");
  const main = page.locator(".chat-main");
  const tabMetrics = await page.locator(".team-view-tabs").evaluate((element) => ({
    clientWidth: element.clientWidth,
    scrollWidth: element.scrollWidth,
    height: element.getBoundingClientRect().height,
  }));
  assert.ok(tabMetrics.scrollWidth > tabMetrics.clientWidth, "expected mobile tabs to scroll horizontally");
  assert.ok(tabMetrics.height < 60, `expected a single-row mobile tab strip: ${JSON.stringify(tabMetrics)}`);
  assert.equal(await sidebar.isVisible(), true);
  assert.equal(await main.isVisible(), false);
  assert.equal(await chatPage.evaluate((element) => element.scrollWidth <= element.clientWidth + 1), true);

  await page.locator('[data-chat-conversation-id="conversation-alpha-lead"]').click();
  await page.locator(".chat-message--agent").last().waitFor({ state: "visible" });
  assert.equal(await sidebar.isVisible(), false);
  assert.equal(await main.isVisible(), true);

  const threadMetrics = await page.locator(".chat-thread").evaluate((thread) => {
    const messageList = thread.querySelector(".chat-message-list");
    const composer = thread.querySelector(".chat-composer");
    if (!(messageList instanceof HTMLElement) || !(composer instanceof HTMLElement)) {
      return null;
    }
    const threadBox = thread.getBoundingClientRect();
    const messageBox = messageList.getBoundingClientRect();
    const composerBox = composer.getBoundingClientRect();
    return {
      threadHeight: threadBox.height,
      messageHeight: messageBox.height,
      composerBottom: composerBox.bottom,
      threadBottom: threadBox.bottom,
    };
  });
  assert.ok(threadMetrics, "expected mobile chat thread metrics");
  assert.ok(
    threadMetrics.threadHeight >= 500,
    `expected a full-height mobile chat thread: ${JSON.stringify(threadMetrics)}`,
  );
  assert.ok(
    threadMetrics.messageHeight >= 150,
    `expected agent messages to have a usable scroll region: ${JSON.stringify(threadMetrics)}`,
  );
  assert.ok(
    threadMetrics.composerBottom <= threadMetrics.threadBottom + 2,
    `expected the composer to remain inside the chat thread: ${JSON.stringify(threadMetrics)}`,
  );

  await page.locator("[data-chat-mobile-conversations]").click();
  assert.equal(await sidebar.isVisible(), true);
  assert.equal(await main.isVisible(), false);
});

test("team add-on Chat panel fast-loads the selected thread, stays bounded, and loads older messages", async (t) => {
  const { port, requests, chatRequests } = await createTeamAddonServer(t, {
    initialState: seededLongChatState(),
  });
  const page = await openBrowserPage(t, port, { viewport: { width: 1280, height: 720 } });

  await waitForRevision(page, 9);
  await page.locator("#team-view-chat-tab").click();
  await page.locator("#status-message").waitFor({ state: "hidden" });

  const messageList = page.locator(".chat-message-list");
  await messageList.getByText("Message 119").waitFor({ state: "visible" });
  assert.equal(await page.locator(".chat-message").count(), 50);
  assert.equal(await messageList.getByText("Message 000").count(), 0);
  assert.ok(
    chatRequests.some(
      (request) =>
        request.method === "GET" &&
        (
          request.pathname === "/api/addons/team-addon/views/chat" ||
          request.pathname === "/api/addons/team-addon/conversations"
        ) &&
        String(request.search || "").includes("include_selected_messages=1"),
    ),
    "expected initial chat load to request selected conversation messages in a collection payload",
  );

  let initialChatScrollMetrics = null;
  try {
    await waitForCondition(async () => {
      initialChatScrollMetrics = await messageList.evaluate((element) => {
        const bottomGap = element.scrollHeight - element.clientHeight - element.scrollTop;
        return {
          bottomGap,
          clientHeight: element.clientHeight,
          scrollHeight: element.scrollHeight,
          scrollTop: element.scrollTop,
        };
      });
      return initialChatScrollMetrics.scrollHeight > initialChatScrollMetrics.clientHeight &&
        initialChatScrollMetrics.bottomGap <= 4;
    }, "expected selected chat thread to settle at the bottom");
  } catch (error) {
    throw new Error(
      `${error instanceof Error ? error.message : String(error)}: ${JSON.stringify(initialChatScrollMetrics)}`,
    );
  }

  const viewport = page.viewportSize();
  assert.ok(viewport, "expected viewport size");
  const layoutBox = await page.locator("#team-chat-page").boundingBox();
  assert.ok(layoutBox, "expected chat page bounds");
  assert.ok(
    layoutBox.y + layoutBox.height <= viewport.height + 2,
    `expected chat page to stay within the viewport: ${JSON.stringify({ layoutBox, viewport })}`,
  );
  const documentOverflow = await page.evaluate(() => ({
    clientHeight: document.documentElement.clientHeight,
    scrollHeight: document.documentElement.scrollHeight,
  }));
  assert.ok(
    documentOverflow.scrollHeight <= documentOverflow.clientHeight + 4,
    `expected chat history not to overflow the page: ${JSON.stringify(documentOverflow)}`,
  );

  await messageList.evaluate((element) => {
    element.scrollTop = 0;
    element.dispatchEvent(new Event("scroll"));
  });
  await waitForCondition(
    () =>
      requests.some(
        (request) =>
          request.method === "GET" &&
          request.pathname ===
            "/api/addons/team-addon/conversations/conversation-alpha-lead/messages" &&
          String(request.search || "").includes("before=msg-070"),
      ),
    "expected scrolling upward to request the older chat page",
  );
  await messageList.getByText("Message 020").waitFor({ state: "visible" });
  const renderedMessages = await page.evaluate(() =>
    Array.from(document.querySelectorAll(".chat-message")).map((element) => element.textContent || ""),
  );
  assert.ok(renderedMessages.some((message) => message.includes("Message 020")));
  let afterPrependMetrics = null;
  try {
    await waitForCondition(async () => {
      afterPrependMetrics = await messageList.evaluate((element) => ({
        clientHeight: element.clientHeight,
        scrollHeight: element.scrollHeight,
        scrollTop: element.scrollTop,
      }));
      return afterPrependMetrics.scrollTop > 0;
    }, "expected scroll position to be preserved after prepend");
  } catch (error) {
    throw new Error(
      `${error instanceof Error ? error.message : String(error)}: ${JSON.stringify(afterPrependMetrics)}`,
    );
  }
  const gridPlacement = await page.evaluate(() => {
    const thread = document.querySelector(".chat-thread");
    const list = document.querySelector(".chat-message-list");
    const composer = document.querySelector(".chat-composer");
    if (!(thread instanceof HTMLElement) || !(list instanceof HTMLElement) || !(composer instanceof HTMLElement)) {
      return null;
    }
    return {
      hasCallStripClass: thread.classList.contains("has-call-strip"),
      listConversationId: list.dataset.chatConversationId || null,
      scrollConversationId: list.dataset.chatScrollConversationId || null,
      listGridRowStart: getComputedStyle(list).gridRowStart,
      composerGridRowStart: getComputedStyle(composer).gridRowStart,
    };
  });
  assert.deepEqual(gridPlacement, {
    hasCallStripClass: false,
    listConversationId: null,
    scrollConversationId: "conversation-alpha-lead",
    listGridRowStart: "2",
    composerGridRowStart: "3",
  });
  await page.getByRole("button", { name: "New chat" }).click();
  const picker = page.locator(".chat-agent-picker");
  await picker.waitFor({ state: "visible" });
  const pickerMetrics = await picker.evaluate((element) => {
    const sidebar = document.querySelector(".chat-sidebar");
    if (!(sidebar instanceof HTMLElement)) {
      return null;
    }
    const pickerBox = element.getBoundingClientRect();
    const sidebarBox = sidebar.getBoundingClientRect();
    return {
      clientHeight: element.clientHeight,
      overflowY: getComputedStyle(element).overflowY,
      pickerBottom: pickerBox.bottom,
      scrollHeight: element.scrollHeight,
      sidebarBottom: sidebarBox.bottom,
    };
  });
  assert.ok(pickerMetrics, "expected chat picker metrics");
  assert.match(pickerMetrics.overflowY, /auto|scroll/);
  assert.ok(
    pickerMetrics.scrollHeight > pickerMetrics.clientHeight,
    `expected long agent picker to scroll internally: ${JSON.stringify(pickerMetrics)}`,
  );
  assert.ok(
    pickerMetrics.pickerBottom <= pickerMetrics.sidebarBottom + 2,
    `expected picker to stay inside sidebar: ${JSON.stringify(pickerMetrics)}`,
  );
  const lateAgent = page.locator('[data-chat-agent-id="scroll-agent-30"]');
  await lateAgent.evaluate((element) => element.scrollIntoView({ block: "end" }));
  await lateAgent.click();
  await waitForCondition(
    () =>
      requests.some(
        (request) =>
          request.method === "POST" &&
          request.pathname === "/api/addons/team-addon/conversations" &&
          request.payload?.agent_id === "scroll-agent-30",
      ),
    "expected selecting a late-list chat agent to create or focus that chat",
  );
});

test("team add-on Chat opens the conversation from a deep link", async (t) => {
  const { port, chatRequests } = await createTeamAddonServer(t, {
    initialState: seededTwoConversationChatState(),
  });
  const page = await openBrowserPage(
    t,
    port,
    { viewport: { width: 1280, height: 720 } },
    "/addons/team-addon/?view=chat&conversation_id=conversation-beta-lead",
  );

  await waitForRevision(page, 9);
  await page.locator(".chat-thread-title").getByText("Bea").waitFor({ state: "visible" });
  assert.equal(
    await page
      .locator('[data-chat-conversation-id="conversation-beta-lead"]')
      .getAttribute("aria-pressed"),
    "true",
  );
  assert.ok(
    chatRequests.some(
      (request) =>
        request.method === "GET" &&
        request.pathname === "/api/addons/team-addon/views/chat" &&
        String(request.search || "").includes("selected_conversation_id=conversation-beta-lead"),
    ),
    "expected the chat deep link to select its conversation in the initial request",
  );
});

test("team add-on Chat preserves a reader position across a state-stream refresh", async (t) => {
  const { port, state, chatRequests, emitStateSnapshot, stateStreamCount } = await createTeamAddonServer(t, {
    initialState: seededLongChatState(),
  });
  const page = await openBrowserPage(t, port, { viewport: { width: 1280, height: 720 } });

  await waitForRevision(page, 9);
  await page.locator("#team-view-chat-tab").click();
  const messageList = page.locator(".chat-message-list");
  await messageList.getByText("Message 119").waitFor({ state: "visible" });
  await waitForCondition(
    () => stateStreamCount() > 0,
    "expected Team state stream to be open before forcing a refresh",
  );

  const before = await messageList.evaluate((element) => {
    const maxTop = Math.max(0, element.scrollHeight - element.clientHeight);
    element.scrollTop = Math.max(128, Math.floor(maxTop / 2));
    element.dispatchEvent(new Event("scroll"));
    return { maxTop, scrollTop: element.scrollTop };
  });
  assert.ok(before.maxTop > before.scrollTop, "expected a non-bottom reader position");

  const requestsBeforeRefresh = chatRequests.length;
  state.revision += 1;
  assert.ok(
    emitStateSnapshot({ response_scope: "revision", revision: state.revision }) > 0,
    "expected the test state revision to reach the Team client",
  );
  await waitForCondition(
    () => chatRequests.length > requestsBeforeRefresh,
    "expected the state-stream revision to refresh the active Chat view",
  );
  await waitForRevision(page, state.revision);
  await page.evaluate(
    () => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))),
  );

  const after = await messageList.evaluate((element) => ({
    maxTop: Math.max(0, element.scrollHeight - element.clientHeight),
    scrollTop: element.scrollTop,
  }));
  assert.ok(
    Math.abs(after.scrollTop - before.scrollTop) <= 4,
    `expected state refresh to preserve reading position, got ${after.scrollTop}, expected ${before.scrollTop}`,
  );
  assert.ok(after.maxTop - after.scrollTop > 96, "expected reader to remain away from the newest message");
});

test("team add-on Chat send keeps the selected message list anchored during send refreshes", async (t) => {
  let releaseMessageResponse = null;
  let messageRequestStarted = null;
  const messageStarted = new Promise((resolve) => {
    messageRequestStarted = resolve;
  });
  const messageRelease = new Promise((resolve) => {
    releaseMessageResponse = resolve;
  });
  const { port } = await createTeamAddonServer(t, {
    initialState: seededLongChatState(),
    chatMessageBeforeResponse: async () => {
      messageRequestStarted?.();
      await messageRelease;
    },
  });
  const page = await openBrowserPage(t, port, { viewport: { width: 1280, height: 720 } });

  await waitForRevision(page, 9);
  await page.locator("#team-view-chat-tab").click();
  const messageList = page.locator(".chat-message-list");
  await messageList.getByText("Message 119").waitFor({ state: "visible" });
  await waitForCondition(async () => {
    const metrics = await messageList.evaluate((element) => ({
      bottomGap: element.scrollHeight - element.clientHeight - element.scrollTop,
      clientHeight: element.clientHeight,
      scrollHeight: element.scrollHeight,
    }));
    return metrics.scrollHeight > metrics.clientHeight && metrics.bottomGap <= 4;
  }, "expected chat to start anchored at the newest message");

  await page.evaluate(() => {
    const list = document.querySelector(".chat-message-list");
    const root = document.querySelector("[data-chat-thread-view]");
    if (!(list instanceof HTMLElement) || !(root instanceof HTMLElement)) {
      throw new Error("chat message list not found");
    }
    list.scrollTop = list.scrollHeight;
    window.__teamChatListNode = list;
    window.__teamChatScrollSamples = [];
    window.__teamChatObserver = new MutationObserver(() => {
      const current = document.querySelector(".chat-message-list");
      window.__teamChatScrollSamples.push({
        sameNode: current === window.__teamChatListNode,
        scrollTop: current instanceof HTMLElement ? current.scrollTop : null,
        bottomGap: current instanceof HTMLElement
          ? current.scrollHeight - current.clientHeight - current.scrollTop
          : null,
      });
    });
    window.__teamChatObserver.observe(root, { childList: true, subtree: true });
  });

  await page.locator('.chat-composer textarea[name="body"]').fill("Please continue from the latest context.");
  await page.getByRole("button", { name: "Send" }).click();
  await messageStarted;

  let duringSendMetrics = null;
  await waitForCondition(async () => {
    duringSendMetrics = await page.evaluate(() => {
      const current = document.querySelector(".chat-message-list");
      if (!(current instanceof HTMLElement)) {
        return null;
      }
      return {
        sameNode: current === window.__teamChatListNode,
        bottomGap: current.scrollHeight - current.clientHeight - current.scrollTop,
        scrollTop: current.scrollTop,
      };
    });
    return duringSendMetrics?.sameNode === true && duringSendMetrics.bottomGap <= 4;
  }, "expected delayed send render to keep the selected chat list anchored");

  releaseMessageResponse?.();
  await page.locator(".chat-message").getByText("Please continue from the latest context.").waitFor({
    state: "visible",
  });
  let afterSendMetrics = null;
  await waitForCondition(async () => {
    afterSendMetrics = await page.evaluate(() => {
      const current = document.querySelector(".chat-message-list");
      if (!(current instanceof HTMLElement)) {
        return null;
      }
      return {
        sameNode: current === window.__teamChatListNode,
        bottomGap: current.scrollHeight - current.clientHeight - current.scrollTop,
        scrollTop: current.scrollTop,
      };
    });
    return afterSendMetrics?.sameNode === true && afterSendMetrics.bottomGap <= 4;
  }, "expected chat send response to keep the selected chat list anchored");

  const samples = await page.evaluate(() => {
    window.__teamChatObserver?.disconnect();
    return window.__teamChatScrollSamples || [];
  });
  assert.ok(samples.length > 0, "expected chat send to trigger DOM updates");
  assert.equal(
    samples.some((sample) => sample.sameNode === false),
    false,
    `expected selected chat message list node to be reused: ${JSON.stringify(samples)}`,
  );
});

test("team add-on Chat sends on Enter and keeps Shift+Enter as a newline", async (t) => {
  const { port, requests } = await createTeamAddonServer(t, {
    initialState: seededLongChatState(),
  });
  const page = await openBrowserPage(t, port, { viewport: { width: 1280, height: 720 } });

  await waitForRevision(page, 9);
  await page.locator("#team-view-chat-tab").click();
  const composer = page.locator('.chat-composer textarea[name="body"]');
  await composer.waitFor({ state: "visible" });
  await composer.fill("First line");
  await composer.press("Shift+Enter");
  await composer.type("Second line");

  assert.equal(await composer.inputValue(), "First line\nSecond line");
  assert.equal(
    requests.some(
      (request) =>
        request.method === "POST" &&
        request.pathname === "/api/addons/team-addon/conversations/conversation-alpha-lead/messages",
    ),
    false,
  );

  await composer.press("Enter");
  await page.locator(".chat-message").getByText("First line").waitFor({ state: "visible" });
  await waitForCondition(
    () =>
      requests.some(
        (request) =>
          request.method === "POST" &&
          request.pathname === "/api/addons/team-addon/conversations/conversation-alpha-lead/messages" &&
          request.payload?.body === "First line\nSecond line",
      ),
    "expected plain Enter to submit the complete chat message",
  );
});

test("team add-on Chat sends selected files as multipart attachments", async (t) => {
  const initialState = seededLongChatState(4);
  const { port } = await createTeamAddonServer(t, { initialState });
  const page = await openBrowserPage(t, port, { viewport: { width: 1280, height: 720 } });
  let resolveUpload = null;
  const uploadSeen = new Promise((resolve) => {
    resolveUpload = resolve;
  });

  await page.route(
    "**/api/addons/team-addon/conversations/conversation-alpha-lead/messages",
    async (route) => {
      if (route.request().method() !== "POST") {
        await route.continue();
        return;
      }
      const contentType = route.request().headers()["content-type"] || "";
      const rawBody = route.request().postDataBuffer()?.toString("utf8") || "";
      const message = {
        id: "chatmsg-attachment",
        conversation_id: "conversation-alpha-lead",
        sender: "operator",
        message_type: "text",
        body: "Please review the attachment.",
        status: "queued",
        dispatch_id: "team-chat:chatmsg-attachment",
        voice_session_id: null,
        source_message_id: null,
        error: null,
        attachments: [
          {
            id: "chatatt-attachment",
            filename: "notes.txt",
            mime_type: "text/plain",
            byte_count: 15,
            sha256: "0".repeat(64),
            download_url:
              "/api/addons/team-addon/conversations/conversation-alpha-lead/attachments/chatatt-attachment",
          },
        ],
        metadata: {},
        created_at: "2026-06-09T03:00:00Z",
        updated_at: "2026-06-09T03:00:00Z",
      };
      initialState.revision += 1;
      initialState.conversation_messages[message.id] = message;
      const conversation = initialState.conversations[message.conversation_id];
      conversation.last_message_preview = message.body;
      conversation.updated_at = message.updated_at;
      conversation.last_interaction_at = message.updated_at;
      await route.fulfill({
        status: 202,
        contentType: "application/json",
        body: JSON.stringify({
          revision: initialState.revision,
          conversation: chatConversationPayload(initialState, conversation, {
            includeMessages: true,
            limit: 50,
          }),
          message,
          dispatch: { success: false, queued: true },
          published_event_ids: [],
        }),
      });
      resolveUpload?.({ contentType, rawBody });
    },
  );

  await waitForRevision(page, 9);
  await page.locator("#team-view-chat-tab").click();
  const fileInput = page.locator('[data-chat-attachment-input="conversation-alpha-lead"]');
  await fileInput.setInputFiles({
    name: "notes.txt",
    mimeType: "text/plain",
    buffer: Buffer.from("attachment body"),
  });
  await page.getByText("notes.txt · 15 B").waitFor({ state: "visible" });
  await page.locator('.chat-composer textarea[name="body"]').fill("Please review the attachment.");
  await page.getByRole("button", { name: "Send" }).click();

  const upload = await uploadSeen;
  assert.match(upload.contentType, /^multipart\/form-data; boundary=/);
  assert.match(upload.rawBody, /name="body"[\s\S]*Please review the attachment\./);
  assert.match(upload.rawBody, /name="files"; filename="notes.txt"/);
  assert.match(upload.rawBody, /attachment body/);
  await page.getByRole("link", { name: /notes\.txt/ }).waitFor({ state: "visible" });
});

test("team add-on Chat remains usable while privileged operations are locked", async (t) => {
  const { port, requests } = await createTeamAddonServer(t, {
    initialState: seededLongChatState(),
  });
  const page = await openBrowserPage(
    t,
    port,
    { viewport: { width: 1280, height: 720 } },
    "/addons/team-addon/?privileged_unlocked=false",
  );

  await waitForRevision(page, 9);
  await page.locator("#team-view-chat-tab").click();
  await page.locator(".chat-thread-title").getByText("Ada").waitFor({ state: "visible" });
  await page.locator(".chat-composer .chat-mutation-mode.is-reply-only").getByText("Reply only").waitFor({ state: "visible" });

  const newChatButton = page.getByRole("button", { name: "New chat" });
  assert.equal(await newChatButton.isDisabled(), false);
  assert.equal(await newChatButton.getAttribute("title"), null);
  await newChatButton.click();
  const pickerAgent = page.locator('[data-chat-agent-id="scroll-agent-01"]');
  await pickerAgent.waitFor({ state: "visible" });
  assert.equal(await pickerAgent.isDisabled(), false);
  assert.equal(await pickerAgent.getAttribute("title"), null);

  const body = "Please summarize this while the Team lock is enabled.";
  await page.locator('.chat-composer textarea[name="body"]').fill(body);
  const sendButton = page.getByRole("button", { name: "Send" });
  assert.equal(await sendButton.isDisabled(), false);
  assert.equal(await sendButton.getAttribute("title"), null);
  await sendButton.click();

  await page.locator(".chat-message").getByText(body).waitFor({ state: "visible" });
  await waitForCondition(
    () =>
      requests.some(
        (request) =>
          request.method === "POST" &&
          request.pathname === "/api/addons/team-addon/conversations/conversation-alpha-lead/messages" &&
          request.payload?.body === body,
      ),
    "expected locked-mode chat send to post through the built-in chat client",
  );
  const sendRequest = requests.find(
    (request) =>
      request.method === "POST" &&
      request.pathname === "/api/addons/team-addon/conversations/conversation-alpha-lead/messages" &&
      request.payload?.body === body,
  );
  assert.equal(sendRequest?.payload?.expected_revision, undefined);
});

test("team add-on Chat shows mutation-enabled server authority", async (t) => {
  const initialState = seededLongChatState();
  initialState.conversations["conversation-alpha-lead"].mutation_mode = "project_scoped";
  const { port } = await createTeamAddonServer(t, { initialState });
  const page = await openBrowserPage(t, port, { viewport: { width: 1280, height: 720 } });

  await waitForRevision(page, 9);
  await page.locator("#team-view-chat-tab").click();
  await page.locator(".chat-composer .chat-mutation-mode.is-enabled")
    .getByText("Mutation enabled")
    .waitFor({ state: "visible" });
});

test("team add-on Chat notification sound is gated by focus and sender", async (t) => {
  const { port, emitChatSnapshots, requests, state } = await createTeamAddonServer(t, {
    initialState: seededLongChatState(),
  });
  const page = await openBrowserPage(t, port, { viewport: { width: 1280, height: 720 } });

  await waitForRevision(page, 9);
  await page.locator("#team-view-chat-tab").click();
  await page.locator(".chat-thread-title").getByText("Ada").waitFor({ state: "visible" });
  await waitForCondition(
    () =>
      requests.some(
        (request) =>
          request.method === "GET" &&
          request.pathname === "/api/addons/team-addon/conversations/conversation-alpha-lead/stream",
      ),
    "expected selected chat stream to open",
  );

  await page.evaluate(() => {
    class FakeAudioContext {
      constructor() {
        this.currentTime = 1;
        this.destination = {};
        this.state = "running";
      }

      createOscillator() {
        return {
          frequency: {
            setValueAtTime() {},
            exponentialRampToValueAtTime() {},
          },
          connect() {},
          disconnect() {},
          start(time) {
            window.__teamChatSoundStarts = [...(window.__teamChatSoundStarts || []), time];
          },
          stop() {},
          set onended(_handler) {},
        };
      }

      createGain() {
        return {
          gain: {
            setValueAtTime() {},
            exponentialRampToValueAtTime() {},
          },
          connect() {},
          disconnect() {},
        };
      }

      resume() {
        return Promise.resolve();
      }
    }

    Object.defineProperty(window, "AudioContext", { configurable: true, value: FakeAudioContext });
    Object.defineProperty(document, "hasFocus", { configurable: true, value: () => false });
  });

  const conversation = state.conversations["conversation-alpha-lead"];
  state.revision += 1;
  state.conversation_messages["sound-operator"] = {
    id: "sound-operator",
    conversation_id: conversation.id,
    sender: "operator",
    message_type: "text",
    body: "Operator message should not chime.",
    status: "sent",
    dispatch_id: null,
    voice_session_id: null,
    source_message_id: null,
    error: null,
    metadata: {},
    created_at: "2026-06-09T03:00:00Z",
    updated_at: "2026-06-09T03:00:00Z",
  };
  conversation.last_message_preview = "Operator message should not chime.";
  conversation.updated_at = "2026-06-09T03:00:00Z";
  conversation.last_interaction_at = "2026-06-09T03:00:00Z";
  emitChatSnapshots(conversation.id);
  await page.locator(".chat-message").getByText("Operator message should not chime.").waitFor({
    state: "visible",
  });
  assert.equal(await page.evaluate(() => (window.__teamChatSoundStarts || []).length), 0);

  state.revision += 1;
  state.conversation_messages["sound-agent"] = {
    id: "sound-agent",
    conversation_id: conversation.id,
    sender: "agent",
    message_type: "text",
    body: "Agent message should chime.",
    status: "sent",
    dispatch_id: null,
    voice_session_id: null,
    source_message_id: null,
    error: null,
    metadata: {},
    created_at: "2026-06-09T03:01:00Z",
    updated_at: "2026-06-09T03:01:00Z",
  };
  conversation.last_message_preview = "Agent message should chime.";
  conversation.updated_at = "2026-06-09T03:01:00Z";
  conversation.last_interaction_at = "2026-06-09T03:01:00Z";
  emitChatSnapshots(conversation.id);

  await page.locator(".chat-message").getByText("Agent message should chime.").waitFor({
    state: "visible",
  });
  await waitForCondition(
    async () => (await page.evaluate(() => (window.__teamChatSoundStarts || []).length)) === 1,
    "expected an unfocused incoming agent chat message to play one notification sound",
  );
});

test("team add-on Chat refresh preserves a conversation selected while the list response is in flight", async (t) => {
  let holdConversationList = false;
  let releaseRefreshResponse = null;
  let refreshRequestStarted = null;
  const refreshStarted = new Promise((resolve) => {
    refreshRequestStarted = resolve;
  });
  const refreshRelease = new Promise((resolve) => {
    releaseRefreshResponse = resolve;
  });
  const { port } = await createTeamAddonServer(t, {
    initialState: seededTwoConversationChatState(),
    conversationsBeforeResponse: async () => {
      if (!holdConversationList) {
        return;
      }
      holdConversationList = false;
      refreshRequestStarted?.();
      await refreshRelease;
    },
  });
  const page = await openBrowserPage(t, port, { viewport: { width: 1280, height: 720 } });

  await waitForRevision(page, 9);
  await page.locator("#team-view-chat-tab").click();
  await page.locator(".chat-thread-title").getByText("Ada").waitFor({ state: "visible" });

  holdConversationList = true;
  await page.getByRole("button", { name: "Refresh" }).click();
  await refreshStarted;
  await page.locator('[data-chat-conversation-id="conversation-beta-lead"]').click();
  await page.locator(".chat-thread-title").getByText("Bea").waitFor({ state: "visible" });

  releaseRefreshResponse?.();
  await page.waitForFunction(() => {
    const selected = document.querySelector(".chat-conversation-row.is-selected");
    return selected?.dataset?.chatConversationId === "conversation-beta-lead";
  });
  await page.locator(".chat-thread-title").getByText("Bea").waitFor({ state: "visible" });
  assert.equal(
    await page.locator(".chat-thread-title").getByText("Ada").count(),
    0,
    "expected delayed conversation-list response not to restore the stale selection",
  );
});

test("team add-on Chat preserves unsent composer drafts per conversation and clears a sent draft", async (t) => {
  const { port, requests } = await createTeamAddonServer(t, {
    initialState: seededTwoConversationChatState(),
  });
  const page = await openBrowserPage(t, port, {
    viewport: { width: 1280, height: 720 },
  });

  await waitForRevision(page, 9);
  await page.locator("#team-view-chat-tab").click();
  await page.locator(".chat-thread-title").getByText("Ada").waitFor({
    state: "visible",
  });
  const composer = page.locator('.chat-composer textarea[name="body"]');
  await composer.fill("Alpha conversation draft");
  await composer.evaluate((field) => field.setSelectionRange(6, 18));

  await page
    .locator('[data-chat-conversation-id="conversation-beta-lead"]')
    .click();
  await page.locator(".chat-thread-title").getByText("Bea").waitFor({
    state: "visible",
  });
  await composer.fill("Beta conversation draft");

  await page
    .locator('[data-chat-conversation-id="conversation-alpha-lead"]')
    .click();
  await page.locator(".chat-thread-title").getByText("Ada").waitFor({
    state: "visible",
  });
  assert.equal(await composer.inputValue(), "Alpha conversation draft");
  assert.deepEqual(
    await composer.evaluate((field) => ({
      selectionStart: field.selectionStart,
      selectionEnd: field.selectionEnd,
    })),
    { selectionStart: 6, selectionEnd: 18 },
  );

  await page
    .locator('[data-chat-conversation-id="conversation-beta-lead"]')
    .click();
  await page.locator(".chat-thread-title").getByText("Bea").waitFor({
    state: "visible",
  });
  assert.equal(await composer.inputValue(), "Beta conversation draft");
  await page.locator(".chat-composer button[type='submit']").click();
  await waitForCondition(
    () =>
      requests.some(
        (request) =>
          request.method === "POST"
          && request.pathname
            === "/api/addons/team-addon/conversations/conversation-beta-lead/messages"
          && request.payload?.body === "Beta conversation draft",
      ),
    "expected the beta chat draft to be sent",
  );
  await page.waitForFunction(() => {
    const field = document.querySelector('.chat-composer textarea[name="body"]');
    return field instanceof HTMLTextAreaElement && field.value === "";
  });

  await page
    .locator('[data-chat-conversation-id="conversation-alpha-lead"]')
    .click();
  assert.equal(await composer.inputValue(), "Alpha conversation draft");
  await page
    .locator('[data-chat-conversation-id="conversation-beta-lead"]')
    .click();
  assert.equal(
    await composer.inputValue(),
    "",
    "a successfully sent draft must not return after switching conversations",
  );
});

function seededScrollablePeopleTooltipState() {
  const state = seededGitState();
  for (let index = 2; index <= 18; index += 1) {
    state.inbox[`inbox-scroll-${index}`] = {
      id: `inbox-scroll-${index}`,
      recipient_agent_id: "alpha-lead",
      item_type: "status_request",
      sender_agent_id: "alpha-manager",
      task_id: "task-waiting",
      payload: {
        question: `Follow-up ${index}: confirm the release blocker status, current owner, next decision point, and whether the operator needs to intervene before the next wake cycle.`,
      },
      state: "pending",
    };
  }
  return state;
}

function seededOrgChartState() {
  return {
    revision: 8,
    teams: {
      team: {
        id: "team",
        name: "Team",
        manager_agent_id: "manager",
        operator_communication_target_id: "ops-channel",
        project_ids: ["project"],
        archived: false,
      },
    },
    projects: {
      project: {
        id: "project",
        team_id: "team",
        name: "Project",
        lead_agent_id: "lead",
        goals: [],
        git: {
          managed_repo_id: "mr_0123456789abcdef0123",
          base_branch: "main",
        },
        status: "active",
      },
    },
    roles: {
      team__manager: {
        id: "team__manager",
        team_id: "team",
        project_id: null,
        name: "Manager",
        description: "",
        permissions: ["create_root_task"],
        ephemeral_policy: { max_concurrent: 2 },
        archived: false,
      },
      "project__project-lead": {
        id: "project__project-lead",
        team_id: "team",
        project_id: "project",
        name: "Project Lead",
        description: "",
        permissions: ["create_root_task"],
        ephemeral_policy: { max_concurrent: 1 },
        archived: false,
      },
      project__backend: {
        id: "project__backend",
        team_id: "team",
        project_id: "project",
        name: "Backend",
        description: "",
        permissions: [],
        ephemeral_policy: { max_concurrent: 3 },
        archived: false,
      },
      project__worker: {
        id: "project__worker",
        team_id: "team",
        project_id: "project",
        name: "Worker",
        description: "",
        permissions: [],
        ephemeral_policy: { max_concurrent: 0 },
        archived: false,
      },
    },
    agents: {
      manager: {
        id: "manager",
        team_id: "team",
        project_id: null,
        display_name: "Ops Manager",
        role_id: "team__manager",
        session_id: "session-manager",
        reports_to_agent_id: null,
        subordinate_agent_ids: ["lead"],
        ephemeral_policy: { max_concurrent: 2 },
        status: "active",
      },
      lead: {
        id: "lead",
        team_id: "team",
        project_id: "project",
        role_id: "project__project-lead",
        session_id: "session-lead",
        reports_to_agent_id: "manager",
        subordinate_agent_ids: ["backend"],
        ephemeral_policy: { max_concurrent: 1 },
        status: "paused",
      },
      backend: {
        id: "backend",
        team_id: "team",
        project_id: "project",
        display_name: "Backend Worker",
        role_id: "project__backend",
        session_id: "session-backend",
        reports_to_agent_id: "lead",
        subordinate_agent_ids: ["worker"],
        ephemeral_policy: { max_concurrent: 3 },
        status: "active",
      },
      worker: {
        id: "worker",
        team_id: "team",
        project_id: "project",
        role_id: "project__worker",
        session_id: "session-worker",
        reports_to_agent_id: "backend",
        subordinate_agent_ids: [],
        ephemeral_policy: { max_concurrent: 0 },
        status: "active",
      },
    },
    governance_groups: {
      portfolio: {
        id: "portfolio",
        name: "Portfolio Oversight",
        manager_agent_id: "manager",
        member_team_ids: ["team"],
        goals: [],
        max_cost_usd: null,
        archived: false,
      },
    },
    governance_agenda: {
      "portfolio-agenda": {
        id: "portfolio-agenda",
        governance_group_id: "portfolio",
        item_type: "scheduled_checkin",
        priority: "normal",
        team_id: "team",
        project_id: null,
        agent_id: null,
        title: "Weekly team check-in",
        summary: "Review team progress and blockers.",
        payload: {},
        state: "pending",
        created_at: "2026-06-11T07:00:00Z",
        updated_at: "2026-06-11T07:00:00Z",
      },
    },
    agent_runtimes: {
      backend: {
        active_dispatch_id: "dispatch-42",
        wake_reason: "poll",
        next_wake_at: "2026-06-11T09:00:00Z",
        wake_leased_until: "2026-06-11T09:05:00Z",
        last_wake_at: "2026-06-11T08:55:00Z",
      },
    },
    tasks: {
      task_open: {
        id: "task_open",
        owner_agent_id: "backend",
        project_id: "project",
        priority: 5,
        state: "in_progress",
        title: "Build API",
        description: "Implement backend service.",
        created_by_agent_id: "lead",
        deliverable_ids: [],
        created_at: "2026-06-11T08:00:00Z",
        updated_at: "2026-06-11T08:15:00Z",
      },
      task_blocked: {
        id: "task_blocked",
        owner_agent_id: "backend",
        project_id: "project",
        priority: 4,
        state: "blocked",
        title: "Resolve dependency",
        description: "Waiting on upstream work.",
        created_by_agent_id: "manager",
        deliverable_ids: [],
        created_at: "2026-06-11T08:10:00Z",
        updated_at: "2026-06-11T08:20:00Z",
      },
      task_waiting: {
        id: "task_waiting",
        owner_agent_id: "worker",
        project_id: "project",
        priority: 1,
        state: "waiting_for_review",
        title: "Review docs",
        description: "Needs approval.",
        created_by_agent_id: "backend",
        deliverable_ids: [],
        created_at: "2026-06-11T08:30:00Z",
        updated_at: "2026-06-11T08:45:00Z",
      },
    },
    inbox: {
      "inbox-1": {
        id: "inbox-1",
        recipient_agent_id: "backend",
        item_type: "status_request",
        sender_agent_id: "lead",
        task_id: "task_open",
        payload: { question: "Status?" },
        state: "pending",
        created_at: "2026-06-09T00:00:00Z",
        updated_at: "2026-06-09T02:00:00Z",
      },
    },
    action_records: {
      action: {
        id: "action",
        agent_id: "lead",
        dispatch_id: "dispatch-action",
        action: { type: "deploy", summary: "Deploy failed" },
        success: false,
        error: "boom",
        created_at: "2026-06-09T03:00:00Z",
      },
    },
    ephemeral_assignments: {
      ephemeral: {
        id: "ephemeral",
        parent_agent_id: "lead",
        project_id: "project",
        task_id: "task",
        session_id: "session-ephemeral",
        title: "Ephemeral draft",
        prompt: "Draft an implementation plan.",
        state: "running",
        created_at: "2026-06-09T04:00:00Z",
        updated_at: "2026-06-09T05:00:00Z",
      },
    },
    ephemeral_assignments: {
      assignment_1: {
        id: "assignment_1",
        parent_agent_id: "backend",
        state: "running",
      },
    },
  };
}

function seededMalformedHierarchyState() {
  return {
    revision: 3,
    teams: {
      broken_team: {
        id: "broken-team",
        name: "Broken Team",
        manager_agent_id: "ghost-manager",
        project_ids: [],
        archived: false,
      },
    },
    roles: {
      "broken-team__manager": {
        id: "broken-team__manager",
        team_id: "broken-team",
        project_id: null,
        name: "Manager",
        description: "",
        permissions: ["create_root_task"],
        ephemeral_policy: { max_concurrent: 0 },
        archived: false,
      },
      "broken-team__worker": {
        id: "broken-team__worker",
        team_id: "broken-team",
        project_id: null,
        name: "Worker",
        description: "",
        permissions: [],
        ephemeral_policy: { max_concurrent: 0 },
        archived: false,
      },
    },
    agents: {
      alpha: {
        id: "alpha",
        team_id: "broken-team",
        project_id: null,
        role_id: "broken-team__manager",
        session_id: "session-alpha",
        reports_to_agent_id: null,
        subordinate_agent_ids: ["beta"],
        ephemeral_policy: { max_concurrent: 0 },
        status: "active",
      },
      beta: {
        id: "beta",
        team_id: "broken-team",
        project_id: null,
        role_id: "broken-team__worker",
        session_id: "session-beta",
        reports_to_agent_id: "alpha",
        subordinate_agent_ids: ["alpha", "missing-subordinate"],
        ephemeral_policy: { max_concurrent: 0 },
        status: "active",
      },
    },
  };
}

test("team add-on Mail reads historical agent correspondence", async (t) => {
  const initialState = seededGitState();
  initialState.agents["alpha-manager"].display_name = "Ada Manager";
  initialState.agents["alpha-lead"].display_name = "Grace Lead";
  initialState.inbox["inbox-history"] = {
    id: "inbox-history",
    recipient_agent_id: "alpha-lead",
    sender_agent_id: "alpha-manager",
    task_id: "task-waiting",
    item_type: "progress_report",
    response_requested: false,
    priority: "low",
    state: "handled",
    payload: {
      summary: "Quiet deployment update",
      message: "The deployment is ready for a private follow-up.",
    },
    created_at: "2026-06-10T09:00:00Z",
    updated_at: "2026-06-10T10:00:00Z",
  };
  const { port, requests } = await createTeamAddonServer(t, { initialState });
  const page = await openBrowserPage(t, port);

  await page.getByRole("tab", { name: "Mail" }).click();
  const mailPage = page.locator("#team-mail-page");
  await mailPage.waitFor({ state: "visible" });
  await mailPage.locator('[data-mail-item-id="inbox-history"]').waitFor({ state: "visible" });
  await mailPage.getByRole("heading", { name: "Quiet deployment update" }).waitFor({
    state: "visible",
  });
  await mailPage.locator(".mail-reader-body").getByText("The deployment is ready for a private follow-up.").waitFor({
    state: "visible",
  });
  await mailPage.locator(".mail-reader-addresses strong").getByText("Ada Manager · Manager").waitFor({ state: "visible" });
  await mailPage.locator(".mail-reader-addresses span").getByText("to Grace Lead · Project Lead").waitFor({ state: "visible" });

  await mailPage.locator('[data-mail-state="pending"]').click();
  await mailPage.locator('[data-mail-item-id="inbox-1"]').waitFor({ state: "visible" });
  await mailPage.locator('[data-mail-item-id="inbox-history"]').waitFor({ state: "detached" });
  await mailPage.getByRole("heading", { name: "What is the status?" }).waitFor({ state: "visible" });

  await mailPage.locator('[data-mail-state="handled"]').click();
  await mailPage.locator('[data-mail-item-id="inbox-history"]').waitFor({ state: "visible" });
  await mailPage.locator("#mail-agent-filter").selectOption("alpha-manager");
  await mailPage.locator("#mail-direction-filter").selectOption("sent");
  await mailPage.getByText("To: Grace Lead").waitFor({ state: "visible" });
  await mailPage.locator("#mail-search-input").fill("private follow-up");
  await mailPage.getByRole("button", { name: "Search" }).click();
  await mailPage.locator('[data-mail-item-id="inbox-history"]').waitFor({ state: "visible" });
  await mailPage.locator(".mail-reader-envelope summary").click();
  await mailPage.locator(".mail-envelope-grid").getByText("inbox-history").waitFor({
    state: "visible",
  });

  assert.ok(
    requests.some(
      (request) =>
        request.method === "GET" &&
        request.pathname === "/api/addons/team-addon/inbox" &&
        request.search.includes("direction=sent") &&
        request.search.includes("q=private+follow-up"),
    ),
    "expected Mail filters to be sent to the history endpoint",
  );
});

test("team add-on Mail creates a manager-routed operator directive", async (t) => {
  const { port, requests, state } = await createTeamAddonServer(t, {
    initialState: seededGitState(),
  });
  const page = await openBrowserPage(
    t,
    port,
    {
      storageState: {
        cookies: [],
        origins: [
          {
            origin: `http://127.0.0.1:${port}`,
            localStorage: [{ name: teamViewStorageKey, value: "mail" }],
          },
        ],
      },
    },
    "/addons/team-addon/?privileged_unlocked=true",
  );

  const mailPage = page.locator("#team-mail-page");
  await mailPage.waitFor({ state: "visible" });
  await page.locator('#team-view-mail-tab[aria-selected="true"]').waitFor({ state: "visible" });
  await mailPage.locator("#mail-agent-filter").selectOption("alpha-lead");
  await mailPage.locator("#mail-direction-filter").selectOption("sent");
  await mailPage.locator("#mail-search-input").fill("unrelated historical message");
  await mailPage.getByRole("button", { name: "Search" }).click();
  await mailPage.getByRole("button", { name: "Create directive" }).click();
  const form = mailPage.locator("[data-mail-directive-form]");
  await form.waitFor({ state: "visible" });
  await form.locator('[data-mail-directive-team]').selectOption("team-alpha");
  await form.getByText("Will be delivered to alpha-manager (alpha-manager), this Team’s manager.").waitFor({
    state: "visible",
  });
  await form.locator('[data-mail-directive-project] option[value="alpha-project"]').waitFor({
    state: "attached",
  });
  await form.locator('[data-mail-directive-project]').selectOption("alpha-project");
  await form.locator('[data-mail-directive-title]').fill("Check telemetry retry safety");
  await form.locator('[data-mail-directive-message]').fill(
    "Assess retry safety and report the constraints before changing production behavior.",
  );
  await form.getByRole("button", { name: "Create directive" }).click();

  await mailPage.locator('[data-mail-item-id="operator-directive-5"]').waitFor({ state: "visible" });
  await mailPage.getByRole("heading", { name: "Check telemetry retry safety" }).waitFor({
    state: "visible",
  });
  assert.equal(state.inbox["operator-directive-5"].recipient_agent_id, "alpha-manager");
  assert.equal(state.inbox["operator-directive-5"].payload.project_id, "alpha-project");
  assert.equal(
    state.inbox["operator-directive-5"].payload.message,
    "Assess retry safety and report the constraints before changing production behavior.",
  );

  const createRequest = requests.find(
    (request) =>
      request.method === "POST" &&
      request.pathname === "/api/addons/team-addon/operator-directives",
  );
  assert.ok(createRequest, "expected an operator directive request");
  assert.deepEqual(createRequest.payload, {
    team_id: "team-alpha",
    project_id: "alpha-project",
    title: "Check telemetry retry safety",
    message: "Assess retry safety and report the constraints before changing production behavior.",
    source: "admin_ui_operator_directive",
    expected_revision: 4,
  });
  assert.equal("recipient_agent_id" in createRequest.payload, false);

  assert.equal(await mailPage.locator("#mail-agent-filter").inputValue(), "");
  assert.equal(await mailPage.locator("#mail-direction-filter").inputValue(), "");
  assert.equal(await mailPage.locator("#mail-search-input").inputValue(), "");
  const finalMailRequest = requests.filter(
    (request) => request.method === "GET" && request.pathname === "/api/addons/team-addon/inbox",
  ).at(-1);
  assert.ok(finalMailRequest, "expected Mail to reload after directive creation");
  const finalMailQuery = new URLSearchParams(finalMailRequest.search);
  assert.equal(finalMailQuery.get("team_id"), "team-alpha");
  assert.equal(finalMailQuery.get("item_type"), "operator_directive");
  assert.equal(finalMailQuery.get("state"), "pending");
  assert.equal(finalMailQuery.has("agent_id"), false);
  assert.equal(finalMailQuery.has("direction"), false);
  assert.equal(finalMailQuery.has("q"), false);

  await mailPage.locator("#mail-team-filter").selectOption("team-beta");
  await mailPage.locator("[data-mail-directive-open]").click();
  await form.waitFor({ state: "visible" });
  assert.equal(await form.locator("[data-mail-directive-team]").inputValue(), "team-beta");
  assert.equal(await form.locator("[data-mail-directive-project]").inputValue(), "");
  await form.locator("[data-mail-directive-project]").selectOption("beta-project");
  await form.getByRole("button", { name: "Cancel", exact: true }).click();
  await form.waitFor({ state: "hidden" });

  await mailPage.locator("#mail-team-filter").selectOption("team-alpha");
  await mailPage.locator("[data-mail-directive-open]").click();
  await form.waitFor({ state: "visible" });
  assert.equal(await form.locator("[data-mail-directive-team]").inputValue(), "team-alpha");
  assert.equal(await form.locator("[data-mail-directive-project]").inputValue(), "");
});

test("team add-on Mail renders body, error, and generic historical payloads", async (t) => {
  const initialState = seededGitState();
  initialState.inbox = {
    "body-report": {
      id: "body-report",
      recipient_agent_id: "alpha-lead",
      sender_agent_id: "alpha-manager",
      item_type: "progress_report",
      response_requested: false,
      priority: "low",
      state: "handled",
      payload: {
        summary: "Body-only historical report",
        body: "This report stored its substantive update in payload.body.",
      },
      created_at: "2026-06-12T09:00:00Z",
      updated_at: "2026-06-12T10:00:00Z",
    },
    "error-notice": {
      id: "error-notice",
      recipient_agent_id: "alpha-lead",
      sender_agent_id: null,
      item_type: "deliverable_notice",
      response_requested: true,
      priority: "high",
      state: "handled",
      payload: {
        title: "Release promotion failed",
        error: "fatal: Needed a single revision",
      },
      created_at: "2026-06-13T09:00:00Z",
      updated_at: "2026-06-13T10:00:00Z",
    },
    "generic-notice": {
      id: "generic-notice",
      recipient_agent_id: "alpha-lead",
      sender_agent_id: null,
      item_type: "escalation",
      response_requested: true,
      priority: "normal",
      state: "handled",
      payload: {
        title: "Structured diagnostic",
        diagnostic: { code: "stale_ref", retryable: true },
      },
      created_at: "2026-06-11T09:00:00Z",
      updated_at: "2026-06-11T10:00:00Z",
    },
  };
  const { port } = await createTeamAddonServer(t, { initialState });
  const page = await openBrowserPage(t, port);

  await page.getByRole("tab", { name: "Mail" }).click();
  const mailPage = page.locator("#team-mail-page");
  await mailPage.locator('[data-mail-item-id="error-notice"]').waitFor({ state: "visible" });
  await mailPage.locator(".mail-reader-body").getByText("fatal: Needed a single revision").waitFor({
    state: "visible",
  });

  await mailPage.locator('[data-mail-item-id="body-report"]').click();
  await mailPage.locator(".mail-reader-body").getByText("This report stored its substantive update in payload.body.").waitFor({
    state: "visible",
  });

  await mailPage.locator('[data-mail-item-id="generic-notice"]').click();
  const genericBody = await mailPage.locator(".mail-reader-body").innerText();
  assert.match(genericBody, /Diagnostic:/);
  assert.match(genericBody, /"code": "stale_ref"/);
});

test("team add-on Workspace deduplicates runtime-backed tasks without crowding attention", async (t) => {
  const initialState = seededGitState();
  initialState.tasks = {
    "task-blocked": {
      ...initialState.tasks["task-blocked"],
      title: "A task duplicate that should not render",
    },
  };
  initialState.deliverables = {};
  initialState.review_requests = {};
  initialState.inbox = {};
  initialState.governance_agenda = {};
  initialState.runtime_operations = {
    generated_at: "2026-07-27T12:00:00Z",
    agents: [],
    attention: [
      {
        id: "runtime-task-blocker",
        kind: "blocked_task",
        attention_group: "active_obligation",
        severity: "warning",
        title: "A runtime blocker for the task",
        team_id: "team-beta",
        project_id: "beta-project",
        agent_id: "beta-lead",
        task_id: "task-blocked",
        state: "blocked",
      },
      ...Array.from({ length: 5 }, (_, offset) => ({
        id: `runtime-signal-${offset + 1}`,
        kind: "runtime_signal",
        attention_group: "active_obligation",
        severity: "warning",
        title: `B runtime signal ${offset + 1}`,
        team_id: "team-alpha",
        project_id: "alpha-project",
        agent_id: "alpha-lead",
        state: "pending",
      })),
      {
        id: "runtime-final-signal",
        kind: "runtime_signal",
        attention_group: "active_obligation",
        severity: "warning",
        title: "Z final actionable signal",
        team_id: "team-alpha",
        project_id: "alpha-project",
        agent_id: "alpha-lead",
        state: "pending",
      },
    ],
  };

  const { port } = await createTeamAddonServer(t, { initialState });
  const page = await openBrowserPage(t, port);

  await waitForRevision(page, 4);
  await openWorkspace(page);
  const workspace = page.locator("#team-workspace-page");
  const attentionMetric = workspace.locator(".workspace-metric-card", {
    hasText: "Needs attention",
  });
  await attentionMetric.getByText("7", { exact: true }).waitFor({
    state: "visible",
  });
  await workspace.getByText("7 items need attention.", { exact: true }).waitFor({
    state: "visible",
  });

  const attentionList = workspace.locator(".workspace-attention-list");
  assert.equal(await attentionList.locator("li").count(), 7);
  assert.equal(
    await attentionList
      .getByText("A runtime blocker for the task", { exact: true })
      .count(),
    1,
  );
  assert.equal(
    await attentionList
      .getByText("A task duplicate that should not render", { exact: true })
      .count(),
    0,
  );
  await attentionList
    .getByText("Z final actionable signal", { exact: true })
    .waitFor({ state: "visible" });
});

test("team add-on Operations describes passive mail and failed recovery paths honestly", async (t) => {
  const initialState = seededGitState();
  initialState.agents["alpha-lead"].display_name = "Grace Lead";
  initialState.action_records = {
    "action-failed": {
      id: "action-failed",
      agent_id: "alpha-lead",
      dispatch_id: "dispatch-failed",
      action: { type: "deploy", summary: "Deploy candidate" },
      success: false,
      error: "Deployment rejected.",
      created_at: "2026-07-27T08:00:00Z",
    },
  };
  initialState.runtime_operations = {
    generated_at: "2026-07-27T12:00:00Z",
    agents: [],
    attention: [
      {
        id: "attention-passive-mail",
        kind: "pending_inbox_item",
        attention_group: "retained_history",
        attention_reason: "pending_without_response_obligation",
        reconciliation_required: false,
        severity: "info",
        title: "Deployment progress note",
        team_id: "team-alpha",
        project_id: "alpha-project",
        agent_id: "alpha-lead",
        inbox_item_id: "inbox-progress-note",
        state: "pending",
        detail: "The deployment completed its first verification pass.",
        occurred_at: "2026-07-27T11:00:00Z",
      },
      {
        id: "attention-action-failed",
        kind: "failed_action_record",
        attention_group: "failed_attempt",
        severity: "warning",
        title: "Deploy action failed",
        team_id: "team-alpha",
        project_id: "alpha-project",
        agent_id: "alpha-lead",
        action_record_id: "action-failed",
        state: "failed",
        error: "Deployment rejected.",
        occurred_at: "2026-07-27T10:00:00Z",
      },
      {
        id: "attention-promotion-failed",
        kind: "failed_git_promotion",
        attention_group: "failed_attempt",
        severity: "critical",
        title: "Candidate promotion failed",
        team_id: "team-alpha",
        project_id: "alpha-project",
        agent_id: "alpha-lead",
        promotion_id: "promotion-failed",
        state: "failed",
        error: "Candidate branch diverged.",
        occurred_at: "2026-07-27T09:00:00Z",
      },
      {
        id: "attention-assignment-failed",
        kind: "failed_ephemeral_assignment",
        attention_group: "failed_attempt",
        severity: "warning",
        title: "Ephemeral assignment failed",
        team_id: "team-alpha",
        project_id: "alpha-project",
        agent_id: "alpha-lead",
        assignment_id: "assignment-failed",
        state: "failed",
        error: "Worker exited before producing a result.",
        occurred_at: "2026-07-27T08:30:00Z",
      },
    ],
  };

  const { port } = await createTeamAddonServer(t, { initialState });
  const page = await openBrowserPage(t, port);

  await waitForRevision(page, 4);
  await page.getByRole("tab", { name: "Operations" }).click();
  const operations = page.locator("#team-operations-page");
  await operations.getByRole("button", { name: "Needs attention" }).click();

  const failedGroup = operations.locator('[data-attention-group="failed_attempt"]');
  await failedGroup.getByRole("heading", { name: "Failed attempts", exact: true }).waitFor({
    state: "visible",
  });
  await failedGroup.getByText("3 visible", { exact: true }).waitFor({
    state: "visible",
  });

  const failedAction = failedGroup.locator(
    '[data-attention-id="attention-action-failed"]',
  );
  await failedAction.locator(":scope > details > summary").click();
  const failedActionBody = await failedAction.locator(".compact-record-body").innerText();
  assert.match(failedActionBody, /explicitly dismissed/i);
  assert.equal(await failedAction.locator("[data-dismiss-action-id]").count(), 1);

  const failedPromotion = failedGroup.locator(
    '[data-attention-id="attention-promotion-failed"]',
  );
  await failedPromotion.locator(":scope > details > summary").click();
  const failedPromotionBody = await failedPromotion
    .locator(".compact-record-body")
    .innerText();
  assert.match(failedPromotionBody, /release recovery path/i);
  assert.doesNotMatch(failedPromotionBody, /\bdismiss(?:ed|al)?\b/i);
  assert.equal(await failedPromotion.locator("[data-dismiss-action-id]").count(), 0);

  const failedAssignment = failedGroup.locator(
    '[data-attention-id="attention-assignment-failed"]',
  );
  await failedAssignment.locator(":scope > details > summary").click();
  const failedAssignmentBody = await failedAssignment
    .locator(".compact-record-body")
    .innerText();
  assert.match(failedAssignmentBody, /owning ephemeral workflow/i);
  assert.doesNotMatch(failedAssignmentBody, /\bdismiss(?:ed|al)?\b/i);
  assert.equal(await failedAssignment.locator("[data-dismiss-action-id]").count(), 0);

  const historyGroup = operations.locator(".runtime-attention-history");
  await historyGroup
    .getByRole("button", { name: "Show retained history (1)" })
    .click();
  const passiveMail = historyGroup.locator(
    '[data-attention-id="attention-passive-mail"]',
  );
  assert.match(
    (await passiveMail.getAttribute("class")) || "",
    /runtime-attention-kind-passive-correspondence/,
  );
  await passiveMail
    .locator(".runtime-attention-kind-label", { hasText: "No response requested" })
    .waitFor({ state: "visible" });
  await passiveMail.locator(":scope > details > summary").click();
  const passiveBody = await passiveMail.locator(".compact-record-body").innerText();
  assert.match(passiveBody, /No response was requested/i);
  assert.match(passiveBody, /no response is required/i);
  assert.doesNotMatch(
    passiveBody,
    /Awaiting agent response|response .* outstanding|respond or/i,
  );
});

test("team add-on Operations separates actionable attention from durable history", async (t) => {
  const initialState = seededGitState();
  initialState.agents["alpha-lead"].display_name = "Grace Lead";
  initialState.agents["beta-lead"].display_name = "Beta Lead";
  initialState.runtime_operations.generated_at = "2026-07-27T12:00:00Z";

  const activeFillers = Array.from({ length: 20 }, (_, offset) => {
    const index = offset + 1;
    const suffix = String(index).padStart(2, "0");
    return {
      id: `attention-active-${suffix}`,
      kind: "team_blocker",
      attention_group: "active_obligation",
      severity: "warning",
      title: `Active obligation ${suffix}`,
      team_id: "team-alpha",
      project_id: "alpha-project",
      agent_id: "alpha-lead",
      state: "open",
      detail: `Current operational obligation ${suffix}.`,
      occurred_at: `2026-07-27T10:${suffix}:00Z`,
    };
  });
  initialState.runtime_operations.attention = [
    {
      id: "attention-active-blocker",
      kind: "team_blocker",
      attention_group: "active_obligation",
      severity: "critical",
      title: "Packaging approval is blocked",
      team_id: "team-alpha",
      project_id: "alpha-project",
      agent_id: "alpha-lead",
      task_id: "task-planned",
      blocker_id: "blocker-packaging-approval",
      state: "open",
      detail: "The packaging approval dependency is unresolved.",
      occurred_at: "2026-07-27T11:30:00Z",
    },
    {
      id: "attention-active-inbox",
      kind: "pending_inbox_item",
      attention_group: "active_obligation",
      severity: "warning",
      title: "Clarify the rollout checklist",
      team_id: null,
      project_id: null,
      links: {
        team_ids: ["team-beta", "team-alpha"],
        project_ids: ["beta-project", "alpha-project"],
      },
      agent_id: "beta-lead",
      task_id: "task-rejected",
      inbox_item_id: "inbox-rollout-checklist",
      state: "pending",
      detail: "The rollout checklist needs an agent response.",
      occurred_at: "2026-07-27T11:00:00Z",
      terminal_context: [
        { kind: "deliverable", id: "deliverable-accepted", state: "accepted" },
      ],
    },
    ...activeFillers,
    {
      id: "attention-promotion-failed",
      kind: "failed_git_promotion",
      attention_group: "failed_attempt",
      severity: "critical",
      title: "Release candidate promotion failed",
      team_id: "team-beta",
      project_id: "beta-project",
      agent_id: "beta-lead",
      task_id: "task-blocked",
      deliverable_id: "deliverable-blocked",
      promotion_id: "promotion-invalid-candidate",
      state: "failed",
      source_branch: "agents/beta-lead/release-candidate",
      target_branch: "releases/beta",
      commit_sha: "deadbeefbadc0de",
      error: "Candidate commit is not reachable from the approved branch.",
      detail: "Candidate commit is not reachable from the approved branch.",
      occurred_at: "2026-07-27T09:00:00Z",
    },
    {
      id: "attention-terminal-inbox",
      kind: "pending_inbox_item",
      attention_group: "retained_history",
      severity: "critical",
      title: "Accepted delivery still has pending correspondence",
      team_id: "team-alpha",
      project_id: "alpha-project",
      agent_id: "alpha-lead",
      task_id: "task-accepted",
      inbox_item_id: "inbox-terminal-follow-up",
      state: "pending",
      detail: "A response was never reconciled after the task was accepted.",
      occurred_at: "2026-07-20T12:00:00Z",
      terminal_context: [{ kind: "task", id: "task-accepted", state: "accepted" }],
      reconciliation_required: true,
    },
  ];

  const { port } = await createTeamAddonServer(t, { initialState });
  const page = await openBrowserPage(t, port, {
    permissions: ["clipboard-read", "clipboard-write"],
  });

  await waitForRevision(page, 4);
  await page.getByRole("tab", { name: "Operations" }).click();
  const operations = page.locator("#team-operations-page");
  await operations.getByRole("button", { name: "Needs attention" }).click();

  const counts = operations.locator("[data-runtime-attention-counts]");
  const countsText = await counts.innerText();
  assert.match(countsText, /22 active/);
  assert.match(countsText, /1 failed attempts?/);
  assert.match(countsText, /1 retained history/);
  assert.match(countsText, /2 terminal-linked/);
  assert.match(countsText, /1 need reconciliation/);

  const activeGroup = operations.locator('[data-attention-group="active_obligation"]');
  const failedGroup = operations.locator('[data-attention-group="failed_attempt"]');
  const historyGroup = operations.locator(".runtime-attention-history");
  await activeGroup.getByText("22 visible", { exact: true }).waitFor({ state: "visible" });
  await failedGroup.getByText("1 visible", { exact: true }).waitFor({ state: "visible" });
  await historyGroup.getByText("1 visible", { exact: true }).waitFor({ state: "visible" });
  await historyGroup
    .getByRole("button", { name: "Show retained history (1)" })
    .waitFor({ state: "visible" });
  assert.equal(
    await operations.locator('[data-attention-id="attention-terminal-inbox"]').count(),
    0,
  );
  assert.equal(await operations.locator(".runtime-attention-card details[open]").count(), 0);

  const activeRows = activeGroup.locator("[data-attention-id]");
  assert.equal(await activeRows.count(), 20);
  assert.deepEqual(
    (await activeRows.evaluateAll((nodes) =>
      nodes.slice(0, 3).map((node) => node.dataset.attentionId),
    )),
    ["attention-active-blocker", "attention-active-inbox", "attention-active-20"],
  );
  const activePagination = activeGroup.locator(
    'nav[aria-label="Active Obligation pages"]',
  );
  await activePagination.getByText("Showing 1–20 of 22", { exact: true }).waitFor({
    state: "visible",
  });
  await activePagination.getByRole("button", { name: "Next" }).click();
  assert.deepEqual(
    await activeGroup
      .locator("[data-attention-id]")
      .evaluateAll((nodes) => nodes.map((node) => node.dataset.attentionId)),
    ["attention-active-02", "attention-active-01"],
  );
  await activeGroup.getByText("Showing 21–22 of 22", { exact: true }).waitFor({
    state: "visible",
  });
  await activeGroup.getByRole("button", { name: "Previous" }).click();

  assert.deepEqual(
    await operations
      .locator(".runtime-attention-facets label > span")
      .evaluateAll((nodes) => nodes.map((node) => node.textContent.trim())),
    ["Kind", "Age", "Team", "Project", "Agent"],
  );
  await operations
    .locator('[data-runtime-attention-filter="kind"]')
    .selectOption("pending_inbox_item");
  await operations
    .locator('[data-runtime-attention-filter="age"]')
    .selectOption("under_24_hours");
  await operations
    .locator('[data-runtime-attention-filter="team"]')
    .selectOption("team-beta");
  await operations
    .locator('[data-runtime-attention-filter="project"]')
    .selectOption("beta-project");
  await operations
    .locator('[data-runtime-attention-filter="agent"]')
    .selectOption("beta-lead");
  assert.equal(await activeGroup.locator("[data-attention-id]").count(), 1);
  await activeGroup
    .locator('[data-attention-id="attention-active-inbox"]')
    .waitFor({ state: "visible" });
  await failedGroup
    .getByText("No failed attempts match these filters.")
    .waitFor({ state: "visible" });
  await operations.getByRole("button", { name: "Clear filters" }).click();
  await activeGroup.getByText("22 visible", { exact: true }).waitFor({ state: "visible" });

  const blockerRow = activeGroup.locator(
    '[data-attention-id="attention-active-blocker"]',
  );
  await blockerRow.locator(".runtime-attention-kind-label", { hasText: "Blocker" }).waitFor({
    state: "visible",
  });
  assert.match(
    (await blockerRow.getAttribute("class")) || "",
    /runtime-attention-kind-blocker/,
  );
  const blockerSummaryText = await blockerRow.locator("summary").first().innerText();
  assert.match(blockerSummaryText, /Packaging approval is blocked/);
  assert.match(blockerSummaryText, /Alpha Project/);
  assert.match(blockerSummaryText, /Plan alpha delivery/);
  assert.match(blockerSummaryText, /Owner: Grace Lead/);
  assert.match(blockerSummaryText, /30 minutes ago/);
  assert.match(blockerSummaryText, /critical urgency/i);
  assert.doesNotMatch(
    blockerSummaryText,
    /attention-active-blocker|alpha-project|alpha-lead|task-planned/,
  );
  assert.equal(
    await blockerRow.locator('time[datetime="2026-07-27T11:30:00Z"]').count(),
    1,
  );

  const inboxRow = activeGroup.locator('[data-attention-id="attention-active-inbox"]');
  assert.match(
    (await inboxRow.getAttribute("class")) || "",
    /runtime-attention-kind-awaiting-response/,
  );
  await inboxRow
    .locator(".runtime-attention-kind-label", { hasText: "Awaiting agent response" })
    .waitFor({ state: "visible" });
  await inboxRow.locator(":scope > details > summary").click();
  const activeInboxBody = await inboxRow.locator(".compact-record-body").innerText();
  assert.match(activeInboxBody, /response or explicit inbox reconciliation is still outstanding/i);
  assert.doesNotMatch(activeInboxBody, /does not by itself prove an active operational issue/i);

  const failedRow = failedGroup.locator(
    '[data-attention-id="attention-promotion-failed"]',
  );
  assert.match(
    (await failedRow.getAttribute("class")) || "",
    /runtime-attention-kind-failed-operation/,
  );
  const failedSummaryText = await failedRow.locator("summary").first().innerText();
  assert.match(failedSummaryText, /Git promotion failed/);
  assert.match(failedSummaryText, /Beta Project/);
  assert.match(failedSummaryText, /Owner: Beta Lead/);
  assert.doesNotMatch(
    failedSummaryText,
    /attention-promotion-failed|beta-project|beta-lead|promotion-invalid-candidate|deadbeef/,
  );
  await failedRow.locator(":scope > details > summary").click();
  for (const heading of [
    "Problem",
    "Impact",
    "Owner",
    "Recommended next action",
    "Related work",
    "Technical details",
  ]) {
    await failedRow.getByRole("heading", { name: heading, exact: true }).waitFor({
      state: "visible",
    });
  }
  const failedBodyText = await failedRow.locator(".compact-record-body").innerText();
  assert.equal(
    failedBodyText.match(/Candidate commit is not reachable from the approved branch\./g)
      ?.length,
    1,
  );
  assert.match(failedBodyText, /Inspect the promotion target and candidate/);
  assert.match(failedBodyText, /Blocked delivery/);

  const technicalDetails = failedRow.locator(
    ".runtime-technical-section details.technical-details",
  );
  assert.equal(
    await technicalDetails
      .getByText("attention-promotion-failed", { exact: true })
      .isVisible(),
    false,
  );
  await technicalDetails.locator("summary").click();
  await technicalDetails
    .getByText("attention-promotion-failed", { exact: true })
    .waitFor({ state: "visible" });
  await technicalDetails
    .getByText("agents/beta-lead/release-candidate", { exact: true })
    .waitFor({ state: "visible" });
  await technicalDetails.getByText("deadbeefbadc0de", { exact: true }).waitFor({
    state: "visible",
  });
  const copyAttentionId = technicalDetails.getByRole("button", {
    name: "Copy ID: attention-promotion-failed",
  });
  await copyAttentionId.click();
  await copyAttentionId.getByText("Copied", { exact: true }).waitFor({
    state: "visible",
  });
  assert.equal(
    await page.evaluate(() => navigator.clipboard.readText()),
    "attention-promotion-failed",
  );

  await historyGroup.getByRole("button", { name: "Show retained history (1)" }).click();
  const historyRow = historyGroup.locator(
    '[data-attention-id="attention-terminal-inbox"]',
  );
  await historyRow.waitFor({ state: "visible" });
  await historyRow.locator(":scope > details > summary").click();
  await historyRow.getByText("Terminal Task: Accepted", { exact: true }).first().waitFor({
    state: "visible",
  });
  await historyRow.getByText("Reconciliation required", { exact: true }).first().waitFor({
    state: "visible",
  });
  assert.equal(
    await historyRow.locator('time[datetime="2026-07-20T12:00:00Z"]').count(),
    1,
  );
  const mailLink = historyRow.getByRole("button", { name: "Open Agent Mail" });
  await mailLink.waitFor({ state: "visible" });
  await mailLink.click();
  await page.locator("#team-mail-page").waitFor({ state: "visible" });

  await page.getByRole("tab", { name: "Workspace" }).click();
  const workspace = page.locator("#team-workspace-page");
  await workspace.waitFor({ state: "visible" });
  await workspace
    .getByText("Release candidate promotion failed", { exact: true })
    .waitFor({ state: "visible" });
  assert.equal(
    await workspace.getByText(
      "Accepted delivery still has pending correspondence",
      { exact: true },
    ).count(),
    0,
  );
});

test("team add-on runtime attention escapes state and error text exactly once", async (t) => {
  const initialState = seededGitState();
  initialState.runtime_operations.attention.push({
    id: "failed-action-special-characters",
    kind: "failed_action_record",
    severity: "critical",
    title: "Failed action with validation details",
    state: "failed",
    error: "invalid {'key': '<value>&'}\nretry isn't allowed",
    detail: "Container validation rejected the request.",
    action_record_id: "action-special-characters",
  });
  const { port } = await createTeamAddonServer(t, { initialState });
  const page = await openBrowserPage(t, port);

  await waitForRevision(page, 4);
  await page.getByRole("tab", { name: "Operations" }).click();
  await page.locator("#team-operations-page").waitFor({ state: "visible" });
  await page
    .locator("#team-operations-page")
    .getByRole("button", { name: "Needs attention" })
    .click();
  const row = page.locator(
    '#team-operations-page [data-attention-id="failed-action-special-characters"]',
  );
  await row.locator(":scope > details > summary").click();
  const bodyText = await row.locator(".compact-record-body").innerText();

  assert.match(bodyText, /invalid \{'key': '<value>&'\}/);
  assert.match(bodyText, /retry isn't allowed/);
  assert.doesNotMatch(bodyText, /&#(?:39|x27);|&lt;|&amp;/);
  assert.equal(bodyText.match(/invalid \{'key': '<value>&'\}/g)?.length, 1);
});

test("team add-on UI exposes focused workspace tabs without duplicate hidden rows", async (t) => {
  const initialState = seededGitState();
  initialState.agents["alpha-manager"].display_name = "Ada Manager";
  initialState.agents["alpha-lead"].display_name = "Grace Lead";
  initialState.agents["beta-manager"].display_name = "Zed Manager";
  initialState.agents["beta-lead"].display_name = "Beta Lead";
  const { port } = await createTeamAddonServer(t, {
    initialState,
  });
  const page = await openBrowserPage(t, port);

  await waitForRevision(page, 4);
  await page.locator("#status-message").waitFor({ state: "hidden" });
  assert.equal(await page.getByText("Loaded revision 4.").count(), 0);

  for (const tabName of [
    "Workspace",
    "Operations",
    "Delivery",
    "Task board",
    "Teams",
    "Mail",
    "Governance",
    "Planning",
    "Activity timeline",
    "Org chart",
  ]) {
    await page.getByRole("tab", { name: tabName }).waitFor({ state: "visible" });
  }
  assert.equal(await page.getByRole("tab", { name: "Setup" }).count(), 0);
  assert.equal(await page.getByRole("tab", { name: "Overview", exact: true }).count(), 0);
  assert.equal(await page.locator("#team-overview-page").count(), 0);

  await page.getByRole("tab", { name: "Operations" }).click();
  await page.locator("#team-operations-page").waitFor({ state: "visible" });
  await page.locator("#team-operations-page").getByRole("button", { name: "Needs attention" }).click();
  await page.locator("#team-operations-page").getByText("Showing Needs attention.").waitFor({
    state: "visible",
  });
  await page.locator("#team-operations-page .runtime-item-compact").first().waitFor({
    state: "visible",
  });
  const runtimeAttentionText = await page
    .locator("#team-operations-page .runtime-item-compact")
    .allInnerTexts();
  assert.ok(
    runtimeAttentionText.some((value) => value.includes("Session expired for Grace Lead")),
    `expected the expired session attention card: ${JSON.stringify(runtimeAttentionText)}`,
  );
  assert.equal(
    await page.locator("#team-operations-page").getByRole("heading", { name: "Agents needing attention" }).count(),
    0,
  );
  assert.equal(await page.locator("#team-operations-page .runtime-agent-item").count(), 0);
  await page.locator("#team-operations-page").getByText("session expired").first().waitFor({
    state: "visible",
  });
  assert.equal(await page.locator("#team-operations-page .runtime-item-compact details[open]").count(), 0);

  await page.locator("#team-operations-page").getByRole("button", { name: "Due / leased agents" }).click();
  await page
    .locator("#team-operations-page")
    .getByRole("heading", { name: "Due / leased agents" })
    .waitFor({ state: "visible" });
  const dueAgent = page.locator("#team-operations-page .runtime-agent-item").filter({
    hasText: "alpha-lead",
  });
  await dueAgent.waitFor({ state: "visible" });
  assert.match(await dueAgent.locator("summary").innerText(), /wake due/);

  await page.getByRole("tab", { name: "Delivery" }).click();
  await page.locator("#team-delivery-page").waitFor({ state: "visible" });
  await page.locator("#team-delivery-page").getByRole("heading", { name: "Alpha Team", exact: true }).waitFor({
    state: "visible",
  });
  assert.equal(await page.locator(".delivery-row").count(), 5);
  assert.equal(await page.locator("#team-delivery-page .delivery-row[open]").count(), 0);
  assert.equal(await page.locator("#team-workspace-page .delivery-row").count(), 0);
  await page.locator("#team-delivery-page .review-profiles-view .record-item").first().waitFor({
    state: "visible",
  });
  await page.locator("#team-delivery-page .releases-view .record-item").first().waitFor({
    state: "visible",
  });

  await page.getByRole("tab", { name: "Task board" }).click();
  await page.locator("#team-task-board-page").waitFor({ state: "visible" });
  await page.locator("#task-board-view").getByText("Select a team and project to view its task board.").waitFor({
    state: "visible",
  });
  await page.locator("#task-board-team-select").selectOption("team-alpha");
  assert.equal(await page.locator('#task-board-project-select option[value="alpha-project"]').count(), 1);
  assert.equal(await page.locator('#task-board-project-select option[value="beta-project"]').count(), 0);
  await page.locator("#task-board-project-select").selectOption("alpha-project");
  await page.locator("#team-task-board-page .task-board-lane").first().waitFor({
    state: "visible",
  });
  assert.equal(await page.locator("#team-task-board-page .task-board-lane").count(), 4);
  await page.locator(".task-board-lane-planned").getByText("Plan alpha delivery").waitFor({
    state: "visible",
  });
  await page.locator(".task-board-lane-in-progress").getByText("Draft pending delivery").waitFor({
    state: "visible",
  });
  await page.locator(".task-board-lane-reviewing").getByText("Await review").waitFor({
    state: "visible",
  });
  assert.equal(await page.locator(".task-board-lane-done").count(), 0);
  assert.equal(
    await page.locator(".task-board-lane-done").getByText("Ship accepted delivery").count(),
    0,
  );
  await page.getByRole("checkbox", { name: "Show completed (1)" }).check();
  await page.locator(".task-board-lane-done").getByText("Ship accepted delivery").waitFor({
    state: "visible",
  });
  const inProgressTaskTitles = await page
    .locator(".task-board-lane-in-progress .task-board-card-title-row strong")
    .evaluateAll((nodes) => nodes.map((node) => node.textContent?.trim()));
  assert.deepEqual(inProgressTaskTitles, ["Review new runtime signal", "Draft pending delivery"]);
  await page
    .locator(".task-board-card", { hasText: "Draft pending delivery" })
    .getByText("Assigned to: Grace Lead")
    .waitFor({ state: "visible" });
  await page.locator("#task-board-team-select").selectOption("team-beta");
  await page.locator("#task-board-project-select").selectOption("beta-project");
  const blockedCard = page.locator(".task-board-card-blocked", { hasText: "Blocked delivery" });
  await blockedCard.locator(".state-pill").filter({ hasText: /^Blocked$/ }).waitFor({
    state: "visible",
  });
  await blockedCard.getByText("Assigned to: Beta Lead").waitFor({ state: "visible" });

  await page.getByRole("tab", { name: "Teams" }).click();
  await page.locator("#team-people-page").waitFor({ state: "visible" });
  assert.equal(await page.locator("#team-people-page .team-record-group").count(), 2);
  assert.equal(await page.locator("#team-people-page .team-record-group.is-collapsed").count(), 2);
  assert.equal(await page.locator("#team-people-page .team-record-section").count(), 0);
  assert.equal(await page.locator("#team-people-page [data-agent-id]").count(), 0);
  const alphaGroup = page.locator('#team-people-page [data-team-record-group-id="team-alpha"]');
  const betaGroup = page.locator('#team-people-page [data-team-record-group-id="team-beta"]');
  await alphaGroup.locator(".team-record-group-title strong", { hasText: "Alpha Team" }).waitFor({
    state: "visible",
  });
  await betaGroup.locator(".team-record-group-title strong", { hasText: "Beta Team" }).waitFor({
    state: "visible",
  });
  assert.equal(await page.locator("#team-people-page .hierarchy-view").count(), 0);
  assert.equal(await page.locator("#team-people-page .people-agent-detail-panel").count(), 1);
  assert.equal(await page.locator("#team-people-page").getByRole("heading", { name: "Agent details" }).count(), 0);
  assert.equal(
    await page.locator("#team-people-page .people-layout").evaluate((element) => {
      return getComputedStyle(element).gridTemplateColumns.split(" ").length;
    }),
    2,
  );
  await alphaGroup.locator(".team-record-group-header").click();
  await alphaGroup.locator(".team-record-group-body").waitFor({ state: "visible" });
  assert.equal(await page.locator("#team-people-page .team-record-group.is-expanded").count(), 1);
  assert.equal(await alphaGroup.locator("[data-agent-id]").count(), 2);
  assert.equal(await betaGroup.locator("[data-agent-id]").count(), 0);

  const peopleIdentityRects = await alphaGroup.locator(".people-agent-select").evaluateAll((buttons) => {
    return buttons
      .map((button) => {
        const avatar = button.querySelector(".agent-avatar")?.getBoundingClientRect();
        const identity = button.querySelector(".compact-record-main")?.getBoundingClientRect();
        if (!avatar || !identity) {
          return null;
        }
        return {
          avatarRight: avatar.right,
          identityLeft: identity.left,
        };
      })
      .filter(Boolean);
  });
  assert.ok(peopleIdentityRects.length >= 2, "expected multiple People agent rows");
  const peopleIdentityLefts = peopleIdentityRects.map((rect) => Math.round(rect.identityLeft));
  assert.ok(
    Math.max(...peopleIdentityLefts) - Math.min(...peopleIdentityLefts) <= 1,
    `expected People agent identity columns to align, got ${peopleIdentityLefts.join(", ")}`,
  );
  for (const rect of peopleIdentityRects) {
    const avatarGap = rect.identityLeft - rect.avatarRight;
    assert.ok(avatarGap >= 0 && avatarGap <= 16, `expected People identity to start near avatar, got ${avatarGap}px`);
  }
  const peopleLabels = await alphaGroup.locator(".people-agent-select").evaluateAll((buttons) => {
    return buttons.map((button) => button.querySelector(".compact-record-main strong")?.textContent?.trim());
  });
  assert.deepEqual(peopleLabels, ["Ada Manager", "Grace Lead"]);
  assert.equal(await alphaGroup.locator(".people-agent-select .agent-summary-meta").count(), 0);

  await alphaGroup.locator('[data-team-record-tab="project"]').click();
  const alphaProjectRecord = alphaGroup.locator('[data-project-id="alpha-project"]');
  await alphaProjectRecord.getByRole("button").click();
  await alphaProjectRecord.locator('.team-record-select[aria-pressed="true"]').waitFor({
    state: "visible",
  });
  await page.locator("#people-agent-detail-view").getByRole("heading", { name: "Alpha Project" }).waitFor({
    state: "visible",
  });
  await page
    .locator("#people-agent-detail-view .agent-detail-section strong")
    .filter({ hasText: /^Ship alpha work$/ })
    .waitFor({
      state: "visible",
    });
  await page.locator('#team-people-page [data-team-record-tab="role"]').click();
  const alphaLeadRoleCard = page.locator('#team-people-page [data-role-id="alpha-project__project-lead"]');
  await alphaLeadRoleCard.getByRole("button").click();
  await alphaLeadRoleCard.locator('.team-record-select[aria-pressed="true"]').waitFor({
    state: "visible",
  });
  await page.locator("#people-agent-detail-view").getByRole("heading", { name: "Project Lead" }).waitFor({
    state: "visible",
  });
  await page.locator("#people-agent-detail-view").getByText("Create Root Task").waitFor({
    state: "visible",
  });
  await page.locator('#team-people-page [data-team-record-tab="agent"]').click();
  const alphaLeadCard = page.locator('#team-people-page [data-agent-id="alpha-lead"]');
  await alphaLeadCard.waitFor({ state: "visible" });
  assert.equal((await alphaLeadCard.locator(".people-agent-role").innerText()).trim(), "Project Lead");
  assert.equal((await alphaLeadCard.innerText()).includes("alpha-lead"), false);
  await alphaLeadCard.getByRole("button").click();
  await alphaLeadCard.locator('.people-agent-select[aria-pressed="true"]').waitFor({
    state: "visible",
  });
  await page.locator("#people-agent-detail-view").getByRole("heading", { name: "Grace Lead" }).waitFor({
    state: "visible",
  });
  await page.locator("#people-agent-detail-view .agent-detail-value", { hasText: "session-alpha-lead" }).waitFor({
    state: "visible",
  });
  await page
    .locator("#people-agent-detail-view .agent-detail-work-line strong", { hasText: "Plan alpha delivery" })
    .waitFor({ state: "visible" });
  const peopleStatusIndicator = page.locator(
    "#people-agent-detail-view .agent-detail-badges .agent-inbox-indicator--status_request",
  );
  await peopleStatusIndicator.hover();
  await peopleStatusIndicator.focus();
  const peopleStatusPopover = peopleStatusIndicator.locator(".agent-inbox-popover");
  await peopleStatusPopover.waitFor({ state: "visible" });
  await page.waitForFunction(
    () => {
      const popover = document.querySelector(
        "#people-agent-detail-view .agent-detail-badges .agent-inbox-indicator--status_request .agent-inbox-popover",
      );
      if (!(popover instanceof HTMLElement)) {
        return false;
      }
      const rect = popover.getBoundingClientRect();
      return rect.left >= 0 && rect.right <= document.documentElement.clientWidth;
    },
    null,
    { timeout: 2000 },
  );
  const peopleStatusPopoverBox = await peopleStatusPopover.boundingBox();
  const viewport = page.viewportSize();
  assert.ok(peopleStatusPopoverBox, "expected People inbox popover to have a bounding box");
  assert.ok(viewport, "expected browser viewport size");
  assert.ok(
    peopleStatusPopoverBox.x >= 0 &&
      peopleStatusPopoverBox.x + peopleStatusPopoverBox.width <= viewport.width,
    `People inbox popover should stay inside the viewport: ${JSON.stringify({
      popover: peopleStatusPopoverBox,
      viewport,
    })}`,
  );

  await page.getByRole("tab", { name: "Planning" }).click();
  await page.locator("#team-planning-page").waitFor({ state: "visible" });
  await page.locator("#team-planning-page .planning-project-card").first().waitFor({
    state: "visible",
  });
  assert.equal(await page.locator(".project-block").count(), 2);
  assert.equal(await page.locator("#team-planning-page .planning-project-card details[open]").count(), 0);
  const alphaProjectCard = page.locator("#team-planning-page .planning-project-card", { hasText: "Alpha Project" });
  await alphaProjectCard.locator(":scope > details > summary").click();
  await page
    .locator("#team-planning-page")
    .getByText("Git: managed mr_aaaaaaaaaaaaaaaaaaaa - base main")
    .waitFor({
    state: "visible",
  });
  await page
    .locator('#team-planning-page [data-planning-task-id="task-pending"]')
    .getByText("Draft pending delivery", { exact: true })
    .waitFor({
      state: "visible",
    });
  assert.equal(
    await page.locator(
      '#team-planning-page [data-planning-task-id="task-accepted"]',
    ).count(),
    0,
  );
  await page.locator("#planning-status-accepted").check();
  await alphaProjectCard.locator(":scope > details > summary").click();
  await page
    .locator('#team-planning-page [data-planning-task-id="task-accepted"]')
    .getByText("Ship accepted delivery", { exact: true })
    .waitFor({
      state: "visible",
    });

  await page.getByRole("tab", { name: "Activity timeline", exact: true }).click();
  await page.locator("#team-timeline-page").waitFor({ state: "visible" });
  await page.locator("#team-timeline-page .timeline-entry").first().waitFor({ state: "visible" });
  assert.equal(await page.locator("#team-timeline-page .timeline-entry").count(), 10);
  await page.locator("#team-timeline-page .timeline-pagination").getByText(/1-10 of/).waitFor({
    state: "visible",
  });
  assert.equal(await page.locator("#team-timeline-page .timeline-entry details[open]").count(), 0);
  await page
    .locator("#team-timeline-page .timeline-entry")
    .first()
    .locator(":scope > details > summary")
    .click();
  await page.locator("#team-timeline-page .timeline-entry details[open]").first().waitFor({
    state: "visible",
  });
  await page.locator("#focused-timeline-type-filter").selectOption("review");
  await page.locator("#team-timeline-page .timeline-entry").first().waitFor({ state: "visible" });
  assert.equal(await page.locator("#focused-timeline-type-filter").inputValue(), "review");

  await page.getByRole("tab", { name: "Org chart" }).click();
  await page.locator("#team-org-page").waitFor({ state: "visible" });
  await page.locator("#team-org-page [data-team-org-flow-canvas]").waitFor({ state: "visible" });
  assert.equal(await page.locator("#team-org-page .org-focus-team").count(), 2);
  await page.locator('#team-org-page [data-org-team-id="all"][aria-selected="true"]').waitFor({
    state: "visible",
  });
  await page.locator("#team-org-page").getByRole("heading", { name: "Alpha Team" }).waitFor({
    state: "visible",
  });
  await page.locator("#team-org-page").getByRole("heading", { name: "Beta Team" }).waitFor({
    state: "visible",
  });
  await page.locator('#team-org-page [data-org-team-id="team-alpha"]').click();
  assert.equal(await page.locator("#team-org-page .org-focus-team").count(), 1);
  await page.locator("#team-org-page").getByRole("heading", { name: "Alpha Team" }).waitFor({
    state: "visible",
  });
  assert.equal(await page.locator("#team-org-page").getByRole("heading", { name: "Beta Team" }).count(), 0);
  await page.locator('#team-org-page [data-org-team-id="team-beta"]').click();
  assert.equal(await page.locator("#team-org-page .org-focus-team").count(), 1);
  await page.locator("#team-org-page").getByRole("heading", { name: "Beta Team" }).waitFor({
    state: "visible",
  });
  assert.equal(await page.locator("#team-org-page").getByRole("heading", { name: "Alpha Team" }).count(), 0);
  await page.locator('#team-org-page [data-org-team-id="all"]').click();
  assert.equal(await page.locator("#team-org-page .org-focus-team").count(), 2);

  await page.getByRole("tab", { name: "Workspace" }).click();
  await page.locator("#team-workspace-page").waitFor({ state: "visible" });
  await page.locator("#team-workspace-page").getByRole("heading", { name: "Team Dashboard" }).waitFor({
    state: "visible",
  });
  await page.locator("#team-workspace-page").getByText("Needs attention").first().waitFor({
    state: "visible",
  });
  assert.equal(await page.locator("#team-workspace-page .workspace-metric-card").count(), 6);
  assert.equal(await page.locator("#team-workspace-page .delivery-row").count(), 0);
  assert.equal(await page.locator("#team-workspace-page #setup-template-form").count(), 0);
  assert.equal(await page.locator("#team-setup-page").count(), 0);
  await page.locator("#team-workspace-page .workspace-dashboard-actions").getByRole("button", { name: "Delivery" }).click();
  await page.locator("#team-delivery-page").waitFor({ state: "visible" });
  await page.locator("#team-delivery-page .delivery-row").first().waitFor({ state: "visible" });
});

test("team add-on Task board keeps space between panels, cards, and view tabs", async (t) => {
  const { port } = await createTeamAddonServer(t, { initialState: seededGitState() });
  const page = await openBrowserPage(t, port, { viewport: { width: 1280, height: 1600 } });

  await waitForRevision(page, 4);
  await page.getByRole("tab", { name: "Task board" }).click();
  await page.locator("#task-board-team-select").selectOption("team-alpha");
  await page.locator("#task-board-project-select").selectOption("alpha-project");
  await page.locator("#task-board-view .task-board-lanes").waitFor({ state: "visible" });

  const taskBoardSpacing = await page.locator("#task-board-view").evaluate((view) => {
    const sections = [
      ".task-board-context",
      ".task-board-create",
      ".task-board-toolbar",
      ".task-board-filters",
      ".task-board-lanes",
    ].map((selector) => view.querySelector(selector));
    const gaps = sections.slice(1).map((section, index) => {
      const previousRect = sections[index].getBoundingClientRect();
      const currentRect = section.getBoundingClientRect();
      return currentRect.top - previousRect.bottom;
    });
    const modeSwitch = view.querySelector(".task-board-mode-switch");
    const modeTabs = [...modeSwitch.querySelectorAll('[role="tab"]')];
    const firstTabRect = modeTabs[0].getBoundingClientRect();
    const secondTabRect = modeTabs[1].getBoundingClientRect();
    return {
      display: getComputedStyle(view).display,
      gaps,
      tabGap: parseFloat(getComputedStyle(modeSwitch).gap),
      tabDistance: secondTabRect.left - firstTabRect.right,
    };
  });

  assert.equal(taskBoardSpacing.display, "grid");
  assert.ok(
    taskBoardSpacing.gaps.every((gap) => gap >= 12),
    `expected separated task-board sections, received gaps ${taskBoardSpacing.gaps.join(", ")}`,
  );
  assert.ok(taskBoardSpacing.tabGap >= 8, "expected a visible gap between Board and Hierarchy tabs");
  assert.ok(
    taskBoardSpacing.tabDistance >= 8,
    `expected Board and Hierarchy tabs to be separated, received ${taskBoardSpacing.tabDistance}px`,
  );

  await page.getByRole("tab", { name: "Hierarchy" }).click();
  await page.locator("#task-board-view .task-board-hierarchy").waitFor({ state: "visible" });
  const hierarchyGap = await page.locator("#task-board-view").evaluate((view) => {
    const filters = view.querySelector(".task-board-filters").getBoundingClientRect();
    const hierarchy = view.querySelector(".task-board-hierarchy").getBoundingClientRect();
    return hierarchy.top - filters.bottom;
  });
  assert.ok(hierarchyGap >= 12, `expected space before the hierarchy panel, received ${hierarchyGap}px`);
});

test("team add-on Teams agent detail inbox popover stays reachable and scrollable", async (t) => {
  const { port } = await createTeamAddonServer(t, {
    initialState: seededScrollablePeopleTooltipState(),
  });
  const page = await openBrowserPage(t, port, {
    viewport: { width: 760, height: 720 },
  });

  await waitForRevision(page, 4);
  await page.getByRole("tab", { name: "Teams" }).click();
  await page.locator("#team-people-page").waitFor({ state: "visible" });
  const alphaGroup = page.locator('#team-people-page [data-team-record-group-id="team-alpha"]');
  await alphaGroup.locator(".team-record-group-header").click();
  await alphaGroup.locator(".team-record-group-body").waitFor({ state: "visible" });
  await alphaGroup.locator('[data-team-record-tab="agent"]').click();
  const alphaLeadCard = alphaGroup.locator('[data-agent-id="alpha-lead"]');
  assert.equal(await alphaLeadCard.locator(".agent-inbox-indicator--status_request").count(), 0);
  await alphaLeadCard.getByRole("button").click();
  await page.locator("#people-agent-detail-view").getByRole("heading", { name: "alpha-lead" }).waitFor({
    state: "visible",
  });

  const peopleStatusIndicator = page.locator(
    "#people-agent-detail-view .agent-detail-badges .agent-inbox-indicator--status_request",
  );
  await peopleStatusIndicator.hover();
  await peopleStatusIndicator.focus();
  const peopleStatusPopover = peopleStatusIndicator.locator(".agent-inbox-popover");
  await peopleStatusPopover.waitFor({ state: "visible" });
  await page.waitForFunction(
    () => document.documentElement.scrollWidth <= document.documentElement.clientWidth,
    null,
    { timeout: 2000 },
  );
  const indicatorBox = await peopleStatusIndicator.boundingBox();
  const popoverBox = await peopleStatusPopover.boundingBox();
  assert.ok(indicatorBox, "expected People inbox indicator to have a bounding box");
  assert.ok(popoverBox, "expected People inbox popover to have a bounding box");
  assert.equal(
    await page.evaluate(() => {
      return document.documentElement.scrollWidth <= document.documentElement.clientWidth;
    }),
    true,
  );

  await page.mouse.move(
    indicatorBox.x + indicatorBox.width / 2,
    indicatorBox.y + indicatorBox.height / 2,
  );
  await page.mouse.move(popoverBox.x + 24, popoverBox.y + 24, { steps: 12 });
  assert.equal(await peopleStatusPopover.isVisible(), true);
  assert.equal(
    await peopleStatusPopover.evaluate((element) => element.scrollHeight > element.clientHeight),
    true,
  );

  const scrolledPopoverTop = await peopleStatusPopover.evaluate((element) => {
    element.scrollTop = 0;
    element.scrollTop = 600;
    return element.scrollTop;
  });
  assert.ok(scrolledPopoverTop > 0, "expected People detail popover content to scroll");
  assert.equal(await peopleStatusPopover.isVisible(), true);
});

test("team add-on UI renders git refs and releases and submits a review decision", async (t) => {
  const initialState = seededGitState();
  const acceptedRelease = initialState.release_packages["release-accepted"];
  acceptedRelease.detail = {
    ...acceptedRelease,
    policy_version: 2,
    candidate_commit_sha: "ccc333",
    manifest: {
      candidate_commit_sha: "ccc333",
      deliverables: [{ deliverable_id: "deliverable-accepted" }],
      verification_commands: [{ name: "targeted-dotnet" }],
      review_requirements: [{ reviewer_role_id: "alpha-project__project-lead" }],
      human_review_requirement: { reviewer_group_ids: ["release-approvers"] },
    },
    verification_runs: [
      {
        id: "verification-accepted-1",
        state: "failed",
        attempt_number: 1,
        failure_kind: "timeout",
        superseded_by_verification_run_id: "verification-accepted-2",
        command_results: [
          {
            name: "targeted-dotnet",
            state: "timed_out",
            exit_code: 124,
            log_excerpt: [
              "Starting targeted tests",
              "error: test host timed out waiting for worker response",
            ].join("\n"),
          },
        ],
      },
      {
        id: "verification-accepted-2",
        state: "passed",
        attempt_number: 2,
        supersedes_verification_run_id: "verification-accepted-1",
      },
    ],
    release_reviews: [
      {
        id: "release-review-accepted",
        reviewer_agent_id: "alpha-manager",
        state: "accepted",
      },
    ],
    human_review: {
      state: "approved",
      requests: [{ id: "human-review-accepted", state: "approved" }],
    },
  };
  const { port, requests, state, timelineRequests } = await createTeamAddonServer(t, {
    initialState,
  });
  const page = await openBrowserPage(t, port);

  await waitForRevision(page, 4);
  const timelineRequestsBeforeTabRefresh = timelineRequests.length;
  await page.getByRole("tab", { name: "Activity timeline", exact: true }).click();
  const timelinePage = page.locator("#team-timeline-page");
  await timelinePage.waitFor({ state: "visible" });
  const retentionNote = timelinePage.locator(".timeline-note");
  await retentionNote.waitFor({ state: "visible" });
  const retentionCopy = await retentionNote.innerText();
  assert.match(
    retentionCopy,
    /reconstructed from records still present in the current Team\s+snapshot/i,
  );
  assert.match(
    retentionCopy,
    /may omit earlier changes, deleted or compacted records, and previous\s+field values/i,
  );
  await timelinePage.getByText("Timestamp unavailable").first().waitFor({ state: "visible" });
  await timelinePage.locator(".timeline-entry").first().waitFor({ state: "visible" });
  assert.ok((await timelinePage.locator(".timeline-entry").count()) >= 10);
  await waitForCondition(
    () => timelineRequests.length > timelineRequestsBeforeTabRefresh,
    "expected timeline tab request",
  );
  const initialTimelineSearch = new URLSearchParams(timelineRequests.at(-1));
  assert.equal(initialTimelineSearch.get("limit"), "10");
  assert.equal(initialTimelineSearch.get("offset"), "0");
  const timelineRequestsBeforeDecision = timelineRequests.length;

  await page.getByRole("tab", { name: "Delivery" }).click();
  const deliveryPage = page.locator("#team-delivery-page");
  await deliveryPage.waitFor({ state: "visible" });
  const workspace = deliveryPage.locator(".review-delivery-panel");
  await workspace.getByRole("heading", { name: "Review & Delivery" }).waitFor({
    state: "visible",
  });
  await workspace.getByRole("heading", { name: "Alpha Team", exact: true }).waitFor({
    state: "visible",
  });
  await workspace.getByRole("heading", { name: "Beta Team", exact: true }).waitFor({
    state: "visible",
  });
  const deliveryFilter = (filter) => deliveryPage.locator(`.delivery-filter[data-filter="${filter}"]`);
  const projectFocus = deliveryPage.locator("[data-delivery-project-filter]");
  assert.equal(await deliveryFilter("attention").getAttribute("aria-pressed"), "true");
  assert.equal(await deliveryFilter("all").getAttribute("aria-pressed"), "false");
  assert.equal(await projectFocus.inputValue(), "");
  assert.equal(
    await projectFocus.locator('option[value=""]').textContent(),
    "All projects (collapsed)",
  );
  await workspace.getByText("4 matching deliverables", { exact: true }).waitFor({
    state: "visible",
  });
  const collapsedProjects = workspace.locator("details.delivery-project");
  assert.equal(await collapsedProjects.count(), 2);
  assert.deepEqual(
    await collapsedProjects.evaluateAll((projects) =>
      projects.map((project) => project.open),
    ),
    [false, false],
  );
  assert.equal(await deliveryPage.locator(".delivery-row").count(), 4);
  assert.equal(
    await deliveryPage.locator(
      '.delivery-row[data-deliverable-id="deliverable-accepted"]',
    ).count(),
    0,
  );

  await projectFocus.selectOption("alpha-project");
  await workspace.getByText("2 matching deliverables", { exact: true }).waitFor({
    state: "visible",
  });
  assert.equal(await workspace.locator("details.delivery-project").count(), 1);
  assert.equal(
    await workspace.locator("details.delivery-project").evaluate((project) => project.open),
    true,
  );
  await workspace.getByText("Alpha Project", { exact: true }).waitFor({
    state: "visible",
  });
  assert.equal(await workspace.getByText("Beta Project", { exact: true }).count(), 0);
  assert.equal(await deliveryPage.locator(".delivery-row").count(), 2);

  const pendingRow = deliveryPage.locator('.delivery-row[data-deliverable-id="deliverable-pending"]');
  await openDeliveryRow(pendingRow);
  await pendingRow.getByText("Draft pending delivery", { exact: true }).first().waitFor({
    state: "visible",
  });
  await pendingRow.getByRole("heading", { name: "Task and producer" }).waitFor({
    state: "visible",
  });
  await pendingRow.getByText("artifacts/pending.tar.gz", { exact: true }).waitFor({
    state: "visible",
  });
  const pendingTechnicalDetails = pendingRow.locator(
    ":scope > .delivery-row-body > section.deliverable-detail-group > details.technical-details",
  ).last();
  await pendingTechnicalDetails.locator("summary").click();
  await pendingTechnicalDetails.getByText("promo-pending", { exact: true }).waitFor({
    state: "visible",
  });

  const waitingRow = deliveryPage.locator('.delivery-row[data-deliverable-id="deliverable-waiting"]');
  await openDeliveryRow(waitingRow);
  await waitingRow.getByText("Review by alpha-manager", { exact: true }).waitFor({
    state: "visible",
  });
  await waitingRow.getByText(/Profile Alpha review profile/).waitFor({
    state: "visible",
  });
  await waitingRow.getByText("Profile criteria:").waitFor({ state: "visible" });
  await waitingRow.getByText("Open carried findings:").waitFor({ state: "visible" });
  await waitingRow.getByText("finding-ui - Remove the stale station-tool spec.").waitFor({
    state: "visible",
  });

  await deliveryFilter("accepted").click();
  const acceptedRow = deliveryPage.locator('.delivery-row[data-deliverable-id="deliverable-accepted"]');
  await openDeliveryRow(acceptedRow);
  const relatedRelease = acceptedRow.locator('[data-release-id="release-accepted"]');
  await relatedRelease.getByText("Release: Ship accepted delivery", { exact: true }).waitFor({
    state: "visible",
  });
  const acceptedTechnicalDetails = acceptedRow.locator(
    ":scope > .delivery-row-body > section.deliverable-detail-group > details.technical-details",
  ).last();
  await acceptedTechnicalDetails.locator("summary").click();
  await acceptedTechnicalDetails.getByText("promo-accepted", { exact: true }).waitFor({
    state: "visible",
  });
  await acceptedTechnicalDetails.getByText("promo-release", { exact: true }).waitFor({
    state: "visible",
  });
  await acceptedTechnicalDetails.getByText("team/alpha/releases/accepted", { exact: true }).waitFor({
    state: "visible",
  });
  await relatedRelease
    .getByText("Release notes, included work, and technical details", { exact: true })
    .click();
  await relatedRelease
    .getByText("Ship the accepted delivery after targeted verification.", { exact: true })
    .waitFor({ state: "visible" });
  await relatedRelease.locator(".release-included-work").getByText("Ship accepted delivery").waitFor({
    state: "visible",
  });
  await acceptedRow.getByText("tests: passed - Targeted tests passed").waitFor({
    state: "visible",
  });
  await acceptedRow.getByText("Findings: Looks good.").waitFor({ state: "visible" });

  const deliveryPipeline = acceptedRow.locator(
    'section[aria-label="Deliverable delivery pipeline"]',
  );
  await deliveryPipeline.getByText("Delivery pipeline", { exact: true }).waitFor({
    state: "visible",
  });
  await deliveryPipeline.getByRole("heading", { name: "Current: Publication" }).waitFor({
    state: "visible",
  });
  await deliveryPipeline
    .getByRole("button", { name: "Load exact gate evidence" })
    .click();
  await waitForCondition(
    () =>
      requests.some(
        (request) =>
          request.method === "GET" &&
          request.pathname ===
            "/api/addons/team-addon/release-packages/release-accepted",
      ),
    "expected release gate evidence request",
  );
  await openDeliveryRow(acceptedRow);
  await deliveryPipeline
    .getByText("Showing exact release gate evidence available to this operator.", {
      exact: true,
    })
    .waitFor({ state: "visible" });
  const gateEvidence = deliveryPipeline.locator(
    "details.delivery-pipeline-evidence",
  );
  await gateEvidence.getByText("Gate attempts and decisions", { exact: true }).click();
  await gateEvidence
    .getByText("Automated verification attempt 1", { exact: true })
    .waitFor({ state: "visible" });
  await gateEvidence
    .getByText("Automated verification attempt 2", { exact: true })
    .waitFor({ state: "visible" });
  await gateEvidence.getByText("Failure type: Timeout", { exact: true }).waitFor({
    state: "visible",
  });
  const failedGateCommand = gateEvidence.locator(
    ".release-verification-command-failed",
  );
  await failedGateCommand
    .getByText("Failed command: targeted-dotnet", { exact: true })
    .waitFor({ state: "visible" });
  await failedGateCommand.getByText("Exit code: 124", { exact: true }).waitFor({
    state: "visible",
  });
  await failedGateCommand
    .getByText(
      "Decisive error: error: test host timed out waiting for worker response",
      { exact: true },
    )
    .waitFor({ state: "visible" });
  const failedGateLog = failedGateCommand.locator(
    "details.release-verification-log",
  );
  await failedGateLog.getByText("Show captured command log", { exact: true }).click();
  assert.equal(
    await failedGateLog.locator("pre").innerText(),
    [
      "Starting targeted tests",
      "error: test host timed out waiting for worker response",
    ].join("\n"),
  );

  await projectFocus.selectOption("beta-project");
  await deliveryFilter("rejected").click();
  const rejectedRow = deliveryPage.locator('.delivery-row[data-deliverable-id="deliverable-rejected"]');
  await openDeliveryRow(rejectedRow);
  await rejectedRow.locator(".state-pill.rejected").first().waitFor({ state: "visible" });
  await rejectedRow.getByText("Findings: Needs more work.").waitFor({ state: "visible" });

  await deliveryFilter("blocked").click();
  const blockedRow = deliveryPage.locator('.delivery-row[data-deliverable-id="deliverable-blocked"]');
  await openDeliveryRow(blockedRow);
  await blockedRow.locator(".state-pill.blocked").first().waitFor({ state: "visible" });
  const blockedTechnicalDetails = blockedRow.locator(
    ":scope > .delivery-row-body > section.deliverable-detail-group > details.technical-details",
  ).last();
  await blockedTechnicalDetails.locator("summary").click();
  await blockedTechnicalDetails.getByText("promo-blocked", { exact: true }).waitFor({
    state: "visible",
  });
  assert.equal(
    await blockedRow.locator(
      'form[data-review-decision-form][data-review-request-id="review-blocked"]',
    ).count(),
    0,
  );

  await deliveryFilter("pending").click();
  assert.equal(await deliveryPage.locator(".delivery-row").count(), 0);
  await projectFocus.selectOption("alpha-project");
  assert.equal(await deliveryPage.locator(".delivery-row").count(), 1);
  await deliveryPage.locator('.delivery-row[data-deliverable-id="deliverable-pending"]').waitFor({
    state: "visible",
  });

  await deliveryFilter("waiting_for_review").click();
  assert.equal(await deliveryPage.locator(".delivery-row").count(), 1);
  await deliveryPage.locator('.delivery-row[data-deliverable-id="deliverable-waiting"]').waitFor({
    state: "visible",
  });
  const filteredDeliveryLayout = await deliveryPage.locator(".delivery-buckets").first().evaluate((bucket) => {
    const row = bucket.querySelector('.delivery-row[data-deliverable-id="deliverable-waiting"]');
    if (!(row instanceof HTMLElement)) {
      return null;
    }
    return {
      bucketWidth: bucket.getBoundingClientRect().width,
      rowWidth: row.getBoundingClientRect().width,
    };
  });
  assert.ok(filteredDeliveryLayout, "expected the filtered deliverable row to be rendered");
  assert.ok(
    filteredDeliveryLayout.rowWidth > filteredDeliveryLayout.bucketWidth * 0.7,
    `expected the filtered deliverable row to use the available workspace width: ${JSON.stringify(filteredDeliveryLayout)}`,
  );

  await deliveryFilter("accepted").click();
  assert.equal(await deliveryPage.locator(".delivery-row").count(), 1);
  await deliveryPage.locator('.delivery-row[data-deliverable-id="deliverable-accepted"]').waitFor({
    state: "visible",
  });

  await deliveryFilter("rejected").click();
  assert.equal(await deliveryPage.locator(".delivery-row").count(), 0);
  await projectFocus.selectOption("beta-project");
  assert.equal(await deliveryPage.locator(".delivery-row").count(), 1);
  await deliveryPage.locator('.delivery-row[data-deliverable-id="deliverable-rejected"]').waitFor({
    state: "visible",
  });

  await deliveryFilter("blocked").click();
  assert.equal(await deliveryPage.locator(".delivery-row").count(), 1);
  await deliveryPage.locator('.delivery-row[data-deliverable-id="deliverable-blocked"]').waitFor({
    state: "visible",
  });

  await deliveryFilter("all").click();
  await projectFocus.selectOption("alpha-project");
  assert.equal(await deliveryPage.locator(".delivery-row").count(), 3);
  await openDeliveryRow(waitingRow);

  const workspaceDecisionForm = deliveryPage.locator(
    'form[data-review-decision-form][data-review-request-id="review-waiting"]',
  );
  await workspaceDecisionForm.waitFor({ state: "visible" });
  await workspaceDecisionForm.locator('[name="state"]').selectOption("accepted");
  await workspaceDecisionForm.locator('[name="findings"]').fill("Looks good to me.");
  await workspaceDecisionForm.locator('[name="criterion_state__tests"]').selectOption("passed");
  await workspaceDecisionForm
    .locator('[name="criterion_finding__tests"]')
    .fill("Added acceptance notes");
  await workspaceDecisionForm.locator('[name="criterion_state__docs"]').selectOption("passed");
  await workspaceDecisionForm
    .locator('[name="criterion_finding__docs"]')
    .fill("README updated");
  await workspaceDecisionForm
    .locator('[name="finding_resolution__finding-ui"]')
    .fill("Removed and re-ran the OperatorClient test.");
  await workspaceDecisionForm.getByRole("button", { name: "Submit decision" }).click();
  await page.getByText("Saved revision 5.").waitFor({ state: "visible" });
  await waitForCondition(
    () => timelineRequests.length > timelineRequestsBeforeDecision,
    "expected timeline to refresh after a successful review decision",
  );

  const decisionRequest = requests.find((request) =>
    request.pathname === "/api/addons/team-addon/review-requests/review-waiting/decision",
  );
  assert.ok(decisionRequest, "expected a review decision request");
  assert.equal(decisionRequest.method, "POST");
  assert.equal(decisionRequest.payload.state, "accepted");
  assert.deepEqual(decisionRequest.payload.findings, ["Looks good to me."]);
  assert.deepEqual(decisionRequest.payload.criteria, [
    {
      criterion_id: "tests",
      state: "passed",
      finding: "Added acceptance notes",
    },
    {
      criterion_id: "docs",
      state: "passed",
      finding: "README updated",
    },
  ]);
  assert.deepEqual(decisionRequest.payload.finding_resolutions, [
    {
      finding_id: "finding-ui",
      resolution: "Removed and re-ran the OperatorClient test.",
      evidence_refs: [],
    },
  ]);
  assert.equal(decisionRequest.payload.expected_revision, 4);
  assert.equal(state.review_requests["review-waiting"].state, "accepted");
  assert.deepEqual(state.review_requests["review-waiting"].criteria, [
    {
      criterion_id: "tests",
      state: "passed",
      finding: "Added acceptance notes",
    },
    {
      criterion_id: "docs",
      state: "passed",
      finding: "README updated",
    },
  ]);
  await openDeliveryRow(waitingRow);
  await waitingRow.locator('time[datetime="2026-06-09T00:00:00Z"]').waitFor({
    state: "visible",
  });
  await waitingRow.getByText("Findings: Looks good to me.").waitFor({ state: "visible" });
  await waitingRow.getByText("tests: passed - Added acceptance notes").waitFor({
    state: "visible",
  });

  await page.getByRole("tab", { name: "Activity timeline", exact: true }).click();
  await timelinePage.waitFor({ state: "visible" });
  await waitForSelectOption(page, "#focused-timeline-type-filter", "review");
  const timelineTypeFilter = page.locator("#focused-timeline-type-filter");
  const timelineAttentionFilter = page.locator("#focused-timeline-attention-filter");
  await timelineTypeFilter.selectOption("review");
  await timelineAttentionFilter.selectOption("false");
  await timelinePage.locator(".timeline-entry").first().waitFor({ state: "visible" });
  const filteredTimelineSearch = new URLSearchParams(timelineRequests.at(-1));
  assert.equal(filteredTimelineSearch.get("type"), "review");
  assert.equal(filteredTimelineSearch.get("attention_needed"), "false");
  assert.equal(filteredTimelineSearch.get("limit"), "10");
  assert.equal(filteredTimelineSearch.get("offset"), "0");
  assert.equal(await timelinePage.locator(".timeline-entry").count(), 4);
  await timelinePage.getByText(/Requested review — Await review/).first().waitFor({
    state: "visible",
  });
  await timelinePage.getByText(/Review decided accepted — Await review/).first().waitFor({
    state: "visible",
  });
});

test("team add-on timeline filter options use full-history facets", async (t) => {
  const timelinePayload = {
    revision: 1,
    derived: true,
    audit_log: false,
    source: "derived_from_current_team_state",
    description:
      "Activity reconstructed from records retained in the current Team snapshot; this is not an audit log.",
    filters: {},
    timestampless_count: 0,
    total_count: 30,
    limit: 10,
    offset: 0,
    has_more: true,
    facets: {
      types: ["action", "inbox_item", "review"],
      statuses: ["handled", "succeeded"],
    },
    entries: [
      timelineEntry({
        id: "action:latest:created",
        type: "action",
        category: "created",
        timestamp: "2026-07-26T10:00:00Z",
        timestamp_source: "created_at",
        actor_agent_id: "agent",
        status: "succeeded",
        attention_needed: false,
        summary: "Latest action succeeded",
      }),
    ],
  };
  const { port } = await createTeamAddonServer(t, { timelinePayload });
  const page = await openBrowserPage(t, port);

  await page.getByRole("tab", { name: "Activity timeline", exact: true }).click();
  await page.locator("#team-timeline-page").waitFor({ state: "visible" });
  await waitForSelectOption(page, "#focused-timeline-type-filter", "review");
  await waitForSelectOption(page, "#focused-timeline-type-filter", "inbox_item");
  await waitForSelectOption(page, "#focused-timeline-status-filter", "handled");

  assert.deepEqual(
    await page.locator("#focused-timeline-type-filter option").allTextContents(),
    ["All types", "Inbox Item", "Review", "Action"],
  );
  assert.deepEqual(
    await page.locator("#focused-timeline-status-filter option").allTextContents(),
    ["All statuses", "Handled", "Succeeded"],
  );
});

test("team add-on Activity timeline renders snapshot-only updates without a state flow", async (t) => {
  const initialState = seededGitState();
  const timelinePayload = {
    revision: 4,
    derived: true,
    audit_log: false,
    source: "derived_from_current_team_state",
    description:
      "Activity reconstructed from records retained in the current Team snapshot; this is not an audit log.",
    filters: {},
    timestampless_count: 0,
    total_count: 1,
    limit: 10,
    offset: 0,
    has_more: false,
    facets: {
      types: ["task"],
      statuses: ["in_progress"],
    },
    entries: [
      timelineEntry({
        id: "task:task-pending:updated",
        type: "task",
        category: "updated",
        timestamp: "2026-07-27T11:00:00Z",
        timestamp_source: "updated_at",
        actor_agent_id: null,
        actor_subject: null,
        related_agent_ids: [],
        team_id: "team-alpha",
        project_id: "alpha-project",
        task_id: "task-pending",
        status: "in_progress",
        attention_needed: false,
        subject: "Draft pending delivery",
        change: null,
        summary: "Task snapshot updated: Draft pending delivery",
      }),
    ],
  };
  const { port } = await createTeamAddonServer(t, {
    initialState,
    timelinePayload,
  });
  const page = await openBrowserPage(t, port);

  await waitForRevision(page, 4);
  await page.getByRole("tab", { name: "Activity timeline", exact: true }).click();
  const entry = page.locator("#team-timeline-page .timeline-entry").first();
  await entry.waitFor({ state: "visible" });
  await entry.locator(":scope > details > summary").click();

  const changeSection = entry.locator(".timeline-change-section");
  await changeSection
    .getByText(
      "Operation: Snapshot updated; no state transition is implied.",
      { exact: true },
    )
    .waitFor({ state: "visible" });
  assert.equal(await changeSection.locator(".timeline-change-flow").count(), 0);
  assert.equal(
    await changeSection.getByText("Before", { exact: true }).count(),
    0,
  );
  assert.equal(
    await changeSection.getByText("After", { exact: true }).count(),
    0,
  );
});

test("team add-on activity timeline leads with human context and explains retained-history limits", async (t) => {
  const initialState = seededGitState();
  initialState.agents["alpha-manager"].display_name = "Ada Manager";
  initialState.agents["alpha-lead"].display_name = "Grace Lead";
  Object.assign(initialState.tasks["task-pending"], {
    title: "Restore customer export",
    state: "blocked",
    waiting_reason: "Upstream schema verification failed.",
    state_entered_at: "2026-07-27T10:45:00Z",
    updated_at: "2026-07-27T10:45:00Z",
  });

  const targetEntryId = "task:task-pending:updated";
  const timelinePayload = (state, url) => {
    const payload = buildTimelinePayload(state, url);
    return {
      ...payload,
      description:
        "Activity reconstructed from records retained in the current Team snapshot; this is not an audit log.",
      entries: payload.entries.map((entry) =>
        entry.id === targetEntryId
          ? {
              ...entry,
              actor_agent_id: "alpha-lead",
              actor_subject: "Grace Lead · Project Lead",
              subject: "Restore customer export",
              change: {
                field: "State",
                before: null,
                after: "Blocked",
                before_known: false,
              },
              reason: "Upstream schema verification failed.",
              summary: "Task entered blocked: Restore customer export",
            }
          : entry,
      ),
    };
  };
  const { port, timelineRequests } = await createTeamAddonServer(t, {
    initialState,
    timelinePayload,
  });
  const page = await openBrowserPage(t, port, {
    permissions: ["clipboard-read", "clipboard-write"],
  });

  await waitForRevision(page, 4);
  await page.getByRole("tab", { name: "Activity timeline", exact: true }).click();
  const timelinePage = page.locator("#team-timeline-page");
  await timelinePage.waitFor({ state: "visible" });
  await timelinePage
    .getByRole("heading", { name: "Activity timeline", exact: true })
    .waitFor({ state: "visible" });
  assert.equal(await page.getByRole("tab", { name: "Audit", exact: true }).count(), 0);
  const retentionNote = timelinePage.locator(".timeline-note");
  await retentionNote.waitFor({ state: "visible" });
  const retentionCopy = await retentionNote.innerText();
  assert.match(
    retentionCopy,
    /reconstructed from records still present in the current Team\s+snapshot/i,
  );
  assert.match(
    retentionCopy,
    /may omit earlier changes, deleted or compacted records, and previous\s+field values/i,
  );

  await waitForSelectOption(page, "#focused-timeline-team-filter", "team-alpha");
  await waitForSelectOption(page, "#focused-timeline-project-filter", "alpha-project");
  await waitForSelectOption(page, "#focused-timeline-agent-filter", "alpha-lead");
  const teamOptions = await page
    .locator("#focused-timeline-team-filter option")
    .allTextContents();
  const projectOptions = await page
    .locator("#focused-timeline-project-filter option")
    .allTextContents();
  const agentOptions = await page
    .locator("#focused-timeline-agent-filter option")
    .allTextContents();
  assert.ok(teamOptions.includes("Alpha Team"));
  assert.ok(projectOptions.includes("Alpha Project"));
  assert.ok(agentOptions.includes("Grace Lead · Project Lead"));
  assert.equal(teamOptions.some((label) => label.includes("team-alpha")), false);
  assert.equal(projectOptions.some((label) => label.includes("alpha-project")), false);
  assert.equal(agentOptions.some((label) => label.includes("alpha-lead")), false);

  await timelinePage.locator(".timeline-pagination").waitFor({ state: "visible" });
  const requestsBeforeNextPage = timelineRequests.length;
  await timelinePage
    .locator(".timeline-pagination")
    .getByRole("button", { name: "Next" })
    .click();
  await waitForCondition(
    () => timelineRequests.length > requestsBeforeNextPage,
    "expected the activity timeline next-page request",
  );
  const nextPageSearch = new URLSearchParams(timelineRequests.at(-1));
  assert.equal(nextPageSearch.get("limit"), "10");
  assert.equal(nextPageSearch.get("offset"), "10");

  for (const [selector, value] of [
    ["#focused-timeline-team-filter", "team-alpha"],
    ["#focused-timeline-project-filter", "alpha-project"],
    ["#focused-timeline-agent-filter", "alpha-lead"],
    ["#focused-timeline-type-filter", "task"],
    ["#focused-timeline-status-filter", "blocked"],
    ["#focused-timeline-attention-filter", "true"],
  ]) {
    const requestsBeforeFilter = timelineRequests.length;
    await page.locator(selector).selectOption(value);
    await waitForCondition(
      () => timelineRequests.length > requestsBeforeFilter,
      `expected an activity timeline request after selecting ${value}`,
    );
  }

  const filteredSearch = new URLSearchParams(timelineRequests.at(-1));
  assert.equal(filteredSearch.get("team_id"), "team-alpha");
  assert.equal(filteredSearch.get("project_id"), "alpha-project");
  assert.equal(filteredSearch.get("agent_id"), "alpha-lead");
  assert.equal(filteredSearch.get("type"), "task");
  assert.equal(filteredSearch.get("status"), "blocked");
  assert.equal(filteredSearch.get("attention_needed"), "true");
  assert.equal(filteredSearch.get("limit"), "10");
  assert.equal(filteredSearch.get("offset"), "0");

  const entry = timelinePage.locator(".timeline-entry").first();
  await entry.waitFor({ state: "visible" });
  const collapsedText = await entry.locator(":scope > details > summary").innerText();
  assert.match(collapsedText, /Task entered blocked: Restore customer export/);
  assert.doesNotMatch(
    collapsedText,
    /task-pending|team-alpha|alpha-project|alpha-lead/,
  );
  const technicalDetails = entry.locator("details.technical-details");
  assert.equal(await technicalDetails.count(), 1);
  assert.equal(await technicalDetails.evaluate((element) => element.open), false);

  await entry.locator(":scope > details > summary").click();
  const domainGrid = entry.locator(".timeline-domain-grid");
  await domainGrid.getByText("Task entered blocked: Restore customer export", { exact: true }).waitFor({
    state: "visible",
  });
  await domainGrid.getByText("Grace Lead · Project Lead", { exact: true }).waitFor({
    state: "visible",
  });
  await domainGrid.getByText("Restore customer export", { exact: true }).waitFor({
    state: "visible",
  });
  await domainGrid.getByText("Alpha Team · Alpha Project", { exact: true }).waitFor({
    state: "visible",
  });
  await entry.getByRole("heading", { name: "Change · State", exact: true }).waitFor({
    state: "visible",
  });
  await entry.getByText("Earlier value not retained", { exact: true }).waitFor({
    state: "visible",
  });
  await entry.getByText("Blocked", { exact: true }).last().waitFor({
    state: "visible",
  });
  await entry.getByText("Reason: Upstream schema verification failed.", { exact: true }).waitFor({
    state: "visible",
  });
  assert.equal(await technicalDetails.evaluate((element) => element.open), false);

  await technicalDetails.locator("summary").click();
  const copyTaskId = technicalDetails.getByRole("button", {
    name: "Copy ID: task-pending",
  });
  await copyTaskId.click();
  await copyTaskId.getByText("Copied", { exact: true }).waitFor({ state: "visible" });
  assert.equal(await page.evaluate(() => navigator.clipboard.readText()), "task-pending");
  const expandedText = await entry.innerText();
  assert.equal(expandedText.match(/task-pending/g)?.length, 1);
});

test("team add-on timeline keeps naive timestamps in server UTC order", async (t) => {
  const timelinePayload = {
    revision: 1,
    derived: true,
    audit_log: false,
    description:
      "Activity reconstructed from records retained in the current Team snapshot; this is not an audit log.",
    filters: {},
    timestampless_count: 0,
    entries: [
      timelineEntry({
        id: "review:aware-newer:decision",
        type: "review",
        category: "decision",
        timestamp: "2026-01-01T12:40:00Z",
        timestamp_source: "decision_at",
        status: "accepted",
        attention_needed: false,
        event: "Aware UTC event",
        summary: "Aware UTC event",
      }),
      timelineEntry({
        id: "review:naive-older:created",
        type: "review",
        category: "created",
        timestamp: "2026-01-01T12:30:00",
        timestamp_source: "created_at",
        status: "pending",
        attention_needed: false,
        event: "Naive UTC event",
        summary: "Naive UTC event",
      }),
    ],
  };
  const { port } = await createTeamAddonServer(t, { timelinePayload });
  const page = await openBrowserPage(t, port, { timezoneId: "America/Los_Angeles" });

  await page.getByRole("tab", { name: "Activity timeline", exact: true }).click();
  await page.locator("#team-timeline-page .timeline-entry").first().waitFor({
    state: "visible",
  });

  const summaries = await page
    .locator("#team-timeline-page .timeline-summary-text")
    .allTextContents();
  assert.deepEqual(summaries.slice(0, 2), ["Aware UTC event", "Naive UTC event"]);
});

test("team add-on task board coordinates hierarchy focus and bounds completed history", async (t) => {
  const initialState = seededGitState();
  initialState.agents["alpha-manager"].display_name = "Ada Manager";
  initialState.agents["alpha-lead"].display_name = "Grace Lead";
  const planned = initialState.tasks["task-planned"];
  const pending = initialState.tasks["task-pending"];
  const activeSibling = initialState.tasks["task-active-later"];
  const waiting = initialState.tasks["task-waiting"];
  const accepted = initialState.tasks["task-accepted"];
  Object.assign(planned, {
    description: [
      "The task board must retain this complete task brief.",
      "It includes the ownership context, acceptance criteria, and handoff notes.",
      "Operators should be able to read every line without leaving the board.",
      "This final line ensures the brief uses the expandable board presentation.",
    ].join("\n"),
    readiness: "waiting_on_children",
    resolution_status: "unresolved",
    integration_ready: false,
    assessment: {
      readiness: "waiting_on_children",
      resolution_status: "unresolved",
      integration_ready: false,
      direct_child_ids: [pending.id, activeSibling.id],
      unresolved_descendant_ids: [pending.id, activeSibling.id, waiting.id],
    },
  });
  Object.assign(pending, {
    parent_task_id: planned.id,
    readiness: "blocked",
    resolution_status: "unresolved",
    integration_ready: false,
    assessment: {
      readiness: "blocked",
      resolution_status: "unresolved",
      integration_ready: false,
      direct_child_ids: [waiting.id],
      unresolved_descendant_ids: [waiting.id],
    },
  });
  initialState.blockers = {
    "blocker-task-pending": {
      id: "blocker-task-pending",
      state: "open",
      source_kind: "task",
      source_id: pending.id,
      task_id: pending.id,
      reason_code: "test_environment_unavailable",
      summary: "The task test environment is unavailable.",
      owner_agent_id: "alpha-lead",
      route: "owned",
      escalation_depth: 0,
    },
  };
  Object.assign(activeSibling, {
    parent_task_id: planned.id,
    owner_agent_id: "alpha-manager",
    readiness: "actionable",
  });
  Object.assign(waiting, {
    parent_task_id: pending.id,
    readiness: "waiting_for_review",
    assessment: {
      readiness: "waiting_for_review",
      resolution_status: "unresolved",
      integration_ready: false,
      direct_child_ids: [accepted.id],
      unresolved_descendant_ids: [],
    },
  });
  Object.assign(accepted, {
    parent_task_id: waiting.id,
    readiness: "resolved",
    resolution_status: "resolved",
    integration_ready: true,
    assessment: {
      readiness: "resolved",
      resolution_status: "resolved",
      integration_ready: true,
      direct_child_ids: [],
      unresolved_descendant_ids: [],
    },
  });
  initialState.tasks["task-independent"] = {
    id: "task-independent",
    owner_agent_id: "alpha-lead",
    project_id: "alpha-project",
    priority: 9,
    state: "pending",
    state_entered_at: "2026-06-10T08:00:00Z",
    title: "Independent active root",
    description: "A separate hierarchy waiting for prerequisite work.",
    created_by_agent_id: "alpha-manager",
    dependency_task_ids: [activeSibling.id],
    dependency_status: "waiting",
    blocked_by_task_ids: [activeSibling.id],
    readiness: "waiting_on_dependencies",
    resolution_status: "unresolved",
    assessment: {
      readiness: "waiting_on_dependencies",
      resolution_status: "unresolved",
      unsatisfied_dependency_ids: [activeSibling.id],
      direct_child_ids: [],
      unresolved_descendant_ids: [],
    },
    created_at: "2026-06-10T08:00:00Z",
    updated_at: "2026-06-10T08:00:00Z",
  };
  const terminalStates = ["finished", "accepted", "rejected", "cancelled"];
  for (let index = 1; index <= 24; index += 1) {
    const suffix = String(index).padStart(2, "0");
    const completedDay = index <= 12 ? "10" : "12";
    const taskId = `task-history-${suffix}`;
    initialState.tasks[taskId] = {
      id: taskId,
      owner_agent_id: index % 2 ? "alpha-lead" : "alpha-manager",
      project_id: "alpha-project",
      priority: 20 + index,
      state: terminalStates[(index - 1) % terminalStates.length],
      state_entered_at: `2026-06-${completedDay}T12:00:00Z`,
      title: `Historical task ${suffix}`,
      description: `Durable completed history ${suffix}.`,
      created_by_agent_id: "alpha-manager",
      readiness: "resolved",
      resolution_status: "resolved",
      integration_ready: true,
      cancellation:
        terminalStates[(index - 1) % terminalStates.length] === "cancelled"
          ? {
              reason_code: "superseded",
              reason: "Replaced by later work.",
              actor_id: "alpha-manager",
              cancelled_at: `2026-06-${completedDay}T12:00:00Z`,
            }
          : null,
      created_at: `2026-06-${completedDay}T08:00:00Z`,
      updated_at: `2026-06-${completedDay}T12:00:00Z`,
    };
  }

  const { port, requests } = await createTeamAddonServer(t, { initialState });
  const page = await openBrowserPage(t, port);

  await waitForRevision(page, 4);
  await page.getByRole("tab", { name: "Task board" }).click();
  await page.locator("#task-board-team-select").selectOption("team-alpha");
  await page.locator("#task-board-project-select").selectOption("alpha-project");
  const boardPanel = page.locator("#team-task-board-page");
  await boardPanel.getByRole("tab", { name: "Board" }).waitFor({ state: "visible" });
  assert.equal(
    await boardPanel.getByRole("tab", { name: "Board" }).getAttribute("aria-selected"),
    "true",
  );
  await boardPanel.getByRole("checkbox", { name: "Show completed (25)" }).waitFor({ state: "visible" });
  assert.equal(await boardPanel.locator(".task-board-lane-done").count(), 0);
  assert.equal(await boardPanel.locator(".task-board-lane-done .task-board-card").count(), 0);
  await boardPanel
    .locator(".task-board-context")
    .getByText(/1 waiting on dependencies/)
    .waitFor({ state: "visible" });
  const dependencyWaitCard = boardPanel.locator(
    '.task-board-card[data-task-board-task-id="task-independent"]',
  );
  await dependencyWaitCard.getByText("Waiting on 1 dependency", { exact: true }).waitFor({
    state: "visible",
  });
  await dependencyWaitCard.getByText("Waiting on dependencies:", { exact: true }).waitFor({
    state: "visible",
  });
  assert.equal(
    await dependencyWaitCard.evaluate((element) => element.classList.contains("task-board-card-blocked")),
    false,
  );
  assert.equal(await dependencyWaitCard.getByText("Blocked", { exact: true }).count(), 0);

  await boardPanel.getByRole("checkbox", { name: "Show completed (25)" }).check();
  assert.equal(await boardPanel.locator(".task-board-lane-done .task-board-card").count(), 20);
  await boardPanel
    .locator(".task-board-lane-done")
    .getByText("Showing 1–20 of 25")
    .waitFor({ state: "visible" });
  await boardPanel.locator(".task-board-lane-done").getByRole("button", { name: "Next" }).click();
  assert.equal(await boardPanel.locator(".task-board-lane-done .task-board-card").count(), 5);
  await boardPanel
    .locator(".task-board-lane-done")
    .getByText("Showing 21–25 of 25")
    .waitFor({ state: "visible" });

  const searchForm = boardPanel.locator("[data-task-board-search-form]");
  await searchForm.locator('[name="task_board_query"]').fill("Historical task 24");
  await searchForm.getByRole("button", { name: "Search" }).click();
  assert.equal(await boardPanel.locator(".task-board-lane-done .task-board-card").count(), 1);
  await boardPanel.getByRole("button", { name: "Historical task 24" }).waitFor({
    state: "visible",
  });

  await boardPanel.getByRole("button", { name: "Clear filters" }).click();
  await boardPanel
    .locator('[data-task-board-filter="assignee"]')
    .selectOption("alpha-manager");
  await boardPanel.getByRole("button", { name: "Review new runtime signal" }).waitFor({
    state: "visible",
  });
  assert.equal(
    await boardPanel.getByRole("button", { name: "Draft pending delivery" }).count(),
    0,
  );

  await boardPanel.getByRole("button", { name: "Clear filters" }).click();
  await boardPanel.locator('[data-task-board-filter="readiness"]').selectOption("blocked");
  await boardPanel.locator('[data-task-board-primary-task-id="task-pending"]').waitFor({
    state: "visible",
  });
  assert.equal(await boardPanel.getByRole("button", { name: "Plan alpha delivery" }).count(), 1);

  await boardPanel.getByRole("button", { name: "Clear filters" }).click();
  await boardPanel.locator('[data-task-board-filter="root"]').selectOption("task-planned");
  await boardPanel.locator('[data-task-board-primary-task-id="task-planned"]').waitFor({
    state: "visible",
  });
  assert.equal(await boardPanel.getByRole("button", { name: "Independent active root" }).count(), 0);

  await boardPanel.getByRole("button", { name: "Clear filters" }).click();
  await boardPanel.locator('[data-task-board-filter="state"]').selectOption("accepted");
  await boardPanel.locator('[data-task-board-primary-task-id="task-accepted"]').waitFor({
    state: "visible",
  });
  assert.equal(
    await boardPanel.locator(".task-board-card .state-pill", { hasText: "Accepted" }).count() > 0,
    true,
  );

  await boardPanel.getByRole("button", { name: "Clear filters" }).click();
  await boardPanel.getByRole("checkbox", { name: "Show completed (25)" }).check();
  await boardPanel
    .locator('[data-task-board-filter="completed-since"]')
    .fill("2026-06-12");
  await boardPanel
    .locator('[data-task-board-filter="completed-through"]')
    .fill("2026-06-12");
  assert.equal(await boardPanel.locator(".task-board-lane-done .task-board-card").count(), 12);
  assert.equal(await boardPanel.getByRole("button", { name: "Ship accepted delivery" }).count(), 0);

  await boardPanel.getByRole("button", { name: "Clear filters" }).click();
  await boardPanel.getByRole("tab", { name: "Hierarchy" }).click();
  assert.equal(
    await boardPanel.getByRole("tab", { name: "Hierarchy" }).getAttribute("aria-selected"),
    "true",
  );
  const plannedNode = boardPanel.locator(
    '.task-board-hierarchy-branch[data-task-board-task-id="task-planned"]',
  );
  const hierarchyBrief = plannedNode.locator(".task-board-task-brief");
  await hierarchyBrief.locator("summary").click();
  const hierarchyBriefContent = hierarchyBrief.locator(".task-board-task-brief-content");
  await hierarchyBriefContent.waitFor({ state: "visible" });
  assert.match(
    await hierarchyBriefContent.textContent(),
    /Operators should be able to read every line without leaving the board\./,
  );
  await plannedNode.getByRole("button", { name: "Plan alpha delivery", exact: true }).click();
  assert.equal(
    await plannedNode
      .getByRole("button", { name: /Collapse children of Plan alpha delivery/ })
      .getAttribute("aria-expanded"),
    "true",
  );
  assert.equal(
    await plannedNode.locator(
      ':scope > .task-board-hierarchy-children > [data-task-board-task-id="task-pending"].task-board-task-direct-child',
    ).count(),
    1,
  );
  assert.equal(
    await plannedNode
      .locator(":scope > .task-board-hierarchy-children")
      .evaluate((element) => getComputedStyle(element).borderLeftStyle),
    "solid",
  );
  await plannedNode
    .getByRole("button", { name: /Collapse children of Plan alpha delivery/ })
    .click();
  assert.equal(
    await plannedNode.locator(
      ':scope > .task-board-hierarchy-children > [data-task-board-task-id="task-pending"]',
    ).count(),
    0,
  );
  await plannedNode
    .getByRole("button", { name: /Expand children of Plan alpha delivery/ })
    .click();

  const pendingNode = boardPanel.locator(
    '.task-board-hierarchy-branch[data-task-board-task-id="task-pending"]',
  );
  await pendingNode.getByRole("button", { name: /Expand children of Draft pending delivery/ }).click();
  const waitingNode = boardPanel.locator(
    '.task-board-hierarchy-branch[data-task-board-task-id="task-waiting"]',
  );
  await waitingNode.getByRole("button", { name: /Expand children of Await review/ }).click();
  const acceptedNode = boardPanel.locator(
    '.task-board-hierarchy-branch[data-task-board-task-id="task-accepted"]',
  );
  await acceptedNode.getByRole("button", { name: "Ship accepted delivery", exact: true }).waitFor({
    state: "visible",
  });
  const pendingNodeCard = pendingNode.locator(":scope > .task-board-hierarchy-node");
  await pendingNodeCard.getByText("Readiness: Blocked").waitFor({ state: "visible" });
  await pendingNodeCard.getByText("Assignee: Grace Lead").waitFor({ state: "visible" });
  await pendingNodeCard.getByText(/1 assigned blocker/).waitFor({ state: "visible" });
  await pendingNodeCard.getByText("Assigned blocker:", { exact: true }).waitFor({
    state: "visible",
  });

  await acceptedNode.getByRole("button", { name: "Ship accepted delivery", exact: true }).click();
  await boardPanel.getByRole("tab", { name: "Board" }).click();
  const acceptedCard = boardPanel.locator(
    '.task-board-card[data-task-board-task-id="task-accepted"]',
  );
  await acceptedCard.waitFor({ state: "visible" });
  assert.equal(await acceptedCard.evaluate((element) => element.classList.contains("task-board-task-selected")), true);

  const pendingCard = boardPanel.locator(
    '.task-board-card[data-task-board-task-id="task-pending"]',
  );
  await pendingCard.getByRole("button", { name: "Plan alpha delivery" }).click();
  const plannedCard = boardPanel.locator(
    '.task-board-card[data-task-board-task-id="task-planned"]',
  );
  assert.equal(await plannedCard.evaluate((element) => element.classList.contains("task-board-task-selected")), true);
  const cardBrief = plannedCard.locator(".task-board-task-brief");
  const cardBriefContent = cardBrief.locator(".task-board-task-brief-content");
  await cardBriefContent.waitFor({ state: "visible" });
  assert.match(
    await cardBriefContent.textContent(),
    /This final line ensures the brief uses the expandable board presentation\./,
  );
  assert.equal(await pendingCard.evaluate((element) => element.classList.contains("task-board-task-direct-child")), true);

  await boardPanel.locator("details.task-board-create > summary").click();
  const createTaskForm = boardPanel.locator("[data-task-board-create-form]");
  await createTaskForm.locator('[name="owner_agent_id"]').selectOption("alpha-lead");
  await createTaskForm.locator('[name="title"]').fill("Feature workstream task");
  await createTaskForm.locator('[name="description"]').fill("Keep this task on feature/demo.");
  assert.deepEqual(
    await createTaskForm.locator('[name="task_kind"] option').evaluateAll(
      (options) => options.map((option) => option.value),
    ),
    ["support", "coordination"],
  );
  await createTaskForm.locator('[name="task_kind"]').selectOption("coordination");
  assert.equal(await createTaskForm.locator('[name="require_git_provenance"]').isDisabled(), true);
  await createTaskForm.locator('[name="task_kind"]').selectOption("support");
  assert.equal(await createTaskForm.locator('[name="require_git_provenance"]').isEnabled(), true);
  await createTaskForm.locator('[name="task_author_name"]').fill("Alice Example");
  await createTaskForm.locator('[name="task_author_email"]').fill("alice@example.test");
  await createTaskForm.locator('[name="git_base_branch"]').fill("feature/demo");
  await createTaskForm.locator('[name="parent_task_id"]').selectOption("task-planned");
  await createTaskForm.locator('[name="originating_task_id"]').selectOption("task-open");
  await createTaskForm.locator('[name="require_git_provenance"]').check();
  await createTaskForm.getByRole("button", { name: "Create task" }).click();
  await waitForCondition(
    () => requests.some((request) => (
      request.method === "POST"
      && request.pathname === "/api/addons/team-addon/tasks"
      && request.payload?.title === "Feature workstream task"
    )),
    "expected task-board create request",
  );
  const createRequest = requests.find((request) => (
    request.method === "POST"
    && request.pathname === "/api/addons/team-addon/tasks"
    && request.payload?.title === "Feature workstream task"
  ));
  assert.equal(createRequest.payload.git_base_branch, "feature/demo");
  assert.equal(createRequest.payload.task_kind, "support");
  assert.deepEqual(createRequest.payload.authored_by, { name: "Alice Example", email: "alice@example.test" });
  assert.equal(createRequest.payload.parent_task_id, "task-planned");
  assert.equal(createRequest.payload.originating_task_id, "task-open");
  assert.equal(createRequest.payload.integration_boundary, false);
  assert.deepEqual(createRequest.payload.deliverable_contract, {
    require_git_provenance: true,
    required_contribution_task_ids: [],
  });
});

test("team add-on renders TaskAssessment resolution reasons as operator prose", async (t) => {
  const initialState = seededGitState();
  const reasonFixtures = [
    {
      taskId: "task-planned",
      readiness: "actionable",
      resolution: "unresolved",
      reason: "task_actionable",
      expected: "The task is ready for its next action.",
    },
    {
      taskId: "task-pending",
      readiness: "waiting_on_children",
      resolution: "unresolved",
      reason: "unresolved_descendants",
      expected: "One or more descendant tasks are still unresolved.",
      unresolvedDescendantIds: ["task-waiting"],
    },
    {
      taskId: "task-waiting",
      readiness: "waiting_for_review",
      resolution: "unresolved",
      reason: "active_formal_review",
      expected: "A formal review is still in progress.",
    },
    {
      taskId: "task-blocked",
      readiness: "blocked",
      resolution: "unresolved",
      reason: "intrinsic_blocker_open",
      expected: "An active blocker is preventing this task from progressing.",
    },
    {
      taskId: "task-active-later",
      readiness: "waiting_on_dependencies",
      resolution: "unresolved",
      reason: "future_operator_state",
      expected: "Future operator state.",
    },
    {
      taskId: "task-rejected",
      readiness: "invalid",
      resolution: "invalid",
      reason:
        "invalid:terminal_task_with_active_review,active_deliverable_without_acceptance_path",
      expected:
        "Lifecycle data needs repair: Terminal task with active review; Active deliverable without acceptance path.",
    },
  ];
  reasonFixtures.forEach((fixture) => {
    const task = initialState.tasks[fixture.taskId];
    Object.assign(task, {
      readiness: fixture.readiness,
      resolution_status: fixture.resolution,
      integration_ready: false,
      assessment: {
        readiness: fixture.readiness,
        resolution_status: fixture.resolution,
        resolution_reason: fixture.reason,
        integration_ready: false,
        direct_child_ids: [],
        unresolved_descendant_ids: fixture.unresolvedDescendantIds || [],
      },
    });
  });

  const { port } = await createTeamAddonServer(t, { initialState });
  const page = await openBrowserPage(t, port);

  await waitForRevision(page, 4);
  await page.getByRole("tab", { name: "Planning" }).click();
  for (const projectName of ["Alpha Project", "Beta Project"]) {
    const projectCard = page.locator("#team-planning-page .planning-project-card", {
      hasText: projectName,
    });
    await projectCard.locator(":scope > details > summary").click();
  }
  for (const fixture of reasonFixtures) {
    const taskCard = page.locator(
      `#team-planning-page [data-planning-task-id="${fixture.taskId}"]`,
    );
    await taskCard.waitFor({ state: "visible" });
    assert.equal(
      await taskCard.locator(".planning-task-reason-preview").innerText(),
      fixture.expected,
    );
    assert.equal((await taskCard.textContent()).includes(fixture.reason), false);
  }

  await page.getByRole("tab", { name: "Teams" }).click();
  const alphaGroup = page.locator(
    '#team-people-page [data-team-record-group-id="team-alpha"]',
  );
  await alphaGroup.locator(".team-record-group-header").click();
  await alphaGroup.locator('[data-team-record-tab="project"]').click();
  await alphaGroup
    .locator('[data-project-id="alpha-project"]')
    .getByRole("button")
    .click();
  const taskGraphItem = page
    .locator("#people-agent-detail-view .task-item")
    .filter({ hasText: "Plan alpha delivery" });
  await taskGraphItem.locator("summary").click();
  await taskGraphItem
    .getByText("Lifecycle reason: The task is ready for its next action.", {
      exact: true,
    })
    .waitFor({ state: "visible" });
  assert.equal((await taskGraphItem.textContent()).includes("task_actionable"), false);
});

test("team add-on task surfaces preserve hierarchy, lifecycle, blockers, and worktree identity", async (t) => {
  const initialState = seededGitState();
  const parent = initialState.tasks["task-planned"];
  const child = initialState.tasks["task-pending"];
  Object.assign(parent, {
    child_task_ids: [child.id],
    readiness: "blocked",
    resolution_status: "unresolved",
    integration_ready: false,
    assessment: {
      readiness: "blocked",
      resolution_status: "unresolved",
      integration_ready: false,
      direct_child_ids: [child.id],
      unresolved_descendant_ids: [child.id],
      resolution_reason: "A direct child still requires acceptance.",
    },
  });
  Object.assign(child, {
    parent_task_id: parent.id,
    originating_task_id: "task-waiting",
    dependency_task_ids: ["task-waiting"],
    dependency_status: "blocked",
    blocked_by_task_ids: ["task-waiting"],
    readiness: "blocked",
    resolution_status: "unresolved",
    integration_ready: false,
    invalid_reason_codes: ["legacy_missing_parent_acceptance"],
    inherited_blockers: [
      {
        upstream_task_id: "task-waiting",
        relationship: "dependency",
        blocker_id: "blocker-upstream-contract",
        reason_code: "upstream_contract_unsigned",
        reason: "The upstream runtime contract is not signed.",
        summary: "The upstream runtime contract is not signed.",
        details: "The waiting task owner needs the signed contract before proceeding.",
        source_kind: "task",
        source_id: "task-waiting",
        blocker_task_id: "task-waiting",
        owner_agent_id: "alpha-manager",
        delegate_agent_id: null,
        route: "owned",
        escalation_depth: 0,
        priority: "high",
        created_at: "2026-06-09T07:30:00Z",
        follow_up_at: "2026-06-10T07:30:00Z",
        last_progress_at: null,
      },
    ],
    assessment: {
      readiness: "blocked",
      resolution_status: "unresolved",
      integration_ready: false,
      direct_child_ids: [],
      unresolved_descendant_ids: ["task-waiting"],
      resolution_reason: "The prerequisite task has not resolved.",
      active_acceptance_ids: ["acceptance-child"],
      active_review_request_ids: ["review-waiting"],
      contribution_coverage: {
        status: "incomplete",
        missing_source_deliverable_ids: ["deliverable-child"],
        unexpected_source_deliverable_ids: [],
      },
    },
  });
  initialState.blockers = {
    "blocker-task-pending": {
      id: "blocker-task-pending",
      state: "open",
      source_kind: "task",
      source_id: child.id,
      task_id: child.id,
      reason_code: "external_dependency",
      summary: "Waiting for the signed runtime contract.",
      details: "The owner cannot complete integration until the contract is available.",
      owner_agent_id: "alpha-lead",
      delegate_agent_id: "alpha-manager",
      route: "manager",
      escalation_depth: 1,
      created_at: "2026-06-09T08:00:00Z",
      follow_up_at: "2026-06-10T08:00:00Z",
      last_progress_at: "2026-06-09T09:00:00Z",
    },
  };
  initialState.blocker_events = {
    "blocker-event-task-pending": {
      id: "blocker-event-task-pending",
      blocker_id: "blocker-task-pending",
      event_type: "progress",
      note: "Owner contacted the contract approver.",
      created_at: "2026-06-09T09:00:00Z",
    },
  };
  initialState.git_work_contexts = {
    "ctx-task-pending": {
      id: "ctx-task-pending",
      branch_context_id: "ctx-task-pending",
      task_id: child.id,
      project_id: child.project_id,
      worktree_path: "/home/agent/worktrees/task-pending",
      agent_branch: "agents/session-alpha-lead/task-pending",
      dispatch_id: "dispatch-task-pending",
      state: "active",
    },
  };
  initialState.dispatches = {
    "dispatch-task-pending": {
      id: "dispatch-task-pending",
      task_id: child.id,
      status: "leased",
    },
  };
  initialState.worktree_cleanup_candidates = {
    "cleanup-task-pending": {
      id: "cleanup-task-pending",
      task_id: child.id,
      state: "recovery_required",
      worktree_path: "/home/agent/worktrees/task-pending",
      branch: "agents/session-alpha-lead/task-pending",
      last_safety_check: "tracked_changes",
      last_error: "Tracked changes still exist.",
      updated_at: "2026-06-09T10:00:00Z",
    },
  };

  const { port } = await createTeamAddonServer(t, { initialState });
  const page = await openBrowserPage(t, port);

  await waitForRevision(page, 4);
  await page.getByRole("tab", { name: "Teams" }).click();
  const alphaGroup = page.locator(
    '#team-people-page [data-team-record-group-id="team-alpha"]',
  );
  await alphaGroup.locator(".team-record-group-header").click();
  await alphaGroup.locator('[data-team-record-tab="project"]').click();
  await alphaGroup
    .locator('[data-project-id="alpha-project"]')
    .getByRole("button")
    .click();
  const taskItems = page.locator("#people-agent-detail-view .task-item");
  await taskItems.first().waitFor({ state: "visible" });
  const orderedTaskTitles = await taskItems
    .locator("summary .compact-record-main strong")
    .allTextContents();
  assert.equal(
    orderedTaskTitles.indexOf("Draft pending delivery"),
    orderedTaskTitles.indexOf("Plan alpha delivery") + 1,
  );

  const childItem = taskItems.filter({ hasText: "Draft pending delivery" });
  await childItem.locator("summary").click();
  await childItem.getByText(/parent task-planned/).waitFor({ state: "visible" });
  await childItem.getByText(/originating task task-waiting/).waitFor({ state: "visible" });
  await childItem.getByText("Direct acceptance: acceptance-child").waitFor({
    state: "visible",
  });
  await childItem.getByText("Formal review: review-waiting").waitFor({ state: "visible" });
  await childItem
    .getByText("Contribution composition: coverage incomplete - missing deliverable-child")
    .waitFor({ state: "visible" });
  await childItem
    .getByText("Blocker blocker-task-pending", { exact: true })
    .waitFor({ state: "visible" });
  await childItem
    .getByText("Upstream dependency task-waiting", { exact: true })
    .waitFor({ state: "visible" });
  await childItem
    .getByText("Blocker blocker-upstream-contract", { exact: true })
    .waitFor({ state: "visible" });
  await childItem
    .getByText("The upstream runtime contract is not signed.", { exact: true })
    .waitFor({ state: "visible" });
  await childItem
    .getByText("Latest event progress: Owner contacted the contract approver.")
    .waitFor({ state: "visible" });
  await childItem
    .getByText(
      "Next action: delegate alpha-manager reports progress; owner alpha-lead remains accountable",
    )
    .waitFor({ state: "visible" });
  await childItem.getByText("Legacy lifecycle repair required:").waitFor({ state: "visible" });
  assert.deepEqual(await childItem.locator("table th").allTextContents(), [
    "Task",
    "Project",
    "Repository",
    "Worktree",
    "Branch",
    "Context / dispatch",
  ]);
  await childItem.getByText("/home/agent/worktrees/task-pending", { exact: true }).first().waitFor({
    state: "visible",
  });
  await childItem.getByText(/ctx-task-pending - leased/).waitFor({ state: "visible" });
  await childItem
    .getByText(/cleanup recovery_required - check tracked_changes - Tracked changes still exist\./)
    .waitFor({ state: "visible" });

  await page.getByRole("tab", { name: "Task board" }).click();
  await page.locator("#task-board-team-select").selectOption("team-alpha");
  await page.locator("#task-board-project-select").selectOption("alpha-project");
  const childCard = page.locator('.task-board-card[data-task-board-task-id="task-pending"]');
  await childCard.getByRole("button", { name: "Plan alpha delivery" }).waitFor({ state: "visible" });
  await childCard
    .getByText("Readiness: blocked - resolution: unresolved - integration not ready")
    .waitFor({ state: "visible" });
  await childCard
    .getByText("1 unresolved descendant")
    .waitFor({ state: "visible" });
  await childCard
    .getByText("Worktree: /home/agent/worktrees/task-pending")
    .waitFor({ state: "visible" });
  await childCard.getByText("Waiting on 1 blocked dependency", { exact: true }).waitFor({
    state: "visible",
  });
  const childBlockers = childCard.locator(".task-board-card-blockers");
  await childBlockers.getByText("Waiting on blocked dependencies:", { exact: true }).waitFor({
    state: "visible",
  });
  await childBlockers.getByText("Assigned blocker:", { exact: true }).waitFor({
    state: "visible",
  });
  await childBlockers.getByText("Blocker on dependency:", { exact: true }).waitFor({
    state: "visible",
  });
  await childBlockers.getByRole("button", { name: "Await review" }).first().waitFor({ state: "visible" });
  await childBlockers
    .getByText("The upstream runtime contract is not signed.", { exact: true })
    .waitFor({ state: "visible" });
  assert.equal(await childCard.evaluate((element) => element.classList.contains("task-board-card-blocked")), true);
});

test("team add-on My Human Reviews renders pinned release evidence and submits identity-free decisions", async (t) => {
  const initialState = seededGitState();
  const requirement = {
    reviewer_group_ids: ["release-approvers"],
    checklist: [
      {
        id: "smoke-release",
        label: "Smoke release",
        description: "Exercise the prepared release path.",
        required: true,
      },
    ],
    test_instructions: "Open the preview and complete the release smoke test.",
    preparation_mode: "workflow",
  };
  const humanRequest = {
    id: "human-review-release-human",
    release_package_id: "release-human",
    state: "pending",
    revision: 3,
    candidate_commit_sha: "candidate111",
    candidate_tree_hash: "tree222",
    requirement_snapshot: requirement,
    current_assignment: {
      explicitly_assigned_user_ids: ["admin"],
      eligible_user_ids: ["admin"],
    },
    assignment_generation: 1,
    ready_at: "2026-06-09T16:00:00Z",
    test_target: {
      alias: "release-preview",
      url: "https://preview.example/releases/release-human",
      instructions: "Sign in with the release test account.",
      evidence_refs: ["artifact-space:preview-manifest"],
      expires_at: "2026-06-10T16:00:00Z",
    },
    target_deliverable_options: [
      {
        deliverable_id: "deliverable-accepted",
        task_id: "task-accepted",
        task_title: "Ship accepted delivery",
        producer: {
          agent_id: "alpha-lead",
          display_name: "Alpha Lead",
        },
        summary: "Accepted delivery.",
        is_top_level: true,
      },
      {
        deliverable_id: "deliverable-child-a",
        task_id: "task-child-a",
        task_title: "Implement child contribution A",
        producer: {
          agent_id: "alpha-lead",
          display_name: "Alpha Lead",
        },
        summary: "Child contribution A.",
        is_top_level: false,
      },
    ],
    decisions: [
      {
        id: "human-decision-prior-block",
        decision: "blocked",
        reviewer_user_id: "reviewer@example.com",
        reviewer_display_name_snapshot: "Release Reviewer",
        summary: "Preview credentials were unavailable on the first attempt.",
        evidence_refs: ["artifact-space:prior-decision-log"],
        decided_at: "2026-06-09T15:58:00Z",
      },
    ],
    findings: [
      {
        id: "human-finding-preview-copy",
        state: "open",
        text: "The preview footer must show the pinned candidate SHA.",
        correction_task_id: "task-human-preview-copy",
        target_deliverable_ids: ["deliverable-accepted"],
        evidence_refs: ["artifact-space:finding-screenshot"],
        proposed_resolution: "Render the pinned candidate SHA in the footer.",
        resolution_evidence_refs: ["artifact-space:resolution-preview"],
      },
    ],
    correction_tasks: [
      {
        id: "task-human-preview-copy",
        title: "Correct preview candidate footer",
        state: "in_progress",
        originating_task_id: "task-accepted",
      },
    ],
    created_at: "2026-06-09T15:55:00Z",
  };
  const releaseDetail = {
    id: "release-human",
    project_id: "alpha-project",
    deliverable_ids: ["deliverable-accepted"],
    artifact_refs: ["artifact-space:release-bundle"],
    state: "pending",
    policy_version: 2,
    base_commit_sha: "base000",
    candidate_commit_sha: "candidate111",
    candidate_tree_hash: "tree222",
    approved_commit_sha: null,
    release_branch: "team/alpha/releases/human",
    canonical_branch: "releases/alpha/human",
    published_commit_sha: null,
    published_at: null,
    candidate_checkout: {
      canonical_repository_identity: "azure-devops:example/app/ExampleIndustryAPP",
      repository_display_name: "ExampleIndustryAPP",
      managed_repo_id: "mr_0123456789abcdef0123",
      release_ref: "refs/heads/team/alpha/releases/human",
      candidate_commit_sha: "candidate111",
      candidate_tree_hash: "tree222",
      checkout_command:
        "evander-cli team checkout-release-candidate release-human --target ./release-candidate-release-human --expected-commit-sha candidate111 --runtime-dir /srv/evander/runtime",
    },
    manifest: {
      candidate_commit_sha: "candidate111",
      candidate_tree_hash: "tree222",
      contribution_graph: {
        "deliverable-accepted": ["deliverable-child-a", "deliverable-child-b"],
      },
      deliverables: [
        {
          deliverable_id: "deliverable-accepted",
          feature_branch: "team/alpha/features/accepted",
          feature_commit_sha: "ccc333",
          artifact_refs: ["artifact-space:deliverable-test-notes"],
          verification: { status: "passed" },
          accepted_reviews: [
            {
              review_request_id: "review-accepted",
              reviewer_agent_id: "alpha-manager",
            },
          ],
        },
      ],
      verification_commands: [{ name: "release-smoke" }],
      human_review_requirement: requirement,
      carried_human_findings: [
        {
          finding_id: "human-finding-carried-accessibility",
          text: "Confirm the corrected keyboard focus order.",
          target_deliverable_ids: ["deliverable-accepted"],
          correction_task_id: "task-accessibility-correction",
          proposed_resolution: "Use the corrected focus sequence.",
          resolution_evidence_refs: ["artifact-space:carried-finding-fix"],
          proposed_by_deliverable_ids: ["deliverable-accepted"],
        },
      ],
    },
    verification_runs: [
      {
        id: "verification-release-human-1",
        state: "passed",
        attempt_number: 1,
        candidate_commit_sha: "candidate111",
        command_results: [
          {
            name: "release-smoke",
            state: "passed",
            log_excerpt: "All release checks passed.",
          },
        ],
      },
    ],
    release_review_cohort: {
      id: "release-review-cohort-release-human",
      sealed_at: "2026-06-09T15:40:00Z",
      status: "accepted",
      total: 1,
      resolved: 1,
      pending_review_request_ids: [],
      blocked_review_request_ids: [],
      rejected_review_request_ids: [],
      accepted_review_request_ids: ["agent-release-review-1"],
    },
    release_reviews: [
      {
        id: "agent-release-review-1",
        review_cohort_id: "release-review-cohort-release-human",
        reviewer_agent_id: "alpha-manager",
        state: "accepted",
        instructions: "Review the release manifest.",
        criteria_snapshot: [
          {
            id: "release-scope",
            label: "Release scope",
            required: true,
          },
        ],
        criteria: [
          {
            criterion_id: "release-scope",
            state: "passed",
            finding: "Scope matches the candidate.",
          },
        ],
        findings: [],
      },
    ],
    human_review: {
      required: true,
      state: "pending",
      request_id: humanRequest.id,
      request_count: 1,
      carried_finding_ids: [],
      open_finding_ids: [],
      next_action: "human_decision",
      requests: [humanRequest],
    },
  };
  initialState.human_review_requests = {
    [humanRequest.id]: humanRequest,
  };
  initialState.release_packages = {
    "release-human": {
      id: "release-human",
      project_id: "alpha-project",
      deliverable_ids: ["deliverable-accepted"],
      state: "pending",
      candidate_commit_sha: "candidate111",
      candidate_tree_hash: "tree222",
      human_review: {
        required: true,
        state: "pending",
        request_id: humanRequest.id,
        request_count: 1,
        open_finding_ids: [],
        next_action: "human_decision",
      },
      detail: releaseDetail,
    },
    "release-not-assigned": {
      id: "release-not-assigned",
      project_id: "beta-project",
      deliverable_ids: [],
      state: "pending",
    },
  };

  const {
    port,
    requests,
    state,
    emitStateSnapshot,
    stateStreamCount,
  } = await createTeamAddonServer(t, { initialState });
  const page = await openBrowserPage(
    t,
    port,
    {},
    "/addons/team-addon/?privileged_unlocked=true&view=my-human-reviews&human_review_request_id=human-review-release-human",
  );

  await waitForRevision(page, 4);
  await page.locator("#team-delivery-page").waitFor({ state: "visible" });
  await page.locator('[data-my-human-reviews][aria-pressed="true"]').waitFor({
    state: "visible",
  });
  const releasesView = page.locator("#team-delivery-page [data-releases-view]");
  await releasesView.locator('li.release-card[data-release-id="release-human"]').waitFor({
    state: "visible",
  });
  assert.equal(
    await releasesView.locator('[data-release-id="release-not-assigned"]').count(),
    0,
  );
  const queueRequest = requests.find(
    (request) =>
      request.method === "GET" &&
      request.pathname === "/api/addons/team-addon/human-review-requests",
  );
  assert.equal(new URLSearchParams(queueRequest?.search || "").get("assigned_to"), "me");

  const detail = page.locator("#release-detail-release-human");
  await detail.waitFor({ state: "visible" });
  const focusedRequest = detail.locator("#human-review-human-review-release-human");
  await focusedRequest.waitFor({ state: "visible" });
  assert.equal(
    await focusedRequest.evaluate((element) =>
      element.classList.contains("human-review-request-focused"),
    ),
    true,
  );
  assert.equal(
    await page.evaluate(() => document.activeElement?.id),
    "human-review-human-review-release-human",
  );
  await detail.getByText("Pinned candidate prepared", { exact: true }).waitFor({
    state: "visible",
  });
  const candidateCheckout = detail.locator(".release-candidate-checkout");
  await candidateCheckout.getByRole("heading", { name: "Test this candidate" }).waitFor({
    state: "visible",
  });
  await candidateCheckout.getByText("ExampleIndustryAPP", { exact: true }).waitFor({
    state: "visible",
  });
  await candidateCheckout
    .getByText("refs/heads/team/alpha/releases/human", { exact: true })
    .waitFor({ state: "visible" });
  await candidateCheckout
    .getByRole("button", {
      name: /Copy command: evander-cli team checkout-release-candidate release-human/,
    })
    .waitFor({ state: "visible" });
  await detail.getByText("Alpha Project", { exact: true }).waitFor({
    state: "visible",
  });
  await detail.getByRole("heading", { name: "Release: Ship accepted delivery" }).waitFor({
    state: "visible",
  });
  await detail.getByRole("heading", { name: "Included work evidence" }).waitFor({
    state: "visible",
  });
  const includedWorkSection = detail
    .getByRole("heading", { name: "Included work evidence" })
    .locator("..");
  await includedWorkSection.getByText("Ship accepted delivery", { exact: true }).waitFor({
    state: "visible",
  });
  await includedWorkSection.getByText("Accepted delivery.", { exact: true }).waitFor({
    state: "visible",
  });
  await includedWorkSection
    .getByText("Verification Passed · 1 accepted agent review", { exact: true })
    .waitFor({ state: "visible" });
  const includedWorkTechnicalDetails = includedWorkSection.locator(
    "details.technical-details",
  );
  assert.equal(
    await includedWorkTechnicalDetails
      .getByText("deliverable-accepted", { exact: true })
      .isVisible(),
    false,
  );
  await includedWorkTechnicalDetails.locator("summary").click();
  await includedWorkTechnicalDetails
    .getByText("team/alpha/features/accepted", { exact: true })
    .waitFor({ state: "visible" });
  await includedWorkTechnicalDetails.getByText("review-accepted", { exact: true }).waitFor({
    state: "visible",
  });
  await detail.getByRole("heading", { name: "Contribution lineage" }).waitFor({
    state: "visible",
  });
  await detail.getByText("Direct contribution", { exact: true }).waitFor({
    state: "visible",
  });
  await detail.getByRole("heading", { name: "Automated verification" }).waitFor({
    state: "visible",
  });
  await detail.getByText(/release-smoke: passed - All release checks passed\./).waitFor({
    state: "visible",
  });
  await detail.getByRole("heading", { name: "Agent review decisions" }).waitFor({
    state: "visible",
  });
  await detail
    .getByText(
      /Sealed cohort:\s*Accepted · 1 of 1 resolved\. Correction work starts only after every cohort slot is accepted or rejected\./,
    )
    .waitFor({ state: "visible" });
  await detail.getByText("Release scope: passed - Scope matches the candidate.").waitFor({
    state: "visible",
  });
  await detail.getByText("Review the release manifest.", { exact: true }).waitFor({
    state: "visible",
  });
  await detail.getByRole("heading", { name: "Human request and decision history" }).waitFor({
    state: "visible",
  });
  await detail
    .getByText(
      "Carried findings requiring explicit confirmation: human-finding-carried-accessibility",
      { exact: true },
    )
    .waitFor({ state: "visible" });
  await detail.getByText("https://preview.example/releases/release-human", { exact: true }).waitFor({
    state: "visible",
  });
  await detail.getByText("Open the preview and complete the release smoke test.").waitFor({
    state: "visible",
  });
  await detail.getByText("Sign in with the release test account.", { exact: true }).waitFor({
    state: "visible",
  });
  await detail.getByText("Target evidence: artifact-space:preview-manifest").waitFor({
    state: "visible",
  });
  await detail
    .getByText("Smoke release (required) - Exercise the prepared release path.")
    .waitFor({ state: "visible" });
  await detail.getByText("blocked by Release Reviewer at 2026-06-09T15:58:00Z").waitFor({
    state: "visible",
  });
  await detail
    .getByText("Preview credentials were unavailable on the first attempt.")
    .waitFor({ state: "visible" });
  await detail.getByText("Evidence: artifact-space:prior-decision-log").waitFor({
    state: "visible",
  });
  await detail.getByText("Findings and correction work", { exact: true }).waitFor({
    state: "visible",
  });
  await detail.getByText(/human-finding-preview-copy open - The preview footer must show/).waitFor({
    state: "visible",
  });
  await detail
    .getByText(
      /Correction task task-human-preview-copy · affected work: Ship accepted delivery · by Alpha Lead · included deliverable/,
    )
    .waitFor({ state: "visible" });
  await detail
    .getByText("Resolution proposal: Render the pinned candidate SHA in the footer.")
    .waitFor({ state: "visible" });
  await detail.getByText("Resolution evidence: artifact-space:resolution-preview").waitFor({
    state: "visible",
  });
  await detail
    .getByText(/task-human-preview-copy - Correct preview candidate footer in_progress - origin task-accepted/)
    .waitFor({ state: "visible" });
  await detail.getByRole("heading", { name: "Evidence and attachments" }).waitFor({
    state: "visible",
  });
  await detail.getByText("artifact-space:release-bundle", { exact: true }).waitFor({
    state: "visible",
  });
  for (const evidenceRef of [
    "artifact-space:deliverable-test-notes",
    "artifact-space:finding-screenshot",
    "artifact-space:preview-manifest",
    "artifact-space:prior-decision-log",
    "artifact-space:resolution-preview",
  ]) {
    await detail.getByText(evidenceRef, { exact: true }).last().waitFor({
      state: "visible",
    });
  }

  const decisionForm = detail.locator("[data-human-review-decision-form]");
  const findingEditor = decisionForm.locator("[data-human-review-findings-editor]");
  const findingRows = findingEditor.locator("[data-human-review-finding-row]");
  assert.equal(
    await findingRows.count(),
    0,
    "the flat findings editor should begin empty until the reviewer explicitly adds one",
  );
  await findingEditor.getByRole("button", { name: "Add finding" }).click();
  assert.equal(
    await findingRows
      .nth(0)
      .locator('textarea[name="human_finding_target_deliverable_ids"]')
      .count(),
    0,
    "reviewers should never need to enter opaque deliverable IDs",
  );
  await findingRows
    .nth(0)
    .getByText(
      "Ship accepted delivery · by Alpha Lead · included deliverable",
      { exact: true },
    )
    .waitFor({ state: "visible" });
  await findingRows
    .nth(0)
    .getByText(
      "Implement child contribution A · by Alpha Lead · contribution",
      { exact: true },
    )
    .waitFor({ state: "visible" });
  const allTargetOptions = humanRequest.target_deliverable_options;
  state.human_review_requests[humanRequest.id].target_deliverable_options = [
    allTargetOptions[0],
  ];
  state.release_packages["release-human"].detail.human_review.requests[0]
    .target_deliverable_options = [allTargetOptions[0]];
  state.revision += 1;
  await page.reload({ waitUntil: "domcontentloaded" });
  await waitForRevision(page, state.revision);
  await findingEditor.getByRole("button", { name: "Add finding" }).click();
  assert.equal(
    await findingRows.nth(0).locator('[role="group"][aria-label="Affected work"]').count(),
    0,
    "a sole candidate deliverable should be inferred rather than shown as a selector",
  );
  await findingRows.nth(0).getByRole("button", { name: "Remove finding 1" }).click();
  state.human_review_requests[humanRequest.id].target_deliverable_options =
    allTargetOptions;
  state.release_packages["release-human"].detail.human_review.requests[0]
    .target_deliverable_options = allTargetOptions;
  state.revision += 1;
  await page.reload({ waitUntil: "domcontentloaded" });
  await waitForRevision(page, state.revision);
  await findingEditor.getByRole("button", { name: "Add finding" }).click();
  await findingRows
    .nth(0)
    .locator('[role="group"][aria-label="Affected work"]')
    .waitFor({ state: "visible" });
  await findingRows.nth(0).getByRole("button", { name: "Remove finding 1" }).click();
  assert.equal(await findingRows.count(), 0);
  const decisionEndpoint =
    "**/api/addons/team-addon/human-review-requests/human-review-release-human/decisions";
  let findingsDecisionPayload = null;
  await page.route(decisionEndpoint, async (route) => {
    findingsDecisionPayload = route.request().postDataJSON();
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        revision: state.revision,
        result: {},
        human_review_request: humanRequest,
      }),
    });
  });
  await findingEditor.getByRole("button", { name: "Add finding" }).click();
  await findingRows
    .nth(0)
    .locator('textarea[name="human_finding_evidence_refs"]')
    .fill("artifact-space:metadata-only");
  await decisionForm.getByRole("button", { name: "Request changes" }).click();
  assert.equal(
    findingsDecisionPayload,
    null,
    "a metadata-only finding must not be submitted",
  );
  assert.equal(
    await findingRows
      .nth(0)
      .locator('textarea[name="human_finding_text"]')
      .evaluate((field) => field.validationMessage),
    "Describe the finding before attaching targets or evidence.",
  );
  await findingRows
    .nth(0)
    .locator('textarea[name="human_finding_text"]')
    .fill("[P1] Keep this finding intact, including its explanatory comma.");
  assert.equal(
    await findingRows
      .nth(0)
      .locator('textarea[name="human_finding_text"]')
      .evaluate((field) => field.validationMessage),
    "",
    "editing a finding should clear its stale validation error",
  );
  await findingRows
    .nth(0)
    .locator('textarea[name="human_finding_evidence_refs"]')
    .fill("artifact-space:finding-one");
  await findingEditor.getByRole("button", { name: "Add finding" }).click();
  await decisionForm.getByRole("button", { name: "Request changes" }).click();
  assert.equal(
    findingsDecisionPayload,
    null,
    "an explicitly added empty finding must not be silently discarded",
  );
  assert.equal(
    await findingRows
      .nth(1)
      .locator('textarea[name="human_finding_text"]')
      .evaluate((field) => field.validationMessage),
    "Describe this finding or remove the empty row.",
  );
  await findingRows
    .nth(1)
    .locator('textarea[name="human_finding_text"]')
    .fill("[P2] Keep a second finding intact, too.");
  await findingRows
    .nth(1)
    .locator(
      'input[name="human_finding_target_deliverable_ids"][value="deliverable-child-a"]',
    )
    .check();
  await findingRows
    .nth(1)
    .locator('textarea[name="human_finding_evidence_refs"]')
    .fill("artifact-space:finding-two");
  await decisionForm.getByRole("button", { name: "Request changes" }).click();
  await waitForCondition(
    () => findingsDecisionPayload !== null,
    "expected an intercepted changes-requested decision",
  );
  assert.deepEqual(
    findingsDecisionPayload.findings,
    [
      {
        text: "[P1] Keep this finding intact, including its explanatory comma.",
        target_deliverable_ids: [],
        evidence_refs: ["artifact-space:finding-one"],
      },
      {
        text: "[P2] Keep a second finding intact, too.",
        target_deliverable_ids: ["deliverable-child-a"],
        evidence_refs: ["artifact-space:finding-two"],
      },
    ],
    "each explicit finding row should retain its own prose, targets, and evidence",
  );
  await page.getByText("Human review changes requested recorded.").waitFor({
    state: "visible",
  });
  await page.unroute(decisionEndpoint);
  await decisionForm.waitFor({ state: "visible" });
  await decisionForm.locator('[data-human-checklist-id="smoke-release"]').check();
  const carriedFindingConfirmation = decisionForm.locator(
    '[data-confirm-finding-id="human-finding-carried-accessibility"]',
  );
  const carriedFindingConfirmationBox = await carriedFindingConfirmation.boundingBox();
  assert.ok(
    carriedFindingConfirmationBox?.width >= 20 && carriedFindingConfirmationBox?.height >= 20,
    "carried-finding confirmation checkbox should have a visible 20px target",
  );
  await carriedFindingConfirmation.check();
  await decisionForm
    .locator('textarea[name="summary"]')
    .fill("Smoke test passed against the pinned preview.");
  await decisionForm
    .locator('textarea[name="evidence_refs"]')
    .fill("artifact-space:human-smoke-evidence");
  await findingEditor.getByRole("button", { name: "Add finding" }).click();
  await findingRows
    .nth(0)
    .locator('textarea[name="human_finding_text"]')
    .fill("Pinned preview footer does not show the release identifier.");
  await findingRows
    .nth(0)
    .locator(
      'input[name="human_finding_target_deliverable_ids"][value="deliverable-accepted"]',
    )
    .check();
  await findingRows
    .nth(0)
    .locator('textarea[name="human_finding_evidence_refs"]')
    .fill("artifact-space:footer-screenshot");
  await findingEditor.getByRole("button", { name: "Add finding" }).click();
  await findingRows
    .nth(1)
    .locator('textarea[name="human_finding_text"]')
    .fill("Secondary finding survives refresh.");
  await findingRows
    .nth(1)
    .locator(
      'input[name="human_finding_target_deliverable_ids"][value="deliverable-child-a"]',
    )
    .check();
  await findingRows
    .nth(1)
    .locator('textarea[name="human_finding_evidence_refs"]')
    .fill("artifact-space:secondary-evidence,\nartifact-space:follow-up");
  await findingRows
    .nth(1)
    .locator('textarea[name="human_finding_text"]')
    .evaluate((field) => {
    field.focus();
      field.setSelectionRange(10, 17);
    });
  await waitForCondition(
    () => stateStreamCount() > 0,
    "expected Team state stream to be open before refreshing the human-review draft",
  );
  state.revision += 1;
  assert.ok(
    emitStateSnapshot({ response_scope: "revision", revision: state.revision }) > 0,
    "expected the human-review draft refresh to reach the Team client",
  );
  await waitForRevision(page, state.revision);

  assert.equal(
    await decisionForm.locator('textarea[name="summary"]').inputValue(),
    "Smoke test passed against the pinned preview.",
  );
  assert.equal(
    await decisionForm.locator('textarea[name="evidence_refs"]').inputValue(),
    "artifact-space:human-smoke-evidence",
  );
  assert.equal(
    await findingRows.nth(0).locator('textarea[name="human_finding_text"]').inputValue(),
    "Pinned preview footer does not show the release identifier.",
  );
  assert.equal(
    await findingRows
      .nth(0)
      .locator(
        'input[name="human_finding_target_deliverable_ids"][value="deliverable-accepted"]',
      )
      .isChecked(),
    true,
  );
  assert.equal(
    await findingRows
      .nth(0)
      .locator('textarea[name="human_finding_evidence_refs"]')
      .inputValue(),
    "artifact-space:footer-screenshot",
  );
  assert.equal(await findingRows.count(), 2);
  assert.equal(
    await findingRows.nth(1).locator('textarea[name="human_finding_text"]').inputValue(),
    "Secondary finding survives refresh.",
  );
  assert.equal(
    await findingRows
      .nth(1)
      .locator(
        'input[name="human_finding_target_deliverable_ids"][value="deliverable-child-a"]',
      )
      .isChecked(),
    true,
  );
  assert.equal(
    await findingRows
      .nth(1)
      .locator('textarea[name="human_finding_evidence_refs"]')
      .inputValue(),
    "artifact-space:secondary-evidence,\nartifact-space:follow-up",
    "draft refresh should not normalize unfinished list punctuation",
  );
  assert.equal(
    await decisionForm.locator('[data-human-checklist-id="smoke-release"]').isChecked(),
    true,
  );
  assert.equal(
    await decisionForm
      .locator('[data-confirm-finding-id="human-finding-carried-accessibility"]')
      .isChecked(),
    true,
  );
  assert.deepEqual(
    await page.evaluate(() => {
      const field = document.activeElement;
      return {
        name: field?.getAttribute?.("name") || "",
        value: field?.value || "",
        selectionStart:
          field instanceof HTMLTextAreaElement ? field.selectionStart : null,
        selectionEnd:
          field instanceof HTMLTextAreaElement ? field.selectionEnd : null,
      };
    }),
    {
      name: "human_finding_text",
      value: "Secondary finding survives refresh.",
      selectionStart: 10,
      selectionEnd: 17,
    },
  );

  await findingRows.nth(1).getByRole("button", { name: "Remove finding 2" }).click();
  await findingRows.nth(0).getByRole("button", { name: "Remove finding 1" }).click();
  assert.equal(await findingRows.count(), 0);
  await decisionForm.getByRole("button", { name: "Approve" }).click();
  await page.getByText("Human review approved recorded.").waitFor({ state: "visible" });
  assert.equal(
    await detail.locator("[data-human-review-decision-form]").count(),
    0,
    "successful submission should remove rather than restore the completed decision draft",
  );

  const decisionRequest = requests.find(
    (request) =>
      request.method === "POST" &&
      request.pathname ===
        "/api/addons/team-addon/human-review-requests/human-review-release-human/decisions",
  );
  assert.ok(decisionRequest, "expected a human release review decision request");
  assert.equal(decisionRequest.payload.decision, "approved");
  assert.equal(decisionRequest.payload.expected_revision, 3);
  assert.match(
    decisionRequest.payload.request_id,
    /^team-ui:human-review-release-human:approved:/,
  );
  assert.deepEqual(decisionRequest.payload.checklist_results, {
    "smoke-release": true,
  });
  assert.deepEqual(decisionRequest.payload.confirmed_finding_ids, [
    "human-finding-carried-accessibility",
  ]);
  assert.deepEqual(decisionRequest.payload.evidence_refs, [
    "artifact-space:human-smoke-evidence",
  ]);
  assert.equal(
    "findings" in decisionRequest.payload,
    false,
    "non-changes decisions should not send structured findings",
  );
  assert.equal("reviewer_user_id" in decisionRequest.payload, false);
  assert.equal("reviewer_display_name" in decisionRequest.payload, false);
  assert.equal(initialState.human_review_requests[humanRequest.id].state, "approved");

  await page.goto(
    `http://127.0.0.1:${port}/addons/team-addon/?privileged_unlocked=true&view=my-human-reviews&human_review_request_id=missing-review`,
    { waitUntil: "domcontentloaded" },
  );
  await waitForRevision(page, state.revision);
  await page
    .getByText(
      "Human review missing-review is not assigned or visible; showing your assigned reviews instead.",
    )
    .waitFor({ state: "visible" });
  await page.locator('[data-my-human-reviews][aria-pressed="true"]').waitFor({
    state: "visible",
  });
  await page
    .locator('#team-delivery-page [data-releases-view] li.release-card[data-release-id="release-human"]')
    .waitFor({ state: "visible" });
});

test("team add-on reports published release regressions without implying rollback or successor creation", async (t) => {
  const initialState = seededGitState();
  initialState.release_packages["release-timestamp-only"] = {
    ...initialState.release_packages["release-accepted"],
    id: "release-timestamp-only",
    published_commit_sha: null,
  };
  initialState.release_packages["release-commit-only"] = {
    ...initialState.release_packages["release-accepted"],
    id: "release-commit-only",
    published_at: null,
  };
  const { port, requests, state } = await createTeamAddonServer(t, { initialState });
  const page = await openBrowserPage(
    t,
    port,
    {},
    "/addons/team-addon/?privileged_unlocked=true",
  );

  await waitForRevision(page, 4);
  await page.getByRole("tab", { name: "Delivery" }).click();
  for (const incompleteReleaseId of [
    "release-timestamp-only",
    "release-commit-only",
  ]) {
    const incompleteCard = page.locator(
      `#team-delivery-page .releases-view li.release-card[data-release-id="${incompleteReleaseId}"]`,
    );
    await incompleteCard.waitFor({ state: "visible" });
    assert.equal(
      await incompleteCard.locator(".post-release-report-control").count(),
      0,
      `${incompleteReleaseId} must not expose post-release reporting`,
    );
  }
  const releaseCard = page.locator(
    '#team-delivery-page .releases-view li.release-card[data-release-id="release-accepted"]',
  );
  await releaseCard.waitFor({ state: "visible" });
  await releaseCard.locator(":scope > details > summary").click();

  const reportControl = releaseCard.locator(".post-release-report-control");
  await reportControl
    .getByText(
      "Reporting creates a follow-up case and triage task. It does not roll back this published release and does not automatically create or authorize a successor.",
      { exact: true },
    )
    .waitFor({ state: "attached" });
  await reportControl.getByText("Report regression or adjustment", { exact: true }).click();
  const reportForm = reportControl.locator("[data-post-release-report-form]");
  await reportForm.waitFor({ state: "visible" });
  assert.equal(await reportForm.locator('textarea[name="report"]').getAttribute("required"), "");
  assert.equal(
    await reportForm.locator('textarea[name="evidence_refs"]').getAttribute("required"),
    "",
  );

  await reportForm
    .locator('textarea[name="report"]')
    .fill("The published confirmation path regressed after rollout.");
  await reportForm.locator('textarea[name="evidence_refs"]').fill("   ");
  await reportForm
    .getByRole("button", { name: "Create follow-up case and triage task" })
    .click();
  await page.getByText("At least one evidence reference is required.").waitFor({
    state: "visible",
  });
  assert.equal(
    requests.filter(
      (request) =>
        request.method === "POST" &&
        request.pathname ===
          "/api/addons/team-addon/release-packages/release-accepted/report-issue",
    ).length,
    0,
  );

  await reportForm
    .locator('textarea[name="evidence_refs"]')
    .fill("artifact-space:reports/release-accepted-regression.png");
  await reportForm
    .getByRole("button", { name: "Create follow-up case and triage task" })
    .click();
  await page
    .getByText(
      "Post-release follow-up recorded. The published release remains unchanged pending triage.",
      { exact: true },
    )
    .waitFor({ state: "visible" });

  const reportRequest = requests.find(
    (request) =>
      request.method === "POST" &&
      request.pathname ===
        "/api/addons/team-addon/release-packages/release-accepted/report-issue",
  );
  assert.ok(reportRequest, "expected a published release report request");
  assert.equal(
    reportRequest.payload.report,
    "The published confirmation path regressed after rollout.",
  );
  assert.deepEqual(reportRequest.payload.evidence_refs, [
    "artifact-space:reports/release-accepted-regression.png",
  ]);
  assert.equal(reportRequest.payload.expected_revision, 4);
  assert.match(
    reportRequest.payload.request_id,
    /^team-ui:release-accepted:report-issue:/,
  );
  assert.equal("reporter_user_id" in reportRequest.payload, false);
  assert.equal("reporter_display_name" in reportRequest.payload, false);

  const result = releaseCard.locator(".post-release-report-result");
  await result.waitFor({ state: "visible" });
  await result.getByText(/Case post-release-case-5 .* triage/).waitFor({
    state: "visible",
  });
  await result
    .getByText(/Triage task post-release-triage-5 .* Triage post-release feedback for release-accepted .* pending/)
    .waitFor({ state: "visible" });
  assert.equal(state.release_packages["release-accepted"].state, "accepted");
  assert.equal(state.release_packages["release-accepted"].published_commit_sha, "ccc333");
  assert.equal(Object.keys(state.release_packages).length, 3);
  assert.equal(state.cases["post-release-case-5"].source, "post_release_feedback");
  assert.equal(state.tasks["post-release-triage-5"].case_id, "post-release-case-5");
});

test("team add-on project editor keeps human release policy in bounded controls", async (t) => {
  const initialState = seededGitState();
  initialState.projects["alpha-project"].release_policy = {
    schema_version: 2,
    gate_sequence: [
      { kind: "verification", enabled: true },
      { kind: "agent_review", enabled: false },
      { kind: "human_review", enabled: true },
    ],
    allow_ungated: false,
    verification_commands: [{ name: "pytest", command: "./.venv/bin/pytest -q" }],
    human_review_requirement: {
      reviewer_group_ids: ["release-approvers"],
      checklist: [
        {
          id: "operator-smoke",
          label: "Operator smoke test",
          description: "Run the primary operator flow.",
          required: true,
        },
      ],
      test_instructions: "Exercise the primary operator flow.",
      preparation_mode: "workflow",
    },
  };
  const { port, requests } = await createTeamAddonServer(t, { initialState });
  const page = await openBrowserPage(t, port);

  await waitForRevision(page, 4);
  await page.getByRole("tab", { name: "Teams" }).click();
  const alphaGroup = page.locator(
    '#team-people-page [data-team-record-group-id="team-alpha"]',
  );
  await alphaGroup.locator(".team-record-group-header").click();
  await alphaGroup.locator('[data-team-record-tab="project"]').click();
  await alphaGroup
    .locator('[data-project-id="alpha-project"]')
    .getByRole("button")
    .click();
  await page
    .locator(
      '#people-agent-detail-view [data-edit-type="project"][data-edit-id="alpha-project"]',
    )
    .click();

  const form = page.locator("#edit-record-form");
  await form.waitFor({ state: "visible" });
  assert.equal(await form.locator('[name="release_policy_enabled"]').isChecked(), true);
  const gateRows = form.locator("[data-release-gate-row]");
  assert.deepEqual(await gateRows.evaluateAll((rows) => rows.map((row) => row.dataset.releaseGateKind)), [
    "verification",
    "agent_review",
    "human_review",
  ]);
  assert.equal(await form.locator('[name="human_review_enabled"]').isChecked(), true);
  assert.equal(
    await form.locator('[name="human_reviewer_group_ids"]').inputValue(),
    "release-approvers",
  );
  assert.equal(await form.locator('[name="human_review_checklist"]').count(), 0);
  const checklistEditor = form.locator("[data-human-review-checklist-editor]");
  const checklistRows = checklistEditor.locator("[data-human-review-checklist-row]");
  assert.equal(await checklistRows.count(), 1);
  assert.equal(
    await checklistRows.nth(0).locator('[name="human_review_checklist_label"]').inputValue(),
    "Operator smoke test",
  );
  assert.equal(
    await checklistRows.nth(0).locator('[name="human_review_checklist_description"]').inputValue(),
    "Run the primary operator flow.",
  );
  assert.equal(
    await checklistRows.nth(0).locator('[name="human_review_checklist_id"]').inputValue(),
    "operator-smoke",
  );
  assert.equal(
    await checklistRows.nth(0).locator('[name="human_review_checklist_required"]').isChecked(),
    true,
  );
  assert.equal(
    await form.locator('[name="human_review_preparation_mode"]').inputValue(),
    "workflow",
  );
  const rawPolicy = await form.locator('[name="release_policy_json"]').inputValue();
  assert.match(rawPolicy, /verification_commands/);
  assert.doesNotMatch(rawPolicy, /human_review_requirement/);
  await form
    .locator('[name="release_policy_json"]')
    .fill(
      '{"allow_ungated":false,"verification_commands":[{"name":"pytest","command":"./.venv/bin/pytest -q"}],"human_review_requirement":{"reviewer_group_ids":["injected-user-control"]}}',
    );
  await gateRows.nth(2).getByRole("button", { name: "Move Human review earlier" }).click();
  await gateRows.nth(1).getByRole("button", { name: "Move Human review earlier" }).click();
  await form.locator('[name="release_gate_enabled_verification"]').uncheck();
  await form
    .locator('[name="human_reviewer_group_ids"]')
    .fill("release-approvers\nrelease-qa");
  await checklistEditor.getByRole("button", { name: "Add check" }).click();
  assert.equal(await checklistRows.count(), 2);
  const newChecklistRow = checklistRows.nth(1);
  await newChecklistRow
    .locator('[name="human_review_checklist_label"]')
    .fill("Release confidence");
  await newChecklistRow
    .locator('[name="human_review_checklist_description"]')
    .fill("Confirm the candidate | remaining risk is acceptable.");
  await newChecklistRow
    .locator('[name="human_review_checklist_id"]')
    .fill("operator-smoke");
  await newChecklistRow
    .locator('[name="human_review_checklist_required"]')
    .uncheck();

  await form.getByRole("button", { name: "Save changes" }).click();
  await page
    .getByText("Fix the highlighted human review requirement before saving.")
    .waitFor({ state: "visible" });
  assert.match(
    await newChecklistRow
      .locator('[name="human_review_checklist_id"]')
      .evaluate((field) => field.validationMessage),
    /Checklist keys must be unique/,
  );
  assert.equal(
    requests.filter(
      (request) =>
        request.method === "PATCH" &&
        request.pathname === "/api/addons/team-addon/projects/alpha-project",
    ).length,
    0,
  );

  await newChecklistRow
    .locator('[name="human_review_checklist_id"]')
    .fill("");
  await checklistEditor.getByRole("button", { name: "Add check" }).click();
  assert.equal(await checklistRows.count(), 3);
  await checklistRows
    .nth(2)
    .locator('[name="human_review_checklist_label"]')
    .fill("Temporary check");
  await checklistRows.nth(2).getByRole("button", { name: "Remove" }).click();
  assert.equal(await checklistRows.count(), 2);
  await checklistRows
    .nth(1)
    .getByRole("button", { name: "Move checklist item up" })
    .click();
  assert.equal(
    await checklistRows.nth(0).locator('[name="human_review_checklist_label"]').inputValue(),
    "Release confidence",
  );

  await form.getByRole("button", { name: "Save changes" }).click();
  await page.getByText("Saved revision 5.").waitFor({ state: "visible" });

  const patchRequest = requests.find(
    (request) =>
      request.method === "PATCH" &&
      request.pathname === "/api/addons/team-addon/projects/alpha-project",
  );
  assert.ok(patchRequest, "expected the project release policy update");
  assert.deepEqual(patchRequest.payload.release_policy.gate_sequence, [
    { kind: "human_review", enabled: true },
    { kind: "verification", enabled: false },
    { kind: "agent_review", enabled: false },
  ]);
  assert.equal(patchRequest.payload.release_policy.verification_commands.length, 1);
  assert.deepEqual(
    patchRequest.payload.release_policy.human_review_requirement.reviewer_group_ids,
    ["release-approvers", "release-qa"],
  );
  assert.equal(
    patchRequest.payload.release_policy.human_review_requirement.preparation_mode,
    "workflow",
  );
  assert.deepEqual(
    patchRequest.payload.release_policy.human_review_requirement.checklist,
    [
      {
        id: "release-confidence",
        label: "Release confidence",
        description: "Confirm the candidate | remaining risk is acceptable.",
        required: false,
      },
      {
        id: "operator-smoke",
        label: "Operator smoke test",
        description: "Run the primary operator flow.",
        required: true,
      },
    ],
  );
  assert.equal(
    JSON.stringify(patchRequest.payload.release_policy).includes("injected-user-control"),
    false,
  );
  assert.equal(
    "reviewer_user_ids" in patchRequest.payload.release_policy.human_review_requirement,
    false,
  );
});

test("team add-on Task board exposes the existing task editor", async (t) => {
  const { port, requests } = await createTeamAddonServer(t, {
    initialState: seededGitState(),
  });
  const page = await openBrowserPage(t, port);

  await waitForRevision(page, 4);
  await page.getByRole("tab", { name: "Task board" }).click();
  await page.locator("#task-board-team-select").selectOption("team-alpha");
  await page.locator("#task-board-project-select").selectOption("alpha-project");

  const editableTaskCard = page.locator(
    '.task-board-card[data-task-board-task-id="task-pending"]',
  );
  await editableTaskCard.getByRole("button", { name: "Edit", exact: true }).click();

  const taskEditForm = page.locator("#edit-record-form");
  await taskEditForm.waitFor({ state: "visible" });
  assert.equal(
    await taskEditForm.locator('[name="title"]').inputValue(),
    "Draft pending delivery",
  );
  await taskEditForm.locator('[name="task_author_name"]').fill("Alice Example");
  await taskEditForm.locator('[name="task_author_email"]').fill("alice@example.test");
  await taskEditForm.getByRole("button", { name: "Save changes", exact: true }).click();
  await taskEditForm.waitFor({ state: "hidden" });
  const updates = () => requests.filter((request) => request.method === "PATCH" && request.pathname.endsWith("/tasks/task-pending"));
  assert.deepEqual(updates().at(-1).payload.authored_by, { name: "Alice Example", email: "alice@example.test" });
  await editableTaskCard.getByRole("button", { name: "Edit", exact: true }).click();
  assert.equal(await taskEditForm.locator('[name="task_author_name"]').inputValue(), "Alice Example");
  await taskEditForm.locator('[name="task_author_name"]').fill("");
  await taskEditForm.locator('[name="task_author_email"]').fill("");
  await taskEditForm.getByRole("button", { name: "Save changes", exact: true }).click();
  await taskEditForm.waitFor({ state: "hidden" });
  assert.equal(updates().at(-1).payload.authored_by, null);
});

test("team add-on task cancellation is explicit audited and lifecycle-aware", async (t) => {
  const initialState = seededGitState();
  initialState.agents["alpha-lead"].display_name = "Grace Lead";
  const taskBase = {
    owner_agent_id: "alpha-lead",
    project_id: "alpha-project",
    created_by_agent_id: "alpha-manager",
    dependency_task_ids: [],
    deliverable_ids: [],
    created_at: "2026-07-27T08:00:00Z",
    updated_at: "2026-07-27T08:00:00Z",
  };
  initialState.tasks["task-cancellable"] = {
    ...taskBase,
    id: "task-cancellable",
    priority: 1,
    state: "in_progress",
    title: "Retire the incorporated retry action",
    description: "The accepted parent deliverable already contains the corrected contribution.",
    deliverable_ids: ["deliverable-existing"],
    assessment: {
      readiness: "actionable",
      resolution_status: "unresolved",
      unresolved_descendant_ids: [],
      active_acceptance_ids: [],
      active_review_request_ids: [],
    },
  };
  initialState.tasks["task-successor"] = {
    ...taskBase,
    id: "task-successor",
    priority: 2,
    state: "accepted",
    title: "Accepted parent integration",
    description: "The canonical accepted integration.",
    parent_task_id: "task-with-child",
    assessment: {
      readiness: "resolved",
      resolution_status: "resolved",
      unresolved_descendant_ids: [],
      active_acceptance_ids: [],
      active_review_request_ids: [],
    },
  };
  initialState.tasks["task-with-child"] = {
    ...taskBase,
    id: "task-with-child",
    priority: 3,
    state: "blocked",
    title: "Parent with active child",
    description: "This task cannot be cancelled until its child is resolved.",
    child_task_ids: ["task-active-child", "task-successor"],
    assessment: {
      readiness: "waiting",
      resolution_status: "unresolved",
      direct_child_ids: ["task-active-child", "task-successor"],
      unresolved_descendant_ids: ["task-active-child"],
      active_acceptance_ids: [],
      active_review_request_ids: [],
    },
  };
  initialState.tasks["task-active-child"] = {
    ...taskBase,
    id: "task-active-child",
    priority: 4,
    state: "pending",
    title: "Active child contribution",
    description: "Finish or cancel this child first.",
    parent_task_id: "task-with-child",
    assessment: {
      readiness: "actionable",
      resolution_status: "unresolved",
      unresolved_descendant_ids: [],
      active_acceptance_ids: [],
      active_review_request_ids: [],
    },
  };

  const { port, requests } = await createTeamAddonServer(t, { initialState });
  const page = await openBrowserPage(t, port);

  await waitForRevision(page, 4);
  await page.getByRole("tab", { name: "Planning" }).click();
  const alphaProjectCard = page.locator(
    "#team-planning-page .planning-project-card",
    { hasText: "Alpha Project" },
  );
  await alphaProjectCard.locator(":scope > details > summary").click();

  const cancellableTask = page.locator(
    '#team-planning-page [data-planning-task-id="task-cancellable"]',
  );
  await cancellableTask.locator(":scope > .planning-task-details > summary").click();
  const cancellableBody = cancellableTask.locator(".planning-task-body");
  await cancellableBody.getByRole("button", { name: "Edit" }).click();
  const editState = page.locator('#edit-record-form select[name="state"]');
  await editState.waitFor({ state: "visible" });
  assert.equal(await editState.locator('option[value="accepted"]').count(), 0);
  assert.equal(await editState.locator('option[value="cancelled"]').count(), 0);
  let editForm = page.locator("#edit-record-form");
  await editForm
    .locator('[name="title"]')
    .fill("Retire the incorporated retry action safely");
  await editForm.getByRole("button", { name: "Save changes" }).click();
  await page.getByText("Saved revision 5.").waitFor({ state: "visible" });
  const ordinaryEditRequest = requests.filter(
    (request) =>
      request.method === "PATCH" &&
      request.pathname ===
        "/api/addons/team-addon/tasks/task-cancellable",
  ).at(-1);
  assert.ok(ordinaryEditRequest, "expected ordinary task edit");
  assert.equal("integration_boundary" in ordinaryEditRequest.payload, false);
  assert.equal("deliverable_contract" in ordinaryEditRequest.payload, false);

  await cancellableBody.getByRole("button", { name: "Edit" }).click();
  editForm = page.locator("#edit-record-form");
  await editForm.locator('[name="require_git_provenance"]').check();
  await editForm.getByRole("button", { name: "Save changes" }).click();
  await page.getByText("Saved revision 6.").waitFor({ state: "visible" });
  const contractedLeafEditRequest = requests.filter(
    (request) =>
      request.method === "PATCH" &&
      request.pathname ===
        "/api/addons/team-addon/tasks/task-cancellable",
  ).at(-1);
  assert.ok(contractedLeafEditRequest, "expected contracted-leaf task edit");
  assert.equal(contractedLeafEditRequest.payload.integration_boundary, false);
  assert.deepEqual(contractedLeafEditRequest.payload.deliverable_contract, {
    require_git_provenance: true,
    required_contribution_task_ids: [],
  });

  await cancellableBody.getByRole("button", { name: "Edit" }).click();
  editForm = page.locator("#edit-record-form");
  await editForm.locator('[name="integration_boundary"]').check();
  await editForm
    .locator('[name="required_contribution_task_ids"]')
    .fill("task-successor");
  await editForm.getByRole("button", { name: "Save changes" }).click();
  await page.getByText("Saved revision 7.").waitFor({ state: "visible" });
  const integrationEditRequest = requests.filter(
    (request) =>
      request.method === "PATCH" &&
      request.pathname ===
        "/api/addons/team-addon/tasks/task-cancellable",
  ).at(-1);
  assert.ok(integrationEditRequest, "expected integration-boundary task edit");
  assert.equal(integrationEditRequest.payload.integration_boundary, true);
  assert.deepEqual(integrationEditRequest.payload.deliverable_contract, {
    require_git_provenance: true,
    required_contribution_task_ids: ["task-successor"],
  });

  const cancelButton = cancellableBody.getByRole("button", {
    name: "Cancel task",
  });
  assert.equal(await cancelButton.isEnabled(), true);
  assert.match(await cancelButton.getAttribute("title"), /audited reason/);
  await cancelButton.click();

  const cancellationForm = page.locator("#task-cancellation-form");
  await cancellationForm.waitFor({ state: "visible" });
  await page
    .locator("#task-cancellation-subject")
    .getByText(/Retire the incorporated retry action/)
    .waitFor({ state: "visible" });
  await cancellationForm.locator('[name="reason_code"]').selectOption("superseded");
  await cancellationForm
    .locator('[name="reason"]')
    .fill("Its contribution was incorporated and corrected in the accepted parent deliverable.");
  await cancellationForm
    .locator('[name="successor_task_id"]')
    .selectOption("task-successor");
  await cancellationForm.getByRole("button", { name: "Cancel task" }).click();
  await page.getByText("Saved revision 8.").waitFor({ state: "visible" });

  const cancellationRequest = requests.find(
    (request) =>
      request.method === "POST" &&
      request.pathname ===
        "/api/addons/team-addon/tasks/task-cancellable/cancel",
  );
  assert.ok(cancellationRequest, "expected the dedicated cancellation request");
  assert.deepEqual(
    {
      reason_code: cancellationRequest.payload.reason_code,
      reason: cancellationRequest.payload.reason,
      successor_task_id: cancellationRequest.payload.successor_task_id,
    },
    {
      reason_code: "superseded",
      reason:
        "Its contribution was incorporated and corrected in the accepted parent deliverable.",
      successor_task_id: "task-successor",
    },
  );
  assert.equal(await page.locator("#task-cancellation-modal").isHidden(), true);
  const projectDetails = alphaProjectCard.locator(":scope > details");
  if (!(await projectDetails.evaluate((details) => details.open))) {
    await projectDetails.locator(":scope > summary").click();
  }

  const blockedParent = page.locator(
    '#team-planning-page [data-planning-task-id="task-with-child"]',
  );
  await blockedParent.locator(":scope > .planning-task-details > summary").click();
  const blockedCancel = blockedParent
    .locator(".planning-task-body")
    .getByRole("button", { name: "Cancel task" });
  assert.equal(await blockedCancel.isDisabled(), true);
  assert.match(await blockedCancel.getAttribute("title"), /descendant task/);

  await page.getByRole("tab", { name: "Task board" }).click();
  await page.locator("#task-board-team-select").selectOption("team-alpha");
  await page.locator("#task-board-project-select").selectOption("alpha-project");
  await page
    .locator("#team-task-board-page")
    .getByRole("checkbox", { name: /Show completed/ })
    .check();
  const terminalCancel = page
    .locator('.task-board-card[data-task-board-task-id="task-successor"]')
    .getByRole("button", { name: "Cancel task" });
  await terminalCancel.waitFor({ state: "visible" });
  assert.equal(await terminalCancel.isDisabled(), true);
  assert.match(await terminalCancel.getAttribute("title"), /already Accepted/);
});

test("team add-on planning tasks separate overview hierarchy contribution brief and technical detail", async (t) => {
  let releaseCosts = () => {};
  const costsGate = new Promise((resolve) => {
    releaseCosts = resolve;
  });
  t.after(() => {
    releaseCosts();
  });
  const initialState = seededGitState();
  initialState.agents["alpha-lead"].display_name = "Grace Lead";
  initialState.agents["alpha-manager"].display_name = "Ada Manager";
  initialState.projects["alpha-project"].goals = [
    { id: "shared-goal", description: "Coordinate alpha launch", state: "active" },
  ];
  initialState.projects["beta-project"].goals = [
    { id: "shared-goal", description: "Coordinate beta launch", state: "active" },
  ];

  initialState.tasks["task-starship-root"] = {
    id: "task-starship-root",
    owner_agent_id: "alpha-lead",
    project_id: "alpha-project",
    priority: -10,
    state: "in_progress",
    state_entered_at: "2026-07-26T12:00:00Z",
    title: "Prepare the Starship launch",
    description: [
      "=== THE GAME ===",
      "Build the **Starship** launch console without exposing storage identifiers.",
      "",
      "MISSION CONTROL",
      "Coordinate the named flight teams and preserve `telemetry` evidence.",
      "",
      "- Verify the launch window",
      "- Reconcile every child contribution",
      "",
      "# Acceptance",
      "1. Review the composed delivery",
      "2. Publish the operator handoff",
      "",
      "<script>window.__unsafePlanningBrief = true</script>",
    ].join("\n"),
    created_by_agent_id: "alpha-manager",
    child_task_ids: [
      "task-starship-missing",
      "task-starship-submitted",
      "task-starship-reviewed",
      "task-starship-accepted",
    ],
    dependency_status: "waiting",
    readiness: "waiting",
    resolution_status: "unresolved",
    assessment: {
      readiness: "waiting",
      resolution_status: "unresolved",
      resolution_reason: "Waiting for two unresolved child contributions.",
      direct_child_ids: [
        "task-starship-missing",
        "task-starship-submitted",
        "task-starship-reviewed",
        "task-starship-accepted",
      ],
      unresolved_descendant_ids: [
        "task-starship-missing",
        "task-starship-submitted",
      ],
      contribution_coverage: {
        status: "incomplete",
        complete: false,
        required_source_deliverable_ids: [
          "deliverable-starship-missing",
          "deliverable-starship-submitted",
          "deliverable-starship-reviewed",
          "deliverable-starship-accepted",
        ],
        missing_source_deliverable_ids: ["deliverable-starship-missing"],
        provided_source_deliverable_ids: [
          "deliverable-starship-submitted",
          "deliverable-starship-reviewed",
          "deliverable-starship-accepted",
        ],
        unexpected_source_deliverable_ids: [],
      },
    },
    created_at: "2026-07-25T09:00:00Z",
    updated_at: "2026-07-26T12:00:00Z",
  };

  const childTasks = [
    {
      id: "task-starship-missing",
      title: "Specify engine telemetry",
      state: "pending",
      deliverableId: "deliverable-starship-missing",
    },
    {
      id: "task-starship-submitted",
      title: "Implement flight controls",
      state: "finished",
      deliverableId: "deliverable-starship-submitted",
    },
    {
      id: "task-starship-reviewed",
      title: "Review navigation safeguards",
      state: "waiting_for_review",
      deliverableId: "deliverable-starship-reviewed",
    },
    {
      id: "task-starship-accepted",
      title: "Approve the launch checklist",
      state: "accepted",
      deliverableId: "deliverable-starship-accepted",
    },
  ];
  childTasks.forEach((child, index) => {
    initialState.tasks[child.id] = {
      id: child.id,
      owner_agent_id: "alpha-lead",
      project_id: "alpha-project",
      priority: index,
      state: child.state,
      state_entered_at: `2026-07-26T1${index}:00:00Z`,
      title: child.title,
      description: `${child.title} task brief.`,
      created_by_agent_id: "alpha-manager",
      parent_task_id: "task-starship-root",
      deliverable_ids: [child.deliverableId],
      created_at: "2026-07-25T09:00:00Z",
      updated_at: `2026-07-26T1${index}:00:00Z`,
    };
  });
  initialState.tasks["task-starship-cancelled"] = {
    id: "task-starship-cancelled",
    owner_agent_id: "alpha-lead",
    project_id: "alpha-project",
    priority: 20,
    state: "cancelled",
    state_entered_at: "2026-07-26T15:00:00Z",
    title: "Retire the superseded launch rehearsal",
    description: "This rehearsal was cancelled before review.",
    created_by_agent_id: "alpha-manager",
    dependency_status: "satisfied",
    readiness: "resolved",
    resolution_status: "resolved",
    integration_ready: false,
    assessment: {
      readiness: "resolved",
      resolution_status: "resolved",
      integration_ready: false,
      direct_child_ids: [],
      unresolved_descendant_ids: [],
    },
    cancellation: {
      reason_code: "superseded",
      reason: "Replaced by the production launch rehearsal.",
      actor_id: "alpha-manager",
      cancelled_at: "2026-07-26T15:00:00Z",
    },
    deliverable_ids: [],
    created_at: "2026-07-25T10:00:00Z",
    updated_at: "2026-07-26T15:00:00Z",
  };

  initialState.deliverables["deliverable-starship-missing"] = {
    id: "deliverable-starship-missing",
    task_id: "task-starship-missing",
    producer_agent_id: "alpha-lead",
    summary: "Engine telemetry specification.",
    state: "pending",
    artifact_refs: [],
    review_request_ids: [],
  };
  initialState.deliverables["deliverable-starship-submitted"] = {
    id: "deliverable-starship-submitted",
    task_id: "task-starship-submitted",
    producer_agent_id: "alpha-lead",
    summary: "Flight controls implementation.",
    state: "pending",
    artifact_refs: ["artifact-space:starship/flight-controls"],
    review_request_ids: [],
  };
  initialState.deliverables["deliverable-starship-reviewed"] = {
    id: "deliverable-starship-reviewed",
    task_id: "task-starship-reviewed",
    producer_agent_id: "alpha-lead",
    summary: "Navigation safeguards review.",
    state: "pending",
    artifact_refs: ["artifact-space:starship/navigation"],
    review_request_ids: ["review-starship-navigation"],
  };
  initialState.deliverables["deliverable-starship-accepted"] = {
    id: "deliverable-starship-accepted",
    task_id: "task-starship-accepted",
    producer_agent_id: "alpha-lead",
    summary: "Accepted launch checklist.",
    state: "accepted",
    artifact_refs: ["artifact-space:starship/launch-checklist"],
    review_request_ids: [],
  };
  initialState.review_requests["review-starship-navigation"] = {
    id: "review-starship-navigation",
    deliverable_id: "deliverable-starship-reviewed",
    reviewer_agent_id: "alpha-manager",
    state: "accepted",
    findings: [],
    decision_at: "2026-07-26T14:00:00Z",
  };

  const { port, costResponses, emitStateSnapshot, state } = await createTeamAddonServer(t, {
    costsBeforeResponse: costsGate,
    initialState,
  });
  const page = await openBrowserPage(t, port);

  await waitForRevision(page, 4);
  await page.getByRole("tab", { name: "Planning" }).click();
  const alphaProjectCard = page.locator(
    "#team-planning-page .planning-project-card",
    { hasText: "Alpha Project" },
  );
  await alphaProjectCard.locator(":scope > details > summary").click();
  const alphaGoalDisclosure = alphaProjectCard
    .locator(".goal-list .compact-record-card", { hasText: "Coordinate alpha launch" })
    .locator(":scope > details");
  await alphaGoalDisclosure.locator(":scope > summary").click();
  const betaGoalDisclosure = page
    .locator("#team-planning-page .planning-project-card", { hasText: "Beta Project" })
    .locator(".goal-list .compact-record-card", { hasText: "Coordinate beta launch" })
    .locator(":scope > details");
  assert.notEqual(
    await alphaGoalDisclosure.getAttribute("data-team-disclosure-key"),
    await betaGoalDisclosure.getAttribute("data-team-disclosure-key"),
    "project-local goal IDs should be scoped by their owning project",
  );

  const rootTask = page.locator(
    '#team-planning-page [data-planning-task-id="task-starship-root"]',
  );
  await rootTask.waitFor({ state: "visible" });
  const compactOverview = await rootTask
    .locator(":scope > .planning-task-details > .planning-task-summary")
    .innerText();
  assert.match(compactOverview, /Prepare the Starship launch/);
  assert.match(compactOverview, /Grace Lead · state for (?:just now|\d+ (?:minute|hour|day)s?)/);
  assert.match(compactOverview, /Waiting for two unresolved child contributions\./);
  assert.match(compactOverview, /In Progress/);
  assert.doesNotMatch(
    compactOverview,
    /task-starship-root|task-starship-missing|alpha-lead|alpha-project/,
  );

  await rootTask.locator(":scope > .planning-task-details > summary").click();
  const rootBody = rootTask.locator(".planning-task-body");
  for (const heading of [
    "Overview",
    "Lifecycle",
    "Hierarchy",
    "Delivery / contribution",
    "Task brief",
    "Technical details",
  ]) {
    await rootBody.getByRole("heading", { name: heading, exact: true }).waitFor({
      state: "visible",
    });
  }
  await rootBody.getByText("Owner", { exact: true }).waitFor({ state: "visible" });
  await rootBody.getByText("Grace Lead", { exact: true }).first().waitFor({
    state: "visible",
  });
  await rootBody.getByText("State age", { exact: true }).waitFor({ state: "visible" });
  await rootBody
    .locator(".planning-lifecycle-reason", {
      hasText: "Waiting for two unresolved child contributions.",
    })
    .waitFor({ state: "visible" });

  const lifecycle = rootBody.locator('ol[aria-label="Task lifecycle"]');
  await lifecycle.waitFor({ state: "visible" });
  assert.deepEqual(
    await lifecycle.locator("li strong").allTextContents(),
    ["Created", "Dependencies", "Execution", "Review / acceptance", "Resolved"],
  );

  const hierarchySection = rootBody
    .getByRole("heading", { name: "Hierarchy", exact: true })
    .locator("..");
  const hierarchySummary = await hierarchySection
    .locator(".planning-hierarchy-summary")
    .innerText();
  assert.match(hierarchySummary, /4 direct children/);
  assert.match(hierarchySummary, /2 unresolved descendants/);
  const namedHierarchy = hierarchySection.locator(".planning-hierarchy-details");
  assert.equal(await namedHierarchy.getAttribute("open"), null);
  await namedHierarchy.locator("summary").click();
  for (const child of childTasks) {
    await namedHierarchy.getByText(child.title, { exact: true }).waitFor({
      state: "visible",
    });
  }

  const contributionSection = rootBody
    .getByRole("heading", {
      name: "Delivery / contribution",
      exact: true,
    })
    .locator("..");
  const expectedContributionRows = [
    [".planning-contribution-missing", "Specify engine telemetry", "Missing"],
    [".planning-contribution-submitted", "Implement flight controls", "Submitted"],
    [".planning-contribution-reviewed", "Review navigation safeguards", "Reviewed"],
    [".planning-contribution-accepted", "Approve the launch checklist", "Accepted"],
  ];
  for (const [selector, title, status] of expectedContributionRows) {
    const row = contributionSection.locator(selector);
    await row.getByText(title, { exact: true }).waitFor({ state: "visible" });
    await row.getByText(status, { exact: true }).waitFor({ state: "visible" });
  }

  const briefDetails = rootBody.locator(".planning-task-brief-details");
  assert.equal(await briefDetails.getAttribute("open"), null);
  assert.equal(
    await briefDetails.locator(".planning-task-brief-content").isVisible(),
    false,
  );
  await briefDetails.locator("summary", { hasText: "Show full brief" }).click();
  await briefDetails.getByRole("heading", { name: "THE GAME", exact: true }).waitFor({
    state: "visible",
  });
  await briefDetails
    .getByRole("heading", { name: "MISSION CONTROL", exact: true })
    .waitFor({ state: "visible" });
  await briefDetails.getByRole("heading", { name: "Acceptance", exact: true }).waitFor({
    state: "visible",
  });
  await briefDetails.locator("strong", { hasText: "Starship" }).waitFor({
    state: "visible",
  });
  await briefDetails.locator("code", { hasText: "telemetry" }).waitFor({
    state: "visible",
  });
  assert.equal(await briefDetails.locator("ul > li").count(), 2);
  assert.equal(await briefDetails.locator("ol > li").count(), 2);
  assert.equal(await briefDetails.locator("script").count(), 0);
  assert.equal(
    await briefDetails.getByText("=== THE GAME ===", { exact: true }).count(),
    0,
  );

  releaseCosts();
  await waitForCondition(
    () => costResponses.length >= 1,
    "expected the delayed costs response to complete",
  );
  assert.notEqual(
    await alphaProjectCard.locator(":scope > details").getAttribute("open"),
    null,
    "the planning project should remain expanded after a background render",
  );
  assert.notEqual(
    await rootTask.locator(":scope > .planning-task-details").getAttribute("open"),
    null,
    "the planning task should remain expanded after a background render",
  );
  assert.notEqual(
    await briefDetails.getAttribute("open"),
    null,
    "the full task brief should remain expanded after a background render",
  );
  assert.notEqual(
    await alphaGoalDisclosure.getAttribute("open"),
    null,
    "the opened alpha goal should remain expanded after a background render",
  );
  assert.equal(
    await betaGoalDisclosure.getAttribute("open"),
    null,
    "the closed beta goal with the same local ID should remain closed",
  );
  await briefDetails.locator(".planning-task-brief-content").waitFor({ state: "visible" });

  for (const identifier of [
    "task-starship-root",
    "task-starship-missing",
    "alpha-lead",
    "alpha-project",
  ]) {
    const identifierLocations = await rootBody
      .getByText(identifier, { exact: true })
      .evaluateAll((nodes) => ({
        count: nodes.length,
        outsideTechnical: nodes.filter(
          (node) =>
            !node.closest(
              ".planning-task-technical-section details.technical-details",
            ),
        ).length,
      }));
    assert.ok(
      identifierLocations.count > 0,
      `${identifier} should remain available in technical details`,
    );
    assert.equal(
      identifierLocations.outsideTechnical,
      0,
      `${identifier} should be confined to technical details`,
    );
  }
  const technicalIdentifiers = rootBody.locator(
    ".planning-task-technical-section details.technical-details",
  );
  assert.equal(await technicalIdentifiers.getAttribute("open"), null);
  await technicalIdentifiers.locator("summary").click();
  for (const identifier of [
    "task-starship-root",
    "task-starship-missing",
    "alpha-lead",
    "alpha-project",
  ]) {
    await technicalIdentifiers.getByText(identifier, { exact: true }).first().waitFor({
      state: "visible",
    });
  }

  state.revision += 1;
  assert.ok(
    emitStateSnapshot({ response_scope: "revision", revision: state.revision }) > 0,
    "expected an active state stream",
  );
  await waitForRevision(page, state.revision);
  assert.notEqual(
    await alphaProjectCard.locator(":scope > details").getAttribute("open"),
    null,
    "the planning project should remain expanded after a state-stream refresh",
  );
  assert.notEqual(
    await rootTask.locator(":scope > .planning-task-details").getAttribute("open"),
    null,
    "the planning task should remain expanded after a state-stream refresh",
  );
  assert.notEqual(
    await briefDetails.getAttribute("open"),
    null,
    "the full task brief should remain expanded after a state-stream refresh",
  );
  assert.notEqual(
    await technicalIdentifiers.getAttribute("open"),
    null,
    "the task technical details should remain expanded after a state-stream refresh",
  );

  await page.locator("#planning-status-cancelled").check();
  const refreshedAlphaProjectCard = page.locator(
    "#team-planning-page .planning-project-card",
    { hasText: "Alpha Project" },
  );
  assert.notEqual(
    await refreshedAlphaProjectCard.locator(":scope > details").getAttribute("open"),
    null,
    "the planning project should stay expanded when filters re-render the task list",
  );
  const cancelledTask = page.locator(
    '#team-planning-page [data-planning-task-id="task-starship-cancelled"]',
  );
  await cancelledTask.locator(":scope > .planning-task-details > summary").click();
  const cancelledLifecycle = cancelledTask.locator('ol[aria-label="Task lifecycle"]');
  assert.equal(
    await cancelledLifecycle.locator(".planning-lifecycle-cancelled").count(),
    1,
  );
  const skippedReview = cancelledLifecycle.locator(".planning-lifecycle-skipped", {
    hasText: "Review / acceptance",
  });
  assert.equal(await skippedReview.count(), 1);
  await skippedReview
    .getByText("Skipped — task cancelled", { exact: true })
    .waitFor({ state: "visible" });
});

test("team add-on Planning skips review for resolved support tasks", async (t) => {
  const initialState = seededGitState();
  initialState.tasks = {
    "task-support-finished": {
      id: "task-support-finished",
      owner_agent_id: "alpha-lead",
      project_id: "alpha-project",
      priority: 1,
      task_kind: "support",
      state: "finished",
      state_entered_at: "2026-07-27T10:00:00Z",
      title: "Reconcile the deployment checklist",
      description: "Confirm the checklist state without producing a durable deliverable.",
      created_by_agent_id: "alpha-manager",
      dependency_status: "satisfied",
      readiness: "resolved",
      resolution_status: "resolved",
      integration_ready: true,
      assessment: {
        readiness: "resolved",
        resolution_status: "resolved",
        integration_ready: true,
        resolution_reason: "support_task_finished",
        direct_child_ids: [],
        unresolved_descendant_ids: [],
      },
      deliverable_ids: [],
      created_at: "2026-07-27T09:00:00Z",
      updated_at: "2026-07-27T10:00:00Z",
    },
  };
  initialState.deliverables = {};
  initialState.review_requests = {};

  const { port } = await createTeamAddonServer(t, { initialState });
  const page = await openBrowserPage(t, port);

  await waitForRevision(page, 4);
  await page.getByRole("tab", { name: "Planning" }).click();
  await page.locator("#planning-status-finished").check();
  const projectCard = page.locator(
    "#team-planning-page .planning-project-card",
    { hasText: "Alpha Project" },
  );
  await projectCard.locator(":scope > details > summary").click();
  const supportTask = projectCard.locator(
    '[data-planning-task-id="task-support-finished"]',
  );
  await supportTask.waitFor({ state: "visible" });
  await supportTask.locator(":scope > .planning-task-details > summary").click();

  const lifecycle = supportTask.locator('ol[aria-label="Task lifecycle"]');
  const reviewStage = lifecycle.locator(".planning-lifecycle-skipped", {
    hasText: "Review / acceptance",
  });
  assert.equal(await reviewStage.count(), 1);
  assert.equal(
    await lifecycle.locator(".planning-lifecycle-current", {
      hasText: "Review / acceptance",
    }).count(),
    0,
  );
  await reviewStage
    .getByText("Not required — support task finished", { exact: true })
    .waitFor({ state: "visible" });
  assert.equal(
    await lifecycle.locator(".planning-lifecycle-complete", {
      hasText: "Resolved",
    }).count(),
    1,
  );
});

test("team add-on task board shows and filters queued role assignments", async (t) => {
  const initialState = seededGitState();
  const task = initialState.tasks["task-pending"];
  Object.assign(task, {
    owner_agent_id: null,
    state: "pending",
    deliverable_ids: [],
    assignment_mode: "role",
    assignment_role_id: "role-senior",
    assignment_role_name: "Senior Developer",
    assignment_wait_reason: "waiting_for_dependencies",
  });
  const { port, requests } = await createTeamAddonServer(t, { initialState });
  const page = await openBrowserPage(t, port);
  await waitForRevision(page, 4);
  await page.getByRole("tab", { name: "Task board" }).click();
  await page.locator("#task-board-team-select").selectOption("team-alpha");
  await page.locator("#task-board-project-select").selectOption("alpha-project");
  const card = page.locator('.task-board-card[data-task-board-task-id="task-pending"]');
  assert.match(await card.innerText(), /Waiting for Senior Developer/);
  assert.doesNotMatch(await card.innerText(), /Unavailable assignee/);
  assert.match(await card.innerText(), /Waiting for dependencies/);
  await card.getByRole("button", { name: "Edit", exact: true }).click();
  const form = page.locator("#edit-record-form");
  await form.waitFor({ state: "visible" });
  assert.equal(await form.getByLabel("Assigned to", { exact: true }).inputValue(), "Waiting for Senior Developer");
  assert.equal(await form.locator('select[name="state"]').count(), 0);
  await form.locator('[name="title"]').fill("Updated queued task");
  await form.getByRole("button", { name: "Save changes", exact: true }).click();
  await form.waitFor({ state: "hidden" });
  const patch = requests.find((request) => request.method === "PATCH" && request.pathname.endsWith("/tasks/task-pending"));
  assert.equal(patch.payload.state, "pending");
  assert.equal(patch.payload.title, "Updated queued task");
  assert.equal("owner_agent_id" in patch.payload, false);

  await page.locator('[data-task-board-filter="assignee"]').selectOption("role:role-senior");
  assert.equal(await page.locator(".task-board-card").count(), 1);
  await page.locator('[data-task-board-mode="hierarchy"]').click();
  const branch = page.locator('.task-board-hierarchy-branch[data-task-board-task-id="task-pending"]');
  assert.match(await branch.innerText(), /Waiting for Senior Developer/);
});

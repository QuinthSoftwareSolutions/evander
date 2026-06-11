import assert from "node:assert/strict";
import { createServer as createHttpServer } from "node:http";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";
import test from "node:test";

import { chromium } from "playwright";

const staticRoot = path.resolve(
  fileURLToPath(new URL("../../../src/truman_team_addon/ui_static", import.meta.url)),
);

function emptyState() {
  return {
    revision: 0,
    teams: {},
    projects: {},
    agents: {},
    agent_runtimes: {},
    tasks: {},
    deliverables: {},
    review_requests: {},
    review_profiles: {},
    inbox: {},
    action_records: {},
    release_packages: {},
    ephemeral_assignments: {},
    git_promotions: {},
  };
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
        ephemeral_policy: { max_concurrent: 0 },
      },
      {
        key: "project_lead",
        label: "Project Lead",
        description: "Owns the project plan and task routing.",
        reports_to_role_key: "manager",
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
        ephemeral_policy: { max_concurrent: 1 },
      },
    ],
    required_fields: [
      {
        path: "team.id",
        label: "Team ID",
        description: "Stable identifier for the new team.",
        required: true,
        example: "team-auto-dev-squad",
      },
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
        path: "project.id",
        label: "Project ID",
        description: "Stable project identifier.",
        required: true,
        example: "project-auto-dev-squad",
      },
      {
        path: "project.name",
        label: "Project Name",
        description: "Human-readable project name.",
        required: true,
        example: "Reviewed Auto-Dev Delivery",
      },
      {
        path: "project.git.host_repo_path",
        label: "Git Host Repo Path",
        description: "Optional host path for Git integration.",
        required: false,
        example: "/srv/repos/auto-dev-squad",
      },
      {
        path: "project.git.bare_repo_name",
        label: "Git Bare Repo Name",
        description: "Optional bare repo name. Provide exactly one bare repo field if Git is enabled.",
        required: false,
        example: "auto-dev-squad.git",
      },
      {
        path: "project.git.bare_repo_path",
        label: "Git Bare Repo Path",
        description: "Optional bare repo path. Provide exactly one bare repo field if Git is enabled.",
        required: false,
        example: "/srv/git/auto-dev-squad.git",
      },
      {
        path: "project.git.base_branch",
        label: "Git Base Branch",
        description: "Optional upstream branch to mirror.",
        required: false,
        example: "main",
      },
      {
        path: "project.git.canonical_origin_url",
        label: "Git Canonical Origin",
        description: "Optional canonical origin URL.",
        required: false,
        example: "ssh://git.example/auto-dev-squad.git",
      },
      {
        path: "project.git.provider",
        label: "Git Provider",
        description: "Optional provider label for the canonical origin.",
        required: false,
        example: "github",
      },
      {
        path: "manager.agent_id",
        label: "Manager Agent ID",
        description: "Persistent manager agent identifier.",
        required: true,
        example: "manager-auto-dev-squad",
      },
      {
        path: "manager.session_id",
        label: "Manager Session ID",
        description: "Persistent manager session identifier.",
        required: true,
        example: "session-manager-auto-dev-squad",
      },
      {
        path: "project_lead.agent_id",
        label: "Project Lead Agent ID",
        description: "Persistent project lead agent identifier.",
        required: true,
        example: "project-lead-auto-dev-squad",
      },
      {
        path: "project_lead.session_id",
        label: "Project Lead Session ID",
        description: "Persistent project lead session identifier.",
        required: true,
        example: "session-project-lead-auto-dev-squad",
      },
      {
        path: "implementer.agent_id",
        label: "Implementer Agent ID",
        description: "Persistent implementer agent identifier.",
        required: true,
        example: "implementer-auto-dev-squad",
      },
      {
        path: "implementer.session_id",
        label: "Implementer Session ID",
        description: "Persistent implementer session identifier.",
        required: true,
        example: "session-implementer-auto-dev-squad",
      },
      {
        path: "reviewer.agent_id",
        label: "Reviewer Agent ID",
        description: "Persistent reviewer agent identifier.",
        required: true,
        example: "reviewer-auto-dev-squad",
      },
      {
        path: "reviewer.session_id",
        label: "Reviewer Session ID",
        description: "Persistent reviewer session identifier.",
        required: true,
        example: "session-reviewer-auto-dev-squad",
      },
      {
        path: "release_operator.agent_id",
        label: "Release Operator Agent ID",
        description: "Persistent release operator agent identifier.",
        required: true,
        example: "release-operator-auto-dev-squad",
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

async function serveStatic(response, assetName, contentType) {
  const assetPath = path.join(staticRoot, assetName);
  const body = await readFile(assetPath);
  response.writeHead(200, { "content-type": contentType });
  response.end(body);
}

async function createTeamAddonServer(t, { conflictOnFirstTeamCreate = false, initialState = null } = {}) {
  const state = initialState ? { ...emptyState(), ...initialState } : emptyState();
  const requests = [];
  let conflictSent = false;
  const template = autoDevSquadTemplate();

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
      if (request.method === "GET" && pathname === "/addons/team-addon/team-ui.css") {
        await serveStatic(response, "team-ui.css", "text/css; charset=utf-8");
        return;
      }
      if (request.method === "GET" && pathname === "/api/addons/team-addon/state") {
        jsonResponse(response, state);
        return;
      }
      if (request.method === "GET" && pathname === "/api/addons/team-addon/templates") {
        jsonResponse(response, { templates: [template] });
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
        const duplicateError = materializationDuplicateError(state, payload);
        if (duplicateError) {
          jsonResponse(response, { error: duplicateError }, 400);
          return;
        }
        const materialized = applyTemplateMaterialization(state, payload, template);
        jsonResponse(response, { ...state, materialized }, 201);
        return;
      }

      if (!pathname.startsWith("/api/addons/team-addon/")) {
        jsonResponse(response, { error: `unexpected route ${pathname}` }, 404);
        return;
      }

      const payload = await requestJson(request);
      requests.push({ method: request.method, pathname, payload });

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
        state.agents["existing-manager"] = {
          id: "existing-manager",
          team_id: "existing",
          project_id: null,
          role: "manager",
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
    await new Promise((resolve) => server.close(resolve));
  });
  const address = server.address();
  assert.ok(address && typeof address === "object" && "port" in address);
  return { port: address.port, requests, state };
}

function materializationDuplicateError(state, payload) {
  const ids = [];
  const teamId = payload?.team?.id;
  const projectId = payload?.project?.id;
  const managerAgentId = payload?.team?.manager_agent_id;
  const projectLeadAgentId = payload?.project?.lead_agent_id;
  const implementerAgentId = payload?.agents?.implementer?.agent_id;
  const reviewerAgentId = payload?.agents?.reviewer?.agent_id;
  const releaseOperatorAgentId = payload?.agents?.release_operator?.agent_id;

  if (teamId && state.teams[teamId]) ids.push(teamId);
  if (projectId && state.projects[projectId]) ids.push(projectId);
  if (managerAgentId && state.agents[managerAgentId]) ids.push(managerAgentId);
  if (projectLeadAgentId && state.agents[projectLeadAgentId]) ids.push(projectLeadAgentId);
  if (implementerAgentId && state.agents[implementerAgentId]) ids.push(implementerAgentId);
  if (reviewerAgentId && state.agents[reviewerAgentId]) ids.push(reviewerAgentId);
  if (releaseOperatorAgentId && state.agents[releaseOperatorAgentId]) ids.push(releaseOperatorAgentId);
  if (projectId) {
    const reviewProfileId = materializedTemplateRecordId(projectId, "auto-dev-review");
    const implementTaskId = materializedTemplateRecordId(projectId, "implement-scoped-slice");
    const releaseTaskId = materializedTemplateRecordId(projectId, "release-handoff");
    if (state.review_profiles[reviewProfileId]) ids.push(reviewProfileId);
    if (state.tasks[implementTaskId]) ids.push(implementTaskId);
    if (state.tasks[releaseTaskId]) ids.push(releaseTaskId);
  }
  return ids.length ? `duplicate ids: ${Array.from(new Set(ids)).join(", ")}` : null;
}

function materializedTemplateRecordId(projectId, templateRecordId) {
  return `${projectId}-${templateRecordId}`;
}

function applyTemplateMaterialization(state, payload, template) {
  const team = payload.team || {};
  const project = payload.project || {};
  const agents = payload.agents || {};
  const manager = team.manager_agent_id;
  const projectLead = project.lead_agent_id;
  const implementer = agents.implementer || {};
  const reviewer = agents.reviewer || {};
  const releaseOperator = agents.release_operator || {};

  state.revision += 1;
  state.teams[team.id] = {
    id: team.id,
    name: team.name,
    manager_agent_id: manager,
    operator_communication_target_id: team.operator_communication_target_id ?? null,
    project_ids: [project.id],
    archived: false,
  };
  state.agents[manager] = {
    id: manager,
    team_id: team.id,
    project_id: null,
    role: "manager",
    session_id: team.manager_session_id,
    reports_to_agent_id: null,
    subordinate_agent_ids: [projectLead],
    ephemeral_policy: { max_concurrent: 0 },
    status: "active",
  };
  state.projects[project.id] = {
    id: project.id,
    team_id: team.id,
    name: project.name,
    lead_agent_id: projectLead,
    goals: template.project_goals.map((goal) => ({
      ...goal,
      id: materializedTemplateRecordId(project.id, goal.id),
    })),
    git: project.git || null,
    status: "active",
  };
  state.agents[projectLead] = {
    id: projectLead,
    team_id: team.id,
    project_id: project.id,
    role: "project_lead",
    session_id: project.lead_session_id,
    reports_to_agent_id: manager,
    subordinate_agent_ids: [implementer.agent_id, reviewer.agent_id, releaseOperator.agent_id],
    ephemeral_policy: { max_concurrent: 1 },
    status: "active",
  };
  state.agents[implementer.agent_id] = {
    id: implementer.agent_id,
    team_id: team.id,
    project_id: project.id,
    role: "implementer",
    session_id: implementer.session_id,
    reports_to_agent_id: projectLead,
    subordinate_agent_ids: [],
    ephemeral_policy: { max_concurrent: 2 },
    status: "active",
  };
  state.agents[reviewer.agent_id] = {
    id: reviewer.agent_id,
    team_id: team.id,
    project_id: project.id,
    role: "reviewer",
    session_id: reviewer.session_id,
    reports_to_agent_id: projectLead,
    subordinate_agent_ids: [],
    ephemeral_policy: { max_concurrent: 1 },
    status: "active",
  };
  state.agents[releaseOperator.agent_id] = {
    id: releaseOperator.agent_id,
    team_id: team.id,
    project_id: project.id,
    role: "release_operator",
    session_id: releaseOperator.session_id,
    reports_to_agent_id: projectLead,
    subordinate_agent_ids: [],
    ephemeral_policy: { max_concurrent: 1 },
    status: "active",
  };
  const reviewProfileId = materializedTemplateRecordId(project.id, "auto-dev-review");
  const implementTaskId = materializedTemplateRecordId(project.id, "implement-scoped-slice");
  const releaseTaskId = materializedTemplateRecordId(project.id, "release-handoff");
  state.review_profiles[reviewProfileId] = {
    id: reviewProfileId,
    team_id: team.id,
    project_id: project.id,
    name: "Auto-Dev Delivery Review",
    description:
      "Checks that the slice stays in scope, automated tests exist, risk is understood, and the release handoff is ready.",
    applicable_roles: ["reviewer"],
    criteria: template.review_profiles[0].criteria.map((criterion) => ({ ...criterion })),
    archived: false,
    created_at: "2026-06-09T00:00:00Z",
    updated_at: "2026-06-09T00:00:00Z",
  };
  state.tasks[implementTaskId] = {
    id: implementTaskId,
    owner_agent_id: implementer.agent_id,
    project_id: project.id,
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
    owner_agent_id: releaseOperator.agent_id,
    project_id: project.id,
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
    created_team_id: team.id,
    created_project_id: project.id,
    created_agent_ids: {
      manager: manager,
      project_lead: projectLead,
      implementer: implementer.agent_id,
      reviewer: reviewer.agent_id,
      release_operator: releaseOperator.agent_id,
    },
    created_review_profile_ids: [reviewProfileId],
    created_task_ids: [implementTaskId, releaseTaskId],
    revision: state.revision,
  };
}

function applyMutation(state, pathname, payload) {
  state.revision += 1;
  const decisionMatch = pathname.match(
    /^\/api\/addons\/team-addon\/review-requests\/(.+)\/decision$/,
  );
  if (decisionMatch) {
    const reviewId = decodeURIComponent(decisionMatch[1]);
    const review = state.review_requests[reviewId];
    if (review) {
      review.state = payload.state;
      review.findings = payload.findings || [];
      review.decision_at = "2026-06-09T00:00:00Z";
    }
    return;
  }
  if (pathname === "/api/addons/team-addon/teams") {
    state.teams[payload.id] = {
      id: payload.id,
      name: payload.name,
      manager_agent_id: payload.manager_agent_id,
      operator_communication_target_id: payload.operator_communication_target_id ?? null,
      project_ids: [],
      archived: false,
    };
    state.agents[payload.manager_agent_id] = {
      id: payload.manager_agent_id,
      team_id: payload.id,
      project_id: null,
      role: payload.manager_role || "manager",
      session_id: payload.manager_session_id,
      reports_to_agent_id: null,
      subordinate_agent_ids: [],
      ephemeral_policy: { max_concurrent: 0 },
      status: "active",
    };
    return;
  }
  if (pathname === "/api/addons/team-addon/projects") {
    state.projects[payload.id] = {
      id: payload.id,
      team_id: payload.team_id,
      name: payload.name,
      lead_agent_id: payload.lead_agent_id,
      goals: payload.goals || [],
      git: payload.git || null,
      status: "active",
    };
    state.teams[payload.team_id].project_ids.push(payload.id);
    state.agents[payload.lead_agent_id] = {
      id: payload.lead_agent_id,
      team_id: payload.team_id,
      project_id: payload.id,
      role: payload.lead_role || "project_lead",
      session_id: payload.lead_session_id,
      reports_to_agent_id: state.teams[payload.team_id].manager_agent_id,
      subordinate_agent_ids: [],
      ephemeral_policy: { max_concurrent: 0 },
      status: "active",
    };
    state.agents[state.teams[payload.team_id].manager_agent_id].subordinate_agent_ids.push(
      payload.lead_agent_id,
    );
    return;
  }
  if (pathname === "/api/addons/team-addon/agents") {
    state.agents[payload.id] = {
      id: payload.id,
      team_id: payload.team_id,
      project_id: payload.project_id,
      role: payload.role,
      session_id: payload.session_id,
      reports_to_agent_id: payload.reports_to_agent_id,
      subordinate_agent_ids: [],
      ephemeral_policy: payload.ephemeral_policy || { max_concurrent: 0 },
      status: "active",
    };
    state.agents[payload.reports_to_agent_id].subordinate_agent_ids.push(payload.id);
    return;
  }
  if (pathname === "/api/addons/team-addon/tasks") {
    state.tasks[payload.id] = {
      id: payload.id,
      owner_agent_id: payload.owner_agent_id,
      project_id: payload.project_id,
      priority: payload.priority,
      state: payload.state || "pending",
      title: payload.title,
      description: payload.description,
      created_by_agent_id: payload.created_by_agent_id,
      deliverable_ids: [],
      created_at: "2026-06-09T00:00:00Z",
      updated_at: "2026-06-09T00:00:00Z",
    };
    return;
  }
  if (pathname === "/api/addons/team-addon/tasks/task") {
    Object.assign(state.tasks.task, payload);
    return;
  }
  if (pathname === "/api/addons/team-addon/deliverables") {
    state.deliverables[payload.id] = {
      id: payload.id,
      task_id: payload.task_id,
      producer_agent_id: payload.producer_agent_id,
      artifact_refs: payload.artifact_refs || [],
      summary: payload.summary,
      state: payload.state || "pending",
      review_request_ids: [],
    };
    state.tasks[payload.task_id].deliverable_ids.push(payload.id);
    return;
  }
  if (pathname === "/api/addons/team-addon/review-requests") {
    state.review_requests[payload.id] = {
      id: payload.id,
      deliverable_id: payload.deliverable_id,
      reviewer_agent_id: payload.reviewer_agent_id,
      state: payload.state || "pending",
      findings: payload.findings || [],
      decision_at: null,
    };
    state.deliverables[payload.deliverable_id].review_request_ids.push(payload.id);
  }
}

async function openBrowserPage(t, port) {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext();
  const page = await context.newPage();
  await t.after(async () => {
    await page.close().catch(() => {});
    await context.close().catch(() => {});
    await browser.close().catch(() => {});
  });
  await page.goto(`http://127.0.0.1:${port}/addons/team-addon/`, {
    waitUntil: "domcontentloaded",
  });
  return page;
}

async function waitForSelectOption(page, selector, value) {
  await page.locator(`${selector} option[value="${value}"]`).waitFor({ state: "attached" });
}

async function openDetails(page, name) {
  const summary = page.locator("summary", { hasText: name });
  await summary.click();
}

async function fillAutoDevSquadForm(page) {
  const form = page.locator("#setup-template-form");
  await form.locator('[name="template_id"]').selectOption("auto-dev-squad");
  await form.locator('[name="team_id"]').fill("team-auto-dev-squad");
  await form.locator('[name="team_name"]').fill("Auto-Dev Squad");
  await form.locator('[name="manager_agent_id"]').fill("manager-auto-dev-squad");
  await form.locator('[name="manager_session_id"]').fill("session-manager-auto-dev-squad");
  await form.locator('[name="operator_communication_target_id"]').fill("slack://ops-channel");
  await form.locator('[name="project_id"]').fill("project-auto-dev-squad");
  await form.locator('[name="project_name"]').fill("Reviewed Auto-Dev Delivery");
  await form.locator('[name="project_lead_agent_id"]').fill("project-lead-auto-dev-squad");
  await form.locator('[name="project_lead_session_id"]').fill("session-project-lead-auto-dev-squad");
  await form.locator('[name="host_repo_path"]').fill("/srv/repos/auto-dev-squad");
  await form.locator('[name="bare_repo_name"]').fill("auto-dev-squad.git");
  await form.locator('[name="base_branch"]').fill("main");
  await form.locator('[name="canonical_origin_url"]').fill("ssh://git.example/auto-dev-squad.git");
  await form.locator('[name="provider"]').fill("github");
  await form.locator('[name="canonical_release_branch_prefix"]').fill("releases");
  await form.locator('[name="implementer_agent_id"]').fill("implementer-auto-dev-squad");
  await form.locator('[name="implementer_session_id"]').fill("session-implementer-auto-dev-squad");
  await form.locator('[name="reviewer_agent_id"]').fill("reviewer-auto-dev-squad");
  await form.locator('[name="reviewer_session_id"]').fill("session-reviewer-auto-dev-squad");
  await form.locator('[name="release_operator_agent_id"]').fill("release-operator-auto-dev-squad");
  await form.locator('[name="release_operator_session_id"]').fill("session-release-operator-auto-dev-squad");
}

test("team add-on UI renders the auto-dev squad template preview and materializes it", async (t) => {
  const { port, requests, state } = await createTeamAddonServer(t);
  const page = await openBrowserPage(t, port);
  const statusMessage = page.locator("#status-message");

  await page.getByText("No teams registered.").waitFor({ state: "visible" });
  await page.getByText("Setup from template").waitFor({ state: "visible" });
  await page.getByText("manager -> project_lead -> implementer/reviewer/release_operator").waitFor({
    state: "visible",
  });
  await page.getByText("team.operator_communication_target_id").waitFor({ state: "visible" });
  await page.getByText("Auto-Dev Delivery Review").waitFor({ state: "visible" });
  await page.getByText("Prepare release handoff").waitFor({ state: "visible" });

  const templateSelect = page.locator("#template-select");
  await templateSelect.selectOption("auto-dev-squad");

  await fillAutoDevSquadForm(page);
  await page.locator("#setup-template-form").getByRole("button", { name: "Materialize template" }).click();
  try {
    await page.getByText("Saved revision 1.").waitFor({ state: "visible" });
  } catch (error) {
    throw new Error(
      `${error instanceof Error ? error.message : String(error)}; status=${await statusMessage.textContent()}; requests=${JSON.stringify(requests, null, 2)}`,
    );
  }

  assert.equal(await page.locator(".agents-view .agent-item").count(), 5);
  assert.equal(await page.locator(".projects-view .project-block").count(), 1);
  assert.equal(await page.locator(".review-profiles-view .record-item").count(), 1);
  assert.equal(await page.locator(".projects-view .task-item").count(), 2);

  assert.equal(requests.length, 1);
  assert.equal(requests[0].method, "POST");
  assert.equal(
    requests[0].pathname,
    "/api/addons/team-addon/templates/auto-dev-squad/materialize",
  );
  assert.equal(requests[0].payload.expected_revision, 0);
  assert.deepEqual(requests[0].payload.team, {
    id: "team-auto-dev-squad",
    name: "Auto-Dev Squad",
    manager_agent_id: "manager-auto-dev-squad",
    manager_session_id: "session-manager-auto-dev-squad",
    operator_communication_target_id: "slack://ops-channel",
  });
  assert.deepEqual(requests[0].payload.project, {
    id: "project-auto-dev-squad",
    name: "Reviewed Auto-Dev Delivery",
    lead_agent_id: "project-lead-auto-dev-squad",
    lead_session_id: "session-project-lead-auto-dev-squad",
    git: {
      host_repo_path: "/srv/repos/auto-dev-squad",
      bare_repo_name: "auto-dev-squad.git",
      base_branch: "main",
      canonical_origin_url: "ssh://git.example/auto-dev-squad.git",
      provider: "github",
      canonical_release_branch_prefix: "releases",
    },
  });
  assert.deepEqual(requests[0].payload.agents, {
    implementer: {
      agent_id: "implementer-auto-dev-squad",
      session_id: "session-implementer-auto-dev-squad",
    },
    reviewer: {
      agent_id: "reviewer-auto-dev-squad",
      session_id: "session-reviewer-auto-dev-squad",
    },
    release_operator: {
      agent_id: "release-operator-auto-dev-squad",
      session_id: "session-release-operator-auto-dev-squad",
    },
  });
  assert.equal(state.revision, 1);
  assert.equal(state.teams["team-auto-dev-squad"].project_ids[0], "project-auto-dev-squad");
  assert.equal(
    state.review_profiles["project-auto-dev-squad-auto-dev-review"].criteria[0].id,
    "scope",
  );

  await fillAutoDevSquadForm(page);
  await page.locator("#setup-template-form").getByRole("button", { name: "Materialize template" }).click();
  await page.getByText(/duplicate ids/).waitFor({ state: "visible" });
  assert.equal(await page.locator('#setup-template-form [name="team_id"]').inputValue(), "team-auto-dev-squad");
  assert.equal(
    await page.locator('#setup-template-form [name="manager_session_id"]').inputValue(),
    "session-manager-auto-dev-squad",
  );
  assert.equal(state.revision, 1);
  assert.equal(requests.length, 2);
  assert.equal(requests[1].payload.expected_revision, 1);
});

test("team add-on UI reloads state and preserves form values on revision conflict", async (t) => {
  const { port } = await createTeamAddonServer(t, { conflictOnFirstTeamCreate: true });
  const page = await openBrowserPage(t, port);

  const teamForm = page.locator("#create-team-form");
  await teamForm.locator('[name="id"]').fill("team");
  await teamForm.locator('[name="name"]').fill("Team");
  await teamForm.locator('[name="manager_agent_id"]').fill("manager");
  await teamForm.locator('[name="manager_session_id"]').fill("session-manager");
  await teamForm.getByRole("button", { name: "Create team" }).click();

  await page.getByText(/Revision conflict/).waitFor({ state: "visible" });
  assert.equal(await teamForm.locator('[name="id"]').inputValue(), "team");
  assert.equal(await teamForm.locator('[name="manager_session_id"]').inputValue(), "session-manager");
  await page.locator(".hierarchy-team h3", { hasText: "Existing" }).waitFor({ state: "visible" });
});

function seededGitState() {
  return {
    revision: 4,
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
          host_repo_path: "/srv/git/project",
          bare_repo_name: "project.git",
          base_branch: "main",
        },
        status: "active",
      },
    },
    agents: {
      manager: {
        id: "manager",
        team_id: "team",
        project_id: null,
        role: "manager",
        session_id: "session-manager",
        reports_to_agent_id: null,
        subordinate_agent_ids: ["lead"],
        ephemeral_policy: { max_concurrent: 0 },
        status: "active",
      },
      lead: {
        id: "lead",
        team_id: "team",
        project_id: "project",
        role: "project_lead",
        session_id: "session-lead",
        reports_to_agent_id: "manager",
        subordinate_agent_ids: [],
        ephemeral_policy: { max_concurrent: 0 },
        status: "active",
      },
    },
    tasks: {
      task: {
        id: "task",
        owner_agent_id: "lead",
        project_id: "project",
        priority: 5,
        state: "waiting_for_review",
        title: "Build backend",
        description: "Implement backend work.",
        created_by_agent_id: "manager",
        deliverable_ids: ["deliverable"],
        created_at: "2026-06-09T00:00:00Z",
        updated_at: "2026-06-09T00:00:00Z",
      },
    },
    deliverables: {
      deliverable: {
        id: "deliverable",
        task_id: "task",
        producer_agent_id: "lead",
        artifact_refs: [],
        summary: "Backend task is ready.",
        state: "pending",
        review_request_ids: ["review"],
        git_refs: {
          agent_branch: "agents/session-lead/assignment",
          review_branch: "team/team/reviews/assignment",
          feature_branch: null,
          release_branch: null,
          commit_sha: "abc123",
          promotion_record_ids: ["promo"],
        },
      },
    },
    review_requests: {
      review: {
        id: "review",
        deliverable_id: "deliverable",
        reviewer_agent_id: "lead",
        state: "pending",
        findings: [],
        decision_at: null,
      },
    },
    git_promotions: {
      promo: {
        id: "promo",
        project_id: "project",
        actor_agent_id: "lead",
        source_branch: "agents/session-lead/assignment",
        target_branch: "team/team/reviews/assignment",
        promotion_kind: "review",
        state: "succeeded",
        commit_sha: "abc123",
        outputs: {},
        error: null,
      },
    },
    release_packages: {
      release: {
        id: "release",
        project_id: "project",
        deliverable_ids: ["deliverable"],
        artifact_refs: [],
        feature_branches: ["team/team/features/task-lead"],
        release_branch: "team/team/releases/release",
        canonical_branch: "releases/project/release",
        published_commit_sha: "def456",
        git_promotion_record_ids: [],
        state: "accepted",
        published_at: "2026-06-09T00:00:00Z",
        error: null,
      },
    },
    inbox: {
      "inbox-1": {
        id: "inbox-1",
        recipient_agent_id: "lead",
        item_type: "status_request",
        sender_agent_id: "manager",
        task_id: "task",
        payload: { question: "What is the status?" },
        state: "pending",
      },
    },
  };
}

test("team add-on UI renders git refs and releases and submits a review decision", async (t) => {
  const { port, requests, state } = await createTeamAddonServer(t, {
    initialState: seededGitState(),
  });
  const page = await openBrowserPage(t, port);

  await page.getByText("Loaded revision 4.").waitFor({ state: "visible" });

  // (d) git refs and promotion state render on the deliverable.
  await page.getByText(/team\/team\/reviews\/assignment/).first().waitFor({ state: "visible" });
  await page.getByText(/review:succeeded/).first().waitFor({ state: "visible" });
  // (d) release package state renders in the Releases panel.
  await page.getByText(/team\/team\/releases\/release/).first().waitFor({ state: "visible" });
  await page.locator(".release-list .state-pill", { hasText: "accepted" }).first().waitFor({
    state: "visible",
  });
  // Pending downward status-request inbox item renders for the agent.
  await page.getByText("What is the status?").first().waitFor({ state: "visible" });

  // (c) a review decision submission posts to /review-requests/{id}/decision.
  await openDetails(page, "Decide review");
  await waitForSelectOption(page, '#decide-review-form [name="review_request_id"]', "review");
  const decideForm = page.locator("#decide-review-form");
  await decideForm.locator('[name="review_request_id"]').selectOption("review");
  await decideForm.locator('[name="state"]').selectOption("accepted");
  await decideForm.locator('[name="findings"]').fill("Looks good");
  await decideForm.getByRole("button", { name: "Submit decision" }).click();
  await page.getByText("Saved revision 5.").waitFor({ state: "visible" });

  const decisionRequest = requests.find((request) =>
    request.pathname === "/api/addons/team-addon/review-requests/review/decision",
  );
  assert.ok(decisionRequest, "expected a review decision request");
  assert.equal(decisionRequest.method, "POST");
  assert.equal(decisionRequest.payload.state, "accepted");
  assert.deepEqual(decisionRequest.payload.findings, ["Looks good"]);
  assert.equal(decisionRequest.payload.expected_revision, 4);
  assert.equal(state.review_requests.review.state, "accepted");
});

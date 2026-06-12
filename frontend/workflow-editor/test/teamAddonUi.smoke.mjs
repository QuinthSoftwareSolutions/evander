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
      review.criteria = payload.criteria || [];
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
      display_name: payload.manager_display_name ?? null,
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
      display_name: payload.lead_display_name ?? null,
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
      display_name: payload.display_name ?? null,
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

test("team add-on UI renders empty state and submits setup forms with revisions", async (t) => {
  const { port, requests } = await createTeamAddonServer(t);
  const page = await openBrowserPage(t, port);

  await page
    .locator("#hierarchy-view")
    .getByText("No teams registered.")
    .waitFor({ state: "visible" });

  const teamForm = page.locator("#create-team-form");
  await teamForm.locator('[name="id"]').fill("team");
  await teamForm.locator('[name="name"]').fill("Team");
  await teamForm.locator('[name="manager_agent_id"]').fill("manager");
  await teamForm.locator('[name="manager_session_id"]').fill("session-manager");
  await teamForm.locator('[name="manager_display_name"]').fill("Team Manager");
  await teamForm.locator('[name="operator_communication_target_id"]').fill("ops-channel");
  await teamForm.getByRole("button", { name: "Create team" }).click();
  await page.getByText("Saved revision 1.").waitFor({ state: "visible" });

  await openDetails(page, "Create project");
  await waitForSelectOption(page, '#create-project-form [name="team_id"]', "team");
  const projectForm = page.locator("#create-project-form");
  await projectForm.locator('[name="id"]').fill("project");
  await projectForm.locator('[name="team_id"]').selectOption("team");
  await projectForm.locator('[name="name"]').fill("Project");
  await projectForm.locator('[name="lead_agent_id"]').fill("lead");
  await projectForm.locator('[name="lead_session_id"]').fill("session-lead");
  await projectForm.locator('[name="lead_display_name"]').fill("Project Lead");
  await projectForm.locator('[name="goal_id"]').fill("goal");
  await projectForm.locator('[name="goal_description"]').fill("Ship reviewed work");
  await projectForm.locator('[name="host_repo_path"]').fill("/srv/git/project");
  await projectForm.locator('[name="bare_repo_name"]').fill("project.git");
  await projectForm.locator('[name="base_branch"]').fill("main");
  await projectForm.getByRole("button", { name: "Create project" }).click();
  await page.getByText("Saved revision 2.").waitFor({ state: "visible" });

  await openDetails(page, "Register agent");
  await waitForSelectOption(page, '#register-agent-form [name="reports_to_agent_id"]', "lead");
  const agentForm = page.locator("#register-agent-form");
  await agentForm.locator('[name="id"]').fill("backend");
  await agentForm.locator('[name="team_id"]').selectOption("team");
  await agentForm.locator('[name="project_id"]').selectOption("project");
  await agentForm.locator('[name="role"]').fill("backend");
  await agentForm.locator('[name="session_id"]').fill("session-backend");
  await agentForm.locator('[name="display_name"]').fill("Backend Agent");
  await agentForm.locator('[name="reports_to_agent_id"]').selectOption("lead");
  await agentForm.locator('[name="max_concurrent"]').fill("2");
  await agentForm.getByRole("button", { name: "Register agent" }).click();
  await page.getByText("Saved revision 3.").waitFor({ state: "visible" });
  await page.getByText("Team Manager").first().waitFor({ state: "visible" });
  await page.getByText("Project Lead").first().waitFor({ state: "visible" });
  await page.getByText("Backend Agent").first().waitFor({ state: "visible" });

  await openDetails(page, "Create task");
  await waitForSelectOption(page, '#create-task-form [name="owner_agent_id"]', "backend");
  const taskForm = page.locator("#create-task-form");
  await taskForm.locator('[name="id"]').fill("task");
  await taskForm.locator('[name="owner_agent_id"]').selectOption("backend");
  await taskForm.locator('[name="project_id"]').selectOption("project");
  await taskForm.locator('[name="priority"]').fill("5");
  await taskForm.locator('[name="title"]').fill("Build backend");
  await taskForm.locator('[name="description"]').fill("Implement backend work.");
  await taskForm.locator('[name="created_by_agent_id"]').selectOption("lead");
  await taskForm.getByRole("button", { name: "Create task" }).click();
  await page.getByText("Saved revision 4.").waitFor({ state: "visible" });

  await openDetails(page, "Update task");
  await waitForSelectOption(page, '#update-task-form [name="task_id"]', "task");
  const updateForm = page.locator("#update-task-form");
  await updateForm.locator('[name="task_id"]').selectOption("task");
  await updateForm.locator('[name="state"]').selectOption("waiting_for_review");
  await updateForm.getByRole("button", { name: "Update task" }).click();
  await page.getByText("Saved revision 5.").waitFor({ state: "visible" });

  await openDetails(page, "Record deliverable");
  await waitForSelectOption(page, '#record-deliverable-form [name="task_id"]', "task");
  const deliverableForm = page.locator("#record-deliverable-form");
  await deliverableForm.locator('[name="id"]').fill("deliverable");
  await deliverableForm.locator('[name="task_id"]').selectOption("task");
  await deliverableForm.locator('[name="producer_agent_id"]').selectOption("backend");
  await deliverableForm.locator('[name="summary"]').fill("Backend task is ready.");
  await deliverableForm.locator('[name="artifact_refs"]').fill("artifacts/backend.tar.gz");
  await deliverableForm.getByRole("button", { name: "Record deliverable" }).click();
  await page.getByText("Saved revision 6.").waitFor({ state: "visible" });

  await openDetails(page, "Request review");
  await waitForSelectOption(page, '#record-review-form [name="deliverable_id"]', "deliverable");
  const reviewForm = page.locator("#record-review-form");
  await reviewForm.locator('[name="id"]').fill("review");
  await reviewForm.locator('[name="deliverable_id"]').selectOption("deliverable");
  await reviewForm.locator('[name="reviewer_agent_id"]').selectOption("lead");
  await reviewForm.getByRole("button", { name: "Request review" }).click();
  await page.getByText("Saved revision 7.").waitFor({ state: "visible" });

  assert.deepEqual(
    requests.map((request) => request.payload.expected_revision),
    [0, 1, 2, 3, 4, 5, 6],
  );
  assert.equal(requests[2].payload.ephemeral_policy.max_concurrent, 2);
  assert.deepEqual(requests[5].payload.artifact_refs, ["artifacts/backend.tar.gz"]);
  assert.equal(requests[0].payload.manager_display_name, "Team Manager");
  assert.equal(requests[1].payload.lead_display_name, "Project Lead");
  assert.equal(requests[2].payload.display_name, "Backend Agent");
  assert.equal(requests[0].payload.operator_communication_target_id, "ops-channel");
  assert.deepEqual(requests[1].payload.git, {
    host_repo_path: "/srv/git/project",
    bare_repo_name: "project.git",
    base_branch: "main",
  });
});

test("team add-on UI renders the auto-dev squad template preview and materializes it", async (t) => {
  const { port, requests, state } = await createTeamAddonServer(t);
  const page = await openBrowserPage(t, port);
  const statusMessage = page.locator("#status-message");

  await page.locator("#hierarchy-view").getByText("No teams registered.").waitFor({
    state: "visible",
  });
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

test("team add-on UI renders org charts, details, and hierarchy warnings", async (t) => {
  const { port } = await createTeamAddonServer(t, {
    initialState: seededOrgChartState(),
  });
  const page = await openBrowserPage(t, port);

  await page.getByText("Loaded revision 8.").waitFor({ state: "visible" });

  assert.equal(await page.locator(".hierarchy-team").count(), 1);
  assert.equal(await page.locator(".hierarchy-children").count(), 3);

  const managerCard = page.locator(".hierarchy-node-card").filter({
    has: page.locator(".hierarchy-node-title strong", { hasText: /^Ops Manager$/ }),
  });
  const leadCard = page.locator(".hierarchy-node-card").filter({
    has: page.locator(".hierarchy-node-title strong", { hasText: /^lead$/ }),
  });
  const backendCard = page.locator(".hierarchy-node-card").filter({
    has: page.locator(".hierarchy-node-title strong", { hasText: /^Backend Worker$/ }),
  });
  const workerCard = page.locator(".hierarchy-node-card").filter({
    has: page.locator(".hierarchy-node-title strong", { hasText: /^worker$/ }),
  });

  await managerCard.waitFor({ state: "visible" });
  await leadCard.waitFor({ state: "visible" });
  await backendCard.waitFor({ state: "visible" });
  await workerCard.waitFor({ state: "visible" });

  assert.equal(await page.locator(".hierarchy-node-card--warning").count(), 0);
  assert.equal(await managerCard.locator(".hierarchy-node-title strong").textContent(), "Ops Manager");
  assert.equal(await leadCard.locator(".hierarchy-node-title strong").textContent(), "lead");
  assert.equal(await backendCard.locator(".hierarchy-node-title strong").textContent(), "Backend Worker");
  assert.equal(await workerCard.locator(".hierarchy-node-title strong").textContent(), "worker");
  assert.equal(await managerCard.locator(".hierarchy-node-id").count(), 1);
  assert.equal(await backendCard.locator(".hierarchy-node-id").count(), 1);
  assert.equal(await leadCard.locator(".hierarchy-node-id").count(), 0);
  assert.equal(await workerCard.locator(".hierarchy-node-id").count(), 0);

  await backendCard.hover();
  const backendDetails = backendCard.locator(".hierarchy-node-details");
  await backendDetails.getByText("Agent backend").waitFor({ state: "visible" });
  await backendDetails.getByText("Session session-backend").waitFor({ state: "visible" });
  await backendDetails.getByText("Project Project (project)").waitFor({ state: "visible" });
  await backendDetails.getByText("Reports to lead").waitFor({ state: "visible" });
  await backendDetails.getByText("Direct subordinates 1").waitFor({ state: "visible" });
  await backendDetails.getByText("Open tasks 2").waitFor({ state: "visible" });
  await backendDetails.getByText("Blocked/waiting 1").waitFor({ state: "visible" });
  await backendDetails.getByText("Pending inbox 1").waitFor({ state: "visible" });
  await backendDetails.getByText("Runtime busy - dispatch dispatch-42 - wake poll").waitFor({
    state: "visible",
  });
  await backendDetails.getByText("Ephemeral 1 / 3").waitFor({ state: "visible" });

  await backendCard.focus();
  await backendDetails.getByText("Session session-backend").waitFor({ state: "visible" });

  await page.locator("#agents-view .agent-item", { hasText: "Ops Manager" }).waitFor({
    state: "visible",
  });
  await page.locator("#agents-view .agent-item", { hasText: "Backend Worker" }).waitFor({
    state: "visible",
  });
  assert.equal(await page.locator("#agents-view .agent-item").count(), 4);
  await page.locator("#agents-view").getByText("Build API").waitFor({ state: "visible" });

  const orgChartState = await page.locator("#hierarchy-view").innerText();
  assert.match(orgChartState, /Ops Manager/);
  assert.match(orgChartState, /\blead\b/);
  assert.match(orgChartState, /Backend Worker/);
  assert.match(orgChartState, /\bworker\b/);
});

test("team add-on UI renders hierarchy warnings for missing and cyclical references", async (t) => {
  const { port } = await createTeamAddonServer(t, {
    initialState: seededMalformedHierarchyState(),
  });
  const page = await openBrowserPage(t, port);

  await page.getByText("Loaded revision 3.").waitFor({ state: "visible" });

  await page.getByText("Missing manager ghost-manager").waitFor({ state: "visible" });
  await page.getByText("Cycle detected at alpha").waitFor({ state: "visible" });
  await page.getByText("Missing subordinate missing-subordinate").waitFor({ state: "visible" });
  assert.ok(await page.locator(".hierarchy-node-card--warning").count() >= 2);
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
          host_repo_path: "/srv/git/alpha-project",
          bare_repo_name: "alpha-project.git",
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
          host_repo_path: "/srv/git/beta-project",
          bare_repo_name: "beta-project.git",
          base_branch: "main",
        },
        status: "active",
      },
    },
    agents: {
      "alpha-manager": {
        id: "alpha-manager",
        team_id: "team-alpha",
        project_id: null,
        role: "manager",
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
        role: "project_lead",
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
        role: "manager",
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
        role: "project_lead",
        session_id: "session-beta-lead",
        reports_to_agent_id: "beta-manager",
        subordinate_agent_ids: [],
        ephemeral_policy: { max_concurrent: 0 },
        status: "active",
      },
    },
    tasks: {
      "task-pending": {
        id: "task-pending",
        owner_agent_id: "alpha-lead",
        project_id: "alpha-project",
        priority: 1,
        state: "in_progress",
        title: "Draft pending delivery",
        description: "Pending work in progress.",
        created_by_agent_id: "alpha-manager",
        deliverable_ids: ["deliverable-pending"],
        created_at: "2026-06-09T00:00:00Z",
        updated_at: "2026-06-09T00:00:00Z",
      },
      "task-waiting": {
        id: "task-waiting",
        owner_agent_id: "alpha-lead",
        project_id: "alpha-project",
        priority: 2,
        state: "in_progress",
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
        deliverable_ids: ["deliverable-accepted"],
        artifact_refs: ["artifacts/release-accepted.zip"],
        feature_branches: ["team/alpha/features/accepted"],
        release_branch: "team/alpha/releases/accepted",
        canonical_branch: "releases/alpha/accepted",
        published_commit_sha: "ccc333",
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
    review_profiles: {
      "profile-alpha": {
        id: "profile-alpha",
        team_id: "team-alpha",
        project_id: "alpha-project",
        name: "Alpha review profile",
        description: "Checklist for alpha deliverables.",
        applicable_roles: ["project_lead"],
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
        display_name: "Ops Manager",
        role: "manager",
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
        role: "project_lead",
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
        role: "backend",
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
        role: "worker",
        session_id: "session-worker",
        reports_to_agent_id: "backend",
        subordinate_agent_ids: [],
        ephemeral_policy: { max_concurrent: 0 },
        status: "archived",
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
    agents: {
      alpha: {
        id: "alpha",
        team_id: "broken-team",
        project_id: null,
        role: "manager",
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
        role: "worker",
        session_id: "session-beta",
        reports_to_agent_id: "alpha",
        subordinate_agent_ids: ["alpha", "missing-subordinate"],
        ephemeral_policy: { max_concurrent: 0 },
        status: "active",
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

  const workspace = page.locator(".review-delivery-panel");
  await workspace.getByRole("heading", { name: "Review & Delivery" }).waitFor({
    state: "visible",
  });
  await workspace.getByRole("heading", { name: "Alpha Team (team-alpha)" }).waitFor({
    state: "visible",
  });
  await workspace.getByRole("heading", { name: "Beta Team (team-beta)" }).waitFor({
    state: "visible",
  });
  await workspace.getByRole("heading", { name: "Alpha Project (alpha-project)" }).waitFor({
    state: "visible",
  });
  await workspace.getByRole("heading", { name: "Beta Project (beta-project)" }).waitFor({
    state: "visible",
  });
  assert.equal(await page.locator(".delivery-row").count(), 5);

  const pendingRow = page.locator('.delivery-row[data-deliverable-id="deliverable-pending"]');
  await pendingRow.getByText("deliverable-pending", { exact: true }).first().waitFor({
    state: "visible",
  });
  await pendingRow.getByText("task-pending - Draft pending delivery - in_progress").waitFor({
    state: "visible",
  });
  await pendingRow.getByText("Artifacts: artifacts/pending.tar.gz").waitFor({
    state: "visible",
  });
  await pendingRow.getByText("Promotions: promo-pending review:succeeded").waitFor({
    state: "visible",
  });

  const waitingRow = page.locator('.delivery-row[data-deliverable-id="deliverable-waiting"]');
  await waitingRow.getByText("review-waiting", { exact: true }).first().waitFor({
    state: "visible",
  });
  await waitingRow.getByText("Reviewer id alpha-manager - profile profile-alpha (Alpha review profile)").waitFor({
    state: "visible",
  });
  await waitingRow.getByText("Profile criteria:").waitFor({ state: "visible" });

  const acceptedRow = page.locator('.delivery-row[data-deliverable-id="deliverable-accepted"]');
  await acceptedRow.getByText("release-accepted", { exact: true }).first().waitFor({
    state: "visible",
  });
  await acceptedRow.getByText("Promotions: promo-accepted review:succeeded, promo-release release:succeeded").waitFor({
    state: "visible",
  });
  await acceptedRow.getByText("Release team/alpha/releases/accepted", { exact: true }).first().waitFor({
    state: "visible",
  });
  await acceptedRow.getByText("tests: passed - Targeted tests passed").waitFor({
    state: "visible",
  });
  await acceptedRow.getByText("Findings: Looks good.").waitFor({ state: "visible" });

  const rejectedRow = page.locator('.delivery-row[data-deliverable-id="deliverable-rejected"]');
  await rejectedRow.locator(".state-pill.rejected").first().waitFor({ state: "visible" });
  await rejectedRow.getByText("Findings: Needs more work.").waitFor({ state: "visible" });

  const blockedRow = page.locator('.delivery-row[data-deliverable-id="deliverable-blocked"]');
  await blockedRow.locator(".state-pill.blocked").first().waitFor({ state: "visible" });
  await blockedRow.getByText("Promotions: promo-blocked review:failed").waitFor({
    state: "visible",
  });

  await page.locator("#delivery-filter-pending").click();
  assert.equal(await page.locator(".delivery-row").count(), 1);
  await page.locator('.delivery-row[data-deliverable-id="deliverable-pending"]').waitFor({
    state: "visible",
  });

  await page.locator("#delivery-filter-waiting-for-review").click();
  assert.equal(await page.locator(".delivery-row").count(), 1);
  await page.locator('.delivery-row[data-deliverable-id="deliverable-waiting"]').waitFor({
    state: "visible",
  });

  await page.locator("#delivery-filter-accepted").click();
  assert.equal(await page.locator(".delivery-row").count(), 1);
  await page.locator('.delivery-row[data-deliverable-id="deliverable-accepted"]').waitFor({
    state: "visible",
  });

  await page.locator("#delivery-filter-rejected").click();
  assert.equal(await page.locator(".delivery-row").count(), 1);
  await page.locator('.delivery-row[data-deliverable-id="deliverable-rejected"]').waitFor({
    state: "visible",
  });

  await page.locator("#delivery-filter-blocked").click();
  assert.equal(await page.locator(".delivery-row").count(), 1);
  await page.locator('.delivery-row[data-deliverable-id="deliverable-blocked"]').waitFor({
    state: "visible",
  });

  await page.locator("#delivery-filter-all").click();
  assert.equal(await page.locator(".delivery-row").count(), 5);

  const workspaceDecisionForm = page.locator(
    'form[data-review-decision-form][data-review-request-id="review-waiting"]',
  );
  await workspaceDecisionForm.waitFor({ state: "visible" });
  await workspaceDecisionForm.locator('[name="state"]').selectOption("accepted");
  await workspaceDecisionForm.locator('[name="findings"]').fill("Looks good to me.");
  await workspaceDecisionForm
    .locator('[name="criteria"]')
    .fill("tests | passed | Added acceptance notes\ndocs | passed | README updated");
  await workspaceDecisionForm.getByRole("button", { name: "Submit decision" }).click();
  await page.getByText("Saved revision 5.").waitFor({ state: "visible" });

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
  await waitingRow.getByText("decided 2026-06-09T00:00:00Z").waitFor({ state: "visible" });
  await waitingRow.getByText("Findings: Looks good to me.").waitFor({ state: "visible" });
  await waitingRow.getByText("tests: passed - Added acceptance notes").waitFor({
    state: "visible",
  });
});

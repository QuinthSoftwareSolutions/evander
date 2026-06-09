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
    inbox: {},
    action_records: {},
    release_packages: {},
    ephemeral_assignments: {},
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

async function createTeamAddonServer(t, { conflictOnFirstTeamCreate = false } = {}) {
  const state = emptyState();
  const requests = [];
  let conflictSent = false;

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

function applyMutation(state, pathname, payload) {
  state.revision += 1;
  if (pathname === "/api/addons/team-addon/teams") {
    state.teams[payload.id] = {
      id: payload.id,
      name: payload.name,
      manager_agent_id: payload.manager_agent_id,
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

test("team add-on UI renders empty state and submits setup forms with revisions", async (t) => {
  const { port, requests } = await createTeamAddonServer(t);
  const page = await openBrowserPage(t, port);

  await page.getByText("No teams registered.").waitFor({ state: "visible" });

  const teamForm = page.locator("#create-team-form");
  await teamForm.locator('[name="id"]').fill("team");
  await teamForm.locator('[name="name"]').fill("Team");
  await teamForm.locator('[name="manager_agent_id"]').fill("manager");
  await teamForm.locator('[name="manager_session_id"]').fill("session-manager");
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
  await projectForm.locator('[name="goal_id"]').fill("goal");
  await projectForm.locator('[name="goal_description"]').fill("Ship reviewed work");
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
  await agentForm.locator('[name="reports_to_agent_id"]').selectOption("lead");
  await agentForm.locator('[name="max_concurrent"]').fill("2");
  await agentForm.getByRole("button", { name: "Register agent" }).click();
  await page.getByText("Saved revision 3.").waitFor({ state: "visible" });

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

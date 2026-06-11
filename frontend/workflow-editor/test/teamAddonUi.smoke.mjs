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
    git_promotions: {},
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

test("team add-on UI renders empty state and submits setup forms with revisions", async (t) => {
  const { port, requests } = await createTeamAddonServer(t);
  const page = await openBrowserPage(t, port);

  await page.getByText("No teams registered.").waitFor({ state: "visible" });

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

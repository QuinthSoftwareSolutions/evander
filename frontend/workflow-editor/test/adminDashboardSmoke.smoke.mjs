import assert from "node:assert/strict";
import { fileURLToPath } from "node:url";
import path from "node:path";
import test from "node:test";

import { chromium } from "playwright";
import { createServer } from "vite";

function jsonResponse(payload, status = 200) {
  return {
    status,
    contentType: "application/json",
    body: JSON.stringify(payload),
  };
}

test("admin shell defaults to the dashboard and preserves deep links", async (t) => {
  const projectRoot = path.resolve(fileURLToPath(new URL("..", import.meta.url)));
  const server = await createServer({
    root: projectRoot,
    server: {
      host: "127.0.0.1",
      port: 0,
    },
    logLevel: "error",
  });
  await server.listen();

  const address = server.httpServer?.address();
  assert.ok(address && typeof address === "object" && "port" in address, "expected Vite to bind an ephemeral port");

  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext();
  const page = await context.newPage();
  page.setDefaultTimeout(10000);

  const requestedEndpoints = [];
  const pageErrors = [];
  const consoleErrors = [];

  function assertDashboardCacheParams(url) {
    assert.equal(url.searchParams.get("_dashboard"), "1");
    assert.equal(url.searchParams.has("_dashboard_refresh"), false);
  }

  const sessionRecord = {
    session_id: "session-1",
    vm_id: "vm-1",
    model: "gpt-5.4",
    directory_path: "root",
    allowed_tools: ["prompt_agent"],
    approval_required_tools: ["prompt_agent"],
    allowed_script_names: [],
    discord_channel_allowlist: [],
    teams_channel_allowlist: [],
    communication_file_receive_enabled: false,
    communication_file_send_enabled: false,
    communication_file_max_bytes: null,
    communication_file_allowed_mime_types: [],
    communication_file_allowed_extensions: [],
    communication_file_blocked_extensions: [],
    communication_file_require_approval_for_send: false,
    expires_at: "2026-05-18T12:00:00Z",
    created_at: "2026-05-17T12:00:00Z",
    metadata: {
      wake_schedule_count: 1,
    },
    issue_number: "issue-1",
    is_expired: false,
  };

  const sessionExpiredRecord = {
    ...sessionRecord,
    session_id: "session-expired",
    vm_id: null,
    is_expired: true,
    issue_number: null,
  };

  const sessionResponse = {
    auth_mode: "none",
    auth_required: false,
    authenticated: true,
    username: "admin",
    display_name: "Admin Operator",
    role: "admin",
    can_manage_users: true,
    session_id: "session-123",
    available_login_methods: [],
    azure_sso_login_url: null,
    privileged_operations: {
      unlocked: true,
    },
  };

  const adminStateResponse = {
    selected_directory_path: "root",
    directory_scoped_config_enabled: false,
    execution_backend: "libvirt_vm",
    role: "admin",
    can_manage_users: true,
    privileged_operations: {
      unlocked: true,
    },
    assistant: {
      enabled: false,
      show_launcher_when_disabled: false,
    },
  };

  const rootDirectoryNode = {
    directory: {
      id: "root",
      name: "root",
      parent_id: null,
      created_at: "2026-05-16T00:00:00Z",
      metadata: {},
      path: "root",
    },
    config_domains: [],
    children: [],
  };

  const configurationStateResponse = {
    directory_scoped_config_enabled: false,
    selected_directory_path: "root",
    execution_backend: "libvirt_vm",
    selected_directory: {
      path: "root",
      parent_path: null,
      can_create: false,
      can_delete: false,
    },
    tree: {
      ok: true,
      data: rootDirectoryNode,
      errors: [],
      validation_diagnostics: [],
    },
  };

  const deploymentStatusResponse = {
    active: false,
    message: null,
    source: null,
    started_at: null,
    updated_at: null,
    ttl_seconds: 0,
    stale: false,
  };

  const workersResponse = {
    workers: [
      {
        worker_id: "worker-1",
        name: "Workflow worker",
        unit: "agent-sandbox-worker@1.service",
        active_state: "active",
        sub_state: "running",
        result: null,
        unit_file_state: "enabled",
        description: "Workflow worker",
        main_pid: 1234,
        running: true,
        failed: false,
        detail: null,
      },
      {
        worker_id: "worker-2",
        name: "Approval worker",
        unit: "agent-sandbox-worker@2.service",
        active_state: "failed",
        sub_state: "failed",
        result: "exit-code",
        unit_file_state: "enabled",
        description: "Approval worker",
        main_pid: null,
        running: false,
        failed: true,
        detail: "Exited with status 1",
      },
    ],
  };

  const agentsResponse = {
    items: [
      {
        agent_id: "agent-1",
        backend: "libvirt_vm",
        target_name: "prod-runner",
        session_id: "session-1",
        directory_path: "root",
        status: "running",
        started_at: "2026-05-18T10:00:00Z",
        finished_at: null,
        updated_at: "2026-05-18T10:00:30Z",
        host: {},
        capabilities: {
          start: true,
          shutdown: true,
          resume: true,
          enable_disable: true,
          kill: true,
          logs: true,
          stop: true,
          destroy: true,
        },
      },
      {
        agent_id: "agent-2",
        backend: "container_worker_vm",
        target_name: "audit-runner",
        session_id: "session-2",
        directory_path: "root",
        status: "failed",
        started_at: "2026-05-18T09:00:00Z",
        finished_at: "2026-05-18T09:01:00Z",
        updated_at: "2026-05-18T09:01:00Z",
        host: {},
        capabilities: {
          start: true,
          shutdown: true,
          resume: true,
          enable_disable: true,
          kill: true,
          logs: true,
          stop: true,
          destroy: true,
        },
      },
    ],
    directory_path: "root",
    direct_only: true,
  };

  const workflowRunsResponse = {
    worker_id: "workflow-run-worker",
    generated_at: "2026-05-18T10:02:00Z",
    runs: [
      {
        event_id: "evt-1",
        run_state: "running",
        trace_id: "trace-1",
        source_node_id: "trigger-1",
        source_trigger_type: "trigger:manual",
        delivery_state: "delivered",
        occurred_at: "2026-05-18T10:00:00Z",
        latest_activity_at: "2026-05-18T10:02:00Z",
        active_nodes: [
          {
            node_id: "agent-1",
            status: "running",
            count: 1,
          },
        ],
        resumable: true,
      },
      {
        event_id: "evt-2",
        run_state: "errored",
        trace_id: "trace-2",
        source_node_id: "trigger-2",
        source_trigger_type: "trigger:manual",
        delivery_state: "failed",
        occurred_at: "2026-05-18T09:00:00Z",
        latest_activity_at: "2026-05-18T09:01:00Z",
        active_nodes: [],
        failed_node_id: "agent-2",
        failed_node_status: "failed",
        failed_at: "2026-05-18T09:01:00Z",
        error: "boom",
        resumable: false,
      },
    ],
    node_activity: {},
  };

  const workflowActivityResponse = {
    worker_id: "graph-activity-worker",
    generated_at: "2026-05-18T10:05:00Z",
    period_start: "2026-05-18T09:00:00Z",
    period_end: "2026-05-18T10:05:00Z",
    graphs: [
      {
        graph_id: "graph-1",
        name: "Support flow",
        description: "Primary flow",
        directory_paths: ["root"],
        user_ids: [],
        node_ids: ["trigger-1", "agent-1"],
        node_count: 2,
        run_count: 3,
        activity_count: 5,
        trigger_event_count: 2,
        gate_execution_count: 1,
        effect_execution_count: 2,
        status_counts: {
          running: 1,
          errored: 1,
        },
        first_activity_at: "2026-05-18T09:00:00Z",
        latest_activity_at: "2026-05-18T10:04:00Z",
        nodes: [
          {
            node_id: "agent-1",
            node_type: "effect:agent",
            display_name: "Dispatch agent",
            activity_count: 3,
            status_counts: {
              running: 2,
              errored: 1,
            },
            first_activity_at: "2026-05-18T09:15:00Z",
            latest_activity_at: "2026-05-18T10:04:00Z",
          },
        ],
      },
    ],
  };

  const approvalsResponse = {
    items: [
      {
        request_id: "approval-1",
        session_id: "session-1",
        action_type: "prompt_agent",
        tool_name: "prompt_agent",
        payload_digest: "sha256:abc",
        human_summary: "Dispatch agent",
        directory_path: "root",
        status: "pending",
        created_at: "2026-05-18T09:58:00Z",
        expires_at: "2026-05-18T10:58:00Z",
        approver: null,
        decision_at: null,
        decision_reason: null,
        payload: {},
        decision_history: [],
      },
    ],
    health: {
      total: 1,
      pending: 1,
      approved: 0,
      denied: 0,
      expired: 0,
      oldest_pending_age_seconds: 125,
    },
  };

  const sessionsResponse = {
    items: [sessionRecord, sessionExpiredRecord],
    active_items: [sessionRecord],
    expired_items: [sessionExpiredRecord],
    counts: {
      total: 2,
      active: 1,
      expired: 1,
    },
  };

  const sessionDetailResponse = {
    session: sessionRecord,
    wake_schedules: [
      {
        request_id: "wake-1",
        session_id: "session-1",
        prompt: "Check in",
        cron: "0 * * * *",
        resume_at: null,
        resume_mode: "same_context",
        enabled: true,
        updated_at: "2026-05-18T09:00:00Z",
        schedule_kind: "cron",
      },
    ],
    summary_json: {},
  };

  const sessionCatalogResponse = {
    tool_options: [
      {
        value: "prompt_agent",
        label: "Prompt agent",
      },
    ],
    resume_mode_options: [
      {
        value: "same_context",
        label: "Same context",
      },
    ],
    renew_extension_presets: ["1d"],
  };

  const releaseInfoResponse = {
    version: "0.1.0",
    version_source: "pyproject.toml",
    build: "agent-sandbox-build-20260521",
    build_source: "AGENT_SANDBOX_REPO_VERSION",
    changelog: {
      source: "CHANGELOG.md",
      path: "CHANGELOG.md",
      heading: "0.1.0",
      entries: [
        {
          text: "Added release metadata coverage to the dashboard.",
          kind: "feature",
        },
        {
          text: "Fixed release metadata fallback handling for missing data.",
          kind: "bugfix",
        },
      ],
    },
    missing_reasons: [],
  };

  const workflowDocumentResponse = {
    config_path: "workflow.json",
    layout_path: "workflow-layout.json",
    config_exists: true,
    layout_exists: true,
    config: {
      triggers: [
        {
          id: "trigger-1",
          display_name: "Manual trigger",
          type: "trigger:manual",
        },
      ],
      gates: [],
      effects: [
        {
          id: "agent-1",
          display_name: "Dispatch agent",
          type: "effect:agent",
        },
      ],
      queries: [],
      edges: [
        {
          source_id: "trigger-1",
          target_id: "agent-1",
          source_port: "completed",
        },
      ],
    },
    layout: {
      nodes: {
        "trigger-1": { x: 80, y: 80 },
        "agent-1": { x: 360, y: 80 },
      },
    },
    graphs: [
      {
        id: "graph-1",
        name: "Support flow",
        description: null,
        directory_paths: ["root"],
        user_ids: [],
        node_ids: ["trigger-1", "agent-1"],
      },
    ],
    output_schemas: {},
    validation_diagnostics: [],
    revision: "rev-1",
  };

  const workflowRunDetailResponse = {
    ...workflowRunsResponse.runs[1],
    worker_id: "workflow-run-worker",
    generated_at: "2026-05-18T10:02:00Z",
    matched_by: ["trace_id"],
    lookup_candidates: [
      {
        event_id: "evt-2",
        trace_id: "trace-2",
        latest_activity_at: "2026-05-18T09:01:00Z",
        matched_by: ["trace_id"],
      },
    ],
    recovery: {
      clear: { eligible: true, event_id: "evt-2" },
      resume: { eligible: false, reason: "Run is not resumable.", event_id: "evt-2" },
    },
    trigger_event: {
      event_id: "evt-2",
      trigger_id: "trigger-2",
      trigger_type: "trigger:manual",
      trace_id: "trace-2",
      occurred_at: "2026-05-18T09:00:00Z",
      delivery_state: "failed",
      payload: { command: "fail" },
    },
    graph: {
      config_revision: "rev-1",
      config: workflowDocumentResponse.config,
      snapshot: {
        revision: "rev-1",
        captured_at: "2026-05-18T09:00:00Z",
        snapshot_type: "run_start",
        source: "runtime",
        node_count: 2,
        edge_count: 1,
        triggers: workflowDocumentResponse.config.triggers,
        gates: [],
        effects: workflowDocumentResponse.config.effects,
        queries: [],
        edges: workflowDocumentResponse.config.edges,
      },
      data_availability: {
        graph_snapshot: "available",
        binding_trace: "available",
        approvals: "missing_legacy_or_unrecorded",
        agent_links: "missing_legacy_or_unrecorded",
        communication_artifacts: "missing_legacy_or_unrecorded",
        trigger_event: "available",
      },
    },
    nodes: [
      {
        node_id: "agent-2",
        node_type: "effect:agent",
        display_name: "Audit runner",
        source_kind: "effect",
        execution_id: "exec-2",
        event_id: "evt-2",
        trace_id: "trace-2",
        status: "failed",
        started_at: "2026-05-18T09:00:30Z",
        finished_at: "2026-05-18T09:01:00Z",
        duration_ms: 30000,
        occurred_at: null,
        delivery_state: null,
        resolved_config: {},
        binding_trace: null,
        binding_error: null,
        input_payload: {},
        outputs: {},
        inputs_by_source: {},
        error: "boom",
        published_event_id: null,
        completion_event_enqueued_at: null,
        config_revision: "rev-1",
        retry_resume_eligible: false,
        retry_resume_reason: "Run is not resumable.",
        missing_fields: [],
        approval_links: [],
        agent_links: [],
        communication_artifact_links: [],
        records: [],
      },
    ],
    data_availability: {
      graph_snapshot: "available",
      binding_trace: "available",
      approvals: "missing_legacy_or_unrecorded",
      agent_links: "missing_legacy_or_unrecorded",
      communication_artifacts: "missing_legacy_or_unrecorded",
      trigger_event: "available",
    },
  };

  const workflowCatalogResponse = {
    worker_id: "trigger-orchestrator",
    nodes: [
      {
        kind: "trigger",
        type: "trigger:manual",
        label: "Manual",
        fields: [],
        input_ports: [],
        output_ports: [
          {
            key: "completed",
            label: "Completed",
          },
        ],
      },
      {
        kind: "effect",
        type: "effect:agent",
        label: "Agent",
        fields: [],
        input_ports: [],
        output_ports: [],
      },
    ],
  };

  await t.after(async () => {
    await page.close().catch(() => {});
    await context.close().catch(() => {});
    await browser.close().catch(() => {});
    await server.close().catch(() => {});
  });

  page.on("pageerror", (error) => {
    pageErrors.push(error);
  });
  page.on("console", (message) => {
    if (message.type() === "error") {
      consoleErrors.push(message.text());
    }
  });

  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const { pathname } = url;
    requestedEndpoints.push(`${pathname}${url.search}`);

    if (pathname === "/api/auth/session") {
      await route.fulfill(jsonResponse(sessionResponse));
      return;
    }

    if (pathname === "/api/admin/deployment-status") {
      if (url.searchParams.has("_dashboard")) {
        assertDashboardCacheParams(url);
      }
      await route.fulfill(jsonResponse(deploymentStatusResponse));
      return;
    }

    if (pathname === "/api/admin/state") {
      assert.equal(url.searchParams.get("directory_path"), "root");
      await route.fulfill(jsonResponse(adminStateResponse));
      return;
    }

    if (pathname === "/api/configuration/state") {
      assert.equal(url.searchParams.get("directory_path"), "root");
      await route.fulfill(jsonResponse(configurationStateResponse));
      return;
    }

    if (pathname === "/api/workers") {
      assertDashboardCacheParams(url);
      await route.fulfill(jsonResponse(workersResponse));
      return;
    }

    if (pathname === "/api/admin/agents") {
      assert.equal(url.searchParams.get("directory_path"), "root");
      assert.equal(url.searchParams.get("direct_only"), "true");
      assert.equal(url.searchParams.get("include_inactive"), "true");
      assert.equal(url.searchParams.get("backend"), "all");
      assertDashboardCacheParams(url);
      await route.fulfill(jsonResponse(agentsResponse));
      return;
    }

    if (pathname === "/api/workflow-editor/runs") {
      if (url.searchParams.has("_dashboard")) {
        assertDashboardCacheParams(url);
      }
      await route.fulfill(jsonResponse(workflowRunsResponse));
      return;
    }

    if (pathname === "/api/workflow-editor/runs/stream") {
      await route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        headers: {
          "Cache-Control": "no-cache",
          Connection: "keep-alive",
        },
        body: ": workflow run stream\n\n",
      });
      return;
    }

    if (pathname === "/api/workflow-editor/run-detail") {
      assert.equal(url.searchParams.get("trace_id"), "trace-2");
      await route.fulfill(jsonResponse(workflowRunDetailResponse));
      return;
    }

    if (pathname === "/api/admin/reports/workflow-graph-activity") {
      assert.equal(url.searchParams.get("limit"), "5");
      assertDashboardCacheParams(url);
      await route.fulfill(jsonResponse(workflowActivityResponse));
      return;
    }

    if (pathname === "/api/admin/approvals") {
      assert.equal(url.searchParams.get("statuses"), "pending");
      assert.equal(url.searchParams.get("directory_path"), "root");
      assert.equal(url.searchParams.get("direct_only"), "true");
      assertDashboardCacheParams(url);
      await route.fulfill(jsonResponse(approvalsResponse));
      return;
    }

    if (pathname === "/api/admin/sessions/catalog") {
      await route.fulfill(jsonResponse(sessionCatalogResponse));
      return;
    }

    if (pathname === "/api/admin/sessions") {
      assert.equal(url.searchParams.get("include_expired"), "true");
      assert.equal(url.searchParams.get("directory_path"), "root");
      assert.equal(url.searchParams.get("direct_only"), "true");
      if (url.searchParams.has("_dashboard")) {
        assertDashboardCacheParams(url);
      }
      await route.fulfill(jsonResponse(sessionsResponse));
      return;
    }

    if (pathname === "/api/admin/sessions/session-1") {
      await route.fulfill(jsonResponse(sessionDetailResponse));
      return;
    }

    if (pathname === "/api/workflow-editor/document") {
      if (url.searchParams.has("_dashboard")) {
        assertDashboardCacheParams(url);
      }
      await route.fulfill(jsonResponse(workflowDocumentResponse));
      return;
    }

    if (pathname === "/api/workflow-editor/catalog") {
      assert.equal(url.searchParams.get("directory_path"), "root");
      if (url.searchParams.has("_dashboard")) {
        assertDashboardCacheParams(url);
      }
      await route.fulfill(
        jsonResponse({
          worker_id: "trigger-orchestrator",
          nodes: [],
        }),
      );
      return;
    }

    if (pathname === "/api/admin/tool-calls") {
      assert.equal(url.searchParams.get("directory_path"), "root");
      assert.equal(url.searchParams.get("direct_only"), "true");
      assert.equal(url.searchParams.get("limit"), "5");
      assertDashboardCacheParams(url);
      await route.fulfill(
        jsonResponse({
          items: [],
          counts: {
            returned: 0,
            status_counts: {
              success: 0,
              denied: 0,
              error: 0,
            },
          },
        }),
      );
      return;
    }

    if (pathname === "/api/admin/release-info") {
      assertDashboardCacheParams(url);
      await route.fulfill(jsonResponse(releaseInfoResponse));
      return;
    }

    assert.fail(`unexpected API request: ${pathname}${url.search}`);
  });

  await page.goto(`http://127.0.0.1:${address.port}/`, {
    waitUntil: "domcontentloaded",
  });

  await assert.doesNotReject(() => page.getByRole("heading", { name: "Control-plane status" }).waitFor({ state: "visible" }));
  await assert.doesNotReject(() => page.getByRole("heading", { name: "Live execution targets" }).waitFor({ state: "visible" }));
  const topLevelPanels = page.locator(".dashboard-panel-grid > .dashboard-panel");
  assert.equal(await topLevelPanels.count(), 7);
  assert.equal(
    await topLevelPanels.evaluateAll((elements) =>
      elements.every((element) => element instanceof HTMLDetailsElement && !element.open),
    ),
    true,
  );
  const systemHealthPanel = topLevelPanels.first();
  const workflowPanel = topLevelPanels.filter({ hasText: "Automation activity" });
  const approvalsPanel = topLevelPanels.filter({ hasText: "Human-control work" });
  const toolCallsPanel = topLevelPanels.filter({ hasText: "Explainability shortcuts" });
  const releasePanel = topLevelPanels.filter({ hasText: "Product context" });
  for (const panel of [systemHealthPanel, workflowPanel, approvalsPanel, toolCallsPanel, releasePanel]) {
    await panel.evaluate((element) => {
      if (element instanceof HTMLDetailsElement) {
        element.open = true;
      }
    });
  }
  await assert.doesNotReject(() => systemHealthPanel.locator(".detail-list strong").first().waitFor({ state: "visible" }));
  await assert.doesNotReject(() => workflowPanel.getByText("2 runs, 1 active, 1 failed, 1 resumable.").waitFor({ state: "visible" }));
  const pendingApprovalPill = approvalsPanel.locator(".status-pill").filter({ hasText: "1 pending" }).first();
  await assert.doesNotReject(() => pendingApprovalPill.waitFor({ state: "visible" }));
  await assert.doesNotReject(() => toolCallsPanel.getByText("No tool-call audit entries were returned.").first().waitFor({ state: "visible" }));
  await assert.doesNotReject(() => releasePanel.getByRole("heading", { name: "Version and changelog" }).waitFor({ state: "visible" }));
  await assert.doesNotReject(() => releasePanel.locator(".dashboard-release-summary").waitFor({ state: "visible" }));
  assert.equal((await systemHealthPanel.locator(".detail-list strong").first().textContent())?.trim(), "root");
  const erroredRun = workflowPanel.locator(".node-activity-item").filter({ hasText: "evt-2" });
  const erroredRunStateClass = await erroredRun.locator(".status-pill").last().getAttribute("class");
  assert.ok(erroredRunStateClass?.includes("state-fail"), `expected errored run to use failure tone, got ${erroredRunStateClass}`);
  assert.equal((await pendingApprovalPill.textContent())?.trim(), "1 pending");
  assert.equal((await toolCallsPanel.getByText("No tool-call audit entries were returned.").first().textContent())?.trim(), "No tool-call audit entries were returned.");
  assert.match(await releasePanel.locator(".dashboard-release-summary").innerText(), /Version 0\.1\.0/);
  assert.match(await releasePanel.locator(".dashboard-release-summary").innerText(), /Build agent-sandbox-build-20260521/);
  assert.deepEqual(await releasePanel.locator(".release-kind-pill").allTextContents(), ["Feature", "Bugfix"]);

  assert.equal(await page.evaluate(() => window.location.hash), "");
  assert.deepEqual(pageErrors, []);
  assert.deepEqual(consoleErrors, []);

  await erroredRun.locator(".dashboard-link-chip").filter({ hasText: "trace-2" }).click();
  await assert.doesNotReject(() => page.getByText("Inspected run").waitFor({ state: "visible" }));
  await assert.doesNotReject(() => page.getByText("evt-2").waitFor({ state: "visible" }));
  assert.equal(await page.evaluate(() => window.location.hash), "#workflow?trace_id=trace-2");
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/workflow-editor/run-detail?trace_id=trace-2"));

  await page.goto(`http://127.0.0.1:${address.port}/#sessions`, {
    waitUntil: "domcontentloaded",
  });

  await assert.doesNotReject(() => page.getByRole("heading", { name: "Sessions", level: 1 }).waitFor({ state: "visible" }));
  await assert.doesNotReject(() => page.locator(".session-tools-panel").waitFor({ state: "visible" }));
  assert.equal(await page.getByRole("heading", { name: "Control-plane status" }).count(), 0);
  assert.equal(await page.evaluate(() => window.location.hash), "#sessions");

  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/admin/state?directory_path=root"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/admin/deployment-status?_dashboard=1"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/workers?_dashboard=1"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/configuration/state?directory_path=root"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/admin/agents?directory_path=root&direct_only=true&include_inactive=true&backend=all&_dashboard=1"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/admin/approvals?statuses=pending&directory_path=root&direct_only=true&_dashboard=1"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/admin/sessions/catalog"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/admin/sessions?include_expired=true&directory_path=root&direct_only=true"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/admin/sessions/session-1"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/admin/release-info?_dashboard=1"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/workflow-editor/document?_dashboard=1"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/workflow-editor/catalog?directory_path=root&_dashboard=1"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/admin/tool-calls?directory_path=root&direct_only=true&limit=5&_dashboard=1"));
  assert.deepEqual(pageErrors, []);
  assert.deepEqual(consoleErrors, []);
});

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

async function assertDashboardFitsViewport(page, label) {
  const layout = await page.evaluate(() => {
    const viewportWidth = window.innerWidth;
    const scrollWidth = document.documentElement.scrollWidth;
    const measuredElements = Array.from(
      document.querySelectorAll(
        [
          ".dashboard-page .dashboard-panel",
          ".dashboard-page .dashboard-block",
          ".dashboard-page .dashboard-metric-row",
          ".dashboard-page .dashboard-row-item",
          ".dashboard-page .dashboard-row-item > .status-pill",
          ".dashboard-page .dashboard-link-chip",
          ".dashboard-page .dashboard-disk-card",
          ".dashboard-page .dashboard-agent-icon",
        ].join(", "),
      ),
    );
    const zeroSized = measuredElements
      .filter((element) => element.getClientRects().length > 0)
      .filter((element) => {
        const rect = element.getBoundingClientRect();
        return rect.width <= 0 || rect.height <= 0;
      })
      .map((element) => (element instanceof HTMLElement ? element.className : element.nodeName))
      .slice(0, 5);
    return {
      viewportWidth,
      scrollWidth,
      zeroSized,
    };
  });
  assert.ok(
    layout.scrollWidth <= layout.viewportWidth + 1,
    `${label} dashboard should not create horizontal overflow: scrollWidth=${layout.scrollWidth}, viewport=${layout.viewportWidth}`,
  );
  assert.deepEqual(layout.zeroSized, [], `${label} dashboard compact elements should have visible layout boxes`);
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
  const context = await browser.newContext({ viewport: { width: 1440, height: 1200 } });
  const page = await context.newPage();
  page.setDefaultTimeout(10000);

  const requestedEndpoints = [];
  const dashboardAgentRequests = [];
  const pageErrors = [];
  const consoleErrors = [];

  function assertDashboardCacheParams(url) {
    assert.equal(url.searchParams.get("_dashboard"), "1");
    assert.equal(url.searchParams.has("_dashboard_refresh"), false);
  }

  function assertDashboardAgentInventoryParams(url) {
    assert.ok(["root", "root/team"].includes(url.searchParams.get("directory_path") ?? ""));
    assert.ok(["true", "false"].includes(url.searchParams.get("direct_only") ?? ""));
    assert.equal(url.searchParams.get("include_inactive"), "true");
    assert.equal(url.searchParams.get("backend"), "all");
    assert.equal(url.searchParams.get("summary_only"), "true");
    assert.equal(url.searchParams.get("reconcile_containers"), "false");
    assert.equal(url.searchParams.get("omit_vm_state"), "true");
    assertDashboardCacheParams(url);
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
      {
        worker_id: "worker-3",
        name: "Scheduled wake worker",
        unit: "agent-sandbox-scheduled-wake.service",
        active_state: "active",
        sub_state: "running",
        result: null,
        unit_file_state: "enabled",
        description: "Scheduled wake worker",
        main_pid: 2222,
        running: true,
        failed: false,
        detail: null,
      },
      {
        worker_id: "worker-4",
        name: "Teams ingest worker",
        unit: "agent-sandbox-teams-ingest.service",
        active_state: "inactive",
        sub_state: "dead",
        result: null,
        unit_file_state: "enabled",
        description: "Teams ingest worker",
        main_pid: null,
        running: false,
        failed: false,
        detail: "Waiting for timer",
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
      {
        agent_id: "agent-3",
        backend: "container_worker_vm",
        target_name: "child-runner",
        session_id: "session-3",
        directory_path: "root/team",
        status: "stopped",
        started_at: "2026-05-18T08:00:00Z",
        finished_at: "2026-05-18T08:20:00Z",
        updated_at: "2026-05-18T08:20:00Z",
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
    direct_only: false,
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

  const capacityHealthResponse = {
    generated_at: "2026-05-18T10:06:00Z",
    control_plane: {
      disk: {
        scope: "control_plane_runtime",
        status: "available",
        health: "ok",
        total_bytes: 107374182400,
        used_bytes: 42949672960,
        free_bytes: 64424509440,
        used_percent: 40,
        free_percent: 60,
        error_count: 0,
        errors: [],
      },
      storage: {
        total: {
          scope: "control_plane_known_storage",
          status: "available",
          health: "ok",
          size_bytes: 134217728,
          file_count: 120,
          directory_count: 12,
          symlink_count: 0,
          error_count: 0,
          errors: [],
        },
        areas: [],
      },
    },
    sessions: {
      total_sessions: 2,
      total_agents: 2,
      lifecycle_counts: {
        active: 1,
        retained: 1,
      },
      status_counts: {
        running: 1,
        stopped: 1,
      },
      backend_counts: {
        libvirt_vm: 1,
        container_worker_vm: 1,
      },
      directory_counts: {
        root: 2,
      },
      directory_breakdowns: {},
      age_buckets: {
        lt_1d: 2,
      },
      storage: {
        measured_total: {
          scope: "session_storage:container_worker_vm",
          status: "available",
          health: "ok",
          size_bytes: 52428800,
          file_count: 24,
          directory_count: 6,
          symlink_count: 0,
          error_count: 0,
          errors: [],
        },
        measured_session_count: 1,
        largest_sessions: [
          {
            session_id: "session-1",
            size_bytes: 50331648,
            backend: "container_worker_vm",
            directory_path: "sessions/session-1",
            created_at: "2026-05-17T12:00:00Z",
            updated_at: "2026-05-18T10:00:00Z",
          },
        ],
        oldest_sessions: [],
        missing_telemetry: [],
      },
    },
    workers: {
      total_workers: 2,
      reachable_workers: 1,
      telemetry_available_workers: 1,
      entries: [
        {
          worker_url: "http://worker-a.test:11888",
          directory_paths: ["root"],
          discovered_from: ["config:root"],
          reachable: true,
          last_checked_at: "2026-05-18T10:05:00Z",
          last_heartbeat_at: "2026-05-18T10:05:00Z",
          last_heartbeat_source: "worker_telemetry_live_probe",
          healthz_status: "available",
          telemetry_status: "available",
          healthz: {},
          telemetry: {
            disk: {
              worker_root: {
                scope: "worker_root",
                status: "available",
                health: "warning",
                total_bytes: 107374182400,
                used_bytes: 64424509440,
                free_bytes: 42949672960,
                used_percent: 60,
                free_percent: 40,
                error_count: 0,
                errors: [],
              },
            },
          },
          errors: [],
        },
        {
          worker_url: "http://worker-b.test:11888",
          directory_paths: ["root/team"],
          discovered_from: ["config:root/team"],
          reachable: true,
          last_checked_at: "2026-05-18T10:05:00Z",
          last_heartbeat_at: "2026-05-18T10:05:00Z",
          last_heartbeat_source: "worker_telemetry_live_probe",
          healthz_status: "available",
          telemetry_status: "available",
          healthz: {},
          telemetry: {
            disk: {
              worker_root: {
                scope: "worker_root",
                status: "available",
                health: "critical",
                total_bytes: 107374182400,
                used_bytes: 91268055040,
                free_bytes: 16106127360,
                used_percent: 85,
                free_percent: 15,
                error_count: 0,
                errors: [],
              },
            },
          },
          errors: [],
        },
      ],
    },
    queue: {
      status: "available",
      health: "warning",
      backend: "json",
      active_record_count: 3,
      pending_count: 2,
      inflight_count: 1,
      oldest_event_id: "evt-old",
      oldest_occurred_at: "2026-05-18T08:06:00Z",
      oldest_age_seconds: 7200,
      errors: [],
    },
    prompts: {
      status: "available",
      health: "warning",
      unread_count: 3,
      oldest_message_id: "prompt-old",
      oldest_created_at: "2026-05-18T08:30:00Z",
      oldest_age_seconds: 5400,
      errors: [],
    },
  };

  const releaseInfoResponse = {
    version: "0.2.2",
    version_source: "pyproject.toml",
    build: "agent-sandbox-build-20260521",
    build_source: "AGENT_SANDBOX_REPO_VERSION",
    changelog: {
      source: "CHANGELOG.md",
      path: "CHANGELOG.md",
      heading: "0.2.2",
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
    news: {
      source: "NEWS.md",
      path: "NEWS.md",
      index: 0,
      heading: "Safer workflows, clearer operations",
      blocks: [
        {
          kind: "paragraph",
          text: "The latest dashboard update puts release context next to practical operator guidance.",
          level: null,
          items: [],
          src: null,
          dark_src: null,
          alt: null,
          title: null,
        },
        {
          kind: "heading",
          text: "Practical tips",
          level: 3,
          items: [],
          src: null,
          dark_src: null,
          alt: null,
          title: null,
        },
        {
          kind: "list",
          text: null,
          level: null,
          items: [
            "Open the dashboard first.",
            "Download the changelog when you need the full deployment record.",
          ],
          src: null,
          dark_src: null,
          alt: null,
          title: null,
        },
        {
          kind: "image",
          text: null,
          level: null,
          items: [],
          src: "/api/admin/news/assets/truman-signature.svg",
          dark_src: "/api/admin/news/assets/truman-signature-inverse.svg",
          alt: "Truman signature",
          title: "Signed by Truman",
        },
        {
          kind: "paragraph",
          text: "Use the changelog download when you need the full deployment record, then scan News for the human-readable story.",
          level: null,
          items: [],
          src: null,
          dark_src: null,
          alt: null,
          title: null,
        },
      ],
      has_previous: false,
      has_next: true,
      previous_index: null,
      next_index: 1,
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

    if (pathname === "/api/admin/branding") {
      await route.fulfill(jsonResponse({}));
      return;
    }

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
      assert.ok(["root", "root/team"].includes(url.searchParams.get("directory_path") ?? ""));
      await route.fulfill(jsonResponse(adminStateResponse));
      return;
    }

    if (pathname === "/api/configuration/state") {
      assert.ok(["root", "root/team"].includes(url.searchParams.get("directory_path") ?? ""));
      await route.fulfill(jsonResponse(configurationStateResponse));
      return;
    }

    if (pathname === "/api/workers") {
      assertDashboardCacheParams(url);
      await route.fulfill(jsonResponse(workersResponse));
      return;
    }

    if (pathname === "/api/admin/agents") {
      const directoryPath = url.searchParams.get("directory_path");
      assert.equal(url.searchParams.get("include_inactive"), "true");
      assert.equal(url.searchParams.get("backend"), "all");
      if (url.searchParams.has("_dashboard")) {
        dashboardAgentRequests.push(`${pathname}${url.search}`);
        assertDashboardAgentInventoryParams(url);
        await route.fulfill(jsonResponse(agentsResponse));
      } else {
        assert.equal(directoryPath, "root/team");
        assert.equal(url.searchParams.get("direct_only"), "true");
        await route.fulfill(
          jsonResponse({
            ...agentsResponse,
            items: agentsResponse.items.filter((agent) => agent.directory_path === "root/team"),
            directory_path: "root/team",
            direct_only: true,
          }),
        );
      }
      return;
    }

    if (pathname === "/api/admin/agents/agent-3/log") {
      await route.fulfill(
        jsonResponse({
          agent_id: "agent-3",
          backend: "container_worker_vm",
          lines: 200,
          text: "child agent log\n",
          returned_lines: 1,
          total_lines: 1,
          has_more_before: false,
        }),
      );
      return;
    }

    if (pathname === "/api/operator-steering/context") {
      await route.fulfill(
        jsonResponse({
          session_id: url.searchParams.get("session_id"),
          directory_path: "root/team",
          context: null,
        }),
      );
      return;
    }

    if (pathname === "/api/operator-steering/notes") {
      await route.fulfill(
        jsonResponse({
          session_id: url.searchParams.get("session_id"),
          dispatch_id: url.searchParams.get("dispatch_id"),
          directory_path: "root/team",
          notes: [],
        }),
      );
      return;
    }

    if (pathname === "/api/admin/skills") {
      await route.fulfill(jsonResponse({ items: [] }));
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
      assert.ok(["root", "root/team"].includes(url.searchParams.get("directory_path") ?? ""));
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
      assert.ok(["root", "root/team"].includes(url.searchParams.get("directory_path") ?? ""));
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
      assert.ok(["root", "root/team"].includes(url.searchParams.get("directory_path") ?? ""));
      if (url.searchParams.has("_dashboard")) {
        assertDashboardCacheParams(url);
      }
      await route.fulfill(
        jsonResponse({
          worker_id: workflowCatalogResponse.worker_id,
          nodes: workflowCatalogResponse.nodes,
        }),
      );
      return;
    }

    if (pathname === "/api/admin/tool-calls") {
      assert.ok(["root", "root/team"].includes(url.searchParams.get("directory_path") ?? ""));
      assert.equal(url.searchParams.get("direct_only"), "true");
      assert.equal(url.searchParams.get("limit"), "5");
      assertDashboardCacheParams(url);
      await route.fulfill(
        jsonResponse({
          items: [
            {
              tool_name: "prompt_agent",
              created_at: "2026-05-18T10:01:30Z",
              session_id: "session-1",
              approval_request_id: "approval-1",
              workflow_run_id: "evt-2",
              trace_id: "trace-2",
              correlation_id: "corr-1",
              agent_id: "agent-1",
              vm_id: "vm-1",
              status: "denied",
              decision: "denied",
              error_summary: "Denied by policy",
            },
          ],
          counts: {
            returned: 1,
            status_counts: {
              success: 0,
              denied: 1,
              error: 0,
            },
          },
        }),
      );
      return;
    }

    if (pathname === "/api/admin/capacity-health") {
      assertDashboardCacheParams(url);
      await route.fulfill(jsonResponse(capacityHealthResponse));
      return;
    }

    if (pathname === "/api/admin/release-info") {
      assertDashboardCacheParams(url);
      await route.fulfill(jsonResponse(releaseInfoResponse));
      return;
    }

    if (pathname === "/api/admin/user-preferences/workflow-tutorial-progress") {
      await route.fulfill(
        jsonResponse({
          completed_tutorial_ids: [],
          skipped_tutorial_ids: [],
        }),
      );
      return;
    }

    if (pathname === "/api/admin/scheduled-wakes") {
      assert.equal(url.searchParams.get("session_id"), "session-1");
      assert.equal(url.searchParams.get("limit"), "100");
      assert.equal(url.searchParams.get("include_completed"), "true");
      await route.fulfill(jsonResponse({ items: [] }));
      return;
    }

    if (pathname === "/api/admin/agents/create-options") {
      await route.fulfill(
        jsonResponse({
          directory_path: url.searchParams.get("directory_path") ?? "",
          backends: ["libvirt_vm", "container_worker_vm"],
          agent_providers: [],
          model_provider_modes: [],
          communication_targets: [],
          libvirt: {
            default_template: null,
            templates: [],
          },
          container_worker: {
            default_image: null,
            images: [],
          },
        }),
      );
      return;
    }

    if (pathname.startsWith("/api/admin/news/assets/")) {
      await route.fulfill({
        status: 200,
        contentType: "image/svg+xml",
        body: '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><path d="M1 5h8" stroke="black"/></svg>',
      });
      return;
    }

    assert.fail(`unexpected API request: ${pathname}${url.search}`);
  });

  await page.goto(`http://127.0.0.1:${address.port}/`, {
    waitUntil: "domcontentloaded",
  });

  await assert.doesNotReject(() => page.getByRole("heading", { name: "Capacity and execution" }).waitFor({ state: "visible" }));
  assert.match(dashboardAgentRequests[0], /summary_only=true/);
  assert.match(dashboardAgentRequests[0], /reconcile_containers=false/);
  assert.match(dashboardAgentRequests[0], /omit_vm_state=true/);
  const topLevelPanels = page.locator(".dashboard-panel-grid .dashboard-panel");
  assert.equal(await topLevelPanels.count(), 6);
  const systemHealthPanel = topLevelPanels.filter({ hasText: "System health" });
  const workflowPanel = topLevelPanels.filter({ hasText: "Automation activity" });
  const approvalsPanel = topLevelPanels.filter({ hasText: "Human-control work" });
  const toolCallsPanel = topLevelPanels.filter({ hasText: "Explainability shortcuts" });
  const releasePanel = topLevelPanels.filter({ hasText: "Product context" });
  const onboardingPanel = topLevelPanels.filter({ hasText: "Onboarding / workflow actions" });
  assert.equal(
    await topLevelPanels.evaluateAll((elements) =>
      elements.every((element) =>
        element instanceof HTMLDetailsElement
          ? element.classList.contains("dashboard-release-panel")
            ? element.open
            : !element.open
          : false,
      ),
    ),
    true,
  );
  for (const panel of [systemHealthPanel, workflowPanel, approvalsPanel, toolCallsPanel, onboardingPanel]) {
    await panel.evaluate((element) => {
      if (element instanceof HTMLDetailsElement) {
        element.open = true;
      }
    });
  }
  const releaseBox = await releasePanel.boundingBox();
  const systemHealthBox = await systemHealthPanel.boundingBox();
  assert.ok(releaseBox && systemHealthBox, "expected dashboard panels to have layout boxes");
  assert.ok(releaseBox.x < systemHealthBox.x, `expected release panel to occupy the left dashboard column, got release x=${releaseBox.x} system x=${systemHealthBox.x}`);
  await assertDashboardFitsViewport(page, "desktop");
  const onboardingWorkflowButton = onboardingPanel.getByRole("button", { name: "Open workflow" });
  let previousRightColumnY = -Infinity;
  for (const panel of [systemHealthPanel, workflowPanel, approvalsPanel, toolCallsPanel, onboardingPanel]) {
    const panelBox = await panel.boundingBox();
    assert.ok(panelBox, "expected right-column dashboard panel to have a layout box");
    assert.ok(Math.abs(panelBox.x - systemHealthBox.x) < 2, `expected right-column panels to share x=${systemHealthBox.x}, got ${panelBox.x}`);
    assert.ok(panelBox.y >= previousRightColumnY, `expected right-column panels to stack vertically, got y=${panelBox.y} after ${previousRightColumnY}`);
    previousRightColumnY = panelBox.y;
  }
  assert.equal(await page.getByRole("button", { name: "Refresh dashboard" }).getAttribute("title"), "Refresh dashboard");
  assert.equal(await onboardingWorkflowButton.getAttribute("title"), "Open workflow");
  await assert.doesNotReject(() => systemHealthPanel.locator(".dashboard-row-item").first().waitFor({ state: "visible" }));
  await assert.doesNotReject(() => workflowPanel.getByText("2 runs, 1 active, 1 failed, 1 resumable.").waitFor({ state: "visible" }));
  assert.equal(await systemHealthPanel.locator(".detail-list").count(), 0);
  assert.equal(await systemHealthPanel.locator(".node-activity-item").count(), 0);
  await assert.doesNotReject(() => workflowPanel.locator(".dashboard-metric-row").first().waitFor({ state: "visible" }));
  await assert.doesNotReject(() => approvalsPanel.locator(".dashboard-metric-row").first().waitFor({ state: "visible" }));
  await assert.doesNotReject(() => toolCallsPanel.locator(".dashboard-metric-row").first().waitFor({ state: "visible" }));
  await assert.doesNotReject(() => onboardingPanel.locator(".dashboard-metric-row").first().waitFor({ state: "visible" }));
  assert.equal(await workflowPanel.locator(".detail-list").count(), 0);
  assert.equal(await approvalsPanel.locator(".detail-list").count(), 0);
  assert.equal(await toolCallsPanel.locator(".detail-list").count(), 0);
  assert.equal(await onboardingPanel.locator(".detail-list").count(), 0);
  assert.equal(await workflowPanel.locator(".node-activity-item").count(), 0);
  assert.equal(await approvalsPanel.locator(".node-activity-item").count(), 0);
  assert.equal(await toolCallsPanel.locator(".node-activity-item").count(), 0);
  assert.equal(await onboardingPanel.locator(".node-activity-item").count(), 0);
  await assert.doesNotReject(() => systemHealthPanel.locator(".dashboard-agent-icon").first().waitFor({ state: "visible" }));
  assert.equal(await systemHealthPanel.getByText("Admin state", { exact: true }).count(), 0);
  assert.equal(await systemHealthPanel.getByText("Deployment", { exact: true }).count(), 0);
  const capacityBlock = systemHealthPanel.locator(".dashboard-block-capacity");
  const agentInventoryBlock = systemHealthPanel.locator(".dashboard-block-agent-inventory");
  const workersBlock = systemHealthPanel.locator(".dashboard-block-workers");
  const capacityBox = await capacityBlock.boundingBox();
  const agentInventoryBox = await agentInventoryBlock.boundingBox();
  const workersBox = await workersBlock.boundingBox();
  assert.ok(capacityBox && agentInventoryBox && workersBox, "expected System Health blocks to have layout boxes");
  assert.ok(capacityBox.y < agentInventoryBox.y, "expected Capacity above Agent inventory");
  assert.ok(agentInventoryBox.y < workersBox.y, "expected Agent inventory above Workers");
  assert.equal(await workersBlock.evaluate((element) => element instanceof HTMLDetailsElement && element.open), false);
  assert.equal(await workersBlock.getByText("Workflow worker", { exact: true }).isVisible().catch(() => false), false);
  assert.equal(await systemHealthPanel.locator(".dashboard-disk-dial").count(), 3);
  await assert.doesNotReject(() => systemHealthPanel.locator(".dashboard-disk-dial").filter({ hasText: "40%" }).waitFor({ state: "visible" }));
  await assert.doesNotReject(() => systemHealthPanel.locator(".dashboard-disk-dial.state-running").filter({ hasText: "60%" }).waitFor({ state: "visible" }));
  await assert.doesNotReject(() => systemHealthPanel.locator(".dashboard-disk-dial.state-fail").filter({ hasText: "85%" }).waitFor({ state: "visible" }));
  assert.equal(await systemHealthPanel.locator(".dashboard-agent-icon").count(), 3);
  await assert.doesNotReject(() => systemHealthPanel.getByText("Total 3", { exact: true }).waitFor({ state: "visible" }));
  await assert.doesNotReject(() => systemHealthPanel.getByText("Active 1", { exact: true }).waitFor({ state: "visible" }));
  await assert.doesNotReject(() => systemHealthPanel.getByText("Inactive 2", { exact: true }).waitFor({ state: "visible" }));
  const childAgentButton = systemHealthPanel.getByRole("button", { name: "Open agent child-runner" });
  await childAgentButton.hover();
  await assert.doesNotReject(() => page.getByText("Directory: root/team", { exact: true }).waitFor({ state: "visible" }));
  await workersBlock.evaluate((element) => {
    if (element instanceof HTMLDetailsElement) {
      element.open = true;
    }
  });
  for (const workerName of ["Workflow worker", "Approval worker", "Scheduled wake worker", "Teams ingest worker"]) {
    await assert.doesNotReject(() => workersBlock.locator(".dashboard-row-title").filter({ hasText: workerName }).first().waitFor({ state: "visible" }));
  }
  await assert.doesNotReject(() => workflowPanel.getByText("evt-2", { exact: true }).waitFor({ state: "visible" }));
  await assert.doesNotReject(() => approvalsPanel.getByText("Dispatch agent, root", { exact: true }).waitFor({ state: "visible" }));
  const approvalsRow = approvalsPanel.locator(".dashboard-row-item").filter({ hasText: "Dispatch agent, root" }).first();
  await assert.doesNotReject(() => approvalsRow.waitFor({ state: "visible" }));
  await assert.doesNotReject(() => toolCallsPanel.getByText("prompt_agent", { exact: true }).waitFor({ state: "visible" }));
  await assert.doesNotReject(() => toolCallsPanel.getByText("corr-1, Denied by policy", { exact: true }).waitFor({ state: "visible" }));
  await assert.doesNotReject(() => toolCallsPanel.locator(".dashboard-link-chip").filter({ hasText: "approval-1" }).first().waitFor({ state: "visible" }));
  await assert.doesNotReject(() => toolCallsPanel.locator(".dashboard-link-chip").filter({ hasText: "evt-2" }).first().waitFor({ state: "visible" }));
  assert.ok((await onboardingPanel.locator(".dashboard-row-item").count()) > 0);
  await assert.doesNotReject(() => systemHealthPanel.getByText("Disk / storage", { exact: true }).waitFor({ state: "visible" }));
  await assert.doesNotReject(() => systemHealthPanel.getByText("40% used (40.0 GiB / 100.0 GiB)", { exact: false }).waitFor({ state: "visible" }));
  await assert.doesNotReject(() => systemHealthPanel.getByText("60% (60.0 GiB)", { exact: false }).waitFor({ state: "visible" }));
  await assert.doesNotReject(() => systemHealthPanel.locator(".dashboard-row-detail").filter({ hasText: "Known storage 128.0 MiB" }).first().waitFor({ state: "visible" }));
  await assert.doesNotReject(() => systemHealthPanel.getByText("session-1 · 48.0 MiB · container_worker_vm", { exact: false }).waitFor({ state: "visible" }));
  await assert.doesNotReject(() => systemHealthPanel.getByText("2 pending, 1 in-flight", { exact: true }).waitFor({ state: "visible" }));
  await assert.doesNotReject(() => systemHealthPanel.getByText("3 unread", { exact: true }).waitFor({ state: "visible" }));
  const pendingApprovalPill = approvalsPanel.locator(".status-pill").filter({ hasText: "1 pending" }).first();
  await assert.doesNotReject(() => pendingApprovalPill.waitFor({ state: "visible" }));
  await assert.doesNotReject(() => releasePanel.getByRole("heading", { name: "Version and changelog" }).waitFor({ state: "visible" }));
  await assert.doesNotReject(() => releasePanel.locator(".dashboard-release-summary").waitFor({ state: "visible" }));
  const erroredRun = workflowPanel.locator(".dashboard-row-item").filter({ hasText: "evt-2" });
  const erroredRunStateClass = await erroredRun.locator(".status-pill").last().getAttribute("class");
  assert.ok(erroredRunStateClass?.includes("state-fail"), `expected errored run to use failure tone, got ${erroredRunStateClass}`);
  assert.match(await approvalsRow.innerText(), /session-1/);
  await assert.doesNotReject(() => workflowPanel.locator(".dashboard-link-chip").filter({ hasText: "trace-2" }).waitFor({ state: "visible" }));
  await assert.doesNotReject(() => workflowPanel.getByText("trigger-2, trigger:manual", { exact: true }).waitFor({ state: "visible" }));
  await assert.doesNotReject(() => toolCallsPanel.getByText("1 recent calls, 0 successful, 1 denied, 0 errored.", { exact: true }).waitFor({ state: "visible" }));
  const deniedToolCall = toolCallsPanel.locator(".dashboard-row-item").filter({ hasText: "prompt_agent" });
  const deniedToolCallStateClass = await deniedToolCall.locator(".status-pill").last().getAttribute("class");
  assert.ok(deniedToolCallStateClass?.includes("state-fail"), `expected denied tool call to use failure tone, got ${deniedToolCallStateClass}`);
  assert.equal((await pendingApprovalPill.textContent())?.trim(), "1 pending");
  assert.match(await releasePanel.locator(".dashboard-release-summary").innerText(), /Version 0\.2\.2/);
  assert.match(await releasePanel.locator(".dashboard-release-summary").innerText(), /Build agent-sandbox-build-20260521/);
  await assert.doesNotReject(() => releasePanel.getByRole("button", { name: "Download changelog" }).waitFor({ state: "visible" }));
  assert.equal(await releasePanel.getByText("News", { exact: true }).count(), 0);
  assert.equal(await releasePanel.getByText("News: Safer workflows, clearer operations", { exact: true }).count(), 0);
  await assert.doesNotReject(() => releasePanel.locator(".release-news-title").filter({ hasText: "Safer workflows, clearer operations" }).waitFor({ state: "visible" }));
  await assert.doesNotReject(() => releasePanel.locator(".release-news-subheading").filter({ hasText: "Practical tips" }).waitFor({ state: "visible" }));
  await assert.doesNotReject(() => releasePanel.locator(".release-news-list").filter({ hasText: "Open the dashboard first." }).waitFor({ state: "visible" }));
  await assert.doesNotReject(() => releasePanel.getByAltText("Truman signature").first().waitFor({ state: "attached" }));
  assert.equal(await releasePanel.locator(".release-news-image img").count(), 2);
  assert.equal((await releasePanel.locator(".release-news-image figcaption").textContent())?.trim(), "Signed by Truman");
  await assert.doesNotReject(() => releasePanel.getByRole("button", { name: "Older" }).waitFor({ state: "visible" }));
  assert.equal(await releasePanel.getByText("Entry 1 · Latest", { exact: true }).count(), 1);
  assert.equal(await releasePanel.getByText("Recent entries", { exact: true }).count(), 0);
  assert.equal(await releasePanel.locator(".dashboard-block").count(), 0);
  const newsEntryBox = await releasePanel.locator(".release-news-entry").boundingBox();
  const releaseMetadataBox = await releasePanel.locator(".dashboard-release-secondary").boundingBox();
  assert.ok(newsEntryBox && releaseMetadataBox, "expected news and compact release metadata to have layout boxes");
  assert.ok(newsEntryBox.y < releaseMetadataBox.y, `expected news entry above release metadata, got news y=${newsEntryBox.y} metadata y=${releaseMetadataBox.y}`);

  await page.setViewportSize({ width: 390, height: 900 });
  await assertDashboardFitsViewport(page, "mobile");
  const mobileReleaseBox = await releasePanel.boundingBox();
  const mobileSystemHealthBox = await systemHealthPanel.boundingBox();
  assert.ok(mobileReleaseBox && mobileSystemHealthBox, "expected dashboard panels to keep layout boxes on mobile");
  assert.ok(
    Math.abs(mobileReleaseBox.x - mobileSystemHealthBox.x) < 2,
    `expected mobile dashboard panels to share one column, got release x=${mobileReleaseBox.x} system x=${mobileSystemHealthBox.x}`,
  );

  assert.equal(await page.evaluate(() => window.location.hash), "");
  assert.deepEqual(pageErrors, []);
  assert.deepEqual(consoleErrors, []);

  await erroredRun.locator(".dashboard-link-chip").filter({ hasText: "trace-2" }).click();
  assert.equal(await page.evaluate(() => window.location.hash), "#workflow?trace_id=trace-2");

  await page.goto(`http://127.0.0.1:${address.port}/#sessions`, {
    waitUntil: "domcontentloaded",
  });

  await assert.doesNotReject(() => page.getByRole("heading", { name: "Sessions", level: 1 }).waitFor({ state: "visible" }));
  await assert.doesNotReject(() => page.locator(".session-tools-panel").waitFor({ state: "visible" }));
  assert.equal(await page.getByRole("heading", { name: "Capacity and execution" }).count(), 0);
  assert.equal(await page.evaluate(() => window.location.hash), "#sessions");

  await page.goto(`http://127.0.0.1:${address.port}/`, {
    waitUntil: "domcontentloaded",
  });
  const reloadedSystemHealthPanel = page.locator(".dashboard-panel-grid .dashboard-panel").filter({ hasText: "System health" });
  await reloadedSystemHealthPanel.evaluate((element) => {
    if (element instanceof HTMLDetailsElement) {
      element.open = true;
    }
  });
  await reloadedSystemHealthPanel.getByRole("button", { name: "Open agent child-runner" }).click();
  await assert.doesNotReject(() => page.getByRole("heading", { name: "Agents", level: 1 }).waitFor({ state: "visible" }));
  assert.equal(await page.evaluate(() => window.location.hash), "#agents?agent_id=agent-3&session_id=session-3");

  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/admin/state?directory_path=root"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/admin/deployment-status"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/workers?_dashboard=1"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/configuration/state?directory_path=root"));
  assert.match(dashboardAgentRequests[0], /summary_only=true/);
  assert.match(dashboardAgentRequests[0], /reconcile_containers=false/);
  assert.match(dashboardAgentRequests[0], /omit_vm_state=true/);
  assert.ok(
    requestedEndpoints.some(
      (endpoint) =>
        endpoint.startsWith("/api/admin/agents?") &&
        endpoint.includes("include_inactive=true") &&
        endpoint.includes("backend=all") &&
        endpoint.includes("summary_only=true"),
    ),
  );
  assert.ok(
    requestedEndpoints.some(
      (endpoint) =>
        endpoint.startsWith("/api/admin/agents?") &&
        endpoint.includes("direct_only=true") &&
        endpoint.includes("include_inactive=true") &&
        endpoint.includes("backend=all"),
    ),
  );
  assert.ok(
    requestedEndpoints.some(
      (endpoint) => endpoint.startsWith("/api/admin/approvals?") && endpoint.includes("statuses=pending") && endpoint.includes("direct_only=true"),
    ),
  );
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/admin/sessions/catalog"));
  assert.ok(
    requestedEndpoints.some(
      (endpoint) => endpoint.startsWith("/api/admin/sessions?") && endpoint.includes("include_expired=true") && endpoint.includes("direct_only=true"),
    ),
  );
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/admin/sessions/session-1"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/admin/capacity-health?_dashboard=1"));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint.startsWith("/api/admin/release-info?") && endpoint.includes("news_index=0")));
  assert.ok(requestedEndpoints.some((endpoint) => endpoint === "/api/workflow-editor/document?_dashboard=1"));
  assert.ok(
    requestedEndpoints.some(
      (endpoint) => endpoint.startsWith("/api/workflow-editor/catalog?") && endpoint.includes("directory_path=") && endpoint.includes("_dashboard=1"),
    ),
  );
  assert.ok(
    requestedEndpoints.some(
      (endpoint) => endpoint.startsWith("/api/admin/tool-calls?") && endpoint.includes("direct_only=true") && endpoint.includes("limit=5"),
    ),
  );
  assert.deepEqual(pageErrors, []);
  assert.deepEqual(consoleErrors, []);
});

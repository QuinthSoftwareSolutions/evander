#!/usr/bin/env node

/**
 * Capture the sanitized, deterministic screenshots used by the Evander manuals.
 *
 * The script serves the checked-in Admin Web production bundle and Team Add-on UI,
 * but answers their API calls from the synthetic Northstar fixture below. It never
 * connects to an operator's Admin Web instance or reads live runtime state.
 */

import { createServer } from "node:http";
import { createRequire } from "node:module";
import { copyFile, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const ADMIN_DIST = path.join(ROOT, "frontend", "workflow-editor", "dist");
const TEAM_STATIC = path.join(ROOT, "src", "evander_team_addon", "ui_static");
const RAW_DIR = path.join(ROOT, "output", "playwright", "evander-user-manual-screenshots");
const MANUAL_SCREENSHOT_DIR = path.join(ROOT, "docs", "assets", "evander-user-manual", "screenshots");
const ADMIN_SCREENSHOT_DIR = path.join(ROOT, "docs", "assets", "admin-ui");
const FIXED_NOW = "2026-07-13T12:00:00.000Z";
const DIRECTORY = "root/northstar-platform/mission-control";
const VIEWPORT = { width: 1600, height: 1000 };

const frontendRequire = createRequire(path.join(ROOT, "frontend", "workflow-editor", "package.json"));
const { chromium } = frontendRequire("playwright");

const PERMISSIONS = [
  "admin.owner",
  "groups.manage",
  "team_chats.read_all",
  "users.manage",
  "workflow_packs.manage",
  "workflow_runs.control",
  "workflow_runs.read",
  "workflows.read",
  "workflows.write",
];

const ALL_TEAM_VIEWS = [
  "workspace",
  "operations",
  "delivery",
  "memory",
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

const TIMESTAMPS = {
  created: "2026-07-13T08:00:00Z",
  started: "2026-07-13T09:05:00Z",
  reviewed: "2026-07-13T10:35:00Z",
  updated: "2026-07-13T11:42:00Z",
  expires: "2026-07-14T12:00:00Z",
};

function directoryNode(id, name, parentId, nodePath, children = []) {
  return {
    directory: {
      id,
      name,
      parent_id: parentId,
      created_at: TIMESTAMPS.created,
      metadata: {},
      path: nodePath,
    },
    config_domains: ["policy", "providers", "automation"],
    children,
  };
}

const DIRECTORY_TREE = directoryNode("root", "root", null, "root", [
  directoryNode("northstar-platform", "northstar-platform", "root", "root/northstar-platform", [
    directoryNode(
      "mission-control",
      "mission-control",
      "northstar-platform",
      DIRECTORY,
      [
        directoryNode(
          "telemetry-lab",
          "telemetry-lab",
          "mission-control",
          `${DIRECTORY}/telemetry-lab`,
        ),
      ],
    ),
  ]),
  directoryNode("shared-services", "shared-services", "root", "root/shared-services"),
]);

const SESSION = {
  session_id: "session-imani-telemetry",
  vm_id: "vm-imani-telemetry",
  vm_provider: "libvirt",
  vm_provider_id: "vm-imani-telemetry",
  model: "gpt-5.4",
  directory_path: DIRECTORY,
  allowed_tools: ["get_issue", "prompt_agent", "stage_artifact", "publish_artifact"],
  approval_required_tools: ["publish_artifact"],
  approval_tool_settings: {
    publish_artifact: { ttl_seconds: 3600, max_uses: 1, payload_mode: "exact" },
  },
  approval_notification_target_ids: ["northstar-operators"],
  approval_notification_target_options: [
    { value: "northstar-operators", label: "Northstar operators" },
  ],
  effective_tool_policy: {
    allowed_tools: ["get_issue", "prompt_agent", "stage_artifact", "publish_artifact"],
    inherited_approval_required_tools: ["publish_artifact"],
    approval_required_tools: ["publish_artifact"],
    approval_tool_settings: {
      publish_artifact: { ttl_seconds: 3600, max_uses: 1, payload_mode: "exact" },
    },
    mcp_tool_policies: {},
    tool_argument_policies: {},
  },
  allowed_script_names: ["test-telemetry", "verify-retry-policy"],
  discord_channel_allowlist: [],
  teams_channel_allowlist: ["northstar-platform/mission-control"],
  communication_file_receive_enabled: true,
  communication_file_send_enabled: true,
  communication_file_max_bytes: 25_000_000,
  communication_file_allowed_mime_types: ["application/json", "text/plain"],
  communication_file_allowed_extensions: [".json", ".txt", ".log"],
  communication_file_blocked_extensions: [".exe"],
  communication_file_require_approval_for_send: true,
  web_search_enabled: false,
  expires_at: TIMESTAMPS.expires,
  created_at: TIMESTAMPS.created,
  metadata: {
    work_item: "MC-248",
    purpose: "Telemetry retry behavior",
    team: "northstar-platform",
  },
  runtime_image: {
    backend: "libvirt_vm",
    kind: "libvirt_vm_template",
    label: "Python engineering image",
    template_alias: "python",
    image_ref: "python-agent.qcow2",
    image_ref_sanitized: true,
    missing_reason: null,
  },
  issue_number: "MC-248",
  is_expired: false,
};

const AGENT_CAPABILITIES = {
  start: false,
  shutdown: true,
  resume: true,
  enable_disable: true,
  kill: true,
  logs: true,
  stop: true,
  destroy: true,
};

const ADMIN_AGENTS = [
  {
    agent_id: "agent-imani",
    backend: "libvirt_vm",
    target_name: "Imani - telemetry implementer",
    session_id: SESSION.session_id,
    directory_path: DIRECTORY,
    status: "running",
    started_at: TIMESTAMPS.started,
    finished_at: null,
    updated_at: TIMESTAMPS.updated,
    host: {
      power_state: "running",
      libvirt_state: "running",
      agent: {
        state: "running",
        pid: 4242,
        dispatch_id: "dispatch-telemetry-retry",
        repo_names: ["mission-control"],
        preferred_repo_name: "mission-control",
      },
      labels: { fixture: "northstar", role: "implementer" },
    },
    capabilities: AGENT_CAPABILITIES,
    prompt_queue: {
      record_count: 1,
      coalesced_count: 2,
      oldest_created_at: "2026-07-13T11:20:00Z",
      newest_updated_at: TIMESTAMPS.updated,
      records: [
        {
          queue_key: "team:mission-control:telemetry-retry",
          effect_id: "effect-prompt-imani",
          target: SESSION.session_id,
          vm_name: "vm-imani-telemetry",
          session_id: SESSION.session_id,
          created_at: "2026-07-13T11:20:00Z",
          updated_at: TIMESTAMPS.updated,
          coalesced_count: 2,
          prompt_preview: "Apply Rowan's retry-policy review notes and rerun focused tests.",
          prompt_length: 64,
          metadata: {},
        },
      ],
      truncated_count: 0,
      read_error: null,
    },
  },
  {
    agent_id: "agent-rowan",
    backend: "container_worker_vm",
    target_name: "Rowan - telemetry reviewer",
    session_id: "session-rowan-review",
    directory_path: DIRECTORY,
    status: "idle",
    started_at: "2026-07-13T09:25:00Z",
    finished_at: null,
    updated_at: TIMESTAMPS.reviewed,
    host: { image_alias: "runner", last_status: "succeeded", execution_count: 3 },
    capabilities: { ...AGENT_CAPABILITIES, shutdown: false, resume: false, destroy: false },
  },
  {
    agent_id: "agent-riley",
    backend: "container_worker_vm",
    target_name: "Riley - release operator",
    session_id: "session-riley-release",
    directory_path: DIRECTORY,
    status: "waiting",
    started_at: "2026-07-13T10:00:00Z",
    finished_at: null,
    updated_at: TIMESTAMPS.updated,
    host: { image_alias: "runner", last_status: "waiting", execution_count: 1 },
    capabilities: { ...AGENT_CAPABILITIES, shutdown: false, resume: false, destroy: false },
  },
];

const APPROVAL = {
  request_id: "approval-telemetry-retry",
  session_id: SESSION.session_id,
  action_type: "publish_artifact",
  tool_name: "publish_artifact",
  payload_digest: "sha256:3ed4c72d1a2b08f6",
  human_summary: "Publish the reviewed telemetry retry evidence bundle",
  intended_effect: "Make the sanitized verification report available to the Northstar release operator.",
  directory_path: DIRECTORY,
  vm_id: SESSION.vm_id,
  status: "pending",
  created_at: "2026-07-13T11:45:00Z",
  expires_at: "2026-07-13T12:45:00Z",
  approver: null,
  decision_at: null,
  decision_reason: null,
  payload: {
    artifact_path: "reports/telemetry-retry-verification.json",
    destination: "northstar/mission-control/reviews",
  },
  decision_history: [],
};

const ISSUE = {
  issue_id: "issue-telemetry-retry",
  issue_number: "MC-248",
  title: "Harden telemetry retry behavior",
  body: "Bound retries, preserve the original error, and emit one terminal metric when the retry budget is exhausted.",
  description: "Mission Control loses useful failure context when telemetry delivery reaches its retry limit.",
  reason: "Operator-approved Northstar reliability work.",
  rejection_reason: null,
  yaml_path: "runtime/issues/mission-control/MC-248.yaml",
  directory_path: DIRECTORY,
  managed_repo_id: "mr_northstar_mission_control",
  source_type: "local",
  state: "in_progress",
  url: null,
  tags: ["telemetry", "reliability", "retry"],
  comments: [
    { author: "Priya", body: "Keep the change bounded to retry and terminal telemetry behavior." },
  ],
  source: "local",
  metadata: { owner: "Imani", reviewer: "Rowan", release_operator: "Riley" },
  created_at: TIMESTAMPS.created,
  updated_at: TIMESTAMPS.updated,
  remote_reference: null,
  can_edit: true,
  can_delete: false,
  can_create_vm: false,
};

const ARTIFACTS = [
  {
    key: "northstar/mission-control/telemetry-retry-review",
    storage_id: "artifact-northstar-001",
    archive_format: "tar.gz",
    filename: "telemetry-retry-review.tar.gz",
    mime_type: "application/gzip",
    byte_count: 184_320,
    sha256: "9d58ef7a93ae5bf278a0e40968a7ab9aa1014dfd8bd36e07c7af18acfe091422",
    published_at: TIMESTAMPS.reviewed,
    session_id: SESSION.session_id,
    vm_name: SESSION.vm_id,
    execution_backend: "libvirt_vm",
    source_artifacts_root: "/artifacts",
    source_file_count: 5,
    source_byte_count: 512_000,
    metadata: { work_item: "MC-248", fixture: "northstar", verification: "passed" },
  },
  {
    key: "northstar/mission-control/retry-test-log",
    storage_id: "artifact-northstar-002",
    archive_format: "tar.gz",
    filename: "retry-test-log.tar.gz",
    mime_type: "application/gzip",
    byte_count: 96_128,
    sha256: "6ef2846af8c7755b9bb2a2d1b600ed2076a837f09da024442643712cf81b515f",
    published_at: "2026-07-13T10:20:00Z",
    session_id: SESSION.session_id,
    vm_name: SESSION.vm_id,
    execution_backend: "libvirt_vm",
    source_artifacts_root: "/artifacts",
    source_file_count: 2,
    source_byte_count: 210_944,
    metadata: { test_suite: "telemetry-retry", result: "passed" },
  },
];

const WORKERS = [
  {
    worker_id: "trigger-orchestrator",
    name: "Trigger Orchestrator",
    unit: "evander-trigger-orchestrator.service",
    active_state: "active",
    sub_state: "running",
    result: "success",
    unit_file_state: "enabled",
    description: "Routes Northstar workflow triggers",
    main_pid: 4101,
    running: true,
    failed: false,
    detail: "Healthy; last event 18 seconds ago",
  },
  {
    worker_id: "effect-orchestrator",
    name: "Effect Orchestrator",
    unit: "evander-effect-orchestrator.service",
    active_state: "active",
    sub_state: "running",
    result: "success",
    unit_file_state: "enabled",
    description: "Executes gated workflow effects",
    main_pid: 4102,
    running: true,
    failed: false,
    detail: "Healthy; queue depth 1",
  },
  {
    worker_id: "scheduled-wake",
    name: "Scheduled Wake",
    unit: "evander-scheduled-wake.service",
    active_state: "active",
    sub_state: "running",
    result: "success",
    unit_file_state: "enabled",
    description: "Resumes scheduled agent work",
    main_pid: 4103,
    running: true,
    failed: false,
    detail: "Healthy; next wake 12:15 UTC",
  },
  {
    worker_id: "ado",
    name: "ADO Worker",
    unit: "evander-ado-worker.service",
    active_state: "inactive",
    sub_state: "dead",
    result: "success",
    unit_file_state: "disabled",
    description: "Azure DevOps intake (unused by fixture)",
    main_pid: null,
    running: false,
    failed: false,
    detail: "Not configured for the Northstar fixture",
  },
];

const WORKFLOW_DOCUMENT = {
  config_path: "runtime/workflows/northstar-mission-control.json",
  layout_path: "runtime/workflows/northstar-mission-control.layout.json",
  config_exists: true,
  layout_exists: true,
  config: {
    triggers: [
      { id: "trigger-ticket-created", display_name: "Telemetry work accepted", type: "trigger:ticket:created" },
    ],
    gates: [
      { id: "gate-mission-control", display_name: "Mission Control scope", type: "gate:filter" },
    ],
    effects: [
      { id: "effect-create-imani", display_name: "Create Imani session", type: "effect:agent:create" },
      { id: "effect-ensure-repo", display_name: "Ensure managed repo", type: "effect:git:ensure-managed-repo" },
      { id: "effect-clone-repo", display_name: "Clone mission-control", type: "effect:agent:clone-repo" },
      { id: "effect-prompt-imani", display_name: "Prompt telemetry retry work", type: "effect:agent:prompt" },
    ],
    queries: [],
    edges: [
      { source_id: "trigger-ticket-created", target_id: "gate-mission-control", source_port: "completed" },
      { source_id: "gate-mission-control", target_id: "effect-create-imani", source_port: "matched" },
      { source_id: "effect-create-imani", target_id: "effect-ensure-repo", source_port: "completed" },
      { source_id: "effect-ensure-repo", target_id: "effect-clone-repo", source_port: "completed" },
      { source_id: "effect-clone-repo", target_id: "effect-prompt-imani", source_port: "completed" },
    ],
  },
  layout: {
    nodes: {
      "trigger-ticket-created": { x: 70, y: 170 },
      "gate-mission-control": { x: 330, y: 170 },
      "effect-create-imani": { x: 590, y: 60 },
      "effect-ensure-repo": { x: 590, y: 280 },
      "effect-clone-repo": { x: 850, y: 280 },
      "effect-prompt-imani": { x: 1110, y: 280 },
    },
  },
  graphs: [
    {
      id: "graph-northstar-mission-control",
      name: "Mission Control delivery",
      description: "Turns approved telemetry work into an isolated, reviewed agent run.",
      directory_paths: [DIRECTORY],
      user_ids: [],
      node_ids: [
        "trigger-ticket-created",
        "gate-mission-control",
        "effect-create-imani",
        "effect-ensure-repo",
        "effect-clone-repo",
        "effect-prompt-imani",
      ],
    },
  ],
  output_schemas: {},
  validation_diagnostics: [],
  revision: "northstar-workflow-rev-7",
};

const WORKFLOW_CATALOG = {
  worker_id: "trigger-orchestrator",
  nodes: [
    { kind: "trigger", type: "trigger:ticket:created", label: "Ticket created", fields: [], input_ports: [], output_ports: [{ key: "completed", label: "Completed" }] },
    { kind: "gate", type: "gate:filter", label: "Filter", fields: [], input_ports: [], output_ports: [{ key: "matched", label: "Matched" }, { key: "unmatched", label: "Unmatched" }] },
    { kind: "effect", type: "effect:agent:create", label: "Create agent", fields: [], input_ports: [], output_ports: [{ key: "completed", label: "Completed" }] },
    { kind: "effect", type: "effect:git:ensure-managed-repo", label: "Ensure managed repository", fields: [], input_ports: [], output_ports: [{ key: "completed", label: "Completed" }] },
    { kind: "effect", type: "effect:agent:clone-repo", label: "Clone repository", fields: [], input_ports: [], output_ports: [{ key: "completed", label: "Completed" }] },
    { kind: "effect", type: "effect:agent:prompt", label: "Prompt agent", fields: [], input_ports: [], output_ports: [{ key: "completed", label: "Completed" }] },
  ],
};

const WORKFLOW_RUNS = {
  worker_id: "trigger-orchestrator",
  generated_at: FIXED_NOW,
  runs: [
    {
      event_id: "run-telemetry-retry",
      run_state: "running",
      trace_id: "trace-northstar-248",
      source_node_id: "trigger-ticket-created",
      source_trigger_type: "trigger:ticket:created",
      delivery_state: "delivered",
      occurred_at: TIMESTAMPS.started,
      latest_activity_at: TIMESTAMPS.updated,
      active_nodes: [{ node_id: "effect-prompt-imani", status: "running", count: 1 }],
      resumable: true,
    },
    {
      event_id: "run-telemetry-review",
      run_state: "completed",
      trace_id: "trace-northstar-247",
      source_node_id: "trigger-ticket-created",
      source_trigger_type: "trigger:ticket:created",
      delivery_state: "delivered",
      occurred_at: "2026-07-13T08:15:00Z",
      latest_activity_at: TIMESTAMPS.reviewed,
      active_nodes: [],
      resumable: false,
    },
  ],
  node_activity: {},
};

const GRAPH_ACTIVITY = {
  worker_id: "trigger-orchestrator",
  generated_at: FIXED_NOW,
  period_start: "2026-07-12T12:00:00Z",
  period_end: FIXED_NOW,
  graphs: [
    {
      graph_id: "graph-northstar-mission-control",
      name: "Mission Control delivery",
      description: "Northstar telemetry work delivery and review.",
      directory_paths: [DIRECTORY],
      user_ids: [],
      node_ids: WORKFLOW_DOCUMENT.graphs[0].node_ids,
      node_count: 6,
      run_count: 8,
      activity_count: 43,
      trigger_event_count: 8,
      gate_execution_count: 8,
      effect_execution_count: 27,
      status_counts: { completed: 7, running: 1 },
      first_activity_at: "2026-07-12T13:04:00Z",
      latest_activity_at: TIMESTAMPS.updated,
      nodes: [
        {
          node_id: "effect-prompt-imani",
          node_type: "effect:agent:prompt",
          display_name: "Prompt telemetry retry work",
          activity_count: 8,
          status_counts: { completed: 7, running: 1 },
          first_activity_at: "2026-07-12T13:08:00Z",
          latest_activity_at: TIMESTAMPS.updated,
        },
        {
          node_id: "gate-mission-control",
          node_type: "gate:filter",
          display_name: "Mission Control scope",
          activity_count: 8,
          status_counts: { completed: 8 },
          first_activity_at: "2026-07-12T13:05:00Z",
          latest_activity_at: TIMESTAMPS.started,
        },
      ],
    },
  ],
};

const TOOL_CALLS = {
  items: [
    {
      tool_name: "stage_artifact",
      created_at: TIMESTAMPS.reviewed,
      status: "success",
      decision: "allowed",
      session_id: SESSION.session_id,
      directory_path: DIRECTORY,
      duration_ms: 138,
      role: "automation",
      actor_user_id: "imani",
      request_id: "request-stage-retry-evidence",
      correlation_id: "correlation-northstar-248",
      approval_request_id: null,
      workflow_run_id: "run-telemetry-retry",
      trace_id: "trace-northstar-248",
      agent_id: "agent-imani",
      vm_id: SESSION.vm_id,
      error_summary: null,
      redacted_args: { path: "reports/telemetry-retry-verification.json" },
      redacted_result: { status: "staged", file_count: 1 },
      metadata: { work_item: "MC-248" },
      related_links: [
        { tab: "sessions", label: "Session", value: SESSION.session_id },
        { tab: "workflow", label: "Workflow run", value: "run-telemetry-retry" },
      ],
    },
    {
      tool_name: "publish_artifact",
      created_at: "2026-07-13T11:45:00Z",
      status: "pending_approval",
      decision: "pending_approval",
      session_id: SESSION.session_id,
      directory_path: DIRECTORY,
      duration_ms: 21,
      role: "automation",
      actor_user_id: "imani",
      request_id: "request-publish-retry-evidence",
      correlation_id: "correlation-northstar-248",
      approval_request_id: APPROVAL.request_id,
      workflow_run_id: "run-telemetry-retry",
      trace_id: "trace-northstar-248",
      agent_id: "agent-imani",
      vm_id: SESSION.vm_id,
      error_summary: "Human approval required",
      redacted_args: { artifact_path: "reports/telemetry-retry-verification.json", token: "<redacted>" },
      redacted_result: { status: "pending_approval" },
      metadata: { work_item: "MC-248" },
      related_links: [
        { tab: "approvals", label: "Approval", value: APPROVAL.request_id },
        { tab: "sessions", label: "Session", value: SESSION.session_id },
      ],
    },
  ],
  counts: {
    scanned: 2,
    matched: 2,
    returned: 2,
    skipped_malformed: 0,
    skipped_non_tool_call: 0,
    status_counts: { success: 1, pending_approval: 1, denied: 0, error: 0 },
  },
  limit: 100,
  has_more: false,
};

const CONFIGURATION_TABS = [
  {
    key: "people_communications",
    title: "People & Communications",
    help_text: "Contacts, notification targets, and the communication boundaries inherited by Mission Control.",
    underlying_domains: ["contacts", "communication_targets", "policy"],
    sections: [
      {
        title: "Contacts",
        fields: [
          {
            source_domain: "contacts",
            field_name: "contacts",
            label: "Operator contacts",
            kind: "json",
            options: [],
            scope: "directory",
            root_starts_editable: true,
            tooltip: "Sanitized directory-scoped operator contacts keyed by stable ID.",
          },
        ],
      },
      {
        title: "Approval notification targets",
        fields: [
          {
            source_domain: "policy",
            field_name: "approval_notification_target_ids",
            label: "Approval notification targets",
            kind: "string_list",
            options: [],
            scope: "directory",
            root_starts_editable: true,
            tooltip: "Host-owned targets that receive approval notifications.",
          },
        ],
      },
    ],
  },
  {
    key: "execution_isolation",
    title: "Execution & Isolation",
    help_text: "Execution backend, network isolation, and session policy for this directory.",
    underlying_domains: ["policy", "vm", "providers"],
    sections: [
      {
        title: "Execution defaults",
        fields: [
          {
            source_domain: "providers",
            field_name: "execution_backend",
            label: "Execution backend",
            kind: "literal",
            options: ["libvirt_vm", "container_worker_vm"],
            scope: "directory",
            root_starts_editable: true,
            tooltip: "Isolated execution backend used by agents in this directory.",
          },
        ],
      },
    ],
  },
  {
    key: "models_assistant_knowledge",
    title: "Models, Assistant & Knowledge",
    help_text: "Model routing, pricing metadata, and assistant controls.",
    underlying_domains: ["providers", "policy"],
    sections: [
      {
        title: "Model routing",
        fields: [
          {
            source_domain: "providers",
            field_name: "openai_model",
            label: "Default OpenAI model",
            kind: "text",
            options: [],
            scope: "directory",
            root_starts_editable: true,
            tooltip: "Default model identifier used for new Northstar agent sessions.",
          },
        ],
      },
    ],
  },
];

function tabSnapshot(tab) {
  const values = {
    people_communications: {
      contacts: {
        mira: { name: "Mira", description: "Northstar manager", email_addresses: [], phone_numbers: [] },
        priya: { name: "Priya", description: "Mission Control project lead", email_addresses: [], phone_numbers: [] },
      },
      approval_notification_target_ids: ["northstar-operators"],
    },
    execution_isolation: { execution_backend: "libvirt_vm" },
    models_assistant_knowledge: { openai_model: "gpt-5.4" },
  }[tab.key];
  return {
    key: tab.key,
    title: tab.title,
    revision: `northstar-${tab.key}-rev-1`,
    raw: { exists: true, config: values },
    effective: {
      config: values,
      validation_diagnostics: [],
      errors: [],
      payload: { directory_path: DIRECTORY, config: values },
    },
    parent_defaults: {
      directory_path: "root/northstar-platform",
      config: {},
      payload: {},
    },
  };
}

const CONFIGURATION_STATE = {
  directory_scoped_config_enabled: true,
  selected_directory_path: DIRECTORY,
  execution_backend: "libvirt_vm",
  selected_directory: {
    path: DIRECTORY,
    parent_path: "root/northstar-platform",
    can_create: true,
    can_delete: false,
  },
  tree: { ok: true, data: DIRECTORY_TREE, errors: [], validation_diagnostics: [] },
  directory: {
    ok: true,
    data: { details: { directory: DIRECTORY_TREE.children[0].children[0].directory, ancestry: [] }, children: [], config_domains: ["policy", "providers", "automation"] },
    errors: [],
    validation_diagnostics: [],
  },
  domains: ["contacts", "communication_targets", "policy", "providers", "vm", "automation"],
  tabs: CONFIGURATION_TABS,
  tab_snapshots: Object.fromEntries(CONFIGURATION_TABS.map((tab) => [tab.key, tabSnapshot(tab)])),
  directory_overrides: {
    directory_path: DIRECTORY,
    exists: true,
    domain_count: 3,
    field_count: 4,
    overrides: {
      contacts: tabSnapshot(CONFIGURATION_TABS[0]).raw.config,
      providers: { execution_backend: "libvirt_vm", openai_model: "gpt-5.4" },
    },
    errors: [],
    payload: { fixture: "northstar", directory_path: DIRECTORY },
  },
};

function teamRole(id, projectId, name, permissions = []) {
  return {
    id,
    team_id: "northstar-platform",
    project_id: projectId,
    name,
    description: `${name} role for Mission Control delivery.`,
    permissions,
    ephemeral_policy: { max_concurrent: projectId ? 1 : 0 },
    archived: false,
    created_at: TIMESTAMPS.created,
    updated_at: TIMESTAMPS.updated,
  };
}

function teamAgent(id, displayName, roleId, projectId, reportsTo, subordinates, status = "active") {
  return {
    id,
    display_name: displayName,
    team_id: "northstar-platform",
    project_id: projectId,
    role_id: roleId,
    session_id: `session-${id}`,
    reports_to_agent_id: reportsTo,
    subordinate_agent_ids: subordinates,
    ephemeral_policy: { max_concurrent: roleId.endsWith("implementer") ? 2 : 1 },
    status,
    created_at: TIMESTAMPS.created,
    updated_at: TIMESTAMPS.updated,
  };
}

const TEAM_STATE = {
  revision: 12,
  teams: {
    "northstar-platform": {
      id: "northstar-platform",
      name: "Northstar Platform",
      manager_agent_id: "mira",
      operator_communication_target_id: "northstar-operators",
      project_ids: ["mission-control"],
      max_cost_usd: 250,
      archived: false,
    },
  },
  projects: {
    "mission-control": {
      id: "mission-control",
      team_id: "northstar-platform",
      name: "Mission Control",
      lead_agent_id: "priya",
      goals: [
        { id: "telemetry-resilience", description: "Make telemetry delivery bounded, observable, and safe to retry.", state: "in_progress" },
      ],
      git: { managed_repo_id: "mr_northstar_mission_control", base_branch: "main", provider: "github" },
      status: "active",
      created_at: TIMESTAMPS.created,
      updated_at: TIMESTAMPS.updated,
    },
  },
  roles: {
    "northstar-platform__manager": teamRole("northstar-platform__manager", null, "Manager", ["create_root_task"]),
    "mission-control__project-lead": teamRole("mission-control__project-lead", "mission-control", "Project Lead", ["create_root_task"]),
    "mission-control__implementer": teamRole("mission-control__implementer", "mission-control", "Implementer"),
    "mission-control__reviewer": teamRole("mission-control__reviewer", "mission-control", "Reviewer"),
    "mission-control__release-operator": teamRole("mission-control__release-operator", "mission-control", "Release Operator", ["request_release"]),
  },
  available_role_permissions: ["create_root_task", "request_release"],
  agents: {
    mira: teamAgent("mira", "Mira", "northstar-platform__manager", null, null, ["priya"]),
    priya: teamAgent("priya", "Priya", "mission-control__project-lead", "mission-control", "mira", ["imani", "rowan", "riley"]),
    imani: teamAgent("imani", "Imani", "mission-control__implementer", "mission-control", "priya", [], "active"),
    rowan: teamAgent("rowan", "Rowan", "mission-control__reviewer", "mission-control", "priya", [], "active"),
    riley: teamAgent("riley", "Riley", "mission-control__release-operator", "mission-control", "priya", [], "paused"),
  },
  agent_runtimes: {},
  project_assignments: {},
  tasks: {
    "task-retry-design": {
      id: "task-retry-design",
      owner_agent_id: "priya",
      project_id: "mission-control",
      priority: 5,
      state: "accepted",
      state_entered_at: "2026-07-13T09:00:00Z",
      title: "Bound telemetry retry policy",
      description: "Define retry budget, backoff, terminal metrics, and preserved failure context.",
      created_by_agent_id: "mira",
      dependency_task_ids: [],
      deliverable_ids: ["deliverable-retry-design"],
      created_at: TIMESTAMPS.created,
      updated_at: "2026-07-13T09:00:00Z",
    },
    "task-telemetry-retry": {
      id: "task-telemetry-retry",
      owner_agent_id: "imani",
      project_id: "mission-control",
      priority: 10,
      state: "in_progress",
      state_entered_at: TIMESTAMPS.started,
      title: "Implement telemetry retry behavior",
      description: "Apply bounded exponential backoff and emit one terminal outcome metric.",
      created_by_agent_id: "priya",
      dependency_task_ids: ["task-retry-design"],
      deliverable_ids: ["deliverable-telemetry-retry"],
      created_at: TIMESTAMPS.created,
      updated_at: TIMESTAMPS.updated,
    },
    "task-retry-review": {
      id: "task-retry-review",
      owner_agent_id: "rowan",
      project_id: "mission-control",
      priority: 12,
      state: "waiting_for_review",
      state_entered_at: TIMESTAMPS.reviewed,
      title: "Review retry evidence and failure semantics",
      description: "Confirm tests cover retry exhaustion, original errors, and metric cardinality.",
      created_by_agent_id: "priya",
      dependency_task_ids: ["task-telemetry-retry"],
      deliverable_ids: ["deliverable-telemetry-retry"],
      created_at: TIMESTAMPS.created,
      updated_at: TIMESTAMPS.reviewed,
    },
    "task-release-handoff": {
      id: "task-release-handoff",
      owner_agent_id: "riley",
      project_id: "mission-control",
      priority: 20,
      state: "blocked",
      state_entered_at: "2026-07-13T11:00:00Z",
      title: "Prepare Mission Control release handoff",
      description: "Publish the accepted commit and attach the sanitized verification bundle.",
      created_by_agent_id: "priya",
      dependency_task_ids: ["task-retry-review"],
      blocked_by_task_ids: ["task-retry-review"],
      dependency_status: "blocked",
      deliverable_ids: [],
      created_at: TIMESTAMPS.created,
      updated_at: TIMESTAMPS.updated,
    },
  },
  deliverables: {
    "deliverable-retry-design": {
      id: "deliverable-retry-design",
      task_id: "task-retry-design",
      producer_agent_id: "priya",
      summary: "Approved retry-policy design and acceptance criteria.",
      artifact_refs: ["northstar/mission-control/retry-policy.md"],
      git_refs: { branch: "agents/session-priya/retry-design", commit_sha: "7d41b9c", promotion_record_ids: [] },
      verification: { status: "passed", summary: "Design criteria approved." },
      state: "accepted",
      created_at: "2026-07-13T08:30:00Z",
      updated_at: "2026-07-13T09:00:00Z",
    },
    "deliverable-telemetry-retry": {
      id: "deliverable-telemetry-retry",
      task_id: "task-telemetry-retry",
      producer_agent_id: "imani",
      summary: "Bounded retry implementation with focused tests and terminal metric evidence.",
      artifact_refs: ["northstar/mission-control/telemetry-retry-review"],
      git_refs: {
        branch: "agents/session-imani-telemetry/telemetry-retry",
        commit_sha: "c91e2a7",
        promotion_record_ids: ["promotion-telemetry-candidate"],
      },
      verification: { status: "passed", summary: "18 focused tests passed in 4.2 seconds." },
      state: "waiting_for_review",
      created_at: "2026-07-13T10:10:00Z",
      updated_at: TIMESTAMPS.reviewed,
    },
  },
  review_requests: {
    "review-telemetry-retry": {
      id: "review-telemetry-retry",
      deliverable_id: "deliverable-telemetry-retry",
      reviewer_agent_id: "rowan",
      review_profile_id: "profile-telemetry-delivery",
      state: "pending",
      requested_at: TIMESTAMPS.reviewed,
      updated_at: TIMESTAMPS.updated,
      evidence: [],
      decision: null,
    },
  },
  review_profiles: {
    "profile-telemetry-delivery": {
      id: "profile-telemetry-delivery",
      team_id: "northstar-platform",
      project_id: "mission-control",
      name: "Telemetry Delivery Review",
      description: "Checks retry bounds, preserved errors, focused tests, and release evidence.",
      applicable_role_ids: ["mission-control__reviewer"],
      criteria: [
        { id: "bounds", label: "Retry bounds", description: "Retries stop at the configured budget.", required: true },
        { id: "error", label: "Failure context", description: "The original error remains inspectable.", required: true },
        { id: "tests", label: "Focused tests", description: "Retry and exhaustion tests pass.", required: true },
      ],
      archived: false,
    },
  },
  governance_groups: {
    "northstar-reliability": {
      id: "northstar-reliability",
      name: "Northstar Reliability",
      manager_agent_id: "mira",
      member_team_ids: ["northstar-platform"],
      goals: [{ id: "safe-delivery", description: "Keep automated delivery observable and reversible." }],
      max_cost_usd: 500,
      archived: false,
    },
  },
  governance_agenda: {
    "agenda-release-wait": {
      id: "agenda-release-wait",
      governance_group_id: "northstar-reliability",
      item_type: "blocked_team",
      priority: "normal",
      team_id: "northstar-platform",
      project_id: "mission-control",
      agent_id: "riley",
      title: "Release waits for Rowan's review",
      summary: "The release handoff remains intentionally blocked until telemetry evidence is accepted.",
      payload: {},
      state: "pending",
      created_at: "2026-07-13T11:00:00Z",
      updated_at: TIMESTAMPS.updated,
    },
  },
  governance_runtimes: {},
  inbox: {
    "inbox-status-retry": {
      id: "inbox-status-retry",
      recipient_agent_id: "imani",
      item_type: "status_request",
      sender_agent_id: "priya",
      task_id: "task-telemetry-retry",
      priority: "normal",
      payload: { question: "Are the retry-exhaustion tests ready for Rowan?" },
      state: "pending",
      created_at: "2026-07-13T11:25:00Z",
      updated_at: "2026-07-13T11:25:00Z",
    },
  },
  action_records: {},
  release_packages: {
    "release-telemetry-retry": {
      id: "release-telemetry-retry",
      project_id: "mission-control",
      deliverable_ids: ["deliverable-telemetry-retry"],
      artifact_refs: ["northstar/mission-control/telemetry-retry-review"],
      feature_branches: ["agents/session-imani-telemetry/telemetry-retry"],
      release_branch: "team/northstar-platform/releases/telemetry-retry",
      canonical_branch: "main",
      published_commit_sha: null,
      verification_waivers: [],
      scope_validation: { state: "passed", unexpected_commit_shas: [], unexpected_paths: [] },
      git_promotion_record_ids: ["promotion-telemetry-candidate"],
      state: "pending",
      created_at: "2026-07-13T11:00:00Z",
      updated_at: TIMESTAMPS.updated,
      error: null,
    },
  },
  ephemeral_assignments: {},
  git_promotions: {
    "promotion-telemetry-candidate": {
      id: "promotion-telemetry-candidate",
      project_id: "mission-control",
      actor_agent_id: "imani",
      source_branch: "agents/session-imani-telemetry/telemetry-retry",
      target_branch: "team/northstar-platform/candidates/telemetry-retry",
      promotion_kind: "candidate",
      state: "succeeded",
      commit_sha: "c91e2a7",
      outputs: {},
      error: null,
    },
  },
  stand_downs: {},
  cases: {},
  memories: {},
  resource_tokens: {},
  runtime_operations: {
    generated_at: FIXED_NOW,
    agents: [
      {
        id: "imani",
        team_id: "northstar-platform",
        project_id: "mission-control",
        session_id: SESSION.session_id,
        session_is_expired: false,
        session_expires_at: TIMESTAMPS.expires,
        session_status: "active",
        wake_due: true,
        lease_active: true,
        active_dispatch_id: "dispatch-telemetry-retry",
        wake_reason: "status_request",
        last_wake_at: "2026-07-13T11:20:00Z",
        next_wake_at: "2026-07-13T12:15:00Z",
        wake_leased_until: "2026-07-13T12:05:00Z",
        no_progress_count: 0,
        context_clearing_policy: "idle",
        context_rotation_pending: false,
        auto_context_clearing_enabled: true,
        last_context_rotated_at: "2026-07-13T09:00:00Z",
      },
    ],
    attention: [
      {
        id: "attention-release-wait",
        kind: "blocked_task",
        severity: "warning",
        title: "Release handoff waits for review",
        team_id: "northstar-platform",
        project_id: "mission-control",
        agent_id: "riley",
        task_id: "task-release-handoff",
        state: "blocked",
        detail: "Rowan must accept deliverable-telemetry-retry before Riley can publish.",
      },
      {
        id: "attention-imani-status",
        kind: "pending_inbox_item",
        severity: "info",
        title: "Priya requested implementation status",
        team_id: "northstar-platform",
        project_id: "mission-control",
        agent_id: "imani",
        task_id: "task-telemetry-retry",
        state: "pending",
        detail: "One status request is waiting in Imani's inbox.",
      },
    ],
    session_health: [
      {
        agent_id: "imani",
        team_id: "northstar-platform",
        project_ids: ["mission-control"],
        lead_project_ids: [],
        role_id: "mission-control__implementer",
        session_id: SESSION.session_id,
        session_present: true,
        expires_at: TIMESTAMPS.expires,
        is_expired: false,
        status: "active",
      },
    ],
  },
  conversations: {
    "conversation-imani": {
      id: "conversation-imani",
      agent_id: "imani",
      created_by: "admin",
      last_message_preview: "Focused retry tests pass. Rowan has the evidence bundle.",
      created_at: "2026-07-13T09:10:00Z",
      updated_at: TIMESTAMPS.updated,
      last_interaction_at: TIMESTAMPS.updated,
    },
    "conversation-rowan": {
      id: "conversation-rowan",
      agent_id: "rowan",
      created_by: "admin",
      last_message_preview: "I am checking the exhaustion-path metric now.",
      created_at: "2026-07-13T10:30:00Z",
      updated_at: "2026-07-13T11:38:00Z",
      last_interaction_at: "2026-07-13T11:38:00Z",
    },
  },
  conversation_messages: {
    "message-imani-1": {
      id: "message-imani-1",
      conversation_id: "conversation-imani",
      sender: "operator",
      message_type: "text",
      body: "How is the telemetry retry change progressing?",
      status: "sent",
      dispatch_id: null,
      voice_session_id: null,
      source_message_id: null,
      error: null,
      metadata: { operator_display_name: "Northstar operator" },
      created_at: "2026-07-13T11:30:00Z",
      updated_at: "2026-07-13T11:30:00Z",
    },
    "message-imani-2": {
      id: "message-imani-2",
      conversation_id: "conversation-imani",
      sender: "agent",
      message_type: "text",
      body: "The retry budget is bounded at four attempts and the original delivery error is preserved.",
      status: "sent",
      dispatch_id: "dispatch-telemetry-retry",
      voice_session_id: null,
      source_message_id: null,
      error: null,
      metadata: {},
      created_at: "2026-07-13T11:36:00Z",
      updated_at: "2026-07-13T11:36:00Z",
    },
    "message-imani-3": {
      id: "message-imani-3",
      conversation_id: "conversation-imani",
      sender: "operator",
      message_type: "text",
      body: "Please stage the focused test report for Rowan.",
      status: "sent",
      dispatch_id: null,
      voice_session_id: null,
      source_message_id: null,
      error: null,
      metadata: { operator_display_name: "Northstar operator" },
      created_at: "2026-07-13T11:38:00Z",
      updated_at: "2026-07-13T11:38:00Z",
    },
    "message-imani-4": {
      id: "message-imani-4",
      conversation_id: "conversation-imani",
      sender: "agent",
      message_type: "text",
      body: "Focused retry tests pass. Rowan has the evidence bundle.",
      status: "sent",
      dispatch_id: "dispatch-telemetry-retry",
      voice_session_id: null,
      source_message_id: null,
      error: null,
      metadata: {},
      created_at: TIMESTAMPS.updated,
      updated_at: TIMESTAMPS.updated,
    },
    "message-rowan-1": {
      id: "message-rowan-1",
      conversation_id: "conversation-rowan",
      sender: "agent",
      message_type: "text",
      body: "I am checking the exhaustion-path metric now.",
      status: "sent",
      dispatch_id: "dispatch-rowan-review",
      voice_session_id: null,
      source_message_id: null,
      error: null,
      metadata: {},
      created_at: "2026-07-13T11:38:00Z",
      updated_at: "2026-07-13T11:38:00Z",
    },
  },
};

function chatMessages(conversationId) {
  return Object.values(TEAM_STATE.conversation_messages)
    .filter((message) => message.conversation_id === conversationId)
    .sort((left, right) => left.created_at.localeCompare(right.created_at));
}

function chatAgent(agentId) {
  const agent = TEAM_STATE.agents[agentId];
  if (!agent) return null;
  return {
    id: agent.id,
    display_name: agent.display_name,
    team_id: agent.team_id,
    project_id: agent.project_id,
    role_id: agent.role_id,
    role_name: TEAM_STATE.roles[agent.role_id]?.name ?? null,
    status: agent.status,
    session_id: agent.session_id,
    avatar: null,
  };
}

function chatConversation(conversation, includeMessages = false) {
  const messages = chatMessages(conversation.id);
  const payload = {
    id: conversation.id,
    agent_id: conversation.agent_id,
    agent: chatAgent(conversation.agent_id),
    created_by: conversation.created_by,
    last_message_preview: conversation.last_message_preview,
    created_at: conversation.created_at,
    updated_at: conversation.updated_at,
    last_interaction_at: conversation.last_interaction_at,
    message_count: messages.length,
    newest_message_id: messages.at(-1)?.id ?? null,
    newest_message_sender: messages.at(-1)?.sender ?? null,
  };
  if (includeMessages) {
    Object.assign(payload, {
      messages,
      messages_limit: 50,
      has_older: false,
      oldest_message_id: messages[0]?.id ?? null,
      newest_message_id: messages.at(-1)?.id ?? null,
    });
  }
  return payload;
}

function chatCollection(selectedConversationId = null, includeSelectedMessages = false) {
  const conversations = Object.values(TEAM_STATE.conversations).sort((a, b) => b.last_interaction_at.localeCompare(a.last_interaction_at));
  const selected = conversations.some((item) => item.id === selectedConversationId)
    ? selectedConversationId
    : includeSelectedMessages
      ? conversations[0]?.id ?? null
      : selectedConversationId;
  return {
    revision: TEAM_STATE.revision,
    current_operator: { user_id: "northstar-operator", display_name: "Northstar Operator" },
    selected_operator: { user_id: "northstar-operator", display_name: "Northstar Operator" },
    can_view_all_operators: true,
    operator_options: [{ user_id: "northstar-operator", display_name: "Northstar Operator" }],
    read_only: false,
    selected_conversation_id: selected,
    eligible_agents: Object.keys(TEAM_STATE.agents).map(chatAgent),
    conversations: conversations.map((conversation) =>
      chatConversation(conversation, includeSelectedMessages && conversation.id === selected),
    ),
  };
}

const TEAM_TEMPLATE = {
  id: "auto-dev-squad",
  name: "Auto-Dev Squad",
  description: "Manager, lead, implementer, reviewer, and release operator for reviewed delivery.",
  required_role_keys: ["manager", "project_lead", "implementer", "reviewer", "release_operator"],
  roles: [],
  required_fields: [],
  project_goals: [],
  tasks: [],
  review_profiles: [],
};

const ADMIN_CAPTURES = [
  { file: "manual-dashboard.png", hash: "#dashboard", title: "Admin dashboard", waitFor: ".dashboard-page" },
  { file: "manual-directory-chooser.png", hash: "#dashboard", title: "Directory chooser", waitFor: ".dashboard-page", prepare: openDirectoryChooser },
  { file: "manual-agents.png", hash: "#agents?agent_id=agent-imani", title: "Agents", waitFor: ".agents-page" },
  { file: "manual-approvals.png", hash: `#approvals?request_id=${APPROVAL.request_id}`, title: "Approvals", waitFor: ".approvals-page" },
  { file: "manual-artifacts.png", hash: "#artifacts", title: "Artifacts", waitFor: ".artifacts-page" },
  { file: "manual-skills.png", hash: "#skills", title: "Skills", waitFor: ".skills-page" },
  { file: "manual-vms.png", hash: "#vms", title: "VMs", waitFor: ".vms-page" },
  { file: "manual-issues.png", hash: `#issues?issue_id=${ISSUE.issue_id}`, title: "Issues", waitFor: ".issues-page" },
  { file: "manual-sessions.png", hash: `#sessions?session_id=${SESSION.session_id}`, title: "Sessions", waitFor: ".sessions-page" },
  { file: "manual-tool-calls.png", hash: `#tool-calls?session_id=${SESSION.session_id}`, title: "Tool calls", waitFor: ".tool-calls-page" },
  { file: "manual-configuration.png", hash: "#configuration", title: "Configuration", waitFor: ".configuration-page" },
  { file: "manual-reports.png", hash: "#reports", title: "Reports", waitFor: ".reports-page" },
  { file: "manual-users.png", hash: "#users/users", title: "Users and groups", waitFor: ".users-page" },
  { file: "manual-workers.png", hash: "#workers", title: "Workers", waitFor: ".feature-page" },
  { file: "manual-workflow.png", hash: "#workflow", title: "Workflow editor", waitFor: ".workflow-embedded-page", prepare: prepareWorkflow },
];

const TEAM_CAPTURES = [
  { file: "team-workspace.png", view: "workspace", title: "Team workspace", waitFor: "#team-workspace-page", route: "/addons/team-addon/" },
  { file: "team-task-board.png", view: "task-board", title: "Team task board", waitFor: "#team-task-board-page", route: "/addons/team-addon/", prepare: prepareTaskBoard },
  { file: "team-delivery.png", view: "delivery", title: "Team delivery", waitFor: "#team-delivery-page", route: "/addons/team-addon/", prepare: prepareDelivery },
  { file: "team-org-chart.png", view: "org", title: "Team organization chart", waitFor: "#team-org-page", route: "/addons/team-addon/" },
  { file: "team-chat.png", view: "chat", title: "Team chat", waitFor: "#team-chat-page", route: "/addons/team-addon/" },
];

const MANUAL_ADMIN_REUSE = [
  { source: "manual-dashboard.png", file: "admin-dashboard.png", title: "Admin operations dashboard", route: "#dashboard", view: "dashboard" },
  { source: "manual-sessions.png", file: "admin-sessions.png", title: "Admin session detail", route: `#sessions?session_id=${SESSION.session_id}`, view: "session-detail" },
  { source: "manual-approvals.png", file: "admin-approvals.png", title: "Admin approval detail", route: `#approvals?request_id=${APPROVAL.request_id}`, view: "approval-detail" },
  { source: "manual-workflow.png", file: "admin-workflow.png", title: "Admin workflow editor", route: "#workflow", view: "workflow-editor" },
];

function json(res, payload, status = 200) {
  const body = JSON.stringify(payload);
  res.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "content-length": Buffer.byteLength(body),
    "cache-control": "no-store",
  });
  res.end(body);
}

function contentType(filePath) {
  const extension = path.extname(filePath).toLowerCase();
  return {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".png": "image/png",
    ".svg": "image/svg+xml; charset=utf-8",
    ".json": "application/json; charset=utf-8",
  }[extension] ?? "application/octet-stream";
}

function safeStaticPath(root, relative) {
  const resolved = path.resolve(root, relative);
  return resolved === root || resolved.startsWith(`${root}${path.sep}`) ? resolved : null;
}

function sessionList() {
  return {
    items: [
      SESSION,
      {
        ...SESSION,
        session_id: "session-rowan-review",
        vm_id: null,
        model: "gpt-5.4",
        metadata: { purpose: "Review telemetry retry behavior", reviewer: "Rowan" },
      },
    ],
    counts: { total: 2, active: 2, expired: 0 },
    status_ids: { active: [SESSION.session_id, "session-rowan-review"], expired: [] },
  };
}

function adminUsers() {
  const groups = [
    { group_id: "owner", display_name: "Owner", description: "Full Admin Web control.", is_required: true, permissions: ["admin.owner"], created_at: TIMESTAMPS.created, updated_at: TIMESTAMPS.updated },
    { group_id: "northstar-operator", display_name: "Northstar Operator", description: "Operate Mission Control workflows and review runs.", is_required: false, permissions: ["workflows.read", "workflows.write", "workflow_runs.read", "workflow_runs.control"], created_at: TIMESTAMPS.created, updated_at: TIMESTAMPS.updated },
    { group_id: "reviewer", display_name: "Reviewer", description: "Read workflows, runs, and approval evidence.", is_required: false, permissions: ["workflows.read", "workflow_runs.read"], created_at: TIMESTAMPS.created, updated_at: TIMESTAMPS.updated },
  ];
  const contact = (id, name, description) => ({ id, name, description, email_address_count: 0, phone_number_count: 0, primary_phone_label: null });
  const contacts = [contact("mira", "Mira", "Northstar manager"), contact("priya", "Priya", "Mission Control project lead")];
  const user = (login, displayName, contactId, groupId) => ({
    login,
    role: login === "mira" ? "admin" : "regular",
    display_name: displayName,
    contact_id: contactId,
    contact: contacts.find((entry) => entry.id === contactId) ?? null,
    local_password_enabled: true,
    password_last_changed_at: TIMESTAMPS.created,
    group_assignments: [{ login, group_id: groupId, directory_path: DIRECTORY, created_at: TIMESTAMPS.created }],
    workflow_grants: [],
    effective_permissions: groupId === "northstar-operator" ? groups[1].permissions : PERMISSIONS,
    created_at: TIMESTAMPS.created,
    updated_at: TIMESTAMPS.updated,
    last_login_at: "2026-07-13T11:50:00Z",
  });
  return {
    directory_path: DIRECTORY,
    directory_scoped_config_enabled: true,
    contacts,
    groups,
    permission_catalog: PERMISSIONS,
    users: [user("mira", "Mira", "mira", "owner"), user("priya", "Priya", "priya", "northstar-operator")],
  };
}

function workerDetail(worker) {
  return {
    summary: worker,
    config: {
      worker_id: worker.worker_id,
      path: `config/${worker.worker_id}/config.json`,
      exists: true,
      config: { enabled: worker.unit_file_state === "enabled", poll_interval_seconds: 15, directory_paths: [DIRECTORY] },
      revision: `northstar-${worker.worker_id}-rev-1`,
    },
    config_schema: {
      worker_id: worker.worker_id,
      fields: [
        { field_name: "enabled", label: "Enabled", kind: "bool", options: [], tooltip: "Whether this worker participates in the host runtime." },
        { field_name: "poll_interval_seconds", label: "Poll interval seconds", kind: "int", options: [], tooltip: "Delay between worker polling cycles." },
        { field_name: "directory_paths", label: "Directory paths", kind: "string_list", options: [], tooltip: "Directory scopes handled by this worker." },
      ],
    },
  };
}

function bootstrapTeamPayload() {
  return {
    revision: TEAM_STATE.revision,
    views: ALL_TEAM_VIEWS,
    default_view: "workspace",
    generated_at: FIXED_NOW,
    current_operator: { user_id: "northstar-operator", display_name: "Northstar Operator" },
    selected_operator: { user_id: "northstar-operator", display_name: "Northstar Operator" },
    can_view_all_operators: true,
    operator_options: [{ user_id: "northstar-operator", display_name: "Northstar Operator" }],
    counts: {
      teams: 1,
      governance_groups: 1,
      projects: 1,
      agents: 5,
      cases: 0,
      tasks: 4,
      deliverables: 2,
      review_requests: 1,
      conversations: 2,
    },
  };
}

function viewPayload(view, url) {
  const data = { state: TEAM_STATE };
  if (view === "workspace") data.templates = [TEAM_TEMPLATE];
  if (view === "chat") {
    data.chat = chatCollection(
      url.searchParams.get("selected_conversation_id"),
      url.searchParams.get("include_selected_messages") === "1",
    );
  }
  if (view === "timeline") {
    data.timeline = {
      revision: TEAM_STATE.revision,
      derived: true,
      audit_log: false,
      source: "derived_from_current_team_state",
      description: "Derived timeline for the Northstar fixture.",
      filters: {},
      timestampless_count: 0,
      total_count: 2,
      limit: 10,
      offset: 0,
      has_more: false,
      entries: [],
    };
  }
  return { revision: TEAM_STATE.revision, view, generated_at: FIXED_NOW, data, resource_tokens: {} };
}

function handleApi(req, res, url, unknownRequests, openStreams) {
  const pathname = url.pathname;
  const method = req.method ?? "GET";

  if (pathname.endsWith("/stream") && method === "GET") {
    res.writeHead(200, {
      "content-type": "text/event-stream",
      "cache-control": "no-cache",
      connection: "keep-alive",
    });
    if (pathname === "/api/addons/team-addon/state/stream") {
      res.write(`event: snapshot\ndata: ${JSON.stringify(TEAM_STATE)}\n\n`);
    } else if (pathname.includes("/conversations/")) {
      const conversationId = decodeURIComponent(pathname.split("/").at(-2));
      const conversation = TEAM_STATE.conversations[conversationId];
      if (conversation) {
        res.write(`event: snapshot\ndata: ${JSON.stringify({ revision: TEAM_STATE.revision, conversation: chatConversation(conversation, true) })}\n\n`);
      }
    } else {
      res.write(": ready\n\n");
    }
    openStreams.add(res);
    req.on("close", () => openStreams.delete(res));
    return true;
  }

  if (pathname === "/api/admin/branding") return json(res, {}), true;
  if (pathname === "/api/admin/branding/assets/evander-logo-dark.png") {
    res.writeHead(302, { location: "/assets/evander-logo-dark-CwQ3MVYv.png" });
    res.end();
    return true;
  }
  if (pathname === "/api/admin/branding/assets/evander-logo-light.png") {
    res.writeHead(302, { location: "/assets/evander-logo-light-BfuG_IQI.png" });
    res.end();
    return true;
  }
  if (pathname === "/api/auth/session") {
    return json(res, {
      auth_mode: "none",
      auth_required: false,
      authenticated: true,
      username: "northstar-operator",
      display_name: "Northstar Operator",
      role: "admin",
      permissions: PERMISSIONS,
      effective_permissions: PERMISSIONS,
      can_manage_users: true,
      session_id: "admin-session-northstar",
      available_login_methods: [],
      azure_sso_login_url: null,
      privileged_operations: { unlocked: true },
    }), true;
  }
  if (pathname === "/api/admin/deployment-status") {
    return json(res, { active: false, message: null, source: null, started_at: null, updated_at: null, ttl_seconds: 0, stale: false }), true;
  }
  if (pathname === "/api/admin/state") {
    return json(res, {
      selected_directory_path: DIRECTORY,
      directory_scoped_config_enabled: true,
      execution_backend: "libvirt_vm",
      role: "admin",
      can_manage_users: true,
      permissions: PERMISSIONS,
      effective_permissions: PERMISSIONS,
      assistant: { enabled: false, show_launcher_when_disabled: false },
      deployment: { active: false, message: null, source: null, started_at: null, updated_at: null, ttl_seconds: 0, stale: false },
      addons: [{ addon_id: "team-addon", display_name: "Team Add-on", tab_key: "addon:team-addon", label: "Teams", short_label: "Teams", description: "Hierarchical agent-team orchestration.", api_base_path: "/api/addons/team-addon", ui_path: "/addons/team-addon/" }],
      privileged_operations: { unlocked: true },
    }), true;
  }
  if (pathname === "/api/configuration/state") return json(res, CONFIGURATION_STATE), true;
  if (pathname === "/api/configuration/model-pricing") {
    return json(res, {
      status: "ok",
      path: "runtime/model-pricing.openai.json",
      path_source: "default",
      env_var: "EVANDER_MODEL_PRICING_PATH",
      version: "northstar-1",
      currency: "usd",
      description: "Sanitized documentation pricing catalog.",
      sources: ["Northstar fixture"],
      rule_count: 1,
      rules: [{ id: "gpt-5.4", model_pattern: "^gpt-5\\.4$", input_usd_per_million_tokens: 1, output_usd_per_million_tokens: 4 }],
      revision: "northstar-pricing-rev-1",
      catalog: { version: "northstar-1", currency: "usd", description: "Sanitized documentation pricing catalog.", sources: ["Northstar fixture"], rules: [] },
      editable: true,
      write_requires_privileged_unlock: true,
      error: null,
      error_class: null,
    }), true;
  }
  if (pathname === "/api/teams-auth/status") {
    return json(res, {
      directory_path: DIRECTORY,
      backend: "stub",
      state: "not_configured",
      identity_id: null,
      account_identifier: null,
      tenant_id: null,
      scopes: [],
      message: "Delegated Teams authentication is not used by the Northstar fixture.",
      issued_at: null,
      expires_at: null,
    }), true;
  }
  if (pathname === "/api/admin/user-notifications" && method === "GET") return json(res, { items: [], unread_count: 0, next_cursor: null }), true;
  if (pathname === "/api/admin/user-notifications/read" && method === "POST") return json(res, { updated: 1, unread_count: 0 }), true;

  if (pathname === "/api/admin/agents") return json(res, { items: ADMIN_AGENTS, directory_path: DIRECTORY, direct_only: url.searchParams.get("direct_only") !== "false" }), true;
  if (pathname === "/api/admin/agents/create-options") {
    return json(res, { directory_path: DIRECTORY, backends: ["libvirt_vm", "container_worker_vm"], agent_providers: [{ value: "codex", label: "Codex", execution_backends: ["libvirt_vm", "container_worker_vm"] }], model_provider_modes: [{ value: "openai", label: "OpenAI relay" }], communication_targets: [{ value: "northstar-operators", label: "Northstar operators" }], libvirt: { default_template: "python", templates: [{ alias: "python", image_ref: "python-agent.qcow2" }] }, container_worker: { default_image: "runner", images: [{ alias: "runner", image_ref: "evander-runner:latest" }] } }), true;
  }
  if (pathname === "/api/admin/agents/create-operations" && method === "GET") {
    return json(res, { items: [] }), true;
  }
  const agentLog = pathname.match(/^\/api\/admin\/agents\/([^/]+)\/log$/);
  if (agentLog) return json(res, { agent_id: decodeURIComponent(agentLog[1]), backend: "libvirt_vm", view: "executions", lines: 200, text: "11:36 focused retry tests: 18 passed\n11:42 evidence bundle staged for Rowan\n", returned_lines: 2, total_lines: 2, has_more_before: false }), true;
  if (pathname === "/api/operator-steering/context") return json(res, { session_id: url.searchParams.get("session_id"), directory_path: DIRECTORY, context: { session_id: SESSION.session_id, dispatch_id: "dispatch-telemetry-retry", workflow_run_id: "run-telemetry-retry", effect_id: "effect-prompt-imani", node_id: "effect-prompt-imani", agent_harness: "codex", execution_backend: "libvirt_vm", directory_path: DIRECTORY, model: "gpt-5.4", trace_id: "trace-northstar-248", started_at: TIMESTAMPS.started, expires_at: TIMESTAMPS.expires } }), true;
  if (pathname === "/api/operator-steering/notes") return json(res, { session_id: url.searchParams.get("session_id"), dispatch_id: url.searchParams.get("dispatch_id"), directory_path: DIRECTORY, notes: [] }), true;

  if (pathname === "/api/admin/approvals") return json(res, { items: [APPROVAL], health: { total: 3, pending: 1, approved: 2, denied: 0, expired: 0, exhausted: 0, oldest_pending_age_seconds: 900 } }), true;
  if (pathname === `/api/admin/approvals/${APPROVAL.request_id}`) return json(res, { request: APPROVAL, redacted_payload: APPROVAL.payload, audit_context: [{ event: "tool_call_guarded", agent_id: "agent-imani", trace_id: "trace-northstar-248" }], age_seconds: 900, expires_in_seconds: 2700, payload_summary: "Publish one sanitized telemetry retry verification report.", approval_diagnostics: { request_id: APPROVAL.request_id, status: "pending", action_type: APPROVAL.action_type, tool_name: APPROVAL.tool_name, payload_digest: APPROVAL.payload_digest, requested_payload: APPROVAL.payload, blocked_tool_call_diagnostics: [] } }), true;

  if (pathname === "/api/admin/artifact-spaces") return json(res, { success: true, revision: 3, count: 3, spaces: [{ path: "/", parent_path: null, display_name: "All artifacts", metadata: {} }, { path: "/northstar", parent_path: "/", display_name: "Northstar", metadata: {} }, { path: "/northstar/mission-control", parent_path: "/northstar", display_name: "Mission Control", metadata: {} }] }), true;
  if (pathname === "/api/admin/artifacts") return json(res, { items: ARTIFACTS, limit: 50, total: ARTIFACTS.length, next_cursor: null }), true;
  const artifactDetail = pathname.match(/^\/api\/admin\/artifacts\/(.+)$/);
  if (artifactDetail) return json(res, ARTIFACTS.find((item) => item.key === decodeURIComponent(artifactDetail[1])) ?? ARTIFACTS[0]), true;

  if (pathname === "/api/admin/skills") return json(res, { items: [{ name: "evander-operations", size_bytes: 8_420, updated_at: TIMESTAMPS.updated }, { name: "mission-control-review", size_bytes: 5_184, updated_at: TIMESTAMPS.reviewed }, { name: "telemetry-diagnostics", size_bytes: 3_992, updated_at: "2026-07-13T10:00:00Z" }] }), true;
  const skillDetail = pathname.match(/^\/api\/admin\/skills\/([^/]+)$/);
  if (skillDetail) return json(res, { skill: { name: decodeURIComponent(skillDetail[1]), size_bytes: 8_420, updated_at: TIMESTAMPS.updated, content: "# Northstar documentation fixture\n\nSanitized managed skill content." } }), true;

  if (pathname === "/api/admin/vms") return json(res, { items: [{ vm_name: SESSION.vm_id, provider: "libvirt", issue_number: "MC-248", session_ids: [SESSION.session_id], labels: { fixture: "northstar", role: "implementer" }, directory_paths: [DIRECTORY], power_state: "running", libvirt_state: "running", ip_address: "192.0.2.41", agent: { state: "running", pid: 4242, dispatch_id: "dispatch-telemetry-retry", repo_names: ["mission-control"], preferred_repo_name: "mission-control" }, last_state_update_at: TIMESTAMPS.updated }, { vm_name: "vm-priya-lead", provider: "libvirt", issue_number: null, session_ids: ["session-priya"], labels: { fixture: "northstar", role: "lead" }, directory_paths: [DIRECTORY], power_state: "shut off", libvirt_state: "shut off", ip_address: null, agent: { state: "idle", pid: null, dispatch_id: null, repo_names: ["mission-control"], preferred_repo_name: "mission-control" }, last_state_update_at: TIMESTAMPS.reviewed }], directory_path: DIRECTORY, direct_only: true }), true;

  if (pathname === "/api/admin/issues") return json(res, { items: [ISSUE, { ...ISSUE, issue_id: "issue-retry-dashboard", issue_number: "MC-247", title: "Expose retry exhaustion on the operations dashboard", state: "accepted", updated_at: TIMESTAMPS.reviewed }], directory_path: DIRECTORY, direct_only: true }), true;
  if (pathname === `/api/admin/issues/${ISSUE.issue_id}`) return json(res, { ...ISSUE, raw: ISSUE }), true;
  if (pathname === "/api/admin/issues/issue-retry-dashboard") return json(res, { ...ISSUE, issue_id: "issue-retry-dashboard", issue_number: "MC-247", title: "Expose retry exhaustion on the operations dashboard", state: "accepted", raw: ISSUE }), true;

  if (pathname === "/api/admin/sessions/catalog") return json(res, { tool_options: [{ value: "get_issue", label: "Get issue" }, { value: "stage_artifact", label: "Stage artifact" }, { value: "publish_artifact", label: "Publish artifact" }, { value: "prompt_agent", label: "Prompt agent" }], model_options: [{ value: "gpt-5.4", label: "GPT-5.4" }], resume_mode_options: [{ value: "same_context", label: "Same context" }, { value: "fresh_context", label: "Fresh context" }], renew_extension_presets: ["1h", "1d", "7d"] }), true;
  if (pathname === "/api/admin/sessions") return json(res, sessionList()), true;
  if (pathname === `/api/admin/sessions/${SESSION.session_id}`) return json(res, { session: SESSION, wake_schedules: [{ request_id: "wake-imani-status", session_id: SESSION.session_id, prompt: "Check Mission Control telemetry retry status.", cron: "*/30 * * * *", resume_at: null, resume_mode: "same_context", status: "scheduled", enabled: true, updated_at: TIMESTAMPS.updated, schedule_kind: "cron" }], summary_json: { work_item: "MC-248", workflow_run_id: "run-telemetry-retry", owner: "Imani" } }), true;
  if (pathname === "/api/admin/scheduled-wakes") return json(res, { items: [{ request_id: "wake-imani-status", session_id: SESSION.session_id, source: "team-addon", next_run_at: "2026-07-13T12:15:00Z", schedule_cron: "*/30 * * * *", status: "pending", prompt_summary: "Check Mission Control telemetry retry status.", latest_dispatch: null, retry_available: false, enabled: true }] }), true;

  if (pathname === "/api/admin/tool-calls") return json(res, TOOL_CALLS), true;
  if (pathname === "/api/workers") return json(res, { workers: WORKERS }), true;
  const workerLog = pathname.match(/^\/api\/workers\/([^/]+)\/log$/);
  if (workerLog) { const worker = WORKERS.find((item) => item.worker_id === decodeURIComponent(workerLog[1])) ?? WORKERS[0]; return json(res, { worker_id: worker.worker_id, worker_name: worker.name, unit: worker.unit, lines: 200, text: "12:00:00 worker healthy\n11:59:42 processed northstar telemetry event\n" }), true; }
  const workerPath = pathname.match(/^\/api\/workers\/([^/]+)$/);
  if (workerPath) { const worker = WORKERS.find((item) => item.worker_id === decodeURIComponent(workerPath[1])) ?? WORKERS[0]; return json(res, workerDetail(worker)), true; }

  if (pathname === "/api/admin/users") return json(res, adminUsers()), true;
  if (pathname === "/api/admin/reports/workflow-graph-activity") return json(res, GRAPH_ACTIVITY), true;
  if (pathname === "/api/admin/reports/workflow-failure-clusters") return json(res, { generated_at: FIXED_NOW, period_start: "2026-07-12T12:00:00Z", period_end: FIXED_NOW, clusters: [], cluster_count: 0, run_count: 0 }), true;
  if (pathname === "/api/admin/reports/conformance") return json(res, { generated_at: FIXED_NOW, reports: [], available_kinds: ["trigger", "effect"] }), true;

  if (pathname === "/api/workflow-editor/document") return json(res, WORKFLOW_DOCUMENT), true;
  if (pathname === "/api/workflow-editor/catalog") return json(res, WORKFLOW_CATALOG), true;
  if (pathname === "/api/workflow-editor/runs") return json(res, WORKFLOW_RUNS), true;
  if (pathname === "/api/admin/release-info") return json(res, { version: "0.3.1", version_source: "pyproject.toml", build: "northstar-docs", build_source: "documentation fixture", changelog: { source: "CHANGELOG.md", path: "CHANGELOG.md", heading: "0.3.1", entries: [{ text: "Improved reviewed Team delivery and operator evidence.", kind: "feature" }, { text: "Hardened telemetry retry handling.", kind: "bugfix" }] }, news: null, missing_reasons: [] }), true;
  if (pathname === "/api/admin/capacity-health") return json(res, { generated_at: FIXED_NOW, control_plane: { disk: { scope: "control_plane", status: "available", health: "ok", total_bytes: 536_870_912_000, used_bytes: 214_748_364_800, free_bytes: 322_122_547_200, used_percent: 40, free_percent: 60, error_count: 0, errors: [] }, storage: { total: { scope: "known_storage", status: "available", health: "ok", size_bytes: 2_147_483_648, file_count: 412, directory_count: 48, symlink_count: 0, error_count: 0, errors: [] }, areas: [] } }, sessions: { total_sessions: 2, total_agents: 3, lifecycle_counts: { active: 2 }, status_counts: { running: 1, idle: 1 }, backend_counts: { libvirt_vm: 1, container_worker_vm: 1 }, directory_counts: { [DIRECTORY]: 2 }, directory_breakdowns: {}, age_buckets: { lt_1d: 2 }, storage: { measured_total: { scope: "session_storage", status: "available", health: "ok", size_bytes: 524_288_000, file_count: 61, directory_count: 12, symlink_count: 0, error_count: 0, errors: [] }, measured_session_count: 2, largest_sessions: [], oldest_sessions: [], missing_telemetry: [] } }, workers: { total_workers: 4, reachable_workers: 4, telemetry_available_workers: 4, entries: [] }, queue: { status: "available", health: "ok", backend: "sqlite", active_record_count: 1, pending_count: 1, inflight_count: 0, oldest_event_id: "run-telemetry-retry", oldest_occurred_at: TIMESTAMPS.updated, oldest_age_seconds: 1080, errors: [] }, prompts: { status: "available", health: "ok", unread_count: 1, oldest_message_id: "inbox-status-retry", oldest_created_at: "2026-07-13T11:25:00Z", oldest_age_seconds: 2100, errors: [] } }), true;

  if (pathname === "/api/admin/user-preferences/workflow-tutorial-progress") return json(res, { completed_tutorial_ids: [], dismissed_tutorial_ids: [] }), true;

  if (pathname === "/api/addons/team-addon/state") return json(res, TEAM_STATE), true;
  if (pathname === "/api/addons/team-addon/bootstrap") return json(res, bootstrapTeamPayload()), true;
  const teamView = pathname.match(/^\/api\/addons\/team-addon\/views\/([^/]+)$/);
  if (teamView) return json(res, viewPayload(decodeURIComponent(teamView[1]), url)), true;
  if (pathname === "/api/addons/team-addon/costs") return json(res, { generated_at: FIXED_NOW, coverage_status: "complete", coverage_reasons: [], team_costs: { "northstar-platform": { team_id: "northstar-platform", estimated_cost_usd: 18.42, max_cost_usd: 250, coverage_status: "complete" } }, governance_group_costs: {}, project_costs: { "mission-control": { project_id: "mission-control", estimated_cost_usd: 12.76, coverage_status: "complete" } }, unattributed_costs: null, source_counts: { model_usage: 14 }, events_count: 14, priced_events_count: 14, unpriced_events_count: 0, backfill: null }), true;
  if (pathname === "/api/addons/team-addon/templates") return json(res, { templates: [TEAM_TEMPLATE] }), true;
  if (pathname === "/api/addons/team-addon/conversations") return json(res, chatCollection(url.searchParams.get("selected_conversation_id"), url.searchParams.get("include_selected_messages") === "1")), true;
  const conversationDetail = pathname.match(/^\/api\/addons\/team-addon\/conversations\/([^/]+)$/);
  if (conversationDetail) { const conversation = TEAM_STATE.conversations[decodeURIComponent(conversationDetail[1])]; return conversation ? (json(res, { revision: TEAM_STATE.revision, conversation: chatConversation(conversation, true) }), true) : false; }
  const conversationMessagesPath = pathname.match(/^\/api\/addons\/team-addon\/conversations\/([^/]+)\/messages$/);
  if (conversationMessagesPath && method === "GET") { const conversationId = decodeURIComponent(conversationMessagesPath[1]); const messages = chatMessages(conversationId); return json(res, { revision: TEAM_STATE.revision, conversation_id: conversationId, before_message_id: null, messages_limit: 50, messages, message_count: messages.length, has_older: false, oldest_message_id: messages[0]?.id ?? null, newest_message_id: messages.at(-1)?.id ?? null }), true; }
  if (pathname === "/api/addons/team-addon/inbox") return json(res, { revision: TEAM_STATE.revision, generated_at: FIXED_NOW, total_count: 1, limit: 50, offset: 0, has_more: false, items: Object.values(TEAM_STATE.inbox) }), true;
  if (pathname === "/api/addons/team-addon/timeline") return json(res, viewPayload("timeline", url).data.timeline), true;

  unknownRequests.add(`${method} ${pathname}${url.search}`);
  json(res, { error: `No Northstar fixture route for ${method} ${pathname}` }, 404);
  return false;
}

async function startFixtureServer() {
  const unknownRequests = new Set();
  const openStreams = new Set();
  const server = createServer(async (req, res) => {
    try {
      const url = new URL(req.url ?? "/", "http://127.0.0.1");
      if (url.pathname === "/favicon.ico") {
        res.writeHead(204);
        res.end();
        return;
      }
      if (url.pathname.startsWith("/api/")) {
        handleApi(req, res, url, unknownRequests, openStreams);
        return;
      }

      let filePath;
      if (url.pathname === "/addons/team-addon" || url.pathname === "/addons/team-addon/") {
        filePath = path.join(TEAM_STATIC, "index.html");
      } else if (url.pathname.startsWith("/addons/team-addon/")) {
        filePath = safeStaticPath(TEAM_STATIC, decodeURIComponent(url.pathname.slice("/addons/team-addon/".length)));
      } else if (url.pathname === "/" || url.pathname === "/index.html") {
        filePath = path.join(ADMIN_DIST, "index.html");
      } else {
        filePath = safeStaticPath(ADMIN_DIST, decodeURIComponent(url.pathname.slice(1)));
      }

      if (!filePath) {
        res.writeHead(404);
        res.end("Not found");
        return;
      }
      const body = await readFile(filePath);
      res.writeHead(200, { "content-type": contentType(filePath), "cache-control": "no-store" });
      res.end(body);
    } catch (error) {
      res.writeHead(error?.code === "ENOENT" ? 404 : 500, { "content-type": "text/plain; charset=utf-8" });
      res.end(error instanceof Error ? error.message : String(error));
    }
  });

  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("Fixture server did not bind an HTTP port.");
  return {
    origin: `http://127.0.0.1:${address.port}`,
    unknownRequests,
    async close() {
      for (const response of openStreams) response.destroy();
      await new Promise((resolve) => server.close(resolve));
    },
  };
}

async function openDirectoryChooser(page) {
  await page.getByRole("button", { name: "Choose directory" }).click();
  await page.getByRole("dialog", { name: "Choose directory" }).waitFor({ state: "visible" });
}

async function prepareWorkflow(page) {
  const panel = page.locator(".workflow-embedded-page");
  await panel.waitFor({ state: "visible" });
  await page.waitForFunction(() => document.querySelectorAll(".react-flow__node").length >= 6);
  await page.waitForTimeout(800);
}

async function prepareTaskBoard(page) {
  const teamSelect = page.locator("#task-board-team-select");
  await teamSelect.selectOption("northstar-platform");
  const projectSelect = page.locator("#task-board-project-select");
  await projectSelect.selectOption("mission-control");
  await page.locator(".task-board-lanes").waitFor({ state: "visible" });
  await page.locator(".task-board-card").first().waitFor({ state: "visible" });
}

async function prepareDelivery(page) {
  await page.locator(".delivery-row").first().waitFor({ state: "visible" });
}

const FREEZE_AND_THEME_SCRIPT = ({ fixedNow, teamView, directory }) => {
  const NativeDate = Date;
  const epoch = NativeDate.parse(fixedNow);
  class FixedDate extends NativeDate {
    constructor(...args) {
      super(...(args.length ? args : [epoch]));
    }
    static now() {
      return epoch;
    }
  }
  Object.setPrototypeOf(FixedDate, NativeDate);
  globalThis.Date = FixedDate;
  try {
    localStorage.setItem("admin-web:theme", "dark");
    localStorage.setItem("admin-web:selected-directory", directory);
    localStorage.setItem("admin-web:privileges-unlocked", "true");
    localStorage.setItem("admin-web:workflow-sidebar-collapsed", "false");
    localStorage.setItem("team-addon:active-view", teamView || "workspace");
    localStorage.setItem("team-addon:chat:notification-sound-muted", "true");
    localStorage.setItem("team-addon:chat:read-markers", "{}");
  } catch {
    // Hardened contexts may make storage unavailable before navigation.
  }
};

const STABILITY_STYLE = `
  *, *::before, *::after {
    animation: none !important;
    transition: none !important;
    caret-color: transparent !important;
    scroll-behavior: auto !important;
  }
  html { color-scheme: dark !important; }
`;

async function capturePage(browser, fixture, definition, { surface, origin, destination }) {
  const context = await browser.newContext({
    viewport: VIEWPORT,
    deviceScaleFactor: 1,
    colorScheme: "dark",
    reducedMotion: "reduce",
    locale: "en-US",
    timezoneId: "UTC",
  });
  await context.addInitScript(FREEZE_AND_THEME_SCRIPT, {
    fixedNow: FIXED_NOW,
    teamView: surface === "team" ? definition.view : "workspace",
    directory: DIRECTORY,
  });
  const page = await context.newPage();
  page.setDefaultTimeout(12_000);
  const pageErrors = [];
  const consoleErrors = [];
  const failedRequests = [];
  const badResponses = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  page.on("requestfailed", (request) => failedRequests.push(`${request.method()} ${request.url()}: ${request.failure()?.errorText ?? "failed"}`));
  page.on("response", (response) => {
    if (response.status() >= 400) badResponses.push(`${response.status()} ${response.url()}`);
  });

  try {
    const url = surface === "admin" ? `${origin}/${definition.hash}` : `${origin}${definition.route}`;
    await page.goto(url, { waitUntil: "domcontentloaded" });
    await page.locator(definition.waitFor).waitFor({ state: "visible" });
    if (surface === "admin") {
      await page.getByRole("button", { name: "Light mode" }).waitFor({ state: "visible" });
      await page.getByRole("button", { name: "Choose directory" }).waitFor({ state: "visible" });
      await page.locator("img.admin-shell-logo").waitFor({ state: "visible" });
      await page.waitForFunction(() =>
        Array.from(document.images).every((image) => image.complete && image.naturalWidth > 0),
      );
    }
    await page.addStyleTag({ content: STABILITY_STYLE });
    if (definition.prepare) await definition.prepare(page);
    await page.evaluate(() => {
      document.documentElement.scrollTop = 0;
      document.body.scrollTop = 0;
      const active = document.activeElement;
      if (active instanceof HTMLElement) active.blur();
    });
    await page.waitForTimeout(300);
    await page.screenshot({
      path: destination,
      animations: "disabled",
      caret: "hide",
      scale: "css",
      fullPage: false,
    });

    const visibleSize = await page.evaluate(() => ({ width: innerWidth, height: innerHeight }));
    if (visibleSize.width !== VIEWPORT.width || visibleSize.height !== VIEWPORT.height) {
      throw new Error(`${definition.file}: viewport was ${visibleSize.width}x${visibleSize.height}`);
    }
    if (pageErrors.length || consoleErrors.length || failedRequests.length || badResponses.length) {
      const details = [
        ...pageErrors.map((entry) => `pageerror: ${entry}`),
        ...consoleErrors.map((entry) => `console: ${entry}`),
        ...failedRequests.map((entry) => `request: ${entry}`),
        ...badResponses.map((entry) => `response: ${entry}`),
      ].join("\n");
      throw new Error(`${definition.file} emitted browser errors:\n${details}`);
    }
    fixture.captures.push({ file: definition.file, surface, route: surface === "admin" ? definition.hash : definition.route, view: definition.view ?? definition.hash.slice(1).split("?")[0] });
  } finally {
    await context.close();
  }
}

async function main() {
  await rm(RAW_DIR, { recursive: true, force: true });
  await mkdir(RAW_DIR, { recursive: true });
  await mkdir(MANUAL_SCREENSHOT_DIR, { recursive: true });
  await mkdir(ADMIN_SCREENSHOT_DIR, { recursive: true });

  const fixtureServer = await startFixtureServer();
  const browser = await chromium.launch({ headless: true });
  const report = {
    schema_version: 1,
    fixture: "northstar",
    dark_mode: true,
    viewport: VIEWPORT,
    fixed_now: FIXED_NOW,
    captures: [],
  };

  try {
    for (const definition of ADMIN_CAPTURES) {
      const rawPath = path.join(RAW_DIR, definition.file);
      await capturePage(browser, report, definition, { surface: "admin", origin: fixtureServer.origin, destination: rawPath });
      await copyFile(rawPath, path.join(ADMIN_SCREENSHOT_DIR, definition.file));
    }

    for (const reuse of MANUAL_ADMIN_REUSE) {
      await copyFile(path.join(RAW_DIR, reuse.source), path.join(MANUAL_SCREENSHOT_DIR, reuse.file));
    }

    for (const definition of TEAM_CAPTURES) {
      const rawPath = path.join(RAW_DIR, definition.file);
      await capturePage(browser, report, definition, { surface: "team", origin: fixtureServer.origin, destination: rawPath });
      await copyFile(rawPath, path.join(MANUAL_SCREENSHOT_DIR, definition.file));
    }

    if (fixtureServer.unknownRequests.size) {
      throw new Error(`Unmocked fixture requests:\n${[...fixtureServer.unknownRequests].sort().join("\n")}`);
    }

    const manifest = {
      schema_version: 1,
      fixture: "northstar",
      dark_mode: true,
      screenshots: [
        ...MANUAL_ADMIN_REUSE.map(({ file, title, route, view }) => ({ file, title, surface: "admin", route, view })),
        ...TEAM_CAPTURES.map(({ file, title, route, view }) => ({ file, title, surface: "team", route, view })),
      ],
    };
    await writeFile(path.join(MANUAL_SCREENSHOT_DIR, "manifest.json"), `${JSON.stringify(manifest, null, 2)}\n`, "utf8");
    await writeFile(path.join(RAW_DIR, "capture-report.json"), `${JSON.stringify(report, null, 2)}\n`, "utf8");
    process.stdout.write(`Captured ${ADMIN_CAPTURES.length} Admin and ${TEAM_CAPTURES.length} Team screenshots from the Northstar fixture.\n`);
  } finally {
    await browser.close();
    await fixtureServer.close();
  }
}

await main();

import { useEffect, useRef, useState, type CSSProperties, type MutableRefObject, type ReactNode } from "react";

import { ApiError, fetchJson, fetchJsonConditional, formatRelativeSeconds, formatTimestamp } from "../admin/api";
import { useAdminUi } from "../admin/AdminContext";
import { useDocumentVisible } from "../admin/visibility";
import { DownloadIcon } from "../admin/PageActionIcons";
import {
  DashboardApprovalsIcon,
  DashboardAddonsIcon,
  DashboardAgentsIcon,
  DashboardArtifactsIcon,
  DashboardConfigurationIcon,
  DashboardIssuesIcon,
  DashboardMcpToolsIcon,
  DashboardNewerIcon,
  DashboardOlderIcon,
  DashboardRefreshIcon,
  DashboardReportsIcon,
  DashboardSessionsIcon,
  DashboardSkillsIcon,
  DashboardToolVisibilityIcon,
  DashboardToolsIcon,
  DashboardUsersIcon,
  DashboardVmsIcon,
  DashboardWorkflowIcon,
  DashboardWorkersIcon,
} from "../admin/PageActionIcons";
import { Tooltip } from "../admin/Tooltip";
import type { AgentBackend } from "../admin/types";
import { adminHash, type AdminHashParamValue, type AdminTab } from "../../adminRouting";
import type { WorkflowEditorCatalog, WorkflowEditorDocument, WorkflowRunSnapshot } from "../../types";

const DASHBOARD_AGENT_INVENTORY_POLL_MS = 10_000;

type DashboardAdminState = {
  selected_directory_path: string;
  directory_scoped_config_enabled: boolean;
  execution_backend: AgentBackend;
  role: "admin" | "regular" | null;
  can_manage_users: boolean;
  privileged_operations: {
    unlocked: boolean;
  };
};

type DeploymentStatus = {
  active: boolean;
  message: string | null;
  source: string | null;
  started_at: string | null;
  updated_at: string | null;
  ttl_seconds: number;
  stale: boolean;
};

type WorkerSummary = {
  worker_id: string;
  name: string;
  unit: string;
  active_state: string;
  sub_state: string | null;
  result: string | null;
  unit_file_state: string | null;
  description: string | null;
  main_pid: number | null;
  running: boolean;
  failed: boolean;
  detail: string | null;
};

type WorkerListResponse = {
  workers: WorkerSummary[];
};

type AgentRecord = {
  agent_id: string;
  backend: AgentBackend;
  target_name: string;
  session_id: string | null;
  directory_path: string;
  status: string;
  started_at: string | null;
  finished_at: string | null;
  updated_at: string | null;
  host: Record<string, unknown>;
  capabilities: {
    start: boolean;
    shutdown: boolean;
    resume: boolean;
    enable_disable: boolean;
    kill: boolean;
    logs: boolean;
    stop: boolean;
    destroy: boolean;
  };
};

type AgentListResponse = {
  items: AgentRecord[];
  directory_path: string;
  direct_only: boolean;
};

type ApprovalStatus = "pending" | "approved" | "denied" | "expired";

type ApprovalItem = {
  request_id: string;
  session_id: string;
  action_type: string;
  tool_name: string;
  payload_digest: string;
  human_summary: string;
  intended_effect?: string | null;
  directory_path: string;
  vm_id?: string | null;
  status: ApprovalStatus;
  created_at: string;
  expires_at: string;
  approver?: string | null;
  decision_at?: string | null;
  decision_reason?: string | null;
  payload: Record<string, unknown>;
  decision_history: Array<{
    status: ApprovalStatus;
    approver?: string | null;
    reason?: string | null;
    decided_at: string;
  }>;
};

type ApprovalHealth = {
  total: number;
  pending: number;
  approved: number;
  denied: number;
  expired: number;
  oldest_pending_age_seconds?: number | null;
};

type ApprovalIndexResponse = {
  items: ApprovalItem[];
  health: ApprovalHealth;
};

type SessionRecord = {
  session_id: string;
  vm_id: string | null;
  model: string | null;
  directory_path: string;
  allowed_tools?: string[];
  approval_required_tools?: string[];
  allowed_script_names?: string[];
  discord_channel_allowlist?: string[];
  teams_channel_allowlist?: string[];
  communication_file_receive_enabled?: boolean;
  communication_file_send_enabled?: boolean;
  communication_file_max_bytes?: number | null;
  communication_file_allowed_mime_types?: string[];
  communication_file_allowed_extensions?: string[];
  communication_file_blocked_extensions?: string[];
  communication_file_require_approval_for_send?: boolean;
  expires_at: string;
  created_at: string;
  metadata?: Record<string, unknown>;
  issue_number?: string | null;
  is_expired: boolean;
};

type SessionListResponse = {
  items: SessionRecord[];
  counts: {
    total: number;
    active: number;
    expired: number;
  };
  status_ids: {
    active: string[];
    expired: string[];
  };
};

type ToolCallStatus = "success" | "denied" | "error" | "pending_approval";

type ToolCallItem = {
  tool_name: string;
  created_at: string;
  session_id?: string | null;
  directory_path?: string | null;
  duration_ms?: number | null;
  status?: string | null;
  decision?: string | null;
  role?: string | null;
  agent_role?: string | null;
  actor_user_id?: string | null;
  request_id?: string | null;
  correlation_id?: string | null;
  approval_request_id?: string | null;
  workflow_run_id?: string | null;
  trace_id?: string | null;
  vm_id?: string | null;
  vm_name?: string | null;
  agent_id?: string | null;
  error?: string | null;
  error_summary?: string | null;
  metadata?: Record<string, unknown> | null;
  related_links?: Array<Record<string, unknown>>;
  [key: string]: unknown;
};

type ToolCallsResponse = {
  items: ToolCallItem[];
  counts?: {
    total?: number | null;
    matched?: number | null;
    returned?: number | null;
    success?: number | null;
    denied?: number | null;
    error?: number | null;
    status_counts?: {
      success?: number | null;
      denied?: number | null;
      error?: number | null;
      [key: string]: number | null | undefined;
    };
  };
  limit?: number | null;
  total?: number | null;
};

type WorkflowGraphActivityNode = {
  node_id: string;
  node_type: string | null;
  display_name: string | null;
  activity_count: number;
  status_counts: Record<string, number>;
  first_activity_at: string | null;
  latest_activity_at: string | null;
};

type WorkflowGraphActivityGraph = {
  graph_id: string;
  name: string;
  description: string | null;
  directory_paths: string[];
  user_ids: string[];
  node_ids: string[];
  node_count: number;
  run_count: number;
  activity_count: number;
  trigger_event_count: number;
  gate_execution_count: number;
  effect_execution_count: number;
  status_counts: Record<string, number>;
  first_activity_at: string | null;
  latest_activity_at: string | null;
  nodes: WorkflowGraphActivityNode[];
};

type WorkflowGraphActivityReport = {
  worker_id: string;
  generated_at: string;
  period_start: string;
  period_end: string;
  graphs: WorkflowGraphActivityGraph[];
};

type ReleaseChangelogEntry = {
  text: string;
  kind: string | null;
};

type ReleaseChangelog = {
  source: string | null;
  path: string | null;
  heading: string | null;
  entries: ReleaseChangelogEntry[];
};

type ReleaseNewsBlock =
  | {
      kind: "paragraph";
      text: string;
      level?: null;
      items?: [];
      src?: null;
      dark_src?: null;
      alt?: null;
      title?: null;
    }
  | {
      kind: "heading";
      text: string;
      level: number;
      items?: [];
      src?: null;
      dark_src?: null;
      alt?: null;
      title?: null;
    }
  | {
      kind: "list";
      items: string[];
      text?: null;
      level?: null;
      src?: null;
      dark_src?: null;
      alt?: null;
      title?: null;
    }
  | {
      kind: "image";
      src: string;
      dark_src: string | null;
      alt: string;
      title: string | null;
      text?: null;
      level?: null;
      items?: [];
    };

type ReleaseNews = {
  source: string | null;
  path: string | null;
  index: number;
  heading: string | null;
  blocks: ReleaseNewsBlock[];
  has_previous: boolean;
  has_next: boolean;
  previous_index: number | null;
  next_index: number | null;
};

type ReleaseInfoPayload = {
  version: string | null;
  version_source: string | null;
  build: string | null;
  build_source: string | null;
  changelog: ReleaseChangelog | null;
  news: ReleaseNews | null;
  missing_reasons: string[];
};

function releaseNewsImageClassName(block: Extract<ReleaseNewsBlock, { kind: "image" }>): string {
  const descriptor = `${block.src} ${block.dark_src ?? ""} ${block.alt}`.toLowerCase();
  return descriptor.includes("signature")
    ? "release-news-image release-news-image-signature"
    : "release-news-image";
}

function renderReleaseNewsBlock(block: ReleaseNewsBlock, key: string): ReactNode {
  if (block.kind === "image") {
    return (
      <figure className={releaseNewsImageClassName(block)} key={key}>
        <img
          className={block.dark_src ? "release-news-image-light" : undefined}
          src={block.src}
          alt={block.alt}
          loading="lazy"
          decoding="async"
        />
        {block.dark_src ? (
          <img
            className="release-news-image-dark"
            src={block.dark_src}
            alt={block.alt}
            loading="lazy"
            decoding="async"
          />
        ) : null}
        {block.title ? <figcaption>{block.title}</figcaption> : null}
      </figure>
    );
  }
  if (block.kind === "heading") {
    return (
      <h4 className="release-news-subheading" key={key}>
        {block.text}
      </h4>
    );
  }
  if (block.kind === "list") {
    return (
      <ul className="release-news-list" key={key}>
        {block.items.map((item, index) => (
          <li key={`${key}-${index}`}>{item}</li>
        ))}
      </ul>
    );
  }
  return <p key={key}>{block.text}</p>;
}

type TelemetryStatus = "available" | "missing" | "unsupported" | "permission_denied";

type HealthState = "ok" | "warning" | "critical" | "unknown";

type TelemetryError = {
  status: TelemetryStatus;
  message: string;
};

type DiskUsageTelemetry = {
  scope: string;
  status: TelemetryStatus;
  health: HealthState;
  total_bytes: number | null;
  used_bytes: number | null;
  free_bytes: number | null;
  used_percent: number | null;
  free_percent: number | null;
  error_count: number;
  errors: TelemetryError[];
};

type AvailableDiskUsageTelemetry = DiskUsageTelemetry & {
  total_bytes: number;
  used_bytes: number;
  free_bytes: number;
};

type DirectorySizeTelemetry = {
  scope: string;
  status: TelemetryStatus;
  health: HealthState;
  size_bytes: number;
  file_count: number;
  directory_count: number;
  symlink_count: number;
  error_count: number;
  errors: TelemetryError[];
};

type WorkerTelemetryPayload = {
  generated_at?: string;
  disk?: {
    worker_root?: DiskUsageTelemetry | null;
  } | null;
  session_storage?: Record<string, unknown> | null;
  jobs?: Record<string, unknown> | null;
};

type SessionStorageSample = {
  session_id: string;
  size_bytes: number;
  backend: string | null;
  directory_path: string | null;
  created_at: string | null;
  updated_at: string | null;
};

type CapacityHealthPayload = {
  generated_at: string;
  control_plane: {
    disk: DiskUsageTelemetry;
    storage: {
      total: DirectorySizeTelemetry;
      areas: DirectorySizeTelemetry[];
    };
  };
  sessions: {
    total_sessions: number;
    total_agents: number;
    lifecycle_counts: Record<string, number>;
    status_counts: Record<string, number>;
    backend_counts: Record<string, number>;
    directory_counts: Record<string, number>;
    directory_breakdowns: Record<string, Record<string, unknown>>;
    age_buckets: Record<string, number>;
    storage: {
      measured_total: DirectorySizeTelemetry;
      measured_session_count: number;
      largest_sessions: SessionStorageSample[];
      oldest_sessions: SessionStorageSample[];
      missing_telemetry: DirectorySizeTelemetry[];
    };
  };
  workers: {
    total_workers: number;
    reachable_workers: number;
    telemetry_available_workers: number;
    entries: Array<{
      worker_url: string;
      directory_paths: string[];
      discovered_from: string[];
      reachable: boolean;
      last_checked_at: string | null;
      last_heartbeat_at: string | null;
      last_heartbeat_source: string | null;
      healthz_status: TelemetryStatus;
      telemetry_status: TelemetryStatus;
      healthz: Record<string, unknown> | null;
      telemetry: WorkerTelemetryPayload | null;
      errors: TelemetryError[];
    }>;
  };
  queue: {
    status: TelemetryStatus;
    health: HealthState;
    backend: string | null;
    active_record_count: number;
    pending_count: number;
    inflight_count: number;
    oldest_event_id: string | null;
    oldest_occurred_at: string | null;
    oldest_age_seconds: number | null;
    errors: TelemetryError[];
  };
  prompts: {
    status: TelemetryStatus;
    health: HealthState;
    unread_count: number;
    oldest_message_id: string | null;
    oldest_created_at: string | null;
    oldest_age_seconds: number | null;
    errors: TelemetryError[];
  };
};

type RemoteState<T> = {
  loading: boolean;
  error: string | null;
  missing: string | null;
  data: T | null;
  updatedAt: string | null;
};

type DashboardState = {
  adminState: RemoteState<DashboardAdminState>;
  deploymentStatus: RemoteState<DeploymentStatus>;
  workers: RemoteState<WorkerListResponse>;
  agents: RemoteState<AgentListResponse>;
  workflowRuns: RemoteState<WorkflowRunSnapshot>;
  workflowActivity: RemoteState<WorkflowGraphActivityReport>;
  approvals: RemoteState<ApprovalIndexResponse>;
  sessions: RemoteState<SessionListResponse>;
  toolCalls: RemoteState<ToolCallsResponse>;
  capacityHealth: RemoteState<CapacityHealthPayload>;
  releaseInfo: RemoteState<ReleaseInfoPayload>;
  workflowDocument: RemoteState<WorkflowEditorDocument>;
  workflowCatalog: RemoteState<WorkflowEditorCatalog>;
  lastRefreshedAt: string | null;
};

type DashboardRemoteKey = Exclude<keyof DashboardState, "lastRefreshedAt">;
const DASHBOARD_NOT_MODIFIED = Symbol("dashboard-not-modified");
type DashboardNotModified = typeof DASHBOARD_NOT_MODIFIED;
type DashboardLoadValue<T> = T | DashboardNotModified;

type DashboardLoadTask<T> = {
  key: DashboardRemoteKey;
  request: (signal: AbortSignal) => Promise<DashboardLoadValue<T>>;
  missing?: string;
  missingIfEmpty?: (value: T) => string | null;
};

type DashboardResponseCacheEntry = {
  etag: string | null;
  data: unknown;
};

type DashboardPillTone = "pass" | "fail" | "missing" | "running" | "idle";

function createRemotePendingState<T>(current?: RemoteState<T>): RemoteState<T> {
  return {
    loading: true,
    error: null,
    missing: null,
    data: current?.data ?? null,
    updatedAt: current?.updatedAt ?? null,
  };
}

function createDashboardPendingState(): DashboardState {
  return {
    adminState: createRemotePendingState(),
    deploymentStatus: createRemotePendingState(),
    workers: createRemotePendingState(),
    agents: createRemotePendingState(),
    workflowRuns: createRemotePendingState(),
    workflowActivity: createRemotePendingState(),
    approvals: createRemotePendingState(),
    sessions: createRemotePendingState(),
    toolCalls: createRemotePendingState(),
    capacityHealth: createRemotePendingState(),
    releaseInfo: createRemotePendingState(),
    workflowDocument: createRemotePendingState(),
    workflowCatalog: createRemotePendingState(),
    lastRefreshedAt: null,
  };
}

function createDirectoryDashboardPendingState(current: DashboardState): DashboardState {
  return {
    ...current,
    agents: createRemotePendingState(current.agents),
    approvals: createRemotePendingState(current.approvals),
    sessions: createRemotePendingState(current.sessions),
    toolCalls: createRemotePendingState(current.toolCalls),
    workflowCatalog: createRemotePendingState(current.workflowCatalog),
    lastRefreshedAt: null,
  };
}

function createGlobalDashboardPendingState(current: DashboardState): DashboardState {
  return {
    ...current,
    workers: createRemotePendingState(current.workers),
    workflowRuns: createRemotePendingState(current.workflowRuns),
    workflowActivity: createRemotePendingState(current.workflowActivity),
    capacityHealth: createRemotePendingState(current.capacityHealth),
    workflowDocument: createRemotePendingState(current.workflowDocument),
    lastRefreshedAt: null,
  };
}

function createReleaseInfoPendingState(current: DashboardState): DashboardState {
  return {
    ...current,
    releaseInfo: {
      ...current.releaseInfo,
      loading: true,
      error: null,
      missing: null,
    },
  };
}

function classifyFailure(reason: unknown): { error: string | null; missing: string | null } {
  if (reason instanceof ApiError) {
    if (reason.status === 404 || reason.status === 410 || reason.status === 501) {
      return {
        error: null,
        missing: reason.message || "This source is not available in the current environment.",
      };
    }
    return { error: reason.message, missing: null };
  }
  if (reason instanceof Error) {
    return { error: reason.message, missing: null };
  }
  return { error: String(reason), missing: null };
}

function remoteFromResult<T>(
  result: PromiseSettledResult<T>,
  missing: string | null = null,
  missingIfEmpty: ((data: T) => string | null) | null = null,
): RemoteState<T> {
  if (result.status === "fulfilled") {
    const emptyMissing = missingIfEmpty?.(result.value);
    return {
      loading: false,
      error: null,
      missing: emptyMissing ?? (missingIfEmpty ? null : missing),
      data: result.value,
      updatedAt: new Date().toISOString(),
    };
  }
  const failure = classifyFailure(result.reason);
  return {
    loading: false,
    error: failure.error,
    missing: failure.error ? null : failure.missing ?? missing,
    data: null,
    updatedAt: new Date().toISOString(),
  };
}

function remoteFromNotModified<T>(current: RemoteState<T>): RemoteState<T> {
  return {
    ...current,
    loading: false,
    error: null,
    missing: current.missing,
    updatedAt: new Date().toISOString(),
  };
}

function statusTone(state: RemoteState<unknown>): DashboardPillTone {
  if (state.loading) return "running";
  if (state.error) return "fail";
  if (state.missing) return "missing";
  return "pass";
}

function statusLabel(state: RemoteState<unknown>): string {
  if (state.loading) return "Loading";
  if (state.error) return "Error";
  if (state.missing) return "Missing";
  return "Live";
}

function stateClassName(tone: DashboardPillTone): string {
  if (tone === "pass") return "state-pass";
  if (tone === "fail") return "state-fail";
  if (tone === "missing") return "state-missing";
  if (tone === "running") return "state-running";
  return "state-idle";
}

function countBy<T>(items: readonly T[], predicate: (item: T) => boolean): number {
  return items.reduce((count, item) => (predicate(item) ? count + 1 : count), 0);
}

function hasActiveAgentStatus(status: string): boolean {
  const normalized = status.trim().toLowerCase();
  return normalized === "running" || normalized === "starting" || normalized === "preparing" || normalized === "active" || normalized === "online";
}

function agentStatusTone(status: string): DashboardPillTone {
  const normalized = status.trim().toLowerCase();
  if (normalized === "failed" || normalized === "error" || normalized === "stale" || normalized === "destroyed") {
    return "fail";
  }
  if (normalized === "starting" || normalized === "preparing") {
    return "running";
  }
  if (hasActiveAgentStatus(normalized)) {
    return "pass";
  }
  return "idle";
}

function workerStatusTone(worker: WorkerSummary): DashboardPillTone {
  if (worker.failed) return "fail";
  if (worker.running) return "pass";
  if (worker.active_state.trim().toLowerCase() === "activating") return "running";
  return "idle";
}

function runStatusTone(status: string): DashboardPillTone {
  const normalized = status.trim().toLowerCase();
  if (
    normalized === "failed" ||
    normalized === "failure" ||
    normalized === "error" ||
    normalized === "errored" ||
    normalized === "cancelled" ||
    normalized === "canceled" ||
    normalized === "aborted" ||
    normalized === "timed_out" ||
    normalized === "timeout"
  ) {
    return "fail";
  }
  if (normalized === "running" || normalized === "active" || normalized === "in_progress") {
    return "running";
  }
  if (normalized === "resumable") {
    return "missing";
  }
  return "pass";
}

function toolCallStatus(item: ToolCallItem): ToolCallStatus {
  const explicitStatus = (item.status ?? "").trim().toLowerCase();
  if (
    explicitStatus === "success" ||
    explicitStatus === "denied" ||
    explicitStatus === "error" ||
    explicitStatus === "pending_approval"
  ) {
    return explicitStatus;
  }

  const decision = (item.decision ?? "").trim().toLowerCase();
  if (decision === "pending_approval") return "pending_approval";
  if (decision === "denied" || decision === "rejected" || decision === "forbidden") return "denied";
  if (decision === "error" || decision === "failed" || decision === "failure") return "error";
  return "success";
}

function toolCallTone(item: ToolCallItem): DashboardPillTone {
  const status = toolCallStatus(item);
  if (status === "error" || status === "denied") return "fail";
  if (status === "pending_approval") return "missing";
  return "pass";
}

function approvalTone(response: ApprovalIndexResponse | null | undefined): DashboardPillTone {
  const health = response?.health;
  if (!health) return "idle";
  if (health.pending > 0) return "running";
  if (health.total === 0) return "missing";
  return "pass";
}

function approvalItemTone(status: ApprovalStatus): DashboardPillTone {
  if (status === "pending") return "running";
  if (status === "approved") return "pass";
  if (status === "expired") return "missing";
  return "fail";
}

function sessionTone(response: SessionListResponse | null | undefined): DashboardPillTone {
  if (!response) return "idle";
  if (response.counts.total === 0) return "missing";
  if (response.counts.active > 0) return "pass";
  return "idle";
}

function sessionItemTone(session: SessionRecord): DashboardPillTone {
  return session.is_expired ? "missing" : "pass";
}

function sessionItemsForStatus(response: SessionListResponse, status: "active" | "expired"): SessionRecord[] {
  const ids = new Set(response.status_ids[status]);
  return response.items.filter((item) => ids.has(item.session_id));
}

function workflowDocumentTone(document: WorkflowEditorDocument | null | undefined): DashboardPillTone {
  if (!document) return "idle";
  if (!document.config_exists && !document.layout_exists) return "missing";
  if (document.validation_diagnostics && document.validation_diagnostics.length > 0) return "running";
  return "pass";
}

function workflowCatalogTone(catalog: WorkflowEditorCatalog | null | undefined): DashboardPillTone {
  if (!catalog) return "idle";
  if (catalog.nodes.length === 0) return "missing";
  return "pass";
}

function openTab(tab: AdminTab, params: Record<string, AdminHashParamValue> = {}) {
  window.location.hash = adminHash(tab, params);
}

function combinedStatusTone(states: RemoteState<unknown>[]): DashboardPillTone {
  if (states.some((state) => state.error)) return "fail";
  if (states.some((state) => state.loading)) return "running";
  if (states.some((state) => state.missing)) return "missing";
  return "pass";
}

function combinedStatusLabel(states: RemoteState<unknown>[]): string {
  if (states.some((state) => state.error)) return "Error";
  if (states.some((state) => state.loading)) return "Loading";
  if (states.some((state) => state.missing)) return "Missing";
  return "Live";
}

function panelTitleList(items: readonly string[]): string {
  return items.filter(Boolean).join(", ");
}

function detailValue(value: string | number | boolean | null | undefined): string {
  if (value == null) return "n/a";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  return String(value);
}

function formatBytes(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "n/a";
  const bytes = Math.max(0, value);
  if (bytes < 1024) return `${Math.round(bytes)} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MiB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GiB`;
}

function formatPercent(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "n/a";
  const digits = Math.abs(value) >= 10 || Number.isInteger(value) ? 0 : 1;
  return `${value.toFixed(digits)}%`;
}

function clampPercent(value: number | null | undefined): number | null {
  if (value == null || !Number.isFinite(value)) return null;
  return Math.max(0, Math.min(100, value));
}

function diskUsedPercent(disk: DiskUsageTelemetry | null | undefined): number | null {
  if (!disk || disk.status !== "available") return null;
  const explicit = clampPercent(disk.used_percent);
  if (explicit != null) return explicit;
  if (disk.total_bytes == null || disk.used_bytes == null || disk.total_bytes <= 0) return null;
  return clampPercent((disk.used_bytes / disk.total_bytes) * 100);
}

function diskDialTone(disk: DiskUsageTelemetry | null | undefined): DashboardPillTone {
  const usedPercent = diskUsedPercent(disk);
  if (usedPercent == null) return "missing";
  if (usedPercent > 80) return "fail";
  if (usedPercent > 50) return "running";
  return "pass";
}

function healthStateTone(health: HealthState): DashboardPillTone {
  if (health === "critical") return "fail";
  if (health === "warning") return "running";
  if (health === "unknown") return "missing";
  return "pass";
}

function capacityWorkerTone(workers: CapacityHealthPayload["workers"]): DashboardPillTone {
  if (workers.total_workers === 0) return "missing";
  if (workers.reachable_workers < workers.total_workers) return "fail";
  if (workers.telemetry_available_workers < workers.total_workers) return "running";
  return "pass";
}

function workerRootDisks(workers: CapacityHealthPayload["workers"]): AvailableDiskUsageTelemetry[] {
  return workers.entries
    .map((entry) => entry.telemetry?.disk?.worker_root ?? null)
    .filter(
      (disk): disk is AvailableDiskUsageTelemetry =>
        disk !== null &&
        disk.status === "available" &&
        disk.total_bytes !== null &&
        disk.used_bytes !== null &&
        disk.free_bytes !== null,
    );
}

function workerDiskSummary(workers: CapacityHealthPayload["workers"]): string {
  const disks = workerRootDisks(workers);
  if (workers.total_workers === 0) return "No configured workers.";
  if (disks.length === 0) return "Worker telemetry unavailable.";
  const totalBytes = disks.reduce((total, disk) => total + disk.total_bytes, 0);
  const usedBytes = disks.reduce((total, disk) => total + disk.used_bytes, 0);
  const usedPercent = totalBytes > 0 ? (usedBytes / totalBytes) * 100 : null;
  const workerCount = `${disks.length}/${workers.total_workers} worker${workers.total_workers === 1 ? "" : "s"}`;
  return `${formatPercent(usedPercent)} (${formatBytes(usedBytes)} / ${formatBytes(totalBytes)}) across ${workerCount}`;
}

function workerDiskHealth(workers: CapacityHealthPayload["workers"]): HealthState {
  const disks = workerRootDisks(workers);
  if (disks.length === 0) return "unknown";
  if (disks.some((disk) => disk.health === "critical")) return "critical";
  if (disks.some((disk) => disk.health === "warning")) return "warning";
  if (disks.length < workers.total_workers) return "unknown";
  return "ok";
}

function workerHostLabel(workerUrl: string): string {
  try {
    const parsed = new URL(workerUrl);
    return parsed.host || workerUrl;
  } catch {
    return workerUrl;
  }
}

function diskUsageDetail(disk: DiskUsageTelemetry | null | undefined): string {
  if (!disk || disk.status !== "available") {
    return disk?.errors[0]?.message ?? "Telemetry unavailable.";
  }
  return `${formatBytes(disk.used_bytes)} / ${formatBytes(disk.total_bytes)} used, ${formatBytes(disk.free_bytes)} free.`;
}

function capacitySummary(payload: CapacityHealthPayload): string {
  const disk = payload.control_plane.disk;
  return [
    `Disk ${formatPercent(disk.used_percent)} used`,
    `${formatBytes(disk.free_bytes)} free`,
    `${payload.workers.reachable_workers}/${payload.workers.total_workers} workers reachable`,
    `${payload.queue.pending_count} queued`,
    `${payload.prompts.unread_count} unread prompts`,
  ].join(" · ");
}

function sessionStorageSampleSummary(sample: SessionStorageSample | null | undefined): string {
  if (!sample) return "n/a";
  return `${sample.session_id} · ${formatBytes(sample.size_bytes)}${sample.backend ? ` · ${sample.backend}` : ""}`;
}

function sessionWakeCue(session: SessionRecord): string | null {
  const metadata = session.metadata;
  if (!metadata || typeof metadata !== "object" || Array.isArray(metadata)) {
    return null;
  }

  const readString = (...keys: string[]): string | null => {
    for (const key of keys) {
      const value = metadata[key];
      if (typeof value === "string" && value.trim()) {
        return value.trim();
      }
    }
    return null;
  };

  const readSchedule = (value: unknown): string | null => {
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      return null;
    }
    const schedule = value as Record<string, unknown>;
    const cron = typeof schedule.cron === "string" && schedule.cron.trim() ? schedule.cron.trim() : null;
    const resumeAt =
      typeof schedule.resume_at === "string" && schedule.resume_at.trim() ? schedule.resume_at.trim() : null;
    if (cron) {
      return `Recurring ${cron}`;
    }
    if (resumeAt) {
      return `Resume at ${formatTimestamp(resumeAt)}`;
    }
    return null;
  };

  const directCue =
    readString("next_wake_at", "wake_at", "resume_at") ?? readSchedule(metadata.schedule) ?? readSchedule(metadata.wake_schedule);
  if (directCue) {
    return directCue.startsWith("Recurring") ? directCue : `Wake cue: ${directCue}`;
  }

  const wakeSchedules = metadata.wake_schedules;
  if (Array.isArray(wakeSchedules) && wakeSchedules.length > 0) {
    const first = wakeSchedules[0];
    const cue = readSchedule(first);
    if (cue) {
      return `${cue} (${wakeSchedules.length} configured)`;
    }
    return `${wakeSchedules.length} wake schedule${wakeSchedules.length === 1 ? "" : "s"} configured`;
  }

  const wakeScheduleCount = metadata.wake_schedule_count;
  if (typeof wakeScheduleCount === "number" && Number.isFinite(wakeScheduleCount) && wakeScheduleCount > 0) {
    return `${wakeScheduleCount} wake schedule${wakeScheduleCount === 1 ? "" : "s"} configured`;
  }

  return null;
}

function remoteMessage(state: RemoteState<unknown>, missingFallback: string, summary: string | null | undefined): string {
  if (state.error) return state.error;
  if (state.missing) return state.missing;
  if (state.loading) return "Loading…";
  return summary?.trim() || missingFallback;
}

function dashboardBlockSlug(title: string): string {
  return title.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "block";
}

function DashboardBlock({
  title,
  state,
  missingFallback,
  summary,
  actions,
  children,
}: {
  title: string;
  state: RemoteState<unknown>;
  missingFallback: string;
  summary?: string | null;
  actions?: ReactNode;
  children: ReactNode;
}) {
  const tone = statusTone(state);
  const showChildren = !state.loading && !state.error && !state.missing && state.data !== null;
  if (title === "Admin state" || title === "Deployment") {
    return null;
  }
  if (title === "Workers") {
    return (
      <DashboardCollapsibleBlock
        title={title}
        state={state}
        missingFallback={missingFallback}
        summary={summary}
        actions={actions}
      >
        {children}
      </DashboardCollapsibleBlock>
    );
  }
  return (
    <section className={`dashboard-block dashboard-block-${dashboardBlockSlug(title)}`}>
      <div className="dashboard-block-header">
        <div className="dashboard-block-copy">
          <div className="dashboard-block-title-row">
            <div className="eyebrow">{title}</div>
            <span className={`status-pill ${stateClassName(tone)}`}>{statusLabel(state)}</span>
          </div>
          <p>{remoteMessage(state, missingFallback, summary)}</p>
        </div>
        {actions ? <div className="dashboard-block-actions">{actions}</div> : null}
      </div>
      {state.loading && !state.data ? <div className="empty-state">Loading…</div> : null}
      {!state.loading && !state.error && state.missing && !state.data ? <div className="empty-state">{state.missing}</div> : null}
      {state.error && !state.data ? <div className="callout error-callout">{state.error}</div> : null}
      {showChildren ? children : null}
    </section>
  );
}

function DashboardCollapsibleBlock({
  title,
  state,
  missingFallback,
  summary,
  actions,
  defaultOpen = false,
  children,
}: {
  title: string;
  state: RemoteState<unknown>;
  missingFallback: string;
  summary?: string | null;
  actions?: ReactNode;
  defaultOpen?: boolean;
  children: ReactNode;
}) {
  const tone = statusTone(state);
  const showChildren = !state.loading && !state.error && !state.missing && state.data !== null;
  return (
    <details className={`dashboard-block dashboard-block-${dashboardBlockSlug(title)} dashboard-block-collapsible`} open={defaultOpen}>
      <summary className="dashboard-block-summary">
        <div className="dashboard-block-header">
          <div className="dashboard-block-copy">
            <div className="dashboard-block-title-row">
              <div className="eyebrow">{title}</div>
              <span className={`status-pill ${stateClassName(tone)}`}>{statusLabel(state)}</span>
            </div>
            <p>{remoteMessage(state, missingFallback, summary)}</p>
          </div>
          <div className="dashboard-block-actions">
            {actions}
            <span className="dashboard-panel-expand-control dashboard-block-expand-control" aria-hidden="true">
              <span className="dashboard-panel-expand-text-closed">+</span>
              <span className="dashboard-panel-expand-text-open">-</span>
            </span>
          </div>
        </div>
      </summary>
      <div className="dashboard-block-collapsible-body">
        {state.loading && !state.data ? <div className="empty-state">Loading...</div> : null}
        {!state.loading && !state.error && state.missing && !state.data ? <div className="empty-state">{state.missing}</div> : null}
        {state.error && !state.data ? <div className="callout error-callout">{state.error}</div> : null}
        {showChildren ? children : null}
      </div>
    </details>
  );
}

type DashboardIconName = AdminTab | "refresh" | "newer" | "older";

function DashboardIcon({ name }: { name: DashboardIconName }) {
  const icons: Record<string, ReactNode> = {
    refresh: <DashboardRefreshIcon />,
    newer: <DashboardNewerIcon />,
    older: <DashboardOlderIcon />,
    workflow: <DashboardWorkflowIcon />,
    approvals: <DashboardApprovalsIcon />,
    sessions: <DashboardSessionsIcon />,
    "tool-calls": <DashboardToolsIcon />,
    configuration: <DashboardConfigurationIcon />,
    workers: <DashboardWorkersIcon />,
    agents: <DashboardAgentsIcon />,
    reports: <DashboardReportsIcon />,
    vms: <DashboardVmsIcon />,
    artifacts: <DashboardArtifactsIcon />,
    addons: <DashboardAddonsIcon />,
    skills: <DashboardSkillsIcon />,
    issues: <DashboardIssuesIcon />,
    "tool-visibility": <DashboardToolVisibilityIcon />,
    "mcp-tools": <DashboardMcpToolsIcon />,
    users: <DashboardUsersIcon />,
    dashboard: <DashboardWorkflowIcon />,
  };

  return icons[name] ?? icons.workflow;
}

function TabButton({ tab, label }: { tab: AdminTab; label: string }) {
  return (
    <button
      type="button"
      className="admin-shell-button admin-shell-button-muted dashboard-icon-button"
      aria-label={label}
      title={label}
      onClick={() => openTab(tab)}
    >
      <DashboardIcon name={tab} />
    </button>
  );
}

function DashboardPanel({
  eyebrow,
  title,
  statusClassName,
  statusLabel,
  className = "",
  defaultOpen = false,
  children,
}: {
  eyebrow: string;
  title: string;
  statusClassName: string;
  statusLabel: ReactNode;
  className?: string;
  defaultOpen?: boolean;
  children: ReactNode;
}) {
  const [isOpen, setIsOpen] = useState(defaultOpen);

  if (eyebrow === "Agent inventory") {
    return null;
  }

  return (
    <details
      className={["admin-panel", "dashboard-panel", className].filter(Boolean).join(" ")}
      open={isOpen}
      onToggle={(event) => setIsOpen(event.currentTarget.open)}
    >
      <summary className="panel-heading dashboard-panel-summary">
        <div className="dashboard-panel-summary-copy">
          <div className="dashboard-panel-kicker-row">
            <div className="eyebrow">{eyebrow}</div>
            <div className="dashboard-panel-summary-actions">
              <span className={`status-pill ${statusClassName}`}>{statusLabel}</span>
              <span className="dashboard-panel-expand-control" aria-hidden="true">
                <span className="dashboard-panel-expand-text-closed">+</span>
                <span className="dashboard-panel-expand-text-open">-</span>
              </span>
            </div>
          </div>
          <h2>{title}</h2>
        </div>
      </summary>
      <div className="panel-body dashboard-panel-body">{children}</div>
    </details>
  );
}

type DashboardLink = {
  tab: AdminTab;
  label: string;
  value: string;
  params?: Record<string, AdminHashParamValue>;
};

type DashboardMetricRowItem = {
  label: string;
  value: ReactNode;
  detail?: ReactNode;
};

type DashboardMetricChipItem = {
  label: string;
  value: ReactNode;
  tone?: DashboardPillTone;
};

type DashboardCompactRowProps = {
  title: ReactNode;
  meta?: ReactNode;
  detail?: ReactNode;
  links?: DashboardLink[];
  statusText?: ReactNode;
  statusTone?: DashboardPillTone;
};

function DashboardLinkRow({ links }: { links: DashboardLink[] }) {
  if (links.length === 0) {
    return null;
  }

  return (
    <div className="dashboard-link-row">
      {links.map((link) =>
        link.params ? (
          <button
            key={`${link.tab}:${link.value}`}
            type="button"
            className="dashboard-link-chip"
            title={`${link.label}: ${link.value}`}
            onClick={() => openTab(link.tab, link.params)}
          >
            <span>{link.label}</span>
            <strong>{link.value}</strong>
          </button>
        ) : (
          <span key={`${link.tab}:${link.value}`} className="dashboard-link-chip dashboard-link-chip-static" title={`${link.label}: ${link.value}`}>
            <span>{link.label}</span>
            <strong>{link.value}</strong>
          </span>
        ),
      )}
    </div>
  );
}

function DashboardMetricRow({ label, value, detail }: DashboardMetricRowItem) {
  return (
    <div className="dashboard-metric-row">
      <div className="dashboard-metric-label">{label}</div>
      <div className="dashboard-metric-copy">
        <strong className="dashboard-metric-value">{value}</strong>
        {detail ? <div className="dashboard-metric-detail">{detail}</div> : null}
      </div>
    </div>
  );
}

function DashboardMetricRows({ items, className = "" }: { items: DashboardMetricRowItem[]; className?: string }) {
  return (
    <div className={["dashboard-metric-list", className].filter(Boolean).join(" ")}>
      {items.map((item, index) => (
        <DashboardMetricRow key={`${item.label}-${index}`} {...item} />
      ))}
    </div>
  );
}

function DashboardMetricChips({ items, className = "" }: { items: DashboardMetricChipItem[]; className?: string }) {
  if (items.length === 0) {
    return null;
  }

  return (
    <div className={["dashboard-chip-row", className].filter(Boolean).join(" ")}>
      {items.map((item, index) => (
        <span
          key={`${item.label}-${index}`}
          className={`status-pill ${stateClassName(item.tone ?? "idle")}`}
          title={typeof item.value === "string" || typeof item.value === "number" ? `${item.label}: ${item.value}` : item.label}
        >
          <span>{item.label}</span>
          <strong>{item.value}</strong>
        </span>
      ))}
    </div>
  );
}

function DashboardCompactRows({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={["dashboard-row-list", className].filter(Boolean).join(" ")}>{children}</div>;
}

function DashboardCompactRow({ title, meta, detail, links, statusText, statusTone = "idle" }: DashboardCompactRowProps) {
  return (
    <div className="dashboard-row-item">
      <div className="dashboard-row-copy">
        <strong className="dashboard-row-title">{title}</strong>
        {meta ? <span className="dashboard-row-meta">{meta}</span> : null}
        {detail ? <span className="dashboard-row-detail">{detail}</span> : null}
        {links ? <DashboardLinkRow links={links} /> : null}
      </div>
      {statusText ? <span className={`status-pill ${stateClassName(statusTone)}`}>{statusText}</span> : null}
    </div>
  );
}

function DashboardDiskDial({
  label,
  disk,
  detail,
}: {
  label: string;
  disk: DiskUsageTelemetry | null | undefined;
  detail?: ReactNode;
}) {
  const usedPercent = diskUsedPercent(disk);
  const clampedPercent = usedPercent ?? 0;
  const tone = diskDialTone(disk);
  const dialStyle = {
    "--dashboard-disk-dial-percent": `${clampedPercent}%`,
  } as CSSProperties;
  return (
    <div className="dashboard-disk-card">
      <div className={`dashboard-disk-dial ${stateClassName(tone)}`} style={dialStyle} role="img" aria-label={`${label} disk usage ${formatPercent(usedPercent)}`}>
        <span>{formatPercent(usedPercent)}</span>
      </div>
      <div className="dashboard-disk-copy">
        <strong>{label}</strong>
        <span>{diskUsageDetail(disk)}</span>
        {detail ? <span>{detail}</span> : null}
      </div>
    </div>
  );
}

function AgentTooltipContent({ agent }: { agent: AgentRecord }) {
  return (
    <span className="dashboard-agent-tooltip">
      <strong>{agentDisplayLabel(agent)}</strong>
      <span>Status: {agent.status}</span>
      <span>Directory: {agent.directory_path}</span>
      <span>Backend: {agent.backend}</span>
      {agent.session_id ? <span>Session: {agent.session_id}</span> : null}
      {agent.updated_at ? <span>Updated: {formatTimestamp(agent.updated_at)}</span> : null}
    </span>
  );
}

function DashboardAgentInventory({
  agents,
  onOpenAgent,
}: {
  agents: AgentRecord[];
  onOpenAgent: (agent: AgentRecord) => void;
}) {
  const sortedAgents = sortedAgentInventory(agents);
  const activeCount = countBy(agents, (agent) => hasActiveAgentStatus(agent.status));
  const inactiveCount = agents.length - activeCount;
  return (
    <div className="dashboard-agent-inventory">
      <div className="dashboard-agent-icon-grid" aria-label="Agent inventory">
        {sortedAgents.map((agent) => {
          const tone = agentInventoryTone(agent);
          return (
            <Tooltip key={`${agent.backend}:${agent.agent_id}`} content={<AgentTooltipContent agent={agent} />}>
              <button
                type="button"
                className={`dashboard-agent-icon ${stateClassName(tone)}`}
                aria-label={`Open agent ${agentDisplayLabel(agent)}`}
                onClick={() => onOpenAgent(agent)}
              >
                <DashboardIcon name="agents" />
              </button>
            </Tooltip>
          );
        })}
      </div>
      <div className="dashboard-agent-totals">
        <span>
          Total <strong>{agents.length}</strong>
        </span>
        <span>
          Active <strong>{activeCount}</strong>
        </span>
        <span>
          Inactive <strong>{inactiveCount}</strong>
        </span>
      </div>
    </div>
  );
}

function makeQuery(params: Record<string, string | number | boolean | null | undefined>): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value == null) continue;
    const text = typeof value === "string" ? value.trim() : String(value);
    if (!text) continue;
    query.set(key, text);
  }
  const result = query.toString();
  return result ? `?${result}` : "";
}

function dashboardCacheParams(refreshKey: number | null = null) {
  return {
    _dashboard: "1",
    _dashboard_refresh: refreshKey && refreshKey > 0 ? refreshKey : null,
  };
}

function dashboardAgentInventoryUrl(directoryPath: string, refreshKey: number | null = null): string {
  return `/api/admin/agents${makeQuery({
    directory_path: directoryPath,
    direct_only: false,
    include_inactive: true,
    backend: "all",
    summary_only: true,
    _dashboard_agent_inventory: true,
    reconcile_containers: false,
    omit_vm_state: true,
    ...dashboardCacheParams(refreshKey),
  })}`;
}

function workerSummary(workers: WorkerSummary[]): string {
  if (workers.length === 0) return "No workers are reporting.";
  const running = countBy(workers, (worker) => worker.running);
  const failed = countBy(workers, (worker) => worker.failed);
  return `${workers.length} workers, ${running} running, ${failed} failed.`;
}

function agentSummary(items: AgentRecord[]): string {
  if (items.length === 0) return "No agents were returned for this directory scope.";
  const active = countBy(items, (item) => hasActiveAgentStatus(item.status));
  const failed = countBy(items, (item) => agentStatusTone(item.status) === "fail");
  return `${items.length} agents, ${active} active, ${failed} flagged.`;
}

function agentInventoryTone(agent: AgentRecord): DashboardPillTone {
  return agentStatusTone(agent.status);
}

function agentInventoryRank(agent: AgentRecord): number {
  const tone = agentInventoryTone(agent);
  if (tone === "pass") return 0;
  if (tone === "running") return 1;
  if (tone === "fail") return 2;
  return 3;
}

function agentDisplayLabel(agent: AgentRecord): string {
  return agent.target_name || agent.session_id || agent.agent_id;
}

function sortedAgentInventory(items: AgentRecord[]): AgentRecord[] {
  return [...items].sort((left, right) => {
    const byState = agentInventoryRank(left) - agentInventoryRank(right);
    if (byState !== 0) return byState;
    const byDirectory = left.directory_path.localeCompare(right.directory_path, undefined, {
      numeric: true,
      sensitivity: "base",
    });
    if (byDirectory !== 0) return byDirectory;
    return agentDisplayLabel(left).localeCompare(agentDisplayLabel(right), undefined, {
      numeric: true,
      sensitivity: "base",
    });
  });
}

function runsSummary(snapshot: WorkflowRunSnapshot): string {
  if (snapshot.runs.length === 0) return "No workflow runs have been observed yet.";
  const running = countBy(snapshot.runs, (run) => runStatusTone(run.run_state) === "running");
  const failed = countBy(snapshot.runs, (run) => runStatusTone(run.run_state) === "fail");
  const resumable = countBy(snapshot.runs, (run) => Boolean(run.resumable));
  return `${snapshot.runs.length} runs, ${running} active, ${failed} failed, ${resumable} resumable.`;
}

function graphActivitySummary(report: WorkflowGraphActivityReport): string {
  if (report.graphs.length === 0) return "No workflow graph activity is available yet.";
  const latest = report.graphs.reduce<string | null>((current, graph) => {
    if (!graph.latest_activity_at) return current;
    if (!current) return graph.latest_activity_at;
    return new Date(graph.latest_activity_at) > new Date(current) ? graph.latest_activity_at : current;
  }, null);
  return `${report.graphs.length} graphs, ${report.graphs.reduce((sum, graph) => sum + graph.activity_count, 0)} activities${latest ? `, latest ${formatTimestamp(latest)}` : ""}.`;
}

function approvalsSummary(response: ApprovalIndexResponse): string {
  if (response.health.total === 0) return "No approval requests are waiting for review.";
  const oldest = response.health.oldest_pending_age_seconds;
  return `${response.health.pending} pending, ${response.health.expired} expired${oldest == null ? "" : `, oldest ${formatRelativeSeconds(oldest)}`}.`;
}

function sessionsSummary(response: SessionListResponse): string {
  if (response.counts.total === 0) return "No sessions are active or expired in the current directory scope.";
  return `${response.counts.active} active, ${response.counts.expired} expired, ${response.counts.total} total.`;
}

function toolCallsSummary(response: ToolCallsResponse): string {
  const counts = response.counts?.status_counts ?? {};
  const total = response.counts?.returned ?? response.items.length;
  const success = counts.success ?? response.counts?.success ?? 0;
  const denied = counts.denied ?? response.counts?.denied ?? 0;
  const error = counts.error ?? response.counts?.error ?? 0;
  return `${total} recent calls, ${success} successful, ${denied} denied, ${error} errored.`;
}

function workflowDocumentSummary(document: WorkflowEditorDocument): string {
  const graphCount = document.graphs?.length ?? 0;
  const schemaCount = document.output_schemas ? Object.keys(document.output_schemas).length : 0;
  return `${graphCount} graphs, revision ${document.revision}${schemaCount ? `, ${schemaCount} output schema${schemaCount === 1 ? "" : "s"}` : ""}.`;
}

function workflowCatalogSummary(catalog: WorkflowEditorCatalog): string {
  return `${catalog.nodes.length} catalog node${catalog.nodes.length === 1 ? "" : "s"} from ${catalog.worker_id}.`;
}

function releaseInfoSummary(releaseInfo: ReleaseInfoPayload | null): string | null {
  if (!releaseInfo) {
    return null;
  }

  const parts: string[] = [];
  parts.push(releaseInfo.version ? `Version ${releaseInfo.version}` : "Version unavailable");
  parts.push(releaseInfo.build ? `Build ${releaseInfo.build}` : "Build unavailable");
  if (releaseInfo.changelog?.heading) {
    parts.push(releaseInfo.changelog.heading);
  } else if (releaseInfo.missing_reasons.length > 0) {
    parts.push("Recent changelog unavailable");
  }
  return parts.join(" · ");
}

function releaseInfoTone(state: RemoteState<ReleaseInfoPayload>): DashboardPillTone {
  if (state.data && state.data.missing_reasons.length > 0) {
    return "missing";
  }
  return statusTone(state);
}

function releaseInfoStatusLabel(state: RemoteState<ReleaseInfoPayload>): string {
  if (state.data && state.data.missing_reasons.length > 0) {
    return "Missing";
  }
  return statusLabel(state);
}

function downloadChangelog() {
  window.location.assign("/api/admin/release-info/changelog");
}

function statusByCountClass(count: number): DashboardPillTone {
  if (count > 0) return "pass";
  return "missing";
}

function ProductContextPanel({
  releaseInfo,
  releaseInfoState,
  onLoadNewsEntry,
}: {
  releaseInfo: ReleaseInfoPayload | null;
  releaseInfoState: RemoteState<ReleaseInfoPayload>;
  onLoadNewsEntry: (targetIndex: number) => void;
}) {
  const releaseSummary = releaseInfoSummary(releaseInfo);
  return (
    <DashboardPanel
      eyebrow="Product context"
      title="Version and changelog"
      className="dashboard-release-panel"
      defaultOpen
      statusClassName={stateClassName(releaseInfoTone(releaseInfoState))}
      statusLabel={releaseInfoStatusLabel(releaseInfoState)}
    >
      {releaseInfoState.loading && !releaseInfo ? <div className="empty-state">Loading…</div> : null}
      {!releaseInfoState.loading && !releaseInfoState.error && releaseInfoState.missing && !releaseInfo ? (
        <div className="empty-state">{releaseInfoState.missing}</div>
      ) : null}
      {releaseInfoState.error && !releaseInfo ? <div className="callout error-callout">{releaseInfoState.error}</div> : null}

      {releaseInfo ? (
        <div className="dashboard-product-context">
          {releaseInfo.news ? (
            <article className="release-news-entry">
              <h3 className="release-news-title">{releaseInfo.news.heading ?? "Latest update"}</h3>
              <div className="release-news-copy">
                {releaseInfo.news.blocks.map((block, index) =>
                  renderReleaseNewsBlock(block, `${releaseInfo.news?.heading ?? "news"}-${block.kind}-${index}`),
                )}
            </div>
            <p className="panel-meta release-news-source">
              {[releaseInfo.news.source ?? "NEWS.md", releaseInfo.news.path ?? null].filter(Boolean).join(" · ")}
            </p>
            <div className="release-news-pager">
              <button
                type="button"
                className="dashboard-icon-button release-news-page-button"
                aria-label="Newer"
                title="Show newer news entry"
                disabled={!releaseInfo.news.has_previous || releaseInfoState.loading}
                onClick={() => {
                  if (releaseInfo.news?.previous_index != null) {
                    onLoadNewsEntry(releaseInfo.news.previous_index);
                  }
                }}
              >
                <DashboardIcon name="newer" />
              </button>
              <span>
                Entry {releaseInfo.news.index + 1}
                {releaseInfo.news.index === 0 ? " · Latest" : ""}
              </span>
              <button
                type="button"
                className="dashboard-icon-button release-news-page-button"
                aria-label="Older"
                title="Show older news entry"
                disabled={!releaseInfo.news.has_next || releaseInfoState.loading}
                onClick={() => {
                  if (releaseInfo.news?.next_index != null) {
                    onLoadNewsEntry(releaseInfo.news.next_index);
                  }
                }}
              >
                <DashboardIcon name="older" />
                </button>
              </div>
            </article>
          ) : null}

          <section className="dashboard-release-secondary" aria-label="Release metadata">
            <div className="dashboard-release-secondary-heading">
              {releaseSummary ? (
                <p className="panel-meta dashboard-release-summary">
                  {releaseSummary}
                </p>
              ) : null}
              {releaseInfo.changelog ? (
                <div className="feature-toolbar-actions icon-actions dashboard-release-actions">
                  <button type="button" aria-label="Download changelog" title="Download changelog" onClick={downloadChangelog}>
                    <DownloadIcon />
                  </button>
                </div>
              ) : null}
            </div>
            <DashboardMetricRows
              className="dashboard-release-metadata"
              items={[
                {
                  label: "Version",
                  value: releaseInfo.version ?? "Version unavailable",
                  detail: releaseInfo.version_source ?? "No version source.",
                },
                {
                  label: "Build",
                  value: releaseInfo.build ?? "Build unavailable",
                  detail: releaseInfo.build_source ?? "No build source.",
                },
                {
                  label: "Changelog",
                  value: releaseInfo.changelog ? releaseInfo.changelog.heading ?? "Recent release entries" : "Changelog unavailable",
                  detail: releaseInfo.changelog
                    ? [releaseInfo.changelog.source ?? "CHANGELOG.md", releaseInfo.changelog.path ?? null].filter(Boolean).join(" · ")
                    : "No changelog source.",
                },
              ]}
            />
          </section>

          {releaseInfo.missing_reasons.length > 0 ? (
            <div className="release-missing-list">
              <span>Missing details</span>
              <strong>{releaseInfo.missing_reasons.join(" · ")}</strong>
            </div>
          ) : null}
        </div>
      ) : null}
    </DashboardPanel>
  );
}

export function OperationsDashboard() {
  const { selectedDirectoryPath, setSelectedDirectoryPath, privilegedUnlocked } = useAdminUi();
  const documentVisible = useDocumentVisible();
  const [refreshNonce, setRefreshNonce] = useState(0);
  const [newsIndex, setNewsIndex] = useState(0);
  const [state, setState] = useState<DashboardState>(() => createDashboardPendingState());

  const directoryLoadIdRef = useRef(0);
  const globalLoadIdRef = useRef(0);
  const releaseLoadIdRef = useRef(0);
  const agentInventoryLoadIdRef = useRef(0);
  const dashboardResponseCacheRef = useRef<Record<string, DashboardResponseCacheEntry>>({});

  async function fetchDashboardJson<T>(
    key: DashboardRemoteKey,
    path: string,
    signal?: AbortSignal,
  ): Promise<DashboardLoadValue<T>> {
    const cacheKey = `${key}\n${path}`;
    const cached = dashboardResponseCacheRef.current[cacheKey];
    const result = await fetchJsonConditional<T>(
      path,
      signal ? { signal } : undefined,
      cached?.etag ?? null,
    );
    if (result.status === "not_modified") {
      if (cached) {
        dashboardResponseCacheRef.current[cacheKey] = {
          ...cached,
          etag: result.etag,
        };
        return cached.data as T;
      }
      return DASHBOARD_NOT_MODIFIED;
    }
    dashboardResponseCacheRef.current[cacheKey] = {
      etag: result.etag,
      data: result.data,
    };
    return result.data;
  }

  function startDashboardLoad(
    loadIdRef: MutableRefObject<number>,
    tasks: DashboardLoadTask<any>[],
    pendingState: (current: DashboardState) => DashboardState,
  ) {
    const controller = new AbortController();
    const loadId = loadIdRef.current + 1;
    loadIdRef.current = loadId;
    let cancelled = false;
    setState(pendingState);

    function isCurrentLoad() {
      return !cancelled && !controller.signal.aborted && loadIdRef.current === loadId;
    }

    for (const task of tasks) {
      void task
        .request(controller.signal)
        .then((value) => {
          if (!isCurrentLoad()) return;
          if (value === DASHBOARD_NOT_MODIFIED) {
            setState(
              (current) =>
                ({
                  ...current,
                  [task.key]: remoteFromNotModified(current[task.key] as RemoteState<any>),
                }) as DashboardState,
            );
            return;
          }
          const result: PromiseSettledResult<any> = { status: "fulfilled", value };
          setState(
            (current) =>
              ({
                ...current,
                [task.key]: remoteFromResult(result, task.missing ?? null, task.missingIfEmpty ?? null),
              }) as DashboardState,
          );
        })
        .catch((reason: unknown) => {
          if (!isCurrentLoad()) return;
          const result: PromiseSettledResult<any> = { status: "rejected", reason };
          setState(
            (current) =>
              ({
                ...current,
                [task.key]: remoteFromResult(result, task.missing ?? null, task.missingIfEmpty ?? null),
              }) as DashboardState,
          );
        });
    }

    return () => {
      cancelled = true;
      controller.abort();
    };
  }

  useEffect(() => {
    if (!documentVisible) {
      return;
    }
    const cacheParams = dashboardCacheParams(refreshNonce);
    const directoryTasks: DashboardLoadTask<any>[] = [
      {
        key: "approvals",
        request: (signal) =>
          fetchDashboardJson<ApprovalIndexResponse>(
            "approvals",
            `/api/admin/approvals${makeQuery({
              statuses: "pending",
              directory_path: selectedDirectoryPath,
              direct_only: true,
              ...cacheParams,
            })}`,
            signal,
          ),
        missing: "No approval requests are waiting for review.",
        missingIfEmpty: (value) => (value.health.total === 0 ? "No approval requests are waiting for review." : null),
      },
      {
        key: "sessions",
        request: (signal) =>
          fetchDashboardJson<SessionListResponse>(
            "sessions",
            `/api/admin/sessions${makeQuery({
              include_expired: true,
              directory_path: selectedDirectoryPath,
              direct_only: true,
              ...cacheParams,
            })}`,
            signal,
          ),
        missing: "No sessions are active or expired in the current directory scope.",
        missingIfEmpty: (value) =>
          value.counts.total === 0 ? "No sessions are active or expired in the current directory scope." : null,
      },
      {
        key: "toolCalls",
        request: (signal) =>
          fetchDashboardJson<ToolCallsResponse>(
            "toolCalls",
            `/api/admin/tool-calls${makeQuery({
              directory_path: selectedDirectoryPath,
              direct_only: true,
              limit: 5,
              ...cacheParams,
            })}`,
            signal,
          ),
        missing: "No tool-call audit entries were returned.",
        missingIfEmpty: (value) => (value.items.length === 0 ? "No tool-call audit entries were returned." : null),
      },
      {
        key: "workflowCatalog",
        request: (signal) =>
          fetchDashboardJson<WorkflowEditorCatalog>(
            "workflowCatalog",
            `/api/workflow-editor/catalog${makeQuery({ directory_path: selectedDirectoryPath, ...cacheParams })}`,
            signal,
          ),
        missing: "No catalog nodes are available for this directory.",
        missingIfEmpty: (value) => (value.nodes.length === 0 ? "No catalog nodes are available for this directory." : null),
      },
    ];

    return startDashboardLoad(directoryLoadIdRef, directoryTasks, createDirectoryDashboardPendingState);
  }, [documentVisible, selectedDirectoryPath, refreshNonce]);

  useEffect(() => {
    if (!documentVisible) {
      return;
    }
    const controller = new AbortController();
    const loadId = agentInventoryLoadIdRef.current + 1;
    agentInventoryLoadIdRef.current = loadId;
    let cancelled = false;
    let timer: number | null = null;
    let firstPoll = true;

    function isCurrentLoad() {
      return !cancelled && !controller.signal.aborted && agentInventoryLoadIdRef.current === loadId;
    }

    async function pollAgents(refreshKey: number | null = null) {
      const showInitialLoading = firstPoll;
      firstPoll = false;
      if (showInitialLoading) {
        setState((current) => ({
          ...current,
          agents: {
            ...current.agents,
            loading: true,
            error: null,
            missing: null,
          },
        }));
      }

      try {
        const value = await fetchDashboardJson<AgentListResponse>(
          "agents",
          dashboardAgentInventoryUrl(selectedDirectoryPath, refreshKey),
          controller.signal,
        );
        if (!isCurrentLoad()) return;
        if (value === DASHBOARD_NOT_MODIFIED) {
          setState((current) => ({
            ...current,
            agents: remoteFromNotModified(current.agents),
          }));
          return;
        }
        const result: PromiseSettledResult<AgentListResponse> = { status: "fulfilled", value };
        setState((current) => ({
          ...current,
          agents: remoteFromResult(
            result,
            "No agents were returned for this directory scope.",
            (payload) => (payload.items.length === 0 ? "No agents were returned for this directory scope." : null),
          ),
        }));
      } catch (reason: unknown) {
        if (!isCurrentLoad()) return;
        const result: PromiseSettledResult<AgentListResponse> = { status: "rejected", reason };
        setState((current) => ({
          ...current,
          agents: remoteFromResult(result, "No agents were returned for this directory scope."),
        }));
      }
    }

    async function pollLoop(refreshKey: number | null = null) {
      await pollAgents(refreshKey);
      if (!isCurrentLoad()) return;
      timer = window.setTimeout(() => {
        void pollLoop();
      }, DASHBOARD_AGENT_INVENTORY_POLL_MS);
    }

    void pollLoop(refreshNonce > 0 ? refreshNonce : null);
    return () => {
      cancelled = true;
      controller.abort();
      if (timer !== null) {
        window.clearTimeout(timer);
      }
    };
  }, [documentVisible, selectedDirectoryPath, refreshNonce]);

  useEffect(() => {
    if (!documentVisible) {
      return;
    }
    const cacheParams = dashboardCacheParams(refreshNonce);
    const globalTasks: DashboardLoadTask<any>[] = [
      {
        key: "workers",
        request: (signal) => fetchDashboardJson<WorkerListResponse>("workers", `/api/workers${makeQuery(cacheParams)}`, signal),
        missing: "No workers are reporting.",
        missingIfEmpty: (value) => (value.workers.length === 0 ? "No workers are reporting." : null),
      },
      {
        key: "workflowRuns",
        request: (signal) =>
          fetchDashboardJson<WorkflowRunSnapshot>("workflowRuns", `/api/workflow-editor/runs${makeQuery(cacheParams)}`, signal),
        missing: "No workflow runs have been observed yet.",
        missingIfEmpty: (value) => (value.runs.length === 0 ? "No workflow runs have been observed yet." : null),
      },
      {
        key: "workflowActivity",
        request: (signal) =>
          fetchDashboardJson<WorkflowGraphActivityReport>(
            "workflowActivity",
            `/api/admin/reports/workflow-graph-activity${makeQuery({ limit: 5, ...cacheParams })}`,
            signal,
          ),
        missing: "No workflow graph activity is available yet.",
        missingIfEmpty: (value) => (value.graphs.length === 0 ? "No workflow graph activity is available yet." : null),
      },
      {
        key: "capacityHealth",
        request: (signal) =>
          fetchDashboardJson<CapacityHealthPayload>("capacityHealth", `/api/admin/capacity-health${makeQuery(cacheParams)}`, signal),
      },
      {
        key: "workflowDocument",
        request: (signal) =>
          fetchDashboardJson<WorkflowEditorDocument>("workflowDocument", `/api/workflow-editor/document${makeQuery(cacheParams)}`, signal),
        missing: "No workflow document has been created yet.",
        missingIfEmpty: (value) => (!value.config_exists && !value.layout_exists ? "No workflow document has been created yet." : null),
      },
    ];

    return startDashboardLoad(globalLoadIdRef, globalTasks, createGlobalDashboardPendingState);
  }, [documentVisible, refreshNonce]);

  useEffect(() => {
    if (!documentVisible) {
      return;
    }
    const controller = new AbortController();
    const loadId = releaseLoadIdRef.current + 1;
    releaseLoadIdRef.current = loadId;

    setState(createReleaseInfoPendingState);

    async function loadReleaseInfo() {
      try {
        const releaseInfo = await fetchJson<ReleaseInfoPayload>(
          `/api/admin/release-info${makeQuery({
            _dashboard: "1",
            _dashboard_refresh: refreshNonce > 0 ? refreshNonce : null,
            news_index: newsIndex,
          })}`,
          { signal: controller.signal },
        );
        if (controller.signal.aborted || releaseLoadIdRef.current !== loadId) return;
        if (releaseInfo.news) {
          setNewsIndex(releaseInfo.news.index);
        }
        setState((current) => ({
          ...current,
          releaseInfo: {
            loading: false,
            error: null,
            missing: null,
            data: releaseInfo,
            updatedAt: new Date().toISOString(),
          },
        }));
      } catch (reason) {
        if (controller.signal.aborted || releaseLoadIdRef.current !== loadId) return;
        const failure = classifyFailure(reason);
        setState((current) => ({
          ...current,
          releaseInfo: {
            loading: false,
            error: failure.error,
            missing: failure.error ? null : failure.missing ?? "Release metadata is not available.",
            data: null,
            updatedAt: new Date().toISOString(),
          },
        }));
      }
    }

    void loadReleaseInfo();
    return () => {
      controller.abort();
    };
  }, [documentVisible, refreshNonce]);

  async function loadNewsEntry(targetIndex: number) {
    if (targetIndex < 0) {
      return;
    }
    const loadId = releaseLoadIdRef.current + 1;
    releaseLoadIdRef.current = loadId;
    setNewsIndex(targetIndex);
    setState(createReleaseInfoPendingState);

    try {
      const releaseInfo = await fetchJson<ReleaseInfoPayload>(
        `/api/admin/release-info${makeQuery({
          _dashboard: "1",
          _dashboard_refresh: refreshNonce > 0 ? refreshNonce : null,
          news_index: targetIndex,
        })}`,
      );
      if (releaseLoadIdRef.current !== loadId) return;
      setNewsIndex(releaseInfo.news?.index ?? targetIndex);
      setState((current) => ({
        ...current,
        releaseInfo: {
          loading: false,
          error: null,
          missing: null,
          data: releaseInfo,
          updatedAt: new Date().toISOString(),
        },
      }));
    } catch (reason) {
      if (releaseLoadIdRef.current !== loadId) return;
      const failure = classifyFailure(reason);
      setState((current) => ({
        ...current,
        releaseInfo: {
          loading: false,
          error: failure.error,
          missing: failure.error ? null : failure.missing ?? "Release metadata is not available.",
          data: null,
          updatedAt: new Date().toISOString(),
        },
      }));
    }
  }

  const adminState = state.adminState.data;
  const deploymentStatus = state.deploymentStatus.data;
  const workers = state.workers.data?.workers ?? [];
  const agents = state.agents.data?.items ?? [];
  const runSnapshot = state.workflowRuns.data;
  const activityReport = state.workflowActivity.data;
  const approvals = state.approvals.data;
  const sessions = state.sessions.data;
  const toolCalls = state.toolCalls.data;
  const capacityHealth = state.capacityHealth.data;
  const releaseInfo = state.releaseInfo.data;
  const workflowDocument = state.workflowDocument.data;
  const workflowCatalog = state.workflowCatalog.data;
  const anyLoading =
    state.workers.loading ||
    state.agents.loading ||
    state.workflowRuns.loading ||
    state.workflowActivity.loading ||
    state.approvals.loading ||
    state.sessions.loading ||
    state.toolCalls.loading ||
    state.capacityHealth.loading ||
    state.releaseInfo.loading ||
    state.workflowDocument.loading ||
    state.workflowCatalog.loading;
  const systemHealthStates = [state.capacityHealth, state.agents, state.workers];

  useEffect(() => {
    if (anyLoading) return;
    setState((current) =>
      current.lastRefreshedAt
        ? current
        : { ...current, lastRefreshedAt: new Date().toISOString() },
    );
  }, [anyLoading]);

  function openDashboardAgent(agent: AgentRecord) {
    setSelectedDirectoryPath(agent.directory_path || "root");
    openTab("agents", {
      agent_id: agent.agent_id,
      session_id: agent.session_id,
    });
  }

  return (
    <div className="feature-page dashboard-page">
      <div className="admin-toolbar">
        <div className="dashboard-toolbar-copy">
          <span className="section-label">Operations dashboard</span>
          <strong>{selectedDirectoryPath}</strong>
          <span className="dashboard-toolbar-summary">
            {state.lastRefreshedAt ? `Last refreshed ${formatTimestamp(state.lastRefreshedAt)}` : "Refreshing live control-plane data."}
          </span>
        </div>
        <div className="status-pills">
          <span className={`status-pill ${stateClassName(anyLoading ? "running" : "pass")}`}>{anyLoading ? "Refreshing" : "Live"}</span>
          <span className={`status-pill ${stateClassName(statusByCountClass(state.approvals.data?.health.pending ?? 0))}`}>{state.approvals.data ? `${state.approvals.data.health.pending} approvals pending` : "Approvals pending"}</span>
          <span className={`status-pill ${stateClassName(statusByCountClass(state.sessions.data?.counts.active ?? 0))}`}>{state.sessions.data ? `${state.sessions.data.counts.active} sessions active` : "Sessions"}</span>
        </div>
        <div className="inline-actions">
          <button
            type="button"
            className="admin-shell-button dashboard-icon-button"
            aria-label="Refresh dashboard"
            title="Refresh dashboard"
            onClick={() => setRefreshNonce((current) => current + 1)}
          >
            <DashboardIcon name="refresh" />
          </button>
          <TabButton tab="workflow" label="Open workflow" />
          <TabButton tab="approvals" label="Open approvals" />
          <TabButton tab="sessions" label="Open sessions" />
          <TabButton tab="tool-calls" label="Open tool calls" />
          <TabButton tab="configuration" label="Open config" />
        </div>
      </div>

      <div className="dashboard-panel-grid">
        <div className="dashboard-panel-column dashboard-panel-column-primary">
          <ProductContextPanel releaseInfo={releaseInfo} releaseInfoState={state.releaseInfo} onLoadNewsEntry={loadNewsEntry} />
        </div>

        <div className="dashboard-panel-column dashboard-panel-column-secondary">
          <DashboardPanel
            eyebrow="System health"
            title="Capacity and execution"
            statusClassName={stateClassName(combinedStatusTone(systemHealthStates))}
            statusLabel={combinedStatusLabel(systemHealthStates)}
          >
            <DashboardBlock
              title="Admin state"
              state={state.adminState}
              missingFallback="The admin state endpoint is unavailable."
              summary={
                adminState
                  ? `${adminState.selected_directory_path} · ${adminState.execution_backend} · ${adminState.directory_scoped_config_enabled ? "scoped" : "shared"}`
                  : null
              }
              actions={<TabButton tab="configuration" label="Configuration" />}
            >
              {adminState ? (
                <DashboardMetricRows
                  items={[
                    {
                      label: "Directory",
                      value: adminState.selected_directory_path,
                    },
                    {
                      label: "Execution backend",
                      value: adminState.execution_backend,
                    },
                    {
                      label: "Directory-scoped config",
                      value: adminState.directory_scoped_config_enabled ? "Enabled" : "Disabled",
                    },
                    {
                      label: "Privileges",
                      value: privilegedUnlocked ? "Unlocked" : "Locked",
                    },
                  ]}
                />
              ) : null}
            </DashboardBlock>

            <DashboardBlock
              title="Deployment"
              state={state.deploymentStatus}
              missingFallback="Deployment status is not available."
              summary={
                deploymentStatus
                  ? deploymentStatus.active
                    ? `${deploymentStatus.stale ? "Active, stale" : "Active"} · ${deploymentStatus.message ?? "No message"}`
                    : "Idle"
                  : null
              }
              actions={<TabButton tab="workers" label="Workers" />}
            >
              {deploymentStatus ? (
                <DashboardMetricRows
                  items={[
                    {
                      label: "Status",
                      value: deploymentStatus.active ? (deploymentStatus.stale ? "Active, stale" : "Active") : "Idle",
                    },
                    {
                      label: "Message",
                      value: deploymentStatus.message ?? "No deployment message.",
                    },
                    {
                      label: "Updated",
                      value: formatTimestamp(deploymentStatus.updated_at ?? deploymentStatus.started_at),
                    },
                    {
                      label: "TTL",
                      value: detailValue(deploymentStatus.ttl_seconds),
                    },
                  ]}
                />
              ) : null}
            </DashboardBlock>

            <DashboardBlock
              title="Capacity"
              state={state.capacityHealth}
              missingFallback="Capacity health is not available."
              summary={capacityHealth ? capacitySummary(capacityHealth) : null}
            >
              {capacityHealth ? (
                <div className="dashboard-block-stack">
                  <div className="dashboard-disk-grid">
                    <DashboardDiskDial
                      label="Control plane"
                      disk={capacityHealth.control_plane.disk}
                      detail={`Known storage ${formatBytes(capacityHealth.control_plane.storage.total.size_bytes)}.`}
                    />
                    {capacityHealth.workers.entries.length > 0 ? (
                      capacityHealth.workers.entries.map((entry) => (
                        <DashboardDiskDial
                          key={entry.worker_url}
                          label={workerHostLabel(entry.worker_url)}
                          disk={entry.telemetry?.disk?.worker_root ?? null}
                          detail={panelTitleList([
                            entry.reachable ? "Reachable" : "Unreachable",
                            entry.directory_paths.join(", ") || "No directory scope",
                          ])}
                        />
                      ))
                    ) : (
                      <div className="dashboard-disk-empty">No worker host disk telemetry.</div>
                    )}
                  </div>
                  <DashboardCompactRows>
                    <DashboardCompactRow
                      title="Disk / storage"
                      meta={`${formatPercent(capacityHealth.control_plane.disk.used_percent)} used (${formatBytes(capacityHealth.control_plane.disk.used_bytes)} / ${formatBytes(capacityHealth.control_plane.disk.total_bytes)})`}
                      detail={`Free ${formatPercent(capacityHealth.control_plane.disk.free_percent)} (${formatBytes(capacityHealth.control_plane.disk.free_bytes)}) · Known storage ${formatBytes(capacityHealth.control_plane.storage.total.size_bytes)}`}
                    />
                    <DashboardCompactRow
                      title="Sessions / storage"
                      meta={`${capacityHealth.sessions.total_sessions} sessions, ${capacityHealth.sessions.total_agents} agents`}
                      detail={`${formatBytes(capacityHealth.sessions.storage.measured_total.size_bytes)} across ${capacityHealth.sessions.storage.measured_session_count} measured · Largest session ${sessionStorageSampleSummary(capacityHealth.sessions.storage.largest_sessions[0])}`}
                    />
                    <DashboardCompactRow
                      title="Workers / disk"
                      meta={`${capacityHealth.workers.reachable_workers}/${capacityHealth.workers.total_workers} reachable, ${capacityHealth.workers.telemetry_available_workers} telemetry`}
                      detail={workerDiskSummary(capacityHealth.workers)}
                    />
                    <DashboardCompactRow
                      title="Queue"
                      meta={`${capacityHealth.queue.pending_count} pending, ${capacityHealth.queue.inflight_count} in-flight`}
                      detail={`Oldest queue ${capacityHealth.queue.oldest_age_seconds == null ? "n/a" : formatRelativeSeconds(capacityHealth.queue.oldest_age_seconds)}`}
                    />
                    <DashboardCompactRow
                      title="Prompts"
                      meta={`${capacityHealth.prompts.unread_count} unread`}
                      detail={`Queue health ${capacityHealth.queue.health} · Prompt health ${capacityHealth.prompts.health}`}
                    />
                  </DashboardCompactRows>
                  <DashboardMetricChips
                    items={[
                      {
                        label: "Disk",
                        value: capacityHealth.control_plane.disk.health,
                        tone: healthStateTone(capacityHealth.control_plane.disk.health),
                      },
                      {
                        label: "Storage",
                        value: capacityHealth.control_plane.storage.total.health,
                        tone: healthStateTone(capacityHealth.control_plane.storage.total.health),
                      },
                      {
                        label: "Workers",
                        value: `${capacityHealth.workers.telemetry_available_workers}/${capacityHealth.workers.total_workers} telemetry`,
                        tone: capacityWorkerTone(capacityHealth.workers),
                      },
                      {
                        label: "Worker disk",
                        value: workerDiskHealth(capacityHealth.workers),
                        tone: healthStateTone(workerDiskHealth(capacityHealth.workers)),
                      },
                      {
                        label: "Queue",
                        value: capacityHealth.queue.health,
                        tone: healthStateTone(capacityHealth.queue.health),
                      },
                      {
                        label: "Prompts",
                        value: capacityHealth.prompts.health,
                        tone: healthStateTone(capacityHealth.prompts.health),
                      },
                    ]}
                  />
                </div>
              ) : null}
            </DashboardBlock>

            <DashboardBlock
              title="Agent inventory"
              state={state.agents}
              missingFallback="No agents are available for the selected directory and its descendants."
              summary={agentSummary(agents)}
              actions={<TabButton tab="agents" label="Agents" />}
            >
              {agents.length > 0 ? <DashboardAgentInventory agents={agents} onOpenAgent={openDashboardAgent} /> : null}
            </DashboardBlock>

            <DashboardBlock
              title="Workers"
              state={state.workers}
              missingFallback="No worker summary is available."
              summary={workerSummary(workers)}
              actions={<TabButton tab="workers" label="Workers" />}
            >
              {workers.length > 0 ? (
                <div className="dashboard-block-stack">
                  <DashboardMetricRows
                    items={[
                      {
                        label: "Total",
                        value: workers.length,
                      },
                      {
                        label: "Running",
                        value: countBy(workers, (worker) => worker.running),
                      },
                      {
                        label: "Failed",
                        value: countBy(workers, (worker) => worker.failed),
                      },
                    ]}
                  />
                  <DashboardCompactRows>
                    {workers.map((worker) => {
                      const metadata = worker.description ?? worker.unit;
                      const detailParts = [worker.unit, worker.main_pid == null ? null : `PID ${worker.main_pid}`, worker.detail].filter(
                        (part): part is string => Boolean(part),
                      );
                      return (
                        <DashboardCompactRow
                          key={worker.worker_id}
                          title={worker.name}
                          meta={metadata}
                          detail={detailParts.length > 0 ? detailParts.join(" · ") : null}
                          statusText={worker.active_state}
                          statusTone={workerStatusTone(worker)}
                        />
                      );
                    })}
                  </DashboardCompactRows>
                </div>
              ) : null}
            </DashboardBlock>
        </DashboardPanel>

        <DashboardPanel
          eyebrow="Agent inventory"
          title="Live execution targets"
          statusClassName={stateClassName(statusTone(state.agents))}
          statusLabel={statusLabel(state.agents)}
        >
            <DashboardBlock
              title="Agents"
              state={state.agents}
              missingFallback="No agents are available for the selected directory."
              summary={agentSummary(agents)}
              actions={<TabButton tab="agents" label="Agents" />}
            >
              {agents.length > 0 ? (
                <div className="dashboard-block-stack">
                  <DashboardMetricRows
                    items={[
                      { label: "Total", value: agents.length },
                      { label: "Active", value: countBy(agents, (agent) => hasActiveAgentStatus(agent.status)) },
                      { label: "Inactive", value: countBy(agents, (agent) => !hasActiveAgentStatus(agent.status)) },
                      {
                        label: "Backends",
                        value: [
                          `${countBy(agents, (agent) => agent.backend === "libvirt_vm")} VM`,
                          `${countBy(agents, (agent) => agent.backend === "container_worker_vm")} container`,
                        ].join(", "),
                      },
                    ]}
                  />
                  <DashboardCompactRows>
                    {agents.slice(0, 4).map((agent) => (
                      <DashboardCompactRow
                        key={agent.agent_id}
                        title={agent.target_name}
                        meta={`${agent.agent_id} · ${agent.session_id ?? "no session"} · ${agent.directory_path}`}
                        detail={agent.backend}
                        statusText={agent.status}
                        statusTone={agentStatusTone(agent.status)}
                      />
                    ))}
                  </DashboardCompactRows>
                </div>
              ) : null}
            </DashboardBlock>
        </DashboardPanel>

        <DashboardPanel
          eyebrow="Automation activity"
          title="Workflow runtime"
          statusClassName={stateClassName(statusTone(state.workflowRuns))}
          statusLabel={statusLabel(state.workflowRuns)}
        >
            <DashboardBlock
              title="Workflow runs"
              state={state.workflowRuns}
              missingFallback="No workflow runs are currently available."
              summary={runSnapshot ? runsSummary(runSnapshot) : null}
              actions={<TabButton tab="workflow" label="Workflow" />}
            >
              {runSnapshot ? (
                <div className="dashboard-block-stack">
                  <DashboardMetricRows
                    items={[
                      { label: "Total", value: runSnapshot.runs.length },
                      { label: "Active", value: countBy(runSnapshot.runs, (run) => runStatusTone(run.run_state) === "running") },
                      { label: "Failed", value: countBy(runSnapshot.runs, (run) => runStatusTone(run.run_state) === "fail") },
                      { label: "Resumable", value: countBy(runSnapshot.runs, (run) => Boolean(run.resumable)) },
                    ]}
                  />
                  <DashboardCompactRows>
                    {runSnapshot.runs.slice(0, 4).map((run) => (
                      <DashboardCompactRow
                        key={run.event_id}
                        title={run.event_id}
                        meta={`${run.trace_id ?? "no trace"} · ${formatTimestamp(run.latest_activity_at ?? run.occurred_at)}`}
                        detail={panelTitleList([run.source_node_id ?? "source missing", run.source_trigger_type ?? "trigger missing"])}
                        links={
                          run.trace_id
                            ? [{ tab: "workflow", label: "Trace", value: run.trace_id, params: { trace_id: run.trace_id } }]
                            : []
                        }
                        statusText={run.run_state}
                        statusTone={runStatusTone(run.run_state)}
                      />
                    ))}
                  </DashboardCompactRows>
                </div>
              ) : null}
            </DashboardBlock>

            <DashboardBlock
              title="Graph activity"
              state={state.workflowActivity}
              missingFallback="No workflow graph activity has been recorded."
              summary={activityReport ? graphActivitySummary(activityReport) : null}
              actions={<TabButton tab="reports" label="Reports" />}
            >
              {activityReport ? (
                <div className="dashboard-block-stack">
                  <DashboardMetricRows
                    items={[
                      { label: "Graphs", value: activityReport.graphs.length },
                      { label: "Activities", value: activityReport.graphs.reduce((sum, graph) => sum + graph.activity_count, 0) },
                      {
                        label: "Latest",
                        value: activityReport.graphs.length === 0 ? "n/a" : formatTimestamp(activityReport.graphs[0]?.latest_activity_at ?? activityReport.generated_at),
                      },
                    ]}
                  />
                  <DashboardCompactRows>
                    {activityReport.graphs.slice(0, 3).map((graph) => (
                      <DashboardCompactRow
                        key={graph.graph_id}
                        title={graph.name}
                        meta={`${graph.activity_count} activities · ${graph.run_count} runs · ${graph.node_count} nodes`}
                        detail={formatTimestamp(graph.latest_activity_at ?? activityReport.generated_at)}
                        links={[
                          { tab: "reports", label: "Graph", value: graph.graph_id },
                          ...(graph.node_ids[0] ? [{ tab: "workflow" as AdminTab, label: "Node", value: graph.node_ids[0] }] : []),
                        ]}
                        statusText={String(graph.activity_count)}
                        statusTone={statusByCountClass(graph.activity_count)}
                      />
                    ))}
                  </DashboardCompactRows>
                </div>
              ) : null}
            </DashboardBlock>
        </DashboardPanel>

        <DashboardPanel
          eyebrow="Human-control work"
          title="Approvals and sessions"
          statusClassName={stateClassName(approvalTone(approvals))}
          statusLabel={approvals ? `${approvals.health.pending} pending` : statusLabel(state.approvals)}
        >
            <DashboardBlock
              title="Approvals"
              state={state.approvals}
              missingFallback="No approval requests are waiting for review."
              summary={approvals ? approvalsSummary(approvals) : null}
              actions={<TabButton tab="approvals" label="Approvals" />}
            >
              {approvals ? (
                <div className="dashboard-block-stack">
                  <DashboardMetricRows
                    items={[
                      { label: "Pending", value: approvals.health.pending },
                      { label: "Expired", value: approvals.health.expired },
                      {
                        label: "Oldest pending",
                        value: approvals.health.oldest_pending_age_seconds == null ? "n/a" : formatRelativeSeconds(approvals.health.oldest_pending_age_seconds),
                      },
                    ]}
                  />
                  <DashboardCompactRows>
                    {approvals.items.slice(0, 4).map((item) => (
                      <DashboardCompactRow
                        key={item.request_id}
                        title={item.tool_name || item.action_type}
                        meta={`${item.session_id} · ${formatTimestamp(item.created_at)}`}
                        detail={panelTitleList([item.human_summary ?? "No human summary", item.directory_path ?? "no directory"])}
                        links={[
                          { tab: "sessions", label: "Session", value: item.session_id, params: { session_id: item.session_id } },
                          { tab: "approvals", label: "Approval", value: item.request_id, params: { request_id: item.request_id } },
                        ]}
                        statusText={item.status}
                        statusTone={approvalItemTone(item.status)}
                      />
                    ))}
                  </DashboardCompactRows>
                </div>
              ) : null}
            </DashboardBlock>

            <DashboardBlock
              title="Sessions"
              state={state.sessions}
              missingFallback="No sessions are active or expired in this directory scope."
              summary={sessions ? sessionsSummary(sessions) : null}
              actions={<TabButton tab="sessions" label="Sessions" />}
            >
              {sessions ? (
                <div className="dashboard-block-stack">
                  <DashboardMetricRows
                    items={[
                      { label: "Active", value: sessions.counts.active },
                      { label: "Expired", value: sessions.counts.expired },
                      { label: "Total", value: sessions.counts.total },
                      {
                        label: "Wake cue",
                        value:
                          sessionItemsForStatus(sessions, "active").map(sessionWakeCue).find(Boolean) ??
                          sessionItemsForStatus(sessions, "expired").map(sessionWakeCue).find(Boolean) ??
                          "No wake-schedule cue exposed by the list payload.",
                      },
                    ]}
                  />
                  <DashboardCompactRows>
                    {sessionItemsForStatus(sessions, "active").slice(0, 3).map((session) => (
                      <DashboardCompactRow
                        key={session.session_id}
                        title={session.session_id}
                        meta={panelTitleList([session.model ?? "no model", session.vm_id ?? "no VM", session.directory_path])}
                        detail={sessionWakeCue(session) ?? "No wake-schedule cue exposed by the list payload."}
                        links={[
                          ...(session.issue_number ? [{ tab: "issues" as AdminTab, label: "Issue", value: session.issue_number }] : []),
                          { tab: "sessions", label: "Session", value: session.session_id, params: { session_id: session.session_id } },
                        ]}
                        statusText={session.is_expired ? "Expired" : "Active"}
                        statusTone={sessionItemTone(session)}
                      />
                    ))}
                  </DashboardCompactRows>
                </div>
              ) : null}
            </DashboardBlock>
        </DashboardPanel>

        <DashboardPanel
          eyebrow="Explainability shortcuts"
          title="Tool-call audit trail"
          statusClassName={stateClassName(statusTone(state.toolCalls))}
          statusLabel={statusLabel(state.toolCalls)}
        >
            <DashboardBlock
              title="Tool calls"
              state={state.toolCalls}
              missingFallback="No tool-call audit entries were returned."
              summary={toolCalls ? toolCallsSummary(toolCalls) : null}
              actions={<TabButton tab="tool-calls" label="Tool calls" />}
            >
              {toolCalls ? (
                <div className="dashboard-block-stack">
                  <DashboardMetricRows
                    items={[
                      { label: "Returned", value: toolCalls.counts?.returned ?? toolCalls.items.length },
                      { label: "Success", value: toolCalls.counts?.status_counts?.success ?? toolCalls.counts?.success ?? 0 },
                      { label: "Denied", value: toolCalls.counts?.status_counts?.denied ?? toolCalls.counts?.denied ?? 0 },
                      { label: "Error", value: toolCalls.counts?.status_counts?.error ?? toolCalls.counts?.error ?? 0 },
                    ]}
                  />
                  <DashboardCompactRows>
                    {toolCalls.items.slice(0, 5).map((item) => (
                      <DashboardCompactRow
                        key={`${item.tool_name}-${item.created_at}-${item.request_id ?? item.correlation_id ?? item.trace_id ?? item.session_id ?? "call"}`}
                        title={item.tool_name}
                        meta={`${item.session_id ?? "no session"} · ${formatTimestamp(item.created_at)}`}
                        detail={panelTitleList([
                          item.correlation_id ?? "Correlation missing",
                          item.error_summary ?? item.error ?? "No error summary",
                        ])}
                        links={[
                          ...(item.session_id
                            ? [{ tab: "sessions" as AdminTab, label: "Session", value: item.session_id, params: { session_id: item.session_id } }]
                            : []),
                          ...(item.approval_request_id
                            ? [
                                {
                                  tab: "approvals" as AdminTab,
                                  label: "Approval",
                                  value: item.approval_request_id,
                                  params: { request_id: item.approval_request_id },
                                },
                              ]
                            : []),
                          ...(item.workflow_run_id ?? item.trace_id
                            ? [
                                {
                                  tab: "workflow" as AdminTab,
                                  label: item.workflow_run_id ? "Run" : "Trace",
                                  value: item.workflow_run_id ?? item.trace_id ?? "",
                                  params: item.workflow_run_id ? { run_id: item.workflow_run_id } : { trace_id: item.trace_id ?? "" },
                                },
                              ]
                            : []),
                          ...(item.agent_id ? [{ tab: "agents" as AdminTab, label: "Agent", value: item.agent_id }] : []),
                          ...(item.vm_id ? [{ tab: "vms" as AdminTab, label: "VM", value: item.vm_id }] : []),
                        ]}
                        statusText={toolCallStatus(item)}
                        statusTone={toolCallTone(item)}
                      />
                    ))}
                  </DashboardCompactRows>
                </div>
              ) : null}
            </DashboardBlock>
        </DashboardPanel>

        <DashboardPanel
          eyebrow="Onboarding / workflow actions"
          title="Workflow document and catalog"
          statusClassName={stateClassName(workflowDocumentTone(workflowDocument))}
          statusLabel={workflowDocument ? `${workflowDocument.graphs.length} graphs` : statusLabel(state.workflowDocument)}
        >
            <DashboardBlock
              title="Workflow document"
              state={state.workflowDocument}
              missingFallback="No workflow document has been created yet."
              summary={workflowDocument ? workflowDocumentSummary(workflowDocument) : null}
              actions={
                <>
                  <TabButton tab="workflow" label="Workflow" />
                  <TabButton tab="configuration" label="Configuration" />
                </>
              }
              >
              {workflowDocument ? (
                <div className="dashboard-block-stack">
                  <DashboardMetricRows
                    items={[
                      { label: "Config", value: workflowDocument.config_exists ? "Present" : "Missing" },
                      { label: "Layout", value: workflowDocument.layout_exists ? "Present" : "Missing" },
                      { label: "Graphs", value: workflowDocument.graphs.length },
                      { label: "Revision", value: workflowDocument.revision },
                    ]}
                  />
                  <DashboardMetricChips
                    items={[
                      {
                        label: "Config",
                        value: workflowDocument.config_exists ? "available" : "missing",
                        tone: statusByCountClass(workflowDocument.config_exists ? 1 : 0),
                      },
                      {
                        label: "Layout",
                        value: workflowDocument.layout_exists ? "available" : "missing",
                        tone: statusByCountClass(workflowDocument.layout_exists ? 1 : 0),
                      },
                    ]}
                  />
                </div>
              ) : null}
            </DashboardBlock>

            <DashboardBlock
              title="Catalog"
              state={state.workflowCatalog}
              missingFallback="No workflow catalog nodes are available for this directory."
              summary={workflowCatalog ? workflowCatalogSummary(workflowCatalog) : null}
              actions={
                <>
                  <TabButton tab="workflow" label="Open workflow" />
                  <TabButton tab="configuration" label="Config" />
                </>
              }
              >
              {workflowCatalog ? (
                <div className="dashboard-block-stack">
                  <DashboardMetricRows
                    items={[
                      { label: "Worker", value: workflowCatalog.worker_id },
                      { label: "Nodes", value: workflowCatalog.nodes.length },
                      {
                        label: "Kinds",
                        value: `${countBy(workflowCatalog.nodes, (node) => node.kind === "trigger")} triggers, ${countBy(
                          workflowCatalog.nodes,
                          (node) => node.kind === "gate",
                        )} gates, ${countBy(workflowCatalog.nodes, (node) => node.kind === "effect")} effects`,
                      },
                    ]}
                  />
                  <DashboardCompactRows>
                    {workflowCatalog.nodes.slice(0, 4).map((node) => (
                      <DashboardCompactRow
                        key={`${node.kind}:${node.type}`}
                        title={node.label}
                        meta={node.type}
                        detail={panelTitleList([
                          `${node.fields.length} field${node.fields.length === 1 ? "" : "s"}`,
                          `${node.input_ports.length} inputs`,
                          `${node.output_ports.length} outputs`,
                        ])}
                        statusText={node.kind}
                        statusTone={statusByCountClass(node.fields.length)}
                      />
                    ))}
                  </DashboardCompactRows>
                </div>
              ) : null}
            </DashboardBlock>
        </DashboardPanel>
        </div>
      </div>
    </div>
  );
}

export default OperationsDashboard;

import { useEffect, useState, type ReactNode } from "react";

import { ApiError, fetchJson, formatRelativeSeconds, formatTimestamp } from "../admin/api";
import { useAdminUi } from "../admin/AdminContext";
import { DownloadIcon } from "../admin/PageActionIcons";
import type { AgentBackend } from "../admin/types";
import { adminHash, type AdminHashParamValue, type AdminTab } from "../../adminRouting";
import type { WorkflowEditorCatalog, WorkflowEditorDocument, WorkflowRunSnapshot } from "../../types";

type DashboardAdminState = {
  selected_directory_path: string;
  directory_scoped_config_enabled: boolean;
  execution_backend: AgentBackend;
  role: "admin" | "regular" | null;
  can_manage_users: boolean;
  privileged_operations: {
    unlocked: boolean;
  };
  assistant?: {
    enabled: boolean;
    show_launcher_when_disabled: boolean;
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
  allowed_tools: string[];
  approval_required_tools?: string[];
  allowed_script_names: string[];
  discord_channel_allowlist: string[];
  teams_channel_allowlist: string[];
  communication_file_receive_enabled: boolean;
  communication_file_send_enabled: boolean;
  communication_file_max_bytes: number | null;
  communication_file_allowed_mime_types: string[];
  communication_file_allowed_extensions: string[];
  communication_file_blocked_extensions: string[];
  communication_file_require_approval_for_send: boolean;
  expires_at: string;
  created_at: string;
  metadata: Record<string, unknown>;
  issue_number?: string | null;
  is_expired: boolean;
};

type SessionListResponse = {
  items: SessionRecord[];
  active_items: SessionRecord[];
  expired_items: SessionRecord[];
  counts: {
    total: number;
    active: number;
    expired: number;
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

type ReleaseNews = {
  source: string | null;
  path: string | null;
  index: number;
  heading: string | null;
  paragraphs: string[];
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

type DashboardPillTone = "pass" | "fail" | "missing" | "running" | "idle";

function createRemotePendingState<T>(): RemoteState<T> {
  return {
    loading: true,
    error: null,
    missing: null,
    data: null,
    updatedAt: null,
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
  if (normalized === "failed" || normalized === "error" || normalized === "stopped" || normalized === "inactive") {
    return "fail";
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
  return (
    <section className="dashboard-block">
      <div className="dashboard-block-header">
        <div className="dashboard-block-copy">
          <div className="dashboard-block-title-row">
            <div className="eyebrow">{title}</div>
            <span className={`status-pill ${stateClassName(tone)}`}>{statusLabel(state)}</span>
          </div>
          <p style={{ margin: "0.2rem 0 0", color: "inherit" }}>{remoteMessage(state, missingFallback, summary)}</p>
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

type DashboardIconName = AdminTab | "refresh" | "newer" | "older";

function DashboardIcon({ name }: { name: DashboardIconName }) {
  const icons: Record<string, ReactNode> = {
    refresh: (
      <>
        <path d="M20 6v5h-5" />
        <path d="M4 18v-5h5" />
        <path d="M6.8 9.2A7 7 0 0 1 18.5 8L20 11" />
        <path d="M17.2 14.8A7 7 0 0 1 5.5 16L4 13" />
      </>
    ),
    workflow: (
      <>
        <rect x="4" y="5" width="5" height="5" rx="1.2" />
        <rect x="15" y="5" width="5" height="5" rx="1.2" />
        <rect x="9.5" y="15" width="5" height="5" rx="1.2" />
        <path d="M9 7.5h6" />
        <path d="M12 10v5" />
      </>
    ),
    approvals: (
      <>
        <path d="M5 12.5l4 4L19 6.5" />
        <path d="M5.5 5.5h9" />
        <path d="M5.5 10h5" />
        <path d="M5.5 18.5h13" />
      </>
    ),
    sessions: (
      <>
        <circle cx="12" cy="8" r="3.5" />
        <path d="M5.5 19a6.8 6.8 0 0 1 13 0" />
        <path d="M17 6.5h3.5" />
        <path d="M18.75 4.75v3.5" />
      </>
    ),
    "tool-calls": (
      <>
        <path d="M7 8.5L3.5 12 7 15.5" />
        <path d="M17 8.5l3.5 3.5L17 15.5" />
        <path d="M14 5l-4 14" />
      </>
    ),
    configuration: (
      <>
        <circle cx="12" cy="12" r="3" />
        <path d="M12 3.8v2.1" />
        <path d="M12 18.1v2.1" />
        <path d="M4.9 7.3l1.8 1" />
        <path d="M17.3 15.7l1.8 1" />
        <path d="M4.9 16.7l1.8-1" />
        <path d="M17.3 8.3l1.8-1" />
      </>
    ),
    workers: (
      <>
        <rect x="4.5" y="6" width="15" height="11" rx="2" />
        <path d="M8 10h3.5" />
        <path d="M8 13.5h8" />
        <path d="M6.5 20h11" />
      </>
    ),
    agents: (
      <>
        <rect x="5" y="6" width="14" height="10" rx="2" />
        <path d="M9 20h6" />
        <path d="M12 16v4" />
        <path d="M9 10.5h.01" />
        <path d="M15 10.5h.01" />
      </>
    ),
    reports: (
      <>
        <path d="M5 19V5" />
        <path d="M5 19h14" />
        <path d="M8.5 15v-3" />
        <path d="M12 15V8" />
        <path d="M15.5 15v-5" />
      </>
    ),
    newer: <path d="M14.5 6.5L9 12l5.5 5.5" />,
    older: <path d="M9.5 6.5L15 12l-5.5 5.5" />,
  };

  return (
    <svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">
      {icons[name] ?? icons.workflow}
    </svg>
  );
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
  if (releaseInfo.news?.heading) {
    parts.push(`News: ${releaseInfo.news.heading}`);
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
  return (
    <DashboardPanel
      eyebrow="Product context"
      title="Version and changelog"
      className="dashboard-release-panel"
      defaultOpen
      statusClassName={stateClassName(releaseInfoTone(releaseInfoState))}
      statusLabel={releaseInfoStatusLabel(releaseInfoState)}
    >
      {releaseInfo?.news ? (
        <DashboardBlock
          title="News"
          state={releaseInfoState}
          missingFallback="News is not available."
          summary={releaseInfo.news.heading ?? "Latest update"}
        >
          <div className="release-news-card">
            <div className="release-news-header">
              <span>News</span>
              <strong>{releaseInfo.news.heading ?? "Latest update"}</strong>
            </div>
            <div className="release-news-copy">
              {releaseInfo.news.paragraphs.map((paragraph, index) => (
                <p key={`${releaseInfo.news?.heading ?? "news"}-${index}`}>{paragraph}</p>
              ))}
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
          </div>
        </DashboardBlock>
      ) : null}

      <DashboardBlock
        title="Release metadata"
        state={releaseInfoState}
        missingFallback="Release metadata is not available."
        summary={releaseInfoSummary(releaseInfo)}
        actions={
          releaseInfo?.changelog ? (
            <div className="feature-toolbar-actions icon-actions dashboard-release-actions">
              <button type="button" aria-label="Download changelog" title="Download changelog" onClick={downloadChangelog}>
                <DownloadIcon />
              </button>
            </div>
          ) : null
        }
      >
        {releaseInfo ? (
          <div style={{ display: "grid", gap: "0.85rem" }}>
            <p className="panel-meta dashboard-release-summary" style={{ margin: 0 }}>
              {releaseInfoSummary(releaseInfo)}
            </p>
            <div className="detail-list dashboard-release-details">
              <div>
                <span>Version</span>
                <strong>{releaseInfo.version ?? "Version unavailable"}</strong>
              </div>
              <div>
                <span>Version source</span>
                <strong>{releaseInfo.version_source ?? "No version source."}</strong>
              </div>
              <div>
                <span>Build</span>
                <strong>{releaseInfo.build ?? "Build unavailable"}</strong>
              </div>
              <div>
                <span>Build source</span>
                <strong>{releaseInfo.build_source ?? "No build source."}</strong>
              </div>
              <div>
                <span>Changelog</span>
                <strong>{releaseInfo.changelog ? releaseInfo.changelog.heading ?? "Recent release entries" : "Changelog unavailable"}</strong>
              </div>
              <div>
                <span>Changelog source</span>
                <strong>
                  {releaseInfo.changelog
                    ? [releaseInfo.changelog.source ?? "CHANGELOG.md", releaseInfo.changelog.path ?? null].filter(Boolean).join(" · ")
                    : "No changelog source."}
                </strong>
              </div>
            </div>
            {releaseInfo.missing_reasons.length > 0 ? (
              <div className="release-missing-list">
                <span>Missing details</span>
                <strong>{releaseInfo.missing_reasons.join(" · ")}</strong>
              </div>
            ) : null}
          </div>
        ) : null}
      </DashboardBlock>
    </DashboardPanel>
  );
}

export function OperationsDashboard() {
  const { selectedDirectoryPath } = useAdminUi();
  const [refreshNonce, setRefreshNonce] = useState(0);
  const [newsIndex, setNewsIndex] = useState(0);
  const [state, setState] = useState<DashboardState>(() => createDashboardPendingState());

  useEffect(() => {
    const controller = new AbortController();
    let cancelled = false;

    setState(createDashboardPendingState());

    async function load() {
      const directoryQuery = makeQuery({ directory_path: selectedDirectoryPath });
      const dashboardCacheParams = {
        _dashboard: "1",
        _dashboard_refresh: refreshNonce > 0 ? refreshNonce : null,
      };
      const [
        adminStateResult,
        deploymentStatusResult,
        workersResult,
        agentsResult,
        workflowRunsResult,
        workflowActivityResult,
        approvalsResult,
        sessionsResult,
        toolCallsResult,
        capacityHealthResult,
        releaseInfoResult,
        workflowDocumentResult,
        workflowCatalogResult,
      ] =
        await Promise.allSettled([
          fetchJson<DashboardAdminState>(`/api/admin/state${directoryQuery}`, { credentials: "same-origin", signal: controller.signal }),
          fetchJson<DeploymentStatus>(`/api/admin/deployment-status${makeQuery(dashboardCacheParams)}`, { signal: controller.signal }),
          fetchJson<WorkerListResponse>(`/api/workers${makeQuery(dashboardCacheParams)}`, { signal: controller.signal }),
          fetchJson<AgentListResponse>(
            `/api/admin/agents${makeQuery({
              directory_path: selectedDirectoryPath,
              direct_only: true,
              include_inactive: true,
              backend: "all",
              ...dashboardCacheParams,
            })}`,
            { signal: controller.signal },
          ),
          fetchJson<WorkflowRunSnapshot>(`/api/workflow-editor/runs${makeQuery(dashboardCacheParams)}`, { signal: controller.signal }),
          fetchJson<WorkflowGraphActivityReport>(
            `/api/admin/reports/workflow-graph-activity${makeQuery({ limit: 5, ...dashboardCacheParams })}`,
            { signal: controller.signal },
          ),
          fetchJson<ApprovalIndexResponse>(
            `/api/admin/approvals${makeQuery({
              statuses: "pending",
              directory_path: selectedDirectoryPath,
              direct_only: true,
              ...dashboardCacheParams,
            })}`,
            { signal: controller.signal },
          ),
          fetchJson<SessionListResponse>(
            `/api/admin/sessions${makeQuery({
              include_expired: true,
              directory_path: selectedDirectoryPath,
              direct_only: true,
              ...dashboardCacheParams,
            })}`,
            { signal: controller.signal },
          ),
          fetchJson<ToolCallsResponse>(
            `/api/admin/tool-calls${makeQuery({
              directory_path: selectedDirectoryPath,
              direct_only: true,
              limit: 5,
              ...dashboardCacheParams,
            })}`,
            { signal: controller.signal },
          ),
          fetchJson<CapacityHealthPayload>(`/api/admin/capacity-health${makeQuery(dashboardCacheParams)}`, { signal: controller.signal }),
          fetchJson<ReleaseInfoPayload>(
            `/api/admin/release-info${makeQuery({ ...dashboardCacheParams, news_index: newsIndex })}`,
            { signal: controller.signal },
          ),
          fetchJson<WorkflowEditorDocument>(`/api/workflow-editor/document${makeQuery(dashboardCacheParams)}`, { signal: controller.signal }),
          fetchJson<WorkflowEditorCatalog>(
            `/api/workflow-editor/catalog${makeQuery({ directory_path: selectedDirectoryPath, ...dashboardCacheParams })}`,
            { signal: controller.signal },
          ),
        ]);

      if (cancelled) return;
      if (releaseInfoResult.status === "fulfilled" && releaseInfoResult.value.news) {
        setNewsIndex(releaseInfoResult.value.news.index);
      }
      setState({
        adminState: remoteFromResult(adminStateResult),
        deploymentStatus: remoteFromResult(deploymentStatusResult),
        workers: remoteFromResult(
          workersResult,
          "No workers are reporting.",
          (value) => (value.workers.length === 0 ? "No workers are reporting." : null),
        ),
        agents: remoteFromResult(
          agentsResult,
          "No agents were returned for this directory scope.",
          (value) => (value.items.length === 0 ? "No agents were returned for this directory scope." : null),
        ),
        workflowRuns: remoteFromResult(
          workflowRunsResult,
          "No workflow runs have been observed yet.",
          (value) => (value.runs.length === 0 ? "No workflow runs have been observed yet." : null),
        ),
        workflowActivity: remoteFromResult(
          workflowActivityResult,
          "No workflow graph activity is available yet.",
          (value) => (value.graphs.length === 0 ? "No workflow graph activity is available yet." : null),
        ),
        approvals: remoteFromResult(
          approvalsResult,
          "No approval requests are waiting for review.",
          (value) => (value.health.total === 0 ? "No approval requests are waiting for review." : null),
        ),
        sessions: remoteFromResult(
          sessionsResult,
          "No sessions are active or expired in the current directory scope.",
          (value) => (value.counts.total === 0 ? "No sessions are active or expired in the current directory scope." : null),
        ),
        toolCalls: remoteFromResult(
          toolCallsResult,
          "No tool-call audit entries were returned.",
          (value) => (value.items.length === 0 ? "No tool-call audit entries were returned." : null),
        ),
        capacityHealth: remoteFromResult(capacityHealthResult),
        releaseInfo: remoteFromResult(releaseInfoResult),
        workflowDocument: remoteFromResult(
          workflowDocumentResult,
          "No workflow document has been created yet.",
          (value) => (!value.config_exists && !value.layout_exists ? "No workflow document has been created yet." : null),
        ),
        workflowCatalog: remoteFromResult(
          workflowCatalogResult,
          "No catalog nodes are available for this directory.",
          (value) => (value.nodes.length === 0 ? "No catalog nodes are available for this directory." : null),
        ),
        lastRefreshedAt: new Date().toISOString(),
      });
    }

    void load();
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [selectedDirectoryPath, refreshNonce]);

  async function loadNewsEntry(targetIndex: number) {
    if (targetIndex < 0) {
      return;
    }
    setNewsIndex(targetIndex);
    setState((current) => ({
      ...current,
      releaseInfo: {
        ...current.releaseInfo,
        loading: true,
        error: null,
        missing: null,
      },
    }));

    try {
      const releaseInfo = await fetchJson<ReleaseInfoPayload>(
        `/api/admin/release-info${makeQuery({
          _dashboard: "1",
          _dashboard_refresh: refreshNonce > 0 ? refreshNonce : null,
          news_index: targetIndex,
        })}`,
      );
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
    state.adminState.loading ||
    state.deploymentStatus.loading ||
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

  return (
    <div className="feature-page dashboard-page" style={{ display: "grid", gap: "1rem" }}>
      <div className="admin-toolbar">
        <div style={{ display: "grid", gap: "0.2rem", minWidth: 0 }}>
          <span className="section-label">Operations dashboard</span>
          <strong style={{ overflowWrap: "anywhere" }}>{selectedDirectoryPath}</strong>
          <span style={{ color: "inherit", opacity: 0.8 }}>
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
          title="Control-plane status"
          statusClassName={stateClassName(statusTone(state.adminState))}
          statusLabel={statusLabel(state.adminState)}
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
                <div className="detail-list">
                  <div>
                    <span>Directory</span>
                    <strong>{adminState.selected_directory_path}</strong>
                  </div>
                  <div>
                    <span>Execution backend</span>
                    <strong>{adminState.execution_backend}</strong>
                  </div>
                  <div>
                    <span>Directory-scoped config</span>
                    <strong>{adminState.directory_scoped_config_enabled ? "Enabled" : "Disabled"}</strong>
                  </div>
                  <div>
                    <span>Privileges</span>
                    <strong>{adminState.privileged_operations.unlocked ? "Unlocked" : "Locked"}</strong>
                  </div>
                </div>
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
                <div className="detail-list">
                  <div>
                    <span>Status</span>
                    <strong>{deploymentStatus.active ? (deploymentStatus.stale ? "Active, stale" : "Active") : "Idle"}</strong>
                  </div>
                  <div>
                    <span>Message</span>
                    <strong>{deploymentStatus.message ?? "No deployment message."}</strong>
                  </div>
                  <div>
                    <span>Updated</span>
                    <strong>{formatTimestamp(deploymentStatus.updated_at ?? deploymentStatus.started_at)}</strong>
                  </div>
                  <div>
                    <span>TTL</span>
                    <strong>{detailValue(deploymentStatus.ttl_seconds)}</strong>
                  </div>
                </div>
              ) : null}
            </DashboardBlock>

            <DashboardBlock
              title="Workers"
              state={state.workers}
              missingFallback="No worker summary is available."
              summary={workerSummary(workers)}
              actions={<TabButton tab="workers" label="Workers" />}
            >
              {workers.length > 0 ? (
                <div style={{ display: "grid", gap: "0.55rem" }}>
                  <div className="detail-list">
                    <div>
                      <span>Total</span>
                      <strong>{workers.length}</strong>
                    </div>
                    <div>
                      <span>Running</span>
                      <strong>{countBy(workers, (worker) => worker.running)}</strong>
                    </div>
                    <div>
                      <span>Failed</span>
                      <strong>{countBy(workers, (worker) => worker.failed)}</strong>
                    </div>
                  </div>
                  <div style={{ display: "grid", gap: "0.4rem" }}>
                    {workers.slice(0, 3).map((worker) => (
                      <div key={worker.worker_id} className="node-activity-item">
                        <div>
                          <strong>{worker.name}</strong>
                          <span>{worker.description ?? worker.unit}</span>
                        </div>
                        <span className={`status-pill ${stateClassName(workerStatusTone(worker))}`}>{worker.active_state}</span>
                      </div>
                    ))}
                  </div>
                </div>
              ) : null}
            </DashboardBlock>

            <DashboardBlock
              title="Capacity"
              state={state.capacityHealth}
              missingFallback="Capacity health is not available."
              summary={capacityHealth ? capacitySummary(capacityHealth) : null}
            >
              {capacityHealth ? (
                <div style={{ display: "grid", gap: "0.55rem" }}>
                  <div className="detail-list">
                    <div>
                      <span>Disk used</span>
                      <strong>
                        {formatPercent(capacityHealth.control_plane.disk.used_percent)} ({formatBytes(capacityHealth.control_plane.disk.used_bytes)} /{" "}
                        {formatBytes(capacityHealth.control_plane.disk.total_bytes)})
                      </strong>
                    </div>
                    <div>
                      <span>Disk free</span>
                      <strong>
                        {formatPercent(capacityHealth.control_plane.disk.free_percent)} ({formatBytes(capacityHealth.control_plane.disk.free_bytes)})
                      </strong>
                    </div>
                    <div>
                      <span>Known storage</span>
                      <strong>{formatBytes(capacityHealth.control_plane.storage.total.size_bytes)}</strong>
                    </div>
                    <div>
                      <span>Sessions</span>
                      <strong>
                        {capacityHealth.sessions.total_sessions} sessions, {capacityHealth.sessions.total_agents} agents
                      </strong>
                    </div>
                    <div>
                      <span>Session storage</span>
                      <strong>
                        {formatBytes(capacityHealth.sessions.storage.measured_total.size_bytes)} across{" "}
                        {capacityHealth.sessions.storage.measured_session_count} measured
                      </strong>
                    </div>
                    <div>
                      <span>Largest session</span>
                      <strong>{sessionStorageSampleSummary(capacityHealth.sessions.storage.largest_sessions[0])}</strong>
                    </div>
                    <div>
                      <span>Workers</span>
                      <strong>
                        {capacityHealth.workers.reachable_workers}/{capacityHealth.workers.total_workers} reachable,{" "}
                        {capacityHealth.workers.telemetry_available_workers} telemetry
                      </strong>
                    </div>
                    <div>
                      <span>Worker disk</span>
                      <strong>{workerDiskSummary(capacityHealth.workers)}</strong>
                    </div>
                    <div>
                      <span>Queue</span>
                      <strong>
                        {capacityHealth.queue.pending_count} pending, {capacityHealth.queue.inflight_count} in-flight
                      </strong>
                    </div>
                    <div>
                      <span>Oldest queue</span>
                      <strong>{capacityHealth.queue.oldest_age_seconds == null ? "n/a" : formatRelativeSeconds(capacityHealth.queue.oldest_age_seconds)}</strong>
                    </div>
                    <div>
                      <span>Prompts</span>
                      <strong>{capacityHealth.prompts.unread_count} unread</strong>
                    </div>
                  </div>
                  <div className="status-pills">
                    <span className={`status-pill ${stateClassName(healthStateTone(capacityHealth.control_plane.disk.health))}`}>
                      Disk {capacityHealth.control_plane.disk.health}
                    </span>
                    <span className={`status-pill ${stateClassName(healthStateTone(capacityHealth.control_plane.storage.total.health))}`}>
                      Storage {capacityHealth.control_plane.storage.total.health}
                    </span>
                    <span className={`status-pill ${stateClassName(capacityWorkerTone(capacityHealth.workers))}`}>
                      Workers {capacityHealth.workers.telemetry_available_workers}/{capacityHealth.workers.total_workers} telemetry
                    </span>
                    <span className={`status-pill ${stateClassName(healthStateTone(workerDiskHealth(capacityHealth.workers)))}`}>
                      Worker disk {workerDiskHealth(capacityHealth.workers)}
                    </span>
                    <span className={`status-pill ${stateClassName(healthStateTone(capacityHealth.queue.health))}`}>
                      Queue {capacityHealth.queue.health}
                    </span>
                    <span className={`status-pill ${stateClassName(healthStateTone(capacityHealth.prompts.health))}`}>
                      Prompts {capacityHealth.prompts.health}
                    </span>
                  </div>
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
                <div style={{ display: "grid", gap: "0.55rem" }}>
                  <div className="detail-list">
                    <div>
                      <span>Total</span>
                      <strong>{agents.length}</strong>
                    </div>
                    <div>
                      <span>Active</span>
                      <strong>{countBy(agents, (agent) => hasActiveAgentStatus(agent.status))}</strong>
                    </div>
                    <div>
                      <span>Inactive</span>
                      <strong>{countBy(agents, (agent) => !hasActiveAgentStatus(agent.status))}</strong>
                    </div>
                    <div>
                      <span>Backends</span>
                      <strong>
                        {[
                          `${countBy(agents, (agent) => agent.backend === "libvirt_vm")} VM`,
                          `${countBy(agents, (agent) => agent.backend === "container_worker_vm")} container`,
                        ].join(", ")}
                      </strong>
                    </div>
                  </div>
                  <div style={{ display: "grid", gap: "0.4rem" }}>
                    {agents.slice(0, 4).map((agent) => (
                      <div key={agent.agent_id} className="node-activity-item">
                        <div>
                          <strong>{agent.target_name}</strong>
                          <span>
                            {agent.agent_id} · {agent.session_id ?? "no session"} · {agent.directory_path}
                          </span>
                        </div>
                        <span className={`status-pill ${stateClassName(agentStatusTone(agent.status))}`}>{agent.status}</span>
                      </div>
                    ))}
                  </div>
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
                <div style={{ display: "grid", gap: "0.55rem" }}>
                  <div className="detail-list">
                    <div>
                      <span>Total</span>
                      <strong>{runSnapshot.runs.length}</strong>
                    </div>
                    <div>
                      <span>Active</span>
                      <strong>{countBy(runSnapshot.runs, (run) => runStatusTone(run.run_state) === "running")}</strong>
                    </div>
                    <div>
                      <span>Failed</span>
                      <strong>{countBy(runSnapshot.runs, (run) => runStatusTone(run.run_state) === "fail")}</strong>
                    </div>
                    <div>
                      <span>Resumable</span>
                      <strong>{countBy(runSnapshot.runs, (run) => Boolean(run.resumable))}</strong>
                    </div>
                  </div>
                  <div style={{ display: "grid", gap: "0.4rem" }}>
                    {runSnapshot.runs.slice(0, 4).map((run) => (
                      <div key={run.event_id} className="node-activity-item">
                        <div>
                          <strong>{run.event_id}</strong>
                          <span>
                            {run.trace_id ?? "no trace"} · {formatTimestamp(run.latest_activity_at ?? run.occurred_at)}
                          </span>
                          <DashboardLinkRow
                            links={[
                              ...(run.trace_id
                                ? [{ tab: "workflow" as AdminTab, label: "Trace", value: run.trace_id, params: { trace_id: run.trace_id } }]
                                : []),
                            ]}
                          />
                        </div>
                        <span className={`status-pill ${stateClassName(runStatusTone(run.run_state))}`}>{run.run_state}</span>
                      </div>
                    ))}
                  </div>
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
                <div style={{ display: "grid", gap: "0.55rem" }}>
                  <div className="detail-list">
                    <div>
                      <span>Graphs</span>
                      <strong>{activityReport.graphs.length}</strong>
                    </div>
                    <div>
                      <span>Activities</span>
                      <strong>{activityReport.graphs.reduce((sum, graph) => sum + graph.activity_count, 0)}</strong>
                    </div>
                    <div>
                      <span>Latest</span>
                      <strong>{activityReport.graphs.length === 0 ? "n/a" : formatTimestamp(activityReport.graphs[0]?.latest_activity_at ?? activityReport.generated_at)}</strong>
                    </div>
                  </div>
                  <div style={{ display: "grid", gap: "0.4rem" }}>
                    {activityReport.graphs.slice(0, 3).map((graph) => (
                      <div key={graph.graph_id} className="node-activity-item">
                        <div>
                          <strong>{graph.name}</strong>
                          <span>
                            {graph.activity_count} activities · {graph.run_count} runs · {graph.node_count} nodes
                          </span>
                          <DashboardLinkRow
                            links={[
                              { tab: "reports", label: "Graph", value: graph.graph_id },
                              ...(graph.node_ids[0] ? [{ tab: "workflow" as AdminTab, label: "Node", value: graph.node_ids[0] }] : []),
                            ]}
                          />
                        </div>
                        <span className={`status-pill ${stateClassName(statusByCountClass(graph.activity_count))}`}>{graph.activity_count}</span>
                      </div>
                    ))}
                  </div>
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
                <div style={{ display: "grid", gap: "0.55rem" }}>
                  <div className="detail-list">
                    <div>
                      <span>Pending</span>
                      <strong>{approvals.health.pending}</strong>
                    </div>
                    <div>
                      <span>Expired</span>
                      <strong>{approvals.health.expired}</strong>
                    </div>
                    <div>
                      <span>Oldest pending</span>
                      <strong>{approvals.health.oldest_pending_age_seconds == null ? "n/a" : formatRelativeSeconds(approvals.health.oldest_pending_age_seconds)}</strong>
                    </div>
                  </div>
                  <div style={{ display: "grid", gap: "0.4rem" }}>
                    {approvals.items.slice(0, 4).map((item) => (
                      <div key={item.request_id} className="node-activity-item">
                        <div>
                          <strong>{item.tool_name || item.action_type}</strong>
                          <span>
                            {item.session_id} · {formatTimestamp(item.created_at)}
                          </span>
                          <DashboardLinkRow
                            links={[
                              { tab: "sessions", label: "Session", value: item.session_id, params: { session_id: item.session_id } },
                              { tab: "approvals", label: "Approval", value: item.request_id, params: { request_id: item.request_id } },
                            ]}
                          />
                        </div>
                        <span className={`status-pill ${stateClassName(approvalItemTone(item.status))}`}>{item.status}</span>
                      </div>
                    ))}
                  </div>
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
                <div style={{ display: "grid", gap: "0.55rem" }}>
                  <div className="detail-list">
                    <div>
                      <span>Active</span>
                      <strong>{sessions.counts.active}</strong>
                    </div>
                    <div>
                      <span>Expired</span>
                      <strong>{sessions.counts.expired}</strong>
                    </div>
                    <div>
                      <span>Total</span>
                      <strong>{sessions.counts.total}</strong>
                    </div>
                    <div>
                      <span>Wake cue</span>
                      <strong>
                        {sessions.active_items.map(sessionWakeCue).find(Boolean) ??
                          sessions.expired_items.map(sessionWakeCue).find(Boolean) ??
                          "No wake-schedule cue exposed by the list payload."}
                      </strong>
                    </div>
                  </div>
                  <div style={{ display: "grid", gap: "0.4rem" }}>
                    {sessions.active_items.slice(0, 3).map((session) => (
                      <div key={session.session_id} className="node-activity-item">
                        <div>
                          <strong>{session.session_id}</strong>
                          <span>
                            {panelTitleList([session.model ?? "no model", session.vm_id ?? "no VM", session.directory_path])}
                          </span>
                          <DashboardLinkRow
                            links={[
                              ...(session.issue_number
                                ? [{ tab: "issues" as AdminTab, label: "Issue", value: session.issue_number }]
                                : []),
                              { tab: "sessions", label: "Session", value: session.session_id, params: { session_id: session.session_id } },
                            ]}
                          />
                        </div>
                        <span className={`status-pill ${stateClassName(sessionItemTone(session))}`}>{session.is_expired ? "Expired" : "Active"}</span>
                      </div>
                    ))}
                  </div>
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
                <div style={{ display: "grid", gap: "0.55rem" }}>
                  <div className="detail-list">
                    <div>
                      <span>Returned</span>
                      <strong>{toolCalls.counts?.returned ?? toolCalls.items.length}</strong>
                    </div>
                    <div>
                      <span>Success</span>
                      <strong>{toolCalls.counts?.status_counts?.success ?? toolCalls.counts?.success ?? 0}</strong>
                    </div>
                    <div>
                      <span>Denied</span>
                      <strong>{toolCalls.counts?.status_counts?.denied ?? toolCalls.counts?.denied ?? 0}</strong>
                    </div>
                    <div>
                      <span>Error</span>
                      <strong>{toolCalls.counts?.status_counts?.error ?? toolCalls.counts?.error ?? 0}</strong>
                    </div>
                  </div>
                  <div style={{ display: "grid", gap: "0.4rem" }}>
                    {toolCalls.items.slice(0, 5).map((item) => (
                      <div key={`${item.tool_name}-${item.created_at}-${item.request_id ?? item.correlation_id ?? item.trace_id ?? item.session_id ?? "call"}`} className="node-activity-item">
                        <div>
                          <strong>{item.tool_name}</strong>
                          <span>
                            {item.session_id ?? "no session"} · {formatTimestamp(item.created_at)}
                          </span>
                          <DashboardLinkRow
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
                                      params: item.workflow_run_id
                                        ? { run_id: item.workflow_run_id }
                                        : { trace_id: item.trace_id ?? "" },
                                    },
                                  ]
                                : []),
                              ...(item.agent_id ? [{ tab: "agents" as AdminTab, label: "Agent", value: item.agent_id }] : []),
                              ...(item.vm_id ? [{ tab: "vms" as AdminTab, label: "VM", value: item.vm_id }] : []),
                            ]}
                          />
                        </div>
                        <span className={`status-pill ${stateClassName(toolCallTone(item))}`}>{toolCallStatus(item)}</span>
                      </div>
                    ))}
                  </div>
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
                <div style={{ display: "grid", gap: "0.55rem" }}>
                  <div className="detail-list">
                    <div>
                      <span>Config</span>
                      <strong>{workflowDocument.config_exists ? "Present" : "Missing"}</strong>
                    </div>
                    <div>
                      <span>Layout</span>
                      <strong>{workflowDocument.layout_exists ? "Present" : "Missing"}</strong>
                    </div>
                    <div>
                      <span>Graphs</span>
                      <strong>{workflowDocument.graphs.length}</strong>
                    </div>
                    <div>
                      <span>Revision</span>
                      <strong>{workflowDocument.revision}</strong>
                    </div>
                  </div>
                  <div className="status-pills">
                    <span className={`status-pill ${stateClassName(statusByCountClass(workflowDocument.config_exists ? 1 : 0))}`}>Config {workflowDocument.config_exists ? "available" : "missing"}</span>
                    <span className={`status-pill ${stateClassName(statusByCountClass(workflowDocument.layout_exists ? 1 : 0))}`}>Layout {workflowDocument.layout_exists ? "available" : "missing"}</span>
                  </div>
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
                <div style={{ display: "grid", gap: "0.55rem" }}>
                  <div className="detail-list">
                    <div>
                      <span>Worker</span>
                      <strong>{workflowCatalog.worker_id}</strong>
                    </div>
                    <div>
                      <span>Nodes</span>
                      <strong>{workflowCatalog.nodes.length}</strong>
                    </div>
                    <div>
                      <span>Kinds</span>
                      <strong>
                        {countBy(workflowCatalog.nodes, (node) => node.kind === "trigger")} triggers,{" "}
                        {countBy(workflowCatalog.nodes, (node) => node.kind === "gate")} gates,{" "}
                        {countBy(workflowCatalog.nodes, (node) => node.kind === "effect")} effects
                      </strong>
                    </div>
                  </div>
                  <div style={{ display: "grid", gap: "0.4rem" }}>
                    {workflowCatalog.nodes.slice(0, 4).map((node) => (
                      <div key={`${node.kind}:${node.type}`} className="node-activity-item">
                        <div>
                          <strong>{node.label}</strong>
                          <span>{node.type}</span>
                        </div>
                        <span className={`status-pill ${stateClassName(statusByCountClass(node.fields.length))}`}>{node.kind}</span>
                      </div>
                    ))}
                  </div>
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

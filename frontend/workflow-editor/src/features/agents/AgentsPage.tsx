import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";

import { fetchJson, formatTimestamp, isApiAuthError } from "../admin/api";
import { useAdminUi } from "../admin/AdminContext";
import { Tooltip } from "../admin/Tooltip";
import { readAdminHashQuery } from "../admin/hash";
import type {
  AgentActionResponse,
  AgentBackend,
  AgentCreateRequest,
  AgentCreateOptionsResponse,
  AgentExecutionRecord,
  AgentListResponse,
  AgentLogResponse,
  AgentLogView,
  AgentPromptQueueSummary,
  AgentRecord,
  AgentPolicyUpdateRequest,
  AgentSessionPolicy,
  SkillLibraryItem,
  SkillListResponse,
  RuntimeImageInfo,
} from "../admin/types";

type ActionLogEntry = {
  at: string;
  message: string;
  tone: "info" | "error";
};

type OperatorSteeringNoteStatus = "pending" | "injecting" | "delivered" | "cancelled" | "failed";

type OperatorSteeringNote = {
  note_id: string;
  session_id: string;
  dispatch_id: string;
  message: string;
  status: OperatorSteeringNoteStatus | string;
  worker_execution_id: string | null;
  created_by: string | null;
  created_at: string;
  updated_at: string;
  delivered_at: string | null;
  cancelled_at: string | null;
  provider: string | null;
  provider_endpoint: string | null;
  error: string | null;
  delivery_attempts: number;
};

type OperatorSteeringContext = {
  session_id: string;
  dispatch_id: string;
  workflow_run_id: string | null;
  effect_id: string | null;
  node_id: string | null;
  agent_harness: string;
  execution_backend: string | null;
  directory_path: string | null;
  model: string | null;
  trace_id: string | null;
  started_at: string;
  expires_at: string;
};

type OperatorSteeringContextResponse = {
  session_id: string;
  directory_path: string;
  context: OperatorSteeringContext | null;
};

type OperatorSteeringNoteListResponse = {
  session_id: string;
  dispatch_id: string | null;
  directory_path: string;
  notes: OperatorSteeringNote[];
};

type OperatorSteeringNoteMutationResponse = {
  directory_path: string;
  mode?: "queued_note" | "interrupt_and_resume" | string;
  note: OperatorSteeringNote;
  interrupt?: {
    session_id: string;
    dispatch_id: string;
    interrupted_execution_id: string;
    replacement_execution_id: string;
    thread_id: string | null;
    message: string;
  };
};

type BackendFilter = "all" | AgentBackend;
type StatusFilter = "active" | "inactive" | "all";

type AgentCreateFormState = {
  backend: AgentBackend;
  vmName: string;
  targetName: string;
  sessionId: string;
  model: string;
  ttlHours: string;
  labels: string;
  webSearchEnabled: boolean;
  allowedTools: string;
  allowedScriptNames: string;
  discordChannels: string;
  emailMailboxes: string;
  emailFolders: string;
  provider: string;
  vmUser: string;
  agentProvider: string;
  vmImage: string;
  containerImage: string;
  dotnet: boolean;
  nuget: boolean;
  mssql: boolean;
  mssqlInitRoot: string;
  playwright: boolean;
  codexSkills: string[];
};

type AgentPolicyFormState = {
  model: string;
  ttlHours: string;
  labels: string;
  webSearchEnabled: boolean;
  allowedTools: string;
  allowedScriptNames: string;
  approvalNotificationTargetIds: string[];
  discordChannels: string;
  teamsChannels: string;
  emailMailboxes: string;
  emailFolders: string;
};

type AgentSkillInstallFormState = {
  codexSkills: string[];
};

const REFRESH_MS = 5000;
const LOG_PAGE_LIMIT = 300;
const LOG_SCROLL_BOTTOM_THRESHOLD = 24;
const AGENT_SELECTION_STORAGE_KEY = "admin-web:agents:selected-agent";
const AUTH_EXPIRED_MESSAGE = "Your admin session expired. Sign in again to resume live updates.";

type AgentLogViewState = {
  text: string;
  loading: boolean;
  loadingOlder: boolean;
  error: string | null;
  appliedQuery: string;
  loadedLines: number;
  returnedLines: number;
  totalLines: number | null;
  startLine: number | null;
  endLine: number | null;
  hasMoreBefore: boolean;
  pendingLiveUpdate: boolean;
  updatedAt: string | null;
};

function emptyLogState(text = "Loading..."): AgentLogViewState {
  return {
    text,
    loading: true,
    loadingOlder: false,
    error: null,
    appliedQuery: "",
    loadedLines: 0,
    returnedLines: 0,
    totalLines: null,
    startLine: null,
    endLine: null,
    hasMoreBefore: false,
    pendingLiveUpdate: false,
    updatedAt: null,
  };
}

function emptyCreateForm(): AgentCreateFormState {
  return {
    backend: "libvirt_vm",
    vmName: "",
    targetName: "",
    sessionId: "",
    model: "",
    ttlHours: "",
    labels: "",
    webSearchEnabled: false,
    allowedTools: "",
    allowedScriptNames: "",
    discordChannels: "",
    emailMailboxes: "",
    emailFolders: "",
    provider: "",
    vmUser: "agent",
    agentProvider: "",
    vmImage: "",
    containerImage: "",
    dotnet: false,
    nuget: false,
    mssql: false,
    mssqlInitRoot: "",
    playwright: false,
    codexSkills: [],
  };
}

function emptyPolicyForm(): AgentPolicyFormState {
  return {
    model: "",
    ttlHours: "",
    labels: "",
    webSearchEnabled: false,
    allowedTools: "",
    allowedScriptNames: "",
    approvalNotificationTargetIds: [],
    discordChannels: "",
    teamsChannels: "",
    emailMailboxes: "",
    emailFolders: "",
  };
}

function emptySkillInstallForm(): AgentSkillInstallFormState {
  return {
    codexSkills: [],
  };
}

function parseList(value: string): string[] {
  const seen = new Set<string>();
  const result: string[] = [];
  for (const item of value.split(/[\n,]/)) {
    const normalized = item.trim();
    if (!normalized || seen.has(normalized)) continue;
    seen.add(normalized);
    result.push(normalized);
  }
  return result;
}

function formatList(value: unknown): string {
  if (!Array.isArray(value)) return "";
  return value.filter((item): item is string => typeof item === "string" && item.trim().length > 0).join("\n");
}

function dedupeStrings(values: string[]): string[] {
  const seen = new Set<string>();
  const result: string[] = [];
  for (const value of values) {
    const normalized = value.trim();
    if (!normalized || seen.has(normalized)) continue;
    seen.add(normalized);
    result.push(normalized);
  }
  return result;
}

function formatNotificationTargetOptionLabel(option: { value: string; label: string }): string {
  const label = option.label.trim();
  const value = option.value.trim();
  if (!label || label === value) {
    return value;
  }
  return `${label} (${value})`;
}

function parseLabels(value: string): Record<string, string> {
  const labels: Record<string, string> = {};
  for (const line of value.split(/\n/)) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    const separator = trimmed.includes("=") ? "=" : ":";
    const [rawKey, ...rest] = trimmed.split(separator);
    const key = rawKey.trim();
    const labelValue = rest.join(separator).trim();
    if (key && labelValue) labels[key] = labelValue;
  }
  return labels;
}

function formatLabels(value: Record<string, unknown> | null | undefined): string {
  if (!value) return "";
  return Object.entries(value)
    .filter(([, item]) => item != null && typeof item !== "object")
    .map(([key, item]) => `${key}=${String(item)}`)
    .join("\n");
}

function optionalText(value: string): string | null {
  return value.trim() || null;
}

function optionalNumber(value: string): number | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) ? parsed : null;
}

type AgentListLoadOptions = {
  summaryOnly?: boolean;
  dashboardCache?: boolean;
  dashboardRefresh?: boolean;
  reconcileContainers?: boolean;
  omitVmState?: boolean;
};

type AgentListRefreshBehavior = {
  applyItems?: boolean;
};

function buildAgentListQuery(
  directoryPath: string,
  directOnly: boolean,
  backend: AgentBackend | "all",
  options: AgentListLoadOptions = {},
): string {
  const summaryOnly = options.summaryOnly ?? false;
  const query = new URLSearchParams({
    directory_path: directoryPath,
    direct_only: String(directOnly),
    include_inactive: "true",
    backend,
    summary_only: String(summaryOnly),
    reconcile_containers: String(options.reconcileContainers ?? !summaryOnly),
    omit_vm_state: String(options.omitVmState ?? summaryOnly),
  });
  if (options.dashboardRefresh) {
    query.set("_dashboard", "1");
    query.set("_dashboard_refresh", "1");
  } else if (options.dashboardCache) {
    query.set("_dashboard", "1");
  }
  return query.toString();
}

async function loadAgentList(
  directoryPath: string,
  directOnly: boolean,
  backend: AgentBackend | "all",
  options: AgentListLoadOptions = {},
): Promise<AgentListResponse> {
  return fetchJson<AgentListResponse>(
    `/api/admin/agents?${buildAgentListQuery(directoryPath, directOnly, backend, options)}`,
  );
}

async function loadSkillList(): Promise<SkillLibraryItem[]> {
  const payload = await fetchJson<SkillListResponse>("/api/admin/skills");
  return payload.items;
}

async function loadAgentCreateOptions(
  directoryPath: string,
): Promise<AgentCreateOptionsResponse> {
  const query = new URLSearchParams();
  const trimmed = directoryPath.trim();
  if (trimmed) query.set("directory_path", trimmed);
  const suffix = query.toString();
  return fetchJson<AgentCreateOptionsResponse>(
    `/api/admin/agents/create-options${suffix ? `?${suffix}` : ""}`,
  );
}

async function loadAgentLogPage(
  agent: AgentRecord | null,
  options: { limit?: number; offset?: number; query?: string; view?: AgentLogView } = {},
): Promise<AgentLogResponse> {
  if (!agent) {
    return {
      agent_id: "",
      backend: null,
      view: options.view ?? "executions",
      lines: 0,
      text: "No agent selected.",
      returned_lines: 1,
      has_more_before: false,
    };
  }
  if (!agent.capabilities.logs) {
    return {
      agent_id: agent.agent_id,
      backend: agent.backend,
      view: options.view ?? "executions",
      lines: 0,
      text: "Logs are not available for this agent.",
      returned_lines: 1,
      has_more_before: false,
    };
  }
  const query = new URLSearchParams({
    backend: agent.backend,
    limit: String(options.limit ?? LOG_PAGE_LIMIT),
    offset: String(options.offset ?? 0),
    view: options.view ?? "executions",
  });
  const searchQuery = options.query?.trim();
  if (searchQuery) query.set("query", searchQuery);
  const guestAgent = hostRecord(agent.host, "agent");
  const repoName = hostString(guestAgent, "preferred_repo_name");
  if (repoName) {
    query.set("repo_name", repoName);
  }
  return fetchJson<AgentLogResponse>(
    `/api/admin/agents/${encodeURIComponent(agent.agent_id)}/log?${query.toString()}`,
  );
}

async function loadOperatorSteeringContext(sessionId: string): Promise<OperatorSteeringContextResponse> {
  const query = new URLSearchParams({ session_id: sessionId });
  return fetchJson<OperatorSteeringContextResponse>(`/api/operator-steering/context?${query.toString()}`);
}

async function loadOperatorSteeringNotes(
  sessionId: string,
  dispatchId: string,
): Promise<OperatorSteeringNoteListResponse> {
  const query = new URLSearchParams({ session_id: sessionId, dispatch_id: dispatchId });
  return fetchJson<OperatorSteeringNoteListResponse>(`/api/operator-steering/notes?${query.toString()}`);
}

function agentKey(agent: AgentRecord): string {
  return `${agent.backend}:${agent.agent_id}`;
}

type AgentHashSelection = {
  agentId: string;
  sessionId: string;
};

function agentSelectionFromHash(): AgentHashSelection {
  const params = readAdminHashQuery();
  return {
    agentId: params.get("agent_id")?.trim() ?? "",
    sessionId: params.get("session_id")?.trim() ?? "",
  };
}

function agentMatchesSelection(agent: AgentRecord, selection: AgentHashSelection): boolean {
  if (selection.agentId && agent.agent_id === selection.agentId) {
    return true;
  }
  if (selection.sessionId && agent.session_id === selection.sessionId) {
    return true;
  }
  return false;
}

function backendLabel(backend: AgentBackend): string {
  return backend === "container_worker_vm" ? "Container agents" : "VM agents";
}

function operatorSteeringNoteSummary(note: OperatorSteeringNote): string {
  if (note.status === "pending") return `Pending since ${formatTimestamp(note.created_at)}`;
  if (note.status === "injecting") return `Injecting since ${formatTimestamp(note.updated_at)}`;
  if (note.status === "delivered") return `Delivered ${formatTimestamp(note.delivered_at ?? note.updated_at)}`;
  if (note.status === "cancelled") return `Cancelled ${formatTimestamp(note.cancelled_at ?? note.updated_at)}`;
  return `${note.status} ${formatTimestamp(note.updated_at)}`;
}

function operatorSteeringTargetKey(
  sessionId: string | null | undefined,
  dispatchId: string | null | undefined,
): string | null {
  const normalizedSessionId = sessionId?.trim();
  if (!normalizedSessionId) return null;
  return `${normalizedSessionId}\n${dispatchId?.trim() ?? ""}`;
}

function shortDispatchId(dispatchId: string | null | undefined): string {
  const trimmed = dispatchId?.trim();
  if (!trimmed) return "No dispatch";
  if (trimmed.length <= 18) return trimmed;
  return `${trimmed.slice(0, 10)}...${trimmed.slice(-6)}`;
}

function hostString(host: Record<string, unknown> | null | undefined, key: string): string | null {
  const value = host?.[key];
  return typeof value === "string" && value.trim() ? value : null;
}

function hostNumber(host: Record<string, unknown> | null | undefined, key: string): number | null {
  const value = host?.[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function hostStringList(host: Record<string, unknown> | null | undefined, key: string): string[] {
  const value = host?.[key];
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === "string" && item.trim().length > 0);
}

function hostExecutionList(
  host: Record<string, unknown> | null | undefined,
  key: string,
): AgentExecutionRecord[] {
  const value = host?.[key];
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is AgentExecutionRecord => {
    if (typeof item !== "object" || item === null || Array.isArray(item)) return false;
    const candidate = item as Record<string, unknown>;
    return typeof candidate.execution_id === "string" && typeof candidate.worker_job_id === "string";
  });
}

function hostRecord(
  host: Record<string, unknown> | null | undefined,
  key: string,
): Record<string, unknown> | null {
  const value = host?.[key];
  if (typeof value === "object" && value !== null && !Array.isArray(value)) {
    return value as Record<string, unknown>;
  }
  return null;
}

function hostRuntimeImage(host: Record<string, unknown> | null | undefined): RuntimeImageInfo | null {
  const value = host?.runtime_image;
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return null;
  }
  const candidate = value as Record<string, unknown>;
  if (
    candidate.backend !== "libvirt_vm" &&
    candidate.backend !== "container_worker_vm"
  ) {
    return null;
  }
  if (
    candidate.kind !== "libvirt_vm_template" &&
    candidate.kind !== "container_worker_image"
  ) {
    return null;
  }
  if (typeof candidate.label !== "string" || !candidate.label.trim()) {
    return null;
  }
  return {
    backend: candidate.backend as RuntimeImageInfo["backend"],
    kind: candidate.kind as RuntimeImageInfo["kind"],
    label: candidate.label,
    template_alias: typeof candidate.template_alias === "string" && candidate.template_alias.trim() ? candidate.template_alias : null,
    image_alias: typeof candidate.image_alias === "string" && candidate.image_alias.trim() ? candidate.image_alias : null,
    image_ref: typeof candidate.image_ref === "string" && candidate.image_ref.trim() ? candidate.image_ref : null,
    image_ref_sanitized: typeof candidate.image_ref_sanitized === "boolean" ? candidate.image_ref_sanitized : false,
    missing_reason: typeof candidate.missing_reason === "string" && candidate.missing_reason.trim() ? candidate.missing_reason : null,
  };
}

function agentRuntimeImage(agent: AgentRecord | null | undefined): RuntimeImageInfo | null {
  const runtimeImage = hostRuntimeImage(agent?.host);
  if (runtimeImage) return runtimeImage;
  if (agent?.backend !== "container_worker_vm") return null;
  const imageRef = hostString(agent.host, "image");
  const imageAlias = hostString(agent.host, "image_alias");
  return {
    backend: "container_worker_vm",
    kind: "container_worker_image",
    label: "Container worker image",
    image_alias: imageAlias,
    image_ref: imageRef,
    missing_reason: imageRef ? null : "container_worker_image_missing",
  };
}

function runtimeImagePrimaryText(runtimeImage: RuntimeImageInfo | null): string {
  if (!runtimeImage) return "Unavailable";
  if (runtimeImage.backend === "container_worker_vm") {
    return runtimeImage.image_ref ?? "Unavailable";
  }
  if (runtimeImage.missing_reason) {
    return runtimeImage.label;
  }
  return runtimeImage.template_alias ?? runtimeImage.label;
}

function runtimeImageTooltip(runtimeImage: RuntimeImageInfo | null): string {
  if (!runtimeImage) return "Runtime image metadata unavailable.";
  if (runtimeImage.backend === "container_worker_vm") {
    const imageRef = runtimeImage.image_ref ?? "Unavailable";
    return runtimeImage.image_alias
      ? `${runtimeImage.label}: ${imageRef} (alias: ${runtimeImage.image_alias})`
      : `${runtimeImage.label}: ${imageRef}`;
  }
  if (runtimeImage.missing_reason) {
    return runtimeImage.label;
  }
  const imageRef = runtimeImage.image_ref ?? "Unavailable";
  if (runtimeImage.image_ref_sanitized) {
    return `${runtimeImage.label}: ${imageRef} (sanitized display from host-path metadata)`;
  }
  return runtimeImage.template_alias
    ? `${runtimeImage.label}: ${runtimeImage.template_alias} uses ${imageRef}`
    : `${runtimeImage.label}: ${imageRef}`;
}

function runtimeImageListSummary(runtimeImage: RuntimeImageInfo | null): string | null {
  if (!runtimeImage) return null;
  if (runtimeImage.backend === "container_worker_vm") {
    const imageRef = runtimeImage.image_ref ?? "unavailable";
    return runtimeImage.image_alias ? `Container image ${imageRef} (${runtimeImage.image_alias})` : `Container image ${imageRef}`;
  }
  if (runtimeImage.missing_reason) {
    return runtimeImage.label;
  }
  const imageRef = runtimeImage.image_ref ?? "Unavailable";
  return runtimeImage.template_alias
    ? `VM template ${runtimeImage.template_alias} / ${imageRef}`
    : `VM image ref ${imageRef}`;
}

function recordEntries(value: Record<string, unknown> | null | undefined): Array<[string, string]> {
  if (!value) return [];
  return Object.entries(value)
    .filter(([, item]) => item != null && typeof item !== "object")
    .map(([key, item]) => [key, String(item)]);
}

function responseReturnedLines(payload: AgentLogResponse): number {
  if (typeof payload.returned_lines === "number" && Number.isFinite(payload.returned_lines)) {
    return payload.returned_lines;
  }
  return payload.text ? payload.text.split(/\r?\n/).filter((line, index, lines) => line || index < lines.length - 1).length : 0;
}

function appendLogText(older: string, newer: string): string {
  if (!older) return newer;
  if (!newer) return older;
  if (older.endsWith("\n") || newer.startsWith("\n")) return older + newer;
  return `${older}\n${newer}`;
}

function isScrolledToBottom(element: HTMLElement | null): boolean {
  if (!element) return true;
  return (
    element.scrollHeight - element.scrollTop - element.clientHeight <=
    LOG_SCROLL_BOTTOM_THRESHOLD
  );
}

function metadataForAgent(agent: AgentRecord | null): Record<string, unknown> | null {
  if (!agent) return null;
  return hostRecord(agent.host, "metadata");
}

function labelsForAgent(agent: AgentRecord | null): Array<[string, string]> {
  if (!agent) return [];
  const directLabels = hostRecord(agent.host, "labels");
  const metadataLabels = hostRecord(metadataForAgent(agent), "labels");
  return recordEntries(directLabels ?? metadataLabels).sort(([left], [right]) =>
    left.localeCompare(right, undefined, { sensitivity: "base" }),
  );
}

function installedCodexSkillsForAgent(agent: AgentRecord | null): string[] {
  const directMetadata = metadataForAgent(agent);
  const policyMetadata = hostRecord(hostRecord(agent?.host, "session_policy"), "metadata");
  const skills = hostStringList(directMetadata, "codex_skills");
  return skills.length > 0 ? skills : hostStringList(policyMetadata, "codex_skills");
}

function sessionPolicyForAgent(agent: AgentRecord | null): AgentSessionPolicy | null {
  const rawPolicy = hostRecord(agent?.host, "session_policy");
  if (!rawPolicy) return null;
  const sessionId = hostString(rawPolicy, "session_id");
  if (!sessionId) return null;
  return {
    session_id: sessionId,
    model: hostString(rawPolicy, "model"),
    expires_at: hostString(rawPolicy, "expires_at"),
    allowed_tools: hostStringList(rawPolicy, "allowed_tools"),
    approval_notification_target_ids: hostStringList(rawPolicy, "approval_notification_target_ids"),
    approval_notification_target_options: Array.isArray(rawPolicy.approval_notification_target_options)
      ? rawPolicy.approval_notification_target_options.filter(
          (option): option is { value: string; label: string } => {
            if (!option || typeof option !== "object") {
              return false;
            }
            const candidate = option as Record<string, unknown>;
            return typeof candidate.value === "string" && typeof candidate.label === "string";
          },
        )
      : [],
    web_search_enabled: Boolean(rawPolicy.web_search_enabled),
    allowed_script_names: hostStringList(rawPolicy, "allowed_script_names"),
    discord_channels: hostStringList(rawPolicy, "discord_channels"),
    discord_channel_allowlist: hostStringList(rawPolicy, "discord_channel_allowlist"),
    teams_channel_allowlist: hostStringList(rawPolicy, "teams_channel_allowlist"),
    metadata: hostRecord(rawPolicy, "metadata") ?? {},
    email_mailboxes: hostStringList(rawPolicy, "email_mailboxes"),
    email_folders: hostStringList(rawPolicy, "email_folders"),
    labels: Object.fromEntries(recordEntries(hostRecord(rawPolicy, "labels"))),
  };
}

function promptThreadIdForAgent(agent: AgentRecord | null): string {
  const threadId = sessionPolicyForAgent(agent)?.metadata.agent_thread_id;
  return typeof threadId === "string" ? threadId.trim() : "";
}

function policyFormFromAgent(agent: AgentRecord): AgentPolicyFormState {
  const policy = sessionPolicyForAgent(agent);
  const labels = policy?.labels ?? Object.fromEntries(labelsForAgent(agent));
  return {
    model: policy?.model ?? "",
    ttlHours: "",
    labels: formatLabels(labels),
    webSearchEnabled: Boolean(policy?.web_search_enabled),
    allowedTools: formatList(policy?.allowed_tools),
    allowedScriptNames: formatList(policy?.allowed_script_names),
    approvalNotificationTargetIds: dedupeStrings(policy?.approval_notification_target_ids ?? []),
    discordChannels: formatList(policy?.discord_channel_allowlist.length ? policy.discord_channel_allowlist : policy?.discord_channels),
    teamsChannels: formatList(policy?.teams_channel_allowlist),
    emailMailboxes: formatList(policy?.email_mailboxes),
    emailFolders: formatList(policy?.email_folders),
  };
}

function buildCreatePayload(form: AgentCreateFormState, directoryPath: string): AgentCreateRequest {
  const labels = parseLabels(form.labels);
  const payload: AgentCreateRequest = {
    backend: form.backend,
    session_id: optionalText(form.sessionId),
    directory_path: optionalText(directoryPath),
    model: optionalText(form.model),
    ttl_hours: optionalNumber(form.ttlHours),
    labels,
    web_search_enabled: form.webSearchEnabled,
    allowed_tools: parseList(form.allowedTools),
    allowed_script_names: parseList(form.allowedScriptNames),
    discord_channels: parseList(form.discordChannels),
    email_mailboxes: parseList(form.emailMailboxes),
    email_folders: parseList(form.emailFolders),
  };
  const agentProvider = optionalText(form.agentProvider);
  if (agentProvider) payload.agent_provider = agentProvider;
  if (form.backend === "libvirt_vm") {
    const provider = optionalText(form.provider);
    payload.vm_name = optionalText(form.vmName);
    if (provider) payload.provider = provider;
    payload.vm_user = optionalText(form.vmUser) ?? "agent";
    const vmImage = optionalText(form.vmImage);
    if (vmImage) payload.vm_image = vmImage;
    payload.dotnet = form.dotnet;
    payload.nuget = form.nuget;
    payload.mssql = form.mssql;
    payload.mssql_init_root = form.mssql ? optionalText(form.mssqlInitRoot) : null;
    payload.playwright = form.playwright;
    payload.codex_skills = form.codexSkills;
  } else {
    payload.target_name = optionalText(form.targetName);
    payload.container_image = optionalText(form.containerImage);
  }
  if (form.backend === "container_worker_vm") {
    payload.codex_skills = form.codexSkills;
  }
  return payload;
}

function buildPolicyPayload(form: AgentPolicyFormState): AgentPolicyUpdateRequest {
  const payload: AgentPolicyUpdateRequest = {
    model: optionalText(form.model),
    labels: parseLabels(form.labels),
    web_search_enabled: form.webSearchEnabled,
    allowed_tools: parseList(form.allowedTools),
    allowed_script_names: parseList(form.allowedScriptNames),
    approval_notification_target_ids: dedupeStrings(form.approvalNotificationTargetIds),
    discord_channels: parseList(form.discordChannels),
    teams_channel_allowlist: parseList(form.teamsChannels),
    email_mailboxes: parseList(form.emailMailboxes),
    email_folders: parseList(form.emailFolders),
  };
  if (form.ttlHours.trim()) {
    payload.ttl_hours = optionalNumber(form.ttlHours);
  }
  return payload;
}

function isActiveStatus(status: string): boolean {
  return ["preparing", "starting", "running"].includes(status.trim().toLowerCase());
}

function statusClass(status: string): string {
  const normalized = status.trim().toLowerCase();
  if (normalized === "running" || normalized === "starting" || normalized === "preparing") {
    return "running";
  }
  if (normalized === "failed") return "failed";
  return "idle";
}

function timestampRank(value: string | null): number {
  if (!value) return 0;
  const time = new Date(value).getTime();
  return Number.isFinite(time) ? time : 0;
}

const EMPTY_PROMPT_QUEUE_SUMMARY: AgentPromptQueueSummary = {
  record_count: 0,
  coalesced_count: 0,
  oldest_created_at: null,
  newest_updated_at: null,
  records: [],
  truncated_count: 0,
  read_error: null,
};

function promptQueueForAgent(agent: AgentRecord | null | undefined): AgentPromptQueueSummary {
  return agent?.prompt_queue ?? EMPTY_PROMPT_QUEUE_SUMMARY;
}

function promptQueueSummaryText(summary: AgentPromptQueueSummary): string {
  if (summary.read_error) return "Queue unavailable";
  if (summary.record_count === 0) return "0 queued";
  if (summary.coalesced_count === summary.record_count) {
    return `${summary.record_count} queued`;
  }
  return `${summary.record_count} records / ${summary.coalesced_count} prompts`;
}

function AgentIcon({ children, viewBox = "0 0 24 24" }: { children: ReactNode; viewBox?: string }) {
  return (
    <svg aria-hidden="true" viewBox={viewBox} fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">
      {children}
    </svg>
  );
}

function StartIcon() {
  return (
    <AgentIcon>
      <path d="M8 6.5v11l8-5.5z" fill="currentColor" stroke="none" />
    </AgentIcon>
  );
}

function StopIcon() {
  return (
    <AgentIcon>
      <rect x="7.5" y="7.5" width="9" height="9" rx="1.5" fill="currentColor" stroke="none" />
    </AgentIcon>
  );
}

function PowerIcon() {
  return (
    <AgentIcon>
      <path d="M12 3.5v7" />
      <path d="M7.2 6.8a8 8 0 1 0 9.6 0" />
    </AgentIcon>
  );
}

function ResumeIcon() {
  return (
    <AgentIcon>
      <path d="M7.5 7.5H4.5v-3" />
      <path d="M4.7 11a7.5 7.5 0 1 1 2.2 6.1" />
    </AgentIcon>
  );
}

function EnableIcon() {
  return (
    <AgentIcon>
      <circle cx="12" cy="12" r="8" />
      <path d="m8.8 12.2 2.1 2.1 4.5-4.8" />
    </AgentIcon>
  );
}

function DisableIcon() {
  return (
    <AgentIcon>
      <circle cx="12" cy="12" r="8" />
      <path d="M8.2 15.8 15.8 8.2" />
    </AgentIcon>
  );
}

function KillIcon() {
  return (
    <AgentIcon>
      <path d="M9 4.8h6l4.2 4.2v6L15 19.2H9L4.8 15v-6z" />
      <path d="m9.4 9.4 5.2 5.2" />
      <path d="m14.6 9.4-5.2 5.2" />
    </AgentIcon>
  );
}

function DestroyIcon() {
  return (
    <AgentIcon>
      <path d="M5.5 7.5h13" />
      <path d="M9 7.5v-2h6v2" />
      <path d="M8.5 10.5v6" />
      <path d="M12 10.5v6" />
      <path d="M15.5 10.5v6" />
      <path d="M7.5 7.5v10.2a1.3 1.3 0 0 0 1.3 1.3h6.4a1.3 1.3 0 0 0 1.3-1.3V7.5" />
    </AgentIcon>
  );
}

function EditIcon() {
  return (
    <AgentIcon>
      <path d="M5 19h4.2L18.4 9.8a2.1 2.1 0 0 0-3-3L6.2 16H5z" />
      <path d="m13.9 8.3 1.8 1.8" />
    </AgentIcon>
  );
}

function SkillIcon() {
  return (
    <AgentIcon>
      <path d="M6.5 5.5h8.2L18 8.8v9.7H6.5z" />
      <path d="M14.7 5.5v3.3H18" />
      <path d="M9 12h6" />
      <path d="M9 15h4" />
    </AgentIcon>
  );
}

function SteeringIcon() {
  return (
    <AgentIcon>
      <path d="M5.5 6.5h13v8h-5.4L9.2 18v-3.5H5.5z" />
      <path d="M8.5 9.5h7" />
      <path d="M8.5 12h4.5" />
    </AgentIcon>
  );
}

export function AgentsPage() {
  const {
    canControlWorkflowRuns,
    canReadWorkflowRuns,
    privilegedUnlocked,
    refreshAuthSession,
    selectedDirectoryPath,
  } = useAdminUi();
  const [items, setItems] = useState<AgentRecord[]>([]);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [hashSelection, setHashSelection] = useState<AgentHashSelection>(() => agentSelectionFromHash());
  const [backendFilter, setBackendFilter] = useState<BackendFilter>("all");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [agentLog, setAgentLog] = useState<AgentLogViewState>(() => emptyLogState());
  const [logSearchInput, setLogSearchInput] = useState("");
  const [logView, setLogView] = useState<AgentLogView>("executions");
  const [logReloadNonce, setLogReloadNonce] = useState(0);
  const [agentPrompt, setAgentPrompt] = useState("");
  const [agentPromptResumeThreadId, setAgentPromptResumeThreadId] = useState("");
  const [showPromptForm, setShowPromptForm] = useState(false);
  const [showOperatorSteeringDialog, setShowOperatorSteeringDialog] = useState(false);
  const [operatorSteeringContext, setOperatorSteeringContext] = useState<OperatorSteeringContext | null>(null);
  const [operatorSteeringNotes, setOperatorSteeringNotes] = useState<OperatorSteeringNote[]>([]);
  const [operatorSteeringDraft, setOperatorSteeringDraft] = useState("");
  const [operatorSteeringLoading, setOperatorSteeringLoading] = useState(false);
  const [operatorSteeringSubmitting, setOperatorSteeringSubmitting] = useState(false);
  const [operatorSteeringCancellingId, setOperatorSteeringCancellingId] = useState<string | null>(null);
  const [operatorSteeringError, setOperatorSteeringError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [actionLog, setActionLog] = useState<ActionLogEntry[]>([]);
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [createForm, setCreateForm] = useState<AgentCreateFormState>(() =>
    emptyCreateForm(),
  );
  const [skillOptions, setSkillOptions] = useState<SkillLibraryItem[]>([]);
  const [createOptions, setCreateOptions] = useState<AgentCreateOptionsResponse | null>(null);
  const [showEditForm, setShowEditForm] = useState(false);
  const [editForm, setEditForm] = useState<AgentPolicyFormState>(() => emptyPolicyForm());
  const [showSkillInstallForm, setShowSkillInstallForm] = useState(false);
  const [skillInstallForm, setSkillInstallForm] = useState<AgentSkillInstallFormState>(() =>
    emptySkillInstallForm(),
  );
  const [inlineValidation, setInlineValidation] = useState("");
  const [selectionHydrated, setSelectionHydrated] = useState(false);
  const logViewRef = useRef<HTMLPreElement | null>(null);
  const logSticksToBottomRef = useRef(true);
  const logLoadedLinesRef = useRef(LOG_PAGE_LIMIT);
  const pendingLogScrollRef = useRef<"bottom" | "top" | { previousHeight: number; previousTop: number } | null>(null);
  const operatorSteeringLoadSeqRef = useRef(0);
  const operatorSteeringDraftTargetRef = useRef<string | null>(null);
  const refreshAuthSessionRef = useRef(refreshAuthSession);
  const hashSelectionActive = Boolean(hashSelection.agentId || hashSelection.sessionId);

  useEffect(() => {
    refreshAuthSessionRef.current = refreshAuthSession;
  }, [refreshAuthSession]);

  useEffect(() => {
    const syncHashSelection = () => {
      const nextSelection = agentSelectionFromHash();
      setHashSelection(nextSelection);
      if (nextSelection.agentId || nextSelection.sessionId) {
        setSelectedKey(null);
        setBackendFilter("all");
        setStatusFilter("all");
      }
    };

    syncHashSelection();
    window.addEventListener("hashchange", syncHashSelection);
    return () => {
      window.removeEventListener("hashchange", syncHashSelection);
    };
  }, []);

  useEffect(() => {
    const saved = window.localStorage.getItem(AGENT_SELECTION_STORAGE_KEY);
    if (saved && !hashSelectionActive) {
      setSelectedKey(saved);
    }
    setSelectionHydrated(true);
  }, [hashSelectionActive]);

  useEffect(() => {
    if (!selectionHydrated) return;
    if (selectedKey) {
      window.localStorage.setItem(AGENT_SELECTION_STORAGE_KEY, selectedKey);
      return;
    }
    window.localStorage.removeItem(AGENT_SELECTION_STORAGE_KEY);
  }, [selectedKey, selectionHydrated]);

  useLayoutEffect(() => {
    const pending = pendingLogScrollRef.current;
    const element = logViewRef.current;
    if (!pending || !element) return;
    if (pending === "bottom") {
      element.scrollTop = element.scrollHeight;
      logSticksToBottomRef.current = true;
    } else if (pending === "top") {
      element.scrollTop = 0;
      logSticksToBottomRef.current = false;
    } else {
      element.scrollTop = element.scrollHeight - pending.previousHeight + pending.previousTop;
    }
    pendingLogScrollRef.current = null;
  }, [agentLog.text]);

  useEffect(() => {
    if (!showCreateForm) {
      setCreateForm(emptyCreateForm());
    }
  }, [showCreateForm]);

  useEffect(() => {
    setCreateForm((current) => ({
      ...current,
      agentProvider: "",
      vmImage: "",
      containerImage: "",
    }));
  }, [selectedDirectoryPath]);

  useEffect(() => {
    let cancelled = false;
    const refresh = async () => {
      try {
        const skills = await loadSkillList();
        if (!cancelled) setSkillOptions(skills);
      } catch (reason: unknown) {
        if (!cancelled) setError(reason instanceof Error ? reason.message : String(reason));
      }
    };
    void refresh();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    const refresh = async () => {
      setCreateOptions(null);
      try {
        const options = await loadAgentCreateOptions(selectedDirectoryPath);
        if (!cancelled) setCreateOptions(options);
      } catch {
        if (!cancelled) setCreateOptions(null);
      }
    };
    void refresh();
    return () => {
      cancelled = true;
    };
  }, [selectedDirectoryPath]);

  const filteredItems = useMemo(() => {
    return [...items]
      .filter((item) => backendFilter === "all" || item.backend === backendFilter)
      .filter((item) => {
        if (statusFilter === "all") return true;
        const active = isActiveStatus(item.status);
        return statusFilter === "active" ? active : !active;
      })
      .sort((left, right) => {
        const byTime = timestampRank(right.updated_at) - timestampRank(left.updated_at);
        if (byTime !== 0) return byTime;
        return agentKey(left).localeCompare(agentKey(right), undefined, {
          numeric: true,
          sensitivity: "base",
        });
      });
  }, [backendFilter, items, statusFilter]);

  const groupedItems = useMemo(() => {
    return {
      libvirt_vm: filteredItems.filter((item) => item.backend === "libvirt_vm"),
      container_worker_vm: filteredItems.filter((item) => item.backend === "container_worker_vm"),
    } satisfies Record<AgentBackend, AgentRecord[]>;
  }, [filteredItems]);

  const selectedAgent = useMemo(
    () => {
      const explicitSelection = selectedKey
        ? filteredItems.find((item) => agentKey(item) === selectedKey) ?? null
        : null;
      if (explicitSelection) {
        return explicitSelection;
      }
      const hashSelectionMatch = hashSelection.agentId || hashSelection.sessionId
        ? filteredItems.find((item) => agentMatchesSelection(item, hashSelection)) ?? null
        : null;
      if (hashSelectionMatch) {
        return hashSelectionMatch;
      }
      return filteredItems[0] ?? null;
    },
    [filteredItems, hashSelection, selectedKey],
  );
  const selectedAgentKey = selectedAgent ? agentKey(selectedAgent) : null;
  const selectedAgentPolicy = selectedAgent ? sessionPolicyForAgent(selectedAgent) : null;
  const selectedAgentApprovalNotificationTargetOptions = selectedAgentPolicy?.approval_notification_target_options ?? [];
  const activeLogView: AgentLogView = selectedAgent?.backend === "container_worker_vm" ? logView : "executions";
  const operatorSteeringSessionId = selectedAgent?.session_id?.trim() ?? "";

  const refreshOperatorSteering = useCallback(async () => {
    const loadSeq = operatorSteeringLoadSeqRef.current + 1;
    operatorSteeringLoadSeqRef.current = loadSeq;
    if (!operatorSteeringSessionId || !canReadWorkflowRuns) {
      operatorSteeringDraftTargetRef.current = null;
      setOperatorSteeringDraft("");
      setOperatorSteeringContext(null);
      setOperatorSteeringNotes([]);
      setOperatorSteeringLoading(false);
      setOperatorSteeringError(null);
      return;
    }
    setOperatorSteeringLoading(true);
    setOperatorSteeringError(null);
    try {
      const contextPayload = await loadOperatorSteeringContext(operatorSteeringSessionId);
      if (operatorSteeringLoadSeqRef.current !== loadSeq) return;
      const context = contextPayload.context;
      const nextDraftTarget = operatorSteeringTargetKey(
        context?.session_id ?? operatorSteeringSessionId,
        context?.dispatch_id ?? null,
      );
      if (operatorSteeringDraftTargetRef.current !== nextDraftTarget) {
        operatorSteeringDraftTargetRef.current = nextDraftTarget;
        setOperatorSteeringDraft("");
      }
      setOperatorSteeringContext(context);
      if (context === null) {
        setOperatorSteeringNotes([]);
        return;
      }
      const notesPayload = await loadOperatorSteeringNotes(context.session_id, context.dispatch_id);
      if (operatorSteeringLoadSeqRef.current !== loadSeq) return;
      setOperatorSteeringNotes(notesPayload.notes);
    } catch (reason: unknown) {
      if (operatorSteeringLoadSeqRef.current !== loadSeq) return;
      if (isApiAuthError(reason)) {
        await refreshAuthSessionRef.current(AUTH_EXPIRED_MESSAGE);
      }
      setOperatorSteeringContext(null);
      setOperatorSteeringNotes([]);
      setOperatorSteeringError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      if (operatorSteeringLoadSeqRef.current === loadSeq) {
        setOperatorSteeringLoading(false);
      }
    }
  }, [canReadWorkflowRuns, operatorSteeringSessionId]);

  useEffect(() => {
    setOperatorSteeringContext(null);
    setOperatorSteeringNotes([]);
    setOperatorSteeringError(null);
    void refreshOperatorSteering();
  }, [refreshOperatorSteering]);

  useEffect(() => {
    if (showPromptForm) return;
    const nextThreadId = promptThreadIdForAgent(selectedAgent);
    setAgentPromptResumeThreadId((current) => (current === nextThreadId ? current : nextThreadId));
  }, [selectedAgent, showPromptForm]);

  const refreshAgents = useCallback(async (
    options: AgentListLoadOptions = {},
    behavior: AgentListRefreshBehavior = {},
  ) => {
    const payload = await loadAgentList(selectedDirectoryPath, true, backendFilter, options);
    if (behavior.applyItems ?? true) {
      setItems(payload.items);
    }
    return payload;
  }, [backendFilter, selectedDirectoryPath]);

  const refreshSummaryAgents = useCallback(async () => {
    await refreshAgents({ summaryOnly: true, dashboardCache: true });
  }, [refreshAgents]);

  const refreshSummaryCache = useCallback(async () => {
    try {
      await refreshAgents({ summaryOnly: true, dashboardRefresh: true }, { applyItems: false });
    } catch {
      // Summary cache refresh is best-effort; the live read has already updated the page.
    }
  }, [refreshAgents]);

  const refreshLiveAgents = useCallback(async () => {
    const payload = await refreshAgents({
      summaryOnly: false,
      reconcileContainers: true,
      omitVmState: false,
    });
    await refreshSummaryCache();
    return payload;
  }, [refreshAgents, refreshSummaryCache]);

  useEffect(() => {
    let cancelled = false;
    const refresh = async () => {
      try {
        await refreshSummaryAgents();
        if (!cancelled) setError(null);
      } catch (reason: unknown) {
        if (!cancelled) {
          if (isApiAuthError(reason)) {
            await refreshAuthSession(AUTH_EXPIRED_MESSAGE);
          }
          setError(reason instanceof Error ? reason.message : String(reason));
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void refresh();
    const timer = window.setInterval(() => {
      void refresh();
    }, REFRESH_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [refreshSummaryAgents]);

  useEffect(() => {
    if (!selectedAgent) {
      setAgentLog({ ...emptyLogState("No agent selected."), loading: false });
      return;
    }
    let cancelled = false;
    const activeQuery = agentLog.appliedQuery;
    const refreshLog = async (showLoadingState: boolean) => {
      if (showLoadingState) {
        pendingLogScrollRef.current = activeLogView === "threads" ? "top" : "bottom";
        setAgentLog((current) => ({
          ...emptyLogState("Loading..."),
          appliedQuery: activeQuery,
          loading: true,
          pendingLiveUpdate: current.pendingLiveUpdate,
        }));
      }
      try {
        const shouldStick = isScrolledToBottom(logViewRef.current);
        const payload = await loadAgentLogPage(selectedAgent, {
          limit: activeQuery ? LOG_PAGE_LIMIT : Math.max(LOG_PAGE_LIMIT, logLoadedLinesRef.current),
          offset: 0,
          query: activeQuery || undefined,
          view: activeLogView,
        });
        if (cancelled) return;
        if (!activeQuery && !showLoadingState && !shouldStick) {
          setAgentLog((current) => ({
            ...current,
            loading: false,
            error: null,
            pendingLiveUpdate: true,
            updatedAt: new Date().toISOString(),
          }));
          return;
        }
        if (!activeQuery) {
          pendingLogScrollRef.current = activeLogView === "threads" ? "top" : "bottom";
        }
        const returnedLines = responseReturnedLines(payload);
        logLoadedLinesRef.current = Math.max(LOG_PAGE_LIMIT, returnedLines);
        setAgentLog((current) => ({
          ...current,
          text: payload.text || (activeQuery ? "No matching log lines." : "No log output yet."),
          loading: false,
          loadingOlder: false,
          error: null,
          appliedQuery: activeQuery,
          loadedLines: returnedLines,
          returnedLines,
          totalLines: payload.total_lines ?? null,
          startLine: payload.start_line ?? null,
          endLine: payload.end_line ?? null,
          hasMoreBefore: Boolean(payload.has_more_before),
          pendingLiveUpdate: false,
          updatedAt: new Date().toISOString(),
        }));
      } catch (reason: unknown) {
        const message = reason instanceof Error ? reason.message : String(reason);
        if (!cancelled) {
          if (isApiAuthError(reason)) {
            await refreshAuthSession(AUTH_EXPIRED_MESSAGE);
          }
          setAgentLog((current) => ({
            ...current,
            text: current.text || message,
            loading: false,
            loadingOlder: false,
            error: message,
            updatedAt: new Date().toISOString(),
          }));
        }
      }
    };
    void refreshLog(true);
    const timer = activeQuery || activeLogView === "threads"
      ? null
      : window.setInterval(() => {
          void refreshLog(false);
        }, REFRESH_MS);
    return () => {
      cancelled = true;
      if (timer !== null) window.clearInterval(timer);
    };
  }, [activeLogView, agentLog.appliedQuery, logReloadNonce, selectedAgent?.backend, selectedAgent?.agent_id]);

  async function handleManualRefresh() {
    setLoading(true);
    try {
      await refreshLiveAgents();
      setError(null);
      setStatus("Refreshed agent list.");
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setLoading(false);
    }
  }

  function reloadLog(query: string) {
    const normalizedQuery = query.trim();
    pendingLogScrollRef.current = activeLogView === "threads" ? "top" : "bottom";
    logLoadedLinesRef.current = LOG_PAGE_LIMIT;
    setAgentLog((current) => ({
      ...emptyLogState("Loading..."),
      appliedQuery: normalizedQuery,
      pendingLiveUpdate: current.pendingLiveUpdate,
    }));
    setLogReloadNonce((current) => current + 1);
  }

  function changeLogView(nextView: AgentLogView) {
    if (nextView === logView) return;
    setLogView(nextView);
    setLogSearchInput("");
    logLoadedLinesRef.current = LOG_PAGE_LIMIT;
    pendingLogScrollRef.current = nextView === "threads" ? "top" : "bottom";
    setAgentLog(emptyLogState("Loading..."));
    setLogReloadNonce((current) => current + 1);
  }

  async function loadOlderLogLines() {
    if (!selectedAgent || agentLog.loadingOlder || !agentLog.hasMoreBefore) return;
    const requestedView = activeLogView;
    const element = logViewRef.current;
    pendingLogScrollRef.current = requestedView === "threads" ? null : element
      ? { previousHeight: element.scrollHeight, previousTop: element.scrollTop }
      : null;
    setAgentLog((current) => ({ ...current, loadingOlder: true, error: null }));
    try {
      const payload = await loadAgentLogPage(selectedAgent, {
        limit: LOG_PAGE_LIMIT,
        offset: agentLog.loadedLines,
        query: agentLog.appliedQuery || undefined,
        view: requestedView,
      });
      const returnedLines = responseReturnedLines(payload);
      setAgentLog((current) => {
        const loadedLines = current.loadedLines + returnedLines;
        logLoadedLinesRef.current = Math.max(LOG_PAGE_LIMIT, loadedLines);
        return {
          ...current,
          text: requestedView === "threads"
            ? appendLogText(current.text, payload.text)
            : appendLogText(payload.text, current.text),
          loading: false,
          loadingOlder: false,
          error: null,
          loadedLines,
          returnedLines,
          totalLines: payload.total_lines ?? current.totalLines,
          startLine: payload.start_line ?? current.startLine,
          hasMoreBefore: Boolean(payload.has_more_before) && returnedLines > 0,
          updatedAt: new Date().toISOString(),
        };
      });
    } catch (reason: unknown) {
      const message = reason instanceof Error ? reason.message : String(reason);
      pendingLogScrollRef.current = null;
      setAgentLog((current) => ({
        ...current,
        loadingOlder: false,
        error: message,
        updatedAt: new Date().toISOString(),
      }));
    }
  }

  async function runAgentAction(action: string, body: Record<string, unknown> = {}) {
    if (!selectedAgent) return;
    setBusyAction(action);
    try {
      const query = new URLSearchParams({ backend: selectedAgent.backend });
      const payload = await fetchJson<AgentActionResponse>(
        `/api/admin/agents/${encodeURIComponent(selectedAgent.agent_id)}/actions/${encodeURIComponent(action)}?${query.toString()}`,
        {
          method: "POST",
          body: JSON.stringify(body),
        },
      );
      const message = `${selectedAgent.target_name}: ${payload.message}`;
      setActionLog((current) => [{ at: new Date().toISOString(), message, tone: "info" as const }, ...current].slice(0, 30));
      setStatus(payload.message);
      setError(null);
      if (action === "prompt-agent") {
        setAgentPrompt("");
        setShowPromptForm(false);
      }
      if (action === "resume-agent") {
        setAgentPrompt("");
        setAgentPromptResumeThreadId("");
        setShowPromptForm(false);
      }
      await refreshLiveAgents();
      setSelectedKey(agentKey(selectedAgent));
    } catch (reason: unknown) {
      const message = reason instanceof Error ? reason.message : String(reason);
      setError(message);
      setStatus(null);
      setActionLog((current) => [{ at: new Date().toISOString(), message: `${selectedAgent.target_name}: ${message}`, tone: "error" as const }, ...current].slice(0, 30));
    } finally {
      setBusyAction(null);
    }
  }

  async function runContainerExecutionAction(execution: AgentExecutionRecord, action: "stop") {
    if (!selectedAgent) return;
    const actionKey = `${action}:${execution.execution_id}`;
    setBusyAction(actionKey);
    try {
      const query = new URLSearchParams({ backend: "container_worker_vm" });
      const payload = await fetchJson<AgentActionResponse>(
        `/api/admin/agents/${encodeURIComponent(execution.execution_id)}/actions/${encodeURIComponent(action)}?${query.toString()}`,
        {
          method: "POST",
          body: JSON.stringify({}),
        },
      );
      const message = `${execution.execution_id}: ${payload.message}`;
      setActionLog((current) => [{ at: new Date().toISOString(), message, tone: "info" as const }, ...current].slice(0, 30));
      setStatus(payload.message);
      setError(null);
      await refreshLiveAgents();
      setSelectedKey(agentKey(selectedAgent));
    } catch (reason: unknown) {
      const message = reason instanceof Error ? reason.message : String(reason);
      setError(message);
      setStatus(null);
      setActionLog((current) => [{ at: new Date().toISOString(), message: `${execution.execution_id}: ${message}`, tone: "error" as const }, ...current].slice(0, 30));
    } finally {
      setBusyAction(null);
    }
  }

  async function submitOperatorSteeringNote() {
    if (!selectedAgent) return;
    const message = operatorSteeringDraft.trim();
    if (!message) {
      setOperatorSteeringError("Steering note is required.");
      return;
    }
    if (!operatorSteeringContext) {
      setOperatorSteeringError("No active model dispatch is available for this session.");
      return;
    }
    if (!canControlWorkflowRuns) {
      setOperatorSteeringError("Permission required: workflow_runs.control");
      return;
    }
    if (!privilegedUnlocked) {
      setOperatorSteeringError("Privileged operations are locked.");
      return;
    }
    setOperatorSteeringSubmitting(true);
    setOperatorSteeringError(null);
    try {
      const payload = await fetchJson<OperatorSteeringNoteMutationResponse>(
        "/api/operator-steering/notes",
        {
          method: "POST",
          body: JSON.stringify({
            session_id: operatorSteeringContext.session_id,
            dispatch_id: operatorSteeringContext.dispatch_id,
            worker_execution_id: hostString(selectedAgent.host, "latest_execution_id"),
            message,
          }),
        },
      );
      if (payload.note) {
        setOperatorSteeringNotes((current) => [
          payload.note,
          ...current.filter((note) => note.note_id !== payload.note.note_id),
        ]);
      }
      setOperatorSteeringDraft("");
      const actionMessage = payload.interrupt
        ? `${selectedAgent.target_name}: interrupted ${shortDispatchId(payload.interrupt.interrupted_execution_id)} and resumed as ${shortDispatchId(payload.interrupt.replacement_execution_id)}`
        : `${selectedAgent.target_name}: queued steering note for ${shortDispatchId(payload.note.dispatch_id)}`;
      setActionLog((current) => [{ at: new Date().toISOString(), message: actionMessage, tone: "info" as const }, ...current].slice(0, 30));
      setStatus(payload.interrupt ? "Interrupted agent and sent steering note." : "Queued operator steering note.");
      setError(null);
    } catch (reason: unknown) {
      if (isApiAuthError(reason)) {
        await refreshAuthSession(AUTH_EXPIRED_MESSAGE);
      }
      const errorMessage = reason instanceof Error ? reason.message : String(reason);
      setOperatorSteeringError(errorMessage);
      setActionLog((current) => [{ at: new Date().toISOString(), message: `${selectedAgent.target_name}: ${errorMessage}`, tone: "error" as const }, ...current].slice(0, 30));
    } finally {
      setOperatorSteeringSubmitting(false);
    }
  }

  async function cancelOperatorSteeringNote(note: OperatorSteeringNote) {
    if (!selectedAgent || note.status !== "pending" || !canControlWorkflowRuns || !privilegedUnlocked) return;
    setOperatorSteeringCancellingId(note.note_id);
    setOperatorSteeringError(null);
    try {
      const payload = await fetchJson<OperatorSteeringNoteMutationResponse>(
        `/api/operator-steering/notes/${encodeURIComponent(note.note_id)}/cancel`,
        { method: "POST" },
      );
      setOperatorSteeringNotes((current) => current.map((item) => (
        item.note_id === payload.note.note_id ? payload.note : item
      )));
      const actionMessage = `${selectedAgent.target_name}: cancelled steering note for ${shortDispatchId(payload.note.dispatch_id)}`;
      setActionLog((current) => [{ at: new Date().toISOString(), message: actionMessage, tone: "info" as const }, ...current].slice(0, 30));
      setStatus("Cancelled operator steering note.");
      setError(null);
    } catch (reason: unknown) {
      if (isApiAuthError(reason)) {
        await refreshAuthSession(AUTH_EXPIRED_MESSAGE);
      }
      const errorMessage = reason instanceof Error ? reason.message : String(reason);
      setOperatorSteeringError(errorMessage);
      setActionLog((current) => [{ at: new Date().toISOString(), message: `${selectedAgent.target_name}: ${errorMessage}`, tone: "error" as const }, ...current].slice(0, 30));
    } finally {
      setOperatorSteeringCancellingId(null);
    }
  }

  function ttlIsInvalid(value: string): boolean {
    const trimmed = value.trim();
    return Boolean(trimmed) && optionalNumber(trimmed) == null;
  }

  function toggleCreateSkill(skillName: string, selected: boolean) {
    setCreateForm((current) => {
      const selectedSkills = new Set(current.codexSkills);
      if (selected) {
        selectedSkills.add(skillName);
      } else {
        selectedSkills.delete(skillName);
      }
      return { ...current, codexSkills: Array.from(selectedSkills) };
    });
  }

  function toggleInstallSkill(skillName: string, selected: boolean) {
    setSkillInstallForm((current) => {
      const selectedSkills = new Set(current.codexSkills);
      if (selected) {
        selectedSkills.add(skillName);
      } else {
        selectedSkills.delete(skillName);
      }
      return { ...current, codexSkills: Array.from(selectedSkills) };
    });
  }

  async function handleCreateAgent() {
    if (createForm.backend === "libvirt_vm" && !createForm.vmName.trim()) {
      setInlineValidation("VM name is required.");
      return;
    }
    if (createForm.backend === "container_worker_vm" && !createForm.sessionId.trim()) {
      setInlineValidation("Session id is required for container workers.");
      return;
    }
    if (ttlIsInvalid(createForm.ttlHours)) {
      setInlineValidation("TTL hours must be a number.");
      return;
    }
    setBusyAction("create-agent");
    setInlineValidation("");
    try {
      const payload = await fetchJson<AgentActionResponse>("/api/admin/agents", {
        method: "POST",
        body: JSON.stringify(buildCreatePayload(createForm, selectedDirectoryPath)),
      });
      const message = `${payload.target_name}: ${payload.message}`;
      setActionLog((current) => [{ at: new Date().toISOString(), message, tone: "info" as const }, ...current].slice(0, 30));
      setStatus(payload.message);
      setError(null);
      setShowCreateForm(false);
      setCreateForm(emptyCreateForm());
      await refreshLiveAgents();
      setSelectedKey(`${payload.backend}:${payload.agent_id}`);
    } catch (reason: unknown) {
      const message = reason instanceof Error ? reason.message : String(reason);
      setError(message);
      setStatus(null);
      setInlineValidation(message);
      setActionLog((current) => [{ at: new Date().toISOString(), message: `Create agent: ${message}`, tone: "error" as const }, ...current].slice(0, 30));
    } finally {
      setBusyAction(null);
    }
  }

  function openEditForm() {
    if (!selectedAgent) return;
    const policy = sessionPolicyForAgent(selectedAgent);
    if (!policy) {
      setError("The selected agent does not have an unambiguous linked session to edit.");
      return;
    }
    setEditForm(policyFormFromAgent(selectedAgent));
    setShowEditForm(true);
    setShowPromptForm(false);
    setShowSkillInstallForm(false);
    setInlineValidation("");
  }

  function openSkillInstallForm() {
    if (!selectedAgent) return;
    const policy = sessionPolicyForAgent(selectedAgent);
    if (!policy) {
      setError("The selected agent does not have an unambiguous linked session.");
      return;
    }
    setSkillInstallForm({ codexSkills: installedCodexSkillsForAgent(selectedAgent) });
    setShowSkillInstallForm(true);
    setShowEditForm(false);
    setShowPromptForm(false);
    setInlineValidation("");
  }

  function openPromptForm() {
    if (!selectedAgent) return;
    setAgentPromptResumeThreadId(promptThreadIdForAgent(selectedAgent));
    setShowPromptForm(true);
    setShowEditForm(false);
    setShowSkillInstallForm(false);
    setShowCreateForm(false);
    setInlineValidation("");
  }

  async function handleInstallSkills() {
    if (!selectedAgent) return;
    if (skillInstallForm.codexSkills.length === 0) {
      setInlineValidation("Select at least one skill.");
      return;
    }
    setBusyAction("install-skills");
    setInlineValidation("");
    try {
      const query = new URLSearchParams({ backend: selectedAgent.backend });
      const payload = await fetchJson<AgentActionResponse>(
        `/api/admin/agents/${encodeURIComponent(selectedAgent.agent_id)}/actions/install-skills?${query.toString()}`,
        {
          method: "POST",
          body: JSON.stringify({ codex_skills: skillInstallForm.codexSkills }),
        },
      );
      const message = `${selectedAgent.target_name}: ${payload.message}`;
      setActionLog((current) => [{ at: new Date().toISOString(), message, tone: "info" as const }, ...current].slice(0, 30));
      setStatus(payload.message);
      setError(null);
      setShowSkillInstallForm(false);
      await refreshLiveAgents();
      setSelectedKey(agentKey(selectedAgent));
    } catch (reason: unknown) {
      const message = reason instanceof Error ? reason.message : String(reason);
      setError(message);
      setStatus(null);
      setInlineValidation(message);
      setActionLog((current) => [{ at: new Date().toISOString(), message: `${selectedAgent.target_name}: ${message}`, tone: "error" as const }, ...current].slice(0, 30));
    } finally {
      setBusyAction(null);
    }
  }

  async function handleEditAgent() {
    if (!selectedAgent) return;
    if (ttlIsInvalid(editForm.ttlHours)) {
      setInlineValidation("TTL hours must be a number.");
      return;
    }
    setBusyAction("edit-agent");
    setInlineValidation("");
    try {
      const query = new URLSearchParams({ backend: selectedAgent.backend });
      const payload = await fetchJson<AgentActionResponse>(
        `/api/admin/agents/${encodeURIComponent(selectedAgent.agent_id)}?${query.toString()}`,
        {
          method: "PATCH",
          body: JSON.stringify(buildPolicyPayload(editForm)),
        },
      );
      const message = `${selectedAgent.target_name}: ${payload.message}`;
      setActionLog((current) => [{ at: new Date().toISOString(), message, tone: "info" as const }, ...current].slice(0, 30));
      setStatus(payload.message);
      setError(null);
      setShowEditForm(false);
      await refreshLiveAgents();
      setSelectedKey(agentKey(selectedAgent));
    } catch (reason: unknown) {
      const message = reason instanceof Error ? reason.message : String(reason);
      setError(message);
      setStatus(null);
      setInlineValidation(message);
      setActionLog((current) => [{ at: new Date().toISOString(), message: `${selectedAgent.target_name}: ${message}`, tone: "error" as const }, ...current].slice(0, 30));
    } finally {
      setBusyAction(null);
    }
  }

  async function handleDeleteAgent() {
    if (!selectedAgent) return;
    setBusyAction("delete-agent");
    try {
      const query = new URLSearchParams({ backend: selectedAgent.backend });
      const payload = await fetchJson<AgentActionResponse>(
        `/api/admin/agents/${encodeURIComponent(selectedAgent.agent_id)}?${query.toString()}`,
        { method: "DELETE" },
      );
      const message = `${selectedAgent.target_name}: ${payload.message}`;
      setActionLog((current) => [{ at: new Date().toISOString(), message, tone: "info" as const }, ...current].slice(0, 30));
      setStatus(payload.message);
      setError(null);
      setShowEditForm(false);
      await refreshLiveAgents();
    } catch (reason: unknown) {
      const message = reason instanceof Error ? reason.message : String(reason);
      setError(message);
      setStatus(null);
      setActionLog((current) => [{ at: new Date().toISOString(), message: `${selectedAgent.target_name}: ${message}`, tone: "error" as const }, ...current].slice(0, 30));
    } finally {
      setBusyAction(null);
    }
  }

  const guestAgent = hostRecord(selectedAgent?.host, "agent");
  const agentState = hostString(guestAgent, "state") ?? "unknown";
  const agentPid = hostNumber(guestAgent, "pid");
  const powerState = hostString(selectedAgent?.host, "power_state") ?? selectedAgent?.status ?? "unknown";
  const selectedRuntimeImage = agentRuntimeImage(selectedAgent);
  const selectedMetadata = metadataForAgent(selectedAgent);
  const selectedLabels = labelsForAgent(selectedAgent);
  const selectedExecutions = hostExecutionList(selectedAgent?.host, "executions");
  const selectedPromptQueue = promptQueueForAgent(selectedAgent);
  const canMutate = busyAction === null && privilegedUnlocked;
  const showStart = selectedAgent?.backend === "libvirt_vm" && selectedAgent.capabilities.start && powerState !== "running";
  const showShutdown = selectedAgent?.backend === "libvirt_vm" && selectedAgent.capabilities.shutdown && powerState === "running";
  const showEnable = selectedAgent?.backend === "libvirt_vm" && selectedAgent.capabilities.enable_disable && agentState === "disabled";
  const showDisable = selectedAgent?.backend === "libvirt_vm" && selectedAgent.capabilities.enable_disable && agentState !== "disabled";
  const showKill = selectedAgent?.backend === "libvirt_vm" && selectedAgent.capabilities.kill && agentState === "running" && agentPid != null;
  const canPromptAgent =
    selectedAgent != null &&
    agentState !== "disabled" &&
    (selectedAgent.backend === "container_worker_vm" || selectedAgent.capabilities.resume);
  const showContainerStop = selectedAgent?.backend === "container_worker_vm" && selectedAgent.capabilities.stop;
  const showDestroy = Boolean(selectedAgent?.capabilities.destroy);
  const canEditPolicy = Boolean(selectedAgentPolicy);
  const canInstallSkills = canEditPolicy && skillOptions.length > 0;
  const createProviderOptions = (createOptions?.agent_providers ?? []).filter((option) =>
    option.execution_backends.includes(createForm.backend),
  );
  const operatorSteeringCanSubmit = Boolean(
    operatorSteeringContext &&
    canControlWorkflowRuns &&
    privilegedUnlocked &&
    operatorSteeringDraft.trim() &&
    !operatorSteeringSubmitting,
  );
  const operatorSteeringInitialLoading = operatorSteeringLoading && operatorSteeringContext === null;
  const operatorSteeringMeta = operatorSteeringContext
    ? shortDispatchId(operatorSteeringContext.dispatch_id)
    : operatorSteeringLoading
      ? "Loading"
      : "No active dispatch";
  const logTitle = selectedAgent?.backend === "container_worker_vm"
    ? selectedExecutions.length > 0
      ? `${selectedExecutions.length} execution${selectedExecutions.length === 1 ? "" : "s"}`
      : "Container session"
    : hostString(guestAgent, "preferred_repo_name") ?? "Agent output";
  const logRangeLabel = agentLog.totalLines != null && agentLog.startLine != null && agentLog.endLine != null
    ? `${agentLog.startLine}-${agentLog.endLine} of ${agentLog.totalLines}`
    : `${agentLog.loadedLines || agentLog.returnedLines || 0} visible`;

  function renderOperatorSteeringPanel() {
    return (
      <div className="operator-steering-panel">
        {!operatorSteeringSessionId ? (
          <div className="empty-state compact-empty-state">No linked session.</div>
        ) : !canReadWorkflowRuns ? (
          <div className="empty-state compact-empty-state">Permission required: workflow_runs.read.</div>
        ) : operatorSteeringInitialLoading ? (
          <div className="empty-state compact-empty-state">Loading steering context.</div>
        ) : operatorSteeringContext ? (
          <>
            <dl className="property-grid agent-steering-context-grid">
              <div>
                <dt>Session</dt>
                <dd>{operatorSteeringContext.session_id}</dd>
              </div>
              <div>
                <dt>Dispatch</dt>
                <dd>{operatorSteeringContext.dispatch_id}</dd>
              </div>
              <div>
                <dt>Model</dt>
                <dd>{operatorSteeringContext.model ?? "Unknown"}</dd>
              </div>
              <div>
                <dt>Expires</dt>
                <dd>{formatTimestamp(operatorSteeringContext.expires_at)}</dd>
              </div>
            </dl>
            <label className="field-stack operator-steering-form">
              <span>{selectedAgent?.backend === "container_worker_vm" ? "Steering instruction" : "Steering note"}</span>
              <textarea
                rows={3}
                maxLength={2000}
                value={operatorSteeringDraft}
                placeholder="Move from exploration to implementation."
                disabled={!canControlWorkflowRuns || !privilegedUnlocked || operatorSteeringSubmitting}
                onChange={(event) => {
                  setOperatorSteeringDraft(event.target.value);
                  if (operatorSteeringError) setOperatorSteeringError(null);
                }}
              />
            </label>
            <div className="operator-steering-actions">
              <button
                type="button"
                className="admin-action-button primary compact"
                disabled={!operatorSteeringCanSubmit}
                onClick={() => void submitOperatorSteeringNote()}
              >
                {operatorSteeringSubmitting
                  ? (selectedAgent?.backend === "container_worker_vm" ? "Interrupting..." : "Queuing...")
                  : (selectedAgent?.backend === "container_worker_vm" ? "Interrupt and steer" : "Queue note")}
              </button>
              {canControlWorkflowRuns && !privilegedUnlocked ? (
                <span className="operator-steering-lock">Privileged operations locked</span>
              ) : null}
              {!canControlWorkflowRuns ? (
                <span className="operator-steering-lock">Permission required: workflow_runs.control</span>
              ) : null}
            </div>
          </>
        ) : (
          <div className="empty-state compact-empty-state">No active model dispatch for this session.</div>
        )}
        {operatorSteeringError ? <div className="runtime-run-error">{operatorSteeringError}</div> : null}
        {operatorSteeringNotes.length > 0 ? (
          <div className="operator-steering-notes">
            {operatorSteeringNotes.slice(0, 4).map((note) => {
              const cancelling = operatorSteeringCancellingId === note.note_id;
              return (
                <div className={`operator-steering-note status-${note.status}`} key={note.note_id}>
                  <span>{operatorSteeringNoteSummary(note)}</span>
                  {note.provider ? <span>{note.provider}{note.provider_endpoint ? ` / ${note.provider_endpoint}` : ""}</span> : null}
                  {note.error ? <span className="missing">{note.error}</span> : null}
                  {note.status === "pending" && canControlWorkflowRuns ? (
                    <button
                      type="button"
                      className="admin-action-button compact"
                      disabled={!privilegedUnlocked || cancelling}
                      onClick={() => void cancelOperatorSteeringNote(note)}
                    >
                      {cancelling ? "Cancelling..." : "Cancel"}
                    </button>
                  ) : null}
                </div>
              );
            })}
          </div>
        ) : null}
      </div>
    );
  }

  return (
    <div className="feature-page agents-page">
      <section className="feature-sidebar">
        <div className="feature-sidebar-header">
          <div>
            <div className="eyebrow">Agent sessions</div>
            <h1>Agents</h1>
          </div>
          <div className="inline-actions compact-actions">
            <button className="admin-action-button compact" type="button" onClick={() => void handleManualRefresh()} disabled={loading}>
              Refresh
            </button>
            <button
              type="button"
              className={`admin-action-button compact${showCreateForm ? "" : " primary"}`}
              onClick={() => {
                setCreateForm(emptyCreateForm());
                setShowCreateForm(true);
                setShowEditForm(false);
                setShowSkillInstallForm(false);
                setInlineValidation("");
              }}
              disabled={busyAction !== null || !privilegedUnlocked}
            >
              Create
            </button>
          </div>
        </div>
        {!privilegedUnlocked ? (
          <div className="callout warning-callout">Unlock privileged operations to create, edit, stop, destroy, or resume targets.</div>
        ) : null}
        <div className="config-field-grid agents-filter-grid">
          <label className="field-stack">
            <span>Backend</span>
            <select value={backendFilter} onChange={(event) => setBackendFilter(event.target.value as BackendFilter)}>
              <option value="all">All backends</option>
              <option value="libvirt_vm">VM agents</option>
              <option value="container_worker_vm">Container agents</option>
            </select>
          </label>
          <label className="field-stack">
            <span>Status</span>
            <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value as StatusFilter)}>
              <option value="all">All</option>
              <option value="active">Active</option>
              <option value="inactive">Inactive</option>
            </select>
          </label>
        </div>
        <div className="feature-list vm-list agent-list">
          {(["libvirt_vm", "container_worker_vm"] as AgentBackend[]).map((backend) => {
            const group = groupedItems[backend];
            if (group.length === 0) return null;
            return (
              <div className="agent-list-section" key={backend}>
                <div className="section-label agent-list-heading">{backendLabel(backend)}</div>
                {group.map((item) => {
                  const runtimeImageSummary = runtimeImageListSummary(agentRuntimeImage(item));
                  const queueSummary = promptQueueForAgent(item);
                  return (
                    <button
                      key={agentKey(item)}
                      type="button"
                      className={`feature-list-item vm-list-item agent-list-item${selectedAgentKey === agentKey(item) ? " selected" : ""}`}
                      onClick={() => {
                        setSelectedKey(agentKey(item));
                        setShowPromptForm(false);
                        setShowEditForm(false);
                        setShowSkillInstallForm(false);
                        setShowOperatorSteeringDialog(false);
                      }}
                    >
                      <div className="feature-list-title-row vm-list-item-row">
                        <strong>{item.target_name || item.agent_id}</strong>
                        <span className={`status-pill vm-status-pill state-${statusClass(item.status)}`}>
                          {item.status}
                        </span>
                      </div>
                      <div className="feature-list-meta agent-list-item-meta">
                        {item.session_id ?? "No session"} / {item.directory_path}
                      </div>
                      {runtimeImageSummary ? (
                        <div className="feature-list-meta agent-list-item-meta">{runtimeImageSummary}</div>
                      ) : null}
                      {queueSummary.record_count > 0 || queueSummary.read_error ? (
                        <div className="feature-list-meta agent-list-item-meta">
                          Prompt queue: {promptQueueSummaryText(queueSummary)}
                        </div>
                      ) : null}
                    </button>
                  );
                })}
              </div>
            );
          })}
          {!loading && filteredItems.length === 0 ? <div className="empty-state">No agents found for the selected filters.</div> : null}
        </div>
      </section>

      <section className="feature-content">
        <div className="feature-toolbar">
          <div>
            <h2>{selectedAgent?.target_name ?? "Select an agent"}</h2>
            <p className="muted-text">
              {selectedAgent
                ? `${backendLabel(selectedAgent.backend)} / ${selectedAgent.directory_path}`
                : "Choose an agent from the left to inspect details and logs."}
            </p>
          </div>
          {selectedAgent ? (
            <div className="feature-toolbar-actions icon-actions">
              <Tooltip content={`Current status: ${selectedAgent.status}.`}>
                <span className={`status-pill state-${statusClass(selectedAgent.status)}`}>
                  {selectedAgent.status}
                </span>
              </Tooltip>
              <Tooltip content="Edit the selected session policy.">
                <button
                  type="button"
                  className={`admin-icon-button${showEditForm ? " primary" : ""}`}
                  onClick={openEditForm}
                  disabled={!canMutate || !canEditPolicy}
                  aria-label="Edit policy"
                >
                  {busyAction === "edit-agent" ? "..." : <EditIcon />}
                </button>
              </Tooltip>
              <Tooltip content="Copy managed Codex skills into the selected session.">
                <button
                  type="button"
                  className={`admin-icon-button${showSkillInstallForm ? " primary" : ""}`}
                  onClick={openSkillInstallForm}
                  disabled={!canMutate || !canInstallSkills}
                  aria-label="Install skills"
                >
                  {busyAction === "install-skills" ? "..." : <SkillIcon />}
                </button>
              </Tooltip>
              {showStart ? (
                <Tooltip content="Start the selected VM.">
                  <button className="admin-icon-button" type="button" onClick={() => void runAgentAction("start")} disabled={!canMutate} aria-label="Start VM">
                    {busyAction === "start" ? "..." : <StartIcon />}
                  </button>
                </Tooltip>
              ) : null}
              {showShutdown ? (
                <Tooltip content="Request a graceful shutdown for the selected VM.">
                  <button
                    className="admin-icon-button"
                    type="button"
                    onClick={() => {
                      if (window.confirm(`Shut down ${selectedAgent.target_name}?`)) {
                        void runAgentAction("shutdown");
                      }
                    }}
                    disabled={!canMutate}
                    aria-label="Shut down VM"
                  >
                    {busyAction === "shutdown" ? "..." : <PowerIcon />}
                  </button>
                </Tooltip>
              ) : null}
              {showContainerStop ? (
                <Tooltip content="Stop the selected worker job.">
                  <button
                    className="admin-icon-button"
                    type="button"
                    onClick={() => {
                      if (window.confirm(`Stop ${selectedAgent.target_name}?`)) {
                        void runAgentAction("stop");
                      }
                    }}
                    disabled={!canMutate}
                    aria-label="Stop worker job"
                  >
                    {busyAction === "stop" ? "..." : <StopIcon />}
                  </button>
                </Tooltip>
              ) : null}
              {showEnable ? (
                <Tooltip content="Enable the guest agent.">
                  <button className="admin-icon-button" type="button" onClick={() => void runAgentAction("enable-agent")} disabled={!canMutate} aria-label="Enable agent">
                    <EnableIcon />
                  </button>
                </Tooltip>
              ) : null}
              {showDisable ? (
                <Tooltip content="Disable the guest agent.">
                  <button
                    className="admin-icon-button"
                    type="button"
                    onClick={() => {
                      if (window.confirm(`Disable the agent for ${selectedAgent.target_name}?`)) {
                        void runAgentAction("disable-agent");
                      }
                    }}
                    disabled={!canMutate}
                    aria-label="Disable agent"
                  >
                    <DisableIcon />
                  </button>
                </Tooltip>
              ) : null}
              {showKill ? (
                <Tooltip content="Force-kill the currently running guest agent process.">
                  <button
                    className="admin-icon-button"
                    type="button"
                    onClick={() => {
                      if (window.confirm(`Kill the running agent on ${selectedAgent.target_name}?`)) {
                        void runAgentAction("kill-agent", { pid: agentPid });
                      }
                    }}
                    disabled={!canMutate}
                    aria-label="Kill agent"
                  >
                    <KillIcon />
                  </button>
                </Tooltip>
              ) : null}
              {canPromptAgent ? (
                <Tooltip content="Prompt agent">
                  <button
                    type="button"
                    className={`admin-icon-button${showPromptForm ? " primary" : ""}`}
                    onClick={() => {
                      if (showPromptForm) {
                        setShowPromptForm(false);
                        return;
                      }
                      openPromptForm();
                    }}
                    disabled={!canMutate}
                    aria-label="Prompt agent"
                  >
                    <ResumeIcon />
                  </button>
                </Tooltip>
              ) : null}
              <Tooltip content="Queue or review operator steering notes.">
                <button
                  type="button"
                  className={`admin-icon-button${showOperatorSteeringDialog ? " primary" : ""}`}
                  onClick={() => {
                    setShowOperatorSteeringDialog(true);
                    void refreshOperatorSteering();
                  }}
                  aria-label="Operator steering"
                >
                  <SteeringIcon />
                </button>
              </Tooltip>
              {showDestroy ? (
                <Tooltip content={selectedAgent.backend === "container_worker_vm" ? "Delete the worker target and linked session." : "Destroy the VM and remove the linked session."}>
                  <button
                    type="button"
                    className="admin-icon-button danger"
                    onClick={() => {
                      if (window.confirm(`Destroy ${selectedAgent.target_name}?`)) {
                        void handleDeleteAgent();
                      }
                    }}
                    disabled={!canMutate}
                    aria-label="Destroy target"
                  >
                    {busyAction === "delete-agent" ? "..." : <DestroyIcon />}
                  </button>
                </Tooltip>
              ) : null}
            </div>
          ) : null}
        </div>

        {status ? <div className="callout success-callout">{status}</div> : null}
        {error ? <div className="callout error-callout">{error}</div> : null}

        {showCreateForm ? (
          <div className="modal-backdrop" role="presentation">
            <section className="modal-card agent-crud-modal" role="dialog" aria-modal="true" aria-label="Create agent">
              <div className="modal-header">
                <div>
                  <h2>Create agent</h2>
                </div>
                <button className="admin-action-button compact" type="button" onClick={() => setShowCreateForm(false)} disabled={busyAction === "create-agent"}>
                  Close
                </button>
              </div>
              <div className="modal-body">
                {inlineValidation ? <div className="callout error-callout">{inlineValidation}</div> : null}
                <div className="config-field-grid">
                  <label className="field-stack">
                    <span>Backend</span>
                    <select
                      value={createForm.backend}
                      onChange={(event) => {
                        const nextBackend = event.target.value as AgentBackend;
                        setCreateForm((current) => {
                          const stillValid = (createOptions?.agent_providers ?? []).some(
                            (option) =>
                              option.value === current.agentProvider &&
                              option.execution_backends.includes(nextBackend),
                          );
                          return {
                            ...current,
                            backend: nextBackend,
                            agentProvider:
                              current.agentProvider && stillValid ? current.agentProvider : "",
                          };
                        });
                      }}
                    >
                      <option value="libvirt_vm">VM agent</option>
                      <option value="container_worker_vm">Container worker</option>
                    </select>
                  </label>
                  <label className="field-stack">
                    <span>Agent provider</span>
                    <select
                      value={createForm.agentProvider}
                      onChange={(event) =>
                        setCreateForm((current) => ({ ...current, agentProvider: event.target.value }))
                      }
                    >
                      <option value="">Directory default</option>
                      {createProviderOptions.map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="field-stack">
                    <span>Session</span>
                    <input
                      value={createForm.sessionId}
                      onChange={(event) => setCreateForm((current) => ({ ...current, sessionId: event.target.value }))}
                      placeholder={createForm.backend === "container_worker_vm" ? "Required" : "Optional"}
                    />
                  </label>
                  <label className="field-stack">
                    <span>Model</span>
                    <input
                      value={createForm.model}
                      onChange={(event) => setCreateForm((current) => ({ ...current, model: event.target.value }))}
                      placeholder="Optional"
                    />
                  </label>
                  <label className="field-stack">
                    <span>TTL hours</span>
                    <input
                      inputMode="numeric"
                      value={createForm.ttlHours}
                      onChange={(event) => setCreateForm((current) => ({ ...current, ttlHours: event.target.value }))}
                      placeholder="Default"
                    />
                  </label>
                  {createForm.backend === "libvirt_vm" ? (
                    <>
                      <label className="field-stack">
                        <span>VM name</span>
                        <input
                          value={createForm.vmName}
                          onChange={(event) => setCreateForm((current) => ({ ...current, vmName: event.target.value }))}
                        />
                      </label>
                      <label className="field-stack">
                        <span>Provider</span>
                        <input
                          value={createForm.provider}
                          onChange={(event) => setCreateForm((current) => ({ ...current, provider: event.target.value }))}
                          placeholder="Directory default"
                        />
                      </label>
                      <label className="field-stack">
                        <span>VM user</span>
                        <input
                          value={createForm.vmUser}
                          onChange={(event) => setCreateForm((current) => ({ ...current, vmUser: event.target.value }))}
                        />
                      </label>
                      <label className="field-stack">
                        <span>VM image</span>
                        <select
                          value={createForm.vmImage}
                          onChange={(event) =>
                            setCreateForm((current) => ({ ...current, vmImage: event.target.value }))
                          }
                        >
                          <option value="">
                            {`Directory default (${createOptions?.libvirt.default_template ?? "default"})`}
                          </option>
                          {(createOptions?.libvirt.templates ?? []).map((template) => (
                            <option key={template.alias} value={template.alias}>
                              {template.alias}
                            </option>
                          ))}
                        </select>
                      </label>
                    </>
                  ) : (
                    <>
                      <label className="field-stack">
                        <span>Target name</span>
                        <input
                          value={createForm.targetName}
                          onChange={(event) =>
                            setCreateForm((current) => ({ ...current, targetName: event.target.value }))
                          }
                          placeholder="Optional"
                        />
                      </label>
                      <label className="field-stack">
                        <span>Container image selector</span>
                        {createOptions ? (
                          <select
                            value={createForm.containerImage}
                            onChange={(event) =>
                              setCreateForm((current) => ({ ...current, containerImage: event.target.value }))
                            }
                          >
                            <option value="">
                              {`Default image (${createOptions.container_worker.default_image ?? "default"})`}
                            </option>
                            {createOptions.container_worker.images.map((image) => (
                              <option key={image.alias} value={image.alias}>
                                {image.alias}
                              </option>
                            ))}
                          </select>
                        ) : (
                          <input
                            value={createForm.containerImage}
                            onChange={(event) =>
                              setCreateForm((current) => ({ ...current, containerImage: event.target.value }))
                            }
                            placeholder="Alias or default image"
                          />
                        )}
                      </label>
                    </>
                  )}
                  <label className="field-stack">
                    <span>Labels</span>
                    <textarea
                      rows={4}
                      value={createForm.labels}
                      onChange={(event) => setCreateForm((current) => ({ ...current, labels: event.target.value }))}
                    />
                  </label>
                  <label className="field-stack">
                    <span>Allowed tools</span>
                    <textarea
                      rows={4}
                      value={createForm.allowedTools}
                      onChange={(event) => setCreateForm((current) => ({ ...current, allowedTools: event.target.value }))}
                    />
                  </label>
                  <label className="checkbox-row agent-checkbox-row">
                    <input
                      type="checkbox"
                      checked={createForm.webSearchEnabled}
                      onChange={(event) => setCreateForm((current) => ({ ...current, webSearchEnabled: event.target.checked }))}
                    />
                    <span>Web search</span>
                  </label>
                  <label className="field-stack">
                    <span>Allowed scripts</span>
                    <textarea
                      rows={4}
                      value={createForm.allowedScriptNames}
                      onChange={(event) => setCreateForm((current) => ({ ...current, allowedScriptNames: event.target.value }))}
                    />
                  </label>
                  <label className="field-stack">
                    <span>Discord channels or targets</span>
                    <textarea
                      rows={4}
                      value={createForm.discordChannels}
                      onChange={(event) => setCreateForm((current) => ({ ...current, discordChannels: event.target.value }))}
                      placeholder="channel-id or communication-target-id"
                    />
                  </label>
                  <label className="field-stack">
                    <span>Email mailboxes</span>
                    <textarea
                      rows={4}
                      value={createForm.emailMailboxes}
                      onChange={(event) => setCreateForm((current) => ({ ...current, emailMailboxes: event.target.value }))}
                    />
                  </label>
                  <label className="field-stack">
                    <span>Email folders</span>
                    <textarea
                      rows={4}
                      value={createForm.emailFolders}
                      onChange={(event) => setCreateForm((current) => ({ ...current, emailFolders: event.target.value }))}
                    />
                  </label>
                  {createForm.backend === "libvirt_vm" ? (
                    <>
                      <label className="checkbox-row agent-checkbox-row">
                        <input
                          type="checkbox"
                          checked={createForm.dotnet}
                          onChange={(event) => setCreateForm((current) => ({ ...current, dotnet: event.target.checked }))}
                        />
                        <span>.NET</span>
                      </label>
                      <label className="checkbox-row agent-checkbox-row">
                        <input
                          type="checkbox"
                          checked={createForm.nuget}
                          onChange={(event) => setCreateForm((current) => ({ ...current, nuget: event.target.checked }))}
                        />
                        <span>NuGet</span>
                      </label>
                      <label className="checkbox-row agent-checkbox-row">
                        <input
                          type="checkbox"
                          checked={createForm.mssql}
                          onChange={(event) => setCreateForm((current) => ({ ...current, mssql: event.target.checked }))}
                        />
                        <span>MSSQL</span>
                      </label>
                      <label className="checkbox-row agent-checkbox-row">
                        <input
                          type="checkbox"
                          checked={createForm.playwright}
                          onChange={(event) => setCreateForm((current) => ({ ...current, playwright: event.target.checked }))}
                        />
                        <span>Playwright</span>
                      </label>
                      <label className="field-stack">
                        <span>MSSQL init root</span>
                        <input
                          value={createForm.mssqlInitRoot}
                          onChange={(event) => setCreateForm((current) => ({ ...current, mssqlInitRoot: event.target.value }))}
                          disabled={!createForm.mssql}
                        />
                      </label>
                    </>
                  ) : null}
                  <div className="field-stack field-span-2">
                    <span>Codex skills</span>
                    <div className="checkbox-grid skill-checkbox-grid">
                      {skillOptions.map((skill) => (
                        <label key={skill.name} className="checkbox-row agent-checkbox-row">
                          <input
                            type="checkbox"
                            checked={createForm.codexSkills.includes(skill.name)}
                            onChange={(event) => toggleCreateSkill(skill.name, event.target.checked)}
                          />
                          <span>{skill.name}</span>
                        </label>
                      ))}
                      {skillOptions.length === 0 ? <div className="empty-state">No managed skills.</div> : null}
                    </div>
                  </div>
                </div>
                <div className="inline-actions compact-actions">
                  <button className="admin-action-button primary compact" type="button" disabled={!privilegedUnlocked || busyAction === "create-agent"} onClick={() => void handleCreateAgent()}>
                    {busyAction === "create-agent" ? "Creating..." : "Create"}
                  </button>
                </div>
              </div>
            </section>
          </div>
        ) : null}

        {showEditForm && selectedAgent ? (
          <div className="modal-backdrop" role="presentation">
            <section className="modal-card agent-crud-modal" role="dialog" aria-modal="true" aria-label="Edit agent policy">
              <div className="modal-header">
                <div>
                  <h2>Edit policy</h2>
                  <p>{selectedAgent.session_id ?? selectedAgent.agent_id}</p>
                </div>
                <button className="admin-action-button compact" type="button" onClick={() => setShowEditForm(false)} disabled={busyAction === "edit-agent"}>
                  Close
                </button>
              </div>
              <div className="modal-body">
                {inlineValidation ? <div className="callout error-callout">{inlineValidation}</div> : null}
                <div className="config-field-grid">
                  <label className="field-stack">
                    <span>Model</span>
                    <input
                      value={editForm.model}
                      onChange={(event) => setEditForm((current) => ({ ...current, model: event.target.value }))}
                    />
                  </label>
                  <label className="field-stack">
                    <span>Renew TTL hours</span>
                    <input
                      inputMode="numeric"
                      value={editForm.ttlHours}
                      onChange={(event) => setEditForm((current) => ({ ...current, ttlHours: event.target.value }))}
                      placeholder="Leave blank"
                    />
                  </label>
                  <label className="field-stack">
                    <span>Labels</span>
                    <textarea
                      rows={4}
                      value={editForm.labels}
                      onChange={(event) => setEditForm((current) => ({ ...current, labels: event.target.value }))}
                    />
                  </label>
                  <label className="field-stack">
                    <span>Allowed tools</span>
                    <textarea
                      rows={4}
                      value={editForm.allowedTools}
                      onChange={(event) => setEditForm((current) => ({ ...current, allowedTools: event.target.value }))}
                    />
                  </label>
                  <label className="checkbox-row agent-checkbox-row">
                    <input
                      type="checkbox"
                      checked={editForm.webSearchEnabled}
                      onChange={(event) => setEditForm((current) => ({ ...current, webSearchEnabled: event.target.checked }))}
                    />
                    <span>Web search</span>
                  </label>
                  <label className="field-stack">
                    <span>Allowed scripts</span>
                    <textarea
                      rows={4}
                      value={editForm.allowedScriptNames}
                      onChange={(event) => setEditForm((current) => ({ ...current, allowedScriptNames: event.target.value }))}
                    />
                  </label>
                  <div className="field-stack">
                    <span>Approval notifications</span>
                    <p className="field-description">
                      Notification-only host targets that receive approval requests for this session.
                      These IDs must resolve to notify-capable communication targets in the selected directory.
                    </p>
                    <div className="string-list-selector">
                      <div className="contacts-editor-header">
                        <p className="muted-text">
                          Select notify-capable communication targets from the selected session&apos;s directory.
                        </p>
                        <div className="inline-actions contacts-editor-actions">
                          <button
                            type="button"
                            onClick={() =>
                              setEditForm((current) => {
                                const nextAvailable = selectedAgentApprovalNotificationTargetOptions.find(
                                  (option) => !current.approvalNotificationTargetIds.includes(option.value),
                                );
                                if (!nextAvailable) {
                                  return current;
                                }
                                return {
                                  ...current,
                                  approvalNotificationTargetIds: dedupeStrings([
                                    ...current.approvalNotificationTargetIds,
                                    nextAvailable.value,
                                  ]),
                                };
                              })
                            }
                            disabled={!selectedAgentApprovalNotificationTargetOptions.length}
                          >
                            Add notification target
                          </button>
                        </div>
                      </div>
                      {editForm.approvalNotificationTargetIds.length === 0 ? (
                        <div className="empty-state">
                          {selectedAgentApprovalNotificationTargetOptions.length
                            ? "No approval notification targets selected."
                            : "No notify-capable communication targets are currently available for this session."}
                        </div>
                      ) : null}
                      {editForm.approvalNotificationTargetIds.map((entry, index) => {
                        const rowOptions = dedupeStrings([
                          entry,
                          ...selectedAgentApprovalNotificationTargetOptions
                            .map((option) => option.value)
                            .filter((option) => option === entry || !editForm.approvalNotificationTargetIds.includes(option)),
                        ]);
                        const knownTarget =
                          selectedAgentApprovalNotificationTargetOptions.find((option) => option.value === entry) ?? null;
                        const isUnknownTarget = knownTarget == null;
                        return (
                          <div key={`agent-approval-notification-target-${index}-${entry}`} className="string-list-selector-row">
                            <label className="field-stack">
                              <span>Notification target</span>
                              <select
                                value={entry}
                                onChange={(event) =>
                                  setEditForm((current) => {
                                    const nextEntries = [...current.approvalNotificationTargetIds];
                                    nextEntries[index] = event.target.value;
                                    return {
                                      ...current,
                                      approvalNotificationTargetIds: dedupeStrings(nextEntries),
                                    };
                                  })
                                }
                              >
                                {rowOptions.map((option) => {
                                  const optionLabel =
                                    selectedAgentApprovalNotificationTargetOptions.find(
                                      (knownOption) => knownOption.value === option,
                                    ) ?? null;
                                  return (
                                    <option key={option} value={option}>
                                      {option === entry && isUnknownTarget
                                        ? `${option} (unknown target)`
                                        : optionLabel
                                          ? formatNotificationTargetOptionLabel(optionLabel)
                                          : option}
                                    </option>
                                  );
                                })}
                              </select>
                            </label>
                            <div className="inline-actions compact-actions">
                              <button
                                type="button"
                                className="danger"
                                onClick={() =>
                                  setEditForm((current) => ({
                                    ...current,
                                    approvalNotificationTargetIds: dedupeStrings(
                                      current.approvalNotificationTargetIds.filter(
                                        (_, itemIndex) => itemIndex !== index,
                                      ),
                                    ),
                                  }))
                                }
                              >
                                Remove
                              </button>
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                  <label className="field-stack">
                    <span>Discord channels or targets</span>
                    <textarea
                      rows={4}
                      value={editForm.discordChannels}
                      onChange={(event) => setEditForm((current) => ({ ...current, discordChannels: event.target.value }))}
                      placeholder="channel-id or communication-target-id"
                    />
                  </label>
                  <label className="field-stack">
                    <span>Teams targets</span>
                    <textarea
                      rows={4}
                      value={editForm.teamsChannels}
                      onChange={(event) => setEditForm((current) => ({ ...current, teamsChannels: event.target.value }))}
                      placeholder="communication-target-id, team-id/channel-id, or chat:<chat-id>"
                    />
                  </label>
                  <label className="field-stack">
                    <span>Email mailboxes</span>
                    <textarea
                      rows={4}
                      value={editForm.emailMailboxes}
                      onChange={(event) => setEditForm((current) => ({ ...current, emailMailboxes: event.target.value }))}
                    />
                  </label>
                  <label className="field-stack">
                    <span>Email folders</span>
                    <textarea
                      rows={4}
                      value={editForm.emailFolders}
                      onChange={(event) => setEditForm((current) => ({ ...current, emailFolders: event.target.value }))}
                    />
                  </label>
                </div>
                <div className="inline-actions compact-actions">
                  <button className="admin-action-button primary compact" type="button" disabled={!privilegedUnlocked || busyAction === "edit-agent"} onClick={() => void handleEditAgent()}>
                    {busyAction === "edit-agent" ? "Saving..." : "Save"}
                  </button>
                </div>
              </div>
            </section>
          </div>
        ) : null}

        {showSkillInstallForm && selectedAgent ? (
          <div className="modal-backdrop" role="presentation">
            <section className="modal-card agent-crud-modal" role="dialog" aria-modal="true" aria-label="Install skills">
              <div className="modal-header">
                <div>
                  <h2>Install skills</h2>
                  <p>{selectedAgent.session_id ?? selectedAgent.agent_id}</p>
                </div>
                <button className="admin-action-button compact" type="button" onClick={() => setShowSkillInstallForm(false)} disabled={busyAction === "install-skills"}>
                  Close
                </button>
              </div>
              <div className="modal-body">
                {inlineValidation ? <div className="callout error-callout">{inlineValidation}</div> : null}
                <div className="field-stack">
                  <span>Codex skills</span>
                  <div className="checkbox-grid skill-checkbox-grid">
                    {skillOptions.map((skill) => (
                      <label key={skill.name} className="checkbox-row agent-checkbox-row">
                        <input
                          type="checkbox"
                          checked={skillInstallForm.codexSkills.includes(skill.name)}
                          onChange={(event) => toggleInstallSkill(skill.name, event.target.checked)}
                        />
                        <span>{skill.name}</span>
                      </label>
                    ))}
                    {skillOptions.length === 0 ? <div className="empty-state">No managed skills.</div> : null}
                  </div>
                </div>
                <div className="inline-actions compact-actions">
                  <button
                    className="admin-action-button primary compact"
                    type="button"
                    disabled={!privilegedUnlocked || busyAction === "install-skills" || skillInstallForm.codexSkills.length === 0}
                    onClick={() => void handleInstallSkills()}
                  >
                    {busyAction === "install-skills" ? "Installing..." : "Install"}
                  </button>
                </div>
              </div>
            </section>
          </div>
        ) : null}

        {showPromptForm && selectedAgent ? (
          <div className="modal-backdrop" role="presentation">
            <section className="modal-card agent-crud-modal" role="dialog" aria-modal="true" aria-label="Prompt agent">
              <div className="modal-header">
                <div>
                  <h2>Prompt agent</h2>
                </div>
                <button className="admin-action-button compact" type="button" onClick={() => setShowPromptForm(false)} disabled={busyAction === "prompt-agent"}>
                  Close
                </button>
              </div>
              <div className="modal-body">
                <div className="config-field-grid">
                  <label className="field-stack">
                    <span>Prompt</span>
                    <textarea rows={5} value={agentPrompt} onChange={(event) => setAgentPrompt(event.target.value)} />
                  </label>
                  <label className="field-stack">
                    <span>Resume thread ID</span>
                    <input
                      value={agentPromptResumeThreadId}
                      onChange={(event) => setAgentPromptResumeThreadId(event.target.value)}
                      placeholder="Optional"
                    />
                  </label>
                </div>
                <div className="inline-actions compact-actions">
                  <button
                    className="admin-action-button primary compact"
                    type="button"
                    disabled={!canMutate || !agentPrompt.trim()}
                    onClick={() =>
                      void runAgentAction("prompt-agent", {
                        prompt: agentPrompt.trim(),
                        resume_thread_id: agentPromptResumeThreadId.trim() || null,
                      })
                    }
                  >
                    {busyAction === "prompt-agent" ? "Prompting..." : "Prompt agent"}
                  </button>
                </div>
              </div>
            </section>
          </div>
        ) : null}

        {showOperatorSteeringDialog && selectedAgent ? (
          <div className="modal-backdrop" role="presentation">
            <section
              className="modal-card agent-crud-modal agent-steering-modal"
              role="dialog"
              aria-modal="true"
              aria-labelledby="operator-steering-dialog-title"
            >
              <div className="modal-header">
                <div>
                  <h2 id="operator-steering-dialog-title">Operator steering</h2>
                  <p>{selectedAgent.session_id ?? selectedAgent.agent_id} / {operatorSteeringMeta}</p>
                </div>
                <div className="agent-steering-modal-actions">
                  <button
                    className="admin-action-button compact"
                    type="button"
                    aria-label="Refresh operator steering"
                    onClick={() => void refreshOperatorSteering()}
                    disabled={operatorSteeringLoading || !operatorSteeringSessionId || !canReadWorkflowRuns}
                  >
                    Refresh
                  </button>
                  <button
                    className="admin-action-button compact"
                    type="button"
                    onClick={() => setShowOperatorSteeringDialog(false)}
                  >
                    Close
                  </button>
                </div>
              </div>
              <div className="modal-body">
                {renderOperatorSteeringPanel()}
              </div>
            </section>
          </div>
        ) : null}

        {selectedAgent ? (
          <>
            <div className="feature-grid">
              <article className="panel">
                <div className="panel-header">
                  <h3>Target</h3>
                  <span className="panel-meta">{selectedAgent.backend}</span>
                </div>
                <dl className="property-grid">
                  <div>
                    <dt>Agent id</dt>
                    <dd>{selectedAgent.agent_id}</dd>
                  </div>
                  <div>
                    <dt>Session</dt>
                    <dd>{selectedAgent.session_id ?? "None"}</dd>
                  </div>
                  <div>
                    <dt>Directory</dt>
                    <dd>{selectedAgent.directory_path}</dd>
                  </div>
                  <div>
                    <dt>Prompt queue</dt>
                    <dd>{promptQueueSummaryText(selectedPromptQueue)}</dd>
                  </div>
                  <div>
                    <dt>Updated</dt>
                    <dd>{formatTimestamp(selectedAgent.updated_at)}</dd>
                  </div>
                  <div>
                    <dt>Started</dt>
                    <dd>{formatTimestamp(selectedAgent.started_at)}</dd>
                  </div>
                  <div>
                    <dt>Finished</dt>
                    <dd>{formatTimestamp(selectedAgent.finished_at)}</dd>
                  </div>
                </dl>
              </article>

              <article className="panel">
                <div className="panel-header">
                  <h3>{selectedAgent.backend === "container_worker_vm" ? "Worker session" : "VM host"}</h3>
                  <span className="panel-meta">{selectedAgent.status}</span>
                </div>
                <dl className="property-grid">
                  {selectedAgent.backend === "container_worker_vm" ? (
                    <>
                      <div>
                        <dt>Executions</dt>
                        <dd>{hostNumber(selectedAgent.host, "execution_count") ?? selectedExecutions.length}</dd>
                      </div>
                      <div>
                        <dt>Active runs</dt>
                        <dd>{hostNumber(selectedAgent.host, "active_execution_count") ?? 0}</dd>
                      </div>
                      <div>
                        <dt>Latest execution</dt>
                        <dd>{hostString(selectedAgent.host, "latest_execution_id") ?? "None"}</dd>
                      </div>
                      <div>
                        <dt>Worker URL</dt>
                        <dd>{hostString(selectedAgent.host, "worker_url") ?? "Unknown"}</dd>
                      </div>
                      <div>
                        <dt>Container image</dt>
                        <dd>
                          <Tooltip content={runtimeImageTooltip(selectedRuntimeImage)}>
                            <span>{runtimeImagePrimaryText(selectedRuntimeImage)}</span>
                          </Tooltip>
                        </dd>
                      </div>
                      {selectedRuntimeImage?.image_alias ? (
                        <div>
                          <dt>Container image alias</dt>
                          <dd>{selectedRuntimeImage.image_alias}</dd>
                        </div>
                      ) : null}
                      <div>
                        <dt>Latest status</dt>
                        <dd>{hostString(selectedAgent.host, "last_status") ?? selectedAgent.status}</dd>
                      </div>
                    </>
                  ) : (
                    <>
                      <div>
                        <dt>Power</dt>
                        <dd>{powerState}</dd>
                      </div>
                      <div>
                        <dt>IP address</dt>
                        <dd>{hostString(selectedAgent.host, "ip_address") ?? "Unknown"}</dd>
                      </div>
                      <div>
                        <dt>Agent state</dt>
                        <dd>{agentState}</dd>
                      </div>
                      <div>
                        <dt>Agent pid</dt>
                        <dd>{agentPid ?? "Not running"}</dd>
                      </div>
                      <div>
                        <dt>Repositories</dt>
                        <dd>{hostStringList(guestAgent, "repo_names").join(", ") || "None"}</dd>
                      </div>
                      <div>
                        <dt>Issue</dt>
                        <dd>{hostString(selectedAgent.host, "issue_number") ?? "None"}</dd>
                      </div>
                      {selectedRuntimeImage ? (
                        <>
                          <div>
                            <dt>VM template</dt>
                            <dd>
                              <Tooltip content={runtimeImageTooltip(selectedRuntimeImage)}>
                                <span>{runtimeImagePrimaryText(selectedRuntimeImage)}</span>
                              </Tooltip>
                            </dd>
                          </div>
                          <div>
                            <dt>VM image ref</dt>
                            <dd>
                              <Tooltip content={runtimeImageTooltip(selectedRuntimeImage)}>
                                <span>{selectedRuntimeImage.image_ref ?? "Unavailable"}</span>
                              </Tooltip>
                            </dd>
                          </div>
                        </>
                      ) : null}
                    </>
                  )}
                  {selectedLabels.length > 0 ? (
                    <div className="agent-labels-property field-span-2">
                      <dt>Labels</dt>
                      <dd className="agent-inline-label-list">
                        {selectedLabels.map(([key, value]) => (
                          <span className="agent-inline-label" key={key}>
                            <strong>{key}</strong>
                            <span>{value}</span>
                          </span>
                        ))}
                      </dd>
                    </div>
                  ) : null}
                </dl>
              </article>
            </div>

            {selectedPromptQueue.record_count > 0 || selectedPromptQueue.read_error ? (
              <article className="panel agent-prompt-queue-panel">
                <div className="panel-header">
                  <h3>Prompt queue</h3>
                  <span className="panel-meta">{promptQueueSummaryText(selectedPromptQueue)}</span>
                </div>
                {selectedPromptQueue.read_error ? (
                  <div className="callout error-callout">{selectedPromptQueue.read_error}</div>
                ) : null}
                {selectedPromptQueue.record_count > 0 ? (
                  <>
                    <dl className="property-grid agent-prompt-queue-summary-grid">
                      <div>
                        <dt>Oldest queued</dt>
                        <dd>{formatTimestamp(selectedPromptQueue.oldest_created_at)}</dd>
                      </div>
                      <div>
                        <dt>Newest update</dt>
                        <dd>{formatTimestamp(selectedPromptQueue.newest_updated_at)}</dd>
                      </div>
                    </dl>
                    <div className="agent-prompt-queue-list">
                      {selectedPromptQueue.records.map((record) => (
                        <div className="agent-prompt-queue-row" key={record.queue_key}>
                          <div className="agent-prompt-queue-row-main">
                            <strong>{record.queue_key}</strong>
                            <span>{record.effect_id}</span>
                          </div>
                          <div className="agent-prompt-queue-row-meta">
                            <span>{record.session_id ?? "No session"}</span>
                            <span>{record.target}</span>
                            <span>{formatTimestamp(record.updated_at)}</span>
                            <span>
                              {record.coalesced_count} prompt{record.coalesced_count === 1 ? "" : "s"}
                            </span>
                          </div>
                          {record.prompt_preview ? (
                            <p className="agent-prompt-queue-preview">{record.prompt_preview}</p>
                          ) : null}
                        </div>
                      ))}
                      {selectedPromptQueue.truncated_count > 0 ? (
                        <div className="empty-state compact-empty-state">
                          {selectedPromptQueue.truncated_count} additional queued record{selectedPromptQueue.truncated_count === 1 ? "" : "s"} not shown.
                        </div>
                      ) : null}
                    </div>
                  </>
                ) : null}
              </article>
            ) : null}

            {selectedMetadata ? (
              <details className="panel compact-disclosure">
                <summary className="panel-header compact-disclosure-summary">
                  <h3>Metadata</h3>
                  <span className="panel-meta">Worker-supplied context</span>
                </summary>
                <pre className="log-view compact-log-view">{JSON.stringify(selectedMetadata, null, 2)}</pre>
              </details>
            ) : null}

            {selectedAgent.backend === "container_worker_vm" ? (
              <details className="panel compact-disclosure">
                <summary className="panel-header compact-disclosure-summary">
                  <h3>Executions</h3>
                  <span className="panel-meta">
                    {selectedExecutions.length} run{selectedExecutions.length === 1 ? "" : "s"}
                  </span>
                </summary>
                {selectedExecutions.length > 0 ? (
                  <div className="agent-execution-list">
                    {selectedExecutions.map((execution) => {
                      const executionCanStop = isActiveStatus(execution.status);
                      return (
                        <div className="agent-execution-row" key={execution.execution_id}>
                          <div>
                            <strong>{execution.execution_id}</strong>
                            <span>{execution.worker_job_id}</span>
                          </div>
                          <div className="agent-execution-status-actions">
                            <span className={`status-pill state-${statusClass(execution.status)}`}>
                              {execution.status}
                            </span>
                            {executionCanStop ? (
                              <Tooltip content="Stop this running container execution.">
                                <button
                                  type="button"
                                  className="admin-icon-button danger agent-execution-stop-button"
                                  onClick={() => {
                                    if (window.confirm(`Stop container execution ${execution.execution_id}?`)) {
                                      void runContainerExecutionAction(execution, "stop");
                                    }
                                  }}
                                  disabled={!canMutate}
                                  aria-label={`Stop container execution ${execution.execution_id}`}
                                >
                                  {busyAction === `stop:${execution.execution_id}` ? "..." : <StopIcon />}
                                </button>
                              </Tooltip>
                            ) : null}
                          </div>
                          <div className="agent-execution-meta">
                            <span>{formatTimestamp(execution.started_at ?? execution.created_at)}</span>
                            <span>{execution.image ?? "Image unknown"}</span>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                ) : (
                  <div className="empty-state compact-empty-state">No executions have run for this session.</div>
                )}
              </details>
            ) : null}

            {actionLog.length > 0 ? (
              <details className="panel compact-disclosure">
                <summary className="panel-header compact-disclosure-summary">
                  <h3>Recent actions</h3>
                  <span className="panel-meta">{actionLog.length} recent</span>
                </summary>
                <pre className="log-view compact-log-view">
                  {actionLog.map((entry) => `[${formatTimestamp(entry.at)}] ${entry.message}`).join("\n")}
                </pre>
              </details>
            ) : null}

            <article className="panel">
              <div className="panel-header agent-log-header">
                <div>
                  <h3>Logs</h3>
                  <span className="panel-meta">
                    {logTitle} / {logRangeLabel}
                    {agentLog.pendingLiveUpdate ? " / live update waiting" : ""}
                  </span>
                </div>
                <form
                  className="agent-log-search"
                  onSubmit={(event) => {
                    event.preventDefault();
                    reloadLog(logSearchInput);
                  }}
                >
                  <input
                    value={logSearchInput}
                    onChange={(event) => setLogSearchInput(event.target.value)}
                    placeholder="Search logs"
                    aria-label="Search logs"
                  />
                  <button className="admin-action-button compact" type="submit" disabled={agentLog.loading}>
                    Search
                  </button>
                  {agentLog.appliedQuery ? (
                    <button
                      className="admin-action-button compact"
                      type="button"
                      onClick={() => {
                        setLogSearchInput("");
                        reloadLog("");
                      }}
                      disabled={agentLog.loading}
                    >
                      Clear
                    </button>
                  ) : null}
                </form>
              </div>
              <div className="agent-log-controls">
                <button
                  className="admin-action-button compact"
                  type="button"
                  onClick={() => void loadOlderLogLines()}
                  disabled={agentLog.loading || agentLog.loadingOlder || !agentLog.hasMoreBefore}
                >
                  {agentLog.loadingOlder ? "Loading..." : activeLogView === "threads" ? "Load more" : "Load older"}
                </button>
                <button
                  className="admin-action-button compact"
                  type="button"
                  onClick={() => {
                    setLogSearchInput("");
                    reloadLog("");
                  }}
                  disabled={agentLog.loading && !agentLog.text}
                >
                  Latest
                </button>
                {agentLog.loading ? <span className="panel-meta">Loading...</span> : null}
                {agentLog.error ? <span className="panel-meta error-text">{agentLog.error}</span> : null}
              </div>
              {selectedAgent?.backend === "container_worker_vm" ? (
                <div className="agent-log-tabs" role="tablist" aria-label="Log view">
                  <button
                    type="button"
                    role="tab"
                    className={activeLogView === "executions" ? "active" : ""}
                    aria-selected={activeLogView === "executions"}
                    onClick={() => changeLogView("executions")}
                    disabled={agentLog.loading && activeLogView === "executions"}
                  >
                    Executions
                  </button>
                  <button
                    type="button"
                    role="tab"
                    className={activeLogView === "threads" ? "active" : ""}
                    aria-selected={activeLogView === "threads"}
                    onClick={() => changeLogView("threads")}
                    disabled={agentLog.loading && activeLogView === "threads"}
                  >
                    Threads
                  </button>
                </div>
              ) : null}
              <pre
                ref={logViewRef}
                className={`log-view vm-agent-log ${selectedAgent?.backend === "container_worker_vm" ? "has-log-tabs" : ""}`}
                onScroll={(event) => {
                  const atBottom = isScrolledToBottom(event.currentTarget);
                  logSticksToBottomRef.current = atBottom;
                  if (atBottom && agentLog.pendingLiveUpdate && !agentLog.loading) {
                    pendingLogScrollRef.current = "bottom";
                    setAgentLog((current) => ({ ...current, pendingLiveUpdate: false }));
                  }
                }}
              >
                {agentLog.text}
              </pre>
            </article>
          </>
        ) : (
          <div className="empty-state">Select an agent to inspect details and logs.</div>
        )}
      </section>
    </div>
  );
}

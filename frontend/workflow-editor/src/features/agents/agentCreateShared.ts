import { fetchJson } from "../admin/api";
import type {
  AgentBackend,
  AgentCreateOperation,
  AgentCreateOperationListResponse,
  AgentCreateOperationResponse,
  AgentCreateOptionsResponse,
  AgentCreateRequest,
} from "../admin/types";

export type AgentCreateFormState = {
  backend: AgentBackend;
  vmName: string;
  targetName: string;
  sessionId: string;
  ttlDuration: string;
  model: string;
  provider: string;
  vmUser: string;
  agentProvider: string;
  modelProviderMode: string;
  vmImage: string;
  containerImage: string;
  dotnet: boolean;
  nuget: boolean;
  mssql: boolean;
  mssqlInitRoot: string;
  playwright: boolean;
  initialPolicy: AgentInitialPolicyFormState;
};

export type AgentInitialPolicyFormState = {
  labels: Record<string, string>;
  webSearchEnabled: boolean;
  allowedTools: string[];
  allowedScriptNames: string[];
  communicationTargetIds: string[];
};

export type AgentCreateSubmitMeta = {
  form: AgentCreateFormState;
  targetLabel: string;
};

export function emptyCreateForm(): AgentCreateFormState {
  return {
    backend: "libvirt_vm",
    vmName: "",
    targetName: "",
    sessionId: "",
    ttlDuration: "7d",
    model: "",
    provider: "",
    vmUser: "agent",
    agentProvider: "",
    modelProviderMode: "",
    vmImage: "",
    containerImage: "",
    dotnet: false,
    nuget: false,
    mssql: false,
    mssqlInitRoot: "",
    playwright: false,
    initialPolicy: emptyInitialPolicyForm(),
  };
}

export function emptyInitialPolicyForm(): AgentInitialPolicyFormState {
  return {
    labels: {},
    webSearchEnabled: false,
    allowedTools: [],
    allowedScriptNames: [],
    communicationTargetIds: [],
  };
}

export function dedupeStrings(values: string[]): string[] {
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

export function optionalText(value: string): string | null {
  return value.trim() || null;
}

export function optionalNumber(value: string): number | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) ? parsed : null;
}

export function parseDurationSeconds(value: string): number | null {
  const match = value
    .trim()
    .toLowerCase()
    .match(
      /^(\d+)\s*(s|sec|secs|second|seconds|m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days|w|week|weeks)$/,
    );
  if (!match) return null;
  const amount = Number(match[1]);
  if (!Number.isInteger(amount) || amount <= 0) return null;
  const unitSeconds: Record<string, number> = {
    s: 1,
    sec: 1,
    secs: 1,
    second: 1,
    seconds: 1,
    m: 60,
    min: 60,
    mins: 60,
    minute: 60,
    minutes: 60,
    h: 60 * 60,
    hr: 60 * 60,
    hrs: 60 * 60,
    hour: 60 * 60,
    hours: 60 * 60,
    d: 24 * 60 * 60,
    day: 24 * 60 * 60,
    days: 24 * 60 * 60,
    w: 7 * 24 * 60 * 60,
    week: 7 * 24 * 60 * 60,
    weeks: 7 * 24 * 60 * 60,
  };
  return amount * unitSeconds[match[2]];
}

export function buildCreatePayload(form: AgentCreateFormState, directoryPath: string): AgentCreateRequest {
  const payload: AgentCreateRequest = {
    backend: form.backend,
    session_id: optionalText(form.sessionId),
    directory_path: optionalText(directoryPath),
  };
  const ttlSeconds = parseDurationSeconds(form.ttlDuration);
  if (ttlSeconds != null) payload.ttl_seconds = ttlSeconds;
  const model = optionalText(form.model);
  if (model) payload.model = model;
  const agentProvider = optionalText(form.agentProvider);
  if (agentProvider) payload.agent_provider = agentProvider;
  const modelProviderMode = optionalText(form.modelProviderMode);
  if (modelProviderMode) payload.model_provider_mode = modelProviderMode;
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
  } else {
    payload.target_name = optionalText(form.targetName);
    payload.container_image = optionalText(form.containerImage);
  }
  const initialPolicy = form.initialPolicy;
  if (Object.keys(initialPolicy.labels).length > 0) {
    payload.labels = initialPolicy.labels;
  }
  if (initialPolicy.webSearchEnabled) {
    payload.web_search_enabled = true;
  }
  const allowedTools = dedupeStrings(initialPolicy.allowedTools);
  if (allowedTools.length > 0) payload.allowed_tools = allowedTools;
  const allowedScriptNames = dedupeStrings(initialPolicy.allowedScriptNames);
  if (allowedScriptNames.length > 0) payload.allowed_script_names = allowedScriptNames;
  const communicationTargetIds = dedupeStrings(initialPolicy.communicationTargetIds);
  if (communicationTargetIds.length > 0) payload.communication_target_ids = communicationTargetIds;
  return payload;
}

export function agentCreateTargetLabel(form: AgentCreateFormState): string {
  return (
    form.vmName.trim() ||
    form.targetName.trim() ||
    form.sessionId.trim() ||
    "agent"
  );
}

export async function loadAgentCreateOptions(
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

export async function submitAgentCreateOperation(
  createPayload: AgentCreateRequest,
): Promise<AgentCreateOperation> {
  const payload = await fetchJson<AgentCreateOperationResponse>(
    "/api/admin/agents/create-operations",
    {
      method: "POST",
      body: JSON.stringify(createPayload),
    },
  );
  return payload.operation;
}

export async function loadAgentCreateOperations(directoryPath: string): Promise<AgentCreateOperation[]> {
  const query = new URLSearchParams({ limit: "30" });
  const trimmed = directoryPath.trim();
  if (trimmed) query.set("directory_path", trimmed);
  const payload = await fetchJson<AgentCreateOperationListResponse>(
    `/api/admin/agents/create-operations?${query.toString()}`,
  );
  return payload.items;
}

export async function loadAgentCreateOperation(operationId: string): Promise<AgentCreateOperation> {
  const payload = await fetchJson<AgentCreateOperationResponse>(
    `/api/admin/agents/create-operations/${encodeURIComponent(operationId)}`,
  );
  return payload.operation;
}

export function createOperationIsActive(operation: AgentCreateOperation): boolean {
  return operation.status === "queued" || operation.status === "running";
}

export function createOperationMessage(operation: AgentCreateOperation): string {
  if (operation.status === "succeeded") {
    const message = operation.result?.message;
    return typeof message === "string" && message.trim()
      ? message
      : `Created ${operation.target_name}`;
  }
  if (operation.status === "failed") {
    return operation.error || "Agent create failed.";
  }
  if (operation.status === "running") return "Setting up agent target.";
  return "Waiting to start.";
}

export function createdSessionIdFromOperation(operation: AgentCreateOperation): string | null {
  const direct = operation.session_id?.trim();
  if (direct) return direct;
  const result = operation.result;
  if (!result) return null;
  for (const key of ["session_id", "sessionId"]) {
    const value = result[key];
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  for (const key of ["session", "agent"]) {
    const nested = result[key];
    if (typeof nested !== "object" || nested === null || Array.isArray(nested)) continue;
    const value = (nested as Record<string, unknown>).session_id;
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return null;
}

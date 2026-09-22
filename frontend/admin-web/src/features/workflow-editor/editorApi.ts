import { ApiError } from "../../features/admin/api";

export const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "";

export async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  const payload = (await response.json()) as T & { error?: string };
  if (!response.ok && typeof payload === "object" && payload && "error" in payload) {
    throw new ApiError(String(payload.error), response.status, payload);
  }
  return payload as T;
}

export function workflowCatalogPath(directoryPath: string, workerId = ""): string {
  const params = new URLSearchParams();
  const trimmedDirectoryPath = directoryPath.trim();
  if (trimmedDirectoryPath) params.set("directory_path", trimmedDirectoryPath);
  if (workerId.trim()) params.set("worker_id", workerId.trim());
  const query = params.toString();
  return query ? `/api/workflow-editor/catalog?${query}` : "/api/workflow-editor/catalog";
}

export function workflowEditorDocumentPath(workflowPack?: string | null): string {
  const params = new URLSearchParams();
  const trimmedWorkflowPack = (workflowPack ?? "").trim();
  if (trimmedWorkflowPack) params.set("workflow_pack", trimmedWorkflowPack);
  const query = params.toString();
  return query ? `/api/workflow-editor/document?${query}` : "/api/workflow-editor/document";
}

export function workflowPackHistoryPath(workflowPack?: string | null, cursor?: string | null): string {
  const params = new URLSearchParams();
  const trimmedWorkflowPack = (workflowPack ?? "").trim();
  if (trimmedWorkflowPack) params.set("workflow_pack", trimmedWorkflowPack);
  params.set("limit", "10");
  const trimmedCursor = (cursor ?? "").trim();
  if (trimmedCursor) params.set("cursor", trimmedCursor);
  return `/api/workflow-editor/packs/history?${params.toString()}`;
}

export function workflowPackHistoryDetailPath(changeId: string): string {
  return `/api/workflow-editor/packs/history/${encodeURIComponent(changeId)}`;
}

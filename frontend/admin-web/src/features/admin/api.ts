const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "";

export class ApiError extends Error {
  status: number;
  payload: unknown;

  constructor(message: string, status: number, payload: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.payload = payload;
  }
}

export type ConditionalJsonResult<T> =
  | {
      status: "ok";
      data: T;
      etag: string | null;
    }
  | {
      status: "not_modified";
      etag: string | null;
    };

export function isApiAuthError(error: unknown): boolean {
  return error instanceof ApiError && error.status === 401;
}

export async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  const contentType = response.headers.get("content-type") ?? "";
  let payload: unknown;
  if (contentType.includes("application/json")) {
    payload = (await response.json()) as T & { error?: string };
  } else {
    const text = await response.text();
    throw new Error(text.trim() || `${response.status} ${response.statusText}`);
  }
  if (!response.ok && typeof payload === "object" && payload && "error" in payload) {
    throw new ApiError(String((payload as { error?: string }).error), response.status, payload);
  }
  return payload as T;
}

export async function fetchJsonConditional<T>(
  path: string,
  init?: RequestInit,
  etag?: string | null,
): Promise<ConditionalJsonResult<T>> {
  const headers = {
    "Content-Type": "application/json",
    ...(etag ? { "If-None-Match": etag } : {}),
    ...(init?.headers ?? {}),
  };
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers,
  });
  const responseEtag = response.headers.get("etag");
  if (response.status === 304) {
    return {
      status: "not_modified",
      etag: responseEtag ?? etag ?? null,
    };
  }

  const contentType = response.headers.get("content-type") ?? "";
  let payload: unknown;
  if (contentType.includes("application/json")) {
    payload = (await response.json()) as T & { error?: string };
  } else {
    const text = await response.text();
    throw new Error(text.trim() || `${response.status} ${response.statusText}`);
  }
  if (!response.ok && typeof payload === "object" && payload && "error" in payload) {
    throw new ApiError(String((payload as { error?: string }).error), response.status, payload);
  }
  return {
    status: "ok",
    data: payload as T,
    etag: responseEtag,
  };
}

export function formatTimestamp(value: string | null | undefined): string {
  if (!value) return "Never";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

export function formatRelativeSeconds(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "n/a";
  if (value < 60) return `${value}s`;
  if (value < 3600) return `${Math.round(value / 60)}m`;
  if (value < 86400) return `${Math.round(value / 3600)}h`;
  return `${Math.round(value / 86400)}d`;
}

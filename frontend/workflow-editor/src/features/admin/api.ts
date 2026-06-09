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

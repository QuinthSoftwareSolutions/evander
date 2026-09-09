import type { HttpTargetsPayload } from "../admin/types";

export type HttpTargetDraftEntry = {
  id: string;
  alias: string;
  baseUrl: string;
  metadata: Record<string, unknown>;
  managedWebsiteSession?: string;
};

export type HttpTargetsDraft = {
  entries: HttpTargetDraftEntry[];
  legacyFallback: boolean;
  sourceError: string | null;
};

let nextDraftId = 1;

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

function draftId(alias: string): string {
  const prefix = alias.trim() || "target";
  const id = `${prefix}-${nextDraftId}`;
  nextDraftId += 1;
  return id;
}

export function createHttpTargetDraftEntry(): HttpTargetDraftEntry {
  return {
    id: draftId("target"),
    alias: "",
    baseUrl: "",
    metadata: {},
  };
}

export function metadataWithoutHttpTargets(
  metadata: Record<string, unknown> | null | undefined,
): Record<string, unknown> {
  const next = { ...(metadata ?? {}) };
  delete next.http_targets;
  return next;
}

export function httpTargetsDraftFromMetadata(
  metadata: Record<string, unknown> | null | undefined,
): HttpTargetsDraft {
  const sourceMetadata = metadata ?? {};
  const canonical = sourceMetadata.http_targets;
  let legacyFallback = false;
  let rawTargets: unknown;

  if (canonical !== undefined && canonical !== null) {
    if (!isRecord(canonical)) {
      return {
        entries: [],
        legacyFallback: false,
        sourceError: "HTTP targets metadata must be a JSON object.",
      };
    }
    rawTargets = canonical.targets;
  } else {
    const exampleMes = sourceMetadata.example_app;
    if (isRecord(exampleMes) && exampleMes.environments !== undefined) {
      rawTargets = exampleMes.environments;
      legacyFallback = true;
    }
  }

  if (rawTargets === undefined || rawTargets === null) {
    rawTargets = {};
  }
  if (!isRecord(rawTargets)) {
    return {
      entries: [],
      legacyFallback,
      sourceError: "HTTP targets must contain a targets object.",
    };
  }

  let sourceError: string | null = null;
  const entries = Object.entries(rawTargets).map(([alias, rawTarget]) => {
    if (!isRecord(rawTarget)) {
      sourceError ??= `HTTP target ${alias} must be a JSON object.`;
      return {
        id: draftId(alias),
        alias,
        baseUrl: "",
        metadata: {},
      };
    }
    const rawBaseUrl = rawTarget.base_url ?? rawTarget.allowed_base_url;
    if (typeof rawBaseUrl !== "string") {
      sourceError ??= `HTTP target ${alias} must define base_url.`;
    }
    return {
      id: draftId(alias),
      alias,
      baseUrl: typeof rawBaseUrl === "string" ? rawBaseUrl : "",
      metadata: isRecord(rawTarget.metadata) ? { ...rawTarget.metadata } : {},
      ...(typeof rawTarget.managed_website_session === "string"
        ? { managedWebsiteSession: rawTarget.managed_website_session } : {}),
    };
  });

  return { entries, legacyFallback, sourceError };
}

export function httpTargetsValidationError(
  entries: readonly HttpTargetDraftEntry[],
  sourceError: string | null = null,
): string | null {
  if (sourceError) return sourceError;
  const aliases = new Set<string>();
  for (const entry of entries) {
    const alias = entry.alias.trim();
    if (!alias) return "HTTP target aliases must be non-empty.";
    if (aliases.has(alias)) return `Duplicate HTTP target alias: ${alias}`;
    aliases.add(alias);

    const baseUrl = entry.baseUrl.trim();
    if (!baseUrl) return `HTTP target ${alias} must define an allowed base URL.`;
    try {
      const parsed = new URL(baseUrl);
      if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
        return `HTTP target ${alias} must use an http:// or https:// URL.`;
      }
    } catch {
      return `HTTP target ${alias} must use a valid HTTP(S) URL.`;
    }
  }
  return null;
}

export function httpTargetsPayload(
  entries: readonly HttpTargetDraftEntry[],
): HttpTargetsPayload {
  const error = httpTargetsValidationError(entries);
  if (error) throw new Error(error);
  const targets: HttpTargetsPayload["targets"] = {};
  for (const entry of entries) {
    const metadata = { ...entry.metadata };
    targets[entry.alias.trim()] = {
      base_url: entry.baseUrl.trim().replace(/\/+$/, ""),
      ...(entry.managedWebsiteSession ? { managed_website_session: entry.managedWebsiteSession } : {}),
      ...(Object.keys(metadata).length > 0 ? { metadata } : {}),
    };
  }
  return { targets };
}

export type Question = {type: "choice" | "score" | "boolean"; instructions: unknown; criteria?: unknown};
export type Questions = Record<string, Question>;
export type Profile = {label?: string; description?: string; enabled?: boolean; provider?: string; model?: string; questions?: unknown};
export type ProviderSettings = {enabled?: boolean; default_model?: string; api_key_env?: string; timeout_seconds?: number; max_retries?: number; max_request_bytes?: number; max_response_bytes?: number};
export type ClassificationConfig = {enabled?: boolean; default_provider?: string; providers?: Record<string, ProviderSettings>; profiles?: Record<string, Profile>};
export type ClassificationSnapshot = {revision: string; config: ClassificationConfig; effective_config: ClassificationConfig; parent_config: ClassificationConfig; providers: {id: string; supported_question_types: string[]; credential_configured: boolean}[]};

export function mergeClassification(parent: ClassificationConfig, overlay: ClassificationConfig): ClassificationConfig {
  const providers = {...parent.providers};
  for (const [id, settings] of Object.entries(overlay.providers ?? {})) providers[id] = {...providers[id], ...settings};
  return {...parent, ...overlay, providers, profiles: {...parent.profiles, ...overlay.profiles}};
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

export function isEditableConfig(value: unknown): value is ClassificationConfig {
  if (!isRecord(value)) return false;
  if (value.enabled !== undefined && typeof value.enabled !== "boolean") return false;
  if (value.default_provider !== undefined && typeof value.default_provider !== "string") return false;
  for (const key of ["providers", "profiles"]) {
    if (value[key] === undefined) continue;
    if (!isRecord(value[key])) return false;
    for (const entry of Object.values(value[key])) {
      if (!isRecord(entry)) return false;
      for (const field of ["label", "description", "provider", "model", "default_model", "api_key_env"]) {
        if (entry[field] !== undefined && typeof entry[field] !== "string") return false;
      }
      if (entry.enabled !== undefined && typeof entry.enabled !== "boolean") return false;
      for (const field of ["timeout_seconds", "max_retries", "max_request_bytes", "max_response_bytes"]) {
        if (entry[field] !== undefined && typeof entry[field] !== "number") return false;
      }
    }
  }
  return true;
}

export function nextId(existing: object, prefix: string): string {
  let number = 1;
  while (`${prefix}_${number}` in existing) number += 1;
  return `${prefix}_${number}`;
}

export function renameKey<T>(values: Record<string, T>, from: string, to: string): Record<string, T> {
  if (!to.trim()) throw new Error("IDs cannot be empty.");
  if (to !== from && to in values) throw new Error("This ID is already in use.");
  return Object.fromEntries(Object.entries(values).map(([key, value]) => [key === from ? to : key, value]));
}

export function newQuestion(type: Question["type"]): Question {
  return {type, instructions: "", ...(type === "choice" ? {criteria: {option_a: "", option_b: ""}}
    : type === "score" ? {criteria: ["Low", "High"]} : {})};
}

export function inlineEvaluation(questions: unknown, provider?: string, model?: string) {
  return {questions, profile_id: undefined, provider: provider || undefined, model: model || undefined};
}

export function profileEvaluation(profile_id: string) {
  return {profile_id, questions: undefined, provider: undefined, model: undefined};
}

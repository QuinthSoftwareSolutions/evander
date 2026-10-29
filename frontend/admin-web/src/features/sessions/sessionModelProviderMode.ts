export type SessionModelProviderMode =
  | "openai_public"
  | "local_openai"
  | "azure_openai"
  | "anthropic";

export type SessionModelProviderModeSelection = SessionModelProviderMode | "";

export const SESSION_MODEL_PROVIDER_MODE_OPTIONS: ReadonlyArray<{
  value: SessionModelProviderMode;
  label: string;
}> = [
  { value: "openai_public", label: "Public OpenAI" },
  { value: "local_openai", label: "Local OpenAI" },
  { value: "azure_openai", label: "Azure OpenAI" },
  { value: "anthropic", label: "Anthropic" },
];

export function sessionModelProviderModeFromMetadata(
  metadata: Record<string, unknown> | null | undefined,
): SessionModelProviderModeSelection {
  const value = metadata?.model_provider_mode;
  return SESSION_MODEL_PROVIDER_MODE_OPTIONS.some((option) => option.value === value)
    ? value as SessionModelProviderMode
    : "";
}

export function metadataWithoutSessionModelProviderMode(
  metadata: Record<string, unknown> | null | undefined,
): Record<string, unknown> {
  const next = { ...(metadata ?? {}) };
  delete next.model_provider_mode;
  return next;
}

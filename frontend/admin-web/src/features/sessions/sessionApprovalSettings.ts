export type ApprovalPayloadMode = "exact" | "any";

export type ApprovalGrantSettings = {
  ttl_seconds: number;
  max_uses: number | null;
  payload_mode: ApprovalPayloadMode;
};

export type McpToolPolicyRule = {
  denied?: boolean;
  require_approval?: boolean;
  default_settings?: ApprovalGrantSettings | null;
  max_ttl_seconds?: number | null;
  max_uses?: number | null;
  require_exact_payload?: boolean;
};

export const DEFAULT_APPROVAL_SETTINGS: ApprovalGrantSettings = {
  ttl_seconds: 3600,
  max_uses: 1,
  payload_mode: "exact",
};

export function normalizeApprovalSettings(
  value: ApprovalGrantSettings | null | undefined,
): ApprovalGrantSettings {
  const ttlSeconds = Math.max(
    1,
    Math.trunc(Number(value?.ttl_seconds ?? DEFAULT_APPROVAL_SETTINGS.ttl_seconds)),
  );
  const rawMaxUses = value?.max_uses;
  return {
    ttl_seconds: ttlSeconds,
    max_uses: rawMaxUses == null ? null : Math.max(1, Math.trunc(Number(rawMaxUses))),
    payload_mode: value?.payload_mode === "any" ? "any" : "exact",
  };
}

export function approvalSettingsEqual(
  left: ApprovalGrantSettings,
  right: ApprovalGrantSettings,
): boolean {
  return (
    left.ttl_seconds === right.ttl_seconds &&
    left.max_uses === right.max_uses &&
    left.payload_mode === right.payload_mode
  );
}

export function normalizeApprovalToolSettings(
  value: Record<string, ApprovalGrantSettings> | null | undefined,
): Record<string, ApprovalGrantSettings> {
  const result: Record<string, ApprovalGrantSettings> = {};
  for (const [toolName, settings] of Object.entries(value ?? {})) {
    const normalizedTool = toolName.trim();
    if (!normalizedTool) continue;
    result[normalizedTool] = normalizeApprovalSettings(settings);
  }
  return result;
}

export function parsePositiveInteger(value: string, label: string): number {
  const trimmed = value.trim();
  const parsed = Number(trimmed);
  if (!trimmed || !Number.isInteger(parsed) || parsed < 1) {
    throw new Error(`${label} must be a positive whole number.`);
  }
  return parsed;
}

export function parseNullablePositiveInteger(value: string, label: string): number | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  return parsePositiveInteger(trimmed, label);
}

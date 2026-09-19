export const SMART_SWITCH_FIELDS = ["state", "instructions", "cases", "default_port", "default_description", "provider", "model", "min_probability"] as const;

export function classifierSwitchConfig(value: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(SMART_SWITCH_FIELDS.filter(key => value[key] !== undefined
    && !(["provider", "model", "min_probability"].includes(key) && (value[key] === "" || value[key] === null)))
    .map(key => [key, value[key]]));
}

export function classifierSwitchProblems(value: Record<string, unknown>): string[] {
  const errors: string[] = [];
  const cases = Array.isArray(value.cases) ? value.cases : [];
  if (!cases.length) errors.push("Add at least one branch.");
  const ports = [...cases.map(c => c?.port), value.default_port];
  if (ports.some(p => typeof p !== "string" || !p.trim() || p.trim() === "error" || p.includes("${{") || p.includes("$("))) {
    errors.push("Use nonempty static ports other than error.");
  }
  if (new Set(ports.map(p => typeof p === "string" ? p.trim() : p)).size !== ports.length) errors.push("Branch and default ports must be unique.");
  if (typeof value.instructions !== "string" || !value.instructions.trim()) errors.push("Enter routing instructions.");
  if (cases.some(c => typeof c?.description !== "string" || !c.description.trim())) errors.push("Describe every branch.");
  if (value.default_description !== undefined && (typeof value.default_description !== "string" || !value.default_description.trim())) errors.push("Describe the default branch.");
  const minimum = value.min_probability;
  if (minimum != null && (typeof minimum !== "number" || !Number.isFinite(minimum) || minimum < 0 || minimum > 1)) errors.push("The probability cutoff must be between 0 and 1.");
  return errors;
}

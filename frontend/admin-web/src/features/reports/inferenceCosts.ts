export type CostGroup = {
  provider: string; api_key_name: string | null; category: string | null;
  cost: number | null; priced_events: number; unpriced_events: number;
};
export type CostAccount = { id: string; provider: string; api_key_name: string | null };
export type InferenceCostReport = {
  period_start: string; period_end: string; timezone: string; directory_path: string; currency: string;
  pricing_version: string | null; total_cost: number; estimated_cost: number; reported_cost: number;
  coverage: { priced_events: number; unpriced_events: number; unknown_billing_events: number;
    excluded_subscription_events: number; excluded_local_events: number; partial: boolean };
  groups: CostGroup[]; accounts: CostAccount[];
  daily: { date: string; cost: number; cumulative_cost: number;
    categories: { category: string | null; cost: number }[];
    accounts: { id: string; cost: number; cumulative_cost: number }[] }[];
};

export type InferenceTokenReport = {
  period_start: string; period_end: string; timezone: string; directory_path: string;
  total_tokens: number;
  coverage: { available_events: number; unavailable_events: number; partial: boolean };
  daily: { date: string; total_tokens: number;
    categories: { category: string | null; total_tokens: number }[] }[];
};

export const inferenceTokens = (value: number) => `${value.toLocaleString(undefined, { maximumFractionDigits: 0 })} tokens`;

export const costMoney = (value: number) => new Intl.NumberFormat(undefined, {
  style: "currency", currency: "USD", maximumFractionDigits: value > 0 && value < 0.01 ? 6 : 2,
}).format(value);
export const costCategoryName = (value: string | null) => value ?? "Uncategorized";
export const costAccountName = (value: CostAccount) => `${value.provider} / ${value.api_key_name ?? "Unknown key"}`;
export const costAccountId = (value: Pick<CostGroup, "provider" | "api_key_name">) => JSON.stringify([value.provider, value.api_key_name]);

const COST_PALETTE = ["#0072b2", "#d55e00", "#009e73", "#cc79a7", "#e69f00", "#56b4e9", "#6f4e9c", "#8c564b"];

export function costColors(values: readonly (string | null)[], existing?: Map<string | null, string>): Map<string | null, string> {
  const colors = new Map<string | null, string>(existing ?? [[null, "#88929e"]]);
  const labels = [...new Set(values.filter((value): value is string => value !== null))].sort();
  // Allocate across the whole report so adjacent series cannot collide and
  // selecting or reordering accounts does not change their colors.
  let index = 0;
  const used = new Set(colors.values());
  labels.forEach((label) => {
    if (colors.has(label)) return;
    let color: string;
    do { color = COST_PALETTE[index] ?? `hsl(${(index * 137.508) % 360} 65% 42%)`; index += 1; } while (used.has(color));
    colors.set(label, color); used.add(color);
  });
  return colors;
}

export function shiftCostDate(value: string, days: number): string {
  const date = new Date(`${value}T12:00:00Z`);
  if (!Number.isFinite(date.getTime())) return "";
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

export function browserCostDate(now = new Date()): string {
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

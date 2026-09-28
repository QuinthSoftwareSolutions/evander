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

export const costMoney = (value: number) => new Intl.NumberFormat(undefined, {
  style: "currency", currency: "USD", maximumFractionDigits: value > 0 && value < 0.01 ? 6 : 2,
}).format(value);
export const costCategoryName = (value: string | null) => value ?? "Uncategorized";
export const costAccountName = (value: CostAccount) => `${value.provider} / ${value.api_key_name ?? "Unknown key"}`;
export const costAccountId = (value: Pick<CostGroup, "provider" | "api_key_name">) => JSON.stringify([value.provider, value.api_key_name]);

export function costColor(value: string | null): string {
  if (value === null) return "#88929e";
  let hash = 0;
  for (const character of value) hash = ((hash * 31) + character.codePointAt(0)!) >>> 0;
  return `hsl(${hash % 360} 62% 48%)`;
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

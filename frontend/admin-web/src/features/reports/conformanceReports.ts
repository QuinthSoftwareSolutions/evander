export const CONFORMANCE_REPORT_ENDPOINT = "/api/admin/reports/conformance";

export type ConformanceReportKind = "trigger" | "effect";

export type ConformanceReportState = "pass" | "fail" | "missing" | "invalid" | "stale";

export type ConformanceRequirementCounts = Record<string, number>;

export type ConformanceRequirementFinding = {
  requirement_id: string;
  status: string;
  details?: string | null;
  remediation?: string | null;
  coverage?: string[] | null;
};

export type ConformanceCoverageMap = Record<string, string[]>;

export type ConformanceReportRecord = {
  kind: ConformanceReportKind;
  label: string;
  artifact_path: string;
  state: ConformanceReportState;
  status: string;
  generated_at: string | null;
  requirement_counts: ConformanceRequirementCounts;
  requirement_count: number;
  missing_coverage_count: number;
  missing_coverage: ConformanceCoverageMap;
  traceability_task_count: number;
  failing_requirements: ConformanceRequirementFinding[];
  remediation_snippets: ConformanceRequirementFinding[];
  error: string | null;
  guidance: string | null;
};

export type ConformanceReportsPayload = {
  generated_at: string | null;
  stale_after_seconds: number;
  reports: ConformanceReportRecord[];
};

export type ConformanceReportRunPayload = {
  kind: ConformanceReportKind;
  exit_code: number;
  started_at: string;
  completed_at: string;
  duration_seconds: number;
  output?: string | null;
  reports: ConformanceReportsPayload;
};

export function conformanceReportRunEndpoint(kind: ConformanceReportKind): string {
  return `${CONFORMANCE_REPORT_ENDPOINT}/${kind}/run`;
}

const STATE_LABELS: Record<ConformanceReportState, string> = {
  pass: "Pass",
  fail: "Fail",
  missing: "Missing",
  invalid: "Invalid",
  stale: "Stale",
};

const STATUS_PRIORITY = new Map<string, number>([
  ["pass", 0],
  ["fail", 1],
  ["unknown", 2],
]);

function humanizeStatus(value: string): string {
  return value
    .replace(/[_-]+/g, " ")
    .trim()
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function statusSortKey(value: string): number {
  return STATUS_PRIORITY.get(value) ?? 10;
}

export function stateLabel(state: ConformanceReportState): string {
  return STATE_LABELS[state] ?? humanizeStatus(state);
}

export function stateClassName(state: ConformanceReportState): string {
  return `state-${state}`;
}

export function summarizeRequirementCounts(counts: ConformanceRequirementCounts): string {
  const entries = Object.entries(counts).filter(([, count]) => Number.isFinite(count) && count > 0);
  if (entries.length === 0) {
    return "None";
  }

  entries.sort(([left], [right]) => {
    const leftPriority = statusSortKey(left);
    const rightPriority = statusSortKey(right);
    if (leftPriority !== rightPriority) {
      return leftPriority - rightPriority;
    }
    return left.localeCompare(right);
  });

  return entries
    .map(([status, count]) => `${humanizeStatus(status)} ${count}`)
    .join(", ");
}

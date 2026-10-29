export type TableSortValue = string | number | null | undefined;
export type TableSort = { column: string; direction: "ascending" | "descending" };

const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: "base" });

/** Compare source timestamps, never their localized display strings. */
export function timestampSortValue(value: string | null | undefined): number | null {
  const timestamp = value ? Date.parse(value) : NaN;
  return Number.isFinite(timestamp) ? timestamp : null;
}

function isMissing(value: TableSortValue): boolean {
  return value == null || value === "" || (typeof value === "number" && !Number.isFinite(value));
}

/** Keep the original order for ties and leave the source array untouched. */
export function sortTableRows<T>(
  rows: readonly T[],
  sort: TableSort | null,
  value: (row: T, column: string) => TableSortValue,
): readonly T[] {
  if (!sort) return rows;
  const direction = sort.direction === "ascending" ? 1 : -1;
  return [...rows].sort((left, right) => {
    const a = value(left, sort.column);
    const b = value(right, sort.column);
    // Unavailable values stay at the bottom in either direction.
    if (isMissing(a)) return isMissing(b) ? 0 : 1;
    if (isMissing(b)) return -1;
    const comparison = typeof a === "number" && typeof b === "number"
      ? a - b
      : collator.compare(String(a), String(b));
    return comparison * direction;
  });
}

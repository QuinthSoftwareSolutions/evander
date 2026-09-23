import { useMemo, useState } from "react";
import { ArrowDown, ArrowUp, ArrowUpDown } from "lucide-react";
import { sortTableRows, type TableSort, type TableSortValue } from "./tableSorting";

export function useTableSort<T>(rows: readonly T[], value: (row: T, column: string) => TableSortValue) {
  const [sort, setSort] = useState<TableSort | null>(null);
  const sortedRows = useMemo(() => sortTableRows(rows, sort, value), [rows, sort, value]);
  function toggleSort(column: string) {
    setSort(current => ({
      column,
      direction: current?.column === column && current.direction === "ascending" ? "descending" : "ascending",
    }));
  }
  return { sortedRows, sort, toggleSort };
}

export function SortableTableHeader({ column, label = column, sort, onSort }: {
  column: string;
  label?: string;
  sort: TableSort | null;
  onSort: (column: string) => void;
}) {
  const direction = sort?.column === column ? sort.direction : "none";
  const nextDirection = direction === "ascending" ? "descending" : "ascending";
  const Icon = direction === "ascending" ? ArrowUp : direction === "descending" ? ArrowDown : ArrowUpDown;
  return (
    <th scope="col" aria-sort={direction} className="sortable-column">
      <button
        type="button"
        className="table-sort-button"
        onClick={() => onSort(column)}
        aria-label={`Sort by ${label}, ${nextDirection}`}
      >
        <span>{label}</span>
        <Icon size={14} aria-hidden="true" />
      </button>
    </th>
  );
}

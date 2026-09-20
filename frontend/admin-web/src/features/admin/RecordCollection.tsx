import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { ChevronDown, ChevronRight, Search } from "lucide-react";

type RecordCollectionProps<T> = {
  label: string;
  entries: readonly T[];
  name: (entry: T, index: number) => string;
  description: (entry: T) => string;
  searchText: (entry: T) => string;
  category?: (entry: T) => string;
  categoryLabel?: string;
  metadata?: (entry: T) => ReactNode;
  isNew?: (entry: T) => boolean;
  children: (entry: T, index: number) => ReactNode;
};

/** Filtering and sorting are presentation-only: editors always receive the source index. */
export function RecordCollection<T>({
  label, entries, name, description, searchText, category, categoryLabel = "Provider", metadata, isNew, children,
}: RecordCollectionProps<T>) {
  const id = useId();
  const [query, setQuery] = useState("");
  const [categoryFilter, setCategoryFilter] = useState("");
  const [sort, setSort] = useState("name");
  const [expandedIndex, setExpandedIndex] = useState<number | null>(() => {
    const index = entries.findIndex(entry => isNew?.(entry));
    return index < 0 ? null : index;
  });
  const previousCount = useRef(entries.length);
  const focusNewIndex = useRef<number | null>(null);

  useEffect(() => {
    if (entries.length > previousCount.current && isNew) {
      // Editable collections append drafts; inherited inventories only support inspection.
      // Reveal added drafts even with an active filter.
      setQuery("");
      setCategoryFilter("");
      focusNewIndex.current = entries.length - 1;
      setExpandedIndex(entries.length - 1);
    } else if (entries.length < previousCount.current) {
      focusNewIndex.current = null;
      setExpandedIndex(null);
    }
    previousCount.current = entries.length;
  }, [entries.length]);

  useEffect(() => {
    if (focusNewIndex.current === null || expandedIndex !== focusNewIndex.current) return;
    const editor = document.getElementById(`${id}-${expandedIndex}`);
    const input = editor?.querySelector<HTMLInputElement>("input:not(:disabled)");
    if (input) {
      input.focus();
      focusNewIndex.current = null;
    }
  }, [expandedIndex, entries.length, id]);

  const terms = query.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
  const categories = category ? [...new Set([...entries.map(category), categoryFilter])].filter(Boolean).sort() : [];
  const rows = entries.map((entry, index) => ({ entry, index })).filter(({ entry, index }) => {
    // Keep an editor visible while its name/address is being changed under a filter.
    if (index === expandedIndex) return true;
    const text = `${name(entry, index)} ${description(entry)} ${searchText(entry)}`.toLocaleLowerCase();
    return (!categoryFilter || category?.(entry) === categoryFilter) && terms.every(term => text.includes(term));
  });
  if (sort !== "configured") {
    rows.sort((left, right) => {
      const compare = (a: string, b: string) => a.localeCompare(b, undefined, { numeric: true, sensitivity: "base" });
      if (sort === "provider") {
        const result = compare(category?.(left.entry) ?? "", category?.(right.entry) ?? "");
        if (result) return result;
      }
      return compare(name(left.entry, left.index), name(right.entry, right.index)) * (sort === "descending" ? -1 : 1);
    });
  }

  return (
    <div className="record-collection" aria-label={label}>
      <div className="record-collection-toolbar">
        <label className="record-collection-search">
          <Search aria-hidden="true" size={16} />
          <input type="search" aria-label={`Search ${label.toLowerCase()}`} placeholder={`Search ${label.toLowerCase()}…`}
            value={query} onChange={event => { setQuery(event.target.value); setExpandedIndex(null); }} />
        </label>
        {category ? <label className="record-collection-filter">
          <span>{categoryLabel}</span>
          <select aria-label={`Filter ${label.toLowerCase()} by ${categoryLabel.toLowerCase()}`} value={categoryFilter} onChange={event => { setCategoryFilter(event.target.value); setExpandedIndex(null); }}>
            <option value="">All</option>
            {categories.map(value => <option key={value} value={value}>{value}</option>)}
          </select>
        </label> : null}
        <label className="record-collection-filter">
          <span>Sort</span>
          <select aria-label={`Sort ${label.toLowerCase()}`} value={sort} onChange={event => setSort(event.target.value)}>
            <option value="name">Name A–Z</option>
            <option value="descending">Name Z–A</option>
            {category ? <option value="provider">{categoryLabel}</option> : null}
            <option value="configured">Configured order</option>
          </select>
        </label>
      </div>
      <div className="record-collection-count" role="status">
        {rows.length} of {entries.length} {label.toLowerCase()}
        {query || categoryFilter ? <button type="button" onClick={() => { setQuery(""); setCategoryFilter(""); }}>Clear filters</button> : null}
      </div>
      <div className="record-collection-list">
        {rows.map(({ entry, index }) => {
          const expanded = expandedIndex === index;
          return <div className={`record-collection-item${expanded ? " expanded" : ""}`} key={index}>
            <button type="button" className="record-collection-summary" aria-expanded={expanded}
              aria-controls={`${id}-${index}`} onClick={() => setExpandedIndex(expanded ? null : index)}>
              {expanded ? <ChevronDown size={16} aria-hidden="true" /> : <ChevronRight size={16} aria-hidden="true" />}
              <span className="record-collection-copy">
                <strong>{name(entry, index)}</strong>
                <span>{description(entry)}</span>
              </span>
              {metadata ? <span className="record-collection-meta">{metadata(entry)}</span> : null}
            </button>
            <div id={`${id}-${index}`} hidden={!expanded} className="record-collection-editor">
              {expanded ? children(entry, index) : null}
            </div>
          </div>;
        })}
        {!rows.length ? <div className="empty-state">{entries.length ? "No matches. Try another search or clear the filters." : `No ${label.toLowerCase()} configured.`}</div> : null}
      </div>
    </div>
  );
}

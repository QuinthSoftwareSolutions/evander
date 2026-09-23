import { useEffect, useRef, useState, type ReactNode } from "react";
import { RecordInspector } from "./RecordInspector";
import { readAdminHashQuery } from "./hash";

export type WorkspaceRecord = {
  id: string;
  title: string;
  description?: ReactNode;
  cells: ReactNode[];
  facts: Array<[string, ReactNode]>;
  href?: string;
};

/** Shared collection/inspection/full-record navigation. Both work surfaces retain their state. */
export function RecordWorkspace({
  title,
  description,
  columns,
  records,
  selectedId,
  onSelect,
  filters,
  actions,
  loading,
  error,
  initialRecordOpen = false,
  recordRouteKeys = "",
  inspectorContent,
  children,
}: {
  title: string;
  description: string;
  columns: string[];
  records: WorkspaceRecord[];
  selectedId?: string | null;
  onSelect: (id: string) => void | boolean;
  filters?: ReactNode;
  actions?: (openRecord: () => void) => ReactNode;
  loading?: boolean;
  error?: string | null;
  initialRecordOpen?: boolean;
  recordRouteKeys?: string;
  inspectorContent?: ReactNode;
  children: ReactNode;
}) {
  const [recordOpen, setRecordOpen] = useState(initialRecordOpen && readAdminHashQuery().get("inspect") !== "1");
  const [inspectOpen, setInspectOpen] = useState(readAdminHashQuery().get("inspect") === "1");
  const backRef = useRef<HTMLButtonElement>(null);
  const selectionTriggerRef = useRef<HTMLElement | null>(null);
  const appliedRouteRef = useRef("");
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;
  const [search, setSearch] = useState("");
  const selected = records.find((record) => record.id === selectedId);
  const visibleRecords =
    !filters && search.trim()
      ? records.filter((record) =>
          [record.title, record.id, ...record.facts.map(([, value]) => (typeof value === "string" ? value : ""))]
            .join(" ")
            .toLocaleLowerCase()
            .includes(search.trim().toLocaleLowerCase()),
        )
      : records;

  useEffect(() => {
    const hash = window.location.hash;
    if (appliedRouteRef.current === hash) return;
    const query = readAdminHashQuery(hash);
    const routeKeys = recordRouteKeys.split(",").filter((key) => key && query.has(key));
    if (!routeKeys.length) {
      appliedRouteRef.current = hash;
      return;
    }
    const record = records.find(
      (candidate) =>
        candidate.href && routeKeys.some((key) => readAdminHashQuery(candidate.href).get(key) === query.get(key)),
    );
    if (!record) return;
    appliedRouteRef.current = hash;
    if (record.id !== selectedId) onSelectRef.current(record.id);
  }, [records, recordRouteKeys, selectedId]);

  useEffect(() => {
    const followHash = () => {
      const query = readAdminHashQuery();
      if (recordRouteKeys.split(",").some((key) => key && query.has(key))) {
        setRecordOpen(query.get("inspect") !== "1");
        setInspectOpen(query.get("inspect") === "1");
      }
    };
    window.addEventListener("hashchange", followHash);
    return () => window.removeEventListener("hashchange", followHash);
  }, [recordRouteKeys]);

  function openRecord() {
    setRecordOpen(true);
    if (selected?.href) window.history.replaceState(null, "", selected.href);
    window.requestAnimationFrame(() => backRef.current?.focus());
  }

  function openWorkspace() {
    const query = readAdminHashQuery();
    for (const key of recordRouteKeys.split(",")) query.delete(key);
    query.delete("inspect");
    window.history.replaceState(null, "", `${window.location.hash.split("?")[0]}${query.size ? `?${query}` : ""}`);
    setRecordOpen(true);
    window.requestAnimationFrame(() => backRef.current?.focus());
  }

  return (
    <div className="record-workspace">
      <section className="collection-page" hidden={recordOpen} aria-label={`${title} inventory`}>
        <header className="collection-heading">
          <div>
            <h1>{title}</h1>
            <p>{description}</p>
          </div>
          <div className="inline-actions">{actions?.(openWorkspace)}</div>
        </header>
        <div className="collection-toolbar">
          {filters ?? (
            <label className="field-stack collection-search">
              <span>Search {title.toLowerCase()}</span>
              <input type="search" value={search} onChange={(event) => setSearch(event.target.value)} />
            </label>
          )}
          <span className="panel-meta" role="status">
            {loading && !records.length ? "Loading…" : `${visibleRecords.length} records`}
          </span>
        </div>
        {error && !recordOpen ? (
          <p className="callout error-callout" role="alert">
            {error}
          </p>
        ) : null}
        <div className={`collection-workspace${inspectOpen && selected ? " has-inspector" : ""}`}>
          <div className="collection-scroll">
            <table className="collection-table">
              <caption className="sr-only">{title}</caption>
              <thead>
                <tr>
                  {columns.map((column) => (
                    <th scope="col" key={column}>
                      {column}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {visibleRecords.map((record) => (
                  <tr key={record.id} data-selected={inspectOpen && record.id === selectedId}>
                    <td>
                      <button
                        type="button"
                        className="record-name"
                        onClick={(event) => {
                          if (onSelect(record.id) === false) return;
                          selectionTriggerRef.current = event.currentTarget;
                          setInspectOpen(true);
                          if (record.href)
                            window.history.replaceState(
                              null,
                              "",
                              `${record.href}${record.href.includes("?") ? "&" : "?"}inspect=1`,
                            );
                        }}
                      >
                        {record.title}
                      </button>
                      {record.description ? <small>{record.description}</small> : null}
                    </td>
                    {record.cells.map((cell, index) => (
                      <td key={index}>{cell}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
            {!visibleRecords.length && !loading ? <div className="empty-state">No records match this view.</div> : null}
          </div>
          {inspectOpen && selected && !recordOpen ? (
            <RecordInspector
              key={selected.id}
              title={selected.title}
              subtitle={selected.description}
              returnFocus={() => selectionTriggerRef.current}
              onClose={() => {
                setInspectOpen(false);
                const query = readAdminHashQuery();
                for (const key of recordRouteKeys.split(",")) query.delete(key);
                query.delete("inspect");
                window.history.replaceState(
                  null,
                  "",
                  `${window.location.hash.split("?")[0]}${query.size ? `?${query}` : ""}`,
                );
              }}
              onOpenRecord={openRecord}
            >
              <dl className="property-grid">
                {selected.facts.map(([label, value]) => (
                  <div key={label}>
                    <dt>{label}</dt>
                    <dd>{value ?? "Unavailable"}</dd>
                  </div>
                ))}
              </dl>
              {inspectorContent}
            </RecordInspector>
          ) : null}
        </div>
      </section>
      <div hidden={!recordOpen} className="workspace-full-record">
        <div className="workspace-record-navigation">
          <button
            ref={backRef}
            type="button"
            onClick={() => {
              setRecordOpen(false);
              setInspectOpen(true);
              if (selected?.href)
                window.history.replaceState(
                  null,
                  "",
                  `${selected.href}${selected.href.includes("?") ? "&" : "?"}inspect=1`,
                );
            }}
          >
            ← {title} inventory
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

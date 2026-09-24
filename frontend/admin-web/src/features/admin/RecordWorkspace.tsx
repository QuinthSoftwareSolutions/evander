import { useEffect, useRef, useState, type ReactNode } from "react";
import { readAdminHashQuery } from "./hash";

type WorkspaceRecord = { id: string; href: string };

/** Keep the sidebar selection shareable without an intermediate inventory or inspector. */
export function RecordWorkspace({ records, selectedId, onSelect, recordRouteKeys, allowUnlistedSelection = false, children }: {
  records: WorkspaceRecord[];
  selectedId?: string | null;
  onSelect: (id: string) => void | boolean;
  recordRouteKeys: string;
  allowUnlistedSelection?: boolean;
  children: ReactNode;
}) {
  const [routeHash, setRouteHash] = useState(() => window.location.hash);
  const appliedRouteRef = useRef<string | null>(null);
  const previousSelectedIdRef = useRef(selectedId);
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;

  useEffect(() => {
    const followHash = () => setRouteHash(window.location.hash);
    window.addEventListener("hashchange", followHash);
    return () => window.removeEventListener("hashchange", followHash);
  }, []);

  useEffect(() => {
    const selectionChanged = previousSelectedIdRef.current !== selectedId;
    previousSelectedIdRef.current = selectedId;
    const hash = window.location.hash;
    const query = readAdminHashQuery(hash);
    const keys = recordRouteKeys.split(",");
    const routeKeys = keys.filter((key) => query.has(key));
    const selected = records.find((record) => record.id === selectedId);
    const routed = records.find((record) =>
      routeKeys.some((key) => readAdminHashQuery(record.href).get(key) === query.get(key)),
    );
    const selectionHash = () => {
      for (const key of keys) query.delete(key);
      query.delete("inspect");
      if (selected) {
        for (const [key, value] of readAdminHashQuery(selected.href)) query.set(key, value);
      }
      return `${hash.split("?")[0]}${query.size ? `?${query}` : ""}`;
    };
    const replaceHash = (nextHash: string) => {
      window.history.replaceState(null, "", nextHash);
      appliedRouteRef.current = window.location.hash;
      setRouteHash(window.location.hash);
    };

    if (appliedRouteRef.current !== hash) {
      // Single-key routes can load details even when a record is outside the current list.
      // Composite selections (such as agent/session aliases) need the list to resolve an ID.
      const routeId = routed?.id ?? (allowUnlistedSelection && keys.length === 1 ? query.get(keys[0]) : null);
      if (routeKeys.length && !routeId && !records.length) return;
      appliedRouteRef.current = hash;
      const nextId = routeId ?? (routeKeys.length ? selectedId : "");
      if (nextId !== selectedId && (nextId || selectedId)) {
        if (onSelectRef.current(nextId ?? "") === false) replaceHash(selectionHash());
        return;
      }
    } else if (selectionChanged && (selected || !selectedId)) {
      replaceHash(selectionHash());
    }
    // Old inspector links now open the same detail view directly.
    if (query.has("inspect")) {
      query.delete("inspect");
      replaceHash(`${hash.split("?")[0]}${query.size ? `?${query}` : ""}`);
    }
  }, [records, recordRouteKeys, selectedId, routeHash, allowUnlistedSelection]);

  return (
    <div className="record-workspace">
      <div className="workspace-full-record">{children}</div>
    </div>
  );
}

export function RecordPlaceholder({ children }: { children: ReactNode }) {
  return (
    <article className="record-placeholder">
      <h2>{children}</h2>
      <p>Choose an item from the list to get started.</p>
    </article>
  );
}

import { useEffect, useId, useState } from "react";
import { fetchJson } from "../admin/api";

export function CostCategoryInput({ value, onChange, directoryPath, disabled = false, label = "Cost category" }: {
  value: string; onChange: (value: string) => void; directoryPath: string; disabled?: boolean; label?: string;
}) {
  const id = useId();
  const [suggestions, setSuggestions] = useState<string[]>([]);
  useEffect(() => {
    let active = true;
    setSuggestions([]);
    fetchJson<{ categories: string[] }>(`/api/admin/inference-cost-categories?${new URLSearchParams({ directory_path: directoryPath })}`)
      .then((result) => { if (active) setSuggestions(result.categories); })
      .catch(() => { /* Suggestions are optional; category editing remains available. */ });
    return () => { active = false; };
  }, [directoryPath]);
  return <label className="field-stack">
    <span>{label}</span>
    <input list={id} value={value} onChange={(event) => onChange(event.target.value)} disabled={disabled} placeholder="Uncategorized" />
    <datalist id={id}>{suggestions.map((category) => <option key={category} value={category} />)}</datalist>
  </label>;
}

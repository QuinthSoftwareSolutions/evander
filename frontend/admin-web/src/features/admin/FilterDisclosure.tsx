import { useEffect, useState, type ReactNode } from "react";

/** Keep secondary filters compact, and reveal filters supplied by a record link. */
export function FilterDisclosure({ activeCount, children }: { activeCount: number; children: ReactNode }) {
  const [open, setOpen] = useState(activeCount > 0);
  useEffect(() => {
    if (activeCount > 0) setOpen(true);
  }, [activeCount]);

  return (
    <details className="filter-disclosure" open={open} onToggle={event => setOpen(event.currentTarget.open)}>
      <summary>More filters{activeCount > 0 ? ` · ${activeCount} active` : ""}</summary>
      <div className="filter-disclosure-fields">{children}</div>
    </details>
  );
}

import { useEffect, useId, useRef, useState, type ReactNode } from "react";

/** A non-modal companion to a collection. The collection stays mounted and keeps its scroll. */
export function RecordInspector({
  title,
  subtitle,
  children,
  onClose,
  onOpenRecord,
  returnFocus,
  className = "",
}: {
  title: string;
  subtitle?: ReactNode;
  children: ReactNode;
  onClose: () => void;
  onOpenRecord?: () => void;
  returnFocus?: () => HTMLElement | null;
  className?: string;
}) {
  const titleId = useId();
  const closeRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [narrow, setNarrow] = useState(() => window.matchMedia("(max-width: 900px)").matches);
  const onCloseRef = useRef(onClose);
  const returnFocusRef = useRef(returnFocus);
  onCloseRef.current = onClose;
  returnFocusRef.current = returnFocus;

  useEffect(() => {
    const media = window.matchMedia("(max-width: 900px)");
    const changed = () => setNarrow(media.matches);
    media.addEventListener("change", changed);
    return () => media.removeEventListener("change", changed);
  }, []);

  useEffect(() => {
    const trigger = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    if (narrow) dialogRef.current?.showModal();
    closeRef.current?.focus({ preventScroll: true });
    return () => {
      const target = returnFocusRef.current?.() ?? trigger;
      if (target?.isConnected) target.focus({ preventScroll: true });
    };
  }, [narrow]);

  const content = (
    <>
      <header className="record-inspector-header">
        <div>
          <h2 id={titleId}>{title}</h2>
          {subtitle ? <div className="record-inspector-subtitle">{subtitle}</div> : null}
        </div>
        <button ref={closeRef} type="button" aria-label="Close inspector" onClick={onClose}>
          ×
        </button>
      </header>
      <div className="record-inspector-body">{children}</div>
      {onOpenRecord ? (
        <footer className="record-inspector-footer">
          <button type="button" className="primary" onClick={onOpenRecord}>
            Open full record
          </button>
        </footer>
      ) : null}
    </>
  );

  return narrow ? (
    <dialog
      ref={dialogRef}
      className={`record-inspector record-inspector-sheet ${className}`}
      aria-labelledby={titleId}
      onKeyDown={(event) => {
        if (event.key !== "Tab") return;
        const controls = Array.from(event.currentTarget.querySelectorAll<HTMLElement>(
          'button:not(:disabled), a[href], input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])',
        )).filter((element) => element.getClientRects().length > 0);
        const first = controls[0];
        const last = controls[controls.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last?.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first?.focus();
        }
      }}
      onCancel={(event) => {
        event.preventDefault();
        onCloseRef.current();
      }}
    >
      {content}
    </dialog>
  ) : (
    <aside
      className={`record-inspector ${className}`}
      aria-labelledby={titleId}
      onKeyDown={(event) => {
        if (event.key === "Escape" && !event.defaultPrevented) {
          event.preventDefault();
          onCloseRef.current();
        }
      }}
    >
      {content}
    </aside>
  );
}

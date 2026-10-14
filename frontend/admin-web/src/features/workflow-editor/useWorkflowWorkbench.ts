import { useEffect, useRef, useState, type RefObject, type PointerEvent, type KeyboardEvent } from "react";

export type WorkflowInspectorTab = "configuration" | "outputs" | "history";
type WorkbenchView = "split" | "canvas" | "editor";
const STORAGE_KEY = "workflow-editor:workbench-layout";

function readPreferences(): { canvasShare: number; view: WorkbenchView; tab: WorkflowInspectorTab } {
  const defaults = { canvasShare: 34, view: "split" as WorkbenchView, tab: "configuration" as WorkflowInspectorTab };
  try {
    const stored = JSON.parse(window.localStorage.getItem(STORAGE_KEY) ?? "null");
    return { canvasShare: typeof stored?.canvasShare === "number" && Number.isFinite(stored.canvasShare) ? Math.min(80, Math.max(20, stored.canvasShare)) : defaults.canvasShare,
      view: ["split", "canvas", "editor"].includes(stored?.view) ? stored.view : defaults.view,
      tab: ["configuration", "outputs", "history"].includes(stored?.tab) ? stored.tab : defaults.tab };
  } catch { return defaults; }
}

export function useWorkflowWorkbench(shellRef: RefObject<HTMLDivElement>) {
  const [preferences, setPreferences] = useState(readPreferences);
  const [isResizing, setIsResizing] = useState(false);
  const resize = useRef<{ pointerId: number; startY: number; startShare: number; height: number } | null>(null);
  useEffect(() => { try { window.localStorage.setItem(STORAGE_KEY, JSON.stringify(preferences)); } catch { /* Storage can be disabled. */ } }, [preferences]);
  useEffect(() => {
    const move = (event: globalThis.PointerEvent) => {
      const current = resize.current;
      if (!current || current.pointerId !== event.pointerId) return;
      const canvasShare = Math.min(80, Math.max(20, current.startShare + (event.clientY - current.startY) * 100 / current.height));
      setPreferences(previous => ({ ...previous, canvasShare }));
    };
    const stop = (event: globalThis.PointerEvent) => { if (resize.current?.pointerId === event.pointerId) { resize.current = null; setIsResizing(false); } };
    const blur = () => { resize.current = null; setIsResizing(false); };
    window.addEventListener("pointermove", move); window.addEventListener("pointerup", stop); window.addEventListener("pointercancel", stop);
    window.addEventListener("blur", blur);
    return () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", stop); window.removeEventListener("pointercancel", stop); window.removeEventListener("blur", blur); };
  }, []);
  useEffect(() => {
    if (!isResizing) return;
    const body = globalThis.document.body;
    const cursor = body.style.cursor, userSelect = body.style.userSelect;
    body.style.cursor = "row-resize"; body.style.userSelect = "none";
    return () => { body.style.cursor = cursor; body.style.userSelect = userSelect; };
  }, [isResizing]);
  function startResize(event: PointerEvent<HTMLDivElement>) {
    if (event.button !== 0) return;
    const height = shellRef.current?.getBoundingClientRect().height;
    if (!height) return;
    resize.current = { pointerId: event.pointerId, startY: event.clientY, startShare: preferences.canvasShare, height };
    setIsResizing(true); event.preventDefault();
  }
  function resizeWithKeyboard(event: KeyboardEvent<HTMLDivElement>) {
    const changes: Record<string, number> = { ArrowUp: preferences.canvasShare - 5, ArrowDown: preferences.canvasShare + 5, Home: 20, End: 80 };
    if (!(event.key in changes)) return;
    event.preventDefault(); setPreferences(previous => ({ ...previous, canvasShare: Math.min(80, Math.max(20, changes[event.key])) }));
  }
  return { ...preferences, isResizing, startResize, resizeWithKeyboard,
    setView: (view: WorkbenchView) => setPreferences(previous => ({ ...previous, view })),
    setTab: (tab: WorkflowInspectorTab) => setPreferences(previous => ({ ...previous, tab })) };
}

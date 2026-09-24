import {
  useEffect,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
  type RefObject,
} from "react";
import {
  clampWorkflowSidebarWidth,
  maxWorkflowSidebarWidth,
  readStoredWorkflowSidebarWidth,
} from "./canvasModel";
import {
  WORKFLOW_COMPACT_BREAKPOINT,
  WORKFLOW_SIDEBAR_MIN_WIDTH,
  WORKFLOW_SIDEBAR_RESIZE_STEP,
  WORKFLOW_SIDEBAR_WIDTH_STORAGE_KEY,
} from "./editorConstants";
import { type SidebarResizeState } from "./editorTypes";

/** Owns sidebar width persistence and pointer/keyboard resize cleanup. */
export function useWorkflowSidebar(appShellRef: RefObject<HTMLDivElement>) {
  const sidebarResizeRef = useRef<SidebarResizeState | null>(null);

  const [sidebarWidth, setSidebarWidth] = useState<number>(() => readStoredWorkflowSidebarWidth());

  const [isResizingSidebar, setIsResizingSidebar] = useState(false);

  useEffect(() => {
    if (typeof window === "undefined") return;
    window.localStorage.setItem(WORKFLOW_SIDEBAR_WIDTH_STORAGE_KEY, String(sidebarWidth));
  }, [sidebarWidth]);

  useEffect(() => {
    if (typeof window === "undefined") return;

    const handlePointerMove = (event: PointerEvent) => {
      const resizeState = sidebarResizeRef.current;
      if (!resizeState || event.pointerId !== resizeState.pointerId) return;
      const containerWidth = appShellRef.current?.getBoundingClientRect().width ?? window.innerWidth;
      const delta = event.clientX - resizeState.startX;
      setSidebarWidth(clampWorkflowSidebarWidth(resizeState.startWidth + delta, containerWidth));
    };

    const stopResize = (event?: PointerEvent) => {
      const resizeState = sidebarResizeRef.current;
      if (!resizeState) return;
      if (event && event.pointerId !== resizeState.pointerId) return;
      sidebarResizeRef.current = null;
      setIsResizingSidebar(false);
    };

    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", stopResize);
    window.addEventListener("pointercancel", stopResize);

    return () => {
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", stopResize);
      window.removeEventListener("pointercancel", stopResize);
    };
  }, []);

  useEffect(() => {
    if (!isResizingSidebar || typeof globalThis.document === "undefined") return;
    const previousCursor = globalThis.document.body.style.cursor;
    const previousUserSelect = globalThis.document.body.style.userSelect;
    globalThis.document.body.style.cursor = "col-resize";
    globalThis.document.body.style.userSelect = "none";
    return () => {
      globalThis.document.body.style.cursor = previousCursor;
      globalThis.document.body.style.userSelect = previousUserSelect;
    };
  }, [isResizingSidebar]);

  function startSidebarResize(event: ReactPointerEvent<HTMLDivElement>) {
    if (event.button !== 0 || typeof window === "undefined" || window.innerWidth <= WORKFLOW_COMPACT_BREAKPOINT) return;
    sidebarResizeRef.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startWidth: sidebarWidth,
    };
    setIsResizingSidebar(true);
    event.preventDefault();
  }

  function handleSidebarResizeKeyDown(event: ReactKeyboardEvent<HTMLDivElement>) {
    if (typeof window === "undefined" || window.innerWidth <= WORKFLOW_COMPACT_BREAKPOINT) return;
    const containerWidth = appShellRef.current?.getBoundingClientRect().width ?? window.innerWidth;
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      setSidebarWidth((current) => clampWorkflowSidebarWidth(current - WORKFLOW_SIDEBAR_RESIZE_STEP, containerWidth));
      return;
    }
    if (event.key === "ArrowRight") {
      event.preventDefault();
      setSidebarWidth((current) => clampWorkflowSidebarWidth(current + WORKFLOW_SIDEBAR_RESIZE_STEP, containerWidth));
      return;
    }
    if (event.key === "Home") {
      event.preventDefault();
      setSidebarWidth(WORKFLOW_SIDEBAR_MIN_WIDTH);
      return;
    }
    if (event.key === "End") {
      event.preventDefault();
      setSidebarWidth(maxWorkflowSidebarWidth(containerWidth));
    }
  }

  return { sidebarWidth, setSidebarWidth, isResizingSidebar, startSidebarResize, handleSidebarResizeKeyDown };
}

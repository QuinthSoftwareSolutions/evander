import {
  useCallback,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type FocusEvent,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";

type TooltipProps = {
  content: ReactNode;
  children: ReactNode;
  className?: string;
  triggerClassName?: string;
  bubbleClassName?: string;
  focusable?: boolean;
  ariaLabel?: string;
  inline?: boolean;
};

function joinClasses(...values: Array<string | undefined | false>): string {
  return values.filter(Boolean).join(" ");
}

export function Tooltip({
  content,
  children,
  className,
  triggerClassName,
  bubbleClassName,
  focusable = false,
  ariaLabel,
  inline = true,
}: TooltipProps) {
  const rootRef = useRef<HTMLElement | null>(null);
  const triggerRef = useRef<HTMLElement | null>(null);
  const bubbleRef = useRef<HTMLSpanElement | null>(null);
  const [visible, setVisible] = useState(false);
  const [bubbleStyle, setBubbleStyle] = useState<CSSProperties>({});
  const [placement, setPlacement] = useState<"top" | "bottom">("bottom");

  const updatePosition = useCallback(() => {
    const trigger = triggerRef.current;
    const bubble = bubbleRef.current;
    if (!trigger || !bubble || typeof window === "undefined") {
      return;
    }

    const viewportPadding = 12;
    const gap = 12;
    const triggerRect = trigger.getBoundingClientRect();
    const bubbleRect = bubble.getBoundingClientRect();
    const bubbleHeight = Math.ceil(bubbleRect.height);
    const bubbleWidth = Math.ceil(bubbleRect.width);
    const centeredLeft = triggerRect.left + triggerRect.width / 2 - bubbleWidth / 2;
    const left = Math.min(
      Math.max(viewportPadding, centeredLeft),
      Math.max(viewportPadding, window.innerWidth - bubbleWidth - viewportPadding),
    );

    const fitsBelow = triggerRect.bottom + gap + bubbleHeight + viewportPadding <= window.innerHeight;
    const nextPlacement =
      fitsBelow || triggerRect.top < bubbleHeight + gap + viewportPadding ? "bottom" : "top";
    const top =
      nextPlacement === "bottom"
        ? triggerRect.bottom + gap
        : Math.max(viewportPadding, triggerRect.top - bubbleHeight - gap);

    const triggerCenter = triggerRect.left + triggerRect.width / 2;
    const arrowLeft = Math.min(
      Math.max(18, triggerCenter - left),
      Math.max(18, bubbleWidth - 18),
    );

    setPlacement(nextPlacement);
    setBubbleStyle({
      left,
      top,
      ["--admin-tooltip-arrow-left" as string]: `${arrowLeft}px`,
    });
  }, []);

  useLayoutEffect(() => {
    if (!visible) {
      return;
    }
    updatePosition();
    const handle = () => updatePosition();
    window.addEventListener("resize", handle);
    window.addEventListener("scroll", handle, true);
    return () => {
      window.removeEventListener("resize", handle);
      window.removeEventListener("scroll", handle, true);
    };
  }, [updatePosition, visible]);

  const RootTag = inline ? "span" : "div";
  const TriggerTag = inline ? "span" : "div";
  return (
    <RootTag
      className={joinClasses("admin-tooltip", className)}
      onMouseEnter={() => setVisible(true)}
      onMouseLeave={() => setVisible(false)}
      onFocusCapture={() => setVisible(true)}
      onBlurCapture={(event: FocusEvent) => {
        if (!rootRef.current?.contains(event.relatedTarget as Node | null)) {
          setVisible(false);
        }
      }}
      ref={(node) => {
        rootRef.current = node as HTMLElement | null;
      }}
    >
      <TriggerTag
        className={joinClasses("admin-tooltip-trigger", triggerClassName)}
        {...(focusable ? { tabIndex: 0, "aria-label": ariaLabel } : {})}
        ref={(node) => {
          triggerRef.current = node as HTMLElement | null;
        }}
      >
        {children}
      </TriggerTag>
      {typeof document !== "undefined"
        ? createPortal(
            <span
              ref={bubbleRef}
              className={joinClasses("admin-tooltip-bubble", `placement-${placement}`, bubbleClassName, visible && "visible")}
              role="tooltip"
              style={bubbleStyle}
            >
              {content}
            </span>,
            document.body,
          )
        : null}
    </RootTag>
  );
}

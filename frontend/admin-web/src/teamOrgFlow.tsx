import React, { useEffect, useId, useMemo, useRef, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import {
  Background,
  BaseEdge,
  Controls,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  ReactFlowProvider,
  getSmoothStepPath,
  useReactFlow,
  useViewport,
  type Edge,
  type EdgeProps,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import "./teamOrgFlow.css";

type InboxIndicator = {
  itemType: "status_request" | "progress_report";
  direction: "down" | "up";
  label: string;
  count: number;
  items?: Array<{
    detail: string;
    meta: string;
  }>;
};

type BaseNodeData = {
  kind: "agent" | "team" | "governance" | "warning";
};

type AgentNodeData = BaseNodeData & {
  kind: "agent";
  agentId: string;
  label: string;
  idLabel: string | null;
  role: string;
  status: string;
  cardClassName: string;
  avatar: {
    src: string;
    alt: string;
    initials: string;
  };
  awake: {
    kind: string;
    label: string;
    detail: string;
  };
  inboxIndicators: InboxIndicator[];
  work: {
    primary: string;
    waiting: string;
  };
  activity: {
    headline: string;
    tone: string;
  };
  activityBubble?: {
    headline: string;
    tone: string;
    eventKey: string;
  };
  detailLines: string[];
};

type TeamNodeData = BaseNodeData & {
  kind: "team";
  teamId: string;
  name: string;
  archived: boolean;
  awakeCount: number;
  attentionCount: number;
  costHtml: string;
  costBlockedNoticeHtml: string;
  costBlocked: boolean;
};

type GovernanceNodeData = BaseNodeData & {
  kind: "governance";
  groupId: string;
  name: string;
  archived: boolean;
  managerLabel: string;
  managerAvatar: {
    src: string;
    alt: string;
    initials: string;
  } | null;
  memberTeamCount: number;
  pendingAgendaCount: number;
  costAttention: boolean;
  costHtml: string;
};

type WarningNodeData = BaseNodeData & {
  kind: "warning";
  label: string;
  detailLines: string[];
};

type OrgNodeData = AgentNodeData | TeamNodeData | GovernanceNodeData | WarningNodeData;
type OrgNode = Node<OrgNodeData, "teamOrgNode">;

type OrgMessageEvent = {
  id: string;
  sourceNodeId: string;
  targetNodeId: string;
  sourceLabel: string;
  targetLabel: string;
  itemType: "status_request" | "progress_report";
  label: string;
};

type OrgMessagePulseData = OrgMessageEvent & {
  reverse: boolean;
};

type OrgFlowPayload = {
  nodes: OrgNode[];
  edges: Edge[];
  messageEvents: OrgMessageEvent[];
  fitKey: string;
};

type VisibleActivityBubble = NonNullable<AgentNodeData["activityBubble"]>;

const activityBubbleDurationMs = 5200;
const messagePulseDurationMs = 1250;
const maxVisibleActivityBubbles = 2;
const maxVisibleMessagePulses = 2;
const activityBubbleMinZoom = 0.5;

declare global {
  interface Window {
    renderTeamOrgFlow?: (payload: OrgFlowPayload | null) => void;
    __TEAM_ORG_FLOW_PAYLOAD__?: OrgFlowPayload | null;
  }
}

const roots = new WeakMap<Element, Root>();
const nodeTypes = {
  teamOrgNode: TeamOrgNode,
};
const edgeTypes = {
  messagePulse: MessagePulseEdge,
};

function TeamOrgFlowApp({ payload }: { payload: OrgFlowPayload | null }) {
  if (!payload) {
    return null;
  }
  return (
    <ReactFlowProvider>
      <TeamOrgFlowCanvas payload={payload} />
    </ReactFlowProvider>
  );
}

function TeamOrgFlowCanvas({ payload }: { payload: OrgFlowPayload }) {
  const reactFlow = useReactFlow<OrgNode, Edge>();
  const { zoom } = useViewport();
  const canvasRef = useRef<HTMLDivElement | null>(null);
  const previousFitKeyRef = useRef<string | null>(null);
  const activityFingerprintsRef = useRef(new Map<string, string>());
  const seenMessageEventIdsRef = useRef(new Set<string>());
  const timeoutIdsRef = useRef<number[]>([]);
  const [activityBubbles, setActivityBubbles] = useState<Record<string, VisibleActivityBubble>>({});
  const [messagePulses, setMessagePulses] = useState<OrgMessageEvent[]>([]);
  const [overflowNotice, setOverflowNotice] = useState<{ eventKey: string; count: number } | null>(null);
  const [liveAnnouncement, setLiveAnnouncement] = useState("");
  const prefersReducedMotion = usePrefersReducedMotion();
  const messageEvents = payload.messageEvents || [];
  const nodes = useMemo(
    () =>
      payload.nodes.map((node) => {
        if (node.data.kind !== "agent") {
          return node;
        }
        const activityBubble = zoom >= activityBubbleMinZoom ? activityBubbles[node.data.agentId] : undefined;
        return activityBubble
          ? { ...node, data: { ...node.data, activityBubble } }
          : node;
      }),
    [activityBubbles, payload.nodes, zoom],
  );
  const edges = useMemo(
    () => [
      ...payload.edges,
      ...messagePulses.map((message): Edge => {
        const hierarchyEdge = payload.edges.find(
          (edge) =>
            (edge.source === message.sourceNodeId && edge.target === message.targetNodeId) ||
            (edge.source === message.targetNodeId && edge.target === message.sourceNodeId),
        );
        const reverse = Boolean(
          hierarchyEdge &&
            hierarchyEdge.source === message.targetNodeId &&
            hierarchyEdge.target === message.sourceNodeId,
        );
        return {
          id: `message-pulse:${message.id}`,
          source: hierarchyEdge?.source || message.sourceNodeId,
          target: hierarchyEdge?.target || message.targetNodeId,
          sourceHandle: hierarchyEdge?.sourceHandle,
          targetHandle: hierarchyEdge?.targetHandle,
          type: "messagePulse",
          className: `org-flow-message-edge org-flow-message-edge--${message.itemType}`,
          data: { ...message, reverse } satisfies OrgMessagePulseData,
          selectable: false,
          focusable: false,
          deletable: false,
          zIndex: 20,
        };
      }),
    ],
    [messagePulses, payload.edges],
  );

  useEffect(() => {
    const activityFingerprints = new Map<string, string>();
    payload.nodes.forEach((node) => {
      if (node.data.kind === "agent") {
        activityFingerprints.set(
          node.data.agentId,
          `${node.data.activity.tone}\u0000${node.data.activity.headline}`,
        );
      }
    });
    const messageEventIds = new Set(messageEvents.map((event) => event.id));
    const layoutChanged = previousFitKeyRef.current !== payload.fitKey;
    if (layoutChanged) {
      previousFitKeyRef.current = payload.fitKey;
      activityFingerprintsRef.current = activityFingerprints;
      seenMessageEventIdsRef.current = messageEventIds;
      setActivityBubbles({});
      setMessagePulses([]);
      setOverflowNotice(null);
      return;
    }

    const changedActivities = payload.nodes
      .filter((node): node is OrgNode & { data: AgentNodeData } => node.data.kind === "agent")
      .flatMap((node) => {
        const nextFingerprint = activityFingerprints.get(node.data.agentId) || "";
        const previousFingerprint = activityFingerprintsRef.current.get(node.data.agentId);
        if (!previousFingerprint || previousFingerprint === nextFingerprint) {
          return [];
        }
        return [{
          agentId: node.data.agentId,
          label: node.data.label,
          headline: node.data.activity.headline,
          tone: node.data.activity.tone,
          eventKey: `${node.data.agentId}:${nextFingerprint}`,
        }];
      });
    activityFingerprintsRef.current = activityFingerprints;

    const newMessageEvents = messageEvents.filter(
      (event) => !seenMessageEventIdsRef.current.has(event.id),
    );
    messageEvents.forEach((event) => seenMessageEventIdsRef.current.add(event.id));

    const visibleActivities = changedActivities.slice(0, maxVisibleActivityBubbles);
    if (visibleActivities.length) {
      setActivityBubbles((current) => {
        const next = { ...current };
        visibleActivities.forEach((activity) => {
          next[activity.agentId] = {
            headline: activity.headline,
            tone: activity.tone,
            eventKey: activity.eventKey,
          };
        });
        return next;
      });
      visibleActivities.forEach((activity) => {
        const timeoutId = window.setTimeout(() => {
          setActivityBubbles((current) => {
            if (current[activity.agentId]?.eventKey !== activity.eventKey) {
              return current;
            }
            const next = { ...current };
            delete next[activity.agentId];
            return next;
          });
        }, activityBubbleDurationMs);
        timeoutIdsRef.current.push(timeoutId);
      });
    }

    const visibleMessages = prefersReducedMotion
      ? []
      : newMessageEvents.slice(0, maxVisibleMessagePulses);
    if (visibleMessages.length) {
      setMessagePulses((current) => [...current, ...visibleMessages].slice(-maxVisibleMessagePulses));
      visibleMessages.forEach((message) => {
        const timeoutId = window.setTimeout(() => {
          setMessagePulses((current) => current.filter((item) => item.id !== message.id));
        }, messagePulseDurationMs);
        timeoutIdsRef.current.push(timeoutId);
      });
    }

    const overflowCount =
      Math.max(0, changedActivities.length - maxVisibleActivityBubbles) +
      Math.max(0, newMessageEvents.length - maxVisibleMessagePulses);
    if (overflowCount) {
      const eventKey = `${Date.now()}:${overflowCount}`;
      setOverflowNotice({ eventKey, count: overflowCount });
      const timeoutId = window.setTimeout(() => {
        setOverflowNotice((current) => current?.eventKey === eventKey ? null : current);
      }, messagePulseDurationMs);
      timeoutIdsRef.current.push(timeoutId);
    }

    const announcements = [
      ...visibleActivities.map((activity) => `${activity.label}: ${activity.headline}`),
      ...newMessageEvents
        .slice(0, maxVisibleMessagePulses)
        .map((message) => `${message.sourceLabel} sent ${message.label} to ${message.targetLabel}`),
    ];
    if (overflowCount) {
      announcements.push(`${overflowCount} more updates`);
    }
    if (announcements.length) {
      setLiveAnnouncement(announcements.join(". "));
    }
  }, [messageEvents, payload.fitKey, payload.nodes, prefersReducedMotion]);

  useEffect(() => () => {
    timeoutIdsRef.current.forEach((timeoutId) => window.clearTimeout(timeoutId));
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) {
      return;
    }
    const handleWheel = (event: WheelEvent) => {
      const popover = inboxPopoverForWheelEvent(canvas, event);
      if (!popover) {
        return;
      }
      scrollInboxPopoverFromWheel(event, popover);
    };
    const handleContextMenu = (event: MouseEvent) => {
      const target = event.target;
      if (
        target instanceof Element &&
        target.closest(".react-flow__node, .react-flow__controls, .react-flow__panel")
      ) {
        return;
      }
      dispatchOrgFlowContextMenu("team-org-flow:canvas-menu", event);
    };
    canvas.addEventListener("wheel", handleWheel, { capture: true, passive: false });
    canvas.addEventListener("contextmenu", handleContextMenu, { capture: true });
    return () => {
      canvas.removeEventListener("wheel", handleWheel, { capture: true });
      canvas.removeEventListener("contextmenu", handleContextMenu, { capture: true });
    };
  }, []);

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => {
      void reactFlow.fitView({ padding: 0.18, duration: 180 });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [payload.fitKey, reactFlow]);

  return (
    <div ref={canvasRef} className="team-org-flow-canvas" data-team-org-flow-canvas>
      <ReactFlow<OrgNode, Edge>
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable
        selectNodesOnDrag={false}
        panOnScroll
        zoomOnScroll
        zoomOnPinch
        onPaneContextMenu={(event) => {
          event.preventDefault();
          dispatchOrgFlowContextMenu("team-org-flow:canvas-menu", event);
        }}
        onMoveStart={() => {
          window.dispatchEvent(new CustomEvent("team-org-flow:dismiss-menu"));
        }}
        fitView
        minZoom={0.25}
        maxZoom={1.5}
        defaultEdgeOptions={{
          type: "smoothstep",
          markerEnd: { type: MarkerType.ArrowClosed },
        }}
        proOptions={{ hideAttribution: true }}
      >
        <Background gap={24} size={1} />
        <Controls position="bottom-left" showInteractive={false} />
      </ReactFlow>
      {overflowNotice ? (
        <div className="org-flow-overflow-notice" aria-hidden="true">
          +{overflowNotice.count} more update{overflowNotice.count === 1 ? "" : "s"}
        </div>
      ) : null}
      <div className="visually-hidden" aria-live="polite" aria-atomic="true">
        {liveAnnouncement}
      </div>
    </div>
  );
}

function MessagePulseEdge({
  id,
  sourceX,
  sourceY,
  sourcePosition,
  targetX,
  targetY,
  targetPosition,
  data,
}: EdgeProps) {
  const [edgePath] = getSmoothStepPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
  });
  const reverse = Boolean((data as OrgMessagePulseData | undefined)?.reverse);
  return (
    <g className="org-flow-message-pulse" aria-hidden="true">
      <BaseEdge id={id} path={edgePath} className="org-flow-message-guide" />
      <circle className="org-flow-message-particle" r="5">
        <animateMotion
          dur={`${messagePulseDurationMs}ms`}
          path={edgePath}
          keyPoints={reverse ? "1;0" : "0;1"}
          keyTimes="0;1"
          calcMode="linear"
          fill="freeze"
        />
        <animate
          attributeName="opacity"
          values="0;1;1;0"
          keyTimes="0;0.12;0.72;1"
          dur={`${messagePulseDurationMs}ms`}
          fill="freeze"
        />
      </circle>
    </g>
  );
}

function usePrefersReducedMotion() {
  const [reducedMotion, setReducedMotion] = useState(false);
  useEffect(() => {
    const query = window.matchMedia("(prefers-reduced-motion: reduce)");
    const update = () => setReducedMotion(query.matches);
    update();
    query.addEventListener?.("change", update);
    return () => query.removeEventListener?.("change", update);
  }, []);
  return reducedMotion;
}

function TeamOrgNode({ data }: NodeProps<OrgNode>) {
  let content: React.ReactNode;
  if (data.kind === "agent") {
    content = <AgentNode data={data} />;
  } else if (data.kind === "team") {
    content = <TeamNode data={data} />;
  } else if (data.kind === "governance") {
    content = <GovernanceNode data={data} />;
  } else {
    content = <WarningNode data={data} />;
  }
  return (
    <div className={`team-org-flow-node team-org-flow-node--${data.kind}`}>
      <Handle
        className="team-org-flow-handle team-org-flow-handle--target"
        type="target"
        position={Position.Top}
        isConnectable={false}
      />
      {content}
      <Handle
        className="team-org-flow-handle team-org-flow-handle--source"
        type="source"
        position={Position.Bottom}
        isConnectable={false}
      />
    </div>
  );
}

function AgentNode({ data }: { data: AgentNodeData }) {
  const descriptionId = useId();
  const openAgent = () => {
    window.dispatchEvent(
      new CustomEvent("team-org-flow:edit-agent", {
        detail: { agentId: data.agentId },
      }),
    );
  };
  return (
    <article
      className={data.cardClassName}
      tabIndex={0}
      role="button"
      aria-label={`Edit ${data.label}`}
      aria-describedby={descriptionId}
      onClick={openAgent}
      onContextMenu={(event) => {
        dispatchOrgFlowContextMenu("team-org-flow:agent-menu", event, { agentId: data.agentId });
      }}
      onKeyDown={(event) => {
        if (event.key !== "Enter" && event.key !== " ") {
          return;
        }
        event.preventDefault();
        openAgent();
      }}
    >
      <div className="hierarchy-node-summary">
        <div className="hierarchy-node-title">
          <AvatarHoverCard
            avatar={data.avatar}
            label={data.label}
            role={data.role}
            idLabel={data.idLabel}
          />
          <strong>{data.label}</strong>
        </div>
        <div className="hierarchy-node-subtitle">
          <span className="hierarchy-node-role">{data.role}</span>
          <span className={`state-pill ${data.status}`}>{data.status}</span>
          <AwakePill awake={data.awake} />
          <InboxIndicators indicators={data.inboxIndicators} />
        </div>
      </div>
      <span id={descriptionId} className="visually-hidden">
        Agent ID {data.agentId}. Role {data.role}.
      </span>
      <div className="org-node-work">
        <div className="org-node-work-line org-node-work-line--now">
          <span>Now</span>
          <strong>{data.work.primary}</strong>
        </div>
        <div className="org-node-work-line org-node-work-line--waiting">
          <span>Waiting</span>
          <strong>{data.work.waiting}</strong>
        </div>
      </div>
      {data.activityBubble ? (
        <span
          className={`org-flow-activity-bubble org-flow-activity-bubble--${data.activityBubble.tone || "idle"}`}
          aria-hidden="true"
        >
          <small>Activity</small>
          <strong>{data.activityBubble.headline}</strong>
        </span>
      ) : null}
      <div className="hierarchy-node-details org-node-hover-panel">
        {data.detailLines.map((line, index) => (
          <div className="meta-line" key={`${line}-${index}`}>
            {line}
          </div>
        ))}
      </div>
    </article>
  );
}

function TeamNode({ data }: { data: TeamNodeData }) {
  return (
    <article className="org-focus-team org-flow-team-node">
      <div className="org-flow-team-header">
        <div className="org-flow-team-title">
          <p className="section-label">{data.teamId}</p>
          <h3>{data.name}</h3>
        </div>
        <div className="org-flow-node-pills org-focus-team-status">
          {data.archived ? <span className="state-pill archived">archived</span> : null}
          {data.costBlocked ? <span className="state-pill warning team-cost-blocked-pill">cost blocked</span> : null}
          <span className="state-pill info">{data.awakeCount} awake</span>
          <span className={`state-pill ${data.attentionCount ? "warning" : "accepted"}`}>
            {data.attentionCount} attention
          </span>
        </div>
      </div>
      <HtmlBlock html={data.costHtml} />
      <HtmlBlock html={data.costBlockedNoticeHtml} />
    </article>
  );
}

function GovernanceNode({ data }: { data: GovernanceNodeData }) {
  return (
    <article
      className="org-flow-governance-node governance-item"
      data-governance-group-id={data.groupId}
      onContextMenu={(event) => {
        dispatchOrgFlowContextMenu("team-org-flow:governance-menu", event, { groupId: data.groupId });
      }}
    >
      <div className="org-flow-governance-header">
        <div className="org-flow-governance-title">
          <p className="section-label">Governance</p>
          <h3>{data.name}</h3>
        </div>
        <div className="org-flow-node-pills">
          {data.archived ? <span className="state-pill blocked">archived</span> : null}
          {data.costAttention ? <span className="state-pill warning">cost attention</span> : null}
          <span className="state-pill neutral">
            {data.memberTeamCount} team{data.memberTeamCount === 1 ? "" : "s"}
          </span>
          <span className={`state-pill ${data.pendingAgendaCount ? "warning" : "accepted"}`}>
            {data.pendingAgendaCount} agenda
          </span>
        </div>
        <div className="org-flow-governance-manager">
          {data.managerAvatar ? <Avatar avatar={data.managerAvatar} extraClass="agent-avatar-small" /> : null}
          <span>{data.managerLabel}</span>
        </div>
      </div>
      <div className="meta-line">{data.groupId}</div>
      <HtmlBlock html={data.costHtml} />
    </article>
  );
}

function WarningNode({ data }: { data: WarningNodeData }) {
  return (
    <article className="hierarchy-node-card hierarchy-node-card--warning org-flow-warning-node" tabIndex={0}>
      <div className="hierarchy-node-summary">
        <div className="hierarchy-node-title">
          <strong>{data.label}</strong>
        </div>
        <div className="hierarchy-node-subtitle">
          <span className="state-pill warning">warning</span>
        </div>
      </div>
      {data.detailLines.length ? (
        <div className="hierarchy-node-details">
          {data.detailLines.map((line, index) => (
            <div className="meta-line" key={`${line}-${index}`}>
              {line}
            </div>
          ))}
        </div>
      ) : null}
    </article>
  );
}

function Avatar({
  avatar,
  extraClass = "",
}: {
  avatar: { src: string; alt: string; initials: string };
  extraClass?: string;
}) {
  const className = ["agent-avatar", extraClass].filter(Boolean).join(" ");
  if (avatar.src) {
    return (
      <span className={className}>
        <img src={avatar.src} alt={avatar.alt} loading="lazy" />
      </span>
    );
  }
  return (
    <span className={`${className} agent-avatar-fallback`} aria-hidden="true">
      {avatar.initials}
    </span>
  );
}

function AvatarHoverCard({
  avatar,
  label,
  role,
  idLabel,
}: {
  avatar: { src: string; alt: string; initials: string };
  label: string;
  role: string;
  idLabel: string | null;
}) {
  return (
    <span className="hierarchy-node-avatar-wrap">
      <Avatar avatar={avatar} />
      <span className="hierarchy-node-avatar-card" aria-hidden="true">
        <strong className="hierarchy-node-avatar-name">{label}</strong>
        {idLabel ? <span className="hierarchy-node-avatar-id">{idLabel}</span> : null}
        <span className="hierarchy-node-avatar-role">{role}</span>
      </span>
    </span>
  );
}

function AwakePill({ awake }: { awake: AgentNodeData["awake"] }) {
  return (
    <span className={`awake-indicator awake-indicator--${awake.kind}`} title={awake.detail}>
      <span className="awake-dot" aria-hidden="true" />
      {awake.label}
    </span>
  );
}

function InboxIndicators({ indicators }: { indicators: InboxIndicator[] }) {
  return (
    <span className="agent-inbox-indicators" aria-label="Pending inbox summary">
      {indicators.map((indicator) => (
        <InboxIndicatorBadge indicator={indicator} key={indicator.itemType} />
      ))}
    </span>
  );
}

function InboxIndicatorBadge({ indicator }: { indicator: InboxIndicator }) {
  const popoverId = useId();
  const popoverRef = useRef<HTMLSpanElement | null>(null);
  const items = indicator.items || [];
  const hasItems = items.length > 0;
  useEffect(() => {
    const popover = popoverRef.current;
    if (!popover) {
      return;
    }
    const handleWheel = (event: WheelEvent) => {
      scrollInboxPopoverFromWheel(event, popover);
    };
    popover.addEventListener("wheel", handleWheel, { passive: false });
    return () => {
      popover.removeEventListener("wheel", handleWheel);
    };
  }, [hasItems]);
  return (
    <span
      className={[
        "agent-inbox-indicator",
        `agent-inbox-indicator--${indicator.itemType}`,
        indicator.count ? "agent-inbox-indicator--active" : "agent-inbox-indicator--empty",
      ].join(" ")}
      aria-label={`${indicator.label}: ${indicator.count}`}
      aria-describedby={hasItems ? popoverId : undefined}
      tabIndex={hasItems ? 0 : undefined}
      onClick={(event) => event.stopPropagation()}
      onKeyDown={(event) => event.stopPropagation()}
    >
      <InboxIcon direction={indicator.direction} />
      <strong>{indicator.count}</strong>
      {hasItems ? (
        <span
          ref={popoverRef}
          className="agent-inbox-popover nowheel"
          role="tooltip"
          id={popoverId}
          aria-label={indicator.label}
        >
          {items.map((item, index) => (
            <span className="agent-inbox-bubble" key={`${item.detail}-${item.meta}-${index}`}>
              <strong>{item.detail}</strong>
              {item.meta ? <small>{item.meta}</small> : null}
            </span>
          ))}
        </span>
      ) : null}
    </span>
  );
}

function inboxPopoverForWheelEvent(canvas: HTMLElement, event: WheelEvent) {
  for (const popover of Array.from(canvas.querySelectorAll<HTMLElement>(".agent-inbox-popover"))) {
    const rect = popover.getBoundingClientRect();
    if (
      rect.width > 0 &&
      rect.height > 0 &&
      event.clientX >= rect.left &&
      event.clientX <= rect.right &&
      event.clientY >= rect.top &&
      event.clientY <= rect.bottom
    ) {
      return popover;
    }
  }

  const target = document.elementFromPoint(event.clientX, event.clientY);
  const popover = target?.closest(".agent-inbox-popover");
  return popover instanceof HTMLElement && canvas.contains(popover) ? popover : null;
}

function scrollInboxPopoverFromWheel(event: WheelEvent, popover: HTMLElement) {
  popover.scrollTop += event.deltaY;
  event.preventDefault();
  event.stopPropagation();
  event.stopImmediatePropagation();
}

function InboxIcon({ direction }: { direction: InboxIndicator["direction"] }) {
  return (
    <svg className={`agent-inbox-icon agent-inbox-icon--${direction}`} viewBox="0 0 20 20" aria-hidden="true" focusable="false">
      {direction === "up" ? (
        <>
          <path d="M10 8V3" />
          <path d="M7.3 5.7 10 3l2.7 2.7" />
        </>
      ) : (
        <>
          <path d="M10 3v5" />
          <path d="M7.3 5.3 10 8l2.7-2.7" />
        </>
      )}
      <path d="M5.5 8.2h9a2.2 2.2 0 0 1 2.2 2.2v2.6a2.2 2.2 0 0 1-2.2 2.2H9.3L5 18v-2.8a2.2 2.2 0 0 1-1.7-2.1v-2.7a2.2 2.2 0 0 1 2.2-2.2Z" />
    </svg>
  );
}

function HtmlBlock({ html }: { html: string }) {
  if (!html) {
    return null;
  }
  return <div dangerouslySetInnerHTML={{ __html: html }} />;
}

function dispatchOrgFlowContextMenu(
  eventName: "team-org-flow:canvas-menu" | "team-org-flow:agent-menu" | "team-org-flow:governance-menu",
  event: MouseEvent | React.MouseEvent,
  detail: Record<string, string> = {},
) {
  event.preventDefault();
  event.stopPropagation();
  window.dispatchEvent(
    new CustomEvent(eventName, {
      detail: {
        ...detail,
        clientX: event.clientX,
        clientY: event.clientY,
      },
    }),
  );
}

function renderTeamOrgFlow(payload: OrgFlowPayload | null) {
  const container = document.querySelector("#team-org-flow-root");
  if (!container) {
    window.__TEAM_ORG_FLOW_PAYLOAD__ = payload;
    return;
  }
  let root = roots.get(container);
  if (!root) {
    root = createRoot(container);
    roots.set(container, root);
  }
  root.render(<TeamOrgFlowApp payload={payload} />);
}

window.renderTeamOrgFlow = renderTeamOrgFlow;
window.addEventListener("team-org-flow:render", (event) => {
  renderTeamOrgFlow((event as CustomEvent<OrgFlowPayload | null>).detail ?? null);
});

if (window.__TEAM_ORG_FLOW_PAYLOAD__) {
  renderTeamOrgFlow(window.__TEAM_ORG_FLOW_PAYLOAD__);
}

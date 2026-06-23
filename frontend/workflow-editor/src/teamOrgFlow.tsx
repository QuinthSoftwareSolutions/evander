import React, { useEffect, useMemo } from "react";
import { createRoot, type Root } from "react-dom/client";
import {
  Background,
  Controls,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  type Edge,
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

type OrgFlowPayload = {
  nodes: OrgNode[];
  edges: Edge[];
  fitKey: string;
};

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
  const nodes = useMemo(() => payload.nodes, [payload]);
  const edges = useMemo(() => payload.edges, [payload]);

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => {
      void reactFlow.fitView({ padding: 0.18, duration: 180 });
    });
    return () => window.cancelAnimationFrame(frame);
  }, [payload.fitKey, reactFlow]);

  return (
    <div className="team-org-flow-canvas" data-team-org-flow-canvas>
      <ReactFlow<OrgNode, Edge>
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable
        selectNodesOnDrag={false}
        panOnScroll
        zoomOnScroll
        zoomOnPinch
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
    </div>
  );
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
      onClick={openAgent}
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
    <article className="org-flow-governance-node governance-item" data-governance-group-id={data.groupId}>
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
        <span
          className={[
            "agent-inbox-indicator",
            `agent-inbox-indicator--${indicator.itemType}`,
            indicator.count ? "agent-inbox-indicator--active" : "agent-inbox-indicator--empty",
          ].join(" ")}
          aria-label={`${indicator.label}: ${indicator.count}`}
          key={indicator.itemType}
        >
          <InboxIcon direction={indicator.direction} />
          <strong>{indicator.count}</strong>
        </span>
      ))}
    </span>
  );
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

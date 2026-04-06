import { useEffect, useRef, useState } from "react";
import {
  applyEdgeChanges,
  applyNodeChanges,
  Background,
  Controls,
  Handle,
  MiniMap,
  Position,
  ReactFlow,
  type Connection,
  type Edge,
  type Node,
  type NodeChange,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import {
  type WorkflowConfig,
  type WorkflowEdge,
  type WorkflowEditorDocument,
  type WorkflowLayout,
  type WorkflowNodeConfig,
  type WorkflowNodeKind,
} from "./types";

type GraphNodeData = {
  id: string;
  displayName: string;
  kind: WorkflowNodeKind;
  type: string;
};

type SaveState = "idle" | "saving" | "saved";

type NodeCatalogEntry = {
  kind: WorkflowNodeKind;
  type: string;
  label: string;
};

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "";

const NODE_CATALOG: ReadonlyArray<NodeCatalogEntry> = [
  { kind: "trigger", type: "trigger:cron", label: "Cron" },
  { kind: "trigger", type: "trigger:ticket:created", label: "Ticket created" },
  { kind: "trigger", type: "trigger:ticket:changed", label: "Ticket changed" },
  { kind: "trigger", type: "trigger:discord:new-message", label: "Discord message" },
  { kind: "trigger", type: "trigger:email:new-message", label: "Email message" },
  { kind: "trigger", type: "trigger:pull-request:new-feedback", label: "PR feedback" },
  { kind: "trigger", type: "trigger:git:push", label: "Git push" },
  { kind: "trigger", type: "trigger:agent:run:completed", label: "Agent run completed" },
  { kind: "gate", type: "gate:filter", label: "Filter" },
  { kind: "gate", type: "gate:map", label: "Map" },
  { kind: "gate", type: "gate:merge", label: "Merge" },
  { kind: "effect", type: "effect:vm:create", label: "VM create" },
  { kind: "effect", type: "effect:vm:start", label: "VM start" },
  { kind: "effect", type: "effect:vm:stop", label: "VM stop" },
  { kind: "effect", type: "effect:agent:prompt", label: "Agent prompt" },
  { kind: "effect", type: "effect:vm:exec", label: "VM exec" },
  { kind: "effect", type: "effect:vm:copy-files", label: "VM copy files" },
  { kind: "effect", type: "effect:pr:create", label: "PR create" },
  { kind: "query", type: "query:vm:run-state", label: "VM run state" },
];

const NODE_GROUPS: ReadonlyArray<{
  kind: WorkflowNodeKind;
  label: string;
  description: string;
}> = [
  { kind: "trigger", label: "Triggers", description: "Start workflow execution from incoming events." },
  { kind: "gate", label: "Gates", description: "Transform, filter, or combine workflow data." },
  { kind: "effect", label: "Effects", description: "Perform side effects in VMs, agents, or Git providers." },
  { kind: "query", label: "Queries", description: "Read workflow state without producing a side effect." },
];

function WorkflowNodeCard({ data, selected }: NodeProps) {
  const nodeData = data as GraphNodeData;
  return (
    <div className={`node-card node-${nodeData.kind}${selected ? " selected" : ""}`}>
      <Handle type="target" position={Position.Left} className="workflow-handle" />
      <div className="node-drag-surface">
        <div className="node-kind">{nodeData.kind}</div>
        <div className="node-title">{nodeData.displayName}</div>
        <div className="node-id">{nodeData.id}</div>
      </div>
      <Handle type="source" position={Position.Right} className="workflow-handle" />
    </div>
  );
}

const nodeTypes = {
  workflowNode: WorkflowNodeCard,
};

function emptyConfig(): WorkflowConfig {
  return {
    triggers: [],
    gates: [],
    effects: [],
    queries: [],
    edges: [],
  };
}

function emptyLayout(): WorkflowLayout {
  return { nodes: {} };
}

function nodeCollections(config: WorkflowConfig) {
  return {
    triggers: config.triggers ?? [],
    gates: config.gates ?? [],
    effects: config.effects ?? [],
    queries: config.queries ?? [],
  };
}

function fallbackPosition(index: number): { x: number; y: number } {
  return {
    x: 120 + (index % 4) * 280,
    y: 100 + Math.floor(index / 4) * 180,
  };
}

function nodeKindFromType(type: string): WorkflowNodeKind {
  if (type.startsWith("trigger:")) return "trigger";
  if (type.startsWith("gate:")) return "gate";
  if (type.startsWith("query:")) return "query";
  return "effect";
}

function kindCollectionKey(kind: WorkflowNodeKind): "triggers" | "gates" | "effects" | "queries" {
  if (kind === "trigger") return "triggers";
  if (kind === "gate") return "gates";
  if (kind === "query") return "queries";
  return "effects";
}

function inboundLimit(node: WorkflowNodeConfig): number | null {
  const kind = nodeKindFromType(node.type);
  if (kind === "trigger") return 0;
  if (node.type === "gate:merge") return null;
  return 1;
}

function buildNodes(config: WorkflowConfig, layout: WorkflowLayout): Node<GraphNodeData>[] {
  const collections = nodeCollections(config);
  const ordered = [
    ...collections.triggers,
    ...collections.gates,
    ...collections.effects,
    ...collections.queries,
  ];
  return ordered.map((node, index) => {
    const kind = nodeKindFromType(node.type);
    const position = layout.nodes[node.id] ?? fallbackPosition(index);
    return {
      id: node.id,
      type: "workflowNode",
      position,
      data: {
        id: node.id,
        displayName: node.display_name,
        kind,
        type: node.type,
      },
    };
  });
}

function buildEdges(config: WorkflowConfig): Edge[] {
  return config.edges.map((edge) => ({
    id: `${edge.source_id}->${edge.target_id}`,
    source: edge.source_id,
    target: edge.target_id,
  }));
}

function cloneConfig(config: WorkflowConfig): WorkflowConfig {
  return JSON.parse(JSON.stringify(config)) as WorkflowConfig;
}

function cloneLayout(layout: WorkflowLayout): WorkflowLayout {
  return JSON.parse(JSON.stringify(layout)) as WorkflowLayout;
}

function createDefaultNode(kind: WorkflowNodeKind, type: string, id: string): WorkflowNodeConfig {
  if (kind === "trigger" && type === "trigger:cron") {
    return { id, display_name: "New trigger", type, expression: "*/15 * * * *" };
  }
  if (kind === "trigger" && type === "trigger:ticket:created") {
    return { id, display_name: "Ticket created", type, directories: ["root"] };
  }
  if (kind === "trigger" && type === "trigger:ticket:changed") {
    return { id, display_name: "Ticket changed", type, directories: ["root"] };
  }
  if (kind === "trigger" && type === "trigger:discord:new-message") {
    return {
      id,
      display_name: "Discord message",
      type,
      discord_bot_token_env: "DISCORD_BOT_TOKEN",
      channel_ids: [],
      user_ids: [],
    };
  }
  if (kind === "trigger" && type === "trigger:email:new-message") {
    return {
      id,
      display_name: "Email message",
      type,
      provider: "json_store",
      account_identifier: "default",
      mailbox_ids: [],
      auth_secret_refs: {},
    };
  }
  if (kind === "trigger" && type === "trigger:pull-request:new-feedback") {
    return { id, display_name: "PR feedback", type, provider: "local" };
  }
  if (kind === "trigger" && type === "trigger:git:push") {
    return { id, display_name: "Git push", type, path_to_repo: "/path/to/repo.git" };
  }
  if (kind === "trigger" && type === "trigger:agent:run:completed") {
    return { id, display_name: "Agent run completed", type, vm_user: "agent" };
  }
  if (kind === "gate" && type === "gate:filter") {
    return { id, display_name: "New filter", type, outputs: {}, match: {} };
  }
  if (kind === "gate" && type === "gate:map") {
    return { id, display_name: "New map", type, outputs: {}, template: {} };
  }
  if (kind === "gate" && type === "gate:merge") {
    return { id, display_name: "New merge", type, outputs: {} };
  }
  if (kind === "effect" && type === "effect:agent:prompt") {
    return { id, display_name: "New prompt", type, prompt: "Describe the next action." };
  }
  if (kind === "effect" && type === "effect:vm:create") {
    return { id, display_name: "New VM effect", type };
  }
  if (kind === "effect" && type === "effect:vm:start") {
    return { id, display_name: "VM start", type };
  }
  if (kind === "effect" && type === "effect:vm:stop") {
    return { id, display_name: "VM stop", type, timeout_seconds: 30 };
  }
  if (kind === "effect" && type === "effect:vm:exec") {
    return { id, display_name: "New exec effect", type, command: "echo hello" };
  }
  if (kind === "effect" && type === "effect:vm:copy-files") {
    return {
      id,
      display_name: "New copy effect",
      type,
      files: [{ destination_path: "/tmp/example.txt", content: "example" }],
    };
  }
  if (kind === "effect" && type === "effect:pr:create") {
    return { id, display_name: "New PR effect", type, host_repo_path: "/path/to/repo" };
  }
  if (kind === "query" && type === "query:vm:run-state") {
    return { id, display_name: "VM run state", type };
  }
  return { id, display_name: "New node", type };
}

function slugifyNodeType(type: string): string {
  return type.replace(/[^a-zA-Z0-9]+/g, "-").replace(/^-+|-+$/g, "").toLowerCase() || "node";
}

function generateNodeId(config: WorkflowConfig, kind: WorkflowNodeKind, type: string): string {
  const collections = nodeCollections(config);
  const existingIds = new Set(
    [...collections.triggers, ...collections.gates, ...collections.effects, ...collections.queries].map(
      (node) => node.id,
    ),
  );
  const base = `${kind}-${slugifyNodeType(type).replace(`${kind}-`, "")}`;
  let index = 1;
  let candidate = `${base}-${index}`;
  while (existingIds.has(candidate)) {
    index += 1;
    candidate = `${base}-${index}`;
  }
  return candidate;
}

function nodeById(config: WorkflowConfig, nodeId: string): WorkflowNodeConfig | undefined {
  const collections = nodeCollections(config);
  return [...collections.triggers, ...collections.gates, ...collections.effects, ...collections.queries].find(
    (node) => node.id === nodeId,
  );
}

function validateConnection(config: WorkflowConfig, sourceId: string, targetId: string): string | null {
  if (!sourceId || !targetId) return "Edge endpoints are required.";
  if (sourceId === targetId) return "Nodes cannot connect to themselves.";
  if (config.edges.some((edge) => edge.source_id === sourceId && edge.target_id === targetId)) {
    return "That edge already exists.";
  }
  const targetNode = nodeById(config, targetId);
  if (!targetNode) return "Target node does not exist.";
  const limit = inboundLimit(targetNode);
  const inboundCount = config.edges.filter((edge) => edge.target_id === targetId).length;
  if (limit !== null && inboundCount >= limit) {
    return `Node ${targetId} already has the maximum supported inbound connections.`;
  }
  const nextEdges = [...config.edges, { source_id: sourceId, target_id: targetId }];
  const downstream = new Map<string, string[]>();
  for (const edge of nextEdges) {
    const current = downstream.get(edge.source_id) ?? [];
    current.push(edge.target_id);
    downstream.set(edge.source_id, current);
  }
  const stack = [...(downstream.get(targetId) ?? [])];
  const visited = new Set<string>();
  while (stack.length > 0) {
    const current = stack.pop()!;
    if (current === sourceId) return "That edge would create a cycle.";
    if (visited.has(current)) continue;
    visited.add(current);
    for (const child of downstream.get(current) ?? []) {
      stack.push(child);
    }
  }
  return null;
}

async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  const payload = (await response.json()) as T & { error?: string };
  if (!response.ok && typeof payload === "object" && payload && "error" in payload) {
    throw new Error(String(payload.error));
  }
  return payload as T;
}

export default function App() {
  const appShellRef = useRef<HTMLDivElement | null>(null);
  const [document, setDocument] = useState<WorkflowEditorDocument | null>(null);
  const [flowNodes, setFlowNodes] = useState<Node<GraphNodeData>[]>([]);
  const [flowEdges, setFlowEdges] = useState<Edge[]>([]);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);
  const [inspectorNodeId, setInspectorNodeId] = useState("");
  const [inspectorDisplayName, setInspectorDisplayName] = useState("");
  const [inspectorJson, setInspectorJson] = useState("{}");
  const [error, setError] = useState<string | null>(null);
  const [saveState, setSaveState] = useState<SaveState>("idle");

  useEffect(() => {
    fetchJson<WorkflowEditorDocument>("/api/workflow-editor/document")
      .then((payload) => {
        setDocument(payload);
        setFlowNodes(buildNodes(payload.config, payload.layout));
        setFlowEdges(buildEdges(payload.config));
      })
      .catch((reason: unknown) => {
        setError(reason instanceof Error ? reason.message : String(reason));
      });
  }, []);

  const config = document?.config ?? emptyConfig();
  const layout = document?.layout ?? emptyLayout();
  const selectedNode = selectedNodeId ? nodeById(config, selectedNodeId) : undefined;
  const selectedEdge = selectedEdgeId
    ? config.edges.find((edge) => `${edge.source_id}->${edge.target_id}` === selectedEdgeId)
    : undefined;

  useEffect(() => {
    if (!selectedNode) {
      setInspectorNodeId("");
      setInspectorDisplayName("");
      setInspectorJson("{}");
      return;
    }
    setInspectorNodeId(selectedNode.id);
    setInspectorDisplayName(selectedNode.display_name);
    const { id: _id, display_name: _displayName, type: _type, ...extras } = selectedNode;
    setInspectorJson(JSON.stringify(extras, null, 2));
  }, [selectedNode]);

  useEffect(() => {
    if (!selectedEdgeId) return;
    appShellRef.current?.focus();
  }, [selectedEdgeId]);

  function patchDocument(nextConfig: WorkflowConfig, nextLayout: WorkflowLayout) {
    if (!document) return;
    const nextDocument = {
      ...document,
      config: nextConfig,
      layout: nextLayout,
    };
    setDocument(nextDocument);
    setFlowNodes(buildNodes(nextConfig, nextLayout));
    setFlowEdges(buildEdges(nextConfig));
    setSaveState("idle");
  }

  function updateNodePositions(changes: NodeChange[]) {
    setFlowNodes((currentNodes) => applyNodeChanges(changes, currentNodes) as Node<GraphNodeData>[]);
  }

  function updateEdgeChanges(changes: Parameters<typeof applyEdgeChanges>[0]) {
    setFlowEdges((currentEdges) => applyEdgeChanges(changes, currentEdges));
  }

  function deleteSelectedEdge() {
    if (!document || !selectedEdgeId) return;
    const nextConfig = cloneConfig(document.config);
    nextConfig.edges = nextConfig.edges.filter(
      (edge) => `${edge.source_id}->${edge.target_id}` !== selectedEdgeId,
    );
    patchDocument(nextConfig, cloneLayout(document.layout));
    setSelectedEdgeId(null);
    setError(null);
  }

  function handleAppShellKeyDown(event: React.KeyboardEvent<HTMLDivElement>) {
    const target = event.target;
    if (
      target instanceof HTMLInputElement ||
      target instanceof HTMLTextAreaElement ||
      (target instanceof HTMLElement && target.isContentEditable)
    ) {
      return;
    }
    if (!selectedEdgeId) return;
    if (event.key !== "Delete" && event.key !== "Backspace") return;
    event.preventDefault();
    deleteSelectedEdge();
  }

  function persistNodePosition(nodeId: string, position: { x: number; y: number }) {
    if (!document) return;
    const nextLayout = cloneLayout(document.layout);
    nextLayout.nodes[nodeId] = position;
    setDocument({
      ...document,
      layout: nextLayout,
    });
    setSaveState("idle");
  }

  function addNode(kind: WorkflowNodeKind, type: string) {
    if (!document) return;
    const nextConfig = cloneConfig(document.config);
    nextConfig.triggers ??= [];
    nextConfig.gates ??= [];
    nextConfig.effects ??= [];
    nextConfig.queries ??= [];
    const node = createDefaultNode(kind, type, generateNodeId(nextConfig, kind, type));
    nextConfig[kindCollectionKey(kind)].push(node);
    const nextLayout = cloneLayout(document.layout);
    nextLayout.nodes[node.id] = fallbackPosition(
      nextConfig.triggers.length +
        nextConfig.gates.length +
        nextConfig.effects.length +
        nextConfig.queries.length -
        1,
    );
    patchDocument(nextConfig, nextLayout);
    setSelectedNodeId(node.id);
    setSelectedEdgeId(null);
    setError(null);
  }

  function deleteSelectedNode() {
    if (!document || !selectedNodeId) return;
    const nextConfig = cloneConfig(document.config);
    nextConfig.triggers ??= [];
    nextConfig.gates ??= [];
    nextConfig.effects ??= [];
    nextConfig.queries ??= [];
    nextConfig.triggers = nextConfig.triggers.filter((node) => node.id !== selectedNodeId);
    nextConfig.gates = nextConfig.gates.filter((node) => node.id !== selectedNodeId);
    nextConfig.effects = nextConfig.effects.filter((node) => node.id !== selectedNodeId);
    nextConfig.queries = nextConfig.queries.filter((node) => node.id !== selectedNodeId);
    nextConfig.edges = nextConfig.edges.filter(
      (edge) => edge.source_id !== selectedNodeId && edge.target_id !== selectedNodeId,
    );
    const nextLayout = cloneLayout(document.layout);
    delete nextLayout.nodes[selectedNodeId];
    patchDocument(nextConfig, nextLayout);
    setSelectedNodeId(null);
    setSelectedEdgeId(null);
    setError(null);
  }

  function applyInspectorChanges() {
    if (!document || !selectedNode) return;
    try {
      const parsed = JSON.parse(inspectorJson) as Record<string, unknown>;
      const rawId = inspectorNodeId.trim();
      const rawName = inspectorDisplayName.trim();
      if (!rawId) throw new Error("Node id is required.");
      if (!rawName) throw new Error("Display name is required.");
      if (rawId !== selectedNode.id && nodeById(document.config, rawId)) {
        throw new Error(`Node ${rawId} already exists.`);
      }
      const nextNode = {
        id: rawId,
        display_name: rawName,
        type: selectedNode.type,
        ...parsed,
      };
      const nextConfig = cloneConfig(document.config);
      nextConfig.triggers ??= [];
      nextConfig.gates ??= [];
      nextConfig.effects ??= [];
      nextConfig.queries ??= [];
      const collectionName = kindCollectionKey(nodeKindFromType(selectedNode.type));
      nextConfig[collectionName] = nextConfig[collectionName].map((node) =>
        node.id === selectedNode.id ? nextNode : node,
      );
      nextConfig.edges = nextConfig.edges.map((edge) => ({
        source_id: edge.source_id === selectedNode.id ? rawId : edge.source_id,
        target_id: edge.target_id === selectedNode.id ? rawId : edge.target_id,
      }));
      const nextLayout = cloneLayout(document.layout);
      if (rawId !== selectedNode.id) {
        nextLayout.nodes[rawId] = nextLayout.nodes[selectedNode.id] ?? fallbackPosition(0);
        delete nextLayout.nodes[selectedNode.id];
      }
      patchDocument(nextConfig, nextLayout);
      setSelectedNodeId(rawId);
      setSelectedEdgeId(null);
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  function onConnect(connection: Connection) {
    if (!document || !connection.source || !connection.target) return;
    const validationError = validateConnection(document.config, connection.source, connection.target);
    if (validationError) {
      setError(validationError);
      return;
    }
    const nextConfig = cloneConfig(document.config);
    nextConfig.edges = [
      ...nextConfig.edges,
      { source_id: connection.source, target_id: connection.target } satisfies WorkflowEdge,
    ];
    patchDocument(nextConfig, cloneLayout(document.layout));
    setSelectedEdgeId(null);
    setError(null);
  }

  function onEdgesDelete(deletedEdges: Edge[]) {
    if (!document) return;
    const deleted = new Set(deletedEdges.map((edge) => `${edge.source}->${edge.target}`));
    const nextConfig = cloneConfig(document.config);
    nextConfig.edges = nextConfig.edges.filter(
      (edge) => !deleted.has(`${edge.source_id}->${edge.target_id}`),
    );
    patchDocument(nextConfig, cloneLayout(document.layout));
    setSelectedEdgeId(null);
  }

  async function validateDocument() {
    if (!document) return;
    const validation = await fetchJson<{ config: WorkflowConfig; layout: WorkflowLayout }>(
      "/api/workflow-editor/validate",
      {
        method: "POST",
        body: JSON.stringify({
          config: document.config,
          layout: document.layout,
        }),
      },
    );
    patchDocument(validation.config, validation.layout);
  }

  async function saveDocument() {
    if (!document) return;
    setSaveState("saving");
    setError(null);
    try {
      const saved = await fetchJson<{ config: WorkflowConfig; layout: WorkflowLayout }>(
        "/api/workflow-editor/document",
        {
          method: "PUT",
          body: JSON.stringify({
            config: document.config,
            layout: document.layout,
          }),
        },
      );
      patchDocument(saved.config, saved.layout);
      setSaveState("saved");
    } catch (reason) {
      setSaveState("idle");
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  return (
    <div className="app-shell" ref={appShellRef} tabIndex={-1} onKeyDown={handleAppShellKeyDown}>
      <aside className="sidebar">
        <div className="sidebar-section">
          <div className="eyebrow">Workflow editor</div>
          <h1>Trigger graph</h1>
          <p>Drag nodes, connect edges, and edit full node config in the inspector.</p>
        </div>

        <div className="sidebar-section">
          <div className="section-label">Create</div>
          {NODE_GROUPS.map((group) => {
            const entries = NODE_CATALOG.filter((entry) => entry.kind === group.kind);
            return (
              <details key={group.kind} className="create-group" open={group.kind === "trigger"}>
                <summary className="create-group-summary">
                  <span>{group.label}</span>
                  <span className="create-group-count">{entries.length}</span>
                </summary>
                <p className="create-group-description">{group.description}</p>
                <div className="create-group-actions">
                  {entries.map((entry) => (
                    <button key={entry.type} onClick={() => addNode(entry.kind, entry.type)}>
                      {entry.label}
                    </button>
                  ))}
                </div>
              </details>
            );
          })}
        </div>

        <div className="sidebar-section">
          <div className="section-label">Save</div>
          <button onClick={() => void validateDocument()}>Validate</button>
          <button className="primary" onClick={() => void saveDocument()}>
            {saveState === "saving" ? "Saving..." : "Save workflow"}
          </button>
          {document ? (
            <div className="path-note">
              <div>{document.config_path}</div>
              <div>{document.layout_path}</div>
            </div>
          ) : null}
        </div>

        {selectedNode ? (
          <div className="sidebar-section inspector">
            <div className="section-label">Inspector</div>
            <div className="inspector-summary">
              <div>{selectedNode.type}</div>
              <div>{selectedNode.id}</div>
            </div>
            <label className="inspector-label">
              <span>Node id</span>
              <input value={inspectorNodeId} onChange={(event) => setInspectorNodeId(event.target.value)} />
            </label>
            <label className="inspector-label">
              <span>Display name</span>
              <input
                value={inspectorDisplayName}
                onChange={(event) => setInspectorDisplayName(event.target.value)}
              />
            </label>
            <textarea value={inspectorJson} onChange={(event) => setInspectorJson(event.target.value)} rows={18} />
            <button className="primary" onClick={applyInspectorChanges}>
              Apply node changes
            </button>
            <button className="danger" onClick={deleteSelectedNode}>
              Delete node
            </button>
          </div>
        ) : null}

        {selectedEdge ? (
          <div className="sidebar-section inspector">
            <div className="section-label">Edge</div>
            <div className="inspector-summary">
              <div>{selectedEdge.source_id}</div>
              <div>{selectedEdge.target_id}</div>
            </div>
            <button className="danger" onClick={deleteSelectedEdge}>
              Delete edge
            </button>
          </div>
        ) : null}

        {error ? <div className="error-box">{error}</div> : null}
      </aside>

      <main className="canvas-shell">
        <ReactFlow
          nodes={flowNodes}
          edges={flowEdges}
          nodeTypes={nodeTypes}
          onNodesChange={updateNodePositions}
          onEdgesChange={updateEdgeChanges}
          onNodeClick={(_, node) => {
            setSelectedNodeId(node.id);
            setSelectedEdgeId(null);
          }}
          onEdgeClick={(_, edge) => {
            setSelectedNodeId(null);
            setSelectedEdgeId(edge.id);
          }}
          onNodeDragStop={(_, node) => persistNodePosition(node.id, node.position)}
          onPaneClick={() => {
            setSelectedNodeId(null);
            setSelectedEdgeId(null);
          }}
          onConnect={onConnect}
          onEdgesDelete={onEdgesDelete}
          fitView
          deleteKeyCode={["Backspace", "Delete"]}
        >
          <MiniMap />
          <Controls />
          <Background gap={20} size={1} />
        </ReactFlow>
      </main>
    </div>
  );
}

import { useEffect, useState } from "react";
import {
  applyNodeChanges,
  Background,
  Controls,
  MiniMap,
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

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "";

function WorkflowNodeCard({ data, selected }: NodeProps) {
  const nodeData = data as GraphNodeData;
  return (
    <div className={`node-card node-${nodeData.kind}${selected ? " selected" : ""}`}>
      <div className="node-kind">{nodeData.kind}</div>
      <div className="node-title">{nodeData.displayName}</div>
      <div className="node-id">{nodeData.id}</div>
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
    edges: [],
  };
}

function emptyLayout(): WorkflowLayout {
  return { nodes: {} };
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
  return "effect";
}

function kindCollectionKey(kind: WorkflowNodeKind): "triggers" | "gates" | "effects" {
  if (kind === "trigger") return "triggers";
  if (kind === "gate") return "gates";
  return "effects";
}

function inboundLimit(node: WorkflowNodeConfig): number | null {
  const kind = nodeKindFromType(node.type);
  if (kind === "trigger") return 0;
  if (node.type === "gate:merge") return null;
  return 1;
}

function buildNodes(config: WorkflowConfig, layout: WorkflowLayout): Node<GraphNodeData>[] {
  const ordered = [...config.triggers, ...config.gates, ...config.effects];
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
  return { id, display_name: "New node", type };
}

function nodeById(config: WorkflowConfig, nodeId: string): WorkflowNodeConfig | undefined {
  return [...config.triggers, ...config.gates, ...config.effects].find((node) => node.id === nodeId);
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
  const [document, setDocument] = useState<WorkflowEditorDocument | null>(null);
  const [flowNodes, setFlowNodes] = useState<Node<GraphNodeData>[]>([]);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [inspectorJson, setInspectorJson] = useState("{}");
  const [error, setError] = useState<string | null>(null);
  const [saveState, setSaveState] = useState<SaveState>("idle");

  useEffect(() => {
    fetchJson<WorkflowEditorDocument>("/api/workflow-editor/document")
      .then((payload) => {
        setDocument(payload);
        setFlowNodes(buildNodes(payload.config, payload.layout));
      })
      .catch((reason: unknown) => {
        setError(reason instanceof Error ? reason.message : String(reason));
      });
  }, []);

  const config = document?.config ?? emptyConfig();
  const layout = document?.layout ?? emptyLayout();
  const edges = buildEdges(config);
  const selectedNode = selectedNodeId ? nodeById(config, selectedNodeId) : undefined;

  useEffect(() => {
    if (!selectedNode) {
      setInspectorJson("{}");
      return;
    }
    const { id: _id, display_name: _displayName, type: _type, ...extras } = selectedNode;
    setInspectorJson(JSON.stringify(extras, null, 2));
  }, [selectedNode]);

  function patchDocument(nextConfig: WorkflowConfig, nextLayout: WorkflowLayout) {
    if (!document) return;
    const nextDocument = {
      ...document,
      config: nextConfig,
      layout: nextLayout,
    };
    setDocument(nextDocument);
    setFlowNodes(buildNodes(nextConfig, nextLayout));
    setSaveState("idle");
  }

  function updateNodePositions(changes: NodeChange[]) {
    setFlowNodes((currentNodes) => applyNodeChanges(changes, currentNodes) as Node<GraphNodeData>[]);
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
    const rawId = window.prompt(`New ${kind} id`);
    const id = rawId?.trim();
    if (!id) return;
    if (nodeById(document.config, id)) {
      setError(`Node ${id} already exists.`);
      return;
    }
    const nextConfig = cloneConfig(document.config);
    nextConfig[kindCollectionKey(kind)].push(createDefaultNode(kind, type, id));
    const nextLayout = cloneLayout(document.layout);
    nextLayout.nodes[id] = fallbackPosition(
      nextConfig.triggers.length + nextConfig.gates.length + nextConfig.effects.length - 1,
    );
    patchDocument(nextConfig, nextLayout);
    setSelectedNodeId(id);
    setError(null);
  }

  function deleteSelectedNode() {
    if (!document || !selectedNodeId) return;
    const nextConfig = cloneConfig(document.config);
    nextConfig.triggers = nextConfig.triggers.filter((node) => node.id !== selectedNodeId);
    nextConfig.gates = nextConfig.gates.filter((node) => node.id !== selectedNodeId);
    nextConfig.effects = nextConfig.effects.filter((node) => node.id !== selectedNodeId);
    nextConfig.edges = nextConfig.edges.filter(
      (edge) => edge.source_id !== selectedNodeId && edge.target_id !== selectedNodeId,
    );
    const nextLayout = cloneLayout(document.layout);
    delete nextLayout.nodes[selectedNodeId];
    patchDocument(nextConfig, nextLayout);
    setSelectedNodeId(null);
    setError(null);
  }

  function applyInspectorChanges() {
    if (!document || !selectedNode) return;
    try {
      const parsed = JSON.parse(inspectorJson) as Record<string, unknown>;
      const rawId = window.prompt("Node id", selectedNode.id)?.trim() ?? selectedNode.id;
      const rawName =
        window.prompt("Display name", selectedNode.display_name)?.trim() ?? selectedNode.display_name;
      if (!rawId) throw new Error("Node id is required.");
      if (!rawName) throw new Error("Display name is required.");
      const nextNode = {
        id: rawId,
        display_name: rawName,
        type: selectedNode.type,
        ...parsed,
      };
      const nextConfig = cloneConfig(document.config);
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
    <div className="app-shell">
      <aside className="sidebar">
        <div className="sidebar-section">
          <div className="eyebrow">Workflow editor</div>
          <h1>Trigger graph</h1>
          <p>Drag nodes, connect edges, and edit full node config in the inspector.</p>
        </div>

        <div className="sidebar-section">
          <div className="section-label">Create</div>
          <button onClick={() => addNode("trigger", "trigger:cron")}>Add cron trigger</button>
          <button onClick={() => addNode("trigger", "trigger:git:push")}>Add git push trigger</button>
          <button onClick={() => addNode("gate", "gate:filter")}>Add filter gate</button>
          <button onClick={() => addNode("gate", "gate:map")}>Add map gate</button>
          <button onClick={() => addNode("gate", "gate:merge")}>Add merge gate</button>
          <button onClick={() => addNode("effect", "effect:agent:prompt")}>Add prompt effect</button>
          <button onClick={() => addNode("effect", "effect:vm:create")}>Add VM effect</button>
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
            <textarea value={inspectorJson} onChange={(event) => setInspectorJson(event.target.value)} rows={18} />
            <button className="primary" onClick={applyInspectorChanges}>
              Apply node changes
            </button>
            <button className="danger" onClick={deleteSelectedNode}>
              Delete node
            </button>
          </div>
        ) : null}

        {error ? <div className="error-box">{error}</div> : null}
      </aside>

      <main className="canvas-shell">
        <ReactFlow
          nodes={flowNodes}
          edges={edges}
          nodeTypes={nodeTypes}
          onNodesChange={updateNodePositions}
          onNodeClick={(_, node) => setSelectedNodeId(node.id)}
          onNodeDragStop={(_, node) => persistNodePosition(node.id, node.position)}
          onPaneClick={() => setSelectedNodeId(null)}
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

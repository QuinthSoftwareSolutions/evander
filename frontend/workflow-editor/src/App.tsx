import {
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type ChangeEvent as ReactChangeEvent,
  type DragEvent as ReactDragEvent,
  type FormEvent as ReactFormEvent,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
  type ReactNode,
  type Touch as ReactTouch,
  type TouchEvent as ReactTouchEvent,
} from "react";
import {
  applyEdgeChanges,
  applyNodeChanges,
  Background,
  Handle,
  Position,
  ReactFlow,
  type ReactFlowInstance,
  type Connection,
  type Edge,
  type Node,
  type NodeChange,
  type NodeProps,
  type Viewport,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import {
  type WorkflowConfig,
  type WorkflowEdge,
  type WorkflowGraphMetadata,
  type WorkflowNodeActivityCounts,
  type WorkflowEditorCatalog,
  type WorkflowEditorDocument,
  type WorkflowEditorFieldDefinition,
  type WorkflowEditorGeneratedDraft,
  type WorkflowEditorNodeCatalogEntry,
  type WorkflowEditorNodePortDefinition,
  type WorkflowEditorSave,
  type WorkflowEditorValidation,
  type WorkflowLayout,
  type WorkflowNodeConfig,
  type WorkflowNodeKind,
  type WorkflowOutputSchemas,
  type WorkflowPackDescriptor,
  type WorkflowPackMutation,
  type WorkflowRunDetail,
  type WorkflowRunDataAvailability,
  type WorkflowRunAgentLink,
  type WorkflowRunSearch,
  type WorkflowRunSearchItem,
  type WorkflowRunNodeDetail,
  type WorkflowRunNodeActivity,
  type WorkflowRunRecentNodeActivity,
  type WorkflowRunToolCallLink,
  type WorkflowRunSnapshot,
  type WorkflowRunSummary,
  type WorkflowValidationDiagnostic,
} from "./types";
import { ApiError } from "./features/admin/api";
import { useAdminUi, type DirectoryTreeNode } from "./features/admin/AdminContext";
import { CopyIcon } from "./features/admin/PageActionIcons";
import { Tooltip } from "./features/admin/Tooltip";
import { hashQueryParam } from "./adminRouting";
import { graphVisibleInDirectory } from "./workflowGraphDirectory";
import {
  BindingParseError,
  isParsedBindingExpression,
  isParsedBindingReference,
  parseBindingExpression,
  rewriteWorkflowConfigBindingReferences,
  rewriteWorkflowNodeConfigBindings,
  type ParsedBindingExpression,
  type ParsedBindingReference,
} from "./workflowBindings";
import {
  normalizeWorkflowSourcePort,
  workflowConnectionRouteOptions,
  workflowEdgeRouteLabel,
  workflowOutputPortsForNode,
  humanizeWorkflowRouteKey,
  workflowRouteClass,
  type WorkflowConnectionRouteOption,
} from "./workflowRoutes";
import {
  sameGraphNodeIdsForRunAnchors,
  workflowRunDetailActivityCounts,
} from "./workflowRunInspect";
import {
  compareWorkflowNodeCatalogEntries,
  groupWorkflowNodeCatalogEntries,
} from "./workflowCatalogGroups";
import {
  workflowJsonFieldDraftFallback,
  workflowJsonFieldFallbackValue,
} from "./workflowFieldDefaults";
import {
  buildWorkflowUpstreamIndex,
  declaredOutputsForNode,
  deriveWorkflowOutputSchemas,
  isPassThroughGateType,
  selectedNodeOutputContract as workflowNodeOutputContract,
  upstreamBindingOptions,
} from "./workflowNodeOutputs";
import {
  workflowCommunicationTargetInspectorState,
} from "./workflowCommunicationTargets";
import {
  workflowTriggerEnabled,
  workflowTriggerEnabledValue,
} from "./workflowTriggerState";
import {
  toolArgumentPolicyDraftRowsFromValue,
  toolArgumentPolicyDraftRowsToValue,
  type ToolArgumentPolicyConstraintDraftRow,
  type ToolArgumentPolicyDraftRow,
  type ToolArgumentPolicyRuleDraftRow,
  type ToolArgumentPolicyFieldError,
} from "./workflowToolArgumentPolicies";
import {
  FALLBACK_NODE_HEIGHT,
  FALLBACK_NODE_WIDTH,
  snapDraggedNodes,
  workflowNodeBounds as nodeBounds,
  type SnapGuide,
} from "./workflowNodeSnap";
import {
  createWorkflowTutorialSession,
  WORKFLOW_TUTORIAL_DEFINITIONS,
  workflowTutorialDefinitionById,
  workflowTutorialTargetNodeId,
  workflowTutorialRequiredActionMatches,
  type WorkflowTutorialRequiredAction,
  type WorkflowTutorialSession,
  type WorkflowTutorialStep,
} from "./workflowTutorials";

type GraphNodeData = {
  id: string;
  displayName: string;
  kind: WorkflowNodeKind;
  type: string;
  description: string;
  inputPorts: ReadonlyArray<WorkflowEditorNodePortDefinition>;
  outputPorts: ReadonlyArray<WorkflowEditorNodePortDefinition>;
  dominantStatus: string | null;
  statusBadges: ReadonlyArray<GraphNodeStatusBadge>;
  diagnosticCount: number;
  diagnosticErrorCount: number;
  diagnosticWarningCount: number;
  hasDiagnostics: boolean;
  hasErrorDiagnostics: boolean;
  hasUnsavedChanges: boolean;
  isDisabled: boolean;
  isInspectGhost?: boolean;
  runtimePulse: boolean;
  pendingConnectionRole?: PendingConnectionRole | null;
  tutorialHighlight?: boolean;
  tutorialBlocked?: boolean;
  tutorialHighlightMode?: WorkflowTutorialStep["highlightMode"] | null;
};

type GraphNodeStatusBadge = {
  status: string;
  count: number;
  label: string;
  title: string;
  compact: boolean;
};

type SaveState = "idle" | "saving" | "saved";
type WorkflowGeneratorState = "idle" | "draftable" | "generating" | "ready" | "error";
type WorkflowPackModalMode = "create" | "edit";

type WorkflowPackFormState = {
  packId: string;
  packVersion: string;
  description: string;
};

type WorkflowUndoEntry = {
  document: WorkflowDocumentSurface;
  nodeEditOrigins: Record<string, string>;
};

type WorkflowRunDetailLookupKind =
  | "auto"
  | "run_id"
  | "event_id"
  | "trigger_event_id"
  | "trace_id"
  | "session_id"
  | "artifact_id";

type WorkflowRunSearchScopeOption = {
  value: string;
  kind: "graph" | "node";
  id: string;
  label: string;
};

type BindingOption = {
  value: string;
  label: string;
};

type WorkflowFieldOption = WorkflowEditorFieldDefinition["options"][number];

type OperatorSteeringNoteStatus = "pending" | "injecting" | "delivered" | "cancelled" | "failed";

type OperatorSteeringNote = {
  note_id: string;
  session_id: string;
  dispatch_id: string;
  message: string;
  status: OperatorSteeringNoteStatus | string;
  worker_execution_id?: string | null;
  created_by?: string | null;
  created_at: string;
  updated_at: string;
  delivered_at?: string | null;
  cancelled_at?: string | null;
  provider?: string | null;
  provider_endpoint?: string | null;
  error?: string | null;
  delivery_attempts: number;
};

type OperatorSteeringNoteListResponse = {
  session_id: string;
  dispatch_id?: string | null;
  directory_path: string;
  notes: OperatorSteeringNote[];
};

type OperatorSteeringNoteMutationResponse = {
  directory_path: string;
  note: OperatorSteeringNote;
};

type StringMapDraftRow = {
  id: string;
  key: string;
  value: string;
};

type SessionListMetadataRuleKind = "equals" | "exists" | "prefix";

type SessionListMetadataRuleDraftRow = {
  id: string;
  kind: SessionListMetadataRuleKind;
  key: string;
  value: string;
};

type JsonDisplayMode = "structured" | "raw";

type InspectorDraftSnapshot = {
  nodeId: string;
  displayName: string;
  knownValues: Record<string, unknown>;
  unknownValues: Record<string, unknown>;
  effectTargetKey?: EffectTargetKey;
  jsonDrafts: Record<string, string>;
  toolArgumentPolicyDrafts: Record<string, ToolArgumentPolicyDraftRow[]>;
  jsonDisplayModes: Record<string, JsonDisplayMode>;
  stringListDrafts: Record<string, string>;
  stringMapDrafts: Record<string, StringMapDraftRow[]>;
};

type DirectorySelectOption = {
  value: string;
  label: string;
};

type WorkflowGraphLabel = {
  graph: WorkflowGraphMetadata;
  left: number;
  top: number;
};

type CanvasPoint = {
  x: number;
  y: number;
};

type ScreenPoint = {
  clientX: number;
  clientY: number;
};

type TutorialEditorSnapshot = {
  selectedNodeIds: string[];
  selectedNodeId: string | null;
  selectedEdgeId: string | null;
  selectedGraphId: string | null;
  runDetail: WorkflowRunDetail | null;
  canvasViewport: Viewport;
  viewportWasMeaningful: boolean;
};

type TutorialBubbleSize = {
  width: number;
  height: number;
};

type PendingConnectionState = {
  sourceId: string;
  sourcePort: string | null;
  routeLabel: string;
  routeClass: string;
  sourcePoint: CanvasPoint;
  cursorPoint: CanvasPoint;
};

type NodeConnectionMenuState = {
  nodeId: string;
  position: CanvasPoint;
  sourcePoint: CanvasPoint;
  routes: WorkflowConnectionRouteOption[];
  failedCount: number;
};

type PendingConnectionRole = "source" | "valid-target" | "invalid-target";

type CopiedWorkflowNode = {
  node: WorkflowNodeConfig;
  position: { x: number; y: number };
  width: number;
  height: number;
};

type CopiedWorkflowSelection = {
  nodes: CopiedWorkflowNode[];
  edges: WorkflowEdge[];
  primaryNodeId: string | null;
};

type WorkflowDocumentSurface = Pick<WorkflowEditorDocument, "config" | "layout" | "graphs">;

type WorkflowDocumentLike = {
  config: WorkflowConfig;
  layout: WorkflowLayout;
  graphs: WorkflowGraphMetadata[];
  output_schemas?: WorkflowOutputSchemas;
};

type WorkflowValidationCacheEntry = {
  signature: string;
  diagnostics: WorkflowValidationDiagnostic[];
};

type RuntimePulseContext = {
  isInspectMode: boolean;
  isTutorialMode: boolean;
  workflowGeneratorOpen: boolean;
  visibleGraphNodeIds: ReadonlySet<string>;
};

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "";
const EMPTY_OUTPUT_SCHEMAS: WorkflowOutputSchemas = {};
const RUNTIME_NODE_PULSE_DURATION_MS = 1400;
const RUNTIME_NODE_PULSE_SEEN_KEY_CAP = 1000;
const BINDING_EXACT_RE = /^\$\{\{([A-Za-z0-9][A-Za-z0-9_-]*)\.([A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*)\}\}$/;
const EFFECT_TARGET_KEYS = ["target", "session_id", "vm_name"] as const;
const TARGET_SELECTOR_NODE_TYPES = new Set([
  "effect:vm:clone-repo",
  "effect:vm:bootstrap-python",
  "effect:vm:bootstrap-frontend",
  "effect:vm:start",
  "effect:vm:stop",
  "effect:vm:exec",
  "effect:vm:copy-files",
  "effect:vm:delete-files",
  "effect:agent:destroy",
  "effect:agent:clone-repo",
  "effect:agent:bootstrap-frontend",
  "effect:agent:copy-files",
  "effect:agent:prompt",
  "effect:agent:enqueue-prompt",
  "effect:agent:exec",
  "effect:artifact:stage",
  "effect:artifact:publish-artifacts",
  "effect:artifact:import-published",
  "effect:session:update-metadata",
  "effect:session:approve-mcp-tools-for-run",
  "effect:session:renew-if-expired",
  "query:session-context",
]);
const SESSION_LIST_METADATA_FIELD_KEYS = [
  "metadata_equals",
  "metadata_exists",
  "metadata_prefix",
] as const;
const COMMUNICATION_EFFECT_NODE_TYPES = new Set([
  "effect:communication:send-message",
  "effect:notification:send",
]);
const SEMANTIC_SAMPLE_POINT_ID = "00000000-0000-4000-8000-000000000001";
type EffectTargetKey = (typeof EFFECT_TARGET_KEYS)[number];

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

const TOOL_GROUP_ORDER = [
  "Issue intake",
  "Azure DevOps",
  "Pull requests",
  "Approvals",
  "Agent control",
  "Messaging",
  "Voice calling",
  "SQL Server",
  "Files",
  "Discord",
  "Teams",
  "Email",
  "Feedback",
  "Assistant",
  "Semantic store",
  "Other",
] as const;

const NODE_KIND_DESCRIPTIONS: Record<WorkflowNodeKind, string> = {
  trigger: "Triggers start workflow execution from incoming events.",
  gate: "Gates transform, filter, fan out, or combine workflow data between nodes.",
  effect: "Effects perform side effects such as VM actions, session updates, Git operations, or agent prompts.",
  query: "Queries read workflow or environment state without causing a side effect.",
};

const NODE_TYPE_DESCRIPTIONS: Record<string, string> = {
  "trigger:cron": "Start a workflow on a recurring cron schedule.",
  "trigger:event": "Start when an internal workflow event is emitted.",
  "trigger:http:webhook": "Start from a signed HTTP webhook delivered to a public endpoint.",
  "trigger:ticket:created": "Start when a tracked ticket is created.",
  "trigger:ticket:changed": "Start when a tracked ticket changes state or metadata.",
  "trigger:issue:comment-created": "Start when a tracked issue receives a new comment.",
  "trigger:discord:new-message": "Start from new Discord messages that match the configured filters.",
  "trigger:teams:new-message": "Start from new Microsoft Teams messages that match the configured targets.",
  "trigger:communication:new-message": "Start from new messages on configured communication targets.",
  "trigger:approval:pending-created": "Start when a pending approval request is created.",
  "trigger:teams:direct-message": "Start from allowlisted one-on-one Microsoft Teams direct messages.",
  "trigger:email:new-message": "Start from newly ingested email messages for the configured mailbox scope.",
  "trigger:pull-request:new-feedback": "Start when new pull-request feedback arrives from the configured provider.",
  "trigger:pull-request:completed": "Start when a tracked pull request reaches a terminal state.",
  "trigger:ci:run:completed": "Start when a CI run completes on GitHub Actions or Azure DevOps.",
  "trigger:git:push": "Start from Git push events captured by the host-side bare-repo hook.",
  "trigger:agent:run:completed": "Start when a guest agent run completes.",
  "gate:filter": "Allow only matching events to continue downstream.",
  "gate:map": "Project upstream outputs into a new payload shape.",
  "gate:deserialize": "Parse a JSON or YAML string into object properties for downstream bindings.",
  "gate:serialize": "Serialize a bindable value as JSON or YAML text for downstream nodes.",
  "gate:merge": "Merge compatible inbound event streams into one downstream path.",
  "gate:switch": "Route activations to one of several downstream branches.",
  "gate:debounce": "Wait for a quiet period, then forward the latest activation.",
  "gate:buffer": "Collect activations by key and release them as a batch by count or timeout.",
  "gate:dedupe": "Suppress duplicate activations for the same key until a TTL expires.",
  "gate:await-agent": "Pause the workflow until a selected agent session completes.",
  "gate:aggregate": "Aggregate numeric fields from an array into flat summary outputs.",
  "gate:for": "Iterate over an array and execute the body branch for each item.",
  "gate:while": "Repeat a body branch while a bounded predicate remains true.",
  "effect:agent:prompt": "Send a prompt to an agent session.",
  "effect:agent:exec": "Run a shell command in an agent session.",
  "effect:agent:destroy": "Destroy an agent session and its backend target.",
  "effect:session:approve-mcp-tools-for-run": "Approve MCP tools for one agent session during this run.",
  "effect:session:revoke-mcp-tool-approval-lease": "Revoke an MCP tool approval lease for one agent session.",
  "effect:semantic:ensure-collection": "Ensure a semantic collection exists with the configured vector schema.",
  "effect:semantic:upsert": "Embed one text string and upsert it as one semantic point.",
  "effect:semantic:delete": "Delete semantic points by explicit point IDs.",
  "effect:state:update": "Set, patch, or delete graph-local workflow state.",
  "effect:http:request": "Perform a host-side raw HTTP GET, POST, or PUT request with URL allowlist control.",
  "effect:runtime:diagnostics-snapshot": "Capture a bounded, redacted host runtime diagnostics snapshot.",
  "effect:vm:create": "Create a VM for the workflow.",
  "effect:vm:clone-repo": "Clone a repository into a target VM.",
  "effect:vm:bootstrap-python": "Bootstrap a Python checkout and environment in a VM.",
  "effect:vm:bootstrap-frontend": "Bootstrap frontend dependencies in a VM.",
  "effect:vm:start": "Start an existing VM.",
  "effect:vm:stop": "Stop an existing VM.",
  "effect:vm:exec": "Run a shell command inside a VM.",
  "effect:vm:copy-files": "Copy rendered file content into a VM.",
  "effect:vm:delete-files": "Delete files from a VM.",
  "effect:event:emit": "Emit an internal workflow event for other graphs.",
  "effect:event:schedule": "Schedule a one-shot internal workflow event for later delivery.",
  "effect:issue:update-metadata": "Patch metadata on a tracked issue.",
  "effect:ado:pipeline-run": "Queue an Azure DevOps pipeline run and wait for its terminal result.",
  "effect:ado:update-work-item": "Update an Azure DevOps work item with an optional state guard.",
  "effect:ado:add-work-item-tag": "Add a tag to an Azure DevOps work item when it is not already present.",
  "effect:ado:remove-work-item-tag": "Remove a tag from an Azure DevOps work item when it is present.",
  "effect:session:update-metadata": "Patch session metadata on the host.",
  "effect:session:renew-if-expired": "Renew an expired or soon-expiring session.",
  "effect:artifact:stage": "Copy a file or directory from /home/agent into /artifacts.",
  "effect:artifact:publish-artifacts": "Publish the session /artifacts folder, or selected source paths, as an immutable tar.gz archive.",
  "effect:artifact:import-published": "Import a published artifact archive into a session artifacts folder.",
  "effect:git:promote-agent-branch": "Promote an agent branch into a trusted host checkout.",
  "effect:pr:create": "Create a pull request from the host side.",
  "effect:pull-request:update": "Update pull request metadata or tags.",
  "effect:pull-request:add-tag": "Add a tag to a pull request when it is not already present.",
  "effect:pull-request:remove-tag": "Remove a tag from a pull request when it is present.",
  "query:vm:run-state": "Read the current run state for a VM.",
  "query:git:pr-note": "Read pull-request note context from a host checkout.",
  "query:pull-request:list": "List provider pull requests.",
  "query:communication:messages": "Read messages from a read-capable Teams or Discord target.",
  "query:pull-request:get": "Get a provider pull request by id.",
  "query:session-context": "Read session context and metadata.",
  "query:state:get": "Read graph-local workflow state for the current persisted workflow graph.",
  "query:agent:artifact-text": "Read a UTF-8 text file from an agent session artifacts folder.",
  "query:issue:get": "Read a local issue record by id.",
  "query:ado:work-items": "Query Azure DevOps work items.",
  "query:semantic:store-status": "Read semantic store health and collection status.",
  "query:semantic:search": "Search a semantic collection with an embedded text query.",
  "query:semantic:get": "Retrieve semantic points by explicit point IDs.",
};

const RUNTIME_POLL_INTERVAL_MS = 5000;
const NODE_STATUS_ORDER = [
  "failed",
  "error_routed",
  "running",
  "waiting",
  "completion_pending",
  "inflight",
  "pending",
  "completed",
  "succeeded",
  "skipped",
] as const;
const WORKFLOW_SIDEBAR_WIDTH_STORAGE_KEY = "workflow-editor:sidebar-width";
const WORKFLOW_TUTORIAL_PROGRESS_API_PATH = "/api/admin/user-preferences/workflow-tutorial-progress";
const WORKFLOW_SIDEBAR_DEFAULT_WIDTH = 320;
const WORKFLOW_SIDEBAR_MIN_WIDTH = 320;
const WORKFLOW_SIDEBAR_MAX_WIDTH = 760;
const WORKFLOW_SIDEBAR_CANVAS_MIN_WIDTH = 420;
const WORKFLOW_SIDEBAR_RESIZE_STEP = 32;
const WORKFLOW_COMPACT_BREAKPOINT = 980;
const WORKFLOW_GENERATOR_MIN_DESCRIPTION_LENGTH = 24;
const WORKFLOW_UNDO_HISTORY_LIMIT = 50;
const NODE_TYPE_DRAG_DATA_MIME = "application/x-agent-sandbox-workflow-node-type";

type SidebarResizeState = {
  pointerId: number;
  startX: number;
  startWidth: number;
};

type NodeTypeDragPayload = {
  kind: WorkflowNodeKind;
  type: string;
};

type WorkflowTutorialProgress = {
  completedTutorialIds: string[];
  skippedTutorialIds: string[];
};

type WorkflowTutorialChooserStatus = "available" | "completed" | "skipped" | "in-progress";

function workflowTutorialProgressIds(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  const validTutorialIds = new Set(WORKFLOW_TUTORIAL_DEFINITIONS.map((definition) => definition.id));
  const seen = new Set<string>();
  const result: string[] = [];
  for (const entry of value) {
    if (typeof entry !== "string") continue;
    const tutorialId = entry.trim();
    if (!tutorialId || !validTutorialIds.has(tutorialId) || seen.has(tutorialId)) continue;
    seen.add(tutorialId);
    result.push(tutorialId);
  }
  return result;
}

function normalizeWorkflowTutorialProgress(value: unknown): WorkflowTutorialProgress {
  if (!isRecord(value)) {
    return { completedTutorialIds: [], skippedTutorialIds: [] };
  }
  return {
    completedTutorialIds: workflowTutorialProgressIds(value.completedTutorialIds ?? value.completed_tutorial_ids),
    skippedTutorialIds: workflowTutorialProgressIds(value.skippedTutorialIds ?? value.skipped_tutorial_ids),
  };
}

function workflowTutorialProgressApiPayload(progress: WorkflowTutorialProgress): Record<string, string[]> {
  return {
    completed_tutorial_ids: progress.completedTutorialIds,
    skipped_tutorial_ids: progress.skippedTutorialIds,
  };
}

function readStoredWorkflowSidebarWidth(): number {
  if (typeof window === "undefined") return WORKFLOW_SIDEBAR_DEFAULT_WIDTH;
  const raw = window.localStorage.getItem(WORKFLOW_SIDEBAR_WIDTH_STORAGE_KEY);
  if (!raw) return WORKFLOW_SIDEBAR_DEFAULT_WIDTH;
  const parsed = Number.parseInt(raw, 10);
  if (!Number.isFinite(parsed)) return WORKFLOW_SIDEBAR_DEFAULT_WIDTH;
  return Math.min(Math.max(parsed, WORKFLOW_SIDEBAR_MIN_WIDTH), WORKFLOW_SIDEBAR_MAX_WIDTH);
}

function maxWorkflowSidebarWidth(containerWidth: number): number {
  return Math.max(
    WORKFLOW_SIDEBAR_MIN_WIDTH,
    Math.min(WORKFLOW_SIDEBAR_MAX_WIDTH, Math.floor(containerWidth - WORKFLOW_SIDEBAR_CANVAS_MIN_WIDTH)),
  );
}

function clampWorkflowSidebarWidth(width: number, containerWidth: number): number {
  const maxWidth = maxWorkflowSidebarWidth(containerWidth);
  return Math.min(Math.max(width, WORKFLOW_SIDEBAR_MIN_WIDTH), maxWidth);
}

function isCompactCanvasTouchViewport(): boolean {
  if (typeof window === "undefined") return false;
  return window.innerWidth <= WORKFLOW_COMPACT_BREAKPOINT;
}

type CompactActivePanel = null | "create" | "inspector" | "runs" | "graphs" | "save";
type CompactCanvasMode = "select" | "pan" | "connect";
type CompactCanvasTouchMode = "idle" | "pane-scroll" | "pane-pan" | "node-drag" | "multi-touch-pan";
type CompactPanePanState = {
  touchId: number;
  clientX: number;
  clientY: number;
  viewport: Viewport;
};

function eventTargetInsideWorkflowNodeDragSurface(target: EventTarget | null): boolean {
  if (!(target instanceof Element)) return false;
  const dragSurface = target.closest(".node-drag-surface");
  if (!dragSurface) return false;
  const node = dragSurface.closest(".react-flow__node");
  return Boolean(node && node.contains(dragSurface));
}

function WorkflowNodeCard({ data, selected }: NodeProps) {
  const nodeData = data as GraphNodeData;
  const kindDescription = NODE_KIND_DESCRIPTIONS[nodeData.kind];
  const typeDescription =
    nodeData.description || NODE_TYPE_DESCRIPTIONS[nodeData.type] || `${nodeData.type} node.`;
  return (
    <div
      className={`node-card node-${nodeData.kind}${selected ? " selected" : ""}${
        nodeData.dominantStatus ? ` runtime-${nodeData.dominantStatus}` : ""
      }${nodeData.hasErrorDiagnostics ? " invalid" : ""}${
        nodeData.pendingConnectionRole ? ` connection-${nodeData.pendingConnectionRole}` : ""
      }${nodeData.hasUnsavedChanges ? " unsaved" : ""}${nodeData.isDisabled ? " trigger-disabled" : ""}${nodeData.isInspectGhost ? " inspect-ghost" : ""}${nodeData.runtimePulse ? " runtime-pulse" : ""}${
        nodeData.tutorialHighlight ? ` tutorial-highlight tutorial-highlight-${nodeData.tutorialHighlightMode ?? "pulse"}` : ""
      }${nodeData.tutorialBlocked ? " tutorial-blocked" : ""}`}
    >
      <Handle type="target" position={Position.Left} className="workflow-handle workflow-technical-handle" />
      {nodeData.inputPorts.map((port) => (
        <Handle
          key={`input-${port.key}`}
          id={port.key}
          type="target"
          position={Position.Left}
          className="workflow-handle workflow-technical-handle"
        />
      ))}
      <div className="node-drag-surface">
        <div className="node-heading">
          <Tooltip content={`${kindDescription} ${typeDescription}`}>
            <div className="node-kind">{nodeData.kind}</div>
          </Tooltip>
          <div className="node-badges">
            {nodeData.diagnosticErrorCount > 0 ? (
              <Tooltip content={`${nodeData.diagnosticErrorCount} workflow binding error${nodeData.diagnosticErrorCount === 1 ? "" : "s"}`}>
                <div className="node-diagnostic-badge error">{nodeData.diagnosticErrorCount}</div>
              </Tooltip>
            ) : null}
            {nodeData.diagnosticWarningCount > 0 ? (
              <Tooltip content={`${nodeData.diagnosticWarningCount} workflow binding warning${nodeData.diagnosticWarningCount === 1 ? "" : "s"}`}>
                <div className="node-diagnostic-badge warning">{nodeData.diagnosticWarningCount}</div>
              </Tooltip>
            ) : null}
            {nodeData.hasUnsavedChanges ? (
              <Tooltip content="Unsaved changes">
                <div className="node-unsaved-badge" aria-label="Unsaved changes">
                  Unsaved changes
                </div>
              </Tooltip>
            ) : null}
            {nodeData.isDisabled ? (
              <Tooltip content="Trigger disabled">
                <div className="node-disabled-badge" aria-label="Trigger disabled">
                  Disabled
                </div>
              </Tooltip>
            ) : null}
            {nodeData.statusBadges.map((badge) => (
              <Tooltip key={badge.status} content={badge.title}>
                <div
                  className={`node-runtime-badge status-${badge.status}${badge.compact ? " compact" : ""}`}
                  aria-label={badge.title}
                >
                  {badge.label}
                </div>
              </Tooltip>
            ))}
          </div>
        </div>
        <Tooltip content={typeDescription}>
          <div className="node-title">{nodeData.displayName}</div>
        </Tooltip>
        <div className="node-id">{nodeData.id}</div>
      </div>
      <Handle type="source" position={Position.Right} className="workflow-handle workflow-technical-handle" />
      {nodeData.outputPorts.map((port) => (
        <Handle
          key={`output-${port.key}`}
          id={port.key}
          type="source"
          position={Position.Right}
          className="workflow-handle workflow-technical-handle"
        />
      ))}
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

function emptyCatalog(): WorkflowEditorCatalog {
  return { worker_id: "trigger-orchestrator", nodes: [] };
}

function nodeCollections(config: WorkflowConfig) {
  return {
    triggers: config.triggers ?? [],
    gates: config.gates ?? [],
    effects: config.effects ?? [],
    queries: config.queries ?? [],
  };
}

function workflowNodes(config: WorkflowConfig): WorkflowNodeConfig[] {
  const collections = nodeCollections(config);
  return [
    ...collections.triggers,
    ...collections.gates,
    ...collections.effects,
    ...collections.queries,
  ];
}

function workflowNodeIds(config: WorkflowConfig): string[] {
  return workflowNodes(config).map((node) => node.id);
}

function workflowGraphKey(nodeIds: string[]): string {
  return [...nodeIds].sort().join("\u0000");
}

function connectedWorkflowGraphNodeIds(config: WorkflowConfig): string[][] {
  const orderedNodeIds = workflowNodeIds(config);
  const nodeIdSet = new Set(orderedNodeIds);
  const order = new Map(orderedNodeIds.map((nodeId, index) => [nodeId, index]));
  const neighbors = new Map<string, Set<string>>();
  orderedNodeIds.forEach((nodeId) => neighbors.set(nodeId, new Set()));
  for (const edge of config.edges ?? []) {
    if (!nodeIdSet.has(edge.source_id) || !nodeIdSet.has(edge.target_id)) continue;
    neighbors.get(edge.source_id)?.add(edge.target_id);
    neighbors.get(edge.target_id)?.add(edge.source_id);
  }
  const visited = new Set<string>();
  const components: string[][] = [];
  for (const nodeId of orderedNodeIds) {
    if (visited.has(nodeId)) continue;
    const stack = [nodeId];
    const component: string[] = [];
    while (stack.length > 0) {
      const current = stack.pop()!;
      if (visited.has(current)) continue;
      visited.add(current);
      component.push(current);
      const nextNodeIds = [...(neighbors.get(current) ?? [])]
        .filter((candidate) => !visited.has(candidate))
        .sort((left, right) => (order.get(right) ?? 0) - (order.get(left) ?? 0));
      stack.push(...nextNodeIds);
    }
    components.push(component.sort((left, right) => (order.get(left) ?? 0) - (order.get(right) ?? 0)));
  }
  return components;
}

function slugifyGraphId(value: string): string {
  return value.replace(/[^a-zA-Z0-9_-]+/g, "-").replace(/^-+|-+$/g, "").toLowerCase() || "graph";
}

function defaultWorkflowGraphName(config: WorkflowConfig, component: string[], index: number): string {
  const trigger = component
    .map((nodeId) => nodeById(config, nodeId))
    .find((node): node is WorkflowNodeConfig => Boolean(node && node.type.startsWith("trigger:")));
  const fallbackNode = nodeById(config, component[0] ?? "");
  return trigger?.display_name?.trim() || fallbackNode?.display_name?.trim() || `Graph ${index + 1}`;
}

function defaultWorkflowGraphId(config: WorkflowConfig, component: string[], index: number): string {
  const trigger = component
    .map((nodeId) => nodeById(config, nodeId))
    .find((node): node is WorkflowNodeConfig => Boolean(node && node.type.startsWith("trigger:")));
  return `graph-${slugifyGraphId(trigger?.id ?? component[0] ?? String(index + 1))}`;
}

function normalizeWorkflowGraphs(
  config: WorkflowConfig,
  graphs: ReadonlyArray<WorkflowGraphMetadata> = [],
  defaultDirectoryPath = "root",
): WorkflowGraphMetadata[] {
  const components = connectedWorkflowGraphNodeIds(config);
  const validNodeIds = new Set(workflowNodeIds(config));
  const sanitizedGraphs = graphs
    .map((graph) => ({
      id: graph.id.trim(),
      name: graph.name.trim(),
      description: graph.description?.trim() || null,
      directory_path:
        typeof graph.directory_path === "string" && graph.directory_path.trim()
          ? graph.directory_path.trim()
          : Array.from(
              new Set(
                (Array.isArray(graph.directory_paths) ? graph.directory_paths : [])
                  .map((directoryPath) => directoryPath.trim())
                  .filter(Boolean),
              ),
            )[0] ?? defaultDirectoryPath,
      user_ids: Array.from(
        new Set(
          (Array.isArray(graph.user_ids) ? graph.user_ids : [])
            .map((userId) => userId.trim())
            .filter(Boolean),
        ),
      ),
      node_ids: Array.from(new Set((graph.node_ids ?? []).map((nodeId) => nodeId.trim()).filter((nodeId) => validNodeIds.has(nodeId)))),
    }))
    .filter((graph) => graph.id && graph.name && graph.node_ids.length > 0);
  const exactGraphs = new Map(sanitizedGraphs.map((graph) => [workflowGraphKey(graph.node_ids), graph]));
  const usedGraphIds = new Set<string>();
  const usedResultIds = new Set<string>();
  return components.map((component, index) => {
    const componentKey = workflowGraphKey(component);
    let graph = exactGraphs.get(componentKey);
    if (!graph) {
      graph = sanitizedGraphs
        .filter((candidate) => !usedGraphIds.has(candidate.id))
        .map((candidate) => ({
          graph: candidate,
          overlap: candidate.node_ids.filter((nodeId) => component.includes(nodeId)).length,
        }))
        .filter((candidate) => candidate.overlap > 0)
        .sort((left, right) => right.overlap - left.overlap)[0]?.graph;
    }
    const baseId = graph?.id ?? defaultWorkflowGraphId(config, component, index);
    let id = baseId;
    let suffix = 2;
    while (usedResultIds.has(id)) {
      id = `${baseId}-${suffix}`;
      suffix += 1;
    }
    usedResultIds.add(id);
    if (graph) usedGraphIds.add(graph.id);
    return {
      id,
      name: graph?.name ?? defaultWorkflowGraphName(config, component, index),
      description: graph?.description ?? null,
      directory_path: graph?.directory_path ?? defaultDirectoryPath,
      user_ids: graph?.user_ids ?? [],
      node_ids: component,
    };
  });
}

function fallbackPosition(index: number): { x: number; y: number } {
  return {
    x: 120 + (index % 4) * 280,
    y: 100 + Math.floor(index / 4) * 180,
  };
}

function copiedWorkflowSelectionCenter(nodes: ReadonlyArray<CopiedWorkflowNode>): { x: number; y: number } | null {
  if (nodes.length === 0) return null;
  const left = Math.min(...nodes.map((node) => node.position.x));
  const top = Math.min(...nodes.map((node) => node.position.y));
  const right = Math.max(...nodes.map((node) => node.position.x + node.width));
  const bottom = Math.max(...nodes.map((node) => node.position.y + node.height));
  return {
    x: (left + right) / 2,
    y: (top + bottom) / 2,
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
  if (node.type === "gate:merge" || node.type === "gate:for" || node.type === "gate:while") {
    return null;
  }
  return 1;
}

function nodeStatusBuckets(
  activity: WorkflowNodeActivityCounts | undefined,
): Array<{ status: string; count: number }> {
  if (!activity) return [];
  const orderedStatuses = new Set<string>(NODE_STATUS_ORDER);
  const buckets: Array<{ status: string; count: number }> = [];
  for (const status of NODE_STATUS_ORDER) {
    const count = activity[status] ?? 0;
    if (count > 0) {
      buckets.push({ status, count });
    }
  }
  for (const [status, count] of Object.entries(activity)) {
    if (!orderedStatuses.has(status) && count > 0) {
      buckets.push({ status, count });
    }
  }
  return buckets;
}

function dominantNodeStatus(activity: WorkflowNodeActivityCounts | undefined): string | null {
  return nodeStatusBuckets(activity)[0]?.status ?? null;
}

function nodeStatusBadges(activity: WorkflowNodeActivityCounts | undefined): GraphNodeStatusBadge[] {
  const buckets = nodeStatusBuckets(activity);
  const compact = buckets.length > 1;
  return buckets.map(({ status, count }) => {
    const title = `${count} ${status}`;
    return {
      status,
      count,
      title,
      compact,
      label: compact ? `${count}` : title,
    };
  });
}

function trimNonEmpty(value: string | null | undefined): string | null {
  const normalized = value?.trim() ?? "";
  return normalized ? normalized : null;
}

function buildNodes(
  config: WorkflowConfig,
  layout: WorkflowLayout,
  catalog: WorkflowEditorCatalog,
  nodeActivity: Record<string, WorkflowNodeActivityCounts> = {},
  diagnostics: WorkflowValidationDiagnostic[] = [],
  currentPositions: Record<string, { x: number; y: number }> = {},
  selectedNodeIds: string[] = [],
  dirtyNodeIds: ReadonlySet<string> = new Set<string>(),
  ghostNodeIds: ReadonlySet<string> = new Set<string>(),
  runtimePulseNodeIds: ReadonlySet<string> = new Set<string>(),
): Node<GraphNodeData>[] {
  const collections = nodeCollections(config);
  const ordered = [
    ...collections.triggers,
    ...collections.gates,
    ...collections.effects,
    ...collections.queries,
  ];
  return ordered.map((node, index) => {
    const kind = nodeKindFromType(node.type);
    const position = currentPositions[node.id] ?? layout.nodes[node.id] ?? fallbackPosition(index);
    const activity = nodeActivity[node.id];
    const catalogEntry = getCatalogEntry(catalog, node.type);
    const outputPorts = workflowOutputPortsForNode(node, catalogEntry?.output_ports ?? []);
    const nodeDiagnostics = diagnostics.filter((diagnostic) => diagnostic.node_id === node.id);
    const diagnosticErrorCount = nodeDiagnostics.filter((diagnostic) => (diagnostic.severity ?? "error") !== "warning").length;
    const diagnosticWarningCount = nodeDiagnostics.filter((diagnostic) => (diagnostic.severity ?? "error") === "warning").length;
    const diagnosticCount = diagnosticErrorCount + diagnosticWarningCount;
    return {
      id: node.id,
      type: "workflowNode",
      position,
      selected: selectedNodeIds.includes(node.id),
      data: {
        id: node.id,
        displayName: node.display_name,
        kind,
        type: node.type,
        description: catalogEntry
          ? nodeTypeDescription(catalogEntry)
          : NODE_TYPE_DESCRIPTIONS[node.type] || `${node.type} node.`,
        inputPorts: catalogEntry?.input_ports ?? [],
        outputPorts,
        dominantStatus: dominantNodeStatus(activity),
        statusBadges: nodeStatusBadges(activity),
        diagnosticCount,
        diagnosticErrorCount,
        diagnosticWarningCount,
        hasDiagnostics: diagnosticCount > 0,
        hasErrorDiagnostics: diagnosticErrorCount > 0,
        hasUnsavedChanges: dirtyNodeIds.has(node.id),
        isDisabled: kind === "trigger" && !workflowTriggerEnabled(node),
        isInspectGhost: ghostNodeIds.has(node.id),
        runtimePulse: runtimePulseNodeIds.has(node.id),
      },
    };
  });
}

function edgeId(edge: WorkflowEdge): string {
  return `${edge.source_id}:${edge.source_port ?? ""}->${edge.target_id}`;
}

function wouldCreateCycle(config: WorkflowConfig, sourceId: string, targetId: string): boolean {
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
    if (current === sourceId) return true;
    if (visited.has(current)) continue;
    visited.add(current);
    for (const child of downstream.get(current) ?? []) {
      stack.push(child);
    }
  }
  return false;
}

function buildEdges(config: WorkflowConfig, catalog: WorkflowEditorCatalog): Edge[] {
  return config.edges.map((edge) => {
    const sourceNode = nodeById(config, edge.source_id);
    const sourceCatalogEntry = sourceNode ? getCatalogEntry(catalog, sourceNode.type) : undefined;
    const sourceOutputPorts = sourceNode
      ? workflowOutputPortsForNode(sourceNode, sourceCatalogEntry?.output_ports ?? [])
      : sourceCatalogEntry?.output_ports ?? [];
    const routeClass = workflowRouteClass(edge.source_port);
    return {
      id: edgeId(edge),
      source: edge.source_id,
      sourceHandle: edge.source_port,
      target: edge.target_id,
      label: workflowSwitchEdgeRouteLabel(sourceNode, edge.source_port, sourceOutputPorts),
      animated: Boolean(edge.allow_cycle),
      className: `workflow-edge workflow-edge-route-${routeClass}`,
    };
  });
}

function workflowSwitchEdgeRouteLabel(
  sourceNode: WorkflowNodeConfig | undefined,
  sourcePort: string | null | undefined,
  outputPorts: ReadonlyArray<WorkflowEditorNodePortDefinition>,
): string | undefined {
  const routeLabel = workflowEdgeRouteLabel(sourcePort, outputPorts);
  if (routeLabel !== undefined || sourceNode?.type !== "gate:switch") {
    return routeLabel;
  }
  const normalizedSourcePort = normalizeWorkflowSourcePort(sourcePort);
  return normalizedSourcePort ? humanizeWorkflowRouteKey(normalizedSourcePort) : undefined;
}

function renderStringInput(
  field: WorkflowEditorFieldDefinition,
  value: unknown,
  onChange: (nextValue: string) => void,
  disabled: boolean,
  fallbackPlaceholder: string,
) {
  const optionListId = field.options.length > 0
    ? `workflow-field-options-${field.key.replace(/[^A-Za-z0-9_-]/g, "-")}`
    : null;
  if (field.multiline) {
    return (
      <textarea
        value={typeof value === "string" ? value : ""}
        rows={6}
        placeholder={field.placeholder ?? fallbackPlaceholder}
        onChange={(event) => onChange(event.target.value)}
        disabled={disabled}
      />
    );
  }
  return (
    <>
      <input
        value={typeof value === "string" ? value : ""}
        list={optionListId ?? undefined}
        placeholder={field.placeholder ?? fallbackPlaceholder}
        onChange={(event) => onChange(event.target.value)}
        disabled={disabled}
      />
      {optionListId ? (
        <datalist id={optionListId}>
          {field.options.map((option) => (
            <option key={`${field.key}-${option.value}`} value={option.value} label={option.label} />
          ))}
        </datalist>
      ) : null}
    </>
  );
}

function createStringMapDraftRow(key = "", value = ""): StringMapDraftRow {
  const randomSuffix = Math.random().toString(36).slice(2);
  return { id: `map-row-${Date.now()}-${randomSuffix}`, key, value };
}

function stringMapDraftRows(value: unknown): StringMapDraftRow[] {
  if (!isRecord(value)) return [];
  return Object.entries(value).map(([key, entry]) => createStringMapDraftRow(key, String(entry ?? "")));
}

function stringMapRowsToObject(rows: StringMapDraftRow[]): Record<string, string> {
  const result: Record<string, string> = {};
  for (const row of rows) {
    const key = row.key.trim();
    if (!key) continue;
    result[key] = row.value;
  }
  return result;
}

function createToolArgumentPolicyConstraintDraftRow(): ToolArgumentPolicyConstraintDraftRow {
  return {
    id: `constraint-${Date.now()}-${Math.random().toString(36).slice(2)}`,
    path: "",
    operator: "",
    valueText: "",
    valuesText: "",
    pattern: "",
    caseSensitive: true,
  };
}

function createToolArgumentPolicyRuleDraftRow(): ToolArgumentPolicyRuleDraftRow {
  return {
    id: `rule-${Date.now()}-${Math.random().toString(36).slice(2)}`,
    name: "",
    constraints: [createToolArgumentPolicyConstraintDraftRow()],
  };
}

function createToolArgumentPolicyDraftRow(toolName = ""): ToolArgumentPolicyDraftRow {
  return {
    toolName,
    rules: [],
  };
}

function parseStringMapDraftRows(
  rows: StringMapDraftRow[],
  fieldLabel: string,
  required: boolean,
): Record<string, string> | undefined {
  const result: Record<string, string> = {};
  const seen = new Set<string>();
  for (const row of rows) {
    const key = row.key.trim();
    const value = row.value;
    if (!key && !value.trim()) continue;
    if (!key) throw new Error(`${fieldLabel} contains a value without a key.`);
    if (seen.has(key)) throw new Error(`${fieldLabel} contains duplicate key '${key}'.`);
    seen.add(key);
    result[key] = value;
  }
  if (Object.keys(result).length === 0 && !required) return undefined;
  return result;
}

function directoryOptionLabel(path: string, name: string | undefined, depth: number): string {
  if (path === "root") return "root";
  const prefix = depth > 0 ? `${"  ".repeat(depth)}` : "";
  return name && name !== path ? `${prefix}${name} (${path})` : `${prefix}${path}`;
}

function flattenDirectorySelectOptions(node: DirectoryTreeNode, depth = 0): DirectorySelectOption[] {
  const path = node.directory.path;
  const current = {
    value: path,
    label: directoryOptionLabel(path, node.directory.name, depth),
  };
  return [
    current,
    ...node.children.flatMap((child) => flattenDirectorySelectOptions(child, depth + 1)),
  ];
}

function ensureDirectorySelectOption(
  options: DirectorySelectOption[],
  value: string,
  label: string,
): DirectorySelectOption[] {
  if (!value || options.some((option) => option.value === value)) return options;
  return [...options, { value, label }];
}

function groupToolOption(option: WorkflowFieldOption): string {
  const { value } = option;
  if ([
    "create_local_issue",
    "list_issues",
    "get_issue",
    "set_issue_plan",
    "approve_issue",
    "commit_issue",
    "reject_issue",
    "claim_next_issue",
    "complete_issue",
  ].includes(value)) {
    return "Issue intake";
  }
  if ([
    "create_ado_backlog_item",
    "post_ado_backlog_comment",
    "update_ado_backlog_text",
    "update_ado_backlog_title",
    "update_ado_backlog_tags",
    "query_ado_backlog_items",
    "list_ado_current_iteration_backlog_items",
    "get_ado_backlog_item",
    "get_ado_daily_status_report_data",
    "resolve_ado_identity",
    "query_ado_work_item_activity",
  ].includes(value)) {
    return "Azure DevOps";
  }
  if ([
    "list_pull_requests",
    "get_pull_request",
    "list_ado_pr_comment_activity",
    "list_ado_pr_commit_activity",
    "request_pr_review",
    "post_pr_comment",
    "post_pr_inline_comment",
    "submit_pr_review",
    "post_pr_feedback_reply",
  ].includes(value)) {
    return "Pull requests";
  }
  if (value.startsWith("approval_")) return "Approvals";
  if ([
    "get_agent_status",
    "get_agent_logs",
    "prompt_agent",
    "list_pending_agent_prompts",
    "respond_to_agent",
    "schedule_resume",
    "run_script",
    "copilot_host_auth",
  ].includes(value)) {
    return "Agent control";
  }
  if ([
    "assistant_read_status",
    "assistant_list_agents",
    "assistant_inspect_workflows",
    "assistant_read_config",
    "assistant_upsert_config",
    "assistant_create_workflow",
    "assistant_run_safe_operation",
    "assistant_post_chat_reply",
  ].includes(value)) {
    return "Assistant";
  }
  if ([
    "discord_get_commands",
    "discord_post_status",
    "has_pending_discord_messages",
    "list_unread_discord_messages",
    "list_all_discord_messages",
  ].includes(value)) {
    return "Discord";
  }
  if (["add_message_reaction"].includes(value)) return "Messaging";
  if (["list_contacts", "make_phone_call", "get_phone_call_result"].includes(value)) {
    return "Voice calling";
  }
  if (["mssql_provision_database", "mssql_list_databases", "mssql_drop_database"].includes(value)) {
    return "SQL Server";
  }
  if ([
    "communication_list_attachments",
    "communication_download_attachment",
    "stage_artifact",
    "communication_copy_artifact_to_workspace",
    "communication_stage_workspace_file",
    "discord_post_file",
    "teams_post_file",
    "email_create_draft_with_attachments",
    "email_send_approved_with_attachments",
  ].includes(value)) {
    return "Files";
  }
  if ([
    "teams_get_messages",
    "teams_post_status",
    "teams_meeting_get_events",
    "teams_meeting_speak",
    "teams_meeting_stop",
  ].includes(value)) {
    return "Teams";
  }
  if (["email_list_unread", "email_get_message", "email_create_draft", "email_send_approved"].includes(value)) {
    return "Email";
  }
  if (["has_pending_feedback", "list_unread_feedback", "list_all_feedback"].includes(value)) {
    return "Feedback";
  }
  if (value.startsWith("semantic_")) return "Semantic store";
  return "Other";
}

function groupToolOptions(options: WorkflowFieldOption[]) {
  const groups = new Map<string, WorkflowFieldOption[]>();
  for (const option of options) {
    const group = groupToolOption(option);
    groups.set(group, [...(groups.get(group) ?? []), option]);
  }
  return TOOL_GROUP_ORDER
    .map((name) => ({ name, items: groups.get(name) ?? [] }))
    .filter((group) => group.items.length > 0)
    .map((group) => ({
      ...group,
      items: [...group.items].sort((left, right) => left.label.localeCompare(right.label, undefined, { sensitivity: "base" })),
    }));
}

function cloneConfig(config: WorkflowConfig): WorkflowConfig {
  return JSON.parse(JSON.stringify(config)) as WorkflowConfig;
}

function cloneLayout(layout: WorkflowLayout): WorkflowLayout {
  return JSON.parse(JSON.stringify(layout)) as WorkflowLayout;
}

function cloneNodeConfig(node: WorkflowNodeConfig): WorkflowNodeConfig {
  return JSON.parse(JSON.stringify(node)) as WorkflowNodeConfig;
}

function cloneWorkflowDocumentSurface(document: WorkflowDocumentSurface): WorkflowDocumentSurface {
  return JSON.parse(JSON.stringify({
    config: document.config,
    layout: document.layout,
    graphs: document.graphs,
  })) as WorkflowDocumentSurface;
}

function workflowComparableValue(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map((entry) => workflowComparableValue(entry));
  }
  if (isRecord(value)) {
    const result: Record<string, unknown> = {};
    for (const key of Object.keys(value).sort()) {
      const nextValue = workflowComparableValue(value[key]);
      if (nextValue !== undefined) {
        result[key] = nextValue;
      }
    }
    return result;
  }
  return value;
}

function workflowValuesEqual(left: unknown, right: unknown): boolean {
  return JSON.stringify(workflowComparableValue(left)) === JSON.stringify(workflowComparableValue(right));
}

function normalizeWorkflowEditorDocument<T extends WorkflowDocumentLike>(
  document: T,
  defaultDirectoryPath: string,
): T {
  const backendOutputSchemas = document.output_schemas ?? {};
  const normalizedConfig = normalizeDerivedNodeFields(
    normalizeWorkflowConfigShape(document.config),
    backendOutputSchemas,
  );
  const outputSchemas = deriveWorkflowOutputSchemas(normalizedConfig, backendOutputSchemas);
  const normalizedGraphs = normalizeWorkflowGraphs(
    normalizedConfig,
    document.graphs ?? [],
    defaultDirectoryPath,
  );
  return {
    ...document,
    config: normalizedConfig,
    graphs: normalizedGraphs,
    output_schemas: outputSchemas,
  } as T;
}

function blankWorkflowGeneratorDocument(
  baseDocument: WorkflowEditorDocument | null,
  defaultDirectoryPath: string,
): WorkflowEditorDocument {
  return normalizeWorkflowEditorDocument(
    {
      config_path: baseDocument?.config_path ?? "",
      layout_path: baseDocument?.layout_path ?? "",
      config_exists: Boolean(baseDocument?.config_exists),
      layout_exists: Boolean(baseDocument?.layout_exists),
      document_scope: baseDocument?.document_scope ?? "inline",
      workflow_packs: baseDocument?.workflow_packs ?? [],
      selected_workflow_pack: baseDocument?.selected_workflow_pack ?? null,
      config: emptyConfig(),
      layout: { nodes: {} },
      graphs: [],
      output_schemas: {},
      validation_diagnostics: [],
      revision: baseDocument?.revision ?? "generator-draft",
    },
    defaultDirectoryPath,
  );
}

function uniqueWorkflowNodeId(existingIds: ReadonlySet<string>, requestedId: string): string {
  const base = slugifyGraphId(requestedId || "generated-node");
  let candidate = base;
  let index = 2;
  while (existingIds.has(candidate)) {
    candidate = `${base}-${index}`;
    index += 1;
  }
  return candidate;
}

function uniqueWorkflowGraphId(existingIds: ReadonlySet<string>, requestedId: string): string {
  const base = slugifyGraphId(requestedId || "generated-workflow");
  let candidate = base;
  let index = 2;
  while (existingIds.has(candidate)) {
    candidate = `${base}-${index}`;
    index += 1;
  }
  return candidate;
}

function mergeGeneratedDraftIntoWorkflow(
  baseDocument: WorkflowEditorDocument,
  draftDocument: WorkflowEditorDocument,
  defaultDirectoryPath: string,
): { document: WorkflowEditorDocument; importedNodeIds: string[] } {
  const nextConfig = cloneConfig(baseDocument.config);
  nextConfig.triggers ??= [];
  nextConfig.gates ??= [];
  nextConfig.effects ??= [];
  nextConfig.queries ??= [];
  nextConfig.edges ??= [];

  const existingNodeIds = new Set(workflowNodeIds(nextConfig));
  const idMap = new Map<string, string>();
  const generatedNodes = workflowNodes(draftDocument.config);
  for (const node of generatedNodes) {
    const nextId = uniqueWorkflowNodeId(existingNodeIds, node.id);
    existingNodeIds.add(nextId);
    idMap.set(node.id, nextId);
  }

  const importedNodeIds: string[] = [];
  for (const node of generatedNodes) {
    const kind = nodeKindFromType(node.type);
    const nextId = idMap.get(node.id) ?? node.id;
    const nextNode = rewriteWorkflowNodeConfigBindings(
      {
        ...cloneNodeConfig(node),
        id: nextId,
      },
      idMap,
    );
    nextConfig[kindCollectionKey(kind)].push(nextNode);
    importedNodeIds.push(nextId);
  }

  const nextLayout = cloneLayout(baseDocument.layout);
  const draftPositions = generatedNodes
    .map((node, index) => draftDocument.layout.nodes[node.id] ?? fallbackPosition(index));
  const basePositions = Object.values(nextLayout.nodes);
  const draftMinX = draftPositions.length > 0 ? Math.min(...draftPositions.map((position) => position.x)) : 0;
  const draftMinY = draftPositions.length > 0 ? Math.min(...draftPositions.map((position) => position.y)) : 0;
  const baseMaxX = basePositions.length > 0 ? Math.max(...basePositions.map((position) => position.x)) : null;
  const xOffset = baseMaxX === null ? 0 : baseMaxX + 360 - draftMinX;
  const yOffset = baseMaxX === null ? 0 : 100 - draftMinY;
  generatedNodes.forEach((node, index) => {
    const nextId = idMap.get(node.id);
    if (!nextId) return;
    const position = draftDocument.layout.nodes[node.id] ?? fallbackPosition(index);
    nextLayout.nodes[nextId] = {
      x: position.x + xOffset,
      y: position.y + yOffset,
    };
  });

  const existingEdgeIds = new Set(nextConfig.edges.map(edgeId));
  for (const edge of draftDocument.config.edges ?? []) {
    const sourceId = idMap.get(edge.source_id);
    const targetId = idMap.get(edge.target_id);
    if (!sourceId || !targetId) continue;
    const nextEdge: WorkflowEdge = {
      ...edge,
      source_id: sourceId,
      target_id: targetId,
    };
    const nextEdgeId = edgeId(nextEdge);
    if (existingEdgeIds.has(nextEdgeId)) continue;
    existingEdgeIds.add(nextEdgeId);
    nextConfig.edges.push(nextEdge);
  }

  const existingGraphIds = new Set((baseDocument.graphs ?? []).map((graph) => graph.id));
  const generatedGraphs = (draftDocument.graphs.length > 0
    ? draftDocument.graphs
    : normalizeWorkflowGraphs(draftDocument.config, [], defaultDirectoryPath)
  ).map((graph, index) => {
    const nextId = uniqueWorkflowGraphId(existingGraphIds, graph.id || `generated-workflow-${index + 1}`);
    existingGraphIds.add(nextId);
    return {
      ...graph,
      id: nextId,
      directory_path: graph.directory_path || defaultDirectoryPath,
      node_ids: graph.node_ids.map((nodeId) => idMap.get(nodeId)).filter((nodeId): nodeId is string => Boolean(nodeId)),
    };
  }).filter((graph) => graph.node_ids.length > 0);
  const remappedDraftOutputSchemas = remapGeneratedDraftOutputSchemas(
    draftDocument.output_schemas ?? {},
    idMap,
  );

  const nextDocument = normalizeWorkflowEditorDocument(
    {
      ...baseDocument,
      config: nextConfig,
      layout: nextLayout,
      graphs: [...(baseDocument.graphs ?? []), ...generatedGraphs],
      output_schemas: {
        ...(baseDocument.output_schemas ?? {}),
        ...remappedDraftOutputSchemas,
      },
      validation_diagnostics: [],
      revision: baseDocument.revision,
    },
    defaultDirectoryPath,
  );
  return { document: nextDocument, importedNodeIds };
}

function workflowEditableDocumentsEqual(left: WorkflowDocumentSurface, right: WorkflowDocumentSurface): boolean {
  return workflowValuesEqual(left, right);
}

function workflowUndoDocumentsEqual(left: WorkflowDocumentSurface, right: WorkflowDocumentSurface): boolean {
  return workflowValuesEqual(
    {
      config: left.config,
      layout: left.layout,
      graphs: left.graphs,
    },
    {
      config: right.config,
      layout: right.layout,
      graphs: right.graphs,
    },
  );
}

function dirtyWorkflowNodeIds(
  current: WorkflowEditorDocument | null,
  baseline: WorkflowEditorDocument | null,
  nodeEditOrigins: Record<string, string>,
): string[] {
  if (!current || !baseline) return [];
  const baselineNodesById = new Map(workflowNodes(baseline.config).map((node) => [node.id, node]));
  const dirtyNodeIds: string[] = [];
  for (const node of workflowNodes(current.config)) {
    const originId = nodeEditOrigins[node.id] ?? node.id;
    const baselineNode = baselineNodesById.get(originId);
    if (!baselineNode) {
      dirtyNodeIds.push(node.id);
      continue;
    }
    if (originId !== node.id) {
      dirtyNodeIds.push(node.id);
      continue;
    }
    if (!workflowValuesEqual(node, baselineNode)) {
      dirtyNodeIds.push(node.id);
      continue;
    }
    if (!workflowValuesEqual(current.layout.nodes[node.id], baseline.layout.nodes[originId])) {
      dirtyNodeIds.push(node.id);
    }
  }
  return dirtyNodeIds;
}

function flattenWorkflowNodeConfig(node: WorkflowNodeConfig): WorkflowNodeConfig {
  const clonedNode = cloneNodeConfig(node);
  const nestedConfig = isRecord(clonedNode.config) ? clonedNode.config : undefined;
  if (!nestedConfig) return clonedNode;
  const { config: _config, ...rest } = clonedNode;
  return {
    ...nestedConfig,
    ...rest,
  };
}

function normalizeWorkflowConfigShape(config: WorkflowConfig): WorkflowConfig {
  const nextConfig = cloneConfig(config);
  nextConfig.triggers = (nextConfig.triggers ?? []).map(flattenWorkflowNodeConfig);
  nextConfig.gates = (nextConfig.gates ?? []).map(flattenWorkflowNodeConfig);
  nextConfig.effects = (nextConfig.effects ?? []).map(flattenWorkflowNodeConfig);
  nextConfig.queries = (nextConfig.queries ?? []).map(flattenWorkflowNodeConfig);
  return nextConfig;
}

function isEditableEventTarget(target: EventTarget | null): boolean {
  return (
    target instanceof HTMLInputElement ||
    target instanceof HTMLTextAreaElement ||
    target instanceof HTMLSelectElement ||
    (target instanceof HTMLElement && target.isContentEditable)
  );
}

function createDefaultNode(kind: WorkflowNodeKind, type: string, id: string): WorkflowNodeConfig {
  if (kind === "trigger" && type === "trigger:cron") {
    return { id, display_name: "New trigger", type, expression: "*/15 * * * *" };
  }
  if (kind === "trigger" && type === "trigger:event") {
    return { id, display_name: "Internal event", type, event_type: "custom.operator_event" };
  }
  if (kind === "trigger" && type === "trigger:http:webhook") {
    return {
      id,
      display_name: "Webhook",
      type,
      slug: `webhook-${id}`,
      secret_env: "WEBHOOK_SECRET",
      event_type: `webhook.webhook-${id}`,
    };
  }
  if (kind === "trigger" && type === "trigger:ticket:created") {
    return { id, display_name: "Ticket created", type, directories: ["root"] };
  }
  if (kind === "trigger" && type === "trigger:ticket:changed") {
    return { id, display_name: "Ticket changed", type, directories: ["root"] };
  }
  if (kind === "trigger" && type === "trigger:issue:comment-created") {
    return {
      id,
      display_name: "Issue comment",
      type,
      provider: "ado",
      directories: ["root"],
    };
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
  if (kind === "trigger" && type === "trigger:teams:new-message") {
    return {
      id,
      display_name: "Teams message",
      type,
      targets: [],
      user_ids: [],
    };
  }
  if (kind === "trigger" && type === "trigger:communication:new-message") {
    return {
      id,
      display_name: "Communication message",
      type,
      communication_target_ids: [],
      directory_path: "root",
      author_ids: [],
      case_sensitive: false,
    };
  }
  if (kind === "trigger" && type === "trigger:approval:pending-created") {
    return {
      id,
      display_name: "Pending approval",
      type,
      directory_path: "root",
      action_types: [],
      tool_names: [],
    };
  }
  if (kind === "trigger" && type === "trigger:teams:direct-message") {
    return {
      id,
      display_name: "Teams direct message",
      type,
      sender_user_ids: [],
      message_filter: null,
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
  if (kind === "trigger" && type === "trigger:ci:run:completed") {
    return {
      id,
      display_name: "CI run completed",
      type,
      provider: "github_actions",
      repo: "owner/repo",
      branches: [],
      conclusions: [],
      top: 50,
      include_existing: false,
    };
  }
  if (kind === "trigger" && type === "trigger:git:push") {
    return { id, display_name: "Git push", type, path_glob: "/path/to/git-remotes/*.git" };
  }
  if (kind === "trigger" && type === "trigger:agent:run:completed") {
    return { id, display_name: "Agent run completed", type, vm_user: "agent" };
  }
  if (kind === "gate" && type === "gate:filter") {
    return { id, display_name: "New filter", type, outputs: {} };
  }
  if (kind === "gate" && type === "gate:map") {
    return { id, display_name: "New map", type, outputs: {}, template: {} };
  }
  if (kind === "gate" && type === "gate:aggregate") {
    return {
      id,
      display_name: "Aggregate",
      type,
      items: [],
      metrics: { total: { operation: "sum", path: "value" } },
      outputs: { count: { type: "integer" }, total: { type: "number" } },
    };
  }
  if (kind === "gate" && type === "gate:deserialize") {
    return {
      id,
      display_name: "Deserialize",
      type,
      content: "${{node.payload_text}}",
      format: "auto",
      outputs: {},
    };
  }
  if (kind === "gate" && type === "gate:serialize") {
    return {
      id,
      display_name: "Serialize",
      type,
      value: {},
      format: "json",
      outputs: { content: { type: "string" }, format: { type: "string" } },
    };
  }
  if (kind === "gate" && type === "gate:merge") {
    return { id, display_name: "New merge", type, outputs: {} };
  }
  if (kind === "gate" && type === "gate:switch") {
    return {
      id,
      display_name: "Switch",
      type,
      cases: [
        {
          port: "matched",
          when: { left: "${{trigger-1.status}}", equals: "ready" },
          label: "Matched",
        },
      ],
      default_port: "default",
      outputs: {},
    };
  }
  if (kind === "gate" && type === "gate:debounce") {
    return {
      id,
      display_name: "Debounce",
      type,
      key: "debounce:${{system.execution.event_id}}",
      quiet_period: "PT2M",
      max_delay: "PT10M",
      outputs: {},
    };
  }
  if (kind === "gate" && type === "gate:buffer") {
    return {
      id,
      display_name: "Buffer",
      type,
      key: "buffer:${{system.execution.trace_id}}",
      count: 10,
      timeout: "PT10M",
      outputs: {
        items: { type: "array" },
        count: { type: "integer" },
        release_reason: { type: "string" },
      },
    };
  }
  if (kind === "gate" && type === "gate:dedupe") {
    return {
      id,
      display_name: "Dedupe",
      type,
      key: "dedupe:${{system.execution.event_id}}",
      ttl: "P7D",
      outputs: {},
    };
  }
  if (kind === "gate" && type === "gate:await-agent") {
    return { id, display_name: "Await agent", type, session_id: "session-id", outputs: {} };
  }
  if (kind === "gate" && type === "gate:for") {
    return { id, display_name: "For each item", type, items: [] };
  }
  if (kind === "gate" && type === "gate:while") {
    return {
      id,
      display_name: "While",
      type,
      when: { left: 0, greater_than: 0 },
      max_iterations: 3,
    };
  }
  if (kind === "effect" && type === "effect:agent:prompt") {
    return { id, display_name: "New prompt", type, prompt: "Describe the next action." };
  }
  if (kind === "effect" && type === "effect:agent:exec") {
    return { id, display_name: "Agent exec", type, command: "echo hello" };
  }
  if (kind === "effect" && type === "effect:agent:destroy") {
    return { id, display_name: "Agent destroy", type, session_id: "session-id" };
  }
  if (kind === "effect" && type === "effect:semantic:ensure-collection") {
    return { id, display_name: "Ensure semantic collection", type, collection_name: "agent_memory" };
  }
  if (kind === "effect" && type === "effect:semantic:upsert") {
    return {
      id,
      display_name: "Semantic upsert",
      type,
      collection_name: "agent_memory",
      point_id: SEMANTIC_SAMPLE_POINT_ID,
      text: "Important context to remember.",
      payload: {},
    };
  }
  if (kind === "effect" && type === "effect:semantic:delete") {
    return {
      id,
      display_name: "Semantic delete",
      type,
      collection_name: "agent_memory",
      point_ids: [SEMANTIC_SAMPLE_POINT_ID],
    };
  }
  if (kind === "effect" && type === "effect:state:update") {
    return {
      id,
      display_name: "Update workflow state",
      type,
      namespace: "workflow",
      key: "cursor",
      operation: "set",
      value: {},
    };
  }
  if (kind === "effect" && type === "effect:http:request") {
    return {
      id,
      display_name: "HTTP request",
      type,
      method: "GET",
      url: "https://api.example.test/items",
      allowed_url_patterns: ["*"],
      check: false,
    };
  }
  if (kind === "effect" && type === "effect:runtime:diagnostics-snapshot") {
    return {
      id,
      display_name: "Runtime diagnostics snapshot",
      type,
      since: "24 hours ago",
      include_failed_units: true,
      include_journal: true,
      include_runtime_files: true,
      max_journal_lines_per_unit: 200,
      max_runtime_file_bytes: 120000,
      command_timeout_seconds: 10,
    };
  }
  if (kind === "effect" && type === "effect:vm:create") {
    return { id, display_name: "New VM effect", type };
  }
  if (kind === "effect" && type === "effect:vm:clone-repo") {
    return { id, display_name: "Clone repo into VM", type, repo_source: "/path/to/repo", clone_branch: "main" };
  }
  if (kind === "effect" && type === "effect:vm:bootstrap-python") {
    return {
      id,
      display_name: "Bootstrap Python in VM",
      type,
      host_checkout: "/path/to/repo",
      target_dir: "/home/agent/repo",
    };
  }
  if (kind === "effect" && type === "effect:vm:bootstrap-frontend") {
    return {
      id,
      display_name: "Bootstrap frontend deps in VM",
      type,
      host_checkout: "/path/to/repo",
      install_roots: ["/path/to/repo/Src/Clients"],
    };
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
  if (kind === "effect" && type === "effect:vm:delete-files") {
    return {
      id,
      display_name: "Delete files",
      type,
      paths: ["/tmp/example.txt"],
      missing_ok: true,
    };
  }
  if (kind === "effect" && type === "effect:event:emit") {
    return {
      id,
      display_name: "Emit internal event",
      type,
      event_type: "custom.operator_event",
      payload: {},
    };
  }
  if (kind === "effect" && type === "effect:event:schedule") {
    return {
      id,
      display_name: "Schedule internal event",
      type,
      event_type: "custom.operator_event",
      payload: {},
      delay_seconds: 60,
    };
  }
  if (kind === "effect" && type === "effect:issue:update-metadata") {
    return {
      id,
      display_name: "Update issue metadata",
      type,
      issue_id: "issue-1",
      patch: { "fields.workflow_status": "updated" },
      sync_proxy_cache: true,
    };
  }
  if (kind === "effect" && type === "effect:ado:update-work-item") {
    return {
      id,
      display_name: "ADO update work item",
      type,
      directory_path: "root",
      project: "Project name",
      work_item_id: "12345",
      state: "In Progress",
      expected_states: ["New", "To Do"],
      sync_proxy_cache: true,
    };
  }
  if (
    kind === "effect" &&
    (type === "effect:ado:add-work-item-tag" || type === "effect:ado:remove-work-item-tag")
  ) {
    const isAdd = type === "effect:ado:add-work-item-tag";
    return {
      id,
      display_name: isAdd ? "ADO add work item tag" : "ADO remove work item tag",
      type,
      directory_path: "root",
      project: "Project name",
      work_item_id: "12345",
      tag: "tag-name",
      sync_proxy_cache: true,
    };
  }
  if (kind === "effect" && type === "effect:ado:pipeline-run") {
    return {
      id,
      display_name: "ADO pipeline run",
      type,
      directory_path: "root",
      pipeline_id: 12345,
      variables: {},
      template_parameters: {},
      timeout_seconds: 3600,
      poll_interval_seconds: 10,
      check: true,
    };
  }
  if (kind === "effect" && type === "effect:session:update-metadata") {
    return {
      id,
      display_name: "Update session metadata",
      type,
      metadata: {},
    };
  }
  if (kind === "effect" && type === "effect:session:renew-if-expired") {
    return {
      id,
      display_name: "Renew expired or soon-expiring session",
      type,
      session_id: "session-id",
      extend_by: "1d",
      renew_if_expires_within: "2h",
    };
  }
  if (kind === "effect" && type === "effect:session:revoke-mcp-tool-approval-lease") {
    return {
      id,
      display_name: "Revoke MCP tool approval lease",
      type,
      mcp_tool_approval_lease_id: "mcp-tool-approval-lease-id",
    };
  }
  if (kind === "effect" && type === "effect:artifact:stage") {
    return {
      id,
      display_name: "Stage artifact",
      type,
      session_id: "session-id",
      path: "repos/project/output/report.html",
      destination_path: "reports/report.html",
    };
  }
  if (kind === "effect" && type === "effect:artifact:publish-artifacts") {
    return {
      id,
      display_name: "Publish artifacts",
      type,
      session_id: "session-id",
      key: "runs/artifact-key",
    };
  }
  if (kind === "effect" && type === "effect:artifact:import-published") {
    return {
      id,
      display_name: "Import published artifact",
      type,
      session_id: "session-id",
      key: "runs/artifact-key",
      destination_path: "input",
      overwrite: false,
    };
  }
  if (kind === "effect" && type === "effect:git:promote-agent-branch") {
    return {
      id,
      display_name: "Promote agent branch",
      type,
      host_repo_path: "/path/to/trusted/repo",
      bare_repo_path: "/path/to/git-remotes/sample.git",
      agent_branch: "agents/session-1/topic",
    };
  }
  if (kind === "effect" && type === "effect:pr:create") {
    return { id, display_name: "New PR effect", type, host_repo_path: "/path/to/repo" };
  }
  if (kind === "effect" && type === "effect:pull-request:update") {
    return {
      id,
      display_name: "Update pull request",
      type,
      provider: "azure_devops",
      repo: "repo-name",
      pr_id: "123",
      expected_statuses: ["active"],
    };
  }
  if (
    kind === "effect" &&
    (type === "effect:pull-request:add-tag" || type === "effect:pull-request:remove-tag")
  ) {
    const isAdd = type === "effect:pull-request:add-tag";
    return {
      id,
      display_name: isAdd ? "Add pull request tag" : "Remove pull request tag",
      type,
      provider: "azure_devops",
      repo: "repo-name",
      pr_id: "123",
      tag: "tag-name",
    };
  }
  if (kind === "query" && type === "query:vm:run-state") {
    return { id, display_name: "VM run state", type };
  }
  if (kind === "query" && type === "query:git:pr-note") {
    return {
      id,
      display_name: "Git PR note",
      type,
      host_repo_path: "/path/to/trusted/repo",
      local_branch: "review/session-1/topic",
    };
  }
  if (kind === "query" && type === "query:session-context") {
    return {
      id,
      display_name: "Session context",
      type,
    };
  }
  if (kind === "query" && type === "query:state:get") {
    return {
      id,
      display_name: "Read workflow state",
      type,
      namespace: "workflow",
      key: "cursor",
    };
  }
  if (kind === "query" && type === "query:agent:artifact-text") {
    return {
      id,
      display_name: "Agent artifact text",
      type,
      path: "report/summary.txt",
    };
  }
  if (kind === "query" && type === "query:issue:get") {
    return {
      id,
      display_name: "Issue get",
      type,
      issue_id: "issue-1",
    };
  }
  if (kind === "query" && type === "query:ado:work-items") {
    return {
      id,
      display_name: "ADO work items",
      type,
      top: 50,
      skip: 0,
    };
  }
  if (kind === "query" && type === "query:pull-request:list") {
    return {
      id,
      display_name: "Pull request list",
      type,
      provider: "azure_devops",
      repo: "repo-name",
      status: "active",
      top: 50,
      skip: 0,
    };
  }
  if (kind === "query" && type === "query:communication:messages") {
    return {
      id,
      display_name: "Communication messages",
      type,
      communication_target_id: "communication-target-id",
      limit: 50,
    };
  }
  if (kind === "query" && type === "query:pull-request:get") {
    return {
      id,
      display_name: "Pull request get",
      type,
      provider: "azure_devops",
      repo: "repo-name",
      pr_id: "123",
      include_reviewers: false,
      include_commits: false,
    };
  }
  if (kind === "query" && type === "query:semantic:store-status") {
    return { id, display_name: "Semantic store status", type, collection_name: "agent_memory" };
  }
  if (kind === "query" && type === "query:semantic:search") {
    return {
      id,
      display_name: "Semantic search",
      type,
      collection_name: "agent_memory",
      query_text: "What context is relevant?",
      limit: 10,
    };
  }
  if (kind === "query" && type === "query:semantic:get") {
    return {
      id,
      display_name: "Semantic get",
      type,
      collection_name: "agent_memory",
      point_ids: [SEMANTIC_SAMPLE_POINT_ID],
    };
  }
  return { id, display_name: "New node", type };
}

function slugifyNodeType(type: string): string {
  return type.replace(/[^a-zA-Z0-9]+/g, "-").replace(/^-+|-+$/g, "").toLowerCase() || "node";
}

function workflowPackReferenceValue(packReference: unknown): string {
  if (typeof packReference === "string") return packReference;
  if (packReference && typeof packReference === "object" && "reference" in packReference) {
    return String((packReference as { reference?: unknown }).reference ?? "");
  }
  return String(packReference ?? "");
}

function workflowPackSlugFromReference(packReference: unknown): string | null {
  const reference = workflowPackReferenceValue(packReference).trim();
  if (!reference) return null;
  const packFileName = reference.split(/[\\/]/).pop() ?? reference;
  const withoutJson = packFileName.replace(/\.json$/i, "");
  const withoutVersion = withoutJson.replace(/\.v[0-9]+(?:-example)?$/i, "");
  return slugifyGraphId(withoutVersion || withoutJson);
}

function selectedWorkflowPackSlug(
  config: WorkflowConfig,
  selectedWorkflowPackReference?: string | null,
): string | null {
  const selectedPackSlug = workflowPackSlugFromReference(selectedWorkflowPackReference);
  if (selectedPackSlug) return selectedPackSlug;
  const workflowPacks = config.workflow_packs;
  if (!Array.isArray(workflowPacks) || workflowPacks.length !== 1) return null;
  return workflowPackSlugFromReference(workflowPacks[0]);
}

function generateNodeId(
  config: WorkflowConfig,
  kind: WorkflowNodeKind,
  type: string,
  label?: string | null,
  selectedWorkflowPackReference?: string | null,
): string {
  const collections = nodeCollections(config);
  const existingIds = new Set(
    [...collections.triggers, ...collections.gates, ...collections.effects, ...collections.queries].map(
      (node) => node.id,
    ),
  );
  const labelPart = slugifyGraphId(label?.trim() || slugifyNodeType(type).replace(`${kind}-`, ""));
  const packSlug = selectedWorkflowPackSlug(config, selectedWorkflowPackReference);
  const base = [packSlug, kind, labelPart].filter(Boolean).join("-");
  let index = 2;
  let candidate = base;
  while (existingIds.has(candidate)) {
    candidate = `${base}-${index}`;
    index += 1;
  }
  return candidate;
}

function nodeById(config: WorkflowConfig, nodeId: string): WorkflowNodeConfig | undefined {
  const collections = nodeCollections(config);
  return [...collections.triggers, ...collections.gates, ...collections.effects, ...collections.queries].find(
    (node) => node.id === nodeId,
  );
}

function validateConnection(
  config: WorkflowConfig,
  catalog: WorkflowEditorCatalog,
  sourceId: string,
  targetId: string,
  sourcePort?: string | null,
): string | null {
  if (!sourceId || !targetId) return "Edge endpoints are required.";
  if (sourceId === targetId) return "Nodes cannot connect to themselves.";
  if (
    config.edges.some(
      (edge) => edge.source_id === sourceId && edge.target_id === targetId && (edge.source_port ?? "") === (sourcePort ?? ""),
    )
  ) {
    return "That edge already exists.";
  }
  const sourceNode = nodeById(config, sourceId);
  const targetNode = nodeById(config, targetId);
  if (!sourceNode) return "Source node does not exist.";
  if (!targetNode) return "Target node does not exist.";
  const sourceCatalogEntry = getCatalogEntry(catalog, sourceNode.type);
  const allowedPorts = workflowOutputPortsForNode(sourceNode, sourceCatalogEntry?.output_ports ?? []);
  if (sourceNode.type === "gate:switch" && allowedPorts.length === 0) {
    return `Node ${sourceId} does not have any configured output ports.`;
  }
  if ((sourceNode.type === "gate:switch" || allowedPorts.length > 0) && !allowedPorts.some((port) => port.key === sourcePort)) {
    return `Node ${sourceId} requires selecting one of its named output ports.`;
  }
  const limit = inboundLimit(targetNode);
  const inboundCount = config.edges.filter((edge) => edge.target_id === targetId).length;
  if (limit !== null && inboundCount >= limit) {
    return `Node ${targetId} already has the maximum supported inbound connections.`;
  }
  if (wouldCreateCycle(config, sourceId, targetId)) {
    if (
      (sourceNode.type === "gate:for" || sourceNode.type === "gate:while")
      && sourcePort === "body"
    ) return null;
    if (targetNode.type === "gate:for" || targetNode.type === "gate:while") return null;
    return "That edge would create a cycle.";
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
    throw new ApiError(String(payload.error), response.status, payload);
  }
  return payload as T;
}

function currentPageVisible(): boolean {
  return typeof globalThis.document === "undefined" || globalThis.document.visibilityState !== "hidden";
}

function workflowCatalogPath(directoryPath: string): string {
  const params = new URLSearchParams();
  const trimmedDirectoryPath = directoryPath.trim();
  if (trimmedDirectoryPath) params.set("directory_path", trimmedDirectoryPath);
  const query = params.toString();
  return query ? `/api/workflow-editor/catalog?${query}` : "/api/workflow-editor/catalog";
}

function workflowEditorDocumentPath(workflowPack?: string | null): string {
  const params = new URLSearchParams();
  const trimmedWorkflowPack = (workflowPack ?? "").trim();
  if (trimmedWorkflowPack) params.set("workflow_pack", trimmedWorkflowPack);
  const query = params.toString();
  return query ? `/api/workflow-editor/document?${query}` : "/api/workflow-editor/document";
}

function currentPositionsFromNodes(nodes: Node<GraphNodeData>[]): Record<string, { x: number; y: number }> {
  return Object.fromEntries(nodes.map((node) => [node.id, node.position]));
}

function workflowGraphLabels(
  graphs: ReadonlyArray<WorkflowGraphMetadata>,
  nodes: ReadonlyArray<Node<GraphNodeData>>,
  viewport: Viewport,
): WorkflowGraphLabel[] {
  const nodesById = new Map(nodes.map((node) => [node.id, node]));
  return graphs.flatMap((graph) => {
    const graphNodes = graph.node_ids
      .map((nodeId) => nodesById.get(nodeId))
      .filter((node): node is Node<GraphNodeData> => Boolean(node));
    if (graphNodes.length === 0) return [];
    const bounds = graphNodes.map((node) => nodeBounds(node));
    const left = Math.min(...bounds.map((bound) => bound.left));
    const top = Math.min(...bounds.map((bound) => bound.top));
    const bottom = Math.max(...bounds.map((bound) => bound.bottom));
    return [
      {
        graph,
        left: left * viewport.zoom + viewport.x - 24,
        top: ((top + bottom) / 2) * viewport.zoom + viewport.y,
      },
    ];
  });
}

function decorateWorkflowTutorialNodes(
  nodes: Node<GraphNodeData>[],
  step: WorkflowTutorialStep | null,
): Node<GraphNodeData>[] {
  if (!step) return nodes;
  const targetNodeId = workflowTutorialTargetNodeId(step.target);
  const connectSourceNodeId = step.requiredAction.kind === "connect" ? step.requiredAction.sourceNodeId : null;
  const connectTargetNodeId = step.requiredAction.kind === "connect" ? step.requiredAction.targetNodeId : null;
  return nodes.map((node) => {
    const isTarget = targetNodeId !== null && node.id === targetNodeId;
    const isConnectParticipant =
      connectSourceNodeId !== null &&
      connectTargetNodeId !== null &&
      (node.id === connectSourceNodeId || node.id === connectTargetNodeId);
    return {
      ...node,
      style: {
        ...(node.style ?? {}),
        visibility: "visible",
      },
      data: {
        ...node.data,
        tutorialHighlight: (isTarget || isConnectParticipant) && step.highlightMode !== "none",
        tutorialBlocked: targetNodeId !== null ? !(isTarget || isConnectParticipant) : false,
        tutorialHighlightMode: step.highlightMode,
      },
    };
  });
}

function tutorialNodeLabel(nodeId: string): string {
  switch (nodeId) {
    case "tutorial-trigger":
      return "Agent run completed";
    case "tutorial-effect":
      return "Prompt agent";
    default:
      return nodeId;
  }
}

function tutorialFieldLabel(fieldKey: string): string {
  switch (fieldKey) {
    case "prompt":
      return "Prompt";
    default:
      return fieldKey;
  }
}

function tutorialRequiredActionLabel(action: WorkflowTutorialRequiredAction): string {
  switch (action.kind) {
    case "next":
      return "Next";
    case "select-node":
      return `Select ${tutorialNodeLabel(action.nodeId)}`;
    case "add-node":
      return `Add ${action.nodeType}`;
    case "connect":
      return `Connect ${tutorialNodeLabel(action.sourceNodeId)} to ${tutorialNodeLabel(action.targetNodeId)}`;
    case "bind-field":
      return `Bind ${tutorialFieldLabel(action.fieldKey)}`;
    case "reset":
      return "Reset";
    default:
      return "Continue";
  }
}

function tutorialRequiredActionDescription(action: WorkflowTutorialRequiredAction): string {
  switch (action.kind) {
    case "next":
      return "Press Next to continue.";
    case "select-node":
      return `Click ${tutorialNodeLabel(action.nodeId)} to continue.`;
    case "add-node":
      return `Add ${action.nodeType} at the tutorial position.`;
    case "connect":
      return `Right-click ${tutorialNodeLabel(action.sourceNodeId)}, choose Completed, then click ${tutorialNodeLabel(action.targetNodeId)}.`;
    case "bind-field":
      return `Bind ${tutorialFieldLabel(action.fieldKey)} to the trigger's Completed output.`;
    case "reset":
      return "Reset the tutorial to practice again.";
    default:
      return "Follow the tutorial prompt to continue.";
  }
}

function isMeaningfulViewport(viewport: Viewport): boolean {
  return Math.abs(viewport.x) > 0.5 || Math.abs(viewport.y) > 0.5 || Math.abs(viewport.zoom - 1) > 0.01;
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), max);
}

function clampTutorialBubblePosition(
  preferred: { left: number; top: number },
  size: TutorialBubbleSize,
  canvasSize: { width: number; height: number },
  padding = 16,
): { left: number; top: number } {
  const maxLeft = Math.max(padding, canvasSize.width - size.width - padding);
  const maxTop = Math.max(padding, canvasSize.height - size.height - padding);
  return {
    left: clamp(preferred.left, padding, maxLeft),
    top: clamp(preferred.top, padding, maxTop),
  };
}

function tutorialBubblePosition(
  step: WorkflowTutorialStep,
  nodes: ReadonlyArray<Node<GraphNodeData>>,
  viewport: Viewport,
  bubbleSize: TutorialBubbleSize,
  canvasSize: { width: number; height: number },
): { left: number; top: number } {
  const padding = 16;
  const gap = 20;
  if (step.target.kind === "canvas") {
    return clampTutorialBubblePosition(
      {
        left: (canvasSize.width - bubbleSize.width) / 2,
        top: (canvasSize.height - bubbleSize.height) / 2,
      },
      bubbleSize,
      canvasSize,
      padding,
    );
  }
  const targetNode = nodes.find((node) => node.id === step.target.nodeId);
  if (!targetNode) {
    return clampTutorialBubblePosition(
      {
        left: (canvasSize.width - bubbleSize.width) / 2,
        top: (canvasSize.height - bubbleSize.height) / 2,
      },
      bubbleSize,
      canvasSize,
      padding,
    );
  }
  const bounds = nodeBounds(targetNode);
  const left = bounds.left * viewport.zoom + viewport.x;
  const top = bounds.top * viewport.zoom + viewport.y;
  const right = bounds.right * viewport.zoom + viewport.x;
  const bottom = bounds.bottom * viewport.zoom + viewport.y;
  const centerX = (left + right) / 2;
  const centerY = (top + bottom) / 2;
  let preferredLeft = centerX - bubbleSize.width / 2;
  let preferredTop = centerY - bubbleSize.height / 2;
  switch (step.bubblePlacement) {
    case "left":
      preferredLeft = left - bubbleSize.width - gap;
      break;
    case "right":
      preferredLeft = right + gap;
      break;
    case "top":
      preferredTop = top - bubbleSize.height - gap;
      break;
    case "bottom":
      preferredTop = bottom + gap;
      break;
    case "center":
      preferredLeft = (canvasSize.width - bubbleSize.width) / 2;
      preferredTop = (canvasSize.height - bubbleSize.height) / 2;
      break;
    default:
      break;
  }
  if (step.bubblePlacement === "left" || step.bubblePlacement === "right") {
    preferredTop = centerY - bubbleSize.height / 2;
  }
  if (step.bubblePlacement === "top" || step.bubblePlacement === "bottom") {
    preferredLeft = centerX - bubbleSize.width / 2;
  }
  return clampTutorialBubblePosition({ left: preferredLeft, top: preferredTop }, bubbleSize, canvasSize, padding);
}

function formatRuntimeTimestamp(value: string | null | undefined): string {
  if (!value) return "Unknown time";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function summarizeRunNode(activity: WorkflowRunNodeActivity): string {
  const countLabel = activity.count > 1 ? ` x${activity.count}` : "";
  const errorLabel = activity.error ? `: ${activity.error}` : "";
  return `${activity.node_id} ${activity.status}${countLabel}${errorLabel}`;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isExactBindingValue(value: unknown): value is string {
  return typeof value === "string" && BINDING_EXACT_RE.test(value);
}

function hasBindingToken(value: unknown): boolean {
  return typeof value === "string" && value.includes("${{");
}

function formatJson(value: unknown, fallback = "{}"): string {
  if (value === undefined) return fallback;
  return JSON.stringify(value, null, 2);
}

function isSessionListMetadataFieldKey(
  key: string,
): key is (typeof SESSION_LIST_METADATA_FIELD_KEYS)[number] {
  return SESSION_LIST_METADATA_FIELD_KEYS.includes(
    key as (typeof SESSION_LIST_METADATA_FIELD_KEYS)[number],
  );
}

function createSessionListMetadataRuleDraftRow(
  kind: SessionListMetadataRuleKind = "equals",
  key = "",
  value = "",
): SessionListMetadataRuleDraftRow {
  const randomSuffix = Math.random().toString(36).slice(2);
  return {
    id: `metadata-rule-${Date.now()}-${randomSuffix}`,
    kind,
    key,
    value,
  };
}

function sessionListMetadataEqualsRuleValueText(value: unknown): string {
  if (typeof value === "string") {
    try {
      JSON.parse(value);
      return JSON.stringify(value);
    } catch {
      return value;
    }
  }
  try {
    const text = JSON.stringify(value);
    return text === undefined ? String(value ?? "") : text;
  } catch {
    return String(value ?? "");
  }
}

function sessionListMetadataPrefixRuleValueText(value: unknown): string {
  if (typeof value === "string") return value;
  try {
    const text = JSON.stringify(value);
    return text === undefined ? String(value ?? "") : text;
  } catch {
    return String(value ?? "");
  }
}

function sessionListMetadataRuleValue(value: string): unknown {
  const trimmed = value.trim();
  if (!trimmed) return "";
  try {
    return JSON.parse(trimmed) as unknown;
  } catch {
    return value;
  }
}

function sessionListMetadataExistsFlagIsPresent(value: unknown): boolean {
  if (value === true) return true;
  if (typeof value === "string") {
    return ["true", "1", "yes", "on"].includes(value.trim().toLowerCase());
  }
  return false;
}

function appendSessionListMetadataEqualsRuleRows(
  rows: SessionListMetadataRuleDraftRow[],
  selector: string,
  value: unknown,
) {
  if (isRecord(value) && Object.keys(value).length > 0) {
    for (const [nestedKey, nestedValue] of Object.entries(value)) {
      const nestedSelector = selector ? `${selector}.${nestedKey}` : nestedKey;
      appendSessionListMetadataEqualsRuleRows(rows, nestedSelector, nestedValue);
    }
    return;
  }
  rows.push(createSessionListMetadataRuleDraftRow(
    "equals",
    selector,
    sessionListMetadataEqualsRuleValueText(value),
  ));
}

function sessionListMetadataRuleDraftRowsFromValues(values: Record<string, unknown>): {
  rows: SessionListMetadataRuleDraftRow[];
  supported: boolean;
} {
  const rows: SessionListMetadataRuleDraftRow[] = [];
  const metadataEquals = values.metadata_equals;
  const metadataExists = values.metadata_exists;
  const metadataPrefix = values.metadata_prefix;
  if (
    isExactBindingValue(metadataEquals) ||
    isExactBindingValue(metadataExists) ||
    isExactBindingValue(metadataPrefix)
  ) {
    return { rows, supported: false };
  }
  if (metadataEquals !== undefined && metadataEquals !== null && !isRecord(metadataEquals)) {
    return { rows, supported: false };
  }
  if (metadataPrefix !== undefined && metadataPrefix !== null && !isRecord(metadataPrefix)) {
    return { rows, supported: false };
  }
  if (isRecord(metadataEquals)) {
    for (const [key, value] of Object.entries(metadataEquals)) {
      appendSessionListMetadataEqualsRuleRows(rows, key, value);
    }
  }
  if (typeof metadataExists === "string") {
    const key = metadataExists.trim();
    if (key) rows.push(createSessionListMetadataRuleDraftRow("exists", key));
  } else if (Array.isArray(metadataExists)) {
    for (const item of metadataExists) {
      const key = String(item ?? "").trim();
      if (key) rows.push(createSessionListMetadataRuleDraftRow("exists", key));
    }
  } else if (isRecord(metadataExists)) {
    for (const [key, flag] of Object.entries(metadataExists)) {
      if (!sessionListMetadataExistsFlagIsPresent(flag)) {
        return { rows: [], supported: false };
      }
      if (key.trim()) rows.push(createSessionListMetadataRuleDraftRow("exists", key));
    }
  } else if (metadataExists !== undefined && metadataExists !== null) {
    return { rows: [], supported: false };
  }
  if (isRecord(metadataPrefix)) {
    for (const [key, value] of Object.entries(metadataPrefix)) {
      rows.push(createSessionListMetadataRuleDraftRow(
        "prefix",
        key,
        sessionListMetadataPrefixRuleValueText(value),
      ));
    }
  }
  return { rows, supported: true };
}

function sessionListMetadataRuleValues(rows: SessionListMetadataRuleDraftRow[]): {
  metadata_equals?: Record<string, unknown>;
  metadata_exists?: string[];
  metadata_prefix?: Record<string, string>;
} {
  const metadataEquals: Record<string, unknown> = {};
  const metadataExists: string[] = [];
  const metadataPrefix: Record<string, string> = {};
  for (const row of rows) {
    const key = row.key.trim();
    if (!key) continue;
    if (row.kind === "equals") {
      metadataEquals[key] = sessionListMetadataRuleValue(row.value);
    } else if (row.kind === "exists") {
      metadataExists.push(key);
    } else if (row.kind === "prefix") {
      metadataPrefix[key] = row.value;
    }
  }
  return {
    ...(Object.keys(metadataEquals).length > 0 ? { metadata_equals: metadataEquals } : {}),
    ...(metadataExists.length > 0 ? { metadata_exists: metadataExists } : {}),
    ...(Object.keys(metadataPrefix).length > 0 ? { metadata_prefix: metadataPrefix } : {}),
  };
}

function workflowRunDetailLookupQuery(value: string, kind: WorkflowRunDetailLookupKind): URLSearchParams {
  const normalized = value.trim();
  const query = new URLSearchParams();
  if (kind !== "auto") {
    query.set(kind, normalized);
  } else if (normalized.startsWith("comm_")) {
    query.set("artifact_id", normalized);
  } else if (normalized.startsWith("trace")) {
    query.set("trace_id", normalized);
  } else if (normalized.startsWith("session")) {
    query.set("session_id", normalized);
  } else {
    query.set("event_id", normalized);
  }
  return query;
}

function workflowRunLookupFromHash(): { kind: WorkflowRunDetailLookupKind; value: string } | null {
  if (typeof window === "undefined") {
    return null;
  }
  const hash = window.location.hash;
  const runId = hashQueryParam(hash, "run_id");
  if (runId) {
    return { kind: "run_id", value: runId };
  }
  const eventId = hashQueryParam(hash, "event_id");
  if (eventId) {
    return { kind: "event_id", value: eventId };
  }
  const traceId = hashQueryParam(hash, "trace_id");
  if (traceId) {
    return { kind: "trace_id", value: traceId };
  }
  return null;
}

function parseWorkflowRunSearchScope(value: string): { kind: "graph" | "node"; id: string } | null {
  const separatorIndex = value.indexOf(":");
  if (separatorIndex < 0) return null;
  const kind = value.slice(0, separatorIndex);
  const id = value.slice(separatorIndex + 1).trim();
  if (!id || (kind !== "graph" && kind !== "node")) return null;
  return { kind, id };
}

function workflowRunSearchQuery(
  scopeValue: string,
  options: {
    withoutSession: boolean;
    includeCompleted: boolean;
    limit: number;
  },
): URLSearchParams | null {
  const { withoutSession, includeCompleted, limit } = options;
  const scope = parseWorkflowRunSearchScope(scopeValue);
  if (!scope) return null;
  const query = new URLSearchParams({
    without_session: withoutSession ? "true" : "false",
    include_completed: includeCompleted ? "true" : "false",
    limit: String(limit),
  });
  query.set(scope.kind === "graph" ? "graph_id" : "node_id", scope.id);
  return query;
}

function summarizeRunSearchStatuses(counts: Record<string, number>): string {
  const entries = Object.entries(counts).filter(([, count]) => count > 0);
  if (entries.length === 0) return "No recorded node status";
  return entries.map(([status, count]) => `${status} ${count}`).join(", ");
}

function workflowRunStateLabel(runState: string | null | undefined, fallbackState: string | null | undefined = null): string {
  return runState ?? fallbackState ?? "active";
}

function workflowRunStateClassName(
  runState: string | null | undefined,
  fallbackState: string | null | undefined = null,
): string {
  const state = workflowRunStateLabel(runState, fallbackState);
  return state === "errored" ? "failed" : state;
}

function workflowRunDetailGraphNodes(detail: WorkflowRunDetail): Array<WorkflowNodeConfig & { kind: WorkflowNodeKind }> {
  const snapshot = detail.graph.snapshot;
  const source = snapshot
    ? {
        triggers: snapshot.triggers ?? [],
        gates: snapshot.gates ?? [],
        effects: snapshot.effects ?? [],
        queries: snapshot.queries ?? [],
      }
    : detail.graph.config;
  return [
    ...(source.triggers ?? []).map((node) => ({ ...node, kind: "trigger" as const })),
    ...(source.gates ?? []).map((node) => ({ ...node, kind: "gate" as const })),
    ...(source.effects ?? []).map((node) => ({ ...node, kind: "effect" as const })),
    ...(source.queries ?? []).map((node) => ({ ...node, kind: "query" as const })),
  ].filter((node) => Boolean(node.id));
}

function workflowRunDetailGraphEdges(detail: WorkflowRunDetail): WorkflowEdge[] {
  const rawEdges = detail.graph.snapshot?.edges ?? detail.graph.config.edges ?? [];
  return rawEdges.flatMap((edge) => {
    const rawEdge = edge as Record<string, unknown>;
    const source = typeof rawEdge.source_id === "string" ? rawEdge.source_id : typeof rawEdge.source === "string" ? rawEdge.source : "";
    const target = typeof rawEdge.target_id === "string" ? rawEdge.target_id : typeof rawEdge.target === "string" ? rawEdge.target : "";
    if (!source || !target) return [];
    return [{
      source_id: source,
      target_id: target,
      source_port: typeof rawEdge.source_port === "string" ? rawEdge.source_port : undefined,
    }];
  });
}

function workflowRunDetailNodeById(detail: WorkflowRunDetail): Map<string, WorkflowRunNodeDetail> {
  return new Map(detail.nodes.map((node) => [node.node_id, node]));
}

function workflowRunDetailVisitedNodeIds(detail: WorkflowRunDetail): Set<string> {
  const nodeIds = new Set<string>();
  detail.nodes.forEach((node) => {
    if (node.node_id) nodeIds.add(node.node_id);
  });
  detail.active_nodes.forEach((activity) => {
    if (activity.node_id) nodeIds.add(activity.node_id);
  });
  if (detail.source_node_id) nodeIds.add(detail.source_node_id);
  if (detail.trigger_event?.trigger_id) nodeIds.add(detail.trigger_event.trigger_id);
  if (detail.failed_node_id) nodeIds.add(detail.failed_node_id);
  return nodeIds;
}

function workflowRunDetailIsFailed(detail: WorkflowRunDetail): boolean {
  if (detail.run_state === "error_routed") return false;
  return Boolean(
    detail.run_state === "errored" ||
      detail.delivery_state === "failed" ||
      detail.failed_node_id ||
      detail.failed_node_status === "failed",
  );
}

function coerceWorkflowGraphMetadataList(value: unknown): WorkflowGraphMetadata[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((entry) => {
    if (!isRecord(entry)) return [];
    const id = typeof entry.id === "string" ? entry.id.trim() : "";
    const name = typeof entry.name === "string" ? entry.name.trim() : "";
    const nodeIds = Array.isArray(entry.node_ids)
      ? entry.node_ids.filter((nodeId): nodeId is string => typeof nodeId === "string" && Boolean(nodeId.trim()))
      : [];
    if (!id || !name || nodeIds.length === 0) return [];
    const directoryPath = typeof entry.directory_path === "string" ? entry.directory_path.trim() : "";
    const legacyDirectoryPaths = Array.isArray(entry.directory_paths)
      ? entry.directory_paths
      : typeof entry.directory_paths === "string"
        ? [entry.directory_paths]
        : [];
    const normalizedDirectoryPath =
      directoryPath ||
      legacyDirectoryPaths
        .map((directoryPathValue) => (typeof directoryPathValue === "string" ? directoryPathValue.trim() : ""))
        .find((directoryPathValue) => Boolean(directoryPathValue)) ||
      "root";
    return [{
      id,
      name,
      description: typeof entry.description === "string" ? entry.description : null,
      directory_path: normalizedDirectoryPath,
      user_ids: Array.isArray(entry.user_ids)
        ? entry.user_ids.filter((userId): userId is string => typeof userId === "string" && Boolean(userId.trim()))
        : [],
      node_ids: nodeIds,
    }];
  });
}

function workflowRunDetailGraphMetadata(detail: WorkflowRunDetail): WorkflowGraphMetadata[] {
  return [
    ...coerceWorkflowGraphMetadataList(detail.graph.snapshot?.workflow_graphs),
    ...coerceWorkflowGraphMetadataList(detail.graph.config.workflow_graphs),
    ...coerceWorkflowGraphMetadataList(detail.graph.config.graphs),
  ];
}

function workflowRunDetailSameGraphNodeIds(detail: WorkflowRunDetail): Set<string> {
  const allNodeIds = new Set(workflowRunDetailGraphNodes(detail).map((node) => node.id));
  const anchorNodeIds = workflowRunDetailVisitedNodeIds(detail);
  return sameGraphNodeIdsForRunAnchors(
    allNodeIds,
    workflowRunDetailGraphEdges(detail),
    workflowRunDetailGraphMetadata(detail),
    anchorNodeIds,
  );
}

function workflowRunDetailDisplayNodeIds(detail: WorkflowRunDetail): Set<string> {
  const sameGraphNodeIds = workflowRunDetailSameGraphNodeIds(detail);
  if (workflowRunDetailIsFailed(detail)) return sameGraphNodeIds;
  const visitedNodeIds = workflowRunDetailVisitedNodeIds(detail);
  const displayedNodeIds = new Set([...sameGraphNodeIds].filter((nodeId) => visitedNodeIds.has(nodeId)));
  return displayedNodeIds.size > 0 ? displayedNodeIds : sameGraphNodeIds;
}

function stripWorkflowRunGraphNodeKind(node: WorkflowNodeConfig & { kind: WorkflowNodeKind }): WorkflowNodeConfig {
  const { kind: _kind, ...nodeConfig } = node;
  return nodeConfig;
}

function workflowRunDetailConfig(detail: WorkflowRunDetail, displayNodeIds: ReadonlySet<string>): WorkflowConfig {
  const nodes = workflowRunDetailGraphNodes(detail).filter((node) => displayNodeIds.has(node.id));
  return {
    triggers: nodes.filter((node) => node.kind === "trigger").map(stripWorkflowRunGraphNodeKind),
    gates: nodes.filter((node) => node.kind === "gate").map(stripWorkflowRunGraphNodeKind),
    effects: nodes.filter((node) => node.kind === "effect").map(stripWorkflowRunGraphNodeKind),
    queries: nodes.filter((node) => node.kind === "query").map(stripWorkflowRunGraphNodeKind),
    edges: workflowRunDetailGraphEdges(detail).filter(
      (edge) => displayNodeIds.has(edge.source_id) && displayNodeIds.has(edge.target_id),
    ),
  };
}

function workflowRunDetailLayout(
  config: WorkflowConfig,
  baseLayout: WorkflowLayout | undefined,
  currentPositions: Record<string, { x: number; y: number }>,
): WorkflowLayout {
  return {
    nodes: Object.fromEntries(
      workflowNodes(config).map((node, index) => [
        node.id,
        currentPositions[node.id] ?? baseLayout?.nodes[node.id] ?? fallbackPosition(index),
      ]),
    ),
  };
}

function preferredWorkflowRunDetailNodeId(detail: WorkflowRunDetail): string | null {
  const displayNodeIds = workflowRunDetailDisplayNodeIds(detail);
  const candidates = [
    detail.failed_node_id,
    [...detail.nodes].reverse()[0]?.node_id,
    detail.active_nodes[0]?.node_id,
    detail.source_node_id,
    detail.trigger_event?.trigger_id,
  ];
  return candidates.find((nodeId): nodeId is string => Boolean(nodeId && displayNodeIds.has(nodeId))) ?? null;
}

function objectHasData(value: Record<string, unknown> | null | undefined): boolean {
  return Boolean(value && Object.keys(value).length > 0);
}

function truncateRunDetailText(value: string, maxLength = 120): string {
  const trimmed = value.trim();
  if (trimmed.length <= maxLength) {
    return trimmed;
  }
  return `${trimmed.slice(0, maxLength - 1).trimEnd()}…`;
}

function firstNonEmptyString(...values: Array<string | null | undefined>): string | null {
  for (const value of values) {
    const trimmed = value?.trim();
    if (trimmed) {
      return trimmed;
    }
  }
  return null;
}

function operatorSteeringKey(sessionId: string | null | undefined, dispatchId: string | null | undefined): string | null {
  const normalizedSessionId = sessionId?.trim();
  const normalizedDispatchId = dispatchId?.trim();
  if (!normalizedSessionId || !normalizedDispatchId) return null;
  return `${normalizedSessionId}::${normalizedDispatchId}`;
}

function operatorSteeringQuery(sessionId: string, dispatchId: string): string {
  const params = new URLSearchParams();
  params.set("session_id", sessionId);
  params.set("dispatch_id", dispatchId);
  return params.toString();
}

function normalizeAvailabilityState(value: string | null | undefined): string {
  const normalized = value?.trim().toLowerCase();
  if (!normalized) {
    return "missing";
  }
  if (normalized === "available" || normalized === "recorded" || normalized === "present") {
    return "available";
  }
  return normalized;
}

function availabilityFlag(value: boolean | string | null | undefined, fallback: boolean): boolean {
  if (typeof value === "boolean") {
    return value;
  }
  const normalized = normalizeAvailabilityState(value);
  if (
    normalized === "available" ||
    normalized === "session_available" ||
    normalized === "agent_available" ||
    normalized === "log_available" ||
    normalized === "artifact_available" ||
    normalized === "result_available" ||
    normalized === "recorded"
  ) {
    return true;
  }
  if (
    normalized === "missing" ||
    normalized === "session_missing" ||
    normalized === "agent_missing" ||
    normalized === "log_unavailable" ||
    normalized === "artifact_missing" ||
    normalized === "result_missing" ||
    normalized === "unavailable" ||
    normalized === "empty" ||
    normalized === "recorded_empty" ||
    normalized === "missing_legacy_or_unrecorded"
  ) {
    return false;
  }
  return fallback;
}

function availabilitySummary(value: string | null | undefined, availableText = "Available", missingText = "Missing legacy or unrecorded"): string {
  const normalized = normalizeAvailabilityState(value);
  if (normalized === "available") {
    return availableText;
  }
  return value ? `${missingText} (${value})` : missingText;
}

function structuredValueSummary(value: unknown, missingText: string, emptyText: string, maxLength = 120): string {
  if (value === undefined || value === null) {
    return missingText;
  }
  if (typeof value === "string") {
    const trimmed = value.trim();
    return trimmed ? truncateRunDetailText(trimmed, maxLength) : missingText;
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  if (Array.isArray(value)) {
    return value.length === 0 ? emptyText : truncateRunDetailText(formatJson(value, "[]").replace(/\s+/g, " "), maxLength);
  }
  if (isRecord(value)) {
    return objectHasData(value)
      ? truncateRunDetailText(formatJson(value, "{}").replace(/\s+/g, " "), maxLength)
      : emptyText;
  }
  return String(value);
}

function detailHashTarget(tab: "tool-calls" | "agents" | "sessions", params: Record<string, string | null | undefined> = {}): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    const trimmed = value?.trim();
    if (trimmed) {
      query.set(key, trimmed);
    }
  }
  const queryText = query.toString();
  return queryText ? `${tab}?${queryText}` : tab;
}

function durationLabel(durationMs: number | null | undefined): string {
  if (durationMs === null || durationMs === undefined) return "Unknown duration";
  if (durationMs < 1000) return `${durationMs} ms`;
  return `${(durationMs / 1000).toFixed(2)} s`;
}

function objectListJsonDraftKey(fieldKey: string, index: number, itemFieldKey: string): string {
  return `${fieldKey}.${index}.${itemFieldKey}`;
}

function getCatalogEntry(catalog: WorkflowEditorCatalog, type: string) {
  return catalog.nodes.find((entry) => entry.type === type);
}

function nodeTypeDescription(entry: WorkflowEditorNodeCatalogEntry): string {
  const description = entry.description?.trim();
  return description || NODE_TYPE_DESCRIPTIONS[entry.type] || NODE_KIND_DESCRIPTIONS[entry.kind];
}

function copyOutputSchema(schema: Record<string, unknown> | undefined): Record<string, unknown> {
  return { ...(schema ?? {}) };
}

function rewriteWorkflowGraphNodeIds(
  graphs: ReadonlyArray<WorkflowGraphMetadata>,
  nodeIdMap: ReadonlyMap<string, string>,
): WorkflowGraphMetadata[] {
  if (nodeIdMap.size === 0) return graphs.map((graph) => ({ ...graph, node_ids: [...graph.node_ids] }));
  return graphs.map((graph) => ({
    ...graph,
    node_ids: graph.node_ids.map((nodeId) => nodeIdMap.get(nodeId) ?? nodeId),
  }));
}

function rewriteWorkflowOutputSchemaNodeIds(
  outputSchemas: WorkflowOutputSchemas = {},
  nodeIdMap: ReadonlyMap<string, string>,
  fallbackOutputSchemas: WorkflowOutputSchemas = {},
): WorkflowOutputSchemas {
  if (nodeIdMap.size === 0) return { ...outputSchemas };
  const nextSchemas: WorkflowOutputSchemas = {};
  for (const [nodeId, schema] of Object.entries(outputSchemas)) {
    const nextNodeId = nodeIdMap.get(nodeId) ?? nodeId;
    const fallbackSchema = isRecord(fallbackOutputSchemas[nextNodeId])
      ? fallbackOutputSchemas[nextNodeId]
      : undefined;
    nextSchemas[nextNodeId] = copyOutputSchema(fallbackSchema ?? (isRecord(schema) ? schema : undefined));
  }
  for (const nextNodeId of nodeIdMap.values()) {
    if (Object.prototype.hasOwnProperty.call(nextSchemas, nextNodeId)) continue;
    const fallbackSchema = isRecord(fallbackOutputSchemas[nextNodeId])
      ? fallbackOutputSchemas[nextNodeId]
      : undefined;
    if (fallbackSchema) nextSchemas[nextNodeId] = copyOutputSchema(fallbackSchema);
  }
  return nextSchemas;
}

function remapGeneratedDraftOutputSchemas(
  outputSchemas: WorkflowOutputSchemas = {},
  nodeIdMap: ReadonlyMap<string, string>,
): WorkflowOutputSchemas {
  const nextSchemas: WorkflowOutputSchemas = {};
  for (const [draftNodeId, importedNodeId] of nodeIdMap.entries()) {
    const schema = outputSchemas[draftNodeId];
    if (isRecord(schema)) {
      nextSchemas[importedNodeId] = copyOutputSchema(schema);
    }
  }
  return nextSchemas;
}

function normalizeDerivedNodeFields(
  config: WorkflowConfig,
  baseOutputSchemas: WorkflowOutputSchemas = {},
): WorkflowConfig {
  const nextConfig = cloneConfig(config);
  const outputSchemas = deriveWorkflowOutputSchemas(nextConfig, baseOutputSchemas);
  for (const gate of nextConfig.gates ?? []) {
    if (isPassThroughGateType(gate.type)) {
      gate.outputs = declaredOutputsForNode(gate, outputSchemas);
    } else if (gate.type === "gate:map") {
      gate.outputs = declaredOutputsForNode(gate, outputSchemas);
    } else if (gate.type === "gate:aggregate") {
      gate.outputs = declaredOutputsForNode(gate, outputSchemas);
    } else if (gate.type === "gate:buffer") {
      if (typeof gate.timeout === "string" && gate.timeout.trim() === "") {
        delete gate.timeout;
      }
      gate.outputs = declaredOutputsForNode(gate, outputSchemas);
    } else if (gate.type === "gate:deserialize") {
      gate.outputs = declaredOutputsForNode(gate, outputSchemas);
    } else if (gate.type === "gate:serialize") {
      gate.outputs = declaredOutputsForNode(gate, outputSchemas);
    } else if (gate.type === "gate:dedupe") {
      gate.outputs = declaredOutputsForNode(gate, outputSchemas);
    } else if (gate.type === "gate:await-agent") {
      delete gate.completion_trigger_id;
      gate.outputs = declaredOutputsForNode(gate, outputSchemas);
    } else if (gate.type === "gate:for") {
      gate.outputs = declaredOutputsForNode(gate, outputSchemas);
    } else if (gate.type === "gate:while") {
      gate.outputs = declaredOutputsForNode(gate, outputSchemas);
    } else if (gate.type === "gate:merge") {
      gate.outputs = declaredOutputsForNode(gate, outputSchemas);
    }
  }
  return nextConfig;
}

function validateWorkflowBindingDiagnostics(
  config: WorkflowConfig,
  outputSchemas: WorkflowOutputSchemas = deriveWorkflowOutputSchemas(config),
): WorkflowValidationDiagnostic[] {
  const diagnostics: WorkflowValidationDiagnostic[] = [];
  const nodes = workflowNodes(config);
  const nodesById = new Map(nodes.map((node) => [node.id, node]));
  const allNodeIds = new Set(nodes.map((node) => node.id));
  const upstreamIndex = buildWorkflowUpstreamIndex(config);

  for (const node of nodes) {
    const ancestorNodeIds = upstreamIndex.ancestorNodeIdsByNodeId.get(node.id) ?? [];
    const guaranteedNodeIds = new Set(
      upstreamIndex.guaranteedUpstreamNodeIdsByNodeId.get(node.id) ?? [],
    );
    const declaredOutputs = new Map(
      ancestorNodeIds
        .map((upstreamNodeId) => [upstreamNodeId, declaredOutputsForNode(nodesById.get(upstreamNodeId), outputSchemas)] as const),
    );
    for (const [fieldPath, value] of walkEditableNodeConfigValues(node)) {
      diagnostics.push(
        ...validateBindingValue({
          value,
          fieldPath,
          node,
          declaredOutputs,
          guaranteedNodeIds,
          allNodeIds,
        }),
      );
    }
  }
  return diagnostics;
}

function validateWorkflowAgentCreateDiagnostics(
  config: WorkflowConfig,
): WorkflowValidationDiagnostic[] {
  const diagnostics: WorkflowValidationDiagnostic[] = [];
  for (const effect of config.effects ?? []) {
    if (effect.type !== AGENT_CREATE_NODE_TYPE) continue;
    const executionBackend = typeof effect.execution_backend === "string"
      ? effect.execution_backend.trim()
      : "";
    if (executionBackend) continue;
    diagnostics.push({
      code: "agent_create_missing_execution_backend",
      severity: "error",
      node_id: effect.id,
      node_type: AGENT_CREATE_NODE_TYPE,
      field_path: "execution_backend",
      message: "effect:agent:create requires an execution backend.",
    });
  }
  return diagnostics;
}

function validateWorkflowClientDiagnostics(
  config: WorkflowConfig,
  outputSchemas: WorkflowOutputSchemas = deriveWorkflowOutputSchemas(config),
): WorkflowValidationDiagnostic[] {
  return mergeWorkflowDiagnostics(
    validateWorkflowAgentCreateDiagnostics(config),
    validateWorkflowBindingDiagnostics(config, outputSchemas),
  );
}

function validateWorkflowGraphBindingDiagnostics(
  config: WorkflowConfig,
  graph: WorkflowGraphMetadata,
  outputSchemas: WorkflowOutputSchemas = deriveWorkflowOutputSchemas(config),
): WorkflowValidationDiagnostic[] {
  const graphConfig = workflowGraphScopedConfig(config, graph);
  return validateWorkflowClientDiagnostics(
    graphConfig,
    deriveWorkflowOutputSchemas(graphConfig, outputSchemas),
  );
}

function workflowGraphScopedConfig(
  config: WorkflowConfig,
  graph: WorkflowGraphMetadata,
): WorkflowConfig {
  const graphNodeIds = new Set(graph.node_ids);
  const includesGraphNode = (node: WorkflowNodeConfig) => graphNodeIds.has(node.id);
  return {
    ...config,
    triggers: (config.triggers ?? []).filter(includesGraphNode),
    gates: (config.gates ?? []).filter(includesGraphNode),
    effects: (config.effects ?? []).filter(includesGraphNode),
    queries: (config.queries ?? []).filter(includesGraphNode),
    edges: (config.edges ?? []).filter((edge) =>
      graphNodeIds.has(edge.source_id) && graphNodeIds.has(edge.target_id),
    ),
  };
}

function workflowGraphValidationSignature(
  config: WorkflowConfig,
  graph: WorkflowGraphMetadata,
  outputSchemas: WorkflowOutputSchemas,
): string {
  const graphNodeIds = new Set(graph.node_ids);
  const nodes = workflowNodes(config)
    .filter((node) => graphNodeIds.has(node.id))
    .map((node) => [node.id, node] as const)
    .sort(([left], [right]) => left.localeCompare(right));
  const edges = (config.edges ?? [])
    .filter((edge) => graphNodeIds.has(edge.source_id) && graphNodeIds.has(edge.target_id))
    .map((edge) => ({
      source_id: edge.source_id,
      source_port: edge.source_port ?? null,
      target_id: edge.target_id,
      allow_cycle: Boolean(edge.allow_cycle),
    }))
    .sort((left, right) =>
      `${left.source_id}\u0000${left.source_port ?? ""}\u0000${left.target_id}`.localeCompare(
        `${right.source_id}\u0000${right.source_port ?? ""}\u0000${right.target_id}`,
      ),
    );
  const schemas = [...graphNodeIds]
    .sort()
    .map((nodeId) => [nodeId, outputSchemas[nodeId] ?? null] as const);
  return JSON.stringify({
    node_ids: [...graphNodeIds].sort(),
    nodes,
    edges,
    schemas,
  });
}

function cachedVisibleValidationDiagnostics(
  cache: Map<string, WorkflowValidationCacheEntry>,
  visibleGraphs: ReadonlyArray<WorkflowGraphMetadata>,
): WorkflowValidationDiagnostic[] {
  const diagnostics: WorkflowValidationDiagnostic[] = [];
  for (const graph of visibleGraphs) {
    const entry = cache.get(graph.id);
    if (!entry) continue;
    for (const diagnostic of entry.diagnostics) {
      diagnostics.push(diagnostic);
    }
  }
  return dedupeWorkflowDiagnostics(diagnostics);
}

function workflowDiagnosticsForGraphs(
  diagnostics: ReadonlyArray<WorkflowValidationDiagnostic>,
  graphs: ReadonlyArray<WorkflowGraphMetadata>,
): WorkflowValidationDiagnostic[] {
  const graphNodeIds = new Set(graphs.flatMap((graph) => graph.node_ids));
  return diagnostics.filter((diagnostic) =>
    diagnostic.node_id ? graphNodeIds.has(diagnostic.node_id) : true,
  );
}

function remoteOnlyWorkflowDiagnostics(
  diagnostics: ReadonlyArray<WorkflowValidationDiagnostic>,
): WorkflowValidationDiagnostic[] {
  return diagnostics.filter((diagnostic) => !CLIENT_RECOMPUTED_DIAGNOSTIC_CODES.has(diagnostic.code));
}

function mergeWorkflowDiagnostics(
  first: ReadonlyArray<WorkflowValidationDiagnostic>,
  second: ReadonlyArray<WorkflowValidationDiagnostic>,
): WorkflowValidationDiagnostic[] {
  return dedupeWorkflowDiagnostics([...first, ...second]);
}

function dedupeWorkflowDiagnostics(
  diagnostics: ReadonlyArray<WorkflowValidationDiagnostic>,
): WorkflowValidationDiagnostic[] {
  const nextDiagnostics: WorkflowValidationDiagnostic[] = [];
  const seen = new Set<string>();
  for (const diagnostic of diagnostics) {
    const key = [
      diagnostic.code,
      diagnostic.severity ?? "error",
      diagnostic.node_id ?? "",
      diagnostic.node_type ?? "",
      diagnostic.field_path ?? "",
      diagnostic.reference ?? "",
      diagnostic.message,
    ].join("\u0000");
    if (seen.has(key)) continue;
    seen.add(key);
    nextDiagnostics.push(diagnostic);
  }
  return nextDiagnostics;
}

function scheduleWorkflowValidation(callback: () => void): () => void {
  const idleGlobal = globalThis as typeof globalThis & {
    requestIdleCallback?: (callback: () => void, options?: { timeout?: number }) => number;
    cancelIdleCallback?: (handle: number) => void;
  };
  if (
    typeof idleGlobal.requestIdleCallback === "function" &&
    typeof idleGlobal.cancelIdleCallback === "function"
  ) {
    const handle = idleGlobal.requestIdleCallback(callback, { timeout: 250 });
    return () => idleGlobal.cancelIdleCallback?.(handle);
  }
  const handle = globalThis.setTimeout(callback, 0);
  return () => globalThis.clearTimeout(handle);
}

function walkEditableNodeConfigValues(node: WorkflowNodeConfig): Array<[string, unknown]> {
  const values: Array<[string, unknown]> = [];
  for (const [key, value] of Object.entries(node)) {
    if (key === "id" || key === "display_name" || key === "type") continue;
    walkConfigValue(value, key, values);
  }
  return values;
}

function walkConfigValue(value: unknown, path: string, values: Array<[string, unknown]>) {
  if (isRecord(value)) {
    if (Object.keys(value).length === 0) {
      values.push([path, value]);
      return;
    }
    for (const [key, childValue] of Object.entries(value)) {
      walkConfigValue(childValue, `${path}.${key}`, values);
    }
    return;
  }
  if (Array.isArray(value)) {
    if (value.length === 0) {
      values.push([path, value]);
      return;
    }
    value.forEach((childValue, index) => {
      walkConfigValue(childValue, `${path}[${index}]`, values);
    });
    return;
  }
  values.push([path, value]);
}

function validateBindingValue({
  value,
  fieldPath,
  node,
  declaredOutputs,
  guaranteedNodeIds,
  allNodeIds,
}: {
  value: unknown;
  fieldPath: string;
  node: WorkflowNodeConfig;
  declaredOutputs: Map<string, Record<string, unknown>>;
  guaranteedNodeIds: Set<string>;
  allNodeIds: Set<string>;
}): WorkflowValidationDiagnostic[] {
  if (typeof value !== "string" || !value.includes("${{")) return [];

  const diagnostics: WorkflowValidationDiagnostic[] = [];
  let index = 0;
  while (true) {
    const start = value.indexOf("${{", index);
    if (start === -1) return diagnostics;
    const end = value.indexOf("}}", start + 3);
    if (end === -1) {
      diagnostics.push({
        code: "malformed_binding",
        message: `${fieldPath} contains malformed output reference`,
        node_id: node.id,
        node_type: node.type,
        field_path: fieldPath,
        reference: value.slice(start),
      });
      return diagnostics;
    }

    const sourceText = value.slice(start, end + 2);
    const expressionText = value.slice(start + 3, end);
    try {
      const expression = parseBindingExpression(expressionText, sourceText);
      diagnostics.push(
        ...validateBindingExpression({
          expression,
          fieldPath,
          node,
          declaredOutputs,
          guaranteedNodeIds,
          allNodeIds,
        }),
      );
    } catch (error) {
      const parseError = error instanceof BindingParseError
        ? error
        : new BindingParseError("malformed_binding", "Malformed output reference");
      diagnostics.push({
        code: parseError.code,
        message: parseError.code === "malformed_binding_fallback"
          ? `${fieldPath} has malformed fallback syntax`
          : `${fieldPath} contains malformed output reference`,
        node_id: node.id,
        node_type: node.type,
        field_path: fieldPath,
        reference: sourceText,
      });
    }
    index = end + 2;
  }
}

function validateBindingExpression({
  expression,
  fieldPath,
  node,
  declaredOutputs,
  guaranteedNodeIds,
  allNodeIds,
}: {
  expression: ParsedBindingExpression;
  fieldPath: string;
  node: WorkflowNodeConfig;
  declaredOutputs: Map<string, Record<string, unknown>>;
  guaranteedNodeIds: Set<string>;
  allNodeIds: Set<string>;
}): WorkflowValidationDiagnostic[] {
  const diagnostics = validateBindingReference({
    reference: expression.primary,
    fieldPath,
    node,
    declaredOutputs,
    guaranteedNodeIds,
    allNodeIds,
  });
  if (!expression.hasFallback) return diagnostics;
  const fallback = expression.fallback;
  if (isParsedBindingExpression(fallback)) {
    diagnostics.push(
      ...validateBindingExpression({
        expression: fallback,
        fieldPath,
        node,
        declaredOutputs,
        guaranteedNodeIds,
        allNodeIds,
      }),
    );
  } else if (isParsedBindingReference(fallback)) {
    diagnostics.push(
      ...validateBindingReference({
        reference: fallback,
        fieldPath,
        node,
        declaredOutputs,
        guaranteedNodeIds,
        allNodeIds,
      }),
    );
  }
  return diagnostics;
}

function validateBindingReference({
  reference,
  fieldPath,
  node,
  declaredOutputs,
  guaranteedNodeIds,
  allNodeIds,
}: {
  reference: ParsedBindingReference;
  fieldPath: string;
  node: WorkflowNodeConfig;
  declaredOutputs: Map<string, Record<string, unknown>>;
  guaranteedNodeIds: Set<string>;
  allNodeIds: Set<string>;
}): WorkflowValidationDiagnostic[] {
  const resolved = resolveAvailableBindingReference(reference, declaredOutputs, allNodeIds);
  const outputRef = `${resolved.nodeId}.${resolved.outputPath}`;
  if (resolved.nodeId === "system") return [];
  if (!declaredOutputs.has(resolved.nodeId)) {
    const relation = allNodeIds.has(resolved.nodeId) ? "non-upstream" : "deleted";
    return [{
      code: "invalid_binding_node",
      message: `${fieldPath} references ${relation} node ${resolved.nodeId}`,
      node_id: node.id,
      node_type: node.type,
      field_path: fieldPath,
      reference: outputRef,
    }];
  }

  const outputName = resolved.outputPath.split(".", 1)[0] ?? "";
  if (!(outputName in (declaredOutputs.get(resolved.nodeId) ?? {}))) {
    return [{
      code: "missing_binding_output",
      message: `${fieldPath} references missing output ${outputRef}`,
      node_id: node.id,
      node_type: node.type,
      field_path: fieldPath,
      reference: outputRef,
    }];
  }
  if (!guaranteedNodeIds.has(resolved.nodeId)) {
    return [{
      code: "conditional_binding_node",
      message: `${fieldPath} references conditionally available upstream node ${resolved.nodeId}; bind through a merge-projected output when this value must be available after branching`,
      severity: "warning",
      node_id: node.id,
      node_type: node.type,
      field_path: fieldPath,
      reference: outputRef,
    }];
  }
  return [];
}

function resolveAvailableBindingReference(
  reference: ParsedBindingReference,
  declaredOutputs: Map<string, Record<string, unknown>>,
  allNodeIds: Set<string>,
): ParsedBindingReference {
  const segments = `${reference.nodeId}.${reference.outputPath}`.split(".").filter(Boolean);
  for (let index = segments.length - 1; index > 0; index -= 1) {
    const candidateNodeId = segments.slice(0, index).join(".");
    const candidateOutputPath = segments.slice(index).join(".");
    if (candidateNodeId === "system" || declaredOutputs.has(candidateNodeId) || allNodeIds.has(candidateNodeId)) {
      return { nodeId: candidateNodeId, outputPath: candidateOutputPath };
    }
  }
  return reference;
}

function fieldValueMode(field: WorkflowEditorFieldDefinition, value: unknown): "literal" | "bind" | "template" {
  if (!field.bindable) return "literal";
  if (isExactBindingValue(value)) return "bind";
  if (field.kind === "string" && hasBindingToken(value)) return "template";
  return "literal";
}

function enumOptionValue(field: WorkflowEditorFieldDefinition, value: unknown): string {
  const stringValue = typeof value === "string" ? value : "";
  if (!stringValue) return "";
  return field.options.some((option) => option.value === stringValue) ? stringValue : "";
}

function enumLiteralFallbackValue(field: WorkflowEditorFieldDefinition, value: unknown): string {
  const currentValue = enumOptionValue(field, value);
  if (currentValue) return currentValue;
  const defaultValue = enumOptionValue(field, field.default_value);
  if (defaultValue) return defaultValue;
  return field.required ? field.options[0]?.value ?? "" : "";
}

function parseJsonDraft(text: string, fallback: unknown): unknown {
  const trimmed = text.trim();
  if (!trimmed) return fallback;
  return JSON.parse(trimmed) as unknown;
}

function removeUndefinedValues(value: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(Object.entries(value).filter(([, entry]) => entry !== undefined));
}

function renderBindingSelect(
  field: WorkflowEditorFieldDefinition,
  bindingOptions: BindingOption[],
  value: unknown,
  onChange: (nextValue: string) => void,
) {
  const stringValue = typeof value === "string" ? value : "";
  const hasPresetOption = bindingOptions.some((option) => option.value === stringValue);
  return (
    <div className="binding-editor">
      <label className="field-inline">
        <span>Suggested upstream output</span>
        <select
          value={hasPresetOption ? stringValue : ""}
          onChange={(event) => {
            if (event.target.value) onChange(event.target.value);
          }}
          disabled={bindingOptions.length === 0}
        >
          <option value="">{bindingOptions.length === 0 ? "No suggested outputs available" : "Select output"}</option>
          {bindingOptions.map((option) => (
            <option key={`${field.key}-${option.value}`} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      </label>
      <label className="field-inline">
        <span>Binding reference</span>
        <input
          value={stringValue}
          placeholder="${{node.output.path}}"
          onChange={(event) => onChange(event.target.value)}
        />
      </label>
    </div>
  );
}

function insertBindingReference(currentValue: unknown, bindingReference: string): string {
  const text = typeof currentValue === "string" ? currentValue : "";
  const trimmedReference = bindingReference.trim();
  if (!trimmedReference) return text;
  if (!text.trim()) return trimmedReference;
  return `${text}${trimmedReference}`;
}

function activeEffectTargetKey(values: Record<string, unknown>): EffectTargetKey {
  if (typeof values.session_id === "string" && values.session_id.trim()) return "session_id";
  if (typeof values.vm_name === "string" && values.vm_name.trim()) return "vm_name";
  if (typeof values.session_id === "string" && !Object.prototype.hasOwnProperty.call(values, "target")) {
    return "session_id";
  }
  if (typeof values.vm_name === "string" && !Object.prototype.hasOwnProperty.call(values, "target")) {
    return "vm_name";
  }
  return "target";
}

function isCommunicationEffectNodeType(type: string | undefined): type is
  | "effect:communication:send-message"
  | "effect:notification:send" {
  return COMMUNICATION_EFFECT_NODE_TYPES.has(type ?? "");
}

function isWorkflowNodeKind(value: unknown): value is WorkflowNodeKind {
  return value === "trigger" || value === "gate" || value === "effect" || value === "query";
}

function parseNodeTypeDragPayload(dataTransfer: DataTransfer): NodeTypeDragPayload | null {
  const raw = dataTransfer.getData(NODE_TYPE_DRAG_DATA_MIME);
  if (!raw) return null;
  try {
    const payload = JSON.parse(raw) as Partial<NodeTypeDragPayload>;
    if (!isWorkflowNodeKind(payload.kind) || typeof payload.type !== "string" || !payload.type.trim()) {
      return null;
    }
    return { kind: payload.kind, type: payload.type };
  } catch {
    return null;
  }
}

const TOOL_ARGUMENT_POLICY_STRUCTURED_NODE_TYPES = new Set([
  "effect:agent:create",
  "effect:vm:create",
]);

const AGENT_CREATE_NODE_TYPE = "effect:agent:create";
const CLIENT_RECOMPUTED_DIAGNOSTIC_CODES = new Set([
  "agent_create_missing_execution_backend",
]);

function supportsStructuredToolArgumentPolicies(nodeType: string | undefined): boolean {
  return Boolean(nodeType && TOOL_ARGUMENT_POLICY_STRUCTURED_NODE_TYPES.has(nodeType));
}

function normalizedAgentCreateExecutionBackend(values: Record<string, unknown>): string {
  const rawValue = values.execution_backend;
  return typeof rawValue === "string" ? rawValue.trim() : "";
}

function metadataExecutionBackends(metadata: Record<string, unknown> | undefined): string[] | null {
  const rawValue = metadata?.execution_backends;
  if (!Array.isArray(rawValue)) return null;
  const values = rawValue
    .filter((value): value is string => typeof value === "string" && value.trim().length > 0)
    .map((value) => value.trim());
  return values.length > 0 ? values : null;
}

function fieldExecutionBackends(field: WorkflowEditorFieldDefinition): string[] | null {
  return metadataExecutionBackends(field.metadata);
}

function fieldSelectionOnly(field: WorkflowEditorFieldDefinition): boolean {
  return field.metadata?.selection_only === true;
}

function agentCreateFieldVisibleForBackend(
  field: WorkflowEditorFieldDefinition,
  executionBackend: string,
): boolean {
  if (field.key === "execution_backend") return true;
  if (!executionBackend) return false;
  const visibleBackends = fieldExecutionBackends(field);
  return !visibleBackends || visibleBackends.includes(executionBackend);
}

function pruneAgentCreateBackendFieldValues(
  values: Record<string, unknown>,
  fields: WorkflowEditorFieldDefinition[],
): Record<string, unknown> {
  const executionBackend = normalizedAgentCreateExecutionBackend(values);
  if (!executionBackend) return values;
  const nextValues = { ...values };
  for (const field of fields) {
    if (agentCreateFieldVisibleForBackend(field, executionBackend)) continue;
    delete nextValues[field.key];
  }
  return nextValues;
}

function agentCreateFieldOptionsForBackend(
  field: WorkflowEditorFieldDefinition,
  executionBackend: string,
) {
  if (field.key !== "agent_provider") return field.options;
  return field.options.filter((option) => {
    const visibleBackends = metadataExecutionBackends(option.metadata);
    return !visibleBackends || visibleBackends.includes(executionBackend);
  });
}

function toolArgumentPolicyRowPath(toolName: string, toolIndex: number): string {
  const normalizedToolName = toolName.trim();
  return normalizedToolName
    ? `tool_argument_policies.${normalizedToolName}`
    : `tool_argument_policies[${toolIndex}]`;
}

function toolArgumentPolicyRulePath(toolName: string, toolIndex: number, ruleIndex: number): string {
  return `${toolArgumentPolicyRowPath(toolName, toolIndex)}[${ruleIndex}]`;
}

function toolArgumentPolicyConstraintPath(
  toolName: string,
  toolIndex: number,
  ruleIndex: number,
  constraintIndex: number,
): string {
  return `${toolArgumentPolicyRulePath(toolName, toolIndex, ruleIndex)}.constraints[${constraintIndex}]`;
}

function toolArgumentPolicyFieldErrorMessages(
  errors: ToolArgumentPolicyFieldError[],
  path: string,
): string[] {
  return errors.filter((error) => error.path === path).map((error) => error.message);
}

function toolArgumentPolicyValidationErrorSummary(errors: ToolArgumentPolicyFieldError[]): string {
  return errors.map((error) => `${error.path}: ${error.message}`).join("; ");
}

function renderToolArgumentPolicyErrorMessages(messages: string[]) {
  if (messages.length === 0) return null;
  return (
    <div className="error-text">
      {messages.map((message, index) => (
        <div key={`${message}-${index}`}>{message}</div>
      ))}
    </div>
  );
}

export default function App() {
  const {
    privilegedUnlocked,
    selectedDirectoryPath,
    directoryTree,
    setWorkflowHasUnsavedChanges,
    canReadWorkflows,
    canReadWorkflowRuns,
    canWriteWorkflows,
    canControlWorkflowRuns,
  } = useAdminUi();
  const initialWorkflowRunLookup = workflowRunLookupFromHash();
  const appShellRef = useRef<HTMLDivElement | null>(null);
  const canvasShellRef = useRef<HTMLElement | null>(null);
  const tutorialBubbleRef = useRef<HTMLDivElement | null>(null);
  const tutorialSnapshotRef = useRef<TutorialEditorSnapshot | null>(null);
  const hasAutoFitRef = useRef(false);
  const initialGraphDirectoryPathRef = useRef(selectedDirectoryPath);
  const copiedSelectionRef = useRef<CopiedWorkflowSelection | null>(null);
  const canvasCursorPointRef = useRef<ScreenPoint | null>(null);
  const sidebarResizeRef = useRef<SidebarResizeState | null>(null);
  const workflowGeneratorAbortRef = useRef<AbortController | null>(null);
  const workflowGeneratorRequestIdRef = useRef(0);
  const validationCacheRef = useRef<Map<string, WorkflowValidationCacheEntry>>(new Map());
  const validationRunIdRef = useRef(0);
  const runtimePulseContextRef = useRef<RuntimePulseContext>({
    isInspectMode: false,
    isTutorialMode: false,
    workflowGeneratorOpen: false,
    visibleGraphNodeIds: new Set<string>(),
  });
  const runtimePulseNodeIdsRef = useRef<Set<string>>(new Set<string>());
  const runtimePulseSeenActivityKeysRef = useRef<Set<string>>(new Set());
  const runtimePulseSeenActivityKeyOrderRef = useRef<string[]>([]);
  const runtimePulseTimeoutsRef = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map());
  const previousSelectedDirectoryPathRef = useRef(selectedDirectoryPath);
  const [document, setDocument] = useState<WorkflowEditorDocument | null>(null);
  const [savedDocument, setSavedDocument] = useState<WorkflowEditorDocument | null>(null);
  const [undoHistory, setUndoHistory] = useState<WorkflowUndoEntry[]>([]);
  const [catalog, setCatalog] = useState<WorkflowEditorCatalog>(emptyCatalog());
  const [workflowGeneratorOpen, setWorkflowGeneratorOpen] = useState(false);
  const [workflowGeneratorDescription, setWorkflowGeneratorDescription] = useState("");
  const [workflowGeneratorState, setWorkflowGeneratorState] = useState<WorkflowGeneratorState>("idle");
  const [workflowGeneratorError, setWorkflowGeneratorError] = useState<string | null>(null);
  const [workflowGeneratorWarnings, setWorkflowGeneratorWarnings] = useState<string[]>([]);
  const [workflowGeneratorModel, setWorkflowGeneratorModel] = useState<string | null>(null);
  const [workflowGeneratorReadyDescription, setWorkflowGeneratorReadyDescription] = useState<string | null>(null);
  const [workflowPackModalMode, setWorkflowPackModalMode] = useState<WorkflowPackModalMode | null>(null);
  const [workflowPackForm, setWorkflowPackForm] = useState<WorkflowPackFormState>({
    packId: "",
    packVersion: "v1",
    description: "",
  });
  const [workflowPackSubmitting, setWorkflowPackSubmitting] = useState(false);
  const [workflowPackError, setWorkflowPackError] = useState<string | null>(null);
  const [pageVisible, setPageVisible] = useState(currentPageVisible);
  const [runSnapshot, setRunSnapshot] = useState<WorkflowRunSnapshot | null>(null);
  const [runtimePulseNodeIds, setRuntimePulseNodeIds] = useState<Set<string>>(() => new Set<string>());
  const [workflowTutorialChooserOpen, setWorkflowTutorialChooserOpen] = useState(false);
  const [workflowTutorialProgress, setWorkflowTutorialProgress] = useState<WorkflowTutorialProgress>(
    () => ({ completedTutorialIds: [], skippedTutorialIds: [] }),
  );
  const [activeWorkflowTutorial, setActiveWorkflowTutorial] = useState<WorkflowTutorialSession | null>(null);
  const [workflowTutorialHint, setWorkflowTutorialHint] = useState<string | null>(null);
  const [runDetail, setRunDetail] = useState<WorkflowRunDetail | null>(null);
  const [runDetailLookup, setRunDetailLookup] = useState(initialWorkflowRunLookup?.value ?? "");
  const [runDetailLookupKind, setRunDetailLookupKind] = useState<WorkflowRunDetailLookupKind>(
    initialWorkflowRunLookup?.kind ?? "auto",
  );
  const [runDetailLoading, setRunDetailLoading] = useState(false);
  const [runDetailError, setRunDetailError] = useState<string | null>(null);
  const [operatorSteeringNotesByKey, setOperatorSteeringNotesByKey] = useState<Record<string, OperatorSteeringNote[]>>({});
  const [operatorSteeringDraftsByKey, setOperatorSteeringDraftsByKey] = useState<Record<string, string>>({});
  const [operatorSteeringLoadingByKey, setOperatorSteeringLoadingByKey] = useState<Record<string, boolean>>({});
  const [operatorSteeringSubmittingByKey, setOperatorSteeringSubmittingByKey] = useState<Record<string, boolean>>({});
  const [operatorSteeringCancellingById, setOperatorSteeringCancellingById] = useState<Record<string, boolean>>({});
  const [operatorSteeringErrorsByKey, setOperatorSteeringErrorsByKey] = useState<Record<string, string>>({});
  const [runSearchScope, setRunSearchScope] = useState("");
  const [runSearchWithoutSession, setRunSearchWithoutSession] = useState(false);
  const [runSearchIncludeCompleted, setRunSearchIncludeCompleted] = useState(true);
  const [runSearchResult, setRunSearchResult] = useState<WorkflowRunSearch | null>(null);
  const [runSearchLoading, setRunSearchLoading] = useState(false);
  const [runSearchError, setRunSearchError] = useState<string | null>(null);
  const [runSnapshotError, setRunSnapshotError] = useState<string | null>(null);
  const [compactActivePanel, setCompactActivePanel] = useState<CompactActivePanel>(null);
  const [compactCanvasMode, setCompactCanvasMode] = useState<CompactCanvasMode>("select");
  const [compactCreateSearch, setCompactCreateSearch] = useState("");
  const [isCompactCanvasTouchMode, setIsCompactCanvasTouchMode] = useState(isCompactCanvasTouchViewport);
  const compactCanvasTouchModeRef = useRef<CompactCanvasTouchMode>("idle");
  const compactPanePanRef = useRef<CompactPanePanState | null>(null);
  const [backendValidationDiagnostics, setBackendValidationDiagnostics] = useState<WorkflowValidationDiagnostic[]>([]);
  const [validationDiagnostics, setValidationDiagnostics] = useState<WorkflowValidationDiagnostic[]>([]);
  const [flowNodes, setFlowNodes] = useState<Node<GraphNodeData>[]>([]);
  const [flowEdges, setFlowEdges] = useState<Edge[]>([]);
  const [flowInstance, setFlowInstance] = useState<ReactFlowInstance<Node<GraphNodeData>, Edge> | null>(null);
  const [canvasViewport, setCanvasViewport] = useState<Viewport>({ x: 0, y: 0, zoom: 1 });
  const [tutorialBubbleSize, setTutorialBubbleSize] = useState<TutorialBubbleSize>({ width: 0, height: 0 });
  const [selectedNodeIds, setSelectedNodeIds] = useState<string[]>([]);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);
  const [selectedGraphId, setSelectedGraphId] = useState<string | null>(null);
  const [nodeConnectionMenu, setNodeConnectionMenu] = useState<NodeConnectionMenuState | null>(null);
  const [pendingConnection, setPendingConnection] = useState<PendingConnectionState | null>(null);
  const [nodeEditOrigins, setNodeEditOrigins] = useState<Record<string, string>>({});
  const [graphNameDraft, setGraphNameDraft] = useState("");
  const [graphDescriptionDraft, setGraphDescriptionDraft] = useState("");
  const [graphDirectoryDraft, setGraphDirectoryDraft] = useState("");
  const [graphUsersDraft, setGraphUsersDraft] = useState("");
  const [inspectorNodeId, setInspectorNodeId] = useState("");
  const [inspectorDisplayName, setInspectorDisplayName] = useState("");
  const [inspectorKnownValues, setInspectorKnownValues] = useState<Record<string, unknown>>({});
  const [inspectorUnknownValues, setInspectorUnknownValues] = useState<Record<string, unknown>>({});
  const [inspectorJsonDrafts, setInspectorJsonDrafts] = useState<Record<string, string>>({});
  const [inspectorToolArgumentPolicyDrafts, setInspectorToolArgumentPolicyDrafts] = useState<
    Record<string, ToolArgumentPolicyDraftRow[]>
  >({});
  const [inspectorJsonDisplayModes, setInspectorJsonDisplayModes] = useState<Record<string, JsonDisplayMode>>({});
  const [inspectorStringListDrafts, setInspectorStringListDrafts] = useState<Record<string, string>>({});
  const [inspectorStringMapDrafts, setInspectorStringMapDrafts] = useState<Record<string, StringMapDraftRow[]>>({});
  const [inspectorSessionListMetadataRules, setInspectorSessionListMetadataRules] = useState<
    SessionListMetadataRuleDraftRow[]
  >([]);
  const [inspectorSessionListMetadataRawMode, setInspectorSessionListMetadataRawMode] = useState(false);
  const [inspectorFieldModes, setInspectorFieldModes] = useState<Record<string, "literal" | "bind" | "template">>({});
  const [inspectorEffectTargetKey, setInspectorEffectTargetKey] = useState<EffectTargetKey>("target");
  const [rawJsonDraft, setRawJsonDraft] = useState("{}");
  const [copiedBindingExpression, setCopiedBindingExpression] = useState<string | null>(null);
  const [outputCopyError, setOutputCopyError] = useState<string | null>(null);
  const [clearingRunIds, setClearingRunIds] = useState<Record<string, boolean>>({});
  const [dismissingNodeFailureIds, setDismissingNodeFailureIds] = useState<Record<string, boolean>>({});
  const [resumingRunIds, setResumingRunIds] = useState<Record<string, boolean>>({});
  const [snapGuides, setSnapGuides] = useState<SnapGuide[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [inspectorDraftError, setInspectorDraftError] = useState<string | null>(null);
  const [saveState, setSaveState] = useState<SaveState>("idle");
  const [sidebarWidth, setSidebarWidth] = useState<number>(() => readStoredWorkflowSidebarWidth());
  const [isResizingSidebar, setIsResizingSidebar] = useState(false);
  const editLocked = !privilegedUnlocked;
  const isInspectMode = runDetail !== null;
  const activeWorkflowTutorialDefinition = activeWorkflowTutorial
    ? workflowTutorialDefinitionById(activeWorkflowTutorial.tutorialId) ?? null
    : null;
  const activeWorkflowTutorialStep = activeWorkflowTutorialDefinition
    ? activeWorkflowTutorialDefinition.steps[activeWorkflowTutorial?.stepIndex ?? 0] ?? null
    : null;
  const isTutorialMode = Boolean(activeWorkflowTutorialDefinition && activeWorkflowTutorialStep);
  const workflowEditLocked = editLocked || isInspectMode || Boolean(activeWorkflowTutorial) || !canWriteWorkflows;
  const workflowRunReadLocked = !canReadWorkflowRuns;
  const workflowRunControlLocked = !canControlWorkflowRuns || workflowRunReadLocked;
  const canUndoWorkflowChanges = !workflowEditLocked && undoHistory.length > 0;
  useEffect(() => {
    runtimePulseNodeIdsRef.current = runtimePulseNodeIds;
  }, [runtimePulseNodeIds]);
  const workflowTutorialProgressSets = useMemo(() => ({
    completed: new Set(workflowTutorialProgress.completedTutorialIds),
    skipped: new Set(workflowTutorialProgress.skippedTutorialIds),
  }), [workflowTutorialProgress]);
  const unfinishedUnskippedTutorialCount = WORKFLOW_TUTORIAL_DEFINITIONS.filter((definition) => (
    !workflowTutorialProgressSets.completed.has(definition.id) &&
    !workflowTutorialProgressSets.skipped.has(definition.id)
  )).length;
  const hasUnfinishedUnskippedTutorials = unfinishedUnskippedTutorialCount > 0;
  const activeWorkflowDocument = isTutorialMode && activeWorkflowTutorial ? activeWorkflowTutorial.document : document;
  const activeWorkflowConfig = activeWorkflowDocument?.config ?? emptyConfig();
  const activeWorkflowOutputSchemas = activeWorkflowDocument?.output_schemas ?? EMPTY_OUTPUT_SCHEMAS;
  const activeWorkflowCatalog = activeWorkflowTutorialDefinition?.catalog ?? catalog;
  const config = document?.config ?? emptyConfig();
  const workflowGraphs = document?.graphs ?? [];
  const workflowPackOptions = document?.workflow_packs ?? [];
  const selectedWorkflowPack = document?.selected_workflow_pack ?? workflowPackOptions.find((pack) => pack.selected) ?? null;
  const selectedWorkflowPackReference = selectedWorkflowPack?.reference ?? "";
  const workflowReadLocked = !canReadWorkflows;
  const visibleWorkflowGraphs = workflowGraphs.filter((graph) =>
    graphVisibleInDirectory(graph, selectedDirectoryPath),
  );
  const visibleGraphNodeIds = new Set(visibleWorkflowGraphs.flatMap((graph) => graph.node_ids));
  const visibleGraphNodeIdKey = workflowGraphKey([...visibleGraphNodeIds]);
  useEffect(() => {
    runtimePulseContextRef.current = {
      isInspectMode,
      isTutorialMode,
      workflowGeneratorOpen,
      visibleGraphNodeIds: new Set(visibleGraphNodeIds),
    };
  }, [isInspectMode, isTutorialMode, visibleGraphNodeIdKey, workflowGeneratorOpen]);
  const visibleRuns = (runSnapshot?.runs ?? []).filter((run) =>
    run.active_nodes.some((activity) => visibleGraphNodeIds.has(activity.node_id)),
  );
  const runDetailGraphNodes = runDetail ? workflowRunDetailGraphNodes(runDetail) : [];
  const runDetailNodesById = runDetail ? workflowRunDetailNodeById(runDetail) : new Map<string, WorkflowRunNodeDetail>();
  const inspectVisitedNodeIds = runDetail ? workflowRunDetailVisitedNodeIds(runDetail) : new Set<string>();
  const inspectDisplayNodeIds = runDetail ? workflowRunDetailDisplayNodeIds(runDetail) : new Set<string>();
  const inspectGhostNodeIds = runDetail && workflowRunDetailIsFailed(runDetail)
    ? new Set([...inspectDisplayNodeIds].filter((nodeId) => !inspectVisitedNodeIds.has(nodeId)))
    : new Set<string>();
  const inspectConfig = runDetail ? workflowRunDetailConfig(runDetail, inspectDisplayNodeIds) : null;
  const inspectLayout = inspectConfig
    ? workflowRunDetailLayout(inspectConfig, document?.layout, currentPositionsFromNodes(flowNodes))
    : { nodes: {} };
  const inspectFlowNodes = inspectConfig
    ? buildNodes(
        inspectConfig,
        inspectLayout,
        catalog,
        runDetail ? workflowRunDetailActivityCounts(runDetail) : {},
        [],
        {},
        selectedNodeId ? [selectedNodeId] : [],
        new Set<string>(),
        inspectGhostNodeIds,
        new Set<string>(),
      )
    : [];
  const inspectFlowEdges = inspectConfig
    ? buildEdges(inspectConfig, catalog).map((edge) => {
        const isGhost = inspectGhostNodeIds.has(edge.source) || inspectGhostNodeIds.has(edge.target);
        return {
          ...edge,
          selectable: false,
          className: `${edge.className ?? ""}${isGhost ? " workflow-edge-ghost" : ""}`,
        };
      })
    : [];
  const canvasDocument = isTutorialMode && activeWorkflowTutorial ? activeWorkflowTutorial.document : document;
  const canvasCatalog = activeWorkflowTutorialDefinition?.catalog ?? catalog;
  const canvasWorkflowGraphs = canvasDocument?.graphs ?? workflowGraphs;
  const canvasVisibleWorkflowGraphs = isTutorialMode
    ? canvasWorkflowGraphs
    : canvasWorkflowGraphs.filter((graph) => graphVisibleInDirectory(graph, selectedDirectoryPath));
  const canvasVisibleGraphNodeIds = new Set(canvasVisibleWorkflowGraphs.flatMap((graph) => graph.node_ids));
  const tutorialDisplayedFlowNodes = isTutorialMode && canvasDocument
    ? decorateWorkflowTutorialNodes(
        buildNodes(
          canvasDocument.config,
          canvasDocument.layout,
          canvasCatalog,
          {},
          [],
          {},
          selectedNodeIds,
          new Set<string>(),
          new Set<string>(),
          new Set<string>(),
        ),
        activeWorkflowTutorialStep,
      )
    : [];
  const tutorialDisplayedFlowEdges = isTutorialMode && canvasDocument
    ? buildEdges(canvasDocument.config, canvasCatalog).filter(
        (edge) => canvasVisibleGraphNodeIds.has(edge.source) && canvasVisibleGraphNodeIds.has(edge.target),
      )
    : [];
  const baseDisplayedFlowNodes = isTutorialMode
    ? tutorialDisplayedFlowNodes
    : flowNodes.filter((node) => visibleGraphNodeIds.has(node.id));
  const normalDisplayedFlowNodes = !isTutorialMode && pendingConnection && document
    ? baseDisplayedFlowNodes.map((node) => {
        let pendingConnectionRole: PendingConnectionRole;
        if (node.id === pendingConnection.sourceId) {
          pendingConnectionRole = "source";
        } else {
          pendingConnectionRole = validateConnection(
            document.config,
            catalog,
            pendingConnection.sourceId,
            node.id,
            pendingConnection.sourcePort,
          )
            ? "invalid-target"
            : "valid-target";
        }
        return {
          ...node,
          data: {
            ...node.data,
            pendingConnectionRole,
          },
        };
      })
    : baseDisplayedFlowNodes;
  const normalDisplayedFlowEdges = isTutorialMode
    ? tutorialDisplayedFlowEdges
    : flowEdges.filter((edge) => visibleGraphNodeIds.has(edge.source) && visibleGraphNodeIds.has(edge.target));
  const displayedFlowNodes = isInspectMode ? inspectFlowNodes : normalDisplayedFlowNodes;
  const displayedFlowEdges = isInspectMode ? inspectFlowEdges : normalDisplayedFlowEdges;
  const tutorialCameraTargetNodeId = isTutorialMode && activeWorkflowTutorialStep
    ? workflowTutorialTargetNodeId(activeWorkflowTutorialStep.target)
    : null;
  const tutorialCameraTargetNode = tutorialCameraTargetNodeId
    ? displayedFlowNodes.find((node) => node.id === tutorialCameraTargetNodeId)
    : undefined;
  const tutorialCameraTargetKey = tutorialCameraTargetNode
    ? `${tutorialCameraTargetNode.id}:${tutorialCameraTargetNode.position.x}:${tutorialCameraTargetNode.position.y}`
    : "";
  const selectedInspectGraphNode = isInspectMode && selectedNodeId
    ? runDetailGraphNodes.find((node) => node.id === selectedNodeId)
    : undefined;
  const selectedInspectNodeDetail = isInspectMode && selectedNodeId
    ? runDetailNodesById.get(selectedNodeId)
    : undefined;
  const selectedInspectCatalogEntry = selectedInspectGraphNode
    ? getCatalogEntry(catalog, selectedInspectGraphNode.type)
    : undefined;
  const selectedNode = !isInspectMode && selectedNodeIds.length === 1 && selectedNodeId
    ? nodeById(activeWorkflowConfig, selectedNodeId)
    : undefined;
  const selectedNodeGraph = selectedNode && !isTutorialMode
    ? visibleWorkflowGraphs.find((graph) => graph.node_ids.includes(selectedNode.id))
    : undefined;
  const selectedCatalogEntry = selectedNode ? getCatalogEntry(activeWorkflowCatalog, selectedNode.type) : undefined;
  const selectedNodeDiagnostics = selectedNode
    ? (isTutorialMode
        ? (activeWorkflowDocument?.validation_diagnostics ?? [])
        : validationDiagnostics
      ).filter((diagnostic) => diagnostic.node_id === selectedNode.id)
    : [];
  const selectedNodeOutputContract = useMemo(
    () => (
      selectedNode
        ? workflowNodeOutputContract(activeWorkflowConfig, selectedNode.id, activeWorkflowOutputSchemas)
        : null
    ),
    [activeWorkflowConfig, activeWorkflowOutputSchemas, selectedNode?.id],
  );
  const selectedEdge = selectedEdgeId
    ? activeWorkflowConfig.edges.find(
        (edge) =>
          edgeId(edge) === selectedEdgeId &&
          visibleGraphNodeIds.has(edge.source_id) &&
          visibleGraphNodeIds.has(edge.target_id),
      )
    : undefined;
  const selectedEdgeSourceNode = selectedEdge ? nodeById(activeWorkflowConfig, selectedEdge.source_id) : undefined;
  const selectedEdgeSourceCatalogEntry = selectedEdgeSourceNode
    ? getCatalogEntry(activeWorkflowCatalog, selectedEdgeSourceNode.type)
    : undefined;
  const selectedEdgeSourceOutputPorts = selectedEdgeSourceNode
    ? workflowOutputPortsForNode(selectedEdgeSourceNode, selectedEdgeSourceCatalogEntry?.output_ports ?? [])
    : selectedEdgeSourceCatalogEntry?.output_ports ?? [];
  const selectedEdgeRouteLabel = selectedEdge
    ? workflowEdgeRouteLabel(selectedEdge.source_port, selectedEdgeSourceOutputPorts)
      ?? (selectedEdge.source_port ? humanizeWorkflowRouteKey(selectedEdge.source_port) : "Default")
    : null;
  const selectedGraph = selectedGraphId
    ? visibleWorkflowGraphs.find((graph) => graph.id === selectedGraphId)
    : undefined;
  const hasUnsavedWorkflowChanges = useMemo(
    () => (
      document !== null &&
      savedDocument !== null &&
      !workflowEditableDocumentsEqual(
        {
          config: document.config,
          layout: document.layout,
          graphs: document.graphs,
        },
        {
          config: savedDocument.config,
          layout: savedDocument.layout,
          graphs: savedDocument.graphs,
        },
      )
    ),
    [document, savedDocument],
  );
  const dirtyNodeIds = useMemo(
    () => dirtyWorkflowNodeIds(document, savedDocument, nodeEditOrigins),
    [document, savedDocument, nodeEditOrigins],
  );
  const dirtyNodeIdSet = useMemo(() => new Set(dirtyNodeIds), [dirtyNodeIds]);
  const selectedNodeHasUnsavedChanges = Boolean(selectedNodeId && dirtyNodeIdSet.has(selectedNodeId));
  const dirtyNodeSummary = useMemo(() => {
    if (dirtyNodeIds.length === 0) return null;
    const dirtyNodeLabels = dirtyNodeIds
      .map((nodeId) => nodeById(config, nodeId)?.display_name || nodeId)
      .slice(0, 3);
    const remainingCount = dirtyNodeIds.length - dirtyNodeLabels.length;
    return `${dirtyNodeIds.length} edited node${dirtyNodeIds.length === 1 ? "" : "s"}: ${dirtyNodeLabels.join(", ")}${
      remainingCount > 0 ? `, +${remainingCount} more` : ""
    }`;
  }, [config, dirtyNodeIds]);
  const savePanelStatusTone = hasUnsavedWorkflowChanges ? "dirty" : saveState === "saved" ? "saved" : "clean";
  const visibleWorkflowNodes = workflowNodes(config).filter((node) => visibleGraphNodeIds.has(node.id));
  const runSearchScopeOptions: WorkflowRunSearchScopeOption[] = [
    ...visibleWorkflowGraphs.map((graph) => ({
      value: `graph:${graph.id}`,
      kind: "graph" as const,
      id: graph.id,
      label: graph.name || graph.id,
    })),
    ...visibleWorkflowNodes.map((node) => ({
      value: `node:${node.id}`,
      kind: "node" as const,
      id: node.id,
      label: node.display_name?.trim() ? `${node.display_name} (${node.id})` : node.id,
    })),
  ];
  const runSearchScopeOptionValues = runSearchScopeOptions.map((option) => option.value).join("\u0000");
  const selectedRunSearchScope = runSearchScopeOptions.find((option) => option.value === runSearchScope);
  const graphLabels = isInspectMode
    ? []
    : workflowGraphLabels(isTutorialMode ? canvasVisibleWorkflowGraphs : visibleWorkflowGraphs, displayedFlowNodes, canvasViewport);
  const graphViewContext = `Directory: ${selectedDirectoryPath}`;
  const directoryPathOptions = useMemo(() => {
    const treeOptions = directoryTree?.ok && directoryTree.data
      ? flattenDirectorySelectOptions(directoryTree.data)
      : [];
    const withRoot = ensureDirectorySelectOption(treeOptions, "root", "root");
    return ensureDirectorySelectOption(withRoot, selectedDirectoryPath, selectedDirectoryPath);
  }, [directoryTree, selectedDirectoryPath]);
  const selectedNodeBindingConfig = useMemo(
    () => selectedNodeGraph
      ? workflowGraphScopedConfig(activeWorkflowConfig, selectedNodeGraph)
      : activeWorkflowConfig,
    [activeWorkflowConfig, selectedNodeGraph],
  );
  const selectedNodeBindingOptions = useMemo(
    () => selectedNode
      ? upstreamBindingOptions(selectedNodeBindingConfig, selectedNode.id, activeWorkflowOutputSchemas)
      : [],
    [activeWorkflowOutputSchemas, selectedNode?.id, selectedNodeBindingConfig],
  );
  const hasCollapsedTargetUi = TARGET_SELECTOR_NODE_TYPES.has(selectedNode?.type ?? "");
  const effectTargetKey = hasCollapsedTargetUi ? inspectorEffectTargetKey : activeEffectTargetKey(inspectorKnownValues);
  const inspectorFields = (() => {
    if (!selectedCatalogEntry) return [];
    let fields = selectedCatalogEntry.fields;
    if (selectedNode?.type === AGENT_CREATE_NODE_TYPE) {
      const executionBackend = normalizedAgentCreateExecutionBackend(inspectorKnownValues);
      fields = fields.filter((field) => agentCreateFieldVisibleForBackend(field, executionBackend));
    }
    if (!hasCollapsedTargetUi) return fields;
    return fields.filter((field) => !EFFECT_TARGET_KEYS.includes(field.key as EffectTargetKey));
  })();
  const effectTargetField = selectedCatalogEntry?.fields.find((field) => field.key === effectTargetKey)
    ?? selectedCatalogEntry?.fields.find((field) => field.key === "target");
  const communicationThreadField = selectedCatalogEntry?.fields.find((field) => field.key === "thread_id");

  useEffect(() => {
    setWorkflowHasUnsavedChanges(hasUnsavedWorkflowChanges);
  }, [hasUnsavedWorkflowChanges, setWorkflowHasUnsavedChanges]);

  useEffect(
    () => () => {
      setWorkflowHasUnsavedChanges(false);
    },
    [setWorkflowHasUnsavedChanges],
  );

  useEffect(() => {
    if (!hasUnsavedWorkflowChanges) return;
    const handleBeforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", handleBeforeUnload);
    return () => window.removeEventListener("beforeunload", handleBeforeUnload);
  }, [hasUnsavedWorkflowChanges]);

  useEffect(() => {
    if (typeof globalThis.document === "undefined") return;
    const updateVisibility = () => setPageVisible(currentPageVisible());
    const markHidden = () => setPageVisible(false);
    globalThis.document.addEventListener("visibilitychange", updateVisibility);
    window.addEventListener("pageshow", updateVisibility);
    window.addEventListener("pagehide", markHidden);
    return () => {
      globalThis.document.removeEventListener("visibilitychange", updateVisibility);
      window.removeEventListener("pageshow", updateVisibility);
      window.removeEventListener("pagehide", markHidden);
    };
  }, []);

  useEffect(() => {
    setRunSearchScope((current) => {
      if (current && runSearchScopeOptions.some((option) => option.value === current)) return current;
      return runSearchScopeOptions[0]?.value ?? "";
    });
  }, [runSearchScopeOptionValues]);

  useEffect(() => {
    setRunSearchResult(null);
    setRunSearchError(null);
  }, [runSearchScope, runSearchWithoutSession, runSearchIncludeCompleted]);

  useEffect(() => {
    setCopiedBindingExpression(null);
    setOutputCopyError(null);
  }, [selectedNode?.id]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    window.localStorage.setItem(WORKFLOW_SIDEBAR_WIDTH_STORAGE_KEY, String(sidebarWidth));
  }, [sidebarWidth]);

  useEffect(() => {
    let cancelled = false;
    fetchJson<unknown>(WORKFLOW_TUTORIAL_PROGRESS_API_PATH)
      .then((payload) => {
        if (cancelled) return;
        setWorkflowTutorialProgress(normalizeWorkflowTutorialProgress(payload));
      })
      .catch((reason: unknown) => {
        if (cancelled) return;
        setError(reason instanceof Error ? reason.message : String(reason));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") return;

    const handleResize = () => {
      const isCompact = isCompactCanvasTouchViewport();
      setIsCompactCanvasTouchMode(isCompact);
      if (isCompact) return;
      setCompactActivePanel(null);
      setCompactCanvasMode("select");
      setCompactCreateSearch("");
      const containerWidth = appShellRef.current?.getBoundingClientRect().width ?? window.innerWidth;
      setSidebarWidth((current) => clampWorkflowSidebarWidth(current, containerWidth));
    };

    handleResize();
    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, []);

  useEffect(() => {
    if (isCompactCanvasTouchMode) return;
    compactCanvasTouchModeRef.current = "idle";
    compactPanePanRef.current = null;
  }, [isCompactCanvasTouchMode]);

  useEffect(() => {
    if (!isCompactCanvasTouchMode || typeof globalThis.document === "undefined") return;

    const stopSingleTouchContinuation = (event: TouchEvent) => {
      if (compactCanvasTouchModeRef.current !== "multi-touch-pan" || event.touches.length >= 2) return;
      event.stopImmediatePropagation();
    };

    const handleTouchSequenceEnd = (event: TouchEvent) => {
      if (compactCanvasTouchModeRef.current === "multi-touch-pan" && event.touches.length > 0 && event.touches.length < 2) {
        event.stopImmediatePropagation();
        return;
      }
      if (event.touches.length === 0) {
        compactCanvasTouchModeRef.current = "idle";
      }
    };

    globalThis.document.addEventListener("touchmove", stopSingleTouchContinuation, { capture: true });
    globalThis.window.addEventListener("touchmove", stopSingleTouchContinuation, { capture: true });
    globalThis.document.addEventListener("touchend", handleTouchSequenceEnd, { capture: true });
    globalThis.window.addEventListener("touchend", handleTouchSequenceEnd, { capture: true });
    globalThis.document.addEventListener("touchcancel", handleTouchSequenceEnd, { capture: true });
    globalThis.window.addEventListener("touchcancel", handleTouchSequenceEnd, { capture: true });
    return () => {
      globalThis.document.removeEventListener("touchmove", stopSingleTouchContinuation, { capture: true });
      globalThis.window.removeEventListener("touchmove", stopSingleTouchContinuation, { capture: true });
      globalThis.document.removeEventListener("touchend", handleTouchSequenceEnd, { capture: true });
      globalThis.window.removeEventListener("touchend", handleTouchSequenceEnd, { capture: true });
      globalThis.document.removeEventListener("touchcancel", handleTouchSequenceEnd, { capture: true });
      globalThis.window.removeEventListener("touchcancel", handleTouchSequenceEnd, { capture: true });
    };
  }, [isCompactCanvasTouchMode]);

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

  function applyLoadedWorkflowDocument(
    payload: WorkflowEditorDocument,
    defaultDirectoryPath: string,
  ) {
    const backendOutputSchemas = payload.output_schemas ?? {};
    const normalizedConfig = normalizeDerivedNodeFields(
      normalizeWorkflowConfigShape(payload.config),
      backendOutputSchemas,
    );
    const outputSchemas = deriveWorkflowOutputSchemas(normalizedConfig, backendOutputSchemas);
    const normalizedGraphs = normalizeWorkflowGraphs(
      normalizedConfig,
      payload.graphs ?? [],
      defaultDirectoryPath,
    );
    const normalizedDocument = {
      ...payload,
      config: normalizedConfig,
      graphs: normalizedGraphs,
      output_schemas: outputSchemas,
    };
    const savedNormalizedDocument = normalizeWorkflowEditorDocument(
      normalizedDocument,
      defaultDirectoryPath,
    );
    validationCacheRef.current.clear();
    const visibleGraphsForDocument = normalizedGraphs.filter((graph) =>
      graphVisibleInDirectory(graph, defaultDirectoryPath),
    );
    const nextBackendDiagnostics = workflowDiagnosticsForGraphs(
      payload.validation_diagnostics ?? [],
      visibleGraphsForDocument,
    );
    setBackendValidationDiagnostics(payload.validation_diagnostics ?? []);
    setValidationDiagnostics(nextBackendDiagnostics);
    setDocument(savedNormalizedDocument);
    setSavedDocument(savedNormalizedDocument);
    setUndoHistory([]);
    setNodeEditOrigins({});
    setProgrammaticSelection([]);
    setSelectedEdgeId(null);
    setSaveState("idle");
    setError(null);
  }

  async function loadWorkflowDocument(workflowPack?: string | null) {
    const payload = await fetchJson<WorkflowEditorDocument>(
      workflowEditorDocumentPath(workflowPack),
    );
    applyLoadedWorkflowDocument(payload, selectedDirectoryPath);
  }

  useEffect(() => {
    let cancelled = false;
    fetchJson<WorkflowEditorDocument>(workflowEditorDocumentPath())
      .then((payload) => {
        if (cancelled) return;
        applyLoadedWorkflowDocument(payload, initialGraphDirectoryPathRef.current);
      })
      .catch((reason: unknown) => {
        if (cancelled) return;
        setError(reason instanceof Error ? reason.message : String(reason));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (workflowRunReadLocked) {
      setRunSnapshot(null);
      setRunDetail(null);
      setRunDetailError(null);
      setRunSearchResult(null);
      setRunSearchError(null);
      setRunSnapshotError(null);
      return;
    }
    let cancelled = false;
    fetchJson<WorkflowRunSnapshot>("/api/workflow-editor/runs")
      .then((runSnapshotPayload) => {
        if (cancelled) return;
        applyWorkflowRunSnapshot(runSnapshotPayload);
        setRunSnapshotError(null);
      })
      .catch((reason: unknown) => {
        if (cancelled) return;
        setRunSnapshotError(reason instanceof Error ? reason.message : String(reason));
      });
    return () => {
      cancelled = true;
    };
  }, [workflowRunReadLocked]);

  function rememberRuntimePulseSeenActivityKey(activityKey: string) {
    const seenKeys = runtimePulseSeenActivityKeysRef.current;
    if (seenKeys.has(activityKey)) {
      return;
    }
    seenKeys.add(activityKey);
    const keyOrder = runtimePulseSeenActivityKeyOrderRef.current;
    keyOrder.push(activityKey);
    while (keyOrder.length > RUNTIME_NODE_PULSE_SEEN_KEY_CAP) {
      const removedKey = keyOrder.shift();
      if (removedKey !== undefined) {
        seenKeys.delete(removedKey);
      }
    }
  }

  function scheduleRuntimePulse(nodeId: string) {
    const existingTimeout = runtimePulseTimeoutsRef.current.get(nodeId);
    if (existingTimeout !== undefined) {
      clearTimeout(existingTimeout);
    }
    setRuntimePulseNodeIds((current) => {
      if (current.has(nodeId)) return current;
      const next = new Set(current);
      next.add(nodeId);
      return next;
    });
    const timeout = setTimeout(() => {
      if (runtimePulseTimeoutsRef.current.get(nodeId) !== timeout) {
        return;
      }
      runtimePulseTimeoutsRef.current.delete(nodeId);
      setRuntimePulseNodeIds((current) => {
        if (!current.has(nodeId)) return current;
        const next = new Set(current);
        next.delete(nodeId);
        return next;
      });
    }, RUNTIME_NODE_PULSE_DURATION_MS);
    runtimePulseTimeoutsRef.current.set(nodeId, timeout);
  }

  function applyWorkflowRunSnapshot(payload: WorkflowRunSnapshot) {
    setRunSnapshot(payload);
    const recentNodeActivity = payload.recent_node_activity ?? [];
    if (recentNodeActivity.length === 0) {
      return;
    }
    const context = runtimePulseContextRef.current;
    const latestUnseenNodeActivity = new Map<string, WorkflowRunRecentNodeActivity>();
    for (const activity of recentNodeActivity) {
      const activityKey = trimNonEmpty(activity.activity_key);
      if (!activityKey) continue;
      const wasSeen = runtimePulseSeenActivityKeysRef.current.has(activityKey);
      rememberRuntimePulseSeenActivityKey(activityKey);
      const nodeId = trimNonEmpty(activity.node_id);
      const activityAt = trimNonEmpty(activity.activity_at);
      if (!nodeId || !activityAt) continue;
      if (wasSeen || latestUnseenNodeActivity.has(nodeId)) continue;
      latestUnseenNodeActivity.set(nodeId, activity);
    }
    if (context.isInspectMode || context.isTutorialMode || context.workflowGeneratorOpen) {
      return;
    }
    for (const [nodeId] of latestUnseenNodeActivity) {
      if (!context.visibleGraphNodeIds.has(nodeId)) continue;
      scheduleRuntimePulse(nodeId);
    }
  }

  const documentLoaded = document !== null;

  useEffect(() => {
    if (!documentLoaded || !document) return;
    let cancelled = false;
    fetchJson<WorkflowEditorCatalog>(workflowCatalogPath(selectedDirectoryPath))
      .then((catalogPayload) => {
        if (cancelled) return;
        setCatalog(catalogPayload);
        setError(null);
        const pulseNodeIds = workflowGeneratorOpen ? new Set<string>() : runtimePulseNodeIdsRef.current;
        setFlowNodes((currentNodes) =>
          buildNodes(
            document.config,
            document.layout,
            catalogPayload,
            workflowGeneratorOpen ? {} : runSnapshot?.node_activity ?? {},
            validationDiagnostics,
            currentPositionsFromNodes(currentNodes),
            currentNodes.filter((node) => node.selected).map((node) => node.id),
            dirtyNodeIdSet,
            new Set<string>(),
            pulseNodeIds,
          ),
        );
        setFlowEdges(buildEdges(document.config, catalogPayload));
      })
      .catch((reason: unknown) => {
        if (cancelled) return;
        setError(reason instanceof Error ? reason.message : String(reason));
      });
    return () => {
      cancelled = true;
    };
  }, [documentLoaded, selectedDirectoryPath, workflowGeneratorOpen]);

  useEffect(() => {
    if (!document || isTutorialMode) return;
    const visibleGraphsForValidation = (document.graphs ?? []).filter((graph) =>
      graphVisibleInDirectory(graph, selectedDirectoryPath),
    );
    const outputSchemas = document.output_schemas ?? EMPTY_OUTPUT_SCHEMAS;
    const cache = validationCacheRef.current;
    const pendingGraphs: Array<{ graph: WorkflowGraphMetadata; signature: string }> = [];
    const visibleBackendDiagnostics = workflowDiagnosticsForGraphs(
      remoteOnlyWorkflowDiagnostics(backendValidationDiagnostics),
      visibleGraphsForValidation,
    );
    for (const graph of visibleGraphsForValidation) {
      const signature = workflowGraphValidationSignature(document.config, graph, outputSchemas);
      const cached = cache.get(graph.id);
      if (cached?.signature === signature) continue;
      cache.delete(graph.id);
      pendingGraphs.push({ graph, signature });
    }

    const cachedDiagnostics = cachedVisibleValidationDiagnostics(cache, visibleGraphsForValidation);
    setValidationDiagnostics(mergeWorkflowDiagnostics(visibleBackendDiagnostics, cachedDiagnostics));
    if (pendingGraphs.length === 0) return;

    let cancelled = false;
    let cancelScheduled = () => {};
    const runId = validationRunIdRef.current + 1;
    validationRunIdRef.current = runId;
    const runNext = () => {
      if (cancelled || validationRunIdRef.current !== runId) return;
      const next = pendingGraphs.shift();
      if (!next) return;
      const diagnostics = validateWorkflowGraphBindingDiagnostics(
        document.config,
        next.graph,
        outputSchemas,
      );
      if (cancelled || validationRunIdRef.current !== runId) return;
      cache.set(next.graph.id, {
        signature: next.signature,
        diagnostics,
      });
      setValidationDiagnostics(
        mergeWorkflowDiagnostics(
          visibleBackendDiagnostics,
          cachedVisibleValidationDiagnostics(cache, visibleGraphsForValidation),
        ),
      );
      if (pendingGraphs.length > 0) {
        cancelScheduled = scheduleWorkflowValidation(runNext);
      }
    };
    cancelScheduled = scheduleWorkflowValidation(runNext);
    return () => {
      cancelled = true;
      cancelScheduled();
    };
  }, [backendValidationDiagnostics, document?.config, document?.graphs, document?.output_schemas, selectedDirectoryPath, isTutorialMode]);

  useEffect(() => {
    if (workflowRunReadLocked || !pageVisible) {
      for (const timeout of runtimePulseTimeoutsRef.current.values()) {
        clearTimeout(timeout);
      }
      runtimePulseTimeoutsRef.current.clear();
      setRuntimePulseNodeIds((current) => current.size === 0 ? current : new Set<string>());
      return;
    }
    const stream = new EventSource("/api/workflow-editor/runs/stream");

    stream.addEventListener("snapshot", (event) => {
      if (!(event instanceof MessageEvent)) return;
      try {
        const payload = JSON.parse(event.data) as WorkflowRunSnapshot;
        applyWorkflowRunSnapshot(payload);
      } catch {
        // Ignore malformed event payloads and keep last known snapshot.
      }
    });

    stream.addEventListener("error", () => {
      // Keep the last snapshot visible when the stream reports an error.
    });

    return () => {
      stream.close();
      for (const timeout of runtimePulseTimeoutsRef.current.values()) {
        clearTimeout(timeout);
      }
      runtimePulseTimeoutsRef.current.clear();
    };
  }, [workflowRunReadLocked, pageVisible]);

  async function clearWorkflowRun(eventId: string, runState?: string) {
    const normalizedEventId = eventId.trim();
    if (!normalizedEventId || clearingRunIds[normalizedEventId] || workflowRunControlLocked) return;
    const inspectedRunEventId = (runDetail as WorkflowRunDetail | null)?.event_id ?? null;
    const verb = runState === "errored" ? "Dismiss" : "Clear";
    if (!window.confirm(`${verb} workflow run ${normalizedEventId}?`)) return;
    setClearingRunIds((current) => ({ ...current, [normalizedEventId]: true }));
    try {
      await fetchJson(`/api/workflow-editor/runs/${encodeURIComponent(normalizedEventId)}`, {
        method: "DELETE",
      });
      setRunSnapshot((current) => {
        if (!current) return current;
        return {
          ...current,
          runs: current.runs.filter((run) => run.event_id !== normalizedEventId),
        };
      });
      if (inspectedRunEventId === normalizedEventId) {
        exitInspectMode();
      }
      setError(null);
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setClearingRunIds((current) => {
        const next = { ...current };
        delete next[normalizedEventId];
        return next;
      });
    }
  }

  async function dismissNodeFailures(nodeId: string, failedCount: number) {
    const normalizedNodeId = nodeId.trim();
    if (!normalizedNodeId || dismissingNodeFailureIds[normalizedNodeId] || workflowRunControlLocked) return;
    const countLabel = failedCount === 1 ? "1 active fail log" : `${failedCount} active fail logs`;
    if (!window.confirm(`Dismiss all ${countLabel} for node ${normalizedNodeId}?`)) return;
    setDismissingNodeFailureIds((current) => ({ ...current, [normalizedNodeId]: true }));
    try {
      await fetchJson(`/api/workflow-editor/node-failures/${encodeURIComponent(normalizedNodeId)}`, {
        method: "DELETE",
      });
      const nextSnapshot = await fetchJson<WorkflowRunSnapshot>("/api/workflow-editor/runs");
      applyWorkflowRunSnapshot(nextSnapshot);
      setNodeConnectionMenu(null);
      setPendingConnection(null);
      setError(null);
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setDismissingNodeFailureIds((current) => {
        const next = { ...current };
        delete next[normalizedNodeId];
        return next;
      });
    }
  }

  async function resumeWorkflowRun(eventId: string) {
    const normalizedEventId = eventId.trim();
    if (!normalizedEventId || resumingRunIds[normalizedEventId] || workflowRunControlLocked) return;
    const inspectedRunEventId = (runDetail as WorkflowRunDetail | null)?.event_id ?? null;
    setResumingRunIds((current) => ({ ...current, [normalizedEventId]: true }));
    try {
      await fetchJson(`/api/workflow-editor/runs/${encodeURIComponent(normalizedEventId)}/resume`, {
        method: "POST",
      });
      if (inspectedRunEventId === normalizedEventId) {
        await loadWorkflowRunDetail(new URLSearchParams({ event_id: normalizedEventId }), normalizedEventId);
      }
      setError(null);
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setResumingRunIds((current) => {
        const next = { ...current };
        delete next[normalizedEventId];
        return next;
      });
    }
  }

  async function loadWorkflowRunDetail(query: URLSearchParams, lookupText?: string) {
    const queryText = query.toString();
    if (!queryText || runDetailLoading || workflowRunReadLocked) return;
    setRunDetailLoading(true);
    setRunDetailError(null);
    try {
      const detail = await fetchJson<WorkflowRunDetail>(`/api/workflow-editor/run-detail?${queryText}`);
      const preferredNodeId = preferredWorkflowRunDetailNodeId(detail);
      setRunDetail(detail);
      void loadOperatorSteeringNotesForDetail(detail);
      setRunDetailLookup(lookupText ?? detail.event_id);
      setSelectedNodeIds(preferredNodeId ? [preferredNodeId] : []);
      setSelectedNodeId(preferredNodeId);
      setSelectedEdgeId(null);
      setSelectedGraphId(null);
      setSnapGuides([]);
      closeConnectionUi();
      hasAutoFitRef.current = false;
    } catch (reason: unknown) {
      setRunDetailError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setRunDetailLoading(false);
    }
  }

  function setOperatorSteeringError(key: string, message: string | null) {
    setOperatorSteeringErrorsByKey((current) => {
      const next = { ...current };
      if (message) {
        next[key] = message;
      } else {
        delete next[key];
      }
      return next;
    });
  }

  async function loadOperatorSteeringNotes(sessionId: string, dispatchId: string) {
    const key = operatorSteeringKey(sessionId, dispatchId);
    if (!key || workflowRunReadLocked) return;
    setOperatorSteeringLoadingByKey((current) => ({ ...current, [key]: true }));
    try {
      const payload = await fetchJson<OperatorSteeringNoteListResponse>(
        `/api/operator-steering/notes?${operatorSteeringQuery(sessionId, dispatchId)}`,
      );
      setOperatorSteeringNotesByKey((current) => ({ ...current, [key]: payload.notes }));
      setOperatorSteeringError(key, null);
    } catch (reason: unknown) {
      setOperatorSteeringError(key, reason instanceof Error ? reason.message : String(reason));
    } finally {
      setOperatorSteeringLoadingByKey((current) => {
        const next = { ...current };
        delete next[key];
        return next;
      });
    }
  }

  async function loadOperatorSteeringNotesForDetail(detail: WorkflowRunDetail) {
    const seen = new Set<string>();
    const pairs = detail.nodes.flatMap((node) =>
      node.agent_links
        .map((agent) => ({
          sessionId: agent.session_id?.trim() ?? "",
          dispatchId: agent.dispatch_id?.trim() ?? "",
        }))
        .filter((pair) => pair.sessionId && pair.dispatchId),
    );
    for (const pair of pairs) {
      const key = operatorSteeringKey(pair.sessionId, pair.dispatchId);
      if (!key || seen.has(key)) continue;
      seen.add(key);
      void loadOperatorSteeringNotes(pair.sessionId, pair.dispatchId);
    }
  }

  async function submitOperatorSteeringNote(agent: WorkflowRunAgentLink) {
    const sessionId = agent.session_id?.trim() ?? "";
    const dispatchId = agent.dispatch_id?.trim() ?? "";
    const key = operatorSteeringKey(sessionId, dispatchId);
    if (!key) return;
    const message = (operatorSteeringDraftsByKey[key] ?? "").trim();
    if (!message) {
      setOperatorSteeringError(key, "Steering note is required.");
      return;
    }
    if (!canControlWorkflowRuns) {
      setOperatorSteeringError(key, "Permission required: workflow_runs.control");
      return;
    }
    if (!privilegedUnlocked) {
      setOperatorSteeringError(key, "Privileged operations are locked.");
      return;
    }
    setOperatorSteeringSubmittingByKey((current) => ({ ...current, [key]: true }));
    try {
      const payload = await fetchJson<OperatorSteeringNoteMutationResponse>(
        "/api/operator-steering/notes",
        {
          method: "POST",
          body: JSON.stringify({
            session_id: sessionId,
            dispatch_id: dispatchId,
            worker_execution_id: agent.execution_id ?? null,
            message,
          }),
        },
      );
      setOperatorSteeringNotesByKey((current) => ({
        ...current,
        [key]: [payload.note, ...(current[key] ?? []).filter((note) => note.note_id !== payload.note.note_id)],
      }));
      setOperatorSteeringDraftsByKey((current) => ({ ...current, [key]: "" }));
      setOperatorSteeringError(key, null);
    } catch (reason: unknown) {
      setOperatorSteeringError(key, reason instanceof Error ? reason.message : String(reason));
    } finally {
      setOperatorSteeringSubmittingByKey((current) => {
        const next = { ...current };
        delete next[key];
        return next;
      });
    }
  }

  async function cancelOperatorSteeringNote(note: OperatorSteeringNote) {
    const key = operatorSteeringKey(note.session_id, note.dispatch_id);
    if (!key || !canControlWorkflowRuns || !privilegedUnlocked || note.status !== "pending") return;
    setOperatorSteeringCancellingById((current) => ({ ...current, [note.note_id]: true }));
    try {
      const payload = await fetchJson<OperatorSteeringNoteMutationResponse>(
        `/api/operator-steering/notes/${encodeURIComponent(note.note_id)}/cancel`,
        { method: "POST" },
      );
      setOperatorSteeringNotesByKey((current) => ({
        ...current,
        [key]: (current[key] ?? []).map((entry) =>
          entry.note_id === payload.note.note_id ? payload.note : entry,
        ),
      }));
      setOperatorSteeringError(key, null);
    } catch (reason: unknown) {
      setOperatorSteeringError(key, reason instanceof Error ? reason.message : String(reason));
    } finally {
      setOperatorSteeringCancellingById((current) => {
        const next = { ...current };
        delete next[note.note_id];
        return next;
      });
    }
  }

  async function loadWorkflowRunSearch(query: URLSearchParams) {
    const queryText = query.toString();
    if (!queryText || runSearchLoading || workflowRunReadLocked) return;
    setRunSearchLoading(true);
    setRunSearchError(null);
    try {
      const payload = await fetchJson<WorkflowRunSearch>(`/api/workflow-editor/run-search?${queryText}`);
      setRunSearchResult(payload);
    } catch (reason: unknown) {
      setRunSearchError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setRunSearchLoading(false);
    }
  }

  function inspectWorkflowRun(eventId: string) {
    if (activeWorkflowTutorial) return;
    if (workflowRunReadLocked) return;
    const normalizedEventId = eventId.trim();
    if (!normalizedEventId) return;
    void loadWorkflowRunDetail(new URLSearchParams({ event_id: normalizedEventId }), normalizedEventId);
  }

  useEffect(() => {
    const applyDeepLink = () => {
      if (workflowRunReadLocked) {
        return;
      }
      const lookup = workflowRunLookupFromHash();
      if (!lookup) {
        return;
      }
      setRunDetailLookup(lookup.value);
      setRunDetailLookupKind(lookup.kind);
      void loadWorkflowRunDetail(workflowRunDetailLookupQuery(lookup.value, lookup.kind), lookup.value);
    };

    applyDeepLink();
    window.addEventListener("hashchange", applyDeepLink);
    return () => window.removeEventListener("hashchange", applyDeepLink);
  }, [workflowRunReadLocked]);

  function submitWorkflowRunDetailLookup(event: ReactFormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (workflowRunReadLocked) return;
    const normalizedLookup = runDetailLookup.trim();
    if (!normalizedLookup) return;
    void loadWorkflowRunDetail(workflowRunDetailLookupQuery(normalizedLookup, runDetailLookupKind), normalizedLookup);
  }

  function submitWorkflowRunSearch(event: ReactFormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (workflowRunReadLocked) return;
    const query = workflowRunSearchQuery(runSearchScope, {
      withoutSession: runSearchWithoutSession,
      includeCompleted: runSearchIncludeCompleted,
      limit: 25,
    });
    if (!query) return;
    void loadWorkflowRunSearch(query);
  }

  function tutorialCurrentRequiredAction(): WorkflowTutorialRequiredAction | null {
    return activeWorkflowTutorialStep?.requiredAction ?? null;
  }

  function tutorialActionMatchesCurrentStep(action: WorkflowTutorialRequiredAction): boolean {
    const requiredAction = tutorialCurrentRequiredAction();
    return requiredAction ? workflowTutorialRequiredActionMatches(requiredAction, action) : false;
  }

  function tutorialActionHintFor(requiredAction: WorkflowTutorialRequiredAction | null): string {
    if (!requiredAction) return "";
    return `Follow this step: ${tutorialRequiredActionDescription(requiredAction)}`;
  }

  function tutorialAllowsAction(action: WorkflowTutorialRequiredAction): boolean {
    if (!isTutorialMode) return true;
    return tutorialActionMatchesCurrentStep(action);
  }

  function tutorialAllowsNodeType(kind: WorkflowNodeKind, type: string): boolean {
    const requiredAction = tutorialCurrentRequiredAction();
    return Boolean(
      isTutorialMode &&
      requiredAction?.kind === "add-node" &&
      requiredAction.nodeKind === kind &&
      requiredAction.nodeType === type,
    );
  }

  function tutorialAllowsInspectorFieldEdit(fieldKey: string): boolean {
    const requiredAction = tutorialCurrentRequiredAction();
    return Boolean(
      isTutorialMode &&
      requiredAction?.kind === "bind-field" &&
      selectedNode?.id === requiredAction.nodeId &&
      requiredAction.fieldKey === fieldKey,
    );
  }

  async function saveWorkflowTutorialProgress(nextProgress: WorkflowTutorialProgress) {
    try {
      const payload = await fetchJson<unknown>(WORKFLOW_TUTORIAL_PROGRESS_API_PATH, {
        method: "PUT",
        body: JSON.stringify(workflowTutorialProgressApiPayload(nextProgress)),
      });
      setWorkflowTutorialProgress(normalizeWorkflowTutorialProgress(payload));
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  function workflowTutorialProgressWithStatus(
    current: WorkflowTutorialProgress,
    tutorialId: string,
    status: "completed" | "skipped" | null,
  ): WorkflowTutorialProgress {
    const completedTutorialIds = current.completedTutorialIds.filter((id) => id !== tutorialId);
    const skippedTutorialIds = current.skippedTutorialIds.filter((id) => id !== tutorialId);
    if (status === "completed") {
      completedTutorialIds.push(tutorialId);
    } else if (status === "skipped") {
      skippedTutorialIds.push(tutorialId);
    }
    return { completedTutorialIds, skippedTutorialIds };
  }

  function updateWorkflowTutorialProgressStatus(
    tutorialId: string,
    status: "completed" | "skipped" | null,
  ) {
    const nextProgress = workflowTutorialProgressWithStatus(workflowTutorialProgress, tutorialId, status);
    setWorkflowTutorialProgress(nextProgress);
    void saveWorkflowTutorialProgress(nextProgress);
  }

  function clearWorkflowTutorialProgressStatus(tutorialId: string) {
    updateWorkflowTutorialProgressStatus(tutorialId, null);
  }

  function completeTutorialAction(action: WorkflowTutorialRequiredAction): boolean {
    if (!isTutorialMode) return true;
    const requiredAction = tutorialCurrentRequiredAction();
    if (!requiredAction || !workflowTutorialRequiredActionMatches(requiredAction, action)) {
      setWorkflowTutorialHint(tutorialActionHintFor(requiredAction));
      return false;
    }
    setWorkflowTutorialHint(null);
    if (requiredAction.kind === "reset") {
      resetWorkflowTutorial();
      return true;
    }
    advanceWorkflowTutorial();
    return true;
  }

  function startWorkflowTutorial(tutorialId = "first-steps") {
    setWorkflowTutorialChooserOpen(false);
    if (activeWorkflowTutorial) return;
    const definition = workflowTutorialDefinitionById(tutorialId);
    if (!definition) return;
    tutorialSnapshotRef.current = {
      selectedNodeIds,
      selectedNodeId,
      selectedEdgeId,
      selectedGraphId,
      runDetail,
      canvasViewport,
      viewportWasMeaningful: isMeaningfulViewport(canvasViewport),
    };
    setWorkflowTutorialHint(null);
    setActiveWorkflowTutorial(createWorkflowTutorialSession(definition.id));
    setRunDetail(null);
    setProgrammaticSelection([]);
    setSelectedEdgeId(null);
    setSelectedGraphId(null);
    setNodeConnectionMenu(null);
    setPendingConnection(null);
    setSnapGuides([]);
    hasAutoFitRef.current = false;
    setError(null);
  }

  function restartWorkflowTutorial(tutorialId = "first-steps") {
    const definition = workflowTutorialDefinitionById(tutorialId);
    if (!definition) return;
    setWorkflowTutorialChooserOpen(false);
    if (activeWorkflowTutorial?.tutorialId === definition.id) {
      resetWorkflowTutorial();
      return;
    }
    startWorkflowTutorial(definition.id);
  }

  function skipWorkflowTutorial(tutorialId = "first-steps") {
    const definition = workflowTutorialDefinitionById(tutorialId);
    if (!definition) return;
    updateWorkflowTutorialProgressStatus(definition.id, "skipped");
    setWorkflowTutorialChooserOpen(false);
    if (activeWorkflowTutorial?.tutorialId === definition.id) {
      exitWorkflowTutorial({ markCompleted: false });
    }
  }

  function workflowTutorialChooserStatusFor(tutorialId: string): WorkflowTutorialChooserStatus {
    if (activeWorkflowTutorial?.tutorialId === tutorialId) return "in-progress";
    if (workflowTutorialProgressSets.completed.has(tutorialId)) return "completed";
    if (workflowTutorialProgressSets.skipped.has(tutorialId)) return "skipped";
    return "available";
  }

  function workflowTutorialChooserStatusLabel(status: WorkflowTutorialChooserStatus): string {
    switch (status) {
      case "completed":
        return "Completed";
      case "skipped":
        return "Skipped";
      case "in-progress":
        return "In progress";
      case "available":
      default:
        return "Ready";
    }
  }

  function workflowTutorialIsAtFinalStep(tutorial: WorkflowTutorialSession | null): boolean {
    if (!tutorial) return false;
    const definition = workflowTutorialDefinitionById(tutorial.tutorialId);
    if (!definition) return false;
    return tutorial.stepIndex >= definition.steps.length - 1;
  }

  function markActiveWorkflowTutorialCompletedIfFinished() {
    if (!activeWorkflowTutorial || !workflowTutorialIsAtFinalStep(activeWorkflowTutorial)) return;
    updateWorkflowTutorialProgressStatus(activeWorkflowTutorial.tutorialId, "completed");
  }

  function advanceWorkflowTutorial() {
    const currentTutorial = activeWorkflowTutorial;
    const currentDefinition = currentTutorial ? workflowTutorialDefinitionById(currentTutorial.tutorialId) : null;
    const currentNextStepIndex = currentTutorial && currentDefinition
      ? Math.min(currentTutorial.stepIndex + 1, currentDefinition.steps.length - 1)
      : null;
    const reachedFinalStep = Boolean(
      currentTutorial &&
      currentDefinition &&
      currentNextStepIndex !== null &&
      currentNextStepIndex !== currentTutorial.stepIndex &&
      currentNextStepIndex === currentDefinition.steps.length - 1,
    );
    setActiveWorkflowTutorial((current) => {
      if (!current) return current;
      const definition = workflowTutorialDefinitionById(current.tutorialId);
      if (!definition) return current;
      const nextStepIndex = Math.min(current.stepIndex + 1, definition.steps.length - 1);
      if (nextStepIndex === current.stepIndex) return current;
      hasAutoFitRef.current = false;
      setWorkflowTutorialHint(null);
      return {
        ...current,
        stepIndex: nextStepIndex,
      };
    });
    if (reachedFinalStep && currentTutorial) {
      updateWorkflowTutorialProgressStatus(currentTutorial.tutorialId, "completed");
    }
  }

  function resetWorkflowTutorial() {
    markActiveWorkflowTutorialCompletedIfFinished();
    setActiveWorkflowTutorial((current) => {
      if (!current) return current;
      const definition = workflowTutorialDefinitionById(current.tutorialId);
      if (!definition) return current;
      hasAutoFitRef.current = false;
      setWorkflowTutorialHint(null);
      return createWorkflowTutorialSession(definition.id);
    });
    setProgrammaticSelection([]);
    setSelectedEdgeId(null);
    setSelectedGraphId(null);
    setNodeConnectionMenu(null);
    setPendingConnection(null);
    setSnapGuides([]);
  }

  function exitWorkflowTutorial(options: { markCompleted?: boolean } = {}) {
    if (options.markCompleted !== false) {
      markActiveWorkflowTutorialCompletedIfFinished();
    }
    const snapshot = tutorialSnapshotRef.current;
    setActiveWorkflowTutorial(null);
    setWorkflowTutorialHint(null);
    setNodeConnectionMenu(null);
    setPendingConnection(null);
    setSnapGuides([]);
    if (!snapshot) {
      return;
    }
    if (document) {
      const validNodeIds = new Set(workflowNodeIds(document.config));
      const restoredSelectedNodeIds = snapshot.selectedNodeIds.filter((nodeId) => validNodeIds.has(nodeId));
      const restoredDirtyNodeIds = dirtyWorkflowNodeIds(document, savedDocument, nodeEditOrigins);
      setFlowNodes((currentNodes) =>
        buildNodes(
          document.config,
          document.layout,
          catalog,
          workflowGeneratorOpen ? {} : runSnapshot?.node_activity ?? {},
          validationDiagnostics,
          currentPositionsFromNodes(currentNodes),
          restoredSelectedNodeIds,
          new Set(restoredDirtyNodeIds),
          new Set<string>(),
          workflowGeneratorOpen ? new Set<string>() : runtimePulseNodeIds,
        ),
      );
      setFlowEdges(buildEdges(document.config, catalog));
    }
    setProgrammaticSelection(snapshot.selectedNodeIds, snapshot.selectedNodeId);
    setSelectedEdgeId(snapshot.selectedEdgeId);
    setSelectedGraphId(snapshot.selectedGraphId);
    setRunDetail(snapshot.runDetail);
    setCanvasViewport(snapshot.canvasViewport);
    if (flowInstance) {
      void flowInstance.setViewport(snapshot.canvasViewport);
    }
    hasAutoFitRef.current = snapshot.viewportWasMeaningful;
  }

  function exitInspectMode() {
    setRunDetail(null);
    setOperatorSteeringNotesByKey({});
    setOperatorSteeringDraftsByKey({});
    setOperatorSteeringErrorsByKey({});
    setSelectedNodeIds([]);
    setSelectedNodeId(null);
    setSelectedEdgeId(null);
    setSnapGuides([]);
    closeConnectionUi();
    hasAutoFitRef.current = false;
  }

  function openWorkflowGenerator() {
    if (workflowEditLocked) return;
    if (hasUnsavedWorkflowChanges) {
      const confirmed = window.confirm(
        "Opening the workflow generator will discard unsaved workflow edits. Continue?",
      );
      if (!confirmed) return;
    }
    workflowGeneratorAbortRef.current?.abort();
    workflowGeneratorRequestIdRef.current += 1;
    setWorkflowGeneratorOpen(true);
    setWorkflowGeneratorDescription("");
    setWorkflowGeneratorState("idle");
    setWorkflowGeneratorError(null);
    setWorkflowGeneratorWarnings([]);
    setWorkflowGeneratorModel(null);
    setWorkflowGeneratorReadyDescription(null);
    setRunDetail(null);
    setNodeConnectionMenu(null);
    setPendingConnection(null);
    setSelectedEdgeId(null);
    setSelectedGraphId(null);
    setNodeEditOrigins({});
    applyWorkflowDocumentState(
      blankWorkflowGeneratorDocument(savedDocument, selectedDirectoryPath),
      [],
      [],
      { autoFit: true },
    );
  }

  function cancelWorkflowGenerator() {
    workflowGeneratorAbortRef.current?.abort();
    workflowGeneratorRequestIdRef.current += 1;
    setWorkflowGeneratorOpen(false);
    setWorkflowGeneratorDescription("");
    setWorkflowGeneratorState("idle");
    setWorkflowGeneratorError(null);
    setWorkflowGeneratorWarnings([]);
    setWorkflowGeneratorModel(null);
    setWorkflowGeneratorReadyDescription(null);
    setSelectedEdgeId(null);
    setSelectedGraphId(null);
    setNodeEditOrigins({});
    if (savedDocument) {
      applyWorkflowDocumentState(
        savedDocument,
        [],
        [],
        { autoFit: true },
      );
    }
  }

  function acceptWorkflowGeneratorDraft() {
    if (workflowEditLocked || !workflowGeneratorOpen || !document || !savedDocument) return;
    if (workflowGeneratorState !== "ready") {
      setWorkflowGeneratorError("Generate a ready draft before accepting it.");
      return;
    }
    if (workflowGeneratorReadyDescription !== workflowGeneratorDescription.trim()) {
      setWorkflowGeneratorError("Generate a ready draft for the current description before accepting it.");
      return;
    }
    const generatedNodeIds = workflowNodeIds(document.config);
    if (generatedNodeIds.length === 0) {
      setWorkflowGeneratorError("Generate a workflow before accepting the draft.");
      return;
    }
    const merged = mergeGeneratedDraftIntoWorkflow(savedDocument, document, selectedDirectoryPath);
    workflowGeneratorAbortRef.current?.abort();
    workflowGeneratorRequestIdRef.current += 1;
    setWorkflowGeneratorOpen(false);
    setWorkflowGeneratorDescription("");
    setWorkflowGeneratorState("idle");
    setWorkflowGeneratorError(null);
    setWorkflowGeneratorWarnings([]);
    setWorkflowGeneratorModel(null);
    setWorkflowGeneratorReadyDescription(null);
    setNodeEditOrigins({});
    applyWorkflowDocumentState(merged.document, merged.importedNodeIds, undefined, { autoFit: true });
    const selectedGeneratedGraph = merged.document.graphs.find((graph) =>
      graph.node_ids.some((nodeId) => merged.importedNodeIds.includes(nodeId)),
    );
    setSelectedGraphId(selectedGeneratedGraph?.id ?? null);
  }

  function resetWorkflowGeneratorPreview(nextDescription: string) {
    workflowGeneratorAbortRef.current?.abort();
    workflowGeneratorRequestIdRef.current += 1;
    setWorkflowGeneratorState(nextDescription.trim() ? "draftable" : "idle");
    setWorkflowGeneratorError(null);
    setWorkflowGeneratorWarnings([]);
    setWorkflowGeneratorModel(null);
    setWorkflowGeneratorReadyDescription(null);
    applyWorkflowDocumentState(
      blankWorkflowGeneratorDocument(savedDocument, selectedDirectoryPath),
      [],
      [],
      { autoFit: true },
    );
  }

  function handleWorkflowGeneratorDescriptionChange(value: string) {
    setWorkflowGeneratorDescription(value);
    resetWorkflowGeneratorPreview(value);
  }

  async function generateWorkflowGeneratorDraft() {
    if (workflowEditLocked || !workflowGeneratorOpen) return;
    const description = workflowGeneratorDescription.trim();
    workflowGeneratorAbortRef.current?.abort();
    workflowGeneratorRequestIdRef.current += 1;
    setWorkflowGeneratorReadyDescription(null);
    if (!description) {
      resetWorkflowGeneratorPreview(description);
      return;
    }
    if (description.length < WORKFLOW_GENERATOR_MIN_DESCRIPTION_LENGTH) {
      setWorkflowGeneratorState("draftable");
      setWorkflowGeneratorError(
        `Describe the workflow in at least ${WORKFLOW_GENERATOR_MIN_DESCRIPTION_LENGTH} characters before generating.`,
      );
      return;
    }

    const requestId = workflowGeneratorRequestIdRef.current;
    const controller = new AbortController();
    workflowGeneratorAbortRef.current = controller;
    setWorkflowGeneratorState("generating");
    setWorkflowGeneratorError(null);
    setWorkflowGeneratorWarnings([]);
    setWorkflowGeneratorModel(null);
    try {
      const draft = await fetchJson<WorkflowEditorGeneratedDraft>("/api/workflow-editor/generate-draft", {
        method: "POST",
        signal: controller.signal,
        body: JSON.stringify({
          description,
          directory_path: selectedDirectoryPath,
          include_current_packs: true,
        }),
      });
      if (requestId !== workflowGeneratorRequestIdRef.current) return;
      const draftDocument = normalizeWorkflowEditorDocument(
        {
          config_path: savedDocument?.config_path ?? "",
          layout_path: savedDocument?.layout_path ?? "",
          config_exists: Boolean(savedDocument?.config_exists),
          layout_exists: Boolean(savedDocument?.layout_exists),
          config: draft.config,
          layout: draft.layout,
          graphs: draft.graphs,
          output_schemas: draft.output_schemas,
          validation_diagnostics: draft.validation_diagnostics,
          revision: savedDocument?.revision ?? "generator-draft",
        },
        selectedDirectoryPath,
      );
      applyWorkflowDocumentState(
        draftDocument,
        workflowNodeIds(draftDocument.config),
        draft.validation_diagnostics,
        { autoFit: true },
      );
      setWorkflowGeneratorWarnings(draft.warnings ?? []);
      setWorkflowGeneratorModel(draft.model);
      setWorkflowGeneratorReadyDescription(description);
      setWorkflowGeneratorState("ready");
      setWorkflowGeneratorError(null);
    } catch (reason: unknown) {
      if (requestId !== workflowGeneratorRequestIdRef.current) return;
      if (reason instanceof DOMException && reason.name === "AbortError") return;
      setWorkflowGeneratorState("error");
      setWorkflowGeneratorError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      if (workflowGeneratorAbortRef.current === controller) {
        workflowGeneratorAbortRef.current = null;
      }
    }
  }

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

  useEffect(() => {
    if (!document) return;
    setFlowNodes((currentNodes) => {
      const pulseNodeIds = workflowGeneratorOpen ? new Set<string>() : runtimePulseNodeIds;
      return buildNodes(
        document.config,
        document.layout,
        catalog,
        workflowGeneratorOpen ? {} : runSnapshot?.node_activity ?? {},
        validationDiagnostics,
        currentPositionsFromNodes(currentNodes),
        currentNodes.filter((node) => node.selected).map((node) => node.id),
        dirtyNodeIdSet,
        new Set<string>(),
        pulseNodeIds,
      );
    });
    setFlowEdges(buildEdges(document.config, catalog));
  }, [catalog, document, dirtyNodeIds, runSnapshot, runtimePulseNodeIds, validationDiagnostics, workflowGeneratorOpen]);

  useEffect(() => {
    hasAutoFitRef.current = false;
  }, [selectedDirectoryPath, runDetail?.event_id]);

  useEffect(() => {
    if (!runDetail || workflowRunReadLocked) return;
    void loadOperatorSteeringNotesForDetail(runDetail);
  }, [runDetail?.event_id, workflowRunReadLocked]);

  useEffect(() => {
    if (!flowInstance || displayedFlowNodes.length === 0 || hasAutoFitRef.current) return;
    queueMicrotask(() => {
      void flowInstance.fitView({ padding: 0.2 }).finally(() => {
        setCanvasViewport(flowInstance.getViewport());
      });
      hasAutoFitRef.current = true;
    });
  }, [flowInstance, displayedFlowNodes, displayedFlowEdges]);

  useEffect(() => {
    if (!isTutorialMode || !activeWorkflowTutorialStep) {
      setTutorialBubbleSize({ width: 0, height: 0 });
      return;
    }
    const bubble = tutorialBubbleRef.current;
    if (!bubble) return;
    const updateBubbleSize = () => {
      const rect = bubble.getBoundingClientRect();
      setTutorialBubbleSize({ width: rect.width, height: rect.height });
    };
    updateBubbleSize();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(updateBubbleSize);
    observer.observe(bubble);
    return () => observer.disconnect();
  }, [activeWorkflowTutorialStep?.id, isTutorialMode]);

  useEffect(() => {
    if (!isTutorialMode || !activeWorkflowTutorialStep || !flowInstance) return;
    if (activeWorkflowTutorialStep.camera === "none") return;
    const targetNode = tutorialCameraTargetNode;
    if (!targetNode) return;

    let cancelled = false;
    const applyCamera = async () => {
      if (activeWorkflowTutorialStep.camera === "fit-node") {
        try {
          await flowInstance.fitView({ nodes: [{ id: targetNode.id }], padding: 0.28, duration: 350 });
        } catch {
          const bounds = nodeBounds(targetNode);
          flowInstance.setCenter(bounds.centerX, bounds.centerY, {
            zoom: flowInstance.getViewport().zoom || 1,
            duration: 350,
          });
        }
      } else if (activeWorkflowTutorialStep.camera === "center-node") {
        const bounds = nodeBounds(targetNode);
        flowInstance.setCenter(bounds.centerX, bounds.centerY, {
          zoom: flowInstance.getViewport().zoom || 1,
          duration: 300,
        });
      }
      if (!cancelled) {
        setCanvasViewport(flowInstance.getViewport());
      }
    };
    queueMicrotask(() => {
      void applyCamera();
    });
    return () => {
      cancelled = true;
    };
  }, [activeWorkflowTutorialStep?.camera, activeWorkflowTutorialStep?.id, flowInstance, isTutorialMode, tutorialCameraTargetKey]);

  useEffect(() => {
    if (!selectedGraph) {
      setGraphNameDraft("");
      setGraphDescriptionDraft("");
      setGraphDirectoryDraft("");
      setGraphUsersDraft("");
      return;
    }
    setGraphNameDraft(selectedGraph.name);
    setGraphDescriptionDraft(selectedGraph.description ?? "");
    setGraphDirectoryDraft(selectedGraph.directory_path || selectedDirectoryPath);
    setGraphUsersDraft(selectedGraph.user_ids.join("\n"));
  }, [selectedGraph, selectedDirectoryPath]);

  useEffect(() => {
    if (!selectedGraphId || visibleWorkflowGraphs.some((graph) => graph.id === selectedGraphId)) return;
    setSelectedGraphId(null);
  }, [selectedGraphId, visibleWorkflowGraphs]);

  function hydrateInspectorDraftFromNode(
    nextNode: WorkflowNodeConfig | undefined,
    nextCatalogEntry: WorkflowEditorNodeCatalogEntry | undefined,
  ) {
    if (!nextNode) {
      setInspectorDraftError(null);
      setInspectorNodeId("");
      setInspectorDisplayName("");
      setInspectorKnownValues({});
      setInspectorUnknownValues({});
      setInspectorJsonDrafts({});
      setInspectorToolArgumentPolicyDrafts({});
      setInspectorJsonDisplayModes({});
      setInspectorStringListDrafts({});
      setInspectorStringMapDrafts({});
      setInspectorSessionListMetadataRules([]);
      setInspectorSessionListMetadataRawMode(false);
      setInspectorFieldModes({});
      setInspectorEffectTargetKey("target");
      setRawJsonDraft("{}");
      return;
    }

    const { id: _id, display_name: _displayName, type: _type, ...extras } = nextNode;
    const knownFieldKeys = new Set((nextCatalogEntry?.fields ?? []).map((field) => field.key));
    if (nextNode.type.startsWith("trigger:")) {
      knownFieldKeys.add("enabled");
    }
    const nextKnownValues = Object.fromEntries(Object.entries(extras).filter(([key]) => knownFieldKeys.has(key)));
    const nextUnknownValues = Object.fromEntries(Object.entries(extras).filter(([key]) => !knownFieldKeys.has(key)));
    const nextJsonDrafts: Record<string, string> = {};
    const nextToolArgumentPolicyDrafts: Record<string, ToolArgumentPolicyDraftRow[]> = {};
    const nextJsonDisplayModes: Record<string, JsonDisplayMode> = {};
    const nextStringListDrafts: Record<string, string> = {};
    const nextStringMapDrafts: Record<string, StringMapDraftRow[]> = {};
    const nextSessionListMetadataRules = nextNode.type === "query:session:list"
      ? sessionListMetadataRuleDraftRowsFromValues(nextKnownValues).rows
      : [];
    const nextFieldModes: Record<string, "literal" | "bind" | "template"> = {};
    for (const field of nextCatalogEntry?.fields ?? []) {
      nextFieldModes[field.key] = fieldValueMode(field, nextKnownValues[field.key]);
      if (field.kind !== "json") continue;
      if (isExactBindingValue(nextKnownValues[field.key])) {
        const fallbackValue = field.required || field.key === "outputs" ? {} : undefined;
        const fallbackDraft = workflowJsonFieldDraftFallback(field, fallbackValue);
        nextJsonDrafts[field.key] = formatJson(nextKnownValues[field.key], fallbackDraft);
        if (field.key === "tool_argument_policies" && supportsStructuredToolArgumentPolicies(nextNode.type)) {
          nextJsonDisplayModes[field.key] = "raw";
        }
        continue;
      }
      if (field.key === "tool_argument_policies" && supportsStructuredToolArgumentPolicies(nextNode.type)) {
        const fieldValue = nextKnownValues[field.key] ?? workflowJsonFieldFallbackValue(field, {});
        const structuredRows = toolArgumentPolicyDraftRowsFromValue(fieldValue, field.options.map((option) => option.value));
        if (structuredRows) {
          nextToolArgumentPolicyDrafts[field.key] = structuredRows;
          nextJsonDisplayModes[field.key] = "structured";
          const validation = toolArgumentPolicyDraftRowsToValue(structuredRows);
          nextJsonDrafts[field.key] = JSON.stringify(validation.value ?? {}, null, 2);
        } else {
          nextJsonDisplayModes[field.key] = "raw";
          nextJsonDrafts[field.key] = JSON.stringify(fieldValue, null, 2);
        }
        continue;
      }
      const fallbackValue = field.required || field.key === "outputs" ? {} : undefined;
      const fallbackDraft = workflowJsonFieldDraftFallback(field, fallbackValue);
      nextJsonDrafts[field.key] = nextKnownValues[field.key] === undefined
        ? fallbackDraft
        : formatJson(nextKnownValues[field.key], fallbackDraft);
    }
    for (const field of nextCatalogEntry?.fields ?? []) {
      if (field.kind !== "object_list") continue;
      const items: unknown[] = Array.isArray(nextKnownValues[field.key]) ? (nextKnownValues[field.key] as unknown[]) : [];
      items.forEach((item: unknown, index: number) => {
        const itemValue = isRecord(item) ? item : {};
        field.item_fields.forEach((itemField) => {
          if (itemField.kind !== "json") return;
          const draftKey = objectListJsonDraftKey(field.key, index, itemField.key);
          const nestedValue = itemValue[itemField.key];
          const fallbackValue = itemField.required ? {} : undefined;
          const fallbackDraft = workflowJsonFieldDraftFallback(itemField, fallbackValue);
          nextJsonDrafts[draftKey] = nestedValue === undefined
            ? fallbackDraft
            : formatJson(nestedValue, fallbackDraft);
        });
      });
    }
    for (const field of nextCatalogEntry?.fields ?? []) {
      if (field.kind !== "string_list") continue;
      nextStringListDrafts[field.key] = Array.isArray(nextKnownValues[field.key])
        ? (nextKnownValues[field.key] as unknown[]).map((item) => String(item)).join("\n")
        : "";
    }
    for (const field of nextCatalogEntry?.fields ?? []) {
      if (field.kind !== "string_map") continue;
      nextStringMapDrafts[field.key] = stringMapDraftRows(nextKnownValues[field.key]);
    }
    setInspectorNodeId(nextNode.id);
    setInspectorDisplayName(nextNode.display_name);
    setInspectorKnownValues(nextKnownValues);
    setInspectorUnknownValues(nextUnknownValues);
    setInspectorJsonDrafts(nextJsonDrafts);
    setInspectorToolArgumentPolicyDrafts(nextToolArgumentPolicyDrafts);
    setInspectorJsonDisplayModes(nextJsonDisplayModes);
    setInspectorStringListDrafts(nextStringListDrafts);
    setInspectorStringMapDrafts(nextStringMapDrafts);
    setInspectorSessionListMetadataRules(nextSessionListMetadataRules);
    setInspectorSessionListMetadataRawMode(false);
    setInspectorFieldModes(nextFieldModes);
    setInspectorEffectTargetKey(activeEffectTargetKey(nextKnownValues));
    setRawJsonDraft(JSON.stringify(extras, null, 2));
    setInspectorDraftError(null);
  }

  useEffect(() => {
    hydrateInspectorDraftFromNode(selectedNode, selectedCatalogEntry);
  }, [selectedNode?.id, selectedCatalogEntry]);

  useEffect(() => {
    if (!selectedEdgeId) return;
    appShellRef.current?.focus();
  }, [selectedEdgeId]);

  useEffect(() => {
    if (isInspectMode || isTutorialMode) return;
    const nextSelectedNodeIds = flowNodes
      .filter((node) => node.selected && visibleGraphNodeIds.has(node.id))
      .map((node) => node.id);
    setSelectedNodeIds(nextSelectedNodeIds);
    setSelectedNodeId((currentSelectedNodeId) => {
      if (nextSelectedNodeIds.length === 0) return null;
      if (currentSelectedNodeId && nextSelectedNodeIds.includes(currentSelectedNodeId)) return currentSelectedNodeId;
      return nextSelectedNodeIds[nextSelectedNodeIds.length - 1] ?? null;
    });
  }, [flowNodes, visibleGraphNodeIdKey, isInspectMode, isTutorialMode]);

  function applyWorkflowDocumentState(
    nextDocument: WorkflowEditorDocument,
    selectedNodeIdsAfterPatch: string[] = [],
    diagnosticsOverride?: WorkflowValidationDiagnostic[],
    options: { autoFit?: boolean; markClean?: boolean; nodeEditOrigins?: Record<string, string> } = {},
  ) {
    const normalizedDocument = normalizeWorkflowEditorDocument(nextDocument, selectedDirectoryPath);
    const validNodeIds = new Set(workflowNodeIds(normalizedDocument.config));
    const nextSelectedNodeIds = Array.from(new Set(selectedNodeIdsAfterPatch)).filter((nodeId) =>
      validNodeIds.has(nodeId),
    );
    const visibleGraphsForDocument = (normalizedDocument.graphs ?? []).filter((graph) =>
      graphVisibleInDirectory(graph, selectedDirectoryPath),
    );
    const nextBackendDiagnostics = diagnosticsOverride ?? [];
    const nextDiagnostics = diagnosticsOverride
      ? workflowDiagnosticsForGraphs(diagnosticsOverride, visibleGraphsForDocument)
      : [];
    const baselineDocument = options.markClean ? normalizedDocument : savedDocument;
    const nextDirtyNodeIds = dirtyWorkflowNodeIds(
      normalizedDocument,
      baselineDocument,
      options.nodeEditOrigins ?? nodeEditOrigins,
    );
    setDocument(normalizedDocument);
    setBackendValidationDiagnostics(nextBackendDiagnostics);
    setValidationDiagnostics(nextDiagnostics);
    setSelectedNodeIds(nextSelectedNodeIds);
    setSelectedNodeId((currentSelectedNodeId) => {
      if (nextSelectedNodeIds.length === 0) return null;
      if (currentSelectedNodeId && nextSelectedNodeIds.includes(currentSelectedNodeId)) {
        return currentSelectedNodeId;
      }
      return nextSelectedNodeIds[0] ?? null;
    });
    setFlowNodes((currentNodes) =>
      buildNodes(
        normalizedDocument.config,
        normalizedDocument.layout,
        catalog,
        workflowGeneratorOpen ? {} : runSnapshot?.node_activity ?? {},
        nextDiagnostics,
        currentPositionsFromNodes(currentNodes),
        nextSelectedNodeIds,
        new Set(nextDirtyNodeIds),
        new Set<string>(),
        workflowGeneratorOpen ? new Set<string>() : runtimePulseNodeIds,
      ),
    );
    setFlowEdges(buildEdges(normalizedDocument.config, catalog));
    setSaveState("idle");
    if (options.autoFit) {
      hasAutoFitRef.current = false;
    }
  }

  function rememberWorkflowUndoSnapshot(snapshot: WorkflowEditorDocument) {
    const entry: WorkflowUndoEntry = {
      document: cloneWorkflowDocumentSurface(snapshot),
      nodeEditOrigins: { ...nodeEditOrigins },
    };
    setUndoHistory((current) => {
      const lastEntry = current[current.length - 1];
      if (
        lastEntry &&
        workflowUndoDocumentsEqual(lastEntry.document, entry.document) &&
        workflowValuesEqual(lastEntry.nodeEditOrigins, entry.nodeEditOrigins)
      ) {
        return current;
      }
      return [...current, entry].slice(-WORKFLOW_UNDO_HISTORY_LIMIT);
    });
  }

  function undoWorkflowChange() {
    if (!canUndoWorkflowChanges || !document) return;
    const entry = undoHistory[undoHistory.length - 1];
    if (!entry) return;
    const restoredDocument = normalizeWorkflowEditorDocument(
      {
        ...document,
        ...cloneWorkflowDocumentSurface(entry.document),
      },
      selectedDirectoryPath,
    );
    const restoredNodeIds = new Set(workflowNodeIds(restoredDocument.config));
    const nextSelectedNodeIds = selectedNodeIds.filter((nodeId) => restoredNodeIds.has(nodeId));
    setUndoHistory((current) => current.slice(0, -1));
    setNodeEditOrigins({ ...entry.nodeEditOrigins });
    applyWorkflowDocumentState(
      restoredDocument,
      nextSelectedNodeIds,
      restoredDocument.validation_diagnostics ?? [],
      { nodeEditOrigins: entry.nodeEditOrigins },
    );
    setSelectedEdgeId((currentEdgeId) => (
      currentEdgeId && restoredDocument.config.edges.some((edge) => edgeId(edge) === currentEdgeId)
        ? currentEdgeId
        : null
    ));
    setPendingConnection(null);
    setNodeConnectionMenu(null);
    setInspectorDraftError(null);
    setError(null);
  }

  useLayoutEffect(() => {
    const previousDirectoryPath = previousSelectedDirectoryPathRef.current;
    if (previousDirectoryPath === selectedDirectoryPath) return;
    previousSelectedDirectoryPathRef.current = selectedDirectoryPath;
    validationCacheRef.current.clear();
    setSaveState("idle");
    if (!hasUnsavedWorkflowChanges && !workflowGeneratorOpen) return;

    workflowGeneratorAbortRef.current?.abort();
    workflowGeneratorRequestIdRef.current += 1;
    setWorkflowGeneratorOpen(false);
    setWorkflowGeneratorDescription("");
    setWorkflowGeneratorState("idle");
    setWorkflowGeneratorError(null);
    setWorkflowGeneratorWarnings([]);
    setWorkflowGeneratorModel(null);
    setWorkflowGeneratorReadyDescription(null);
    setRunDetail(null);
    setNodeConnectionMenu(null);
    setPendingConnection(null);
    setSelectedEdgeId(null);
    setSelectedGraphId(null);
    setNodeEditOrigins({});
    setUndoHistory([]);
    setInspectorDraftError(null);

    if (savedDocument) {
      applyWorkflowDocumentState(
        savedDocument,
        [],
        savedDocument.validation_diagnostics ?? [],
        { autoFit: true },
      );
    }
  }, [hasUnsavedWorkflowChanges, savedDocument, selectedDirectoryPath, workflowGeneratorOpen]);

  function patchDocument(
    nextConfig: WorkflowConfig,
    nextLayout: WorkflowLayout,
    nextGraphs?: WorkflowGraphMetadata[],
    selectedNodeIdsAfterPatch?: string[],
    diagnosticsOverride?: WorkflowValidationDiagnostic[],
    outputSchemasOverride?: WorkflowOutputSchemas,
    revisionOverride?: string,
    options: { recordUndo?: boolean } = {},
  ) {
    if (!document) return;
    const nextDocument = normalizeWorkflowEditorDocument(
      {
        ...document,
        config: nextConfig,
        layout: nextLayout,
        graphs: nextGraphs ?? document.graphs ?? [],
        output_schemas: outputSchemasOverride ?? document.output_schemas ?? {},
        revision: revisionOverride ?? document.revision,
      },
      selectedDirectoryPath,
    );
    if (options.recordUndo !== false && !workflowUndoDocumentsEqual(document, nextDocument)) {
      rememberWorkflowUndoSnapshot(document);
    }
    applyWorkflowDocumentState(
      nextDocument,
      selectedNodeIdsAfterPatch ?? flowNodes.filter((node) => node.selected).map((node) => node.id),
      diagnosticsOverride,
    );
  }

  function patchWorkflowDocument(
    nextConfig: WorkflowConfig,
    nextLayout: WorkflowLayout,
    nextGraphs?: WorkflowGraphMetadata[],
    selectedNodeIdsAfterPatch?: string[],
    diagnosticsOverride?: WorkflowValidationDiagnostic[],
    outputSchemasOverride?: WorkflowOutputSchemas,
    revisionOverride?: string,
  ) {
    if (isTutorialMode && activeWorkflowTutorial) {
      const baseDocument = activeWorkflowTutorial.document;
      const nextDocument = normalizeWorkflowEditorDocument(
        {
          ...baseDocument,
          config: nextConfig,
          layout: nextLayout,
          graphs: nextGraphs ?? baseDocument.graphs ?? [],
          output_schemas: outputSchemasOverride ?? baseDocument.output_schemas ?? {},
          validation_diagnostics: diagnosticsOverride ?? baseDocument.validation_diagnostics ?? [],
          revision: revisionOverride ?? baseDocument.revision,
        },
        selectedDirectoryPath,
      );
      const nextDiagnostics = diagnosticsOverride ?? validateWorkflowClientDiagnostics(
        nextDocument.config,
        nextDocument.output_schemas,
      );
      const nextTutorialDocument = {
        ...nextDocument,
        validation_diagnostics: nextDiagnostics,
      };
      setActiveWorkflowTutorial((current) => (
        current ? {
          ...current,
          document: nextTutorialDocument,
        } : current
      ));
      if (selectedNodeIdsAfterPatch) {
        setProgrammaticSelection(selectedNodeIdsAfterPatch, selectedNodeIdsAfterPatch[0] ?? null);
      }
      setSaveState("idle");
      return;
    }
    patchDocument(
      nextConfig,
      nextLayout,
      nextGraphs,
      selectedNodeIdsAfterPatch,
      diagnosticsOverride,
      outputSchemasOverride,
      revisionOverride,
    );
  }

  function setProgrammaticSelection(nextNodeIds: string[], preferredNodeId: string | null = null) {
    const uniqueNodeIds = Array.from(new Set(nextNodeIds));
    setSelectedNodeIds(uniqueNodeIds);
    setSelectedNodeId((currentSelectedNodeId) => {
      if (uniqueNodeIds.length === 0) return null;
      if (preferredNodeId && uniqueNodeIds.includes(preferredNodeId)) return preferredNodeId;
      if (currentSelectedNodeId && uniqueNodeIds.includes(currentSelectedNodeId)) return currentSelectedNodeId;
      return uniqueNodeIds[uniqueNodeIds.length - 1] ?? null;
    });
    setFlowNodes((currentNodes) =>
      currentNodes.map((node) => {
        const isSelected = uniqueNodeIds.includes(node.id);
        return node.selected === isSelected ? node : { ...node, selected: isSelected };
      }),
    );
  }

  function updateNodePositions(changes: NodeChange[]) {
    setFlowNodes((currentNodes) => applyNodeChanges(changes, currentNodes) as Node<GraphNodeData>[]);
  }

  function snapVisibleDraggedNodes(
    activeNode: Node<GraphNodeData>,
    draggedNodes: Node<GraphNodeData>[],
    currentNodes: Node<GraphNodeData>[],
  ) {
    const nodesToSnap = draggedNodes.some((node) => node.id === activeNode.id)
      ? draggedNodes
      : [activeNode, ...draggedNodes];
    const draggedById = new Map(nodesToSnap.map((node) => [node.id, node]));
    const visibleCurrentNodes = currentNodes
      .filter((node) => visibleGraphNodeIds.has(node.id))
      .map((node) => draggedById.get(node.id) ?? node);
    return snapDraggedNodes(activeNode, nodesToSnap, visibleCurrentNodes);
  }

  function updateDraggedNodePositions(activeNode: Node<GraphNodeData>, draggedNodes: Node<GraphNodeData>[]) {
    setFlowNodes((currentNodes) => {
      const { snappedNodes, guides } = snapVisibleDraggedNodes(activeNode, draggedNodes, currentNodes);
      setSnapGuides(guides);
      const snappedById = new Map(snappedNodes.map((node) => [node.id, node]));
      return currentNodes.map((node) => snappedById.get(node.id) ?? node);
    });
  }

  function updateEdgeChanges(changes: Parameters<typeof applyEdgeChanges>[0]) {
    setFlowEdges((currentEdges) => applyEdgeChanges(changes, currentEdges));
  }

  function handleFlowInit(instance: ReactFlowInstance<Node<GraphNodeData>, Edge>) {
    setFlowInstance(instance);
    setCanvasViewport(instance.getViewport());
  }

  function fitWorkflowCanvas() {
    if (!flowInstance) return;
    void flowInstance.fitView({ padding: 0.18, duration: 180 });
  }

  function selectCompactCanvasMode(nextMode: CompactCanvasMode) {
    setCompactCanvasMode(nextMode);
    compactCanvasTouchModeRef.current = "idle";
    compactPanePanRef.current = null;
    if (nextMode !== "connect") {
      closeConnectionUi();
    }
  }

  function toggleCompactPanel(panel: Exclude<CompactActivePanel, null>) {
    setCompactActivePanel((current) => current === panel ? null : panel);
  }

  function flowPositionFromScreenPoint(clientX: number, clientY: number): { x: number; y: number } | null {
    if (flowInstance) {
      return flowInstance.screenToFlowPosition({ x: clientX, y: clientY });
    }
    const canvasRect = canvasShellRef.current?.getBoundingClientRect();
    if (!canvasRect) return null;
    const zoom = canvasViewport.zoom || 1;
    return {
      x: (clientX - canvasRect.left - canvasViewport.x) / zoom,
      y: (clientY - canvasRect.top - canvasViewport.y) / zoom,
    };
  }

  function canvasPointFromScreenPoint(clientX: number, clientY: number): CanvasPoint | null {
    const canvasRect = canvasShellRef.current?.getBoundingClientRect();
    if (!canvasRect) return null;
    return {
      x: clientX - canvasRect.left,
      y: clientY - canvasRect.top,
    };
  }

  function screenPointIsOverCanvas(point: ScreenPoint): boolean {
    const canvasRect = canvasShellRef.current?.getBoundingClientRect();
    if (!canvasRect) return false;
    return (
      point.clientX >= canvasRect.left &&
      point.clientX <= canvasRect.right &&
      point.clientY >= canvasRect.top &&
      point.clientY <= canvasRect.bottom
    );
  }

  function updateCanvasCursorPoint(clientX: number, clientY: number) {
    canvasCursorPointRef.current = { clientX, clientY };
  }

  function flowPositionAtCanvasCursor(): { x: number; y: number } | null {
    const cursorPoint = canvasCursorPointRef.current;
    if (!cursorPoint || !screenPointIsOverCanvas(cursorPoint)) return null;
    return flowPositionFromScreenPoint(cursorPoint.clientX, cursorPoint.clientY);
  }

  function nodePositionCenteredOnFlowPoint(flowPosition: { x: number; y: number }): { x: number; y: number } {
    return {
      x: flowPosition.x - FALLBACK_NODE_WIDTH / 2,
      y: flowPosition.y - FALLBACK_NODE_HEIGHT / 2,
    };
  }

  function nodePositionFromScreenPoint(clientX: number, clientY: number): { x: number; y: number } | null {
    const flowPosition = flowPositionFromScreenPoint(clientX, clientY);
    return flowPosition ? nodePositionCenteredOnFlowPoint(flowPosition) : null;
  }

  function flowPositionAtCanvasCenter(): { x: number; y: number } | null {
    const canvasRect = canvasShellRef.current?.getBoundingClientRect();
    if (!canvasRect) return null;
    return flowPositionFromScreenPoint(
      canvasRect.left + canvasRect.width / 2,
      canvasRect.top + canvasRect.height / 2,
    );
  }

  function nodePositionAtCanvasCenter(): { x: number; y: number } | null {
    const flowPosition = flowPositionAtCanvasCenter();
    return flowPosition ? nodePositionCenteredOnFlowPoint(flowPosition) : null;
  }

  function nodeCenterCanvasPoint(node: Node<GraphNodeData>): CanvasPoint {
    const bounds = nodeBounds(node);
    return {
      x: (bounds.centerX * canvasViewport.zoom) + canvasViewport.x,
      y: (bounds.centerY * canvasViewport.zoom) + canvasViewport.y,
    };
  }

  function closeConnectionUi() {
    setNodeConnectionMenu(null);
    setPendingConnection(null);
  }

  function setPendingConnectionFromRoute(
    sourceId: string,
    route: WorkflowConnectionRouteOption,
    sourcePoint: CanvasPoint,
    cursorPoint: CanvasPoint = sourcePoint,
  ) {
    setPendingConnection({
      sourceId,
      sourcePort: route.sourcePort,
      routeLabel: route.label,
      routeClass: route.routeClass,
      sourcePoint,
      cursorPoint,
    });
    setNodeConnectionMenu(null);
    setSelectedEdgeId(null);
    setWorkflowTutorialHint(null);
    setError(null);
    appShellRef.current?.focus();
  }

  function startPendingConnection(route: WorkflowConnectionRouteOption) {
    const expectedAction = tutorialCurrentRequiredAction();
    const canStartConnection = Boolean(
      isTutorialMode &&
      expectedAction?.kind === "connect" &&
      nodeConnectionMenu &&
      expectedAction.sourceNodeId === nodeConnectionMenu.nodeId &&
      expectedAction.sourcePort === route.sourcePort,
    );
    if (isTutorialMode && expectedAction?.kind === "connect" && !canStartConnection) {
      setWorkflowTutorialHint(tutorialActionHintFor(expectedAction));
      return;
    }
    if (workflowEditLocked && !canStartConnection) {
      return;
    }
    if (!nodeConnectionMenu) return;
    setPendingConnectionFromRoute(
      nodeConnectionMenu.nodeId,
      route,
      nodeConnectionMenu.sourcePoint,
      nodeConnectionMenu.position,
    );
  }

  function startCompactConnectionFromNode(node: Node<GraphNodeData>) {
    if (workflowEditLocked) return;
    const sourcePoint = nodeCenterCanvasPoint(node);
    const routes = node.data.type === "gate:switch" && node.data.outputPorts.length === 0
      ? []
      : workflowConnectionRouteOptions(node.data.outputPorts);
    setProgrammaticSelection([node.id], node.id);
    setSelectedEdgeId(null);
    setSnapGuides([]);
    if (routes.length === 1 && routes[0]) {
      setPendingConnectionFromRoute(node.id, routes[0], sourcePoint);
      return;
    }
    setNodeConnectionMenu({
      nodeId: node.id,
      position: sourcePoint,
      sourcePoint,
      routes,
      failedCount: node.data.statusBadges.find((badge) => badge.status === "failed")?.count ?? 0,
    });
    setPendingConnection(null);
    setError(null);
    appShellRef.current?.focus();
  }

  function updatePendingConnectionCursor(clientX: number, clientY: number) {
    const cursorPoint = canvasPointFromScreenPoint(clientX, clientY);
    if (!cursorPoint) return;
    setPendingConnection((current) => current ? { ...current, cursorPoint } : current);
  }

  function addWorkflowEdge(sourceId: string, targetId: string, sourcePort?: string | null): boolean {
    const workflowDocument = activeWorkflowDocument;
    if (!workflowDocument) return false;
    const normalizedSourcePort = normalizeWorkflowSourcePort(sourcePort);
    const validationError = validateConnection(
      workflowDocument.config,
      activeWorkflowCatalog,
      sourceId,
      targetId,
      normalizedSourcePort,
    );
    if (validationError) {
      setError(validationError);
      return false;
    }
    const targetNode = nodeById(workflowDocument.config, targetId);
    const allowCycle = (
      targetNode?.type === "gate:for" || targetNode?.type === "gate:while"
    ) && wouldCreateCycle(workflowDocument.config, sourceId, targetId);
    const nextConfig = cloneConfig(workflowDocument.config);
    const nextEdge: WorkflowEdge = {
      source_id: sourceId,
      target_id: targetId,
    };
    if (normalizedSourcePort) {
      nextEdge.source_port = normalizedSourcePort;
    }
    if (allowCycle) {
      nextEdge.allow_cycle = true;
    }
    nextConfig.edges = [
      ...nextConfig.edges,
      nextEdge,
    ];
    patchWorkflowDocument(nextConfig, cloneLayout(workflowDocument.layout));
    setSelectedEdgeId(null);
    setError(null);
    return true;
  }

  function startNodeTypeDrag(
    event: ReactDragEvent<HTMLButtonElement>,
    entry: WorkflowEditorNodeCatalogEntry,
  ) {
    const expectedAction = tutorialCurrentRequiredAction();
    const canDrag = Boolean(
      isTutorialMode &&
      expectedAction?.kind === "add-node" &&
      expectedAction.nodeKind === entry.kind &&
      expectedAction.nodeType === entry.type,
    );
    if (isTutorialMode && expectedAction?.kind === "add-node" && !canDrag) {
      event.preventDefault();
      setWorkflowTutorialHint(tutorialActionHintFor(expectedAction));
      return;
    }
    if (workflowEditLocked && !canDrag) {
      event.preventDefault();
      return;
    }
    event.dataTransfer.effectAllowed = "copy";
    event.dataTransfer.setData(
      NODE_TYPE_DRAG_DATA_MIME,
      JSON.stringify({ kind: entry.kind, type: entry.type } satisfies NodeTypeDragPayload),
    );
    event.dataTransfer.setData("text/plain", entry.type);
  }

  function handleNodeTypeDragOver(event: ReactDragEvent<HTMLElement>) {
    const expectedAction = tutorialCurrentRequiredAction();
    if (
      workflowEditLocked &&
      !(
        isTutorialMode &&
        expectedAction?.kind === "add-node"
      )
    ) return;
    if (!Array.from(event.dataTransfer.types).includes(NODE_TYPE_DRAG_DATA_MIME)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
  }

  function handleNodeTypeDrop(event: ReactDragEvent<HTMLElement>) {
    const expectedAction = tutorialCurrentRequiredAction();
    if (
      workflowEditLocked &&
      !(
        isTutorialMode &&
        expectedAction?.kind === "add-node"
      )
    ) return;
    const payload = parseNodeTypeDragPayload(event.dataTransfer);
    if (!payload) return;
    if (
      isTutorialMode &&
      expectedAction?.kind === "add-node" &&
      (expectedAction.nodeKind !== payload.kind || expectedAction.nodeType !== payload.type)
    ) {
      setWorkflowTutorialHint(tutorialActionHintFor(expectedAction));
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    addNode(payload.kind, payload.type, nodePositionFromScreenPoint(event.clientX, event.clientY));
  }

  function handleNodeContextMenu(event: React.MouseEvent, node: Node<GraphNodeData>) {
    event.preventDefault();
    event.stopPropagation();
    const expectedAction = tutorialCurrentRequiredAction();
    if (
      workflowEditLocked &&
      !(
        isTutorialMode &&
        expectedAction?.kind === "connect" &&
        expectedAction.sourceNodeId === node.id
      )
    ) return;
    if (isTutorialMode && expectedAction?.kind === "connect" && expectedAction.sourceNodeId !== node.id) {
      setWorkflowTutorialHint(tutorialActionHintFor(expectedAction));
      return;
    }
    const position = canvasPointFromScreenPoint(event.clientX, event.clientY);
    if (!position) return;
    setProgrammaticSelection([node.id], node.id);
    setSelectedEdgeId(null);
    setNodeConnectionMenu({
      nodeId: node.id,
      position,
      sourcePoint: nodeCenterCanvasPoint(node),
      routes: node.data.type === "gate:switch" && node.data.outputPorts.length === 0
        ? []
        : workflowConnectionRouteOptions(node.data.outputPorts),
      failedCount: node.data.statusBadges.find((badge) => badge.status === "failed")?.count ?? 0,
    });
    setPendingConnection(null);
    setError(null);
    setWorkflowTutorialHint(null);
    appShellRef.current?.focus();
  }

  function handleWorkflowNodeClick(event: React.MouseEvent, node: Node<GraphNodeData>) {
    if (isInspectMode) {
      event.stopPropagation();
      setNodeConnectionMenu(null);
      setPendingConnection(null);
      setSelectedNodeIds([node.id]);
      setSelectedNodeId(node.id);
      setSelectedEdgeId(null);
      setSnapGuides([]);
      appShellRef.current?.focus();
      return;
    }
    if (isTutorialMode) {
      const expectedAction = tutorialCurrentRequiredAction();
      if (pendingConnection) {
        const connectAction: WorkflowTutorialRequiredAction = {
          kind: "connect",
          sourceNodeId: pendingConnection.sourceId,
          sourcePort: pendingConnection.sourcePort,
          targetNodeId: node.id,
        };
        if (tutorialAllowsAction(connectAction)) {
          event.stopPropagation();
          if (addWorkflowEdge(pendingConnection.sourceId, node.id, pendingConnection.sourcePort)) {
            setPendingConnection(null);
            setProgrammaticSelection([node.id], node.id);
            setSelectedEdgeId(null);
            setSnapGuides([]);
            completeTutorialAction(connectAction);
          }
          appShellRef.current?.focus();
          return;
        }
        setWorkflowTutorialHint(tutorialActionHintFor(expectedAction));
        event.stopPropagation();
        appShellRef.current?.focus();
        return;
      }
      if (expectedAction?.kind === "select-node" && expectedAction.nodeId === node.id) {
        event.stopPropagation();
        setNodeConnectionMenu(null);
        setPendingConnection(null);
        setProgrammaticSelection([node.id], node.id);
        setSelectedEdgeId(null);
        setSnapGuides([]);
        setWorkflowTutorialHint(null);
        completeTutorialAction({ kind: "select-node", nodeId: node.id });
        appShellRef.current?.focus();
        return;
      }
      if (expectedAction?.kind === "connect" && expectedAction.sourceNodeId === node.id) {
        event.stopPropagation();
        setProgrammaticSelection([node.id], node.id);
        setSelectedEdgeId(null);
        setWorkflowTutorialHint(null);
        appShellRef.current?.focus();
        return;
      }
      event.stopPropagation();
      setWorkflowTutorialHint(tutorialActionHintFor(expectedAction));
      appShellRef.current?.focus();
      return;
    }
    if (pendingConnection) {
      event.stopPropagation();
      if (addWorkflowEdge(pendingConnection.sourceId, node.id, pendingConnection.sourcePort)) {
        setPendingConnection(null);
      }
      appShellRef.current?.focus();
      return;
    }
    if (isCompactCanvasTouchMode && compactCanvasMode === "connect") {
      event.stopPropagation();
      startCompactConnectionFromNode(node);
      return;
    }
    setNodeConnectionMenu(null);
    const additiveSelection = event.ctrlKey || event.metaKey;
    setProgrammaticSelection(additiveSelection ? [...selectedNodeIds, node.id] : [node.id], node.id);
    setSelectedEdgeId(null);
    appShellRef.current?.focus();
  }

  function handleCanvasPointerEnter(event: ReactPointerEvent<HTMLElement>) {
    updateCanvasCursorPoint(event.clientX, event.clientY);
  }

  function handleCanvasPointerMove(event: ReactPointerEvent<HTMLElement>) {
    updateCanvasCursorPoint(event.clientX, event.clientY);
    if (!pendingConnection) return;
    updatePendingConnectionCursor(event.clientX, event.clientY);
  }

  function handleCanvasPointerLeave() {
    canvasCursorPointRef.current = null;
  }

  function compactPanePanTouch(event: ReactTouchEvent<HTMLElement>): ReactTouch | null {
    const panState = compactPanePanRef.current;
    if (!panState) return event.touches[0] ?? null;
    return Array.from(event.touches).find((touch) => touch.identifier === panState.touchId) ?? null;
  }

  function updateCompactPanePan(event: ReactTouchEvent<HTMLElement>) {
    const panState = compactPanePanRef.current;
    const touch = compactPanePanTouch(event);
    if (!panState || !touch || !flowInstance) return;
    const nextViewport = {
      ...panState.viewport,
      x: panState.viewport.x + (touch.clientX - panState.clientX),
      y: panState.viewport.y + (touch.clientY - panState.clientY),
    };
    flowInstance.setViewport(nextViewport);
    setCanvasViewport(nextViewport);
  }

  function handleCanvasTouchStartCapture(event: ReactTouchEvent<HTMLElement>) {
    if (!isCompactCanvasTouchMode) return;
    if (event.touches.length >= 2) {
      compactPanePanRef.current = null;
      compactCanvasTouchModeRef.current = "multi-touch-pan";
      return;
    }
    if (!workflowEditLocked && eventTargetInsideWorkflowNodeDragSurface(event.target)) {
      compactCanvasTouchModeRef.current = "node-drag";
      return;
    }
    if (compactCanvasMode === "pan") {
      compactCanvasTouchModeRef.current = "pane-pan";
      const touch = event.touches[0];
      compactPanePanRef.current = touch
        ? {
            touchId: touch.identifier,
            clientX: touch.clientX,
            clientY: touch.clientY,
            viewport: flowInstance?.getViewport() ?? canvasViewport,
          }
        : null;
      event.stopPropagation();
      return;
    }
    compactPanePanRef.current = null;
    compactCanvasTouchModeRef.current = "pane-scroll";
    event.stopPropagation();
  }

  function handleCanvasTouchMoveCapture(event: ReactTouchEvent<HTMLElement>) {
    if (!isCompactCanvasTouchMode) return;
    if (event.touches.length >= 2) {
      compactPanePanRef.current = null;
      compactCanvasTouchModeRef.current = "multi-touch-pan";
      return;
    }
    if (compactCanvasTouchModeRef.current === "node-drag") return;
    if (compactCanvasTouchModeRef.current === "pane-pan") {
      updateCompactPanePan(event);
      event.stopPropagation();
      event.nativeEvent.stopImmediatePropagation();
      return;
    }
    if (compactCanvasTouchModeRef.current === "multi-touch-pan") {
      event.stopPropagation();
      event.nativeEvent.stopImmediatePropagation();
      return;
    }
    compactCanvasTouchModeRef.current = "pane-scroll";
    event.stopPropagation();
    event.nativeEvent.stopImmediatePropagation();
  }

  function handleCanvasTouchEndCapture(event: ReactTouchEvent<HTMLElement>) {
    if (!isCompactCanvasTouchMode) return;
    if (event.touches.length === 0) {
      compactPanePanRef.current = null;
      compactCanvasTouchModeRef.current = "idle";
      return;
    }
    if (compactCanvasTouchModeRef.current === "node-drag") return;
    if (compactCanvasTouchModeRef.current === "multi-touch-pan") {
      event.stopPropagation();
      event.nativeEvent.stopImmediatePropagation();
      return;
    }
    event.stopPropagation();
  }

  function handleCanvasTouchCancelCapture(event: ReactTouchEvent<HTMLElement>) {
    if (!isCompactCanvasTouchMode) return;
    if (event.touches.length === 0) {
      compactPanePanRef.current = null;
      compactCanvasTouchModeRef.current = "idle";
      return;
    }
    if (compactCanvasTouchModeRef.current === "node-drag") return;
    if (compactCanvasTouchModeRef.current === "multi-touch-pan") {
      event.stopPropagation();
      event.nativeEvent.stopImmediatePropagation();
      return;
    }
    event.stopPropagation();
  }

  function handlePaneClick() {
    if (isTutorialMode) {
      if (pendingConnection || nodeConnectionMenu) {
        closeConnectionUi();
      }
      return;
    }
    if (pendingConnection || nodeConnectionMenu) {
      closeConnectionUi();
    }
    if (isInspectMode) {
      setSelectedNodeIds([]);
      setSelectedNodeId(null);
    }
    setSelectedEdgeId(null);
    setSnapGuides([]);
  }

  function handlePaneContextMenu(event: MouseEvent | React.MouseEvent) {
    if (!pendingConnection && !nodeConnectionMenu) return;
    event.preventDefault();
    closeConnectionUi();
  }

  function deleteSelectedEdge() {
    if (!document || !selectedEdgeId || workflowEditLocked) return;
    const nextConfig = cloneConfig(document.config);
    nextConfig.edges = nextConfig.edges.filter(
      (edge) => edgeId(edge) !== selectedEdgeId,
    );
    patchDocument(nextConfig, cloneLayout(document.layout));
    setSelectedEdgeId(null);
    setError(null);
  }

  function deleteNodes(nodeIds: string[]) {
    if (!document || nodeIds.length === 0 || workflowEditLocked) return;
    const deletedNodeIds = new Set(nodeIds);
    const nextConfig = cloneConfig(document.config);
    nextConfig.triggers = (nextConfig.triggers ?? []).filter((node) => !deletedNodeIds.has(node.id));
    nextConfig.gates = (nextConfig.gates ?? []).filter((node) => !deletedNodeIds.has(node.id));
    nextConfig.effects = (nextConfig.effects ?? []).filter((node) => !deletedNodeIds.has(node.id));
    nextConfig.queries = (nextConfig.queries ?? []).filter((node) => !deletedNodeIds.has(node.id));
    nextConfig.edges = nextConfig.edges.filter(
      (edge) => !deletedNodeIds.has(edge.source_id) && !deletedNodeIds.has(edge.target_id),
    );
    const nextLayout = cloneLayout(document.layout);
    for (const nodeId of deletedNodeIds) {
      delete nextLayout.nodes[nodeId];
    }
    patchDocument(nextConfig, nextLayout);
    setProgrammaticSelection([]);
    setSelectedEdgeId(null);
    setNodeEditOrigins((current) => {
      const next = { ...current };
      for (const nodeId of deletedNodeIds) {
        delete next[nodeId];
      }
      return next;
    });
    setInspectorDraftError(null);
    setError(null);
  }

  function copySelectedNodes() {
    if (!document || selectedNodeIds.length === 0) return false;
    const selectedNodeIdSet = new Set(selectedNodeIds);
    const nodesById = new Map(workflowNodes(document.config).map((node) => [node.id, node]));
    const currentFlowNodes = flowInstance?.getNodes() as Node<GraphNodeData>[] | undefined;
    const flowNodesById = new Map(
      (currentFlowNodes && currentFlowNodes.length > 0 ? currentFlowNodes : flowNodes).map((node) => [node.id, node]),
    );
    const copiedNodes = selectedNodeIds
      .map((nodeId, index) => {
        const node = nodesById.get(nodeId);
        if (!node) return null;
        const flowNode = flowNodesById.get(nodeId);
        const position = flowNode?.position ?? document.layout.nodes[nodeId] ?? fallbackPosition(index);
        const bounds = nodeBounds(flowNode ? { ...flowNode, position } : { id: node.id, position });
        return {
          node: cloneNodeConfig(node),
          position,
          width: bounds.right - bounds.left,
          height: bounds.bottom - bounds.top,
        };
      })
      .filter((node): node is CopiedWorkflowNode => Boolean(node));
    if (copiedNodes.length === 0) return false;
    copiedSelectionRef.current = {
      nodes: copiedNodes,
      edges: document.config.edges
        .filter((edge) => selectedNodeIdSet.has(edge.source_id) && selectedNodeIdSet.has(edge.target_id))
        .map((edge) => ({ ...edge })),
      primaryNodeId:
        selectedNodeId && selectedNodeIdSet.has(selectedNodeId)
          ? selectedNodeId
          : copiedNodes[0]?.node.id ?? null,
    };
    setError(null);
    return true;
  }

  function pasteCopiedSelection() {
    const copiedSelection = copiedSelectionRef.current;
    if (!document || !copiedSelection || copiedSelection.nodes.length === 0 || workflowEditLocked) return false;
    const nextConfig = cloneConfig(document.config);
    nextConfig.triggers ??= [];
    nextConfig.gates ??= [];
    nextConfig.effects ??= [];
    nextConfig.queries ??= [];
    const idMap = new Map<string, string>();
    const duplicatedNodes: Array<{ kind: WorkflowNodeKind; node: WorkflowNodeConfig }> = [];
    for (const copiedNode of copiedSelection.nodes) {
      const templateNode = copiedNode.node;
      const templateKind = nodeKindFromType(templateNode.type);
      const nextId = generateNodeId(
        nextConfig,
        templateKind,
        templateNode.type,
        templateNode.display_name,
        selectedWorkflowPackReference,
      );
      const duplicatedNode = cloneNodeConfig(templateNode);
      duplicatedNode.id = nextId;
      duplicatedNode.display_name = `${templateNode.display_name} Copy`;
      nextConfig[kindCollectionKey(templateKind)].push(duplicatedNode);
      idMap.set(templateNode.id, nextId);
      duplicatedNodes.push({ kind: templateKind, node: duplicatedNode });
    }
    for (const duplicatedNode of duplicatedNodes) {
      const collection = nextConfig[kindCollectionKey(duplicatedNode.kind)];
      const nodeIndex = collection.findIndex((node) => node.id === duplicatedNode.node.id);
      if (nodeIndex === -1) continue;
      collection[nodeIndex] = rewriteWorkflowNodeConfigBindings(duplicatedNode.node, idMap);
    }
    const duplicatedEdges = copiedSelection.edges
      .map((edge) => {
        const sourceId = idMap.get(edge.source_id);
        const targetId = idMap.get(edge.target_id);
        if (!sourceId || !targetId) return null;
        return {
          ...edge,
          source_id: sourceId,
          target_id: targetId,
        };
      })
      .filter((edge): edge is WorkflowEdge => Boolean(edge));
    nextConfig.edges = [...nextConfig.edges, ...duplicatedEdges];
    const nextLayout = cloneLayout(document.layout);
    const selectionCenter = copiedWorkflowSelectionCenter(copiedSelection.nodes);
    const pasteTarget = flowPositionAtCanvasCursor() ?? flowPositionAtCanvasCenter();
    const pasteDelta = selectionCenter && pasteTarget
      ? {
          x: pasteTarget.x - selectionCenter.x,
          y: pasteTarget.y - selectionCenter.y,
        }
      : { x: 40, y: 40 };
    for (const copiedNode of copiedSelection.nodes) {
      const nextId = idMap.get(copiedNode.node.id);
      if (!nextId) continue;
      nextLayout.nodes[nextId] = {
        x: copiedNode.position.x + pasteDelta.x,
        y: copiedNode.position.y + pasteDelta.y,
      };
    }
    const pastedNodeIds = copiedSelection.nodes
      .map((copiedNode) => idMap.get(copiedNode.node.id))
      .filter((nodeId): nodeId is string => Boolean(nodeId));
    const preferredPastedNodeId =
      copiedSelection.primaryNodeId ? idMap.get(copiedSelection.primaryNodeId) ?? null : null;
    patchDocument(nextConfig, nextLayout, undefined, pastedNodeIds);
    setProgrammaticSelection(pastedNodeIds, preferredPastedNodeId);
    setSelectedEdgeId(null);
    setError(null);
    return true;
  }

  function handleAppShellKeyDown(event: React.KeyboardEvent<HTMLDivElement>) {
    if (isEditableEventTarget(event.target)) return;
    if (isTutorialMode) {
      if (event.key === "Escape") {
        event.preventDefault();
        exitWorkflowTutorial();
      }
      return;
    }
    if (isInspectMode) {
      if (event.key === "Escape") {
        event.preventDefault();
        exitInspectMode();
      }
      return;
    }
    if (event.key === "Escape" && (pendingConnection || nodeConnectionMenu)) {
      event.preventDefault();
      closeConnectionUi();
      return;
    }
    if (event.key === "Escape" && compactActivePanel) {
      event.preventDefault();
      setCompactActivePanel(null);
      return;
    }
    const isCopy = (event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "c";
    const isPaste = (event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "v";
    const isUndo = (event.ctrlKey || event.metaKey) && !event.shiftKey && event.key.toLowerCase() === "z";
    if (isUndo) {
      if (canUndoWorkflowChanges) {
        event.preventDefault();
        undoWorkflowChange();
      }
      return;
    }
    if (isCopy) {
      if (copySelectedNodes()) event.preventDefault();
      return;
    }
    if (isPaste) {
      if (pasteCopiedSelection()) event.preventDefault();
      return;
    }
    if (!selectedEdgeId) return;
    if (event.key !== "Delete" && event.key !== "Backspace") return;
    event.preventDefault();
    deleteSelectedEdge();
  }

  function persistNodePositions(activeNode: Node<GraphNodeData>, draggedNodes: Node<GraphNodeData>[]) {
    if (!document || workflowEditLocked) return;
    setSnapGuides([]);
    const currentNodes = flowInstance?.getNodes() as Node<GraphNodeData>[] | undefined;
    const { snappedNodes } = snapVisibleDraggedNodes(
      activeNode,
      draggedNodes,
      currentNodes && currentNodes.length > 0 ? currentNodes : flowNodes,
    );
    const nextLayout = cloneLayout(document.layout);
    for (const node of snappedNodes) {
      nextLayout.nodes[node.id] = node.position;
    }
    const snappedById = new Map(snappedNodes.map((node) => [node.id, node]));
    setFlowNodes((nodes) => nodes.map((node) => snappedById.get(node.id) ?? node));
    const nextDocument = {
      ...document,
      layout: nextLayout,
    };
    if (!workflowUndoDocumentsEqual(document, nextDocument)) {
      rememberWorkflowUndoSnapshot(document);
    }
    setDocument(nextDocument);
    setSaveState("idle");
  }

  function addNode(kind: WorkflowNodeKind, type: string, requestedPosition?: { x: number; y: number } | null) {
    const workflowDocument = activeWorkflowDocument;
    if (!workflowDocument) return;
    const expectedAction = tutorialCurrentRequiredAction();
    const isTutorialAddNode =
      isTutorialMode &&
      expectedAction?.kind === "add-node" &&
      expectedAction.nodeKind === kind &&
      expectedAction.nodeType === type;
    const tutorialAddNodeAction = isTutorialAddNode ? expectedAction : null;
    if (workflowEditLocked && !isTutorialAddNode) return;
    if (isTutorialMode && expectedAction?.kind === "add-node" && !isTutorialAddNode) {
      setWorkflowTutorialHint(tutorialActionHintFor(expectedAction));
      return;
    }
    const nextConfig = cloneConfig(workflowDocument.config);
    nextConfig.triggers ??= [];
    nextConfig.gates ??= [];
    nextConfig.effects ??= [];
    nextConfig.queries ??= [];
    const catalogEntry = getCatalogEntry(activeWorkflowCatalog, type);
    const nodeId = tutorialAddNodeAction
      ? tutorialAddNodeAction.nodeId
      : generateNodeId(nextConfig, kind, type, catalogEntry?.label, selectedWorkflowPackReference);
    const node = createDefaultNode(kind, type, nodeId);
    nextConfig[kindCollectionKey(kind)].push(node);
    const nextLayout = cloneLayout(workflowDocument.layout);
    const nextNodeIndex =
      nextConfig.triggers.length +
        nextConfig.gates.length +
        nextConfig.effects.length +
        nextConfig.queries.length -
        1;
    nextLayout.nodes[node.id] =
      (tutorialAddNodeAction ? tutorialAddNodeAction.position : requestedPosition) ?? nodePositionAtCanvasCenter() ?? fallbackPosition(nextNodeIndex);
    patchWorkflowDocument(nextConfig, nextLayout, undefined, [node.id]);
    setProgrammaticSelection([node.id], node.id);
    setSelectedEdgeId(null);
    setInspectorDraftError(null);
    if (tutorialAddNodeAction) {
      completeTutorialAction({
        kind: "add-node",
        nodeKind: tutorialAddNodeAction.nodeKind,
        nodeType: tutorialAddNodeAction.nodeType,
        nodeId: tutorialAddNodeAction.nodeId,
        position: tutorialAddNodeAction.position,
      });
    }
    setError(null);
  }

  function deleteSelectedNode() {
    deleteNodes(selectedNodeIds);
  }

  function selectDownstreamBranch() {
    if (!document || !selectedNodeId || workflowEditLocked) return;
    const queuedNodeIds = [selectedNodeId];
    const selectedNodeSet = new Set<string>();
    while (queuedNodeIds.length > 0) {
      const currentNodeId = queuedNodeIds.shift();
      if (!currentNodeId || selectedNodeSet.has(currentNodeId)) continue;
      selectedNodeSet.add(currentNodeId);
      for (const edge of document.config.edges) {
        if (edge.source_id === currentNodeId && !selectedNodeSet.has(edge.target_id)) {
          queuedNodeIds.push(edge.target_id);
        }
      }
    }
    setProgrammaticSelection([...selectedNodeSet], selectedNodeId);
    setSelectedEdgeId(null);
  }

  function selectWorkflowGraph(graph: WorkflowGraphMetadata) {
    if (workflowEditLocked) return;
    setSelectedGraphId(graph.id);
    setProgrammaticSelection(graph.node_ids, graph.node_ids[0] ?? null);
    setSelectedEdgeId(null);
    setError(null);
  }

  function applyWorkflowGraphChanges() {
    if (!document || !selectedGraph || workflowEditLocked) return;
    const nextName = graphNameDraft.trim();
    if (!nextName) {
      setError("Graph name is required.");
      return;
    }
    const nextDirectoryPath = graphDirectoryDraft.trim();
    if (!nextDirectoryPath) {
      setError("Graph directory is required.");
      return;
    }
    const nextUserIds = Array.from(
      new Set(
        graphUsersDraft
          .split("\n")
          .map((userId) => userId.trim())
          .filter(Boolean),
      ),
    );
    const nextGraphs = document.graphs.map((graph) =>
        graph.id === selectedGraph.id
          ? {
            ...graph,
            name: nextName,
            description: graphDescriptionDraft.trim() || null,
            directory_path: nextDirectoryPath,
            user_ids: nextUserIds,
          }
        : graph,
    );
    patchDocument(document.config, document.layout, nextGraphs);
    setError(null);
  }

  function materializeInspectorNodeFromDraft(draft: InspectorDraftSnapshot): WorkflowNodeConfig {
    if (!selectedNode) throw new Error("No node is selected.");
    const rawId = selectedNode.id;
    const rawName = draft.displayName.trim();
    if (!rawName) throw new Error("Display name is required.");

    let nextKnownValues = { ...draft.knownValues };
    if (selectedNode.type.startsWith("trigger:") && nextKnownValues.enabled !== false) {
      delete nextKnownValues.enabled;
    }
    if (hasCollapsedTargetUi) {
      const activeTargetKey = draft.effectTargetKey ?? activeEffectTargetKey(nextKnownValues);
      for (const key of EFFECT_TARGET_KEYS) {
        if (key !== activeTargetKey) {
          delete nextKnownValues[key];
        }
      }
    }
    for (const field of selectedCatalogEntry?.fields ?? []) {
      if (field.kind !== "json") continue;
      if (isExactBindingValue(nextKnownValues[field.key])) continue;
      if (field.read_only) continue;
      if (field.key === "tool_argument_policies" && supportsStructuredToolArgumentPolicies(selectedNode.type)) {
        if (draft.jsonDisplayModes[field.key] === "structured") {
          const validation = toolArgumentPolicyDraftRowsToValue(draft.toolArgumentPolicyDrafts[field.key] ?? []);
          if (validation.errors.length > 0 || !validation.value) {
            throw new Error(toolArgumentPolicyValidationErrorSummary(validation.errors));
          }
          nextKnownValues[field.key] = validation.value;
          continue;
        }
        const fallbackValue = workflowJsonFieldFallbackValue(field, {});
        nextKnownValues[field.key] = parseJsonDraft(draft.jsonDrafts[field.key] ?? "", fallbackValue);
        continue;
      }
      const fallbackValue = workflowJsonFieldFallbackValue(field, field.required ? {} : undefined);
      nextKnownValues[field.key] = parseJsonDraft(draft.jsonDrafts[field.key] ?? "", fallbackValue);
    }
    for (const field of selectedCatalogEntry?.fields ?? []) {
      if (field.kind !== "string_list") continue;
      nextKnownValues[field.key] = (draft.stringListDrafts[field.key] ?? "")
        .split("\n")
        .map((item) => item.trim())
        .filter(Boolean);
    }
    for (const field of selectedCatalogEntry?.fields ?? []) {
      if (field.kind !== "string_map") continue;
      if (isExactBindingValue(nextKnownValues[field.key])) continue;
      if (field.read_only) continue;
      nextKnownValues[field.key] = parseStringMapDraftRows(
        draft.stringMapDrafts[field.key] ?? [],
        field.label,
        field.required,
      );
    }
    for (const field of selectedCatalogEntry?.fields ?? []) {
      if (field.kind !== "object_list") continue;
      const currentItems = Array.isArray(nextKnownValues[field.key]) ? [...(nextKnownValues[field.key] as unknown[])] : [];
      field.item_fields.forEach((itemField) => {
        if (itemField.kind !== "json") return;
        currentItems.forEach((item, index) => {
          if (!isRecord(item)) return;
          const draftKey = objectListJsonDraftKey(field.key, index, itemField.key);
          const currentValue = item[itemField.key];
          if (isExactBindingValue(currentValue)) return;
          const fallbackValue = workflowJsonFieldFallbackValue(itemField, itemField.required ? {} : undefined);
          const parsedValue = parseJsonDraft(draft.jsonDrafts[draftKey] ?? "", fallbackValue);
          if (parsedValue === undefined) {
            delete item[itemField.key];
          } else {
            item[itemField.key] = parsedValue;
          }
        });
      });
      nextKnownValues[field.key] = currentItems;
    }

    if (isCommunicationEffectNodeType(selectedNode.type)) {
      const communicationTargetField = selectedCatalogEntry?.fields.find((field) => field.key === "communication_target_id");
      const selectedTargetValue = typeof nextKnownValues.communication_target_id === "string"
        ? nextKnownValues.communication_target_id
        : "";
      const selectedTargetOption = communicationTargetField?.options.find((option) => option.value === selectedTargetValue);
      const threadField = selectedCatalogEntry?.fields.find((field) => field.key === "thread_id");
      const threadMode = threadField
        ? fieldValueMode(threadField, nextKnownValues.thread_id)
        : "literal";
      const communicationState = workflowCommunicationTargetInspectorState(
        selectedNode.type,
        selectedTargetOption,
        nextKnownValues.thread_id,
        threadMode,
      );
      if (communicationState?.validation?.blocked) {
        throw new Error(communicationState.validation.message);
      }
    }
    if (selectedNode.type === AGENT_CREATE_NODE_TYPE && selectedCatalogEntry) {
      nextKnownValues = pruneAgentCreateBackendFieldValues(nextKnownValues, selectedCatalogEntry.fields);
    }

    return removeUndefinedValues({
      id: rawId,
      display_name: rawName,
      type: selectedNode.type,
      ...draft.unknownValues,
      ...nextKnownValues,
    }) as WorkflowNodeConfig;
  }

  function replaceSelectedNode(nextNode: WorkflowNodeConfig, originalNodeId: string) {
    if (!document || !selectedNode) return;
    if (workflowEditLocked && !(isTutorialMode && tutorialCurrentRequiredAction()?.kind === "bind-field")) return;
    if (isTutorialMode && activeWorkflowTutorial && activeWorkflowDocument) {
      const nextConfig = cloneConfig(activeWorkflowDocument.config);
      const collectionName = kindCollectionKey(nodeKindFromType(selectedNode.type));
      nextConfig[collectionName] = nextConfig[collectionName].map((node) =>
        node.id === originalNodeId ? nextNode : node,
      );
      nextConfig.edges = nextConfig.edges.map((edge) => ({
        ...edge,
        source_id: edge.source_id === originalNodeId ? nextNode.id : edge.source_id,
        target_id: edge.target_id === originalNodeId ? nextNode.id : edge.target_id,
      }));
      const nextLayout = cloneLayout(activeWorkflowDocument.layout);
      if (nextNode.id !== originalNodeId) {
        nextLayout.nodes[nextNode.id] = nextLayout.nodes[originalNodeId] ?? fallbackPosition(0);
        delete nextLayout.nodes[originalNodeId];
      }
      patchWorkflowDocument(
        nextConfig,
        nextLayout,
        activeWorkflowDocument.graphs,
        [nextNode.id],
        undefined,
        activeWorkflowDocument.output_schemas,
        activeWorkflowDocument.revision,
      );
      setProgrammaticSelection([nextNode.id], nextNode.id);
      setSelectedEdgeId(null);
      setInspectorDraftError(null);
      setError(null);
      return;
    }
    const currentDocument = document;
    const nextConfig = cloneConfig(document.config);
    const collectionName = kindCollectionKey(nodeKindFromType(selectedNode.type));
    nextConfig[collectionName] = nextConfig[collectionName].map((node) =>
      node.id === originalNodeId ? nextNode : node,
    );
    nextConfig.edges = nextConfig.edges.map((edge) => ({
      ...edge,
      source_id: edge.source_id === originalNodeId ? nextNode.id : edge.source_id,
      target_id: edge.target_id === originalNodeId ? nextNode.id : edge.target_id,
    }));
    const nextLayout = cloneLayout(document.layout);
    let nextGraphs: WorkflowGraphMetadata[] | undefined;
    let nextOutputSchemas: WorkflowOutputSchemas | undefined;
    if (nextNode.id !== originalNodeId) {
      const nodeIdMap = new Map([[originalNodeId, nextNode.id]]);
      const rewrittenConfig = rewriteWorkflowConfigBindingReferences(nextConfig, nodeIdMap);
      nextConfig.triggers = rewrittenConfig.triggers;
      nextConfig.gates = rewrittenConfig.gates;
      nextConfig.effects = rewrittenConfig.effects;
      nextConfig.queries = rewrittenConfig.queries;
      nextGraphs = rewriteWorkflowGraphNodeIds(currentDocument.graphs ?? [], nodeIdMap);
      nextOutputSchemas = rewriteWorkflowOutputSchemaNodeIds(
        currentDocument.output_schemas ?? {},
        nodeIdMap,
      );
      nextLayout.nodes[nextNode.id] = nextLayout.nodes[originalNodeId] ?? fallbackPosition(0);
      delete nextLayout.nodes[originalNodeId];
    }
    patchWorkflowDocument(
      nextConfig,
      nextLayout,
      nextGraphs,
      [nextNode.id],
      undefined,
      nextOutputSchemas,
    );
    setProgrammaticSelection([nextNode.id], nextNode.id);
    setSelectedEdgeId(null);
    setNodeEditOrigins((current) => {
      const next = { ...current };
      const originId = current[originalNodeId] ?? (savedDocument && nodeById(savedDocument.config, originalNodeId) ? originalNodeId : null);
      delete next[originalNodeId];
      if (originId && originId !== nextNode.id) {
        next[nextNode.id] = originId;
      }
      return next;
    });
    setInspectorDraftError(null);
    setError(null);
  }

  function resetSelectedNodeChanges() {
    if (!document || !selectedNode) return;
    if (isTutorialMode) return;
    if (workflowEditLocked && !(isTutorialMode && tutorialCurrentRequiredAction()?.kind === "bind-field")) return;
    const originalNodeId = nodeEditOrigins[selectedNode.id] ?? selectedNode.id;
    const savedWorkflowDocument = savedDocument;
    if (!savedWorkflowDocument) {
      deleteNodes([selectedNode.id]);
      return;
    }
    const savedNode = nodeById(savedWorkflowDocument.config, originalNodeId);
    if (!savedNode) {
      deleteNodes([selectedNode.id]);
      return;
    }

    const nextConfig = cloneConfig(document.config);
    const currentCollectionName = kindCollectionKey(nodeKindFromType(selectedNode.type));
    const savedCollectionName = kindCollectionKey(nodeKindFromType(savedNode.type));
    if (currentCollectionName === savedCollectionName) {
      nextConfig[currentCollectionName] = nextConfig[currentCollectionName].map((node) =>
        node.id === selectedNode.id ? cloneNodeConfig(savedNode) : node,
      );
    } else {
      nextConfig.triggers = (nextConfig.triggers ?? []).filter((node) => node.id !== selectedNode.id);
      nextConfig.gates = (nextConfig.gates ?? []).filter((node) => node.id !== selectedNode.id);
      nextConfig.effects = (nextConfig.effects ?? []).filter((node) => node.id !== selectedNode.id);
      nextConfig.queries = (nextConfig.queries ?? []).filter((node) => node.id !== selectedNode.id);
      nextConfig[savedCollectionName] = [
        ...(nextConfig[savedCollectionName] ?? []).filter((node) => node.id !== originalNodeId),
        cloneNodeConfig(savedNode),
      ];
    }
    if (selectedNode.id !== originalNodeId) {
      nextConfig.edges = nextConfig.edges.map((edge) => ({
        ...edge,
        source_id: edge.source_id === selectedNode.id ? originalNodeId : edge.source_id,
        target_id: edge.target_id === selectedNode.id ? originalNodeId : edge.target_id,
      }));
      const nodeIdMap = new Map([[selectedNode.id, originalNodeId]]);
      const rewrittenConfig = rewriteWorkflowConfigBindingReferences(nextConfig, nodeIdMap);
      nextConfig.triggers = rewrittenConfig.triggers;
      nextConfig.gates = rewrittenConfig.gates;
      nextConfig.effects = rewrittenConfig.effects;
      nextConfig.queries = rewrittenConfig.queries;
    }
    const nextLayout = cloneLayout(document.layout);
    const savedPosition = savedWorkflowDocument.layout.nodes[originalNodeId];
    nextLayout.nodes[originalNodeId] = savedPosition ? { ...savedPosition } : fallbackPosition(0);
    let nextGraphs: WorkflowGraphMetadata[] | undefined;
    let nextOutputSchemas: WorkflowOutputSchemas | undefined;
    if (selectedNode.id !== originalNodeId) {
      const nodeIdMap = new Map([[selectedNode.id, originalNodeId]]);
      nextGraphs = rewriteWorkflowGraphNodeIds(document.graphs ?? [], nodeIdMap);
      nextOutputSchemas = rewriteWorkflowOutputSchemaNodeIds(
        document.output_schemas ?? {},
        nodeIdMap,
        savedWorkflowDocument.output_schemas ?? {},
      );
      delete nextLayout.nodes[selectedNode.id];
    }
    patchWorkflowDocument(
      nextConfig,
      nextLayout,
      nextGraphs,
      [originalNodeId],
      undefined,
      nextOutputSchemas,
    );
    setProgrammaticSelection([originalNodeId], originalNodeId);
    setSelectedEdgeId(null);
    hydrateInspectorDraftFromNode(savedNode, getCatalogEntry(catalog, savedNode.type));
    setNodeEditOrigins((current) => {
      const next = { ...current };
      for (const [nodeId, originId] of Object.entries(current)) {
        if (nodeId === selectedNode.id || originId === originalNodeId) {
          delete next[nodeId];
        }
      }
      return next;
    });
    setInspectorDraftError(null);
    setError(null);
  }

  function stageInspectorDraft(nextDraft: Partial<InspectorDraftSnapshot> = {}) {
    if (!document || !selectedNode) return;
    if (workflowEditLocked && !(isTutorialMode && tutorialCurrentRequiredAction()?.kind === "bind-field")) return;
    try {
      const nextNode = materializeInspectorNodeFromDraft({
        nodeId: nextDraft.nodeId ?? inspectorNodeId,
        displayName: nextDraft.displayName ?? inspectorDisplayName,
        knownValues: nextDraft.knownValues ?? inspectorKnownValues,
        unknownValues: nextDraft.unknownValues ?? inspectorUnknownValues,
        effectTargetKey: nextDraft.effectTargetKey ?? inspectorEffectTargetKey,
        jsonDrafts: nextDraft.jsonDrafts ?? inspectorJsonDrafts,
        toolArgumentPolicyDrafts: nextDraft.toolArgumentPolicyDrafts ?? inspectorToolArgumentPolicyDrafts,
        jsonDisplayModes: nextDraft.jsonDisplayModes ?? inspectorJsonDisplayModes,
        stringListDrafts: nextDraft.stringListDrafts ?? inspectorStringListDrafts,
        stringMapDrafts: nextDraft.stringMapDrafts ?? inspectorStringMapDrafts,
      });
      replaceSelectedNode(nextNode, selectedNode.id);
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : String(reason);
      setInspectorDraftError(message);
      setError(message);
    }
  }

  function onConnect(connection: Connection) {
    if (!document || !connection.source || !connection.target) return;
    if (workflowEditLocked && !(isTutorialMode && tutorialCurrentRequiredAction()?.kind === "connect")) return;
    addWorkflowEdge(connection.source, connection.target, connection.sourceHandle);
  }

  function onEdgesDelete(deletedEdges: Edge[]) {
    if (!document || workflowEditLocked) return;
    const deleted = new Set(deletedEdges.map((edge) => edge.id));
    const nextConfig = cloneConfig(document.config);
    nextConfig.edges = nextConfig.edges.filter(
      (edge) => !deleted.has(edgeId(edge)),
    );
    patchDocument(nextConfig, cloneLayout(document.layout));
    setSelectedEdgeId(null);
  }

  function onNodesDelete(deletedNodes: Node[]) {
    if (workflowEditLocked) return;
    deleteNodes(deletedNodes.map((node) => node.id));
  }

  async function switchWorkflowPack(nextWorkflowPack: string) {
    if (!nextWorkflowPack || nextWorkflowPack === selectedWorkflowPackReference) return;
    if (hasUnsavedWorkflowChanges) {
      const confirmed = window.confirm("Discard unsaved workflow changes and switch workflow packs?");
      if (!confirmed) return;
    }
    setError(null);
    try {
      await loadWorkflowDocument(nextWorkflowPack);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  async function handleWorkflowPackChange(event: ReactChangeEvent<HTMLSelectElement>) {
    await switchWorkflowPack(event.target.value);
  }

  function confirmDiscardUnsavedWorkflowPackAction(): boolean {
    if (!hasUnsavedWorkflowChanges) return true;
    return window.confirm("Discard unsaved workflow changes before managing workflow packs?");
  }

  function workflowPackFormFor(pack: WorkflowPackDescriptor | null): WorkflowPackFormState {
    return {
      packId: pack?.id ?? "",
      packVersion: pack?.version ?? "v1",
      description: pack?.description ?? "",
    };
  }

  function openCreateWorkflowPackModal() {
    if (!confirmDiscardUnsavedWorkflowPackAction()) return;
    setWorkflowPackForm({ packId: "", packVersion: "v1", description: "" });
    setWorkflowPackError(null);
    setWorkflowPackModalMode("create");
  }

  function openEditWorkflowPackModal() {
    if (!selectedWorkflowPack || !confirmDiscardUnsavedWorkflowPackAction()) return;
    setWorkflowPackForm(workflowPackFormFor(selectedWorkflowPack));
    setWorkflowPackError(null);
    setWorkflowPackModalMode("edit");
  }

  function closeWorkflowPackModal() {
    if (workflowPackSubmitting) return;
    setWorkflowPackModalMode(null);
    setWorkflowPackError(null);
  }

  function applyWorkflowPackMutation(mutation: WorkflowPackMutation) {
    const nextDocument = normalizeWorkflowEditorDocument(mutation.document, selectedDirectoryPath);
    setSavedDocument(nextDocument);
    setUndoHistory([]);
    setNodeEditOrigins({});
    setSelectedGraphId(null);
    setSelectedEdgeId(null);
    setPendingConnection(null);
    setNodeConnectionMenu(null);
    setInspectorDraftError(null);
    applyWorkflowDocumentState(
      nextDocument,
      [],
      nextDocument.validation_diagnostics ?? [],
      { autoFit: true, markClean: true, nodeEditOrigins: {} },
    );
    setSaveState("saved");
  }

  async function submitWorkflowPackModal(event: ReactFormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!document || workflowEditLocked || !workflowPackModalMode) return;
    const packId = workflowPackForm.packId.trim();
    const packVersion = workflowPackForm.packVersion.trim() || "v1";
    const description = workflowPackForm.description.trim();
    if (!packId) {
      setWorkflowPackError("Pack ID is required.");
      return;
    }
    if (!packVersion) {
      setWorkflowPackError("Version is required.");
      return;
    }
    setWorkflowPackSubmitting(true);
    setWorkflowPackError(null);
    try {
      const mutation = await fetchJson<WorkflowPackMutation>(
        "/api/workflow-editor/packs",
        {
          method: workflowPackModalMode === "create" ? "POST" : "PUT",
          body: JSON.stringify({
            workflow_pack: workflowPackModalMode === "edit" ? selectedWorkflowPackReference : undefined,
            directory_path: selectedDirectoryPath,
            pack_id: packId,
            pack_version: packVersion,
            description,
            expected_revision:
              workflowPackModalMode === "create"
                ? document.pack_list_revision
                : selectedWorkflowPack?.revision,
          }),
        },
      );
      applyWorkflowPackMutation(mutation);
      setWorkflowPackModalMode(null);
    } catch (reason) {
      setWorkflowPackError(
        reason instanceof ApiError && reason.status === 409
          ? "This workflow pack changed on the server. Reload before saving again."
          : reason instanceof Error
            ? reason.message
            : String(reason),
      );
    } finally {
      setWorkflowPackSubmitting(false);
    }
  }

  async function removeSelectedWorkflowPack() {
    if (!document || workflowEditLocked || !selectedWorkflowPack) return;
    if (workflowPackOptions.length <= 1) return;
    if (!confirmDiscardUnsavedWorkflowPackAction()) return;
    const confirmed = window.confirm(`Remove ${selectedWorkflowPack.display_name || selectedWorkflowPack.reference} from active workflow packs?`);
    if (!confirmed) return;
    setWorkflowPackSubmitting(true);
    setWorkflowPackError(null);
    try {
      const mutation = await fetchJson<WorkflowPackMutation>(
        "/api/workflow-editor/packs",
        {
          method: "DELETE",
          body: JSON.stringify({
            workflow_pack: selectedWorkflowPack.reference,
            directory_path: selectedDirectoryPath,
            expected_revision: document.pack_list_revision,
          }),
        },
      );
      applyWorkflowPackMutation(mutation);
    } catch (reason) {
      setWorkflowPackError(
        reason instanceof ApiError && reason.status === 409
          ? "The workflow pack list changed on the server. Reload before saving again."
          : reason instanceof Error
            ? reason.message
            : String(reason),
      );
    } finally {
      setWorkflowPackSubmitting(false);
    }
  }

  async function validateDocument() {
    if (!document || workflowReadLocked) return;
    const validation = await fetchJson<WorkflowEditorValidation>(
      "/api/workflow-editor/validate",
      {
        method: "POST",
        body: JSON.stringify({
          workflow_pack: selectedWorkflowPackReference || undefined,
          config: document.config,
          layout: document.layout,
          graphs: document.graphs,
        }),
      },
    );
    patchDocument(
      validation.config,
      validation.layout,
      validation.graphs,
      undefined,
      validation.validation_diagnostics,
      validation.output_schemas,
      undefined,
      { recordUndo: false },
    );
  }

  async function saveDocument() {
    if (!document || workflowEditLocked || inspectorDraftError) return;
    setSaveState("saving");
    setError(null);
    try {
      const expectedRevision = savedDocument?.revision ?? document.revision;
      const saved = await fetchJson<WorkflowEditorSave>(
        "/api/workflow-editor/document",
        {
          method: "PUT",
          body: JSON.stringify({
            workflow_pack: selectedWorkflowPackReference || undefined,
            config: document.config,
            layout: document.layout,
            graphs: document.graphs,
            expected_revision: expectedRevision,
          }),
        },
      );
      const normalizedSavedDocument = normalizeWorkflowEditorDocument(
        {
          ...document,
          ...saved,
          output_schemas: saved.output_schemas,
        },
        selectedDirectoryPath,
      );
      patchDocument(
        saved.config,
        saved.layout,
        saved.graphs,
        undefined,
        saved.validation_diagnostics,
        saved.output_schemas,
        saved.revision,
        { recordUndo: false },
      );
      setSavedDocument(normalizedSavedDocument);
      setUndoHistory([]);
      setNodeEditOrigins({});
      setSaveState("saved");
    } catch (reason) {
      setSaveState("idle");
      setError(
        reason instanceof ApiError && reason.status === 409
          ? "This workflow changed on the server. Reload the workflow before saving again; your local edits are still visible."
          : reason instanceof Error
            ? reason.message
            : String(reason),
      );
    }
  }

  function updateSelectedTriggerEnabled(nextEnabled: boolean) {
    if (!selectedNode || !selectedNode.type.startsWith("trigger:")) return;
    const next = { ...inspectorKnownValues, enabled: workflowTriggerEnabledValue(nextEnabled) };
    if (next.enabled === undefined) {
      delete next.enabled;
    }
    setInspectorKnownValues(next);
    stageInspectorDraft({ knownValues: next });
  }

  function updateKnownField(key: string, value: unknown) {
    const next = { ...inspectorKnownValues, [key]: value };
    let nextJsonDrafts = inspectorJsonDrafts;
    if (selectedNode?.type === "gate:filter") {
      if (key === "match" && value !== undefined && value !== null && value !== "") {
        delete next.when;
        nextJsonDrafts = { ...inspectorJsonDrafts, when: "" };
      }
      if (key === "when" && value !== undefined && value !== null && value !== "") {
        delete next.match;
        nextJsonDrafts = { ...inspectorJsonDrafts, match: "" };
      }
    }
    setInspectorKnownValues(next);
    if (nextJsonDrafts !== inspectorJsonDrafts) {
      setInspectorJsonDrafts(nextJsonDrafts);
    }
    stageInspectorDraft({ knownValues: next, jsonDrafts: nextJsonDrafts });
    const expectedAction = tutorialCurrentRequiredAction();
    if (
      isTutorialMode &&
      expectedAction?.kind === "bind-field" &&
      selectedNode &&
      expectedAction.nodeId === selectedNode.id &&
      expectedAction.fieldKey === key &&
      String(value) === expectedAction.bindingExpression
    ) {
      completeTutorialAction({
        kind: "bind-field",
        nodeId: selectedNode.id,
        fieldKey: key,
        bindingExpression: expectedAction.bindingExpression,
      });
    }
  }

  function updateToolArgumentPolicyDraftRows(fieldKey: string, nextRows: ToolArgumentPolicyDraftRow[]) {
    const nextToolArgumentPolicyDrafts = { ...inspectorToolArgumentPolicyDrafts, [fieldKey]: nextRows };
    const validation = toolArgumentPolicyDraftRowsToValue(nextRows);
    const nextJsonDrafts = validation.value
      ? { ...inspectorJsonDrafts, [fieldKey]: JSON.stringify(validation.value, null, 2) }
      : inspectorJsonDrafts;
    setInspectorToolArgumentPolicyDrafts(nextToolArgumentPolicyDrafts);
    if (validation.value) {
      setInspectorJsonDrafts(nextJsonDrafts);
    }
    stageInspectorDraft({
      toolArgumentPolicyDrafts: nextToolArgumentPolicyDrafts,
      ...(validation.value ? { jsonDrafts: nextJsonDrafts } : {}),
    });
  }

  function updateToolArgumentPolicyJsonDisplayMode(
    field: WorkflowEditorFieldDefinition,
    nextMode: JsonDisplayMode,
  ) {
    if (!supportsStructuredToolArgumentPolicies(selectedNode?.type) || field.key !== "tool_argument_policies") {
      return;
    }
    const currentMode = inspectorJsonDisplayModes[field.key] ?? "structured";
    if (currentMode === nextMode) return;

    if (nextMode === "structured") {
      try {
        const fallbackValue = workflowJsonFieldFallbackValue(field, {});
        const parsedValue = parseJsonDraft(inspectorJsonDrafts[field.key] ?? "", fallbackValue);
        const structuredRows = toolArgumentPolicyDraftRowsFromValue(parsedValue, field.options.map((option) => option.value));
        if (!structuredRows) return;
        const validation = toolArgumentPolicyDraftRowsToValue(structuredRows);
        if (validation.errors.length > 0 || !validation.value) return;
        const nextToolArgumentPolicyDrafts = { ...inspectorToolArgumentPolicyDrafts, [field.key]: structuredRows };
        const nextJsonDrafts = { ...inspectorJsonDrafts, [field.key]: JSON.stringify(validation.value, null, 2) };
        setInspectorToolArgumentPolicyDrafts(nextToolArgumentPolicyDrafts);
        setInspectorJsonDrafts(nextJsonDrafts);
        setInspectorJsonDisplayModes((current) => ({ ...current, [field.key]: "structured" }));
        setInspectorDraftError(null);
        setError(null);
      } catch {
        return;
      }
      return;
    }

    const currentRows = inspectorToolArgumentPolicyDrafts[field.key] ?? [];
    const validation = toolArgumentPolicyDraftRowsToValue(currentRows);
    if (validation.value) {
      setInspectorJsonDrafts((current) => ({
        ...current,
        [field.key]: JSON.stringify(validation.value, null, 2),
      }));
    }
    setInspectorJsonDisplayModes((current) => ({ ...current, [field.key]: "raw" }));
    setInspectorDraftError(null);
    setError(null);
  }

  function mutateToolArgumentPolicyDraftRows(
    fieldKey: string,
    updater: (rows: ToolArgumentPolicyDraftRow[]) => ToolArgumentPolicyDraftRow[],
  ) {
    const nextRows = updater(inspectorToolArgumentPolicyDrafts[fieldKey] ?? []);
    updateToolArgumentPolicyDraftRows(fieldKey, nextRows);
  }

  function updateFieldMode(key: string, mode: "literal" | "bind" | "template") {
    setInspectorFieldModes((current) => ({ ...current, [key]: mode }));
  }

  function updateBindableEnumFieldMode(
    field: WorkflowEditorFieldDefinition,
    value: unknown,
    nextMode: "literal" | "bind" | "template",
  ) {
    updateFieldMode(field.key, nextMode);
    if (nextMode === "bind") {
      updateKnownField(field.key, selectedNodeBindingOptions[0]?.value ?? "");
    } else {
      updateKnownField(field.key, enumLiteralFallbackValue(field, value));
    }
  }

  function updateMetadataFieldMode(
    field: WorkflowEditorFieldDefinition,
    value: unknown,
    nextMode: "literal" | "bind" | "template",
  ) {
    updateFieldMode(field.key, nextMode);
    if (nextMode === "bind") {
      updateKnownField(field.key, selectedNodeBindingOptions[0]?.value ?? "");
    } else if (isExactBindingValue(value)) {
      updateKnownField(field.key, undefined);
    }
  }

  async function copyBindingExpression(bindingExpression: string) {
    try {
      if (!navigator.clipboard?.writeText) {
        throw new Error("Clipboard API is not available in this browser.");
      }
      await navigator.clipboard.writeText(bindingExpression);
      setCopiedBindingExpression(bindingExpression);
      setOutputCopyError(null);
    } catch (reason) {
      setCopiedBindingExpression(null);
      setOutputCopyError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  function updateEffectTargetKey(nextKey: EffectTargetKey) {
    const currentKey = effectTargetKey;
    const currentValue = inspectorKnownValues[currentKey];
    const currentMode = inspectorFieldModes[currentKey]
      ?? (effectTargetField ? fieldValueMode(effectTargetField, currentValue) : "literal");
    const next = { ...inspectorKnownValues };
    for (const key of EFFECT_TARGET_KEYS) {
      delete next[key];
    }
    if (currentValue !== undefined && currentValue !== "") {
      next[nextKey] = currentValue;
    }
    const nextModes = { ...inspectorFieldModes };
    for (const key of EFFECT_TARGET_KEYS) {
      delete nextModes[key];
    }
    nextModes[nextKey] = currentMode;
    setInspectorEffectTargetKey(nextKey);
    setInspectorKnownValues(next);
    setInspectorFieldModes(nextModes);
    stageInspectorDraft({ knownValues: next, effectTargetKey: nextKey });
  }

  function renderEffectTargetField() {
    if (!hasCollapsedTargetUi || !effectTargetField) return null;
    const value = inspectorKnownValues[effectTargetKey];
    const mode = inspectorFieldModes[effectTargetKey] ?? fieldValueMode(effectTargetField, value);
    const isDisabled = effectTargetField.read_only || (workflowEditLocked && !tutorialAllowsInspectorFieldEdit(effectTargetKey));
    const targetTypeLabel = selectedNode?.type === "effect:agent:prompt" ? "Prompt target type" : "Target type";
    const targetPlaceholder =
      effectTargetKey === "session_id"
        ? "Session ID"
        : effectTargetKey === "vm_name"
          ? "VM name"
          : "VM name or session ID";
    return (
      <div className="inspector-field" key="effect-target">
        <div className="field-toolbar">
          {renderFieldHeader({ ...effectTargetField, label: "Target" })}
          {renderModeSelect(effectTargetField, mode, (nextMode) => {
            updateFieldMode(effectTargetKey, nextMode);
            if (nextMode === "bind") {
              updateKnownField(effectTargetKey, selectedNodeBindingOptions[0]?.value ?? "");
            } else if (nextMode === "template") {
              updateKnownField(effectTargetKey, typeof value === "string" ? value : "");
            } else {
              updateKnownField(effectTargetKey, isExactBindingValue(value) ? "" : value ?? "");
            }
          }, isDisabled)}
        </div>
        <label className="inspector-field">
          <div className="field-toolbar">
            <div className="field-header">
              <span>{targetTypeLabel}</span>
            </div>
          </div>
          <select
            value={effectTargetKey}
            onChange={(event) => updateEffectTargetKey(event.target.value as EffectTargetKey)}
            disabled={isDisabled}
          >
            <option value="target">Generic target</option>
            <option value="session_id">Session ID</option>
            <option value="vm_name">VM name</option>
          </select>
        </label>
        {mode === "bind"
          ? renderBindingSelect(effectTargetField, selectedNodeBindingOptions, value, (nextValue) => updateKnownField(effectTargetKey, nextValue))
          : mode === "template"
            ? (
              <div className="template-editor">
                <label className="field-inline">
                  <span>Insert output</span>
                  <select
                    value=""
                    onChange={(event) => {
                      const nextValue = event.target.value;
                      if (!nextValue) return;
                      updateKnownField(effectTargetKey, insertBindingReference(value, nextValue));
                      event.target.value = "";
                    }}
                    disabled={selectedNodeBindingOptions.length === 0 || isDisabled}
                  >
                    <option value="">
                      {selectedNodeBindingOptions.length === 0 ? "No outputs available" : "Insert output reference"}
                    </option>
                    {selectedNodeBindingOptions.map((option) => (
                      <option key={`vm-copy-target-template-${option.value}`} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </label>
                <input
                  value={typeof value === "string" ? value : ""}
                  placeholder={`${targetPlaceholder} or binding`}
                  onChange={(event) => updateKnownField(effectTargetKey, event.target.value)}
                  disabled={isDisabled}
                />
              </div>
            )
          : (
            <input
              value={typeof value === "string" ? value : ""}
              placeholder={targetPlaceholder}
              onChange={(event) => updateKnownField(effectTargetKey, event.target.value)}
              disabled={isDisabled}
            />
          )}
      </div>
    );
  }

  function renderSelectedNodeOutputContract() {
    if (!selectedNodeOutputContract) return null;
    const contract = selectedNodeOutputContract;
    const stateLabel =
      contract.state === "declared"
        ? "Declared outputs"
        : contract.state === "derived"
          ? "Derived outputs"
          : contract.state === "empty"
            ? "No known outputs"
            : "Unknown outputs";

    return (
      <div className="output-contract-panel">
        <div className="field-toolbar">
          <div className="field-header">
            <span>Outputs</span>
          </div>
          <span className={`output-contract-state output-contract-state-${contract.state}`}>{stateLabel}</span>
        </div>
        {contract.rows.length === 0 ? (
          <p className="output-contract-empty">{contract.note}</p>
        ) : (
          <>
            <p className="field-description">{contract.note}</p>
            <div className="output-contract-list">
              {contract.rows.map((row) => (
                <div key={row.bindingExpression} className={`output-contract-row output-contract-row-${row.state}`}>
                  <div className="output-contract-row-main">
                    <div className="output-contract-path-line">
                      <span className="output-contract-path">{row.bindingPath}</span>
                    </div>
                    <div className="output-contract-type-line">
                      <span className="output-contract-type">{row.typeLabel}</span>
                      {row.details.length > 0 ? (
                        <span className="output-contract-details">{row.details.join(" • ")}</span>
                      ) : null}
                    </div>
                  </div>
                  <Tooltip content={`Copy ${row.bindingExpression}`}>
                    <button
                      type="button"
                      className="subtle-button output-contract-copy"
                      onClick={() => void copyBindingExpression(row.bindingExpression)}
                      aria-label={`Copy binding expression for ${row.bindingPath}`}
                      disabled={workflowEditLocked}
                    >
                      <CopyIcon />
                    </button>
                  </Tooltip>
                </div>
              ))}
            </div>
          </>
        )}
        {copiedBindingExpression ? (
          <p className="output-copy-status" role="status" aria-live="polite">
            Copied {copiedBindingExpression}.
          </p>
        ) : null}
        {outputCopyError ? (
          <p className="output-copy-status output-copy-error" role="status" aria-live="polite">
            {outputCopyError}
          </p>
        ) : null}
      </div>
    );
  }

  function renderModeSelect(
    field: WorkflowEditorFieldDefinition,
    mode: "literal" | "bind" | "template",
    onChange: (nextMode: "literal" | "bind" | "template") => void,
    disabled: boolean,
  ) {
    return (
      <label className="mode-select">
        <select value={mode} onChange={(event) => onChange(event.target.value as "literal" | "bind" | "template")} disabled={disabled}>
          <option value="literal">Literal</option>
          {field.bindable ? <option value="bind">Bind</option> : null}
          {field.bindable && field.kind === "string" ? <option value="template">Template</option> : null}
        </select>
      </label>
    );
  }

  function renderFieldHeader(field: WorkflowEditorFieldDefinition) {
    return (
      <div className="field-header">
        <span>{field.label}</span>
        {field.description ? (
          <Tooltip
            content={field.description}
            ariaLabel={`${field.label} field help`}
            focusable
            triggerClassName="workflow-field-tooltip-trigger"
          >
            <span className="workflow-field-tooltip-icon" aria-hidden="true">i</span>
          </Tooltip>
        ) : null}
      </div>
    );
  }

  function renderSelectedTriggerToggle() {
    if (!selectedNode || !selectedNode.type.startsWith("trigger:")) return null;
    const enabled = inspectorKnownValues.enabled === undefined
      ? workflowTriggerEnabled(selectedNode)
      : inspectorKnownValues.enabled !== false;
    return (
      <div className="trigger-enable-control">
        <label className="checkbox-row">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(event) => updateSelectedTriggerEnabled(event.target.checked)}
            disabled={workflowEditLocked}
            aria-label="Trigger enabled"
          />
          <span>{enabled ? "Trigger enabled" : "Trigger disabled"}</span>
        </label>
      </div>
    );
  }

  function updateStringListField(key: string, value: string) {
    const next = { ...inspectorStringListDrafts, [key]: value };
    setInspectorStringListDrafts(next);
    stageInspectorDraft({ stringListDrafts: next });
  }

  function addStringMapRow(key: string) {
    if (workflowEditLocked) return;
    const next = {
      ...inspectorStringMapDrafts,
      [key]: [...(inspectorStringMapDrafts[key] ?? []), createStringMapDraftRow()],
    };
    setInspectorStringMapDrafts(next);
    stageInspectorDraft({ stringMapDrafts: next });
  }

  function updateStringMapRow(
    fieldKey: string,
    rowId: string,
    key: "key" | "value",
    value: string,
  ) {
    const next = {
      ...inspectorStringMapDrafts,
      [fieldKey]: (inspectorStringMapDrafts[fieldKey] ?? []).map((row) =>
        row.id === rowId ? { ...row, [key]: value } : row,
      ),
    };
    setInspectorStringMapDrafts(next);
    stageInspectorDraft({ stringMapDrafts: next });
  }

  function removeStringMapRow(fieldKey: string, rowId: string) {
    if (workflowEditLocked) return;
    const next = {
      ...inspectorStringMapDrafts,
      [fieldKey]: (inspectorStringMapDrafts[fieldKey] ?? []).filter((row) => row.id !== rowId),
    };
    setInspectorStringMapDrafts(next);
    stageInspectorDraft({ stringMapDrafts: next });
  }

  function renderDirectoryPathSelect(
    field: WorkflowEditorFieldDefinition,
    value: unknown,
    onChange: (nextValue: string) => void,
    disabled: boolean,
  ) {
    const stringValue = typeof value === "string" ? value : "";
    const options = ensureDirectorySelectOption(
      directoryPathOptions,
      stringValue,
      `${stringValue} (not in directory tree)`,
    );
    return (
      <select
        value={stringValue}
        onChange={(event) => onChange(event.target.value)}
        disabled={disabled}
        aria-label={field.label}
      >
        <option value="">Use workflow context</option>
        {options.map((option) => (
          <option key={`${field.key}-${option.value}`} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    );
  }

  function renderOptionCheckboxRows(
    field: WorkflowEditorFieldDefinition,
    options: WorkflowFieldOption[],
    selectedValues: Set<string>,
    disabled: boolean,
  ) {
    return options.map((option) => {
      const checked = selectedValues.has(option.value);
      return (
        <label key={option.value} className="checkbox-row option-checkbox-row">
          <input
            type="checkbox"
            checked={checked}
            onChange={(event) => {
              const nextValues = new Set(selectedValues);
              if (event.target.checked) {
                nextValues.add(option.value);
              } else {
                nextValues.delete(option.value);
              }
              updateStringListField(field.key, Array.from(nextValues).join("\n"));
            }}
            disabled={disabled}
          />
          <span>{option.label}</span>
        </label>
      );
    });
  }

  function renderStringMapEditor(field: WorkflowEditorFieldDefinition, disabled: boolean) {
    const rows = inspectorStringMapDrafts[field.key] ?? [];
    return (
      <div className="string-map-editor">
        {rows.map((row, index) => (
          <div key={row.id} className="string-map-row">
            <label>
              <span>Key</span>
              <input
                value={row.key}
                onChange={(event) => updateStringMapRow(field.key, row.id, "key", event.target.value)}
                disabled={disabled}
              />
            </label>
            <label>
              <span>Value</span>
              <input
                value={row.value}
                onChange={(event) => updateStringMapRow(field.key, row.id, "value", event.target.value)}
                disabled={disabled}
              />
            </label>
            <button
              type="button"
              className="subtle-button string-map-remove"
              onClick={() => removeStringMapRow(field.key, row.id)}
              disabled={disabled}
              aria-label={`Remove ${field.label} row ${index + 1}`}
            >
              Remove
            </button>
          </div>
        ))}
        <button type="button" className="subtle-button" onClick={() => addStringMapRow(field.key)} disabled={disabled}>
          Add label
        </button>
      </div>
    );
  }

  function renderToolArgumentPolicyField(
    field: WorkflowEditorFieldDefinition,
    value: unknown,
    mode: "literal" | "bind" | "template",
    disabled: boolean,
  ) {
    const rows = inspectorToolArgumentPolicyDrafts[field.key] ?? [];
    const displayMode = inspectorJsonDisplayModes[field.key] ?? "structured";
    const validation = toolArgumentPolicyDraftRowsToValue(rows);
    const operatorOptions = [
      { value: "required", label: "required" },
      { value: "allowlist", label: "allowlist" },
      { value: "denylist", label: "denylist" },
      { value: "fixed", label: "fixed" },
      { value: "pattern", label: "pattern" },
      { value: "enum", label: "enum" },
    ] as const;

    return (
      <div key={field.key} className="inspector-field">
        <div className="field-toolbar">
          {renderFieldHeader(field)}
          {renderModeSelect(field, mode, (nextMode) => {
            updateFieldMode(field.key, nextMode);
            if (nextMode === "bind") {
              updateKnownField(field.key, selectedNodeBindingOptions[0]?.value ?? "");
            } else {
              updateKnownField(field.key, undefined);
            }
          }, disabled)}
        </div>
        {mode === "bind" ? (
          renderBindingSelect(field, selectedNodeBindingOptions, value, (nextValue) => updateKnownField(field.key, nextValue))
        ) : (
          <div className="tool-argument-policy-editor">
            <label className="mode-select">
              <span>JSON mode</span>
              <select
                value={displayMode}
                onChange={(event) => updateToolArgumentPolicyJsonDisplayMode(field, event.target.value as JsonDisplayMode)}
                disabled={disabled}
              >
                <option value="structured">Structured</option>
                <option value="raw">Raw JSON</option>
              </select>
            </label>

            {displayMode === "structured" ? (
              <div className="tool-argument-policy-structured">
                {rows.length === 0 ? (
                  <p className="field-description">No structured tool policies yet.</p>
                ) : null}
                {rows.map((toolRow, toolIndex) => {
                  const toolPathBase = toolArgumentPolicyRowPath(toolRow.toolName, toolIndex);
                  return (
                    <div key={`${toolIndex}-${toolRow.toolName || "tool"}`} className="object-list-item tool-argument-policy-tool">
                      <div className="object-list-title">
                        <span>Tool policy {toolIndex + 1}</span>
                        <button
                          type="button"
                          className="subtle-button"
                          onClick={() =>
                            mutateToolArgumentPolicyDraftRows(field.key, (currentRows) =>
                              currentRows.filter((_, currentIndex) => currentIndex !== toolIndex),
                            )
                          }
                          disabled={disabled}
                        >
                          Remove
                        </button>
                      </div>

                      <label className="field-inline">
                        <span>Tool name</span>
                        {field.options.length > 0 ? (
                          <select
                            value={toolRow.toolName}
                            onChange={(event) =>
                              mutateToolArgumentPolicyDraftRows(field.key, (currentRows) =>
                                currentRows.map((row, currentIndex) =>
                                  currentIndex === toolIndex ? { ...row, toolName: event.target.value } : row,
                                ),
                              )
                            }
                            disabled={disabled}
                          >
                            <option value="">Select tool</option>
                            {field.options.map((option) => (
                              <option key={option.value} value={option.value}>
                                {option.label}
                              </option>
                            ))}
                          </select>
                        ) : (
                          <input
                            value={toolRow.toolName}
                            onChange={(event) =>
                              mutateToolArgumentPolicyDraftRows(field.key, (currentRows) =>
                                currentRows.map((row, currentIndex) =>
                                  currentIndex === toolIndex ? { ...row, toolName: event.target.value } : row,
                                ),
                              )
                            }
                            placeholder="Tool name"
                            disabled={disabled}
                          />
                        )}
                        {renderToolArgumentPolicyErrorMessages(
                          toolArgumentPolicyFieldErrorMessages(validation.errors, `${toolPathBase}.toolName`),
                        )}
                      </label>

                      <div className="tool-argument-policy-rules">
                        {toolRow.rules.length === 0 ? (
                          <p className="field-description">Add a rule to define argument constraints.</p>
                        ) : null}
                        {toolRow.rules.map((ruleRow, ruleIndex) => {
                          const rulePathBase = toolArgumentPolicyRulePath(toolRow.toolName, toolIndex, ruleIndex);
                          return (
                            <div key={ruleRow.id} className="object-list-item tool-argument-policy-rule">
                              <div className="object-list-title">
                                <span>Rule {ruleIndex + 1}</span>
                                <button
                                  type="button"
                                  className="subtle-button"
                                  onClick={() =>
                                    mutateToolArgumentPolicyDraftRows(field.key, (currentRows) =>
                                      currentRows.map((row, currentIndex) =>
                                        currentIndex === toolIndex
                                          ? {
                                              ...row,
                                              rules: row.rules.filter((_, currentRuleIndex) => currentRuleIndex !== ruleIndex),
                                            }
                                          : row,
                                      ),
                                    )
                                  }
                                  disabled={disabled}
                                >
                                  Remove
                                </button>
                              </div>

                              <label className="field-inline">
                                <span>Rule id</span>
                                <input
                                  value={ruleRow.id}
                                  onChange={(event) =>
                                    mutateToolArgumentPolicyDraftRows(field.key, (currentRows) =>
                                      currentRows.map((row, currentIndex) =>
                                        currentIndex === toolIndex
                                          ? {
                                              ...row,
                                              rules: row.rules.map((rule, currentRuleIndex) =>
                                                currentRuleIndex === ruleIndex ? { ...rule, id: event.target.value } : rule,
                                              ),
                                            }
                                          : row,
                                      ),
                                    )
                                  }
                                  disabled={disabled}
                                />
                                {renderToolArgumentPolicyErrorMessages(
                                  toolArgumentPolicyFieldErrorMessages(validation.errors, `${rulePathBase}.id`),
                                )}
                              </label>

                              <label className="field-inline">
                                <span>Name</span>
                                <input
                                  value={ruleRow.name}
                                  onChange={(event) =>
                                    mutateToolArgumentPolicyDraftRows(field.key, (currentRows) =>
                                      currentRows.map((row, currentIndex) =>
                                        currentIndex === toolIndex
                                          ? {
                                              ...row,
                                              rules: row.rules.map((rule, currentRuleIndex) =>
                                                currentRuleIndex === ruleIndex
                                                  ? { ...rule, name: event.target.value }
                                                  : rule,
                                              ),
                                            }
                                          : row,
                                      ),
                                    )
                                  }
                                  placeholder="Optional rule name"
                                  disabled={disabled}
                                />
                              </label>

                              <div className="tool-argument-policy-constraints">
                                {ruleRow.constraints.map((constraintRow, constraintIndex) => {
                                  const constraintPathBase = toolArgumentPolicyConstraintPath(
                                    toolRow.toolName,
                                    toolIndex,
                                    ruleIndex,
                                    constraintIndex,
                                  );
                                  const operator = constraintRow.operator;
                                  const comparisonOperators = new Set(["allowlist", "denylist", "enum", "pattern"]);
                                  return (
                                    <div
                                      key={constraintRow.id}
                                      className="object-list-item tool-argument-policy-constraint"
                                    >
                                      <div className="object-list-title">
                                        <span>Constraint {constraintIndex + 1}</span>
                                        <button
                                          type="button"
                                          className="subtle-button"
                                          onClick={() =>
                                            mutateToolArgumentPolicyDraftRows(field.key, (currentRows) =>
                                              currentRows.map((row, currentIndex) =>
                                                currentIndex === toolIndex
                                                  ? {
                                                      ...row,
                                                      rules: row.rules.map((rule, currentRuleIndex) =>
                                                        currentRuleIndex === ruleIndex
                                                          ? {
                                                              ...rule,
                                                              constraints: rule.constraints.filter(
                                                                (_, currentConstraintIndex) =>
                                                                  currentConstraintIndex !== constraintIndex,
                                                              ),
                                                            }
                                                          : rule,
                                                      ),
                                                    }
                                                  : row,
                                              ),
                                            )
                                          }
                                          disabled={disabled}
                                        >
                                          Remove
                                        </button>
                                      </div>

                                      <label className="field-inline">
                                        <span>Constraint id</span>
                                        <input
                                          value={constraintRow.id}
                                          onChange={(event) =>
                                            mutateToolArgumentPolicyDraftRows(field.key, (currentRows) =>
                                              currentRows.map((row, currentIndex) =>
                                                currentIndex === toolIndex
                                                  ? {
                                                      ...row,
                                                      rules: row.rules.map((rule, currentRuleIndex) =>
                                                        currentRuleIndex === ruleIndex
                                                          ? {
                                                              ...rule,
                                                              constraints: rule.constraints.map((constraint, currentConstraintIndex) =>
                                                                currentConstraintIndex === constraintIndex
                                                                  ? { ...constraint, id: event.target.value }
                                                                  : constraint,
                                                              ),
                                                            }
                                                          : rule,
                                                      ),
                                                    }
                                                  : row,
                                              ),
                                            )
                                          }
                                          disabled={disabled}
                                        />
                                        {renderToolArgumentPolicyErrorMessages(
                                          toolArgumentPolicyFieldErrorMessages(validation.errors, `${constraintPathBase}.id`),
                                        )}
                                      </label>

                                      <label className="field-inline">
                                        <span>Path</span>
                                        <input
                                          value={constraintRow.path}
                                          onChange={(event) =>
                                            mutateToolArgumentPolicyDraftRows(field.key, (currentRows) =>
                                              currentRows.map((row, currentIndex) =>
                                                currentIndex === toolIndex
                                                  ? {
                                                      ...row,
                                                      rules: row.rules.map((rule, currentRuleIndex) =>
                                                        currentRuleIndex === ruleIndex
                                                          ? {
                                                              ...rule,
                                                              constraints: rule.constraints.map((constraint, currentConstraintIndex) =>
                                                                currentConstraintIndex === constraintIndex
                                                                  ? { ...constraint, path: event.target.value }
                                                                  : constraint,
                                                              ),
                                                            }
                                                          : rule,
                                                      ),
                                                    }
                                                  : row,
                                              ),
                                            )
                                          }
                                          disabled={disabled}
                                        />
                                        {renderToolArgumentPolicyErrorMessages(
                                          toolArgumentPolicyFieldErrorMessages(validation.errors, `${constraintPathBase}.path`),
                                        )}
                                      </label>

                                      <label className="field-inline">
                                        <span>Operator</span>
                                        <select
                                          value={constraintRow.operator}
                                          onChange={(event) =>
                                            mutateToolArgumentPolicyDraftRows(field.key, (currentRows) =>
                                              currentRows.map((row, currentIndex) =>
                                                currentIndex === toolIndex
                                                  ? {
                                                      ...row,
                                                      rules: row.rules.map((rule, currentRuleIndex) =>
                                                        currentRuleIndex === ruleIndex
                                                          ? {
                                                              ...rule,
                                                              constraints: rule.constraints.map((constraint, currentConstraintIndex) =>
                                                                currentConstraintIndex === constraintIndex
                                                                  ? { ...constraint, operator: event.target.value as typeof constraintRow.operator }
                                                                  : constraint,
                                                              ),
                                                            }
                                                          : rule,
                                                      ),
                                                    }
                                                  : row,
                                              ),
                                            )
                                          }
                                          disabled={disabled}
                                        >
                                          <option value="">Select operator</option>
                                          {operatorOptions.map((option) => (
                                            <option key={option.value} value={option.value}>
                                              {option.label}
                                            </option>
                                          ))}
                                        </select>
                                        {renderToolArgumentPolicyErrorMessages(
                                          toolArgumentPolicyFieldErrorMessages(validation.errors, `${constraintPathBase}.operator`),
                                        )}
                                      </label>

                                      {operator === "fixed" ? (
                                        <label className="field-inline">
                                          <span>Fixed value</span>
                                          <input
                                            value={constraintRow.valueText}
                                            onChange={(event) =>
                                              mutateToolArgumentPolicyDraftRows(field.key, (currentRows) =>
                                                currentRows.map((row, currentIndex) =>
                                                  currentIndex === toolIndex
                                                    ? {
                                                        ...row,
                                                        rules: row.rules.map((rule, currentRuleIndex) =>
                                                          currentRuleIndex === ruleIndex
                                                            ? {
                                                                ...rule,
                                                                constraints: rule.constraints.map((constraint, currentConstraintIndex) =>
                                                                  currentConstraintIndex === constraintIndex
                                                                    ? { ...constraint, valueText: event.target.value }
                                                                    : constraint,
                                                                ),
                                                              }
                                                            : rule,
                                                        ),
                                                      }
                                                    : row,
                                                ),
                                              )
                                            }
                                            disabled={disabled}
                                          />
                                          {renderToolArgumentPolicyErrorMessages(
                                            toolArgumentPolicyFieldErrorMessages(validation.errors, `${constraintPathBase}.valueText`),
                                          )}
                                        </label>
                                      ) : null}

                                      {operator === "allowlist" || operator === "denylist" || operator === "enum" ? (
                                        <label className="field-inline">
                                          <span>Values</span>
                                          <textarea
                                            value={constraintRow.valuesText}
                                            rows={4}
                                            onChange={(event) =>
                                              mutateToolArgumentPolicyDraftRows(field.key, (currentRows) =>
                                                currentRows.map((row, currentIndex) =>
                                                  currentIndex === toolIndex
                                                    ? {
                                                        ...row,
                                                        rules: row.rules.map((rule, currentRuleIndex) =>
                                                          currentRuleIndex === ruleIndex
                                                            ? {
                                                                ...rule,
                                                                constraints: rule.constraints.map((constraint, currentConstraintIndex) =>
                                                                  currentConstraintIndex === constraintIndex
                                                                    ? { ...constraint, valuesText: event.target.value }
                                                                    : constraint,
                                                                ),
                                                              }
                                                            : rule,
                                                        ),
                                                      }
                                                    : row,
                                                ),
                                              )
                                            }
                                            disabled={disabled}
                                          />
                                          {renderToolArgumentPolicyErrorMessages(
                                            toolArgumentPolicyFieldErrorMessages(validation.errors, `${constraintPathBase}.valuesText`),
                                          )}
                                        </label>
                                      ) : null}

                                      {operator === "pattern" ? (
                                        <label className="field-inline">
                                          <span>Pattern</span>
                                          <input
                                            value={constraintRow.pattern}
                                            onChange={(event) =>
                                              mutateToolArgumentPolicyDraftRows(field.key, (currentRows) =>
                                                currentRows.map((row, currentIndex) =>
                                                  currentIndex === toolIndex
                                                    ? {
                                                        ...row,
                                                        rules: row.rules.map((rule, currentRuleIndex) =>
                                                          currentRuleIndex === ruleIndex
                                                            ? {
                                                                ...rule,
                                                                constraints: rule.constraints.map((constraint, currentConstraintIndex) =>
                                                                  currentConstraintIndex === constraintIndex
                                                                    ? { ...constraint, pattern: event.target.value }
                                                                    : constraint,
                                                                ),
                                                              }
                                                            : rule,
                                                        ),
                                                      }
                                                    : row,
                                                ),
                                              )
                                            }
                                            disabled={disabled}
                                          />
                                          {renderToolArgumentPolicyErrorMessages(
                                            toolArgumentPolicyFieldErrorMessages(validation.errors, `${constraintPathBase}.pattern`),
                                          )}
                                        </label>
                                      ) : null}

                                      {comparisonOperators.has(operator) ? (
                                        <label className="checkbox-row">
                                          <input
                                            type="checkbox"
                                            checked={constraintRow.caseSensitive}
                                            onChange={(event) =>
                                              mutateToolArgumentPolicyDraftRows(field.key, (currentRows) =>
                                                currentRows.map((row, currentIndex) =>
                                                  currentIndex === toolIndex
                                                    ? {
                                                        ...row,
                                                        rules: row.rules.map((rule, currentRuleIndex) =>
                                                          currentRuleIndex === ruleIndex
                                                            ? {
                                                                ...rule,
                                                                constraints: rule.constraints.map((constraint, currentConstraintIndex) =>
                                                                  currentConstraintIndex === constraintIndex
                                                                    ? { ...constraint, caseSensitive: event.target.checked }
                                                                    : constraint,
                                                                ),
                                                              }
                                                            : rule,
                                                        ),
                                                      }
                                                    : row,
                                                ),
                                              )
                                            }
                                            disabled={disabled}
                                          />
                                          <span>Case sensitive</span>
                                        </label>
                                      ) : null}
                                    </div>
                                  );
                                })}
                                <button
                                  type="button"
                                  className="subtle-button"
                                  onClick={() =>
                                    mutateToolArgumentPolicyDraftRows(field.key, (currentRows) =>
                                      currentRows.map((row, currentIndex) =>
                                        currentIndex === toolIndex
                                          ? {
                                              ...row,
                                              rules: row.rules.map((rule, currentRuleIndex) =>
                                                currentRuleIndex === ruleIndex
                                                  ? {
                                                      ...rule,
                                                      constraints: [...rule.constraints, createToolArgumentPolicyConstraintDraftRow()],
                                                    }
                                                  : rule,
                                              ),
                                            }
                                          : row,
                                      ),
                                    )
                                  }
                                  disabled={disabled}
                                >
                                  Add constraint
                                </button>
                              </div>
                            </div>
                          );
                        })}
                        <button
                          type="button"
                          className="subtle-button"
                          onClick={() =>
                            mutateToolArgumentPolicyDraftRows(field.key, (currentRows) =>
                              currentRows.map((row, currentIndex) =>
                                currentIndex === toolIndex
                                  ? {
                                      ...row,
                                      rules: [...row.rules, createToolArgumentPolicyRuleDraftRow()],
                                    }
                                  : row,
                              ),
                            )
                          }
                          disabled={disabled}
                        >
                          Add rule
                        </button>
                      </div>
                    </div>
                  );
                })}
                <button
                  type="button"
                  className="subtle-button"
                  onClick={() =>
                    mutateToolArgumentPolicyDraftRows(field.key, (currentRows) => [
                      ...currentRows,
                      createToolArgumentPolicyDraftRow(field.options[0]?.value ?? ""),
                    ])
                  }
                  disabled={disabled}
                >
                  Add policy tool
                </button>
                {validation.errors.length > 0 ? (
                  <div className="error-text">
                    {validation.errors.map((error) => (
                      <div key={`${error.path}-${error.message}`}>{error.path}: {error.message}</div>
                    ))}
                  </div>
                ) : null}
              </div>
            ) : (
              <textarea
                value={inspectorJsonDrafts[field.key] ?? ""}
                rows={8}
                onChange={(event) => {
                  const nextText = event.target.value;
                  const next = { ...inspectorJsonDrafts, [field.key]: nextText };
                  setInspectorJsonDrafts(next);
                  stageInspectorDraft({ jsonDrafts: next });
                }}
                disabled={disabled}
              />
            )}
          </div>
        )}
      </div>
    );
  }

  function updateObjectListItem(fieldKey: string, index: number, key: string, value: unknown) {
    const currentItems = Array.isArray(inspectorKnownValues[fieldKey]) ? [...(inspectorKnownValues[fieldKey] as unknown[])] : [];
    const currentItem = isRecord(currentItems[index]) ? { ...currentItems[index] } : {};
    if (value === undefined || value === "") {
      delete currentItem[key];
    } else {
      currentItem[key] = value;
    }
    currentItems[index] = currentItem;
    const next = { ...inspectorKnownValues, [fieldKey]: currentItems };
    setInspectorKnownValues(next);
    stageInspectorDraft({ knownValues: next });
  }

  function addObjectListItem(key: string, field: WorkflowEditorFieldDefinition) {
    if (workflowEditLocked) return;
    const currentItems = Array.isArray(inspectorKnownValues[key]) ? [...(inspectorKnownValues[key] as unknown[])] : [];
    const nextItem = Object.fromEntries(
      field.item_fields
        .filter((itemField) => itemField.required)
        .map((itemField) => [itemField.key, itemField.kind === "boolean" ? false : ""]),
    );
    currentItems.push(nextItem);
    const next = { ...inspectorKnownValues, [key]: currentItems };
    setInspectorKnownValues(next);
    stageInspectorDraft({ knownValues: next });
  }

  function removeObjectListItem(key: string, index: number) {
    if (workflowEditLocked) return;
    const currentItems = Array.isArray(inspectorKnownValues[key]) ? [...(inspectorKnownValues[key] as unknown[])] : [];
    currentItems.splice(index, 1);
    const next = { ...inspectorKnownValues, [key]: currentItems };
    setInspectorKnownValues(next);
    stageInspectorDraft({ knownValues: next });
  }

  function updateSessionListMetadataRuleRows(nextRows: SessionListMetadataRuleDraftRow[]) {
    const metadataValues = sessionListMetadataRuleValues(nextRows);
    const nextKnownValues = { ...inspectorKnownValues };
    const nextJsonDrafts = { ...inspectorJsonDrafts };
    for (const key of SESSION_LIST_METADATA_FIELD_KEYS) {
      delete nextKnownValues[key];
      const value = metadataValues[key];
      nextJsonDrafts[key] = value === undefined ? "" : JSON.stringify(value, null, 2);
      if (value !== undefined) {
        nextKnownValues[key] = value;
      }
    }
    setInspectorSessionListMetadataRules(nextRows);
    setInspectorKnownValues(nextKnownValues);
    setInspectorJsonDrafts(nextJsonDrafts);
    setInspectorFieldModes((current) => ({
      ...current,
      metadata_equals: "literal",
      metadata_exists: "literal",
      metadata_prefix: "literal",
    }));
    setInspectorSessionListMetadataRawMode(false);
    stageInspectorDraft({
      knownValues: nextKnownValues,
      jsonDrafts: nextJsonDrafts,
    });
  }

  function mutateSessionListMetadataRuleRows(
    updater: (rows: SessionListMetadataRuleDraftRow[]) => SessionListMetadataRuleDraftRow[],
  ) {
    const derivedRows = sessionListMetadataRuleDraftRowsFromValues(inspectorKnownValues).rows;
    const currentRows = inspectorSessionListMetadataRules.length > 0
      ? inspectorSessionListMetadataRules
      : derivedRows;
    updateSessionListMetadataRuleRows(updater(currentRows));
  }

  function renderGenericJsonField(
    field: WorkflowEditorFieldDefinition,
    value: unknown,
    mode: "literal" | "bind" | "template",
    isDisabled: boolean,
  ) {
    return (
      <div key={field.key} className="inspector-field">
        <div className="field-toolbar">
          {renderFieldHeader(field)}
          {renderModeSelect(field, mode, (nextMode) => {
            updateFieldMode(field.key, nextMode);
            if (nextMode === "bind") {
              updateKnownField(field.key, selectedNodeBindingOptions[0]?.value ?? "");
            } else {
              updateKnownField(field.key, undefined);
            }
          }, isDisabled)}
        </div>
        {mode === "bind"
          ? renderBindingSelect(
            field,
            selectedNodeBindingOptions,
            value,
            (nextValue) => updateKnownField(field.key, nextValue),
          )
          : (
            <textarea
              value={inspectorJsonDrafts[field.key] ?? ""}
              rows={field.key === "outputs" ? 5 : 8}
              onChange={(event) => {
                const nextText = event.target.value;
                const next = { ...inspectorJsonDrafts, [field.key]: nextText };
                setInspectorJsonDrafts(next);
                stageInspectorDraft({ jsonDrafts: next });
              }}
              disabled={isDisabled}
            />
          )}
      </div>
    );
  }

  function renderSessionListMetadataRules(isDisabled: boolean) {
    const metadataFields = SESSION_LIST_METADATA_FIELD_KEYS
      .map((fieldKey) => selectedCatalogEntry?.fields.find((field) => field.key === fieldKey))
      .filter((field): field is WorkflowEditorFieldDefinition => Boolean(field));
    const unsupportedByMode = metadataFields.some((field) => {
      const value = inspectorKnownValues[field.key];
      return (inspectorFieldModes[field.key] ?? fieldValueMode(field, value)) !== "literal";
    });
    const supportedByValues = sessionListMetadataRuleDraftRowsFromValues(inspectorKnownValues).supported;
    const structured = !unsupportedByMode && supportedByValues;
    const showRawFields = !structured || inspectorSessionListMetadataRawMode;
    const headerField: WorkflowEditorFieldDefinition = {
      key: "metadata_rules",
      label: "Metadata rules",
      kind: "json",
      required: false,
      bindable: false,
      read_only: false,
      multiline: false,
      description: "Rules that match session metadata with exact values, key presence, or string prefixes.",
      placeholder: null,
      json_value_type: "object",
      default_value: null,
      options: [],
      item_fields: [],
    };

    if (showRawFields) {
      return (
        <div key="metadata_rules" className="inspector-field metadata-rule-editor">
          <div className="field-toolbar">
            {renderFieldHeader(headerField)}
            {structured ? (
              <button
                type="button"
                className="subtle-button metadata-rule-mode-toggle"
                onClick={() => setInspectorSessionListMetadataRawMode(false)}
              >
                Rule editor
              </button>
            ) : null}
          </div>
          <div className="metadata-rule-raw-fields">
            {metadataFields.map((field) => {
              const value = inspectorKnownValues[field.key];
              const mode = inspectorFieldModes[field.key] ?? fieldValueMode(field, value);
              const rawFieldDisabled = field.read_only
                || (workflowEditLocked && !tutorialAllowsInspectorFieldEdit(field.key));
              return renderGenericJsonField(field, value, mode, rawFieldDisabled);
            })}
          </div>
        </div>
      );
    }

    const derivedRows = sessionListMetadataRuleDraftRowsFromValues(inspectorKnownValues).rows;
    const metadataRuleRows = inspectorSessionListMetadataRules.length > 0
      ? inspectorSessionListMetadataRules
      : derivedRows;
    return (
      <div key="metadata_rules" className="inspector-field metadata-rule-editor">
        <div className="field-toolbar">
          {renderFieldHeader(headerField)}
          <button
            type="button"
            className="subtle-button metadata-rule-mode-toggle"
            onClick={() => setInspectorSessionListMetadataRawMode(true)}
          >
            Raw filters
          </button>
        </div>
        <div className="metadata-rule-list">
          {metadataRuleRows.map((row, index) => {
            const rowClassName = row.kind === "exists"
              ? "metadata-rule-row metadata-rule-row-exists"
              : "metadata-rule-row";
            return (
              <div key={row.id} className={rowClassName}>
                <label className="field-inline">
                  <span>Rule</span>
                  <select
                    value={row.kind}
                    onChange={(event) => {
                      const nextKind = event.target.value as SessionListMetadataRuleKind;
                      mutateSessionListMetadataRuleRows((currentRows) =>
                        currentRows.map((currentRow) =>
                          currentRow.id === row.id ? { ...currentRow, kind: nextKind } : currentRow,
                        ),
                      );
                    }}
                    disabled={isDisabled}
                  >
                    <option value="equals">Equals</option>
                    <option value="exists">Exists</option>
                    <option value="prefix">Prefix</option>
                  </select>
                </label>
                <label className="field-inline">
                  <span>Metadata key</span>
                  <input
                    value={row.key}
                    placeholder="user.name"
                    onChange={(event) => {
                      const nextKey = event.target.value;
                      mutateSessionListMetadataRuleRows((currentRows) =>
                        currentRows.map((currentRow) =>
                          currentRow.id === row.id ? { ...currentRow, key: nextKey } : currentRow,
                        ),
                      );
                    }}
                    disabled={isDisabled}
                  />
                </label>
                {row.kind !== "exists" ? (
                  <label className="field-inline">
                    <span>{row.kind === "prefix" ? "Prefix" : "Exact value"}</span>
                    <input
                      value={row.value}
                      onChange={(event) => {
                        const nextValue = event.target.value;
                        mutateSessionListMetadataRuleRows((currentRows) =>
                          currentRows.map((currentRow) =>
                            currentRow.id === row.id
                              ? { ...currentRow, value: nextValue }
                              : currentRow,
                          ),
                        );
                      }}
                      disabled={isDisabled}
                    />
                  </label>
                ) : null}
                <button
                  type="button"
                  className="subtle-button metadata-rule-remove"
                  onClick={() =>
                    mutateSessionListMetadataRuleRows((currentRows) =>
                      currentRows.filter((currentRow) => currentRow.id !== row.id),
                    )
                  }
                  disabled={isDisabled}
                  aria-label={`Remove metadata rule ${index + 1}`}
                >
                  Remove
                </button>
              </div>
            );
          })}
          <button
            type="button"
            className="subtle-button"
            onClick={() =>
              mutateSessionListMetadataRuleRows((currentRows) => [
                ...currentRows,
                createSessionListMetadataRuleDraftRow(),
              ])
            }
            disabled={isDisabled}
          >
            Add metadata rule
          </button>
        </div>
      </div>
    );
  }

  function renderField(field: WorkflowEditorFieldDefinition) {
    const value = inspectorKnownValues[field.key];
    const mode = inspectorFieldModes[field.key] ?? fieldValueMode(field, value);
    const isDisabled = field.read_only || (workflowEditLocked && !tutorialAllowsInspectorFieldEdit(field.key));
    const selectedNodeType = selectedNode?.type;

    if (selectedNodeType === "query:session:list" && isSessionListMetadataFieldKey(field.key)) {
      return field.key === "metadata_equals" ? renderSessionListMetadataRules(isDisabled) : null;
    }

    if (field.kind === "string") {
      const communicationState = field.key === "thread_id" && isCommunicationEffectNodeType(selectedNodeType)
        ? workflowCommunicationTargetInspectorState(
          selectedNodeType,
          selectedCatalogEntry?.fields.find((currentField) => currentField.key === "communication_target_id")
            ?.options.find((option) => option.value === (typeof inspectorKnownValues.communication_target_id === "string"
              ? inspectorKnownValues.communication_target_id
              : "")),
          value,
          mode,
        )
        : null;
      return (
        <div key={field.key} className="inspector-field">
          <div className="field-toolbar">
            {renderFieldHeader(field)}
            {renderModeSelect(field, mode, (nextMode) => {
              updateFieldMode(field.key, nextMode);
              if (nextMode === "bind") {
                updateKnownField(field.key, selectedNodeBindingOptions[0]?.value ?? "");
              } else if (nextMode === "template") {
                updateKnownField(field.key, typeof value === "string" ? value : "");
              } else {
                updateKnownField(field.key, isExactBindingValue(value) ? "" : value ?? "");
              }
            }, isDisabled)}
          </div>
          {mode === "bind"
            ? renderBindingSelect(field, selectedNodeBindingOptions, value, (nextValue) => updateKnownField(field.key, nextValue))
            : mode === "template"
              ? (
                <div className="template-editor">
                  <label className="field-inline">
                    <span>Insert output</span>
                    <select
                      value=""
                      onChange={(event) => {
                        const nextValue = event.target.value;
                        if (!nextValue) return;
                        updateKnownField(field.key, insertBindingReference(value, nextValue));
                        event.target.value = "";
                      }}
                      disabled={selectedNodeBindingOptions.length === 0 || isDisabled}
                    >
                      <option value="">
                        {selectedNodeBindingOptions.length === 0 ? "No outputs available" : "Insert output reference"}
                      </option>
                      {selectedNodeBindingOptions.map((option) => (
                        <option key={`${field.key}-template-${option.value}`} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </label>
                  {renderStringInput(
                    field,
                    value,
                    (nextValue) => updateKnownField(field.key, nextValue),
                    isDisabled,
                    "Use ${{node.output.path}} inside the text",
                  )}
                </div>
              )
            : (
              field.key === "directory_path"
                ? renderDirectoryPathSelect(
                  field,
                  value,
                  (nextValue) => updateKnownField(field.key, nextValue || undefined),
                  isDisabled,
                )
                : renderStringInput(
                  field,
                  value,
                  (nextValue) => updateKnownField(field.key, nextValue),
                  isDisabled,
                  "",
              )
            )}
          {field.key === "thread_id" && communicationState ? (
            <div>
              <p className="field-description">{communicationState.threadHelp}</p>
              {communicationState.advisory ? (
                <div className="callout warning-callout">{communicationState.advisory}</div>
              ) : null}
              {communicationState.validation ? (
                <div className="callout error-callout">{communicationState.validation.message}</div>
              ) : null}
            </div>
          ) : null}
        </div>
      );
    }

    if (field.kind === "number") {
      return (
        <div key={field.key} className="inspector-field">
          <div className="field-toolbar">
            {renderFieldHeader(field)}
            {renderModeSelect(field, mode, (nextMode) => {
              updateFieldMode(field.key, nextMode);
              if (nextMode === "bind") {
                updateKnownField(field.key, selectedNodeBindingOptions[0]?.value ?? "");
              } else {
                updateKnownField(field.key, typeof value === "number" ? value : undefined);
              }
            }, isDisabled)}
          </div>
          {mode === "bind"
            ? renderBindingSelect(field, selectedNodeBindingOptions, value, (nextValue) => updateKnownField(field.key, nextValue))
            : (
              <input
                type="number"
                value={typeof value === "number" ? value : ""}
                onChange={(event) => {
                  const nextValue = event.target.value;
                  updateKnownField(field.key, nextValue === "" ? undefined : Number(nextValue));
                }}
                disabled={isDisabled}
              />
            )}
        </div>
      );
    }

    if (field.kind === "boolean") {
      return (
        <div key={field.key} className="inspector-field">
          <div className="field-toolbar">
            {renderFieldHeader(field)}
            {renderModeSelect(field, mode, (nextMode) => {
              updateFieldMode(field.key, nextMode);
              if (nextMode === "bind") {
                updateKnownField(field.key, selectedNodeBindingOptions[0]?.value ?? "");
              } else {
                updateKnownField(field.key, Boolean(value));
              }
            }, isDisabled)}
          </div>
          {mode === "bind"
            ? renderBindingSelect(field, selectedNodeBindingOptions, value, (nextValue) => updateKnownField(field.key, nextValue))
            : (
              <label className="checkbox-row">
                <input
                  type="checkbox"
                  checked={typeof value === "boolean" ? value : false}
                  onChange={(event) => updateKnownField(field.key, event.target.checked)}
                  disabled={isDisabled}
                />
                <span>{typeof value === "boolean" && value ? "Enabled" : "Disabled"}</span>
              </label>
            )}
        </div>
      );
    }

    if (field.kind === "enum") {
      if (field.key === "communication_target_id" && isCommunicationEffectNodeType(selectedNodeType)) {
        const threadField = communicationThreadField;
        const threadValue = threadField ? inspectorKnownValues[threadField.key] : undefined;
        const threadMode = threadField
          ? inspectorFieldModes[threadField.key] ?? fieldValueMode(threadField, threadValue)
          : "literal";
        const communicationState = workflowCommunicationTargetInspectorState(
          selectedNodeType,
          field.options.find((option) => option.value === (typeof value === "string" ? value : "")),
          threadValue,
          threadMode,
        );
        return (
          <label key={field.key} className="inspector-field">
            <div className="field-toolbar">
              {renderFieldHeader(field)}
            </div>
            <select
              value={typeof value === "string" ? value : ""}
              onChange={(event) => updateKnownField(field.key, event.target.value)}
              disabled={isDisabled}
            >
              <option value="">Select</option>
              {field.options.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
            {communicationState ? (
              <div>
                {communicationState.targetHelp.map((line, index) => (
                  <p className="field-description" key={`${field.key}-target-help-${index}`}>{line}</p>
                ))}
              </div>
            ) : null}
          </label>
        );
      }
      const enumOptions = selectedNodeType === AGENT_CREATE_NODE_TYPE
        ? agentCreateFieldOptionsForBackend(
          field,
          normalizedAgentCreateExecutionBackend(inspectorKnownValues),
        )
        : field.options;
      return (
        <div key={field.key} className="inspector-field">
          <div className="field-toolbar">
            {renderFieldHeader(field)}
            {renderModeSelect(
              field,
              mode,
              (nextMode) => updateBindableEnumFieldMode(field, value, nextMode),
              isDisabled,
            )}
          </div>
          {mode === "bind"
            ? renderBindingSelect(field, selectedNodeBindingOptions, value, (nextValue) => updateKnownField(field.key, nextValue))
            : (
              <select
                value={enumOptionValue(field, value)}
                onChange={(event) => updateKnownField(field.key, event.target.value || undefined)}
                disabled={isDisabled}
              >
                <option value="">Select</option>
                {enumOptions.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            )}
        </div>
      );
    }

    if (field.kind === "string_list") {
      const listValue = inspectorStringListDrafts[field.key]
        ?? (Array.isArray(value) ? value.map((item) => String(item)).join("\n") : "");
      const selectedValues = new Set(
        listValue
          .split("\n")
          .map((item) => item.trim())
          .filter(Boolean),
      );
      if (field.options.length > 0 || fieldSelectionOnly(field)) {
        if (field.key === "allowed_tools") {
          return (
            <div key={field.key} className="inspector-field">
              <div className="field-toolbar">
                {renderFieldHeader(field)}
              </div>
              <div className="option-checkbox-groups">
                {groupToolOptions(field.options).map((group) => {
                  const enabledCount = group.items.filter((option) => selectedValues.has(option.value)).length;
                  return (
                    <details key={group.name} className="option-checkbox-section">
                      <summary className="option-checkbox-section-header">
                        <span className="option-checkbox-section-title">{group.name}</span>
                        <span className="option-checkbox-section-count">{enabledCount} of {group.items.length}</span>
                      </summary>
                      <div className="option-checkbox-list">
                        {renderOptionCheckboxRows(field, group.items, selectedValues, isDisabled)}
                      </div>
                    </details>
                  );
                })}
              </div>
            </div>
          );
        }
        return (
          <div key={field.key} className="inspector-field">
            <div className="field-toolbar">
              {renderFieldHeader(field)}
            </div>
            <div className="option-checkbox-list">
              {renderOptionCheckboxRows(field, field.options, selectedValues, isDisabled)}
              {field.options.length === 0 ? (
                <p className="field-description">No targets configured for this directory.</p>
              ) : null}
            </div>
          </div>
        );
      }
      return (
        <label key={field.key} className="inspector-field">
          <div className="field-toolbar">
            {renderFieldHeader(field)}
          </div>
          <textarea
            value={listValue}
            rows={4}
            placeholder={field.placeholder ?? "One value per line"}
            onChange={(event) => updateStringListField(field.key, event.target.value)}
            disabled={isDisabled}
          />
        </label>
      );
    }

    if (field.kind === "string_map") {
      return (
        <div key={field.key} className="inspector-field">
          <div className="field-toolbar">
            {renderFieldHeader(field)}
            {renderModeSelect(field, mode, (nextMode) => {
              updateFieldMode(field.key, nextMode);
              if (nextMode === "bind") {
                updateKnownField(field.key, selectedNodeBindingOptions[0]?.value ?? "");
              } else {
                updateKnownField(field.key, stringMapRowsToObject(inspectorStringMapDrafts[field.key] ?? []));
              }
            }, isDisabled)}
          </div>
          {mode === "bind"
            ? renderBindingSelect(field, selectedNodeBindingOptions, value, (nextValue) => updateKnownField(field.key, nextValue))
            : renderStringMapEditor(field, isDisabled)}
        </div>
      );
    }

    if (field.key === "tool_argument_policies" && supportsStructuredToolArgumentPolicies(selectedNode?.type)) {
      return renderToolArgumentPolicyField(field, value, mode, isDisabled);
    }

    if (field.kind === "json") {
      return renderGenericJsonField(field, value, mode, isDisabled);
    }

    if (field.kind === "object_list") {
      const items = Array.isArray(value) ? value : [];
      return (
        <div key={field.key} className="inspector-field">
          <div className="field-toolbar">
            {renderFieldHeader(field)}
          </div>
          {items.map((item, index) => {
            const itemValue = isRecord(item) ? item : {};
            return (
              <div key={`${field.key}-${index}`} className="object-list-item">
                <div className="object-list-title">
                  <span>Item {index + 1}</span>
                  <button type="button" className="subtle-button" onClick={() => removeObjectListItem(field.key, index)} disabled={isDisabled}>
                    Remove
                  </button>
                </div>
                {field.item_fields.map((itemField) => {
                  const currentValue = itemValue[itemField.key];
                  const currentMode = fieldValueMode(itemField, currentValue);
                  const nestedJsonKey = objectListJsonDraftKey(field.key, index, itemField.key);
                  return (
                    <div key={itemField.key} className="nested-field">
                      <div className="field-toolbar">
                        {renderFieldHeader(itemField)}
                        {itemField.bindable ? (
                          <label className="mode-select">
                            <span>Value mode</span>
                            <select
                              value={currentMode}
                              onChange={(event) => {
                                const nextMode = event.target.value as "literal" | "bind";
                                if (nextMode === "bind") {
                                  updateObjectListItem(field.key, index, itemField.key, selectedNodeBindingOptions[0]?.value ?? "");
                                } else {
                                  updateObjectListItem(field.key, index, itemField.key, "");
                                }
                              }}
                            >
                              <option value="literal">Literal</option>
                              <option value="bind">Bind</option>
                            </select>
                          </label>
                        ) : null}
                      </div>
                      {currentMode === "bind"
                        ? renderBindingSelect(itemField, selectedNodeBindingOptions, currentValue, (nextValue) =>
                            updateObjectListItem(field.key, index, itemField.key, nextValue),
                          )
                        : itemField.kind === "json"
                          ? (
                            <textarea
                              value={inspectorJsonDrafts[nestedJsonKey] ?? ""}
                              rows={6}
                              onChange={(event) => {
                                const nextText = event.target.value;
                                const next = { ...inspectorJsonDrafts, [nestedJsonKey]: nextText };
                                setInspectorJsonDrafts(next);
                                stageInspectorDraft({ jsonDrafts: next });
                              }}
                            />
                          )
                        : itemField.kind === "enum"
                          ? (
                            <select
                              value={typeof currentValue === "string" ? currentValue : ""}
                              onChange={(event) => updateObjectListItem(field.key, index, itemField.key, event.target.value)}
                            >
                              <option value="">Select</option>
                              {itemField.options.map((option) => (
                                <option key={`${field.key}-${index}-${itemField.key}-${option.value}`} value={option.value}>
                                  {option.label}
                                </option>
                              ))}
                            </select>
                          )
                        : itemField.kind === "boolean"
                          ? (
                            <label className="checkbox-row">
                              <input
                                type="checkbox"
                                checked={typeof currentValue === "boolean" ? currentValue : false}
                                onChange={(event) => updateObjectListItem(field.key, index, itemField.key, event.target.checked)}
                              />
                              <span>{typeof currentValue === "boolean" && currentValue ? "Enabled" : "Disabled"}</span>
                            </label>
                          )
                          : (
                            renderStringInput(
                              itemField,
                              currentValue,
                              (nextValue) => updateObjectListItem(field.key, index, itemField.key, nextValue),
                              isDisabled,
                              "",
                            )
                          )}
                    </div>
                  );
                })}
              </div>
            );
          })}
          <button type="button" className="subtle-button" onClick={() => addObjectListItem(field.key, field)} disabled={isDisabled}>
            Add item
          </button>
        </div>
      );
    }

    return null;
  }

  function renderRunDetailJson(label: string, value: unknown, missing: boolean) {
    if (missing) {
      return (
        <div className="run-detail-data-block run-detail-missing" key={label}>
          <span>{label}</span>
          <strong>Missing legacy trace data</strong>
        </div>
      );
    }
    const empty = Array.isArray(value)
      ? value.length === 0
      : isRecord(value)
        ? !objectHasData(value)
        : value === null || value === undefined;
    return (
      <details className="run-detail-json" key={label}>
        <summary>
          <span>{label}</span>
          <strong>{empty ? "Empty" : "Recorded"}</strong>
        </summary>
      <pre>{formatJson(value, "null")}</pre>
      </details>
    );
  }

  function navigateToAdminHash(hash: string) {
    window.location.hash = hash;
  }

  function renderRunDetailToolCallLink(link: WorkflowRunToolCallLink, scope: "run" | "node", index: number) {
    const toolCallTarget = firstNonEmptyString(
      link.request_id,
      link.correlation_id,
      link.trace_id,
      link.workflow_run_id,
      link.session_id,
    );
    const toolCallHash = toolCallTarget ? detailHashTarget("tool-calls", { q: toolCallTarget }) : null;
    const redactedArgs = link.redacted_args ?? link.args;
    const redactedResult = link.redacted_result ?? link.result;
    const argsSummary = firstNonEmptyString(
      link.args_summary,
      structuredValueSummary(redactedArgs, "Args payload missing", "Empty args"),
      link.args_missing_reason,
    ) ?? "Args payload missing";
    const resultSummary = link.result_empty || normalizeAvailabilityState(link.result_status) === "empty"
      ? "Empty result"
      : firstNonEmptyString(
          link.result_summary,
          structuredValueSummary(redactedResult, "Result payload missing", "Empty result"),
          link.result_missing_reason,
        ) ?? "Result payload missing";

    return (
      <div className="run-detail-link-item" key={`${scope}-${link.tool_name}-${index}-${toolCallTarget ?? "missing"}`}>
        <strong>{scope === "run" ? "Run tool call" : "Tool call"} {link.tool_name}</strong>
        <span>{link.status ?? "status missing"}{link.decision ? ` / ${link.decision}` : ""}</span>
        <span>Request {link.request_id ?? "missing"}</span>
        <span>Correlation {link.correlation_id ?? "missing"}</span>
        {link.approval_request_id ? <span>Approval request {link.approval_request_id}</span> : null}
        {link.workflow_run_id ? <span>Run {link.workflow_run_id}</span> : null}
        {link.trace_id ? <span>Trace {link.trace_id}</span> : null}
        {link.session_id ? <span>Session {link.session_id}</span> : null}
        {link.node_id ? <span>Node {link.node_id}</span> : null}
        <span>Args {argsSummary}</span>
        <span>Result {resultSummary}</span>
        {toolCallHash ? (
          <div className="run-detail-link-actions">
            <button type="button" onClick={() => navigateToAdminHash(toolCallHash)}>
              Tool call audit
            </button>
          </div>
        ) : null}
      </div>
    );
  }

  function operatorSteeringNoteSummary(note: OperatorSteeringNote): string {
    if (note.status === "pending") return `Pending since ${formatRuntimeTimestamp(note.created_at)}`;
    if (note.status === "injecting") return `Injecting since ${formatRuntimeTimestamp(note.updated_at)}`;
    if (note.status === "delivered") return `Delivered ${formatRuntimeTimestamp(note.delivered_at ?? note.updated_at)}`;
    if (note.status === "cancelled") return `Cancelled ${formatRuntimeTimestamp(note.cancelled_at ?? note.updated_at)}`;
    return `${note.status} ${formatRuntimeTimestamp(note.updated_at)}`;
  }

  function renderOperatorSteeringPanel(agent: WorkflowRunAgentLink) {
    const sessionId = agent.session_id?.trim() ?? "";
    const dispatchId = agent.dispatch_id?.trim() ?? "";
    const key = operatorSteeringKey(sessionId, dispatchId);
    if (!key) return null;
    const notes = operatorSteeringNotesByKey[key] ?? [];
    const draft = operatorSteeringDraftsByKey[key] ?? "";
    const loading = Boolean(operatorSteeringLoadingByKey[key]);
    const submitting = Boolean(operatorSteeringSubmittingByKey[key]);
    const error = operatorSteeringErrorsByKey[key] ?? null;
    const latestNote = notes[0] ?? null;
    const canSubmitSteering =
      canControlWorkflowRuns &&
      privilegedUnlocked &&
      !submitting &&
      draft.trim().length > 0;

    return (
      <div className="operator-steering-panel" title="Delivered on the next model call for this session and dispatch.">
        <div className="operator-steering-header">
          <strong>Operator steering</strong>
          <span>{loading ? "Loading" : latestNote ? operatorSteeringNoteSummary(latestNote) : "No notes"}</span>
        </div>
        {canControlWorkflowRuns ? (
          <form
            className="operator-steering-form"
            onSubmit={(event) => {
              event.preventDefault();
              void submitOperatorSteeringNote(agent);
            }}
          >
            <textarea
              value={draft}
              rows={3}
              maxLength={2000}
              placeholder="Move from exploration to implementation."
              disabled={!privilegedUnlocked || submitting}
              onChange={(event) => {
                setOperatorSteeringDraftsByKey((current) => ({ ...current, [key]: event.target.value }));
                if (operatorSteeringErrorsByKey[key]) setOperatorSteeringError(key, null);
              }}
            />
            <div className="operator-steering-actions">
              <button type="submit" className="primary" disabled={!canSubmitSteering}>
                {submitting ? "Queuing..." : "Queue note"}
              </button>
              <button
                type="button"
                className="subtle-button"
                disabled={loading}
                onClick={() => {
                  void loadOperatorSteeringNotes(sessionId, dispatchId);
                }}
              >
                Refresh
              </button>
            </div>
          </form>
        ) : null}
        {canControlWorkflowRuns && !privilegedUnlocked ? (
          <span className="operator-steering-lock">Privileged operations locked</span>
        ) : null}
        {error ? <div className="runtime-run-error">{error}</div> : null}
        {notes.length > 0 ? (
          <div className="operator-steering-notes">
            {notes.slice(0, 4).map((note) => {
              const cancelling = Boolean(operatorSteeringCancellingById[note.note_id]);
              return (
                <div className={`operator-steering-note status-${note.status}`} key={note.note_id}>
                  <span>{operatorSteeringNoteSummary(note)}</span>
                  {note.provider ? <span>{note.provider}{note.provider_endpoint ? ` / ${note.provider_endpoint}` : ""}</span> : null}
                  {note.error ? <span className="missing">{note.error}</span> : null}
                  {note.status === "pending" && canControlWorkflowRuns ? (
                    <button
                      type="button"
                      className="subtle-button"
                      disabled={!privilegedUnlocked || cancelling}
                      onClick={() => {
                        void cancelOperatorSteeringNote(note);
                      }}
                    >
                      {cancelling ? "Cancelling..." : "Cancel"}
                    </button>
                  ) : null}
                </div>
              );
            })}
          </div>
        ) : null}
      </div>
    );
  }

  function renderRunDetailAgentLink(agent: WorkflowRunAgentLink, index: number) {
    const sessionAvailable = availabilityFlag(agent.session_available ?? agent.session_status, Boolean(agent.session_id));
    const agentAvailable = availabilityFlag(
      agent.agent_available ?? agent.agent_status,
      Boolean(firstNonEmptyString(agent.agent_id, agent.execution_id, agent.target_name, agent.vm_name)),
    );
    const logAvailable = availabilityFlag(
      agent.log_available ?? agent.log_status,
      Boolean(agent.log_href || agent.log_reference_href),
    );
    const sessionHash = sessionAvailable && agent.session_id ? detailHashTarget("sessions", { session_id: agent.session_id }) : null;
    const agentTarget = agentAvailable
      ? detailHashTarget("agents", {
          agent_id: firstNonEmptyString(agent.agent_id, agent.execution_id, agent.target_name),
          session_id: agent.session_id,
        })
      : null;
    const logTarget = logAvailable
      ? detailHashTarget("agents", {
          agent_id: firstNonEmptyString(agent.agent_id, agent.execution_id, agent.target_name),
          session_id: agent.session_id,
        })
      : null;
    const promptSummary = firstNonEmptyString(
      agent.prompt_summary,
      agent.prompt,
      agent.prompt_missing_reason,
    ) ?? "Prompt summary missing";
    const resultSummary = agent.result_empty || normalizeAvailabilityState(agent.result_status) === "empty"
      ? "Empty result"
      : firstNonEmptyString(
          agent.result_summary,
          agent.result_missing_reason,
          agent.result_status && normalizeAvailabilityState(agent.result_status) !== "available"
            ? `Result payload ${agent.result_status}`
            : null,
        ) ?? "Result payload missing";
    const logReferenceSummary = agent.log_reference
      ? structuredValueSummary(agent.log_reference, "Log reference recorded", "Log reference recorded")
      : null;

    return (
      <div className="run-detail-link-item" key={`${agent.node_id}-${agent.session_id ?? "session-missing"}-${agent.execution_id ?? index}`}>
        <strong>Agent {agent.session_id ?? agent.vm_name ?? agent.node_id}</strong>
        <span>{agent.status ?? (agent.waiting ? "waiting" : agent.completed ? "completed" : "status missing")}</span>
        {agent.node_type ? <span>Node type {agent.node_type}</span> : null}
        {agent.dispatch_id ? <span>Dispatch {agent.dispatch_id}</span> : null}
        {agent.thread_id ? <span>Thread {agent.thread_id}</span> : null}
        {agent.execution_id ? <span>Execution {agent.execution_id}</span> : null}
        {sessionAvailable ? (
          <span>Session {agent.session_id ?? "missing"}</span>
        ) : (
          <span className="missing">{agent.session_missing_reason ?? "Session record missing"}</span>
        )}
        {agentAvailable && agentTarget ? <span>Agent target available</span> : null}
        {logAvailable ? (
          <span>{logReferenceSummary ?? "Log reference recorded"}</span>
        ) : (
          <span className="missing">{agent.log_missing_reason ?? "No persisted log reference"}</span>
        )}
        <span>Prompt {promptSummary}</span>
        <span>Result {resultSummary}</span>
        {agent.artifact_ids.length > 0 ? <span>Artifacts {agent.artifact_ids.join(", ")}</span> : <span className="missing">No communication artifact records</span>}
        <div className="run-detail-link-actions">
          {sessionHash ? (
            <button type="button" onClick={() => navigateToAdminHash(sessionHash)}>
              Session
            </button>
          ) : null}
          {agentTarget ? (
            <button type="button" onClick={() => navigateToAdminHash(agentTarget)}>
              Agent
            </button>
          ) : null}
          {logTarget ? (
            <button type="button" onClick={() => navigateToAdminHash(logTarget)}>
              Logs
            </button>
          ) : null}
        </div>
        {renderOperatorSteeringPanel(agent)}
      </div>
    );
  }

  function renderRunDetailMissingBlock(label: string, message: string, key: string) {
    return (
      <div className="run-detail-data-block run-detail-missing" key={key}>
        <span>{label}</span>
        <strong>{message}</strong>
      </div>
    );
  }

  function renderRunDetailLinkGroup(
    label: string,
    missingMessage: string,
    items: ReactNode[],
  ) {
    return (
      <div className="run-detail-link-group" key={label}>
        <div className="section-label">{label}</div>
        {items.length > 0 ? <div className="run-detail-node-links">{items}</div> : null}
        {items.length === 0 ? renderRunDetailMissingBlock(label, missingMessage, label) : null}
      </div>
    );
  }

  function renderReadOnlyResolvedField(field: WorkflowEditorFieldDefinition, value: unknown) {
    const textValue = value === undefined || value === null
      ? ""
      : typeof value === "string" || typeof value === "number" || typeof value === "boolean"
        ? String(value)
        : formatJson(value, "null");
    if (field.kind === "json" || field.kind === "object_list" || field.kind === "string_map") {
      return (
        <label key={field.key} className="inspector-label read-only-inspector-field">
          <span>{field.label}</span>
          <textarea value={value === undefined ? "" : formatJson(value, "null")} rows={5} readOnly />
        </label>
      );
    }
    if (field.kind === "string_list") {
      const listValue = Array.isArray(value) ? value.map((item) => String(item)).join("\n") : textValue;
      return (
        <label key={field.key} className="inspector-label read-only-inspector-field">
          <span>{field.label}</span>
          <textarea value={listValue} rows={4} readOnly />
        </label>
      );
    }
    return (
      <label key={field.key} className="inspector-label read-only-inspector-field">
        <span>{field.label}</span>
        <input value={textValue} readOnly />
      </label>
    );
  }

  function renderResolvedInputEditor(
    node: WorkflowRunNodeDetail,
    catalogEntry: WorkflowEditorNodeCatalogEntry | undefined,
  ) {
    const fields = catalogEntry?.fields ?? [];
    const knownFieldKeys = new Set(fields.map((field) => field.key));
    const extraResolvedInputs = Object.fromEntries(
      Object.entries(node.resolved_config).filter(([key]) => !knownFieldKeys.has(key)),
    );
    return (
      <div className="inspect-resolved-inputs">
        <div className="section-label">Resolved inputs</div>
        {fields.length > 0 ? (
          <div className="inspect-resolved-field-list">
            {fields.map((field) => renderReadOnlyResolvedField(field, node.resolved_config[field.key]))}
          </div>
        ) : null}
        {fields.length === 0 || objectHasData(extraResolvedInputs)
          ? renderRunDetailJson(
              fields.length === 0 ? "Resolved inputs" : "Additional resolved inputs",
              fields.length === 0 ? node.resolved_config : extraResolvedInputs,
              node.missing_fields.includes("resolved_config"),
            )
          : null}
      </div>
    );
  }

  function renderWorkflowGeneratorPanel() {
    const generatedNodeCount = workflowNodeIds(config).length;
    const generatorDescription = workflowGeneratorDescription.trim();
    const canGenerateDraft =
      workflowGeneratorOpen &&
      workflowGeneratorState !== "generating" &&
      !workflowEditLocked &&
      generatorDescription.length >= WORKFLOW_GENERATOR_MIN_DESCRIPTION_LENGTH;
    const generatedDraftMatchesDescription =
      workflowGeneratorState === "ready" &&
      workflowGeneratorReadyDescription === generatorDescription;
    const canAcceptGeneratedDraft =
      workflowGeneratorOpen && generatedNodeCount > 0 && generatedDraftMatchesDescription;
    const generatorStatus =
      workflowGeneratorState === "idle"
        ? "Blank draft"
        : workflowGeneratorState === "draftable"
          ? generatorDescription.length >= WORKFLOW_GENERATOR_MIN_DESCRIPTION_LENGTH
            ? "Ready to generate"
            : "Add detail"
          : workflowGeneratorState === "generating"
            ? "Generating"
            : generatedDraftMatchesDescription
              ? `Generated${workflowGeneratorModel ? ` with ${workflowGeneratorModel}` : ""}`
              : workflowGeneratorState === "ready"
                ? "Draft out of date"
                : "Generation failed";
    return (
      <div className="sidebar-section workflow-generator-section">
        <div className="workflow-generator-header">
          <div>
            <div className="section-label">Generate</div>
            <p className="runtime-summary">Draft workflow canvas</p>
          </div>
          <span className={`workflow-generator-status state-${workflowGeneratorState}`}>
            {generatorStatus}
          </span>
        </div>
        <label className="inspector-label workflow-generator-prompt">
          <span>Description</span>
          <textarea
            value={workflowGeneratorDescription}
            rows={7}
            placeholder="Describe the workflow to draft."
            onChange={(event) => handleWorkflowGeneratorDescriptionChange(event.target.value)}
            disabled={workflowEditLocked}
          />
        </label>
        {workflowGeneratorError ? <div className="runtime-run-error">{workflowGeneratorError}</div> : null}
        {workflowGeneratorWarnings.length > 0 ? (
          <div className="workflow-generator-warnings">
            {workflowGeneratorWarnings.slice(0, 4).map((warning, index) => (
              <div key={`${warning}-${index}`}>{warning}</div>
            ))}
          </div>
        ) : null}
        <div className="workflow-generator-actions">
          <button
            type="button"
            className="primary"
            onClick={generateWorkflowGeneratorDraft}
            disabled={!canGenerateDraft}
          >
            Generate draft
          </button>
          <button
            type="button"
            onClick={acceptWorkflowGeneratorDraft}
            disabled={!canAcceptGeneratedDraft || workflowEditLocked}
          >
            Accept draft
          </button>
          <button type="button" className="subtle-button" onClick={cancelWorkflowGenerator}>
            Cancel
          </button>
        </div>
      </div>
    );
  }

  function renderWorkflowPackModal() {
    if (!workflowPackModalMode) return null;
    const title = workflowPackModalMode === "create" ? "Add workflow pack" : "Edit workflow pack";
    const submitLabel = workflowPackModalMode === "create" ? "Add pack" : "Save metadata";
    return (
      <div
        className="modal-backdrop workflow-pack-modal-backdrop"
        role="presentation"
        onClick={(event) => {
          if (event.target === event.currentTarget) {
            closeWorkflowPackModal();
          }
        }}
      >
        <div className="modal-card workflow-pack-modal" role="dialog" aria-modal="true" aria-label={title}>
          <div className="modal-header">
            <div>
              <div className="section-label">Workflow pack</div>
              <h2>{title}</h2>
              {workflowPackModalMode === "edit" && selectedWorkflowPack ? (
                <p>{selectedWorkflowPack.reference}</p>
              ) : null}
            </div>
            <button type="button" className="subtle-button" onClick={closeWorkflowPackModal} disabled={workflowPackSubmitting}>
              Close
            </button>
          </div>
          <form className="modal-body workflow-pack-form" onSubmit={submitWorkflowPackModal}>
            <label className="inspector-label">
              <span>Pack ID</span>
              <input
                value={workflowPackForm.packId}
                onChange={(event) => setWorkflowPackForm((current) => ({ ...current, packId: event.target.value }))}
                disabled={workflowPackSubmitting}
                autoFocus
              />
            </label>
            <label className="inspector-label">
              <span>Version</span>
              <input
                value={workflowPackForm.packVersion}
                onChange={(event) => setWorkflowPackForm((current) => ({ ...current, packVersion: event.target.value }))}
                disabled={workflowPackSubmitting}
              />
            </label>
            <label className="inspector-label">
              <span>Description</span>
              <textarea
                value={workflowPackForm.description}
                onChange={(event) => setWorkflowPackForm((current) => ({ ...current, description: event.target.value }))}
                rows={4}
                disabled={workflowPackSubmitting}
              />
            </label>
            {workflowPackError ? <div className="runtime-run-error">{workflowPackError}</div> : null}
            <div className="workflow-pack-form-actions">
              <button type="button" className="subtle-button" onClick={closeWorkflowPackModal} disabled={workflowPackSubmitting}>
                Cancel
              </button>
              <button type="submit" className="primary" disabled={workflowPackSubmitting || workflowEditLocked}>
                {workflowPackSubmitting ? "Saving..." : submitLabel}
              </button>
            </div>
          </form>
        </div>
      </div>
    );
  }

  function renderWorkflowTutorialChooser() {
    if (!workflowTutorialChooserOpen) return null;
    return (
      <div
        className="modal-backdrop workflow-tutorial-chooser-backdrop"
        role="presentation"
        onClick={(event) => {
          if (event.target === event.currentTarget) {
            setWorkflowTutorialChooserOpen(false);
          }
        }}
      >
        <div className="modal-card workflow-tutorial-chooser-modal" role="dialog" aria-modal="true" aria-label="Tutorials">
          <div className="modal-header workflow-tutorial-chooser-header">
            <div>
              <div className="section-label">Tutorials</div>
              <h2>Choose a walkthrough</h2>
              <p>Start, restart, skip, or clear local tutorial status without touching the saved workflow.</p>
            </div>
            <button type="button" className="subtle-button" onClick={() => setWorkflowTutorialChooserOpen(false)}>
              Close
            </button>
          </div>
          <div className="workflow-tutorial-chooser-body">
            <div className="workflow-tutorial-chooser-list" aria-label="Tutorial chooser">
              {WORKFLOW_TUTORIAL_DEFINITIONS.map((definition) => {
                const status = workflowTutorialChooserStatusFor(definition.id);
                const isActive = status === "in-progress";
                const primaryAction = status === "available"
                  ? () => startWorkflowTutorial(definition.id)
                  : () => restartWorkflowTutorial(definition.id);
                const secondaryAction = status === "available" || isActive
                  ? () => skipWorkflowTutorial(definition.id)
                  : () => clearWorkflowTutorialProgressStatus(definition.id);
                return (
                  <article key={definition.id} className={`workflow-tutorial-chooser-item status-${status}`}>
                    <div className="workflow-tutorial-chooser-item-header">
                      <div>
                        <h3>{definition.title}</h3>
                        <p>{definition.description}</p>
                      </div>
                      <span className="workflow-tutorial-chooser-status">{workflowTutorialChooserStatusLabel(status)}</span>
                    </div>
                    <div className="workflow-tutorial-chooser-actions">
                      <button type="button" className="primary" onClick={primaryAction}>
                        {status === "available" ? "Start" : "Restart"}
                      </button>
                      <button type="button" className="subtle-button" onClick={secondaryAction}>
                        {status === "available" || isActive ? "Skip" : "Clear status"}
                      </button>
                    </div>
                  </article>
                );
              })}
            </div>
          </div>
        </div>
      </div>
    );
  }

  function renderWorkflowTutorialLauncher() {
    const tutorialLauncherHasAttention = hasUnfinishedUnskippedTutorials;
    const tutorialLauncherStatusLabel = tutorialLauncherHasAttention
      ? `${unfinishedUnskippedTutorialCount} available`
      : "All done";
    const tutorialLauncherAriaLabel = tutorialLauncherHasAttention
      ? `Open tutorials chooser, ${unfinishedUnskippedTutorialCount} tutorial${unfinishedUnskippedTutorialCount === 1 ? "" : "s"} available`
      : "Open tutorials chooser, all tutorials completed or skipped";
    return (
      <div className="workflow-tutorial-launcher">
        <button
          type="button"
          className={`workflow-tutorial-launcher-button${tutorialLauncherHasAttention ? " needs-attention" : " is-complete"}`}
          aria-haspopup="dialog"
          aria-expanded={workflowTutorialChooserOpen}
          aria-label={tutorialLauncherAriaLabel}
          onClick={() => {
            setWorkflowTutorialChooserOpen(true);
          }}
        >
          <span>Tutorials</span>
          <span className="workflow-tutorial-launcher-status">{tutorialLauncherStatusLabel}</span>
        </button>
      </div>
    );
  }

  function renderInspectedRunPanel() {
    if (!runDetail) return null;
    const availabilityItems = [
      ["Tool calls", runDetail.data_availability.tool_calls],
      ["Agent links", runDetail.data_availability.agent_links],
      ["Communication artifacts", runDetail.data_availability.communication_artifacts],
    ] as const;
    return (
      <div className="sidebar-section inspected-run-panel">
        <div className="run-detail-header">
          <div>
            <div className="section-label">Inspected run</div>
            <div className="runtime-run-event">{runDetail.event_id}</div>
          </div>
          <span
            className={`runtime-run-state state-${workflowRunStateClassName(runDetail.run_state, runDetail.delivery_state)}`}
          >
            {workflowRunStateLabel(runDetail.run_state, runDetail.delivery_state)}
          </span>
        </div>
        <div className="run-detail-meta-grid">
          <span>Worker {runDetail.worker_id}</span>
          <span>Trace {runDetail.trace_id ?? "missing"}</span>
          <span>Started {formatRuntimeTimestamp(runDetail.occurred_at)}</span>
          <span>Latest {formatRuntimeTimestamp(runDetail.latest_activity_at ?? runDetail.occurred_at)}</span>
          <span>{runDetail.graph.snapshot ? "Historical graph snapshot" : "Current graph fallback"}</span>
          <span>
            {inspectVisitedNodeIds.size} visited / {inspectDisplayNodeIds.size} shown
          </span>
        </div>
        <div className="run-detail-availability">
          {availabilityItems.map(([label, value]) => (
            <span key={label} className={normalizeAvailabilityState(value) === "available" ? "" : "missing"}>
              {label} {availabilitySummary(value)}
            </span>
          ))}
        </div>
        {runDetail.tool_call_links?.length
          ? renderRunDetailLinkGroup(
              "Run-level tool calls",
              availabilitySummary(runDetail.data_availability.tool_calls, "Available", "No persisted tool-call records"),
              runDetail.tool_call_links.map((link, index) => renderRunDetailToolCallLink(link, "run", index)),
            )
          : runDetail.data_availability.tool_calls === "available"
            ? null
            : renderRunDetailMissingBlock(
                "Run-level tool calls",
                availabilitySummary(runDetail.data_availability.tool_calls, "Available", "No persisted tool-call records"),
                "run-tool-calls-missing",
              )}
        {runDetail.error ? <div className="runtime-run-error">{runDetail.error}</div> : null}
        <button type="button" className="primary" onClick={exitInspectMode}>
          Exit inspect mode
        </button>
      </div>
    );
  }

  function renderInspectedNodePanel() {
    if (!runDetail) return null;
    const nodeId = selectedNodeId;
    if (!nodeId || !selectedInspectGraphNode) {
      return (
        <div className="sidebar-section inspect-node-panel">
          <div className="section-label">Node run</div>
          <div className="inspector-summary">
            <div>No node selected</div>
          </div>
        </div>
      );
    }
    const nodeTitle = selectedInspectGraphNode.display_name || selectedInspectGraphNode.id;
    if (!selectedInspectNodeDetail) {
      return (
        <div className="sidebar-section inspect-node-panel">
          <div className="section-label">Node run</div>
          <div className="inspector-summary">
            <div>{nodeTitle}</div>
            <div>{selectedInspectGraphNode.type}</div>
            <div>{selectedInspectGraphNode.id}</div>
          </div>
          <div className="run-detail-data-block run-detail-missing">
            <span>Status</span>
            <strong>Not visited in this run</strong>
          </div>
        </div>
      );
    }
    return (
      <div className="sidebar-section inspect-node-panel">
        <div className="section-label">Node run</div>
        <div className="run-detail-header">
          <div>
            <div className="run-detail-node-title">{nodeTitle}</div>
            <div className="runtime-run-event">{selectedInspectNodeDetail.node_id}</div>
          </div>
          <span className={`runtime-run-state state-${selectedInspectNodeDetail.status ?? "unknown"}`}>
            {selectedInspectNodeDetail.status ?? "not recorded"}
          </span>
        </div>
        <div className="run-detail-node-meta">
          <span>{selectedInspectNodeDetail.node_type ?? selectedInspectGraphNode.type}</span>
          <span>{durationLabel(selectedInspectNodeDetail.duration_ms)}</span>
          <span>
            {selectedInspectNodeDetail.started_at
              ? `Started ${formatRuntimeTimestamp(selectedInspectNodeDetail.started_at)}`
              : "Start missing"}
          </span>
          <span>
            {selectedInspectNodeDetail.finished_at
              ? `Finished ${formatRuntimeTimestamp(selectedInspectNodeDetail.finished_at)}`
              : "Finish missing"}
          </span>
        </div>
        {selectedInspectNodeDetail.error ? <div className="runtime-run-error">{selectedInspectNodeDetail.error}</div> : null}
        {selectedInspectNodeDetail.binding_error ? (
          <div className="runtime-run-error">Binding error: {selectedInspectNodeDetail.binding_error}</div>
        ) : null}
        {renderRunDetailNodeLinks(selectedInspectNodeDetail, runDetail.data_availability)}
        {renderResolvedInputEditor(selectedInspectNodeDetail, selectedInspectCatalogEntry)}
        {renderRunDetailJson("Outputs", selectedInspectNodeDetail.outputs, selectedInspectNodeDetail.missing_fields.includes("outputs"))}
        {renderRunDetailJson("Input payload", selectedInspectNodeDetail.input_payload, selectedInspectNodeDetail.missing_fields.includes("payload"))}
        {renderRunDetailJson("Inputs by source", selectedInspectNodeDetail.inputs_by_source, false)}
        {renderRunDetailJson("Binding trace", selectedInspectNodeDetail.binding_trace, selectedInspectNodeDetail.missing_fields.includes("binding_trace"))}
        {renderRunDetailJson("Raw records", selectedInspectNodeDetail.records, selectedInspectNodeDetail.records.length === 0)}
      </div>
    );
  }

  function renderRunDetailNodeLinks(node: WorkflowRunNodeDetail, availability: WorkflowRunDataAvailability) {
    const toolCallAvailable = normalizeAvailabilityState(availability.tool_calls) === "available";
    const agentLinkAvailable = normalizeAvailabilityState(availability.agent_links) === "available";
    const artifactAvailable = normalizeAvailabilityState(availability.communication_artifacts) === "available";
    const toolCallItems = node.tool_call_links.map((link, index) => renderRunDetailToolCallLink(link, "node", index));
    const approvalItems = node.approval_links.map((approval) => (
      <div className="run-detail-link-item" key={approval.request_id}>
        <strong>Approval {approval.request_id}</strong>
        <span>{approval.action_type}{approval.tool_name ? ` / ${approval.tool_name}` : ""}</span>
        <span>Boundary {approval.directory_path}</span>
        <span>{approval.status}</span>
        <span>Payload {approval.payload_digest}</span>
        {approval.human_summary ? <span>{approval.human_summary}</span> : null}
        {approval.approver ? <span>Approver {approval.approver}</span> : null}
        {approval.decision_at ? <span>Decision {formatRuntimeTimestamp(approval.decision_at)}</span> : null}
      </div>
    ));
    const agentItems = node.agent_links.map((agent, index) => renderRunDetailAgentLink(agent, index));
    const artifactItems = node.communication_artifact_links.map((artifact) => (
      <div className="run-detail-link-item" key={artifact.artifact_id}>
        <strong>Artifact {artifact.artifact_id}</strong>
        <span>{artifact.filename ?? artifact.original_filename ?? "filename missing"}</span>
        <span>{artifact.provider ?? "provider missing"}</span>
        {artifact.sha256 ? <span>SHA-256 {artifact.sha256}</span> : null}
        {artifact.status ? <span>{artifact.status}</span> : null}
      </div>
    ));
    if (
      toolCallItems.length === 0 &&
      approvalItems.length === 0 &&
      agentItems.length === 0 &&
      artifactItems.length === 0 &&
      toolCallAvailable &&
      agentLinkAvailable &&
      artifactAvailable
    ) {
      return null;
    }
    return (
      <div className="run-detail-node-links">
        {toolCallItems.length > 0 || !toolCallAvailable ? renderRunDetailLinkGroup("Tool calls", "No tool-call audit records", toolCallItems) : null}
        {approvalItems.length > 0 ? renderRunDetailLinkGroup("Approvals", "No approval records", approvalItems) : null}
        {agentItems.length > 0 || !agentLinkAvailable ? renderRunDetailLinkGroup("Agents", "No agent session records", agentItems) : null}
        {artifactItems.length > 0 || !artifactAvailable ? renderRunDetailLinkGroup("Communication artifacts", "No communication artifact records", artifactItems) : null}
      </div>
    );
  }

  function renderRunSearchResult(run: WorkflowRunSearchItem) {
    const stateClass = workflowRunStateClassName(run.run_state, run.delivery_state);
    const sessionLabel = run.has_agent_session
      ? `Session ${run.session_ids.slice(0, 2).join(", ")}${run.session_ids.length > 2 ? ` +${run.session_ids.length - 2}` : ""}`
      : "No agent session";
    return (
      <div key={run.event_id} className="runtime-run-card run-search-card">
        <div className="runtime-run-header">
          <div className="runtime-run-event">{run.event_id}</div>
          <div className={`runtime-run-state state-${stateClass}`}>{workflowRunStateLabel(run.run_state, run.delivery_state)}</div>
        </div>
        <div className="runtime-run-meta">
          Latest {formatRuntimeTimestamp(run.latest_activity_at ?? run.occurred_at)}
        </div>
        <div className="runtime-run-meta">
          {sessionLabel}
          {run.trace_id ? ` - Trace ${run.trace_id}` : ""}
        </div>
        <div className="runtime-run-meta">
          Matched {run.matched_node_ids.join(", ") || "None"} - {summarizeRunSearchStatuses(run.status_counts)}
        </div>
        {run.error ? <div className="runtime-run-error">{run.error}</div> : null}
        <div className="runtime-run-nodes">
          {run.nodes.slice(0, 6).map((activity, index) => (
            <div key={`${run.event_id}-${activity.node_id}-${activity.status}-${index}`} className="runtime-run-node">
              {summarizeRunNode(activity)}
            </div>
          ))}
          {run.nodes.length > 6 ? (
            <div className="runtime-run-node">+{run.nodes.length - 6} more</div>
          ) : null}
        </div>
        <div className="runtime-run-actions">
                      <button
                        type="button"
                        className="subtle-button"
                        onClick={() => inspectWorkflowRun(run.event_id)}
                        disabled={runDetailLoading || workflowRunReadLocked}
                      >
                        Inspect
                      </button>
        </div>
      </div>
    );
  }

  const createEntriesByKind = NODE_GROUPS.map((group) => {
    const entries = activeWorkflowCatalog.nodes
      .filter((entry) => entry.kind === group.kind)
      .sort(compareWorkflowNodeCatalogEntries);
    return {
      ...group,
      entries,
      subgroups: groupWorkflowNodeCatalogEntries(entries, group.kind),
    };
  });
  const createEntryCount = createEntriesByKind.reduce((total, group) => total + group.entries.length, 0);
  const appShellStyle = {
    "--workflow-sidebar-width": `${sidebarWidth}px`,
  } as CSSProperties;
  const canvasRect = canvasShellRef.current?.getBoundingClientRect();
  const tutorialBubblePositionValue = isTutorialMode && activeWorkflowTutorialStep && canvasRect
    ? tutorialBubblePosition(
        activeWorkflowTutorialStep,
        displayedFlowNodes,
        canvasViewport,
        tutorialBubbleSize,
        { width: canvasRect.width, height: canvasRect.height },
      )
    : null;
  const tutorialBubbleStyle: CSSProperties | undefined = tutorialBubblePositionValue
    ? {
        left: `${tutorialBubblePositionValue.left}px`,
        top: `${tutorialBubblePositionValue.top}px`,
      }
    : undefined;
  const inspectorConfigurationFields = inspectorFields.filter((field) => field.key !== "outputs");
  const inspectorOutputFields = inspectorFields.filter((field) => field.key === "outputs");

  function renderCompactToolbar() {
    const modeButton = (mode: CompactCanvasMode, label: string) => (
      <button
        type="button"
        className={`compact-workflow-toolbar-button${compactCanvasMode === mode ? " active" : ""}`}
        aria-pressed={compactCanvasMode === mode}
        onClick={() => selectCompactCanvasMode(mode)}
      >
        {label}
      </button>
    );
    const panelButton = (panel: Exclude<CompactActivePanel, null>, label: string, disabled = false) => (
      <button
        type="button"
        className={`compact-workflow-toolbar-button${compactActivePanel === panel ? " active" : ""}${panel === "save" && hasUnsavedWorkflowChanges ? " dirty" : ""}`}
        aria-pressed={compactActivePanel === panel}
        disabled={disabled}
        onClick={() => toggleCompactPanel(panel)}
      >
        {label}
      </button>
    );

    return (
      <div className="compact-workflow-toolbar" aria-label="Workflow tools">
        <button
          type="button"
          className="compact-workflow-toolbar-button"
          onClick={() => {
            fitWorkflowCanvas();
            setCompactActivePanel(null);
          }}
          disabled={!flowInstance}
        >
          Fit
        </button>
        {modeButton("pan", "Pan")}
        {modeButton("select", "Select")}
        {modeButton("connect", "Connect")}
        <button
          type="button"
          className="compact-workflow-toolbar-button"
          onClick={undoWorkflowChange}
          disabled={!canUndoWorkflowChanges}
        >
          Undo
        </button>
        {panelButton("create", "Create", workflowEditLocked)}
        {panelButton("inspector", "Inspector")}
        {panelButton("runs", "Runs", workflowRunReadLocked)}
        {panelButton("graphs", "Graphs")}
        {panelButton("save", "Save")}
      </div>
    );
  }

  function renderCreatePanelBody(compact = false) {
    const normalizedSearch = compactCreateSearch.trim().toLowerCase();
    const groups = createEntriesByKind
      .map((group) => {
        const entries = normalizedSearch
          ? group.entries.filter((entry) => {
              const haystack = [
                entry.label,
                entry.type,
                entry.kind,
                nodeTypeDescription(entry),
              ].join(" ").toLowerCase();
              return haystack.includes(normalizedSearch);
            })
          : group.entries;
        return {
          ...group,
          entries,
          subgroups: groupWorkflowNodeCatalogEntries(entries, group.kind),
        };
      })
      .filter((group) => !compact || group.entries.length > 0);

    return (
      <>
        {compact ? (
          <label className="compact-workflow-search">
            <span>Search nodes</span>
            <input
              value={compactCreateSearch}
              onChange={(event) => setCompactCreateSearch(event.target.value)}
              placeholder="Trigger, gate, effect, query"
            />
          </label>
        ) : null}
        {compact && groups.length === 0 ? (
          <div className="empty-state">No matching nodes.</div>
        ) : null}
        {groups.map((group) => {
          const hasTutorialAllowedNodeType = group.entries.some((entry) =>
            tutorialAllowsNodeType(entry.kind, entry.type),
          );
          return (
            <details
              key={group.kind}
              className="create-group"
              open={compact || hasTutorialAllowedNodeType}
            >
              <summary className="create-group-summary">
                <Tooltip content={group.description}>
                  <span>{group.label}</span>
                </Tooltip>
                <span className="create-group-count">{group.entries.length}</span>
              </summary>
              <p className="create-group-description">{group.description}</p>
              <div className="create-subgroups">
                {group.subgroups.map((subgroup) => {
                  const subgroupHasTutorialAllowedNodeType = subgroup.entries.some((entry) =>
                    tutorialAllowsNodeType(entry.kind, entry.type),
                  );
                  return (
                    <details
                      key={subgroup.key}
                      className="create-subgroup"
                      open={compact || subgroupHasTutorialAllowedNodeType || undefined}
                    >
                      <summary className="create-subgroup-summary">
                        <Tooltip content={subgroup.description}>
                          <span>{subgroup.label}</span>
                        </Tooltip>
                        <span className="create-subgroup-count">{subgroup.entries.length}</span>
                      </summary>
                      <div className="create-group-actions create-subgroup-actions">
                        {subgroup.entries.map((entry) => (
                          <Tooltip key={entry.type} content={nodeTypeDescription(entry)} inline={false}>
                            <button
                              type="button"
                              draggable={!workflowEditLocked || tutorialAllowsNodeType(entry.kind, entry.type)}
                              onDragStart={(event) => startNodeTypeDrag(event, entry)}
                              onClick={() => {
                                addNode(entry.kind, entry.type);
                                if (compact) setCompactActivePanel(null);
                              }}
                              disabled={workflowEditLocked && !tutorialAllowsNodeType(entry.kind, entry.type)}
                            >
                              {entry.label}
                            </button>
                          </Tooltip>
                        ))}
                      </div>
                    </details>
                  );
                })}
              </div>
            </details>
          );
        })}
      </>
    );
  }

  function renderWorkflowPackIdentity() {
    if (!document) return null;
    const packActionsDisabled = workflowEditLocked || isTutorialMode || workflowPackSubmitting;
    const canRemoveSelectedPack = Boolean(selectedWorkflowPack && workflowPackOptions.length > 1);
    if (workflowPackOptions.length === 0) {
      return (
        <div className="workflow-pack-identity">
          <div className="workflow-pack-selected">
            <span>Workflow source</span>
            <strong>Inline config</strong>
          </div>
          <div className="workflow-pack-actions">
            <button type="button" onClick={openCreateWorkflowPackModal} disabled={packActionsDisabled}>
              Add pack
            </button>
          </div>
          {workflowPackError ? <div className="runtime-run-error">{workflowPackError}</div> : null}
        </div>
      );
    }
    return (
      <div className="workflow-pack-identity">
        <div className="workflow-pack-header">
          <div>
            <span className="section-label">Workflow packs</span>
            <strong>{workflowPackOptions.length}</strong>
          </div>
          <div className="workflow-pack-actions">
            <button type="button" onClick={openCreateWorkflowPackModal} disabled={packActionsDisabled}>
              Add
            </button>
            <button
              type="button"
              onClick={openEditWorkflowPackModal}
              disabled={packActionsDisabled || !selectedWorkflowPack?.editable}
            >
              Edit
            </button>
            <button
              type="button"
              className="danger"
              onClick={() => void removeSelectedWorkflowPack()}
              disabled={packActionsDisabled || !canRemoveSelectedPack}
            >
              Remove
            </button>
          </div>
        </div>
        {workflowPackOptions.length > 1 ? (
          <label className="workflow-pack-selector">
            <span>Workflow pack</span>
            <select
              value={selectedWorkflowPackReference}
              onChange={(event) => void handleWorkflowPackChange(event)}
              disabled={workflowReadLocked || isTutorialMode}
            >
              {workflowPackOptions.map((pack) => (
                <option key={pack.reference} value={pack.reference}>
                  {pack.display_name || pack.reference}
                </option>
              ))}
            </select>
          </label>
        ) : (
          <div className="workflow-pack-selected">
            <span>Workflow pack</span>
            <strong>{selectedWorkflowPack?.display_name || selectedWorkflowPackReference}</strong>
          </div>
        )}
        {selectedWorkflowPack ? (
          <div className="path-note workflow-pack-paths">
            <div>{selectedWorkflowPack.path}</div>
            <div>{selectedWorkflowPack.layout_path}</div>
          </div>
        ) : null}
        <div className="workflow-pack-list" aria-label="Configured workflow packs">
          {workflowPackOptions.map((pack) => (
            <button
              key={pack.reference}
              type="button"
              className={`workflow-pack-list-row${pack.reference === selectedWorkflowPackReference ? " selected" : ""}${pack.exists ? "" : " missing"}`}
              onClick={() => {
                if (pack.reference !== selectedWorkflowPackReference) {
                  void switchWorkflowPack(pack.reference);
                }
              }}
              disabled={workflowReadLocked || isTutorialMode}
            >
              <span>
                <strong>{pack.display_name || pack.reference}</strong>
                <small>{pack.reference}</small>
              </span>
              <span className="workflow-pack-row-meta">
                {pack.exists ? `${pack.graph_count} graphs / ${pack.node_count} nodes` : "Missing file"}
              </span>
            </button>
          ))}
        </div>
        {workflowPackError ? <div className="runtime-run-error">{workflowPackError}</div> : null}
      </div>
    );
  }

  function renderSavePanelContent() {
    return (
      <div className="sidebar-section compact-panel-section save-panel-section">
        <div className="section-label">Save</div>
        {renderWorkflowPackIdentity()}
        <button onClick={() => void validateDocument()} disabled={workflowReadLocked}>
          Validate
        </button>
        <button onClick={undoWorkflowChange} disabled={!canUndoWorkflowChanges}>
          Undo
        </button>
        <button className="primary" onClick={() => void saveDocument()} disabled={workflowEditLocked}>
          {saveState === "saving" ? "Saving..." : "Save workflow"}
        </button>
        <div className={`save-status save-status-${savePanelStatusTone}`} role="status" aria-live="polite">
          <strong>
            {hasUnsavedWorkflowChanges
              ? "Unsaved workflow changes"
              : saveState === "saved"
                ? "Workflow saved"
                : "No unsaved workflow changes"}
          </strong>
          {hasUnsavedWorkflowChanges && dirtyNodeSummary ? <span>{dirtyNodeSummary}</span> : null}
        </div>
        {error ? <div className="error-box save-error-box">{error}</div> : null}
        {document ? (
          <div className="path-note">
            <div>{document.config_path}</div>
            <div>{document.layout_path}</div>
          </div>
        ) : null}
      </div>
    );
  }

  function renderGraphsPanelContent() {
    return (
      <div className="sidebar-section compact-panel-section workflow-graphs-section">
        <div className="collapsible-sidebar-summary compact-section-heading">
          <span className="section-label">Graphs</span>
          <span className="collapsible-sidebar-meta">
            {visibleWorkflowGraphs.length} {visibleWorkflowGraphs.length === 1 ? "graph" : "graphs"}
          </span>
        </div>
        <p className="runtime-summary">{graphViewContext}</p>
        <div className="workflow-graph-list">
          {visibleWorkflowGraphs.length === 0 ? (
            <p className="runtime-summary">No graphs associated with this directory.</p>
          ) : (
            visibleWorkflowGraphs.map((graph) => (
              <button
                type="button"
                key={graph.id}
                className={`workflow-graph-item${selectedGraphId === graph.id ? " selected" : ""}`}
                onClick={() => selectWorkflowGraph(graph)}
                disabled={workflowEditLocked}
              >
                <span className="workflow-graph-name">{graph.name}</span>
                <span className="workflow-graph-meta">
                  {graph.node_ids.length} {graph.node_ids.length === 1 ? "node" : "nodes"}
                </span>
              </button>
            ))
          )}
        </div>
        {selectedGraph ? (
          <div className="workflow-graph-editor">
            <label className="inspector-label">
              <span>Graph name</span>
              <input
                value={graphNameDraft}
                onChange={(event) => setGraphNameDraft(event.target.value)}
                disabled={workflowEditLocked}
              />
            </label>
            <label className="inspector-label">
              <span>Description</span>
              <textarea
                value={graphDescriptionDraft}
                rows={3}
                onChange={(event) => setGraphDescriptionDraft(event.target.value)}
                disabled={workflowEditLocked}
              />
            </label>
            <label className="inspector-label">
              <span>Directory path</span>
              <p className="field-description">
                Single canonical directory scope for this graph. If omitted in older data, the editor falls back to the selected directory or root.
              </p>
              <input
                value={graphDirectoryDraft}
                placeholder={selectedDirectoryPath}
                onChange={(event) => setGraphDirectoryDraft(event.target.value)}
                disabled={workflowEditLocked}
              />
            </label>
            <label className="inspector-label">
              <span>User IDs</span>
              <p className="field-description">
                Ownership and audit metadata only. These IDs do not grant visibility or change who can run the graph.
              </p>
              <textarea
                value={graphUsersDraft}
                rows={3}
                onChange={(event) => setGraphUsersDraft(event.target.value)}
                disabled={workflowEditLocked}
              />
            </label>
            <button
              type="button"
              className="primary"
              onClick={applyWorkflowGraphChanges}
              disabled={workflowEditLocked}
            >
              Apply graph details
            </button>
          </div>
        ) : null}
      </div>
    );
  }

  function renderRunsPanelContent() {
    if (!canReadWorkflowRuns) {
      return (
        <div className="sidebar-section compact-panel-section">
          <div className="section-label">Runs</div>
          <p className="runtime-summary">Permission required: workflow_runs.read.</p>
        </div>
      );
    }

    return (
      <div className="sidebar-section compact-panel-section current-runs-section">
        <div className="current-runs-summary compact-section-heading">
          <span className="section-label current-runs-label">Current runs</span>
          <span className="current-runs-count">{visibleRuns.length}</span>
        </div>
        <p className="runtime-summary">
          {visibleRuns.length === 0
            ? "No active or errored workflow runs."
            : `${visibleRuns.length} ${visibleRuns.length === 1 ? "run" : "runs"} visible on the graph.`}
        </p>
        {runSnapshotError ? <div className="runtime-run-error">{runSnapshotError}</div> : null}
        <form className="run-detail-lookup" onSubmit={submitWorkflowRunDetailLookup}>
          <select
            value={runDetailLookupKind}
            onChange={(event) => setRunDetailLookupKind(event.target.value as WorkflowRunDetailLookupKind)}
            aria-label="Run lookup key"
          >
            <option value="auto">Auto</option>
            <option value="run_id">Run ID</option>
            <option value="event_id">Event ID</option>
            <option value="trigger_event_id">Trigger event</option>
            <option value="trace_id">Trace ID</option>
            <option value="session_id">Session ID</option>
            <option value="artifact_id">Artifact ID</option>
          </select>
          <input
            value={runDetailLookup}
            onChange={(event) => setRunDetailLookup(event.target.value)}
            placeholder="Lookup id"
            aria-label="Workflow run lookup id"
          />
          <button
            type="submit"
            className="subtle-button"
            disabled={runDetailLoading || !runDetailLookup.trim() || workflowRunReadLocked}
          >
            {runDetailLoading ? "Loading..." : "Inspect"}
          </button>
        </form>
        {runDetailError ? <div className="runtime-run-error">{runDetailError}</div> : null}
        <div className="run-search-panel">
          <div className="run-search-header">
            <span className="section-label">Latest runs</span>
            {runSearchResult ? (
              <span className="current-runs-count">{runSearchResult.returned}</span>
            ) : null}
          </div>
          <form className="run-search-form" onSubmit={submitWorkflowRunSearch}>
            <select
              value={runSearchScope}
              onChange={(event) => setRunSearchScope(event.target.value)}
              aria-label="Run search scope"
            >
              <optgroup label="Graphs">
                {runSearchScopeOptions
                  .filter((option) => option.kind === "graph")
                  .map((option) => (
                    <option value={option.value} key={option.value}>
                      {option.label}
                    </option>
                  ))}
              </optgroup>
              <optgroup label="Nodes">
                {runSearchScopeOptions
                  .filter((option) => option.kind === "node")
                  .map((option) => (
                    <option value={option.value} key={option.value}>
                      {option.label}
                    </option>
                  ))}
              </optgroup>
            </select>
            <label className="checkbox-row run-search-checkbox">
              <input
                type="checkbox"
                checked={runSearchWithoutSession}
                onChange={(event) => setRunSearchWithoutSession(event.target.checked)}
              />
              <span>No agent session</span>
            </label>
            <label className="checkbox-row run-search-checkbox">
              <input
                type="checkbox"
                checked={runSearchIncludeCompleted}
                onChange={(event) => setRunSearchIncludeCompleted(event.target.checked)}
              />
              <span>Completed</span>
            </label>
            <button
              type="submit"
              className="subtle-button"
              disabled={runSearchLoading || !selectedRunSearchScope || workflowRunReadLocked}
            >
              {runSearchLoading ? "Loading..." : "Find"}
            </button>
          </form>
          {runSearchError ? <div className="runtime-run-error">{runSearchError}</div> : null}
          {runSearchResult ? (
            <div className="runtime-run-meta">
              {runSearchResult.matched} matched {runSearchResult.filters.graph_name ?? selectedRunSearchScope?.label ?? ""}
              {runSearchResult.has_more ? `; showing ${runSearchResult.returned}` : ""}
            </div>
          ) : null}
          {runSearchResult && runSearchResult.runs.length === 0 ? (
            <div className="runtime-run-meta">No matching runs.</div>
          ) : null}
          {runSearchResult && runSearchResult.runs.length > 0 ? (
            <div className="runtime-runs run-search-results">
              {runSearchResult.runs.map((run) => renderRunSearchResult(run))}
            </div>
          ) : null}
        </div>
        <div className="runtime-runs">
          {visibleRuns.map((run: WorkflowRunSummary) => (
            <div key={run.event_id} className="runtime-run-card">
              <div className="runtime-run-header">
                <div className="runtime-run-event">{run.event_id}</div>
                <div
                  className={`runtime-run-state state-${workflowRunStateClassName(run.run_state, run.delivery_state)}`}
                >
                  {workflowRunStateLabel(run.run_state, run.delivery_state)}
                </div>
              </div>
              <div className="runtime-run-meta">
                Last activity {formatRuntimeTimestamp(run.latest_activity_at ?? run.occurred_at)}
              </div>
              {run.run_state === "errored" ? (
                <div className="runtime-run-error">
                  Failed at <strong>{run.failed_node_id ?? "unknown node"}</strong>
                  {run.error ? `: ${run.error}` : ""}
                </div>
              ) : null}
              <div className="runtime-run-nodes">
                {run.active_nodes.map((activity, index) => (
                  <div key={`${run.event_id}-${activity.node_id}-${activity.status}-${index}`} className="runtime-run-node">
                    {summarizeRunNode(activity)}
                  </div>
                ))}
              </div>
              <div className="runtime-run-actions">
                <button
                  type="button"
                  className="subtle-button"
                  onClick={() => inspectWorkflowRun(run.event_id)}
                  disabled={runDetailLoading || workflowRunReadLocked}
                >
                  Inspect
                </button>
                {run.run_state === "errored" && run.resumable ? (
                  <button
                    type="button"
                    className="subtle-button"
                    onClick={() => void resumeWorkflowRun(run.event_id)}
                    disabled={Boolean(resumingRunIds[run.event_id]) || workflowRunControlLocked}
                  >
                    {resumingRunIds[run.event_id] ? "Resuming..." : "Resume from failure"}
                  </button>
                ) : null}
                <button
                  type="button"
                  className="subtle-button runtime-run-clear"
                  onClick={() => void clearWorkflowRun(run.event_id, run.run_state)}
                  disabled={Boolean(clearingRunIds[run.event_id]) || workflowRunControlLocked}
                >
                  {clearingRunIds[run.event_id]
                    ? run.run_state === "errored"
                      ? "Dismissing..."
                      : "Clearing..."
                    : run.run_state === "errored"
                      ? "Dismiss run"
                      : "Clear run"}
                </button>
              </div>
            </div>
          ))}
        </div>
      </div>
    );
  }

  function renderSelectionInspectorPanel() {
    if (selectedNodeIds.length <= 1) return null;
    return (
      <div className="sidebar-section compact-panel-section inspector">
        <div className="section-label">Selection</div>
        <div className="inspector-summary">
          <div>{selectedNodeIds.length} nodes selected</div>
        </div>
        <button onClick={() => setProgrammaticSelection([])} disabled={workflowEditLocked}>
          Clear selection
        </button>
        <button onClick={selectDownstreamBranch} disabled={workflowEditLocked || !selectedNodeId}>
          Select downstream branch
        </button>
        <button className="danger" onClick={deleteSelectedNode} disabled={workflowEditLocked}>
          Delete selected nodes
        </button>
      </div>
    );
  }

  function renderEditableNodeInspectorPanel() {
    if (!selectedNode) return null;
    return (
      <div className="sidebar-section compact-panel-section inspector">
        <div className="section-label">Inspector</div>
        <div className="inspector-summary">
          <div>{selectedNode.type}</div>
          <div>{selectedNode.id}</div>
        </div>
        {selectedNodeDiagnostics.length > 0 ? (
          <div className="problems-panel">
            <div className="problems-heading">
              <span>Problems</span>
              <span>{selectedNodeDiagnostics.length}</span>
            </div>
            <div className="problems-list">
              {selectedNodeDiagnostics.map((diagnostic, index) => (
                <div className="problem-item" key={`${diagnostic.code}-${diagnostic.field_path ?? ""}-${diagnostic.reference ?? ""}-${index}`}>
                  <div className="problem-message">{diagnostic.message}</div>
                  <div className="problem-meta">
                    {diagnostic.field_path ? <span>{diagnostic.field_path}</span> : null}
                    {diagnostic.reference ? <span>{diagnostic.reference}</span> : null}
                  </div>
                </div>
              ))}
            </div>
          </div>
        ) : null}
        <button onClick={selectDownstreamBranch} disabled={workflowEditLocked}>
          Select downstream branch
        </button>
        {renderSelectedTriggerToggle()}
        <div className="inspector-metadata-field">
          <span>Node ID</span>
          <code>{selectedNode.id}</code>
        </div>
        <label className="inspector-label">
          <span>Display name</span>
          <input
            value={inspectorDisplayName}
            onChange={(event) => {
              const nextValue = event.target.value;
              setInspectorDisplayName(nextValue);
              stageInspectorDraft({ displayName: nextValue });
            }}
            disabled={workflowEditLocked}
          />
        </label>

        {renderEffectTargetField()}
        {inspectorConfigurationFields.map((field) => renderField(field))}

        {Object.keys(inspectorUnknownValues).length > 0 ? (
          <details className="raw-json-panel">
            <summary>Additional raw JSON</summary>
            <p className="field-description">These keys are preserved because they are not in the current catalog.</p>
            <textarea value={rawJsonDraft} rows={8} readOnly />
          </details>
        ) : null}

        {inspectorOutputFields.map((field) => renderField(field))}

        {renderSelectedNodeOutputContract()}

        {selectedNodeHasUnsavedChanges ? (
          <button className="primary" onClick={resetSelectedNodeChanges} disabled={workflowEditLocked}>
            Reset changes
          </button>
        ) : null}

        <button className="danger" onClick={deleteSelectedNode} disabled={workflowEditLocked}>
          Delete node
        </button>
      </div>
    );
  }

  function renderEditableEdgeInspectorPanel() {
    if (!selectedEdge) return null;
    return (
      <div className="sidebar-section compact-panel-section inspector">
        <div className="section-label">Edge</div>
        <div className="inspector-summary">
          <div>{selectedEdge.source_id}</div>
          <div>Route: {selectedEdgeRouteLabel}</div>
          <div>{selectedEdge.target_id}</div>
          {selectedEdge.allow_cycle ? <div>Cycle return</div> : null}
        </div>
        <button className="danger" onClick={deleteSelectedEdge} disabled={workflowEditLocked}>
          Delete edge
        </button>
      </div>
    );
  }

  function renderCompactInspectorPanelContent() {
    if (isInspectMode) {
      return (
        <>
          {renderInspectedRunPanel()}
          {renderInspectedNodePanel()}
          {runDetailError ? <div className="runtime-run-error">{runDetailError}</div> : null}
          {error ? <div className="error-box">{error}</div> : null}
        </>
      );
    }
    if (selectedNodeIds.length > 1) return renderSelectionInspectorPanel();
    if (selectedNode) return renderEditableNodeInspectorPanel();
    if (selectedEdge) return renderEditableEdgeInspectorPanel();
    return (
      <div className="sidebar-section compact-panel-section inspector">
        <div className="section-label">Inspector</div>
        <div className="inspector-summary">
          <div>No node or edge selected</div>
        </div>
      </div>
    );
  }

  function renderCompactBottomSheet() {
    if (!compactActivePanel) return null;
    const titleByPanel: Record<Exclude<CompactActivePanel, null>, string> = {
      create: "Create",
      inspector: "Inspector",
      runs: "Runs",
      graphs: "Graphs",
      save: "Save",
    };
    const contentByPanel: Record<Exclude<CompactActivePanel, null>, ReactNode> = {
      create: (
        <div className="sidebar-section compact-panel-section">
          <div className="compact-section-heading">
            <span className="section-label">Create</span>
            <span className="collapsible-sidebar-meta">
              {createEntryCount} {createEntryCount === 1 ? "node" : "nodes"}
            </span>
          </div>
          {renderCreatePanelBody(true)}
        </div>
      ),
      inspector: renderCompactInspectorPanelContent(),
      runs: renderRunsPanelContent(),
      graphs: renderGraphsPanelContent(),
      save: renderSavePanelContent(),
    };

    return (
      <div
        className="compact-workflow-sheet-backdrop"
        role="presentation"
        onClick={(event) => {
          if (event.target === event.currentTarget) {
            setCompactActivePanel(null);
          }
        }}
      >
        <section
          className={`compact-workflow-sheet panel-${compactActivePanel}`}
          role="dialog"
          aria-modal="false"
          aria-label={titleByPanel[compactActivePanel]}
        >
          <div className="compact-workflow-sheet-header">
            <div>
              <div className="section-label">Workflow</div>
              <h2>{titleByPanel[compactActivePanel]}</h2>
            </div>
            <button type="button" className="subtle-button" onClick={() => setCompactActivePanel(null)}>
              Close
            </button>
          </div>
          <div className="compact-workflow-sheet-body">
            {contentByPanel[compactActivePanel]}
          </div>
        </section>
      </div>
    );
  }

  return (
    <div className="admin-page workflow-embedded-page">
      <div
        className={`app-shell workflow-editor-shell${isResizingSidebar ? " is-resizing-sidebar" : ""}${isInspectMode ? " is-inspect-mode" : ""}${isCompactCanvasTouchMode ? ` compact-mode-${compactCanvasMode}` : ""}`}
        ref={appShellRef}
        style={appShellStyle}
        tabIndex={-1}
        onKeyDown={handleAppShellKeyDown}
      >
        <aside className="sidebar">
          {workflowGeneratorOpen ? (
            <>
              {renderWorkflowGeneratorPanel()}
              {selectedNode ? (
                <div className="sidebar-section inspector">
                  <div className="section-label">Inspector</div>
                  <div className="inspector-summary">
                    <div>{selectedNode.type}</div>
                    <div>{selectedNode.id}</div>
                  </div>
                  {selectedNodeDiagnostics.length > 0 ? (
                    <div className="problems-panel">
                      <div className="problems-heading">
                        <span>Problems</span>
                        <span>{selectedNodeDiagnostics.length}</span>
                      </div>
                      <div className="problems-list">
                        {selectedNodeDiagnostics.map((diagnostic, index) => (
                          <div className="problem-item" key={`${diagnostic.code}-${diagnostic.field_path ?? ""}-${diagnostic.reference ?? ""}-${index}`}>
                            <div className="problem-message">{diagnostic.message}</div>
                            <div className="problem-meta">
                              {diagnostic.field_path ? <span>{diagnostic.field_path}</span> : null}
                              {diagnostic.reference ? <span>{diagnostic.reference}</span> : null}
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  ) : null}
                  <button onClick={selectDownstreamBranch}>Select downstream branch</button>
                  {renderSelectedTriggerToggle()}
                  <div className="inspector-metadata-field">
                    <span>Node ID</span>
                    <code>{selectedNode.id}</code>
                  </div>
                  <label className="inspector-label">
                    <span>Display name</span>
                    <input
                      value={inspectorDisplayName}
                      onChange={(event) => {
                        const nextValue = event.target.value;
                        setInspectorDisplayName(nextValue);
                        stageInspectorDraft({ displayName: nextValue });
                      }}
                    />
                  </label>
                  {renderEffectTargetField()}
                  {inspectorConfigurationFields.map((field) => renderField(field))}
                  {inspectorOutputFields.map((field) => renderField(field))}
                  <button className="danger" onClick={deleteSelectedNode} disabled={workflowEditLocked}>
                    Delete node
                  </button>
                </div>
              ) : null}
              {selectedEdge ? (
                <div className="sidebar-section inspector">
                  <div className="section-label">Edge</div>
                  <div className="inspector-summary">
                    <div>{selectedEdge.source_id}</div>
                    <div>Route: {selectedEdgeRouteLabel}</div>
                    <div>{selectedEdge.target_id}</div>
                    {selectedEdge.allow_cycle ? <div>Cycle return</div> : null}
                  </div>
                  <button className="danger" onClick={deleteSelectedEdge} disabled={workflowEditLocked}>
                    Delete edge
                  </button>
                </div>
              ) : null}
            </>
          ) : isInspectMode ? (
            <>
              {renderWorkflowTutorialLauncher()}
              {renderInspectedRunPanel()}
              {renderInspectedNodePanel()}
              {runDetailError ? <div className="runtime-run-error">{runDetailError}</div> : null}
              {error ? <div className="error-box">{error}</div> : null}
            </>
          ) : (
            <>
              {renderWorkflowTutorialLauncher()}
              {editLocked ? <div className="callout warning-callout workflow-lock-callout">Unlock privileged operations to modify workflow state.</div> : null}

              <div className="workflow-generator-launcher">
                <button type="button" className="subtle-button" onClick={openWorkflowGenerator} disabled={workflowEditLocked}>
                  Generate workflow
                </button>
              </div>

              {canReadWorkflowRuns ? (
              <details className="sidebar-section current-runs-section">
                <summary className="current-runs-summary">
                  <span className="section-label current-runs-label">Current runs</span>
                  <span className="current-runs-count">{visibleRuns.length}</span>
                </summary>
                <p className="runtime-summary">
                  {visibleRuns.length === 0
                    ? "No active or errored workflow runs."
                    : `${visibleRuns.length} ${visibleRuns.length === 1 ? "run" : "runs"} visible on the graph.`}
                </p>
                {runSnapshotError ? <div className="runtime-run-error">{runSnapshotError}</div> : null}
                <form className="run-detail-lookup" onSubmit={submitWorkflowRunDetailLookup}>
                  <select
                    value={runDetailLookupKind}
                    onChange={(event) => setRunDetailLookupKind(event.target.value as WorkflowRunDetailLookupKind)}
                    aria-label="Run lookup key"
                  >
                    <option value="auto">Auto</option>
                    <option value="run_id">Run ID</option>
                    <option value="event_id">Event ID</option>
                    <option value="trigger_event_id">Trigger event</option>
                    <option value="trace_id">Trace ID</option>
                    <option value="session_id">Session ID</option>
                    <option value="artifact_id">Artifact ID</option>
                  </select>
                  <input
                    value={runDetailLookup}
                    onChange={(event) => setRunDetailLookup(event.target.value)}
                    placeholder="Lookup id"
                    aria-label="Workflow run lookup id"
                  />
                  <button
                    type="submit"
                    className="subtle-button"
                    disabled={runDetailLoading || !runDetailLookup.trim() || workflowRunReadLocked}
                  >
                    {runDetailLoading ? "Loading..." : "Inspect"}
                  </button>
                </form>
                {runDetailError ? <div className="runtime-run-error">{runDetailError}</div> : null}
                <div className="run-search-panel">
                  <div className="run-search-header">
                    <span className="section-label">Latest runs</span>
                    {runSearchResult ? (
                      <span className="current-runs-count">{runSearchResult.returned}</span>
                    ) : null}
                  </div>
                  <form className="run-search-form" onSubmit={submitWorkflowRunSearch}>
                    <select
                      value={runSearchScope}
                      onChange={(event) => setRunSearchScope(event.target.value)}
                      aria-label="Run search scope"
                    >
                      <optgroup label="Graphs">
                        {runSearchScopeOptions
                          .filter((option) => option.kind === "graph")
                          .map((option) => (
                            <option value={option.value} key={option.value}>
                              {option.label}
                            </option>
                          ))}
                      </optgroup>
                      <optgroup label="Nodes">
                        {runSearchScopeOptions
                          .filter((option) => option.kind === "node")
                          .map((option) => (
                            <option value={option.value} key={option.value}>
                              {option.label}
                            </option>
                          ))}
                      </optgroup>
                    </select>
                    <label className="checkbox-row run-search-checkbox">
                      <input
                        type="checkbox"
                        checked={runSearchWithoutSession}
                        onChange={(event) => setRunSearchWithoutSession(event.target.checked)}
                      />
                      <span>No agent session</span>
                    </label>
                    <label className="checkbox-row run-search-checkbox">
                      <input
                        type="checkbox"
                        checked={runSearchIncludeCompleted}
                        onChange={(event) => setRunSearchIncludeCompleted(event.target.checked)}
                      />
                      <span>Completed</span>
                    </label>
                    <button
                      type="submit"
                      className="subtle-button"
                      disabled={runSearchLoading || !selectedRunSearchScope || workflowRunReadLocked}
                    >
                      {runSearchLoading ? "Loading..." : "Find"}
                    </button>
                  </form>
                  {runSearchError ? <div className="runtime-run-error">{runSearchError}</div> : null}
                  {runSearchResult ? (
                    <div className="runtime-run-meta">
                      {runSearchResult.matched} matched {runSearchResult.filters.graph_name ?? selectedRunSearchScope?.label ?? ""}
                      {runSearchResult.has_more ? `; showing ${runSearchResult.returned}` : ""}
                    </div>
                  ) : null}
                  {runSearchResult && runSearchResult.runs.length === 0 ? (
                    <div className="runtime-run-meta">No matching runs.</div>
                  ) : null}
                  {runSearchResult && runSearchResult.runs.length > 0 ? (
                    <div className="runtime-runs run-search-results">
                      {runSearchResult.runs.map((run) => renderRunSearchResult(run))}
                    </div>
                  ) : null}
                </div>
                <div className="runtime-runs">
                  {visibleRuns.map((run: WorkflowRunSummary) => (
                    <div key={run.event_id} className="runtime-run-card">
                <div className="runtime-run-header">
                  <div className="runtime-run-event">{run.event_id}</div>
                        <div
                          className={`runtime-run-state state-${workflowRunStateClassName(run.run_state, run.delivery_state)}`}
                        >
                          {workflowRunStateLabel(run.run_state, run.delivery_state)}
                        </div>
                      </div>
                      <div className="runtime-run-meta">
                        Last activity {formatRuntimeTimestamp(run.latest_activity_at ?? run.occurred_at)}
                      </div>
                      {run.run_state === "errored" ? (
                        <div className="runtime-run-error">
                          Failed at <strong>{run.failed_node_id ?? "unknown node"}</strong>
                          {run.error ? `: ${run.error}` : ""}
                        </div>
                      ) : null}
                      <div className="runtime-run-nodes">
                        {run.active_nodes.map((activity, index) => (
                          <div key={`${run.event_id}-${activity.node_id}-${activity.status}-${index}`} className="runtime-run-node">
                            {summarizeRunNode(activity)}
                          </div>
                        ))}
                      </div>
                      <div className="runtime-run-actions">
                        <button
                          type="button"
                          className="subtle-button"
                          onClick={() => inspectWorkflowRun(run.event_id)}
                          disabled={runDetailLoading || workflowRunReadLocked}
                        >
                          Inspect
                        </button>
                        {run.run_state === "errored" && run.resumable ? (
                          <button
                            type="button"
                            className="subtle-button"
                            onClick={() => void resumeWorkflowRun(run.event_id)}
                            disabled={Boolean(resumingRunIds[run.event_id]) || workflowRunControlLocked}
                          >
                            {resumingRunIds[run.event_id] ? "Resuming..." : "Resume from failure"}
                          </button>
                        ) : null}
                        <button
                          type="button"
                          className="subtle-button runtime-run-clear"
                          onClick={() => void clearWorkflowRun(run.event_id, run.run_state)}
                          disabled={Boolean(clearingRunIds[run.event_id]) || workflowRunControlLocked}
                        >
                          {clearingRunIds[run.event_id]
                            ? run.run_state === "errored"
                              ? "Dismissing..."
                              : "Clearing..."
                            : run.run_state === "errored"
                              ? "Dismiss run"
                              : "Clear run"}
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              </details>
              ) : null}

            <details className="sidebar-section workflow-graphs-section collapsible-sidebar-section">
              <summary className="collapsible-sidebar-summary">
                <span className="section-label">Graphs</span>
                <span className="collapsible-sidebar-meta">
                  {visibleWorkflowGraphs.length} {visibleWorkflowGraphs.length === 1 ? "graph" : "graphs"}
                </span>
              </summary>
              <p className="runtime-summary">{graphViewContext}</p>
              <div className="workflow-graph-list">
                {visibleWorkflowGraphs.length === 0 ? (
                  <p className="runtime-summary">No graphs associated with this directory.</p>
                ) : (
                  visibleWorkflowGraphs.map((graph) => (
                    <button
                      type="button"
                      key={graph.id}
                      className={`workflow-graph-item${selectedGraphId === graph.id ? " selected" : ""}`}
                      onClick={() => selectWorkflowGraph(graph)}
                      disabled={workflowEditLocked}
                    >
                      <span className="workflow-graph-name">{graph.name}</span>
                      <span className="workflow-graph-meta">
                        {graph.node_ids.length} {graph.node_ids.length === 1 ? "node" : "nodes"}
                      </span>
                    </button>
                  ))
                )}
              </div>
              {selectedGraph ? (
                <div className="workflow-graph-editor">
                  <label className="inspector-label">
                    <span>Graph name</span>
                    <input
                      value={graphNameDraft}
                      onChange={(event) => setGraphNameDraft(event.target.value)}
                      disabled={workflowEditLocked}
                    />
                  </label>
                  <label className="inspector-label">
                    <span>Description</span>
                    <textarea
                      value={graphDescriptionDraft}
                      rows={3}
                      onChange={(event) => setGraphDescriptionDraft(event.target.value)}
                      disabled={workflowEditLocked}
                    />
                  </label>
                  <label className="inspector-label">
                    <span>Directory path</span>
                    <p className="field-description">
                      Single canonical directory scope for this graph. If omitted in older data, the editor falls back to the selected directory or root.
                    </p>
                    <input
                      value={graphDirectoryDraft}
                      placeholder={selectedDirectoryPath}
                      onChange={(event) => setGraphDirectoryDraft(event.target.value)}
                      disabled={workflowEditLocked}
                    />
                  </label>
                  <label className="inspector-label">
                    <span>User IDs</span>
                    <p className="field-description">
                      Ownership and audit metadata only. These IDs do not grant visibility or change who can run the graph.
                    </p>
                    <textarea
                      value={graphUsersDraft}
                      rows={3}
                      onChange={(event) => setGraphUsersDraft(event.target.value)}
                      disabled={workflowEditLocked}
                    />
                  </label>
                  <button
                    type="button"
                    className="primary"
                    onClick={applyWorkflowGraphChanges}
                    disabled={workflowEditLocked}
                  >
                    Apply graph details
                  </button>
                </div>
              ) : null}
            </details>

            <details
              className="sidebar-section collapsible-sidebar-section"
              open={isTutorialMode && tutorialCurrentRequiredAction()?.kind === "add-node"}
            >
              <summary className="collapsible-sidebar-summary">
                <span className="section-label">Create</span>
                <span className="collapsible-sidebar-meta">
                  {createEntryCount} {createEntryCount === 1 ? "node" : "nodes"}
                </span>
              </summary>
              {createEntriesByKind.map((group) => {
                const hasTutorialAllowedNodeType = group.entries.some((entry) =>
                  tutorialAllowsNodeType(entry.kind, entry.type),
                );
                return (
                  <details
                    key={group.kind}
                    className="create-group"
                    open={hasTutorialAllowedNodeType}
                  >
                    <summary className="create-group-summary">
                      <Tooltip content={group.description}>
                        <span>{group.label}</span>
                      </Tooltip>
                      <span className="create-group-count">{group.entries.length}</span>
                    </summary>
                    <p className="create-group-description">{group.description}</p>
                    <div className="create-subgroups">
                      {group.subgroups.map((subgroup) => {
                        const subgroupHasTutorialAllowedNodeType = subgroup.entries.some((entry) =>
                          tutorialAllowsNodeType(entry.kind, entry.type),
                        );
                        return (
                          <details
                            key={subgroup.key}
                            className="create-subgroup"
                            open={subgroupHasTutorialAllowedNodeType || undefined}
                          >
                            <summary className="create-subgroup-summary">
                              <Tooltip content={subgroup.description}>
                                <span>{subgroup.label}</span>
                              </Tooltip>
                              <span className="create-subgroup-count">{subgroup.entries.length}</span>
                            </summary>
                            <div className="create-group-actions create-subgroup-actions">
                              {subgroup.entries.map((entry) => (
                                <Tooltip key={entry.type} content={nodeTypeDescription(entry)} inline={false}>
                                  <button
                                    type="button"
                                    draggable={!workflowEditLocked || tutorialAllowsNodeType(entry.kind, entry.type)}
                                    onDragStart={(event) => startNodeTypeDrag(event, entry)}
                                    onClick={() => addNode(entry.kind, entry.type)}
                                    disabled={workflowEditLocked && !tutorialAllowsNodeType(entry.kind, entry.type)}
                                  >
                                    {entry.label}
                                  </button>
                                </Tooltip>
                              ))}
                            </div>
                          </details>
                        );
                      })}
                    </div>
                  </details>
                );
              })}
            </details>

            <div className="sidebar-section">
              <div className="section-label">Save</div>
              {renderWorkflowPackIdentity()}
              <button onClick={() => void validateDocument()} disabled={workflowReadLocked}>
                Validate
              </button>
              <button onClick={undoWorkflowChange} disabled={!canUndoWorkflowChanges}>
                Undo
              </button>
              <button className="primary" onClick={() => void saveDocument()} disabled={workflowEditLocked}>
                {saveState === "saving" ? "Saving..." : "Save workflow"}
              </button>
              <div className={`save-status save-status-${savePanelStatusTone}`} role="status" aria-live="polite">
                <strong>
                  {hasUnsavedWorkflowChanges
                    ? "Unsaved workflow changes"
                    : saveState === "saved"
                      ? "Workflow saved"
                      : "No unsaved workflow changes"}
                </strong>
                {hasUnsavedWorkflowChanges && dirtyNodeSummary ? <span>{dirtyNodeSummary}</span> : null}
              </div>
              {error ? <div className="error-box save-error-box">{error}</div> : null}
              {document ? (
                <div className="path-note">
                  <div>{document.config_path}</div>
                  <div>{document.layout_path}</div>
                </div>
              ) : null}
            </div>

            {selectedNodeIds.length > 1 ? (
              <div className="sidebar-section inspector">
                <div className="section-label">Selection</div>
                <div className="inspector-summary">
                  <div>{selectedNodeIds.length} nodes selected</div>
                </div>
                <button onClick={() => setProgrammaticSelection([])} disabled={workflowEditLocked}>
                  Clear selection
                </button>
                <button onClick={selectDownstreamBranch} disabled={workflowEditLocked || !selectedNodeId}>
                  Select downstream branch
                </button>
                <button className="danger" onClick={deleteSelectedNode} disabled={workflowEditLocked}>
                  Delete selected nodes
                </button>
              </div>
            ) : null}

            {selectedNode ? (
              <div className="sidebar-section inspector">
                <div className="section-label">Inspector</div>
                <div className="inspector-summary">
                  <div>{selectedNode.type}</div>
                  <div>{selectedNode.id}</div>
                </div>
                {selectedNodeDiagnostics.length > 0 ? (
                  <div className="problems-panel">
                    <div className="problems-heading">
                      <span>Problems</span>
                      <span>{selectedNodeDiagnostics.length}</span>
                    </div>
                    <div className="problems-list">
                      {selectedNodeDiagnostics.map((diagnostic, index) => (
                        <div className="problem-item" key={`${diagnostic.code}-${diagnostic.field_path ?? ""}-${diagnostic.reference ?? ""}-${index}`}>
                          <div className="problem-message">{diagnostic.message}</div>
                          <div className="problem-meta">
                            {diagnostic.field_path ? <span>{diagnostic.field_path}</span> : null}
                            {diagnostic.reference ? <span>{diagnostic.reference}</span> : null}
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                ) : null}
                <button onClick={selectDownstreamBranch} disabled={workflowEditLocked}>
                  Select downstream branch
                </button>
                {renderSelectedTriggerToggle()}
                <div className="inspector-metadata-field">
                  <span>Node ID</span>
                  <code>{selectedNode.id}</code>
                </div>
                <label className="inspector-label">
                  <span>Display name</span>
                  <input
                    value={inspectorDisplayName}
                    onChange={(event) => {
                      const nextValue = event.target.value;
                      setInspectorDisplayName(nextValue);
                      stageInspectorDraft({ displayName: nextValue });
                    }}
                    disabled={workflowEditLocked}
                  />
                </label>

                {renderEffectTargetField()}
                {inspectorConfigurationFields.map((field) => renderField(field))}

                {Object.keys(inspectorUnknownValues).length > 0 ? (
                  <details className="raw-json-panel">
                    <summary>Additional raw JSON</summary>
                    <p className="field-description">These keys are preserved because they are not in the current catalog.</p>
                    <textarea value={rawJsonDraft} rows={8} readOnly />
                  </details>
                ) : null}

                {inspectorOutputFields.map((field) => renderField(field))}

                {renderSelectedNodeOutputContract()}

                {selectedNodeHasUnsavedChanges ? (
                  <button className="primary" onClick={resetSelectedNodeChanges} disabled={workflowEditLocked}>
                    Reset changes
                  </button>
                ) : null}

                <button className="danger" onClick={deleteSelectedNode} disabled={workflowEditLocked}>
                  Delete node
                </button>
              </div>
            ) : null}

            {selectedEdge ? (
              <div className="sidebar-section inspector">
                <div className="section-label">Edge</div>
                <div className="inspector-summary">
                  <div>{selectedEdge.source_id}</div>
                  <div>Route: {selectedEdgeRouteLabel}</div>
                  <div>{selectedEdge.target_id}</div>
                  {selectedEdge.allow_cycle ? <div>Cycle return</div> : null}
                </div>
                <button className="danger" onClick={deleteSelectedEdge} disabled={workflowEditLocked}>
                  Delete edge
                </button>
              </div>
            ) : null}

            {error ? <div className="error-box">{error}</div> : null}
            </>
          )}
        </aside>

        {renderWorkflowTutorialChooser()}
        {renderWorkflowPackModal()}

          <div
            className="workflow-sidebar-resize-handle"
            role="separator"
            aria-label="Resize workflow sidebar"
            aria-orientation="vertical"
            aria-valuemin={WORKFLOW_SIDEBAR_MIN_WIDTH}
            aria-valuemax={maxWorkflowSidebarWidth(appShellRef.current?.getBoundingClientRect().width ?? sidebarWidth + WORKFLOW_SIDEBAR_CANVAS_MIN_WIDTH)}
            aria-valuenow={sidebarWidth}
            tabIndex={0}
            onPointerDown={startSidebarResize}
            onKeyDown={handleSidebarResizeKeyDown}
          />

          <div className="workflow-canvas-stage">
            {renderCompactToolbar()}
          <main
            className="canvas-shell"
            ref={canvasShellRef}
            onDragOver={handleNodeTypeDragOver}
            onDrop={handleNodeTypeDrop}
            onPointerEnter={handleCanvasPointerEnter}
            onPointerMove={handleCanvasPointerMove}
            onPointerLeave={handleCanvasPointerLeave}
            onTouchStartCapture={handleCanvasTouchStartCapture}
            onTouchMoveCapture={handleCanvasTouchMoveCapture}
            onTouchEndCapture={handleCanvasTouchEndCapture}
            onTouchCancelCapture={handleCanvasTouchCancelCapture}
          >
            <ReactFlow
              nodes={displayedFlowNodes}
              edges={displayedFlowEdges}
              nodeTypes={nodeTypes}
              preventScrolling={!isCompactCanvasTouchMode || compactCanvasMode === "pan"}
              panOnDrag
              zoomOnPinch
              onInit={handleFlowInit}
              onMove={(_, viewport) => setCanvasViewport(viewport)}
              onNodesChange={(changes) => {
                if (!isInspectMode) updateNodePositions(changes);
              }}
              onEdgesChange={(changes) => {
                if (!isInspectMode) updateEdgeChanges(changes);
              }}
              onNodeClick={handleWorkflowNodeClick}
              onNodeContextMenu={handleNodeContextMenu}
              onEdgeClick={(_, edge) => {
                if (isInspectMode || isTutorialMode) return;
                setNodeConnectionMenu(null);
                setPendingConnection(null);
                setProgrammaticSelection([]);
                setSelectedEdgeId(edge.id);
              }}
              onNodeDrag={(_, node, nodes) => {
                if (!isInspectMode) updateDraggedNodePositions(node, nodes.length > 0 ? nodes : [node]);
              }}
              onNodeDragStop={(_, node, nodes) => {
                if (!isInspectMode) persistNodePositions(node, nodes.length > 0 ? nodes : [node]);
              }}
              onPaneClick={handlePaneClick}
              onPaneContextMenu={handlePaneContextMenu}
              onConnect={onConnect}
              onEdgesDelete={onEdgesDelete}
              onNodesDelete={onNodesDelete}
              nodesDraggable={!workflowEditLocked && (!isCompactCanvasTouchMode || compactCanvasMode !== "connect")}
              nodesConnectable={!workflowEditLocked}
              elementsSelectable={!workflowEditLocked}
              fitView
              deleteKeyCode={isInspectMode ? null : ["Backspace", "Delete"]}
              multiSelectionKeyCode={["Control", "Meta"]}
            >
              <Background gap={20} size={1} />
            </ReactFlow>
            {isTutorialMode && activeWorkflowTutorialStep ? (
              <div className="workflow-tutorial-overlay" aria-hidden="false">
                <div
                  className={`workflow-tutorial-bubble placement-${activeWorkflowTutorialStep.bubblePlacement}`}
                  ref={tutorialBubbleRef}
                  style={tutorialBubbleStyle}
                  role="dialog"
                  aria-label={activeWorkflowTutorialStep.title}
                >
                  <div className="workflow-tutorial-bubble-header">
                    <div className="section-label">First steps</div>
                    <div className="workflow-tutorial-progress">
                      Step {(activeWorkflowTutorial?.stepIndex ?? 0) + 1} of {activeWorkflowTutorialDefinition?.steps.length ?? 0}
                    </div>
                  </div>
                  <h2>{activeWorkflowTutorialStep.title}</h2>
                  <p>{activeWorkflowTutorialStep.body}</p>
                  {workflowTutorialHint ? (
                    <div className="workflow-tutorial-action-hint workflow-tutorial-action-hint-message">
                      {workflowTutorialHint}
                    </div>
                  ) : null}
                  <div className="workflow-tutorial-action-hint">
                    Required: {tutorialRequiredActionDescription(activeWorkflowTutorialStep.requiredAction)}
                  </div>
                  <div className="workflow-tutorial-actions">
                    {activeWorkflowTutorialStep.requiredAction.kind === "next" ? (
                      <button type="button" className="primary" onClick={advanceWorkflowTutorial}>
                        Next
                      </button>
                    ) : null}
                    <button type="button" className="subtle-button" onClick={resetWorkflowTutorial}>
                      Reset
                    </button>
                    <button type="button" className="subtle-button" onClick={() => exitWorkflowTutorial()}>
                      Exit
                    </button>
                  </div>
                </div>
              </div>
            ) : null}
            {pendingConnection ? (
              <svg className="pending-connection-layer" aria-hidden="true">
                <line
                  className={`pending-connection-line route-${pendingConnection.routeClass}`}
                  x1={pendingConnection.sourcePoint.x}
                  y1={pendingConnection.sourcePoint.y}
                  x2={pendingConnection.cursorPoint.x}
                  y2={pendingConnection.cursorPoint.y}
                />
                {pendingConnection.sourcePort ? (
                  <text
                    className="pending-connection-label"
                    x={(pendingConnection.sourcePoint.x + pendingConnection.cursorPoint.x) / 2}
                    y={(pendingConnection.sourcePoint.y + pendingConnection.cursorPoint.y) / 2 - 8}
                  >
                    {pendingConnection.routeLabel}
                  </text>
                ) : null}
              </svg>
            ) : null}
            {nodeConnectionMenu ? (
              <div
                className="node-context-menu"
                style={{
                  left: `${nodeConnectionMenu.position.x}px`,
                  top: `${nodeConnectionMenu.position.y}px`,
                }}
                role="menu"
              >
                {nodeConnectionMenu.failedCount > 0 ? (
                  <>
                    <div className="node-context-menu-heading">Runtime</div>
                    <button
                      type="button"
                      className="node-context-menu-item node-context-menu-danger"
                      onClick={() => void dismissNodeFailures(nodeConnectionMenu.nodeId, nodeConnectionMenu.failedCount)}
                      disabled={Boolean(dismissingNodeFailureIds[nodeConnectionMenu.nodeId]) || workflowRunControlLocked}
                      role="menuitem"
                    >
                      {dismissingNodeFailureIds[nodeConnectionMenu.nodeId] ? "Dismissing fail logs..." : "Dismiss all fail logs"}
                    </button>
                    <div className="node-context-menu-separator" />
                  </>
                ) : null}
                <div className="node-context-menu-heading">Add connection</div>
                {nodeConnectionMenu.routes.map((route) => (
                  <button
                    key={route.sourcePort ?? "__default"}
                    type="button"
                    className={`node-context-menu-item route-${route.routeClass}`}
                    onClick={() => startPendingConnection(route)}
                    title={route.description ?? undefined}
                    role="menuitem"
                  >
                    {route.label}
                  </button>
                ))}
              </div>
            ) : null}
            <div className="workflow-graph-label-layer" aria-hidden="true">
              {graphLabels.map((label) => (
                <div
                  key={label.graph.id}
                  className={`workflow-graph-canvas-label${selectedGraphId === label.graph.id ? " selected" : ""}`}
                  style={{ left: `${label.left}px`, top: `${label.top}px` }}
                >
                  {label.graph.name}
                </div>
              ))}
            </div>
            {flowInstance
              ? snapGuides.map((guide) => {
                  const viewport = flowInstance.getViewport();
                  const scaledPosition = guide.position * viewport.zoom;
                  const offset = guide.orientation === "vertical" ? viewport.x : viewport.y;
                  const style =
                    guide.orientation === "vertical"
                      ? { left: `${scaledPosition + offset}px` }
                      : { top: `${scaledPosition + offset}px` };
                  return (
                    <div
                      key={`${guide.orientation}-${guide.position}`}
                      className={`snap-guide snap-guide-${guide.orientation}`}
                      style={style}
                    />
                  );
                })
              : null}
          </main>
          {renderCompactBottomSheet()}
          </div>
        </div>
    </div>
  );
}

import { type Viewport } from "@xyflow/react";
import {
  type WorkflowConfig,
  type WorkflowEdge,
  type WorkflowEditorDocument,
  type WorkflowEditorFieldDefinition,
  type WorkflowEditorNodePortDefinition,
  type WorkflowGraphMetadata,
  type WorkflowLayout,
  type WorkflowNodeConfig,
  type WorkflowNodeExecutionFilter,
  type WorkflowNodeKind,
  type WorkflowOutputSchemas,
  type WorkflowRunDetail,
  type WorkflowValidationDiagnostic,
} from "../../types";
import { type WorkflowConnectionRouteOption } from "../../workflowRoutes";
import { type ToolArgumentPolicyDraftRow } from "../../workflowToolArgumentPolicies";
import { type WorkflowTutorialStep } from "../../workflowTutorials";
import { type MapTemplateDraftRow } from "./mapTemplateDraft";
import { EFFECT_TARGET_KEYS } from "./editorConstants";

export type GraphNodeData = {
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

export type GraphNodeStatusBadge = {
  status: string;
  count: number;
  label: string;
  title: string;
  compact: boolean;
};

export type SaveState = "idle" | "saving" | "saved";

export type WorkflowGeneratorState = "idle" | "draftable" | "generating" | "ready" | "error";

export type WorkflowPackModalMode = "create" | "edit";

export type WorkflowPackFormState = {
  packId: string;
  packVersion: string;
  description: string;
};

export type WorkflowUndoEntry = {
  document: WorkflowDocumentSurface;
  nodeEditOrigins: Record<string, string>;
};

export type WorkflowRunDetailLookupKind =
  | "auto"
  | "run_id"
  | "event_id"
  | "trigger_event_id"
  | "trace_id"
  | "session_id"
  | "artifact_id";

export type WorkflowRunSearchScopeOption = {
  value: string;
  kind: "graph" | "node";
  id: string;
  label: string;
};

export type WorkflowNodeExecutionFilterDraft = {
  location: "input" | "output";
  path: string;
  operator: WorkflowNodeExecutionFilter["operator"];
  valueType: "string" | "number" | "boolean" | "null";
  value: string;
};

export type WorkflowNodeExecutionQuery = {
  node_id: string;
  started_from: string;
  started_to: string;
  statuses: string[];
  filters: WorkflowNodeExecutionFilter[];
  limit: number;
};

export type BindingOption = {
  value: string;
  label: string;
};

export type WorkflowFieldOption = WorkflowEditorFieldDefinition["options"][number];

export type OperatorSteeringNoteStatus = "pending" | "injecting" | "delivered" | "cancelled" | "failed";

export type OperatorSteeringNote = {
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

export type OperatorSteeringNoteListResponse = {
  session_id: string;
  dispatch_id?: string | null;
  directory_path: string;
  notes: OperatorSteeringNote[];
};

export type OperatorSteeringNoteMutationResponse = {
  directory_path: string;
  note: OperatorSteeringNote;
};

export type StringMapDraftRow = {
  id: string;
  key: string;
  value: string;
};

export type SessionListMetadataRuleKind = "equals" | "exists" | "prefix";

export type SessionListMetadataRuleDraftRow = {
  id: string;
  kind: SessionListMetadataRuleKind;
  key: string;
  value: string;
};

export type JsonDisplayMode = "structured" | "raw";

export type InspectorDraftSnapshot = {
  nodeId: string;
  displayName: string;
  knownValues: Record<string, unknown>;
  unknownValues: Record<string, unknown>;
  effectTargetKey?: EffectTargetKey;
  jsonDrafts: Record<string, string>;
  toolArgumentPolicyDrafts: Record<string, ToolArgumentPolicyDraftRow[]>;
  mapTemplateDrafts: Record<string, MapTemplateDraftRow[]>;
  jsonDisplayModes: Record<string, JsonDisplayMode>;
  stringListDrafts: Record<string, string>;
  stringMapDrafts: Record<string, StringMapDraftRow[]>;
};

export type DirectorySelectOption = {
  value: string;
  label: string;
};

export type WorkflowGraphLabel = {
  graph: WorkflowGraphMetadata;
  left: number;
  top: number;
};

export type CanvasPoint = {
  x: number;
  y: number;
};

export type ScreenPoint = {
  clientX: number;
  clientY: number;
};

export type TutorialEditorSnapshot = {
  selectedNodeIds: string[];
  selectedNodeId: string | null;
  selectedEdgeId: string | null;
  selectedGraphId: string | null;
  runDetail: WorkflowRunDetail | null;
  canvasViewport: Viewport;
  viewportWasMeaningful: boolean;
};

export type TutorialBubbleSize = {
  width: number;
  height: number;
};

export type PendingConnectionState = {
  sourceId: string;
  sourcePort: string | null;
  routeLabel: string;
  routeClass: string;
  sourcePoint: CanvasPoint;
  cursorPoint: CanvasPoint;
};

export type NodeConnectionMenuState = {
  nodeId: string;
  position: CanvasPoint;
  sourcePoint: CanvasPoint;
  routes: WorkflowConnectionRouteOption[];
  failedCount: number;
};

export type PendingConnectionRole = "source" | "valid-target" | "invalid-target";

export type CopiedWorkflowNode = {
  node: WorkflowNodeConfig;
  position: { x: number; y: number };
  width: number;
  height: number;
};

export type CopiedWorkflowSelection = {
  nodes: CopiedWorkflowNode[];
  edges: WorkflowEdge[];
  primaryNodeId: string | null;
};

export type WorkflowDocumentSurface = Pick<WorkflowEditorDocument, "config" | "layout" | "graphs">;

export type WorkflowDocumentLike = {
  config: WorkflowConfig;
  layout: WorkflowLayout;
  graphs: WorkflowGraphMetadata[];
  output_schemas?: WorkflowOutputSchemas;
};

export type WorkflowValidationCacheEntry = {
  signature: string;
  diagnostics: WorkflowValidationDiagnostic[];
};

export type RuntimePulseContext = {
  isInspectMode: boolean;
  isTutorialMode: boolean;
  workflowGeneratorOpen: boolean;
  visibleGraphNodeIds: ReadonlySet<string>;
};

export type EffectTargetKey = (typeof EFFECT_TARGET_KEYS)[number];

export type NodeTypeDragPayload = {
  kind: WorkflowNodeKind;
  type: string;
};

export type WorkflowTutorialProgress = {
  completedTutorialIds: string[];
  skippedTutorialIds: string[];
};

export type WorkflowTutorialChooserStatus = "available" | "completed" | "skipped" | "in-progress";

export type CompactActivePanel = null | "create" | "inspector" | "runs" | "graphs" | "save";

export type CompactCanvasMode = "select" | "pan" | "connect";

export type CompactCanvasTouchMode = "idle" | "pane-scroll" | "pane-pan" | "node-drag" | "multi-touch-pan";

export type CompactPanePanState = {
  touchId: number;
  clientX: number;
  clientY: number;
  viewport: Viewport;
};

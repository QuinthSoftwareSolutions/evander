import {
  applyEdgeChanges,
  Background,
  type Connection,
  type Edge,
  type Node,
  type NodeChange,
  ReactFlow,
  type ReactFlowInstance,
  type Viewport,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import {
  type CSSProperties,
  type ChangeEvent as ReactChangeEvent,
  type DragEvent as ReactDragEvent,
  type FormEvent as ReactFormEvent,
  type ReactNode,
  type PointerEvent as ReactPointerEvent,
  type TouchEvent as ReactTouchEvent,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { hashQueryParam, updateHashQueryParam } from "./adminRouting";
import { useAdminUi } from "./features/admin/AdminContext";
import { ApiError } from "./features/admin/api";
import {
  cachedVisibleValidationDiagnostics,
  mergeWorkflowDiagnostics,
  remoteOnlyWorkflowDiagnostics,
  scheduleWorkflowValidation,
  validateWorkflowClientDiagnostics,
  validateWorkflowGraphBindingDiagnostics,
  workflowDiagnosticsForGraphs,
  workflowGraphScopedConfig,
  workflowGraphValidationSignature,
} from "./features/workflow-editor/bindingValidation";
import { createCanvasConnectionActions } from "./features/workflow-editor/canvasConnectionActions";
import {
  buildEdges,
  buildNodes,
  currentPageVisible,
  currentPositionsFromNodes,
  isCompactCanvasTouchViewport,
  nodeTypes,
  workflowGraphLabels,
} from "./features/workflow-editor/canvasModel";
import { createCanvasViewportActions } from "./features/workflow-editor/canvasViewportActions";
import { createCompactPanelRenderers } from "./features/workflow-editor/compactPanelRenderers";
import { createDocumentPanelRenderers } from "./features/workflow-editor/documentPanelRenderers";
import {
  fetchJson,
  workflowCatalogPath,
  workflowEditorDocumentPath,
  workflowPackHistoryDetailPath,
  workflowPackHistoryPath,
} from "./features/workflow-editor/editorApi";
import {
  AGENT_CREATE_NODE_TYPE,
  EFFECT_TARGET_KEYS,
  EMPTY_OUTPUT_SCHEMAS,
  NODE_GROUPS,
  RUNTIME_NODE_PULSE_DURATION_MS,
  RUNTIME_NODE_PULSE_SEEN_KEY_CAP,
  TARGET_SELECTOR_NODE_TYPES,
  WORKFLOW_TUTORIAL_PROGRESS_API_PATH,
  WORKFLOW_UNDO_HISTORY_LIMIT,
} from "./features/workflow-editor/editorConstants";
import {
  type CanvasPoint,
  type CompactActivePanel,
  type CompactCanvasMode,
  type CompactCanvasTouchMode,
  type CompactPanePanState,
  type CopiedWorkflowSelection,
  type EffectTargetKey,
  type GraphNodeData,
  type InspectorDraftSnapshot,
  type JsonDisplayMode,
  type NodeConnectionMenuState,
  type OperatorSteeringNote,
  type OperatorSteeringNoteListResponse,
  type OperatorSteeringNoteMutationResponse,
  type PendingConnectionRole,
  type PendingConnectionState,
  type RuntimePulseContext,
  type SaveState,
  type ScreenPoint,
  type SessionListMetadataRuleDraftRow,
  type StringMapDraftRow,
  type TutorialBubbleSize,
  type TutorialEditorSnapshot,
  type WorkflowGeneratorState,
  type WorkflowPackFormState,
  type WorkflowPackModalMode,
  type WorkflowRunDetailLookupKind,
  type WorkflowRunSearchScopeOption,
  type WorkflowTutorialChooserStatus,
  type WorkflowTutorialProgress,
  type WorkflowUndoEntry,
  type WorkflowValidationCacheEntry,
} from "./features/workflow-editor/editorTypes";
import { getCatalogEntry, trimNonEmpty } from "./features/workflow-editor/editorValues";
import {
  activeEffectTargetKey,
  agentCreateFieldVisibleForBackend,
  ensureDirectorySelectOption,
  flattenDirectorySelectOptions,
  normalizedAgentCreateExecutionBackend,
} from "./features/workflow-editor/fieldModels";
import { createFieldRenderers } from "./features/workflow-editor/fieldRenderers";
import { createGeneratorActions } from "./features/workflow-editor/generatorActions";
import {
  cloneConfig,
  cloneWorkflowDocumentSurface,
  dirtyWorkflowNodeIds,
  edgeId,
  emptyCatalog,
  emptyConfig,
  nodeById,
  normalizeDerivedNodeFields,
  normalizeWorkflowEditorDocument,
  normalizeWorkflowGraphs,
  validateConnection,
  workflowEditableDocumentsEqual,
  workflowGraphKey,
  workflowNodeIds,
  workflowNodes,
  workflowUndoDocumentsEqual,
  workflowValuesEqual,
} from "./features/workflow-editor/graphModel";
import { createInspectorPanelRenderers } from "./features/workflow-editor/inspectorPanelRenderers";
import { useManualEventTrigger } from "./features/workflow-editor/useManualEventTrigger";
import { createNodeDraftActions } from "./features/workflow-editor/nodeDraftActions";
import { createNodeFieldActions } from "./features/workflow-editor/nodeFieldActions";
import { createPackPanelRenderers } from "./features/workflow-editor/packPanelRenderers";
import {
  operatorSteeringKey,
  operatorSteeringQuery,
  preferredWorkflowRunDetailNodeId,
  workflowRunDetailConfig,
  workflowRunDetailDisplayNodeIds,
  workflowRunDetailGraphNodes,
  workflowRunDetailIsFailed,
  workflowRunDetailLayout,
  workflowRunDetailLookupQuery,
  workflowRunDetailNodeById,
  workflowRunDetailVisitedNodeIds,
  workflowRunLookupFromHash,
  workflowRunSearchQuery,
} from "./features/workflow-editor/runDetailsModel";
import { createRunPanelRenderers } from "./features/workflow-editor/runPanelRenderers";
import { createSelectionActions } from "./features/workflow-editor/selectionActions";
import { createTutorialActions } from "./features/workflow-editor/tutorialActions";
import {
  decorateWorkflowTutorialNodes,
  normalizeWorkflowTutorialProgress,
  tutorialBubblePosition,
  tutorialRequiredActionDescription,
} from "./features/workflow-editor/tutorialModel";
import { createTutorialPanelRenderers } from "./features/workflow-editor/tutorialPanelRenderers";
import { useNodeExecutionHistory } from "./features/workflow-editor/useNodeExecutionHistory";
import { useWorkflowWorkbench } from "./features/workflow-editor/useWorkflowWorkbench";
import { type MapTemplateDraftRow } from "./features/workflow-editor/mapTemplateDraft";
import { createWorkflowPackActions } from "./features/workflow-editor/workflowPackActions";
import {
  type WorkflowConfig,
  type WorkflowEditorCatalog,
  type WorkflowEditorDocument,
  type WorkflowEditorFieldDefinition,
  type WorkflowEditorNodeCatalogEntry,
  type WorkflowEditorSave,
  type WorkflowEditorValidation,
  type WorkflowGraphMetadata,
  type WorkflowLayout,
  type WorkflowNodeConfig,
  type WorkflowNodeKind,
  type WorkflowOutputSchemas,
  type WorkflowPackDescriptor,
  type WorkflowPackHistoryDetail,
  type WorkflowPackHistoryEntry,
  type WorkflowPackHistoryList,
  type WorkflowRunAgentLink,
  type WorkflowRunDetail,
  type WorkflowRunNodeDetail,
  type WorkflowRunRecentNodeActivity,
  type WorkflowRunSearch,
  type WorkflowRunSearchItem,
  type WorkflowRunSnapshot,
  type WorkflowValidationDiagnostic,
} from "./types";
import { compareWorkflowNodeCatalogEntries, groupWorkflowNodeCatalogEntries } from "./workflowCatalogGroups";
import { graphVisibleInDirectory } from "./workflowGraphDirectory";
import {
  deriveWorkflowOutputSchemas,
  upstreamBindingOptions,
  selectedNodeOutputContract as workflowNodeOutputContract,
} from "./workflowNodeOutputs";
import { workflowNodeBounds as nodeBounds, type SnapGuide } from "./workflowNodeSnap";
import {
  humanizeWorkflowRouteKey,
  type WorkflowConnectionRouteOption,
  workflowEdgeRouteLabel,
  workflowOutputPortsForNode,
} from "./workflowRoutes";
import { workflowRunDetailActivityCounts } from "./workflowRunInspect";
import {
  EMPTY_WORKFLOW_TARGET_INVENTORY,
  type WorkflowTargetInventory,
  workflowTargetInventoryPath,
  workflowTargetOptions,
} from "./workflowTargetInventory";
import { type ToolArgumentPolicyDraftRow } from "./workflowToolArgumentPolicies";
import {
  WORKFLOW_TUTORIAL_DEFINITIONS,
  workflowTutorialDefinitionById,
  type WorkflowTutorialRequiredAction,
  type WorkflowTutorialSession,
  workflowTutorialTargetNodeId,
} from "./workflowTutorials";

export default function App() {
  const {
    privilegedUnlocked,
    selectedDirectoryPath,
    directoryTree,
    setWorkflowHasUnsavedChanges,
    canReadWorkflows,
    canReadWorkflowRuns,
    canWriteWorkflows,
    canManageWorkflowPacks,
    canControlWorkflowRuns,
    currentUserId,
  } = useAdminUi();
  const initialWorkflowRunLookup = workflowRunLookupFromHash();
  const initialWorkflowPack = hashQueryParam(
    typeof window === "undefined" ? "" : window.location.hash,
    "workflow_pack",
  );
  const appShellRef = useRef<HTMLDivElement | null>(null);
  const workbench = useWorkflowWorkbench(appShellRef);
  const [workbenchPanel, setWorkbenchPanel] = useState<"inspector" | "runs" | "checks">("inspector");
  const canvasShellRef = useRef<HTMLElement | null>(null);
  const tutorialBubbleRef = useRef<HTMLDivElement | null>(null);
  const tutorialSnapshotRef = useRef<TutorialEditorSnapshot | null>(null);
  const hasAutoFitRef = useRef(false);
  const initialGraphDirectoryPathRef = useRef(selectedDirectoryPath);
  const copiedSelectionRef = useRef<CopiedWorkflowSelection | null>(null);
  const canvasCursorPointRef = useRef<ScreenPoint | null>(null);

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
  const workflowTargetInventoryCacheRef = useRef<Map<string, WorkflowTargetInventory>>(new Map());

  const previousSelectedDirectoryPathRef = useRef(selectedDirectoryPath);
  const selectedWorkflowPackReferenceRef = useRef("");
  const [document, setDocument] = useState<WorkflowEditorDocument | null>(null);
  const [savedDocument, setSavedDocument] = useState<WorkflowEditorDocument | null>(null);
  const [undoHistory, setUndoHistory] = useState<WorkflowUndoEntry[]>([]);
  const [catalog, setCatalog] = useState<WorkflowEditorCatalog>(emptyCatalog());
  const [workflowTargetInventory, setWorkflowTargetInventory] = useState<WorkflowTargetInventory>(
    EMPTY_WORKFLOW_TARGET_INVENTORY,
  );
  const [workflowGeneratorOpen, setWorkflowGeneratorOpen] = useState(false);
  const [workflowGeneratorDescription, setWorkflowGeneratorDescription] = useState("");
  const [workflowGeneratorState, setWorkflowGeneratorState] = useState<WorkflowGeneratorState>("idle");
  const [workflowGeneratorError, setWorkflowGeneratorError] = useState<string | null>(null);
  const [workflowGeneratorWarnings, setWorkflowGeneratorWarnings] = useState<string[]>([]);
  const [workflowGeneratorModel, setWorkflowGeneratorModel] = useState<string | null>(null);
  const [workflowGeneratorReadyDescription, setWorkflowGeneratorReadyDescription] = useState<string | null>(null);
  const [workflowPackModalMode, setWorkflowPackModalMode] = useState<WorkflowPackModalMode | null>(null);
  const [workflowPackModalTargetReference, setWorkflowPackModalTargetReference] = useState<string | null>(null);
  const [workflowPackForm, setWorkflowPackForm] = useState<WorkflowPackFormState>({
    packId: "",
    packVersion: "v1",
    description: "",
  });
  const [workflowPackSubmitting, setWorkflowPackSubmitting] = useState(false);
  const [workflowPackError, setWorkflowPackError] = useState<string | null>(null);
  const [workflowPackHistory, setWorkflowPackHistory] = useState<WorkflowPackHistoryEntry[]>([]);
  const [workflowPackHistoryLoading, setWorkflowPackHistoryLoading] = useState(false);
  const [workflowPackHistoryLoadingMore, setWorkflowPackHistoryLoadingMore] = useState(false);
  const [workflowPackHistoryNextCursor, setWorkflowPackHistoryNextCursor] = useState<string | null>(null);
  const [workflowPackHistoryError, setWorkflowPackHistoryError] = useState<string | null>(null);
  const [workflowPackHistoryDetail, setWorkflowPackHistoryDetail] = useState<WorkflowPackHistoryDetail | null>(null);
  const [workflowPackHistoryDetailLoading, setWorkflowPackHistoryDetailLoading] = useState(false);
  const [workflowPackHistoryOpen, setWorkflowPackHistoryOpen] = useState(false);
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
  const [agentWorkerCatalog, setAgentWorkerCatalog] = useState<{ key: string; catalog: WorkflowEditorCatalog } | null>(null);
  const [inspectorUnknownValues, setInspectorUnknownValues] = useState<Record<string, unknown>>({});
  const [inspectorJsonDrafts, setInspectorJsonDrafts] = useState<Record<string, string>>({});
  const [inspectorMapTemplateDrafts, setInspectorMapTemplateDrafts] = useState<Record<string, MapTemplateDraftRow[]>>({});
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
  const workflowMutationLocked = workflowEditLocked || workflowGeneratorOpen;
  const workflowPackManageLocked = editLocked || isInspectMode || Boolean(activeWorkflowTutorial) || workflowGeneratorOpen || !canManageWorkflowPacks;
  const workflowRunReadLocked = !canReadWorkflowRuns;
  const workflowRunControlLocked = !canControlWorkflowRuns || workflowRunReadLocked;
  const canUndoWorkflowChanges = !workflowMutationLocked && undoHistory.length > 0;
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
  selectedWorkflowPackReferenceRef.current = selectedWorkflowPackReference;
  const workflowPackModalTarget = workflowPackModalTargetReference
    ? workflowPackOptions.find((pack) => pack.reference === workflowPackModalTargetReference) ?? null
    : null;
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
  const nodeExecutionHistory = useNodeExecutionHistory(selectedNode, workflowRunReadLocked);
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
  const manualEventTrigger = useManualEventTrigger({
    node: selectedNode,
    savedDocument,
    hasUnsavedChanges: hasUnsavedWorkflowChanges || Boolean(inspectorDraftError) || saveState === "saving",
    canControl: canControlWorkflowRuns,
    canReadRuns: canReadWorkflowRuns,
    userId: currentUserId,
    unavailableReason: isTutorialMode ? "Manual triggering is unavailable in tutorials."
      : workflowGeneratorOpen ? "Close the workflow generator before triggering a node." : null,
    inspectRun: inspectWorkflowRun,
  });
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
  const agentWorkerId = selectedNode?.type === AGENT_CREATE_NODE_TYPE
    && inspectorFieldModes.worker_id !== "bind"
    && typeof inspectorKnownValues.worker_id === "string"
    ? inspectorKnownValues.worker_id.trim() : "";
  const agentWorkerDirectory = selectedNode?.type === AGENT_CREATE_NODE_TYPE
    && inspectorFieldModes.directory_path !== "bind"
    && typeof inspectorKnownValues.directory_path === "string"
    && inspectorKnownValues.directory_path.trim()
    ? inspectorKnownValues.directory_path.trim() : selectedDirectoryPath;
  const agentWorkerCatalogKey = selectedNode?.type === AGENT_CREATE_NODE_TYPE
    && (agentWorkerId || agentWorkerDirectory !== selectedDirectoryPath)
    ? workflowCatalogPath(agentWorkerDirectory, agentWorkerId) : "";

  useEffect(() => {
    if (!agentWorkerCatalogKey || isTutorialMode) return;
    let cancelled = false;
    fetchJson<WorkflowEditorCatalog>(agentWorkerCatalogKey)
      .then((catalog) => {
        if (!cancelled) setAgentWorkerCatalog({ key: agentWorkerCatalogKey, catalog });
      })
      .catch((reason: unknown) => {
        if (!cancelled) setError(reason instanceof Error ? reason.message : String(reason));
      });
    return () => { cancelled = true; };
  }, [agentWorkerCatalogKey, isTutorialMode]);

  const effectTargetKey = hasCollapsedTargetUi ? inspectorEffectTargetKey : activeEffectTargetKey(inspectorKnownValues);
  const inspectorFields = (() => {
    if (!selectedCatalogEntry) return [];
    let fields = selectedCatalogEntry.fields;
    if (selectedNode?.type === AGENT_CREATE_NODE_TYPE) {
      const executionBackend = normalizedAgentCreateExecutionBackend(inspectorKnownValues);
      fields = fields.filter((field) => agentCreateFieldVisibleForBackend(field, executionBackend));
      if (agentWorkerCatalogKey) {
        const workerFields = agentWorkerCatalog?.key === agentWorkerCatalogKey
          ? agentWorkerCatalog.catalog.nodes.find((node) => node.type === AGENT_CREATE_NODE_TYPE)?.fields
          : undefined;
        fields = fields.map((field) => {
          if (field.key !== "subscription_profile_id" && field.key !== "worker_id") return field;
          return workerFields?.find((candidate) => candidate.key === field.key)
            ?? (field.key === "subscription_profile_id" ? { ...field, options: [] } : field);
        });
      }
    }
    if (!hasCollapsedTargetUi) return fields;
    return fields.filter((field) => !EFFECT_TARGET_KEYS.includes(field.key as EffectTargetKey));
  })();
  const effectTargetField = selectedCatalogEntry?.fields.find((field) => field.key === effectTargetKey)
    ?? selectedCatalogEntry?.fields.find((field) => field.key === "target");
  const effectTargetOptions = useMemo(
    () => workflowTargetOptions(workflowTargetInventory, effectTargetKey),
    [effectTargetKey, workflowTargetInventory],
  );
  const communicationThreadField = selectedCatalogEntry?.fields.find((field) => field.key === "thread_id");

  const fieldRenderers = createFieldRenderers({
    hasCollapsedTargetUi,
    effectTargetField,
    inspectorKnownValues,
    effectTargetKey,
    inspectorFieldModes,
    workflowMutationLocked,
    tutorialAllowsInspectorFieldEdit,
    selectedNode,
    effectTargetOptions,
    updateFieldMode,
    updateKnownField,
    selectedNodeBindingOptions,
    updateEffectTargetKey,
    selectedNodeOutputContract,
    copyBindingExpression,
    workflowEditLocked,
    copiedBindingExpression,
    outputCopyError,
    updateSelectedTriggerEnabled,
    directoryPathOptions,
    updateStringListField,
    inspectorStringMapDrafts,
    updateStringMapRow,
    removeStringMapRow,
    addStringMapRow,
    inspectorToolArgumentPolicyDrafts,
    inspectorJsonDisplayModes,
    updateToolArgumentPolicyJsonDisplayMode,
    inspectorMapTemplateDrafts,
    setInspectorMapTemplateDrafts,
    setInspectorJsonDisplayModes,
    mutateToolArgumentPolicyDraftRows,
    inspectorJsonDrafts,
    setInspectorJsonDrafts,
    stageInspectorDraft,
    selectedCatalogEntry,
    inspectorSessionListMetadataRawMode,
    setInspectorSessionListMetadataRawMode,
    inspectorSessionListMetadataRules,
    mutateSessionListMetadataRuleRows,
    selectedNodeGraph,
    selectedDirectoryPath,
    updateClassificationFields,
    communicationThreadField,
    updateBindableEnumFieldMode,
    updateAdoQueryModeField,
    inspectorStringListDrafts,
    removeObjectListItem,
    updateObjectListItem,
    addObjectListItem,
  });

  const canvasViewportActions = createCanvasViewportActions({
    setFlowNodes,
    visibleGraphNodeIds,
    setSnapGuides,
    setFlowEdges,
    setFlowInstance,
    setCanvasViewport,
    flowInstance,
    setCompactCanvasMode,
    compactCanvasTouchModeRef,
    compactPanePanRef,
    closeConnectionUi,
    setCompactActivePanel,
    canvasShellRef,
    canvasViewport,
    canvasCursorPointRef,
    pendingConnection,
    updatePendingConnectionCursor,
    isCompactCanvasTouchMode,
    compactCanvasMode,
    workflowMutationLocked,
    document,
    flowNodes,
    rememberWorkflowUndoSnapshot,
    setDocument,
    setSaveState,
  });

  const canvasConnectionActions = createCanvasConnectionActions({
    setNodeConnectionMenu,
    setPendingConnection,
    setSelectedEdgeId,
    setWorkflowTutorialHint,
    setError,
    appShellRef,
    tutorialCurrentRequiredAction,
    isTutorialMode,
    nodeConnectionMenu,
    tutorialActionHintFor,
    workflowMutationLocked,
    nodeCenterCanvasPoint,
    setProgrammaticSelection,
    setSnapGuides,
    canvasPointFromScreenPoint,
    activeWorkflowDocument,
    activeWorkflowCatalog,
    patchWorkflowDocument,
    addNode,
    nodePositionFromScreenPoint,
    isInspectMode,
    setSelectedNodeIds,
    setSelectedNodeId,
    pendingConnection,
    tutorialAllowsAction,
    completeTutorialAction,
    isCompactCanvasTouchMode,
    compactCanvasMode,
    selectedNodeIds,
    document,
    patchDocument,
    deleteNodes,
  });

  const selectionActions = createSelectionActions({
    setSelectedNodeIds,
    setSelectedNodeId,
    setFlowNodes,
    document,
    selectedEdgeId,
    workflowMutationLocked,
    patchDocument,
    setSelectedEdgeId,
    setError,
    setNodeEditOrigins,
    setInspectorDraftError,
    selectedNodeIds,
    flowInstance,
    flowNodes,
    copiedSelectionRef,
    selectedNodeId,
    selectedWorkflowPackReference,
    flowPositionAtCanvasCursor,
    flowPositionAtCanvasCenter,
    isTutorialMode,
    exitWorkflowTutorial,
    isInspectMode,
    exitInspectMode,
    pendingConnection,
    nodeConnectionMenu,
    closeConnectionUi,
    compactActivePanel,
    setCompactActivePanel,
    canUndoWorkflowChanges,
    undoWorkflowChange,
    activeWorkflowDocument,
    tutorialCurrentRequiredAction,
    setWorkflowTutorialHint,
    tutorialActionHintFor,
    activeWorkflowCatalog,
    nodePositionAtCanvasCenter,
    patchWorkflowDocument,
    completeTutorialAction,
    workflowEditLocked,
    setSelectedGraphId,
    selectedGraph,
    graphNameDraft,
    graphDirectoryDraft,
    graphUsersDraft,
    graphDescriptionDraft,
  });

  const tutorialActions = createTutorialActions({
    activeWorkflowTutorialStep,
    isTutorialMode,
    selectedNode,
    setWorkflowTutorialProgress,
    setError,
    workflowTutorialProgress,
    setWorkflowTutorialHint,
    setWorkflowTutorialChooserOpen,
    activeWorkflowTutorial,
    tutorialSnapshotRef,
    selectedNodeIds,
    selectedNodeId,
    selectedEdgeId,
    selectedGraphId,
    runDetail,
    canvasViewport,
    setActiveWorkflowTutorial,
    setRunDetail,
    setProgrammaticSelection,
    setSelectedEdgeId,
    setSelectedGraphId,
    setNodeConnectionMenu,
    setPendingConnection,
    setSnapGuides,
    hasAutoFitRef,
    workflowTutorialProgressSets,
    document,
    savedDocument,
    nodeEditOrigins,
    setFlowNodes,
    catalog,
    workflowGeneratorOpen,
    runSnapshot,
    validationDiagnostics,
    runtimePulseNodeIds,
    setFlowEdges,
    setCanvasViewport,
    flowInstance,
  });

  const generatorActions = createGeneratorActions({
    workflowEditLocked,
    workflowGeneratorOpen,
    hasUnsavedWorkflowChanges,
    workflowGeneratorAbortRef,
    workflowGeneratorRequestIdRef,
    setWorkflowGeneratorOpen,
    setWorkflowGeneratorDescription,
    setWorkflowGeneratorState,
    setWorkflowGeneratorError,
    setWorkflowGeneratorWarnings,
    setWorkflowGeneratorModel,
    setWorkflowGeneratorReadyDescription,
    setRunDetail,
    setNodeConnectionMenu,
    setPendingConnection,
    setSelectedEdgeId,
    setSelectedGraphId,
    setNodeEditOrigins,
    applyWorkflowDocumentState,
    savedDocument,
    selectedDirectoryPath,
    document,
    workflowGeneratorState,
    workflowGeneratorReadyDescription,
    workflowGeneratorDescription,
  });

  const workflowPackActions = createWorkflowPackActions({
    selectedWorkflowPackReference,
    hasUnsavedWorkflowChanges,
    setError,
    loadWorkflowDocument,
    setWorkflowPackModalTargetReference,
    setWorkflowPackForm,
    setWorkflowPackError,
    setWorkflowPackModalMode,
    selectedWorkflowPack,
    workflowPackSubmitting,
    selectedDirectoryPath,
    setSavedDocument,
    setUndoHistory,
    setNodeEditOrigins,
    setSelectedGraphId,
    setSelectedEdgeId,
    setPendingConnection,
    setNodeConnectionMenu,
    setInspectorDraftError,
    applyWorkflowDocumentState,
    setSaveState,
    loadWorkflowPackHistory,
    document,
    workflowPackManageLocked,
    workflowPackModalMode,
    workflowPackModalTarget,
    workflowPackForm,
    setWorkflowPackSubmitting,
    workflowPackOptions,
    workflowReadLocked,
    workflowPackHistoryOpen,
    setWorkflowPackHistoryOpen,
  });

  const nodeDraftActions = createNodeDraftActions({
    inspectorMapTemplateDrafts,
    setInspectorMapTemplateDrafts,
    setInspectorDraftError,
    setInspectorNodeId,
    setInspectorDisplayName,
    setInspectorKnownValues,
    setInspectorUnknownValues,
    setInspectorJsonDrafts,
    setInspectorToolArgumentPolicyDrafts,
    setInspectorJsonDisplayModes,
    setInspectorStringListDrafts,
    setInspectorStringMapDrafts,
    setInspectorSessionListMetadataRules,
    setInspectorSessionListMetadataRawMode,
    setInspectorFieldModes,
    setInspectorEffectTargetKey,
    setRawJsonDraft,
    selectedNode,
    hasCollapsedTargetUi,
    selectedCatalogEntry,
    document,
    workflowMutationLocked,
    isTutorialMode,
    tutorialCurrentRequiredAction,
    activeWorkflowTutorial,
    activeWorkflowDocument,
    patchWorkflowDocument,
    setProgrammaticSelection,
    setSelectedEdgeId,
    setError,
    setNodeEditOrigins,
    savedDocument,
    nodeEditOrigins,
    deleteNodes,
    catalog,
    inspectorNodeId,
    inspectorDisplayName,
    inspectorKnownValues,
    inspectorUnknownValues,
    inspectorEffectTargetKey,
    inspectorJsonDrafts,
    inspectorToolArgumentPolicyDrafts,
    inspectorJsonDisplayModes,
    inspectorStringListDrafts,
    inspectorStringMapDrafts,
  });

  const nodeFieldActions = createNodeFieldActions({
    selectedNode,
    inspectorKnownValues,
    setInspectorKnownValues,
    stageInspectorDraft,
    inspectorJsonDrafts,
    setInspectorJsonDrafts,
    tutorialCurrentRequiredAction,
    isTutorialMode,
    completeTutorialAction,
    inspectorUnknownValues,
    setInspectorUnknownValues,
    inspectorStringListDrafts,
    setInspectorStringListDrafts,
    inspectorToolArgumentPolicyDrafts,
    setInspectorToolArgumentPolicyDrafts,
    inspectorJsonDisplayModes,
    setInspectorJsonDisplayModes,
    setInspectorDraftError,
    setError,
    setInspectorFieldModes,
    selectedNodeBindingOptions,
    setCopiedBindingExpression,
    setOutputCopyError,
    effectTargetKey,
    inspectorFieldModes,
    effectTargetField,
    setInspectorEffectTargetKey,
    workflowMutationLocked,
    inspectorStringMapDrafts,
    setInspectorStringMapDrafts,
    setInspectorSessionListMetadataRules,
    setInspectorSessionListMetadataRawMode,
    inspectorSessionListMetadataRules,
  });

  useEffect(() => {
    if (!hasCollapsedTargetUi || isTutorialMode) return;
    const directoryPath = selectedDirectoryPath.trim() || "root";
    const cachedInventory = workflowTargetInventoryCacheRef.current.get(directoryPath);
    if (cachedInventory) {
      setWorkflowTargetInventory(cachedInventory);
      return;
    }
    setWorkflowTargetInventory(EMPTY_WORKFLOW_TARGET_INVENTORY);
    let cancelled = false;
    fetchJson<WorkflowTargetInventory>(workflowTargetInventoryPath(directoryPath))
      .then((inventory) => {
        if (cancelled) return;
        setWorkflowTargetInventory(inventory);
        workflowTargetInventoryCacheRef.current.set(directoryPath, inventory);
      })
      .catch(() => {
        if (!cancelled) setWorkflowTargetInventory(EMPTY_WORKFLOW_TARGET_INVENTORY);
      });
    return () => {
      cancelled = true;
    };
  }, [hasCollapsedTargetUi, isTutorialMode, selectedDirectoryPath]);

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
    setWorkbenchPanel("inspector");
    if ((selectedNodeId || selectedEdgeId || isInspectMode) && workbench.view === "canvas") workbench.setView("split");
  }, [selectedNodeId, selectedEdgeId, isInspectMode]);

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

  function applyLoadedWorkflowDocument(
    payload: WorkflowEditorDocument,
    defaultDirectoryPath: string,
  ) {
    const backendOutputSchemas = payload.output_schemas ?? {};
    const normalizedConfig = normalizeDerivedNodeFields(cloneConfig(payload.config), backendOutputSchemas);
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
    hasAutoFitRef.current = false;
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

  async function loadWorkflowPackHistory(workflowPack?: string | null, cursor?: string | null) {
    const normalizedWorkflowPack = (workflowPack ?? "").trim();
    const normalizedCursor = (cursor ?? "").trim();
    const loadingMore = Boolean(normalizedCursor);
    if (!normalizedWorkflowPack || workflowReadLocked) {
      setWorkflowPackHistory([]);
      setWorkflowPackHistoryNextCursor(null);
      setWorkflowPackHistoryDetail(null);
      return;
    }
    if (loadingMore) {
      setWorkflowPackHistoryLoadingMore(true);
    } else {
      setWorkflowPackHistoryLoading(true);
    }
    setWorkflowPackHistoryError(null);
    try {
      const payload = await fetchJson<WorkflowPackHistoryList>(
        workflowPackHistoryPath(normalizedWorkflowPack, normalizedCursor),
      );
      if (selectedWorkflowPackReferenceRef.current.trim() !== normalizedWorkflowPack) {
        return;
      }
      setWorkflowPackHistory((current) => (
        loadingMore ? [...current, ...payload.entries] : payload.entries
      ));
      setWorkflowPackHistoryNextCursor(payload.next_cursor ?? null);
      if (!loadingMore) {
        setWorkflowPackHistoryDetail((current) => (
          current && payload.entries.some((entry) => entry.change_id === current.entry.change_id)
            ? current
            : null
        ));
      }
    } catch (reason) {
      if (!loadingMore) {
        setWorkflowPackHistory([]);
        setWorkflowPackHistoryNextCursor(null);
        setWorkflowPackHistoryDetail(null);
      }
      setWorkflowPackHistoryError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      if (loadingMore) {
        setWorkflowPackHistoryLoadingMore(false);
      } else {
        setWorkflowPackHistoryLoading(false);
      }
    }
  }

  async function loadWorkflowPackHistoryDetail(changeId: string) {
    if (!changeId || workflowReadLocked) return;
    setWorkflowPackHistoryDetailLoading(true);
    setWorkflowPackHistoryError(null);
    try {
      const payload = await fetchJson<WorkflowPackHistoryDetail>(
        workflowPackHistoryDetailPath(changeId),
      );
      setWorkflowPackHistoryDetail(payload);
    } catch (reason) {
      setWorkflowPackHistoryError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setWorkflowPackHistoryDetailLoading(false);
    }
  }

  useEffect(() => {
    let cancelled = false;
    fetchJson<WorkflowEditorDocument>(workflowEditorDocumentPath(initialWorkflowPack))
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
    if (!document || typeof window === "undefined") return;
    const nextHash = updateHashQueryParam(
      window.location.hash,
      "workflow_pack",
      selectedWorkflowPackReference,
    );
    if (window.location.hash !== nextHash) {
      window.location.hash = nextHash;
    }
  }, [document, selectedWorkflowPackReference]);

  useEffect(() => {
    setWorkflowPackHistoryOpen(false);
    void loadWorkflowPackHistory(selectedWorkflowPackReference);
  }, [selectedWorkflowPackReference, workflowReadLocked]);

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
    return tutorialActions.tutorialCurrentRequiredAction();
  }

  function tutorialActionHintFor(requiredAction: WorkflowTutorialRequiredAction | null): string {
    return tutorialActions.tutorialActionHintFor(requiredAction);
  }

  function tutorialAllowsAction(action: WorkflowTutorialRequiredAction): boolean {
    return tutorialActions.tutorialAllowsAction(action);
  }

  function tutorialAllowsNodeType(kind: WorkflowNodeKind, type: string): boolean {
    return tutorialActions.tutorialAllowsNodeType(kind, type);
  }

  function tutorialAllowsInspectorFieldEdit(fieldKey: string): boolean {
    return tutorialActions.tutorialAllowsInspectorFieldEdit(fieldKey);
  }

  function clearWorkflowTutorialProgressStatus(tutorialId: string): void {
    return tutorialActions.clearWorkflowTutorialProgressStatus(tutorialId);
  }

  function completeTutorialAction(action: WorkflowTutorialRequiredAction): boolean {
    return tutorialActions.completeTutorialAction(action);
  }

  function startWorkflowTutorial(tutorialId = "first-steps"): void {
    return tutorialActions.startWorkflowTutorial(tutorialId);
  }

  function restartWorkflowTutorial(tutorialId = "first-steps"): void {
    return tutorialActions.restartWorkflowTutorial(tutorialId);
  }

  function skipWorkflowTutorial(tutorialId = "first-steps"): void {
    return tutorialActions.skipWorkflowTutorial(tutorialId);
  }

  function workflowTutorialChooserStatusFor(tutorialId: string): WorkflowTutorialChooserStatus {
    return tutorialActions.workflowTutorialChooserStatusFor(tutorialId);
  }

  function workflowTutorialChooserStatusLabel(status: WorkflowTutorialChooserStatus): string {
    return tutorialActions.workflowTutorialChooserStatusLabel(status);
  }

  function advanceWorkflowTutorial(): void {
    return tutorialActions.advanceWorkflowTutorial();
  }

  function resetWorkflowTutorial(): void {
    return tutorialActions.resetWorkflowTutorial();
  }

  function exitWorkflowTutorial(options: { markCompleted?: boolean } = {}): void {
    return tutorialActions.exitWorkflowTutorial(options);
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

  function openWorkflowGenerator(): void {
    return generatorActions.openWorkflowGenerator();
  }

  function cancelWorkflowGenerator(): void {
    return generatorActions.cancelWorkflowGenerator();
  }

  function acceptWorkflowGeneratorDraft(): void {
    return generatorActions.acceptWorkflowGeneratorDraft();
  }

  function handleWorkflowGeneratorDescriptionChange(value: string): void {
    return generatorActions.handleWorkflowGeneratorDescriptionChange(value);
  }

  function generateWorkflowGeneratorDraft(): Promise<void> {
    return generatorActions.generateWorkflowGeneratorDraft();
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
    hasAutoFitRef.current = true;
    queueMicrotask(() => {
      const nodesById = new Map(displayedFlowNodes.map((node) => [node.id, node]));
      const graphs = canvasVisibleWorkflowGraphs
        .map((graph) => graph.node_ids.flatMap((id) => nodesById.get(id) ?? []))
        .filter((nodes) => nodes.length > 0)
        .sort((left, right) =>
          Math.min(...left.map((node) => node.position.y)) - Math.min(...right.map((node) => node.position.y))
          || Math.min(...left.map((node) => node.position.x)) - Math.min(...right.map((node) => node.position.x)),
        );
      const firstNode = [...(graphs[0] ?? displayedFlowNodes)].sort((left, right) =>
        left.position.x - right.position.x || left.position.y - right.position.y,
      )[0];
      const zoom = 0.8;
      const positionCanvas = isInspectMode || isTutorialMode
        ? flowInstance.fitView({ padding: 0.2 })
        : flowInstance.setViewport({
          x: Math.min(280, (canvasShellRef.current?.clientWidth ?? 960) * 0.25) - firstNode.position.x * zoom,
          y: 100 - firstNode.position.y * zoom,
          zoom,
        });
      void positionCanvas.finally(() => {
        setCanvasViewport(flowInstance.getViewport());
      });
    });
  }, [flowInstance, displayedFlowNodes, displayedFlowEdges, canvasVisibleWorkflowGraphs, isInspectMode, isTutorialMode]);

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
  ): void {
    return nodeDraftActions.hydrateInspectorDraftFromNode(nextNode, nextCatalogEntry);
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
    const restoredInspectorNodeId =
      selectedNodeId && restoredNodeIds.has(selectedNodeId)
        ? selectedNodeId
        : nextSelectedNodeIds[0] ?? null;
    const restoredInspectorNode = restoredInspectorNodeId
      ? nodeById(restoredDocument.config, restoredInspectorNodeId)
      : undefined;
    hydrateInspectorDraftFromNode(
      restoredInspectorNode,
      restoredInspectorNode
        ? getCatalogEntry(catalog, restoredInspectorNode.type)
        : undefined,
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

  function setProgrammaticSelection(nextNodeIds: string[], preferredNodeId: string | null = null): void {
    return selectionActions.setProgrammaticSelection(nextNodeIds, preferredNodeId);
  }

  function updateNodePositions(changes: NodeChange[]): void {
    return canvasViewportActions.updateNodePositions(changes);
  }

  function updateDraggedNodePositions(activeNode: Node<GraphNodeData>, draggedNodes: Node<GraphNodeData>[]): void {
    return canvasViewportActions.updateDraggedNodePositions(activeNode, draggedNodes);
  }

  function updateEdgeChanges(changes: Parameters<typeof applyEdgeChanges>[0]): void {
    return canvasViewportActions.updateEdgeChanges(changes);
  }

  function handleFlowInit(instance: ReactFlowInstance<Node<GraphNodeData>, Edge>): void {
    return canvasViewportActions.handleFlowInit(instance);
  }

  function fitWorkflowCanvas(): void {
    return canvasViewportActions.fitWorkflowCanvas();
  }

  function selectCompactCanvasMode(nextMode: CompactCanvasMode): void {
    return canvasViewportActions.selectCompactCanvasMode(nextMode);
  }

  function toggleCompactPanel(panel: Exclude<CompactActivePanel, null>): void {
    return canvasViewportActions.toggleCompactPanel(panel);
  }

  function canvasPointFromScreenPoint(clientX: number, clientY: number): CanvasPoint | null {
    return canvasViewportActions.canvasPointFromScreenPoint(clientX, clientY);
  }

  function flowPositionAtCanvasCursor(): { x: number; y: number } | null {
    return canvasViewportActions.flowPositionAtCanvasCursor();
  }

  function nodePositionFromScreenPoint(clientX: number, clientY: number): { x: number; y: number } | null {
    return canvasViewportActions.nodePositionFromScreenPoint(clientX, clientY);
  }

  function flowPositionAtCanvasCenter(): { x: number; y: number } | null {
    return canvasViewportActions.flowPositionAtCanvasCenter();
  }

  function nodePositionAtCanvasCenter(): { x: number; y: number } | null {
    return canvasViewportActions.nodePositionAtCanvasCenter();
  }

  function nodeCenterCanvasPoint(node: Node<GraphNodeData>): CanvasPoint {
    return canvasViewportActions.nodeCenterCanvasPoint(node);
  }

  function closeConnectionUi(): void {
    return canvasConnectionActions.closeConnectionUi();
  }

  function startPendingConnection(route: WorkflowConnectionRouteOption): void {
    return canvasConnectionActions.startPendingConnection(route);
  }

  function updatePendingConnectionCursor(clientX: number, clientY: number): void {
    return canvasConnectionActions.updatePendingConnectionCursor(clientX, clientY);
  }

  function startNodeTypeDrag(
    event: ReactDragEvent<HTMLButtonElement>,
    entry: WorkflowEditorNodeCatalogEntry,
  ): void {
    return canvasConnectionActions.startNodeTypeDrag(event, entry);
  }

  function handleNodeTypeDragOver(event: ReactDragEvent<HTMLElement>): void {
    return canvasConnectionActions.handleNodeTypeDragOver(event);
  }

  function handleNodeTypeDrop(event: ReactDragEvent<HTMLElement>): void {
    return canvasConnectionActions.handleNodeTypeDrop(event);
  }

  function handleNodeContextMenu(event: React.MouseEvent, node: Node<GraphNodeData>): void {
    return canvasConnectionActions.handleNodeContextMenu(event, node);
  }

  function handleWorkflowNodeClick(event: React.MouseEvent, node: Node<GraphNodeData>): void {
    return canvasConnectionActions.handleWorkflowNodeClick(event, node);
  }

  function handleCanvasPointerEnter(event: ReactPointerEvent<HTMLElement>): void {
    return canvasViewportActions.handleCanvasPointerEnter(event);
  }

  function handleCanvasPointerMove(event: ReactPointerEvent<HTMLElement>): void {
    return canvasViewportActions.handleCanvasPointerMove(event);
  }

  function handleCanvasPointerLeave(): void {
    return canvasViewportActions.handleCanvasPointerLeave();
  }

  function handleCanvasTouchStartCapture(event: ReactTouchEvent<HTMLElement>): void {
    return canvasViewportActions.handleCanvasTouchStartCapture(event);
  }

  function handleCanvasTouchMoveCapture(event: ReactTouchEvent<HTMLElement>): void {
    return canvasViewportActions.handleCanvasTouchMoveCapture(event);
  }

  function handleCanvasTouchEndCapture(event: ReactTouchEvent<HTMLElement>): void {
    return canvasViewportActions.handleCanvasTouchEndCapture(event);
  }

  function handleCanvasTouchCancelCapture(event: ReactTouchEvent<HTMLElement>): void {
    return canvasViewportActions.handleCanvasTouchCancelCapture(event);
  }

  function handlePaneClick(): void {
    return canvasConnectionActions.handlePaneClick();
  }

  function handlePaneContextMenu(event: MouseEvent | React.MouseEvent): void {
    return canvasConnectionActions.handlePaneContextMenu(event);
  }

  function deleteSelectedEdge(): void {
    return selectionActions.deleteSelectedEdge();
  }

  function deleteNodes(nodeIds: string[]): void {
    return selectionActions.deleteNodes(nodeIds);
  }

  function handleAppShellKeyDown(event: React.KeyboardEvent<HTMLDivElement>): void {
    return selectionActions.handleAppShellKeyDown(event);
  }

  function persistNodePositions(activeNode: Node<GraphNodeData>, draggedNodes: Node<GraphNodeData>[]): void {
    return canvasViewportActions.persistNodePositions(activeNode, draggedNodes);
  }

  function addNode(kind: WorkflowNodeKind, type: string, requestedPosition?: { x: number; y: number } | null): void {
    return selectionActions.addNode(kind, type, requestedPosition);
  }

  function deleteSelectedNode(): void {
    return selectionActions.deleteSelectedNode();
  }

  function selectDownstreamBranch(): void {
    return selectionActions.selectDownstreamBranch();
  }

  function selectWorkflowGraph(graph: WorkflowGraphMetadata): void {
    return selectionActions.selectWorkflowGraph(graph);
  }

  function applyWorkflowGraphChanges(): void {
    return selectionActions.applyWorkflowGraphChanges();
  }

  function resetSelectedNodeChanges(): void {
    return nodeDraftActions.resetSelectedNodeChanges();
  }

  function stageInspectorDraft(nextDraft: Partial<InspectorDraftSnapshot> = {}): void {
    return nodeDraftActions.stageInspectorDraft(nextDraft);
  }

  function onConnect(connection: Connection): void {
    return canvasConnectionActions.onConnect(connection);
  }

  function onEdgesDelete(deletedEdges: Edge[]): void {
    return canvasConnectionActions.onEdgesDelete(deletedEdges);
  }

  function onNodesDelete(deletedNodes: Node[]): void {
    return canvasConnectionActions.onNodesDelete(deletedNodes);
  }

  function handleWorkflowPackChange(event: ReactChangeEvent<HTMLSelectElement>): Promise<void> {
    return workflowPackActions.handleWorkflowPackChange(event);
  }

  function openCreateWorkflowPackModal(): void {
    return workflowPackActions.openCreateWorkflowPackModal();
  }

  function openEditWorkflowPackModal(pack: WorkflowPackDescriptor | null = selectedWorkflowPack): void {
    return workflowPackActions.openEditWorkflowPackModal(pack);
  }

  function closeWorkflowPackModal(): void {
    return workflowPackActions.closeWorkflowPackModal();
  }

  function submitWorkflowPackModal(event: ReactFormEvent<HTMLFormElement>): Promise<void> {
    return workflowPackActions.submitWorkflowPackModal(event);
  }

  function removeSelectedWorkflowPack(pack: WorkflowPackDescriptor | null = selectedWorkflowPack): Promise<boolean> {
    return workflowPackActions.removeSelectedWorkflowPack(pack);
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
    if (!document || workflowMutationLocked || inspectorDraftError) return;
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
      setSavedDocument(normalizedSavedDocument);
      setUndoHistory([]);
      setNodeEditOrigins({});
      applyWorkflowDocumentState(
        normalizedSavedDocument,
        selectedNodeIds,
        saved.validation_diagnostics,
        { markClean: true, nodeEditOrigins: {} },
      );
      setSaveState("saved");
      void loadWorkflowPackHistory(saved.selected_workflow_pack?.reference ?? selectedWorkflowPackReference);
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

  function updateSelectedTriggerEnabled(nextEnabled: boolean): void {
    return nodeFieldActions.updateSelectedTriggerEnabled(nextEnabled);
  }

  function updateKnownField(key: string, value: unknown): void {
    return nodeFieldActions.updateKnownField(key, value);
  }

  function updateClassificationFields(patch: Record<string, unknown>, answers?: Record<string, unknown>): void {
    return nodeFieldActions.updateClassificationFields(patch, answers);
  }

  function updateAdoQueryModeField(value: string): void {
    return nodeFieldActions.updateAdoQueryModeField(value);
  }

  function updateToolArgumentPolicyJsonDisplayMode(
    field: WorkflowEditorFieldDefinition,
    nextMode: JsonDisplayMode,
  ): void {
    return nodeFieldActions.updateToolArgumentPolicyJsonDisplayMode(field, nextMode);
  }

  function mutateToolArgumentPolicyDraftRows(
    fieldKey: string,
    updater: (rows: ToolArgumentPolicyDraftRow[]) => ToolArgumentPolicyDraftRow[],
  ): void {
    return nodeFieldActions.mutateToolArgumentPolicyDraftRows(fieldKey, updater);
  }

  function updateFieldMode(key: string, mode: "literal" | "bind" | "template"): void {
    return nodeFieldActions.updateFieldMode(key, mode);
  }

  function updateBindableEnumFieldMode(
    field: WorkflowEditorFieldDefinition,
    value: unknown,
    nextMode: "literal" | "bind" | "template",
  ): void {
    return nodeFieldActions.updateBindableEnumFieldMode(field, value, nextMode);
  }

  function copyBindingExpression(bindingExpression: string): Promise<void> {
    return nodeFieldActions.copyBindingExpression(bindingExpression);
  }

  function updateEffectTargetKey(nextKey: EffectTargetKey): void {
    return nodeFieldActions.updateEffectTargetKey(nextKey);
  }

  function renderEffectTargetField(): ReactNode | null {
    return fieldRenderers.renderEffectTargetField();
  }

  function renderSelectedNodeOutputContract(): ReactNode | null {
    return fieldRenderers.renderSelectedNodeOutputContract();
  }

  function renderSelectedTriggerToggle(): ReactNode | null {
    return fieldRenderers.renderSelectedTriggerToggle();
  }

  function updateStringListField(key: string, value: string): void {
    return nodeFieldActions.updateStringListField(key, value);
  }

  function addStringMapRow(key: string): void {
    return nodeFieldActions.addStringMapRow(key);
  }

  function updateStringMapRow(
    fieldKey: string,
    rowId: string,
    key: "key" | "value",
    value: string,
  ): void {
    return nodeFieldActions.updateStringMapRow(fieldKey, rowId, key, value);
  }

  function removeStringMapRow(fieldKey: string, rowId: string): void {
    return nodeFieldActions.removeStringMapRow(fieldKey, rowId);
  }

  function updateObjectListItem(fieldKey: string, index: number, key: string, value: unknown): void {
    return nodeFieldActions.updateObjectListItem(fieldKey, index, key, value);
  }

  function addObjectListItem(key: string, field: WorkflowEditorFieldDefinition): void {
    return nodeFieldActions.addObjectListItem(key, field);
  }

  function removeObjectListItem(key: string, index: number): void {
    return nodeFieldActions.removeObjectListItem(key, index);
  }

  function mutateSessionListMetadataRuleRows(
    updater: (rows: SessionListMetadataRuleDraftRow[]) => SessionListMetadataRuleDraftRow[],
  ): void {
    return nodeFieldActions.mutateSessionListMetadataRuleRows(updater);
  }

  function renderField(field: WorkflowEditorFieldDefinition): ReactNode | null {
    return fieldRenderers.renderField(field);
  }

  function renderWorkflowGeneratorCanvasPanel(): ReactNode {
    return packPanelRenderers.renderWorkflowGeneratorCanvasPanel();
  }

  function renderWorkflowPackModal(): ReactNode | null {
    return packPanelRenderers.renderWorkflowPackModal();
  }

  function renderWorkflowTutorialChooser(): ReactNode | null {
    return tutorialPanelRenderers.renderWorkflowTutorialChooser();
  }

  function renderWorkflowTutorialLauncher(): ReactNode {
    return tutorialPanelRenderers.renderWorkflowTutorialLauncher();
  }

  function renderInspectedRunPanel(): ReactNode {
    return runPanelRenderers.renderInspectedRunPanel();
  }

  function renderInspectedNodePanel(): ReactNode {
    return runPanelRenderers.renderInspectedNodePanel();
  }

  function renderRunSearchResult(run: WorkflowRunSearchItem): ReactNode {
    return runPanelRenderers.renderRunSearchResult(run);
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
    "--workflow-canvas-share": `${workbench.canvasShare}fr`,
    "--workflow-editor-share": `${100 - workbench.canvasShare}fr`,
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

  const runPanelRenderers = createRunPanelRenderers({
    operatorSteeringNotesByKey,
    operatorSteeringDraftsByKey,
    operatorSteeringLoadingByKey,
    operatorSteeringSubmittingByKey,
    operatorSteeringErrorsByKey,
    canControlWorkflowRuns,
    privilegedUnlocked,
    submitOperatorSteeringNote,
    setOperatorSteeringDraftsByKey,
    setOperatorSteeringError,
    loadOperatorSteeringNotes,
    operatorSteeringCancellingById,
    cancelOperatorSteeringNote,
    runDetail,
    inspectVisitedNodeIds,
    inspectDisplayNodeIds,
    exitInspectMode,
    selectedNodeId,
    selectedInspectGraphNode,
    selectedInspectNodeDetail,
    selectedInspectCatalogEntry,
    inspectWorkflowRun,
    runDetailLoading,
    workflowRunReadLocked,
    canReadWorkflowRuns,
    visibleRuns,
    runSnapshotError,
    submitWorkflowRunDetailLookup,
    runDetailLookupKind,
    setRunDetailLookupKind,
    runDetailLookup,
    setRunDetailLookup,
    runDetailError,
    runSearchResult,
    submitWorkflowRunSearch,
    runSearchScope,
    setRunSearchScope,
    runSearchScopeOptions,
    runSearchWithoutSession,
    setRunSearchWithoutSession,
    runSearchIncludeCompleted,
    setRunSearchIncludeCompleted,
    runSearchLoading,
    selectedRunSearchScope,
    runSearchError,
    resumeWorkflowRun,
    resumingRunIds,
    workflowRunControlLocked,
    clearWorkflowRun,
    clearingRunIds,
  });

  const packPanelRenderers = createPackPanelRenderers({
    workflowGeneratorOpen,
    config,
    workflowGeneratorDescription,
    workflowGeneratorState,
    workflowEditLocked,
    workflowGeneratorReadyDescription,
    workflowGeneratorModel,
    handleWorkflowGeneratorDescriptionChange,
    workflowGeneratorError,
    workflowGeneratorWarnings,
    generateWorkflowGeneratorDraft,
    acceptWorkflowGeneratorDraft,
    cancelWorkflowGenerator,
    workflowPackModalMode,
    workflowPackModalTarget,
    workflowPackOptions,
    closeWorkflowPackModal,
    workflowPackSubmitting,
    submitWorkflowPackModal,
    workflowPackForm,
    setWorkflowPackForm,
    workflowPackError,
    removeSelectedWorkflowPack,
    setWorkflowPackModalMode,
    setWorkflowPackModalTargetReference,
    workflowPackManageLocked,
    workflowPackHistoryDetail,
    setWorkflowPackHistoryDetail,
    selectedWorkflowPack,
    workflowPackHistoryLoading,
    workflowPackHistoryLoadingMore,
    workflowPackHistoryError,
    workflowPackHistory,
    loadWorkflowPackHistoryDetail,
    workflowPackHistoryDetailLoading,
    workflowPackHistoryNextCursor,
    loadWorkflowPackHistory,
    selectedWorkflowPackReference,
    workflowReadLocked,
    document,
    isInspectMode,
    openWorkflowGenerator,
    handleWorkflowPackChange,
    isTutorialMode,
    canManageWorkflowPacks,
    workflowPackHistoryOpen,
    toggleWorkflowPackHistoryPanel,
    openEditWorkflowPackModal,
    openCreateWorkflowPackModal,
  });

  const tutorialPanelRenderers = createTutorialPanelRenderers({
    workflowTutorialChooserOpen,
    setWorkflowTutorialChooserOpen,
    workflowTutorialChooserStatusFor,
    startWorkflowTutorial,
    restartWorkflowTutorial,
    skipWorkflowTutorial,
    clearWorkflowTutorialProgressStatus,
    workflowTutorialChooserStatusLabel,
    hasUnfinishedUnskippedTutorials,
    unfinishedUnskippedTutorialCount,
  });

  const inspectorPanelRenderers = createInspectorPanelRenderers({
    inspectorTab: workbench.tab,
    setInspectorTab: workbench.setTab,
    selectedNodeIds,
    setProgrammaticSelection,
    workflowEditLocked,
    selectDownstreamBranch,
    selectedNodeId,
    deleteSelectedNode,
    workflowMutationLocked,
    nodeExecutionHistory,
    selectedNode,
    canReadWorkflowRuns,
    inspectWorkflowRun,
    selectedNodeDiagnostics,
    renderSelectedTriggerToggle,
    manualEventTriggerButton: manualEventTrigger.button,
    inspectorDisplayName,
    setInspectorDisplayName,
    stageInspectorDraft,
    renderEffectTargetField,
    inspectorConfigurationFields,
    renderField,
    inspectorUnknownValues,
    rawJsonDraft,
    inspectorOutputFields,
    renderSelectedNodeOutputContract,
    selectedNodeHasUnsavedChanges,
    resetSelectedNodeChanges,
    selectedEdge,
    selectedEdgeRouteLabel,
    deleteSelectedEdge,
    isInspectMode,
    renderInspectedRunPanel,
    renderInspectedNodePanel,
    runDetailError,
    error,
  });

  const documentPanelRenderers = createDocumentPanelRenderers({
    isTutorialMode,
    createEntryCount,
    tutorialCurrentRequiredAction,
    compactCreateSearch,
    createEntriesByKind,
    setCompactCreateSearch,
    tutorialAllowsNodeType,
    workflowMutationLocked,
    startNodeTypeDrag,
    addNode,
    setCompactActivePanel,
    validateDocument,
    workflowReadLocked,
    undoWorkflowChange,
    canUndoWorkflowChanges,
    saveDocument,
    saveState,
    savePanelStatusTone,
    hasUnsavedWorkflowChanges,
    dirtyNodeSummary,
    error,
    document,
    visibleWorkflowGraphs,
    graphViewContext,
    selectedGraphId,
    selectWorkflowGraph,
    workflowEditLocked,
    selectedGraph,
    graphNameDraft,
    setGraphNameDraft,
    graphDescriptionDraft,
    setGraphDescriptionDraft,
    graphDirectoryDraft,
    selectedDirectoryPath,
    setGraphDirectoryDraft,
    graphUsersDraft,
    setGraphUsersDraft,
    applyWorkflowGraphChanges,
  });

  const compactPanelRenderers = createCompactPanelRenderers({
    compactCanvasMode,
    selectCompactCanvasMode,
    compactActivePanel,
    hasUnsavedWorkflowChanges,
    toggleCompactPanel,
    fitWorkflowCanvas,
    setCompactActivePanel,
    flowInstance,
    undoWorkflowChange,
    canUndoWorkflowChanges,
    workflowMutationLocked,
    saveDocument,
    saveState,
    workflowRunReadLocked,
    createEntryCount,
    renderCreatePanelBody,
    renderCompactInspectorPanelContent,
    renderRunsPanelContent,
    renderGraphsPanelContent,
    renderSavePanelContent,
  });

  function renderCompactToolbar(): ReactNode {
    return compactPanelRenderers.renderCompactToolbar();
  }

  function renderCreatePanelBody(compact = false): ReactNode {
    return documentPanelRenderers.renderCreatePanelBody(compact);
  }

  function toggleWorkflowPackHistoryPanel(): void {
    return workflowPackActions.toggleWorkflowPackHistoryPanel();
  }

  function renderWorkflowGeneratorCanvasControl(): ReactNode | null {
    return packPanelRenderers.renderWorkflowGeneratorCanvasControl();
  }

  function renderWorkflowPackCanvasControl(): ReactNode | null {
    return packPanelRenderers.renderWorkflowPackCanvasControl();
  }

  function renderWorkflowPackHistoryCanvasPanel(): ReactNode | null {
    return packPanelRenderers.renderWorkflowPackHistoryCanvasPanel();
  }

  function renderSavePanelContent(): ReactNode {
    return documentPanelRenderers.renderSavePanelContent();
  }

  function renderGraphsPanelContent(): ReactNode {
    return documentPanelRenderers.renderGraphsPanelContent();
  }

  function renderRunsPanelContent(): ReactNode {
    return runPanelRenderers.renderRunsPanelContent();
  }

  function renderNodeExecutionHistory(): ReactNode {
    return inspectorPanelRenderers.renderNodeExecutionHistory();
  }

  function renderCompactInspectorPanelContent(): ReactNode | null {
    return inspectorPanelRenderers.renderCompactInspectorPanelContent();
  }

  function renderCompactBottomSheet(): ReactNode | null {
    return compactPanelRenderers.renderCompactBottomSheet();
  }

  return (
    <div className="admin-page workflow-embedded-page" onKeyDown={handleAppShellKeyDown}>
      <header className="workflow-command-bar">
        <div className="workflow-command-title">
          <h1>{isInspectMode ? "Run inspection" : "Workflow"}</h1>
          <span>{selectedWorkflowPack?.display_name || selectedWorkflowPack?.reference || "Workflow editor"}</span>
        </div>
        {!isCompactCanvasTouchMode ? <div className="workflow-command-tools">
          <button type="button" aria-expanded={compactActivePanel === "graphs"} onClick={() => setCompactActivePanel(current => current === "graphs" ? null : "graphs")}>Graphs</button>
          {!isInspectMode ? <button type="button" aria-expanded={compactActivePanel === "create"} onClick={() => setCompactActivePanel(current => current === "create" ? null : "create")} disabled={workflowMutationLocked}>Add node</button> : null}
          {renderWorkflowTutorialLauncher()}
          {workbench.view === "canvas" ? <button type="button" onClick={() => workbench.setView("split")}>Show editor</button> : null}
          {workbench.view === "editor" ? <button type="button" onClick={() => workbench.setView("split")}>Show canvas</button> : null}
        </div> : null}
        {!isInspectMode ? (
          <>
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
            <div className="workflow-command-actions">
              <button type="button" onClick={fitWorkflowCanvas} disabled={!flowInstance}>Fit</button>
              <button type="button" onClick={() => void validateDocument()} disabled={workflowReadLocked}>Validate</button>
              <button type="button" onClick={undoWorkflowChange} disabled={!canUndoWorkflowChanges}>Undo</button>
              <button type="button" className="primary" onClick={() => void saveDocument()} disabled={workflowMutationLocked || Boolean(inspectorDraftError)}>
                {saveState === "saving" ? "Saving..." : "Save workflow"}
              </button>
            </div>
          </>
        ) : null}
      </header>
      {!isInspectMode && error ? <div className="error-box save-error-box workflow-command-error">{error}</div> : null}
      <div
        className={`app-shell workflow-editor-shell workflow-lower-workbench-shell view-${workbench.view}${workbench.isResizing ? " is-resizing-workbench" : ""}${isInspectMode ? " is-inspect-mode" : ""}${isCompactCanvasTouchMode ? ` compact-mode-${compactCanvasMode}` : ""}`}
        ref={appShellRef}
        style={appShellStyle}
        tabIndex={-1}
      >
        {!isCompactCanvasTouchMode && (compactActivePanel === "create" || compactActivePanel === "graphs") ? (
          <aside className="sidebar workflow-tool-popover" role="dialog" aria-modal="false" aria-label={compactActivePanel === "create" ? "Add node" : "Graphs"} onKeyDown={event => {
            if (event.key !== "Escape") return;
            event.preventDefault(); event.stopPropagation();
            (globalThis.document.querySelector('.workflow-command-tools button[aria-expanded="true"]') as HTMLButtonElement | null)?.focus();
            setCompactActivePanel(null);
          }}>
            <div className="workflow-tool-popover-heading"><strong>{compactActivePanel === "create" ? "Add node" : "Graphs"}</strong><button type="button" onClick={() => setCompactActivePanel(null)}>Close</button></div>
            {compactActivePanel === "create" ? documentPanelRenderers.renderCreatePanelBody(true) : <>{documentPanelRenderers.renderGraphsPanelContent()}{documentPanelRenderers.renderSourceFilesPanel()}</>}
          </aside>
        ) : null}

        {renderWorkflowTutorialChooser()}
        {renderWorkflowPackModal()}
        {manualEventTrigger.dialog}
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
            {renderWorkflowGeneratorCanvasControl()}
            {renderWorkflowGeneratorCanvasPanel()}
            {renderWorkflowPackCanvasControl()}
            {renderWorkflowPackHistoryCanvasPanel()}
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
              nodesDraggable={!workflowMutationLocked && (!isCompactCanvasTouchMode || compactCanvasMode !== "connect")}
              nodesConnectable={!workflowMutationLocked}
              elementsSelectable={!workflowEditLocked}
              fitView={isInspectMode || isTutorialMode}
              deleteKeyCode={workflowMutationLocked ? null : ["Backspace", "Delete"]}
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
                  style={
                    {
                      left: `${label.left}px`,
                      top: `${label.top}px`,
                      "--workflow-graph-label-scale": Math.max(0.45, Math.min(1, canvasViewport.zoom)),
                    } as CSSProperties
                  }
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
          {isCompactCanvasTouchMode ? renderCompactBottomSheet() : null}
          </div>
          {!isCompactCanvasTouchMode ? <>
            <div className="workflow-workbench-resize-handle" role="separator" aria-label="Resize workflow workbench" aria-orientation="horizontal" aria-valuemin={20} aria-valuemax={80} aria-valuenow={Math.round(workbench.canvasShare)} tabIndex={0} onPointerDown={workbench.startResize} onKeyDown={workbench.resizeWithKeyboard} />
            <section className="workflow-workbench sidebar" aria-label="Workflow workbench">
              <div className="workflow-workbench-bar">
                <div className="workflow-workbench-panels" role="tablist" aria-label="Workbench panels">
                  {(["inspector", "runs", "checks"] as const).map(panel => <button key={panel} type="button" role="tab" id={`workbench-${panel}-tab`} aria-controls={`workbench-${panel}-panel`} aria-selected={workbenchPanel === panel} disabled={panel === "runs" && workflowRunReadLocked} onClick={() => setWorkbenchPanel(panel)}>{panel === "inspector" ? isInspectMode ? "Run inspection" : "Inspector" : panel === "runs" ? "Runs" : "Checks"}</button>)}
                </div>
                <div className="workflow-workbench-actions">
                  <button type="button" onClick={() => workbench.setView(workbench.view === "editor" ? "split" : "editor")}>{workbench.view === "editor" ? "Restore canvas" : "Expand editor"}</button>
                  <button type="button" onClick={() => workbench.setView("canvas")}>Collapse editor</button>
                </div>
              </div>
              <div className="workflow-workbench-content">
                {editLocked && !isInspectMode ? <div className="callout warning-callout workflow-lock-callout">Unlock privileged operations to modify workflow state.</div> : null}
                <div role="tabpanel" id="workbench-inspector-panel" aria-labelledby="workbench-inspector-tab" hidden={workbenchPanel !== "inspector"}>{renderCompactInspectorPanelContent()}</div>
                <div role="tabpanel" id="workbench-runs-panel" aria-labelledby="workbench-runs-tab" hidden={workbenchPanel !== "runs"}>{runPanelRenderers.renderRunsPanelContent()}</div>
                <div role="tabpanel" id="workbench-checks-panel" aria-labelledby="workbench-checks-tab" hidden={workbenchPanel !== "checks"}>
                  <div className="section-label">Workflow checks</div>
                  {inspectorDraftError ? <div className="error-box" role="alert">{inspectorDraftError}</div> : null}
                  {validationDiagnostics.length === 0 ? <p className="field-description">No current validation diagnostics.</p> : validationDiagnostics.map((diagnostic, index) => <div className="problem-item" key={index}><div className="problem-message">{diagnostic.message}</div><div className="problem-meta">{diagnostic.node_id} {diagnostic.field_path}</div></div>)}
                  {document ? <div className="path-note">{document.config_path}<br />{document.layout_path}</div> : null}
                </div>
              </div>
            </section>
          </> : null}
        </div>
    </div>
  );
}

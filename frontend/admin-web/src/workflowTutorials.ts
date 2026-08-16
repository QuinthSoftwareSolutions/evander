import type {
  WorkflowConfig,
  WorkflowEditorCatalog,
  WorkflowEditorDocument,
  WorkflowEditorFieldDefinition,
  WorkflowEditorNodeCatalogEntry,
  WorkflowEditorNodePortDefinition,
  WorkflowGraphMetadata,
  WorkflowLayout,
  WorkflowNodeKind,
} from "./types";

export type WorkflowTutorialCameraMode = "none" | "center-node" | "fit-node";

export type WorkflowTutorialBubblePlacement = "center" | "top" | "right" | "bottom" | "left";

export type WorkflowTutorialHighlightMode = "none" | "pulse" | "spotlight";

export type WorkflowTutorialTarget =
  | {
      kind: "canvas";
      nodeId: null;
    }
  | {
      kind: "node";
      nodeId: string;
    };

export type WorkflowTutorialRequiredAction =
  | {
      kind: "next";
    }
  | {
      kind: "select-node";
      nodeId: string;
    }
  | {
      kind: "add-node";
      nodeKind: WorkflowNodeKind;
      nodeType: string;
      nodeId: string;
      position: { x: number; y: number };
    }
  | {
      kind: "connect";
      sourceNodeId: string;
      sourcePort: string | null;
      targetNodeId: string;
    }
  | {
      kind: "bind-field";
      nodeId: string;
      fieldKey: string;
      bindingExpression: string;
    }
  | {
      kind: "reset";
    };

export type WorkflowTutorialStep = {
  id: string;
  title: string;
  body: string;
  target: WorkflowTutorialTarget;
  bubblePlacement: WorkflowTutorialBubblePlacement;
  camera: WorkflowTutorialCameraMode;
  highlightMode: WorkflowTutorialHighlightMode;
  requiredAction: WorkflowTutorialRequiredAction;
};

export type WorkflowTutorialDefinition = {
  id: string;
  title: string;
  description: string;
  catalog: WorkflowEditorCatalog;
  seedDocument: WorkflowEditorDocument;
  document: WorkflowEditorDocument;
  steps: WorkflowTutorialStep[];
};

export type WorkflowTutorialSession = {
  tutorialId: string;
  stepIndex: number;
  document: WorkflowEditorDocument;
  selectedNodeId: string | null;
  selectedEdgeId: string | null;
};

const FIRST_STEPS_TUTORIAL_ID = "first-steps";
const TUTORIAL_TRIGGER_NODE_ID = "tutorial-trigger";
const TUTORIAL_EFFECT_NODE_ID = "tutorial-effect";
const TUTORIAL_GRAPH_ID = "tutorial:first-steps";

const TUTORIAL_TRIGGER_OUTPUTS: ReadonlyArray<WorkflowEditorNodePortDefinition> = [
  {
    key: "completed",
    label: "Completed",
    description: "Completion output to route or bind into downstream steps.",
  },
];

const TUTORIAL_PROMPT_FIELD: WorkflowEditorFieldDefinition = {
  key: "prompt",
  label: "Prompt",
  kind: "string",
  required: true,
  bindable: true,
  read_only: false,
  multiline: true,
  description: "Prompt text that can be literal or bound from upstream outputs.",
  placeholder: "Describe the next action.",
  default_value: "Describe the next action.",
  options: [],
  item_fields: [],
};

const TUTORIAL_TRIGGER_CATALOG_ENTRY: WorkflowEditorNodeCatalogEntry = {
  kind: "trigger",
  type: "trigger:agent:run:completed",
  label: "Agent run completed",
  description: "Start when a guest agent run completes.",
  fields: [],
  input_ports: [],
  output_ports: [...TUTORIAL_TRIGGER_OUTPUTS],
};

const TUTORIAL_EFFECT_CATALOG_ENTRY: WorkflowEditorNodeCatalogEntry = {
  kind: "effect",
  type: "effect:agent:prompt",
  label: "Prompt agent",
  description: "Send a follow-up prompt to an agent session.",
  fields: [TUTORIAL_PROMPT_FIELD],
  input_ports: [
    {
      key: "default",
      label: "Default",
      description: "Primary prompt input.",
    },
  ],
  output_ports: [],
};

const TUTORIAL_CATALOG: WorkflowEditorCatalog = {
  worker_id: "trigger-orchestrator",
  nodes: [TUTORIAL_TRIGGER_CATALOG_ENTRY, TUTORIAL_EFFECT_CATALOG_ENTRY],
  internal_events: [],
};

const TUTORIAL_GRAPH: WorkflowGraphMetadata = {
  id: TUTORIAL_GRAPH_ID,
  name: "First steps",
  description: "Isolated tutorial graph for the first workflow editor walkthrough.",
  directory_path: "root",
  user_ids: [],
  node_ids: [TUTORIAL_TRIGGER_NODE_ID, TUTORIAL_EFFECT_NODE_ID],
};

const TUTORIAL_LAYOUT: WorkflowLayout = {
  nodes: {
    [TUTORIAL_TRIGGER_NODE_ID]: { x: 180, y: 180 },
    [TUTORIAL_EFFECT_NODE_ID]: { x: 540, y: 180 },
  },
};

const TUTORIAL_TRIGGER_NODE = {
  id: TUTORIAL_TRIGGER_NODE_ID,
  display_name: "Agent run completed",
  type: "trigger:agent:run:completed",
  config: {},
} satisfies WorkflowConfig["triggers"][number];

const TUTORIAL_EFFECT_NODE = {
  id: TUTORIAL_EFFECT_NODE_ID,
  display_name: "Prompt agent",
  type: "effect:agent:prompt",
  config: {
    prompt: "${{tutorial-trigger.completed}}",
  },
} satisfies WorkflowConfig["effects"][number];

const TUTORIAL_SEED_DOCUMENT: WorkflowEditorDocument = {
  config_path: "tutorials/first-steps.yaml",
  layout_path: "tutorials/first-steps.layout.json",
  config_exists: false,
  layout_exists: false,
  config: {
    triggers: [TUTORIAL_TRIGGER_NODE],
    gates: [],
    effects: [],
    queries: [],
    edges: [],
  },
  layout: {
    nodes: {
      [TUTORIAL_TRIGGER_NODE_ID]: { x: 180, y: 180 },
    },
  },
  graphs: [
    {
      ...TUTORIAL_GRAPH,
      node_ids: [TUTORIAL_TRIGGER_NODE_ID],
    },
  ],
  output_schemas: {
    [TUTORIAL_TRIGGER_NODE_ID]: {
      completed: { type: "string" },
      event_id: { type: "string" },
      trace_id: { type: "string" },
    },
  },
  validation_diagnostics: [],
  revision: "tutorial:first-steps:seed",
};

const TUTORIAL_DOCUMENT: WorkflowEditorDocument = {
  config_path: "tutorials/first-steps.yaml",
  layout_path: "tutorials/first-steps.layout.json",
  config_exists: false,
  layout_exists: false,
  config: {
    triggers: [TUTORIAL_TRIGGER_NODE],
    gates: [],
    effects: [TUTORIAL_EFFECT_NODE],
    queries: [],
    edges: [
      {
        source_id: TUTORIAL_TRIGGER_NODE_ID,
        source_port: "completed",
        target_id: TUTORIAL_EFFECT_NODE_ID,
      },
    ],
  },
  layout: TUTORIAL_LAYOUT,
  graphs: [TUTORIAL_GRAPH],
  output_schemas: {
    [TUTORIAL_TRIGGER_NODE_ID]: {
      completed: { type: "string" },
      event_id: { type: "string" },
      trace_id: { type: "string" },
    },
  },
  validation_diagnostics: [],
  revision: "tutorial:first-steps:revision",
};

const TUTORIAL_FIRST_STEPS: WorkflowTutorialDefinition = {
  id: FIRST_STEPS_TUTORIAL_ID,
  title: "First steps",
  description: "Learn the basics of selecting, adding, connecting, and binding workflow nodes.",
  catalog: TUTORIAL_CATALOG,
  seedDocument: TUTORIAL_SEED_DOCUMENT,
  document: TUTORIAL_DOCUMENT,
  steps: [
    {
      id: "welcome",
      title: "Welcome",
      body: "This walkthrough covers the basics of configuring workflows.",
      target: { kind: "canvas", nodeId: null },
      bubblePlacement: "center",
      camera: "none",
      highlightMode: "none",
      requiredAction: { kind: "next" },
    },
    {
      id: "select-trigger",
      title: "Select the trigger",
      body: "The trigger starts the workflow. Click it to inspect its outputs and configuration.",
      target: { kind: "node", nodeId: TUTORIAL_TRIGGER_NODE_ID },
      bubblePlacement: "right",
      camera: "center-node",
      highlightMode: "pulse",
      requiredAction: { kind: "select-node", nodeId: TUTORIAL_TRIGGER_NODE_ID },
    },
    {
      id: "explain-trigger",
      title: "Trigger entrypoints",
      body: "The Completed output is the handoff point you will connect downstream.",
      target: { kind: "node", nodeId: TUTORIAL_TRIGGER_NODE_ID },
      bubblePlacement: "right",
      camera: "center-node",
      highlightMode: "pulse",
      requiredAction: { kind: "next" },
    },
    {
      id: "add-effect",
      title: "Add a downstream node",
      body: "Effects run downstream work. Add the Prompt agent effect so the trigger has somewhere to connect.",
      target: { kind: "node", nodeId: TUTORIAL_EFFECT_NODE_ID },
      bubblePlacement: "left",
      camera: "fit-node",
      highlightMode: "spotlight",
      requiredAction: {
        kind: "add-node",
        nodeKind: "effect",
        nodeType: "effect:agent:prompt",
        nodeId: TUTORIAL_EFFECT_NODE_ID,
        position: { x: 540, y: 180 },
      },
    },
    {
      id: "connect-nodes",
      title: "Connect the nodes",
      body: "Right-click the trigger, choose Completed, then click Prompt agent to wire the trigger output downstream.",
      target: { kind: "node", nodeId: TUTORIAL_TRIGGER_NODE_ID },
      bubblePlacement: "bottom",
      camera: "fit-node",
      highlightMode: "spotlight",
      requiredAction: {
        kind: "connect",
        sourceNodeId: TUTORIAL_TRIGGER_NODE_ID,
        sourcePort: "completed",
        targetNodeId: TUTORIAL_EFFECT_NODE_ID,
      },
    },
    {
      id: "bind-prompt",
      title: "Bind the prompt",
      body: "Bind the Prompt field to the trigger's Completed output so the effect receives the workflow data.",
      target: { kind: "node", nodeId: TUTORIAL_EFFECT_NODE_ID },
      bubblePlacement: "right",
      camera: "center-node",
      highlightMode: "pulse",
      requiredAction: {
        kind: "bind-field",
        nodeId: TUTORIAL_EFFECT_NODE_ID,
        fieldKey: "prompt",
        bindingExpression: "${{tutorial-trigger.completed}}",
      },
    },
    {
      id: "finish",
      title: "Finish",
      body: "You have the basic workflow shape. Reset the tutorial to practice again, or exit when you are done.",
      target: { kind: "canvas", nodeId: null },
      bubblePlacement: "center",
      camera: "none",
      highlightMode: "none",
      requiredAction: { kind: "reset" },
    },
  ],
};

export const WORKFLOW_TUTORIAL_DEFINITIONS: readonly WorkflowTutorialDefinition[] = [TUTORIAL_FIRST_STEPS];

const WORKFLOW_TUTORIAL_DEFINITION_BY_ID = new Map(
  WORKFLOW_TUTORIAL_DEFINITIONS.map((definition) => [definition.id, definition]),
);

export function workflowTutorialDefinitionById(id: string): WorkflowTutorialDefinition | undefined {
  return WORKFLOW_TUTORIAL_DEFINITION_BY_ID.get(id);
}

export function workflowTutorialStepById(
  definition: WorkflowTutorialDefinition,
  stepId: string,
): WorkflowTutorialStep | undefined {
  return definition.steps.find((step) => step.id === stepId);
}

export function workflowTutorialStepIndex(
  definition: WorkflowTutorialDefinition,
  stepId: string,
): number {
  return definition.steps.findIndex((step) => step.id === stepId);
}

export function workflowTutorialTargetNodeId(target: WorkflowTutorialTarget): string | null {
  return target.kind === "node" ? target.nodeId : null;
}

export function workflowTutorialRequiredActionMatches(
  expected: WorkflowTutorialRequiredAction,
  actual: WorkflowTutorialRequiredAction,
): boolean {
  if (expected.kind !== actual.kind) return false;
  switch (expected.kind) {
    case "next":
    case "reset":
      return true;
    case "select-node":
      return actual.kind === "select-node" && expected.nodeId === actual.nodeId;
    case "add-node":
      return (
        actual.kind === "add-node" &&
        expected.nodeKind === actual.nodeKind &&
        expected.nodeType === actual.nodeType &&
        expected.nodeId === actual.nodeId
      );
    case "connect":
      return (
        actual.kind === "connect" &&
        expected.sourceNodeId === actual.sourceNodeId &&
        expected.targetNodeId === actual.targetNodeId &&
        expected.sourcePort === actual.sourcePort
      );
    case "bind-field":
      return (
        actual.kind === "bind-field" &&
        expected.nodeId === actual.nodeId &&
        expected.fieldKey === actual.fieldKey &&
        expected.bindingExpression === actual.bindingExpression
      );
    default:
      return false;
  }
}

export function cloneWorkflowTutorialDocument(document: WorkflowEditorDocument): WorkflowEditorDocument {
  return JSON.parse(JSON.stringify(document)) as WorkflowEditorDocument;
}

export function createWorkflowTutorialSession(tutorialId = FIRST_STEPS_TUTORIAL_ID): WorkflowTutorialSession {
  const definition = workflowTutorialDefinitionById(tutorialId) ?? TUTORIAL_FIRST_STEPS;
  return {
    tutorialId: definition.id,
    stepIndex: 0,
    document: cloneWorkflowTutorialDocument(definition.seedDocument),
    selectedNodeId: null,
    selectedEdgeId: null,
  };
}

export const WORKFLOW_TUTORIAL_FIRST_STEPS = TUTORIAL_FIRST_STEPS;
export const WORKFLOW_TUTORIAL_FIRST_STEPS_DOCUMENT = TUTORIAL_DOCUMENT;
export const WORKFLOW_TUTORIAL_FIRST_STEPS_SEED_DOCUMENT = TUTORIAL_SEED_DOCUMENT;
export const WORKFLOW_TUTORIAL_CATALOG = TUTORIAL_CATALOG;

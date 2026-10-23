import assert from "node:assert/strict";
import test, { before } from "node:test";
import { createServer } from "vite";
import { initialInspectorDraft } from "../src/features/workflow-editor/inspectorDraftState.ts";

let createFieldRenderers;
let createNodeFieldActions;
before(async () => {
  const server = await createServer({ server: { middlewareMode: true }, appType: "custom", logLevel: "error" });
  try {
    ({ createFieldRenderers } = await server.ssrLoadModule("/src/features/workflow-editor/fieldRenderers.tsx"));
    ({ createNodeFieldActions } = await server.ssrLoadModule("/src/features/workflow-editor/nodeFieldActions.ts"));
  } finally {
    await server.close();
  }
}, { timeout: 10000 });

const speedField = {
  key: "fast_mode", label: "Speed", kind: "enum", bindable: true,
  options: [{ value: "false", label: "Standard" }, { value: "true", label: "Fast" }, { value: "ultrafast", label: "Ultrafast" }],
  metadata: { nullable: true, unset_label: "Session default", literal_values: { false: false, true: true, ultrafast: "ultrafast" } },
};

function controls(element, type) {
  if (!element || typeof element !== "object") return [];
  const children = [element.props?.children].flat(Infinity);
  return [...(element.type === type ? [element] : []), ...children.flatMap((child) => controls(child, type))];
}

function render(value, mode = "literal", field = speedField) {
  const updates = [];
  const actions = createNodeFieldActions({
    selectedNode: { type: "effect:agent:prompt" },
    inspector: {
      state: { ...initialInspectorDraft(), inspectorKnownValues: { fast_mode: value } },
      setters: {
        setInspectorFieldModes: () => {},
        setInspectorKnownValues: (next) => updates.push(["fast_mode", next.fast_mode]),
      },
    },
    selectedNodeBindingOptions: [],
    stageInspectorDraft: () => {},
    tutorialCurrentRequiredAction: () => null,
  });
  const { renderField } = createFieldRenderers({
    selectedNode: { type: "effect:agent:prompt" },
    inspectorKnownValues: { fast_mode: value },
    inspectorFieldModes: { fast_mode: mode },
    selectedNodeBindingOptions: [],
    workflowMutationLocked: false,
    updateKnownField: (key, next) => updates.push([key, next]),
    updateFieldMode: () => {},
    updateBindableEnumFieldMode: actions.updateBindableEnumFieldMode,
  });
  return { element: renderField(field), updates };
}

test("workflow speed edits preserve standard as a boolean and allow restoring inheritance", () => {
  for (const [stored, selection] of [[undefined, ""], [null, ""], [false, "false"], [true, "true"], ["ultrafast", "ultrafast"]]) {
    const { element, updates } = render(stored);
    const picker = controls(element, "select")[1];
    assert.equal(picker.props.value, selection);
    for (const value of ["true", "false", "ultrafast", ""]) picker.props.onChange({ target: { value } });
    assert.deepEqual(updates, [["fast_mode", true], ["fast_mode", false], ["fast_mode", "ultrafast"], ["fast_mode", undefined]]);
  }
});

test("leaving a speed binding restores inheritance instead of enabling Fast", () => {
  const { element, updates } = render("${{upstream.fast_mode}}", "bind");
  controls(element, "select")[0].props.onChange({ target: { value: "literal" } });
  assert.deepEqual(updates, [["fast_mode", undefined]]);
});

test("ordinary workflow booleans retain checkbox controls", () => {
  const { element, updates } = render(false, "literal", { ...speedField, kind: "boolean", metadata: {} });
  const checkbox = controls(element, "input")[0];
  assert.equal(checkbox.props.type, "checkbox");
  assert.equal(checkbox.props.checked, false);
  checkbox.props.onChange({ target: { checked: true } });
  assert.deepEqual(updates, [["fast_mode", true]]);
});

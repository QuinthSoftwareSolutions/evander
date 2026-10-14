import { CostCategoryInput } from "../reports/CostCategoryInput";
import { type Dispatch, type SetStateAction } from "react";
import { CopyIcon } from "../../features/admin/PageActionIcons";
import { Tooltip } from "../../features/admin/Tooltip";
import { WorkflowClassificationEditor } from "../../features/classification/WorkflowClassificationEditor";
import { WorkflowClassifierSwitchEditor } from "../../features/classification/WorkflowClassifierSwitchEditor";
import {
  type WorkflowEditorFieldDefinition,
  type WorkflowEditorNodeCatalogEntry,
  type WorkflowGraphMetadata,
  type WorkflowNodeConfig,
} from "../../types";
import { workflowCommunicationTargetInspectorState } from "../../workflowCommunicationTargets";
import { type ToolArgumentPolicyDraftRow } from "../../workflowToolArgumentPolicies";
import { workflowTriggerEnabled } from "../../workflowTriggerState";
import { AGENT_CREATE_NODE_TYPE } from "./editorConstants";
import {
  type EffectTargetKey,
  type InspectorDraftSnapshot,
  type JsonDisplayMode,
  type SessionListMetadataRuleDraftRow,
  type StringMapDraftRow,
  type WorkflowFieldOption,
} from "./editorTypes";
import { isRecord, objectListJsonDraftKey } from "./editorValues";
import {
  adoQueryModeHidesField,
  agentCreateFieldOptionsForBackend,
  effectiveAdoQueryMode,
  ensureDirectorySelectOption,
  enumOptionValue,
  enumLiteralValue,
  fieldSelectionOnly,
  fieldValueMode,
  groupToolOptions,
  insertBindingReference,
  isAdoQueryModeNodeType,
  isCommunicationEffectNodeType,
  isExactBindingValue,
  isSessionListMetadataFieldKey,
  normalizedAgentCreateExecutionBackend,
  renderBindingSelect,
  renderStringInput,
  stringMapRowsToObject,
  supportsStructuredToolArgumentPolicies,
  teamsDirectChatModeHidesField,
} from "./fieldModels";
import { SessionListMetadataRules } from "./SessionListMetadataRules";
import { ToolArgumentPolicyField } from "./ToolArgumentPolicyField";
import { MapTemplateField } from "./MapTemplateField";
import { type MapTemplateDraftRow } from "./mapTemplateDraft";

type FieldRenderersInputs = {
  hasCollapsedTargetUi: boolean;
  effectTargetField: WorkflowEditorFieldDefinition | undefined;
  inspectorKnownValues: Record<string, unknown>;
  effectTargetKey: "target" | "session_id" | "vm_name";
  inspectorFieldModes: Record<string, "literal" | "bind" | "template">;
  workflowMutationLocked: boolean;
  tutorialAllowsInspectorFieldEdit: (fieldKey: string) => boolean;
  selectedNode: WorkflowNodeConfig | undefined;
  effectTargetOptions: import("../../workflowTargetInventory").WorkflowTargetOption[];
  updateFieldMode: (key: string, mode: "literal" | "bind" | "template") => void;
  updateKnownField: (key: string, value: unknown) => void;
  selectedNodeBindingOptions: import("../../workflowNodeOutputs").WorkflowBindingOption[];
  updateEffectTargetKey: (nextKey: EffectTargetKey) => void;
  selectedNodeOutputContract: import("../../workflowNodeOutputs").WorkflowNodeOutputContract | null;
  copyBindingExpression: (bindingExpression: string) => Promise<void>;
  workflowEditLocked: boolean;
  copiedBindingExpression: string | null;
  outputCopyError: string | null;
  updateSelectedTriggerEnabled: (nextEnabled: boolean) => void;
  directoryPathOptions: import("../../features/workflow-editor/editorTypes").DirectorySelectOption[];
  updateStringListField: (key: string, value: string) => void;
  inspectorStringMapDrafts: Record<string, StringMapDraftRow[]>;
  updateStringMapRow: (fieldKey: string, rowId: string, key: "key" | "value", value: string) => void;
  removeStringMapRow: (fieldKey: string, rowId: string) => void;
  addStringMapRow: (key: string) => void;
  inspectorToolArgumentPolicyDrafts: Record<string, ToolArgumentPolicyDraftRow[]>;
  inspectorMapTemplateDrafts: Record<string, MapTemplateDraftRow[]>;
  setInspectorMapTemplateDrafts: Dispatch<SetStateAction<Record<string, MapTemplateDraftRow[]>>>;
  setInspectorJsonDisplayModes: Dispatch<SetStateAction<Record<string, JsonDisplayMode>>>;
  inspectorJsonDisplayModes: Record<string, JsonDisplayMode>;
  updateToolArgumentPolicyJsonDisplayMode: (field: WorkflowEditorFieldDefinition, nextMode: JsonDisplayMode) => void;
  mutateToolArgumentPolicyDraftRows: (fieldKey: string, updater: (rows: ToolArgumentPolicyDraftRow[]) => ToolArgumentPolicyDraftRow[]) => void;
  inspectorJsonDrafts: Record<string, string>;
  setInspectorJsonDrafts: Dispatch<SetStateAction<Record<string, string>>>;
  stageInspectorDraft: (nextDraft?: Partial<InspectorDraftSnapshot>) => void;
  selectedCatalogEntry: WorkflowEditorNodeCatalogEntry | undefined;
  inspectorSessionListMetadataRawMode: boolean;
  setInspectorSessionListMetadataRawMode: Dispatch<SetStateAction<boolean>>;
  inspectorSessionListMetadataRules: SessionListMetadataRuleDraftRow[];
  mutateSessionListMetadataRuleRows: (updater: (rows: SessionListMetadataRuleDraftRow[]) => SessionListMetadataRuleDraftRow[]) => void;
  selectedNodeGraph: WorkflowGraphMetadata | undefined;
  selectedDirectoryPath: string;
  updateClassificationFields: (patch: Record<string, unknown>, answers?: Record<string, unknown>) => void;
  communicationThreadField: WorkflowEditorFieldDefinition | undefined;
  updateBindableEnumFieldMode: (field: WorkflowEditorFieldDefinition, value: unknown, nextMode: "literal" | "bind" | "template") => void;
  updateAdoQueryModeField: (value: string) => void;
  inspectorStringListDrafts: Record<string, string>;
  removeObjectListItem: (key: string, index: number) => void;
  updateObjectListItem: (fieldKey: string, index: number, key: string, value: unknown) => void;
  addObjectListItem: (key: string, field: WorkflowEditorFieldDefinition) => void;
};

/** Binds field renderers to the current render state. */
export function createFieldRenderers({
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
  inspectorMapTemplateDrafts,
  setInspectorMapTemplateDrafts,
  setInspectorJsonDisplayModes,
  inspectorJsonDisplayModes,
  updateToolArgumentPolicyJsonDisplayMode,
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
}: FieldRenderersInputs) {
  function renderEffectTargetField() {
    if (!hasCollapsedTargetUi || !effectTargetField) return null;
    const value = inspectorKnownValues[effectTargetKey];
    const mode = inspectorFieldModes[effectTargetKey] ?? fieldValueMode(effectTargetField, value);
    const isDisabled = effectTargetField.read_only || (workflowMutationLocked && !tutorialAllowsInspectorFieldEdit(effectTargetKey));
    const targetTypeLabel = selectedNode?.type === "effect:agent:prompt" ? "Prompt target type" : "Target type";
    const targetPlaceholder =
      effectTargetKey === "session_id"
        ? "Session ID"
        : effectTargetKey === "vm_name"
          ? "VM name"
          : "VM name or session ID";
    const optionListId = effectTargetOptions.length > 0
      ? `workflow-target-options-${effectTargetKey}`
      : undefined;
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
              list={optionListId}
              placeholder={targetPlaceholder}
              onChange={(event) => updateKnownField(effectTargetKey, event.target.value)}
              disabled={isDisabled}
            />
          )}
        {mode === "literal" && optionListId ? (
          <datalist id={optionListId}>
            {effectTargetOptions.map((option) => (
              <option key={`${effectTargetKey}-${option.value}`} value={option.value} label={option.label} />
            ))}
          </datalist>
        ) : null}
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
            disabled={workflowMutationLocked}
            aria-label="Trigger enabled"
          />
          <span>{enabled ? "Trigger enabled" : "Trigger disabled"}</span>
        </label>
      </div>
    );
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
    return (
      <ToolArgumentPolicyField key={field.key}
        field={field}
        value={value}
        mode={mode}
        disabled={disabled}
        inspectorToolArgumentPolicyDrafts={inspectorToolArgumentPolicyDrafts}
        inspectorJsonDisplayModes={inspectorJsonDisplayModes}
        renderFieldHeader={renderFieldHeader}
        renderModeSelect={renderModeSelect}
        updateFieldMode={updateFieldMode}
        updateKnownField={updateKnownField}
        selectedNodeBindingOptions={selectedNodeBindingOptions}
        updateToolArgumentPolicyJsonDisplayMode={updateToolArgumentPolicyJsonDisplayMode}
        mutateToolArgumentPolicyDraftRows={mutateToolArgumentPolicyDraftRows}
        inspectorJsonDrafts={inspectorJsonDrafts}
        setInspectorJsonDrafts={setInspectorJsonDrafts}
        stageInspectorDraft={stageInspectorDraft}
      />
    );
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
    return (
      <SessionListMetadataRules
        key="metadata_rules"
        isDisabled={isDisabled}
        selectedCatalogEntry={selectedCatalogEntry}
        inspectorKnownValues={inspectorKnownValues}
        inspectorFieldModes={inspectorFieldModes}
        inspectorSessionListMetadataRawMode={inspectorSessionListMetadataRawMode}
        renderFieldHeader={renderFieldHeader}
        setInspectorSessionListMetadataRawMode={setInspectorSessionListMetadataRawMode}
        workflowMutationLocked={workflowMutationLocked}
        tutorialAllowsInspectorFieldEdit={tutorialAllowsInspectorFieldEdit}
        renderGenericJsonField={renderGenericJsonField}
        inspectorSessionListMetadataRules={inspectorSessionListMetadataRules}
        mutateSessionListMetadataRuleRows={mutateSessionListMetadataRuleRows}
      />
    );
  }

  function renderField(field: WorkflowEditorFieldDefinition) {
    const selectedNodeType = selectedNode?.type;
    if (adoQueryModeHidesField(selectedNodeType, field.key, inspectorKnownValues)) {
      return null;
    }
    if (teamsDirectChatModeHidesField(selectedNodeType, field.key, inspectorKnownValues)) {
      return null;
    }
    const value = inspectorKnownValues[field.key];
    const mode = inspectorFieldModes[field.key] ?? fieldValueMode(field, value);
    const isDisabled = field.read_only
      || (workflowMutationLocked && !tutorialAllowsInspectorFieldEdit(field.key));

    if (selectedNodeType === "gate:classifier:switch") {
      if (["instructions", "default_port", "default_description", "provider", "model", "min_probability"].includes(field.key)) return null;
      if (field.key === "cases") return <WorkflowClassifierSwitchEditor key={selectedNode?.id}
        value={inspectorKnownValues} directoryPath={selectedNodeGraph?.directory_path ?? selectedDirectoryPath}
        disabled={isDisabled} onChange={updateClassificationFields} />;
    }

    if (selectedNodeType === "query:classification:evaluate") {
      if (["profile_id", "provider", "model"].includes(field.key)) return null;
      if (field.key === "questions") return <WorkflowClassificationEditor key={selectedNode?.id}
        value={inspectorKnownValues} directoryPath={selectedNodeGraph?.directory_path ?? selectedDirectoryPath}
        nodeId={selectedNode?.id ?? "classify"} disabled={isDisabled} onChange={updateClassificationFields} />;
    }

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
              field.key === "cost_category"
                ? <div>
                  {selectedNodeType !== "effect:agent:create" && <label className="field-stack">
                    <span>Category assignment</span>
                    <select disabled={isDisabled} value={value == null ? "inherit" : "override"}
                      onChange={(event) => updateKnownField(field.key, event.target.value === "inherit" ? undefined : "")}>
                      <option value="inherit">Use session category</option><option value="override">Override category</option>
                    </select>
                  </label>}
                  {(selectedNodeType === "effect:agent:create" || value != null) && <CostCategoryInput
                    value={typeof value === "string" ? value : ""} disabled={isDisabled}
                    directoryPath={selectedNodeGraph?.directory_path ?? selectedDirectoryPath}
                    onChange={(nextValue) => updateKnownField(field.key, nextValue)} />}
                  <p className="field-description">{selectedNodeType === "effect:agent:create"
                    ? "Default category for prompts in this session."
                    : "An empty override is Uncategorized. Existing prompt costs keep their recorded category."}</p>
                </div>
                : field.key === "directory_path"
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
      const nullable = field.metadata?.nullable === true;
      return (
        <div key={field.key} className="inspector-field">
          <div className="field-toolbar">
            {renderFieldHeader(field)}
            {renderModeSelect(field, mode, (nextMode) => {
              updateFieldMode(field.key, nextMode);
              if (nextMode === "bind") {
                updateKnownField(field.key, selectedNodeBindingOptions[0]?.value ?? "");
              } else {
                updateKnownField(field.key, typeof value === "boolean" ? value : nullable ? undefined : Boolean(value));
              }
            }, isDisabled)}
          </div>
          {mode === "bind"
            ? renderBindingSelect(field, selectedNodeBindingOptions, value, (nextValue) => updateKnownField(field.key, nextValue))
            : nullable ? (
              <select
                value={typeof value === "boolean" ? String(value) : ""}
                onChange={(event) => updateKnownField(field.key, event.target.value === "" ? undefined : event.target.value === "true")}
                disabled={isDisabled}
              >
                <option value="">{String(field.metadata?.unset_label ?? "Default")}</option>
                <option value="true">{String(field.metadata?.true_label ?? "Enabled")}</option>
                <option value="false">{String(field.metadata?.false_label ?? "Disabled")}</option>
              </select>
            ) : (
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
      const literalEnumValue = typeof value === "string" ? value : "";
      const unavailableSubscriptionProfile = (field.key === "subscription_profile_id" || field.key === "worker_id")
        && Boolean(literalEnumValue)
        && !enumOptions.some((option) => option.value === literalEnumValue);
      const enumValue = field.key === "query_mode" && isAdoQueryModeNodeType(selectedNodeType)
        ? effectiveAdoQueryMode(inspectorKnownValues)
        : unavailableSubscriptionProfile
          ? literalEnumValue
          : enumOptionValue(field, value);
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
                value={enumValue}
                onChange={(event) => {
                  if (field.key === "query_mode" && isAdoQueryModeNodeType(selectedNodeType)) {
                    updateAdoQueryModeField(event.target.value);
                  } else {
                    const nextValue = enumLiteralValue(field, event.target.value);
                    updateKnownField(field.key, nextValue === "" ? undefined : nextValue);
                  }
                }}
                disabled={isDisabled}
              >
                <option value="">{String(field.metadata?.unset_label ?? "Select")}</option>
                {unavailableSubscriptionProfile ? (
                  <option value={literalEnumValue}>
                    {literalEnumValue} (disabled or unavailable)
                  </option>
                ) : null}
                {enumOptions.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            )}
          {unavailableSubscriptionProfile ? (
            <p className="field-description field-warning" role="alert">
              This saved subscription profile is disabled or unavailable on the effective worker.
              It remains selected so the workflow is not silently changed.
            </p>
          ) : null}
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

    if (selectedNodeType === "gate:map" && field.key === "template") {
      return <MapTemplateField key={selectedNode?.id} rows={inspectorMapTemplateDrafts.template ?? []}
        draft={inspectorJsonDrafts.template ?? "{}"} mode={inspectorJsonDisplayModes.template ?? "structured"}
        disabled={isDisabled} bindingOptions={selectedNodeBindingOptions}
        onRowsChange={rows => {
          const mapTemplateDrafts = { ...inspectorMapTemplateDrafts, template: rows };
          setInspectorMapTemplateDrafts(mapTemplateDrafts);
          stageInspectorDraft({ mapTemplateDrafts });
        }}
        onRawChange={text => {
          const jsonDrafts = { ...inspectorJsonDrafts, template: text };
          setInspectorJsonDrafts(jsonDrafts); stageInspectorDraft({ jsonDrafts });
        }}
        onModeChange={(nextMode, rows, text) => {
          const mapTemplateDrafts = { ...inspectorMapTemplateDrafts, template: rows };
          const jsonDrafts = { ...inspectorJsonDrafts, template: text };
          const jsonDisplayModes = { ...inspectorJsonDisplayModes, template: nextMode };
          setInspectorMapTemplateDrafts(mapTemplateDrafts); setInspectorJsonDrafts(jsonDrafts); setInspectorJsonDisplayModes(jsonDisplayModes);
          stageInspectorDraft({ mapTemplateDrafts, jsonDrafts, jsonDisplayModes });
        }} />;
    }

    if (field.kind === "json") {
      return renderGenericJsonField(field, value, mode, isDisabled);
    }

    if (field.kind === "object_list") {
      const modeSelect = field.bindable
        ? renderModeSelect(field, mode, (nextMode) => {
          updateFieldMode(field.key, nextMode);
          if (nextMode === "bind") {
            updateKnownField(field.key, selectedNodeBindingOptions[0]?.value ?? "");
          } else {
            updateKnownField(field.key, Array.isArray(value) ? value : []);
          }
        }, isDisabled)
        : null;
      if (mode === "bind") {
        return (
          <div key={field.key} className="inspector-field">
            <div className="field-toolbar">
              {renderFieldHeader(field)}
              {modeSelect}
            </div>
            {renderBindingSelect(
              field,
              selectedNodeBindingOptions,
              value,
              (nextValue) => updateKnownField(field.key, nextValue),
            )}
          </div>
        );
      }
      const items = Array.isArray(value) ? value : [];
      return (
        <div key={field.key} className="inspector-field">
          <div className="field-toolbar">
            {renderFieldHeader(field)}
            {modeSelect}
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

  return {
    renderSelectedTriggerToggle,
    renderEffectTargetField,
    renderField,
    renderSelectedNodeOutputContract,
  };
}

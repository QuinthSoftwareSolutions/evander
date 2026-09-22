import {
  type Dispatch,
  type ReactNode,
  type SetStateAction,
} from "react";
import { type WorkflowEditorFieldDefinition, type WorkflowEditorNodeCatalogEntry } from "../../types";
import { SESSION_LIST_METADATA_FIELD_KEYS } from "./editorConstants";
import { type SessionListMetadataRuleDraftRow, type SessionListMetadataRuleKind } from "./editorTypes";
import {
  createSessionListMetadataRuleDraftRow,
  fieldValueMode,
  sessionListMetadataRuleDraftRowsFromValues,
} from "./fieldModels";

type SessionListMetadataRulesProps = {
  isDisabled: boolean;
  selectedCatalogEntry: WorkflowEditorNodeCatalogEntry | undefined;
  inspectorKnownValues: Record<string, unknown>;
  inspectorFieldModes: Record<string, "literal" | "bind" | "template">;
  inspectorSessionListMetadataRawMode: boolean;
  renderFieldHeader: (field: WorkflowEditorFieldDefinition) => ReactNode;
  setInspectorSessionListMetadataRawMode: Dispatch<SetStateAction<boolean>>;
  workflowMutationLocked: boolean;
  tutorialAllowsInspectorFieldEdit: (fieldKey: string) => boolean;
  renderGenericJsonField: (field: WorkflowEditorFieldDefinition, value: unknown, mode: "literal" | "bind" | "template", isDisabled: boolean) => ReactNode;
  inspectorSessionListMetadataRules: SessionListMetadataRuleDraftRow[];
  mutateSessionListMetadataRuleRows: (updater: (rows: SessionListMetadataRuleDraftRow[]) => SessionListMetadataRuleDraftRow[]) => void;
};

export function SessionListMetadataRules({
  isDisabled,
  selectedCatalogEntry,
  inspectorKnownValues,
  inspectorFieldModes,
  inspectorSessionListMetadataRawMode,
  renderFieldHeader,
  setInspectorSessionListMetadataRawMode,
  workflowMutationLocked,
  tutorialAllowsInspectorFieldEdit,
  renderGenericJsonField,
  inspectorSessionListMetadataRules,
  mutateSessionListMetadataRuleRows,
}: SessionListMetadataRulesProps) {
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
              || (workflowMutationLocked && !tutorialAllowsInspectorFieldEdit(field.key));
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

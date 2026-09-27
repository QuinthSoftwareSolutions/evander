import {
  type Dispatch,
  type ReactNode,
  type SetStateAction,
} from "react";
import { type WorkflowEditorFieldDefinition } from "../../types";
import { type WorkflowBindingOption } from "../../workflowNodeOutputs";
import { type ToolArgumentPolicyDraftRow, toolArgumentPolicyDraftRowsToValue } from "../../workflowToolArgumentPolicies";
import { type InspectorDraftSnapshot, type JsonDisplayMode } from "./editorTypes";
import {
  createToolArgumentPolicyConstraintDraftRow,
  createToolArgumentPolicyDraftRow,
  createToolArgumentPolicyRuleDraftRow,
  renderBindingSelect,
  renderToolArgumentPolicyErrorMessages,
  toolArgumentPolicyConstraintPath,
  toolArgumentPolicyFieldErrorMessages,
  toolArgumentPolicyRowPath,
  toolArgumentPolicyRulePath,
} from "./fieldModels";

type ToolArgumentPolicyFieldProps = {
  field: WorkflowEditorFieldDefinition;
  value: unknown;
  mode: "literal" | "bind" | "template";
  disabled: boolean;
  inspectorToolArgumentPolicyDrafts: Record<string, ToolArgumentPolicyDraftRow[]>;
  inspectorJsonDisplayModes: Record<string, JsonDisplayMode>;
  renderFieldHeader: (field: WorkflowEditorFieldDefinition) => ReactNode;
  renderModeSelect: (field: WorkflowEditorFieldDefinition, mode: "literal" | "bind" | "template", onChange: (nextMode: "literal" | "bind" | "template") => void, disabled: boolean) => ReactNode;
  updateFieldMode: (key: string, mode: "literal" | "bind" | "template") => void;
  updateKnownField: (key: string, value: unknown) => void;
  selectedNodeBindingOptions: WorkflowBindingOption[];
  updateToolArgumentPolicyJsonDisplayMode: (field: WorkflowEditorFieldDefinition, nextMode: JsonDisplayMode) => void;
  mutateToolArgumentPolicyDraftRows: (fieldKey: string, updater: (rows: ToolArgumentPolicyDraftRow[]) => ToolArgumentPolicyDraftRow[]) => void;
  inspectorJsonDrafts: Record<string, string>;
  setInspectorJsonDrafts: Dispatch<SetStateAction<Record<string, string>>>;
  stageInspectorDraft: (nextDraft?: Partial<InspectorDraftSnapshot>) => void;
};

export function ToolArgumentPolicyField({
  field,
  value,
  mode,
  disabled,
  inspectorToolArgumentPolicyDrafts,
  inspectorJsonDisplayModes,
  renderFieldHeader,
  renderModeSelect,
  updateFieldMode,
  updateKnownField,
  selectedNodeBindingOptions,
  updateToolArgumentPolicyJsonDisplayMode,
  mutateToolArgumentPolicyDraftRows,
  inspectorJsonDrafts,
  setInspectorJsonDrafts,
  stageInspectorDraft,
}: ToolArgumentPolicyFieldProps) {
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

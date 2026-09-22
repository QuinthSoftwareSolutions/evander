import { type DirectoryTreeNode } from "../../features/admin/AdminContext";
import { type WorkflowEditorFieldDefinition, type WorkflowEditorNodeCatalogEntry } from "../../types";
import { isExactWorkflowBindingValue } from "../../workflowBindings";
import {
  type ToolArgumentPolicyConstraintDraftRow,
  type ToolArgumentPolicyDraftRow,
  type ToolArgumentPolicyFieldError,
  type ToolArgumentPolicyRuleDraftRow,
} from "../../workflowToolArgumentPolicies";
import {
  ADO_QUERY_MODE_MANUAL_WIQL,
  ADO_QUERY_MODE_NODE_TYPES,
  ADO_QUERY_MODE_STRUCTURED_FILTERS,
  ADO_STRUCTURED_FILTER_FIELD_KEYS,
  COMMUNICATION_EFFECT_NODE_TYPES,
  NODE_KIND_DESCRIPTIONS,
  NODE_TYPE_DESCRIPTIONS,
  SESSION_LIST_METADATA_FIELD_KEYS,
  TOOL_ARGUMENT_POLICY_STRUCTURED_NODE_TYPES,
  TOOL_GROUP_ORDER,
} from "./editorConstants";
import {
  type BindingOption,
  type DirectorySelectOption,
  type EffectTargetKey,
  type SessionListMetadataRuleDraftRow,
  type SessionListMetadataRuleKind,
  type StringMapDraftRow,
  type WorkflowFieldOption,
} from "./editorTypes";
import { isRecord } from "./editorValues";

export function renderStringInput(
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

export function createStringMapDraftRow(key = "", value = ""): StringMapDraftRow {
  const randomSuffix = Math.random().toString(36).slice(2);
  return { id: `map-row-${Date.now()}-${randomSuffix}`, key, value };
}

export function stringMapDraftRows(value: unknown): StringMapDraftRow[] {
  if (!isRecord(value)) return [];
  return Object.entries(value).map(([key, entry]) => createStringMapDraftRow(key, String(entry ?? "")));
}

export function stringMapRowsToObject(rows: StringMapDraftRow[]): Record<string, string> {
  const result: Record<string, string> = {};
  for (const row of rows) {
    const key = row.key.trim();
    if (!key) continue;
    result[key] = row.value;
  }
  return result;
}

export function createToolArgumentPolicyConstraintDraftRow(): ToolArgumentPolicyConstraintDraftRow {
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

export function createToolArgumentPolicyRuleDraftRow(): ToolArgumentPolicyRuleDraftRow {
  return {
    id: `rule-${Date.now()}-${Math.random().toString(36).slice(2)}`,
    name: "",
    constraints: [createToolArgumentPolicyConstraintDraftRow()],
  };
}

export function createToolArgumentPolicyDraftRow(toolName = ""): ToolArgumentPolicyDraftRow {
  return {
    toolName,
    rules: [],
  };
}

export function parseStringMapDraftRows(
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

export function directoryOptionLabel(path: string, name: string | undefined, depth: number): string {
  if (path === "root") return "root";
  const prefix = depth > 0 ? `${"  ".repeat(depth)}` : "";
  return name && name !== path ? `${prefix}${name} (${path})` : `${prefix}${path}`;
}

export function flattenDirectorySelectOptions(node: DirectoryTreeNode, depth = 0): DirectorySelectOption[] {
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

export function ensureDirectorySelectOption(
  options: DirectorySelectOption[],
  value: string,
  label: string,
): DirectorySelectOption[] {
  if (!value || options.some((option) => option.value === value)) return options;
  return [...options, { value, label }];
}

export function groupToolOption(option: WorkflowFieldOption): string {
  const declaredGroup = option.metadata?.tool_group;
  if (typeof declaredGroup === "string" && declaredGroup.trim()) return declaredGroup.trim();
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
    "list_pr_feedback_attachments",
    "download_pr_feedback_attachment",
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

export function groupToolOptions(options: WorkflowFieldOption[]) {
  const groups = new Map<string, WorkflowFieldOption[]>();
  for (const option of options) {
    const group = groupToolOption(option);
    groups.set(group, [...(groups.get(group) ?? []), option]);
  }
  const orderedNames = [
    ...TOOL_GROUP_ORDER.filter((name) => groups.has(name)),
    ...[...groups.keys()]
      .filter((name) => !TOOL_GROUP_ORDER.includes(name as (typeof TOOL_GROUP_ORDER)[number]))
      .sort((left, right) => left.localeCompare(right, undefined, { sensitivity: "base" })),
  ];
  return orderedNames
    .map((name) => ({ name, items: groups.get(name) ?? [] }))
    .filter((group) => group.items.length > 0)
    .map((group) => ({
      ...group,
      items: [...group.items].sort((left, right) => left.label.localeCompare(right.label, undefined, { sensitivity: "base" })),
    }));
}

export function isExactBindingValue(value: unknown): value is string {
  return isExactWorkflowBindingValue(value);
}

export function hasBindingToken(value: unknown): boolean {
  return typeof value === "string" && value.includes("${{");
}

export function isSessionListMetadataFieldKey(
  key: string,
): key is (typeof SESSION_LIST_METADATA_FIELD_KEYS)[number] {
  return SESSION_LIST_METADATA_FIELD_KEYS.includes(
    key as (typeof SESSION_LIST_METADATA_FIELD_KEYS)[number],
  );
}

export function createSessionListMetadataRuleDraftRow(
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

export function sessionListMetadataEqualsRuleValueText(value: unknown): string {
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

export function sessionListMetadataPrefixRuleValueText(value: unknown): string {
  if (typeof value === "string") return value;
  try {
    const text = JSON.stringify(value);
    return text === undefined ? String(value ?? "") : text;
  } catch {
    return String(value ?? "");
  }
}

export function sessionListMetadataRuleValue(value: string): unknown {
  const trimmed = value.trim();
  if (!trimmed) return "";
  try {
    return JSON.parse(trimmed) as unknown;
  } catch {
    return value;
  }
}

export function sessionListMetadataExistsFlagIsPresent(value: unknown): boolean {
  if (value === true) return true;
  if (typeof value === "string") {
    return ["true", "1", "yes", "on"].includes(value.trim().toLowerCase());
  }
  return false;
}

export function appendSessionListMetadataEqualsRuleRows(
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

export function sessionListMetadataRuleDraftRowsFromValues(values: Record<string, unknown>): {
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

export function sessionListMetadataRuleValues(rows: SessionListMetadataRuleDraftRow[]): {
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

export function nodeTypeDescription(entry: WorkflowEditorNodeCatalogEntry): string {
  const description = entry.description?.trim();
  return description || NODE_TYPE_DESCRIPTIONS[entry.type] || NODE_KIND_DESCRIPTIONS[entry.kind];
}

export function fieldValueMode(field: WorkflowEditorFieldDefinition, value: unknown): "literal" | "bind" | "template" {
  if (!field.bindable) return "literal";
  if (isExactBindingValue(value)) return "bind";
  if (field.kind === "string" && hasBindingToken(value)) return "template";
  return "literal";
}

export function enumOptionValue(field: WorkflowEditorFieldDefinition, value: unknown): string {
  const stringValue = typeof value === "string" ? value : "";
  if (!stringValue) return "";
  return field.options.some((option) => option.value === stringValue) ? stringValue : "";
}

export function enumLiteralFallbackValue(field: WorkflowEditorFieldDefinition, value: unknown): string {
  const currentValue = enumOptionValue(field, value);
  if (currentValue) return currentValue;
  const defaultValue = enumOptionValue(field, field.default_value);
  if (defaultValue) return defaultValue;
  return field.required ? field.options[0]?.value ?? "" : "";
}

export function parseJsonDraft(text: string, fallback: unknown): unknown {
  const trimmed = text.trim();
  if (!trimmed) return fallback;
  return JSON.parse(trimmed) as unknown;
}

export function renderBindingSelect(
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

export function insertBindingReference(currentValue: unknown, bindingReference: string): string {
  const text = typeof currentValue === "string" ? currentValue : "";
  const trimmedReference = bindingReference.trim();
  if (!trimmedReference) return text;
  if (!text.trim()) return trimmedReference;
  return `${text}${trimmedReference}`;
}

export function activeEffectTargetKey(values: Record<string, unknown>): EffectTargetKey {
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

export function isCommunicationEffectNodeType(type: string | undefined): type is
  | "effect:communication:send-message"
  | "effect:notification:send" {
  return COMMUNICATION_EFFECT_NODE_TYPES.has(type ?? "");
}

export function supportsStructuredToolArgumentPolicies(nodeType: string | undefined): boolean {
  return Boolean(nodeType && TOOL_ARGUMENT_POLICY_STRUCTURED_NODE_TYPES.has(nodeType));
}

export function isAdoQueryModeNodeType(nodeType: string | undefined): boolean {
  return Boolean(nodeType && ADO_QUERY_MODE_NODE_TYPES.has(nodeType));
}

export function effectiveAdoQueryMode(values: Record<string, unknown>): string {
  const configuredMode = typeof values.query_mode === "string" ? values.query_mode.trim() : "";
  if (configuredMode === ADO_QUERY_MODE_STRUCTURED_FILTERS || configuredMode === ADO_QUERY_MODE_MANUAL_WIQL) {
    return configuredMode;
  }
  const wiql = typeof values.wiql === "string" ? values.wiql.trim() : "";
  return wiql ? ADO_QUERY_MODE_MANUAL_WIQL : ADO_QUERY_MODE_STRUCTURED_FILTERS;
}

export function adoQueryModeHidesField(
  nodeType: string | undefined,
  fieldKey: string,
  values: Record<string, unknown>,
): boolean {
  if (!isAdoQueryModeNodeType(nodeType) || fieldKey === "query_mode") return false;
  const mode = effectiveAdoQueryMode(values);
  if (mode === ADO_QUERY_MODE_MANUAL_WIQL) {
    return ADO_STRUCTURED_FILTER_FIELD_KEYS.has(fieldKey);
  }
  return fieldKey === "wiql";
}

export function pruneAdoQueryModeHiddenFieldValues(
  nodeType: string | undefined,
  values: Record<string, unknown>,
): Record<string, unknown> {
  if (!isAdoQueryModeNodeType(nodeType)) return values;
  const nextValues = { ...values };
  for (const fieldKey of ADO_STRUCTURED_FILTER_FIELD_KEYS) {
    if (adoQueryModeHidesField(nodeType, fieldKey, values)) {
      delete nextValues[fieldKey];
    }
  }
  if (adoQueryModeHidesField(nodeType, "wiql", values)) {
    delete nextValues.wiql;
  }
  return nextValues;
}

export function normalizedAgentCreateExecutionBackend(values: Record<string, unknown>): string {
  const rawValue = values.execution_backend;
  return typeof rawValue === "string" ? rawValue.trim() : "";
}

export function metadataExecutionBackends(metadata: Record<string, unknown> | undefined): string[] | null {
  const rawValue = metadata?.execution_backends;
  if (!Array.isArray(rawValue)) return null;
  const values = rawValue
    .filter((value): value is string => typeof value === "string" && value.trim().length > 0)
    .map((value) => value.trim());
  return values.length > 0 ? values : null;
}

export function fieldExecutionBackends(field: WorkflowEditorFieldDefinition): string[] | null {
  return metadataExecutionBackends(field.metadata);
}

export function fieldSelectionOnly(field: WorkflowEditorFieldDefinition): boolean {
  return field.metadata?.selection_only === true;
}

export function agentCreateFieldVisibleForBackend(
  field: WorkflowEditorFieldDefinition,
  executionBackend: string,
): boolean {
  if (field.key === "execution_backend") return true;
  if (!executionBackend) return false;
  const visibleBackends = fieldExecutionBackends(field);
  return !visibleBackends || visibleBackends.includes(executionBackend);
}

export function pruneAgentCreateBackendFieldValues(
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

export function agentCreateFieldOptionsForBackend(
  field: WorkflowEditorFieldDefinition,
  executionBackend: string,
) {
  if (field.key !== "agent_provider") return field.options;
  return field.options.filter((option) => {
    const visibleBackends = metadataExecutionBackends(option.metadata);
    return !visibleBackends || visibleBackends.includes(executionBackend);
  });
}

export function toolArgumentPolicyRowPath(toolName: string, toolIndex: number): string {
  const normalizedToolName = toolName.trim();
  return normalizedToolName
    ? `tool_argument_policies.${normalizedToolName}`
    : `tool_argument_policies[${toolIndex}]`;
}

export function toolArgumentPolicyRulePath(toolName: string, toolIndex: number, ruleIndex: number): string {
  return `${toolArgumentPolicyRowPath(toolName, toolIndex)}[${ruleIndex}]`;
}

export function toolArgumentPolicyConstraintPath(
  toolName: string,
  toolIndex: number,
  ruleIndex: number,
  constraintIndex: number,
): string {
  return `${toolArgumentPolicyRulePath(toolName, toolIndex, ruleIndex)}.constraints[${constraintIndex}]`;
}

export function toolArgumentPolicyFieldErrorMessages(
  errors: ToolArgumentPolicyFieldError[],
  path: string,
): string[] {
  return errors.filter((error) => error.path === path).map((error) => error.message);
}

export function toolArgumentPolicyValidationErrorSummary(errors: ToolArgumentPolicyFieldError[]): string {
  return errors.map((error) => `${error.path}: ${error.message}`).join("; ");
}

export function renderToolArgumentPolicyErrorMessages(messages: string[]) {
  if (messages.length === 0) return null;
  return (
    <div className="error-text">
      {messages.map((message, index) => (
        <div key={`${message}-${index}`}>{message}</div>
      ))}
    </div>
  );
}

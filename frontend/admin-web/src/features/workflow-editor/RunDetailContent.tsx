import { type ReactNode } from "react";
import {
  type WorkflowEditorFieldDefinition,
  type WorkflowEditorNodeCatalogEntry,
  type WorkflowRunNodeDetail,
  type WorkflowRunToolCallLink,
} from "../../types";
import { type OperatorSteeringNote } from "./editorTypes";
import { formatJson, isRecord } from "./editorValues";
import {
  detailHashTarget,
  firstNonEmptyString,
  formatRuntimeTimestamp,
  normalizeAvailabilityState,
  objectHasData,
  structuredValueSummary,
} from "./runDetailsModel";

export function renderRunDetailJson(label: string, value: unknown, missing: boolean) {
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

export function navigateToAdminHash(hash: string) {
  window.location.hash = hash;
}

export function renderRunDetailToolCallLink(link: WorkflowRunToolCallLink, scope: "run" | "node", index: number) {
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

export function operatorSteeringNoteSummary(note: OperatorSteeringNote): string {
  if (note.status === "pending") return `Pending since ${formatRuntimeTimestamp(note.created_at)}`;
  if (note.status === "injecting") return `Injecting since ${formatRuntimeTimestamp(note.updated_at)}`;
  if (note.status === "delivered") return `Delivered ${formatRuntimeTimestamp(note.delivered_at ?? note.updated_at)}`;
  if (note.status === "cancelled") return `Cancelled ${formatRuntimeTimestamp(note.cancelled_at ?? note.updated_at)}`;
  return `${note.status} ${formatRuntimeTimestamp(note.updated_at)}`;
}

export function renderRunDetailMissingBlock(label: string, message: string, key: string) {
  return (
    <div className="run-detail-data-block run-detail-missing" key={key}>
      <span>{label}</span>
      <strong>{message}</strong>
    </div>
  );
}

export function renderRunDetailLinkGroup(
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

export function renderReadOnlyResolvedField(field: WorkflowEditorFieldDefinition, value: unknown) {
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

export function renderResolvedInputEditor(
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

import { useEffect, useId, useRef, useState, type ReactNode } from "react";

import {
  createJsonObjectDraftRow,
  createRegexPatternDraftRow,
  createStringMapDraftRow,
  dedupeStrings,
  encodeStringListDraftValue,
  jsonObjectDraftRowsFromValue,
  parseJsonTextDraftValue,
  parseJsonObjectDraftRows,
  parseRegexPatternDraftRows,
  parseStringMapDraftRows,
  regexPatternDraftRowsFromValue,
  stringMapDraftRowsFromValue,
  validateJsonTextDraftValue,
  validateRegexPattern,
  type JsonObjectDraftRow,
  type RegexPatternDraftRow,
  type StringMapDraftRow,
} from "./structuredPolicyInputs";

type KnownOption = {
  value: string;
  label: string;
};

type StringDraftRow = {
  id: string;
  value: string;
};

type StringListEditorProps = {
  values: readonly string[];
  onChange: (nextValues: string[]) => void;
  disabled?: boolean;
  className?: string;
  description?: ReactNode;
  emptyState?: string;
  addLabel?: string;
  itemLabel?: string;
  placeholder?: string;
  suggestions?: readonly string[];
  validateItem?: (value: string, index: number, values: readonly string[]) => string | null;
  normalizeItem?: (value: string) => string;
};

type OptionMultiSelectEditorProps = {
  values: readonly string[];
  options: readonly KnownOption[];
  onChange: (nextValues: string[]) => void;
  disabled?: boolean;
  className?: string;
  description?: ReactNode;
  emptyState?: string;
  unknownLabel?: string;
};

type StringMapEditorProps = {
  value: Record<string, string>;
  onChange: (nextValue: Record<string, string>) => void;
  onValidationChange?: (error: string | null) => void;
  fieldLabel: string;
  disabled?: boolean;
  className?: string;
  description?: ReactNode;
  emptyState?: string;
  addLabel?: string;
  keyLabel?: string;
  valueLabel?: string;
};

type JsonObjectKeyValueEditorProps = {
  value: Record<string, unknown>;
  onChange: (nextValue: Record<string, unknown>) => void;
  onValidationChange?: (error: string | null) => void;
  fieldLabel: string;
  disabled?: boolean;
  className?: string;
  description?: ReactNode;
  emptyState?: string;
  addLabel?: string;
};

type JsonTextEditorProps = {
  value: string;
  onChange: (nextValue: string) => void;
  fieldLabel: string;
  disabled?: boolean;
  className?: string;
  description?: ReactNode;
  placeholder?: string;
};

type RegexListEditorProps = {
  values: readonly string[];
  onChange: (nextValues: string[]) => void;
  fieldLabel: string;
  disabled?: boolean;
  className?: string;
  description?: ReactNode;
  emptyState?: string;
  addLabel?: string;
  placeholder?: string;
};

function createStringDraftRow(value = ""): StringDraftRow {
  return {
    id: `string-row-${Date.now()}-${Math.random().toString(36).slice(2)}`,
    value,
  };
}

function canonicalStringList(values: readonly string[], normalizeItem?: (value: string) => string): string[] {
  const normalized = values.map((entry) => (normalizeItem ? normalizeItem(entry) : entry));
  return dedupeStrings(normalized);
}

function renderEmptyState(emptyState: string | undefined, fallback: string): ReactNode {
  return <div className="empty-state">{emptyState ?? fallback}</div>;
}

export function StringListEditor({
  values,
  onChange,
  disabled = false,
  className,
  description,
  emptyState,
  addLabel = "Add item",
  itemLabel = "Value",
  placeholder = "Enter a value",
  suggestions,
  validateItem,
  normalizeItem,
}: StringListEditorProps) {
  const datalistId = useId();
  const normalizedValues = canonicalStringList(values, normalizeItem);
  const snapshot = encodeStringListDraftValue(normalizedValues);
  const [rows, setRows] = useState<StringDraftRow[]>(() => normalizedValues.map((value) => createStringDraftRow(value)));
  const snapshotRef = useRef(snapshot);

  useEffect(() => {
    if (snapshotRef.current === snapshot) return;
    snapshotRef.current = snapshot;
    setRows(normalizedValues.map((value) => createStringDraftRow(value)));
  }, [normalizedValues, snapshot]);

  function emitChange(nextRows: readonly StringDraftRow[]) {
    const nextValues = canonicalStringList(
      nextRows.map((row) => row.value),
      normalizeItem,
    );
    onChange(nextValues);
  }

  const optionValues = suggestions ?? [];
  const normalizedOptionValues = optionValues.map((entry) => String(entry));
  const options = normalizedOptionValues.length > 0
    ? dedupeStrings(normalizedOptionValues)
    : [];

  return (
    <div className={["string-list-selector", "structured-input-editor", className].filter(Boolean).join(" ")}>
      {description ? <div className="structured-input-description">{description}</div> : null}
      <div className="inline-actions compact-actions">
        <button
          type="button"
          onClick={() => {
            const nextRows = [...rows, createStringDraftRow()];
            setRows(nextRows);
          }}
          disabled={disabled}
        >
          {addLabel}
        </button>
      </div>
      {rows.length === 0 ? renderEmptyState(emptyState, `No ${itemLabel.toLowerCase()}s are configured.`) : null}
      {rows.map((row, index) => {
        const validation = validateItem?.(row.value, index, rows.map((current) => current.value)) ?? null;
        return (
          <div key={row.id} className="string-list-selector-row structured-input-row">
            <label className="field-stack structured-input-field">
              <span>{itemLabel}</span>
              <input
                list={options.length > 0 ? datalistId : undefined}
                value={row.value}
                onChange={(event) => {
                  const nextValue = normalizeItem ? normalizeItem(event.target.value) : event.target.value;
                  const nextRows = rows.map((current, currentIndex) =>
                    currentIndex === index ? { ...current, value: nextValue } : current,
                  );
                  setRows(nextRows);
                  emitChange(nextRows);
                }}
                placeholder={placeholder}
                disabled={disabled}
              />
              {validation ? <div className="structured-input-error">{validation}</div> : null}
            </label>
            <div className="inline-actions compact-actions">
              <button
                type="button"
                className="danger"
                onClick={() => {
                  const nextRows = rows.filter((_, currentIndex) => currentIndex !== index);
                  setRows(nextRows);
                  emitChange(nextRows);
                }}
                disabled={disabled}
              >
                Remove
              </button>
            </div>
          </div>
        );
      })}
      {options.length > 0 ? (
        <datalist id={datalistId}>
          {options.map((option) => (
            <option key={option} value={option} />
          ))}
        </datalist>
      ) : null}
    </div>
  );
}

export function JsonTextEditor({
  value,
  onChange,
  fieldLabel,
  disabled = false,
  className,
  description,
  placeholder = "Enter valid JSON",
}: JsonTextEditorProps) {
  const validationError = validateJsonTextDraftValue(value, fieldLabel);
  let preview: string | null = null;
  if (!validationError) {
    try {
      const parsed = parseJsonTextDraftValue(value, fieldLabel);
      preview = parsed === null ? "null" : JSON.stringify(parsed, null, 2) ?? "";
    } catch {
      preview = null;
    }
  }

  return (
    <div className={["structured-input-editor", className].filter(Boolean).join(" ")}>
      {description ? <div className="structured-input-description">{description}</div> : null}
      <textarea
        className="json-editor compact-editor"
        value={value}
        spellCheck={false}
        onChange={(event) => onChange(event.target.value)}
        disabled={disabled}
        placeholder={placeholder}
      />
      {validationError ? <div className="structured-input-error">{validationError}</div> : null}
      {preview ? (
        <details className="structured-input-preview">
          <summary>Preview</summary>
          <pre className="json-view structured-input-json-preview">{preview}</pre>
        </details>
      ) : null}
    </div>
  );
}

export function OptionMultiSelectEditor({
  values,
  options,
  onChange,
  disabled = false,
  className,
  description,
  emptyState,
  unknownLabel = "unknown value",
}: OptionMultiSelectEditorProps) {
  const normalizedValues = dedupeStrings(values);
  const optionValues = new Set(options.map((option) => option.value));
  const unknownValues = normalizedValues.filter((value) => !optionValues.has(value));

  function emitChange(nextValues: string[]) {
    onChange(dedupeStrings(nextValues));
  }

  return (
    <div className={["structured-input-editor", className].filter(Boolean).join(" ")}>
      {description ? <div className="structured-input-description">{description}</div> : null}
      <div className="structured-input-option-grid">
        {options.map((option) => {
          const checked = normalizedValues.includes(option.value);
          return (
            <label key={option.value} className="checkbox-row structured-input-option-row">
              <input
                type="checkbox"
                checked={checked}
                onChange={(event) => {
                  const nextValues = event.target.checked
                    ? [...normalizedValues, option.value]
                    : normalizedValues.filter((value) => value !== option.value);
                  emitChange(nextValues);
                }}
                disabled={disabled}
              />
              <span>{option.label}</span>
            </label>
          );
        })}
      </div>
      {normalizedValues.length === 0 ? renderEmptyState(emptyState, "No selections are configured.") : null}
      {unknownValues.map((value) => (
        <div key={value} className="structured-input-unknown-row">
          <div className="structured-input-unknown-copy">
            <strong>{value}</strong>
            <span className="status-pill">{unknownLabel}</span>
          </div>
          <div className="inline-actions compact-actions">
            <button
              type="button"
              className="danger"
              onClick={() => emitChange(normalizedValues.filter((entry) => entry !== value))}
              disabled={disabled}
            >
              Remove
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}

export function StringMapEditor({
  value,
  onChange,
  onValidationChange,
  fieldLabel,
  disabled = false,
  className,
  description,
  emptyState,
  addLabel = "Add row",
  keyLabel = "Key",
  valueLabel = "Value",
}: StringMapEditorProps) {
  const snapshot = JSON.stringify(value ?? {});
  const [rows, setRows] = useState<StringMapDraftRow[]>(() => stringMapDraftRowsFromValue(value));
  const [error, setError] = useState<string | null>(null);
  const snapshotRef = useRef(snapshot);

  useEffect(() => {
    if (snapshotRef.current === snapshot) return;
    snapshotRef.current = snapshot;
    setRows(stringMapDraftRowsFromValue(value));
    setError(null);
  }, [snapshot, value]);

  function emitChange(nextRows: readonly StringMapDraftRow[]) {
    try {
      const parsed = parseStringMapDraftRows(nextRows, fieldLabel, false);
      onChange(parsed ?? {});
      setError(null);
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : String(nextError));
    }
  }

  const validationError = error ?? (() => {
    try {
      parseStringMapDraftRows(rows, fieldLabel, false);
      return null;
    } catch (nextError) {
      return nextError instanceof Error ? nextError.message : String(nextError);
    }
  })();

  useEffect(() => {
    onValidationChange?.(validationError);
  }, [onValidationChange, validationError]);

  return (
    <div className={["structured-input-editor", className].filter(Boolean).join(" ")}>
      {description ? <div className="structured-input-description">{description}</div> : null}
      <div className="inline-actions compact-actions">
        <button
          type="button"
          onClick={() => setRows((current) => [...current, createStringMapDraftRow()])}
          disabled={disabled}
        >
          {addLabel}
        </button>
      </div>
      {error ? <div className="callout error-callout">{error}</div> : null}
      {rows.length === 0 ? renderEmptyState(emptyState, `No ${fieldLabel.toLowerCase()} rows are configured.`) : null}
      {rows.map((row, index) => (
        <div key={row.id} className="string-list-selector-row structured-input-row structured-input-map-row">
          <label className="field-stack structured-input-field">
            <span>{keyLabel}</span>
            <input
              value={row.key}
              onChange={(event) => {
                const nextRows = rows.map((current, currentIndex) =>
                  currentIndex === index ? { ...current, key: event.target.value } : current,
                );
                setRows(nextRows);
                emitChange(nextRows);
              }}
              disabled={disabled}
            />
          </label>
          <label className="field-stack structured-input-field">
            <span>{valueLabel}</span>
            <input
              value={row.value}
              onChange={(event) => {
                const nextRows = rows.map((current, currentIndex) =>
                  currentIndex === index ? { ...current, value: event.target.value } : current,
                );
                setRows(nextRows);
                emitChange(nextRows);
              }}
              disabled={disabled}
            />
          </label>
          <div className="inline-actions compact-actions">
            <button
              type="button"
              className="danger"
              onClick={() => {
                const nextRows = rows.filter((_, currentIndex) => currentIndex !== index);
                setRows(nextRows);
                emitChange(nextRows);
              }}
              disabled={disabled}
            >
              Remove
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}

export function JsonObjectKeyValueEditor({
  value,
  onChange,
  onValidationChange,
  fieldLabel,
  disabled = false,
  className,
  description,
  emptyState,
  addLabel = "Add row",
}: JsonObjectKeyValueEditorProps) {
  const snapshot = JSON.stringify(value ?? {});
  const [rows, setRows] = useState<JsonObjectDraftRow[]>(() => jsonObjectDraftRowsFromValue(value));
  const [error, setError] = useState<string | null>(null);
  const snapshotRef = useRef(snapshot);

  useEffect(() => {
    if (snapshotRef.current === snapshot) return;
    snapshotRef.current = snapshot;
    setRows(jsonObjectDraftRowsFromValue(value));
    setError(null);
  }, [snapshot, value]);

  function emitChange(nextRows: readonly JsonObjectDraftRow[]) {
    try {
      const parsed = parseJsonObjectDraftRows(nextRows, fieldLabel);
      onChange(parsed);
      setError(null);
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : String(nextError));
    }
  }

  const validationError = error ?? (() => {
    try {
      parseJsonObjectDraftRows(rows, fieldLabel);
      return null;
    } catch (nextError) {
      return nextError instanceof Error ? nextError.message : String(nextError);
    }
  })();

  useEffect(() => {
    onValidationChange?.(validationError);
  }, [onValidationChange, validationError]);

  return (
    <div className={["structured-input-editor", className].filter(Boolean).join(" ")}>
      {description ? <div className="structured-input-description">{description}</div> : null}
      <div className="inline-actions compact-actions">
        <button
          type="button"
          onClick={() => setRows((current) => [...current, createJsonObjectDraftRow()])}
          disabled={disabled}
        >
          {addLabel}
        </button>
      </div>
      {error ? <div className="callout error-callout">{error}</div> : null}
      {rows.length === 0 ? renderEmptyState(emptyState, `No ${fieldLabel.toLowerCase()} rows are configured.`) : null}
      {rows.map((row, index) => {
        let rowPreview: string | null = null;
        let rowError: string | null = null;
        const key = row.key.trim();
        const valueText = row.valueText.trim();
        if (key && valueText) {
          try {
            const parsed = JSON.parse(valueText);
            rowPreview = JSON.stringify(parsed, null, 2);
          } catch (nextError) {
            rowError = nextError instanceof Error && nextError.message
              ? nextError.message
              : "Invalid JSON value.";
          }
        }
        return (
          <div key={row.id} className="structured-input-json-row">
            <div className="string-list-selector-row structured-input-row structured-input-map-row">
              <label className="field-stack structured-input-field">
                <span>Key</span>
                <input
                  value={row.key}
                  onChange={(event) => {
                    const nextRows = rows.map((current, currentIndex) =>
                      currentIndex === index ? { ...current, key: event.target.value } : current,
                    );
                    setRows(nextRows);
                    emitChange(nextRows);
                  }}
                  disabled={disabled}
                />
              </label>
              <label className="field-stack structured-input-field structured-input-json-value">
                <span>Value</span>
                <textarea
                  className="json-editor compact-editor"
                  value={row.valueText}
                  spellCheck={false}
                  onChange={(event) => {
                    const nextRows = rows.map((current, currentIndex) =>
                      currentIndex === index ? { ...current, valueText: event.target.value } : current,
                    );
                    setRows(nextRows);
                    emitChange(nextRows);
                  }}
                  disabled={disabled}
                />
                {rowError ? <div className="structured-input-error">{rowError}</div> : null}
              </label>
              <div className="inline-actions compact-actions">
                <button
                  type="button"
                  className="danger"
                  onClick={() => {
                    const nextRows = rows.filter((_, currentIndex) => currentIndex !== index);
                    setRows(nextRows);
                    emitChange(nextRows);
                  }}
                  disabled={disabled}
                >
                  Remove
                </button>
              </div>
            </div>
            {rowPreview ? (
              <details className="structured-input-preview">
                <summary>Preview</summary>
                <pre className="json-view structured-input-json-preview">{rowPreview}</pre>
              </details>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

export function RegexListEditor({
  values,
  onChange,
  fieldLabel,
  disabled = false,
  className,
  description,
  emptyState,
  addLabel = "Add pattern",
  placeholder = "Enter a regular expression",
}: RegexListEditorProps) {
  const snapshot = encodeStringListDraftValue(values);
  const [rows, setRows] = useState<RegexPatternDraftRow[]>(() => regexPatternDraftRowsFromValue(values));
  const [error, setError] = useState<string | null>(null);
  const snapshotRef = useRef(snapshot);

  useEffect(() => {
    if (snapshotRef.current === snapshot) return;
    snapshotRef.current = snapshot;
    setRows(regexPatternDraftRowsFromValue(values));
    setError(null);
  }, [snapshot, values]);

  function emitChange(nextRows: readonly RegexPatternDraftRow[]) {
    try {
      const parsed = parseRegexPatternDraftRows(nextRows, fieldLabel, false);
      onChange(parsed ?? []);
      setError(null);
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : String(nextError));
    }
  }

  return (
    <div className={["string-list-selector", "structured-input-editor", className].filter(Boolean).join(" ")}>
      {description ? <div className="structured-input-description">{description}</div> : null}
      <div className="inline-actions compact-actions">
        <button
          type="button"
          onClick={() => setRows((current) => [...current, createRegexPatternDraftRow()])}
          disabled={disabled}
        >
          {addLabel}
        </button>
      </div>
      {error ? <div className="callout error-callout">{error}</div> : null}
      {rows.length === 0 ? renderEmptyState(emptyState, `No ${fieldLabel.toLowerCase()} rows are configured.`) : null}
      {rows.map((row, index) => {
        const rowError = validateRegexPattern(row.pattern);
        return (
          <div key={row.id} className="string-list-selector-row structured-input-row">
            <label className="field-stack structured-input-field">
              <span>Pattern</span>
              <input
                value={row.pattern}
                onChange={(event) => {
                  const nextRows = rows.map((current, currentIndex) =>
                    currentIndex === index ? { ...current, pattern: event.target.value } : current,
                  );
                  setRows(nextRows);
                  emitChange(nextRows);
                }}
                placeholder={placeholder}
                disabled={disabled}
              />
              {rowError ? <div className="structured-input-error">{rowError}</div> : null}
            </label>
            <div className="inline-actions compact-actions">
              <button
                type="button"
                className="danger"
                onClick={() => {
                  const nextRows = rows.filter((_, currentIndex) => currentIndex !== index);
                  setRows(nextRows);
                  emitChange(nextRows);
                }}
                disabled={disabled}
              >
                Remove
              </button>
            </div>
          </div>
        );
      })}
    </div>
  );
}

export {
  createJsonObjectDraftRow,
  createRegexPatternDraftRow,
  createStringMapDraftRow,
  dedupeStrings,
  encodeStringListDraftValue,
  jsonObjectDraftRowsFromValue,
  parseJsonObjectDraftRows,
  parseRegexPatternDraftRows,
  parseStringMapDraftRows,
  regexPatternDraftRowsFromValue,
  stringMapDraftRowsFromValue,
  validateRegexPattern,
};

export type ToolArgumentConstraintOperator =
  | "required"
  | "allowlist"
  | "denylist"
  | "fixed"
  | "pattern"
  | "enum";

export type ToolArgumentPolicyConstraintDraftRow = {
  id: string;
  path: string;
  operator: ToolArgumentConstraintOperator | "";
  valueText: string;
  valuesText: string;
  pattern: string;
  caseSensitive: boolean;
};

export type ToolArgumentPolicyRuleDraftRow = {
  id: string;
  name: string;
  constraints: ToolArgumentPolicyConstraintDraftRow[];
};

export type ToolArgumentPolicyDraftRow = {
  toolName: string;
  rules: ToolArgumentPolicyRuleDraftRow[];
};

export type ToolArgumentPolicyConstraintJson = {
  id: string;
  path: string;
  operator: ToolArgumentConstraintOperator;
  value: unknown | null;
  values: unknown[] | null;
  pattern: string | null;
  case_sensitive: boolean;
};

export type ToolArgumentPolicyRuleJson = {
  id: string;
  name: string | null;
  constraints: ToolArgumentPolicyConstraintJson[];
};

export type ToolArgumentPolicyMap = Record<string, ToolArgumentPolicyRuleJson[]>;

export type ToolArgumentPolicyFieldError = {
  path: string;
  message: string;
};

export type ToolArgumentPolicyDraftValidationResult = {
  value: ToolArgumentPolicyMap | null;
  errors: ToolArgumentPolicyFieldError[];
};

const SUPPORTED_OPERATORS = new Set<ToolArgumentConstraintOperator>([
  "required",
  "allowlist",
  "denylist",
  "fixed",
  "pattern",
  "enum",
]);

const RULE_KEYS = new Set(["id", "name", "constraints"]);
const CONSTRAINT_KEYS = new Set(["id", "path", "operator", "value", "values", "pattern", "case_sensitive"]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function normalizeText(value: unknown): string {
  return String(value ?? "").trim();
}

function normalizeNullableText(value: unknown): string | null {
  const text = normalizeText(value);
  return text ? text : null;
}

function parseBooleanDraftValue(value: unknown, fallback = true): boolean {
  if (value === undefined || value === null) return fallback;
  if (typeof value === "boolean") return value;
  if (typeof value === "string") {
    const normalized = value.trim().toLowerCase();
    if (["true", "1", "yes", "on"].includes(normalized)) return true;
    if (["false", "0", "no", "off"].includes(normalized)) return false;
  }
  return Boolean(value);
}

function normalizeConstraintOperator(value: unknown): ToolArgumentConstraintOperator | null {
  const text = normalizeText(value).toLowerCase();
  if (!text) return null;
  return SUPPORTED_OPERATORS.has(text as ToolArgumentConstraintOperator)
    ? (text as ToolArgumentConstraintOperator)
    : null;
}

function formatDraftValue(value: unknown): string {
  if (value === undefined) return "";
  if (typeof value === "string") return value;
  return JSON.stringify(value);
}

function formatDraftValues(value: unknown): string {
  if (value === undefined || value === null) return "";
  const entries = Array.isArray(value) ? value : [value];
  return entries.map((entry) => formatDraftValue(entry)).join("\n");
}

function parseDraftLineValue(line: string): unknown | null {
  const trimmed = line.trim();
  if (!trimmed) return null;
  try {
    return JSON.parse(trimmed) as unknown;
  } catch {
    return line;
  }
}

function constraintPathBase(toolPathBase: string, ruleIndex: number, constraintIndex?: number): string {
  const rulePath = `${toolPathBase}[${ruleIndex}]`;
  if (constraintIndex === undefined) return rulePath;
  return `${rulePath}.constraints[${constraintIndex}]`;
}

function knownToolNameSet(knownToolNames?: ReadonlyArray<string> | ReadonlySet<string>): ReadonlySet<string> | null {
  if (!knownToolNames) return null;
  return new Set(Array.from(knownToolNames).map((toolName) => normalizeText(toolName)).filter(Boolean));
}

function validateStructuredToolArgumentPoliciesValue(
  value: unknown,
  knownToolNames?: ReadonlyArray<string> | ReadonlySet<string>,
): boolean {
  if (!isRecord(value)) return false;
  const knownTools = knownToolNameSet(knownToolNames);

  for (const [toolName, rulesValue] of Object.entries(value)) {
    const normalizedToolName = normalizeText(toolName);
    if (!normalizedToolName) return false;
    if (knownTools && !knownTools.has(normalizedToolName)) return false;
    if (!Array.isArray(rulesValue)) return false;

    for (const ruleValue of rulesValue) {
      if (!isRecord(ruleValue)) return false;
      if (Object.keys(ruleValue).some((key) => !RULE_KEYS.has(key))) return false;

      if (!("constraints" in ruleValue)) return false;
      if (!Array.isArray(ruleValue.constraints)) return false;

      for (const constraintValue of ruleValue.constraints) {
        if (!isRecord(constraintValue)) return false;
        if (Object.keys(constraintValue).some((key) => !CONSTRAINT_KEYS.has(key))) return false;

        const operator = normalizeConstraintOperator(constraintValue.operator);
        if ("operator" in constraintValue && normalizeText(constraintValue.operator) && !operator) {
          return false;
        }
      }
    }
  }

  return true;
}

function ruleRowsFromRulesValue(rulesValue: unknown[]): ToolArgumentPolicyRuleDraftRow[] {
  return rulesValue.map((ruleValue) => {
    const record = isRecord(ruleValue) ? ruleValue : {};
    const constraintsValue = Array.isArray(record.constraints) ? record.constraints : [];
    return {
      id: normalizeText(record.id),
      name: normalizeText(record.name),
      constraints: constraintsValue.map((constraintValue) => {
        const constraint = isRecord(constraintValue) ? constraintValue : {};
        return {
          id: normalizeText(constraint.id),
          path: normalizeText(constraint.path),
          operator: normalizeConstraintOperator(constraint.operator) ?? "",
          valueText: formatDraftValue(constraint.value),
          valuesText: formatDraftValues(constraint.values),
          pattern: normalizeText(constraint.pattern),
          caseSensitive: parseBooleanDraftValue(constraint.case_sensitive, true),
        };
      }),
    };
  });
}

function parseValueText(valueText: string): { value: unknown | null; error?: string } {
  if (!valueText.trim()) {
    return { value: null, error: "valueText must be a non-empty string" };
  }
  try {
    return { value: JSON.parse(valueText.trim()) as unknown };
  } catch {
    return { value: valueText };
  }
}

function parseValuesText(valuesText: string): { values: unknown[] | null; error?: string } {
  const values: unknown[] = [];
  for (const line of valuesText.split(/\r?\n/)) {
    const parsed = parseDraftLineValue(line);
    if (parsed === null) continue;
    values.push(parsed);
  }
  if (values.length === 0) {
    return { values: null, error: "valuesText must contain at least one value" };
  }
  return { values };
}

function validatePatternText(pattern: string, caseSensitive: boolean): string | null {
  if (!pattern.trim()) {
    return "pattern must be a non-empty string";
  }
  try {
    // Quick browser-side feedback only; Python regex semantics remain the backend source of truth.
    // eslint-disable-next-line no-new
    new RegExp(pattern, caseSensitive ? "" : "i");
    return null;
  } catch (error) {
    return error instanceof Error
      ? `pattern is not a valid JavaScript regular expression: ${error.message}`
      : "pattern is not a valid JavaScript regular expression";
  }
}

export function isStructuredToolArgumentPolicies(
  value: unknown,
  knownToolNames?: ReadonlyArray<string> | ReadonlySet<string>,
): value is ToolArgumentPolicyMap {
  return validateStructuredToolArgumentPoliciesValue(value, knownToolNames);
}

export function toolArgumentPolicyDraftRowsFromValue(
  value: unknown,
  knownToolNames?: ReadonlyArray<string> | ReadonlySet<string>,
): ToolArgumentPolicyDraftRow[] | null {
  if (!isStructuredToolArgumentPolicies(value, knownToolNames)) return null;
  return Object.entries(value).map(([toolName, rulesValue]) => ({
    toolName: normalizeText(toolName),
    rules: ruleRowsFromRulesValue(rulesValue),
  }));
}

export function validateToolArgumentPolicyDraftRows(
  rows: ToolArgumentPolicyDraftRow[],
): ToolArgumentPolicyFieldError[] {
  return toolArgumentPolicyDraftRowsToValue(rows).errors;
}

export function toolArgumentPolicyDraftRowsToValue(
  rows: ToolArgumentPolicyDraftRow[],
): ToolArgumentPolicyDraftValidationResult {
  const errors: ToolArgumentPolicyFieldError[] = [];
  const value: ToolArgumentPolicyMap = {};
  const seenTools = new Set<string>();

  rows.forEach((toolRow, toolIndex) => {
    const toolName = normalizeText(toolRow.toolName);
    const toolPathBase = toolName ? `tool_argument_policies.${toolName}` : `tool_argument_policies[${toolIndex}]`;
    let includeTool = false;

    if (!toolName) {
      errors.push({
        path: `${toolPathBase}.toolName`,
        message: "toolName must be a non-empty string",
      });
    } else if (seenTools.has(toolName)) {
      errors.push({
        path: `${toolPathBase}.toolName`,
        message: `duplicate tool name '${toolName}'`,
      });
    } else {
      seenTools.add(toolName);
      includeTool = true;
    }

    const rules: ToolArgumentPolicyRuleJson[] = [];
    const seenRuleIds = new Set<string>();

    toolRow.rules.forEach((ruleRow, ruleIndex) => {
      const rulePathBase = constraintPathBase(toolPathBase, ruleIndex);
      const ruleId = normalizeText(ruleRow.id);
      const ruleName = normalizeNullableText(ruleRow.name);
      let includeRule = false;

      if (!ruleId) {
        errors.push({
          path: `${rulePathBase}.id`,
          message: "id must be a non-empty string",
        });
      } else if (seenRuleIds.has(ruleId)) {
        errors.push({
          path: `${rulePathBase}.id`,
          message: `duplicate rule id '${ruleId}'`,
        });
      } else {
        seenRuleIds.add(ruleId);
        includeRule = true;
      }

      if (ruleRow.constraints.length === 0) {
        errors.push({
          path: `${rulePathBase}.constraints`,
          message: "constraints must contain at least one constraint",
        });
      }

      const seenConstraintIds = new Set<string>();
      const constraints: ToolArgumentPolicyConstraintJson[] = [];

      ruleRow.constraints.forEach((constraintRow, constraintIndex) => {
        const constraintPath = constraintPathBase(toolPathBase, ruleIndex, constraintIndex);
        const constraintId = normalizeText(constraintRow.id);
        const path = normalizeText(constraintRow.path);
        const operator = normalizeConstraintOperator(constraintRow.operator);
        let includeConstraint = false;

        if (!constraintId) {
          errors.push({
            path: `${constraintPath}.id`,
            message: "id must be a non-empty string",
          });
        } else if (seenConstraintIds.has(constraintId)) {
          errors.push({
            path: `${constraintPath}.id`,
            message: `duplicate constraint id '${constraintId}'`,
          });
        } else {
          seenConstraintIds.add(constraintId);
          includeConstraint = true;
        }

        if (!path) {
          errors.push({
            path: `${constraintPath}.path`,
            message: "path must be a non-empty string",
          });
        }

        if (!operator) {
          errors.push({
            path: `${constraintPath}.operator`,
            message: "operator must be one of: required, allowlist, denylist, fixed, pattern, enum",
          });
          return;
        }

        const constraint: ToolArgumentPolicyConstraintJson = {
          id: constraintId,
          path,
          operator,
          value: null,
          values: null,
          pattern: null,
          case_sensitive: constraintRow.caseSensitive,
        };

        if (operator === "fixed") {
          const parsedValue = parseValueText(constraintRow.valueText);
          if (parsedValue.error) {
            errors.push({
              path: `${constraintPath}.valueText`,
              message: parsedValue.error,
            });
            return;
          }
          constraint.value = parsedValue.value;
        } else if (operator === "allowlist" || operator === "denylist" || operator === "enum") {
          const parsedValues = parseValuesText(constraintRow.valuesText);
          if (parsedValues.error) {
            errors.push({
              path: `${constraintPath}.valuesText`,
              message: parsedValues.error,
            });
            return;
          }
          constraint.values = parsedValues.values;
        } else if (operator === "pattern") {
          const patternError = validatePatternText(constraintRow.pattern, constraintRow.caseSensitive);
          if (patternError) {
            errors.push({
              path: `${constraintPath}.pattern`,
              message: patternError,
            });
            return;
          }
          constraint.pattern = constraintRow.pattern;
        }

        if (includeConstraint) {
          constraints.push(constraint);
        }
      });

      if (includeRule) {
        rules.push({
          id: ruleId,
          name: ruleName,
          constraints,
        });
      }
    });

    if (includeTool) {
      value[toolName] = rules;
    }
  });

  return {
    value: errors.length === 0 ? value : null,
    errors,
  };
}

import type { WorkflowConfig, WorkflowNodeConfig } from "./types";

const BINDING_REFERENCE_RE = /^[A-Za-z0-9][A-Za-z0-9_-]*\.[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*$/;

export type ParsedBindingReference = {
  nodeId: string;
  outputPath: string;
};

export type ParsedBindingExpression = {
  primary: ParsedBindingReference;
  fallback?: ParsedBindingReference | ParsedBindingExpression | unknown;
  hasFallback: boolean;
};

export class BindingParseError extends Error {
  readonly code: "malformed_binding" | "malformed_binding_fallback";

  constructor(code: "malformed_binding" | "malformed_binding_fallback", message: string) {
    super(message);
    this.code = code;
  }
}

export function parseBindingExpression(expression: string, sourceText: string): ParsedBindingExpression {
  const text = expression.trim();
  if (!text) {
    throw new BindingParseError("malformed_binding", "Binding expression is empty");
  }
  const [primaryText, fallbackText] = splitFallbackExpression(text);
  const primary = parseBindingReference(primaryText, sourceText);
  if (fallbackText === null) return { primary, hasFallback: false };
  return {
    primary,
    fallback: parseFallbackValue(fallbackText, sourceText),
    hasFallback: true,
  };
}

export function isParsedBindingReference(value: unknown): value is ParsedBindingReference {
  return isRecord(value) && typeof value.nodeId === "string" && typeof value.outputPath === "string";
}

export function isParsedBindingExpression(value: unknown): value is ParsedBindingExpression {
  return isRecord(value) && isParsedBindingReference(value.primary) && typeof value.hasFallback === "boolean";
}

export function rewriteWorkflowNodeConfigBindings(
  node: WorkflowNodeConfig,
  nodeIdMap: ReadonlyMap<string, string>,
): WorkflowNodeConfig {
  return {
    ...node,
    config: rewriteWorkflowBindingReferences(node.config, nodeIdMap) as Record<string, unknown>,
  };
}

export function rewriteWorkflowConfigBindingReferences(
  config: WorkflowConfig,
  nodeIdMap: ReadonlyMap<string, string>,
): WorkflowConfig {
  if (nodeIdMap.size === 0) return config;
  return {
    ...config,
    triggers: config.triggers.map((node) => rewriteWorkflowNodeConfigBindings(node, nodeIdMap)),
    gates: config.gates.map((node) => rewriteWorkflowNodeConfigBindings(node, nodeIdMap)),
    effects: config.effects.map((node) => rewriteWorkflowNodeConfigBindings(node, nodeIdMap)),
    queries: config.queries.map((node) => rewriteWorkflowNodeConfigBindings(node, nodeIdMap)),
  };
}

export function rewriteWorkflowBindingReferences(
  value: unknown,
  nodeIdMap: ReadonlyMap<string, string>,
): unknown {
  if (nodeIdMap.size === 0) return value;
  if (typeof value === "string") return rewriteWorkflowBindingString(value, nodeIdMap);
  if (Array.isArray(value)) {
    return value.map((item) => rewriteWorkflowBindingReferences(item, nodeIdMap));
  }
  if (isRecord(value)) {
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [key, rewriteWorkflowBindingReferences(item, nodeIdMap)]),
    );
  }
  return value;
}

function rewriteWorkflowBindingString(value: string, nodeIdMap: ReadonlyMap<string, string>): string {
  if (!value.includes("${{")) return value;
  let index = 0;
  let changed = false;
  let result = "";

  while (true) {
    const start = value.indexOf("${{", index);
    if (start === -1) {
      return changed ? result + value.slice(index) : value;
    }
    const end = value.indexOf("}}", start + 3);
    if (end === -1) {
      return changed ? result + value.slice(index) : value;
    }

    result += value.slice(index, start);
    const sourceText = value.slice(start, end + 2);
    const expressionText = value.slice(start + 3, end);
    try {
      const rewrite = rewriteBindingExpression(
        parseBindingExpression(expressionText, sourceText),
        nodeIdMap,
      );
      if (rewrite.changed) {
        result += `\${{${formatBindingExpression(rewrite.expression)}}}`;
        changed = true;
      } else {
        result += sourceText;
      }
    } catch {
      result += sourceText;
    }
    index = end + 2;
  }
}

function rewriteBindingExpression(
  expression: ParsedBindingExpression,
  nodeIdMap: ReadonlyMap<string, string>,
): { expression: ParsedBindingExpression; changed: boolean } {
  const primary = rewriteBindingReference(expression.primary, nodeIdMap);
  if (!expression.hasFallback) {
    return {
      expression: { primary: primary.reference, hasFallback: false },
      changed: primary.changed,
    };
  }

  let fallback = expression.fallback;
  let fallbackChanged = false;
  if (isParsedBindingExpression(fallback)) {
    const rewrittenFallback = rewriteBindingExpression(fallback, nodeIdMap);
    fallback = rewrittenFallback.expression;
    fallbackChanged = rewrittenFallback.changed;
  } else if (isParsedBindingReference(fallback)) {
    const rewrittenFallback = rewriteBindingReference(fallback, nodeIdMap);
    fallback = rewrittenFallback.reference;
    fallbackChanged = rewrittenFallback.changed;
  }

  return {
    expression: {
      primary: primary.reference,
      fallback,
      hasFallback: true,
    },
    changed: primary.changed || fallbackChanged,
  };
}

function rewriteBindingReference(
  reference: ParsedBindingReference,
  nodeIdMap: ReadonlyMap<string, string>,
): { reference: ParsedBindingReference; changed: boolean } {
  const resolved = resolveMappedBindingReference(reference, nodeIdMap);
  const nextNodeId = nodeIdMap.get(resolved.nodeId);
  if (!nextNodeId) return { reference, changed: false };
  return {
    reference: {
      nodeId: nextNodeId,
      outputPath: resolved.outputPath,
    },
    changed: true,
  };
}

function resolveMappedBindingReference(
  reference: ParsedBindingReference,
  nodeIdMap: ReadonlyMap<string, string>,
): ParsedBindingReference {
  const segments = `${reference.nodeId}.${reference.outputPath}`.split(".").filter(Boolean);
  for (let index = segments.length - 1; index > 0; index -= 1) {
    const candidateNodeId = segments.slice(0, index).join(".");
    const candidateOutputPath = segments.slice(index).join(".");
    if (nodeIdMap.has(candidateNodeId)) {
      return { nodeId: candidateNodeId, outputPath: candidateOutputPath };
    }
  }
  return reference;
}

function formatBindingExpression(expression: ParsedBindingExpression): string {
  const primary = formatBindingReference(expression.primary);
  if (!expression.hasFallback) return primary;
  return `${primary} ?? ${formatFallbackValue(expression.fallback)}`;
}

function formatBindingReference(reference: ParsedBindingReference): string {
  return `${reference.nodeId}.${reference.outputPath}`;
}

function formatFallbackValue(value: unknown): string {
  if (isParsedBindingExpression(value)) return formatBindingExpression(value);
  if (isParsedBindingReference(value)) return formatBindingReference(value);
  return JSON.stringify(value);
}

function splitFallbackExpression(expression: string): [string, string | null] {
  let inString = false;
  let quoteChar = "";
  let escape = false;
  for (let index = 0; index < expression.length; index += 1) {
    const char = expression[index];
    if (inString) {
      if (escape) {
        escape = false;
        continue;
      }
      if (char === "\\") {
        escape = true;
        continue;
      }
      if (char === quoteChar) {
        inString = false;
        quoteChar = "";
      }
      continue;
    }
    if (char === "\"" || char === "'") {
      inString = true;
      quoteChar = char;
      continue;
    }
    if (expression.slice(index, index + 2) === "??") {
      return [expression.slice(0, index).trim(), expression.slice(index + 2).trim()];
    }
  }
  return [expression.trim(), null];
}

function parseBindingReference(reference: string, sourceText: string): ParsedBindingReference {
  if (!BINDING_REFERENCE_RE.test(reference)) {
    throw new BindingParseError("malformed_binding", `Malformed output reference in ${sourceText}`);
  }
  const separator = reference.indexOf(".");
  return {
    nodeId: reference.slice(0, separator),
    outputPath: reference.slice(separator + 1),
  };
}

function parseFallbackValue(value: string, sourceText: string): ParsedBindingReference | ParsedBindingExpression | unknown {
  if (!value) {
    throw new BindingParseError("malformed_binding_fallback", `Malformed fallback in ${sourceText}`);
  }
  const [_fallbackPrimary, nestedFallbackText] = splitFallbackExpression(value);
  if (nestedFallbackText !== null) {
    return parseBindingExpression(value, sourceText);
  }
  if (BINDING_REFERENCE_RE.test(value)) {
    return parseBindingReference(value, sourceText);
  }
  try {
    return JSON.parse(value);
  } catch {
    throw new BindingParseError("malformed_binding_fallback", `Malformed fallback in ${sourceText}`);
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

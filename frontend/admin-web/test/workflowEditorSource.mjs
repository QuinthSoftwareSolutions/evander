import { readFileSync, readdirSync } from "node:fs";
import ts from "typescript";

// Existing source contracts apply to the editor and its extracted feature modules.
export function readWorkflowEditorSource() {
  const featureRoot = new URL("../src/features/workflow-editor/", import.meta.url);
  return [
    readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8"),
    ...readdirSync(featureRoot).filter((name) => /\.tsx?$/.test(name)).sort()
      .map((name) => readFileSync(new URL(name, featureRoot), "utf8")),
  ].join("\n\n");
}

// Locate the owning implementation, keeping contracts independent of App wrappers.
export function readWorkflowEditorFunction(name) {
  const featureRoot = new URL("../src/features/workflow-editor/", import.meta.url);
  const files = [
    ...readdirSync(featureRoot).filter((entry) => /\.tsx?$/.test(entry)).sort()
      .map((entry) => new URL(entry, featureRoot)),
    new URL("../src/App.tsx", import.meta.url),
  ];
  for (const file of files) {
    const source = ts.createSourceFile(file.pathname, readFileSync(file, "utf8"), ts.ScriptTarget.Latest, true);
    let match;
    function visit(node) {
      if (ts.isFunctionDeclaration(node) && node.name?.text === name) match = node.getText(source);
      if (!match) ts.forEachChild(node, visit);
    }
    visit(source);
    if (match) return match;
  }
  throw new Error(`Missing workflow editor function: ${name}`);
}

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("../src/App.tsx", import.meta.url), "utf8");
const typesSource = readFileSync(new URL("../src/types.ts", import.meta.url), "utf8");

test("workflow editor exposes workflow pack selection and sends it on save", () => {
  assert.match(source, /function workflowEditorDocumentPath\(workflowPack/);
  assert.match(source, /params\.set\("workflow_pack", trimmedWorkflowPack\)/);
  assert.match(source, /function renderWorkflowPackCanvasControl\(\)/);
  assert.match(source, /className="workflow-pack-canvas-control"/);
  assert.match(source, /aria-label="Workflow pack"/);
  assert.match(source, /function handleWorkflowPackChange/);
  assert.match(source, /workflow_pack: selectedWorkflowPackReference \|\| undefined/);
});

test("workflow editor exposes workflow pack management actions", () => {
  assert.match(source, /function openCreateWorkflowPackModal/);
  assert.match(source, /function openEditWorkflowPackModal/);
  assert.match(source, /function removeSelectedWorkflowPack/);
  assert.match(source, /function renderWorkflowPackModal/);
  assert.match(source, /canManageWorkflowPacks/);
  assert.match(source, /const workflowPackManageLocked = [\s\S]*!canManageWorkflowPacks/);
  assert.match(source, /fetchJson<WorkflowPackMutation>\(\s*"\/api\/workflow-editor\/packs"/);
  assert.match(source, /method: workflowPackModalMode === "create" \? "POST" : "PUT"/);
  assert.match(source, /method: "DELETE"/);
  assert.match(source, /expected_revision:\s*document\.pack_list_revision/);
  assert.match(source, /const \[workflowPackModalTargetReference, setWorkflowPackModalTargetReference\]/);
  assert.match(source, /const workflowPackModalTarget = workflowPackModalTargetReference/);
  assert.match(source, /expected_revision:\s*[\s\S]*editingWorkflowPack\?\.revision/);
  assert.match(source, /onClick=\{\(\) => openEditWorkflowPackModal\(selectedWorkflowPack\)\}/);
  assert.match(source, /<SettingsIcon \/>/);
  assert.match(source, /<PlusIcon \/>/);
  assert.match(source, /const canRemoveEditingWorkflowPack = Boolean\(editingWorkflowPack && workflowPackOptions\.length > 1\)/);
  assert.match(source, /const removed = await removeSelectedWorkflowPack\(editingWorkflowPack\)/);
});

test("workflow editor exposes workflow pack history", () => {
  const historySource = source.slice(
    source.indexOf("function renderWorkflowPackHistory()"),
    source.indexOf("function toggleWorkflowPackHistoryPanel()"),
  );
  const historyPanelSource = source.slice(
    source.indexOf("function renderWorkflowPackHistoryCanvasPanel()"),
    source.indexOf("function renderSavePanelContent()"),
  );
  assert.match(typesSource, /export type WorkflowPackHistoryEntry = \{/);
  assert.match(typesSource, /export type WorkflowPackHistoryDetail = \{/);
  assert.match(source, /function workflowPackHistoryPath\(workflowPack/);
  assert.match(source, /params\.set\("cursor", trimmedCursor\)/);
  assert.match(source, /\/api\/workflow-editor\/packs\/history/);
  assert.match(source, /function loadWorkflowPackHistory\(workflowPack/);
  assert.match(source, /const \[workflowPackHistoryNextCursor, setWorkflowPackHistoryNextCursor\]/);
  assert.match(source, /loadingMore \? \[\.\.\.current, \.\.\.payload\.entries\] : payload\.entries/);
  assert.match(source, /function loadWorkflowPackHistoryDetail\(changeId/);
  assert.match(source, /function renderWorkflowPackHistory\(\)/);
  assert.match(source, /function renderWorkflowPackHistoryDetail\(\)/);
  assert.match(source, /function renderWorkflowPackHistoryCanvasPanel\(\)/);
  assert.match(source, /function toggleWorkflowPackHistoryPanel\(\)/);
  assert.match(source, /<HistoryIcon \/>/);
  assert.match(source, /className="workflow-pack-history-popover"/);
  assert.match(source, /workflowPackHistoryNextCursor \?/);
  assert.match(source, /workflow-pack-history-more/);
  assert.match(source, /<span className="section-label">History<\/span>/);
  assert.match(historyPanelSource, /workflow-pack-history-pack-name/);
  assert.doesNotMatch(historyPanelSource, /Close/);
  assert.doesNotMatch(historySource, /Refresh/);
  assert.match(source, /void loadWorkflowPackHistory\(saved\.selected_workflow_pack\?\.reference/);
});

test("workflow editor keeps pack management out of sidebar panels", () => {
  assert.doesNotMatch(source, /function renderWorkflowPacksPanel\(\)/);
  assert.doesNotMatch(source, /function renderWorkflowPacksSheetContent\(\)/);
  assert.doesNotMatch(source, /function renderWorkflowPackHistoryPanel\(\)/);
  assert.doesNotMatch(source, /function renderWorkflowPackHistorySheetContent\(\)/);
  assert.doesNotMatch(source, /panelButton\("packs", "Packs"/);
  assert.doesNotMatch(source, /panelButton\("history", "History"/);
  assert.match(source, /<div className="section-label">Save<\/div>/);
  assert.match(source, /\{renderWorkflowPackCanvasControl\(\)\}\s*\{renderWorkflowPackHistoryCanvasPanel\(\)\}\s*<ReactFlow/);
});

test("workflow editor refreshes saved pack descriptors after saving document changes", () => {
  assert.match(typesSource, /export type WorkflowEditorSave = \{[\s\S]*pack_list_revision\?: string \| null;/);
  assert.match(source, /const normalizedSavedDocument = normalizeWorkflowEditorDocument/);
  assert.match(
    source,
    /applyWorkflowDocumentState\(\s*normalizedSavedDocument,\s*selectedNodeIds,\s*saved\.validation_diagnostics,\s*\{ markClean: true, nodeEditOrigins: \{\} \},\s*\);/,
  );
});

test("workflow editor uses selected workflow pack when generating node ids", () => {
  assert.match(source, /function workflowPackSlugFromReference\(packReference: unknown\)/);
  assert.match(
    source,
    /function selectedWorkflowPackSlug\(\s*config: WorkflowConfig,\s*selectedWorkflowPackReference\?: string \| null,/,
  );
  assert.match(
    source,
    /const selectedPackSlug = workflowPackSlugFromReference\(selectedWorkflowPackReference\);/,
  );
  assert.match(
    source,
    /generateNodeId\(\s*nextConfig,\s*templateKind,\s*templateNode\.type,\s*templateNode\.display_name,\s*selectedWorkflowPackReference,\s*\)/,
  );
  assert.match(
    source,
    /generateNodeId\(nextConfig, kind, type, catalogEntry\?\.label, selectedWorkflowPackReference\)/,
  );
});

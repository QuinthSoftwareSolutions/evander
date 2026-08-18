import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const styles = readFileSync(new URL("../src/styles.css", import.meta.url), "utf8");
const artifactsPage = readFileSync(
  new URL("../src/features/artifacts/ArtifactsPage.tsx", import.meta.url),
  "utf8",
);
const reportsPage = readFileSync(
  new URL("../src/features/reports/ReportsPage.tsx", import.meta.url),
  "utf8",
);
const toolCallsPage = readFileSync(
  new URL("../src/features/toolCalls/ToolCallsPage.tsx", import.meta.url),
  "utf8",
);
const toolVisibilityPage = readFileSync(
  new URL("../src/features/toolVisibility/ToolVisibilityPage.tsx", import.meta.url),
  "utf8",
);
const usersPage = readFileSync(
  new URL("../src/features/users/UsersPage.tsx", import.meta.url),
  "utf8",
);

function assertPairedTableAndMobileList(source, tableClass, mobileListClass) {
  assert.ok(source.includes("table-wrap mobile-paired-table"), `missing paired table wrapper for ${tableClass}`);
  assert.ok(source.includes(tableClass), `desktop table ${tableClass} should remain rendered`);
  assert.ok(source.includes(mobileListClass), `missing mobile list ${mobileListClass}`);

  const tableIndex = source.indexOf(tableClass);
  const mobileIndex = source.indexOf(mobileListClass);
  assert.ok(tableIndex >= 0, `missing desktop table marker ${tableClass}`);
  assert.ok(mobileIndex > tableIndex, `mobile list ${mobileListClass} should follow desktop table ${tableClass}`);
}

test("shared mobile record card CSS hides paired tables below 720px", () => {
  for (const className of [
    ".mobile-record-list",
    ".mobile-record-card",
    ".mobile-record-row",
    ".mobile-record-actions",
  ]) {
    assert.ok(styles.includes(className), `missing shared responsive class ${className}`);
  }

  assert.match(styles, /@media \(max-width: 720px\) \{[\s\S]*\.mobile-paired-table \{[\s\S]*display: none;/);
  assert.match(styles, /@media \(max-width: 720px\) \{[\s\S]*\.mobile-record-list \{[\s\S]*display: grid;/);
});

test("artifacts keep the desktop table and add selectable mobile cards", () => {
  assertPairedTableAndMobileList(
    artifactsPage,
    "admin-table artifacts-table",
    "artifacts-mobile-record-list",
  );

  const mobileList = artifactsPage.slice(artifactsPage.indexOf("artifacts-mobile-record-list"));
  for (const snippet of [
    "setSelectedKey(item.key);",
    "setResolvedArtifact(null);",
    "formatTimestamp(item.published_at)",
    "item.session_id",
    "formatBytes(item.byte_count)",
    "item.source_file_count",
    "shortSha(item.sha256)",
    "Download artifact",
    "Loading artifacts...",
    "No artifacts matched the current filters.",
  ]) {
    assert.ok(mobileList.includes(snippet), `missing artifacts mobile snippet: ${snippet}`);
  }
});

test("tool calls keep the desktop table and add keyed mobile cards", () => {
  assertPairedTableAndMobileList(
    toolCallsPage,
    "admin-table tool-calls-table",
    "tool-calls-mobile-record-list",
  );

  const mobileList = toolCallsPage.slice(toolCallsPage.indexOf("tool-calls-mobile-record-list"));
  for (const snippet of [
    "toolCallRowKey(item, index)",
    "setSelectedCallKey(itemKey)",
    "item.tool_name",
    "statusLabel(itemStatus)",
    "formatTimestamp(item.created_at)",
    "formatDurationMs(item.duration_ms)",
    'stringField(item, "session_id")',
    'stringField(item, "directory_path")',
    'stringField(item, "request_id")',
    'stringField(item, "correlation_id")',
    'stringField(item, "error_summary", "error")',
    "Loading tool call audit entries...",
    "No tool calls matched the current filters.",
  ]) {
    assert.ok(mobileList.includes(snippet), `missing tool calls mobile snippet: ${snippet}`);
  }
});

test("tool visibility mobile cards keep locked approval tooltip non-focusable", () => {
  assertPairedTableAndMobileList(
    toolVisibilityPage,
    "admin-table tool-visibility-table",
    "tool-visibility-mobile-record-list",
  );

  const mobileList = toolVisibilityPage.slice(toolVisibilityPage.indexOf("tool-visibility-mobile-record-list"));
  assert.ok(
    mobileList.includes("approvalBadge(item, false, { focusable: false })"),
    "locked approval badge tooltip inside the mobile record button must not add a focusable descendant",
  );
});

test("reports keep both desktop tables and add mobile cards for both views", () => {
  assertPairedTableAndMobileList(
    reportsPage,
    "admin-table report-activity-table",
    "report-activity-mobile-record-list",
  );
  assertPairedTableAndMobileList(
    reportsPage,
    "admin-table report-conformance-table",
    "report-conformance-mobile-record-list",
  );
  assertPairedTableAndMobileList(
    reportsPage,
    "admin-table report-failure-clusters-table",
    "report-failure-clusters-mobile-record-list",
  );

  const activityList = reportsPage.slice(reportsPage.indexOf("report-activity-mobile-record-list"));
  for (const snippet of [
    "setSelectedGraphId(graph.graph_id)",
    "graph.name",
    "graph.activity_count",
    "graph.run_count",
    "graph.trigger_event_count",
    "graph.gate_execution_count",
    "graph.effect_execution_count",
    "formatTimestamp(graph.latest_activity_at)",
    "No workflow graphs found.",
  ]) {
    assert.ok(activityList.includes(snippet), `missing reports activity mobile snippet: ${snippet}`);
  }

  const conformanceList = reportsPage.slice(reportsPage.indexOf("report-conformance-mobile-record-list"));
  for (const snippet of [
    "setConformanceSelectedKind(item.kind)",
    "CONFORMANCE_REPORT_TITLES[item.kind]",
    "formatTimestamp(item.generated_at)",
    "formatTimestamp(conformanceReport?.generated_at)",
    "formatRequirementCount(item)",
    "stateLabel(item.state)",
    "summarizeRequirementCounts(item.requirement_counts)",
    "No conformance reports found.",
  ]) {
    assert.ok(conformanceList.includes(snippet), `missing reports conformance mobile snippet: ${snippet}`);
  }

  const failureClusterList = reportsPage.slice(
    reportsPage.indexOf("report-failure-clusters-mobile-record-list"),
  );
  for (const snippet of [
    "setSelectedFailureClusterId(cluster.cluster_id)",
    "workflowFailureClusterStateLabel(cluster.state)",
    "workflowFailureClusterNodeType(cluster)",
    "cluster.session_ids.length",
    "cluster.pr_ids.length",
    "cluster.ado_item_ids.length",
    "workflowFailureClusterRepresentativeRunLabel(representativeExample)",
    "No workflow failure clusters found.",
  ]) {
    assert.ok(failureClusterList.includes(snippet), `missing reports failure clusters mobile snippet: ${snippet}`);
  }
});

test("users keep the desktop records table and add mobile user cards", () => {
  assertPairedTableAndMobileList(
    usersPage,
    "admin-table",
    "users-mobile-record-list",
  );

  const mobileList = usersPage.slice(usersPage.indexOf("users-mobile-record-list"));
  for (const snippet of [
    "setSelectedLogin(user.login)",
    "userLabel(user)",
    "user.login",
    "user.display_name || \"None\"",
    "user.group_assignments",
    "user.contact ? user.contact.name : \"None\"",
    "user.local_password_enabled ? \"Set\" : \"None\"",
    "formatTimestamp(user.last_login_at)",
    "formatTimestamp(user.updated_at)",
    "No user records.",
  ]) {
    assert.ok(mobileList.includes(snippet), `missing users mobile snippet: ${snippet}`);
  }
});

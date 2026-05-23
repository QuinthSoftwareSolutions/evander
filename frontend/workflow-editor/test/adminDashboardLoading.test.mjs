import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const dashboard = readFileSync(
  new URL("../src/features/dashboard/OperationsDashboard.tsx", import.meta.url),
  "utf8",
);

test("dashboard data sources settle independently", () => {
  assert.match(dashboard, /function startDashboardLoad/);
  assert.match(dashboard, /\[task\.key\]: remoteFromResult/);
  assert.doesNotMatch(dashboard, /Promise\.allSettled\(/);
});

test("directory changes do not reload invariant dashboard metadata", () => {
  const directoryEffectStart = dashboard.indexOf("const directoryTasks");
  const agentEffectStart = dashboard.indexOf("async function pollAgents");
  const globalEffectStart = dashboard.indexOf("const globalTasks");
  assert.notEqual(directoryEffectStart, -1);
  assert.notEqual(agentEffectStart, -1);
  assert.notEqual(globalEffectStart, -1);

  const directoryEffect = dashboard.slice(directoryEffectStart, agentEffectStart);
  assert.equal(directoryEffect.includes("/api/admin/agents"), false);
  assert.equal(directoryEffect.includes("/api/admin/release-info"), false);
  assert.equal(directoryEffect.includes("deploymentStatus"), false);
  assert.equal(directoryEffect.includes("workers"), false);
  assert.equal(directoryEffect.includes("capacityHealth"), false);

  const directoryPendingStart = dashboard.indexOf("function createDirectoryDashboardPendingState");
  const globalPendingStart = dashboard.indexOf("function createGlobalDashboardPendingState");
  const directoryPending = dashboard.slice(directoryPendingStart, globalPendingStart);
  assert.equal(directoryPending.includes("releaseInfo"), false);

  const releaseEffectStart = dashboard.indexOf("async function loadReleaseInfo");
  const newsLoaderStart = dashboard.indexOf("async function loadNewsEntry", releaseEffectStart);
  const releaseEffect = dashboard.slice(releaseEffectStart, newsLoaderStart);
  assert.match(releaseEffect, /\/api\/admin\/release-info/);
  assert.match(releaseEffect, /\}, \[refreshNonce\]\);/);
});

test("agent inventory polls through the shared dashboard cache", () => {
  assert.match(dashboard, /const DASHBOARD_AGENT_INVENTORY_POLL_MS = 10_000;/);
  assert.match(dashboard, /function dashboardAgentInventoryUrl/);
  assert.match(dashboard, /\/api\/admin\/agents/);
  assert.match(dashboard, /direct_only: false/);
  assert.match(dashboard, /include_inactive: true/);
  assert.match(dashboard, /backend: "all"/);
  assert.match(dashboard, /void pollLoop\(refreshNonce > 0 \? refreshNonce : null\);/);
  assert.match(dashboard, /window\.setTimeout\(\(\) => \{/);
  assert.match(dashboard, /\}, DASHBOARD_AGENT_INVENTORY_POLL_MS\);/);

  const pollLoopStart = dashboard.indexOf("async function pollLoop");
  const effectEnd = dashboard.indexOf("}, [selectedDirectoryPath, refreshNonce]);", pollLoopStart);
  assert.notEqual(pollLoopStart, -1);
  assert.notEqual(effectEnd, -1);
  const pollLoop = dashboard.slice(pollLoopStart, effectEnd);
  assert.match(pollLoop, /void pollLoop\(\);/);
  assert.equal(pollLoop.includes("_dashboard_refresh: refreshNonce"), false);
});

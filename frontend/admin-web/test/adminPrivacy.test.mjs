import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const page = readFileSync(new URL("../src/features/privacy/PrivacyPage.tsx", import.meta.url), "utf8");
const root = readFileSync(new URL("../src/AdminRoot.tsx", import.meta.url), "utf8");
const routing = readFileSync(new URL("../src/adminRouting.ts", import.meta.url), "utf8");

test("privacy page is lazy routed under governance", () => {
  assert.match(root, /features\/privacy\/PrivacyPage/);
  assert.match(root, /renderedTab === "privacy"/);
  assert.match(routing, /key: "privacy".*group: "governance"/);
});

test("privacy forms use revision guarded scoped requests and do not retain response cache", () => {
  assert.match(page, /expected_revision: baseState.revision/);
  assert.match(page, /payload.directory_path !== requestedDirectoryPath/);
  assert.match(page, /requestId !== requestSequenceRef.current/);
  assert.match(page, /activeDirectoryPathRef.current !== requestedDirectoryPath/);
  assert.match(page, /controller.abort\(\)/);
  assert.match(page, /cache: "no-store"/);
  assert.match(page, /disabled=\{busy \|\| !privilegedUnlocked \|\| !permitted \|\| !reviewedApproval\}/);
});

test("privacy UI distinguishes supported operations and separate export access", () => {
  assert.match(page, /not a compliance certification/);
  assert.match(page, /Manual \/ unsupported/);
  assert.match(page, /incomplete — unsupported datasets remain/);
  assert.match(page, /privacy.export/);
  assert.match(page, /window.confirm/);
  assert.match(page, /nothing is silently retried/);
  assert.match(page, /Reviewer downloads/);
  assert.match(page, /job.scope === selectedDirectoryPath/);
  assert.match(page, /Date.parse\(job.expires_at\) > Date.now\(\)/);
  assert.match(page, /not approved for subject delivery/);
  assert.match(page, /evidence_reference/);
  assert.match(page, /Inspect persisted selection/);
  assert.match(page, /reviewedJob.id === values.job_id\?\.trim\(\)/);
  assert.match(page, /reviewedJob.revision === state\?\.revision/);
  assert.match(page, /"jobs.cancel": \{\s*label: "Cancel future job work", permission: "privacy.approve", destructive: true/);
  assert.match(page, /Job ID to cancel \(completed erasure cannot be undone\)/);
});

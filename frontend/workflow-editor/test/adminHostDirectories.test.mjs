import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const page = readFileSync(
  new URL("../src/features/hostDirectories/HostDirectoriesPage.tsx", import.meta.url),
  "utf8",
);
const root = readFileSync(new URL("../src/AdminRoot.tsx", import.meta.url), "utf8");
const routing = readFileSync(new URL("../src/adminRouting.ts", import.meta.url), "utf8");

test("host directories have a dedicated routed structured admin page", () => {
  assert.match(routing, /\| "host-directories"/);
  assert.match(routing, /label: "Host Directories"/);
  assert.match(root, /features\/hostDirectories\/HostDirectoriesPage/);
  assert.match(root, /renderedTab === "host-directories"/);
  assert.match(page, /Agent capabilities/);
  assert.match(page, /Maximum import bytes/);
  assert.match(page, /Maximum watched entries/);
  assert.doesNotMatch(page, /Directory override JSON/);
});

test("host directory configuration stays revision and directory bound", () => {
  assert.match(page, /const activeDirectoryPathRef = useRef\(selectedDirectoryPath\)/);
  assert.match(page, /payload\.directory_path !== requestedDirectoryPath/);
  assert.match(page, /const requestId = \+\+refreshRequestIdRef\.current/);
  assert.match(page, /expected_revision: baseState\.revision/);
  assert.match(page, /controller\.abort\(\)/);
});

test("host directory page exposes health and recoverable deletion controls", () => {
  assert.match(page, /\/health\?\$\{queryForDirectory/);
  assert.match(page, /\/quarantine\?\$\{queryForDirectory/);
  assert.match(page, /\/quarantine\/\$\{encodeURIComponent\(entry\.recovery_id\)\}\/\$\{action\}/);
  assert.match(page, /Permanently purge/);
  assert.match(page, /Restore/);
  assert.match(page, /!privilegedUnlocked/);
  assert.match(page, /entry\.restore_available === false/);
  assert.match(page, /Recovery record needs attention/);
});

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const connectorsPage = readFileSync(
  new URL("../src/features/connectors/ConnectorsPage.tsx", import.meta.url),
  "utf8",
);

test("connector requests and state stay bound to the initiating directory", () => {
  assert.match(connectorsPage, /const activeDirectoryPathRef = useRef\(selectedDirectoryPath\)/);
  assert.match(connectorsPage, /payload\.directory_path !== requestedDirectoryPath/);
  assert.match(connectorsPage, /const requestId = \+\+refreshRequestIdRef\.current/);
  assert.match(connectorsPage, /controller\.abort\(\)/);
  assert.match(
    connectorsPage,
    /const activeState = state\?\.directory_path === selectedDirectoryPath \? state : null/,
  );
  assert.match(connectorsPage, /expected_revision: baseState\.revision/);
  assert.match(connectorsPage, /queryForDirectory\(requestedDirectoryPath\)/);
});

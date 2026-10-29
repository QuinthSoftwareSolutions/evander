import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const adminRoot = readFileSync(
  new URL("../src/AdminRoot.tsx", import.meta.url),
  "utf8",
);

test("admin shell reloads once when a deployed Vite lazy chunk is stale", () => {
  assert.match(adminRoot, /window\.addEventListener\("vite:preloadError", recoverFromStaleViteChunk\)/);
  assert.match(adminRoot, /function recoverFromStaleViteChunk\(event: Event\) \{[\s\S]*?event\.preventDefault\(\);/);
  assert.match(adminRoot, /if \(staleChunkReloadRequested\) return;/);
  assert.match(adminRoot, /window\.sessionStorage\.setItem\(STALE_CHUNK_RELOAD_STORAGE_KEY, "true"\);/);
  assert.match(adminRoot, /window\.location\.reload\(\);/);
  assert.match(adminRoot, /window\.sessionStorage\.removeItem\(STALE_CHUNK_RELOAD_STORAGE_KEY\);/);
});

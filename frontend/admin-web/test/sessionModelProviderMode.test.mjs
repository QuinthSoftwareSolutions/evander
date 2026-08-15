import assert from "node:assert/strict";
import test from "node:test";

import {
  metadataWithoutSessionModelProviderMode,
  sessionModelProviderModeFromMetadata,
} from "../src/features/sessions/sessionModelProviderMode.ts";

test("session model provider mode is separated from generic metadata", () => {
  const metadata = {
    model_provider_mode: "local_openai",
    keep: "value",
  };

  assert.equal(sessionModelProviderModeFromMetadata(metadata), "local_openai");
  assert.deepEqual(metadataWithoutSessionModelProviderMode(metadata), { keep: "value" });
});

test("unknown session model provider mode falls back to directory default", () => {
  assert.equal(sessionModelProviderModeFromMetadata({ model_provider_mode: "unknown" }), "");
  assert.equal(sessionModelProviderModeFromMetadata({ model_provider_mode: null }), "");
});

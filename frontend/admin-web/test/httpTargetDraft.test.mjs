import assert from "node:assert/strict";
import test from "node:test";

import {
  httpTargetsDraftFromMetadata,
  httpTargetsPayload,
  httpTargetsValidationError,
  metadataWithoutHttpTargets,
} from "../src/features/httpTargets/httpTargetDraft.ts";

test("HTTP target drafts preserve canonical target metadata and normalize payload URLs", () => {
  const metadata = {
    keep: "value",
    http_targets: {
      targets: {
        api: {
          allowed_base_url: "https://api.example.test/v1/",
          metadata: { owner: "workflow" },
        },
      },
    },
  };

  const draft = httpTargetsDraftFromMetadata(metadata);

  assert.equal(draft.legacyFallback, false);
  assert.equal(draft.sourceError, null);
  assert.deepEqual(
    draft.entries.map(({ alias, baseUrl, metadata: targetMetadata }) => ({
      alias,
      baseUrl,
      metadata: targetMetadata,
    })),
    [
      {
        alias: "api",
        baseUrl: "https://api.example.test/v1/",
        metadata: { owner: "workflow" },
      },
    ],
  );
  assert.deepEqual(httpTargetsPayload(draft.entries), {
    targets: {
      api: {
        base_url: "https://api.example.test/v1",
        metadata: { owner: "workflow" },
      },
    },
  });
  assert.deepEqual(metadataWithoutHttpTargets(metadata), { keep: "value" });
});

test("HTTP target drafts surface legacy APP targets and explicit empty payloads", () => {
  const draft = httpTargetsDraftFromMetadata({
    example_app: {
      environments: {
        ui: {
          base_url: "https://ui.example.test",
          metadata: { role: "ui" },
        },
      },
    },
  });

  assert.equal(draft.legacyFallback, true);
  assert.equal(draft.sourceError, null);
  assert.equal(draft.entries[0].alias, "ui");
  assert.deepEqual(httpTargetsPayload([]), { targets: {} });
});

test("HTTP target validation rejects duplicate aliases and non-HTTP URLs", () => {
  const baseEntry = {
    metadata: {},
  };
  assert.equal(
    httpTargetsValidationError([
      { ...baseEntry, id: "one", alias: "api", baseUrl: "https://api.example.test" },
      { ...baseEntry, id: "two", alias: " api ", baseUrl: "https://other.example.test" },
    ]),
    "Duplicate HTTP target alias: api",
  );
  assert.equal(
    httpTargetsValidationError([
      { ...baseEntry, id: "one", alias: "files", baseUrl: "file:///tmp/report" },
    ]),
    "HTTP target files must use an http:// or https:// URL.",
  );
});

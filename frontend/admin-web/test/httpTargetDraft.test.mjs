import assert from "node:assert/strict";
import test from "node:test";

import {
  httpTargetsDraftFromMetadata,
  httpTargetsPayload,
  httpTargetsValidationError,
  metadataWithoutHttpTargets,
} from "../src/features/httpTargets/httpTargetDraft.ts";

test("editing HTTP targets preserves managed login grants until explicitly removed", () => {
  const draft = httpTargetsDraftFromMetadata({http_targets: {targets: {
    browser: {base_url: "https://training.example.test", managed_website_session: "training", metadata: {owner: "workflow"}},
  }}});
  draft.entries[0].baseUrl = "https://training.example.test/new-path";
  let payload = httpTargetsPayload(draft.entries);
  assert.equal(payload.targets.browser.managed_website_session, "training");
  assert.deepEqual(payload.targets.browser.metadata, {owner: "workflow"});
  draft.entries[0].managedWebsiteSession = "";
  payload = httpTargetsPayload(draft.entries);
  assert.equal(Object.hasOwn(payload.targets.browser, "managed_website_session"), false);
});

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

test("HTTP target drafts ignore unrelated metadata and support empty payloads", () => {
  const draft = httpTargetsDraftFromMetadata({
    other_feature: {
      environments: {
        ui: {
          base_url: "https://ui.example.test",
          metadata: { role: "ui" },
        },
      },
    },
  });

  assert.equal(draft.sourceError, null);
  assert.deepEqual(draft.entries, []);
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

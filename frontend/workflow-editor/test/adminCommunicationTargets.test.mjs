import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

test("communication target editor hides inapplicable fields and preserves capability drafts", () => {
  const configuration = readFileSync(
    new URL("../src/features/configuration/ConfigurationScreen.tsx", import.meta.url),
    "utf8",
  );

  assert.match(configuration, /COMMUNICATION_TARGET_KINDS_BY_PROVIDER/);
  assert.match(configuration, /showKindSelector = kindOptions\.length > 1/);
  assert.match(configuration, /showAuthProfile = target\.provider === "email"/);
  assert.match(configuration, /authProfileRef: provider === "email" \? target\.authProfileRef : ""/);
  assert.match(configuration, /draftCapabilities = stringListFromValue\(target\.capabilities\)/);
  assert.match(
    configuration,
    /capabilities: draftCapabilities\.length \? draftCapabilities : policyCapabilities/,
  );
});

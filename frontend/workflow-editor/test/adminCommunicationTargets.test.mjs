import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const configuration = readFileSync(
  new URL("../src/features/configuration/ConfigurationScreen.tsx", import.meta.url),
  "utf8",
);
const styles = readFileSync(new URL("../src/styles.css", import.meta.url), "utf8");

test("communication target editor preserves capability drafts and inserts Teams channels into the draft", () => {
  assert.match(configuration, /COMMUNICATION_TARGET_KINDS_BY_PROVIDER/);
  assert.match(configuration, /showKindSelector = kindOptions\.length > 1/);
  assert.match(configuration, /showAuthProfile = target\.provider === "email"/);
  assert.match(configuration, /authProfileRef: provider === "email" \? target\.authProfileRef : ""/);
  assert.match(configuration, /draftCapabilities = stringListFromValue\(target\.capabilities\)/);
  assert.match(
    configuration,
    /capabilities: draftCapabilities\.length \? draftCapabilities : policyCapabilities/,
  );
  assert.match(configuration, /new URLSearchParams\(\{\s*directory_path: directoryPath,/);
  assert.match(configuration, /team_id: teamId/);
  assert.match(configuration, /include_all: String\(teamsChannelLookupIncludeAll\)/);
  assert.match(configuration, /query\.set\("channel_name", channelName\)/);
  assert.match(configuration, /teamsChannelLookupTeamId/);
  assert.match(configuration, /teamsChannelLookupChannelName/);
  assert.match(configuration, /teamsChannelLookupIncludeAll/);
  assert.match(configuration, /handleTeamsChannelLookup/);
  assert.match(configuration, /insertTeamsChannelLookupResult/);
  assert.match(configuration, /reserveGeneratedCommunicationTargetId/);
  assert.match(configuration, /provider: "teams"/);
  assert.match(configuration, /kind: "teams"/);
  assert.match(configuration, /address: allowlistEntry/);
  assert.match(configuration, /capabilities: \["notify"\]/);
  assert.match(configuration, /Insert target/);
});

test("configuration screen does not render raw secret-bearing Teams auth fields", () => {
  assert.doesNotMatch(configuration, /device_code/i);
  assert.doesNotMatch(configuration, /store_path/i);
  assert.doesNotMatch(configuration, /<span>Access token<\/span>/i);
  assert.doesNotMatch(configuration, /refresh_token/i);
});

test("configuration screen exposes editable model pricing catalog controls", () => {
  assert.match(configuration, /\/api\/configuration\/model-pricing/);
  assert.match(configuration, /Model pricing catalog/);
  assert.match(configuration, /modelPricingPayloadFromDraft/);
  assert.match(configuration, /saveModelPricingCatalog/);
  assert.match(
    configuration,
    /const \[modelPricingPanelCollapsed, setModelPricingPanelCollapsed\] = useState\(true\)/,
  );
  assert.match(configuration, /modelPricingPanelCollapsed/);
  assert.match(configuration, /className="panel-header panel-toggle model-pricing-panel-toggle"/);
  assert.match(configuration, /aria-controls=\{modelPricingPanelBodyId\}/);
  assert.match(configuration, /status-pill state-stale">Unsaved changes<\/span>/);
  assert.match(configuration, /activeModelPricingDraft\.rules\.map/);
  assert.match(configuration, /duplicateModelPricingRule/);
  assert.match(configuration, /deleteModelPricingRule/);
  assert.match(configuration, /modelPricingDraftBaseRevision/);
  assert.match(configuration, /const preserveDirtyDraft = modelPricingDirtyRef\.current/);
  assert.match(configuration, /preserveDirtyDraft \? current : payload\.revision/);
  assert.match(configuration, /preserveDirtyDraft \? current : fallback\.revision/);
  assert.match(configuration, /expected_revision: modelPricingExpectedRevision/);
  assert.match(configuration, /feature-toolbar-actions model-pricing-section-actions/);
  assert.match(configuration, /inline-actions compact-actions model-pricing-source-row/);
  assert.match(configuration, /pricingPathSourceLabel/);
  assert.match(styles, /\.model-pricing-source-row/);
  assert.match(styles, /grid-template-columns: minmax\(0, 1fr\) auto/);
});

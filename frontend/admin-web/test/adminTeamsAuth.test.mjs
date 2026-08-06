import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const configuration = readFileSync(
  new URL("../src/features/configuration/ConfigurationScreen.tsx", import.meta.url),
  "utf8",
);
const styles = readFileSync(new URL("../src/styles.css", import.meta.url), "utf8");

test("configuration screen wires the Teams auth panel to the expected endpoints", () => {
  assert.match(configuration, /\/api\/teams-auth\/status\?directory_path=/);
  assert.match(configuration, /\/api\/teams-auth\/login\?directory_path=/);
  assert.match(configuration, /\/api\/teams-auth\/complete\?directory_path=/);
  assert.match(configuration, /\/api\/teams-auth\/logout\?directory_path=/);
  assert.match(configuration, /login_session_id:/);
  assert.match(configuration, /verification_uri_complete/);
  assert.match(configuration, /userCode/);
  assert.match(configuration, /formatTeamsAuthState/);
  assert.match(configuration, /formatTimestamp\(teamsAuthStatus\?\.issuedAt\)/);
  assert.match(configuration, /formatTimestamp\(teamsAuthStatus\?\.expiresAt\)/);
  assert.match(configuration, /formatTimestamp\(teamsAuthLoginSession\.createdAt\)/);
  assert.match(configuration, /formatRelativeSeconds\(teamsAuthLoginSession\.intervalSeconds\)/);
  assert.match(configuration, /mutationLocked \|\| teamsAuthLoading \|\| teamsAuthSigningIn/);
});

test("configuration screen does not render raw secret-bearing Teams auth fields", () => {
  assert.doesNotMatch(configuration, /device_code/i);
  assert.doesNotMatch(configuration, /store_path/i);
  assert.doesNotMatch(configuration, /access_token/i);
  assert.doesNotMatch(configuration, /refresh_token/i);
});

test("Teams auth device-code card has dark mode contrast styles", () => {
  assert.match(styles, /:root\[data-theme="dark"\] \.teams-auth-session-card\s*\{/);
  assert.match(styles, /:root\[data-theme="dark"\] \.teams-auth-code-line\s*\{/);
  assert.match(styles, /:root\[data-theme="dark"\] \.teams-auth-uri\s*\{/);
  assert.match(styles, /:root\[data-theme="dark"\] \.teams-auth-session-card \.detail-grid strong/);
});

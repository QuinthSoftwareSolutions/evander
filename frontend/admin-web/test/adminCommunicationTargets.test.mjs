import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import {
  communicationTargetDiscordConversationScopeFromValue,
  communicationTargetSenderIdentityFromValue,
} from "../src/features/configuration/configurationDraftState.ts";

const configuration = readFileSync(
  new URL("../src/features/configuration/ConfigurationScreen.tsx", import.meta.url),
  "utf8",
);
test("Discord resolved sender identity survives draft rerenders", () => {
  assert.equal(
    communicationTargetSenderIdentityFromValue({ senderIdentity: "resolved" }),
    "resolved",
  );
  assert.equal(
    communicationTargetSenderIdentityFromValue({ policy: { sender_identity: "resolved" } }),
    "resolved",
  );
  assert.equal(
    communicationTargetSenderIdentityFromValue({
      senderIdentity: "platform",
      policy: { sender_identity: "resolved" },
    }),
    "platform",
  );
});

test("Discord conversation scope survives persisted config and draft rerenders", () => {
  assert.equal(
    communicationTargetDiscordConversationScopeFromValue({
      policy: { discord_conversation_scope: "public_threads" },
    }),
    "public_threads",
  );
  assert.equal(
    communicationTargetDiscordConversationScopeFromValue({
      discordConversationScope: "channel_and_public_threads",
    }),
    "channel_and_public_threads",
  );
  assert.equal(
    communicationTargetDiscordConversationScopeFromValue({
      discordConversationScope: "channel",
      policy: { discord_conversation_scope: "public_threads" },
    }),
    "channel",
  );
});

test("configuration screen does not render raw secret-bearing Teams auth fields", () => {
  assert.doesNotMatch(configuration, /device_code/i);
  assert.doesNotMatch(configuration, /store_path/i);
  assert.doesNotMatch(configuration, /<span>Access token<\/span>/i);
  assert.doesNotMatch(configuration, /refresh_token/i);
});

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  DEFAULT_ASSISTANT_SESSION,
  canSendChatMessage,
  launcherAssistantStatusLabel,
} from "../src/features/chat/status.ts";

const launcher = readFileSync(
  new URL("../src/features/chat/ChatLauncher.tsx", import.meta.url),
  "utf8",
);

test("chat launcher status labels track assistant prewarm state", () => {
  assert.equal(
    launcherAssistantStatusLabel(true, DEFAULT_ASSISTANT_SESSION),
    "Preparing",
  );
  assert.equal(
    launcherAssistantStatusLabel(true, { status: "preparing", error: null, updated_at: null }),
    "Preparing",
  );
  assert.equal(
    launcherAssistantStatusLabel(true, { status: "ready", error: null, updated_at: null }),
    "Ready",
  );
  assert.equal(
    launcherAssistantStatusLabel(true, { status: "failed", error: "offline", updated_at: null }),
    "Failed",
  );
  assert.equal(
    launcherAssistantStatusLabel(false, { status: "ready", error: null, updated_at: null }),
    "Disabled",
  );
});

test("chat input remains enabled while assistant prewarm is preparing or failed", () => {
  for (const status of ["preparing", "failed"]) {
    assert.notEqual(
      launcherAssistantStatusLabel(true, { status, error: null, updated_at: null }),
      "Disabled",
    );
    assert.equal(
      canSendChatMessage({
        assistantEnabled: true,
        sending: false,
        resetting: false,
        threadId: "thread-1",
        draft: "hello",
      }),
      true,
    );
  }
});

test("chat launcher updates preparation status without a blocking loading pill", () => {
  assert.match(launcher, /const \[state, setState\] = useState<LauncherState>\(launcherAllowed \? "minimized" : "disabled"\)/);
  assert.doesNotMatch(launcher, /Loading/);
  assert.doesNotMatch(launcher, /disabled=\{state === "loading"\}/);
  assert.match(launcher, /setAssistantSession\(session\.assistant_session\)/);
  assert.match(launcher, /setAssistantSession\(transcript\.assistant_session\)/);
  assert.match(launcher, /retryChatAssistantPrewarm/);
  assert.match(launcher, /if \(!threadId \|\| !assistantEnabled\) return;/);
});

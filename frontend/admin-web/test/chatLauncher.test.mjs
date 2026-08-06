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
const api = readFileSync(
  new URL("../src/features/chat/api.ts", import.meta.url),
  "utf8",
);
const styles = readFileSync(
  new URL("../src/styles.css", import.meta.url),
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
  assert.doesNotMatch(launcher, /"loading"/);
  assert.doesNotMatch(launcher, /disabled=\{state === "loading"\}/);
  assert.match(launcher, /setAssistantSession\(session\.assistant_session\)/);
  assert.match(launcher, /setAssistantSession\(transcript\.assistant_session\)/);
  assert.match(launcher, /retryChatAssistantPrewarm/);
  assert.match(launcher, /if \(!threadId \|\| !assistantEnabled \|\| !pageVisible\) return;/);
  assert.match(launcher, /globalThis\.document\.addEventListener\("visibilitychange", updateVisibility\)/);
});

test("chat launcher keeps sent messages anchored to the bottom", () => {
  assert.match(launcher, /useLayoutEffect/);
  assert.match(launcher, /function chatIsNearBottom\(element: HTMLDivElement\)/);
  assert.match(
    launcher,
    /element\.scrollHeight - element\.clientHeight - element\.scrollTop <=\s+CHAT_SCROLL_BOTTOM_TOLERANCE_PX/,
  );
  assert.match(launcher, /function scrollChatToBottom\(element: HTMLDivElement\)/);
  assert.match(
    launcher,
    /element\.scrollTop = Math\.max\(0, element\.scrollHeight - element\.clientHeight\)/,
  );
  assert.match(launcher, /const stickToBottomRef = useRef\(true\)/);
  assert.match(launcher, /const forceScrollToBottomRef = useRef\(false\)/);
  assert.match(launcher, /const requestChatScrollToBottom = \(\) => \{/);
  assert.match(launcher, /const handleChatScroll = \(\) => \{/);
  assert.match(launcher, /requestChatScrollToBottom\(\);\s+setSending\(true\)/);
  assert.match(launcher, /if \(nextState === "expanded"\) requestChatScrollToBottom\(\)/);
  assert.match(launcher, /ref=\{scrollRef\} onScroll=\{handleChatScroll\}/);
  assert.match(styles, /\.chat-messages \{[^}]*overflow-anchor: none;/s);
});

test("chat launcher loads long transcripts progressively", () => {
  assert.match(api, /has_older\?: boolean/);
  assert.match(api, /oldest_message_id\?: string \| null/);
  assert.match(api, /newest_message_id\?: string \| null/);
  assert.match(api, /const params = new URLSearchParams\(\)/);
  assert.match(api, /params\.set\("limit", String\(options\.limit\)\)/);
  assert.match(api, /params\.set\("before", options\.before\)/);
  assert.match(launcher, /const CHAT_TRANSCRIPT_PAGE_SIZE = 80/);
  assert.match(launcher, /const \[hasOlderMessages, setHasOlderMessages\] = useState\(false\)/);
  assert.match(launcher, /const \[loadingOlderMessages, setLoadingOlderMessages\] = useState\(false\)/);
  assert.match(launcher, /const messagesRef = useRef<ChatMessage\[\]>\(\[\]\)/);
  assert.match(launcher, /const pendingOlderScrollRestoreRef = useRef<OlderScrollRestore \| null>\(null\)/);
  assert.match(launcher, /function messagesOverlap\(current: ChatMessage\[\], incoming: ChatMessage\[\]\)/);
  assert.match(launcher, /const setVisibleMessages = \(nextMessages: ChatMessage\[\]\) => \{/);
  assert.match(launcher, /const applyLatestTranscript = \(transcript: ChatTranscript\) => \{/);
  assert.match(launcher, /!messagesOverlap\(current, incoming\)/);
  assert.match(launcher, /const nextMessages = replaceStaleWindow \? incoming : mergeMessages\(current, incoming\)/);
  assert.match(launcher, /setHasOlderMessages\(Boolean\(transcript\.has_older\)\)/);
  assert.match(
    launcher,
    /fetchTranscript\(session\.thread_id,\s+\{\s+limit: CHAT_TRANSCRIPT_PAGE_SIZE,\s+\}\)/,
  );
  assert.match(
    launcher,
    /new EventSource\(\s+`\/api\/chat\/stream\/\$\{encodeURIComponent\(threadId\)\}\?limit=\$\{CHAT_TRANSCRIPT_PAGE_SIZE\}`/,
  );
  assert.match(launcher, /applyLatestTranscript\(transcript\)/);
  assert.match(launcher, /element\.scrollTop <= CHAT_SCROLL_TOP_LOAD_THRESHOLD_PX/);
  assert.match(launcher, /async function loadOlderMessages\(\)/);
  assert.match(launcher, /before: oldestMessage\.id/);
  assert.match(launcher, /if \(!messagesRef\.current\.some\(\(message\) => message\.id === oldestMessage\.id\)\) return;/);
  assert.match(
    launcher,
    /setVisibleMessages\(mergeMessages\(transcript\.messages, messagesRef\.current\)\)/,
  );
  assert.match(launcher, /className="chat-load-older-button"/);
  assert.match(styles, /\.chat-load-older-button \{/);
  assert.match(styles, /:root\[data-theme="dark"\] \.chat-load-older-button/);
});

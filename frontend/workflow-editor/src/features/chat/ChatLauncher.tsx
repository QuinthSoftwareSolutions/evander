import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import {
  fetchTranscript,
  postChatMessage,
  retryChatAssistantPrewarm,
  startNewChatSession,
  startOrGetChatSession,
  type AssistantSession,
  type CapabilityMode,
  type ChatMessage,
  type ChatTranscript,
} from "./api";
import {
  DEFAULT_ASSISTANT_SESSION,
  canSendChatMessage,
  capabilityModeLabel,
  launcherAssistantStatusLabel,
} from "./status";
import { AssistantIcon } from "../admin/PageActionIcons";

type LauncherState = "minimized" | "expanded" | "disabled" | "error";
const UI_KEY = "admin-web:chat:ui";
const CHAT_TRANSCRIPT_PAGE_SIZE = 80;
const CHAT_SCROLL_BOTTOM_TOLERANCE_PX = 48;
const CHAT_SCROLL_TOP_LOAD_THRESHOLD_PX = 32;
type OlderScrollRestore = { scrollHeight: number; scrollTop: number };

function currentPageVisible() {
  return typeof globalThis.document === "undefined" || globalThis.document.visibilityState !== "hidden";
}

function formatTime(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" }).format(date);
}

function statusLabel(message: ChatMessage) {
  if (message.role !== "user") return "";
  if (message.status === "failed") return "Failed";
  if (message.status === "answered") return "Answered";
  if (message.status === "running") return "Running";
  return message.status === "queued" ? "Queued" : "";
}

function chatIsNearBottom(element: HTMLDivElement) {
  return (
    element.scrollHeight - element.clientHeight - element.scrollTop <=
    CHAT_SCROLL_BOTTOM_TOLERANCE_PX
  );
}

function scrollChatToBottom(element: HTMLDivElement) {
  element.scrollTop = Math.max(0, element.scrollHeight - element.clientHeight);
}

export function ChatLauncher({
  enabled,
  assistantEnabled,
  showWhenDisabled,
}: {
  enabled: boolean;
  assistantEnabled: boolean;
  showWhenDisabled: boolean;
}) {
  const launcherAllowed = enabled && (assistantEnabled || showWhenDisabled);
  const [state, setState] = useState<LauncherState>(launcherAllowed ? "minimized" : "disabled");
  const [threadId, setThreadId] = useState("");
  const [mode, setMode] = useState<CapabilityMode>("read-only");
  const [assistantSession, setAssistantSession] = useState<AssistantSession>(
    assistantEnabled
      ? DEFAULT_ASSISTANT_SESSION
      : { status: "disabled", error: null, updated_at: null },
  );
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState("");
  const [sending, setSending] = useState(false);
  const [resetting, setResetting] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [pageVisible, setPageVisible] = useState(currentPageVisible);
  const [hasOlderMessages, setHasOlderMessages] = useState(false);
  const [loadingOlderMessages, setLoadingOlderMessages] = useState(false);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const activeThreadRef = useRef("");
  const stickToBottomRef = useRef(true);
  const forceScrollToBottomRef = useRef(false);
  const loadingOlderMessagesRef = useRef(false);
  const pendingOlderScrollRestoreRef = useRef<OlderScrollRestore | null>(null);

  const expanded = state === "expanded";
  const disabled = !assistantEnabled || state === "disabled";
  const canSend = canSendChatMessage({
    assistantEnabled,
    sending,
    resetting,
    threadId,
    draft,
  });
  const modeText = useMemo(() => capabilityModeLabel(mode), [mode]);
  const statusText = useMemo(
    () => launcherAssistantStatusLabel(assistantEnabled, assistantSession),
    [assistantEnabled, assistantSession],
  );
  const minimizedStatusText = state === "error" ? "Failed" : statusText;
  const prewarmFailed = assistantEnabled && assistantSession.status === "failed";

  useEffect(() => {
    activeThreadRef.current = threadId;
  }, [threadId]);

  const requestChatScrollToBottom = () => {
    stickToBottomRef.current = true;
    forceScrollToBottomRef.current = true;
  };

  const handleChatScroll = () => {
    const element = scrollRef.current;
    if (!element) return;
    stickToBottomRef.current = chatIsNearBottom(element);
    if (element.scrollTop <= CHAT_SCROLL_TOP_LOAD_THRESHOLD_PX) {
      void loadOlderMessages();
    }
  };

  useEffect(() => {
    if (typeof globalThis.document === "undefined") return;
    const updateVisibility = () => setPageVisible(currentPageVisible());
    const markHidden = () => setPageVisible(false);
    globalThis.document.addEventListener("visibilitychange", updateVisibility);
    window.addEventListener("pageshow", updateVisibility);
    window.addEventListener("pagehide", markHidden);
    return () => {
      globalThis.document.removeEventListener("visibilitychange", updateVisibility);
      window.removeEventListener("pageshow", updateVisibility);
      window.removeEventListener("pagehide", markHidden);
    };
  }, []);

  useEffect(() => {
    if (!launcherAllowed) {
      setState("disabled");
      return;
    }
    if (!assistantEnabled) {
      setAssistantSession({ status: "disabled", error: null, updated_at: null });
      setState("disabled");
      return;
    }

    let cancelled = false;
    const preferredState: LauncherState =
      window.localStorage.getItem(UI_KEY) === "expanded" ? "expanded" : "minimized";

    async function loadChatSession() {
      setState(preferredState);
      setAssistantSession(DEFAULT_ASSISTANT_SESSION);
      setHasOlderMessages(false);
      setLoadingOlderMessages(false);
      loadingOlderMessagesRef.current = false;
      pendingOlderScrollRestoreRef.current = null;
      setError("");

      try {
        const session = await startOrGetChatSession();
        if (cancelled) return;
        setThreadId(session.thread_id);
        setMode(session.assistant_capability_mode);
        setAssistantSession(session.assistant_session);

        const transcript = await fetchTranscript(session.thread_id, {
          limit: CHAT_TRANSCRIPT_PAGE_SIZE,
        });
        if (cancelled) return;
        if (preferredState === "expanded") requestChatScrollToBottom();
        setMessages(transcript.messages);
        setHasOlderMessages(Boolean(transcript.has_older));
        setMode(transcript.assistant_capability_mode);
        setAssistantSession(transcript.assistant_session);
        setState(preferredState);
      } catch (e) {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : "Failed to load assistant");
        setState("error");
      }
    }

    void loadChatSession();

    return () => {
      cancelled = true;
    };
  }, [launcherAllowed, assistantEnabled]);

  useEffect(() => {
    if (!threadId || !assistantEnabled || !pageVisible) return;
    let closed = false;
    const stream = new EventSource(
      `/api/chat/stream/${encodeURIComponent(threadId)}?limit=${CHAT_TRANSCRIPT_PAGE_SIZE}`,
    );
    stream.addEventListener("snapshot", (event) => {
      if (closed) return;
      try {
        const transcript = JSON.parse((event as MessageEvent).data) as ChatTranscript;
        setMessages((current) => mergeMessages(current, transcript.messages));
        setMode(transcript.assistant_capability_mode);
        setAssistantSession(transcript.assistant_session);
      } catch {
        // Ignore malformed stream events; EventSource will keep the connection alive.
      }
    });
    stream.onerror = () => {
      if (closed) return;
      void fetchTranscript(threadId, { limit: CHAT_TRANSCRIPT_PAGE_SIZE })
        .then((transcript) => {
          if (closed) return;
          setMessages((current) => mergeMessages(current, transcript.messages));
          setMode(transcript.assistant_capability_mode);
          setAssistantSession(transcript.assistant_session);
        })
        .catch(() => undefined);
    };
    return () => {
      closed = true;
      stream.close();
    };
  }, [threadId, assistantEnabled, pageVisible]);

  useLayoutEffect(() => {
    if (!expanded) return;
    const element = scrollRef.current;
    if (!element) return;
    const pendingOlderScrollRestore = pendingOlderScrollRestoreRef.current;
    if (pendingOlderScrollRestore) {
      pendingOlderScrollRestoreRef.current = null;
      element.scrollTop = Math.max(
        0,
        element.scrollHeight -
          pendingOlderScrollRestore.scrollHeight +
          pendingOlderScrollRestore.scrollTop,
      );
      return;
    }
    if (!forceScrollToBottomRef.current && !stickToBottomRef.current) return;
    forceScrollToBottomRef.current = false;
    scrollChatToBottom(element);
  }, [expanded, messages]);

  async function loadOlderMessages() {
    if (!threadId || !hasOlderMessages || loadingOlderMessagesRef.current) return;
    const oldestMessage = messages[0];
    if (!oldestMessage) {
      setHasOlderMessages(false);
      return;
    }
    const targetThreadId = threadId;
    const element = scrollRef.current;
    if (element) {
      pendingOlderScrollRestoreRef.current = {
        scrollHeight: element.scrollHeight,
        scrollTop: element.scrollTop,
      };
    }
    loadingOlderMessagesRef.current = true;
    setLoadingOlderMessages(true);
    try {
      const transcript = await fetchTranscript(targetThreadId, {
        limit: CHAT_TRANSCRIPT_PAGE_SIZE,
        before: oldestMessage.id,
      });
      if (activeThreadRef.current !== targetThreadId) return;
      setMessages((current) => mergeMessages(transcript.messages, current));
      setHasOlderMessages(Boolean(transcript.has_older));
      setMode(transcript.assistant_capability_mode);
      setAssistantSession(transcript.assistant_session);
    } catch {
      if (activeThreadRef.current === targetThreadId) {
        pendingOlderScrollRestoreRef.current = null;
        setError("Failed to load older messages");
      }
    } finally {
      loadingOlderMessagesRef.current = false;
      setLoadingOlderMessages(false);
    }
  }

  async function send(event?: { preventDefault: () => void }) {
    event?.preventDefault();
    const content = draft.trim();
    if (!content || !threadId || sending || resetting) return;
    const targetThreadId = threadId;
    requestChatScrollToBottom();
    setSending(true);
    setError("");
    try {
      const response = await postChatMessage(targetThreadId, content);
      if (activeThreadRef.current !== response.thread_id) return;
      setMessages((current) => mergeMessages(current, response.messages));
      setMode(response.assistant_capability_mode);
      setAssistantSession(response.assistant_session);
      setDraft("");
    } catch (e) {
      if (activeThreadRef.current !== targetThreadId) return;
      setError(e instanceof Error ? e.message : "Failed to send message");
    } finally {
      setSending(false);
    }
  }

  async function startNewChat() {
    if (resetting || !assistantEnabled) return;
    setResetting(true);
    setSending(false);
    setError("");
    try {
      const session = await startNewChatSession();
      activeThreadRef.current = session.thread_id;
      setThreadId(session.thread_id);
      setMode(session.assistant_capability_mode);
      setAssistantSession(session.assistant_session);
      setMessages([]);
      setHasOlderMessages(false);
      loadingOlderMessagesRef.current = false;
      pendingOlderScrollRestoreRef.current = null;
      setLoadingOlderMessages(false);
      setDraft("");
      requestChatScrollToBottom();
      setState("expanded");
      window.localStorage.setItem(UI_KEY, "expanded");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to start new chat");
    } finally {
      setResetting(false);
    }
  }

  async function retryPrewarm() {
    if (!threadId || retrying || !assistantEnabled) return;
    const targetThreadId = threadId;
    setRetrying(true);
    setError("");
    try {
      const session = await retryChatAssistantPrewarm(targetThreadId);
      if (activeThreadRef.current !== session.thread_id) return;
      setMode(session.assistant_capability_mode);
      setAssistantSession(session.assistant_session);
    } catch (e) {
      if (activeThreadRef.current !== targetThreadId) return;
      setError(e instanceof Error ? e.message : "Failed to retry assistant preparation");
    } finally {
      setRetrying(false);
    }
  }

  if (!launcherAllowed) return null;

  const toggleState = () => {
    const nextState = expanded ? "minimized" : "expanded";
    if (nextState === "expanded") requestChatScrollToBottom();
    setState(nextState);
    window.localStorage.setItem(UI_KEY, nextState);
  };

  return (
    <div className={`chat-launcher ${expanded ? "is-expanded" : "is-minimized"}`}>
      {expanded ? (
        <section className="chat-panel" aria-label="Admin assistant">
          <header className="chat-panel-header">
            <div>
              <h2>Assistant</h2>
              <span className={`chat-mode-badge ${mode === "full-access" ? "is-full" : ""}`}>
                {assistantEnabled ? modeText : "Disabled"}
              </span>
            </div>
            <div className="chat-panel-actions">
              <button
                type="button"
                className="chat-new-button"
                onClick={() => void startNewChat()}
                disabled={resetting || !assistantEnabled}
              >
                {resetting ? "Starting" : "New chat"}
              </button>
              <button
                type="button"
                className="chat-icon-button"
                aria-label="Minimize assistant"
                onClick={toggleState}
              >
                -
              </button>
            </div>
          </header>

          <div className="chat-messages" ref={scrollRef} onScroll={handleChatScroll}>
            {hasOlderMessages ? (
              <button
                type="button"
                className="chat-load-older-button"
                onClick={() => void loadOlderMessages()}
                disabled={loadingOlderMessages}
              >
                {loadingOlderMessages ? "Loading" : "Load older"}
              </button>
            ) : null}
            {messages.length === 0 ? (
              <div className="chat-empty">No messages yet.</div>
            ) : (
              messages.map((message) => (
                <article key={message.id} className={`chat-message is-${message.role}`}>
                  <div className="chat-message-bubble">{message.content}</div>
                  <footer>
                    <span>{formatTime(message.created_at)}</span>
                    {statusLabel(message) ? <span>{statusLabel(message)}</span> : null}
                  </footer>
                </article>
              ))
            )}
          </div>

          {error ? <div className="chat-error">{error}</div> : null}
          {prewarmFailed ? (
            <div className="chat-error chat-prewarm-warning">
              <span>
                {assistantSession.error
                  ? `Assistant target failed: ${assistantSession.error}`
                  : "Assistant target failed."}
              </span>
              <button
                type="button"
                className="chat-new-button"
                onClick={() => void retryPrewarm()}
                disabled={retrying}
              >
                {retrying ? "Retrying" : "Retry"}
              </button>
            </div>
          ) : null}

          <form className="chat-input-row" onSubmit={(event) => void send(event)}>
            <textarea
              aria-label="Chat message"
              value={draft}
              rows={2}
              placeholder={disabled ? "Assistant is disabled" : "Message assistant"}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !event.shiftKey) {
                  void send(event);
                }
              }}
              disabled={disabled || sending || resetting}
            />
            <button type="submit" className="chat-send-button" disabled={!canSend}>
              {sending ? "Sending" : "Send"}
            </button>
          </form>
        </section>
      ) : (
        <button
          type="button"
          className="chat-pill"
          onClick={toggleState}
          aria-label={`Assistant ${minimizedStatusText}`}
        >
          <AssistantIcon />
          <span>Assistant</span>
          <small>{minimizedStatusText}</small>
        </button>
      )}
    </div>
  );
}

function mergeMessages(current: ChatMessage[], incoming: ChatMessage[]) {
  const merged = [...current];
  const indexes = new Map(merged.map((message, index) => [message.id, index]));
  for (const message of incoming) {
    const index = indexes.get(message.id);
    if (index === undefined) {
      indexes.set(message.id, merged.length);
      merged.push(message);
      continue;
    }
    merged[index] = message;
  }
  return merged;
}

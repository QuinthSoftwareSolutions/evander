import { useEffect, useMemo, useRef, useState } from "react";
import {
  fetchTranscript,
  postChatMessage,
  startNewChatSession,
  startOrGetChatSession,
  type CapabilityMode,
  type ChatMessage,
  type ChatTranscript,
} from "./api";

type LauncherState = "minimized" | "expanded" | "disabled" | "loading" | "error";
const UI_KEY = "admin-web:chat:ui";

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
  const [state, setState] = useState<LauncherState>(launcherAllowed ? "loading" : "disabled");
  const [threadId, setThreadId] = useState("");
  const [mode, setMode] = useState<CapabilityMode>("read-only");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [error, setError] = useState("");
  const [sending, setSending] = useState(false);
  const [resetting, setResetting] = useState(false);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const activeThreadRef = useRef("");

  const expanded = state === "expanded";
  const disabled = !assistantEnabled || state === "disabled";
  const canSend =
    assistantEnabled && !sending && !resetting && Boolean(threadId) && draft.trim().length > 0;
  const statusText = useMemo(() => {
    if (!assistantEnabled) return "Disabled";
    return mode === "full-access" ? "Full access" : "Read only";
  }, [assistantEnabled, mode]);

  useEffect(() => {
    activeThreadRef.current = threadId;
  }, [threadId]);

  useEffect(() => {
    if (!launcherAllowed || !assistantEnabled) {
      setState("disabled");
      return;
    }

    let cancelled = false;
    const preferredState: LauncherState =
      window.localStorage.getItem(UI_KEY) === "expanded" ? "expanded" : "minimized";

    async function loadChatSession() {
      setState("loading");
      setError("");

      try {
        const session = await startOrGetChatSession();
        if (cancelled) return;
        setThreadId(session.thread_id);
        setMode(session.assistant_capability_mode);

        const transcript = await fetchTranscript(session.thread_id);
        if (cancelled) return;
        setMessages(transcript.messages);
        setMode(transcript.assistant_capability_mode);
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
    if (!expanded || !threadId || !assistantEnabled) return;
    let closed = false;
    const stream = new EventSource(`/api/chat/stream/${encodeURIComponent(threadId)}`);
    stream.addEventListener("snapshot", (event) => {
      if (closed) return;
      try {
        const transcript = JSON.parse((event as MessageEvent).data) as ChatTranscript;
        setMessages(transcript.messages);
        setMode(transcript.assistant_capability_mode);
      } catch {
        // Ignore malformed stream events; EventSource will keep the connection alive.
      }
    });
    stream.onerror = () => {
      if (closed) return;
      void fetchTranscript(threadId)
        .then((transcript) => {
          if (closed) return;
          setMessages(transcript.messages);
          setMode(transcript.assistant_capability_mode);
        })
        .catch(() => undefined);
    };
    return () => {
      closed = true;
      stream.close();
    };
  }, [expanded, threadId, assistantEnabled]);

  useEffect(() => {
    if (!expanded) return;
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [expanded, messages.length]);

  async function send(event?: { preventDefault: () => void }) {
    event?.preventDefault();
    const content = draft.trim();
    if (!content || !threadId || sending || resetting) return;
    const targetThreadId = threadId;
    setSending(true);
    setError("");
    try {
      const response = await postChatMessage(targetThreadId, content);
      if (activeThreadRef.current !== response.thread_id) return;
      setMessages((current) => mergeMessages(current, response.messages));
      setMode(response.assistant_capability_mode);
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
      setMessages([]);
      setDraft("");
      setState("expanded");
      window.localStorage.setItem(UI_KEY, "expanded");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to start new chat");
    } finally {
      setResetting(false);
    }
  }

  if (!launcherAllowed) return null;

  const toggleState = () => {
    const nextState = expanded ? "minimized" : "expanded";
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
                {statusText}
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

          <div className="chat-messages" ref={scrollRef}>
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
          disabled={state === "loading"}
          onClick={toggleState}
        >
          <span>Assistant</span>
          <small>{state === "loading" ? "Loading" : statusText}</small>
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

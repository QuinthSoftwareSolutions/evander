import { useEffect, useState } from "react";
import {
  fetchTranscript,
  postChatMessage,
  startOrGetChatSession,
  type CapabilityMode,
  type ChatMessage,
} from "./api";

type LauncherState = "minimized" | "expanded" | "disabled" | "loading" | "error";
const UI_KEY = "admin-web:chat:ui";

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
        setError(e instanceof Error ? e.message : "Failed");
        setState("error");
      }
    }

    void loadChatSession();

    return () => {
      cancelled = true;
    };
  }, [launcherAllowed, assistantEnabled]);

  const expanded = state === "expanded";

  async function send() {
    const content = draft.trim();
    if (!content || !threadId) return;

    const response = await postChatMessage(threadId, content);
    setMessages((current) => [...current, ...response.messages]);
    setMode(response.assistant_capability_mode);
    setDraft("");
  }

  if (!launcherAllowed) return null;

  return (
    <div className="chat-launcher">
      <button
        type="button"
        className="admin-shell-button"
        disabled={state === "disabled" || state === "loading"}
        onClick={() => {
          const nextState = expanded ? "minimized" : "expanded";
          setState(nextState);
          window.localStorage.setItem(UI_KEY, nextState);
        }}
      >
        {assistantEnabled
          ? mode === "full-access"
            ? "Assistant (full-access)"
            : "Assistant (read-only)"
          : "Assistant (disabled)"}
      </button>
      {expanded ? (
        <div className="chat-panel">
          <div className="chat-messages">
            {messages.map((message) => (
              <div key={message.id}>
                <strong>{message.role}:</strong> {message.content}
              </div>
            ))}
          </div>
          <input
            aria-label="Chat message"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
          />
          <button
            type="button"
            className="admin-shell-button"
            onClick={() => void send()}
            disabled={!assistantEnabled}
          >
            Send
          </button>
        </div>
      ) : null}
      {state === "loading" ? <div>Loading...</div> : null}
      {state === "error" ? <div>{error}</div> : null}
    </div>
  );
}

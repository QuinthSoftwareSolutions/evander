import { useEffect, useState } from "react";
import { fetchTranscript, postChatMessage, startOrGetChatSession, type ChatMessage, type CapabilityMode } from "./api";

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
  useEffect(() => { if (!launcherAllowed || !assistantEnabled) { setState("disabled"); return; } const pref = window.localStorage.getItem(UI_KEY) === "expanded" ? "expanded" : "minimized"; setState("loading"); startOrGetChatSession().then((s) => { setThreadId(s.thread_id); setMode(s.assistant_capability_mode); return fetchTranscript(s.thread_id); }).then((t) => { setMessages(t.messages); setMode(t.assistant_capability_mode); setState(pref); }).catch((e) => { setError(e instanceof Error ? e.message : "Failed"); setState("error"); }); }, [launcherAllowed, assistantEnabled]);
  const expanded = state === "expanded";
  async function send() { if (!draft.trim() || !threadId) return; const r = await postChatMessage(threadId, draft.trim()); setMessages((c) => [...c, ...r.messages]); setMode(r.assistant_capability_mode); setDraft(""); }
  if (!launcherAllowed) return null;
  return <div className="chat-launcher"><button type="button" className="admin-shell-button" disabled={state === "disabled" || state === "loading"} onClick={() => { const n = expanded ? "minimized" : "expanded"; setState(n); window.localStorage.setItem(UI_KEY, n); }}>{assistantEnabled ? (mode === "full-access" ? "Assistant (full-access)" : "Assistant (read-only)") : "Assistant (disabled)"}</button>{expanded ? <div className="chat-panel"><div className="chat-messages">{messages.map((m) => <div key={m.id}><strong>{m.role}:</strong> {m.content}</div>)}</div><input value={draft} onChange={(e) => setDraft(e.target.value)} /><button type="button" className="admin-shell-button" onClick={() => void send()} disabled={!assistantEnabled}>Send</button></div> : null}{state === "loading" ? <div>Loading…</div> : null}{state === "error" ? <div>{error}</div> : null}</div>;
}

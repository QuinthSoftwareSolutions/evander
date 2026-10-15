import { fetchJson } from "../admin/api";
export type ChatPrompt = { content: string; context?: Record<string, unknown> };
export type CapabilityMode = "read-only" | "full-access";
export type AssistantSessionStatus = "disabled" | "not_started" | "preparing" | "ready" | "failed";
export type AssistantSession = { status: AssistantSessionStatus; error: string | null; updated_at: string | null };
export type ChatSession = { user_id: string; thread_id: string; assistant_capability_mode: CapabilityMode; assistant_session: AssistantSession };
export type ChatMessageStatus = "queued" | "running" | "failed" | "answered";
export type ChatMessage = { id: string; role: "user" | "assistant"; content: string; created_at: string; status?: ChatMessageStatus };
export type ChatTranscript = { thread_id: string; messages: ChatMessage[]; assistant_capability_mode: CapabilityMode; assistant_session: AssistantSession; updated_at?: string; has_older?: boolean; oldest_message_id?: string | null; newest_message_id?: string | null };
export const startOrGetChatSession = () => fetchJson<ChatSession>("/api/chat/session", { method: "POST" });
export const startNewChatSession = () => fetchJson<ChatSession>("/api/chat/session/new", { method: "POST" });
export const retryChatAssistantPrewarm = (thread_id: string) => fetchJson<ChatSession>("/api/chat/session/prewarm", { method: "POST", body: JSON.stringify({ thread_id }) });
export const postChatMessage = (thread_id: string, content: string, context?: Record<string, unknown>) => fetchJson<{ thread_id: string; messages: ChatMessage[]; assistant_capability_mode: CapabilityMode; assistant_session: AssistantSession }>("/api/chat/message", { method: "POST", body: JSON.stringify({ thread_id, content, context }) });
export const fetchTranscript = (
  thread_id: string,
  options: { limit?: number; before?: string } = {},
) => {
  const params = new URLSearchParams();
  if (options.limit !== undefined) params.set("limit", String(options.limit));
  if (options.before) params.set("before", options.before);
  const query = params.toString();
  return fetchJson<ChatTranscript>(
    `/api/chat/transcript/${encodeURIComponent(thread_id)}${query ? `?${query}` : ""}`,
  );
};

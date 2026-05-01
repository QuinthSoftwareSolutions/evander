import { fetchJson } from "../admin/api";
export type CapabilityMode = "read-only" | "full-access";
export type ChatSession = { user_id: string; thread_id: string; assistant_capability_mode: CapabilityMode };
export type ChatMessage = { id: string; role: "user" | "assistant"; content: string; created_at: string };
export type ChatTranscript = { thread_id: string; messages: ChatMessage[]; assistant_capability_mode: CapabilityMode };
export const startOrGetChatSession = () => fetchJson<ChatSession>("/api/chat/session", { method: "POST" });
export const postChatMessage = (thread_id: string, content: string) => fetchJson<{ thread_id: string; messages: ChatMessage[]; assistant_capability_mode: CapabilityMode }>("/api/chat/message", { method: "POST", body: JSON.stringify({ thread_id, content }) });
export const fetchTranscript = (thread_id: string) => fetchJson<ChatTranscript>(`/api/chat/transcript/${encodeURIComponent(thread_id)}`);

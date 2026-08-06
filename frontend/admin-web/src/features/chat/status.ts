import type { AssistantSession, CapabilityMode } from "./api";

export const DEFAULT_ASSISTANT_SESSION: AssistantSession = {
  status: "not_started",
  error: null,
  updated_at: null,
};

export function launcherAssistantStatusLabel(
  assistantEnabled: boolean,
  assistantSession: AssistantSession,
) {
  if (!assistantEnabled || assistantSession.status === "disabled") return "Disabled";
  if (assistantSession.status === "ready") return "Ready";
  if (assistantSession.status === "failed") return "Failed";
  return "Preparing";
}

export function capabilityModeLabel(mode: CapabilityMode) {
  return mode === "full-access" ? "Full access" : "Read only";
}

export function canSendChatMessage({
  assistantEnabled,
  sending,
  resetting,
  threadId,
  draft,
}: {
  assistantEnabled: boolean;
  sending: boolean;
  resetting: boolean;
  threadId: string;
  draft: string;
}) {
  return assistantEnabled && !sending && !resetting && Boolean(threadId) && draft.trim().length > 0;
}

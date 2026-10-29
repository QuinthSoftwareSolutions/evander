export type ConfigurationDraftFieldState = {
  include: boolean;
  clear: boolean;
  value: string | boolean;
};

export type CommunicationTargetSenderIdentity = "platform" | "resolved";

export type DiscordConversationScope =
  | "channel"
  | "public_threads"
  | "channel_and_public_threads";

export function communicationTargetSenderIdentityFromValue(
  value: unknown,
): CommunicationTargetSenderIdentity {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return "platform";
  }
  const target = value as Record<string, unknown>;
  const draftSenderIdentity = target.senderIdentity;
  if (draftSenderIdentity === "platform" || draftSenderIdentity === "resolved") {
    return draftSenderIdentity;
  }
  const policy = target.policy;
  const persistedSenderIdentity = (
    policy && typeof policy === "object" && !Array.isArray(policy)
      ? (policy as Record<string, unknown>).sender_identity
      : undefined
  );
  return persistedSenderIdentity === "resolved" ? "resolved" : "platform";
}

export function communicationTargetDiscordConversationScopeFromValue(
  value: unknown,
): DiscordConversationScope {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return "channel";
  }
  const target = value as Record<string, unknown>;
  const draftScope = target.discordConversationScope;
  if (
    draftScope === "channel"
    || draftScope === "public_threads"
    || draftScope === "channel_and_public_threads"
  ) {
    return draftScope;
  }
  const policy = target.policy;
  const persistedScope = (
    policy && typeof policy === "object" && !Array.isArray(policy)
      ? (policy as Record<string, unknown>).discord_conversation_scope
      : undefined
  );
  return persistedScope === "public_threads"
    || persistedScope === "channel_and_public_threads"
      ? persistedScope
      : "channel";
}

export type ConfigurationDraftFieldState = {
  include: boolean;
  clear: boolean;
  value: string | boolean;
};

export type CommunicationTargetSenderIdentity = "platform" | "resolved";

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

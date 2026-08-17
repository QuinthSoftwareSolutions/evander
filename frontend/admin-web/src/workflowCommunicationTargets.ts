import type { WorkflowEditorFieldOption } from "./types";

export type WorkflowCommunicationTargetNodeType =
  | "effect:communication:send-message"
  | "effect:notification:send";

export type WorkflowCommunicationTargetThreadMode = "literal" | "bind" | "template";

export type WorkflowCommunicationTargetValidation = {
  blocked: boolean;
  message: string;
};

export type WorkflowCommunicationTargetInspectorState = {
  targetHelp: string[];
  threadHelp: string;
  advisory: string | null;
  validation: WorkflowCommunicationTargetValidation | null;
};

type WorkflowCommunicationTargetThreadRule = {
  supported?: boolean;
  requires_capability?: string;
  supported_target_shapes?: string[];
  unsupported_target_shapes?: string[];
};

type WorkflowCommunicationTargetMetadata = {
  provider?: unknown;
  address?: unknown;
  target_shape?: unknown;
  policy_capabilities?: unknown;
  discord_conversation_scope?: unknown;
  thread_id?: {
    top_level?: WorkflowCommunicationTargetThreadRule;
    reply?: WorkflowCommunicationTargetThreadRule;
    reply_hint?: WorkflowCommunicationTargetThreadRule;
  } | null;
};

function normalizeText(value: unknown): string {
  return String(value ?? "").trim();
}

function normalizeStringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.map((item) => normalizeText(item)).filter(Boolean);
}

function readMetadata(option: WorkflowEditorFieldOption | undefined): WorkflowCommunicationTargetMetadata | null {
  if (!option?.metadata || typeof option.metadata !== "object" || Array.isArray(option.metadata)) return null;
  return option.metadata as WorkflowCommunicationTargetMetadata;
}

function readThreadRule(
  metadata: WorkflowCommunicationTargetMetadata | null,
  ruleKey: "top_level" | "reply" | "reply_hint",
): WorkflowCommunicationTargetThreadRule | null {
  const threadId = metadata?.thread_id;
  if (!threadId || typeof threadId !== "object" || Array.isArray(threadId)) return null;
  const rule = threadId[ruleKey];
  if (!rule || typeof rule !== "object" || Array.isArray(rule)) return null;
  return rule;
}

function ruleSupportsTargetShape(rule: WorkflowCommunicationTargetThreadRule | null, targetShape: string): boolean {
  if (!rule) return false;
  if (rule.supported === false) return false;
  const supportedTargetShapes = normalizeStringList(rule.supported_target_shapes);
  if (supportedTargetShapes.length > 0) {
    return supportedTargetShapes.includes(targetShape);
  }
  const unsupportedTargetShapes = normalizeStringList(rule.unsupported_target_shapes);
  if (unsupportedTargetShapes.length > 0) {
    return !unsupportedTargetShapes.includes(targetShape);
  }
  return true;
}

function formatCapabilityList(capabilities: string[]): string {
  return capabilities.length > 0 ? capabilities.join(", ") : "none";
}

function targetShapeLabel(targetShape: string): string {
  switch (targetShape) {
    case "teams_channel":
      return "Teams channel";
    case "teams_chat":
      return "Teams chat";
    case "teams_user":
      return "Teams user";
    case "discord_channel":
      return "Discord channel";
    case "email_recipient":
      return "Email recipient";
    default:
      return targetShape || "unknown target";
  }
}

function providerLabel(provider: string): string {
  switch (provider) {
    case "teams":
      return "Teams";
    case "discord":
      return "Discord";
    case "email":
      return "Email";
    default:
      return provider || "unknown provider";
  }
}

function targetThreadHelp(
  nodeType: WorkflowCommunicationTargetNodeType,
  provider: string,
  targetShape: string,
  discordConversationScope: string,
): string {
  if (nodeType === "effect:communication:send-message") {
    if (targetShape === "teams_channel") {
      return provider === "teams"
        ? "Blank thread_id sends a top-level message. Teams channel targets can reply to an existing channel message when the selected target allows replies."
        : "Blank thread_id sends a top-level message. Only Teams channel targets can use thread replies.";
    }
    if (targetShape === "teams_chat" || targetShape === "teams_user") {
      return "Blank thread_id sends a top-level message. Teams chat and Teams user targets do not support thread replies.";
    }
    if (targetShape === "discord_channel") {
      return discordConversationScope === "public_threads"
        ? "This Discord target requires a registered public thread_id and does not send to the parent channel."
        : discordConversationScope === "channel_and_public_threads"
          ? "Blank thread_id sends to the parent Discord channel; a registered public thread_id replies in that thread."
          : "Blank thread_id sends to the Discord channel. This target is not configured for child threads.";
    }
    return "Blank thread_id sends a top-level message. This target does not support thread replies.";
  }

  if (targetShape === "teams_channel") {
    return "Blank thread_id sends a top-level notification. On Teams channels, thread_id is a reply hint, not a destination.";
  }
  if (targetShape === "teams_chat" || targetShape === "teams_user") {
    return "Blank thread_id sends a top-level notification. Teams chat and Teams user targets reject thread replies at runtime.";
  }
  if (targetShape === "discord_channel") {
    return discordConversationScope === "public_threads"
      ? "This Discord target requires a registered public thread_id."
      : discordConversationScope === "channel_and_public_threads"
        ? "Blank thread_id sends to the parent Discord channel; a registered public thread_id sends within that thread."
        : "Blank thread_id sends to the Discord channel. This target is not configured for child threads.";
  }
  if (targetShape === "email_recipient") {
    return "Blank thread_id sends a top-level notification. Email targets create drafts rather than thread replies.";
  }
  return "Blank thread_id sends a top-level notification.";
}

function targetSummaryLines(option: WorkflowEditorFieldOption, metadata: WorkflowCommunicationTargetMetadata | null): string[] {
  const provider = normalizeText(metadata?.provider);
  const targetShape = normalizeText(metadata?.target_shape);
  const capabilities = normalizeStringList(metadata?.policy_capabilities);
  const address = normalizeText(metadata?.address);
  const discordConversationScope = normalizeText(metadata?.discord_conversation_scope);
  const lines: string[] = [];

  if (option.description) {
    lines.push(option.description.trim());
  }
  if (provider) {
    lines.push(`Provider: ${providerLabel(provider)}`);
  }
  if (targetShape) {
    lines.push(`Target shape: ${targetShapeLabel(targetShape)}`);
  }
  if (capabilities.length > 0) {
    lines.push(`Policy capabilities: ${formatCapabilityList(capabilities)}`);
  }
  if (address) {
    lines.push(`Address: ${address}`);
  }
  if (provider === "discord") {
    lines.push(`Conversation scope: ${discordConversationScope || "channel"}`);
  }

  return lines;
}

function validateLiteralThreadSelection(
  nodeType: WorkflowCommunicationTargetNodeType,
  metadata: WorkflowCommunicationTargetMetadata | null,
  threadValue: unknown,
  threadMode: WorkflowCommunicationTargetThreadMode,
): WorkflowCommunicationTargetValidation | null {
  if (threadMode !== "literal") return null;

  const threadText = normalizeText(threadValue);

  const provider = normalizeText(metadata?.provider);
  const targetShape = normalizeText(metadata?.target_shape);
  const topLevelRule = readThreadRule(metadata, "top_level");
  const replyRule = nodeType === "effect:communication:send-message"
    ? readThreadRule(metadata, "reply")
    : readThreadRule(metadata, "reply_hint");

  if (nodeType === "effect:communication:send-message") {
    if (!threadText) {
      if (topLevelRule && !ruleSupportsTargetShape(topLevelRule, targetShape)) {
        return {
          blocked: true,
          message: "This target cannot send a top-level message. Choose a target with write capability.",
        };
      }
      return null;
    }
    if (replyRule && !ruleSupportsTargetShape(replyRule, targetShape)) {
      return {
        blocked: true,
        message: "This target does not support thread replies. Clear thread_id or choose a Teams channel target with reply capability.",
      };
    }
    if (!replyRule && targetShape) {
      if (targetShape === "teams_channel") {
        return {
          blocked: true,
          message: "This Teams channel target does not support thread replies. Clear thread_id or choose a reply-capable target.",
        };
      }
      return {
        blocked: true,
        message: "This target does not support thread replies. Clear thread_id.",
      };
    }
    return null;
  }

  if (!threadText) {
    if (topLevelRule && !ruleSupportsTargetShape(topLevelRule, targetShape)) {
      return {
        blocked: true,
        message: "This target cannot send notifications. Choose a target with notify capability.",
      };
    }
    return null;
  }
  if (replyRule && !ruleSupportsTargetShape(replyRule, targetShape)) {
    const providerText = provider === "email"
      ? "Email targets are not thread destinations."
      : "This target does not accept thread_id as a reply hint.";
    return {
      blocked: true,
      message: providerText,
    };
  }
  if (!replyRule && targetShape) {
    if (targetShape === "teams_channel") {
      return {
        blocked: true,
        message: "This Teams channel target does not accept thread_id as a reply hint.",
      };
    }
    if (targetShape === "teams_chat" || targetShape === "teams_user") {
      return {
        blocked: true,
        message: "Teams chat and Teams user targets reject thread replies at runtime.",
      };
    }
    if (targetShape === "discord_channel") {
      return {
        blocked: true,
        message: "Discord targets are not thread destinations.",
      };
    }
    if (targetShape === "email_recipient") {
      return {
        blocked: true,
        message: "Email targets create drafts rather than thread replies.",
      };
    }
  }

  return null;
}

export function workflowCommunicationTargetInspectorState(
  nodeType: WorkflowCommunicationTargetNodeType | string | undefined,
  option: WorkflowEditorFieldOption | undefined,
  threadValue: unknown,
  threadMode: WorkflowCommunicationTargetThreadMode,
): WorkflowCommunicationTargetInspectorState | null {
  if (nodeType !== "effect:communication:send-message" && nodeType !== "effect:notification:send") {
    return null;
  }

  const metadata = readMetadata(option);
  const targetHelp = option ? targetSummaryLines(option, metadata) : [];
  const provider = normalizeText(metadata?.provider);
  const targetShape = normalizeText(metadata?.target_shape);
  const discordConversationScope = normalizeText(metadata?.discord_conversation_scope);
  const threadHelp = targetThreadHelp(
    nodeType,
    provider,
    targetShape,
    discordConversationScope,
  );
  const validation = validateLiteralThreadSelection(nodeType, metadata, threadValue, threadMode);
  const advisory = threadMode === "literal" || !normalizeText(threadValue)
    ? null
    : "thread_id is resolved at runtime for bound and template values, so the editor cannot fully validate it here.";

  return {
    targetHelp: targetHelp.length > 0 ? targetHelp : ["Select a communication target to see provider and thread support details."],
    threadHelp,
    advisory,
    validation,
  };
}

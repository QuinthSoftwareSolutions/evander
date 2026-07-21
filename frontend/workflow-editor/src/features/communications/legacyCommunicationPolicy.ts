import type { EmailMailboxGrant } from "../admin/types";

export const LEGACY_EMAIL_DRAFT_TOOL_VARIANTS = [
  "email_create_draft",
  "email_create_draft_with_attachments",
] as const;

export const LEGACY_EMAIL_SEND_TOOL_VARIANTS = [
  "email_send",
  "email_send_with_attachments",
  "email_send_approved",
  "email_send_approved_with_attachments",
] as const;

export function legacyEmailMailboxAccess({
  allowedTools,
  folders,
  emailSendEnabled,
}: {
  allowedTools: string[];
  folders: string[];
  emailSendEnabled: boolean;
}): EmailMailboxGrant["access"] {
  const tools = new Set(allowedTools.map((tool) => tool.trim()).filter(Boolean));
  const draftsAvailable = folders.some((folder) => folder.trim() === "drafts");
  if (!draftsAvailable) return "read";
  if (
    emailSendEnabled
    && LEGACY_EMAIL_SEND_TOOL_VARIANTS.some((tool) => tools.has(tool))
  ) {
    return "send";
  }
  if (LEGACY_EMAIL_DRAFT_TOOL_VARIANTS.some((tool) => tools.has(tool))) {
    return "draft";
  }
  return "read";
}

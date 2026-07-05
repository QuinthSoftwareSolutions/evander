import { useEffect, useRef, useState } from "react";

import { ApiError, fetchJson, formatRelativeSeconds, formatTimestamp } from "../admin/api";
import type { AdminResult } from "../admin/AdminContext";
import { useAdminUi } from "../admin/AdminContext";
import { CopyIcon, SaveIcon } from "../admin/PageActionIcons";
import { Tooltip } from "../admin/Tooltip";
import { JsonTextEditor, RegexListEditor, StringListEditor } from "../admin/StructuredPolicyInputs";
import {
  formatJsonTextDraftValue,
  parseJsonTextDraftValue,
  parseStringListDraftValue as parseStringListDraftValueText,
} from "../admin/structuredPolicyInputs";
import {
  emptyModelPricingRuleDraft,
  modelPricingDraftFromCatalog,
  modelPricingPayloadFromDraft,
  type ModelPricingCatalogDraft,
  type ModelPricingCatalogPayload,
  type ModelPricingRuleDraft,
  type ModelPricingRuleSummary,
} from "./modelPricingDraft";

type Diagnostic = { code: string; message: string; field?: string | null };

type ConfigurationField = {
  source_domain: string;
  field_name: string;
  label: string;
  kind: "bool" | "int" | "float" | "literal" | "text" | "multiline_text" | "string_list" | "json";
  options: string[];
  scope?: "directory" | "global";
  root_starts_editable: boolean;
  tooltip: string;
};

type ConfigurationSection = {
  title: string;
  fields: ConfigurationField[];
};

type ConfigurationTab = {
  key: string;
  title: string;
  help_text: string | null;
  underlying_domains: string[];
  sections: ConfigurationSection[];
};

type TabSnapshot = {
  key: string;
  title: string;
  revision: string;
  raw: {
    exists: boolean;
    config: Record<string, unknown>;
  };
  effective: {
    config: Record<string, unknown>;
    validation_diagnostics: Diagnostic[];
    errors: Diagnostic[];
    payload: unknown;
  };
  parent_defaults: {
    directory_path: string | null;
    config: Record<string, unknown>;
    payload: unknown;
  };
};

type DirectoryOverridesSnapshot = {
  directory_path: string;
  exists: boolean;
  domain_count: number;
  field_count: number;
  overrides: Record<string, Record<string, unknown>>;
  errors: Diagnostic[];
  payload: unknown;
};

type ConfigurationState = {
  directory_scoped_config_enabled: boolean;
  selected_directory_path: string;
  selected_directory: {
    path: string;
    parent_path: string | null;
    can_create: boolean;
    can_delete: boolean;
  };
  tabs: ConfigurationTab[];
  tab_snapshots: Record<string, TabSnapshot>;
  directory_overrides: DirectoryOverridesSnapshot;
};

type ModelPricingCatalogStatus = {
  status: "ok" | "error";
  path: string | null;
  path_source: "env" | "default" | string;
  env_var: string;
  version: string | null;
  currency: string | null;
  description: string | null;
  sources: string[];
  rule_count: number;
  rules: ModelPricingRuleSummary[];
  revision: string | null;
  catalog: ModelPricingCatalogPayload;
  editable: boolean;
  write_requires_privileged_unlock: boolean;
  error: string | null;
  error_class: string | null;
};

type DraftFieldState = {
  include: boolean;
  clear: boolean;
  value: string | boolean;
};

type TabDraft = Record<string, DraftFieldState>;
type TabDrafts = Record<string, TabDraft>;

const CONTACTS_TAB_KEY = "contacts";
const CONTACTS_FIELD_NAME = "contacts";
const COMMUNICATION_TARGETS_DOMAIN = "communication_targets";
const COMMUNICATION_TARGETS_FIELD_NAME = "targets";
const COMMUNICATION_TARGET_PROVIDERS = ["teams", "discord", "email", "github", "ado", "jira", "linear"];
const COMMUNICATION_TARGET_KINDS = ["teams", "discord", "email", "pull-request", "ticket"];
const COMMUNICATION_TARGET_KINDS_BY_PROVIDER: Record<string, string[]> = {
  teams: ["teams"],
  discord: ["discord"],
  email: ["email"],
  github: ["pull-request", "ticket"],
  ado: ["pull-request", "ticket"],
  jira: ["ticket"],
  linear: ["ticket"],
};
const COMMUNICATION_TARGET_CAPABILITIES = ["read", "write", "reply", "notify", "comment"];
const APPROVAL_NOTIFICATION_TARGET_FIELD_NAME = "approval_notification_target_ids";
const DEFAULT_CONFIGURATION_TAB_KEY = "people_communications";
const TEAMS_AUTH_CONFIGURATION_TAB_KEY = "people_communications";
const MODEL_PRICING_CONFIGURATION_TAB_KEY = "models_assistant_knowledge";

const MODEL_PRICING_FALLBACK_ERROR = {
  status: "error",
  path: null,
  path_source: "unknown",
  env_var: "EVANDER_MODEL_PRICING_PATH",
  version: null,
  currency: null,
  description: null,
  sources: [],
  rule_count: 0,
  rules: [],
  revision: null,
  catalog: {
    version: "",
    currency: "usd",
    description: null,
    sources: [],
    rules: [],
  },
  editable: false,
  write_requires_privileged_unlock: false,
  error: "Model pricing catalog status is unavailable.",
  error_class: null,
} satisfies ModelPricingCatalogStatus;

type ContactPhoneDraft = {
  label: string;
  number: string;
  primary: boolean;
};

type ContactDraftEntry = {
  id: string;
  isNew: boolean;
  name: string;
  description: string;
  emailAddresses: string[];
  phoneNumbers: ContactPhoneDraft[];
};

type ContactDraftParseResult = {
  entries: ContactDraftEntry[];
  error: string | null;
};

type CommunicationTargetDraftEntry = {
  id: string;
  disabled: boolean;
  provider: string;
  kind: string;
  address: string;
  capabilities: string[];
  authProfileRef: string;
};

type CommunicationTargetDraftParseResult = {
  entries: CommunicationTargetDraftEntry[];
  error: string | null;
};

type TeamsAuthStatusView = {
  directoryPath: string;
  backend: string;
  state: string;
  identityId: string | null;
  accountIdentifier: string | null;
  tenantId: string | null;
  scopes: string[];
  message: string | null;
  issuedAt: string | null;
  expiresAt: string | null;
};

type TeamsAuthLoginSessionView = {
  loginSessionId: string;
  directoryPath: string;
  identityId: string | null;
  tenantId: string | null;
  scopes: string[];
  message: string | null;
  verificationUri: string | null;
  verificationUriComplete: string | null;
  userCode: string | null;
  intervalSeconds: number | null;
  createdAt: string | null;
  expiresAt: string | null;
};

type TeamsChannelLookupResultView = {
  teamId: string;
  channelId: string | null;
  displayName: string;
  membershipType: string | null;
  webUrl: string | null;
  allowlistEntry: string | null;
};

type TeamsChannelLookupView = {
  directoryPath: string;
  backend: string;
  teamId: string;
  channelName: string | null;
  matchCount: number;
  channels: TeamsChannelLookupResultView[];
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isContactsConfigField(field: ConfigurationField): boolean {
  return field.source_domain === CONTACTS_TAB_KEY && field.field_name === CONTACTS_FIELD_NAME;
}

function isCommunicationTargetsConfigField(field: ConfigurationField): boolean {
  return field.source_domain === COMMUNICATION_TARGETS_DOMAIN && field.field_name === COMMUNICATION_TARGETS_FIELD_NAME;
}

function isApprovalNotificationTargetField(field: ConfigurationField): boolean {
  return field.source_domain === "policy" && field.field_name === APPROVAL_NOTIFICATION_TARGET_FIELD_NAME;
}

function stringListFromValue(value: unknown): string[] {
  if (value == null) return [];
  if (Array.isArray(value)) return value.map((item) => String(item ?? ""));
  if (typeof value === "string") return [value];
  return [];
}

function parseStringListDraftValue(value: string | boolean): string[] {
  if (typeof value === "boolean") return [];
  return String(value)
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
}

function encodeStringListDraftValue(entries: string[]): string {
  return entries.join("\n");
}

function dedupeStrings(entries: string[]): string[] {
  const seen = new Set<string>();
  const result: string[] = [];
  for (const entry of entries) {
    const normalized = entry.trim();
    if (!normalized || seen.has(normalized)) continue;
    seen.add(normalized);
    result.push(normalized);
  }
  return result;
}

function phoneDraftsFromValue(value: unknown): ContactPhoneDraft[] {
  if (!Array.isArray(value)) return [];
  return value.map((item) => {
    const record = isRecord(item) ? item : {};
    return {
      label: String(record.label ?? ""),
      number: String(record.number ?? ""),
      primary: Boolean(record.primary),
    };
  });
}

function contactIdSlug(name: string): string {
  const slug = name
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/_+/g, "_")
    .replace(/^_+|_+$/g, "");
  return slug || "contact";
}

function reserveGeneratedContactId(name: string, reservedIds: Set<string>): string {
  const base = contactIdSlug(name);
  let candidate = base;
  let suffix = 2;
  while (reservedIds.has(candidate)) {
    candidate = `${base}_${suffix}`;
    suffix += 1;
  }
  reservedIds.add(candidate);
  return candidate;
}

function communicationTargetIdSlug(name: string): string {
  const slug = name
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-+|-+$/g, "");
  return slug || "channel";
}

function reserveGeneratedCommunicationTargetId(name: string, reservedIds: Set<string>): string {
  const base = `teams-${communicationTargetIdSlug(name)}`;
  let candidate = base;
  let suffix = 2;
  while (reservedIds.has(candidate)) {
    candidate = `${base}-${suffix}`;
    suffix += 1;
  }
  reservedIds.add(candidate);
  return candidate;
}

function contactDraftResolvedIds(entries: ContactDraftEntry[]): string[] {
  const reservedIds = new Set<string>();
  for (const entry of entries) {
    if (!entry.isNew) {
      const existingId = entry.id.trim();
      if (existingId) {
        reservedIds.add(existingId);
      }
    }
  }
  return entries.map((entry) => {
    if (!entry.isNew) {
      return entry.id.trim();
    }
    return reserveGeneratedContactId(entry.name, reservedIds);
  });
}

function contactsValueToDraftEntries(value: unknown): ContactDraftEntry[] {
  if (value == null) return [];
  if (Array.isArray(value)) {
    return value.map((item) => {
      const record = isRecord(item) ? item : {};
      const id = String(record.id ?? "");
      return {
        id,
        isNew: Boolean(record.isNew) || !id.trim(),
        name: String(record.name ?? ""),
        description: String(record.description ?? ""),
        emailAddresses: stringListFromValue(record.emailAddresses ?? record.email_addresses),
        phoneNumbers: phoneDraftsFromValue(record.phoneNumbers ?? record.phone_numbers),
      };
    });
  }
  if (!isRecord(value)) return [];
  return Object.entries(value).map(([id, rawContact]) => {
    const contact = isRecord(rawContact) ? rawContact : {};
    return {
      id,
      isNew: false,
      name: String(contact.name ?? ""),
      description: String(contact.description ?? ""),
      emailAddresses: stringListFromValue(contact.email_addresses),
      phoneNumbers: phoneDraftsFromValue(contact.phone_numbers),
    };
  });
}

function parseContactDraftValue(value: string | boolean): ContactDraftParseResult {
  if (typeof value === "boolean") {
    return { entries: [], error: null };
  }
  const text = String(value).trim();
  if (!text) {
    return { entries: [], error: null };
  }
  try {
    return { entries: contactsValueToDraftEntries(JSON.parse(text)), error: null };
  } catch (reason) {
    return {
      entries: [],
      error: reason instanceof Error ? reason.message : String(reason),
    };
  }
}

function contactsDraftValue(entries: ContactDraftEntry[]): string {
  return JSON.stringify(entries, null, 2);
}

function contactsDraftEntriesToConfig(entries: ContactDraftEntry[]): Record<string, unknown> {
  const contacts: Record<string, unknown> = {};
  const reservedIds = new Set<string>();
  entries.forEach((entry, index) => {
    if (entry.isNew) return;
    const existingId = entry.id.trim();
    if (!existingId) {
      throw new Error(`Contact ${index + 1} id is missing.`);
    }
    if (reservedIds.has(existingId)) {
      throw new Error(`Duplicate contact id: ${existingId}.`);
    }
    reservedIds.add(existingId);
  });
  entries.forEach((entry, index) => {
    const name = entry.name.trim();
    if (!name) {
      throw new Error(`Contact ${entry.id.trim() || index + 1} name is required.`);
    }
    const contactId = entry.isNew
      ? reserveGeneratedContactId(name, reservedIds)
      : entry.id.trim();

    const emailAddresses: string[] = [];
    const seenEmails = new Set<string>();
    for (const rawAddress of entry.emailAddresses) {
      const address = rawAddress.trim();
      if (!address) continue;
      const key = address.toLowerCase();
      if (seenEmails.has(key)) {
        throw new Error(`Contact ${contactId} has a duplicate email address: ${address}.`);
      }
      seenEmails.add(key);
      emailAddresses.push(address);
    }

    const phoneNumbers: ContactPhoneDraft[] = [];
    const seenPhoneLabels = new Set<string>();
    for (const rawPhone of entry.phoneNumbers) {
      const label = rawPhone.label.trim();
      const number = rawPhone.number.trim();
      if (!label && !number) continue;
      if (!label || !number) {
        throw new Error(`Contact ${contactId} phone numbers require both label and number.`);
      }
      const key = label.toLowerCase();
      if (seenPhoneLabels.has(key)) {
        throw new Error(`Contact ${contactId} has a duplicate phone label: ${label}.`);
      }
      seenPhoneLabels.add(key);
      phoneNumbers.push({ label, number, primary: Boolean(rawPhone.primary) });
    }
    if (phoneNumbers.filter((phone) => phone.primary).length > 1) {
      throw new Error(`Contact ${contactId} can have only one primary phone number.`);
    }

    contacts[contactId] = {
      name,
      description: entry.description.trim(),
      email_addresses: emailAddresses,
      phone_numbers: phoneNumbers,
    };
  });
  return contacts;
}

function communicationTargetEntriesFromConfig(value: unknown): CommunicationTargetDraftEntry[] {
  const rawTargets = Array.isArray(value)
    ? value
    : isRecord(value) && Array.isArray(value.targets)
      ? value.targets
      : [];
  return rawTargets.map((item) => {
    const target = isRecord(item) ? item : {};
    const policy = isRecord(target.policy) ? target.policy : {};
    const provider = String(target.provider ?? "teams");
    const draftCapabilities = stringListFromValue(target.capabilities);
    const policyCapabilities = stringListFromValue(policy.capabilities);
    return {
      id: String(target.id ?? ""),
      disabled: Boolean(target.disabled),
      provider,
      kind: normalizeCommunicationTargetKind(provider, String(target.kind ?? "")),
      address: String(target.address ?? ""),
      capabilities: draftCapabilities.length ? draftCapabilities : policyCapabilities,
      authProfileRef: String(target.authProfileRef ?? policy.auth_profile_ref ?? ""),
    };
  });
}

function parseCommunicationTargetDraftValue(value: string | boolean): CommunicationTargetDraftParseResult {
  if (typeof value === "boolean") {
    return { entries: [], error: null };
  }
  const text = String(value).trim();
  if (!text) {
    return { entries: [], error: null };
  }
  try {
    return { entries: communicationTargetEntriesFromConfig(JSON.parse(text)), error: null };
  } catch (reason) {
    return {
      entries: [],
      error: reason instanceof Error ? reason.message : String(reason),
    };
  }
}

function communicationTargetsDraftValue(entries: CommunicationTargetDraftEntry[]): string {
  return JSON.stringify(entries, null, 2);
}

function communicationTargetKindsForProvider(provider: string): string[] {
  return COMMUNICATION_TARGET_KINDS_BY_PROVIDER[provider.trim()] ?? COMMUNICATION_TARGET_KINDS;
}

function defaultKindForCommunicationTargetProvider(provider: string): string {
  return communicationTargetKindsForProvider(provider)[0] ?? "ticket";
}

function normalizeCommunicationTargetKind(provider: string, kind: string): string {
  const normalizedKind = kind.trim();
  const providerKinds = communicationTargetKindsForProvider(provider);
  if (providerKinds.includes(normalizedKind)) {
    return normalizedKind;
  }
  return defaultKindForCommunicationTargetProvider(provider);
}

function communicationTargetAddressPlaceholder(provider: string, kind: string): string {
  if (provider === "teams") return "team-id/channel-id";
  if (provider === "discord") return "123456789012345678";
  if (provider === "email") return "ops@example.com";
  if (kind === "pull-request") return "owner/repo#123";
  if (kind === "ticket") return "project/TICKET-123";
  return "target address";
}

function communicationTargetDraftEntriesToConfig(entries: CommunicationTargetDraftEntry[]): unknown[] {
  const seenIds = new Set<string>();
  return entries.map((entry, index) => {
    const id = entry.id.trim().toLowerCase();
    if (!id) {
      throw new Error(`Communication target ${index + 1} id is required.`);
    }
    if (!/^[a-z0-9](?:[a-z0-9._:-]{0,126}[a-z0-9])?$/.test(id)) {
      throw new Error(`Communication target ${id} has an invalid id.`);
    }
    if (seenIds.has(id)) {
      throw new Error(`Duplicate communication target id: ${id}.`);
    }
    seenIds.add(id);

    if (entry.disabled) {
      return { id, disabled: true };
    }

    const provider = entry.provider.trim();
    const kind = normalizeCommunicationTargetKind(provider, entry.kind);
    const address = entry.address.trim();
    if (!provider || !kind || !address) {
      throw new Error(`Communication target ${id} requires provider, kind, and address.`);
    }
    const capabilities = entry.capabilities.map((capability) => capability.trim()).filter(Boolean);
    const policy: { capabilities: string[]; auth_profile_ref?: string } = { capabilities };
    const authProfileRef = entry.authProfileRef.trim();
    if (provider === "email" && authProfileRef) {
      policy.auth_profile_ref = authProfileRef;
    }
    return {
      id,
      provider,
      kind,
      address,
      policy,
    };
  });
}

function formatJson(value: unknown): string {
  return JSON.stringify(value ?? {}, null, 2);
}

function pricingPathSourceLabel(value: string): string {
  if (value === "env") return "Environment override";
  if (value === "default") return "Default catalog";
  return value || "Unknown";
}

function normalizeFieldValue(field: ConfigurationField, value: unknown): string | boolean {
  if (field.kind === "bool") {
    return Boolean(value);
  }
  if (isContactsConfigField(field)) {
    return JSON.stringify(contactsValueToDraftEntries(value), null, 2);
  }
  if (isCommunicationTargetsConfigField(field)) {
    return JSON.stringify(communicationTargetEntriesFromConfig(value), null, 2);
  }
  if (field.kind === "string_list") {
    return Array.isArray(value) ? value.map((item) => String(item)).join("\n") : value == null ? "" : String(value);
  }
  if (field.kind === "json") {
    return formatJsonTextDraftValue(value);
  }
  return value == null ? "" : String(value);
}

function isGlobalField(field: ConfigurationField): boolean {
  return field.scope === "global";
}

function buildTabDraft(tab: ConfigurationTab | undefined, snapshot: TabSnapshot | undefined, directoryPath: string): TabDraft {
  const draft: TabDraft = {};
  const isRoot = directoryPath === "root";
  for (const section of tab?.sections ?? []) {
    for (const field of section.fields) {
      const rawValue = snapshot?.raw.config[field.field_name];
      const hasExplicitValue = Object.prototype.hasOwnProperty.call(snapshot?.raw.config ?? {}, field.field_name);
      if (isGlobalField(field)) {
        draft[field.field_name] = {
          include: true,
          clear: false,
          value: normalizeFieldValue(field, hasExplicitValue ? rawValue : snapshot?.effective.config[field.field_name]),
        };
        continue;
      }
      if (isRoot) {
        const clear = !hasExplicitValue && !field.root_starts_editable;
        draft[field.field_name] = {
          include: !clear,
          clear,
          value: normalizeFieldValue(field, hasExplicitValue ? rawValue : field.root_starts_editable ? "" : null),
        };
        continue;
      }
      draft[field.field_name] = {
        include: hasExplicitValue,
        clear: hasExplicitValue && rawValue === null,
        value: normalizeFieldValue(field, rawValue),
      };
    }
  }
  return draft;
}

function parseFieldValue(field: ConfigurationField, rawValue: string | boolean): unknown {
  if (field.kind === "bool") {
    return Boolean(rawValue);
  }
  if (isContactsConfigField(field)) {
    const parsed = parseContactDraftValue(rawValue);
    if (parsed.error) {
      throw new Error(`${field.label} contains an invalid draft payload: ${parsed.error}`);
    }
    return contactsDraftEntriesToConfig(parsed.entries);
  }
  if (isCommunicationTargetsConfigField(field)) {
    const parsed = parseCommunicationTargetDraftValue(rawValue);
    if (parsed.error) {
      throw new Error(`${field.label} contains an invalid draft payload: ${parsed.error}`);
    }
    return communicationTargetDraftEntriesToConfig(parsed.entries);
  }
  if (field.kind === "int") {
    const text = String(rawValue).trim();
    if (!text) {
      throw new Error(`${field.label} is required.`);
    }
    const parsed = Number.parseInt(text, 10);
    if (!Number.isFinite(parsed)) {
      throw new Error(`${field.label} must be an integer.`);
    }
    return parsed;
  }
  if (field.kind === "float") {
    const text = String(rawValue).trim();
    if (!text) {
      throw new Error(`${field.label} is required.`);
    }
    const parsed = Number.parseFloat(text);
    if (!Number.isFinite(parsed)) {
      throw new Error(`${field.label} must be a number.`);
    }
    return parsed;
  }
  if (field.kind === "string_list") {
    return String(rawValue)
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean);
  }
  if (field.kind === "literal") {
    return String(rawValue);
  }
  if (field.kind === "json") {
    return parseJsonTextDraftValue(rawValue, field.label);
  }
  return String(rawValue).trim();
}

function inheritedPreview(value: unknown): string {
  if (value === undefined) return "Inherited: not set";
  if (value === null) return "Inherited: null";
  if (value === "") return 'Inherited: ""';
  const rendered = typeof value === "string" ? JSON.stringify(value) : JSON.stringify(value);
  if (!rendered) return "Inherited";
  return rendered.length > 120 ? `Inherited: ${rendered.slice(0, 117)}...` : `Inherited: ${rendered}`;
}

function hasDirtyDrafts(dirtyTabs: Record<string, boolean>): boolean {
  return Object.values(dirtyTabs).some(Boolean);
}

function normalizeDirectoryPath(directoryPath: string | null | undefined): string {
  const trimmed = directoryPath?.trim() ?? "";
  return trimmed || "root";
}

function readRecordString(record: Record<string, unknown>, keys: string[]): string | null {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "string") {
      const trimmed = value.trim();
      if (trimmed) {
        return trimmed;
      }
    } else if (typeof value === "number" && Number.isFinite(value)) {
      return String(value);
    }
  }
  return null;
}

function readRecordStringArray(record: Record<string, unknown>, keys: string[]): string[] {
  for (const key of keys) {
    const value = record[key];
    if (Array.isArray(value)) {
      return value
        .map((item) => (typeof item === "string" ? item.trim() : String(item ?? "").trim()))
        .filter(Boolean);
    }
  }
  return [];
}

function readRecordNumber(record: Record<string, unknown>, keys: string[]): number | null {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "number" && Number.isFinite(value)) {
      return value;
    }
    if (typeof value === "string" && value.trim()) {
      const parsed = Number.parseFloat(value);
      if (Number.isFinite(parsed)) {
        return parsed;
      }
    }
  }
  return null;
}

function readRecordBoolean(record: Record<string, unknown>, keys: string[]): boolean | null {
  for (const key of keys) {
    const value = record[key];
    if (typeof value === "boolean") {
      return value;
    }
    if (typeof value === "string") {
      const normalized = value.trim().toLowerCase();
      if (normalized === "true") return true;
      if (normalized === "false") return false;
    }
  }
  return null;
}

function readNestedRecord(record: Record<string, unknown>, keys: string[]): Record<string, unknown> | null {
  for (const key of keys) {
    const value = record[key];
    if (isRecord(value)) {
      return value;
    }
  }
  return null;
}

function normalizeTeamsAuthState(state: string | null): string {
  const normalized = state?.trim().toLowerCase() ?? "";
  if (!normalized) return "unknown";
  if (normalized === "reauth" || normalized === "reauthorize") return "reauth_required";
  if (normalized === "logged-out") return "logged_out";
  return normalized;
}

function formatTeamsAuthState(state: string | null): string {
  const normalized = normalizeTeamsAuthState(state);
  if (normalized === "reauth_required") return "Reauth required";
  if (normalized === "logged_out") return "Logged out";
  if (normalized === "authenticated") return "Authenticated";
  if (normalized === "pending" || normalized === "polling") return "Pending";
  if (normalized === "unknown") return "Unknown";
  return normalized.replace(/_/g, " ");
}

function isTeamsAuthPendingState(state: string | null): boolean {
  const normalized = normalizeTeamsAuthState(state);
  return normalized === "pending" || normalized === "polling" || normalized === "in_progress";
}

function normalizeTeamsAuthStatus(value: unknown): TeamsAuthStatusView | null {
  if (!isRecord(value)) return null;
  const source = readNestedRecord(value, ["status", "data", "payload"]) ?? value;
  const state = readRecordString(source, ["state", "status", "auth_state", "authentication_state"]);
  const pending = readRecordBoolean(source, ["pending", "polling", "in_progress"]);
  return {
    directoryPath: normalizeDirectoryPath(readRecordString(source, ["directory_path", "directoryPath"])),
    backend: readRecordString(source, ["backend", "teams_backend", "provider_backend"]) ?? "unknown",
    state: normalizeTeamsAuthState(state ?? (pending ? "pending" : null)),
    identityId: readRecordString(source, ["identity_id", "identityId", "account_identity_id"]),
    accountIdentifier: readRecordString(source, ["account_identifier", "accountIdentifier", "account", "email"]),
    tenantId: readRecordString(source, ["tenant_id", "tenantId"]),
    scopes: readRecordStringArray(source, ["scopes", "granted_scopes", "requested_scopes"]),
    message: readRecordString(source, ["message", "detail", "status_message"]),
    issuedAt: readRecordString(source, ["issued_at", "issuedAt", "created_at", "createdAt"]),
    expiresAt: readRecordString(source, ["expires_at", "expiresAt"]),
  };
}

function normalizeTeamsAuthLoginSession(value: unknown): TeamsAuthLoginSessionView | null {
  if (!isRecord(value)) return null;
  const source = readNestedRecord(value, ["login_session", "session", "data", "payload"]) ?? value;
  const loginSessionId = readRecordString(source, ["login_session_id", "loginSessionId", "session_id", "sessionId"]);
  if (!loginSessionId) return null;
  return {
    loginSessionId,
    directoryPath: normalizeDirectoryPath(readRecordString(source, ["directory_path", "directoryPath"])),
    identityId: readRecordString(source, ["identity_id", "identityId", "account_identity_id"]),
    tenantId: readRecordString(source, ["tenant_id", "tenantId"]),
    scopes: readRecordStringArray(source, ["scopes", "granted_scopes", "requested_scopes"]),
    message: readRecordString(source, ["message", "detail", "status_message"]),
    verificationUri: readRecordString(source, ["verification_uri", "verificationUri"]),
    verificationUriComplete: readRecordString(source, ["verification_uri_complete", "verificationUriComplete"]),
    userCode: readRecordString(source, ["user_code", "userCode"]),
    intervalSeconds: readRecordNumber(source, ["interval_seconds", "intervalSeconds"]),
    createdAt: readRecordString(source, ["created_at", "createdAt"]),
    expiresAt: readRecordString(source, ["expires_at", "expiresAt"]),
  };
}

function normalizeTeamsChannelLookupResult(value: unknown): TeamsChannelLookupView | null {
  if (!isRecord(value)) return null;
  const source = readNestedRecord(value, ["result", "data", "payload"]) ?? value;
  const channels = Array.isArray(source.channels) ? source.channels : [];
  return {
    directoryPath: normalizeDirectoryPath(readRecordString(source, ["directory_path", "directoryPath"])),
    backend: readRecordString(source, ["backend", "teams_backend", "provider_backend"]) ?? "unknown",
    teamId: readRecordString(source, ["team_id", "teamId"]) ?? "",
    channelName: readRecordString(source, ["channel_name", "channelName"]),
    matchCount: readRecordNumber(source, ["match_count", "matchCount"]) ?? channels.length,
    channels: channels.map((item) => {
      const channel = isRecord(item) ? item : {};
      return {
        teamId: readRecordString(channel, ["team_id", "teamId"]) ?? "",
        channelId: readRecordString(channel, ["channel_id", "channelId"]),
        displayName: readRecordString(channel, ["display_name", "displayName"]) ?? "",
        membershipType: readRecordString(channel, ["membership_type", "membershipType"]),
        webUrl: readRecordString(channel, ["web_url", "webUrl"]),
        allowlistEntry: readRecordString(channel, ["allowlist_entry", "allowlistEntry"]),
      };
    }),
  };
}

export function ConfigurationScreen() {
  const {
    privilegedUnlocked,
    selectedDirectoryPath: globalSelectedDirectoryPath,
    setSelectedDirectoryPath: setGlobalSelectedDirectoryPath,
    refreshAdminState,
  } = useAdminUi();
  const [state, setState] = useState<ConfigurationState | null>(null);
  const [modelPricingCatalog, setModelPricingCatalog] = useState<ModelPricingCatalogStatus | null>(null);
  const [modelPricingDraft, setModelPricingDraft] = useState<ModelPricingCatalogDraft | null>(null);
  const [modelPricingDraftBaseRevision, setModelPricingDraftBaseRevision] = useState<string | null>(null);
  const [modelPricingDirty, setModelPricingDirty] = useState(false);
  const [modelPricingSaving, setModelPricingSaving] = useState(false);
  const [modelPricingError, setModelPricingError] = useState<string | null>(null);
  const [modelPricingPanelCollapsed, setModelPricingPanelCollapsed] = useState(true);
  const [selectedDirectoryPath, setSelectedDirectoryPath] = useState(globalSelectedDirectoryPath);
  const [selectedTab, setSelectedTab] = useState(DEFAULT_CONFIGURATION_TAB_KEY);
  const [collapsedSections, setCollapsedSections] = useState<Record<string, boolean>>({});
  const [drafts, setDrafts] = useState<TabDrafts>({});
  const [dirtyTabs, setDirtyTabs] = useState<Record<string, boolean>>({});
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [teamsAuthStatus, setTeamsAuthStatus] = useState<TeamsAuthStatusView | null>(null);
  const [teamsAuthLoginSession, setTeamsAuthLoginSession] = useState<TeamsAuthLoginSessionView | null>(null);
  const [teamsAuthNotice, setTeamsAuthNotice] = useState<{ kind: "success" | "warning" | "info"; message: string } | null>(null);
  const [teamsAuthError, setTeamsAuthError] = useState<string | null>(null);
  const [teamsAuthLoading, setTeamsAuthLoading] = useState(false);
  const [teamsAuthSigningIn, setTeamsAuthSigningIn] = useState(false);
  const [teamsAuthCompleting, setTeamsAuthCompleting] = useState(false);
  const [teamsAuthSigningOut, setTeamsAuthSigningOut] = useState(false);
  const [teamsAuthPollTimeoutSeconds, setTeamsAuthPollTimeoutSeconds] = useState("5");
  const [teamsChannelLookupTeamId, setTeamsChannelLookupTeamId] = useState("");
  const [teamsChannelLookupChannelName, setTeamsChannelLookupChannelName] = useState("");
  const [teamsChannelLookupIncludeAll, setTeamsChannelLookupIncludeAll] = useState(false);
  const [teamsChannelLookupResult, setTeamsChannelLookupResult] = useState<TeamsChannelLookupView | null>(null);
  const [teamsChannelLookupLoading, setTeamsChannelLookupLoading] = useState(false);
  const [teamsChannelLookupError, setTeamsChannelLookupError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const teamsAuthDirectoryPathRef = useRef(normalizeDirectoryPath(selectedDirectoryPath));
  const modelPricingDirtyRef = useRef(false);

  async function loadModelPricingCatalog() {
    try {
      const preserveDirtyDraft = modelPricingDirtyRef.current;
      const payload = await fetchJson<ModelPricingCatalogStatus>("/api/configuration/model-pricing");
      setModelPricingCatalog(payload);
      setModelPricingDraft((current) =>
        preserveDirtyDraft && current ? current : modelPricingDraftFromCatalog(payload.catalog),
      );
      setModelPricingDraftBaseRevision((current) =>
        preserveDirtyDraft ? current : payload.revision,
      );
      if (!preserveDirtyDraft) {
        setModelPricingError(null);
      }
    } catch (reason) {
      const preserveDirtyDraft = modelPricingDirtyRef.current;
      const fallback = {
        ...MODEL_PRICING_FALLBACK_ERROR,
        error: reason instanceof Error ? reason.message : String(reason),
      };
      setModelPricingCatalog(fallback);
      setModelPricingDraft((current) =>
        preserveDirtyDraft && current ? current : modelPricingDraftFromCatalog(fallback.catalog),
      );
      setModelPricingDraftBaseRevision((current) =>
        preserveDirtyDraft ? current : fallback.revision,
      );
    }
  }

  async function loadState(directoryPath: string, options?: { preserveDirty?: boolean }) {
    const payload = await fetchJson<ConfigurationState>(
      `/api/configuration/state?directory_path=${encodeURIComponent(directoryPath)}`,
    );
    await loadModelPricingCatalog();
    setState((current) => {
      if (!options?.preserveDirty || !current) {
        return payload;
      }
      const tab_snapshots = { ...payload.tab_snapshots };
      for (const tab of payload.tabs) {
        if (dirtyTabs[tab.key] && current.tab_snapshots[tab.key]) {
          tab_snapshots[tab.key] = current.tab_snapshots[tab.key];
        }
      }
      return { ...payload, tab_snapshots };
    });
    setSelectedDirectoryPath(payload.selected_directory_path);
    if (payload.selected_directory_path !== globalSelectedDirectoryPath) {
      setGlobalSelectedDirectoryPath(payload.selected_directory_path);
    }
    setSelectedTab((current) => (
      payload.tabs.some((tab) => tab.key === current)
        ? current
        : payload.tabs[0]?.key ?? DEFAULT_CONFIGURATION_TAB_KEY
    ));
    setDrafts((current) => {
      const next = { ...current };
      for (const tab of payload.tabs) {
        if (options?.preserveDirty && dirtyTabs[tab.key]) {
          continue;
        }
        next[tab.key] = buildTabDraft(tab, payload.tab_snapshots[tab.key], payload.selected_directory_path);
      }
      return next;
    });
    if (!options?.preserveDirty) {
      setDirtyTabs({});
    }
  }

  function updateModelPricingDraft(
    updater: (draft: ModelPricingCatalogDraft) => ModelPricingCatalogDraft,
  ) {
    setModelPricingDraft((current) =>
      updater(current ?? modelPricingDraftFromCatalog(modelPricingCatalog?.catalog ?? null)),
    );
    setModelPricingDraftBaseRevision((current) => current ?? modelPricingCatalog?.revision ?? null);
    modelPricingDirtyRef.current = true;
    setModelPricingDirty(true);
    setModelPricingError(null);
  }

  function updateModelPricingSource(index: number, value: string) {
    updateModelPricingDraft((draft) => ({
      ...draft,
      sources: draft.sources.map((source, sourceIndex) =>
        sourceIndex === index ? value : source,
      ),
    }));
  }

  function addModelPricingSource() {
    updateModelPricingDraft((draft) => ({ ...draft, sources: [...draft.sources, ""] }));
  }

  function removeModelPricingSource(index: number) {
    updateModelPricingDraft((draft) => ({
      ...draft,
      sources: draft.sources.filter((_, sourceIndex) => sourceIndex !== index),
    }));
  }

  function updateModelPricingRule(
    index: number,
    patch: Partial<ModelPricingRuleDraft>,
  ) {
    updateModelPricingDraft((draft) => ({
      ...draft,
      rules: draft.rules.map((rule, ruleIndex) =>
        ruleIndex === index ? { ...rule, ...patch } : rule,
      ),
    }));
  }

  function addModelPricingRule() {
    updateModelPricingDraft((draft) => ({
      ...draft,
      rules: [...draft.rules, emptyModelPricingRuleDraft(draft.rules.length + 1)],
    }));
  }

  function duplicateModelPricingRule(index: number) {
    updateModelPricingDraft((draft) => {
      const source = draft.rules[index] ?? emptyModelPricingRuleDraft(draft.rules.length + 1);
      return {
        ...draft,
        rules: [
          ...draft.rules,
          {
            ...source,
            id: source.id.trim() ? `${source.id.trim()}-copy` : `openai:new-rule-${draft.rules.length + 1}`,
          },
        ],
      };
    });
  }

  function deleteModelPricingRule(index: number) {
    updateModelPricingDraft((draft) => ({
      ...draft,
      rules: draft.rules.filter((_, ruleIndex) => ruleIndex !== index),
    }));
  }

  function discardModelPricingChanges() {
    setModelPricingDraft(modelPricingDraftFromCatalog(modelPricingCatalog?.catalog ?? null));
    setModelPricingDraftBaseRevision(modelPricingCatalog?.revision ?? null);
    modelPricingDirtyRef.current = false;
    setModelPricingDirty(false);
    setModelPricingError(null);
  }

  async function saveModelPricingCatalog() {
    if (!modelPricingCatalog || !modelPricingDraft) {
      setModelPricingError("Model pricing catalog is not loaded.");
      return;
    }
    const modelPricingExpectedRevision = modelPricingDraftBaseRevision;
    if (!modelPricingExpectedRevision) {
      setModelPricingError("Model pricing catalog revision is unavailable.");
      return;
    }
    if (mutationLocked) {
      setModelPricingError("Privileged operations are locked.");
      return;
    }
    let catalogPayload: ModelPricingCatalogPayload;
    try {
      catalogPayload = modelPricingPayloadFromDraft(modelPricingDraft);
    } catch (reason) {
      setModelPricingError(reason instanceof Error ? reason.message : String(reason));
      return;
    }
    setModelPricingSaving(true);
    setModelPricingError(null);
    setStatusMessage(null);
    try {
      const payload = await fetchJson<ModelPricingCatalogStatus>(
        "/api/configuration/model-pricing",
        {
          method: "PUT",
          body: JSON.stringify({
            catalog: catalogPayload,
            expected_revision: modelPricingExpectedRevision,
          }),
        },
      );
      setModelPricingCatalog(payload);
      setModelPricingDraft(modelPricingDraftFromCatalog(payload.catalog));
      setModelPricingDraftBaseRevision(payload.revision);
      modelPricingDirtyRef.current = false;
      setModelPricingDirty(false);
      setStatusMessage("Saved model pricing catalog.");
    } catch (reason) {
      setModelPricingError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setModelPricingSaving(false);
    }
  }

  useEffect(() => {
    let cancelled = false;
    async function refresh() {
      if (state !== null && globalSelectedDirectoryPath === selectedDirectoryPath) {
        return;
      }
      if (
        state !== null
        && globalSelectedDirectoryPath !== selectedDirectoryPath
        && hasDirtyDrafts(dirtyTabs)
        && !window.confirm("Discard unsaved configuration edits and switch directories?")
      ) {
        setGlobalSelectedDirectoryPath(selectedDirectoryPath);
        return;
      }
      try {
        if (!cancelled) {
          setLoading(true);
        }
        await loadState(globalSelectedDirectoryPath);
        if (!cancelled) {
          setError(null);
          setStatusMessage(null);
        }
      } catch (reason) {
        if (!cancelled) {
          setError(reason instanceof Error ? reason.message : String(reason));
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
        }
      }
    }
    void refresh();
    return () => {
      cancelled = true;
    };
  }, [globalSelectedDirectoryPath]);

  const selectedTabDefinition = state?.tabs.find((tab) => tab.key === selectedTab);
  const snapshot = state?.tab_snapshots[selectedTab] ?? null;
  const tabDraft = drafts[selectedTab] ?? {};
  const isDirty = dirtyTabs[selectedTab] ?? false;
  const hasUnsavedTabs = hasDirtyDrafts(dirtyTabs);
  const isRoot = selectedDirectoryPath === "root";
  const mutationLocked = !privilegedUnlocked;
  const selectedTabKey = selectedTabDefinition?.key ?? selectedTab;
  const selectedTabHasGlobalFields = Boolean(
    selectedTabDefinition?.sections.some((section) => section.fields.some(isGlobalField)),
  );
  const saveBlockedByConfigMode = !state?.directory_scoped_config_enabled && !selectedTabHasGlobalFields;
  const teamsAuthBusy = teamsAuthLoading || teamsAuthSigningIn || teamsAuthCompleting || teamsAuthSigningOut;
  const teamsAuthDirectoryPath = normalizeDirectoryPath(selectedDirectoryPath);
  const activeModelPricingDraft = modelPricingDraft ?? modelPricingDraftFromCatalog(modelPricingCatalog?.catalog ?? null);
  const modelPricingPanelBodyId = "model-pricing-catalog-panel-body";
  const modelPricingSaveDisabled =
    loading ||
    modelPricingSaving ||
    mutationLocked ||
    !modelPricingDirty ||
    !modelPricingCatalog?.editable ||
    !modelPricingDraftBaseRevision;

  async function reloadCurrentDirectory(options?: { preserveDirty?: boolean }) {
    try {
      setLoading(true);
      await loadState(selectedDirectoryPath, options);
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setLoading(false);
    }
  }

  async function handleTeamsAuthLogin() {
    const directoryPath = normalizeDirectoryPath(selectedDirectoryPath);
    setTeamsAuthSigningIn(true);
    setTeamsAuthError(null);
    setTeamsAuthNotice(null);
    try {
      const payload = await fetchJson<Record<string, unknown>>(
        `/api/teams-auth/login?directory_path=${encodeURIComponent(directoryPath)}`,
        {
          method: "POST",
          body: JSON.stringify({}),
        },
      );
      const nextSession = normalizeTeamsAuthLoginSession(payload);
      if (!nextSession) {
        throw new Error("Teams login did not return a device-code session.");
      }
      setTeamsAuthLoginSession(nextSession);
      setTeamsAuthNotice({
        kind: "info",
        message: "Device-code login started. Open the verification page and enter the code shown below.",
      });
    } catch (reason) {
      setTeamsAuthError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setTeamsAuthSigningIn(false);
    }
  }

  async function handleTeamsAuthComplete() {
    if (!teamsAuthLoginSession) {
      setTeamsAuthError("Start a device-code login before checking its status.");
      return;
    }
    const directoryPath = normalizeDirectoryPath(selectedDirectoryPath);
    const timeoutValue = Number.parseInt(teamsAuthPollTimeoutSeconds, 10);
    const timeoutSeconds = Number.isFinite(timeoutValue) && timeoutValue > 0 ? Math.min(timeoutValue, 30) : undefined;
    setTeamsAuthCompleting(true);
    setTeamsAuthError(null);
    try {
      const payload = await fetchJson<Record<string, unknown>>(
        `/api/teams-auth/complete?directory_path=${encodeURIComponent(directoryPath)}`,
        {
          method: "POST",
          body: JSON.stringify({
            login_session_id: teamsAuthLoginSession.loginSessionId,
            ...(timeoutSeconds ? { timeout_seconds: timeoutSeconds } : {}),
          }),
        },
      );
      const nextStatus = normalizeTeamsAuthStatus(payload);
      if (nextStatus && isTeamsAuthPendingState(nextStatus.state)) {
        setTeamsAuthStatus(nextStatus);
        setTeamsAuthNotice({
          kind: "warning",
          message: nextStatus.message ?? "Teams sign-in is still pending. Try again in a few seconds.",
        });
        return;
      }
      setTeamsAuthStatus(nextStatus);
      setTeamsAuthLoginSession(null);
      setTeamsAuthNotice({
        kind: "success",
        message: nextStatus?.message ?? "Teams sign-in completed.",
      });
      await refreshTeamsAuthStatus(directoryPath);
    } catch (reason) {
      setTeamsAuthError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setTeamsAuthCompleting(false);
    }
  }

  async function handleTeamsAuthLogout() {
    const directoryPath = normalizeDirectoryPath(selectedDirectoryPath);
    setTeamsAuthSigningOut(true);
    setTeamsAuthError(null);
    try {
      const payload = await fetchJson<Record<string, unknown>>(
        `/api/teams-auth/logout?directory_path=${encodeURIComponent(directoryPath)}`,
        {
          method: "POST",
          body: JSON.stringify({}),
        },
      );
      setTeamsAuthStatus(normalizeTeamsAuthStatus(payload));
      setTeamsAuthLoginSession(null);
      setTeamsAuthNotice({
        kind: "success",
        message: `Teams auth signed out for ${directoryPath}.`,
      });
      await refreshTeamsAuthStatus(directoryPath);
    } catch (reason) {
      setTeamsAuthError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setTeamsAuthSigningOut(false);
    }
  }

  async function refreshTeamsAuthStatus(directoryPath: string) {
    const normalizedDirectoryPath = normalizeDirectoryPath(directoryPath);
    setTeamsAuthLoading(true);
    try {
      const payload = await fetchJson<Record<string, unknown>>(
        `/api/teams-auth/status?directory_path=${encodeURIComponent(normalizedDirectoryPath)}`,
      );
      const nextStatus = normalizeTeamsAuthStatus(payload);
      setTeamsAuthStatus(nextStatus);
      setTeamsAuthError(null);
      return nextStatus;
    } catch (reason) {
      setTeamsAuthError(reason instanceof Error ? reason.message : String(reason));
      return null;
    } finally {
      setTeamsAuthLoading(false);
    }
  }

  async function handleTeamsChannelLookup() {
    const directoryPath = normalizeDirectoryPath(selectedDirectoryPath);
    const teamId = teamsChannelLookupTeamId.trim();
    const channelName = teamsChannelLookupIncludeAll ? teamsChannelLookupChannelName.trim() || null : teamsChannelLookupChannelName.trim();
    if (!teamId) {
      setTeamsChannelLookupError("Team id is required.");
      setTeamsChannelLookupResult(null);
      return;
    }
    if (!teamsChannelLookupIncludeAll && !channelName) {
      setTeamsChannelLookupError("Channel name is required unless all channels are requested.");
      setTeamsChannelLookupResult(null);
      return;
    }
    setTeamsChannelLookupLoading(true);
    setTeamsChannelLookupError(null);
    try {
      const query = new URLSearchParams({
        directory_path: directoryPath,
        team_id: teamId,
        include_all: String(teamsChannelLookupIncludeAll),
      });
      if (channelName) {
        query.set("channel_name", channelName);
      }
      const payload = await fetchJson<Record<string, unknown>>(`/api/teams-auth/channels?${query.toString()}`);
      const nextResult = normalizeTeamsChannelLookupResult(payload);
      if (!nextResult) {
        throw new Error("Teams channel lookup returned an unexpected payload.");
      }
      setTeamsChannelLookupResult(nextResult);
    } catch (reason) {
      setTeamsChannelLookupResult(null);
      setTeamsChannelLookupError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setTeamsChannelLookupLoading(false);
    }
  }

  function insertTeamsChannelLookupResult(
    field: ConfigurationField,
    fieldState: DraftFieldState,
    channel: TeamsChannelLookupResultView,
  ) {
    const allowlistEntry = channel.allowlistEntry?.trim();
    if (!allowlistEntry) {
      setTeamsChannelLookupError("The selected channel does not include an allowlist entry.");
      return;
    }
    const updated = mutateCommunicationTargetsField(field, fieldState, (current) => {
      const reservedIds = new Set(current.map((entry) => entry.id.trim().toLowerCase()).filter(Boolean));
      const channelId = channel.displayName.trim() || channel.channelId?.trim() || "channel";
      return [
        ...current,
        {
          id: reserveGeneratedCommunicationTargetId(channelId, reservedIds),
          disabled: false,
          provider: "teams",
          kind: "teams",
          address: allowlistEntry,
          capabilities: ["notify"],
          authProfileRef: "",
        },
      ];
    });
    if (!updated) {
      return;
    }
    setTeamsChannelLookupError(null);
    setStatusMessage(
      `Inserted ${channel.displayName.trim() || channel.channelId?.trim() || "Teams channel"} into the People & Communications draft.`,
    );
    setError(null);
  }

  useEffect(() => {
    async function refresh() {
      if (selectedTab !== TEAMS_AUTH_CONFIGURATION_TAB_KEY) {
        return;
      }
      const directoryPath = normalizeDirectoryPath(selectedDirectoryPath);
      const previousDirectoryPath = teamsAuthDirectoryPathRef.current;
      teamsAuthDirectoryPathRef.current = directoryPath;
      if (previousDirectoryPath !== directoryPath) {
        setTeamsAuthStatus(null);
        setTeamsAuthLoginSession(null);
        setTeamsAuthNotice(null);
      }
      setTeamsAuthError(null);
      await refreshTeamsAuthStatus(directoryPath);
    }

    void refresh();
    return () => {};
  }, [selectedDirectoryPath, selectedTab]);

  useEffect(() => {
    setTeamsChannelLookupResult(null);
    setTeamsChannelLookupError(null);
  }, [selectedDirectoryPath]);

  async function handleSaveTab() {
    if (!selectedTabDefinition || !snapshot) return;
    const currentDraft = drafts[selectedTab] ?? {};
    const payload: Record<string, unknown> = {};
    const removedFields: string[] = [];
    try {
      for (const section of selectedTabDefinition.sections) {
        for (const field of section.fields) {
          const fieldState = currentDraft[field.field_name];
          if (!fieldState) continue;
          const hadExplicitValue = Object.prototype.hasOwnProperty.call(snapshot.raw.config, field.field_name);
          if (isGlobalField(field)) {
            payload[field.field_name] = parseFieldValue(field, fieldState.value);
            continue;
          }
          if (!state?.directory_scoped_config_enabled) {
            continue;
          }
          if (isRoot) {
            if (fieldState.clear) {
              if (hadExplicitValue) {
                removedFields.push(field.field_name);
              }
              continue;
            }
            payload[field.field_name] = parseFieldValue(field, fieldState.value);
            continue;
          }
          if (!fieldState.include) {
            if (hadExplicitValue) {
              removedFields.push(field.field_name);
            }
            continue;
          }
          if (fieldState.clear) {
            payload[field.field_name] = field.kind === "string_list" ? [] : null;
            continue;
          }
          payload[field.field_name] = parseFieldValue(field, fieldState.value);
        }
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
      return;
    }

    setSaving(true);
    setStatusMessage(null);
    try {
      const payloadResponse = await fetchJson<{
        write_result: AdminResult<unknown>;
        tab_snapshot: TabSnapshot | null;
      }>(`/api/configuration/domains/${encodeURIComponent(selectedTab)}?directory_path=${encodeURIComponent(selectedDirectoryPath)}`, {
        method: "PUT",
        body: JSON.stringify({ config: payload, removed_fields: removedFields, expected_revision: snapshot.revision }),
      });
      if (!payloadResponse.write_result.ok || !payloadResponse.tab_snapshot) {
        throw new Error(payloadResponse.write_result.errors[0]?.message ?? "Save failed.");
      }
      setState((current) =>
        current
          ? {
              ...current,
              tab_snapshots: {
                ...current.tab_snapshots,
                [selectedTab]: payloadResponse.tab_snapshot as TabSnapshot,
              },
            }
          : current,
      );
      setDrafts((current) => ({
        ...current,
        [selectedTab]: buildTabDraft(selectedTabDefinition, payloadResponse.tab_snapshot ?? undefined, selectedDirectoryPath),
      }));
      setDirtyTabs((current) => ({ ...current, [selectedTab]: false }));
      setStatusMessage(
        selectedTabHasGlobalFields
          ? `Saved ${selectedTabDefinition.title} settings.`
          : `Saved ${selectedTabDefinition.title} overrides for ${selectedDirectoryPath}.`,
      );
      setError(null);
      if (selectedTabHasGlobalFields) {
        await refreshAdminState();
      }
      await reloadCurrentDirectory();
    } catch (reason) {
      setError(
        reason instanceof ApiError && reason.status === 409
          ? "This configuration tab changed on the server. Reload this tab before saving again; your draft is still visible."
          : reason instanceof Error
            ? reason.message
            : String(reason),
      );
    } finally {
      setSaving(false);
    }
  }

  async function copyDirectoryOverrides() {
    const payload = state?.directory_overrides.payload ?? {
      directory_path: selectedDirectoryPath,
      overrides: {},
    };
    try {
      if (!navigator.clipboard?.writeText) {
        throw new Error("Clipboard API is not available in this browser.");
      }
      await navigator.clipboard.writeText(formatJson(payload));
      setStatusMessage(`Copied unified override JSON for ${selectedDirectoryPath}.`);
      setError(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  function updateFieldState(fieldName: string, next: Partial<DraftFieldState>) {
    setDrafts((current) => ({
      ...current,
      [selectedTab]: {
        ...(current[selectedTab] ?? {}),
        [fieldName]: {
          ...(current[selectedTab]?.[fieldName] ?? { include: false, clear: false, value: "" }),
          ...next,
        },
      },
    }));
    setDirtyTabs((current) => ({ ...current, [selectedTab]: true }));
  }

  function updateContactsField(fieldName: string, nextEntries: ContactDraftEntry[]) {
    updateFieldState(fieldName, {
      include: true,
      clear: false,
      value: contactsDraftValue(nextEntries),
    });
  }

  function updateCommunicationTargetsField(fieldName: string, nextEntries: CommunicationTargetDraftEntry[]) {
    updateFieldState(fieldName, {
      include: true,
      clear: false,
      value: communicationTargetsDraftValue(nextEntries),
    });
  }

  function mutateCommunicationTargetsField(
    field: ConfigurationField,
    fieldState: DraftFieldState,
    mutate: (entries: CommunicationTargetDraftEntry[]) => CommunicationTargetDraftEntry[],
  ): boolean {
    const parsed = parseCommunicationTargetDraftValue(fieldState.value);
    if (parsed.error) {
      setError(`Unable to edit communication targets: ${parsed.error}`);
      return false;
    }
    updateCommunicationTargetsField(field.field_name, mutate(parsed.entries));
    setError(null);
    return true;
  }

  function updateCommunicationTargetEntry(
    field: ConfigurationField,
    fieldState: DraftFieldState,
    targetIndex: number,
    nextTarget: CommunicationTargetDraftEntry,
  ) {
    mutateCommunicationTargetsField(field, fieldState, (entries) =>
      entries.map((entry, index) => (index === targetIndex ? nextTarget : entry)),
    );
  }

  function updateStringListField(fieldName: string, nextEntries: string[]) {
    updateFieldState(fieldName, {
      include: true,
      clear: false,
      value: encodeStringListDraftValue(dedupeStrings(nextEntries)),
    });
  }

  function mutateContactsField(
    field: ConfigurationField,
    fieldState: DraftFieldState,
    mutate: (entries: ContactDraftEntry[]) => ContactDraftEntry[],
  ) {
    const parsed = parseContactDraftValue(fieldState.value);
    if (parsed.error) {
      setError(`Unable to edit contacts: ${parsed.error}`);
      return;
    }
    updateContactsField(field.field_name, mutate(parsed.entries));
    setError(null);
  }

  function updateContactEntry(
    field: ConfigurationField,
    fieldState: DraftFieldState,
    contactIndex: number,
    nextContact: ContactDraftEntry,
  ) {
    mutateContactsField(field, fieldState, (entries) =>
      entries.map((entry, index) => (index === contactIndex ? nextContact : entry)),
    );
  }

  function renderContactsEditor(field: ConfigurationField, fieldState: DraftFieldState, disabled: boolean) {
    const parsed = parseContactDraftValue(fieldState.value);
    const entries = parsed.entries;
    const resolvedContactIds = contactDraftResolvedIds(entries);
    return (
      <div className="contacts-editor">
        <div className="contacts-editor-header">
          <p className="muted-text">User attachments are managed from the Users page.</p>
          <div className="inline-actions contacts-editor-actions">
            <button
              type="button"
              onClick={() =>
                mutateContactsField(field, fieldState, (current) => [
                  ...current,
                  {
                    id: "",
                    isNew: true,
                    name: "",
                    description: "",
                    emailAddresses: [],
                    phoneNumbers: [],
                  },
                ])
              }
              disabled={disabled}
            >
              Add contact
            </button>
          </div>
        </div>
        {parsed.error ? (
          <div className="callout error-callout">Contacts draft could not be read: {parsed.error}</div>
        ) : null}
        {!parsed.error && entries.length === 0 ? (
          <div className="empty-state">No contacts are configured for this override.</div>
        ) : null}
        {!parsed.error
          ? entries.map((contact, contactIndex) => (
              <div key={`contact-${contactIndex}`} className="contact-editor-item">
                <div className="contact-editor-item-header">
                  <strong>{contact.name.trim() || contact.id.trim() || `Contact ${contactIndex + 1}`}</strong>
                  <div className="inline-actions compact-actions">
                    <button
                      type="button"
                      className="danger"
                      onClick={() =>
                        mutateContactsField(field, fieldState, (current) =>
                          current.filter((_, index) => index !== contactIndex),
                        )
                      }
                      disabled={disabled}
                    >
                      Remove
                    </button>
                  </div>
                </div>
                <div className="contact-editor-grid">
                  <div className="contact-id-field">
                    <span>Contact id</span>
                    <strong>{resolvedContactIds[contactIndex] || "contact"}</strong>
                  </div>
                  <label className="field-stack">
                    <span>Name</span>
                    <input
                      value={contact.name}
                      onChange={(event) =>
                        updateContactEntry(field, fieldState, contactIndex, {
                          ...contact,
                          name: event.target.value,
                        })
                      }
                      disabled={disabled}
                      placeholder="Ops Duty"
                    />
                  </label>
                  <label className="field-stack contact-description-field">
                    <span>Description</span>
                    <textarea
                      value={contact.description}
                      onChange={(event) =>
                        updateContactEntry(field, fieldState, contactIndex, {
                          ...contact,
                          description: event.target.value,
                        })
                      }
                      disabled={disabled}
                      placeholder="Optional"
                    />
                  </label>
                </div>

                <div className="contact-subsection">
                  <div className="contact-subsection-header">
                    <strong>Email addresses</strong>
                    <div className="inline-actions compact-actions">
                      <button
                        type="button"
                        onClick={() =>
                          updateContactEntry(field, fieldState, contactIndex, {
                            ...contact,
                            emailAddresses: [...contact.emailAddresses, ""],
                          })
                        }
                        disabled={disabled}
                      >
                        Add email
                      </button>
                    </div>
                  </div>
                  {contact.emailAddresses.length === 0 ? (
                    <div className="empty-state compact-empty-state">No email addresses.</div>
                  ) : null}
                  {contact.emailAddresses.map((address, emailIndex) => (
                    <div key={`contact-${contactIndex}-email-${emailIndex}`} className="contact-row">
                      <label className="field-stack">
                        <span>Email</span>
                        <input
                          type="email"
                          value={address}
                          onChange={(event) => {
                            const nextEmails = contact.emailAddresses.map((current, index) =>
                              index === emailIndex ? event.target.value : current,
                            );
                            updateContactEntry(field, fieldState, contactIndex, {
                              ...contact,
                              emailAddresses: nextEmails,
                            });
                          }}
                          disabled={disabled}
                          placeholder="ops@example.com"
                        />
                      </label>
                      <div className="inline-actions compact-actions">
                        <button
                          type="button"
                          className="danger"
                          onClick={() =>
                            updateContactEntry(field, fieldState, contactIndex, {
                              ...contact,
                              emailAddresses: contact.emailAddresses.filter(
                                (_, index) => index !== emailIndex,
                              ),
                            })
                          }
                          disabled={disabled}
                        >
                          Remove
                        </button>
                      </div>
                    </div>
                  ))}
                </div>

                <div className="contact-subsection">
                  <div className="contact-subsection-header">
                    <strong>Phone numbers</strong>
                    <div className="inline-actions compact-actions">
                      <button
                        type="button"
                        onClick={() =>
                          updateContactEntry(field, fieldState, contactIndex, {
                            ...contact,
                            phoneNumbers: [
                              ...contact.phoneNumbers,
                              {
                                label: "",
                                number: "",
                                primary: contact.phoneNumbers.length === 0,
                              },
                            ],
                          })
                        }
                        disabled={disabled}
                      >
                        Add phone
                      </button>
                    </div>
                  </div>
                  {contact.phoneNumbers.length === 0 ? (
                    <div className="empty-state compact-empty-state">No phone numbers.</div>
                  ) : null}
                  {contact.phoneNumbers.map((phone, phoneIndex) => (
                    <div key={`contact-${contactIndex}-phone-${phoneIndex}`} className="contact-row contact-phone-row">
                      <label className="field-stack">
                        <span>Label</span>
                        <input
                          value={phone.label}
                          onChange={(event) => {
                            const nextPhones = contact.phoneNumbers.map((current, index) =>
                              index === phoneIndex ? { ...current, label: event.target.value } : current,
                            );
                            updateContactEntry(field, fieldState, contactIndex, {
                              ...contact,
                              phoneNumbers: nextPhones,
                            });
                          }}
                          disabled={disabled}
                          placeholder="mobile"
                        />
                      </label>
                      <label className="field-stack">
                        <span>Number</span>
                        <input
                          value={phone.number}
                          onChange={(event) => {
                            const nextPhones = contact.phoneNumbers.map((current, index) =>
                              index === phoneIndex ? { ...current, number: event.target.value } : current,
                            );
                            updateContactEntry(field, fieldState, contactIndex, {
                              ...contact,
                              phoneNumbers: nextPhones,
                            });
                          }}
                          disabled={disabled}
                          placeholder="+15551230001"
                        />
                      </label>
                      <label className="checkbox-row contact-primary-row">
                        <input
                          type="radio"
                          checked={phone.primary}
                          onChange={() => {
                            const nextPhones = contact.phoneNumbers.map((current, index) => ({
                              ...current,
                              primary: index === phoneIndex,
                            }));
                            updateContactEntry(field, fieldState, contactIndex, {
                              ...contact,
                              phoneNumbers: nextPhones,
                            });
                          }}
                          disabled={disabled}
                        />
                        <span>Primary</span>
                      </label>
                      <div className="inline-actions compact-actions">
                        <button
                          type="button"
                          className="danger"
                          onClick={() =>
                            updateContactEntry(field, fieldState, contactIndex, {
                              ...contact,
                              phoneNumbers: contact.phoneNumbers.filter(
                                (_, index) => index !== phoneIndex,
                              ),
                            })
                          }
                          disabled={disabled}
                        >
                          Remove
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            ))
          : null}
      </div>
    );
  }

  function renderCommunicationTargetsEditor(
    field: ConfigurationField,
    fieldState: DraftFieldState,
    disabled: boolean,
    inheritedTargets: CommunicationTargetDraftEntry[],
  ) {
    const parsed = parseCommunicationTargetDraftValue(fieldState.value);
    const entries = parsed.entries;
    const explicitIds = new Set(entries.map((entry) => entry.id.trim().toLowerCase()).filter(Boolean));
    const availableInheritedTargets = inheritedTargets.filter((target) => !explicitIds.has(target.id.trim().toLowerCase()));
    const teamsChannelLookupDisabled = loading || saving || teamsChannelLookupLoading || !state?.directory_scoped_config_enabled;
    const teamsChannelInsertDisabled = disabled || mutationLocked || teamsChannelLookupLoading || Boolean(parsed.error);
    return (
      <div className="communication-targets-editor">
        <div className="contacts-editor-header">
          <p className="muted-text">
            Targets are resolved through directory inheritance and used by workflow messaging effects.
            Use Add target for Teams, Discord, email, GitHub, ADO, Jira, or Linear targets; the Teams
            lookup only helps fill channel addresses.
          </p>
          <div className="inline-actions contacts-editor-actions">
            <button
              type="button"
              onClick={() =>
                mutateCommunicationTargetsField(field, fieldState, (current) => [
                  ...current,
                  {
                    id: "",
                    disabled: false,
                    provider: "teams",
                    kind: "teams",
                    address: "",
                    capabilities: ["notify"],
                    authProfileRef: "",
                  },
                ])
              }
              disabled={disabled || mutationLocked}
            >
              Add target
            </button>
          </div>
        </div>
        <div className="contact-subsection teams-channel-lookup">
          <div className="contact-subsection-header">
            <strong>Optional Teams channel lookup</strong>
            <div className="inline-actions compact-actions">
              <button
                type="button"
                onClick={() => void handleTeamsChannelLookup()}
                disabled={teamsChannelLookupDisabled}
              >
                {teamsChannelLookupLoading ? "Looking up..." : "Lookup channels"}
              </button>
            </div>
          </div>
          <div className="teams-channel-lookup-grid">
            <label className="field-stack">
              <span>Team id</span>
              <input
                value={teamsChannelLookupTeamId}
                onChange={(event) => setTeamsChannelLookupTeamId(event.target.value)}
                disabled={teamsChannelLookupDisabled}
                placeholder="c85e4de5-e0d7-4240-8302-8bc944f2360d"
              />
            </label>
            <label className="field-stack">
              <span>Channel name</span>
              <input
                value={teamsChannelLookupChannelName}
                onChange={(event) => setTeamsChannelLookupChannelName(event.target.value)}
                disabled={teamsChannelLookupDisabled}
                placeholder="General"
              />
            </label>
            <label className="checkbox-row teams-channel-lookup-all-toggle">
              <input
                type="checkbox"
                checked={teamsChannelLookupIncludeAll}
                onChange={(event) => setTeamsChannelLookupIncludeAll(event.target.checked)}
                disabled={teamsChannelLookupDisabled}
              />
              <span>Include all channels</span>
            </label>
          </div>
          {teamsChannelLookupError ? <div className="callout error-callout">{teamsChannelLookupError}</div> : null}
          {teamsChannelLookupResult ? (
            <div className="teams-channel-lookup-results">
              <div className="panel-meta">
                Directory {teamsChannelLookupResult.directoryPath} · backend {teamsChannelLookupResult.backend} ·{" "}
                {teamsChannelLookupResult.matchCount} match{teamsChannelLookupResult.matchCount === 1 ? "" : "es"}
              </div>
              {!teamsChannelLookupResult.channels.length ? (
                <div className="empty-state">
                  No matching channels were returned for team {teamsChannelLookupResult.teamId}.
                </div>
              ) : (
                teamsChannelLookupResult.channels.map((channel) => (
                  <div key={`${channel.teamId}-${channel.channelId ?? channel.displayName}`} className="teams-channel-lookup-result">
                    <div className="teams-channel-lookup-result-header">
                      <div>
                        <strong>{channel.displayName || channel.channelId || "Teams channel"}</strong>
                        <div className="panel-meta">
                          {channel.membershipType ?? "membership unknown"}
                          {channel.allowlistEntry ? ` · ${channel.allowlistEntry}` : ""}
                        </div>
                      </div>
                      <div className="inline-actions compact-actions teams-channel-lookup-result-actions">
                        {channel.webUrl ? (
                          <a href={channel.webUrl} target="_blank" rel="noreferrer">
                            Open
                          </a>
                        ) : null}
                        <button
                          type="button"
                          onClick={() => insertTeamsChannelLookupResult(field, fieldState, channel)}
                          disabled={teamsChannelInsertDisabled || !channel.allowlistEntry}
                        >
                          Insert target
                        </button>
                      </div>
                    </div>
                    <div className="teams-channel-lookup-result-fields">
                      <div>
                        <span>Channel id</span>
                        <strong>{channel.channelId ?? "n/a"}</strong>
                      </div>
                      <div>
                        <span>Allowlist entry</span>
                        <strong>{channel.allowlistEntry ?? "n/a"}</strong>
                      </div>
                    </div>
                  </div>
                ))
              )}
            </div>
          ) : null}
        </div>
        {parsed.error ? (
          <div className="callout error-callout">Communication target draft could not be read: {parsed.error}</div>
        ) : null}
        {!parsed.error && entries.length === 0 ? (
          <div className="empty-state">No communication targets are configured for this override.</div>
        ) : null}
        {!parsed.error
          ? entries.map((target, targetIndex) => {
              const kindOptions = communicationTargetKindsForProvider(target.provider);
              const showKindSelector = kindOptions.length > 1;
              const selectedKind = normalizeCommunicationTargetKind(target.provider, target.kind);
              const showAuthProfile = target.provider === "email";
              return (
              <div key={`communication-target-${targetIndex}`} className="contact-editor-item communication-target-editor-item">
                <div className="contact-editor-item-header">
                  <strong>{target.id.trim() || `Target ${targetIndex + 1}`}</strong>
                  <div className="inline-actions compact-actions">
                    <button
                      type="button"
                      className="danger"
                      onClick={() =>
                        mutateCommunicationTargetsField(field, fieldState, (current) =>
                          current.filter((_, index) => index !== targetIndex),
                        )
                      }
                      disabled={disabled}
                    >
                      Remove
                    </button>
                  </div>
                </div>
                <div className="communication-target-grid">
                  <label className="field-stack">
                    <span>Target id</span>
                    <input
                      value={target.id}
                      onChange={(event) =>
                        updateCommunicationTargetEntry(field, fieldState, targetIndex, {
                          ...target,
                          id: event.target.value,
                        })
                      }
                      disabled={disabled}
                      placeholder="target-primary"
                    />
                  </label>
                  <label className="field-stack">
                    <span>Provider</span>
                    <select
                      value={target.provider}
                      onChange={(event) => {
                        const provider = event.target.value;
                        updateCommunicationTargetEntry(field, fieldState, targetIndex, {
                          ...target,
                          provider,
                          kind: normalizeCommunicationTargetKind(provider, target.kind),
                          authProfileRef: provider === "email" ? target.authProfileRef : "",
                        });
                      }}
                      disabled={disabled || target.disabled}
                    >
                      {COMMUNICATION_TARGET_PROVIDERS.map((provider) => (
                        <option key={provider} value={provider}>{provider}</option>
                      ))}
                    </select>
                  </label>
                  {showKindSelector ? (
                    <label className="field-stack">
                      <span>Kind</span>
                      <select
                        value={selectedKind}
                        onChange={(event) =>
                          updateCommunicationTargetEntry(field, fieldState, targetIndex, {
                            ...target,
                            kind: event.target.value,
                          })
                        }
                        disabled={disabled || target.disabled}
                      >
                        {kindOptions.map((kind) => (
                          <option key={kind} value={kind}>{kind}</option>
                        ))}
                      </select>
                    </label>
                  ) : null}
                  <label className="field-stack communication-target-address-field">
                    <span>Address</span>
                    <input
                      value={target.address}
                      onChange={(event) =>
                        updateCommunicationTargetEntry(field, fieldState, targetIndex, {
                          ...target,
                          address: event.target.value,
                        })
                      }
                      disabled={disabled || target.disabled}
                      placeholder={communicationTargetAddressPlaceholder(target.provider, selectedKind)}
                    />
                  </label>
                  {showAuthProfile ? (
                    <label className="field-stack">
                      <span>Auth profile</span>
                      <input
                        value={target.authProfileRef}
                        onChange={(event) =>
                          updateCommunicationTargetEntry(field, fieldState, targetIndex, {
                            ...target,
                            authProfileRef: event.target.value,
                          })
                        }
                        disabled={disabled || target.disabled}
                        placeholder="email.default"
                      />
                    </label>
                  ) : null}
                </div>
                <div className="communication-target-capabilities">
                  {COMMUNICATION_TARGET_CAPABILITIES.map((capability) => (
                    <label key={capability} className="checkbox-row">
                      <input
                        type="checkbox"
                        checked={target.capabilities.includes(capability)}
                        onChange={(event) => {
                          const capabilities = event.target.checked
                            ? [...target.capabilities, capability]
                            : target.capabilities.filter((current) => current !== capability);
                          updateCommunicationTargetEntry(field, fieldState, targetIndex, {
                            ...target,
                            capabilities,
                          });
                        }}
                        disabled={disabled || target.disabled}
                      />
                      <span>{capability}</span>
                    </label>
                  ))}
                  <label className="checkbox-row communication-target-disabled-toggle">
                    <input
                      type="checkbox"
                      checked={target.disabled}
                      onChange={(event) =>
                        updateCommunicationTargetEntry(field, fieldState, targetIndex, {
                          ...target,
                          disabled: event.target.checked,
                        })
                      }
                      disabled={disabled}
                    />
                    <span>Disable inherited target</span>
                  </label>
                </div>
              </div>
              );
            })
          : null}
        {availableInheritedTargets.length ? (
          <div className="contact-subsection communication-target-inherited">
            <div className="contact-subsection-header">
              <strong>Inherited targets</strong>
            </div>
            {availableInheritedTargets.map((target) => (
              <div key={`inherited-${target.id}`} className="communication-target-inherited-row">
                <div>
                  <strong>{target.id}</strong>
                  <span className="panel-meta">{target.provider} / {target.kind}</span>
                </div>
                <div className="inline-actions compact-actions">
                  <button
                    type="button"
                    onClick={() =>
                      mutateCommunicationTargetsField(field, fieldState, (current) => [
                        ...current,
                        { ...target },
                      ])
                    }
                    disabled={disabled}
                  >
                    Override
                  </button>
                  <button
                    type="button"
                    className="danger"
                    onClick={() =>
                      mutateCommunicationTargetsField(field, fieldState, (current) => [
                        ...current,
                        { ...target, disabled: true },
                      ])
                    }
                    disabled={disabled}
                  >
                    Disable
                  </button>
                </div>
              </div>
            ))}
          </div>
        ) : null}
      </div>
    );
  }

  function renderApprovalNotificationTargetEditor(
    field: ConfigurationField,
    fieldState: DraftFieldState,
    disabled: boolean,
  ) {
    const entries = parseStringListDraftValue(fieldState.value);
    const knownTargets = field.options;
    const nextAvailableTarget = knownTargets.find((option) => !entries.includes(option)) ?? "";

    return (
      <div className="string-list-selector">
        <div className="contacts-editor-header">
          <p className="muted-text">
            Select notify-capable communication targets declared in the Communications tab.
          </p>
          <div className="inline-actions contacts-editor-actions">
            <button
              type="button"
              onClick={() => updateStringListField(field.field_name, [...entries, nextAvailableTarget])}
              disabled={disabled || !nextAvailableTarget}
            >
              Add communication target
            </button>
          </div>
        </div>
        {entries.length === 0 ? (
          <div className="empty-state">
            {knownTargets.length
              ? "No communication targets selected."
              : "No notify-capable communication targets are currently declared."}
          </div>
        ) : null}
        {entries.map((entry, index) => {
          const rowOptions = dedupeStrings([
            entry,
            ...knownTargets.filter((option) => option === entry || !entries.includes(option)),
          ]);
          const isUnknownTarget = !knownTargets.includes(entry);
          return (
            <div key={`${field.field_name}-${index}-${entry}`} className="string-list-selector-row">
              <label className="field-stack">
                <span>Communication target</span>
                <select
                  value={entry}
                  onChange={(event) => {
                    const nextEntries = [...entries];
                    nextEntries[index] = event.target.value;
                    updateStringListField(field.field_name, nextEntries);
                  }}
                  disabled={disabled}
                >
                  {rowOptions.map((option) => (
                    <option key={option} value={option}>
                      {option === entry && isUnknownTarget ? `${option} (unknown target)` : option}
                    </option>
                  ))}
                </select>
              </label>
              <div className="inline-actions compact-actions">
                <button
                  type="button"
                  className="danger"
                  onClick={() =>
                    updateStringListField(
                      field.field_name,
                      entries.filter((_, itemIndex) => itemIndex !== index),
                    )
                  }
                  disabled={disabled}
                >
                  Remove
                </button>
              </div>
            </div>
          );
        })}
      </div>
    );
  }

  function sectionStorageKey(sectionTitle: string) {
    return `${selectedTabKey}:${sectionTitle}`;
  }

  function isSectionCollapsed(sectionTitle: string) {
    return collapsedSections[sectionStorageKey(sectionTitle)] ?? true;
  }

  function toggleSection(sectionTitle: string) {
    const key = sectionStorageKey(sectionTitle);
    setCollapsedSections((current) => ({ ...current, [key]: !(current[key] ?? false) }));
  }

  function renderFieldInput(field: ConfigurationField, fieldState: DraftFieldState) {
    const disabled = loading || saving || (!isGlobalField(field) && !state?.directory_scoped_config_enabled);
    if (field.kind === "bool") {
      return (
        <select
          value={String(Boolean(fieldState.value))}
          onChange={(event) => updateFieldState(field.field_name, { value: event.target.value === "true" })}
          disabled={disabled}
        >
          <option value="false">false</option>
          <option value="true">true</option>
        </select>
      );
    }
    if (field.kind === "literal") {
      return (
        <select
          value={String(fieldState.value)}
          onChange={(event) => updateFieldState(field.field_name, { value: event.target.value })}
          disabled={disabled}
        >
          {field.options.map((option) => (
            <option key={option} value={option}>
              {option || "default"}
            </option>
          ))}
        </select>
      );
    }
    if (field.kind === "string_list") {
      return (
        <StringListEditor
          values={parseStringListDraftValue(fieldState.value)}
          onChange={(nextValues) =>
            updateFieldState(field.field_name, {
              value: encodeStringListDraftValue(nextValues),
            })
          }
          disabled={disabled}
          itemLabel="Value"
          suggestions={field.options}
          placeholder="Enter a value"
        />
      );
    }
    if (field.kind === "json") {
      return (
        <JsonTextEditor
          value={String(fieldState.value)}
          onChange={(nextValue) => updateFieldState(field.field_name, { value: nextValue })}
          fieldLabel={field.label}
          disabled={disabled}
        />
      );
    }
    if (field.kind === "multiline_text") {
      return (
        <textarea
          className="json-editor compact-editor"
          value={String(fieldState.value)}
          spellCheck={false}
          onChange={(event) => updateFieldState(field.field_name, { value: event.target.value })}
          disabled={disabled}
        />
      );
    }
    return (
      <input
        type={field.kind === "int" || field.kind === "float" ? "number" : "text"}
        value={String(fieldState.value)}
        onChange={(event) => updateFieldState(field.field_name, { value: event.target.value })}
        disabled={disabled}
      />
    );
  }

  return (
    <div className="feature-page configuration-page">
      <section className="feature-sidebar">
        <div className="feature-sidebar-header">
          <div>
            <div className="eyebrow">Directory-scoped configuration</div>
            <h1>Configuration</h1>
          </div>
        </div>
        {mutationLocked ? (
          <div className="callout warning-callout">Unlock privileged operations to save configuration overrides.</div>
        ) : null}

        <div className="feature-list configuration-tab-list">
          {(state?.tabs ?? []).map((tab) => (
            <button
              key={tab.key}
              type="button"
              className={`feature-list-item configuration-tab-item${tab.key === selectedTab ? " selected" : ""}`}
              onClick={() => setSelectedTab(tab.key)}
              disabled={loading || saving}
            >
              <div className="feature-list-title-row">
                <strong>{tab.title}</strong>
                {dirtyTabs[tab.key] ? <span className="panel-meta">Unsaved</span> : null}
              </div>
              <div className="feature-list-meta">{tab.help_text ?? "Configuration controls for this domain."}</div>
            </button>
          ))}
          {!state?.tabs.length ? <div className="empty-state">No configuration tabs are available for this directory.</div> : null}
        </div>
      </section>

      <section className="feature-content">
        <div className="feature-toolbar">
          <div>
            <h2>{selectedTabDefinition?.title ?? "Configuration"}</h2>
            <p className="muted-text">
              Editing {selectedDirectoryPath}. Inspect inherited defaults, adjust explicit overrides, and save when the selected tab is ready.
            </p>
          </div>
          <div className="feature-toolbar-actions icon-actions">
            <Tooltip content={selectedTabHasGlobalFields ? "Save the current tab's settings." : "Save the current tab's explicit overrides for the selected directory."}>
              <button
                type="button"
                className="primary"
                onClick={() => void handleSaveTab()}
                disabled={saving || loading || saveBlockedByConfigMode || !selectedTabDefinition || !snapshot || !isDirty || mutationLocked}
                aria-label={saving ? "Saving tab" : "Save tab"}
              >
                {saving ? "…" : <SaveIcon />}
              </button>
            </Tooltip>
          </div>
        </div>

        {!state?.directory_scoped_config_enabled ? (
          <div className="callout warning-callout">
            Directory-scoped config is disabled. You can inspect current values, but create, delete, and save actions are unavailable.
          </div>
        ) : null}
        {mutationLocked ? (
          <div className="callout warning-callout">
            Privileged operations are locked. You can inspect configuration, but save actions are unavailable.
          </div>
        ) : null}
        {statusMessage ? <div className="callout success-callout">{statusMessage}</div> : null}
        {error ? <div className="callout error-callout">{error}</div> : null}
        {hasUnsavedTabs ? (
          <div className="callout warning-callout">
            Unsaved override edits are being preserved during refresh. Switching directories will discard them.
          </div>
        ) : null}

        <div className="feature-grid configuration-summary-grid">
          {selectedTabDefinition?.key === TEAMS_AUTH_CONFIGURATION_TAB_KEY ? (
            <article className="panel detail-panel configuration-summary-panel teams-auth-panel">
              <div className="panel-header">
                <div>
                  <h3>Teams delegated auth</h3>
                  <p className="muted-text">
                    Inspect the host-side Microsoft Teams auth state, start device-code sign-in, and sign out without exposing credentials.
                  </p>
                </div>
                <span className="panel-meta">{teamsAuthDirectoryPath}</span>
              </div>
              {teamsAuthNotice ? (
                <div
                  className={`callout ${
                    teamsAuthNotice.kind === "warning"
                      ? "warning-callout"
                      : teamsAuthNotice.kind === "info"
                        ? "warning-callout"
                        : "success-callout"
                  }`}
                >
                  {teamsAuthNotice.message}
                </div>
              ) : null}
              {teamsAuthError ? <div className="callout error-callout">{teamsAuthError}</div> : null}
              {teamsAuthLoading ? <div className="callout warning-callout">Refreshing Teams auth status...</div> : null}
              <div className="detail-stack compact-detail-stack">
                <div className="detail-grid teams-auth-summary-grid">
                  <div>
                    <span>Directory</span>
                    <strong>{teamsAuthDirectoryPath}</strong>
                  </div>
                  <div>
                    <span>Backend</span>
                    <strong>{teamsAuthStatus?.backend ?? "n/a"}</strong>
                  </div>
                  <div>
                    <span>State</span>
                    <strong>{formatTeamsAuthState(teamsAuthStatus?.state ?? null)}</strong>
                  </div>
                  <div>
                    <span>Identity</span>
                    <strong>{teamsAuthStatus?.identityId ?? teamsAuthStatus?.accountIdentifier ?? "n/a"}</strong>
                  </div>
                  <div>
                    <span>Tenant</span>
                    <strong>{teamsAuthStatus?.tenantId ?? "n/a"}</strong>
                  </div>
                  <div>
                    <span>Scopes</span>
                    <strong>{teamsAuthStatus?.scopes.length ? teamsAuthStatus.scopes.join(", ") : "n/a"}</strong>
                  </div>
                  <div>
                    <span>Issued</span>
                    <strong>{formatTimestamp(teamsAuthStatus?.issuedAt)}</strong>
                  </div>
                  <div>
                    <span>Expires</span>
                    <strong>{formatTimestamp(teamsAuthStatus?.expiresAt)}</strong>
                  </div>
                </div>
              </div>
              <div className="inline-actions compact-actions teams-auth-actions">
                <button
                  type="button"
                  onClick={() => void refreshTeamsAuthStatus(teamsAuthDirectoryPath)}
                  disabled={loading || saving || teamsAuthLoading || teamsAuthSigningIn || teamsAuthCompleting || teamsAuthSigningOut}
                >
                  {teamsAuthLoading ? "Refreshing..." : "Refresh status"}
                </button>
                <button
                  type="button"
                  className="primary"
                  onClick={() => void handleTeamsAuthLogin()}
                  disabled={mutationLocked || teamsAuthLoading || teamsAuthSigningIn || teamsAuthCompleting || teamsAuthSigningOut || Boolean(teamsAuthLoginSession)}
                >
                  {teamsAuthSigningIn ? "Starting..." : "Start sign-in"}
                </button>
                <label className="field-stack teams-auth-timeout-field">
                  <span>Poll timeout</span>
                  <input
                    type="number"
                    min="1"
                    max="30"
                    value={teamsAuthPollTimeoutSeconds}
                    onChange={(event) => setTeamsAuthPollTimeoutSeconds(event.target.value)}
                    disabled={mutationLocked || teamsAuthLoading || teamsAuthSigningIn || teamsAuthCompleting || teamsAuthSigningOut}
                  />
                </label>
                <button
                  type="button"
                  onClick={() => void handleTeamsAuthComplete()}
                  disabled={mutationLocked || teamsAuthLoading || teamsAuthCompleting || teamsAuthSigningIn || teamsAuthSigningOut || !teamsAuthLoginSession}
                >
                  {teamsAuthCompleting ? "Checking..." : "Check sign-in status"}
                </button>
                <button
                  type="button"
                  className="danger"
                  onClick={() => void handleTeamsAuthLogout()}
                  disabled={mutationLocked || teamsAuthLoading || teamsAuthSigningIn || teamsAuthCompleting || teamsAuthSigningOut || (!teamsAuthStatus && !teamsAuthLoginSession)}
                >
                  {teamsAuthSigningOut ? "Signing out..." : "Sign out"}
                </button>
              </div>
              {teamsAuthLoginSession ? (
                <div className="teams-auth-session-card">
                  <div className="panel-header">
                    <div>
                      <h4>Device-code login</h4>
                      <p className="muted-text">
                        Use the Microsoft verification page from another browser or device, then check sign-in status here.
                      </p>
                    </div>
                    <span className="panel-meta">{teamsAuthLoginSession.loginSessionId}</span>
                  </div>
                  {teamsAuthLoginSession.message ? <div className="callout warning-callout">{teamsAuthLoginSession.message}</div> : null}
                  <div className="detail-grid teams-auth-session-grid">
                    <div>
                      <span>Directory</span>
                      <strong>{teamsAuthLoginSession.directoryPath}</strong>
                    </div>
                    <div>
                      <span>Identity</span>
                      <strong>{teamsAuthLoginSession.identityId ?? "n/a"}</strong>
                    </div>
                    <div>
                      <span>Tenant</span>
                      <strong>{teamsAuthLoginSession.tenantId ?? "n/a"}</strong>
                    </div>
                    <div>
                      <span>Scopes</span>
                      <strong>{teamsAuthLoginSession.scopes.length ? teamsAuthLoginSession.scopes.join(", ") : "n/a"}</strong>
                    </div>
                    <div>
                      <span>Created</span>
                      <strong>{formatTimestamp(teamsAuthLoginSession.createdAt)}</strong>
                    </div>
                    <div>
                      <span>Expires</span>
                      <strong>{formatTimestamp(teamsAuthLoginSession.expiresAt)}</strong>
                    </div>
                    <div>
                      <span>Poll interval</span>
                      <strong>{teamsAuthLoginSession.intervalSeconds == null ? "n/a" : formatRelativeSeconds(teamsAuthLoginSession.intervalSeconds)}</strong>
                    </div>
                  </div>
                  <div className="teams-auth-code-line">
                    <span>User code</span>
                    <code>{teamsAuthLoginSession.userCode ?? "n/a"}</code>
                  </div>
                  <div className="teams-auth-link-list">
                    {teamsAuthLoginSession.verificationUri ? (
                      <div>
                        <span>Verification URI</span>
                        <a
                          className="teams-auth-uri"
                          href={teamsAuthLoginSession.verificationUri}
                          target="_blank"
                          rel="noreferrer"
                        >
                          {teamsAuthLoginSession.verificationUri}
                        </a>
                      </div>
                    ) : null}
                    {teamsAuthLoginSession.verificationUriComplete ? (
                      <div>
                        <span>Verification URI complete</span>
                        <a
                          className="teams-auth-uri"
                          href={teamsAuthLoginSession.verificationUriComplete}
                          target="_blank"
                          rel="noreferrer"
                        >
                          {teamsAuthLoginSession.verificationUriComplete}
                        </a>
                      </div>
                    ) : null}
                  </div>
                </div>
              ) : (
                <div className="empty-state">No active device-code login is in progress.</div>
              )}
            </article>
          ) : null}
          <article className="panel detail-panel configuration-summary-panel">
            <div className="panel-header">
              <div>
                <h3>Directory summary</h3>
                <p className="muted-text">
                  Active location, inheritance boundaries, and save scope for the selected configuration tab.
                </p>
              </div>
              <span className="panel-meta">{selectedTabDefinition?.title ?? "No tab selected"}</span>
            </div>
            <div className="detail-stack compact-detail-stack">
              <div className="detail-grid">
                <div><span>Directory</span><strong>{selectedDirectoryPath}</strong></div>
                <div><span>Parent</span><strong>{state?.selected_directory.parent_path ?? "root has no parent"}</strong></div>
                <div><span>Scope</span><strong>{isRoot ? "Root defaults" : "Directory override"}</strong></div>
                <div><span>Config mode</span><strong>{state?.directory_scoped_config_enabled ? "Writable" : "Read only"}</strong></div>
                <div><span>Selected tab</span><strong>{selectedTabDefinition?.title ?? "None"}</strong></div>
                <div><span>Underlying domains</span><strong>{selectedTabDefinition?.underlying_domains.join(", ") || "n/a"}</strong></div>
                <div><span>Override domains</span><strong>{state?.directory_overrides.domain_count ?? 0}</strong></div>
                <div><span>Override fields</span><strong>{state?.directory_overrides.field_count ?? 0}</strong></div>
              </div>
            </div>
          </article>
          {selectedTabDefinition?.key === MODEL_PRICING_CONFIGURATION_TAB_KEY ? (
            <article className="panel detail-panel configuration-summary-panel model-pricing-panel">
              <button
                type="button"
                className="panel-header panel-toggle model-pricing-panel-toggle"
                onClick={() => setModelPricingPanelCollapsed((current) => !current)}
                aria-expanded={!modelPricingPanelCollapsed}
                aria-controls={modelPricingPanelBodyId}
              >
                <div>
                  <h3>Model pricing catalog</h3>
                  <p className="muted-text">
                    Host-global OpenAI pricing rules used for query-time model usage cost estimates.
                  </p>
                </div>
                <div className="model-pricing-panel-toggle-meta">
                  <span className="panel-meta">{modelPricingCatalog?.status ?? "loading"}</span>
                  {modelPricingDirty && modelPricingPanelCollapsed ? (
                    <span className="status-pill state-stale">Unsaved changes</span>
                  ) : null}
                  <span className="panel-toggle-indicator">{modelPricingPanelCollapsed ? "Show" : "Hide"}</span>
                </div>
              </button>
              {!modelPricingPanelCollapsed ? (
                <div id={modelPricingPanelBodyId} className="detail-stack compact-detail-stack">
                <div className="inline-actions compact-actions">
                  <button
                    type="button"
                    onClick={() => void loadModelPricingCatalog()}
                    disabled={loading || modelPricingSaving}
                  >
                    Refresh
                  </button>
                  <button
                    type="button"
                    onClick={discardModelPricingChanges}
                    disabled={modelPricingSaving || !modelPricingDirty}
                  >
                    Discard
                  </button>
                  <button
                    type="button"
                    className="primary"
                    onClick={() => void saveModelPricingCatalog()}
                    disabled={modelPricingSaveDisabled}
                  >
                    {modelPricingSaving ? "Saving..." : "Save pricing"}
                  </button>
                </div>
                {!modelPricingCatalog ? (
                  <div className="callout warning-callout">Loading model pricing catalog...</div>
                ) : null}
                {mutationLocked ? (
                  <div className="callout warning-callout">
                    Privileged operations are locked. Unlock privileged operations to save pricing changes.
                  </div>
                ) : null}
                {modelPricingCatalog?.status === "error" ? (
                  <div className="callout error-callout">
                    {modelPricingCatalog.error ?? "Model pricing catalog could not be loaded."}
                  </div>
                ) : null}
                {modelPricingError ? <div className="callout error-callout">{modelPricingError}</div> : null}
                {modelPricingCatalog ? (
                  <>
                <div className="detail-grid">
                  <div><span>Revision</span><strong>{modelPricingCatalog.revision?.slice(0, 12) ?? "n/a"}</strong></div>
                  <div><span>Path source</span><strong>{pricingPathSourceLabel(modelPricingCatalog.path_source)}</strong></div>
                  <div><span>Override env</span><strong>{modelPricingCatalog.env_var}</strong></div>
                  <div><span>Error class</span><strong>{modelPricingCatalog.error_class ?? "n/a"}</strong></div>
                  <div><span>Catalog path</span><strong>{modelPricingCatalog.path ?? "n/a"}</strong></div>
                  <div><span>Current rules</span><strong>{modelPricingCatalog.rule_count}</strong></div>
                </div>
                <div className="config-field-grid">
                  <label className="field-stack">
                    <span>Version</span>
                    <input
                      type="text"
                      value={activeModelPricingDraft.version}
                      onChange={(event) =>
                        updateModelPricingDraft((draft) => ({ ...draft, version: event.target.value }))
                      }
                      disabled={modelPricingSaving}
                    />
                  </label>
                  <label className="field-stack">
                    <span>Currency</span>
                    <select
                      value={activeModelPricingDraft.currency}
                      onChange={(event) =>
                        updateModelPricingDraft((draft) => ({ ...draft, currency: event.target.value }))
                      }
                      disabled={modelPricingSaving}
                    >
                      <option value="usd">usd</option>
                    </select>
                  </label>
                  <label className="field-stack wide-field">
                    <span>Description</span>
                    <textarea
                      rows={3}
                      value={activeModelPricingDraft.description}
                      onChange={(event) =>
                        updateModelPricingDraft((draft) => ({ ...draft, description: event.target.value }))
                      }
                      disabled={modelPricingSaving}
                    />
                  </label>
                </div>

                <div className="detail-stack compact-detail-stack">
                  <div className="panel-header compact-panel-header">
                    <div>
                      <h4>Sources</h4>
                      <p className="muted-text">OpenAI documentation URLs used to maintain this rate card.</p>
                    </div>
                    <div className="feature-toolbar-actions model-pricing-section-actions">
                      <button type="button" onClick={addModelPricingSource} disabled={modelPricingSaving}>
                        Add source
                      </button>
                    </div>
                  </div>
                  {activeModelPricingDraft.sources.length ? (
                    <div className="detail-stack compact-detail-stack">
                      {activeModelPricingDraft.sources.map((source, index) => (
                        <div className="inline-actions compact-actions model-pricing-source-row" key={`source-${index}`}>
                          <input
                            type="url"
                            value={source}
                            onChange={(event) => updateModelPricingSource(index, event.target.value)}
                            disabled={modelPricingSaving}
                          />
                          <button
                            type="button"
                            onClick={() => removeModelPricingSource(index)}
                            disabled={modelPricingSaving}
                          >
                            Remove
                          </button>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <div className="empty-state">No source URLs are configured.</div>
                  )}
                </div>

                <div className="detail-stack compact-detail-stack">
                  <div className="panel-header compact-panel-header">
                    <div>
                      <h4>Pricing rules</h4>
                      <p className="muted-text">
                        Rates are expressed per 1M tokens. Exact models are structured tags and patterns are validated regular expressions.
                      </p>
                    </div>
                    <div className="feature-toolbar-actions model-pricing-section-actions">
                      <button type="button" onClick={addModelPricingRule} disabled={modelPricingSaving}>
                        Add rule
                      </button>
                    </div>
                  </div>
                  {activeModelPricingDraft.rules.length ? (
                  <div className="detail-stack compact-detail-stack">
                    {activeModelPricingDraft.rules.map((rule, index) => (
                      <div className="config-field-card model-pricing-rule-card" key={`${rule.id}-${index}`}>
                        <div className="feature-list-title-row">
                          <strong>{rule.id || `Rule ${index + 1}`}</strong>
                          <div className="inline-actions compact-actions">
                            <button
                              type="button"
                              onClick={() => duplicateModelPricingRule(index)}
                              disabled={modelPricingSaving}
                            >
                              Duplicate
                            </button>
                            <button
                              type="button"
                              className="danger"
                              onClick={() => deleteModelPricingRule(index)}
                              disabled={modelPricingSaving}
                            >
                              Delete
                            </button>
                          </div>
                        </div>
                        <div className="config-field-grid">
                          <label className="field-stack">
                            <span>Rule id</span>
                            <input
                              type="text"
                              value={rule.id}
                              onChange={(event) => updateModelPricingRule(index, { id: event.target.value })}
                              disabled={modelPricingSaving}
                            />
                          </label>
                          <label className="field-stack">
                            <span>Provider</span>
                            <input
                              type="text"
                              value={rule.provider}
                              onChange={(event) => updateModelPricingRule(index, { provider: event.target.value })}
                              disabled={modelPricingSaving}
                            />
                          </label>
                          <label className="field-stack">
                            <span>Effective from</span>
                            <input
                              type="text"
                              value={rule.effectiveFrom}
                              onChange={(event) => updateModelPricingRule(index, { effectiveFrom: event.target.value })}
                              disabled={modelPricingSaving}
                              placeholder="2026-06-04T00:00:00Z"
                            />
                          </label>
                          <label className="field-stack">
                            <span>Effective to</span>
                            <input
                              type="text"
                              value={rule.effectiveTo}
                              onChange={(event) => updateModelPricingRule(index, { effectiveTo: event.target.value })}
                              disabled={modelPricingSaving}
                              placeholder="optional"
                            />
                          </label>
                          <div className="field-stack wide-field">
                            <span>Models</span>
                            <StringListEditor
                              values={parseStringListDraftValueText(rule.modelsText)}
                              onChange={(values) =>
                                updateModelPricingRule(index, { modelsText: values.join("\n") })
                              }
                              itemLabel="Exact model"
                              addLabel="Add model"
                              emptyState="No exact models are configured."
                              placeholder="Enter an exact model"
                              disabled={modelPricingSaving}
                            />
                          </div>
                          <div className="field-stack wide-field">
                            <span>Model patterns</span>
                            <RegexListEditor
                              values={parseStringListDraftValueText(rule.modelPatternsText)}
                              onChange={(values) =>
                                updateModelPricingRule(index, { modelPatternsText: values.join("\n") })
                              }
                              fieldLabel="Model patterns"
                              addLabel="Add pattern"
                              emptyState="No model patterns are configured."
                              placeholder="Enter a regular expression"
                              disabled={modelPricingSaving}
                            />
                          </div>
                          <label className="field-stack">
                            <span>Input rate</span>
                            <input
                              type="text"
                              value={rule.inputRate}
                              onChange={(event) => updateModelPricingRule(index, { inputRate: event.target.value })}
                              disabled={modelPricingSaving}
                            />
                          </label>
                          <label className="field-stack">
                            <span>Cached input rate</span>
                            <input
                              type="text"
                              value={rule.cachedInputRate}
                              onChange={(event) => updateModelPricingRule(index, { cachedInputRate: event.target.value })}
                              disabled={modelPricingSaving}
                            />
                          </label>
                          <label className="field-stack">
                            <span>Output rate</span>
                            <input
                              type="text"
                              value={rule.outputRate}
                              onChange={(event) => updateModelPricingRule(index, { outputRate: event.target.value })}
                              disabled={modelPricingSaving}
                            />
                          </label>
                          <label className="field-stack">
                            <span>Batch multiplier</span>
                            <input
                              type="text"
                              value={rule.batchMultiplier}
                              onChange={(event) => updateModelPricingRule(index, { batchMultiplier: event.target.value })}
                              disabled={modelPricingSaving}
                              placeholder="optional"
                            />
                          </label>
                          <label className="field-stack">
                            <span>Service tier</span>
                            <input
                              type="text"
                              value={rule.serviceTier}
                              onChange={(event) => updateModelPricingRule(index, { serviceTier: event.target.value })}
                              disabled={modelPricingSaving}
                              placeholder="optional"
                            />
                          </label>
                          <label className="field-stack">
                            <span>Long-context threshold</span>
                            <input
                              type="text"
                              value={rule.longContextInputTokensOver}
                              onChange={(event) => updateModelPricingRule(index, { longContextInputTokensOver: event.target.value })}
                              disabled={modelPricingSaving}
                              placeholder="optional"
                            />
                          </label>
                          <label className="field-stack">
                            <span>Long-context input x</span>
                            <input
                              type="text"
                              value={rule.longContextInputMultiplier}
                              onChange={(event) => updateModelPricingRule(index, { longContextInputMultiplier: event.target.value })}
                              disabled={modelPricingSaving}
                              placeholder="optional"
                            />
                          </label>
                          <label className="field-stack">
                            <span>Long-context output x</span>
                            <input
                              type="text"
                              value={rule.longContextOutputMultiplier}
                              onChange={(event) => updateModelPricingRule(index, { longContextOutputMultiplier: event.target.value })}
                              disabled={modelPricingSaving}
                              placeholder="optional"
                            />
                          </label>
                        </div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="empty-state">No pricing rules are configured.</div>
                )}
                </div>

                {modelPricingCatalog.sources.length ? (
                  <div className="detail-stack compact-detail-stack">
                    <h4>Current source links</h4>
                    <div className="teams-auth-link-list">
                      {modelPricingCatalog.sources.map((source) => (
                        <div key={source}>
                          <span>OpenAI docs</span>
                          <a className="teams-auth-uri" href={source} target="_blank" rel="noreferrer">
                            {source}
                          </a>
                        </div>
                      ))}
                    </div>
                  </div>
                ) : null}
                  </>
                ) : null}
                </div>
              ) : null}
            </article>
          ) : null}
        </div>

        {(selectedTabDefinition?.sections ?? []).map((section) => {
          const sectionIsGlobal = section.fields.every(isGlobalField);
          return (
            <article key={section.title} className="panel section-panel">
              <button
                type="button"
                className="panel-header panel-toggle"
                onClick={() => toggleSection(section.title)}
                aria-expanded={!isSectionCollapsed(section.title)}
              >
                <div>
                  <h3>{section.title}</h3>
                  <p className="muted-text">
                    {sectionIsGlobal
                      ? "Edit runtime-global settings for this section."
                      : isRoot
                      ? "Edit root-level defaults for this section."
                      : "Enable overrides field-by-field to write only the values that should diverge from the parent directory."}
                  </p>
                </div>
                <span className="panel-toggle-indicator">{isSectionCollapsed(section.title) ? "Show" : "Hide"}</span>
              </button>
              {!isSectionCollapsed(section.title) ? (
                <div className="config-field-grid">
                  {section.fields.map((field) => {
                    const fieldIsGlobal = isGlobalField(field);
                    const fieldState = tabDraft[field.field_name] ?? {
                      include: false,
                      clear: false,
                      value: normalizeFieldValue(field, undefined),
                    };
                    const inherited = snapshot?.parent_defaults.config?.[field.field_name];
                    const inputDisabled = fieldIsGlobal
                      ? false
                      : isRoot
                      ? fieldState.clear
                      : !fieldState.include || fieldState.clear;
                    const isContactsField = isContactsConfigField(field);
                    const isCommunicationTargetsField = isCommunicationTargetsConfigField(field);
                    const isApprovalNotificationTargetList = isApprovalNotificationTargetField(field);
                    const inheritedCommunicationTargets = isCommunicationTargetsField
                      ? communicationTargetEntriesFromConfig(snapshot?.parent_defaults.config?.[field.field_name])
                      : [];
                    return (
                      <div
                        key={field.field_name}
                        className={`config-field-card${isContactsField || isCommunicationTargetsField ? " contacts-field-card" : ""}`}
                      >
                        <div className="feature-list-title-row">
                          <div className="config-field-label">
                            <strong>{field.label}</strong>
                            <Tooltip
                              content={field.tooltip}
                              focusable
                              ariaLabel={`${field.label} setting help`}
                              triggerClassName="config-field-tooltip-trigger"
                            >
                              <span className="config-field-tooltip-icon" aria-hidden="true">i</span>
                            </Tooltip>
                          </div>
                          <Tooltip content={fieldIsGlobal ? "Runtime-global admin setting." : `Underlying config domain: ${field.source_domain}.`}>
                            <span className="panel-meta">{field.source_domain}</span>
                          </Tooltip>
                        </div>
                        {!fieldIsGlobal ? (
                          <div className="config-toggle-row">
                            {!isRoot ? (
                              <label className="checkbox-row">
                                <input
                                  type="checkbox"
                                  checked={fieldState.include}
                                  onChange={(event) => updateFieldState(field.field_name, { include: event.target.checked })}
                                  disabled={!state?.directory_scoped_config_enabled || loading || saving}
                                />
                                <span>Override</span>
                              </label>
                            ) : null}
                            <label className="checkbox-row">
                              <input
                                type="checkbox"
                                checked={fieldState.clear}
                                onChange={(event) =>
                                  updateFieldState(field.field_name, isRoot ? { clear: event.target.checked, include: !event.target.checked } : { clear: event.target.checked })
                                }
                                disabled={!state?.directory_scoped_config_enabled || loading || saving || (!isRoot && !fieldState.include)}
                              />
                              <span>{isRoot ? "Unset" : "Clear"}</span>
                            </label>
                          </div>
                        ) : null}
                        <div className={inputDisabled ? "disabled-block" : ""}>
                          {isContactsField
                            ? renderContactsEditor(field, fieldState, inputDisabled || loading || saving || !state?.directory_scoped_config_enabled)
                            : isCommunicationTargetsField
                            ? renderCommunicationTargetsEditor(
                                field,
                                fieldState,
                                inputDisabled || loading || saving || !state?.directory_scoped_config_enabled,
                                inheritedCommunicationTargets,
                              )
                            : isApprovalNotificationTargetList
                            ? renderApprovalNotificationTargetEditor(
                                field,
                                fieldState,
                                inputDisabled || loading || saving || !state?.directory_scoped_config_enabled,
                              )
                            : renderFieldInput(field, fieldState)}
                        </div>
                        {fieldIsGlobal ? (
                          <div className="panel-meta">Runtime-global setting.</div>
                        ) : !isRoot ? (
                          <Tooltip content={inheritedPreview(inherited)} inline={false}>
                            <div className="panel-meta">{inheritedPreview(inherited)}</div>
                          </Tooltip>
                        ) : null}
                      </div>
                    );
                  })}
                </div>
              ) : null}
            </article>
          );
        })}
        {selectedTabDefinition && selectedTabDefinition.sections.length === 0 ? (
          <article className="panel">
            <div className="empty-state">This tab has no editable fields yet.</div>
          </article>
        ) : null}
        {!selectedTabDefinition ? (
          <article className="panel">
            <div className="empty-state">Select a configuration tab to inspect and edit it.</div>
          </article>
        ) : null}

        <article className="panel">
          <div className="panel-header">
            <h3>Validation</h3>
            <span className="panel-meta">{snapshot?.raw.exists ? "Saved overrides present" : "Inherited only"}</span>
          </div>
          <div className="diagnostic-list">
            {!snapshot ? (
              <div className="empty-state">No validation snapshot is available for the selected tab.</div>
            ) : snapshot.effective.validation_diagnostics.length ? (
              snapshot.effective.validation_diagnostics.map((diagnostic, index) => (
                <div key={`${diagnostic.code}-${index}`} className="diagnostic-item">
                  {diagnostic.message}
                </div>
              ))
            ) : (
              <div className="empty-state">No validation errors.</div>
            )}
            {snapshot?.effective.errors.length
              ? snapshot.effective.errors.map((diagnostic, index) => (
                  <div key={`${diagnostic.code}-error-${index}`} className="diagnostic-item error-item">
                    {diagnostic.message}
                  </div>
                ))
              : null}
          </div>
        </article>

        <div className="feature-grid configuration-diagnostics-grid">
          <article className="panel section-panel">
            <div className="panel-header configuration-json-panel-header">
              <button
                type="button"
                className="panel-toggle configuration-json-toggle"
                onClick={() => toggleSection("__directory_overrides")}
                aria-expanded={!isSectionCollapsed("__directory_overrides")}
              >
                <div>
                  <h3>Directory override JSON</h3>
                  <p className="muted-text">
                    Unified explicit overrides for {selectedDirectoryPath}, grouped by config domain.
                  </p>
                </div>
                <span className="panel-toggle-indicator">{isSectionCollapsed("__directory_overrides") ? "Show" : "Hide"}</span>
              </button>
              <div className="feature-toolbar-actions icon-actions">
                <Tooltip content="Copy unified directory override JSON.">
                  <button
                    type="button"
                    onClick={() => void copyDirectoryOverrides()}
                    disabled={!state}
                    aria-label="Copy directory override JSON"
                  >
                    <CopyIcon />
                  </button>
                </Tooltip>
              </div>
            </div>
            {!isSectionCollapsed("__directory_overrides") ? (
              <pre className="json-view">{formatJson(state?.directory_overrides.payload ?? {})}</pre>
            ) : null}
          </article>

          <article className="panel section-panel">
            <button
              type="button"
              className="panel-header panel-toggle"
              onClick={() => toggleSection("__effective_config")}
              aria-expanded={!isSectionCollapsed("__effective_config")}
            >
              <div>
                <h3>Effective config</h3>
                <p className="muted-text">Resolved output across {selectedTabDefinition?.underlying_domains.join(", ") || "the selected domains"}.</p>
              </div>
              <span className="panel-toggle-indicator">{isSectionCollapsed("__effective_config") ? "Show" : "Hide"}</span>
            </button>
            {!isSectionCollapsed("__effective_config") ? (
              <pre className="json-view">{formatJson(snapshot?.effective.payload ?? {})}</pre>
            ) : null}
          </article>

          <article className="panel section-panel">
            <button
              type="button"
              className="panel-header panel-toggle"
              onClick={() => toggleSection("__parent_defaults")}
              aria-expanded={!isSectionCollapsed("__parent_defaults")}
            >
              <div>
                <h3>Parent defaults</h3>
                <p className="muted-text">Inherited diagnostic payload from {snapshot?.parent_defaults.directory_path ?? "root"}.</p>
              </div>
              <span className="panel-toggle-indicator">{isSectionCollapsed("__parent_defaults") ? "Show" : "Hide"}</span>
            </button>
            {!isSectionCollapsed("__parent_defaults") ? (
              <pre className="json-view">{formatJson(snapshot?.parent_defaults.payload ?? {})}</pre>
            ) : null}
          </article>
        </div>
      </section>
    </div>
  );
}

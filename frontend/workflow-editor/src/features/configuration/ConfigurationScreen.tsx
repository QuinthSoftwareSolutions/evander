import { useEffect, useState } from "react";

import { fetchJson } from "../admin/api";
import type { AdminResult } from "../admin/AdminContext";
import { useAdminUi } from "../admin/AdminContext";
import { CopyIcon, SaveIcon } from "../admin/PageActionIcons";
import { Tooltip } from "../admin/Tooltip";

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

type DraftFieldState = {
  include: boolean;
  clear: boolean;
  value: string | boolean;
};

type TabDraft = Record<string, DraftFieldState>;
type TabDrafts = Record<string, TabDraft>;

const CONTACTS_TAB_KEY = "contacts";
const CONTACTS_FIELD_NAME = "contacts";
const APPROVAL_NOTIFICATION_TARGET_FIELD_NAME = "approval_notification_target_ids";

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

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isContactsConfigField(field: ConfigurationField): boolean {
  return field.source_domain === CONTACTS_TAB_KEY && field.field_name === CONTACTS_FIELD_NAME;
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

function formatJson(value: unknown): string {
  return JSON.stringify(value ?? {}, null, 2);
}

function normalizeFieldValue(field: ConfigurationField, value: unknown): string | boolean {
  if (field.kind === "bool") {
    return Boolean(value);
  }
  if (isContactsConfigField(field)) {
    return JSON.stringify(contactsValueToDraftEntries(value), null, 2);
  }
  if (field.kind === "string_list") {
    return Array.isArray(value) ? value.map((item) => String(item)).join("\n") : value == null ? "" : String(value);
  }
  if (field.kind === "json") {
    return value == null ? "" : JSON.stringify(value, null, 2);
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
    const text = String(rawValue).trim();
    if (!text) {
      throw new Error(`${field.label} must be valid JSON.`);
    }
    return JSON.parse(text);
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

export function ConfigurationScreen() {
  const {
    privilegedUnlocked,
    selectedDirectoryPath: globalSelectedDirectoryPath,
    setSelectedDirectoryPath: setGlobalSelectedDirectoryPath,
    refreshAdminState,
  } = useAdminUi();
  const [state, setState] = useState<ConfigurationState | null>(null);
  const [selectedDirectoryPath, setSelectedDirectoryPath] = useState(globalSelectedDirectoryPath);
  const [selectedTab, setSelectedTab] = useState("communications");
  const [collapsedSections, setCollapsedSections] = useState<Record<string, boolean>>({});
  const [drafts, setDrafts] = useState<TabDrafts>({});
  const [dirtyTabs, setDirtyTabs] = useState<Record<string, boolean>>({});
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  async function loadState(directoryPath: string, options?: { preserveDirty?: boolean }) {
    const payload = await fetchJson<ConfigurationState>(
      `/api/configuration/state?directory_path=${encodeURIComponent(directoryPath)}`,
    );
    setState(payload);
    setSelectedDirectoryPath(payload.selected_directory_path);
    if (payload.selected_directory_path !== globalSelectedDirectoryPath) {
      setGlobalSelectedDirectoryPath(payload.selected_directory_path);
    }
    setSelectedTab((current) => (payload.tabs.some((tab) => tab.key === current) ? current : payload.tabs[0]?.key ?? "communications"));
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

  async function handleSaveTab() {
    if (!selectedTabDefinition || !snapshot) return;
    const currentDraft = drafts[selectedTab] ?? {};
    const payload: Record<string, unknown> = {};
    try {
      for (const section of selectedTabDefinition.sections) {
        for (const field of section.fields) {
          const fieldState = currentDraft[field.field_name];
          if (!fieldState) continue;
          if (isGlobalField(field)) {
            payload[field.field_name] = parseFieldValue(field, fieldState.value);
            continue;
          }
          if (!state?.directory_scoped_config_enabled) {
            continue;
          }
          if (isRoot) {
            if (fieldState.clear) {
              continue;
            }
            payload[field.field_name] = parseFieldValue(field, fieldState.value);
            continue;
          }
          if (!fieldState.include) {
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
        body: JSON.stringify({ config: payload }),
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
      setError(reason instanceof Error ? reason.message : String(reason));
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
            Select notify-capable communication targets declared in{" "}
            <code>config/communication-targets/config.json</code>.
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
    if (field.kind === "multiline_text" || field.kind === "string_list" || field.kind === "json") {
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
                    const isApprovalNotificationTargetList = isApprovalNotificationTargetField(field);
                    return (
                      <div
                        key={field.field_name}
                        className={`config-field-card${isContactsField ? " contacts-field-card" : ""}`}
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

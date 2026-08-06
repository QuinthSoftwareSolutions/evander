import type {
  AgentCreateCommunicationTargetOption,
  AgentCreateEmailMailboxOption,
  EmailMailboxAccess,
  EmailMailboxGrant,
} from "../admin/types";

function dedupeStrings(values: readonly string[]): string[] {
  return [...new Set(values.map((value) => value.trim()).filter(Boolean))];
}

function metadataString(option: AgentCreateCommunicationTargetOption, key: string): string {
  const value = option.metadata?.[key];
  return typeof value === "string" ? value.trim() : "";
}

function metadataStringList(option: AgentCreateCommunicationTargetOption, key: string): string[] {
  const value = option.metadata?.[key];
  return Array.isArray(value)
    ? value.map((entry) => (typeof entry === "string" ? entry.trim() : "")).filter(Boolean)
    : [];
}

function formatToken(value: string, fallback = "Unknown"): string {
  return value.trim()
    ? value.trim().replace(/[_-]+/g, " ").replace(/\s+/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase())
    : fallback;
}

export function CommunicationTargetCheckboxTable({
  values,
  options,
  onChange,
  emptyState,
  disabled = false,
}: {
  values: readonly string[];
  options: readonly AgentCreateCommunicationTargetOption[];
  onChange: (nextValues: string[]) => void;
  emptyState: string;
  disabled?: boolean;
}) {
  const normalizedValues = dedupeStrings(values);
  const selectedValues = new Set(normalizedValues);
  const knownValues = new Set(options.map((option) => option.value));
  const rows = [
    ...options.map((option) => ({
      value: option.value,
      detail: option.description?.trim() || (option.label !== option.value ? option.label : ""),
      provider: formatToken(metadataString(option, "provider")),
      shape: formatToken(metadataString(option, "target_shape")),
      capabilities: metadataStringList(option, "policy_capabilities"),
      unknown: false,
    })),
    ...normalizedValues.filter((value) => !knownValues.has(value)).map((value) => ({
      value,
      detail: "Selected target is no longer in the directory options.",
      provider: "Unknown",
      shape: "Unknown",
      capabilities: [] as string[],
      unknown: true,
    })),
  ];

  return (
    <div className="agent-communication-target-selector">
      {rows.length > 0 ? (
        <div className="agent-communication-target-table-wrap">
          <table className="agent-communication-target-table">
            <thead><tr><th>Select</th><th>Target</th><th>Provider</th><th>Shape</th><th>Capabilities</th></tr></thead>
            <tbody>
              {rows.map((row) => {
                const checked = selectedValues.has(row.value);
                return (
                  <tr key={row.value} className={checked ? "selected" : ""}>
                    <td className="agent-communication-target-select-cell"><input type="checkbox" disabled={disabled} checked={checked} onChange={(event) => onChange(event.target.checked ? dedupeStrings([...normalizedValues, row.value]) : normalizedValues.filter((value) => value !== row.value))} aria-label={`Allow communication target ${row.value}`} /></td>
                    <td><div className="agent-communication-target-name"><strong>{row.value}</strong>{row.detail ? <span>{row.detail}</span> : null}{row.unknown ? <span className="agent-communication-target-warning">Unknown target</span> : null}</div></td>
                    <td>{row.provider}</td><td>{row.shape}</td>
                    <td>{row.capabilities.length ? <span className="agent-communication-target-capabilities">{row.capabilities.map((capability) => <span key={capability}>{capability}</span>)}</span> : <span className="muted-text">None</span>}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}
      {normalizedValues.length === 0 ? <div className="empty-state">{emptyState}</div> : null}
    </div>
  );
}

export function EmailMailboxGrantEditor({
  grants,
  options,
  onChange,
  emptyState,
  disabled = false,
}: {
  grants: readonly EmailMailboxGrant[];
  options: readonly AgentCreateEmailMailboxOption[];
  onChange: (nextGrants: EmailMailboxGrant[]) => void;
  emptyState: string;
  disabled?: boolean;
}) {
  const grantsById = new Map(grants.map((grant) => [grant.mailbox_id, grant]));
  const optionsById = new Map(options.map((option) => [option.mailbox_id, option]));
  const rows = [
    ...options,
    ...grants.filter((grant) => !optionsById.has(grant.mailbox_id)).map((grant) => ({
      mailbox_id: grant.mailbox_id,
      provider: "unknown",
      account_identifier: "Mailbox is no longer configured",
      folders: grant.folders,
      default_folders: grant.folders,
      send_enabled: false,
    })),
  ];

  function replaceGrant(mailboxId: string, grant: EmailMailboxGrant | null) {
    onChange([
      ...grants.filter((item) => item.mailbox_id !== mailboxId),
      ...(grant ? [grant] : []),
    ]);
  }

  function changeAccess(option: AgentCreateEmailMailboxOption, access: EmailMailboxAccess) {
    const current = grantsById.get(option.mailbox_id);
    const folders = dedupeStrings([
      ...(current?.folders ?? option.default_folders),
      ...(access === "read" ? [] : ["drafts"]),
    ]);
    replaceGrant(option.mailbox_id, { mailbox_id: option.mailbox_id, access, folders });
  }

  return (
    <div className="agent-communication-target-selector">
      {rows.length ? (
        <div className="agent-communication-target-table-wrap">
          <table className="agent-communication-target-table mailbox-grant-table">
            <thead><tr><th>Select</th><th>Mailbox account</th><th>Access</th><th>Folders</th></tr></thead>
            <tbody>
              {rows.map((option) => {
                const grant = grantsById.get(option.mailbox_id);
                const selectedFolders = new Set(grant?.folders ?? []);
                const hasDrafts = option.folders.includes("drafts");
                return (
                  <tr key={option.mailbox_id} className={grant ? "selected" : ""}>
                    <td className="agent-communication-target-select-cell"><input type="checkbox" disabled={disabled} checked={Boolean(grant)} onChange={(event) => replaceGrant(option.mailbox_id, event.target.checked ? { mailbox_id: option.mailbox_id, access: "read", folders: dedupeStrings(option.default_folders) } : null)} aria-label={`Grant mailbox ${option.mailbox_id}`} /></td>
                    <td><div className="agent-communication-target-name"><strong>{option.mailbox_id}</strong><span>{option.account_identifier} · {option.provider}</span>{!optionsById.has(option.mailbox_id) ? <span className="agent-communication-target-warning">Unknown mailbox</span> : null}</div></td>
                    <td>
                      <select value={grant?.access ?? "read"} disabled={disabled || !grant} onChange={(event) => changeAccess(option, event.target.value as EmailMailboxAccess)}>
                        <option value="read">Read only</option>
                        <option value="draft" disabled={!hasDrafts}>Read + draft</option>
                        <option value="send" disabled={!hasDrafts || !option.send_enabled}>Read + draft + send</option>
                      </select>
                    </td>
                    <td><div className="mailbox-folder-options">{option.folders.map((folder) => <label key={folder}><input type="checkbox" disabled={disabled || !grant || ((grant.access === "draft" || grant.access === "send") && folder === "drafts")} checked={selectedFolders.has(folder)} onChange={(event) => grant && replaceGrant(option.mailbox_id, { ...grant, folders: event.target.checked ? dedupeStrings([...grant.folders, folder]) : grant.folders.filter((value) => value !== folder) })} />{folder}</label>)}</div></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}
      {grants.length === 0 ? <div className="empty-state">{emptyState}</div> : null}
    </div>
  );
}

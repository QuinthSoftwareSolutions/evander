import type {
  AgentCreateHostDirectoryOption,
  HostDirectoryCapability,
} from "../admin/types";

const TOOL_BY_CAPABILITY: Record<HostDirectoryCapability, string> = {
  list: "host_directory_list",
  import: "host_directory_import",
  delete: "host_directory_delete",
};

const CAPABILITY_DESCRIPTIONS: Record<HostDirectoryCapability, string> = {
  list: "Browse approved files and folders without exposing the host path.",
  import: "Copy a file or folder into the session as an artifact.",
  delete: "Move a file or folder into the host-side quarantine.",
};

export function hostDirectoryToolsForGrants(
  grants: Readonly<Record<string, readonly string[]>>,
): string[] {
  const tools = new Set<string>();
  for (const capabilities of Object.values(grants)) {
    for (const capability of capabilities) {
      if (capability in TOOL_BY_CAPABILITY) {
        tools.add(TOOL_BY_CAPABILITY[capability as HostDirectoryCapability]);
      }
    }
  }
  return [...tools];
}

export function HostDirectoryGrantSelector({
  values,
  options,
  onChange,
  disabled = false,
}: {
  values: Readonly<Record<string, string[]>>;
  options: readonly AgentCreateHostDirectoryOption[];
  onChange: (nextValues: Record<string, string[]>) => void;
  disabled?: boolean;
}) {
  function setCapability(
    directoryId: string,
    capability: HostDirectoryCapability,
    checked: boolean,
  ) {
    const current = values[directoryId] ?? [];
    const nextCapabilities = checked
      ? [...new Set([...current, capability])]
      : current.filter((name) => name !== capability);
    const next = { ...values };
    if (nextCapabilities.length) next[directoryId] = nextCapabilities;
    else delete next[directoryId];
    onChange(next);
  }

  const optionsById = new Map(
    options.map((directory) => [directory.directory_id, directory] as const),
  );
  const unavailableGrants = Object.entries(values).flatMap(
    ([directoryId, capabilities]) => {
      const option = optionsById.get(directoryId);
      const availableCapabilities = new Set(option?.capabilities ?? []);
      const unavailableCapabilities = capabilities.filter(
        (capability) => !availableCapabilities.has(capability as HostDirectoryCapability),
      );
      return unavailableCapabilities.length
        ? [{
            directoryId,
            displayName: option?.display_name ?? directoryId,
            capabilities: unavailableCapabilities,
          }]
        : [];
    },
  );

  function revokeUnavailable(directoryId: string, capabilities: readonly string[]) {
    const unavailable = new Set(capabilities);
    const retained = (values[directoryId] ?? []).filter(
      (capability) => !unavailable.has(capability),
    );
    const next = { ...values };
    if (retained.length) next[directoryId] = retained;
    else delete next[directoryId];
    onChange(next);
  }

  return (
    <div className="detail-stack host-directory-grant-selector">
      {!options.length ? (
        <div className="empty-state">No host directories are configured for this directory.</div>
      ) : null}
      {options.map((directory) => (
        <details key={directory.directory_id} className="compact-disclosure">
          <summary className="compact-disclosure-summary">
            <span>{directory.display_name}</span>
            <span className="panel-meta">
              {values[directory.directory_id]?.length ?? 0} granted
            </span>
          </summary>
          {directory.description ? <p className="muted-text">{directory.description}</p> : null}
          <div className="feature-list compact-list">
            {directory.capabilities.map((capability) => (
              <label className="session-tool-row" key={capability}>
                <span className="session-tool-row-main">
                  <input
                    type="checkbox"
                    checked={(values[directory.directory_id] ?? []).includes(capability)}
                    onChange={(event) =>
                      setCapability(directory.directory_id, capability, event.target.checked)
                    }
                    disabled={disabled}
                  />
                  <span>
                    <strong>{capability}</strong>
                    <span className="muted-text"> {CAPABILITY_DESCRIPTIONS[capability]}</span>
                  </span>
                </span>
                {capability === "delete" && directory.delete_requires_approval ? (
                  <span className="status-pill state-waiting">Approval</span>
                ) : null}
              </label>
            ))}
          </div>
        </details>
      ))}
      {unavailableGrants.length ? (
        <div className="field-stack host-directory-unavailable-grants">
          <strong>Unavailable grants</strong>
          <p className="muted-text">
            These grants refer to a host directory or capability that is no longer available.
            Revoke them before saving other policy changes.
          </p>
          <div className="feature-list compact-list">
            {unavailableGrants.map((grant) => (
              <div className="session-tool-row" key={grant.directoryId}>
                <span className="session-tool-row-main">
                  <span>
                    <strong>{grant.displayName}</strong>
                    <span className="muted-text">
                      {" "}{grant.capabilities.join(", ")}
                    </span>
                  </span>
                </span>
                <button
                  type="button"
                  className="admin-action-button compact"
                  onClick={() => revokeUnavailable(grant.directoryId, grant.capabilities)}
                  disabled={disabled}
                >
                  Revoke unavailable
                </button>
              </div>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}

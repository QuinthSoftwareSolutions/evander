import { type ReactNode, useCallback, useEffect, useMemo, useState } from "react";

import { fetchJson, formatTimestamp } from "../admin/api";
import { useAdminUi } from "../admin/AdminContext";
import { RecordWorkspace } from "../admin/RecordWorkspace";
import { timestampSortValue } from "../admin/tableSorting";
import { readAdminHashQuery } from "../admin/hash";
import { CreateVmIcon, DeleteIcon, EditIcon } from "../admin/PageActionIcons";
import { Tooltip } from "../admin/Tooltip";
import { type IssueCreateResponse, type IssueDetail, type IssueListResponse, type IssueRecord, type VmActionResponse } from "../admin/types";

type IssueMode = "manual" | "import";

type VmOptionsState = {
  model: string;
  dotnet: boolean;
  nugetEnabled: boolean;
  nuget: string;
  playwright: boolean;
  mssql: boolean;
  mssqlInitRoot: string;
};

type IssueFormState = {
  mode: IssueMode;
  title: string;
  repoSource: string;
  managedRepoId: string;
  state: string;
  body: string;
  yamlPath: string;
  startVm: boolean;
  vmOptions: VmOptionsState;
};

const ISSUE_STATES = ["New", "Approved", "Committed", "In Progress", "Completed", "Closed"];

const EMPTY_VM_OPTIONS: VmOptionsState = {
  model: "",
  dotnet: false,
  nugetEnabled: false,
  nuget: "",
  playwright: false,
  mssql: false,
  mssqlInitRoot: "",
};

const EMPTY_FORM: IssueFormState = {
  mode: "manual",
  title: "",
  repoSource: "",
  managedRepoId: "",
  state: "New",
  body: "",
  yamlPath: "",
  startVm: false,
  vmOptions: EMPTY_VM_OPTIONS,
};

const VM_OPTION_HELP = {
  dotnet: ".NET SDK bootstrap installs the .NET tooling used by this VM.",
  nugetEnabled: "NuGet packages bootstrap enables the optional restore target below.",
  nuget: "Only enabled when NuGet packages is checked. Leave blank to use the default restore target.",
  playwright: "Playwright browsers bootstrap installs browser and test dependencies.",
  mssql: "MSSQL test database bootstrap provisions SQL Server and database setup using the optional init root.",
  mssqlInitRoot: "Only enabled when MSSQL test database is checked. Leave blank to use the default init root.",
} as const;

const REFRESH_MS = 10000;
const ISSUE_SELECTION_STORAGE_KEY = "admin-web:issues:selected-issue";

type IssueMetadataEntry = {
  label: string;
  value: IssueMetadataValue;
};

type IssueMetadataValue =
  | { kind: "scalar"; text: string }
  | { kind: "list"; items: IssueMetadataValue[] }
  | { kind: "map"; entries: IssueMetadataEntry[] };

function issueSummaryText(issue: IssueDetail): string {
  const summaryText = issueBodyText(issue);
  const lines = [
    `Issue: ${issue.issue_number}`,
    `Title: ${issue.title}`,
    `State: ${issue.state ?? ""}`,
    `Source: ${issueSourceLabel(issue.source_type)}`,
    `Directory: ${issue.directory_path ?? "root"}`,
    `Managed repository: ${issue.managed_repo_id ?? "None"}`,
  ];
  if (issue.reason) {
    lines.push(`Reason: ${issue.reason}`);
  }
  if (summaryText) {
    lines.push("", summaryText);
  }
  return lines.join("\n");
}

function htmlToPlainText(value: string | null | undefined): string {
  const raw = String(value ?? "").trim();
  if (!raw) return "";
  if (typeof document === "undefined") {
    return raw.replace(/<[^>]+>/g, " ").replace(/&nbsp;/g, " ").replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ").trim();
  }
  const element = document.createElement("div");
  element.innerHTML = raw;
  return (element.textContent || element.innerText || "")
    .replace(/\u00a0/g, " ")
    .replace(/\s+\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function issueBodyText(issue: IssueDetail): string {
  return htmlToPlainText(issue.description ?? issue.body ?? "");
}

function issueSourceLabel(sourceType: string | null | undefined): string {
  const normalized = String(sourceType ?? "").trim();
  if (normalized === "local_manual") return "Local issue";
  if (normalized === "remote_proxy") return "Remote issue";
  if (normalized === "local") return "Local issue";
  if (normalized === "proxy") return "Remote issue";
  return normalized || "Unknown";
}

function scalarMetadataValueLabel(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "string") return value.trim();
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return String(value);
}

function issueMetadataValue(value: unknown): IssueMetadataValue | null {
  if (value == null) return null;
  if (Array.isArray(value)) {
    const items = value.map((item) => issueMetadataValue(item)).filter((item): item is IssueMetadataValue => item !== null);
    return items.length > 0 ? { kind: "list", items } : null;
  }
  if (typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>)
      .map(([key, item]) => {
        const nestedValue = issueMetadataValue(item);
        return nestedValue ? { label: metadataLabel(key), value: nestedValue } : null;
      })
      .filter((item): item is IssueMetadataEntry => item !== null);
    return entries.length > 0 ? { kind: "map", entries } : null;
  }
  const text = scalarMetadataValueLabel(value);
  return text ? { kind: "scalar", text } : null;
}

function metadataLabel(key: string): string {
  return key
    .replace(/_/g, " ")
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

function issueMetadataDisplayEntries(issue: IssueDetail): IssueMetadataEntry[] {
  return Object.entries(issue.metadata ?? {})
    .filter(([key]) => !["proxy_cache", "remote"].includes(key))
    .map(([key, rawValue]) => {
      const value = issueMetadataValue(rawValue);
      return value ? { label: metadataLabel(key), value } : null;
    })
    .filter((entry): entry is IssueMetadataEntry => entry !== null);
}

function renderIssueMetadataValue(value: IssueMetadataValue): ReactNode {
  if (value.kind === "scalar") {
    return <span className="issue-metadata-scalar">{value.text}</span>;
  }
  if (value.kind === "list") {
    return (
      <ol className="issue-metadata-items">
        {value.items.map((item, index) => (
          <li key={index}>{renderIssueMetadataValue(item)}</li>
        ))}
      </ol>
    );
  }
  return (
    <dl className="issue-metadata-map">
      {value.entries.map((entry, index) => (
        <div key={`${entry.label}-${index}`}>
          <dt>{entry.label}</dt>
          <dd>{renderIssueMetadataValue(entry.value)}</dd>
        </div>
      ))}
    </dl>
  );
}

function issueFormFromDetail(issue: IssueDetail): IssueFormState {
  return {
    mode: "manual",
    title: issue.title,
    repoSource: "",
    managedRepoId: issue.managed_repo_id ?? "",
    state: issue.state ?? "New",
    body: issue.body,
    yamlPath: issue.yaml_path ?? "",
    startVm: false,
    vmOptions: { ...EMPTY_VM_OPTIONS },
  };
}

function vmOptionsPayload(options: VmOptionsState) {
  return {
    model: options.model.trim() || null,
    dotnet: options.dotnet,
    nuget: options.nugetEnabled ? options.nuget.trim() || null : null,
    playwright: options.playwright,
    mssql: options.mssql,
    mssql_init_root: options.mssql ? options.mssqlInitRoot.trim() || null : null,
  };
}

function issueStateClass(state: string | null | undefined): string {
  const normalized = String(state ?? "New").trim().toLowerCase();
  if (normalized.includes("progress") || normalized === "approved") {
    return "running";
  }
  if (normalized === "closed" || normalized === "completed") {
    return "failed";
  }
  return "idle";
}

function VmOptionsEditor({
  value,
  onChange,
  disabled = false,
}: {
  value: VmOptionsState;
  onChange: (next: VmOptionsState) => void;
  disabled?: boolean;
}) {
  return (
    <>
      <div className="toolbar-grid">
        <label className="field-stack">
          <span>Model</span>
          <input
            value={value.model}
            onChange={(event) => onChange({ ...value, model: event.target.value })}
            placeholder="Optional; uses directory default when empty"
            disabled={disabled}
          />
        </label>
        <label className="field-stack">
          <Tooltip content={VM_OPTION_HELP.nuget}>
            <span>NuGet restore target</span>
          </Tooltip>
          <input
            value={value.nuget}
            onChange={(event) => onChange({ ...value, nuget: event.target.value })}
            placeholder="/path/to/project-or-solution.slnx"
            disabled={disabled || !value.nugetEnabled}
          />
        </label>
        <label className="field-stack">
          <Tooltip content={VM_OPTION_HELP.mssqlInitRoot}>
            <span>MSSQL init root</span>
          </Tooltip>
          <input
            value={value.mssqlInitRoot}
            onChange={(event) => onChange({ ...value, mssqlInitRoot: event.target.value })}
            placeholder="/path/to/mssql-init"
            disabled={disabled || !value.mssql}
          />
        </label>
      </div>
      <div className="toolbar-grid">
        <label className="checkbox-row">
          <input type="checkbox" checked={value.dotnet} onChange={(event) => onChange({ ...value, dotnet: event.target.checked })} disabled={disabled} />
          <Tooltip content={VM_OPTION_HELP.dotnet}>
            <span>Bootstrap .NET SDK</span>
          </Tooltip>
        </label>
        <label className="checkbox-row">
          <input
            type="checkbox"
            checked={value.nugetEnabled}
            onChange={(event) => onChange({ ...value, nugetEnabled: event.target.checked, nuget: event.target.checked ? value.nuget : "" })}
            disabled={disabled}
          />
          <Tooltip content={VM_OPTION_HELP.nugetEnabled}>
            <span>NuGet packages</span>
          </Tooltip>
        </label>
        <label className="checkbox-row">
          <input
            type="checkbox"
            checked={value.playwright}
            onChange={(event) => onChange({ ...value, playwright: event.target.checked })}
            disabled={disabled}
          />
          <Tooltip content={VM_OPTION_HELP.playwright}>
            <span>Playwright browsers</span>
          </Tooltip>
        </label>
        <label className="checkbox-row">
          <input type="checkbox" checked={value.mssql} onChange={(event) => onChange({ ...value, mssql: event.target.checked })} disabled={disabled} />
          <Tooltip content={VM_OPTION_HELP.mssql}>
            <span>MSSQL test database</span>
          </Tooltip>
        </label>
      </div>
    </>
  );
}

export function IssuesPage() {
  const { privilegedUnlocked, selectedDirectoryPath } = useAdminUi();
  const [stateFilter, setStateFilter] = useState(() => readAdminHashQuery().get("issue_id") ? "all" : "active");
  const [items, setItems] = useState<IssueRecord[]>([]);
  const [selectedIssueId, setSelectedIssueId] = useState<string>("");
  const [selectedIssue, setSelectedIssue] = useState<IssueDetail | null>(null);
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState(false);
  const [creatingVm, setCreatingVm] = useState(false);
  const [form, setForm] = useState<IssueFormState>(EMPTY_FORM);
  const [vmOptions, setVmOptions] = useState<VmOptionsState>(EMPTY_VM_OPTIONS);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [validation, setValidation] = useState<string | null>(null);
  const [selectionHydrated, setSelectionHydrated] = useState(false);

  useEffect(() => {
    const saved = readAdminHashQuery().get("issue_id") ?? window.localStorage.getItem(ISSUE_SELECTION_STORAGE_KEY);
    if (saved) {
      setSelectedIssueId(saved);
    }
    setSelectionHydrated(true);
  }, []);

  useEffect(() => {
    if (!selectionHydrated) return;
    if (selectedIssueId) {
      window.localStorage.setItem(ISSUE_SELECTION_STORAGE_KEY, selectedIssueId);
      return;
    }
    window.localStorage.removeItem(ISSUE_SELECTION_STORAGE_KEY);
  }, [selectedIssueId, selectionHydrated]);

  const selectedIssueRecord = useMemo(
    () => items.find((item) => item.issue_id === selectedIssueId) ?? null,
    [items, selectedIssueId],
  );
  const sortedItems = useMemo(
    () =>
      [...items].sort((left, right) =>
        left.issue_number.localeCompare(right.issue_number, undefined, {
          numeric: true,
          sensitivity: "base",
        }),
      ),
    [items],
  );
  const mutationLocked = !privilegedUnlocked;

  const refreshList = useCallback(async () => {
    const query = new URLSearchParams({
      state_filter: stateFilter,
      directory_path: selectedDirectoryPath,
      direct_only: "true",
    });
    const payload = await fetchJson<IssueListResponse>(`/api/admin/issues?${query.toString()}`);
    setItems(payload.items);
    setSelectedIssueId((current) => {
      if (current && payload.items.some((item) => item.issue_id === current)) {
        return current;
      }
      return payload.items[0]?.issue_id ?? "";
    });
  }, [stateFilter, selectedDirectoryPath]);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        await refreshList();
        if (!cancelled) {
          setError(null);
        }
      } catch (reason) {
        if (!cancelled) {
          setError(reason instanceof Error ? reason.message : String(reason));
        }
      }
    }
    void load();
    const timer = window.setInterval(() => void load(), REFRESH_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [refreshList]);

  useEffect(() => {
    if (!selectedIssueId) {
      setSelectedIssue(null);
      return;
    }
    let cancelled = false;
    fetchJson<IssueDetail>(`/api/admin/issues/${encodeURIComponent(selectedIssueId)}`)
      .then((issuePayload) => {
        if (cancelled) return;
        setSelectedIssue(issuePayload);
        if (!editing) {
          setForm(issueFormFromDetail(issuePayload));
        }
        if (!cancelled) {
          setError(null);
        }
      })
      .catch((reason) => {
        if (cancelled) return;
        setSelectedIssue(null);
        setError(reason instanceof Error ? reason.message : String(reason));
      });
    return () => {
      cancelled = true;
    };
  }, [selectedIssueId, editing]);

  async function handleCreateIssue() {
    if (form.mode === "manual" && !form.title.trim()) {
      setValidation("Title is required when creating a local issue from the form.");
      return;
    }
    if (form.mode === "import" && !form.yamlPath.trim()) {
      setValidation("YAML path is required when importing an existing issue file.");
      return;
    }
    setBusy("create");
    setValidation(null);
    try {
      const payload = await fetchJson<IssueCreateResponse>("/api/admin/issues", {
        method: "POST",
        body: JSON.stringify({
          title: form.mode === "manual" ? form.title : "",
          body: form.mode === "manual" ? form.body : "",
          repo_source: form.repoSource || null,
          managed_repo_id: form.repoSource ? null : form.managedRepoId || null,
          yaml_path: form.mode === "import" ? form.yamlPath || null : null,
          directory_path: selectedDirectoryPath,
          start_vm: form.startVm,
          ...vmOptionsPayload(form.vmOptions),
        }),
      });
      setCreating(false);
      setCreatingVm(false);
      setStatus(
        payload.vm_action
          ? `Created issue ${payload.issue.issue_number} and started VM ${payload.vm_action.vm_name}.`
          : `Created issue ${payload.issue.issue_number}.`,
      );
      setError(null);
      setForm({ ...EMPTY_FORM, vmOptions: { ...EMPTY_VM_OPTIONS } });
      await refreshList();
      setSelectedIssueId(payload.issue.issue_id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(null);
    }
  }

  async function handleSaveIssue() {
    if (!selectedIssue) return;
    setBusy("save");
    setValidation(null);
    try {
      await fetchJson<IssueDetail>(`/api/admin/issues/${encodeURIComponent(selectedIssue.issue_id)}`, {
        method: "PUT",
        body: JSON.stringify({
          title: form.title,
          body: form.body,
          repo_source: form.repoSource || null,
          managed_repo_id: form.repoSource ? null : form.managedRepoId || null,
          state: form.state,
        }),
      });
      setEditing(false);
      setStatus(`Updated issue ${selectedIssue.issue_number}.`);
      setError(null);
      await refreshList();
      setSelectedIssueId(selectedIssue.issue_id);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(null);
    }
  }

  async function handleDeleteIssue() {
    if (!selectedIssue) return;
    if (!window.confirm(`Delete local issue ${selectedIssue.issue_number}? This removes the stored YAML and local issue record.`)) return;
    setBusy("delete");
    try {
      const payload = await fetchJson<VmActionResponse>(`/api/admin/issues/${encodeURIComponent(selectedIssue.issue_id)}`, {
        method: "DELETE",
      });
      setStatus(payload.message);
      setError(null);
      setEditing(false);
      setCreatingVm(false);
      setSelectedIssue(null);
      await refreshList();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(null);
    }
  }

  async function handleCreateVm() {
    if (!selectedIssue) return;
    setBusy("create-vm");
    setValidation(null);
    try {
      const payload = await fetchJson<VmActionResponse>(
        `/api/admin/issues/${encodeURIComponent(selectedIssue.issue_id)}/create-vm`,
        {
          method: "POST",
          body: JSON.stringify(vmOptionsPayload(vmOptions)),
        },
      );
      setStatus(payload.message);
      setError(null);
      setCreatingVm(false);
      await refreshList();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(null);
    }
  }

  const showingForm = creating || editing;
  const activeIssueState = selectedIssue?.state ?? selectedIssueRecord?.state ?? "New";
  const selectedIssueBodyText = useMemo(() => (selectedIssue ? issueBodyText(selectedIssue) : ""), [selectedIssue]);
  const selectedIssueMetadata = useMemo(() => (selectedIssue ? issueMetadataDisplayEntries(selectedIssue) : []), [selectedIssue]);

  return (
    <RecordWorkspace
      title="Issues"
      description="Browse local issues and their source context. Open an issue to read its description or launch work."
      columns={["Issue", "State", "Source", "Updated"]}
      records={sortedItems.map((item) => ({
        id: item.issue_id,
        title: `${item.issue_number} · ${item.title}`,
        href: `#issues?${new URLSearchParams({ issue_id: item.issue_id })}`,
        cells: [
          {
            value: item.state ?? "New",
            content: <span className={`status-pill state-${issueStateClass(item.state)}`}>{item.state ?? "New"}</span>,
          },
          { value: issueSourceLabel(item.source_type) },
          { value: timestampSortValue(item.updated_at), content: formatTimestamp(item.updated_at) },
        ],
        facts: [
          ["Issue", item.issue_number],
          ["State", item.state ?? "New"],
          ["Directory", item.directory_path],
          ["Source", issueSourceLabel(item.source_type)],
          ["Repository", item.managed_repo_id ?? "None"],
          ["Updated", formatTimestamp(item.updated_at)],
        ],
      }))}
      selectedId={selectedIssueId}
      error={error}
      initialRecordOpen={Boolean(readAdminHashQuery().get("issue_id"))}
      recordRouteKeys="issue_id"
      onSelect={(id) => {
        if ((editing || creating) && !window.confirm("Discard the open issue form and inspect another issue?"))
          return false;
        setSelectedIssueId(id);
        setCreating(false);
        setEditing(false);
        setCreatingVm(false);
        setValidation(null);
      }}
      filters={
        <label className="field-stack">
          <span>State</span>
          <select value={stateFilter} onChange={(event) => setStateFilter(event.target.value)}>
            <option value="active">Active</option>
            <option value="all">All</option>
            {ISSUE_STATES.map((state) => (
              <option key={state} value={state}>
                {state}
              </option>
            ))}
          </select>
        </label>
      }
      actions={(openRecord) => (
        <button
          type="button"
          className="primary"
          disabled={mutationLocked}
          title={mutationLocked ? "Unlock privileged operations to create issues" : undefined}
          onClick={() => {
            setCreating(true);
            setEditing(false);
            setCreatingVm(false);
            setForm({ ...EMPTY_FORM, vmOptions: { ...EMPTY_VM_OPTIONS } });
            setValidation(null);
            openRecord();
          }}
        >
          New issue
        </button>
      )}
    >
      <div className="feature-page issues-page">
        <section className="feature-sidebar">
          <div className="feature-sidebar-header">
            <div>
              <div className="eyebrow">Local issues</div>
              <h1>Issues</h1>
            </div>
            <div className="inline-actions compact-actions">
              <button
                type="button"
                className={creating ? "" : "primary"}
                onClick={() => {
                  setCreating((current) => !current);
                  setEditing(false);
                  setCreatingVm(false);
                  setForm({ ...EMPTY_FORM, vmOptions: { ...EMPTY_VM_OPTIONS } });
                  setValidation(null);
                }}
                disabled={mutationLocked}
              >
                {creating ? "Cancel" : "New issue"}
              </button>
            </div>
          </div>
          {mutationLocked ? (
            <div className="callout warning-callout">
              Unlock privileged operations to create, edit, delete, or launch work from issues.
            </div>
          ) : null}
          <div className="toolbar-grid issues-filter-grid">
            <label className="field-stack">
              <span>State</span>
              <select value={stateFilter} onChange={(event) => setStateFilter(event.target.value)}>
                <option value="active">Active</option>
                <option value="all">All</option>
                {ISSUE_STATES.map((state) => (
                  <option key={state} value={state}>
                    {state}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <div className="feature-list issue-list">
            {sortedItems.map((item) => (
              <button
                key={item.issue_id}
                type="button"
                className={`feature-list-item issue-list-item${item.issue_id === selectedIssueId ? " selected" : ""}`}
                onClick={() => {
                  setSelectedIssueId(item.issue_id);
                  setCreating(false);
                  setEditing(false);
                  setCreatingVm(false);
                  setValidation(null);
                }}
              >
                <div className="feature-list-title-row issue-list-item-row">
                  <strong>{item.issue_number}</strong>
                  <span className={`status-pill issue-state-pill state-${issueStateClass(item.state)}`}>
                    {item.state ?? "New"}
                  </span>
                </div>
              </button>
            ))}
            {items.length === 0 ? (
              <div className="empty-state">No issues found for {selectedDirectoryPath}.</div>
            ) : null}
          </div>
        </section>

        <section className="feature-content">
          <div className="feature-toolbar">
            <div>
              <h2>
                {creating
                  ? "Create issue"
                  : editing
                    ? `Edit ${selectedIssue?.issue_number ?? "issue"}`
                    : (selectedIssue?.title ?? "Issues")}
              </h2>
              <p className="muted-text">
                Create local issues, import YAML-backed issues, and inspect issue records without leaving the admin
                surface.
              </p>
            </div>
            {!showingForm && selectedIssue ? (
              <div className="feature-toolbar-actions icon-actions">
                {selectedIssue.can_create_vm ? (
                  <Tooltip
                    content={
                      creatingVm
                        ? "Close the inline VM creation panel for this issue."
                        : "Create a VM for the selected issue using the configured bootstrap options."
                    }
                  >
                    <button
                      type="button"
                      onClick={() => {
                        setCreatingVm((current) => !current);
                        setVmOptions({ ...EMPTY_VM_OPTIONS });
                        setValidation(null);
                      }}
                      disabled={mutationLocked}
                      aria-label={creatingVm ? "Cancel VM creation" : "Create VM"}
                    >
                      <CreateVmIcon />
                    </button>
                  </Tooltip>
                ) : null}
                {selectedIssue.can_edit ? (
                  <Tooltip content="Edit the selected local issue record and save changes back to disk.">
                    <button
                      type="button"
                      onClick={() => {
                        setEditing(true);
                        setCreating(false);
                        setCreatingVm(false);
                        setValidation(null);
                      }}
                      disabled={mutationLocked}
                      aria-label="Edit issue"
                    >
                      <EditIcon />
                    </button>
                  </Tooltip>
                ) : null}
                {selectedIssue.can_delete ? (
                  <Tooltip content="Delete the selected local issue record from the host-side store.">
                    <button
                      className="danger"
                      type="button"
                      onClick={() => void handleDeleteIssue()}
                      disabled={busy === "delete" || mutationLocked}
                      aria-label="Delete issue"
                    >
                      <DeleteIcon />
                    </button>
                  </Tooltip>
                ) : null}
              </div>
            ) : null}
          </div>
          {error ? <div className="callout error-callout">{error}</div> : null}
          {status ? <div className="callout success-callout">{status}</div> : null}
          {validation ? <div className="callout error-callout">{validation}</div> : null}
          {showingForm ? (
            <article className="panel section-panel">
              <div className="panel-header">
                <div>
                  <h3>{creating ? "Issue draft" : `Editing ${selectedIssue?.issue_number ?? "issue"}`}</h3>
                  <p className="muted-text">
                    {creating
                      ? "Choose whether to create a new local issue or import an existing YAML file."
                      : "Update the local issue record and save it back to disk."}
                  </p>
                </div>
                <span className="panel-meta">{creating ? form.mode : activeIssueState}</span>
              </div>
              <div className="config-field-grid">
                {creating ? (
                  <div className="config-field-card">
                    <div className="feature-list-title-row">
                      <strong>Source</strong>
                      <span className="panel-meta">Creation mode</span>
                    </div>
                    <label className="checkbox-row">
                      <input
                        type="radio"
                        checked={form.mode === "manual"}
                        onChange={() => setForm((current) => ({ ...current, mode: "manual" }))}
                        disabled={mutationLocked}
                      />
                      <span>Create local issue from form</span>
                    </label>
                    <label className="checkbox-row">
                      <input
                        type="radio"
                        checked={form.mode === "import"}
                        onChange={() => setForm((current) => ({ ...current, mode: "import" }))}
                        disabled={mutationLocked}
                      />
                      <span>Import existing YAML</span>
                    </label>
                  </div>
                ) : null}

                <div className="config-field-card">
                  <div className="feature-list-title-row">
                    <strong>Issue details</strong>
                    <span className="panel-meta">{creating ? "Required fields" : "Editable metadata"}</span>
                  </div>
                  <label className="field-stack">
                    <span>Title</span>
                    <input
                      value={form.title}
                      onChange={(event) => setForm((current) => ({ ...current, title: event.target.value }))}
                      disabled={mutationLocked || (form.mode === "import" && creating)}
                    />
                  </label>
                  <label className="field-stack">
                    <span>Repository source to register</span>
                    <input
                      value={form.repoSource}
                      onChange={(event) => setForm((current) => ({ ...current, repoSource: event.target.value }))}
                      placeholder="Git URL or local checkout (transient)"
                      disabled={mutationLocked}
                    />
                  </label>
                  <label className="field-stack">
                    <span>Managed repository ID</span>
                    <input
                      value={form.managedRepoId}
                      onChange={(event) => setForm((current) => ({ ...current, managedRepoId: event.target.value }))}
                      placeholder="mr_..."
                      disabled={mutationLocked || Boolean(form.repoSource)}
                    />
                  </label>
                  {creating && form.mode === "import" ? (
                    <label className="field-stack">
                      <span>Import YAML</span>
                      <input
                        value={form.yamlPath}
                        onChange={(event) => setForm((current) => ({ ...current, yamlPath: event.target.value }))}
                        placeholder="/path/to/issue.yaml"
                        disabled={mutationLocked}
                      />
                    </label>
                  ) : null}
                  {!creating ? (
                    <label className="field-stack">
                      <span>State</span>
                      <select
                        value={form.state}
                        onChange={(event) => setForm((current) => ({ ...current, state: event.target.value }))}
                        disabled={mutationLocked}
                      >
                        {ISSUE_STATES.map((state) => (
                          <option key={state} value={state}>
                            {state}
                          </option>
                        ))}
                      </select>
                    </label>
                  ) : null}
                  <label className="field-stack">
                    <span>Body</span>
                    <textarea
                      rows={12}
                      value={form.body}
                      onChange={(event) => setForm((current) => ({ ...current, body: event.target.value }))}
                      disabled={mutationLocked || (form.mode === "import" && creating)}
                    />
                  </label>
                </div>

                {creating ? (
                  <div className="config-field-card">
                    <div className="feature-list-title-row">
                      <strong>VM bootstrap</strong>
                      <span className="panel-meta">Optional</span>
                    </div>
                    <label className="checkbox-row">
                      <input
                        type="checkbox"
                        checked={form.startVm}
                        onChange={(event) => setForm((current) => ({ ...current, startVm: event.target.checked }))}
                        disabled={mutationLocked}
                      />
                      <span>Create and start VM immediately</span>
                    </label>
                    {form.startVm ? (
                      <VmOptionsEditor
                        value={form.vmOptions}
                        onChange={(next) => setForm((current) => ({ ...current, vmOptions: next }))}
                        disabled={mutationLocked}
                      />
                    ) : null}
                  </div>
                ) : null}
              </div>
              <div className="feature-toolbar-actions">
                <button
                  className="primary"
                  type="button"
                  disabled={busy !== null || mutationLocked}
                  onClick={() => void (creating ? handleCreateIssue() : handleSaveIssue())}
                >
                  {busy === "create" || busy === "save" ? "Saving..." : creating ? "Create issue" : "Save changes"}
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setCreating(false);
                    setEditing(false);
                    setValidation(null);
                    if (selectedIssue) {
                      setForm(issueFormFromDetail(selectedIssue));
                    }
                  }}
                >
                  Cancel
                </button>
              </div>
            </article>
          ) : selectedIssue ? (
            <div className="feature-grid issues-detail-grid">
              <div className="panel detail-panel">
                <div className="panel-heading">
                  <div>
                    <div className="section-label">
                      {selectedIssueRecord ? `Selected: ${selectedIssueRecord.issue_number}` : "Issue"}
                    </div>
                    <h2>{selectedIssue.title}</h2>
                  </div>
                  <span className="status-pill">{activeIssueState}</span>
                </div>
                <div className="detail-stack">
                  <div className="detail-grid">
                    <div>
                      <span>Issue</span>
                      <strong>{selectedIssue.issue_number}</strong>
                    </div>
                    <div>
                      <span>Source</span>
                      <strong>{issueSourceLabel(selectedIssue.source_type)}</strong>
                    </div>
                    <div>
                      <span>Updated</span>
                      <strong>{formatTimestamp(selectedIssue.updated_at)}</strong>
                    </div>
                    <div>
                      <span>Managed repository</span>
                      <Tooltip
                        content={selectedIssue.managed_repo_id ?? "No managed repository is bound to this issue."}
                      >
                        <strong className="issue-meta-value issue-path-value">
                          {selectedIssue.managed_repo_id ?? "None"}
                        </strong>
                      </Tooltip>
                    </div>
                    <div>
                      <span>Directory</span>
                      <Tooltip content={`Directory scope: ${selectedIssue.directory_path ?? "root"}`}>
                        <strong className="issue-meta-value issue-path-value">
                          {selectedIssue.directory_path ?? "root"}
                        </strong>
                      </Tooltip>
                    </div>
                    <div>
                      <span>YAML path</span>
                      <Tooltip
                        content={selectedIssue.yaml_path ?? "This issue does not currently have a YAML file path."}
                      >
                        <strong className="issue-meta-value issue-path-value">
                          {selectedIssue.yaml_path ?? "No file"}
                        </strong>
                      </Tooltip>
                    </div>
                  </div>

                  {creatingVm ? (
                    <div className="detail-block">
                      <div className="section-label">Create VM</div>
                      <VmOptionsEditor value={vmOptions} onChange={setVmOptions} disabled={mutationLocked} />
                      <div className="inline-actions">
                        <button
                          className="primary"
                          type="button"
                          onClick={() => void handleCreateVm()}
                          disabled={busy === "create-vm" || mutationLocked}
                        >
                          {busy === "create-vm" ? "Creating..." : "Create VM"}
                        </button>
                      </div>
                    </div>
                  ) : null}
                </div>
              </div>

              <div className="panel section-panel issue-summary-panel">
                <div className="panel-heading">
                  <div>
                    <div className="section-label">Summary</div>
                    <h2>Issue summary</h2>
                  </div>
                </div>
                <div className="issue-copy-block issue-body-block">
                  {selectedIssueBodyText || issueSummaryText(selectedIssue) || "No summary available."}
                </div>
                {selectedIssueMetadata.length > 0 ? (
                  <dl className="property-grid vm-label-grid issue-metadata-list">
                    {selectedIssueMetadata.map((entry) => (
                      <div key={entry.label}>
                        <dt>{entry.label}</dt>
                        <dd>{renderIssueMetadataValue(entry.value)}</dd>
                      </div>
                    ))}
                  </dl>
                ) : null}
              </div>
            </div>
          ) : (
            <div className="empty-state">Select an issue or create a new one.</div>
          )}
        </section>
      </div>
    </RecordWorkspace>
  );
}

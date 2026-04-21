import { useCallback, useEffect, useMemo, useState } from "react";

import { fetchJson, formatTimestamp } from "../admin/api";
import { useAdminUi } from "../admin/AdminContext";
import { type IssueCreateResponse, type IssueDetail, type IssueListResponse, type IssueRecord, type IssueYamlResponse, type VmActionResponse } from "../admin/types";

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
  state: "New",
  body: "",
  yamlPath: "",
  startVm: false,
  vmOptions: EMPTY_VM_OPTIONS,
};

const REFRESH_MS = 10000;
const ISSUE_SELECTION_STORAGE_KEY = "admin-web:issues:selected-issue";

function issueSummaryText(issue: IssueDetail): string {
  const lines = [
    `Issue: ${issue.issue_number}`,
    `Title: ${issue.title}`,
    `State: ${issue.state ?? ""}`,
    `Directory: ${issue.directory_path ?? "root"}`,
    `Repo source: ${issue.repo_source ?? ""}`,
    `YAML: ${issue.yaml_path ?? ""}`,
  ];
  if (issue.reason) {
    lines.push(`Reason: ${issue.reason}`);
  }
  const description = String(issue.description ?? issue.body ?? "").trim();
  if (description) {
    lines.push("", "Description:", description);
  }
  if (issue.metadata && Object.keys(issue.metadata).length > 0) {
    lines.push("", "Metadata:", JSON.stringify(issue.metadata, null, 2));
  }
  return lines.join("\n");
}

function issueFormFromDetail(issue: IssueDetail): IssueFormState {
  return {
    mode: "manual",
    title: issue.title,
    repoSource: issue.repo_source ?? "",
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
          <span>NuGet target</span>
          <input
            value={value.nuget}
            onChange={(event) => onChange({ ...value, nuget: event.target.value })}
            placeholder="/path/to/project-or-solution.slnx"
            disabled={disabled || !value.nugetEnabled}
          />
        </label>
        <label className="field-stack">
          <span>MSSQL init root</span>
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
          <span>Bootstrap .NET</span>
        </label>
        <label className="checkbox-row">
          <input
            type="checkbox"
            checked={value.nugetEnabled}
            onChange={(event) => onChange({ ...value, nugetEnabled: event.target.checked, nuget: event.target.checked ? value.nuget : "" })}
            disabled={disabled}
          />
          <span>Bootstrap NuGet</span>
        </label>
        <label className="checkbox-row">
          <input
            type="checkbox"
            checked={value.playwright}
            onChange={(event) => onChange({ ...value, playwright: event.target.checked })}
            disabled={disabled}
          />
          <span>Bootstrap Playwright</span>
        </label>
        <label className="checkbox-row">
          <input type="checkbox" checked={value.mssql} onChange={(event) => onChange({ ...value, mssql: event.target.checked })} disabled={disabled} />
          <span>Bootstrap MSSQL</span>
        </label>
      </div>
    </>
  );
}

export function IssuesPage() {
  const { privilegedUnlocked, selectedDirectoryPath } = useAdminUi();
  const [stateFilter, setStateFilter] = useState("active");
  const [directOnly, setDirectOnly] = useState(true);
  const [items, setItems] = useState<IssueRecord[]>([]);
  const [selectedIssueId, setSelectedIssueId] = useState<string>("");
  const [selectedIssue, setSelectedIssue] = useState<IssueDetail | null>(null);
  const [yamlPayload, setYamlPayload] = useState<IssueYamlResponse | null>(null);
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
    const saved = window.localStorage.getItem(ISSUE_SELECTION_STORAGE_KEY);
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
  const mutationLocked = !privilegedUnlocked;

  const refreshList = useCallback(async () => {
    const query = new URLSearchParams({
      state_filter: stateFilter,
      directory_path: selectedDirectoryPath,
      direct_only: String(directOnly),
    });
    const payload = await fetchJson<IssueListResponse>(`/api/admin/issues?${query.toString()}`);
    setItems(payload.items);
    setSelectedIssueId((current) => {
      if (current && payload.items.some((item) => item.issue_id === current)) {
        return current;
      }
      return payload.items[0]?.issue_id ?? "";
    });
  }, [stateFilter, selectedDirectoryPath, directOnly]);

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
      setYamlPayload(null);
      return;
    }
    let cancelled = false;
    setYamlPayload(null);
    fetchJson<IssueDetail>(`/api/admin/issues/${encodeURIComponent(selectedIssueId)}`)
      .then(async (issuePayload) => {
        if (cancelled) return;
        setSelectedIssue(issuePayload);
        if (!editing) {
          setForm(issueFormFromDetail(issuePayload));
        }
        try {
          const yaml = await fetchJson<IssueYamlResponse>(`/api/admin/issues/${encodeURIComponent(selectedIssueId)}/yaml`);
          if (!cancelled) {
            setYamlPayload(yaml);
            setError(null);
          }
        } catch (reason) {
          if (!cancelled) {
            setYamlPayload(null);
            setError(null);
          }
        }
      })
      .catch((reason) => {
        if (cancelled) return;
        setSelectedIssue(null);
        setYamlPayload(null);
        setError(reason instanceof Error ? reason.message : String(reason));
      });
    return () => {
      cancelled = true;
    };
  }, [selectedIssueId, editing]);

  async function handleManualRefresh() {
    try {
      await refreshList();
      setStatus("Refreshed issue list.");
      setError(null);
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

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
      setYamlPayload(null);
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
  const issueCountLabel = `${items.length} ${items.length === 1 ? "issue" : "issues"}`;
  const activeIssueState = selectedIssue?.state ?? selectedIssueRecord?.state ?? "New";

  return (
    <div className="feature-page">
      <section className="feature-sidebar">
        <div className="feature-sidebar-header">
          <div>
            <div className="eyebrow">Local issues</div>
            <h1>Issues</h1>
          </div>
          <div className="inline-actions">
            <button
              type="button"
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
            <button type="button" onClick={() => void handleManualRefresh()}>
              Refresh
            </button>
          </div>
        </div>
        {mutationLocked ? (
          <div className="callout warning-callout">Unlock privileged operations to create, edit, delete, or launch work from issues.</div>
        ) : null}
        <div className="toolbar-grid">
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
          <div className="panel section-panel">
            <div className="feature-list-title-row">
              <strong>Directory scope</strong>
              <span className="panel-meta">{selectedDirectoryPath}</span>
            </div>
            <div className="feature-list-meta">{directOnly ? "Direct matches only" : "Including descendants"}</div>
          </div>
          <label className="checkbox-row">
            <input type="checkbox" checked={directOnly} onChange={(event) => setDirectOnly(event.target.checked)} />
            <span>Direct only</span>
          </label>
        </div>
        <div className="panel section-panel">
          <div className="feature-list-title-row">
            <strong>Queue</strong>
            <span className="panel-meta">{issueCountLabel}</span>
          </div>
          <div className="feature-list-meta">Directory scope: {selectedDirectoryPath}</div>
          <div className="feature-list-meta">
            View: {stateFilter === "active" ? "Active issues" : stateFilter}
            {directOnly ? " · direct only" : " · including descendants"}
          </div>
        </div>
        <div className="feature-list">
          {items.map((item) => (
            <button
              key={item.issue_id}
              type="button"
              className={`feature-list-item${item.issue_id === selectedIssueId ? " selected" : ""}`}
              onClick={() => {
                setSelectedIssueId(item.issue_id);
                setCreating(false);
                setEditing(false);
                setCreatingVm(false);
                setValidation(null);
              }}
            >
              <div className="feature-list-title-row">
                <strong>{item.issue_number}</strong>
                <span>{item.state ?? "New"}</span>
              </div>
              <div>{item.title}</div>
              <div className="feature-list-meta">{item.repo_source ?? "No repo source"}</div>
            </button>
          ))}
          {items.length === 0 ? <div className="empty-state">No issues found for the selected directory scope.</div> : null}
        </div>
      </section>

      <section className="feature-content">
        <div className="feature-toolbar">
          <div>
            <h2>{creating ? "Create issue" : editing ? `Edit ${selectedIssue?.issue_number ?? "issue"}` : selectedIssue?.title ?? "Issues"}</h2>
            <p className="muted-text">
              Create local issues, import YAML-backed issues, and launch issue VMs without leaving the admin surface.
            </p>
          </div>
          {!showingForm && selectedIssue ? (
            <div className="feature-toolbar-actions">
              {selectedIssue.can_create_vm ? (
                <button
                  type="button"
                  onClick={() => {
                    setCreatingVm((current) => !current);
                    setVmOptions({ ...EMPTY_VM_OPTIONS });
                    setValidation(null);
                  }}
                  disabled={mutationLocked}
                >
                  {creatingVm ? "Cancel VM" : "Create VM"}
                </button>
              ) : null}
              {selectedIssue.can_edit ? (
                <button
                  type="button"
                  onClick={() => {
                    setEditing(true);
                    setCreating(false);
                    setCreatingVm(false);
                    setValidation(null);
                  }}
                  disabled={mutationLocked}
                >
                  Edit
                </button>
              ) : null}
              {selectedIssue.can_delete ? (
                <button className="danger" type="button" onClick={() => void handleDeleteIssue()} disabled={busy === "delete" || mutationLocked}>
                  Delete
                </button>
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
                  {creating ? "Choose whether to create a new local issue or import an existing YAML file." : "Update the local issue record and save it back to disk."}
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
                  <span>Repo source</span>
                  <input value={form.repoSource} onChange={(event) => setForm((current) => ({ ...current, repoSource: event.target.value }))} disabled={mutationLocked} />
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
                    <select value={form.state} onChange={(event) => setForm((current) => ({ ...current, state: event.target.value }))} disabled={mutationLocked}>
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
              <button className="primary" type="button" disabled={busy !== null || mutationLocked} onClick={() => void (creating ? handleCreateIssue() : handleSaveIssue())}>
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
          <div className="feature-grid">
            <div className="panel detail-panel">
              <div className="panel-heading">
                <div>
                  <div className="section-label">{selectedIssueRecord ? `Selected: ${selectedIssueRecord.issue_number}` : "Issue"}</div>
                  <h2>{selectedIssue.title}</h2>
                </div>
                <span className="status-pill">{activeIssueState}</span>
              </div>
              <div className="detail-stack">
                <div className="detail-grid">
                  <div><span>Issue</span><strong>{selectedIssue.issue_number}</strong></div>
                  <div><span>Source type</span><strong>{selectedIssue.source_type ?? "unknown"}</strong></div>
                  <div><span>Updated</span><strong>{formatTimestamp(selectedIssue.updated_at)}</strong></div>
                  <div><span>Repo source</span><strong>{selectedIssue.repo_source ?? "None"}</strong></div>
                  <div><span>Directory</span><strong>{selectedIssue.directory_path ?? "root"}</strong></div>
                  <div><span>YAML path</span><strong>{selectedIssue.yaml_path ?? "No file"}</strong></div>
                </div>

                <div className="detail-block">
                  <div className="section-label">Summary</div>
                  <pre>{issueSummaryText(selectedIssue)}</pre>
                </div>

                {creatingVm ? (
                  <div className="detail-block">
                    <div className="section-label">Create VM</div>
                    <VmOptionsEditor value={vmOptions} onChange={setVmOptions} disabled={mutationLocked} />
                    <div className="inline-actions">
                      <button className="primary" type="button" onClick={() => void handleCreateVm()} disabled={busy === "create-vm" || mutationLocked}>
                        {busy === "create-vm" ? "Creating..." : "Create VM"}
                      </button>
                    </div>
                  </div>
                ) : null}
              </div>
            </div>

            <div className="detail-stack">
              <div className="detail-block">
                <div className="detail-block-heading">
                  <div>
                    <div className="section-label">YAML</div>
                    <strong>{yamlPayload?.issue_number ?? selectedIssue.issue_number}</strong>
                  </div>
                  <span className="panel-meta">{yamlPayload?.path ?? "No file"}</span>
                </div>
                <pre>{yamlPayload?.yaml_text ?? "No YAML available."}</pre>
              </div>

              <div className="detail-block">
                <div className="section-label">Body</div>
                <pre>{selectedIssue.body?.trim() ? selectedIssue.body : "No body content."}</pre>
              </div>
            </div>

            <div className="panel section-panel">
              <div className="panel-heading">
                <div>
                  <div className="section-label">Actions</div>
                  <h2>Issue operations</h2>
                </div>
                <span>{activeIssueState}</span>
              </div>
              <div className="feature-list-meta">
                Use edit for metadata changes, create VM to launch work from this issue, or delete to remove the local issue record.
              </div>
              <div className="feature-toolbar-actions">
                {selectedIssue.can_create_vm ? (
                  <button
                    type="button"
                    onClick={() => {
                      setCreatingVm((current) => !current);
                      setVmOptions({ ...EMPTY_VM_OPTIONS });
                      setEditing(false);
                      setValidation(null);
                    }}
                    disabled={mutationLocked}
                  >
                    {creatingVm ? "Cancel VM" : "Create VM"}
                  </button>
                ) : null}
                {selectedIssue.can_edit ? (
                  <button
                    type="button"
                    onClick={() => {
                      setEditing(true);
                      setValidation(null);
                    }}
                    disabled={mutationLocked}
                  >
                    Edit issue
                  </button>
                ) : null}
                {selectedIssue.can_delete ? (
                  <button className="danger" type="button" onClick={() => void handleDeleteIssue()} disabled={busy === "delete" || mutationLocked}>
                    Delete issue
                  </button>
                ) : null}
              </div>
            </div>
          </div>
        ) : (
          <div className="empty-state">Select an issue or create a new one.</div>
        )}
      </section>
    </div>
  );
}

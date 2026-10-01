import { useEffect, useRef, useState } from "react";
import { fetchJson, formatTimestamp } from "../admin/api";

type Settings = {
  enabled: boolean; worker_id: string | null; image: string; tracked_ref: string;
  refresh_on_git_change: boolean; refresh_interval_seconds: number; worker_cpus: number; semantic: boolean; scip: boolean;
  includes: string[]; excludes: string[]; dotnet_projects: string[]; typescript_projects: string[];
  timeout_seconds: number; retention_days: number; max_file_bytes: number;
  npm_restore: "locked" | "install";
};
type Build = {
  id: string; sha: string; status: string; created: string;
  coverage: { lexical?: string; semantic?: string; navigation?: string; documents?: number; chunks?: number; projects?: {path: string; status: string}[] };
  diagnostics: string[];
  metrics?: { compiler_submission_pending?: boolean };
};
type BuildFilter = "ready" | "downloadable" | "all";
type Status = {
  revision: number; settings: Settings; builds: Build[]; status: string;
  builds_total: number; builds_limit: number; builds_offset: number; build_filter: BuildFilter;
};

const BUILD_PAGE_SIZE = 20;

export function RepositoryIndexPanel({ repositoryId, privilegedUnlocked }: { repositoryId: string; privilegedUnlocked: boolean }) {
  const [status, setStatus] = useState<Status | null>(null);
  const [form, setForm] = useState<Settings | null>(null);
  const [revision, setRevision] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [commit, setCommit] = useState("");
  const [includeEmbeddings, setIncludeEmbeddings] = useState(false);
  const [buildFilter, setBuildFilter] = useState<BuildFilter>("ready");
  const [buildOffset, setBuildOffset] = useState(0);
  const initialized = useRef(false);
  const base = `/api/admin/git-repositories/${encodeURIComponent(repositoryId)}/indexing`;
  const statusUrl = (filter = buildFilter, offset = buildOffset) =>
    `${base}?build_filter=${filter}&limit=${BUILD_PAGE_SIZE}&offset=${offset}`;

  useEffect(() => {
    const controller = new AbortController();
    const poll = async () => {
      try {
        const value = await fetchJson<Status>(statusUrl(), { signal: controller.signal });
        if (controller.signal.aborted) return;
        const lastOffset = value.builds_total > 0
          ? Math.floor((value.builds_total - 1) / BUILD_PAGE_SIZE) * BUILD_PAGE_SIZE
          : 0;
        if (buildOffset > lastOffset) {
          setBuildOffset(lastOffset);
          return;
        }
        setStatus(value);
        if (!initialized.current) {
          setForm(value.settings);
          setRevision(value.revision);
          initialized.current = true;
        }
      } catch (e) { if (!controller.signal.aborted) setError(String(e)); }
    };
    void poll();
    const timer = window.setInterval(() => void poll(), 5000);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, [base, buildFilter, buildOffset]);

  async function reload(filter = buildFilter, offset = buildOffset) {
    const value = await fetchJson<Status>(statusUrl(filter, offset));
    setStatus(value); setForm(value.settings); setRevision(value.revision);
  }
  async function act(operation: string) {
    setBusy(true); setError(null);
    try {
      if (operation === "save") {
        const settings = form && { ...form, ...Object.fromEntries((["includes", "excludes", "dotnet_projects", "typescript_projects"] as const).map(key => [key, form[key].map(value => value.trim()).filter(Boolean)])) };
        await fetchJson(base, { method: "PATCH", body: JSON.stringify({ expected_revision: revision, settings }) });
      } else if (operation === "build" || operation === "rebuild") {
        await fetchJson(`${base}/builds`, { method: "POST", body: JSON.stringify({ commit_sha: commit.trim() || null, rebuild: operation === "rebuild" }) });
        setBuildFilter("all");
        setBuildOffset(0);
        await reload("all", 0);
        return;
      } else if (operation !== "reload") {
        await fetchJson(`${base}/builds/${encodeURIComponent(operation)}/cancel`, { method: "POST", body: "{}" });
      }
      await reload();
    } catch (e) { setError(String(e)); } finally { setBusy(false); }
  }
  function update<K extends keyof Settings>(key: K, value: Settings[K]) { setForm(current => current ? { ...current, [key]: value } : current); }
  const dirty = !!form && JSON.stringify(form) !== JSON.stringify(status?.settings);
  const locked = busy || !privilegedUnlocked;
  const displayedBuildFilter = status?.build_filter ?? buildFilter;
  const displayedBuildOffset = status?.builds_offset ?? buildOffset;
  const buildsTotal = status?.builds_total ?? 0;
  const pageStart = buildsTotal > 0 ? displayedBuildOffset + 1 : 0;
  const pageEnd = Math.min(displayedBuildOffset + (status?.builds.length ?? 0), buildsTotal);
  const hasPreviousPage = displayedBuildOffset > 0;
  const hasNextPage = pageEnd < buildsTotal;
  return <article className="panel managed-git-index-panel">
    <div className="panel-heading"><h3>Repository indexing</h3><span className="status-pill">{status?.status ?? "Loading"}</span></div>
    <p>Search source and documentation, navigate symbols, and export an index for external tools.</p>
    {error && <p role="alert" className="error-message">{error}</p>}
    {form && <>
      <details className="compact-disclosure">
        <summary>Indexing settings{dirty ? " · Unsaved changes" : ""}</summary>
      <fieldset disabled={locked}>
        <label className="checkbox-row"><input type="checkbox" checked={form.enabled} onChange={e => update("enabled", e.target.checked)} /> Enable indexing</label>
        <div className="form-grid">
          <label className="field-stack">Tracked branch<input value={form.tracked_ref} onChange={e => update("tracked_ref", e.target.value)} /></label>
          <label className="field-stack">Worker ID (empty uses directory default)<input value={form.worker_id ?? ""} onChange={e => update("worker_id", e.target.value || null)} /></label>
          <label className="field-stack">Indexing image alias<input value={form.image} onChange={e => update("image", e.target.value)} /></label>
          <label className="field-stack">Build timeout (seconds)<input type="number" min={60} max={14400} value={form.timeout_seconds} onChange={e => update("timeout_seconds", Number(e.target.value))} /></label>
          <label className="field-stack">Indexing CPU limit<input type="number" min={0.1} max={4} step={0.1} value={form.worker_cpus} onChange={e => update("worker_cpus", Number(e.target.value))} /></label>
          <label className="field-stack">Minimum automatic refresh interval (seconds)<input type="number" min={300} max={604800} value={form.refresh_interval_seconds} onChange={e => update("refresh_interval_seconds", Number(e.target.value))} /></label>
          <label className="field-stack">Unreferenced snapshot retention (days)<input type="number" min={1} max={365} value={form.retention_days} onChange={e => update("retention_days", Number(e.target.value))} /></label>
          {form.scip && <label className="field-stack">JavaScript dependencies<select value={form.npm_restore} onChange={e => update("npm_restore", e.target.value as Settings["npm_restore"])}><option value="locked">Require package lock</option><option value="install">Resolve when lock is missing</option></select></label>}
        </div>
        <label className="checkbox-row"><input type="checkbox" checked={form.semantic} onChange={e => update("semantic", e.target.checked)} /> Search by meaning using the configured embedding provider</label>
        <label className="checkbox-row"><input type="checkbox" checked={form.scip} onChange={e => update("scip", e.target.checked)} /> SCIP symbol navigation</label>
        <p>Enable definitions, references and implementations for C# and TypeScript. Requires the corresponding language tools and project dependencies on the indexing worker.</p>
        <label className="checkbox-row"><input type="checkbox" checked={form.refresh_on_git_change} onChange={e => update("refresh_on_git_change", e.target.checked)} /> Refresh the shared branch index at the configured interval</label>
        <p>Teams use existing indexes without starting compiler builds. Branch changes are searched in the checkout. One compiler indexing job can run at a time.</p>
        {([
          ["includes", "Include files (one pattern per line)"], ["excludes", "Exclude files (one pattern per line)"],
          ["dotnet_projects", ".NET project paths (empty discovers tracked projects)"], ["typescript_projects", "TypeScript configuration paths (empty discovers tracked configurations)"],
        ] as const).filter(([key]) => form.scip || key === "includes" || key === "excludes").map(([key, label]) => <label className="field-stack" key={key}>{label}<textarea rows={3} value={form[key].join("\n")} onChange={e => update(key, e.target.value.split("\n"))} /></label>)}
      </fieldset>
      <div className="feature-toolbar">
        <button className="admin-action-button" type="button" disabled={locked || !dirty} onClick={() => void act("save")}>Save indexing settings</button>
        <button className="admin-action-button" type="button" disabled={busy} onClick={() => void act("reload")}>Reload settings</button>
      </div>
      </details>
      <label className="field-stack">Commit to index (empty uses tracked branch)<input value={commit} onChange={e => setCommit(e.target.value)} placeholder="Exact Git SHA" /></label>
      <div className="feature-toolbar">
        <button className="admin-action-button" type="button" disabled={locked || dirty || !status?.settings.enabled} onClick={() => void act("build")}>Build index</button>
        <button className="admin-action-button" type="button" disabled={locked || dirty || !status?.settings.enabled} onClick={() => void act("rebuild")}>Rebuild index</button>
      </div>
      {dirty && <p>Save or reload settings before starting a build.</p>}
    </>}
    <div className="panel-header managed-git-index-history-heading">
      <div>
        <h4>Index snapshots</h4>
        <p className="muted-text">Newest first. Ready snapshots are shown by default.</p>
      </div>
      <label className="field-stack">Show
        <select value={buildFilter} onChange={e => { setBuildFilter(e.target.value as BuildFilter); setBuildOffset(0); }}>
          <option value="ready">Ready to download</option>
          <option value="downloadable">Ready and partial</option>
          <option value="all">All builds</option>
        </select>
      </label>
    </div>
    <label className="checkbox-row"><input type="checkbox" checked={includeEmbeddings} onChange={e => setIncludeEmbeddings(e.target.checked)} /> Include embeddings in exports</label>
    {status && status.builds.length === 0 ? <p className="empty-state">No index snapshots match this filter.</p> : null}
    {status?.builds.map((build, index) => <section key={build.id} className="detail-block managed-git-index-build">
      <div className="detail-block-heading managed-git-index-build-heading">
        <p><code>{build.sha.slice(0, 12)}</code> · {build.status} · {build.coverage.documents ?? 0} files</p>
        {displayedBuildOffset === 0 && index === 0 ? <span className="status-pill">{displayedBuildFilter === "ready" ? "Newest ready" : "Newest"}</span> : null}
      </div>
      <dl className="managed-git-index-build-meta">
        <div><dt>Created</dt><dd>{formatTimestamp(build.created)}</dd></div>
        <div><dt>Build ID</dt><dd><code>{build.id}</code></dd></div>
        <div><dt>Commit</dt><dd><code>{build.sha}</code></dd></div>
      </dl>
      <p>Text: {build.coverage.lexical ?? "pending"} · Meaning: {build.coverage.semantic ?? "pending"} · Navigation: {build.coverage.navigation ?? "pending"}</p>
      {["ready", "partial"].includes(build.status) && <a href={`${base}/builds/${encodeURIComponent(build.id)}/export?include_embeddings=${includeEmbeddings}`} download>Download portable index</a>}
      {(["queued", "running", "importing"].includes(build.status) || build.metrics?.compiler_submission_pending) && <button className="admin-action-button" type="button" disabled={locked} onClick={() => void act(build.id)}>Cancel build</button>}
      {(build.diagnostics.length > 0 || build.coverage.projects?.length) ? <details><summary>Coverage and diagnostics</summary>
        <ul>{build.diagnostics.map((message, i) => <li key={i}>{message}</li>)}{build.coverage.projects?.map(project => <li key={project.path}>{project.path}: {project.status}</li>)}</ul>
      </details> : null}
    </section>)}
    <div className="feature-toolbar managed-git-index-pagination" aria-label="Index snapshot pages">
      <p>{pageStart}–{pageEnd} of {buildsTotal}</p>
      <div className="feature-toolbar-actions">
        <button className="admin-action-button" type="button" disabled={!hasPreviousPage} onClick={() => setBuildOffset(Math.max(0, displayedBuildOffset - BUILD_PAGE_SIZE))}>Previous</button>
        <button className="admin-action-button" type="button" disabled={!hasNextPage} onClick={() => setBuildOffset(displayedBuildOffset + BUILD_PAGE_SIZE)}>Next</button>
      </div>
    </div>
  </article>;
}

import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { RepositoryIndexPanel } from "./RepositoryIndexPanel";

import { fetchJson, formatTimestamp } from "../admin/api";
import { RecordWorkspace } from "../admin/RecordWorkspace";
import { timestampSortValue } from "../admin/tableSorting";
import { readAdminHashQuery } from "../admin/hash";

type ManagedGitBinding = {
  kind: string;
  binding_id: string;
  bound_at?: string | null;
};

type ManagedGitRepository = {
  managed_repo_id: string;
  directory_path: string;
  canonical_repository_identity?: string | null;
  canonical_repository_url?: string | null;
  provider?: string | null;
  display_name?: string | null;
  default_branch?: string | null;
  host_commit_identity?: {
    name?: string | null;
    email?: string | null;
  } | null;
  created_at?: string | null;
  updated_at?: string | null;
  refreshed_at?: string | null;
  maintenance_at?: string | null;
  bindings?: ManagedGitBinding[];
};

type ManagedGitRepositoryListResponse = {
  repositories: ManagedGitRepository[];
};

type ManagedGitRepositoryResponse = {
  repository: ManagedGitRepository;
};

type ManagedGitBindingGroup = {
  kind: string;
  bindings: ManagedGitBinding[];
};

function repositoryLabel(repository: ManagedGitRepository): string {
  return repository.display_name?.trim() || repository.managed_repo_id;
}

function providerLabel(provider: string | null | undefined): string {
  const normalized = provider?.trim().toLocaleLowerCase() ?? "";
  if (normalized === "azure_devops") return "Azure DevOps";
  if (normalized === "github") return "GitHub";
  if (normalized === "local") return "Local";
  return provider?.trim() || "Unknown";
}

function repositorySearchText(repository: ManagedGitRepository): string {
  return [
    repository.managed_repo_id,
    repository.display_name,
    repository.directory_path,
    repository.provider,
    repository.canonical_repository_identity,
    repository.canonical_repository_url,
    repository.default_branch,
  ]
    .filter(Boolean)
    .join(" ")
    .toLocaleLowerCase();
}

function bindingGroups(bindings: ManagedGitBinding[] | undefined): ManagedGitBindingGroup[] {
  const byKind = new Map<string, ManagedGitBinding[]>();
  for (const binding of bindings ?? []) {
    const kind = binding.kind?.trim() || "other";
    const entries = byKind.get(kind) ?? [];
    entries.push(binding);
    byKind.set(kind, entries);
  }
  return Array.from(byKind, ([kind, entries]) => ({
    kind,
    bindings: entries.sort((left, right) => left.binding_id.localeCompare(right.binding_id)),
  })).sort((left, right) => left.kind.localeCompare(right.kind));
}

function hostCommitIdentity(repository: ManagedGitRepository): string {
  const name = repository.host_commit_identity?.name?.trim() ?? "";
  const email = repository.host_commit_identity?.email?.trim() ?? "";
  if (name && email) return `${name} <${email}>`;
  return name || email || "Not configured";
}

function sortRepositories(repositories: ManagedGitRepository[]): ManagedGitRepository[] {
  return [...repositories].sort((left, right) =>
    repositoryLabel(left).localeCompare(repositoryLabel(right), undefined, { sensitivity: "base" }),
  );
}

export function ManagedGitRepositoriesPage({
  selectedDirectoryPath,
  privilegedUnlocked,
}: {
  selectedDirectoryPath: string;
  privilegedUnlocked: boolean;
}) {
  const [repositories, setRepositories] = useState<ManagedGitRepository[]>([]);
  const [selectedRepositoryId, setSelectedRepositoryId] = useState(() => readAdminHashQuery().get("repository_id") ?? "");
  const [searchQuery, setSearchQuery] = useState("");
  const [includeDescendants, setIncludeDescendants] = useState(true);
  const [reloadVersion, setReloadVersion] = useState(0);
  const [loading, setLoading] = useState(true);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [repoSource, setRepoSource] = useState("");
  const [defaultBranch, setDefaultBranch] = useState("");
  const [registerRefresh, setRegisterRefresh] = useState(true);
  const [identityName, setIdentityName] = useState("");
  const [identityEmail, setIdentityEmail] = useState("");
  const activeDirectoryPathRef = useRef(selectedDirectoryPath);
  const loadRequestIdRef = useRef(0);
  activeDirectoryPathRef.current = selectedDirectoryPath;

  function upsertRepository(repository: ManagedGitRepository) {
    setRepositories((current) => {
      const withoutCurrent = current.filter(
        (candidate) => candidate.managed_repo_id !== repository.managed_repo_id,
      );
      return sortRepositories([...withoutCurrent, repository]);
    });
  }

  async function loadRepositories(
    requestedDirectoryPath = selectedDirectoryPath,
    signal?: AbortSignal,
  ) {
    const requestId = ++loadRequestIdRef.current;
    setLoading(true);
    setError(null);
    try {
      const query = new URLSearchParams({
        directory_path: requestedDirectoryPath,
        include_descendants: String(includeDescendants),
      });
      const payload = await fetchJson<ManagedGitRepositoryListResponse>(
        `/api/admin/git-repositories?${query.toString()}`,
        { signal },
      );
      if (
        requestId !== loadRequestIdRef.current ||
        requestedDirectoryPath !== activeDirectoryPathRef.current
      ) {
        return;
      }
      const nextRepositories = sortRepositories(payload.repositories ?? []);
      setRepositories(nextRepositories);
      setSelectedRepositoryId((current) =>
        current && nextRepositories.some((repository) => repository.managed_repo_id === current)
          ? current
          : nextRepositories[0]?.managed_repo_id ?? "",
      );
    } catch (reason: unknown) {
      if (
        signal?.aborted ||
        requestId !== loadRequestIdRef.current ||
        requestedDirectoryPath !== activeDirectoryPathRef.current
      ) {
        return;
      }
      setRepositories([]);
      setSelectedRepositoryId("");
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      if (
        requestId === loadRequestIdRef.current &&
        requestedDirectoryPath === activeDirectoryPathRef.current
      ) {
        setLoading(false);
      }
    }
  }

  useEffect(() => {
    const controller = new AbortController();
    setStatus(null);
    setBusyAction(null);
    void loadRepositories(selectedDirectoryPath, controller.signal);
    return () => {
      controller.abort();
      loadRequestIdRef.current += 1;
    };
  }, [includeDescendants, reloadVersion, selectedDirectoryPath]);

  const normalizedSearch = searchQuery.trim().toLocaleLowerCase();
  const visibleRepositories = useMemo(
    () =>
      repositories.filter(
        (repository) =>
          !normalizedSearch || repositorySearchText(repository).includes(normalizedSearch),
      ),
    [normalizedSearch, repositories],
  );
  const selectedRepository = useMemo(
    () =>
      repositories.find((repository) => repository.managed_repo_id === selectedRepositoryId) ??
      null,
    [repositories, selectedRepositoryId],
  );
  const selectedBindingGroups = useMemo(
    () => bindingGroups(selectedRepository?.bindings),
    [selectedRepository?.bindings],
  );
  const resultLabel = normalizedSearch
    ? `${visibleRepositories.length} of ${repositories.length} repositories`
    : `${repositories.length} ${repositories.length === 1 ? "repository" : "repositories"}`;
  const mutationsLocked = !privilegedUnlocked;

  useEffect(() => {
    setIdentityName(selectedRepository?.host_commit_identity?.name?.trim() ?? "");
    setIdentityEmail(selectedRepository?.host_commit_identity?.email?.trim() ?? "");
  }, [selectedRepository?.managed_repo_id, selectedRepository?.host_commit_identity?.email, selectedRepository?.host_commit_identity?.name]);

  async function registerRepository(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const source = repoSource.trim();
    if (!source) {
      setError("Repository source is required.");
      return;
    }
    setBusyAction("register");
    setError(null);
    setStatus(null);
    try {
      const payload = await fetchJson<ManagedGitRepositoryResponse>("/api/admin/git-repositories", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          directory_path: selectedDirectoryPath,
          repo_source: source,
          default_branch: defaultBranch.trim() || null,
          refresh: registerRefresh,
        }),
      });
      upsertRepository(payload.repository);
      setSelectedRepositoryId(payload.repository.managed_repo_id);
      setRepoSource("");
      setDefaultBranch("");
      setStatus(`Registered ${repositoryLabel(payload.repository)}.`);
      void loadRepositories();
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusyAction(null);
    }
  }

  async function refreshRepository() {
    if (!selectedRepository) return;
    const repository = selectedRepository;
    setBusyAction("refresh");
    setError(null);
    setStatus(null);
    try {
      const payload = await fetchJson<ManagedGitRepositoryResponse>(
        `/api/admin/git-repositories/${encodeURIComponent(repository.managed_repo_id)}/refresh`,
        { method: "POST" },
      );
      upsertRepository(payload.repository);
      setStatus(`Refreshed ${repositoryLabel(payload.repository)} from its upstream.`);
      void loadRepositories();
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusyAction(null);
    }
  }

  async function runMaintenance() {
    if (!selectedRepository) return;
    const repository = selectedRepository;
    setBusyAction("maintenance");
    setError(null);
    setStatus(null);
    try {
      await fetchJson<Record<string, unknown>>("/api/admin/git-repositories/maintenance", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          managed_repo_id: repository.managed_repo_id,
          directory_path: repository.directory_path,
        }),
      });
      setStatus(`Maintenance completed for ${repositoryLabel(repository)}.`);
      void loadRepositories();
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusyAction(null);
    }
  }

  async function saveHostCommitIdentity(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedRepository) return;
    const name = identityName.trim();
    const email = identityEmail.trim();
    if (!name || !email) {
      setError("Host commit name and email are required.");
      return;
    }
    setBusyAction("identity");
    setError(null);
    setStatus(null);
    try {
      const payload = await fetchJson<ManagedGitRepositoryResponse>(
        `/api/admin/git-repositories/${encodeURIComponent(selectedRepository.managed_repo_id)}/host-commit-identity`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name, email }),
        },
      );
      upsertRepository(payload.repository);
      setStatus(`Updated the host commit identity for ${repositoryLabel(payload.repository)}.`);
      void loadRepositories();
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusyAction(null);
    }
  }

  async function deleteRepository() {
    if (!selectedRepository) return;
    const repository = selectedRepository;
    if (!window.confirm(`Delete managed repository ${repositoryLabel(repository)}? Existing bindings prevent deletion.`)) {
      return;
    }
    setBusyAction("delete");
    setError(null);
    setStatus(null);
    try {
      await fetchJson<Record<string, unknown>>(
        `/api/admin/git-repositories/${encodeURIComponent(repository.managed_repo_id)}`,
        { method: "DELETE" },
      );
      setRepositories((current) =>
        current.filter((candidate) => candidate.managed_repo_id !== repository.managed_repo_id),
      );
      setSelectedRepositoryId("");
      setStatus(`Deleted ${repositoryLabel(repository)}.`);
      void loadRepositories();
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusyAction(null);
    }
  }

  return (
    <RecordWorkspace
      recordLabel="repository"
      title="Git repositories"
      description="Compare repository sources, directory scope, and refresh state. Inspect bindings and maintenance on the repository page."
      columns={["Repository", "Provider", "Directory", "Last refreshed"]}
      records={visibleRepositories.map((repository) => ({
        id: repository.managed_repo_id,
        title: repositoryLabel(repository),
        description: repository.canonical_repository_url,
        href: `#git-repositories?${new URLSearchParams({ repository_id: repository.managed_repo_id })}`,
        cells: [
          { value: providerLabel(repository.provider) },
          { value: repository.directory_path },
          { value: timestampSortValue(repository.refreshed_at), content: formatTimestamp(repository.refreshed_at) },
        ],
        facts: [
          ["Repository", repository.managed_repo_id],
          ["Provider", providerLabel(repository.provider)],
          ["Directory", repository.directory_path],
          ["Default branch", repository.default_branch],
          ["Bindings", repository.bindings?.length ?? 0],
          ["Refreshed", formatTimestamp(repository.refreshed_at)],
        ],
      }))}
      selectedId={selectedRepositoryId}
      onSelect={setSelectedRepositoryId}
      loading={loading}
      error={error}
      initialRecordOpen={Boolean(readAdminHashQuery().get("repository_id"))}
      recordRouteKeys="repository_id"
      filters={
        <>
          <label className="field-stack collection-search">
            <span>Find repository</span>
            <input
              type="search"
              value={searchQuery}
              placeholder="Name, ID, URL, or provider"
              onChange={(event) => setSearchQuery(event.target.value)}
            />
          </label>
          <label className="checkbox-row">
            <input
              type="checkbox"
              checked={includeDescendants}
              onChange={(event) => setIncludeDescendants(event.target.checked)}
            />
            <span>Include descendant directories</span>
          </label>
        </>
      }
      actions={(openRecord) => (
        <>
          <button type="button" disabled={loading} onClick={() => setReloadVersion((current) => current + 1)}>
            Reload
          </button>
          <button
            type="button"
            className="primary"
            disabled={mutationsLocked}
            title={mutationsLocked ? "Unlock privileged operations to register repositories" : undefined}
            onClick={() => {
              openRecord();
              window.requestAnimationFrame(() =>
                document.querySelector<HTMLInputElement>(".managed-git-source-field input")?.focus(),
              );
            }}
          >
            Register repository
          </button>
        </>
      )}
    >
      <div className="feature-page managed-git-page">
        <section className="feature-sidebar">
          <div className="feature-sidebar-header">
            <div>
              <div className="eyebrow">Code transport</div>
              <h1>Managed Git</h1>
            </div>
            <button
              type="button"
              className="admin-action-button compact"
              onClick={() => setReloadVersion((current) => current + 1)}
              disabled={loading}
            >
              {loading ? "Loading…" : "Reload"}
            </button>
          </div>

          <div className="config-field-grid managed-git-filter-grid">
            <label className="field-stack">
              <span>Find repository</span>
              <input
                type="search"
                value={searchQuery}
                placeholder="Name, ID, URL, or provider"
                onChange={(event) => setSearchQuery(event.target.value)}
              />
            </label>
            <label className="checkbox-row">
              <input
                type="checkbox"
                checked={includeDescendants}
                onChange={(event) => setIncludeDescendants(event.target.checked)}
              />
              <span>Include descendant directories</span>
            </label>
          </div>

          <div className="managed-git-list-summary" aria-live="polite">
            <span>{resultLabel}</span>
            <span>{selectedDirectoryPath}</span>
          </div>

          <div className="feature-list managed-git-repository-list" aria-label="Managed Git repositories">
            {visibleRepositories.map((repository) => (
              <button
                key={repository.managed_repo_id}
                type="button"
                className={`feature-list-item managed-git-list-item${
                  repository.managed_repo_id === selectedRepositoryId ? " selected" : ""
                }`}
                aria-pressed={repository.managed_repo_id === selectedRepositoryId}
                onClick={() => setSelectedRepositoryId(repository.managed_repo_id)}
              >
                <div className="feature-list-title-row managed-git-list-title-row">
                  <strong>{repositoryLabel(repository)}</strong>
                  <span className="status-pill state-idle">{providerLabel(repository.provider)}</span>
                </div>
                <code>{repository.managed_repo_id}</code>
                <span className="feature-list-meta">{repository.directory_path}</span>
              </button>
            ))}
            {loading && repositories.length === 0 ? (
              <div className="empty-state">Loading managed repositories…</div>
            ) : null}
            {!loading && repositories.length === 0 && !error ? (
              <div className="empty-state">No managed repositories are visible from {selectedDirectoryPath}.</div>
            ) : null}
            {!loading && repositories.length > 0 && visibleRepositories.length === 0 ? (
              <div className="empty-state">No repositories match the current search.</div>
            ) : null}
          </div>
        </section>

        <section className="feature-content managed-git-content">
          <div className="feature-toolbar">
            <div>
              <h2>{selectedRepository ? repositoryLabel(selectedRepository) : "Repository catalog"}</h2>
              <p className="muted-text">
                Register, inspect, and maintain host-owned repositories in the selected Admin directory.
              </p>
            </div>
          </div>

          {mutationsLocked ? (
            <div className="callout managed-git-lock-notice">
              Unlock privileged operations to register, refresh, maintain, edit, or delete a repository.
            </div>
          ) : null}
          {error ? <div className="callout error-callout">{error}</div> : null}
          {status ? (
            <div className="callout success-callout" role="status">
              {status}
            </div>
          ) : null}

          <article className="panel managed-git-registration-panel">
            <div className="panel-header">
              <div>
                <h3>Register repository</h3>
                <p className="muted-text">
                  The control plane owns upstream credentials; use a canonical HTTPS or SSH repository URL.
                </p>
              </div>
              <span className="panel-meta">{selectedDirectoryPath}</span>
            </div>
            <form className="managed-git-register-form" onSubmit={(event) => void registerRepository(event)}>
              <label className="field-stack managed-git-source-field">
                <span>Repository source</span>
                <input
                  type="text"
                  value={repoSource}
                  placeholder="https://dev.azure.com/organization/project/_git/repository"
                  onChange={(event) => setRepoSource(event.target.value)}
                  disabled={mutationsLocked || busyAction !== null}
                  required
                />
              </label>
              <label className="field-stack">
                <span>Default branch</span>
                <input
                  value={defaultBranch}
                  placeholder="Detect automatically"
                  onChange={(event) => setDefaultBranch(event.target.value)}
                  disabled={mutationsLocked || busyAction !== null}
                />
              </label>
              <label className="checkbox-row managed-git-refresh-toggle">
                <input
                  type="checkbox"
                  checked={registerRefresh}
                  onChange={(event) => setRegisterRefresh(event.target.checked)}
                  disabled={mutationsLocked || busyAction !== null}
                />
                <span>Fetch upstream refs now</span>
              </label>
              <button
                type="submit"
                className="admin-shell-button"
                disabled={mutationsLocked || busyAction !== null || !repoSource.trim()}
              >
                {busyAction === "register" ? "Registering…" : "Register repository"}
              </button>
            </form>
          </article>

          {selectedRepository ? (
            <>
              <article className="panel detail-panel managed-git-detail-panel">
                <div className="panel-heading">
                  <div>
                    <div className="section-label">Managed repository</div>
                    <h2>{repositoryLabel(selectedRepository)}</h2>
                  </div>
                  <span className="status-pill state-running">{providerLabel(selectedRepository.provider)}</span>
                </div>
                <dl className="property-grid managed-git-property-grid">
                  <div>
                    <dt>Managed repository ID</dt>
                    <dd>
                      <code>{selectedRepository.managed_repo_id}</code>
                    </dd>
                  </div>
                  <div>
                    <dt>Directory</dt>
                    <dd>
                      <code>{selectedRepository.directory_path}</code>
                    </dd>
                  </div>
                  <div>
                    <dt>Provider</dt>
                    <dd>{providerLabel(selectedRepository.provider)}</dd>
                  </div>
                  <div>
                    <dt>Default branch</dt>
                    <dd>
                      <code>{selectedRepository.default_branch || "Not detected"}</code>
                    </dd>
                  </div>
                  <div className="managed-git-wide-property">
                    <dt>Canonical identity</dt>
                    <dd>
                      <code>{selectedRepository.canonical_repository_identity || "Unavailable"}</code>
                    </dd>
                  </div>
                  <div className="managed-git-wide-property">
                    <dt>Canonical URL</dt>
                    <dd className="managed-git-url">
                      {selectedRepository.canonical_repository_url?.match(/^https?:\/\//i) ? (
                        <a href={selectedRepository.canonical_repository_url} target="_blank" rel="noreferrer">
                          {selectedRepository.canonical_repository_url}
                        </a>
                      ) : (
                        <code>{selectedRepository.canonical_repository_url || "Unavailable"}</code>
                      )}
                    </dd>
                  </div>
                  <div>
                    <dt>Host commit identity</dt>
                    <dd>{hostCommitIdentity(selectedRepository)}</dd>
                  </div>
                  <div>
                    <dt>Bindings</dt>
                    <dd>{selectedRepository.bindings?.length ?? 0}</dd>
                  </div>
                  <div>
                    <dt>Created</dt>
                    <dd>{formatTimestamp(selectedRepository.created_at)}</dd>
                  </div>
                  <div>
                    <dt>Updated</dt>
                    <dd>{formatTimestamp(selectedRepository.updated_at)}</dd>
                  </div>
                  <div>
                    <dt>Last upstream refresh</dt>
                    <dd>{formatTimestamp(selectedRepository.refreshed_at)}</dd>
                  </div>
                  <div>
                    <dt>Last maintenance</dt>
                    <dd>{formatTimestamp(selectedRepository.maintenance_at)}</dd>
                  </div>
                </dl>
              </article>

              <RepositoryIndexPanel
                key={selectedRepository.managed_repo_id}
                repositoryId={selectedRepository.managed_repo_id}
                privilegedUnlocked={privilegedUnlocked}
              />
              <article className="panel managed-git-actions-panel">
                <div className="panel-header">
                  <div>
                    <h3>Repository operations</h3>
                    <p className="muted-text">
                      Refresh is safe to repeat. Deletion is blocked while repository bindings remain.
                    </p>
                  </div>
                </div>
                <div className="feature-toolbar managed-git-action-buttons">
                  <button
                    type="button"
                    className="admin-shell-button"
                    onClick={() => void refreshRepository()}
                    disabled={mutationsLocked || busyAction !== null}
                  >
                    {busyAction === "refresh" ? "Refreshing…" : "Refresh upstream"}
                  </button>
                  <button
                    type="button"
                    className="admin-shell-button admin-shell-button-muted"
                    onClick={() => void runMaintenance()}
                    disabled={mutationsLocked || busyAction !== null}
                  >
                    {busyAction === "maintenance" ? "Running maintenance…" : "Run maintenance"}
                  </button>
                  <button
                    type="button"
                    className="admin-action-button danger"
                    onClick={() => void deleteRepository()}
                    disabled={mutationsLocked || busyAction !== null}
                  >
                    {busyAction === "delete" ? "Deleting…" : "Delete repository"}
                  </button>
                </div>
              </article>

              <article className="panel managed-git-identity-panel">
                <div className="panel-header">
                  <div>
                    <h3>Host commit identity</h3>
                    <p className="muted-text">
                      Used for future host-created commits; existing history and agent checkouts are unchanged.
                    </p>
                  </div>
                </div>
                <form className="managed-git-identity-form" onSubmit={(event) => void saveHostCommitIdentity(event)}>
                  <label className="field-stack">
                    <span>Name</span>
                    <input
                      value={identityName}
                      onChange={(event) => setIdentityName(event.target.value)}
                      disabled={mutationsLocked || busyAction !== null}
                      required
                    />
                  </label>
                  <label className="field-stack">
                    <span>Email</span>
                    <input
                      type="email"
                      value={identityEmail}
                      onChange={(event) => setIdentityEmail(event.target.value)}
                      disabled={mutationsLocked || busyAction !== null}
                      required
                    />
                  </label>
                  <button
                    type="submit"
                    className="admin-shell-button"
                    disabled={mutationsLocked || busyAction !== null || !identityName.trim() || !identityEmail.trim()}
                  >
                    {busyAction === "identity" ? "Saving…" : "Save identity"}
                  </button>
                </form>
              </article>

              <article className="panel managed-git-bindings-panel">
                <div className="panel-header">
                  <div>
                    <h3>Bindings</h3>
                    <p className="muted-text">Records that currently refer to this managed repository.</p>
                  </div>
                  <span className="panel-meta">{selectedRepository.bindings?.length ?? 0} total</span>
                </div>
                {selectedBindingGroups.length > 0 ? (
                  <div className="managed-git-binding-groups">
                    {selectedBindingGroups.map((group) => (
                      <section key={group.kind} className="detail-block managed-git-binding-group">
                        <div className="feature-list-title-row">
                          <strong>{group.kind}</strong>
                          <span className="panel-meta">{group.bindings.length}</span>
                        </div>
                        <ul>
                          {group.bindings.map((binding) => (
                            <li key={`${group.kind}:${binding.binding_id}`}>
                              <code>{binding.binding_id}</code>
                              {binding.bound_at ? <span>{formatTimestamp(binding.bound_at)}</span> : null}
                            </li>
                          ))}
                        </ul>
                      </section>
                    ))}
                  </div>
                ) : (
                  <div className="empty-state">This repository has no active bindings.</div>
                )}
              </article>
            </>
          ) : !loading && !error ? (
            <div className="empty-state">
              Register or select a repository to inspect its stable identity and bindings.
            </div>
          ) : null}
        </section>
      </div>
    </RecordWorkspace>
  );
}

import { type FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

import { fetchJson, formatTimestamp } from "../admin/api";
import { DownloadIcon, RefreshIcon, UploadIcon } from "../admin/PageActionIcons";
import { Tooltip } from "../admin/Tooltip";
import { RecordInspector } from "../admin/RecordInspector";
import { FilterDisclosure } from "../admin/FilterDisclosure";
import { adminHash } from "../../adminRouting";

type PublishedArtifact = {
  key: string;
  storage_id: string;
  archive_format: "tar.gz";
  filename: string;
  mime_type: string;
  byte_count: number;
  sha256: string;
  published_at: string;
  session_id: string;
  vm_name?: string | null;
  execution_backend?: string | null;
  source_artifacts_root?: string | null;
  source_file_count: number;
  source_byte_count: number;
  metadata: Record<string, unknown>;
};

type ArtifactListResponse = {
  items: PublishedArtifact[];
  limit: number;
  total: number;
  next_cursor: string | null;
};

type ArtifactSpace = {
  path: string;
  parent_path: string | null;
  display_name?: string | null;
  metadata: Record<string, unknown>;
};

type ArtifactSpacesResponse = {
  success: boolean;
  revision: number;
  spaces: ArtifactSpace[];
  count: number;
};

type ArtifactSpaceTreeNode = ArtifactSpace & {
  children: ArtifactSpaceTreeNode[];
};

type ArtifactFilters = {
  key: string;
  publishedAfter: string;
  publishedBefore: string;
  limit: string;
};

const DEFAULT_LIMIT = 50;
const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "";

function toIsoTimestamp(value: string): string | null {
  const trimmed = value.trim();
  if (!trimmed) return null;
  const date = new Date(trimmed);
  if (Number.isNaN(date.getTime())) return null;
  return date.toISOString();
}

function buildQuery(filters: ArtifactFilters, cursor: string | null, spacePath: string | null): string {
  const params = new URLSearchParams();
  const key = filters.key.trim();
  const limit = Number(filters.limit);
  if (key) params.set("key", key);
  if (Number.isFinite(limit) && limit > 0) params.set("limit", String(Math.floor(limit)));
  const publishedAfter = toIsoTimestamp(filters.publishedAfter);
  const publishedBefore = toIsoTimestamp(filters.publishedBefore);
  if (publishedAfter) params.set("published_after", publishedAfter);
  if (publishedBefore) params.set("published_before", publishedBefore);
  if (spacePath) {
    params.set("space_path", spacePath);
    params.set("space_recursive", "true");
  }
  if (cursor) params.set("cursor", cursor);
  const query = params.toString();
  return query ? `?${query}` : "";
}

function artifactUrl(key: string): string {
  return `/api/admin/artifacts/${encodeURIComponent(key)}`;
}

function artifactDownloadUrl(key: string): string {
  return `/api/admin/artifact-downloads/${encodeURIComponent(key)}`;
}

function formatBytes(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "n/a";
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KiB`;
  if (value < 1024 * 1024 * 1024) return `${(value / (1024 * 1024)).toFixed(1)} MiB`;
  return `${(value / (1024 * 1024 * 1024)).toFixed(1)} GiB`;
}

function compactArtifactKey(key: string): string {
  return key.length > 100 ? `${key.slice(0, 56)}…${key.slice(-40)}` : key;
}

function artifactSpaceLabel(space: ArtifactSpace): string {
  const displayName = space.display_name?.trim();
  if (displayName) return displayName;
  if (space.path === "/") return "Root space";
  return space.path.split("/").filter(Boolean).pop() ?? space.path;
}

function buildArtifactSpaceTree(spaces: ArtifactSpace[]): ArtifactSpaceTreeNode[] {
  const nodesByPath = new Map<string, ArtifactSpaceTreeNode>();
  for (const space of spaces) {
    nodesByPath.set(space.path, { ...space, children: [] });
  }
  const roots: ArtifactSpaceTreeNode[] = [];
  for (const node of nodesByPath.values()) {
    const parent = node.parent_path ? nodesByPath.get(node.parent_path) : null;
    if (parent && parent.path !== node.path) {
      parent.children.push(node);
    } else {
      roots.push(node);
    }
  }
  const sortNodes = (nodes: ArtifactSpaceTreeNode[]) => {
    nodes.sort((left, right) => left.path.localeCompare(right.path));
    for (const node of nodes) sortNodes(node.children);
  };
  sortNodes(roots);
  return roots;
}

function defaultExpandedSpacePaths(nodes: ArtifactSpaceTreeNode[]): Set<string> {
  const expandedPaths = new Set<string>();
  const visit = (node: ArtifactSpaceTreeNode, depth: number) => {
    if (node.children.length > 0 && depth < 2) {
      expandedPaths.add(node.path);
    }
    for (const child of node.children) visit(child, depth + 1);
  };
  for (const node of nodes) visit(node, 0);
  return expandedPaths;
}

function ArtifactSpaceTree({
  nodes,
  selectedPath,
  expandedPaths,
  onToggle,
  onSelect,
  root = false,
}: {
  nodes: ArtifactSpaceTreeNode[];
  selectedPath: string | null;
  expandedPaths: Set<string>;
  onToggle: (path: string) => void;
  onSelect: (path: string) => void;
  root?: boolean;
}) {
  return (
    <ul className={`artifact-space-tree-list${root ? " root" : ""}`}>
      {nodes.map((node) => {
        const hasChildren = node.children.length > 0;
        const expanded = expandedPaths.has(node.path);
        return (
          <li key={node.path}>
            <div className="artifact-space-tree-row">
              {hasChildren ? (
                <button
                  type="button"
                  className="artifact-space-tree-toggle"
                  aria-label={`${expanded ? "Collapse" : "Expand"} ${node.path}`}
                  aria-expanded={expanded}
                  onClick={() => onToggle(node.path)}
                >
                  {expanded ? <ChevronDown size={14} strokeWidth={2} /> : <ChevronRight size={14} strokeWidth={2} />}
                </button>
              ) : (
                <span className="artifact-space-tree-spacer" aria-hidden="true" />
              )}
              <button
                type="button"
                className={`artifact-space-tree-button${node.path === selectedPath ? " selected" : ""}`}
                aria-pressed={node.path === selectedPath}
                onClick={() => onSelect(node.path)}
              >
                <span className="artifact-space-tree-name">{artifactSpaceLabel(node)}</span>
                <span className="artifact-space-tree-path">{node.path}</span>
              </button>
            </div>
            {hasChildren && expanded ? (
              <ArtifactSpaceTree
                nodes={node.children}
                selectedPath={selectedPath}
                expandedPaths={expandedPaths}
                onToggle={onToggle}
                onSelect={onSelect}
              />
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}

export function ArtifactsPage() {
  const [filters, setFilters] = useState<ArtifactFilters>({
    key: "",
    publishedAfter: "",
    publishedBefore: "",
    limit: String(DEFAULT_LIMIT),
  });
  const [cursor, setCursor] = useState<string | null>(null);
  const [previousCursors, setPreviousCursors] = useState<string[]>([]);
  const [response, setResponse] = useState<ArtifactListResponse | null>(null);
  const [artifactSpaces, setArtifactSpaces] = useState<ArtifactSpacesResponse | null>(null);
  const [selectedSpacePath, setSelectedSpacePath] = useState<string | null>(null);
  const [expandedSpacePaths, setExpandedSpacePaths] = useState<Set<string>>(() => new Set());
  const [spacePickerOpen, setSpacePickerOpen] = useState(false);
  const spacePickerRef = useRef<HTMLDialogElement>(null);
  const spacePickerTriggerRef = useRef<HTMLButtonElement>(null);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const inspectorTriggerRef = useRef<HTMLElement | null>(null);
  const [resolvedArtifact, setResolvedArtifact] = useState<PublishedArtifact | null>(null);
  const [loading, setLoading] = useState(false);
  const [artifactSpacesLoading, setArtifactSpacesLoading] = useState(false);
  const [lookupLoading, setLookupLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [artifactSpacesError, setArtifactSpacesError] = useState<string | null>(null);
  const [lookupError, setLookupError] = useState<string | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [uploadMessage, setUploadMessage] = useState<string | null>(null);
  const [uploadKey, setUploadKey] = useState("");
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [uploadDialogOpen, setUploadDialogOpen] = useState(false);

  const items = response?.items ?? [];
  const selectedArtifact = resolvedArtifact ?? items.find((item) => item.key === selectedKey) ?? items[0] ?? null;
  const artifactSpaceTree = useMemo(() => buildArtifactSpaceTree(artifactSpaces?.spaces ?? []), [artifactSpaces]);
  const selectedSpaceLabel = selectedSpacePath ?? "All spaces";
  const selectedSpace = artifactSpaces?.spaces.find((space) => space.path === selectedSpacePath);

  useEffect(() => {
    if (!spacePickerOpen) return;
    const dialog = spacePickerRef.current;
    if (!dialog) return;
    const trigger = spacePickerTriggerRef.current;
    dialog.showModal();
    const selectedButton = dialog.querySelector<HTMLButtonElement>('[aria-pressed="true"]');
    selectedButton?.focus({ preventScroll: true });
    selectedButton?.scrollIntoView({ block: "nearest" });
    return () => {
      dialog.close();
      if (trigger?.isConnected) trigger.focus({ preventScroll: true });
    };
  }, [spacePickerOpen]);

  async function loadArtifacts(nextCursor = cursor, spacePath = selectedSpacePath) {
    setLoading(true);
    setError(null);
    try {
      const payload = await fetchJson<ArtifactListResponse>(`/api/admin/artifacts${buildQuery(filters, nextCursor, spacePath)}`);
      setResponse(payload);
      if (!selectedKey && payload.items.length > 0) {
        setSelectedKey(payload.items[0].key);
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setLoading(false);
    }
  }

  async function loadArtifactSpaces() {
    setArtifactSpacesLoading(true);
    setArtifactSpacesError(null);
    try {
      const payload = await fetchJson<ArtifactSpacesResponse>("/api/admin/artifact-spaces?recursive=true&include_versions=false");
      setArtifactSpaces(payload);
      const nextTree = buildArtifactSpaceTree(payload.spaces);
      setExpandedSpacePaths((current) => (current.size > 0 ? current : defaultExpandedSpacePaths(nextTree)));
    } catch (reason) {
      setArtifactSpacesError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setArtifactSpacesLoading(false);
    }
  }

  async function resolveKey() {
    const key = filters.key.trim();
    if (!key) {
      setLookupError("Artifact key is required.");
      setResolvedArtifact(null);
      return;
    }
    setLookupLoading(true);
    setLookupError(null);
    try {
      const payload = await fetchJson<PublishedArtifact>(artifactUrl(key));
      setResolvedArtifact(payload);
      setSelectedKey(payload.key);
      setInspectorOpen(true);
    } catch (reason) {
      setResolvedArtifact(null);
      setLookupError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setLookupLoading(false);
    }
  }

  useEffect(() => {
    void loadArtifactSpaces();
    void loadArtifacts(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function refreshArtifacts() {
    void loadArtifactSpaces();
    void loadArtifacts();
  }

  function applyFilters() {
    setCursor(null);
    setPreviousCursors([]);
    setResolvedArtifact(null);
    void loadArtifacts(null);
  }

  function selectSpaceFilter(path: string | null) {
    setSelectedSpacePath(path);
    setSpacePickerOpen(false);
    setCursor(null);
    setPreviousCursors([]);
    setResolvedArtifact(null);
    void loadArtifacts(null, path);
  }

  function toggleSpacePath(path: string) {
    setExpandedSpacePaths((current) => {
      const next = new Set(current);
      if (next.has(path)) {
        next.delete(path);
      } else {
        next.add(path);
      }
      return next;
    });
  }

  function nextPage() {
    if (!response?.next_cursor) return;
    setPreviousCursors((current) => [...current, cursor ?? ""]);
    setCursor(response.next_cursor);
    setResolvedArtifact(null);
    void loadArtifacts(response.next_cursor);
  }

  function previousPage() {
    if (previousCursors.length === 0) return;
    const nextStack = previousCursors.slice(0, -1);
    const nextCursor = previousCursors[previousCursors.length - 1] || null;
    setPreviousCursors(nextStack);
    setCursor(nextCursor);
    setResolvedArtifact(null);
    void loadArtifacts(nextCursor);
  }

  function downloadArtifact(key: string) {
    window.location.assign(artifactDownloadUrl(key));
  }

  function openUploadDialog() {
    setUploadError(null);
    setUploadDialogOpen(true);
  }

  function closeUploadDialog() {
    if (uploading) return;
    setUploadError(null);
    setUploadDialogOpen(false);
  }

  async function uploadArtifact(event?: FormEvent<HTMLFormElement>) {
    event?.preventDefault();
    const key = uploadKey.trim();
    if (!key) {
      setUploadError("Artifact key is required.");
      return;
    }
    if (!uploadFile) {
      setUploadError("File is required.");
      return;
    }
    const formData = new FormData();
    formData.set("key", key);
    formData.set("file", uploadFile);
    setUploading(true);
    setUploadError(null);
    setUploadMessage(null);
    let successMessage: string | null = null;
    try {
      const response = await fetch(`${API_BASE}/api/admin/artifacts/uploads`, {
        method: "POST",
        body: formData,
      });
      const payload = (await response.json()) as PublishedArtifact & { error?: string };
      if (!response.ok) throw new Error(payload.error || `${response.status} ${response.statusText}`);
      successMessage = `Uploaded ${payload.key}.`;
      setUploadKey("");
      setUploadFile(null);
      setUploadDialogOpen(false);
      await loadArtifacts(null);
      setSelectedKey(payload.key);
      setInspectorOpen(true);
      setResolvedArtifact(payload);
    } catch (reason) {
      setUploadError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      if (successMessage) {
        setUploadMessage(successMessage);
      }
      setUploading(false);
    }
  }

  return (
    <div className="feature-page artifacts-page">
      <div className="feature-toolbar">
        <div>
          <h1>Artifacts</h1>
        </div>
        <div className="feature-toolbar-actions">
          <span className="panel-meta">
            {response?.total ?? 0} matched - {selectedSpaceLabel}
          </span>
          <Tooltip content="Upload a local file as a published artifact.">
            <button
              type="button"
              className="admin-icon-button toolbar-icon-button"
              aria-label="Upload artifact"
              onClick={openUploadDialog}
            >
              <UploadIcon />
            </button>
          </Tooltip>
          <button
            className="admin-action-button compact"
            type="button"
            onClick={refreshArtifacts}
            disabled={loading || artifactSpacesLoading}
          >
            <RefreshIcon />
            Refresh
          </button>
        </div>
      </div>

      <section className="feature-sidebar">
        <div className="artifact-space-filter">
          <div className="artifact-space-filter-heading">
            <span>Artifact spaces</span>
            <span className="panel-meta">{artifactSpaces?.count ?? 0}</span>
          </div>
          <button
            ref={spacePickerTriggerRef}
            type="button"
            className="artifact-space-tree-button artifact-space-picker-trigger selected"
            aria-label="Choose artifact space"
            aria-describedby="artifact-space-selected-path"
            aria-haspopup="dialog"
            aria-expanded={spacePickerOpen}
            onClick={() => setSpacePickerOpen(true)}
          >
            <span className="artifact-space-picker-selection">
              <span className="artifact-space-tree-name">{selectedSpace ? artifactSpaceLabel(selectedSpace) : selectedSpaceLabel}</span>
              <span className="artifact-space-tree-path" id="artifact-space-selected-path">{selectedSpacePath ?? "Unfiltered"}</span>
            </span>
            <ChevronRight size={16} aria-hidden="true" />
          </button>
        </div>

        <div className="sidebar-divider" />

        <form onSubmit={event => { event.preventDefault(); void resolveKey(); }}>
        <label className="field-stack">
          <span>Key</span>
          <input
            value={filters.key}
            onChange={(event) => setFilters((current) => ({ ...current, key: event.target.value }))}
            placeholder="runs/artifact-key"
          />
        </label>
        <div className="inline-actions artifact-lookup-actions">
          <button
            className="admin-action-button compact"
            type="submit"
            disabled={lookupLoading}
          >
            {lookupLoading ? "Resolving..." : "Resolve key"}
          </button>
          <button className="admin-action-button compact" type="button" onClick={applyFilters} disabled={loading}>
            Apply filters
          </button>
        </div>
        </form>
        {lookupError ? <div className="callout error-callout">{lookupError}</div> : null}

        <FilterDisclosure activeCount={[filters.publishedAfter, filters.publishedBefore, filters.limit !== String(DEFAULT_LIMIT)].filter(Boolean).length}>
        <label className="field-stack">
          <span>Published after</span>
          <input
            type="datetime-local"
            value={filters.publishedAfter}
            onChange={(event) => setFilters((current) => ({ ...current, publishedAfter: event.target.value }))}
          />
        </label>
        <label className="field-stack">
          <span>Published before</span>
          <input
            type="datetime-local"
            value={filters.publishedBefore}
            onChange={(event) => setFilters((current) => ({ ...current, publishedBefore: event.target.value }))}
          />
        </label>
        <label className="field-stack">
          <span>Limit</span>
          <input
            type="number"
            min="1"
            max="200"
            step="1"
            value={filters.limit}
            onChange={(event) => setFilters((current) => ({ ...current, limit: event.target.value }))}
          />
        </label>
        </FilterDisclosure>
      </section>

      <section className="feature-content">
        {error ? <div className="callout error-callout">{error}</div> : null}
        {uploadMessage ? <div className="callout success-callout">{uploadMessage}</div> : null}

        <div
          className={`admin-grid two-column artifacts-grid${inspectorOpen && selectedArtifact ? " has-inspector" : ""}`}
        >
          <article className="panel section-panel">
            <div className="panel-header">
              <div>
                <h3>Archive list</h3>
                <p className="muted-text">
                  Showing {items.length} {items.length === 1 ? "archive" : "archives"}.
                </p>
              </div>
              <div className="feature-toolbar-actions">
                <button
                  className="admin-action-button compact"
                  type="button"
                  onClick={previousPage}
                  disabled={previousCursors.length === 0 || loading}
                >
                  Previous
                </button>
                <button
                  className="admin-action-button compact"
                  type="button"
                  onClick={nextPage}
                  disabled={!response?.next_cursor || loading}
                >
                  Next
                </button>
              </div>
            </div>
            <div className="table-wrap mobile-paired-table">
              <table className="admin-table artifacts-table">
                <thead>
                  <tr>
                    <th>Key</th>
                    <th>Published</th>
                    <th>Session</th>
                    <th>Size</th>
                    <th>Download</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((item) => (
                    <tr
                      key={item.key}
                      className={item.key === selectedArtifact?.key ? "selected" : ""}
                      onClick={(event) => {
                        setSelectedKey(item.key);
                        setInspectorOpen(true);
                        inspectorTriggerRef.current = event.currentTarget.querySelector("button");
                        setResolvedArtifact(null);
                      }}
                    >
                      <td className="artifact-key-cell">
                        <button type="button" className="record-name" title={item.key} aria-label={item.key}>
                          {compactArtifactKey(item.key)}
                        </button>
                      </td>
                      <td className="artifact-fixed-cell">{formatTimestamp(item.published_at)}</td>
                      <td>{item.session_id}</td>
                      <td className="artifact-fixed-cell">{formatBytes(item.byte_count)}</td>
                      <td>
                        <div className="feature-toolbar-actions icon-actions artifacts-download-cell">
                          <button
                            className="admin-icon-button"
                            type="button"
                            aria-label={`Download artifact ${item.key}`}
                            onClick={(event) => {
                              event.stopPropagation();
                              downloadArtifact(item.key);
                            }}
                          >
                            <DownloadIcon />
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                  {loading && items.length === 0 ? (
                    <tr>
                      <td className="empty-state-cell" colSpan={5}>
                        Loading artifacts...
                      </td>
                    </tr>
                  ) : null}
                  {!loading && items.length === 0 ? (
                    <tr>
                      <td className="empty-state-cell" colSpan={5}>
                        No artifacts matched the current filters.
                      </td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </div>
            <div className="mobile-record-list artifacts-mobile-record-list" aria-label="Published artifacts">
              {items.map((item) => (
                <article
                  key={item.key}
                  className={`mobile-record-card${item.key === selectedArtifact?.key ? " selected" : ""}`}
                >
                  <button
                    type="button"
                    className="mobile-record-card-main"
                    aria-label={item.key}
                    onClick={(event) => {
                      setSelectedKey(item.key);
                      setInspectorOpen(true);
                      inspectorTriggerRef.current = event.currentTarget;
                      setResolvedArtifact(null);
                    }}
                  >
                    <div className="mobile-record-title-row">
                      <strong title={item.key}>{compactArtifactKey(item.key)}</strong>
                    </div>
                    <div className="mobile-record-row">
                      <span>Published</span>
                      <span>{formatTimestamp(item.published_at)}</span>
                    </div>
                    <div className="mobile-record-row">
                      <span>Session</span>
                      <span>{item.session_id}</span>
                    </div>
                    <div className="mobile-record-row">
                      <span>Size</span>
                      <span>{formatBytes(item.byte_count)}</span>
                    </div>
                  </button>
                  <div className="mobile-record-actions">
                    <button
                      className="admin-icon-button"
                      type="button"
                      aria-label={`Download artifact ${item.key}`}
                      onClick={() => downloadArtifact(item.key)}
                    >
                      <DownloadIcon />
                    </button>
                  </div>
                </article>
              ))}
              {loading && items.length === 0 ? (
                <div className="empty-state mobile-record-empty">Loading artifacts...</div>
              ) : null}
              {!loading && items.length === 0 ? (
                <div className="empty-state mobile-record-empty">No artifacts matched the current filters.</div>
              ) : null}
            </div>
          </article>

          {inspectorOpen && selectedArtifact ? (
            <RecordInspector
              key={selectedArtifact.key}
              className="artifacts-detail-panel"
              title={selectedArtifact.key}
              subtitle={selectedArtifact.filename}
              returnFocus={() => inspectorTriggerRef.current}
              onClose={() => setInspectorOpen(false)}
            >
              <button
                type="button"
                className="primary"
                aria-label="Download selected artifact"
                onClick={() => downloadArtifact(selectedArtifact.key)}
              >
                Download archive
              </button>
              {selectedArtifact ? (
                <div className="artifact-detail-stack">
                  <dl className="detail-list">
                    <div>
                      <dt>Published</dt>
                      <dd>{formatTimestamp(selectedArtifact.published_at)}</dd>
                    </div>
                    <div>
                      <dt>Session</dt>
                      <dd><a href={adminHash("sessions", { session_id: selectedArtifact.session_id })}>{selectedArtifact.session_id}</a></dd>
                    </div>
                    <div>
                      <dt>Backend</dt>
                      <dd>{selectedArtifact.execution_backend ?? "n/a"}</dd>
                    </div>
                    <div>
                      <dt>Archive size</dt>
                      <dd>{formatBytes(selectedArtifact.byte_count)}</dd>
                    </div>
                    <div>
                      <dt>Source files</dt>
                      <dd>{selectedArtifact.source_file_count}</dd>
                    </div>
                    <div>
                      <dt>Source bytes</dt>
                      <dd>{formatBytes(selectedArtifact.source_byte_count)}</dd>
                    </div>
                    <div>
                      <dt>Artifacts root</dt>
                      <dd>{selectedArtifact.source_artifacts_root ?? "n/a"}</dd>
                    </div>
                    <div>
                      <dt>SHA-256</dt>
                      <dd className="artifact-sha">{selectedArtifact.sha256}</dd>
                    </div>
                  </dl>
                  <details className="raw-json-panel">
                  <summary>Metadata</summary>
                  <label className="field-stack artifact-metadata-field">
                    <span>Metadata</span>
                    <textarea readOnly value={JSON.stringify(selectedArtifact.metadata ?? {}, null, 2)} />
                  </label>
                  </details>
                </div>
              ) : (
                <div className="empty-state">No artifact selected.</div>
              )}
            </RecordInspector>
          ) : null}
        </div>
      </section>
      {spacePickerOpen ? (
        <dialog
          ref={spacePickerRef}
          className="modal-card artifact-space-picker-modal"
          aria-labelledby="artifact-space-picker-title"
          aria-describedby="artifact-space-picker-description"
          onCancel={(event) => {
            event.preventDefault();
            setSpacePickerOpen(false);
          }}
          onClick={(event) => {
            if (event.target !== event.currentTarget) return;
            const bounds = event.currentTarget.getBoundingClientRect();
            if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) {
              setSpacePickerOpen(false);
            }
          }}
        >
          <div className="modal-header">
            <div>
              <div className="eyebrow">Artifact spaces</div>
              <h2 id="artifact-space-picker-title">Choose artifact space</h2>
              <p id="artifact-space-picker-description">Selecting a space includes artifacts in its child spaces.</p>
            </div>
            <button type="button" className="admin-action-button compact" onClick={() => setSpacePickerOpen(false)}>
              Close
            </button>
          </div>
          <div className="modal-body">
            <button
              type="button"
              className={`artifact-space-tree-button artifact-space-tree-all${selectedSpacePath === null ? " selected" : ""}`}
              aria-pressed={selectedSpacePath === null}
              onClick={() => selectSpaceFilter(null)}
            >
              <span className="artifact-space-tree-name">All spaces</span>
              <span className="artifact-space-tree-path">Unfiltered</span>
            </button>
            {artifactSpacesError ? <div className="callout error-callout">{artifactSpacesError}</div> : null}
            {artifactSpacesLoading && !artifactSpaces ? (
              <div className="empty-state artifact-space-empty">Loading spaces...</div>
            ) : null}
            {!artifactSpacesLoading && !artifactSpacesError && (artifactSpaces?.spaces.length ?? 0) === 0 ? (
              <div className="empty-state artifact-space-empty">No artifact spaces.</div>
            ) : null}
            {artifactSpaceTree.length > 0 ? (
              <ArtifactSpaceTree
                nodes={artifactSpaceTree}
                selectedPath={selectedSpacePath}
                expandedPaths={expandedSpacePaths}
                onToggle={toggleSpacePath}
                onSelect={selectSpaceFilter}
                root
              />
            ) : null}
          </div>
        </dialog>
      ) : null}
      {uploadDialogOpen ? (
        <div className="modal-backdrop" onClick={closeUploadDialog} role="presentation">
          <section
            className="modal-card artifact-upload-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="artifact-upload-modal-title"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="modal-header">
              <div>
                <div className="eyebrow">Artifact store</div>
                <h2 id="artifact-upload-modal-title">Upload artifact</h2>
              </div>
              <button
                className="admin-action-button compact"
                type="button"
                onClick={closeUploadDialog}
                disabled={uploading}
              >
                Close
              </button>
            </div>
            <div className="modal-body">
              <form className="form-grid artifact-upload-form" onSubmit={(event) => void uploadArtifact(event)}>
                <label className="field-stack field-span-2">
                  <span>Artifact key</span>
                  <input
                    value={uploadKey}
                    onChange={(event) => setUploadKey(event.target.value)}
                    placeholder="uploads/example"
                    autoFocus
                  />
                </label>
                <label className="field-stack field-span-2">
                  <span>File</span>
                  <input
                    type="file"
                    onChange={(event) => {
                      const file = event.target.files?.[0] ?? null;
                      setUploadFile(file);
                    }}
                  />
                </label>
                {uploadError ? <div className="callout error-callout field-span-2">{uploadError}</div> : null}
                <div className="inline-actions artifact-upload-actions field-span-2">
                  <button
                    className="admin-action-button compact"
                    type="button"
                    onClick={closeUploadDialog}
                    disabled={uploading}
                  >
                    Cancel
                  </button>
                  <button type="submit" className="admin-action-button primary compact" disabled={uploading}>
                    <UploadIcon />
                    {uploading ? "Uploading..." : "Upload"}
                  </button>
                </div>
              </form>
            </div>
          </section>
        </div>
      ) : null}
    </div>
  );
}

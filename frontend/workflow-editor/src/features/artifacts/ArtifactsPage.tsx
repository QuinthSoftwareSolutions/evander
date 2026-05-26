import { type FormEvent, useEffect, useState } from "react";

import { fetchJson, formatTimestamp } from "../admin/api";
import { DownloadIcon, UploadIcon } from "../admin/PageActionIcons";
import { Tooltip } from "../admin/Tooltip";

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

function buildQuery(filters: ArtifactFilters, cursor: string | null): string {
  const params = new URLSearchParams();
  const key = filters.key.trim();
  const limit = Number(filters.limit);
  if (key) params.set("key", key);
  if (Number.isFinite(limit) && limit > 0) params.set("limit", String(Math.floor(limit)));
  const publishedAfter = toIsoTimestamp(filters.publishedAfter);
  const publishedBefore = toIsoTimestamp(filters.publishedBefore);
  if (publishedAfter) params.set("published_after", publishedAfter);
  if (publishedBefore) params.set("published_before", publishedBefore);
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

function shortSha(value: string | null | undefined): string {
  const text = value?.trim() ?? "";
  return text.length > 16 ? `${text.slice(0, 16)}...` : text || "n/a";
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
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [resolvedArtifact, setResolvedArtifact] = useState<PublishedArtifact | null>(null);
  const [loading, setLoading] = useState(false);
  const [lookupLoading, setLookupLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lookupError, setLookupError] = useState<string | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [uploadMessage, setUploadMessage] = useState<string | null>(null);
  const [uploadKey, setUploadKey] = useState("");
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [uploadDialogOpen, setUploadDialogOpen] = useState(false);

  const items = response?.items ?? [];
  const selectedArtifact = resolvedArtifact ?? items.find((item) => item.key === selectedKey) ?? items[0] ?? null;

  async function loadArtifacts(nextCursor = cursor) {
    setLoading(true);
    setError(null);
    try {
      const payload = await fetchJson<ArtifactListResponse>(`/api/admin/artifacts${buildQuery(filters, nextCursor)}`);
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
    } catch (reason) {
      setResolvedArtifact(null);
      setLookupError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setLookupLoading(false);
    }
  }

  useEffect(() => {
    void loadArtifacts(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function applyFilters() {
    setCursor(null);
    setPreviousCursors([]);
    setResolvedArtifact(null);
    void loadArtifacts(null);
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
    try {
      const response = await fetch(`${API_BASE}/api/admin/artifacts/uploads`, {
        method: "POST",
        body: formData,
      });
      const payload = (await response.json()) as PublishedArtifact & { error?: string };
      if (!response.ok) throw new Error(payload.error || `${response.status} ${response.statusText}`);
      setUploadMessage(`Uploaded ${payload.key}.`);
      setUploadKey("");
      setUploadFile(null);
      setUploadDialogOpen(false);
      await loadArtifacts(null);
      setSelectedKey(payload.key);
      setResolvedArtifact(payload);
    } catch (reason) {
      setUploadError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setUploading(false);
    }
  }

  return (
    <div className="feature-page artifacts-page">
      <section className="feature-sidebar">
        <div className="feature-sidebar-header">
          <div>
            <div className="eyebrow">Artifact store</div>
            <h1>Artifacts</h1>
          </div>
          <button type="button" onClick={() => void loadArtifacts()}>
            Refresh
          </button>
        </div>

        <label className="field-stack">
          <span>Key</span>
          <input
            value={filters.key}
            onChange={(event) => setFilters((current) => ({ ...current, key: event.target.value }))}
            placeholder="runs/artifact-key"
          />
        </label>
        <div className="inline-actions artifact-lookup-actions">
          <button type="button" onClick={() => void resolveKey()} disabled={lookupLoading}>
            {lookupLoading ? "Resolving..." : "Resolve key"}
          </button>
          <button type="button" onClick={applyFilters} disabled={loading}>
            Apply filters
          </button>
        </div>
        {lookupError ? <div className="callout error-callout">{lookupError}</div> : null}

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
      </section>

      <section className="feature-content">
        <div className="feature-toolbar">
          <div>
            <h2>Published artifacts</h2>
          </div>
          <div className="feature-toolbar-actions">
            <span className="panel-meta">{response?.total ?? 0} matched</span>
            <Tooltip content="Upload a local file as a published artifact.">
              <button type="button" className="toolbar-icon-button" aria-label="Upload artifact" onClick={openUploadDialog}>
                <UploadIcon />
              </button>
            </Tooltip>
            <button type="button" onClick={() => void loadArtifacts()} disabled={loading}>
              Refresh
            </button>
          </div>
        </div>

        {error ? <div className="callout error-callout">{error}</div> : null}
        {uploadMessage ? <div className="callout success-callout">{uploadMessage}</div> : null}

        <div className="admin-grid two-column artifacts-grid">
          <article className="panel section-panel">
            <div className="panel-header">
              <div>
                <h3>Archive list</h3>
                <p className="muted-text">
                  Showing {items.length} {items.length === 1 ? "archive" : "archives"}.
                </p>
              </div>
              <div className="feature-toolbar-actions">
                <button type="button" onClick={previousPage} disabled={previousCursors.length === 0 || loading}>
                  Previous
                </button>
                <button type="button" onClick={nextPage} disabled={!response?.next_cursor || loading}>
                  Next
                </button>
              </div>
            </div>
            <div className="table-wrap">
              <table className="admin-table artifacts-table">
                <thead>
                  <tr>
                    <th>Key</th>
                    <th>Published</th>
                    <th>Session</th>
                    <th>Size</th>
                    <th>Files</th>
                    <th>SHA-256</th>
                    <th>Download</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((item) => (
                    <tr
                      key={item.key}
                      className={item.key === selectedArtifact?.key ? "selected" : ""}
                      onClick={() => {
                        setSelectedKey(item.key);
                        setResolvedArtifact(null);
                      }}
                    >
                      <td><strong>{item.key}</strong></td>
                      <td>{formatTimestamp(item.published_at)}</td>
                      <td>{item.session_id}</td>
                      <td>{formatBytes(item.byte_count)}</td>
                      <td>{item.source_file_count}</td>
                      <td>{shortSha(item.sha256)}</td>
                      <td>
                        <div className="feature-toolbar-actions icon-actions artifacts-download-cell">
                          <button
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
                      <td className="empty-state-cell" colSpan={7}>Loading artifacts...</td>
                    </tr>
                  ) : null}
                  {!loading && items.length === 0 ? (
                    <tr>
                      <td className="empty-state-cell" colSpan={7}>No artifacts matched the current filters.</td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </div>
          </article>

          <article className="panel detail-panel artifacts-detail-panel">
            <div className="panel-header">
              <div>
                <h3>{selectedArtifact?.key ?? "Select an artifact"}</h3>
                <p className="muted-text">
                  {selectedArtifact ? selectedArtifact.filename : "No archive selected."}
                </p>
              </div>
              {selectedArtifact ? (
                <div className="feature-toolbar-actions icon-actions">
                  <button type="button" aria-label="Download selected artifact" onClick={() => downloadArtifact(selectedArtifact.key)}>
                    <DownloadIcon />
                  </button>
                </div>
              ) : null}
            </div>
            {selectedArtifact ? (
              <div className="artifact-detail-stack">
                <dl className="detail-list">
                  <div>
                    <dt>Published</dt>
                    <dd>{formatTimestamp(selectedArtifact.published_at)}</dd>
                  </div>
                  <div>
                    <dt>Session</dt>
                    <dd>{selectedArtifact.session_id}</dd>
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
                <label className="field-stack artifact-metadata-field">
                  <span>Metadata</span>
                  <textarea readOnly value={JSON.stringify(selectedArtifact.metadata ?? {}, null, 2)} />
                </label>
              </div>
            ) : (
              <div className="empty-state">No artifact selected.</div>
            )}
          </article>
        </div>
      </section>
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
              <button type="button" onClick={closeUploadDialog} disabled={uploading}>
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
                  <button type="button" onClick={closeUploadDialog} disabled={uploading}>
                    Cancel
                  </button>
                  <button type="submit" className="primary" disabled={uploading}>
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

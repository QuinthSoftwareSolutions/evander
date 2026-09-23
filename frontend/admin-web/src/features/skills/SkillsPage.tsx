import { FormEvent, useEffect, useMemo, useState } from "react";

import { ApiError, fetchJson, formatTimestamp } from "../admin/api";
import { useAdminUi } from "../admin/AdminContext";
import { RecordWorkspace } from "../admin/RecordWorkspace";
import { readAdminHashQuery } from "../admin/hash";
import type {
  SkillLibraryDetail,
  SkillLibraryItem,
  SkillListResponse,
  SkillMutationResponse,
  SkillResponse,
} from "../admin/types";

type SkillWorkflowReference = {
  scope: string | null;
  path: string | null;
  workflow_pack: string | null;
  node_id: string | null;
  node_type: string | null;
  display_name: string | null;
  field_path: string | null;
  reference_count: number | null;
};

type SkillDeleteConflictState = {
  references: SkillWorkflowReference[];
};

async function loadSkills(): Promise<SkillLibraryItem[]> {
  const payload = await fetchJson<SkillListResponse>("/api/admin/skills");
  return payload.items;
}

async function loadSkill(name: string): Promise<SkillLibraryDetail> {
  const payload = await fetchJson<SkillResponse>(
    `/api/admin/skills/${encodeURIComponent(name)}`,
  );
  return payload.skill;
}

function formatBytes(value: number): string {
  if (!Number.isFinite(value)) return "0 B";
  if (value < 1024) return `${value} B`;
  return `${(value / 1024).toFixed(1)} KB`;
}

function skillNameFromFileName(fileName: string): string {
  return fileName.replace(/\.md$/i, "").trim();
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function optionalString(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

function optionalNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function skillWorkflowReferenceFromPayload(value: unknown): SkillWorkflowReference | null {
  if (!isRecord(value)) return null;
  return {
    scope: optionalString(value.scope),
    path: optionalString(value.path),
    workflow_pack: optionalString(value.workflow_pack),
    node_id: optionalString(value.node_id),
    node_type: optionalString(value.node_type),
    display_name: optionalString(value.display_name),
    field_path: optionalString(value.field_path),
    reference_count: optionalNumber(value.reference_count),
  };
}

function skillDeleteConflictFromError(error: unknown): SkillDeleteConflictState | null {
  if (!(error instanceof ApiError) || error.status !== 409 || !isRecord(error.payload)) return null;
  const details = error.payload.details;
  if (!isRecord(details) || !Array.isArray(details.references)) return null;
  const references = details.references
    .map(skillWorkflowReferenceFromPayload)
    .filter((reference): reference is SkillWorkflowReference => reference !== null);
  if (references.length === 0) return null;
  return {
    references,
  };
}

function workflowReferenceTitle(reference: SkillWorkflowReference): string {
  const label = reference.display_name ?? reference.node_id ?? "Workflow node";
  return reference.node_type ? `${label} (${reference.node_type})` : label;
}

function workflowReferenceLocation(reference: SkillWorkflowReference): string {
  const parts: string[] = [];
  if (reference.workflow_pack) {
    parts.push(`Pack ${reference.workflow_pack}`);
  } else if (reference.scope === "inline") {
    parts.push("Inline workflow config");
  } else if (reference.scope) {
    parts.push(reference.scope);
  }
  if (reference.path) parts.push(reference.path);
  if (reference.field_path) parts.push(reference.field_path);
  if (reference.reference_count && reference.reference_count > 1) {
    parts.push(`${reference.reference_count} references`);
  }
  return parts.join(" / ") || "Workflow reference";
}

export function SkillsPage() {
  const { privilegedUnlocked } = useAdminUi();
  const mutationLocked = !privilegedUnlocked;
  const [items, setItems] = useState<SkillLibraryItem[]>([]);
  const [selectedName, setSelectedName] = useState<string | null>(() => readAdminHashQuery().get("skill"));
  const [selectedSkill, setSelectedSkill] = useState<SkillLibraryDetail | null>(null);
  const [uploadName, setUploadName] = useState("");
  const [uploadContent, setUploadContent] = useState("");
  const [uploadFileName, setUploadFileName] = useState("");
  const [editContent, setEditContent] = useState("");
  const [replaceExisting, setReplaceExisting] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const [deleteConflict, setDeleteConflict] = useState<SkillDeleteConflictState | null>(null);

  const selectedSummary = useMemo(
    () => items.find((item) => item.name === selectedName) ?? null,
    [items, selectedName],
  );

  async function refreshSkills(nextSelectedName?: string | null) {
    const nextItems = await loadSkills();
    setItems(nextItems);
    setSelectedName((current) => {
      if (nextSelectedName !== undefined) return nextSelectedName;
      if (current && nextItems.some((item) => item.name === current)) return current;
      return nextItems[0]?.name ?? null;
    });
    setError(null);
    setDeleteConflict(null);
  }

  useEffect(() => {
    let cancelled = false;
    const refresh = async () => {
      try {
        const nextItems = await loadSkills();
        if (cancelled) return;
        setItems(nextItems);
        setSelectedName((current) =>
          current && nextItems.some((item) => item.name === current)
            ? current
            : nextItems[0]?.name ?? null,
        );
        setError(null);
        setDeleteConflict(null);
      } catch (reason: unknown) {
        if (!cancelled) {
          setError(reason instanceof Error ? reason.message : String(reason));
          setDeleteConflict(null);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void refresh();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!selectedName) {
      setSelectedSkill(null);
      setEditContent("");
      return;
    }
    let cancelled = false;
    const refresh = async () => {
      try {
        const detail = await loadSkill(selectedName);
        if (cancelled) return;
        setSelectedSkill(detail);
        setEditContent(detail.content);
        setError(null);
        setDeleteConflict(null);
      } catch (reason: unknown) {
        if (!cancelled) {
          setError(reason instanceof Error ? reason.message : String(reason));
          setDeleteConflict(null);
        }
      }
    };
    void refresh();
    return () => {
      cancelled = true;
    };
  }, [selectedName]);

  async function handleFileChange(file: File | null) {
    if (!file) return;
    if (!file.name.toLowerCase().endsWith(".md")) {
      setError("Only Markdown files are accepted.");
      setDeleteConflict(null);
      return;
    }
    setUploadFileName(file.name);
    setUploadContent(await file.text());
    setUploadName((current) => current || skillNameFromFileName(file.name));
    setError(null);
    setDeleteConflict(null);
  }

  async function handleUpload(event: FormEvent) {
    event.preventDefault();
    if (mutationLocked) return;
    setBusy("upload");
    try {
      const payload = await fetchJson<SkillMutationResponse>("/api/admin/skills", {
        method: "POST",
        body: JSON.stringify({
          name: uploadName.trim(),
          content: uploadContent,
          replace: replaceExisting,
        }),
      });
      setStatus(`Saved ${payload.skill.name}.`);
      setUploadContent("");
      setUploadFileName("");
      setReplaceExisting(false);
      await refreshSkills(payload.skill.name);
      setError(null);
      setDeleteConflict(null);
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : String(reason));
      setDeleteConflict(null);
      setStatus(null);
    } finally {
      setBusy(null);
    }
  }

  async function handleReplace() {
    if (mutationLocked) return;
    if (!selectedSkill) return;
    setBusy("replace");
    try {
      const payload = await fetchJson<SkillMutationResponse>("/api/admin/skills", {
        method: "POST",
        body: JSON.stringify({
          name: selectedSkill.name,
          content: editContent,
          replace: true,
        }),
      });
      setStatus(`Saved ${payload.skill.name}.`);
      await refreshSkills(payload.skill.name);
      setError(null);
      setDeleteConflict(null);
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : String(reason));
      setDeleteConflict(null);
      setStatus(null);
    } finally {
      setBusy(null);
    }
  }

  async function handleDelete() {
    if (mutationLocked) return;
    if (!selectedSkill) return;
    if (!window.confirm(`Delete ${selectedSkill.name}?`)) return;
    setBusy("delete");
    try {
      const deletedName = selectedSkill.name;
      await fetchJson<SkillMutationResponse>(
        `/api/admin/skills/${encodeURIComponent(deletedName)}`,
        { method: "DELETE" },
      );
      setStatus(`Deleted ${deletedName}.`);
      await refreshSkills(null);
      setError(null);
      setDeleteConflict(null);
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : String(reason));
      setDeleteConflict(skillDeleteConflictFromError(reason));
      setStatus(null);
    } finally {
      setBusy(null);
    }
  }

  return (
    <RecordWorkspace
      title="Skills"
      description="Search the managed skill library. Open a skill to read its content, update it, or inspect workflow references."
      columns={["Skill", "Size", "Updated"]}
      records={items.map((item) => ({
        id: item.name,
        title: item.name,
        href: `#skills?${new URLSearchParams({ skill: item.name })}`,
        cells: [formatBytes(item.size_bytes), formatTimestamp(item.updated_at)],
        facts: [
          ["Name", item.name],
          ["Size", formatBytes(item.size_bytes)],
          ["Updated", formatTimestamp(item.updated_at)],
        ],
      }))}
      selectedId={selectedName}
      onSelect={setSelectedName}
      loading={loading}
      error={error}
      initialRecordOpen={Boolean(readAdminHashQuery().get("skill"))}
      recordRouteKeys="skill"
      actions={(openRecord) => (
        <>
          <button type="button" disabled={loading || busy !== null} onClick={() => void refreshSkills()}>
            Refresh
          </button>
          <button
            type="button"
            className="primary"
            disabled={mutationLocked}
            title={mutationLocked ? "Unlock privileged operations to upload skills." : undefined}
            onClick={openRecord}
          >
            Upload skill
          </button>
        </>
      )}
    >
      <div className="feature-page skills-page">
        <section className="feature-sidebar">
          <div className="feature-sidebar-header">
            <div>
              <div className="eyebrow">Managed library</div>
              <h1>Skills</h1>
            </div>
            <div className="inline-actions compact-actions">
              <button type="button" onClick={() => void refreshSkills()} disabled={loading || busy !== null}>
                Refresh
              </button>
            </div>
          </div>
          <div className="feature-list skill-list">
            {items.map((item) => (
              <button
                key={item.name}
                type="button"
                className={`feature-list-item skill-list-item${selectedName === item.name ? " selected" : ""}`}
                onClick={() => setSelectedName(item.name)}
              >
                <div className="feature-list-title-row">
                  <strong>{item.name}</strong>
                  <span className="status-pill">{formatBytes(item.size_bytes)}</span>
                </div>
                <div className="feature-list-meta">{formatTimestamp(item.updated_at)}</div>
              </button>
            ))}
            {!loading && items.length === 0 ? <div className="empty-state">No skills found.</div> : null}
          </div>
        </section>

        <section className="feature-content">
          <div className="feature-toolbar">
            <div>
              <h2>{selectedSkill?.name ?? "Managed skills"}</h2>
              <p className="muted-text">
                {selectedSummary
                  ? `${formatBytes(selectedSummary.size_bytes)} / ${formatTimestamp(selectedSummary.updated_at)}`
                  : "Global Codex skills available to agent creation flows."}
              </p>
            </div>
          </div>

          {error ? (
            <div className="callout warning-callout">
              <div>{error}</div>
              {deleteConflict ? (
                <div className="skill-delete-conflict">
                  <div className="skill-delete-conflict-heading">Workflow references</div>
                  <ul className="skill-delete-reference-list" aria-label="Workflow references">
                    {deleteConflict.references.map((reference, index) => (
                      <li key={`${reference.workflow_pack ?? "inline"}:${reference.node_id ?? index}:${index}`}>
                        <strong>{workflowReferenceTitle(reference)}</strong>
                        <span>{workflowReferenceLocation(reference)}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              ) : null}
            </div>
          ) : null}
          {status ? <div className="callout">{status}</div> : null}

          <div className="detail-grid skills-detail-grid">
            <article className="panel">
              <div className="panel-header">
                <h3>Upload</h3>
                <span className="panel-meta">{uploadFileName || "Markdown"}</span>
              </div>
              <form className="form-grid" onSubmit={(event) => void handleUpload(event)}>
                <label className="field-stack">
                  <span>Name</span>
                  <input
                    value={uploadName}
                    onChange={(event) => setUploadName(event.target.value)}
                    placeholder="skill-name"
                  />
                </label>
                <label className="field-stack">
                  <span>File</span>
                  <input
                    type="file"
                    accept=".md,text/markdown"
                    onChange={(event) => void handleFileChange(event.target.files?.[0] ?? null)}
                  />
                </label>
                <label className="checkbox-row field-span-2">
                  <input
                    type="checkbox"
                    checked={replaceExisting}
                    onChange={(event) => setReplaceExisting(event.target.checked)}
                  />
                  <span>Replace existing</span>
                </label>
                <label className="field-stack field-span-2">
                  <span>Content</span>
                  <textarea
                    rows={10}
                    value={uploadContent}
                    onChange={(event) => setUploadContent(event.target.value)}
                  />
                </label>
                <div className="inline-actions compact-actions field-span-2">
                  <button
                    className="primary"
                    type="submit"
                    disabled={mutationLocked || busy !== null || !uploadName.trim() || !uploadContent.trim()}
                  >
                    {busy === "upload" ? "Saving..." : "Save"}
                  </button>
                </div>
              </form>
            </article>

            <article className="panel">
              <div className="panel-header">
                <h3>Edit</h3>
                <span className="panel-meta">{selectedSkill?.name ?? "None selected"}</span>
              </div>
              {selectedSkill ? (
                <div className="field-stack">
                  <textarea
                    className="skill-editor"
                    rows={18}
                    value={editContent}
                    onChange={(event) => setEditContent(event.target.value)}
                  />
                  <div className="inline-actions compact-actions">
                    <button
                      type="button"
                      className="primary"
                      onClick={() => void handleReplace()}
                      disabled={mutationLocked || busy !== null || !editContent.trim()}
                    >
                      {busy === "replace" ? "Saving..." : "Replace"}
                    </button>
                    <button
                      type="button"
                      className="danger"
                      onClick={() => void handleDelete()}
                      disabled={mutationLocked || busy !== null}
                    >
                      {busy === "delete" ? "Deleting..." : "Delete"}
                    </button>
                  </div>
                </div>
              ) : (
                <div className="empty-state">Select a skill to preview or edit.</div>
              )}
            </article>
          </div>
        </section>
      </div>
    </RecordWorkspace>
  );
}

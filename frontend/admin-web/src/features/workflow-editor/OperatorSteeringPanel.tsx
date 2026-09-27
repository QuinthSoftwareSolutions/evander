import { type Dispatch, type SetStateAction } from "react";
import { type WorkflowRunAgentLink } from "../../types";
import { type OperatorSteeringNote } from "./editorTypes";
import { operatorSteeringNoteSummary } from "./RunDetailContent";
import { operatorSteeringKey } from "./runDetailsModel";

type OperatorSteeringPanelProps = {
  agent: WorkflowRunAgentLink;
  operatorSteeringNotesByKey: Record<string, OperatorSteeringNote[]>;
  operatorSteeringDraftsByKey: Record<string, string>;
  operatorSteeringLoadingByKey: Record<string, boolean>;
  operatorSteeringSubmittingByKey: Record<string, boolean>;
  operatorSteeringErrorsByKey: Record<string, string>;
  canControlWorkflowRuns: boolean;
  privilegedUnlocked: boolean;
  submitOperatorSteeringNote: (agent: WorkflowRunAgentLink) => Promise<void>;
  setOperatorSteeringDraftsByKey: Dispatch<SetStateAction<Record<string, string>>>;
  setOperatorSteeringError: (key: string, message: string | null) => void;
  loadOperatorSteeringNotes: (sessionId: string, dispatchId: string) => Promise<void>;
  operatorSteeringCancellingById: Record<string, boolean>;
  cancelOperatorSteeringNote: (note: OperatorSteeringNote) => Promise<void>;
};

export function OperatorSteeringPanel({
  agent,
  operatorSteeringNotesByKey,
  operatorSteeringDraftsByKey,
  operatorSteeringLoadingByKey,
  operatorSteeringSubmittingByKey,
  operatorSteeringErrorsByKey,
  canControlWorkflowRuns,
  privilegedUnlocked,
  submitOperatorSteeringNote,
  setOperatorSteeringDraftsByKey,
  setOperatorSteeringError,
  loadOperatorSteeringNotes,
  operatorSteeringCancellingById,
  cancelOperatorSteeringNote,
}: OperatorSteeringPanelProps) {
  const sessionId = agent.session_id?.trim() ?? "";
  const dispatchId = agent.dispatch_id?.trim() ?? "";
  const key = operatorSteeringKey(sessionId, dispatchId);
  if (!key) return null;
  const notes = operatorSteeringNotesByKey[key] ?? [];
  const draft = operatorSteeringDraftsByKey[key] ?? "";
  const loading = Boolean(operatorSteeringLoadingByKey[key]);
  const submitting = Boolean(operatorSteeringSubmittingByKey[key]);
  const error = operatorSteeringErrorsByKey[key] ?? null;
  const latestNote = notes[0] ?? null;
  const canSubmitSteering =
    canControlWorkflowRuns &&
    privilegedUnlocked &&
    !submitting &&
    draft.trim().length > 0;

  return (
    <div className="operator-steering-panel" title="Delivered on the next model call for this session and dispatch.">
      <div className="operator-steering-header">
        <strong>Operator steering</strong>
        <span>{loading ? "Loading" : latestNote ? operatorSteeringNoteSummary(latestNote) : "No notes"}</span>
      </div>
      {canControlWorkflowRuns ? (
        <form
          className="operator-steering-form"
          onSubmit={(event) => {
            event.preventDefault();
            void submitOperatorSteeringNote(agent);
          }}
        >
          <textarea
            value={draft}
            rows={3}
            maxLength={2000}
            placeholder="Move from exploration to implementation."
            disabled={!privilegedUnlocked || submitting}
            onChange={(event) => {
              setOperatorSteeringDraftsByKey((current) => ({ ...current, [key]: event.target.value }));
              if (operatorSteeringErrorsByKey[key]) setOperatorSteeringError(key, null);
            }}
          />
          <div className="operator-steering-actions">
            <button type="submit" className="primary" disabled={!canSubmitSteering}>
              {submitting ? "Queuing..." : "Queue note"}
            </button>
            <button
              type="button"
              className="subtle-button"
              disabled={loading}
              onClick={() => {
                void loadOperatorSteeringNotes(sessionId, dispatchId);
              }}
            >
              Refresh
            </button>
          </div>
        </form>
      ) : null}
      {canControlWorkflowRuns && !privilegedUnlocked ? (
        <span className="operator-steering-lock">Privileged operations locked</span>
      ) : null}
      {error ? <div className="runtime-run-error">{error}</div> : null}
      {notes.length > 0 ? (
        <div className="operator-steering-notes">
          {notes.slice(0, 4).map((note) => {
            const cancelling = Boolean(operatorSteeringCancellingById[note.note_id]);
            return (
              <div className={`operator-steering-note status-${note.status}`} key={note.note_id}>
                <span>{operatorSteeringNoteSummary(note)}</span>
                {note.provider ? <span>{note.provider}{note.provider_endpoint ? ` / ${note.provider_endpoint}` : ""}</span> : null}
                {note.error ? <span className="missing">{note.error}</span> : null}
                {note.status === "pending" && canControlWorkflowRuns ? (
                  <button
                    type="button"
                    className="subtle-button"
                    disabled={!privilegedUnlocked || cancelling}
                    onClick={() => {
                      void cancelOperatorSteeringNote(note);
                    }}
                  >
                    {cancelling ? "Cancelling..." : "Cancel"}
                  </button>
                ) : null}
              </div>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}

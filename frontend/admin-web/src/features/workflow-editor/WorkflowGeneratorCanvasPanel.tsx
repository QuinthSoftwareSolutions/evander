import { type WorkflowConfig } from "../../types";
import { WORKFLOW_GENERATOR_MIN_DESCRIPTION_LENGTH } from "./editorConstants";
import { type WorkflowGeneratorState } from "./editorTypes";
import { workflowNodeIds } from "./graphModel";

type WorkflowGeneratorCanvasPanelProps = {
  workflowGeneratorOpen: boolean;
  config: WorkflowConfig;
  workflowGeneratorDescription: string;
  workflowGeneratorState: WorkflowGeneratorState;
  workflowEditLocked: boolean;
  workflowGeneratorReadyDescription: string | null;
  workflowGeneratorModel: string | null;
  handleWorkflowGeneratorDescriptionChange: (value: string) => void;
  workflowGeneratorError: string | null;
  workflowGeneratorWarnings: string[];
  generateWorkflowGeneratorDraft: () => Promise<void>;
  acceptWorkflowGeneratorDraft: () => void;
  cancelWorkflowGenerator: () => void;
};

export function WorkflowGeneratorCanvasPanel({
  workflowGeneratorOpen,
  config,
  workflowGeneratorDescription,
  workflowGeneratorState,
  workflowEditLocked,
  workflowGeneratorReadyDescription,
  workflowGeneratorModel,
  handleWorkflowGeneratorDescriptionChange,
  workflowGeneratorError,
  workflowGeneratorWarnings,
  generateWorkflowGeneratorDraft,
  acceptWorkflowGeneratorDraft,
  cancelWorkflowGenerator,
}: WorkflowGeneratorCanvasPanelProps) {
  if (!workflowGeneratorOpen) return null;
  const generatedNodeCount = workflowNodeIds(config).length;
  const generatorDescription = workflowGeneratorDescription.trim();
  const canGenerateDraft =
    workflowGeneratorOpen &&
    workflowGeneratorState !== "generating" &&
    !workflowEditLocked &&
    generatorDescription.length >= WORKFLOW_GENERATOR_MIN_DESCRIPTION_LENGTH;
  const generatedDraftMatchesDescription =
    workflowGeneratorState === "ready" &&
    workflowGeneratorReadyDescription === generatorDescription;
  const canAcceptGeneratedDraft =
    workflowGeneratorOpen && generatedNodeCount > 0 && generatedDraftMatchesDescription;
  const generatorStatus =
    workflowGeneratorState === "idle"
      ? "Blank draft"
      : workflowGeneratorState === "draftable"
        ? generatorDescription.length >= WORKFLOW_GENERATOR_MIN_DESCRIPTION_LENGTH
          ? "Ready to generate"
          : "Add detail"
        : workflowGeneratorState === "generating"
          ? "Generating"
          : generatedDraftMatchesDescription
            ? `Generated${workflowGeneratorModel ? ` with ${workflowGeneratorModel}` : ""}`
            : workflowGeneratorState === "ready"
              ? "Draft out of date"
              : "Generation failed";
  return (
    <section className="workflow-generator-canvas-panel" role="dialog" aria-modal="false" aria-label="Generate workflow">
      <div className="workflow-generator-header">
        <div>
          <div className="section-label">Generate</div>
          <h2>Generate workflow</h2>
          <p>Draft workflow canvas</p>
        </div>
        <span className={`workflow-generator-status state-${workflowGeneratorState}`}>
          {generatorStatus}
        </span>
      </div>
      <div className="workflow-generator-form">
        <label className="inspector-label workflow-generator-prompt">
          <span>Description</span>
          <textarea
            value={workflowGeneratorDescription}
            rows={7}
            placeholder="Describe the workflow to draft."
            onChange={(event) => handleWorkflowGeneratorDescriptionChange(event.target.value)}
            disabled={workflowEditLocked}
            autoFocus
          />
        </label>
        {workflowGeneratorError ? <div className="runtime-run-error">{workflowGeneratorError}</div> : null}
        {workflowGeneratorWarnings.length > 0 ? (
          <div className="workflow-generator-warnings">
            {workflowGeneratorWarnings.slice(0, 4).map((warning, index) => (
              <div key={`${warning}-${index}`}>{warning}</div>
            ))}
          </div>
        ) : null}
        <div className="workflow-generator-actions">
          <button
            type="button"
            className="primary"
            onClick={generateWorkflowGeneratorDraft}
            disabled={!canGenerateDraft}
          >
            Generate draft
          </button>
          <button
            type="button"
            onClick={acceptWorkflowGeneratorDraft}
            disabled={!canAcceptGeneratedDraft || workflowEditLocked}
          >
            Accept draft
          </button>
          <button type="button" className="subtle-button" onClick={cancelWorkflowGenerator}>
            Cancel
          </button>
        </div>
      </div>
    </section>
  );
}

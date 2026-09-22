import { type Dispatch, type SetStateAction } from "react";
import { WORKFLOW_TUTORIAL_DEFINITIONS } from "../../workflowTutorials";
import { type WorkflowTutorialChooserStatus } from "./editorTypes";

type TutorialPanelRenderersInputs = {
  workflowTutorialChooserOpen: boolean;
  setWorkflowTutorialChooserOpen: Dispatch<SetStateAction<boolean>>;
  workflowTutorialChooserStatusFor: (tutorialId: string) => WorkflowTutorialChooserStatus;
  startWorkflowTutorial: (tutorialId?: string) => void;
  restartWorkflowTutorial: (tutorialId?: string) => void;
  skipWorkflowTutorial: (tutorialId?: string) => void;
  clearWorkflowTutorialProgressStatus: (tutorialId: string) => void;
  workflowTutorialChooserStatusLabel: (status: WorkflowTutorialChooserStatus) => string;
  hasUnfinishedUnskippedTutorials: boolean;
  unfinishedUnskippedTutorialCount: number;
};

/** Binds tutorial panel renderers to the current render state. */
export function createTutorialPanelRenderers({
  workflowTutorialChooserOpen,
  setWorkflowTutorialChooserOpen,
  workflowTutorialChooserStatusFor,
  startWorkflowTutorial,
  restartWorkflowTutorial,
  skipWorkflowTutorial,
  clearWorkflowTutorialProgressStatus,
  workflowTutorialChooserStatusLabel,
  hasUnfinishedUnskippedTutorials,
  unfinishedUnskippedTutorialCount,
}: TutorialPanelRenderersInputs) {
  function renderWorkflowTutorialChooser() {
    if (!workflowTutorialChooserOpen) return null;
    return (
      <div
        className="modal-backdrop workflow-tutorial-chooser-backdrop"
        role="presentation"
        onClick={(event) => {
          if (event.target === event.currentTarget) {
            setWorkflowTutorialChooserOpen(false);
          }
        }}
      >
        <div className="modal-card workflow-tutorial-chooser-modal" role="dialog" aria-modal="true" aria-label="Tutorials">
          <div className="modal-header workflow-tutorial-chooser-header">
            <div>
              <div className="section-label">Tutorials</div>
              <h2>Choose a walkthrough</h2>
              <p>Start, restart, skip, or clear local tutorial status without touching the saved workflow.</p>
            </div>
            <button type="button" className="subtle-button" onClick={() => setWorkflowTutorialChooserOpen(false)}>
              Close
            </button>
          </div>
          <div className="workflow-tutorial-chooser-body">
            <div className="workflow-tutorial-chooser-list" aria-label="Tutorial chooser">
              {WORKFLOW_TUTORIAL_DEFINITIONS.map((definition) => {
                const status = workflowTutorialChooserStatusFor(definition.id);
                const isActive = status === "in-progress";
                const primaryAction = status === "available"
                  ? () => startWorkflowTutorial(definition.id)
                  : () => restartWorkflowTutorial(definition.id);
                const secondaryAction = status === "available" || isActive
                  ? () => skipWorkflowTutorial(definition.id)
                  : () => clearWorkflowTutorialProgressStatus(definition.id);
                return (
                  <article key={definition.id} className={`workflow-tutorial-chooser-item status-${status}`}>
                    <div className="workflow-tutorial-chooser-item-header">
                      <div>
                        <h3>{definition.title}</h3>
                        <p>{definition.description}</p>
                      </div>
                      <span className="workflow-tutorial-chooser-status">{workflowTutorialChooserStatusLabel(status)}</span>
                    </div>
                    <div className="workflow-tutorial-chooser-actions">
                      <button type="button" className="primary" onClick={primaryAction}>
                        {status === "available" ? "Start" : "Restart"}
                      </button>
                      <button type="button" className="subtle-button" onClick={secondaryAction}>
                        {status === "available" || isActive ? "Skip" : "Clear status"}
                      </button>
                    </div>
                  </article>
                );
              })}
            </div>
          </div>
        </div>
      </div>
    );
  }

  function renderWorkflowTutorialLauncher() {
    const tutorialLauncherHasAttention = hasUnfinishedUnskippedTutorials;
    const tutorialLauncherStatusLabel = tutorialLauncherHasAttention
      ? `${unfinishedUnskippedTutorialCount} available`
      : "All done";
    const tutorialLauncherAriaLabel = tutorialLauncherHasAttention
      ? `Open tutorials chooser, ${unfinishedUnskippedTutorialCount} tutorial${unfinishedUnskippedTutorialCount === 1 ? "" : "s"} available`
      : "Open tutorials chooser, all tutorials completed or skipped";
    return (
      <div className="workflow-tutorial-launcher">
        <button
          type="button"
          className={`workflow-tutorial-launcher-button${tutorialLauncherHasAttention ? " needs-attention" : " is-complete"}`}
          aria-haspopup="dialog"
          aria-expanded={workflowTutorialChooserOpen}
          aria-label={tutorialLauncherAriaLabel}
          onClick={() => {
            setWorkflowTutorialChooserOpen(true);
          }}
        >
          <span>Tutorials</span>
          <span className="workflow-tutorial-launcher-status">{tutorialLauncherStatusLabel}</span>
        </button>
      </div>
    );
  }

  return {
    renderWorkflowTutorialLauncher,
    renderWorkflowTutorialChooser,
  };
}

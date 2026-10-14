import React from "react";
import { createRoot } from "react-dom/client";
import WorkflowStartersPage from "../../src/features/starters/WorkflowStartersPage";
import { AdminUiContext, type AdminUiState } from "../../src/features/admin/AdminContext";
import "../../src/styles.css";

const query = new URLSearchParams(location.search);
document.documentElement.dataset.theme = query.get("theme") ?? "light";
createRoot(document.getElementById("root")!).render(<AdminUiContext.Provider value={{
  selectedDirectoryPath: "root", canWriteWorkflows: true, canManageWorkflowPacks: true,
  canReadWorkflowRuns: true, canControlWorkflowRuns: true, privilegedUnlocked: !query.has("locked"),
} as AdminUiState}><WorkflowStartersPage /></AdminUiContext.Provider>);

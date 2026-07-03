import { useEffect, useState } from "react";
import { createRoot, type Root } from "react-dom/client";

import { AgentCreateDialog } from "./features/agents/AgentCreateDialog";
import {
  createdSessionIdFromOperation,
  createOperationIsActive,
  loadAgentCreateOperation,
  submitAgentCreateOperation,
} from "./features/agents/agentCreateShared";
import type { AgentCreateOperation, AgentCreateRequest } from "./features/admin/types";
import "./teamAgentCreate.css";

type TeamAgentCreateOpenDetail = {
  contextId?: string | null;
  directoryPath?: string | null;
};

type TeamAgentCreatedDetail = {
  contextId: string | null;
  sessionId: string;
  targetName: string;
  operation: AgentCreateOperation;
};

declare global {
  interface Window {
    openTeamAgentCreateDialog?: (detail?: TeamAgentCreateOpenDetail) => void;
  }
}

const POLL_INTERVAL_MS = 1500;
const POLL_TIMEOUT_MS = 10 * 60 * 1000;

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

async function waitForCreateOperation(operation: AgentCreateOperation): Promise<AgentCreateOperation> {
  let current = operation;
  const deadline = Date.now() + POLL_TIMEOUT_MS;
  while (createOperationIsActive(current)) {
    if (Date.now() >= deadline) {
      throw new Error(`Timed out waiting for ${current.target_name} to finish creating.`);
    }
    await sleep(POLL_INTERVAL_MS);
    current = await loadAgentCreateOperation(current.operation_id);
  }
  if (current.status === "failed") {
    throw new Error(current.error || `Agent create failed for ${current.target_name}.`);
  }
  return current;
}

function dispatchCreated(detail: TeamAgentCreatedDetail) {
  window.dispatchEvent(new CustomEvent("team-agent-create:created", { detail }));
}

function TeamAgentCreateHost() {
  const [request, setRequest] = useState<TeamAgentCreateOpenDetail | null>(null);
  const [progress, setProgress] = useState("");

  useEffect(() => {
    const openDialog = (detail?: TeamAgentCreateOpenDetail) => {
      setProgress("");
      setRequest({
        contextId: detail?.contextId ?? null,
        directoryPath: detail?.directoryPath ?? "root",
      });
    };
    window.openTeamAgentCreateDialog = openDialog;
    const listener = (event: Event) => {
      openDialog((event as CustomEvent<TeamAgentCreateOpenDetail>).detail ?? {});
    };
    window.addEventListener("team-agent-create:open", listener);
    return () => {
      if (window.openTeamAgentCreateDialog === openDialog) {
        delete window.openTeamAgentCreateDialog;
      }
      window.removeEventListener("team-agent-create:open", listener);
    };
  }, []);

  if (!request) {
    return null;
  }

  async function handleCreate(payload: AgentCreateRequest) {
    setProgress("Queueing core agent session.");
    const submitted = await submitAgentCreateOperation(payload);
    setProgress(`Creating ${submitted.target_name}.`);
    const operation = await waitForCreateOperation(submitted);
    const sessionId = createdSessionIdFromOperation(operation);
    if (!sessionId) {
      throw new Error(`Created ${operation.target_name}, but the create operation did not return a session id.`);
    }
    setProgress("Registering Team agent.");
    dispatchCreated({
      contextId: request?.contextId ?? null,
      sessionId,
      targetName: operation.target_name,
      operation,
    });
  }

  return (
    <AgentCreateDialog
      directoryPath={request.directoryPath?.trim() || "root"}
      title="Create and register agent"
      description={progress || "Create a VM or container session, then Team registration will continue automatically."}
      submitLabel="Create and register"
      submittingLabel="Creating..."
      privilegedUnlocked
      closeOnSuccess
      onClose={() => {
        setRequest(null);
        setProgress("");
      }}
      onCreate={handleCreate}
    />
  );
}

function ensureRoot(): Element {
  let root = document.querySelector("#team-agent-create-root");
  if (!root) {
    root = document.createElement("div");
    root.id = "team-agent-create-root";
    document.body.appendChild(root);
  }
  return root;
}

let root: Root | null = null;

function mountTeamAgentCreate() {
  const container = ensureRoot();
  if (!root) {
    root = createRoot(container);
  }
  root.render(<TeamAgentCreateHost />);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", mountTeamAgentCreate, { once: true });
} else {
  mountTeamAgentCreate();
}

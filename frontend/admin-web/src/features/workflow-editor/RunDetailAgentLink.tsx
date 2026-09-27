import { type ReactNode } from "react";
import { type WorkflowRunAgentLink } from "../../types";
import { navigateToAdminHash } from "./RunDetailContent";
import {
  availabilityFlag,
  detailHashTarget,
  firstNonEmptyString,
  normalizeAvailabilityState,
  structuredValueSummary,
} from "./runDetailsModel";

type RunDetailAgentLinkProps = {
  agent: WorkflowRunAgentLink;
  index: number;
  renderOperatorSteeringPanel: (agent: WorkflowRunAgentLink) => ReactNode | null;
};

export function RunDetailAgentLink({
  agent,
  index,
  renderOperatorSteeringPanel,
}: RunDetailAgentLinkProps) {
  const sessionAvailable = availabilityFlag(agent.session_available ?? agent.session_status, Boolean(agent.session_id));
  const agentAvailable = availabilityFlag(
    agent.agent_available ?? agent.agent_status,
    Boolean(firstNonEmptyString(agent.agent_id, agent.execution_id, agent.target_name, agent.vm_name)),
  );
  const logAvailable = availabilityFlag(
    agent.log_available ?? agent.log_status,
    Boolean(agent.log_href || agent.log_reference_href),
  );
  const sessionHash = sessionAvailable && agent.session_id ? detailHashTarget("sessions", { session_id: agent.session_id }) : null;
  const agentTarget = agentAvailable
    ? detailHashTarget("agents", {
        agent_id: firstNonEmptyString(agent.agent_id, agent.execution_id, agent.target_name),
        session_id: agent.session_id,
      })
    : null;
  const logTarget = logAvailable
    ? detailHashTarget("agents", {
        agent_id: firstNonEmptyString(agent.agent_id, agent.execution_id, agent.target_name),
        session_id: agent.session_id,
      })
    : null;
  const promptSummary = firstNonEmptyString(
    agent.prompt_summary,
    agent.prompt,
    agent.prompt_missing_reason,
  ) ?? "Prompt summary missing";
  const resultSummary = agent.result_empty || normalizeAvailabilityState(agent.result_status) === "empty"
    ? "Empty result"
    : firstNonEmptyString(
        agent.result_summary,
        agent.result_missing_reason,
        agent.result_status && normalizeAvailabilityState(agent.result_status) !== "available"
          ? `Result payload ${agent.result_status}`
          : null,
      ) ?? "Result payload missing";
  const logReferenceSummary = agent.log_reference
    ? structuredValueSummary(agent.log_reference, "Log reference recorded", "Log reference recorded")
    : null;

  return (
    <div className="run-detail-link-item" key={`${agent.node_id}-${agent.session_id ?? "session-missing"}-${agent.execution_id ?? index}`}>
      <strong>Agent {agent.session_id ?? agent.vm_name ?? agent.node_id}</strong>
      <span>{agent.status ?? (agent.waiting ? "waiting" : agent.completed ? "completed" : "status missing")}</span>
      {agent.node_type ? <span>Node type {agent.node_type}</span> : null}
      {agent.dispatch_id ? <span>Dispatch {agent.dispatch_id}</span> : null}
      {agent.thread_id ? <span>Thread {agent.thread_id}</span> : null}
      {agent.execution_id ? <span>Execution {agent.execution_id}</span> : null}
      {sessionAvailable ? (
        <span>Session {agent.session_id ?? "missing"}</span>
      ) : (
        <span className="missing">{agent.session_missing_reason ?? "Session record missing"}</span>
      )}
      {agentAvailable && agentTarget ? <span>Agent target available</span> : null}
      {logAvailable ? (
        <span>{logReferenceSummary ?? "Log reference recorded"}</span>
      ) : (
        <span className="missing">{agent.log_missing_reason ?? "No persisted log reference"}</span>
      )}
      <span>Prompt {promptSummary}</span>
      <span>Result {resultSummary}</span>
      {agent.artifact_ids.length > 0 ? <span>Artifacts {agent.artifact_ids.join(", ")}</span> : <span className="missing">No communication artifact records</span>}
      <div className="run-detail-link-actions">
        {sessionHash ? (
          <button type="button" onClick={() => navigateToAdminHash(sessionHash)}>
            Session
          </button>
        ) : null}
        {agentTarget ? (
          <button type="button" onClick={() => navigateToAdminHash(agentTarget)}>
            Agent
          </button>
        ) : null}
        {logTarget ? (
          <button type="button" onClick={() => navigateToAdminHash(logTarget)}>
            Logs
          </button>
        ) : null}
      </div>
      {renderOperatorSteeringPanel(agent)}
    </div>
  );
}

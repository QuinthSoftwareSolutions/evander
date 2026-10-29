import type { AgentRecord } from "../admin/types";

export function mergeAgentDetailWithSummary(
  detail: AgentRecord,
  summary: AgentRecord,
): AgentRecord {
  const merged: AgentRecord = {
    ...detail,
    ...summary,
    host: {
      ...detail.host,
      ...summary.host,
    },
    capabilities: {
      ...detail.capabilities,
      ...summary.capabilities,
    },
  };
  const basePromptQueue = detail.prompt_queue ?? summary.prompt_queue;
  if (basePromptQueue) {
    merged.prompt_queue = {
      ...basePromptQueue,
      ...detail.prompt_queue,
      ...summary.prompt_queue,
    };
  }
  return merged;
}

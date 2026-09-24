import {
  useEffect,
  useRef,
  useState,
  type Dispatch,
  type MutableRefObject,
  type SetStateAction,
} from "react";
import {
  type WorkflowNodeConfig,
  type WorkflowNodeExecutionDetail,
  type WorkflowNodeExecutionFields,
  type WorkflowNodeExecutionFilter,
  type WorkflowNodeExecutionSearch,
} from "../../types";
import { fetchJson } from "./editorApi";
import { type WorkflowNodeExecutionFilterDraft, type WorkflowNodeExecutionQuery } from "./editorTypes";
import {
  workflowNodeExecutionDefaultEnd,
  workflowNodeExecutionDefaultStart,
  workflowNodeExecutionDefaultWindow,
  workflowNodeExecutionFilters,
} from "./runDetailsModel";

export type WorkflowNodeExecutionHistoryState = {
  nodeExecutionSearch: WorkflowNodeExecutionSearch | null;
  loadNodeExecutionHistory: (cursor?: string | null, append?: boolean) => Promise<void>;
  nodeExecutionStartedFrom: string;
  nodeExecutionWindowTouchedRef: MutableRefObject<boolean>;
  setNodeExecutionStartedFrom: Dispatch<SetStateAction<string>>;
  nodeExecutionStartedTo: string;
  setNodeExecutionStartedTo: Dispatch<SetStateAction<string>>;
  nodeExecutionStatuses: string;
  setNodeExecutionStatuses: Dispatch<SetStateAction<string>>;
  nodeExecutionFields: WorkflowNodeExecutionFields | null;
  nodeExecutionFilters: WorkflowNodeExecutionFilterDraft[];
  updateNodeExecutionFilter: (index: number, patch: Partial<WorkflowNodeExecutionFilterDraft>) => void;
  setNodeExecutionFilters: Dispatch<SetStateAction<WorkflowNodeExecutionFilterDraft[]>>;
  nodeExecutionLoading: boolean;
  nodeExecutionError: string | null;
  nodeExecutionDetailLoading: boolean;
  loadNodeExecutionDetail: (executionKey: string) => Promise<void>;
  nodeExecutionDetail: WorkflowNodeExecutionDetail | null;
};

/** Keeps query pagination and selection guards with execution-history state. */
export function useNodeExecutionHistory(selectedNode: WorkflowNodeConfig | undefined, workflowRunReadLocked: boolean): WorkflowNodeExecutionHistoryState {
  const selectedNodeIdRef = useRef<string | null>(null);

  const nodeExecutionQueryRef = useRef<WorkflowNodeExecutionQuery | null>(null);

  const nodeExecutionWindowTouchedRef = useRef(false);

  const [nodeExecutionStartedFrom, setNodeExecutionStartedFrom] = useState(
    workflowNodeExecutionDefaultStart,
  );

  const [nodeExecutionStartedTo, setNodeExecutionStartedTo] = useState(
    workflowNodeExecutionDefaultEnd,
  );

  const [nodeExecutionStatuses, setNodeExecutionStatuses] = useState("");

  const [nodeExecutionFilters, setNodeExecutionFilters] = useState<
    WorkflowNodeExecutionFilterDraft[]
  >([{ location: "output", path: "", operator: "eq", valueType: "string", value: "" }]);

  const [nodeExecutionSearch, setNodeExecutionSearch] = useState<WorkflowNodeExecutionSearch | null>(null);

  const [nodeExecutionFields, setNodeExecutionFields] = useState<WorkflowNodeExecutionFields | null>(null);

  const [nodeExecutionDetail, setNodeExecutionDetail] = useState<WorkflowNodeExecutionDetail | null>(null);

  const [nodeExecutionLoading, setNodeExecutionLoading] = useState(false);

  const [nodeExecutionDetailLoading, setNodeExecutionDetailLoading] = useState(false);

  const [nodeExecutionError, setNodeExecutionError] = useState<string | null>(null);

  useEffect(() => {
    selectedNodeIdRef.current = selectedNode?.id ?? null;
    const defaultWindow = workflowNodeExecutionDefaultWindow();
    nodeExecutionWindowTouchedRef.current = false;
    setNodeExecutionStartedFrom(defaultWindow.from);
    setNodeExecutionStartedTo(defaultWindow.to);
    setNodeExecutionSearch(null);
    nodeExecutionQueryRef.current = null;
    setNodeExecutionFields(null);
    setNodeExecutionDetail(null);
    setNodeExecutionError(null);
  }, [selectedNode?.id]);

  async function loadNodeExecutionHistory(cursor: string | null = null, append = false) {
    if (!selectedNode || nodeExecutionLoading || workflowRunReadLocked) return;
    const nodeId = selectedNode.id;
    let query: WorkflowNodeExecutionQuery;
    if (append) {
      const originalQuery = nodeExecutionQueryRef.current;
      if (!cursor || !originalQuery || originalQuery.node_id !== nodeId) {
        setNodeExecutionError("Run the history query again before loading more results.");
        return;
      }
      query = originalQuery;
    } else {
      const defaultWindow = nodeExecutionWindowTouchedRef.current
        ? null
        : workflowNodeExecutionDefaultWindow();
      const startedFromText = defaultWindow?.from ?? nodeExecutionStartedFrom;
      const startedToText = defaultWindow?.to ?? nodeExecutionStartedTo;
      if (defaultWindow) {
        setNodeExecutionStartedFrom(defaultWindow.from);
        setNodeExecutionStartedTo(defaultWindow.to);
      }
      const startedFrom = new Date(startedFromText);
      const startedTo = new Date(startedToText);
      if (
        Number.isNaN(startedFrom.getTime())
        || Number.isNaN(startedTo.getTime())
        || startedFrom >= startedTo
      ) {
        setNodeExecutionError("Choose a valid start before the end date.");
        return;
      }
      let filters: WorkflowNodeExecutionFilter[];
      try {
        filters = workflowNodeExecutionFilters(nodeExecutionFilters);
      } catch (reason: unknown) {
        setNodeExecutionError(reason instanceof Error ? reason.message : String(reason));
        return;
      }
      query = {
        node_id: nodeId,
        started_from: startedFrom.toISOString(),
        started_to: startedTo.toISOString(),
        statuses: nodeExecutionStatuses
          .split(",")
          .map((value) => value.trim())
          .filter(Boolean),
        filters,
        limit: 50,
      };
    }
    const body = { ...query, cursor };
    setNodeExecutionLoading(true);
    setNodeExecutionError(null);
    try {
      const payload = await fetchJson<WorkflowNodeExecutionSearch>(
        "/api/workflow-editor/node-executions/search",
        { method: "POST", body: JSON.stringify(body) },
      );
      if (selectedNodeIdRef.current !== nodeId) return;
      if (!append) nodeExecutionQueryRef.current = query;
      setNodeExecutionSearch((current) => append && current
        ? {
            ...payload,
            returned: current.executions.length + payload.executions.length,
            executions: [...current.executions, ...payload.executions],
          }
        : payload);
      if (!append) {
        const fields = await fetchJson<WorkflowNodeExecutionFields>(
          "/api/workflow-editor/node-executions/fields",
          {
            method: "POST",
            body: JSON.stringify({
              node_id: nodeId,
              started_from: query.started_from,
              started_to: query.started_to,
            }),
          },
        );
        if (selectedNodeIdRef.current !== nodeId) return;
        setNodeExecutionFields(fields);
      }
    } catch (reason: unknown) {
      setNodeExecutionError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setNodeExecutionLoading(false);
    }
  }

  async function loadNodeExecutionDetail(executionKey: string) {
    if (nodeExecutionDetailLoading || workflowRunReadLocked) return;
    const nodeId = selectedNode?.id ?? null;
    setNodeExecutionDetailLoading(true);
    setNodeExecutionError(null);
    try {
      const detail = await fetchJson<WorkflowNodeExecutionDetail>(
        `/api/workflow-editor/node-executions/${encodeURIComponent(executionKey)}`,
      );
      if (selectedNodeIdRef.current !== nodeId) return;
      setNodeExecutionDetail(detail);
    } catch (reason: unknown) {
      setNodeExecutionError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setNodeExecutionDetailLoading(false);
    }
  }

  function updateNodeExecutionFilter(
    index: number,
    patch: Partial<WorkflowNodeExecutionFilterDraft>,
  ) {
    setNodeExecutionFilters((current) => current.map((filter, filterIndex) =>
      filterIndex === index ? { ...filter, ...patch } : filter));
  }

  return {
    nodeExecutionSearch,
    loadNodeExecutionHistory,
    nodeExecutionStartedFrom,
    nodeExecutionWindowTouchedRef,
    setNodeExecutionStartedFrom,
    nodeExecutionStartedTo,
    setNodeExecutionStartedTo,
    nodeExecutionStatuses,
    setNodeExecutionStatuses,
    nodeExecutionFields,
    nodeExecutionFilters,
    updateNodeExecutionFilter,
    setNodeExecutionFilters,
    nodeExecutionLoading,
    nodeExecutionError,
    nodeExecutionDetailLoading,
    loadNodeExecutionDetail,
    nodeExecutionDetail,
  };
}

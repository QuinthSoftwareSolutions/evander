export type WorkflowGraphDirectoryScope = {
  directory_path?: string | null;
  directory_paths?: string[] | string | null;
};

export function normalizeWorkflowGraphDirectoryPath(value: unknown): string {
  const text = typeof value === "string" ? value.trim() : "";
  return text || "root";
}

function legacyWorkflowGraphDirectoryPath(value: unknown): string | null {
  const candidates = Array.isArray(value) ? value : typeof value === "string" ? [value] : [];
  for (const candidate of candidates) {
    const text = typeof candidate === "string" ? candidate.trim() : "";
    if (text) return text;
  }
  return null;
}

export function graphVisibleInDirectory(
  graph: WorkflowGraphDirectoryScope,
  selectedDirectoryPath: string,
): boolean {
  const selectedPath = normalizeWorkflowGraphDirectoryPath(selectedDirectoryPath);
  const explicitGraphDirectoryPath = typeof graph.directory_path === "string" ? graph.directory_path.trim() : "";
  const graphDirectoryPath = normalizeWorkflowGraphDirectoryPath(
    explicitGraphDirectoryPath || legacyWorkflowGraphDirectoryPath(graph.directory_paths),
  );
  return graphDirectoryPath === selectedPath;
}

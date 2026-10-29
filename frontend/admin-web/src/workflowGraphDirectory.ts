export type WorkflowGraphDirectoryScope = {
  directory_path?: string | null;
};

export function normalizeWorkflowGraphDirectoryPath(value: unknown): string {
  const text = typeof value === "string" ? value.trim() : "";
  return text || "root";
}

export function graphVisibleInDirectory(
  graph: WorkflowGraphDirectoryScope,
  selectedDirectoryPath: string,
): boolean {
  const selectedPath = normalizeWorkflowGraphDirectoryPath(selectedDirectoryPath);
  const explicitGraphDirectoryPath = typeof graph.directory_path === "string" ? graph.directory_path.trim() : "";
  const graphDirectoryPath = normalizeWorkflowGraphDirectoryPath(explicitGraphDirectoryPath);
  return graphDirectoryPath === selectedPath;
}

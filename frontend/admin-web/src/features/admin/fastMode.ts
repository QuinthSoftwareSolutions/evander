export type FastMode = boolean | "ultrafast";

export function fastModeLabel(value: FastMode | null | undefined): string {
  return value === "ultrafast" ? "Ultrafast" : value ? "Fast" : "Standard";
}

export function fastModeSelection(value: FastMode | null | undefined): string {
  if (value === "ultrafast") return value;
  return value == null ? "" : value ? "true" : "false";
}

export function fastModeValue(selection: string): FastMode | null {
  if (selection === "") return null;
  if (selection === "true") return true;
  if (selection === "false") return false;
  if (selection === "ultrafast") return selection;
  throw new Error("Choose Ultrafast, Fast, Standard, or the default speed.");
}

export function fastModeSelection(value: boolean | null | undefined): string {
  return value == null ? "" : value ? "true" : "false";
}

export function fastModeValue(selection: string): boolean | null {
  if (selection === "") return null;
  if (selection === "true") return true;
  if (selection === "false") return false;
  throw new Error("Choose Fast, Standard, or the default speed.");
}

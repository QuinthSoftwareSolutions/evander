export type SessionEffectiveToolPolicy = {
  inherited_approval_required_tools?: string[];
  approval_required_tools?: string[];
  mcp_tool_policies?: Record<string, { require_approval?: boolean }>;
};

export function isInheritedApprovalRequiredForTool(
  toolValue: string,
  effectivePolicy: SessionEffectiveToolPolicy | null | undefined,
  localApprovalRequiredTools: string[] | null | undefined,
): boolean {
  const rule = effectivePolicy?.mcp_tool_policies?.[toolValue];
  if (rule?.require_approval) {
    return true;
  }
  if (effectivePolicy?.inherited_approval_required_tools) {
    return effectivePolicy.inherited_approval_required_tools.includes(toolValue);
  }
  if (!effectivePolicy?.approval_required_tools?.includes(toolValue)) {
    return false;
  }
  return !(localApprovalRequiredTools ?? []).includes(toolValue);
}

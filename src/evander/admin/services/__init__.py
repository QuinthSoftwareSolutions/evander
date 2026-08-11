from __future__ import annotations

_SERVICE_EXPORTS = {
    "AddonAdminDependencies": (".addon_admin_service", "AddonAdminDependencies"),
    "AddonAdminService": (".addon_admin_service", "AddonAdminService"),
    "WorkflowConfigPort": (".ports", "WorkflowConfigPort"),
    "PrivilegedHelperPort": (".ports", "PrivilegedHelperPort"),
    "PrivilegedHelperFactoryPort": (".ports", "PrivilegedHelperFactoryPort"),
    "DirectoryPathPort": (".ports", "DirectoryPathPort"),
    "DirectoryOwnershipPort": (".ports", "DirectoryOwnershipPort"),
    "ApprovalAuditAdminDependencies": (
        ".approval_audit_service",
        "ApprovalAuditAdminDependencies",
    ),
    "ApprovalAuditAdminService": (".approval_audit_service", "ApprovalAuditAdminService"),
    "ArtifactAdminDependencies": (".artifact_service", "ArtifactAdminDependencies"),
    "ArtifactAdminService": (".artifact_service", "ArtifactAdminService"),
    "CapacityHealthAdminDependencies": (
        ".capacity_health_service",
        "CapacityHealthAdminDependencies",
    ),
    "CapacityHealthAdminService": (".capacity_health_service", "CapacityHealthAdminService"),
    "CodexAuthAdminDependencies": (
        ".codex_auth_admin_service",
        "CodexAuthAdminDependencies",
    ),
    "CodexAuthAdminService": (".codex_auth_admin_service", "CodexAuthAdminService"),
    "ConnectorAdminDependencies": (
        ".connector_admin_service",
        "ConnectorAdminDependencies",
    ),
    "ConnectorAdminService": (".connector_admin_service", "ConnectorAdminService"),
    "ConnectorConfigRevisionConflict": (
        ".connector_admin_service",
        "ConnectorConfigRevisionConflict",
    ),
    "HostDirectoryAdminDependencies": (
        ".host_directory_admin_service",
        "HostDirectoryAdminDependencies",
    ),
    "HostDirectoryAdminService": (
        ".host_directory_admin_service",
        "HostDirectoryAdminService",
    ),
    "HostDirectoryConfigRevisionConflict": (
        ".host_directory_admin_service",
        "HostDirectoryConfigRevisionConflict",
    ),
    "DocumentationService": (".documentation_service", "DocumentationService"),
    "DiscordWebhookAdminDependencies": (
        ".discord_webhook_service",
        "DiscordWebhookAdminDependencies",
    ),
    "DiscordWebhookAdminService": (
        ".discord_webhook_service",
        "DiscordWebhookAdminService",
    ),
    "IssueGitAdminDependencies": (".issue_git_service", "IssueGitAdminDependencies"),
    "IssueGitAdminService": (".issue_git_service", "IssueGitAdminService"),
    "IssueGitAdminSupport": (".issue_git_service", "IssueGitAdminSupport"),
    "ManagedGitAdminService": (".managed_git_service", "ManagedGitAdminService"),
    "ManagedGitMaintenancePort": (
        ".managed_git_runtime_service",
        "ManagedGitMaintenancePort",
    ),
    "ManagedGitMaintenanceService": (
        ".managed_git_runtime_service",
        "ManagedGitMaintenanceService",
    ),
    "ManagedGitRefreshAuthorizationDependencies": (
        ".managed_git_runtime_service",
        "ManagedGitRefreshAuthorizationDependencies",
    ),
    "ManagedGitRefreshAuthorizationPort": (
        ".managed_git_runtime_service",
        "ManagedGitRefreshAuthorizationPort",
    ),
    "ManagedGitRefreshAuthorizationService": (
        ".managed_git_runtime_service",
        "ManagedGitRefreshAuthorizationService",
    ),
    "ManagedGitRepositoryRefreshPort": (
        ".managed_git_runtime_service",
        "ManagedGitRepositoryRefreshPort",
    ),
    "ManagedGitRepositoryRefreshService": (
        ".managed_git_runtime_service",
        "ManagedGitRepositoryRefreshService",
    ),
    "ManagedGitWorktreeDependencies": (
        ".managed_git_worktree_service",
        "ManagedGitWorktreeDependencies",
    ),
    "ManagedGitWorktreeService": (
        ".managed_git_worktree_service",
        "ManagedGitWorktreeService",
    ),
    "ModelUsageAdminDependencies": (".model_usage_service", "ModelUsageAdminDependencies"),
    "ModelUsageAdminService": (".model_usage_service", "ModelUsageAdminService"),
    "OperatorSteeringAdminDependencies": (
        ".operator_steering_service",
        "OperatorSteeringAdminDependencies",
    ),
    "OperatorSteeringAdminService": (
        ".operator_steering_service",
        "OperatorSteeringAdminService",
    ),
    "OperationsAdminService": (".operations_service", "OperationsAdminService"),
    "PrIntegrationPreflightAdminDependencies": (
        ".pr_preflight_service",
        "PrIntegrationPreflightAdminDependencies",
    ),
    "PrIntegrationPreflightAdminService": (
        ".pr_preflight_service",
        "PrIntegrationPreflightAdminService",
    ),
    "SkillAdminService": (".skill_admin_service", "SkillAdminService"),
    "SessionAdminDependencies": (".session_service", "SessionAdminDependencies"),
    "SessionAdminService": (".session_service", "SessionAdminService"),
    "TeamsAdminDependencies": (".teams_service", "TeamsAdminDependencies"),
    "TeamsAdminService": (".teams_service", "TeamsAdminService"),
    "VmAgentAdminDependencies": (".vm_agent_service", "VmAgentAdminDependencies"),
    "VmAgentAdminService": (".vm_agent_service", "VmAgentAdminService"),
    "VmAgentAdminSupport": (".vm_agent_service", "VmAgentAdminSupport"),
    "VmInventoryAdminDependencies": (
        ".vm_inventory_service",
        "VmInventoryAdminDependencies",
    ),
    "VmInventoryAdminService": (".vm_inventory_service", "VmInventoryAdminService"),
    "WakeScheduleAdminDependencies": (
        ".wake_schedule_service",
        "WakeScheduleAdminDependencies",
    ),
    "WakeScheduleAdminService": (".wake_schedule_service", "WakeScheduleAdminService"),
    "WorkerControlAdminService": (
        ".worker_control_service",
        "WorkerControlAdminService",
    ),
    "WorkflowConfigAdminService": (".workflow_config_service", "WorkflowConfigAdminService"),
    "WorkflowConfigAdminDependencies": (
        ".workflow_config_service",
        "WorkflowConfigAdminDependencies",
    ),
    "WorkflowGenerationAdminService": (
        ".workflow_generation_service",
        "WorkflowGenerationAdminService",
    ),
    "WorkflowGenerationAdminDependencies": (
        ".workflow_generation_service",
        "WorkflowGenerationAdminDependencies",
    ),
    "WorkflowReportsAdminService": (".workflow_reports_service", "WorkflowReportsAdminService"),
    "WorkflowReportsAdminDependencies": (
        ".workflow_reports_service",
        "WorkflowReportsAdminDependencies",
    ),
}

__all__ = sorted(_SERVICE_EXPORTS)


def __getattr__(name: str):
    try:
        module_name, attribute_name = _SERVICE_EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(name) from exc
    from importlib import import_module

    value = getattr(import_module(module_name, __name__), attribute_name)
    globals()[name] = value
    return value

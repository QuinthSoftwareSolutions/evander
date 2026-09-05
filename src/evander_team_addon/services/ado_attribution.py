"""Trusted ADO intake and release attribution; no credential selectors in agent tools."""

from __future__ import annotations

from evander.admin.services.ado_user_connections import normalize_organization
from evander.issue_owner import assigned_to_identity
from evander.issues.service import IssueService

from ..models import AdoWorkItemAttribution, ProjectGitIdentity, TeamRecordState
from .domain.task_authorship import combined_deliverable_attributions


def capture_work_item_attribution(
    context, *, directory_path: str, work_item_id: str, project: str | None = None
):
    if not work_item_id.isdecimal():
        raise ValueError("ado_work_item_id must be a numeric ADO work item ID")
    resolver = context.provider_config_resolver
    if resolver is None:
        raise ValueError("ADO intake requires a provider configuration resolver")
    config = resolver.resolve_for_directory(directory_path)
    organization = normalize_organization(config.azure_devops_organization_url)
    item = IssueService(resolver).get_remote_backlog_item_by_id(
        provider="azure_devops",
        directory_path=directory_path,
        backlog_item_id=work_item_id,
        project=project,
        include_comments=False,
        include_relations=False,
    )
    identity = assigned_to_identity((item.get("fields") or {}).get("System.AssignedTo")) or {}
    attribution = AdoWorkItemAttribution(
        organization=organization,
        work_item_id=work_item_id,
        assignee_id=str(identity.get("id") or "").lower() or None,
        display_name=identity.get("display_name"),
        email=identity.get("email"),
    )
    author = None
    if attribution.display_name and attribution.email:
        author = ProjectGitIdentity(name=attribution.display_name, email=attribution.email)
    return [attribution], author


def release_publication_context(state, *, release_package_id: str, managed_git_admin):
    release = state.release_packages[release_package_id]
    project = state.projects[release.project_id]
    if (
        release.state != TeamRecordState.ACCEPTED
        or not release.published_commit_sha
        or not project.git
        or managed_git_admin is None
    ):
        return None
    record = managed_git_admin.get_repository(
        project.git.managed_repo_id, directory_path=project.git.directory_path
    )
    if record.get("provider") != "azure_devops":
        return None
    organization, _, _ = managed_git_admin._azure_repository_parts(
        record["canonical_repository_url"]
    )
    ids = set(release.deliverable_ids)
    if release.manifest:
        ids.update(item.deliverable_id for item in release.manifest.deliverables)
        for key, values in release.manifest.contribution_graph.items():
            ids.add(key)
            ids.update(values)
    attributions = combined_deliverable_attributions(state, ids)
    owners = {(item.organization, item.assignee_id) for item in (attributions or [])}
    identity_id = None
    reason = "missing_work_item_attribution"
    if len(owners) > 1:
        reason = "mixed_work_item_assignees"
    elif len(owners) == 1:
        source_org, source_identity = next(iter(owners))
        if source_org == organization.lower() and source_identity:
            identity_id = source_identity
            reason = None
        else:
            reason = "work_item_assignee_unavailable"
    return managed_git_admin.ado_user_connections.issue_publication_context(
        managed_repo_id=project.git.managed_repo_id,
        commit_sha=release.published_commit_sha,
        directory_path=project.git.directory_path,
        organization=organization,
        identity_id=identity_id,
        fallback_reason=reason,
        source_ref=release.id,
    )

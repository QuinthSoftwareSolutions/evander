from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel

from evander.directories import DirectoryService
from evander.directory_config import (
    DirectoryConfigResolver,
    DirectoryConfigStore,
    get_domain_base_config,
    validate_effective_domain_payload,
)
from evander.models import SessionManifest
from evander.provider_config import ProviderConfigResolver
from evander.repositories.session_manifests import as_session_manifest_repository
from evander.runtime import RuntimeContext


class EffectiveIssueSetupConfig(BaseModel):
    git_host: str
    git_user: str
    git_ssh_alias: str
    default_model: str | None = None
    post_exec_verification_prompt: str | None = None
    issue_setup_azure_devops_pr_pat_env: str = "AZURE_DEVOPS_PR_PAT"


def resolve_effective_issue_setup_config(
    *,
    config,
    runtime: RuntimeContext,
    directory_path: str,
    root_dir: Path,
) -> EffectiveIssueSetupConfig:
    resolver = DirectoryConfigResolver(
        DirectoryService(runtime.directories),
        base_gateway_config=config,
        config_store=DirectoryConfigStore(runtime.directories, runtime_dir=config.runtime_dir),
    )
    resolution = resolver.resolve_domain(
        directory_path,
        domain="issue_setup",
        base_config=get_domain_base_config(config, "issue_setup"),
        validator=lambda candidate: validate_effective_domain_payload("issue_setup", candidate),
    )
    effective = resolution.effective_config
    provider_config = ProviderConfigResolver(
        config,
        directory_store=runtime.directories,
        session_store=as_session_manifest_repository(runtime.session_repository),
    ).resolve_for_directory(directory_path)
    provider_pat_env = provider_config.azure_devops_pr_pat_env.strip() or "AZURE_DEVOPS_PR_PAT"
    issue_setup_pat_env_raw = effective.get("issue_setup_azure_devops_pr_pat_env")
    issue_setup_pat_env = (
        str(issue_setup_pat_env_raw).strip() if isinstance(issue_setup_pat_env_raw, str) else ""
    ) or provider_pat_env
    if not issue_setup_pat_env:
        raise ValueError(
            "Missing env-var name for Azure DevOps PR PAT lookup. "
            "Set issue_setup_azure_devops_pr_pat_env or providers.azure_devops_pr_pat_env."
        )
    return EffectiveIssueSetupConfig.model_validate(
        {
            "git_host": str(effective.get("git_host") or "192.168.250.1"),
            "git_user": str(effective.get("git_user") or os.environ.get("USER", "agent")),
            "git_ssh_alias": str(effective.get("git_ssh_alias") or "evander-git-host"),
            "default_model": str(effective.get("default_model") or "").strip() or None,
            "post_exec_verification_prompt": effective.get("post_exec_verification_prompt"),
            "issue_setup_azure_devops_pr_pat_env": issue_setup_pat_env,
        }
    )


def resolve_agent_model_for_manifest(
    session: SessionManifest,
    *,
    issue_setup: EffectiveIssueSetupConfig,
) -> str:
    session_id = session.session_id
    directory_path = session.directory_path
    session_model = str(session.model or "").strip()
    if session_model:
        return session_model
    default_model = str(issue_setup.default_model or "").strip()
    if default_model:
        return default_model
    raise ValueError(
        f"Cannot resolve AGENT_MODEL for session '{session_id}' in directory '{directory_path}': "
        "missing session.model and missing issue_setup.default_model. "
        f"Remediation: set session.model for session '{session_id}' or configure "
        f"issue_setup.default_model for directory '{directory_path}' (or an inherited parent)."
    )

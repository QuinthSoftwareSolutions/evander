from __future__ import annotations

from typing import Any

from evander.services import (
    AzureDevOpsPrQueryProvider,
    normalize_pr_automation_provider,
)


def build_pull_request_provider(
    *,
    context: Any,
    provider: str,
    directory_path: str | None,
    organization_url: str | None,
    project: str | None,
    node_type: str,
) -> tuple[str, str, AzureDevOpsPrQueryProvider]:
    provider_name = normalize_pr_automation_provider(provider)
    if provider_name != "azure_devops":
        raise ValueError(f"{node_type} provider {provider!r} is not supported")

    resolver = getattr(context, "provider_config_resolver", None)
    if resolver is None:
        raise RuntimeError(f"{node_type} provider_config_resolver is not available")
    resolve_for_directory = getattr(resolver, "resolve_for_directory", None)
    if not callable(resolve_for_directory):
        raise RuntimeError(f"{node_type} provider_config_resolver cannot resolve directories")

    config = resolve_for_directory(directory_path)
    resolved_organization_url = _normalize_optional_string(
        organization_url
    ) or _normalize_optional_string(getattr(config, "azure_devops_organization_url", None))
    resolved_project = _normalize_optional_string(project) or _normalize_optional_string(
        getattr(config, "azure_devops_project", None)
    )
    if not resolved_organization_url:
        raise ValueError(f"{node_type} requires organization_url for Azure DevOps")
    if not resolved_project:
        raise ValueError(f"{node_type} requires project for Azure DevOps")

    pat_env = _normalize_optional_string(getattr(config, "azure_devops_pr_pat_env", None))
    if not pat_env:
        raise ValueError(f"{node_type} requires azure_devops_pr_pat_env")
    pat = context.resolve_secret(pat_env)
    if not pat:
        raise RuntimeError(f"{node_type} Azure DevOps PAT is not configured in {pat_env}")

    timeout_seconds = float(getattr(config, "email_provider_timeout_seconds", 30.0) or 30.0)
    return (
        provider_name,
        resolved_project,
        AzureDevOpsPrQueryProvider(
            organization_url=resolved_organization_url,
            project=resolved_project,
            pat=pat,
            timeout_seconds=timeout_seconds,
        ),
    )


def _normalize_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None

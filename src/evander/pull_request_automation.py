from __future__ import annotations

from collections.abc import Callable
from typing import Any

from evander.optional_integrations import azure_devops_enabled, require_azure_devops_module
from evander.azure_devops import resolve_azure_devops_organization_url
from evander.services import (
    AzureDevOpsPrReplyProvider,
    AzureDevOpsPrQueryProvider,
    GitHubPrReplyProvider,
    PrReplyService,
    normalize_pr_automation_provider,
    normalize_pr_feedback_provider,
    pr_reply_state_namespace,
)


def build_pull_request_provider(
    *,
    context: Any,
    provider: str,
    directory_path: str | None,
    ado_organization: str | None,
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
    require_azure_devops_module(config=config)
    resolved_organization_url = resolve_azure_devops_organization_url(
        ado_organization=ado_organization,
        legacy_organization_url=getattr(config, "azure_devops_organization_url", None),
        node_type=node_type,
    )
    resolved_project = _normalize_optional_string(project) or _normalize_optional_string(
        getattr(config, "azure_devops_project", None)
    )
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


def build_pr_reply_service(
    *,
    config: Any,
    resolve_secret: Callable[[str], str | None],
    directory_path: str = "root",
    provider: str | None = None,
    ado_organization: str | None = None,
    project: str | None = None,
) -> PrReplyService:
    """Build the host-side PR reply service from one resolved directory configuration."""

    github_token = resolve_secret(str(getattr(config, "github_token_env", "") or "")) or ""
    azure_pat = (
        resolve_secret(str(getattr(config, "azure_devops_pr_pat_env", "") or "")) or ""
    )
    timeout_seconds = float(getattr(config, "email_provider_timeout_seconds", 30.0) or 30.0)

    selected_provider = normalize_pr_feedback_provider(provider) if provider else None
    providers = {}
    provider_state_namespaces: dict[str, str] = {}
    if github_token and selected_provider in {None, "github"}:
        providers["github"] = GitHubPrReplyProvider(
            token=github_token,
            timeout_seconds=timeout_seconds,
        )
        provider_state_namespaces["github"] = pr_reply_state_namespace(
            provider="github",
            directory_path=directory_path,
        )
    if selected_provider == "azure_devops":
        require_azure_devops_module(config=config)
    if azure_pat and azure_devops_enabled(config) and selected_provider in {None, "azure_devops"}:
        configured_organization_url = _normalize_optional_string(
            getattr(config, "azure_devops_organization_url", None)
        )
        resolved_project = _normalize_optional_string(project) or _normalize_optional_string(
            getattr(config, "azure_devops_project", None)
        )
        organization_url: str | None = None
        if ado_organization or configured_organization_url:
            try:
                organization_url = resolve_azure_devops_organization_url(
                    ado_organization=ado_organization,
                    legacy_organization_url=configured_organization_url,
                    node_type="effect:pull-request:reply",
                )
            except ValueError:
                if selected_provider == "azure_devops":
                    raise
        if organization_url and resolved_project:
            providers["azure_devops"] = AzureDevOpsPrReplyProvider(
                organization_url=organization_url,
                project=resolved_project,
                pat=azure_pat,
                timeout_seconds=timeout_seconds,
            )
            provider_state_namespaces["azure_devops"] = pr_reply_state_namespace(
                provider="azure_devops",
                directory_path=directory_path,
                ado_organization_url=organization_url,
                project=resolved_project,
            )
        elif selected_provider == "azure_devops":
            if not organization_url:
                raise ValueError("effect:pull-request:reply requires ado_organization")
            raise ValueError("effect:pull-request:reply requires project for Azure DevOps")
    runtime_dir = getattr(config, "runtime_dir", None)
    if runtime_dir is None:
        raise ValueError("PR reply service requires runtime_dir")
    return PrReplyService(
        providers=providers,
        idempotency_store_path=runtime_dir / "pr-reply-idempotency.json",
        ignored_feedback_store_path=runtime_dir / "ignored-feedback-ids.json",
        provider_state_namespaces=provider_state_namespaces,
    )


def build_pull_request_reply_service_for_context(
    *,
    context: Any,
    directory_path: str | None,
    node_type: str,
    provider: str,
    ado_organization: str | None = None,
    project: str | None = None,
) -> PrReplyService:
    """Resolve one workflow directory and build its host-owned PR reply service."""

    resolver = getattr(context, "provider_config_resolver", None)
    if resolver is None:
        raise RuntimeError(f"{node_type} provider_config_resolver is not available")
    resolve_for_directory = getattr(resolver, "resolve_for_directory", None)
    if not callable(resolve_for_directory):
        raise RuntimeError(f"{node_type} provider_config_resolver cannot resolve directories")
    resolve_secret = getattr(context, "resolve_secret", None)
    if not callable(resolve_secret):
        raise RuntimeError(f"{node_type} secret resolution is not available")
    return build_pr_reply_service(
        config=resolve_for_directory(directory_path),
        resolve_secret=resolve_secret,
        directory_path=directory_path or "root",
        provider=provider,
        ado_organization=ado_organization,
        project=project,
    )


def _normalize_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None

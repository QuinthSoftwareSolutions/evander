"""Descriptive Azure DevOps extension metadata, without network or worker startup."""

from __future__ import annotations

import os
from pathlib import Path
from dataclasses import replace

from evander.addons import AddonRegistration
from evander.config import GatewayConfig
from evander.integration_contracts import (
    AddonIntegration,
    IntegrationBackgroundWorker,
    IntegrationCheckResult,
    IntegrationConfigSection,
    IntegrationCredentialProvider,
    IntegrationReadinessCheck,
)


def resolved_config(context):
    if context.service is not None:
        runtime = getattr(context.service, "runtime", context.service)
        return runtime.provider_config.resolve_for_directory(context.directory_path)
    if context.config is None:
        raise ValueError("Azure DevOps requires host configuration")
    return context.config


def credential_present(context, field):
    return bool(os.environ.get(getattr(resolved_config(context), field), "").strip())


def credential(context, field):
    value = os.environ.get(getattr(resolved_config(context), field), "").strip()
    if not value:
        raise ValueError("Azure DevOps credential is unavailable")
    return {"pat": value}


def configuration_ready(context):
    config = resolved_config(context)
    if (
        not config.ado_organization
        or not config.azure_devops_project
        or config.azure_devops_project == "default"
    ):
        return IntegrationCheckResult("blocked", "ado_project_not_configured")
    return IntegrationCheckResult("ready", "ado_configuration_ready")


class HostWorker:
    def __init__(self, kind, config, project_dir):
        self.kind, self.config = kind, config
        self.project_dir = Path(project_dir or Path.cwd())

    def run(self, stop):
        if self.kind == "ado-pr-events":
            from .workers.pr_events import (
                AdoWorkerRuntimeConfig,
                _resolve_worker_config_for_directory,
                run_worker,
            )

            settings = AdoWorkerRuntimeConfig.load(workspace_root=self.project_dir)
            config = _resolve_worker_config_for_directory(
                worker_runtime_config=settings,
                runtime_gateway_config=self.config,
                directory_path=settings.directories[0] if settings.directories else "root",
            )
            run_worker(config, stop)
        else:
            from .workers.proxy_sync import (
                load_proxy_issue_sync_worker_config,
                run_proxy_issue_sync_worker,
            )

            settings = load_proxy_issue_sync_worker_config(
                workspace_root=self.project_dir, runtime_dir=self.config.runtime_dir
            )
            run_proxy_issue_sync_worker(
                runtime_dir=settings.runtime_dir,
                interval_seconds=settings.poll_interval_seconds,
                freshness_window_seconds=settings.freshness_window_seconds,
                stop=stop,
            )


def register_workflow_providers():
    from .delivery import register_providers as delivery_providers
    from .pipeline_tasks import register_providers as task_providers

    delivery, tasks = delivery_providers(), task_providers()
    return replace(
        delivery,
        effect_providers={**delivery.effect_providers, **tasks.effect_providers},
        node_definition_resolvers={
            **delivery.node_definition_resolvers,
            **tasks.node_definition_resolvers,
        },
        workflow_editor_catalog_entries=(
            *delivery.workflow_editor_catalog_entries,
            *tasks.workflow_editor_catalog_entries,
        ),
    )


def register_addon():
    from .starter import DELIVERY_STARTER

    schema = GatewayConfig.model_json_schema()
    fields = {
        key: value
        for key, value in schema["properties"].items()
        if key.startswith(("ado_", "azure_devops_"))
    }
    return AddonRegistration(
        addon_id="azure-devops",
        display_name="Azure DevOps",
        requires_gateway_restart=True,
        workflow_providers=register_workflow_providers,
        required_restart_services=("evander-ado-worker", "evander-proxy-issue-sync"),
        metadata={
            "description": "Work items, pull requests, builds, personal credentials and delivery workflows."
        },
        integration=AddonIntegration(
            workflow_starters=(DELIVERY_STARTER,),
            settings=(
                IntegrationConfigSection(
                    "azure-devops",
                    "Azure DevOps",
                    {
                        "type": "object",
                        "properties": fields,
                        "$defs": schema.get("$defs", {}),
                        "additionalProperties": False,
                    },
                ),
            ),
            credentials=tuple(
                IntegrationCredentialProvider(
                    key,
                    label,
                    lambda context, field=field: credential_present(context, field),
                    lambda context, field=field: credential(context, field),
                )
                for key, label, field in (
                    ("work-items", "Work items and builds", "azure_devops_pat_env"),
                    ("pull-requests", "Pull requests", "azure_devops_pr_pat_env"),
                )
            ),
            readiness_checks=(
                IntegrationReadinessCheck(
                    "project", "Organization and project", configuration_ready
                ),
            ),
            workers=tuple(
                IntegrationBackgroundWorker(
                    kind,
                    label,
                    lambda context, kind=kind: HostWorker(
                        kind, resolved_config(context), context.project_dir
                    ),
                )
                for kind, label in (
                    ("ado-pr-events", "PR feedback and completion"),
                    ("ado-proxy-sync", "Work item freshness"),
                )
            ),
        ),
    )

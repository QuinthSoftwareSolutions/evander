"""Optional registration; worker entrypoints do not load control-plane surfaces."""

PREFIX = "rabbitmq"
OPERATIONS = ["inspect", "manage", "publish", "get_messages", "control"]


def register_mcp(server, context):
    from evander.resources.surfaces import register_calls, register_lifecycle

    from .adapter import Adapter
    from .service import service

    register_lifecycle(server, context, service, PREFIX, control="control" in OPERATIONS)
    calls = [op for op in OPERATIONS if op != "control"]
    register_calls(
        server, context, service, PREFIX, calls, Adapter.argument_model, Adapter.read_operations
    )
    return [
        f"{PREFIX}_{op}" for op in ["discover", "acquire", "get", "reset", "release", *OPERATIONS]
    ]


def register_addon():
    from pathlib import Path

    from evander.addons import AddonAdminNavigation, AddonRegistration
    from evander.integration_contracts import (
        AddonIntegration,
        IntegrationConfigSection,
        IntegrationReadinessCheck,
    )
    from evander.resources.providers import register_providers as resource_providers
    from evander.resources.readiness import check as check_readiness
    from evander.resources.starters import maintenance
    from evander.resources.surfaces import admin_routes, catalog, ui_routes

    from .adapter import Adapter
    from .service import service
    from .settings import Settings

    return AddonRegistration(
        addon_id=PREFIX + "-addon",
        display_name="RabbitMQ",
        admin_navigation=AddonAdminNavigation(
            label="RabbitMQ",
            ui_path="/addons/" + PREFIX + "-addon/",
            description="Registered services, managed resources and disposable leases",
        ),
        admin_api_routes=lambda ctx: admin_routes(ctx, service),
        admin_ui_routes=lambda ctx: ui_routes(ctx, Path(__file__).parent),
        mcp_tools=register_mcp,
        mcp_tool_catalog=catalog(PREFIX, OPERATIONS),
        workflow_providers=lambda: resource_providers(
            service,
            PREFIX,
            Adapter.read_operations,
            Adapter.operations - Adapter.read_operations,
        ),
        integration=AddonIntegration(
            workflow_starters=(maintenance(PREFIX),),
            readiness_checks=(
                IntegrationReadinessCheck(
                    "connections",
                    "Registered connections",
                    lambda ctx: check_readiness(ctx, service),
                ),
            ),
            settings=(
                IntegrationConfigSection(
                    "settings", "RabbitMQ settings", Settings.model_json_schema()
                ),
            ),
        ),
        required_restart_services=(
            "evander-gateway",
            "evander-trigger-orchestrator",
            "evander-effect-orchestrator",
        ),
        requires_gateway_restart=True,
    )

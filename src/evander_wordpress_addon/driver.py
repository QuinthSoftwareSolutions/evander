import json
from evander.connectors.models import ConnectorInvocationResult
from evander.website_sessions import website_origin

from .models import OPERATIONS, WordPressError
from .service import Actor, Service


class WordPressConnectorDriver:
    def validate_definition(self, definition):
        website_origin(definition.config.get("base_url", ""))
        if set(definition.operations) - OPERATIONS:
            raise ValueError("Unknown WordPress connector operation")
        for name, operation in definition.operations.items():
            if (
                name in {"prepare_change", "apply_change", "restore_change"}
                and operation.access != "write"
            ):
                raise ValueError("WordPress mutations require write access")

    async def invoke(
        self, *, context, connector_id, definition, operation_name, operation, arguments
    ):
        service = Service(
            context.runtime_dir,
            Actor(context.directory_path, context.session_id),
            context.host_service,
            authorize=context.authorize,
        )
        site = service.site(
            arguments["site_id"],
            configure=operation_name in {"discover", "change_status", "restore_change"},
        )
        if site.connector_id != connector_id:
            raise WordPressError("This connector is not bound to the requested site")
        if operation_name == "discover":
            value = await service.discover(arguments["site_id"])
        elif operation_name == "inspect":
            value = await service.inspect(arguments["site_id"], arguments["key"])
        elif operation_name == "prepare_change":
            value = await service.prepare(arguments)
        elif operation_name == "apply_change":
            value = await service.queue(arguments)
        elif operation_name == "restore_change":
            value = await service.queue(arguments, restore=True)
        else:
            value = service.status(arguments["site_id"], arguments.get("change_id"))
        if len(json.dumps(value).encode()) > operation.max_response_bytes:
            raise WordPressError("WordPress result exceeds this operation's inline limit")
        return ConnectorInvocationResult(
            connector_id=connector_id, operation=operation_name, result_mode="json", value=value
        )

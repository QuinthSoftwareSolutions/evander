from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import BaseRoute, Route

from agent_sandbox.admin.service import AdminService
from agent_sandbox.admin.services.openai_billing_service import OpenAIBillingService
from agent_sandbox.admin.web.shared import AdminWebAuth


def provider_status_routes(service: AdminService, auth: AdminWebAuth) -> list[BaseRoute]:
    openai_billing = OpenAIBillingService(
        service.config,
        provider_config_resolver=service.runtime.provider_config,
    )

    async def get_openai_costs(request: Request) -> JSONResponse:
        auth.require(request)
        directory_path = str(request.query_params.get("directory_path") or "root").strip() or "root"
        force_refresh = str(request.query_params.get("refresh") or "").lower() in {
            "1",
            "true",
            "yes",
        }
        status = openai_billing.fetch_status(directory_path, force_refresh=force_refresh)
        return JSONResponse({"costs": status.model_dump(mode="json")})

    return [
        Route("/api/admin/providers/openai/costs", get_openai_costs, methods=["GET"]),
    ]

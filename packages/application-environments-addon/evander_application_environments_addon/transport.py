import hmac
from starlette.responses import JSONResponse
from evander.resources.container_runtime import RuntimeError


def require_internal(request, token):
    if not token or not hmac.compare_digest(
        request.headers.get("authorization", ""), "Bearer " + token
    ):
        raise RuntimeError("Worker authentication required", "unauthorized", 401)


async def read_json(request):
    from evander.resources.worker import read_body

    return await read_body(request)


def error(exc):
    return JSONResponse(
        {
            "error": str(exc) if isinstance(exc, RuntimeError) else "Invalid application request",
            "code": getattr(exc, "code", "invalid_request"),
        },
        status_code=getattr(exc, "status", 400),
    )

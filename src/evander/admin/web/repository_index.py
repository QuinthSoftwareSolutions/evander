from __future__ import annotations

from pathlib import Path
import tempfile

from pydantic import BaseModel, ConfigDict, Field
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Route

from evander.admin.web.git_repositories import _service_error_response
from evander.admin.web.rbac import ADMIN_OWNER_PERMISSION
from evander.admin.web.shared import json_error, request_has_privileged_operations
from evander.code_index.models import IndexSettings


class IndexSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)
    settings: IndexSettings


class IndexBuildRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    commit_sha: str | None = None
    rebuild: bool = False


def repository_index_routes(components, auth):
    service = components.repository_index_admin

    async def endpoint(request):
        temp_path = None
        try:
            repo = request.path_params["managed_repo_id"]
            auth.require(request)
            record = await run_in_threadpool(components.managed_git_admin.get_repository, repo)
            directory = record["directory_path"]
            auth.require_permission(request, ADMIN_OWNER_PERMISSION, directory_path=directory)
            if request.method != "GET" and not request_has_privileged_operations(
                auth=auth,
                service=components,
                request=request,
                required_permission=ADMIN_OWNER_PERMISSION,
                directory_path=directory,
            ):
                return json_error("Privileged operations are locked.", status_code=403)
            action = request.path_params.get("action")
            if action and (action, request.method) not in {("export", "GET"), ("cancel", "POST")}:
                return json_error("Unsupported indexing operation", status_code=405)
            if action == "export":
                build = service.store.get(request.path_params["build_id"])
                if build["repo"] != repo:
                    raise LookupError("Index build not found")
                include = request.query_params.get("include_embeddings", "false")
                if include not in {"true", "false"}:
                    raise ValueError("include_embeddings must be true or false")
                with tempfile.NamedTemporaryFile(
                    prefix="code-index-export-", suffix=".zip", delete=False
                ) as file:
                    temp_path = Path(file.name)
                await run_in_threadpool(
                    service.export,
                    repo,
                    directory,
                    build["id"],
                    temp_path,
                    include_embeddings=include == "true",
                )
                return FileResponse(
                    temp_path,
                    media_type="application/zip",
                    filename=f"code-index-{build['sha'][:12]}.zip",
                    headers={"Cache-Control": "no-store"},
                    background=BackgroundTask(temp_path.unlink, missing_ok=True),
                )
            if action == "cancel":
                result = await run_in_threadpool(
                    service.cancel, repo, directory, request.path_params["build_id"]
                )
            elif request.method == "GET":
                result = await run_in_threadpool(service.status, repo, directory)
            elif request.method == "PATCH":
                body = IndexSettingsRequest.model_validate(await request.json())
                result = await run_in_threadpool(
                    service.save_settings,
                    repo,
                    directory,
                    body.settings.model_dump(),
                    expected_revision=body.expected_revision,
                )
            elif action is None and request.url.path.endswith("/builds"):
                body = IndexBuildRequest.model_validate(await request.json())
                result = await run_in_threadpool(
                    service.ensure, repo, directory, body.commit_sha, rebuild=body.rebuild
                )
            else:
                raise ValueError("Unsupported indexing operation")
            return JSONResponse(result, headers={"Cache-Control": "no-store"})
        except PermissionError as exc:
            return json_error(str(exc), status_code=403)
        except Exception as exc:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
            return _service_error_response(exc)

    prefix = "/api/admin/git-repositories/{managed_repo_id:str}/indexing"
    return [
        Route(prefix, endpoint, methods=["GET", "PATCH"]),
        Route(prefix + "/builds", endpoint, methods=["POST"]),
        Route(prefix + "/builds/{build_id:str}/{action:str}", endpoint, methods=["GET", "POST"]),
    ]

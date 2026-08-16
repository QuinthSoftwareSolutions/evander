from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import BaseRoute, Route

from evander.admin.web.rbac import ADMIN_OWNER_PERMISSION
from evander.admin.web.shared import AdminWebAuth, json_error, request_has_privileged_operations

if TYPE_CHECKING:
    from evander.admin.composition import AdminRuntimeComponents


class ManagedGitRepositoryRegistrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    directory_path: str = Field(min_length=1)
    repo_source: str = Field(min_length=1)
    default_branch: str | None = None
    refresh: bool = True

    @field_validator("directory_path", "repo_source", mode="before")
    @classmethod
    def _normalize_required_text(cls, value: object) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("value is required")
        return normalized

    @field_validator("default_branch", mode="before")
    @classmethod
    def _normalize_optional_text(cls, value: object) -> str | None:
        normalized = str(value or "").strip()
        return normalized or None


class ManagedGitHostCommitIdentityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    email: str = Field(min_length=1)

    @field_validator("name", "email", mode="before")
    @classmethod
    def _normalize_required_text(cls, value: object) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError("value is required")
        return normalized


class ManagedGitMaintenanceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    managed_repo_id: str | None = None
    directory_path: str = "root"

    @field_validator("managed_repo_id", mode="before")
    @classmethod
    def _normalize_optional_id(cls, value: object) -> str | None:
        normalized = str(value or "").strip()
        return normalized or None

    @field_validator("directory_path", mode="before")
    @classmethod
    def _normalize_directory_path(cls, value: object) -> str:
        return str(value or "root").strip() or "root"


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    return value


async def _json_body(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("Request body must be valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object")
    return payload


async def _optional_json_body(request: Request) -> dict[str, Any]:
    if not (await request.body()).strip():
        return {}
    return await _json_body(request)


def _validation_error_text(exc: ValidationError) -> str:
    messages = [str(item.get("msg") or "").strip() for item in exc.errors()]
    return "; ".join(message for message in messages if message) or "Invalid request"


def _directory_path_for_repository(repository: Any) -> str:
    if isinstance(repository, BaseModel):
        repository = repository.model_dump(mode="python")
    if isinstance(repository, dict):
        return str(repository.get("directory_path") or "root").strip() or "root"
    return str(getattr(repository, "directory_path", None) or "root").strip() or "root"


def _service_error_response(exc: Exception, *, default_status_code: int = 500) -> JSONResponse:
    status_code = getattr(exc, "status_code", None)
    if not isinstance(status_code, int):
        if isinstance(exc, PermissionError):
            status_code = 403
        elif isinstance(exc, LookupError):
            status_code = 404
        elif isinstance(exc, ValueError):
            status_code = 400
        else:
            status_code = default_status_code
    if not isinstance(status_code, int) or status_code < 400 or status_code > 599:
        status_code = default_status_code
    details = getattr(exc, "details", None)
    return json_error(
        str(exc) or "Managed Git operation failed",
        status_code=status_code,
        details=details if isinstance(details, dict) else None,
    )


def git_repository_routes(
    service: AdminRuntimeComponents,
    auth: AdminWebAuth,
) -> list[BaseRoute]:
    managed_git = service.managed_git_admin

    def _owner_error(request: Request, *, directory_path: str) -> JSONResponse | None:
        try:
            auth.require_permission(
                request,
                ADMIN_OWNER_PERMISSION,
                directory_path=directory_path,
            )
        except PermissionError as exc:
            return json_error(str(exc), status_code=403)
        return None

    def _mutation_access_error(
        request: Request,
        *,
        directory_path: str,
    ) -> JSONResponse | None:
        owner_error = _owner_error(request, directory_path=directory_path)
        if owner_error is not None:
            return owner_error
        if not request_has_privileged_operations(
            auth=auth,
            service=service,
            request=request,
            required_permission=ADMIN_OWNER_PERMISSION,
            directory_path=directory_path,
        ):
            return json_error("Privileged operations are locked.", status_code=403)
        return None

    async def _repository_for_scoped_request(
        request: Request,
        managed_repo_id: str,
    ) -> tuple[Any | None, JSONResponse | None]:
        try:
            auth.require(request)
            repository = await run_in_threadpool(
                managed_git.get_repository,
                managed_repo_id,
            )
        except PermissionError as exc:
            return None, json_error(str(exc), status_code=403)
        except Exception as exc:
            return None, _service_error_response(exc)
        return repository, None

    async def list_repositories(request: Request) -> JSONResponse:
        directory_path = str(request.query_params.get("directory_path") or "root").strip() or "root"
        include_descendants_value = (
            str(request.query_params.get("include_descendants") or "true").strip().casefold()
        )
        if include_descendants_value not in {"1", "0", "true", "false", "yes", "no"}:
            return json_error("include_descendants must be a boolean", status_code=400)
        include_descendants = include_descendants_value in {"1", "true", "yes"}
        owner_error = _owner_error(request, directory_path=directory_path)
        if owner_error is not None:
            return owner_error
        try:
            repositories = await run_in_threadpool(
                managed_git.list_repositories,
                directory_path=directory_path,
                include_descendants=include_descendants,
            )
        except Exception as exc:
            return _service_error_response(exc)
        return JSONResponse({"repositories": _jsonable(repositories)})

    async def register_repository(request: Request) -> JSONResponse:
        try:
            payload = ManagedGitRepositoryRegistrationRequest.model_validate(
                await _json_body(request)
            )
        except ValueError as exc:
            message = _validation_error_text(exc) if isinstance(exc, ValidationError) else str(exc)
            return json_error(message, status_code=400)
        access_error = _mutation_access_error(
            request,
            directory_path=payload.directory_path,
        )
        if access_error is not None:
            return access_error
        try:
            repository = await run_in_threadpool(
                managed_git.register_repository,
                payload.directory_path,
                payload.repo_source,
                default_branch=payload.default_branch,
                refresh=payload.refresh,
                authorize_existing_owner=lambda directory_path: auth.require_permission(
                    request,
                    ADMIN_OWNER_PERMISSION,
                    directory_path=directory_path,
                ),
            )
        except Exception as exc:
            return _service_error_response(exc)
        return JSONResponse({"repository": _jsonable(repository)})

    async def get_repository(request: Request) -> JSONResponse:
        managed_repo_id = str(request.path_params.get("managed_repo_id") or "").strip()
        repository, error = await _repository_for_scoped_request(request, managed_repo_id)
        if error is not None:
            return error
        assert repository is not None
        owner_error = _owner_error(
            request,
            directory_path=_directory_path_for_repository(repository),
        )
        if owner_error is not None:
            return owner_error
        return JSONResponse({"repository": _jsonable(repository)})

    async def refresh_repository(request: Request) -> JSONResponse:
        managed_repo_id = str(request.path_params.get("managed_repo_id") or "").strip()
        repository, error = await _repository_for_scoped_request(request, managed_repo_id)
        if error is not None:
            return error
        assert repository is not None
        access_error = _mutation_access_error(
            request,
            directory_path=_directory_path_for_repository(repository),
        )
        if access_error is not None:
            return access_error
        try:
            refreshed = await run_in_threadpool(
                managed_git.refresh_repository,
                managed_repo_id,
            )
        except Exception as exc:
            return _service_error_response(exc)
        return JSONResponse({"repository": _jsonable(refreshed)})

    async def set_host_commit_identity(request: Request) -> JSONResponse:
        managed_repo_id = str(request.path_params.get("managed_repo_id") or "").strip()
        try:
            payload = ManagedGitHostCommitIdentityRequest.model_validate(await _json_body(request))
        except ValueError as exc:
            message = _validation_error_text(exc) if isinstance(exc, ValidationError) else str(exc)
            return json_error(message, status_code=400)
        repository, error = await _repository_for_scoped_request(request, managed_repo_id)
        if error is not None:
            return error
        assert repository is not None
        directory_path = _directory_path_for_repository(repository)
        access_error = _mutation_access_error(
            request,
            directory_path=directory_path,
        )
        if access_error is not None:
            return access_error
        try:
            updated = await run_in_threadpool(
                managed_git.set_host_commit_identity,
                managed_repo_id,
                directory_path=directory_path,
                name=payload.name,
                email=payload.email,
            )
        except Exception as exc:
            return _service_error_response(exc)
        return JSONResponse({"repository": _jsonable(updated)})

    async def delete_repository(request: Request) -> JSONResponse:
        managed_repo_id = str(request.path_params.get("managed_repo_id") or "").strip()
        repository, error = await _repository_for_scoped_request(request, managed_repo_id)
        if error is not None:
            return error
        assert repository is not None
        access_error = _mutation_access_error(
            request,
            directory_path=_directory_path_for_repository(repository),
        )
        if access_error is not None:
            return access_error
        try:
            deleted = await run_in_threadpool(
                managed_git.delete_repository,
                managed_repo_id,
            )
        except Exception as exc:
            return _service_error_response(exc)
        return JSONResponse({"deleted": True, "repository": _jsonable(deleted)})

    async def run_maintenance(request: Request) -> JSONResponse:
        try:
            payload = ManagedGitMaintenanceRequest.model_validate(
                await _optional_json_body(request)
            )
        except ValueError as exc:
            message = _validation_error_text(exc) if isinstance(exc, ValidationError) else str(exc)
            return json_error(message, status_code=400)

        directory_path = payload.directory_path
        if payload.managed_repo_id is not None:
            repository, error = await _repository_for_scoped_request(
                request,
                payload.managed_repo_id,
            )
            if error is not None:
                return error
            assert repository is not None
            directory_path = _directory_path_for_repository(repository)
        elif directory_path != "root":
            return json_error(
                "directory_path requires managed_repo_id for repository-scoped maintenance",
                status_code=400,
            )

        access_error = _mutation_access_error(request, directory_path=directory_path)
        if access_error is not None:
            return access_error
        try:
            result = await run_in_threadpool(
                managed_git.maintenance,
                payload.managed_repo_id,
            )
        except Exception as exc:
            return _service_error_response(exc)
        return JSONResponse({"maintenance": _jsonable(result)})

    return [
        Route("/api/admin/git-repositories", list_repositories, methods=["GET"]),
        Route("/api/admin/git-repositories", register_repository, methods=["POST"]),
        Route(
            "/api/admin/git-repositories/maintenance",
            run_maintenance,
            methods=["POST"],
        ),
        Route(
            "/api/admin/git-repositories/{managed_repo_id:str}/refresh",
            refresh_repository,
            methods=["POST"],
        ),
        Route(
            "/api/admin/git-repositories/{managed_repo_id:str}/host-commit-identity",
            set_host_commit_identity,
            methods=["PUT"],
        ),
        Route(
            "/api/admin/git-repositories/{managed_repo_id:str}",
            get_repository,
            methods=["GET"],
        ),
        Route(
            "/api/admin/git-repositories/{managed_repo_id:str}",
            delete_repository,
            methods=["DELETE"],
        ),
    ]


__all__ = [
    "ManagedGitHostCommitIdentityRequest",
    "ManagedGitMaintenanceRequest",
    "ManagedGitRepositoryRegistrationRequest",
    "git_repository_routes",
]

from __future__ import annotations

from typing import Any

from starlette.requests import Request
from starlette.routing import BaseRoute, Route

from evander.admin.composition import AdminRuntimeComponents
from evander.admin.web.shared import AdminWebAuth, json_error, json_ok
from evander.admin.web.user_store import (
    AdminWebGroupRecord,
    AdminWebUserStore,
    normalize_admin_web_login,
)
from evander.admin.web.rbac import (
    GROUPS_MANAGE_PERMISSION,
    PERMISSION_CATALOG,
    USERS_MANAGE_PERMISSION,
)
from evander.directory_config import ContactDirectoryConfig

WORKFLOW_TUTORIAL_PROGRESS_PREFERENCE_KEY = "workflow_tutorial_progress"
WORKFLOW_TUTORIAL_IDS = frozenset({"first-steps"})


def _user_payload(
    user_store: AdminWebUserStore,
    user,
    *,
    auth: AdminWebAuth | None = None,
    directory_path: str = "root",
    contact_id: str | None = None,
    contacts_config: ContactDirectoryConfig | None = None,
) -> dict[str, Any]:
    contact = contacts_config.contacts.get(contact_id) if contacts_config and contact_id else None
    payload = user.model_dump(mode="json")
    payload["contact_id"] = contact_id
    payload["contact"] = (
        {
            "id": contact_id,
            "name": contact.name,
            "description": contact.description,
            "email_address_count": len(contact.email_addresses),
            "phone_number_count": len(contact.phone_numbers),
            "primary_phone_label": next(
                (phone.label for phone in contact.phone_numbers if phone.primary), None
            ),
        }
        if contact is not None and contact_id is not None
        else None
    )
    credential = user_store.get_local_password_credential(user.login)
    legacy_local_password_enabled = (
        auth is not None
        and auth.config.auth_mode == "local_password"
        and auth.config.password is not None
        and normalize_admin_web_login(user.login) == normalize_admin_web_login(auth.config.username)
    )
    payload["local_password_enabled"] = credential is not None or legacy_local_password_enabled
    payload["password_last_changed_at"] = (
        credential.model_dump(mode="json")["updated_at"] if credential is not None else None
    )
    payload["group_assignments"] = [
        assignment.model_dump(mode="json")
        for assignment in user_store.list_user_group_assignments(user.login)
    ]
    payload["workflow_grants"] = [
        grant.model_dump(mode="json") for grant in user_store.list_user_workflow_grants(user.login)
    ]
    payload["effective_permissions"] = sorted(
        user_store.effective_permissions(user.login, directory_path=directory_path)
    )
    return payload


def _group_payload(group: AdminWebGroupRecord) -> dict[str, Any]:
    return group.model_dump(mode="json")


def _assignment_payloads(payload: dict[str, Any]) -> list[dict[str, Any]] | None:
    if "group_assignments" not in payload:
        return None
    raw_assignments = payload.get("group_assignments")
    if raw_assignments is None:
        return []
    if not isinstance(raw_assignments, list):
        raise ValueError("group_assignments must be a JSON array")
    result: list[dict[str, Any]] = []
    for item in raw_assignments:
        if not isinstance(item, dict):
            raise ValueError("group_assignments entries must be JSON objects")
        result.append(
            {
                "group_id": item.get("group_id"),
                "directory_path": item.get("directory_path"),
            }
        )
    return result


def _workflow_grant_payloads(payload: dict[str, Any]) -> list[dict[str, Any]] | None:
    if "workflow_grants" not in payload:
        return None
    raw_grants = payload.get("workflow_grants")
    if raw_grants is None:
        return []
    if not isinstance(raw_grants, list):
        raise ValueError("workflow_grants must be a JSON array")
    result: list[dict[str, Any]] = []
    for item in raw_grants:
        if not isinstance(item, dict):
            raise ValueError("workflow_grants entries must be JSON objects")
        result.append(
            {
                "permission": item.get("permission"),
                "directory_path": item.get("directory_path"),
            }
        )
    return result


def _optional_password_payload(payload: dict[str, Any]) -> tuple[bool, str | None]:
    if "password" not in payload:
        return False, None
    password = payload.get("password")
    if not isinstance(password, str) or password == "":
        raise ValueError("password is required")
    return True, password


def _apply_user_access_payload(
    user_store: AdminWebUserStore,
    *,
    login: str,
    group_assignments: list[dict[str, Any]] | None,
    workflow_grants: list[dict[str, Any]] | None,
) -> None:
    if group_assignments is not None:
        user_store.set_user_group_assignments(login=login, assignments=group_assignments)
    if workflow_grants is not None:
        user_store.set_user_workflow_grants(login=login, grants=workflow_grants)


def _contact_options_payload(config: ContactDirectoryConfig) -> list[dict[str, Any]]:
    options: list[dict[str, Any]] = []
    for contact_id, contact in sorted(config.contacts.items()):
        options.append(
            {
                "id": contact_id,
                "name": contact.name,
                "description": contact.description,
                "email_address_count": len(contact.email_addresses),
                "phone_number_count": len(contact.phone_numbers),
                "primary_phone_label": next(
                    (phone.label for phone in contact.phone_numbers if phone.primary), None
                ),
            }
        )
    return options


async def _json_body(request: Request) -> dict[str, Any]:
    try:
        payload = await request.json()
    except Exception as exc:
        raise ValueError("Request body must be valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("Request body must be a JSON object")
    return payload


def _current_preference_login(auth: AdminWebAuth, request: Request) -> str:
    session = auth.require(request)
    login = normalize_admin_web_login(session.get("login"))
    if login is None:
        raise PermissionError("Authentication required")
    return login


def _normalized_workflow_tutorial_ids(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    seen: set[str] = set()
    result: list[str] = []
    for entry in value:
        if not isinstance(entry, str):
            continue
        tutorial_id = entry.strip()
        if not tutorial_id or tutorial_id not in WORKFLOW_TUTORIAL_IDS or tutorial_id in seen:
            continue
        seen.add(tutorial_id)
        result.append(tutorial_id)
    return result


def _workflow_tutorial_progress_payload(payload: object) -> dict[str, list[str]]:
    source = payload if isinstance(payload, dict) else {}
    completed = _normalized_workflow_tutorial_ids(
        source.get("completed_tutorial_ids", source.get("completedTutorialIds"))
    )
    completed_set = set(completed)
    skipped = [
        tutorial_id
        for tutorial_id in _normalized_workflow_tutorial_ids(
            source.get("skipped_tutorial_ids", source.get("skippedTutorialIds"))
        )
        if tutorial_id not in completed_set
    ]
    return {
        "completed_tutorial_ids": completed,
        "skipped_tutorial_ids": skipped,
    }


def _require_user_manager(auth: AdminWebAuth, request: Request) -> None:
    auth.require_permission(request, USERS_MANAGE_PERMISSION)


def _require_group_manager(auth: AdminWebAuth, request: Request) -> None:
    auth.require(request)
    if not auth.can(request, GROUPS_MANAGE_PERMISSION):
        raise PermissionError("Group-management permission is required")


def _directory_path_from_request(service: AdminRuntimeComponents, request: Request) -> str:
    requested = str(request.query_params.get("directory_path") or "root").strip() or "root"
    return service.teams_admin.normalize_directory_reference(requested)


def _contacts_config_for_directory(
    service: AdminRuntimeComponents, directory_path: str
) -> ContactDirectoryConfig:
    result = service.operations_admin.compute_directory_effective_config(directory_path, "contacts")
    if not result.ok or result.data is None:
        return ContactDirectoryConfig()
    return ContactDirectoryConfig.model_validate(result.data.resolution.effective_config)


def _normalize_contact_update(contacts_config: ContactDirectoryConfig, value: object) -> str | None:
    if value is None:
        return None
    contact_id = str(value).strip()
    if not contact_id:
        return None
    if contact_id not in contacts_config.contacts:
        raise ValueError(f"Unknown contact id: {contact_id}")
    return contact_id


def _write_user_contact_link(
    service: AdminRuntimeComponents,
    *,
    directory_path: str,
    login: str,
    contact_id: str | None,
) -> None:
    if not service.config.enable_directory_scoped_config:
        raise ValueError(
            "directory-scoped config is disabled; user contact attachments are read-only"
        )
    existing_result = service.operations_admin.read_directory_domain_config(
        directory_path, "contacts"
    )
    existing_config = (
        dict(existing_result.data.config)
        if existing_result.ok
        and existing_result.data is not None
        and existing_result.data.config is not None
        else {}
    )
    raw_links = existing_config.get("user_contact_links")
    links = dict(raw_links) if isinstance(raw_links, dict) else {}
    if contact_id is None:
        links[login] = None
    else:
        links[login] = contact_id
    if links:
        existing_config["user_contact_links"] = links
    else:
        existing_config.pop("user_contact_links", None)
    result = service.operations_admin.write_directory_domain_config(
        directory_path, "contacts", existing_config
    )
    if not result.ok:
        raise ValueError(
            result.errors[0].message if result.errors else "Unable to save contact link"
        )


def admin_user_routes(
    user_store: AdminWebUserStore, auth: AdminWebAuth, service: AdminRuntimeComponents
) -> list[BaseRoute]:
    async def list_users(request: Request):
        try:
            _require_user_manager(auth, request)
            directory_path = _directory_path_from_request(service, request)
            contacts_config = _contacts_config_for_directory(service, directory_path)
        except PermissionError as exc:
            return json_error(str(exc), status_code=403)
        except ValueError as exc:
            return json_error(str(exc), status_code=400)
        return json_ok(
            {
                "directory_path": directory_path,
                "directory_scoped_config_enabled": service.config.enable_directory_scoped_config,
                "contacts": _contact_options_payload(contacts_config),
                "permission_catalog": sorted(PERMISSION_CATALOG),
                "groups": [_group_payload(group) for group in user_store.list_groups()],
                "users": [
                    _user_payload(
                        user_store,
                        user,
                        auth=auth,
                        directory_path=directory_path,
                        contact_id=contacts_config.user_contact_links.get(user.login),
                        contacts_config=contacts_config,
                    )
                    for user in user_store.list_users()
                ],
            }
        )

    async def upsert_user(request: Request):
        try:
            _require_user_manager(auth, request)
            directory_path = _directory_path_from_request(service, request)
            contacts_config = _contacts_config_for_directory(service, directory_path)
            payload = await _json_body(request)
            if "role" in payload:
                return json_error(
                    "role is no longer supported; use group_assignments",
                    status_code=400,
                )
            login = payload.get("login")
            has_password, password = _optional_password_payload(payload)
            existing_user = user_store.get_user(login)
            normalized_login = normalize_admin_web_login(login)
            if has_password and auth.config.auth_mode != "local_password":
                return json_error("password can only be set when auth mode is local_password")
            if (
                auth.config.auth_mode == "local_password"
                and existing_user is None
                and normalized_login != normalize_admin_web_login(auth.config.username)
                and not has_password
            ):
                return json_error("password is required for new local password users")
            if "contact_id" in payload and not service.config.enable_directory_scoped_config:
                return json_error(
                    "directory-scoped config is disabled; user contact attachments are read-only"
                )
            contact_id = (
                _normalize_contact_update(contacts_config, payload.get("contact_id"))
                if "contact_id" in payload
                else None
            )
            update: dict[str, Any] = {
                "login": login,
            }
            if "display_name" in payload:
                update["display_name"] = payload["display_name"]
            user = user_store.upsert_user(**update)  # type: ignore[arg-type]
            if password is not None:
                user_store.set_local_password(login=user.login, password=password)
            _apply_user_access_payload(
                user_store,
                login=user.login,
                group_assignments=_assignment_payloads(payload),
                workflow_grants=_workflow_grant_payloads(payload),
            )
            if "contact_id" in payload:
                _write_user_contact_link(
                    service,
                    directory_path=directory_path,
                    login=user.login,
                    contact_id=contact_id,
                )
                contacts_config = _contacts_config_for_directory(service, directory_path)
        except PermissionError as exc:
            return json_error(str(exc), status_code=403)
        except ValueError as exc:
            return json_error(str(exc))
        return json_ok(
            {
                "user": _user_payload(
                    user_store,
                    user,
                    auth=auth,
                    directory_path=directory_path,
                    contact_id=contacts_config.user_contact_links.get(user.login),
                    contacts_config=contacts_config,
                )
            }
        )

    async def update_user(request: Request):
        login = request.path_params["login"]
        try:
            _require_user_manager(auth, request)
            directory_path = _directory_path_from_request(service, request)
            contacts_config = _contacts_config_for_directory(service, directory_path)
            payload = await _json_body(request)
            if "role" in payload:
                return json_error(
                    "role is no longer supported; use group_assignments",
                    status_code=400,
                )
            if user_store.get_user(login) is None:
                return json_error("Admin web user not found", status_code=404)
            has_password, password = _optional_password_payload(payload)
            if has_password and auth.config.auth_mode != "local_password":
                return json_error("password can only be set when auth mode is local_password")
            if "contact_id" in payload and not service.config.enable_directory_scoped_config:
                return json_error(
                    "directory-scoped config is disabled; user contact attachments are read-only"
                )
            contact_id = (
                _normalize_contact_update(contacts_config, payload.get("contact_id"))
                if "contact_id" in payload
                else None
            )
            update: dict[str, Any] = {"login": login}
            if "display_name" in payload:
                update["display_name"] = payload["display_name"]
            _apply_user_access_payload(
                user_store,
                login=login,
                group_assignments=_assignment_payloads(payload),
                workflow_grants=_workflow_grant_payloads(payload),
            )
            user = user_store.update_user(**update)
            if user is None:
                return json_error("Admin web user not found", status_code=404)
            if password is not None:
                user_store.set_local_password(login=user.login, password=password)
            if "contact_id" in payload:
                _write_user_contact_link(
                    service,
                    directory_path=directory_path,
                    login=user.login,
                    contact_id=contact_id,
                )
                contacts_config = _contacts_config_for_directory(service, directory_path)
        except PermissionError as exc:
            return json_error(str(exc), status_code=403)
        except ValueError as exc:
            return json_error(str(exc))
        return json_ok(
            {
                "user": _user_payload(
                    user_store,
                    user,
                    auth=auth,
                    directory_path=directory_path,
                    contact_id=contacts_config.user_contact_links.get(user.login),
                    contacts_config=contacts_config,
                )
            }
        )

    async def delete_user(request: Request):
        login = request.path_params["login"]
        try:
            _require_user_manager(auth, request)
            directory_path = _directory_path_from_request(service, request)
            user = user_store.delete_user(login)
            if user is None:
                return json_error("Admin web user not found", status_code=404)
            connections = getattr(service, "ado_user_connections", None)
            if connections is not None:
                connections.remove_user(user.login)
            if service.config.enable_directory_scoped_config:
                _write_user_contact_link(
                    service,
                    directory_path=directory_path,
                    login=user.login,
                    contact_id=None,
                )
        except PermissionError as exc:
            return json_error(str(exc), status_code=403)
        except ValueError as exc:
            return json_error(str(exc))
        return json_ok(
            {"user": _user_payload(user_store, user, auth=auth, directory_path=directory_path)}
        )

    async def list_groups(request: Request):
        try:
            _require_group_manager(auth, request)
        except PermissionError as exc:
            return json_error(str(exc), status_code=403)
        return json_ok(
            {
                "permission_catalog": sorted(PERMISSION_CATALOG),
                "groups": [_group_payload(group) for group in user_store.list_groups()],
            }
        )

    async def upsert_group(request: Request):
        try:
            _require_group_manager(auth, request)
            payload = await _json_body(request)
            group_id = payload.get("group_id")
            display_name = payload.get("display_name")
            group_update: dict[str, Any] = {
                "group_id": group_id,
                "display_name": display_name,
            }
            if "description" in payload:
                group_update["description"] = payload.get("description")
            group = user_store.upsert_group(**group_update)
            if "permissions" in payload:
                permissions = payload.get("permissions")
                if not isinstance(permissions, list):
                    return json_error("permissions must be a JSON array")
                group = user_store.set_group_permissions(group.group_id, permissions)
        except PermissionError as exc:
            return json_error(str(exc), status_code=403)
        except ValueError as exc:
            return json_error(str(exc))
        return json_ok({"group": _group_payload(group)})

    async def update_group(request: Request):
        group_id = request.path_params["group_id"]
        try:
            _require_group_manager(auth, request)
            payload = await _json_body(request)
            existing = user_store.get_group(group_id)
            if existing is None:
                return json_error("Admin web group not found", status_code=404)
            group = user_store.upsert_group(
                group_id=group_id,
                display_name=payload.get("display_name", existing.display_name),
                description=(
                    payload.get("description") if "description" in payload else existing.description
                ),
            )
            if "permissions" in payload:
                permissions = payload.get("permissions")
                if not isinstance(permissions, list):
                    return json_error("permissions must be a JSON array")
                group = user_store.set_group_permissions(group.group_id, permissions)
        except PermissionError as exc:
            return json_error(str(exc), status_code=403)
        except ValueError as exc:
            return json_error(str(exc))
        return json_ok({"group": _group_payload(group)})

    async def delete_group(request: Request):
        group_id = request.path_params["group_id"]
        try:
            _require_group_manager(auth, request)
            group = user_store.delete_group(group_id)
            if group is None:
                return json_error("Admin web group not found", status_code=404)
        except PermissionError as exc:
            return json_error(str(exc), status_code=403)
        except ValueError as exc:
            return json_error(str(exc))
        return json_ok({"group": _group_payload(group)})

    async def get_workflow_tutorial_progress(request: Request):
        try:
            login = _current_preference_login(auth, request)
            payload = user_store.get_user_preference(
                login, WORKFLOW_TUTORIAL_PROGRESS_PREFERENCE_KEY
            )
        except PermissionError as exc:
            return json_error(str(exc), status_code=401)
        return json_ok(_workflow_tutorial_progress_payload(payload))

    async def put_workflow_tutorial_progress(request: Request):
        try:
            login = _current_preference_login(auth, request)
            payload = _workflow_tutorial_progress_payload(await _json_body(request))
            user_store.put_user_preference(
                login,
                WORKFLOW_TUTORIAL_PROGRESS_PREFERENCE_KEY,
                payload,
            )
        except PermissionError as exc:
            return json_error(str(exc), status_code=401)
        except ValueError as exc:
            return json_error(str(exc))
        return json_ok(payload)

    return [
        Route("/api/admin/users", list_users, methods=["GET"]),
        Route("/api/admin/users", upsert_user, methods=["POST"]),
        Route("/api/admin/users/{login}", update_user, methods=["PATCH"]),
        Route("/api/admin/users/{login}", delete_user, methods=["DELETE"]),
        Route("/api/admin/groups", list_groups, methods=["GET"]),
        Route("/api/admin/groups", upsert_group, methods=["POST"]),
        Route("/api/admin/groups/{group_id}", update_group, methods=["PATCH"]),
        Route("/api/admin/groups/{group_id}", delete_group, methods=["DELETE"]),
        Route(
            "/api/admin/user-preferences/workflow-tutorial-progress",
            get_workflow_tutorial_progress,
            methods=["GET"],
        ),
        Route(
            "/api/admin/user-preferences/workflow-tutorial-progress",
            put_workflow_tutorial_progress,
            methods=["PUT"],
        ),
    ]


__all__ = ["admin_user_routes"]

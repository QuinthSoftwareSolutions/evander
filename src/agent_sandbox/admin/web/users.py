from __future__ import annotations

from typing import Any

from starlette.requests import Request
from starlette.routing import BaseRoute, Route

from agent_sandbox.admin.service import AdminService
from agent_sandbox.admin.web.shared import AdminWebAuth, json_error, json_ok
from agent_sandbox.admin.web.user_store import AdminWebUserRole, AdminWebUserStore
from agent_sandbox.directory_config import ContactDirectoryConfig


def _user_payload(
    user,
    *,
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
    return payload


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


def _require_user_manager(auth: AdminWebAuth, request: Request) -> None:
    auth.require(request)
    if not auth.can_manage_users(request):
        raise PermissionError("Admin user role is required")


def _directory_path_from_request(service: AdminService, request: Request) -> str:
    requested = str(request.query_params.get("directory_path") or "root").strip() or "root"
    return service._normalize_directory_reference(requested)


def _contacts_config_for_directory(
    service: AdminService, directory_path: str
) -> ContactDirectoryConfig:
    result = service.compute_directory_effective_config(directory_path, "contacts")
    if not result.ok or result.data is None:
        return ContactDirectoryConfig()
    return ContactDirectoryConfig.model_validate(result.data.resolution.effective_config)


def _normalize_contact_update(
    contacts_config: ContactDirectoryConfig, value: object
) -> str | None:
    if value is None:
        return None
    contact_id = str(value).strip()
    if not contact_id:
        return None
    if contact_id not in contacts_config.contacts:
        raise ValueError(f"Unknown contact id: {contact_id}")
    return contact_id


def _write_user_contact_link(
    service: AdminService,
    *,
    directory_path: str,
    login: str,
    contact_id: str | None,
) -> None:
    if not service.directory_scoped_config_enabled:
        raise ValueError(
            "directory-scoped config is disabled; user contact attachments are read-only"
        )
    existing_result = service.read_directory_domain_config(directory_path, "contacts")
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
    result = service.write_directory_domain_config(directory_path, "contacts", existing_config)
    if not result.ok:
        raise ValueError(result.errors[0].message if result.errors else "Unable to save contact link")


def admin_user_routes(
    user_store: AdminWebUserStore, auth: AdminWebAuth, service: AdminService
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
                "directory_scoped_config_enabled": service.directory_scoped_config_enabled,
                "contacts": _contact_options_payload(contacts_config),
                "users": [
                    _user_payload(
                        user,
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
            login = payload.get("login")
            role = str(payload.get("role") or "").strip().lower()
            if role not in {"admin", "regular"}:
                return json_error("role must be admin or regular")
            if "contact_id" in payload and not service.directory_scoped_config_enabled:
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
                "role": role,
            }
            if "display_name" in payload:
                update["display_name"] = payload["display_name"]
            user = user_store.upsert_user(**update)  # type: ignore[arg-type]
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
                    user,
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
            raw_role = payload.get("role")
            role: AdminWebUserRole | None
            if raw_role is None:
                role = None
            else:
                candidate = str(raw_role).strip().lower()
                if candidate not in {"admin", "regular"}:
                    return json_error("role must be admin or regular")
                role = candidate  # type: ignore[assignment]
            if "contact_id" in payload and not service.directory_scoped_config_enabled:
                return json_error(
                    "directory-scoped config is disabled; user contact attachments are read-only"
                )
            contact_id = (
                _normalize_contact_update(contacts_config, payload.get("contact_id"))
                if "contact_id" in payload
                else None
            )
            update: dict[str, Any] = {"login": login, "role": role}
            if "display_name" in payload:
                update["display_name"] = payload["display_name"]
            user = user_store.update_user(**update)
            if user is None:
                return json_error("Admin web user not found", status_code=404)
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
                    user,
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
            if service.directory_scoped_config_enabled:
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
        return json_ok({"user": _user_payload(user)})

    return [
        Route("/api/admin/users", list_users, methods=["GET"]),
        Route("/api/admin/users", upsert_user, methods=["POST"]),
        Route("/api/admin/users/{login}", update_user, methods=["PATCH"]),
        Route("/api/admin/users/{login}", delete_user, methods=["DELETE"]),
    ]


__all__ = ["admin_user_routes"]

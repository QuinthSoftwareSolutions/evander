from __future__ import annotations

from agent_sandbox.directory_config import ContactDirectoryConfig


def _normalize_contact_ids(value: object, *, field_name: str) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field_name} must be a JSON array of strings or null")
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        contact_id = str(item or "").strip()
        if not contact_id:
            raise ValueError(f"{field_name} cannot contain empty values")
        if contact_id in seen:
            continue
        seen.add(contact_id)
        normalized.append(contact_id)
    return normalized


def validate_allowed_contact_ids_for_directory(
    contacts_config: ContactDirectoryConfig,
    allowed_contact_ids: object,
    *,
    session_id: str,
    directory_path: str,
) -> list[str] | None:
    normalized_allowed_contact_ids = _normalize_contact_ids(
        allowed_contact_ids,
        field_name="allowed_contact_ids",
    )
    if normalized_allowed_contact_ids is None:
        return None

    unknown_contact_ids = sorted(
        {
            contact_id
            for contact_id in normalized_allowed_contact_ids
            if contact_id not in contacts_config.contacts
        }
    )
    if unknown_contact_ids:
        raise ValueError(
            f"contacts allowlist for session {session_id!r} and directory {directory_path!r} "
            f"references unknown contact ids: {', '.join(unknown_contact_ids)}"
        )
    return normalized_allowed_contact_ids


def resolve_effective_contact_directory(
    contacts_config: ContactDirectoryConfig,
    allowed_contact_ids: object,
    *,
    session_id: str,
    directory_path: str,
) -> ContactDirectoryConfig:
    normalized_allowed_contact_ids = validate_allowed_contact_ids_for_directory(
        contacts_config,
        allowed_contact_ids,
        session_id=session_id,
        directory_path=directory_path,
    )
    if normalized_allowed_contact_ids is None:
        return ContactDirectoryConfig.model_validate(
            contacts_config.model_dump(mode="json")
        )

    contacts = {
        contact_id: contacts_config.contacts[contact_id].model_dump(mode="json")
        for contact_id in normalized_allowed_contact_ids
    }
    payload = contacts_config.model_dump(mode="json")
    payload["contacts"] = contacts
    return ContactDirectoryConfig.model_validate(payload)


def require_allowed_contact_id(
    contact_id: object,
    allowed_contact_ids: object,
    *,
    session_id: str,
) -> str:
    normalized_contact_id = str(contact_id or "").strip()
    if not normalized_contact_id:
        raise ValueError("contact id must be a non-empty string")

    normalized_allowed_contact_ids = _normalize_contact_ids(
        allowed_contact_ids,
        field_name="allowed_contact_ids",
    )
    if normalized_allowed_contact_ids is None:
        return normalized_contact_id

    if normalized_contact_id not in set(normalized_allowed_contact_ids):
        raise ValueError(
            f"Contact {normalized_contact_id!r} is not allowed for session {session_id!r}"
        )
    return normalized_contact_id

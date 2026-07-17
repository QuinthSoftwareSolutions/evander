from __future__ import annotations

import pytest

from evander.contact_allowlists import (
    require_allowed_contact_id,
    resolve_effective_contact_directory,
    validate_allowed_contact_ids_for_directory,
)
from evander.directory_config import ContactDirectoryConfig


def _contacts() -> ContactDirectoryConfig:
    return ContactDirectoryConfig.model_validate(
        {
            "contacts": {
                "ops": {
                    "name": "Ops",
                    "description": "Ops contact",
                    "email_addresses": ["ops@example.com"],
                    "phone_numbers": [{"label": "main", "number": "+15550001", "primary": True}],
                },
                "support": {
                    "name": "Support",
                    "description": "Support contact",
                    "email_addresses": ["support@example.com"],
                    "phone_numbers": [{"label": "main", "number": "+15550002", "primary": True}],
                },
            },
            "user_contact_links": {"agent": "ops"},
        }
    )


def test_resolve_effective_contact_directory_keeps_all_contacts_when_allowlist_missing() -> None:
    contacts = _contacts()

    effective = resolve_effective_contact_directory(
        contacts,
        None,
        session_id="session-1",
        directory_path="root/team",
    )

    assert list(effective.contacts) == ["ops", "support"]
    assert effective.user_contact_links == {"agent": "ops"}


def test_resolve_effective_contact_directory_filters_to_allowlisted_contacts_only() -> None:
    contacts = _contacts()

    effective = resolve_effective_contact_directory(
        contacts,
        [" ops ", "ops", "support"],
        session_id="session-1",
        directory_path="root/team",
    )

    assert list(effective.contacts) == ["ops", "support"]


def test_resolve_effective_contact_directory_returns_empty_contacts_for_empty_allowlist() -> None:
    contacts = _contacts()

    effective = resolve_effective_contact_directory(
        contacts,
        [],
        session_id="session-1",
        directory_path="root/team",
    )

    assert effective.contacts == {}
    assert effective.user_contact_links == {"agent": "ops"}


def test_validate_allowed_contact_ids_for_directory_rejects_unknown_ids() -> None:
    contacts = _contacts()

    with pytest.raises(
        ValueError,
        match=(
            r"contacts allowlist for session 'session-1' and directory 'root/team' "
            r"references unknown contact ids: missing"
        ),
    ):
        validate_allowed_contact_ids_for_directory(
            contacts,
            ["support", "missing"],
            session_id="session-1",
            directory_path="root/team",
        )


def test_require_allowed_contact_id_rejects_contacts_outside_allowlist() -> None:
    with pytest.raises(
        ValueError,
        match=r"Contact 'support' is not allowed for session 'session-1'",
    ):
        require_allowed_contact_id(
            "support",
            ["ops"],
            session_id="session-1",
        )


def test_require_allowed_contact_id_allows_contact_when_allowlist_missing() -> None:
    assert (
        require_allowed_contact_id(
            " support ",
            None,
            session_id="session-1",
        )
        == "support"
    )

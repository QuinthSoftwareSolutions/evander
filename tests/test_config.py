import pytest

from agent_sandbox.config import GatewayConfig


def test_folder_allowlist_defaults_populate_only_when_mapping_is_empty() -> None:
    config = GatewayConfig.model_validate(
        {
            "email_mailboxes": [
                {"mailbox_id": "primary", "folder_allowlist_defaults": ["inbox", "drafts"]},
                {"mailbox_id": "secondary", "folder_allowlist_defaults": ["inbox"]},
            ],
            "email_folder_allowlist_by_mailbox": {"primary": ["inbox"]},
        }
    )

    assert config.email_folder_allowlist_by_mailbox == {"primary": ["inbox"]}


def test_folder_allowlist_defaults_populate_all_mailboxes_when_mapping_left_empty() -> None:
    config = GatewayConfig.model_validate(
        {
            "email_mailboxes": [
                {"mailbox_id": "primary", "folder_allowlist_defaults": ["inbox", "drafts"]},
                {"mailbox_id": "secondary", "folder_allowlist_defaults": ["inbox"]},
            ],
        }
    )

    assert config.email_folder_allowlist_by_mailbox == {
        "primary": ["inbox", "drafts"],
        "secondary": ["inbox"],
    }


def test_duplicate_email_mailbox_ids_fail_validation() -> None:
    with pytest.raises(ValueError, match="Duplicate email mailbox IDs are not allowed: primary"):
        GatewayConfig.model_validate(
            {
                "email_mailboxes": [
                    {"mailbox_id": "primary"},
                    {"mailbox_id": "primary"},
                ]
            }
        )


def test_unique_email_mailbox_ids_pass_validation_with_alias_fields() -> None:
    config = GatewayConfig.model_validate(
        {
            "email_mailboxes": [
                {
                    "mailbox_id": "primary",
                    "account": "primary@example.com",
                    "auth_env_var_names": {"access_token": "PRIMARY_TOKEN"},
                },
                {
                    "mailbox_id": "secondary",
                    "address": "secondary@example.com",
                    "auth_env_var_names": {"access_token": "SECONDARY_TOKEN"},
                },
            ]
        }
    )

    assert [mailbox.mailbox_id for mailbox in config.email_mailboxes] == ["primary", "secondary"]
    assert [mailbox.account_identifier for mailbox in config.email_mailboxes] == [
        "primary@example.com",
        "secondary@example.com",
    ]
    assert [mailbox.auth_secret_refs for mailbox in config.email_mailboxes] == [
        {"access_token": "PRIMARY_TOKEN"},
        {"access_token": "SECONDARY_TOKEN"},
    ]

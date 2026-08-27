from __future__ import annotations

from types import SimpleNamespace

import pytest

from evander.email.providers import EmailAttachmentContent, EmailAttachmentMetadata
from evander_team_addon.chat_attachments import TeamChatAttachmentStorage
from evander_team_addon.external_chat_attachments import (
    ExternalTeamChatAttachmentImporter,
)
from evander_team_addon.models import TeamChatAttachmentPolicy


class _TeamsOrDiscordAttachmentService:
    def __init__(self, *, expected_target_key: str, expected_target: str) -> None:
        self.expected_target_key = expected_target_key
        self.expected_target = expected_target

    def list_attachments(self, message_id, **kwargs):
        assert message_id == "message-1"
        assert kwargs[self.expected_target_key] == self.expected_target
        return [
            {
                "id": "attachment-1",
                "name": "screen.png",
                "contentType": "image/png",
                "size": 8,
            }
        ]

    def download_attachment(self, message_id, attachment_id, **kwargs):
        assert message_id == "message-1"
        assert attachment_id == "attachment-1"
        assert kwargs[self.expected_target_key] == self.expected_target
        return {
            "name": "screen.png",
            "contentType": "image/png",
            "content": b"png-data",
        }


class _EmailAttachmentService:
    def list_attachments(self, *, mailbox_id, message_id):
        assert mailbox_id == "support@example.test"
        assert message_id == "message-1"
        return [
            EmailAttachmentMetadata(
                attachment_id="attachment-1",
                filename="screen.png",
                mime_type="image/png",
                byte_count=8,
            )
        ]

    def download_attachment(self, *, mailbox_id, message_id, attachment_id):
        assert mailbox_id == "support@example.test"
        assert message_id == "message-1"
        assert attachment_id == "attachment-1"
        return EmailAttachmentContent(
            attachment_id=attachment_id,
            filename="screen.png",
            mime_type="image/png",
            byte_count=8,
            content=b"png-data",
        )


@pytest.mark.parametrize(
    ("provider", "target_address"),
    [
        ("teams", "team/channel"),
        ("discord", "123456789012345678/123456789012345679"),
        ("email", "support@example.test"),
    ],
)
def test_imports_supported_provider_attachments_with_provenance(
    tmp_path,
    provider,
    target_address,
) -> None:
    teams_service = _TeamsOrDiscordAttachmentService(
        expected_target_key="channel_target",
        expected_target="team/channel",
    )
    discord_service = _TeamsOrDiscordAttachmentService(
        expected_target_key="channel_id",
        expected_target="123456789012345679",
    )
    email_service = _EmailAttachmentService()
    runtime = SimpleNamespace(
        teams_service_for_session=lambda _session_id: teams_service,
        discord_service_for_session=lambda _session_id: discord_service,
        email_service_for_session=lambda _session_id: email_service,
    )

    result = ExternalTeamChatAttachmentImporter(
        runtime,
        TeamChatAttachmentStorage(tmp_path),
    ).import_message_attachments(
        session_id="session-1",
        provider=provider,
        communication_target_id="source-target",
        target_address=target_address,
        source_message_id="message-1",
        policy=TeamChatAttachmentPolicy.root_defaults(),
    )

    assert result.status == "complete"
    assert result.discovered_count == 1
    assert len(result.attachments) == 1
    attachment = result.attachments[0]
    assert attachment.filename == "screen.png"
    assert attachment.external_source is not None
    assert attachment.external_source.provider == provider
    assert attachment.external_source.source_message_id == "message-1"
    assert attachment.external_source.source_attachment_id == "attachment-1"


def test_partial_import_keeps_valid_files_and_reports_policy_failures(tmp_path) -> None:
    class _MixedService:
        def list_attachments(self, _message_id, *, channel_target=None):
            return [
                {"id": "good", "name": "screen.png", "contentType": "image/png", "size": 4},
                {
                    "id": "blocked",
                    "name": "payload.exe",
                    "contentType": "application/octet-stream",
                    "size": 4,
                },
                {"id": "extra", "name": "extra.png", "contentType": "image/png", "size": 4},
            ]

        def download_attachment(
            self,
            _message_id,
            attachment_id,
            *,
            channel_target=None,
        ):
            return {
                "name": "screen.png" if attachment_id == "good" else "payload.exe",
                "contentType": (
                    "image/png" if attachment_id == "good" else "application/octet-stream"
                ),
                "content": b"data",
            }

    policy = TeamChatAttachmentPolicy.root_defaults().model_copy(
        update={"max_attachments_per_message": 2}
    )
    result = ExternalTeamChatAttachmentImporter(
        SimpleNamespace(teams_service_for_session=lambda _session_id: _MixedService()),
        TeamChatAttachmentStorage(tmp_path),
    ).import_message_attachments(
        session_id="session-1",
        provider="teams",
        communication_target_id="teams-target",
        target_address="team/channel",
        source_message_id="message-1",
        policy=policy,
    )

    assert result.status == "partial"
    assert [item.filename for item in result.attachments] == ["screen.png"]
    assert {failure.code for failure in result.failures} == {
        "count_limit",
        "policy_rejected",
    }


def test_partial_import_keeps_valid_files_when_other_metadata_is_malformed(tmp_path) -> None:
    class _MalformedMetadataService:
        def list_attachments(self, _message_id, *, channel_target=None):
            return [
                {"name": "missing-id.png", "contentType": "image/png", "size": 4},
                {"id": "good", "name": "screen.png", "contentType": "image/png", "size": 4},
            ]

        def download_attachment(
            self,
            _message_id,
            attachment_id,
            *,
            channel_target=None,
        ):
            assert attachment_id == "good"
            return {
                "name": "screen.png",
                "contentType": "image/png",
                "content": b"data",
            }

    result = ExternalTeamChatAttachmentImporter(
        SimpleNamespace(
            teams_service_for_session=lambda _session_id: _MalformedMetadataService()
        ),
        TeamChatAttachmentStorage(tmp_path),
    ).import_message_attachments(
        session_id="session-1",
        provider="teams",
        communication_target_id="teams-target",
        target_address="team/channel",
        source_message_id="message-1",
        policy=TeamChatAttachmentPolicy.root_defaults(),
    )

    assert result.status == "partial"
    assert result.discovered_count == 2
    assert [item.filename for item in result.attachments] == ["screen.png"]
    assert [failure.code for failure in result.failures] == ["metadata_invalid"]

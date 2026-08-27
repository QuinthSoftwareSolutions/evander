from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest

from evander.email.providers import EmailAttachmentContent, EmailAttachmentMetadata
from evander.messaging.discord_inbox_store import DiscordInboxStore
from evander.messaging.teams_inbox_store import TeamsInboxMessage, TeamsInboxStore
from evander.models import DiscordCommandEnvelope, DiscordMessageEnvelope, TeamsMessageEnvelope
from evander.repositories.records import JsonDiscordRecordsRepository, JsonTeamsRecordsRepository
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
        assert mailbox_id == "default"
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
        assert mailbox_id == "default"
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
        teams_service_for_directory=lambda _directory, **_kwargs: teams_service,
        discord_service_for_directory=lambda _directory, **_kwargs: discord_service,
        email_service_for_directory=lambda _directory: email_service,
    )

    result = ExternalTeamChatAttachmentImporter(
        runtime,
        TeamChatAttachmentStorage(tmp_path),
    ).import_message_attachments(
        source_directory_path="root/source",
        provider=provider,
        communication_target_id="source-target",
        target_address=target_address,
        source_message_id="message-1",
        policy=TeamChatAttachmentPolicy.root_defaults(),
        auth_profile_ref="email.default" if provider == "email" else None,
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
            max_bytes=None,
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
        SimpleNamespace(teams_service_for_directory=lambda _directory, **_kwargs: _MixedService()),
        TeamChatAttachmentStorage(tmp_path),
    ).import_message_attachments(
        source_directory_path="root/source",
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
            max_bytes=None,
        ):
            assert attachment_id == "good"
            return {
                "name": "screen.png",
                "contentType": "image/png",
                "content": b"data",
            }

    result = ExternalTeamChatAttachmentImporter(
        SimpleNamespace(
            teams_service_for_directory=lambda _directory, **_kwargs: _MalformedMetadataService()
        ),
        TeamChatAttachmentStorage(tmp_path),
    ).import_message_attachments(
        source_directory_path="root/source",
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


@pytest.mark.parametrize("wrapped", [False, True])
def test_provider_url_credentials_are_not_recorded_in_import_failures(
    tmp_path,
    wrapped,
) -> None:
    signed_url = "https://cdn.example.test/file.png?sig=host-secret#fragment"
    request = httpx.Request("GET", signed_url)
    response = httpx.Response(403, request=request)
    http_error = httpx.HTTPStatusError(
        "provider download failed",
        request=request,
        response=response,
    )

    class _FailingService:
        def list_attachments(self, _message_id, *, channel_target=None):
            return [{"id": "attachment-1", "name": "screen.png"}]

        def download_attachment(
            self,
            _message_id,
            _attachment_id,
            *,
            channel_target=None,
            max_bytes=None,
        ):
            if wrapped:
                raise RuntimeError(f"provider request failed for {signed_url}") from http_error
            raise http_error

    result = ExternalTeamChatAttachmentImporter(
        SimpleNamespace(
            teams_service_for_directory=lambda _directory, **_kwargs: _FailingService()
        ),
        TeamChatAttachmentStorage(tmp_path),
    ).import_message_attachments(
        source_directory_path="root/source",
        provider="teams",
        communication_target_id="teams-target",
        target_address="team/channel",
        source_message_id="message-1",
        policy=TeamChatAttachmentPolicy.root_defaults(),
    )

    assert result.status == "failed"
    assert len(result.failures) == 1
    failure_message = result.failures[0].message
    assert "host-secret" not in failure_message
    assert "cdn.example.test" not in failure_message
    assert failure_message == ("RuntimeError" if wrapped else "HTTPStatusError (HTTP 403)")


def test_discord_thread_import_uses_source_directory_and_thread_for_all_adapter_calls(
    tmp_path,
) -> None:
    calls: list[tuple[str, str, list[str], str, int | None]] = []

    class _ThreadAttachmentService:
        def list_attachments(self, message_id, *, channel_id=None):
            calls.append(("list", message_id, [], str(channel_id), None))
            return [
                {
                    "id": "attachment-1",
                    "filename": "thread.png",
                    "content_type": "image/png",
                    "size": 4,
                }
            ]

        def download_attachment(
            self,
            message_id,
            attachment_id,
            *,
            channel_id=None,
            max_bytes=None,
        ):
            calls.append(("download", message_id, [], str(channel_id), max_bytes))
            assert attachment_id == "attachment-1"
            return {
                "filename": "thread.png",
                "content_type": "image/png",
                "content": b"data",
            }

    service = _ThreadAttachmentService()

    def service_for_directory(directory_path, *, allowed_channel_ids):
        calls.append(("resolve", directory_path, list(allowed_channel_ids), "", None))
        return service

    policy = TeamChatAttachmentPolicy.root_defaults()
    result = ExternalTeamChatAttachmentImporter(
        SimpleNamespace(discord_service_for_directory=service_for_directory),
        TeamChatAttachmentStorage(tmp_path),
    ).import_message_attachments(
        source_directory_path="root/source",
        provider="discord",
        communication_target_id="discord-target",
        target_address="guild/parent-channel",
        source_message_id="thread-message",
        source_thread_id="child-thread",
        policy=policy,
    )

    assert result.status == "complete"
    assert calls == [
        ("resolve", "root/source", ["parent-channel", "child-thread"], "", None),
        ("list", "thread-message", [], "child-thread", None),
        ("resolve", "root/source", ["parent-channel", "child-thread"], "", None),
        (
            "download",
            "thread-message",
            [],
            "child-thread",
            policy.max_attachment_bytes,
        ),
    ]


def test_email_unknown_size_is_rejected_before_download_when_policy_has_a_cap(
    tmp_path,
) -> None:
    class _UnknownSizeEmailService:
        def list_attachments(self, *, mailbox_id, message_id):
            return [
                EmailAttachmentMetadata(
                    attachment_id="attachment-1",
                    filename="unknown.png",
                    mime_type="image/png",
                    byte_count=None,
                )
            ]

        def download_attachment(self, **_kwargs):
            raise AssertionError("unknown-size email attachment must not be downloaded")

    result = ExternalTeamChatAttachmentImporter(
        SimpleNamespace(email_service_for_directory=lambda _directory: _UnknownSizeEmailService()),
        TeamChatAttachmentStorage(tmp_path),
    ).import_message_attachments(
        source_directory_path="root/source",
        provider="email",
        communication_target_id="email-target",
        target_address="support@example.test",
        source_message_id="message-1",
        policy=TeamChatAttachmentPolicy.root_defaults(),
        auth_profile_ref="email.default",
    )

    assert result.status == "failed"
    assert result.failures[0].code == "policy_rejected"
    assert "omitted size" in result.failures[0].message


def test_import_heals_teams_message_persisted_only_in_ingress_store(tmp_path) -> None:
    inbox_message = TeamsInboxMessage(
        cursor="team-a/channel-a/old-message",
        message_id="old-message",
        team_id="team-a",
        channel_id="channel-a",
        thread_id="old-message",
        author_id="operator-a",
        content="historical attachment",
        created_at=datetime(2026, 8, 27, tzinfo=UTC),
        metadata={
            "source": "teams_graph_api",
            "target": "team-a/channel-a",
            "attachments": [
                {
                    "id": "attachment-1",
                    "name": "report.txt",
                    "contentType": "text/plain",
                    "size": 4,
                }
            ],
        },
    )
    TeamsInboxStore(tmp_path / "teams-inbox-store.db").add_message_if_new(inbox_message)
    records = JsonTeamsRecordsRepository(tmp_path / "teams-messages.json")

    class _StoreBackedTeamsService:
        def list_attachments(self, message_id, *, channel_target=None):
            mirrored = records.get(inbox_message.cursor)
            assert mirrored is not None
            assert mirrored.message_id == message_id
            return list(mirrored.metadata["attachments"])

        def download_attachment(
            self,
            message_id,
            attachment_id,
            *,
            channel_target=None,
            max_bytes=None,
        ):
            assert records.get(inbox_message.cursor) is not None
            return {
                "filename": "report.txt",
                "content_type": "text/plain",
                "content": b"data",
            }

    service = _StoreBackedTeamsService()
    runtime = SimpleNamespace(
        config=SimpleNamespace(runtime_dir=tmp_path),
        teams_messages=records,
        teams_service_for_directory=lambda _directory, **_kwargs: service,
    )

    result = ExternalTeamChatAttachmentImporter(
        runtime,
        TeamChatAttachmentStorage(tmp_path),
    ).import_message_attachments(
        source_directory_path="root/source",
        provider="teams",
        communication_target_id="teams-target",
        target_address="team-a/channel-a",
        source_message_id="old-message",
        policy=TeamChatAttachmentPolicy.root_defaults(),
    )

    assert result.status == "complete"
    assert records.get(inbox_message.cursor) is not None


def test_import_heals_discord_thread_message_persisted_only_in_ingress_store(
    tmp_path,
) -> None:
    inbox_message = DiscordMessageEnvelope(
        message_id="old-thread-message",
        channel_id="child-thread",
        author_id="operator-a",
        author_name="Ada",
        content="historical attachment",
        created_at=datetime(2026, 8, 27, tzinfo=UTC),
        thread_id="child-thread",
        parent_channel_id="parent-channel",
        raw={
            "source": "discord_bot_api",
            "channel_id": "child-thread",
            "thread_id": "child-thread",
            "parent_channel_id": "parent-channel",
            "attachments": [
                {
                    "id": "attachment-1",
                    "filename": "report.txt",
                    "content_type": "text/plain",
                    "size": 4,
                    "url": "https://cdn.discord.test/attachment-1",
                }
            ],
        },
    )
    DiscordInboxStore(tmp_path / "discord-inbox-store.db").add_message_if_new(inbox_message)
    records = JsonDiscordRecordsRepository(tmp_path / "discord-commands.json")

    class _StoreBackedDiscordService:
        def list_attachments(self, message_id, *, channel_id=None):
            mirrored = records.get(message_id)
            assert mirrored is not None
            assert mirrored.channel_id == channel_id
            return list(mirrored.metadata["attachments"])

        def download_attachment(
            self,
            message_id,
            attachment_id,
            *,
            channel_id=None,
            max_bytes=None,
        ):
            assert records.get(message_id) is not None
            return {
                "filename": "report.txt",
                "content_type": "text/plain",
                "content": b"data",
            }

    service = _StoreBackedDiscordService()
    runtime = SimpleNamespace(
        config=SimpleNamespace(runtime_dir=tmp_path),
        discord_commands=records,
        discord_service_for_directory=lambda _directory, **_kwargs: service,
    )

    result = ExternalTeamChatAttachmentImporter(
        runtime,
        TeamChatAttachmentStorage(tmp_path),
    ).import_message_attachments(
        source_directory_path="root/source",
        provider="discord",
        communication_target_id="discord-target",
        target_address="guild/parent-channel",
        source_message_id="old-thread-message",
        source_thread_id="child-thread",
        policy=TeamChatAttachmentPolicy.root_defaults(),
    )

    assert result.status == "complete"
    assert records.get("old-thread-message") is not None


@pytest.mark.parametrize(
    ("provider", "target_address"),
    [
        ("teams", "team-a/channel-a"),
        ("discord", "guild/channel-a"),
    ],
)
def test_import_rejects_adapter_record_that_is_absent_from_ingress_store(
    tmp_path,
    provider,
    target_address,
) -> None:
    def unexpected_service_resolution(*_args, **_kwargs):
        raise AssertionError("provider adapter must not be consulted after an ingress miss")

    if provider == "teams":
        teams_records = JsonTeamsRecordsRepository(tmp_path / "teams-messages.json")
        teams_records.put(
            "team-a/channel-a/central-only",
            TeamsMessageEnvelope(
                cursor="team-a/channel-a/central-only",
                message_id="central-only",
                author_id="operator-a",
                team_id="team-a",
                channel_id="channel-a",
                content="not ingress authorized",
                created_at=datetime(2026, 8, 27, tzinfo=UTC),
                metadata={
                    "target": "team-a/channel-a",
                    "attachments": [{"id": "attachment-1", "size": 4}],
                },
            ),
        )
        runtime = SimpleNamespace(
            config=SimpleNamespace(runtime_dir=tmp_path),
            teams_messages=teams_records,
            teams_service_for_directory=unexpected_service_resolution,
        )
    else:
        discord_records = JsonDiscordRecordsRepository(tmp_path / "discord-commands.json")
        discord_records.put(
            "central-only",
            DiscordCommandEnvelope(
                command_id="central-only",
                author_id="operator-a",
                channel_id="channel-a",
                content="not ingress authorized",
                created_at=datetime(2026, 8, 27, tzinfo=UTC),
                metadata={"attachments": [{"id": "attachment-1", "size": 4}]},
            ),
        )
        runtime = SimpleNamespace(
            config=SimpleNamespace(runtime_dir=tmp_path),
            discord_commands=discord_records,
            discord_service_for_directory=unexpected_service_resolution,
        )

    result = ExternalTeamChatAttachmentImporter(
        runtime,
        TeamChatAttachmentStorage(tmp_path),
    ).import_message_attachments(
        source_directory_path="root/source",
        provider=provider,
        communication_target_id=f"{provider}-target",
        target_address=target_address,
        source_message_id="central-only",
        policy=TeamChatAttachmentPolicy.root_defaults(),
    )

    assert result.status == "failed"
    assert result.failures[0].code == "list_failed"
    assert result.failures[0].message == (
        f"{provider} source message is not present in the ingress store"
    )


def test_import_rejects_teams_ingress_message_from_a_different_thread(tmp_path) -> None:
    TeamsInboxStore(tmp_path / "teams-inbox-store.db").add_message_if_new(
        TeamsInboxMessage(
            cursor="team-a/channel-a/message-1",
            message_id="message-1",
            team_id="team-a",
            channel_id="channel-a",
            thread_id="actual-thread",
            author_id="operator-a",
            content="thread attachment",
            created_at=datetime(2026, 8, 27, tzinfo=UTC),
            metadata={"source": "teams_graph_api", "target": "team-a/channel-a"},
        )
    )

    result = ExternalTeamChatAttachmentImporter(
        SimpleNamespace(
            config=SimpleNamespace(runtime_dir=tmp_path),
            teams_messages=JsonTeamsRecordsRepository(tmp_path / "teams-messages.json"),
            teams_service_for_directory=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("mismatched Teams thread must not reach the adapter")
            ),
        ),
        TeamChatAttachmentStorage(tmp_path),
    ).import_message_attachments(
        source_directory_path="root/source",
        provider="teams",
        communication_target_id="teams-target",
        target_address="team-a/channel-a",
        source_message_id="message-1",
        source_thread_id="forged-thread",
        policy=TeamChatAttachmentPolicy.root_defaults(),
    )

    assert result.status == "failed"
    assert result.failures[0].message == (
        "teams source message is not present in the ingress store"
    )


def test_import_rejects_discord_thread_from_a_different_parent(tmp_path) -> None:
    DiscordInboxStore(tmp_path / "discord-inbox-store.db").add_message_if_new(
        DiscordMessageEnvelope(
            message_id="message-1",
            channel_id="child-thread",
            author_id="operator-a",
            author_name="Ada",
            content="thread attachment",
            created_at=datetime(2026, 8, 27, tzinfo=UTC),
            thread_id="child-thread",
            parent_channel_id="other-parent",
            raw={"source": "discord_bot_api"},
        )
    )

    result = ExternalTeamChatAttachmentImporter(
        SimpleNamespace(
            config=SimpleNamespace(runtime_dir=tmp_path),
            discord_commands=JsonDiscordRecordsRepository(tmp_path / "discord-commands.json"),
            discord_service_for_directory=lambda *_args, **_kwargs: (_ for _ in ()).throw(
                AssertionError("mismatched Discord parent must not reach the adapter")
            ),
        ),
        TeamChatAttachmentStorage(tmp_path),
    ).import_message_attachments(
        source_directory_path="root/source",
        provider="discord",
        communication_target_id="discord-target",
        target_address="guild/expected-parent",
        source_message_id="message-1",
        source_thread_id="child-thread",
        policy=TeamChatAttachmentPolicy.root_defaults(),
    )

    assert result.status == "failed"
    assert result.failures[0].message == (
        "discord source message is not present in the ingress store"
    )

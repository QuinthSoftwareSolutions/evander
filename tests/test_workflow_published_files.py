from __future__ import annotations

import io
import tarfile
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest

from evander.communication_artifact_links import verify_communication_artifact_download_token
from evander.communication_artifacts import CommunicationArtifactStore
from evander.config import GatewayConfig
from evander.directories import DirectoryService
from evander.effects.providers.base import EffectRuntimeContext
from evander.effects.providers.communication_send_message import (
    CommunicationSendMessageEffectProvider,
)
from evander.events.provider_models import CommunicationSendMessageEffectDefinition
from evander.messaging.notifications import CommunicationMessageSender
from evander.messaging.targets import (
    SqliteCommunicationTargetsRepository,
    communication_targets_db_path,
)
from evander.published_artifacts import PublishedArtifactStore
from evander.runtime import build_runtime


class RecordingTeams:
    def __init__(self):
        self.calls = []

    def post_file(self, message, channel_target, session_id, artifacts, thread_id=None):
        self.calls.append(
            {
                "message": message,
                "target": channel_target,
                "files": artifacts,
                "session_id": session_id,
                "thread_id": thread_id,
            }
        )
        return {"status": "posted", "message_id": "1792000000000", "channel_target": channel_target}

    def post_status(self, message, channel_target, session_id, thread_id=None):
        self.calls.append(
            {
                "message": message,
                "target": channel_target,
                "files": [],
                "session_id": session_id,
                "thread_id": thread_id,
            }
        )
        return {"status": "posted", "message_id": "1792000000001", "channel_target": channel_target}


@pytest.fixture
def delivery(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKFLOW_TEST_FILE_SECRET", "synthetic-signing-secret")
    runtime = build_runtime(
        GatewayConfig(
            runtime_dir=tmp_path / "runtime",
            communication_artifact_public_base_url="https://control.example.test",
            communication_artifact_link_secret_env="WORKFLOW_TEST_FILE_SECRET",
        )
    )
    directories = DirectoryService(runtime.directories)
    directories.create(parent="root", name="reports")
    directories.create(parent="root", name="other")
    targets = SqliteCommunicationTargetsRepository(
        communication_targets_db_path(tmp_path / "runtime")
    )
    targets.write_overlay(
        "root",
        {
            "targets": [
                {
                    "id": "results",
                    "provider": "teams",
                    "kind": "teams",
                    "address": "team-a/channel-a",
                    "policy": {"capabilities": ["write", "reply"]},
                }
            ]
        },
    )
    teams = RecordingTeams()
    monkeypatch.setattr(CommunicationMessageSender, "_teams_service", lambda self, address: teams)
    return SimpleNamespace(project=tmp_path, runtime=runtime, teams=teams, targets=targets)


def archive_bytes(files=None, *, link=False, duplicate=False):
    files = files or {"results/report.md": b"# Passed\n"}
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for name, content in files.items():
            member = tarfile.TarInfo(name)
            if link:
                member.type = tarfile.SYMTYPE
                member.linkname = "/outside/report.md"
                archive.addfile(member)
            else:
                member.size = len(content)
                archive.addfile(member, io.BytesIO(content))
                if duplicate:
                    archive.addfile(member, io.BytesIO(b"different".ljust(len(content))))
    return output.getvalue()


def publish(delivery, *, owner="root/reports", content=None, metadata=True):
    store = PublishedArtifactStore(delivery.runtime.config.runtime_dir)
    return store.publish_bytes(
        key="smoke/build-7/findings",
        session_id="expired-test-session",
        content=content or archive_bytes(),
        metadata={"directory_path": owner} if metadata else {},
    )


def send(delivery, specifications, *, directory="root/reports", thread_id=None):
    provider = CommunicationSendMessageEffectProvider()
    provider.initialize(
        CommunicationSendMessageEffectDefinition(
            id="send-results",
            type="effect:communication:send-message",
            display_name="Send results",
            communication_target_id="results",
            message="Smoke test [7](https://build.example.test/7)",
            published_artifacts="${{report.files}}",
            thread_id=thread_id,
        ),
        EffectRuntimeContext(
            admin_service=SimpleNamespace(project_dir=delivery.project, runtime=delivery.runtime),
            metadata={"directory_path": directory},
        ),
    )
    return provider.execute(resolved_config={"published_artifacts": specifications})


def specifications(record):
    return [
        {"key": record.key, "path": "results/report.md", "filename": "report.md"},
        {"key": record.key, "filename": "evidence.tar.gz"},
    ]


def assert_nothing_sent_or_staged(delivery):
    assert delivery.teams.calls == []
    assert not list(
        (delivery.runtime.config.runtime_dir / "communication-artifacts").glob("comm_*.json")
    )


def test_workflow_sends_report_and_complete_archive_without_live_agent(delivery):
    files = {"results/report.md": b"# Passed\n", "results/logs/readiness.json": b'{"ready":true}'}
    files.update({f"results/screenshots/step-{i}.png": f"image {i}".encode() for i in range(80)})
    content = archive_bytes(files)
    record = publish(delivery, content=content)
    assert delivery.runtime.session_repository.get("expired-test-session") is None

    result = send(delivery, specifications(record), thread_id="original-thread")

    assert result["success"] is True
    assert result["status"] == "posted"
    assert result["message_id"] == "1792000000000"
    assert result["artifact_count"] == 2
    assert len(delivery.teams.calls) == 1
    call = delivery.teams.calls[0]
    assert call["target"] == "team-a/channel-a"
    assert call["session_id"] == "workflow"
    assert call["thread_id"] == "original-thread"
    assert call["message"] == "Smoke test [7](https://build.example.test/7)"
    assert [item["filename"] for item in call["files"]] == ["report.md", "evidence.tar.gz"]
    store = CommunicationArtifactStore(delivery.runtime.config.runtime_dir)
    report, evidence = [store.get(item) for item in result["artifact_ids"]]
    assert report.blob_path.read_bytes() == b"# Passed\n"
    assert evidence.blob_path.read_bytes() == content
    with tarfile.open(fileobj=io.BytesIO(evidence.blob_path.read_bytes()), mode="r:gz") as archive:
        assert (
            len(
                [
                    member
                    for member in archive.getmembers()
                    if member.name.startswith("results/screenshots/")
                ]
            )
            == 80
        )
        assert archive.extractfile("results/screenshots/step-79.png").read() == b"image 79"
    for item in call["files"]:
        url = urlparse(item["download_url"])
        assert url.netloc == "control.example.test"
        assert (
            verify_communication_artifact_download_token(
                artifact_id=item["artifact_id"],
                token=parse_qs(url.query)["token"][0],
                secret_env="WORKFLOW_TEST_FILE_SECRET",
            )
            == item["artifact_id"]
        )


@pytest.mark.parametrize("owner", ["root/other", "root/reports-extra", "root"])
def test_workflow_cannot_send_another_directorys_publication(delivery, owner):
    record = publish(delivery, owner=owner)
    with pytest.raises(PermissionError, match="outside the workflow directory"):
        send(delivery, specifications(record))
    assert_nothing_sent_or_staged(delivery)


def test_legacy_publication_requires_verifiable_session_ownership(delivery):
    record = publish(delivery, metadata=False)
    with pytest.raises(PermissionError, match="no verifiable directory owner"):
        send(delivery, specifications(record))
    assert_nothing_sent_or_staged(delivery)


def test_checksum_failure_cannot_be_skipped_as_optional(delivery):
    record = publish(delivery)
    store = PublishedArtifactStore(delivery.runtime.config.runtime_dir)
    store.get(record.key).blob_path.write_bytes(b"corrupted archive")
    with pytest.raises(ValueError, match="checksum mismatch"):
        send(delivery, [{"key": record.key, "optional": True}])
    assert_nothing_sent_or_staged(delivery)


@pytest.mark.parametrize("kwargs", [{"link": True}, {"duplicate": True}])
def test_selected_member_must_be_a_unique_regular_file(delivery, kwargs):
    record = publish(delivery, content=archive_bytes(**kwargs))
    with pytest.raises(ValueError, match="one regular file"):
        send(delivery, specifications(record))
    assert_nothing_sent_or_staged(delivery)


@pytest.mark.parametrize(
    "path",
    ["../report.md", "/report.md", "results/../report.md", "results\\report.md", "./report.md"],
)
def test_workflow_rejects_unsafe_selected_paths_before_delivery(delivery, path):
    record = publish(delivery)
    with pytest.raises(ValueError, match="canonical relative file path"):
        send(delivery, [{"key": record.key, "path": path}])
    assert_nothing_sent_or_staged(delivery)


@pytest.mark.parametrize(
    "config",
    [
        {"communication_file_max_bytes": 1},
        {"communication_file_allowed_extensions": [".md"]},
        {"communication_file_blocked_extensions": [".gz"]},
        {"communication_file_allowed_mime_types": ["text/markdown"]},
    ],
)
def test_rejected_second_file_leaves_no_staged_files_or_message(delivery, config, monkeypatch):
    resolved = GatewayConfig(runtime_dir=delivery.runtime.config.runtime_dir, **config)
    monkeypatch.setattr(CommunicationMessageSender, "_resolved_config", lambda self: resolved)
    record = publish(delivery)
    with pytest.raises(ValueError, match="Published attachment"):
        send(delivery, specifications(record))
    assert_nothing_sent_or_staged(delivery)


def test_missing_optional_files_fall_back_to_the_text_message(delivery):
    result = send(delivery, [{"key": "missing", "optional": True}])
    assert result["success"] is True
    assert result["artifact_count"] == 0
    assert delivery.teams.calls[0]["files"] == []


def test_missing_required_member_aborts_before_any_staging(delivery):
    record = publish(delivery)
    with pytest.raises(ValueError, match="one regular file"):
        send(delivery, [{"key": record.key}, {"key": record.key, "path": "missing.md"}])
    assert_nothing_sent_or_staged(delivery)


def test_target_write_capability_is_required_even_for_published_files(delivery):
    delivery.targets.write_overlay(
        "root",
        {
            "targets": [
                {
                    "id": "results",
                    "provider": "teams",
                    "kind": "teams",
                    "address": "team-a/channel-a",
                    "policy": {"capabilities": ["read"]},
                }
            ]
        },
    )
    record = publish(delivery)
    with pytest.raises(ValueError, match="does not allow write capability"):
        send(delivery, specifications(record))
    assert_nothing_sent_or_staged(delivery)


@pytest.mark.parametrize("invalid", [False, 0, {}, "unresolved-array", [{"key": "a"}] * 17])
def test_invalid_resolved_file_lists_cannot_degrade_to_a_text_success(delivery, invalid):
    with pytest.raises(ValueError, match="published_artifacts must resolve to an array"):
        send(delivery, invalid)
    assert_nothing_sent_or_staged(delivery)


def test_archive_expansion_is_bounded_before_parsing_tar_members(delivery, monkeypatch):
    monkeypatch.setattr("evander.messaging.published_message_artifacts.MAX_ARCHIVE_BYTES", 8192)
    record = publish(delivery, content=archive_bytes({"results/report.md": b"x" * 20000}))
    with pytest.raises(ValueError, match="Expanded evidence archive"):
        send(delivery, specifications(record))
    assert_nothing_sent_or_staged(delivery)


def test_teams_transport_errors_propagate_without_claiming_delivery(delivery, monkeypatch):
    record = publish(delivery)

    def fail(*args, **kwargs):
        raise RuntimeError("Teams unavailable")

    monkeypatch.setattr(delivery.teams, "post_file", fail)
    with pytest.raises(RuntimeError, match="Teams unavailable"):
        send(delivery, specifications(record))


def test_published_files_reject_unsupported_providers_before_staging(delivery):
    delivery.targets.write_overlay(
        "root",
        {
            "targets": [
                {
                    "id": "results",
                    "provider": "discord",
                    "kind": "discord",
                    "address": "123456789012345678",
                    "policy": {"capabilities": ["write"]},
                }
            ]
        },
    )
    record = publish(delivery)
    with pytest.raises(ValueError, match="require a Teams target"):
        send(delivery, specifications(record))
    assert_nothing_sent_or_staged(delivery)


def test_image_files_are_only_embedded_when_explicitly_selected(delivery):
    record = publish(delivery, content=archive_bytes({"results/screenshot.png": b"png-content"}))
    result = send(delivery, [{"key": record.key, "path": "results/screenshot.png"}])
    assert result["artifact_count"] == 1
    files = delivery.teams.calls[0]["files"]
    assert files[0]["mime_type"] == "image/png"
    assert "download_url" not in files[0]


def test_missing_link_signing_configuration_leaves_no_staged_files(delivery, monkeypatch):
    monkeypatch.delenv("WORKFLOW_TEST_FILE_SECRET")
    record = publish(delivery)
    with pytest.raises(ValueError, match="is not set"):
        send(delivery, specifications(record))
    assert_nothing_sent_or_staged(delivery)


def test_optional_missing_member_is_skipped_while_required_report_is_sent(delivery):
    record = publish(delivery)
    result = send(
        delivery,
        [
            {"key": record.key, "path": "missing.png", "optional": True},
            {"key": record.key, "path": "results/report.md"},
        ],
    )
    assert result["artifact_count"] == 1
    assert [f["filename"] for f in delivery.teams.calls[0]["files"]] == ["report.md"]


def test_workflow_file_schema_defaults_to_no_files_and_exposes_receipt_fields():
    from evander.workflow.contracts import effective_node_output_schema

    definition = CommunicationSendMessageEffectDefinition(
        id="text",
        type="effect:communication:send-message",
        display_name="Text",
        communication_target_id="results",
        message="done",
    )
    assert definition.published_artifacts == []
    outputs = effective_node_output_schema(definition)
    assert outputs["artifact_ids"] == {"type": "array", "items": {"type": "string"}}
    assert outputs["artifact_count"] == {"type": "integer"}


def test_parent_workflow_can_deliver_a_descendant_publication(delivery):
    record = publish(delivery)
    result = send(delivery, specifications(record), directory="root")
    assert result["artifact_count"] == 2


def test_optional_does_not_bypass_directory_ownership(delivery):
    record = publish(delivery, owner="root/other")
    with pytest.raises(PermissionError, match="outside the workflow directory"):
        send(delivery, [{"key": record.key, "optional": True}])
    assert_nothing_sent_or_staged(delivery)


def test_legacy_publication_can_use_expired_session_ownership(delivery):
    from datetime import UTC, datetime, timedelta
    from evander.models import SessionManifest

    delivery.runtime.session_repository.upsert_session(
        SessionManifest(
            session_id="expired-test-session",
            allowed_tools=[],
            directory_path="root/reports",
            expires_at=datetime.now(UTC) - timedelta(hours=1),
        )
    )
    record = publish(delivery, metadata=False)
    result = send(delivery, specifications(record))
    assert result["artifact_count"] == 2
    assert result["message_id"] == "1792000000000"

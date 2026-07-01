from __future__ import annotations

from datetime import timedelta

import pytest

from evander.communication_artifacts import CommunicationArtifactStore, utc_now
from evander.models import CommunicationFileArtifact
from evander.worker.models import WorkerFileWriteRequest, WorkerSessionPrepareRequest
from evander.worker.service import WorkerDaemon


def test_store_stages_metadata_with_safe_filename_and_opaque_id(tmp_path):
    store = CommunicationArtifactStore(tmp_path)

    record = store.stage(
        provider="Email",
        session_id="session-1",
        content=b"hello",
        original_filename="../../Reports/Q4 Plan.pdf",
        mime_type=None,
        source_message_id="message-1",
        source_attachment_id="attachment-1",
        source_mailbox="inbox@example.test",
    )

    assert record.artifact_id.startswith("comm_")
    assert record.provider == "email"
    assert record.session_id == "session-1"
    assert record.source_message_id == "message-1"
    assert record.source_mailbox == "inbox@example.test"
    assert record.original_filename == "../../Reports/Q4 Plan.pdf"
    assert record.normalized_safe_filename == "Q4-Plan.pdf"
    assert record.filename == "Q4-Plan.pdf"
    assert record.byte_count == 5
    assert record.sha256 == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    assert store.blob_path(record.artifact_id).read_bytes() == b"hello"

    artifact = CommunicationFileArtifact.model_validate(record.model_dump(mode="json"))
    assert artifact.normalized_safe_filename == "Q4-Plan.pdf"


def test_store_rejects_non_opaque_artifact_ids(tmp_path):
    store = CommunicationArtifactStore(tmp_path)

    with pytest.raises(ValueError, match="opaque staged artifact ID"):
        store.get("../escape")


def test_store_cleanup_removes_expired_records(tmp_path):
    store = CommunicationArtifactStore(tmp_path)
    now = utc_now()
    record = store.stage(
        provider="discord",
        session_id="session-1",
        content=b"expired",
        original_filename="file.txt",
        created_at=now - timedelta(days=2),
        expires_at=now - timedelta(days=1),
    )

    removed = store.cleanup_expired(now=now)

    assert removed == 1
    assert not store.metadata_path(record.artifact_id).exists()
    assert not store.blob_path(record.artifact_id).exists()


def test_worker_file_write_supports_binary_content_base64(tmp_path):
    service = WorkerDaemon(runtime_dir=tmp_path / "runtime", worker_root=tmp_path)
    service.prepare_session(WorkerSessionPrepareRequest(session_id="session-1"))

    record = service.write_files(
        "session-1",
        WorkerFileWriteRequest.model_validate(
            {
                "files": [
                    {
                        "destination_path": "/home/agent/communication-artifacts/blob.bin",
                        "content_base64": "AAEC/w==",
                    }
                ]
            }
        ),
    )

    assert record.copied_paths == ["/home/agent/communication-artifacts/blob.bin"]
    assert (
        tmp_path / "sessions" / "session-1" / "agent_home" / "communication-artifacts" / "blob.bin"
    ).read_bytes() == b"\x00\x01\x02\xff"

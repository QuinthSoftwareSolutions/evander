from __future__ import annotations

from datetime import timedelta

import pytest

from evander.communication_artifacts import CommunicationArtifactStore, utc_now
from evander.models import CommunicationFileArtifact
from evander.worker.models import WorkerFileWriteRequest, WorkerSessionPrepareRequest
from evander.worker.service import WorkerDaemon


def test_only_signed_verified_progress_png_downloads_are_inline(tmp_path, monkeypatch):
    import asyncio
    from io import BytesIO
    from PIL import Image
    from types import SimpleNamespace
    from starlette.requests import Request
    from evander.admin.web.artifacts import artifact_routes
    from evander.communication_artifact_links import issue_communication_artifact_download_link

    monkeypatch.setenv("SYNTHETIC_IMAGE_SECRET", "local-test-secret")
    config = SimpleNamespace(
        runtime_dir=tmp_path, communication_artifact_link_secret_env="SYNTHETIC_IMAGE_SECRET"
    )
    service = SimpleNamespace(
        config=config,
        runtime=SimpleNamespace(
            provider_config=SimpleNamespace(resolve_for_session_id=lambda sid: config)
        ),
    )
    route = next(
        r
        for r in artifact_routes(service, None)
        if r.path == "/public/communication-artifacts/{artifact_id:str}"
    )
    store = CommunicationArtifactStore(tmp_path)
    buffer = BytesIO()
    Image.new("RGB", (2, 2), "blue").save(buffer, format="PNG")
    for mime, content, purpose, expected in (
        ("image/png", buffer.getvalue(), "teams_progress_image", "inline"),
        ("image/png", buffer.getvalue(), None, "attachment"),
        ("image/svg+xml", b"<svg/>", "teams_progress_image", "attachment"),
        ("image/png", b"not a PNG", "teams_progress_image", "attachment"),
    ):
        record = store.stage(
            provider="teams",
            session_id="session-a",
            content=content,
            original_filename="graphic",
            mime_type=mime,
            metadata={"purpose": purpose},
        )
        link = issue_communication_artifact_download_link(
            artifact_id=record.artifact_id,
            artifact_expires_at=record.expires_at,
            public_base_url="https://media.example",
            secret_env="SYNTHETIC_IMAGE_SECRET",
            ttl_seconds=600,
        )

        def request(token):
            return Request(
                {
                    "type": "http",
                    "method": "GET",
                    "path": "/",
                    "headers": [],
                    "path_params": {"artifact_id": record.artifact_id},
                    "query_string": ("token=" + token).encode(),
                }
            )

        response = asyncio.run(route.endpoint(request(link.token)))
        assert response.headers["content-disposition"].startswith(expected)
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["cache-control"] == "no-store"
        assert asyncio.run(route.endpoint(request("invalid"))).status_code == 403


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

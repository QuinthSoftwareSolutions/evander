from __future__ import annotations

import base64
import json
import tarfile
from pathlib import Path

import pytest

from agent_sandbox.effects.providers.agent_exec import AgentExecEffectProvider
from agent_sandbox.effects.providers.artifacts import (
    ArtifactPublishArtifactsEffectProvider,
    ArtifactStageEffectProvider,
)
from agent_sandbox.effects.providers.base import EffectRuntimeContext
from agent_sandbox.events.provider_models import (
    ArtifactPublishArtifactsEffectDefinition,
    ArtifactStageEffectDefinition,
)
from agent_sandbox.published_artifacts import PublishedArtifactStore

_BEGIN = "__AGENT_SANDBOX_ARTIFACT_EFFECT_JSON_BEGIN__"
_END = "__AGENT_SANDBOX_ARTIFACT_EFFECT_JSON_END__"


def _context(tmp_path: Path) -> EffectRuntimeContext:
    return EffectRuntimeContext(
        provider_config={
            "runtime_dir": str(tmp_path),
            "execution_backend": "container_worker_vm",
        },
        metadata={"directory_path": "root"},
    )


def _stdout_payload(payload: dict[str, object]) -> str:
    return f"{_BEGIN}\n{json.dumps(payload)}\n{_END}\n"


def test_artifact_stage_effect_returns_staged_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_execute(self: AgentExecEffectProvider, *, resolved_config=None):
        del self, resolved_config
        return {
            "success": True,
            "session_id": "session-1",
            "execution_backend": "container_worker_vm",
            "stdout": _stdout_payload(
                {
                    "success": True,
                    "source_path": "/home/agent/repos/project/report.html",
                    "artifact_path": "reports/report.html",
                    "artifacts_root": "/artifacts",
                    "copied_count": 1,
                    "byte_count": 42,
                }
            ),
        }

    monkeypatch.setattr(AgentExecEffectProvider, "execute", fake_execute)
    provider = ArtifactStageEffectProvider()
    provider.initialize(
        ArtifactStageEffectDefinition(
            id="artifact-stage-1",
            display_name="Stage",
            type="effect:artifact:stage",
            session_id="session-1",
            path="repos/project/report.html",
            destination_path="reports/report.html",
        ),
        _context(tmp_path),
    )

    result = provider.execute()

    assert result["success"] is True
    assert result["session_id"] == "session-1"
    assert result["artifact_path"] == "reports/report.html"
    assert result["copied_count"] == 1
    assert result["byte_count"] == 42


def test_artifact_publish_effect_stores_tar_gz_by_immutable_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "payload.tar.gz"
    source = tmp_path / "source.txt"
    source.write_text("hello\n", encoding="utf-8")
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(source, arcname="source.txt")
    archive_content = archive.read_bytes()
    calls = 0

    def fake_execute(self: AgentExecEffectProvider, *, resolved_config=None):
        nonlocal calls
        del self, resolved_config
        calls += 1
        return {
            "success": True,
            "session_id": "session-1",
            "execution_backend": "container_worker_vm",
            "stdout": _stdout_payload(
                {
                    "success": True,
                    "archive_format": "tar.gz",
                    "artifacts_root": "/artifacts",
                    "source_file_count": 1,
                    "source_byte_count": len("hello\n"),
                    "content_base64": base64.b64encode(archive_content).decode("ascii"),
                }
            ),
        }

    monkeypatch.setattr(AgentExecEffectProvider, "execute", fake_execute)
    provider = ArtifactPublishArtifactsEffectProvider()
    definition = ArtifactPublishArtifactsEffectDefinition(
        id="artifact-publish-1",
        display_name="Publish",
        type="effect:artifact:publish-artifacts",
        session_id="session-1",
        key="runs/session-1/report",
    )
    provider.initialize(definition, _context(tmp_path))

    result = provider.execute()

    assert result["success"] is True
    assert result["key"] == "runs/session-1/report"
    assert result["source_file_count"] == 1
    assert result["download_url"] == "/api/admin/artifact-downloads/runs%2Fsession-1%2Freport"
    stored = PublishedArtifactStore(tmp_path).get("runs/session-1/report")
    assert stored.blob_path.read_bytes() == archive_content
    assert stored.metadata.source_byte_count == len("hello\n")

    with pytest.raises(ValueError, match="already exists"):
        provider.execute()
    assert calls == 1

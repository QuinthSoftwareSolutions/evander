from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from agent_sandbox.effects.providers.agent_exec import AgentExecEffectProvider
from agent_sandbox.effects.providers.agent_copy_files import AgentCopyFilesEffectProvider
from agent_sandbox.effects.providers.artifacts import (
    ArtifactImportPublishedEffectProvider,
    ArtifactPublishArtifactsEffectProvider,
    ArtifactStageEffectProvider,
    _IMPORT_SCRIPT,
    _PUBLISH_SCRIPT,
)
from agent_sandbox.effects.providers.base import EffectRuntimeContext
from agent_sandbox.events.provider_models import (
    ArtifactImportPublishedEffectDefinition,
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


def _script_payload(stdout: str) -> dict[str, object]:
    start = stdout.find(_BEGIN)
    end = stdout.find(_END)
    assert start >= 0
    assert end > start
    return json.loads(stdout[start + len(_BEGIN) : end].strip())


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


def test_artifact_publish_effect_accepts_source_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "payload.tar.gz"
    source = tmp_path / "report.txt"
    source.write_text("report\n", encoding="utf-8")
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(source, arcname="reports/report.txt")
    archive_content = archive.read_bytes()
    commands: list[str] = []

    def fake_execute(self: AgentExecEffectProvider, *, resolved_config=None):
        del resolved_config
        effect = self.effect_definition
        commands.append(str(effect.command))  # type: ignore[attr-defined]
        return {
            "success": True,
            "session_id": "session-1",
            "execution_backend": "container_worker_vm",
            "stdout": _stdout_payload(
                {
                    "success": True,
                    "archive_format": "tar.gz",
                    "artifacts_root": "/artifacts",
                    "source_paths": ["reports/report.txt"],
                    "source_file_count": 1,
                    "source_byte_count": len("report\n"),
                    "content_base64": base64.b64encode(archive_content).decode("ascii"),
                }
            ),
        }

    monkeypatch.setattr(AgentExecEffectProvider, "execute", fake_execute)
    provider = ArtifactPublishArtifactsEffectProvider()
    provider.initialize(
        ArtifactPublishArtifactsEffectDefinition(
            id="artifact-publish-1",
            display_name="Publish",
            type="effect:artifact:publish-artifacts",
            session_id="session-1",
            key="runs/session-1/report",
            source_paths=["reports/report.txt"],
        ),
        _context(tmp_path),
    )

    result = provider.execute()

    assert result["source_paths"] == ["reports/report.txt"]
    assert "\"reports/report.txt\"" in commands[0]
    stored = PublishedArtifactStore(tmp_path).get("runs/session-1/report")
    assert stored.metadata.metadata["source_paths"] == ["reports/report.txt"]


def test_artifact_publish_script_publishes_only_selected_paths(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    (artifacts / "reports").mkdir(parents=True)
    (artifacts / "reports" / "report.txt").write_text("report\n", encoding="utf-8")
    (artifacts / "logs").mkdir()
    (artifacts / "logs" / "debug.txt").write_text("debug\n", encoding="utf-8")
    (artifacts / "team-action.json").write_text("{}", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "-", json.dumps(["reports/report.txt"])],
        input=_PUBLISH_SCRIPT,
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "AGENT_SANDBOX_ARTIFACTS_DIR": str(artifacts)},
    )

    assert result.returncode == 0, result.stderr
    payload = _script_payload(result.stdout)
    assert payload["source_paths"] == ["reports/report.txt"]
    archive_content = base64.b64decode(str(payload["content_base64"]))
    archive_path = tmp_path / "selected.tar.gz"
    archive_path.write_bytes(archive_content)
    with tarfile.open(archive_path, "r:gz") as archive:
        names = set(archive.getnames())
    assert names == {"reports/report.txt"}


def test_artifact_publish_script_rejects_unsafe_source_paths(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    (artifacts / "report.txt").write_text("report\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, "-", json.dumps(["../report.txt"])],
        input=_PUBLISH_SCRIPT,
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "AGENT_SANDBOX_ARTIFACTS_DIR": str(artifacts)},
    )

    assert result.returncode == 2
    assert "source_paths entries must be relative" in result.stderr


def test_artifact_import_published_copies_archive_and_extracts_into_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = PublishedArtifactStore(tmp_path)
    record = store.publish_single_file(
        key="manuals/reviewed/run-1",
        session_id="manual-upload",
        filename="reviewed.docx",
        artifact_path="input/reviewed.docx",
        content=b"docx",
    )
    copy_calls: list[dict[str, object]] = []
    exec_commands: list[str] = []

    def fake_copy(self: AgentCopyFilesEffectProvider, *, resolved_config=None):
        del resolved_config
        effect = self.effect_definition
        files = [entry.model_dump(mode="json") for entry in effect.files]  # type: ignore[attr-defined]
        copy_calls.append({"session_id": effect.session_id, "files": files})  # type: ignore[attr-defined]
        return {
            "success": True,
            "session_id": "session-1",
            "execution_backend": "container_worker_vm",
            "copied_count": 1,
            "copied_paths": [files[0]["destination_path"]],
        }

    def fake_exec(self: AgentExecEffectProvider, *, resolved_config=None):
        del resolved_config
        effect = self.effect_definition
        exec_commands.append(str(effect.command))  # type: ignore[attr-defined]
        return {
            "success": True,
            "session_id": "session-1",
            "execution_backend": "container_worker_vm",
            "stdout": _stdout_payload(
                {
                    "success": True,
                    "artifacts_root": "/artifacts",
                    "destination_path": "input",
                    "imported_file_count": 1,
                    "imported_paths": ["input/input/reviewed.docx"],
                    "source_byte_count": 4,
                    "overwrite": True,
                }
            ),
        }

    monkeypatch.setattr(AgentCopyFilesEffectProvider, "execute", fake_copy)
    monkeypatch.setattr(AgentExecEffectProvider, "execute", fake_exec)
    provider = ArtifactImportPublishedEffectProvider()
    provider.initialize(
        ArtifactImportPublishedEffectDefinition(
            id="artifact-import-1",
            display_name="Import",
            type="effect:artifact:import-published",
            session_id="session-1",
            key="manuals/reviewed/run-1",
            destination_path="input",
            overwrite=True,
        ),
        _context(tmp_path),
    )

    result = provider.execute()

    assert result["success"] is True
    assert result["key"] == "manuals/reviewed/run-1"
    assert result["sha256"] == record.sha256
    assert result["imported_file_count"] == 1
    assert copy_calls[0]["session_id"] == "session-1"
    copied_file = copy_calls[0]["files"][0]  # type: ignore[index]
    assert copied_file["source_path"] == str(store.get("manuals/reviewed/run-1").blob_path)
    assert copied_file["destination_path"].endswith(f"{record.storage_id}.tar.gz")
    assert exec_commands[0].count("effect:artifact:import-published") == 1
    assert " input 1 " in exec_commands[0]


def test_artifact_import_script_extracts_regular_files_and_rejects_collisions(
    tmp_path: Path,
) -> None:
    archive_path = tmp_path / "archive.tar.gz"
    source = tmp_path / "reviewed.docx"
    source.write_bytes(b"docx")
    with tarfile.open(archive_path, "w:gz") as archive:
        archive.add(source, arcname="input/reviewed.docx")
    artifacts_root = tmp_path / "artifacts"
    artifacts_root.mkdir()

    first = subprocess.run(
        [sys.executable, "-", str(archive_path), "", "0"],
        input=_IMPORT_SCRIPT,
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "AGENT_SANDBOX_ARTIFACTS_DIR": str(artifacts_root)},
    )
    second = subprocess.run(
        [sys.executable, "-", str(archive_path), "", "0"],
        input=_IMPORT_SCRIPT,
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "AGENT_SANDBOX_ARTIFACTS_DIR": str(artifacts_root)},
    )

    assert first.returncode == 0
    assert (artifacts_root / "input/reviewed.docx").read_bytes() == b"docx"
    assert "input/reviewed.docx" in first.stdout
    assert second.returncode == 2
    assert "import destination already exists" in second.stderr

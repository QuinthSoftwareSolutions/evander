from __future__ import annotations

import base64
import json
import shlex
from collections.abc import Mapping
from typing import Any

from agent_sandbox.events.provider_models import (
    AgentCopyFilesEffectDefinition,
    AgentExecEffectDefinition,
    ArtifactImportPublishedEffectDefinition,
    ArtifactPublishArtifactsEffectDefinition,
    ArtifactStageEffectDefinition,
)
from agent_sandbox.published_artifacts import PublishedArtifactStore, validate_artifact_key

from .agent_copy_files import AgentCopyFilesEffectProvider
from .agent_exec import AgentExecEffectProvider
from .agent_session import (
    effective_config_for_directory,
    normalize_optional_string,
    resolve_session_id_from_vm_name,
    runtime_session_repository,
)
from .base import EffectProviderBase, EffectRuntimeContext

_PAYLOAD_BEGIN = "__AGENT_SANDBOX_ARTIFACT_EFFECT_JSON_BEGIN__"
_PAYLOAD_END = "__AGENT_SANDBOX_ARTIFACT_EFFECT_JSON_END__"

_STAGE_SCRIPT = r"""
import json
import os
import pathlib
import shutil
import stat
import sys

BEGIN = "__AGENT_SANDBOX_ARTIFACT_EFFECT_JSON_BEGIN__"
END = "__AGENT_SANDBOX_ARTIFACT_EFFECT_JSON_END__"

def normalize_destination(value, source_name):
    text = str(value or "").strip().replace("\\", "/")
    if not text:
        text = source_name
    path = pathlib.PurePosixPath(text)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("destination_path must be relative and stay inside /artifacts")
    return path.as_posix()

def resolve_source(value):
    text = str(value or "").strip()
    if not text:
        raise ValueError("path is required")
    if "\x00" in text or "\\" in text:
        raise ValueError("path must use POSIX separators and must not contain NUL")
    home = pathlib.Path("/home/agent").resolve(strict=True)
    candidate = pathlib.Path(text)
    if not candidate.is_absolute():
        candidate = home / candidate
    resolved = candidate.resolve(strict=True)
    resolved.relative_to(home)
    if candidate.is_symlink():
        raise ValueError("path must not be a symlink")
    return home, resolved

def copy_file(source, destination):
    if source.is_symlink():
        raise ValueError(f"refusing to stage symlink: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    return source.stat().st_size

def copy_directory(source, destination):
    copied_count = 0
    byte_count = 0
    for child in sorted(source.rglob("*")):
        if child.is_symlink():
            raise ValueError(f"refusing to stage symlink: {child}")
        relative = child.relative_to(source)
        target = destination / relative
        if child.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        if not child.is_file():
            raise ValueError(f"refusing to stage non-regular path: {child}")
        byte_count += copy_file(child, target)
        copied_count += 1
    destination.mkdir(parents=True, exist_ok=True)
    return copied_count, byte_count

try:
    source_arg = sys.argv[1] if len(sys.argv) > 1 else ""
    destination_arg = sys.argv[2] if len(sys.argv) > 2 else ""
    _home, source = resolve_source(source_arg)
    artifacts_root = pathlib.Path(os.environ.get("AGENT_SANDBOX_ARTIFACTS_DIR") or "/artifacts")
    artifacts_root = artifacts_root.resolve(strict=True)
    destination_relative = normalize_destination(destination_arg, source.name or "artifact")
    destination = (artifacts_root / destination_relative).resolve()
    destination.relative_to(artifacts_root)
    if source.is_file():
        byte_count = copy_file(source, destination)
        copied_count = 1
    elif source.is_dir():
        copied_count, byte_count = copy_directory(source, destination)
    else:
        raise ValueError("path must resolve to a regular file or directory")
    payload = {
        "success": True,
        "source_path": str(source),
        "artifact_path": destination_relative,
        "artifacts_root": str(artifacts_root),
        "copied_count": copied_count,
        "byte_count": byte_count,
    }
    print(BEGIN)
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    print(END)
except Exception as exc:
    print(f"effect:artifact:stage: {exc}", file=sys.stderr)
    raise SystemExit(2)
""".strip()

_PUBLISH_SCRIPT = r"""
import base64
import io
import json
import os
import pathlib
import stat
import tarfile
import sys

BEGIN = "__AGENT_SANDBOX_ARTIFACT_EFFECT_JSON_BEGIN__"
END = "__AGENT_SANDBOX_ARTIFACT_EFFECT_JSON_END__"

try:
    artifacts_root = pathlib.Path(os.environ.get("AGENT_SANDBOX_ARTIFACTS_DIR") or "/artifacts")
    artifacts_root = artifacts_root.resolve(strict=True)
    dirs = []
    files = []
    source_byte_count = 0
    for child in sorted(artifacts_root.rglob("*")):
        if child.is_symlink():
            raise ValueError(f"refusing to publish symlink: {child}")
        try:
            child.relative_to(artifacts_root)
        except ValueError:
            raise ValueError(f"artifact path escaped artifact root: {child}")
        if child.is_dir():
            dirs.append(child)
            continue
        if child.is_file():
            file_stat = child.stat()
            if not stat.S_ISREG(file_stat.st_mode):
                raise ValueError(f"refusing to publish non-regular file: {child}")
            source_byte_count += file_stat.st_size
            files.append(child)
            continue
        raise ValueError(f"refusing to publish unsupported path: {child}")
    if not files:
        raise ValueError("artifacts directory does not contain any regular files")
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w:gz", format=tarfile.PAX_FORMAT) as tar:
        for directory in dirs:
            tar.add(directory, arcname=directory.relative_to(artifacts_root).as_posix(), recursive=False)
        for file_path in files:
            tar.add(file_path, arcname=file_path.relative_to(artifacts_root).as_posix(), recursive=False)
    content = archive.getvalue()
    payload = {
        "success": True,
        "archive_format": "tar.gz",
        "artifacts_root": str(artifacts_root),
        "source_file_count": len(files),
        "source_byte_count": source_byte_count,
        "content_base64": base64.b64encode(content).decode("ascii"),
    }
    print(BEGIN)
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    print(END)
except Exception as exc:
    print(f"effect:artifact:publish-artifacts: {exc}", file=sys.stderr)
    raise SystemExit(2)
""".strip()

_IMPORT_SCRIPT = r"""
import json
import os
import pathlib
import sys
import tarfile

BEGIN = "__AGENT_SANDBOX_ARTIFACT_EFFECT_JSON_BEGIN__"
END = "__AGENT_SANDBOX_ARTIFACT_EFFECT_JSON_END__"

def normalize_destination(value):
    text = str(value or "").strip().replace("\\", "/")
    if not text:
        return ""
    path = pathlib.PurePosixPath(text)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("destination_path must be relative and stay inside /artifacts")
    return path.as_posix()

def normalize_member(name):
    text = str(name or "").strip().replace("\\", "/")
    if not text or "\x00" in text:
        raise ValueError("archive member path is empty or invalid")
    path = pathlib.PurePosixPath(text)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"archive member path is unsafe: {name}")
    return path.as_posix()

def resolve_destination(root, prefix, member_name):
    member = normalize_member(member_name)
    relative = pathlib.PurePosixPath(prefix) / member if prefix else pathlib.PurePosixPath(member)
    destination = (root / pathlib.Path(relative.as_posix())).resolve()
    destination.relative_to(root)
    return relative.as_posix(), destination

try:
    archive_arg = sys.argv[1] if len(sys.argv) > 1 else ""
    destination_arg = sys.argv[2] if len(sys.argv) > 2 else ""
    overwrite = (sys.argv[3] if len(sys.argv) > 3 else "").strip().lower() in {"1", "true", "yes"}
    archive_path = pathlib.Path(archive_arg)
    if not archive_path.is_file():
        raise ValueError("published artifact archive is not available in the session")
    artifacts_root = pathlib.Path(os.environ.get("AGENT_SANDBOX_ARTIFACTS_DIR") or "/artifacts")
    artifacts_root = artifacts_root.resolve(strict=True)
    prefix = normalize_destination(destination_arg)

    planned = []
    with tarfile.open(archive_path, "r:gz") as tar:
        for member in tar.getmembers():
            relative, destination = resolve_destination(artifacts_root, prefix, member.name)
            if member.isdir():
                planned.append((member, relative, destination))
                continue
            if not member.isfile():
                raise ValueError(f"refusing to import non-regular archive member: {member.name}")
            planned.append((member, relative, destination))

        collisions = [
            relative
            for member, relative, destination in planned
            if member.isfile() and destination.exists() and not overwrite
        ]
        if collisions:
            raise FileExistsError("import destination already exists: " + ", ".join(collisions))

        imported_paths = []
        imported_count = 0
        imported_bytes = 0
        for member, relative, destination in planned:
            if member.isdir():
                destination.mkdir(parents=True, exist_ok=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True)
            source = tar.extractfile(member)
            if source is None:
                raise ValueError(f"archive member could not be read: {member.name}")
            with source, destination.open("wb") as handle:
                content = source.read()
                handle.write(content)
            imported_paths.append(relative)
            imported_count += 1
            imported_bytes += len(content)

    payload = {
        "success": True,
        "artifacts_root": str(artifacts_root),
        "destination_path": prefix or None,
        "imported_file_count": imported_count,
        "imported_paths": imported_paths,
        "source_byte_count": imported_bytes,
        "overwrite": overwrite,
    }
    print(BEGIN)
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    print(END)
except Exception as exc:
    print(f"effect:artifact:import-published: {exc}", file=sys.stderr)
    raise SystemExit(2)
""".strip()


class ArtifactStageEffectProvider(EffectProviderBase):
    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        effect = self._effect()
        payload = {
            "target": effect.target,
            "session_id": effect.session_id,
            "vm_name": effect.vm_name,
            "path": effect.path,
            "destination_path": effect.destination_path,
            "vm_user": effect.vm_user,
        }
        if resolved_config:
            payload.update(dict(resolved_config))
        path = _required_text(payload.get("path"), "effect:artifact:stage path is required")
        destination_path = normalize_optional_string(payload.get("destination_path")) or ""
        exec_result = _run_agent_exec(
            self._context(),
            payload,
            effect_id=effect.id,
            display_name="Stage artifact",
            command=_python_stdin_command(_STAGE_SCRIPT, path, destination_path),
            timeout_seconds=300,
        )
        script_payload = _parse_script_payload(
            str(exec_result.get("stdout") or ""), effect_type="effect:artifact:stage"
        )
        return {
            "success": True,
            "action": "artifact_stage",
            "message": (
                f"Staged {script_payload.get('copied_count', 0)} artifact file(s) "
                f"to {script_payload.get('artifact_path')}"
            ),
            "session_id": exec_result.get("session_id"),
            "vm_name": exec_result.get("vm_name"),
            "execution_backend": exec_result.get("execution_backend") or "libvirt_vm",
            **script_payload,
        }

    def _effect(self) -> ArtifactStageEffectDefinition:
        if not isinstance(self.effect_definition, ArtifactStageEffectDefinition):
            raise RuntimeError(
                "ArtifactStageEffectProvider initialized with a non-artifact-stage definition"
            )
        return self.effect_definition

    def _context(self) -> EffectRuntimeContext:
        if self.runtime_context is None:
            raise RuntimeError("ArtifactStageEffectProvider has no runtime context")
        return self.runtime_context


class ArtifactPublishArtifactsEffectProvider(EffectProviderBase):
    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        effect = self._effect()
        context = self._context()
        payload = {
            "target": effect.target,
            "session_id": effect.session_id,
            "vm_name": effect.vm_name,
            "key": effect.key,
            "vm_user": effect.vm_user,
        }
        if resolved_config:
            payload.update(dict(resolved_config))

        key = validate_artifact_key(_required_text(payload.get("key"), "artifact key is required"))
        store = PublishedArtifactStore(context.config_path("runtime_dir"))
        if store.exists(key):
            raise ValueError(f"Published artifact key already exists: {key}")

        exec_result = _run_agent_exec(
            context,
            payload,
            effect_id=effect.id,
            display_name="Publish artifacts",
            command=_python_stdin_command(_PUBLISH_SCRIPT),
            timeout_seconds=1800,
        )
        script_payload = _parse_script_payload(
            str(exec_result.get("stdout") or ""),
            effect_type="effect:artifact:publish-artifacts",
        )
        content_base64 = str(script_payload.get("content_base64") or "")
        if not content_base64:
            raise RuntimeError("artifact publish script did not return archive content")
        try:
            archive_content = base64.b64decode(content_base64, validate=True)
        except Exception as exc:
            raise RuntimeError("artifact publish script returned invalid archive content") from exc

        session_id = _required_text(exec_result.get("session_id"), "artifact publish did not resolve session_id")
        record = store.publish_bytes(
            key=key,
            session_id=session_id,
            content=archive_content,
            source_artifacts_root=normalize_optional_string(script_payload.get("artifacts_root")),
            source_file_count=int(script_payload.get("source_file_count") or 0),
            source_byte_count=int(script_payload.get("source_byte_count") or 0),
            vm_name=normalize_optional_string(exec_result.get("vm_name")),
            execution_backend=normalize_optional_string(exec_result.get("execution_backend"))
            or "libvirt_vm",
            metadata={
                "effect_type": "effect:artifact:publish-artifacts",
                "effect_id": effect.id,
            },
        )
        return {
            "success": True,
            "action": "artifact_publish",
            "message": f"Published artifacts as {record.key}",
            "key": record.key,
            "session_id": record.session_id,
            "vm_name": record.vm_name,
            "execution_backend": record.execution_backend,
            "published_at": record.published_at.isoformat(),
            "archive_format": record.archive_format,
            "filename": record.filename,
            "mime_type": record.mime_type,
            "byte_count": record.byte_count,
            "sha256": record.sha256,
            "source_file_count": record.source_file_count,
            "source_byte_count": record.source_byte_count,
            "download_url": f"/api/admin/artifact-downloads/{_url_quote_key(record.key)}",
        }

    def _effect(self) -> ArtifactPublishArtifactsEffectDefinition:
        if not isinstance(self.effect_definition, ArtifactPublishArtifactsEffectDefinition):
            raise RuntimeError(
                "ArtifactPublishArtifactsEffectProvider initialized with a non-artifact-publish definition"
            )
        return self.effect_definition

    def _context(self) -> EffectRuntimeContext:
        if self.runtime_context is None:
            raise RuntimeError("ArtifactPublishArtifactsEffectProvider has no runtime context")
        return self.runtime_context


class ArtifactImportPublishedEffectProvider(EffectProviderBase):
    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        effect = self._effect()
        context = self._context()
        payload = {
            "target": effect.target,
            "session_id": effect.session_id,
            "vm_name": effect.vm_name,
            "key": effect.key,
            "destination_path": effect.destination_path,
            "overwrite": effect.overwrite,
            "vm_user": effect.vm_user,
        }
        if resolved_config:
            payload.update(dict(resolved_config))

        key = validate_artifact_key(_required_text(payload.get("key"), "artifact key is required"))
        destination_path = normalize_optional_string(payload.get("destination_path")) or ""
        overwrite = _optional_bool(payload.get("overwrite"), default=False)
        artifact = PublishedArtifactStore(context.config_path("runtime_dir")).get(key)
        staged_archive = (
            f"/home/agent/.agent-sandbox/artifact-imports/{artifact.metadata.storage_id}.tar.gz"
        )
        copy_result = _copy_file_to_agent(
            context,
            payload,
            effect_id=effect.id,
            source_path=str(artifact.blob_path),
            destination_path=staged_archive,
        )
        exec_result = _run_agent_exec(
            context,
            payload,
            effect_id=effect.id,
            display_name="Import published artifact",
            command=_python_stdin_command(
                _IMPORT_SCRIPT,
                staged_archive,
                destination_path,
                "1" if overwrite else "0",
            ),
            timeout_seconds=1800,
        )
        script_payload = _parse_script_payload(
            str(exec_result.get("stdout") or ""),
            effect_type="effect:artifact:import-published",
        )
        return {
            "success": True,
            "action": "artifact_import_published",
            "message": (
                f"Imported {script_payload.get('imported_file_count', 0)} file(s) "
                f"from {key}"
            ),
            "key": artifact.metadata.key,
            "session_id": exec_result.get("session_id") or copy_result.get("session_id"),
            "vm_name": exec_result.get("vm_name") or copy_result.get("vm_name"),
            "execution_backend": exec_result.get("execution_backend")
            or copy_result.get("execution_backend")
            or artifact.metadata.execution_backend,
            "archive_format": artifact.metadata.archive_format,
            "filename": artifact.metadata.filename,
            "mime_type": artifact.metadata.mime_type,
            "byte_count": artifact.metadata.byte_count,
            "sha256": artifact.metadata.sha256,
            **script_payload,
        }

    def _effect(self) -> ArtifactImportPublishedEffectDefinition:
        if not isinstance(self.effect_definition, ArtifactImportPublishedEffectDefinition):
            raise RuntimeError(
                "ArtifactImportPublishedEffectProvider initialized with a non-artifact-import definition"
            )
        return self.effect_definition

    def _context(self) -> EffectRuntimeContext:
        if self.runtime_context is None:
            raise RuntimeError("ArtifactImportPublishedEffectProvider has no runtime context")
        return self.runtime_context


def _copy_file_to_agent(
    context: EffectRuntimeContext,
    payload: Mapping[str, Any],
    *,
    effect_id: str,
    source_path: str,
    destination_path: str,
) -> dict[str, Any]:
    exec_payload = _agent_exec_target_payload(context, payload)
    effect = AgentCopyFilesEffectDefinition(
        id=f"{effect_id}-agent-copy-files",
        display_name="Copy published artifact archive",
        type="effect:agent:copy-files",
        target=exec_payload.get("target"),
        session_id=exec_payload.get("session_id"),
        vm_name=exec_payload.get("vm_name"),
        vm_user=normalize_optional_string(payload.get("vm_user")),
        files=[
            {
                "source_path": source_path,
                "destination_path": destination_path,
                "overwrite": True,
            }
        ],
    )
    provider = AgentCopyFilesEffectProvider()
    provider.initialize(effect_definition=effect, runtime_context=context)
    result = provider.execute()
    if not isinstance(result, Mapping):
        raise RuntimeError("artifact import copy did not return a mapping")
    return dict(result)


def _run_agent_exec(
    context: EffectRuntimeContext,
    payload: Mapping[str, Any],
    *,
    effect_id: str,
    display_name: str,
    command: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    exec_payload = _agent_exec_target_payload(context, payload)
    effect = AgentExecEffectDefinition(
        id=f"{effect_id}-agent-exec",
        display_name=display_name,
        type="effect:agent:exec",
        target=exec_payload.get("target"),
        session_id=exec_payload.get("session_id"),
        vm_name=exec_payload.get("vm_name"),
        vm_user=normalize_optional_string(payload.get("vm_user")),
        command=command,
        working_directory="/home/agent",
        timeout_seconds=timeout_seconds,
        check=True,
    )
    provider = AgentExecEffectProvider()
    provider.initialize(effect_definition=effect, runtime_context=context)
    result = provider.execute()
    if not isinstance(result, Mapping):
        raise RuntimeError("artifact agent execution did not return a mapping")
    return dict(result)


def _agent_exec_target_payload(
    context: EffectRuntimeContext,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    normalized = {
        "target": normalize_optional_string(payload.get("target")),
        "session_id": normalize_optional_string(payload.get("session_id")),
        "vm_name": normalize_optional_string(payload.get("vm_name")),
    }
    if normalized["session_id"] is None and normalized["vm_name"] is not None:
        try:
            normalized["session_id"] = resolve_session_id_from_vm_name(
                context, normalized["vm_name"] or ""
            )
        except Exception:
            pass
    if normalized["session_id"] is None and normalized["target"]:
        directory_path = str(context.metadata.get("directory_path") or "root")
        repository = runtime_session_repository(
            context,
            effective_config_for_directory(context, directory_path),
        )
        session = repository.resolve_session_target(normalized["target"] or "")
        if session is not None:
            normalized["session_id"] = session.session_id
    if not any(normalized.values()):
        raise ValueError("artifact effects require target, session_id, or vm_name")
    return normalized


def _python_stdin_command(script: str, *args: str) -> str:
    quoted_args = " ".join(shlex.quote(str(arg)) for arg in args)
    if quoted_args:
        return f"python3 - {quoted_args} <<'PY'\n{script}\nPY"
    return f"python3 - <<'PY'\n{script}\nPY"


def _parse_script_payload(stdout: str, *, effect_type: str) -> dict[str, Any]:
    text = str(stdout or "")
    start = text.find(_PAYLOAD_BEGIN)
    end = text.find(_PAYLOAD_END)
    if start >= 0 and end > start:
        json_text = text[start + len(_PAYLOAD_BEGIN) : end].strip()
    else:
        json_text = text.strip()
    try:
        payload = json.loads(json_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{effect_type} script did not return valid JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{effect_type} script returned an invalid payload")
    return payload


def _required_text(value: Any, message: str) -> str:
    text = normalize_optional_string(value)
    if text is None:
        raise ValueError(message)
    return text


def _optional_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"boolean value expected: {value!r}")


def _url_quote_key(key: str) -> str:
    from urllib.parse import quote

    return quote(key, safe="")

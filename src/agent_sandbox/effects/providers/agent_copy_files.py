from __future__ import annotations

import base64
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agent_sandbox.events.provider_models import AgentCopyFilesEffectDefinition

from .agent_session import (
    container_backend,
    effective_config_for_session,
    normalize_optional_string,
    require_container_session_id,
    resolve_vm_target,
    selected_execution_backend_for_target,
)
from .vm_copy_files import VmCopyFilesEffectProvider


class AgentCopyFilesEffectProvider(VmCopyFilesEffectProvider):
    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        effect = self._effect()
        context = self._context()
        payload = {
            "target": effect.target,
            "session_id": effect.session_id,
            "vm_name": effect.vm_name,
            "vm_user": effect.vm_user,
            "files": [
                entry.model_dump(mode="json", exclude_none=True) for entry in effect.files
            ],
        }
        if resolved_config:
            payload.update(dict(resolved_config))

        directory_path = str(context.metadata.get("directory_path") or "root")
        execution_backend, config = selected_execution_backend_for_target(
            context, payload, directory_path=directory_path
        )
        if execution_backend == "libvirt_vm":
            return self._execute_libvirt(payload, execution_backend=execution_backend)
        if execution_backend != "container_worker_vm":
            raise ValueError(f"Unsupported execution_backend: {execution_backend}")

        session_id, session = require_container_session_id(
            context,
            payload,
            effect_type="effect:agent:copy-files",
            config=config,
        )
        config = effective_config_for_session(
            context,
            session_id,
            fallback_directory_path=getattr(session, "directory_path", directory_path),
        )
        files = self._normalize_file_entries(payload.get("files"))
        if not files:
            raise ValueError("effect:agent:copy-files requires at least one file entry")
        worker_files = [self._worker_file_entry(entry) for entry in files]
        result = container_backend(context, config).write_files(
            session_id=session_id,
            files=worker_files,
        )
        return {
            "success": True,
            "message": f"Copied {result.copied_count} file(s) into container worker session {session_id}",
            "session_id": session_id,
            "execution_backend": "container_worker_vm",
            "vm_name": None,
            "copied_count": result.copied_count,
            "copied_paths": result.copied_paths,
        }

    def _execute_libvirt(self, payload: dict[str, Any], *, execution_backend: str) -> dict[str, Any]:
        context = self._context()
        vm_copy_files = getattr(context.vm_file_copier, "vm_copy_files", None)
        if not callable(vm_copy_files):
            raise RuntimeError("effect:agent:copy-files vm_file_copier is not available")
        vm_name, session_id = resolve_vm_target(
            context, payload, effect_type="effect:agent:copy-files"
        )
        files = self._normalize_file_entries(payload.get("files"))
        result = vm_copy_files(
            target=vm_name,
            files=files,
            vm_user=normalize_optional_string(payload.get("vm_user")) or "agent",
        )
        return {
            "success": bool(result.get("success", False)),
            "message": str(result.get("message") or ""),
            "session_id": session_id,
            "execution_backend": execution_backend,
            "vm_name": str(result.get("vm_name") or vm_name),
            "copied_count": int(result.get("copied_count") or 0),
            "copied_paths": list(result.get("copied_paths") or []),
        }

    def _worker_file_entry(self, entry: dict[str, Any]) -> dict[str, Any]:
        content = entry.get("content")
        payload: dict[str, Any] = {
            "destination_path": entry["destination_path"],
            "mode": entry.get("mode"),
            "overwrite": bool(entry.get("overwrite", True)),
        }
        if content is None:
            source_path = normalize_optional_string(entry.get("source_path"))
            if not source_path:
                raise ValueError("effect:agent:copy-files entries require source_path or content")
            local_source = Path(source_path).expanduser().resolve()
            if not local_source.exists():
                raise FileNotFoundError(f"Source path does not exist: {local_source}")
            if local_source.is_dir():
                raise ValueError(
                    "effect:agent:copy-files only supports file sources for container sessions"
                )
            payload["content_base64"] = base64.b64encode(local_source.read_bytes()).decode(
                "ascii"
            )
            return payload
        if isinstance(content, (bytes, bytearray)):
            payload["content_base64"] = base64.b64encode(bytes(content)).decode("ascii")
        else:
            payload["content"] = str(content)
        return payload

    def _effect(self) -> AgentCopyFilesEffectDefinition:
        if not isinstance(self.effect_definition, AgentCopyFilesEffectDefinition):
            raise RuntimeError(
                "AgentCopyFilesEffectProvider initialized with a non-agent-copy-files effect definition"
            )
        return self.effect_definition

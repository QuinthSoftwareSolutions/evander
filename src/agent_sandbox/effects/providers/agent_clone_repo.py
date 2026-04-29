from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent_sandbox.events.provider_models import AgentCloneRepoEffectDefinition

from .agent_session import (
    container_backend,
    effective_config_for_session,
    normalize_optional_string,
    require_container_session_id,
    required_string,
    resolve_vm_target,
    selected_execution_backend_for_target,
)
from .base import EffectProviderBase


class AgentCloneRepoEffectProvider(EffectProviderBase):
    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        effect = self._effect()
        context = self._context()
        payload = {
            "target": effect.target,
            "session_id": effect.session_id,
            "vm_name": effect.vm_name,
            "repo_source": effect.repo_source,
            "clone_branch": effect.clone_branch,
            "vm_user": effect.vm_user,
            "directory_path": effect.directory_path,
        }
        if resolved_config:
            payload.update(dict(resolved_config))

        directory_path = normalize_optional_string(payload.get("directory_path")) or str(
            context.metadata.get("directory_path") or "root"
        )
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
            effect_type="effect:agent:clone-repo",
            config=config,
        )
        config = effective_config_for_session(
            context,
            session_id,
            fallback_directory_path=getattr(session, "directory_path", directory_path),
        )
        repo_source = required_string(
            payload.get("repo_source"), "effect:agent:clone-repo requires repo_source"
        )
        clone_branch = normalize_optional_string(payload.get("clone_branch"))
        backend = container_backend(context, config)
        backend.prepare_session(
            session_id=session_id,
            metadata={
                "execution_backend": "container_worker_vm",
                "directory_path": session.directory_path if session is not None else directory_path,
            },
        )
        result = backend.clone_repo(
            session_id=session_id,
            repo_source=repo_source,
            clone_branch=clone_branch,
        )
        return {
            "success": True,
            "message": (
                f"Cloned repo into container worker session {session_id}"
                if result.cloned
                else f"Workspace already prepared for container worker session {session_id}"
            ),
            "session_id": session_id,
            "target_dir": result.target_dir,
            "guest_repo_name": result.guest_repo_name,
            "remote_url": result.remote_url,
            "execution_backend": "container_worker_vm",
            "vm_name": None,
        }

    def _execute_libvirt(self, payload: dict[str, Any], *, execution_backend: str) -> dict[str, Any]:
        context = self._context()
        repo_cloner = getattr(context.vm_repo_cloner, "clone_repo_into_vm", None)
        if not callable(repo_cloner):
            raise RuntimeError("effect:agent:clone-repo vm_repo_cloner is not available")
        vm_name, session_id = resolve_vm_target(
            context, payload, effect_type="effect:agent:clone-repo"
        )
        result = repo_cloner(
            vm_name=vm_name,
            session_id=session_id,
            repo_source=required_string(
                payload.get("repo_source"), "effect:agent:clone-repo requires repo_source"
            ),
            clone_branch=normalize_optional_string(payload.get("clone_branch")),
            vm_user=normalize_optional_string(payload.get("vm_user")) or "agent",
            directory_path=normalize_optional_string(payload.get("directory_path"))
            or str(context.metadata.get("directory_path") or "root"),
        )
        return {
            "success": bool(result.success),
            "message": normalize_optional_string(getattr(result, "message", None)) or "",
            "session_id": session_id,
            "target_dir": normalize_optional_string(getattr(result, "target_dir", None)),
            "guest_repo_name": normalize_optional_string(getattr(result, "guest_repo_name", None)),
            "remote_url": normalize_optional_string(getattr(result, "remote_url", None)),
            "execution_backend": execution_backend,
            "vm_name": result.vm_name or vm_name,
        }

    def _effect(self) -> AgentCloneRepoEffectDefinition:
        if not isinstance(self.effect_definition, AgentCloneRepoEffectDefinition):
            raise RuntimeError(
                "AgentCloneRepoEffectProvider initialized with a non-agent-clone-repo effect definition"
            )
        return self.effect_definition

    def _context(self):
        if self.runtime_context is None:
            raise RuntimeError("AgentCloneRepoEffectProvider has no runtime context")
        return self.runtime_context

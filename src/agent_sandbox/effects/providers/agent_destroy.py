from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent_sandbox.events.provider_models import AgentDestroyEffectDefinition

from .agent_session import (
    effective_config_for_directory,
    effective_config_for_session,
    execution_backend_for_session,
    normalize_optional_string,
    resolve_vm_target,
    runtime_session_repository,
)
from .base import EffectProviderBase, EffectRuntimeContext


class AgentDestroyEffectProvider(EffectProviderBase):
    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        effect = self._effect()
        context = self._context()
        payload = {
            "target": effect.target,
            "session_id": effect.session_id,
            "vm_name": effect.vm_name,
            "execution_backend": effect.execution_backend,
            "directory_path": effect.directory_path,
        }
        if resolved_config:
            payload.update(dict(resolved_config))

        directory_path = normalize_optional_string(payload.get("directory_path")) or str(
            context.metadata.get("directory_path") or "root"
        )
        execution_backend, _config = self._execution_backend_for_payload(
            context,
            payload,
            directory_path=directory_path,
        )
        if execution_backend == "libvirt_vm":
            return self._execute_libvirt(context, payload, execution_backend=execution_backend)
        if execution_backend == "container_worker_vm":
            return self._execute_admin_delete(
                context,
                payload,
                execution_backend=execution_backend,
            )
        raise ValueError(f"Unsupported execution_backend: {execution_backend}")

    def _execute_libvirt(
        self,
        context: EffectRuntimeContext,
        payload: Mapping[str, Any],
        *,
        execution_backend: str,
    ) -> dict[str, Any]:
        admin_service = self._admin_service(context)
        if hasattr(admin_service, "delete_admin_agent"):
            return self._execute_admin_delete(
                context,
                payload,
                execution_backend=execution_backend,
            )
        if not hasattr(admin_service, "destroy_vm"):
            raise RuntimeError("effect:agent:destroy admin_service.destroy_vm is not available")
        vm_name, session_id = resolve_vm_target(
            context,
            payload,
            effect_type="effect:agent:destroy",
        )
        result = admin_service.destroy_vm(vm_name)
        success = bool(getattr(result, "success", False))
        resolved_vm_name = normalize_optional_string(getattr(result, "vm_name", None)) or vm_name
        return {
            "success": success,
            "action": "destroy",
            "target": self._target_from_payload(payload),
            "session_id": session_id,
            "execution_backend": execution_backend,
            "vm_name": resolved_vm_name,
            "target_name": resolved_vm_name,
            "status": "destroyed" if success else "failed",
            "message": normalize_optional_string(getattr(result, "message", None)) or "",
        }

    def _execute_admin_delete(
        self,
        context: EffectRuntimeContext,
        payload: Mapping[str, Any],
        *,
        execution_backend: str,
    ) -> dict[str, Any]:
        admin_service = self._admin_service(context)
        delete_admin_agent = getattr(admin_service, "delete_admin_agent", None)
        if not callable(delete_admin_agent):
            raise RuntimeError(
                "effect:agent:destroy admin_service.delete_admin_agent is not available"
            )
        target = self._target_from_payload(payload)
        result = delete_admin_agent(target, backend=execution_backend)
        if not isinstance(result, Mapping):
            raise RuntimeError(
                "effect:agent:destroy expected delete_admin_agent to return a mapping"
            )
        success = bool(result.get("success"))
        target_name = normalize_optional_string(result.get("target_name")) or target
        session_id = normalize_optional_string(result.get("session_id")) or (
            target if normalize_optional_string(payload.get("session_id")) else None
        )
        vm_name = target_name if execution_backend == "libvirt_vm" else None
        return {
            "success": success,
            "action": "destroy",
            "target": target,
            "session_id": session_id,
            "execution_backend": execution_backend,
            "vm_name": vm_name,
            "target_name": target_name,
            "status": normalize_optional_string(result.get("status"))
            or ("destroyed" if success else "failed"),
            "message": normalize_optional_string(result.get("message")) or "",
        }

    def _execution_backend_for_payload(
        self,
        context: EffectRuntimeContext,
        payload: Mapping[str, Any],
        *,
        directory_path: str,
    ) -> tuple[str, Any]:
        base_config = effective_config_for_directory(context, directory_path)
        explicit_backend = normalize_optional_string(payload.get("execution_backend"))
        if explicit_backend:
            return explicit_backend, base_config

        repository = runtime_session_repository(context, base_config)
        session = None
        session_id = normalize_optional_string(payload.get("session_id"))
        target = normalize_optional_string(payload.get("target"))
        vm_name = normalize_optional_string(payload.get("vm_name"))
        if session_id:
            session = repository.get_session(session_id)
        elif target:
            session = repository.get_session(target)

        vm_target = vm_name or (target if session is None else None)
        if session is None and vm_target:
            matches = repository.list_sessions_for_vm(vm_target)
            if len(matches) == 1:
                session = matches[0]
            elif len(matches) > 1:
                session_ids = ", ".join(sorted(match.session_id for match in matches))
                raise ValueError(
                    f"VM {vm_target!r} is associated with multiple sessions: {session_ids}"
                )

        if session is not None:
            session_config = effective_config_for_session(
                context,
                session.session_id,
                fallback_directory_path=session.directory_path,
            )
            return (
                execution_backend_for_session(session, fallback_config=session_config),
                session_config,
            )
        return str(base_config.execution_backend), base_config

    def _target_from_payload(self, payload: Mapping[str, Any]) -> str:
        for field_name in ("target", "session_id", "vm_name"):
            value = normalize_optional_string(payload.get(field_name))
            if value:
                return value
        raise ValueError("effect:agent:destroy requires target, session_id, or vm_name")

    def _effect(self) -> AgentDestroyEffectDefinition:
        if not isinstance(self.effect_definition, AgentDestroyEffectDefinition):
            raise RuntimeError(
                "AgentDestroyEffectProvider initialized with a non-agent-destroy effect definition"
            )
        return self.effect_definition

    def _context(self) -> EffectRuntimeContext:
        if self.runtime_context is None:
            raise RuntimeError("AgentDestroyEffectProvider has no runtime context")
        return self.runtime_context

    def _admin_service(self, context: EffectRuntimeContext) -> Any:
        if context.admin_service is None:
            raise RuntimeError("effect:agent:destroy admin_service is not available")
        return context.admin_service

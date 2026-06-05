from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from agent_sandbox.execution import ExecutionBackendKind, ExecutionSpec, ExecutionStatusKind
from agent_sandbox.events.provider_models import AgentBootstrapFrontendEffectDefinition

from .agent_session import (
    as_string_list,
    container_backend,
    container_worker_image_for_session,
    effective_config_for_session,
    frontend_bootstrap_command,
    map_install_roots_to_container_repo,
    normalize_optional_string,
    require_container_session_id,
    required_string,
    resolve_vm_target,
    safe_effect_execution_id,
    selected_execution_backend_for_target,
    wait_for_container_execution,
)
from .base import EffectProviderBase


class AgentBootstrapFrontendEffectProvider(EffectProviderBase):
    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        effect = self._effect()
        context = self._context()
        payload = {
            "target": effect.target,
            "session_id": effect.session_id,
            "vm_name": effect.vm_name,
            "host_checkout": effect.host_checkout,
            "install_roots": effect.install_roots,
            "vm_user": effect.vm_user,
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
            effect_type="effect:agent:bootstrap-frontend",
            config=config,
        )
        config = effective_config_for_session(
            context,
            session_id,
            fallback_directory_path=getattr(session, "directory_path", directory_path),
        )
        install_roots = map_install_roots_to_container_repo(
            host_checkout=required_string(
                payload.get("host_checkout"),
                "effect:agent:bootstrap-frontend requires host_checkout",
            ),
            install_roots=as_string_list(payload.get("install_roots")) or [],
        )
        if not install_roots:
            return {
                "success": True,
                "message": "No container frontend install roots mapped under /home/agent/repos",
                "session_id": session_id,
                "execution_backend": "container_worker_vm",
                "vm_name": None,
            }

        backend = container_backend(context, config)
        command = frontend_bootstrap_command(install_roots)
        handle = backend.start(
            backend.prepare(
                ExecutionSpec(
                    backend=ExecutionBackendKind.CONTAINER_WORKER_VM,
                    target_name=safe_effect_execution_id("frontend", session_id),
                    session_id=session_id,
                    directory_path=session.directory_path if session is not None else "root",
                    image=container_worker_image_for_session(session, config),
                    command=command,
                    metadata={
                        "effect_type": "effect:agent:bootstrap-frontend",
                        "install_roots": install_roots,
                    },
                )
            )
        )
        status = wait_for_container_execution(
            backend,
            handle.execution_id,
            timeout_seconds=float(config.container_worker_job_timeout_seconds),
        )
        try:
            backend.destroy(handle.execution_id)
        except Exception:
            pass
        success = status.status == ExecutionStatusKind.STOPPED
        return {
            "success": bool(success),
            "message": status.message or f"frontend bootstrap status={status.status.value}",
            "session_id": session_id,
            "execution_backend": "container_worker_vm",
            "vm_name": None,
        }

    def _execute_libvirt(self, payload: dict[str, Any], *, execution_backend: str) -> dict[str, Any]:
        context = self._context()
        bootstrapper = getattr(context.vm_frontend_bootstrapper, "bootstrap_frontend_in_vm", None)
        if not callable(bootstrapper):
            raise RuntimeError(
                "effect:agent:bootstrap-frontend vm_frontend_bootstrapper is not available"
            )
        vm_name, session_id = resolve_vm_target(
            context, payload, effect_type="effect:agent:bootstrap-frontend"
        )
        result = bootstrapper(
            vm_name=vm_name,
            session_id=session_id,
            host_checkout=required_string(
                payload.get("host_checkout"),
                "effect:agent:bootstrap-frontend requires host_checkout",
            ),
            install_roots=as_string_list(payload.get("install_roots")),
            vm_user=normalize_optional_string(payload.get("vm_user")) or "agent",
        )
        return {
            "success": bool(result.success),
            "message": normalize_optional_string(getattr(result, "message", None)) or "",
            "session_id": session_id,
            "execution_backend": execution_backend,
            "vm_name": result.vm_name or vm_name,
        }

    def _effect(self) -> AgentBootstrapFrontendEffectDefinition:
        if not isinstance(self.effect_definition, AgentBootstrapFrontendEffectDefinition):
            raise RuntimeError(
                "AgentBootstrapFrontendEffectProvider initialized with a non-agent-bootstrap-frontend effect definition"
            )
        return self.effect_definition

    def _context(self):
        if self.runtime_context is None:
            raise RuntimeError("AgentBootstrapFrontendEffectProvider has no runtime context")
        return self.runtime_context

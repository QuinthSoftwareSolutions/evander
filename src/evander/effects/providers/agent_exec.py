from __future__ import annotations

import shlex
from collections.abc import Mapping, Sequence
from typing import Any

from evander.execution import ExecutionBackendKind, ExecutionSpec, ExecutionStatusKind
from evander.events.provider_models import AgentExecEffectDefinition

from .agent_session import (
    container_backend,
    container_worker_image_for_session,
    container_shell_preamble,
    effective_config_for_session,
    normalize_optional_string,
    require_container_session_id,
    safe_effect_execution_id,
    selected_execution_backend_for_target,
    wait_for_container_execution,
)
from .vm_exec import VmExecEffectProvider


class AgentExecEffectProvider(VmExecEffectProvider):
    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        effect = self._effect()
        context = self._context()
        payload = {
            "target": effect.target,
            "session_id": effect.session_id,
            "vm_name": effect.vm_name,
            "vm_user": effect.vm_user,
            "command": effect.command,
            "working_directory": effect.working_directory,
            "environment": effect.environment,
            "stdin": effect.stdin,
            "timeout_seconds": effect.timeout_seconds,
            "check": effect.check,
        }
        if resolved_config:
            payload.update(dict(resolved_config))

        directory_path = str(context.metadata.get("directory_path") or "root")
        execution_backend, config = selected_execution_backend_for_target(
            context,
            payload,
            directory_path=directory_path,
        )
        if execution_backend == ExecutionBackendKind.LIBVIRT_VM.value:
            result = super().execute(resolved_config=payload)
            result.setdefault("execution_backend", ExecutionBackendKind.LIBVIRT_VM.value)
            return result
        if execution_backend != ExecutionBackendKind.CONTAINER_WORKER_VM.value:
            raise ValueError(f"Unsupported execution_backend: {execution_backend}")

        session_id, session = require_container_session_id(
            context,
            payload,
            effect_type="effect:agent:exec",
            config=config,
        )
        config = effective_config_for_session(
            context,
            session_id,
            fallback_directory_path=getattr(session, "directory_path", directory_path),
        )
        environment = self._normalize_agent_environment(payload.get("environment"))
        if environment:
            raise ValueError(
                "effect:agent:exec with container_worker_vm does not support environment"
            )
        command = self._build_container_command(
            command=self._normalize_agent_command(payload.get("command")),
            working_directory=normalize_optional_string(payload.get("working_directory")),
            stdin=normalize_optional_string(payload.get("stdin")),
        )
        timeout_seconds = self._as_float_or_none(payload.get("timeout_seconds")) or float(
            config.container_worker_job_timeout_seconds
        )
        check = bool(payload.get("check", True))
        execution_metadata = self._execution_metadata(payload.get("execution_metadata"))

        backend = container_backend(context, config)
        handle = backend.start(
            backend.prepare(
                ExecutionSpec(
                    backend=ExecutionBackendKind.CONTAINER_WORKER_VM,
                    target_name=safe_effect_execution_id("exec", session_id),
                    session_id=session_id,
                    directory_path=getattr(session, "directory_path", directory_path),
                    image=container_worker_image_for_session(session, config),
                    command=command,
                    metadata={
                        "effect_type": "effect:agent:exec",
                        "effect_id": effect.id,
                        "effect_display_name": effect.display_name,
                        **execution_metadata,
                    },
                )
            )
        )
        logs = ""
        status = None
        try:
            status = wait_for_container_execution(
                backend,
                handle.execution_id,
                timeout_seconds=timeout_seconds,
            )
            logs = self._log_snapshot(backend, handle.execution_id)
        finally:
            try:
                backend.destroy(handle.execution_id)
            except Exception:
                pass

        if status is None:
            raise RuntimeError("effect:agent:exec did not receive a container execution status")
        success = status.status == ExecutionStatusKind.STOPPED
        stdout, stderr = self._stdout_stderr_from_status(status=status, logs=logs, success=success)
        exit_code = self._exit_code_from_status(status=status, success=success)
        status_metadata = getattr(status, "metadata", {})
        failure_kind = None
        if not success:
            failure_kind = (
                "command"
                if isinstance(status_metadata, Mapping)
                and status_metadata.get("worker_terminal_kind") == "command_exit"
                and status_metadata.get("exit_code") is not None
                else "infrastructure"
            )
        message = (
            normalize_optional_string(getattr(status, "message", None))
            or normalize_optional_string(stderr)
            or normalize_optional_string(stdout)
            or f"container worker job status={status.status.value}"
        )
        result = {
            "success": success,
            "session_id": session_id,
            "execution_backend": ExecutionBackendKind.CONTAINER_WORKER_VM.value,
            "execution_id": handle.execution_id,
            "status": status.status.value,
            "action": "agent_exec",
            "message": message,
            "exit_code": exit_code,
            "failure_kind": failure_kind,
            "stdout": stdout,
            "stderr": stderr,
            "vm_name": None,
        }
        if check and not success:
            raise RuntimeError(
                normalize_optional_string(stderr)
                or normalize_optional_string(stdout)
                or message
                or "Container worker command failed"
            )
        return result

    def _effect(self) -> AgentExecEffectDefinition:
        if not isinstance(self.effect_definition, AgentExecEffectDefinition):
            raise RuntimeError(
                "AgentExecEffectProvider initialized with a non-agent-exec effect definition"
            )
        return self.effect_definition

    @classmethod
    def _normalize_agent_command(cls, value: Any) -> str:
        if isinstance(value, str):
            command = value.strip()
            if not command:
                raise ValueError("effect:agent:exec requires a non-empty command")
            return command
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            pieces = [normalize_optional_string(item) for item in value]
            normalized = [piece for piece in pieces if piece]
            if not normalized:
                raise ValueError("effect:agent:exec requires a non-empty command")
            return shlex.join(normalized)
        raise ValueError("effect:agent:exec command must be a string or sequence of strings")

    @staticmethod
    def _normalize_agent_environment(value: Any) -> dict[str, str]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise ValueError("effect:agent:exec environment must be a mapping")
        environment: dict[str, str] = {}
        for key, raw_value in value.items():
            normalized_key = str(key).strip()
            if not normalized_key:
                continue
            environment[normalized_key] = "" if raw_value is None else str(raw_value)
        return environment

    @staticmethod
    def _execution_metadata(value: Any) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            return {}
        allowed_keys = {
            "operation_kind",
            "operation_label",
            "artifact_key",
            "artifact_destination_path",
            "artifact_source_path",
        }
        return {key: raw_value for key in allowed_keys if (raw_value := value.get(key)) is not None}

    @staticmethod
    def _build_container_command(
        *,
        command: str,
        working_directory: str | None,
        stdin: str | None,
    ) -> str:
        lines = [container_shell_preamble().rstrip()]
        lines.append(f"cd {shlex.quote(working_directory or '/home/agent')}")
        if stdin is not None:
            lines.append("cat <<'EOF_AGENT_EXEC_STDIN' >/tmp/agent_exec.stdin")
            lines.append(stdin)
            lines.append("EOF_AGENT_EXEC_STDIN")
            lines.append(f"{command} < /tmp/agent_exec.stdin")
        else:
            lines.append(command)
        return "\n".join(lines) + "\n"

    @staticmethod
    def _log_snapshot(backend: Any, execution_id: str) -> str:
        try:
            return str(backend.get_log_snapshot(execution_id, lines=0, mode="raw") or "")
        except Exception:
            return ""

    @staticmethod
    def _stdout_stderr_from_status(*, status: Any, logs: str, success: bool) -> tuple[str, str]:
        metadata = getattr(status, "metadata", {})
        stdout = ""
        stderr = ""
        if isinstance(metadata, Mapping):
            stdout = str(metadata.get("stdout") or "")
            stderr = str(metadata.get("stderr") or "")
        if not stdout:
            stdout = logs
        if not stderr and not success:
            stderr = str(getattr(status, "message", None) or "")
        return stdout, stderr

    @staticmethod
    def _exit_code_from_status(*, status: Any, success: bool) -> int:
        metadata = getattr(status, "metadata", {})
        if isinstance(metadata, Mapping):
            raw_exit_code = metadata.get("exit_code")
            if raw_exit_code is not None:
                try:
                    return int(raw_exit_code)
                except (TypeError, ValueError):
                    pass
        return 0 if success else 1

from __future__ import annotations

import shlex
import subprocess
from collections.abc import Mapping, Sequence
from typing import Any

from .base import EffectProviderBase
from .vm_targeting import resolve_vm_target


class VmExecEffectProvider(EffectProviderBase):
    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = self._payload(resolved_config)
        context = self._context()
        resolved = resolve_vm_target(context, payload, effect_type="effect:vm:exec")
        vm_name = resolved.vm_name
        session_id = resolved.session_id
        vm_user = self._normalize_optional_string(payload.get("vm_user")) or "agent"
        command = self._normalize_command(payload.get("command"))
        working_directory = self._normalize_optional_string(payload.get("working_directory"))
        timeout_seconds = self._as_float_or_none(payload.get("timeout_seconds")) or 3600.0
        check = bool(payload.get("check", True))
        stdin = self._normalize_optional_string(payload.get("stdin"))
        environment = self._normalize_environment(payload.get("environment"))

        vm_exec = getattr(context.vm_executor, "vm_exec", None)
        if callable(vm_exec):
            result = vm_exec(
                target=vm_name,
                vm_user=vm_user,
                command=command,
                working_directory=working_directory,
                environment=environment,
                stdin=stdin,
                timeout_seconds=timeout_seconds,
                check=check,
            )
            if not isinstance(result, Mapping):
                raise RuntimeError("Admin runtime vm_exec must return a mapping")
            payload = dict(result)
            payload.setdefault("vm_name", vm_name)
            payload.setdefault("action", "vm_exec")
            payload.setdefault("success", False)
            payload.setdefault("stdout", "")
            payload.setdefault("stderr", "")
            payload.setdefault("exit_code", 0 if payload.get("success") else 1)
            payload.setdefault("message", "")
            payload.setdefault("session_id", session_id)
            if not payload.get("session_id"):
                payload["session_id"] = session_id
            if check and not bool(payload.get("success")):
                raise RuntimeError(
                    str(
                        payload.get("stderr")
                        or payload.get("stdout")
                        or payload.get("message")
                        or "VM command failed"
                    )
                )
            return payload

        return self._execute_via_ssh(
            vm_name=vm_name,
            session_id=session_id,
            vm_user=vm_user,
            command=command,
            working_directory=working_directory,
            timeout_seconds=timeout_seconds,
            check=check,
            stdin=stdin,
            environment=environment,
        )

    def _payload(self, resolved_config: Mapping[str, Any] | None) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if resolved_config:
            payload.update(dict(resolved_config))
        return payload

    def _context(self):
        if self.runtime_context is None:
            raise RuntimeError("VmExecEffectProvider has no runtime context")
        return self.runtime_context

    def _execute_via_ssh(
        self,
        *,
        vm_name: str,
        session_id: str | None,
        vm_user: str,
        command: str,
        working_directory: str | None,
        timeout_seconds: float,
        check: bool,
        stdin: str | None,
        environment: Mapping[str, str],
    ) -> dict[str, Any]:

        from evander.admin.services.vm_helpers import (
            resolve_private_key_path,
            resolve_vm_ip,
            ssh_options,
        )

        vm_ip = resolve_vm_ip(vm_name)
        ssh_private_key_path = resolve_private_key_path()
        if not vm_ip:
            raise RuntimeError(f"Unable to resolve VM IP for {vm_name!r}")
        if ssh_private_key_path is None:
            raise RuntimeError(
                "Missing SSH private key. Set SSH_PRIVATE_KEY_PATH to the private key that matches the VM's authorized key."
            )

        remote_script = self._build_remote_script(
            command=command,
            working_directory=working_directory,
            env=environment,
            stdin=stdin,
        )
        completed = subprocess.run(
            [
                "ssh",
                *ssh_options(vm_name, vm_ip, ssh_private_key_path),
                f"{vm_user}@{vm_ip}",
                "bash",
                "-s",
            ],
            input=remote_script,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        success = completed.returncode == 0
        if check and not success:
            raise RuntimeError(
                stderr.strip()
                or stdout.strip()
                or f"VM command failed with exit code {completed.returncode}"
            )
        return {
            "success": success,
            "vm_name": vm_name,
            "session_id": session_id,
            "action": "vm_exec",
            "message": stderr.strip()
            or stdout.strip()
            or f"VM command exited with code {completed.returncode}",
            "exit_code": completed.returncode,
            "stdout": stdout,
            "stderr": stderr,
        }

    def _build_remote_script(
        self,
        *,
        command: str,
        working_directory: str | None,
        env: Mapping[str, str],
        stdin: str | None,
    ) -> str:
        lines = ["set -euo pipefail"]
        if working_directory:
            lines.append(f"cd {shlex.quote(working_directory)}")
        for key, value in env.items():
            lines.append(f"export {key}={shlex.quote(value)}")
        if stdin is not None:
            lines.append("cat <<'EOF_VM_EXEC_STDIN' >/tmp/vm_exec.stdin")
            lines.append(stdin)
            lines.append("EOF_VM_EXEC_STDIN")
            lines.append(f"{command} < /tmp/vm_exec.stdin")
        else:
            lines.append(command)
        return "\n".join(lines) + "\n"

    @staticmethod
    def _normalize_optional_string(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

    @classmethod
    def _normalize_command(cls, value: Any) -> str:
        if isinstance(value, str):
            command = value.strip()
            if not command:
                raise ValueError("effect:vm:exec requires a non-empty command")
            return command
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            pieces = [cls._normalize_optional_string(item) for item in value]
            normalized = [piece for piece in pieces if piece]
            if not normalized:
                raise ValueError("effect:vm:exec requires a non-empty command")
            return shlex.join(normalized)
        raise ValueError("effect:vm:exec command must be a string or sequence of strings")

    @staticmethod
    def _normalize_environment(value: Any) -> dict[str, str]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise ValueError("effect:vm:exec environment must be a mapping")
        env: dict[str, str] = {}
        for key, raw_value in value.items():
            normalized_key = str(key).strip()
            if not normalized_key:
                continue
            env[normalized_key] = "" if raw_value is None else str(raw_value)
        return env

    @staticmethod
    def _as_float_or_none(value: Any) -> float | None:
        if value is None or value == "":
            return None
        return float(value)

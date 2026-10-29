from __future__ import annotations

import shlex
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath
from typing import Any

from .base import EffectProviderBase
from .vm_targeting import resolve_vm_target


class VmDeleteFilesEffectProvider(EffectProviderBase):
    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload = dict(resolved_config or {})
        context = self._context()
        resolved = resolve_vm_target(context, payload, effect_type="effect:vm:delete-files")
        vm_name = resolved.vm_name
        session_id = resolved.session_id
        vm_user = self._normalize_optional_string(payload.get("vm_user")) or "agent"
        paths = self._normalize_paths(payload.get("paths"))
        recursive = bool(payload.get("recursive", False))
        missing_ok = bool(payload.get("missing_ok", True))

        vm_delete_files = getattr(context.vm_file_deleter, "vm_delete_files", None)
        if callable(vm_delete_files):
            result = vm_delete_files(
                target=resolved.operation_target,
                vm_user=vm_user,
                paths=paths,
                recursive=recursive,
                missing_ok=missing_ok,
            )
            if not isinstance(result, Mapping):
                raise RuntimeError("Admin runtime vm_delete_files must return a mapping")
            normalized = dict(result)
            normalized.setdefault("success", False)
            normalized.setdefault("action", "vm_delete_files")
            normalized.setdefault("target", resolved.operation_target)
            normalized.setdefault("vm_name", vm_name)
            normalized.setdefault("vm_user", vm_user)
            normalized.setdefault("deleted_count", len(normalized.get("deleted_paths", [])))
            normalized.setdefault("deleted_paths", [])
            normalized.setdefault("message", "")
            normalized.setdefault("session_id", session_id)
            if not normalized.get("session_id"):
                normalized["session_id"] = session_id
            return normalized

        return self._execute_via_ssh(
            vm_name=vm_name,
            session_id=session_id,
            vm_user=vm_user,
            paths=paths,
            recursive=recursive,
            missing_ok=missing_ok,
        )

    def _context(self):
        if self.runtime_context is None:
            raise RuntimeError("VmDeleteFilesEffectProvider has no runtime context")
        return self.runtime_context

    def _execute_via_ssh(
        self,
        *,
        vm_name: str,
        session_id: str | None,
        vm_user: str,
        paths: list[str],
        recursive: bool,
        missing_ok: bool,
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

        flag = "-rf" if recursive else "-f"
        commands = ["set -euo pipefail"]
        if missing_ok:
            commands.extend(f"rm {flag} -- {shlex.quote(path)}" for path in paths)
        else:
            commands.extend(f"test -e {shlex.quote(path)}" for path in paths)
            commands.extend(f"rm {flag} -- {shlex.quote(path)}" for path in paths)
        completed = subprocess.run(
            [
                "ssh",
                *ssh_options(vm_name, vm_ip, ssh_private_key_path),
                f"{vm_user}@{vm_ip}",
                "bash",
                "-s",
            ],
            input="\n".join(commands) + "\n",
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                completed.stderr.strip() or completed.stdout.strip() or "VM delete failed"
            )
        return {
            "success": True,
            "vm_name": vm_name,
            "session_id": session_id,
            "action": "vm_delete_files",
            "message": f"Deleted {len(paths)} path(s) from {vm_name}",
            "deleted_count": len(paths),
            "deleted_paths": paths,
            "vm_user": vm_user,
        }

    @classmethod
    def _normalize_paths(cls, value: object) -> list[str]:
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
            raise ValueError("effect:vm:delete-files paths must be a list")
        normalized: list[str] = []
        for raw_path in value:
            path = cls._normalize_optional_string(raw_path)
            if not path:
                raise ValueError("effect:vm:delete-files paths entries must be non-empty strings")
            if path.startswith("~") or not path.startswith("/"):
                raise ValueError("effect:vm:delete-files paths must be absolute guest paths")
            normalized.append(PurePosixPath(path).as_posix())
        if not normalized:
            raise ValueError("effect:vm:delete-files requires at least one path")
        return normalized

    @staticmethod
    def _normalize_optional_string(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

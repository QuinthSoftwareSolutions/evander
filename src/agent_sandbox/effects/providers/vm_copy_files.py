from __future__ import annotations

import os
import shlex
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any

from agent_sandbox.admin_service import _resolve_private_key_path, _ssh_options

from .base import EffectProviderBase


class VmCopyFilesEffectProvider(EffectProviderBase):
    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        context = self._context()
        payload = dict(resolved_config or {})
        vm_user = self._normalize_optional_string(payload.get("vm_user")) or "agent"
        files = self._normalize_file_entries(payload.get("files"))
        if not files:
            raise ValueError("effect:vm:copy-files requires at least one file entry")
        target = self._resolve_target(payload)

        vm_copy_files = getattr(context.admin_service, "vm_copy_files", None)
        if callable(vm_copy_files):
            result = vm_copy_files(target=target, files=files, vm_user=vm_user)
            if not isinstance(result, Mapping):
                raise RuntimeError("AdminService.vm_copy_files must return a mapping")
            normalized = dict(result)
            normalized.setdefault("success", False)
            normalized.setdefault("action", "vm_copy_files")
            normalized.setdefault("target", target)
            normalized.setdefault("vm_user", vm_user)
            normalized.setdefault("copied_count", len(normalized.get("copied_paths", [])))
            normalized.setdefault("copied_paths", [])
            normalized.setdefault("message", "")
            return normalized

        directory_path = self._normalize_optional_string(context.metadata.get("directory_path")) or "root"
        explicit_vm_ip = self._normalize_optional_string(payload.get("vm_ip"))
        vm_name, vm_ip = self._resolve_vm_target(
            target=target,
            explicit_vm_ip=explicit_vm_ip,
            vm_user=vm_user,
            directory_path=directory_path,
        )
        ssh_private_key_path = _resolve_private_key_path()
        if ssh_private_key_path is None:
            raise RuntimeError(
                "Missing SSH private key. Set SSH_PRIVATE_KEY_PATH to the private key that matches the VM's authorized key."
            )

        copied_files: list[dict[str, Any]] = []
        with tempfile.TemporaryDirectory(prefix="agent-sandbox-vm-copy-files-") as staging_dir:
            staging_root = Path(staging_dir)
            for index, entry in enumerate(files):
                staged_source = self._stage_source_file(entry, staging_root, index=index)
                remote_path = self._normalize_remote_path(entry["destination_path"])
                if not entry["overwrite"]:
                    self._ensure_remote_absent(vm_name, vm_ip, vm_user, ssh_private_key_path, remote_path)
                self._ensure_remote_parent(vm_name, vm_ip, vm_user, ssh_private_key_path, remote_path)
                self._copy_one_file(
                    vm_name=vm_name,
                    vm_ip=vm_ip,
                    vm_user=vm_user,
                    ssh_private_key_path=ssh_private_key_path,
                    local_source=staged_source,
                    remote_path=remote_path,
                    mode=entry["mode"],
                )
                copied_files.append(
                    {
                        "source_path": entry.get("source_path"),
                        "destination_path": remote_path,
                        "source_kind": "content" if entry.get("content") is not None else "source_path",
                        "recursive": staged_source.is_dir(),
                        "mode": entry["mode"],
                        "overwrite": entry["overwrite"],
                    }
                )

        copied_paths = [entry["destination_path"] for entry in copied_files]
        message = f"Copied {len(copied_paths)} file(s) to {vm_name}"
        return {
            "success": True,
            "action": "vm_copy_files",
            "message": message,
            "target": target,
            "vm_name": vm_name,
            "vm_ip": vm_ip,
            "vm_user": vm_user,
            "directory_path": directory_path,
            "copied_count": len(copied_paths),
            "copied_paths": copied_paths,
            "file_count": len(copied_files),
            "copied_files": copied_files,
        }

    def _context(self):
        if self.runtime_context is None:
            raise RuntimeError("VmCopyFilesEffectProvider has no runtime context")
        return self.runtime_context

    def _resolve_target(self, payload: dict[str, Any]) -> str:
        for field_name in ("target", "vm_name", "session_id", "vm_id"):
            value = self._normalize_optional_string(payload.get(field_name))
            if value:
                return value
        raise ValueError("effect:vm:copy-files requires target, vm_name, or session_id")

    def _resolve_vm_target(
        self,
        *,
        target: str,
        explicit_vm_ip: str | None,
        vm_user: str,
        directory_path: str,
    ) -> tuple[str, str]:
        context = self._context()
        if explicit_vm_ip:
            vm_name = target
            return vm_name, explicit_vm_ip

        admin_service = context.admin_service
        list_vms = getattr(admin_service, "list_vms", None)
        if not callable(list_vms):
            raise RuntimeError("VmCopyFilesEffectProvider requires AdminService.list_vms()")

        summaries = list_vms(include_inactive=True, omit_state=True, vm_user=vm_user, directory_path=directory_path)
        matches: list[tuple[str, str]] = []
        for summary in summaries:
            vm_name = self._normalize_optional_string(getattr(summary, "vm_name", None))
            vm_ip = self._normalize_optional_string(getattr(summary, "ip_address", None))
            session_ids = [
                self._normalize_optional_string(value)
                for value in getattr(summary, "session_ids", [])
                if self._normalize_optional_string(value)
            ]
            if vm_name == target or target in session_ids:
                if vm_ip:
                    matches.append((vm_name or target, vm_ip))

        if not matches:
            raise RuntimeError(f"Unable to resolve running VM for target {target!r}")
        if len(matches) > 1:
            raise RuntimeError(f"Target {target!r} resolved to multiple running VMs")
        return matches[0]

    def _ensure_remote_parent(
        self,
        vm_name: str,
        vm_ip: str,
        vm_user: str,
        ssh_private_key_path: Path,
        remote_path: str,
    ) -> None:
        remote_parent = PurePosixPath(remote_path).parent.as_posix() or "."
        if remote_parent == ".":
            return
        command = f"mkdir -p -- {shlex.quote(remote_parent)}"
        completed = subprocess.run(
            [
                "ssh",
                *_ssh_options(vm_name, vm_ip, ssh_private_key_path),
                f"{vm_user}@{vm_ip}",
                command,
            ],
            capture_output=True,
            text=True,
            check=False,
            env=os.environ.copy(),
        )
        if completed.returncode != 0:
            raise RuntimeError(
                (completed.stderr or completed.stdout or "").strip()
                or f"Failed to create remote directory for {remote_path}"
            )

    def _ensure_remote_absent(
        self,
        vm_name: str,
        vm_ip: str,
        vm_user: str,
        ssh_private_key_path: Path,
        remote_path: str,
    ) -> None:
        command = f"test ! -e {shlex.quote(remote_path)}"
        completed = subprocess.run(
            [
                "ssh",
                *_ssh_options(vm_name, vm_ip, ssh_private_key_path),
                f"{vm_user}@{vm_ip}",
                command,
            ],
            capture_output=True,
            text=True,
            check=False,
            env=os.environ.copy(),
        )
        if completed.returncode != 0:
            raise RuntimeError(
                (completed.stderr or completed.stdout or "").strip()
                or f"Remote path already exists: {remote_path}"
            )

    def _copy_one_file(
        self,
        *,
        vm_name: str,
        vm_ip: str,
        vm_user: str,
        ssh_private_key_path: Path,
        local_source: Path,
        remote_path: str,
        mode: str | None,
    ) -> None:
        remote_spec = f"{vm_user}@{vm_ip}:{remote_path}"
        cmd = ["scp"]
        if local_source.is_dir():
            cmd.append("-r")
        cmd.extend(
            [
                *_ssh_options(vm_name, vm_ip, ssh_private_key_path),
                str(local_source),
                remote_spec,
            ]
        )
        completed = subprocess.run(cmd, capture_output=True, text=True, check=False, env=os.environ.copy())
        if completed.returncode != 0:
            raise RuntimeError((completed.stderr or completed.stdout or "").strip() or f"Failed to copy {local_source}")
        if mode is None:
            return
        chmod_mode = self._normalize_mode(mode)
        chmod_command = f"chmod {shlex.quote(chmod_mode)} -- {shlex.quote(remote_path)}"
        chmod_result = subprocess.run(
            [
                "ssh",
                *_ssh_options(vm_name, vm_ip, ssh_private_key_path),
                f"{vm_user}@{vm_ip}",
                chmod_command,
            ],
            capture_output=True,
            text=True,
            check=False,
            env=os.environ.copy(),
        )
        if chmod_result.returncode != 0:
            raise RuntimeError(
                (chmod_result.stderr or chmod_result.stdout or "").strip()
                or f"Failed to set mode for {remote_path}"
            )

    def _stage_source_file(self, entry: dict[str, Any], staging_root: Path, *, index: int) -> Path:
        if entry.get("content") is not None:
            suffix = self._suffix_for_destination(entry["destination_path"])
            staged_path = staging_root / f"inline-{index}{suffix}"
            content = entry["content"]
            if isinstance(content, (bytes, bytearray)):
                staged_path.write_bytes(bytes(content))
            else:
                staged_path.write_text(str(content), encoding="utf-8")
            return staged_path

        source_path = self._normalize_optional_string(entry.get("source_path"))
        if not source_path:
            raise ValueError("effect:vm:copy-files entries require source_path or content")
        local_source = Path(source_path).expanduser().resolve()
        if not local_source.exists():
            raise FileNotFoundError(f"Source path does not exist: {local_source}")
        return local_source

    def _normalize_file_entries(self, value: object) -> list[dict[str, Any]]:
        if value is None:
            return []
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
            raise ValueError("effect:vm:copy-files files must be a list")
        entries: list[dict[str, Any]] = []
        for raw_entry in value:
            if not isinstance(raw_entry, Mapping):
                raise ValueError("effect:vm:copy-files file entries must be objects")
            destination_path = self._normalize_optional_string(
                raw_entry.get("destination_path") or raw_entry.get("destination")
            )
            if not destination_path:
                raise ValueError("effect:vm:copy-files entries require destination_path")
            source_path = self._normalize_optional_string(raw_entry.get("source_path") or raw_entry.get("source"))
            content = raw_entry.get("content")
            if (source_path is None) == (content is None):
                raise ValueError("effect:vm:copy-files entries require exactly one of source_path or content")
            mode = self._normalize_optional_string(raw_entry.get("mode"))
            overwrite = raw_entry.get("overwrite")
            if overwrite is None:
                overwrite = True
            elif not isinstance(overwrite, bool):
                raise ValueError("effect:vm:copy-files overwrite must be a boolean when provided")
            entries.append(
                {
                    "source_path": source_path,
                    "destination_path": destination_path,
                    "content": content,
                    "mode": mode,
                    "overwrite": overwrite,
                }
            )
        return entries

    @staticmethod
    def _normalize_remote_path(value: str) -> str:
        remote_path = str(value).strip()
        if not remote_path:
            raise ValueError("effect:vm:copy-files entries require destination_path")
        if remote_path.startswith("~"):
            raise ValueError("effect:vm:copy-files destination_path must be an absolute guest path")
        if not remote_path.startswith("/"):
            raise ValueError("effect:vm:copy-files destination_path must be an absolute guest path")
        return PurePosixPath(remote_path).as_posix()

    @staticmethod
    def _suffix_for_destination(destination_path: str) -> str:
        suffix = PurePosixPath(destination_path).suffix
        return suffix if suffix else ".txt"

    @staticmethod
    def _normalize_mode(value: str) -> str:
        mode = value.strip()
        if not mode:
            raise ValueError("effect:vm:copy-files mode must be a non-empty string")
        if mode.isdigit():
            return mode
        if not all(char in "01234567" for char in mode):
            raise ValueError(f"effect:vm:copy-files mode must be octal digits: {mode!r}")
        return mode

    @staticmethod
    def _normalize_optional_string(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

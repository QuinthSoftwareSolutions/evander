from __future__ import annotations

import base64
import json
import os
import shlex
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from evander.managed_sections import normalize_managed_section_id
from evander.workflow.binding import resolve_workflow_bindings

from .base import EffectProviderBase

_REPO_ROOT = Path(__file__).resolve().parents[4]
_DEFAULT_REPO_SOURCE_ROOTS = (
    _REPO_ROOT / "templates",
    _REPO_ROOT / "scripts" / "guest",
)
_DEFAULT_RUNTIME_SOURCE_ROOT_NAMES = (
    "published-artifacts",
    "communication-artifacts",
)


class VmCopyFilesEffectProvider(EffectProviderBase):
    def normalize_resolved_config(
        self,
        *,
        resolved_config: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        upstream_payload = kwargs.get("upstream_payload")
        declared_upstream_output_names = kwargs.get("declared_upstream_output_names")
        system_value_resolver = kwargs.get("system_value_resolver")

        next_config = deepcopy(dict(resolved_config or {}))
        raw_files = next_config.get("files")
        if not isinstance(raw_files, list):
            return next_config
        effect_type = self._effect_type_label()
        allowed_source_roots = self._approved_source_roots(
            next_config.get("allowed_source_roots"),
            effect_type=effect_type,
        )
        for index, raw_entry in enumerate(raw_files):
            if not isinstance(raw_entry, dict):
                continue
            source_format = str(raw_entry.get("source_format") or "").strip().lower()
            if source_format not in {"", "file", "template"}:
                raise ValueError(
                    f"effect:vm:copy-files files[{index}] source_format must be one of: file, template"
                )
            if source_format != "template":
                continue
            source_path = str(raw_entry.get("source_path") or "").strip()
            if not source_path:
                raise ValueError(
                    f"{effect_type} files[{index}] source_path is required for template sources"
                )
            template_path = self._resolve_allowed_source_path(
                source_path,
                allowed_source_roots=allowed_source_roots,
                effect_type=effect_type,
                index=index,
            )
            if not template_path.exists():
                raise ValueError(f"{effect_type} template source does not exist: {template_path}")
            if template_path.is_dir():
                raise ValueError(f"{effect_type} template source must be a file: {template_path}")
            template_text = template_path.read_text(encoding="utf-8")
            rendered = resolve_workflow_bindings(
                template_text,
                upstream_payload=upstream_payload,
                declared_upstream_output_names=declared_upstream_output_names,
                system_value_resolver=system_value_resolver,
                strict_validation=True,
            ).resolved_config
            if not isinstance(rendered, str):
                raise ValueError(
                    f"{effect_type} template source must resolve to text: {template_path}"
                )
            raw_entry["content"] = rendered
            raw_entry.pop("source_path", None)
        return next_config

    def execute(self, *, resolved_config: Mapping[str, Any] | None = None) -> dict[str, Any]:
        context = self._context()
        payload = dict(resolved_config or {})
        vm_user = self._normalize_optional_string(payload.get("vm_user")) or "agent"
        effect_type = self._effect_type_label()
        allowed_source_roots = self._approved_source_roots(
            payload.get("allowed_source_roots"),
            effect_type=effect_type,
        )
        files = self._normalize_file_entries(
            payload.get("files"),
            allowed_source_roots=allowed_source_roots,
            effect_type=effect_type,
        )
        if not files:
            raise ValueError("effect:vm:copy-files requires at least one file entry")
        target = self._resolve_target(payload)

        vm_copy_files = getattr(context.vm_file_copier, "vm_copy_files", None)
        if callable(vm_copy_files):
            result = vm_copy_files(target=target, files=files, vm_user=vm_user)
            if not isinstance(result, Mapping):
                raise RuntimeError("Admin runtime vm_copy_files must return a mapping")
            normalized = dict(result)
            normalized.setdefault("success", False)
            normalized.setdefault("action", "vm_copy_files")
            normalized.setdefault("target", target)
            normalized.setdefault("vm_user", vm_user)
            normalized.setdefault("copied_count", len(normalized.get("copied_paths", [])))
            normalized.setdefault("copied_paths", [])
            normalized.setdefault("message", "")
            return normalized

        directory_path = (
            self._normalize_optional_string(context.metadata.get("directory_path")) or "root"
        )
        explicit_vm_ip = self._normalize_optional_string(payload.get("vm_ip"))
        vm_name, vm_ip = self._resolve_vm_target(
            target=target,
            explicit_vm_ip=explicit_vm_ip,
            vm_user=vm_user,
            directory_path=directory_path,
        )

        from evander.admin.services.vm_helpers import resolve_private_key_path

        ssh_private_key_path = resolve_private_key_path()
        if ssh_private_key_path is None:
            raise RuntimeError(
                "Missing SSH private key. Set SSH_PRIVATE_KEY_PATH to the private key that matches the VM's authorized key."
            )

        copied_files: list[dict[str, Any]] = []
        with tempfile.TemporaryDirectory(prefix="evander-vm-copy-files-") as staging_dir:
            staging_root = Path(staging_dir)
            for index, entry in enumerate(files):
                remote_path = self._normalize_remote_path(entry["destination_path"])
                if not entry["overwrite"]:
                    self._ensure_remote_absent(
                        vm_name, vm_ip, vm_user, ssh_private_key_path, remote_path
                    )
                self._ensure_remote_parent(
                    vm_name, vm_ip, vm_user, ssh_private_key_path, remote_path
                )
                if entry.get("merge_strategy") == "managed_section":
                    self._merge_remote_managed_section(
                        vm_name=vm_name,
                        vm_ip=vm_ip,
                        vm_user=vm_user,
                        ssh_private_key_path=ssh_private_key_path,
                        remote_path=remote_path,
                        content=self._text_content(entry.get("content")),
                        section_id=str(entry.get("section_id") or ""),
                        remove_section_ids=list(entry.get("remove_section_ids") or []),
                        mode=entry["mode"],
                    )
                    recursive = False
                else:
                    staged_source = self._stage_source_file(entry, staging_root, index=index)
                    self._copy_one_file(
                        vm_name=vm_name,
                        vm_ip=vm_ip,
                        vm_user=vm_user,
                        ssh_private_key_path=ssh_private_key_path,
                        local_source=staged_source,
                        remote_path=remote_path,
                        mode=entry["mode"],
                    )
                    recursive = staged_source.is_dir()
                copied_file = {
                    "source_path": entry.get("source_path"),
                    "destination_path": remote_path,
                    "source_kind": "content" if entry.get("content") is not None else "source_path",
                    "recursive": recursive,
                    "mode": entry["mode"],
                    "overwrite": entry["overwrite"],
                    "merge_strategy": entry.get("merge_strategy"),
                    "section_id": entry.get("section_id"),
                }
                if entry.get("remove_section_ids"):
                    copied_file["remove_section_ids"] = list(entry["remove_section_ids"])
                copied_files.append(copied_file)

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

        list_vms = getattr(context.vm_inventory, "list_vms", None)
        if not callable(list_vms):
            raise RuntimeError("VmCopyFilesEffectProvider requires vm_inventory.list_vms()")

        summaries = list_vms(
            include_inactive=True, omit_state=True, vm_user=vm_user, directory_path=directory_path
        )
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
        from evander.admin.services.vm_helpers import ssh_options

        remote_parent = PurePosixPath(remote_path).parent.as_posix() or "."
        if remote_parent == ".":
            return
        command = f"mkdir -p -- {shlex.quote(remote_parent)}"
        completed = subprocess.run(
            [
                "ssh",
                *ssh_options(vm_name, vm_ip, ssh_private_key_path),
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
        from evander.admin.services.vm_helpers import ssh_options

        command = f"test ! -e {shlex.quote(remote_path)}"
        completed = subprocess.run(
            [
                "ssh",
                *ssh_options(vm_name, vm_ip, ssh_private_key_path),
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
        from evander.admin.services.vm_helpers import ssh_options

        remote_spec = f"{vm_user}@{vm_ip}:{remote_path}"
        cmd = ["scp"]
        if local_source.is_dir():
            cmd.append("-r")
        cmd.extend(
            [
                *ssh_options(vm_name, vm_ip, ssh_private_key_path),
                str(local_source),
                remote_spec,
            ]
        )
        completed = subprocess.run(
            cmd, capture_output=True, text=True, check=False, env=os.environ.copy()
        )
        if completed.returncode != 0:
            raise RuntimeError(
                (completed.stderr or completed.stdout or "").strip()
                or f"Failed to copy {local_source}"
            )
        if mode is None:
            return
        chmod_mode = self._normalize_mode(mode)
        chmod_command = f"chmod {shlex.quote(chmod_mode)} -- {shlex.quote(remote_path)}"
        chmod_result = subprocess.run(
            [
                "ssh",
                *ssh_options(vm_name, vm_ip, ssh_private_key_path),
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

    def _merge_remote_managed_section(
        self,
        *,
        vm_name: str,
        vm_ip: str,
        vm_user: str,
        ssh_private_key_path: Path,
        remote_path: str,
        content: str,
        section_id: str,
        remove_section_ids: Sequence[str],
        mode: str | None,
    ) -> None:
        from evander.admin.services.vm_helpers import ssh_options

        normalized_section_id = normalize_managed_section_id(section_id)
        normalized_remove_section_ids = [
            normalize_managed_section_id(stale_section_id)
            for stale_section_id in remove_section_ids
        ]
        encoded_content = base64.b64encode(content.encode("utf-8")).decode("ascii")
        script = "\n".join(
            [
                "python3 - <<'PY'",
                "import base64",
                "import re",
                "from pathlib import Path",
                f"path = Path({remote_path!r})",
                f"section_id = {normalized_section_id!r}",
                f"remove_section_ids = {normalized_remove_section_ids!r}",
                f"content = base64.b64decode({encoded_content!r}).decode('utf-8')",
                "def markers(current_section_id):",
                "    return (",
                "        f'<!-- evander-managed-section:{current_section_id}:begin -->',",
                "        f'<!-- evander-managed-section:{current_section_id}:end -->',",
                "    )",
                "begin_marker, end_marker = markers(section_id)",
                "body = content.rstrip() + '\\n'",
                "section = f'{begin_marker}\\n{body}{end_marker}\\n'",
                "existing = path.read_text(encoding='utf-8') if path.exists() else ''",
                "for stale_section_id in remove_section_ids:",
                "    stale_begin, stale_end = markers(stale_section_id)",
                "    stale_pattern = re.compile(rf'(?ms)^{re.escape(stale_begin)}\\n.*?^{re.escape(stale_end)}\\n{{0,2}}')",
                "    existing = stale_pattern.sub('', existing)",
                "pattern = re.compile(rf'(?ms)^{re.escape(begin_marker)}\\n.*?^{re.escape(end_marker)}\\n?')",
                "if pattern.search(existing):",
                "    merged = pattern.sub(section, existing, count=1)",
                "elif not existing:",
                "    merged = section",
                "else:",
                "    prefix = existing if existing.endswith('\\n') else existing + '\\n'",
                "    if not prefix.endswith('\\n\\n'):",
                "        prefix += '\\n'",
                "    merged = prefix + section",
                "path.write_text(merged, encoding='utf-8')",
                "PY",
            ]
        )
        completed = subprocess.run(
            [
                "ssh",
                *ssh_options(vm_name, vm_ip, ssh_private_key_path),
                f"{vm_user}@{vm_ip}",
                "bash",
                "-s",
            ],
            capture_output=True,
            text=True,
            input=f"{script}\n",
            check=False,
            env=os.environ.copy(),
        )
        if completed.returncode != 0:
            raise RuntimeError(
                (completed.stderr or completed.stdout or "").strip()
                or f"Failed to merge managed section into {remote_path}"
            )
        if mode is None:
            return
        chmod_mode = self._normalize_mode(mode)
        chmod_command = f"chmod {shlex.quote(chmod_mode)} -- {shlex.quote(remote_path)}"
        chmod_result = subprocess.run(
            [
                "ssh",
                *ssh_options(vm_name, vm_ip, ssh_private_key_path),
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

    def _normalize_file_entries(
        self,
        value: object,
        *,
        allowed_source_roots: Sequence[Path] | None = None,
        effect_type: str = "effect:vm:copy-files",
    ) -> list[dict[str, Any]]:
        if value is None:
            return []
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
            raise ValueError(f"{effect_type} files must be a list")
        entries: list[dict[str, Any]] = []
        source_roots = list(
            allowed_source_roots or self._approved_source_roots(None, effect_type=effect_type)
        )
        for raw_entry in value:
            if not isinstance(raw_entry, Mapping):
                raise ValueError(f"{effect_type} file entries must be objects")
            destination_path = self._normalize_optional_string(
                raw_entry.get("destination_path") or raw_entry.get("destination")
            )
            if not destination_path:
                raise ValueError(f"{effect_type} entries require destination_path")
            source_path = self._normalize_optional_string(
                raw_entry.get("source_path") or raw_entry.get("source")
            )
            content = raw_entry.get("content")
            if (source_path is None) == (content is None):
                raise ValueError(
                    f"{effect_type} entries require exactly one of source_path or content"
                )
            if source_path is not None:
                source_path = str(
                    self._resolve_allowed_source_path(
                        source_path,
                        allowed_source_roots=source_roots,
                        effect_type=effect_type,
                        index=len(entries),
                    )
                )
            mode = self._normalize_optional_string(raw_entry.get("mode"))
            content_format = self._normalize_content_format(raw_entry.get("content_format"))
            merge_strategy = self._normalize_merge_strategy(raw_entry.get("merge_strategy"))
            section_id = self._normalize_optional_string(raw_entry.get("section_id"))
            remove_section_ids = self._normalize_remove_section_ids(
                raw_entry.get("remove_section_ids")
            )
            if merge_strategy == "managed_section":
                if source_path is not None:
                    raise ValueError(
                        f"{effect_type} managed_section entries require inline content"
                    )
                section_id = normalize_managed_section_id(section_id or "")
            elif remove_section_ids:
                raise ValueError(
                    f"{effect_type} remove_section_ids requires managed_section merge_strategy"
                )
            overwrite = raw_entry.get("overwrite")
            if overwrite is None:
                overwrite = True
            elif not isinstance(overwrite, bool):
                raise ValueError(f"{effect_type} overwrite must be a boolean when provided")
            entry = {
                "source_path": source_path,
                "destination_path": destination_path,
                "content": self._render_inline_content(content, content_format=content_format)
                if content is not None
                else None,
                "content_format": content_format,
                "mode": mode,
                "overwrite": overwrite,
            }
            if merge_strategy is not None:
                entry["merge_strategy"] = merge_strategy
                entry["section_id"] = section_id
                if remove_section_ids:
                    entry["remove_section_ids"] = remove_section_ids
            entries.append(entry)
        return entries

    def _approved_source_roots(self, value: Any, *, effect_type: str) -> list[Path]:
        roots = [self._resolve_root_path(root) for root in _DEFAULT_REPO_SOURCE_ROOTS]
        runtime_dir = self._runtime_dir_or_none()
        if runtime_dir is not None:
            roots.extend(
                self._resolve_root_path(runtime_dir / name)
                for name in _DEFAULT_RUNTIME_SOURCE_ROOT_NAMES
            )
        roots.extend(self._normalize_configured_source_roots(value, effect_type=effect_type))
        unique_roots: list[Path] = []
        seen: set[str] = set()
        for root in roots:
            key = str(root)
            if key in seen:
                continue
            seen.add(key)
            unique_roots.append(root)
        return unique_roots

    def _runtime_dir_or_none(self) -> Path | None:
        try:
            return self._context().config_path("runtime_dir")
        except KeyError:
            return None

    @staticmethod
    def _normalize_configured_source_roots(value: Any, *, effect_type: str) -> list[Path]:
        if value is None:
            return []
        if isinstance(value, str):
            raw_values = [value]
        elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
            raw_values = list(value)
        else:
            raise ValueError(f"{effect_type} allowed_source_roots must be a string or string list")
        roots: list[Path] = []
        for item in raw_values:
            if not isinstance(item, str):
                raise ValueError(
                    f"{effect_type} allowed_source_roots must be a string or string list"
                )
            text = item.strip()
            if not text:
                raise ValueError(f"{effect_type} allowed_source_roots must include a path")
            root = Path(text).expanduser()
            if not root.is_absolute():
                raise ValueError(
                    f"{effect_type} allowed_source_roots entries must be absolute host paths"
                )
            roots.append(VmCopyFilesEffectProvider._resolve_root_path(root))
        return roots

    @staticmethod
    def _resolve_root_path(root: Path) -> Path:
        return Path(root).expanduser().resolve(strict=False)

    def _resolve_allowed_source_path(
        self,
        source_path: str,
        *,
        allowed_source_roots: Sequence[Path],
        effect_type: str,
        index: int,
    ) -> Path:
        text = str(source_path or "").strip()
        if not text:
            raise ValueError(f"{effect_type} files[{index}] source_path is required")
        if "\x00" in text:
            raise ValueError(f"{effect_type} files[{index}] source_path must not contain NUL")
        candidate = Path(text).expanduser()
        if not candidate.is_absolute():
            candidate = _REPO_ROOT / candidate
        preflight_path = candidate.resolve(strict=False)
        if not self._path_is_under_any_root(preflight_path, allowed_source_roots):
            raise ValueError(
                f"{effect_type} files[{index}] source_path is outside approved "
                f"host source roots: {preflight_path}"
            )
        if not preflight_path.exists():
            raise FileNotFoundError(f"Source path does not exist: {preflight_path}")
        resolved = candidate.resolve(strict=True)
        if not self._path_is_under_any_root(resolved, allowed_source_roots):
            raise ValueError(
                f"{effect_type} files[{index}] source_path is outside approved "
                f"host source roots: {resolved}"
            )
        return resolved

    @staticmethod
    def _path_is_under_any_root(path: Path, roots: Sequence[Path]) -> bool:
        for root in roots:
            try:
                path.relative_to(root)
                return True
            except ValueError:
                continue
        return False

    def _effect_type_label(self) -> str:
        value = getattr(self.effect_definition, "type", None)
        if isinstance(value, str) and value.strip():
            return value.strip()
        return "effect:vm:copy-files"

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
    def _normalize_content_format(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip().lower()
        if not text:
            return None
        if text not in {"text", "json", "yaml"}:
            raise ValueError("effect:vm:copy-files content_format must be one of: text, json, yaml")
        return text

    @staticmethod
    def _normalize_merge_strategy(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip().lower()
        if not text:
            return None
        if text != "managed_section":
            raise ValueError("effect:vm:copy-files merge_strategy must be one of: managed_section")
        return text

    @staticmethod
    def _normalize_remove_section_ids(value: Any) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
            raise ValueError("effect:vm:copy-files remove_section_ids must be a list")
        return [normalize_managed_section_id(str(item or "")) for item in value]

    @classmethod
    def _render_inline_content(cls, content: Any, *, content_format: str | None) -> Any:
        if content_format in (None, "text"):
            return content
        if content_format == "json":
            return json.dumps(content, indent=2, ensure_ascii=False)
        if content_format == "yaml":
            return yaml.safe_dump(content, sort_keys=False, allow_unicode=True)
        raise ValueError(f"Unsupported content_format: {content_format}")

    @staticmethod
    def _text_content(content: Any) -> str:
        if isinstance(content, (bytes, bytearray)):
            return bytes(content).decode("utf-8")
        return str(content if content is not None else "")

    @staticmethod
    def _normalize_optional_string(value: Any) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        return text or None

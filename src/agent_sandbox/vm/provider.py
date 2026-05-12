from __future__ import annotations

import base64
import os
import shlex
import subprocess
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Protocol

from agent_sandbox.admin import service as admin_service_module
from agent_sandbox.models import SessionManifest, VmActionResult, VmSummary
from agent_sandbox.repositories.session_manifests import (
    SessionManifestRepository,
    as_session_manifest_repository,
)
from agent_sandbox.repositories.skills_library import (
    SkillLibraryRepository,
    normalize_skill_names,
)
from agent_sandbox.vm.power_state import normalize_power_state


def session_vm_provider(session: SessionManifest | Any | None) -> str:
    if session is None:
        return "libvirt"
    provider = str(getattr(session, "vm_provider", "") or "").strip()
    if provider:
        return provider
    metadata = getattr(session, "metadata", {}) or {}
    if isinstance(metadata, Mapping):
        provider = str(metadata.get("vm_provider") or "").strip()
        if provider:
            return provider
    return "libvirt"


def session_vm_provider_id(
    session: SessionManifest | Any | None, *, fallback_vm_name: str | None = None
) -> str | None:
    if session is None:
        return fallback_vm_name
    provider_id = str(getattr(session, "vm_provider_id", "") or "").strip()
    if provider_id:
        return provider_id
    return str(getattr(session, "vm_id", "") or "").strip() or fallback_vm_name


def _runtime_session_repository(runtime: Any) -> SessionManifestRepository:
    return as_session_manifest_repository(getattr(runtime, "session_repository"))


def _managed_codex_skills_root(deps: Any, skill_names: list[str] | None) -> Path | None:
    names = normalize_skill_names(skill_names)
    if not names:
        return None
    runtime_config = getattr(getattr(deps, "runtime", None), "config", None)
    runtime_dir = getattr(runtime_config, "runtime_dir", None)
    if runtime_dir is None:
        return None
    repository = SkillLibraryRepository(Path(runtime_dir))
    if all(repository.has_skill(name) for name in names):
        return repository.skills_root
    return None


class AzureVmControlClientProtocol(Protocol):
    def list_vms(self, *, config: Any, include_inactive: bool = True) -> list[dict[str, Any]]: ...

    def create_vm(
        self,
        *,
        config: Any,
        vm_name: str,
        vm_user: str,
        cloud_init: str,
        tags: Mapping[str, str] | None = None,
    ) -> dict[str, Any]: ...

    def start_vm(self, *, config: Any, provider_id: str) -> dict[str, Any]: ...

    def stop_vm(self, *, config: Any, provider_id: str) -> dict[str, Any]: ...

    def resolve_private_ip(self, *, config: Any, provider_id: str) -> str | None: ...


def _read_ssh_public_key(path: Path) -> str:
    try:
        return path.expanduser().read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _derive_ssh_public_key(private_key_path: Path) -> str:
    try:
        completed = subprocess.run(
            ["ssh-keygen", "-y", "-f", str(private_key_path)],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return ""
    if completed.returncode != 0:
        return ""
    return (completed.stdout or "").strip()


def _resolve_azure_agent_admin_ssh_public_key(azure_config: Any) -> str:
    ssh_key_path = str(getattr(azure_config, "ssh_public_key_path", "") or "").strip()
    if ssh_key_path:
        return _read_ssh_public_key(Path(ssh_key_path))

    private_key_path = admin_service_module._resolve_private_key_path()
    if private_key_path is None:
        return ""
    public_key = _read_ssh_public_key(Path(f"{private_key_path}.pub"))
    if public_key:
        return public_key
    return _derive_ssh_public_key(private_key_path)


class AzureVmControlClient:
    def list_vms(self, *, config: Any, include_inactive: bool = True) -> list[dict[str, Any]]:
        del include_inactive
        command = self._base_command(config) + ["vm", "list", "-d", "--output", "json"]
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        if completed.returncode != 0:
            raise RuntimeError(
                (completed.stderr or completed.stdout or "").strip() or "az vm list failed"
            )
        import json

        payload = json.loads(completed.stdout or "[]")
        return payload if isinstance(payload, list) else []

    def create_vm(
        self,
        *,
        config: Any,
        vm_name: str,
        vm_user: str,
        cloud_init: str,
        tags: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        image_reference = str(getattr(config, "image_reference", "") or "").strip()
        image_tokens = image_reference.split(":") if image_reference else []
        command = self._base_command(config) + [
            "vm",
            "create",
            "--name",
            vm_name,
            "--resource-group",
            str(config.resource_group),
            "--admin-username",
            vm_user,
            "--size",
            str(config.vm_size),
            "--custom-data",
            cloud_init,
            "--vnet-name",
            str(config.virtual_network),
            "--subnet",
            str(config.subnet),
            "--nsg",
            "",
            "--public-ip-address",
            "",
            "--output",
            "json",
        ]
        if len(image_tokens) == 4:
            command.extend(["--image", image_reference])
        else:
            command.extend(["--image", image_reference or "Ubuntu2204"])
        ssh_key_path = str(getattr(config, "ssh_public_key_path", "") or "").strip()
        if ssh_key_path:
            command.extend(["--ssh-key-values", ssh_key_path])
        if getattr(config, "tags", None) or tags:
            merged_tags = dict(getattr(config, "tags", {}) or {})
            merged_tags.update(dict(tags or {}))
            if merged_tags:
                command.extend(
                    ["--tags", *[f"{key}={value}" for key, value in sorted(merged_tags.items())]]
                )
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        if completed.returncode != 0:
            raise RuntimeError(
                (completed.stderr or completed.stdout or "").strip() or "az vm create failed"
            )
        import json

        payload = json.loads(completed.stdout or "{}")
        return payload if isinstance(payload, dict) else {}

    def start_vm(self, *, config: Any, provider_id: str) -> dict[str, Any]:
        command = self._base_command(config) + [
            "vm",
            "start",
            "--resource-group",
            str(config.resource_group),
            "--name",
            provider_id,
            "--output",
            "json",
        ]
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        if completed.returncode != 0:
            raise RuntimeError(
                (completed.stderr or completed.stdout or "").strip() or "az vm start failed"
            )
        return {"provider_id": provider_id}

    def stop_vm(self, *, config: Any, provider_id: str) -> dict[str, Any]:
        command = self._base_command(config) + [
            "vm",
            "deallocate",
            "--resource-group",
            str(config.resource_group),
            "--name",
            provider_id,
            "--output",
            "json",
        ]
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        if completed.returncode != 0:
            raise RuntimeError(
                (completed.stderr or completed.stdout or "").strip() or "az vm deallocate failed"
            )
        return {"provider_id": provider_id}

    def resolve_private_ip(self, *, config: Any, provider_id: str) -> str | None:
        command = self._base_command(config) + [
            "vm",
            "show",
            "-d",
            "--resource-group",
            str(config.resource_group),
            "--name",
            provider_id,
            "--query",
            "privateIps",
            "--output",
            "tsv",
        ]
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        if completed.returncode != 0:
            raise RuntimeError(
                (completed.stderr or completed.stdout or "").strip() or "az vm show failed"
            )
        return str(completed.stdout or "").strip() or None

    @staticmethod
    def _base_command(config: Any) -> list[str]:
        command = ["az"]
        subscription = str(getattr(config, "subscription", "") or "").strip()
        if subscription:
            command.extend(["account", "set", "--subscription", subscription])
        return command


@dataclass
class VmProviderContext:
    dependencies: Any


class VmProvider:
    name = "base"

    def __init__(self, context: VmProviderContext) -> None:
        self.context = context

    def list_vms(self, **kwargs: Any) -> list[VmSummary]:
        raise NotImplementedError

    def create_vm(self, **kwargs: Any) -> VmActionResult:
        raise NotImplementedError

    def start_vm(self, **kwargs: Any) -> VmActionResult:
        raise NotImplementedError

    def shutdown_vm(self, **kwargs: Any) -> VmActionResult:
        raise NotImplementedError

    def vm_exec(self, **kwargs: Any) -> dict[str, Any]:
        raise NotImplementedError

    def vm_copy_files(self, **kwargs: Any) -> dict[str, Any]:
        raise NotImplementedError

    def vm_delete_files(self, **kwargs: Any) -> dict[str, Any]:
        raise NotImplementedError


class LibvirtVmProvider(VmProvider):
    name = "libvirt"

    def list_vms(
        self,
        *,
        include_inactive: bool = True,
        omit_state: bool = False,
        vm_user: str = "agent",
        directory_path: str | None = None,
        direct_only: bool = False,
    ) -> list[VmSummary]:
        deps = self.context.dependencies
        session_repository = _runtime_session_repository(deps.runtime)
        session_ids_by_vm = _session_ids_by_vm(session_repository.list_sessions())
        labels_by_vm = _labels_by_vm(session_repository.list_sessions())
        directory_paths_by_vm = _directory_paths_by_vm(session_repository.list_sessions())
        normalized_directory_path = deps.normalize_directory_path(directory_path)
        owned_vm_ids = (
            deps.direct_owned_entity_ids(normalized_directory_path, kind="vm")
            if direct_only
            else set()
        )
        vm_names = admin_service_module._virsh_lines(
            ["list", "--all" if include_inactive else "--state-running", "--name"]
        )
        summaries: list[VmSummary] = []
        for vm_name in vm_names:
            vm_directory_paths = directory_paths_by_vm.get(vm_name, [])
            if direct_only:
                if vm_name not in owned_vm_ids and not (
                    normalized_directory_path == "root" and not vm_directory_paths
                ):
                    continue
            elif directory_path is not None and normalized_directory_path not in vm_directory_paths:
                continue
            provider_state = admin_service_module._dominfo_field(vm_name, "State") or "unknown"
            power_state = normalize_power_state(provider_state)
            host_control_status = deps.agent_control_store.status_for_vm(vm_name)
            ip_address = (
                admin_service_module._resolve_vm_ip(vm_name) if power_state == "running" else None
            )
            if power_state != "running":
                agent_state = "disabled" if host_control_status == "disabled" else "inactive_vm"
                agent_pid = None
                repo_names: list[str] = []
                updated_at = None
                dispatch_id = None
            elif omit_state or not ip_address:
                agent_state = (
                    "disabled"
                    if host_control_status == "disabled"
                    else ("unknown" if not ip_address else "omitted")
                )
                agent_pid = None
                repo_names = []
                updated_at = None
                dispatch_id = None
            else:
                agent_state, repo_names, updated_at, dispatch_id, agent_pid = (
                    admin_service_module._probe_agent_state(vm_name, ip_address, vm_user)
                )
            summaries.append(
                VmSummary(
                    vm_name=vm_name,
                    provider=self.name,
                    issue_number=admin_service_module._extract_issue_number(vm_name),
                    session_ids=session_ids_by_vm.get(vm_name, []),
                    labels=labels_by_vm.get(vm_name, {}),
                    directory_paths=vm_directory_paths,
                    power_state=power_state,
                    provider_state=provider_state,
                    ip_address=ip_address,
                    agent_state=agent_state,
                    agent_pid=agent_pid
                    if power_state == "running" and not omit_state and ip_address
                    else None,
                    agent_repo_names=repo_names,
                    last_state_update_at=updated_at,
                    dispatch_id=dispatch_id
                    if power_state == "running" and not omit_state and ip_address
                    else None,
                )
            )
        return summaries

    def create_vm(self, **kwargs: Any) -> VmActionResult:
        return _libvirt_create_vm(self.context.dependencies, **kwargs).model_copy(
            update={"provider": self.name}
        )

    def start_vm(self, **kwargs: Any) -> VmActionResult:
        result = _libvirt_start_vm(kwargs["target"])
        return result.model_copy(update={"provider": self.name})

    def shutdown_vm(self, **kwargs: Any) -> VmActionResult:
        result = _libvirt_shutdown_vm(
            kwargs["target"],
            timeout_seconds=int(kwargs.get("timeout_seconds", 60)),
        )
        return result.model_copy(update={"provider": self.name})

    def vm_exec(self, **kwargs: Any) -> dict[str, Any]:
        result = dict(
            _libvirt_vm_exec(
                kwargs["target"],
                session_repository=_runtime_session_repository(self.context.dependencies.runtime),
                command=kwargs["command"],
                vm_user=kwargs.get("vm_user", "agent"),
                working_directory=kwargs.get("working_directory"),
                environment=kwargs.get("environment"),
                stdin=kwargs.get("stdin"),
                timeout_seconds=kwargs.get("timeout_seconds"),
                check=bool(kwargs.get("check", True)),
            )
        )
        result.setdefault("provider", self.name)
        return result

    def vm_copy_files(self, **kwargs: Any) -> dict[str, Any]:
        result = dict(
            _libvirt_vm_copy_files(
                kwargs["target"],
                session_repository=_runtime_session_repository(self.context.dependencies.runtime),
                files=kwargs["files"],
                vm_user=kwargs.get("vm_user", "agent"),
            )
        )
        result.setdefault("provider", self.name)
        return result

    def vm_delete_files(self, **kwargs: Any) -> dict[str, Any]:
        result = dict(
            _libvirt_vm_delete_files(
                kwargs["target"],
                session_repository=_runtime_session_repository(self.context.dependencies.runtime),
                paths=kwargs["paths"],
                vm_user=kwargs.get("vm_user", "agent"),
                recursive=bool(kwargs.get("recursive", False)),
                missing_ok=bool(kwargs.get("missing_ok", True)),
            )
        )
        result.setdefault("provider", self.name)
        return result


class AzureVmProvider(VmProvider):
    name = "azure"

    def __init__(
        self, context: VmProviderContext, *, client: AzureVmControlClientProtocol | None = None
    ) -> None:
        super().__init__(context)
        self._client = client or AzureVmControlClient()

    def list_vms(
        self,
        *,
        include_inactive: bool = True,
        omit_state: bool = False,
        vm_user: str = "agent",
        directory_path: str | None = None,
        direct_only: bool = False,
    ) -> list[VmSummary]:
        del vm_user, omit_state
        deps = self.context.dependencies
        vm_defaults = deps.resolve_vm_defaults(deps.normalize_directory_path(directory_path))
        azure_config = getattr(vm_defaults, "azure", None)
        if (
            azure_config is None
            or not str(getattr(azure_config, "resource_group", "") or "").strip()
        ):
            return []
        session_repository = _runtime_session_repository(deps.runtime)
        session_ids_by_vm = _session_ids_by_vm(
            session_repository.list_sessions(), provider=self.name
        )
        labels_by_vm = _labels_by_vm(session_repository.list_sessions(), provider=self.name)
        directory_paths_by_vm = _directory_paths_by_vm(
            session_repository.list_sessions(), provider=self.name
        )
        normalized_directory_path = deps.normalize_directory_path(directory_path)
        owned_vm_ids = (
            deps.direct_owned_entity_ids(normalized_directory_path, kind="vm")
            if direct_only
            else set()
        )
        summaries: list[VmSummary] = []
        for item in self._client.list_vms(config=azure_config, include_inactive=include_inactive):
            vm_name = str(item.get("name") or item.get("vm_name") or "").strip()
            if not vm_name:
                continue
            vm_directory_paths = directory_paths_by_vm.get(vm_name, [])
            if direct_only:
                if vm_name not in owned_vm_ids and not (
                    normalized_directory_path == "root" and not vm_directory_paths
                ):
                    continue
            elif directory_path is not None and normalized_directory_path not in vm_directory_paths:
                continue
            provider_state = (
                str(
                    item.get("powerState") or item.get("provider_state") or item.get("status") or ""
                ).strip()
                or None
            )
            power_state = normalize_power_state(provider_state)
            ip_address = (
                str(
                    item.get("privateIps") or item.get("private_ip") or item.get("ip_address") or ""
                ).strip()
                or None
            )
            if power_state != "running":
                ip_address = None
            summaries.append(
                VmSummary(
                    vm_name=vm_name,
                    provider=self.name,
                    issue_number=admin_service_module._extract_issue_number(vm_name),
                    session_ids=session_ids_by_vm.get(vm_name, []),
                    labels=labels_by_vm.get(vm_name, {}),
                    directory_paths=vm_directory_paths,
                    power_state=power_state,
                    provider_state=provider_state,
                    provider_details=item if isinstance(item, dict) else {},
                    ip_address=ip_address,
                    agent_state="omitted"
                    if power_state == "running" and ip_address
                    else "inactive_vm",
                )
            )
        return summaries

    def create_vm(
        self,
        *,
        vm_name: str,
        vm_user: str = "agent",
        labels: Mapping[str, str] | None = None,
        directory_path: str = "root",
        **_: Any,
    ) -> VmActionResult:
        deps = self.context.dependencies
        vm_defaults = deps.resolve_vm_defaults(directory_path)
        azure_config = getattr(vm_defaults, "azure", None)
        if (
            azure_config is None
            or not str(getattr(azure_config, "resource_group", "") or "").strip()
        ):
            return VmActionResult(
                success=False,
                vm_name=vm_name,
                action="create_vm",
                message="Azure VM provider is not configured.",
                provider=self.name,
            )
        cloud_init = self._render_cloud_init(
            vm_name=vm_name, vm_user=vm_user, vm_defaults=vm_defaults
        )
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            delete=False,
            prefix="agent-sandbox-azure-cloud-init-",
            suffix=".yaml",
        ) as handle:
            handle.write(cloud_init)
            temp_path = handle.name
        try:
            response = self._client.create_vm(
                config=azure_config,
                vm_name=vm_name,
                vm_user=vm_user,
                cloud_init=temp_path,
                tags=labels,
            )
        finally:
            try:
                os.unlink(temp_path)
            except FileNotFoundError:
                pass
        provider_id = str(response.get("id") or response.get("name") or vm_name).strip() or vm_name
        return VmActionResult(
            success=True,
            vm_name=vm_name,
            action="create_vm",
            message=f"Created Azure VM {vm_name}",
            provider=self.name,
            provider_operation_id=provider_id,
            provider_details=response if isinstance(response, dict) else {},
        )

    def start_vm(
        self, *, vm_name: str, session: Any | None = None, directory_path: str = "root", **_: Any
    ) -> VmActionResult:
        deps = self.context.dependencies
        vm_defaults = deps.resolve_vm_defaults(directory_path)
        azure_config = getattr(vm_defaults, "azure", None)
        provider_id = session_vm_provider_id(session, fallback_vm_name=vm_name) or vm_name
        self._client.start_vm(config=azure_config, provider_id=provider_id)
        return VmActionResult(
            success=True,
            vm_name=vm_name,
            action="start",
            message=f"Started VM {vm_name}",
            provider=self.name,
            provider_operation_id=provider_id,
        )

    def shutdown_vm(
        self,
        *,
        vm_name: str,
        session: Any | None = None,
        directory_path: str = "root",
        timeout_seconds: int = 60,
        **_: Any,
    ) -> VmActionResult:
        del timeout_seconds
        deps = self.context.dependencies
        vm_defaults = deps.resolve_vm_defaults(directory_path)
        azure_config = getattr(vm_defaults, "azure", None)
        provider_id = session_vm_provider_id(session, fallback_vm_name=vm_name) or vm_name
        self._client.stop_vm(config=azure_config, provider_id=provider_id)
        return VmActionResult(
            success=True,
            vm_name=vm_name,
            action="shutdown",
            message=f"Shut down VM {vm_name}",
            provider=self.name,
            provider_operation_id=provider_id,
        )

    def vm_exec(
        self,
        *,
        vm_name: str,
        session: Any | None = None,
        vm_user: str = "agent",
        command: str | list[str],
        working_directory: str | None = None,
        environment: dict[str, str] | None = None,
        stdin: str | None = None,
        timeout_seconds: float | None = None,
        check: bool = True,
        directory_path: str = "root",
        **_: Any,
    ) -> dict[str, Any]:
        return _ssh_exec(
            vm_name=vm_name,
            provider="azure",
            vm_ip=self._resolve_ip(vm_name=vm_name, session=session, directory_path=directory_path),
            vm_user=vm_user,
            command=command,
            working_directory=working_directory,
            environment=environment,
            stdin=stdin,
            timeout_seconds=timeout_seconds,
            check=check,
        )

    def vm_copy_files(
        self,
        *,
        target: str,
        vm_name: str,
        session: Any | None = None,
        vm_user: str = "agent",
        files: list[dict[str, Any]],
        directory_path: str = "root",
        **_: Any,
    ) -> dict[str, Any]:
        return _ssh_copy_files(
            target=target,
            provider="azure",
            vm_name=vm_name,
            vm_ip=self._resolve_ip(vm_name=vm_name, session=session, directory_path=directory_path),
            vm_user=vm_user,
            files=files,
        )

    def vm_delete_files(
        self,
        *,
        target: str,
        vm_name: str,
        session: Any | None = None,
        vm_user: str = "agent",
        paths: list[str],
        recursive: bool = False,
        missing_ok: bool = True,
        directory_path: str = "root",
        **_: Any,
    ) -> dict[str, Any]:
        return _ssh_delete_files(
            target=target,
            provider="azure",
            vm_name=vm_name,
            vm_ip=self._resolve_ip(vm_name=vm_name, session=session, directory_path=directory_path),
            vm_user=vm_user,
            paths=paths,
            recursive=recursive,
            missing_ok=missing_ok,
        )

    def _resolve_ip(self, *, vm_name: str, session: Any | None, directory_path: str) -> str:
        deps = self.context.dependencies
        vm_defaults = deps.resolve_vm_defaults(directory_path)
        azure_config = getattr(vm_defaults, "azure", None)
        provider_id = session_vm_provider_id(session, fallback_vm_name=vm_name) or vm_name
        ip_address = self._client.resolve_private_ip(config=azure_config, provider_id=provider_id)
        if not ip_address:
            raise RuntimeError(f"Unable to resolve VM IP for {vm_name!r}")
        return ip_address

    @staticmethod
    def _render_cloud_init(*, vm_name: str, vm_user: str, vm_defaults: Any) -> str:
        guest_environment = getattr(vm_defaults, "guest_environment")()
        azure_config = getattr(vm_defaults, "azure", None)
        ssh_public_key = _resolve_azure_agent_admin_ssh_public_key(azure_config)
        lines = [
            "#cloud-config",
            "users:",
            f"  - name: {vm_user}",
            "    shell: /bin/bash",
            "  - name: agent-admin",
            "    groups: [sudo]",
            "    sudo: ALL=(ALL) NOPASSWD:ALL",
            "    shell: /bin/bash",
            "packages:",
            "  - openssh-server",
            "write_files:",
            "  - path: /etc/agent-sandbox/session.env",
            "    permissions: '0644'",
            "    content: |",
        ]
        if ssh_public_key:
            lines[
                lines.index("    shell: /bin/bash") + 1 : lines.index("  - name: agent-admin")
            ] = [
                "    ssh_authorized_keys:",
                f"      - {ssh_public_key}",
            ]
            admin_shell_index = lines.index(
                "    shell: /bin/bash", lines.index("  - name: agent-admin")
            )
            lines[admin_shell_index + 1 : admin_shell_index + 1] = [
                "    ssh_authorized_keys:",
                f"      - {ssh_public_key}",
            ]
        for key, value in sorted(guest_environment.items()):
            lines.append(f"      {key}={value}")
        lines.extend(
            [
                "runcmd:",
                f"  - mkdir -p /home/{vm_user}/.agent",
                f"  - install -d -m 0700 -o {vm_user} -g {vm_user} /home/{vm_user}/.agent-sandbox",
                "  - install -d -m 0700 -o root -g root /var/log/agent-sandbox/agent-runs",
                f"  - chown -R {vm_user}:{vm_user} /home/{vm_user}",
                f"  - [ bash, -lc, 'echo Azure VM bootstrap prepared for {vm_name}' ]",
            ]
        )
        return "\n".join(lines) + "\n"


class VmProviderRegistry:
    def __init__(self, dependencies: Any) -> None:
        context = VmProviderContext(dependencies=dependencies)
        self._providers: dict[str, VmProvider] = {
            "libvirt": LibvirtVmProvider(context),
            "azure": AzureVmProvider(context),
        }

    def get(self, name: str | None) -> VmProvider:
        normalized = str(name or "libvirt").strip().lower() or "libvirt"
        provider = self._providers.get(normalized)
        if provider is None:
            raise ValueError(f"Unsupported VM provider: {name}")
        return provider

    def list_vms(self, **kwargs: Any) -> list[VmSummary]:
        requested_provider = str(kwargs.pop("provider", "") or "").strip().lower() or None
        provider_names = [requested_provider] if requested_provider else list(self._providers)
        summaries: list[VmSummary] = []
        for provider_name in provider_names:
            summaries.extend(self.get(provider_name).list_vms(**kwargs))
        return summaries

    def resolve_target(
        self,
        *,
        target: str,
        session_repository: SessionManifestRepository,
        directory_path: str = "root",
    ) -> tuple[VmProvider, str, Any | None]:
        session = session_repository.get_session(target)
        if session is not None:
            vm_name = str(getattr(session, "vm_id", "") or "").strip()
            if not vm_name:
                raise ValueError(f"Session {target!r} does not have an associated VM")
            return self.get(session_vm_provider(session)), vm_name, session
        return self.get("libvirt"), target, None


def _libvirt_create_vm(
    deps: Any,
    *,
    vm_name: str,
    session_id: str | None = None,
    labels: dict[str, str] | None = None,
    model: str | None = None,
    vm_user: str = "agent",
    ttl_hours: int | None = None,
    allowed_tools: list[str | Any] | None = None,
    allowed_script_names: list[str] | None = None,
    discord_channels: list[str] | None = None,
    teams_channel_allowlist: list[str] | None = None,
    teams_user_allowlist: list[str] | None = None,
    email_mailboxes: list[str] | None = None,
    email_folders: list[str] | None = None,
    communication_file_receive_enabled: bool | None = None,
    communication_file_send_enabled: bool | None = None,
    communication_file_max_bytes: int | None = None,
    communication_file_require_approval_for_send: bool | None = None,
    dotnet: bool = False,
    nuget: bool = False,
    mssql: bool = False,
    mssql_init_root: str | None = None,
    playwright: bool = False,
    codex_skills: list[str] | None = None,
    directory_path: str = "root",
    provider: str | None = None,
    allow_existing_session: bool = False,
) -> VmActionResult:
    resolved_vm_name = vm_name.strip()
    if not resolved_vm_name:
        return VmActionResult(
            success=False, vm_name="", action="create_vm", message="VM name is required."
        )
    if admin_service_module._dominfo_field(resolved_vm_name, "State") is not None:
        return VmActionResult(
            success=False,
            vm_name=resolved_vm_name,
            action="create_vm",
            message=f"VM already exists: {resolved_vm_name}",
        )

    normalized_directory_path = deps.normalize_directory_path(directory_path)
    policy = deps.runtime.policy_config.resolve_for_directory(normalized_directory_path)
    resolved_session_id = (
        session_id.strip()
        if session_id and session_id.strip()
        else admin_service_module._default_vm_session_id(resolved_vm_name)
    )
    session_repository = _runtime_session_repository(deps.runtime)
    existing_session = session_repository.get_session(resolved_session_id)
    had_existing_session = existing_session is not None
    if existing_session is not None and not allow_existing_session:
        return VmActionResult(
            success=False,
            vm_name=resolved_vm_name,
            action="create_vm",
            message=f"Session already exists: {resolved_session_id}",
        )
    normalized_codex_skills = [
        item.strip() for item in (codex_skills or []) if item and item.strip()
    ]
    if mssql and not os.environ.get("MSSQL_SA_PASSWORD", "").strip():
        return VmActionResult(
            success=False,
            vm_name=resolved_vm_name,
            action="create_vm",
            message="MSSQL_SA_PASSWORD must be set when MSSQL bootstrap is enabled.",
        )

    if existing_session is not None:
        manifest = existing_session.model_copy(
            update={
                "vm_id": resolved_vm_name,
                "vm_provider": provider or "libvirt",
                "vm_provider_id": resolved_vm_name,
                "directory_path": normalized_directory_path,
            }
        )
    else:
        manifest = admin_service_module._build_issue_session_manifest(
            policy=policy,
            session_id=resolved_session_id,
            vm_id=resolved_vm_name,
            issue_number=None,
            directory_path=normalized_directory_path,
            ttl_hours=ttl_hours,
            extra_allowed_tools=allowed_tools,
            allowed_script_names=allowed_script_names,
            discord_channels=discord_channels or [],
            teams_channel_allowlist=teams_channel_allowlist or [],
            teams_user_allowlist=teams_user_allowlist or [],
            email_mailboxes=email_mailboxes or [],
            email_folders=email_folders or [],
            communication_file_receive_enabled=communication_file_receive_enabled,
            communication_file_send_enabled=communication_file_send_enabled,
            communication_file_max_bytes=communication_file_max_bytes,
            communication_file_require_approval_for_send=(
                communication_file_require_approval_for_send
            ),
        )
    normalized_labels = admin_service_module._normalize_session_labels(labels or {})
    if normalized_labels:
        manifest.metadata = dict(manifest.metadata)
        manifest.metadata["labels"] = normalized_labels
    manifest.vm_provider = provider or "libvirt"
    manifest.vm_provider_id = resolved_vm_name
    normalized_model = (model or "").strip() or None
    if normalized_model is not None:
        manifest.model = normalized_model
    else:
        try:
            manifest.model = deps.resolve_agent_model_for_session(manifest)
        except ValueError:
            manifest.model = None
    session_repository.upsert_session(manifest)

    root_dir = Path(__file__).resolve().parents[3]
    vm_defaults = deps.resolve_vm_defaults(normalized_directory_path)
    setup_script = root_dir / "scripts" / "setup_vm.sh"
    run_env = os.environ.copy()
    run_env.update(vm_defaults.guest_environment())
    if manifest.model:
        run_env["AGENT_MODEL"] = manifest.model
    managed_skills_root = _managed_codex_skills_root(deps, normalized_codex_skills)
    if managed_skills_root is not None:
        run_env["HOST_REPO_CODEX_SKILLS_DIR"] = str(managed_skills_root)

    try:
        helper = deps.get_privileged_helper() if deps.get_use_privileged_helper() else None
        if helper is not None and not helper.is_running():
            helper.start()
        setup_args: list[str] = []
        if dotnet:
            setup_args.append("--dotnet")
        if nuget:
            setup_args.append("--nuget")
        if mssql:
            setup_args.append("--mssql")
            if mssql_init_root is not None:
                setup_args.extend(["--mssql-init-root", mssql_init_root])
        if playwright:
            setup_args.append("--playwright")
        for skill_name in normalized_codex_skills:
            setup_args.extend(["--codex-skill", skill_name])
        setup_args.extend([resolved_vm_name, resolved_session_id, vm_user])
        if helper is not None:
            response = helper.call(
                "run_script", script_path=str(setup_script), args=setup_args, env=run_env
            )
            message = admin_service_module._combined_process_message(
                str(response.get("stdout") or ""),
                str(response.get("stderr") or ""),
            )
            completed = type(
                "Completed", (), {"returncode": int(response.get("returncode") or 0)}
            )()
        else:
            allowed_env_keys = (
                "SSH_PUBLIC_KEY_FILE",
                "SSH_PRIVATE_KEY_PATH",
                "GATEWAY_URL",
                "LLM_BASE_URL",
                "AGENT_MODEL",
                "NUGET_RELAY_BASE_URL",
                "AGENT_SANDBOX_RUNTIME_DIR",
                "VM_STORAGE_DIR",
                "SHARED_NUGET_CACHE_DIR",
                "SHARED_NUGET_CACHE_TAG",
                "SHARED_NUGET_CACHE_MODE",
                "RAM_MB",
                "VCPUS",
                "DISK_SIZE_GB",
                "VM_WAIT_IP_TIMEOUT_SECONDS",
                "VM_WAIT_SSH_TIMEOUT_SECONDS",
                "MSSQL_SA_PASSWORD",
                "HOST_REPO_CODEX_SKILLS_DIR",
            )
            launch_step = admin_service_module._sudo_env_command(
                [str(setup_script), *setup_args], run_env, allowed_env_keys
            )
            completed = admin_service_module._run_subprocess(
                launch_step, capture_output=True, text=True, env=run_env
            )
            message = admin_service_module._combined_process_message(
                completed.stdout, completed.stderr
            )
            if completed.returncode != 0 and admin_service_module._can_prompt_for_privileges():
                launch_step = admin_service_module._pkexec_env_command(
                    [str(setup_script), *setup_args], run_env, allowed_env_keys
                )
                completed = admin_service_module._run_subprocess(
                    launch_step, capture_output=True, text=True, env=run_env
                )
                message = admin_service_module._combined_process_message(
                    completed.stdout, completed.stderr
                )
            if completed.returncode != 0:
                raise RuntimeError(
                    message or f"{setup_script.name} exited with code {completed.returncode}"
                )
    except Exception as exc:
        preserved_vm = admin_service_module._dominfo_field(resolved_vm_name, "State") is not None
        if not preserved_vm and not had_existing_session:
            session_repository.delete_session(resolved_session_id)
        detail = f"Failed to create VM {resolved_vm_name}: {exc}"
        if preserved_vm:
            detail = f"{detail}\nVM {resolved_vm_name} and session {resolved_session_id} were preserved for inspection."
        return VmActionResult(
            success=False, vm_name=resolved_vm_name, action="create_vm", message=detail
        )

    summary = message or f"Created VM {resolved_vm_name}"
    return VmActionResult(
        success=True, vm_name=resolved_vm_name, action="create_vm", message=summary
    )


def _libvirt_start_vm(target: str) -> VmActionResult:
    vm_name = admin_service_module._resolve_vm_name(target)
    current_state = admin_service_module._dominfo_field(vm_name, "State")
    if current_state is None:
        return VmActionResult(
            success=False, vm_name=vm_name, action="start", message=f"VM not found: {vm_name}"
        )
    if current_state == "running":
        return VmActionResult(
            success=True, vm_name=vm_name, action="start", message=f"VM already running: {vm_name}"
        )
    result = admin_service_module._run_virsh(["start", vm_name])
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        return VmActionResult(
            success=False,
            vm_name=vm_name,
            action="start",
            message=stderr or f"Failed to start {vm_name}",
        )
    return VmActionResult(
        success=True, vm_name=vm_name, action="start", message=f"Started VM {vm_name}"
    )


def _libvirt_shutdown_vm(target: str, *, timeout_seconds: int = 60) -> VmActionResult:
    vm_name = admin_service_module._resolve_vm_name(target)
    current_state = admin_service_module._dominfo_field(vm_name, "State")
    if current_state is None:
        return VmActionResult(
            success=False, vm_name=vm_name, action="shutdown", message=f"VM not found: {vm_name}"
        )
    if current_state != "running":
        return VmActionResult(
            success=True,
            vm_name=vm_name,
            action="shutdown",
            message=f"VM already stopped: {vm_name}",
        )
    result = admin_service_module._run_virsh(["shutdown", vm_name])
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        return VmActionResult(
            success=False,
            vm_name=vm_name,
            action="shutdown",
            message=stderr or f"Failed to request shutdown for {vm_name}",
        )
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        state = admin_service_module._dominfo_field(vm_name, "State")
        if state and state != "running":
            return VmActionResult(
                success=True, vm_name=vm_name, action="shutdown", message=f"Shut down VM {vm_name}"
            )
        time.sleep(2)
    return VmActionResult(
        success=False,
        vm_name=vm_name,
        action="shutdown",
        message=f"Timed out waiting for {vm_name} to shut down after {timeout_seconds}s",
    )


def _libvirt_vm_exec(
    target: str,
    *,
    session_repository: SessionManifestRepository,
    command: str | list[str],
    vm_user: str = "agent",
    working_directory: str | None = None,
    environment: dict[str, str] | None = None,
    stdin: str | None = None,
    timeout_seconds: float | None = None,
    check: bool = True,
) -> dict[str, Any]:
    vm_name = session_repository.resolve_vm_name_from_target(target)
    vm_ip, ssh_private_key_path = admin_service_module._resolve_vm_ssh_access(vm_name, vm_user)
    remote_script = admin_service_module._remote_shell_script(
        command=command, working_directory=working_directory, environment=environment
    )
    completed = admin_service_module._run_subprocess(
        [
            "ssh",
            *admin_service_module._ssh_options(vm_name, vm_ip, ssh_private_key_path),
            f"{vm_user}@{vm_ip}",
            "bash",
            "-s",
        ],
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        input=f"{remote_script}{stdin or ''}",
    )
    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    success = completed.returncode == 0 or not check
    message = (stderr or stdout).strip() or f"vm_exec exited with code {completed.returncode}"
    return {
        "success": success,
        "vm_name": vm_name,
        "action": "vm_exec",
        "message": message,
        "exit_code": completed.returncode,
        "stdout": stdout,
        "stderr": stderr,
    }


def _libvirt_vm_copy_files(
    target: str,
    *,
    session_repository: SessionManifestRepository,
    files: list[dict[str, Any]],
    vm_user: str = "agent",
) -> dict[str, Any]:
    vm_name = session_repository.resolve_vm_name_from_target(target)
    vm_ip, ssh_private_key_path = admin_service_module._resolve_vm_ssh_access(vm_name, vm_user)
    copied_paths: list[str] = []
    for index, file_spec in enumerate(files):
        destination_path = str(file_spec.get("destination_path") or "").strip()
        if not destination_path:
            raise ValueError(f"files[{index}] destination_path is required")
        source_path_raw = file_spec.get("source_path")
        content_raw = file_spec.get("content")
        if bool(source_path_raw) == bool(content_raw):
            raise ValueError(f"files[{index}] must provide exactly one of source_path or content")
        overwrite = bool(file_spec.get("overwrite", True))
        mode = str(file_spec.get("mode") or "").strip()
        parent_path = str(Path(destination_path).parent)
        prelude = ["set -euo pipefail", f"mkdir -p {shlex.quote(parent_path)}"]
        if not overwrite:
            prelude.append(f"if [ -e {shlex.quote(destination_path)} ]; then exit 17; fi")
        prep_script = "\n".join(prelude) + "\n"
        if source_path_raw:
            source_path = Path(str(source_path_raw)).expanduser().resolve()
            if not source_path.exists():
                raise ValueError(f"files[{index}] source_path does not exist: {source_path}")
            completed = admin_service_module._run_subprocess(
                [
                    "ssh",
                    *admin_service_module._ssh_options(vm_name, vm_ip, ssh_private_key_path),
                    f"{vm_user}@{vm_ip}",
                    "bash",
                    "-s",
                ],
                capture_output=True,
                text=True,
                input=prep_script,
            )
            if completed.returncode != 0:
                raise RuntimeError(
                    (completed.stderr or completed.stdout or "").strip()
                    or "remote file prep failed"
                )
            scp_args = ["scp"]
            if source_path.is_dir():
                scp_args.append("-r")
            scp_result = admin_service_module._run_subprocess(
                [
                    *scp_args,
                    *admin_service_module._ssh_options(vm_name, vm_ip, ssh_private_key_path),
                    str(source_path),
                    f"{vm_user}@{vm_ip}:{destination_path}",
                ],
                capture_output=True,
                text=True,
            )
            if scp_result.returncode != 0:
                raise RuntimeError(
                    (scp_result.stderr or scp_result.stdout or "").strip() or "scp failed"
                )
            if mode:
                chmod_args = ["chmod"]
                if source_path.is_dir():
                    chmod_args.append("-R")
                chmod_args.extend([mode, destination_path])
                chmod_result = admin_service_module._run_subprocess(
                    [
                        "ssh",
                        *admin_service_module._ssh_options(vm_name, vm_ip, ssh_private_key_path),
                        f"{vm_user}@{vm_ip}",
                        *chmod_args,
                    ],
                    capture_output=True,
                    text=True,
                )
                if chmod_result.returncode != 0:
                    raise RuntimeError(
                        (chmod_result.stderr or chmod_result.stdout or "").strip() or "chmod failed"
                    )
        else:
            content_text = str(content_raw if content_raw is not None else "")
            encoded_content = base64.b64encode(content_text.encode("utf-8")).decode("ascii")
            remote_script = (
                "\n".join(
                    prelude
                    + [
                        f"python3 - <<'PY'\nimport base64\nfrom pathlib import Path\nPath({destination_path!r}).write_bytes(base64.b64decode({encoded_content!r}))\nPY"
                    ]
                    + (
                        [f"chmod {shlex.quote(mode)} {shlex.quote(destination_path)}"]
                        if mode
                        else []
                    )
                )
                + "\n"
            )
            completed = admin_service_module._run_subprocess(
                [
                    "ssh",
                    *admin_service_module._ssh_options(vm_name, vm_ip, ssh_private_key_path),
                    f"{vm_user}@{vm_ip}",
                    "bash",
                    "-s",
                ],
                capture_output=True,
                text=True,
                input=remote_script,
            )
            if completed.returncode != 0:
                if completed.returncode == 17:
                    raise RuntimeError(f"Destination already exists: {destination_path}")
                raise RuntimeError(
                    (completed.stderr or completed.stdout or "").strip()
                    or "remote content write failed"
                )
        copied_paths.append(destination_path)
    return {
        "success": True,
        "vm_name": vm_name,
        "action": "vm_copy_files",
        "message": f"Copied {len(copied_paths)} path(s) into {vm_name}",
        "copied_count": len(copied_paths),
        "copied_paths": copied_paths,
    }


def _libvirt_vm_delete_files(
    target: str,
    *,
    session_repository: SessionManifestRepository,
    paths: list[str],
    vm_user: str = "agent",
    recursive: bool = False,
    missing_ok: bool = True,
) -> dict[str, Any]:
    if not paths:
        raise ValueError("paths must not be empty")
    vm_name = session_repository.resolve_vm_name_from_target(target)
    vm_ip, ssh_private_key_path = admin_service_module._resolve_vm_ssh_access(vm_name, vm_user)
    normalized_paths: list[str] = []
    for index, raw_path in enumerate(paths):
        path = str(raw_path or "").strip()
        if not path:
            raise ValueError(f"paths[{index}] must be a non-empty string")
        if path.startswith("~") or not path.startswith("/"):
            raise ValueError(f"paths[{index}] must be an absolute guest path")
        normalized_paths.append(PurePosixPath(path).as_posix())
    rm_flag = "-rf" if recursive else "-f"
    commands = ["set -euo pipefail"]
    if not missing_ok:
        commands.extend(f"test -e {shlex.quote(path)}" for path in normalized_paths)
    commands.extend(f"rm {rm_flag} -- {shlex.quote(path)}" for path in normalized_paths)
    completed = admin_service_module._run_subprocess(
        [
            "ssh",
            *admin_service_module._ssh_options(vm_name, vm_ip, ssh_private_key_path),
            f"{vm_user}@{vm_ip}",
            "bash",
            "-s",
        ],
        capture_output=True,
        text=True,
        input="\n".join(commands) + "\n",
    )
    if completed.returncode != 0:
        raise RuntimeError(
            (completed.stderr or completed.stdout or "").strip() or "remote delete failed"
        )
    return {
        "success": True,
        "vm_name": vm_name,
        "action": "vm_delete_files",
        "message": f"Deleted {len(normalized_paths)} path(s) from {vm_name}",
        "deleted_count": len(normalized_paths),
        "deleted_paths": normalized_paths,
        "vm_user": vm_user,
    }


def _session_ids_by_vm(sessions: list[Any], *, provider: str | None = None) -> dict[str, list[str]]:
    mapping: dict[str, list[str]] = {}
    for session in sessions:
        session_id = str(getattr(session, "session_id", "") or "").strip()
        vm_id = str(getattr(session, "vm_id", "") or "").strip()
        if not vm_id:
            continue
        if provider is not None and session_vm_provider(session) != provider:
            continue
        mapping.setdefault(vm_id, []).append(session_id)
    return {vm_id: sorted(session_ids) for vm_id, session_ids in mapping.items()}


def _labels_by_vm(sessions: list[Any], *, provider: str | None = None) -> dict[str, dict[str, str]]:
    mapping: dict[str, dict[str, str]] = {}
    for session in sessions:
        vm_id = str(getattr(session, "vm_id", "") or "").strip()
        if not vm_id:
            continue
        if provider is not None and session_vm_provider(session) != provider:
            continue
        labels = admin_service_module._normalize_session_labels(
            getattr(session, "metadata", {}).get("labels")
        )
        current = mapping.get(vm_id)
        if current is None:
            mapping[vm_id] = dict(labels)
            continue
        mapping[vm_id] = {key: value for key, value in current.items() if labels.get(key) == value}
    return {vm_id: labels for vm_id, labels in mapping.items() if labels}


def _directory_paths_by_vm(
    sessions: list[Any], *, provider: str | None = None
) -> dict[str, list[str]]:
    mapping: dict[str, set[str]] = {}
    for session in sessions:
        vm_id = str(getattr(session, "vm_id", "") or "").strip()
        directory_path = str(getattr(session, "directory_path", "") or "").strip()
        if not vm_id or not directory_path:
            continue
        if provider is not None and session_vm_provider(session) != provider:
            continue
        mapping.setdefault(vm_id, set()).add(directory_path)
    return {vm_id: sorted(values) for vm_id, values in mapping.items()}


def _ssh_exec(
    *,
    vm_name: str,
    provider: str,
    vm_ip: str,
    vm_user: str,
    command: str | list[str],
    working_directory: str | None,
    environment: dict[str, str] | None,
    stdin: str | None,
    timeout_seconds: float | None,
    check: bool,
) -> dict[str, Any]:
    ssh_private_key_path = admin_service_module._resolve_private_key_path()
    if ssh_private_key_path is None:
        raise RuntimeError(
            "Missing SSH private key. Set SSH_PRIVATE_KEY_PATH to the VM access key."
        )
    remote_script = admin_service_module._remote_shell_script(
        command=command,
        working_directory=working_directory,
        environment=environment,
    )
    completed = admin_service_module._run_subprocess(
        [
            "ssh",
            *admin_service_module._ssh_options(vm_name, vm_ip, ssh_private_key_path),
            f"{vm_user}@{vm_ip}",
            "bash",
            "-s",
        ],
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        input=f"{remote_script}{stdin or ''}",
    )
    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    success = completed.returncode == 0 or not check
    message = (stderr or stdout).strip() or f"vm_exec exited with code {completed.returncode}"
    return {
        "success": success,
        "vm_name": vm_name,
        "provider": provider,
        "action": "vm_exec",
        "message": message,
        "exit_code": completed.returncode,
        "stdout": stdout,
        "stderr": stderr,
    }


def _ssh_copy_files(
    *,
    target: str,
    provider: str,
    vm_name: str,
    vm_ip: str,
    vm_user: str,
    files: list[dict[str, Any]],
) -> dict[str, Any]:
    ssh_private_key_path = admin_service_module._resolve_private_key_path()
    if ssh_private_key_path is None:
        raise RuntimeError(
            "Missing SSH private key. Set SSH_PRIVATE_KEY_PATH to the VM access key."
        )
    copied_paths: list[str] = []
    for index, file_spec in enumerate(files):
        destination_path = str(file_spec.get("destination_path") or "").strip()
        if not destination_path:
            raise ValueError(f"files[{index}] destination_path is required")
        source_path_raw = file_spec.get("source_path")
        content_raw = file_spec.get("content")
        if bool(source_path_raw) == bool(content_raw):
            raise ValueError(f"files[{index}] must provide exactly one of source_path or content")
        overwrite = bool(file_spec.get("overwrite", True))
        mode = str(file_spec.get("mode") or "").strip()
        parent_path = str(Path(destination_path).parent)
        prelude = ["set -euo pipefail", f"mkdir -p {shlex.quote(parent_path)}"]
        if not overwrite:
            prelude.append(f"if [ -e {shlex.quote(destination_path)} ]; then exit 17; fi")
        prep_script = "\n".join(prelude) + "\n"
        if source_path_raw:
            source_path = Path(str(source_path_raw)).expanduser().resolve()
            if not source_path.exists():
                raise ValueError(f"files[{index}] source_path does not exist: {source_path}")
            completed = admin_service_module._run_subprocess(
                [
                    "ssh",
                    *admin_service_module._ssh_options(vm_name, vm_ip, ssh_private_key_path),
                    f"{vm_user}@{vm_ip}",
                    "bash",
                    "-s",
                ],
                capture_output=True,
                text=True,
                input=prep_script,
            )
            if completed.returncode != 0:
                raise RuntimeError(
                    (completed.stderr or completed.stdout or "").strip()
                    or "remote file prep failed"
                )
            scp_args = ["scp"]
            if source_path.is_dir():
                scp_args.append("-r")
            scp_result = admin_service_module._run_subprocess(
                [
                    *scp_args,
                    *admin_service_module._ssh_options(vm_name, vm_ip, ssh_private_key_path),
                    str(source_path),
                    f"{vm_user}@{vm_ip}:{destination_path}",
                ],
                capture_output=True,
                text=True,
            )
            if scp_result.returncode != 0:
                raise RuntimeError(
                    (scp_result.stderr or scp_result.stdout or "").strip() or "scp failed"
                )
            if mode:
                chmod_args = ["chmod"]
                if source_path.is_dir():
                    chmod_args.append("-R")
                chmod_args.extend([mode, destination_path])
                chmod_result = admin_service_module._run_subprocess(
                    [
                        "ssh",
                        *admin_service_module._ssh_options(vm_name, vm_ip, ssh_private_key_path),
                        f"{vm_user}@{vm_ip}",
                        *chmod_args,
                    ],
                    capture_output=True,
                    text=True,
                )
                if chmod_result.returncode != 0:
                    raise RuntimeError(
                        (chmod_result.stderr or chmod_result.stdout or "").strip() or "chmod failed"
                    )
        else:
            content_text = str(content_raw if content_raw is not None else "")
            encoded_content = base64.b64encode(content_text.encode("utf-8")).decode("ascii")
            remote_script = (
                "\n".join(
                    prelude
                    + [
                        f"python3 - <<'PY'\nimport base64\nfrom pathlib import Path\nPath({destination_path!r}).write_bytes(base64.b64decode({encoded_content!r}))\nPY"
                    ]
                    + (
                        [f"chmod {shlex.quote(mode)} {shlex.quote(destination_path)}"]
                        if mode
                        else []
                    )
                )
                + "\n"
            )
            completed = admin_service_module._run_subprocess(
                [
                    "ssh",
                    *admin_service_module._ssh_options(vm_name, vm_ip, ssh_private_key_path),
                    f"{vm_user}@{vm_ip}",
                    "bash",
                    "-s",
                ],
                capture_output=True,
                text=True,
                input=remote_script,
            )
            if completed.returncode != 0:
                raise RuntimeError(
                    (completed.stderr or completed.stdout or "").strip()
                    or "remote content write failed"
                )
        copied_paths.append(destination_path)
    return {
        "success": True,
        "provider": provider,
        "action": "vm_copy_files",
        "message": f"Copied {len(copied_paths)} path(s) into {vm_name}",
        "target": target,
        "vm_name": vm_name,
        "copied_count": len(copied_paths),
        "copied_paths": copied_paths,
        "vm_user": vm_user,
    }


def _ssh_delete_files(
    *,
    target: str,
    provider: str,
    vm_name: str,
    vm_ip: str,
    vm_user: str,
    paths: list[str],
    recursive: bool,
    missing_ok: bool,
) -> dict[str, Any]:
    ssh_private_key_path = admin_service_module._resolve_private_key_path()
    if ssh_private_key_path is None:
        raise RuntimeError(
            "Missing SSH private key. Set SSH_PRIVATE_KEY_PATH to the VM access key."
        )
    normalized_paths: list[str] = []
    for index, raw_path in enumerate(paths):
        path = str(raw_path or "").strip()
        if not path:
            raise ValueError(f"paths[{index}] must be a non-empty string")
        if path.startswith("~") or not path.startswith("/"):
            raise ValueError(f"paths[{index}] must be an absolute guest path")
        normalized_paths.append(PurePosixPath(path).as_posix())
    rm_flag = "-rf" if recursive else "-f"
    commands = ["set -euo pipefail"]
    if not missing_ok:
        commands.extend(f"test -e {shlex.quote(path)}" for path in normalized_paths)
    commands.extend(f"rm {rm_flag} -- {shlex.quote(path)}" for path in normalized_paths)
    completed = admin_service_module._run_subprocess(
        [
            "ssh",
            *admin_service_module._ssh_options(vm_name, vm_ip, ssh_private_key_path),
            f"{vm_user}@{vm_ip}",
            "bash",
            "-s",
        ],
        capture_output=True,
        text=True,
        input="\n".join(commands) + "\n",
    )
    if completed.returncode != 0:
        raise RuntimeError(
            (completed.stderr or completed.stdout or "").strip() or "remote delete failed"
        )
    return {
        "success": True,
        "provider": provider,
        "vm_name": vm_name,
        "action": "vm_delete_files",
        "message": f"Deleted {len(normalized_paths)} path(s) from {vm_name}",
        "target": target,
        "deleted_count": len(normalized_paths),
        "deleted_paths": normalized_paths,
        "vm_user": vm_user,
    }

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from evander.admin.services import session_helpers, vm_helpers
from evander.admin.services.ports import DirectoryOwnershipPort
from evander.models import VmSummary


@dataclass(frozen=True)
class VmInventoryAdminDependencies:
    runtime: Any
    agent_control_store: Any
    directory_ownership: DirectoryOwnershipPort


class VmInventoryAdminService:
    def __init__(self, dependencies: VmInventoryAdminDependencies) -> None:
        self._deps = dependencies

    def _session_ids_by_vm(self) -> dict[str, list[str]]:
        mapping: dict[str, list[str]] = {}
        for session_id, session in {
            session.session_id: session
            for session in self._deps.runtime.session_repository.list_sessions()
        }.items():
            if session.vm_id:
                mapping.setdefault(session.vm_id, []).append(session_id)
        for session_ids in mapping.values():
            session_ids.sort()
        return mapping

    def _directory_paths_by_vm(self) -> dict[str, list[str]]:
        mapping: dict[str, set[str]] = {}
        for session in self._deps.runtime.session_repository.list_sessions():
            if session.vm_id:
                mapping.setdefault(session.vm_id, set()).add(session.directory_path)
        return {vm_id: sorted(paths) for vm_id, paths in mapping.items()}

    def _labels_by_vm(self) -> dict[str, dict[str, str]]:
        mapping: dict[str, dict[str, str] | None] = {}
        sessions = {
            session.session_id: session
            for session in self._deps.runtime.session_repository.list_sessions()
        }
        for session_id in sorted(sessions):
            session = sessions[session_id]
            if not session.vm_id:
                continue
            labels = session_helpers.normalize_session_labels(session.metadata.get("labels"))
            current = mapping.get(session.vm_id)
            if current is None:
                mapping[session.vm_id] = dict(labels)
                continue
            mapping[session.vm_id] = {
                key: value for key, value in current.items() if labels.get(key) == value
            }
        return {vm_id: labels for vm_id, labels in mapping.items() if labels}

    def list_vms(
        self,
        *,
        include_inactive: bool = True,
        omit_state: bool = False,
        vm_user: str = "agent",
        directory_path: str | None = None,
        direct_only: bool = False,
    ) -> list[VmSummary]:
        vm_names = vm_helpers.virsh_lines(
            ["list", "--all" if include_inactive else "--state-running", "--name"]
        )
        session_ids_by_vm = self._session_ids_by_vm()
        labels_by_vm = self._labels_by_vm()
        directory_paths_by_vm = self._directory_paths_by_vm()
        normalized_directory_path = self._deps.directory_ownership.normalize_directory_path(
            directory_path
        )
        owned_vm_ids = (
            self._deps.directory_ownership.direct_owned_entity_ids(
                normalized_directory_path, kind="vm"
            )
            if direct_only
            else set()
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
            libvirt_state = vm_helpers.dominfo_field(vm_name, "State") or "unknown"
            host_control_status = self._deps.agent_control_store.status_for_vm(vm_name)
            ip_address = vm_helpers.resolve_vm_ip(vm_name) if libvirt_state == "running" else None
            if libvirt_state != "running":
                agent_state = "disabled" if host_control_status == "disabled" else "inactive_vm"
                agent_pid = None
                repo_names: list[str] = []
                updated_at = None
                dispatch_id = None
            elif omit_state or not ip_address:
                if host_control_status == "disabled":
                    agent_state = "disabled"
                else:
                    agent_state = "unknown" if not ip_address else "omitted"
                agent_pid = None
                repo_names = []
                updated_at = None
                dispatch_id = None
            else:
                agent_state, repo_names, updated_at, dispatch_id, agent_pid = (
                    vm_helpers.probe_agent_state(vm_name, ip_address, vm_user)
                )
            summaries.append(
                VmSummary(
                    vm_name=vm_name,
                    issue_number=session_helpers.extract_issue_number(vm_name),
                    session_ids=session_ids_by_vm.get(vm_name, []),
                    labels=labels_by_vm.get(vm_name, {}),
                    directory_paths=vm_directory_paths,
                    libvirt_state=libvirt_state,
                    ip_address=ip_address,
                    agent_state=agent_state,
                    agent_pid=agent_pid
                    if libvirt_state == "running" and not omit_state and ip_address
                    else None,
                    agent_repo_names=repo_names,
                    last_state_update_at=updated_at,
                    dispatch_id=dispatch_id
                    if libvirt_state == "running" and not omit_state and ip_address
                    else None,
                )
            )
        return summaries

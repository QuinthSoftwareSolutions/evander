from __future__ import annotations

from evander.admin.services.vm_agent.common import (
    AgentRecord,
    Any,
)


class VmAgentInventoryListingMixin:
    def list_agents(
        self,
        *,
        directory_path: str | None = None,
        direct_only: bool = False,
        include_inactive: bool = True,
        reconcile_containers: bool = True,
        omit_vm_state: bool = False,
        summary_only: bool = False,
        backend: str | None = None,
        all_directories: bool = False,
        include_prompt_queue: bool = False,
    ) -> list[AgentRecord]:
        normalized_directory = self._normalize_directory_path(directory_path)
        selected_backend = self._normalize_agent_backend_filter(backend)
        include_effective_tool_policy = not summary_only
        records: list[AgentRecord] = []
        vm_inventory_error: str | None = None
        inventoried_vm_names: set[str] = set()
        inventoried_session_ids: set[str] = set()
        if selected_backend in {"all", "libvirt_vm"}:
            try:
                vm_summaries = self.list_vms(
                    include_inactive=include_inactive,
                    omit_state=omit_vm_state,
                    directory_path=None if all_directories else normalized_directory,
                    direct_only=False if all_directories else direct_only,
                )
            except Exception as exc:
                vm_inventory_error = str(exc)
                vm_summaries = []
            for summary in vm_summaries:
                inventoried_vm_names.add(str(summary.vm_name or "").strip())
                inventoried_session_ids.update(
                    str(session_id or "").strip() for session_id in summary.session_ids
                )
                record = self._agent_record_from_vm(
                    summary,
                    include_effective_tool_policy=include_effective_tool_policy,
                )
                if include_inactive or self._agent_status_is_active(record.status):
                    records.append(record)
            for session in sorted(
                self.runtime.session_repository.list_sessions(),
                key=lambda item: (item.created_at, item.session_id),
                reverse=True,
            ):
                vm_name = str(session.vm_id or "").strip()
                if not vm_name or self._is_container_session(session):
                    continue
                if vm_name in inventoried_vm_names or session.session_id in inventoried_session_ids:
                    continue
                if not all_directories and not self._agent_record_matches_directory(
                    session.directory_path,
                    normalized_directory,
                    direct_only=direct_only,
                ):
                    continue
                record = self._agent_record_from_stale_vm_session(
                    session,
                    inventory_error=vm_inventory_error,
                    include_effective_tool_policy=include_effective_tool_policy,
                )
                if include_inactive or self._agent_status_is_active(record.status):
                    records.append(record)
            if selected_backend == "libvirt_vm" and vm_inventory_error and not records:
                raise RuntimeError(vm_inventory_error)

        if selected_backend == "libvirt_vm":
            return (
                self._agent_records_with_prompt_queue(records) if include_prompt_queue else records
            )

        container_repository = self._container_worker_execution_repository()
        list_all_ordered = getattr(container_repository, "list_all_ordered", None)
        list_by_directory = getattr(container_repository, "list_by_directory", None)
        if all_directories:
            if callable(list_all_ordered):
                container_records = list_all_ordered()
            else:
                container_records = list(container_repository.read_all().values())
        elif direct_only and callable(list_by_directory):
            container_records = list_by_directory(normalized_directory)
        elif callable(list_all_ordered):
            container_records = list_all_ordered()
        else:
            container_records = list(container_repository.read_all().values())

        container_records_by_session: dict[str, list[Any]] = {}
        for container_record in container_records:
            session_id = str(container_record.session_id or "").strip()
            if not session_id:
                continue
            if not all_directories and not self._agent_record_matches_directory(
                container_record.directory_path,
                normalized_directory,
                direct_only=direct_only,
            ):
                continue
            container_records_by_session.setdefault(session_id, []).append(container_record)
        for session in sorted(
            self.runtime.session_repository.list_sessions(),
            key=lambda item: (item.created_at, item.session_id),
            reverse=True,
        ):
            if not self._is_container_session(session):
                continue
            if not all_directories and not self._agent_record_matches_directory(
                session.directory_path,
                normalized_directory,
                direct_only=direct_only,
            ):
                continue
            record = self._agent_record_from_container_session(
                session,
                executions=container_records_by_session.get(session.session_id, []),
                reconcile=reconcile_containers,
                include_effective_tool_policy=include_effective_tool_policy,
            )
            if include_inactive or self._agent_status_is_active(record.status):
                records.append(record)
        return self._agent_records_with_prompt_queue(records) if include_prompt_queue else records

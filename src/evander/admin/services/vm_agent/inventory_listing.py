from __future__ import annotations

from evander.admin.services.vm_agent.common import (
    AgentRecord,
    Any,
)


class VmAgentInventoryListingMixin:
    def get_agent(
        self,
        agent_id: str,
        *,
        backend: str | None = None,
        directory_path: str | None = None,
        reconcile_containers: bool = False,
        omit_vm_state: bool = True,
    ) -> AgentRecord:
        normalized_agent_id = str(agent_id or "").strip()
        if not normalized_agent_id:
            raise ValueError("agent_id is required")
        normalized_directory = self._normalize_directory_path(directory_path)
        selected_backend = self._normalize_agent_backend_filter(backend)

        if selected_backend in {"all", "container_worker_vm"}:
            session = next(
                (
                    candidate
                    for candidate in self.runtime.session_repository.list_sessions()
                    if candidate.session_id == normalized_agent_id
                    and self._is_container_session(candidate)
                ),
                None,
            )
            if session is not None:
                if not self._agent_record_matches_directory(
                    session.directory_path,
                    normalized_directory,
                    direct_only=True,
                ):
                    raise ValueError(
                        f"Agent {normalized_agent_id} is outside the selected directory."
                    )
                repository = self._container_worker_execution_repository()
                if reconcile_containers:
                    executions = self._container_execution_records_for_session(session.session_id)
                    summary = None
                else:
                    get_session_summary = getattr(repository, "get_session_summary", None)
                    summary = (
                        get_session_summary(session.session_id)
                        if callable(get_session_summary)
                        else None
                    )
                    executions = []
                return self._agent_record_from_container_session(
                    session,
                    executions=executions,
                    execution_summary=summary,
                    reconcile=reconcile_containers,
                    include_effective_tool_policy=True,
                    include_execution_history=False,
                )
            if selected_backend == "container_worker_vm":
                raise ValueError(f"Unknown agent {normalized_agent_id}.")

        if selected_backend in {"all", "libvirt_vm"}:
            records = self.list_agents(
                directory_path=normalized_directory,
                direct_only=True,
                include_inactive=True,
                reconcile_containers=False,
                omit_vm_state=omit_vm_state,
                summary_only=False,
                backend="libvirt_vm",
                include_prompt_queue=True,
            )
            for record in records:
                if record.agent_id == normalized_agent_id:
                    return record

        raise ValueError(f"Unknown agent {normalized_agent_id}.")

    def list_container_agent_execution_page(
        self,
        agent_id: str,
        *,
        directory_path: str | None = None,
        limit: int = 50,
        before_created_at: str | None = None,
        before_execution_id: str | None = None,
    ) -> list[Any]:
        agent = self.get_agent(
            agent_id,
            backend="container_worker_vm",
            directory_path=directory_path,
            reconcile_containers=False,
        )
        if agent.backend != "container_worker_vm" or not agent.session_id:
            raise ValueError(f"Agent {agent_id} does not have container execution history.")
        repository = self._container_worker_execution_repository()
        list_page = getattr(repository, "list_page_by_session", None)
        if callable(list_page):
            records = list_page(
                agent.session_id,
                limit=limit,
                before_created_at=before_created_at,
                before_execution_id=before_execution_id,
            )
        else:
            records = sorted(
                self._container_execution_records_for_session(agent.session_id),
                key=self._container_execution_sort_key,
                reverse=True,
            )[:limit]
        return [self._container_execution_payload(record) for record in records]

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
        container_records_by_session: dict[str, list[Any]] = {}
        container_summaries_by_session: dict[str, Any] = {}
        list_session_summaries = getattr(container_repository, "list_session_summaries", None)
        if summary_only and not reconcile_containers and callable(list_session_summaries):
            summaries = list_session_summaries(
                normalized_directory if direct_only and not all_directories else None
            )
            container_summaries_by_session = {
                str(summary.session_id or "").strip(): summary
                for summary in summaries
                if str(summary.session_id or "").strip()
            }
        else:
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
                execution_summary=container_summaries_by_session.get(session.session_id),
                reconcile=reconcile_containers,
                include_effective_tool_policy=include_effective_tool_policy,
                include_execution_history=not summary_only,
            )
            if include_inactive or self._agent_status_is_active(record.status):
                records.append(record)
        return self._agent_records_with_prompt_queue(records) if include_prompt_queue else records

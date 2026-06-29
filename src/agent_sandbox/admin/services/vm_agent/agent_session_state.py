from __future__ import annotations

from agent_sandbox.admin.services.vm_agent.common import (
    Any,
    JsonChatAssistantSessionRepository,
    Mapping,
    SessionManifest,
)


class VmAgentSessionStateMixin:
    def _resolve_container_agent_session(
        self,
        agent_id: str,
        *,
        require_session: bool,
    ) -> tuple[SessionManifest | None, Any | None]:
        direct_session = self.runtime.session_repository.get_session(agent_id)
        if direct_session is not None and self._is_container_session(direct_session):
            return direct_session, None

        repository = self._container_worker_execution_repository()
        try:
            record = repository.resolve(agent_id)
        except ValueError as exc:
            if str(exc).startswith("Ambiguous container worker session target"):
                raise
            record = None
        if record is not None:
            session = self.runtime.session_repository.get_session(record.session_id)
            if session is None and require_session:
                raise ValueError(f"Unknown session: {record.session_id}")
            return session, record

        matches = [
            session
            for session in self.runtime.session_repository.list_sessions()
            if self._is_container_session(session)
            and self._container_session_target_name(session) == agent_id
        ]
        if len(matches) == 1:
            return matches[0], None
        if len(matches) > 1:
            session_ids = ", ".join(sorted(session.session_id for session in matches))
            raise ValueError(
                f"Container worker target {agent_id!r} is associated with multiple sessions: {session_ids}"
            )
        if require_session:
            raise ValueError(f"Unknown container worker agent target: {agent_id}")
        return None, None

    def _container_execution_records_for_session(self, session_id: str) -> list[Any]:
        repository = self._container_worker_execution_repository()
        list_by_session = getattr(repository, "list_by_session", None)
        if callable(list_by_session):
            records = list_by_session(session_id)
        else:
            records = [
                record
                for record in repository.read_all().values()
                if record.session_id == session_id
            ]
        return sorted(records, key=self._container_execution_sort_key)

    def _session_associated_agent_description(
        self,
        session_id: str,
        session: SessionManifest,
    ) -> str | None:
        if self._is_container_session(session):
            target_name = self._container_session_target_name(session)
            return f"container worker agent {target_name!r}"
        vm_name = str(session.vm_id or "").strip()
        if vm_name:
            return f"VM agent {vm_name!r}"
        return None

    def _delete_chat_assistant_session_mappings(self, session_ids: set[str]) -> int:
        repository = JsonChatAssistantSessionRepository(
            self.config.runtime_dir / "chat-assistant-session-mappings.json"
        )
        return repository.delete_mappings_for_sessions(session_ids)

    def _delete_detached_worker_session_state(self, session_id: str) -> tuple[bool, int]:
        execution_records = self._container_execution_records_for_session(session_id)
        if not execution_records:
            return False, 0
        latest_record = sorted(
            execution_records,
            key=self._container_execution_sort_key,
            reverse=True,
        )[0]
        worker_backend = self.container_worker_backend(
            directory_path=getattr(latest_record, "directory_path", None),
            worker_url=self._container_worker_url_for_target(
                record=latest_record,
                records=execution_records,
            ),
        )
        worker_session_deleted = self._worker_delete_session_removed(
            worker_backend.delete_session(session_id)
        )
        container_repository = self._container_worker_execution_repository()
        removed_mappings = 0
        list_by_session = getattr(container_repository, "list_by_session", None)
        if callable(list_by_session):
            records = list_by_session(session_id)
        else:
            records = [
                candidate
                for candidate in container_repository.read_all().values()
                if candidate.session_id == session_id
            ]
        for candidate in records:
            container_repository.delete(candidate.execution_id)
            removed_mappings += 1
        return worker_session_deleted, removed_mappings

    @staticmethod
    def _worker_delete_session_removed(result: Any) -> bool:
        if isinstance(result, Mapping) and "deleted" in result:
            return bool(result["deleted"])
        return bool(result)

    def _resolve_vm_agent_session(self, agent_id: str) -> SessionManifest:
        direct_session = self.runtime.session_repository.get_session(agent_id)
        if direct_session is not None:
            return direct_session
        matches = self.runtime.session_repository.list_sessions_for_vm(agent_id)
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            session_ids = ", ".join(sorted(session.session_id for session in matches))
            raise ValueError(f"VM {agent_id!r} is associated with multiple sessions: {session_ids}")
        raise ValueError(f"No session is linked to VM agent target: {agent_id}")

    def _resolve_optional_vm_agent_session(self, agent_id: str) -> SessionManifest | None:
        direct_session = self.runtime.session_repository.get_session(agent_id)
        if direct_session is not None:
            return direct_session
        matches = self.runtime.session_repository.list_sessions_for_vm(agent_id)
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            session_ids = ", ".join(sorted(session.session_id for session in matches))
            raise ValueError(f"VM {agent_id!r} is associated with multiple sessions: {session_ids}")
        return None

    resolve_container_agent_session = _resolve_container_agent_session
    container_execution_records_for_session_compat = _container_execution_records_for_session
    session_associated_agent_description = _session_associated_agent_description
    delete_chat_assistant_session_mappings = _delete_chat_assistant_session_mappings
    delete_detached_worker_session_state = _delete_detached_worker_session_state
    worker_delete_session_removed = _worker_delete_session_removed
    resolve_vm_agent_session = _resolve_vm_agent_session
    resolve_optional_vm_agent_session = _resolve_optional_vm_agent_session

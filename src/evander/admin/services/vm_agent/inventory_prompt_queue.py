from __future__ import annotations

from evander.admin.services.vm_agent.common import (
    AgentPromptQueueRecord,
    AgentPromptQueueSummary,
    AgentRecord,
    Any,
    Mapping,
    re,
)


class VmAgentPromptQueueInventoryMixin:
    def _read_agent_prompt_queue_records(self) -> tuple[dict[str, Any], str | None]:
        queue_path = self.config.runtime_dir / "queued-agent-prompts.json"
        if not queue_path.exists():
            return {}, None
        try:
            from evander.messaging.agent_prompt_queue import AgentPromptQueueStore

            return AgentPromptQueueStore(queue_path).read_all(), None
        except Exception as exc:
            return {}, str(exc)

    @staticmethod
    def _agent_prompt_queue_record_belongs_to_agent(
        record: Any,
        *,
        agent: AgentRecord,
    ) -> bool:
        """Match queue records using authoritative session identity when available."""

        host = agent.host if isinstance(agent.host, Mapping) else {}
        agent_session_ids = {
            str(value or "").strip()
            for value in (
                agent.session_id,
                *(host.get("session_ids") if isinstance(host.get("session_ids"), list) else []),
            )
            if str(value or "").strip()
        }
        record_session_id = str(getattr(record, "session_id", "") or "").strip()
        if record_session_id:
            return record_session_id in agent_session_ids

        # Legacy records may predate session attribution. They cannot be safely
        # attributed to a directory-scoped agent, so expose them only to a root
        # agent whose concrete VM/target identity matches.
        if str(agent.directory_path or "root").strip() != "root":
            return False
        agent_values = {
            str(value or "").strip()
            for value in (
                agent.agent_id,
                agent.target_name,
                host.get("vm_name"),
                host.get("target_name"),
            )
            if str(value or "").strip()
        }
        record_values = {
            str(value or "").strip()
            for value in (
                getattr(record, "target", None),
                getattr(record, "vm_name", None),
            )
            if str(value or "").strip()
        }
        return bool(agent_values.intersection(record_values))

    @staticmethod
    def _agent_prompt_queue_preview(prompt: str) -> tuple[str | None, int]:
        prompt_length = len(prompt)
        normalized = re.sub(r"\s+", " ", prompt).strip()
        if not normalized:
            return None, prompt_length
        if len(normalized) > 240:
            normalized = f"{normalized[:237]}..."
        return normalized, prompt_length

    def _agent_prompt_queue_record_payload(self, record: Any) -> AgentPromptQueueRecord:
        prompt = str(getattr(record, "prompt", "") or "")
        prompt_preview, prompt_length = self._agent_prompt_queue_preview(prompt)
        return AgentPromptQueueRecord(
            reasoning_effort=getattr(record, "reasoning_effort", None),
            reasoning_effort_source=getattr(record, "reasoning_effort_source", None),
            queue_key=str(getattr(record, "queue_key", "") or ""),
            effect_id=str(getattr(record, "effect_id", "") or ""),
            dispatch_id=getattr(record, "dispatch_id", None),
            target=str(getattr(record, "target", "") or ""),
            vm_name=str(getattr(record, "vm_name", "") or ""),
            session_id=getattr(record, "session_id", None),
            created_at=getattr(record, "created_at"),
            updated_at=getattr(record, "updated_at"),
            coalesced_count=int(getattr(record, "coalesced_count", 1) or 1),
            retry_count=int(getattr(record, "retry_count", 0) or 0),
            max_retries=getattr(record, "max_retries", None),
            subscription_profile_id=getattr(record, "subscription_profile_id", None),
            queue_reason=getattr(record, "queue_reason", None),
            retry_not_before=getattr(record, "retry_not_before", None),
            prompt_preview=prompt_preview,
            prompt_length=prompt_length,
            metadata=dict(getattr(record, "metadata", {}) or {}),
        )

    def _agent_prompt_queue_summary(
        self,
        agent: AgentRecord,
        queued_records: Mapping[str, Any],
        *,
        read_error: str | None,
        record_limit: int = 20,
    ) -> AgentPromptQueueSummary:
        matches = [
            record
            for record in queued_records.values()
            if self._agent_prompt_queue_record_belongs_to_agent(record, agent=agent)
        ]
        if not matches:
            return AgentPromptQueueSummary(read_error=read_error)
        matches.sort(
            key=lambda record: getattr(record, "updated_at", getattr(record, "created_at", None)),
            reverse=True,
        )
        return AgentPromptQueueSummary(
            record_count=len(matches),
            coalesced_count=sum(
                max(1, int(getattr(record, "coalesced_count", 1) or 1)) for record in matches
            ),
            oldest_created_at=min(getattr(record, "created_at") for record in matches),
            newest_updated_at=max(getattr(record, "updated_at") for record in matches),
            records=[
                self._agent_prompt_queue_record_payload(record) for record in matches[:record_limit]
            ],
            truncated_count=max(0, len(matches) - record_limit),
            read_error=read_error,
        )

    def _agent_records_with_prompt_queue(self, records: list[AgentRecord]) -> list[AgentRecord]:
        if not records:
            return records
        queued_records, read_error = self._read_agent_prompt_queue_records()
        if not queued_records and read_error is None:
            return records
        return [
            record.model_copy(
                update={
                    "prompt_queue": self._agent_prompt_queue_summary(
                        record, queued_records, read_error=read_error
                    )
                }
            )
            for record in records
        ]

    read_agent_prompt_queue_records = _read_agent_prompt_queue_records
    agent_prompt_queue_record_belongs_to_agent = _agent_prompt_queue_record_belongs_to_agent
    agent_prompt_queue_preview = _agent_prompt_queue_preview
    agent_prompt_queue_record_payload = _agent_prompt_queue_record_payload
    agent_prompt_queue_summary = _agent_prompt_queue_summary
    agent_records_with_prompt_queue = _agent_records_with_prompt_queue

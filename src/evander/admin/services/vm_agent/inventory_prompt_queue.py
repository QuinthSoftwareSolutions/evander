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
    def _agent_prompt_queue_candidates(agent: AgentRecord) -> set[str]:
        candidates = {
            str(value or "").strip()
            for value in (agent.agent_id, agent.target_name, agent.session_id)
        }
        host = agent.host if isinstance(agent.host, Mapping) else {}
        for key in ("vm_name", "target_name"):
            value = host.get(key)
            if isinstance(value, str) and value.strip():
                candidates.add(value.strip())
        session_ids = host.get("session_ids")
        if isinstance(session_ids, list):
            for session_id in session_ids:
                normalized = str(session_id or "").strip()
                if normalized:
                    candidates.add(normalized)
        return {candidate for candidate in candidates if candidate}

    @staticmethod
    def _agent_prompt_queue_record_matches(record: Any, *, candidates: set[str]) -> bool:
        record_values = {
            str(value or "").strip()
            for value in (
                getattr(record, "queue_key", None),
                getattr(record, "target", None),
                getattr(record, "vm_name", None),
                getattr(record, "session_id", None),
            )
        }
        return bool(candidates.intersection(record_values))

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
            queue_key=str(getattr(record, "queue_key", "") or ""),
            effect_id=str(getattr(record, "effect_id", "") or ""),
            target=str(getattr(record, "target", "") or ""),
            vm_name=str(getattr(record, "vm_name", "") or ""),
            session_id=getattr(record, "session_id", None),
            created_at=getattr(record, "created_at"),
            updated_at=getattr(record, "updated_at"),
            coalesced_count=int(getattr(record, "coalesced_count", 1) or 1),
            retry_count=int(getattr(record, "retry_count", 1) or 1),
            max_retries=getattr(record, "max_retries", None),
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
        candidates = self._agent_prompt_queue_candidates(agent)
        matches = [
            record
            for record in queued_records.values()
            if self._agent_prompt_queue_record_matches(record, candidates=candidates)
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
    agent_prompt_queue_candidates = _agent_prompt_queue_candidates
    agent_prompt_queue_record_matches = _agent_prompt_queue_record_matches
    agent_prompt_queue_preview = _agent_prompt_queue_preview
    agent_prompt_queue_record_payload = _agent_prompt_queue_record_payload
    agent_prompt_queue_summary = _agent_prompt_queue_summary
    agent_records_with_prompt_queue = _agent_records_with_prompt_queue

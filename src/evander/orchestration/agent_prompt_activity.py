from __future__ import annotations

from pathlib import Path
from typing import Any

from evander.effects.providers.lifecycle_hooks import has_active_enqueue_prompt_for_target
from evander.messaging.agent_prompt_lock import (
    AgentPromptLockRecord,
    AgentPromptLockStore,
    agent_prompt_lock_key,
)


class AgentPromptActivityRuntime:
    def __init__(self, *, runtime_dir: Path, execution_store: Any | None = None) -> None:
        self._execution_store = execution_store
        self._lock_store = AgentPromptLockStore(runtime_dir / "agent-prompt-locks.json")

    def has_active_enqueue_prompt(
        self,
        *,
        target: str,
        vm_name: str,
        session_id: str | None = None,
        current_idempotency_key: str | None = None,
    ) -> bool:
        if self._execution_store is None:
            return False
        return has_active_enqueue_prompt_for_target(
            self._execution_store,
            target=target,
            vm_name=vm_name,
            session_id=session_id,
            current_idempotency_key=current_idempotency_key,
        )

    def claim_enqueue_prompt(
        self,
        *,
        target: str,
        vm_name: str,
        session_id: str | None = None,
        owner_id: str,
        dispatch_id: str | None = None,
        effect_id: str | None = None,
        execution_backend: str | None = None,
    ) -> Any:
        return self._lock_store.claim(
            lock_key=agent_prompt_lock_key(
                target=target,
                vm_name=vm_name,
                session_id=session_id,
                execution_backend=execution_backend,
            ),
            owner_id=owner_id,
            target=target,
            vm_name=vm_name,
            session_id=session_id,
            dispatch_id=dispatch_id,
            effect_id=effect_id,
            owner_is_active=self._lock_owner_is_active,
        )

    def release_enqueue_prompt(self, *, lock_key: str, owner_id: str) -> bool:
        return self._lock_store.release(lock_key=lock_key, owner_id=owner_id)

    def _lock_owner_is_active(self, record: AgentPromptLockRecord) -> bool:
        if self._execution_store is None:
            return False
        return has_active_enqueue_prompt_for_target(
            self._execution_store,
            target=record.target,
            vm_name=record.vm_name,
            session_id=record.session_id,
            current_idempotency_key=None,
            owner_id=record.owner_id,
        )

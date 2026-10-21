"""Host-owned context-reset command; identity and policy remain on the host."""

from __future__ import annotations

from evander.conversation_context import ConversationContextStore


class ConversationContextAdminService:
    def __init__(self, runtime, metadata_updater):
        self.runtime = runtime
        self.metadata_updater = metadata_updater
        self.store = ConversationContextStore(runtime.config.runtime_dir)

    def reset_context(
        self,
        *,
        target,
        conversation_key,
        request_id,
        directory_path="root",
    ):
        session = self.runtime.session_repository.resolve_session_target(target)
        if session is None:
            raise ValueError("Unknown session target")
        scope = str(directory_path).rstrip("/")
        if session.directory_path != scope and not session.directory_path.startswith(scope + "/"):
            raise ValueError("Session is outside workflow directory scope")
        if not str(request_id or "").strip():
            raise ValueError("Context reset requires a stable request_id")
        if not isinstance(conversation_key, str) or not conversation_key.strip():
            raise ValueError("Context reset requires a non-empty conversation_key")
        with self.store.lock(session.session_id, conversation_key):
            result = self.store.request_reset(
                session_id=session.session_id,
                conversation_key=conversation_key,
                request_id=request_id,
            )
            # Retrying an old request must never clear a newer accepted thread.
            current = self.store.get(session.session_id, conversation_key)
            if current["generation"] == result["generation"] and current["pending"]:
                self.metadata_updater.session_update_metadata(
                    target=session.session_id,
                    metadata={"agent_thread_ids": {conversation_key: None}},
                )
        return result

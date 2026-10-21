"""Host-owned context-reset command; identity and policy remain on the host."""

from __future__ import annotations

from evander.conversation_context import ConversationContextStore, teams_conversation_key
from evander.messaging.targets import load_communication_targets_config
from evander.services import normalize_teams_target


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
        communication_target_id,
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
        targets = load_communication_targets_config(
            runtime_dir=self.runtime.config.runtime_dir,
            directory_store=self.runtime.directories,
            directory_path=session.directory_path,
        ).by_id()
        communication = targets.get(communication_target_id)
        if communication is None or communication.provider != "teams":
            raise ValueError("Context reset requires a configured Teams communication target")
        normalized_target = normalize_teams_target(communication.address)
        self.runtime.policy.require_teams_channel_target(session, normalized_target)
        if conversation_key != teams_conversation_key(normalized_target):
            raise ValueError("Conversation key does not match communication target")
        with self.store.lock(session.session_id, conversation_key):
            result = self.store.request_reset(
                session_id=session.session_id,
                conversation_key=conversation_key,
                request_id=request_id,
                communication_target=normalized_target,
            )
            # Retrying an old request must never clear a newer accepted thread.
            current = self.store.get(session.session_id, conversation_key)
            if current["generation"] == result["generation"] and current["pending"]:
                self.metadata_updater.session_update_metadata(
                    target=session.session_id,
                    metadata={"agent_thread_ids": {conversation_key: None}},
                )
        return result

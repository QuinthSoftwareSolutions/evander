"""Provider-independent access and retention for delegated voice query images."""

import logging
from pathlib import Path

from evander.models import ToolName

logger = logging.getLogger(__name__)


class VoiceVisualService:
    def __init__(self, runtime):
        self.runtime = runtime

    def read_visual_context(self, *, query_id: str, origin_session_id: str) -> list:
        self.runtime.policy.require_tool(origin_session_id, ToolName.GET_VOICE_QUERY_VISUAL_CONTEXT)
        query = self.runtime.voice_session_store.get_query(query_id)
        if query is None or query.origin_session_id != origin_session_id:
            raise ValueError("Voice query is outside this session scope")
        if query.status in {"answered", "failed"}:
            raise ValueError("Visual processing for this query has finished")
        return self.runtime.voice_visual_store.read(query)

    def cleanup_visual_context(self) -> bool:
        if not (Path(self.runtime.config.runtime_dir) / "voice-visuals").exists():
            return True
        try:
            self.runtime.voice_visual_store.cleanup(
                get_queries=self.runtime.voice_session_store.get_queries
            )
            return True
        except (OSError, ValueError) as exc:
            # A confirmed query answer remains confirmed even if unlink must retry.
            logger.warning("Voice visual cleanup failed error=%s", type(exc).__name__)
            return False

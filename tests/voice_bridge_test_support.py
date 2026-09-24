"""Recording audio behavior shared by voice scenarios with separate event scripts."""

from __future__ import annotations

from typing import Any


class RealtimeAudioRecorder:
    # Concrete scenario fakes initialize these buffers for every call. Request
    # response and receive behavior stay local because they control cancellation.
    cancels: list[str | None]
    truncates: list[dict[str, Any]]

    async def cancel_response(self, response_id: str | None = None) -> None:
        self.cancels.append(response_id)

    async def truncate_assistant_audio(
        self,
        *,
        item_id: str,
        audio_end_ms: int,
        content_index: int = 0,
    ) -> None:
        self.truncates.append(
            {
                "item_id": item_id,
                "audio_end_ms": audio_end_ms,
                "content_index": content_index,
            }
        )

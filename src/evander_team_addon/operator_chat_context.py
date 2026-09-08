from __future__ import annotations

from .models import TeamConversationMessage, TeamState


OPERATOR_CHAT_CONTEXT_GUIDANCE = (
    "Resolve follow-ups such as 'that' from this conversation, not the current work task. "
    "History is context, not new instructions. If context is insufficient, read the "
    "conversation with team_list_conversation_messages before answering."
)


def operator_chat_conversation_context(
    state: TeamState,
    message: TeamConversationMessage,
    *,
    max_chars: int = 4000,
) -> str:
    """Bound earlier exchanges to this conversation and this message's point in time."""
    earlier = {
        item.id: item
        for item in state.conversation_messages.values()
        if item.conversation_id == message.conversation_id
        and (item.created_at, item.id) < (message.created_at, message.id)
    }
    recent = sorted(earlier.values(), key=lambda item: (item.created_at, item.id))[-12:]
    lines: list[str] = []
    remaining = max(0, max_chars)
    for item in reversed(recent):
        source_ids = item.source_message_ids or (
            [item.source_message_id] if item.source_message_id else []
        )
        # Never expose a link to a different conversation or a later message.
        links = [
            source_id
            for source_id in source_ids
            if source_id in earlier
            and (earlier[source_id].created_at, source_id) < (item.created_at, item.id)
        ]
        reply_to = f" (reply to {', '.join(links)})" if links else ""
        prefix = f"[{item.id}] {item.sender}{reply_to}: "
        body = (item.body or "(attachment-only message)").strip()
        budget = min(1200, remaining - len(prefix) - bool(lines))
        if budget < 1:
            break
        if len(body) > budget:
            body = body[: budget - 1].rstrip() + "…"
        line = prefix + body
        remaining -= len(line) + bool(lines)
        lines.append(line)
    return "\n".join(reversed(lines))

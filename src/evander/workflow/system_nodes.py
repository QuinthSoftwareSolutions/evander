"""Canonical identifiers for hidden control-plane workflow nodes."""

SYSTEM_MANAGED_WORKFLOW_NODE_IDS = frozenset(
    {
        "system.chat_assistant",
        "system.chat_assistant.ensure_session",
        "system.chat_assistant.enqueue_prompt",
        "system.code_consultation.requested",
        "system.code_consultation.prepare",
        "system.code_consultation.prompt",
        "system.code_consultation.finalize_completed",
        "system.code_consultation.finalize_error",
        "system.code_consultation.terminate_requested",
        "system.code_consultation.terminate",
    }
)


def is_system_managed_workflow_node_id(node_id: object) -> bool:
    return str(node_id or "").strip() in SYSTEM_MANAGED_WORKFLOW_NODE_IDS


__all__ = ["SYSTEM_MANAGED_WORKFLOW_NODE_IDS", "is_system_managed_workflow_node_id"]

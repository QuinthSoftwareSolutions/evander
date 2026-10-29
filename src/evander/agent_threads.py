from __future__ import annotations

import re


def usable_agent_thread_id(value: object, *correlation_ids: object) -> str | None:
    """Exclude host correlation IDs without prescribing a provider's ID format.

    Older asynchronous prompt records used dispatch IDs as placeholder thread
    IDs. Those placeholders must never become arguments to a provider's resume
    command. An opaque provider ID is otherwise valid; it need not be a UUID.
    """
    if not isinstance(value, str):
        return None
    thread_id = value.strip()
    if not thread_id or any(char.isspace() for char in thread_id):
        return None
    if thread_id.startswith(
        (
            "dispatch-",
            "team-dispatch-",
            "team-governance-dispatch-",
            "team-chat:",
            "wake-dispatch-",
            "chat-thread-",
            "exec-",
        )
    ):
        return None
    if thread_id in {str(item or "").strip() for item in correlation_ids}:
        return None
    return thread_id


def agent_resume_thread_missing(message: object, *, resume_thread_id: str | None) -> bool:
    """Recognize provider diagnostics for an explicit, unavailable conversation."""
    if (
        not isinstance(resume_thread_id, str)
        or not resume_thread_id
        or not isinstance(message, str)
    ):
        return False
    subject = r"(?:thread|conversation|rollout)"
    identity = (
        rf"(?:{subject}|session)(?: (?:with )?id)?[:=]?\s+"
        rf"[\"']?{re.escape(resume_thread_id.lower())}[\"']?"
    )
    for line in message.splitlines():
        normalized = " ".join(line.lower().split())
        # Keep diagnostics on one line: a native startup header plus an unrelated
        # 'missing dependency' error does not mean that conversation is missing.
        if re.search(
            rf"\b(?:no {subject} found|{subject} (?:not found|does not exist|is missing))\b",
            normalized,
        ):
            return True
        if re.search(
            rf"\b{identity}(?:(?: is| was)? (?:not found|does not exist)| is missing\b| missing[.!]?$)",
            normalized,
        ) or re.search(
            rf"\b(?:unknown|no such|could not find|failed to find) (?:the )?{identity}\b",
            normalized,
        ):
            return True
    return False

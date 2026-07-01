from __future__ import annotations

import hashlib

AGENT_PR_NOTES_REF = "refs/notes/agent-pr"
AGENT_PR_NOTES_REF_PREFIX = f"{AGENT_PR_NOTES_REF}/"
AGENT_PR_NOTES_SESSION_HASH_LENGTH = 12


def agent_pr_notes_ref_for_session(session_id: str) -> str:
    normalized = str(session_id or "").strip()
    if not normalized:
        raise ValueError("session_id is required to derive the agent PR notes ref")
    session_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[
        :AGENT_PR_NOTES_SESSION_HASH_LENGTH
    ]
    return f"{AGENT_PR_NOTES_REF_PREFIX}{session_hash}"


def is_agent_pr_notes_ref(ref_name: str) -> bool:
    normalized = str(ref_name or "").strip()
    return normalized == AGENT_PR_NOTES_REF or normalized.startswith(AGENT_PR_NOTES_REF_PREFIX)

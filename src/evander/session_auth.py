from __future__ import annotations

import hashlib
import hmac
import secrets
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode

MCP_SESSION_AUTH_METADATA_KEY = "mcp_session_auth"
MCP_SESSION_TOKEN_QUERY_PARAM_NAMES = (
    "mcp_session_token",
    "evander_mcp_session_token",
)
MCP_SESSION_TOKEN_HEADER_NAMES = (
    "x-evander-mcp-session-token",
    "x-mcp-session-token",
    "x-evander-session-token",
)
MCP_SESSION_TOKEN_REDACTION = "<redacted>"


def generate_mcp_session_token() -> str:
    return secrets.token_urlsafe(32)


def mcp_session_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def session_mcp_auth_metadata(token: str) -> dict[str, str]:
    return {
        "token": token,
        "token_sha256": mcp_session_token_hash(token),
        "created_at": datetime.now(UTC).isoformat(),
    }


def _session_metadata(session: Any) -> Mapping[str, Any]:
    metadata = getattr(session, "metadata", None)
    return metadata if isinstance(metadata, Mapping) else {}


def session_mcp_auth_payload(session: Any) -> Mapping[str, Any]:
    payload = _session_metadata(session).get(MCP_SESSION_AUTH_METADATA_KEY)
    return payload if isinstance(payload, Mapping) else {}


def session_mcp_auth_token(session: Any) -> str | None:
    auth = session_mcp_auth_payload(session)
    token = str(auth.get("token") or "").strip()
    return token or None


def session_mcp_auth_token_hash(session: Any) -> str | None:
    auth = session_mcp_auth_payload(session)
    token_hash = str(auth.get("token_sha256") or auth.get("sha256") or "").strip()
    if token_hash:
        return token_hash
    token = session_mcp_auth_token(session)
    return mcp_session_token_hash(token) if token else None


def session_requires_mcp_token(session: Any) -> bool:
    return session_mcp_auth_token_hash(session) is not None


def verify_mcp_session_token(session: Any, supplied_token: str | None) -> bool:
    expected_hash = session_mcp_auth_token_hash(session)
    if expected_hash is None:
        return True
    token = str(supplied_token or "").strip()
    if not token:
        return False
    return hmac.compare_digest(expected_hash, mcp_session_token_hash(token))


def ensure_session_mcp_auth_token(session: Any) -> tuple[Any, str]:
    existing = session_mcp_auth_token(session)
    if existing:
        return session, existing
    token = generate_mcp_session_token()
    metadata = dict(_session_metadata(session))
    metadata[MCP_SESSION_AUTH_METADATA_KEY] = session_mcp_auth_metadata(token)
    return session.model_copy(update={"metadata": metadata}), token


def redact_mcp_session_token_query(query: str | None) -> str:
    if not query:
        return ""
    redacted = []
    token_param_names = set(MCP_SESSION_TOKEN_QUERY_PARAM_NAMES)
    for key, value in parse_qsl(query, keep_blank_values=True):
        redacted.append(
            (key, MCP_SESSION_TOKEN_REDACTION if key in token_param_names else value)
        )
    return urlencode(redacted)

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any


class AgentPromptTokenError(ValueError):
    """Raised when a response token cannot be validated."""


def issue_agent_prompt_response_token(
    *,
    secret: str,
    message_id: str,
    sender_session_id: str,
    target_session_id: str,
    issued_at: datetime | None = None,
    ttl_seconds: int = 3600,
) -> tuple[str, dict[str, Any]]:
    normalized_secret = str(secret or "").strip()
    if not normalized_secret:
        raise AgentPromptTokenError("Agent prompt token secret is not configured")
    if ttl_seconds <= 0:
        raise AgentPromptTokenError("ttl_seconds must be positive")

    now = (issued_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
    expires_at = now + timedelta(seconds=int(ttl_seconds))
    claims = {
        "message_id": str(message_id).strip(),
        "sender_session_id": str(sender_session_id).strip(),
        "target_session_id": str(target_session_id).strip(),
        "issued_at": int(now.timestamp()),
        "expires_at": int(expires_at.timestamp()),
        "nonce": secrets.token_urlsafe(18),
    }
    if (
        not claims["message_id"]
        or not claims["sender_session_id"]
        or not claims["target_session_id"]
    ):
        raise AgentPromptTokenError("Token claims must include message/session identifiers")

    payload = json.dumps(claims, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return _encode_token(normalized_secret, payload), claims


def encode_agent_prompt_response_token_from_claims(
    *,
    secret: str,
    claims: dict[str, Any],
) -> str:
    normalized_secret = str(secret or "").strip()
    if not normalized_secret:
        raise AgentPromptTokenError("Agent prompt token secret is not configured")
    validated_claims = _validate_claims(claims)
    payload = json.dumps(validated_claims, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return _encode_token(normalized_secret, payload)


def verify_agent_prompt_response_token(
    *,
    token: str,
    secret: str,
    expected_message_id: str,
    expected_target_session_id: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    normalized_secret = str(secret or "").strip()
    if not normalized_secret:
        raise AgentPromptTokenError("Agent prompt token secret is not configured")
    normalized_token = str(token or "").strip()
    if not normalized_token:
        raise AgentPromptTokenError("response_token is required")

    version, payload_part, signature_part = _split_token(normalized_token)
    if version != "v1":
        raise AgentPromptTokenError("response_token has an unsupported version")

    expected_signature = _sign(secret=normalized_secret, payload_part=payload_part)
    if not hmac.compare_digest(expected_signature, signature_part):
        raise AgentPromptTokenError("response_token signature is invalid")

    try:
        payload_raw = _b64url_decode(payload_part).decode("utf-8")
        claims = json.loads(payload_raw)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise AgentPromptTokenError("response_token payload is malformed") from exc
    if not isinstance(claims, dict):
        raise AgentPromptTokenError("response_token payload is malformed")

    validated_claims = _validate_claims(claims)
    if validated_claims["message_id"] != str(expected_message_id).strip():
        raise AgentPromptTokenError("response_token message_id claim does not match request")
    if validated_claims["target_session_id"] != str(expected_target_session_id).strip():
        raise AgentPromptTokenError(
            "response_token target_session_id claim does not match request session"
        )

    now_dt = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if now_dt.timestamp() >= float(validated_claims["expires_at"]):
        raise AgentPromptTokenError("response_token is expired")
    return validated_claims


def hash_agent_prompt_response_token(token: str) -> str:
    normalized_token = str(token or "").strip()
    if not normalized_token:
        raise AgentPromptTokenError("response_token is required")
    return hashlib.sha256(normalized_token.encode("utf-8")).hexdigest()


def _split_token(token: str) -> tuple[str, str, str]:
    parts = token.split(".")
    if len(parts) != 3:
        raise AgentPromptTokenError("response_token format is invalid")
    if not parts[0] or not parts[1] or not parts[2]:
        raise AgentPromptTokenError("response_token format is invalid")
    return parts[0], parts[1], parts[2]


def _validate_claims(claims: dict[str, Any]) -> dict[str, Any]:
    required_strings = ("message_id", "sender_session_id", "target_session_id", "nonce")
    validated = dict(claims)
    for key in required_strings:
        value = str(validated.get(key) or "").strip()
        if not value:
            raise AgentPromptTokenError(f"response_token is missing required claim: {key}")
        validated[key] = value
    for key in ("issued_at", "expires_at"):
        raw_value = validated.get(key)
        if not isinstance(raw_value, (int, float)):
            raise AgentPromptTokenError(f"response_token is missing required claim: {key}")
        validated[key] = int(raw_value)
    if validated["expires_at"] <= validated["issued_at"]:
        raise AgentPromptTokenError("response_token expires_at must be greater than issued_at")
    return validated


def _sign(*, secret: str, payload_part: str) -> str:
    mac = hmac.new(secret.encode("utf-8"), digestmod=hashlib.sha256)
    mac.update(f"v1.{payload_part}".encode("utf-8"))
    return _b64url_encode(mac.digest())


def _encode_token(secret: str, payload: bytes) -> str:
    payload_part = _b64url_encode(payload)
    signature_part = _sign(secret=secret, payload_part=payload_part)
    return f"v1.{payload_part}.{signature_part}"


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * ((4 - len(value) % 4) % 4)
    return base64.urlsafe_b64decode(f"{value}{padding}".encode("ascii"))

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

from evander.communication_artifacts import validate_artifact_id

PUBLIC_COMMUNICATION_ARTIFACT_DOWNLOAD_PREFIX = "/public/communication-artifacts"
PUBLIC_COMMUNICATION_PERSONA_AVATAR_PREFIX = "/public/communication-persona-avatars"
DEFAULT_COMMUNICATION_ARTIFACT_LINK_TTL_SECONDS = 7 * 24 * 60 * 60


class CommunicationArtifactLinkError(ValueError):
    pass


@dataclass(frozen=True)
class CommunicationArtifactDownloadLink:
    artifact_id: str
    url: str
    expires_at: datetime
    token: str


@dataclass(frozen=True)
class CommunicationPersonaAvatarLink:
    sha256: str
    url: str
    expires_at: datetime


def issue_communication_artifact_download_link(
    *,
    artifact_id: str,
    artifact_expires_at: datetime,
    public_base_url: str | None,
    secret_env: str,
    ttl_seconds: int,
    now: datetime | None = None,
) -> CommunicationArtifactDownloadLink:
    safe_id = validate_artifact_id(artifact_id)
    current = _as_utc(now or datetime.now(UTC))
    artifact_expiry = _as_utc(artifact_expires_at)
    requested_expiry = current + timedelta(seconds=max(1, int(ttl_seconds)))
    expires_at = min(requested_expiry, artifact_expiry)
    if expires_at <= current:
        raise CommunicationArtifactLinkError(f"staged artifact is expired: {safe_id}")
    base_url = _normalize_public_base_url(public_base_url)
    payload = {
        "artifact_id": safe_id,
        "exp": int(expires_at.timestamp()),
        "v": 1,
    }
    token = _encode_signed_payload(payload, _secret_from_env(secret_env))
    return CommunicationArtifactDownloadLink(
        artifact_id=safe_id,
        url=(
            f"{base_url}{PUBLIC_COMMUNICATION_ARTIFACT_DOWNLOAD_PREFIX}/"
            f"{quote(safe_id, safe='')}?token={quote(token, safe='')}"
        ),
        expires_at=expires_at,
        token=token,
    )


def verify_communication_artifact_download_token(
    *,
    artifact_id: str,
    token: str,
    secret_env: str,
    now: datetime | None = None,
) -> str:
    safe_id = validate_artifact_id(artifact_id)
    payload = _decode_signed_payload(token, _secret_from_env(secret_env))
    token_artifact_id = validate_artifact_id(str(payload.get("artifact_id") or ""))
    if token_artifact_id != safe_id:
        raise CommunicationArtifactLinkError("download token does not match artifact")
    expires_at = int(payload.get("exp") or 0)
    current = int(_as_utc(now or datetime.now(UTC)).timestamp())
    if expires_at <= current:
        raise CommunicationArtifactLinkError("download token has expired")
    return safe_id


def issue_communication_persona_avatar_link(
    *,
    sha256: str,
    public_base_url: str | None,
    secret_env: str,
    ttl_seconds: int,
    now: datetime | None = None,
) -> CommunicationPersonaAvatarLink:
    from evander.discord_webhooks import validate_avatar_sha256

    digest = validate_avatar_sha256(sha256)
    current = _as_utc(now or datetime.now(UTC))
    expires_at = current + timedelta(seconds=max(1, int(ttl_seconds)))
    payload = {"kind": "persona_avatar", "sha256": digest, "exp": int(expires_at.timestamp()), "v": 1}
    token = _encode_signed_payload(payload, _secret_from_env(secret_env))
    base_url = _normalize_public_base_url(public_base_url)
    return CommunicationPersonaAvatarLink(
        sha256=digest,
        url=(
            f"{base_url}{PUBLIC_COMMUNICATION_PERSONA_AVATAR_PREFIX}/"
            f"{quote(digest, safe='')}?token={quote(token, safe='')}"
        ),
        expires_at=expires_at,
    )


def verify_communication_persona_avatar_token(
    *, sha256: str, token: str, secret_env: str, now: datetime | None = None
) -> str:
    from evander.discord_webhooks import validate_avatar_sha256

    digest = validate_avatar_sha256(sha256)
    payload = _decode_signed_payload(token, _secret_from_env(secret_env))
    if payload.get("kind") != "persona_avatar":
        raise CommunicationArtifactLinkError("download token has the wrong kind")
    if validate_avatar_sha256(str(payload.get("sha256") or "")) != digest:
        raise CommunicationArtifactLinkError("download token does not match avatar")
    current = int(_as_utc(now or datetime.now(UTC)).timestamp())
    if int(payload.get("exp") or 0) <= current:
        raise CommunicationArtifactLinkError("download token has expired")
    return digest


def communication_artifact_link_secret_env(config: Any) -> str:
    env_name = str(
        getattr(config, "communication_artifact_link_secret_env", "")
        or "EVANDER_COMMUNICATION_ARTIFACT_LINK_SECRET"
    ).strip()
    if not env_name:
        raise CommunicationArtifactLinkError(
            "communication artifact link secret env is not configured"
        )
    return env_name


def communication_artifact_public_base_url(config: Any) -> str | None:
    configured = str(getattr(config, "communication_artifact_public_base_url", "") or "").strip()
    if configured:
        return configured
    for env_name in (
        "EVANDER_COMMUNICATION_ARTIFACT_PUBLIC_BASE_URL",
        "EVANDER_ADMIN_WEB_PUBLIC_BASE_URL",
    ):
        value = os.environ.get(env_name, "").strip()
        if value:
            return value
    return None


def _normalize_public_base_url(value: str | None) -> str:
    text = str(value or "").strip().rstrip("/")
    if not text:
        raise CommunicationArtifactLinkError(
            "communication artifact public base URL is not configured"
        )
    if not text.startswith(("https://", "http://")):
        raise CommunicationArtifactLinkError(
            "communication artifact public base URL must start with http:// or https://"
        )
    return text


def _secret_from_env(env_name: str) -> bytes:
    normalized_env_name = str(env_name or "").strip()
    if not normalized_env_name:
        raise CommunicationArtifactLinkError(
            "communication artifact link secret env is not configured"
        )
    secret = os.environ.get(normalized_env_name, "")
    if not secret:
        raise CommunicationArtifactLinkError(
            f"communication artifact link secret env {normalized_env_name!r} is not set"
        )
    return secret.encode("utf-8")


def _encode_signed_payload(payload: dict[str, Any], secret: bytes) -> str:
    payload_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    payload_part = _base64url_encode(payload_bytes)
    signature = hmac.new(secret, payload_part.encode("ascii"), hashlib.sha256).digest()
    return f"{payload_part}.{_base64url_encode(signature)}"


def _decode_signed_payload(token: str, secret: bytes) -> dict[str, Any]:
    try:
        payload_part, signature_part = str(token or "").split(".", 1)
    except ValueError as exc:
        raise CommunicationArtifactLinkError("download token is invalid") from exc
    expected = _base64url_encode(
        hmac.new(secret, payload_part.encode("ascii"), hashlib.sha256).digest()
    )
    if not hmac.compare_digest(signature_part, expected):
        raise CommunicationArtifactLinkError("download token signature is invalid")
    try:
        payload = json.loads(_base64url_decode(payload_part).decode("utf-8"))
    except Exception as exc:
        raise CommunicationArtifactLinkError("download token payload is invalid") from exc
    if not isinstance(payload, dict):
        raise CommunicationArtifactLinkError("download token payload is invalid")
    return payload


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _base64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(f"{value}{padding}".encode("ascii"))


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)

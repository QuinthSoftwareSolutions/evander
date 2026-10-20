"""Validate and publish one immutable PNG from host-owned communication storage."""

from datetime import UTC, datetime
from hashlib import sha256
from io import BytesIO

from PIL import Image

from evander.communication_artifact_links import (
    communication_artifact_link_secret_env,
    communication_artifact_public_base_url,
    issue_communication_artifact_download_link,
    verify_communication_artifact_download_token,
)
from evander.teams_outbound import TeamsImageVersion


def utc_now():
    return datetime.now(UTC)


def validated_png(store, session_id, artifact_id):
    staged = store.get(artifact_id)
    info = staged.metadata
    if info.session_id != session_id:
        raise ValueError("Progress image is not owned by this session")
    content = staged.blob_path.read_bytes()
    if (
        info.mime_type != "image/png"
        or not content.startswith(b"\x89PNG\r\n\x1a\n")
        or len(content) > 8 * 1024 * 1024
        or len(content) != info.byte_count
        or sha256(content).hexdigest() != info.sha256
    ):
        raise ValueError("Progress image MIME, size or checksum is invalid")
    try:
        with Image.open(BytesIO(content)) as image:
            if image.format != "PNG" or not (
                1 <= image.width <= 2048 and 1 <= image.height <= 1200
            ):
                raise ValueError("Progress image dimensions exceed the supported bounds")
            image.verify()
    except (OSError, SyntaxError):
        raise ValueError("Invalid progress PNG") from None
    return info, content


def publish_png(runtime, store, session_id, artifact_id, *, existing=None, now=None):
    info, _ = validated_png(store, session_id, artifact_id)
    artifact_id = info.artifact_id
    current = now or utc_now()
    if existing is not None and existing.artifact_id == artifact_id:
        if existing.sha256 != info.sha256 or existing.expires_at <= current:
            raise ValueError("Progress image source is expired or changed")
        return existing
    config = runtime.resolve_provider_config_for_session(session_id)
    link = issue_communication_artifact_download_link(
        artifact_id=artifact_id,
        artifact_expires_at=info.expires_at,
        public_base_url=communication_artifact_public_base_url(config),
        secret_env=communication_artifact_link_secret_env(config),
        ttl_seconds=config.communication_artifact_link_ttl_seconds,
        now=current,
    )
    # Tokens use whole Unix seconds; never advertise a longer usable lifetime.
    expiry = datetime.fromtimestamp(int(link.expires_at.timestamp()), UTC)
    if (expiry - current).total_seconds() < 300:
        raise ValueError("Progress images require at least five minutes of source lifetime")
    verify_communication_artifact_download_token(
        artifact_id=artifact_id,
        token=link.token,
        secret_env=communication_artifact_link_secret_env(config),
        now=current,
    )
    return TeamsImageVersion(
        artifact_id=artifact_id,
        sha256=info.sha256,
        expires_at=expiry,
        renew_at=current + (expiry - current) / 2,
        source_url=link.url,
        alt_text=str(info.metadata.get("status_text") or "Progress graphic")[:500],
    )

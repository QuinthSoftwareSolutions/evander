"""Host artifact publication shared by communication commands and gateway adapters."""

from __future__ import annotations

from datetime import datetime, timezone

from evander.communication_artifacts import CommunicationArtifactStore
from evander.communication_artifact_links import (
    communication_artifact_link_secret_env,
    communication_artifact_public_base_url,
    issue_communication_artifact_download_link,
)


class CommunicationArtifactPublication:
    def __init__(self, context, store=None):
        self.context = context
        self.store = store or CommunicationArtifactStore(context.config.runtime_dir)

    def artifact_payloads(self, artifact_ids):
        return [self.store.payload(key) for key in artifact_ids]

    def artifact_audit_summaries(self, artifact_ids):
        return [
            {
                k: v
                for k, v in item.items()
                if k
                in {"artifact_id", "sha256", "filename", "byte_count", "mime_type", "expires_at"}
            }
            for item in self.artifact_payloads(artifact_ids)
        ]

    @staticmethod
    def artifact_is_inline_teams_image(item):
        return str(item.get("mime_type") or "").split(";", 1)[0].lower() in {
            "image/png",
            "image/jpeg",
            "image/gif",
            "image/webp",
        }

    @staticmethod
    def artifact_has_teams_reference_url(item):
        candidates = [item]
        metadata = item.get("metadata") or {}
        candidates += [metadata, metadata.get("attachment_ref") or {}]
        candidates.append((metadata.get("attachment_ref") or {}).get("metadata") or {})
        return any(
            str(c.get(k) or "").startswith(("http://", "https://"))
            for c in candidates
            for k in ("contentUrl", "content_url")
        )

    @staticmethod
    def artifact_expires_at(item):
        value = item.get("expires_at")
        if not isinstance(value, datetime):
            value = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value

    def teams_artifacts_with_public_download_links(self, session_id, artifacts):
        return publish_teams_file_links(self.context, self, session_id, artifacts)


def publish_teams_file_links(context, helpers, session_id, artifacts):
    config = context.resolve_provider_config_for_session(session_id)
    updated, links, audit = [], [], []
    for artifact in artifacts:
        item = dict(artifact)
        if not helpers.artifact_is_inline_teams_image(
            item
        ) and not helpers.artifact_has_teams_reference_url(item):
            link = issue_communication_artifact_download_link(
                artifact_id=item["artifact_id"],
                artifact_expires_at=helpers.artifact_expires_at(item),
                public_base_url=communication_artifact_public_base_url(config),
                secret_env=communication_artifact_link_secret_env(config),
                ttl_seconds=config.communication_artifact_link_ttl_seconds,
            )
            item["download_url"] = link.url
            summary = {
                "artifact_id": link.artifact_id,
                "filename": item.get("normalized_safe_filename")
                or item.get("filename")
                or link.artifact_id,
                "expires_at": link.expires_at.isoformat(),
            }
            links.append({**summary, "url": link.url})
            audit.append(
                {
                    **summary,
                    "byte_count": int(item.get("byte_count") or 0),
                    "mime_type": item.get("mime_type"),
                }
            )
        updated.append(item)
    return updated, links, audit

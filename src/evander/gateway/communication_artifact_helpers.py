"""Communication artifact helpers for gateway tool registration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from mcp.server.fastmcp.exceptions import ToolError

from evander.communication_artifact_links import (
    CommunicationArtifactLinkError,
    communication_artifact_link_secret_env,
    communication_artifact_public_base_url,
    issue_communication_artifact_download_link,
)
from evander.communication_artifacts import (
    CommunicationArtifactStore,
    validate_artifact_id,
)
from evander.effects.providers.agent_exec import AgentExecEffectProvider
from evander.effects.providers.base import EffectRuntimeContext
from evander.effects.providers.schemas import AgentExecEffectDefinition
from evander.gateway.dependencies import GatewayToolDependencies, _compat_gateway_attr
from evander.models import CommunicationFileArtifact
from evander.policy import PolicyError
from evander.provider_runtime.adapters import AdminRuntimeProviderAdapter
from evander.runtime import RuntimeContext

_WORKSPACE_FILE_PAYLOAD_BEGIN = "__EVANDER_WORKSPACE_FILE_JSON_BEGIN__"
_WORKSPACE_FILE_PAYLOAD_END = "__EVANDER_WORKSPACE_FILE_JSON_END__"

_WORKSPACE_FILE_READER_SCRIPT = r"""
import base64
import json
import mimetypes
import pathlib
import stat
import sys

try:
    raw_path = sys.argv[1]
    max_bytes = int(sys.argv[2]) if len(sys.argv) > 2 else -1
    base = pathlib.Path("/home/agent").resolve()
    candidate = pathlib.Path(raw_path)
    if not candidate.is_absolute():
        candidate = base / candidate
    resolved = candidate.resolve(strict=True)
    resolved.relative_to(base)
    file_stat = resolved.stat()
    if not stat.S_ISREG(file_stat.st_mode):
        raise ValueError("source_path must resolve to a regular file")
    if max_bytes >= 0 and file_stat.st_size > max_bytes:
        raise ValueError(
            f"source_path exceeds communication_file_max_bytes: {file_stat.st_size} > {max_bytes}"
        )
    content = resolved.read_bytes()
    payload = {
        "source_path": str(resolved),
        "filename": resolved.name or "attachment",
        "mime_type": mimetypes.guess_type(resolved.name)[0],
        "byte_count": len(content),
        "content_base64": base64.b64encode(content).decode("ascii"),
    }
    print("__EVANDER_WORKSPACE_FILE_JSON_BEGIN__")
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    print("__EVANDER_WORKSPACE_FILE_JSON_END__")
except Exception as exc:
    print(f"communication_stage_workspace_file: {exc}", file=sys.stderr)
    raise SystemExit(2)
""".strip()

_TEAMS_INLINE_IMAGE_MIME_TYPES = {
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
}


@dataclass(frozen=True)
class CommunicationArtifactHelpers:
    context: RuntimeContext
    deps: GatewayToolDependencies

    def admin_runtime(self) -> Any:
        return self.deps.admin_runtime_factory()

    def admin_service(self) -> Any:
        return AdminRuntimeProviderAdapter(self.admin_runtime())

    def communication_artifact_store(self) -> CommunicationArtifactStore:
        return self.deps.communication_artifact_store_factory()

    def safe_artifact_id(self, value: str) -> str:
        try:
            return validate_artifact_id(value)
        except ValueError as exc:
            raise ToolError(str(exc)) from exc

    def artifact_to_model(self, record: Any) -> CommunicationFileArtifact:
        payload = record.model_dump(mode="json") if hasattr(record, "model_dump") else dict(record)
        return CommunicationFileArtifact.model_validate(payload)

    def normalize_workspace_source_path(self, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise PolicyError("source_path is required")
        if "\x00" in text:
            raise PolicyError("source_path contains a NUL byte")
        if "\\" in text:
            raise PolicyError("source_path must use POSIX separators")
        windows_path = PureWindowsPath(text)
        if windows_path.drive:
            raise PolicyError("source_path must be under /home/agent")
        posix_path = PurePosixPath(text)
        if any(part == ".." for part in posix_path.parts):
            raise PolicyError("source_path must not contain parent traversal")
        if posix_path.is_absolute():
            try:
                posix_path.relative_to(PurePosixPath("/home/agent"))
            except ValueError as exc:
                raise PolicyError("source_path must be under /home/agent") from exc
        return text

    def communication_file_max_bytes_for_session(self, session: Any) -> int | None:
        session_value = getattr(session, "communication_file_max_bytes", None)
        if session_value is not None:
            return int(session_value)
        effective_value = self.context.policy.effective_policy_for_session(
            session
        ).communication_file_max_bytes
        return None if effective_value is None else int(effective_value)

    def parse_workspace_file_payload(self, stdout: str) -> dict[str, Any]:
        text = str(stdout or "")
        start = text.find(_WORKSPACE_FILE_PAYLOAD_BEGIN)
        end = text.find(_WORKSPACE_FILE_PAYLOAD_END)
        if start >= 0 and end > start:
            json_text = text[start + len(_WORKSPACE_FILE_PAYLOAD_BEGIN) : end].strip()
        else:
            json_text = text.strip()
        try:
            payload = json.loads(json_text)
        except json.JSONDecodeError as exc:
            raise ToolError(
                "Workspace file reader did not return a valid artifact payload"
            ) from exc
        if not isinstance(payload, dict):
            raise ToolError("Workspace file reader returned an invalid artifact payload")
        return payload

    def read_agent_workspace_file(
        self,
        session: Any,
        *,
        source_path: str,
    ) -> dict[str, Any]:
        max_bytes = self.communication_file_max_bytes_for_session(session)
        effect = AgentExecEffectDefinition(
            id="communication-stage-workspace-file",
            display_name="Stage workspace communication file",
            type="effect:agent:exec",
            session_id=session.session_id,
            command=[
                "python3",
                "-c",
                _WORKSPACE_FILE_READER_SCRIPT,
                source_path,
                str(max_bytes if max_bytes is not None else -1),
            ],
            working_directory="/home/agent",
            timeout_seconds=30,
            check=True,
        )
        effect_context = EffectRuntimeContext(
            provider_config=self.context.resolve_provider_config_for_session(
                session.session_id
            ).model_dump(mode="json"),
            admin_service=self.admin_service(),
            metadata={"directory_path": session.directory_path or "root"},
        )
        provider_cls = _compat_gateway_attr("AgentExecEffectProvider", AgentExecEffectProvider)
        provider = provider_cls()
        provider.initialize(effect_definition=effect, runtime_context=effect_context)
        exec_result = provider.execute()
        return self.parse_workspace_file_payload(str(exec_result.get("stdout") or ""))

    def load_artifacts(self, artifact_ids: list[str]) -> list[CommunicationFileArtifact]:
        artifacts: list[CommunicationFileArtifact] = []
        store = self.communication_artifact_store()
        for artifact_id in artifact_ids:
            safe_id = self.safe_artifact_id(artifact_id)
            try:
                artifacts.append(self.artifact_to_model(store.get(safe_id).metadata))
            except FileNotFoundError as exc:
                raise ToolError(str(exc)) from exc
        return artifacts

    def artifact_payloads(self, artifact_ids: list[str]) -> list[dict[str, Any]]:
        payloads: list[dict[str, Any]] = []
        store = self.communication_artifact_store()
        for artifact_id in artifact_ids:
            safe_id = self.safe_artifact_id(artifact_id)
            try:
                payloads.append(store.payload(safe_id))
            except FileNotFoundError as exc:
                raise ToolError(str(exc)) from exc
        return payloads

    def artifact_audit_summaries(self, artifact_ids: list[str]) -> list[dict[str, Any]]:
        return [
            {
                "provider": artifact.provider,
                "message_id": artifact.source_message_id,
                "attachment_id": artifact.source_attachment_id,
                "artifact_id": artifact.artifact_id,
                "byte_count": artifact.byte_count,
                "size": artifact.byte_count,
                "mime_type": artifact.mime_type,
                "filename": artifact.filename,
                "normalized_safe_filename": artifact.normalized_safe_filename,
                "expires_at": artifact.expires_at,
            }
            for artifact in self.load_artifacts(artifact_ids)
        ]

    def artifact_mime_type(self, artifact: dict[str, Any]) -> str:
        return str(artifact.get("mime_type") or "").split(";", 1)[0].strip().lower()

    def artifact_is_inline_teams_image(self, artifact: dict[str, Any]) -> bool:
        return self.artifact_mime_type(artifact) in _TEAMS_INLINE_IMAGE_MIME_TYPES

    def artifact_has_teams_reference_url(self, artifact: dict[str, Any]) -> bool:
        candidates: list[Any] = [artifact.get("contentUrl"), artifact.get("content_url")]
        metadata = artifact.get("metadata")
        if isinstance(metadata, dict):
            candidates.extend([metadata.get("contentUrl"), metadata.get("content_url")])
            attachment_ref = metadata.get("attachment_ref")
            if isinstance(attachment_ref, dict):
                candidates.extend(
                    [attachment_ref.get("contentUrl"), attachment_ref.get("content_url")]
                )
                ref_metadata = attachment_ref.get("metadata")
                if isinstance(ref_metadata, dict):
                    candidates.extend(
                        [ref_metadata.get("contentUrl"), ref_metadata.get("content_url")]
                    )
        return any(
            str(candidate or "").strip().startswith(("http://", "https://"))
            for candidate in candidates
        )

    def artifact_expires_at(self, artifact: dict[str, Any]) -> datetime:
        raw_value = artifact.get("expires_at")
        if isinstance(raw_value, datetime):
            value = raw_value
        else:
            text = str(raw_value or "").strip()
            if not text:
                raise ToolError("Communication file artifact does not include an expiry")
            try:
                value = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ToolError("Communication file artifact has an invalid expiry") from exc
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def teams_artifacts_with_public_download_links(
        self,
        session_id: str,
        artifacts: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        resolved_config = self.context.resolve_provider_config_for_session(session_id)
        updated_artifacts: list[dict[str, Any]] = []
        published_links: list[dict[str, Any]] = []
        audit_links: list[dict[str, Any]] = []
        for artifact in artifacts:
            updated = dict(artifact)
            if not self.artifact_is_inline_teams_image(
                updated
            ) and not self.artifact_has_teams_reference_url(updated):
                try:
                    link = issue_communication_artifact_download_link(
                        artifact_id=str(updated.get("artifact_id") or ""),
                        artifact_expires_at=self.artifact_expires_at(updated),
                        public_base_url=communication_artifact_public_base_url(resolved_config),
                        secret_env=communication_artifact_link_secret_env(resolved_config),
                        ttl_seconds=resolved_config.communication_artifact_link_ttl_seconds,
                    )
                except CommunicationArtifactLinkError as exc:
                    raise ToolError(str(exc)) from exc
                updated["download_url"] = link.url
                published_links.append(
                    {
                        "artifact_id": link.artifact_id,
                        "filename": str(
                            updated.get("normalized_safe_filename")
                            or updated.get("filename")
                            or link.artifact_id
                        ),
                        "url": link.url,
                        "expires_at": link.expires_at.isoformat(),
                    }
                )
                audit_links.append(
                    {
                        "artifact_id": link.artifact_id,
                        "filename": str(
                            updated.get("normalized_safe_filename")
                            or updated.get("filename")
                            or link.artifact_id
                        ),
                        "byte_count": int(updated.get("byte_count") or 0),
                        "mime_type": updated.get("mime_type"),
                        "expires_at": link.expires_at.isoformat(),
                    }
                )
            updated_artifacts.append(updated)
        return updated_artifacts, published_links, audit_links

    def require_ado_artifact_send(
        self, session: Any, artifact_payloads: list[dict[str, Any]]
    ) -> None:
        if not artifact_payloads:
            return
        self.context.policy.require_communication_file_send_enabled(session)
        for artifact in artifact_payloads:
            artifact_session_id = artifact.get("session_id")
            if artifact_session_id is not None and str(artifact_session_id) != session.session_id:
                raise PolicyError(
                    f"Communication file artifact is not owned by session {session.session_id}"
                )
            self.context.policy.require_communication_file_content(
                session,
                filename=str(
                    artifact.get("normalized_safe_filename")
                    or artifact.get("filename")
                    or "attachment"
                ),
                mime_type=artifact.get("mime_type"),
                byte_count=int(artifact.get("byte_count") or 0),
            )

    def ado_artifacts_for_ids(self, session: Any, artifact_ids: list[str]) -> list[dict[str, Any]]:
        artifacts = self.artifact_payloads(artifact_ids)
        self.require_ado_artifact_send(session, artifacts)
        return artifacts

    def ado_artifact_approval_payload(self, payload: Any) -> dict[str, Any]:
        if hasattr(payload, "model_dump"):
            return payload.model_dump(mode="json", exclude={"request_id"}, exclude_none=True)
        if isinstance(payload, dict):
            return {key: value for key, value in payload.items() if key != "request_id"}
        return {}

    def communication_target_audit_fields(
        self,
        *,
        provider: str,
        mailbox_id: str | None = None,
        channel_id: str | None = None,
        channel_target: str | None = None,
        approval_request_id: str | None = None,
        approval_decision: str | None = None,
    ) -> dict[str, Any]:
        return {
            "provider": provider,
            "mailbox_id": mailbox_id,
            "channel_id": channel_id,
            "channel_target": channel_target,
            "approval_request_id": approval_request_id,
            "approval_decision": approval_decision,
            "decision": "allowed",
        }

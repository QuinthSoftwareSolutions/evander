"""MCP tools for artifacts."""

from __future__ import annotations
import base64
from collections.abc import Callable
from typing import Any
from mcp.server.mcpserver.exceptions import ToolError
from evander.gateway.dependencies import _compat_gateway_attr
from evander.events.agent_artifacts import (
    AGENT_ARTIFACT_STAGED_EVENT_SOURCE_TYPE,
    AGENT_ARTIFACT_STAGED_EVENT_TYPE,
)
from evander.events.internal import InternalEventSource
from evander.models import (
    CommunicationCopyArtifactToWorkspaceResponse,
    CommunicationCopyArtifactToWorkspaceRequest,
    CommunicationStageWorkspaceFileRequest,
    StageArtifactRequest,
    CommunicationStageWorkspaceFileResponse,
    StageArtifactResponse,
    ToolName,
)
from evander.policy import PolicyError
from evander.communication_artifacts import normalize_safe_filename
from evander.effects.providers.artifacts import ArtifactStageEffectProvider
from evander.effects.providers.agent_copy_files import AgentCopyFilesEffectProvider
from evander.effects.providers.base import EffectRuntimeContext
from evander.effects.providers.schemas import (
    AgentCopyFilesEffectDefinition,
    ArtifactStageEffectDefinition,
    VmCopyFilesItemDefinition,
)
from evander.orchestration.internal_events import publish_internal_event_for_runtime
from evander.session_artifacts import normalize_artifact_relative_path

from .communication_support import CommunicationToolHelpers


def create_artifacts_tools(
    context: Any, helpers: CommunicationToolHelpers
) -> dict[str, Callable[..., Any]]:
    _admin_service = helpers.admin_service
    _artifact_to_model = helpers.artifact_to_model
    _communication_artifact_store = helpers.communication_artifact_store
    _normalize_workspace_source_path = helpers.normalize_workspace_source_path
    _read_agent_workspace_file = helpers.read_agent_workspace_file
    _safe_artifact_id = helpers.safe_artifact_id
    audit_failure = helpers.audit_failure
    audit_success = helpers.audit_success

    def stage_artifact(
        session_id: str,
        path: str,
        destination_path: str | None = None,
    ) -> dict[str, Any]:
        """Copy a file or directory under /home/agent into the session /artifacts folder."""
        request = StageArtifactRequest(
            session_id=session_id,
            path=path,
            destination_path=destination_path,
        )
        args = request.model_dump(mode="json", exclude_none=True)
        try:
            session = context.policy.require_tool(session_id, ToolName.STAGE_ARTIFACT)
            effect = ArtifactStageEffectDefinition(
                id="stage-artifact",
                display_name="Stage artifact",
                type="effect:artifact:stage",
                session_id=session_id,
                path=request.path,
                destination_path=request.destination_path,
            )
            effect_context = EffectRuntimeContext(
                provider_config=context.resolve_provider_config_for_session(session_id).model_dump(
                    mode="json"
                ),
                admin_service=_admin_service(),
                metadata={"directory_path": session.directory_path or "root"},
            )
            provider_cls = _compat_gateway_attr(
                "ArtifactStageEffectProvider",
                ArtifactStageEffectProvider,
            )
            provider = provider_cls()
            provider.initialize(effect_definition=effect, runtime_context=effect_context)
            stage_result = provider.execute()
            response = StageArtifactResponse(
                success=bool(stage_result.get("success", True)),
                message=str(stage_result.get("message") or ""),
                session_id=str(stage_result.get("session_id") or session_id),
                vm_name=str(stage_result.get("vm_name") or "") or None,
                execution_backend=str(stage_result.get("execution_backend") or "") or None,
                source_path=str(stage_result.get("source_path") or ""),
                artifact_path=str(stage_result.get("artifact_path") or ""),
                artifacts_root=str(stage_result.get("artifacts_root") or ""),
                copied_count=int(stage_result.get("copied_count") or 0),
                byte_count=int(stage_result.get("byte_count") or 0),
                result=dict(stage_result),
            )
            if response.success:
                artifact_path = normalize_artifact_relative_path(response.artifact_path)
                publish_internal_event_for_runtime(
                    context.config.runtime_dir,
                    event_type=AGENT_ARTIFACT_STAGED_EVENT_TYPE,
                    payload={
                        "session_id": response.session_id,
                        "artifact_path": artifact_path,
                        "source_path": response.source_path,
                        "artifacts_root": response.artifacts_root,
                        "copied_count": response.copied_count,
                        "byte_count": response.byte_count,
                        "vm_name": response.vm_name,
                        "execution_backend": response.execution_backend,
                        "directory_path": session.directory_path or "root",
                    },
                    correlation_key=response.session_id,
                    source=InternalEventSource(
                        source_type=AGENT_ARTIFACT_STAGED_EVENT_SOURCE_TYPE,
                        source_name=ToolName.STAGE_ARTIFACT.value,
                    ),
                    base_config=context.config,
                )
            result = response.model_dump(mode="json")
            audit_success(
                session_id,
                ToolName.STAGE_ARTIFACT,
                args,
                {
                    "decision": "allowed",
                    "source_path": response.source_path,
                    "artifact_path": response.artifact_path,
                    "artifacts_root": response.artifacts_root,
                    "copied_count": response.copied_count,
                    "byte_count": response.byte_count,
                },
            )
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.STAGE_ARTIFACT, args, str(exc))
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.STAGE_ARTIFACT,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    def communication_copy_artifact_to_workspace(
        session_id: str,
        artifact_id: str,
        destination_filename: str | None = None,
        overwrite: bool = True,
    ) -> dict[str, Any]:
        """Copy a session-owned downloaded artifact into the agent workspace.

        Open the returned destination_path with your local image viewer to inspect
        screenshots. Use a unique destination_filename for each image when provider
        filenames repeat. Requires file reception and this tool's session grant.
        """
        request = CommunicationCopyArtifactToWorkspaceRequest(
            session_id=session_id,
            artifact_id=artifact_id,
            destination_filename=destination_filename,
            overwrite=overwrite,
        )
        args = request.model_dump(mode="json")
        try:
            session = context.policy.require_tool(
                session_id, ToolName.COMMUNICATION_COPY_ARTIFACT_TO_WORKSPACE
            )
            context.policy.require_communication_file_receive_enabled(session)
            staged = _communication_artifact_store().get(_safe_artifact_id(artifact_id))
            artifact = _artifact_to_model(staged.metadata)
            if artifact.session_id != session_id:
                raise ToolError("artifact_id is not owned by this session")
            requested_name = (
                destination_filename or artifact.normalized_safe_filename or artifact.filename
            )
            safe_name = context.policy.require_safe_communication_filename(session, requested_name)
            # Provider filenames are never used as paths; the only destination is this
            # controlled workspace drop directory plus a policy-validated basename.
            safe_name = normalize_safe_filename(safe_name)
            destination_path = f"/home/agent/communication-artifacts/{safe_name}"

            effect = AgentCopyFilesEffectDefinition(
                id="communication-copy-artifact-to-workspace",
                display_name="Copy communication artifact to workspace",
                type="effect:agent:copy-files",
                session_id=session_id,
                files=[
                    VmCopyFilesItemDefinition(
                        source_path=str(staged.blob_path),
                        destination_path=destination_path,
                        overwrite=overwrite,
                    )
                ],
            )
            effect_context = EffectRuntimeContext(
                provider_config=context.resolve_provider_config_for_session(session_id).model_dump(
                    mode="json"
                ),
                admin_service=_admin_service(),
                metadata={"directory_path": session.directory_path or "root"},
            )
            provider_cls = _compat_gateway_attr(
                "AgentCopyFilesEffectProvider",
                AgentCopyFilesEffectProvider,
            )
            provider = provider_cls()
            provider.initialize(effect_definition=effect, runtime_context=effect_context)
            copy_result = provider.execute()
            response = CommunicationCopyArtifactToWorkspaceResponse(
                artifact=artifact,
                destination_path=destination_path,
                copied_count=int(copy_result.get("copied_count") or 0),
                copied_paths=list(copy_result.get("copied_paths") or []),
                result=copy_result,
            )
            result = response.model_dump(mode="json")
            audit_success(
                session_id,
                ToolName.COMMUNICATION_COPY_ARTIFACT_TO_WORKSPACE,
                args,
                {
                    "decision": "allowed",
                    "artifact_id": artifact.artifact_id,
                    "destination_path": destination_path,
                    "copied_count": response.copied_count,
                    "copied_paths": response.copied_paths,
                },
            )
            return result
        except PolicyError as exc:
            audit_failure(
                session_id, ToolName.COMMUNICATION_COPY_ARTIFACT_TO_WORKSPACE, args, str(exc)
            )
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.COMMUNICATION_COPY_ARTIFACT_TO_WORKSPACE,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    def communication_stage_workspace_file(
        session_id: str,
        source_path: str,
        filename: str | None = None,
        mime_type: str | None = None,
    ) -> dict[str, Any]:
        """Stage a regular file under /home/agent for outbound file-send tools."""
        request = CommunicationStageWorkspaceFileRequest(
            session_id=session_id,
            source_path=source_path,
            filename=filename,
            mime_type=mime_type,
        )
        args = request.model_dump(mode="json", exclude_none=True)
        try:
            session = context.policy.require_tool(
                session_id, ToolName.COMMUNICATION_STAGE_WORKSPACE_FILE
            )
            context.policy.require_communication_file_send_enabled(session)
            normalized_source_path = _normalize_workspace_source_path(source_path)
            if filename is not None:
                context.policy.require_safe_communication_filename(session, filename)
            payload = _read_agent_workspace_file(
                session,
                source_path=normalized_source_path,
            )
            content_base64 = str(payload.get("content_base64") or "")
            if not content_base64:
                raise ToolError("Workspace file reader did not return file content")
            try:
                content = base64.b64decode(content_base64, validate=True)
            except Exception as exc:
                raise ToolError("Workspace file reader returned invalid base64 content") from exc
            resolved_source_path = str(payload.get("source_path") or normalized_source_path)
            resolved_filename = str(filename or payload.get("filename") or "attachment")
            resolved_mime_type = str(mime_type or payload.get("mime_type") or "").strip() or None
            context.policy.require_communication_file_content(
                session,
                filename=resolved_filename,
                mime_type=resolved_mime_type,
                byte_count=len(content),
            )
            record = _communication_artifact_store().stage(
                provider="workspace",
                session_id=session_id,
                content=content,
                original_filename=resolved_filename,
                mime_type=resolved_mime_type,
                metadata={
                    "source": "agent_workspace",
                    "source_path": resolved_source_path,
                    "requested_source_path": normalized_source_path,
                },
            )
            artifact = _artifact_to_model(record)
            response = CommunicationStageWorkspaceFileResponse(
                artifact=artifact,
                source_path=resolved_source_path,
            )
            result = response.model_dump(mode="json")
            audit_success(
                session_id,
                ToolName.COMMUNICATION_STAGE_WORKSPACE_FILE,
                args,
                {
                    "decision": "allowed",
                    "artifact_id": artifact.artifact_id,
                    "source_path": resolved_source_path,
                    "byte_count": artifact.byte_count,
                    "size": artifact.byte_count,
                    "mime_type": artifact.mime_type,
                    "filename": artifact.filename,
                    "sha256": artifact.sha256,
                },
            )
            return result
        except PolicyError as exc:
            audit_failure(session_id, ToolName.COMMUNICATION_STAGE_WORKSPACE_FILE, args, str(exc))
            raise
        except Exception as exc:
            audit_failure(
                session_id,
                ToolName.COMMUNICATION_STAGE_WORKSPACE_FILE,
                args,
                str(exc),
                decision="error",
                error_class="unexpected_error",
            )
            raise

    return {
        "stage_artifact": stage_artifact,
        "communication_copy_artifact_to_workspace": communication_copy_artifact_to_workspace,
        "communication_stage_workspace_file": communication_stage_workspace_file,
    }

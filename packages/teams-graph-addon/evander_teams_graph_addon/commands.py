"""Shared, session-authorized host commands for MCP and deterministic effects."""

from __future__ import annotations

import hashlib
import re
from typing import Any, Callable

from evander.models import AuditEvent, ToolName
from evander.policy import PolicyError
from evander.integration_services.teams import parse_teams_target
from evander.communication_publication import CommunicationArtifactPublication
from evander.models import (
    CommunicationSendMessageWithAttachmentsRequest,
    CommunicationSendMessageWithAttachmentsResponse,
)
from evander.integration_services.teams import resolve_teams_send_target
from .durable import OperationJournal
from .media import publish_png, validated_png


class TeamsMessageCommands:
    def __init__(
        self,
        runtime,
        *,
        audit_success=None,
        audit_failure=None,
        workflow_context=None,
        artifact_helpers=None,
    ):
        self.runtime = runtime
        self.audit_success = audit_success
        self.audit_failure = audit_failure
        self.workflow_context = workflow_context or {}
        self.artifacts = artifact_helpers

    def _publication(self):
        return self.artifacts or CommunicationArtifactPublication(self.runtime)

    def _artifact_store(self):
        helper = self._publication()
        return (
            helper.communication_artifact_store()
            if hasattr(helper, "communication_artifact_store")
            else helper.store
        )

    def _audit(self, session_id, args, result=None, error=None, tool=ToolName.TEAMS_UPDATE_MESSAGE):
        session_id = session_id if isinstance(session_id, str) else None
        denied = isinstance(error, PolicyError)
        reason = re.sub(r"https?://[^\s'\"<>]+", "[redacted]", str(error)) if error else None
        if error is None and self.audit_success is not None:
            self.audit_success(session_id, tool, args, result)
            return
        if error is not None and self.audit_failure is not None:
            self.audit_failure(
                session_id,
                tool,
                args,
                reason,
                decision="denied" if denied else "error",
                error_class="policy_error" if denied else "teams_edit_error",
            )
            return
        session = self.runtime.session_repository.get_session(session_id) if session_id else None
        self.runtime.audit.log(
            AuditEvent(
                event_type="tool_call",
                session_id=session_id or None,
                tool_name=tool.value,
                provider="teams",
                directory_path=session.directory_path if session else None,
                vm_id=session.vm_id if session else None,
                workflow_run_id=self.workflow_context.get("workflow_run_id"),
                correlation_id=self.workflow_context.get("correlation_id"),
                decision=("denied" if denied else "error") if error else "allowed",
                reason=reason,
                args={**args, "workflow": self.workflow_context},
                result=result or {"error_class": "policy_error" if denied else "teams_edit_error"},
            )
        )

    def update_message(
        self,
        session_id: str,
        channel_target: str,
        message_id: str,
        message: str,
        thread_id: str | None = None,
        expected_revision: int | None = None,
        *,
        image_artifact_id: str | None = None,
        clear_image: bool = False,
        request_id: str | None = None,
        authorize_session: Callable[[Any], None] | None = None,
    ) -> dict[str, Any]:
        args = {
            "session_id": session_id,
            "channel_target": channel_target,
            "message_id": message_id,
            "thread_id": thread_id,
            "expected_revision": expected_revision,
            "message_sha256": hashlib.sha256(str(message).encode()).hexdigest(),
            "image_artifact_id": image_artifact_id,
            "clear_image": clear_image,
        }
        try:
            if not isinstance(session_id, str) or not session_id.strip():
                raise PolicyError("A real active session_id is required")
            session = self.runtime.policy.require_tool(session_id, ToolName.TEAMS_UPDATE_MESSAGE)
            if authorize_session is not None:
                try:
                    authorize_session(session)
                except ValueError as exc:
                    raise PolicyError(str(exc)) from exc
            if not isinstance(message, str) or not message.strip():
                raise ValueError("message must be non-empty replacement text")
            if not isinstance(message_id, str) or not message_id.strip():
                raise ValueError("message_id must be non-empty")
            if expected_revision is not None and (
                type(expected_revision) is not int or expected_revision < 1
            ):
                raise ValueError("expected_revision must be a positive integer")
            # Resolve configured target references without resolving user aliases
            # or creating chats. Either capability permits locating the receipt;
            # the actual recorded parent decides the final required capability.
            try:
                target = self.runtime.policy.require_teams_channel_target(
                    session, channel_target, capability="write"
                )
            except PolicyError:
                target = self.runtime.policy.require_teams_channel_target(
                    session, channel_target, capability="reply"
                )
            receipt = self.runtime.teams_messages.get_outbound_receipt(target, message_id)
            if receipt is None or receipt.session_id != session.session_id:
                raise PolicyError("No outbound ownership receipt for this session and conversation")
            parsed = parse_teams_target(target)
            if (parsed.kind == "chat" and thread_id is not None) or (
                thread_id is not None and thread_id != receipt.thread_id
            ):
                raise PolicyError("thread_id does not match the recorded Teams message")
            capability = (
                "reply" if parsed.kind == "channel" and receipt.thread_id != message_id else "write"
            )
            self.runtime.policy.require_teams_channel_target(
                session, channel_target, capability=capability
            )
            if type(clear_image) is not bool or (
                image_artifact_id is not None
                and (not isinstance(image_artifact_id, str) or not image_artifact_id.strip())
            ):
                raise ValueError(
                    "image_artifact_id must be nonempty and clear_image must be boolean"
                )
            if image_artifact_id is not None and clear_image:
                raise ValueError("Replacement image and clear_image are mutually exclusive")
            image_options = {}
            if image_artifact_id is not None or clear_image:
                if receipt.image_slot is None:
                    raise PolicyError("No editable image slot was recorded for this message")
                image_options["clear_image"] = clear_image
            if image_artifact_id is not None:
                self.runtime.policy.require_tool(session_id, ToolName.TEAMS_POST_FILE)
                store = self._artifact_store()
                artifacts = [store.payload(image_artifact_id)]
                self.runtime.policy.require_communication_file_artifacts(
                    session,
                    artifacts,
                    provider="teams",
                    channel_target=channel_target,
                    capability=capability,
                )
                self.runtime.policy.require_communication_file_send_approval(
                    session,
                    request_id=request_id,
                    payload={
                        "session_id": session_id,
                        "provider": "teams",
                        "operation": "teams_update_message",
                        "channel_target": channel_target,
                        "message_id": message_id,
                        "message": message,
                        "thread_id": receipt.thread_id,
                        "artifact_ids": [image_artifact_id],
                    },
                )
                state = self.runtime.teams_messages.get_outbound_edit_state(target, message_id)
                existing = (
                    state.pending.image
                    if state and state.pending
                    else state.image
                    if state
                    else None
                )
                image_options["image_version"] = publish_png(
                    self.runtime,
                    store,
                    session_id,
                    image_artifact_id,
                    existing=existing,
                )

            # The service decides whether a source needs verification from state
            # read under its message lock. A text-only caller can encounter an
            # image intent admitted after this command began, so always provide
            # the verifier; load artifact storage only if it is actually called.
            def verify_source(version):
                info, _ = validated_png(self._artifact_store(), session_id, version.artifact_id)
                if info.sha256 != version.sha256:
                    raise ValueError("Published progress image checksum changed")

            image_options["verify_image_source"] = verify_source
            result = self.runtime.teams_service_for_session(session_id).update_message(
                session_id=session_id,
                channel_target=target,
                message_id=message_id,
                message=message,
                thread_id=thread_id,
                expected_revision=expected_revision,
                **image_options,
            )
        except Exception as exc:
            self._audit(session_id, args, error=exc)
            raise
        self._audit(session_id, args, result=result)
        return result

    def post_file(
        self,
        session_id,
        channel_target,
        message,
        artifact_ids,
        thread_id=None,
        request_id=None,
        *,
        editable_image_artifact_id=None,
        idempotency_key=None,
        authorize_session=None,
    ):
        if thread_id is not None:
            if not isinstance(thread_id, str):
                raise ValueError("thread_id must be a string")
            thread_id = thread_id.strip() or None
        request = CommunicationSendMessageWithAttachmentsRequest(
            session_id=session_id,
            provider="teams",
            channel_target=channel_target,
            message=message,
            artifact_ids=artifact_ids,
            thread_id=thread_id,
            request_id=request_id,
        )
        args = request.model_dump(mode="json")
        if editable_image_artifact_id is not None:
            args["editable_image_artifact_id"] = editable_image_artifact_id
        try:
            session = self.runtime.policy.require_tool(session_id, ToolName.TEAMS_POST_FILE)
            if authorize_session is not None:
                try:
                    authorize_session(session)
                except ValueError as exc:
                    raise PolicyError(str(exc)) from exc
            helper = self._publication()
            artifacts = helper.artifact_payloads(artifact_ids)
            args["artifacts"] = helper.artifact_audit_summaries(artifact_ids)
            capability = "reply" if thread_id is not None else "write"
            self.runtime.policy.require_communication_file_artifacts(
                session,
                artifacts,
                provider="teams",
                channel_target=channel_target,
                allow_teams_user_target=editable_image_artifact_id is None,
                capability=capability,
            )
            target = self.runtime.policy.require_teams_send_target(
                session, channel_target, capability=capability
            )
            if parse_teams_target(target).kind == "user" and (
                thread_id is not None or idempotency_key is not None
            ):
                raise PolicyError("Idempotent Teams posts require an existing channel or chat")
            approval_payload = request.model_dump(
                mode="json", exclude={"request_id"}, exclude_none=True
            )
            if editable_image_artifact_id is not None:
                self.runtime.policy.require_tool(session_id, ToolName.TEAMS_UPDATE_MESSAGE)
                if artifact_ids.count(editable_image_artifact_id) != 1:
                    raise ValueError(
                        "editable_image_artifact_id must select exactly one supplied artifact"
                    )
                approval_payload["editable_image_artifact_id"] = editable_image_artifact_id
            approval = self.runtime.policy.require_communication_file_send_approval(
                session,
                request_id=request_id,
                payload=approval_payload,
            )
            audit_result = {
                "provider": "teams",
                "mailbox_id": None,
                "channel_id": None,
                "channel_target": target,
                "approval_request_id": request_id,
                "approval_decision": None if approval is None else approval.status.value,
                "decision": "allowed",
                "artifacts": args["artifacts"],
            }
            if idempotency_key is not None and (
                not isinstance(idempotency_key, str)
                or not idempotency_key.strip()
                or len(idempotency_key) > 200
            ):
                raise ValueError("idempotency_key must contain 1 to 200 characters")
            service = self.runtime.teams_service_for_session(session_id)
            target = resolve_teams_send_target(service, target)
            journal = OperationJournal(self.runtime.config.runtime_dir, "creation")
            key = journal.key(session_id, target, thread_id, idempotency_key)
            from contextlib import nullcontext

            with journal.lock(key) if idempotency_key is not None else nullcontext():
                import json

                identity = hashlib.sha256(
                    json.dumps(
                        {**approval_payload, "channel_target": target}, sort_keys=True
                    ).encode()
                ).hexdigest()
                prior = journal.read(key) if idempotency_key is not None else None
                if prior:
                    if prior["identity"] != identity:
                        raise ValueError("Creation idempotency key conflicts with another post")
                    if prior.get("result") is not None:
                        result = prior["result"]
                        self._audit(
                            session_id,
                            args,
                            result={
                                **audit_result,
                                "message_id": result.get("message_id"),
                                "status": "unchanged",
                                "published_links": [
                                    {k: v for k, v in link.items() if k != "url"}
                                    for link in result.get("published_links", [])
                                ],
                            },
                            tool=ToolName.TEAMS_POST_FILE,
                        )
                        return result
                    raise ValueError(
                        "Teams post outcome is uncertain; operator reconciliation is required before another POST"
                    )
                image = None
                if editable_image_artifact_id is not None:
                    image = publish_png(
                        self.runtime, self._artifact_store(), session_id, editable_image_artifact_id
                    )
                artifacts, links, link_audit = helper.teams_artifacts_with_public_download_links(
                    session_id, artifacts
                )
                if idempotency_key is not None:
                    journal.write(key, {"identity": identity, "status": "uncertain"})
                options = {"image_version": image} if image else {}
                if idempotency_key is not None:
                    options["post_once"] = True
                posted = service.post_file(
                    message, target, session_id, artifacts, thread_id=thread_id, **options
                )
                if links:
                    posted["published_links"] = links
                response = CommunicationSendMessageWithAttachmentsResponse(
                    provider="teams",
                    status=str(posted.get("status", "posted")),
                    message_id=posted.get("message_id"),
                    artifact_ids=artifact_ids,
                    published_links=links,
                    result=posted,
                ).model_dump(mode="json")
                if "message_revision" in posted:
                    response["message_revision"] = posted["message_revision"]
                if image:
                    response.update(
                        image_sha256=image.sha256,
                        image_expires_at=image.expires_at.isoformat(),
                        image_renew_at=image.renew_at.isoformat(),
                        image_editable=posted.get("image_editable", False),
                    )
                if idempotency_key is not None:
                    journal.write(
                        key, {"identity": identity, "status": "completed", "result": response}
                    )
            self._audit(
                session_id,
                args,
                result={
                    **audit_result,
                    "message_id": response["message_id"],
                    "artifact_ids": artifact_ids,
                    "published_links": link_audit,
                },
                tool=ToolName.TEAMS_POST_FILE,
            )
            return response
        except Exception as exc:
            self._audit(session_id, args, error=exc, tool=ToolName.TEAMS_POST_FILE)
            raise

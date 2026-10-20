"""Durable, optional progress notifications; independent of document production."""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import re

from evander.communication_artifacts import CommunicationArtifactStore
from evander.events.scheduled import ScheduledInternalEventStore
from evander.models import ToolName
from evander.integration_services.teams import _teams_message_to_html
from evander.teams_outbound import outbound_edit_result

from .commands import TeamsMessageCommands
from .durable import OperationJournal
from .progress_images import ProgressSnapshot, render_progress_png
from .media import validated_png

TERMINAL = {"delivered", "cancelled"}


def verified_progress_counts(
    chapters, completions, fragment_hashes, *, generation_id, outline_sha256
):
    """Adapter for already-verified host completion records, never for file-presence counts."""
    ids = [section for sections in chapters.values() for section in sections]
    if len(ids) != len(set(ids)):
        raise ValueError("Approved section IDs must be unique")
    complete = {
        r["section_id"]
        for r in completions
        if r.get("generation_id") == generation_id
        and r.get("outline_sha256") == outline_sha256
        and r.get("status") == "completed"
        and not r.get("superseded")
        and re.fullmatch(r"[a-f0-9]{64}", str(r.get("sha256") or ""))
        and r.get("sha256") == fragment_hashes.get(r.get("section_id"))
        and r.get("section_id") in ids
    }
    return {
        "chapters_total": len(chapters),
        "sections_total": len(ids),
        "sections_completed": len(complete),
        "chapters_completed": sum(
            bool(items) and set(items) <= complete for items in chapters.values()
        ),
    }


def delivery_links(evidence):
    """Require two receipts for the current source/output hashes before terminal delivery."""
    from urllib.parse import urlparse

    if not isinstance(evidence, dict):
        raise ValueError("Delivered progress requires current Word and PDF delivery evidence")
    source = evidence.get("document_sha256")
    if not re.fullmatch(r"[a-f0-9]{64}", str(source or "")):
        raise ValueError("Invalid delivered document digest")
    links = []
    for fmt, label in (("docx", "Word"), ("pdf", "PDF")):
        receipt = (evidence.get("receipts") or {}).get(fmt) or {}
        digest = (evidence.get("outputs") or {}).get(fmt)
        if (
            receipt.get("status") != "posted"
            or receipt.get("source_sha256") != source
            or not re.fullmatch(r"[a-f0-9]{64}", str(digest or ""))
            or receipt.get("output_sha256") != digest
            or not receipt.get("message_id")
        ):
            raise ValueError("Delivery receipts do not match the current Word/PDF output hashes")
        url = str(receipt.get("web_url") or "")
        parsed = urlparse(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in {"teams.microsoft.com", "teams.cloud.microsoft"}
            or not parsed.path.startswith("/l/message/")
        ):
            raise ValueError("Delivery evidence requires Teams message links")
        links.append(f"[{label}]({url})")
    return "Delivered for owner review: " + " · ".join(links)


class ProgressMessageCoordinator:
    def __init__(self, runtime, *, commands=None, now=None, schedule=None, authorize_session=None):
        self.runtime = runtime
        self.commands = commands or TeamsMessageCommands(runtime)
        self.now = now or (lambda: datetime.now(UTC))
        self.journal = OperationJournal(runtime.config.runtime_dir, "progress")
        self.authorize_session = authorize_session
        self.schedule = schedule or self._schedule

    def _schedule(self, *, event_type, payload, run_at, idempotency_key):
        record, _ = ScheduledInternalEventStore(
            self.runtime.config.runtime_dir / "orchestration.db"
        ).schedule_event(
            event_type=event_type,
            payload=payload,
            run_at=run_at,
            idempotency_key=idempotency_key,
        )
        return record.schedule_id

    def _identity(self, session_id, channel_target, thread_id, generation_id):
        session = self.runtime.policy.require_tool(session_id, ToolName.TEAMS_UPDATE_MESSAGE)
        if self.authorize_session is not None:
            self.authorize_session(session)
        target = self.runtime.policy.require_teams_channel_target(
            session,
            channel_target,
            capability="reply" if thread_id is not None else "write",
        )
        return target, self.journal.key(session_id, target, thread_id, generation_id)

    def sync(
        self,
        session_id,
        channel_target,
        snapshot=None,
        thread_id=None,
        *,
        enabled=False,
        generation_id=None,
        source_version=None,
        action="update",
        delivery=None,
        request_id=None,
        minimum_interval_seconds=30,
        outline_sha256=None,
    ):
        if enabled is not True:
            return {"status": "disabled"}
        if thread_id is not None:
            if not isinstance(thread_id, str):
                raise ValueError("thread_id must be a string")
            thread_id = thread_id.strip() or None
        incoming = ProgressSnapshot.model_validate(snapshot) if snapshot is not None else None
        generation = incoming.generation_id if incoming else generation_id
        if not generation or action not in {"update", "renew", "flush"}:
            raise ValueError("A generation and valid progress action are required")
        target, key = self._identity(session_id, channel_target, thread_id, generation)
        with self.journal.lock(key):
            state = self.journal.read(key)
            if state is None:
                if incoming is None or incoming.phase in TERMINAL:
                    return {"status": "unchanged"}
                state = {
                    "generation_id": generation,
                    "outline_sha256": incoming.outline_sha256,
                    "latest_snapshot": incoming.model_dump(),
                    "latest_delivery": delivery,
                    "message_id": None,
                    "pending": None,
                    "terminal": False,
                }
            current = ProgressSnapshot.model_validate(state["latest_snapshot"])
            if outline_sha256 is not None and outline_sha256 != state["outline_sha256"]:
                return self._result(state, "unchanged")
            if incoming is not None:
                if incoming.outline_sha256 != state["outline_sha256"]:
                    raise ValueError("Progress outline identity changed; start a new generation")
                if state.get("terminal") or incoming.snapshot_sequence < current.snapshot_sequence:
                    return self._result(state, "unchanged")
                if (
                    incoming.snapshot_sequence == current.snapshot_sequence
                    and incoming.visible_hash() != current.visible_hash()
                ):
                    raise ValueError("Conflicting progress snapshots have the same sequence")
                state["latest_snapshot"] = incoming.model_dump()
                state["latest_delivery"] = delivery
            self.journal.write(key, state)
            try:
                # Finish the exact persisted operation before accepting a different one.
                if state.get("pending"):
                    desired_terminal = (
                        ProgressSnapshot.model_validate(state["latest_snapshot"]).phase in TERMINAL
                    )
                    recorded = (
                        self.runtime.teams_messages.get_outbound_edit_state(
                            target, state["message_id"]
                        )
                        if state.get("message_id")
                        else None
                    )
                    if (
                        desired_terminal
                        and recorded is not None
                        and recorded.pending is None
                        and state["pending"].get("artifact_id") is not None
                        and (
                            recorded.image is None
                            or recorded.image.artifact_id != state["pending"]["artifact_id"]
                        )
                    ):
                        # No Graph intent was admitted for this candidate. Cancellation
                        # may discard it and clear using edit authority alone.
                        state["pending"] = None
                        self.journal.write(key, state)
                if state.get("pending"):
                    self._apply(key, state, session_id, target, thread_id, request_id)
                snapshot = ProgressSnapshot.model_validate(state["latest_snapshot"])
                if state.get("terminal"):
                    return self._result(state, "unchanged")
                image = state.get("source_version")
                if action == "renew" and source_version != image:
                    return self._result(state, "unchanged")
                renewal_due = bool(
                    state.get("image_renew_at")
                    and self.now() >= datetime.fromisoformat(state["image_renew_at"])
                )
                same = snapshot.visible_hash() == state.get("visible_hash")
                terminal = snapshot.phase in TERMINAL
                if snapshot.phase == "delivered":
                    final_links = delivery_links(state.get("latest_delivery"))
                else:
                    final_links = ""
                if same and not renewal_due:
                    if state.get("image_renew_at"):
                        self._timer(
                            key,
                            state,
                            session_id,
                            target,
                            thread_id,
                            "renew",
                            datetime.fromisoformat(state["image_renew_at"]),
                        )
                        self.journal.write(key, state)
                    return self._result(state, "unchanged")
                last = (
                    datetime.fromisoformat(state["updated_at"]) if state.get("updated_at") else None
                )
                interval = max(0, min(300, int(minimum_interval_seconds)))
                if (
                    not renewal_due
                    and not terminal
                    and last is not None
                    and snapshot.phase == "writing"
                    and state.get("phase") == snapshot.phase
                    and self.now() < last + timedelta(seconds=interval)
                ):
                    self._timer(
                        key,
                        state,
                        session_id,
                        target,
                        thread_id,
                        "flush",
                        last + timedelta(seconds=interval),
                    )
                    self.journal.write(key, state)
                    return self._result(state, "coalesced")
                text = snapshot.status_text() + ("\n\n" + final_links if final_links else "")
                pending = {
                    "snapshot": snapshot.model_dump(),
                    "text": text,
                    "terminal": terminal,
                    "artifact_id": None,
                }
                if not terminal:
                    self.runtime.policy.require_tool(session_id, ToolName.TEAMS_POST_FILE)
                    self.runtime.policy.require_communication_file_send_enabled(
                        self.runtime.session_repository.get_session(session_id)
                    )
                    content = (
                        base64.b64decode(state["png_base64"])
                        if same and state.get("png_base64")
                        else render_progress_png(snapshot)
                    )
                    if same and sha256(content).hexdigest() != state.get("png_sha256"):
                        raise ValueError("Retained progress PNG checksum changed")
                    self.runtime.policy.require_communication_file_content(
                        self.runtime.session_repository.get_session(session_id),
                        filename="progress.png",
                        mime_type="image/png",
                        byte_count=len(content),
                    )
                    artifact = CommunicationArtifactStore(self.runtime.config.runtime_dir).stage(
                        provider="teams",
                        session_id=session_id,
                        content=content,
                        original_filename="progress.png",
                        mime_type="image/png",
                        metadata={"status_text": text, "purpose": "teams_progress_image"},
                    )
                    pending.update(
                        artifact_id=artifact.artifact_id,
                        png_base64=base64.b64encode(content).decode("ascii"),
                    )
                state["pending"] = pending
                self.journal.write(key, state)
                self._apply(key, state, session_id, target, thread_id, request_id)
                state["error"] = None
                self.journal.write(key, state)
                return self._result(state, "updated")
            except Exception as exc:
                # Notification-only retries never enqueue writers, reviews or exports.
                state["error"] = re.sub(r"https?://\S+", "[redacted]", str(exc))[:500]
                self.journal.write(key, state)
                return {**self._result(state, "notification_failed"), "error": state["error"]}

    def _apply(self, key, state, session_id, target, thread_id, request_id):
        pending = state["pending"]
        snapshot = ProgressSnapshot.model_validate(pending["snapshot"])
        if state.get("message_id") is None:
            result = self.commands.post_file(
                session_id,
                target,
                pending["text"],
                [pending["artifact_id"]],
                thread_id,
                request_id=request_id,
                editable_image_artifact_id=pending["artifact_id"],
                idempotency_key="progress-" + key,
                authorize_session=self.authorize_session,
            )
            if result.get("image_editable") is not True:
                # Remember a successful send even if Graph normalized its slot ambiguously.
                state["message_id"] = result["message_id"]
                state["revision"] = result["message_revision"]
                self.journal.write(key, state)
                raise ValueError(
                    "Progress message posted, but acknowledged image slot is uneditable"
                )
            state["message_id"] = result["message_id"]
        else:
            recorded = self.runtime.teams_messages.get_outbound_edit_state(
                target, state["message_id"]
            )
            receipt = self.runtime.teams_messages.get_outbound_receipt(target, state["message_id"])
            if recorded is not None and recorded.pending is not None:
                try:
                    # Readback of an already-applied intent needs no new file
                    # publication, including after that source's expiry.
                    self.commands.update_message(
                        session_id,
                        target,
                        state["message_id"],
                        pending["text"],
                        thread_id,
                        expected_revision=state["revision"],
                        authorize_session=self.authorize_session,
                    )
                except ValueError:
                    pass  # An unapplied image still needs its original authorized retry.
                recorded = self.runtime.teams_messages.get_outbound_edit_state(
                    target, state["message_id"]
                )
            if (
                receipt is not None
                and receipt.session_id == session_id
                and recorded is not None
                and recorded.pending is None
                and recorded.revision == state["revision"] + 1
                and recorded.text_html == _teams_message_to_html(pending["text"])
                and (recorded.image.artifact_id if recorded.image else None)
                == pending["artifact_id"]
            ):
                if recorded.image is not None:
                    info, _ = validated_png(
                        CommunicationArtifactStore(self.runtime.config.runtime_dir),
                        session_id,
                        recorded.image.artifact_id,
                    )
                    if info.sha256 != recorded.image.sha256:
                        raise ValueError("Verified progress source checksum changed")
                result = outbound_edit_result(receipt, recorded, "unchanged")
            else:
                result = self.commands.update_message(
                    session_id,
                    target,
                    state["message_id"],
                    pending["text"],
                    thread_id,
                    expected_revision=state["revision"],
                    image_artifact_id=pending["artifact_id"],
                    clear_image=pending["terminal"],
                    request_id=request_id,
                    authorize_session=self.authorize_session,
                )
        state.update(
            revision=result["message_revision"],
            visible_hash=snapshot.visible_hash(),
            phase=snapshot.phase,
            terminal=pending["terminal"],
            source_version=pending["artifact_id"],
            image_renew_at=result.get("image_renew_at"),
            image_expires_at=result.get("image_expires_at"),
            updated_at=self.now().isoformat(),
            png_base64=pending.get("png_base64"),
            png_sha256=sha256(base64.b64decode(pending["png_base64"])).hexdigest()
            if pending.get("png_base64")
            else None,
            pending=None,
            error=None,
        )
        self.journal.write(key, state)
        if state["image_renew_at"]:
            self._timer(
                key,
                state,
                session_id,
                target,
                thread_id,
                "renew",
                datetime.fromisoformat(state["image_renew_at"]),
            )
            self.journal.write(key, state)

    def _timer(self, key, state, session_id, target, thread_id, action, run_at):
        version = state.get("source_version")
        schedule_key = f"teams-progress:{key}:{action}:{version}:{run_at.isoformat()}"
        state[action + "_schedule_id"] = self.schedule(
            event_type="internal.teams.progress-image." + action,
            payload={
                "session_id": session_id,
                "channel_target": target,
                "thread_id": thread_id,
                "directory_path": self.runtime.session_repository.get_session(
                    session_id
                ).directory_path
                or "root",
                "generation_id": state["generation_id"],
                "outline_sha256": state["outline_sha256"],
                "source_version": version,
                "action": action,
            },
            run_at=run_at,
            idempotency_key=schedule_key,
        )

    @staticmethod
    def _result(state, status):
        return {
            "status": status,
            "message_id": state.get("message_id"),
            "message_revision": state.get("revision"),
            "generation_id": state["generation_id"],
            "source_version": state.get("source_version"),
            "image_expires_at": state.get("image_expires_at"),
            "image_renew_at": state.get("image_renew_at"),
            "phase": state.get("phase"),
        }

"""Run with PYTHONPATH=src:packages/teams-graph-addon. Uses a temporary stub runtime."""

from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from evander.config import GatewayConfig
from evander.models import SessionManifest, ToolName
from evander.runtime import build_runtime
from evander_teams_graph_addon.progress_images import ProgressSnapshot
from evander_teams_graph_addon.progress_messages import ProgressMessageCoordinator


def run():
    # No credential, connection, production runtime or workflow configuration is used.
    os.environ.setdefault("EVANDER_COMMUNICATION_ARTIFACT_LINK_SECRET", "offline-example-only")
    with TemporaryDirectory(prefix="teams-progress-example-") as directory:
        root = Path(directory)
        runtime = build_runtime(
            GatewayConfig(
                runtime_dir=root,
                audit_log_path=root / "audit.jsonl",
                session_store_path=root / "sessions.json",
                approval_store_path=root / "approvals.json",
                draft_store_path=root / "drafts.json",
                email_store_path=root / "emails.json",
                discord_command_store_path=root / "discord.json",
                teams_backend="stub",
                communication_artifact_public_base_url="https://example.invalid",
            )
        )
        runtime.session_repository.upsert_session(
            SessionManifest(
                session_id="example-session",
                allowed_tools=[ToolName.TEAMS_POST_FILE, ToolName.TEAMS_UPDATE_MESSAGE],
                teams_channel_allowlist=["example-team/example-channel"],
                communication_file_send_enabled=True,
                communication_file_require_approval_for_send=False,
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        progress = ProgressMessageCoordinator(runtime)
        revisions = []
        for sequence, phase, chapters, sections in (
            (1, "writing", 0, 0),
            (2, "writing", 1, 2),
            (3, "reviewing", 3, 5),
            (4, "cancelled", 3, 5),
        ):
            snapshot = ProgressSnapshot(
                generation_id="example-generation",
                outline_sha256="a" * 64,
                snapshot_sequence=sequence,
                document_title="Example handbook",
                phase=phase,
                chapters_total=3,
                chapters_completed=chapters,
                sections_total=5,
                sections_completed=sections,
                status="Synthetic example; no document is being generated.",
            )
            result = progress.sync(
                "example-session",
                "example-team/example-channel",
                snapshot.model_dump(),
                enabled=True,
                minimum_interval_seconds=0,
            )
            if result["status"] == "notification_failed":
                raise RuntimeError(result["error"])
            revisions.append(result["message_revision"])
        records = runtime.teams_messages.read_all()
        assert len(records) == 1
        assert "<img" not in next(iter(records.values())).content
        return {
            "messages_created": len(records),
            "revisions": revisions,
            "terminal_image_cleared": True,
            "backend": "stub",
        }


if __name__ == "__main__":
    print(json.dumps(run()))

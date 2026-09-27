"""Explicit dataset inventory and bounded, source-specific privacy adapters.

An inventory entry is NOT a claim of automated erasure. Unsupported stores stay
visible to the operator and keep readiness/case coverage incomplete.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from evander.privacy.models import (
    DatasetDescriptor,
    PrivacyConflict,
    PrivacyError,
    PrivacyUnsupported,
    RecordReference,
    revision,
)

MAX_SOURCE_BYTES = 32 * 1024 * 1024
MAX_SCAN_RECORDS = 10000


class DatasetAdapter:
    def __init__(self, descriptor: DatasetDescriptor, path: Path | None = None):
        self.descriptor = descriptor
        self.path = path

    def inventory(self, scope: str) -> dict:
        return {
            **self.descriptor.model_dump(mode="json"),
            "status": "supported" if self.descriptor.capabilities.get("retention") else "partial",
            "present": self.path.exists() if self.path is not None and scope == "root" else None,
        }

    def records(self, scope: str) -> list[tuple[RecordReference, dict]]:
        raise PrivacyUnsupported("This dataset requires a reviewed manual locator")

    def locate(self, scope: str, subject_keys: dict[str, str]) -> list[RecordReference]:
        matches = []
        identifiers = dict(subject_keys)
        if "session_id" in identifiers:
            identifiers.setdefault("origin_session_id", identifiers["session_id"])
        if "origin_session_id" in identifiers:
            identifiers.setdefault("session_id", identifiers["origin_session_id"])
        for ref, record in self.records(scope):
            # Exact structured identifiers only. Names/body text are not an identity index.
            if any(str(record.get(key, "")) == value for key, value in identifiers.items()):
                matches.append(ref)
        return matches

    def preview(self, scope: str, *, cutoff: datetime) -> list[RecordReference]:
        if not self.descriptor.capabilities.get("retention"):
            raise PrivacyUnsupported("Scheduled retention is not implemented for this dataset")
        return [
            ref
            for ref, _ in self.records(scope)
            if ref.eligible_at is not None and ref.eligible_at <= cutoff
        ]

    def export_record(self, scope: str, ref: RecordReference) -> dict:
        raise PrivacyUnsupported("Reviewed export is not implemented for this dataset")

    def erase(self, scope: str, ref: RecordReference) -> str:
        raise PrivacyUnsupported("Automated erasure is not implemented for this dataset")

    def check_current(
        self, scope: str, ref: RecordReference
    ) -> tuple[RecordReference, dict] | None:
        for current, value in self.records(scope):
            if current.record_id == ref.record_id:
                if (
                    current.fingerprint != ref.fingerprint
                    or current.directory_path != ref.directory_path
                ):
                    raise PrivacyConflict(
                        "A selected record changed; create and approve a new preview"
                    )
                return current, value
        return None


class JsonLocatorAdapter(DatasetAdapter):
    def __init__(self, descriptor, path, *, group=None, export_fields=(), sessions_path=None):
        super().__init__(descriptor, path)
        self.group = group
        self.export_fields = tuple(export_fields)
        self.sessions_path = sessions_path

    @staticmethod
    def read_json(path: Path) -> dict:
        if not path.exists():
            return {}
        if path.is_symlink() or path.stat().st_size > MAX_SOURCE_BYTES:
            raise PrivacyUnsupported("Dataset requires a bounded manual extraction")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError()
            return data
        except (OSError, ValueError):
            raise PrivacyError(
                "Dataset could not be read; no completeness claim is possible"
            ) from None

    def records(self, scope: str):
        raw = self.read_json(self.path)
        if self.group is not None and raw and self.group not in raw:
            raise PrivacyError("Dataset shape is unrecognized; manual extraction is required")
        values = raw if self.group is None else raw.get(self.group, {})
        if not isinstance(values, dict) or len(values) > MAX_SCAN_RECORDS:
            raise PrivacyUnsupported("Dataset requires a bounded manual extraction")
        sessions = self.read_json(self.sessions_path) if self.sessions_path else {}
        result = []
        for record_id, value in values.items():
            if not isinstance(value, dict):
                raise PrivacyError(
                    "Dataset contains an unrecognized record; manual review is required"
                )
            directory = value.get("directory_path")
            session_id = value.get("session_id") or value.get("origin_session_id")
            if not directory and session_id:
                directory = (sessions.get(session_id) or {}).get("directory_path")
            # Root is the installation-owner view, not an inferred owner for legacy rows.
            if scope != "root" and directory != scope:
                continue
            ref = RecordReference(
                dataset_id=self.descriptor.dataset_id,
                record_id=str(record_id),
                fingerprint=revision(value),
                directory_path=directory,
            )
            result.append((ref, value))
        return result

    def export_record(self, scope, ref):
        if not self.export_fields:
            return super().export_record(scope, ref)
        current = self.check_current(scope, ref)
        if current is None:
            raise PrivacyConflict("A selected record is no longer available; refresh the preview")
        return {key: current[1][key] for key in self.export_fields if key in current[1]}


class CommunicationArtifactAdapter(DatasetAdapter):
    def __init__(self, runtime_dir: Path, sessions_path: Path):
        super().__init__(
            DatasetDescriptor(
                dataset_id="communication_artifacts",
                label="Communication attachment staging",
                storage="communication-artifacts/*.json + *.bin",
                purpose="Temporary local staging of provider attachments",
                retention_source="Stored expires_at; existing default is seven days",
                capabilities={
                    "locate": True,
                    "retention": True,
                    "hold": True,
                    "erase": True,
                    "export": True,
                    "restrict": False,
                },
                related_datasets=["email", "team_state", "workspaces", "providers", "backups"],
                limitation="Cleanup covers staging only, not delivered workspace/provider copies. "
                "Exports contain selected metadata, not attachment bytes.",
            ),
            Path(runtime_dir) / "communication-artifacts",
        )
        self.runtime_dir = Path(runtime_dir)
        self.sessions_path = Path(sessions_path)

    def records(self, scope):
        from evander.communication_artifacts import (
            CommunicationArtifactMetadata,
            validate_artifact_id,
        )

        sessions = JsonLocatorAdapter.read_json(self.sessions_path)
        result = []
        if self.path.is_symlink():
            raise PrivacyUnsupported("Symlinked artifact storage requires manual review")
        for index, path in enumerate(self.path.glob("comm_*.json")):
            if index >= MAX_SCAN_RECORDS:
                raise PrivacyUnsupported("Artifact inventory exceeds the bounded scan size")
            try:
                raw = JsonLocatorAdapter.read_json(path)
                metadata = CommunicationArtifactMetadata.model_validate(raw)
                validate_artifact_id(metadata.artifact_id)
                if metadata.artifact_id != path.stem:
                    raise ValueError()
                directory = (sessions.get(metadata.session_id) or {}).get("directory_path")
                if scope != "root" and directory != scope:
                    continue
                result.append(
                    (
                        RecordReference(
                            dataset_id=self.descriptor.dataset_id,
                            record_id=metadata.artifact_id,
                            fingerprint=revision(raw),
                            directory_path=directory,
                            eligible_at=metadata.expires_at.astimezone(UTC),
                        ),
                        raw,
                    )
                )
            except PrivacyError:
                raise
            except (ValueError, TypeError):
                raise PrivacyError("Artifact metadata requires manual review") from None
        return result

    def export_record(self, scope, ref):
        current = self.check_current(scope, ref)
        if current is None:
            raise PrivacyConflict("A selected artifact changed; refresh the preview")
        fields = (
            "artifact_id",
            "provider",
            "session_id",
            "source_message_id",
            "source_attachment_id",
            "source_channel",
            "source_mailbox",
            "original_filename",
            "mime_type",
            "byte_count",
            "created_at",
            "expires_at",
        )
        return {key: current[1][key] for key in fields if key in current[1]}

    def erase(self, scope, ref):
        from evander.communication_artifacts import CommunicationArtifactStore

        if self.check_current(scope, ref) is None:
            return "already_absent"
        # Caller holds privacy_guard. Preserve metadata on blob deletion failure so
        # retries can still discover the artifact and verify its identity.
        store = CommunicationArtifactStore(self.runtime_dir)
        store._delete_under_privacy_guard(ref.record_id)
        if store.metadata_path(ref.record_id).exists() or store.blob_path(ref.record_id).exists():
            raise PrivacyError("Artifact cleanup could not be verified")
        return "deleted"

    def check_current(self, scope, ref):
        from evander.communication_artifacts import (
            CommunicationArtifactMetadata,
            validate_artifact_id,
        )

        if self.path.is_symlink():
            raise PrivacyConflict(
                "Artifact storage changed to a symlink; manual review is required"
            )
        safe_id = validate_artifact_id(ref.record_id)
        path = self.path / f"{safe_id}.json"
        blob = self.path / f"{safe_id}.bin"
        if not path.exists() and not blob.exists():
            return None
        if not path.exists():
            raise PrivacyConflict(
                "Selected artifact ownership changed or metadata is missing; review is required"
            )
        raw = JsonLocatorAdapter.read_json(path)
        try:
            metadata = CommunicationArtifactMetadata.model_validate(raw)
        except ValueError:
            raise PrivacyError("Artifact metadata requires manual review") from None
        sessions = JsonLocatorAdapter.read_json(self.sessions_path)
        directory = (sessions.get(metadata.session_id) or {}).get("directory_path")
        if (
            metadata.artifact_id != safe_id
            or revision(raw) != ref.fingerprint
            or directory != ref.directory_path
            or (scope != "root" and directory != scope)
        ):
            raise PrivacyConflict("Selected artifact changed or moved scope; review is required")
        return ref, raw


class DatasetRegistry:
    def __init__(self, adapters):
        self.adapters = {adapter.descriptor.dataset_id: adapter for adapter in adapters}
        if len(self.adapters) != len(adapters):
            raise ValueError("Duplicate privacy dataset identifier")

    def get(self, dataset: str) -> DatasetAdapter:
        try:
            return self.adapters[dataset]
        except KeyError:
            raise PrivacyError("Unknown privacy dataset") from None

    def inventory(self, scope):
        return [self.adapters[key].inventory(scope) for key in sorted(self.adapters)]


def default_registry(config) -> DatasetRegistry:
    root = Path(config.runtime_dir)
    adapters = [CommunicationArtifactAdapter(root, config.session_store_path)]
    # Conservative allowlists exclude arbitrary prompt/bridge/credential metadata.
    common_voice = (
        "voice_session_id",
        "origin_session_id",
        "directory_path",
        "channel",
        "status",
        "contact_id",
        "contact_display_name",
        "meeting_id",
        "transcript",
        "summary",
        "decisions",
        "action_items",
        "created_at",
        "ended_at",
    )
    json_sources = [
        (
            "sessions",
            "Session manifests",
            config.session_store_path,
            None,
            ("session_id", "directory_path", "created_at", "expires_at"),
            "Session service",
        ),
        (
            "voice",
            "Voice transcripts and summaries",
            root / "voice-sessions-store.json",
            "sessions",
            common_voice,
            "No periodic retention established",
        ),
        (
            "voice_queries",
            "Voice questions and answers",
            root / "voice-sessions-store.json",
            "queries",
            (
                "query_id",
                "voice_session_id",
                "origin_session_id",
                "directory_path",
                "question",
                "answer",
                "created_at",
                "answered_at",
            ),
            "No periodic retention established",
        ),
        (
            "meetings",
            "Meeting bindings and transcript events",
            config.teams_meeting_store_path,
            "bindings",
            (),
            "No periodic retention established",
        ),
        (
            "didww",
            "DIDWW bridge call state",
            root / "didww-sip-bridge-store.json",
            "calls",
            (),
            "No periodic retention established",
        ),
        (
            "retell",
            "Retell call state",
            root / "retell-phone-calls-store.json",
            "calls",
            (),
            "Existing 90-day conditional compaction",
        ),
        ("approvals", "Tool approvals", config.approval_store_path, None, (), "Approval expiry"),
        ("email", "Email records", config.email_store_path, None, (), "Not established"),
        ("drafts", "Email drafts", config.draft_store_path, None, (), "Not established"),
        (
            "prompts",
            "Agent prompts",
            root / "agent-prompt-store.json",
            "messages",
            (),
            "Terminal records older than 90 days; opportunistic",
        ),
    ]
    for dataset, label, path, group, fields, retention in json_sources:
        adapters.append(
            JsonLocatorAdapter(
                DatasetDescriptor(
                    dataset_id=dataset,
                    label=label,
                    storage=Path(path).name,
                    purpose="Customer-directed agent operations and communications",
                    retention_source=retention,
                    capabilities={
                        "locate": dataset not in {"prompts", "email", "drafts"},
                        "export": bool(fields),
                        "retention": False,
                        "erase": False,
                        "hold": False,
                        "restrict": False,
                    },
                    related_datasets=["workflows", "audit", "workspaces", "providers", "backups"],
                    limitation="Structured locator only; legacy unattributed rows require installation "
                    "owner review. Export, where available, contains allowlisted fields only. "
                    "Mirrors and free-text identity references require manual review.",
                ),
                Path(path),
                group=group,
                export_fields=fields,
                sessions_path=config.session_store_path,
            )
        )
    manual = [
        ("admin_identity", "Admin identities and authentication", "admin-web.db"),
        ("credentials", "Provider and website credentials", "Credential stores and host secrets"),
        ("contacts", "Contacts and directory configuration", "directory-config/"),
        ("audit", "Tool audit and search index", "audit.jsonl and its SQLite/FTS index"),
        (
            "workflows",
            "Workflow full/compact histories and queues",
            "orchestration.db and event stores",
        ),
        ("wake", "Wake requests, dispatches, and messages", "wake-requests-store.db"),
        ("scripts", "Script requests and consultations", "Script request and consultation stores"),
        (
            "external_chat",
            "Teams/Discord inboxes and notification state",
            "Inbox databases and cursors",
        ),
        ("team_state", "Team chat, memory, cases and retained snapshots", "Team add-on state.db"),
        ("team_attachments", "Team attachment bytes", "Team add-on attachment roots"),
        ("indexes", "Semantic memory and code indexes", "Qdrant and code-index/state.db"),
        ("git", "Managed Git repositories and retained refs", "managed-git/"),
        (
            "workspaces",
            "VM/container workspaces and worktrees",
            "Worker sessions, VM disks, worktrees",
        ),
        (
            "artifacts",
            "Published/session artifacts and artifact spaces",
            "Artifact stores and files",
        ),
        ("usage", "Model usage and classification attribution", "model-usage.db"),
        (
            "provider_feedback",
            "PR feedback, issue and external-source copies",
            "Provider feedback stores",
        ),
        ("privacy_evidence", "Privacy cases, jobs, holds and suppression evidence", "privacy.db"),
        ("providers", "External recipient copies", "Provider-controlled storage"),
        ("backups", "Backups, snapshots and external logs", "Deployment-controlled storage"),
    ]
    for dataset, label, storage in manual:
        adapters.append(
            DatasetAdapter(
                DatasetDescriptor(
                    dataset_id=dataset,
                    label=label,
                    storage=storage,
                    purpose="See approved data map and controller instructions",
                    retention_source="Existing owning service or deployment policy; review required",
                    capabilities=dict.fromkeys(
                        ("locate", "export", "retention", "erase", "hold", "restrict"), False
                    ),
                )
            )
        )
    return DatasetRegistry(adapters)

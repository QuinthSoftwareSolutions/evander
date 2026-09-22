from __future__ import annotations

import json
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from evander.admin.services.privacy_service import PrivacyAdminService
from evander.communication_artifacts import CommunicationArtifactStore
from evander.config import GatewayConfig
from evander.privacy.models import PrivacyConflict, PrivacyError, PrivacyUnsupported
from evander.provider_config import ProviderConfigResolver


class Operations:
    def __init__(self, resolver):
        self.resolver = resolver

    def admin_config_mutation_lock(self):
        return nullcontext()

    def read_directory_domain_config(self, scope, domain):
        return SimpleNamespace(
            ok=True,
            data=SimpleNamespace(
                config=self.resolver.config_store.read(scope, domain=domain) or {}
            ),
        )

    def write_directory_domain_config(self, scope, domain, payload):
        self.resolver.config_store.write(scope, domain=domain, payload=payload)
        return SimpleNamespace(ok=True)


@pytest.fixture
def service(tmp_path):
    config = GatewayConfig(runtime_dir=tmp_path)
    resolver = ProviderConfigResolver(config)
    return PrivacyAdminService(Operations(resolver), resolver, config)


def action(service, name, payload, actor="operator-a", scope="root"):
    return service.mutate(
        name, scope, payload, expected_revision=service.snapshot(scope)["revision"], actor=actor
    )["result"]


def artifact(service, *, expired=True):
    store = CommunicationArtifactStore(service.runtime_dir)
    record = store.stage(
        provider="teams",
        session_id="session-one",
        content=b"attachment",
        original_filename="file.txt",
        expires_at=datetime.now(UTC) + timedelta(days=-1 if expired else 1),
    )
    return store, record


def publish(service, *, scheduled=False):
    return action(
        service,
        "configure",
        {
            "config": {
                "retention": {
                    "communication_artifacts": {
                        "approved": True,
                        "approval_reference": "legal/retention/1",
                        "scheduled": scheduled,
                    },
                }
            }
        },
    )


def create_case(service):
    return action(
        service,
        "cases.create",
        {
            "request_type": "access",
            "controller_ref": "customer/instruction/1",
            "instruction_ref": "request/1",
            "subject_keys": {"session_id": "session-one"},
            "deadline_at": (datetime.now(UTC) + timedelta(days=25)).isoformat(),
        },
    )


def test_defaults_are_report_only_no_scheduled_deletion(service):
    store, record = artifact(service)
    snapshot = service.snapshot("root")
    assert snapshot["effective_config"]["mode"] == "report_only"
    assert snapshot["coverage_complete"] is False
    assert snapshot["readiness"]["ready"] is False
    result = service.maintenance()
    assert not result["jobs"]
    assert store.blob_path(record.artifact_id).exists()


def test_preview_requires_policy_and_separate_approval(service):
    store, record = artifact(service)
    preview = action(service, "retention.preview", {"dataset": "communication_artifacts"})
    assert preview["record_count"] == 1
    assert store.blob_path(record.artifact_id).exists()
    with pytest.raises(PrivacyError, match="Publish a reviewed"):
        action(service, "jobs.approve", {"job_id": preview["id"]})
    with pytest.raises(PrivacyConflict):
        action(service, "jobs.run", {"job_id": preview["id"]})
    publish(service)
    with pytest.raises(PrivacyConflict, match="Policy changed"):
        action(service, "jobs.approve", {"job_id": preview["id"]})
    preview = action(service, "retention.preview", {"dataset": "communication_artifacts"})
    action(service, "jobs.approve", {"job_id": preview["id"]})
    result = action(service, "jobs.run", {"job_id": preview["id"]})
    assert result["status"] == "local_complete"
    assert not store.metadata_path(record.artifact_id).exists()
    assert not store.blob_path(record.artifact_id).exists()


def test_hold_blocks_opportunistic_cleanup_and_expired_access(service):
    store, record = artifact(service)
    hold = action(
        service,
        "holds.create",
        {
            "dataset": "communication_artifacts",
            "record_ids": [record.artifact_id],
            "reason": "case preservation",
            "review_at": (datetime.now(UTC) - timedelta(days=1)).isoformat(),
        },
    )
    assert store.cleanup_expired() == 0
    assert store.delete(record.artifact_id) is False
    with pytest.raises(FileNotFoundError, match="Expired"):
        store.get(record.artifact_id)
    assert store.blob_path(record.artifact_id).exists()
    action(service, "holds.release", {"hold_id": hold["id"]})
    assert store.cleanup_expired() == 1


def test_hold_published_after_approval_stops_job(service):
    store, record = artifact(service)
    publish(service)
    job = action(service, "retention.preview", {"dataset": "communication_artifacts"})
    action(service, "jobs.approve", {"job_id": job["id"]})
    hold = action(
        service,
        "holds.create",
        {
            "dataset": "communication_artifacts",
            "record_ids": [record.artifact_id],
            "reason": "controller instruction",
            "review_at": datetime.now(UTC).isoformat(),
        },
    )
    result = action(service, "jobs.run", {"job_id": job["id"]})
    assert result["status"] == "held"
    assert store.blob_path(record.artifact_id).exists()
    action(service, "holds.release", {"hold_id": hold["id"]})
    result = action(service, "jobs.run", {"job_id": job["id"]})
    assert result["status"] == "local_complete"


def test_stale_revision_and_unknown_scope_fail(service):
    old = service.snapshot("root")["revision"]
    create_case(service)
    with pytest.raises(PrivacyConflict):
        service.mutate("cases.create", "root", {}, expected_revision=old, actor="operator")
    with pytest.raises((ValueError, KeyError)):
        service.snapshot("root/does-not-exist")


def test_changed_selection_never_deletes_new_data(service):
    store, record = artifact(service)
    publish(service)
    job = action(service, "retention.preview", {"dataset": "communication_artifacts"})
    action(service, "jobs.approve", {"job_id": job["id"]})
    path = store.metadata_path(record.artifact_id)
    payload = json.loads(path.read_text())
    payload["expires_at"] = (datetime.now(UTC) + timedelta(days=30)).isoformat()
    path.write_text(json.dumps(payload))
    result = action(service, "jobs.run", {"job_id": job["id"]})
    assert result["status"] == "needs_review"
    assert store.blob_path(record.artifact_id).exists()


def test_partial_failure_preserves_metadata_and_can_retry(service, monkeypatch):
    store, record = artifact(service)
    publish(service)
    job = action(service, "retention.preview", {"dataset": "communication_artifacts"})
    action(service, "jobs.approve", {"job_id": job["id"]})
    adapter = service.registry.get("communication_artifacts")
    erase = adapter.erase
    monkeypatch.setattr(adapter, "erase", lambda *_: (_ for _ in ()).throw(OSError("secret error")))
    result = action(service, "jobs.run", {"job_id": job["id"]})
    assert result["status"] == "partial_failure"
    assert "secret error" not in json.dumps(result)
    assert store.metadata_path(record.artifact_id).exists()
    monkeypatch.setattr(adapter, "erase", erase)
    assert action(service, "jobs.run", {"job_id": job["id"]})["status"] == "local_complete"


@pytest.mark.parametrize(
    "name,payload",
    [
        ("retention.preview", {"dataset": "voice"}),
        (
            "holds.create",
            {
                "dataset": "voice",
                "record_ids": ["voice1"],
                "reason": "hold",
                "review_at": "2026-10-01T00:00:00Z",
            },
        ),
        ("configure", {"config": {"mode": "enforce"}}),
    ],
)
def test_unsupported_operations_cannot_claim_coverage(service, name, payload):
    with pytest.raises(PrivacyUnsupported):
        action(service, name, payload)


def test_case_export_has_review_gate_encrypted_storage_and_expiry(service, monkeypatch):
    _, record = artifact(service, expired=False)
    case = create_case(service)
    assert "subject_keys" not in json.dumps(service.snapshot("root")["cases"])
    located = action(service, "cases.locate", {"case_id": case["id"]})
    assert located["coverage_complete"] is False
    assert "backups" in located["manual_datasets"]
    assert {"email", "drafts", "prompts"}.issubset(located["manual_datasets"])
    payload = {
        "case_id": case["id"],
        "operation": "export",
        "selections": {"communication_artifacts": [record.artifact_id]},
    }
    with pytest.raises(PrivacyError, match="verification"):
        action(service, "cases.preview", payload)
    action(
        service,
        "cases.update",
        {"case_id": case["id"], "status": "open", "verification_reference": "verified/account/1"},
    )
    job = action(service, "cases.preview", payload)
    action(service, "jobs.approve", {"job_id": job["id"]})
    result = action(service, "jobs.run", {"job_id": job["id"]})
    assert result["status"] == "awaiting_review"
    encrypted = service.runtime_dir / "privacy-exports" / f"{job['id']}.fernet"
    assert b"file.txt" not in encrypted.read_bytes()
    decoded = json.loads(service.export_bytes("root", job["id"]))
    assert decoded["records"][0]["data"]["original_filename"] == "file.txt"
    assert "metadata" not in decoded["records"][0]["data"]
    with pytest.raises(PrivacyError, match="second reviewer"):
        action(service, "exports.release", {"job_id": job["id"], "review_reference": "review/1"})
    assert (
        action(
            service,
            "exports.release",
            {"job_id": job["id"], "review_reference": "review/1"},
            actor="operator-b",
        )["status"]
        == "released"
    )
    monkeypatch.setattr(
        "evander.admin.services.privacy_service.now_utc",
        lambda: datetime.now(UTC) + timedelta(days=8),
    )
    with pytest.raises(PrivacyError, match="expired"):
        service.export_bytes("root", job["id"])
    service.maintenance()
    assert not encrypted.exists()


def test_followups_require_evidenced_resolution_before_case_closure(service):
    case = create_case(service)
    followup = action(
        service,
        "followups.create",
        {
            "case_id": case["id"],
            "kind": "backup",
            "owner": "infra",
            "reference": "backup-expiry/1",
            "deadline_at": "2026-11-01T00:00:00Z",
        },
    )
    with pytest.raises(PrivacyConflict, match="follow-ups"):
        action(
            service,
            "cases.update",
            {
                "case_id": case["id"],
                "status": "closed_with_exceptions",
                "closure_reference": "legal/1",
            },
        )
    action(
        service,
        "followups.update",
        {
            "followup_id": followup["id"],
            "status": "exception",
            "evidence_reference": "approved-expiry/1",
        },
    )
    assert (
        action(
            service,
            "cases.update",
            {
                "case_id": case["id"],
                "status": "closed_with_exceptions",
                "closure_reference": "legal/1",
            },
        )["status"]
        == "closed_with_exceptions"
    )


def test_scheduled_retention_is_explicit_opt_in(service):
    store, record = artifact(service)
    publish(service, scheduled=True)
    result = service.maintenance(limit=1)
    assert result["examined"] == 1
    assert not store.blob_path(record.artifact_id).exists()


def test_privacy_database_permissions_and_no_source_content(service):
    create_case(service)
    assert service.repository.path.stat().st_mode & 0o777 == 0o600
    assert "attachment" not in service.repository.path.read_bytes().decode(errors="ignore")


def test_incident_requires_notification_decision_reference(service):
    incident = action(
        service,
        "incidents.create",
        {
            "discovered_at": "2026-09-21T12:00:00Z",
            "controller_ref": "customer1",
            "evidence_reference": "incident-restricted/1",
        },
    )
    with pytest.raises(PrivacyError, match="notification"):
        action(service, "incidents.update", {"incident_id": incident["id"], "status": "closed"})
    assert (
        action(
            service,
            "incidents.update",
            {
                "incident_id": incident["id"],
                "status": "closed",
                "notification_reference": "legal-decision/1",
            },
        )["status"]
        == "closed"
    )


def test_empty_schedules_do_not_grow_job_history(service):
    publish(service, scheduled=True)
    for _ in range(4):
        assert service.maintenance(limit=1)["jobs"] == []
    assert service.repository.list("job", "root") == []


def test_metadata_paging_is_bounded_and_does_not_starve_scheduled_cleanup(service):
    publish(service, scheduled=True)
    for _ in range(4):
        job = action(service, "retention.preview", {"dataset": "communication_artifacts"})
        action(service, "jobs.cancel", {"job_id": job["id"]})
    store, record = artifact(service)
    result = service.maintenance(limit=1)
    assert result["scanned_jobs"] <= 1
    assert result["examined"] <= 1
    assert not store.blob_path(record.artifact_id).exists()


def test_cancel_preserves_completed_outcomes_and_stops_pending_work(service):
    store, record = artifact(service)
    publish(service)
    job = action(service, "retention.preview", {"dataset": "communication_artifacts"})
    action(service, "jobs.approve", {"job_id": job["id"]})
    assert action(service, "jobs.cancel", {"job_id": job["id"]})["status"] == "cancelled"
    with pytest.raises(PrivacyConflict):
        action(service, "jobs.run", {"job_id": job["id"]})
    assert store.blob_path(record.artifact_id).exists()


def test_job_detail_audits_actor_without_invalidating_revision(service):
    artifact(service)
    job = action(service, "retention.preview", {"dataset": "communication_artifacts"})
    before = service.snapshot("root")["revision"]
    details = service.job_detail("root", job["id"], actor="reviewer-identity")
    assert details["revision"] == before
    assert "records" in details and "data" not in details["records"][0]
    with service.repository.connect() as db:
        row = db.execute("SELECT actor,operation FROM privacy_access_events").fetchone()
        assert tuple(row) == ("reviewer-identity", "review_job_selection")
    assert service.snapshot("root")["revision"] == before


def _acquire_privacy_lock_in_child(runtime_dir, entered):
    from evander.privacy.repository import privacy_guard

    with privacy_guard(runtime_dir):
        entered.set()


def test_forked_child_does_not_inherit_reentrant_lock_ownership(service):
    import multiprocessing

    from evander.privacy.repository import privacy_guard

    context = multiprocessing.get_context("fork")
    entered = context.Event()
    process = context.Process(
        target=_acquire_privacy_lock_in_child, args=(service.runtime_dir, entered)
    )
    try:
        with privacy_guard(service.runtime_dir):
            process.start()
            assert not entered.wait(0.2)
        assert entered.wait(5)
        process.join(timeout=5)
        assert process.exitcode == 0
    finally:
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)


def test_maintenance_removes_only_known_encrypted_export_temporaries(service):
    job = service._new("job", "root", {"operation": "export", "status": "cancelled"}, "test")
    directory = service.runtime_dir / "privacy-exports"
    directory.mkdir()
    abandoned = directory / f".{job['id']}.interrupted.fernet.tmp"
    abandoned.write_bytes(b"encrypted-abandoned-data")
    unrelated = directory / "operator-notes.txt"
    unrelated.write_bytes(b"preserve")
    result = service.maintenance(limit=1)
    assert result["temporary_exports_removed"] == 1
    assert result["examined"] == 1
    assert not abandoned.exists()
    assert unrelated.read_bytes() == b"preserve"


def test_export_storage_symlink_is_rejected(service, tmp_path):
    external = tmp_path / "operator-owned-archive"
    external.mkdir()
    (service.runtime_dir / "privacy-exports").symlink_to(external, target_is_directory=True)
    with pytest.raises(PrivacyError, match="symlink"):
        service._export_cipher(create=True)
    assert list(external.iterdir()) == []

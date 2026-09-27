"""Independent lifecycle regressions discovered during implementation review."""

from __future__ import annotations

from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from evander.admin.services.privacy_service import PrivacyAdminService
from evander.communication_artifacts import CommunicationArtifactStore
from evander.config import GatewayConfig
from evander.models import SessionManifest
from evander.privacy.models import PrivacyError
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
                config=self.resolver.config_store.read(scope, domain=domain) or {},
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


def action(service, name, payload, *, scope="root", actor="reviewer-a"):
    return service.mutate(
        name,
        scope,
        payload,
        actor=actor,
        expected_revision=service.snapshot(scope)["revision"],
    )["result"]


def export_job(service, *, run=True):
    artifact = CommunicationArtifactStore(service.runtime_dir).stage(
        provider="teams",
        session_id="session-one",
        content=b"fixture attachment",
        original_filename="fixture.txt",
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    case = action(
        service,
        "cases.create",
        {
            "request_type": "access",
            "controller_ref": "controller-1",
            "instruction_ref": "instruction-1",
            "subject_keys": {"session_id": "session-one"},
            "deadline_at": (datetime.now(UTC) + timedelta(days=20)).isoformat(),
        },
    )
    action(
        service,
        "cases.update",
        {
            "case_id": case["id"],
            "status": "open",
            "verification_reference": "verification-1",
        },
    )
    action(service, "cases.locate", {"case_id": case["id"]})
    job = action(
        service,
        "cases.preview",
        {
            "case_id": case["id"],
            "operation": "export",
            "selections": {"communication_artifacts": [artifact.artifact_id]},
        },
    )
    action(service, "jobs.approve", {"job_id": job["id"]})
    if run:
        action(service, "jobs.run", {"job_id": job["id"]})
    return case, job


@pytest.mark.parametrize(
    "changes",
    [
        {"instruction_ref": "withdrawn-instruction"},
        {"verification_reference": "corrected-identity"},
        {"status": "awaiting_instruction"},
    ],
)
@pytest.mark.parametrize("operation", ["download", "release"])
def test_export_cannot_outlive_its_case_authority(service, changes, operation):
    case, job = export_job(service)
    action(service, "cases.update", {"case_id": case["id"], "status": "open", **changes})
    with pytest.raises(PrivacyError):
        if operation == "download":
            service.export_bytes("root", job["id"])
        else:
            action(
                service,
                "exports.release",
                {
                    "job_id": job["id"],
                    "review_reference": "review-1",
                },
                actor="reviewer-b",
            )


@pytest.mark.parametrize("operation", ["download", "release"])
def test_export_requires_review_after_policy_change(service, operation):
    _, job = export_job(service)
    action(service, "configure", {"config": {"export_ttl_days": 8}})
    with pytest.raises(PrivacyError):
        if operation == "download":
            service.export_bytes("root", job["id"])
        else:
            action(
                service,
                "exports.release",
                {
                    "job_id": job["id"],
                    "review_reference": "review-1",
                },
                actor="reviewer-b",
            )


def test_previous_process_export_temp_does_not_permanently_block_retry(service):
    _, job = export_job(service, run=False)
    service._export_cipher(create=True)
    interrupted = service.runtime_dir / "privacy-exports" / f"{job['id']}.tmp"
    interrupted.write_bytes(b"fixture encrypted interrupted write")
    result = action(service, "jobs.run", {"job_id": job["id"]})
    assert result["status"] == "awaiting_review"
    assert b"fixture.txt" in service.export_bytes("root", job["id"])


def test_moved_artifact_owner_is_not_reported_as_successful_erasure(service):
    for name in ("alpha", "beta"):
        service.resolver.directory_service.create(name=name, parent="root")
    session = SessionManifest(
        session_id="moving-session",
        directory_path="root/alpha",
        allowed_tools=[],
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    service.resolver.session_store.upsert_session(session)
    store = CommunicationArtifactStore(service.runtime_dir)
    artifact = store.stage(
        provider="teams",
        session_id=session.session_id,
        content=b"keep-after-scope-move",
        original_filename="fixture.txt",
        expires_at=datetime.now(UTC) - timedelta(days=1),
    )
    action(
        service,
        "configure",
        {
            "config": {
                "retention": {
                    "communication_artifacts": {
                        "approved": True,
                        "approval_reference": "reviewed-period",
                    },
                }
            }
        },
        scope="root/alpha",
    )
    job = action(
        service,
        "retention.preview",
        {
            "dataset": "communication_artifacts",
        },
        scope="root/alpha",
    )
    action(service, "jobs.approve", {"job_id": job["id"]}, scope="root/alpha")
    service.resolver.session_store.upsert_session(
        session.model_copy(update={"directory_path": "root/beta"})
    )
    outcome = action(service, "jobs.run", {"job_id": job["id"]}, scope="root/alpha")
    assert outcome["status"] == "needs_review"
    assert store.blob_path(artifact.artifact_id).read_bytes() == b"keep-after-scope-move"
    assert outcome["outcome_counts"] == {"changed": 1}


def test_orphan_blob_is_not_reported_as_already_erased(service):
    store = CommunicationArtifactStore(service.runtime_dir)
    artifact = store.stage(
        provider="teams",
        session_id="s",
        content=b"remaining orphan bytes",
        original_filename="fixture.txt",
        expires_at=datetime.now(UTC) - timedelta(days=1),
    )
    action(
        service,
        "configure",
        {
            "config": {
                "retention": {
                    "communication_artifacts": {
                        "approved": True,
                        "approval_reference": "reviewed-period",
                    },
                }
            }
        },
    )
    job = action(service, "retention.preview", {"dataset": "communication_artifacts"})
    action(service, "jobs.approve", {"job_id": job["id"]})
    store.metadata_path(artifact.artifact_id).unlink()
    outcome = action(service, "jobs.run", {"job_id": job["id"]})
    assert outcome["status"] == "needs_review"
    assert outcome["outcome_counts"] == {"changed": 1}
    assert store.blob_path(artifact.artifact_id).read_bytes() == b"remaining orphan bytes"


@pytest.mark.parametrize("field", ["instruction_ref", "verification_reference"])
def test_blank_authority_reference_does_not_authorize_export(service, field):
    case, _ = export_job(service, run=False)
    try:
        action(service, "cases.update", {"case_id": case["id"], "status": "open", field: " \t "})
    except PrivacyError:
        # Rejecting the invalid update itself is also a safe interface.
        return
    located = action(service, "cases.locate", {"case_id": case["id"]})
    selections = {}
    for ref in located["matches"]:
        if ref["dataset_id"] == "communication_artifacts":
            selections.setdefault(ref["dataset_id"], []).append(ref["record_id"])
    with pytest.raises(PrivacyError):
        action(
            service,
            "cases.preview",
            {
                "case_id": case["id"],
                "operation": "export",
                "selections": selections,
            },
        )


def test_followup_cannot_reopen_under_a_closed_case(service):
    case = action(
        service,
        "cases.create",
        {
            "request_type": "erasure",
            "controller_ref": "controller-1",
            "subject_keys": {"session_id": "s"},
            "deadline_at": (datetime.now(UTC) + timedelta(days=20)).isoformat(),
        },
    )
    followup = action(
        service,
        "followups.create",
        {
            "case_id": case["id"],
            "kind": "backup",
            "owner": "operator-a",
            "reference": "backup-1",
            "deadline_at": (datetime.now(UTC) + timedelta(days=20)).isoformat(),
        },
    )
    action(
        service,
        "followups.update",
        {
            "followup_id": followup["id"],
            "status": "exception",
            "evidence_reference": "controller-exception-1",
        },
    )
    action(
        service,
        "cases.update",
        {
            "case_id": case["id"],
            "status": "closed_with_exceptions",
            "closure_reference": "controller-closure-1",
        },
    )
    with pytest.raises(PrivacyError):
        action(
            service,
            "followups.update",
            {
                "followup_id": followup["id"],
                "status": "pending",
                "evidence_reference": "needs-more-work",
            },
        )


def test_export_filesystem_failure_is_durable_sanitized_and_retryable(service, monkeypatch):
    import evander.admin.services.privacy_service as module

    _, job = export_job(service, run=False)
    replace = module.os.replace

    def fail_export_replace(source, destination):
        if str(destination).endswith(".fernet"):
            raise OSError("fixture confidential path must not appear in operator error")
        return replace(source, destination)

    monkeypatch.setattr(module.os, "replace", fail_export_replace)
    with pytest.raises(PrivacyError) as failure:
        action(service, "jobs.run", {"job_id": job["id"]})
    assert "confidential" not in str(failure.value)
    stored = service.repository.get("job", job["id"], "root")
    assert stored["status"] == "partial_failure"
    assert stored["attempts"] == 1
    monkeypatch.setattr(module.os, "replace", replace)
    assert action(service, "jobs.run", {"job_id": job["id"]})["status"] == "awaiting_review"


def test_selected_artifact_validation_does_not_rescan_unrelated_records(service, monkeypatch):
    store = CommunicationArtifactStore(service.runtime_dir)
    artifact = store.stage(
        provider="teams",
        session_id="s",
        content=b"fixture",
        original_filename="fixture.txt",
    )
    adapter = service.registry.get("communication_artifacts")
    selected = next(
        ref for ref, _ in adapter.records("root") if ref.record_id == artifact.artifact_id
    )

    def forbidden_scan(_scope):
        pytest.fail("Exact-ID validation must not scan every artifact while holding global locks")

    monkeypatch.setattr(adapter, "records", forbidden_scan)
    assert adapter.check_current("root", selected)[0] == selected


def test_artifact_root_replaced_by_symlink_cannot_redirect_approved_deletion(service):
    store = CommunicationArtifactStore(service.runtime_dir)
    artifact = store.stage(
        provider="teams",
        session_id="s",
        content=b"do not delete through symlink",
        original_filename="fixture.txt",
        expires_at=datetime.now(UTC) - timedelta(days=1),
    )
    action(
        service,
        "configure",
        {
            "config": {
                "retention": {
                    "communication_artifacts": {
                        "approved": True,
                        "approval_reference": "reviewed-period",
                    },
                }
            }
        },
    )
    job = action(service, "retention.preview", {"dataset": "communication_artifacts"})
    action(service, "jobs.approve", {"job_id": job["id"]})
    moved = service.runtime_dir / "relocated-artifact-fixtures"
    store.root.rename(moved)
    store.root.symlink_to(moved, target_is_directory=True)
    result = action(service, "jobs.run", {"job_id": job["id"]})
    assert result["status"] in {"needs_review", "partial_failure"}
    assert (moved / f"{artifact.artifact_id}.bin").read_bytes() == b"do not delete through symlink"

from types import SimpleNamespace
from copy import deepcopy
from datetime import timedelta
import hashlib
import io
import json
import tarfile

import pytest

from evander.execution import ExecutionStatusKind
from evander.published_artifacts import PublishedArtifactStore
from evander.security_scans import SecurityScanHandle
from evander_codex_security_addon.coverage import make_state, progress
from evander_codex_security_addon import coverage as c
from evander_codex_security_addon.coverage_provider import CoverageController

from test_coverage import entry, request, mapping, finish as finish_scope, receipt as c_receipt


class Worker:
    def __init__(self):
        self.active, self.results, self.destroyed = {}, {}, []
        self.fail_destroy = False

    def inspect(self, execution_id):
        if execution_id in self.destroyed:
            return SimpleNamespace(status=ExecutionStatusKind.DESTROYED)
        return SimpleNamespace(
            status=ExecutionStatusKind.STOPPED
            if execution_id in self.results
            else ExecutionStatusKind.RUNNING
        )

    def stop(self, execution_id):
        if self.fail_destroy:
            raise RuntimeError("Worker is unreachable")

    def destroy(self, execution_id):
        if self.fail_destroy:
            raise RuntimeError("Worker is unreachable")
        self.active.pop(execution_id, None)
        self.destroyed.append(execution_id)


def native_archive(
    store,
    scan_id,
    receipt,
    *,
    bad_revision=False,
    native_files=None,
    mutated_files=None,
    missing_files=(),
    extra_records=(),
    native_coverage=None,
    native_paths=None,
):
    prefix = f"security-scans/{scan_id}/reports/review/"
    documents = {
        "findings.json": {
            "findings": [
                {
                    "findingId": "finding-1",
                    "occurrenceId": "occ-1",
                    "title": "Unchecked boundary",
                    "severity": {"level": "high"},
                    "fingerprints": {"primary": "same"},
                }
            ]
        },
        "coverage.json": {"completeness": receipt["coverage"], "deferred": receipt["deferred"]},
    }
    documents["coverage.json"].update(native_coverage or {})
    writeups = [path for path in (native_files or {}) if path.startswith("findings/")]
    if writeups:
        documents["findings.json"]["findings"][0]["writeup"] = {"reportPath": writeups[0]}
    bodies = {name: json.dumps(value).encode() for name, value in documents.items()}
    bodies.update(native_files or {})
    bodies["scan-manifest.json"] = json.dumps(
        {
            "scan": {
                "id": scan_id,
                "status": "completed",
                "sealedAt": "2026-10-20T00:00:00Z",
                "threatModel": receipt.get("architecture", {}).get("threat_model"),
                "target": {
                    "kind": "git_revision",
                    "revision": "e" * 40 if bad_revision else receipt.get("source_projection", {}).get("revision", receipt["revision"]),
                    "targetId": "logical-source",
                },
                "scope": {
                    "includePaths": native_paths if native_paths is not None else (["."] if receipt["kind"] == "architecture" else receipt["paths"])
                },
                "artifacts": [
                    {"path": name, "sha256": hashlib.sha256(body).hexdigest()}
                    for name, body in bodies.items()
                ]
                + list(extra_records),
            }
        }
    ).encode()
    # The pinned SDK renders report.md as an unsealed projection by default.
    bodies.setdefault("report.md", b"Native immutable report\n")
    bodies.update(mutated_files or {})
    for name in missing_files:
        bodies.pop(name)
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, body in bodies.items():
            member = tarfile.TarInfo(prefix + name)
            member.size = len(body)
            archive.addfile(member, io.BytesIO(body))
    key = "security-scan-" + scan_id
    store.publish_bytes(key=key, session_id="scan-session", content=buffer.getvalue())
    return key


@pytest.fixture
def running(tmp_path):
    worker = Worker()
    runtime = SimpleNamespace(
        config_path=lambda _: tmp_path, metadata={"directory_path": "root/project"}
    )
    artifacts = PublishedArtifactStore(tmp_path)
    provider = SimpleNamespace()
    provider._command = lambda runtime, sid, args: worker.results.get(
        "scan-" + args[1], {"status": "running"}
    )

    def preserve(result, handle, runtime):
        key = "security-scan-" + handle.metadata["scan_id"]
        if not artifacts.exists(key):
            # A failed guest still preserves its public status document.
            buffer = io.BytesIO()
            body = json.dumps(worker.results[handle.execution_id]).encode()
            with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
                member = tarfile.TarInfo(
                    "security-scans/" + handle.metadata["scan_id"] + "/result.json"
                )
                member.size = len(body)
                archive.addfile(member, io.BytesIO(body))
            artifacts.publish_bytes(key=key, session_id="scan-session", content=buffer.getvalue())
        artifacts.get(key)
        return result.model_copy(update={"artifacts": {"archive_key": key}})

    provider._preserve_result = preserve
    provider._cancel_raw = lambda **kwargs: worker.stop(kwargs["handle"].execution_id)

    controller = CoverageController(provider, runtime)
    controller.route = lambda _: {"model": "example"}
    controller.backend = lambda _: worker

    def launch(state, item):
        attempt = item["attempts"][-1]
        handle = SecurityScanHandle(
            execution_id="scan-" + attempt["scan_id"],
            metadata={
                "backend": "container_worker_vm",
                "session_id": "scan-session",
                "scan_id": attempt["scan_id"],
            },
        )
        attempt["handle"] = handle.model_dump(mode="json")
        worker.active[handle.execution_id] = (state, item, attempt)
        controller.store.save(state)

    controller.launch = launch
    state = make_state(
        "a" * 32,
        request(),
        "root/project",
        {"revision": "d" * 40, "entries": [entry("a/a.py"), entry("b/b.py"), entry("c/c.py")]},
        {"model": "example"},
    )
    controller.store.create(state)

    def finish_jobs(*, native_files=None, native_coverage=None):
        for execution_id, (state, item, attempt) in list(worker.active.items()):
            receipt = {
                "kind": item["kind"],
                "paths": item["paths"],
                "revision": state["revision"],
                "sdk_version": "0.1.31",
                "coverage": "complete",
                "deferred": [],
                "exclusions": [],
                "needs_follow_up": False,
            }
            if item["kind"] == "architecture":
                receipt["architecture"] = {
                    "threat_model": {"assets": ["source"]},
                    "paths": ["a/a.py", "b/b.py"],
                }
            native_archive(
                artifacts,
                attempt["scan_id"],
                receipt,
                native_files=native_files,
                native_coverage=native_coverage,
            )
            worker.results[execution_id] = {
                "status": "completed",
                "coverage": "complete",
                "coverage_unit": receipt,
            }

    return controller, controller.handle(state), worker, finish_jobs


def test_full_controller_preserves_native_files_and_runs_at_most_two_reviews(running):
    controller, handle, worker, finish = running
    observed = []
    for _ in range(10):
        result = controller.poll(handle)
        observed.append(len(worker.active))
        if result.status != "running":
            break
        finish()
    assert result.success
    assert max(observed) == 2
    assert result.coverage_progress["files_reviewed"] == 3
    assert result.cleanup_confirmed
    assert result.severity_counts["high"] == 1
    artifact = controller.artifacts.get(result.artifacts["archive_key"])
    with tarfile.open(artifact.blob_path) as archive:
        names = archive.getnames()
        native = [x for x in names if "/sources/" in x and x.endswith("/report.md")]
        assert native
        assert all(
            archive.extractfile(name).read() == b"Native immutable report\n" for name in native
        )
        assert not any("diagnostics" in name or name.endswith(".log") for name in names)
        coverage = json.load(archive.extractfile(result.artifacts["coverage.json"]))
        assert coverage["progress"]["files_total"] == 3
        assert all(value["receipt"] for value in coverage["files"].values())


def test_sealed_advisory_questions_allow_completion_and_are_retained_in_reports(running):
    controller, handle, worker, finish = running
    question = {
        "question": "Which external application uses these helpers?",
        "followUpPrompt": "Review its deployment when it becomes available.",
    }
    # Reconstruct the controller while the architecture job is active.
    controller.poll(handle)
    restarted = CoverageController(controller.provider, controller.runtime)
    restarted.route, restarted.backend, restarted.launch = (
        controller.route,
        controller.backend,
        controller.launch,
    )
    for _ in range(10):
        finish(native_coverage={"openQuestions": [question]})
        result = restarted.poll(handle)
        if result.status != "running":
            break
    assert result.success
    assert result.coverage_progress["files_reviewed"] == 3
    assert result.coverage_progress["attempts"] == 7  # Mapping, three files, three boundaries.
    assert not worker.active
    state = restarted.store.get(handle.metadata["scan_id"], "root/project")
    assert not state["deferred"]
    assert len(state["open_questions"]) == 7
    assert all(not t["deferred_keys"] for t in state["tasks"].values())
    artifact = restarted.artifacts.get(result.artifacts["archive_key"])
    with tarfile.open(artifact.blob_path) as archive:
        coverage = json.load(archive.extractfile(result.artifacts["coverage.json"]))
        report = archive.extractfile(result.artifacts["report.md"]).read().decode()
        assert coverage["completeness"] == "complete"
        assert coverage["open_questions"] == state["open_questions"]
        assert question["question"] in report
        assert question["followUpPrompt"] in report
        native = [
            n for n in archive.getnames() if "/sources/" in n and n.endswith("/coverage.json")
        ]
        assert len(native) == 7
        for name in native:
            assert json.load(archive.extractfile(name))["openQuestions"] == [question]


@pytest.mark.parametrize(
    "native_coverage",
    [
        {"completeness": "partial"},
        {"deferred": [{"id": "source", "reason": "A call site remains unreviewed"}]},
        {"explicitExclusions": [{"path": "a/a.py"}]},
        {"surfaces": [{"label": "file:a/a.py", "disposition": "needs_follow_up"}]},
    ],
)
def test_native_unfinished_work_cannot_be_hidden_by_a_complete_wrapper(running, native_coverage):
    controller, handle, _, _ = running
    state = controller.store.get(handle.metadata["scan_id"], "root/project")
    mapping(state)
    item = c.pending(state)[0]
    attempt = c.begin(state, item)
    receipt = {
        **c_receipt(state, item),
        "open_questions": [{"question": "Unsealed wrapper question"}],
    }
    question = {"question": "Which external application uses this?"}
    native_archive(
        controller.artifacts,
        attempt["scan_id"],
        receipt,
        native_coverage={**native_coverage, "openQuestions": [question]},
    )
    child = SecurityScanHandle(
        execution_id="scan-" + attempt["scan_id"], metadata={"scan_id": attempt["scan_id"]}
    )
    if "completeness" in native_coverage or "deferred" in native_coverage:
        with pytest.raises(ValueError, match="coverage receipt"):
            controller.proof({"status": "completed", "coverage": "complete"}, child, receipt)
    else:
        proof = controller.proof({"status": "completed", "coverage": "complete"}, child, receipt)
        assert receipt["open_questions"] == [question]
        c.accept(state, item, attempt["scan_id"], receipt, proof)
    assert progress(state)["files_reviewed"] == 0
    assert item["status"] != "completed"


def legacy_question_journal(controller, handle, *, collision=False):
    """An old journal with a sealed map and question-derived boundary work."""
    state = controller.store.get(handle.metadata["scan_id"], "root/project")
    item = c.pending(state)[0]
    attempt = c.begin(state, item)
    question = {"question": "How is this deployed outside the repository?"}
    question_id = "question:" + c.digest(question)[:24]
    receipt = {
        "kind": item["kind"],
        "paths": item["paths"],
        "revision": state["revision"],
        "sdk_version": c.SDK_VERSION,
        "coverage": "partial",
        "deferred": [
            {
                "id": question_id if collision else "source",
                "reason": "Review the authorization boundary",
            }
        ],
        "architecture": {"threat_model": {"assets": ["source"]}},
    }
    native_archive(
        controller.artifacts,
        attempt["scan_id"],
        receipt,
        native_coverage={"openQuestions": [question]},
    )
    child = SecurityScanHandle(
        execution_id="scan-" + attempt["scan_id"], metadata={"scan_id": attempt["scan_id"]}
    )
    proof = controller.proof({"status": "completed", "coverage": "partial"}, child, receipt)
    c.accept(state, item, attempt["scan_id"], receipt, proof)
    attempt["cleanup_confirmed"] = True
    key = attempt["scan_id"] + ":" + question_id
    state.pop("open_questions")
    state["deferred"][key] = {"reason": question["question"], "resolution": None}
    # A question-shaped key alone is never enough evidence to remove work.
    state["deferred"]["unknown:question:" + c.digest(question)[:24]] = {
        "reason": question["question"],
        "resolution": None,
    }
    for item in state["tasks"].values():
        if item["kind"] == "files":
            item["status"] = "blocked"
    c.plan_boundaries(state)
    state["cleanup_confirmed"] = True
    controller.store.save(state)
    return state, key, question


@pytest.mark.parametrize("collision", [False, True])
def test_resume_reclassifies_only_proven_legacy_questions_and_keeps_source_immutable(
    running, collision
):
    controller, handle, _, _ = running
    source, key, question = legacy_question_journal(controller, handle, collision=collision)
    source.update(finished_at=c.now().isoformat(), phase="finished", stop_reason="Review stopped")
    controller.publish(source)
    controller.store.save(source)
    before = deepcopy(source)
    artifact = controller.artifacts.get(source["archive_key"])
    original_archive = artifact.blob_path.read_bytes()
    original_command = controller.provider._command
    inventory = {
        "coverage_runtime": 1,
        "revision": source["revision"],
        "entries": source["inventory"],
    }
    controller.provider._command = lambda runtime, sid, args: (
        inventory if args[0] == "coverage-inventory" else original_command(runtime, sid, args)
    )
    controller.runtime.admin_service = SimpleNamespace(
        managed_git_admin=SimpleNamespace(
            get_repository=lambda *_: None,
            pin_retained_snapshot=lambda *_, **__: None,
        )
    )
    controller.verify_inventory = lambda _: None
    resumed_handle = controller.start("b" * 32, request(resume_from_scan_id=source["scan_id"]))
    resumed = controller.store.get(resumed_handle.metadata["scan_id"], "root/project")
    assert resumed["open_questions"][key] == {
        **question,
        "receipt": source["proofs"][0]["archive_key"],
    }
    assert (key in resumed["deferred"]) is collision
    assert sum(
        key in item["deferred_keys"]
        for item in resumed["tasks"].values()
        if item["kind"] == "boundary"
    ) == int(collision)
    assert "unknown:question:" + c.digest(question)[:24] in resumed["deferred"]
    if not collision:
        assert source["proofs"][0]["provider_scan_id"] + ":source" in resumed["deferred"]
    assert progress(resumed)["files_reviewed"] == 0
    assert resumed["revision"] == source["revision"]
    assert resumed["proofs"] == source["proofs"]
    assert resumed["lineage"] == [source["scan_id"]]
    assert controller.store.get(source["scan_id"], "root/project") == before
    assert artifact.blob_path.read_bytes() == original_archive


def test_active_legacy_journal_migrates_once_and_preserves_the_deadline(running):
    controller, handle, _, _ = running
    source, key, _ = legacy_question_journal(controller, handle)
    deadline = source["deadline_at"]
    controller.poll(handle)
    migrated = controller.store.get(source["scan_id"], "root/project")
    assert key in migrated["open_questions"]
    assert key not in migrated["deferred"]
    assert migrated["deadline_at"] == deadline
    assert migrated["files"] == source["files"]
    controller.restore_open_questions = lambda _: pytest.fail(
        "Already migrated journal was reprocessed"
    )
    controller.poll(handle)


@pytest.mark.parametrize("damage", ["archive", "revision", "seal"])
def test_legacy_question_migration_requires_authentic_preserved_provenance(running, damage):
    controller, handle, _, _ = running
    state, key, _ = legacy_question_journal(controller, handle)
    before = deepcopy(state)
    if damage == "archive":
        controller.artifacts.get(state["proofs"][0]["archive_key"]).blob_path.write_bytes(
            b"tampered"
        )
    elif damage == "revision":
        state["revision"] = "e" * 40
    else:
        state["proofs"][0]["provider_scan_id"] = "wrong-native-id"
    with pytest.raises(ValueError, match="integrity|provenance"):
        controller.restore_open_questions(state)
    assert key in state["deferred"]
    assert "open_questions" not in state
    assert state["files"] == before["files"]


def damaged_legacy_scan_with_running_child(running, *, missing=False):
    controller, handle, worker, _ = running
    state, _, _ = legacy_question_journal(controller, handle)
    item = next(x for x in state["tasks"].values() if x["kind"] == "files")
    item["status"] = "pending"
    attempt = c.begin(state, item)
    controller.launch(state, item)
    source = controller.artifacts.get(state["proofs"][0]["archive_key"])
    if missing:
        source.blob_path.unlink()
    else:
        source.blob_path.write_bytes(b"corrupt retained report")
    controller.store.save(state)
    return state, attempt["handle"]["execution_id"]


@pytest.mark.parametrize("operation", ["poll", "cancel", "deadline"])
@pytest.mark.parametrize("missing", [False, True])
def test_bad_legacy_evidence_still_stops_workers_and_releases_reservation(
    running, operation, missing
):
    controller, handle, worker, _ = running
    state, child = damaged_legacy_scan_with_running_child(running, missing=missing)
    if operation == "deadline":
        state["deadline_at"] = (c.now() + timedelta(seconds=60)).isoformat()
        controller.store.save(state)
    before = deepcopy(state)
    result = controller.cancel(handle) if operation == "cancel" else controller.poll(handle)
    assert result.status == "failed" and result.coverage == "partial"
    assert result.cleanup_confirmed
    assert "evidence could not be verified" in result.error
    assert "aggregate report unavailable" in result.error
    assert not result.artifacts
    assert worker.active == {}
    assert worker.destroyed == [child]
    saved = controller.store.get(state["scan_id"], "root/project")
    assert saved["finished_at"] and saved["migration_error"]
    assert saved["deadline_at"] == before["deadline_at"]
    assert saved["proofs"] == before["proofs"]
    assert saved["files"] == before["files"]
    assert saved["deferred"] == before["deferred"]
    assert "open_questions" not in saved
    assert progress(saved)["attempts"] == progress(before)["attempts"]
    # A restart can report the failure without retrying migration or launching work.
    restarted = CoverageController(controller.provider, controller.runtime)
    restarted.backend = controller.backend
    restarted.restore_open_questions = lambda _: pytest.fail("Retried failed conversion")
    assert restarted.poll(handle).cleanup_confirmed
    replacement = make_state(
        "b" * 32,
        request(),
        "root/project",
        {"revision": state["revision"], "entries": state["inventory"]},
        state["route"],
    )
    controller.store.create(replacement)


def test_bad_legacy_evidence_retains_reservation_until_uncertain_cleanup_recovers(running):
    controller, handle, worker, _ = running
    state, child = damaged_legacy_scan_with_running_child(running)
    worker.fail_destroy = True
    result = controller.cancel(handle)
    assert result.status == "failed" and not result.cleanup_confirmed
    assert list(worker.active) == [child]
    assert result.coverage_progress["cleanup_condition"]
    replacement = make_state(
        "b" * 32,
        request(),
        "root/project",
        {"revision": state["revision"], "entries": state["inventory"]},
        state["route"],
    )
    with pytest.raises(ValueError, match="active scan"):
        controller.store.create(replacement)
    worker.fail_destroy = False
    result = controller.poll(handle)
    assert result.status == "failed" and result.cleanup_confirmed
    assert worker.active == {}
    assert worker.destroyed == [child]
    controller.store.create(replacement)


@pytest.mark.parametrize(
    "path",
    [
        "report.md",
        "findings/auth_bypass/auth_bypass.md",
        "hardening/boundary.md",
        "exports/results.sarif",
        "checkpoints/review.json",
    ],
)
@pytest.mark.parametrize("change", ["tampered", "missing", "duplicate"])
def test_native_sealed_report_changes_are_rejected_after_publication(running, path, change):
    controller, _, _, _ = running
    receipt = {
        "kind": "files",
        "paths": ["a/a.py"],
        "revision": "d" * 40,
        "sdk_version": "0.1.31",
        "coverage": "complete",
        "deferred": [],
    }
    body = b"original sealed native report"
    scan_id = "f" * 32
    native_archive(
        controller.artifacts,
        scan_id,
        receipt,
        native_files={path: body},
        mutated_files={path: b"changed after sealing"} if change == "tampered" else {},
        missing_files=[path] if change == "missing" else [],
        extra_records=[{"path": path, "sha256": hashlib.sha256(body).hexdigest()}]
        if change == "duplicate"
        else [],
    )
    child = SecurityScanHandle(
        execution_id="scan-" + scan_id, metadata={"scan_id": scan_id, "session_id": "scan-session"}
    )
    with pytest.raises(ValueError, match="[Ss]eal"):
        controller.proof({"status": "completed", "coverage": "complete"}, child, receipt)


@pytest.mark.parametrize(
    "slug", ["auth_bypass", "auth.bypass", "auth-bypass", "diagnostics", "sessions", "log"]
)
def test_native_writeup_links_survive_aggregate_publication(running, slug):
    controller, handle, _, finish = running
    relative = f"findings/{slug}/{slug}.md"
    body = b"Unchanged native finding writeup\n"
    for _ in range(10):
        result = controller.poll(handle)
        if result.status != "running":
            break
        finish(native_files={relative: body})
    assert result.success
    artifact = controller.artifacts.get(result.artifacts["archive_key"])
    with tarfile.open(artifact.blob_path) as archive:
        native_findings = [
            n for n in archive.getnames() if "/sources/" in n and n.endswith("/findings.json")
        ]
        assert native_findings
        for name in native_findings:
            linked = json.load(archive.extractfile(name))["findings"][0]["writeup"]["reportPath"]
            assert linked == relative
            assert archive.extractfile(name.rsplit("/", 1)[0] + "/" + linked).read() == body


def test_boundary_worker_inputs_only_include_their_own_outstanding_work(running, monkeypatch):
    from evander.effects.providers.agent_copy_files import AgentCopyFilesEffectProvider
    from evander_codex_security_addon.coverage import begin, pending, plan_boundaries

    controller, handle, _, _ = running
    state = controller.store.get(handle.metadata["scan_id"], "root/project")
    mapping(state)
    for scope in list(pending(state)):
        finish_scope(state, scope)
    for scope in list(pending(state)):
        finish_scope(state, scope)
    state["deferred"] = {
        "native:a": {"paths": ["a/a.py"], "resolution": None},
        "native:b": {"paths": ["b/b.py"], "resolution": None},
    }
    plan_boundaries(state)
    monkeypatch.setattr(AgentCopyFilesEffectProvider, "execute", lambda *_: None)
    controller.provider._start_raw = lambda **kwargs: SecurityScanHandle(
        execution_id="scan-" + kwargs["scan_id"], metadata={}
    )
    scopes = pending(state)
    assert len(scopes) == 2
    for scope, expected in zip(scopes, ["native:a", "native:b"], strict=True):
        attempt = begin(state, scope)
        CoverageController.launch(controller, state, scope)
        staged = json.loads(
            (controller.store.root / "requests" / (attempt["scan_id"] + ".json")).read_text()
        )
        assert list(staged["coverage_task"]["deferred"]) == [expected]
        assert staged["coverage_task"]["paths"] == scope["paths"]


def test_cancel_keeps_repository_claim_until_cleanup_is_confirmed(running):
    controller, handle, worker, _ = running
    controller.poll(handle)
    worker.fail_destroy = True
    result = controller.cancel(handle)
    assert result.status == "failed"
    assert not result.cleanup_confirmed
    assert result.artifacts["archive_key"]
    alternate = make_state(
        "b" * 32,
        request(),
        "root/project",
        {"revision": "d" * 40, "entries": [entry("a/a.py")]},
        {"model": "example"},
    )
    with pytest.raises(ValueError, match="active scan"):
        controller.store.create(alternate)
    worker.fail_destroy = False
    cleaned = controller.poll(handle)
    assert cleaned.cleanup_confirmed
    assert cleaned.artifacts == result.artifacts
    controller.store.create(alternate)


def test_retained_archive_tampering_is_rejected_before_resume(running):
    controller, handle, _, finish = running
    controller.poll(handle)
    finish()
    controller.poll(handle)
    state = controller.store.get(handle.metadata["scan_id"], "root/project")
    source = controller.artifacts.get(state["proofs"][0]["archive_key"])
    source.blob_path.write_bytes(b"modified archive")
    with pytest.raises(ValueError, match="integrity"):
        controller.verify_proofs(state)


def test_bad_native_revision_cannot_close_scope(running):
    controller, _, worker, _ = running
    receipt = {
        "kind": "files",
        "paths": ["a/a.py"],
        "revision": "d" * 40,
        "sdk_version": "0.1.31",
        "coverage": "complete",
        "deferred": [],
    }
    native_archive(controller.artifacts, "c" * 32, receipt, bad_revision=True)
    handle = SecurityScanHandle(execution_id="scan-" + "c" * 32, metadata={"scan_id": "c" * 32})
    with pytest.raises(ValueError, match="scope or seal"):
        controller.proof({"status": "completed", "coverage": "complete"}, handle, receipt)


def test_uncertain_cleanup_occupies_a_slot_and_survives_controller_restart(running):
    controller, handle, worker, finish = running
    controller.poll(handle)
    finish()
    # Save the architecture result but fail to remove its worker.
    worker.fail_destroy = True
    controller.poll(handle)
    state = controller.store.get(handle.metadata["scan_id"], "root/project")
    assert len(worker.active) == 2  # one unconfirmed old worker plus one new review
    assert progress(state)["active_reviews"] == 2
    assert (
        len(
            [
                t
                for t in state["tasks"].values()
                if t["kind"] == "files" and t["status"] == "running"
            ]
        )
        == 1
    )
    recovered = CoverageController(controller.provider, controller.runtime)
    recovered.route, recovered.backend, recovered.launch = (
        controller.route,
        controller.backend,
        controller.launch,
    )
    for _ in range(3):
        recovered.poll(handle)
        assert len(worker.active) == 2
    worker.fail_destroy = False
    recovered.poll(handle)
    assert len(worker.active) == 2
    assert worker.destroyed


def test_lost_launch_response_rejoins_the_saved_generation(running):
    from evander.security_scans import SecurityScanRetryableError

    controller, handle, worker, finish = running
    launch = controller.launch
    calls = []

    def lose_response(state, item):
        calls.append(item["attempts"][-1]["scan_id"])
        launch(state, item)
        # The worker accepted it, but the host did not receive the handle.
        item["attempts"][-1]["handle"] = None
        item["attempts"][-1]["launch_submitted"] = True
        controller.store.save(state)
        raise SecurityScanRetryableError("Synthetic lost launch receipt")

    controller.launch = lose_response
    controller.poll(handle)
    controller.launch = lambda state, item: (
        calls.append(item["attempts"][-1]["scan_id"]),
        launch(state, item),
    )
    saved = controller.store.get(handle.metadata["scan_id"], "root/project")
    for item in saved["tasks"].values():
        if item["attempts"]:
            item["attempts"][-1]["launch_retry_at"] = None
    controller.store.save(saved)
    controller.poll(handle)
    assert len(worker.active) == 1
    assert len(calls) == 2 and calls[0] == calls[1]
    finish()
    controller.poll(handle)
    state = controller.store.get(handle.metadata["scan_id"], "root/project")
    assert state["architecture"]
    assert len(state["proofs"]) == 1


def test_quota_wait_preserves_deadline_and_stops_after_two_retries(running):
    controller, handle, worker, _ = running
    original = controller.store.get(handle.metadata["scan_id"], "root/project")["deadline_at"]
    for number in range(3):
        controller.poll(handle)
        for execution_id in list(worker.active):
            worker.results[execution_id] = {
                "status": "failed",
                "failure_category": "quota",
                "retry_after_seconds": 30,
            }
        result = controller.poll(handle)
        state = controller.store.get(handle.metadata["scan_id"], "root/project")
        assert state["deadline_at"] == original
        if number < 2:
            assert result.status == "running"
            assert state["phase"] == "waiting_for_quota"
            assert state["not_before"]
            assert not worker.active
            state.pop("not_before")
            for item in state["tasks"].values():
                item["next_at"] = None
            controller.store.save(state)
    assert result.status == "failed"
    assert "quota" in result.error.lower()
    assert result.cleanup_confirmed
    assert result.coverage_progress["attempts"] == 3


@pytest.mark.parametrize("category", ["authentication", "policy"])
def test_access_failure_stops_without_starting_another_review(running, category):
    controller, handle, worker, _ = running
    controller.poll(handle)
    worker.results.update(
        {key: {"status": "failed", "failure_category": category} for key in worker.active}
    )
    result = controller.poll(handle)
    assert result.status == "failed"
    assert result.coverage_progress["attempts"] == 1
    assert not worker.active
    assert result.cleanup_confirmed


def test_uncertain_launch_without_worker_record_keeps_cleanup_and_reservation(running):
    from evander.security_scans import SecurityScanRetryableError

    controller, handle, worker, _ = running

    def lost(state, item):
        item["attempts"][-1]["launch_submitted"] = True
        controller.store.save(state)
        raise SecurityScanRetryableError("Synthetic lost response")

    def unknown(_):
        raise ValueError("No execution mapping")

    controller.launch = lost
    worker.inspect = unknown
    controller.provider._cancel_raw = lambda **_: False
    for _ in range(3):
        result = controller.poll(handle)
        state = controller.store.get(handle.metadata["scan_id"], "root/project")
        for item in state["tasks"].values():
            if item["attempts"]:
                item["attempts"][-1]["launch_retry_at"] = None
        controller.store.save(state)
    assert result.status == "failed"
    assert not result.cleanup_confirmed
    assert result.artifacts["archive_key"]
    assert result.coverage_progress["active_reviews"] == 1
    assert result.coverage_progress["cleanup_condition"]
    controller.provider._cancel_raw = lambda **_: True
    assert controller.poll(handle).cleanup_confirmed


def test_lost_delete_response_is_reconciled_without_restarting_the_review(running):
    controller, handle, worker, finish = running
    controller.poll(handle)
    finish()
    destroy = worker.destroy

    def lose(execution_id):
        destroy(execution_id)
        raise RuntimeError("Synthetic lost removal response")

    worker.destroy = lose
    result = controller.poll(handle)
    state = controller.store.get(handle.metadata["scan_id"], "root/project")
    architecture = next(x for x in state["tasks"].values() if x["kind"] == "architecture")
    assert architecture["attempts"][0]["cleanup_confirmed"]
    assert len(architecture["attempts"]) == 1
    assert result.coverage_progress["active_reviews"] == 2


def test_managed_snapshot_inventory_and_resume_survive_force_push(tmp_path, monkeypatch):
    import subprocess
    from evander.admin.services.managed_git_service import ManagedGitAdminService
    from evander.security_scans import SecurityScanRequest
    from evander_codex_security_addon import coverage_runner, runner

    monkeypatch.setattr(coverage_runner, "runner", runner)
    monkeypatch.setattr(runner, "HOME", tmp_path)
    source = tmp_path / "source"
    source.mkdir()

    def git(*args, repo=source):
        return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()

    git("init", "-q", "-b", "main")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.invalid")
    (source / "README.md").write_text("first snapshot\n")
    git("add", ".")
    git("commit", "-qm", "snapshot")
    old = git("rev-parse", "HEAD")
    managed = ManagedGitAdminService(
        SimpleNamespace(runtime_dir=tmp_path / "runtime"), SimpleNamespace(), tmp_path
    )
    repository = managed.register_repository("root/project", str(source))
    inventory = coverage_runner.inventory({"repo_path": str(source)})
    provider = SimpleNamespace(_command=lambda *_: inventory)
    runtime = SimpleNamespace(
        config_path=lambda _: tmp_path / "runtime",
        metadata={"directory_path": "root/project"},
        admin_service=SimpleNamespace(managed_git_admin=managed),
    )
    controller = CoverageController(provider, runtime)
    controller.route = lambda _: {"model": "synthetic"}
    request = SecurityScanRequest(
        session_id="scan-session",
        repo_path="/home/agent/repo",
        managed_repo_id=repository["managed_repo_id"],
        coverage_policy="all_tracked_files",
    )
    first = controller.start("a" * 32, request)
    state = controller.store.get("a" * 32, "root/project")
    from evander_codex_security_addon.coverage import now

    state.update(finished_at=now().isoformat(), phase="finished", cleanup_confirmed=True)
    controller.store.save(state)
    git("checkout", "--orphan", "replacement")
    git("rm", "-rf", ".")
    (source / "new.py").write_text("new unrelated history\n")
    git("add", ".")
    git("commit", "-qm", "rewritten history")
    git("branch", "-f", "main", "HEAD")
    managed.refresh_repository(repository["managed_repo_id"])
    bare, _ = managed.resolve_internal_repository(repository["managed_repo_id"], "root/project")
    git("gc", "--prune=now", repo=bare)
    resumed = controller.start(
        "b" * 32, request.model_copy(update={"resume_from_scan_id": "a" * 32})
    )
    assert resumed.execution_id != first.execution_id
    assert controller.store.get("b" * 32, "root/project")["revision"] == old
    assert git("rev-parse", "refs/evander/security-scans/" + "b" * 32, repo=bare) == old
    malformed = {**state, "inventory": []}
    with pytest.raises(ValueError, match="does not match"):
        controller.verify_inventory(malformed)
    runtime.metadata["directory_path"] = "root/other"
    with pytest.raises(LookupError):
        controller.start("c" * 32, request)


def test_report_retention_failure_keeps_job_but_does_not_block_other_cleanup(running):
    controller, handle, worker, finish = running
    controller.poll(handle)
    finish()
    controller.poll(handle)
    assert len(worker.active) == 2
    finish()
    failed_id = sorted(worker.active)[0]
    preserve = controller.provider._preserve_result

    def unavailable(result, child, runtime):
        if child.execution_id == failed_id:
            raise RuntimeError("Synthetic report transport failure")
        return preserve(result, child, runtime)

    controller.provider._preserve_result = unavailable
    cancelled = controller.cancel(handle)
    assert cancelled.status == "failed"
    assert not cancelled.cleanup_confirmed
    assert list(worker.active) == [failed_id]
    state = controller.store.get(handle.metadata["scan_id"], "root/project")
    assert len(state["proofs"]) == 2  # Architecture plus the independently retained child.
    original_archive = cancelled.artifacts["archive_key"]
    original_hash = controller.artifacts.get(original_archive).metadata.sha256
    controller.provider._preserve_result = preserve
    recovered = controller.poll(handle)
    assert recovered.cleanup_confirmed
    assert worker.active == {}
    assert recovered.artifacts["archive_key"] == original_archive
    assert controller.artifacts.get(original_archive).metadata.sha256 == original_hash


def test_invalid_access_configuration_still_stops_owned_children(running):
    controller, handle, worker, finish = running
    controller.poll(handle)
    finish()
    controller.poll(handle)
    assert len(worker.active) == 2

    def unavailable(_):
        raise ValueError("Subscription configuration is no longer available")

    controller.route = unavailable
    result = controller.poll(handle)
    assert result.status == "failed"
    assert result.cleanup_confirmed
    assert worker.active == {}
    assert result.coverage_progress["attempts"] == 3

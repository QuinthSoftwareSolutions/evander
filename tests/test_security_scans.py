from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from evander.addons import AddonRegistration, LoadedAddonCatalog
from evander.admin.services.security_scan_service import SecurityScanService
from evander.effects.providers.schemas import SecurityScanEffectDefinition
from evander.effects.providers.security_scan import SecurityScanEffectProvider
from evander.effects.providers.base import EffectRuntimeContext
from evander.security_scans import (
    SecurityScanRequest,
    SecurityScanHandle,
    SecurityScanResult,
    security_scan_providers,
)


class Scanner:
    def __init__(self):
        self.starts = []
        self.cancels = []
        self.result = SecurityScanResult(status="running")

    def start(self, *, scan_id, request, runtime):
        self.starts.append(scan_id)
        return SecurityScanHandle(execution_id=scan_id)

    def poll(self, **kwargs):
        return self.result

    def cancel(self, *, scan_id, request, handle, runtime):
        self.cancels.append(scan_id)


@pytest.fixture
def scanning(tmp_path):
    session = SimpleNamespace(
        directory_path="root/project", expires_at=datetime.now(UTC) + timedelta(hours=2)
    )
    scanner = Scanner()
    sessions = SimpleNamespace(get_session=lambda sid: session if sid == "session-1" else None)
    service = SecurityScanService(
        runtime_dir=tmp_path, providers={"codex-security": scanner}, sessions=sessions
    )
    return service, scanner, session


def request(**values):
    return SecurityScanRequest(session_id="session-1", repo_path="/home/agent/repo", **values)


def start(service, **kwargs):
    return service.start(
        request(**kwargs),
        directory_path="root/project",
        execution_id="execution-1",
        idempotency_key="event-1:scan-1",
        runtime=None,
    )


@pytest.mark.parametrize(
    "values",
    [
        {"mode": "deep", "from_commit": "a" * 40},
        {"to_commit": "HEAD"},
        {"from_commit": "abc"},
        {"timeout_seconds": float("inf")},
        {"timeout_seconds": 0},
        {"mode": "unknown"},
    ],
)
def test_invalid_requests_are_rejected(values):
    with pytest.raises(ValidationError):
        request(**values)


@pytest.mark.parametrize("paths", [["../escape"], ["/absolute"], ["folder/"], ["folder//file"], ["*.bin"], [1], "file.py"])
def test_tail_exclusions_require_exact_relative_file_paths(paths):
    with pytest.raises(ValidationError):
        request(coverage_policy="all_tracked_files", managed_repo_id="example", resume_from_scan_id="a" * 32, exclude_paths=paths)


def test_tail_controls_are_exposed_in_workflow_schema_and_reject_unsupported_providers(scanning):
    definition = SecurityScanEffectDefinition(
        id="scan", display_name="Scan", type="effect:security:scan-repo",
        session_id="session-1", repo_path="/home/agent/repo", coverage_policy="all_tracked_files",
        managed_repo_id="example", resume_from_scan_id="a" * 32,
        exclude_paths=["vendor/opaque.bin"], rebuild_pending_batches=True,
        incomplete_review_policy="retry_scope",
    )
    assert definition.exclude_paths == ["vendor/opaque.bin"]
    service, scanner, _ = scanning
    scanner.coverage_policies = {"all_tracked_files"}
    with pytest.raises(ValueError, match="tail controls"):
        service.start(SecurityScanRequest.model_validate({k: getattr(definition,k) for k in SecurityScanRequest.model_fields}), directory_path="root/project", execution_id="e", idempotency_key="tail", runtime=None)
    assert scanner.starts == []


@pytest.mark.parametrize("patterns", [
    "*.pfx", [""], ["/bin/*"], ["../*.zip"], ["src/../*.pfx"],
    ["bin//*.dll"], ["!important.pfx"], ["bin\\*"], ["a\x00b"],
    [123], ["a" * 513], [f"p{i}" for i in range(101)],
])
def test_exclusion_patterns_reject_unsafe_or_unbounded_selectors(patterns):
    with pytest.raises(ValidationError):
        request(coverage_policy="all_tracked_files", managed_repo_id="example", exclude_patterns=patterns)


@pytest.mark.parametrize("inputs", [
    {"exclude_patterns": ["*.pfx", "bin/*"]},
    {"retry_blocked_paths": ["locked.pfx"]},
    {"incomplete_review_policy": "isolate_blockers"},
])
def test_file_controls_require_tracked_coverage(inputs):
    with pytest.raises(ValidationError, match="all_tracked_files"):
        request(**inputs)


def test_explicit_blocker_retry_requires_a_continuation():
    with pytest.raises(ValidationError, match="retained source"):
        request(coverage_policy="all_tracked_files", managed_repo_id="example", retry_blocked_paths=["locked.pfx"])


@pytest.mark.parametrize(("inputs", "capability", "message"), [
    ({"exclude_patterns": ["*.pfx"]}, "supports_pattern_exclusions", "pattern or initial exclusions"),
    ({"exclude_paths": ["opaque.bin"]}, "supports_pattern_exclusions", "pattern or initial exclusions"),
    ({"incomplete_review_policy": "isolate_blockers"}, "supports_file_blocker_isolation", "file blocker isolation"),
    ({"retry_blocked_paths": ["opaque.bin"], "resume_from_scan_id": "a" * 32}, "supports_file_blocker_isolation", "file blocker isolation"),
])
def test_file_controls_fail_closed_on_older_providers(scanning, inputs, capability, message):
    service, scanner, _ = scanning
    scanner.coverage_policies = {"all_tracked_files"}
    scanner.supports_scan_tail_controls = True
    with pytest.raises(ValueError, match=message):
        start(service, coverage_policy="all_tracked_files", managed_repo_id="example", **inputs)
    assert scanner.starts == []
    setattr(scanner, capability, True)
    record = start(service, coverage_policy="all_tracked_files", managed_repo_id="example", **inputs)
    assert scanner.starts == [record.scan_id]


@pytest.mark.parametrize("path", ["repo", "/etc", "/home/agent/../other", "/home/other/repo"])
def test_paths_cannot_escape_the_session(path):
    with pytest.raises(ValidationError):
        SecurityScanRequest(session_id="s", repo_path=path)


def test_concurrent_retries_and_restart_reuse_one_scan(scanning, tmp_path):
    service, scanner, _ = scanning
    with ThreadPoolExecutor(max_workers=4) as executor:
        records = list(executor.map(lambda _: start(service), range(4)))
    assert len({record.scan_id for record in records}) == 1
    restarted = SecurityScanService(
        runtime_dir=tmp_path, providers=service.providers, sessions=service.sessions
    )
    start(restarted)
    assert scanner.starts == [records[0].scan_id]
    with pytest.raises(ValueError, match="different inputs"):
        start(restarted, to_commit="a" * 40)


def test_findings_are_successful_and_survive_restart(scanning, tmp_path):
    service, scanner, _ = scanning
    record = start(service)
    scanner.result = SecurityScanResult(
        status="completed",
        coverage="complete",
        findings=[{"id": "finding-1", "severity": "critical", "title": "Unsafe query"}],
        severity_counts={"critical": 1},
        artifacts={"report": "scan/report.md"},
    )
    final = service.poll(record.scan_id, directory_path="root/project", runtime=None)
    assert final.outputs()["success"] is True
    assert final.outputs()["severity_counts"] == {"critical": 1}
    restarted = SecurityScanService(runtime_dir=tmp_path, providers={}, sessions=service.sessions)
    assert (
        restarted.poll(record.scan_id, directory_path="root/project", runtime=None).outputs()
        == final.outputs()
    )


def test_finished_agreed_scope_with_exclusions_succeeds_and_survives_restart(scanning, tmp_path):
    service, scanner, _ = scanning
    scanner.coverage_policies = {"all_tracked_files"}
    scanner.supports_scan_tail_controls = scanner.supports_pattern_exclusions = True
    scanner.result = SecurityScanResult(
        status="completed", coverage="partial", scope_complete=True,
        excluded_paths=["opaque.bin"], artifacts={"report": "scan/report.md"},
    )
    definition = SecurityScanEffectDefinition(
        id="scan", display_name="Scan", type="effect:security:scan-repo",
        **request(coverage_policy="all_tracked_files", managed_repo_id="example", exclude_paths=["opaque.bin"]).model_dump(),
    )
    provider = SecurityScanEffectProvider()
    provider.initialize(definition, EffectRuntimeContext(
        security_scan_service=service, metadata={"directory_path": "root/project"},
    ))
    output = provider.execute(execution_context={"idempotency_key": "scope", "execution_id": "e"})
    assert output["success"] and output["status"] == "completed"
    assert output["scope_complete"] and output["coverage"] == "partial"
    assert output["excluded_paths"] == ["opaque.bin"] and output["error"] is None
    restarted = SecurityScanService(runtime_dir=tmp_path, providers={}, sessions=service.sessions)
    assert restarted.poll(output["scan_id"], directory_path="root/project", runtime=None).outputs() == output
    metadata = restarted.read_metadata(output["scan_id"], directory_path="root/project")
    assert metadata["scope_complete"] and metadata["excluded_paths"] == ["opaque.bin"]


@pytest.mark.parametrize("changes", [
    {"scope_complete": False}, {"excluded_paths": []}, {"coverage": "unknown"},
    {"status": "failed"}, {"status": "timed_out"}, {"error": "Unresolved checkpoint"},
])
def test_partial_or_stopped_review_cannot_become_success_by_claiming_exclusions(changes):
    result = SecurityScanResult(**{
        "status": "completed", "coverage": "partial", "scope_complete": True,
        "excluded_paths": ["opaque.bin"], **changes,
    })
    assert not result.success


@pytest.mark.parametrize("coverage", ["partial", "unknown"])
def test_incomplete_scan_is_failed_without_discarding_report(scanning, coverage):
    service, scanner, _ = scanning
    record = start(service)
    scanner.result = SecurityScanResult(
        status="completed", coverage=coverage, artifacts={"report": "partial/report.md"}
    )
    final = service.poll(record.scan_id, directory_path="root/project", runtime=None)
    assert final.outputs()["status"] == "failed"
    assert final.outputs()["success"] is False
    assert final.outputs()["artifacts"]["report"] == "partial/report.md"


def test_expiration_cancels_underlying_work(scanning):
    service, scanner, session = scanning
    record = start(service)
    session.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    final = service.poll(record.scan_id, directory_path="root/project", runtime=None)
    assert final.result.status == "cancelled"
    assert scanner.cancels == [record.scan_id]


def test_time_limit_cancels_underlying_work(scanning):
    service, scanner, _ = scanning
    record = start(service)
    record.created_at = datetime.now(UTC) - timedelta(hours=2)
    service.store.save(record)
    assert (
        service.poll(record.scan_id, directory_path="root/project", runtime=None).result.status
        == "timed_out"
    )
    assert scanner.cancels == [record.scan_id]


def test_scope_and_scan_identity_are_enforced(scanning):
    service, scanner, _ = scanning
    with pytest.raises(ValueError, match="outside workflow"):
        service.start(
            request(),
            directory_path="root/other",
            execution_id="e",
            idempotency_key="k",
            runtime=None,
        )
    record = start(service)
    with pytest.raises(ValueError, match="Unknown security scan"):
        service.poll(record.scan_id, directory_path="root/other", runtime=None)
    with pytest.raises(ValueError, match="Invalid security scan"):
        service.poll("../../x", directory_path="root/project", runtime=None)


def test_registry_is_optional_and_rejects_duplicates():
    empty = LoadedAddonCatalog(registrations=(), loaded_addons=())
    assert security_scan_providers(empty) == {}
    registration = AddonRegistration(
        addon_id="synthetic-addon",
        display_name="Synthetic",
        security_scan_providers={"synthetic": Scanner},
    )
    catalog = LoadedAddonCatalog(registrations=(registration,), loaded_addons=("synthetic-addon",))
    assert isinstance(security_scan_providers(catalog)["synthetic"], Scanner)
    with pytest.raises(ValueError, match="Duplicate"):
        security_scan_providers(
            LoadedAddonCatalog(registrations=(registration, registration), loaded_addons=())
        )


def test_effect_requires_durable_execution_context_and_enters_waiting(scanning):
    service, scanner, _ = scanning
    provider = SecurityScanEffectProvider()
    provider.initialize(
        SecurityScanEffectDefinition(
            id="scan",
            display_name="Scan",
            type="effect:security:scan-repo",
            **request().model_dump(),
        ),
        EffectRuntimeContext(
            security_scan_service=service, metadata={"directory_path": "root/project"}
        ),
    )
    with pytest.raises(ValueError, match="durable"):
        provider.execute()
    output = provider.execute(execution_context={"idempotency_key": "k", "execution_id": "e"})
    assert provider.on_execution_started(outputs=output) is True
    assert output["scan_id"] == scanner.starts[0]


def test_lost_launch_response_retries_only_the_same_scan(scanning):
    from evander.security_scans import SecurityScanRetryableError

    service, scanner, _ = scanning
    attempts = []

    def launch(*, scan_id, **kwargs):
        attempts.append(scan_id)
        if len(attempts) == 1:
            raise SecurityScanRetryableError("lost response")
        return SecurityScanHandle(execution_id=scan_id)

    scanner.start = launch
    record = start(service)
    assert record.handle is None
    recovered = service.poll(record.scan_id, directory_path="root/project", runtime=None)
    assert recovered.handle.execution_id == record.scan_id
    assert attempts == [record.scan_id, record.scan_id]


def test_malformed_provider_result_is_terminal_failure(scanning):
    service, scanner, _ = scanning
    record = start(service)

    def invalid(**kwargs):
        raise ValueError("invalid sealed artifact")

    scanner.poll = invalid
    result = service.poll(record.scan_id, directory_path="root/project", runtime=None)
    assert result.result.status == "failed"
    assert result.result.error == "invalid sealed artifact"


def test_cancellation_recovers_identity_when_launch_acknowledgment_was_lost(scanning):
    from evander.security_scans import SecurityScanRetryableError

    service, scanner, _ = scanning

    def uncertain(**kwargs):
        raise SecurityScanRetryableError("lost acknowledgment")

    scanner.start = uncertain
    record = start(service)
    assert record.handle is None
    final = service.cancel(record.scan_id, directory_path="root/project", runtime=None)
    assert final.result.status == "cancelled"
    assert scanner.cancels == [record.scan_id]


def test_builtin_schema_and_catalog_expose_scan_fields():
    from evander.admin.workflow_catalog import _WORKFLOW_EDITOR_NODE_CATALOG
    from evander.workflow.contracts import effective_node_output_schema

    schema = effective_node_output_schema({"type": "effect:security:scan-repo"})
    assert {"findings", "coverage", "artifacts", "severity_counts", "scope_complete", "excluded_paths"} <= schema.keys()
    assert schema["scope_complete"] == {"type": "boolean"}
    assert schema["excluded_paths"] == {"type": "array", "items": {"type": "string"}}
    node = next(
        node for node in _WORKFLOW_EDITOR_NODE_CATALOG if node.type == "effect:security:scan-repo"
    )
    assert {field.key for field in node.fields if field.required} == {"session_id", "repo_path"}
    assert {port.key for port in node.output_ports} == {"completed", "error"}
    field = next(field for field in node.fields if field.key == "resume_subscription_profile_id")
    assert field.bindable and not field.required
    schema = SecurityScanEffectDefinition.model_json_schema()["properties"]
    assert schema["resume_subscription_profile_id"]["default"] is None
    field = next(field for field in node.fields if field.key == "max_batch_text_bytes")
    assert field.kind == "number" and field.bindable and not field.required
    assert field.default_value is None  # Omitted resumes inherit their saved limit.
    assert schema["max_batch_text_bytes"]["default"] is None
    budget = SecurityScanRequest.model_json_schema()["properties"]["max_batch_text_bytes"]
    assert budget["anyOf"][0] == {"type": "integer", "minimum": 1048576, "maximum": 2097152}
    field = next(field for field in node.fields if field.key == "max_parallel_reviews")
    assert field.kind == "number" and field.bindable and not field.required
    assert field.default_value is None  # New scans use two; omitted resumes inherit.
    assert schema["max_parallel_reviews"]["default"] is None
    parallelism = SecurityScanRequest.model_json_schema()["properties"]["max_parallel_reviews"]
    assert parallelism["anyOf"][0] == {"type": "integer", "minimum": 1, "maximum": 4}
    for key in ("exclude_patterns", "retry_blocked_paths"):
        field = next(field for field in node.fields if field.key == key)
        assert field.kind == "json" and field.json_value_type == "array" and field.bindable
        assert field.default_value is None
        assert schema[key]["default"] is None
    field = next(field for field in node.fields if field.key == "incomplete_review_policy")
    assert {option.value for option in field.options} == {"split", "retry_scope", "isolate_blockers"}
    definition = SecurityScanEffectDefinition(
        id="scan", display_name="Scan", type="effect:security:scan-repo",
        session_id="session-1", repo_path="/home/agent/repo", coverage_policy="all_tracked_files",
        managed_repo_id="example", exclude_patterns=["*.pfx", "bin/*"], incomplete_review_policy="isolate_blockers",
    )
    assert definition.exclude_patterns == ["*.pfx", "bin/*"]
    progress = effective_node_output_schema({"type": "effect:security:scan-repo"})["coverage_progress"]["properties"]
    assert progress["files_isolated"] == {"type": "integer"}
    assert progress["validation_questions_pending"] == {"type": "integer"}
    assert progress["isolated_files"]["items"]["properties"]["path"] == {"type": "string"}


def test_unconfirmed_handleless_cancel_stays_unclean_until_positive_acknowledgement(scanning):
    from evander.security_scans import SecurityScanRetryableError

    service, scanner, _ = scanning

    def uncertain(**kwargs):
        raise SecurityScanRetryableError("lost acknowledgement")

    scanner.start = uncertain
    record = start(service)
    scanner.cancel = lambda **_: False
    cancelled = service.cancel(record.scan_id, directory_path="root/project", runtime=None)
    assert cancelled.result.status == "cancelled" and not cancelled.result.cleanup_confirmed
    scanner.cancel = lambda **_: True
    reconciled = service.poll(record.scan_id, directory_path="root/project", runtime=None)
    assert reconciled.result.status == "cancelled" and reconciled.result.cleanup_confirmed
    assert scanner.starts == []


def test_tracked_coverage_defaults_and_unsupported_modes():
    tracked = request(coverage_policy="all_tracked_files", managed_repo_id="managed-example")
    assert tracked.timeout_seconds == 21600
    for invalid in (
        {"managed_repo_id": None},
        {"from_commit": "a" * 40},
        {"to_commit": "a" * 40},
        {"mode": "deep"},
    ):
        values = {
            "coverage_policy": "all_tracked_files",
            "managed_repo_id": "managed-example",
            **invalid,
        }
        with pytest.raises(ValidationError):
            request(**values)
    with pytest.raises(ValidationError, match="resume requires"):
        request(resume_from_scan_id="a" * 32)


@pytest.mark.parametrize("profile", ["", "  ", "UPPERCASE", "../profile", 123, True])
def test_subscription_handoff_requires_a_valid_explicit_profile(profile):
    with pytest.raises(ValidationError):
        request(
            coverage_policy="all_tracked_files", managed_repo_id="managed-example",
            resume_from_scan_id="a" * 32, resume_subscription_profile_id=profile,
        )


def test_subscription_handoff_requires_a_linked_tracked_resume():
    with pytest.raises(ValidationError, match="requires resume_from_scan_id"):
        request(
            coverage_policy="all_tracked_files", managed_repo_id="managed-example",
            resume_subscription_profile_id="subscription-b",
        )
    with pytest.raises(ValidationError, match="resume requires"):
        request(resume_from_scan_id="a" * 32, resume_subscription_profile_id="subscription-b")


def test_subscription_handoff_is_forwarded_only_to_capable_providers_and_is_idempotent(scanning):
    service, scanner, _ = scanning
    scanner.coverage_policies = {"provider", "all_tracked_files"}
    inputs = dict(
        coverage_policy="all_tracked_files", managed_repo_id="managed-example",
        resume_from_scan_id="a" * 32, resume_subscription_profile_id="subscription-b",
    )
    with pytest.raises(ValueError, match="does not support subscription handoff"):
        start(service, **inputs)
    assert scanner.starts == [] and service.store.active() == []
    scanner.supports_subscription_handoff = True
    provider = SecurityScanEffectProvider()
    provider.initialize(
        SecurityScanEffectDefinition(
            id="scan", display_name="Scan", type="effect:security:scan-repo",
            **request(**{**inputs, "resume_subscription_profile_id": None}).model_dump(),
        ),
        EffectRuntimeContext(
            security_scan_service=service, metadata={"directory_path": "root/project"}
        ),
    )
    execution = {"idempotency_key": "event-1:scan-1", "execution_id": "execution-1"}
    result = provider.execute(
        resolved_config={"resume_subscription_profile_id": " subscription-b "},
        execution_context=execution,
    )
    record = service.store.get(result["scan_id"], "root/project")
    assert record.request.resume_subscription_profile_id == "subscription-b"
    assert start(service, **inputs).scan_id == result["scan_id"]
    assert scanner.starts == [result["scan_id"]]
    for different in (None, "subscription-c"):
        with pytest.raises(ValueError, match="reused with different inputs"):
            start(service, **{**inputs, "resume_subscription_profile_id": different})
    assert scanner.starts == [result["scan_id"]]


@pytest.mark.parametrize("limit", [1048576, 1572864, 2097152])
def test_batch_text_limit_is_a_bounded_tracked_workflow_input(limit):
    inputs = dict(
        coverage_policy="all_tracked_files", managed_repo_id="managed-example",
        max_batch_text_bytes=limit,
    )
    assert request(**inputs).max_batch_text_bytes == limit
    definition = SecurityScanEffectDefinition(
        id="scan", display_name="Scan", type="effect:security:scan-repo",
        **request(**inputs).model_dump(),
    )
    assert definition.max_batch_text_bytes == limit
    with pytest.raises(ValidationError, match="requires all_tracked_files"):
        request(max_batch_text_bytes=limit)


@pytest.mark.parametrize("limit", [0, 1048575, 2097153, True, 2097152.0, "2097152"])
def test_batch_text_limit_rejects_out_of_range_or_noninteger_values(limit):
    with pytest.raises(ValidationError):
        request(
            coverage_policy="all_tracked_files", managed_repo_id="managed-example",
            max_batch_text_bytes=limit,
        )


def test_bound_batch_text_limit_requires_capability_and_preserves_execution_identity(scanning):
    service, scanner, _ = scanning
    scanner.coverage_policies = {"provider", "all_tracked_files"}
    inputs = dict(
        coverage_policy="all_tracked_files", managed_repo_id="managed-example",
        max_batch_text_bytes=2097152,
    )
    with pytest.raises(ValueError, match="does not support a batch text limit"):
        start(service, **inputs)
    assert scanner.starts == [] and service.store.active() == []
    scanner.supports_batch_text_limit = True
    provider = SecurityScanEffectProvider()
    provider.initialize(
        SecurityScanEffectDefinition(
            id="scan", display_name="Scan", type="effect:security:scan-repo",
            **request(**inputs).model_dump(exclude={"max_batch_text_bytes"}),
            max_batch_text_bytes="${{event.payload.batch_bytes}}",
        ),
        EffectRuntimeContext(
            security_scan_service=service, metadata={"directory_path": "root/project"}
        ),
    )
    execution = {"idempotency_key": "event-1:scan-1", "execution_id": "execution-1"}
    with pytest.raises(ValidationError):
        provider.execute(resolved_config={"max_batch_text_bytes": 2097153}, execution_context=execution)
    assert scanner.starts == []
    result = provider.execute(
        resolved_config={"max_batch_text_bytes": 2097152}, execution_context=execution,
    )
    record = service.store.get(result["scan_id"], "root/project")
    assert record.request.max_batch_text_bytes == 2097152
    assert start(service, **inputs).scan_id == result["scan_id"]
    for different in (None, 1048576):
        with pytest.raises(ValueError, match="reused with different inputs"):
            start(service, **{**inputs, "max_batch_text_bytes": different})
    assert scanner.starts == [result["scan_id"]]


@pytest.mark.parametrize("limit", [1, 2, 3, 4])
def test_review_parallelism_is_a_bounded_tracked_workflow_input(limit):
    inputs = dict(
        coverage_policy="all_tracked_files", managed_repo_id="managed-example",
        max_parallel_reviews=limit,
    )
    value = request(**inputs)
    definition = SecurityScanEffectDefinition(
        id="scan", display_name="Scan", type="effect:security:scan-repo", **value.model_dump()
    )
    assert value.max_parallel_reviews == definition.max_parallel_reviews == limit
    with pytest.raises(ValidationError, match="requires all_tracked_files"):
        request(max_parallel_reviews=limit)


@pytest.mark.parametrize("limit", [-1, 0, 5, True, False, 4.0, "4"])
def test_review_parallelism_rejects_invalid_static_values(limit):
    with pytest.raises(ValidationError):
        SecurityScanEffectDefinition(
            id="scan", display_name="Scan", type="effect:security:scan-repo",
            session_id="session-1", repo_path="/home/agent/repo",
            coverage_policy="all_tracked_files", managed_repo_id="managed-example",
            max_parallel_reviews=limit,
        )


def test_bound_parallelism_requires_capability_and_preserves_execution_identity(scanning):
    service, scanner, _ = scanning
    scanner.coverage_policies = {"provider", "all_tracked_files"}
    inputs = dict(
        coverage_policy="all_tracked_files", managed_repo_id="managed-example",
        max_parallel_reviews=4,
    )
    with pytest.raises(ValueError, match="does not support review parallelism"):
        start(service, **inputs)
    assert scanner.starts == [] and service.store.active() == []
    scanner.supports_review_parallelism = True
    provider = SecurityScanEffectProvider()
    provider.initialize(
        SecurityScanEffectDefinition(
            id="scan", display_name="Scan", type="effect:security:scan-repo",
            **request(**inputs).model_dump(exclude={"max_parallel_reviews"}),
            max_parallel_reviews="${{event.payload.parallel_reviews}}",
        ),
        EffectRuntimeContext(
            security_scan_service=service, metadata={"directory_path": "root/project"}
        ),
    )
    execution = {"idempotency_key": "event-1:scan-1", "execution_id": "execution-1"}
    for invalid in (5, True, "4"):
        with pytest.raises(ValidationError):
            provider.execute(
                resolved_config={"max_parallel_reviews": invalid}, execution_context=execution
            )
    assert scanner.starts == []
    result = provider.execute(
        resolved_config={"max_parallel_reviews": 4}, execution_context=execution
    )
    assert service.store.get(result["scan_id"], "root/project").request.max_parallel_reviews == 4
    assert start(service, **inputs).scan_id == result["scan_id"]
    for changed in (None, 2):
        with pytest.raises(ValueError, match="reused with different inputs"):
            start(service, **{**inputs, "max_parallel_reviews": changed})
    assert scanner.starts == [result["scan_id"]]


def test_scan_metadata_query_cannot_poll_start_or_leak_diagnostics(scanning):
    from evander.queries.providers.base import QueryRuntimeContext
    from evander.queries.providers.security_scan import SecurityScanQueryProvider
    from evander.queries.providers.schemas import SecurityScanQueryDefinition

    service, scanner, _ = scanning
    record = start(service)
    scanner.result = SecurityScanResult(
        status="running",
        error="private diagnostic data",
        artifacts={"private": "hidden"},
        findings=[{"raw": "hidden"}],
        coverage_progress={"files_total": 3},
    )
    service.poll(record.scan_id, directory_path="root/project", runtime=None)
    provider = SecurityScanQueryProvider()
    provider.initialize(
        SecurityScanQueryDefinition(
            id="lookup", display_name="Lookup", type="query:security:scan", scan_id=record.scan_id
        ),
        QueryRuntimeContext(
            provider_config={},
            metadata={"directory_path": "root/project"},
            admin_service=SimpleNamespace(security_scan_service=service),
        ),
    )
    before = list(scanner.starts)
    result = provider.execute()
    assert result["coverage_progress"] == {"files_total": 3}
    assert result["resumable"] is False
    assert not ({"error", "artifacts", "findings", "repo_path"} & result.keys())
    assert scanner.starts == before
    scanner.describe = lambda **_: {
        "coverage_progress": {
            "files_total": 4, "max_batch_text_bytes": 2097152, "max_parallel_reviews": 4,
            "files_isolated": 1, "files_excluded": 1, "exclude_patterns": ["*.pfx"], "validation_questions_pending": 2,
            "incomplete_review_policy": "isolate_blockers",
            "isolated_files": [{"path": "vendor/locked.bin", "reason": "Decoder required", "receipt": "native-archive", "provider_scan_id": "native"}],
            "raw_diagnostics": "private",
        },
        "execution_configuration": {"model": "synthetic", "api_key": "private"},
        "raw_diagnostics": "private",
    }
    result = provider.execute()
    assert result["coverage_progress"] == {
        "files_total": 4, "max_batch_text_bytes": 2097152, "max_parallel_reviews": 4,
        "files_isolated": 1, "files_excluded": 1, "exclude_patterns": ["*.pfx"], "validation_questions_pending": 2,
        "incomplete_review_policy": "isolate_blockers",
        "isolated_files": [{"path": "vendor/locked.bin", "reason": "Decoder required", "receipt": "native-archive", "provider_scan_id": "native"}],
    }
    assert result["execution_configuration"] == {"model": "synthetic"}
    assert "raw_diagnostics" not in result
    scanner.describe = lambda **_: {"execution_configuration": None}
    assert provider.execute()["execution_configuration"] == {}
    provider.runtime_context = QueryRuntimeContext(
        provider_config={},
        metadata={"directory_path": "root/other"},
        admin_service=SimpleNamespace(security_scan_service=service),
    )
    assert provider.execute() == {"found": False, "scan_id": record.scan_id, "resumable": False}


def test_provider_must_explicitly_support_tracked_policy(scanning):
    service, scanner, _ = scanning
    with pytest.raises(ValueError, match="does not support"):
        start(service, coverage_policy="all_tracked_files", managed_repo_id="managed-example")
    assert scanner.starts == []


def test_uncertain_cancellation_retains_active_record_until_cleanup(scanning):
    service, scanner, _ = scanning
    record = start(service)
    scanner.cancel = lambda **_: (_ for _ in ()).throw(RuntimeError("Worker unreachable"))
    cancelled = service.cancel(record.scan_id, directory_path="root/project", runtime=None)
    assert cancelled.result.status == "cancelled"
    assert not cancelled.result.cleanup_confirmed
    assert [x.scan_id for x in service.store.active()] == [record.scan_id]
    scanner.result = SecurityScanResult(
        status="failed",
        coverage="partial",
        cleanup_confirmed=True,
        findings=[{"late": "do not replace the published result"}],
    )
    cleaned = service.poll(record.scan_id, directory_path="root/project", runtime=None)
    assert cleaned.result.status == "cancelled"
    assert cleaned.result.cleanup_confirmed
    assert cleaned.result.findings == cancelled.result.findings
    assert list(service.store.active()) == []


def test_tracked_start_failure_preserves_scan_identity_and_safe_status(scanning):
    service, scanner, _ = scanning
    scanner.coverage_policies = {"provider", "all_tracked_files"}
    scanner.start = lambda **_: (_ for _ in ()).throw(
        ValueError("Private runtime configuration detail")
    )
    record = start(service, coverage_policy="all_tracked_files", managed_repo_id="managed-example")
    assert record.scan_id
    assert record.result.status == "failed"
    assert record.result.cleanup_confirmed
    assert "Private runtime" not in record.result.error
    assert list(service.store.active()) == []

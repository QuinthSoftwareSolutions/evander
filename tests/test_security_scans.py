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
    assert {"findings", "coverage", "artifacts", "severity_counts"} <= schema.keys()
    node = next(
        node for node in _WORKFLOW_EDITOR_NODE_CATALOG if node.type == "effect:security:scan-repo"
    )
    assert {field.key for field in node.fields if field.required} == {"session_id", "repo_path"}
    assert {port.key for port in node.output_ports} == {"completed", "error"}

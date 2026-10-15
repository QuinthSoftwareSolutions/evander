"""Behavior at the boundaries shared by the control-plane refactors."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from evander import cli_workers
from evander.admin.services.workflow_run_detail import WorkflowRunDetailBuilder
from evander.admin.workflow_report_helpers import (
    _collect_workflow_artifact_ids,
    _collect_workflow_keyed_values,
)
from evander.cli import app
from evander.effects.providers import agent_enqueue_prompt, agent_prompt
from evander.effects.providers.base import EffectRuntimeContext
from evander.events.provider_models import (
    IssueGetQueryDefinition,
    RemoteIssuesQueryDefinition,
    SessionContextQueryDefinition,
)
from evander.local_inference_capacity import LocalInferenceCapacityStore
from evander.models import SessionManifest
from evander.queries.providers import remote_issues
from evander.queries.providers.base import QueryRuntimeContext
from evander.queries.providers.issue_get import IssueGetQueryProvider
from evander.queries.providers.session_context import SessionContextQueryProvider
from evander.repositories.session_manifests import (
    JsonSessionManifestRepository,
    SessionManifestStoreAdapter,
)
from evander.storage import JsonStore


@pytest.mark.parametrize("legacy", [False, True], ids=["repository", "legacy-adapter"])
def test_session_target_selection_preserves_direct_precedence_and_order(tmp_path, legacy):
    path = tmp_path / "sessions.json"
    repository = (
        SessionManifestStoreAdapter(JsonStore(path, SessionManifest))
        if legacy
        else JsonSessionManifestRepository(path)
    )
    created = datetime(2026, 1, 1, tzinfo=UTC)
    for session_id, vm_id, age in (
        ("issue-17-new", "vm-17", 2),
        ("17", "vm-other", 0),
        ("issue-170-unrelated", "vm-170", 5),
        ("issue-17-old", "vm-17", 1),
    ):
        repository.upsert_session(
            SessionManifest(
                session_id=session_id,
                directory_path="root",
                allowed_tools=[],
                vm_id=vm_id,
                created_at=created + timedelta(hours=age),
                expires_at=created + timedelta(days=1),
            )
        )

    assert [s.session_id for s in repository.list_sessions_for_issue(" 17 ")] == [
        "17",
        "issue-17-old",
        "issue-17-new",
    ]
    assert [s.session_id for s in repository.list_sessions_for_vm(" vm-17 ")] == [
        "issue-17-old",
        "issue-17-new",
    ]
    assert repository.resolve_session_target(" 17 ").session_id == "17"
    assert repository.resolve_session_target("vm-17").session_id == "issue-17-new"
    assert repository.resolve_vm_name_from_target("17") == "vm-other"
    assert repository.resolve_vm_name_from_target(" vm-17 ") == "vm-17"
    assert repository.resolve_session_target("missing") is None

    # Blank queries must not touch storage, which may be unavailable.
    repository.get_session = lambda *_: pytest.fail("unexpected session lookup")
    repository.list_sessions = lambda: pytest.fail("unexpected storage read")
    assert repository.list_sessions_for_vm(" ") == []
    assert repository.list_sessions_for_issue("") == []
    assert repository.resolve_session_target(" ") is None
    assert repository.resolve_vm_name_from_target("") == ""


class _DumpedResult:
    def model_dump(self):
        return {"items": [17]}


class _NonMappingDump:
    def __init__(self):
        self.items = [18]

    def model_dump(self):
        return [17]


class _MappingResult(dict):
    def model_dump(self):
        pytest.fail("mapping results must take precedence over model_dump")


@pytest.mark.parametrize("kind", ["issue", "session", "remote"])
@pytest.mark.parametrize(
    "result, expected",
    [
        (None, {}),
        (_MappingResult(items=[16]), {"items": [16]}),
        (_DumpedResult(), {"items": [17]}),
        (_NonMappingDump(), {"items": [18]}),
        (SimpleNamespace(items=[19]), {"items": [19]}),
        (42, {"result": 42}),
    ],
)
def test_query_execution_preserves_result_shape(kind, result, expected, monkeypatch):
    reader = SimpleNamespace(
        get_issue=lambda **_: result,
        session_context_query=lambda **_: result,
        query_remote_issues=lambda **_: result,
    )
    monkeypatch.setattr(remote_issues, "IssueService", lambda _: reader)
    provider, definition = {
        "issue": (
            IssueGetQueryProvider(),
            IssueGetQueryDefinition(
                id="query",
                display_name="Issue",
                type="query:issue:get",
                issue_id="17",
            ),
        ),
        "session": (
            SessionContextQueryProvider(),
            SessionContextQueryDefinition(
                id="query",
                display_name="Session",
                type="query:session-context",
                target="session-17",
            ),
        ),
        "remote": (
            remote_issues.RemoteIssuesQueryProvider(),
            RemoteIssuesQueryDefinition(
                id="query",
                display_name="Remote",
                type="query:remote-issues",
                provider="github",
                repo="example/repository",
            ),
        ),
    }[kind]
    provider.initialize(
        definition,
        QueryRuntimeContext(
            provider_config_resolver=object(),
            issue_reader=reader,
            session_context_reader=reader,
        ),
    )
    actual = provider.execute()
    assert actual == expected
    if isinstance(result, dict):
        assert actual is not result


def test_report_collectors_preserve_nested_key_context_and_first_seen_order():
    payload = {
        "session_id": [" s2 ", "s1", "s2", {"other": "ignored"}],
        "nested": {"SESSION_ID": "s3", "other": "s4"},
        "empty_session_id": " ",
    }
    builder = WorkflowRunDetailBuilder(SimpleNamespace())
    assert _collect_workflow_keyed_values(payload, key_suffix="session_id") == ["s2", "s1", "s3"]
    assert builder.collect_keyed_values(payload, key_suffix="session_id") == ["s2", "s1", "s3"]
    assert builder.collect_keyed_values(payload) == ["s2", "s1", "ignored", "s3", "s4"]


def test_report_artifact_collectors_skip_invalid_handles_and_deduplicate_in_order():
    first = "comm_" + "1" * 32
    second = "comm_" + "2" * 32
    payload = {"nested": [f" {second} ", "invalid", {"id": first}, second, None]}
    builder = WorkflowRunDetailBuilder(SimpleNamespace())
    assert _collect_workflow_artifact_ids(payload) == [second, first]
    assert builder.collect_artifact_ids(payload) == [second, first]


@pytest.mark.parametrize(
    "command, arguments",
    [
        ("force-complete-await-agent", ["dispatch-17"]),
        ("teams-ingest", []),
        ("teams-meeting-ingest", []),
        ("discord-wake", []),
        ("scheduled-wake", []),
        ("trigger-orchestrator", []),
        ("proxy-issue-sync", []),
    ],
)
def test_worker_cli_rejects_bad_logging_before_loading_configuration(
    command, arguments, monkeypatch
):
    monkeypatch.setattr(
        cli_workers.cli_runtime,
        "_load_runtime_config_from_option",
        lambda *_: pytest.fail("configuration loaded before log validation"),
    )
    monkeypatch.setattr(
        cli_workers.cli_runtime,
        "_runtime_dir_for_config_option",
        lambda *_: pytest.fail("runtime resolved before log validation"),
    )
    result = CliRunner().invoke(app, [command, *arguments, "--log-level", "invalid"])
    assert result.exit_code == 2
    assert "Invalid --log-level value: invalid" in result.output


@pytest.mark.parametrize(
    "builder, module_name, label",
    [
        (cli_workers._build_teams_ingest_worker, "evander.messaging.teams_ingest", "Teams ingest"),
        (
            cli_workers._build_teams_meeting_ingest_worker,
            "evander.messaging.teams_meeting_ingest",
            "Teams meeting ingest",
        ),
    ],
)
@pytest.mark.parametrize(
    "failure", ["missing-module", "missing-dependency", "missing-builder", "success"]
)
def test_optional_worker_loading_keeps_dependency_errors_distinct(
    builder,
    module_name,
    label,
    failure,
    monkeypatch,
    tmp_path,
):
    cfg = object()
    missing = ModuleNotFoundError(name=module_name if failure == "missing-module" else "dependency")
    builder_name = "build_" + module_name.rsplit(".", 1)[1] + "_worker"

    def import_module(name):
        assert name == module_name
        if failure in {"missing-module", "missing-dependency"}:
            raise missing
        return SimpleNamespace(
            **(
                {builder_name: lambda config, **kwargs: (config, kwargs)}
                if failure == "success"
                else {}
            )
        )

    monkeypatch.setattr(cli_workers.importlib, "import_module", import_module)
    if failure == "success":
        assert builder(cfg, project_dir=tmp_path) == (cfg, {"project_dir": tmp_path})
    elif failure == "missing-dependency":
        with pytest.raises(ModuleNotFoundError) as raised:
            builder(cfg, project_dir=tmp_path)
        assert raised.value is missing
    else:
        with pytest.raises(RuntimeError) as raised:
            builder(cfg, project_dir=tmp_path)
        expected = (
            f"{label} worker module {module_name} is not available."
            if failure == "missing-module"
            else f"{label} worker module does not export {builder_name}(config, *, project_dir=...)."
        )
        assert str(raised.value) == expected
        if failure == "missing-module":
            assert raised.value.__cause__ is missing


@pytest.mark.parametrize("module", [agent_prompt, agent_enqueue_prompt])
@pytest.mark.parametrize("probe", ["stopped", "running", "failure"])
def test_prompt_reconciliation_preserves_capacity_when_backend_is_active_or_unavailable(
    module,
    probe,
    monkeypatch,
    tmp_path,
):
    store = LocalInferenceCapacityStore(tmp_path)
    old = datetime.now(UTC) - timedelta(hours=1)
    slots = {}
    for target, backend in [("vm-17", "libvirt_vm"), ("container-17", "container_worker_vm")]:
        admission = store.claim(
            max_concurrency=4,
            owner_id=target,
            model="local-model",
            target_name=target,
            execution_backend=backend,
            now=old,
        )
        slots[target] = admission.slot_id
        store.mark_running(admission.slot_id, execution_id=target, now=old)

    def list_vms(**kwargs):
        assert kwargs == {"include_inactive": True, "vm_user": "agent"}
        if probe == "failure":
            raise OSError("inventory unavailable")
        return [SimpleNamespace(vm_name="vm-17", agent_state=probe)]

    def inspect(execution_id):
        assert execution_id == "container-17"
        if probe == "failure":
            raise OSError("worker unavailable")
        return SimpleNamespace(status=probe)

    monkeypatch.setattr(module, "container_backend", lambda *_: SimpleNamespace(inspect=inspect))
    provider_class = (
        module.AgentPromptEffectProvider
        if module is agent_prompt
        else module.AgentEnqueuePromptEffectProvider
    )
    provider = provider_class()
    provider.runtime_context = EffectRuntimeContext(vm_inventory=SimpleNamespace(list_vms=list_vms))
    provider._reconcile_local_inference_capacity(capacity_store=store, config=object())
    records = store.read_all()
    if probe == "stopped":
        assert records == {}
    else:
        assert set(records) == set(slots.values())
        assert {record.status for record in records.values()} == {"running"}

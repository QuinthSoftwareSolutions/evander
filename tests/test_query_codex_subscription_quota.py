from __future__ import annotations

from datetime import UTC, datetime
import json
from types import SimpleNamespace

import httpx
import jsonschema
import pytest
from pydantic import ValidationError

from evander.admin.services.codex_auth_admin_service import (
    CodexAuthAdminDependencies,
    CodexAuthAdminService,
)
from evander.config import GatewayConfig
from evander.events.models import materialize_node_definition
from evander.execution.container_worker_client import ContainerWorkerClient
from evander.orchestration.adapters.provider_factories import build_query_provider_factory
from evander.queries.providers.schemas import CodexSubscriptionQuotaQueryDefinition
from evander.queries.registry import DEFAULT_QUERY_PROVIDER_REGISTRY
from evander.workflow.contracts import WorkflowNodeEnvelope, effective_node_output_schema
from evander.workflow.binding import resolve_workflow_bindings
from evander.workflow.validation import (
    derive_workflow_output_schemas,
    validate_workflow_binding_diagnostics,
)


NODE_TYPE = "query:codex:subscription-quota"
RESET = "2040-02-10T18:00:00+02:00"


def _window(*, used=37.5, duration=10080, reset=RESET):
    return {"used_percent": used, "window_duration_minutes": duration, "resets_at": reset}


def _profile():
    return {
        "profile_id": "team-a",
        "available": True,
        "signed_in": True,
        "enabled": True,
        "account_id": "private-account-id",
        "email": "private@example.test",
        "access_token": "private-account-token",
        "usage": {"private": "unrelated telemetry"},
        "rate_limits": {
            "default": {"limit_id": "base_model_inference", "secondary": _window(used=99)},
            "by_limit_id": {
                "codex": {
                    "primary": _window(used=12, duration=300),
                    "secondary": _window(),
                    "private": "not part of the quota contract",
                }
            },
        },
    }


@pytest.fixture
def make_query(tmp_path, monkeypatch):
    """Exercise the real provider factory, domain service, and authenticated worker client."""
    monkeypatch.setenv("TEST_QUOTA_WORKER_TOKEN", "private-worker-token")
    clients = []

    def make(
        profile=None, *, config=None, status=200, worker_configured=True, transport_error=False
    ):
        profile = _profile() if profile is None else profile
        requests = []
        directories = []
        wakes = []

        class Resolver:
            def resolve_for_directory(self, directory_path):
                directories.append(directory_path)
                return GatewayConfig(
                    runtime_dir=tmp_path / "runtime",
                    container_worker_base_url=(
                        "http://worker.example.test:11888" if worker_configured else None
                    ),
                    container_worker_token_env="TEST_QUOTA_WORKER_TOKEN",
                )

        def respond(request):
            requests.append(request)
            if transport_error:
                raise httpx.ConnectError("private-worker-token private error", request=request)
            return httpx.Response(
                status, content=json.dumps(profile), headers={"Content-Type": "application/json"}
            )

        client = httpx.Client(transport=httpx.MockTransport(respond))
        clients.append(client)
        resolver = Resolver()
        auth = CodexAuthAdminService(
            CodexAuthAdminDependencies(
                provider_config=resolver,
                audit=SimpleNamespace(
                    log=lambda event: pytest.fail("quota read was audited as a mutation")
                ),
                runtime_dir=tmp_path / "runtime",
                wake_reconciliation=lambda: wakes.append(True),
                client_factory=lambda **kwargs: ContainerWorkerClient(
                    **kwargs, client=client, max_connect_attempts=1
                ),
            )
        )
        factory = build_query_provider_factory(
            runtime_dir=tmp_path / "runtime",
            provider_config_resolver=resolver,
            query_providers=DEFAULT_QUERY_PROVIDER_REGISTRY,
            admin_runtime=SimpleNamespace(codex_auth_admin=auth),
        )
        node = WorkflowNodeEnvelope(
            id="quota",
            display_name="Subscription quota",
            type=NODE_TYPE,
            config={"profile_id": "team-a", **(config or {})},
        )
        provider = factory.build(node, directory_path="root/team")
        return SimpleNamespace(
            provider=provider, requests=requests, directories=directories, wakes=wakes, node=node
        )

    yield make
    for client in clients:
        client.close()


def _validate_output(node, output):
    properties = effective_node_output_schema(node)
    jsonschema.validate(
        output,
        {
            "type": "object",
            "properties": properties,
            "required": list(properties),
            "additionalProperties": False,
        },
        format_checker=jsonschema.FormatChecker(),
    )


@pytest.mark.parametrize("enabled", [True, False])
def test_query_reads_weekly_codex_capacity_without_leaking_identity_or_waking_work(
    make_query, enabled
):
    query = make_query({**_profile(), "enabled": enabled})
    before = datetime.now(UTC)

    output = query.provider.execute()

    assert output == {
        "directory_path": "root/team",
        "profile_id": "team-a",
        "limit_id": "codex",
        "window_duration_minutes": 10080,
        "available": True,
        "unavailable_reason": None,
        "used_percent": 37.5,
        "remaining_percent": 62.5,
        "resets_at": "2040-02-10T16:00:00Z",
        "observed_at": output["observed_at"],
    }
    assert before <= datetime.fromisoformat(output["observed_at"]) <= datetime.now(UTC)
    assert len(query.requests) == 1
    request = query.requests[0]
    assert request.method == "GET"
    assert str(request.url) == "http://worker.example.test:11888/v1/codex-auth/profiles/team-a"
    assert request.headers["Authorization"] == "Bearer private-worker-token"
    assert query.wakes == []
    _validate_output(query.node, output)


@pytest.mark.parametrize("used,remaining", [(0, 100), (27.5, 72.5), (100, 0), (125, 0)])
def test_remaining_percentage_handles_empty_full_and_over_limit_windows(
    make_query, used, remaining
):
    profile = _profile()
    profile["rate_limits"] = {"default": {"secondary": _window(used=used)}}
    output = make_query(profile).provider.execute()
    assert output["available"] is True
    assert output["used_percent"] == used
    assert output["remaining_percent"] == remaining


def test_explicit_limit_window_and_descendant_worker_configuration(make_query):
    profile = _profile()
    profile["rate_limits"]["by_limit_id"]["base_model_inference"] = {
        "primary": _window(used=20, duration=300)
    }
    query = make_query(
        profile,
        config={"profile_id": "${{selection.profile_id}}"},
    )
    output = query.provider.execute(
        resolved_config={
            "profile_id": "team-a",
            "limit_id": "base_model_inference",
            "window_duration_minutes": 300,
            "directory_path": "root/team/reports",
        }
    )
    assert output["directory_path"] == "root/team/reports"
    assert output["limit_id"] == "base_model_inference"
    assert output["window_duration_minutes"] == 300
    assert output["remaining_percent"] == 80
    assert query.directories == ["root/team", "root/team/reports"]


@pytest.mark.parametrize("directory", ["root", "root/other", "root/team-other"])
def test_quota_query_cannot_read_ancestor_or_sibling_worker(make_query, directory):
    query = make_query(config={"directory_path": directory})
    with pytest.raises(ValueError, match="outside workflow directory scope"):
        query.provider.execute()
    assert query.requests == []
    assert query.directories == ["root/team"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("profile_id", ""),
        ("profile_id", "../other"),
        ("profile_id", 123),
        ("limit_id", " "),
        ("window_duration_minutes", 0),
        ("window_duration_minutes", -1),
        ("window_duration_minutes", True),
        ("window_duration_minutes", 10080.5),
        ("window_duration_minutes", "10080"),
        ("directory_path", {"path": "root"}),
    ],
)
def test_quota_schema_and_resolved_config_reject_invalid_selectors(make_query, field, value):
    with pytest.raises(ValidationError):
        materialize_node_definition(
            WorkflowNodeEnvelope(
                id="quota",
                display_name="Quota",
                type=NODE_TYPE,
                config={"profile_id": "team-a", field: value},
            )
        )
    query = make_query()
    with pytest.raises(ValidationError):
        query.provider.execute(resolved_config={field: value})
    assert query.requests == []


def test_schema_defaults_and_unresolved_bindings(make_query):
    query = make_query(config={"profile_id": "${{selection.profile_id}}"})
    definition = materialize_node_definition(query.node)
    assert isinstance(definition, CodexSubscriptionQuotaQueryDefinition)
    assert definition.limit_id == "codex"
    assert definition.window_duration_minutes == 10080
    assert definition.directory_path is None
    with pytest.raises(ValueError, match="unresolved bindings"):
        query.provider.execute()
    assert query.requests == []


@pytest.mark.parametrize(
    "patch",
    [
        {"used_percent": None},
        {"used_percent": True},
        {"used_percent": "37.5"},
        {"used_percent": -1},
        {"used_percent": float("nan")},
        {"used_percent": float("inf")},
        {"window_duration_minutes": 300},
        {"window_duration_minutes": "10080"},
        {"resets_at": None},
        {"resets_at": "invalid"},
        {"resets_at": "2040-02-10T18:00:00"},
        {"resets_at": "2000-01-01T00:00:00Z"},
    ],
)
def test_missing_malformed_or_expired_weekly_windows_are_unavailable(make_query, patch):
    profile = _profile()
    profile["rate_limits"] = {"default": {"primary": {**_window(), **patch}}}
    query = make_query(profile)
    output = query.provider.execute()
    assert output["available"] is False
    assert output["unavailable_reason"] == "window_unavailable"
    assert output["used_percent"] is None
    assert output["remaining_percent"] is None
    assert output["resets_at"] is None
    _validate_output(query.node, output)


@pytest.mark.parametrize(
    "rate_limits",
    [None, {}, {"default": {"limit_id": "base_model_inference", "primary": _window()}}],
)
def test_missing_weekly_codex_data_never_uses_another_bucket(make_query, rate_limits):
    profile = _profile()
    profile["rate_limits"] = rate_limits
    output = make_query(profile).provider.execute()
    assert output["available"] is False
    assert output["remaining_percent"] is None


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"signed_in": False}, "signed_out"),
        ({"available": False, "error": "private-account-token"}, "telemetry_unavailable"),
        ({"profile_id": "other-profile"}, "profile_mismatch"),
    ],
)
def test_unavailable_profile_status_does_not_publish_stale_quota(make_query, changes, reason):
    output = make_query({**_profile(), **changes}).provider.execute()
    assert output["available"] is False
    assert output["unavailable_reason"] == reason
    assert output["remaining_percent"] is None
    assert "private" not in str(output)


@pytest.mark.parametrize(
    "status,reason",
    [(401, "worker_unavailable"), (404, "profile_not_found"), (500, "worker_unavailable")],
)
def test_worker_failures_return_safe_unavailable_results(make_query, status, reason):
    query = make_query({"detail": "private-worker-token private-account-token"}, status=status)
    output = query.provider.execute()
    assert output["available"] is False
    assert output["unavailable_reason"] == reason
    assert output["remaining_percent"] is None
    assert "private" not in str(output)
    assert query.wakes == []
    _validate_output(query.node, output)


def test_missing_worker_or_transport_failure_is_reportable(make_query):
    unconfigured = make_query(worker_configured=False)
    assert unconfigured.provider.execute()["unavailable_reason"] == "worker_not_configured"
    assert unconfigured.requests == []
    failed = make_query(transport_error=True)
    assert failed.provider.execute()["unavailable_reason"] == "worker_unavailable"
    assert len(failed.requests) == 1


@pytest.mark.parametrize("signed_in,remaining", [(True, 62.5), (False, None)])
def test_quota_outputs_are_bindable_in_a_persisted_report_graph(make_query, signed_in, remaining):
    query = make_query({**_profile(), "signed_in": signed_in})
    template = {
        "available": "${{quota.available}}",
        "remaining": "${{quota.remaining_percent}}",
        "resets_at": "${{quota.resets_at}}",
    }
    graph = {
        "triggers": [{"id": "daily", "display_name": "Daily", "type": "trigger:manual"}],
        "queries": [query.node.model_dump(mode="json")],
        "gates": [
            {"id": "report", "display_name": "Report", "type": "gate:map", "template": template}
        ],
        "edges": [
            {"source_id": "daily", "target_id": "quota"},
            {"source_id": "quota", "target_id": "report"},
        ],
    }
    assert validate_workflow_binding_diagnostics(graph) == []
    schemas = derive_workflow_output_schemas(graph)
    result = resolve_workflow_bindings(
        template,
        {"quota": query.provider.execute()},
        {"quota": set(schemas["quota"])},
    ).resolved_config
    assert result == {
        "available": signed_in,
        "remaining": remaining,
        "resets_at": "2040-02-10T16:00:00Z" if signed_in else None,
    }

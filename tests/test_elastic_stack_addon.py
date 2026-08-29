from __future__ import annotations

import base64
import json
import os
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from mcp.server.fastmcp.exceptions import ToolError

from evander.addons import AddonContext
from evander.effects.registry import EffectProviderFactory, EffectProviderRegistry
from evander.events.models import WorkflowNodeEnvelope
from evander.policy import PolicyError
from evander.provider_config import EffectProviderConfig, QueryProviderConfig
from evander.queries.registry import QueryProviderFactory, QueryProviderRegistry
from evander_elastic_stack_addon.client import (
    ElasticStackApiError,
    ElasticStackClient,
    ElasticStackConfigError,
)
from evander_elastic_stack_addon.mcp import MCP_TOOL_NAMES, register_mcp_tools
from evander_elastic_stack_addon.models import (
    ElasticStackSettings,
    settings_from_provider_state,
)
from evander_elastic_stack_addon.providers import (
    ElasticsearchIndicesQueryProvider,
    KibanaImportSavedObjectsEffectProvider,
    KibanaSetDefaultDataViewEffectProvider,
    KibanaUpsertDataViewEffectProvider,
    _boolean,
    register_providers,
)
from evander_elastic_stack_addon.service import client_for_provider_context


def _provider_state() -> dict[str, Any]:
    return {
        "elastic_stack": {
            "auth_profiles": {
                "demo-caddy": {
                    "type": "basic_env",
                    "username_env": "TEST_KIBANA_USERNAME",
                    "password_env": "TEST_KIBANA_PASSWORD",
                    "allowed_origins": ["https://*-kibana.northeurope.cloudapp.azure.com"],
                },
                "demo-es": {
                    "type": "api_key_env",
                    "token_env": "TEST_ELASTIC_API_KEY",
                    "allowed_origins": ["https://elastic.example.test"],
                },
            },
            "targets": {
                "static": {
                    "elasticsearch": {
                        "base_url": "https://elastic.example.test",
                        "auth_profile": "demo-es",
                    }
                }
            },
        }
    }


def _session_metadata() -> dict[str, Any]:
    return {
        "elastic_stack": {
            "targets": {
                "app-demo": {
                    "kibana": {
                        "base_url": ("https://elastic-test-kibana.northeurope.cloudapp.azure.com"),
                        "auth_profile": "demo-caddy",
                    },
                    "metadata": {"environment_alias": "elastic-test"},
                }
            }
        }
    }


def _client(handler) -> ElasticStackClient:
    settings = settings_from_provider_state(_provider_state(), session_metadata=_session_metadata())
    secrets = {
        "TEST_KIBANA_USERNAME": "automation",
        "TEST_KIBANA_PASSWORD": "secret-value",
        "TEST_ELASTIC_API_KEY": "api-key-value",
    }
    return ElasticStackClient(
        settings,
        secret_resolver=secrets.get,
        transport=httpx.MockTransport(handler),
    )


def test_settings_merge_static_and_session_targets_without_exposing_auth_refs() -> None:
    settings = settings_from_provider_state(_provider_state(), session_metadata=_session_metadata())

    targets = ElasticStackClient(settings, secret_resolver=lambda _ref: None).list_targets()

    assert [target["target_id"] for target in targets] == ["app-demo", "static"]
    assert targets[0]["kibana_base_url"].startswith("https://elastic-test-kibana")
    assert "auth_profile" not in targets[0]
    assert "password_env" not in json.dumps(targets)


def test_settings_reject_unknown_profiles_and_unrestricted_origin_patterns() -> None:
    with pytest.raises(ValueError, match="unknown auth profile"):
        settings_from_provider_state(
            {
                "elastic_stack": {
                    "auth_profiles": {},
                    "targets": {
                        "bad": {
                            "kibana": {
                                "base_url": "https://kibana.example.test",
                                "auth_profile": "missing",
                            }
                        }
                    },
                }
            }
        )

    with pytest.raises(ValueError, match="host-restricted"):
        ElasticStackSettings.model_validate(
            {"auth_profiles": {"bad": {"type": "none", "allowed_origins": ["https://*"]}}}
        )


def test_kibana_data_view_calls_use_host_basic_auth_space_and_xsrf() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"data_view": [{"id": "events"}]})
        return httpx.Response(200, json={"data_view": {"id": "events"}})

    client = _client(handler)
    listed = client.kibana_list_data_views("app-demo", space_id="presentation")
    created = client.kibana_upsert_data_view(
        "app-demo",
        data_view={"id": "events", "name": "Events", "title": "events-*"},
        space_id="presentation",
    )

    assert listed["data_view"][0]["id"] == "events"
    assert created["data_view"]["id"] == "events"
    assert requests[0].url.path == "/s/presentation/api/data_views"
    assert "kbn-xsrf" not in requests[0].headers
    assert requests[1].url.path == "/s/presentation/api/data_views/data_view"
    assert requests[1].headers["kbn-xsrf"] == "evander-elastic-stack-addon"
    assert json.loads(requests[1].content) == {
        "data_view": {"id": "events", "name": "Events", "title": "events-*"},
        "override": True,
    }
    expected_auth = base64.b64encode(b"automation:secret-value").decode()
    assert requests[1].headers["authorization"] == f"Basic {expected_auth}"


def test_requests_identity_encoding_for_proxy_compatible_bounded_reads() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers.get("accept-encoding") != "identity":
            return httpx.Response(
                200,
                headers={"content-encoding": "gzip"},
                content=b'{"status":"not-really-gzipped"}',
            )
        return httpx.Response(200, json={"status": "available"})

    assert _client(handler).kibana_status("app-demo") == {"status": "available"}


def test_kibana_status_normalizes_full_and_elastic_9_redacted_responses() -> None:
    responses = iter(
        [
            {
                "status": {
                    "overall": {
                        "level": "available",
                        "summary": "All services are available",
                    }
                },
                "version": {"number": "9.5.2"},
            },
            {"status": {"overall": {"level": "available"}}},
        ]
    )

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=next(responses))

    client = _client(handler)
    full = client.kibana_status("app-demo")
    redacted = client.kibana_status("app-demo")

    assert full["level"] == "available"
    assert full["summary"] == "All services are available"
    assert full["version_number"] == "9.5.2"
    assert redacted == {
        "status": {"overall": {"level": "available"}},
        "level": "available",
    }


def test_kibana_import_validates_ndjson_and_sends_bounded_multipart() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"success": True, "successCount": 2})

    client = _client(handler)
    ndjson = (
        '{"type":"visualization","id":"vis-1","attributes":{}}\n'
        '{"type":"dashboard","id":"dash-1","attributes":{},"references":[]}\n'
    )
    result = client.kibana_import_saved_objects("app-demo", ndjson=ndjson)

    assert result == {"success": True, "successCount": 2, "submitted_object_count": 2}
    request = captured[0]
    assert request.url.path == "/api/saved_objects/_import"
    assert request.url.params["overwrite"] == "true"
    assert request.headers["kbn-xsrf"] == "evander-elastic-stack-addon"
    assert request.headers["content-type"].startswith("multipart/form-data; boundary=")
    assert b'filename="saved-objects.ndjson"' in request.content
    assert b'"id":"dash-1"' in request.content

    with pytest.raises(ValueError, match="line 1"):
        client.kibana_import_saved_objects("app-demo", ndjson="not-json")
    with pytest.raises(ValueError, match="cannot be combined"):
        client.kibana_import_saved_objects(
            "app-demo",
            ndjson=ndjson,
            overwrite=True,
            create_new_copies=True,
        )
    compatible = client.kibana_import_saved_objects(
        "app-demo",
        ndjson=ndjson,
        overwrite=True,
        compatibility_mode=True,
    )
    assert compatible["success"] is True
    assert captured[1].url.params["overwrite"] == "true"
    assert captured[1].url.params["compatibilityMode"] == "true"
    with pytest.raises(ValueError, match="create_new_copies cannot be combined"):
        client.kibana_import_saved_objects(
            "app-demo",
            ndjson=ndjson,
            overwrite=False,
            create_new_copies=True,
            compatibility_mode=True,
        )
    assert len(captured) == 2


def test_elasticsearch_queries_use_api_key_and_restrict_index_paths() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/_mapping"):
            return httpx.Response(200, json={"events-2026": {"mappings": {}}})
        return httpx.Response(200, json=[{"index": "events-2026", "health": "green"}])

    client = _client(handler)
    items = client.elasticsearch_list_indices("static", index_pattern="events-*")
    mappings = client.elasticsearch_get_mappings("static", index_pattern="events-*")

    assert items[0]["index"] == "events-2026"
    assert mappings == {"events-2026": {"mappings": {}}}
    assert requests[0].headers["authorization"] == "ApiKey api-key-value"
    assert requests[0].url.path == "/_cat/indices/events-*"
    with pytest.raises(ValueError, match="unsupported"):
        client.elasticsearch_get_mappings("static", index_pattern="../_security")


def test_client_rejects_origin_before_sending_secret_and_redacts_upstream_errors() -> None:
    calls = 0
    secret_calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(500, text="secret-value should not escape")

    settings = settings_from_provider_state(
        _provider_state(),
        session_metadata={
            "elastic_stack": {
                "targets": {
                    "bad": {
                        "kibana": {
                            "base_url": "https://attacker.example.test",
                            "auth_profile": "demo-caddy",
                        }
                    }
                }
            }
        },
    )

    def secret_resolver(_ref: str) -> str:
        nonlocal secret_calls
        secret_calls += 1
        return "secret-value"

    client = ElasticStackClient(
        settings,
        secret_resolver=secret_resolver,
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ElasticStackConfigError, match="not allowed") as rejected:
        client.kibana_status("bad")
    assert "secret-value" not in str(rejected.value)
    assert calls == 0
    assert secret_calls == 0

    allowed = _client(handler)
    with pytest.raises(ElasticStackApiError, match="HTTP 500") as upstream:
        allowed.kibana_status("app-demo")
    assert "secret-value" not in str(upstream.value)
    assert calls == 1


@pytest.mark.parametrize(
    ("allowed_origin", "base_url", "canonical_allowed", "canonical_base_url"),
    [
        (
            "HTTPS://[2001:DB8::1]:443",
            "https://[2001:db8::1]:443",
            "https://[2001:db8::1]",
            "https://[2001:db8::1]",
        ),
        (
            "https://*.EXAMPLE.test:443",
            "https://KIBANA.example.TEST:443",
            "https://*.example.test",
            "https://kibana.example.test",
        ),
        (
            "http://kibana.example.test:80",
            "http://kibana.example.test",
            "http://kibana.example.test",
            "http://kibana.example.test",
        ),
        (
            "https://[2001:db8::1]:8443",
            "https://[2001:DB8::1]:8443",
            "https://[2001:db8::1]:8443",
            "https://[2001:db8::1]:8443",
        ),
    ],
)
def test_origin_allowlists_and_endpoints_share_canonical_ipv6_and_port_forms(
    allowed_origin: str,
    base_url: str,
    canonical_allowed: str,
    canonical_base_url: str,
) -> None:
    requests: list[httpx.Request] = []
    settings = ElasticStackSettings.model_validate(
        {
            "auth_profiles": {"canonical": {"type": "none", "allowed_origins": [allowed_origin]}},
            "targets": {
                "canonical": {"kibana": {"base_url": base_url, "auth_profile": "canonical"}}
            },
        }
    )
    client = ElasticStackClient(
        settings,
        secret_resolver=lambda _ref: None,
        transport=httpx.MockTransport(
            lambda request: requests.append(request) or httpx.Response(200, json={})
        ),
    )

    assert client.kibana_status("canonical") == {}
    assert settings.auth_profiles["canonical"].allowed_origins == [canonical_allowed]
    assert settings.targets["canonical"].kibana is not None
    assert settings.targets["canonical"].kibana.base_url == canonical_base_url
    assert len(requests) == 1


def test_nondefault_origin_port_mismatch_is_rejected_before_secret_or_transport() -> None:
    secret_calls: list[str] = []
    transport_calls: list[httpx.Request] = []
    settings = ElasticStackSettings.model_validate(
        {
            "auth_profiles": {
                "restricted": {
                    "type": "bearer_env",
                    "token_env": "ELASTIC_TEST_TOKEN",
                    "allowed_origins": ["https://kibana.example.test:8443"],
                }
            },
            "targets": {
                "wrong-port": {
                    "kibana": {
                        "base_url": "https://kibana.example.test:9443",
                        "auth_profile": "restricted",
                    }
                }
            },
        }
    )
    client = ElasticStackClient(
        settings,
        secret_resolver=lambda ref: secret_calls.append(ref) or "secret",
        transport=httpx.MockTransport(
            lambda request: transport_calls.append(request) or httpx.Response(200, json={})
        ),
    )

    with pytest.raises(ElasticStackConfigError, match="not allowed"):
        client.kibana_status("wrong-port")
    assert secret_calls == []
    assert transport_calls == []


def test_workflow_session_target_rejects_out_of_scope_sessions_before_config_or_secrets() -> None:
    class Resolver:
        def __init__(self) -> None:
            self.calls: list[Any] = []

        def resolve_for_session_manifest(self, session: Any) -> dict[str, Any]:
            self.calls.append(session)
            return {"provider_state": _provider_state()}

    def context(records: Any, resolver: Resolver, *, directory_path: str = "root/a") -> Any:
        secret_calls: list[str] = []

        def resolve_secret(ref: str) -> None:
            secret_calls.append(ref)
            return None

        return SimpleNamespace(
            metadata={"directory_path": directory_path},
            session_lookup=SimpleNamespace(read_all_sessions=lambda: records),
            provider_config_resolver=resolver,
            resolve_secret=resolve_secret,
            secret_calls=secret_calls,
        )

    rejected_records = [
        {"session_id": "sibling", "directory_path": "root/b"},
        {"session_id": "ancestor", "directory_path": "root"},
        {"session_id": "prefix", "directory_path": "root/a-evil"},
    ]
    for records in (
        {record["session_id"]: record for record in rejected_records},
        [SimpleNamespace(**record) for record in rejected_records],
    ):
        resolver = Resolver()
        runtime_context = context(records, resolver)
        for session_id in ("sibling", "ancestor", "prefix"):
            with pytest.raises(ValueError, match="outside workflow directory scope"):
                client_for_provider_context(runtime_context, session_id=session_id)
        assert resolver.calls == []
        assert runtime_context.secret_calls == []

    allowed_records = {
        "same": SimpleNamespace(session_id="same", directory_path="root/a", metadata={}),
        "child": SimpleNamespace(session_id="child", directory_path="root/a/child", metadata={}),
        "other": SimpleNamespace(session_id="other", directory_path="root/b", metadata={}),
    }
    resolver = Resolver()
    runtime_context = context(allowed_records, resolver)
    assert client_for_provider_context(runtime_context, session_id="same").list_targets()
    assert client_for_provider_context(runtime_context, session_id="child").list_targets()
    root_context = context(allowed_records, resolver, directory_path="root")
    assert client_for_provider_context(root_context, session_id="other").list_targets()
    assert [session.session_id for session in resolver.calls] == ["same", "child", "other"]


def test_sessionless_workflow_providers_resolve_directory_state_through_real_factories(
    monkeypatch, tmp_path
) -> None:
    calls: list[tuple[str, str]] = []

    class Resolver:
        def resolve_query_provider_config(self, directory_path: str) -> QueryProviderConfig:
            calls.append(("query", directory_path))
            return QueryProviderConfig(runtime_dir=tmp_path)

        def resolve_effect_provider_config(self, directory_path: str) -> EffectProviderConfig:
            calls.append(("effect", directory_path))
            return EffectProviderConfig(
                runtime_dir=tmp_path,
                session_store_path=tmp_path / "sessions.json",
                default_session_ttl_seconds=3600,
            )

        def resolve_for_directory(self, directory_path: str) -> Any:
            calls.append(("full", directory_path))
            return SimpleNamespace(provider_state=_provider_state())

    resolved_settings: list[ElasticStackSettings] = []

    class FakeClient:
        def __init__(self, settings: ElasticStackSettings, **_kwargs: Any) -> None:
            resolved_settings.append(settings)

        def elasticsearch_cluster_health(self, target_id: str) -> dict[str, Any]:
            return {"target_id": target_id, "status": "green"}

        def kibana_set_default_data_view(self, target_id: str, **_kwargs: Any) -> dict[str, Any]:
            return {"target_id": target_id, "acknowledged": True}

    monkeypatch.setattr("evander_elastic_stack_addon.service.ElasticStackClient", FakeClient)

    registration = register_providers()
    resolver = Resolver()
    query_definition = registration.node_definition_resolvers["query:elasticsearch:cluster-health"](
        WorkflowNodeEnvelope(
            id="cluster-health",
            display_name="Cluster health",
            type="query:elasticsearch:cluster-health",
            config={"target_id": "static"},
        )
    )
    query = QueryProviderFactory(
        resolver,
        registry=QueryProviderRegistry(registration.query_providers),
    ).build(query_definition, directory_path="root/operations")
    assert "provider_state" not in query.runtime_context.provider_config
    assert query.execute()["status"] == "green"

    effect_definition = registration.node_definition_resolvers[
        "effect:kibana:set-default-data-view"
    ](
        WorkflowNodeEnvelope(
            id="set-default",
            display_name="Set default data view",
            type="effect:kibana:set-default-data-view",
            config={"target_id": "static", "data_view_id": "events"},
        )
    )
    effect = EffectProviderFactory(
        resolver,
        registry=EffectProviderRegistry(registration.effect_providers),
    ).build(effect_definition, directory_path="root/operations")
    assert "provider_state" not in effect.runtime_context.provider_config
    assert effect.execute()["acknowledged"] is True

    assert [sorted(settings.targets) for settings in resolved_settings] == [
        ["static"],
        ["static"],
    ]
    assert calls == [
        ("query", "root/operations"),
        ("full", "root/operations"),
        ("effect", "root/operations"),
        ("full", "root/operations"),
    ]


def test_mcp_tools_are_policy_scoped_and_delegate_semantic_operations(monkeypatch) -> None:
    tools: dict[str, Any] = {}

    class Server:
        def tool(self, *, name: str, description: str):
            assert description

            def decorator(func):
                tools[name] = func
                return func

            return decorator

    class Policy:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        def require_tool(self, session_id: str, tool_name: str):
            self.calls.append((session_id, tool_name))
            if tool_name == "kibana_import_saved_objects":
                raise PolicyError("tool is not allowed")
            return SimpleNamespace(
                session_id=session_id,
                directory_path="root/example/true-app",
                metadata=_session_metadata(),
            )

    class FakeClient:
        def list_targets(self):
            return [{"target_id": "app-demo"}]

        def kibana_list_data_views(self, target_id: str, *, space_id: str | None = None):
            return {"data_view": [{"id": target_id, "space": space_id}]}

    policy = Policy()
    runtime = SimpleNamespace(policy=policy)
    context = AddonContext(
        addon_id="elastic-stack-addon",
        runtime_dir=os.getcwd(),
        addon_runtime_dir=os.getcwd(),
        service=runtime,
    )
    monkeypatch.setattr(
        "evander_elastic_stack_addon.mcp.client_for_session",
        lambda _runtime, _session: FakeClient(),
    )

    names = register_mcp_tools(Server(), context)
    listed = tools["elastic_stack_list_targets"](session_id="session-1")
    views = tools["kibana_list_data_views"](
        "app-demo", space_id="presentation", session_id="session-1"
    )

    assert names == MCP_TOOL_NAMES
    assert listed == {"items": [{"target_id": "app-demo"}], "count": 1}
    assert views["data_view"][0] == {"id": "app-demo", "space": "presentation"}
    assert policy.calls[:2] == [
        ("session-1", "elastic_stack_list_targets"),
        ("session-1", "kibana_list_data_views"),
    ]
    with pytest.raises(ToolError, match="not allowed"):
        tools["kibana_import_saved_objects"](
            "app-demo", '{"type":"dashboard"}\n', session_id="session-1"
        )


def test_workflow_provider_registration_materializes_and_executes(monkeypatch) -> None:
    registration = register_providers()
    assert set(registration.query_providers) >= {
        "query:elasticsearch:indices",
        "query:kibana:data-views",
    }
    assert set(registration.effect_providers) >= {
        "effect:kibana:upsert-data-view",
        "effect:kibana:import-saved-objects",
    }
    assert (
        registration.node_definition_resolvers["effect:kibana:import-saved-objects"](
            WorkflowNodeEnvelope(
                id="import-1",
                display_name="Import saved objects",
                type="effect:kibana:import-saved-objects",
                config={"target_id": "demo", "ndjson": '{"type":"dashboard"}\n'},
            )
        ).target_id
        == "demo"
    )
    bound = registration.node_definition_resolvers["effect:kibana:upsert-data-view"](
        WorkflowNodeEnvelope(
            id="upsert-1",
            display_name="Upsert data view",
            type="effect:kibana:upsert-data-view",
            config={
                "target_id": "${{gate-map.target_id}}",
                "session_id": "${{effect-agent-create.session_id}}",
                "data_view": "${{gate-map.data_view}}",
                "override": "${{gate-map.override}}",
            },
        )
    )
    assert bound.target_id == "${{gate-map.target_id}}"
    clear_default = registration.node_definition_resolvers["effect:kibana:set-default-data-view"](
        WorkflowNodeEnvelope(
            id="clear-default",
            display_name="Clear default data view",
            type="effect:kibana:set-default-data-view",
            config={"target_id": "demo"},
        )
    )
    assert clear_default.data_view_id is None

    class FakeClient:
        def elasticsearch_list_indices(self, target_id: str, **kwargs):
            return [{"index": target_id, **kwargs}]

        def kibana_import_saved_objects(self, target_id: str, **kwargs):
            return {"success": True, "target_id": target_id, **kwargs}

    monkeypatch.setattr(
        "evander_elastic_stack_addon.providers.client_for_provider_context",
        lambda *_args, **_kwargs: FakeClient(),
    )

    query = ElasticsearchIndicesQueryProvider()
    query.query_definition = SimpleNamespace(
        model_dump=lambda **_kwargs: {
            "type": "query:elasticsearch:indices",
            "target_id": "demo",
            "index_pattern": "events-*",
            "include_hidden": False,
        }
    )
    query.runtime_context = SimpleNamespace()
    assert query.execute()["items"] == [
        {"index": "demo", "index_pattern": "events-*", "include_hidden": False}
    ]

    effect = KibanaImportSavedObjectsEffectProvider()
    effect.effect_definition = SimpleNamespace(
        model_dump=lambda **_kwargs: {
            "type": "effect:kibana:import-saved-objects",
            "target_id": "demo",
            "ndjson": '{"type":"dashboard"}\n',
            "overwrite": True,
            "create_new_copies": False,
            "compatibility_mode": False,
        }
    )
    effect.runtime_context = SimpleNamespace()
    assert effect.execute()["success"] is True


@pytest.mark.parametrize(
    ("overwrite", "create_new_copies", "compatibility_mode"),
    [
        (True, True, False),
        (False, True, True),
    ],
)
def test_import_definition_rejects_mutually_exclusive_modes(
    overwrite: bool,
    create_new_copies: bool,
    compatibility_mode: bool,
) -> None:
    resolver = register_providers().node_definition_resolvers["effect:kibana:import-saved-objects"]
    with pytest.raises(ValueError, match="cannot be combined"):
        resolver(
            WorkflowNodeEnvelope(
                id="invalid-import",
                display_name="Invalid import",
                type="effect:kibana:import-saved-objects",
                config={
                    "target_id": "demo",
                    "ndjson": "{}\n",
                    "overwrite": overwrite,
                    "create_new_copies": create_new_copies,
                    "compatibility_mode": compatibility_mode,
                },
            )
        )

    legal = resolver(
        WorkflowNodeEnvelope(
            id="legal-import",
            display_name="Legal import",
            type="effect:kibana:import-saved-objects",
            config={
                "target_id": "demo",
                "ndjson": "{}\n",
                "compatibility_mode": True,
            },
        )
    )
    assert legal.overwrite is True
    assert legal.compatibility_mode is True


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (True, True),
        (False, False),
        ("true", True),
        ("false", False),
        ("1", True),
        ("0", False),
        ("YES", True),
        ("off", False),
    ],
)
def test_workflow_boolean_parser_accepts_only_explicit_boolean_forms(
    value: Any, expected: bool
) -> None:
    assert _boolean(value, field_name="test_field", default=not expected) is expected


@pytest.mark.parametrize("value", [1, 0, "", "sometimes", [], {}, object()])
def test_workflow_boolean_parser_rejects_ambiguous_values(value: Any) -> None:
    with pytest.raises(ValueError, match="test_field must be a boolean"):
        _boolean(value, field_name="test_field", default=False)


def test_bound_workflow_boolean_strings_reach_all_clients_as_booleans(monkeypatch) -> None:
    calls: dict[str, dict[str, Any]] = {}

    class FakeClient:
        def elasticsearch_list_indices(self, target_id: str, **kwargs: Any) -> list[dict[str, Any]]:
            calls["indices"] = {"target_id": target_id, **kwargs}
            return []

        def kibana_upsert_data_view(self, target_id: str, **kwargs: Any) -> dict[str, Any]:
            calls["upsert"] = {"target_id": target_id, **kwargs}
            return {"ok": True}

        def kibana_import_saved_objects(self, target_id: str, **kwargs: Any) -> dict[str, Any]:
            calls["import"] = {"target_id": target_id, **kwargs}
            return {"ok": True}

        def kibana_set_default_data_view(self, target_id: str, **kwargs: Any) -> dict[str, Any]:
            calls["default"] = {"target_id": target_id, **kwargs}
            return {"ok": True}

    monkeypatch.setattr(
        "evander_elastic_stack_addon.providers.client_for_provider_context",
        lambda *_args, **_kwargs: FakeClient(),
    )

    query = ElasticsearchIndicesQueryProvider()
    query.query_definition = SimpleNamespace(
        model_dump=lambda **_kwargs: {
            "type": "query:elasticsearch:indices",
            "target_id": "demo",
        }
    )
    query.runtime_context = SimpleNamespace()
    query.execute(resolved_config={"include_hidden": "false"})

    upsert = KibanaUpsertDataViewEffectProvider()
    upsert.effect_definition = SimpleNamespace(
        model_dump=lambda **_kwargs: {
            "type": "effect:kibana:upsert-data-view",
            "target_id": "demo",
            "data_view": {"title": "events-*"},
        }
    )
    upsert.runtime_context = SimpleNamespace()
    upsert.execute(resolved_config={"override": "false"})

    saved_object_import = KibanaImportSavedObjectsEffectProvider()
    saved_object_import.effect_definition = SimpleNamespace(
        model_dump=lambda **_kwargs: {
            "type": "effect:kibana:import-saved-objects",
            "target_id": "demo",
            "ndjson": "{}\n",
        }
    )
    saved_object_import.runtime_context = SimpleNamespace()
    saved_object_import.execute(
        resolved_config={
            "overwrite": "false",
            "create_new_copies": "true",
            "compatibility_mode": "false",
        }
    )

    set_default = KibanaSetDefaultDataViewEffectProvider()
    set_default.effect_definition = SimpleNamespace(
        model_dump=lambda **_kwargs: {
            "type": "effect:kibana:set-default-data-view",
            "target_id": "demo",
        }
    )
    set_default.runtime_context = SimpleNamespace()
    set_default.execute(resolved_config={"force": "false"})

    assert calls["indices"]["include_hidden"] is False
    assert calls["upsert"]["override"] is False
    assert calls["import"]["overwrite"] is False
    assert calls["import"]["create_new_copies"] is True
    assert calls["import"]["compatibility_mode"] is False
    assert calls["default"]["data_view_id"] is None
    assert calls["default"]["force"] is False

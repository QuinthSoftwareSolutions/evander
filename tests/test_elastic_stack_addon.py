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
from evander.events.models import WorkflowNodeEnvelope
from evander.policy import PolicyError
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
    register_providers,
)


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
    client = ElasticStackClient(
        settings,
        secret_resolver=lambda _ref: "secret-value",
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ElasticStackConfigError, match="not allowed") as rejected:
        client.kibana_status("bad")
    assert "secret-value" not in str(rejected.value)
    assert calls == 0

    allowed = _client(handler)
    with pytest.raises(ElasticStackApiError, match="HTTP 500") as upstream:
        allowed.kibana_status("app-demo")
    assert "secret-value" not in str(upstream.value)
    assert calls == 1


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

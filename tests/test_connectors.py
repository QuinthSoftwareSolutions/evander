from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from mcp.server.fastmcp.exceptions import ToolError
from pydantic import ValidationError
from starlette.testclient import TestClient

from evander.addons import AddonRegistration, LoadedAddonCatalog
from evander.admin.composition import build_admin_runtime
from evander.admin.web.app import create_app
from evander.admin.web.rbac import OWNER_GROUP_ID
from evander.admin.services.approval_audit_service import tool_call_audit_entry_from_payload
from evander.communication_artifacts import CommunicationArtifactStore
from evander.config import GatewayConfig
from evander.connectors.drivers import ConnectorInvocationError
from evander.connectors.models import (
    ConnectorAuthProfile,
    ConnectorDirectoryConfig,
    ConnectorInvocationResult,
    validate_connector_directory_overlay,
)
from evander.connectors.service import (
    ConnectorPolicyError,
    ConnectorService,
    connector_registry_from_addons,
    default_connector_registry,
)
from evander.effects.providers.vm_create import (
    _validated_connector_grants as validate_vm_connector_grants,
)
from evander.gateway import create_server
from evander.models import (
    ApprovalGrantSettings,
    ApprovalPayloadMode,
    ApprovalStatus,
    SessionManifest,
    ToolName,
)
from evander.runtime import build_runtime


def _config(
    *,
    base_url: str = "https://files.example.test",
    auth_profile: str | None = None,
    requires_approval: bool = False,
    access: str = "read",
    result_mode: str = "json",
    method: str = "GET",
) -> ConnectorDirectoryConfig:
    payload: dict[str, object] = {
        "connectors": {
            "documents": {
                "display_name": "Documents",
                "description": "Approved document service",
                "driver": "rest",
                "config": {"base_url": base_url},
                "operations": {
                    "get_document": {
                        "description": "Fetch one document",
                        "input_schema": {
                            "type": "object",
                            "properties": {"id": {"type": "string"}},
                            "required": ["id"],
                            "additionalProperties": False,
                        },
                        "access": access,
                        "requires_approval": requires_approval,
                        "result_mode": result_mode,
                        "config": {"method": method, "path": "/documents/{id}"},
                    }
                },
            }
        }
    }
    if auth_profile is not None:
        payload["auth_profiles"] = {
            auth_profile: {"type": "bearer_env", "token_env": "DOCUMENTS_TOKEN"}
        }
        payload["connectors"]["documents"]["auth_profile"] = auth_profile  # type: ignore[index]
    return ConnectorDirectoryConfig.model_validate(payload)


def _session(*, grants: dict[str, list[str]] | None = None) -> SessionManifest:
    return SessionManifest(
        session_id="session-connectors",
        directory_path="root/team",
        allowed_tools=[ToolName.LIST_CONNECTORS, ToolName.CALL_CONNECTOR],
        connector_grants=({"documents": ["get_document"]} if grants is None else grants),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )


def _service(
    tmp_path: Path,
    config: ConnectorDirectoryConfig,
    *,
    rest_transport: httpx.AsyncBaseTransport | None = None,
    auth_transport: httpx.AsyncBaseTransport | None = None,
    environ: dict[str, str] | None = None,
) -> ConnectorService:
    provider_config = SimpleNamespace(
        resolve_connectors_for_directory=lambda _directory_path: config
    )
    return ConnectorService(
        runtime_dir=tmp_path,
        provider_config=provider_config,
        artifact_store_factory=lambda: CommunicationArtifactStore(tmp_path),
        registry=default_connector_registry(rest_transport=rest_transport),
        auth_transport=auth_transport,
        environ=environ or {},
    )


def _gateway_config_path(tmp_path: Path) -> Path:
    runtime_dir = tmp_path / "runtime"
    path = tmp_path / "gateway.yaml"
    path.write_text(
        "\n".join(
            [
                f'runtime_dir: "{runtime_dir}"',
                f'audit_log_path: "{runtime_dir / "audit.jsonl"}"',
                f'session_store_path: "{runtime_dir / "sessions.json"}"',
                f'approval_store_path: "{runtime_dir / "approvals.json"}"',
                f'draft_store_path: "{runtime_dir / "drafts.json"}"',
                f'email_store_path: "{runtime_dir / "emails.json"}"',
                f'discord_command_store_path: "{runtime_dir / "discord.json"}"',
                "enable_directory_scoped_config: true",
            ]
        ),
        encoding="utf-8",
    )
    return path


def test_connector_definition_rejects_reserved_headers_and_requires_explicit_approval() -> None:
    payload = _config().model_dump(mode="json")
    payload["connectors"]["documents"]["config"]["headers"] = {  # type: ignore[index]
        "Authorization": "not-allowed"
    }
    with pytest.raises(ValueError, match="reserved"):
        service = _service(
            Path("/tmp/unused-connectors-test"), ConnectorDirectoryConfig.model_validate(payload)
        )
        service.validate_config(service.provider_config.resolve_connectors_for_directory("root"))

    del payload["connectors"]["documents"]["operations"]["get_document"][  # type: ignore[index]
        "requires_approval"
    ]
    with pytest.raises(ValidationError, match="requires_approval"):
        ConnectorDirectoryConfig.model_validate(payload)


@pytest.mark.parametrize("keyword", ["$ref", "$dynamicRef"])
def test_connector_definition_rejects_remote_input_schema_references(keyword: str) -> None:
    payload = _config().model_dump(mode="json")
    payload["connectors"]["documents"]["operations"]["get_document"][  # type: ignore[index]
        "input_schema"
    ] = {keyword: "https://schemas.example.test/connector-input.json"}

    with pytest.raises(ValidationError, match="local fragment reference"):
        ConnectorDirectoryConfig.model_validate(payload)


@pytest.mark.parametrize(
    "input_schema",
    [
        {"type": "array", "items": {"type": "string"}},
        {"oneOf": [{"type": "string"}]},
        {"not": {"type": "object"}},
        {"not": {"properties": {}}},
        {"allOf": [{"type": "object"}, {"not": {"type": "object"}}]},
        {"$defs": {"arguments": {"type": "string"}}, "$ref": "#/$defs/arguments"},
    ],
)
def test_connector_definition_rejects_input_schema_that_excludes_objects(
    input_schema: dict[str, object],
) -> None:
    payload = _config().model_dump(mode="json")
    payload["connectors"]["documents"]["operations"]["get_document"][  # type: ignore[index]
        "input_schema"
    ] = input_schema

    with pytest.raises(ValidationError, match="must allow object arguments"):
        ConnectorDirectoryConfig.model_validate(payload)


@pytest.mark.parametrize(
    "input_schema",
    [
        {"oneOf": [{"type": "string"}, {"type": "object"}]},
        {"$defs": {"arguments": {"type": "object"}}, "$ref": "#/$defs/arguments"},
        {"not": {"type": "string"}},
    ],
)
def test_connector_definition_accepts_object_compatible_composed_schema(
    input_schema: dict[str, object],
) -> None:
    payload = _config().model_dump(mode="json")
    payload["connectors"]["documents"]["operations"]["get_document"][  # type: ignore[index]
        "input_schema"
    ] = input_schema

    config = ConnectorDirectoryConfig.model_validate(payload)

    assert config.connectors["documents"].operations["get_document"].input_schema == input_schema


@pytest.mark.parametrize(
    ("headers", "error"),
    [
        ({"Bad Header": "value"}, "valid HTTP field name"),
        ({"X-Invalid": "first\nsecond"}, "invalid HTTP characters"),
    ],
)
def test_connector_definition_rejects_invalid_fixed_header_syntax(
    headers: dict[str, str], error: str
) -> None:
    payload = _config().model_dump(mode="json")
    payload["connectors"]["documents"]["config"]["headers"] = headers  # type: ignore[index]
    config = ConnectorDirectoryConfig.model_validate(payload)
    service = _service(Path("/tmp/unused-connectors-test"), config)

    with pytest.raises(ValueError, match=error):
        service.validate_config(config)


@pytest.mark.parametrize(
    "header_name",
    [
        "Connection",
        "Content-Length",
        "Transfer-Encoding",
        "Proxy-Authorization",
        "evander-mcp-session-token",
    ],
)
def test_api_key_auth_profile_rejects_protected_headers(header_name: str) -> None:
    with pytest.raises(ValidationError, match="header_name is reserved"):
        ConnectorAuthProfile.model_validate(
            {
                "type": "api_key_header_env",
                "token_env": "DOCUMENTS_API_KEY",
                "header_name": header_name,
            }
        )


def test_connector_overlay_accepts_partial_child_override_and_normalizes_names() -> None:
    overlay = validate_connector_directory_overlay(
        {
            "connectors": {
                "Documents": {
                    "operations": {
                        "Get_Document": {
                            "requires_approval": True,
                            "timeout_seconds": 12,
                        }
                    }
                }
            }
        }
    )

    assert overlay == {
        "connectors": {
            "documents": {
                "operations": {
                    "get_document": {
                        "requires_approval": True,
                        "timeout_seconds": 12.0,
                    }
                }
            }
        }
    }


def test_addon_connector_driver_is_registered(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class ExampleDriver:
        def validate_definition(self, _definition: object) -> None:
            return None

        async def invoke(self, **_kwargs: object) -> object:
            raise NotImplementedError

    catalog = LoadedAddonCatalog(
        registrations=(
            AddonRegistration(
                addon_id="example-addon",
                display_name="Example",
                connector_drivers={"example_service": ExampleDriver},
            ),
        ),
        loaded_addons=("example-addon",),
    )
    monkeypatch.setattr("evander.addons.load_addons", lambda *_args, **_kwargs: catalog)

    registry = connector_registry_from_addons({}, base_dir=tmp_path)

    assert registry.driver_names == ("example_service", "filesystem", "rest")
    registry.require("example_service").validate_definition(_config().connectors["documents"])


def test_vm_create_connector_grants_preserve_absence_and_validate_operations() -> None:
    config = _config()
    context = SimpleNamespace(
        provider_config_resolver=SimpleNamespace(
            resolve_connectors_for_directory=lambda _directory: config
        )
    )

    assert (
        validate_vm_connector_grants(
            context,
            directory_path="root/team",
            session_id="session-connectors",
            value=None,
        )
        is None
    )
    assert validate_vm_connector_grants(
        context,
        directory_path="root/team",
        session_id="session-connectors",
        value={"documents": ["get_document"]},
    ) == {"documents": ["get_document"]}
    with pytest.raises(ValueError, match="unknown or disabled connector operation"):
        validate_vm_connector_grants(
            context,
            directory_path="root/team",
            session_id="session-connectors",
            value={"documents": ["delete_document"]},
        )


def test_list_only_returns_operations_granted_to_the_session(tmp_path: Path) -> None:
    config = _config(auth_profile="documents-auth")
    service = _service(tmp_path, config, environ={})

    listed = service.list_for_session(_session())

    assert listed == [
        {
            "connector_id": "documents",
            "display_name": "Documents",
            "description": "Approved document service",
            "available": False,
            "operations": [
                {
                    "name": "get_document",
                    "description": "Fetch one document",
                    "input_schema": config.connectors["documents"]
                    .operations["get_document"]
                    .input_schema,
                    "access": "read",
                    "requires_approval": False,
                    "result_mode": "json",
                }
            ],
        }
    ]
    assert "DOCUMENTS_TOKEN" not in str(listed)
    assert "files.example.test" not in str(listed)


def test_rest_connector_injects_host_auth_and_validates_arguments(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": "alpha", "ok": True})

    service = _service(
        tmp_path,
        _config(auth_profile="documents-auth"),
        rest_transport=httpx.MockTransport(handler),
        environ={"DOCUMENTS_TOKEN": "host-secret"},
    )

    result = asyncio.run(
        service.invoke(
            session=_session(),
            connector_id="documents",
            operation_name="get_document",
            arguments={"id": "alpha"},
        )
    )

    assert result.value == {"id": "alpha", "ok": True}
    assert requests[0].url == httpx.URL("https://files.example.test/documents/alpha")
    assert requests[0].headers["authorization"] == "Bearer host-secret"
    with pytest.raises(ConnectorPolicyError, match="do not match schema"):
        service.require_operation(_session(), "documents", "get_document", {})
    with pytest.raises(ConnectorPolicyError, match="not granted"):
        service.require_operation(_session(grants={}), "documents", "get_document", {"id": "alpha"})


def test_connector_runtime_schema_validation_never_retrieves_remote_references(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config()
    config.connectors["documents"].operations["get_document"].input_schema = {
        "$ref": "https://schemas.example.test/connector-input.json"
    }
    monkeypatch.setattr(
        "urllib.request.urlopen",
        lambda *_args, **_kwargs: pytest.fail("connector schemas must not be retrieved"),
    )

    with pytest.raises(ConnectorPolicyError, match="unsupported reference"):
        _service(tmp_path, config).require_operation(
            _session(),
            "documents",
            "get_document",
            {"id": "alpha"},
        )


def test_rest_connector_follows_303_with_get_and_without_the_mutation_body(
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(303, headers={"Location": "/documents/result"})
        return httpx.Response(200, json={"ok": True})

    config = _config(access="write", method="POST")
    operation = config.connectors["documents"].operations["get_document"]
    operation.input_schema = {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "payload": {"type": "object"},
        },
        "required": ["id", "payload"],
        "additionalProperties": False,
    }
    operation.config.update(
        {
            "follow_same_origin_redirects": True,
            "json_body_argument": "payload",
        }
    )
    service = _service(
        tmp_path,
        config,
        rest_transport=httpx.MockTransport(handler),
    )

    result = asyncio.run(
        service.invoke(
            session=_session(),
            connector_id="documents",
            operation_name="get_document",
            arguments={"id": "alpha", "payload": {"name": "updated"}},
        )
    )

    assert result.value == {"ok": True}
    assert [request.method for request in requests] == ["POST", "GET"]
    assert requests[0].content == b'{"name":"updated"}'
    assert requests[1].url == httpx.URL("https://files.example.test/documents/result")
    assert requests[1].content == b""


def test_rest_connector_rejects_response_beyond_configured_limit(tmp_path: Path) -> None:
    config = _config(result_mode="text")
    config.connectors["documents"].operations["get_document"].max_response_bytes = 8

    service = _service(
        tmp_path,
        config,
        rest_transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, content=b"response is too large")
        ),
    )

    with pytest.raises(ConnectorInvocationError, match="max_response_bytes"):
        asyncio.run(
            service.invoke(
                session=_session(),
                connector_id="documents",
                operation_name="get_document",
                arguments={"id": "alpha"},
            )
        )


def test_rest_connector_redacts_transport_failure_details(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("secret upstream detail", request=request)

    service = _service(
        tmp_path,
        _config(),
        rest_transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ConnectorInvocationError) as raised:
        asyncio.run(
            service.invoke(
                session=_session(),
                connector_id="documents",
                operation_name="get_document",
                arguments={"id": "alpha"},
            )
        )
    assert str(raised.value) == "connector upstream request failed"


def test_filesystem_connector_stages_artifact_and_blocks_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "approved"
    root.mkdir()
    (root / "report.txt").write_text("approved report", encoding="utf-8")
    (root / "rapport 2026 å.pdf").write_text("unicode report", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    config = ConnectorDirectoryConfig.model_validate(
        {
            "connectors": {
                "reports": {
                    "display_name": "Reports",
                    "description": "Approved report files",
                    "driver": "filesystem",
                    "config": {"root": str(root)},
                    "operations": {
                        "get_report": {
                            "description": "Fetch a report",
                            "input_schema": {
                                "type": "object",
                                "properties": {"name": {"type": "string"}},
                                "required": ["name"],
                                "additionalProperties": False,
                            },
                            "access": "read",
                            "requires_approval": False,
                            "result_mode": "artifact",
                            "config": {"path": "{name}"},
                        }
                    },
                }
            }
        }
    )
    service = _service(tmp_path, config)
    session = _session(grants={"reports": ["get_report"]})

    result = asyncio.run(
        service.invoke(
            session=session,
            connector_id="reports",
            operation_name="get_report",
            arguments={"name": "report.txt"},
        )
    )

    assert result.result_mode == "artifact"
    assert result.artifact is not None
    staged = CommunicationArtifactStore(tmp_path).get(result.artifact.artifact_id)
    assert staged.blob_path.read_text(encoding="utf-8") == "approved report"

    unicode_result = asyncio.run(
        service.invoke(
            session=session,
            connector_id="reports",
            operation_name="get_report",
            arguments={"name": "rapport 2026 å.pdf"},
        )
    )
    assert unicode_result.artifact is not None
    unicode_staged = CommunicationArtifactStore(tmp_path).get(unicode_result.artifact.artifact_id)
    assert unicode_staged.blob_path.read_text(encoding="utf-8") == "unicode report"

    with pytest.raises(ConnectorInvocationError, match="approved root"):
        asyncio.run(
            service.invoke(
                session=session,
                connector_id="reports",
                operation_name="get_report",
                arguments={"name": "../outside.txt"},
            )
        )

    report = root / "report.txt"
    original_open = os.open

    def swap_file_to_symlink_before_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        if path == "report.txt" and not report.is_symlink():
            report.unlink()
            report.symlink_to(outside)
        if dir_fd is None:
            return original_open(path, flags, mode)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", swap_file_to_symlink_before_open)

    with pytest.raises(ConnectorInvocationError, match="approved root"):
        asyncio.run(
            service.invoke(
                session=session,
                connector_id="reports",
                operation_name="get_report",
                arguments={"name": "report.txt"},
            )
        )
    assert outside.read_text(encoding="utf-8") == "outside"


def test_oauth_token_fetch_is_coalesced_for_concurrent_calls(tmp_path: Path) -> None:
    token_calls = 0

    def auth_handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_calls
        token_calls += 1
        assert request.url == httpx.URL("https://identity.example.test/oauth/token")
        return httpx.Response(200, json={"access_token": "oauth-token", "expires_in": 3600})

    def rest_handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer oauth-token"
        return httpx.Response(200, json={"ok": True})

    config = _config()
    config.auth_profiles["oauth"] = ConnectorAuthProfile.model_validate(
        {
            "type": "oauth2_client_credentials",
            "token_url": "https://identity.example.test/oauth/token",
            "client_id_env": "CLIENT_ID",
            "client_secret_env": "CLIENT_SECRET",
            "scopes": ["documents.read"],
        }
    )
    config.connectors["documents"].auth_profile = "oauth"
    service = _service(
        tmp_path,
        config,
        rest_transport=httpx.MockTransport(rest_handler),
        auth_transport=httpx.MockTransport(auth_handler),
        environ={"CLIENT_ID": "id", "CLIENT_SECRET": "secret"},
    )

    async def invoke_twice() -> None:
        await asyncio.gather(
            service.invoke(
                session=_session(),
                connector_id="documents",
                operation_name="get_document",
                arguments={"id": "one"},
            ),
            service.invoke(
                session=_session(),
                connector_id="documents",
                operation_name="get_document",
                arguments={"id": "two"},
            ),
        )

    asyncio.run(invoke_twice())
    assert token_calls == 1


def test_oauth_cache_refreshes_when_the_resolved_profile_changes(tmp_path: Path) -> None:
    auth_requests: list[httpx.Request] = []
    authorization_headers: list[str] = []

    def auth_handler(request: httpx.Request) -> httpx.Response:
        auth_requests.append(request)
        return httpx.Response(
            200,
            json={"access_token": f"oauth-token-{len(auth_requests)}", "expires_in": 3600},
        )

    def rest_handler(request: httpx.Request) -> httpx.Response:
        authorization_headers.append(request.headers["authorization"])
        return httpx.Response(200, json={"ok": True})

    config = _config()
    config.auth_profiles["oauth"] = ConnectorAuthProfile.model_validate(
        {
            "type": "oauth2_client_credentials",
            "token_url": "https://identity.example.test/oauth/token",
            "client_id_env": "CLIENT_ID",
            "client_secret_env": "CLIENT_SECRET",
            "scopes": ["documents.read"],
        }
    )
    config.connectors["documents"].auth_profile = "oauth"
    environment = {
        "CLIENT_ID": "old-client",
        "CLIENT_SECRET": "old-secret",
        "NEW_CLIENT_ID": "new-client",
        "NEW_CLIENT_SECRET": "new-secret",
    }
    service = _service(
        tmp_path,
        config,
        rest_transport=httpx.MockTransport(rest_handler),
        auth_transport=httpx.MockTransport(auth_handler),
        environ=environment,
    )

    asyncio.run(
        service.invoke(
            session=_session(),
            connector_id="documents",
            operation_name="get_document",
            arguments={"id": "one"},
        )
    )
    config.auth_profiles["oauth"] = ConnectorAuthProfile.model_validate(
        {
            "type": "oauth2_client_credentials",
            "token_url": "https://login.example.test/v2/token",
            "client_id_env": "NEW_CLIENT_ID",
            "client_secret_env": "NEW_CLIENT_SECRET",
            "scopes": ["documents.read", "documents.metadata"],
            "audience": "documents-api",
        }
    )
    asyncio.run(
        service.invoke(
            session=_session(),
            connector_id="documents",
            operation_name="get_document",
            arguments={"id": "two"},
        )
    )
    environment["NEW_CLIENT_ID"] = "rotated-client"
    asyncio.run(
        service.invoke(
            session=_session(),
            connector_id="documents",
            operation_name="get_document",
            arguments={"id": "three"},
        )
    )

    assert [request.url for request in auth_requests] == [
        httpx.URL("https://identity.example.test/oauth/token"),
        httpx.URL("https://login.example.test/v2/token"),
        httpx.URL("https://login.example.test/v2/token"),
    ]
    assert authorization_headers == [
        "Bearer oauth-token-1",
        "Bearer oauth-token-2",
        "Bearer oauth-token-3",
    ]


def test_oauth_cache_does_not_extend_short_provider_lifetimes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token_calls = 0
    clock = [1000.0]

    def auth_handler(_request: httpx.Request) -> httpx.Response:
        nonlocal token_calls
        token_calls += 1
        return httpx.Response(
            200,
            json={"access_token": f"short-token-{token_calls}", "expires_in": 10},
        )

    config = _config()
    config.auth_profiles["oauth"] = ConnectorAuthProfile.model_validate(
        {
            "type": "oauth2_client_credentials",
            "token_url": "https://identity.example.test/oauth/token",
            "client_id_env": "CLIENT_ID",
            "client_secret_env": "CLIENT_SECRET",
        }
    )
    config.connectors["documents"].auth_profile = "oauth"
    service = _service(
        tmp_path,
        config,
        rest_transport=httpx.MockTransport(lambda _request: httpx.Response(200, json={"ok": True})),
        auth_transport=httpx.MockTransport(auth_handler),
        environ={"CLIENT_ID": "id", "CLIENT_SECRET": "secret"},
    )
    monkeypatch.setattr("evander.connectors.service.monotonic", lambda: clock[0])

    asyncio.run(
        service.invoke(
            session=_session(),
            connector_id="documents",
            operation_name="get_document",
            arguments={"id": "one"},
        )
    )
    clock[0] += 1
    asyncio.run(
        service.invoke(
            session=_session(),
            connector_id="documents",
            operation_name="get_document",
            arguments={"id": "two"},
        )
    )
    clock[0] += 10
    asyncio.run(
        service.invoke(
            session=_session(),
            connector_id="documents",
            operation_name="get_document",
            arguments={"id": "three"},
        )
    )

    assert token_calls == 2


def test_oauth_write_401_evicts_token_without_automatic_retry(tmp_path: Path) -> None:
    token_calls = 0
    authorization_headers: list[str] = []

    def auth_handler(_request: httpx.Request) -> httpx.Response:
        nonlocal token_calls
        token_calls += 1
        return httpx.Response(
            200,
            json={"access_token": f"oauth-token-{token_calls}", "expires_in": 3600},
        )

    def rest_handler(request: httpx.Request) -> httpx.Response:
        authorization_headers.append(request.headers["authorization"])
        return httpx.Response(401, json={"message": "rejected"})

    config = _config(access="write", method="POST")
    config.auth_profiles["oauth"] = ConnectorAuthProfile.model_validate(
        {
            "type": "oauth2_client_credentials",
            "token_url": "https://identity.example.test/oauth/token",
            "client_id_env": "CLIENT_ID",
            "client_secret_env": "CLIENT_SECRET",
        }
    )
    config.connectors["documents"].auth_profile = "oauth"
    service = _service(
        tmp_path,
        config,
        rest_transport=httpx.MockTransport(rest_handler),
        auth_transport=httpx.MockTransport(auth_handler),
        environ={"CLIENT_ID": "id", "CLIENT_SECRET": "secret"},
    )

    async def invoke_twice_sequentially() -> None:
        for _attempt in range(2):
            with pytest.raises(ConnectorInvocationError, match="rejected authentication"):
                await service.invoke(
                    session=_session(),
                    connector_id="documents",
                    operation_name="get_document",
                    arguments={"id": "one"},
                )

    asyncio.run(invoke_twice_sequentially())

    assert token_calls == 2
    assert authorization_headers == ["Bearer oauth-token-1", "Bearer oauth-token-2"]


def test_gateway_registers_tools_and_operation_approval_precedes_network(tmp_path: Path) -> None:
    context = build_runtime(
        GatewayConfig(
            runtime_dir=tmp_path,
            audit_log_path=tmp_path / "audit.jsonl",
            session_store_path=tmp_path / "sessions.json",
            approval_store_path=tmp_path / "approvals.json",
            draft_store_path=tmp_path / "drafts.json",
            email_store_path=tmp_path / "emails.json",
            discord_command_store_path=tmp_path / "discord.json",
        )
    )
    config = _config(requires_approval=True, access="write", method="POST")
    context.provider_config.resolve_connectors_for_directory = lambda _path: config  # type: ignore[method-assign]
    context.session_repository.upsert_session(
        _session().model_copy(update={"directory_path": "root"})
    )
    server = create_server(context)
    tool_names = {tool.name for tool in server._tool_manager.list_tools()}

    assert {"list_connectors", "call_connector"}.issubset(tool_names)
    listed = asyncio.run(server.call_tool("list_connectors", {"session_id": "session-connectors"}))
    assert "get_document" in str(listed)

    with pytest.raises(ToolError, match="approval"):
        asyncio.run(
            server.call_tool(
                "call_connector",
                {
                    "session_id": "session-connectors",
                    "connector_id": "documents",
                    "operation": "get_document",
                    "arguments": {"id": "alpha"},
                },
            )
        )
    approvals = list(context.approvals.read_all().values())
    assert len(approvals) == 1
    assert approvals[0].status is ApprovalStatus.PENDING
    assert approvals[0].payload["arguments"]["connector_id"] == "documents"
    assert approvals[0].payload["arguments"]["operation"] == "get_document"


def test_required_connector_approval_is_exact_and_single_use_despite_loose_tool_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = build_runtime(
        GatewayConfig(
            runtime_dir=tmp_path,
            audit_log_path=tmp_path / "audit.jsonl",
            session_store_path=tmp_path / "sessions.json",
            approval_store_path=tmp_path / "approvals.json",
            draft_store_path=tmp_path / "drafts.json",
            email_store_path=tmp_path / "emails.json",
            discord_command_store_path=tmp_path / "discord.json",
        )
    )
    config = _config(requires_approval=True, access="write", method="POST")
    context.provider_config.resolve_connectors_for_directory = lambda _path: config  # type: ignore[method-assign]
    context.session_repository.upsert_session(
        _session().model_copy(
            update={
                "directory_path": "root",
                "approval_tool_settings": {
                    ToolName.CALL_CONNECTOR.value: ApprovalGrantSettings(
                        ttl_seconds=7200,
                        max_uses=None,
                        payload_mode=ApprovalPayloadMode.ANY,
                    )
                },
            }
        )
    )
    server = create_server(context)
    approved_arguments = {
        "session_id": "session-connectors",
        "connector_id": "documents",
        "operation": "get_document",
        "arguments": {"id": "alpha"},
    }

    with pytest.raises(ToolError, match="approval"):
        asyncio.run(server.call_tool("call_connector", approved_arguments))

    approval = next(iter(context.approvals.read_all().values()))
    assert approval.approval_settings is not None
    assert approval.approval_settings.ttl_seconds == 7200
    assert approval.approval_settings.max_uses == 1
    assert approval.approval_settings.payload_mode is ApprovalPayloadMode.EXACT
    approval.status = ApprovalStatus.APPROVED
    approval.approver = "operator"
    approval.decision_at = datetime.now(UTC)
    approval.decision_reason = "verified"
    context.approvals.put(approval.request_id, approval)

    with pytest.raises(ToolError, match="payload mismatch"):
        asyncio.run(
            server.call_tool(
                "call_connector",
                {
                    **approved_arguments,
                    "arguments": {"id": "beta"},
                    "approval_request_id": approval.request_id,
                },
            )
        )

    async def fake_invoke(self: ConnectorService, **_kwargs: object) -> ConnectorInvocationResult:
        return ConnectorInvocationResult(
            connector_id="documents",
            operation="get_document",
            result_mode="json",
            value={"ok": True},
        )

    monkeypatch.setattr(ConnectorService, "invoke", fake_invoke)
    asyncio.run(
        server.call_tool(
            "call_connector",
            {**approved_arguments, "approval_request_id": approval.request_id},
        )
    )
    with pytest.raises(ToolError):
        asyncio.run(
            server.call_tool(
                "call_connector",
                {**approved_arguments, "approval_request_id": approval.request_id},
            )
        )
    exhausted = context.approvals.get(approval.request_id)
    assert exhausted is not None
    assert exhausted.status is ApprovalStatus.EXHAUSTED


def test_admin_connector_api_is_revisioned_privileged_and_validates_before_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = build_admin_runtime(_gateway_config_path(tmp_path))
    configured = _config().model_dump(mode="json", exclude_none=True)
    write_result = service.operations_admin.write_directory_domain_config(
        "root", "connectors", configured
    )
    assert write_result.ok
    client = TestClient(create_app(service))

    response = client.get("/api/admin/connectors?directory_path=root")
    assert response.status_code == 200
    state = response.json()
    assert state["connectors"][0]["connector_id"] == "documents"
    assert state["connectors"][0]["auth_status"] == {
        "available": True,
        "type": "none",
    }

    locked = client.put(
        "/api/admin/connectors?directory_path=root",
        json={"expected_revision": state["revision"], "config": configured},
    )
    assert locked.status_code == 403
    assert client.post("/api/admin/privileged/unlock").status_code == 200

    invalid = client.put(
        "/api/admin/connectors?directory_path=root",
        json={
            "expected_revision": state["revision"],
            "config": {"connectors": {"documents": {"disabled": False}}},
        },
    )
    assert invalid.status_code == 400
    readback = client.get("/api/admin/connectors?directory_path=root").json()
    assert readback["revision"] == state["revision"]
    assert readback["connectors"][0]["display_name"] == "Documents"

    lock_held = False
    lock_entries = 0

    @contextmanager
    def tracked_mutation_lock():
        nonlocal lock_held, lock_entries
        assert lock_held is False
        lock_held = True
        lock_entries += 1
        try:
            yield
        finally:
            lock_held = False

    monkeypatch.setattr(
        service.operations_admin,
        "admin_config_mutation_lock",
        tracked_mutation_lock,
    )
    for method_name in (
        "read_directory_domain_config",
        "compute_directory_effective_config",
        "write_directory_domain_config",
    ):
        original = getattr(service.operations_admin, method_name)

        def guarded_call(*args: object, _original=original, **kwargs: object):
            assert lock_held is True
            return _original(*args, **kwargs)

        monkeypatch.setattr(service.operations_admin, method_name, guarded_call)

    saved = client.put(
        "/api/admin/connectors?directory_path=root",
        json={"expected_revision": state["revision"], "config": configured},
    )
    assert saved.status_code == 200
    assert lock_entries == 1
    assert lock_held is False


def test_admin_connector_api_authorizes_scoped_owner_at_requested_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EVANDER_ADMIN_WEB_AUTH_MODE", "local_password")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_USERNAME", "admin")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_PASSWORD", "admin-pass")
    monkeypatch.setenv("EVANDER_ADMIN_WEB_SESSION_SECRET", "test-secret")
    service = build_admin_runtime(_gateway_config_path(tmp_path))
    created = service.directory_admin.create_child(parent="root", name="ops")
    assert created.ok
    config = _config()
    connector = config.connectors["documents"]
    connector.health_operation = "get_document"
    connector.health_arguments = {"id": "health"}
    configured = config.model_dump(mode="json", exclude_none=True)
    assert service.operations_admin.write_directory_domain_config(
        "root", "connectors", configured
    ).ok

    app = create_app(service)
    store = app.state.admin_user_store
    store.upsert_user(login="ops-owner@example.com", role="regular", display_name="Ops Owner")
    store.remove_user_group_assignment(
        login="ops-owner@example.com",
        group_id="operator",
        directory_path="root",
    )
    store.assign_user_group(
        login="ops-owner@example.com",
        group_id=OWNER_GROUP_ID,
        directory_path="root/ops",
    )
    store.set_local_password(login="ops-owner@example.com", password="ops-pass")
    client = TestClient(app)
    assert (
        client.post(
            "/api/auth/login",
            json={"username": "ops-owner@example.com", "password": "ops-pass"},
        ).status_code
        == 200
    )
    assert client.post("/api/admin/privileged/unlock").status_code == 200

    state_response = client.get("/api/admin/connectors?directory_path=root/ops")
    assert state_response.status_code == 200
    assert client.get("/api/admin/connectors?directory_path=root").status_code == 403
    state = state_response.json()
    saved = client.put(
        "/api/admin/connectors?directory_path=root/ops",
        json={"expected_revision": state["revision"], "config": {}},
    )
    assert saved.status_code == 200

    async def fake_invoke(
        self: ConnectorService,
        **_kwargs: object,
    ) -> ConnectorInvocationResult:
        return ConnectorInvocationResult(
            connector_id="documents",
            operation="get_document",
            result_mode="json",
            value={"ok": True},
        )

    monkeypatch.setattr(ConnectorService, "invoke", fake_invoke)
    tested = client.post("/api/admin/connectors/documents/test?directory_path=root/ops")
    assert tested.status_code == 200
    assert tested.json()["status"] == "ok"


def test_tool_call_audit_surfaces_connector_identity() -> None:
    entry = tool_call_audit_entry_from_payload(
        {
            "event_type": "tool_call",
            "created_at": "2026-07-16T10:00:00+00:00",
            "session_id": "session-connectors",
            "tool_name": "call_connector",
            "connector_id": "documents",
            "connector_operation": "get_document",
            "args": {
                "connector_id": "documents",
                "operation": "get_document",
                "arguments": {"id": "alpha"},
            },
            "result": {"status": "success"},
            "decision": "allowed",
        }
    )

    assert entry is not None
    assert entry.connector_id == "documents"
    assert entry.connector_operation == "get_document"


def test_tool_call_audit_does_not_treat_non_connector_operation_as_connector_metadata() -> None:
    entry = tool_call_audit_entry_from_payload(
        {
            "event_type": "tool_call",
            "created_at": "2026-07-17T10:00:00+00:00",
            "session_id": "session-assistant",
            "tool_name": "assistant_run_safe_operation",
            "args": {"operation": "refresh_status"},
            "result": {"status": "success"},
            "decision": "allowed",
        }
    )

    assert entry is not None
    assert entry.connector_id is None
    assert entry.connector_operation is None


def test_tool_call_audit_accepts_operation_fallback_with_explicit_connector_id() -> None:
    entry = tool_call_audit_entry_from_payload(
        {
            "event_type": "tool_call",
            "created_at": "2026-07-17T10:00:00+00:00",
            "session_id": "session-addon",
            "tool_name": "addon_connector_proxy",
            "args": {"connector_id": "documents", "operation": "get_document"},
            "result": {"status": "success"},
            "decision": "allowed",
        }
    )

    assert entry is not None
    assert entry.connector_id == "documents"
    assert entry.connector_operation == "get_document"

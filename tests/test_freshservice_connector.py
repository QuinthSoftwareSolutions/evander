from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from evander.addons import LoadedAddonCatalog
from evander.communication_artifacts import CommunicationArtifactStore
from evander.connectors.drivers import ConnectorInvocationError
from evander.connectors.models import ConnectorDirectoryConfig
from evander.connectors.service import (
    ConnectorService,
    connector_registry_from_catalog,
    default_connector_registry,
)
from evander.models import SessionManifest, ToolName
from evander_freshservice_addon import register_addon
from evander_freshservice_addon.connector import FreshserviceConnectorDriver


def _operation(
    action: str,
    *,
    result_mode: str = "json",
    max_response_bytes: int | None = None,
) -> dict[str, object]:
    properties: dict[str, object]
    required: list[str]
    config: dict[str, object] = {"action": action}
    if action == "search_tickets_by_subject":
        properties = {
            "subject": {"type": "string", "minLength": 1, "maxLength": 500},
            "updated_since": {"type": "string", "format": "date-time"},
            "workspace_id": {"type": "integer", "minimum": 0},
            "page": {"type": "integer", "minimum": 1, "maximum": 10000},
            "per_page": {"type": "integer", "minimum": 1, "maximum": 100},
        }
        required = ["subject"]
    elif action == "list_ticket_conversations":
        properties = {
            "ticket_id": {"type": "integer", "minimum": 1},
            "page": {"type": "integer", "minimum": 1, "maximum": 10000},
            "per_page": {"type": "integer", "minimum": 1, "maximum": 100},
        }
        required = ["ticket_id"]
    elif action == "download_ticket_attachment":
        properties = {
            "ticket_id": {"type": "integer", "minimum": 1},
            "attachment_id": {"type": "integer", "minimum": 1},
        }
        required = ["ticket_id", "attachment_id"]
        config.update(
            {
                "allowed_extensions": [".csv", ".pdf", ".xlsx"],
                "allowed_mime_types": [
                    "application/pdf",
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    "text/csv",
                ],
                "conversation_page_limit": 5,
            }
        )
    else:
        properties = {"ticket_id": {"type": "integer", "minimum": 1}}
        required = ["ticket_id"]
    payload: dict[str, object] = {
        "description": f"Freshservice {action}",
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
        "access": "read",
        "requires_approval": False,
        "result_mode": result_mode,
        "timeout_seconds": 30,
        "config": config,
    }
    if max_response_bytes is not None:
        payload["max_response_bytes"] = max_response_bytes
    return payload


def _config(*, operations: dict[str, dict[str, object]]) -> ConnectorDirectoryConfig:
    return ConnectorDirectoryConfig.model_validate(
        {
            "auth_profiles": {
                "freshservice-readonly": {
                    "type": "basic_env",
                    "username_env": "FRESHSERVICE_API_KEY",
                    "password_env": "FRESHSERVICE_API_PASSWORD",
                }
            },
            "connectors": {
                "freshservice": {
                    "display_name": "Freshservice read-only",
                    "description": "Read approved Freshservice tickets and attachments.",
                    "driver": "freshservice",
                    "auth_profile": "freshservice-readonly",
                    "config": {
                        "base_url": "https://example.freshservice.com/api/v2",
                        "attachment_host_allowlist": ["attachments.example.test"],
                        "headers": {
                            "Accept-Encoding": "identity",
                            "X-Control-Plane-Context": "freshservice-only",
                        },
                    },
                    "operations": operations,
                }
            },
        }
    )


def _session(operations: list[str]) -> SessionManifest:
    return SessionManifest(
        session_id="freshservice-session",
        directory_path="root/techops",
        allowed_tools=[ToolName.LIST_CONNECTORS, ToolName.CALL_CONNECTOR],
        connector_grants={"freshservice": operations},
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )


def _service(
    tmp_path: Path,
    config: ConnectorDirectoryConfig,
    handler,
) -> ConnectorService:
    provider_config = SimpleNamespace(
        resolve_connectors_for_directory=lambda _directory_path: config
    )
    registry = default_connector_registry(rest_transport=httpx.MockTransport(handler))
    registry.register(
        "freshservice",
        FreshserviceConnectorDriver(transport=httpx.MockTransport(handler)),
    )
    return ConnectorService(
        runtime_dir=tmp_path,
        provider_config=provider_config,
        artifact_store_factory=lambda: CommunicationArtifactStore(tmp_path),
        registry=registry,
        environ={
            "FRESHSERVICE_API_KEY": "test-key",
            "FRESHSERVICE_API_PASSWORD": "X",
        },
    )


def test_freshservice_addon_registers_connector_driver() -> None:
    registration = register_addon()
    registry = connector_registry_from_catalog(
        LoadedAddonCatalog(
            registrations=(registration,),
            loaded_addons=(registration.addon_id,),
        )
    )

    assert registration.addon_id == "freshservice-addon"
    assert registry.driver_names == ("filesystem", "freshservice", "rest")


def test_freshservice_example_config_is_valid() -> None:
    project_dir = Path(__file__).resolve().parents[1]
    payload = json.loads(
        (project_dir / "config/connectors/freshservice-readonly.json.example").read_text(
            encoding="utf-8"
        )
    )
    config = ConnectorDirectoryConfig.model_validate(payload)

    driver = FreshserviceConnectorDriver()
    driver.validate_definition(config.connectors["freshservice"])
    assert set(config.connectors["freshservice"].operations) == {
        "download_ticket_attachment",
        "get_ticket",
        "list_ticket_conversations",
        "search_tickets_by_subject",
    }


def test_freshservice_search_scans_recent_tickets_and_returns_exact_subject_matches(
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "tickets": [
                    {"id": 10, "subject": "Daily plant report"},
                    {"id": 11, "subject": "daily plant report"},
                    {"id": 12, "subject": "Daily plant report - follow-up"},
                ]
            },
        )

    config = _config(
        operations={"search_tickets_by_subject": _operation("search_tickets_by_subject")}
    )
    service = _service(tmp_path, config, handler)

    result = asyncio.run(
        service.invoke(
            session=_session(["search_tickets_by_subject"]),
            connector_id="freshservice",
            operation_name="search_tickets_by_subject",
            arguments={
                "subject": "Daily plant report",
                "updated_since": "2026-09-13T00:00:00+02:00",
                "workspace_id": 0,
                "page": 2,
                "per_page": 50,
            },
        )
    )

    assert result.value == {
        "tickets": [
            {"id": 10, "subject": "Daily plant report"},
            {"id": 11, "subject": "daily plant report"},
        ],
        "match_count": 2,
        "scanned_count": 3,
        "page": 2,
        "per_page": 50,
        "has_more": False,
    }
    assert requests[0].url.params["updated_since"] == "2026-09-12T22:00:00Z"
    assert requests[0].url.params["workspace_id"] == "0"
    assert requests[0].url.params["order_type"] == "desc"
    assert requests[0].headers["Authorization"].startswith("Basic ")


def test_freshservice_download_resolves_conversation_attachment_and_strips_auth(
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/v2/tickets/42":
            return httpx.Response(200, json={"ticket": {"id": 42, "attachments": []}})
        if request.url.path == "/api/v2/tickets/42/conversations":
            return httpx.Response(
                200,
                json={
                    "conversations": [
                        {
                            "id": 8,
                            "attachments": [
                                {
                                    "id": 9,
                                    "name": "daily-report.csv",
                                    "content_type": "text/csv",
                                    "attachment_url": (
                                        "https://attachments.example.test/files/daily-report.csv"
                                    ),
                                }
                            ],
                        }
                    ]
                },
            )
        if request.url.host == "attachments.example.test":
            assert "Authorization" not in request.headers
            assert "X-Control-Plane-Context" not in request.headers
            return httpx.Response(
                200, content=b"plant,output\nA,42\n", headers={"content-type": "text/csv"}
            )
        raise AssertionError(f"unexpected request: {request.url}")

    config = _config(
        operations={
            "download_ticket_attachment": _operation(
                "download_ticket_attachment", result_mode="artifact", max_response_bytes=1024
            )
        }
    )
    service = _service(tmp_path, config, handler)

    result = asyncio.run(
        service.invoke(
            session=_session(["download_ticket_attachment"]),
            connector_id="freshservice",
            operation_name="download_ticket_attachment",
            arguments={"ticket_id": 42, "attachment_id": 9},
        )
    )

    assert result.result_mode == "artifact"
    assert result.artifact is not None
    assert result.artifact.filename == "daily-report.csv"
    staged = CommunicationArtifactStore(tmp_path).get(result.artifact.artifact_id)
    assert staged.blob_path.read_bytes() == b"plant,output\nA,42\n"
    assert requests[0].headers["Authorization"].startswith("Basic ")
    assert requests[0].headers["X-Control-Plane-Context"] == "freshservice-only"
    assert requests[1].headers["Authorization"].startswith("Basic ")


@pytest.mark.parametrize(
    ("attachment_url", "expect_auth"),
    [
        ("https://example.freshservice.com/files/daily-report.csv", True),
        ("https://example.freshservice.com./files/daily-report.csv", False),
    ],
)
def test_freshservice_download_scopes_auth_to_canonical_same_origin(
    tmp_path: Path,
    attachment_url: str,
    expect_auth: bool,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/v2/tickets/42":
            return httpx.Response(
                200,
                json={
                    "ticket": {
                        "attachments": [
                            {
                                "id": 9,
                                "name": "daily-report.csv",
                                "content_type": "text/csv",
                                "attachment_url": attachment_url,
                            }
                        ]
                    }
                },
            )
        assert request.url.path == "/files/daily-report.csv"
        assert ("Authorization" in request.headers) is expect_auth
        return httpx.Response(200, content=b"plant,output\nA,42\n")

    config = _config(
        operations={
            "download_ticket_attachment": _operation(
                "download_ticket_attachment", result_mode="artifact", max_response_bytes=1024
            )
        }
    )
    config.connectors["freshservice"].config["base_url"] = (
        "https://example.freshservice.com:443/api/v2"
    )
    service = _service(tmp_path, config, handler)

    result = asyncio.run(
        service.invoke(
            session=_session(["download_ticket_attachment"]),
            connector_id="freshservice",
            operation_name="download_ticket_attachment",
            arguments={"ticket_id": 42, "attachment_id": 9},
        )
    )

    assert result.artifact is not None
    assert result.artifact.mime_type == "text/csv"
    assert len(requests) == 2


@pytest.mark.parametrize(
    ("declared_mime", "response_headers"),
    [
        ("text/csv", {"content-type": "text/html"}),
        (None, {}),
    ],
)
def test_freshservice_download_rejects_missing_or_disallowed_response_content_type(
    tmp_path: Path,
    declared_mime: str | None,
    response_headers: dict[str, str],
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/tickets/42":
            return httpx.Response(
                200,
                json={
                    "ticket": {
                        "attachments": [
                            {
                                "id": 9,
                                "name": "daily-report.csv",
                                "content_type": declared_mime,
                                "attachment_url": (
                                    "https://attachments.example.test/files/daily-report.csv"
                                ),
                            }
                        ]
                    }
                },
            )
        return httpx.Response(
            200,
            content=b"<html>Authentication required</html>",
            headers=response_headers,
        )

    config = _config(
        operations={
            "download_ticket_attachment": _operation(
                "download_ticket_attachment", result_mode="artifact", max_response_bytes=1024
            )
        }
    )
    service = _service(tmp_path, config, handler)

    with pytest.raises(ConnectorInvocationError, match="response content type is not allowed"):
        asyncio.run(
            service.invoke(
                session=_session(["download_ticket_attachment"]),
                connector_id="freshservice",
                operation_name="download_ticket_attachment",
                arguments={"ticket_id": 42, "attachment_id": 9},
            )
        )


def test_freshservice_download_rejects_attachment_url_outside_allowlist(
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "ticket": {
                    "id": 42,
                    "attachments": [
                        {
                            "id": 9,
                            "name": "daily-report.csv",
                            "content_type": "text/csv",
                            "attachment_url": "https://169.254.169.254/latest/meta-data",
                        }
                    ],
                }
            },
        )

    config = _config(
        operations={
            "download_ticket_attachment": _operation(
                "download_ticket_attachment", result_mode="artifact"
            )
        }
    )
    service = _service(tmp_path, config, handler)

    with pytest.raises(ConnectorInvocationError, match="host allowlist"):
        asyncio.run(
            service.invoke(
                session=_session(["download_ticket_attachment"]),
                connector_id="freshservice",
                operation_name="download_ticket_attachment",
                arguments={"ticket_id": 42, "attachment_id": 9},
            )
        )

    assert len(requests) == 1


def test_freshservice_download_rejects_redirect_outside_allowlist(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/v2/tickets/42":
            return httpx.Response(
                200,
                json={
                    "ticket": {
                        "id": 42,
                        "attachments": [
                            {
                                "id": 9,
                                "name": "daily-report.csv",
                                "content_type": "text/csv",
                                "attachment_url": (
                                    "https://attachments.example.test/files/daily-report.csv"
                                ),
                            }
                        ],
                    }
                },
            )
        if request.url.host == "attachments.example.test":
            assert "Authorization" not in request.headers
            return httpx.Response(302, headers={"location": "https://unapproved.test/file.csv"})
        raise AssertionError(f"unexpected request: {request.url}")

    config = _config(
        operations={
            "download_ticket_attachment": _operation(
                "download_ticket_attachment", result_mode="artifact"
            )
        }
    )
    service = _service(tmp_path, config, handler)

    with pytest.raises(ConnectorInvocationError, match="host allowlist"):
        asyncio.run(
            service.invoke(
                session=_session(["download_ticket_attachment"]),
                connector_id="freshservice",
                operation_name="download_ticket_attachment",
                arguments={"ticket_id": 42, "attachment_id": 9},
            )
        )

    assert [request.url.host for request in requests] == [
        "example.freshservice.com",
        "attachments.example.test",
    ]


def test_freshservice_driver_rejects_unsafe_definition(tmp_path: Path) -> None:
    config = _config(
        operations={
            "download_ticket_attachment": _operation(
                "download_ticket_attachment", result_mode="artifact"
            )
        }
    )
    connector = config.connectors["freshservice"]
    connector.config["base_url"] = "http://localhost/api/v2"
    service = _service(tmp_path, config, lambda _request: httpx.Response(200, json={}))

    with pytest.raises(ValueError, match="freshservice.com/api/v2"):
        service.validate_config(config)


def test_freshservice_driver_rejects_unknown_operation_config(tmp_path: Path) -> None:
    operation = _operation("get_ticket")
    operation["config"] = {"action": "get_ticket", "path": "/unreviewed"}
    config = _config(operations={"get_ticket": operation})
    service = _service(tmp_path, config, lambda _request: httpx.Response(200, json={}))

    with pytest.raises(ValueError, match="unsupported config keys: path"):
        service.validate_config(config)

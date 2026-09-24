from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from starlette.testclient import TestClient

from evander.config import GatewayConfig
from evander.relays.http_target import create_app
from evander.models import SessionManifest
from evander.repositories.session_manifests import JsonSessionManifestRepository
from evander.session_auth import session_mcp_auth_metadata


def make_config(tmp_path: Path) -> GatewayConfig:
    return GatewayConfig(
        runtime_dir=tmp_path,
        audit_log_path=tmp_path / "audit.jsonl",
        session_store_path=tmp_path / "sessions.json",
        approval_store_path=tmp_path / "approvals.json",
        draft_store_path=tmp_path / "drafts.json",
        email_store_path=tmp_path / "emails.json",
        discord_command_store_path=tmp_path / "discord.json",
    )


def write_session(
    config: GatewayConfig, *, session_id: str, metadata: dict[str, object] | None = None
) -> None:
    store = JsonSessionManifestRepository(config.session_store_path)
    store.upsert_session(
        SessionManifest(
            session_id=session_id,
            allowed_tools=[],
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            metadata=metadata or {},
        ),
    )


def test_http_target_relay_proxies_to_selected_target_alias(tmp_path: Path) -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("authorization", "")
        captured["cookie"] = request.headers.get("cookie", "")
        captured["accept-encoding"] = request.headers.get("accept-encoding", "")
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"ok": True})

    config = make_config(tmp_path)
    write_session(
        config,
        session_id="session-1",
        metadata={
            "http_targets": {
                "targets": {
                    "demo-a": {"base_url": "https://demo-a.example"},
                    "demo-b": {"base_url": "https://demo-b.example/root"},
                }
            }
        },
    )
    transport = httpx.MockTransport(handler)
    client = TestClient(create_app(config, transport=transport))

    response = client.post(
        "/session/session-1/target/demo-b/api/work-orders",
        params={"status": "open"},
        headers={"authorization": "Bearer guest-token", "cookie": "session=guest-cookie"},
        json={"hello": "world"},
    )

    assert response.status_code == 200
    assert captured["authorization"] == "Bearer guest-token"
    assert captured["cookie"] == "session=guest-cookie"
    assert captured["accept-encoding"] == "identity"
    assert captured["url"] == "https://demo-b.example/root/api/work-orders?status=open"


def test_http_target_relay_rejects_encoded_dot_segment_escape_from_base_path(
    tmp_path: Path,
) -> None:
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json={"ok": True})

    config = make_config(tmp_path)
    write_session(
        config,
        session_id="session-1",
        metadata={
            "http_targets": {
                "targets": {
                    "demo-b": {"base_url": "https://demo-b.example/root"},
                }
            }
        },
    )
    client = TestClient(create_app(config, transport=httpx.MockTransport(handler)))

    response = client.get("/session/session-1/target/demo-b/%2e%2e/admin")

    assert response.status_code == 400
    assert "dot segments" in response.text
    assert called is False


def test_http_target_relay_allows_safe_encoded_path_under_base_path(
    tmp_path: Path,
) -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"ok": True})

    config = make_config(tmp_path)
    write_session(
        config,
        session_id="session-1",
        metadata={
            "http_targets": {
                "targets": {
                    "demo-b": {"base_url": "https://demo-b.example/root"},
                }
            }
        },
    )
    client = TestClient(create_app(config, transport=httpx.MockTransport(handler)))

    response = client.get(
        "/session/session-1/target/demo-b/api/%7Ehealth",
        params={"status": "open"},
    )

    assert response.status_code == 200
    assert captured["url"] == "https://demo-b.example/root/api/~health?status=open"


def test_http_target_relay_rejects_unknown_target_alias(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    write_session(
        config,
        session_id="session-1",
        metadata={
            "http_targets": {
                "targets": {
                    "demo-a": {"base_url": "https://demo-a.example"},
                }
            }
        },
    )
    client = TestClient(create_app(config))

    response = client.get("/session/session-1/target/missing/api/work-orders")

    assert response.status_code == 404
    assert response.json() == {"error": "session session-1 unknown HTTP target alias: missing"}


def test_http_target_relay_requires_mcp_session_token_for_tokenized_session(
    tmp_path: Path,
) -> None:
    calls: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(
            {
                "url": str(request.url),
                "mcp_token_header": request.headers.get("x-evander-mcp-session-token", ""),
                "referer": request.headers.get("referer", ""),
            }
        )
        return httpx.Response(200, json={"ok": True})

    config = make_config(tmp_path)
    write_session(
        config,
        session_id="session-1",
        metadata={
            "mcp_session_auth": session_mcp_auth_metadata("correct-token"),
            "http_targets": {
                "targets": {
                    "demo-a": {"base_url": "https://demo-a.example"},
                }
            },
        },
    )
    client = TestClient(create_app(config, transport=httpx.MockTransport(handler)))

    denied_missing = client.get("/session/session-1/target/demo-a/api/work-orders")
    denied_wrong = client.get(
        "/session/session-1/target/demo-a/api/work-orders",
        headers={"x-evander-mcp-session-token": "wrong-token"},
    )
    allowed = client.get(
        "/session/session-1/target/demo-a/api/work-orders"
        "?mcp_session_token=correct-token&status=open"
    )
    allowed_path_token = client.get(
        "/auth/correct-token/session/session-1/target/demo-a/api/work-orders?status=closed",
        headers={
            "referer": (
                "http://relay.local/auth/correct-token/session/session-1/target/demo-a/login"
            )
        },
    )

    assert denied_missing.status_code == 401
    assert denied_missing.json() == {"error": "MCP session token is required for this session"}
    assert denied_wrong.status_code == 401
    assert denied_wrong.json() == {"error": "MCP session token is required for this session"}
    assert allowed.status_code == 200
    assert allowed_path_token.status_code == 200
    assert calls == [
        {
            "url": "https://demo-a.example/api/work-orders?status=open",
            "mcp_token_header": "",
            "referer": "",
        },
        {
            "url": "https://demo-a.example/api/work-orders?status=closed",
            "mcp_token_header": "",
            "referer": "",
        },
    ]


def test_http_target_relay_keeps_referer_for_untokenized_requests(
    tmp_path: Path,
) -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["referer"] = request.headers.get("referer", "")
        return httpx.Response(200, json={"ok": True})

    config = make_config(tmp_path)
    write_session(
        config,
        session_id="session-1",
        metadata={
            "http_targets": {
                "targets": {
                    "demo-a": {"base_url": "https://demo-a.example"},
                }
            }
        },
    )
    client = TestClient(create_app(config, transport=httpx.MockTransport(handler)))

    response = client.get(
        "/session/session-1/target/demo-a/api/work-orders",
        headers={"referer": "https://app.example/orders"},
    )

    assert response.status_code == 200
    assert captured["referer"] == "https://app.example/orders"


def test_http_target_relay_health_reports_auth_state(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    client = TestClient(create_app(config))

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "relay_mode": "session_scoped_http_targets",
        "auth_mode": "transparent_guest_managed",
        "forwards_authorization": True,
    }


def test_http_target_relay_rejects_cross_origin_redirects(tmp_path: Path) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302, headers={"location": "https://elsewhere.example/api/work-orders"}
        )

    config = make_config(tmp_path)
    write_session(
        config,
        session_id="session-1",
        metadata={
            "http_targets": {
                "targets": {
                    "demo-a": {"base_url": "https://demo-a.example"},
                }
            }
        },
    )
    client = TestClient(create_app(config, transport=httpx.MockTransport(handler)))

    response = client.get("/session/session-1/target/demo-a/api/work-orders")

    assert response.status_code == 502
    assert response.json() == {
        "error": "upstream redirect target is outside the allowlisted target origin"
    }


@pytest.mark.parametrize(
    ("target", "base_url", "location", "rewritten_location"),
    [
        pytest.param(
            "demo-a",
            "https://demo-a.example",
            "https://demo-a.example/login?next=%2Forders#start",
            "/session/session-1/target/demo-a/login?next=%2Forders#start",
            id="absolute",
        ),
        pytest.param(
            "demo-a",
            "https://demo-a.example",
            "/login?next=%2Forders",
            "/session/session-1/target/demo-a/login?next=%2Forders",
            id="root-relative",
        ),
        pytest.param(
            "demo-b",
            "https://demo-b.example/root",
            "https://demo-b.example/root/login?next=%2Forders",
            "/session/session-1/target/demo-b/login?next=%2Forders",
            id="absolute-base-path",
        ),
    ],
)
def test_http_target_relay_rewrites_allowlisted_redirects(
    tmp_path: Path, target: str, base_url: str, location: str, rewritten_location: str
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": location})

    config = make_config(tmp_path)
    write_session(
        config,
        session_id="session-1",
        metadata={"http_targets": {"targets": {target: {"base_url": base_url}}}},
    )
    client = TestClient(create_app(config, transport=httpx.MockTransport(handler)))

    response = client.get(
        f"/session/session-1/target/{target}/api/work-orders", follow_redirects=False
    )

    assert response.status_code == 302
    assert response.headers["location"] == rewritten_location


def test_http_target_relay_preserves_path_token_on_same_origin_redirect(
    tmp_path: Path,
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302,
            headers={"location": "https://demo-a.example/login?next=%2Forders#start"},
        )

    config = make_config(tmp_path)
    write_session(
        config,
        session_id="session-1",
        metadata={
            "mcp_session_auth": session_mcp_auth_metadata("correct-token"),
            "http_targets": {
                "targets": {
                    "demo-a": {"base_url": "https://demo-a.example"},
                }
            },
        },
    )
    client = TestClient(create_app(config, transport=httpx.MockTransport(handler)))

    response = client.get(
        "/auth/correct-token/session/session-1/target/demo-a/api/work-orders",
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert (
        response.headers["location"]
        == "/auth/correct-token/session/session-1/target/demo-a/login?next=%2Forders#start"
    )


def test_http_target_relay_accepts_target_alias_root_path(tmp_path: Path) -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200, text="ok")

    config = make_config(tmp_path)
    write_session(
        config,
        session_id="session-1",
        metadata={
            "http_targets": {
                "targets": {
                    "demo-b": {"base_url": "https://demo-b.example/root"},
                }
            }
        },
    )
    client = TestClient(create_app(config, transport=httpx.MockTransport(handler)))

    response = client.get("/session/session-1/target/demo-b")

    assert response.status_code == 200
    assert captured["url"] == "https://demo-b.example/root"


def test_http_target_relay_rejects_root_relative_redirects_outside_target_base_path(
    tmp_path: Path,
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "/login"})

    config = make_config(tmp_path)
    write_session(
        config,
        session_id="session-1",
        metadata={
            "http_targets": {
                "targets": {
                    "demo-b": {"base_url": "https://demo-b.example/root"},
                }
            }
        },
    )
    client = TestClient(create_app(config, transport=httpx.MockTransport(handler)))

    response = client.get("/session/session-1/target/demo-b/api/work-orders")

    assert response.status_code == 502
    assert response.json() == {
        "error": "upstream redirect target is outside the allowlisted target origin"
    }

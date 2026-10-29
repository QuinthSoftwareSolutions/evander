from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier, Lock, Thread
from urllib.parse import parse_qs

import httpx
import pytest

from evander.messaging.teams_auth import (
    AuthorizationPendingError,
    ConsentDeniedError,
    DeviceCodeExpiredError,
    DeviceCodeLoginSession,
    InvalidClientConfigurationError,
    JsonTeamsCredentialStore,
    MicrosoftDeviceCodeAuthConfig,
    RefreshTokenRevokedError,
    SlowDownError,
    TeamsAuthCredentials,
    TeamsDeviceCodeTokenProvider,
)


class Clock:
    def __init__(self, now: datetime) -> None:
        self._now = now
        self.sleep_calls: list[float] = []

    def now(self) -> datetime:
        return self._now

    def sleep(self, seconds: float) -> None:
        self.sleep_calls.append(seconds)
        self._now += timedelta(seconds=seconds)


def make_config() -> MicrosoftDeviceCodeAuthConfig:
    return MicrosoftDeviceCodeAuthConfig(
        client_id="client-123",
        tenant_id="tenant-abc",
        scopes=["offline_access", "https://graph.microsoft.com/ChannelMessage.Send"],
        authority_base_url="https://login.microsoftonline.com",
        timeout_seconds=5.0,
        expiry_skew_seconds=300,
    )


def make_client_factory(transport: httpx.BaseTransport):
    real_client = httpx.Client
    return lambda timeout: real_client(timeout=timeout, transport=transport)


def make_provider(
    tmp_path: Path,
    *,
    transport: httpx.BaseTransport,
    clock: Clock | None = None,
) -> tuple[TeamsDeviceCodeTokenProvider, JsonTeamsCredentialStore]:
    resolved_clock = clock or Clock(datetime(2026, 4, 9, 12, 0, tzinfo=UTC))
    store = JsonTeamsCredentialStore(tmp_path / "teams-auth.json")
    provider = TeamsDeviceCodeTokenProvider(
        make_config(),
        store,
        now_provider=resolved_clock.now,
        sleep=resolved_clock.sleep,
        client_factory=make_client_factory(transport),
    )
    return provider, store


def test_begin_device_code_login_returns_prompt(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "device_code": "device-code-1",
                "user_code": "ABCD-EFGH",
                "verification_uri": "https://microsoft.com/devicelogin",
                "verification_uri_complete": "https://microsoft.com/devicelogin?code=ABCD-EFGH",
                "message": "Sign in with the provided code.",
                "expires_in": 900,
                "interval": 7,
            },
            request=request,
        )

    provider, _ = make_provider(tmp_path, transport=httpx.MockTransport(handler))

    session = provider.begin_device_code_login()

    assert session.device_code == "device-code-1"
    assert session.user_code == "ABCD-EFGH"
    assert session.verification_uri_complete == "https://microsoft.com/devicelogin?code=ABCD-EFGH"
    assert session.interval_seconds == 7
    assert session.expires_at == datetime(2026, 4, 9, 12, 15, tzinfo=UTC)
    assert requests[0].url.path.endswith("/tenant-abc/oauth2/v2.0/devicecode")
    form = parse_qs(requests[0].content.decode("utf-8"))
    assert form == {
        "client_id": ["client-123"],
        "scope": ["offline_access https://graph.microsoft.com/ChannelMessage.Send openid profile"],
    }


@pytest.mark.parametrize(
    ("error_code", "exc_type"),
    [
        ("authorization_pending", AuthorizationPendingError),
        ("slow_down", SlowDownError),
        ("authorization_declined", ConsentDeniedError),
        ("expired_token", DeviceCodeExpiredError),
    ],
)
def test_poll_device_code_login_once_maps_device_code_errors(
    tmp_path: Path,
    error_code: str,
    exc_type: type[Exception],
) -> None:
    session = DeviceCodeLoginSession(
        client_id="client-123",
        tenant_id="tenant-abc",
        scopes=["offline_access"],
        device_code="device-code-1",
        user_code="ABCD-EFGH",
        verification_uri="https://microsoft.com/devicelogin",
        message="Sign in with the code.",
        interval_seconds=5,
        created_at=datetime(2026, 4, 9, 12, 0, tzinfo=UTC),
        expires_at=datetime(2026, 4, 9, 12, 15, tzinfo=UTC),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"error": error_code, "error_description": f"{error_code} description"},
            request=request,
        )

    provider, _ = make_provider(tmp_path, transport=httpx.MockTransport(handler))

    with pytest.raises(exc_type, match=error_code):
        provider.poll_device_code_login_once(session)


def test_complete_device_code_login_retries_pending_and_slow_down_then_persists_credentials(
    tmp_path: Path,
) -> None:
    token_calls = 0
    clock = Clock(datetime(2026, 4, 9, 12, 0, tzinfo=UTC))

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_calls
        token_calls += 1
        if token_calls == 1:
            return httpx.Response(
                400,
                json={
                    "error": "authorization_pending",
                    "error_description": "authorization_pending",
                },
                request=request,
            )
        if token_calls == 2:
            return httpx.Response(
                400,
                json={"error": "slow_down", "error_description": "slow_down"},
                request=request,
            )
        return httpx.Response(
            200,
            json={
                "access_token": "access-token-2",
                "refresh_token": "refresh-token-2",
                "token_type": "Bearer",
                "scope": "offline_access https://graph.microsoft.com/ChannelMessage.Send",
                "expires_in": 3600,
            },
            request=request,
        )

    provider, store = make_provider(tmp_path, transport=httpx.MockTransport(handler), clock=clock)
    session = DeviceCodeLoginSession(
        client_id="client-123",
        tenant_id="tenant-abc",
        scopes=["offline_access"],
        device_code="device-code-1",
        user_code="ABCD-EFGH",
        verification_uri="https://microsoft.com/devicelogin",
        message="Sign in with the code.",
        interval_seconds=5,
        created_at=clock.now(),
        expires_at=clock.now() + timedelta(minutes=15),
    )

    credentials = provider.complete_device_code_login(session)

    assert credentials.access_token == "access-token-2"
    assert credentials.refresh_token == "refresh-token-2"
    assert clock.sleep_calls == [5.0, 10.0]
    assert token_calls == 3
    assert store.load() is not None
    assert store.load().access_token == "access-token-2"


def test_begin_device_code_login_maps_invalid_client_configuration(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"error": "invalid_client", "error_description": "client secret or id is invalid"},
            request=request,
        )

    provider, _ = make_provider(tmp_path, transport=httpx.MockTransport(handler))

    with pytest.raises(InvalidClientConfigurationError, match="client secret or id is invalid"):
        provider.begin_device_code_login()


def test_poll_device_code_login_once_explains_confidential_client_misconfiguration(
    tmp_path: Path,
) -> None:
    session = DeviceCodeLoginSession(
        client_id="client-123",
        tenant_id="tenant-abc",
        scopes=["offline_access"],
        device_code="device-code-1",
        user_code="ABCD-EFGH",
        verification_uri="https://microsoft.com/devicelogin",
        message="Sign in with the code.",
        interval_seconds=5,
        created_at=datetime(2026, 4, 9, 12, 0, tzinfo=UTC),
        expires_at=datetime(2026, 4, 9, 12, 15, tzinfo=UTC),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "error": "invalid_request",
                "error_description": (
                    "AADSTS7000218: The request body must contain the following parameter: "
                    "'client_assertion' or 'client_secret'."
                ),
            },
            request=request,
        )

    provider, _ = make_provider(tmp_path, transport=httpx.MockTransport(handler))

    with pytest.raises(InvalidClientConfigurationError, match="not enabled as a public client"):
        provider.poll_device_code_login_once(session)


def test_get_bearer_token_refreshes_expiring_credentials_and_reuses_prior_refresh_token(
    tmp_path: Path,
) -> None:
    clock = Clock(datetime(2026, 4, 9, 12, 0, tzinfo=UTC))
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "access_token": "access-token-refreshed",
                "token_type": "Bearer",
                "scope": "offline_access https://graph.microsoft.com/ChannelMessage.Send",
                "expires_in": 7200,
            },
            request=request,
        )

    provider, store = make_provider(tmp_path, transport=httpx.MockTransport(handler), clock=clock)
    store.save(
        TeamsAuthCredentials(
            client_id="client-123",
            tenant_id="tenant-abc",
            access_token="access-token-old",
            refresh_token="refresh-token-old",
            token_type="Bearer",
            scopes=["offline_access", "https://graph.microsoft.com/ChannelMessage.Send"],
            issued_at=clock.now() - timedelta(hours=1),
            expires_at=clock.now() + timedelta(seconds=120),
        )
    )

    token = provider.get_bearer_token()
    refreshed = store.load()

    assert token == "access-token-refreshed"
    assert refreshed is not None
    assert refreshed.refresh_token == "refresh-token-old"
    assert refreshed.access_token == "access-token-refreshed"
    form = parse_qs(requests[0].content.decode("utf-8"))
    assert form["grant_type"] == ["refresh_token"]
    assert form["refresh_token"] == ["refresh-token-old"]
    assert form["scope"] == ["offline_access https://graph.microsoft.com/ChannelMessage.Send"]
    assert form["client_info"] == ["1"]


def test_refresh_preserves_credential_scopes_when_config_defaults_expand(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "access_token": "access-token-refreshed",
                "token_type": "Bearer",
                "expires_in": 7200,
            },
            request=request,
        )

    provider, store = make_provider(tmp_path, transport=httpx.MockTransport(handler))
    provider.config = provider.config.model_copy(
        update={
            "scopes": [
                "offline_access",
                "https://graph.microsoft.com/User.Read",
                "https://graph.microsoft.com/ChannelMessage.Send",
            ]
        }
    )
    granted_scopes = [
        "offline_access",
        "https://graph.microsoft.com/ChannelMessage.Send",
    ]
    store.save(
        TeamsAuthCredentials(
            client_id="client-123",
            tenant_id="tenant-abc",
            access_token="access-token-old",
            refresh_token="refresh-token-old",
            scopes=granted_scopes,
            expires_at=datetime(2026, 4, 9, 13, 0, tzinfo=UTC),
        )
    )

    refreshed = provider.refresh_access_token()

    form = parse_qs(requests[0].content.decode("utf-8"))
    assert form["scope"] == [" ".join(granted_scopes)]
    assert "User.Read" not in form["scope"][0]
    assert refreshed.scopes == granted_scopes


def test_refresh_omits_scope_when_legacy_credential_did_not_persist_grants(
    tmp_path: Path,
) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "access_token": "access-token-refreshed",
                "token_type": "Bearer",
                "expires_in": 7200,
            },
            request=request,
        )

    provider, store = make_provider(tmp_path, transport=httpx.MockTransport(handler))
    store.save(
        TeamsAuthCredentials(
            client_id="client-123",
            tenant_id="organizations",
            access_token="access-token-old",
            refresh_token="refresh-token-old",
            scopes=[],
            expires_at=datetime(2026, 4, 9, 13, 0, tzinfo=UTC),
        )
    )

    refreshed = provider.refresh_access_token()

    form = parse_qs(requests[0].content.decode("utf-8"))
    assert "scope" not in form
    assert refreshed.scopes == []


def test_device_login_persists_concrete_tenant_from_identity_metadata(tmp_path: Path) -> None:
    tenant_id = "11111111-2222-3333-4444-555555555555"
    claims = base64.urlsafe_b64encode(json.dumps({"tid": tenant_id}).encode()).decode().rstrip("=")

    def handler(request: httpx.Request) -> httpx.Response:
        form = parse_qs(request.content.decode("utf-8"))
        assert form["client_info"] == ["1"]
        return httpx.Response(
            200,
            json={
                "access_token": "access-token",
                "refresh_token": "refresh-token",
                "id_token": f"header.{claims}.signature",
                "scope": "offline_access https://graph.microsoft.com/ChannelMessage.Send",
                "expires_in": 3600,
            },
            request=request,
        )

    provider, store = make_provider(tmp_path, transport=httpx.MockTransport(handler))
    session = DeviceCodeLoginSession(
        client_id="client-123",
        tenant_id="organizations",
        scopes=["offline_access"],
        device_code="device-code-1",
        user_code="ABCD-EFGH",
        verification_uri="https://microsoft.com/devicelogin",
        message="Sign in with the code.",
        expires_at=datetime(2026, 4, 9, 12, 15, tzinfo=UTC),
    )

    credentials = provider.poll_device_code_login_once(session)

    assert credentials.tenant_id == tenant_id
    assert provider.get_tenant_id() == tenant_id
    assert store.load() is not None and store.load().tenant_id == tenant_id


def test_get_bearer_token_serializes_concurrent_refreshes(tmp_path: Path) -> None:
    clock = Clock(datetime(2026, 4, 9, 12, 0, tzinfo=UTC))
    requests: list[httpx.Request] = []
    request_lock = Lock()

    def handler(request: httpx.Request) -> httpx.Response:
        with request_lock:
            requests.append(request)
        return httpx.Response(
            200,
            json={
                "access_token": "access-token-refreshed",
                "token_type": "Bearer",
                "scope": "offline_access https://graph.microsoft.com/ChannelMessage.Send",
                "expires_in": 7200,
            },
            request=request,
        )

    provider, store = make_provider(tmp_path, transport=httpx.MockTransport(handler), clock=clock)
    store.save(
        TeamsAuthCredentials(
            client_id="client-123",
            tenant_id="tenant-abc",
            access_token="access-token-old",
            refresh_token="refresh-token-old",
            token_type="Bearer",
            scopes=["offline_access", "https://graph.microsoft.com/ChannelMessage.Send"],
            issued_at=clock.now() - timedelta(hours=1),
            expires_at=clock.now() + timedelta(seconds=120),
        )
    )
    start = Barrier(3)
    tokens: list[str] = []
    token_lock = Lock()

    def get_token() -> None:
        start.wait(timeout=5)
        token = provider.get_bearer_token()
        with token_lock:
            tokens.append(token)

    threads = [Thread(target=get_token) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert all(not thread.is_alive() for thread in threads)
    assert tokens == ["access-token-refreshed"] * 3
    assert len(requests) == 1


def test_refresh_access_token_clears_store_when_refresh_token_is_revoked(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"error": "invalid_grant", "error_description": "refresh token has been revoked"},
            request=request,
        )

    provider, store = make_provider(tmp_path, transport=httpx.MockTransport(handler))
    store.save(
        TeamsAuthCredentials(
            client_id="client-123",
            tenant_id="tenant-abc",
            access_token="access-token-old",
            refresh_token="refresh-token-old",
            token_type="Bearer",
            scopes=["offline_access"],
            issued_at=datetime(2026, 4, 9, 10, 0, tzinfo=UTC),
            expires_at=datetime(2026, 4, 9, 10, 30, tzinfo=UTC),
        )
    )

    with pytest.raises(RefreshTokenRevokedError, match="refresh token has been revoked"):
        provider.refresh_access_token()

    assert store.load() is None

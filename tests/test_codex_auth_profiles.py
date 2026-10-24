from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from evander.codex_auth import CodexRateLimitResetRequest, CodexSubscriptionDeferredError
from evander.worker.codex_auth_broker import CodexAuthBrokerError, CodexExternalAuthTokens
from evander.worker.codex_auth_profiles import CodexAuthBrokerRegistry, CodexAuthProfileInUseError


class FakeProfileBroker:
    def __init__(self, *, auth_home: Path, **_kwargs: Any) -> None:
        self.auth_home = auth_home
        self.status_payload: dict[str, Any] = {
            "available": True,
            "signed_in": False,
            "account_id": None,
            "rate_limits": None,
        }
        self.tokens = CodexExternalAuthTokens(
            access_token="access-secret",
            account_id="account-1",
            plan_type="plus",
        )
        self.closed = False
        self.logged_out = False
        self.barrier: threading.Barrier | None = None
        self.status_error: str | None = None
        self.usage_requests: list[bool] = []
        self.token_requests = 0
        self.reset_requests: list[tuple[CodexRateLimitResetRequest, str]] = []

    def status(self, *, include_usage: bool = True) -> dict[str, Any]:
        self.usage_requests.append(include_usage)
        if self.status_error is not None:
            raise CodexAuthBrokerError(self.status_error)
        if self.barrier is not None:
            self.barrier.wait(timeout=2)
        return dict(self.status_payload)

    def external_tokens(self, **_kwargs: Any) -> CodexExternalAuthTokens:
        self.token_requests += 1
        return self.tokens

    def start_device_login(self) -> dict[str, Any]:
        return {"login_id": "login-1", "status": "pending"}

    def device_login_status(self, login_id: str) -> dict[str, Any]:
        return {"login_id": login_id, "status": "completed"}

    def cancel_device_login(self, login_id: str) -> dict[str, Any]:
        return {"login_id": login_id, "status": "cancelled"}

    def logout(self) -> dict[str, Any]:
        self.logged_out = True
        self.status_payload.update(signed_in=False, account_id=None)
        return self.status()

    def consume_reset_credit(self, request, *, expected_account_id):
        self.reset_requests.append((request, expected_account_id))
        self.status_payload["rate_limits"] = {
            "default": {"primary": {"used_percent": 0}},
            "reset_credits": {"available_count": 1},
        }
        return {"outcome": "reset"}

    def close(self) -> None:
        self.closed = True


def _registry(tmp_path: Path) -> tuple[CodexAuthBrokerRegistry, list[FakeProfileBroker]]:
    brokers: list[FakeProfileBroker] = []

    def factory(**kwargs: Any) -> FakeProfileBroker:
        broker = FakeProfileBroker(**kwargs)
        brokers.append(broker)
        return broker

    return (
        CodexAuthBrokerRegistry(worker_root=tmp_path / "worker", broker_factory=factory),
        brokers,
    )


def test_profile_registry_preserves_legacy_default_home_and_never_stores_tokens(
    tmp_path: Path,
) -> None:
    registry, brokers = _registry(tmp_path)
    try:
        registry.create_profile({"profile_id": "default", "display_name": "Legacy account"})
        created = registry.create_profile(
            {"profile_id": "example-plus", "display_name": "Example Plus"}
        )

        assert created["profile_id"] == "example-plus"
        assert registry.auth_home("default") == tmp_path / "worker" / "codex-auth"
        assert registry.auth_home("example-plus") == (
            tmp_path / "worker" / "codex-auth" / "profiles" / "example-plus"
        )
        assert registry.broker("default").auth_home == registry.auth_home("default")
        assert registry.broker("example-plus").auth_home == registry.auth_home("example-plus")
        assert len(brokers) == 2

        registry_payload = json.loads(
            (tmp_path / "worker" / "codex-auth" / "profiles.json").read_text()
        )
        serialized = json.dumps(registry_payload).casefold()
        assert "access_token" not in serialized
        assert "refresh_token" not in serialized
        assert "access-secret" not in serialized
        assert set(registry_payload) == {"default", "example-plus"}
    finally:
        registry.close()


def test_fresh_worker_does_not_create_an_empty_default_profile(tmp_path: Path) -> None:
    for _ in range(2):
        registry, _brokers = _registry(tmp_path)
        try:
            assert registry.list_profiles() == {"profiles": []}
        finally:
            registry.close()


def test_legacy_login_is_migrated_and_can_be_deleted_without_removing_named_accounts(
    tmp_path: Path,
) -> None:
    home = tmp_path / "worker" / "codex-auth"
    home.mkdir(parents=True)
    (home / "auth.json").write_text('{"tokens": {"account_id": "legacy-account"}}')
    registry, _ = _registry(tmp_path)
    try:
        assert registry.profiles.require("default").display_name == "Default"
        registry.create_profile({"profile_id": "keep", "display_name": "Keep"})
        named_home = registry.auth_home("keep")
        named_home.mkdir(parents=True)
        (named_home / "auth.json").write_text("named credentials")
        registry.delete_profile("default", has_active_jobs=lambda: False)
        assert not (home / "auth.json").exists()
        assert (named_home / "auth.json").read_text() == "named credentials"
        assert [r.profile_id for r in registry.profiles.list()] == ["keep"]
    finally:
        registry.close()
    reopened, _ = _registry(tmp_path)
    try:
        assert [r.profile_id for r in reopened.profiles.list()] == ["keep"]
    finally:
        reopened.close()


def test_profile_deletion_closes_broker_removes_credentials_and_survives_restart(tmp_path: Path) -> None:
    registry, _ = _registry(tmp_path)
    registry.create_profile({"profile_id": "remove", "display_name": "Remove"})
    broker = registry.broker("remove")
    broker.auth_home.mkdir(parents=True, exist_ok=True)
    (broker.auth_home / "auth.json").write_text("saved credentials")
    try:
        assert registry.delete_profile("remove", has_active_jobs=lambda: False) == {
            "profile_id": "remove", "deleted": True,
        }
        assert broker.closed
        assert not broker.auth_home.exists()
        assert registry.list_profiles() == {"profiles": []}
        assert registry.delete_profile("remove", has_active_jobs=lambda: False)["deleted"]
    finally:
        registry.close()
    reopened, _ = _registry(tmp_path)
    try:
        assert reopened.list_profiles() == {"profiles": []}
    finally:
        reopened.close()


def test_deletion_rejects_active_and_admitting_jobs_without_blocking_other_profiles(tmp_path: Path) -> None:
    registry, _ = _registry(tmp_path)
    try:
        for profile in ["busy", "idle"]:
            registry.create_profile({"profile_id": profile, "display_name": profile})
        with pytest.raises(CodexAuthProfileInUseError):
            registry.delete_profile("busy", has_active_jobs=lambda: True)
        with registry.use_profile("busy"):
            with pytest.raises(CodexAuthProfileInUseError):
                registry.delete_profile("busy", has_active_jobs=lambda: False)
            assert registry.delete_profile("idle", has_active_jobs=lambda: False)["deleted"]
        assert registry.delete_profile("busy", has_active_jobs=lambda: False)["deleted"]
    finally:
        registry.close()


def test_failed_credential_cleanup_retains_profile_for_retry(tmp_path: Path, monkeypatch) -> None:
    registry, _ = _registry(tmp_path)
    try:
        registry.create_profile({"profile_id": "retry", "display_name": "Retry"})
        home = registry.auth_home("retry")
        home.mkdir(parents=True)
        with monkeypatch.context() as patcher:
            def fail_cleanup(_path):
                raise OSError("filesystem unavailable")
            patcher.setattr("evander.worker.codex_auth_profiles.shutil.rmtree", fail_cleanup)
            with pytest.raises(OSError, match="filesystem unavailable"):
                registry.delete_profile("retry", has_active_jobs=lambda: False)
        assert registry.profiles.require("retry").profile_id == "retry"
        assert registry.delete_profile("retry", has_active_jobs=lambda: False)["deleted"]
    finally:
        registry.close()


@pytest.mark.parametrize(
    "profile_id",
    ["Uppercase", "contains space", "-leading", "trailing-", "a" * 65],
)
def test_profile_ids_are_immutable_validated_slugs(tmp_path: Path, profile_id: str) -> None:
    registry, _ = _registry(tmp_path)
    try:
        with pytest.raises(ValueError, match="lowercase slug"):
            registry.create_profile({"profile_id": profile_id, "display_name": "Invalid"})
    finally:
        registry.close()


def test_account_identity_is_pinned_and_mismatched_login_is_logged_out(tmp_path: Path) -> None:
    registry, _ = _registry(tmp_path)
    try:
        registry.create_profile({"profile_id": "team-a", "display_name": "Team A"})
        broker = registry.broker("team-a")
        broker.status_payload.update(
            available=True,
            signed_in=True,
            account_id="account-a",
            rate_limits=None,
        )

        assert registry.profile_status("team-a")["pinned_account_id"] == "account-a"

        broker.status_payload["account_id"] = "account-b"
        mismatch = registry.profile_status("team-a")
        assert mismatch["available"] is False
        assert mismatch["signed_in"] is False
        assert mismatch["pinned_account_id"] == "account-a"
        assert "different OpenAI account" in mismatch["error"]
        assert broker.logged_out is True
    finally:
        registry.close()


def test_refresh_tokens_rejects_and_logs_out_a_different_pinned_account(
    tmp_path: Path,
) -> None:
    registry, _ = _registry(tmp_path)
    try:
        registry.create_profile({"profile_id": "team-a", "display_name": "Team A"})
        broker = registry.broker("team-a")
        broker.status_payload.update(
            available=True,
            signed_in=True,
            account_id="account-a",
            rate_limits={
                "default": {
                    "primary": {"used_percent": 100},
                    "credits": {"has_credits": True, "balance": "100"},
                }
            },
        )
        broker.tokens = CodexExternalAuthTokens("access-a", "account-a", "plus")
        assert registry.preflight("team-a").account_id == "account-a"
        assert broker.usage_requests[-1] is False

        broker.tokens = CodexExternalAuthTokens("access-b", "account-b", "business")
        with pytest.raises(CodexSubscriptionDeferredError) as mismatch:
            registry.refresh_tokens(
                "team-a",
                previous_access_token="access-a",
                previous_account_id="account-a",
            )

        assert mismatch.value.deferral.profile_id == "team-a"
        assert mismatch.value.deferral.reason == "unavailable"
        assert broker.logged_out is True
        assert registry.profiles.require("team-a").pinned_account_id == "account-a"
        assert broker.reset_requests == []
    finally:
        registry.close()


def test_preflight_returns_structured_disabled_signed_out_and_rate_limit_deferrals(
    tmp_path: Path,
) -> None:
    registry, _ = _registry(tmp_path)
    reset_at = datetime.now(UTC) + timedelta(minutes=7)
    try:
        registry.create_profile(
            {"profile_id": "capacity", "display_name": "Capacity", "enabled": False}
        )

        with pytest.raises(CodexSubscriptionDeferredError) as disabled:
            registry.preflight("capacity")
        assert disabled.value.deferral.reason == "disabled"

        registry.patch_profile("capacity", {"enabled": True})
        broker = registry.broker("capacity")
        with pytest.raises(CodexSubscriptionDeferredError) as signed_out:
            registry.preflight("capacity")
        assert signed_out.value.deferral.reason == "signed_out"

        broker.status_payload.update(
            available=True,
            signed_in=True,
            account_id="account-1",
            rate_limits={
                "default": {
                    "primary": {
                        "used_percent": 100,
                        "resets_at": reset_at.isoformat(),
                    }
                },
                "by_limit_id": {},
            },
        )
        with pytest.raises(CodexSubscriptionDeferredError) as exhausted:
            registry.preflight("capacity")
        assert exhausted.value.deferral.reason == "rate_limited"
        assert exhausted.value.deferral.retry_at == reset_at

        broker.status_payload["rate_limits"]["default"]["primary"]["used_percent"] = 25
        assert registry.preflight("capacity").account_id == "account-1"
    finally:
        registry.close()


@pytest.mark.parametrize("bucket_key", ["default", "codex"])
@pytest.mark.parametrize(
    ("credits", "admitted"),
    [
        (None, False),
        ({}, False),
        ({"has_credits": False, "unlimited": False, "balance": "100"}, False),
        ({"has_credits": "true", "balance": "100"}, False),
        ({"has_credits": True, "balance": "0"}, False),
        ({"has_credits": True, "balance": "-1"}, False),
        ({"has_credits": True, "balance": "NaN"}, False),
        ({"has_credits": True, "balance": "Infinity"}, False),
        ({"has_credits": True, "balance": "unavailable"}, False),
        ({"has_credits": True, "balance": 100}, False),
        ({"has_credits": True, "unlimited": False, "balance": "0.000001"}, True),
        ({"has_credits": True, "unlimited": False, "balance": None}, True),
        ({"has_credits": False, "unlimited": True, "balance": None}, True),
    ],
)
def test_preflight_uses_existing_purchased_capacity_without_resetting_or_switching_profiles(
    tmp_path: Path, bucket_key: str, credits: object, admitted: bool
) -> None:
    registry, _ = _registry(tmp_path)
    reset_at = datetime.now(UTC) + timedelta(days=6)
    try:
        registry.create_profile({"profile_id": "paid", "display_name": "Paid"})
        broker = registry.broker("paid")
        bucket = {
            "primary": {"used_percent": 100, "resets_at": reset_at.isoformat()},
            "secondary": {"used_percent": 100, "resets_at": reset_at.isoformat()},
            "credits": credits,
        }
        broker.status_payload.update(
            available=True,
            signed_in=True,
            account_id="account-1",
            rate_limits={
                "default": bucket if bucket_key == "default" else None,
                "by_limit_id": {"codex": bucket} if bucket_key == "codex" else {},
            },
        )
        if admitted:
            assert registry.preflight("paid").account_id == "account-1"
            assert registry.profiles.require("paid").pinned_account_id == "account-1"
        else:
            with pytest.raises(CodexSubscriptionDeferredError) as exhausted:
                registry.preflight("paid")
            assert exhausted.value.deferral.reason == "rate_limited"
            assert exhausted.value.deferral.profile_id == "paid"
            assert exhausted.value.deferral.retry_at == reset_at
        assert broker.token_requests == int(admitted)
        assert broker.usage_requests == [False]
        assert broker.reset_requests == []
        assert [record.profile_id for record in registry.profiles.list()] == ["paid"]
    finally:
        registry.close()


@pytest.mark.parametrize(
    "block",
    [
        {"rate_limit_reached_type": "rate_limit_reached"},
        {"rate_limit_reached_type": "workspace_owner_credits_depleted"},
        {"rate_limit_reached_type": "workspace_member_usage_limit_reached"},
        {"rate_limit_reached_type": "unknown_future_block"},
        {"spend_control_reached": True},
    ],
)
def test_purchased_capacity_does_not_override_provider_blocks(tmp_path: Path, block: dict) -> None:
    registry, _ = _registry(tmp_path)
    try:
        registry.create_profile({"profile_id": "paid", "display_name": "Paid"})
        broker = registry.broker("paid")
        broker.status_payload.update(
            available=True,
            signed_in=True,
            account_id="account-1",
            rate_limits={"default": {**block, "credits": {"unlimited": True}}},
        )
        with pytest.raises(CodexSubscriptionDeferredError) as blocked:
            registry.preflight("paid")
        assert blocked.value.deferral.reason == "rate_limited"
        assert broker.token_requests == 0
        assert broker.reset_requests == []
    finally:
        registry.close()


def test_preflight_defers_when_purchased_balance_is_exhausted(tmp_path: Path) -> None:
    registry, _ = _registry(tmp_path)
    try:
        registry.create_profile({"profile_id": "paid", "display_name": "Paid"})
        broker = registry.broker("paid")
        credits = {"has_credits": True, "balance": "1"}
        broker.status_payload.update(
            available=True, signed_in=True, account_id="account-1",
            rate_limits={"default": {"primary": {"used_percent": 100}, "credits": credits}},
        )
        assert registry.preflight("paid").account_id == "account-1"
        credits.update(has_credits=False, balance="0")
        with pytest.raises(CodexSubscriptionDeferredError) as exhausted:
            registry.preflight("paid")
        assert exhausted.value.deferral.reason == "rate_limited"
        assert exhausted.value.deferral.profile_id == "paid"
        assert broker.token_requests == 1
        assert broker.reset_requests == []
    finally:
        registry.close()


def test_purchased_capacity_cannot_cover_another_rate_limit_bucket(tmp_path: Path) -> None:
    registry, _ = _registry(tmp_path)
    reset_at = datetime.now(UTC) + timedelta(hours=1)
    try:
        registry.create_profile({"profile_id": "paid", "display_name": "Paid"})
        broker = registry.broker("paid")
        broker.status_payload.update(
            available=True,
            signed_in=True,
            account_id="account-1",
            rate_limits={
                "default": {"primary": {"used_percent": 100}, "credits": {"unlimited": True}},
                "by_limit_id": {
                    "other": {"primary": {"used_percent": 100, "resets_at": reset_at.isoformat()}}
                },
            },
        )
        with pytest.raises(CodexSubscriptionDeferredError) as exhausted:
            registry.preflight("paid")
        assert exhausted.value.deferral.retry_at == reset_at
        assert broker.token_requests == 0
        assert broker.reset_requests == []
    finally:
        registry.close()


def test_preflight_uses_reset_from_the_exhausted_rate_limit_window(tmp_path: Path) -> None:
    registry, _ = _registry(tmp_path)
    primary_reset = datetime.now(UTC) + timedelta(minutes=2)
    secondary_reset = datetime.now(UTC) + timedelta(minutes=30)
    try:
        registry.create_profile({"profile_id": "capacity", "display_name": "Capacity"})
        broker = registry.broker("capacity")
        broker.status_payload.update(
            available=True,
            signed_in=True,
            account_id="account-1",
            rate_limits={
                "default": {
                    "rate_limit_reached_type": "rate_limit_reached",
                    "primary": {
                        "used_percent": 25,
                        "resets_at": primary_reset.isoformat(),
                    },
                    "secondary": {
                        "used_percent": 100,
                        "resets_at": secondary_reset.isoformat(),
                    },
                },
                "by_limit_id": {},
            },
        )

        with pytest.raises(CodexSubscriptionDeferredError) as exhausted:
            registry.preflight("capacity")

        assert exhausted.value.deferral.reason == "rate_limited"
        assert exhausted.value.deferral.retry_at == secondary_reset
    finally:
        registry.close()


def test_preflight_uses_latest_reset_when_multiple_windows_are_exhausted(
    tmp_path: Path,
) -> None:
    registry, _ = _registry(tmp_path)
    primary_reset = datetime.now(UTC) + timedelta(minutes=10)
    secondary_reset = datetime.now(UTC) + timedelta(minutes=30)
    try:
        registry.create_profile({"profile_id": "capacity", "display_name": "Capacity"})
        broker = registry.broker("capacity")
        broker.status_payload.update(
            available=True,
            signed_in=True,
            account_id="account-1",
            rate_limits={
                "default": {
                    "rate_limit_reached_type": "rate_limit_reached",
                    "primary": {
                        "used_percent": 100,
                        "resets_at": primary_reset.isoformat(),
                    },
                    "secondary": {
                        "used_percent": 100,
                        "resets_at": secondary_reset.isoformat(),
                    },
                },
                "by_limit_id": {},
            },
        )

        with pytest.raises(CodexSubscriptionDeferredError) as exhausted:
            registry.preflight("capacity")

        assert exhausted.value.deferral.retry_at == secondary_reset
    finally:
        registry.close()


def test_preflight_returns_structured_unavailable_deferral_for_missing_profile(
    tmp_path: Path,
) -> None:
    registry, _ = _registry(tmp_path)
    try:
        with pytest.raises(CodexSubscriptionDeferredError) as unavailable:
            registry.preflight("missing-profile")

        assert unavailable.value.deferral.profile_id == "missing-profile"
        assert unavailable.value.deferral.reason == "unavailable"
        assert unavailable.value.deferral.retry_at is None
    finally:
        registry.close()


def test_preflight_returns_structured_unavailable_deferral_when_status_fails(
    tmp_path: Path,
) -> None:
    registry, _ = _registry(tmp_path)
    try:
        registry.create_profile({"profile_id": "team-a", "display_name": "Team A"})
        broker = registry.broker("team-a")
        broker.status_error = "app server unavailable"

        with pytest.raises(CodexSubscriptionDeferredError) as unavailable:
            registry.preflight("team-a")

        assert unavailable.value.deferral.profile_id == "team-a"
        assert unavailable.value.deferral.reason == "unavailable"
        assert str(unavailable.value) == "app server unavailable"
    finally:
        registry.close()


def test_profile_enabled_state_requires_a_boolean(tmp_path: Path) -> None:
    registry, _ = _registry(tmp_path)
    try:
        with pytest.raises(ValueError, match="enabled must be a boolean"):
            registry.create_profile(
                {"profile_id": "team-a", "display_name": "Team A", "enabled": "false"}
            )
        registry.create_profile({"profile_id": "team-a", "display_name": "Team A"})
        with pytest.raises(ValueError, match="enabled must be a boolean"):
            registry.patch_profile("team-a", {"enabled": 0})
        with pytest.raises(ValueError, match="display_name is required"):
            registry.patch_profile("team-a", {"display_name": None})
        with pytest.raises(ValueError, match="Unsupported profile fields: access_token"):
            registry.create_profile(
                {
                    "profile_id": "unsafe",
                    "display_name": "Unsafe",
                    "access_token": "must-not-be-accepted",
                }
            )
    finally:
        registry.close()


def test_profile_brokers_can_report_status_concurrently(tmp_path: Path) -> None:
    registry, _ = _registry(tmp_path)
    try:
        for profile_id in ("first", "second"):
            registry.create_profile({"profile_id": profile_id, "display_name": profile_id.title()})
        barrier = threading.Barrier(2)
        registry.broker("first").barrier = barrier
        registry.broker("second").barrier = barrier
        results: list[str] = []

        threads = [
            threading.Thread(
                target=lambda item=profile_id: (
                    registry.profile_status(item),
                    results.append(item),
                )
            )
            for profile_id in ("first", "second")
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=3)

        assert all(not thread.is_alive() for thread in threads)
        assert sorted(results) == ["first", "second"]
    finally:
        registry.close()


def test_reset_redemption_accepts_exhausted_capacity_and_refreshes_only_selected_profile(
    tmp_path: Path,
) -> None:
    registry, _ = _registry(tmp_path)
    try:
        for profile_id in ("team-a", "team-b"):
            registry.create_profile({"profile_id": profile_id, "display_name": profile_id})
            registry.broker(profile_id).status_payload.update(
                signed_in=True,
                account_id=f"account-{profile_id}",
                rate_limits={"default": {"primary": {"used_percent": 100}}},
            )
        request = CodexRateLimitResetRequest(idempotency_key="attempt")
        result = registry.consume_reset_credit("team-a", request)

        assert result["outcome"] == "reset"
        assert result["profile"]["profile_id"] == "team-a"
        assert result["profile"]["pinned_account_id"] == "account-team-a"
        assert result["profile"]["rate_limits"]["default"]["primary"]["used_percent"] == 0
        assert result["profile"]["rate_limits"]["reset_credits"]["available_count"] == 1
        assert registry.broker("team-a").reset_requests == [(request, "account-team-a")]
        assert registry.broker("team-b").reset_requests == []
        assert registry.broker("team-a").usage_requests == [False, True]
    finally:
        registry.close()


@pytest.mark.parametrize("state", [
    "disabled", "signed_out", "unavailable", "wrong_account", "wrong_expected_account",
])
def test_reset_redemption_rejects_unusable_or_mismatched_profiles(
    tmp_path: Path, state: str
) -> None:
    registry, _ = _registry(tmp_path)
    try:
        registry.create_profile({"profile_id": "team-a", "display_name": "Team A"})
        broker = registry.broker("team-a")
        broker.status_payload.update(signed_in=True, account_id="account-a")
        registry.profile_status("team-a")
        if state == "disabled":
            registry.profiles.patch("team-a", enabled=False)
        elif state == "signed_out":
            broker.status_payload["signed_in"] = False
        elif state == "unavailable":
            broker.status_payload["available"] = False
        elif state == "wrong_account":
            broker.status_payload["account_id"] = "account-b"
        with pytest.raises((CodexAuthBrokerError, CodexSubscriptionDeferredError)):
            registry.consume_reset_credit(
                "team-a", CodexRateLimitResetRequest(
                    idempotency_key="attempt",
                    expected_account_id="account-b" if state == "wrong_expected_account" else "account-a",
                )
            )
        assert broker.reset_requests == []
        if state == "wrong_account":
            assert broker.logged_out is True
    finally:
        registry.close()

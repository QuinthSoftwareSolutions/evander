from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from evander.config import GatewayConfig
from evander.effects.providers.base import EffectRuntimeContext
from evander.effects.providers.session_renew_if_expired import (
    SessionRenewIfExpiredEffectProvider,
    parse_session_extend_by,
    parse_session_renew_if_expires_within,
)
from evander.events.provider_models import SessionRenewIfExpiredEffectDefinition
from evander.models import SessionManifest, ToolName
from evander.repositories.session_manifests import JsonSessionManifestRepository


class _ProviderConfigResolver:
    def __init__(self, config: GatewayConfig) -> None:
        self.config = config

    def resolve_for_directory(self, directory_path: str | None) -> GatewayConfig:
        del directory_path
        return self.config


def _build_provider(
    tmp_path: Path,
    effect: SessionRenewIfExpiredEffectDefinition,
) -> tuple[SessionRenewIfExpiredEffectProvider, JsonSessionManifestRepository]:
    config = GatewayConfig(runtime_dir=tmp_path)
    repository = JsonSessionManifestRepository(config.session_store_path)
    admin_service = SimpleNamespace(
        runtime=SimpleNamespace(
            session_repository=repository,
            provider_config=_ProviderConfigResolver(config),
        )
    )
    provider = SessionRenewIfExpiredEffectProvider()
    provider.initialize(
        effect_definition=effect,
        runtime_context=EffectRuntimeContext(
            resolved_config=config,
            admin_service=admin_service,
            env={},
        ),
    )
    return provider, repository


def _manifest(
    *,
    session_id: str = "session-1",
    vm_id: str = "vm-1",
    expires_at: datetime,
) -> SessionManifest:
    return SessionManifest(
        session_id=session_id,
        vm_id=vm_id,
        allowed_tools=[ToolName.GET_AGENT_STATUS],
        expires_at=expires_at,
    )


def test_session_renew_if_expired_extends_expired_session(tmp_path: Path) -> None:
    provider, repository = _build_provider(
        tmp_path,
        SessionRenewIfExpiredEffectDefinition(
            id="renew-expired",
            display_name="Renew expired",
            type="effect:session:renew-if-expired",
            session_id="session-1",
            extend_by="6h",
        ),
    )
    repository.upsert_session(_manifest(expires_at=datetime.now(UTC) - timedelta(minutes=5)))
    before = datetime.now(UTC)

    output = provider.execute()

    after = datetime.now(UTC)
    renewed = repository.get_session("session-1")
    assert renewed is not None
    assert output["success"] is True
    assert output["extended"] is True
    assert output["renewed"] is True
    assert output["was_expired"] is True
    assert output["renewal_reason"] == "expired"
    assert output["status"] == "extended"
    assert output["extend_by"] == 21600
    assert output["renew_if_expires_within"] is None
    assert before + timedelta(hours=6) <= renewed.expires_at <= after + timedelta(hours=6)
    assert output["expires_at"] == renewed.expires_at.isoformat()


def test_session_renew_if_expired_leaves_active_session_unchanged(tmp_path: Path) -> None:
    provider, repository = _build_provider(
        tmp_path,
        SessionRenewIfExpiredEffectDefinition(
            id="renew-expired",
            display_name="Renew expired",
            type="effect:session:renew-if-expired",
            session_id="session-1",
            extend_by="6h",
        ),
    )
    active_expires_at = datetime.now(UTC) + timedelta(hours=2)
    repository.upsert_session(_manifest(expires_at=active_expires_at))

    output = provider.execute()

    unchanged = repository.get_session("session-1")
    assert unchanged is not None
    assert unchanged.expires_at == active_expires_at
    assert output["success"] is True
    assert output["extended"] is False
    assert output["renewed"] is False
    assert output["was_expired"] is False
    assert output["renewal_reason"] == "outside_window"
    assert output["status"] == "active"
    assert output["renew_if_expires_within"] is None
    assert output["expires_at"] == active_expires_at.isoformat()


def test_session_renew_if_expired_can_resolve_vm_name(tmp_path: Path) -> None:
    provider, repository = _build_provider(
        tmp_path,
        SessionRenewIfExpiredEffectDefinition(
            id="renew-expired",
            display_name="Renew expired",
            type="effect:session:renew-if-expired",
            vm_name="vm-1",
            extend_by="PT30M",
        ),
    )
    repository.upsert_session(_manifest(expires_at=datetime.now(UTC) - timedelta(minutes=5)))

    output = provider.execute()

    assert output["extended"] is True
    assert output["session_id"] == "session-1"
    assert output["vm_name"] == "vm-1"
    assert output["extend_by"] == 1800


def test_session_renew_if_expired_renews_active_session_within_window(
    tmp_path: Path,
) -> None:
    provider, repository = _build_provider(
        tmp_path,
        SessionRenewIfExpiredEffectDefinition(
            id="renew-expired",
            display_name="Renew expired",
            type="effect:session:renew-if-expired",
            session_id="session-1",
            extend_by="6h",
            renew_if_expires_within="30m",
        ),
    )
    active_expires_at = datetime.now(UTC) + timedelta(minutes=10)
    repository.upsert_session(_manifest(expires_at=active_expires_at))
    before = datetime.now(UTC)

    output = provider.execute()

    after = datetime.now(UTC)
    renewed = repository.get_session("session-1")
    assert renewed is not None
    assert output["success"] is True
    assert output["extended"] is True
    assert output["renewed"] is True
    assert output["was_expired"] is False
    assert output["renewal_reason"] == "within_window"
    assert output["status"] == "extended"
    assert output["renew_if_expires_within"] == 1800
    assert before + timedelta(hours=6) <= renewed.expires_at <= after + timedelta(hours=6)
    assert output["expires_at"] == renewed.expires_at.isoformat()


def test_session_renew_if_expired_leaves_active_session_outside_window(
    tmp_path: Path,
) -> None:
    provider, repository = _build_provider(
        tmp_path,
        SessionRenewIfExpiredEffectDefinition(
            id="renew-expired",
            display_name="Renew expired",
            type="effect:session:renew-if-expired",
            session_id="session-1",
            extend_by="6h",
            renew_if_expires_within="30m",
        ),
    )
    active_expires_at = datetime.now(UTC) + timedelta(hours=2)
    repository.upsert_session(_manifest(expires_at=active_expires_at))

    output = provider.execute()

    unchanged = repository.get_session("session-1")
    assert unchanged is not None
    assert unchanged.expires_at == active_expires_at
    assert output["success"] is True
    assert output["extended"] is False
    assert output["renewed"] is False
    assert output["was_expired"] is False
    assert output["renewal_reason"] == "outside_window"
    assert output["status"] == "active"
    assert output["renew_if_expires_within"] == 1800
    assert output["expires_at"] == active_expires_at.isoformat()


def test_session_renew_if_expired_rejects_unknown_target(tmp_path: Path) -> None:
    provider, _repository = _build_provider(
        tmp_path,
        SessionRenewIfExpiredEffectDefinition(
            id="renew-expired",
            display_name="Renew expired",
            type="effect:session:renew-if-expired",
            session_id="missing-session",
        ),
    )

    with pytest.raises(ValueError, match="Unknown session target"):
        provider.execute()


def test_session_renew_if_expired_rejects_ambiguous_runtime_selector(tmp_path: Path) -> None:
    provider, _repository = _build_provider(
        tmp_path,
        SessionRenewIfExpiredEffectDefinition(
            id="renew-expired",
            display_name="Renew expired",
            type="effect:session:renew-if-expired",
            session_id="session-1",
            extend_by="6h",
        ),
    )

    with pytest.raises(
        ValueError,
        match="requires exactly one of target, session_id, or vm_name",
    ):
        provider.execute(resolved_config={"vm_name": "vm-1"})


def test_parse_session_extend_by_rejects_invalid_duration() -> None:
    with pytest.raises(ValueError, match="duration string"):
        parse_session_extend_by("soon")


@pytest.mark.parametrize("value", ["soon", "0m", "PT0S"])
def test_parse_session_renew_if_expires_within_rejects_invalid_duration(value: str) -> None:
    with pytest.raises(ValueError, match="renew_if_expires_within"):
        parse_session_renew_if_expires_within(value)

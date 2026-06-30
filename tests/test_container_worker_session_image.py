from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from evander.config import GatewayConfig
from evander.effects.providers.agent_bootstrap_frontend import (
    AgentBootstrapFrontendEffectProvider,
)
from evander.effects.providers.agent_clone_repo import AgentCloneRepoEffectProvider
from evander.effects.providers.agent_exec import AgentExecEffectProvider
from evander.effects.providers.base import EffectRuntimeContext
from evander.events.provider_models import (
    AgentBootstrapFrontendEffectDefinition,
    AgentCloneRepoEffectDefinition,
    AgentExecEffectDefinition,
)
from evander.execution import ExecutionStatusKind
from evander.models import SessionManifest, ToolName
from evander.repositories.session_manifests import JsonSessionManifestRepository


class _ContainerDispatchBackendStub:
    def __init__(self) -> None:
        self.prepared_specs: list[object] = []
        self.started_specs: list[object] = []
        self.prepared_sessions: list[dict[str, object]] = []
        self.clone_repo_requests: list[dict[str, object]] = []
        self.written_files: list[dict[str, object]] = []
        self.destroyed: list[str] = []
        self.status = SimpleNamespace(
            status=ExecutionStatusKind.STOPPED,
            message=None,
            metadata={},
        )

    def prepare(self, spec):
        self.prepared_specs.append(spec)
        return spec

    def start(self, spec):
        self.started_specs.append(spec)
        return SimpleNamespace(execution_id=spec.target_name)

    def inspect(self, execution_id: str):
        del execution_id
        return self.status

    def destroy(self, execution_id: str):
        self.destroyed.append(execution_id)
        return self.status

    def get_log_snapshot(self, execution_id: str, *, lines: int = 50, mode: str | None = None):
        del execution_id, lines, mode
        return ""

    def prepare_session(self, **kwargs):
        self.prepared_sessions.append(dict(kwargs))
        return SimpleNamespace(session_id=kwargs["session_id"], metadata=kwargs.get("metadata", {}))

    def clone_repo(self, **kwargs):
        self.clone_repo_requests.append(dict(kwargs))
        return SimpleNamespace(
            cloned=True,
            target_dir="/home/agent/repos/repo",
            guest_repo_name=kwargs.get("repo_name") or "repo",
            remote_url=kwargs.get("repo_source") or "",
        )

    def write_files(self, **kwargs):
        self.written_files.append(dict(kwargs))
        return SimpleNamespace()


def _context(
    tmp_path: Path,
    session_repository: JsonSessionManifestRepository,
    *,
    default_image: str = "runner:latest",
) -> EffectRuntimeContext:
    config = GatewayConfig(
        runtime_dir=tmp_path,
        session_store_path=tmp_path / "sessions.json",
        execution_backend="container_worker_vm",
        container_worker_base_url="http://worker.test",
        container_worker_default_image=default_image,
    )
    return EffectRuntimeContext(
        provider_config=config.model_dump(mode="json"),
        admin_service=SimpleNamespace(
            runtime=SimpleNamespace(session_repository=session_repository)
        ),
        metadata={"directory_path": "root/team-a"},
    )


def _session(
    session_repository: JsonSessionManifestRepository,
    *,
    session_id: str,
    metadata: dict[str, object] | None = None,
) -> SessionManifest:
    session = SessionManifest(
        session_id=session_id,
        vm_provider="container_worker_vm",
        allowed_tools=[ToolName.GET_AGENT_STATUS],
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        directory_path="root/team-a",
        metadata=metadata or {},
    )
    session_repository.upsert_session(session)
    return session


def test_agent_exec_uses_session_container_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_repository = JsonSessionManifestRepository(tmp_path / "sessions.json")
    _session(
        session_repository,
        session_id="session-1",
        metadata={"container_worker": {"image": "runner:exec-image"}},
    )
    backend = _ContainerDispatchBackendStub()
    monkeypatch.setattr(
        "evander.effects.providers.agent_exec.container_backend",
        lambda context, config: backend,
    )
    provider = AgentExecEffectProvider()
    provider.initialize(
        AgentExecEffectDefinition(
            id="effect-1",
            display_name="Exec",
            type="effect:agent:exec",
            session_id="session-1",
            command="echo hello",
        ),
        _context(tmp_path, session_repository),
    )

    result = provider.execute()

    assert result["success"] is True
    assert backend.started_specs[0].image == "runner:exec-image"


def test_agent_clone_repo_uses_session_container_image(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_repository = JsonSessionManifestRepository(tmp_path / "sessions.json")
    _session(
        session_repository,
        session_id="session-1",
        metadata={"container_worker": {"image": "runner:clone-image"}},
    )
    backend = _ContainerDispatchBackendStub()
    monkeypatch.setattr(
        "evander.effects.providers.agent_clone_repo.container_backend",
        lambda context, config: backend,
    )
    monkeypatch.setattr(
        "evander.effects.providers.agent_clone_repo._container_git_mirror_resolver",
        lambda context, directory_path: SimpleNamespace(
            resolve=lambda **kwargs: SimpleNamespace(
                worker_repo_source="ssh://evander-git-host/agents/repo.git",
                canonical_repo_source="git@example.test/repo.git",
                repo_name="repo",
            )
        ),
    )
    provider = AgentCloneRepoEffectProvider()
    provider.initialize(
        AgentCloneRepoEffectDefinition(
            id="effect-1",
            display_name="Clone",
            type="effect:agent:clone-repo",
            session_id="session-1",
            repo_source="git@example.test/repo.git",
        ),
        _context(tmp_path, session_repository),
    )

    result = provider.execute()

    assert result["success"] is True
    assert backend.started_specs[0].image == "runner:clone-image"


def test_agent_bootstrap_frontend_falls_back_to_default_container_image_for_legacy_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session_repository = JsonSessionManifestRepository(tmp_path / "sessions.json")
    _session(session_repository, session_id="session-1")
    backend = _ContainerDispatchBackendStub()
    monkeypatch.setattr(
        "evander.effects.providers.agent_bootstrap_frontend.container_backend",
        lambda context, config: backend,
    )
    provider = AgentBootstrapFrontendEffectProvider()
    provider.initialize(
        AgentBootstrapFrontendEffectDefinition(
            id="effect-1",
            display_name="Bootstrap",
            type="effect:agent:bootstrap-frontend",
            session_id="session-1",
            host_checkout=str(tmp_path / "checkout"),
            install_roots=[str(tmp_path / "checkout" / "frontend")],
        ),
        _context(tmp_path, session_repository, default_image="runner:default-image"),
    )

    result = provider.execute()

    assert result["success"] is True
    assert backend.started_specs[0].image == "runner:default-image"

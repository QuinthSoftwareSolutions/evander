from __future__ import annotations

import multiprocessing
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from evander.admin.services.codex_auth_admin_service import (
    CodexAuthAdminDependencies,
    CodexAuthAdminService,
)
from evander.admin.services.session_service import SessionAdminDependencies, SessionAdminService
from evander.config import GatewayConfig
from evander.execution.container_worker_client import ContainerWorkerClientError
from evander.models import SessionManifest
from evander.repositories.session_manifests import JsonSessionManifestRepository
from evander.subscription_profile_bindings import save_session_with_subscription_profile


class ProfileClient:
    base_url = "http://worker.test/"

    def __init__(self, root: Path, deleting=None, finish_delete=None) -> None:
        self.root = root
        self.deleting = deleting
        self.finish_delete = finish_delete

    def codex_auth_profile_status(self, profile_id):
        if not (self.root / f"{profile_id}.profile").is_file():
            raise ContainerWorkerClientError("Profile not found", status_code=404)
        return {"profile_id": profile_id, "enabled": True}

    def delete_codex_auth_profile(self, profile_id):
        self.deleting.set()
        if not self.finish_delete.wait(10):
            raise RuntimeError("Test worker deletion was not released")
        (self.root / f"{profile_id}.profile").unlink()
        return {"profile_id": profile_id, "deleted": True}


def _delete_in_process(root: Path, deleting, finish_delete, result) -> None:
    config = GatewayConfig(runtime_dir=root, container_worker_base_url="http://worker.test")
    client = ProfileClient(root, deleting, finish_delete)
    service = CodexAuthAdminService(
        CodexAuthAdminDependencies(
            provider_config=SimpleNamespace(
                resolve_for_directory=lambda _path: config,
                resolve_for_session_id=lambda _session: config,
            ),
            audit=SimpleNamespace(log=lambda _event: None),
            runtime_dir=root,
            session_repository=JsonSessionManifestRepository(config.session_store_path),
            client_factory=lambda **_kwargs: client,
        )
    )
    try:
        deleted = service.delete_profile(
            "root",
            "team-a",
            {
                "expected_worker_url": "http://worker.test/",
            },
            actor_user_id="owner@example.test",
        )
        result.put(deleted)
    except Exception as exc:
        result.put({"error": repr(exc)})


def _session(session_id: str, profile_id: str, *, expired: bool = False) -> SessionManifest:
    return SessionManifest(
        session_id=session_id,
        vm_provider="container_worker_vm",
        allowed_tools=[],
        codex_auth_mode="chatgpt_subscription",
        subscription_profile_id=profile_id,
        expires_at=datetime.now(UTC) + timedelta(hours=-1 if expired else 1),
        metadata={"worker_url": "http://worker.test"},
    )


def _session_admin(config, repository, *, assigned_config=None):
    return SessionAdminService(
        SessionAdminDependencies(
            config=config,
            directory_ownership=SimpleNamespace(),
            runtime=SimpleNamespace(
                session_repository=repository,
                provider_config=SimpleNamespace(
                    resolve_for_directory=lambda _path: config,
                    resolve_for_session_id=lambda _sid: assigned_config or config,
                ),
            ),
        )
    )


def test_deletion_in_another_process_blocks_new_binding_but_not_other_profiles(
    tmp_path: Path,
) -> None:
    config = GatewayConfig(runtime_dir=tmp_path, container_worker_base_url="http://worker.test")
    repository = JsonSessionManifestRepository(config.session_store_path)
    for profile_id in ("team-a", "team-b"):
        (tmp_path / f"{profile_id}.profile").touch()
    context = multiprocessing.get_context("spawn")
    deleting, finish_delete, result = context.Event(), context.Event(), context.Queue()
    process = context.Process(
        target=_delete_in_process, args=(tmp_path, deleting, finish_delete, result)
    )
    process.start()
    try:
        assert deleting.wait(10), "Deletion did not reach the worker"
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(
                save_session_with_subscription_profile,
                config=config,
                repository=repository,
                session=_session("blocked", "team-a"),
                client=ProfileClient(tmp_path),
            )
            try:
                with pytest.raises(TimeoutError):
                    pending.result(timeout=0.2)
                save_session_with_subscription_profile(
                    config=config,
                    repository=repository,
                    session=_session("independent", "team-b"),
                    client=ProfileClient(tmp_path),
                )
            finally:
                finish_delete.set()
            with pytest.raises(ValueError, match="no longer exists"):
                pending.result(timeout=10)
        assert result.get(timeout=10) == {"profile_id": "team-a", "deleted": True}
        assert repository.get_session("blocked") is None
        assert repository.get_session("independent") is not None
    finally:
        finish_delete.set()
        process.join(timeout=10)
        if process.is_alive():
            process.terminate()
            process.join(timeout=5)
        result.close()
        result.join_thread()
    assert process.exitcode == 0


def test_an_expired_binding_cannot_be_renewed_after_its_profile_is_deleted(
    tmp_path: Path, monkeypatch
) -> None:
    config = GatewayConfig(runtime_dir=tmp_path, container_worker_base_url="http://worker.test")
    repository = JsonSessionManifestRepository(config.session_store_path)
    original = _session("expired", "team-a", expired=True)
    repository.upsert_session(original)
    monkeypatch.setattr(
        "evander.subscription_profile_bindings.ContainerWorkerClient",
        lambda **_kwargs: ProfileClient(tmp_path),
    )
    with pytest.raises(ValueError, match="no longer exists"):
        _session_admin(config, repository).renew_session("expired")
    assert repository.get_session("expired").expires_at == original.expires_at


@pytest.mark.parametrize("vm_provider", ["libvirt", None])
def test_vm_renewal_ignores_the_container_subscription_default(tmp_path: Path, vm_provider) -> None:
    config = GatewayConfig(
        runtime_dir=tmp_path,
        container_worker_base_url="http://worker.test",
        container_worker_codex_auth_mode="chatgpt_subscription",
    )
    repository = JsonSessionManifestRepository(config.session_store_path)
    original = SessionManifest(
        session_id="ordinary-vm",
        vm_id="vm-1",
        vm_provider=vm_provider,
        allowed_tools=[],
        expires_at=datetime.now(UTC) - timedelta(hours=1),
    )
    repository.upsert_session(original)
    assert _session_admin(config, repository).renew_session("ordinary-vm").success
    assert repository.get_session("ordinary-vm").expires_at > datetime.now(UTC)


def test_renewal_classifies_legacy_auth_using_the_saved_worker_config(
    tmp_path: Path, monkeypatch
) -> None:
    directory_config = GatewayConfig(
        runtime_dir=tmp_path, container_worker_base_url="http://worker.test"
    )
    assigned_config = directory_config.model_copy(
        update={
            "container_worker_codex_auth_mode": "chatgpt_subscription",
        }
    )
    repository = JsonSessionManifestRepository(directory_config.session_store_path)
    original = _session("legacy", "default", expired=True).model_copy(
        update={
            "codex_auth_mode": None,
            "subscription_profile_id": None,
        }
    )
    repository.upsert_session(original)
    monkeypatch.setattr(
        "evander.subscription_profile_bindings.ContainerWorkerClient",
        lambda **_kwargs: ProfileClient(tmp_path),
    )
    with pytest.raises(ValueError, match="no longer exists"):
        _session_admin(directory_config, repository, assigned_config=assigned_config).renew_session(
            "legacy"
        )
    assert repository.get_session("legacy").expires_at == original.expires_at


def test_an_existing_live_binding_can_keep_a_disabled_profile(tmp_path: Path) -> None:
    config = GatewayConfig(runtime_dir=tmp_path, container_worker_base_url="http://worker.test")
    repository = JsonSessionManifestRepository(config.session_store_path)
    original = _session("existing", "team-a")
    repository.upsert_session(original)
    save_session_with_subscription_profile(
        config=config,
        repository=repository,
        session=original.model_copy(update={"model": "gpt-5.4"}),
        client=SimpleNamespace(
            base_url="http://worker.test", codex_auth_profile_status=lambda _id: {"enabled": False}
        ),
    )
    assert repository.get_session("existing").model == "gpt-5.4"

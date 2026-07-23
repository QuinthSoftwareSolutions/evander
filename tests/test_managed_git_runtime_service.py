from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from evander.admin.services.managed_git_runtime_service import (
    ManagedGitMaintenanceService,
    ManagedGitRefreshAuthorizationDependencies,
    ManagedGitRefreshAuthorizationService,
    ManagedGitRepositoryRefreshService,
)
from evander.config import GatewayConfig, TriggerOrchestratorWorkerGlobalConfig
from evander.orchestration.managed_git_lifecycle import ManagedGitWorkerLifecycle
from evander.orchestration.wiring import build_effect_worker_wiring


class _ManagedGitOperations:
    def __init__(self) -> None:
        self.calls: list[object] = []

    def preflight(self) -> dict[str, object]:
        self.calls.append("preflight")
        return {"repository_count": 2}

    def reconcile_stale_worktrees(self) -> dict[str, object]:
        self.calls.append("reconcile")
        return {"removed": []}

    def cleanup_active_worktrees(self) -> dict[str, int]:
        self.calls.append("cleanup")
        return {"cleaned": 0, "failed": 0}

    def refresh_repository(self, managed_repo_id: str) -> dict[str, object]:
        self.calls.append(("refresh", managed_repo_id))
        return {"managed_repo_id": managed_repo_id}


def test_managed_git_runtime_services_expose_narrow_operation_views() -> None:
    operations = _ManagedGitOperations()
    maintenance = ManagedGitMaintenanceService(operations)
    repository_refresh = ManagedGitRepositoryRefreshService(operations)

    assert maintenance.preflight() == {"repository_count": 2}
    assert maintenance.reconcile_stale_worktrees() == {"removed": []}
    assert maintenance.cleanup_active_worktrees() == {"cleaned": 0, "failed": 0}
    assert repository_refresh.refresh_repository("mr_0123456789abcdef0123") == {
        "managed_repo_id": "mr_0123456789abcdef0123"
    }
    assert operations.calls == [
        "preflight",
        "reconcile",
        "cleanup",
        ("refresh", "mr_0123456789abcdef0123"),
    ]


def test_refresh_authorization_uses_preassembled_directory_config_collaborators(
    tmp_path,
) -> None:
    root = SimpleNamespace(path="root")
    team = SimpleNamespace(path="root/team")

    class _Directories:
        def show(self, reference: str) -> SimpleNamespace:
            assert reference == "root"
            return SimpleNamespace(directory=root)

        def list_descendants(self, reference: str) -> list[SimpleNamespace]:
            assert reference == "root"
            return [team]

    class _Resolver:
        def resolve_domain(self, directory_path: str, **_kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(
                effective_config={
                    "git_user": "root-git" if directory_path == "root" else "team-git"
                }
            )

    service = ManagedGitRefreshAuthorizationService(
        ManagedGitRefreshAuthorizationDependencies(
            config=GatewayConfig(runtime_dir=tmp_path),
            directory_service=_Directories(),  # type: ignore[arg-type]
            directory_config_resolver=_Resolver(),  # type: ignore[arg-type]
            resolve_user_uid=lambda user: {"root-git": 2001, "team-git": 2002}[user],
        )
    )

    assert service.configured_git_uids() == {2001, 2002}


def test_worker_lifecycle_owns_preflight_reconciliation_and_cleanup() -> None:
    first_operations = _ManagedGitOperations()
    second_operations = _ManagedGitOperations()
    now = [100.0]
    lifecycle = ManagedGitWorkerLifecycle(
        reconcile_interval_seconds=300,
        monotonic=lambda: now[0],
    )

    lifecycle.configure(ManagedGitMaintenanceService(first_operations))
    lifecycle.reconcile(force=True)
    lifecycle.reconcile()
    now[0] += 301
    lifecycle.reconcile()
    lifecycle.configure(ManagedGitMaintenanceService(second_operations))
    lifecycle.cleanup_active_worktrees()

    assert first_operations.calls == ["preflight", "reconcile", "reconcile", "cleanup"]
    assert second_operations.calls == ["preflight", "cleanup"]


def test_effect_wiring_shares_one_composed_admin_runtime(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    config = GatewayConfig(runtime_dir=runtime_dir)
    worker_config = TriggerOrchestratorWorkerGlobalConfig.model_validate(
        {
            "runtime_dir": runtime_dir,
            "poll_interval_seconds": 1,
            "jitter_seconds": 0,
            "triggers": [],
            "effects": [],
            "gates": [],
            "queries": [],
            "edges": [],
        }
    )
    wiring = build_effect_worker_wiring(
        config=config,
        worker_config=worker_config,
        runtime=None,  # type: ignore[arg-type]
    )

    try:
        admin_runtime = wiring.provider_factory.admin_service._admin_runtime  # noqa: SLF001
        assert (
            wiring.query_provider_factory.admin_service._admin_runtime is admin_runtime  # noqa: SLF001
        )
        assert (
            wiring.gate_provider_factory.admin_service._admin_runtime is admin_runtime  # noqa: SLF001
        )
        assert (
            wiring.agent_run_status_port._admin_runtime is admin_runtime  # type: ignore[union-attr]  # noqa: SLF001
        )
        assert (
            wiring.agent_prompt_dispatch_port._admin_runtime is admin_runtime  # type: ignore[union-attr]  # noqa: SLF001
        )
        assert wiring.managed_git_maintenance is admin_runtime.managed_git_maintenance
    finally:
        wiring.executor.shutdown(wait=True)

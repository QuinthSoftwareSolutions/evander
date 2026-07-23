from __future__ import annotations

import os
from pathlib import Path
import subprocess
import threading
import time
from types import SimpleNamespace

import pytest

import evander.managed_git_refresh as refresh
from evander.admin.services.managed_git_runtime_service import (
    ManagedGitRefreshAuthorizationDependencies,
    ManagedGitRefreshAuthorizationService,
)
from evander.admin.services.managed_git_service import ManagedGitAdminService
from evander.config import GatewayConfig


class _FakeBroker:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.requests: list[str] = []
        self.peer_authorized = False

    def authorize_peer(self, _connection: object) -> None:
        self.peer_authorized = True

    def refresh(self, *, managed_repo_id: str) -> dict[str, str]:
        self.requests.append(managed_repo_id)
        if self.error is not None:
            raise self.error
        return {
            "managed_repo_id": managed_repo_id,
            "refreshed_at": "2026-07-22T10:00:00Z",
        }


def _running_server(
    tmp_path: Path,
    broker: _FakeBroker,
    *,
    max_concurrent_requests: int = refresh.DEFAULT_MAX_CONCURRENT_REQUESTS,
):
    socket_path = tmp_path / "refresh.sock"
    server = refresh._RefreshServer(  # noqa: SLF001
        socket_path,
        broker,
        max_concurrent_requests=max_concurrent_requests,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return socket_path, server, thread


def test_refresh_client_requests_exact_managed_repository(tmp_path: Path) -> None:
    broker = _FakeBroker()
    socket_path, server, thread = _running_server(tmp_path, broker)
    git_root = tmp_path / "managed-git"
    repository = git_root / "mr_0123456789abcdef0123.git"

    try:
        assert socket_path.stat().st_mode & 0o777 == 0o666
        response = refresh.request_refresh(
            socket_path=socket_path,
            git_root=git_root,
            repository_path=repository,
            timeout_seconds=1,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert response["managed_repo_id"] == "mr_0123456789abcdef0123"
    assert broker.peer_authorized is True
    assert broker.requests == ["mr_0123456789abcdef0123"]


@pytest.mark.parametrize(
    "repository",
    [
        "legacy.git",
        "mr_0123456789abcdef0123",
        "../mr_0123456789abcdef0123.git",
        "nested/mr_0123456789abcdef0123.git",
    ],
)
def test_refresh_client_rejects_non_managed_or_non_exact_paths(
    tmp_path: Path,
    repository: str,
) -> None:
    git_root = tmp_path / "managed-git"

    with pytest.raises(ValueError):
        refresh._managed_repo_id_from_path(  # noqa: SLF001
            git_root=git_root,
            repository_path=git_root / repository,
        )


def test_refresh_client_fails_closed_on_broker_error(tmp_path: Path) -> None:
    broker = _FakeBroker(error=RuntimeError("secret-bearing upstream failure"))
    socket_path, server, thread = _running_server(tmp_path, broker)
    git_root = tmp_path / "managed-git"

    try:
        with pytest.raises(RuntimeError, match="upstream refresh failed.*RuntimeError") as error:
            refresh.request_refresh(
                socket_path=socket_path,
                git_root=git_root,
                repository_path=git_root / "mr_0123456789abcdef0123.git",
                timeout_seconds=1,
            )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert "secret-bearing" not in str(error.value)


def test_broker_delegates_to_managed_git_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class _ManagedGit:
        def refresh_repository(self, managed_repo_id: str) -> dict[str, str]:
            calls.append(managed_repo_id)
            return {"refreshed_at": "2026-07-22T10:00:00Z"}

    monkeypatch.setattr(
        refresh,
        "build_admin_runtime",
        lambda **_kwargs: SimpleNamespace(
            managed_git_repository_refresh=_ManagedGit(),
            managed_git_refresh_authorization=SimpleNamespace(configured_git_uids=lambda: set()),
        ),
    )
    broker = refresh.ManagedGitRefreshBroker(runtime_dir=tmp_path, allowed_uid=os.getuid())

    result = broker.refresh(managed_repo_id="mr_0123456789abcdef0123")

    assert calls == ["mr_0123456789abcdef0123"]
    assert result["refreshed_at"] == "2026-07-22T10:00:00Z"


def test_broker_rejects_invalid_repository_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        refresh,
        "build_admin_runtime",
        lambda **_kwargs: SimpleNamespace(
            managed_git_repository_refresh=object(),
            managed_git_refresh_authorization=SimpleNamespace(configured_git_uids=lambda: set()),
        ),
    )
    broker = refresh.ManagedGitRefreshBroker(runtime_dir=tmp_path, allowed_uid=os.getuid())

    with pytest.raises(ValueError, match="mr_<20-hex>"):
        broker.refresh(managed_repo_id="../outside")


def test_broker_authorizes_all_effective_directory_git_users(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = SimpleNamespace(path="root")
    child = SimpleNamespace(path="root/team")

    class _DirectoryService:
        def show(self, reference: str) -> SimpleNamespace:
            assert reference == "root"
            return SimpleNamespace(directory=root)

        def list_descendants(self, reference: str) -> list[SimpleNamespace]:
            assert reference == "root"
            return [child]

    class _Resolver:
        def resolve_domain(self, directory_path: str, **_kwargs: object) -> SimpleNamespace:
            return SimpleNamespace(
                effective_config={
                    "git_user": "root-git" if directory_path == "root" else "team-git"
                }
            )

    authorization = ManagedGitRefreshAuthorizationService(
        ManagedGitRefreshAuthorizationDependencies(
            config=GatewayConfig(runtime_dir=tmp_path),
            directory_service=_DirectoryService(),  # type: ignore[arg-type]
            directory_config_resolver=_Resolver(),  # type: ignore[arg-type]
            resolve_user_uid=lambda user: {
                "root-git": 2001,
                "team-git": 2002,
            }[user],
        )
    )
    monkeypatch.setattr(refresh, "_peer_credentials", lambda _connection: (99, 2002, 2002))
    broker = refresh.ManagedGitRefreshBroker(
        runtime_dir=tmp_path,
        allowed_uid=1000,
        repository_refresh=object(),  # type: ignore[arg-type]
        authorization=authorization,
    )

    assert broker._configured_git_uids() == {2001, 2002}  # noqa: SLF001
    broker.authorize_peer(object())  # type: ignore[arg-type]


def test_broker_coalesces_overlapping_refreshes_for_one_repository(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    refresh_started = threading.Event()
    release_refresh = threading.Event()

    class _ManagedGit:
        def __init__(self) -> None:
            self.calls = 0

        def refresh_repository(self, _managed_repo_id: str) -> dict[str, str]:
            self.calls += 1
            refresh_started.set()
            assert release_refresh.wait(timeout=2)
            return {"refreshed_at": "2026-07-22T10:00:00Z"}

    managed_git = _ManagedGit()
    monkeypatch.setattr(
        refresh,
        "build_admin_runtime",
        lambda **_kwargs: SimpleNamespace(
            managed_git_repository_refresh=managed_git,
            managed_git_refresh_authorization=SimpleNamespace(configured_git_uids=lambda: set()),
        ),
    )
    broker = refresh.ManagedGitRefreshBroker(runtime_dir=tmp_path, allowed_uid=os.getuid())
    results: list[dict[str, object]] = []

    def request() -> None:
        results.append(broker.refresh(managed_repo_id="mr_0123456789abcdef0123"))

    first = threading.Thread(target=request)
    second = threading.Thread(target=request)
    first.start()
    assert refresh_started.wait(timeout=1)
    second.start()
    time.sleep(0.05)
    release_refresh.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert not first.is_alive()
    assert not second.is_alive()
    assert managed_git.calls == 1
    assert results == [
        {
            "managed_repo_id": "mr_0123456789abcdef0123",
            "refreshed_at": "2026-07-22T10:00:00Z",
        },
        {
            "managed_repo_id": "mr_0123456789abcdef0123",
            "refreshed_at": "2026-07-22T10:00:00Z",
        },
    ]


def test_refresh_server_rejects_requests_above_concurrency_limit(tmp_path: Path) -> None:
    class _BlockingBroker(_FakeBroker):
        def __init__(self) -> None:
            super().__init__()
            self.started = threading.Event()
            self.release = threading.Event()

        def refresh(self, *, managed_repo_id: str) -> dict[str, str]:
            self.requests.append(managed_repo_id)
            self.started.set()
            assert self.release.wait(timeout=2)
            return {
                "managed_repo_id": managed_repo_id,
                "refreshed_at": "2026-07-22T10:00:00Z",
            }

    broker = _BlockingBroker()
    socket_path, server, server_thread = _running_server(
        tmp_path,
        broker,
        max_concurrent_requests=1,
    )
    git_root = tmp_path / "managed-git"
    repository = git_root / "mr_0123456789abcdef0123.git"
    first_result: list[dict[str, object]] = []

    def first_request() -> None:
        first_result.append(
            refresh.request_refresh(
                socket_path=socket_path,
                git_root=git_root,
                repository_path=repository,
                timeout_seconds=2,
            )
        )

    first_client = threading.Thread(target=first_request)
    first_client.start()
    assert broker.started.wait(timeout=1)
    try:
        with pytest.raises(RuntimeError, match="broker is busy.*BrokerBusy"):
            refresh.request_refresh(
                socket_path=socket_path,
                git_root=git_root,
                repository_path=repository,
                timeout_seconds=1,
            )
    finally:
        broker.release.set()
        first_client.join(timeout=2)
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)

    assert len(first_result) == 1
    assert broker.requests == ["mr_0123456789abcdef0123"]


def test_pull_time_request_advances_canonical_refs_to_latest_upstream(tmp_path: Path) -> None:
    upstream = tmp_path / "upstream.git"
    source = tmp_path / "source"
    subprocess.run(["git", "init", "--bare", str(upstream)], check=True, capture_output=True)
    subprocess.run(
        ["git", "init", "-b", "main", str(source)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Refresh Test"],
        cwd=source,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "refresh@example.invalid"],
        cwd=source,
        check=True,
    )
    (source / "README.md").write_text("first\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=source, check=True)
    subprocess.run(["git", "commit", "-m", "first"], cwd=source, check=True)
    subprocess.run(["git", "remote", "add", "origin", str(upstream)], cwd=source, check=True)
    subprocess.run(["git", "push", "-u", "origin", "main"], cwd=source, check=True)

    service = ManagedGitAdminService(
        SimpleNamespace(runtime_dir=tmp_path / "runtime"),
        SimpleNamespace(),
        tmp_path,
    )
    record = service.register_repository("root", str(source))
    managed_repo_id = str(record["managed_repo_id"])
    managed_repo = tmp_path / "runtime" / "managed-git" / f"{managed_repo_id}.git"

    (source / "README.md").write_text("second\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=source, check=True)
    subprocess.run(["git", "commit", "-m", "second"], cwd=source, check=True)
    subprocess.run(["git", "push", "origin", "main"], cwd=source, check=True)
    latest_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=source,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    class _ServiceBroker:
        def authorize_peer(self, _connection: object) -> None:
            return None

        def refresh(self, *, managed_repo_id: str) -> dict[str, object]:
            return service.refresh_repository(managed_repo_id)

    socket_path, server, thread = _running_server(tmp_path, _ServiceBroker())  # type: ignore[arg-type]
    try:
        refresh.request_refresh(
            socket_path=socket_path,
            git_root=managed_repo.parent,
            repository_path=managed_repo,
            timeout_seconds=5,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    managed_main = subprocess.run(
        ["git", f"--git-dir={managed_repo}", "rev-parse", "refs/heads/main"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert managed_main == latest_sha

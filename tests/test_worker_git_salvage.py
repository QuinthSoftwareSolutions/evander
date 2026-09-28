"""The worker must bind salvage to its session, checkout receipt, and transport."""

from starlette.testclient import TestClient

from evander.worker.models import WorkerSessionPaths
from evander.worker.service import WorkerDaemon
from evander.worker.api import build_worker_app
from test_worker_daemon import FakeContainerRuntime
from test_git_salvage import checkout, git


def test_worker_salvage_authentication_namespace_and_immutable_git_transport(tmp_path):
    daemon = WorkerDaemon(
        runtime_dir=tmp_path / "runtime",
        worker_root=tmp_path / "worker",
        container_runtime=FakeContainerRuntime(),
        token="worker-token",
        allowed_git_source_prefixes=[str(tmp_path)],
    )
    paths = WorkerSessionPaths.resolve(daemon.worker_root, "session")
    repository, original_worktree, remote = checkout(tmp_path)
    paths.repo_root.mkdir(parents=True, exist_ok=True)
    # Move via Git so the linked worktree administration remains authoritative.
    target = paths.agent_home / "worktrees/repo/task"
    target.parent.mkdir(parents=True, exist_ok=True)
    git(repository, "worktree", "move", str(original_worktree), str(target))
    # The worker's reference checkout is real, not an external symlink.
    import shutil

    shutil.move(str(repository), str(paths.repo_root / "repo"))
    repository = paths.repo_root / "repo"
    git(repository, "worktree", "repair", str(target))
    (target / "file").write_text("retained edit\n")
    body = {
        "repo_name": "repo",
        "worktree_name": "task",
        "repo_source": str(remote),
        "expected_branch": "agents/session/task",
        "recovery_branch": "agents/session/recovery",
    }
    endpoint = "/v1/sessions/session/worktrees/salvage"
    with TestClient(build_worker_app(daemon)) as client:
        assert client.get("/healthz").json()["capabilities"]["git_salvage"] == 1
        assert client.post(endpoint, json=body).status_code == 401
        client.headers["Authorization"] = "Bearer worker-token"
        assert (
            client.post(
                endpoint, json={**body, "recovery_branch": "agents/other/recovery"}
            ).status_code
            == 409
        )
        assert (
            client.post(
                endpoint, json={**body, "repo_source": "https://untrusted.invalid/repo"}
            ).status_code
            == 409
        )
        response = client.post(endpoint, json=body)
        assert response.status_code == 200, response.text
        receipt = response.json()
        assert git(remote, "show", f"{receipt['work_ref']}:file") == "retained edit"
        assert git(target, "status", "--porcelain") == "M file"
        replay = client.post(endpoint, json=body)
        assert replay.status_code == 200, replay.text
        assert replay.json()["work_ref"] == receipt["work_ref"]

from pathlib import Path
import subprocess

import pytest


@pytest.mark.parametrize(
    "role_task_path",
    [
        "ansible/roles/package_cache_nexus/tasks/main.yml",
        "ansible/roles/apt_cache_relay/tasks/main.yml",
        "ansible/roles/semantic_store_qdrant/tasks/main.yml",
        "ansible/roles/admin_web_caddy/tasks/main.yml",
        "ansible/roles/worker_apt_proxy/tasks/main.yml",
        "ansible/roles/worker_git_access/tasks/main.yml",
        "ansible/roles/container_worker_runtime/tasks/main.yml",
        "ansible/roles/worker_package_cache_preflight/tasks/main.yml",
        "ansible/roles/worker_dotnet_toolchain/tasks/main.yml",
        "ansible/roles/worker_node_toolchain/tasks/main.yml",
        "ansible/roles/worker_playwright_toolchain/tasks/main.yml",
        "ansible/roles/worker_mssql/tasks/main.yml",
        "ansible/roles/worker_mssql_client_toolchain/tasks/main.yml",
        "ansible/roles/worker_container_egress_policy/tasks/main.yml",
        "ansible/roles/container_worker_service/tasks/main.yml",
        "ansible/roles/container_worker_verification/tasks/main.yml",
    ],
    ids=lambda path: path.split("/")[2],
)
def test_ansible_role_tasks_are_tracked_and_not_ignored(role_task_path: str) -> None:
    repo = Path(__file__).resolve().parents[1]
    # Tracked files must still be checked against ignore rules. Without --no-index,
    # git silently excludes them and the original guard can never catch a regression.
    ignored = subprocess.run(
        ["git", "check-ignore", "--no-index", "--quiet", "--", role_task_path],
        cwd=repo,
        check=False,
    )
    assert ignored.returncode == 1, role_task_path
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", role_task_path],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert tracked.returncode == 0, role_task_path

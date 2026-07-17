from __future__ import annotations

import argparse
import os
from pathlib import Path

import uvicorn

from .api import create_app
from .models import DEFAULT_WORKER_ROOT
from .sandbox_profile import worker_sandbox_profile


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the worker-VM container job daemon.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=11888)
    parser.add_argument("--runtime-dir", type=Path, default=Path("runtime"))
    parser.add_argument("--worker-root", type=Path, default=DEFAULT_WORKER_ROOT)
    parser.add_argument(
        "--sandbox-profile",
        default=os.environ.get("EVANDER_WORKER_SANDBOX_PROFILE", "default"),
        choices=("default", "strict"),
    )
    parser.add_argument(
        "--docker-network",
        default=os.environ.get("EVANDER_WORKER_DOCKER_NETWORK"),
    )
    parser.add_argument(
        "--unsafe-allow-host-network",
        action="store_true",
        default=os.environ.get("EVANDER_WORKER_UNSAFE_ALLOW_HOST_NETWORK", "").strip().lower()
        in {"1", "true", "yes", "on"},
        help=(
            "Allow --docker-network host for single-host local experiments. "
            "This weakens the container isolation boundary and should not be used for customers."
        ),
    )
    parser.add_argument("--read-only-rootfs", action="store_true")
    parser.add_argument("--tmpfs", action="append", default=None)
    parser.add_argument(
        "--container-user",
        default=os.environ.get("EVANDER_WORKER_CONTAINER_USER"),
    )
    parser.add_argument(
        "--readonly-mount",
        action="append",
        default=None,
        help=(
            "Read-only bind mount in /host/path:/container/path form. "
            "Repeat for worker-level toolchain caches."
        ),
    )
    parser.add_argument(
        "--allowed-git-source-prefix",
        action="append",
        default=None,
        help=(
            "Allowed repo_source prefix for worker-side Git clones. "
            "Repeat to allow multiple control-plane mirror prefixes."
        ),
    )
    parser.add_argument(
        "--git-ssh-source-dir",
        type=Path,
        default=(
            Path(os.environ["EVANDER_WORKER_GIT_SSH_SOURCE_DIR"])
            if os.environ.get("EVANDER_WORKER_GIT_SSH_SOURCE_DIR")
            else None
        ),
        help="Directory containing restricted Git SSH config/key copied into session agent homes.",
    )
    parser.add_argument(
        "--mssql-enabled",
        action="store_true",
        default=os.environ.get("EVANDER_WORKER_MSSQL_ENABLED", "").strip().lower()
        in {"1", "true", "yes", "on"},
        help="Enable session-scoped SQL Server provisioning endpoints.",
    )
    parser.add_argument(
        "--mssql-admin-user",
        default=os.environ.get("EVANDER_WORKER_MSSQL_ADMIN_USER", "sa"),
    )
    parser.add_argument(
        "--mssql-admin-password-env",
        default=os.environ.get(
            "EVANDER_WORKER_MSSQL_ADMIN_PASSWORD_ENV",
            "EVANDER_WORKER_MSSQL_ADMIN_PASSWORD",
        ),
    )
    parser.add_argument(
        "--mssql-admin-host",
        default=os.environ.get("EVANDER_WORKER_MSSQL_ADMIN_HOST", "127.0.0.1"),
    )
    parser.add_argument(
        "--mssql-port",
        type=int,
        default=int(os.environ.get("EVANDER_WORKER_MSSQL_PORT", "1433")),
    )
    parser.add_argument(
        "--mssql-container-host",
        default=os.environ.get("EVANDER_WORKER_MSSQL_CONTAINER_HOST", "172.17.0.1"),
    )
    parser.add_argument(
        "--mssql-sqlcmd-path",
        default=os.environ.get("EVANDER_WORKER_MSSQL_SQLCMD_PATH", "sqlcmd"),
    )
    args = parser.parse_args()
    sandbox_profile = worker_sandbox_profile(args.sandbox_profile).with_overrides(
        network_mode=args.docker_network,
        allow_host_network=True if args.unsafe_allow_host_network else None,
        read_only_rootfs=True if args.read_only_rootfs else None,
        tmpfs=args.tmpfs,
        user=args.container_user,
    )

    uvicorn.run(
        create_app(
            runtime_dir=args.runtime_dir,
            worker_root=args.worker_root,
            sandbox_profile=sandbox_profile,
            docker_network=None,
            read_only_mounts=args.readonly_mount,
            allowed_git_source_prefixes=args.allowed_git_source_prefix,
            git_ssh_source_dir=args.git_ssh_source_dir,
            mssql_enabled=args.mssql_enabled,
            mssql_admin_user=args.mssql_admin_user,
            mssql_admin_password_env=args.mssql_admin_password_env,
            mssql_admin_host=args.mssql_admin_host,
            mssql_port=args.mssql_port,
            mssql_container_host=args.mssql_container_host,
            mssql_sqlcmd_path=args.mssql_sqlcmd_path,
        ),
        host=args.host,
        port=args.port,
    )

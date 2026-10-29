from __future__ import annotations

import argparse
import hashlib
import os
import shlex
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
    parser.add_argument(
        "--codex-auth-image",
        default=os.environ.get("EVANDER_WORKER_CODEX_AUTH_IMAGE"),
        help="Runner image used for the worker-owned managed Codex auth utility container.",
    )
    parser.add_argument(
        "--codex-auth-command",
        default=os.environ.get("EVANDER_WORKER_CODEX_AUTH_COMMAND"),
        help="Advanced override for the managed Codex app-server command.",
    )
    parser.add_argument(
        "--codex-egress-proxy-url",
        default=os.environ.get("EVANDER_WORKER_CODEX_EGRESS_PROXY_URL"),
        help="Restricted HTTP CONNECT proxy URL reachable from runner containers.",
    )
    parser.add_argument(
        "--codex-egress-proxy-bind-host",
        default=os.environ.get("EVANDER_WORKER_CODEX_EGRESS_PROXY_BIND_HOST"),
        help="Optional address on which the worker daemon hosts the restricted proxy.",
    )
    parser.add_argument(
        "--codex-egress-proxy-port",
        type=int,
        default=int(os.environ.get("EVANDER_WORKER_CODEX_EGRESS_PROXY_PORT", "11889")),
    )
    parser.add_argument(
        "--codex-egress-host",
        action="append",
        default=None,
        help="Allowed HTTPS hostname or wildcard for subscription traffic; repeat as needed.",
    )
    args = parser.parse_args()
    sandbox_profile = worker_sandbox_profile(args.sandbox_profile).with_overrides(
        network_mode=args.docker_network,
        allow_host_network=True if args.unsafe_allow_host_network else None,
        read_only_rootfs=True if args.read_only_rootfs else None,
        tmpfs=args.tmpfs,
        user=args.container_user,
    )
    codex_auth_command = _codex_auth_command(
        explicit_command=args.codex_auth_command,
        image=args.codex_auth_image,
        worker_root=args.worker_root,
        docker_network=sandbox_profile.network_mode,
        container_user=sandbox_profile.user,
        proxy_url=args.codex_egress_proxy_url,
    )
    codex_auth_command_factory = None
    if args.codex_auth_image:

        def codex_auth_command_factory(profile_id: str, auth_home: Path) -> tuple[str, ...]:
            return _codex_auth_command(
                explicit_command=args.codex_auth_command,
                image=args.codex_auth_image,
                worker_root=args.worker_root,
                docker_network=sandbox_profile.network_mode,
                container_user=sandbox_profile.user,
                proxy_url=args.codex_egress_proxy_url,
                profile_id=profile_id,
                auth_home=auth_home,
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
            codex_auth_command=codex_auth_command,
            codex_auth_command_factory=codex_auth_command_factory,
            codex_egress_proxy_url=args.codex_egress_proxy_url,
            codex_egress_proxy_bind_host=args.codex_egress_proxy_bind_host,
            codex_egress_proxy_port=args.codex_egress_proxy_port,
            codex_egress_allowed_hosts=args.codex_egress_host,
        ),
        host=args.host,
        port=args.port,
    )


def _codex_auth_command(
    *,
    explicit_command: str | None,
    image: str | None,
    worker_root: Path,
    docker_network: str,
    container_user: str | None,
    proxy_url: str | None,
    profile_id: str = "default",
    auth_home: Path | None = None,
) -> tuple[str, ...]:
    if explicit_command:
        command = tuple(shlex.split(explicit_command))
        if not command:
            raise ValueError("--codex-auth-command must not be empty")
        return command
    if image:
        auth_home = auth_home or worker_root / "codex-auth"
        auth_container_name = (
            "evander-codex-auth-"
            + hashlib.sha256(f"{worker_root.resolve()}\0{profile_id}".encode("utf-8")).hexdigest()[
                :12
            ]
        )
        command = [
            "docker",
            "run",
            "--rm",
            "-i",
            "--name",
            auth_container_name,
            "--network",
            docker_network,
            "--tmpfs",
            "/home/agent:rw,noexec,nosuid,nodev,size=16m,mode=1777",
            "-v",
            f"{auth_home}:/codex-auth",
            "-e",
            "CODEX_HOME=/codex-auth",
            "-e",
            "HOME=/home/agent",
        ]
        if container_user:
            command.extend(["--user", container_user])
        if proxy_url:
            command.extend(["-e", f"HTTPS_PROXY={proxy_url}"])
            command.extend(["-e", f"HTTP_PROXY={proxy_url}"])
            command.extend(["-e", "NO_PROXY=127.0.0.1,localhost"])
        command.extend([image, "codex", "app-server", "--stdio"])
        return tuple(command)
    return ("codex", "app-server", "--stdio")

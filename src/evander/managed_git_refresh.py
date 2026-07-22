from __future__ import annotations

import argparse
import json
import logging
import os
from pathlib import Path
import re
import socket
import socketserver
import struct
import sys
from typing import Any

from evander.admin.composition import build_admin_runtime


LOGGER = logging.getLogger(__name__)
DEFAULT_SOCKET_PATH = Path("/run/evander/managed-git-refresh.sock")
DEFAULT_TIMEOUT_SECONDS = 120.0
_MAX_REQUEST_BYTES = 4096
_MANAGED_REPO_NAME = re.compile(r"^(mr_[0-9a-f]{20})\.git$")


def _json_line(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8")


def _peer_credentials(connection: socket.socket) -> tuple[int, int, int] | None:
    if not hasattr(socket, "SO_PEERCRED"):
        return None
    raw = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    return struct.unpack("3i", raw)


class ManagedGitRefreshBroker:
    def __init__(self, *, runtime_dir: Path, allowed_uid: int | None = None) -> None:
        self._managed_git = build_admin_runtime(runtime_dir=runtime_dir).managed_git_admin
        self._allowed_uid = os.getuid() if allowed_uid is None else allowed_uid

    def refresh(self, *, managed_repo_id: str) -> dict[str, Any]:
        if _MANAGED_REPO_NAME.fullmatch(f"{managed_repo_id}.git") is None:
            raise ValueError("managed_repo_id must match mr_<20-hex>")
        result = self._managed_git.refresh_repository(managed_repo_id)
        return {
            "managed_repo_id": managed_repo_id,
            "refreshed_at": result.get("refreshed_at"),
        }

    def authorize_peer(self, connection: socket.socket) -> None:
        credentials = _peer_credentials(connection)
        if credentials is None:
            raise PermissionError("peer credential verification is unavailable")
        _pid, uid, _gid = credentials
        if uid != self._allowed_uid:
            raise PermissionError("refresh client is not the authorized host user")


class _RefreshRequestHandler(socketserver.StreamRequestHandler):
    server: "_RefreshServer"

    def handle(self) -> None:
        managed_repo_id: str | None = None
        try:
            self.server.broker.authorize_peer(self.connection)
            request_line = self.rfile.readline(_MAX_REQUEST_BYTES + 1)
            if not request_line or not request_line.endswith(b"\n"):
                raise ValueError("refresh request must be one newline-terminated JSON object")
            if len(request_line) > _MAX_REQUEST_BYTES:
                raise ValueError("refresh request is too large")
            request = json.loads(request_line)
            if not isinstance(request, dict):
                raise ValueError("refresh request must be a JSON object")
            managed_repo_id = str(request.get("managed_repo_id") or "").strip()
            result = self.server.broker.refresh(managed_repo_id=managed_repo_id)
            LOGGER.info("Managed Git refresh succeeded repo=%s", managed_repo_id)
            response = {"ok": True, **result}
        except Exception as exc:
            category = type(exc).__name__
            LOGGER.warning(
                "Managed Git refresh failed repo=%s category=%s",
                managed_repo_id or "unknown",
                category,
            )
            response = {
                "ok": False,
                "error": "Managed repository upstream refresh failed.",
                "error_category": category,
            }
        self.wfile.write(_json_line(response))
        self.wfile.flush()


class _RefreshServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True

    def __init__(
        self,
        socket_path: Path,
        broker: ManagedGitRefreshBroker,
    ) -> None:
        self.broker = broker
        super().__init__(str(socket_path), _RefreshRequestHandler)


def serve(*, runtime_dir: Path, socket_path: Path, allowed_uid: int | None = None) -> None:
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    if socket_path.exists() or socket_path.is_symlink():
        if not socket_path.is_socket():
            raise RuntimeError(f"Refusing to replace non-socket path: {socket_path}")
        socket_path.unlink()
    broker = ManagedGitRefreshBroker(runtime_dir=runtime_dir, allowed_uid=allowed_uid)
    server = _RefreshServer(socket_path, broker)
    os.chmod(socket_path, 0o660)
    LOGGER.info("Managed Git refresh broker listening socket=%s", socket_path)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()
        if socket_path.is_socket():
            socket_path.unlink()


def _managed_repo_id_from_path(*, git_root: Path, repository_path: Path) -> str:
    root = git_root.resolve(strict=False)
    repository = repository_path.resolve(strict=False)
    if repository.parent != root:
        raise ValueError("repository path is not an exact child of the managed Git root")
    match = _MANAGED_REPO_NAME.fullmatch(repository.name)
    if match is None:
        raise ValueError("repository path is not an Evander managed repository")
    return match.group(1)


def request_refresh(
    *,
    socket_path: Path,
    git_root: Path,
    repository_path: Path,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    managed_repo_id = _managed_repo_id_from_path(
        git_root=git_root,
        repository_path=repository_path,
    )
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(timeout_seconds)
        client.connect(str(socket_path))
        client.sendall(_json_line({"managed_repo_id": managed_repo_id}))
        response_file = client.makefile("rb")
        response_line = response_file.readline(_MAX_REQUEST_BYTES + 1)
    if not response_line or not response_line.endswith(b"\n"):
        raise RuntimeError("refresh broker returned an incomplete response")
    if len(response_line) > _MAX_REQUEST_BYTES:
        raise RuntimeError("refresh broker response is too large")
    response = json.loads(response_line)
    if not isinstance(response, dict):
        raise RuntimeError("refresh broker returned an invalid response")
    if not response.get("ok"):
        error = str(response.get("error") or "Managed repository upstream refresh failed.")
        category = str(response.get("error_category") or "unknown")
        raise RuntimeError(f"{error} category={category}")
    return response


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Host-owned managed Git refresh broker.")
    parser.add_argument("--log-level", default="INFO")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve_parser = subparsers.add_parser("serve", help="Run the local refresh broker.")
    serve_parser.add_argument("--runtime-dir", type=Path, default=Path("runtime"))
    serve_parser.add_argument("--socket-path", type=Path, default=DEFAULT_SOCKET_PATH)
    serve_parser.add_argument("--allowed-uid", type=int)

    refresh_parser = subparsers.add_parser(
        "refresh",
        help="Request a refresh for one already-authorized managed repository path.",
    )
    refresh_parser.add_argument("--socket-path", type=Path, default=DEFAULT_SOCKET_PATH)
    refresh_parser.add_argument("--git-root", type=Path, required=True)
    refresh_parser.add_argument("--repository-path", type=Path, required=True)
    refresh_parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if args.command == "serve":
        serve(
            runtime_dir=args.runtime_dir,
            socket_path=args.socket_path,
            allowed_uid=args.allowed_uid,
        )
        return 0
    try:
        request_refresh(
            socket_path=args.socket_path,
            git_root=args.git_root,
            repository_path=args.repository_path,
            timeout_seconds=args.timeout_seconds,
        )
    except Exception as exc:
        print(f"Managed Git refresh failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import os
from pathlib import Path

import uvicorn

from .api import create_app
from .models import DEFAULT_WORKER_ROOT


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the worker-VM container job daemon.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=11888)
    parser.add_argument("--runtime-dir", type=Path, default=Path("runtime"))
    parser.add_argument("--worker-root", type=Path, default=DEFAULT_WORKER_ROOT)
    parser.add_argument(
        "--docker-network",
        default=os.environ.get("AGENT_SANDBOX_WORKER_DOCKER_NETWORK", "none"),
    )
    args = parser.parse_args()

    uvicorn.run(
        create_app(
            runtime_dir=args.runtime_dir,
            worker_root=args.worker_root,
            docker_network=args.docker_network,
        ),
        host=args.host,
        port=args.port,
    )

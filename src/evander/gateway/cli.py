from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

from evander.bootstrap import load_runtime_gateway_config, resolve_runtime_dir
from evander.config import GatewayConfig
from evander.gateway.app import create_app
from evander.runtime import build_runtime


def _project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _bootstrap_runtime_config(*, runtime_dir: Path | None = None) -> GatewayConfig:
    return load_runtime_gateway_config(
        runtime_dir=resolve_runtime_dir(runtime_dir=runtime_dir or _project_root() / "runtime")
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Evander MCP gateway.")
    parser.add_argument("--runtime-dir", type=Path, help="Runtime directory root.")
    args = parser.parse_args()

    config = _bootstrap_runtime_config(runtime_dir=args.runtime_dir)
    context = build_runtime(config)
    app = create_app(context)
    uvicorn.run(
        app,
        host=config.host,
        port=config.port,
        log_level="info",
    )


if __name__ == "__main__":
    main()

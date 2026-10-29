"""Operator source utilities; no implicit installation, workflow saves or production actions."""

import argparse
import json
from pathlib import Path

from .toolchain import prepare_toolchain
from .workflow import build_workflow


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    bundle = commands.add_parser("prepare-toolchain")
    bundle.add_argument("--output", type=Path, required=True)
    bundle.add_argument("--playwright-cache", type=Path, required=True)
    commands.add_parser("example-workflow")
    args = parser.parse_args(argv)
    if args.command == "prepare-toolchain":
        result = prepare_toolchain(args.output, args.playwright_cache)
    else:
        result = build_workflow()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

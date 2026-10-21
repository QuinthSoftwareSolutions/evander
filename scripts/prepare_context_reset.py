#!/usr/bin/env python3
"""Prepare a local reset-workflow candidate without applying it."""

import argparse
import json
from pathlib import Path

from evander.workflow.context_reset import prepare_context_reset_pack


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pack", required=True, type=Path)
    parser.add_argument("--bindings", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.resolve() in {args.pack.resolve(), args.bindings.resolve()}:
        parser.error("--output must differ from the input files")
    candidate = prepare_context_reset_pack(
        json.loads(args.pack.read_text()), **json.loads(args.bindings.read_text())
    )
    args.output.write_text(json.dumps(candidate, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()

"""Explicit, opt-in live provider check with synthetic input and existing directory settings."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from evander.admin.composition import build_admin_runtime
from evander.classification.models import ClassificationError


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live", action="store_true", help="Allow a billable classification API request"
    )
    parser.add_argument("--runtime-dir", type=Path, default=Path("runtime"))
    parser.add_argument("--directory-path", default="root")
    parser.add_argument(
        "--provider", help="Use this enabled provider without changing directory defaults"
    )
    args = parser.parse_args()
    if not args.live:
        parser.error("--live is required; this check calls the configured provider")
    admin = build_admin_runtime(args.runtime_dir)
    try:
        result = admin.classification_admin.evaluate(
            args.directory_path,
            {
                "state": "A customer reports that the export button no longer works.",
                "provider": args.provider,
                "questions": {
                    "route": {
                        "type": "choice",
                        "instructions": "Choose the best team.",
                        "criteria": {
                            "support": "Existing product problems",
                            "sales": "Prospective purchases",
                        },
                    }
                },
            },
        )
    except ClassificationError as exc:
        print(str(exc))
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

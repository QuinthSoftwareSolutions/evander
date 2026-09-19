"""Opt-in, billable evaluation of the labeled Smart switch example corpus."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from evander.admin.composition import build_admin_runtime
from evander.classification.models import ClassificationError


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Allow one billable request per sample")
    parser.add_argument("--runtime-dir", type=Path, default=Path("runtime"))
    parser.add_argument("--directory-path", default="root")
    args = parser.parse_args()
    if not args.live:
        parser.error("--live is required; every sample calls the configured provider")
    examples = Path(__file__).resolve().parents[1] / "docs" / "examples"
    pack = json.loads((examples / "classifier-level-switch.json").read_text())
    config = next(n["config"] for n in pack["ir"]["nodes"] if n["id"] == "choose-level")
    samples = json.loads((examples / "classifier-level-samples.json").read_text())
    admin = build_admin_runtime(args.runtime_dir)
    failures = 0
    for index, sample in enumerate(samples, 1):
        try:
            result = admin.classification_admin.evaluate_switch(
                args.directory_path,
                {"config": {**config, "state": sample["state"]}},
            )
        except ClassificationError as exc:
            print(str(exc))
            return 1
        matched = result["selected_port"] == sample["expected_port"]
        failures += not matched
        print(
            json.dumps(
                {
                    "sample": index,
                    "expected_port": sample["expected_port"],
                    "matched": matched,
                    **result,
                }
            )
        )
    return int(failures > 0)


if __name__ == "__main__":
    raise SystemExit(main())

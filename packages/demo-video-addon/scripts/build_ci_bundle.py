#!/usr/bin/env python3
"""Build and independently verify a content-addressed video release for CI/CD."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import tempfile

from evander_demo_video_addon import worker
from evander_demo_video_addon.toolchain import prepare_toolchain


def build(output: Path, playwright_cache: Path, source_revision: str) -> dict:
    if not re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", source_revision):
        raise ValueError("source revision must be a full lowercase Git commit")
    output.mkdir(parents=True, exist_ok=True)
    package = Path(__file__).resolve().parents[1] / "evander_demo_video_addon"
    with tempfile.TemporaryDirectory(prefix=".video-build-", dir=output) as temporary:
        temporary_path = Path(temporary)
        release = temporary_path / "release"
        built = prepare_toolchain(release, playwright_cache)
        archive = release / "toolchain.tar.gz"
        bootstrap = release / "worker.py"
        with tempfile.TemporaryDirectory(prefix="video-verify-") as verification:
            installed = worker.install(archive, Path(verification) / "toolchain")
            current = Path(installed["toolchain"])
            for name in ("worker.py", "capture.mjs"):
                if worker.digest(current / name) != worker.digest(package / name):
                    raise ValueError(f"installed {name} does not match the reviewed source")
        if built["sha256"] != worker.digest(archive) or worker.digest(bootstrap) != worker.digest(
            package / "worker.py"
        ):
            raise ValueError("video release digest verification failed")
        metadata = {
            "schema_version": 1,
            "source_revision": source_revision,
            "bundle_sha256": built["sha256"],
            "bootstrap_sha256": worker.digest(bootstrap),
            "capture_sha256": worker.digest(package / "capture.mjs"),
            "recording_revisions": built["recording_revisions"],
        }
        (release / "release.json").write_text(
            json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
        )
        destination = output / built["sha256"]
        if destination.exists():
            existing = json.loads((destination / "release.json").read_text(encoding="utf-8"))
            if (
                existing != metadata
                or worker.digest(destination / "toolchain.tar.gz") != metadata["bundle_sha256"]
                or worker.digest(destination / "worker.py") != metadata["bootstrap_sha256"]
            ):
                raise ValueError("an existing video release does not match its content address")
        else:
            release.rename(destination)
        return {**metadata, "release_dir": str(destination)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--playwright-cache", type=Path, required=True)
    parser.add_argument("--source-revision", required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.output, args.playwright_cache, args.source_revision)))


if __name__ == "__main__":
    main()

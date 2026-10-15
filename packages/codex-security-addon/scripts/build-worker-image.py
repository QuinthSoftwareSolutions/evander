#!/usr/bin/env python3
"""Build and cache the optional scanner image from a prepared runner archive."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


PACKAGE = Path(__file__).resolve().parents[1]
REPOSITORY = PACKAGE.parents[1]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def configured_path(name: str, default: Path) -> Path:
    return Path(os.environ.get(name) or default).expanduser().resolve()


def cache_matches(archive: Path, manifest: Path, fingerprint: str) -> bool:
    try:
        data = json.loads(manifest.read_text())
        return (
            isinstance(data, dict)
            and data.get("schema") == 1
            and data.get("input_sha256") == fingerprint
            and archive.stat().st_size > 0
            and data.get("archive_sha256") == sha256(archive)
        )
    except (OSError, ValueError):
        return False


def save_image(docker: str, image: str, destination: Path, *, compressed: bool) -> None:
    if not compressed:
        subprocess.run([docker, "save", "-o", str(destination), image], check=True, timeout=900)
        return

    # Stream directly into gzip: docker save -o first materializes the full tar.
    command = [docker, "save", image]
    exporter = subprocess.Popen(command, stdout=subprocess.PIPE)
    try:
        with destination.open("wb") as output:
            subprocess.run(
                ["gzip", "-1", "-n"],
                stdin=exporter.stdout,
                stdout=output,
                check=True,
                timeout=900,
            )
        if exporter.wait(timeout=30):
            raise subprocess.CalledProcessError(exporter.returncode, command)
    finally:
        assert exporter.stdout is not None
        exporter.stdout.close()
        if exporter.poll() is None:
            exporter.kill()
        exporter.wait(timeout=30)

    with gzip.open(destination, "rb") as source:
        if not source.read(1):
            raise ValueError("Docker returned an empty scanner image archive")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cache-key", action="store_true", help="Print inputs' hash without building"
    )
    parser.add_argument("--force", action="store_true", help="Rebuild even with a verified cache")
    args = parser.parse_args()
    node = os.environ.get("HOST_NODE_BINARY") or shutil.which("node")
    if not node:
        parser.error("Node is required; set HOST_NODE_BINARY or put node on PATH")
    base = configured_path("SECURITY_SCAN_BASE_ARCHIVE", REPOSITORY / "dist/evander-runner.tar")
    dockerfile = configured_path("SECURITY_SCAN_DOCKERFILE", PACKAGE / "examples/Dockerfile")
    archive = configured_path(
        "SECURITY_SCAN_ARCHIVE_PATH", REPOSITORY / "dist/evander-security-scan.tar"
    )
    compressed = archive.suffix in {".gz", ".tgz"}
    manifest = Path(str(archive) + ".manifest")
    image = os.environ.get("SECURITY_SCAN_IMAGE_NAME", "evander-security-scan:latest")
    runner_image = os.environ.get("SECURITY_SCAN_BASE_IMAGE", "evander-runner:latest")
    configure_override = os.environ.get("SECURITY_SCAN_CONFIGURE_PATH")
    inputs = {
        "base_archive": base,
        "dockerfile": dockerfile,
        "node": Path(node).resolve(),
        "package": PACKAGE / "package.json",
        "lock": PACKAGE / "package-lock.json",
        "runtime_builder": PACKAGE / "scripts/build-runtime.sh",
        "image_builder": Path(__file__).resolve(),
        **{
            name: PACKAGE / "evander_codex_security_addon" / name
            for name in ("runner.py", "codex_bridge.py", "configure.mjs")
        },
    }
    if configure_override:
        inputs["configure_override"] = Path(configure_override).expanduser().resolve()
    try:
        identity = {
            "files": {name: sha256(path) for name, path in inputs.items()},
            "image": image,
            "runner_image": runner_image,
            "archive_format": "tar.gz" if compressed else "tar",
        }
    except OSError as error:
        parser.error(f"Missing or unreadable scanner image input: {error}")
    fingerprint = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    if args.cache_key:
        print(fingerprint)
        return
    force = args.force or os.environ.get("RUNNER_FORCE_REBUILD", "").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if not force and cache_matches(archive, manifest, fingerprint):
        print(f"Reused cached {image} archive at {archive}")
        return

    docker = os.environ.get("DOCKER", "docker")
    with tempfile.TemporaryDirectory(prefix="evander-scanner-image-") as directory:
        context = Path(directory)
        runtime = context / "runtime"
        subprocess.run(
            ["bash", str(PACKAGE / "scripts/build-runtime.sh"), str(runtime)],
            env={**os.environ, "HOST_NODE_BINARY": str(inputs["node"])},
            check=True,
            timeout=600,
        )
        # build-runtime produces an archive; stage only its preserved runtime files.
        bundle = context / "bundle"
        bundle.mkdir()
        subprocess.run(
            ["tar", "-xzf", str(runtime / "codex-security-runtime.tar.gz"), "-C", str(bundle)],
            check=True,
            timeout=120,
        )
        shutil.rmtree(runtime)
        bundle.rename(runtime)
        if configure_override:
            (runtime / "configure.mjs").rename(runtime / "configure-stock.mjs")
            shutil.copyfile(inputs["configure_override"], runtime / "configure.mjs")
            (runtime / "configure.mjs").chmod(0o644)
        shutil.copyfile(dockerfile, context / "Dockerfile")
        subprocess.run([docker, "load", "--input", str(base)], check=True, timeout=600)
        subprocess.run(
            [
                docker,
                "build",
                "--pull=false",
                "--network",
                "none",
                "--build-arg",
                f"RUNNER_IMAGE={runner_image}",
                "-t",
                image,
                str(context),
            ],
            check=True,
            timeout=900,
        )
    # Release the extracted runtime and Docker build context before exporting.
    archive.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".scanner-archive-", dir=archive.parent) as output:
        pending_archive = Path(output) / ("image.tar.gz" if compressed else "image.tar")
        pending_manifest = Path(output) / "image.manifest"
        save_image(docker, image, pending_archive, compressed=compressed)
        if not pending_archive.stat().st_size:
            raise ValueError("Docker returned an empty scanner image archive")
        pending_manifest.write_text(
            json.dumps(
                {
                    "schema": 1,
                    "input_sha256": fingerprint,
                    "archive_sha256": sha256(pending_archive),
                    "inputs": identity,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )
        pending_archive.replace(archive)
        pending_manifest.replace(manifest)
    print(f"Built {image}; saved {archive}")


if __name__ == "__main__":
    main()

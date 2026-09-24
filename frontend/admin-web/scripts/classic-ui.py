"""Temporary frozen UI packaging. Remove with the Classic rollout (see classic/README.md)."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import zipfile

PROJECT = Path(__file__).resolve().parents[1]
REPO = PROJECT.parents[1]
FROZEN = PROJECT / "classic"
ADDONS = ("team", "bokio", "figma", "miro", "mssql_seed", "skatteverket")


def freeze(revision: str) -> None:
    commit = subprocess.check_output(
        ["git", "rev-parse", f"{revision}^{{commit}}"], cwd=REPO, text=True
    ).strip()
    with tempfile.TemporaryDirectory(prefix="evander-classic-") as temporary:
        root = Path(temporary)
        paths = ["frontend/admin-web", *(f"src/evander_{name}_addon/ui_static" for name in ADDONS)]
        archive = subprocess.check_output(["git", "archive", commit, *paths], cwd=REPO)
        with tarfile.open(fileobj=io.BytesIO(archive)) as source:
            source.extractall(root, filter="data")
        project = root / "frontend/admin-web"
        (project / "node_modules").symlink_to(PROJECT / "node_modules", target_is_directory=True)
        # The only adaptation to the pinned UI: embedded built-ins use their frozen assets.
        entry = project / "src/AdminRoot.tsx"
        text = entry.read_text()
        anchor = '    url.searchParams.set("directory_path", directoryPath);'
        assert text.count(anchor) == 1
        names = "|".join(name.replace("_", "-") + "-addon" for name in ADDONS)
        text = text.replace(
            anchor,
            f"""    if (url.origin === base && /^\\/addons\\/({names})\\/?$/.test(url.pathname)) {{
      url.pathname = url.pathname.replace(/\\/?$/, "/classic/");
    }}
    url.searchParams.set("admin_ui", "classic");
{anchor}""",
        )
        entry.write_text(text)
        subprocess.run(
            [str(project / "node_modules/.bin/vite"), "build", "--base", "/classic/"],
            cwd=project,
            check=True,
            timeout=120,
        )
        FROZEN.mkdir(exist_ok=True)
        files: dict[str, bytes] = {}
        for file in sorted((project / "dist").rglob("*")):
            if file.is_file():
                files[f"host/{file.relative_to(project / 'dist').as_posix()}"] = file.read_bytes()
        for name in ADDONS:
            for file in sorted((root / f"src/evander_{name}_addon/ui_static").rglob("*")):
                if file.is_file():
                    files[f"addons/{name.replace('_', '-')}-addon/{file.name}"] = file.read_bytes()
        with zipfile.ZipFile(
            FROZEN / "assets.zip", "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as output:
            for name, contents in files.items():
                info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                output.writestr(info, contents)
        manifest = {
            "revision": commit,
            "adaptation": "Built-in add-on iframe URLs use /classic/; application behavior is frozen.",
            "archive_sha256": hashlib.sha256((FROZEN / "assets.zip").read_bytes()).hexdigest(),
            "files": {
                name: hashlib.sha256(contents).hexdigest() for name, contents in files.items()
            },
        }
        (FROZEN / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        print(f"Frozen Classic from {commit}: {len(files)} files")


def install() -> None:
    manifest = json.loads((FROZEN / "manifest.json").read_text())
    archive = (FROZEN / "assets.zip").read_bytes()
    if hashlib.sha256(archive).hexdigest() != manifest["archive_sha256"]:
        raise ValueError("Classic archive checksum mismatch")
    destinations = {"host": PROJECT / "dist/classic", "addons": PROJECT / ".classic-addons"}
    with zipfile.ZipFile(io.BytesIO(archive)) as source:
        if set(source.namelist()) != set(manifest["files"]):
            raise ValueError("Classic archive inventory mismatch")
        verified = []
        for name, expected in manifest["files"].items():
            path = Path(name)
            if path.is_absolute() or ".." in path.parts or path.parts[0] not in destinations:
                raise ValueError(f"Invalid Classic asset path: {name}")
            contents = source.read(name)
            if hashlib.sha256(contents).hexdigest() != expected:
                raise ValueError(f"Classic asset checksum mismatch: {name}")
            verified.append((destinations[path.parts[0]] / Path(*path.parts[1:]), contents))
        for destination in destinations.values():
            shutil.rmtree(destination, ignore_errors=True)
        for destination, contents in verified:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(contents)
    (PROJECT / "dist/ui-rollout.json").write_text(
        json.dumps(
            {
                "classic_revision": manifest["revision"],
                "required_files": [file.relative_to(PROJECT).as_posix() for file, _ in verified]
                + ["dist/ui-rollout/switcher.js", "dist/ui-rollout/switcher.css"],
            }
        )
        + "\n"
    )
    print(f"Installed frozen Classic assets from {manifest['revision']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("freeze", "install"))
    parser.add_argument("--revision")
    arguments = parser.parse_args()
    if arguments.command == "freeze":
        if not arguments.revision:
            parser.error("freeze requires an explicit --revision")
        freeze(arguments.revision)
    else:
        install()

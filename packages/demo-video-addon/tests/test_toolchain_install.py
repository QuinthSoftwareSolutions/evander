from __future__ import annotations

import gzip
import hashlib
from io import BytesIO
import json
from pathlib import Path
import platform
import tarfile
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from evander_demo_video_addon import worker


def make_bundle(output):
    output.mkdir(parents=True)
    files = {"worker.py": b"original worker", "capture.mjs": b"original recorder"}
    inventory = {
        "schema_version": 1,
        "architecture": platform.machine(),
        "sha256": {name: hashlib.sha256(data).hexdigest() for name, data in files.items()},
    }
    files["bundle.json"] = json.dumps(inventory).encode()
    archive = output / "toolchain.tar.gz"
    with archive.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w") as tar:
                for name, data in files.items():
                    item = tarfile.TarInfo(name)
                    item.size = len(data)
                    tar.addfile(item, BytesIO(data))
    return archive


@pytest.mark.parametrize("tampering", ["file_and_inventory", "extra_file", "symlink"])
def test_reinstall_rejects_changes_to_the_installed_bundle(tmp_path, monkeypatch, tampering):
    source = tmp_path / "source"
    make_bundle(source)
    monkeypatch.setattr(worker, "preflight", lambda path=None: {"success": True})
    root = tmp_path / "installed"
    result = worker.install(source / "toolchain.tar.gz", root)
    installed = root / "toolchains" / result["sha256"]
    if tampering == "file_and_inventory":
        (installed / "worker.py").write_text("modified worker")
        manifest = json.loads((installed / "bundle.json").read_text())
        manifest["sha256"]["worker.py"] = worker.digest(installed / "worker.py")
        (installed / "bundle.json").write_text(json.dumps(manifest))
    elif tampering == "extra_file":
        (installed / "extra.py").write_text("unexpected")
    else:
        relocated = root / "relocated"
        installed.rename(relocated)
        installed.symlink_to(relocated, target_is_directory=True)

    with pytest.raises(worker.VideoError, match="Installed toolchain"):
        worker.install(source / "toolchain.tar.gz", root)


def test_concurrent_installs_of_the_same_bundle_both_complete_safely(tmp_path, monkeypatch):
    source = tmp_path / "source"
    make_bundle(source)
    barrier = Barrier(2)

    def preflight(path=None):
        if path is not None and Path(path).name == "bundle":
            barrier.wait(timeout=10)
        return {"success": True}

    monkeypatch.setattr(worker, "preflight", preflight)
    destination = tmp_path / "installed"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda _: worker.install(source / "toolchain.tar.gz", destination), range(2))
        )
    assert all(result["success"] for result in results)
    assert results[0]["sha256"] == results[1]["sha256"]
    assert (destination / "current").resolve() == (
        destination / "toolchains" / results[0]["sha256"]
    ).resolve()

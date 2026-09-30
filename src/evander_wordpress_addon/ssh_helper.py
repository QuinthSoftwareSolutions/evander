#!/usr/bin/env python3
"""Install as a forced SSH command; reads only the operator's fixed component roots.

No shell, PHP execution, arbitrary paths, URL fetching or agent-selected commands.
Use an isolated pipeline to build/lint/test before supplying an artifact here.
"""

from __future__ import annotations

import base64
import fcntl
from hashlib import sha256
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile

LIMIT = 100 * 1024**2


def tree_hash(root):
    digest = sha256()
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("release contains a symlink")
        if path.is_file():
            digest.update(str(path.relative_to(root)).encode() + b"\0")
            digest.update(sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def atomic_json(path, payload):
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".state-")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(payload, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        fsync(path.parent)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def fsync(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class Helper:
    def __init__(self, config):
        self.config = config

    def component(self, name):
        raw = self.config["components"].get(name)
        if not raw:
            raise ValueError("component is not approved")
        root = Path(raw)
        if not root.is_absolute() or root.is_symlink() or root.resolve() != root:
            raise ValueError("component root must be a real absolute directory")
        if not root.is_dir():
            raise ValueError("component root does not exist")
        return root

    def activate(self):
        command = self.config.get("post_switch_command")
        if command is None:
            return
        if (
            not isinstance(command, list)
            or not 1 <= len(command) <= 16
            or any(
                not isinstance(value, str) or not value or any(ord(c) < 32 for c in value)
                for value in command
            )
            or not Path(command[0]).is_absolute()
        ):
            raise ValueError("invalid operator activation command")
        # Fixed operator configuration only: never caller arguments or a shell.
        subprocess.run(command, check=True, timeout=30, capture_output=True)

    def current(self, root):
        live = root / "current"
        if not live.is_symlink():
            raise ValueError("current must be an operator-provisioned release symlink")
        release = live.resolve(strict=True)
        if release.parent != root / "releases" or not release.is_dir():
            raise ValueError("current release is outside the fixed release root")
        return release.name + "@" + tree_hash(release)

    def switch(self, root, version):
        name, expected = version.split("@", 1)
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", name):
            raise ValueError("invalid release ID")
        target = root / "releases" / name
        if target.is_symlink() or tree_hash(target) != expected:
            raise ValueError("retained release was modified")
        temporary = root / ".next"
        if temporary.exists() or temporary.is_symlink():
            temporary.unlink()
        os.symlink("releases/" + name, temporary)
        os.replace(temporary, root / "current")
        fsync(root)

    def install(self, root, payload):
        archive = base64.b64decode(payload["artifact_archive"], validate=True)
        if len(archive) > LIMIT or sha256(archive).hexdigest() != payload["artifact_sha256"]:
            raise ValueError("artifact digest mismatch")
        release = root / "releases" / payload["artifact_sha256"]
        if not re.fullmatch(r"[a-f0-9]{64}", release.name):
            raise ValueError("invalid artifact digest")
        temporary = Path(tempfile.mkdtemp(prefix=".release-", dir=root))
        try:
            with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
                infos = bundle.infolist()
                if len(infos) > 10000 or sum(x.file_size for x in infos) > LIMIT:
                    raise ValueError("artifact expands beyond limit")
                seen = set()
                for info in infos:
                    path = PurePosixPath(info.filename)
                    if (
                        path.is_absolute()
                        or ".." in path.parts
                        or "\\" in info.filename
                        or "\0" in info.filename
                        or path in seen
                        or not path.parts
                        or stat.S_ISLNK(info.external_attr >> 16)
                    ):
                        raise ValueError("unsafe artifact path")
                    seen.add(path)
                    target = temporary.joinpath(*path.parts)
                    if info.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                    else:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with target.open("xb") as stream:
                            stream.write(bundle.read(info))
                            stream.flush()
                            os.fsync(stream.fileno())
                        target.chmod(0o644)
            version = release.name + "@" + tree_hash(temporary)
            if release.exists():
                if release.is_symlink() or tree_hash(release) != tree_hash(temporary):
                    raise ValueError("immutable release already exists with different content")
            else:
                os.rename(temporary, release)
                release.chmod(0o755)
                fsync(release.parent)
            return version
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)

    def execute(self, payload):
        if payload.get("protocol_version") != 1 or payload.get("action") not in {
            "prepare",
            "status",
            "deploy",
            "restore",
            "probe",
        }:
            raise ValueError("unsupported protocol operation")
        root = self.component(payload.get("component"))
        operation_id = payload.get("operation_id", "probe")
        if not re.fullmatch(r"[a-zA-Z0-9._:-]{1,160}", operation_id):
            raise ValueError("invalid operation ID")
        state_dir = root / "state"
        state_dir.mkdir(mode=0o700, exist_ok=True)
        if state_dir.is_symlink() or state_dir.stat().st_mode & 0o077:
            raise ValueError("state directory must be private")
        fd = os.open(state_dir / "lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            path = state_dir / (sha256(operation_id.encode()).hexdigest() + ".json")
            state = json.loads(path.read_text()) if path.exists() else {"state": "not_found"}
            current = self.current(root)
            action = payload["action"]
            if state["state"] == "deploying":
                if current == state["release_version"]:
                    # Undo must not depend on activating the broken candidate first.
                    if action != "restore":
                        try:
                            self.activate()
                        except Exception:
                            if action != "status":
                                raise
                            return {"protocol_version": 1, **state, "current_release": current}
                        state["state"] = "deployed"
                elif current == state["expected_release"]:
                    state["state"] = "prepared"
                else:
                    return {"protocol_version": 1, "conflict": True}
                atomic_json(path, state)
            if state["state"] == "restoring" and current == state["previous_release"]:
                try:
                    self.activate()
                except Exception:
                    if action != "status":
                        raise
                    return {"protocol_version": 1, **state, "current_release": current}
                state["state"] = "restored"
                atomic_json(path, state)
            if action == "probe":
                # Exercise the real atomic switch against the current release, without changing its contents.
                self.switch(root, current)
                self.activate()
                return {"protocol_version": 1, "restore_verified": self.current(root) == current}
            if action == "status":
                return {"protocol_version": 1, **state, "current_release": current}
            if action == "prepare":
                if state["state"] == "not_found":
                    state = {
                        "state": "prepared",
                        "previous_release": current,
                        "expected_release": current,
                        "artifact_sha256": payload.get("artifact_sha256"),
                        "commit_sha": payload.get("commit_sha"),
                    }
                    atomic_json(path, state)
                elif (
                    state.get("commit_sha") and state["commit_sha"] != payload.get("commit_sha")
                ) or (
                    payload.get("artifact_sha256")
                    and state.get("artifact_sha256")
                    and state["artifact_sha256"] != payload["artifact_sha256"]
                ):
                    return {"protocol_version": 1, "conflict": True}
                if payload.get("artifact_sha256") and not state.get("artifact_sha256"):
                    if state["state"] != "prepared":
                        return {"protocol_version": 1, "conflict": True}
                    state["artifact_sha256"] = payload["artifact_sha256"]
                    atomic_json(path, state)
                return {"protocol_version": 1, **state}
            fence_path = state_dir / "fence.json"
            fence = json.loads(fence_path.read_text())["fence"] if fence_path.exists() else 0
            if type(payload.get("fence")) is not int or payload["fence"] < fence:
                return {"protocol_version": 1, "conflict": True}
            atomic_json(fence_path, {"fence": payload["fence"]})
            if action == "deploy":
                if (
                    not state.get("artifact_sha256")
                    or state["artifact_sha256"] != payload.get("artifact_sha256")
                    or (
                        state.get("commit_sha") and state["commit_sha"] != payload.get("commit_sha")
                    )
                ):
                    return {"protocol_version": 1, "conflict": True}
                if state["state"] == "deployed":
                    return {"protocol_version": 1, **state}
                if state["state"] == "restored" or current != payload["expected_release"]:
                    return {"protocol_version": 1, "conflict": True}
                version = self.install(root, payload)
                # Intent is durable before switching. A lost SSH reply/crash is reconciled by current.
                state.update(
                    state="deploying", release_version=version, release_id=version.split("@")[0]
                )
                atomic_json(path, state)
                self.switch(root, version)
                self.activate()
                state["state"] = "deployed"
            else:
                if state["state"] == "restored" or current == state.get("previous_release"):
                    state["state"] = "restored"
                elif current != payload["expected_release"]:
                    return {"protocol_version": 1, "conflict": True}
                else:
                    state["state"] = "restoring"
                    atomic_json(path, state)
                    self.switch(root, state["previous_release"])
                    self.activate()
                    state["state"] = "restored"
            atomic_json(path, state)
            return {"protocol_version": 1, **state}
        finally:
            os.close(fd)


def main():
    try:
        config_path = Path("/etc/evander/wordpress-release.json")
        if (
            config_path.is_symlink()
            or config_path.stat().st_uid not in {0, os.getuid()}
            or config_path.stat().st_mode & 0o077
        ):
            raise ValueError("unsafe helper configuration")
        raw = sys.stdin.buffer.read(140 * 1024**2 + 1)
        if len(raw) > 140 * 1024**2:
            raise ValueError("request exceeds limit")
        result = Helper(json.loads(config_path.read_text())).execute(json.loads(raw))
    except Exception:
        result = {"protocol_version": 1, "error": "Helper operation failed"}
    print(json.dumps(result))


if __name__ == "__main__":
    main()

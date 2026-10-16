"""Private file delivery and cleanup, including interrupted/legacy delivery."""

import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import uuid

import pytest

from evander.resources.projection import PREPARE, PROJECT, SEAL, UNPROJECT


def helper(script, *arguments, payload=None):
    result = subprocess.run(
        [sys.executable, "-c", script, *map(str, arguments)],
        input=json.dumps(payload).encode() if payload else None,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr.decode()


@pytest.mark.parametrize("interrupted", [False, True])
def test_private_projection_cleanup_and_reuse(tmp_path, interrupted):
    base, lease = tmp_path / "provider", "rs-" + "d" * 32
    temporary = Path("/tmp/evander-resource-" + uuid.uuid4().hex)
    configuration = base / lease / "connection.json"
    try:
        helper(PREPARE, base, lease)
        assert stat.S_IMODE(configuration.parent.stat().st_mode) == 0o711
        helper(
            PROJECT,
            base,
            os.geteuid(),
            temporary,
            payload={
                "lease_id": lease,
                "connections": {"app": {"password": "synthetic-secret"}},
            },
        )
        assert not configuration.exists()
        assert temporary.stat().st_mode & 0o777 == 0o600
        if not interrupted:
            helper(SEAL, base, lease, os.geteuid(), os.getegid(), temporary)
            assert configuration.stat().st_mode & 0o777 == 0o600
            assert json.loads(configuration.read_text())["app"]["password"] == "synthetic-secret"
        helper(UNPROJECT, base, lease, temporary)
        helper(UNPROJECT, base, lease, temporary)
        assert not configuration.parent.exists() and not temporary.exists()
        helper(PREPARE, base, lease)
        assert configuration.parent.is_dir()
    finally:
        temporary.unlink(missing_ok=True)


def test_legacy_job_junk_and_symlinks_do_not_block_credential_removal(tmp_path):
    base, lease = tmp_path / "provider", "rs-" + "e" * 32
    leaf = base / lease
    helper(PREPARE, base, lease)
    leaf.chmod(0o733)  # The previous job-writable delivery protocol.
    (leaf / "connection.json").write_text("private-credential")
    (leaf / "connection.new").write_text("private-credential")
    outside = tmp_path / "unrelated"
    outside.mkdir()
    (outside / "keep").write_text("not-managed")
    (leaf / "link").symlink_to(outside, target_is_directory=True)
    blocked = leaf / "extra" / "unreadable"
    blocked.mkdir(parents=True)
    (blocked / "junk").write_text("job-owned")
    blocked.chmod(0)
    try:
        helper(UNPROJECT, base, lease)
        assert not leaf.exists()
        assert (outside / "keep").read_text() == "not-managed"
        assert not list(base.rglob("connection.json"))
        assert not list(base.rglob("connection.new"))
        helper(UNPROJECT, base, lease)
        helper(PREPARE, base, lease)
    finally:
        # Retired junk is inaccessible until the disposable tmpfs disappears.
        # Restore just the test's mode-000 directory so pytest can remove its tmpdir.
        for directory in base.rglob("unreadable"):
            directory.chmod(0o700)


def test_staging_symlink_is_rejected_and_cleanup_never_follows_it(tmp_path):
    base, lease = tmp_path / "provider", "rs-" + "f" * 32
    helper(PREPARE, base, lease)
    target = tmp_path / "unrelated"
    target.write_text("not-managed")
    temporary = Path("/tmp/evander-resource-" + uuid.uuid4().hex)
    try:
        temporary.symlink_to(target)
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                SEAL,
                str(base),
                lease,
                str(os.geteuid()),
                str(os.getegid()),
                str(temporary),
            ],
            capture_output=True,
            timeout=10,
        )
        assert result.returncode != 0 and not (base / lease / "connection.json").exists()
        helper(UNPROJECT, base, lease, temporary)
        assert not temporary.is_symlink() and target.read_text() == "not-managed"
    finally:
        temporary.unlink(missing_ok=True)

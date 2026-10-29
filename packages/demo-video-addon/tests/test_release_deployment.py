from __future__ import annotations

import grp
import hashlib
import json
import os
from pathlib import Path
import pwd
import shutil
import subprocess

import pytest


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_pipeline_bundle_publication_is_idempotent_and_refuses_changed_artifacts(tmp_path):
    ansible = shutil.which("ansible-playbook")
    if not ansible:
        pytest.skip("ansible-playbook is not installed")
    package = Path(__file__).parents[1]
    source = tmp_path / "source"
    source.mkdir()
    contents = {
        "toolchain.tar.gz": b"synthetic verified archive",
        "worker.py": b"synthetic reviewed bootstrap",
        "release.json": b'{"schema_version":1}\n',
    }
    for name, data in contents.items():
        (source / name).write_bytes(data)
    bundle_sha = sha(contents["toolchain.tar.gz"])
    release_root = tmp_path / "published"
    release_parent = release_root / "releases"
    release_parent.mkdir(parents=True, mode=0o711)
    release_parent.chmod(0o711)
    play = [
        {
            "hosts": "localhost",
            "gather_facts": False,
            "vars": {
                "evander_demo_video_bundle_source_dir": str(source),
                "evander_demo_video_bundle_root": str(release_root),
                "evander_demo_video_bundle_sha256": bundle_sha,
                "evander_demo_video_bootstrap_sha256": sha(contents["worker.py"]),
                "evander_demo_video_release_metadata_sha256": sha(contents["release.json"]),
                "evander_run_user": pwd.getpwuid(os.getuid()).pw_name,
                "evander_run_group": grp.getgrgid(os.getgid()).gr_name,
            },
            "tasks": [
                {"ansible.builtin.include_tasks": str(package / "deployment/install-bundle.yml")}
            ],
        }
    ]
    playbook = tmp_path / "play.json"
    playbook.write_text(json.dumps(play))
    env = {
        **os.environ,
        "ANSIBLE_LOCAL_TEMP": str(tmp_path / "ansible-local"),
        "ANSIBLE_REMOTE_TMP": str(tmp_path / "ansible-remote"),
    }

    def run():
        return subprocess.run(
            [ansible, "-i", "localhost,", "-c", "local", str(playbook)],
            capture_output=True,
            text=True,
            timeout=45,
            env=env,
        )

    first = run()
    assert first.returncode == 0, first.stdout + first.stderr
    assert release_parent.stat().st_mode & 0o777 == 0o711
    release = release_root / "releases" / bundle_sha
    assert {name: (release / name).read_bytes() for name in contents} == contents
    second = run()
    assert second.returncode == 0, second.stdout + second.stderr
    (release / "worker.py").write_bytes(b"unexpected replacement")
    rejected = run()
    assert rejected.returncode != 0
    assert (release / "worker.py").read_bytes() == b"unexpected replacement"

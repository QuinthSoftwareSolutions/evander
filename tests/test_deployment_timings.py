from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("fail", [False, True])
@pytest.mark.parametrize("writable_report", [False, True])
def test_timings_preserve_playbook_outcome_and_redact_private_results(
    tmp_path: Path, fail: bool, writable_report: bool
) -> None:
    ansible = shutil.which("ansible-playbook")
    if ansible is None:
        pytest.skip("ansible-playbook is required for the local callback integration test")
    role = tmp_path / "roles" / "example" / "tasks"
    role.mkdir(parents=True)
    (role / "main.yml").write_text(
        yaml.safe_dump(
            [
                {"name": "Public task", "ansible.builtin.debug": {"msg": "ready"}},
                {
                    "name": "Skipped task",
                    "ansible.builtin.debug": {"msg": "unused"},
                    "when": False,
                },
                {
                    "name": "Private task",
                    "ansible.builtin.debug": {"msg": "private-value-not-for-timing-artifacts"},
                    "no_log": True,
                    "failed_when": fail,
                },
            ]
        ),
        encoding="utf-8",
    )
    playbook = tmp_path / "test.yml"
    playbook.write_text(
        yaml.safe_dump(
            [
                {
                    "hosts": "localhost",
                    "gather_facts": False,
                    "roles": ["example"],
                }
            ]
        ),
        encoding="utf-8",
    )
    report_path = tmp_path / "timings" / "report.json"
    config = tmp_path / "ansible.cfg"
    config.write_text("[defaults]\n", encoding="utf-8")
    if not writable_report:
        report_path.parent.write_text("not a directory", encoding="utf-8")
    completed = subprocess.run(
        [ansible, "-i", "localhost,", "-c", "local", str(playbook)],
        cwd=tmp_path,
        env={
            **os.environ,
            "ANSIBLE_LOCAL_TEMP": str(tmp_path / "ansible-local"),
            "ANSIBLE_CONFIG": str(config),
            "ANSIBLE_NOCOLOR": "1",
            "ANSIBLE_CALLBACK_PLUGINS": str(ROOT / "ansible/callback_plugins"),
            "ANSIBLE_CALLBACKS_ENABLED": "deployment_timings",
            "EVANDER_DEPLOYMENT_TIMINGS_FILE": str(report_path),
        },
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == (2 if fail else 0), completed.stderr
    assert "Deployment timings (seconds)" in completed.stdout
    assert "private-value-not-for-timing-artifacts" not in completed.stdout + completed.stderr
    if not writable_report:
        assert "Deployment timing report could not be written" in completed.stderr
        return
    raw = report_path.read_text(encoding="utf-8")
    assert "private-value-not-for-timing-artifacts" not in raw
    assert "Private task" not in raw
    report = json.loads(raw)
    assert report["schema_version"] == 1
    assert report["elapsed_seconds"] > 0
    assert set(report["roles"]) == {"example"}
    assert len(report["tasks"]) == 3
    assert [task["outcomes"] for task in report["tasks"]] == [
        {"ok": 1},
        {"skipped": 1},
        {"failed" if fail else "ok": 1},
    ]
    assert all(task["elapsed_seconds"] >= 0 for task in report["tasks"])

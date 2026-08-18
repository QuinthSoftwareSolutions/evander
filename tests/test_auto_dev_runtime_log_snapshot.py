from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

from evander.daemons.effect_orchestrator import EffectExecutionRecord, SqliteExecutionStore


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "auto_dev_runtime_log_snapshot.sh"


def test_auto_dev_runtime_log_snapshot_is_executable_and_shell_valid() -> None:
    mode = SCRIPT.stat().st_mode

    assert mode & stat.S_IXUSR
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


def test_auto_dev_runtime_log_snapshot_redacts_secret_shapes(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    runtime_dir = project_dir / "runtime"
    runtime_dir.mkdir(parents=True)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    snapshot_path = tmp_path / "snapshot.log"

    _write_executable(fake_bin / "systemctl", "printf '%s\\n' 'no failed units'\n")
    _write_executable(
        fake_bin / "journalctl",
        "printf '%s\\n' 'Authorization: Bearer journal.secret.token'\n",
    )
    _write_executable(
        fake_bin / "jq",
        'printf \'%s\\n\' \'{"snapshot_path":"test","redaction":"enabled"}\'\n',
    )

    (runtime_dir / "audit.jsonl").write_text(
        "\n".join(
            [
                (
                    '{"decision":"error","args":{'
                    '"OPENAI_API_KEY":"example-openai-api-key",'
                    '"Authorization":"Bearer abc.def.ghi",'
                    '"password":"hunter2",'
                    '"url":"https://user:pass@example.com/path?sig=secret",'
                    '"jwt":"eyJ0123456789.eyJ0123456789.abcdef0123456789"'
                    '},"error_class":"boom"}'
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    SqliteExecutionStore(runtime_dir / "orchestration.db").record_execution(
        EffectExecutionRecord(
            execution_id="execution-failed",
            idempotency_key="execution-failed",
            event_id="event-failed",
            trace_id="trace-failed",
            source_node_id="trigger-test",
            source_trigger_type="trigger:test",
            effect_id="effect-test",
            effect_type="effect:test",
            status="failed",
            started_at="2026-08-18T10:00:00Z",
            finished_at="2026-08-18T10:00:01Z",
            resolved_config_hash="fixture",
            resolved_config={"access_token": "raw-access-token"},
            error="connstr=Server=tcp:db;Password=swordfish;",
        )
    )

    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "AUTO_DEV_LOG_SINCE": "1 minute ago",
        "AUTO_DEV_LOG_OUTPUT": str(snapshot_path),
        "EVANDER_PYTHON": sys.executable,
    }

    result = subprocess.run(
        ["bash", str(SCRIPT), str(project_dir)],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    combined = result.stdout + snapshot_path.read_text(encoding="utf-8")
    assert "redaction=enabled" in combined
    assert '"redaction":"enabled"' in combined
    assert "<redacted>" in combined
    for secret in (
        "example-openai-api-key",
        "Bearer abc.def.ghi",
        "journal.secret.token",
        "hunter2",
        "user:pass",
        "sig=secret",
        "raw-access-token",
        "swordfish",
        "eyJ0123456789.eyJ0123456789.abcdef0123456789",
    ):
        assert secret not in combined


def _write_executable(path: Path, body: str) -> None:
    path.write_text(f"#!/usr/bin/env bash\n{body}", encoding="utf-8")
    path.chmod(0o755)

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_sandbox.config import GatewayConfig
from agent_sandbox.effects.providers.base import EffectRuntimeContext
from agent_sandbox.effects.providers.runtime_diagnostics_snapshot import (
    RuntimeDiagnosticsSnapshotEffectProvider,
)
from agent_sandbox.events.provider_models import RuntimeDiagnosticsSnapshotEffectDefinition


def _runtime_context(tmp_path: Path) -> EffectRuntimeContext:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    return EffectRuntimeContext(
        resolved_config=GatewayConfig(runtime_dir=runtime_dir),
        admin_service=SimpleNamespace(),
        env={},
    )


def _effect_definition(**overrides: object) -> RuntimeDiagnosticsSnapshotEffectDefinition:
    payload: dict[str, object] = {
        "id": "effect-diagnostics",
        "display_name": "Runtime diagnostics snapshot",
        "type": "effect:runtime:diagnostics-snapshot",
    }
    payload.update(overrides)
    return RuntimeDiagnosticsSnapshotEffectDefinition.model_validate(payload)


def test_runtime_diagnostics_snapshot_reads_bounded_sources_and_redacts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    context = _runtime_context(tmp_path)
    runtime_dir = context.config_path("runtime_dir")
    (runtime_dir / "audit.jsonl").write_text(
        '{"decision": "denied", "api_key": "audit-secret", "safe": "visible"}\n',
        encoding="utf-8",
    )
    (runtime_dir / "effect-orchestrator-executions.json").write_text(
        '{"success": false, "error": "password=execution-secret"}\n',
        encoding="utf-8",
    )
    (runtime_dir / "gate-orchestrator-executions.json").write_text(
        '{"status": "succeeded"}\n',
        encoding="utf-8",
    )

    calls: list[list[str]] = []

    def fake_run(
        args: list[str],
        *,
        capture_output: bool,
        text: bool,
        timeout: float,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        assert capture_output is True
        assert text is True
        assert timeout == 3.0
        assert check is False
        if args[0] == "systemctl":
            return subprocess.CompletedProcess(
                args,
                0,
                stdout="UNIT LOAD ACTIVE SUB DESCRIPTION\nAuthorization: Bearer system-secret\n",
                stderr="",
            )
        return subprocess.CompletedProcess(
            args,
            0,
            stdout='warning token="journal-secret"\nSet-Cookie: session=journal-cookie\n',
            stderr="",
        )

    monkeypatch.setattr(
        "agent_sandbox.effects.providers.runtime_diagnostics_snapshot.subprocess.run",
        fake_run,
    )

    provider = RuntimeDiagnosticsSnapshotEffectProvider()
    provider.initialize(
        _effect_definition(
            units=["agent-sandbox-admin-web.service"],
            command_timeout_seconds=3,
        ),
        context,
    )

    result = provider.execute()

    snapshot = result["snapshot_text"]
    assert result["success"] is True
    assert result["action"] == "runtime_diagnostics_snapshot"
    assert result["stdout"] == snapshot
    assert result["message"] == snapshot
    assert result["redaction"] == "enabled"
    assert result["units"] == ["agent-sandbox-admin-web.service"]
    assert "# Runtime diagnostics snapshot" in snapshot
    assert "## Failed systemd units" in snapshot
    assert "### agent-sandbox-admin-web.service" in snapshot
    assert "## Runtime audit errors and denials" in snapshot
    assert "visible" in snapshot
    assert "system-secret" not in snapshot
    assert "journal-secret" not in snapshot
    assert "journal-cookie" not in snapshot
    assert "audit-secret" not in snapshot
    assert "execution-secret" not in snapshot
    assert "Bearer <redacted>" in snapshot
    assert "<redacted>" in snapshot
    assert calls == [
        ["systemctl", "--no-pager", "--failed"],
        [
            "journalctl",
            "--no-pager",
            "-u",
            "agent-sandbox-admin-web.service",
            "--since",
            "24 hours ago",
            "-p",
            "warning..alert",
            "-n",
            "200",
        ],
    ]


def test_runtime_diagnostics_snapshot_rejects_non_allowlisted_units(tmp_path: Path) -> None:
    provider = RuntimeDiagnosticsSnapshotEffectProvider()
    provider.initialize(_effect_definition(), _runtime_context(tmp_path))

    with pytest.raises(ValueError, match="allowlist"):
        provider.execute(resolved_config={"units": ["ssh.service"]})


def test_runtime_diagnostics_snapshot_enforces_bounds(tmp_path: Path) -> None:
    provider = RuntimeDiagnosticsSnapshotEffectProvider()
    provider.initialize(_effect_definition(), _runtime_context(tmp_path))

    with pytest.raises(ValueError, match="max_journal_lines_per_unit"):
        provider.execute(resolved_config={"max_journal_lines_per_unit": 501})

    with pytest.raises(ValueError, match="max_runtime_file_bytes"):
        provider.execute(resolved_config={"max_runtime_file_bytes": 999})

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_workflow_run_inspector_demo_seed_writes_session_and_audit_records(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "demo-runtime"
    script = ROOT / "scripts" / "seed_workflow_run_inspector_demo.sh"

    result = subprocess.run(
        ["bash", str(script), "--runtime-dir", str(runtime_dir)],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "Seeded workflow run inspector demo runtime" in result.stdout

    sessions = json.loads((runtime_dir / "sessions.json").read_text(encoding="utf-8"))
    audit_lines = [
        json.loads(line)
        for line in (runtime_dir / "audit.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert "session-detail" in sessions
    assert sessions["session-detail"]["vm_id"] == "vm-detail"
    assert "prompt_agent" in sessions["session-detail"]["allowed_tools"]
    assert len(audit_lines) == 2
    tool_call = audit_lines[0]
    assert tool_call["event_type"] == "tool_call"
    assert tool_call["tool_call_id"] == "toolcall-rich-01"
    assert tool_call["request_id"] == "tool-request-detail"
    assert tool_call["correlation_id"] == "tool-correlation-detail"
    assert tool_call["approval_request_id"] == "approval-rich"
    assert tool_call["workflow_run_id"] == "evt-detail"
    assert tool_call["trace_id"] == "trace-detail"

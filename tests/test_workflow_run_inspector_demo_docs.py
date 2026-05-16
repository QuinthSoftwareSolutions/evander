from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_workflow_run_inspector_demo_docs_cover_seed_command_and_lookup_ids() -> None:
    doc = (ROOT / "docs" / "workflow-run-inspector-demo.md").read_text(encoding="utf-8")

    assert "seed_workflow_run_inspector_demo.sh" in doc
    assert "runtime/workflow-run-inspector-demo" in doc
    assert "agent-sandbox-admin-web" in doc
    assert "--runtime-dir runtime/workflow-run-inspector-demo" in doc
    assert "evt-detail" in doc
    assert "trace-detail" in doc
    assert "session-detail" in doc
    assert "comm_0123456789abcdef0123456789abcdef" in doc
    assert "evt-legacy" in doc
    assert "rev-detail-rich" in doc

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_operations_doc_mentions_provider_bootstrap_scripts() -> None:
    text = (ROOT / "docs" / "operations.md").read_text(encoding="utf-8")
    assert "bootstrap_codex_vm.sh" in text
    assert "bootstrap_claude_vm.sh" in text
    assert "bootstrap_copilot_vm.sh" in text
    assert "ENABLE_GUEST_HEARTBEAT=1" in text
    assert "GUEST_HEARTBEAT_PROMPT" in text

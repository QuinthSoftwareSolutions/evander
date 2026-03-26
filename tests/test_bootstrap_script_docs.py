from pathlib import Path


def test_operations_doc_mentions_codex_bootstrap_script() -> None:
    text = Path("/home/jonathan/agent-sandbox/docs/operations.md").read_text(encoding="utf-8")
    assert "bootstrap_codex_vm.sh" in text
    assert "ENABLE_GUEST_HEARTBEAT=1" in text
    assert "GUEST_HEARTBEAT_PROMPT" in text

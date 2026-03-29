from pathlib import Path


def test_operations_doc_mentions_firewall_service() -> None:
    text = (Path(__file__).resolve().parents[1] / "docs" / "operations.md").read_text(encoding="utf-8")
    assert "agent-sandbox-firewall.service" in text
    assert "config/firewall.env" in text

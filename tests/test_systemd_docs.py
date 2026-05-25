from pathlib import Path


def test_operations_doc_mentions_firewall_service() -> None:
    text = (Path(__file__).resolve().parents[1] / "docs" / "operations.md").read_text(
        encoding="utf-8"
    )
    assert "agent-sandbox-firewall.service" in text
    assert "agent-sandbox-admin-web" in text
    assert "config/firewall.env" in text
    assert "config/admin-web.env" in text
    assert "agent-sandbox-http-target-relay.service" in text
    assert "agent-sandbox-didww-sip-bridge.service" in text
    assert "HTTP target relay" in text
    assert "systemctl show -p User agent-sandbox-discord-wake.service" in text
    assert "systemctl show -p User agent-sandbox-scheduled-wake.service" in text

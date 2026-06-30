from pathlib import Path


def test_operations_doc_mentions_firewall_service() -> None:
    text = (Path(__file__).resolve().parents[1] / "docs" / "operations.md").read_text(
        encoding="utf-8"
    )
    assert "evander-firewall.service" in text
    assert "evander-admin-web" in text
    assert "config/firewall.env" in text
    assert "config/admin-web.env" in text
    assert "evander-npm-audit-facade.service" in text
    assert "config/npm-audit-facade.env" in text
    assert "evander-http-target-relay.service" in text
    assert "evander-didww-sip-bridge.service" in text
    assert "HTTP target relay" in text
    assert "systemctl show -p User evander-discord-wake.service" in text
    assert "systemctl show -p User evander-scheduled-wake.service" in text

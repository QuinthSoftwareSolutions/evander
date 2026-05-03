from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_firewall_default_ports_include_ssh_mcp_and_relay() -> None:
    env_example = (ROOT / "config" / "firewall.env.example").read_text(encoding="utf-8")
    ops = (ROOT / "docs" / "operations.md").read_text(encoding="utf-8")

    assert 'ALLOWED_TCP_PORTS="22 8000 11434 11666 11777 8081 3142"' in env_example
    assert "`22` for host SSH/Git" in ops
    assert "`11777` for the host-side HTTP target relay" in ops
    assert "`8081` for the host-side Nexus package cache" in ops
    assert "`3142` for the host-side apt-cacher-ng relay" in ops

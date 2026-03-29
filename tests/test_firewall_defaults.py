from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_firewall_default_ports_include_ssh_mcp_and_relay() -> None:
    env_example = (ROOT / "config" / "firewall.env.example").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    ops = (ROOT / "docs" / "operations.md").read_text(encoding="utf-8")

    assert 'ALLOWED_TCP_PORTS="22 8000 11434 11666"' in env_example
    assert "`22`, `8000`, `11434`, and `11666`" in readme
    assert "`22` for host SSH/Git" in ops

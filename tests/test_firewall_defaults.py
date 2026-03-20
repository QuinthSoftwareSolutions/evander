from pathlib import Path


def test_firewall_default_ports_include_ssh_mcp_and_relay() -> None:
    env_example = Path("/home/jonathan/agent-sandbox/config/firewall.env.example").read_text(
        encoding="utf-8"
    )
    readme = Path("/home/jonathan/agent-sandbox/README.md").read_text(encoding="utf-8")
    ops = Path("/home/jonathan/agent-sandbox/docs/operations.md").read_text(encoding="utf-8")

    assert 'ALLOWED_TCP_PORTS="22 8000 11434"' in env_example
    assert "`22`, `8000`, and `11434`" in readme
    assert "`22` for host SSH/Git" in ops

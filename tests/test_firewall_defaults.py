import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_firewall_default_ports_include_ssh_mcp_and_relay() -> None:
    env_example = (ROOT / "config" / "firewall.env.example").read_text(encoding="utf-8")
    ops = (ROOT / "docs" / "operations.md").read_text(encoding="utf-8")

    assert 'ALLOWED_TCP_PORTS="22 8000 11434 11666 11777 11881 8081 3142"' in env_example
    assert "`22` for host SSH/Git" in ops
    assert "`11777` for the host-side HTTP target relay" in ops
    assert "`8081` for the host-side Nexus package cache" in ops
    assert "`3142` for the host-side apt-cacher-ng relay" in ops


def test_firewall_script_replaces_current_table_before_applying_policy(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log_path = tmp_path / "nft.log"
    (fake_bin / "nft").write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                'printf "%s\\n" "$*" >> "${NFT_LOG}"',
                'if [[ "$1 $2 $3 $4" == "list table inet evander" ]]; then',
                "  exit 0",
                "fi",
                "exit 0",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (fake_bin / "nft").chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "NFT_LOG": str(log_path),
        "ALLOWED_TCP_PORTS": "22",
    }

    result = subprocess.run(
        [str(ROOT / "scripts" / "apply_firewall.sh")],
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )

    commands = log_path.read_text(encoding="utf-8").splitlines()
    assert "Applied Evander forward policy for host ports: 22" in result.stdout
    assert not any("agent_sandbox" in command for command in commands)
    assert "delete table inet evander" in commands
    assert commands.index("delete table inet evander") < commands.index("add table inet evander")

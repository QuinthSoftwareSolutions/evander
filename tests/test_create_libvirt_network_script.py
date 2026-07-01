from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_create_libvirt_network_migrates_legacy_agent_sandbox_network(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log_path = tmp_path / "virsh.log"
    (fake_bin / "virsh").write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                'printf "%s\\n" "$*" >> "${VIRSH_LOG}"',
                'if [[ "$1 $2" == "net-info evander" ]]; then',
                "  exit 1",
                "fi",
                'if [[ "$1 $2" == "net-info agent-sandbox" ]]; then',
                '  printf "Name: agent-sandbox\\nActive: yes\\n"',
                "  exit 0",
                "fi",
                "exit 0",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (fake_bin / "virsh").chmod(0o755)
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "VIRSH_LOG": str(log_path),
    }

    result = subprocess.run(
        [str(ROOT / "scripts" / "create_libvirt_network.sh")],
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )

    commands = log_path.read_text(encoding="utf-8").splitlines()
    assert "Migrating legacy libvirt network 'agent-sandbox' to 'evander'" in result.stdout
    assert "Created libvirt network 'evander'" in result.stdout
    assert commands[:5] == [
        "net-info evander",
        "net-info agent-sandbox",
        "net-info agent-sandbox",
        "net-destroy agent-sandbox",
        "net-undefine agent-sandbox",
    ]
    assert commands[-3:] == [
        f"net-define {ROOT / 'config' / 'libvirt-network.xml'}",
        "net-autostart evander",
        "net-start evander",
    ]

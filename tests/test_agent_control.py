from __future__ import annotations

import json
from pathlib import Path

from agent_sandbox.agent_control import AgentControlStore


def test_agent_control_store_set_status_preserves_unrelated_vm_entries(tmp_path: Path) -> None:
    path = tmp_path / "host-agent-control-state.json"
    path.write_text(
        json.dumps(
            {
                "vm-1": {
                    "vm_name": "vm-1",
                    "status": "disabled",
                    "phase": "operator-disable",
                    "updated_at": "2026-03-25T00:00:00Z",
                }
            }
        ),
        encoding="utf-8",
    )
    store = AgentControlStore(path)

    store.set_status(vm_name="vm-2", status="idle", phase="operator-enable")

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["vm-1"]["status"] == "disabled"
    assert payload["vm-2"]["status"] == "idle"

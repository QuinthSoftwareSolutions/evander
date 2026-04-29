from __future__ import annotations

import json
from pathlib import Path

from agent_sandbox.workflow.packs import compose_workflow_ir


def test_compose_workflow_ir_merges_selected_packs(tmp_path: Path) -> None:
    packs_dir = tmp_path / "config" / "workflow-packs"
    packs_dir.mkdir(parents=True, exist_ok=True)

    (packs_dir / "pack-a.v1.json").write_text(
        json.dumps(
            {
                "pack_id": "pack-a",
                "pack_version": "v1",
                "ir": {
                    "version": "v1",
                    "nodes": [
                        {
                            "id": "trigger-a",
                            "display_name": "tick",
                            "type": "trigger:cron",
                            "expression": "* * * * *",
                        }
                    ],
                    "edges": [],
                },
            }
        ),
        encoding="utf-8",
    )
    (packs_dir / "pack-b.v1.json").write_text(
        json.dumps(
            {
                "pack_id": "pack-b",
                "pack_version": "v1",
                "ir": {
                    "version": "v1",
                    "nodes": [
                        {
                            "id": "effect-b",
                            "display_name": "echo",
                            "type": "effect:bash:script",
                            "script_path": "/bin/echo",
                        }
                    ],
                    "edges": [{"source_id": "trigger-a", "target_id": "effect-b"}],
                },
            }
        ),
        encoding="utf-8",
    )

    composed = compose_workflow_ir(project_dir=tmp_path, workflow_packs=["pack-a.v1", "pack-b.v1"])

    assert [trigger["id"] for trigger in composed.triggers] == ["trigger-a"]
    assert [effect["id"] for effect in composed.effects] == ["effect-b"]
    assert composed.edges == [
        {
            "source_id": "trigger-a",
            "target_id": "effect-b",
            "source_port": None,
            "allow_cycle": False,
        }
    ]


def test_compose_workflow_ir_accepts_inline_overlay_with_ir_metadata(tmp_path: Path) -> None:
    composed = compose_workflow_ir(
        project_dir=tmp_path,
        workflow_packs=[],
        inline_ir={
            "version": "v1",
            "nodes": [
                {
                    "node": {
                        "id": "trigger-inline",
                        "display_name": "tick",
                        "type": "trigger:cron",
                        "expression": "* * * * *",
                    },
                    "bindings": {"example": "${{trigger-inline.expression}}"},
                    "retry": {"max_attempts": 3, "backoff_seconds": 1.0},
                    "idempotency": {"key_template": "${{system.event_id}}::trigger-inline"},
                }
            ],
            "edges": [],
        },
    )

    assert [trigger["id"] for trigger in composed.triggers] == ["trigger-inline"]

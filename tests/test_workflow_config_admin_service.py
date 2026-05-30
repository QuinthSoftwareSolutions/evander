from __future__ import annotations

from agent_sandbox.admin.services.workflow_config_service import WorkflowConfigAdminService


class FakeFacade:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    def read_workflow_editor_document(self):
        self.calls.append(("read_workflow_editor_document", (), {}))
        return {"kind": "document"}

    def save_workflow_editor_document(
        self,
        *,
        config: dict[str, object],
        layout: dict[str, object] | None = None,
        graphs: list[dict[str, object]] | None = None,
        expected_revision: str | None = None,
    ):
        self.calls.append(
            (
                "save_workflow_editor_document",
                (),
                {
                    "config": config,
                    "layout": layout,
                    "graphs": graphs,
                    "expected_revision": expected_revision,
                },
            )
        )
        return {"kind": "save", "config": config, "layout": layout, "graphs": graphs}

    def upsert_trigger_config(self, trigger_id: str, payload: dict[str, object]):
        self.calls.append(("upsert_trigger_config", (trigger_id, payload), {}))
        return {"kind": "trigger", "trigger_id": trigger_id, "payload": payload}

    def delete_effect_config(self, effect_id: str):
        self.calls.append(("delete_effect_config", (effect_id,), {}))
        return {"kind": "effect", "effect_id": effect_id}


def test_workflow_config_service_delegates_representative_operations() -> None:
    facade = FakeFacade()
    service = WorkflowConfigAdminService(facade)

    document = service.read_workflow_editor_document()
    saved = service.save_workflow_editor_document(
        config={"name": "demo"},
        layout={"nodes": {}},
        graphs=[
            {
                "id": "graph-demo",
                "name": "Demo",
                "directory_path": "root",
                "user_ids": ["admin"],
                "node_ids": [],
            }
        ],
    )
    trigger = service.upsert_trigger_config("trigger-1", {"type": "trigger:manual"})
    effect = service.delete_effect_config("effect-9")

    assert document == {"kind": "document"}
    assert saved == {
        "kind": "save",
        "config": {"name": "demo"},
        "layout": {"nodes": {}},
        "graphs": [
            {
                "id": "graph-demo",
                "name": "Demo",
                "directory_path": "root",
                "user_ids": ["admin"],
                "node_ids": [],
            }
        ],
    }
    assert trigger == {
        "kind": "trigger",
        "trigger_id": "trigger-1",
        "payload": {"type": "trigger:manual"},
    }
    assert effect == {"kind": "effect", "effect_id": "effect-9"}
    assert facade.calls == [
        ("read_workflow_editor_document", (), {}),
        (
            "save_workflow_editor_document",
            (),
            {
                "config": {"name": "demo"},
                "layout": {"nodes": {}},
                "graphs": [
                    {
                        "id": "graph-demo",
                        "name": "Demo",
                        "directory_path": "root",
                        "user_ids": ["admin"],
                        "node_ids": [],
                    }
                ],
                "expected_revision": None,
            },
        ),
        ("upsert_trigger_config", ("trigger-1", {"type": "trigger:manual"}), {}),
        ("delete_effect_config", ("effect-9",), {}),
    ]

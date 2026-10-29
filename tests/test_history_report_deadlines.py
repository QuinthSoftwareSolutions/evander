import json
from datetime import timedelta

from starlette.testclient import TestClient

from evander.admin.composition import build_admin_runtime
from history_storage_support import seed_indexed_history
from test_admin_web import create_app, write_config


def test_admin_history_reports_handle_ten_thousand_indexed_executions(tmp_path):
    runtime_dir = tmp_path / "runtime"
    config_path = tmp_path / "config" / "trigger-orchestrator" / "config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps(
            {
                "runtime_dir": str(runtime_dir),
                "effects": [
                    {
                        "id": "history-node",
                        "display_name": "History effect",
                        "type": "effect:event:emit",
                        "event_type": "history.completed",
                        "payload": {},
                    }
                ],
                "workflow_graphs": [
                    {
                        "id": "history-graph",
                        "name": "History",
                        "directory_path": "root",
                        "user_ids": [],
                        "node_ids": ["history-node"],
                    }
                ],
            }
        )
    )
    record = seed_indexed_history(
        runtime_dir / "orchestration.db",
        executions=10000,
        fields=20,
        age_days=1,
    )
    window = {
        "node_id": "history-node",
        "started_from": (record["started_at"] - timedelta(days=1)).isoformat(),
        "started_to": (record["started_at"] + timedelta(days=1)).isoformat(),
    }
    with TestClient(create_app(build_admin_runtime(write_config(tmp_path)))) as client:
        fields = client.post("/api/workflow-editor/node-executions/fields", json=window)
        assert fields.status_code == 200, fields.text
        descriptors = fields.json()["fields"]
        assert len(descriptors) == 20
        assert {field["json_pointer"] for field in descriptors} == {
            f"/field-{n}" for n in range(20)
        }
        assert all(field["observed_count"] == 10000 for field in descriptors)
        search = client.post(
            "/api/workflow-editor/node-executions/search",
            json={
                **window,
                "limit": 2,
                "filters": [
                    {
                        "location": "output",
                        "path": "/field-19",
                        "operator": "gte",
                        "value": 19,
                    }
                ],
            },
        )
        assert search.status_code == 200, search.text
        assert search.json()["matched"] == 10000
        assert len(search.json()["executions"]) == 2
        detail = client.get("/api/workflow-editor/node-executions/effect:0")
        assert detail.status_code == 200, detail.text
        assert detail.json()["output"]["field-19"] == 19

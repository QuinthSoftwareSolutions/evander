import json

from evander_test_fixtures_addon.migration import preview, retirement_check
from evander_test_fixtures_addon.models import Settings, Worker, WorkerSettings
from evander_test_fixtures_addon.state import Store


def test_preview_preserves_worker_scope_pins_and_never_enables_management(monkeypatch):
    monkeypatch.setenv("EVANDER_WORKER_MSSQL_ADMIN_PASSWORD", "synthetic-host-password")
    image = "rabbitmq@sha256:" + "1" * 64
    old = Settings(
        allocations_enabled=True,
        allowed_team_ids=["team-a"],
        workers={
            "worker-a": Worker(
                worker_base_url="http://192.0.2.10:11888",
                fixture_base_url="http://192.0.2.10:11991",
                token_env="OLD_TOKEN",
            )
        },
    )
    worker = WorkerSettings(worker_id="worker-a", sql_enabled=True, rabbitmq_image=image)
    result = preview(old.model_dump(), {"worker-a": worker.model_dump()})
    sql = result["settings"]["sqlserver-addon"]
    rabbit = result["settings"]["rabbitmq-addon"]
    assert not sql["allocations_enabled"] and not rabbit["allocations_enabled"]
    assert sql["connections"]["worker-worker-a"]["operations"] == []
    assert sql["profiles"]["legacy-worker-a"]["options"]["databases"] == ["source", "target"]
    assert sql["profiles"]["legacy-worker-a"]["allowed_owner_key_prefixes"] == ["team-a"]
    assert rabbit["profiles"]["legacy-worker-a"]["options"]["image"] == image
    assert sql["workers"]["worker-a"]["worker_base_url"] == "http://192.0.2.10:11888"
    assert set(
        t for values in result["tool_grant_translation"].values() for t in values
    ).isdisjoint(
        {
            "sqlserver_query",
            "sqlserver_execute",
            "rabbitmq_publish",
            "rabbitmq_get_messages",
            "rabbitmq_manage",
        }
    )
    assert "synthetic-host-password" not in json.dumps(result)


def test_retirement_requires_every_worker_and_never_authorizes_provider_absence(tmp_path):
    cp = Store(tmp_path / "cp")
    worker = Store(tmp_path / "worker")
    settings = Settings(
        workers={
            "worker-a": Worker(
                worker_base_url="http://192.0.2.10:11888",
                fixture_base_url="http://192.0.2.10:11991",
                token_env="TOKEN",
            )
        }
    )
    cp.save_settings("root", 0, settings, "operator")
    assert retirement_check(cp.root, {})["journals_drained"] is False
    before = cp.path.stat().st_mtime_ns
    result = retirement_check(cp.root, {"worker-a": worker.root})
    assert result["journals_drained"] and result["retirement_authorized"] is False
    assert cp.path.stat().st_mtime_ns == before
    assert result["workers_to_verify"] == ["worker-a"]

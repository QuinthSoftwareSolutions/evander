from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import stat

import pytest

from evander.directory_config import DirectoryConfigStore
from evander.directories import DirectoryService, DirectoryStore
from evander.host_config import HostConfig


@pytest.fixture
def migration():
    path = Path(__file__).parents[1] / "scripts/migrate_openai_deprecations.py"
    spec = importlib.util.spec_from_file_location("openai_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def configured_runtime(tmp_path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "host-config.json").write_text(HostConfig(runtime_dir=runtime).model_dump_json())
    directories = DirectoryStore(runtime / "directories.json")
    service = DirectoryService(directories)
    service.create(parent="root", name="public")
    service.create(parent="root", name="custom")
    service.create(parent="root/custom", name="public")
    configs = DirectoryConfigStore(directories, runtime_dir=runtime)
    configs.write("root", domain="providers", payload={
        "openai_realtime_model": "gpt-realtime-2025-08-28",
        "openai_realtime_transcription_model": "gpt-4o-transcribe",
    })
    configs.write("root/custom", domain="providers", payload={
        "openai_realtime_api_base_url": "https://custom.example.test/v1",
    })
    configs.write("root/custom/public", domain="providers", payload={
        "openai_realtime_api_base_url": "https://api.openai.com/v1",
    })
    return runtime, configs


def test_preview_apply_preserves_custom_inheritance_and_is_idempotent(
    migration, configured_runtime, tmp_path
):
    runtime, configs = configured_runtime
    before = {str(p.relative_to(runtime)): p.read_bytes() for p in runtime.rglob("*") if p.is_file()}
    plan, _ = migration.inventory(runtime)
    assert before == {str(p.relative_to(runtime)): p.read_bytes() for p in runtime.rglob("*") if p.is_file()}
    assert "gpt-realtime-2.1" in json.dumps(plan)
    assert not any(e["changes"] for e in plan["entries"] if e["directory"] == "root/public")
    result = migration.apply(plan, backup_dir=tmp_path / "recovery", maintenance=True)
    assert configs.read("root", domain="providers")["openai_realtime_model"] == "gpt-realtime-2.1"
    assert configs.read("root", domain="providers")["openai_realtime_transcription_model"] == "gpt-live-transcribe"
    custom = configs.read("root/custom", domain="providers")
    assert custom["openai_realtime_model"] == "gpt-realtime-2025-08-28"
    assert custom["openai_realtime_transcription_model"] == "gpt-4o-transcribe"
    returned_public = configs.read("root/custom/public", domain="providers")
    assert returned_public["openai_realtime_model"] == "gpt-realtime-2.1"
    assert returned_public["openai_realtime_transcription_model"] == "gpt-live-transcribe"
    assert configs.read("root/public", domain="providers") is None
    assert migration.apply(plan, backup_dir=tmp_path / "recovery", maintenance=True)["already_migrated"]
    for path in Path(result["recovery_dir"]).iterdir():
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_stale_sources_and_unreviewed_changes_are_rejected_before_any_write(
    migration, configured_runtime, tmp_path
):
    runtime, configs = configured_runtime
    plan, _ = migration.inventory(runtime)
    configs.write("root/custom", domain="providers", payload={"openai_realtime_api_base_url": "https://changed.test/v1"})
    with pytest.raises(ValueError, match="sources changed"):
        migration.apply(plan, backup_dir=tmp_path / "recovery", maintenance=True)
    assert configs.read("root", domain="providers")["openai_realtime_model"] == "gpt-realtime-2025-08-28"
    fresh, _ = migration.inventory(runtime)
    next(entry for entry in fresh["entries"] if entry["changes"])["changes"][0]["after"] = "unreviewed-target"
    with pytest.raises(ValueError, match="sources changed"):
        migration.apply(fresh, backup_dir=tmp_path / "recovery", maintenance=True)
    assert not (tmp_path / "recovery").exists()


def test_env_migration_preserves_secrets_comments_permissions_and_custom_providers(migration, tmp_path):
    env = tmp_path / "bridge.env"
    env.write_text('OPENAI_API_KEY=synthetic-secret\nexport OPENAI_REALTIME_MODEL="gpt-realtime" # selected\nOPENAI_REALTIME_TRANSCRIPTION_MODEL=gpt-4o-transcribe\n')
    env.chmod(0o640)
    plan, _ = migration.inventory(env_files=[env])
    assert "synthetic-secret" not in json.dumps(plan)
    migration.apply(plan, backup_dir=tmp_path / "recovery", maintenance=True)
    assert env.read_text() == ('OPENAI_API_KEY=synthetic-secret\nexport OPENAI_REALTIME_MODEL="gpt-realtime-2.1" # selected\nOPENAI_REALTIME_TRANSCRIPTION_MODEL=gpt-live-transcribe\n')
    assert stat.S_IMODE(env.stat().st_mode) == 0o640
    custom = tmp_path / "custom.env"
    custom.write_text('OPENAI_REALTIME_API_BASE_URL=https://custom.test/v1\nOPENAI_REALTIME_MODEL=gpt-realtime\n')
    assert not migration.inventory(env_files=[custom])[1]


def test_apply_requires_explicit_maintenance_and_external_recovery_storage(migration, configured_runtime, tmp_path):
    plan, _ = migration.inventory(configured_runtime[0])
    with pytest.raises(ValueError, match="maintenance"):
        migration.apply(plan, backup_dir=tmp_path / "recovery")
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / ".git").write_text("gitdir: synthetic")
    with pytest.raises(ValueError, match="outside Git"):
        migration.apply(plan, backup_dir=checkout / "recovery", maintenance=True)


def test_partial_apply_preserves_custom_models_and_fresh_preview_finishes(
    migration, configured_runtime, tmp_path, monkeypatch
):
    runtime, configs = configured_runtime
    plan, _ = migration.inventory(runtime)
    original = DirectoryConfigStore.write

    def fail_parent(self, reference, **kwargs):
        if reference == "root":
            raise OSError("synthetic write failure with private details")
        return original(self, reference, **kwargs)

    monkeypatch.setattr(DirectoryConfigStore, "write", fail_parent)
    with pytest.raises(RuntimeError) as failure:
        migration.apply(plan, backup_dir=tmp_path / "recovery", maintenance=True)
    report = json.loads(str(failure.value))
    assert any(entry["directory"] == "root/custom" for entry in report["completed"])
    assert "private details" not in str(failure.value)
    assert configs.read("root/custom", domain="providers")["openai_realtime_model"] == "gpt-realtime-2025-08-28"
    assert configs.read("root", domain="providers")["openai_realtime_model"] == "gpt-realtime-2025-08-28"
    monkeypatch.setattr(DirectoryConfigStore, "write", original)
    fresh, _ = migration.inventory(runtime)
    migration.apply(fresh, backup_dir=tmp_path / "recovery", maintenance=True)
    assert configs.read("root/custom", domain="providers")["openai_realtime_model"] == "gpt-realtime-2025-08-28"
    assert configs.read("root", domain="providers")["openai_realtime_model"] == "gpt-realtime-2.1"
    assert not migration.inventory(runtime)[1]

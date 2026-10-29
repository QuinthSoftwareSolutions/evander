from __future__ import annotations

import importlib.util
from copy import deepcopy
import json
from pathlib import Path
import stat

import pytest

from evander.directory_config import DirectoryConfigResolver, DirectoryConfigStore
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


@pytest.fixture
def metadata_runtime(tmp_path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "host-config.json").write_text(HostConfig(runtime_dir=runtime).model_dump_json())
    directories = DirectoryStore(runtime / "directories.json")
    directories.ensure_root()
    configs = DirectoryConfigStore(directories, runtime_dir=runtime)
    return runtime, configs


def set_metadata_overlay(runtime, path, payload):
    directories = DirectoryStore(runtime / "directories.json")
    info = DirectoryService(directories).show(path).directory
    record = directories.get(info.id)
    metadata = deepcopy(record.metadata)
    metadata.setdefault("config_overlays", {})["providers"] = payload
    metadata["operator_note"] = "preserve unrelated metadata"
    directories.put(record.model_copy(update={"metadata": metadata}))


def effective_providers(runtime, configs, path):
    directories = DirectoryStore(runtime / "directories.json")
    return DirectoryConfigResolver(DirectoryService(directories), config_store=configs).resolve_domain(
        path, domain="providers", base_config={
            "openai_realtime_api_base_url": "https://api.openai.com/v1",
            "openai_realtime_model": "gpt-realtime-2",
            "openai_realtime_transcription_model": "gpt-live-transcribe",
        },
    ).effective_config


@pytest.mark.parametrize("transcription,expected", [("gpt-4o-transcribe", "gpt-live-transcribe"), (None, None)])
def test_metadata_owned_models_migrate_in_place_without_hiding_other_settings(
    migration, metadata_runtime, tmp_path, transcription, expected
):
    runtime, configs = metadata_runtime
    set_metadata_overlay(runtime, "root", {
        "openai_realtime_model": "gpt-realtime-2025-08-28",
        "openai_realtime_transcription_model": transcription,
        "openai_realtime_api_key_env": "SYNTHETIC_REALTIME_KEY",
    })
    directories = DirectoryStore(runtime / "directories.json")
    root = directories.get("root")
    metadata = deepcopy(root.metadata)
    metadata["config_overlays"]["connectors"] = {"connectors": {}}
    directories.put(root.model_copy(update={"metadata": metadata}))
    registry_before = (runtime / "directories.json").read_bytes()
    plan, _ = migration.inventory(runtime)
    assert (runtime / "directories.json").read_bytes() == registry_before
    target = next(e for e in plan["entries"] if e["domain"] == "providers")
    assert target["source"] == "metadata" and target["directory_id"] == "root"
    assert "SYNTHETIC_REALTIME_KEY" not in json.dumps(plan)
    migration.apply(plan, backup_dir=tmp_path / "recovery", maintenance=True)
    effective = effective_providers(runtime, configs, "root")
    assert effective["openai_realtime_model"] == "gpt-realtime-2.1"
    assert effective["openai_realtime_transcription_model"] == expected
    assert effective["openai_realtime_api_key_env"] == "SYNTHETIC_REALTIME_KEY"
    assert configs.read("root", domain="providers") is None
    metadata["config_overlays"]["providers"].update(
        openai_realtime_model="gpt-realtime-2.1", openai_realtime_transcription_model=expected,
    )
    assert directories.get("root").metadata == metadata
    assert not migration.inventory(runtime)[1]
    assert migration.apply(plan, backup_dir=tmp_path / "recovery", maintenance=True)["already_migrated"]


@pytest.mark.parametrize("file_model", [None, "gpt-realtime-2025-08-28"])
def test_file_overlay_including_empty_object_takes_precedence_over_metadata(
    migration, metadata_runtime, tmp_path, file_model
):
    runtime, configs = metadata_runtime
    shadowed = {"openai_realtime_model": "gpt-realtime-mini-2025-10-06",
                "openai_realtime_api_base_url": "https://custom.test/v1"}
    set_metadata_overlay(runtime, "root", shadowed)
    configs.write("root", domain="providers", payload={"openai_realtime_model": file_model or "gpt-realtime-2"})
    if file_model is None:
        # The writer removes empty overlays; an existing empty file is still a
        # valid source and must shadow metadata when the runtime reads it.
        source = configs.root_path / "root/providers.json"
        source.write_text("{}")
    assert effective_providers(runtime, configs, "root")["openai_realtime_api_base_url"] == "https://api.openai.com/v1"
    plan, _ = migration.inventory(runtime)
    assert next(e for e in plan["entries"] if e["domain"] == "providers")["source"] == "file"
    migration.apply(plan, backup_dir=tmp_path / "recovery", maintenance=True)
    effective = effective_providers(runtime, configs, "root")
    assert effective["openai_realtime_api_base_url"] == "https://api.openai.com/v1"
    assert effective["openai_realtime_model"] == ("gpt-realtime-2.1" if file_model else "gpt-realtime-2")
    metadata = DirectoryStore(runtime / "directories.json").get("root").metadata
    assert metadata["config_overlays"]["providers"] == shadowed


@pytest.mark.parametrize("parent_source", ["file", "metadata"])
def test_mixed_sources_preserve_custom_descendants_and_migrate_public_descendants(
    migration, metadata_runtime, tmp_path, parent_source
):
    runtime, configs = metadata_runtime
    service = DirectoryService(DirectoryStore(runtime / "directories.json"))
    service.create(parent="root", name="custom")
    service.create(parent="root/custom", name="public")
    root = {"openai_realtime_model": "gpt-realtime-2025-08-28",
            "openai_realtime_transcription_model": "gpt-4o-transcribe"}
    custom = {"openai_realtime_api_base_url": "https://custom.test/v1"}
    if parent_source == "file":
        configs.write("root", domain="providers", payload=root)
        set_metadata_overlay(runtime, "root/custom", custom)
    else:
        set_metadata_overlay(runtime, "root", root)
        configs.write("root/custom", domain="providers", payload=custom)
    set_metadata_overlay(runtime, "root/custom/public", {
        "openai_realtime_api_base_url": "https://api.openai.com/v1",
    })
    plan, _ = migration.inventory(runtime)
    migration.apply(plan, backup_dir=tmp_path / "recovery", maintenance=True)
    for path in ("root", "root/custom/public"):
        effective = effective_providers(runtime, configs, path)
        assert effective["openai_realtime_model"] == "gpt-realtime-2.1"
        assert effective["openai_realtime_transcription_model"] == "gpt-live-transcribe"
    effective = effective_providers(runtime, configs, "root/custom")
    assert effective["openai_realtime_model"] == "gpt-realtime-2025-08-28"
    assert effective["openai_realtime_transcription_model"] == "gpt-4o-transcribe"
    if parent_source == "file":
        assert configs.read("root/custom", domain="providers") is None
    assert not migration.inventory(runtime)[1]


@pytest.mark.parametrize("shadow_with_file", [False, True])
def test_metadata_changes_or_source_switch_reject_stale_preview_before_writing(
    migration, metadata_runtime, tmp_path, shadow_with_file
):
    runtime, configs = metadata_runtime
    payload = {"openai_realtime_model": "gpt-realtime-2025-08-28"}
    set_metadata_overlay(runtime, "root", payload)
    plan, _ = migration.inventory(runtime)
    if shadow_with_file:
        configs.write("root", domain="providers", payload=payload)
    else:
        set_metadata_overlay(runtime, "root", {**payload, "openai_realtime_api_key_env": "CHANGED_KEY"})
    with pytest.raises(ValueError, match="sources changed"):
        migration.apply(plan, backup_dir=tmp_path / "recovery", maintenance=True)
    assert effective_providers(runtime, configs, "root")["openai_realtime_model"] == "gpt-realtime-2025-08-28"
    assert not (tmp_path / "recovery").exists()


@pytest.mark.parametrize("parent_source", ["file", "metadata"])
def test_partial_apply_pins_metadata_child_before_parent_and_fresh_preview_finishes(
    migration, metadata_runtime, tmp_path, monkeypatch, parent_source
):
    runtime, configs = metadata_runtime
    DirectoryService(DirectoryStore(runtime / "directories.json")).create(parent="root", name="custom")
    payload = {"openai_realtime_model": "gpt-realtime-2025-08-28"}
    if parent_source == "file":
        configs.write("root", domain="providers", payload=payload)
    else:
        set_metadata_overlay(runtime, "root", payload)
    set_metadata_overlay(runtime, "root/custom", {"openai_realtime_api_base_url": "https://custom.test/v1"})
    plan, _ = migration.inventory(runtime)
    original = migration.write_directory_candidate

    def fail_parent(runtime, host, entry, candidate):
        if entry["directory"] == "root":
            raise OSError("synthetic write failure with private details")
        return original(runtime, host, entry, candidate)

    monkeypatch.setattr(migration, "write_directory_candidate", fail_parent)
    with pytest.raises(RuntimeError) as failure:
        migration.apply(plan, backup_dir=tmp_path / "recovery", maintenance=True)
    assert "private details" not in str(failure.value)
    report = json.loads(str(failure.value))
    assert any(e["directory"] == "root/custom" and e["source"] == "metadata" for e in report["completed"])
    assert effective_providers(runtime, configs, "root")["openai_realtime_model"] == "gpt-realtime-2025-08-28"
    assert effective_providers(runtime, configs, "root/custom")["openai_realtime_model"] == "gpt-realtime-2025-08-28"
    assert configs.read("root/custom", domain="providers") is None
    monkeypatch.setattr(migration, "write_directory_candidate", original)
    fresh, _ = migration.inventory(runtime)
    migration.apply(fresh, backup_dir=tmp_path / "recovery", maintenance=True)
    assert effective_providers(runtime, configs, "root")["openai_realtime_model"] == "gpt-realtime-2.1"
    assert effective_providers(runtime, configs, "root/custom")["openai_realtime_model"] == "gpt-realtime-2025-08-28"
    assert not migration.inventory(runtime)[1]


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

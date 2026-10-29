from copy import deepcopy
import json
from pathlib import Path
import runpy

from evander.directories import DirectoryService, DirectoryStore
from evander.directory_config import DirectoryConfigStore, DirectoryConfigResolver
from evander.host_config import HostConfig


def test_narration_migration_changes_public_models_and_preserves_custom_child(tmp_path):
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "host-config.json").write_text(HostConfig(runtime_dir=runtime).model_dump_json())
    directories = DirectoryStore(runtime / "directories.json")
    DirectoryService(directories).create(parent="root", name="custom")
    DirectoryService(directories).create(parent="root", name="inherited")
    configs = DirectoryConfigStore(directories, runtime_dir=runtime)
    payload = json.loads((Path(__file__).parents[1] / "examples/connectors.json").read_text())
    voice = payload["connectors"]["demo-narration"]["config"]
    voice.pop("speech_backend")
    voice.update(model="gpt-4o-mini-tts", voice="onyx", speed=2.0)
    configs.write("root", domain="connectors", payload=payload)
    custom = deepcopy(payload)
    custom["connectors"]["demo-narration"]["config"]["base_url"] = "https://custom.test/v1"
    configs.write("root/custom", domain="connectors", payload=custom)
    migration = runpy.run_path(str(Path(__file__).parents[3] / "scripts/migrate_openai_deprecations.py"))
    plan, _ = migration["inventory"](runtime)
    assert "DEMO_SPEECH_API_KEY" not in json.dumps(plan)
    migration["apply"](plan, backup_dir=tmp_path / "recovery", maintenance=True)
    resolver = DirectoryConfigResolver(DirectoryService(directories), config_store=configs)
    def effective(path):
        return resolver.resolve_domain(path, domain="connectors").effective_config["connectors"]["demo-narration"]["config"]
    public = effective("root/inherited")
    assert public["model"] == "gpt-realtime-2.1-mini"
    assert public["speech_backend"] == "realtime" and public["voice"] == "cedar"
    assert public["speed"] == 1.5
    private = effective("root/custom")
    assert private["model"] == "gpt-4o-mini-tts"
    assert private["voice"] == "onyx" and private["speed"] == 2
    assert private["speech_backend"] == "speech_api"
    assert configs.read("root/inherited", domain="connectors") is None
    assert not migration["inventory"](runtime)[1]

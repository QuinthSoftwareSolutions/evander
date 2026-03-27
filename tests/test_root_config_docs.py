from pathlib import Path


def test_root_only_config_docs_describe_root_as_live_baseline() -> None:
    repo = Path("/home/jonathan/agent-sandbox")
    readme = (repo / "README.md").read_text(encoding="utf-8")
    operations = (repo / "docs/operations.md").read_text(encoding="utf-8")
    configuration_reference = (repo / "docs/configuration-reference.md").read_text(encoding="utf-8")

    assert "is the live top-level baseline" in readme
    assert "Legacy gateway YAML is only a migration/import source" in readme
    assert "Configure `root` directory overlays directly" in operations
    assert "is the live top-level layer" in operations
    assert "Legacy gateway YAML import keys" in configuration_reference
    assert "is not the live runtime baseline" in configuration_reference

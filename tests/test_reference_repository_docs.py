from pathlib import Path


def test_on_demand_reference_repository_docs_cover_permission_freshness_and_isolation() -> None:
    repo = Path(__file__).resolve().parents[1]
    readme = (repo / "README.md").read_text(encoding="utf-8")
    operations = (repo / "docs/operations.md").read_text(encoding="utf-8")
    configuration_reference = (repo / "docs/configuration-reference.md").read_text(
        encoding="utf-8"
    )

    for document in (readme, operations, configuration_reference):
        assert "prepare_reference_repository" in document
        assert "managed_repo_id" in document
        assert "read-only" in document

    assert "does not eagerly clone" in readme
    assert "verified upstream refresh" in " ".join(operations.split())
    assert '"operator": "allowlist"' in configuration_reference
    assert "no write or push authority" in configuration_reference

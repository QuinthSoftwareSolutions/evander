from pathlib import Path


def test_email_docs_do_not_regress_to_stub_only_wording() -> None:
    repo_root = Path("/home/jonathan/agent-sandbox")
    if not repo_root.exists():
        repo_root = Path(__file__).resolve().parents[1]

    readme = (repo_root / "README.md").read_text(encoding="utf-8")
    architecture = (repo_root / "docs/architecture.md").read_text(encoding="utf-8")
    operations = (repo_root / "docs/operations.md").read_text(encoding="utf-8")

    stale_phrases = (
        "Email is still a stub provider behind a local JSON store.",
        "Replace the stub email service with a provider-backed adapter",
    )
    for phrase in stale_phrases:
        assert phrase not in readme
        assert phrase not in architecture

    assert "gmail" in readme.lower()
    assert "graph" in readme.lower()
    assert "read + draft only by default" in readme
    assert "read + draft only by default" in operations
    assert "JSON store is an optional fallback" in readme
    assert "gmail" in architecture.lower()
    assert "graph" in architecture.lower()

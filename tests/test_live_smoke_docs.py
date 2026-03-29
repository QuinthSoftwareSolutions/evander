from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_live_smoke_doc_is_linked_from_core_docs() -> None:
    repo = ROOT
    live_doc = "docs/live-smoke-tests.md"

    readme = (repo / "README.md").read_text(encoding="utf-8")
    operations = (repo / "docs/operations.md").read_text(encoding="utf-8")
    agents = (repo / "AGENTS.md").read_text(encoding="utf-8")

    assert "live-smoke-tests.md" in readme
    assert "live-smoke-tests.md" in operations
    assert live_doc in agents or "live-smoke-tests.md" in agents


def test_live_smoke_doc_mentions_seed_discord_channel_helper() -> None:
    contents = (ROOT / "docs" / "live-smoke-tests.md").read_text(encoding="utf-8")

    assert "seed-discord-channel" in contents
    assert "adapter_http_error" in contents

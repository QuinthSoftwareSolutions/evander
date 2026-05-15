from pathlib import Path


def test_teams_chat_docs_cover_channel_and_chat_targets() -> None:
    repo = Path(__file__).resolve().parents[1]
    readme = (repo / "README.md").read_text(encoding="utf-8")
    operations = (repo / "docs" / "operations.md").read_text(encoding="utf-8")
    configuration_reference = (repo / "docs" / "configuration-reference.md").read_text(
        encoding="utf-8"
    )

    assert "teams_chat_allowlist" in readme
    assert "chat:<chat-id>" in readme
    assert "/chats/{chat-id}/messages" in readme
    assert "Chat.ReadWrite" in readme
    assert "team-id/channel-id" in operations
    assert "chat:<chat-id>" in operations
    assert "/chats/{chat-id}/messages" in operations
    assert "Chat.ReadWrite" in operations
    assert "teams_chat_allowlist" in configuration_reference
    assert "both channel threads and group chats" in configuration_reference
    assert "Chat.ReadWrite" in configuration_reference

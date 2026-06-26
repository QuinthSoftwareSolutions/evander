from agent_sandbox.admin.user_notifications import AdminUserNotificationStore


def test_admin_user_notifications_dedupe_and_mark_read(tmp_path):
    store = AdminUserNotificationStore(tmp_path)

    first = store.push(
        user_id="Admin@Example.COM",
        title="New Team chat message",
        body="Miranda: Ready for review",
        source="team-chat",
        source_id="chatmsg-1",
        target_url="#addon:team-addon",
        metadata={"conversation_id": "conversation-1"},
    )
    duplicate = store.push(
        user_id="admin@example.com",
        title="New Team chat message",
        body="Miranda: Ready for review",
        source="team-chat",
        source_id="chatmsg-1",
    )
    store.push(
        user_id="other@example.com",
        title="New Team chat message",
        body="Hidden from admin",
        source="team-chat",
        source_id="chatmsg-2",
    )

    assert duplicate["id"] == first["id"]

    unread, unread_count = store.list_unread(user_id="admin@example.com")
    assert unread_count == 1
    assert unread[0]["id"] == first["id"]
    assert unread[0]["target_url"] == "#addon:team-addon"
    assert unread[0]["metadata"] == {"conversation_id": "conversation-1"}

    marked = store.mark_read(user_id="admin@example.com", notification_ids=[first["id"]])
    assert marked == [first["id"]]

    unread, unread_count = store.list_unread(user_id="admin@example.com")
    assert unread == []
    assert unread_count == 0

    other_unread, other_count = store.list_unread(user_id="other@example.com")
    assert other_count == 1
    assert other_unread[0]["source_id"] == "chatmsg-2"

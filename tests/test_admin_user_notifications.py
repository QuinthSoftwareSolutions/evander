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
    second = store.push(
        user_id="admin@example.com",
        title="New Team chat message",
        body="Miranda: Follow-up",
        source="team-chat",
        source_id="chatmsg-3",
    )

    assert duplicate["id"] == first["id"]

    unread, unread_count = store.list_unread(user_id="admin@example.com")
    assert unread_count == 2
    unread_by_id = {item["id"]: item for item in unread}
    assert unread_by_id[first["id"]]["target_url"] == "#addon:team-addon"
    assert unread_by_id[first["id"]]["metadata"] == {"conversation_id": "conversation-1"}

    assert store.mark_read(user_id="admin@example.com", notification_ids=[]) == []
    _unread, unread_count = store.list_unread(user_id="admin@example.com")
    assert unread_count == 2

    marked_by_source = store.mark_source_read(
        user_id="admin@example.com",
        source="team-chat",
        source_ids=["chatmsg-3"],
    )
    assert marked_by_source == [second["id"]]

    marked = store.mark_read(user_id="admin@example.com", notification_ids=[first["id"]])
    assert marked == [first["id"]]

    unread, unread_count = store.list_unread(user_id="admin@example.com")
    assert unread == []
    assert unread_count == 0

    other_unread, other_count = store.list_unread(user_id="other@example.com")
    assert other_count == 1
    assert other_unread[0]["source_id"] == "chatmsg-2"
